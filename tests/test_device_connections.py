import asyncio
import json
import re
import secrets
import time

import pytest
from aiohttp import web

from amplifier_web.auth import SESSION_COOKIE, new_session
from amplifier_web.device_connections import Connections, PREFIX
from amplifier_web.device_pairing import Pairings, challenge
from amplifier_web.server import create_app
from amplifier_web.terminal_devices import TerminalDevices
from test_service import Runtime


@pytest.fixture
def manager(tmp_path):
    devices = TerminalDevices(tmp_path)
    return Connections(devices, Pairings(devices), 'test-secret')


def enrollment(manager, access=None, **extra):
    verifier = secrets.token_urlsafe(32)
    pending = manager.begin({'name': 'Independent client', 'appId': 'fixture.v1',
                             'grants': access or ['connection.ping'], 'challenge': challenge(verifier), **extra})
    manager.pairings.decide(pending['id'], True)
    return manager.pairings.poll(pending['id'], verifier)


def ready(manager, device, *, revision='document-1', instance='instance-1'):
    manager.heartbeat(device['deviceID'], {'instanceId': instance, 'targets': [
        {'id': 'tab-1', 'revision': revision, 'label': 'Explicitly selected tab',
         'ready': True, 'capabilities': device['grants']}]})


def operation(device, **patch):
    return {'id': secrets.token_hex(16), 'deviceId': device['deviceID'], 'targetId': 'tab-1',
            'revision': 'document-1', 'action': 'connection.ping', 'args': {}, **patch}


def test_connection_scope_setup_and_private_store(manager):
    setup = manager.setup()
    device = enrollment(manager, setupId=setup['id'])
    assert device['scope'] == 'device-v1'
    assert device['token'].startswith('amd_')
    assert manager.devices.identify(device['token']) == device['deviceID']
    assert manager.devices.identify('amt_' + device['token'][4:]) is None
    assert manager.devices.identify('amc_' + device['token'][4:]) is None
    assert device['token'] not in manager.devices.path.read_text()
    assert manager.setup_status(setup['id'])['status'] == 'paired'
    ready(manager, device)
    assert manager.setup_status(setup['id'])['status'] == 'ready'
    manager.presence[device['deviceID']]['seen'] -= 46
    assert manager.listing()[0]['connected'] is False
    assert manager.listing()[0]['targets'] == []
    with pytest.raises(web.HTTPConflict):
        manager.submit(operation(device))


@pytest.mark.parametrize('access', [[], ['arbitrary.shell'], ['browser.act'], [123], 'connection.ping'])
def test_unknown_or_incomplete_grants_rejected(manager, access):
    with pytest.raises(web.HTTPBadRequest):
        manager.begin({'name': 'Client', 'appId': 'fixture', 'challenge': challenge(secrets.token_urlsafe(32)), 'grants': access})


def test_incremental_grants_require_new_proof_and_approval(manager):
    device = enrollment(manager)
    verifier = secrets.token_urlsafe(32)
    payload = {'appId': 'fixture', 'grants': ['browser.observe', 'browser.act'], 'challenge': challenge(verifier)}
    pending = manager.begin(payload, upgrade=device['deviceID'], token=device['token'])
    assert manager.device(device['deviceID'])['grants'] == ['connection.ping']
    with pytest.raises(web.HTTPForbidden):
        manager.pairings.poll(pending['id'], secrets.token_urlsafe(32))
    manager.pairings.decide(pending['id'], False)
    assert manager.device(device['deviceID'])['grants'] == ['connection.ping']
    pending = manager.begin(payload, upgrade=device['deviceID'], token=device['token'])
    manager.pairings.decide(pending['id'], True)
    result = manager.pairings.poll(pending['id'], verifier)
    assert result['deviceID'] == device['deviceID'] and result['token'] == device['token']
    assert result['grants'] == ['browser.act', 'browser.observe', 'connection.ping']
    assert len(manager.devices.listing()) == 1
    other = enrollment(manager)
    assert manager.device(other['deviceID'])['grants'] == ['connection.ping']


