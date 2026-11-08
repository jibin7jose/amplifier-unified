import asyncio
import sys
from unittest.mock import AsyncMock

import pytest

from amplifier_web.runtime import RuntimeManager, SessionInUseError
from amplifier_web.server import create_app
from amplifier_web.service import AppError, AppService

OWNER = {'app': 'amplifier-cli', 'hostname': 'fixture-host', 'pid': 123,
         'handoff': {'version': 1, 'transport': 'unix'}}
LEGACY = ('This conversation is in use (app: amplifier-cli, pid: 123). '
          'Finish and exit that interface, then retry. Your draft has been kept.')


async def test_windows_release_registration_reports_unavailable_keeps_exclusive_handle(monkeypatch):
    from types import SimpleNamespace
    from amplifier_web import ownership
    import amplifier_foundation.session as foundation
    held = SimpleNamespace(active=True, release=AsyncMock())
    worker = SimpleNamespace(shared_handle=held)
    events = []
    registration = AsyncMock(side_effect=AssertionError('POSIX registration called'))
    monkeypatch.setattr(ownership, '_WINDOWS', True)
    monkeypatch.setattr(foundation, 'register_release_handler', registration)
    owner = ownership.WorkerOwnership(worker, events.append)
    await owner.register()
    registration.assert_not_called()
    held.release.assert_not_called()
    assert worker.shared_handle is held and held.active
    assert owner.registration is None and not owner.yielding
    assert events == [{
        'type': 'runtime.capability', 'capability': 'session.handoff',
        'status': 'unavailable', 'platform': 'windows',
        'detail': 'Stop the current owner before opening this conversation elsewhere.',
    }]


async def test_windows_takeover_and_transfer_refuse_before_acquisition(monkeypatch):
    from amplifier_web import runtime
    monkeypatch.setattr(runtime, '_WINDOWS', True)
    manager = RuntimeManager(command=['unused'])
    manager.start = AsyncMock(side_effect=AssertionError('acquisition attempted'))
    try:
        with pytest.raises(RuntimeError, match='unavailable on Windows'):
            await manager.takeover({'id': 'windows-owner'}, AsyncMock())
        with pytest.raises(RuntimeError, match='unavailable on Windows'):
            await manager.quiesce_for_handoff({'id': 'windows-owner'}, 'request')
        manager.start.assert_not_called()
        assert not manager.workers
    finally:
        await manager.close()


async def test_busy_startup_is_ownership_not_a_runtime_error(tmp_path):
    script = tmp_path / 'busy.py'
    script.write_text('import json,sys\njson.loads(sys.stdin.readline())\n'
                      'print(json.dumps({"type":"runtime.error","code":"session_busy",'
                      '"owner":{"app":"amplifier-cli"},"error":"busy"}),flush=True)\n')
    manager = RuntimeManager(command=[sys.executable, str(script)])
    events = []
    async def emit(kind, payload):
        events.append((kind, payload))
    try:
        with pytest.raises(SessionInUseError):
            await manager.start({'id': 'busy', 'workspace': str(tmp_path)}, emit)
        assert any(kind == 'runtime.ownership' and data['status'] == 'blocked' for kind, data in events)
        assert not any(kind == 'runtime.error' for kind, _ in events)
    finally:
        await manager.close()


async def test_http_conflict_has_one_inline_state_and_preserves_draft(authenticated_client, tmp_path):
    class Runtime:
        close = AsyncMock()
        async def send(self, session, text, input_id, emit):
            await emit('runtime.ownership', {'sessionId': session['id'], 'status': 'blocked', 'owner': OWNER})
            raise SessionInUseError(OWNER)
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {})
    sid = service.state['selectedSessionId']
    await service.dispatch('view.update', {'patch': {'draft': 'Keep my draft'}})
    request = {'action': 'conversation.send', 'args': {'sessionId': sid, 'text': 'Keep my draft'}, 'id': 'once'}
    response = await client.post('/api/actions', json=request)
    assert response.status == 409
    result = await response.json()
    assert result['code'] == 'session_busy'
    assert result['state']['view']['draft'] == 'Keep my draft'
    session = next(s for s in result['state']['sessions'] if s['id'] == sid)
    assert session['status'] == 'read-only'
    assert session['ownership']['source'] == 'Amplifier CLI'
    assert session['ownership']['supportsTakeover']
    assert not session.get('error')
    # Release notices can coexist; a session ownership conflict stays inline.
    assert not [item for item in result['state']['attention']['items'] if item.get('sessionId') == sid]
    replay = await (await client.post('/api/actions', json=request)).json()
    assert replay['code'] == 'session_busy' and not replay['accepted']


