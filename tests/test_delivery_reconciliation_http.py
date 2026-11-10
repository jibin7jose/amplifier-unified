"""Causal HTTP recovery tests; a gated runtime replaces workers, never AppService."""
import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web

from amplifier_web.runtime import RuntimeOperationPending
from amplifier_web.server import create_app
from test_service import Runtime


INPUT = 'original-http-input'


class GatedRuntime(Runtime):
    def __init__(self, mode='accepted', evidence=None):
        super().__init__()
        self.mode = mode
        self.evidence = set() if evidence is None else evidence
        self.entered, self.ack = asyncio.Event(), asyncio.Event()
        self.probing, self.probe_release = asyncio.Event(), asyncio.Event()
        self.probe_release.set()
        self.probes = []
        if mode != 'lost-http':
            self.ack.set()

    async def start(self, *args):
        pytest.fail('Recovery must not start a worker')

    async def retry(self, *args, **kwargs):
        pytest.fail('The original input must remain fenced, not be resent')

    async def send(self, session, text, input_id, emit):
        if self.sent or self.mode == 'read-only':
            pytest.fail('Unexpected runtime send/replay')
        self.sent.append((session['id'], text, input_id))
        self.evidence.add((session['id'], input_id))  # Only actual admission creates proof.
        self.emit, self.dispatch_task = emit, asyncio.current_task()
        self.entered.set()
        if self.mode == 'lost-worker':
            raise RuntimeOperationPending('send')
        await self.ack.wait()
        return {'accepted': True, 'inputId': input_id}

    async def delivery(self, session, input_id):
        key = (session['id'], input_id)
        self.probes.append(key)
        # Capture before yielding: a late unknown read must not erase a newer ACK.
        result = 'accepted' if key in self.evidence and self.mode != 'lost-worker' else 'unknown'
        self.probing.set()
        await self.probe_release.wait()
        return result

    async def late_ack(self):
        sid, _, input_id = self.sent[0]
        await self.emit('runtime.delivery', {'sessionId': sid, 'inputId': input_id})


async def action(h, name, args, identity=None, status=200):
    response = await h.client.post('/api/actions', json={'action': name, 'args': args, 'id': identity})
    body = await response.json()
    assert response.status == status, body
    return body


async def check(h, input_id=INPUT):
    body = await action(h, 'conversation.delivery', {'sessionId': h.sid, 'inputId': input_id})
    result = body['result']
    assert (result['sessionId'], result['inputId']) == (h.sid, input_id)
    return result


async def cancel_request(task, cancelled):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(cancelled.wait(), 3)  # Actual server disconnect, not only client cancellation.


@pytest.fixture
async def http_app(tmp_path, authenticated_client):
    opened = []

    async def create(runtime=None, before_admission=False):
        runtime = runtime or GatedRuntime()
        app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=runtime,
            voice=False, preload_providers=False, background_updates=False, background_work=False)
        # Enable aiohttp's disconnect cancellation so removing shield is observable.
        app._handler_args = {'handler_cancellation': True}
        h = SimpleNamespace(service=app['service'], runtime=runtime,
                            arrived=asyncio.Event(), cancelled=asyncio.Event())

        @web.middleware
        async def disconnect_gate(request, handler):
            if request.path == '/api/actions' and (await request.json()).get('id') == INPUT:
                h.arrived.set()
                try:
                    if before_admission:
                        await asyncio.Event().wait()
                    return await handler(request)
                except asyncio.CancelledError:
                    h.cancelled.set()
                    raise
            return await handler(request)

        app.middlewares.append(disconnect_gate)
        h.client = await authenticated_client(app)
        opened.append(h)
        if not h.service.state['sessions']:
            await action(h, 'session.create', {})
        h.sid = h.service._session()['id']
        return h

    yield create
    for h in reversed(opened):
        h.runtime.ack.set()
        h.runtime.probe_release.set()
        await h.client.close()


@pytest.mark.parametrize('identities', [
    {}, {'nativeIdentity': 'native-fallback'},
    {'runtimeSessionId': 'runtime-source', 'nativeIdentity': 'unused-fallback'},
])
async def test_compact_delivery_receipt_binds_effective_native_source_without_state(http_app, identities):
    h = await http_app(GatedRuntime('read-only'))
    session = h.service._session(h.sid)
    session.update(identities)
    h.runtime.evidence.add((h.sid, INPUT))  # Passive-source witness, no send effect.
    response = await h.client.post('/api/actions',
        headers={'X-Amplifier-State-Transport': 'delta-v1'},
        json={'action': 'conversation.delivery', 'args': {'sessionId': h.sid, 'inputId': INPUT}})
    assert response.status == 200
    body = await response.json()
    assert 'state' not in body
    assert body['hostInstanceId'] == h.service.instance_id
    assert body['result']['delivery'] == 'accepted'
    assert body['result']['source'] == {
        'workspace': session['workspace'],
        'nativeIdentity': identities.get('runtimeSessionId') or identities.get('nativeIdentity') or h.sid,
    }
    assert h.runtime.sent == []


