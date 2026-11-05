import asyncio
import copy

import pytest

from amplifier_web.management import Management
from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppError, AppService
from amplifier_web.setup import SetupManager
from test_service import Runtime


def test_capability_reconciliation_keeps_caller_and_configuration_authority():
    from amplifier_web.configuration_authority import requirement
    from amplifier_web.service import ACTION_POLICIES

    assert ACTION_POLICIES['modules.reconcile']['access'] == 'caller_controlled'
    assert requirement('modules.reconcile', {'scope': 'global'}) == ('modules', 'global')
    assert requirement('modules.reconcile', {'scope': 'local'}) == ('modules', 'local')
    with pytest.raises(ValueError, match='explicit configuration scope'):
        requirement('modules.reconcile', {})


@pytest.fixture
async def app(tmp_path, monkeypatch):
    async def no_warm(*args, **kwargs): pass
    monkeypatch.setattr(Management, 'warm_providers', no_warm)
    monkeypatch.setattr(SetupManager, 'ensure_routing_catalog', no_warm)
    selected = tmp_path/'selected'; selected.mkdir()
    edited = tmp_path/'edited'; edited.mkdir()
    service = AppService(tmp_path/'app', Runtime(), workspace=selected)
    service.management = Management(service)
    await service.dispatch('session.create', {'workspace': str(selected), 'bundle': 'anchors'})
    service.state['workspaces'].append({'id': 'edited', 'path': str(edited), 'name': 'Edited workspace'})
    store = SettingsStore(service.data_dir)
    for scope, workspace, model in [('global', selected, 'global-model'), ('local', selected, 'selected-model'), ('local', edited, 'edited-model')]:
        store.update(workspace, scope, lambda settings, model=model: settings.update(config={'providers': [
            {'id': 'one', 'module': 'provider-openai', 'config': {'default_model': model}}]}))
    yield service
    await service.close()


async def run(app, action, args):
    await app.dispatch(action, args)
    for _ in range(200):
        status = app.state.get('actionStatus', {}).get(action, {})
        if status.get('phase') in {'ready', 'error'}:
            assert status['phase'] == 'ready', status
            return
        await asyncio.sleep(.01)
    pytest.fail('Settings command did not complete')


def settings_snapshot(store, *workspaces):
    return {(workspace, scope): copy.deepcopy(store.read(workspace, scope))
            for workspace in workspaces for scope in ('global', 'project', 'local')}


async def test_global_reads_and_provider_save_ignore_selected_workspace(app):
    store = SettingsStore(app.data_dir)
    workspace = app._session()['workspace']
    selected_id = app._session()['id']
    edited = next(w['path'] for w in app.state['workspaces'] if w['id'] == 'edited')
    before = settings_snapshot(store, workspace, edited)
    target = {'configurationTarget': {'kind': 'global'}}
    await run(app, 'providers.list', target)
    assert app.state['setup']['providers'][0]['config']['default_model'] == 'global-model'
    await run(app, 'providers.save', {**target, 'id': 'one', 'module': 'provider-openai',
                                     'config': {'default_model': 'global-saved-model'}})
    after = settings_snapshot(store, workspace, edited)
    assert {key: value for key, value in after.items() if key[1] != 'global'} == {
        key: value for key, value in before.items() if key[1] != 'global'}
    assert store.read(workspace, 'global')['config']['providers'][0]['config']['default_model'] == 'global-saved-model'
    assert app.state['setup']['providers'][0]['config']['default_model'] == 'global-saved-model'
    assert app.state['setup']['configurationTarget'] == target['configurationTarget']
    assert app._session()['id'] == selected_id
    assert app._session()['workspace'] == workspace


@pytest.mark.parametrize('scope', ['local', 'project'])
async def test_workspace_editor_targets_workspace_even_with_another_chat_selected(app, scope):
    store = SettingsStore(app.data_dir)
    selected = app._session()['workspace']
    selected_id = app._session()['id']
    edited = next(w['path'] for w in app.state['workspaces'] if w['id'] == 'edited')
    before = settings_snapshot(store, selected, edited)
    target = {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'edited'}, 'scope': scope}
    await run(app, 'providers.list', target)
    assert app.state['setup']['providers'][0]['config']['default_model'] == 'edited-model'
    await run(app, 'providers.save', {**target, 'id': 'one', 'module': 'provider-openai',
                                     'config': {'default_model': 'edited-saved-model'}})
    await run(app, 'modules.save', {**target, 'section': 'tools', 'module': 'tool-fixture', 'config': {}})
    after = settings_snapshot(store, selected, edited)
    assert {key: value for key, value in after.items() if key != (edited, scope)} == {
        key: value for key, value in before.items() if key != (edited, scope)}
    assert store.read(edited, scope)['config']['providers'][0]['config']['default_model'] == 'edited-saved-model'
    assert app.state['registry']['scope'] == scope
    assert app.state['registry']['modules'][0]['module'] == 'tool-fixture'
    assert app._session()['id'] == selected_id
    assert app._session()['workspace'] == selected


