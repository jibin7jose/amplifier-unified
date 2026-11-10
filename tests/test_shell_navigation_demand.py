"""Resource demand bounds bundled reads; explicit complete APIs remain available."""
from copy import deepcopy
import json
import pytest

from amplifier_web.service import AppError
from amplifier_web.shell_navigation_demand import NAVIGATION_RESOURCES, parse_navigation_resources
from amplifier_web.adaptive_work import STATUS_RESOURCES
from test_shell_modules import service, stage, record_validation


def test_resource_parser_requires_demand_and_bounds_untrusted_input():
    assert parse_navigation_resources('[]') == frozenset()
    assert parse_navigation_resources('["recentNavigation","recentNavigation"]') == {'recentNavigation'}
    for value in (None, 'null', '{}', '"recentNavigation"', '[0]', '[{}]', '["unknown"]', '[', ' '*4097,
                  json.dumps(['recentNavigation']*17), '['*1000, 12):
        with pytest.raises(AppError): parse_navigation_resources(value)


async def test_explicit_full_snapshot_and_custom_packages_keep_existing_contract(service):
    client = service.shell.client('demand-client')
    instance = {'id':'chats','package':'builtin.chats','slot':'navigation'}
    full = service.shell.snapshot(client, instance, identity='demand-client')
    assert 'navigationResources' not in full
    assert full == service.shell.snapshot(client, instance, identity='demand-client', navigation_resources=None)
    digest = await stage(service)
    record_validation(service, digest)
    custom = {**instance, 'id':'custom', 'package':digest}
    before = service.shell.snapshot(client, custom, identity='demand-client')
    assert service.shell.snapshot(client, custom, identity='demand-client', navigation_resources=frozenset()) == before
    assert all(key in before for key in ('chatNavigation','homeNavigation','recentNavigation','workspaceExplorer'))
    receipt = await service.dispatch('shell.query', {'clientId':'demand-client','instanceId':'chats'}, origin='agent')
    assert 'chatNavigation' in receipt['result'] and 'navigationResources' not in receipt['result']


@pytest.mark.parametrize('resource', sorted(NAVIGATION_RESOURCES))
async def test_each_requested_resource_matches_complete_snapshot(service, resource):
    client = service.shell.client('demand-client')
    # A pinned scope exercises the home alias and preserves its workspace fence.
    instance = {'id':'chats','package':'builtin.chats','slot':'navigation',
                'scope':{'mode':'pinned','workspaceId':'two'}}
    full = service.shell.snapshot(client, instance, identity='demand-client')
    scoped = service.shell.snapshot(client, instance, identity='demand-client', navigation_resources=frozenset({resource}))
    assert scoped['navigationResources'] == [resource]
    if resource in STATUS_RESOURCES:
        # Status is explicit bundled demand; third-party full snapshots do not
        # acquire additional pages. Its rows still come from the same catalog.
        assert resource not in full
        expected = full['workHome']['groups'][STATUS_RESOURCES[resource]]
        assert {key: scoped[resource][key] for key in expected} == expected
        assert scoped[resource]['workScope'] == 'two'
    elif resource.startswith('sidebar'):
        field = resource.removeprefix('sidebar').lower()
        assert scoped['sidebarNavigation'][field] == full['sidebarNavigation'][field]
        assert set(scoped['sidebarNavigation']) == {'recentView', field}
    else:
        assert scoped[resource] == full[resource]
    if resource == 'recentNavigation':
        assert scoped['recentScope'] == full['recentScope']
        assert scoped['recentShortcuts'] == full['recentShortcuts']
    if resource == 'workspaceShortcuts':
        assert 'unpinnedWorkspaceShortcuts' in full
        assert 'unpinnedWorkspaceShortcuts' not in scoped
    for key in ('chatNavigation','homeNavigation','recentNavigation','workspaceExplorer','workspaceOverview','pinnedNavigation'):
        assert (key in scoped) == (key == resource)
    assert scoped['view'] == full['view'] and scoped['selectedWorkspaceId'] == 'two'


