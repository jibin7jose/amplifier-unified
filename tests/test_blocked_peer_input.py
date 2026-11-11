"""Fresh peer input can be handled without resuming a dependency-blocked goal.

Action-path coverage uses the real app bridge and canonical RuntimeControls task
mutations. The inherited Runtime scripts transport and terminal events; these
checks do not claim installed native-worker execution or live-model efficacy.
Authored without running tests or importing project code on the host.
"""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_operations.coordination import fingerprint, peer_input
from amplifier_web.automatic_history import directory, display_identity
from amplifier_web.service import AppError
from amplifier_web.session_files import project_slug
from amplifier_web.task_binding import dependency_blocked
from test_collaborative_workspaces import (
    agent_action,
    app,
    finish,
    generation,
    settled,
    until,
)
from test_cross_workspace_coordination import cross_app
from test_task_continuity import controls


BLOCK_REASON = "Waiting for the independently owned dependency result."


async def mutate(ctx, action, *, origin="agent", **fields):
    """Let the actual dispatcher stamp origin and the worker own task CAS."""
    previous = ctx.owner.tasks.record() or {}
    args = {
        "sessionId": ctx.target["id"],
        "expectedRevision": previous.get("revision", 0),
        **fields,
    }
    identity = f"{action}-{args['expectedRevision']}-{origin}"
    previous_management = ctx.app.management
    # Only task dispatch needs this preparation stub. Leave event/notification
    # handling on the inherited fixture's normal management boundary.
    ctx.app.management = SimpleNamespace(ensure_runtime=AsyncMock())
    try:
        if origin == "agent":
            result = await ctx.app.app_bridge("dispatch", {
                "action": action,
                "id": identity,
                "args": args,
                "_runtimeSessionId": ctx.target["id"],
            }, ctx.target["id"])
        else:
            result = await ctx.app.dispatch(action, args, origin=origin, command_id=identity)
    finally:
        ctx.app.management = previous_management
    assert result["accepted"]
    assert ctx.target["task"] == ctx.owner.tasks.record()
    return result["result"]


async def block(ctx, *, origin="agent"):
    return await mutate(ctx, "task.block", origin=origin, reason=BLOCK_REASON)


async def send(ctx, identity="blocked-handoff", *, mode="queue", **fields):
    return await agent_action(ctx.app, ctx.source, "coordination.send", {
        "sessionId": ctx.target["id"],
        "text": "Check the interface dependency",
        "mode": mode,
        **fields,
    }, identity)


@pytest.fixture
async def task_app(cross_app, monkeypatch):
    """Independent roots plus a real task owner; no task guard is mocked."""
    service = cross_app
    source, target = service.state["sessions"]
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(service.data_dir))
    owner = controls(target["id"])
    async def control(sid, operation, args):
        assert sid == target["id"]
        return await owner.perform(operation, args)

    monkeypatch.setattr(service.runtime, "control", control, raising=False)
    ctx = SimpleNamespace(app=service, source=source, target=target, owner=owner, native_rows=[])
    try:
        await mutate(ctx, "task.create", objective="Review the owned dependency before proceeding",
                     constraints=["Preserve the original objective and prior effects"],
                     operationIds=["unresolved-dependency-operation"], maxTurns=6)
        # Synthetic already-consumed progress, captured by the real block action.
        owner.coordinator.session_state["goal"].update(turns_used=2, continuations=1)
        yield ctx
    finally:
        await owner.close()


def install_retained_task(ctx, task):
    """Simulate a legacy canonical record, not a new public mutation route."""
    ctx.owner.coordinator.session_state["task"] = copy.deepcopy(task)
    ctx.owner.persist(task_only=True)
    snapshot = ctx.owner.tasks.canonical_snapshot()
    ctx.target["task"] = snapshot["task"]
    ctx.app.state["runtimeControl"][ctx.target["id"]]["task.get"] = snapshot


