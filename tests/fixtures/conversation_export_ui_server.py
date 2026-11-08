"""Disposable paged conversation fixture, with no provider or real history access."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.automatic_history import display_message
from amplifier_web.host.storage import SessionStore
from amplifier_web.session_files import project_slug


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    source = service._session()
    rows = [{'role': 'user' if index % 2 == 0 else 'assistant', 'content': f'Export message {index}'} for index in range(140)]
    rows[0]['content'] = 'The earliest question'
    rows[-1]['content'] = '```python\nprint("exact α")  \n```\n'
    store = SessionStore.for_app(home, source['workspace'])
    store.save(source['id'], rows, {})
    # This web-owned conversation has a native transcript plus recorded voice
    # bubbles. Native-managed refresh deliberately replaces the whole display.
    source.update(nativeProject=project_slug(source['workspace']), nativeIdentity=source['id'],
                  historyManaged=False, historyLoaded=True, sharedHistoryOffset=120, sharedHistoryTotal=140,
                  messages=[display_message(row, index, source) for index, row in enumerate(rows) if index >= 120])
    source['messages'].append({'id': 'voice', 'role': 'user', 'text': 'Spoken follow-up', 'via': 'call', 'voiceId': 'call'})
    service.state['canvasArtifacts'].append({'id': 'saved-diagram', 'title': 'Export diagram', 'sessionId': source['id'], 'kind': 'markdown', 'messageId': 'voice'})
    service.state['view'].update(panel='settings', settingsSection='setup', settingsExpanded=['conversation'])
    service._publish()
    if os.environ.get('AMPLIFIER_TEST_WORK_DIAGNOSTIC') == '1':
        from amplifier_web.work_windows import _ThreadedWindowProjector
        windows = service.event_log_view.windows
        windows.projector = _ThreadedWindowProjector(service.data_dir)
        def synthetic_failure(*args, **kwargs):
            raise PermissionError(13, 'SECRET-DIAGNOSTIC-CANARY', r'C:\SECRET-DIAGNOSTIC-CANARY')
        windows.projector.model.project = synthetic_failure
        await windows.request(source['id'])
        async def refresh_diagnostic(request):
            # Rebind after normal navigation/publication invalidates the prior
            # attempt. Exercise the actual reader, not a seeded JSON result.
            windows.retry_at[source['id']] = 0
            if source['id'] in windows.pending:
                await windows.pending[source['id']]
                await asyncio.sleep(0)
            await windows.request(source['id'])
            return web.json_response({'ok': True})
        app.router.add_post('/api/fixture/workDiagnostic', refresh_diagnostic)
    async def append_after_preview(request):
        source['messages'].append({'id': 'after-preview', 'role': 'assistant', 'text': 'Appended after review'})
        service._publish()
        return web.json_response({'ok': True})
    app.router.add_post('/api/fixture/exportAppend', append_after_preview)
    runtime_calls = []
    for name in ('start', 'stop', 'control'):
        original = getattr(service.runtime, name)
        async def observed(*args, _name=name, _original=original, **kwargs):
            runtime_calls.append(_name)
            return await _original(*args, **kwargs)
        setattr(service.runtime, name, observed)

    async def inspect_native_window(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            current = service._session(source['id'], hydrate=False)
            messages = current['messages']
            return web.json_response({'sessionId': current['id'],
                'messageIds': [row['id'] for row in messages],
                'messagesSha256': hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest(),
                'nativeSha256': hashlib.sha256((store.directory(source['id'])/'transcript.jsonl').read_bytes()).hexdigest(),
                'runtimeCalls': list(runtime_calls)})
    app.router.add_get('/api/fixture/nativeWindow', inspect_native_window)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-export-ui-') as temp:
        asyncio.run(main(Path(temp)))
