"""Bundled setup presentation over the public connection contract."""
from __future__ import annotations

import html
import io
import json
import re
import zipfile
from pathlib import Path

from aiohttp import web

from .device_capabilities import GRANTS
from .device_connections import browser_cookie, csrf
from .terminal_setup import require_safe_transport

ASSETS = Path(__file__).parent / 'device_client'


def page(title, content, *, script=None):
    text = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + html.escape(title) + ' · Amplifier</title>'
            '<link rel="stylesheet" href="/connect/assets/connect.css"></head><body>'
            '<main><a class="back" href="/">← Amplifier</a>' + content + '</main>'
            + (f'<script type="module" src="/connect/assets/{script}"></script>' if script else '')
            + '</body></html>')
    return web.Response(text=text, content_type='text/html')


def setup_routes(app):
    manager = app['device_connections']

    async def connect(request):
        require_safe_transport(request)
        browser_cookie(request)
        return page('Devices & apps', (ASSETS / 'web/connect.html').read_text(), script='connect.js')

    async def verify(request):
        require_safe_transport(request)
        browser_cookie(request)
        identity = request.query.get('id', '')
        row = manager.pairings.find(identity)
        enrollment = row.get('enrollment')
        if not enrollment:
            raise web.HTTPSeeOther('/devices/pair?id=' + identity)
        if request.method == 'POST':
            form = await request.post()
            import hmac
            if not hmac.compare_digest(str(form.get('csrf', '')), csrf(request)):
                raise web.HTTPForbidden(text='This approval page expired. Reload and try again.')
            if form.get('decision') not in {'allow', 'deny'}:
                raise web.HTTPBadRequest()
            manager.pairings.decide(identity, form['decision'] == 'allow')
        if row['state'] != 'pending':
            allowed = row['state'] == 'approved'
            return page('Connection ' + ('approved' if allowed else 'denied'),
                        '<h1>' + ('Connected' if allowed else 'Connection denied') + '</h1><p>'
                        + ('The app will finish connecting automatically. You can close this page.'
                           if allowed else 'No new access was granted.') + '</p><a class="button" href="/connect">Devices & apps</a>')
        access = ''.join('<li>' + html.escape(GRANTS[g]) + '</li>' for g in enrollment['grants'])
        upgrading = bool(enrollment.get('upgradeDevice'))
        return page('Allow this connection?', '<p class="eyebrow">DEVICE CONNECTION</p><h1>'
            + ('Add access for this app?' if upgrading else 'Connect this app?') + '</h1>'
            '<p>Approve only if you started this connection and the code matches the app.</p><div class="code">'
            + html.escape(row['code']) + '</div><p><strong>' + html.escape(row['name']) + '</strong> is asking to connect to <strong>'
            + html.escape(request.host) + '</strong>.</p><p class="muted">App label: '
            + html.escape(enrollment['appId']) + ' · supplied by the app</p><ul>' + access
            + '</ul><p>You choose the browser document locally. You can stop it there or remove access here at any time.</p>'
            '<form method="post"><input type="hidden" name="csrf" value="' + csrf(request) + '">'
            '<button class="primary" name="decision" value="allow">' + ('Allow added access' if upgrading else 'Allow connection')
            + '</button><button name="decision" value="deny">Deny</button></form>')

    async def code(request):
        require_safe_transport(request)
        browser_cookie(request)
        supplied = request.query.get('code', '').strip().upper()
        if supplied:
            if not re.fullmatch(r'[A-Z2-9]{4}-?[A-Z2-9]{4}', supplied):
                raise web.HTTPBadRequest(text='Enter the eight-character code displayed in the app.')
            supplied = supplied.replace('-', '')
            manager.pairings.clean()
            for identity, row in manager.pairings.pending.items():
                if row.get('enrollment') and row['code'].replace('-', '') == supplied:
                    raise web.HTTPSeeOther('/connect/verify?id=' + identity)
            return page('Code not found', '<h1>That code expired or was not found.</h1><p>Start a new connection in the app.</p>'
                        '<a href="/connect/code">Try another code</a>')
        return page('Enter a connection code', '<h1>Connect another device</h1><p>Enter the code shown in the app you are connecting.</p>'
                    '<form method="get"><label for="code">Connection code</label><input id="code" name="code" '
                    'placeholder="ABCD-2345" maxlength="9" autocomplete="off" required><button class="primary">Continue</button></form>')

    async def asset(request):
        name = request.match_info['name']
        types = {'connect.css': 'text/css', 'connect.js': 'text/javascript', 'sample.js': 'text/javascript'}
        if name not in types:
            raise web.HTTPNotFound()
        return web.Response(text=(ASSETS / 'web' / name).read_text(), content_type=types[name])

    async def download(request):
        require_safe_transport(request)
        browser_cookie(request)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((ASSETS / 'extension').iterdir()):
                if path.is_file() and path.suffix in {'.json', '.js', '.html', '.css', '.txt'}:
                    archive.writestr('amplifier-connect/' + path.name, path.read_bytes())
        return web.Response(body=stream.getvalue(), content_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="amplifier-connect-preview.zip"'})

    async def catalog(request):
        browser_cookie(request)
        manifest = json.loads((ASSETS / 'extension/manifest.json').read_text())
        import base64, hashlib
        extension_id = ''.join(chr(ord('a') + int(c, 16)) for c in hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32])
        return web.json_response({'extensionId': extension_id, 'download': '/connect/download/extension',
                                 'referenceClient': '/connect/download/reference', 'distribution': 'development-preview'})

    async def reference(request):
        require_safe_transport(request)
        browser_cookie(request)
        return web.Response(body=(ASSETS / 'reference_client.py').read_bytes(), content_type='text/x-python',
            headers={'Content-Disposition': 'attachment; filename="amplifier-connect.py"'})

    async def sample(request):
        return page('Connection sample', '<p class="eyebrow">BROWSER CONNECTION SAMPLE</p><h1>Save a team selection</h1>'
            '<p>Open the Amplifier Connect extension and choose <strong>Use this tab</strong>. '
            'Then return to Devices & apps on any connected screen to try it.</p>'
            '<form id="sample"><label for="name">Name</label><input id="name" autocomplete="off">'
            '<fieldset><legend>Team</legend><label><input type="radio" name="team" id="blue" value="blue"> Blue</label>'
            '<label><input type="radio" name="team" id="green" value="green"> Green</label></fieldset>'
            '<button id="save" class="primary">Save</button></form><p id="saved" role="status">Nothing saved yet.</p>'
            '<p>Saved changes: <span id="count">0</span></p>', script='sample.js')

    app.router.add_get('/connect', connect, allow_head=False)
    app.router.add_get('/connect/code', code, allow_head=False)
    app.router.add_get('/connect/verify', verify, allow_head=False)
    app.router.add_post('/connect/verify', verify)
    app.router.add_get('/connect/assets/{name}', asset)
    app.router.add_get('/connect/catalog', catalog)
    app.router.add_get('/connect/download/extension', download)
    app.router.add_get('/connect/download/reference', reference)
    app.router.add_get('/connect/sample', sample)