def test_advertisement_is_bounded_by_grants_and_readiness(manager):
    device = enrollment(manager)
    with pytest.raises(web.HTTPForbidden):
        manager.heartbeat(device['deviceID'], {'instanceId': 'one', 'targets': [
            {'id': 'tab', 'revision': 'doc', 'label': 'Tab', 'ready': True, 'capabilities': ['browser.act']}]})
    ready(manager, device)
    with pytest.raises(web.HTTPConflict):
        manager.submit(operation(device, revision='other-document'))
    with pytest.raises(web.HTTPForbidden):
        manager.submit(operation(device, action='browser.observe'))
    with pytest.raises(web.HTTPBadRequest):
        manager.submit(operation(device, action='javascript.evaluate', args={'source': 'anything'}))


async def test_receipt_idempotency_and_no_redelivery(manager):
    device = enrollment(manager, ['browser.observe', 'browser.act'])
    ready(manager, device)
    command = operation(device, action='browser.click', args={'selector': '#save'})
    accepted = manager.submit(command)
    assert manager.submit(command) == accepted
    with pytest.raises(web.HTTPConflict):
        manager.submit({**command, 'revision': 'other'})
    delivered = (await manager.poll(device['deviceID']))['operation']
    assert delivered['id'] == command['id']
    assert 'lease' not in manager.receipt(manager.operations[command['id']])
    receipt = {'id': delivered['id'], 'lease': delivered['lease'], 'status': 'completed', 'result': {'message': 'pong'}}
    assert manager.result(device['deviceID'], receipt)['status'] == 'completed'
    assert manager.result(device['deviceID'], receipt)['status'] == 'completed'
    assert manager.submit(command)['status'] == 'completed'
    with pytest.raises(web.HTTPConflict):
        manager.result(device['deviceID'], {**receipt, 'result': {'different': True}})
    assert len(manager.operations) == 1


async def test_unknown_outcomes_restart_and_late_receipts(manager):
    device = enrollment(manager, ['browser.observe', 'browser.act'])
    ready(manager, device)
    command = operation(device, action='browser.click', args={'selector': '#save'})
    manager.submit(command)
    delivered = (await manager.poll(device['deviceID']))['operation']
    manager.operations[command['id']]['deadline'] = time.time() - 1
    manager.refresh()
    assert manager.operations[command['id']]['status'] == 'unknown'
    manager.save()
    restarted = Connections(manager.devices, Pairings(manager.devices), 'test-secret')
    assert restarted.operations[command['id']]['status'] == 'unknown'
    assert restarted.listing()[0]['connected'] is False
    assert manager.devices.identify(device['token']) == device['deviceID']
    ready(restarted, device, instance='after-restart')
    second = operation(device, action='browser.click', args={'selector': '#next'})
    restarted.submit(second)
    # Receiving new work cannot replay the uncertain previous effect.
    assert (await restarted.poll(device['deviceID']))['operation']['id'] == second['id']
    assert restarted.result(device['deviceID'], {'id': command['id'], 'lease': delivered['lease'],
        'status': 'completed', 'result': {'message': 'late acknowledgement'}})['status'] == 'completed'


async def test_stop_navigation_revocation_and_cross_device_receipts(manager):
    device = enrollment(manager)
    other = enrollment(manager)
    ready(manager, device)
    command = operation(device)
    manager.submit(command)
    delivered = (await manager.poll(device['deviceID']))['operation']
    with pytest.raises(web.HTTPNotFound):
        manager.result(other['deviceID'], {'id': command['id'], 'lease': delivered['lease'], 'status': 'completed', 'result': {}})
    queued = operation(device)
    manager.submit(queued)
    manager.stop(device['deviceID'])
    assert manager.lookup(command['id'])['status'] == 'unknown'
    assert manager.lookup(queued['id'])['status'] == 'cancelled'
    ready(manager, device)
    navigated = operation(device)
    manager.submit(navigated)
    ready(manager, device, revision='document-2')
    assert manager.lookup(navigated['id'])['status'] == 'cancelled'
    manager.devices.revoke(device['deviceID'])
    assert manager.devices.identify(device['token']) is None
    with pytest.raises(web.HTTPUnauthorized):
        await manager.poll(device['deviceID'])