async def checkpoint_reply(ctx, identity, text):
    """Append scripted canonical rows, retaining the preceding exchange intact."""
    target = ctx.target
    target.update(nativeProject=project_slug(target["workspace"]), nativeIdentity=target["id"])
    path = directory(target)
    path.mkdir(parents=True, exist_ok=True)
    if not ctx.native_rows:
        (path / "metadata.json").write_text(json.dumps({
            "session_id": target["id"], "working_dir": target["workspace"],
            "bundle": target["bundle"],
        }), encoding="utf-8")
    message = next(row for row in target["messages"] if row.get("inputId") == identity)
    new_rows = [
        {"role": "user", "content": peer_input(message["peerEnvelope"], message["text"]),
         "metadata": {"amplifier_input": {"version": 1, "kind": "user", "id": identity}}},
        {"role": "assistant", "content": text},
    ]
    index = len(ctx.native_rows) + 1
    with (path / "transcript.jsonl").open("a", encoding="utf-8") as stream:
        for row in new_rows:
            stream.write(json.dumps(row) + "\n")
    ctx.native_rows.extend(new_rows)
    anchor = {
        "messageId": display_identity(target, index, "assistant", text),
        "nativeIndex": index, "nativeText": text, "textDigest": fingerprint(text),
        "rootSessionId": target["id"], "generationId": "recipient-" + identity,
    }
    await ctx.app.on_runtime_event("runtime.collaboration_checkpoint", {
        "sessionId": target["id"], "rootSessionId": target["id"],
        "generation_id": anchor["generationId"], "messageAnchors": [anchor],
    })
    await finish(ctx.app, target, identity, text=text, nativeTerminal=anchor)
    return anchor


async def waiting_dependency(ctx, identity="manager-dependency"):
    """The task owner requests work, subscribes, then waits for its result."""
    ctx.source["collaborationGeneration"]["terminal"] = True
    await ctx.app.on_runtime_event("runtime.status", {
        "sessionId": ctx.source["id"], "status": "idle",
    })
    await generation(ctx.app, ctx.target, "manager-generation", ["manager-input"])
    await agent_action(ctx.app, ctx.target, "coordination.send", {
        "sessionId": ctx.source["id"], "text": "Finish the dependency and return its evidence",
        "mode": "queue",
    }, identity)
    await settled(ctx.app, identity, "accepted")
    await agent_action(ctx.app, ctx.target, "coordination.subscribe", {
        "sessionId": ctx.target["id"], "requestId": identity,
    }, "subscribe-" + identity)
    await generation(ctx.app, ctx.source, "recipient-" + identity, [identity])
    await block(ctx)
    await ctx.app.on_runtime_event("runtime.status", {
        "sessionId": ctx.target["id"], "status": "idle",
    })
    return identity


async def finish_dependency(ctx, identity):
    await agent_action(ctx.app, ctx.source, "coordination.reply", {
        "requestId": identity, "kind": "result", "outcome": "success",
        "text": "Dependency completed; inspect the retained evidence.",
        "references": ["urn:fixture:completed-dependency"],
    }, "reply-" + identity)
    await finish(ctx.app, ctx.source, identity)
    request = ctx.app.collaboration.receipt(identity)
    assert request["response"]["qualified"]
    return request["subscription"]["continuationId"]


