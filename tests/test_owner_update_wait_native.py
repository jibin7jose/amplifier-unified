"""Offline owner update wait through AppService, Worker, Core/live + streaming.

Only provider replies, goal evaluation and replacement/listener identity are
scripted. The tool result, native goal-yield hook, checkpoint/park, readiness
confirmation and continuation admission are real. No updater loop, installer,
listener, subprocess restart or paid/network provider runs. DirectNativeHost's
serial transport is not full worker-process/restart qualification.

The same test on a predecessor must fail (normally at the unavailable public
wait action); do not replace the missing action or manufacture a yielded row to
make that baseline proceed.
"""
import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("amplifier_module_loop_live.orchestrator")
pytest.importorskip("amplifier_module_loop_streaming")
pytest.importorskip("amplifier_module_context_simple")
shared = pytest.importorskip("amplifier_foundation.session.shared_state")

from fixtures.collaboration_native_probe import (
    DirectNativeHost, PUBLISH_ROOT, ScriptedProvider, message_text,
    peer_envelopes, tool_output,
)
from test_goal_peer_continuation import OrderingHost, last_input
from amplifier_web import __version__, runtime_worker, update_drain
from amplifier_web.auth import data_identity
from amplifier_web.handoff_boundary import inspect as inspect_boundary
from amplifier_web.management import Management
from amplifier_web.service import AppService
from amplifier_web.shared_state import ActivationGate
from amplifier_web.update_restart import target
from amplifier_web.updates import UpdateManager
from amplifier_module_loop_live.runtime import Input


HUMAN = "owner-update-native-human"
WAIT = "owner-update-native-wait"
OBJECTIVE = "Finish the retained report without repeating the completed upload."
REASON = "The retained report needs its next authorized step."
RELEASE = {"version": __version__, "revision": "b" * 40,
           "attemptId": "a" * 32, "generation": "c" * 32}


