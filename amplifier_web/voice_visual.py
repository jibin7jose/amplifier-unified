"""Explicit snapshots of a browser-granted source, bound to one live call.

Consent and pending requests are deliberately memory-only. Pixels are private
attachments; no screen stream, permission or unfinished capture is replayed.
"""
import asyncio
import base64
import copy
import hashlib
import json
import time
import uuid

from aiohttp import web


def definitions(schema, string):
    target = {"sessionId": string(200), "callId": string(200)}
    return {
        "voice.visual.status": ("Read call-scoped screen-source availability; never capture or grant permission.", schema(target)),
        "voice.visual.capture": ("Explicitly capture one frame from the source the user granted for this active voice call. No background observation. Returns saved evidence; app_control supplies typed pixels on the next request if vision is supported.", schema(target)),
        "voice.visual.revoke": ("Stop sharing the selected screen source for this call and discard pending captures.", schema(target)),
    }


class VoiceVisual:
    action_prefix = "voice.visual"
    permission_scope = "Explicit UI or agent snapshots during this call; no automatic capture."
    message_via = "call"

    def __init__(self, service):
        self.service = service
        self.grant = None
        self.pending = None
        self.receipts = {}
        self.next_input = None
        self.native_task = None
        from .native_foreground import NativeForeground
        self.native = NativeForeground()

    def fail(self, message, code="visual_unavailable"):
        from .errors import AppError
        raise AppError(message, 409, code=code)

    def active(self, sid, call_id):
        manager = self.service.voice_service
        call = getattr(manager, "call", None)
        if (not call or call.id != call_id or call.session_id != sid or call.closed
                or call.closing or self.service.state["voice"].get("status") != "connected"):
            self.fail("Visual capture requires this conversation's connected voice call.", "stale_call")
        return call

    def owner(self, sid, call_id):
        call = self.active(sid, call_id)
        client = self.service.clients.current.get()
        if not client or client != getattr(call, "client_id", None):
            self.fail("Only the browser owning this call can grant or deliver visual evidence.", "visual_owner")
        return client

    def current(self, sid, call_id):
        self.active(sid, call_id)
        grant = self.grant
        if not grant or grant["sessionId"] != sid or grant["callId"] != call_id or grant["expiresAt"] < time.time():
            self.fail("Choose a screen source in Chat controls → Computer use first; permission is limited to this call.")
        if grant["source"]["kind"] == "native-foreground":
            from .host_identity import require_local_host
            try:
                require_local_host(grant["source"]["host"]["id"])
            except ValueError:
                self.fail("This desktop source belongs to another host.", "stale_capture")
            if grant["source"]["hostInstanceId"] != self.service.instance_id:
                self.fail("The desktop host restarted; select its source again.", "stale_capture")
        return grant

    def status(self, sid, call_id):
        try:
            grant = self.current(sid, call_id)
            return {"available": True, **copy.deepcopy(grant), "pending": bool(self.pending),
                    "nativeForeground": grant["source"]["kind"] == "native-foreground", "captureMode": "explicit-frame"}
        except Exception as exc:
            from .errors import AppError
            if not isinstance(exc, AppError):
                raise
            return {"available": False, "reason": str(exc), "nativeForeground": False,
                    "captureMode": "explicit-frame", "lastCapture": self.last(sid, call_id)}

    def last(self, sid, call_id):
        return next((copy.deepcopy(row) for row in reversed(list(self.receipts.values()))
                     if row["sessionId"] == sid and row["callId"] == call_id), None)

    def publish(self):
        voice = self.service.state["voice"]
        voice["visual"] = self.status(voice.get("sessionId"), voice.get("id"))
        if voice["visual"]["available"]:
            voice["visual"]["lastCapture"] = self.last(voice.get("sessionId"), voice.get("id"))
        self.service._publish_changes(globals={'voice'})

    async def native_status(self, sid, call_id):
        self.owner(sid, call_id)
        from .host_identity import local_host_identity
        try:
            result = await self.native.run("status")
        except (TimeoutError, ValueError, OSError):
            result = {"available": False, "status": "error", "code": "native_status_failed"}
        self.owner(sid, call_id)
        return {**result, "host": local_host_identity(), "hostInstanceId": self.service.instance_id}

    def revoke(self):
        if self.native_task and not self.native_task.done():
            self.native_task.cancel()
        self.grant = None
        self.next_input = None
        if self.pending and not self.pending["future"].done():
            self.pending["future"].set_result({"error": "Screen permission ended before capture completed."})

    async def close(self):
        task = self.native_task
        self.revoke()
        if task:
            await asyncio.gather(task, return_exceptions=True)

    async def grant_source(self, data):
        sid, call_id = data.get("sessionId"), data.get("callId")
        client = self.owner(sid, call_id)
        source = data.get("source", {})
        if isinstance(source, dict) and source.get("kind") == "native-foreground":
            from .host_identity import require_local_host, local_host_identity
            try:
                require_local_host(source.get("hostId"))
            except ValueError:
                self.fail("Choose this server host explicitly; cross-host capture is unavailable.", "visual_owner")
            if source.get("hostInstanceId") != self.service.instance_id:
                self.fail("The desktop host changed; check availability again.", "stale_capture")
            status = await self.native_status(sid, call_id)
            if status.get("available") is not True:
                self.fail("Native capture is unavailable: "+str(status.get("status", "unknown")), "native_unavailable")
            source = {"kind": "native-foreground", "label": local_host_identity()["label"]+" foreground window",
                      "reportedBy": "native-host", "host": local_host_identity(), "hostInstanceId": self.service.instance_id}
        elif (not isinstance(source, dict) or source.get("kind") not in {"browser", "window", "monitor", "unknown"}
                or not isinstance(source.get("label"), str) or not 1 <= len(source["label"]) <= 200):
            self.fail("The browser must identify the selected capture source.")
        else:
            source = {"kind": source["kind"], "label": source["label"], "reportedBy": "browser"}
        async with self.service.lock:
            self.owner(sid, call_id)
            self.revoke()
            self.grant = {"id": uuid.uuid4().hex, "sessionId": sid, "callId": call_id, "clientId": client,
                          "source": source,
                          "grantedAt": time.time(), "expiresAt": time.time()+900,
                          "scope": self.permission_scope}
            self.publish()
            return copy.deepcopy(self.grant)

    async def capture(self, sid, call_id, *, next_input=False):
        async with self.service.lock:
            grant = self.current(sid, call_id)
            if self.pending:
                self.fail("A screen capture is already pending.", "visual_busy")
            previous = self.last(sid, call_id)
            if previous and time.time()-previous["receivedAt"] < 2:
                self.fail("Wait two seconds before requesting another screen capture.", "visual_busy")
            identity = uuid.uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self.pending = {"id": identity, "grantId": grant["id"], "future": future, "requestedAt": time.time(), "nextInput": next_input}
            command = {"id": identity, "type": self.action_prefix+".capture", "createdAt": time.time(),
                       "callId": call_id, "sessionId": sid, "grantId": grant["id"], "clientId": grant["clientId"]}
            native = grant["source"]["kind"] == "native-foreground"
            if native:
                self.native_task = asyncio.create_task(self._capture_native(command))
            else:
                client = self.service.clients.records[grant["clientId"]]
                client["deviceCommands"] = (client.get("deviceCommands", [])+[command])[-20:]
            self.publish()
        try:
            result = await asyncio.wait_for(asyncio.shield(future), 10)
            if result.get("error"):
                self.fail(result["error"], "visual_capture_failed")
            self.current(sid, call_id)  # End/reconnect cannot turn old pixels into current evidence.
            return result
        except TimeoutError:
            self.revoke()
            self.fail("The capture browser did not respond. Re-select the source; nothing was replayed.", "visual_timeout")
        finally:
            if native and self.native_task:
                task = self.native_task
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if self.native_task is task:
                    self.native_task = None
            async with self.service.lock:
                if self.pending and self.pending["id"] == identity:
                    self.pending = None
                if not future.done():
                    future.cancel()
                self.publish()

    async def _capture_native(self, command):
        try:
            grant = self.current(command["sessionId"], command["callId"])
            if grant["id"] != command["grantId"]:
                self.fail("Native source changed.", "stale_capture")
            result = await self.native.run("capture")
            if "image" not in result:
                self.fail("Native capture unavailable: "+str(result.get("code", result.get("status", "unknown"))), "native_unavailable")
            await self.complete({**command, **result, "requestId": command["id"]}, _native=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            pending = self.pending
            if pending and pending["id"] == command["id"] and not pending["future"].done():
                pending["future"].set_result({"error": "Native capture failed or permission changed. No image was delivered; nothing was replayed."})

    async def complete(self, data, *, _native=False):
        sid, call_id = data.get("sessionId"), data.get("callId")
        if not _native:
            self.owner(sid, call_id)
        async with self.service.lock:
            grant = self.current(sid, call_id)
            if _native != (grant["source"]["kind"] == "native-foreground"):
                self.fail("This capture must come from its selected source.", "visual_owner")
            pending = self.pending
            if (not pending or pending["id"] != data.get("requestId") or pending["grantId"] != grant["id"]
                    or data.get("grantId") != grant["id"] or pending["future"].done()):
                self.fail("This capture request expired or its source changed.", "stale_capture")
            if data.get("error"):
                pending["future"].set_result({"error": str(data["error"])[:300]})
                return {"accepted": True}
            encoded, captured = data.get("image"), data.get("capturedAt")
            try:
                if not isinstance(encoded, str) or len(encoded) > 700000:
                    raise ValueError()
                raw = base64.b64decode(encoded, validate=True)
                from .voice_visual_image import validate_png
                width, height = validate_png(raw)
                if type(captured) not in (float, int) or not pending["requestedAt"]-2 <= captured <= time.time()+2 or time.time()-captured > 10:
                    raise ValueError()
            except (ValueError, TypeError):
                self.fail("Use a fresh PNG frame up to 1280 pixels and 500 KB.", "invalid_capture")
            observation = None
            if _native:
                from .native_foreground import observation_metadata
                observation = observation_metadata(data)
            from .attachments import save
            attachment = save(self.service.data_dir, self.action_prefix+"-"+pending["id"]+".png", encoded)
            row = {"id": pending["id"], "sessionId": sid, "callId": call_id, "grantId": grant["id"],
                   "source": copy.deepcopy(grant["source"]), "capturedAt": captured, "receivedAt": time.time(),
                   "attachment": attachment, "width": width, "height": height,
                   "sha256": hashlib.sha256(raw).hexdigest(), "untrustedData": True,
                   "nativeForeground": _native, "scope": grant["scope"]}
            if _native:
                row["observation"] = observation
            self.receipts[row["id"]] = row
            if pending["nextInput"]:
                self.next_input = row["id"]
            self.receipts = dict(list(self.receipts.items())[-32:])
            # Historical evidence uses the normal private attachment store. It
            # does not create a model turn, touch the draft, or select a chat.
            session = self.service._session(sid)
            self.service._message(session, "user", "Screen snapshot from the selected "+grant["source"]["kind"]+" source (reference data).",
                                  self.message_via, attachments=[attachment], visualCapture=copy.deepcopy(row), activityOnly=True)
            pending["future"].set_result(copy.deepcopy(row))
            self.publish()
            return {"accepted": True, "captureId": row["id"]}

    def read(self, sid, identity):
        row = self.receipts.get(identity)
        if not row or row["sessionId"] != sid:
            self.fail("This capture belongs to another conversation or is unavailable.")
        grant = self.current(sid, row["callId"])
        if row["grantId"] != grant["id"] or time.time()-row["receivedAt"] > 30:
            self.fail("This snapshot is stale; request a new capture.", "stale_capture")
        from .attachments import file_path
        path, _ = file_path(self.service.data_dir, row["attachment"]["id"])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            self.fail("The saved image changed; request a new capture.")
        return {**copy.deepcopy(row), "_image": base64.b64encode(raw).decode()}

    def bind_input(self, sid, input_id):
        """One explicitly captured frame may accompany the next spoken request."""
        identity, self.next_input = self.next_input, None
        if not identity:
            return
        from .errors import AppError
        try:
            row = self.read(sid, identity)
        except (AppError, OSError, ValueError):
            return
        for message in self.service._session(sid)["messages"]:
            if message.get("visualCapture", {}).get("id") == row["id"]:
                message["inputId"] = input_id
                return

    async def dispatch(self, action, args, command_id=None, origin="ui"):
        sid, call_id = args["sessionId"], args.get("callId")
        self.service._session(sid)
        if action == self.action_prefix+".status":
            result = self.status(sid, call_id)
        elif action == self.action_prefix+".capture":
            identity = command_id or str(uuid.uuid4())
            fingerprint = json.dumps([action, sid, call_id]+([self.client_id] if self.action_prefix == "computer.visual" else []))
            async with self.service.lock:
                previous = self.service.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (identity,)).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        self.fail("This request identity was already used for another action.")
                    saved = json.loads(previous[1])
                    if not saved.get("accepted"):
                        self.fail("The previous capture did not have a confirmed result. Nothing was replayed.", "visual_outcome_unknown")
                    return saved
                self.service.db.execute("INSERT INTO commands VALUES (?,?,?)", (identity, fingerprint, json.dumps({"accepted": False, "status": "pending"})))
                self.service.db.commit()
            result = await self.capture(sid, call_id, next_input=origin == "ui")
            receipt = {"accepted": True, "result": result}
            async with self.service.lock:
                self.service.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
                self.service.db.commit()
        else:
            async with self.service.lock:
                self.active(sid, call_id)
                self.revoke()
                self.publish()
            result = {"revoked": True}
        return {"accepted": True, "result": result}


def setup_routes(app):
    service = app["service"]

    async def grant(request):
        return web.json_response(await service.voice_visual.grant_source(await request.json()))

    async def native_status(request):
        data = await request.json()
        return web.json_response(await service.voice_visual.native_status(data.get("sessionId"), data.get("callId")))

    async def complete(request):
        return web.json_response(await service.voice_visual.complete(await request.json()))

    async def revoke(request):
        data = await request.json()
        # Owner fencing still works while the voice connection is ending.
        grant = service.voice_visual.grant
        if grant and (grant["clientId"] != service.clients.current.get() or grant["id"] != data.get("grantId")):
            service.voice_visual.fail("This screen source belongs to another client.")
        async with service.lock:
            service.voice_visual.revoke()
            service.voice_visual.publish()
        return web.json_response({"revoked": True})

    app.router.add_post("/api/voice/visual/native/status", native_status)
    app.router.add_post("/api/voice/visual/grant", grant)
    app.router.add_post("/api/voice/visual/complete", complete)
    app.router.add_post("/api/voice/visual/revoke", revoke)