@pytest.mark.parametrize('action, values', [
    ('providers.save', {'id': 'one', 'module': 'provider-openai', 'config': {'default_model': 'must-not-save'}}),
    ('routing.use', {'name': 'balanced'}),
])
@pytest.mark.parametrize('args', [
    {'configurationTarget': {'kind': 'global'}, 'scope': 'local'},
    {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'edited'}, 'scope': 'global'},
    {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'missing'}},
    {'configurationTarget': {'kind': 'global'}, 'sessionId': 'a-chat'},
    {'configurationTarget': {'kind': 'global'}, 'nativeProject': '/project'},
])
async def test_conflicting_or_missing_targets_rejected_before_mutation(app, action, values, args):
    store = SettingsStore(app.data_dir)
    selected = app._session()['workspace']
    selected_id = app._session()['id']
    edited = next(w['path'] for w in app.state['workspaces'] if w['id'] == 'edited')
    before = settings_snapshot(store, selected, edited)
    with pytest.raises(AppError) as error:
        await app.dispatch(action, {**args, **values})
    assert 'Unknown action' not in str(error.value)
    assert settings_snapshot(store, selected, edited) == before
    assert app._session()['id'] == selected_id
    assert app._session()['workspace'] == selected


async def test_stale_workspace_target_cannot_fall_back_to_selected_chat(app):
    store = SettingsStore(app.data_dir)
    selected = app._session()['workspace']
    selected_id = app._session()['id']
    edited = next(w['path'] for w in app.state['workspaces'] if w['id'] == 'edited')
    target = {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'edited'}}
    await run(app, 'providers.list', target)
    assert app.state['setup']['providers'][0]['config']['default_model'] == 'edited-model'
    before = settings_snapshot(store, selected, edited)
    # The editor still has this target and its read result, but registration
    # disappeared before Save. Neither the selected chat nor global is a fallback.
    app.state['workspaces'] = [row for row in app.state['workspaces'] if row['id'] != 'edited']
    with pytest.raises(AppError, match='Choose an existing workspace'):
        await app.dispatch('providers.save', {**target, 'id': 'one', 'module': 'provider-openai',
                                              'config': {'default_model': 'must-not-save'}})
    assert settings_snapshot(store, selected, edited) == before
    assert app._session()['id'] == selected_id
    assert app._session()['workspace'] == selected


def test_switching_owner_cannot_relabel_previous_results(app):
    from amplifier_web.configuration_target import state_for
    app.state['setup'] = {'providers': [{'id': 'workspace-private'}], 'matrix': {'name': 'workspace-rule'}}
    current = state_for(app, 'setup', {'configurationTarget': {'kind': 'global'}})
    assert 'providers' not in current and 'matrix' not in current


async def test_concurrent_settings_reads_keep_each_owners_receipt(app):
    from amplifier_web.configuration_target import operation_key
    targets = [{'configurationTarget': {'kind': 'global'}},
               {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'edited'}}]
    await asyncio.gather(*(app.management.list_providers(args, 'read-'+str(index))
                           for index, args in enumerate(targets)))
    operations = app.state['setup']['operations']
    for index, args in enumerate(targets):
        receipt = operations[operation_key(args, 'providers.list:')]
        assert receipt['phase'] == 'ready'
        assert receipt['commandId'] == 'read-'+str(index)


def test_workspace_settings_resources_and_global_dialog_have_distinct_demand():
    from amplifier_web.browser_demand import project
    state = {'view': {'workSurface': 'workspace', 'workWorkspaceTab': 'details',
                      'workWorkspaceSettings': 'registries'}, 'registry': {'modules': ['fixture']}}
    assert project(copy.deepcopy(state))['registry'] == state['registry']
    state['view'].update(panel='settings', settingsExpanded=['updates'])
    assert 'registry' not in project(copy.deepcopy(state))