def test_rotation_retry_grace_and_revocation(manager):
    device = enrollment(manager)
    rotated = manager.rotate(device['deviceID'], 'durable-random-rotation-id')
    assert rotated == manager.rotate(device['deviceID'], 'durable-random-rotation-id')
    assert rotated['token'] != device['token']
    assert manager.devices.identify(device['token']) == device['deviceID']
    assert manager.devices.identify(rotated['token']) == device['deviceID']
    assert rotated['token'] not in manager.devices.path.read_text()
    with manager.devices.transaction() as state:
        state['devices'][device['deviceID']]['previousUntil'] = time.time() - 1
    assert manager.devices.identify(device['token']) is None
    assert manager.devices.identify(rotated['token']) == device['deviceID']
    manager.devices.revoke(device['deviceID'])
    assert manager.devices.identify(rotated['token']) is None


def test_failed_durable_admission_is_not_cached_as_accepted(manager, monkeypatch):
    device = enrollment(manager, ['browser.observe', 'browser.act'])
    ready(manager, device)
    command = operation(device, action='browser.click', args={'selector': '#save'})
    from amplifier_web import device_connections
    original = device_connections.write_private
    def fail(*args, **kwargs):
        raise OSError('Synthetic full disk')
    monkeypatch.setattr(device_connections, 'write_private', fail)
    with pytest.raises(OSError):
        manager.submit(command)
    assert command['id'] not in manager.operations
    monkeypatch.setattr(device_connections, 'write_private', original)
    assert manager.submit(command)['status'] == 'queued'


async def test_public_contract_authentication_and_web_consent(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), preload_providers=False,
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    manager = app['device_connections']
    discovery = await client.get(PREFIX)
    assert discovery.status == 200 and (await discovery.json())['protocolVersion'] == 1
    assert 'X-Amplifier-History-Scope' not in discovery.headers
    assert (await client.get(PREFIX + '/devices')).status == 401
    cookie = {'Cookie': SESSION_COOKIE + '=' + new_session(app['session_secret'])}
    verifier = secrets.token_urlsafe(32)
    payload = {'name': '<Browser>', 'appId': 'independent-test', 'challenge': challenge(verifier),
               'grants': ['browser.observe', 'browser.act']}
    extension = {'Origin': 'chrome-extension://' + 'a' * 32, 'Sec-Fetch-Site': 'cross-site'}
    pending_response = await client.post(PREFIX + '/enroll', json=payload, headers=extension)
    assert pending_response.status == 200
    assert pending_response.headers['Access-Control-Allow-Origin'] == extension['Origin']
    pending = await pending_response.json()
    assert (await client.get(pending['verificationPath'], allow_redirects=False)).status == 307
    consent = await client.get(pending['verificationPath'], headers=cookie)
    text = await consent.text()
    assert '&lt;Browser&gt;' in text and 'Click and type' in text and pending['code'] in text
    csrf = re.search(r'name="csrf" value="([^"]+)"', text)[1]
    assert (await client.post(pending['verificationPath'], headers=cookie, data={'decision': 'allow'})).status == 403
    # Old chat consent must never approve a broader new enrollment.
    legacy = await client.post('/devices/pair?id=' + pending['id'], headers=cookie,
                               data={'decision': 'allow', 'csrf': csrf}, allow_redirects=False)
    assert legacy.status == 303
    assert manager.pairings.pending[pending['id']]['state'] == 'pending'
    assert (await client.post(pending['verificationPath'], headers=cookie, data={'decision': 'allow', 'csrf': csrf})).status == 200
    approved = await (await client.post(PREFIX + '/token', headers=extension,
        json={'id': pending['id'], 'verifier': verifier})).json()
    headers = {**extension, 'Authorization': 'Bearer ' + approved['token']}
    identity = await client.get(PREFIX + '/self', headers=headers)
    assert identity.status == 200
    assert 'X-Amplifier-History-Scope' not in identity.headers
    # Exercise credential scope without a cross-origin rejection masking it.
    # A valid browser cookie cannot promote an execution token into a web client.
    attached = await client.post('/api/clients/attach',
        headers={**cookie, 'Authorization': headers['Authorization']},
        json={'clientId': 'execution-token-browser', 'kind': 'web'})
    assert attached.status == 403
    assert 'X-Amplifier-History-Scope' not in attached.headers
    assert (await client.get('/api/state', headers={**headers, **cookie})).status == 403
    assert (await client.get(PREFIX + '/devices', headers={**headers, **cookie})).status == 403
    assert (await client.get(PREFIX + '/self', headers={**extension, **cookie})).status == 401
    heartbeat = await client.post(PREFIX + '/self/heartbeat', headers={**headers, **cookie},
        json={'instanceId': 'browser-instance', 'targets': []})
    assert heartbeat.status == 200
    assert 'X-Amplifier-History-Scope' not in heartbeat.headers
    listing = await (await client.get(PREFIX + '/devices', headers=cookie)).json()
    assert listing['devices'][0]['id'] == approved['deviceID']
    assert 'token' not in json.dumps(listing) and 'digest' not in json.dumps(listing)
    path = PREFIX + '/devices/' + approved['deviceID'] + '/revoke'
    assert (await client.post(path, headers=cookie, json={})).status == 403
    assert (await client.post(path, headers={**cookie, 'Authorization': 'Bearer invalid'}, json={})).status == 403
    assert (await client.post(path, headers={**cookie, 'X-Connection-CSRF': listing['csrf']}, json={})).status == 200
    assert (await client.get(PREFIX + '/self', headers={**headers, **cookie})).status == 401
    assert (await client.post(PREFIX + '/enroll', headers={'Origin': 'https://evil.example'}, json=payload)).status == 403
    assert (await client.post(PREFIX + '/token', json={'id': [], 'verifier': verifier})).status == 400


