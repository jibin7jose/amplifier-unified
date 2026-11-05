"""Synthetic image lifecycle and verified receipts; no model or image API calls."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if os.environ.get("AMPLIFIER_TEST_INSTALLED_STATIC") != "1":
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from aiohttp import web
from amplifier_web import auth, execution
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.server import create_app
from test_output_images import png


class NoModel:
    async def start(self, *args, **kwargs): raise AssertionError('No model startup')
    async def send(self, *args, **kwargs): raise AssertionError('No model input')
    async def stop(self, *args, **kwargs): pass
    async def close(self): pass


async def main():
    with tempfile.TemporaryDirectory(prefix='unified-generated-images-') as temporary:
        root = Path(temporary)
        os.environ.update(AMPLIFIER_HOME=str(root / 'native'), AMPLIFIER_WEB_HOME=str(root / 'app'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'))
        auth.authenticate_pam = lambda username, password: (username, password) == ('image-fixture', 'fixture-password')
        from configured_browser import seed_connection
        seed_connection(root / 'native')
        app = await create_app(root / 'app', workspace=root, runtime=NoModel(), voice=False,
                               preload_providers=False, background_updates=False)
        service = app['service']
        app['control_token'] = 'fixture-generated-images'
        await service.dispatch('session.create', {'title': 'Generated image fixture'})
        session = service._session()
        sid = session['id']
        session['messages'] = [{'id': 'earlier', 'role': 'assistant', 'text': 'Earlier conversation.\n\n' * 30, 'createdAt': time.time() - 60},
                               {'id': 'origin', 'role': 'user', 'text': 'Create two image concepts.',
                                'inputId': 'image-input', 'createdAt': time.time()}]
        execution.ensure_turn(session, 'image-input')
        session['status'] = 'idle'
        service._publish()
        emitted = []
        observer = ExecutionEvents(sid, emitted.append)
        observer.lifecycle({'type': 'input.delivered', 'input_id': 'image-input'})

        async def flush():
            while emitted:
                kind, payload = normalize_event(emitted.pop(0), sid)
                await service.on_runtime_event(kind, payload)

        async def control(request):
            args = await request.json()
            identity = args.get('id', 'first')
            if args['op'] == 'start':
                session['execution']['turns'][0].update(phase='running', endedAt=None)
                await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'working'})
                observer.hook(sid, 'tool:pre', {'tool_name': 'image_generate', 'tool_call_id': identity,
                                               'tool_input': {'action': 'generate', 'request_id': identity, 'prompt': 'Private synthetic prompt'}})
                await flush()
            elif args['op'] == 'delegated-start':
                session['execution']['turns'][0].update(phase='running', endedAt=None)
                await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'working'})
                observer.hook(sid, 'tool:pre', {'tool_name': 'delegate', 'tool_call_id': 'delegate-image', 'tool_input': {}})
                observer.lifecycle({'type': 'child.updated', 'sessionId': 'image-child', 'parentSessionId': sid,
                                    'callId': 'delegate-image', 'status': 'running', 'agent': 'image-worker'})
                observer.hook('image-child', 'tool:pre', {'tool_name': 'nano-banana', 'tool_call_id': 'nano-image',
                    'tool_input': {'operation': 'generate', 'prompt': 'Synthetic delegated request'}})
                observer.hook('image-child', 'tool:pre', {'tool_name': 'nano-banana', 'tool_call_id': 'nano-analysis',
                    'tool_input': {'operation': 'analyze'}})
                await flush()
            elif args['op'] == 'delegated-finish':
                observer.hook('image-child', 'tool:post', {'tool_call_id': 'nano-image',
                    'result': {'success': True, 'output': {'generated_images': ['synthetic-output.png']}}})
                observer.lifecycle({'type': 'child.updated', 'sessionId': 'image-child', 'parentSessionId': sid,
                                    'callId': 'delegate-image', 'status': 'completed'})
                await flush()
            elif args['op'] == 'later':
                session['messages'].append({'id': 'later', 'role': 'user', 'text': 'Keep these with the original request.', 'createdAt': time.time()})
                service._publish_changes(sessions={sid})
            elif args['op'] == 'finish':
                width, height = {'first': (4, 4), 'second': (8, 4), 'third': (4, 8)}[identity]
                image = png(width, height)
                (root / (identity + '.png')).write_bytes(image)
                receipt = {'schema': 'amplifier.image.receipt.v1', 'status': 'completed', 'requestId': identity,
                           'requestHash': 'a' * 64, 'backend': 'fixture', 'model': 'fixture', 'operation': 'generate', 'inputs': [],
                           'artifact': {'path': identity + '.png', 'sha256': hashlib.sha256(image).hexdigest(), 'bytes': len(image),
                                        'mimeType': 'image/png', 'width': width, 'height': height, 'mode': 'RGB'}}
                (root / (identity + '.json')).write_text(json.dumps(receipt))
                if identity == 'first':
                    receipt['receiptPath'] = identity + '.json'
                observer.hook(sid, 'tool:post', {'tool_call_id': identity, 'result': {'success': True, 'output': receipt}})
                await flush()
                if identity == 'first':
                    result = service.outputs.store.list(sid)['items'][0]
                    return web.json_response({'accepted': True, 'result': result})
                result = await service.dispatch('outputs.attachImage', {'sessionId': sid, 'messageId': 'origin',
                    'title': identity.title() + ' concept', 'receiptPath': identity + '.json'}, command_id='attach-' + identity)
                return web.json_response(result)
            elif args['op'] == 'unknown':
                observer.hook(sid, 'tool:post', {'tool_call_id': identity, 'result': {'success': True, 'output': {'status': 'unknown'}}})
                await flush()
            elif args['op'] == 'stop':
                await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
            return web.json_response({'ok': True})

        app.router.add_post('/api/fixture/images', control)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(json.dumps({'url': f'http://127.0.0.1:{port}', 'package': str(Path(sys.modules['amplifier_web.server'].__file__).resolve())}), flush=True)
        stopped = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        await stopped.wait()
        await runner.cleanup()


asyncio.run(main())
