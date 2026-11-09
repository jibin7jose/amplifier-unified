"""Authenticated HTTP(S) host, event stream and bundled SPA delivery."""
from __future__ import annotations
import asyncio
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from . import __version__
from .attachments import MAX_REQUEST_BYTES
from .auth import auth_required, control_token, data_identity, login_page, post_login, session_secret, validated_data_dir, with_history_scope
from .deployment import canonical_host, load_server_config, validate_origin, validate_server
from .service import AppError, AppService
from .runtime import RuntimeOperationPending
from .live_clients import client_context
from .setup_page import detect_platform, render_setup_page
from .tls import ca_bytes
from .request_timing import request_timing
from .static_files import BundledFileResponse, BundledStaticResource
from . import browser_entry


# These exact actions only observe revisions; they never admit the work being
# observed. Do not infer cancellation safety from an action's name or suffix.
_READ_ONLY_WAIT_ACTIONS = frozenset({
    "workspace.work.wait", "assignment.tasks.wait", "coordination.wait",
    "operations.wait", "recall.wait",
})


def _set_response_headers(response: web.StreamResponse, request: web.Request) -> web.StreamResponse:
    path = request.path
    # Some browsers do not include ws/wss in CSP self. Name only this validated
    # authority; allow either scheme for deployments terminating TLS upstream.
    try:
        authority = urlsplit(validate_origin(f"{request.scheme}://{request.host}")).netloc
        sockets = f"ws://{authority} wss://{authority}"
    except ValueError:
        sockets = ""
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Chromium sends Origin: null on navigation POSTs from a no-referrer
    # document. Keep the login form same-origin without leaking referrers to
    # other sites; do not weaken the Origin check to accept opaque origins.
    response.headers["Referrer-Policy"] = "same-origin" if path in {"/login", "/devices", "/devices/pair"} or path.startswith('/connect') else "no-referrer"
    response.headers["Cache-Control"] = "no-store" if path.startswith(("/api/", "/share/", "/devices", "/connect")) or path in {"/login", "/oauth/mcp/callback", "/oauth/mcp/complete"} else "no-cache"
    response.headers.setdefault("Content-Security-Policy", f"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; frame-src 'self' http: https:; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self' {sockets} https://api.openai.com wss://api.openai.com; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'")
    if path == "/login" and browser_entry.is_windows():
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
    from .device_connections import response_headers
    return response_headers(request, response)


@web.middleware
async def boundaries(request, handler):
    """Reject rebinding hosts and cross-origin callers before app actions."""
    try:
        host = canonical_host(urlsplit("//" + request.host).hostname or "")
    except ValueError:
        host = ""
    if host not in request.app["permitted_hosts"]:
        return web.json_response({"error": "This Host is not configured."}, status=403)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        _set_response_headers(exc, request)
        raise
    except RuntimeOperationPending as exc:
        payload = {'error': str(exc), 'code': 'runtime_pending'}
        if exc.operation == 'send':
            payload['delivery'] = 'unknown'
        return _set_response_headers(web.json_response(payload, status=504), request)
    except AppError as exc:
        payload = {"error": str(exc), "accepted": False}
        if exc.code:
            payload['code'] = exc.code
        if exc.receipt is not None:
            from .device_pairing import companion_receipt
            payload['receipt'] = companion_receipt(exc.receipt) if request.get('companion_device') else exc.receipt
        if exc.code == 'session_busy' and not request.get('companion_device'):
            payload['state'] = getattr(exc, 'client_state', None) or request.app['service'].browser_state()
        return _set_response_headers(web.json_response(payload, status=exc.status), request)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        return _set_response_headers(web.json_response({"error": "Invalid request: " + str(exc), "accepted": False}, status=400), request)
    return _set_response_headers(response, request)