@pytest.mark.parametrize("resume", [False, True])
async def test_saved_dependency_result_returns_across_owner_wait(task_app, resume):
    ctx = task_app
    identity = await waiting_dependency(ctx)
    if resume:
        await mutate(ctx, "task.resume")
    task = copy.deepcopy(ctx.target["task"])
    selection, draft = ctx.app.state["selectedSessionId"], ctx.app.state["view"]["draft"]
    returned = await finish_dependency(ctx, identity)
    await until(lambda: ctx.app.collaboration.receipt(returned)["delivery"] in {
        "accepted", "suppressed", "unknown",
    })
    receipt = ctx.app.collaboration.receipt(returned)
    assert receipt["delivery"] == "accepted", receipt.get("detail")
    assert receipt["dependencyRequestId"] == identity
    assert receipt["taskContinuationEpoch"] == task["continuationEpoch"]
    assert ctx.target["task"] == task
    assert ctx.app.state["selectedSessionId"] == selection
    assert ctx.app.state["view"]["draft"] == draft
    # Re-observing the same terminal result does not create another input.
    ctx.app.collaboration.enqueue_continuation(ctx.app.collaboration.receipt(identity))
    await ctx.app.collaboration.drain(ctx.target["id"])
    assert [row[1] for row in ctx.app.runtime.inputs].count(returned) == 1


@pytest.mark.parametrize("change", ["human-block", "pause-resume", "objective", "stop", "cancel"])
async def test_saved_dependency_result_respects_later_owner_boundaries(task_app, change):
    ctx = task_app
    identity = await waiting_dependency(ctx)
    if change == "human-block":
        await block(ctx, origin="ui")
    elif change == "pause-resume":
        await mutate(ctx, "task.pause", origin="ui")
        await mutate(ctx, "task.resume", origin="ui")
    elif change == "objective":
        await mutate(ctx, "task.update", objective="An explicitly different objective")
    elif change == "stop":
        ctx.target["interruptionRevision"] = 1
    else:
        await agent_action(ctx.app, ctx.target, "coordination.cancel", {
            "sessionId": ctx.target["id"], "requestId": identity,
            "reason": "The result is no longer requested",
        }, "cancel-" + identity)
    before = copy.deepcopy(ctx.target["task"])
    returned = await finish_dependency(ctx, identity)
    await ctx.app.collaboration.drain(ctx.target["id"])
    assert not any(row[1] == returned for row in ctx.app.runtime.inputs)
    assert ctx.target["task"] == before


async def test_fresh_handoffs_reply_and_repeat_without_resuming_task(task_app):
    ctx = task_app
    await block(ctx)
    before = copy.deepcopy(ctx.target["task"])
    assert dependency_blocked(before)
    assert before["goalState"]["turns_used"] == 2
    assert ctx.owner.coordinator.session_state["goal"] is None
    selection, draft = ctx.app.state["selectedSessionId"], ctx.app.state["view"]["draft"]
    previous = None
    first_native = None
    for number in (1, 2):
        identity = f"blocked-round-{number}"
        await ctx.app.on_runtime_event("runtime.status", {
            "sessionId": ctx.target["id"], "status": "idle",
        })
        sent = await send(ctx, identity)
        assert sent["delivery"] == "queued" and sent["blockedTaskInput"] is True
        assert sent["taskId"] == before["id"] and sent["taskRevision"] == before["revision"]
        assert "taskContinuationEpoch" not in sent and "humanResume" not in sent
        accepted = await settled(ctx.app, identity, "accepted")
        assert accepted["admission"]["accepted"] is True
        message = next(row for row in ctx.target["messages"] if row.get("inputId") == identity)
        assert message["inputOrigin"] == "peer"
        handling = message["peerEnvelope"]["blockedTaskHandling"]
        assert handling["taskId"] == before["id"] and handling["taskRevision"] == before["revision"]
        assert "blocked" in handling["instruction"].lower()
        assert ctx.app.runtime.inputs[-1][2] == peer_input(message["peerEnvelope"], message["text"])
        await generation(ctx.app, ctx.target, "recipient-" + identity, [identity])
        context = (await agent_action(ctx.app, ctx.target, "coordination.context", {
            "sessionId": ctx.target["id"],
        }, "context-" + identity))["result"]
        assert context["humanInput"] is None
        declaration = await agent_action(ctx.app, ctx.target, "coordination.reply", {
            "requestId": identity, "kind": "result", "outcome": "success",
            "text": f"Handled dependency report {number}; the objective remains blocked.",
            "references": [f"urn:fixture:blocked-report:{number}"],
        }, "reply-" + identity)
        assert declaration["result"]["status"] == "staged"
        assert declaration["result"]["qualified"] is False
        anchor = await checkpoint_reply(ctx, identity, f"Dependency report {number} retained")
        result = (await agent_action(ctx.app, ctx.source, "coordination.result", {
            "requestId": identity,
        }, "result-" + identity))["result"]
        assert result["qualified"] is True
        link, = result["results"]
        assert link["inputId"] == identity and link["messageId"] == anchor["messageId"]
        assert link["declaration"]["generationId"] == "recipient-" + identity
        assert link["declaration"]["independentArtifactVerification"] is False
        assert ctx.target["task"] == before and ctx.owner.tasks.record() == before
        assert not await ctx.owner.tasks.continuation_allowed()
        assert ctx.owner.coordinator.session_state["goal"] is None
        if previous is not None:
            assert ctx.app.collaboration.receipt("blocked-round-1") == previous
            assert (directory(ctx.target) / "transcript.jsonl").read_bytes().startswith(first_native)
        else:
            previous = copy.deepcopy(ctx.app.collaboration.receipt(identity))
            first_native = (directory(ctx.target) / "transcript.jsonl").read_bytes()
        assert (await send(ctx, identity))["duplicate"]
    await ctx.app.collaboration.drain(ctx.target["id"])
    assert [identity for _, identity, _ in ctx.app.runtime.inputs] == ["blocked-round-1", "blocked-round-2"]
    assert ctx.app.state["selectedSessionId"] == selection and ctx.app.state["view"]["draft"] == draft


