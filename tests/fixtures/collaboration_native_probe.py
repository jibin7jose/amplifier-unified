"""Bounded, offline collaboration acceptance in the caller-owned warm DTU.

Run with the DTU's installed runtime Python:
    python tests/fixtures/collaboration_native_probe.py

Two independent PreparedBundle/Core 2 sessions use installed loop-live bdd76
and context-simple. Only the providers are scripted; no grants or coordination
approval decisions are created. AppService owns provenance, committed receipts,
asynchronous admission, replies and subscriptions;
Worker owns bridge stamping and terminal correlation over SessionStore's actual
canonical return. A serial event pump replaces JSON-lines process transport,
NOT the native runtime. No result IDs or lifecycle outcomes are synthesized.

The synthetic artifact is written/checked by fixture Python in its temporary
workspace, not by a model filesystem tool. This proves native composition, not
real-model efficacy, full Worker process/ownership, restart or compaction.
Those gates remain caller-owned; the fixture never tears down the DTU.
"""
from __future__ import annotations

import asyncio
import copy
from contextvars import ContextVar
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
from amplifier_core.models import ProviderInfo
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.app_guidance import install_app_access
from amplifier_web.automatic_history import display_message
from amplifier_web.collaboration_input import admit, checkpoint_anchors, deliver, steer
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime import normalize_event
from amplifier_web.runtime_controls import RuntimeControls
import amplifier_web.runtime_worker as worker_module
from amplifier_web.service import AppService
from amplifier_web.session_files import project_slug

HUMAN_INPUT = "native-probe-human"
CORRECTION = "Keep the scripted artifact plain text; include correction-marker."
PUBLISH_ROOT = ContextVar("native_probe_publish_root")
LIMITS = [
    "Scripted provider, not real-model/tool efficacy; zero network/model calls.",
    "Direct sessions/event pump, not full Worker process or ownership acceptance.",
    "Restart/compaction and negative active-job/accepted-only gates remain caller-owned.",
    "Task creation/inheritance, repeated artifact rounds, notify and negative admission gates are not exercised.",
    "Unsupported-steering refusals, non-coordination approvals and old-worker compatibility are not exercised.",
]


def installed_runtime():
    """Fail loud on an unqualified environment, without installing anything."""
    result = {}
    for name in ("amplifier-core", "amplifier-module-loop-live",
                 "amplifier-module-context-simple"):
        distribution = metadata.distribution(name)
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        result[name] = {
            "version": distribution.version,
            "commit": direct.get("vcs_info", {}).get("commit_id"),
        }
    assert result["amplifier-core"]["version"].split(".")[0] == "2", result
    commit = result["amplifier-module-loop-live"]["commit"]
    if not commit or not commit.startswith("bdd76"):
        raise RuntimeError(
            "Installed loop-live bdd76 provenance required in direct_url.json; "
            "use the caller-qualified warm DTU Python, not a newly resolved runtime."
        )
    return result


def message_text(message):
    if isinstance(message.content, str):
        return message.content
    return "".join(block.text for block in message.content or []
                   if getattr(block, "type", None) == "text")


def tool_output(request, identity):
    """Consume the actual app_control result from the native provider request."""
    row = next((message for message in reversed(request.messages)
                if message.role == "tool" and message.tool_call_id == identity), None)
    assert row is not None, f"Missing native tool result: {identity}"
    value = json.loads(message_text(row))
    # Some engines serialize ToolResult, others its output. Both are typed
    # receipts; never infer success from response prose.
    if isinstance(value, dict) and "success" in value and "output" in value:
        assert value["success"] is True, value
        value = value["output"]
    return value


def peer_envelopes(request):
    values = []
    for message in request.messages:
        text = message_text(message)
        if message.role == "user" and text.startswith("Host-attributed peer task input."):
            envelope = json.loads(text.split("\n", 2)[1])
            values.append((envelope, text))
    return values


