"""Blocked-root handoffs through real native sessions, with no model service.

Only providers and the process transport are scripted. DirectNativeHost uses
Core/Foundation, loop-live, the Worker app bridge and canonical SessionStore
checkpoints. This is not subprocess ownership, restart or real-model evidence.
"""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip(
    "amplifier_module_loop_live.orchestrator",
    reason="Blocked peer boundary needs the installed native live orchestrator",
)
pytest.importorskip(
    "amplifier_module_context_simple",
    reason="Blocked peer boundary needs the installed native context module",
)

from fixtures.collaboration_native_probe import (
    DirectNativeHost,
    PUBLISH_ROOT,
    ScriptedProvider,
    peer_envelopes,
    tool_output,
)
from amplifier_web import runtime_worker
from amplifier_web.automatic_history import display_message
from amplifier_web.management import Management
from amplifier_web.service import AppService


HUMAN_INPUT = "blocked-peer-human"
REQUESTS = ("blocked-peer-first", "blocked-peer-second", "blocked-peer-third")


def assert_task_retained(worker, before, count, identity=None):
    """Only the charged turn and exact last input may change, even on disk."""
    expected = copy.deepcopy(before)
    if count:
        expected["goalState"]["turns_used"] += count
        expected["lastBlockedInputId"] = identity
    assert worker.controls.tasks.record() == expected
    assert worker.session.coordinator.session_state.get("goal") is None
    saved = json.loads(worker.controls.state_path().read_text())
    assert saved["task"] == expected and saved["goal"] is None


def root_identity(row, worker):
    return {
        "host": {key: copy.deepcopy(row.get(key)) for key in (
            "id", "workspace", "location", "workingDirectory", "executionRevision",
            "bundle", "selection", "assignmentId", "collaboration", "sessionKind",
            "runtimeSessionId", "nativeIdentity", "nativeProject",
            "interruptionRevision", "lastInterruption",
        )},
        "mount": copy.deepcopy(worker.session.coordinator.config),
        "selection": copy.deepcopy(worker.controls.selection),
        "capacity": copy.deepcopy(worker.controls.capacity.policy),
        "workspace": worker.workspace,
    }


async def settled_receipt(app, identity):
    """Read the same committed operation; never retry a send to make it settle."""
    async with asyncio.timeout(30):
        while True:
            receipt = app.collaboration.receipt(identity)
            if receipt["delivery"] not in {"queued", "submitting"}:
                return receipt
            await asyncio.sleep(.01)


