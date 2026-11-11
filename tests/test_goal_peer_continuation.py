"""Offline service/Worker/Core/live+streaming goal/peer ordering discriminator.

Provider replies and goal evaluation are scripted. The fallback scenario
advertises an unavailable steering capability through the coordinator; the real
delivery adapter must decline before submit and later use the real idle boundary.
The native probe supplies serial transport in place of a worker process, not
fake generation events, queue admission, terminal sealing, or execute().
"""
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("amplifier_module_loop_live.orchestrator")
pytest.importorskip("amplifier_module_loop_streaming")
pytest.importorskip("amplifier_module_context_simple")

from fixtures.collaboration_native_probe import (
    DirectNativeHost, ScriptedProvider, message_text, peer_envelopes, tool_output,
)
from amplifier_operations.coordination import peer_input
from amplifier_web import runtime_worker
from amplifier_web.management import Management
from amplifier_web.service import AppService
from amplifier_web.service import AppError
from amplifier_foundation.session.activation import ActivationGate


HUMAN = "ordering-human-input"
REASON = "Continue the synthetic objective after checking the exact peer result."


def last_input(request):
    # The provider sees user-role reference messages as well as actual inputs.
    # Streaming a876e6a writes a separate ephemeral, framed tail reminder after
    # steering. Exclude only its producer metadata AND complete envelope; never
    # discard an admitted input, arbitrary ephemeral row, or lookalike text.
    for message in reversed(request.messages):
        if message.role != "user":
            continue
        metadata = message.metadata or {}
        if "amplifier_input" not in metadata:
            if metadata.get("coordinationObservation") is True:
                continue
            text = message_text(message)
            if (metadata.get("ephemeral") is True
                    and metadata.get("reminder_placement") in {"pre_user", "tail"}
                    and text.startswith("<system-reminders>\n")
                    and text.endswith("\n</system-reminders>")):
                continue
        return message
    raise AssertionError("Provider request has no non-reference user input")


class OrderingHost(DirectNativeHost):
    async def mount(self, row, provider, paths):
        # Use the actual worker activation fence. The direct transport does not
        # replace shared-file ownership/process testing.
        gate = ActivationGate()
        gate.activate()
        worker = await super().mount(row, provider, paths)
        worker.activation_gate = gate
        return worker

    async def start(self, session, emit):
        # Already mounted through PreparedBundle in this transport fixture.
        assert session["id"] in self.roots

    async def control(self, sid, operation, args):
        return await self.roots[sid].controls.perform(operation, args)

    async def settle(self):
        """Observe transport and service-owned work; never force admission."""
        async with asyncio.timeout(5):
            while True:
                await self.flush()
                if not self.app.tasks:
                    return
                await asyncio.gather(*list(self.app.tasks))


def unavailable_request_boundary(worker):
    """Expose the supported 'unavailable' capability mode to the real adapter.

    This models only capability absence, not a native-provider implementation.
    Keep the native submit callable intact: no admission response or event is
    forged, and an idle input still traverses deliver() and runtime.submit().
    """
    coordinator = worker.session.coordinator
    capability = coordinator.get_capability("live.steering")
    assert capability["version"] == 1 and capability["mode"] == "request_boundary"
    assert callable(capability["submit"])
    coordinator.register_capability("live.steering", {**capability, "mode": "unavailable"})


def assert_waiting_unsubmitted(host, worker, identity):
    receipt = host.app.collaboration.receipt(identity)
    assert receipt["mode"] == "auto" and receipt["delivery"] == "queued"
    attempts = [a for a in host.admissions if a["inputId"] == identity]
    assert attempts
    for attempt in attempts:
        assert attempt["automaticDelivery"] is True
        assert attempt["accepted"] is False
        assert attempt["effect"] == "none" and attempt["waitingForBoundary"] is True
    assert not host.app.collaboration.guard(receipt)
    assert identity not in worker.runtime.accepted
    assert not any(e.get("input_id") == identity for e in worker.runtime.events)
    return receipt


