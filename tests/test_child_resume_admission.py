"""Failed child admission preserves native evidence without running a model."""
import asyncio
import copy
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_foundation import Bundle
from amplifier_web.host.storage import SessionStore


@dataclass
class Prepared:
    mount_plan: dict
    bundle: Bundle
    child: object

    async def create_session(self, **kwargs):
        return self.child


@pytest.fixture
def admission(tmp_path, monkeypatch):
    pytest.importorskip("amplifier_module_loop_live")
    from amplifier_web.host.children import Children
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "shared"))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    store = SessionStore(tmp_path / "sessions")
    identity = "saved-child"
    original = [{"role": "user", "content": "Original request"},
                {"role": "assistant", "content": "Saved answer"},
                {"role": "tool", "tool_call_id": "old-call", "content": "Recorded tool evidence"}]
    metadata = {"parent_id": "parent", "agent_name": "worker", "agent_overlay": {},
                "status": "completed", "name": "Keep this title", "custom": {"keep": True}}
    store.save(identity, original, metadata)
    path = store.directory(identity)
    # Capture the actual canonical files, including metadata formatting/timestamps.
    before = {name: (path / name).read_bytes() for name in ("transcript.jsonl", "metadata.json")}
    context = SimpleNamespace(messages=[])
    capabilities = {}
    async def get_messages():
        return copy.deepcopy(context.messages)
    async def set_messages(messages):
        context.messages = copy.deepcopy(messages)
    context.get_messages = AsyncMock(side_effect=get_messages)
    context.set_messages = AsyncMock(side_effect=set_messages)
    mounts = {"context": context, "providers": {}, "orchestrator": SimpleNamespace(root_provider=None), "tools": {}}
    coordinator = SimpleNamespace(config={}, get=mounts.get, get_capability=capabilities.get,
        register_capability=lambda key, value: capabilities.update({key: value}),
        hooks=SimpleNamespace(register=Mock(return_value=Mock())), mount=AsyncMock())
    child = SimpleNamespace(session_id=identity, coordinator=coordinator, execute=AsyncMock(return_value="done"), cleanup=AsyncMock())
    parent_capabilities = {"session.working_dir": str(tmp_path)}
    parent_mounts = {"tools": {}}
    parent = SimpleNamespace(session_id="parent", coordinator=SimpleNamespace(config={},
        get=parent_mounts.get, get_capability=parent_capabilities.get))
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), store, None)
    children.prepared["parent"] = Prepared({}, Bundle(name="fixture", base_path=tmp_path), child)
    children.install = AsyncMock()
    return SimpleNamespace(children=children, parent=parent, parent_mounts=parent_mounts,
        parent_capabilities=parent_capabilities, child=child, context=context,
        coordinator=coordinator, capabilities=capabilities, store=store, identity=identity,
        original=original, metadata=metadata, path=path, before=before)


def assert_original(h):
    assert {name: (h.path / name).read_bytes() for name in h.before} == h.before
    h.child.execute.assert_not_awaited()
    h.child.cleanup.assert_awaited_once()


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_initialization_failure_keeps_child_cleanup_and_native_history(admission, monkeypatch, failure):
    h = admission
    h.parent_capabilities["web.provider_isolation"] = SimpleNamespace(preparation={})

    async def create(self, **kwargs):
        await kwargs["before_initialize"](self.child)
        assert "provider.before_load" in h.capabilities
        raise failure("Later initialization failed before create_session returned")

    monkeypatch.setattr(Prepared, "create_session", create)
    with pytest.raises(failure):
        await h.children.resume(h.identity, "Do not execute", h.parent)
    assert_original(h)
    assert h.children.sessions == {}


@pytest.mark.parametrize("failure", ["selection", "activity", "restore", "partial_restore", "install", "app_access",
                                      "cancel_activity", "cancel_restore", "cancel_app_access"])
