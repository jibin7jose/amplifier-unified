"""Optional public enrollment, capability presence and bounded device delivery."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from aiohttp import web

from .auth import SESSION_COOKIE, _verified
from .deployment import write_private
from .device_capabilities import GRANTS, action, grants, transient
from .terminal_devices import digest
from .terminal_setup import require_safe_transport

PREFIX = '/api/connections/v1'
SCOPE = 'device-v1'
PRESENCE_TTL = 45
LEASE_TTL = 15
RECEIPT_TTL = 86400
TRANSIENT_TTL = 60
MAX_TRANSIENT = 64
MAX_TRANSIENT_PER_DEVICE = 16
MAX_TRANSIENT_PENDING = 2
IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,120}')
EXTENSION_ORIGIN = re.compile(r'chrome-extension://[a-p]{32}')
SELF_METHODS = {
    '/self': {'GET'}, '/self/heartbeat': {'POST'}, '/self/poll': {'POST'},
    '/self/result': {'POST'}, '/self/stop': {'POST'}, '/self/rotate': {'POST'},
    '/self/upgrade': {'POST'},
}


def permits_device(request):
    return request.method in SELF_METHODS.get(request.path.removeprefix(PREFIX), set()) and request.path.startswith(PREFIX)


def extension_request(request):
    """Only public enrollment and self APIs accept extension-origin transport.

    This is not authorization: every self handler still requires its scoped
    bearer; no cookie-authenticated request may use this transport exception.
    """
    origin = request.headers.get('Origin', '')
    if not EXTENSION_ORIGIN.fullmatch(origin):
        return False
    return request.path in {PREFIX, PREFIX + '/enroll', PREFIX + '/token', PREFIX + '/qr'} or request.path in {
        PREFIX + path for path in SELF_METHODS}


def response_headers(request, response):
    if extension_request(request):
        response.headers['Access-Control-Allow-Origin'] = request.headers['Origin']
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
    return response


async def body(request):
    data = bytearray()
    async for part in request.content.iter_chunked(8192):
        data.extend(part)
        if len(data) > 524288:
            raise web.HTTPRequestEntityTooLarge(max_size=524288, actual_size=len(data))
    value = json.loads(data)
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(text='Expected an object.')
    return value


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise web.HTTPBadRequest(text='Invalid identifier.')
    return value


def label(value, limit=160):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise web.HTTPBadRequest(text='Invalid label.')
    return value.strip()


class Connections:
    def __init__(self, devices, pairings, secret, *, owned_root):
        self.devices, self.pairings, self.secret = devices, pairings, secret
        self.owned_root = owned_root
        self.presence = {}
        self.setups = {}
        self.events = {}
        self.transient_operations = {}
        self.cancelled = set()
        self.path = devices.path.parent / 'device-operations.json'
        self.operations = json.loads(self.path.read_text()) if self.path.exists() else {}
        for row in self.operations.values():
            if row['status'] == 'delivered':
                row.update(status='unknown', detail='The host restarted before receiving a result.')
            elif row['status'] == 'queued':
                row.update(status='cancelled', detail='The host restarted before delivery.')
        if self.operations:
            self.save()

    def save(self):
        try:
            write_private(self.path, json.dumps(self.operations, separators=(',', ':')), owned_root=self.owned_root)
        except (OSError, ValueError, RuntimeError):
            # A failed durable write cannot leave an uncommitted acceptance
            # available to subsequent requests in this process.
            self.operations = json.loads(self.path.read_text()) if self.path.exists() else {}
            # Stop intent remains a live delivery fence even if persistence
            # failed. Restart already cancels queued rows and marks delivered
            # rows unknown before routes are exposed.
            for identity in self.cancelled:
                row = self.operations.get(identity)
                if row and row['status'] in {'queued', 'delivered'}:
                    row.update(status='cancelled' if row['status'] == 'queued' else 'unknown',
                               detail='Cancelled; durable cancellation could not be confirmed.')
            raise

    def rows(self):
        return (*self.operations.values(), *self.transient_operations.values())

    def lookup(self, identity):
        return self.operations.get(identity) or self.transient_operations.get(identity)

    def save_row(self, row):
        if row.get('retention') != 'transient':
            self.save()

    def transient_capacity(self, identity):
        """Evict only settled observations; preserve bounded pending delivery."""
        store = self.transient_operations
        for key, row in tuple(store.items()):
            if row['status'] not in {'queued', 'delivered'} and time.time() - row['createdAt'] >= TRANSIENT_TTL:
                del store[key]
        while (len(store) >= MAX_TRANSIENT or
               sum(r['deviceId'] == identity for r in store.values()) >= MAX_TRANSIENT_PER_DEVICE):
            per_device = sum(r['deviceId'] == identity for r in store.values()) >= MAX_TRANSIENT_PER_DEVICE
            candidate = next((key for key, row in store.items()
                if row['status'] not in {'queued', 'delivered'} and (not per_device or row['deviceId'] == identity)), None)
            if candidate is None:
                raise web.HTTPTooManyRequests(text='Observation delivery is busy. Wait for the current view to finish.')
            del store[candidate]

    def device(self, identity):
        row = self.devices.read()['devices'].get(identity)
        if not row or row.get('scope') != SCOPE:
            raise web.HTTPUnauthorized(text='This app’s connection was removed. Connect again explicitly.')
        return row

    def refresh(self):
        now = time.time()
        changed = False
        registered = self.devices.read()['devices']
        self.presence = {k: v for k, v in self.presence.items() if k in registered}
        self.events = {k: v for k, v in self.events.items() if k in registered}
        for row in self.rows():
            if row['status'] not in {'queued', 'delivered'}:
                continue
            removed = row['deviceId'] not in registered
            if removed or row['deadline'] <= now:
                row.update(status='unknown' if row['status'] == 'delivered' else 'cancelled',
                           detail='Access removed.' if removed else 'Delivery expired before a confirmed result.')
                changed |= row.get('retention') != 'transient'
        if changed:
            self.save()
        self.setups = {k: v for k, v in self.setups.items() if v['expires'] > now}
        self.transient_operations = {key: row for key, row in self.transient_operations.items()
            if row['status'] in {'queued', 'delivered'} or now - row['createdAt'] < TRANSIENT_TTL}
        self.cancelled.intersection_update(self.operations.keys() | self.transient_operations.keys())

    def listing(self):
        self.refresh()
        result = []
        for row in self.devices.listing():
            if row.get('scope') != SCOPE:
                continue
            presence = self.presence.get(row['id'], {})
            online = time.time() - presence.get('seen', 0) < PRESENCE_TTL
            targets = presence.get('targets', []) if online else []
            result.append({**row, 'connected': online, 'ready': any(t['ready'] for t in targets),
                           'lastSeen': presence.get('seen'), 'targets': targets})
        return result

    def begin(self, payload, *, upgrade=None, token=None):
        access = grants(payload.get('grants'))
        app_id = identifier(payload.get('appId'))
        setup_id = payload.get('setupId')
        self.refresh()
        if setup_id is not None and (not isinstance(setup_id, str) or setup_id not in self.setups):
            raise web.HTTPGone(text='Setup expired. Start again from Connect.')
        enrollment = {'grants': access, 'appId': app_id, 'setupId': setup_id}
        name = payload.get('name')
        if upgrade:
            old = self.device(upgrade)
            name, app_id = old['name'], old['appId']
            enrollment.update(appId=app_id, grants=grants(list(set(access) | set(old['grants']))),
                              upgradeDevice=upgrade, upgradeToken=token)
        result = self.pairings.start(name, payload.get('challenge'), enrollment=enrollment)
        result['verificationPath'] = '/connect/verify?id=' + result['id']
        result['qrPath'] = PREFIX + '/qr?id=' + result['id']
        if setup_id:
            self.setups[setup_id]['requestId'] = result['id']
        return result

    def setup(self):
        self.refresh()
        if len(self.setups) >= 30:
            raise web.HTTPTooManyRequests(text='Too many unfinished connection setups.')
        identity = secrets.token_urlsafe(24)
        self.setups[identity] = {'expires': time.time() + 86400, 'requestId': None}
        return {'id': identity, 'expiresIn': 86400}

    def setup_status(self, identity):
        self.refresh()
        row = self.setups.get(identity)
        if not row:
            raise web.HTTPGone(text='Setup expired. Start again.')
        self.pairings.clean()
        pending = self.pairings.pending.get(row['requestId'])
        if row.get('deviceID'):
            device = next((d for d in self.listing() if d['id'] == row['deviceID']), None)
            return {'status': ('ready' if device['ready'] else 'connected' if device['connected'] else 'paired')
                    if device else 'revoked', 'deviceId': row['deviceID']}
        if not pending:
            return {'status': 'waiting_for_app'}
        state = pending['state']
        if state != 'approved':
            return {'status': 'waiting_for_approval' if state == 'pending' else state}
        device = next((d for d in self.listing() if d['id'] == pending['deviceID']), None)
        return {'status': ('ready' if device['ready'] else 'connected' if device['connected'] else 'paired')
                if device else 'revoked', 'deviceId': pending['deviceID']}

    def heartbeat(self, identity, payload):
        device = self.device(identity)
        instance = identifier(payload.get('instanceId'))
        targets = payload.get('targets')
        if not isinstance(targets, list) or len(targets) > 10:
            raise web.HTTPBadRequest(text='Advertise at most ten targets.')
        clean = []
        for target in targets:
            if not isinstance(target, dict) or set(target) - {'id', 'revision', 'label', 'capabilities', 'ready'}:
                raise web.HTTPBadRequest(text='Invalid target description.')
            caps = target.get('capabilities')
            if (not isinstance(caps, list) or not caps or len(caps) > len(GRANTS)
                    or any(not isinstance(c, str) or c not in device['grants'] for c in caps)
                    or type(target.get('ready')) is not bool):
                raise web.HTTPForbidden(text='The target requires access this app has not been granted.')
            clean.append({'id': identifier(target.get('id')), 'revision': identifier(target.get('revision')),
                          'label': label(target.get('label')), 'capabilities': sorted(set(caps)), 'ready': target['ready']})
        if len({t['id'] for t in clean}) != len(clean):
            raise web.HTTPBadRequest(text='Target identifiers must be unique.')
        old = self.presence.get(identity)
        if old and old['instanceId'] != instance:
            self.stop(identity, 'The app reconnected with a new instance.')
        self.presence[identity] = {'instanceId': instance, 'seen': time.time(), 'targets': clean}
        setup_id = payload.get('setupId')
        if isinstance(setup_id, str) and setup_id in self.setups:
            self.setups[setup_id]['deviceID'] = identity
        # A fresh advertisement must not silently retarget accepted work.
        for row in self.rows():
            if row['deviceId'] == identity and row['status'] == 'queued' and not any(
                t['id'] == row['targetId'] and t['revision'] == row['revision'] and t['ready'] for t in clean
            ):
                row.update(status='cancelled', detail='The selected target changed before delivery.')
                self.save_row(row)
        return {'status': 'connected', 'grants': device['grants'], 'heartbeatSeconds': 10}

    def stop(self, identity, reason='Stopped on the device.'):
        presence = self.presence.get(identity)
        if presence:
            presence['targets'] = []
        changed = False
        for row in self.rows():
            if row['deviceId'] == identity and row['status'] in {'queued', 'delivered'}:
                row.update(status='cancelled' if row['status'] == 'queued' else 'unknown', detail=reason)
                changed |= row.get('retention') != 'transient'
        if changed:
            self.save()
        if event := self.events.get(identity):
            event.set()
        return {'status': 'stopped'}

    @staticmethod
    def receipt(row):
        return {k: v for k, v in row.items() if k not in {'lease', 'fingerprint', 'instanceId'}}

    def submit(self, payload):
        self.refresh()
        if set(payload) != {'id', 'deviceId', 'targetId', 'revision', 'action', 'args'}:
            raise web.HTTPBadRequest(text='Name the exact device and target for this operation.')
        operation_id = identifier(payload['id'])
        permission = action(payload['action'], payload['args'])
        fingerprint = digest(json.dumps(payload, sort_keys=True, separators=(',', ':')))
        if old := self.lookup(operation_id):
            if old['fingerprint'] != fingerprint:
                raise web.HTTPConflict(text='This operation ID already describes different work.')
            return self.receipt(old)
        device = self.device(identifier(payload['deviceId']))
        if permission not in device['grants']:
            raise web.HTTPForbidden(text='This app has not been granted that access.')
        presence = self.presence.get(device['id'])
        if not presence or time.time() - presence['seen'] >= PRESENCE_TTL:
            raise web.HTTPConflict(text='This app is offline. Reconnect it or choose another available route.')
        target = next((t for t in presence['targets'] if t['id'] == payload['targetId']), None)
        if not target or not target['ready'] or target['revision'] != payload['revision'] or permission not in target['capabilities']:
            raise web.HTTPConflict(text='This target is no longer ready. Refresh its state before continuing.')
        if sum(r['status'] in {'queued', 'delivered'} for r in self.rows() if r['deviceId'] == device['id']) >= 10:
            raise web.HTTPTooManyRequests(text='This app already has pending work.')
        now = time.time()
        ephemeral = transient(payload['action'])
        if ephemeral:
            if sum(r['deviceId'] == device['id'] and r['status'] in {'queued', 'delivered'}
                   for r in self.transient_operations.values()) >= MAX_TRANSIENT_PENDING:
                raise web.HTTPTooManyRequests(text='Observation delivery is busy. Wait for the current view to finish.')
            self.transient_capacity(device['id'])
        else:
            self.operations = {k: r for k, r in self.operations.items()
                               if now - r['createdAt'] < RECEIPT_TTL or r['status'] in {'queued', 'delivered'}}
            if len(self.operations) >= 1000:
                raise web.HTTPTooManyRequests(text='The receipt store is full. Wait before starting more work.')
        row = {**payload, 'status': 'queued', 'createdAt': now, 'deadline': now + 30,
               'fingerprint': fingerprint, 'instanceId': presence['instanceId']}
        if ephemeral:
            row['retention'] = 'transient'
            self.transient_operations[operation_id] = row
        else:
            self.operations[operation_id] = row
            self.save()
        self.events.setdefault(device['id'], asyncio.Event()).set()
        return self.receipt(row)

    async def poll(self, identity):
        self.device(identity)
        event = self.events.setdefault(identity, asyncio.Event())
        for attempt in range(2):
            event.clear()
            self.refresh()
            device = self.device(identity)
            presence = self.presence.get(identity)
            if not presence or time.time() - presence['seen'] >= PRESENCE_TTL:
                return {'operation': None, 'heartbeatRequired': True}
            # Effectful work has priority over live observation traffic.
            for row in self.rows():
                if row['deviceId'] == identity and row['status'] == 'queued':
                    permission = action(row['action'], row['args'])
                    if (row['instanceId'] != presence['instanceId'] or permission not in device['grants']
                            or not any(t['id'] == row['targetId'] and t['revision'] == row['revision']
                                       and t['ready'] and permission in t['capabilities'] for t in presence['targets'])):
                        row.update(status='cancelled', detail='The selected target changed before delivery.')
                        self.save_row(row)
                        continue
                    row.update(status='delivered', lease=secrets.token_urlsafe(24), deadline=time.time() + LEASE_TTL)
                    self.save_row(row)
                    return {'operation': {k: v for k, v in row.items() if k != 'fingerprint'}}
            if attempt == 0:
                try:
                    await asyncio.wait_for(event.wait(), 10)
                except asyncio.TimeoutError:
                    pass
        return {'operation': None}

    def result(self, identity, payload):
        self.refresh()
        self.device(identity)
        row = self.lookup(identifier(payload.get('id')))
        if not row or row['deviceId'] != identity:
            raise web.HTTPNotFound(text='No such operation for this app.')
        if not isinstance(payload.get('lease'), str) or not hmac.compare_digest(row.get('lease', ''), payload['lease']):
            raise web.HTTPForbidden(text='Invalid delivery receipt.')
        if payload.get('status') not in {'completed', 'failed'} or not isinstance(payload.get('result'), dict):
            raise web.HTTPBadRequest(text='Invalid operation result.')
        # Match compact UTF-8 wire JSON rather than inflating non-ASCII text
        # into six-character escapes. Lone surrogates remain escaped like JS.
        if len(json.dumps(payload['result'], ensure_ascii=False, separators=(',', ':')).encode('utf-8', 'backslashreplace')) > 262144:
            raise web.HTTPBadRequest(text='The result is too large.')
        if row['status'] in {'completed', 'failed'}:
            if row['status'] != payload['status'] or row['result'] != payload['result']:
                raise web.HTTPConflict(text='A different final result was already recorded.')
            return self.receipt(row)
        if row['status'] not in {'delivered', 'unknown'}:
            raise web.HTTPConflict(text='This operation was not delivered.')
        row.update(status=payload['status'], result=payload['result'], completedAt=time.time())
        self.save_row(row)
        return self.receipt(row)

    def cancel(self, identity):
        row = self.lookup(identity)
        if not row:
            raise web.HTTPNotFound()
        if row['status'] in {'queued', 'delivered'}:
            self.cancelled.add(identity)
            row.update(status='cancelled' if row['status'] == 'queued' else 'unknown',
                       detail='Cancelled; an already delivered effect may have happened.')
            self.save_row(row)
        return self.receipt(row)

    def rotate(self, identity, rotation_id):
        identifier(rotation_id)
        self.device(identity)
        value = hmac.new(self.secret.encode(), ('device-rotation:' + identity + ':' + rotation_id).encode(), hashlib.sha512).digest()[:48]
        token = 'amd_' + identity + '.' + base64.urlsafe_b64encode(value).decode()
        with self.devices.transaction() as state:
            row = state['devices'].get(identity)
            if not row:
                raise web.HTTPUnauthorized()
            if row.get('rotationId') != rotation_id:
                if row.get('previousUntil', 0) > time.time():
                    raise web.HTTPConflict(text='Finish the pending credential rotation first.')
                row.update(previousDigest=row['digest'], previousUntil=time.time() + 60,
                           digest=digest(token), rotationId=rotation_id)
        return {'token': token, 'deviceID': identity, 'scope': SCOPE, 'previousValidUntil': row['previousUntil']}


def browser_cookie(request):
    value = request.cookies.get(SESSION_COOKIE)
    if not _verified(request.app['session_secret'], value, request.app['server_config']['session_ttl_seconds'], 'session'):
        raise web.HTTPForbidden(text='Sign in with your browser to approve connections.')
    return value


def csrf(request):
    return hmac.new(request.app['session_secret'].encode(), ('connections:' + browser_cookie(request)).encode(),
                    hashlib.sha256).hexdigest()


def owner(request, *, mutation=False):
    if request.get('connection_device') or request.get('companion_device') or extension_request(request):
        raise web.HTTPForbidden()
    authorization = request.headers.get('Authorization', '')
    if authorization.lower().startswith('bearer '):
        token = authorization[7:]
        if hmac.compare_digest(token, request.app['control_token']):
            return
        if token.startswith('amt_') and request.app['terminal_setup'].devices.identify(token):
            return
    browser_cookie(request)
    if mutation and not hmac.compare_digest(request.headers.get('X-Connection-CSRF', ''), csrf(request)):
        raise web.HTTPForbidden(text='Reload Connect before trying again.')


def setup_routes(app):
    manager = Connections(app['terminal_setup'].devices, app['device_pairing'], app['session_secret'],
                          owned_root=app['service'].data_dir)
    app['device_connections'] = manager

    async def discover(request):
        require_safe_transport(request)
        return web.json_response({'protocolVersion': 1, 'enrollment': PREFIX + '/enroll',
            'token': PREFIX + '/token', 'self': PREFIX + '/self', 'grants': GRANTS,
            'transport': 'outbound-poll', 'heartbeatSeconds': 10, 'leaseSeconds': LEASE_TTL})

    async def enroll(request):
        require_safe_transport(request)
        return web.json_response(manager.begin(await body(request)))

    async def token(request):
        require_safe_transport(request)
        value = await body(request)
        pending = manager.pairings.find(value.get('id', ''))
        if not pending.get('enrollment'):
            raise web.HTTPBadRequest(text='Use the original companion pairing endpoint.')
        return web.json_response(manager.pairings.poll(value.get('id'), value.get('verifier')))

    async def qr(request):
        require_safe_transport(request)
        row = manager.pairings.find(request.query.get('id', ''))
        if not row.get('enrollment'):
            raise web.HTTPNotFound()
        import qrcode
        from qrcode.image.svg import SvgPathFillImage
        target = f'{request.scheme}://{request.host}/connect/verify?id=' + request.query['id']
        image = qrcode.make(target, image_factory=SvgPathFillImage)
        return web.Response(body=image.to_string(), content_type='image/svg+xml')

    async def self_api(request):
        require_safe_transport(request)
        identity = request.get('connection_device')
        if not identity:
            raise web.HTTPForbidden(text='This endpoint requires the connected app’s credential.')
        device = manager.device(identity)
        suffix = request.path.removeprefix(PREFIX + '/self')
        if suffix == '':
            return web.json_response({k: device[k] for k in ('id', 'name', 'scope', 'grants', 'appId')})
        payload = await body(request)
        if suffix == '/heartbeat':
            result = manager.heartbeat(identity, payload)
        elif suffix == '/poll':
            result = await manager.poll(identity)
        elif suffix == '/result':
            result = manager.result(identity, payload)
        elif suffix == '/stop':
            result = manager.stop(identity)
        elif suffix == '/rotate':
            result = manager.rotate(identity, payload.get('rotationId'))
        elif suffix == '/upgrade':
            payload['appId'] = device['appId']
            result = manager.begin(payload, upgrade=identity, token=request.headers['Authorization'][7:])
        else:
            raise web.HTTPNotFound()
        return web.json_response(result)

    async def owner_api(request):
        require_safe_transport(request)
        owner(request, mutation=request.method == 'POST')
        tail = request.match_info['tail']
        if request.method == 'GET':
            if tail == 'devices':
                return web.json_response({'devices': manager.listing(),
                    'operations': [manager.receipt(r) for r in list(manager.operations.values())[-40:]],
                    'csrf': csrf(request) if not request.headers.get('Authorization') else None})
            if tail.startswith('setups/'):
                return web.json_response(manager.setup_status(tail.removeprefix('setups/')))
            if tail.startswith('operations/'):
                manager.refresh()
                row = manager.lookup(tail.removeprefix('operations/'))
                if row:
                    return web.json_response(manager.receipt(row))
            raise web.HTTPNotFound()
        payload = await body(request)
        if tail == 'setups':
            result = manager.setup()
        elif tail == 'operations':
            result = manager.submit(payload)
        elif re.fullmatch(r'devices/[a-f0-9]{32}/revoke', tail):
            identity = tail.split('/')[1]
            manager.stop(identity, 'Access removed.')
            manager.devices.revoke(identity)
            result = {'status': 'revoked'}
        elif tail.startswith('operations/') and tail.endswith('/cancel'):
            result = manager.cancel(tail.split('/')[1])
        else:
            raise web.HTTPNotFound()
        return web.json_response(result)

    async def preflight(request):
        if not extension_request(request):
            raise web.HTTPForbidden()
        return web.Response(status=204)

    app.router.add_get(PREFIX, discover)
    app.router.add_post(PREFIX + '/enroll', enroll)
    app.router.add_post(PREFIX + '/token', token)
    app.router.add_get(PREFIX + '/qr', qr)
    for suffix, methods in SELF_METHODS.items():
        for method in methods:
            app.router.add_route(method, PREFIX + suffix, self_api)
    app.router.add_route('OPTIONS', PREFIX + '/{tail:.*}', preflight)
    app.router.add_route('OPTIONS', PREFIX, preflight)
    app.router.add_get(PREFIX + '/{tail:.*}', owner_api)
    app.router.add_post(PREFIX + '/{tail:.*}', owner_api)