class Parent(ScriptedProvider):
    def __init__(self, host, target):
        super().__init__()
        self.host, self.target = host, target
        self.waiting, self.release = asyncio.Event(), asyncio.Event()
        self.next_request, self.release_next = asyncio.Event(), asyncio.Event()
        self.request_id = self.continuation_id = None
        self.boundaries = []

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.dispatch("ordering-send", "coordination.send", {
                "sessionId": self.target, "mode": "queue",
                "text": "Return the synthetic result reference.",
            })
        if self.calls == 2:
            receipt = tool_output(request, "ordering-send")
            assert receipt["accepted"] and receipt["delivery"] == "queued", receipt
            self.request_id = receipt["requestId"]
            return self.dispatch("ordering-subscribe", "coordination.subscribe", {
                "sessionId": self.host.parent_id, "requestId": self.request_id,
            })
        if self.calls == 3:
            receipt = tool_output(request, "ordering-subscribe")
            assert receipt["accepted"] and receipt["result"]["status"] == "waiting"
            self.continuation_id = receipt["result"]["continuationId"]
            self.waiting.set()
            await asyncio.wait_for(self.release.wait(), 15)
            return self.final("First parent answer; the qualified peer result is queued.")
        last_user = last_input(request)
        text = message_text(last_user)
        envelopes = peer_envelopes(request)
        peer = next((e for e, t in envelopes
                     if e["inputId"] == self.continuation_id and t == text), None)
        worker = self.host.roots[self.host.parent_id]
        if peer:
            saved = next(m for m in self.host.app._session(self.host.parent_id)["messages"]
                         if m.get("inputId") == self.continuation_id and m.get("inputOrigin") == "peer")
            assert peer == saved["peerEnvelope"]
            assert text == peer_input(saved["peerEnvelope"], saved["text"])
        self.boundaries.append({
            "kind": "peer" if peer else "goal" if text == REASON else "unexpected",
            "generationId": worker.runtime.generation["id"],
            "inputIds": list(worker.runtime.generation["input_ids"]),
            "goal": copy.deepcopy(worker.session.coordinator.session_state.get("goal")),
            "text": text,
            "selectedMetadata": copy.deepcopy(last_user.metadata),
            "peerCandidates": [{"envelope": e, "text": t} for e, t in envelopes
                               if e["inputId"] == self.continuation_id],
            "userRows": [{"metadata": copy.deepcopy(m.metadata),
                          "textStart": message_text(m)[:160], "textLength": len(message_text(m))}
                         for m in request.messages if m.role == "user"],
        })
        if len(self.boundaries) == 1:
            self.next_request.set()
            await asyncio.wait_for(self.release_next.wait(), 15)
        assert peer or text == REASON, text
        return self.final("Peer processed." if peer else "Synthetic goal work continued.")


class Peer(ScriptedProvider):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()
        self.request_id = None

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            envelope, _ = peer_envelopes(request)[0]
            self.request_id = envelope["inputId"]
            await asyncio.wait_for(self.release.wait(), 15)
            return self.dispatch("ordering-reply", "coordination.reply", {
                "requestId": self.request_id, "kind": "result", "outcome": "success",
                "text": "Synthetic result is ready.",
                "references": ["artifact:synthetic-result"],
            })
        assert self.calls == 2
        receipt = tool_output(request, "ordering-reply")
        assert receipt["accepted"] and receipt["result"]["status"] == "staged"
        return self.final("Synthetic result ready for independent verification.")


@pytest.mark.parametrize("goal_on", [False, True], ids=["no-goal", "active-goal"])
async def test_qualified_peer_precedes_automatic_goal_turn(tmp_path, monkeypatch, goal_on):
    # Keep the publisher's exact JUnit identities. Each case must check both
    # supported busy delivery and the genuinely unsubmitted idle fallback.
    for boundary in ("supported", "unavailable"):
        directory = tmp_path / boundary
        directory.mkdir()
        await run_ordering(directory, monkeypatch, goal_on, boundary)


