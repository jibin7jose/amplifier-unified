"""Versioned HTTP/SSE session contract shared by browser and terminal clients."""
from __future__ import annotations

import asyncio
import json
from aiohttp import web

from .service import AppError
from .auth import with_history_scope


@web.middleware
async def client_context(request, handler):
    if request.path == '/api/connections/v1' or request.path.startswith('/api/connections/v1/'):
        # Device enrollment is independent of conversation/view attachment.
        return await handler(request)
    service = request.app["service"]
    header = request.headers.get("X-Amplifier-Client")
    query = request.query.get("clientId")
    if header and query and header != query:
        raise AppError("Conflicting client identities.")
    identity = header or query
    if request.path == "/api/clients/attach":
        identity = None
    if request.get('companion_device') and request.path != '/api/clients/attach':
        record = service.clients.records.get(identity)
        if record is None or record.get('deviceOwner') != request['companion_device']:
            raise AppError("Attach a client owned by this device.", 403)
    with service.clients.bind(identity):
        try:
            return await handler(request)
        except AppError as exc:
            if identity and exc.code == "session_busy" and not request.get("companion_device"):
                exc.client_state = service.browser_state()
            raise


def setup_routes(app, streams):
    service = app["service"]

    def require_client():
        if service.clients.current.get() is None:
            raise AppError("Attach a client and send X-Amplifier-Client.", 400)

    async def attach(request):
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) - {"clientId", "resumeClientId", "kind", "protocolVersion", "includeState"}:
            raise AppError("Invalid client registration.")
        if 'includeState' in payload and type(payload['includeState']) is not bool:
            raise AppError("includeState must be a boolean.")
        if payload.get("protocolVersion", 1) != 1:
            raise AppError("Unsupported client protocol version.", 409)
        identity = payload.get("clientId")
        async with service.lock:
            service.clients.attach(identity, payload.get("resumeClientId"), payload.get("kind", "web"),
                                   device=request.get("companion_device"))
            service._save_changes()
            with service.clients.bind(identity):
                from .client_observations import discovery
                return web.json_response({"clientId": identity, "protocolVersion": 1,
                    "hostInstanceId": service.instance_id, "reconnect": "snapshot",
                    **({"historyScope": request["history_scope"]} if request.get("history_scope") else {}),
                    **discovery(service),
                    "transports": ["http", "sse", "websocket"],
                    **({} if request.get('companion_device') or payload.get('includeState') is False or request.headers.get("X-Amplifier-State-Transport") == "delta-v1"
                       else {"state": with_history_scope(request, service.browser_state())})})

    def session_snapshot(identity, snapshot=None):
        snapshot = snapshot or service.session_state(identity)
        session = next((row for row in snapshot["sessions"] if row["id"] == identity), None)
        if session is None:
            return {"protocolVersion": 1, "revision": snapshot["revision"],
                    "hostInstanceId": service.instance_id, "sessionId": identity, "deleted": True}
        return {"protocolVersion": 1, "revision": snapshot["revision"],
                "hostInstanceId": service.instance_id, "session": session}

    async def sessions(request):
        require_client()
        if any(key in request.query for key in ('view', 'cursor', 'activity', 'pinned', 'scope')):
            return await app['observations'].sessions(request)
        from .browser_state import summary
        if set(request.query) - {'offset', 'limit', 'workspace', 'kind', 'clientId'}:
            raise AppError('Unsupported session catalog parameter.')
        kind = request.query.get('kind')
        if kind not in (None, 'root'):
            raise AppError('Choose kind=root or omit the kind filter.')
        try:
            offset, limit = int(request.query.get("offset", 0)), int(request.query.get("limit", 100))
        except ValueError:
            raise AppError('Invalid offset or limit.') from None
        if offset < 0 or not 1 <= limit <= 100:
            raise AppError("Choose a nonnegative offset and a limit from 1 to 100.")
        workspace = request.query.get("workspace")
        directory = getattr(service, "directory", None)
        if directory is not None:
            where, params = [], []
            if workspace is not None:
                where.append('workspace=?')
                params.append(workspace)
            if kind == 'root':
                where.append("session_kind='root'")
            rows = directory.select(' AND '.join(where) or '1', params,
                offset=offset, limit=limit+1, state=service.state)
            page, more = rows[:limit], len(rows)>limit
        else:
            from .session_navigation import is_top_level
            rows = [row for row in service.state["sessions"]
                    if (workspace is None or row.get("workspace") == workspace)
                    and (kind is None or is_top_level(row))]
            page, more = rows[offset:offset+limit], offset+limit<len(rows)
        return web.json_response({"protocolVersion": 1, "items": [summary(row) for row in page],
            "nextOffset": offset+limit if more else None})

    async def state(request):
        require_client()
        identity = request.match_info["session_id"]
        service._session(identity)
        await service.history.ensure_loaded(identity)
        await service._flush_pending_progress()
        return web.json_response(session_snapshot(identity))

    async def commands(request):
        require_client()
        identity = request.match_info["session_id"]
        service._session(identity)
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) - {"id", "action", "args", "expectedRevision"}:
            raise AppError("Invalid session command.")
        command_id = payload.get("id")
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 200:
            raise AppError("Supply a stable command ID for safe retries.")
        action = payload.get("action")
        key = {
            "message.edit": "sessionId", "message.copy": "sessionId", "conversation.send": "sessionId", "conversation.stop": "sessionId",
            "conversation.delivery": "sessionId", "conversation.retry": "sessionId",
            "worker.spawn": "sessionId", "worker.stop": "sessionId", "worker.steer": "sessionId",
            "approval.respond": "sessionId", "runtime.control": "sessionId",
            "session.takeover": "id", "session.history": "id", "session.rename": "id",
            "session.warm": "id", "session.naming": "id",
            **{name: "sessionId" for name in ("worktree.inspect", "worktree.list", "worktree.status", "worktree.create", "worktree.attach", "worktree.remove", "worktree.archive", "worktree.restore", "worktree.handoff", "worktree.reconcile")},
            "coordination.artifact": "sessionId",
            "coordination.review": "sessionId",
            "coordination.recap": "sessionId", "coordination.diagnostics": "sessionId",
            "access.get": "sessionId", "access.set": "sessionId",
            "capability.read": "sessionId", "capability.request": "sessionId",
            "capability.grant": "sessionId", "capability.revoke": "sessionId",
            "runtime.provenance": "sessionId", "integration.capture": "sessionId",
            "integration.check": "sessionId", "integration.read": "sessionId",
            "integration.list": "sessionId",
            **{name: 'sessionId' for name in ('workspace.resources.managed', 'workspace.resources.provision',
                'workspace.resources.read', 'workspace.resources.retire')},
            "integration.validate": "sessionId", "integration.collect": "sessionId",
            "assignment.read": "sessionId", "assignment.start": "sessionId",
            "assignment.set": "sessionId", "assignment.stop": "sessionId", "assignment.detach": "sessionId",
            "assignment.integration": "sessionId",
            "coordination.watch": "sessionId", "coordination.watch.read": "sessionId", "coordination.watch.cancel": "sessionId",
        }.get(action)
        if key is None:
            raise AppError("This action is not a session command.")
        args = payload.get("args", {})
        if not isinstance(args, dict) or (key in args and args[key] != identity):
            raise AppError("The command targets a different session.")
        from .device_pairing import command_admission
        task = service._task(service.dispatch(action, {**args, key: identity}, command_id=command_id,
            expected_revision=payload.get("expectedRevision"), include_state=False, admission_check=command_admission(request)))
        result = await asyncio.shield(task)
        if request.get('companion_device'):
            from .device_pairing import companion_receipt
            return web.json_response(companion_receipt(result))
        return web.json_response({**result, **session_snapshot(identity)})

    async def events(request):
        require_client()
        identity = request.match_info["session_id"]
        service._session(identity)
        await service.history.ensure_loaded(identity)
        await service._flush_pending_progress()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        task = asyncio.current_task()
        streams.add(task)
        queue = service.subscribe(session_id=identity)
        snapshot = service.session_state(identity)
        try:
            while True:
                data = session_snapshot(identity, snapshot)
                event_id = f"{service.instance_id}:{data['revision']}"
                await response.write(f"event: snapshot\nid: {event_id}\ndata: {json.dumps(data)}\n\n".encode())
                if data.get("deleted"):
                    break
                try:
                    snapshot = await asyncio.wait_for(queue.get(), 20)
                except TimeoutError:
                    await response.write(b": heartbeat\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            service.unsubscribe(queue)
            streams.discard(task)
        return response

    app.router.add_post("/api/clients/attach", attach)
    app.router.add_get("/api/sessions", sessions)
    app.router.add_get("/api/sessions/{session_id}", state)
    app.router.add_post("/api/sessions/{session_id}/commands", commands)
    app.router.add_get("/api/sessions/{session_id}/events", events)
