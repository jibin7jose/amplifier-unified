"""Production service/assets with deterministic runtime emulation, no provider.

Generation bindings and terminal anchors here are scripted adapter evidence,
not checkpoints from a native runtime or evidence of live model cooperation.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class Runtime:
    def __init__(self):
        self.messages, self.sent, self.stops = [], [], []
        self.contexts, self.terminals, self.events, self.bridge_calls = {}, {}, [], []
        self.approvals, self.approval_responses, self.failures = [], [], []
        self.artifacts, self.task_ids = {}, []

    async def event(self, kind, payload, emit):
        self.events.append({"kind": kind, **copy.deepcopy(payload)})
        await emit(kind, payload)

    async def start(self, session, emit):
        await self.event("runtime.status", {"sessionId": session["id"], "status": "ready",
                                          "runtimeSessionId": session["id"]}, emit)

    async def begin(self, session, input_id, emit):
        sid = session["id"]
        context = {"sessionId": sid, "generationId": "fixture-generation:" + input_id,
                   "inputIds": [input_id], "active": True, "emit": emit}
        self.contexts[sid] = context
        await self.event("runtime.generation", {
            "sessionId": sid, "rootSessionId": sid, "event": "generation.started",
            "generation_id": context["generationId"], "input_ids": [input_id],
        }, emit)
        await self.event("runtime.status", {
            "sessionId": sid, "rootSessionId": sid, "runtimeSessionId": sid,
            "status": "working", "event": "input.delivered", "inputId": input_id,
        }, emit)
        return context

    async def bridge(self, actor, action, args, identity):
        """Like the worker bridge, replace transport metadata from model context.

        The HTTP caller can select a scripted role, not an arbitrary principal,
        generation, or delivered input set.
        """
        sid = self.actor_session(actor)
        if not sid or sid not in self.contexts:
            raise ValueError("This scripted actor has no delivered runtime context.")
        context = self.contexts[sid]
        session = self.service._session(sid)
        native = session.get("runtimeSessionId") or session.get("nativeIdentity") or sid
        metadata = {"_runtimeSessionId": native, "_generationId": context["generationId"],
                    "_inputBindings": [{"inputId": i, "clientId": None} for i in context["inputIds"]]}
        self.bridge_calls.append({"actor": actor, "action": action, "id": identity,
                                  "sessionId": sid, "args": copy.deepcopy(args), **copy.deepcopy(metadata)})
        return await self.service.app_bridge("dispatch", {
            "action": action, "args": args, "id": identity, **metadata,
        }, sid)

    def actor_session(self, actor):
        if actor == "source":
            return self.source_id
        index = {"recipient": 0, "recipient2": 1}.get(actor)
        return self.task_ids[index] if index is not None and index < len(self.task_ids) else None

    async def send(self, session, text, input_id, emit):
        self.sent.append({"sessionId": session["id"], "text": text, "inputId": input_id, "kind": "human"})
        await self.begin(session, input_id, emit)
        return {"accepted": True, "inputId": input_id, "completed": False}

    async def steer(self, session, text, input_id, emit):
        from amplifier_web.message_delivery import find_message
        message = find_message(session, input_id)
        target = message['steering']['generationId']
        context = self.contexts[session['id']]
        if not context['active'] or target != context['generationId']:
            return {'accepted': False, 'reason': 'The scripted generation ended.'}
        self.sent.append({'sessionId':session['id'], 'text':text, 'inputId':input_id,
                          'kind':'human-steer', 'allowNextTurn':message['steering']['allowNextTurn']})
        context['inputIds'].append(input_id)
        await self.event('runtime.steering', {'sessionId':session['id'], 'event':'steering.applied',
            'input_id':input_id, 'target_generation_id':target}, emit)
        return {'accepted': True, 'disposition':'applied'}

    async def collaboration_approval(self, sid, prompt, identity):
        self.approvals.append({"sessionId": sid, "id": identity, "prompt": prompt})
        raise AssertionError("Fresh scripted collaboration must not request approval.")

    async def approval(self, sid, identity, decision):
        self.approval_responses.append({"sessionId": sid, "id": identity, "decision": decision})
        raise AssertionError("This fixture has no pending coordination approvals.")

    def candidate(self, session, message):
        namespace = session.get("collaboration", {}).get("outputNamespace")
        if namespace:
            # Each input retains its own bytes; later rounds never overwrite it.
            name = "candidate-" + hashlib.sha256(message["inputId"].encode()).hexdigest()[:16] + ".txt"
            candidate = Path(session["workspace"]) / namespace / name
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("x") as stream:
                stream.write(message["text"])
            self.artifacts.setdefault(session["id"], {})[message["inputId"]] = candidate
            if session["id"] not in self.task_ids:
                self.task_ids.append(session["id"])

    def read_artifact(self, actor="recipient", input_id=None):
        sid = self.actor_session(actor)
        artifacts = self.artifacts.get(sid, {})
        if not artifacts:
            return None
        path = artifacts.get(input_id) if input_id else list(artifacts.values())[-1]
        if path is None:
            return None
        # Read the actual file at inspection time, not a cached claim.
        data = path.read_bytes()
        return {"path": str(path), "text": data.decode(),
                "sha256": hashlib.sha256(data).hexdigest()}

    def admit(self, session, args, guard):
        reason = guard()
        if reason:
            return {"admitted": False, "reason": reason}
        return self.service.collaboration.admission(session["id"], args)

    def record_peer(self, session, args, message, kind):
        from amplifier_operations.coordination import peer_input
        self.sent.append({"sessionId": session["id"],
                          "text": peer_input(message["peerEnvelope"], message["text"]),
                          "inputId": args["inputId"], "kind": kind,
                          "peerEnvelope": copy.deepcopy(message["peerEnvelope"])})

    async def collaboration_input(self, session, args, guard, emit):
        allowed = self.admit(session, args, guard)
        if not allowed["admitted"]:
            return {"accepted": False, "reason": allowed["reason"]}
        message = allowed["message"]
        self.record_peer(session, args, message, "peer")
        await self.begin(session, args["inputId"], emit)
        self.candidate(session, message)
        # Keep source continuations active so subsequent agent calls bind the
        # actually delivered peer input, not a fabricated new human message.
        # /fixture/finish settles this source turn before the next task terminal.
        return {"accepted": True, "inputId": args["inputId"], "completed": False}

    async def collaboration_steer(self, session, args, guard, emit):
        allowed = self.admit(session, args, guard)
        if not allowed["admitted"]:
            return {"accepted": False, "reason": allowed["reason"]}
        context = self.contexts[session["id"]]
        if not context["active"] or context["generationId"] != args["targetGenerationId"]:
            return {"accepted": False, "reason": "The deterministic generation is no longer active."}
        self.record_peer(session, args, allowed["message"], "steer")
        context["inputIds"].append(args["inputId"])
        self.candidate(session, allowed["message"])
        await self.event("runtime.steering", {
            "sessionId": session["id"], "rootSessionId": session["id"],
            "event": "steering.applied", "input_id": args["inputId"],
            "target_generation_id": args["targetGenerationId"],
        }, emit)
        return {"accepted": True, "inputId": args["inputId"], "completed": False}

    async def finish(self, sid, text):
        from amplifier_operations.coordination import fingerprint
        from amplifier_web.automatic_history import display_identity
        context = self.contexts[sid]
        if not context["active"]:
            # Duplicate terminal observation is safe; never replay admission.
            payload = self.terminals[sid]
            await self.event("runtime.generation", payload, context["emit"])
            return payload
        session = self.service._session(sid)
        # Script an actual persisted transcript row so the production reveal
        # action can read/render its canonical ID. An anchor alone is not text.
        from amplifier_web.automatic_history import directory
        from amplifier_web.session_files import project_slug
        session.update(nativeProject=project_slug(session["workspace"]), nativeIdentity=sid)
        native_dir = directory(session)
        native_dir.mkdir(parents=True, exist_ok=True)
        transcript = native_dir / "transcript.jsonl"
        native_index = len(transcript.read_text().splitlines()) if transcript.exists() else 0
        (native_dir / "metadata.json").write_text(json.dumps({"session_id": sid, "working_dir": session["workspace"]}))
        with transcript.open("a") as stream:
            stream.write(json.dumps({"role": "assistant", "content": text}) + "\n")
        anchor = {"messageId": display_identity(session, native_index, "assistant", text),
                  "nativeIndex": native_index, "nativeText": text, "textDigest": fingerprint(text),
                  "rootSessionId": sid, "generationId": context["generationId"],
                  "deterministicEmulation": True}
        payload = {"sessionId": sid, "rootSessionId": sid, "generation_id": context["generationId"],
                   "event": "generation.finished", "input_ids": list(context["inputIds"]),
                   "text": text, "nativeTerminal": anchor, "active_job_ids": [],
                   "disposition": "manager_turn_finished"}
        self.terminals[sid] = copy.deepcopy(payload)
        context["active"] = False
        await self.event("runtime.generation", payload, context["emit"])
        await self.service.history.load(sid, before=native_index + 1, limit=1)
        await self.event("runtime.status", {"sessionId": sid, "status": "idle"}, context["emit"])
        return payload

    async def message_worker(self, sid, wid, text, input_id=None):
        self.messages.append({"sessionId": sid, "workerId": wid, "text": text, "inputId": input_id})
        await self.service.on_runtime_event("worker.updated", {"sessionId": sid, "id": wid, "status": "running"})
        return {"accepted": True, "completed": False, "inputId": input_id}

    async def stop_worker(self, sid, wid):
        self.stops.append({"sessionId": sid, "workerId": wid})
        await self.service.on_runtime_event("worker.updated", {"sessionId": sid, "id": wid, "status": "interrupted"})
        return {"accepted": True, "completed": False}

    async def stop(self, sid, *, reason="stopped"):
        self.stops.append({"sessionId": sid})

    async def close(self):
        pass


async def main(home):
    from aiohttp import web
    from amplifier_web.server import create_app
    workspace = home / "workspace"
    workspace.mkdir()
    os.environ["AMPLIFIER_HOME"] = str(home / "shared")
    # This is a configured-workflow fixture, not the missing-connection journey.
    # Synthetic provider metadata never sends a model request.
    shared = home / 'shared'
    shared.mkdir()
    (shared / 'settings.yaml').write_text(json.dumps({'config': {'providers': [
        {'module': 'provider-fixture', 'id': 'fixture', 'config': {'default_model': 'fixture-model'}}]}}))
    runtime = Runtime()
    app = await create_app(home / "app", workspace=str(workspace), runtime=runtime, voice=False, background_updates=False)
    app["control_token"] = "fixture-browser-control-token"
    service = runtime.service = app["service"]
    for title in ("Selected conversation", "Other conversation"):
        await service.dispatch("session.create", {"title": title})
    selected = next(row for row in service.state["sessions"] if row["title"] == "Selected conversation")
    other = next(row for row in service.state["sessions"] if row["title"] == "Other conversation")
    runtime.source_id, runtime.other_id = selected["id"], other["id"]
    directory = service.data_dir / "sessions" / selected["id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "effective-configuration.json").write_text(json.dumps({"providers": [], "tools": []}))
    await service.dispatch("session.select", {"id": selected["id"]})
    for wid, title in (("worker-a", "First worker"), ("worker-b", "Second worker")):
        await service.on_runtime_event("worker.updated", {"sessionId": other["id"], "id": wid, "name": title, "kind": "session", "parentSessionId": other["id"], "runId": wid + "-run", "status": "running", "persistent": True})

    async def inspect(request):
        return web.json_response({"selected": selected["id"], "other": other["id"], "messages": runtime.messages, "sent": runtime.sent, "stops": runtime.stops,
            "tasks": [{"id": row["id"], "title": row["title"], "sessionKind": row.get("sessionKind", "root"),
                       "workspace": row["workspace"], "bundle": row["bundle"], "collaboration": row["collaboration"]}
                      for row in service.state["sessions"] if row.get("collaboration")],
            "workspace": selected["workspace"], "bundle": selected["bundle"],
            "managementResults": service.state.get("managementResults", {}),
            "artifact": runtime.read_artifact(),
            "coordination": service.collaboration.current(selected["id"]),
            "humanMessages": [row for row in selected["messages"] if row.get("inputOrigin") in {"ui", "user", "voice"}],
            "contexts": {sid: {key: value for key, value in row.items() if key != "emit"}
                         for sid, row in runtime.contexts.items()},
            "statuses": {row["id"]: row["status"] for row in service.state["sessions"]},
            "events": runtime.events, "terminals": runtime.terminals, "bridgeCalls": runtime.bridge_calls,
            "approvals": runtime.approvals, "approvalResponses": runtime.approval_responses,
            "failures": runtime.failures, "deterministicRuntime": True, "nativeRuntime": False, "providerCalls": False})

    async def artifact(request):
        if request.query.get("actor", "recipient") not in {"recipient", "recipient2"}:
            raise web.HTTPBadRequest()
        return web.json_response(runtime.read_artifact(request.query.get("actor", "recipient"), request.query.get("inputId")))

    async def emit(request):
        data = await request.json()
        await service.on_runtime_event("worker.updated", {"sessionId": other["id"], **data})
        return web.json_response({"ok": True})

    async def peer(request):
        data = await request.json()
        if set(data) != {"actor", "action", "args", "id"} or data["actor"] not in {"source", "recipient", "recipient2"}:
            raise web.HTTPBadRequest(text="Only a scripted actor/action is accepted; transport metadata is runtime-owned.")
        result = await runtime.bridge(data["actor"], data["action"], data["args"], data["id"])
        return web.json_response(result)

    async def finish(request):
        # No request-supplied generation, inputs, text, principal or anchor.
        data = await request.json()
        if set(data) != {"actor"} or data["actor"] not in {"source", "recipient", "recipient2"}:
            raise web.HTTPBadRequest()
        sid = runtime.actor_session(data["actor"])
        if not sid or sid not in runtime.contexts:
            raise web.HTTPBadRequest(text="A known candidate is required; terminal metadata is runtime-owned.")
        if data["actor"] == "source":
            terminal = await runtime.finish(sid, "Deterministic continuation received the exact result references.")
            return web.json_response({"terminal": terminal, "deterministicEmulation": True})
        source_context = runtime.contexts.get(runtime.source_id)
        if runtime.contexts[sid]["active"] and source_context and source_context["active"]:
            await runtime.finish(runtime.source_id, "Deterministic sender saved its request-specific wait.")
        candidate = runtime.read_artifact(data["actor"])
        terminal = await runtime.finish(sid, "Deterministic candidate retained: " + candidate["text"])
        return web.json_response({"terminal": terminal, "deterministicEmulation": True})

    app.router.add_get("/fixture", inspect)
    app.router.add_get("/fixture/artifact", artifact)
    app.router.add_post("/fixture/emit", emit)
    app.router.add_post("/fixture/peer", peer)
    app.router.add_post("/fixture/finish", finish)
    runner = web.AppRunner(app)
    await runner.setup()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT) if os.name == "posix" else ()
    for signum in signals:
        loop.add_signal_handler(signum, stopped.set)
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        app["allowed_origins"] = app["allowed_origins"] | {url}
        print(json.dumps({"fixture": "coordination-ui", "pid": os.getpid(), "base": url}), flush=True)
        await stopped.wait()
    finally:
        try:
            await runner.cleanup()
        finally:
            for signum in signals:
                loop.remove_signal_handler(signum)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="Exact synthetic home owned by the browser journey")
    asyncio.run(main(parser.parse_args().home))