async def test_notify_to_blocked_task_never_gets_input_exception_or_wakes(task_app):
    ctx = task_app
    await block(ctx)
    before = copy.deepcopy(ctx.target["task"])
    note = await send(ctx, "blocked-note", mode="notify")
    assert note["delivery"] == "notified" and not note.get("blockedTaskInput")
    message = next(row for row in ctx.target["messages"] if row.get("inputId") == "blocked-note")
    assert message["inputOrigin"] == "peer"
    assert "blockedTaskHandling" not in message["peerEnvelope"]
    await ctx.app.collaboration.drain(ctx.target["id"])
    assert ctx.app.runtime.inputs == [] and ctx.target["task"] == before
    assert ctx.owner.coordinator.session_state["goal"] is None


@pytest.mark.parametrize("capacity_denied", [False, True])
async def test_owner_resumes_resolved_agent_block_without_resetting_limits(task_app, capacity_denied):
    ctx = task_app
    await block(ctx)
    before = copy.deepcopy(ctx.owner.tasks.record())
    policy = copy.deepcopy(ctx.owner.capacity.policy)
    selection, draft = ctx.app.state["selectedSessionId"], ctx.app.state["view"]["draft"]
    await send(ctx, "resolved-dependency", text="The requested dependency result is ready for your inspection.")
    await settled(ctx.app, "resolved-dependency", "accepted")
    assert ctx.owner.tasks.record() == before, 'the handoff itself must not resume the objective'

    # Script the native blocked-input reservation and the owner's independent
    # verification decision; peer prose alone does not make that decision.
    ctx.owner.tasks.reserve_peer_input("resolved-dependency")
    await generation(ctx.app, ctx.target, "inspect-resolved-dependency", ["resolved-dependency"])
    await mutate(ctx, "task.update", correction="Verified the retained dependency artifact against its source.",
                 artifactRefs=["urn:fixture:verified-dependency"])
    used = ctx.owner.tasks.record()["goalState"]["turns_used"]
    assert used == 3
    denial = {"budgetRevision": policy["revision"], "state": "paused"} if capacity_denied else None
    ctx.owner.capacity.last_denial = copy.deepcopy(denial)
    before_inputs = list(ctx.app.runtime.inputs)
    result = await mutate(ctx, "task.resume")
    assert result["task"]["status"] == "active"
    resumed = ctx.owner.tasks.record()

    assert resumed["status"] == "active" and resumed["blockedReason"] is None
    for key in ("id", "objective", "constraints", "maxTurns", "questionIds", "operationIds"):
        assert resumed[key] == before[key]
    assert resumed["goalState"]["turns_used"] == used
    assert ctx.owner.coordinator.session_state["goal"]["turns_used"] == used
    assert ctx.owner.coordinator.session_state["goal"]["cap"] == before["maxTurns"]
    assert resumed["artifactRefs"] == ["urn:fixture:verified-dependency"]
    assert resumed["changes"][-1]["origin"] == "agent"
    assert ctx.owner.capacity.policy == policy and ctx.owner.capacity.last_denial == denial
    assert await ctx.owner.tasks.continuation_allowed() is (not capacity_denied)
    assert ctx.app.runtime.inputs == before_inputs, 'task.resume must not itself send or replay an input'
    assert ctx.app.state["selectedSessionId"] == selection and ctx.app.state["view"]["draft"] == draft