async def test_failed_child_resume_keeps_transcript_and_metadata_bytes(admission, failure):
    h = admission
    error = asyncio.CancelledError() if failure.startswith("cancel") else ValueError("admission rejected")
    if failure == "selection":
        h.store.save(h.identity, h.original, {**h.metadata, "effective_selection": {"instance": "missing", "model": "saved"}})
        h.before = {name: (h.path / name).read_bytes() for name in h.before}
    elif failure in {"activity", "cancel_activity"}:
        async def fail_activity(coordinator):
            # Setup callbacks can request persistence before restoration.
            await coordinator.get_capability("live.checkpoint")()
            raise error
        h.parent_capabilities["web.activity.install"] = fail_activity
    elif failure in {"restore", "partial_restore", "cancel_restore"}:
        async def fail_restore(messages):
            if failure == "partial_restore":
                h.context.messages = copy.deepcopy(messages[:1])
            await h.capabilities["live.checkpoint"]()
            raise error
        h.context.set_messages.side_effect = fail_restore
    elif failure == "install":
        h.children.install.side_effect = error
    else:
        # App tools are installed with the child's authority, rather than
        # copied from the parent's mounted tool instances.
        async def fail_access(coordinator):
            assert coordinator is h.coordinator
            await coordinator.get_capability("live.checkpoint")()
            raise error
        install_access = AsyncMock(side_effect=fail_access)
        h.parent_capabilities["web.app_access.install"] = Mock(return_value=install_access)
    with pytest.raises(asyncio.CancelledError if failure.startswith("cancel") else ValueError):
        await h.children.resume(h.identity, "Do not execute", h.parent)
    assert_original(h)
    if failure in {"app_access", "cancel_app_access"}:
        h.parent_capabilities["web.app_access.install"].assert_called_once_with()
        install_access.assert_awaited_once_with(h.coordinator)
    expected = "cancelled" if failure.startswith("cancel") else "error"
    assert h.children.snapshot()[0]["status"] == expected
    events = []
    while not h.children.root.inbox.empty():
        events.append(h.children.root.inbox.get_nowait())
    assert events[-1][1]["status"] == expected
    assert events[-1][1]["event"] == "execution.finished"
    assert not h.children.sessions
    assert h.identity not in h.children.prepared


async def test_setup_checkpoint_does_not_publish_partially_restored_history(admission):
    h = admission
    async def restore(messages):
        h.context.messages = copy.deepcopy(messages[:1])
        await h.capabilities["live.checkpoint"]()
        assert {name: (h.path / name).read_bytes() for name in h.before} == h.before
        h.context.messages = copy.deepcopy(messages)
    h.context.set_messages.side_effect = restore
    await h.children.resume(h.identity, "New instruction", h.parent)
    assert h.store.load(h.identity)[0] == h.original
    h.child.execute.assert_awaited_once_with("New instruction")
    assert h.store.load(h.identity)[1]["status"] == "completed"


@pytest.mark.parametrize("dispatch", ["job", "task", "unknown"])
async def test_resume_never_reuses_saved_spawn_call_identity(admission, dispatch):
    from amplifier_web.host.children import JOB_CALL
    h = admission
    h.store.save(h.identity, h.original, {**h.metadata, "tool_call_id": "old-spawn"})
    if dispatch == "task":
        h.parent.coordinator._tool_dispatch_contexts = {asyncio.current_task(): {"tool_call_id": "new-resume"}}
    token = JOB_CALL.set("new-resume" if dispatch == "job" else None)
    try:
        await h.children.resume(h.identity, "New instruction", h.parent)
    finally:
        JOB_CALL.reset(token)
    expected = None if dispatch == "unknown" else "new-resume"
    assert h.children.rows[h.identity]["callId"] == expected
    assert h.store.load(h.identity)[1]["tool_call_id"] == expected


@pytest.mark.parametrize("cancelled", [False, True])
async def test_execution_failure_after_admission_still_checkpoints(admission, cancelled):
    h = admission
    async def execute(instruction):
        h.context.messages.append({"role": "user", "content": instruction})
        await h.capabilities["live.checkpoint"]()
        assert h.store.load(h.identity)[0][-1]["content"] == instruction
        raise asyncio.CancelledError() if cancelled else ValueError("execution failed")
    h.child.execute.side_effect = execute
    with pytest.raises(asyncio.CancelledError if cancelled else ValueError):
        await h.children.resume(h.identity, "Explicit new work", h.parent)
    messages, metadata = h.store.load(h.identity)
    assert messages == h.original + [{"role": "user", "content": "Explicit new work"}]
    assert metadata["status"] == ("cancelled" if cancelled else "error")
    h.child.execute.assert_awaited_once()
    h.child.cleanup.assert_awaited_once()


