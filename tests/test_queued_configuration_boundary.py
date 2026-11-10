"""Configuration boundary regressions; not installed-release qualification.

Management, Collaboration, ConfigurationAuthority and RuntimeControls are real.
The coordinator and process transport are fixture adapters. Events hold a real
await in runtime preparation; peer admission uses the real host command ledger.
The original64 cases use no provider, subprocess, network or personal config.
Recovery cases below also exercise real worker command/bridge/controls in an
isolated subprocess with synthetic mounting and lease setup; no provider calls.
"""
import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from amplifier_web.management import Management
from amplifier_web.runtime_controls import RuntimeControls, override_path
from amplifier_web.service import AppError, AppService
from test_configuration_authority import finished, grant, revoke
from test_coordination_generation import agent


def plan(limit):
    return {
        "session": {"orchestrator": {"module": "loop-live"},
                    "context": {"module": "context-simple"}},
        "providers": [{"module": "provider-fixture"}],
        "tools": [{"module": "tool-fixture", "config": {"limit": limit}}],
    }


class ControlsTransport:
    """Only process creation/loading is simulated; mutation and idle checks aren't."""

    def __init__(self, home, sid):
        self.home, self.sid = home, sid
        self.native = SimpleNamespace(generation=None, queued_inputs=0)
        self.loop = SimpleNamespace(root_provider=None, jobs={})
        capabilities = {}
        self.coordinator = SimpleNamespace(
            config=plan(1), session_state={},
            get=lambda name: {"orchestrator": self.loop, "providers": {}}.get(name),
            get_capability=capabilities.get,
            register_capability=lambda name, value: capabilities.update({name: value}),
        )
        self.controls = RuntimeControls(
            SimpleNamespace(session_id=sid, coordinator=self.coordinator), self.native)
        assert override_path(sid).is_relative_to(home)
        self.controls.state_path().parent.mkdir(parents=True, exist_ok=True)
        if not self.controls.state_path().exists():
            self.controls.state_path().write_text(json.dumps({
                "configurator": {"disabled": {"tools": ["tool-fixture"]}},
                "selection": {"instance": "fixture", "model": "fixture-model"},
            }))
        self.original_controls = self.controls.state_path().read_bytes()
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.calls, self.inputs = [], []
        self.block_start = True
        self.needs_mount = False
        self.fault = None

    async def start(self, session, emit, **kwargs):
        assert session["id"] == self.sid
        self.calls.append("start")
        if self.block_start:
            self.block_start = False
            self.entered.set()
            await self.release.wait()
        if kwargs.get("guard"):
            kwargs["guard"]()
        if self.needs_mount:
            # Model only the saved-vs-loaded distinction, not a native mount.
            self.coordinator.config = json.loads(override_path(self.sid).read_text())
            self.calls.append("fixture-mounted")
            self.needs_mount = False

    async def stop(self, sid, *, reason="stopped"):
        self.calls.append("stop:" + reason)
        if reason == "configuration":
            self.needs_mount = True
            if self.fault == "stop-after-save":
                raise RuntimeError("Synthetic retirement failure after settings save")

    async def control(self, sid, operation, args, *, guard=None):
        if guard is not None:
            guard()
        assert sid == self.sid
        self.calls.append(operation)
        if operation == "configuration.providers":
            return {"providers": []}  # Not a provider-catalog test.
        if operation == "configuration.apply" and self.fault == "lost-before-dispatch":
            raise RuntimeError("Synthetic transport lost: mutation outcome unknown")
        if operation == "configuration.apply":
            from amplifier_web.configuration_recovery import native_apply
            result = await native_apply(self.controls, args["config"], args["_configurationAttempt"],
                lambda value: self.app._app_bridge("configuration.effect.admit", value, sid),
                lambda: self.app._session(sid).get("interruptionRevision", 0))
        else:
            result = await self.controls.perform(operation, args)
        if operation == "configuration.apply":
            self.calls.append("saved")
            if self.fault == "lost-after-save":
                raise RuntimeError("Synthetic transport lost: mutation outcome unknown")
            if self.fault == "cancel-after-save":
                raise asyncio.CancelledError()
        return result

    async def collaboration_input(self, session, args, guard, emit):
        reason = guard()
        if reason:
            return {"accepted": False, "reason": reason}
        admission = self.app.collaboration.admission(session["id"], args)
        assert admission["admitted"], admission
        self.inputs.append(args["inputId"])
        self.native.queued_inputs += 1
        # Deliberately delay the status event: native admission may precede
        # the host's projection. This is not an all-success fake control.
        return {"accepted": True}

    async def close(self):
        self.release.set()
        await self.controls.close()


@pytest.fixture
async def boundary(tmp_path, monkeypatch):
    home = tmp_path / "synthetic-app"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "synthetic-native"))
    app = AppService(home, None, workspace=tmp_path)
    runtime = None
    try:
        owner, peer = [app._new_session({"title": title}) for title in ("Owner", "Peer")]
        app.state["sessions"] = [owner, peer]
        app.state["selectedSessionId"] = owner["id"]
        for row in (owner, peer):
            row["collaborationGeneration"] = {
                "id": "generation-" + row["id"], "inputIds": ["synthetic-human"], "terminal": False}
        runtime = ControlsTransport(home, owner["id"])
        app.runtime = runtime
        runtime.app = app
        app._publish()
        app.management = Management(app)
        yield SimpleNamespace(app=app, owner=owner, peer=peer, runtime=runtime)
    finally:
        if runtime is not None:
            runtime.release.set()
        await app.close()


async def queue(boundary, identity="original-apply"):
    app, owner = boundary.app, boundary.owner
    boundary.authorized = await grant(app, ["runtime_configuration"], ["session"])
    owner["status"] = "working"
    boundary.args = {"id": owner["id"], "config": plan(2), "whenIdle": True}
    boundary.receipt = await agent(app, owner, "configuration.apply", boundary.args, identity)
    assert (await finished(app, identity))["phase"] == "ready"
    assert owner["pendingConfiguration"]["phase"] == "queued"
    assert not boundary.runtime.calls  # Receipt is not evidence of application.
    boundary.authority = copy.deepcopy(owner["pendingConfiguration"]["authority"])
    boundary.payload = app.management.queued_path(owner["id"]).read_bytes()
    boundary.queued_task = app.management.pending_config_tasks[owner["id"]]
    return boundary


async def reach_apply(boundary):
    boundary.owner["status"] = "idle"
    await asyncio.wait_for(boundary.runtime.entered.wait(), 3)
    assert boundary.owner["configurationBusy"]
    assert boundary.owner["pendingConfiguration"]["phase"] == "applying"


@pytest.fixture
async def process_boundary(tmp_path, monkeypatch, request):
    from pathlib import Path
    import sys
    from amplifier_web.runtime import RuntimeManager
    home = tmp_path / "process-app"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "process-native"))
    fixture = Path(__file__).parent / "fixtures/configuration_effect_worker.py"
    command = [sys.executable, "-u", str(fixture)]
    if getattr(request, "param", None) == "uv":
        import shutil
        uv = shutil.which("uv")
        assert uv, "The launcher regression requires the installed uv helper"
        command = [uv, "run", "--no-project", "--python", sys.executable, *command]
    runtime = RuntimeManager(command=command,
        startup_timeout=10, retention={"prewarm_on_select": False})
    app = AppService(home, runtime, workspace=tmp_path)
    try:
        owner = app._new_session({"title": "Synthetic process owner"})
        # Exercise the actual app/native alias, not only identical UUIDs.
        owner["runtimeSessionId"] = "synthetic-native-owner"
        owner["collaborationGeneration"] = {
            "id": "process-generation", "inputIds": ["synthetic-human"], "terminal": False}
        app.state["sessions"] = [owner]
        app.state["selectedSessionId"] = owner["id"]
        app._publish()
        app.management = Management(app)
        runtime.app_bridge = app.app_bridge
        full_access = getattr(request, "param", None) == "full"
        if full_access:
            report = (await app.dispatch("access.get", {"sessionId": owner["id"]}))["result"]
            await app.dispatch("access.set", {"sessionId": owner["id"], "scope": "session",
                "mode": "full", "expectedRevision": report["revision"]})
        await asyncio.wait_for(runtime.start(owner, app.on_runtime_event), 12)
        ready = await runtime.control(owner["id"], "configuration.inspect", {})
        assert ready["plan"]["tools"][0]["config"]["limit"] == 1
        authorized = None if full_access else await grant(app, ["runtime_configuration"], ["session"])
        yield SimpleNamespace(app=app, owner=owner, runtime=runtime, authorized=authorized)
    finally:
        await asyncio.wait_for(app.close(), 12)


async def process_apply(boundary, identity="process-apply"):
    await agent(boundary.app, boundary.owner, "configuration.apply",
        {"id": boundary.owner["id"], "config": plan(2)}, identity,
        _runtimeSessionId=boundary.owner["runtimeSessionId"])
    result = await finished(boundary.app, identity)
    # finished is written by the management command's terminal publication.
    return result


@pytest.mark.parametrize("process_boundary", ["uv"], indirect=True)
async def test_uv_launcher_configuration_uses_native_worker_identity(process_boundary):
    b = process_boundary
    row = b.runtime.workers[b.owner["id"]]
    launcher_pid = row["process"].pid
    # Exercise application first: the original failure must be the actual
    # pre-write worker refusal, not a missing new diagnostic field assertion.
    result = await process_apply(b)
    assert result["phase"] == "ready", result
    saved = operation(b)
    assert saved["attempts"][0]["workerPid"] != launcher_pid
    assert saved["state"]["effect"] == "mounted"
    assert not b.owner["configurationBusy"]
    assert json.loads(override_path(b.owner["runtimeSessionId"]).read_text()) == plan(2)


@pytest.mark.parametrize("unconfirmed", [None, 0, True, "123"])
async def test_unconfirmed_native_worker_refuses_before_dispatch(process_boundary, unconfirmed):
    b = process_boundary
    b.runtime.workers[b.owner["id"]]["native_worker_pid"] = unconfirmed
    result = await process_apply(b)
    assert result["phase"] == "error"
    saved = operation(b)
    assert saved["state"]["effect"] == "none"
    assert saved.get("attempts", []) == []
    assert "identity was not confirmed" in saved["state"]["error"]
    assert not b.owner["configurationBusy"]
    assert not override_path(b.owner["runtimeSessionId"]).exists()


async def test_real_worker_process_refusal_reconciles_and_explicit_resume_mounts(process_boundary):
    from amplifier_web.configuration_recovery import evidence
    b = process_boundary
    await b.runtime._request(b.owner["id"], "fixture.busy", count=1)
    assert (await process_apply(b))["phase"] == "error"
    saved = operation(b)
    assert saved["state"]["effect"] == "none"
    assert saved["state"]["stage"] == "reconciled"
    assert not b.owner["configurationBusy"]
    attempt = saved["attempts"][-1]
    assert attempt["nativeSessionId"] == "synthetic-native-owner"
    assert attempt["workerPid"] == b.runtime.workers[b.owner["id"]]["process"].pid
    assert evidence(attempt)["effect"] == "none"
    assert not override_path("synthetic-native-owner").exists()
    await b.runtime._request(b.owner["id"], "fixture.busy", count=0)
    await recovery(b, "resume")
    await asyncio.wait_for(b.app.management.pending_config_tasks[b.owner["id"]], 12)
    final = operation(b)
    assert final["state"]["effect"] == "mounted"
    assert final["request"] == saved["request"] and len(final["attempts"]) == 2
    assert json.loads(override_path("synthetic-native-owner").read_text()) == plan(2)
    assert b.owner["configuration"]["plan"]["tools"][0]["config"]["limit"] == 2