@pytest.mark.parametrize("patch", [
    {"mode": "notify"},
    {"mode": "steer"},
    {"dependencyRequestId": "retained-outcome"},
    {"creationOperationId": "commissioned-brief"},
    {"scheduleRepair": {"sourceMessageId": "separate-human-repair"}},
    {"humanResume": {"sourceMessageId": "separate-human-resume"}},
])
async def test_blocked_exception_does_not_transfer_to_other_input_classes(task_app, patch):
    ctx = task_app
    await block(ctx)
    ctx.target["status"] = "working"
    receipt = await send(ctx)
    assert receipt["blockedTaskInput"] is True
    assert ctx.app.collaboration.blocked_input(receipt, ctx.target["task"])
    # Internal receipt classification, not model-supplied arguments. Even a
    # stamped receipt cannot use this exception for another admission class.
    candidate = {**copy.deepcopy(receipt), **patch}
    assert not ctx.app.collaboration.blocked_input(candidate, ctx.target["task"])
    assert not ctx.app.runtime.inputs


@pytest.mark.parametrize("origin", ["agent", "ui", "user"])
async def test_actual_block_dispatcher_not_reason_text_controls_eligibility(task_app, origin):
    ctx = task_app
    await block(ctx, origin=origin)
    task = ctx.owner.tasks.record()
    proof = task["blockProvenance"]
    assert proof["version"] == 1 and proof["origin"] == origin
    assert proof["revision"] == task["revision"]
    assert dependency_blocked(task) is (origin == "agent")
    saved = json.loads(ctx.owner.state_path().read_text(encoding="utf-8"))
    assert saved["task"]["blockProvenance"] == proof
    before = copy.deepcopy(task)
    sent = await send(ctx)
    assert bool(sent.get("blockedTaskInput")) is (origin == "agent")
    await settled(ctx.app, "blocked-handoff", "accepted" if origin == "agent" else "suppressed")
    assert len(ctx.app.runtime.inputs) == (1 if origin == "agent" else 0)
    assert ctx.owner.tasks.record() == before


async def test_agent_cannot_launder_paused_task_into_dependency_block(task_app):
    ctx = task_app
    await mutate(ctx, "task.pause", origin="ui")
    await block(ctx)
    before = copy.deepcopy(ctx.target["task"])
    assert before["status"] == "blocked" and not dependency_blocked(before)
    sent = await send(ctx)
    assert not sent.get("blockedTaskInput")
    await settled(ctx.app, "blocked-handoff", "suppressed")
    assert not ctx.app.runtime.inputs and ctx.target["task"] == before


