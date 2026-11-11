"""A retained worker may predate the app; never repair it during execution."""
import json
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.runtime_qualification import RuntimeDependencyError, required_activation_gate


@pytest.mark.parametrize('installed', [None, SimpleNamespace()])
def test_missing_activation_api_requires_explicit_component_update(monkeypatch, installed):
    monkeypatch.setitem(sys.modules, 'amplifier_foundation.session.activation', installed)
    with pytest.raises(RuntimeDependencyError, match='Settings > Updates'):
        required_activation_gate()


def test_required_api_is_the_loaded_canonical_owner():
    from amplifier_foundation.session.activation import ActivationGate

    assert required_activation_gate() is ActivationGate


def test_unrelated_import_failure_is_not_reported_as_missing_api(monkeypatch):
    import importlib

    def unavailable(name):
        raise ModuleNotFoundError('private dependency detail', name='unrelated_dependency')

    monkeypatch.setattr(importlib, 'import_module', unavailable)
    with pytest.raises(ModuleNotFoundError) as caught:
        required_activation_gate()
    assert caught.value.name == 'unrelated_dependency'


@pytest.mark.parametrize('available', [False, True])
def test_final_update_probe_requires_api_before_mount(tmp_path, monkeypatch, capsys, available):
    import importlib.util
    from amplifier_web.update_diagnostics import PROBE_PREFIX

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['update_probe', str(tmp_path), 'fixture'])
    session = SimpleNamespace(cleanup=AsyncMock())
    prepare = AsyncMock(return_value=(session, None, {'standalone': True, 'providers': ['fixture']}))
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', prepare)
    # This tests the compatibility probe, not whether the test runner has CLI extras.
    find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None
                        if name in {'amplifier_app_cli', 'amplifier_loop_live_cli', 'amplifier_workspace'}
                        else find_spec(name))
    if not available:
        monkeypatch.setitem(sys.modules, 'amplifier_foundation.session.activation', None)
        with pytest.raises(SystemExit) as caught:
            runpy.run_module('amplifier_web.update_probe', run_name='__main__')
        assert caught.value.code == 1
        prepare.assert_not_awaited()
        session.cleanup.assert_not_awaited()
    else:
        runpy.run_module('amplifier_web.update_probe', run_name='__main__')
        prepare.assert_awaited_once()
        session.cleanup.assert_awaited_once()
    line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith(PROBE_PREFIX))
    facts = json.loads(line.removeprefix(PROBE_PREFIX))
    assert facts['ok'] is available
    if not available:
        assert facts['errorType'] == 'RuntimeDependencyError'


async def test_old_worker_refuses_before_native_ownership_or_history(tmp_path, monkeypatch):
    from amplifier_foundation.session import SharedSessionStore
    from amplifier_web.runtime_worker import Worker

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'ownership'))
    # Keep a recorded old generation: the check must not rewrite or replace it.
    receipt = tmp_path / 'app/updates/releases/old'
    receipt.mkdir(parents=True)
    (receipt / 'runtime.toml').write_bytes(b'old selected manifest')
    (receipt / 'runtime.lock').write_bytes(b'old selected lock')
    before = {p.name: p.read_bytes() for p in receipt.iterdir()}

    def forbidden(*args, **kwargs):
        pytest.fail('An incompatible worker reached ownership, history or composition')

    monkeypatch.setattr(SharedSessionStore, 'acquire', forbidden)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', forbidden)
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', forbidden)
    monkeypatch.setitem(sys.modules, 'amplifier_foundation.session.activation', None)
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker = Worker()
    await worker.start({'id': 'old-worker', 'workspace': str(tmp_path)})
    assert worker.shutdown.is_set()
    assert worker.shared_handle is None and worker.activation_gate is None
    assert not (tmp_path / 'ownership').exists()
    assert not (tmp_path / 'native').exists()
    assert {p.name: p.read_bytes() for p in receipt.iterdir()} == before
    assert not any(row['type'] == 'runtime.ready' for row in events)
    error = next(row for row in events if row['type'] == 'runtime.error')
    assert 'RuntimeDependencyError:' in error['error']
    assert 'included' in error['error'] and 'Settings > Updates' in error['error']


async def test_runtime_delivers_bounded_component_update_guidance(tmp_path, monkeypatch):
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    failure = {'type': 'runtime.error',
               'error': 'RuntimeDependencyError: private-path-and-token'}
    script = ('import sys,json;sys.stdin.readline();'
              f'print({json.dumps(json.dumps(failure))},flush=True)')
    manager = RuntimeManager(command=[sys.executable, '-c', script])
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    try:
        with pytest.raises(RuntimeStartupError, match='Settings > Updates') as caught:
            await manager.start({'id': 'missing-api'}, emit)
        assert 'private-path-and-token' not in str(caught.value) + json.dumps(events)
        assert not manager.workers
        assert not any(kind == 'assistant.message' for kind, _ in events)
    finally:
        await manager.close()
