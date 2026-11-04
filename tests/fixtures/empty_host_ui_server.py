"""Production host/assets, empty storage, synthetic runtime; no provider calls."""
import asyncio
import hashlib
import time
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

installed_mode = os.environ.get('AMPLIFIER_TEST_INSTALLED') == '1'
sys.path.insert(0, str(Path(__file__).resolve().parent if installed_mode else Path(__file__).resolve().parents[2]))


class Runtime:
    def __init__(self):
        self.sent = []
        self.stopped = []
        self.started = []

    async def start(self, session, emit):
        self.started.append(session["id"])
        await emit("runtime.status", {"sessionId": session["id"], "status": "ready"})

    async def send(self, session, text, input_id, emit):
        if hasattr(self, 'provider_scenario'):
            resolved = await self.provider_scenario.dispatch(session)
            await emit("runtime.status", {"sessionId": session["id"], "status": "ready",
                       "report": {"effective_selection": resolved['effective']}})
        if '--startup-failure' in sys.argv and not self.sent:
            from amplifier_web.runtime import RuntimeStartupError
            await self.start_gate.wait()
            raise RuntimeStartupError('The conversation worker could not start. This attempt did not send your message.')
        self.sent.append({"sessionId": session["id"], "text": text})
        if '--chat-controls' in sys.argv:
            self.sent[-1].update(workspace=session['workspace'], bundle=session['bundle'],
                                 selection=session.get('selection'), attachments=session['messages'][-1].get('attachments', []))
        if hasattr(self, 'service'):
            binding = session.get('surfaceInputs', {}).get(input_id, {})
            context = self.service.surface_context.manifest(session['id'], [binding])
            if context['surfaces']:
                self.sent[-1]['surfaceContext'] = context
        await emit("assistant.message", {"sessionId": session["id"],
                   "inputId": input_id, "text": text if "--canvas-versions" in sys.argv else "Synthetic first response"})
        await emit("runtime.status", {"sessionId": session["id"], "status": "idle"})

    async def control(self, *args):
        return {}

    async def stop(self, sid, *, reason="stopped"):
        self.stopped.append(sid)
        await self.service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "idle"})

    async def close(self):
        pass