@pytest.mark.parametrize("state", ["paused", "completed", "stopped", "error", "cancelled", "human-stop"])
async def test_inactive_and_explicitly_stopped_recipients_stay_denied(task_app, state):
    ctx = task_app
    await block(ctx)
    if state == "paused":
        await mutate(ctx, "task.pause", origin="ui")
    elif state == "completed":
        await mutate(ctx, "task.complete", origin="ui", condition=ctx.target["task"]["objective"],
                     evidence=["Synthetic explicit completion decision"])
    elif state == "human-stop":
        await ctx.app.dispatch("conversation.stop", {"sessionId": ctx.target["id"]},
                               origin="ui", command_id="human-stop")
        await until(lambda: ctx.target["status"] == "stopped")
    else:
        ctx.target["status"] = state
    before = copy.deepcopy(ctx.target["task"])
    await send(ctx)
    await settled(ctx.app, "blocked-handoff", "suppressed")
    assert ctx.app.runtime.inputs == [] and ctx.target["task"] == before
    assert ctx.owner.coordinator.session_state["goal"] is None


@pytest.mark.parametrize("change", ["progress", "pause", "resume", "human-block", "stop"])
async def test_queued_handoff_keeps_exact_task_and_stop_binding(task_app, change):
    ctx = task_app
    await block(ctx)
    ctx.target["status"] = "working"
    sent = await send(ctx)
    assert sent["blockedTaskInput"] is True
    if change == "progress":
        await mutate(ctx, "task.update", artifactRefs=["new-evidence-reference"])
    elif change == "pause":
        await mutate(ctx, "task.pause", origin="ui")
    elif change == "resume":
        await mutate(ctx, "task.resume", origin="ui")
    elif change == "human-block":
        await block(ctx, origin="ui")
    else:
        await ctx.app.dispatch("conversation.stop", {"sessionId": ctx.target["id"]},
                               origin="ui", command_id="stop-queued-handoff")
        await until(lambda: ctx.target["status"] == "stopped")
    before = copy.deepcopy(ctx.target["task"])
    ctx.target["status"] = "idle"
    await ctx.app.collaboration.drain(ctx.target["id"])
    await settled(ctx.app, "blocked-handoff", "suppressed")
    assert ctx.app.runtime.inputs == [] and ctx.target["task"] == before


@pytest.mark.parametrize("change", ["task-revision", "stop-revision"])
async def test_final_host_authorization_rechecks_after_preparation(task_app, monkeypatch, change):
    ctx = task_app
    await block(ctx)
    permissions = []

    async def preparing(session, args, guard, emit):
        assert guard() is None
        initial = ctx.app.collaboration.admission(session["id"], args)
        permissions.append(copy.deepcopy(initial))
        assert initial["admitted"] and initial["blockedTaskInput"] is True
        if change == "task-revision":
            await mutate(ctx, "task.update", correction="Retain the revised dependency conditions")
        else:
            ctx.target["interruptionRevision"] = ctx.target.get("interruptionRevision", 0) + 1
        final = ctx.app.collaboration.admission(session["id"], args)
        permissions.append(copy.deepcopy(final))
        assert not final["admitted"] and not final.get("blockedTaskInput")
        assert guard()
        return {"accepted": False, "reason": final["reason"]}

    monkeypatch.setattr(ctx.app.runtime, "collaboration_input", preparing)
    await send(ctx)
    await settled(ctx.app, "blocked-handoff", "suppressed")
    assert len(permissions) == 2 and not ctx.app.runtime.inputs
    assert ctx.target["task"]["status"] == "blocked"


