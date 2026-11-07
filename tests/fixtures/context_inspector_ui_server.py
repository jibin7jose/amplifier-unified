"""Actual inspector/capture/read/UI with synthetic provider input; no accounts.

Native Core/Foundation final-refit execution is qualified separately in
 test_context_inspection. This server does not substitute inspector responses.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings_ui_server as settings_fixture
from aiohttp import web
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_web.context_inspection_adapter import ContextInspection
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.session_files import capture_dir
from test_context_inspection import Provider, config


async def main(home):
    app = await settings_fixture.main(home)
    service = app['service']
    session = service._session()
    session['title'] = 'Context inspection fixture'
    sid = session.get('runtimeSessionId') or session['id']
    empty = await service.dispatch('session.create', {'title': 'No observation fixture', 'workspace': session['workspace'], 'select': False})
    await service.dispatch('session.select', {'id': session['id']})
    config(home)
    c = SimpleNamespace(get=lambda name: SimpleNamespace(config={'max_tokens': 6000, 'summarize_trigger': .8}))
    observer = ContextInspection(c, capture_dir(session['workspace'], sid), home)
    events = ExecutionEvents(sid, lambda event: None)
    events.context_inspectors[sid] = observer
    raw = Provider()
    provider = events.instrument_provider(sid, raw)
    async def capture(scenario='normal'):
        config(home, recording=scenario != 'partial')
        value = ChatRequest(model='fixture', max_output_tokens=200, messages=[
            Message(role='system', content='Respond with attributable evidence.\n<context_file paths="@work:context/AGENTS.md → /synthetic/source-store/objects/'+('a'*64)+'/'+('b'*64)+'/context/AGENTS.md">Keep original documents.\n'+('Check the captured request, not current disk.\n'*80)+'</context_file>'),
            Message(role='user', content='Compare the rollout alternatives and retain the supporting evidence.\n' * 140),
            Message(role='assistant', content='Research observations.\n' * 400),
            Message(role='user', content='Continuation note: preserve the accepted scope.\n'*30, metadata={'source':'context-managed','ephemeral':True}),
            Message(role='user', content='Use the current selected request identity.', metadata={'ephemeral':True}),
            Message(role='tool', tool_call_id='fixture-tool', content='Evidence from selected artifact.\n' * 30),
            Message(role='user', content='Opaque reference', metadata={'openai:compaction': {'output':'NEVER-EXPOSE-OPAQUE'}}),
        ], tools=[ToolSpec(name='read_file', description='Read a selected permitted file.', parameters={'type':'object','properties':{'path':{'type':'string'}}})])
        if scenario == 'unicode':
            value.messages[1].content = '😀' * 10000
        if scenario.startswith('long-list'):
            value.messages[0].content = '\n'.join(
                f'<context_file paths="@fixture:source-{i:02}.md → /synthetic/source-store/{"a"*64}/source-{i:02}.md">'
                + (('Long reader line with captured evidence.\n' * 500) if i == 1 else f'Short captured source {i:02}.\nEnd of source.')
                + '</context_file>' for i in range(60)
            )
        if scenario not in ('partial', 'long-list-unknown'):
            await provider.request_budget(value)
        await provider.complete(value)
        await observer.drain()
        if scenario == 'known-window-no-usage':
            latest = observer.store.index()[0]
            observer.store.finish(latest['callId'], 'completed', {})
        if scenario == 'over-window':
            latest = observer.store.index()[0]
            observer.store.finish(latest['callId'], 'completed', {'inputTokens': 9000})
    await capture()
    service._publish()
    async def scenario(request):
        await capture((await request.json()).get('scenario','normal'))
        return web.json_response({'captured': True})
    async def receipt(request):
        return web.json_response({'sessionId':session['id'], 'emptyId':empty['sessionId'], 'providerCalls':len(raw.calls),
            'index':observer.store.index(), 'boundary':'synthetic provider through production observation'})
    app.router.add_get('/fixture/context', receipt)
    app.router.add_post('/fixture/context', scenario)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM): asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    print(json.dumps({'url':url}), flush=True)
    try: await stopped.wait()
    finally: await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='context-inspector-') as temp:
        asyncio.run(main(Path(temp)))
