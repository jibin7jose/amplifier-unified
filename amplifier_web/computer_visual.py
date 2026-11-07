"""Explicit conversation screen consent shared by text and voice.

Each browser owns its grant. Changing its selected chat or disconnecting ends
that scope, including requests already in flight. Nothing is restored on load.
Voice is a modality of the same conversation; starting or ending a call does
not create, transfer, or revoke this browser-owned permission.
"""
from aiohttp import web

from .voice_visual import VoiceVisual


def definitions(schema, string):
    target = {"sessionId": string(200), "clientId": string(100)}
    return {
        "computer.visual.status": ("Read screen availability for a browser displaying this conversation; never capture or grant consent. Supply clientId if multiple clients display it.", schema(target, ["sessionId"])),
        "computer.visual.capture": ("Capture one explicit snapshot from this conversation's user-granted computer source. Returns evidence; typed pixels accompany the next model request. Never opens a permission picker. Supply clientId when ambiguous.", schema(target, ["sessionId"])),
        "computer.visual.revoke": ("End this browser's conversation-scoped screen permission and discard pending captures.", schema(target, ["sessionId"])),
    }


class ComputerVisual(VoiceVisual):
    action_prefix = "computer.visual"
    permission_scope = "Explicit UI or agent snapshots for this chat in this browser, for up to 15 minutes; switching chats, disconnecting or stopping sharing ends access. Text and voice use the same permission. No automatic capture."
    message_via = "chat"

    def __init__(self, service, client_id, session_id):
        super().__init__(service)
        self.client_id, self.session_id = client_id, session_id
        self.detached = False

    def active(self, sid, call_id):
        record = self.service.clients.records.get(self.client_id, {})
        if (self.detached or call_id is not None or sid != self.session_id
                or record.get("selectedSessionId") != sid):
            self.fail("This browser changed chats or disconnected. Choose the source again.", "stale_visual_scope")
        self.service._session(sid)

    def owner(self, sid, call_id):
        self.active(sid, call_id)
        if self.service.clients.current.get() != self.client_id:
            self.fail("Only the browser displaying this chat can grant or deliver its screen evidence.", "visual_owner")
        return self.client_id

    def current(self, sid, call_id):
        self.active(sid, call_id)
        if not self.grant:
            self.fail("Choose a source in Chat controls → Computer use first; no screen permission has been granted for this chat.")
        return super().current(sid, call_id)

    def publish(self):
        # Projection is per client; consent is never written to saved view state.
        self.service._publish_changes()

    def detach(self):
        self.detached = True
        self.revoke()


class ComputerVisuals:
    def __init__(self, service):
        self.service, self.clients = service, {}

    def for_client(self, sid, client_id=None):
        from .errors import AppError
        current = self.service.clients.current.get()
        if current is None or client_id not in (None, current):
            raise AppError("Use the browser displaying this conversation; another client's consent cannot be borrowed.", 409, code="visual_owner")
        record = self.service.clients.records[current]
        if not sid or record.get("selectedSessionId") != sid:
            raise AppError("The browser must display the requested conversation.", 409, code="stale_visual_scope")
        self.service._session(sid)
        visual = self.clients.get(current)
        if visual is None or visual.detached or visual.session_id != sid:
            if visual:
                visual.detach()
            visual = self.clients[current] = ComputerVisual(self.service, current, sid)
        return visual

    def reconcile(self, client_id, *, disconnect=False):
        visual = self.clients.get(client_id)
        if visual and (disconnect or self.service.clients.records.get(client_id, {}).get("selectedSessionId") != visual.session_id):
            visual.detach()

    def project(self, client_id):
        self.reconcile(client_id)
        visual = self.clients.get(client_id)
        if not visual or visual.detached:
            return {"available": False, "captureMode": "explicit-frame"}
        result = visual.status(visual.session_id, None)
        result["lastCapture"] = visual.last(visual.session_id, None)
        return result

    async def dispatch(self, action, args, command_id=None, origin="ui"):
        if origin == "agent":
            from .agent_canvas import target
            target(self.service, args["sessionId"], args.get("clientId"), required=True, connected_only=True)
        visual = self.for_client(args["sessionId"], args.get("clientId"))
        return await visual.dispatch(action, args, command_id, origin)

    def read(self, sid, identity):
        for visual in self.clients.values():
            if identity in visual.receipts:
                return visual.read(sid, identity)
        from .errors import AppError
        raise AppError("This screen capture is no longer available.", 409)

    def bind_input(self, sid, input_id):
        visual = self.clients.get(self.service.clients.current.get())
        if visual:
            visual.bind_input(sid, input_id)

    async def close(self):
        for visual in self.clients.values():
            await visual.close()


def setup_routes(app):
    service = app["service"]

    async def handle(request):
        data = await request.json()
        operation = request.match_info["operation"]
        if operation == "revoke":
            # Allow cleanup after selection changes, but never revoke another
            # browser or a newer grant through a delayed callback.
            visual = service.computer_visual.clients.get(service.clients.current.get())
            if visual and visual.grant and visual.grant["id"] == data.get("grantId"):
                async with service.lock:
                    visual.revoke()
                    visual.publish()
            return web.json_response({"revoked": True})
        visual = service.computer_visual.for_client(data.get("sessionId"))
        if operation == "grant":
            result = await visual.grant_source(data)
        elif operation == "native/status":
            result = await visual.native_status(data.get("sessionId"), None)
        elif operation == "complete":
            result = await visual.complete(data)
        return web.json_response(result)

    app.router.add_post("/api/computer/visual/{operation:grant|complete|revoke|native/status}", handle)