async def test_old_unstamped_queue_is_suppressed_not_upgraded_or_replayed(task_app):
    ctx = task_app
    await block(ctx)
    ctx.target["status"] = "working"
    await send(ctx, "legacy-unstamped")
    # A retained pre-feature queue has no committed exception. Remove only the
    # new host fields to model that old record, keeping its request/task binding.
    legacy = ctx.app.collaboration.receipt("legacy-unstamped")
    legacy.pop("blockedTaskInput", None)
    message = next(row for row in ctx.target["messages"] if row.get("inputId") == "legacy-unstamped")
    message["peerEnvelope"].pop("blockedTaskHandling", None)
    legacy["displayBinding"] = fingerprint([message["peerEnvelope"], message["text"]])
    ctx.app.collaboration.save("legacy-unstamped", legacy)
    ctx.app._publish()
    ctx.target["status"] = "idle"
    await ctx.app.collaboration.drain(ctx.target["id"])
    old = copy.deepcopy(await settled(ctx.app, "legacy-unstamped", "suppressed"))
    assert not ctx.app.runtime.inputs
    await send(ctx, "fresh-after-legacy")
    await settled(ctx.app, "fresh-after-legacy", "accepted")
    duplicate = await send(ctx, "legacy-unstamped")
    assert duplicate["duplicate"] and duplicate["delivery"] == "suppressed"
    await ctx.app.collaboration.drain(ctx.target["id"])
    assert ctx.app.collaboration.receipt("legacy-unstamped") == old
    assert [identity for _, identity, _ in ctx.app.runtime.inputs] == ["fresh-after-legacy"]


@pytest.mark.parametrize("origin", ["ui", "user", None])
async def test_legacy_block_without_agent_evidence_is_not_inferred_from_reason(task_app, origin):
    ctx = task_app
    await block(ctx, origin=origin or "agent")
    legacy = copy.deepcopy(ctx.target["task"])
    legacy.pop("blockProvenance", None)
    # Remove the new field from historical deltas too. Preserve actual old
    # origin/status records, or model an older record lacking that evidence.
    for row in legacy["changes"]:
        row["fields"].pop("blockProvenance", None)
    if origin is None:
        legacy.pop("changes", None)
    install_retained_task(ctx, legacy)
    assert legacy["blockedReason"] == BLOCK_REASON
    assert not dependency_blocked(legacy)
    sent = await send(ctx)
    assert not sent.get("blockedTaskInput")
    await settled(ctx.app, "blocked-handoff", "suppressed")
    assert not ctx.app.runtime.inputs


async def test_cancelled_queued_handoff_stays_cancelled(task_app):
    ctx = task_app
    await block(ctx)
    ctx.target["status"] = "working"
    await send(ctx)
    await agent_action(ctx.app, ctx.source, "coordination.cancel", {
        "sessionId": ctx.source["id"], "requestId": "blocked-handoff",
        "reason": "Dependency handoff withdrawn before admission",
    }, "cancel-blocked-handoff")
    cancelled = copy.deepcopy(ctx.app.collaboration.receipt("blocked-handoff"))
    assert cancelled["delivery"] == "cancelled"
    ctx.target["status"] = "idle"
    await ctx.app.collaboration.drain(ctx.target["id"])
    assert (await send(ctx))["duplicate"]
    assert ctx.app.collaboration.receipt("blocked-handoff") == cancelled
    assert not ctx.app.runtime.inputs and ctx.target["task"]["status"] == "blocked"


async def test_sending_cannot_administer_blocked_peer_task(task_app):
    ctx = task_app
    await block(ctx)
    before = copy.deepcopy(ctx.target["task"])
    with pytest.raises(AppError):
        await agent_action(ctx.app, ctx.source, "task.resume", {
            "sessionId": ctx.target["id"], "expectedRevision": before["revision"],
        }, "forbidden-peer-resume")
    with pytest.raises(AppError):
        await send(ctx, "forged-block-exception", blockedTaskInput=True)
    assert ctx.target["task"] == before and not ctx.app.runtime.inputs
    assert not ctx.app.db.execute("SELECT 1 FROM commands WHERE id=?", ("forged-block-exception",)).fetchone()


