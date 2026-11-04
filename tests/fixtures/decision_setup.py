"""Fresh-account settings acceptance with installed Jev metadata and no inference."""
import copy
import os

from aiohttp import web


def prepare():
    from amplifier_web import app_features, decision_settings
    installed = decision_settings.catalogs()
    assert 'provider-jev' in installed, 'Install the optional Jev package for this qualification'
    # Other installed adapters must not supply a connection for this empty-account
    # case. Keep the real provider's validator, credential metadata and model list.
    decision_settings.catalogs = lambda: {'provider-jev': installed['provider-jev']}
    os.environ['TYPESAFE_API_KEY'] = 'fixture-not-a-real-key'
    app_features.status = lambda manager: {
        'supported': True, 'pending': False, 'installedExtras': ['decisions-jev'],
        'features': [{'id': 'decisions-jev', 'label': 'Jev decisions',
                      'installed': True, 'providerModule': 'provider-jev'}]}


def attach(app, workspace):
    from amplifier_web.setup import SetupManager
    service = app['service']
    manager = SetupManager(service.data_dir, global_only=True)
    initial = copy.deepcopy(manager.config(workspace))

    async def observations(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        current = manager.config(workspace)
        ordinary = [row for row in current.providers if row['module'] != 'provider-jev']
        accounts = [row for row in current.providers if row['module'] == 'provider-jev']
        return web.json_response({
            'ordinaryProvidersPreserved': ordinary == initial.providers,
            'ordinaryRoutingPreserved': current.settings.get('routing') == initial.settings.get('routing'),
            'decisionAccountCount': len(accounts),
            'usesEnvironmentReference': bool(accounts) and all(
                row['config'].get('api_key') == '${TYPESAFE_API_KEY}' for row in accounts),
            'selection': current.settings.get('decision_models', {}).get('selection', {}),
        })
    app.router.add_get('/fixture/decision-setup-observations', observations)