async def test_admitted_send_survives_lost_http_ack_and_confirms_exactly_once(http_app):
    h = await http_app(GatedRuntime('lost-http'))
    args = {'sessionId': h.sid, 'text': 'one original input'}
    request = asyncio.create_task(action(h, 'conversation.send', args, INPUT))
    await asyncio.wait_for(h.runtime.entered.wait(), 3)
    await cancel_request(request, h.cancelled)
    try:
        assert not h.runtime.dispatch_task.done()  # The cancelled HTTP handler did not cancel admission.
        assert (await check(h))['delivery'] == 'accepted'
        assert (await check(h, 'unrelated-input'))['delivery'] == 'not_saved'
    finally:
        h.runtime.ack.set()
    receipt = await asyncio.wait_for(asyncio.shield(h.runtime.dispatch_task), 3)
    assert receipt['inputId'] == INPUT and receipt['delivery'] == 'accepted'
    duplicate = await action(h, 'conversation.send', args, INPUT)
    assert duplicate['duplicate'] and duplicate['delivery'] == 'accepted'
    assert h.runtime.sent == [(h.sid, args['text'], INPUT)]
    assert [m['inputId'] for m in h.service._session()['messages'] if m['role'] == 'user'] == [INPUT]


async def test_lost_worker_ack_check_retry_and_late_ack_keep_original_fence(http_app):
    h = await http_app(GatedRuntime('lost-worker'))
    args = {'sessionId': h.sid, 'text': 'one original input'}
    pending = await action(h, 'conversation.send', args, INPUT, status=504)
    assert pending['code'] == 'runtime_pending' and pending['delivery'] == 'unknown'
    h.runtime.probe_release.clear()
    checking = asyncio.create_task(check(h))
    await asyncio.wait_for(h.runtime.probing.wait(), 3)
    retry = {'sessionId': h.sid, 'inputId': INPUT, 'confirmUncertain': True}
    try:
        refused = await action(h, 'conversation.retry', retry, status=409)
        assert 'settle' in refused['error']
        duplicate = await action(h, 'conversation.send', args, INPUT)
        assert duplicate['duplicate'] and duplicate['delivery'] == 'unknown'
        await h.runtime.late_ack()
        assert (await action(h, 'conversation.retry', retry))['result'] == {'delivery': 'accepted', 'resent': False}
    finally:
        h.runtime.probe_release.set()
    assert (await asyncio.wait_for(checking, 3))['delivery'] == 'accepted'
    assert h.runtime.probes == [(h.sid, INPUT)]
    assert h.runtime.sent == [(h.sid, args['text'], INPUT)]


async def test_restart_without_host_receipt_or_message_uses_admitted_native_evidence(http_app):
    h = await http_app()
    sent = await action(h, 'conversation.send', {'sessionId': h.sid, 'text': 'saved natively'}, INPUT)
    assert sent['delivery'] == 'accepted'
    # Lose only the host copies after real admission, not the runtime's evidence.
    h.service.db.execute('DELETE FROM commands WHERE id=?', (INPUT,))
    h.service._session()['messages'] = []
    h.service._publish_changes(sessions={h.sid})
    await h.client.close()
    restarted = await http_app(GatedRuntime('read-only', h.runtime.evidence))
    assert restarted.sid == h.sid and restarted.service._session()['messages'] == []
    assert restarted.service.db.execute('SELECT receipt FROM commands WHERE id=?', (INPUT,)).fetchone() is None
    assert (await check(restarted))['delivery'] == 'accepted'
    assert restarted.runtime.probes == [(h.sid, INPUT)]
    assert restarted.runtime.sent == [] and restarted.runtime.started == []
    assert restarted.service._session()['messages'] == []
    assert restarted.service.db.execute('SELECT receipt FROM commands WHERE id=?', (INPUT,)).fetchone() is None


async def test_drop_before_admission_is_not_saved_not_proven_undelivered(http_app):
    h = await http_app(GatedRuntime('read-only'), before_admission=True)
    request = asyncio.create_task(action(h, 'conversation.send', {'sessionId': h.sid, 'text': 'never admitted'}, INPUT))
    await asyncio.wait_for(h.arrived.wait(), 3)
    await cancel_request(request, h.cancelled)
    result = await check(h)
    assert result['delivery'] == 'not_saved'
    assert 'does not prove non-delivery' in result['message'] and 'does not resend' in result['message']
    assert not h.runtime.entered.is_set() and h.runtime.evidence == set()
    assert h.service._session()['messages'] == []
    assert h.service.db.execute('SELECT receipt FROM commands WHERE id=?', (INPUT,)).fetchone() is None


async def test_stop_during_passive_check_and_late_ack_never_restart_work(http_app):
    h = await http_app(GatedRuntime('lost-worker'))
    await action(h, 'conversation.send', {'sessionId': h.sid, 'text': 'stop this input'}, INPUT, status=504)
    h.runtime.probe_release.clear()
    checking = asyncio.create_task(check(h))
    await asyncio.wait_for(h.runtime.probing.wait(), 3)
    try:
        await action(h, 'conversation.stop', {'sessionId': h.sid})
        async with asyncio.timeout(3):
            while h.service._session()['status'] != 'stopped':
                await asyncio.sleep(0)
        revision = h.service._session()['interruptionRevision']
        await h.runtime.late_ack()
    finally:
        h.runtime.probe_release.set()
    assert (await asyncio.wait_for(checking, 3))['delivery'] == 'accepted'
    assert h.service._session()['status'] == 'stopped'
    assert h.service._session()['interruptionRevision'] == revision
    assert h.runtime.stopped == [h.sid] and h.runtime.started == []
    assert h.runtime.sent == [(h.sid, 'stop this input', INPUT)]
