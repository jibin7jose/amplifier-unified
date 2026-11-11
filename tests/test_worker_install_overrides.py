"""Worker installer policy must never become process-wide uv policy."""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from amplifier_web import runtime_worker
from amplifier_foundation.session.activation import ActivationGate


class PolicyBoundaryReached(Exception):
    """Stop after preparation and its postcheck, before unrelated worker setup."""


class PolicyBoundaryReport(dict):
    def get(self, key, default=None):
        if key == "config_inputs":
            raise PolicyBoundaryReached
        return super().get(key, default)


@pytest.fixture
def policy_worker(tmp_path, monkeypatch):
    home = tmp_path / "app"
    runtime = SimpleNamespace(session_id="policy-chat")
    state = SimpleNamespace(
        selected=None, error=None, parent_env=None, order=[], options=None,
        block_prepare=False, preparing=asyncio.Event(), home=home, runtime=runtime,
        qualified=home / "updates/releases" / ("a" * 32) / "runtime-install-overrides.txt",
        compatibility=Path(runtime_worker.__file__).with_name("runtime_deps") / "compatibility.txt",
    )

    def active_policy(*args):
        assert dict(os.environ) == state.parent_env
        state.order.append("policy")
        return state.selected

    async def prepare(*args, **kwargs):
        assert dict(os.environ) == state.parent_env
        state.order.append("prepare")
        state.options = kwargs
        if state.block_prepare:
            state.preparing.set()
            await asyncio.Future()
        if state.error is not None:
            raise state.error
        return SimpleNamespace(), runtime, PolicyBoundaryReport()

    state.policy = Mock(side_effect=active_policy)
    state.prepare = AsyncMock(side_effect=prepare)
    state.capture = Mock()
    state.capture.save.return_value = None
    monkeypatch.setattr("amplifier_web.runtime_bootstrap.bootstrap_app_package", lambda: None)
    monkeypatch.setattr("amplifier_web.host.config.app_home", lambda: home)
    monkeypatch.setattr("amplifier_web.worker_diagnostics.StartupCapture", lambda: state.capture)
    monkeypatch.setattr("amplifier_web.host.session.prepare_manager", state.prepare)
    monkeypatch.setattr("amplifier_web.runtime_qualification.active_install_overrides", state.policy)
    monkeypatch.setattr("amplifier_web.history_revision.recover_pending", lambda *args: None)
    monkeypatch.setitem(sys.modules, "amplifier_module_loop_live.runtime",
                        SimpleNamespace(Runtime=lambda **kwargs: runtime))
    state.events = []
    monkeypatch.setattr(runtime_worker, "publish", state.events.append)
    state.worker = runtime_worker.Worker()
    state.worker.shared_store = SimpleNamespace()
    state.worker.shared_handle = SimpleNamespace()
    state.worker.activation_gate = ActivationGate()
    state.worker.preparation_progress = AsyncMock()
    state.config = {"id": runtime.session_id, "workspace": str(tmp_path)}
    return state


def incoming_policy(state, monkeypatch, kind):
    values = {
        "absent": None, "empty": "",
        "custom": " /caller/first policy.txt /caller/second.txt ",
        "whitespace": " ", "compatibility": str(state.compatibility),
        "qualified": str(state.qualified),
        "app-looking": str(state.home / "updates/releases" / ("b" * 32) / "runtime-install-overrides.txt"),
    }
    value = values[kind]
    if value is None:
        monkeypatch.delenv("UV_OVERRIDE", raising=False)
    else:
        monkeypatch.setenv("UV_OVERRIDE", value)
    monkeypatch.setenv("UV_CONSTRAINT", " /caller/constraints one.txt /caller/constraints two.txt ")
    monkeypatch.setenv("UV_NO_BUILD", "true")
    state.parent_env = dict(os.environ)
    return value


