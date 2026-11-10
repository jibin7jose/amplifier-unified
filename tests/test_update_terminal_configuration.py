"""Synthetic real native settings writes; no live installation/provider/target turn."""
import asyncio
import copy
from contextlib import ExitStack
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, update_blockers
from amplifier_web.configuration_recovery import native_apply, prepare_attempt, receipt_path, seal
from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from test_app_updates import prepared_activation
from test_queued_configuration_boundary import ControlsTransport, plan


@pytest.fixture
async def terminal(tmp_path, monkeypatch, request):
    app, manager, candidate = await prepared_activation(tmp_path / 'app', monkeypatch)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    app.runtime = RuntimeManager()
    await app.dispatch('session.create', {})
    session = app._session()
    app.management = Management(app)
    transport = ControlsTransport(app.data_dir, session['id'])
    record = app.management.create_configuration_operation(session, plan(2), 'original-settings', None, None)
    session.update(configurationBusy=True, configurationOperationFence=record['id'])
    app.management.configuration_transition(session, record['id'], phase='applying', stage='preparing')
    attempt = prepare_attempt(app.management, session, record['id'])
    effect = getattr(request, 'param', 'saved_not_mounted')
    if effect == 'none':
        def refuse(*args): raise ValueError('Synthetic preparation refusal before settings write')
        monkeypatch.setattr(transport.controls, 'prepare_configuration', refuse)
        with pytest.raises(ValueError, match='preparation refusal'):
            await native_apply(transport.controls, plan(2), attempt,
                lambda args: _admit(app, session, args), lambda: session.get('interruptionRevision', 0))
    else:
        await native_apply(transport.controls, plan(2), attempt,
            lambda args: _admit(app, session, args), lambda: session.get('interruptionRevision', 0))
    app.management.configuration_transition(session, record['id'], phase='error', stage='readback',
        effect=effect, recoveryRequired=True, error='Synthetic saved-versus-loaded mismatch')
    session['status'] = 'error'
    process = AsyncMock(return_value='99.0.0')
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda _: True)
    restart = AsyncMock()
    monkeypatch.setattr(app_updates, 'request_managed_restart', restart)
    yield SimpleNamespace(app=app, manager=manager, session=session, attempt=attempt,
                          process=process, restart=restart, transport=transport)
    await transport.close()
    await app.close()


async def _admit(app, session, args):
    from amplifier_web.configuration_recovery import authorize
    return authorize(app.management, session['id'], args)


def originals(fixture):
    app, session = fixture.app, fixture.session
    root = app.data_dir / 'sessions' / session['id']
    return ({str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()},
            copy.deepcopy(session['pendingConfiguration']), session.get('configurationOperationFence'))


async def test_terminal_saved_hold_allows_normal_app_only_activation_without_recovery(terminal):
    f = terminal
    before = originals(f)
    assert f.manager.busy()  # Component activation did not acquire this exception.
    assert not f.manager.app_busy()
    assert update_blockers.assess(f.app, application=True)['observation']['configurationReasonCounts'] == {
        'terminal-operation-quiescent': 1}
    await app_updates.activate(f.manager)
    assert f.app.state['updates']['pendingRestart']['revision'] == 'a' * 40
    f.restart.assert_awaited_once()
    assert originals(f) == before
    assert len(f.app.management.configuration_record(f.session['id'], f.attempt['operationId'])['attempts']) == 1
    assert not f.app.runtime.workers and not f.transport.inputs
    assert not f.transport.calls


@pytest.mark.parametrize('terminal', ['none'], indirect=True)
async def test_terminal_native_no_effect_is_quiescent_without_consuming_original(terminal):
    f = terminal
    before = originals(f)
    assert not f.manager.app_busy() and f.manager.busy()
    await app_updates.activate(f.manager)
    f.restart.assert_awaited_once()
    assert originals(f) == before
    assert not f.app.runtime.workers and not f.transport.calls