async def test_denial_code_entry_and_upgrade_revocation(aiohttp_client, tmp_path):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), preload_providers=False,
                           voice=False, background_updates=False)
    client = await aiohttp_client(app)
    manager = app['device_connections']
    cookie = {'Cookie': SESSION_COOKIE + '=' + new_session(app['session_secret'])}
    verifier = secrets.token_urlsafe(32)
    pending = manager.begin({'name': 'Other client', 'appId': 'other-client', 'grants': ['connection.ping'],
                             'challenge': challenge(verifier)})
    response = await client.get('/connect/code?code=' + pending['code'], headers=cookie, allow_redirects=False)
    assert response.status == 303 and response.headers['Location'] == pending['verificationPath']
    manager.pairings.decide(pending['id'], False)
    assert manager.pairings.poll(pending['id'], verifier)['status'] == 'denied'
    device = enrollment(manager)
    upgrade = manager.begin({'appId': 'other-client', 'grants': ['browser.observe'],
                             'challenge': challenge(verifier)}, upgrade=device['deviceID'], token=device['token'])
    manager.devices.revoke(device['deviceID'])
    with pytest.raises(web.HTTPGone):
        manager.pairings.decide(upgrade['id'], True)
    assert not manager.devices.listing()


async def test_live_observations_do_not_fill_durable_history_or_starve_input(manager, monkeypatch):
    from amplifier_web.device_connections import MAX_TRANSIENT_PER_DEVICE
    device = enrollment(manager, ['browser.observe', 'browser.act', 'browser.task'])
    ready(manager, device)
    writes = []
    original = manager.save
    monkeypatch.setattr(manager, 'save', lambda: (writes.append(True), original())[1])
    first = None
    for _ in range(1005):
        row = manager.submit(operation(device, action='browser.task.screenshot'))
        first = first or row['id']
        delivered = (await manager.poll(device['deviceID']))['operation']
        assert delivered['retention'] == 'transient'
        receipt = {'id': row['id'], 'lease': delivered['lease'], 'status': 'completed',
                   'result': {'image': 'bounded-image', 'mimeType': 'image/jpeg'}}
        result = manager.result(device['deviceID'], receipt)
        assert manager.result(device['deviceID'], receipt) == result
    assert not writes and not manager.operations
    assert len(manager.transient_operations) == MAX_TRANSIENT_PER_DEVICE
    assert manager.lookup(first) is None
    assert not manager.path.exists()
    # A hot viewer may have at most two reads in flight, leaving room for input.
    for _ in range(2):
        manager.submit(operation(device, action='browser.task.screenshot'))
    with pytest.raises(web.HTTPTooManyRequests):
        manager.submit(operation(device, action='browser.task.observe'))
    command = operation(device, action='browser.click', args={'selector': '#save'})
    manager.submit(command)
    assert (await manager.poll(device['deviceID']))['operation']['id'] == command['id']
    assert set(manager.operations) == {command['id']}
    assert 'bounded-image' not in manager.path.read_text()