async def test_blocked_peer_eligibility_preserves_capacity_denial_and_goal_state(task_app):
    ctx = task_app
    await block(ctx)
    before = copy.deepcopy(ctx.owner.tasks.record())
    policy = copy.deepcopy(ctx.owner.capacity.policy)
    assert await ctx.owner.tasks.peer_input_allowed()
    assert not await ctx.owner.tasks.continuation_allowed()
    denial = {"budgetRevision": policy["revision"], "state": "paused"}
    ctx.owner.capacity.last_denial = copy.deepcopy(denial)
    assert not await ctx.owner.tasks.peer_input_allowed()
    assert not await ctx.owner.tasks.continuation_allowed()
    assert ctx.owner.capacity.last_denial == denial and ctx.owner.capacity.policy == policy
    assert ctx.owner.tasks.record() == before and ctx.owner.coordinator.session_state["goal"] is None


async def test_blocked_handoff_keeps_independent_membership_settings_and_placement(task_app):
    from amplifier_web.preferences import SettingsStore

    ctx = task_app
    await block(ctx)
    settings = SettingsStore(ctx.app.data_dir)
    files = {}
    for row, label in ((ctx.source, "source"), (ctx.target, "recipient")):
        ctx.app.assignments.ensure(row, label + "-assignment")
        row["selection"] = {"instance": label, "model": label + "-model", "effort": "high"}
        settings.update(row["workspace"], "session", lambda _, label=label: {
            "overrides": {"tool-filesystem": {"config": {
                "allowed_write_paths": [label + "-allowed"],
                "denied_write_paths": [label + "-private"],
                "denied_read_paths": [label + "-secrets"],
            }}},
        }, session_id=row["id"])
        permissions = settings.path(row["workspace"], "session", session_id=row["id"])
        config = ctx.app.data_dir / "sessions" / row["id"] / "configuration.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"tools": [], "fixtureOwner": label}), encoding="utf-8")
        files.update({path: path.read_bytes() for path in (permissions, config)})
    keys = ("workspace", "workingDirectory", "executionRevision", "location", "selection",
            "assignmentId", "collaboration", "parentId", "parentSessionId", "task")
    before = [{key: copy.deepcopy(row.get(key)) for key in keys} for row in (ctx.source, ctx.target)]
    memberships = [copy.deepcopy(ctx.app.assignments.current(row)) for row in (ctx.source, ctx.target)]
    assert ctx.source["assignmentId"] != ctx.target["assignmentId"]
    await send(ctx)
    await settled(ctx.app, "blocked-handoff", "accepted")
    assert [{key: row.get(key) for key in keys} for row in (ctx.source, ctx.target)] == before
    assert [ctx.app.assignments.current(row) for row in (ctx.source, ctx.target)] == memberships
    assert all(path.read_bytes() == data for path, data in files.items())


@pytest.mark.parametrize("limit,value", [("maxTotalTokens", 1000), ("maxCostUsd", 1.0)])
async def test_sender_assignment_budget_still_refuses_independent_blocked_queue(task_app, limit, value):
    ctx = task_app
    await block(ctx)
    sender = ctx.app.assignments.ensure(ctx.source, "bounded-sender")
    recipient = ctx.app.assignments.ensure(ctx.target, "independent-recipient")
    sender["limits"][limit] = value
    ctx.app.assignments.save(sender)
    before = copy.deepcopy(recipient)
    with pytest.raises(AppError) as denied:
        await send(ctx, "budgeted-blocked-queue")
    assert denied.value.status == 409
    assert not ctx.app.db.execute("SELECT 1 FROM commands WHERE id=?", ("budgeted-blocked-queue",)).fetchone()
    assert not ctx.target["messages"] and not ctx.app.runtime.inputs
    note = await send(ctx, "budgeted-blocked-note", mode="notify")
    assert note["delivery"] == "notified" and not note.get("blockedTaskInput")
    assert ctx.app.assignments.current(ctx.target) == before and not ctx.app.runtime.inputs
