"""Production app, RuntimeManager, subprocess Worker, Core and loop-live.

Only the external provider transports are scripted. No fake runtime, coordinator,
composer action, steering adapter or generation is used. Never uses an account.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))


def log(kind, **data):
    with (Path(os.environ['DIRECTION_FIXTURE']) / 'wire.jsonl').open('a') as stream:
        stream.write(json.dumps({'kind': kind, **data}) + '\n')


def stage(name):
    return (Path(os.environ['DIRECTION_FIXTURE']) / name).exists()


async def wait_stage(name):
    async with asyncio.timeout(90):
        while not stage(name):
            await asyncio.sleep(.02)


def terminal(identity, text='', *, tool=False):
    output = ([{'type': 'function_call', 'id': 'item-tool', 'call_id': 'call-tool',
                'name': 'fixture_hold', 'arguments': '{}', 'status': 'completed'}] if tool else
              [{'type': 'message', 'id': 'message-' + identity, 'role': 'assistant', 'status': 'completed',
                'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}])
    return {'id': identity, 'object': 'response', 'created_at': 1, 'model': 'gpt-6.1-terra',
            'status': 'completed', 'output': output, 'usage': {'input_tokens': 3, 'output_tokens': 2}}


class Socket:
    def __init__(self):
        from websockets.protocol import State
        self.state = State.OPEN
        self.frames = asyncio.Queue()
        self.tasks = []

    async def send(self, wire):
        value = json.loads(wire)
        log('wire', payload=value)
        if value['type'] == 'response.create':
            await self.frames.put(json.dumps({'type': 'response.created', 'response': {'id': 'parent'}}))
            if os.environ.get('DIRECTION_SCENARIO') == 'tool':
                await self.frames.put(json.dumps({'type': 'response.completed', 'response': terminal('parent', tool=True)}))
        elif value['type'] == 'response.steer':
            async def phases():
                await wait_stage('accept')
                await self.frames.put(json.dumps({'type': 'response.steer.accepted', 'steer': {'id': 'steer-one'}}))
                await wait_stage('apply')
                parent = terminal('parent')
                parent.update(status='incomplete', incomplete_details={'reason': 'steered'})
                log('parent-terminal')
                await self.frames.put(json.dumps({'type': 'response.incomplete', 'response': parent}))
                await self.frames.put(json.dumps({'type': 'response.created', 'response': {'id': 'successor'}}))
                await wait_stage('finish')
                await self.frames.put(json.dumps({'type': 'response.completed', 'response': terminal('successor', 'ONE_NATIVE_FINAL')}))
            self.tasks.append(asyncio.create_task(phases()))
        else:
            raise AssertionError('Unexpected native wire operation: ' + value['type'])

    async def recv(self):
        return await self.frames.get()

    async def close(self):
        from websockets.protocol import State
        self.state = State.CLOSED
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def worker():
    import hashlib
    import importlib.metadata
    import httpx
    import websockets
    import amplifier_module_provider_openai.native as native
    import amplifier_module_loop_live.orchestrator as loop
    import amplifier_web
    from amplifier_web.runtime_worker import Worker, configure_protocol
    assert Path(amplifier_web.__file__).resolve().parent == SOURCE / 'amplifier_web'
    log('worker-provenance', source=str(SOURCE),
        packages={name: {'version': importlib.metadata.version(name),
                        'directUrl': json.loads(importlib.metadata.distribution(name).read_text('direct_url.json') or 'null')}
                  for name in ('amplifier-core', 'amplifier-foundation', 'amplifier-module-loop-live',
                               'amplifier-module-provider-openai', 'openai', 'websockets')},
        sourceHashes={str(Path(module.__file__)): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                      for module in (native, loop)})

    async def connect(*args, **kwargs):
        return Socket()

    calls = 0
    async def external_http(client, request, **kwargs):
        nonlocal calls
        # Current SDKs use httpx2; older qualified SDKs use httpx. Script the
        # actual external client in both cases, never the provider/loop adapter.
        http = __import__(type(request).__module__.split('.')[0])
        path = request.url.path
        log('http', path=path)
        if path.endswith('/responses/input_tokens'):
            return http.Response(200, json={'input_tokens': 3}, request=request)
        if path.endswith('/models'):
            return http.Response(200, json={'object': 'list', 'data': []}, request=request)
        if path.endswith('/responses'):
            calls += 1
            payload = json.loads(request.content)
            log('ordinary-request', payload=payload)
            if calls == 1:
                await wait_stage('ordinary-release')
                log('ordinary-parent-terminal')
            response = terminal('ordinary-' + str(calls), 'ORDINARY_FINAL_' + str(calls))
            response['model'] = payload['model']
            if payload.get('stream'):
                item = response['output'][0]
                frames = [
                    {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
                    {'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'content': []}},
                    {'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0,
                     'content_index': 0, 'part': {'type': 'output_text', 'text': '', 'annotations': []}},
                    {'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0,
                     'content_index': 0, 'delta': item['content'][0]['text']},
                    {'type': 'response.output_text.done', 'item_id': item['id'], 'output_index': 0,
                     'content_index': 0, 'text': item['content'][0]['text']},
                    {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                    {'type': 'response.completed', 'response': response}]
                body = ''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames)
                return http.Response(200, content=body, headers={'content-type': 'text/event-stream'}, request=request)
            return http.Response(200, json=response, request=request)
        raise AssertionError('Unscripted external request: ' + str(request.url))

    websockets.connect = connect
    httpx.AsyncClient.send = external_http
    try:
        import httpx2
        httpx2.AsyncClient.send = external_http
    except ImportError:
        pass
    configure_protocol()
    await Worker().run()


async def serve(root):
    import yaml
    import amplifier_module_loop_live
    import amplifier_module_context_simple
    import amplifier_module_provider_openai
    from aiohttp import web
    from amplifier_web import draft_defaults
    from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
    from amplifier_web.draft_defaults_probe import query
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app

    # Existing installed modules only; the fixture never downloads a runtime.
    sources = {name: str(Path(module.__file__).parent) for name, module in (
        ('loop-live', amplifier_module_loop_live), ('context-simple', amplifier_module_context_simple),
        ('provider-openai', amplifier_module_provider_openai))}
    model = 'gpt-5.4' if os.environ.get('DIRECTION_SCENARIO') == 'ordinary' else 'gpt-6.1-terra'
    bundle = root / 'bundle.yaml'
    plan = {'bundle': {'name': 'immediate-direction-fixture', 'version': '1.0.0'},
            'session': {'orchestrator': {'module': 'loop-live', 'config': {
                'configured_bundle': True, 'native_provider': True, 'use_streaming': False,
                'min_delay_between_calls_ms': 0}}, 'context': {'module': 'context-simple', 'source': sources['context-simple']}},
            'providers': [{'module': 'provider-openai', 'source': sources['provider-openai'],
                           'config': {'api_key': 'fixture-not-an-account', 'default_model': model}}]}
    if os.environ.get('DIRECTION_SCENARIO') == 'tool':
        module = root / 'tool' / 'amplifier_module_tool_fixture_hold' / '__init__.py'
        module.parent.mkdir(parents=True)
        module.write_text('''import asyncio, os
from pathlib import Path
from amplifier_core import ToolResult
__amplifier_module_type__ = "tool"
class Hold:
    name="fixture_hold"
    description="Synthetic held tool"
    input_schema={"type":"object","properties":{}}
    async def execute(self, arguments):
        (Path(os.environ["DIRECTION_FIXTURE"])/"tool-running").touch()
        await asyncio.Event().wait()
        return ToolResult(success=True, output="done")
async def mount(coordinator, config=None):
    await coordinator.mount("tools", Hold(), name="fixture_hold")
''')
        plan['tools'] = [{'module': 'tool-fixture-hold', 'source': str(module.parent.parent)}]
    bundle.write_text(yaml.safe_dump(plan))
    shared = root / 'shared'
    shared.mkdir()
    (shared / 'settings.yaml').write_text(yaml.safe_dump({
        'bundle': {'active': str(bundle), 'app': []},
        'web_bundles': {'excluded': list(APPENDED_DEFAULTS)}, 'sources': {'modules': sources}}))
    async def resolver(home, workspace, bundle=None, app_bundle=None, **kwargs):
        return await query({'home': str(home), 'workspace': workspace, 'bundle': bundle,
            'sessionId': kwargs.get('session_id'), 'catalog': kwargs.get('catalog', False),
            'refresh': kwargs.get('refresh', False), 'replacement': kwargs.get('replacement'),
            'desiredBundle': kwargs.get('desired_bundle', False)})
    draft_defaults.resolve_defaults = resolver
    workspace = root / 'workspace'
    workspace.mkdir()
    runtime = RuntimeManager(command=[sys.executable, '-u', str(Path(__file__).resolve()), '--worker'],
                             startup_timeout=45, retention={'prewarm_on_select': False})
    app = await create_app(root / 'web', workspace=workspace, runtime=runtime, voice=False,
                           background_updates=False, preload_providers=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    runtime.app_bridge, runtime.home = service.app_bridge, root / 'web'
    await service.dispatch('session.create', {'bundle': str(bundle), 'title': 'Immediate direction fixture'})
    sid = service._session()['id']
    await service.dispatch('session.naming', {'id': sid, 'automatic': False})
    service.state['updates'].update(items=[], available=0)

    stopped = asyncio.Event()
    async def shutdown(request):
        stopped.set()
        return web.json_response({'stopping': True})
    async def inspect(request):
        path = root / 'wire.jsonl'
        return web.json_response({'wire': [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else [],
            'session': {key: service._session().get(key) for key in ('status', 'collaborationGeneration', 'messages', 'error', 'failure')},
            'toolRunning': stage('tool-running'), 'workers': len(runtime.workers)})
    async def release(request):
        name = (await request.json())['stage']
        assert name in {'accept', 'apply', 'finish', 'ordinary-release'}
        (root / name).touch()
        return web.json_response({'released': name})
    app.router.add_get('/fixture', inspect)
    app.router.add_post('/fixture/release', release)
    app.router.add_post('/fixture/shutdown', shutdown)
    runner = web.AppRunner(app)
    await runner.setup()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    try:
        site = web.TCPSite(runner, os.environ.get('DIRECTION_BIND', '127.0.0.1'),
                           int(os.environ.get('DIRECTION_PORT', '0')))
        await site.start()
        url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url, 'source': str(SOURCE), 'python': sys.executable}), flush=True)
        await stopped.wait()
    finally:
        await runner.cleanup()
        print(json.dumps({'cleanupConfirmed': not runtime.workers}), flush=True)


def isolate(root):
    for key in list(os.environ):
        if key.endswith(('_API_KEY', '_ACCESS_TOKEN')) or key in {'OPENAI_BASE_URL', 'AMPLIFIER_WEB_CONTROL_TOKEN'}:
            os.environ.pop(key, None)
    os.environ.update(HOME=str(root), AMPLIFIER_HOME=str(root / 'shared'),
        AMPLIFIER_WEB_HOME=str(root / 'web'), AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'),
        AMPLIFIER_SESSION_STATE_HOME=str(root / 'ownership'), AMPLIFIER_RUNTIME_IMMUTABLE='1',
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / 'capture'),
        PYTHONPATH=str(SOURCE), DIRECTION_FIXTURE=str(root), OPENAI_API_KEY='fixture-not-an-account')


def deny_external_network():
    # Fail closed below both SDK clients. The scripted WebSocket/HTTP transports
    # above never reach this boundary; only the fixture's loopback app may connect.
    import socket
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def guard(address):
        if isinstance(address, tuple) and address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            raise AssertionError('Fixture refused an unscripted external socket')
    def checked(self, address):
        guard(address)
        return connect(self, address)
    def checked_ex(self, address):
        guard(address)
        return connect_ex(self, address)
    socket.socket.connect, socket.socket.connect_ex = checked, checked_ex


if __name__ == '__main__':
    deny_external_network()
    if '--worker' in sys.argv:
        asyncio.run(worker())
    else:
        with tempfile.TemporaryDirectory(prefix='unified-immediate-direction-') as directory:
            root = Path(directory)
            isolate(root)
            asyncio.run(serve(root))
