"""Real semantic Settings bridge and UI with synthetic credentials; no provider calls."""
import asyncio
import hashlib
import json
import os

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web import setup

original_main = fixture.main


async def main(home):
    os.environ['OPENAI_API_KEY'] = 'synthetic-openai-browser-fixture-key'
    os.environ.pop('ANTHROPIC_API_KEY', None)
    for name in setup.PROVIDER_ENV['provider-github-copilot']:
        os.environ.pop(name, None)
    setup.github_cli_token = lambda: None
    app = await original_main(home)
    service = app['service']
    calls = []
    original_command = service.management.command
    original_perform = fixture.SetupManager.perform
    credential_mode, release = 'available', asyncio.Event()
    release.set()

    async def perform(manager, action, args):
        if action == 'providers.credentials':
            # The explicit second-client probe can finish while the first
            # default-variable read remains held by the synthetic fixture.
            if not args.get('envVar'):
                await asyncio.wait_for(release.wait(), 10)
            if credential_mode == 'error':
                raise ValueError('Synthetic credential store could not be read.')
        return await original_perform(manager, action, args)

    fixture.SetupManager.perform = perform

    async def command(action, args, command_id=None, authority=None):
        calls.append({'action': action, 'configurationTarget': args.get('configurationTarget'),
                      'module': args.get('module'), 'commandId': command_id})
        return await original_command(action, args, command_id, authority)

    service.management.command = command
    settings_path = home / 'native/settings.yaml'
    original_settings = hashlib.sha256(settings_path.read_bytes()).hexdigest()

    async def agent(request):
        data = await request.json()
        result = await service.app_bridge('dispatch', {
            'responseMode': 'receipt', 'action': data['action'],
            'args': {**data.get('args', {}), **(
                {'clientId': data['clientId']} if data['action'].startswith('settings.') else {})},
            **({'id': data['id']} if data.get('id') else {}),
        }, service._session()['id'])
        return web.json_response(result)

    async def inspect(request):
        return web.json_response({'calls': calls,
            'savedSettingsUnchanged': original_settings == hashlib.sha256(settings_path.read_bytes()).hexdigest()})

    async def credentials(request):
        nonlocal credential_mode
        credential_mode = (await request.json())['mode']
        assert credential_mode in {'hold', 'available', 'missing', 'error'}
        if credential_mode == 'hold':
            release.clear()
        else:
            if credential_mode == 'missing':
                os.environ.pop('OPENAI_API_KEY', None)
                os.environ.pop('GITHUB_TOKEN', None)
            else:
                os.environ['OPENAI_API_KEY'] = 'synthetic-openai-browser-fixture-key'
                os.environ['GITHUB_TOKEN'] = 'synthetic-github-browser-fixture-token'
            release.set()
        return web.json_response({'mode': credential_mode})

    app.router.add_post('/fixture/semantic-settings-agent', agent)
    app.router.add_get('/fixture/semantic-settings', inspect)
    app.router.add_post('/fixture/semantic-credentials', credentials)
    return app


fixture.main = main
if __name__ == '__main__':
    asyncio.run(fixture.serve())
