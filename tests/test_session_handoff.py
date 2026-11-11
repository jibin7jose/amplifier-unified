import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_foundation.session import (
    SessionBusyError,
    SharedSessionStore,
    request_release,
)
from amplifier_web.runtime_worker import Worker
from amplifier_foundation.session.activation import ActivationGate
from amplifier_web.service import AppService, AppError
from amplifier_web.runtime import RuntimeManager, SessionInUseError


async def make_worker(tmp_path, monkeypatch, checkpoint):
    # Runtime is installed explicitly for these cross-host lifecycle tests.
    runtime_module = pytest.importorskip("amplifier_module_loop_live.runtime")
    worker = Worker()
    events = []
    monkeypatch.setattr("amplifier_web.runtime_worker.publish", events.append)
    worker.workspace = tmp_path
    worker.shared_store = SharedSessionStore(tmp_path, "interop")
    worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified")
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.runtime = runtime_module.Runtime(session_id="interop")
    worker.runtime.capture_activation = worker.activation_gate.current

    async def execute():
        kind, command = await worker.runtime.inbox.get()
        assert kind == "input" and command.kind == "stop"
        worker.runtime.closed = True

    worker.execution = asyncio.create_task(execute())
    worker.execution.add_done_callback(worker.executed)
    worker.session = SimpleNamespace(
        cleanup=AsyncMock(),
        coordinator=SimpleNamespace(
            get_capability=lambda key: checkpoint if key == "live.checkpoint" else None
        ),
    )
    worker.controls = SimpleNamespace(close=AsyncMock())
    await worker.ownership.register()
    return worker, events


async def test_unified_failed_save_retains_lock_and_blocks_more_input(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "locks"))
    worker, events = await make_worker(
        tmp_path, monkeypatch, AsyncMock(side_effect=OSError("full disk"))
    )
    try:
        result = await request_release(
            worker.shared_store,
            expected_owner=worker.shared_handle.owner,
            request_id="takeover",
            requester_app="Amplifier CLI",
        )
        assert result.status == "cannot_release"
        assert worker.shared_handle.active
        await worker.command({"op": "send", "id": "late", "text": "must not run"})
        assert events[-1]["code"] == "session_busy"
        assert not worker.shutdown.is_set()
        with pytest.raises(SessionBusyError):
            worker.shared_store.acquire(app="other")
    finally:
        await worker.ownership.registration.close()
        worker.shared_handle.release()


async def test_cli_unified_cli_preserves_native_history_and_fences_old_worker(
    tmp_path, monkeypatch
):
    cli = pytest.importorskip("amplifier_app_cli.session_handoff")
    from amplifier_app_cli.shared_root_state import SharedRootSession
    from amplifier_app_cli.session_store import SessionStore
    from amplifier_app_cli.session_runner import SessionConfig
    from rich.console import Console

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "agent-style-locks")
    )
    native = SessionStore(base_dir=tmp_path / "agent-style-history")
    messages = [{"role": "user", "content": "from CLI"}]
    root = SharedRootSession.acquire("interop")
    initialized = SimpleNamespace(
        root_state=root,
        cleanup=AsyncMock(),
        session=SimpleNamespace(
            coordinator=SimpleNamespace(
                cancellation=SimpleNamespace(request_graceful=Mock())
            )
        ),
    )
    controller = await cli.CLIHandoff(initialized, Console(file=StringIO())).start()

    async def save_cli():
        root.checkpoint(
            native, messages, bundle="anchors", metadata={"bundle": "anchors"}
        )

    request = asyncio.create_task(
        request_release(
            SharedSessionStore(tmp_path, "interop"),
            expected_owner=root.held.owner,
            request_id="to-web",
            requester_app="Amplifier Unified",
        )
    )
    await asyncio.wait_for(controller.requested.wait(), 2)
    await controller.finish(save_cli)
    assert (await request).status == "released"

    messages.append({"role": "assistant", "content": "from Unified"})

    async def checkpoint(status):
        worker.shared_handle.check()
        worker.activation_gate.check_current()
        native.save("interop", messages, {"bundle": "anchors"})

    worker, events = await make_worker(tmp_path, monkeypatch, checkpoint)
    # Cleanup can dispose of context. It must never be reread into an empty save.
    worker.session.cleanup.side_effect = lambda: messages.clear()
    previous = worker.activation
    config = SessionConfig(
        {}, [], False, session_id="interop", invocation_mode="single", takeover=True
    )
    successor = await asyncio.wait_for(cli.acquire_root(config, controller.console), 3)
    try:
        history, _ = successor.read(native)
        assert [row["content"] for row in history] == ["from CLI", "from Unified"]
        await asyncio.sleep(0)
        assert any(e.get("status") == "yielded" for e in events)
        assert not worker.shutdown.is_set()
        with pytest.raises(RuntimeError, match="released or superseded"):
            worker.activation_gate.check(previous)
        await worker.command(
            {"op": "send", "id": "late", "text": "no automatic take-back"}
        )
        assert events[-1]["code"] == "session_busy"
        assert successor.held.active
    finally:
        successor.release()
        await worker.ownership.registration.close()