async def test_restart_removes_legacy_lock_attention_but_keeps_real_errors(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    ids = []
    for number in range(3):
        await app.dispatch('session.create', {'title': f'Chat {number}'})
        row = app._session(app.state['selectedSessionId'])
        row.update(status='error', error=LEGACY if number < 2 else 'Provider unavailable', lockOwner=OWNER)
        ids.append(row['id'])
    await app.close()
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        for sid in ids[:2]:
            row = app._session(sid)
            assert row['ownership']['status'] == 'blocked'
            assert not row.get('error')
        items = app.get_state()['attention']['items']
        assert len(items) == 1 and items[0]['sessionId'] == ids[2]
        assert items[0]['detail'] == 'Provider unavailable'
    finally:
        await app.close()


async def test_takeover_conflict_stays_inline_and_true_failure_stays_visible(tmp_path):
    class Runtime:
        close = AsyncMock()
        takeover = AsyncMock(side_effect=SessionInUseError(OWNER))
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        await app.dispatch('session.takeover', {'id': sid})
        await asyncio.gather(*app.tasks)
        row = app._session(sid)
        assert row['ownership']['detail']
        assert not row.get('error') and not app.get_state()['attention']['items']
        app.runtime.takeover.side_effect = RuntimeError('Bundle preparation failed')
        await app.dispatch('session.takeover', {'id': sid})
        await asyncio.gather(*app.tasks)
        assert row['error'] == 'Bundle preparation failed'
        assert row['ownership'] == {'status': 'blocked', 'reason': 'takeover-failed', 'detail': row['error']}
        with pytest.raises(AppError, match='read-only'):
            await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Do not bypass the failed takeover'})
        assert app.get_state()['attention']['items'][0]['detail'] == row['error']
        app.runtime.takeover.side_effect = None
        await app.dispatch('session.takeover', {'id': sid})
        await asyncio.gather(*app.tasks)
        assert row['ownership']['status'] == 'available'
        assert not row.get('error')
    finally:
        await app.close()


async def test_pending_takeover_does_not_offer_a_second_takeover(tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()
    class Runtime:
        close = AsyncMock()
        async def takeover(self, session, emit, expected_owner=None):
            await emit('runtime.ownership', {'sessionId': session['id'], 'status': 'blocked', 'owner': OWNER})
            entered.set()
            await finish.wait()
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        await app.dispatch('session.takeover', {'id': sid})
        await asyncio.wait_for(entered.wait(), 1)
        assert app._session(sid)['ownership']['status'] == 'taking-over'
        finish.set()
        await asyncio.gather(*app.tasks)
    finally:
        finish.set()
        await app.close()


async def test_background_voice_conflict_is_not_republished_as_generic_error(tmp_path):
    from amplifier_web.service import AppError
    class Runtime:
        close = AsyncMock()
        async def send(self, *args):
            raise SessionInUseError(OWNER)
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        await app.voice_delegate('Keep this', 'voice-once', session_id=sid)
        await asyncio.gather(*app.tasks)
        assert not app._session(sid).get('error')
        assert not app.get_state()['attention']['items']
        with pytest.raises(AppError) as result:
            await app.wait_for_response(sid, 'voice-once', timeout=.1)
        assert result.value.code == 'session_busy'
    finally:
        await app.close()