class ScriptedProvider:
    name = "scripted-collaboration"

    def __init__(self):
        self.calls = 0
        self.tool_calls = []

    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Scripted collaboration fixture",
                            capabilities=["tools"], defaults={"model": "offline-script"})

    async def list_models(self):
        return []

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    def tool(self, identity, operation, args):
        self.tool_calls.append(identity)
        return ChatResponse(content=[], tool_calls=[
            ToolCall(id=identity, name="app_control",
                     arguments={"operation": operation, "args": args})
        ], usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))

    def dispatch(self, identity, action, args):
        return self.tool(identity, "dispatch", {"action": action, "args": args, "id": identity})

    @staticmethod
    def final(*blocks):
        return ChatResponse(content=[TextBlock(text=text) for text in blocks],
                            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))


class Sender(ScriptedProvider):
    def __init__(self, sid, recipient, blocked, artifact, host):
        super().__init__()
        self.sid, self.recipient = sid, recipient
        self.blocked, self.artifact, self.host = blocked, artifact, host
        self.request_id = self.continuation_id = None
        self.source_id = self.terminal_id = self.correction_id = None
        self.native_delivery = None
        self.verified_hash = None

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            assert any("Coordinate one checked scripted artifact" in message_text(m)
                       for m in request.messages if m.role == "user")
            return self.tool("sender-state", "get_state", {})
        if self.calls == 2:
            state = tool_output(request, "sender-state")
            assert state["session"]["id"] == self.sid
            human = next(row for row in state["session"]["recentMessages"]
                         if row.get("inputId") == HUMAN_INPUT and row.get("inputOrigin") == "ui")
            assert not human.get("hostAction") and not human.get("peerEnvelope")
            self.source_id = human["id"]
            return self.dispatch("sender-context", "coordination.context", {
                "sessionId": self.sid,
            })
        if self.calls == 3:
            receipt = tool_output(request, "sender-context")
            assert receipt["accepted"], receipt
            context = receipt["result"]
            assert context["grants"] == [] and context["proposals"] == [], context
            assert context["requests"] == [] and context["continuation"]["supported"], context
            return self.dispatch("sender-request", "coordination.send", {
                "sessionId": self.recipient,
                "mode": "queue", "text": "Produce a concrete checked scripted artifact.",
            })
        if self.calls == 4:
            receipt = tool_output(request, "sender-request")
            # Queue success is a committed receipt, not synchronous admission.
            assert receipt["accepted"] and receipt["delivery"] == "queued", receipt
            assert "grantId" not in receipt and receipt["protocol"] == 2
            assert receipt["senderSessionId"] == self.sid
            assert receipt["target"]["sessionId"] == self.recipient
            assert receipt["sourceInputIds"] == [HUMAN_INPUT]
            self.request_id = receipt["requestId"]
            assert receipt["inputId"] == self.request_id
            # Synchronize on the actual native input event, never a fabricated
            # acknowledgement or a replay of the committed request.
            self.native_delivery = await self.host.delivered(self.recipient, self.request_id)
            return self.dispatch("sender-subscribe", "coordination.subscribe", {
                "sessionId": self.sid, "requestId": self.request_id,
            })
        if self.calls == 5:
            receipt = tool_output(request, "sender-subscribe")
            assert receipt["accepted"] and receipt["result"]["status"] == "waiting", receipt
            self.continuation_id = receipt["result"]["continuationId"]
            await asyncio.wait_for(self.blocked.wait(), 30)
            return self.dispatch("sender-correction", "coordination.send", {
                "sessionId": self.recipient,
                "mode": "steer", "text": CORRECTION,
            })
        if self.calls == 6:
            receipt = tool_output(request, "sender-correction")
            assert receipt["delivery"] in {"accepted", "applied"}, receipt
            assert receipt["admission"]["accepted"] and not receipt["admission"]["completed"]
            self.correction_id = receipt["requestId"]
            return self.final("Request-specific wait retained. ", "Awaiting the native peer result.")
        if self.calls == 7:
            envelope, text = next((e, t) for e, t in peer_envelopes(request)
                                  if e["inputId"] == self.continuation_id)
            assert envelope["replyToRequestId"] == self.request_id
            assert envelope["senderSessionId"] == self.recipient
            assert envelope["recipientSessionId"] == self.sid
            assert envelope["requestId"] == self.continuation_id
            assert "grantId" not in envelope and "Independently check" in text
            return self.dispatch("sender-result", "coordination.result", {
                "requestId": self.request_id,
            })
        if self.calls == 8:
            result = tool_output(request, "sender-result")["result"]
            declaration = result["receipt"]["response"]
            assert result["qualified"] and declaration["status"] == "sealed", result
            assert declaration["independentArtifactVerification"] is False
            self.terminal_id = declaration["terminalMessageId"]
            self.native_terminal = declaration["nativeTerminal"]
            assert self.artifact.as_uri() in declaration["references"]
            return self.dispatch("sender-read", "coordination.read", {
                "sessionId": self.recipient, "limit": 50, "textLimit": 4000,
            })
        if self.calls == 9:
            result = tool_output(request, "sender-read")["result"]
            assert result["next_offset"] is None, result
            row = next(row for row in result["messages"] if row["message_id"] == self.terminal_id)
            assert row["role"] == "assistant" and row["text"] == self.native_terminal["nativeText"]
            assert row["next_text_offset"] is None
            data = self.artifact.read_bytes()
            assert data == b"Scripted artifact (not model filesystem-tool evidence).\ncorrection-marker\n"
            self.verified_hash = hashlib.sha256(data).hexdigest()
            # Repeating the same own-root wait must return its original stable
            # continuation; it must not cause a second generation.
            return self.dispatch("sender-subscribe-again", "coordination.subscribe", {
                "sessionId": self.sid, "requestId": self.request_id,
            })
        if self.calls == 10:
            receipt = tool_output(request, "sender-subscribe-again")
            assert receipt["duplicate"] and receipt["result"]["continuationId"] == self.continuation_id
            return self.final("Checked the actual native transcript result. ",
                              "Independently checked the scripted artifact bytes.")
        raise AssertionError(f"Unexpected sender provider request {self.calls}")


