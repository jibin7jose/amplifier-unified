"""Two synthetic accounts; real shared composition, draft probe and native selector.

No provider network, source preparation or paid inference. Does not emulate full
prepare_manager: the synthetic providers replace external module acquisition.
"""
import asyncio
import copy
from collections import Counter
from types import SimpleNamespace

import yaml
from aiohttp import web
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatRequest
from amplifier_foundation import Bundle
from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator

from amplifier_web import draft_defaults, provider_environment, provider_probe
from amplifier_web.draft_defaults_probe import query
from amplifier_web.host import config, session as host_session
from amplifier_web.provider_isolation import require_available
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.setup import SetupManager


class Provider:
    def __init__(self, config):
        self.config = copy.deepcopy(config)
        self.priority = config.get('priority', 100)
        self.default_model = config.get('default_model', 'module-default')
        self.calls = []

    def get_info(self):
        return ProviderInfo(id='fixture', display_name='Synthetic provider', defaults={
            'model': self.default_model, 'reasoning_effort': self.config.get('reasoning_effort')})

    def get_config_schema(self):
        return {'fields': [{'id': 'reasoning_effort', 'field_type': 'choice',
                            'choices': ['low', 'high', 'xhigh'], 'required': False}]}

    async def list_models(self):
        return [{'id': 'cli-model'}, {'id': 'unified-model'}]

    def get_model_list_info(self):
        return {'version':1,'source':self.config.get('catalog_origin','provider-api')}

    async def complete(self, request, **kwargs):
        self.calls.append({'model': kwargs.get('model') or request.model or self.default_model,
            'effort': kwargs.get('reasoning_effort') or request.reasoning_effort or self.config.get('reasoning_effort')})
        return 'synthetic response'


class Scenario:
    def __init__(self, home):
        self.home = home
        self.gate = asyncio.Event()
        self.requests = []
        self.probes = Counter()
        self.faults = {}
        self.settings = home / 'shared-settings/settings.yaml'
        self.rows = [
            {'module': 'provider-fixture', 'id': 'cli', 'label':'Synthetic provider', 'config': {
                'base_url':'https://cli.fixture.test',
                'priority': 1, 'default_model': 'cli-model', 'reasoning_effort': 'low'}},
            {'module': 'provider-fixture', 'id': 'unified', 'label':'Synthetic provider', 'config': {
                'base_url':'https://unified.fixture.test',
                'priority': 20, 'default_model': 'unified-model', 'reasoning_effort': 'high'}},
        ]
        self.save()
        host_session.load_root_bundle = self.root
        provider_environment.provider_class = lambda module: Provider
        provider_probe.provider_class = lambda module: Provider
        self.original_resolve = draft_defaults.resolve_defaults
        draft_defaults.resolve_defaults = self.resolve
        async def catalog(manager, action, args, workspace):
            row = next(row for row in self.rows if row['id'] == args['id'])
            self.probes[row['id']] += 1
            if self.faults.get(row['id'])=='error':
                raise ValueError('Synthetic connection failure')
            result = await provider_probe.query({'module':row['module'],'action':action,'config':row['config']})
            if self.faults.get(row['id'])=='empty':
                result['models']=[]
            if self.faults.get(row['id'])=='unsupported':
                result.update(models=[],modelsSupported=False,modelsProvenance='unsupported')
            return {'modelsProviderId': row['id'], 'models': result['models'],
                    'modelsSupported':result['modelsSupported'], 'modelsProvenance':result['modelsProvenance'],
                    'providerMetadata': {'module': row['module'], 'info': result['info'],
                                         'configSchema': result['configSchema']}}
        # Replace only the external process/acquisition boundary. Keep the real
        # host cache, configured roster, identity, publication and API actions.
        SetupManager.probe = catalog

    def save(self):
        self.settings.write_text(yaml.safe_dump({'bundle': {'active': 'fixture'}, 'config': {'providers': self.rows}}))

    async def root(self, conf, bundle, **kwargs):
        loaded = Bundle.from_dict({'bundle': {'name': bundle}})
        loaded = host_session._apply_settings(loaded, conf)
        if bundle == 'alternate':
            next(row for row in loaded.providers if row['id'] == 'unified')['config']['priority'] = 0
        return None, loaded, None

    async def resolve(self, home, workspace, bundle=None, app_bundle=None, **kwargs):
        if kwargs.get('catalog'):
            return await self.original_resolve(home, workspace, bundle, app_bundle, **kwargs)
        chosen = bundle or app_bundle or 'fixture'
        if chosen == 'alternate':
            await self.gate.wait()
        result = await query({'home': str(home), 'workspace': workspace, 'bundle': chosen,
                              'globalOnly': kwargs.get('global_only', False)})
        return {**result, 'bundle': chosen}

    async def dispatch(self, session):
        _, loaded, _ = await self.root(config.load_config(session['workspace'], home=self.home / 'app'), session['bundle'])
        providers = {row['instance_id']: Provider(row['config']) for row in loaded.providers}
        loop = BundleLiveOrchestrator({})
        plan = {'session': {}, 'providers': loaded.providers}
        coordinator = SimpleNamespace(config=plan, session_state={},
            get=lambda name: {'providers': providers, 'orchestrator': loop, 'context': None}.get(name),
            get_capability=lambda name: None, register_capability=lambda *args: None)
        selected = session.get('selection')
        if selected:
            require_available(coordinator, selected['instance'])
            loop.root_provider = host_session.SelectedProvider(providers[selected['instance']], selected)
        controls = RuntimeControls(SimpleNamespace(session_id=session['id'], coordinator=coordinator, config=plan),
                                   SimpleNamespace(generation=None, queued_inputs=0))
        await controls.restore()
        report = await controls.perform('configuration.providers')
        await loop._select_provider(providers).complete(ChatRequest(messages=[]))
        calls = [{'instance': identity, **call} for identity, provider in providers.items() for call in provider.calls]
        assert len(calls) == 1
        result = {'sessionId': session['id'], 'selection': selected, 'effective': report['effective'], 'dispatch': calls[0]}
        self.requests.append(result)
        await controls.close()
        return result

    def routes(self, app):
        async def inspect(request):
            return web.json_response({'requests': self.requests, 'probes':dict(self.probes)})
        async def fault(request):
            value=await request.json()
            assert value['id'] in {'cli','unified'}
            assert value['mode'] in {'error','empty','unsupported','ready'}
            self.faults[value['id']]=value['mode']
            if 'origin' in value:
                assert value['origin'] in {'provider-api','configured','static','simulated'}
                next(row for row in self.rows if row['id']==value['id'])['config']['catalog_origin']=value['origin']
                self.save()
            return web.json_response({'saved':True})
        async def release(request):
            self.gate.set()
            return web.json_response({'released': True})
        app.router.add_get('/fixture/provider-resolution', inspect)
        app.router.add_post('/fixture/provider-resolution/fault', fault)
        app.router.add_post('/fixture/provider-resolution/release', release)