async def run_ordering(tmp_path, monkeypatch, goal_on, boundary):
    host = OrderingHost()
    monkeypatch.setattr(runtime_worker, "publish", host.publish)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    app = host.app = AppService(tmp_path / "app", host, workspace=workspace)
    app.management = Management(app)
    app.state["sessions"] = [
        app._new_session({"title": title, "bundle": "synthetic-ordering"})
        for title in ("Ordering parent", "Ordering peer")
    ]
    source, target = app.state["sessions"]
    host.parent_id = source["id"]
    app._publish()
    parent, peer = Parent(host, target["id"]), Peer()
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    host.pump = asyncio.create_task(host.route_events())
    trace = {"goalOn": goal_on, "boundary": boundary,
             "oracle": "eligible exact peer input before generic goal prompt"}
    try:
        async with asyncio.timeout(45):
            worker = await host.mount(source, parent, paths)
            peer_worker = await host.mount(target, peer, paths)
            if boundary == "unavailable":
                unavailable_request_boundary(worker)
            evaluations = []

            async def evaluate(*args):
                evaluations.append(copy.deepcopy(worker.session.coordinator.session_state["goal"]))
                # Three finite turns, far below the cap. The very first next
                # provider request discriminates ordering, not timeout/cap exit.
                return len(evaluations) >= 3, REASON

            loop = worker.session.coordinator.get("orchestrator")
            loop._evaluate_goal = evaluate
            if goal_on:
                result = await app.dispatch("task.create", {
                    "sessionId": source["id"], "expectedRevision": 0,
                    "objective": "Check the synthetic peer result, then continue.", "maxTurns": 8,
                }, origin="ui", command_id="ordering-task", include_state=False)
                assert result["accepted"]
                # Seed retained progress before execution, not native lifecycle.
                # Subsequent accounting must preserve it rather than restart at 0.
                worker.session.coordinator.session_state["goal"].update(
                    turns_used=2, continuations=1,
                    reasons=["Retained prior reason."], last_reason="Retained prior reason.",
                    extra_progress={"retain": ["unknown fields"]})
                worker.controls.persist(task_only=True)
            task_before = copy.deepcopy(worker.controls.tasks.record())
            epoch_before = source.get("interruptionRevision", 0)
            policy_before = copy.deepcopy(worker.controls.capacity.policy)
            await app.dispatch("conversation.send", {
                "sessionId": source["id"], "text": "Coordinate the synthetic peer.",
            }, origin="ui", command_id=HUMAN, include_state=False)
            await asyncio.wait_for(parent.waiting.wait(), 15)
            original_generation = worker.runtime.generation["id"]
            goal = worker.session.coordinator.session_state.get("goal")
            before_goal = copy.deepcopy(goal)
            trace["goalBeforeReturn"] = before_goal
            peer.release.set()
            await host.outcome(peer_worker, parent.request_id)
            await host.settle()
            receipt = app.collaboration.receipt(parent.request_id)
            assert receipt["response"]["qualified"] and receipt["response"]["status"] == "sealed"
            if boundary == "unavailable":
                delivery = assert_waiting_unsubmitted(host, worker, parent.continuation_id)
                ready = worker.session.coordinator.get_capability("web.peer_continuation_ready")
                assert await ready() is True
            else:
                delivery = app.collaboration.receipt(parent.continuation_id)
                assert delivery["mode"] == "auto" and delivery["delivery"] == "accepted"
                assert delivery["admissionMode"] == "steer"
                assert delivery["targetGenerationId"] == original_generation
                command = worker.runtime.accepted[parent.continuation_id]
                assert command.kind == "steer" and command.target_generation_id == original_generation
                accepted = [e for e in worker.runtime.events if e["type"] == "steering.accepted"
                            and e["input_id"] == parent.continuation_id]
                assert len(accepted) == 1
                assert accepted[0]["target_generation_id"] == original_generation
                assert not any(e["type"] == "steering.applied" and e["input_id"] == parent.continuation_id
                               for e in worker.runtime.events)
            assert worker.runtime.generation["id"] == original_generation
            assert worker.session.coordinator.session_state.get("goal") == before_goal
            assert evaluations == []
            trace["beforeRelease"] = copy.deepcopy(delivery)
            trace["sealed"] = copy.deepcopy(receipt)
            parent.release.set()
            await asyncio.wait_for(parent.next_request.wait(), 15)
            trace["firstNextRequest"] = copy.deepcopy(parent.boundaries[0])
            # Preserve the discriminator before cleanup lets later turns finish.
            trace["orderingPassed"] = parent.boundaries[0]["kind"] == "peer"
            assert trace["orderingPassed"], (
                "Generic goal prompt reached the provider before the exact peer input",
                trace["firstNextRequest"],
            )
            if boundary == "supported":
                assert parent.boundaries[0]["generationId"] == original_generation
                assert parent.boundaries[0]["inputIds"] == [HUMAN, parent.continuation_id]
                assert parent.boundaries[0]["goal"] == before_goal
                assert worker.session.coordinator.session_state.get("goal") is goal
                assert evaluations == []
            else:
                assert parent.boundaries[0]["generationId"] != original_generation
                assert parent.boundaries[0]["inputIds"] == [parent.continuation_id]
                if goal_on:
                    preserved = parent.boundaries[0]["goal"]
                    assert preserved["turns_used"] == before_goal["turns_used"] + 1
                    assert preserved["continuations"] == before_goal["continuations"]
                    assert preserved["cap"] == before_goal["cap"] == 8
                    assert preserved["reasons"] == before_goal["reasons"] + [REASON]
                    assert preserved["extra_progress"] == before_goal["extra_progress"]
                    assert len(evaluations) == 1
                else:
                    assert parent.boundaries[0]["goal"] is None and evaluations == []
            parent.release_next.set()
            first = await host.outcome(worker, HUMAN)
            final = await host.outcome(worker, parent.continuation_id)
            await host.settle()
            admissions_before = copy.deepcopy(host.admissions)
            receipt_before = copy.deepcopy(app.collaboration.receipt(parent.continuation_id))
            await app.collaboration.drain(source["id"])
            app.collaboration.start()  # Restart only the observation/drain path.
            await host.settle()
            assert host.admissions == admissions_before
            assert app.collaboration.receipt(parent.continuation_id) == receipt_before
            attempts = [a for a in host.admissions if a["inputId"] == parent.continuation_id]
            assert sum(a["accepted"] is True for a in attempts) == 1
            for attempt in attempts:
                if not attempt["accepted"]:
                    assert attempt["effect"] == "none" and attempt["waitingForBoundary"] is True
            if boundary == "supported":
                assert len(attempts) == 1
                assert final["generation_id"] == original_generation == first["generation_id"]
                assert final["input_ids"] == [HUMAN, parent.continuation_id]
                assert receipt_before["delivery"] == "applied"
            else:
                assert len(attempts) >= 2  # No-effect wait(s), then exactly one real submit.
                assert final["generation_id"] != original_generation == first["generation_id"]
                assert final["input_ids"] == [parent.continuation_id]
                assert receipt_before["delivery"] == "accepted"
                assert receipt_before["admissionMode"] == "queue"
            current = worker.controls.tasks.record()
            for key in ("id", "revision", "status", "objective", "maxTurns", "continuationEpoch"):
                assert (current or {}).get(key) == (task_before or {}).get(key)
            assert source.get("interruptionRevision", 0) == epoch_before
            assert worker.controls.capacity.policy == policy_before
            native = worker.runtime.events
            finished = next(e for e in native if e["type"] == "generation.finished"
                            and e["generation_id"] == original_generation)
            starts = [e for e in native if e["type"] == "generation.started"]
            assert [e["initial_input_id"] for e in starts] == (
                [HUMAN] if boundary == "supported" else [HUMAN, parent.continuation_id])
            assert sum(e["type"] == "input.accepted" and e.get("input_id") == parent.continuation_id
                       for e in native) == 1
            delivered = [e for e in native if e["type"] == "input.delivered"
                         and e["input_id"] == parent.continuation_id]
            assert len(delivered) == 1
            applied = [e for e in native if e["type"] == "steering.applied"
                       and e["input_id"] == parent.continuation_id]
            if boundary == "supported":
                assert len(applied) == 1 and applied[0]["target_generation_id"] == original_generation
                assert delivered[0]["sequence"] <= applied[0]["sequence"] < finished["sequence"]
                assert not any(e["type"] == "session.idle"
                               and starts[0]["sequence"] < e["sequence"] < applied[0]["sequence"]
                               for e in native)
            else:
                assert applied == []
                idle = next(e for e in native if e["type"] == "session.idle"
                            and e["sequence"] > finished["sequence"])
                assert finished["sequence"] < idle["sequence"] < starts[1]["sequence"] < delivered[0]["sequence"]
            canonical = worker.store.load(worker.session.session_id)[0]
            persisted = [(m.get("metadata") or {}).get("amplifier_input", {}).get("id")
                         for m in canonical if m.get("role") == "user"]
            assert persisted.count(HUMAN) == persisted.count(parent.continuation_id) == 1
            trace.update(boundaries=parent.boundaries, evaluations=evaluations,
                         finalReceipt=app.collaboration.receipt(parent.continuation_id))
            if goal_on:
                assert len(evaluations) == 3
                assert [g["turns_used"] for g in evaluations] == [3, 4, 5]
                assert [g["continuations"] for g in evaluations] == (
                    [1, 2, 3] if boundary == "supported" else [1, 1, 2])
                assert [g["reasons"] for g in evaluations] == [
                    before_goal["reasons"] + [REASON] * index for index in range(3)]
                assert all(g["cap"] == 8 and g["extra_progress"] == before_goal["extra_progress"]
                           for g in evaluations)
                assert [b["kind"] for b in parent.boundaries] == (
                    ["peer", "goal", "goal"] if boundary == "supported" else ["peer", "goal"])
                assert worker.session.coordinator.session_state["goal"] is None
            else:
                assert evaluations == [] and [b["kind"] for b in parent.boundaries] == ["peer"]
    finally:
        parent.release.set()
        parent.release_next.set()
        peer.release.set()
        trace["events"] = host.published
        trace["admissions"] = host.admissions
        trace["bridges"] = host.bridges
        evidence = os.environ.get("GOAL_PEER_EVIDENCE")
        if evidence:
            path = Path(evidence)
            path.mkdir(parents=True, exist_ok=True)
            (path / f"ordering-{boundary}-{'goal' if goal_on else 'control'}.json").write_text(
                json.dumps(trace, indent=2, default=str) + "\n")
        await host.close()
        await app.close()