class Recipient(ScriptedProvider):
    def __init__(self, sid, sender, blocked, release, artifact):
        super().__init__()
        self.sid, self.sender = sid, sender
        self.blocked, self.release, self.artifact = blocked, release, artifact
        self.request_id = self.correction_id = self.artifact_hash = None

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            envelope, _ = next((e, t) for e, t in peer_envelopes(request) if e["mode"] == "queue")
            assert envelope["senderSessionId"] == self.sender and envelope["recipientSessionId"] == self.sid
            assert envelope["senderRuntimeSessionId"] == self.sender and "grantId" not in envelope
            assert envelope["sourceInputIds"] == [HUMAN_INPUT] and envelope["sourceGenerationId"]
            self.source_generation_id = envelope["sourceGenerationId"]
            self.request_id = envelope["requestId"]
            assert envelope["inputId"] == self.request_id
            self.blocked.set()
            # Hold an actual provider request while the sender submits anchored
            # native steering. Return a tool call to force another request
            # boundary; never cancel-and-resend the in-flight work.
            await asyncio.wait_for(self.release.wait(), 30)
            return self.dispatch("recipient-context", "coordination.context", {"sessionId": self.sid})
        if self.calls == 2:
            receipt = tool_output(request, "recipient-context")
            assert receipt["accepted"]
            context = receipt["result"]
            assert context["grants"] == [] and context["proposals"] == [], context
            assert self.request_id in {row["requestId"] for row in context["requests"]}
            correction, text = next((e, t) for e, t in peer_envelopes(request) if e["mode"] == "steer")
            assert "grantId" not in correction and CORRECTION in text
            assert correction["senderSessionId"] == correction["senderRuntimeSessionId"] == self.sender
            assert correction["recipientSessionId"] == self.sid
            assert correction["sourceGenerationId"] == self.source_generation_id
            assert correction["sourceInputIds"] == [HUMAN_INPUT]
            assert correction["requestId"] != self.request_id
            self.correction_id = correction["requestId"]
            assert correction["inputId"] == self.correction_id
            self.artifact.write_bytes(
                b"Scripted artifact (not model filesystem-tool evidence).\ncorrection-marker\n"
            )
            self.artifact_hash = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
            return self.dispatch("recipient-reply", "coordination.reply", {
                "requestId": self.request_id, "kind": "result", "outcome": "success",
                "text": "Checked the scripted plain-text artifact; correction-marker retained.",
                "references": [self.artifact.as_uri()],
            })
        if self.calls == 3:
            receipt = tool_output(request, "recipient-reply")
            assert receipt["accepted"] and receipt["result"]["status"] == "staged"
            assert receipt["result"]["qualified"] is False
            return self.final("Scripted artifact checked: ",
                              "plain text; correction-marker retained.")
        raise AssertionError(f"Unexpected recipient provider request {self.calls}")