async def test_real_worker_process_partial_save_stays_held(process_boundary):
    from amplifier_web.configuration_recovery import evidence
    b = process_boundary
    await b.runtime._request(b.owner["id"], "fixture.partial")
    assert (await process_apply(b))["phase"] == "error"
    saved = operation(b)
    assert evidence(saved["attempts"][-1])["effect"] == "unknown"
    assert json.loads(override_path("synthetic-native-owner").read_text()) == plan(2)
    await recovery(b, "reconcile")
    with pytest.raises(AppError, match="no-effect"):
        await recovery(b, "resume")
    assert b.owner["configurationBusy"]


async def test_cold_receipt_read_never_starts_worker_or_replays(process_boundary):
    b = process_boundary
    await b.runtime._request(b.owner["id"], "fixture.busy", count=1)
    assert (await process_apply(b))["phase"] == "error"
    saved = operation(b)
    await b.runtime.stop(b.owner["id"], reason="shutdown")
    assert b.owner["id"] not in b.runtime.workers
    before = copy.deepcopy(saved)
    observed = await recovery(b, "read")
    assert observed["result"]["nativeEffect"] == "none"
    assert not b.runtime.workers and operation(b) == before
    assert not override_path("synthetic-native-owner").exists()


async def test_real_process_lost_ack_keeps_saved_distinct_from_mounted(process_boundary, monkeypatch):
    from amplifier_web.configuration_recovery import receipt_path
    b = process_boundary
    await b.runtime._request(b.owner["id"], "fixture.lose_reply")
    original_wait = asyncio.wait_for
    async def short_reply(awaitable, timeout):
        return await original_wait(awaitable, .5 if timeout == 30 else timeout)
    monkeypatch.setattr(asyncio, "wait_for", short_reply)
    assert (await process_apply(b))["phase"] == "error"
    saved = operation(b)
    receipt = receipt_path(saved["attempts"][-1]).read_bytes()
    read = (await recovery(b, "read"))["result"]
    assert read["nativeEffect"] == "saved_not_mounted" and read["state"]["effect"] == "unknown"
    await recovery(b, "reconcile")
    assert operation(b)["state"]["effect"] == "saved_not_mounted"
    await recovery(b, "resume")
    task = b.app.management.pending_config_tasks.get(b.owner["id"])
    if task is not None:
        await asyncio.wait_for(task, 3)
    # The old worker still has the previous plan. Continuation observes it,
    # never retires it, repeats the saved mutation or blesses its stale mount.
    assert "does not match loaded-plan" in operation(b)["state"]["error"]
    assert b.owner["configurationBusy"]
    assert receipt_path(saved["attempts"][-1]).read_bytes() == receipt
    assert json.loads(override_path("synthetic-native-owner").read_text()) == plan(2)
    # Once that worker is absent, an explicit continuation mounts the same save.
    await b.runtime.stop(b.owner["id"], reason="configuration")
    current = operation(b)
    await b.app.dispatch("configuration.operation.resume", {"sessionId": b.owner["id"],
        "operationId": saved["id"], "expectedRevision": current["revision"]})
    task = b.app.management.pending_config_tasks.get(b.owner["id"])
    if task is not None:
        await asyncio.wait_for(task, 12)
    assert operation(b)["state"]["effect"] == "mounted"
    assert operation(b)["attempts"] == saved["attempts"]
    assert receipt_path(saved["attempts"][-1]).read_bytes() == receipt


async def test_unsealed_success_ack_is_not_configuration_save_proof(boundary):
    original = boundary.runtime.control
    async def forged(sid, name, args, *, guard=None):
        if name == "configuration.apply":
            return {"requiresRestart": True, "configuration": plan(2)}
        return await original(sid, name, args, guard=guard)
    boundary.runtime.control = forged
    await queue(boundary)
    await reach_apply(boundary)
    await settle(boundary)
    assert operation(boundary)["state"]["effect"] == "unknown"
    assert boundary.owner["configurationBusy"]
    assert "stop:configuration" not in boundary.runtime.calls
    assert not override_path(boundary.owner["id"]).exists()


@pytest.mark.parametrize("invalidated", ["revoked", "expired", "stop"])
async def test_native_admission_wait_rechecks_original_authority(boundary, invalidated):
    from amplifier_web.configuration_recovery import native_apply, evidence
    entered, release = asyncio.Event(), asyncio.Event()
    previous = boundary.runtime.control
    async def authorize(value):
        entered.set()
        await release.wait()
        return await boundary.app._app_bridge("configuration.effect.admit", value, boundary.owner["id"])
    async def delayed(sid, name, args, *, guard=None):
        if name != "configuration.apply":
            return await previous(sid, name, args, guard=guard)
        if guard:
            guard()
        return await native_apply(boundary.runtime.controls, args["config"], args["_configurationAttempt"],
            authorize, lambda: boundary.owner.get("interruptionRevision", 0))
    boundary.runtime.control = delayed
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.release.set()
    await asyncio.wait_for(entered.wait(), 3)
    try:
        if invalidated == "revoked":
            await revoke(boundary.app, boundary.authorized)
        elif invalidated == "expired":
            grant_record = boundary.app.configuration_authority.record(boundary.authorized["id"])
            grant_record["expiresAt"] = time.time() - 1
            boundary.app.configuration_authority.save(grant_record)
            boundary.app.db.commit()
        else:
            boundary.owner["interruptionRevision"] = 1
    finally:
        release.set()
    await asyncio.wait_for(boundary.queued_task, 3)
    assert evidence(operation(boundary)["attempts"][-1])["effect"] == "none"
    assert not override_path(boundary.owner["id"]).exists()
    with pytest.raises(AppError):
        await recovery(boundary, "reconcile")
    with pytest.raises(AppError):
        await recovery(boundary, "resume")
    # Observing a terminal refusal releases input; it doesn't renew authority.
    assert operation(boundary)["state"]["effect"] == "none"
    assert not boundary.owner["configurationBusy"]


def use_native_receipts(boundary):
    """Real native settings writer/receipt; process transport remains an adapter."""
    from amplifier_web.configuration_recovery import native_apply
    runtime = boundary.runtime
    old = runtime.control

    async def authorize(value):
        return await boundary.app._app_bridge("configuration.effect.admit", value, runtime.sid)

    async def control(sid, name, args, *, guard=None):
        if name != "configuration.apply":
            return await old(sid, name, args, guard=guard)
        if guard:
            guard()
        runtime.calls.append(name)
        return await native_apply(runtime.controls, args["config"], args["_configurationAttempt"],
            authorize, lambda: boundary.owner.get("interruptionRevision", 0))

    runtime.control = control


async def recovery(boundary, verb, *, revision=None, identity=None, actor=None, **extra):
    saved = operation(boundary)
    args = {"sessionId": boundary.owner["id"], "operationId": saved["id"], **extra}
    if verb != "read":
        args["expectedRevision"] = revision if revision is not None else saved["revision"]
    return await agent(boundary.app, actor or boundary.owner, "configuration.operation." + verb,
        args, identity or "recovery-" + verb + "-" + str(saved["revision"]),
        _runtimeSessionId=(actor or boundary.owner).get("runtimeSessionId", (actor or boundary.owner)["id"]))


def deferred_native_evidence():
    """Model a receipt unavailable to the host until after failure handling.

    These cases exercise explicit recovery of retained unknown outcomes. The
    native writer and its authenticated receipt remain real; immediate terminal
    refusals now settle automatically and have separate process regressions.
    """
    return patch("amplifier_web.configuration_recovery.evidence", return_value=None)


async def native_refusal(boundary):
    use_native_receipts(boundary)
    with deferred_native_evidence():
        await queue(boundary)
        boundary.runtime.native.queued_inputs = 1
        await reach_apply(boundary)
        await settle(boundary)
    assert operation(boundary)["state"]["effect"] == "unknown"
    assert not override_path(boundary.owner["id"]).exists()
    return operation(boundary)


async def test_original_operation_read_is_observational_and_redacted(boundary):
    saved = await native_refusal(boundary)
    before = copy.deepcopy(saved)
    calls = list(boundary.runtime.calls)
    inspected = (await recovery(boundary, "read"))["result"]
    assert inspected["nativeEffect"] == "none"
    assert inspected["state"]["effect"] == "unknown"
    assert set(inspected) == {"operationId", "commandId", "sessionId", "revision", "state",
                              "history", "nativeEffect", "attempts", "workReplayed"}
    fields = {"phase", "stage", "effect", "attemptId", "revision", "at"}
    assert not set(inspected["state"]) - fields
    assert all(not set(row) - fields for row in inspected["history"])
    assert saved["attempts"][0]["key"] not in json.dumps(inspected)
    assert saved["request"]["authority"]["grantId"] not in json.dumps(inspected)
    assert operation(boundary) == before and boundary.runtime.calls == calls
    assert boundary.owner["configurationBusy"]


async def test_host_receipt_reads_use_service_home_not_ambient_home(boundary, monkeypatch, tmp_path):
    await native_refusal(boundary)
    calls = list(boundary.runtime.calls)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "unrelated-ambient-home"))
    assert (await recovery(boundary, "read"))["result"]["nativeEffect"] == "none"
    assert (await recovery(boundary, "reconcile"))["result"]["state"]["effect"] == "none"
    assert boundary.runtime.calls == calls
    assert not (tmp_path / "unrelated-ambient-home").exists()


async def test_reconciliation_does_not_schedule_queued_peer_or_warm(boundary, monkeypatch):
    await native_refusal(boundary)
    called = []
    async def drain(sid):
        called.append(sid)
    monkeypatch.setattr(boundary.app.collaboration, "drain", drain)
    calls = list(boundary.runtime.calls)
    await recovery(boundary, "reconcile")
    await scheduler_barrier()
    assert not called and boundary.runtime.calls == calls
    assert not boundary.runtime.inputs


async def test_recovery_cannot_borrow_original_grant_from_new_peer_input(boundary):
    await native_refusal(boundary)
    receipt = await agent(boundary.app, boundary.peer, "coordination.send",
        {"sessionId": boundary.owner["id"], "text": "Synthetic recovery request", "mode": "notify"},
        "unprivileged-peer-recovery")
    boundary.owner["collaborationGeneration"] = {
        "id": "peer-recovery-turn", "inputIds": [receipt["requestId"]], "terminal": False}
    with pytest.raises(AppError, match="cannot borrow"):
        await recovery(boundary, "reconcile")
    assert operation(boundary)["state"]["effect"] == "unknown"


async def test_stale_root_generation_cannot_resume(boundary):
    saved = await native_refusal(boundary)
    await recovery(boundary, "reconcile")
    current = operation(boundary)
    with pytest.raises(AppError, match="transport-bound"):
        await agent(boundary.app, boundary.owner, "configuration.operation.resume",
            {"sessionId": boundary.owner["id"], "operationId": saved["id"], "expectedRevision": current["revision"]},
            "stale-recovery", _generationId="old-generation")
    assert operation(boundary) == current


async def test_explicit_resume_waits_for_original_owner_turn_without_reapproval(boundary):
    await native_refusal(boundary)
    await recovery(boundary, "reconcile")
    boundary.runtime.native.queued_inputs = 0
    boundary.owner["status"] = "working"
    calls = list(boundary.runtime.calls)
    receipt = await recovery(boundary, "resume")
    task = boundary.app.management.pending_config_tasks[boundary.owner["id"]]
    await scheduler_barrier()
    assert receipt["result"]["state"]["phase"] == "queued"
    assert boundary.runtime.calls == calls
    boundary.owner["status"] = "idle"
    await asyncio.wait_for(task, 3)
    assert operation(boundary)["state"]["effect"] == "mounted"
    assert len(boundary.app.configuration_authority.rows(boundary.owner["id"])) == 1


