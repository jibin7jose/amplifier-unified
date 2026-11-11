"""Optional file provenance cannot become a worker readiness dependency."""
import asyncio
import json
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

from amplifier_web import loaded_provenance, runtime_worker
from amplifier_foundation.session.activation import ActivationGate


def test_mount_snapshot_only_reads_bounded_in_memory_identities(tmp_path, monkeypatch):
    module = ModuleType('mount_identity_fixture')
    module.__file__ = str(tmp_path / 'unread.py')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    tool = type('MountedTool', (), {'__module__': module.__name__})()
    seen = []

    class ManyEmptyMounts(dict):
        def items(self):
            for index in range(10_000):
                seen.append(index)
                yield str(index), None

    coordinator = SimpleNamespace(get=lambda name: {'fixture': tool} if name == 'tools'
                                 else ManyEmptyMounts() if name == 'providers' else None)
    session = SimpleNamespace(session_id='mounted-chat', coordinator=coordinator)
    monkeypatch.setattr(loaded_provenance, 'hashes', Mock(side_effect=AssertionError('No mount hashing')))
    monkeypatch.setattr(loaded_provenance.os, 'open', Mock(side_effect=AssertionError('No mount file reads')))
    value = loaded_provenance.capture_mount(session)
    row = next(row for row in value['modules'] if row['module'] == module.__name__)
    assert row == {'module': module.__name__, 'loaded': True, 'origin': module.__file__,
                   'fileStatus': 'unknown', 'importedBytesVerified': False}
    assert len(seen) <= loaded_provenance.MAX_MODULES + 1
    assert value['mounted'] and value['identityStable'] and value['runtimeSessionId'] == 'mounted-chat'
    assert len(value['modules']) <= loaded_provenance.MAX_MODULES
    assert len(value['mounts']) <= loaded_provenance.MAX_MODULES
    assert 'not inspected' in ' '.join(value['limitations'])
    json.dumps(value)  # private module/mount objects must not escape


async def test_worker_ready_does_not_wait_for_slow_provenance_hashing(tmp_path, monkeypatch):
    events, entered, release = [], threading.Event(), threading.Event()
    runtime = SimpleNamespace(session_id='ready-before-hashing')
    coordinator = SimpleNamespace(get=lambda name: {} if name in {'providers', 'tools'} else None,
                                  register_capability=Mock(), get_capability=lambda name: None)

    async def execute(_):
        await asyncio.Future()

    session = SimpleNamespace(session_id=runtime.session_id, coordinator=coordinator, execute=execute)
    controls = SimpleNamespace(capacity=SimpleNamespace(guard=AsyncMock()), restore=AsyncMock(), persist=Mock())
    monkeypatch.setattr('amplifier_web.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_web.host.config.app_home', lambda: tmp_path / 'home')
    monkeypatch.setattr('amplifier_web.worker_diagnostics.StartupCapture', Mock)
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', AsyncMock(return_value=(session, runtime, {})))
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides', lambda *args: None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', lambda *args: None)
    def make_controls(session_arg, runtime_arg, telemetry, *, preserve_configuration, tool_invocation):
        assert session_arg is session
        assert runtime_arg is runtime
        assert preserve_configuration is False
        assert tool_invocation == worker.tool_invocation
        return controls
    monkeypatch.setattr('amplifier_web.runtime_controls.RuntimeControls', make_controls)
    monkeypatch.setattr('amplifier_web.app_guidance.install_app_access', AsyncMock())
    monkeypatch.setattr('amplifier_web.host.naming.LiveSessionNaming', lambda *args: None)
    monkeypatch.setattr('amplifier_web.shared_state.configuration_stamp', lambda *args, **kwargs: ())
    monkeypatch.setattr('amplifier_web.support_diagnostics.runtime_versions', lambda: {})
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime', SimpleNamespace(Runtime=lambda **kwargs: runtime))
    monkeypatch.setattr(runtime_worker, 'publish', events.append)

    def slow_hashes(rows):
        entered.set()
        assert release.wait(5), 'test must release the synthetic blocked file read'
        return [{**row, 'fileStatus': 'unknown', 'importedBytesVerified': False} for row in rows]

    monkeypatch.setattr(loaded_provenance, 'hashes', slow_hashes)
    # Exercise the production timeout path without spending six seconds in a test.
    monkeypatch.setattr(loaded_provenance, 'MAX_SECONDS', .05)
    worker = runtime_worker.Worker()
    worker.shared_store = worker.shared_handle = SimpleNamespace()
    worker.shared_store_stamp = lambda *args: ()
    worker.activation_gate = ActivationGate()
    worker.preparation_progress = AsyncMock()
    worker.install_collaboration_checkpoint = Mock()
    worker.history_stamp = lambda: ()
    worker.ownership.register = AsyncMock()
    try:
        await asyncio.wait_for(worker.start({'id': runtime.session_id, 'workspace': str(tmp_path)},
                                           raise_errors=True, recover_bundle=False), 1)
        assert any(event.get('type') == 'runtime.ready' for event in events)
        worker.ownership.register.assert_awaited_once()
        assert not entered.is_set(), 'readiness must not even schedule provenance file hashing'
        assert all(row['fileStatus'] == 'unknown' for row in worker.provenance_mount['modules'])
        assert not any('currentFileSha256' in row for row in worker.provenance_mount['modules'])
        await asyncio.wait_for(worker.command({'op': 'dependencies', 'id': 'explicit-provenance',
                                              'provenance': True, 'modules': ['json']}), 1)
        assert entered.is_set(), 'an explicit inspection must attempt current file evidence'
        reply = next(row for row in events if row.get('id') == 'explicit-provenance')
        assert 'error' not in reply
        assert all(row['fileStatus'] == 'unknown' for row in reply['result']['modules'])
        assert not any('fileObservedAtMount' in row or 'changedSinceMount' in row
                       for row in reply['result']['modules'])
    finally:
        release.set()
        if worker.execution:
            worker.execution.cancel()
            await asyncio.gather(worker.execution, return_exceptions=True)