class OwnerUpdateHost(OrderingHost):
    def __init__(self):
        super().__init__()
        self.workers = {}
        self.failures = []
        self.failed = asyncio.Event()

    async def mount(self, row, provider, paths):
        # Unlike OrderingHost's post-mount gate, this test needs activation
        # installed BEFORE execute captures context, for real park/reacquisition.
        return await DirectNativeHost.mount(self, row, provider, paths)

    async def before_execute(self, worker):
        sid = worker.runtime.session_id
        # RuntimeManager owns this identity independently of shared-store
        # acquisitions. Reacquiring a parked writer does not replace the process.
        owner = self.workers[sid] = {"runtimeOwnerId": "native-probe-owner:" + sid}
        transport = worker.bridge

        async def owned_bridge(operation, args):
            try:
                assert self.workers.get(sid) is owner, "Native transport owner changed"
                # Stamp at transport, after Worker.app_access_bridge has stamped
                # the real native generation and stripped model-supplied fields.
                return await transport(operation, {
                    **args, "_runtimeOwnerId": owner["runtimeOwnerId"],
                })
            except Exception as exc:
                self.fail("bridge " + operation, exc)
                raise

        worker.bridge = owned_bridge
        worker.activation_gate = ActivationGate()
        worker.activation = worker.activation_gate.activate()
        worker.runtime.capture_activation = worker.activation_gate.current
        coordinator = worker.session.coordinator
        coordinator.register_capability("live.activation", worker.activation_gate)
        worker.shared_store = shared.SharedSessionStore(
            worker.workspace, worker.runtime.session_id, root=self.app.data_dir / "shared")
        worker.shared_store_stamp = shared.file_stamp
        worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified", fixture=True)
        # Same controls-before-transcript contract as the production checkpoint.
        checkpoint = coordinator.get_capability("live.checkpoint")

        async def retained_checkpoint(*args, **kwargs):
            worker.controls.persist(task_only=True)
            return await checkpoint(*args, **kwargs)

        coordinator.register_capability("live.checkpoint", retained_checkpoint)

    async def send(self, session, text, input_id, emit):
        worker = self.roots[session["id"]]
        try:
            async with worker.command_lock:
                worker.controls.require_idle()
                worker.context_bindings[input_id] = session.get("surfaceInputs", {}).get(input_id, {})
                token = worker.activation_gate.bind(worker.activation)
                try:
                    # Match Worker.handle(send): do not depend on the service
                    # task inheriting the mount coroutine's ContextVar token.
                    identity = await worker.runtime.submit(Input(
                        "user", text, id=input_id, activation=worker.activation))
                finally:
                    worker.activation_gate.reset(token)
            return {"accepted": True, "inputId": identity, "completed": False}
        except Exception as exc:
            self.fail("native send " + input_id, exc)
            raise

    async def control(self, sid, operation, args, *, guard=None):
        worker = self.roots[sid]
        async with worker.command_lock:
            if guard:
                guard()
            return await worker.controls.perform(operation, args)

    async def handoff_boundary(self, sid, boundary_id, cancel=False, *, update=False, receipt=None):
        expected = (receipt or {}).get("updateWait")
        if expected and self.workers[sid]["runtimeOwnerId"] != expected["owner"]:
            raise ValueError("The requesting runtime owner changed before its update boundary.")
        return await inspect_boundary(
            self.roots[sid], boundary_id, cancel, update=update, expected=expected)

    def fail(self, stage, error):
        self.failures.append(f"{stage}: {type(error).__name__}: {error}")
        self.failed.set()

    def publish(self, event):
        super().publish(event)
        if event.get("type") in {"generation.failed", "generation.detached", "session.failed", "runtime.error"}:
            self.fail("native event", RuntimeError(repr(event)))

    def diagnostic(self, worker, stage):
        events = [{key: event[key] for key in (
            "type", "sequence", "input_id", "input_ids", "generation_id",
            "error", "error_type", "error_category", "error_stage",
        ) if key in event} for event in worker.runtime.events[-8:]]
        source = self.app._session(worker.runtime.session_id)
        return (f"{stage}; failures={self.failures!r}; routing={self.errors!r}; "
                f"nativeEvents={events!r}; generation={worker.runtime.generation!r}; "
                f"hostStatus={source.get('status')!r}; "
                f"delivery={[(m.get('inputId'), m.get('delivery')) for m in source['messages'] if m.get('role') == 'user']!r}")

    async def wait_stage(self, worker, awaitable, stage, timeout=10):
        """Race positive evidence against real execution/transport failure."""
        observed = asyncio.ensure_future(awaitable)
        failed = asyncio.create_task(self.failed.wait())
        try:
            done, _ = await asyncio.wait(
                {observed, failed, worker.execution}, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED)
            if self.failed.is_set():
                raise AssertionError(self.diagnostic(worker, stage))
            if worker.execution in done:
                try:
                    worker.execution.result()
                except BaseException as exc:
                    raise AssertionError(self.diagnostic(worker, stage)) from exc
                raise AssertionError("Native execution exited before " + self.diagnostic(worker, stage))
            if observed not in done:
                raise AssertionError("Timed out awaiting " + self.diagnostic(worker, stage))
            try:
                return observed.result()
            except Exception as exc:
                raise AssertionError(self.diagnostic(worker, stage)) from exc
        finally:
            # Never cancel/retry the worker just to end an observation.
            for task in (observed, failed):
                if not task.done():
                    task.cancel()
            await asyncio.gather(observed, failed, return_exceptions=True)

    async def settle(self):
        async with asyncio.timeout(5):
            while True:
                await self.flush()
                tasks = list(self.app.tasks)
                if not tasks:
                    return
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for task, result in zip(tasks, results):
                    # Publication coalescing cancels its old flush task normally.
                    # Do not swallow cancellation/failure of domain work.
                    if task.cancelled() and task.get_coro().__name__ == "_flush_progress":
                        continue
                    if isinstance(result, BaseException):
                        raise AssertionError(f"Service settlement failed: {result!r}") from result

    async def checkpoint_and_park(self, worker):
        token = PUBLISH_ROOT.set(worker.runtime.session_id)
        try:
            await worker.park(activation=worker.activation)
        finally:
            PUBLISH_ROOT.reset(token)
        await self.flush()
        assert worker.parked and worker.shared_handle is None

    async def automatic_input(self, session, args, guard, steering):
        worker = self.roots[session["id"]]
        root = PUBLISH_ROOT.set(session["id"])
        try:
            await worker.acquire_for_mutation()
            token = worker.activation_gate.bind(worker.activation)
            try:
                return await super().automatic_input(session, args, guard, steering)
            finally:
                worker.activation_gate.reset(token)
        finally:
            PUBLISH_ROOT.reset(root)

    async def close(self):
        try:
            await super().close()
        finally:
            for worker in self.roots.values():
                if worker.shared_handle:
                    worker.shared_handle.release()
                    worker.shared_handle = None