@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("incoming", ["absent", "empty", "custom", "whitespace", "compatibility", "qualified", "app-looking"])
async def test_worker_policy_matrix_preserves_caller_environment(policy_worker, monkeypatch, qualified, incoming):
    state = policy_worker
    value = incoming_policy(state, monkeypatch, incoming)
    state.selected = state.qualified if qualified else None
    with pytest.raises(PolicyBoundaryReached):
        await state.worker.start(state.config, raise_errors=True, recover_bundle=False)
    if value:
        assert state.options["install_overrides"] is None
        state.policy.assert_not_called()
        assert state.order == ["prepare"]
    else:
        assert state.options["install_overrides"] == (state.qualified if qualified else state.compatibility)
        assert isinstance(state.options["install_overrides"], Path)
        calls = [call(state.home)]
        if qualified:
            calls.append(call(state.home, str(state.qualified)))
        assert state.policy.call_args_list == calls
        assert state.order == (["policy", "prepare", "policy"] if qualified else ["policy", "prepare"])
    assert dict(os.environ) == state.parent_env
    state.prepare.assert_awaited_once()
    state.capture.close.assert_called_once()
    assert state.worker.execution is None


@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("incoming", ["absent", "empty", "custom", "qualified"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_prepare_failure_or_cancellation_keeps_environment_and_skips_postcheck(policy_worker, monkeypatch, qualified, incoming, cancel):
    state = policy_worker
    value = incoming_policy(state, monkeypatch, incoming)
    state.selected = state.qualified if qualified else None
    state.block_prepare = cancel
    state.error = None if cancel else RuntimeError("prepare failed")
    task = asyncio.create_task(state.worker.start(state.config, raise_errors=True, recover_bundle=False))
    try:
        if cancel:
            await asyncio.wait_for(state.preparing.wait(), 1)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert state.options["install_overrides"] == (None if value else state.qualified if qualified else state.compatibility)
    assert state.policy.call_args_list == ([] if value else [call(state.home)])
    assert state.order == (["prepare"] if value else ["policy", "prepare"])
    assert dict(os.environ) == state.parent_env
    state.capture.close.assert_called_once()
    if cancel:
        state.capture.save.assert_not_called()
    assert state.worker.execution is None


@pytest.mark.parametrize("incoming", ["absent", "empty"])
async def test_qualified_precheck_failure_never_prepares_or_changes_environment(policy_worker, monkeypatch, incoming):
    state = policy_worker
    incoming_policy(state, monkeypatch, incoming)
    state.policy.side_effect = ValueError("qualified policy changed")
    with pytest.raises(ValueError, match="qualified policy changed"):
        await state.worker.start(state.config, raise_errors=True, recover_bundle=False)
    state.policy.assert_called_once_with(state.home)
    state.prepare.assert_not_awaited()
    assert dict(os.environ) == state.parent_env
    state.capture.close.assert_called_once()


@pytest.mark.parametrize('cancel', [False, True])
async def test_failed_replacement_start_restores_exact_saved_files(policy_worker, monkeypatch, cancel):
    from amplifier_web.bundle_selection import BundleTransaction
    from amplifier_web.host.config import write_private
    state = policy_worker
    incoming_policy(state, monkeypatch, 'absent')
    config = {**state.config, 'bundle': 'replacement', 'bundleReplacement': {'bundle': 'replacement'}}
    journal = BundleTransaction(state.home, state.config['workspace'], state.config['id'])
    for name, path in journal.paths.items():
        write_private(path, 'original bytes '+name)
    before = {name: path.read_bytes() for name, path in journal.paths.items()}
    marker = object()
    admission = AsyncMock(return_value={})
    monkeypatch.setattr(state.worker, 'bridge', admission)
    async def begin(home, workspace, received, *, admit):
        assert state.worker.shared_handle is not None
        await admit()
        journal.begin()
        for path in journal.paths.values():
            write_private(path, '{}')
        return journal, marker
    monkeypatch.setattr('amplifier_web.bundle_selection.begin_replacement', begin)
    state.block_prepare = cancel
    state.error = None if cancel else RuntimeError('Replacement cannot mount')
    task = asyncio.create_task(state.worker.start(config, raise_errors=True, recover_bundle=False))
    if cancel:
        await asyncio.wait_for(state.preparing.wait(), 1)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await task
    admission.assert_awaited_once_with('configuration.nextMessage.admit', {})
    assert state.options['resolved_root'] is marker
    assert {name: path.read_bytes() for name, path in journal.paths.items()} == before
    assert not journal.path.exists()
    assert not any(event.get('type') == 'runtime.ready' for event in state.events)