@pytest.fixture
async def native_wait(tmp_path, monkeypatch):
    """Real queued/sealed native result, held before the parent's final answer.

    The tests below inspect eligibility and stress its read/await fences; only
    the ordering test above qualifies the actual outer execution/idle boundary.
    """
    host = OrderingHost()
    monkeypatch.setattr(runtime_worker, "publish", host.publish)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = host.app = AppService(tmp_path / "app", host, workspace=workspace)
    app.management = Management(app)
    source, target = [app._new_session({"title": title}) for title in ("Parent", "Peer")]
    app.state["sessions"] = [source, target]
    host.parent_id = source["id"]
    app._publish()
    parent, peer = Parent(host, target["id"]), Peer()
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    host.pump = asyncio.create_task(host.route_events())
    try:
        worker = await host.mount(source, parent, paths)
        peer_worker = await host.mount(target, peer, paths)
        unavailable_request_boundary(worker)
        async def satisfied(*args):
            return True, "Synthetic cleanup evaluation."
        worker.session.coordinator.get("orchestrator")._evaluate_goal = satisfied
        await app.dispatch("task.create", {
            "sessionId": source["id"], "expectedRevision": 0,
            "objective": "Verify the synthetic result.", "maxTurns": 8,
        }, origin="ui", command_id="saved-task", include_state=False)
        await app.dispatch("conversation.send", {
            "sessionId": source["id"], "text": "Coordinate the synthetic peer.",
        }, origin="ui", command_id=HUMAN, include_state=False)
        await asyncio.wait_for(parent.waiting.wait(), 15)
        peer.release.set()
        await host.outcome(peer_worker, parent.request_id)
        await host.settle()
        assert_waiting_unsubmitted(host, worker, parent.continuation_id)
        coordinator = worker.session.coordinator
        ready = coordinator.get_capability("web.peer_continuation_ready")
        # Every negative starts from a known-positive, settled native wait.
        # Otherwise a suppressed/actively submitting fixture could pass vacuously.
        before_rows = app.db.execute("SELECT id,receipt FROM commands ORDER BY rowid").fetchall()
        changes = app.db.total_changes
        assert await ready() is True
        assert app.db.execute("SELECT id,receipt FROM commands ORDER BY rowid").fetchall() == before_rows
        assert app.db.total_changes == changes
        yield SimpleNamespace(host=host, app=app, source=source, target=target,
            worker=worker, parent=parent, peer=peer, coordinator=coordinator,
            ready=ready)
    finally:
        parent.release.set()
        parent.release_next.set()
        peer.release.set()
        await host.close()
        await app.close()