class Owner(ScriptedProvider):
    def __init__(self, host, sid, wait_args):
        super().__init__()
        self.host, self.sid, self.wait_args = host, sid, wait_args
        self.wait_receipt = self.continuation_id = self.resumed = None
        self.continuing, self.release = asyncio.Event(), asyncio.Event()

    async def complete(self, request, **kwargs):
        try:
            return await self.script(request)
        except Exception as exc:
            self.host.fail(f"scripted provider call {self.calls}", exc)
            raise

    async def script(self, request):
        self.calls += 1
        worker = self.host.roots[self.sid]
        if self.calls == 1:
            assert worker.runtime.generation["input_ids"] == [HUMAN]
            assert worker.controls.tasks.record()["status"] == "active"
            return self.dispatch(WAIT, "task.waitForUpdate", self.wait_args)
        if self.calls == 2:
            self.wait_receipt = tool_output(request, WAIT)
            assert self.wait_receipt["accepted"], self.wait_receipt
            result = self.wait_receipt["result"]
            assert result["status"] == "waiting"
            assert result["boundary"]["ready"] is False
            assert "generation" in result["boundary"]["waitingFor"]
            return self.final("Saved the exact update wait; finishing this turn.")
        assert self.calls == 3, "Unexpected automatic goal turn or replay"
        current = last_input(request)
        envelopes = peer_envelopes(request)
        assert envelopes, "Automatic goal turn ran before the exact update return"
        envelope, text = envelopes[-1]
        assert text == message_text(current), "Generic goal prompt preceded the update return"
        request_id = "update-wait:" + RELEASE["attemptId"] + ":" + self.sid
        assert envelope["replyToRequestId"] == request_id
        self.continuation_id = envelope["inputId"]
        assert worker.runtime.generation["input_ids"] == [self.continuation_id]
        assert "Continue the existing task from its saved progress." in text
        self.resumed = {
            "generationId": worker.runtime.generation["id"],
            "task": copy.deepcopy(worker.controls.tasks.record()),
            "goal": copy.deepcopy(worker.session.coordinator.session_state["goal"]),
        }
        self.continuing.set()
        await asyncio.wait_for(self.release.wait(), 15)
        return self.final("Continued the retained report without repeating the upload.")


