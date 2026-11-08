"""One Settings workflow for agents and browser controls, scoped to its owner."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError
from test_agent_canvas_scope import app, caller, command


@pytest.fixture
async def managed_app(app, monkeypatch):
    from amplifier_web.management import Management

    monkeypatch.setattr(Management, 'warm_providers', AsyncMock())
    app.management = Management(app)
    return app


@pytest.fixture
async def settings_app(app):
    previous = app.management
    checks, arrived = [], asyncio.Event()
    async def credentials(*args):
        checks.append(copy.deepcopy(args))
        arrived.set()
    app.management = SimpleNamespace(command=AsyncMock(side_effect=credentials))
    app.settings_test_checks, app.settings_test_arrived = checks, arrived
    queues = []
    for identity in ('caller', 'other'):
        with app.clients.bind(identity):
            queues.append(app.subscribe())
    try:
        yield app
    finally:
        for queue in queues:
            app.unsubscribe(queue)
        app.management = previous


async def agent(app, action, args=None, command_id=None):
    return await app.app_bridge('dispatch', {'responseMode': 'receipt', 'action': action,
        'args': args or {}, **({'id': command_id} if command_id else {})}, caller(app))


def caller_view(app):
    return app.clients.records['caller']['view']


async def dirty(app):
    with app.clients.bind('caller'):
        view = app.canvas_views.summary('primary')
    target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await command(app, 'caller', 'canvas.views.dirty', {**target, 'dirty': True})


async def test_services_are_supported_choices_not_saved_connections_and_do_not_mutate(settings_app):
    app = settings_app
    before = copy.deepcopy(app.clients.records)
    revision = app.state['revision']
    result = await agent(app, 'settings.connection.services')
    assert result['result']['kind'] == 'supported-services'
    assert result['result']['savedConnections'] is False
    assert any(row['module'] == 'provider-openai' for row in result['result']['services'])
    assert app.clients.records == before and app.state['revision'] == revision
    assert not app.settings_test_checks


@pytest.mark.parametrize('owner,scope', [('global', 'global'), ('workspace', 'local'), ('workspace', 'project')])
async def test_select_queues_exact_read_only_owner_and_replays_once(settings_app, owner, scope):
    app = settings_app
    workspace_id = app.clients.records['caller']['selectedWorkspaceId']
    target = {'kind': owner, **({'workspaceId': workspace_id} if owner == 'workspace' else {})}
    other = copy.deepcopy(app.clients.records['other'])
    result = await agent(app, 'settings.connection.select',
        {'module': 'provider-openai', 'scope': scope, 'configurationTarget': target}, 'select-openai')
    await asyncio.wait_for(app.settings_test_arrived.wait(), 2)
    assert app.settings_test_checks == [('providers.credentials', {
        'module': 'provider-openai', 'scope': scope, 'configurationTarget': target}, 'select-openai:credentials', None)]
    assert result['result']['configurationTarget'] == target
    assert result['result']['saved'] is False
    assert result['result']['credentialCheck'] == {
        'action': 'providers.credentials', 'operationId': 'select-openai:credentials', 'status': 'pending'}
    from amplifier_web.configuration_target import operation_key
    queued = app.state['setup']['operations'][operation_key(
        {'configurationTarget': target}, 'providers.credentials:provider-openai')]
    assert queued['phase'] == 'queued' and queued['commandId'] == 'select-openai:credentials'
    assert not any(key in json.dumps(result) for key in ('api_key', 'apiKey', 'privateKey'))
    view = caller_view(app)
    draft = view['aiConnectionEditor'] if owner == 'global' else view['configurationDrafts']['workspace:' + workspace_id]['aiConnectionEditor']
    assert draft['module'] == 'provider-openai' and draft['credentialMode'] == 'auto'
    assert draft['scope'] == scope and draft['initializeRouting'] is False
    assert app.clients.records['other'] == other
    revision, identity = app.state['revision'], draft['id']
    again = await agent(app, 'settings.connection.select',
        {'module': 'provider-openai', 'scope': scope, 'configurationTarget': target}, 'select-openai')
    await asyncio.sleep(0)
    assert again['duplicate'] and again['result'] == result['result']
    assert len(app.settings_test_checks) == 1 and app.state['revision'] == revision
    assert draft['id'] == identity


@pytest.mark.parametrize('available', [False, True])
@pytest.mark.parametrize('route', ['browser', 'agent'])
async def test_select_completes_real_credential_read_for_global_owner(managed_app, monkeypatch, available, route):
    """Exercise the real management/SetupManager path, not just queue admission."""
    from amplifier_web.setup import SetupManager

    app = managed_app
    sentinel = 'synthetic-onboarding-credential-never-a-real-key'
    if available:
        monkeypatch.setenv('OPENAI_API_KEY', sentinel)
    else:
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    original = app.management.command
    completed = asyncio.Event()

    async def observe(action, *args, **kwargs):
        try:
            return await original(action, *args, **kwargs)
        finally:
            if action == 'providers.credentials':
                completed.set()

    monkeypatch.setattr(app.management, 'command', observe)
    probe = AsyncMock(side_effect=AssertionError('Credential discovery must not call a provider'))
    monkeypatch.setattr(SetupManager, 'probe', probe)
    with app.clients.bind('caller'):
        queue = app.subscribe()
    try:
        if route == 'agent':
            receipt = await agent(app, 'settings.connection.select',
                                  {'module': 'provider-openai'}, 'normal-selection')
        else:
            with app.clients.bind('caller'):
                receipt = await app.dispatch('settings.connection.select',
                    {'module': 'provider-openai', 'configurationTarget': {'kind': 'global'}},
                    command_id='normal-selection')
        check_id = receipt['result']['credentialCheck']['operationId']
        await asyncio.wait_for(completed.wait(), 2)
        setup = app.state['setup']
        check = setup['credentialCheck']
        assert setup['configurationTarget'] == {'kind': 'global'}
        assert check['module'] == 'provider-openai' and check['checkedAt'] > 0
        assert check['available'] is available
        assert check['commandId'] == check_id
        operation = setup['operations']['global\nproviders.credentials:provider-openai']
        assert operation['phase'] == 'ready' and operation['commandId'] == check_id
        assert operation['configurationTarget'] == {'kind': 'global'}
        before = copy.deepcopy(setup)
        # Ordinary state reads cannot silently reset a completed configuration owner.
        with app.clients.bind('caller'):
            app.browser_state()
        await app.app_bridge('get_state', {'path': '/setup', 'clientId': 'caller'}, caller(app))
        assert app.state['setup'] == before
        assert sentinel not in json.dumps(app.state)
        assert caller_view(app)['aiConnectionEditor']['saved'] is False
        probe.assert_not_called()
    finally:
        app.unsubscribe(queue)


async def test_select_credential_failure_is_an_error_not_a_negative_check(managed_app, monkeypatch):
    from amplifier_web.setup import SetupManager

    app = managed_app
    def refused(*args, **kwargs):
        raise ValueError('Synthetic private-store refusal')

    monkeypatch.setattr(SetupManager, 'config', refused)
    # UI admission is separate from the completed management command.
    with app.clients.bind('caller'):
        receipt = await app.dispatch('settings.connection.select',
            {'module': 'provider-openai', 'configurationTarget': {'kind': 'global'}})
    identity = receipt['result']['credentialCheck']['operationId']
    for _ in range(100):
        operation = app.state.get('setup', {}).get('operations', {}).get(
            'global\nproviders.credentials:provider-openai', {})
        if operation.get('phase') == 'error':
            break
        await asyncio.sleep(.01)
    assert operation['commandId'] == identity
    assert operation['phase'] == 'error'
    assert operation['error'] == 'Synthetic private-store refusal'
    assert 'credentialCheck' not in app.state['setup']


async def test_credential_read_keeps_its_owner_across_other_setup_publications(managed_app, monkeypatch):
    from amplifier_web.configuration_target import state_for
    from amplifier_web.setup import SetupManager

    app = managed_app
    original = SetupManager.perform
    entered, finish = asyncio.Event(), asyncio.Event()

    async def delayed(self, action, args):
        if action == 'providers.credentials':
            entered.set()
            await finish.wait()
        return await original(self, action, args)

    monkeypatch.setattr(SetupManager, 'perform', delayed)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-owner-isolation-key')
    args = {'module': 'provider-openai', 'configurationTarget': {'kind': 'global'}}
    task = asyncio.create_task(app.management.command('providers.credentials', args, 'owned-check'))
    await asyncio.wait_for(entered.wait(), 2)
    # A composer read with no explicit owner can publish while Settings waits.
    state_for(app, 'setup', {})['credentialCheck'] = {
        'module': 'provider-openai', 'available': False, 'checkedAt': 1}
    finish.set()
    await asyncio.wait_for(task, 2)
    setup = app.state['setup']
    assert setup['configurationTarget'] == {'kind': 'global'}
    assert setup['credentialCheck']['available'] is True
    assert setup['operations']['global\nproviders.credentials:provider-openai']['phase'] == 'ready'
    # Unscoped composer results are not cached as a Settings owner.
    assert 'credentialCheck' not in state_for(app, 'setup', {})
    restored = state_for(app, 'setup', args)
    assert restored['credentialCheck']['available'] is True


async def test_installed_decision_service_uses_scoped_editor_without_chat_routing(settings_app, monkeypatch):
    from amplifier_web import decision_settings
    monkeypatch.setattr(decision_settings, 'catalogs', lambda: {
        'provider-jev': ({'label': 'Jev', 'decision_only': True,
                          'connection': {'auth': 'key'}}, lambda value: value)})
    app = settings_app
    app.state['setup'] = {'configurationTarget': {'kind': 'global'},
                          'providersLoadedAt': 'now', 'providers': []}
    listed = await agent(app, 'settings.connection.services')
    assert any(row['module'] == 'provider-jev' and row['decisionOnly']
               for row in listed['result']['services'])
    selected = await agent(app, 'settings.connection.select', {'module': 'provider-jev'}, 'select-jev')
    await asyncio.wait_for(app.settings_test_arrived.wait(), 2)
    assert selected['result']['saved'] is False
    draft = caller_view(app)['aiConnectionEditor']
    assert draft['module'] == 'provider-jev' and draft['initializeRouting'] is False
    assert app.settings_test_checks == [('providers.credentials', {
        'module': 'provider-jev', 'scope': 'global', 'configurationTarget': {'kind': 'global'}},
        'select-jev:credentials', None)]


@pytest.mark.parametrize('extra', [
    {'module': 'provider-unlisted'},
    {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'missing'}},
    {'configurationTarget': {'kind': 'global'}, 'scope': 'project'},
    {'configurationTarget': {'kind': 'global'}, 'apiKey': 'must-not-be-accepted'},
])
async def test_invalid_selection_has_no_editor_or_credential_effect(settings_app, extra):
    app = settings_app
    before = copy.deepcopy(app.clients.records)
    revision = app.state['revision']
    with pytest.raises(AppError):
        await agent(app, 'settings.connection.select', {'module': 'provider-openai', **extra})
    await asyncio.sleep(0)
    assert app.clients.records == before and app.state['revision'] == revision
    assert not app.settings_test_checks


async def test_missing_management_refuses_before_draft_change_and_signin_does_not_scan(settings_app):
    app = settings_app
    management, app.management = app.management, None
    before = copy.deepcopy(app.clients.records)
    try:
        with pytest.raises(AppError, match='checks are unavailable'):
            await agent(app, 'settings.connection.select', {'module': 'provider-openai'})
        assert app.clients.records == before
        result = await agent(app, 'settings.connection.select', {'module': 'provider-openai-chatgpt'})
        assert result['result']['step'] == 'connect' and 'credentialCheck' not in result['result']
    finally:
        app.management = management
    assert not app.settings_test_checks


async def test_workspace_connection_leaves_global_modal_and_preserves_global_draft(settings_app):
    app = settings_app
    await agent(app, 'settings.open', {'surface': 'dialog'})
    await command(app, 'caller', 'view.update', {'patch': {'aiConnectionEditor': {'step': 'connect', 'id': 'global-draft'}}})
    workspace_id = app.clients.records['caller']['selectedWorkspaceId']
    result = await agent(app, 'settings.connection.start', {'configurationTarget': {'kind': 'workspace', 'workspaceId': workspace_id}})
    view = caller_view(app)
    assert view['panel'] is None and view['settingsCanvas'] is False
    assert view['workSurface'] == 'workspace' and view['workWorkspaceId'] == workspace_id
    assert view['aiConnectionEditor']['id'] == 'global-draft'
    assert view['configurationDrafts']['workspace:' + workspace_id]['aiConnectionEditor']['step'] == 'services'
    assert result['result']['scope'] == 'local' and not app.settings_test_checks


async def test_global_canvas_settings_returns_from_workspace_browsing_without_retargeting_chat(settings_app):
    app = settings_app
    record = app.clients.records['caller']
    selected = record['selectedSessionId'], record['selectedWorkspaceId']
    other = copy.deepcopy(app.clients.records['other'])
    await agent(app, 'settings.connection.start', {'configurationTarget': {
        'kind': 'workspace', 'workspaceId': selected[1]}})
    workspace_drafts = copy.deepcopy(caller_view(app)['configurationDrafts'])
    assert caller_view(app)['workSurface'] == 'workspace'
    opened = await agent(app, 'settings.open', {'surface': 'canvas'})
    assert opened['result']['surface'] == 'canvas'
    assert opened['result']['configurationTarget'] == {'kind': 'global'}
    assert caller_view(app)['workSurface'] == 'chat'
    assert record['canvas']['kind'] == 'settings' and record['canvas']['open']
    assert (record['selectedSessionId'], record['selectedWorkspaceId']) == selected
    assert caller_view(app)['configurationDrafts'] == workspace_drafts
    assert app.clients.records['other'] == other
    assert not app.settings_test_checks


async def test_open_reuses_settings_and_close_preserves_other_artifacts_and_client(settings_app):
    app = settings_app
    await command(app, 'caller', 'canvas.show', {'kind': 'text', 'content': 'Keep this artifact'})
    artifact_id = app.clients.records['caller']['canvas']['id']
    other = copy.deepcopy(app.clients.records['other'])
    opened = await agent(app, 'settings.open')
    settings_id = app.clients.records['caller']['canvas']['id']
    assert opened['result']['surface'] == 'canvas' and settings_id != artifact_id
    await agent(app, 'settings.open')
    assert app.clients.records['caller']['canvas']['id'] == settings_id
    assert len([row for row in app.state['canvasArtifacts'] if row['kind'] == 'settings' and row['sessionId'] == caller(app)]) == 1
    closed = await agent(app, 'settings.close')
    assert closed['result']['surface'] == 'closed'
    assert app.clients.records['caller']['canvas']['id'] == artifact_id
    assert app.clients.records['caller']['canvas']['content'] == 'Keep this artifact'
    assert app.clients.records['other'] == other
    before = copy.deepcopy(app.clients.records['caller']['canvas'])
    again = await agent(app, 'settings.close')
    assert again['result']['changed'] is False and app.clients.records['caller']['canvas'] == before


async def test_dirty_viewer_refuses_open_and_close_before_any_presentation_change(settings_app):
    app = settings_app
    await command(app, 'caller', 'canvas.show', {'kind': 'text', 'content': 'Unsaved'})
    await dirty(app)
    before = copy.deepcopy(app.clients.records)
    with pytest.raises(AppError, match='primary viewer edit'):
        await agent(app, 'settings.open')
    assert app.clients.records == before
    with app.clients.bind('caller'):
        view = app.canvas_views.summary('primary')
    target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await command(app, 'caller', 'canvas.views.dirty', {**target, 'dirty': False})
    await agent(app, 'settings.open')
    await dirty(app)
    # A dialog may overlay the mounted Settings artifact. Closing both must
    # refuse atomically while that artifact still has a dirty primary viewer.
    await agent(app, 'settings.open', {'surface': 'dialog'})
    assert app.clients.records['caller']['canvas']['open']
    assert not caller_view(app).get('canvasDraft', {}).get('library')
    before = copy.deepcopy(app.clients.records)
    with pytest.raises(AppError, match='primary viewer edit'):
        await agent(app, 'settings.close')
    assert app.clients.records == before


async def test_closing_dialog_over_library_does_not_close_or_discard_dirty_artifact(settings_app):
    app = settings_app
    await agent(app, 'settings.open')
    await dirty(app)
    # Legacy panel navigation explicitly switches the Canvas to its library;
    # Settings.close therefore only dismisses the dialog, not that saved tab.
    await command(app, 'caller', 'view.update', {'patch': {'panel': 'settings'}})
    assert caller_view(app)['canvasDraft']['library'] is True
    record = app.clients.records['caller']
    canvas, preferences = copy.deepcopy(record['canvas']), copy.deepcopy(record['canvasViews'])
    result = await agent(app, 'settings.close')
    assert result['result']['surface'] == 'closed'
    assert caller_view(app)['panel'] is None
    assert record['canvas'] == canvas and record['canvasViews'] == preferences


async def test_settings_cannot_retarget_foreign_client(settings_app):
    app = settings_app
    before = copy.deepcopy(app.clients.records)
    with pytest.raises(AppError, match='calling conversation'):
        await agent(app, 'settings.connection.select', {'module': 'provider-openai', 'clientId': 'other'})
    assert app.clients.records == before and not app.settings_test_checks


async def test_settings_rechecks_client_after_waiting_for_mutation_lock(settings_app, monkeypatch):
    app = settings_app
    sid, entered = caller(app), asyncio.Event()
    original = app.dispatch
    async def observed(action, *args, **kwargs):
        if action == 'settings.connection.select':
            entered.set()
        return await original(action, *args, **kwargs)
    monkeypatch.setattr(app, 'dispatch', observed)
    async with app.lock:
        pending = asyncio.create_task(app.app_bridge('dispatch', {'responseMode': 'receipt',
            'action': 'settings.connection.select', 'args': {'module': 'provider-openai'}}, sid))
        await asyncio.wait_for(entered.wait(), 2)
        record = app.clients.records['caller']
        record['selectedSessionId'] = app.clients.records['other']['selectedSessionId']
        record['selectedWorkspaceId'] = app.clients.records['other']['selectedWorkspaceId']
        before = copy.deepcopy(record)
    with pytest.raises(AppError, match='calling conversation|client changed chats'):
        await pending
    assert record == before and not app.settings_test_checks