def saved_receipts(f):
    return f.app.db.execute("SELECT id,receipt FROM commands ORDER BY rowid").fetchall()


async def test_eligibility_is_read_only_and_preserves_exact_goal_state(native_wait):
    f = native_wait
    goal = f.coordinator.session_state["goal"]
    goal.update(extra_progress={"retain": ["unknown fields"]},
                escalated=True, recovery_pending={"before": [{"turn": 0}]})
    before_goal, before_rows = copy.deepcopy(goal), saved_receipts(f)
    changes = f.app.db.total_changes
    for _ in range(3):
        assert await f.ready() is True
    assert saved_receipts(f) == before_rows and f.app.db.total_changes == changes
    assert f.parent.continuation_id not in f.worker.runtime.accepted
    assert f.coordinator.session_state["goal"] is goal
    await f.worker.controls.tasks.yield_to_peer("orchestrator:goal_progress", {
        "state": "continuing", "condition": goal["condition"], "turn": goal["turns_used"]})
    replacement = f.coordinator.session_state["goal"]
    assert replacement is not goal and replacement == before_goal
    assert f.worker.controls.tasks.record()["status"] == "active"
    assert saved_receipts(f) == before_rows
    assert f.parent.continuation_id not in f.worker.runtime.accepted
    saved = json.loads(f.worker.controls.state_path().read_text())
    assert saved["goal"] == before_goal  # Persisted progress, not only memory.


