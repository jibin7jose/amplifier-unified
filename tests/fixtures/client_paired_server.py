"""Actual source server plus independently packaged static bytes, synthetic runtime.

The external assembly record is test provenance, not application configuration.
All source/static checks precede imports. The existing chat fixture owns the fake
runtime and account/catalog substitutes; actual auth, actions and SSE stay real.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile


def verify_tree(root, expected, *, complete=False):
    for name, row in expected.items():
        body = (root / name).read_bytes()
        if len(body) != row['bytes'] or hashlib.sha256(body).hexdigest() != row['sha256']:
            raise ValueError('Assembly input differs: ' + name)
    if complete:
        actual = {str(path.relative_to(root)) for path in root.rglob('*') if path.is_file()}
        if actual != set(expected):
            raise ValueError('Unexpected files in the assembled static directory')


async def serve(args):
    root = args.server_root.resolve()
    record = json.loads(args.assembly_record.read_text())
    client = record['client']['manifest']
    if record['server']['revision'] != client['pairedServer']['revision']:
        raise ValueError('Client and actual source server are not the recorded exact pair')
    verify_tree(root, record['server']['files'])
    verify_tree(root / 'amplifier_web/static', client['assets'], complete=True)
    sys.path[:0] = [str(root), str(root / 'tests/fixtures')]
    import amplifier_web
    import chat_ui_server as fixture
    from aiohttp import web

    if Path(amplifier_web.__file__).resolve().parent != root / 'amplifier_web':
        raise ValueError('Fixture imported a different server')
    if amplifier_web.__version__ != record['server']['version']:
        raise ValueError('Actual server version differs from the recorded source')
    create_app = fixture.fixture.create_app

    async def isolated_app(*positional, **keywords):
        keywords.update(background_updates=False, background_work=False, preload_providers=False)
        return await create_app(*positional, **keywords)

    fixture.fixture.create_app = isolated_app
    with tempfile.TemporaryDirectory(prefix='paired-web-state-', dir=args.state_parent) as directory:
        app = await fixture.main(Path(directory))
        streams = {}

        @web.middleware
        async def observe_stream(request, handler):
            if request.path != '/api/events':
                return await handler(request)
            client_id = request.query.get('clientId')
            transport = request.transport
            streams[client_id] = transport
            try:
                return await handler(request)
            finally:
                if streams.get(client_id) is transport:
                    streams.pop(client_id, None)

        app.middlewares.append(observe_stream)

        async def disconnect(request):
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            client_id = (await request.json())['clientId']
            transport = streams.get(client_id)
            if transport is None:
                raise web.HTTPNotFound()
            # Close an actual selected SSE connection. Browser offline emulation
            # may leave an existing stream open, so it is not this fault oracle.
            transport.close()
            return web.json_response({'disconnected': True})

        async def identity(request):
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            return web.json_response({
                'serverRevision': record['server']['revision'],
                'serverVersion': amplifier_web.__version__,
                'serverSourceFilesVerified': len(record['server']['files']),
                'clientArchiveSha256': record['client']['archiveSha256'],
                'clientSource': client['source'],
                'staticFilesVerified': len(client['assets']),
                'assembly': 'source server with independently installed web assets',
                'runtime': 'existing synthetic ChatRuntime',
            })

        app.router.add_get('/fixture/assembly', identity)
        app.router.add_post('/fixture/disconnect', disconnect)
        runner = web.AppRunner(app)
        await runner.setup()
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
        for signum in signals:
            loop.add_signal_handler(signum, stopped.set)
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
            app['allowed_origins'] = app['allowed_origins'] | {base}
            print(json.dumps({'fixture': 'client-paired-server', 'pid': os.getpid(), 'base': base}), flush=True)
            await stopped.wait()
        finally:
            try:
                await runner.cleanup()
            finally:
                for signum in signals:
                    loop.remove_signal_handler(signum)
        verify_tree(root, record['server']['files'])
        verify_tree(root / 'amplifier_web/static', client['assets'], complete=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-root', type=Path, required=True)
    parser.add_argument('--assembly-record', type=Path, required=True)
    parser.add_argument('--state-parent', type=Path, required=True)
    asyncio.run(serve(parser.parse_args()))