async def test_yielded_clients_keep_drafts_and_only_explicit_takeover_restarts(
    tmp_path,
):
    runtime = SimpleNamespace(takeover=AsyncMock(), close=AsyncMock(), send=AsyncMock())
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch("session.create", {})
        sid = app.state["selectedSessionId"]
        await app.dispatch("view.update", {"patch": {"draft": "Keep this draft"}})
        await app.on_runtime_event(
            "runtime.ownership",
            {"sessionId": sid, "status": "yielded", "source": "Amplifier CLI"},
        )
        await app.dispatch("session.select", {"id": sid})
        assert not runtime.takeover.called
        with pytest.raises(AppError, match="read-only"):
            await app.dispatch(
                "conversation.send", {"sessionId": sid, "text": "must not be admitted"}
            )
        assert not runtime.send.called
        # An explicit id also works when a different chat is selected.
        await app.dispatch("session.create", {})
        assert app.state["selectedSessionId"] != sid
        # Session selection has its own existing draft policy. A takeover itself
        # must keep a draft and never submit it automatically.
        await app.dispatch("view.update", {"patch": {"draft": "Keep this draft"}})
        await app.dispatch(
            "session.takeover", {"id": sid}, command_id="explicit-takeover"
        )
        await asyncio.gather(*app.tasks)
        assert runtime.takeover.await_count == 1
        assert app.state["view"]["draft"] == "Keep this draft"
        assert app._session(sid)["ownership"]["status"] == "available"
        await app.dispatch(
            "session.takeover", {"id": sid}, command_id="explicit-takeover"
        )
        assert runtime.takeover.await_count == 1
    finally:
        await app.close()


async def test_takeover_checks_parked_worker_ownership_even_when_start_says_ready(
    tmp_path, monkeypatch
):
    from amplifier_foundation.session import ReleaseResult

    owner = {"app": "CLI", "acquisition_id": "current"}
    manager = RuntimeManager()
    manager.start = AsyncMock()
    manager._request = AsyncMock(
        side_effect=[SessionInUseError(owner), {"accepted": True}]
    )
    release = AsyncMock(return_value=ReleaseResult("released"))
    monkeypatch.setattr("amplifier_foundation.session.request_release", release)
    await manager.takeover(
        {"id": "interop", "workspace": str(tmp_path)}, AsyncMock(), owner
    )
    assert manager.start.await_count == 2
    assert [call.args for call in manager._request.await_args_list] == [
        ("interop", "resume"),
        ("interop", "resume"),
    ]
    assert release.await_count == 1


async def test_takeover_does_not_retarget_new_owner(tmp_path, monkeypatch):
    manager = RuntimeManager()
    manager.start = AsyncMock(side_effect=SessionInUseError({"acquisition_id": "new"}))
    release = AsyncMock()
    monkeypatch.setattr("amplifier_foundation.session.request_release", release)
    with pytest.raises(SessionInUseError):
        await manager.takeover(
            {"id": "interop", "workspace": str(tmp_path)},
            AsyncMock(),
            {"acquisition_id": "old"},
        )
    release.assert_not_called()