class DirectNativeHost:
    """In-process transport adapter around real sessions, not a fake runtime."""
    def __init__(self):
        self.roots = {}
        self.queue = asyncio.Queue()
        self.published, self.bridges, self.approvals, self.admissions = [], [], [], []
        self.errors = []
        self.app = self.pump = None

    def publish(self, event):
        sid = PUBLISH_ROOT.get()
        self.published.append((sid, copy.deepcopy(event)))
        self.queue.put_nowait((sid, copy.deepcopy(event)))

    def observe(self, worker, event):
        token = PUBLISH_ROOT.set(worker.runtime.session_id)
        try:
            worker.observe(event)
        finally:
            PUBLISH_ROOT.reset(token)

    async def route_events(self):
        while True:
            sid, event = await self.queue.get()
            try:
                normalized = normalize_event(event, sid)
                if normalized:
                    await self.app.on_runtime_event(*normalized)
            except Exception as exc:
                self.errors.append(exc)
            finally:
                self.queue.task_done()

    async def flush(self):
        await asyncio.wait_for(self.queue.join(), 30)
        if self.errors:
            raise RuntimeError("Serial native event routing failed") from self.errors[0]

    async def collaboration_approval(self, sid, prompt, approval_id):
        self.approvals.append({"sessionId": sid, "approvalId": approval_id})
        raise AssertionError("Grant-free coordination must not request a human approval.")

    async def send(self, session, text, input_id, emit):
        worker = self.roots[session["id"]]
        async with worker.command_lock:
            worker.controls.require_idle()
            worker.context_bindings[input_id] = session.get("surfaceInputs", {}).get(input_id, {})
            identity = await worker.runtime.submit(Input("user", text, id=input_id))
        return {"accepted": True, "inputId": identity, "completed": False}

    async def automatic_input(self, session, args, guard, steering):
        worker = self.roots[session["id"]]
        async with worker.command_lock:
            async def authorize(values):
                await self.flush()
                reason = guard()
                if reason:
                    return {"admitted": False, "reason": reason}
                return await worker.bridge("coordination.admit", values)
            # Match RuntimeManager/Worker routing: automatic returns may enter
            # an active request boundary; explicit queue remains idle-only.
            adapter = steer if steering else deliver if args.get("automaticDelivery") is True else admit
            result = await adapter(worker.controls, worker.runtime, args, worker.activation, authorize,
                                   stop_epoch=lambda: worker.stop_revision)
            self.admissions.append({"sessionId": session["id"], **args, **result})
            return result

    async def collaboration_input(self, session, args, guard, emit):
        return await self.automatic_input(session, args, guard, False)

    async def collaboration_steer(self, session, args, guard, emit):
        return await self.automatic_input(session, args, guard, True)

    async def before_execute(self, worker):
        """Optional fixture setup before the real execution task captures context."""

    async def mount(self, row, provider, paths):
        plan = {"session": {
            "orchestrator": {"module": "loop-live", "config": {
                "use_streaming": False, "ephemeral_injection_mode": "tail",
            }},
            "context": {"module": "context-simple"},
        }}
        prepared = PreparedBundle(plan, BundleModuleResolver(paths),
                                  Bundle(name="collaboration-native-probe", session=plan["session"]))
        session = await prepared.create_session(session_id=row["id"],
            session_cwd=Path(row.get('workingDirectory') or row['workspace']))
        worker = worker_module.Worker()
        worker.session = session
        self.roots[row["id"]] = worker
        worker.runtime = Runtime(session.session_id, observer=lambda event: self.observe(worker, event))
        worker.workspace, worker.home = Path(row["workspace"]), self.app.data_dir
        coordinator = session.coordinator
        coordinator.register_capability("live.runtime", worker.runtime)
        worker.controls = RuntimeControls(session, worker.runtime)
        worker.store = SessionStore.for_app(self.app.data_dir, worker.workspace)

        async def bridge(operation, args):
            await self.flush()  # Transport ordering: delivered input precedes tools.
            self.bridges.append({"sessionId": row["id"], "operation": operation, "args": copy.deepcopy(args)})
            return await self.app.app_bridge(operation, args, row["id"])
        worker.bridge = bridge
        await install_app_access(coordinator, worker.app_access_bridge(coordinator))
        transform = coordinator.get_capability("web.provider_transform")
        await coordinator.mount("providers", transform(provider), name=provider.name)
        # Deliberately shift context indexes from canonical indexes. The wrapper
        # MUST use SessionStore's filtered return, never the context row number.
        await coordinator.get("context").add_message({
            "role": "system", "content": "Fixture system row excluded from canonical persistence.",
        })

        async def checkpoint(status="in_progress"):
            rows = await coordinator.get("context").get_messages()
            canonical = worker.store.save(session.session_id, rows, {
                "working_dir": str(worker.workspace), "parent_id": None,
                "bundle_name": "collaboration-native-probe", "status": status,
            })
            anchors = checkpoint_anchors(canonical, worker.runtime)
            if anchors:
                await worker.runtime.emit("collaboration.checkpoint",
                    generation_id=worker.runtime.generation["id"], messageAnchors=anchors)
            return canonical
        coordinator.register_capability("live.checkpoint", checkpoint)
        worker.install_collaboration_checkpoint(coordinator)

        async def checkpoint_hook(event, data):
            from amplifier_core import HookResult
            await coordinator.get_capability("live.checkpoint")()
            return HookResult()
        for event in ("tool:post", "tool:error", "orchestrator:complete"):
            coordinator.hooks.register(event, checkpoint_hook, name="native-probe-" + event)
        await checkpoint()
        row.update(runtimeSessionId=session.session_id, nativeIdentity=session.session_id,
                   nativeProject=project_slug(worker.workspace), sessionKind="root",
                   historyManaged=False)
        await self.before_execute(worker)
        worker.execution = asyncio.create_task(session.execute(""))
        await worker.runtime.wait_for(lambda e: e["type"] == "session.ready", timeout=30)
        await worker.runtime.wait_for(lambda e: e["type"] == "session.idle", timeout=30)
        await self.flush()
        return worker

    async def delivered(self, sid, input_id):
        event = await self.roots[sid].runtime.wait_for(
            lambda e: e["type"] == "input.delivered" and e.get("input_id") == input_id,
            timeout=30)
        await self.flush()
        return event

    async def outcome(self, worker, input_id):
        event = await worker.runtime.wait_for(
            lambda e: e["type"] in {"generation.finished", "generation.failed", "generation.detached"}
            and input_id in e.get("input_ids", []), timeout=30)
        assert event["type"] == "generation.finished", event
        assert event["disposition"] == "manager_turn_finished" and not event["active_job_ids"]
        await worker.runtime.wait_for(
            lambda e: e["type"] == "session.idle" and e["sequence"] > event["sequence"], timeout=30)
        await self.flush()
        return next(raw for sid, raw in self.published
                    if sid == worker.runtime.session_id and raw.get("type") == "generation.finished"
                    and raw["generation_id"] == event["generation_id"])

    async def close(self):
        # Only these direct sessions are stopped. No launcher/process/DTU teardown.
        errors = []
        for worker in self.roots.values():
            if worker.execution and not worker.execution.done() and not worker.runtime.closed:
                await worker.runtime.submit(Input("stop", activation=worker.activation))
        for worker in self.roots.values():
            try:
                if worker.execution:
                    await asyncio.wait_for(asyncio.shield(worker.execution), 3)
            except Exception as exc:
                errors.append(exc)
            finally:
                if worker.execution and not worker.execution.done():
                    worker.execution.cancel()
                    await asyncio.gather(worker.execution, return_exceptions=True)
                try:
                    if worker.controls:
                        await worker.controls.close()
                finally:
                    await worker.session.cleanup()
        if self.pump:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
            self.pump = None
        if errors:
            raise RuntimeError("Direct native session cleanup observed an execution failure") from errors[0]