@pytest.mark.parametrize("change", [
    "paused", "blocked", "completed", "revised", "stop", "owner",
    "unknown", "submitting", "notify", "unqualified", "ordinary", "foreign",
])
async def test_ineligible_receipts_do_not_gain_yield_authority(native_wait, change):
    f = native_wait
    if change in {"paused", "blocked", "completed", "revised"}:
        action = {"paused": "pause", "blocked": "block", "completed": "complete", "revised": "update"}[change]
        args = {"sessionId": f.source["id"], "expectedRevision": 1}
        if change == "blocked":
            args["reason"] = "Synthetic dependency block."
        elif change == "completed":
            args.update(condition=f.worker.controls.tasks.record()["objective"], evidence=["synthetic-check"])
        elif change == "revised":
            args["objective"] = "Revised synthetic objective."
        await f.app.dispatch("task." + action, args, origin="ui", command_id=change, include_state=False)
    elif change == "stop":
        # The actual host Stop fence, without changing busy/idle or replaying.
        f.source["interruptionRevision"] = f.source.get("interruptionRevision", 0) + 1
    elif change == "owner":
        f.source["ownership"] = {"status": "yielded"}
    elif change == "foreign":
        f.target["workspace"] += "/other"
    elif change == "unqualified":
        receipt = f.app.collaboration.receipt(f.parent.request_id)
        receipt["response"]["qualified"] = False
        f.app.collaboration.save(f.parent.request_id, receipt)
    else:
        receipt = f.app.collaboration.receipt(f.parent.continuation_id)
        if change in {"unknown", "submitting"}:
            receipt["delivery"] = change
        elif change == "notify":
            receipt["mode"] = "notify"
        else:
            receipt.pop("dependencyRequestId")
        f.app.collaboration.save(f.parent.continuation_id, receipt)
    await f.host.settle()
    before, changes = saved_receipts(f), f.app.db.total_changes
    if change == "stop":
        # A stale transport-bound generation is rejected by the host before
        # eligibility is evaluated. The goal hook must preserve its state.
        with pytest.raises(AppError, match="crossed Stop") as rejected:
            await f.ready()
        assert rejected.value.status == 403
        goal = f.coordinator.session_state["goal"]
        before_goal = copy.deepcopy(goal)
        await f.worker.controls.tasks.yield_to_peer("orchestrator:goal_progress", {
            "state": "continuing", "condition": goal["condition"], "turn": goal["turns_used"]})
        assert f.coordinator.session_state["goal"] is goal
        assert goal == before_goal
    else:
        assert await f.ready() is False
    assert saved_receipts(f) == before and f.app.db.total_changes == changes
    assert f.parent.continuation_id not in f.worker.runtime.accepted