async def test_app_gate_presentation_and_idle_notification_use_same_assessment(terminal, monkeypatch):
    from amplifier_web import update_report
    f = terminal
    monkeypatch.setattr(update_report, 'runtime_report', lambda _: {})
    await f.manager.publish()
    assert not f.app.state['updates']['blockers']
    report = await update_report.collect(f.manager)
    assert not report['activationGate']['blocked']
    assert report['activationGate']['configurationReasonCounts'] == {'terminal-operation-quiescent': 1}
    f.app.state['updates']['phase'] = 'app-staged'
    f.manager.wakeup.clear()
    f.manager.notify_idle()
    assert f.manager.wakeup.is_set() and f.manager.busy() and not f.manager.app_busy()


@pytest.mark.parametrize('outcome', ['pass', 'failure', 'cancel'])
async def test_writer_exclusion_covers_replacement_await_and_releases_on_every_exit(terminal, monkeypatch, outcome):
    f = terminal
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    store = SharedSessionStore(f.session['workspace'], f.session['id'])
    async def replacement(*args, **kwargs):
        with pytest.raises(SessionBusyError):
            store.acquire(app='competing-during-replacement')
        await asyncio.sleep(0)
        if outcome == 'failure': raise RuntimeError('Synthetic replacement failure')
        if outcome == 'cancel': raise asyncio.CancelledError()
        return '99.0.0'
    monkeypatch.setattr(app_updates, 'process', replacement)
    if outcome == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await app_updates.activate(f.manager)
    else:
        await app_updates.activate(f.manager)
    acquired = store.acquire(app='after-boundary')
    acquired.release()
    assert f.session['configurationBusy']


async def test_reopened_app_readiness_keeps_configuration_hold_and_no_input(terminal, monkeypatch):
    from amplifier_web import update_readiness
    from amplifier_web.auth import data_identity
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from amplifier_web.update_drain import finish
    f = terminal
    intent = {'id': 'exact-app-only', 'scope': 'app'}
    await f.manager.command('app', command_id=intent['id'])
    assert f.app.state['updates']['pendingRestart']['updateIntent'] == intent
    before = originals(f)
    home, workspace, sid = f.app.data_dir, f.app.default_workspace, f.session['id']
    f.app._save_changes(sessions={sid}, globals={'updates'})
    await f.app.close()
    monkeypatch.setattr(update_readiness, 'running_identity', lambda: {
        'version': '99.0.0', 'revision': 'a'*40, 'instanceId': 'new-host'})
    runtime = RuntimeManager()
    runtime.start = AsyncMock(side_effect=AssertionError('Held target must never mount'))
    runtime.send = AsyncMock(side_effect=AssertionError('Held target must never receive input'))
    app = AppService(home, runtime, workspace=workspace)
    app.management = Management(app)
    app.update_manager = manager = UpdateManager(app)
    try:
        await asyncio.gather(*list(app.tasks))
        assert await manager.confirm_readiness({'ok': True, 'app': 'amplifier-unified',
            'dataIdentity': data_identity(home), **manager.running_identity})
        await finish(manager, 'installed')
        await asyncio.sleep(0)
        current = SimpleNamespace(app=app, session=app._session(sid))
        assert originals(current) == before
        assert app._session(sid)['configurationBusy']
        assert manager.service.state['updates']['sequence']['stage'] == 'complete'
        assert manager.service.state['updates']['sequence']['install'] is False
        runtime.start.assert_not_awaited()
        runtime.send.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize('fault', ['missing', 'forged', 'unknown', 'partial', 'wrong-session', 'stale-attempt',
    'wrong-revision', 'payload', 'saved-plan', 'stop', 'native', 'working', 'refresh', 'child', 'worker',
    'observer-unknown', 'observer-error', 'management', 'pending-task', 'preparation'])
