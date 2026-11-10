"""Actual source or explicitly installed-wheel server, with synthetic runtime.

The external assembly record is test provenance, not application configuration.
All source/static checks precede application imports. The default source mode
retains its exact-pair contract. AMPLIFIER_TEST_INSTALLED_PACKAGE opts into the
existing wheel's amplifier_web directory; no installation or asset copy occurs.
In that mode server.files maps ALL *.py paths relative to that package, while
client.manifest is the installed static/client-component.json object and
client.component supplies that file's own {bytes, sha256}. Its unqualified
shared-input peer is NOT the actual server revision declared by server.revision.
The existing chat fixture owns runtime/catalog substitutes; auth/actions/SSE
stay real. The Node browser driver is separate from the Node-free Python host.
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile


def verify_tree(root, expected, *, complete=False):
    for name, row in expected.items():
        body = (root / name).read_bytes()
        if len(body) != row['bytes'] or hashlib.sha256(body).hexdigest() != row['sha256']:
            raise ValueError('Assembly input differs: ' + name)
    if complete:
        actual = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
        if actual != set(expected):
            raise ValueError('Unexpected files in the assembled static directory')


def verify_host_path():
    host_path = os.environ.get('AMPLIFIER_TEST_HOST_PATH', '')
    if (not Path(host_path).is_absolute() or not Path(host_path).is_dir()
            or os.environ.get('PATH') != host_path or 'PYTHONPATH' in os.environ
            or not sys.flags.isolated or not sys.dont_write_bytecode):
        raise ValueError('Installed fixture requires -I -B, no PYTHONPATH and the explicit host-only PATH')
    if any(shutil.which(name) is not None for name in ('node', 'nodejs', 'npm', 'npx')):
        raise ValueError('Node tooling is available to the installed Python host')


def verify_installed(package, record):
    """Verify supplied byte bindings, not a moving checkout or a self-built UI."""
    server, client = record['server'], record['client']
    if not re.fullmatch(r'[0-9a-f]{40}', server['revision']):
        raise ValueError('Installed qualification requires an explicit full server revision')
    files = server['files']
    actual = {path.relative_to(package).as_posix()
              for path in package.rglob('*.py') if path.is_file()}
    if not files or actual != set(files):
        raise ValueError('server.files must bind every installed amplifier_web Python file')
    static = package / 'static'
    if static.is_symlink() or any(path.is_symlink() for path in static.rglob('*')):
        raise ValueError('Installed static tree must not redirect through links')
    manifest = client['manifest']
    if manifest['pairedServer'].get('qualification') != 'unqualified':
        raise ValueError('The installed component peer is shared-input provenance, not qualification')
    if 'client-component.json' in manifest['assets']:
        raise ValueError('Component metadata must not include its own asset hash')
    static_files = {**manifest['assets'], 'client-component.json': client['component']}
    for root, expected in ((package, files), (static, static_files)):
        for name in expected:
            path = root / name
            if (Path(name).is_absolute() or '..' in Path(name).parts
                    or path.is_symlink() or not path.resolve().is_relative_to(root.resolve())):
                raise ValueError('Assembly map contains a nonlocal file')
        verify_tree(root, expected, complete=root == static)
    if json.loads((static / 'client-component.json').read_bytes()) != manifest:
        raise ValueError('Assembly manifest differs from the installed component')

    distribution = importlib.metadata.distribution('amplifier-unified')
    direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    if (not distribution.read_text('WHEEL') or direct.get('dir_info', {}).get('editable')
            or distribution.version != server['version']
            or Path(distribution.locate_file('amplifier_web')).resolve() != package):
        raise ValueError('Expected the exact non-editable installed amplifier-unified wheel')
    owned = {str(path) for path in distribution.files or ()}
    required = {'amplifier_web/' + name for name in files}
    required.update('amplifier_web/static/' + name for name in static_files)
    if not required <= owned:
        raise ValueError('Installed distribution does not own every recorded Python/static file')
    verify_host_path()


def verify_imports(package):
    """Reject source shadowing before and after test helper imports."""
    import amplifier_web

    if (Path(amplifier_web.__file__).resolve() != package / '__init__.py'
            or {Path(path).resolve() for path in amplifier_web.__path__} != {package}):
        raise ValueError('Fixture imported a different amplifier_web package')
    for name, module in tuple(sys.modules.items()):
        if name.startswith('amplifier_web.') and getattr(module, '__file__', None):
            if not Path(module.__file__).resolve().is_relative_to(package):
                raise ValueError('Fixture imported a source-shadowed amplifier_web module')


async def serve(args):
    root = args.server_root.resolve()
    record = json.loads(args.assembly_record.read_text())
    client = record['client']['manifest']
    installed = os.environ.get('AMPLIFIER_TEST_INSTALLED_PACKAGE')
    package = Path(installed).resolve() if installed else root / 'amplifier_web'

    def verify_inputs():
        if installed:
            verify_installed(package, record)
        else:
            verify_tree(root, record['server']['files'])
            verify_tree(package / 'static', client['assets'], complete=True)

    if installed:
        if not Path(installed).is_absolute():
            raise ValueError('Installed package selection must be an absolute path')
        verify_inputs()
        # Import the installed package BEFORE exposing only test-helper siblings.
        # -I excludes the checkout/CWD; settings_ui_server's opt-in check must not
        # reintroduce that source root or redirect helper subprocesses.
        verify_imports(package)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    else:
        if record['server']['revision'] != client['pairedServer']['revision']:
            raise ValueError('Client and actual source server are not the recorded exact pair')
        verify_inputs()
        sys.path[:0] = [str(root), str(root / 'tests/fixtures')]
        # Source mode deliberately binds helper subprocesses to the selected tree.
        os.environ['PYTHONPATH'] = os.pathsep.join(filter(None, (str(root), os.environ.get('PYTHONPATH'))))
    import amplifier_web
    import chat_ui_server as fixture
    from aiohttp import web

    verify_imports(package)
    if installed:
        verify_inputs()
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
            if installed:
                verify_host_path()
                verify_imports(package)
            return web.json_response({
                'serverRevision': record['server']['revision'],
                'serverVersion': amplifier_web.__version__,
                'serverSourceFilesVerified': len(record['server']['files']),
                'serverFilesSha256': hashlib.sha256(json.dumps(
                    record['server']['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                'installedWheel': bool(installed),
                'nodeUnavailable': True if installed else None,
                'installedPackage': str(package) if installed else None,
                'clientArchiveSha256': (record['client'].get('archiveSha256') if installed
                                       else record['client']['archiveSha256']),
                'clientSource': client['source'],
                'clientManifestPeer': client['pairedServer'],
                'staticFilesVerified': len(client['assets']) + (1 if installed else 0),
                'assembly': ('installed wheel with bundled web assets' if installed
                             else 'source server with independently installed web assets'),
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
                # A failed final check makes the owned host exit nonzero; the
                # browser driver cannot report success before that exit.
                verify_inputs()
                verify_imports(package)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-root', type=Path, required=True)
    parser.add_argument('--assembly-record', type=Path, required=True)
    parser.add_argument('--state-parent', type=Path, required=True)
    asyncio.run(serve(parser.parse_args()))
