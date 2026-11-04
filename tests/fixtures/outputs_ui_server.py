"""Real host/assets and private Git fixture; no external provider or publication."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'),AMPLIFIER_WEB_HOME=str(home/'app'),AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app
    root=home/'workspace';root.mkdir();(root/'report.txt').write_text('Original report bytes.')
    (root/'page.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'))
    def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout.decode()
    git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.test')
    (root/'design.txt').write_text('before\n');git('add','design.txt');git('commit','-qm','Initial');(root/'design.txt').write_text('after\n')
    (root/'binary.dat').write_bytes(b'\0original binary');git('add','binary.dat');git('commit','-qm','Binary baseline')
    base=git('rev-parse','HEAD').strip()
    (root/'branch.txt').write_text('Committed branch content\n');git('add','branch.txt');git('commit','-qm','Branch change')
    (root/'binary.dat').write_bytes(b'\0PRIVATE-BINARY-CONTENT');(root/'untracked.txt').write_text('PRIVATE-UNTRACKED-CONTENT')
    runtime=Runtime();app=await create_app(home/'app',workspace=root,runtime=runtime,voice=False,background_updates=False,preload_providers=False)
    app['control_token']='fixture-browser-control-token';service=app['service']
    await service.dispatch('session.create',{'title':'Output task'});sid=service._session()['id']
    original='A reusable draft:\n:::writing{variant="document" id="12345"}\nOriginal writing copy.\n:::'
    service._message(service._session(),'assistant',original,'text')
    if '--canvas-reading' in sys.argv:
        for index in range(80):
            service._message(service._session(),'assistant',f'Canvas reading marker {index:02}', 'text')
    service._publish()
    await service.dispatch('view.update',{'patch':{'draft':'Keep output draft'}})
    async def inspect(request):
        return web.json_response({'selected':service.state['selectedSessionId'],'draft':service.state['view']['draft'],'sent':runtime.sent,
            'message':service._session(sid)['messages'][0]['text'],'file':(root/'report.txt').read_text(),'outputs':service.outputs.store.list(sid,include_unlinked=True)['items'],
            'git':{'base':base,'head':git('rev-parse','HEAD').strip(),'status':git('status','--porcelain'),
                'files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['design.txt','branch.txt','binary.dat','untracked.txt']}}})
    async def change(request):
        (root/'report.txt').write_text('Later original-file edit.');return web.json_response({'changed':True})
    app.router.add_get('/fixture',inspect);app.router.add_post('/fixture/change',change)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins']=app['allowed_origins']|{url}
    stopped=asyncio.Event();loop=asyncio.get_running_loop();installed_signals=[]
    shutdown={'pid':os.getpid(),'home':str(home),'signal':None,'runnerCleanup':False}
    def request_stop(signum):
        shutdown['signal']=signal.Signals(signum).name
        stopped.set()
    try:
        # POSIX qualification requires these handlers before reporting readiness.
        # Other platforms retain startup support, but cannot claim graceful
        # signal cleanup without the receipt and a normal process exit.
        for signum in (signal.SIGTERM,signal.SIGINT) if os.name=='posix' else ():
            loop.add_signal_handler(signum,request_stop,signum)
            installed_signals.append(signum)
        print(json.dumps({'url':url,'sessionId':sid,'original':original,'workspace':str(root)}),flush=True)
        await stopped.wait()
    finally:
        try:
            await runner.cleanup()
            shutdown['runnerCleanup']=True
        finally:
            for signum in installed_signals:
                loop.remove_signal_handler(signum)
    return shutdown


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='outputs-fixture-') as home:
        shutdown=asyncio.run(main(Path(home)))
    shutdown['homeRemoved']=not Path(home).exists()
    print(json.dumps({'outputsFixtureCleanup':shutdown}),flush=True)