async def test_transient_global_bound_expiry_restart_and_matching_receipts(manager):
    from amplifier_web.device_connections import MAX_TRANSIENT, MAX_TRANSIENT_PER_DEVICE, TRANSIENT_TTL
    devices = [enrollment(manager) for _ in range(5)]
    for device in devices:
        ready(manager, device)
        for _ in range(MAX_TRANSIENT_PER_DEVICE):
            command = operation(device)
            manager.submit(command)
            delivery = (await manager.poll(device['deviceID']))['operation']
            manager.result(device['deviceID'], {'id': command['id'], 'lease': delivery['lease'],
                'status': 'completed', 'result': {'message': 'pong'}})
    assert len(manager.transient_operations) == MAX_TRANSIENT
    device = devices[-1]
    command = operation(device)
    manager.submit(command)
    delivery = (await manager.poll(device['deviceID']))['operation']
    with pytest.raises(web.HTTPForbidden):
        manager.result(device['deviceID'], {'id': command['id'], 'lease': 'not-the-lease', 'status': 'completed', 'result': {}})
    manager.lookup(command['id'])['deadline'] = time.time() - 1
    manager.refresh()
    assert manager.lookup(command['id'])['status'] == 'unknown'
    # A late matching result resolves uncertainty while the read receipt remains.
    receipt = {'id': command['id'], 'lease': delivery['lease'], 'status': 'completed', 'result': {'message': 'late'}}
    manager.result(device['deviceID'], receipt)
    manager.lookup(command['id'])['createdAt'] = time.time() - TRANSIENT_TTL - 1
    manager.refresh()
    assert manager.lookup(command['id']) is None
    with pytest.raises(web.HTTPNotFound):
        manager.result(device['deviceID'], receipt)
    restarted = Connections(manager.devices, Pairings(manager.devices), 'test-secret')
    assert not restarted.transient_operations and not restarted.operations


async def test_effectful_delivery_keeps_full_pending_bound_and_read_cache_respects_payload_limit(manager):
    device = enrollment(manager, ['browser.observe', 'browser.act'])
    ready(manager, device)
    read = operation(device, action='browser.observe')
    manager.submit(read)
    delivered = (await manager.poll(device['deviceID']))['operation']
    with pytest.raises(web.HTTPBadRequest):
        manager.result(device['deviceID'], {'id': read['id'], 'lease': delivered['lease'],
            'status': 'completed', 'result': {'text': 'x' * 262144}})
    assert manager.lookup(read['id'])['status'] == 'delivered'
    for _ in range(9):
        manager.submit(operation(device, action='browser.click', args={'selector': '#save'}))
    with pytest.raises(web.HTTPTooManyRequests):
        manager.submit(operation(device, action='browser.click', args={'selector': '#save'}))
    manager.stop(device['deviceID'])
    assert manager.lookup(read['id'])['status'] == 'unknown'
    assert all(row['status'] == 'cancelled' for row in manager.operations.values())


async def test_result_limit_counts_utf8_and_handles_escaped_surrogates(manager):
    device = enrollment(manager, ['browser.observe'])
    ready(manager, device)
    command = operation(device, action='browser.observe')
    manager.submit(command)
    delivered = (await manager.poll(device['deviceID']))['operation']
    receipt = {'id': command['id'], 'lease': delivered['lease'], 'status': 'completed',
               'result': {'text': '\U0001f642' * 60000 + '\ud800'}}
    assert manager.result(device['deviceID'], receipt)['status'] == 'completed'
    other = operation(device, action='browser.observe')
    manager.submit(other)
    delivered = (await manager.poll(device['deviceID']))['operation']
    with pytest.raises(web.HTTPBadRequest):
        manager.result(device['deviceID'], {'id': other['id'], 'lease': delivered['lease'],
            'status': 'completed', 'result': {'text': '\U0001f642' * 65536}})