async def main(home):
    if '--voice-settings' in sys.argv:
        # Credential storage only; never inherit usable provider credentials.
        os.environ.pop('OPENAI_API_KEY', None)
        os.environ.pop('AMPLIFIER_VOICE_API_KEY', None)
    os.environ.update(
        AMPLIFIER_HOME=str(home / "shared-settings"),
        AMPLIFIER_WEB_HOME=str(home / "app"),
        AMPLIFIER_SESSION_STATE_HOME=str(home / "sessions"),
    )
    from configured_browser import seed_connection
    seed_connection(home / 'shared-settings')
    from aiohttp import web
    from amplifier_web.server import create_app
    installed_identity = None
    if installed_mode:
        import importlib.metadata
        import amplifier_web.server
        expected = json.loads(Path(os.environ['AMPLIFIER_TEST_INSTALLED_IDENTITY']).read_text())
        prefix = Path(sys.prefix).resolve()
        package = Path(amplifier_web.server.__file__).resolve().parent
        assert package.is_relative_to(prefix), (package, prefix)
        assert not package.is_relative_to('/workspace')
        assert not any(Path(p).resolve().is_relative_to('/workspace') for p in sys.path if p)
        dist = importlib.metadata.distribution('amplifier-unified')
        assert Path(dist.locate_file('')).resolve().is_relative_to(prefix)
        for name, digest in expected['files'].items():
            source = Path(dist.locate_file(name)).resolve()
            assert source.is_relative_to(prefix) and hashlib.sha256(source.read_bytes()).hexdigest() == digest, name
        installed_identity = {'verified': True, 'wheelSha256': expected['wheelSha256'],
            'fileCount': len(expected['files']), 'prefix': str(prefix),
            'server': str(package / 'server.py'), 'static': expected['static']}
    import amplifier_web
    package_root = Path(amplifier_web.__file__).resolve().parent
    expected_package_root = os.environ.get('AMPLIFIER_TEST_PACKAGE_ROOT')
    installed_package_verified = bool(expected_package_root) and package_root == Path(expected_package_root).resolve()
    if expected_package_root:
        assert installed_package_verified, 'Fixture imported source instead of the installed wheel'
    static_identity = {str(p.relative_to(package_root / 'static')): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (package_root / 'static').rglob('*') if p.is_file()} if expected_package_root else None

    workspace = home / "workspace"
    workspace.mkdir(exist_ok=True)
    stored_copy_path = None
    if '--canvas-versions' in sys.argv:
        stored_copy_path = workspace / 'stored-copy.html'
        stored_copy_path.write_text('<p>Saved source α😀</p>\r\n<!--' + 'x' * 1000100 + '-->\r\n')
    runtime = Runtime()
    runtime.start_gate = asyncio.Event()
    if '--provider-resolution' in sys.argv:
        from provider_selection_scenario import Scenario
        runtime.provider_scenario = Scenario(home)
    elif '--chat-controls' in sys.argv:
        preferred_model = ['first']
        from amplifier_web.setup import SetupManager
        SetupManager.provider_rows=lambda self,workspace:[{'id':'test-provider','module':'provider-test','config':{'model':preferred_model[0]},'enabled':True}]
        async def catalog(self,action,args,workspace):
            return {'modelsProviderId':'test-provider','models':[{'id':'first'},{'id':'chosen-model'}],
                    'providerMetadata':{'module':'provider-test','info':{'display_name':'Test provider'},
                    'configSchema':{'fields':[{'id':'reasoning_effort','choices':['low','high']}]}}}
        SetupManager.cached_probe=catalog
        import amplifier_web.draft_defaults as draft_defaults
        async def resolve_defaults(home,workspace,bundle=None,app_bundle=None,**kwargs):
            return {'bundle':bundle or 'work','effective':{'instance':'test-provider','model':preferred_model[0]},
                    'providers':[{'id':'test-provider','info':{'display_name':'Test provider','defaults':{'model':preferred_model[0]}},
                    'configSchema':{'fields':[{'id':'reasoning_effort','choices':['low','high']}]}}]}
        draft_defaults.resolve_defaults=resolve_defaults
    if '--retention' in sys.argv:
        from amplifier_web.runtime import RuntimeManager
        # Settings exercise the actual manager policy without starting a model.
        runtime = RuntimeManager(command=[sys.executable, '-c', 'raise RuntimeError("Unexpected worker start in settings fixture")'])
    app = await create_app(home / "app", workspace=str(workspace), runtime=runtime,
                           voice='--voice-settings' in sys.argv, background_updates=False)
    app["control_token"] = "fixture-browser-control-token"
    runtime.service = app['service']
    stalled_body_release = asyncio.Event()
    stalled_body_transports = []
    stalled_body_mode = os.environ.get('CANVAS_RENDERER_STALLED_BODY') == '1'
    if stalled_body_mode:
        # Test-only, one exact request marked by the renderer journey's route.
        # Run the complete production handler/auth first; never fabricate acceptance.
        @web.middleware
        async def stall_canvas_details_body(request, handler):
            response = await handler(request)
            identity = request.headers.get('X-Canvas-Renderer-Stall')
            if not identity or request.method != 'POST' or request.path != '/api/actions':
                return response
            payload = await request.json()
            assert request.headers.get('Authorization') == 'Bearer fixture-browser-control-token'
            assert identity == payload['id'] and payload['action'] == 'view.update'
            assert set(payload['args']) == {'sessionId', 'patch'}
            assert payload['args']['patch'] == {'canvasControlsExpanded': True}
            assert not stalled_body_transports, 'Only one stalled response is permitted'
            receipt = json.loads(response.body)
            assert response.status == 200 and receipt['accepted'] is True
            print(json.dumps({'canvasRendererStalledBody': {
                'event': 'accepted', 'id': identity, 'clientId': request.headers['X-Amplifier-Client'],
                'args': payload['args'], 'hostInstanceId': receipt['hostInstanceId'],
                'accepted': receipt['accepted'], 'status': response.status}}), flush=True)
            headers = response.headers.copy()
            headers['Content-Length'] = str(len(response.body))
            headers['X-Canvas-Renderer-Stalled-Request'] = identity
            stream = web.StreamResponse(status=response.status, headers=headers)
            stalled_body_transports.append(request.transport)
            await stream.prepare(request)
            await stream.write(response.body[:1])
            print(json.dumps({'canvasRendererStalledBody': {
                'event': 'headers-sent', 'id': identity, 'bodyBytesSent': 1,
                'bodyBytesTotal': len(response.body)}}), flush=True)
            await stalled_body_release.wait()
            # Shutdown aborts the transport before releasing this wait. Do not
            # send EOF or the remaining bytes and turn a stall into success.
            raise asyncio.CancelledError
        app.middlewares.insert(0, stall_canvas_details_body)
    if '--first-use' not in sys.argv:
        await runtime.service.dispatch('view.update', {'patch': {'setupJourney': 'done'}})
    if '--shared-work-seed-only' in sys.argv:
        from shared_work_scenario import install
        assert '--shared-work' in sys.argv and not (home / 'shared-work-manifest.json').exists()
        try:
            await install(app, runtime, manifest_path=home / 'shared-work-manifest.json')
        finally:
            await runtime.service.close()
        print(json.dumps({'sharedWorkSeed': {'complete': True, 'pid': os.getpid()}}), flush=True)
        return

    async def inspect(request):
        return web.json_response({"installedIdentity": installed_identity,
            "foundationCachePresent": (home / "app/foundation").exists(),
            "installedPackageVerified": installed_package_verified, "staticSha256": static_identity,
            "storedCopyPath": str(stored_copy_path) if stored_copy_path else None,
            "registeredWorkspaces": app["service"]._state["workspaces"], "sent": getattr(runtime, 'sent', []),
            "retention": getattr(getattr(runtime, 'retention', None), 'settings', None),
            "workerCount": len(getattr(runtime, 'workers', {})), "started": getattr(runtime, "started", []), "stopped": getattr(runtime, "stopped", [])})

    app.router.add_get("/fixture", inspect)
    if '--appearance-states' in sys.argv:
        async def appearance_status(request):
            # Synthetic lifecycle input to the real publication/projection path.
            # Never start a runtime or send a provider request for appearance QA.
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            data = await request.json()
            if set(data) != {'sessionId', 'status'} or data['status'] not in {'idle', 'working', 'starting', 'stopping'}:
                raise web.HTTPBadRequest()
            await app['service'].on_runtime_event('runtime.status', data)
            return web.json_response({'ok': True})
        app.router.add_post('/fixture/appearance-status', appearance_status)
    if hasattr(runtime, 'provider_scenario'):
        runtime.provider_scenario.routes(app)
    if '--navigation-history' in sys.argv:
        async def history_message(request):
            service = app['service']
            session = service._session((await request.json())['sessionId'])
            await service.on_runtime_event('assistant.message', {
                'sessionId': session['id'], 'text': 'Open the related conversation.',
                'generationId': 'history-fixture-generation',
            })
            return web.json_response({'messageId': session['messages'][-1]['id']})
        app.router.add_post('/fixture/history-message', history_message)
    if '--startup-failure' in sys.argv:
        async def fail_startup(request):
            runtime.start_gate.set()
            return web.json_response({'ok': True})
        app.router.add_post('/fixture/fail-startup', fail_startup)
        async def report_selection(request):
            data = await request.json()
            await app['service'].on_runtime_event('runtime.status', {
                'sessionId': data['sessionId'], 'status': 'ready',
                'report': {'effective_selection': {'instance': 'test-provider', 'model': 'reported-model'}}})
            return web.json_response({'ok': True})
        app.router.add_post('/fixture/report-selection', report_selection)
    if '--chat-controls' in sys.argv:
        async def provider_preference(request):
            preferred_model[0] = (await request.json())['model']
            await app['service'].management.invalidate_configuration()
            return web.json_response({'ok': True})

        if '--provider-resolution' not in sys.argv:
            app.router.add_post('/fixture/provider-preference', provider_preference)

        async def activity(request):
            data = await request.json()
            service = app['service']
            session = service._session(data['sessionId'])
            session['status'] = data.get('status', 'working')
            session['workers'] = data.get('workers', [])
            if 'plan' in data:
                session.setdefault('execution', {})['plan'] = data['plan']
            service._publish()
            return web.json_response({'ok': True})

        async def damage(request):
            from amplifier_web.resource_files import root
            service = app['service']
            aid = (await request.json())['artifactId']
            row = next(row for row in service.state['canvasArtifacts'] if row['id'] == aid)
            identity = row['body']['$resource']
            (root(service.db) / (identity + '.json')).unlink()
            service.db.execute('DELETE FROM state_resources WHERE id=?', (identity,))
            for record in service.clients.records.values():
                if record.get('canvas', {}).get('id') == aid:
                    record['canvas'] = {'id': 'stale', 'sessionId': 'different-chat', 'open': False}
            service._last_storage_sweep = 0
            service._publish()
            return web.json_response({'ok': True})

        app.router.add_post('/fixture/activity', activity)
        app.router.add_post('/fixture/damage', damage)
    if '--shared-work' in sys.argv:
        from shared_work_scenario import install
        await install(app, runtime, manifest_path=home / 'shared-work-manifest.json')
    release_query_gate = lambda: None
    if '--scoped-query-control' in sys.argv:
        # Only this synthetic browser opts in. Target its visible Chats search,
        # not the independent Workspaces instance's queued search. A global
        # counter mistakes that second legitimate query for an automatic retry.
        # Delegate to the real process owner after release; no replacement pages.
        query_owner = app['service'].shell.resources.queries
        original_factory = query_owner.factory
        gate = asyncio.Event(); gate.set()
        catalog_paused = False
        def release_query_gate():
            nonlocal catalog_paused
            gate.set()
            if catalog_paused:
                catalog_paused = False
                app['service'].history.lock.release()
        query_mode, entered, target_client, target_resource = 'release', 0, None, 'sidebarSearch'
        query_events, query_invalidations = [], []
        original_invalidate = query_owner.resources.invalidate
        def record_invalidation(global_keys=None):
            original_invalidate(global_keys)
            task = asyncio.current_task()
            query_invalidations.append({"at": time.monotonic(), "fields": sorted(global_keys) if global_keys is not None else None,
                "task": task.get_coro().__qualname__ if task else None})
            del query_invalidations[:-32]
        query_owner.resources.invalidate = record_invalidation
        digest = lambda value: hashlib.sha256(repr(value).encode()).hexdigest()[:16]
        def controlled_factory(*args, **kwargs):
            projector = original_factory(*args, **kwargs)
            original_project = projector.project
            async def project(inputs, **options):
                nonlocal entered
                if inputs['operation'] == 'chats' and inputs['args']['section'] == ('recent' if target_resource == 'workHome' else 'search'):
                    job = query_owner.jobs.get(query_owner.active, {})
                    event = {'key': options.get('key'), 'at': time.monotonic(), 'fence': [digest(part) for part in job.get('fence', ())], 'mode': query_mode, 'interests': sorted({(item[2], item[3]) for item in job.get('interests', {}).values()})}
                    query_events.append(event)
                    del query_events[:-16]
                    targeted = any(item[0] == target_client and item[2:4] == ('chats', target_resource)
                                   for item in job.get('interests', {}).values())
                    event['targeted'] = targeted
                    if not targeted:
                        return await original_project(inputs, **options)
                    if query_mode == 'hold':
                        entered += 1
                        await gate.wait()
                    if query_mode == 'fail':
                        event['failureFenceCurrent'] = query_owner._fence(inputs['operation']) == job.get('fence')
                        event['failureAt'] = time.monotonic()
                        event['currentInterests'] = sum(query_owner._current(item) for item in job.get('interests', {}).values())
                        return {'status': 'retry', 'reason': 'worker_failed'}
                return await original_project(inputs, **options)
            projector.project = project
            return projector
        query_owner.factory = controlled_factory
        async def query_control(request):
            nonlocal query_mode, target_client, target_resource, catalog_paused
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            if request.method == 'POST':
                payload = await request.json()
                mode, identity = payload.get('mode'), payload.get('clientId')
                resource = payload.get('resource', 'sidebarSearch')
                if not isinstance(identity, str) or identity not in app['service'].clients.records:
                    raise web.HTTPBadRequest()
                if mode not in {'hold', 'release', 'fail'}:
                    raise web.HTTPBadRequest()
                if resource not in {'sidebarSearch', 'workHome'}:
                    raise web.HTTPBadRequest()
                if mode == 'hold' and not catalog_paused:
                    # This phase proves retry suppression for one unchanged data
                    # generation. Wait for discovery already in flight, then
                    # defer its next reconciliation until the phase releases.
                    # Explicit canonical commands and query workers remain live.
                    await app['service'].history.lock.acquire()
                    catalog_paused = True
                target_client = identity
                target_resource = resource
                query_mode = mode
                if mode == 'hold':
                    gate.clear()
                elif mode == 'release':
                    release_query_gate()
                else:
                    gate.set()
            return web.json_response({'mode': query_mode, 'entered': entered, 'events': query_events,
                'catalogPaused': catalog_paused, 'invalidations': query_invalidations, 'epochs': query_owner.resources.epochs, 'at': time.monotonic(), 'currentFence': [digest(part) for part in query_owner._fence('chats')],
                'jobs': [{'key': digest(key), 'currentInterests': sum(query_owner._current(item) for item in job['interests'].values())} for key, job in query_owner.jobs.items()],
                'failures': [{'key': digest(key), 'until': value['until'], 'automatic': key in query_owner.retries,
                    'currentInterests': sum(query_owner._current(item) for item in value['interests'].values())} for key, value in list(query_owner.failures.items())[-16:]]})
        app.router.add_get('/fixture/scoped-query', query_control)
        app.router.add_post('/fixture/scoped-query', query_control)

        async def project_path(request):
            # A race at the command boundary, confined to disposable fixture folders.
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            data = await request.json()
            path = Path(data['path']).resolve()
            if not path.is_relative_to(home.resolve()) or not path.is_dir():
                raise web.HTTPBadRequest()
            service = app['service']
            workspace = next(row for row in service.state['workspaces'] if row['id'] == data['workspaceId'])
            workspace['path'] = str(path)
            service._publish()
            return web.json_response({'ok': True})

        app.router.add_post('/fixture/project-path', project_path)
    if '--canvas-download-cookie' in sys.argv:
        async def browser_session(request):
            # Native browser downloads use the normal session cookie; the
            # fixture's injected renderer Authorization header is insufficient.
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            from amplifier_web.auth import SESSION_COOKIE, new_session
            response = web.json_response({'issued': True})
            response.set_cookie(SESSION_COOKIE, new_session(app['session_secret']),
                                httponly=True, samesite='Strict', path='/')
            return response
        app.router.add_post('/fixture/browser-session', browser_session)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Windows event loops cannot register Unix signals; preserve fixture startup
    # there while POSIX qualification exercises graceful application teardown.
    shutdown_signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
    def stop_requested(signum):
        if '--shared-work' in sys.argv:
            print(json.dumps({'sharedWorkSignal': {
                'signal': signal.Signals(signum).name, 'at': time.monotonic()}}), flush=True)
        stopped.set()

    for signum in shutdown_signals:
        if '--shared-work' in sys.argv:
            loop.add_signal_handler(signum, stop_requested, signum)
        else:
            loop.add_signal_handler(signum, stopped.set)
    print(json.dumps({"url": url}), flush=True)
    try:
        await stopped.wait()
    finally:
        try:
            release_query_gate()
            if stalled_body_mode:
                for transport in stalled_body_transports:
                    transport.abort()
                stalled_body_release.set()
            await runner.cleanup()
            if stalled_body_mode:
                print(json.dumps({'canvasRendererStalledBody': {
                    'event': 'cleanup-complete', 'stalledResponses': len(stalled_body_transports)}}), flush=True)
            if '--shared-work' in sys.argv:
                print(json.dumps({'sharedWorkCleanup': {'complete': True,
                    'at': time.monotonic(), **app['shared_work_wait_snapshot']()}}), flush=True)
        finally:
            for signum in shutdown_signals:
                loop.remove_signal_handler(signum)


if __name__ == "__main__":
    retained_home = os.environ.get('AMPLIFIER_TEST_SHARED_WORK_HOME')
    if retained_home:
        assert '--shared-work' in sys.argv
        directory = Path(retained_home).resolve(strict=True)
        assert directory.is_dir()
        asyncio.run(main(directory))
    else:
        with tempfile.TemporaryDirectory(prefix="amplifier-empty-host-") as directory:
            asyncio.run(main(Path(directory)))