async def test_quiet_sidebar_never_constructs_hidden_pages(service, monkeypatch):
    client = service.shell.client('demand-client')
    before = deepcopy(service.state)
    calls = []
    original = service.projections.chats
    def chats(state, *, section=None):
        calls.append(section)
        assert section == 'shortcuts', 'Unrequested page was constructed'
        return original(state, section=section)
    monkeypatch.setattr(service.projections, 'chats', chats)
    def forbidden(*args, **kwargs): raise AssertionError('Unrequested workspace page')
    monkeypatch.setattr(service.projections, 'workspaces', forbidden)
    monkeypatch.setattr(service.projections, 'workspace_shortcuts', forbidden)
    wanted = frozenset({'recentNavigation'})
    result = service.shell.inspect('demand-client', snapshots=True, navigation_resources=wanted)
    snapshots = [row for row in result['snapshots'].values() if row.get('navigationResources') is not None]
    assert snapshots and calls == ['shortcuts']*len(snapshots)
    for row in snapshots:
        assert row['navigationResources'] == ['recentNavigation']
        assert row['sidebarNavigation'].keys() == {'recentView'}
        visible = {item['id'] for item in row['recentShortcuts']}
        assert set(row['attention']['sessions']) <= visible
        assert set(row['conversationOrganization']['archived']) <= visible
    assert service.state == before


async def test_empty_demand_retains_controller_metadata_without_pages(service, monkeypatch):
    client = service.shell.client('demand-client')
    def forbidden(*args, **kwargs): raise AssertionError('Unrequested projection')
    for method in ('chats','pinned_navigation','workspaces','workspace_shortcuts'):
        monkeypatch.setattr(service.projections, method, forbidden)
    row = service.shell.snapshot(client, {'id':'workspaces','package':'builtin.workspaces','slot':'navigation'},
        identity='demand-client', navigation_resources=frozenset())
    assert row['navigationResources'] == []
    assert row['attention'] == {'sessions':{},'items':[]}
    assert all(key in row for key in ('view','settings','workspaceDefaults','workspaces','library','generation','sharedHistory'))


@pytest.mark.parametrize('pinned,workspace,expected', [
    (False, 'one', None), (True, 'two', 'two'), (True, 'gone', None),
    (True, 'unknown', None), (True, 'missing', None),
])
async def test_recent_controller_scope_does_not_depend_on_page(service, monkeypatch, pinned, workspace, expected):
    from amplifier_web.shell_resources import context_value, page_value
    client = service.shell.client('recent-controller')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation',
                'scope': {'mode': 'pinned', 'workspaceId': workspace} if pinned else {'mode': 'all'}}
    full = service.shell.navigation(client, instance, identity='recent-controller')
    expected_scope = {'mode': 'workspace' if pinned else 'all', 'workspaceId': expected,
        'filter': '', 'age': 'all', 'clientId': 'recent-controller', 'instanceId': 'chats',
        'generation': client['views'].get('chats', {}).get('generation', 0)}
    assert full['recentScope'] == expected_scope
    assert {key: full['recentNavigation']['scope'][key] for key in expected_scope} == expected_scope
    assert 'recentScope' not in page_value(full, 'recentNavigation')

    def forbidden(*args, **kwargs):
        raise AssertionError('Controller identity must not read a navigation page')
    for method in ('chats', 'pinned_navigation', 'workspaces', 'workspace_shortcuts'):
        monkeypatch.setattr(service.projections, method, forbidden)
    pending = service.shell.navigation(client, instance, identity='recent-controller',
                                       navigation_resources=frozenset())
    assert context_value(pending)['recentScope'] == expected_scope
    assert 'recentNavigation' not in pending