async def test_original_operation_reconcile_resume_reuses_identity_and_authority(boundary):
    saved = await native_refusal(boundary)
    request, history = copy.deepcopy(saved["request"]), copy.deepcopy(saved["history"])
    reconciled = (await recovery(boundary, "reconcile"))["result"]
    assert reconciled["state"]["effect"] == "none"
    assert boundary.runtime.native.queued_inputs == 1  # Accepted work wasn't erased.
    assert not boundary.owner["configurationBusy"]
    # A later owner turn uses the original grant, not a fresh capture.
    boundary.owner["collaborationGeneration"] = {
        "id": "later-recovery-generation", "inputIds": ["synthetic-recovery"], "terminal": False}
    boundary.runtime.native.queued_inputs = 0
    resumed = await recovery(boundary, "resume", identity="explicit-resume")
    task = boundary.app.management.pending_config_tasks[boundary.owner["id"]]
    await asyncio.wait_for(task, 3)
    final = operation(boundary)
    assert final["id"] == saved["id"] and final["request"] == request
    assert final["history"][:len(history)] == history
    assert len(final["attempts"]) == 2
    assert final["attempts"][0] == saved["attempts"][0]
    assert final["attempts"][1]["attemptId"] != final["attempts"][0]["attemptId"]
    assert final["state"]["effect"] == "mounted" and final["state"]["phase"] == "ready"
    assert json.loads(override_path(boundary.owner["id"]).read_text()) == plan(2)
    calls = list(boundary.runtime.calls)
    repeated = await recovery(boundary, "resume", revision=reconciled["revision"], identity="explicit-resume")
    assert repeated["duplicate"] and repeated["result"] == resumed["result"]
    await scheduler_barrier()
    assert boundary.runtime.calls == calls


@pytest.mark.parametrize("verb", ["reconcile", "resume"])
async def test_recovery_stale_cas_and_peer_cannot_claim_original_operation(boundary, verb):
    saved = await native_refusal(boundary)
    if verb == "resume":
        await recovery(boundary, "reconcile")
    before = copy.deepcopy(operation(boundary))
    with pytest.raises(AppError, match="revision changed"):
        await recovery(boundary, verb, revision=1)
    with pytest.raises(AppError):
        await recovery(boundary, verb, actor=boundary.peer)
    assert operation(boundary) == before
    assert before["request"] == saved["request"]


@pytest.mark.parametrize("invalidated", ["revoked", "expired", "stop", "workspace", "execution", "payload"])
async def test_original_recovery_authority_and_target_cannot_be_replaced(boundary, invalidated):
    await native_refusal(boundary)
    await recovery(boundary, "reconcile")
    saved = operation(boundary)
    if invalidated == "revoked":
        await revoke(boundary.app, boundary.authorized)
        # A newly granted capability is deliberately NOT a replacement binding.
        await grant(boundary.app, ["runtime_configuration"], ["session"], "replacement-grant")
    elif invalidated == "expired":
        expired = boundary.app.configuration_authority.record(boundary.authorized["id"])
        expired["expiresAt"] = time.time() - 1
        boundary.app.configuration_authority.save(expired)
        boundary.app.db.commit()
    elif invalidated == "stop":
        boundary.owner["interruptionRevision"] = boundary.owner.get("interruptionRevision", 0) + 1
    elif invalidated == "workspace":
        boundary.owner["workspace"] += "/other"
    elif invalidated == "execution":
        boundary.owner["executionRevision"] = 1
    else:
        boundary.app.management.queued_path(boundary.owner["id"]).write_text(json.dumps(plan(3)))
    with pytest.raises(AppError):
        await recovery(boundary, "resume")
    assert operation(boundary)["request"] == saved["request"]
    assert len(operation(boundary)["attempts"]) == 1
    assert not override_path(boundary.owner["id"]).exists()


@pytest.mark.parametrize("fault", ["missing", "signature", "identity", "unknown", "saved"])
async def test_recovery_never_infers_no_effect_from_missing_or_forged_evidence(boundary, fault):
    from amplifier_web.configuration_recovery import receipt_path, seal
    saved = await native_refusal(boundary)
    attempt = saved["attempts"][-1]
    path = receipt_path(attempt)
    if fault == "missing":
        path.unlink()
    else:
        value = json.loads(path.read_text())
        if fault == "signature":
            value["signature"] = "0" * 64
        elif fault == "identity":
            value["body"]["attemptId"] = "foreign-attempt"
        else:
            value = seal(attempt, "unknown" if fault == "unknown" else "saved_not_mounted", terminal=True)
        path.write_text(json.dumps(value))
    if fault in {"missing", "signature", "identity"}:
        with pytest.raises(AppError):
            await recovery(boundary, "reconcile")
    else:
        assert (await recovery(boundary, "reconcile"))["result"]["state"]["effect"] != "none"
    with pytest.raises(AppError):
        await recovery(boundary, "resume")
    assert boundary.owner["configurationBusy"]
    assert len(operation(boundary)["attempts"]) == 1


async def test_native_attempt_duplicate_refuses_without_overwriting_receipt(boundary):
    from amplifier_web.configuration_recovery import native_apply, receipt_path
    saved = await native_refusal(boundary)
    attempt = saved["attempts"][0]
    original = receipt_path(attempt).read_bytes()
    boundary.runtime.native.queued_inputs = 0
    async def unauthorized(_):
        raise AssertionError("Duplicate must refuse before another admission")
    with pytest.raises((FileExistsError, ValueError)):
        await native_apply(boundary.runtime.controls, plan(2), attempt, unauthorized, lambda: 0)
    assert receipt_path(attempt).read_bytes() == original
    assert not override_path(boundary.owner["id"]).exists()


@pytest.mark.parametrize("field", ["operationId", "requestHash", "sessionId", "attemptId"])
async def test_private_attempt_swap_cannot_reconcile_another_operation(boundary, field):
    saved = await native_refusal(boundary)
    path = boundary.app.management.configuration_record_path(boundary.owner["id"], saved["id"])
    changed = copy.deepcopy(saved)
    changed["attempts"][-1][field] = "foreign-binding"
    path.write_text(json.dumps(changed))
    with pytest.raises(AppError, match="native attempt"):
        await recovery(boundary, "reconcile")
    assert boundary.owner["configurationBusy"]
    assert not override_path(boundary.owner["id"]).exists()


async def test_late_previous_attempt_cannot_authorize_resumed_attempt(boundary):
    from amplifier_web.configuration_recovery import authorize
    saved = await native_refusal(boundary)
    old = {k: v for k, v in saved["attempts"][0].items() if k != "key"}
    await recovery(boundary, "reconcile")
    boundary.runtime.native.queued_inputs = 0
    await recovery(boundary, "resume")
    await asyncio.wait_for(boundary.app.management.pending_config_tasks[boundary.owner["id"]], 3)
    with pytest.raises(ValueError, match="no longer admitted"):
        authorize(boundary.app.management, boundary.owner["id"], old)
    assert operation(boundary)["state"]["effect"] == "mounted"


async def test_partial_native_save_receipt_remains_unknown_and_cannot_resume(boundary, monkeypatch):
    from amplifier_web import runtime_controls
    from amplifier_web.configuration_recovery import evidence
    use_native_receipts(boundary)
    original = runtime_controls.write_private
    def partial(path, text):
        if path == boundary.runtime.controls.state_path():
            raise OSError("Synthetic second settings write failure")
        return original(path, text)
    monkeypatch.setattr(runtime_controls, "write_private", partial)
    await queue(boundary)
    await reach_apply(boundary)
    await settle(boundary)
    saved = operation(boundary)
    assert evidence(saved["attempts"][-1])["effect"] == "unknown"
    assert json.loads(override_path(boundary.owner["id"]).read_text()) == plan(2)
    assert boundary.runtime.controls.state_path().read_bytes() == boundary.runtime.original_controls
    await recovery(boundary, "reconcile")
    with pytest.raises(AppError, match="no-effect"):
        await recovery(boundary, "resume")
    assert boundary.owner["configurationBusy"]


async def test_recovery_raw_route_cannot_inject_receipt_or_borrow_other_origin(boundary):
    await native_refusal(boundary)
    with pytest.raises(AppError):
        await recovery(boundary, "reconcile", evidence={"effect": "none"})
    with pytest.raises(AppError, match="operation-bound"):
        await agent(boundary.app, boundary.owner, "runtime.control",
            {"sessionId": boundary.owner["id"], "operation": "configuration.operation.resume",
             "args": {"effect": "none"}}, "raw-recovery")
    assert operation(boundary)["state"]["effect"] == "unknown"


async def settle(boundary):
    boundary.runtime.release.set()
    await asyncio.wait_for(boundary.queued_task, 3)


async def scheduler_barrier():
    """Give constructor-scheduled tasks a bounded chance to enter, not a sleep."""
    entered = asyncio.Event()
    asyncio.get_running_loop().call_soon(entered.set)
    await asyncio.wait_for(entered.wait(), 1)


@pytest.mark.parametrize("busy", ["generation", "queued_inputs"])
async def test_real_native_guard_refuses_before_either_settings_write(boundary, busy):
    runtime = boundary.runtime
    setattr(runtime.native, busy, object() if busy == "generation" else 1)
    with pytest.raises(ValueError, match="Wait for the current response"):
        await runtime.controls.perform("configuration.apply", {"config": plan(2)})
    assert not override_path(runtime.sid).exists()
    assert runtime.controls.state_path().read_bytes() == runtime.original_controls
    assert runtime.coordinator.config == plan(1)


async def test_acceptance_save_and_loaded_readback_are_separate(boundary):
    await queue(boundary)
    assert not override_path(boundary.owner["id"]).exists()
    await reach_apply(boundary)
    assert boundary.runtime.coordinator.config == plan(1)
    await settle(boundary)
    assert boundary.owner["pendingConfiguration"]["phase"] == "ready"
    assert boundary.runtime.calls.index("saved") < boundary.runtime.calls.index("fixture-mounted")
    assert json.loads(override_path(boundary.owner["id"]).read_text()) == plan(2)
    expected_public = plan(2)
    expected_public["hooks"] = []
    for section in ("providers", "tools"):
        for row in expected_public[section]:
            row["enabled"] = True
    assert boundary.owner["configuration"]["plan"] == expected_public
    assert not boundary.app.management.queued_path(boundary.owner["id"]).exists()


async def test_later_generation_reuses_still_valid_original_authority(boundary):
    await queue(boundary)
    boundary.owner["collaborationGeneration"] = {
        "id": "later-generation", "inputIds": ["another-human-input"], "terminal": True}
    await reach_apply(boundary)
    await settle(boundary)
    assert boundary.owner["pendingConfiguration"]["phase"] == "ready"
    assert len(boundary.app.configuration_authority.rows(boundary.owner["id"])) == 1
    assert boundary.runtime.calls.count("configuration.apply") == 1


async def test_native_accepted_peer_is_not_erased_to_make_configuration_idle(boundary):
    app, owner, peer, runtime = boundary.app, boundary.owner, boundary.peer, boundary.runtime
    incoming = await agent(app, peer, "coordination.send",
                           {"sessionId": owner["id"], "text": "Already admitted input", "mode": "queue"},
                           "native-admitted-peer")
    await app.collaboration.drain(owner["id"])
    assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "accepted"
    assert runtime.native.queued_inputs == 1
    await queue(boundary)
    await reach_apply(boundary)
    await settle(boundary)
    assert not override_path(owner["id"]).exists()
    assert runtime.controls.state_path().read_bytes() == runtime.original_controls
    assert runtime.inputs == ["native-admitted-peer"]
    assert runtime.native.queued_inputs == 1
    assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "accepted"
    assert app.management.queued_path(owner["id"]).read_bytes() == boundary.payload


async def test_queued_peer_respects_mount_plan_fence_and_can_drain_after_release(boundary):
    app, owner, peer, runtime = boundary.app, boundary.owner, boundary.peer, boundary.runtime
    await queue(boundary)
    incoming = await agent(app, peer, "coordination.send",
                           {"sessionId": owner["id"], "text": "Synthetic earlier ACK", "mode": "queue"},
                           "older-peer-ack")
    await app.collaboration.drain(owner["id"])
    assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "queued"
    await reach_apply(boundary)
    assert owner.get("configurationRefresh", {}).get("phase") != "applying"
    try:
        await app.collaboration.drain(owner["id"])
        assert not runtime.inputs, "Peer admission crossed the claimed mount-plan boundary"
        assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "queued"
    finally:
        await settle(boundary)
    assert owner["pendingConfiguration"]["phase"] == "ready"
    # Explicit drain proves eventual eligibility, not the production automatic
    # release trigger. Native lifecycle-event integration remains a separate check.
    await app.collaboration.drain(owner["id"])
    assert runtime.inputs == ["older-peer-ack"], "The fix must release, not starve, the peer"
    assert runtime.calls.count("configuration.apply") == 1