async def test_model_discovery_survives_other_owner_and_late_provider_list(app):
    from amplifier_web.configuration_target import state_for
    global_args = {'configurationTarget': {'kind': 'global'}}
    workspace_args = {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'edited'}}
    global_models = [{'id': 'global-catalog-model'}]
    workspace_models = [{'id': 'workspace-catalog-model'}]
    global_catalog = {'one': {'models': global_models, 'supported': True, 'phase': 'ready'}}
    workspace_catalog = {'one': {'models': workspace_models, 'supported': True, 'phase': 'ready'}}
    state_for(app, 'setup', global_args).update(providerCatalogs=copy.deepcopy(global_catalog),
                                               modelCatalogs={'one': copy.deepcopy(global_models)})
    state_for(app, 'setup', workspace_args).update(providerCatalogs=copy.deepcopy(workspace_catalog),
                                                  modelCatalogs={'one': copy.deepcopy(workspace_models)})
    # A late list must not erase already-completed discovery, even when there
    # was no providersWorkspace yet in that owner's partial snapshot.
    await run(app, 'providers.list', global_args)
    assert app.state['setup']['providerCatalogs'] == global_catalog
    assert app.state['setup']['modelCatalogs'] == {'one': global_models}
    restored = state_for(app, 'setup', workspace_args)
    assert restored['providerCatalogs'] == workspace_catalog
    assert restored['modelCatalogs'] == {'one': workspace_models}


def fresh_image_plan(app, workspace):
    from amplifier_web.host.config import read_config
    from amplifier_web.image_migration import apply_legacy_image_intent
    config = read_config(workspace, home=app.data_dir)
    plan = {'tools': [{'module': 'tool-image', 'config': {'allow_paid': True}}]}
    apply_legacy_image_intent(plan, config.settings, scoped_sources=config.image_intent_sources)
    return plan


@pytest.mark.parametrize('scope', ['global', 'project', 'local'])
async def test_scoped_capability_reconciliation_changes_only_explicit_record(app, scope):
    store = SettingsStore(app.data_dir)
    workspace = next(w['path'] for w in app.state['workspaces'] if w['id'] == 'edited')
    store.update(workspace, scope, lambda s: s.update(image_generation={'mode': 'off', 'note': 'preserve'}))
    target = {'configurationTarget': {'kind': 'global'} if scope == 'global' else
              {'kind': 'workspace', 'workspaceId': 'edited'}, 'scope': scope}
    await run(app, 'modules.diagnostics', target)
    diagnostic = app.state['registry']['diagnostics']
    row, = diagnostic['items']
    assert row['module'] == 'tool-image' and row['sourceScope'] == scope
    assert row['sourceKey'] == 'image_generation.mode' and row['state'] == 'disabled'
    old_plan = fresh_image_plan(app, workspace)
    assert old_plan['tools'][0]['config']['allow_paid'] is False
    before = settings_snapshot(store, workspace)
    app.state['view']['draft'] = 'Keep this draft'
    session_before = copy.deepcopy(app._session())
    await run(app, 'modules.reconcile', {**target, 'id': row['id'],
        'expectedRevision': row['revision'], 'resolution': 'inherit', 'confirm': True})
    assert store.read(workspace, scope)['image_generation'] == {'note': 'preserve'}
    for other in ('global', 'project', 'local'):
        if other != scope:
            assert store.read(workspace, other) == before[(workspace, other)]
    receipt = app.state['registry']['reconciliation']
    assert receipt['sourceScope'] == scope and receipt['before'] == 'off' and receipt['after'] is None
    assert receipt['takesEffect'] == 'new_sessions'
    assert fresh_image_plan(app, workspace)['tools'][0]['config']['allow_paid'] is True
    assert old_plan['tools'][0]['config']['allow_paid'] is False
    assert app._session() == session_before and app.state['view']['draft'] == 'Keep this draft'