async def test_pending_recent_resource_keeps_exact_controller_identity(service, monkeypatch):
    from amplifier_web.shell_resources import scope_for
    from amplifier_web.shell_query_owner import PendingQuery
    client = service.clients.attach('recent-pending')
    client.update(selectedSessionId='alpha', selectedWorkspaceId='one')
    def pending(*args, **kwargs):
        raise PendingQuery()
    monkeypatch.setattr(service.projections, 'chats', pending)
    with service.clients.bind('recent-pending'):
        owner = service.shell.resources
        owner.register('recent-pending', 1, ['recentNavigation'], scope_for(service.state))
        envelope = owner.snapshot('recent-pending')
    entry = envelope['instances']['chats']
    assert entry['pending']['recentNavigation']['status'] == 'pending'
    assert 'recentNavigation' not in entry['resources']
    assert entry['resources']['context']['value']['recentScope'] == {
        'mode': 'all', 'workspaceId': None, 'filter': '', 'age': 'all', 'clientId': 'recent-pending',
        'instanceId': 'chats', 'generation': entry['generation']}


async def test_workspace_chat_demand_keeps_current_identity_while_groups_are_pending(service, monkeypatch):
    from amplifier_web.shell_resources import scope_for
    from amplifier_web.shell_query_owner import PendingQuery
    service.clients.attach('workspace-pending')
    calls = []
    def pending(state, *, section=None):
        calls.append(section)
        assert section == 'recent', 'Shared chat demand must not construct the superseded workspace list'
        assert state['selectedWorkspaceId'] == 'two'
        assert state['view']['navFilter'] == 'beta'
        raise PendingQuery()
    monkeypatch.setattr(service.projections, 'chats', pending)
    with service.clients.bind('workspace-pending'):
        service.state['view'].update(workSurface='workspace', workWorkspaceId='two',
                                     workHomeScope='one', workHomeQuery='ignored',
                                     workWorkspaceChats={'workspaceId': 'two', 'query': 'beta', 'pages': {}})
        owner = service.shell.resources
        owner.register('workspace-pending', 1, ['workHome'], scope_for(service.state))
        envelope = owner.snapshot('workspace-pending')
    entry = envelope['instances']['chats']
    assert calls and all(section == 'recent' for section in calls)
    assert entry['pending']['workHome']['status'] == 'pending'
    assert 'workHome' not in entry['resources']
    context = entry['resources']['context']['value']['workHomeContext']
    assert context['scope'] == context['requestedScope'] == context['workspace']['id'] == 'two'


async def test_custom_full_snapshot_keeps_pin_and_context_when_bundled_workspace_changes(service):
    digest = await stage(service)
    record_validation(service, digest)
    client = service.shell.client('custom-workspace-context')
    custom = {'id': 'custom', 'package': digest, 'slot': 'navigation',
              'scope': {'mode': 'pinned', 'workspaceId': 'one'}}
    client['views']['custom'] = {'view': {'navChatScope': 'workspace'}, 'generation': 0}
    service.state['view'].update(workSurface='home', workHomeScope='all')
    before = service.shell.snapshot(client, custom, identity='custom-workspace-context')
    service.state['view'].update(workSurface='workspace', workWorkspaceId='two',
                                 workWorkspaceChats={'workspaceId': 'two', 'query': 'beta', 'pages': {}})
    after = service.shell.snapshot(client, custom, identity='custom-workspace-context',
                                   navigation_resources=frozenset({'workHome'}))
    assert after['selectedWorkspaceId'] == after['workHomePinnedScope'] == 'one'
    assert after['workHomeContext'] == before['workHomeContext']
    assert after['workHomeContext']['scope'] == after['workHomeContext']['workspace']['id'] == 'one'
    assert after['workHomeContext']['requestedScope'] == 'all'
    assert after['chatNavigation'] == before['chatNavigation']
    assert {row['id'] for row in after['chatNavigation']['items']} == {'alpha'}
    assert 'workWorkspaceChats' not in after['view'] and 'workHome' not in after