async def test_empty_saved_history_is_restored_before_admission(admission):
    h = admission
    h.store.save(h.identity, [], h.metadata)
    h.context.messages = [{"role": "user", "content": "Mount-created placeholder"}]
    await h.children.resume(h.identity, "New instruction", h.parent)
    h.context.set_messages.assert_awaited_once_with([])
    assert h.store.load(h.identity)[0] == []


async def test_child_preparation_applies_recording_policy_to_provider_overrides(admission, monkeypatch, tmp_path):
    h = admission
    path = tmp_path / 'app' / 'diagnostics' / 'config.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"providerRequests":true}')
    seen = []
    async def create(prepared, **kwargs):
        seen.append(prepared.mount_plan)
        return h.child
    monkeypatch.setattr(Prepared, 'create_session', create)
    await h.children.spawn('worker', 'New instruction', h.parent,
        agent_configs={'worker':{'providers':[
            {'module':'provider-litellm'},
            {'module':'provider-openai','config':{'raw':False}}]}})
    assert seen[0]['providers'][0]['config']['raw_debug'] is True
    assert seen[0]['providers'][1]['config']['raw'] is False
    assert h.capabilities['web.provider_request_redaction'] is True


async def test_child_preference_keeps_explicit_capture_with_app_recording_off(admission, monkeypatch):
    from amplifier_web.provider_recording import apply_provider_recording
    h = admission
    plan = h.parent.coordinator.config
    plan['providers'] = [{'module':'provider-openai','id':'one'}]
    apply_provider_recording(plan, enabled=True)
    seen = []
    async def create(prepared, **kwargs):
        seen.append(prepared.mount_plan)
        return h.child
    monkeypatch.setattr(Prepared, 'create_session', create)
    await h.children.spawn('worker', 'New instruction', h.parent,
        agent_configs={'worker':{}},
        provider_preferences=[{'provider':'one','model':'fixture-model','config':{'raw':True}}])
    assert seen[0]['providers'][0]['config']['raw'] is True
    apply_provider_recording(seen[0], enabled=False)
    assert seen[0]['providers'][0]['config']['raw'] is True


def pending_job(h):
    from amplifier_module_loop_live.job_store import JobStore
    ledger = JobStore(h.path / "live-jobs")
    call = SimpleNamespace(id="pending-call", model_dump=lambda: {"id": "pending-call", "name": "fixture", "arguments": {}})
    ledger.begin(call, "job-1", "original receipt")
    ledger.close()
    path = next((h.path / "live-jobs").glob("job-*.json"))
    return path, path.read_bytes()


@pytest.mark.parametrize("stage", ["original_restore", "recovered_restore", "install"])
async def test_persistent_admission_preserves_history_and_never_replays_pending_jobs(admission, stage):
    from amplifier_module_loop_live.job_store import JobStore
    from amplifier_web.host.children import _PERSISTENT
    h = admission
    job_path, job_before = pending_job(h)
    calls = 0
    async def restore(messages):
        nonlocal calls
        calls += 1
        h.context.messages = copy.deepcopy(messages[:1])
        await h.capabilities["live.checkpoint"]()
        if stage == "original_restore" or (stage == "recovered_restore" and calls == 2):
            raise ValueError("restoration failed")
        h.context.messages = copy.deepcopy(messages)
    h.context.set_messages.side_effect = restore
    if stage == "install":
        h.children.install.side_effect = ValueError("setup failed")
    token = _PERSISTENT.set(True)
    try:
        with pytest.raises(ValueError):
            await h.children.resume(h.identity, "Do not execute", h.parent)
    finally:
        _PERSISTENT.reset(token)
    assert_original(h)
    assert h.children.snapshot()[0]["status"] == "error"
    ledger = JobStore(h.path / "live-jobs")  # Ownership was released even on failure.
    try:
        if stage == "recovered_restore":
            assert ledger.rows["pending-call"]["status"] == "interrupted"
            assert "unconfirmed" in ledger.rows["pending-call"]["result"]
        else:
            assert job_path.read_bytes() == job_before
    finally:
        ledger.close()