@pytest.mark.parametrize("busy", ["generation", "queued_inputs"])
async def test_prewrite_refusal_retains_original_authority_and_exact_payload(boundary, busy):
    await queue(boundary)
    await reach_apply(boundary)
    setattr(boundary.runtime.native, busy, object() if busy == "generation" else 1)
    await settle(boundary)
    # This injection holds the real native guard at a known pre-write boundary.
    pending = boundary.owner["pendingConfiguration"]
    assert pending["phase"] == "error"
    assert "Wait for the current response" in pending["error"]
    assert not override_path(boundary.owner["id"]).exists()
    assert boundary.runtime.controls.state_path().read_bytes() == boundary.runtime.original_controls
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload
    assert pending.get("authority") == boundary.authority, "Do not discard the approved recovery binding"


@pytest.mark.parametrize("change", ["stop", "revoke", "expired", "workspace"])
async def test_delayed_apply_rechecks_original_authority_before_native_write(boundary, change):
    await queue(boundary)
    await reach_apply(boundary)
    app, owner = boundary.app, boundary.owner
    if change == "stop":
        await app.dispatch("conversation.stop", {"sessionId": owner["id"]})
    elif change == "revoke":
        await revoke(app, boundary.authorized)
    elif change == "expired":
        saved = app.configuration_authority.record(boundary.authorized["id"])
        saved["expiresAt"] = time.time() - 1
        app.configuration_authority.save(saved)
        app.db.commit()
    else:
        owner["workspace"] += "-moved"
    await settle(boundary)
    assert owner["pendingConfiguration"]["phase"] == "error"
    assert "configuration.apply" not in boundary.runtime.calls
    assert not override_path(owner["id"]).exists()
    assert app.management.queued_path(owner["id"]).read_bytes() == boundary.payload


async def test_peer_root_cannot_borrow_existing_owner_grant(boundary):
    await queue(boundary)
    with pytest.raises(AppError, match="Configuration authority is required"):
        await agent(boundary.app, boundary.peer, "configuration.apply",
                    {**boundary.args, "id": boundary.peer["id"]}, "peer-replay")
    assert not boundary.runtime.calls
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload


async def test_duplicate_delivery_after_prewrite_failure_is_passive(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.native.queued_inputs = 1
    await settle(boundary)
    previous = copy.deepcopy(boundary.owner["pendingConfiguration"])
    calls = list(boundary.runtime.calls)
    duplicate = await agent(boundary.app, boundary.owner, "configuration.apply",
                            boundary.args, "original-apply")
    assert duplicate["duplicate"]
    await scheduler_barrier()
    assert boundary.runtime.calls == calls
    assert boundary.owner["pendingConfiguration"] == previous
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload


async def test_delayed_failure_remains_correlated_with_the_original_command(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.native.queued_inputs = 1
    await settle(boundary)
    status = boundary.app.state["actionStatus"]["configuration.apply"]
    assert status["phase"] == "error"
    assert status["commandId"] == "original-apply", (
        "A generated internal command must not replace the caller's operation identity")


async def test_same_command_id_cannot_replace_the_queued_payload(boundary):
    await queue(boundary)
    with pytest.raises(AppError, match="different"):
        await agent(boundary.app, boundary.owner, "configuration.apply",
                    {**boundary.args, "config": plan(3)}, "original-apply")
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload
    assert boundary.owner["pendingConfiguration"]["authority"] == boundary.authority
    assert not boundary.runtime.calls


async def test_failure_between_native_writes_is_not_reported_as_mounted(boundary, monkeypatch):
    from amplifier_web import runtime_controls
    await queue(boundary)
    await reach_apply(boundary)
    write = runtime_controls.write_private
    writes = []

    def fail_second(path, content):
        writes.append(path)
        if path == boundary.runtime.controls.state_path():
            raise OSError("Synthetic second-write failure")
        return write(path, content)

    monkeypatch.setattr(runtime_controls, "write_private", fail_second)
    await settle(boundary)
    assert writes == [override_path(boundary.owner["id"]), boundary.runtime.controls.state_path()]
    assert json.loads(override_path(boundary.owner["id"]).read_text()) == plan(2)
    assert boundary.runtime.controls.state_path().read_bytes() == boundary.runtime.original_controls
    assert boundary.runtime.coordinator.config == plan(1)
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert "fixture-mounted" not in boundary.runtime.calls
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload


@pytest.mark.parametrize("fault", ["lost-before-dispatch", "lost-after-save", "stop-after-save"])
async def test_failed_apply_keeps_failure_and_does_not_replay_on_reopen(boundary, fault):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.fault = fault
    await settle(boundary)
    owner, runtime = boundary.owner, boundary.runtime
    pending = copy.deepcopy(owner["pendingConfiguration"])
    assert pending["phase"] == "error"
    written = fault != "lost-before-dispatch"
    assert override_path(owner["id"]).exists() == written
    assert runtime.coordinator.config == plan(1)  # Not mounted despite an optional save.
    if written:
        assert json.loads(override_path(owner["id"]).read_text()) == plan(2)
    assert boundary.app.management.queued_path(owner["id"]).read_bytes() == boundary.payload
    await boundary.app.close()
    fresh = ControlsTransport(boundary.app.data_dir, owner["id"])
    restored = AppService(boundary.app.data_dir, fresh, workspace=boundary.app.default_workspace)
    fresh.app = restored
    try:
        restored.management = Management(restored)
        await scheduler_barrier()
        assert restored._session(owner["id"])["pendingConfiguration"] == pending
        assert owner["id"] not in restored.management.pending_config_tasks
        assert not fresh.calls
    finally:
        await restored.close()


async def test_cancelled_native_result_is_not_automatically_requeued_after_reopen(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.fault = "cancel-after-save"
    boundary.runtime.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(boundary.queued_task, 3)
    assert json.loads(override_path(boundary.owner["id"]).read_text()) == plan(2)
    assert boundary.runtime.coordinator.config == plan(1)
    pending = copy.deepcopy(boundary.owner["pendingConfiguration"])
    assert pending["phase"] == "applying"
    await boundary.app.close()
    fresh = ControlsTransport(boundary.app.data_dir, boundary.owner["id"])
    restored = AppService(boundary.app.data_dir, fresh, workspace=boundary.app.default_workspace)
    fresh.app = restored
    try:
        restored.management = Management(restored)
        await scheduler_barrier()
        current = restored._session(boundary.owner["id"])
        assert current["pendingConfiguration"]["phase"] != "queued", (
            "An interrupted apply may have written settings; reconnect cannot silently replay it")
        assert boundary.owner["id"] not in restored.management.pending_config_tasks
        assert not fresh.calls
    finally:
        await restored.close()


async def test_cancel_before_admission_clears_pending_without_native_effect(boundary):
    await queue(boundary)
    await boundary.app.dispatch("configuration.cancel", {"id": boundary.owner["id"]},
                                command_id="cancel-original")
    assert (await finished(boundary.app, "cancel-original"))["phase"] == "ready"
    assert not boundary.app.management.queued_path(boundary.owner["id"]).exists()
    assert "pendingConfiguration" not in boundary.owner
    assert not boundary.runtime.calls


# Additional repair contracts. The original 22 baseline cases above remain
# unchanged, including their assertions that produced the retained hosted RED.
def operation(boundary):
    pending = boundary.owner["pendingConfiguration"]
    return boundary.app.management.configuration_record(boundary.owner["id"], pending["operationId"])


async def test_private_original_record_survives_success_with_ordered_effect_stages(boundary):
    await queue(boundary)
    original = operation(boundary)
    assert original["request"]["commandId"] == "original-apply"
    assert original["request"]["config"] == plan(2)
    assert original["request"]["authority"] == boundary.authority
    assert original["request"]["source"]["origin"] == "agent"
    assert original["request"]["source"]["callerSessionId"] == boundary.owner["id"]
    assert "config" not in boundary.owner["pendingConfiguration"]
    private = boundary.app.management.configuration_record_path(boundary.owner["id"], original["id"])
    assert private.stat().st_mode & 0o777 == 0o600
    await reach_apply(boundary)
    await settle(boundary)
    saved = operation(boundary)
    assert saved["request"] == original["request"]
    assert saved["requestHash"] == original["requestHash"]
    assert saved["history"][:len(original["history"])] == original["history"]
    assert [row["revision"] for row in saved["history"]] == list(range(1, saved["revision"] + 1))
    stages = [row["stage"] for row in saved["history"]]
    assert stages.index("preparing") < stages.index("dispatching") < stages.index("saved") < stages.index("readback")
    assert saved["state"]["phase"] == "ready"
    assert saved["state"]["effect"] == "mounted"
    assert private.exists()


async def test_ui_origin_and_internal_no_command_identity_are_explicit(boundary):
    app, owner = boundary.app, boundary.owner
    owner["status"] = "working"
    await app.dispatch("configuration.apply", {"id": owner["id"], "config": plan(2), "whenIdle": True},
                       command_id="user-apply")
    assert (await finished(app, "user-apply"))["phase"] == "ready"
    original = operation(boundary)
    assert original["request"]["authorization"] == "ui"
    assert original["request"]["authority"] is None
    await app.management.perform("configuration.cancel", {"id": owner["id"]})
    await app.management.perform("configuration.apply",
                                {"id": owner["id"], "config": plan(3), "whenIdle": True})
    internal = operation(boundary)
    assert internal["request"]["authorization"] == "internal"
    assert internal["request"]["commandId"] == internal["id"]
    assert app.management.configuration_record(owner["id"], original["id"])["state"]["phase"] == "cancelled"


async def test_ambient_callback_cannot_replace_a_missing_queued_grant(boundary):
    from amplifier_web.configuration_authority import CURRENT
    boundary.owner["status"] = "working"
    token = CURRENT.set(lambda **kwargs: None)
    try:
        with pytest.raises(ValueError, match="original configuration authority"):
            await boundary.app.management.perform("configuration.apply",
                {"id": boundary.owner["id"], "config": plan(2), "whenIdle": True})
    finally:
        CURRENT.reset(token)
    assert "pendingConfiguration" not in boundary.owner
    assert not boundary.runtime.calls


@pytest.mark.parametrize("change", ["executionRevision", "workingDirectory"])
async def test_original_placement_binding_is_checked_before_dispatch(boundary, change):
    await queue(boundary)
    if change == "executionRevision":
        boundary.owner[change] = 1
    else:
        boundary.owner[change] = boundary.owner["workspace"] + "/different-placement"
    boundary.owner["status"] = "idle"
    await asyncio.wait_for(boundary.queued_task, 3)
    assert not boundary.runtime.calls
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert operation(boundary)["state"]["effect"] == "none"
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload


@pytest.mark.parametrize("change", ["raw-payload", "private-request", "missing-record"])
async def test_corrupt_operation_binding_never_reaches_runtime(boundary, change):
    await queue(boundary)
    record = operation(boundary)
    path = boundary.app.management.configuration_record_path(boundary.owner["id"], record["id"])
    if change == "raw-payload":
        boundary.app.management.queued_path(boundary.owner["id"]).write_text(json.dumps(plan(9)))
    elif change == "private-request":
        altered = copy.deepcopy(record)
        altered["request"]["authority"] = None
        altered["request"]["authorization"] = "ui"
        path.write_text(json.dumps(altered))
    else:
        path.unlink()
    boundary.owner["status"] = "idle"
    await asyncio.wait_for(boundary.queued_task, 3)
    assert not boundary.runtime.calls
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert not override_path(boundary.owner["id"]).exists()
    # No corruption repair rewrites the private request as a trusted user action.
    if change == "private-request":
        assert json.loads(path.read_text()) == altered


@pytest.mark.parametrize("has_authority", [False, True])
async def test_legacy_pending_without_operation_binding_is_not_resumed(boundary, has_authority):
    app, owner = boundary.app, boundary.owner
    if has_authority:
        await queue(boundary)
        legacy = {"phase": "queued", "authority": boundary.authority}
    else:
        legacy = {"phase": "queued"}
        app.management.queued_path(owner["id"]).write_text(json.dumps(plan(2)))
    owner["pendingConfiguration"] = legacy
    owner["status"] = "working"
    app._publish()
    await app.close()
    fresh = ControlsTransport(app.data_dir, owner["id"])
    restored = AppService(app.data_dir, fresh, workspace=app.default_workspace)
    fresh.app = restored
    try:
        restored.management = Management(restored)
        await scheduler_barrier()
        pending = restored._session(owner["id"])["pendingConfiguration"]
        assert pending["phase"] == "error" and pending["recoveryRequired"]
        assert pending.get("authority") == legacy.get("authority")
        assert not fresh.calls
        assert owner["id"] not in restored.management.pending_config_tasks
        assert json.loads(restored.management.queued_path(owner["id"]).read_text()) == plan(2)
    finally:
        await restored.close()


async def test_new_request_cannot_replace_pending_or_unknown_original(boundary):
    await queue(boundary)
    before = operation(boundary)
    with pytest.raises(ValueError, match="original pending"):
        await boundary.app.management.perform("configuration.apply",
            {"id": boundary.owner["id"], "config": plan(3), "whenIdle": True},
            command_id="replacement")
    assert operation(boundary) == before
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload
    with deferred_native_evidence():
        await reach_apply(boundary)
        boundary.runtime.native.queued_inputs = 1
        await settle(boundary)
    failed = operation(boundary)
    assert failed["state"]["effect"] == "unknown"  # Native error text is not a no-effect receipt.
    with pytest.raises(ValueError, match="unresolved"):
        await boundary.app.management.perform("configuration.cancel", {"id": boundary.owner["id"]})
    with pytest.raises(ValueError, match="original pending"):
        await boundary.app.management.perform("configuration.apply",
            {"id": boundary.owner["id"], "config": plan(3), "whenIdle": True})
    assert operation(boundary) == failed


async def test_cancel_and_next_operation_retain_old_record_and_new_task(boundary):
    await queue(boundary)
    original = operation(boundary)
    await boundary.app.management.perform("configuration.cancel", {"id": boundary.owner["id"]})
    cancelled = boundary.app.management.configuration_record(boundary.owner["id"], original["id"])
    assert cancelled["request"] == original["request"]
    assert cancelled["history"][:1] == original["history"]
    assert cancelled["state"]["phase"] == "cancelled"
    await boundary.app.management.perform("configuration.apply",
        {"id": boundary.owner["id"], "config": plan(3), "whenIdle": True}, command_id="next-apply")
    next_record = operation(boundary)
    assert next_record["id"] != original["id"]
    next_task = boundary.app.management.pending_config_tasks[boundary.owner["id"]]
    await scheduler_barrier()
    assert boundary.app.management.pending_config_tasks[boundary.owner["id"]] is next_task
    assert not next_task.done()
    assert boundary.app.management.configuration_record(boundary.owner["id"], original["id"]) == cancelled


async def test_claim_waits_for_peer_already_submitting_without_retracting_it(boundary):
    from test_coordination_generation import until
    app, owner, peer, runtime = boundary.app, boundary.owner, boundary.peer, boundary.runtime
    entered, release = asyncio.Event(), asyncio.Event()
    original_input = runtime.collaboration_input

    async def held_input(*args):
        entered.set()
        await release.wait()
        return await original_input(*args)

    runtime.collaboration_input = held_input
    incoming = await agent(app, peer, "coordination.send",
                           {"sessionId": owner["id"], "text": "In-flight admission", "mode": "queue"},
                           "submitting-peer")
    draining = asyncio.create_task(app.collaboration.drain(owner["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "submitting"
        await queue(boundary)
        owner["status"] = "idle"
        assert not app.management.configuration_idle(owner)
        await scheduler_barrier()
        assert not runtime.entered.is_set()
        assert app.collaboration.receipt(incoming["requestId"])["delivery"] == "submitting"
    finally:
        release.set()
        await asyncio.wait_for(draining, 3)
    await until(lambda: app.collaboration.receipt(incoming["requestId"])["delivery"] == "accepted")
    await reach_apply(boundary)
    await settle(boundary)
    assert runtime.inputs == ["submitting-peer"]
    assert runtime.native.queued_inputs == 1
    assert not override_path(owner["id"]).exists()


async def test_confirmed_application_automatically_releases_waiting_peer(boundary):
    from test_coordination_generation import until
    await queue(boundary)
    await agent(boundary.app, boundary.peer, "coordination.send",
        {"sessionId": boundary.owner["id"], "text": "Wait for configuration", "mode": "queue"},
        "automatically-released")
    await reach_apply(boundary)
    assert not boundary.runtime.inputs
    await settle(boundary)
    await until(lambda: boundary.runtime.inputs == ["automatically-released"])
    assert boundary.owner["pendingConfiguration"]["phase"] == "ready"
    assert not boundary.owner["configurationBusy"]


@pytest.mark.parametrize("fault", ["lost-after-save", "stop-after-save"])
async def test_unresolved_mount_keeps_admission_and_never_warms_on_inspection(boundary, fault):
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.fault = fault
    await settle(boundary)
    calls = list(boundary.runtime.calls)
    assert boundary.owner["configurationBusy"]
    with pytest.raises(ValueError, match="unresolved"):
        await boundary.app.management.perform("configuration.inspect", {"id": boundary.owner["id"]})
    assert boundary.runtime.calls == calls
    # Stop remains an available supported action; it never clears an unknown effect.
    await boundary.app.dispatch("conversation.stop", {"sessionId": boundary.owner["id"]})
    assert operation(boundary)["state"]["effect"] in {"unknown", "saved_not_mounted"}


async def test_raw_apply_route_uses_the_same_original_operation_and_unknown_fence(boundary):
    app, owner, runtime = boundary.app, boundary.owner, boundary.runtime
    await grant(app, ["runtime_configuration"], ["session"])
    owner["status"] = "idle"
    runtime.fault = "lost-after-save"
    await agent(app, owner, "runtime.control",
        {"sessionId": owner["id"], "operation": "configuration.apply", "args": {"config": plan(2)}},
        "raw-original")
    await asyncio.wait_for(runtime.entered.wait(), 3)
    runtime.release.set()
    assert (await finished(app, "raw-original"))["phase"] == "error"
    saved = operation(boundary)
    assert saved["request"]["commandId"] == "raw-original"
    assert saved["request"]["source"]["origin"] == "agent"
    assert saved["state"]["effect"] == "unknown"
    assert owner["configurationBusy"]
    assert json.loads(override_path(owner["id"]).read_text()) == plan(2)


@pytest.mark.parametrize("route", ["configuration.apply", "runtime.control"])
@pytest.mark.parametrize("change", ["stop", "executionRevision", "workingDirectory"])
async def test_ui_admission_binds_target_before_management_lock_wait(boundary, route, change):
    app, owner = boundary.app, boundary.owner
    values = ({"id": owner["id"], "config": plan(2)} if route == "configuration.apply" else
              {"sessionId": owner["id"], "operation": "configuration.apply", "args": {"config": plan(2)}})
    await app.management.lock.acquire()
    try:
        await app.dispatch(route, values, command_id="before-wait")
        if change == "stop":
            await app.dispatch("conversation.stop", {"sessionId": owner["id"]})
        elif change == "executionRevision":
            owner[change] = 1
        else:
            owner[change] = owner["workspace"] + "/moved"
    finally:
        app.management.lock.release()
    assert (await finished(app, "before-wait"))["phase"] == "error"
    assert "configuration.apply" not in boundary.runtime.calls
    assert not override_path(owner["id"]).exists()
    assert "pendingConfiguration" not in owner


@pytest.mark.parametrize("action", ["absent-cancel", "queued-cancel", "queued-revoke"])
async def test_mount_plan_cleanup_does_not_release_an_independent_refresh(boundary, action):
    if action != "absent-cancel":
        await queue(boundary)
    owner = boundary.owner
    owner.update(configurationBusy=True, configurationRefresh={"phase": "applying"})
    if action == "queued-revoke":
        await revoke(boundary.app, boundary.authorized)
        await asyncio.wait_for(boundary.queued_task, 3)
        assert boundary.app.state["managementResults"]["original-apply"]["phase"] == "error"
    else:
        await boundary.app.management.perform("configuration.cancel", {"id": owner["id"]})
        await scheduler_barrier()
    assert owner["configurationBusy"]
    assert owner["configurationRefresh"]["phase"] == "applying"
    assert not boundary.runtime.inputs


async def test_cancelled_preparation_is_cancellable_without_erasing_original(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    original = operation(boundary)["request"]
    boundary.queued_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(boundary.queued_task, 3)
    current = operation(boundary)
    assert current["state"]["phase"] == "error"
    assert current["state"]["effect"] == "none"
    assert not boundary.owner["configurationBusy"]
    await boundary.app.management.perform("configuration.cancel", {"id": boundary.owner["id"]})
    record = boundary.app.management.configuration_record(boundary.owner["id"], current["id"])
    assert record["request"] == original
    assert record["state"]["phase"] == "cancelled"


@pytest.mark.parametrize("stage", ["prepare", "restart"])
@pytest.mark.parametrize("invalidation", ["stop", "revoke"])
async def test_runtime_start_receives_original_guard_across_its_wait(boundary, stage, invalidation):
    runtime = boundary.runtime
    entered, release = asyncio.Event(), asyncio.Event()
    start = runtime.start
    runtime.block_start = False

    async def guarded_start(session, emit, **kwargs):
        assert callable(kwargs.get("guard"))
        if runtime.needs_mount == (stage == "restart"):
            entered.set()
            await release.wait()
            kwargs["guard"]()
        await start(session, emit, **kwargs)

    runtime.start = guarded_start
    await queue(boundary)
    boundary.owner["status"] = "idle"
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if invalidation == "stop":
            await boundary.app.dispatch("conversation.stop", {"sessionId": boundary.owner["id"]})
        else:
            await revoke(boundary.app, boundary.authorized)
    finally:
        release.set()
    await asyncio.wait_for(boundary.queued_task, 3)
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert "fixture-mounted" not in runtime.calls
    assert override_path(boundary.owner["id"]).exists() == (stage == "restart")
    assert operation(boundary)["state"]["effect"] == ("saved_not_mounted" if stage == "restart" else "none")


async def test_unchanged_plan_readback_is_not_a_confirmed_mount(boundary):
    await queue(boundary)
    await reach_apply(boundary)
    control = boundary.runtime.control

    async def stale(sid, name, args, **kwargs):
        if name == "configuration.inspect":
            return {"plan": plan(1)}
        return await control(sid, name, args, **kwargs)

    boundary.runtime.control = stale
    await settle(boundary)
    assert operation(boundary)["state"]["effect"] == "saved_not_mounted"
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert boundary.owner["configurationBusy"]
    assert "does not match" in boundary.owner["pendingConfiguration"]["error"]


@pytest.mark.parametrize("loaded_limit", [1, 2])
async def test_wrong_saved_ack_cannot_replace_the_immutable_request(boundary, loaded_limit):
    await queue(boundary)
    original = copy.deepcopy(operation(boundary)["request"])
    await reach_apply(boundary)
    control = boundary.runtime.control
    observed = {}

    async def wrong_ack(sid, name, args, **kwargs):
        result = await control(sid, name, args, **kwargs)
        if name == "configuration.apply":
            observed["saved"] = plan(1)
            return {**result, "configuration": plan(1)}
        if name == "configuration.inspect":
            observed["loaded"] = plan(loaded_limit)
            return {**result, "plan": plan(loaded_limit)}
        return result

    boundary.runtime.control = wrong_ack
    await settle(boundary)
    # loaded_limit=1 is the discriminator: both reports agree with each other,
    # but neither is the authorized plan(2). The correct readback variant also
    # requires checking the saved acknowledgement, not silently ignoring it.
    assert observed == {"saved": plan(1), "loaded": plan(loaded_limit)}
    record = operation(boundary)
    assert record["request"] == original
    assert original["config"] == plan(2)
    assert original["authority"] == boundary.authority
    assert record["state"]["phase"] == "error"
    assert record["state"]["effect"] == "saved_not_mounted"
    assert boundary.owner["configurationBusy"]
    assert ("acknowledgement does not match" if loaded_limit == 2 else
            "does not match loaded-plan") in record["state"]["error"]
    assert boundary.app.management.queued_path(boundary.owner["id"]).read_bytes() == boundary.payload
    calls = list(boundary.runtime.calls)
    duplicate = await agent(boundary.app, boundary.owner, "configuration.apply",
                            boundary.args, "original-apply")
    assert duplicate["duplicate"]
    await scheduler_barrier()
    assert boundary.runtime.calls == calls
    assert boundary.runtime.calls.count("configuration.apply") == 1
    assert operation(boundary) == record


@pytest.mark.parametrize("legacy", [False, True])
async def test_keyed_cold_owner_restores_unresolved_admission_fence(boundary, legacy):
    app, owner = boundary.app, boundary.owner
    await queue(boundary)
    await reach_apply(boundary)
    boundary.runtime.fault = "lost-after-save"
    await settle(boundary)
    if legacy:
        owner["pendingConfiguration"] = {"phase": "error", "error": "Old unclassified failure"}
    app.state["selectedSessionId"] = boundary.peer["id"]
    app._publish()
    await app.close()
    fresh = ControlsTransport(app.data_dir, owner["id"])
    restored = AppService(app.data_dir, fresh, workspace=app.default_workspace)
    fresh.app = restored
    try:
        # Exercise keyed hydration directly if startup's working set already
        # included this owner; evict only the in-memory view, not saved evidence.
        restored._state["sessions"] = [s for s in restored._state["sessions"] if s["id"] != owner["id"]]
        restored.projections.invalidate(state=restored._state)
        restored.management = Management(restored)
        current = restored._session(owner["id"])
        assert current["configurationBusy"]
        assert not restored.warmup.allowed(current)
        with pytest.raises(AppError, match="settings"):
            await restored.dispatch("conversation.send", {"sessionId": owner["id"], "text": "Do not start"})
        assert not fresh.calls
    finally:
        await restored.close()


@pytest.mark.parametrize("invalidated", [False, True])
async def test_real_transport_admission_rechecks_grant_without_serializing_callback(boundary, invalidated, monkeypatch):
    from amplifier_web.runtime import RuntimeManager
    await queue(boundary)
    manager = RuntimeManager()
    sid = boundary.owner["id"]
    row = {"process": SimpleNamespace(returncode=None), "pending": {}, "inflight": set()}
    manager.workers[sid] = row
    writes = []

    async def write(target, payload):
        writes.append(payload)
        target["pending"][payload["id"]].set_result({"fixture": True})

    monkeypatch.setattr(manager, "_write", write)
    def guard():
        boundary.app.configuration_authority.check(boundary.authority)

    try:
        async with manager._admission(sid):
            pending = asyncio.create_task(manager.control(sid, "configuration.apply", {"config": plan(2)}, guard=guard))
            await scheduler_barrier()
            assert not writes
            if invalidated:
                await revoke(boundary.app, boundary.authorized)
        if invalidated:
            with pytest.raises(AppError, match="revoked"):
                await asyncio.wait_for(pending, 3)
            assert not writes and not row["inflight"]
        else:
            assert await asyncio.wait_for(pending, 3) == {"fixture": True}
            assert len(writes) == 1 and "guard" not in writes[0]
    finally:
        manager.workers.pop(sid, None)
        await manager.close()


@pytest.mark.parametrize("change", ["removed-agent", "removed-config-key", "nested-step-order", "provider-order"])
async def test_readback_rejects_retained_or_reordered_configuration(boundary, change):
    desired = plan(2)
    stale = copy.deepcopy(desired)
    if change == "removed-agent":
        desired["agents"] = {}
        stale["agents"] = {"old-agent": {"module": "agent-fixture"}}
    elif change == "removed-config-key":
        stale["tools"][0]["config"]["removed"] = "old-value"
    elif change == "nested-step-order":
        desired["tools"][0]["config"]["steps"] = [{"id": "validate"}, {"id": "execute"}]
        stale["tools"][0]["config"]["steps"] = [{"id": "execute"}, {"id": "validate"}]
    else:
        desired["providers"] = [{"module": "provider-fixture", "id": "first"},
                                {"module": "provider-fixture", "id": "second"}]
        stale["providers"] = list(reversed(copy.deepcopy(desired["providers"])))
    assert not Management.configuration_readback_matches(desired, stale)
    # Full mutation path must retain the same unconfirmed saved state.
    boundary.owner["status"] = "working"
    await boundary.app.management.perform("configuration.apply",
        {"id": boundary.owner["id"], "config": desired, "whenIdle": True})
    boundary.queued_task = boundary.app.management.pending_config_tasks[boundary.owner["id"]]
    control = boundary.runtime.control
    async def stale_control(sid, name, args, **kwargs):
        if name == "configuration.inspect":
            return {"plan": stale}
        return await control(sid, name, args, **kwargs)
    boundary.runtime.control = stale_control
    await reach_apply(boundary)
    await settle(boundary)
    assert operation(boundary)["state"]["effect"] == "saved_not_mounted"
    assert boundary.owner["pendingConfiguration"]["phase"] == "error"
    assert boundary.owner["configurationBusy"]


@pytest.fixture
async def recovery_http_boundary(tmp_path, monkeypatch, aiohttp_client):
    """Real authenticated HTTP entry; synthetic native mount, no model calls."""
    from amplifier_web.auth import new_session, SESSION_COOKIE
    from amplifier_web.server import create_app

    class UnstartedRuntime:
        async def close(self):
            pass

    home = tmp_path / "http-recovery"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "http-native"))
    web_app = await create_app(home, workspace=tmp_path, runtime=UnstartedRuntime(),
        voice=False, background_updates=False, preload_providers=False)
    app, client = web_app["service"], None
    try:
        owner, peer = [app._new_session({"title": title}) for title in ("Recovery owner", "Selected other chat")]
        app.state["sessions"] = [owner, peer]
        app.state["selectedSessionId"] = peer["id"]
        owner["collaborationGeneration"] = {
            "id": "original-http-generation", "inputIds": ["synthetic-human"], "terminal": False}
        peer["collaborationGeneration"] = {
            "id": "peer-http-generation", "inputIds": ["synthetic-peer"], "terminal": False}
        runtime = ControlsTransport(home, owner["id"])
        runtime.app = app
        await app.install_runtime(runtime)
        app._publish()
        client = await aiohttp_client(web_app)
        # A signed browser session cookie exercises auth_required, not a test
        # override of dispatch origin or a model-supplied caller identity.
        headers = {"Cookie": SESSION_COOKIE + "=" + new_session(web_app["session_secret"]),
                   "X-Amplifier-State-Transport": "delta-v1"}
        yield SimpleNamespace(app=app, owner=owner, peer=peer, runtime=runtime,
            client=client, headers=headers)
    finally:
        if client is not None:
            await asyncio.wait_for(client.close(), 5)
        else:
            await asyncio.wait_for(app.close(), 5)


async def http_recovery(boundary, verb, *, identity=None, revision=None):
    saved = operation(boundary)
    args = {"sessionId": boundary.owner["id"], "operationId": saved["id"]}
    if verb != "read":
        args["expectedRevision"] = saved["revision"] if revision is None else revision
    return await boundary.client.post("/api/actions", headers=boundary.headers, json={
        "action": "configuration.operation." + verb, "args": args,
        "id": identity or "http-" + verb + "-" + str(saved["revision"])})


async def terminal_refusal(boundary):
    with deferred_native_evidence():
        await queue(boundary)
        # The original turn really is over before deferred application begins.
        boundary.owner["collaborationGeneration"]["terminal"] = True
        boundary.runtime.native.queued_inputs = 1
        await reach_apply(boundary)
        await settle(boundary)
    saved = operation(boundary)
    assert saved["state"]["effect"] == "unknown"
    assert boundary.owner["configurationBusy"]
    assert boundary.owner["collaborationGeneration"]["terminal"]
    return saved


async def test_terminal_owner_recovers_via_authenticated_ui_entry_not_new_agent_turn(recovery_http_boundary, monkeypatch):
    from amplifier_web import configuration_recovery
    b = recovery_http_boundary
    saved = await terminal_refusal(b)
    before_grants = copy.deepcopy(b.app.configuration_authority.rows(b.owner["id"]))
    calls, origin_calls = list(b.runtime.calls), []
    original = configuration_recovery.command
    def observed(management, action, args, origin, caller):
        result = original(management, action, args, origin, caller)
        origin_calls.append((action, args["sessionId"], origin, caller))
        return result
    monkeypatch.setattr(configuration_recovery, "command", observed)
    args = {"sessionId": b.owner["id"], "operationId": saved["id"]}
    unauthenticated = await b.client.post("/api/actions", json={
        "action": "configuration.operation.read", "args": args})
    assert unauthenticated.status == 401
    cross_origin = await b.client.post("/api/actions",
        headers={**b.headers, "Origin": "https://untrusted.example"},
        json={"action": "configuration.operation.read", "args": args})
    assert cross_origin.status == 403
    catalog_response = await b.client.get("/api/actions", headers=b.headers)
    assert catalog_response.status == 200
    catalog = {row["name"] for row in await catalog_response.json()}
    assert {"configuration.operation." + verb for verb in ("read", "reconcile", "resume")} <= catalog
    # An active grant is not a live agent generation or permission to wake one.
    with pytest.raises(AppError, match="transport-bound"):
        await recovery(b, "reconcile")
    blocked_input = await b.client.post("/api/actions", headers=b.headers, json={
        "action": "conversation.send", "args": {"sessionId": b.owner["id"], "text": "Do not wake"},
        "id": "blocked-owner-input"})
    assert blocked_input.status == 409
    assert b.runtime.calls == calls
    observed_read = await http_recovery(b, "read")
    assert observed_read.status == 200
    assert (await observed_read.json())["result"]["nativeEffect"] == "none"
    assert operation(b) == saved and b.owner["configurationBusy"]
    reconciled_response = await http_recovery(b, "reconcile")
    assert reconciled_response.status == 200
    reconciled = (await reconciled_response.json())["result"]
    await scheduler_barrier()
    assert reconciled["state"]["effect"] == "none" and not b.owner["configurationBusy"]
    assert b.runtime.calls == calls and not b.runtime.inputs
    assert b.owner["collaborationGeneration"]["terminal"]
    # Only an explicit UI resume now schedules application under the original
    # request. Neither read nor reconcile starts an agent/provider generation.
    b.runtime.native.queued_inputs = 0
    resumed_response = await http_recovery(b, "resume", identity="ui-owner-resume")
    assert resumed_response.status == 200
    resumed = (await resumed_response.json())["result"]
    task = b.app.management.pending_config_tasks.get(b.owner["id"])
    if task is not None:
        await asyncio.wait_for(task, 3)
    final = operation(b)
    assert final["id"] == saved["id"] and final["request"] == saved["request"]
    assert final["state"]["effect"] == "mounted" and len(final["attempts"]) == 2
    assert b.app.configuration_authority.rows(b.owner["id"]) == before_grants
    assert b.app.state["selectedSessionId"] == b.peer["id"]  # Explicit owner target, not selection.
    assert b.owner["collaborationGeneration"]["terminal"]
    assert origin_calls == [
        ("configuration.operation." + name, b.owner["id"], "ui", None)
        for name in ("read", "reconcile", "resume")]
    calls = list(b.runtime.calls)
    duplicate_response = await http_recovery(b, "resume", identity="ui-owner-resume",
        revision=reconciled["revision"])
    duplicate = await duplicate_response.json()
    assert duplicate_response.status == 200 and duplicate["duplicate"]
    assert duplicate["result"] == resumed
    await scheduler_barrier()
    assert b.runtime.calls == calls


@pytest.mark.parametrize("invalidated", ["revoked", "expired", "stop", "execution"])
async def test_terminal_ui_recovery_still_requires_original_authority(recovery_http_boundary, invalidated):
    b = recovery_http_boundary
    saved = await terminal_refusal(b)
    calls = list(b.runtime.calls)
    if invalidated == "revoked":
        await revoke(b.app, b.authorized)
        await grant(b.app, ["runtime_configuration"], ["session"], "new-not-substitute-grant")
    elif invalidated == "expired":
        expired = b.app.configuration_authority.record(b.authorized["id"])
        expired["expiresAt"] = time.time() - 1
        b.app.configuration_authority.save(expired)
        b.app.db.commit()
    elif invalidated == "stop":
        stopped = asyncio.Event()
        original_stop = b.runtime.stop
        async def observed_stop(sid, *, reason="stopped"):
            await original_stop(sid, reason=reason)
            stopped.set()
        b.runtime.stop = observed_stop
        await b.app.dispatch("conversation.stop", {"sessionId": b.owner["id"]})
        await asyncio.wait_for(stopped.wait(), 3)
        calls = list(b.runtime.calls)  # Explicit Stop is not recovery warmup.
    else:
        b.owner["executionRevision"] = b.owner.get("executionRevision", 0) + 1
    assert (await http_recovery(b, "read")).status == 200
    for verb in ("reconcile", "resume"):
        response = await http_recovery(b, verb)
        assert response.status == (403 if invalidated in {"revoked", "expired"} else 409)
        refusal = await response.json()
        assert not refusal["accepted"]
        assert ("authority expired" if invalidated in {"revoked", "expired"}
                else "target was stopped or moved") in refusal["error"]
    assert operation(b)["request"] == saved["request"]
    assert len(operation(b)["attempts"]) == 1
    assert b.owner["configurationBusy"] and b.runtime.calls == calls


@pytest.mark.parametrize("outcome", ["none", "unknown", "saved_not_mounted"])
async def test_settled_cancelled_dispatch_reconciles_late_native_evidence_without_restart(
        recovery_http_boundary, monkeypatch, outcome):
    from amplifier_web import runtime_controls
    from amplifier_web.configuration_recovery import native_apply, evidence
    b = recovery_http_boundary
    entered, release = asyncio.Event(), asyncio.Event()
    native_tasks = []
    original_control = b.runtime.control
    original_write = runtime_controls.write_private
    if outcome == "unknown":
        def partial_write(path, text):
            if path == b.runtime.controls.state_path():
                raise OSError("Synthetic failure between settings writes")
            return original_write(path, text)
        monkeypatch.setattr(runtime_controls, "write_private", partial_write)

    async def authorize(value):
        if outcome == "none":
            entered.set()
            await release.wait()
        return await b.app._app_bridge("configuration.effect.admit", value, b.owner["id"])

    async def native_work(args):
        try:
            result = await native_apply(b.runtime.controls, args["config"], args["_configurationAttempt"],
                authorize, lambda: b.owner.get("interruptionRevision", 0))
        except Exception:
            if outcome != "none":
                entered.set()
                await release.wait()
            raise
        entered.set()
        await release.wait()
        return result

    async def delayed_control(sid, name, args, *, guard=None):
        if name != "configuration.apply" or native_tasks:
            return await original_control(sid, name, args, guard=guard)
        if guard:
            guard()
        task = asyncio.create_task(native_work(args))
        native_tasks.append(task)
        # Cancellation of the host waiter cannot cancel a command already sent.
        return await asyncio.shield(task)

    b.runtime.control = delayed_control
    try:
        await queue(b)
        b.owner["collaborationGeneration"]["terminal"] = True
        await reach_apply(b)
        b.runtime.release.set()
        await asyncio.wait_for(entered.wait(), 3)
        assert operation(b)["state"]["stage"] == "dispatching"
        assert not native_tasks[0].done()
        b.queued_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(b.queued_task, 3)
        saved = operation(b)
        assert saved["state"]["phase"] == "applying"
        assert saved["state"]["stage"] == "interrupted" and saved["state"]["effect"] == "unknown"
        assert b.owner["configurationBusy"] and not b.app.management.lock.locked()
        assert b.queued_task.done() and not native_tasks[0].done()
        assert (await http_recovery(b, "resume", identity="before-proof-resume")).status == 409
        calls = list(b.runtime.calls)
        release.set()
        returned = (await asyncio.wait_for(asyncio.gather(*native_tasks, return_exceptions=True), 3))[0]
        if outcome == "saved_not_mounted":
            assert isinstance(returned, dict) and returned["requiresRestart"]
        else:
            assert isinstance(returned, ValueError if outcome == "none" else OSError)
        proof = evidence(saved["attempts"][-1], home=b.app.data_dir)
        assert proof["effect"] == outcome
        if outcome == "none":
            assert proof["terminal"] and not override_path(b.owner["id"]).exists()
        else:
            assert json.loads(override_path(b.owner["id"]).read_text()) == plan(2)
        response = await http_recovery(b, "reconcile")
        assert response.status == 200
        classified = (await response.json())["result"]
        assert classified["state"]["phase"] == "error"
        assert classified["state"]["stage"] == "reconciled"
        assert classified["state"]["effect"] == outcome
        assert operation(b)["request"] == saved["request"]
        assert operation(b)["history"][:len(saved["history"])] == saved["history"]
        await scheduler_barrier()
        assert b.runtime.calls == calls and len(operation(b)["attempts"]) == 1
        assert b.owner["configurationBusy"] is (outcome != "none")
        assert b.owner["collaborationGeneration"]["terminal"]
        if outcome == "none":
            response = await http_recovery(b, "resume")
            assert response.status == 200
            task = b.app.management.pending_config_tasks.get(b.owner["id"])
            if task is not None:
                await asyncio.wait_for(task, 3)
            assert operation(b)["state"]["effect"] == "mounted"
            assert len(operation(b)["attempts"]) == 2
        elif outcome == "saved_not_mounted":
            assert (await http_recovery(b, "resume", identity="after-proof-resume")).status == 200
            task = b.app.management.pending_config_tasks.get(b.owner["id"])
            if task is not None:
                await asyncio.wait_for(task, 3)
            assert len(operation(b)["attempts"]) == 1
            assert b.runtime.calls.count("configuration.apply") == calls.count("configuration.apply")
        else:
            assert (await http_recovery(b, "resume", identity="after-proof-resume")).status == 409
            assert b.owner["configurationBusy"] and len(operation(b)["attempts"]) == 1
    finally:
        release.set()
        for task in native_tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*native_tasks, return_exceptions=True), 3)


@pytest.mark.parametrize("stage", ["preparing", "dispatching", "saved", "readback"])
async def test_only_interrupted_applying_state_can_enter_reconciliation(boundary, stage):
    await native_refusal(boundary)
    saved = operation(boundary)
    async with boundary.app.lock:
        boundary.app.management.configuration_transition(boundary.owner, saved["id"],
            phase="applying", stage=stage)
    current = operation(boundary)
    with pytest.raises(AppError, match="settled failed"):
        await recovery(boundary, "reconcile")
    assert operation(boundary) == current and boundary.owner["configurationBusy"]


async def test_interrupted_state_still_refuses_while_original_task_is_unsettled(boundary):
    await native_refusal(boundary)
    saved = operation(boundary)
    async with boundary.app.lock:
        boundary.app.management.configuration_transition(boundary.owner, saved["id"],
            phase="applying", stage="interrupted")
    blocked = asyncio.create_task(asyncio.Event().wait())
    boundary.app.management.pending_config_tasks[boundary.owner["id"]] = blocked
    try:
        with pytest.raises(AppError, match="task has not settled"):
            await recovery(boundary, "reconcile")
        assert boundary.owner["configurationBusy"]
    finally:
        blocked.cancel()
        await asyncio.gather(blocked, return_exceptions=True)
        boundary.app.management.pending_config_tasks.pop(boundary.owner["id"], None)


async def test_nonterminal_none_receipt_never_releases_interrupted_operation(boundary):
    from amplifier_web.configuration_recovery import receipt_path, seal
    await native_refusal(boundary)
    saved = operation(boundary)
    attempt = saved["attempts"][-1]
    receipt_path(attempt).write_text(json.dumps(seal(attempt, "none")))
    async with boundary.app.lock:
        boundary.app.management.configuration_transition(boundary.owner, saved["id"],
            phase="applying", stage="interrupted")
    current = operation(boundary)
    with pytest.raises(AppError, match="not terminal"):
        await recovery(boundary, "reconcile")
    assert operation(boundary) == current and boundary.owner["configurationBusy"]


async def wait_native_probe_exit(process, cleanup_recorded_workers, report):
    """This probe's timeout path must reap its handle even if worker identity refuses."""
    primary = None
    errors = report["cleanupErrors"]

    def failed(stage, exc):
        errors.append({"stage": stage, "type": type(exc).__name__, "message": str(exc)})

    try:
        await asyncio.wait_for(process.wait(), 240)
    except BaseException as exc:
        primary = exc
        report["primaryFailure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        try:
            if process.returncode is None:
                try:
                    report["probeSignals"].append("SIGTERM")
                    process.terminate()  # Probe handler cancels once and owns normal cleanup.
                    await asyncio.wait_for(process.wait(), 65)
                except BaseException as exc:
                    failed("probe-grace", exc)
            try:
                # Workers have independent sessions. Identity validation may
                # refuse, but cannot prevent cleanup of our directly owned handle.
                await cleanup_recorded_workers()
            except BaseException as exc:
                failed("recorded-workers", exc)
        finally:
            if process.returncode is None:
                try:
                    report["probeSignals"].append("SIGKILL")
                    process.kill()
                except BaseException as exc:
                    failed("probe-kill", exc)
                finally:
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except BaseException as exc:
                        failed("probe-reap", exc)
            report["returncode"] = process.returncode
    if primary is not None:
        for error in errors:
            primary.add_note("Cleanup: " + json.dumps(error))
        raise primary
    assert not errors, errors
    assert process.returncode is not None, "Owned probe exit remains unconfirmed."


def require_no_child_cleanup_fallback(report):
    assert not report["cleanupFallbackSignals"], (
        "Child fallback cleanup occurred; this cannot qualify normal native cleanup.",
        report["cleanupFallbackSignals"])


@pytest.mark.parametrize("variant", ["original", "skip-save-regression", "settings-continuation", "targeted-saved", "update-held"])
async def test_real_native_recovery_and_deliberately_regressed_save_witness(tmp_path, variant):
    """No importorskip: the admitted fixed workflow owns native prerequisites."""
    import os
    from pathlib import Path
    import signal
    import sys
    fixture = Path(__file__).parent / "fixtures/queued_configuration_native_probe.py"
    root = tmp_path / variant
    command = [sys.executable, "-u", str(fixture), str(root)]
    mutant = variant == "skip-save-regression"
    if mutant:
        command.append("--skip-save")
    if variant == "settings-continuation":
        command.append("--settings-continuation")
    if variant == "targeted-saved":
        command.append("--targeted-saved")
    if variant == "update-held":
        command.append("--update-held")
    # Retain finite structured proof and logs in the existing workflow artifact.
    # No homes, provider scenario, grants, native transcript or receipt keys.
    output = (Path(os.environ["GITHUB_WORKSPACE"]) / "output/python-checks"
        if os.environ.get("GITHUB_ACTIONS") == "true" else tmp_path) / ("native-recovery-" + variant)
    output.mkdir(parents=True, exist_ok=False)
    fallback = []
    async def cleanup_recorded_workers():
        # Only this probe's exact PID/start-tick/argv/group identities may be
        # signalled. Missing or reused identities are never guessed.
        for path in root.glob("worker-source-*.json"):
            row = json.loads(path.read_text())
            assert row["probePid"] == process.pid
            pid = row["pid"]
            def alive():
                try:
                    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                    assert fields[19] == row["startTicks"], "PID was reused; never signal it."
                    if fields[0] == "Z":
                        return False
                    args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                    assert str(root).encode() in args and b"--worker" in args
                    assert row["processGroup"] == os.getpgid(pid) == pid
                    return True
                except FileNotFoundError:
                    return False
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if not alive():
                    break
                fallback.append({"pid": pid, "startTicks": row["startTicks"], "signal": sig.name})
                try:
                    os.killpg(pid, sig)
                except ProcessLookupError:
                    break
                try:
                    async with asyncio.timeout(6):
                        while alive():
                            await asyncio.sleep(.05)
                except TimeoutError:
                    continue
            assert not alive(), "Exact owned worker termination remains unconfirmed."

    with (output / "stdout.log").open("wb") as stdout_file, (output / "stderr.log").open("wb") as stderr_file:
        process = await asyncio.create_subprocess_exec(*command, stdout=stdout_file, stderr=stderr_file)
        cleanup_report = {"probePid": process.pid, "fallbackSignals": fallback,
                          "probeSignals": [], "cleanupErrors": []}
        try:
            await wait_native_probe_exit(process, cleanup_recorded_workers, cleanup_report)
        finally:
            # Partial evidence survives timeout/cancellation, not just a passing
            # probe. Missing evidence is a failing prerequisite, never success.
            try:
                if (root / "evidence.json").exists():
                    (output / "evidence.json").write_bytes((root / "evidence.json").read_bytes())
            finally:
                (output / "parent-cleanup.json").write_text(json.dumps(cleanup_report, indent=2) + "\n")
    stderr = (output / "stderr.log").read_bytes()
    assert not fallback, "Fallback cleanup occurred; this cannot qualify normal native cleanup."
    evidence_path = root / "evidence.json"
    assert evidence_path.is_file(), stderr.decode(errors="replace")[-3000:]
    report = json.loads(evidence_path.read_text())
    (output / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    require_no_child_cleanup_fallback(report)  # Applies to both original and save-skipping variant.
    expected_workers = 1 if variant == 'update-held' else 3 if variant == 'targeted-saved' else 2
    assert len(report["processes"]) == expected_workers
    assert all(row["returncode"] is not None for row in report["processes"])
    assert not report["cleanupErrors"] and report["runtimeRowsAfterClose"] == 0
    assert report["heldWriterControl"] and report["writerAvailableAfterClose"] is True
    assert len(report["workerSources"]) == expected_workers
    assert all(row["mutant"] is mutant for row in report["workerSources"])
    assert all((row["originalFunction"] != row["executedFunction"]) is mutant
        for row in report["workerSources"])
    if variant == 'update-held':
        assert process.returncode == 0, stderr.decode(errors='replace')[-6000:]
        assert report['updateTerminalHeld'] and report['noTargetProviderCalls']
        assert report['oneSettingsSave'] and report['originalFailurePreserved']
        assert report['originalAuthorityPreserved'] and report['attempts'] == 1
        return
    if variant == 'targeted-saved':
        assert process.returncode == 0, stderr.decode(errors='replace')[-6000:]
        assert report['targetedSaved'] and report['noTargetProviderCalls']
        assert report['oneSettingsSave'] and report['actualManagerTool']
        assert report['originalAuthorityPreserved'] and report['attempts'] == 1
        assert report['mountedPlanVerified']
        return
    if variant == "settings-continuation":
        assert process.returncode == 0, stderr.decode(errors="replace")[-6000:]
        assert report["automaticContinuation"] and report["taskAndBudgetPreserved"]
        assert report["oneSettingsSave"] and report["exactlyOneReplacement"]
        assert report["duplicateOutcomePassive"]
        assert report["originalInputCount"] == report["originalToolResultCount"] == report["logicalContinuationCount"] == 1
        return
    assert report["prewriteRefusal"] and report["originalAuthorityPreserved"]
    assert report["resumedSaveAcknowledged"] and report["actualWorkerRemounted"]
    assert report["attempts"] == 2 and report["noRecoveryProviderCalls"]
    if mutant:
        assert process.returncode != 0, "Deliberately skipped product save escaped the same acceptance oracle."
        assert report["negativeWitness"] == "resumed_save_skipped_loaded_plan_mismatch"
        assert report["state"]["phase"] == "error" and report["state"]["effect"] == "saved_not_mounted"
        assert "Native recovery did not mount the original requested plan" in stderr.decode()
    else:
        assert process.returncode == 0, stderr.decode(errors="replace")[-3000:]
        assert report["mountedPlanVerified"] and report["nextInputUsable"]
        assert report["originalInputCount"] == report["originalToolResultCount"] == 1
        assert report["duplicateResumePassive"] and report["writerReleasedWhileParked"]


@pytest.mark.parametrize("exit_signal", ["SIGTERM", "SIGKILL"])
async def test_child_fallback_signals_are_retained_and_cannot_qualify(monkeypatch, exit_signal):
    from fixtures import queued_configuration_native_probe as fixture
    signals = []
    process = SimpleNamespace(pid=731001, returncode=None)

    def send(pid, signum):
        assert pid == process.pid
        signals.append(signum.name)
        if signum.name == exit_signal:
            process.returncode = -int(signum)

    async def wait():
        if process.returncode is None:
            raise TimeoutError("Synthetic worker did not exit after TERM.")
        return process.returncode

    process.wait = wait
    monkeypatch.setattr(fixture.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(fixture.os, "killpg", send)  # Never signal a real process in this control.
    report = {"cleanupFallbackSignals": []}
    await fixture.confirm_process_exit(process, report["cleanupFallbackSignals"])
    expected = ["SIGTERM"] if exit_signal == "SIGTERM" else ["SIGTERM", "SIGKILL"]
    assert signals == expected
    assert [row["signal"] for row in report["cleanupFallbackSignals"]] == expected
    assert all(row["outcome"] == "sent" for row in report["cleanupFallbackSignals"])
    assert report["cleanupFallbackSignals"][-1]["returncode"] == process.returncode
    if exit_signal == "SIGKILL":
        assert report["cleanupFallbackSignals"][0]["wait"] == "timed_out"
    # Same gate used before either positive or deliberately regressed acceptance.
    with pytest.raises(AssertionError, match="Child fallback cleanup occurred"):
        require_no_child_cleanup_fallback(report)


@pytest.mark.parametrize("refusal", ["identity-refused", "record-unreadable"])
@pytest.mark.parametrize("reaped", [True, False])
async def test_parent_timeout_always_reaps_probe_after_worker_cleanup_refusal(monkeypatch, refusal, reaped):
    steps, waits = [], []
    primary = TimeoutError("Synthetic probe deadline.")
    secondary = (AssertionError("PID was reused; never signal it.") if refusal == "identity-refused"
                 else OSError("Synthetic worker identity record unreadable."))
    process = SimpleNamespace(pid=731002, returncode=None)

    async def wait():
        steps.append("wait")
        if len(waits) == 1:
            raise primary
        if len(waits) == 2:
            raise TimeoutError("Synthetic grace deadline.")
        if not reaped:
            raise TimeoutError("Synthetic kill/reap deadline.")
        return process.returncode

    def terminate():
        steps.append("terminate")

    def kill():
        steps.append("kill-owned-probe")
        if reaped:
            process.returncode = -9

    async def workers():
        steps.append("refuse-unverified-worker")
        raise secondary  # No unverified PID may be signalled to make cleanup pass.

    original_wait_for = asyncio.wait_for

    async def timed_wait(awaitable, timeout):
        waits.append(timeout)
        return await original_wait_for(awaitable, timeout)

    process.wait, process.terminate, process.kill = wait, terminate, kill
    monkeypatch.setattr(asyncio, "wait_for", timed_wait)
    report = {"probeSignals": [], "cleanupErrors": []}
    with pytest.raises(TimeoutError) as caught:
        await wait_native_probe_exit(process, workers, report)
    assert caught.value is primary
    assert waits == [240, 65, 5]
    assert steps == ["wait", "terminate", "wait", "refuse-unverified-worker", "kill-owned-probe", "wait"]
    assert report["probeSignals"] == ["SIGTERM", "SIGKILL"]
    assert report["primaryFailure"] == {"type": "TimeoutError", "message": str(primary)}
    errors = report["cleanupErrors"]
    assert errors[1] == {"stage": "recorded-workers", "type": type(secondary).__name__,
                         "message": str(secondary)}
    assert [row["stage"] for row in errors] == (["probe-grace", "recorded-workers"] +
                                               ([] if reaped else ["probe-reap"]))
    assert len(primary.__notes__) == len(errors)
    assert report["returncode"] == (-9 if reaped else None)


async def test_parent_worker_cleanup_refusal_without_primary_is_not_swallowed():
    async def wait():
        return 0

    async def workers():
        raise AssertionError("Synthetic identity refusal after normal probe exit.")

    process = SimpleNamespace(pid=731003, returncode=0, wait=wait)
    report = {"probeSignals": [], "cleanupErrors": []}
    with pytest.raises(AssertionError, match="identity refusal"):
        await wait_native_probe_exit(process, workers, report)
    assert report["returncode"] == 0 and not report["probeSignals"]
    assert "primaryFailure" not in report
    assert report["cleanupErrors"][0]["stage"] == "recorded-workers"


@pytest.mark.parametrize("record_fault", ["malformed", "unreadable"])
@pytest.mark.parametrize("has_primary", [True, False])
def test_child_retains_cleanup_evidence_when_worker_record_is_broken(tmp_path, monkeypatch, record_fault, has_primary):
    from pathlib import Path
    from fixtures import queued_configuration_native_probe as fixture
    valid = {"pid": 731004, "probePid": 731005}
    (tmp_path / "worker-source-good.json").write_text(json.dumps(valid))
    broken = tmp_path / "worker-source-bad.json"
    broken.write_text("{malformed")
    if record_fault == "unreadable":
        read_text = Path.read_text

        def read(path, *args, **kwargs):
            if path == broken:
                raise OSError("Synthetic unreadable worker record.")
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
    primary = AssertionError("Original native loaded-plan mismatch.") if has_primary else None
    fallback = [{"pid": 731004, "signal": "SIGTERM", "outcome": "sent"}]
    report = {"cleanupErrors": ["Earlier cleanup failure."], "cleanupFallbackSignals": fallback,
              "processes": [{"pid": 731004, "returncode": -15}], "runtimeRowsAfterClose": 0}
    if primary is not None:
        report["primaryFailure"] = {"type": type(primary).__name__, "message": str(primary)}

    def finish():
        try:
            if primary is not None:
                raise primary
        finally:
            fixture.finish_cleanup_evidence(tmp_path, report, primary)

    with pytest.raises(AssertionError) as caught:
        finish()
    if primary is not None:
        assert caught.value is primary
        assert "worker-source-bad.json" in primary.__notes__[0]
        assert "Earlier cleanup failure." in primary.__notes__[0]
    else:
        assert "Native cleanup did not complete normally" in str(caught.value)
    saved = json.loads((tmp_path / "evidence.json").read_text())
    assert saved == report and saved["workerSources"] == [valid]
    assert saved["cleanupFallbackSignals"] == fallback
    assert len(saved["cleanupErrors"]) == 2
    assert "worker-source-bad.json" in saved["cleanupErrors"][1]
    assert ("JSONDecodeError" if record_fault == "malformed" else "OSError") in saved["cleanupErrors"][1]
