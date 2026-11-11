"""Real Core/Foundation/loop-live multimodal turn against a local stub provider."""
import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.attachments import save, encode
from amplifier_web.app_guidance import install_app_access, CANVAS_GUIDANCE

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='


async def run():
    plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False}}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent for name in ('loop-live', 'context-simple')}
    prepared = PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name='attachment-fixture', session=plan['session']))
    session = await prepared.create_session(session_id='attachment-fixture')
    runtime = Runtime(session.session_id)
    session.coordinator.register_capability('live.runtime', runtime)
    session.coordinator.register_capability('live.attachments.encode', encode)

    class Provider:
        name = 'fixture'
        requests = []
        def get_info(self):
            return ProviderInfo(id='fixture', display_name='Fixture', defaults={'model': 'fixture-model'})
        def parse_tool_calls(self, response):
            return []
        async def complete(self, request, **kwargs):
            self.requests.append(request)
            return ChatResponse(content=[TextBlock(text='Image received')], usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12))
    provider = Provider()
    await session.coordinator.mount('providers', provider, name='fixture')
    async def bridge(operation, args):
        return {'canvas': {'open': True}, 'operation': operation}
    await install_app_access(session.coordinator, bridge)
    await install_app_access(session.coordinator, bridge)  # idempotent
    tool = session.coordinator.get('tools')['app_control']
    result = await tool.execute({'operation':'get_state'})
    assert result.success and result.output['canvas']['open']
    task = None
    try:
        with tempfile.TemporaryDirectory() as home:
            os.environ['AMPLIFIER_WEB_HOME'] = home
            attachment = save(home, 'tiny.png', PNG)
            task = asyncio.create_task(session.execute(''))
            await runtime.wait_for(lambda e: e['type'] == 'session.ready', timeout=8)
            await runtime.submit(Input('user', 'Please review the attached files.', id='image-only', attachments=(attachment,)))
            event = await runtime.wait_for(lambda e: e['type'] in {'generation.finished', 'generation.failed'}, timeout=8)
            assert event['type'] == 'generation.finished', event
            assert provider.requests
            serialized = provider.requests[0].model_dump_json()
            assert 'canvas.show' in serialized, serialized
            assert 'web-canvas' in str(session.coordinator.hooks) or 'app_control' in serialized
            assert 'one full preview surface at a time' in serialized
            assert 'unified-app' in serialized
            assert 'A progress report is not a completion boundary' in serialized
            assert 'an independent root has no automatic result return' in serialized
            assert 'coordination.send mode:auto' in serialized
            # Recipes are available on demand, not charged to every model call.
            assert 'resetPreview' not in serialized and 'window.BABYLON' not in serialized
            assert len(CANVAS_GUIDANCE.encode('utf-8')) < 9000
            context = await session.coordinator.get('context').get_messages()
            assert CANVAS_GUIDANCE not in json.dumps(context, default=str), 'Ephemeral host instructions must not pollute saved history'

            request = provider.requests[0]
            user = next(message for message in request.messages if message.role == 'user' and isinstance(message.content, list))
            image = next(block for block in user.content if block.type == 'image')
            assert image.source['media_type'] == 'image/png'
            assert base64.b64decode(image.source['data']) == base64.b64decode(PNG)
            assert PNG not in json.dumps(runtime.events)
            await runtime.submit(Input('stop'))
            await asyncio.wait_for(task, 5)
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()
    print(json.dumps({'canvas_guidance_in_provider_request': True, 'app_control_mounted': True, 'instructions_ephemeral': True, 'public_events_contain_no_image_data': True, 'provider_network_calls': 0, 'provider_request_bytes': len(serialized.encode('utf-8')), 'recurring_guidance_bytes': len(CANVAS_GUIDANCE.encode('utf-8'))}))

asyncio.run(run())