async def test_unproven_or_active_operation_stays_blocked(terminal, monkeypatch, fault):
    f = terminal
    app, session = f.app, f.session
    receipt = receipt_path(f.attempt, home=app.data_dir)
    release = asyncio.Event()
    task = None
    if fault == 'missing': receipt.unlink()
    elif fault == 'forged':
        data = json.loads(receipt.read_text()); data['signature'] = '0' * 64; receipt.write_text(json.dumps(data))
    elif fault in {'unknown', 'partial', 'wrong-session'}:
        attempt = {**f.attempt, **({'sessionId': 'foreign'} if fault == 'wrong-session' else {})}
        receipt.write_text(json.dumps(seal(attempt, 'unknown' if fault == 'unknown' else 'saved_not_mounted',
                                           terminal=fault != 'partial')))
    elif fault == 'stale-attempt': session['pendingConfiguration']['attemptId'] = 'stale'
    elif fault == 'wrong-revision': session['pendingConfiguration']['revision'] += 1
    elif fault == 'payload': app.management.queued_path(session['id']).write_text('{}')
    elif fault == 'saved-plan': (receipt.parent.parent / 'configuration.json').write_text('{}')
    elif fault == 'stop': session['interruptionRevision'] = session.get('interruptionRevision', 0) + 1
    elif fault == 'native': session['runtimeSessionId'] = 'other-native'
    elif fault == 'working': session['status'] = 'working'
    elif fault == 'refresh': session['configurationPending'] = True
    elif fault == 'child': session['workers'] = [{'status': 'working'}]
    elif fault == 'worker': app.runtime.workers[session['id']] = {'process': SimpleNamespace(returncode=0)}
    elif fault == 'observer-unknown': monkeypatch.setattr(app.runtime, 'has_pending_operations', lambda: None)
    elif fault == 'observer-error':
        def unavailable(): raise RuntimeError('private error')
        monkeypatch.setattr(app.runtime, 'has_pending_operations', unavailable)
    elif fault == 'management': await app.management.lock.acquire()
    elif fault == 'pending-task':
        task = asyncio.create_task(release.wait()); app.management.pending_config_tasks[session['id']] = task
    elif fault == 'preparation':
        lock = asyncio.Lock(); await lock.acquire(); app.runtime._locks[session['id']] = lock
    before = originals(f)
    try:
        # Throwing the global observer must not make the activation permissive.
        try: await app_updates.activate(f.manager)
        except RuntimeError: assert fault == 'observer-error'
        f.process.assert_not_awaited()
        f.restart.assert_not_awaited()
        assert originals(f) == before
        assert f.app.state['updates'].get('pendingRestart') is None
    finally:
        release.set()
        if task: await task
        if fault == 'management': app.management.lock.release()
        if fault == 'preparation': app.runtime._locks[session['id']].release()
        if fault == 'worker': app.runtime.workers.clear()


@pytest.mark.parametrize('change', ['revision', 'seal', 'stop', 'native', 'target', 'intent', 'writer'])
async def test_activation_rechecks_same_hold_after_target_discovery(terminal, monkeypatch, change):
    f = terminal
    original = app_updates.installed_target
    held = None
    async def discover():
        nonlocal held
        value = await original()
        if change == 'revision':
            f.app.management.configuration_transition(f.session, f.attempt['operationId'], error='Changed error')
        elif change == 'seal':
            receipt_path(f.attempt, home=f.app.data_dir).write_text(json.dumps(seal(f.attempt, 'unknown')))
        elif change == 'stop': f.session['interruptionRevision'] = f.session.get('interruptionRevision', 0) + 1
        elif change == 'native': f.session['runtimeSessionId'] = 'foreign'
        elif change == 'target': f.app.state['updates']['pendingApp']['attemptId'] = 'e' * 32
        elif change == 'intent': f.app.state['updates']['applicationIntent'] = {'id': 'new', 'scope': 'whole'}
        elif change == 'writer':
            from amplifier_foundation.session import SharedSessionStore
            held = SharedSessionStore(f.session['workspace'], f.session['id']).acquire(app='foreign-writer')
        return value
    monkeypatch.setattr(app_updates, 'installed_target', discover)
    try:
        await app_updates.activate(f.manager)
        f.process.assert_not_awaited()
        f.restart.assert_not_awaited()
        assert not f.app.state['updates'].get('pendingRestart')
    finally:
        if held: held.release()
