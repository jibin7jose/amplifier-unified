"""A ready worker can fail its next mount before accepting any new work."""
import asyncio
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.module_failures import ConfiguredModuleError
from amplifier_web.runtime import RuntimeManager, RuntimeOperationPending
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService
from amplifier_foundation.session.activation import ActivationGate


FAILURES = [{'module': 'tool-fixture', 'type': 'tool', 'reason_code': 'validation_failed'}]


@pytest.mark.parametrize('op', ['send', 'retry'])
async def test_real_command_rejects_failed_remount_before_submitting_input(tmp_path, monkeypatch, op):
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setattr('amplifier_web.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_web.host.config.app_home', lambda: home)
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides', lambda *args: None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', lambda *args: None)
    monkeypatch.setattr('amplifier_web.bundle_selection.BundleTransaction.restore', lambda *args: None)
    monkeypatch.setattr('amplifier_web.shared_state.configuration_stamp', lambda *args, **kwargs: ('new',))
    submitted = AsyncMock()
    runtime = SimpleNamespace(session_id='fixture', submit=submitted)
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime', SimpleNamespace(Runtime=lambda **kwargs: runtime))

    async def fail_prepare(*args, **kwargs):
        logging.getLogger('amplifier_core.loader').error('private synthetic mount detail')
        raise ConfiguredModuleError(FAILURES)
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', fail_prepare)
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker = Worker()
    worker.home, worker.workspace, worker.runtime = home, tmp_path, runtime
    worker.shared_store = SimpleNamespace(acquire=lambda **kwargs: SimpleNamespace(release=lambda: None))
    worker.shared_store_stamp = lambda path: None
    worker.activation_gate = ActivationGate()
    worker.parked = True
    worker.parked_config_stamp = ('old',)
    worker.history_stamp = lambda: ()
    worker.start_config = {'id': 'fixture', 'workspace': str(tmp_path)}
    old_session = SimpleNamespace(cleanup=AsyncMock())
    worker.session = old_session
    worker.controls = SimpleNamespace(close=AsyncMock())
    old_execution = asyncio.create_task(asyncio.Event().wait())
    worker.execution = old_execution

    await worker.command({'op': op, 'id': 'command', 'input_id': 'input', 'text': 'Keep this input'})

    reply = next(event for event in events if event.get('op') == 'reply')
    assert reply['code'] == 'worker_preparation_failed'
    assert reply['moduleFailures'][0]['module'] == 'tool-fixture'
    assert 'private synthetic mount detail' not in json.dumps(events)
    assert 'private synthetic mount detail' in (home / 'logs/workers' / reply['diagnosticReceipt']).read_text()
    assert worker.shutdown.is_set() and old_execution.cancelled()
    old_session.cleanup.assert_awaited_once()
    submitted.assert_not_awaited()
    assert not worker.remounting
    assert not any(event.get('type') in {'runtime.ready', 'generation.started', 'input.delivered'} for event in events)


@pytest.mark.parametrize('late', [False, True])
async def test_ready_worker_rejection_preserves_unsent_input_and_current_diagnostics(tmp_path, monkeypatch, late):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    from amplifier_web.worker_diagnostics import save_startup_failure
    diagnostic = save_startup_failure({'stderr': ['private remount detail']}, None)
    # Exercise real process framing, host admission, application receipt, public
    # error and diagnostics after the worker has already announced readiness.
    fixture = '''import json,sys
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start':
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
    elif data['op'] in {'send', 'release'}:
        if data['op']=='send' and LATE:
            pending = data
            continue
        if data['op']=='release':
            data = pending
        print(json.dumps({'op':'reply','id':data['id'],'code':'worker_preparation_failed',
            'error':'private exception must not leak','moduleFailures':FAILURES,
            'diagnosticReceipt':RECEIPT}),flush=True)
        break
'''.replace('FAILURES', repr(FAILURES)).replace('RECEIPT', repr(diagnostic.name)).replace('LATE', repr(late))
    manager = RuntimeManager(command=[sys.executable, '-c', fixture], retention={'prewarm_on_select': False})
    if late:
        original_wait = asyncio.wait_for
        async def short_ack(awaitable, timeout):
            return await original_wait(awaitable, .02 if timeout == 30 else timeout)
        monkeypatch.setattr(asyncio, 'wait_for', short_ack)
    app = AppService(tmp_path, manager, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        await manager.start(session, app.on_runtime_event)
        await app.on_runtime_event('runtime.generation', {'sessionId': session['id'],
            'event': 'generation.failed', 'generation_id': 'old-generation',
            'input_ids': ['old-input'], 'error_type': 'TimeoutError', 'error_category': 'unknown'})
        with pytest.raises(RuntimeOperationPending if late else AppError) as rejected:
            await app.dispatch('conversation.send', {'text': 'Keep this saved request'}, command_id='new-input')
        if late:
            assert session['messages'][-1]['delivery']['status'] == 'unknown'
            await manager._write(manager.workers[session['id']], {'op': 'release'})
        else:
            assert rejected.value.status == 503 and rejected.value.code == 'worker_startup_failed'
            assert rejected.value.receipt['delivery'] == 'failed'
        await manager.workers[session['id']]['reader']
        assert session['failure']['category'] == 'worker_startup'
        assert 'generationId' not in session['failure']
        assert session['diagnosticReceipt'] == diagnostic.name
        assert session['messages'][-1]['delivery']['status'] == 'failed'
        app._delivery(session, 'new-input', 'unknown')
        assert session['messages'][-1]['delivery']['status'] == 'failed'
        inspected = await app.dispatch('session.inspect', {'id': session['id']})
        assert inspected['result']['failure']['category'] == 'worker_startup'
        assert inspected['result']['diagnosticReceipt'] == diagnostic.name
        assert 'private exception' not in json.dumps(app.browser_state())
        duplicate = await app.dispatch('conversation.send', {'text': 'Keep this saved request'}, command_id='new-input')
        assert duplicate['duplicate'] and duplicate['delivery'] == 'failed'
        assert len(session['messages']) == 1
    finally:
        await app.close()


async def test_background_startup_exit_does_not_reuse_previous_manager_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    manager = RuntimeManager(command=[sys.executable, '-c',
        "import sys; sys.stdin.readline(); print('private mount error', file=sys.stderr); sys.exit(1)"],
        retention={'prewarm_on_select': False})
    app = AppService(tmp_path, manager, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        await app.on_runtime_event('runtime.generation', {'sessionId': session['id'],
            'event': 'generation.failed', 'generation_id': 'old-generation',
            'input_ids': ['old-input'], 'error_type': 'ContextLengthError', 'error_category': 'context_limit'})
        with pytest.raises(RuntimeError):
            await manager.start(session, app.on_runtime_event)
        assert session['failure']['category'] == 'worker_startup'
        assert 'generationId' not in session['failure']
        assert session['diagnosticReceipt']
        assert 'private mount error' not in json.dumps(app.browser_state())
        assert not session['messages']
    finally:
        await app.close()
