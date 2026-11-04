"""Production host/static assets; synthetic file-control data, never a provider.

Derived from empty_host_ui_server's isolated host and dynamic-port lifecycle.
Seed only after a browser attaches and explicitly selects its fixture chat.
For installed qualification copy this file and configured_browser.py beneath
/artifacts, run with an installed Python outside /workspace, and set both
AMPLIFIER_TEST_INSTALLED=1 and AMPLIFIER_TEST_PACKAGE_ROOT to the exact installed
amplifier_web package directory. No source-path fallback is allowed in that mode.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile


TOKEN = 'fixture-browser-control-token'
HTML = ('<!doctype html>\r\n<meta charset="utf-8">\r\n'
        '<h1>Original résumé α — 雪</h1>\r\n'
        '<label>Unfinished preview note <input id="preview-note"></label>\r\n'
        '<script>\r\n'
        'window.fileControlsExecutions=(window.fileControlsExecutions||0)+1;\r\n'
        'document.body.dataset.fileControlsExecuted=String(window.fileControlsExecutions);\r\n'
        'document.body.dataset.fileControlsToken=Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-");\r\n'
        '</script>\r\n')
MARKDOWN = '# Adjacent notes α\r\n\r\nDifferent bytes from the HTML.\r\n'
SAVED = '# Saved only note\r\n\r\nNo filesystem source — café.\r\n'
OUTSIDE = '# Earlier workspace file\r\n\r\nNow outside the working folder.\r\n'
NEWER = '<!doctype html>\r\n<h1>Newer revision, not the clicked file</h1>\r\n'


class Runtime:
    """Same no-provider seam as empty_host_ui_server, but sending is forbidden."""
    def __init__(self):
        self.sent = []
        self.started = []

    async def start(self, session, emit):
        self.started.append(session['id'])
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})

    async def send(self, session, text, input_id, emit):
        self.sent.append({'sessionId': session['id'], 'inputId': input_id})
        raise AssertionError('File controls must not send model input')

    async def control(self, *args):
        return {}

    async def stop(self, sid, **kwargs):
        pass

    async def close(self):
        pass


def package_identity():
    installed = os.environ.get('AMPLIFIER_TEST_INSTALLED') == '1'
    expected = os.environ.get('AMPLIFIER_TEST_PACKAGE_ROOT')
    if installed or expected:
        assert expected, 'Installed mode requires the exact AMPLIFIER_TEST_PACKAGE_ROOT'
        assert not Path(__file__).resolve().is_relative_to('/workspace'), 'Copy fixture to /artifacts first'
        assert not Path.cwd().resolve().is_relative_to('/workspace')
        assert not any(Path(p).resolve().is_relative_to('/workspace') for p in sys.path if p)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import amplifier_web
    root = Path(amplifier_web.__file__).resolve().parent
    if installed or expected:
        assert root == Path(expected).resolve(), ('Wrong imported package root', root, expected)
        assert not root.is_relative_to('/workspace')
        assert root.is_relative_to(Path(sys.prefix).resolve()), ('Package is not installed in this interpreter', root)
    static = root / 'static'
    assert (static / 'index.html').is_file(), 'Build/package the candidate before running this proof'
    return {'packageRoot': str(root), 'installedVerified': bool(installed or expected),
            'staticSha256': {str(p.relative_to(static)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted(static.rglob('*')) if p.is_file()}}


def expected_file(body, *, title, path=None, working=None, workspace=None):
    data = body.encode('utf-8')
    paths = {}
    if path is not None:
        paths = {'absolute': str(path), 'base': str(working), 'workspace': str(workspace)}
        if path.is_relative_to(working):
            paths['relative'] = str(path.relative_to(working))
    return {'body': body, 'sha256': hashlib.sha256(data).hexdigest(), 'byteLength': len(data),
            'filename': path.name if path else title + '.md', 'title': title, 'filePaths': paths}


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home / 'shared'), AMPLIFIER_WEB_HOME=str(home / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home / 'sessions'))
    from configured_browser import seed_connection
    seed_connection(home / 'shared')
    identity = package_identity()
    from aiohttp import web
    from amplifier_web.server import create_app

    workspace, working = home / 'workspace', home / 'worktrees' / 'generated-files'
    workspace.mkdir()
    (working / 'generated reports').mkdir(parents=True)
    html = working / 'generated reports' / 'résumé α.html'
    adjacent = html.with_suffix('.md')
    outside = workspace / 'earlier-notes.md'
    for path, body in ((html, HTML), (adjacent, MARKDOWN), (outside, OUTSIDE)):
        path.write_bytes(body.encode('utf-8'))
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=str(workspace), runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    app['control_token'] = TOKEN
    service = app['service']
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done'}})
    manifest = None
    newer = None
    deleted = False
    seed_attempted = False

    def guard(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()

    def attached(data):
        sid, cid = data.get('sessionId'), data.get('clientId')
        record = service.clients.records.get(cid)
        if (not record or cid not in service.queue_clients.values()
                or record.get('selectedSessionId') != sid):
            raise web.HTTPConflict(text='Attach and select the fixture chat before seeding')
        session = service._session(sid)
        if session['workspace'] != str(workspace):
            raise web.HTTPConflict(text='Only the disposable fixture workspace may be seeded')
        return session, cid

    async def command(name, args, cid):
        result = await service.dispatch(name, {**args, 'clientId': cid})
        assert result['accepted'], result
        return result['result']

    async def seed(request):
        nonlocal manifest, seed_attempted
        guard(request)
        session, cid = attached(await request.json())
        if seed_attempted:
            raise web.HTTPConflict(text='Seed is single-use; do not replay a partial seed')
        seed_attempted = True
        sid = session['id']
        # Synthetic transcript only. The maintained canvas.show action binds
        # publications to the originating user turn, without calling send().
        service._message(session, 'user', 'Retain these generated files for local use.', 'text')
        message_id = session['messages'][-1]['id']
        service._message(session, 'assistant', 'Saved file examples. Downloading does not preview HTML.', 'text')
        service._publish()
        outside_row = await command('canvas.show', {'kind': 'auto', 'path': str(outside),
            'title': 'Earlier workspace file', 'sessionId': sid, 'presentation': 'saved'}, cid)
        # Synthetic placement models an already-completed worktree handoff. No
        # repository or machine placement operation is part of this UI fixture.
        session = service._session(sid)
        session['workingDirectory'] = str(working)
        session['executionRevision'] = session.get('executionRevision', 0) + 1
        service._publish()
        rows = {'outside': {**outside_row, **expected_file(OUTSIDE, title='Earlier workspace file',
                    path=outside, working=working, workspace=workspace), 'version': 1}}
        for key, path, body, title in (
                ('html', html, HTML, 'Generated HTML file'),
                ('adjacent', adjacent, MARKDOWN, 'Adjacent Markdown file'),
                ('saved', None, SAVED, 'Saved only note')):
            payload = {'kind': 'auto', 'path': str(path)} if path else {'kind': 'markdown', 'content': body}
            row = await command('canvas.show', {**payload, 'title': title,
                'sessionId': sid, 'presentation': 'saved'}, cid)
            rows[key] = {**row, **expected_file(body, title=title, path=path,
                working=working, workspace=workspace), 'version': 1}
        unrelated = await command('canvas.show', {'kind': 'markdown', 'title': 'Unrelated retained Canvas',
            'content': '# Keep this historical view\n', 'sessionId': sid, 'presentation': 'saved'}, cid)
        await command('canvas.versions.revise', {'id': unrelated['id'], 'sessionId': sid,
            'expectedRevision': 1, 'content': '# Unrelated latest version\n'}, cid)
        await command('canvas.select', {'id': unrelated['id'], 'sessionId': sid,
            'version': 1, 'presentation': 'canvas'}, cid)
        manifest = {'sessionId': sid, 'messageId': message_id, 'workspace': str(workspace),
                    'workingDirectory': str(working), 'artifacts': rows,
                    'unrelated': {'id': unrelated['id'], 'version': 1}}
        return web.json_response(manifest)

    async def inspect(request):
        guard(request)
        return web.json_response({'seed': manifest, 'newer': newer, 'sourceDeleted': deleted,
                                  'sourceExists': html.exists(), 'sent': runtime.sent,
                                  'started': runtime.started})

    async def revise(request):
        nonlocal newer
        guard(request)
        session, cid = attached(await request.json())
        if not manifest or session['id'] != manifest['sessionId'] or newer or deleted:
            raise web.HTTPConflict(text='Newer source is a single-use seeded-fixture effect')
        html.write_bytes(NEWER.encode('utf-8'))
        newer = await command('canvas.show', {'kind': 'auto', 'path': str(html),
            'title': 'Generated HTML file', 'sessionId': session['id'], 'presentation': 'saved'}, cid)
        assert newer['id'] == manifest['artifacts']['html']['id'] and newer['revision'] == 2
        return web.json_response(newer)

    async def delete_source(request):
        nonlocal deleted
        guard(request)
        session, _ = attached(await request.json())
        if not manifest or session['id'] != manifest['sessionId'] or not newer or deleted:
            raise web.HTTPConflict(text='Delete only the one revised fixture source')
        html.unlink()
        deleted = True
        return web.json_response({'sourceDeleted': True, 'sourceExists': html.exists()})

    async def placement(request):
        guard(request)
        data = await request.json()
        session, _ = attached(data)
        if not manifest or session['id'] != manifest['sessionId']:
            raise web.HTTPConflict()
        # Only these two synthetic placements; no user input path or real move.
        session['workingDirectory'] = str(working if data.get('restore') else workspace)
        session['executionRevision'] = session.get('executionRevision', 0) + 1
        service._publish()
        return web.json_response({'workingDirectory': session['workingDirectory']})

    app.router.add_post('/fixture/file-controls/seed', seed)
    app.router.add_get('/fixture/file-controls', inspect)
    app.router.add_post('/fixture/file-controls/newer', revise)
    app.router.add_post('/fixture/file-controls/delete-source', delete_source)
    app.router.add_post('/fixture/file-controls/placement', placement)
    runner = web.AppRunner(app)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
    for signum in signals:
        loop.add_signal_handler(signum, stopped.set)
    try:
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url, **identity}), flush=True)
        await stopped.wait()
    finally:
        try:
            await runner.cleanup()
        finally:
            for signum in signals:
                loop.remove_signal_handler(signum)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='generated-file-controls-') as directory:
        home = Path(directory)
        asyncio.run(main(home))
    # Emitted only after AppRunner cleanup and removal of owned state/sockets.
    print(json.dumps({'cleanup': {'homeRemoved': not home.exists(), 'pid': os.getpid()}}), flush=True)