async def test_native_dependency_return_reaches_waiting_manager_once(tmp_path, monkeypatch):
    """A real send/subscribe/block cycle receives its sealed native result."""
    class Host(DirectNativeHost):
        async def start(self, session, emit, **kwargs):
            # Both actual native sessions are already mounted by this fixture.
            assert session["id"] in self.roots

        async def control(self, sid, operation, args, *, guard=None):
            worker = self.roots[sid]
            async with worker.command_lock:
                if guard is not None:
                    guard()
                token = PUBLISH_ROOT.set(sid)
                try:
                    return await worker.controls.perform(operation, args)
                finally:
                    PUBLISH_ROOT.reset(token)

    host = Host()
    release_dependency = asyncio.Event()
    request_id = "native-waiting-manager-dependency"

    class Manager(ScriptedProvider):
        continuation = before = worker = None

        async def complete(self, request, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return self.dispatch(request_id, "coordination.send", {
                    "sessionId": target["id"], "mode": "queue",
                    "text": "Finish the dependency and return its evidence.",
                })
            if self.calls == 2:
                assert tool_output(request, request_id)["accepted"]
                return self.dispatch("subscribe-native-dependency", "coordination.subscribe", {
                    "sessionId": source["id"], "requestId": request_id,
                })
            if self.calls == 3:
                result = tool_output(request, "subscribe-native-dependency")["result"]
                assert result["status"] == "waiting"
                self.continuation = result["continuationId"]
                return self.dispatch("block-for-native-dependency", "task.block", {
                    "sessionId": source["id"], "expectedRevision": 1,
                    "reason": "Waiting for the requested dependency result",
                })
            if self.calls == 4:
                assert tool_output(request, "block-for-native-dependency")["accepted"]
                self.before = copy.deepcopy(self.worker.controls.tasks.record())
                assert self.before["status"] == "blocked"
                return self.final("Waiting for the dependency's retained result.")
            assert self.calls == 5, "Duplicate or unrelated manager continuation"
            envelope, _ = peer_envelopes(request)[-1]
            assert envelope["inputId"] == self.continuation
            assert envelope["replyToRequestId"] == request_id
            assert envelope["blockedTaskHandling"]["taskId"] == self.before["id"]
            assert_task_retained(self.worker, self.before, 1, self.continuation)
            assert not await self.worker.controls.tasks.continuation_allowed()
            return self.final("The dependency result arrived; review it before resuming the objective.")

    class Dependency(ScriptedProvider):
        async def complete(self, request, **kwargs):
            self.calls += 1
            if self.calls == 1:
                await release_dependency.wait()
                return self.dispatch("reply-native-dependency", "coordination.reply", {
                    "requestId": request_id, "kind": "result", "outcome": "success",
                    "text": "The requested dependency is ready for review.",
                    "references": ["urn:fixture:native-dependency"],
                })
            assert self.calls == 2
            assert tool_output(request, "reply-native-dependency")["accepted"]
            return self.final("Dependency finished; evidence retained.")

    monkeypatch.setattr(runtime_worker, "publish", host.publish)
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = host.app = AppService(tmp_path / "app", host, workspace=workspace)
    app.management = Management(app)
    source, target = [app._new_session({"title": title}) for title in ("Manager", "Dependency")]
    app.state["sessions"] = [source, target]
    app._publish()
    paths = {}
    for name in ("loop-live", "context-simple"):
        spec = importlib.util.find_spec("amplifier_module_" + name.replace("-", "_"))
        assert spec and spec.origin
        paths[name] = Path(spec.origin).parent
    manager, dependency = Manager(), Dependency()
    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(60):
            worker = manager.worker = await host.mount(source, manager, paths)
            dependency_worker = await host.mount(target, dependency, paths)
            created = await worker.controls.perform("task.create", {
                "commandId": "native-manager-task", "origin": "agent", "expectedRevision": 0,
                "objective": "Review the requested dependency before integration", "maxTurns": 6,
            })
            source["task"] = copy.deepcopy(created["task"])
            app._publish()
            await host.send(source, "Request the dependency, then wait for its result.", HUMAN_INPUT, None)
            await host.outcome(worker, HUMAN_INPUT)
            assert manager.calls == 4
            release_dependency.set()
            await host.outcome(dependency_worker, request_id)
            await host.outcome(worker, manager.continuation)
            receipt = await settled_receipt(app, manager.continuation)
            assert receipt["delivery"] == "accepted"
            assert receipt["dependencyRequestId"] == request_id
            assert receipt["taskContinuationEpoch"] == created["task"]["continuationEpoch"]
            app.collaboration.enqueue_continuation(app.collaboration.receipt(request_id))
            await app.collaboration.drain(source["id"])
            await host.flush()
            assert manager.calls == 5 and dependency.calls == 2
            assert len([r for r in host.admissions if r.get("inputId") == manager.continuation]) == 1
            assert_task_retained(worker, manager.before, 1, manager.continuation)
            assert not host.errors and not host.approvals
    finally:
        release_dependency.set()
        await host.close()
        await app.close()


class BlockedRecipient(ScriptedProvider):
    def __init__(self, source_id, target_id):
        super().__init__()
        self.source_id, self.target_id = source_id, target_id
        self.inputs = []
        self.worker = self.before = None

    async def complete(self, request, **kwargs):
        self.calls += 1
        assert self.calls <= 4, "A refused input or generic goal continuation reached the provider"
        index = (self.calls - 1) // 2
        identity = REQUESTS[index]
        envelope, _ = peer_envelopes(request)[-1]
        assert envelope["inputId"] == envelope["requestId"] == identity
        assert envelope["senderSessionId"] == envelope["senderRuntimeSessionId"] == self.source_id
        assert envelope["recipientSessionId"] == self.target_id
        assert envelope["mode"] == "queue" and envelope["sourceInputIds"] == [HUMAN_INPUT]
        assert envelope["sourceGenerationId"]
        assert "humanResume" not in envelope and "task" not in envelope
        handling = envelope["blockedTaskHandling"]
        assert (handling["taskId"], handling["taskRevision"]) == (
            self.before["id"], self.before["revision"],
        )
        assert_task_retained(self.worker, self.before, index + 1, identity)
        if self.calls % 2:
            self.inputs.append(identity)
            return self.dispatch(f"blocked-reply-{index}", "coordination.reply", {
                "requestId": identity, "kind": "result", "outcome": "success",
                "text": f"Inspected fresh dependency handoff {index + 1}; the objective stays blocked.",
                # Typed success needs an explicit result reference as well as
                # the native terminal anchor. This is a synthetic citation, not
                # independently verified artifact bytes.
                "references": [f"urn:fixture:blocked-handoff:{index + 1}"],
            })
        receipt = tool_output(request, f"blocked-reply-{index}")
        assert receipt["accepted"] and receipt["result"]["status"] == "staged", receipt
        assert receipt["result"]["qualified"] is False
        return self.final(
            f"Fresh handoff {index + 1} inspected.",
            "The dependency is still unresolved; the saved objective remains blocked.",
        )


class PeerSender(ScriptedProvider):
    def __init__(self, host, target, recipient, third_guard):
        super().__init__()
        self.host, self.target, self.recipient = host, target, recipient
        self.third_guard = third_guard
        self.terminals, self.results = [], []
        self.first_receipt = self.refusal = self.saved_denial = None

    def send(self, index):
        return self.dispatch(REQUESTS[index], "coordination.send", {
            "sessionId": self.target["id"], "mode": "queue",
            "text": f"Inspect fresh dependency handoff {index + 1}; do not resume the objective.",
        })

    def read_result(self, index):
        return self.dispatch(f"blocked-result-{index}", "coordination.result", {
            "requestId": REQUESTS[index],
        })

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.send(0)
        if self.calls in {2, 4}:
            index = (self.calls - 2) // 2
            queued = tool_output(request, REQUESTS[index])
            assert queued["accepted"] and queued["delivery"] == "queued", queued
            assert queued["requestId"] == queued["inputId"] == REQUESTS[index]
            # Observe actual delivery, canonical terminal checkpoint and idle;
            # queued acceptance alone is not proof of either reply.
            terminal = await self.host.outcome(self.recipient.worker, REQUESTS[index])
            self.terminals.append(terminal)
            receipt = await settled_receipt(self.host.app, REQUESTS[index])
            assert receipt["delivery"] == "accepted" and receipt["admission"]["accepted"], receipt
            assert receipt["blockedTaskInput"] is True and "humanResume" not in receipt
            assert receipt["taskRevision"] == self.recipient.before["revision"]
            assert "taskContinuationEpoch" not in receipt
            assert_task_retained(self.recipient.worker, self.recipient.before, index + 1, REQUESTS[index])
            assert not await self.recipient.worker.controls.tasks.continuation_allowed()
            if index == 0:
                self.first_receipt = copy.deepcopy(receipt)
            return self.read_result(index)
        if self.calls in {3, 5}:
            index = (self.calls - 3) // 2
            read = tool_output(request, f"blocked-result-{index}")
            assert read["accepted"], read
            result = read["result"]
            declaration = result["receipt"]["response"]
            assert result["qualified"] and declaration["status"] == "sealed", result
            assert declaration["kind"] == "result" and declaration["outcome"] == "success"
            terminal = self.terminals[index]
            assert declaration["terminalMessageId"] == terminal["nativeTerminal"]["messageId"]
            assert declaration["generationId"] == terminal["generation_id"]
            assert declaration["admittedInputIds"] == [REQUESTS[index]]
            self.results.append(copy.deepcopy(result))
            if index == 1 and self.third_guard == "saved_capacity_denial":
                # A pre-existing denial is fixture state, not a fabricated
                # admission outcome. Keep the current policy revision and a
                # spare task turn so this refusal cannot pass via exhaustion.
                capacity = self.recipient.worker.controls.capacity
                self.saved_denial = {
                    "allowed": False, "state": "paused",
                    "budgetRevision": capacity.policy["revision"],
                    "reasons": ["Retained assignment capacity refusal"],
                    "callId": "prior-capacity-denial",
                }
                capacity.last_denial = copy.deepcopy(self.saved_denial)
                self.recipient.worker.controls.persist(task_only=True)
            return self.send(index + 1)
        if self.calls == 6:
            queued = tool_output(request, REQUESTS[2])
            assert queued["accepted"] and queued["delivery"] == "queued", queued
            self.refusal = await settled_receipt(self.host.app, REQUESTS[2])
            assert self.refusal["delivery"] == "suppressed", self.refusal
            admission = self.refusal["admission"]
            assert admission["accepted"] is False
            expected = ("turn budget is exhausted" if self.third_guard == "turn_budget"
                        else "saved task or current budget")
            assert expected in admission["reason"], admission
            assert_task_retained(self.recipient.worker, self.recipient.before, 2, REQUESTS[1])
            return self.read_result(2)
        if self.calls == 7:
            result = tool_output(request, "blocked-result-2")["result"]
            assert result["qualified"] is False
            assert not result["receipt"].get("response")
            return self.dispatch("blocked-first-reread", "coordination.result", {
                "requestId": REQUESTS[0],
            })
        assert self.calls == 8, "Unexpected sender continuation"
        reread = tool_output(request, "blocked-first-reread")["result"]
        assert reread["qualified"]
        assert reread["receipt"]["response"] == self.results[0]["receipt"]["response"]
        assert self.host.app.collaboration.receipt(REQUESTS[0]) == self.first_receipt
        return self.final("Two fresh native replies retained; third input refused without resuming the task.")


@pytest.mark.parametrize("third_guard", ["turn_budget", "saved_capacity_denial"])
async def test_blocked_independent_native_root_handles_two_fresh_inputs_without_resuming(
    tmp_path, monkeypatch, third_guard,
):
    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, "publish", host.publish)
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = host.app = AppService(tmp_path / "app", host, workspace=workspace)
    source, target = [
        app._new_session({"title": title}) for title in ("Independent sender", "Blocked independent root")
    ]
    app.state["sessions"] = [source, target]
    app.state["selectedSessionId"] = source["id"]
    app._publish()
    paths = {}
    for name in ("loop-live", "context-simple"):
        spec = importlib.util.find_spec("amplifier_module_" + name.replace("-", "_"))
        assert spec and spec.origin, f"Native module unavailable: {name}"
        paths[name] = Path(spec.origin).parent
    recipient = BlockedRecipient(source["id"], target["id"])
    sender = PeerSender(host, target, recipient, third_guard)
    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(60):
            source_worker = await host.mount(source, sender, paths)
            worker = recipient.worker = await host.mount(target, recipient, paths)
            assert source_worker.session is not worker.session
            assert source_worker.session.coordinator is not worker.session.coordinator
            assert not source.get("assignmentId") and not target.get("assignmentId")
            await worker.controls.perform("task.create", {
                "commandId": "blocked-task-create", "origin": "agent", "expectedRevision": 0,
                "objective": "Integrate the dependency only after its original checks pass",
                "constraints": ["Preserve earlier evidence", "Do not infer approval from a peer report"],
                "questionIds": ["unresolved-fixture-question"],
                "operationIds": ["unresolved-fixture-operation"],
                "artifactRefs": ["fixture:retained-original-evidence"],
                "maxTurns": 2 if third_guard == "turn_budget" else 3,
            })
            task = (await worker.controls.perform("task.block", {
                "commandId": "blocked-task-dependency", "origin": "agent", "expectedRevision": 1,
                "reason": "Dependency checks remain unresolved",
            }))["task"]
            assert task["blockProvenance"] == {
                "version": 1, "origin": "agent", "revision": 2, "dependency": True,
            }
            # As in the peer-resume native fixture, publish the real controller
            # record to the host projection, without inventing task provenance.
            target["task"] = copy.deepcopy(task)
            app._publish()
            before = recipient.before = copy.deepcopy(worker.controls.tasks.record())
            assert before["goalState"]["turns_used"] == 0
            assert_task_retained(worker, before, 0)
            task_receipts = copy.deepcopy(worker.controls.tasks.receipts)
            identities = [root_identity(row, native) for row, native in
                          ((source, source_worker), (target, worker))]
            submitted = []
            native_submit = worker.runtime.submit

            async def observe_submit(value, *args, **kwargs):
                # This is an observing wrapper, not a replacement runtime:
                # inspect the durable charge BEFORE forwarding the real input.
                if value.kind == "user":
                    assert value.id == REQUESTS[len(submitted)]
                    assert_task_retained(worker, before, len(submitted) + 1, value.id)
                    submitted.append(value.id)
                return await native_submit(value, *args, **kwargs)

            monkeypatch.setattr(worker.runtime, "submit", observe_submit)
            await app.dispatch("conversation.send", {
                "sessionId": source["id"],
                "text": "Inspect two fresh dependency reports without resuming the blocked objective.",
            }, origin="ui", command_id=HUMAN_INPUT, include_state=False)
            source_terminal = await host.outcome(source_worker, HUMAN_INPUT)
            await host.flush()
            assert sender.calls == 8 and recipient.calls == 4
            assert submitted == recipient.inputs == list(REQUESTS[:2])
            assert list(worker.runtime.accepted) == list(REQUESTS[:2])
            assert_task_retained(worker, before, 2, REQUESTS[1])
            assert worker.controls.tasks.receipts == task_receipts  # No task.resume/update.
            assert not await worker.controls.tasks.continuation_allowed()
            assert len(host.admissions) == 3
            assert [row["inputId"] for row in host.admissions] == list(REQUESTS)
            assert [row["accepted"] for row in host.admissions] == [True, True, False]
            assert sender.refusal["blockedTaskInput"] is True
            assert app.collaboration.receipt(REQUESTS[0]) == sender.first_receipt
            if sender.saved_denial:
                assert worker.controls.capacity.last_denial == sender.saved_denial
                saved = json.loads(worker.controls.state_path().read_text())
                assert saved["capacityLastDenial"] == sender.saved_denial
                assert before["maxTurns"] > worker.controls.tasks.record()["goalState"]["turns_used"]
            for row, native, identity in zip((source, target), (source_worker, worker), identities):
                assert root_identity(row, native) == identity
                assert app.assignments.current(row) is None
            assert app.state["selectedSessionId"] == source["id"]
            assert not host.approvals and not host.errors
            assert not [m for m in target["messages"] if m.get("inputOrigin") == "ui"]
            peer_messages = [m for m in target["messages"] if m.get("inputOrigin") == "peer"]
            assert [m["inputId"] for m in peer_messages] == list(REQUESTS)
            assert all("humanResume" not in m["peerEnvelope"] for m in peer_messages)
            native_rows = worker.store.load(target["id"])[0]
            persisted_inputs = [
                (row.get("metadata") or {}).get("amplifier_input", {}).get("id")
                for row in native_rows if row["role"] == "user"
            ]
            assert persisted_inputs == list(REQUESTS[:2])  # Refused input never enters native history.
            for index, terminal in enumerate(sender.terminals):
                anchor = terminal["nativeTerminal"]
                native = display_message(native_rows[anchor["nativeIndex"]], anchor["nativeIndex"], target)
                assert native["id"] == anchor["messageId"] and native["text"] == anchor["nativeText"]
                assert terminal["input_ids"] == [REQUESTS[index]]
            assert sender.terminals[0]["generation_id"] != sender.terminals[1]["generation_id"]
            for native, count in ((source_worker, 1), (worker, 2)):
                assert sum(e["type"] == "generation.started" for e in native.runtime.events) == count
                assert not any(e["type"] in {"generation.failed", "generation.detached", "job.cancel_requested"}
                               for e in native.runtime.events)
            sends = [row for row in host.bridges if row["operation"] == "dispatch"
                     and row["args"].get("action") == "coordination.send"]
            replies = [row for row in host.bridges if row["operation"] == "dispatch"
                       and row["args"].get("action") == "coordination.reply"]
            assert len(sends) == 3 and len(replies) == 2
            for bridge in sends:
                assert bridge["args"]["_runtimeSessionId"] == source["id"]
                assert bridge["args"]["_generationId"] == source_terminal["generation_id"]
                assert [b["inputId"] for b in bridge["args"]["_inputBindings"]] == [HUMAN_INPUT]
                assert "resume" not in bridge["args"]["args"]
            for index, bridge in enumerate(replies):
                assert bridge["args"]["_runtimeSessionId"] == target["id"]
                assert bridge["args"]["_generationId"] == sender.terminals[index]["generation_id"]
                assert [b["inputId"] for b in bridge["args"]["_inputBindings"]] == [REQUESTS[index]]
    finally:
        await app.close()