async def create_app(data_dir, workspace=None, runtime=None, voice=True, background_updates=True,
                     preload_providers=True, server_config=None, background_work=True):
    from .windows_proactor import install as install_socket_cleanup
    socket_cleanup = install_socket_cleanup()
    # Validate even with a supplied config. Resolving a Windows address first
    # could erase a junction/alias before the native boundary can reject it.
    data_dir = validated_data_dir(data_dir, create=True)
    config = validate_server(server_config) if server_config is not None else load_server_config(data_dir)
    app = web.Application(middlewares=[request_timing, boundaries, auth_required, client_context], client_max_size=MAX_REQUEST_BYTES)
    app["windows_socket_cleanup"] = socket_cleanup
    app["server_config"] = config
    app["permitted_hosts"] = frozenset({"localhost", "127.0.0.1", "::1"} |
                                       {canonical_host(urlsplit(origin).hostname) for origin in config["public_origins"]})
    app["allowed_origins"] = frozenset(config["public_origins"]) | frozenset(
        validate_origin(f"{scheme}://{host}:{config['port']}")
        for scheme in ("http", "https")
        for host in ("localhost", "127.0.0.1", "[::1]")
    )
    app["session_secret"] = session_secret(data_dir)
    app["control_token"] = control_token(data_dir)
    service = AppService(data_dir, runtime=runtime, workspace=workspace)
    service.port = config["port"]
    service.server_config = config
    from .profiling_host import ProfilingHost
    service.profiling_host = ProfilingHost(service, config)
    if runtime is None:
        from .runtime import RuntimeManager
        runtime = RuntimeManager(app_bridge=service.app_bridge, retention=config["runtime"],
                                 performance_diagnostics=service.performance_diagnostics)
        await service.install_runtime(runtime)
        service.state["runtime"]["available"] = True
    if hasattr(runtime, 'retention'):
        service.state['runtime']['retention'] = dict(runtime.retention.settings)
    app["service"] = service
    browser_entry.initialize(app)

    async def mcp_oauth_callback(request):
        await service.smart_tools.oauth.callback(request.query, validate_origin(f"{request.scheme}://{request.host}"))
        raise web.HTTPSeeOther("/oauth/mcp/complete", headers={"Cache-Control":"no-store", "Referrer-Policy":"no-referrer"})

    async def mcp_oauth_complete(request):
        return web.Response(text="Authorization response received. Return to Amplifier Unified to check the connection.")

    app.router.add_get("/oauth/mcp/callback", mcp_oauth_callback, allow_head=False)
    app.router.add_get("/oauth/mcp/complete", mcp_oauth_complete)
    service.bind_runtime_alias(lambda current: app.__setitem__("runtime", current))
    from .voice_visual import setup_routes as visual_routes
    visual_routes(app)
    from .computer_visual import setup_routes as computer_visual_routes
    computer_visual_routes(app)
    from .terminal_setup import setup_routes as setup_terminal
    setup_terminal(app)
    from .device_pairing import setup_routes as setup_device_pairing
    setup_device_pairing(app)
    from .device_connections import setup_routes as setup_device_connections
    setup_device_connections(app)
    from .device_connection_pages import setup_routes as setup_connection_pages
    setup_connection_pages(app)
    from .ui_use import setup_routes as setup_ui_use
    setup_ui_use(app)
    from .smart_tools import SmartToolsManager
    from .smart_canvas import SmartCanvas
    service.smart_tools = SmartToolsManager(service)
    service.smart_canvas = SmartCanvas(service)
    service._publish_full(reason='Startup attaches and reconciles application controllers')
    from .management import Management
    service.management = Management(service)
    if background_work:
        service.diagnostics.start()
        service.schedules.start()
        service.collaboration.start()
        service.worktrees.start()
        service.test_queue.start()
    service.history.start()
    service.event_log_view.start()
    service.performance_diagnostics.start()
    if preload_providers:
        service.management.background(service.management.command("providers.list", {}))
    from .updates import UpdateManager
    service.update_manager = UpdateManager(service)
    if background_updates:
        service.update_manager.task = asyncio.create_task(service.update_manager.loop())
    streams = set()
    call_waiters = set()
    action_waiters = set()

    async def state(request):
        session_id = request.query.get('sessionId')
        if session_id is not None and (not session_id or len(session_id) > 200):
            raise AppError("Choose a valid conversation ID.")
        await service._flush_pending_progress()
        return web.json_response(with_history_scope(request, service.browser_state(session_id=session_id)))

    async def state_detail(request):
        from .agent_state import read_state
        args = {"path": request.query.get("path", "")}
        for key in ("offset", "limit", "revision"):
            if key in request.query:
                args[key] = int(request.query[key])
        if 'directoryRevision' in request.query:
            args['directoryRevision'] = request.query['directoryRevision']
        await service._flush_pending_progress()
        return web.json_response(read_state(service.state_context(), args, resolve=service.state_resource, directory=service.directory))

    async def conversation_detail(request):
        from .browser_detail import page, read_text
        if request.query.get('peerPreview'):
            return web.json_response(await service.peer_preview.read_text(dict(request.query)))
        session = service._session(request.query.get('sessionId'), hydrate=False)
        from .work_windows import web_context
        windows = service.event_log_view.windows
        if 'field' in request.query:
            if (request.query.get('windowKey') or '').startswith('resident-work:'):
                return web.json_response(windows.live_pages.read_text(session, dict(request.query)))
            if request.query.get('windowKey'):
                owner = service.history.windows if request.query.get('part') == 'messages' else windows
                return web.json_response(await owner.read_text(session['id'], dict(request.query)))
            return web.json_response(await asyncio.to_thread(read_text, session, dict(request.query)))
        part = request.query.get('part')
        if web_context(service):
            if part == 'tool':
                return web.json_response(await windows.read_tool(session['id'], dict(request.query)))
            if part == 'messages':
                return web.json_response(await service.history.windows.read(session['id'],
                    before=request.query.get('before'), native=request.query.get('native') == 'true'))
            if part == 'groups':
                return web.json_response(await windows.read(session['id'], before=request.query.get('before')))
            if part == 'nodes' and (request.query.get('group') or '').startswith('event-window:'):
                return web.json_response(windows.nodes(session['id'], request.query['group'],
                    revision=request.query.get('revision')))
            if part == 'nodes':
                return web.json_response(windows.live_pages.read(session, request.query.get('group'),
                    before=request.query.get('before'), revision=request.query.get('revision')))
        session = service._session(session['id'])
        return web.json_response(page(session, request.query.get('part'), request.query.get('before'),
                                      group=request.query.get('group'), revision=request.query.get('revision'),
                                      window_message_id=request.query.get('windowMessageId'),
                                      resolver=service.peer_attribution.resolve))

    async def provider_recording(request):
        from .provider_payloads import open_record
        field = request.query.get('field')
        if field not in {'request', 'response'}:
            raise ValueError('Choose a provider request or response.')
        windows = service.event_log_view.windows
        page = resident = None
        if (request.query.get('windowKey') or '').startswith('resident-work:'):
            session = service._session(request.query.get('sessionId'), hydrate=False)
            resident, node, reference, recording_receipt = windows.live_pages.recording(session, request.query)
        elif request.query.get('windowKey'):
            session, page, node, reference = windows.recording(request.query.get('sessionId'), request.query)
        else:
            session = service._session(request.query.get('sessionId'))
            node = next((row for row in session.get('execution', {}).get('nodes', [])
                         if row.get('id') == request.query.get('id') and row.get('kind') == 'llm'), None)
            reference = (node or {}).get('_eventFields', {}).get(field)
        if not reference:
            raise ValueError('This provider recording is no longer available.')
        opening = asyncio.create_task(asyncio.to_thread(open_record, reference, request.query.get('digest')))
        try:
            stream, info = await asyncio.shield(opening)
        except asyncio.CancelledError:
            # A worker thread cannot be cancelled. Close its verified handle
            # when it completes even if the browser disconnected meanwhile.
            def release(task):
                if not task.cancelled() and task.exception() is None:
                    task.result()[0].close()
            opening.add_done_callback(release)
            raise
        try:
            if page is not None:
                windows.check_recording(session, page)
            if resident is not None:
                windows.live_pages.check_recording(session, request.query, resident, recording_receipt)
            header = {'schemaVersion': 1, 'kind': 'provider.' + field, 'callId': node['id'],
                      'sessionId': node.get('sessionId'), 'rootSessionId': session['id'],
                      'provider': node.get('provider'), 'model': node.get('model'),
                      'capture': info}
            prefix = (json.dumps(header)[:-1] + ', "payload":').encode()
            suffix = '-partial' if info['truncated'] else ''
            response = web.StreamResponse(headers={
                'Content-Type': 'application/json; charset=utf-8',
                'Content-Disposition': f'attachment; filename="provider-{field}-{info["sha256"][:16]}{suffix}.json"',
                'Content-Length': str(len(prefix) + info['bytes'] + 1),
            })
            _set_response_headers(response, request)
            await response.prepare(request)
            await response.write(prefix)
            while chunk := await asyncio.to_thread(stream.read, 64 * 1024):
                await response.write(chunk)
            await response.write(b'}')
            await response.write_eof()
            return response
        finally:
            stream.close()

    async def conversation_navigation(request):
        from .conversation_navigation import query
        session = service._session(request.query.get('sessionId'))
        result = await asyncio.to_thread(query, session,
            message_id=request.query.get('messageId'), window=request.query.get('window') == 'true',
            _raw_window=True)
        if 'messages' in result:
            # SQLite belongs to this event loop, not the transcript-read worker.
            # Bind raw text before compacting; a >4096-character prompt must not
            # lose its provenance merely because its display body is deferred.
            from .browser_detail import compact, TEXT_LIMIT, enrich_recovery
            from .message_interactions import annotate
            from .peer_attribution import derive, display_annotations
            rows = derive(session, result['messages'], service.peer_attribution.resolve)
            annotation_source = {'messageAnnotations': display_annotations(session)}
            messages = []
            for row in rows:
                item = compact(annotate(annotation_source, row), session['id'], 'messages', TEXT_LIMIT)
                if row.get('attribution'):
                    item['attribution'] = row['attribution']
                messages.append(item)
            enrich_recovery(session, messages)
            result['messages'] = messages
        return web.json_response(result, headers={'Cache-Control':'no-store'})

    async def conversation_work_sync(request):
        from .browser_detail import sync_work
        args = await request.json()
        if (args.get('group') or '').startswith('event-window:'):
            known = args.get('known')
            if not isinstance(known, dict) or len(known) > 100 or not all(isinstance(k, str) and isinstance(v, str) for k, v in known.items()):
                raise ValueError('Work versions must map at most100 step IDs to strings.')
            session = service._session(args.get('sessionId'), hydrate=False)
            return web.json_response(service.event_log_view.windows.nodes(session['id'], args['group'], known=known))
        session = service._session(args.get('sessionId'), hydrate=False)
        from .work_windows import web_context
        if web_context(service):
            return web.json_response(service.event_log_view.windows.live_pages.sync(session, args.get('group'), args.get('known')))
        session = service._session(session['id'])
        return web.json_response(sync_work(session, args.get('group'), args.get('known'),
                                           window_message_id=args.get('windowMessageId')))

    async def conversation_export(request):
        snapshot = service.state.get('conversationExports', {}).get(request.match_info['identity'])
        if snapshot is None:
            raise AppError('This conversation export is unavailable.', 404)
        return web.json_response({'content': service.state_resource(snapshot['content']['$resource']),
                                  'filename': snapshot['filename'], 'mimeType': snapshot['mimeType']},
                                 headers={'Cache-Control': 'no-store'})

    async def output_content(request):
        from urllib.parse import quote
        record=service.outputs.store.read(request.match_info['identity'])
        if record.get('kind') == 'media':
            raise AppError('Use the exact client/session/message media route.',403)
        from . import retained_files
        if record.get('bodyFormat') == retained_files.FORMAT:
            from .resource_files import settle_worker
            filename=Path(record.get('filename','output')).name
            async with retained_files.download(service,record) as stream:
                response=web.StreamResponse(headers={
                    'Content-Type':'application/octet-stream', 'Content-Length':str(record['bytes']),
                    'Content-Disposition': "attachment; filename*=UTF-8''"+quote(filename,safe=''),
                    'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; sandbox"})
                _set_response_headers(response, request)
                await response.prepare(request)
                while data := await settle_worker(stream.read,retained_files.CHUNK):
                    await response.write(data)
                await response.write_eof()
                return response
        data=await service.outputs.content_async(record)
        if data is None:
            raise AppError('This output is an external reference without saved content.',404)
        filename=Path(record.get('filename','output')).name
        return web.Response(body=data,content_type='application/octet-stream',headers={
            'Content-Disposition': "attachment; filename*=UTF-8''"+quote(filename,safe=''),
            'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; sandbox"})

    async def output_media_metadata(request):
        result = await service.outputs.media.describe(request.query['sessionId'], request.query['messageId'])
        return web.json_response(result)

    async def output_media_bytes(request):
        from .output_media import byte_range, MediaRangeError
        from urllib.parse import quote
        values = {key: request.query[key] for key in ('sessionId', 'messageId', 'version', 'sha256')}
        values['id'] = request.match_info['identity']
        record = await service.outputs.media.guarded(values)
        # Auth middleware and tuple/client guards precede body and Range parsing.
        if request.method == 'HEAD':
            size = await service.outputs.media.head(values)
            data = None
        else:
            data = await service.outputs.media.bytes(values)
            size = len(data)
        headers = {'Accept-Ranges':'bytes','ETag':'"' + record['sha256'] + '"'}
        try:
            interval = byte_range(request.headers.get('Range'), size)
        except MediaRangeError as exc:
            if exc.status == 416:
                headers['Content-Range'] = 'bytes */' + str(size)
            return web.Response(status=exc.status, headers=headers)
        status = 200
        if interval is not None:
            start, end = interval
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            if data is not None:
                data = data[start:end + 1]
            size = end - start + 1
            status = 206
        if request.query.get('download') == '1':
            headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(record['filename'], safe='')
        headers['Content-Length'] = str(size)
        return web.Response(body=data if request.method != 'HEAD' else b'', status=status,
                            content_type=record['media']['mimeType'], headers=headers)

    async def output_image(request):
        import base64
        record=service.outputs.store.read(request.match_info['identity'])
        try:
            value=service.outputs.image(record['sessionId'],record['id'],record.get('sha256'))
        except ValueError as exc:
            raise AppError(str(exc),409) from None
        return web.Response(body=base64.b64decode(value['_image']),content_type='image/png',headers={
            'Content-Security-Policy': "default-src 'none'; frame-ancestors 'self'; sandbox"})

    async def actions(request):
        if request.method == "GET":
            return web.json_response(service.get_actions())
        payload = await request.json()
        if isinstance(payload, dict) and payload.get('action') == 'terminal.prepare':
            from .terminal_setup import require_safe_transport
            require_safe_transport(request)
        compact = request.get('companion_device') or request.headers.get('X-Amplifier-State-Transport') == 'delta-v1'
        from .device_pairing import command_admission
        read_wait = isinstance(payload["action"], str) and payload["action"] in _READ_ONLY_WAIT_ACTIONS
        if read_wait and service.closed:
            raise AppError("The host is shutting down.", 503, code="host_shutdown")
        # No yield between the fence check and ownership registration. A wait
        # cannot enter after shutdown snapshots this set, including requests
        # whose JSON body arrived across that boundary.
        task = service._task(service.dispatch(payload["action"], payload.get("args", {}), origin="ui",
                                        command_id=payload.get("id"), expected_revision=payload.get("expectedRevision"),
                                        include_state=not compact, admission_check=command_admission(request)))
        if read_wait:
            action_waiters.add(task)
            task.add_done_callback(action_waiters.discard)
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            if read_wait and service.closed and task.cancelled():
                # A retired observation is not an accepted/timed-out result.
                # Effectful dispatches retain their independent shielding.
                raise AppError("The host is shutting down.", 503, code="host_shutdown") from None
            raise
        # Reads and validated no-ops can be complete without a state publication.
        # Consume this marker in both transports; clients never author it.
        state_independent = result.get('_stateIndependent') is True
        delivery_revision = result.get('_deliveryStateRevision')
        result = {key: value for key, value in result.items() if key not in {'_stateIndependent', '_deliveryStateRevision'}}
        if compact:
            # Some delegated action families return their own snapshot. Keep
            # their receipt/effects, but use the same stream synchronization.
            result = {key: value for key, value in result.items() if key != 'state'}
            # Shell-only receipts have their own revision domain and notify
            # /api/shell. They must not wait for an unrelated app-state frame.
            if not state_independent and (not payload['action'].startswith('shell.') or payload['action'] == 'shell.command'):
                result['stateRevision'] = result.get('revision', service._state['revision'])
                if delivery_revision is not None:
                    result['stateRevision'] = delivery_revision
            result['hostInstanceId'] = service.instance_id
        if request.get('companion_device'):
            from .device_pairing import companion_receipt
            result = companion_receipt(result)
        return web.json_response(result)

    async def profiling_content(request):
        content = await service.profiling_host.content(request.match_info["identity"])
        return web.Response(text=content, content_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="profile.json"',
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox"})

    app.router.add_get("/api/debug/profiles/{identity}", profiling_content, allow_head=False)

    async def view(request):
        await service.update_device(await request.json())
        return web.json_response({"accepted": True})

    async def health(request):
        response = {"ok": True, "app": "amplifier-unified", "version": __version__}
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer ") and hmac.compare_digest(authorization[7:], app["control_token"]):
            response["dataIdentity"] = data_identity(service.data_dir)
            response.update({key: service.update_manager.running_identity[key] for key in ('revision', 'instanceId')})
            response["runtime"] = dict(service.state["runtime"])
        return web.json_response(response)

    async def performance_diagnostics(request):
        # Authentication is the same as the existing diagnostic reads. No
        # capture is started, app snapshot built, or state publication made.
        return web.json_response({'hostInstanceId': service.instance_id,
            'version': __version__, **service.performance_diagnostics.snapshot(),
            'recentRequests': list(getattr(service, 'request_timings', []))})

    async def certificate(request):
        certificate = ca_bytes(data_dir)
        if certificate is None:
            raise web.HTTPNotFound(text="A local CA is not configured.")
        return web.Response(body=certificate, content_type="application/x-x509-ca-cert",
                            headers={"Content-Disposition": 'attachment; filename="amplifier-unified-ca.crt"'})

    async def setup(request):
        certificate = ca_bytes(data_dir)
        from .tls import ca_fingerprint
        return web.Response(
            text=render_setup_page(
                detect_platform(request.headers.get("User-Agent")),
                certificate is not None,
                ca_fingerprint(data_dir),
            ),
            content_type="text/html",
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"},
        )

    async def events(request):
        from .browser_events import serve
        return await serve(request, service, streams)

    async def attachment(request):
        from .attachments import file_path
        try:
            path, row = file_path(service.data_dir, request.match_info["identity"])
        except (ValueError, FileNotFoundError):
            raise AppError("Attachment not found", 404)
        from urllib.parse import quote
        response = web.FileResponse(path)
        response.headers["Content-Type"] = row["mime"]
        response.headers["Content-Disposition"] = ("inline" if row["mime"].startswith("image/") else "attachment") + "; filename*=UTF-8''" + quote(row["name"])
        return response

    from .canvas_documents import canvas_source, raw_source

    def canvas_view_target(request, view_id):
        try:
            args = {'viewId': view_id, 'resourceId': request.query['resourceId'],
                    'resourceRevision': request.query['resourceRevision'], 'generation': int(request.query['generation'])}
        except (KeyError, ValueError):
            raise AppError('Supply the artifact view identity and revision.') from None
        service.canvas_views.target(args)
        return service.canvas_views.canvas(view_id)

    async def canvas_view_resource(request):
        canvas = canvas_view_target(request, request.match_info['view_id'])
        return web.json_response(canvas, headers={'Cache-Control': 'no-store'})

    async def canvas_view_source(request):
        canvas = canvas_view_target(request, request.match_info['view_id'])
        return web.json_response({'content': raw_source(canvas, service.db)}, headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/canvas/views/{view_id}/resource', canvas_view_resource)
    app.router.add_get('/api/canvas/views/{view_id}/source', canvas_view_source)

    def requested_canvas_version(request, row):
        from .canvas_versions import definition
        try:
            version = int(request.query['version']) if 'version' in request.query else None
        except ValueError:
            raise AppError('Choose a valid saved version.') from None
        return definition(row, version, service.db)

    async def canvas_download(request):
        from .state_storage import resource
        from urllib.parse import quote
        identity = request.match_info["identity"]
        row = next((row for row in service.state.get("canvasArtifacts", []) if row["id"] == identity), None)
        if not row:
            raise AppError("Canvas artifact unavailable", 404)
        row = requested_canvas_version(request, row)
        canvas = {**row, **resource(service.db, row["body"]["$resource"])}
        from .canvas_downloads import filename
        if canvas.get('kind') in {'image', 'pdf'}:
            from .canvas_downloads import image_bytes, pdf_bytes
            data, mime = (pdf_bytes if canvas['kind'] == 'pdf' else image_bytes)(canvas)
            return web.Response(body=data, content_type=mime, headers={
                'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename(canvas), safe=''),
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return web.Response(text=canvas_source(canvas,service.db), content_type="text/html",
                            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename(canvas), safe='')})

    async def canvas_source_text(request):
        from .state_storage import resource
        row=next((r for r in service.state.get('canvasArtifacts',[]) if r['id']==request.match_info['identity']),None)
        if not row or row.get('kind') not in {'html','babylon','canvas-app'}:
            raise AppError('Canvas source unavailable',404)
        row = requested_canvas_version(request, row)
        canvas={**row, **({} if row.get('contentResource') else resource(service.db,row['body']['$resource']))}
        return web.Response(text=raw_source(canvas,service.db),content_type='text/plain',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    async def canvas_app_host(request):
        canvas = canvas_view_target(request, request.query.get('viewId', 'primary'))
        if canvas.get('id') != request.match_info['identity'] or not canvas.get('app'):
            raise AppError('Interactive surface no longer available.', 404)
        from .canvas_app_host import document_response
        return document_response(canvas, request)

    app.router.add_get('/api/canvas/{identity}/app-host', canvas_app_host)

    async def canvas_document(request):
        canvas = (canvas_view_target(request, request.query['viewId']) if request.query.get('viewId')
                  else service.state.get("canvas", {}))
        if canvas.get("id") != request.match_info["identity"] or canvas.get("kind") not in {"html", "babylon", "mcp-app", "canvas-app"}:
            raise AppError("Canvas document no longer available", 404)
        if canvas.get("kind") == "mcp-app":
            from .smart_canvas import document_response
            from .mcp_view_recovery import source
            return document_response({**canvas, 'content': source(service, canvas['id'])})
        identity = json.dumps(canvas["id"])
        bridge = 'canvas_app_bridge.js' if canvas.get('app') else 'canvas_bridge.js'
        bootstrap = "<!doctype html><script data-canvas-bridge>" + (Path(__file__).parent / bridge).read_text().replace("__CANVAS_ID__", identity) + "</script>"
        return web.Response(text=bootstrap + canvas_source(canvas,service.db), content_type="text/html", headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=()"})

    def check_tool_view(request):
        if request.query.get('viewId'):
            canvas = canvas_view_target(request, request.query['viewId'])
            if canvas.get('id') != request.match_info['identity']:
                raise AppError('This saved tool view changed.', 409)

    async def smart_canvas_status(request):
        check_tool_view(request)
        from .mcp_view_recovery import inspect
        return web.json_response(inspect(service, request.match_info['identity']), headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/canvas/{identity}/status', smart_canvas_status)

    async def smart_canvas_saved_result(request):
        check_tool_view(request)
        from .mcp_view_recovery import saved
        from .state_storage import resource
        canvas, _ = saved(service, request.match_info['identity'])
        reference = canvas['mcp'].get('savedResult')
        return web.json_response({'result': resource(service.db, reference['$resource']) if reference else None}, headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/canvas/{identity}/result', smart_canvas_saved_result)


    async def smart_canvas_tools(request):
        check_tool_view(request)
        _, binding = service.smart_canvas.binding(request.match_info['identity'])
        server = next(s for s in service.state['smartTools']['servers'] if s['id'] == binding['serverId'])
        tools = [t for t in await service.smart_tools.list_tools(server['id'], origin='app') if t['name'] in binding['allowedTools']]
        check_tool_view(request)
        return web.json_response({'tools':tools})

    app.router.add_get('/api/canvas/{identity}/tools', smart_canvas_tools)

    async def smart_canvas_call(request):
        check_tool_view(request)
        # The same admitted action, visibility checks and durable receipt as
        # app_control, returned immediately on completion instead of browser polling.
        payload = await request.json()
        if not isinstance(payload, dict):
            raise AppError('A tool request must be a JSON object.')
        identity = payload.get('id')
        if not isinstance(identity, str) or not 1 <= len(identity) <= 100:
            raise AppError('A stable request ID is required.')
        waiter = asyncio.current_task()
        call_waiters.add(waiter)
        try:
            accepted = await service.dispatch('smartTools.appCall', {
                'canvasId': request.match_info['identity'],
                'name': payload.get('name'), 'arguments': payload.get('arguments', {}),
                **({'expectedRevision': payload['expectedRevision']} if 'expectedRevision' in payload else {}),
            }, command_id=identity, origin='ui', include_state=False)
            operation = await service.wait_smart_tool(accepted['operationId'])
            return web.json_response(operation, headers={'Cache-Control': 'no-store'})
        finally:
            call_waiters.discard(waiter)

    app.router.add_post('/api/canvas/{identity}/tools/call', smart_canvas_call)

    async def smart_canvas_resource(request):
        check_tool_view(request)
        try:
            result = await service.smart_canvas.resource(
                request.match_info['identity'], request.query.get('kind', 'read'),
                uri=request.query.get('uri'), cursor=request.query.get('cursor'))
        except ValueError as exc:
            raise AppError(str(exc), 400) from None
        check_tool_view(request)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/canvas/{identity}/resources', smart_canvas_resource)

    async def smart_operation(request):
        operation = service.smart_tools.operation(request.match_info['identity'])
        if not operation:
            return web.json_response({'status':'pending'}, status=200)
        return web.json_response(operation)

    app.router.add_get('/api/smart-tools/operations/{identity}', smart_operation)
    async def shared_conversation(request):
        async with service.lock:
            content = service.conversation_library.public_snapshot(request.match_info['token'])
        if content is None:
            raise web.HTTPNotFound(text='This snapshot link is unavailable.')
        return web.Response(text=content, content_type='text/html', headers={
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            'X-Robots-Tag': 'noindex, nofollow, noarchive'})
    app.router.add_get('/share/{token}', shared_conversation)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", post_login)
    app.router.add_post(browser_entry.ISSUE_PATH, browser_entry.issue)
    app.router.add_post(browser_entry.REDEEM_PATH, browser_entry.redeem)
    app.router.add_get("/setup", setup)
    app.router.add_get("/api/ca", certificate)
    app.router.add_get("/ca.crt", certificate)
    app.router.add_get("/api/canvas/{identity}/download", canvas_download)
    app.router.add_get("/api/canvas/{identity}/document", canvas_document)
    app.router.add_get("/api/canvas/{identity}/source", canvas_source_text)
    app.router.add_get("/api/attachments/{identity}", attachment)
    app.router.add_get("/api/outputs/{identity}/content", output_content)
    app.router.add_get("/api/outputs/media", output_media_metadata)
    app.router.add_get("/api/outputs/{identity}/media", output_media_bytes)
    app.router.add_get("/api/outputs/{identity}/image", output_image)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/diagnostics/performance", performance_diagnostics)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/state/detail", state_detail)
    from .message_interactions import install_routes as install_message_annotation_routes
    install_message_annotation_routes(app)
    from .conversation_stop import install_routes as install_conversation_stop_routes
    install_conversation_stop_routes(app)
    async def peer_preview(request):
        return web.json_response(await service.peer_preview.read(dict(request.query)))

    app.router.add_get("/api/peer/preview", peer_preview)
    app.router.add_get("/api/conversation/detail", conversation_detail)
    app.router.add_get("/api/conversation/provider-recording", provider_recording)
    app.router.add_get("/api/conversation/navigation", conversation_navigation)
    app.router.add_post("/api/conversation/work-sync", conversation_work_sync)
    app.router.add_get('/api/conversation/exports/{identity}', conversation_export)
    app.router.add_get("/api/actions", actions)
    app.router.add_post("/api/actions", actions)
    app.router.add_post("/api/view", view)
    app.router.add_get("/api/events", events)
    app.router.add_get("/api/events/ws", events, allow_head=False)
    from .live_clients import setup_routes as setup_clients
    setup_clients(app, streams)
    from .client_observations import setup_routes as setup_observations
    setup_observations(app)
    from .companion import setup_routes as setup_companion
    setup_companion(app)

    async def shell_state(request):
        from .shell_modules import IDENTITY
        from .shell_navigation_demand import parse_navigation_resources
        from jsonschema import validate, ValidationError
        client_id = request.query.get('clientId', '')
        try:
            validate(client_id, IDENTITY)
        except ValidationError:
            raise AppError('A valid shell clientId is required.') from None
        navigation_resources = parse_navigation_resources(request.query.get('navigationResources'))
        bundled = request.query.get('bundled') == '1'
        if bundled:
            demand_id, scope = shell_demand(request.query.get('demandId'), request.query.get('scope'))
        # inspect may initialize a client and persist appearance defaults.
        # It must not commit another operation's resource savepoints.
        async with service.lock:
            if bundled:
                service.shell.resources.register(client_id, demand_id, navigation_resources, scope,
                                                 recovery=request.query.get('recovery') == '1')
                service._client_snapshots.pop(client_id, None)
            result = service.shell.inspect(client_id, snapshots=True, recovery=request.query.get('recovery') == '1',
                                           navigation_resources=navigation_resources, bundled=bundled)
        if request.query.get('compact') == '1' and not bundled:
            from .shell_wire import compact
            result = compact(result)
        from .performance_diagnostics import count
        count(service, 'shell.bootstrap.read' if bundled else 'shell.inspect.read')
        return web.json_response(result)

    def shell_demand(demand, scope):
        try:
            if not isinstance(demand, str) or not demand.isascii() or not demand.isdecimal() or len(demand) > 16:
                raise ValueError
            if not isinstance(scope, str) or len(scope) > 4096:
                raise ValueError
            return int(demand), json.loads(scope)
        except (ValueError, RecursionError):
            raise AppError('Supply a valid shell demand and scope.', 400) from None

    async def shell_resources(request):
        payload = await request.json()
        if (not isinstance(payload, dict) or set(payload) - {'clientId', 'demandId', 'resources', 'scope', 'reason'}
                or not {'clientId', 'demandId', 'resources', 'scope'} <= set(payload)):
            raise AppError('Invalid shell resource request.', 400)
        # Labels are fixed diagnostics, never free-form input, filters or paths.
        reason = payload.get('reason', 'demand')
        if reason not in {'demand', 'scope', 'reconnect', 'resume', 'missing', 'retry', 'visibility', 'manual'}:
            raise AppError('Invalid shell resource request reason.', 400)
        identity = payload['clientId']
        if not isinstance(identity, str) or identity != service.clients.current.get():
            raise AppError('Shell resources must target this attached client.', 400)
        async with service.lock:
            service.shell.resources.register(identity, payload['demandId'], payload['resources'], payload['scope'],
                recovery=service.shell.resources.clients.get(identity, {}).get('recovery', False))
            if reason in {'retry', 'manual'}:
                # Attach this validated demand to failed current query keys
                # before clearing cooldowns (including a newer demand ID).
                service.shell.resources.snapshot(identity)
                service.shell.resources.queries.retry(identity)
            service._client_snapshots.pop(identity, None)
            result = service.shell.resources.snapshot(identity)
        from .performance_diagnostics import count
        count(service, 'shell.resources.request.' + reason)
        return web.json_response(result)

    async def shell_package(request):
        digest = request.match_info['digest']
        async with service.lock:
            service.shell.manifest(digest)
            source = service.shell.source(digest)
        return web.Response(text=source, content_type='text/javascript', headers={'Cache-Control': 'no-store'})

    app.router.add_get('/api/shell', shell_state)
    app.router.add_post('/api/shell/resources', shell_resources)
    app.router.add_get('/api/shell/packages/{digest}.mjs', shell_package)

    if voice:
        from .voice import setup_routes
        service.voice_service = setup_routes(app)

    static = Path(__file__).parent / "static"

    async def index(request):
        file = static / "index.html"
        if not file.exists():
            raise AppError("Frontend assets are missing. Build frontend before packaging.", 503)
        return BundledFileResponse(file)

    app.router.add_get("/", index)
    if static.exists():
        app.router.register_resource(BundledStaticResource("", static, show_index=False))

    async def shutdown(app):
        service.begin_shutdown()
        waiting = list(streams | call_waiters | action_waiters)
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)

    app.on_shutdown.append(shutdown)

    async def confirm_started(app):
        from .update_readiness import wait_for_readiness, recovery_candidate
        if recovery_candidate(service.update_manager):
            service.update_manager.readiness_task = asyncio.create_task(
                wait_for_readiness(service.update_manager, app['control_token']))

    app.on_startup.append(confirm_started)

    async def cleanup(app):
        await service.close()

    app.on_cleanup.append(cleanup)
    return app