async def probe(home):
    provenance = installed_runtime()
    paths = {}
    for name in ("loop-live", "context-simple"):
        spec = importlib.util.find_spec("amplifier_module_" + name.replace("-", "_"))
        assert spec and spec.origin, f"Installed module missing: {name}"
        paths[name] = Path(spec.origin).parent
    workspace = home / "workspace"
    workspace.mkdir()
    artifact = workspace / "scripted-artifact.txt"
    host = DirectNativeHost()
    old_publish = worker_module.publish
    worker_module.publish = host.publish
    app = None
    try:
        async with asyncio.timeout(100):
            app = host.app = AppService(home / "app", host, workspace=workspace)
            app.state["sessions"] = [
                app._new_session({"title": title, "bundle": "collaboration-native-probe"})
                for title in ("Native sender", "Native recipient")
            ]
            source, target = app.state["sessions"]
            app.state["selectedSessionId"] = source["id"]
            app.state["view"]["draft"] = "Unsent fixture draft"
            app._publish()
            blocked, release = asyncio.Event(), asyncio.Event()
            sender = Sender(source["id"], target["id"], blocked, artifact, host)
            recipient = Recipient(target["id"], source["id"], blocked, release, artifact)
            host.pump = asyncio.create_task(host.route_events())
            source_worker = await host.mount(source, sender, paths)
            target_worker = await host.mount(target, recipient, paths)
            assert source_worker.session is not target_worker.session
            assert source_worker.session.coordinator is not target_worker.session.coordinator
            await app.dispatch("conversation.send", {
                "sessionId": source["id"],
                "text": "Coordinate one checked scripted artifact with the other native root.",
                "preserveDraft": True,
            }, origin="ui", command_id=HUMAN_INPUT, include_state=False)
            first = await host.outcome(source_worker, HUMAN_INPUT)
            assert sender.continuation_id not in source_worker.runtime.accepted
            request = app.collaboration.receipt(sender.request_id)
            assert not request.get("response") and request["subscription"]["status"] == "waiting"
            active = target_worker.runtime.generation
            assert active and recipient.blocked.is_set()
            anchored_generation = active["id"]
            assert sender.native_delivery["input_id"] == sender.request_id
            started = next(event for event in target_worker.runtime.events
                           if event["type"] == "generation.started"
                           and event.get("initial_input_id") == sender.request_id)
            assert started["generation_id"] == anchored_generation
            assert recipient.request_id == sender.request_id
            assert recipient.source_generation_id == first["generation_id"]
            release.set()
            terminal = await host.outcome(target_worker, sender.request_id)
            continuation = await host.outcome(source_worker, sender.continuation_id)
            receipt = app.collaboration.receipt(sender.request_id)
            declaration = receipt["response"]
            assert declaration["status"] == "sealed" and declaration["qualified"]
            assert receipt["delivery"] == "accepted"
            assert receipt["admission"]["accepted"] and not receipt["admission"]["completed"]
            assert declaration["terminalMessageId"] == terminal["nativeTerminal"]["messageId"] == sender.terminal_id
            assert declaration["generationId"] == terminal["generation_id"] == anchored_generation
            assert terminal["input_ids"] == [sender.request_id, sender.correction_id]
            assert recipient.correction_id == sender.correction_id
            assert sender.verified_hash == recipient.artifact_hash
            correction = app.collaboration.receipt(sender.correction_id)
            assert correction["delivery"] == "applied"
            assert correction["steering"]["target_generation_id"] == anchored_generation
            assert host.roots[source["id"]].context_inputs == [sender.continuation_id]
            await app.collaboration.drain(source["id"])
            assert sum(row["inputId"] == sender.continuation_id for row in host.admissions) == 1
            assert app.collaboration.receipt(sender.continuation_id)["delivery"] == "accepted"
            terminal_position = next(i for i, (sid, raw) in enumerate(host.published)
                                     if sid == target["id"] and raw.get("type") == "generation.finished")
            continuation_position = next(i for i, (sid, raw) in enumerate(host.published)
                                         if sid == source["id"] and raw.get("type") == "generation.started"
                                         and raw.get("initial_input_id") == sender.continuation_id)
            assert terminal_position < continuation_position
            assert host.approvals == []
            for row in (source, target):
                context = app.collaboration.current(row["id"])
                assert context["grants"] == [] and context["proposals"] == [], context
            assert app.state["selectedSessionId"] == source["id"]
            assert app.state["view"]["draft"] == "Unsent fixture draft"
            for worker, expected_turns in ((source_worker, 2), (target_worker, 1)):
                native = worker.runtime.events
                assert sum(e["type"] == "generation.started" for e in native) == expected_turns
                assert not any(e["type"] in {"generation.failed", "generation.detached", "job.cancel_requested"}
                               for e in native)
                canonical = worker.store.load(worker.session.session_id)[0]
                assert not any(row["role"] in {"system", "developer"} for row in canonical)
                assert any(row["role"] == "system" for row in
                           await worker.session.coordinator.get("context").get_messages())
                expected_inputs = ([HUMAN_INPUT, sender.continuation_id]
                                   if worker is source_worker
                                   else [sender.request_id, sender.correction_id])
                persisted_inputs = [(row.get("metadata") or {}).get("amplifier_input", {}).get("id")
                                    for row in canonical if row["role"] == "user"]
                assert persisted_inputs == expected_inputs, persisted_inputs
            canonical = target_worker.store.load(target["id"])[0]
            anchor = terminal["nativeTerminal"]
            native_row = display_message(canonical[anchor["nativeIndex"]], anchor["nativeIndex"], target)
            assert native_row["id"] == sender.terminal_id and native_row["text"] == anchor["nativeText"]
            context_rows = await target_worker.session.coordinator.get("context").get_messages()
            assert len(context_rows) - 1 > anchor["nativeIndex"]
            final_blocks = canonical[anchor["nativeIndex"]]["content"]
            assert isinstance(final_blocks, list) and len(final_blocks) == 2, final_blocks
            assert "\n" in anchor["nativeText"] and anchor["nativeText"] != terminal["text"]
            context_bridge = next(row for row in host.bridges
                                  if row["sessionId"] == source["id"] and row["operation"] == "dispatch"
                                  and row["args"].get("action") == "coordination.context")
            request_bridge = next(row for row in host.bridges
                                  if row["operation"] == "dispatch"
                                  and row["args"].get("id") == "sender-request")
            for bridge in (context_bridge, request_bridge):
                assert bridge["args"]["_generationId"] == first["generation_id"]
                assert bridge["args"]["_runtimeSessionId"] == source["id"]
                assert [row["inputId"] for row in bridge["args"]["_inputBindings"]] == [HUMAN_INPUT]
            assert receipt["sourceGenerationId"] == first["generation_id"]
            assert receipt["sourceInputIds"] == [HUMAN_INPUT]
            reply_bridge = next(row for row in host.bridges
                                if row["operation"] == "dispatch"
                                and row["args"].get("action") == "coordination.reply")
            assert reply_bridge["args"]["_generationId"] == anchored_generation
            assert reply_bridge["args"]["_runtimeSessionId"] == target["id"]
            assert [row["inputId"] for row in reply_bridge["args"]["_inputBindings"]] == [
                sender.request_id, sender.correction_id,
            ]
            for bridge in host.bridges:
                assert "_coordinationApproval" not in bridge["args"]
                action = bridge["args"].get("action")
                assert action not in {"coordination.grant", "coordination.decide", "coordination.revoke"}
                if action in {"coordination.send", "coordination.subscribe", "coordination.create"}:
                    assert "grantId" not in bridge["args"]["args"]
            await host.flush()
            return {
                "kind": "real native sessions with scripted providers/direct event pump",
                "installed_runtime": provenance, "independent_native_roots": 2,
                "coordination_approval_calls": len(host.approvals), "grant_free": True,
                "native_provider_requests": {"sender": sender.calls, "recipient": recipient.calls},
                "native_tool_calls": sender.tool_calls + recipient.tool_calls,
                "human_input_id": HUMAN_INPUT, "human_source_message_id": sender.source_id,
                "request_id": sender.request_id, "correction_input_id": sender.correction_id,
                "continuation_input_id": sender.continuation_id,
                "terminal_message_id": sender.terminal_id,
                "recipient_generation_id": terminal["generation_id"],
                "continuation_generation_id": continuation["generation_id"],
                "native_result_resolved_through_coordination_read": True,
                "queued_receipt_synchronized_with_native_delivery": True,
                "single_terminal_checkpoint_continuation": True,
                "in_flight_steering_applied_without_cancellation": True,
                "canonical_index_not_context_index": True,
                "scripted_artifact_sha256": sender.verified_hash,
                "network_model_calls": 0, "limits": LIMITS,
            }
    finally:
        try:
            async with asyncio.timeout(12):
                if app:
                    await app.close()
                else:
                    await host.close()
        finally:
            if host.pump:
                host.pump.cancel()
                await asyncio.gather(host.pump, return_exceptions=True)
            for worker in host.roots.values():
                if worker.execution and not worker.execution.done():
                    worker.execution.cancel()
                    await asyncio.gather(worker.execution, return_exceptions=True)
            worker_module.publish = old_publish


async def main():
    started = time.monotonic()
    from contextlib import nullcontext
    output = Path(os.environ.get("AMPLIFIER_TEST_OUTPUT_DIR", Path.cwd() / ".ci"))
    output.mkdir(parents=True, exist_ok=True)
    # Preserve checkpoints/artifact bytes per attempt; never overwrite prior proof.
    with nullcontext(tempfile.mkdtemp(prefix="collaboration-native-probe-", dir=output)) as directory:
        home = Path(directory)
        changes = {
            "HOME": str(home), "AMPLIFIER_HOME": str(home / "native"),
            "AMPLIFIER_WEB_HOME": str(home / "app"),
            "AMPLIFIER_UNIFIED_IMPORT_HOME": str(home / "legacy"),
            "AMPLIFIER_SESSION_STATE_HOME": str(home / "shared"),
            "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(home / "captures"),
        }
        previous = {key: os.environ.get(key) for key in changes}
        os.environ.update(changes)
        try:
            result = await probe(home)
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            result["evidence_directory"] = str(home)
            (home / "result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
            print(json.dumps(result, sort_keys=True))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    asyncio.run(main())