async def test_blocked_native_overlapping_queue_retains_charge_after_lost_ack(tmp_path, monkeypatch):
    """Real admission/submit; synthetic sender binding, not a runtime restart."""
    import traceback
    from test_task_continuity import controls as detached_controls

    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, "publish", host.publish)
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = host.app = AppService(tmp_path / "app", host, workspace=workspace)
    source, target = [app._new_session({"title": title}) for title in ("Synthetic sender", "Blocked root")]
    app.state["sessions"], app.state["selectedSessionId"] = [source, target], source["id"]
    app._publish()
    paths = {}
    for name in ("loop-live", "context-simple"):
        spec = importlib.util.find_spec("amplifier_module_" + name.replace("-", "_"))
        assert spec and spec.origin, name
        paths[name] = Path(spec.origin).parent
    ack_pending, lose_ack = asyncio.Event(), asyncio.Event()
    provider_entered, release_provider = asyncio.Event(), asyncio.Event()
    submitted, seen, failures = [], [], []

    class GatedRecipient(ScriptedProvider):
        async def complete(self, request, **kwargs):
            try:
                self.calls += 1
                assert self.calls <= 2, "Duplicate, capped input or goal continuation reached provider"
                envelope, _ = peer_envelopes(request)[-1]
                identity = REQUESTS[self.calls - 1]
                assert envelope["inputId"] == envelope["requestId"] == identity
                assert envelope["senderSessionId"] == envelope["senderRuntimeSessionId"] == source["id"]
                assert envelope["sourceGenerationId"] == "synthetic-overlap-generation"
                assert envelope["sourceInputIds"] == [HUMAN_INPUT] and envelope["mode"] == "queue"
                assert envelope["recipientSessionId"] == target["id"] and "humanResume" not in envelope
                assert envelope["blockedTaskHandling"]["taskRevision"] == before["revision"]
                assert_task_retained(worker, before, self.calls, identity)
                seen.append(identity)
                if self.calls == 1:
                    provider_entered.set()
                    await release_provider.wait()
                return self.final(f"Handled {identity}; the objective remains blocked.")
            except Exception:
                failures.append(traceback.format_exc())
                raise

    async def bridge(action, args, identity):
        return await app.app_bridge("dispatch", {
            "action": action, "args": args, "id": identity,
            "_runtimeSessionId": source["id"], "_generationId": "synthetic-overlap-generation",
            "_inputBindings": [{"inputId": HUMAN_INPUT, "clientId": None}],
        }, source["id"])

    async def send(index):
        return await bridge("coordination.send", {
            "sessionId": target["id"], "mode": "queue",
            "text": f"Inspect fresh dependency handoff {index + 1}; keep the objective blocked.",
        }, REQUESTS[index])

    async def assert_restored(count, identity):
        # Fresh real RuntimeControls/TaskController, detached from the live
        # coordinator. Restore only: no worker restart or native input replay.
        restored = detached_controls(target["id"])
        restored.preserve_configuration = True
        state_bytes = worker.controls.state_path().read_bytes()
        try:
            for module, attr in (("orchestrator", "max_iterations"), ("context", "max_tokens")):
                setattr(restored.coordinator.get(module), attr, getattr(worker.controls.coordinator.get(module), attr, None))
            await restored.restore()
            assert restored.tasks.record() == worker.controls.tasks.record()
            assert restored.coordinator.session_state["goal"] is None
            assert not await restored.tasks.continuation_allowed()
            assert worker.controls.state_path().read_bytes() == state_bytes
            assert_task_retained(worker, before, count, identity)
        finally:
            await restored.close()

    recipient = GatedRecipient()
    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(60):
            worker = await host.mount(target, recipient, paths)
            # Only the recipient has a native runtime; the source binding is
            # explicit fixture provenance, validated by the actual app bridge.
            await app.on_runtime_event("runtime.generation", {
                "sessionId": source["id"], "rootSessionId": source["id"],
                "event": "generation.started", "generation_id": "synthetic-overlap-generation",
            })
            await app.on_runtime_event("runtime.status", {
                "sessionId": source["id"], "status": "working",
                "event": "input.delivered", "inputId": HUMAN_INPUT,
            })
            await worker.controls.perform("task.create", {
                "commandId": "overlap-create", "origin": "agent", "expectedRevision": 0,
                "objective": "Wait for independently checked dependencies", "maxTurns": 2,
                "constraints": ["Preserve prior evidence"], "questionIds": ["unresolved-question"],
                "operationIds": ["unresolved-operation"], "artifactRefs": ["fixture:original-evidence"],
            })
            task = (await worker.controls.perform("task.block", {
                "commandId": "overlap-block", "origin": "agent", "expectedRevision": 1,
                "reason": "Dependency checks remain unresolved",
            }))["task"]
            target["task"] = copy.deepcopy(task)
            app._publish()
            before = copy.deepcopy(worker.controls.tasks.record())
            task_receipts = copy.deepcopy(worker.controls.tasks.receipts)
            assert_task_retained(worker, before, 0)
            native_submit = worker.runtime.submit

            async def hold_ack(value, *args, **kwargs):
                try:
                    if value.kind == "user":
                        assert value.id == REQUESTS[len(submitted)]
                        # Real reserve_peer_input must persist BEFORE the real submit.
                        assert_task_retained(worker, before, len(submitted) + 1, value.id)
                        submitted.append(value.id)
                    result = await native_submit(value, *args, **kwargs)
                except Exception:
                    failures.append(traceback.format_exc())
                    raise
                if value.kind == "user" and value.id == REQUESTS[0]:
                    ack_pending.set()
                    await lose_ack.wait()
                    raise RuntimeError("Fixture lost acknowledgement AFTER real native submit")
                return result

            monkeypatch.setattr(worker.runtime, "submit", hold_ack)
            assert (await send(0))["delivery"] == "queued"
            await asyncio.gather(ack_pending.wait(), provider_entered.wait())
            await host.flush()
            assert worker.command_lock.locked()
            assert app.collaboration.receipt(REQUESTS[0])["delivery"] == "submitting"
            assert (await send(1))["delivery"] == "queued"
            duplicates = await asyncio.gather(send(0), send(1), send(0), send(1))
            assert all(row["duplicate"] for row in duplicates), duplicates
            assert [row["delivery"] for row in duplicates] == ["submitting", "queued"] * 2
            await app.collaboration.drain(target["id"])
            assert submitted == seen == [REQUESTS[0]]
            assert list(worker.runtime.accepted) == [REQUESTS[0]]
            assert_task_retained(worker, before, 1, REQUESTS[0])

            lose_ack.set()
            unknown = copy.deepcopy(await settled_receipt(app, REQUESTS[0]))
            assert unknown["delivery"] == "unknown" and "no replay" in unknown["admission"]["reason"], unknown
            assert not release_provider.is_set() and worker.runtime.generation
            await assert_restored(1, REQUESTS[0])
            await app.collaboration.drain(target["id"])  # Busy: cannot charge the queued second input.
            assert app.collaboration.receipt(REQUESTS[1])["delivery"] == "queued"
            assert_task_retained(worker, before, 1, REQUESTS[0])
            release_provider.set()
            first = await host.outcome(worker, REQUESTS[0])  # Actual finished, then idle.
            await app.collaboration.drain(target["id"])
            second_receipt = await settled_receipt(app, REQUESTS[1])
            assert second_receipt["delivery"] == "accepted" and second_receipt["admission"]["accepted"], second_receipt
            second = await host.outcome(worker, REQUESTS[1])
            assert first["generation_id"] != second["generation_id"]
            assert [first["input_ids"], second["input_ids"]] == [[REQUESTS[0]], [REQUESTS[1]]]
            assert (await send(2))["delivery"] == "queued"
            third = await settled_receipt(app, REQUESTS[2])
            assert third["delivery"] == "suppressed" and third["admission"]["accepted"] is False, third
            assert "turn budget is exhausted" in third["admission"]["reason"], third
            duplicates = await asyncio.gather(*(send(i) for i in (0, 1, 2)))
            assert all(row["duplicate"] for row in duplicates), duplicates
            assert [row["delivery"] for row in duplicates] == ["unknown", "accepted", "suppressed"]
            read = await bridge("coordination.result", {"requestId": REQUESTS[0]}, "overlap-read-unknown")
            assert read["accepted"] and not read["result"]["qualified"], read
            assert read["result"]["receipt"] == unknown
            await assert_restored(2, REQUESTS[1])
            await app.collaboration.drain(target["id"])
            await host.flush()
            assert app.collaboration.receipt(REQUESTS[0]) == unknown
            assert submitted == seen == list(worker.runtime.accepted) == list(REQUESTS[:2])
            assert recipient.calls == 2 and not failures and not host.errors and not host.approvals
            assert [row["inputId"] for row in host.admissions] == list(REQUESTS[1:])
            assert [row["accepted"] for row in host.admissions] == [True, False]
            assert_task_retained(worker, before, 2, REQUESTS[1])
            assert worker.controls.tasks.receipts == task_receipts
            native_rows = worker.store.load(target["id"])[0]
            assert [(row.get("metadata") or {}).get("amplifier_input", {}).get("id")
                    for row in native_rows if row["role"] == "user"] == list(REQUESTS[:2])
            for terminal in (first, second):
                anchor = terminal["nativeTerminal"]
                assert display_message(native_rows[anchor["nativeIndex"]], anchor["nativeIndex"], target)["id"] == anchor["messageId"]
            assert sum(e["type"] == "generation.started" for e in worker.runtime.events) == 2
    except BaseException as exc:
        exc.add_note(f"Provider/submit failures: {failures!r}\nAdmissions: {host.admissions!r}\n"
                     f"Submitted: {submitted!r}; seen: {seen!r}\nNative tail: {host.published[-12:]!r}")
        raise
    finally:
        lose_ack.set()
        release_provider.set()
        await app.close()