async def test_owner_update_wait_yields_then_continues_once_on_exact_healthy_adoption(tmp_path, monkeypatch):
    host = OwnerUpdateHost()
    monkeypatch.setattr(runtime_worker, "publish", host.publish)
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = host.app = AppService(tmp_path / "app", host, workspace=workspace)
    app.management = Management(app)
    manager = app.update_manager = UpdateManager(app)  # Never start its updater loop.
    manager.running_identity = {
        "version": __version__, "revision": "d" * 40, "instanceId": "fixture-old-process"}
    source = app._new_session({"title": "Owner native update wait", "bundle": "synthetic-update-wait"})
    app.state["sessions"] = [source]
    sid = source["id"]
    manager.diagnostics.begin("application", RELEASE["revision"], RELEASE["attemptId"])
    app.state["updates"].update(
        phase="app-staged", pendingApp=copy.deepcopy(RELEASE),
        pendingRestart=None, pendingReplacement=None)
    app.state["updates"]["application"]["canInstall"] = True
    wait_target = target(manager)
    assert wait_target is not None
    app._publish()
    provider = Owner(host, sid, {"sessionId": sid, "expectedRevision": 1, "target": wait_target})
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(45):
            worker = await host.mount(source, provider, paths)
            evaluations = []

            async def evaluate(*args):
                evaluations.append(copy.deepcopy(worker.session.coordinator.session_state["goal"]))
                # The first answer MUST request more work, not hit the cap or
                # complete the objective. Only the real wait hook can yield it.
                return len(evaluations) == 2, REASON

            worker.session.coordinator.get("orchestrator")._evaluate_goal = evaluate
            created = await app.dispatch("task.create", {
                "sessionId": sid, "expectedRevision": 0, "objective": OBJECTIVE,
                "maxTurns": 8, "constraints": ["Preserve the original files and used budget."],
                "artifactRefs": ["completed-upload-receipt"],
            }, origin="ui", command_id="owner-update-native-task", include_state=False)
            assert created["accepted"]
            # Seed previous task progress, never generation/boundary evidence.
            goal = worker.session.coordinator.session_state["goal"]
            goal.update(turns_used=2, continuations=1, reasons=["Retained prior reason."],
                        last_reason="Retained prior reason.", extra_progress={"retain": ["opaque fields"]})
            worker.controls.persist(task_only=True)
            task_before = copy.deepcopy(worker.controls.tasks.record())
            policy_before = copy.deepcopy(worker.controls.capacity.policy)
            epoch_before = source.get("interruptionRevision", 0)
            sent = await app.dispatch("conversation.send", {
                "sessionId": sid, "text": "Continue the retained report; wait for the exact staged update.",
            }, origin="ui", command_id=HUMAN, include_state=False)
            assert sent["accepted"], sent
            await host.wait_stage(worker, host.delivered(sid, HUMAN), "initial native input delivery")
            first = await host.wait_stage(
                worker, host.outcome(worker, HUMAN), "initial generation finished and idle")
            await host.settle()
            assert provider.calls == 2 and len(evaluations) == 1
            assert evaluations[0]["turns_used"] == 3
            assert worker.runtime.generation is None
            yielded_goal = copy.deepcopy(worker.session.coordinator.session_state["goal"])
            assert yielded_goal["turns_used"] == 3 and yielded_goal["continuations"] == 1
            assert yielded_goal["cap"] == 8
            assert yielded_goal["reasons"] == task_before["goalState"]["reasons"] + [REASON]
            assert yielded_goal["extra_progress"] == task_before["goalState"]["extra_progress"]
            marker = worker._handoff_boundary
            assert marker["yieldedGoal"] == yielded_goal  # Produced by the actual native hook.
            boundary_id = provider.wait_receipt["result"]["boundaryId"]
            assert marker["id"] == boundary_id
            enrolled = app.collaboration.receipt(WAIT)
            assert enrolled["sourceInputIds"] == [HUMAN]
            assert enrolled["sourceGenerationId"] == first["generation_id"]
            assert enrolled["sourceInterruptionRevision"] == app._session(sid).get("interruptionRevision", 0)
            assert worker.controls.tasks.record()["status"] == "active"

            # Native idle alone must not count as checkpoint/release proof.
            assert not await update_drain.prepare(manager, RELEASE)
            row = app.state["updates"]["drain"]["workers"][sid]
            assert not row["ready"] and "checkpoint" in row["waitingFor"]
            old_handle = worker.shared_handle
            await host.checkpoint_and_park(worker)
            assert not old_handle.active
            assert await update_drain.prepare(manager, RELEASE)
            assert row["ready"] and not row["waitingFor"]
            assert row["yieldedGoal"] == {key: yielded_goal[key]
                                          for key in ("task_id", "cap", "turns_used", "continuations")}
            saved = json.loads(worker.controls.state_path().read_text())
            assert saved["goal"] == yielded_goal
            assert saved["task"]["id"] == task_before["id"]
            assert provider.calls == 2 and len(evaluations) == 1
            assert [e["initial_input_id"] for e in worker.runtime.events
                    if e["type"] == "generation.started"] == [HUMAN]

            # Synthetic same-target host adoption: use real readiness validation,
            # never installer/restart commands or a fabricated applicationResult.
            update_drain.begin_replacement(manager, RELEASE)
            restart = {**RELEASE, "sourceInstanceId": manager.running_identity["instanceId"], "requestedAt": 1.0}
            app.state["updates"].update(phase="activating", pendingRestart=copy.deepcopy(restart))
            app.instance_id = "fixture-adopted-host"
            manager.running_identity = {
                "version": __version__, "revision": RELEASE["revision"], "instanceId": "fixture-new-process"}
            assert await manager.confirm_readiness({
                "ok": True, "app": "amplifier-unified", "dataIdentity": data_identity(app.data_dir),
                **manager.running_identity,
            }, expected=restart)
            result = app.state["updates"]["applicationResult"]
            assert result["phase"] == "restart-ack" and result["attemptId"] == RELEASE["attemptId"]
            assert result["revision"] == RELEASE["revision"]
            assert not any(app.state["updates"].get(key)
                           for key in ("pendingApp", "pendingRestart", "pendingReplacement"))
            assert provider.calls == 2  # Health acknowledgement alone submits nothing.
            await update_drain.finish(manager, "installed")
            await host.wait_stage(worker, provider.continuing.wait(), "post-update provider continuation")
            assert provider.resumed["generationId"] != first["generation_id"]
            assert provider.resumed["goal"] == yielded_goal
            for key in ("id", "revision", "status", "objective", "maxTurns", "constraints",
                        "artifactRefs", "continuationEpoch"):
                assert provider.resumed["task"].get(key) == task_before.get(key)
            assert worker.controls.capacity.policy == policy_before
            assert source.get("interruptionRevision", 0) == epoch_before
            provider.release.set()
            final = await host.wait_stage(
                worker, host.outcome(worker, provider.continuation_id),
                "continued generation finished and idle")
            await host.settle()
            assert final["input_ids"] == [provider.continuation_id]
            assert [g["turns_used"] for g in evaluations] == [3, 4]
            assert all(g["continuations"] == 1 and g["cap"] == 8 for g in evaluations)
            assert worker.controls.tasks.record()["goalState"]["turns_used"] == 4
            saved = json.loads(worker.controls.state_path().read_text())
            assert saved["task"]["id"] == task_before["id"]
            assert saved["task"]["goalState"]["turns_used"] == 4

            admissions = copy.deepcopy(host.admissions)
            receipt = copy.deepcopy(app.collaboration.receipt(provider.continuation_id))
            # Reobserve the saved release and queued work, not the original input.
            await update_drain.finish(manager, "installed")
            app.collaboration.start()
            await app.collaboration.drain(sid)
            await host.settle()
            assert host.admissions == admissions
            assert app.collaboration.receipt(provider.continuation_id) == receipt
            assert sum(a["accepted"] is True and a["inputId"] == provider.continuation_id
                       for a in admissions) == 1
            assert provider.calls == 3 and provider.tool_calls == [WAIT]
            dispatched = [b for b in host.bridges if b["operation"] == "dispatch"
                          and b["args"].get("action") == "task.waitForUpdate"]
            assert len(dispatched) == 1 and dispatched[0]["args"]["id"] == WAIT
            binding = dispatched[0]["args"]
            assert binding["_runtimeOwnerId"] == host.workers[sid]["runtimeOwnerId"]
            assert binding["_runtimeSessionId"] == sid
            assert binding["_generationId"] == first["generation_id"]
            assert [entry["inputId"] for entry in binding["_inputBindings"]] == [HUMAN]
            starts = [e for e in worker.runtime.events if e["type"] == "generation.started"]
            assert [e["initial_input_id"] for e in starts] == [HUMAN, provider.continuation_id]
            for event_type in ("input.accepted", "input.delivered"):
                inputs = [e["input_id"] for e in worker.runtime.events if e["type"] == event_type]
                assert inputs == [HUMAN, provider.continuation_id]
            idle = next(e for e in worker.runtime.events
                        if e["type"] == "session.idle" and e["sequence"] > first["sequence"])
            assert first["sequence"] < idle["sequence"] < starts[1]["sequence"]
            canonical = worker.store.load(worker.session.session_id)[0]
            inputs = [(m.get("metadata") or {}).get("amplifier_input", {}).get("id")
                      for m in canonical if m.get("role") == "user"]
            assert inputs.count(HUMAN) == inputs.count(provider.continuation_id) == 1
            assert not host.errors and not host.approvals
    finally:
        provider.release.set()
        failure = sys.exception()
        try:
            await app.close()
        except Exception as cleanup_error:
            if failure is None:
                raise
            # Keep the stage/cause as the primary failure instead of replacing
            # it with DirectNativeHost's generic failed-execution cleanup error.
            failure.add_note(f"Native fixture cleanup also failed: {cleanup_error!r}")


