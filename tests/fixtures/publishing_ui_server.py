"""Real private host and loopback publisher; no provider or public deployment."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home, remote_home):
    os.environ.update(AMPLIFIER_HOME=str(home / 'shared'), AMPLIFIER_WEB_HOME=str(home / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home / 'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app
    from amplifier_publishing.service import PublishingService

    remote_socket = remote_home / 'admin' / 'publisher.sock'
    remote = PublishingService(remote_home / 'store', remote_socket).__enter__()
    remote_identity = remote._dispatch({'method': 'target'})
    alternate_socket = remote_home / 'admin-b' / 'publisher.sock'
    alternate = PublishingService(remote_home / 'store-b', alternate_socket).__enter__()
    alternate_identity = alternate._dispatch({'method': 'target'})
    ssh_log = home / 'fixture-ssh.jsonl'
    bin_dir = home / 'bin'
    bin_dir.mkdir()
    fake_ssh = bin_dir / 'ssh'
    fake_ssh.write_text('#!' + sys.executable + '\n' + '''import json, os, shlex, subprocess, sys
payload = sys.stdin.buffer.read()
with open(os.environ['PUBLISHING_FIXTURE_SSH_LOG'], 'a') as log:
    log.write(json.dumps({'argv': sys.argv[1:], 'method': json.loads(payload)['method']}) + '\\n')
result = subprocess.run(shlex.split(sys.argv[-1]), input=payload, capture_output=True)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
''')
    fake_ssh.chmod(0o700)
    os.environ.update(PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                      PUBLISHING_FIXTURE_SSH_LOG=str(ssh_log),
                      PYTHONPATH=str(Path(__file__).resolve().parents[2]) + os.pathsep + os.environ.get('PYTHONPATH', ''))

    workspace = home / 'workspace'
    output = workspace / 'dist'
    output.mkdir(parents=True)
    (output / 'index.html').write_text('<!doctype html><title>Fixture release</title><h1>First release</h1>')
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=workspace, runtime=runtime, voice=False,
                           background_updates=False, preload_providers=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    runtime.service = service
    await service.dispatch('session.create', {'title': 'Publishing task'})
    sid = service._session()['id']
    await service.dispatch('view.update', {'patch': {'draft': 'Keep publishing draft'}})
    calls = []
    original_dispatch = service.dispatch

    async def dispatch(action, args=None, *positional, **kwargs):
        if action.startswith('publishing.'):
            calls.append({'action': action, 'args': dict(args or {})})
        return await original_dispatch(action, args, *positional, **kwargs)

    service.dispatch = dispatch

    async def inspect(request):
        targets = await original_dispatch('publishing.target.list', {'sessionId': sid})
        listing = await original_dispatch('publishing.list', {'sessionId': sid, 'targetId': request.query.get('target', targets['result']['selectedTargetId'])})
        return web.json_response({'selected': service.state['selectedSessionId'],
                                 'draft': service.state['view']['draft'], 'sent': runtime.sent,
                                 'started': runtime.started, 'calls': calls,
                                 'publishing': listing['result'], 'targets': targets['result'],
                                 'sshCalls': [json.loads(line) for line in ssh_log.read_text().splitlines()] if ssh_log.exists() else []})

    async def change(request):
        (output / 'index.html').write_text('<!doctype html><title>Fixture release</title><h1>Second release</h1>')
        return web.json_response({'changed': True})

    async def remote_change(request):
        (output / 'index.html').write_text('<!doctype html><title>Fixture release</title><h1>Third release</h1>')
        return web.json_response({'changed': True})

    app.router.add_post('/fixture/remote-change', remote_change)

    async def fail_deploy(request):
        original_put = service.publishing.store._put
        def interrupted(kind, record):
            if kind == 'site':
                service.publishing.store._put = original_put
                raise RuntimeError('Injected fixture failure after listener mutation')
            return original_put(kind, record)
        service.publishing.store._put = interrupted
        return web.json_response({'armed': True})

    app.router.add_post('/fixture/fail-deploy', fail_deploy)
    app.router.add_get('/fixture', inspect)
    app.router.add_post('/fixture/change', change)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals = []
    shutdown = {'pid': os.getpid(), 'home': str(home), 'remoteHome': str(remote_home),
                'signal': None, 'runnerCleanup': False, 'remoteCleanup': False}

    def request_stop(signum):
        shutdown['signal'] = signal.Signals(signum).name
        stopped.set()

    try:
        for signum in (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ():
            loop.add_signal_handler(signum, request_stop, signum)
            installed_signals.append(signum)
        print(json.dumps({'url': url, 'sessionId': sid, 'remoteServiceId': remote_identity['serviceId'],
                          'alternateServiceId': alternate_identity['serviceId'], 'alternateSocketPath': str(alternate_socket),
                          'remoteConfig': {'targetId': 'private-fixture', 'label': 'Isolated remote fixture',
                                           'hostname': 'fixture-host', 'python': sys.executable,
                                           'socketPath': str(remote_socket), 'expectedBind': '127.0.0.1'}}), flush=True)
        await stopped.wait()
    finally:
        try:
            await runner.cleanup()
            shutdown['runnerCleanup'] = True
            await asyncio.to_thread(remote.close)
            await asyncio.to_thread(alternate.close)
            shutdown['remoteCleanup'] = True
        finally:
            for signum in installed_signals:
                loop.remove_signal_handler(signum)
    return shutdown


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='publishing-fixture-') as directory, tempfile.TemporaryDirectory(prefix='pub-remote-', dir=Path('/tmp').resolve()) as remote_directory:
        shutdown = asyncio.run(main(Path(directory).resolve(), Path(remote_directory)))
    shutdown.update(homeRemoved=not Path(directory).exists(), remoteHomeRemoved=not Path(remote_directory).exists())
    print(json.dumps({'publishingFixtureCleanup': shutdown}), flush=True)
