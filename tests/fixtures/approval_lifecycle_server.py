"""Authenticated production HTTP/UI over cold saved synthetic approval records.

No providers, configuration writes, external effects, or model calls. Counts prove
whether a passive review attempted to start runtime work, not provider cooperation.
The same fixture can import the isolated installed wheel via PYTHONPATH.
"""
import asyncio
import hashlib
import json
import os
import shutil
import signal
from pathlib import Path
import tempfile
import time

from aiohttp import web
import amplifier_web
from amplifier_web.server import create_app

TOKEN = 'fixture-approval-control-token'


class Runtime:
    def __init__(self):
        self.starts, self.inputs, self.controls, self.decisions = [], [], [], []
        self.stops = []

    async def start(self, session, emit):
        self.starts.append(session['id'])
        raise AssertionError('Passive review started a worker')

    async def send(self, *args, **kwargs):
        self.inputs.append(True)
        raise AssertionError('Passive review sent input')

    async def control(self, *args, **kwargs):
        self.controls.append(True)
        raise AssertionError('Passive review warmed runtime controls')

    async def approval(self, sid, identity, decision, **kwargs):
        self.decisions.append({'id':identity, 'decision':decision, 'kwargs':kwargs})
        return {'accepted':True}

    async def stop(self, sid):
        self.stops.append(sid)

    async def close(self):
        pass