@pytest.mark.parametrize("binding", [
    {"_runtimeSessionId": None}, {"_runtimeSessionId": "foreign-native-root"},
    {"_generationId": None}, {"_generationId": "stale-generation"},
])
async def test_host_readiness_requires_actual_root_and_generation(native_wait, binding):
    f = native_wait
    task = f.worker.controls.tasks.record()
    args = {"taskId": task["id"], "taskRevision": task["revision"],
            "_runtimeSessionId": f.source["id"],
            "_generationId": f.worker.runtime.generation["id"], **binding}
    before = saved_receipts(f)
    with pytest.raises(AppError):
        await f.app.app_bridge("coordination.continuation_ready", args, f.source["id"])
    assert saved_receipts(f) == before


@pytest.mark.parametrize("change", [
    "pause", "revision", "goal-replaced", "progress", "cap", "capacity",
    "stop", "activation", "error",
])
async def test_yield_rechecks_after_awaited_real_bridge(native_wait, change):
    f = native_wait
    original = f.worker.bridge
    reached, release = asyncio.Event(), asyncio.Event()
    async def delayed(operation, args):
        result = await original(operation, args)
        if operation == "coordination.continuation_ready":
            assert result["ready"] is True
            reached.set()
            await asyncio.wait_for(release.wait(), 5)
            if change == "error":
                raise RuntimeError("Synthetic lost read response")
        return result
    f.worker.bridge = delayed
    goal = f.coordinator.session_state["goal"]
    data = {"state": "continuing", "condition": goal["condition"], "turn": goal["turns_used"]}
    pending = asyncio.create_task(f.worker.controls.tasks.yield_to_peer("orchestrator:goal_progress", data))
    try:
        await asyncio.wait_for(reached.wait(), 5)
        if change in {"pause", "revision"}:
            await f.worker.controls.perform("task." + ("pause" if change == "pause" else "update"), {
                "commandId": "during-read", "expectedRevision": 1,
                **({"objective": "New synthetic objective"} if change == "revision" else {})})
        elif change == "goal-replaced":
            f.coordinator.session_state["goal"] = copy.deepcopy(goal)
        elif change == "progress":
            goal["extra_progress"] = "concurrent progress must survive"
        elif change == "cap":
            goal["turns_used"] = goal["cap"]
        elif change == "capacity":
            f.worker.controls.capacity.last_denial = {
                "budgetRevision": f.worker.controls.capacity.policy["revision"]}
        elif change == "stop":
            f.worker.stop_revision += 1
        elif change == "activation":
            f.worker.activation_gate.release(f.worker.activation_gate.current())
        expected = f.coordinator.session_state["goal"]
        state = copy.deepcopy(expected)
        before = saved_receipts(f)
        release.set()
        await asyncio.wait_for(pending, 5)
        assert f.coordinator.session_state["goal"] is expected
        assert f.coordinator.session_state["goal"] == state
        assert saved_receipts(f) == before
        assert f.parent.continuation_id not in f.worker.runtime.accepted
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("denial", ["cap", "capacity", "terminal-event", "foreign-event"])
async def test_goal_denials_do_not_probe_or_yield(native_wait, denial):
    f = native_wait
    goal = f.coordinator.session_state["goal"]
    if denial == "cap":
        goal["turns_used"] = goal["cap"]
    if denial == "capacity":
        f.worker.controls.capacity.last_denial = {
            "budgetRevision": f.worker.controls.capacity.policy["revision"]}
    data = {"state": "achieved" if denial == "terminal-event" else "continuing",
            "condition": goal["condition"], "turn": goal["turns_used"]}
    if denial == "foreign-event":
        data["session_id"] = "foreign-root"
    bridges, before = len(f.host.bridges), copy.deepcopy(goal)
    await f.worker.controls.tasks.yield_to_peer("orchestrator:goal_progress", data)
    assert f.coordinator.session_state["goal"] is goal and goal == before
    assert len(f.host.bridges) == bridges