@pytest.mark.parametrize("boundary", ["accepted", "delegation", "goal"])
async def test_same_generation_human_steering_supersedes_update_wait(tmp_path, monkeypatch, boundary):
    """Fresh applied input, not a different generation/task, must invalidate yield."""
    from amplifier_core import HookResult
    from amplifier_web.conversation_steering import submit

    host = OwnerUpdateHost()
    monkeypatch.setattr(runtime_worker, "publish", host.publish)
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    app = host.app = AppService(tmp_path / "app", host, workspace=tmp_path)
    app.management = Management(app)
    manager = app.update_manager = UpdateManager(app)
    source = app._new_session({"title": "Steered update owner", "bundle": "synthetic-update-wait"})
    app.state["sessions"] = [source]
    sid = source["id"]
    manager.diagnostics.begin("application", RELEASE["revision"], RELEASE["attemptId"])
    app.state["updates"].update(phase="app-staged", pendingApp=copy.deepcopy(RELEASE))
    app.state["updates"]["application"]["canInstall"] = True
    app._publish()
    enrolled, apply_steering, applied, finish_answer = (asyncio.Event() for _ in range(4))
    steering_id = "new-human-steering"
    correction = "Continue current work now instead of waiting for the update."

    class SteeredOwner(Owner):
        async def script(self, request):
            self.calls += 1
            if self.calls == 1:
                return self.dispatch(WAIT, "task.waitForUpdate", self.wait_args)
            if self.calls == 2:
                assert tool_output(request, WAIT)["accepted"]
                enrolled.set()
                await apply_steering.wait()
                # Keep this generation through the next request boundary, where
                # queued steering is genuinely applied by streaming.
                return self.dispatch("read-current-task", "task.get", {"sessionId": sid})
            if self.calls == 3:
                assert any(correction in message_text(m) for m in request.messages)
                applied.set()
                await finish_answer.wait()
                return self.final("Following the fresh human instruction.")
            assert self.calls == 4, "Unexpected input replay or extra goal turn"
            return self.final("The existing objective continued without another input.")

    provider = SteeredOwner(host, sid, {
        "sessionId": sid, "expectedRevision": 1, "target": target(manager)})
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(30):
            worker = await host.mount(source, provider, paths)
            coordinator = worker.session.coordinator
            await app.dispatch("task.create", {"sessionId": sid, "expectedRevision": 0,
                "objective": OBJECTIVE, "maxTurns": 8}, command_id="steered-task", include_state=False)
            coordinator.session_state["goal"].update(turns_used=2, continuations=1)
            worker.controls.persist(task_only=True)
            original_task = copy.deepcopy(worker.controls.tasks.record())
            evaluations, goal_identity = [], []

            async def evaluate(*args):
                evaluations.append(copy.deepcopy(coordinator.session_state["goal"]))
                return len(evaluations) == 2, REASON

            async def before_yield(event, data):
                if data.get("state") == "continuing":
                    goal_identity.append(coordinator.session_state["goal"])
                return HookResult()

            async def after_yield(event, data):
                if data.get("state") == "continuing":
                    assert coordinator.session_state["goal"] is goal_identity[-1], (
                        "Obsolete update marker replaced the fresh-steered goal")
                return HookResult()

            coordinator.get("orchestrator")._evaluate_goal = evaluate
            coordinator.hooks.register("orchestrator:goal_progress", before_yield,
                name="fixture-before-update-yield", priority=-1)
            coordinator.hooks.register("orchestrator:goal_progress", after_yield,
                name="fixture-after-update-yield", priority=1)
            await app.dispatch("conversation.send", {"sessionId": sid, "text": "Do the work"},
                command_id=HUMAN, include_state=False)
            await host.wait_stage(worker, enrolled.wait(), "owner wait enrolled")
            generation = worker.runtime.generation["id"]
            marker = worker._handoff_boundary
            token = PUBLISH_ROOT.set(sid)
            try:
                result = await submit(worker.controls, worker.runtime,
                    {"text": correction, "inputId": steering_id, "targetGenerationId": generation},
                    worker.activation, lambda: worker.stop_revision)
            finally:
                PUBLISH_ROOT.reset(token)
            assert result["accepted"], result
            if boundary == "accepted":
                assert steering_id not in worker.runtime.generation["input_ids"]
                outcome = await coordinator.hooks.emit("tool:pre", {
                    "session_id": sid, "tool_name": "delegate", "tool_call_id": "accepted-steer-child"})
                assert outcome.action != "deny", "Accepted fresh steering still held delegation before application"
            apply_steering.set()
            await host.wait_stage(worker, applied.wait(), "fresh steering applied in original generation")
            assert worker.runtime.generation["id"] == generation
            assert steering_id in worker.runtime.generation["input_ids"]
            assert worker.controls.tasks.record()["revision"] == original_task["revision"]
            assert worker.controls.tasks.record()["continuationEpoch"] == original_task["continuationEpoch"]
            assert worker._handoff_boundary is marker
            # No observe_waiters/cancel call before either decision. Native fresh
            # input evidence must make the obsolete marker inert on its own.
            if boundary == "delegation":
                outcome = await coordinator.hooks.emit("tool:pre", {
                    "session_id": sid, "tool_name": "delegate", "tool_call_id": "fresh-child"})
                assert outcome.action != "deny", "Obsolete update freeze denied fresh-steered delegation"
            finish_answer.set()
            await host.wait_stage(worker, host.outcome(worker, HUMAN),
                                  "fresh-steered objective continued without an update wake")
            await host.settle()
            assert provider.calls == 4 and len(evaluations) == 2
            assert [g["turns_used"] for g in evaluations] == [3, 4]
            assert "yieldedGoal" not in marker
            assert not host.admissions
            assert len([e for e in worker.runtime.events if e["type"] == "steering.applied"
                        and e["input_id"] == steering_id]) == 1
            assert len([e for e in worker.runtime.events if e["type"] == "generation.started"]) == 1
    finally:
        apply_steering.set()
        finish_answer.set()
        failure = sys.exception()
        try:
            await app.close()
        except Exception as cleanup_error:
            if failure is None:
                raise
            failure.add_note(f"Cleanup also failed: {cleanup_error!r}")