async def main(home):
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Install before any readiness publication; SIGTERM is graceful cleanup,
    # not a signal-terminated child that the browser harness can call a pass.
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)
    package = Path(amplifier_web.__file__).resolve().parent
    expected = os.environ.get('AMPLIFIER_TEST_PACKAGE_ROOT')
    server_source = Path(create_app.__code__.co_filename).resolve()
    static_root = (package / 'static').resolve()
    if expected:
        assert package == Path(expected).resolve(), 'Fixture must import the exact installed package'
        assert server_source == package / 'server.py', 'Fixture server must come from that installed package'
        assert static_root == package / 'static', 'Fixture static assets must come from that installed package'
    package_identity = {
        'packageRoot':str(package), 'server':str(server_source), 'staticRoot':str(static_root),
        'expectedPackageRoot':str(Path(expected).resolve()) if expected else None,
        'installedPackageVerified':bool(expected),
        'staticSha256':{str(path.relative_to(static_root)):hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(static_root.rglob('*')) if path.is_file()},
    }
    toolchain = Path(os.environ.get('AMPLIFIER_SHELL_NODE_MODULES', Path(__file__).resolve().parents[2] / '.build/web-client/node_modules')).resolve()
    readiness = {'node':shutil.which('node'), 'nodeModules':str(toolchain),
                 'playwright':(toolchain / 'playwright').is_dir(),
                 'validationHtml':(package / 'static/shell-validation.html').is_file(),
                 'source':str(package), 'packageIdentity':package_identity}
    (home.parent / 'validation-readiness.json').write_text(json.dumps(readiness, indent=2))
    assert readiness['node'] and readiness['playwright'] and readiness['validationHtml'], readiness
    workspace = home / 'workspace'
    workspace.mkdir()
    os.environ['AMPLIFIER_HOME'] = str(home / 'native')
    native = home / 'native'
    native.mkdir()
    (native / 'settings.yaml').write_text(json.dumps({'config':{'providers':[
        {'module':'provider-fixture','id':'fixture','config':{'default_model':'fixture-model'}}]}}))
    app = await create_app(home / 'app', workspace=str(workspace), runtime=Runtime(),
                           voice=False, background_updates=False)
    service = app['service']
    session = service._new_session({'title':'Permission recovery fixture'})
    session.update(status='idle', interruptionRevision=1)
    service.state['sessions'] = [session]
    service.state['selectedSessionId'] = session['id']
    directory = service.data_dir / 'sessions' / session['id']
    directory.mkdir(parents=True,exist_ok=True)
    (directory / 'effective-configuration.json').write_text(json.dumps({'providers':[],'tools':[]}))
    for identity, revision in [('old',0),('current',1)]:
        grant = {'id':'capability:'+identity, 'sessionId':session['id'], 'workspace':session['workspace'],
                 'interruptionRevision':revision, 'revision':1, 'status':'pending', 'createdAt':time.time(),
                 'effects':['file_access'], 'scopes':['session'], 'reason':identity+' file scope',
                 'nativeWait':False}
        service.configuration_authority.save(grant)
        session['approvals'].append({'id':grant['id'],'status':'pending','prompt':grant['reason']})
    # Keep a legacy pending card on disk: hydration must reconcile the cold owner.
    service._save()
    await service.close()
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=str(workspace), runtime=runtime,
                           voice=False, background_updates=False)
    app['control_token'] = TOKEN
    service = app['service']
    sid = session['id']
    fixture_sessions = {sid}
    await service.dispatch('session.select', {'id':sid})

    async def fixture(request):
        if request.headers.get('Authorization') != 'Bearer '+TOKEN:
            raise web.HTTPUnauthorized()
        if request.method == 'POST':
            body = await request.json()
            if body['change'] == 'stop-case':
                if body.get('case') not in {'later-allow', 'pending-preflight', 'lost-ack', 'malformed-ack'}:
                    raise web.HTTPBadRequest()
                target = service._new_session({'title':'Stop ordering '+body['case']})
                target.update(status='working', interruptionRevision=0)
                service.state['sessions'].append(target)
                fixture_sessions.add(target['id'])
                for suffix, status in [('history','denied'), ('pending','pending')]:
                    grant = {'id':'capability:'+target['id']+':'+suffix, 'sessionId':target['id'],
                             'workspace':target['workspace'], 'interruptionRevision':0, 'revision':1,
                             'status':status, 'createdAt':time.time(), 'effects':['file_access'],
                             'scopes':['session'], 'reason':suffix+' Stop ordering permission', 'nativeWait':False}
                    service.configuration_authority.save(grant)
                    target['approvals'].append({'id':grant['id'],'status':'deny' if status=='denied' else status,'prompt':grant['reason']})
                service._publish_changes(sessions={target['id']})
                return web.json_response({'sessionId':target['id'],'pendingId':grant['id']})
            if body['change'] in {'later', 'held-decision'}:
                suffix = 'later'
                if body['change'] == 'held-decision':
                    if body.get('decision') not in {'approve', 'deny'}:
                        raise web.HTTPBadRequest()
                    suffix = 'held-' + body['decision']
                grant = {'id':'capability:'+suffix, 'sessionId':sid, 'workspace':session['workspace'],
                         'interruptionRevision':1, 'revision':1, 'status':'pending', 'createdAt':time.time(),
                         'effects':['file_access'], 'scopes':['session'], 'reason':suffix+' independent permission',
                         'nativeWait':False}
                service.configuration_authority.save(grant)
                service._session(sid)['approvals'].append({'id':grant['id'],'status':'pending','prompt':grant['reason']})
                service._publish_changes(sessions={sid})
                return web.json_response({'created':grant['id']})
            grant = service.configuration_authority.record('capability:current')
            if body['change'] == 'expire':
                grant['expiresAt'] = time.time()-1
            elif body['change'] == 'scope':
                grant['reason'] = 'Changed scope requiring another review'
            else:
                raise web.HTTPBadRequest()
            service.configuration_authority.save(grant)
            service.db.commit()  # Deliberately no pushed card: exercise stale UI preflight.
        requested_sid = request.query.get('sessionId', sid)
        if requested_sid not in fixture_sessions:
            raise web.HTTPForbidden()
        current = service._session(requested_sid)
        from amplifier_web.attention import session_item_at, session_item_count
        from amplifier_web.navigation_summary import activity
        return web.json_response({'sessionId':requested_sid, 'approvals':current['approvals'],
            'interruptionRevision':current.get('interruptionRevision',0), 'lastInterruption':current.get('lastInterruption'),
            'attention':[item for index in range(session_item_count(current))
                         if (item:=session_item_at(current,{},index)) and item['id'].startswith('approval:')],
            'navigation':activity(current),
            'starts':runtime.starts,'inputs':runtime.inputs,'controls':runtime.controls,'decisions':runtime.decisions,'stops':runtime.stops,
            'source':str(Path(amplifier_web.__file__).resolve()),
            'packageIdentity':package_identity,
            'authoritySha256':hashlib.sha256(Path(amplifier_web.__file__).with_name('configuration_authority.py').read_bytes()).hexdigest()})

    app.router.add_get('/fixture', fixture)
    app.router.add_post('/fixture', fixture)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url':url}),flush=True)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    output = Path(os.environ['AMPLIFIER_TEST_OUTPUT_DIR'])
    output.mkdir(parents=True,exist_ok=True)
    asyncio.run(main(Path(tempfile.mkdtemp(prefix='approval-fixture-',dir=output))))