async def test_irrelevant_queues_cannot_hide_qualified_continuation(native_wait):
    f = native_wait
    valid = f.app.collaboration.receipt(f.parent.continuation_id)
    # Retain the real sealed continuation after a full page of stale queues.
    # Only synthetic test receipt order changes; no runtime lifecycle is forged.
    f.app.db.execute("UPDATE commands SET rowid=rowid+1000 WHERE id=?", (valid["requestId"],))
    for index in range(40):
        stale = {**valid, "requestId": f"stale-{index}", "inputId": f"stale-{index}",
                 "taskRevision": -1}
        f.app.collaboration.insert(stale["requestId"], str(index), stale)
        f.app.db.execute("UPDATE commands SET rowid=? WHERE id=?", (100 + index, stale["requestId"]))
    before, changes = saved_receipts(f), f.app.db.total_changes
    assert await f.ready()
    assert saved_receipts(f) == before and f.app.db.total_changes == changes
    assert f.app.collaboration.pending_continuation(f.source["id"])["inputId"] == valid["inputId"]
    f.parent.release.set()
    f.parent.release_next.set()
    await f.host.outcome(f.worker, HUMAN)
    await f.host.outcome(f.worker, valid["inputId"])
    await f.host.settle()
    assert f.app.collaboration.receipt(valid["inputId"])["delivery"] == "accepted"
    attempts = [a for a in f.host.admissions if a["sessionId"] == f.source["id"]]
    assert {a["inputId"] for a in attempts} == {valid["inputId"]}
    assert sum(a["accepted"] is True for a in attempts) == 1
    for attempt in attempts:
        if not attempt["accepted"]:
            assert attempt["effect"] == "none" and attempt["waitingForBoundary"] is True


async def test_host_capacity_denial_is_read_only(native_wait):
    f = native_wait
    capacity = f.worker.controls.capacity
    capacity.policy.update(enabled=True, maxTotalTokens=1)
    capacity.last_denial = {"budgetRevision": capacity.policy["revision"], "reasons": ["Synthetic budget exhausted"]}
    f.worker.controls.persist(task_only=True)
    before, changes = saved_receipts(f), f.app.db.total_changes
    assert await f.ready() is False
    assert saved_receipts(f) == before and f.app.db.total_changes == changes


async def test_finite_generic_goal_without_peer_capability_is_unchanged():
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
    from amplifier_web.runtime_controls import RuntimeControls

    plan = {"session": {
        "orchestrator": {"module": "loop-live", "config": {"use_streaming": False}},
        "context": {"module": "context-simple"},
    }}
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    prepared = PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name="synthetic-finite"))
    session = await prepared.create_session(session_id="synthetic-finite-goal")
    controls = RuntimeControls(session, SimpleNamespace(generation=None))
    seen, evaluations = [], []
    class FiniteProvider(ScriptedProvider):
        async def complete(self, request, **kwargs):
            seen.append(message_text(last_input(request)))
            return self.final("Synthetic finite answer")
    provider = FiniteProvider()
    await session.coordinator.mount("providers", provider, name=provider.name)
    async def evaluate(*args):
        evaluations.append(True)
        return len(evaluations) == 2, REASON
    session.coordinator.get("orchestrator")._evaluate_goal = evaluate
    try:
        await controls.perform("task.create", {
            "commandId": "finite-task", "expectedRevision": 0,
            "objective": "Synthetic finite objective", "maxTurns": 4})
        async with asyncio.timeout(10):
            assert await session.execute("Explicit synthetic input") == "Synthetic finite answer"
        assert seen == ["Explicit synthetic input", REASON]
        assert len(evaluations) == 2
        assert session.coordinator.get_capability("web.peer_continuation_ready") is None
        assert controls.tasks.record()["status"] == "active"
    finally:
        await controls.close()
        await session.cleanup()
