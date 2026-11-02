"""Browser-approved, proof-bound pairing for restricted native companions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import secrets
import time
from collections import deque

from aiohttp import web

from .auth import SESSION_COOKIE, _verified
from .terminal_devices import MAX_DEVICES, digest
from .terminal_setup import require_safe_transport

TTL = 600
SCOPE = 'companion-v1'
PROOF = re.compile(r'[A-Za-z0-9_-]{43}')


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()


async def permits_companion(request):
    """Fail closed before dispatch; never grant a generic action capability."""
    if request.method == 'GET':
        if request.path in {'/api/companion', '/api/activity'}:
            return True
        return request.path == '/api/conversation/detail' and request.query.get('part') in {'messages', 'nodes'}
    if request.method != 'POST':
        return False
    body = await request.json()
    if not isinstance(body, dict):
        return False
    if request.path == '/api/clients/attach':
        return body.get('kind') == 'native' and body.get('protocolVersion') == 1
    args = body.get('args', {})
    if not isinstance(args, dict):
        return False
    if request.path == '/api/actions' and body.get('action') == 'session.create':
        return (set(args) <= {'id', 'title', 'location', 'select'}
                and args.get('location') == {'kind': 'managed'} and args.get('select') is False)
    if re.fullmatch(r'/api/sessions/[^/]+/commands', request.path) and body.get('action') == 'conversation.send':
        return (set(args) <= {'text', 'via', 'preserveDraft', 'attachmentIds'}
                and args.get('via') == 'chat' and args.get('preserveDraft') is True
                and args.get('attachmentIds') == [])
    return False


def command_admission(request):
    """Carry revocable authority into shielded work, through pre-admission waits."""
    identity = request.get('companion_device')
    if identity is None:
        return None
    devices = request.app['terminal_setup'].devices
    token = request.headers['Authorization'][7:]

    def check():
        if devices.identify(token) != identity:
            from .service import AppError
            raise AppError('Device access was removed before this command was accepted.', 403)
    return check


def companion_receipt(value):
    """An authorization projection, independent of caller transport choices."""
    return {key: value[key] for key in ('accepted', 'revision', 'sessionId',
            'hostInstanceId', 'stateRevision', 'delivery', 'code') if key in value}


class Pairings:
    def __init__(self, devices):
        self.devices = devices
        self.pending = {}
        self.starts = deque(maxlen=60)

    def clean(self):
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v['expires'] > now}
        while self.starts and self.starts[0] <= now - 60:
            self.starts.popleft()

    def start(self, name, proof, *, enrollment=None):
        self.clean()
        if not isinstance(proof, str) or not PROOF.fullmatch(proof):
            raise web.HTTPBadRequest(text='Invalid device proof.')
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise web.HTTPBadRequest(text='Invalid device name.')
        if len(self.pending) >= 20 or len(self.starts) >= 60:
            raise web.HTTPTooManyRequests(text='Please wait before connecting another device.', headers={'Retry-After': '60'})
        self.starts.append(time.time())
        identity = secrets.token_urlsafe(32)
        code = '-'.join(''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(4)) for _ in range(2))
        self.pending[identity] = {'name': name.strip(), 'challenge': proof, 'code': code,
                                  'expires': time.time() + TTL, 'state': 'pending'}
        if enrollment is not None:
            self.pending[identity]['enrollment'] = enrollment
        return {'id': identity, 'code': code, 'verificationPath': '/devices/pair?id=' + identity,
                'expiresIn': TTL, 'interval': 5}

    def find(self, identity):
        self.clean()
        if not isinstance(identity, str):
            raise web.HTTPBadRequest(text='Invalid connection request identifier.')
        row = self.pending.get(identity)
        if row is None:
            raise web.HTTPGone(text='This connection request expired. Start again in the app.')
        return row

    def decide(self, identity, allow):
        row = self.find(identity)
        if row['state'] != 'pending':
            return
        if not allow:
            row['state'] = 'denied'
            return
        enrollment = row.get('enrollment')
        upgrade = enrollment.get('upgradeDevice') if enrollment else None
        device_id = upgrade or secrets.token_hex(16)
        token = (enrollment['upgradeToken'] if upgrade else
                 ('amd_' if enrollment else 'amc_') + device_id + '.' + secrets.token_urlsafe(48))
        with self.devices.transaction() as state:
            if upgrade:
                existing = state['devices'].get(upgrade)
                if not existing or existing.get('scope') != 'device-v1' or self.devices.identify(token) != upgrade:
                    raise web.HTTPGone(text='This app was disconnected. Start a new connection.')
                existing['grants'] = sorted(set(existing['grants']) | set(enrollment['grants']))
                row.update(state='approved', token=token, deviceID=device_id)
                return
            if len(state['devices']) >= MAX_DEVICES:
                raise web.HTTPConflict(text='Remove an unused device before connecting another.')
            state['devices'][device_id] = {'id': device_id, 'name': row['name'], 'scope': SCOPE,
                                            'digest': digest(token), 'createdAt': int(time.time())}
            if enrollment:
                state['devices'][device_id].update(scope='device-v1', appId=enrollment['appId'],
                                                   grants=enrollment['grants'])
        row.update(state='approved', token=token, deviceID=device_id)

    def poll(self, identity, verifier):
        row = self.find(identity)
        if not isinstance(verifier, str) or not PROOF.fullmatch(verifier) or not hmac.compare_digest(challenge(verifier), row['challenge']):
            raise web.HTTPForbidden(text='Invalid device proof.')
        if row['state'] == 'approved':
            if self.devices.identify(row['token']) is None:
                return {'status': 'revoked'}
            result = {'status': 'approved', 'token': row['token'], 'deviceID': row['deviceID'], 'scope': SCOPE}
            if row.get('enrollment'):
                result.update(scope='device-v1', grants=self.devices.read()['devices'][row['deviceID']]['grants'])
            return result
        return {'status': row['state']}


def setup_routes(app):
    manager = Pairings(app['terminal_setup'].devices)
    app['device_pairing'] = manager

    def browser_session(request):
        cookie = request.cookies.get(SESSION_COOKIE)
        if not _verified(app['session_secret'], cookie, app['server_config']['session_ttl_seconds'], 'session'):
            raise web.HTTPForbidden(text='Sign in with your browser to manage devices.')
        return cookie

    def csrf(request, identity):
        cookie = browser_session(request)
        return hmac.new(app['session_secret'].encode(), ('devices:' + cookie + ':' + identity).encode(), hashlib.sha256).hexdigest()

    async def check_form(request, identity):
        form = await request.post()
        if not hmac.compare_digest(str(form.get('csrf', '')), csrf(request, identity)):
            raise web.HTTPForbidden(text='This approval page expired. Reload it and try again.')
        return form

    def page(title, body):
        return web.Response(text='<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + title + '</title><style>:root{color-scheme:light dark}body{font:17px system-ui;max-width:640px;margin:60px auto;padding:24px}'
            'button{font:inherit;padding:10px 20px;margin:8px 12px 8px 0}code{font-size:24px}li{padding:16px 0}</style>'
            '<h1>' + title + '</h1>' + body + '</html>', content_type='text/html', headers={
                'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin',
                'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    async def start(request):
        require_safe_transport(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest()
        return web.json_response(manager.start(body.get('name'), body.get('challenge')))

    async def poll(request):
        require_safe_transport(request)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('id'), str):
            raise web.HTTPBadRequest()
        return web.json_response(manager.poll(body['id'], body.get('verifier')))

    async def approve(request):
        require_safe_transport(request)
        identity = request.query.get('id', '')
        browser_session(request)
        row = manager.find(identity)
        if row.get('enrollment'):
            # Never describe a capability grant using the old chat-only page.
            raise web.HTTPSeeOther('/connect/verify?id=' + identity)
        if request.method == 'POST':
            form = await check_form(request, identity)
            if form.get('decision') not in {'allow', 'deny'}:
                raise web.HTTPBadRequest()
            manager.decide(identity, form['decision'] == 'allow')
        if row['state'] != 'pending':
            return page('Connection ' + ('approved' if row['state'] == 'approved' else 'denied'),
                        '<p>Return to your app.</p><p><a href="/devices">Manage connected devices</a></p>')
        return page('Connect a companion', '<p>Only approve if you just started connecting this app and its code matches:</p><p><code>'
            + html.escape(row['code']) + '</code></p><p>Device: <strong>' + html.escape(row['name']) + '</strong></p>'
            '<p>This device can read chat activity and messages, start managed chats, and send messages. '
            'It cannot change host settings, approve requests, or manage devices. Remove its access at any time.</p>'
            '<form method="post"><input type="hidden" name="csrf" value="' + csrf(request, identity) + '">'
            '<button name="decision" value="allow">Allow connection</button><button name="decision" value="deny">Deny</button></form>')

    async def devices(request):
        require_safe_transport(request)
        browser_session(request)
        if request.method == 'POST':
            form = await check_form(request, 'manage')
            identity = form.get('id', '')
            if not re.fullmatch(r'[a-f0-9]{32}', identity):
                raise web.HTTPBadRequest()
            if 'device_connections' in app:
                app['device_connections'].stop(identity, 'Access removed')
            manager.devices.revoke(identity)
            raise web.HTTPSeeOther('/devices')
        rows = []
        for row in manager.devices.listing():
            scope = ('Chat companion' if row.get('scope') == SCOPE else
                     'Connected app · ' + ', '.join(row.get('grants', []))
                     if row.get('scope') == 'device-v1' else 'Full host access')
            rows.append('<li><strong>' + html.escape(row['name']) + '</strong> — ' + scope
                + '<form method="post"><input type="hidden" name="csrf" value="' + csrf(request, 'manage')
                + '"><input type="hidden" name="id" value="' + row['id']
                + '"><button>Remove access</button></form></li>')
        return page('Connected devices', '<p>Removing access leaves accepted work running.</p><ul>'
                    + ''.join(rows) + '</ul>' + ('' if rows else '<p>No connected devices.</p>'))

    app.router.add_post('/api/devices/pair', start)
    app.router.add_post('/api/devices/pair/token', poll)
    app.router.add_get('/devices/pair', approve, allow_head=False)
    app.router.add_post('/devices/pair', approve)
    app.router.add_get('/devices', devices, allow_head=False)
    app.router.add_post('/devices', devices)