async def test_other_scope_off_and_unknown_intent_survive_reconciliation(app):
    from amplifier_web.registry import RegistryManager
    store = SettingsStore(app.data_dir)
    workspace = app._session()['workspace']
    store.update(workspace, 'global', lambda s: s.update(image_generation={'mode': 'off'}))
    store.update(workspace, 'project', lambda s: s.update(image_generation={'mode': 'off'}))
    store.update(workspace, 'local', lambda s: s.update(image_generation={'mode': 'automatic'},
        overrides={'account-a': {'config': {'image_generation': {'enabled': True, 'model': 'pinned'}}}}))
    manager = RegistryManager(app.data_dir, store=store)
    diagnostic = await manager.perform('modules.diagnostics', {'workspace': workspace, 'scope': 'project'})
    row = next(r for r in diagnostic['diagnostics']['items'] if r['sourceScope'] == 'project')
    before = store.read(workspace, 'local')
    await manager.perform('modules.reconcile', {'workspace': workspace, 'scope': 'project',
        'id': row['id'], 'expectedRevision': row['revision'], 'resolution': 'inherit', 'confirm': True})
    assert fresh_image_plan(app, workspace)['tools'][0]['config']['allow_paid'] is False
    assert store.read(workspace, 'local') == before  # Account/model pin untouched.
    store.update(workspace, 'project', lambda s: s.update(image_generation={'mode': 'unknown-intent'}))
    diagnostic = await manager.perform('modules.diagnostics', {'workspace': workspace, 'scope': 'project'})
    row = next(r for r in diagnostic['diagnostics']['items'] if r['sourceScope'] == 'project')
    assert row['state'] == 'needs_review' and not row['canReconcile']
    with pytest.raises(ValueError):
        await manager.perform('modules.reconcile', {'workspace': workspace, 'scope': 'project',
            'id': row['id'], 'expectedRevision': row['revision'], 'resolution': 'inherit', 'confirm': True})
    assert store.read(workspace, 'project')['image_generation']['mode'] == 'unknown-intent'


@pytest.mark.parametrize('fault', ['stale', 'rejected', 'interrupted', 'unconfirmed'])
async def test_scoped_reconcile_never_writes_after_invalid_intent_or_authority(app, fault):
    from amplifier_web.registry import RegistryManager
    from amplifier_web.configuration_authority import CURRENT
    store = SettingsStore(app.data_dir); workspace = app._session()['workspace']
    store.update(workspace, 'local', lambda s: s.update(image_generation={'mode': 'off'}))
    manager = RegistryManager(app.data_dir, store=store)
    rows = (await manager.perform('modules.diagnostics', {'workspace': workspace, 'scope': 'local'}))['diagnostics']['items']
    row = next(r for r in rows if r['sourceScope'] == 'local')
    if fault == 'stale':
        store.update(workspace, 'local', lambda s: s.update(routing={'matrix': 'changed'}))
    before = store.path(workspace, 'local').read_bytes()
    def refuse(**kwargs):
        if fault == 'interrupted':
            raise asyncio.CancelledError()
        raise PermissionError('Rejected test intent')
    token = CURRENT.set(refuse) if fault in {'rejected', 'interrupted'} else None
    try:
        with pytest.raises((ValueError, PermissionError, asyncio.CancelledError)):
            await manager.perform('modules.reconcile', {'workspace': workspace, 'scope': 'local',
                'id': row['id'], 'expectedRevision': row['revision'], 'resolution': 'inherit',
                'confirm': fault != 'unconfirmed'})
    finally:
        if token is not None:
            CURRENT.reset(token)
    assert store.path(workspace, 'local').read_bytes() == before


async def test_unrelated_configuration_does_not_reconcile_capability_off(app):
    store = SettingsStore(app.data_dir); workspace = app._session()['workspace']
    store.update(workspace, 'global', lambda s: s.update(image_generation={'mode': 'off'}))
    target = {'configurationTarget': {'kind': 'global'}}
    await run(app, 'providers.save', {**target, 'id': 'one', 'module': 'provider-openai',
                                     'config': {'default_model': 'different-chat'}})
    matrix = store.shared_home / 'routing' / 'balanced.yaml'
    matrix.parent.mkdir(parents=True, exist_ok=True)
    matrix.write_text('name: balanced\nroles:\n  general:\n    description: General\n'
                      '    candidates: [{provider: one, model: fixture}]\n'
                      '  fast:\n    description: Fast\n    candidates: [{provider: one, model: fixture}]\n')
    await run(app, 'routing.use', {**target, 'name': 'balanced'})
    await run(app, 'modules.save', {**target, 'section': 'tools', 'module': 'tool-image', 'config': {'allow_paid': True}})
    store.update(workspace, 'global', lambda s: s.update(bundle={'app': []}))
    assert store.read(workspace, 'global')['image_generation'] == {'mode': 'off'}
    assert fresh_image_plan(app, workspace)['tools'][0]['config']['allow_paid'] is False
