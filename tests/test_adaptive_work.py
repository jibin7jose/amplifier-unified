from copy import deepcopy
import pytest
from amplifier_web.adaptive_work import work_home, work_home_context, work_home_inputs, work_status
from amplifier_web.chat_navigation import view_patch
from amplifier_web.shell_resource_diagnostics import rebuild_reasons
from test_shell_modules import service
from test_chat_navigation import chat


def test_home_query_rejects_invalid_inputs():
    for patch in ({'workHomeView': 'board'}, {'workHomeScope': None},
                  {'workHomeQuery': 'x'*501}, {'workHomePages': {'idle': True}},
                  {'workHomePages': {'all': 2}}, {'workHomeFilter': 'done'}):
        with pytest.raises(ValueError):
            view_patch(patch)
    view_patch({'workSurface': 'home', 'workHomeView': 'studio', 'workHomeScope': 'all',
                'workHomePages': {'working': 2}})
    for request in (None, {}, {'workspaceId': None, 'query': '', 'pages': {}},
                    {'workspaceId': 'one', 'query': 'x'*501, 'pages': {}},
                    {'workspaceId': 'one', 'query': '', 'pages': {'idle': True}},
                    {'workspaceId': 'one', 'query': '', 'pages': {}, 'savedMap': {}}):
        with pytest.raises(ValueError):
            view_patch({'workWorkspaceChats': request})
    view_patch({'workWorkspaceChats': {'workspaceId': 'two', 'query': 'beta', 'pages': {'idle': 1}}})


def test_sidebar_choices_validate_without_changing_home_contract():
    for patch in ({'navMode': 'inbox'}, {'navStatusPages': {'idle': True}},
                  {'navStatusPages': {'done': 1}}, {'navStatusCollapsed': 'idle'},
                  {'navStatusExpanded': ['unknown']}):
        with pytest.raises(ValueError):
            view_patch(patch)
    view_patch({'navMode': 'status', 'navStatusPages': {'idle': 2},
                'navStatusCollapsed': ['working'], 'navStatusExpanded': ['idle']})


def test_workspace_normalization_is_explicit_not_a_context_side_effect():
    state = {'view': {'workSurface': 'workspace', 'workWorkspaceId': 'two',
                       'navAge': '24h', 'workHomeScope': 'all',
                       'workWorkspaceChats': {'workspaceId': 'two', 'query': 'beta', 'pages': {}}},
             '_navigationListClock': 1_800_000_000,
             'workspaces': [{'id': key, 'name': key, 'path': '/'+key, 'available': True} for key in ('one', 'two')]}
    before = deepcopy(state)
    untouched, pin = work_home_inputs(state, pinned_workspace_id='one')
    assert untouched is state and pin == 'one'
    assert work_home_context(state, pinned_workspace_id='one')['workspace']['id'] == 'one'
    normalized, pin = work_home_inputs(state, pinned_workspace_id='one', workspace_chats=True)
    assert work_home_context(normalized, pinned_workspace_id=pin)['workspace']['id'] == 'two'
    assert normalized['view']['workHomeQuery'] == 'beta'
    assert normalized['view']['navAge'] == '24h'
    assert normalized['_navigationListClock'] == state['_navigationListClock']
    assert state == before


async def test_status_sidebar_is_independent_bounded_and_uses_same_catalog(service, monkeypatch):
    monkeypatch.setattr(service, '_session', lambda *a, **k: pytest.fail('Browsing hydrated a session'))
    initial = work_home(service, service.state)['groups']['idle']
    sidebar = work_status(service, service.state, 'idle')
    assert sidebar['items'] is initial['items'], 'An identical Home query reuses its cached page'
    changed = {**service.state, 'view': {'workHomeScope': 'two', 'workHomeQuery': 'no-match',
                                       'workHomePages': {'idle': 50}, 'navFilter': 'ignored'}}
    assert work_status(service, changed, 'idle')['items'] == initial['items']
    assert {row['id'] for row in work_status(service, changed, 'idle', pinned_workspace_id='two')['items']} == {'beta'}
    missing = work_status(service, changed, 'idle', pinned_workspace_id='missing')
    assert missing['unavailable'] and not missing['items']


async def test_shared_age_precedes_home_status_counts_and_pages(service, monkeypatch):
    from amplifier_web import chat_navigation
    now = 1_800_000_000
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: now)
    service.state['sessions'] = [chat('fresh', recent=now, workspace='one'),
        chat('old-pin', recent=1), chat('old', recent=1), chat('unknown', recent=0)]
    for row in service.state['sessions']:
        row.update(workspace=service.state['workspaces'][0]['path'], messages=[])
    service.state['pinnedSessionIds'] = ['old-pin']
    service.projections.invalidate()
    before = deepcopy(service.state)
    filtered = {**service.state, 'view': {'navAge': '24h', 'workHomePages': {'idle': 9},
                                        'navStatusPages': {'idle': 8}}}
    home = work_home(service, filtered)
    sidebar = work_status(service, filtered, 'idle')
    assert home['age'] == sidebar['scope']['age'] == '24h'
    assert home['groups']['idle']['total'] == sidebar['total'] == 2
    assert {row['id'] for row in home['items']} == {'fresh', 'old-pin'}
    assert home['groups']['idle']['index'] == sidebar['index'] == 0
    assert work_home(service, service.state)['groups']['idle']['total'] == 4
    assert service.state == before


async def test_nested_age_and_group_do_not_override_global_home_or_workspace_age(service):
    from amplifier_web.chat_navigation import frozen_list_clock
    client = service.shell.client('catalog-home-integration')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'}
    client['views']['chats'] = {'view': {'navRecentView': {'navAge': '24h', 'navGroup': 'status'}}, 'generation': 0}
    service.state['view'].update(workSurface='home', workHomeScope='all', navAge='all')
    before = deepcopy(client['views'])
    with frozen_list_clock(1_800_000_000):
        home = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
        assert {row['id'] for row in home['items']} == {'alpha', 'beta'}
        service.state['view']['workSurface'] = 'chats'
        all_chats = service.shell.navigation(client, instance, navigation_resources=frozenset({'sidebarSearch'}))
        search = all_chats['sidebarNavigation']['search']
        assert search['scope']['age'] == 'all' and search['scope']['group'] == 'status'
        assert {row['id'] for row in search['items']} == {'alpha', 'beta'}
        service.state['view'].update(workSurface='workspace', workWorkspaceId='two',
                                     workWorkspaceChats={'workspaceId': 'two', 'query': 'beta', 'pages': {}})
        workspace = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
        assert workspace['scope'] == 'two' and {row['id'] for row in workspace['items']} == {'beta'}
        service.state['view']['navAge'] = '24h'
        finite = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
        assert finite['age'] == '24h' and not finite['items']
        service.state['view']['navAge'] = 'all'
        assert all('group' not in row for row in workspace['items']), 'Home cards retain their status-grouped producer'
        service.state['view']['workSurface'] = 'home'
        assert service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome'] == home
    assert client['views'] == before, 'Browsing never rewrites All Chats grouping/age preferences'


async def test_status_sidebar_pages_do_not_mutate_the_catalog(service):
    base = service.state['sessions'][0]
    service.state['sessions'] = [{**deepcopy(base), 'id': f'status-{i}', 'recentActivityAt': i+1} for i in range(125)]
    before = deepcopy(service.state)
    first = work_status(service, service.state, 'idle')
    second = work_status(service, {**service.state, 'view': {'navStatusPages': {'idle': 1}}}, 'idle')
    assert first['total'] == second['total'] == 125 and len(first['items']) <= 50
    assert not {row['id'] for row in first['items']} & {row['id'] for row in second['items']}
    assert service.state == before


async def test_status_resource_identity_ignores_home_but_tracks_own_page_and_pin(service):
    shell = service.shell
    client = shell.client('status-reader')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'}
    def key():
        return shell.resources._page_key('workStatusIdle', shell.scoped_state(client, instance),
            instance, ((1, 2), 3), client, 7, (1, 2, 3, 4, 5, 6))
    initial = key()
    service.state['view'].update(workHomeScope='two', workHomeQuery='changed', workHomePages={'idle': 3},
                                 navStatusPages={'attention': 4})
    assert key() == initial
    service.state['view']['navStatusPages']['idle'] = 1
    assert key() != initial
    paged = key()
    instance['scope'] = {'mode': 'pinned', 'workspaceId': 'two'}
    assert key() != paged


async def test_scope_search_and_unavailable_project_never_widen(service, monkeypatch):
    before = deepcopy(service.state)
    monkeypatch.setattr(service, '_session', lambda *a, **k: pytest.fail('Browsing hydrated a session'))
    for scope, query, expected in [('all', '', {'alpha', 'beta'}), ('two', '', {'beta'}),
                                  ('two', 'alpha', set()), ('missing', '', set())]:
        result = work_home(service, {**service.state, 'view': {'workHomeScope': scope, 'workHomeQuery': query}})
        assert {row['id'] for row in result['items']} == expected
        assert result['unavailable'] == (scope == 'missing')
    assert service.state == before


async def test_independent_status_pages_are_bounded_and_idle_is_not_completion(service):
    base=service.state['sessions'][0]
    service.state['sessions']=[{**deepcopy(base), 'id': f'item-{i}', 'title': f'Work {i}', 'recentActivityAt': i+1} for i in range(125)]
    first=work_home(service, service.state)
    page=first['groups']['idle']
    assert page['total']==125 and len(page['items'])<=50 and page['pages']>1
    assert first['groups']['unread']['total']==0
    second=work_home(service, {**service.state, 'view': {'workHomePages': {'idle': 1}}})
    assert not {row['id'] for row in page['items']} & {row['id'] for row in second['groups']['idle']['items']}


async def test_home_resource_key_survives_diagnostics_and_query_change(service):
    shell=service.shell; client=shell.client('home-reader'); instance={'id':'chats','package':'builtin.chats','slot':'navigation'}
    state=shell.scoped_state(client,instance)
    one=shell.resources._page_key('workHome',state,instance,((1,2),3),client,7,(1,2,3,4,5,6))
    state['view']['workHomeQuery']='new query'
    two=shell.resources._page_key('workHome',state,instance,((1,2),3),client,7,(1,2,3,4,5,6))
    reasons,_=rebuild_reasons(('bootstrap',one),('bootstrap',two))
    assert reasons==('query',)


async def test_workspace_chats_uses_shared_groups_but_not_home_query_or_component_pin(service, monkeypatch):
    monkeypatch.setattr(service, '_session', lambda *a, **k: pytest.fail('Browsing hydrated a session'))
    client = service.shell.client('workspace-chats')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation',
                'scope': {'mode': 'pinned', 'workspaceId': 'one'}}
    service.state['view'].update(workSurface='home', workHomeScope='one', workHomeQuery='alpha',
                                 workHomePages={}, workWorkspaceChats={'workspaceId': 'two', 'query': 'beta', 'pages': {}})
    home = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
    assert {row['id'] for row in home['items']} == {'alpha'}
    service.state['view'].update(workSurface='workspace', workWorkspaceId='two')
    snapshot = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome', 'workStatusIdle'}))
    page = snapshot['workHome']
    assert page['scope'] == page['requestedScope'] == 'two'
    assert page['query'] == 'beta' and {row['id'] for row in page['items']} == {'beta'}
    assert snapshot['workHomeContext']['workspace']['id'] == 'two'
    assert set(page['groups']) == {'attention', 'unread', 'working', 'waiting', 'paused', 'idle'}
    assert 'workspace' not in snapshot['sidebarNavigation'], 'Superseded workspace list is not demanded'
    assert snapshot['workHomePinnedScope'] == snapshot['workStatusIdle']['workScope'] == 'one', 'Sidebar Status retains its own pin'
    for missing_id in ('missing', None):
        service.state['view']['workWorkspaceId'] = missing_id
        missing = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))
        assert missing['workHome']['unavailable'] and not missing['workHome']['items']
        assert missing['workHomeContext']['workspace'] is None
    service.state['view']['workSurface'] = 'home'
    assert service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome'] == home


async def test_workspace_chat_resource_key_tracks_only_effective_query_scope_and_pin(service):
    shell = service.shell
    client = shell.client('workspace-chats-key')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation',
                'scope': {'mode': 'pinned', 'workspaceId': 'two'}}
    service.state['view'].update(workSurface='home', workHomeScope='one')
    def key():
        return shell.resources._page_key('workHome', shell.scoped_state(client, instance),
            instance, ((1, 2), 3), client, 7, (1, 2, 3, 4, 5, 6))
    home = key()
    service.state['view'].update(workSurface='workspace', workWorkspaceId='one')
    workspace = key()
    assert workspace != home, 'Same requested scope with a different effective pin cannot reuse pages'
    service.state['view'].update(workHomeScope='managed', workHomeQuery='ignored', workHomePages={'idle': 20})
    assert key() == workspace
    for field, value in [('workWorkspaceChats', {'workspaceId': 'one', 'query': 'beta', 'pages': {}}),
                         ('workWorkspaceChats', {'workspaceId': 'one', 'query': 'beta', 'pages': {'idle': 1}}),
                         ('workWorkspaceId', 'two'), ('navAge', '24h')]:
        before = key()
        service.state['view'][field] = value
        assert key() != before


async def test_late_workspace_query_never_becomes_another_workspaces_query(service):
    client = service.shell.client('workspace-query-owner')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'}
    service.state['view'].update(workSurface='workspace', workWorkspaceId='one',
                                 workWorkspaceChats={'workspaceId': 'two', 'query': 'beta', 'pages': {'idle': 2}})
    page = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
    assert page['scope'] == 'one' and page['query'] == '' and page['requestedPages'] == {}
    assert {row['id'] for row in page['items']} == {'alpha'}
    service.state['view']['workWorkspaceChats'] = {'workspaceId': 'one', 'query': 'alpha', 'pages': {}}
    page = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
    assert page['query'] == 'alpha' and {row['id'] for row in page['items']} == {'alpha'}


async def test_pinned_home_request_identity_and_unavailable_scope_never_widen(service, monkeypatch):
    monkeypatch.setattr(service, '_session', lambda *a, **k: pytest.fail('Browsing hydrated a session'))
    client = service.shell.client('pinned-home')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation',
                'scope': {'mode': 'pinned', 'workspaceId': 'two'}}
    for requested in ('all', 'managed', 'one'):
        service.state['view'].update(workSurface='home', workHomeScope=requested)
        snapshot = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))
        page = snapshot['workHome']
        assert snapshot['workHomePinnedScope'] == page['scope'] == 'two'
        assert page['requestedScope'] == requested
        assert {row['id'] for row in page['items']} == {'beta'}
    keys = []
    for workspace_id, expected in [('one', {'alpha'}), ('two', {'beta'}), ('missing', set())]:
        instance['scope']['workspaceId'] = workspace_id
        page = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
        assert page['scope'] == workspace_id
        assert page['unavailable'] == (workspace_id == 'missing')
        assert {row['id'] for row in page['items']} == expected
        scoped = service.shell.scoped_state(client, instance)
        keys.append(service.shell.resources._page_key('workHome', scoped, instance, ((1, 2), 3), client, 7, (1, 2, 3, 4, 5, 6)))
    assert len(set(keys)) == 3, 'Pinned project changes cannot reuse another project page'
    instance['scope']['workspaceId'] = 'two'
    next(row for row in service.state['workspaces'] if row['id'] == 'two')['available'] = False
    page = service.shell.navigation(client, instance, navigation_resources=frozenset({'workHome'}))['workHome']
    assert page['scope'] == 'two' and page['unavailable'] and not page['items']


async def test_current_home_project_context_does_not_depend_on_result_pages(service, monkeypatch):
    monkeypatch.setattr(service.projections, 'chats', lambda *a, **k: pytest.fail('Context queried chat rows'))
    monkeypatch.setattr(service, '_session', lambda *a, **k: pytest.fail('Context hydrated a session'))
    client = service.shell.client('home-context')
    instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'}
    service.state['view'].update(workHomeScope='two', workHomeQuery='pending-search')
    def context():
        snapshot = service.shell.navigation(client, instance, navigation_resources=frozenset())
        assert 'workHome' not in snapshot
        return snapshot['workHomeContext']
    expected = context()
    assert expected['scope'] == expected['requestedScope'] == 'two'
    assert expected['workspace']['id'] == 'two'
    service.state['view'].update(workHomeQuery='another-search', workHomePages={'idle': 2})
    assert context() == expected
    row = next(row for row in service.state['workspaces'] if row['id'] == 'two')
    row['path'] = '/new/current/path'
    assert context()['workspace']['path'] == row['path']
    row['available'] = False
    assert context()['unavailable'] and context()['workspace'] is None
    instance['scope'] = {'mode': 'pinned', 'workspaceId': 'one'}
    assert context()['scope'] == 'one' and context()['requestedScope'] == 'two'
    instance['scope']['workspaceId'] = 'missing'
    assert context()['unavailable'] and context()['workspace'] is None


async def test_new_web_arrival_and_resume_preserve_chat_and_private_draft(service):
    service.state['view']['workHomeView']='studio'
    record=service.clients.attach('fresh-home',kind='web')
    assert record['view']['workSurface']=='home'
    assert 'workHomeView' not in record['view']
    with service.clients.bind('fresh-home'):
        await service.dispatch('view.update', {'patch': {'workSurface':'chat','workHomeView':'focus','draft':'Keep this draft'}})
    selected=record['selectedSessionId']
    resumed=service.clients.attach('resumed-home',resume='fresh-home',kind='web')
    assert resumed['view']['workSurface']=='chat'
    assert resumed['view']['workHomeView']=='focus'
    assert resumed['view']['draft']=='Keep this draft'
    assert resumed['selectedSessionId']==selected


async def test_home_choices_survive_navigation_and_client_resume_without_leaking(service):
    choices = {'workHomeView': 'studio', 'workHomeFilter': 'idle',
               'workHomeScope': 'two', 'workHomeQuery': 'beta',
               'workHomePages': {'idle': 2, 'unread': 1}, 'navPinned': True,
               'navMode': 'status', 'navStatusPages': {'idle': 1},
               'navStatusExpanded': ['idle'], 'navStatusCollapsed': ['working'],
               'workWorkspaceChats': {'workspaceId': 'one', 'query': 'alpha', 'pages': {'unread': 1}}}
    service.clients.attach('home-continuity', kind='web')
    with service.clients.bind('home-continuity'):
        await service.dispatch('view.update', {'patch': choices})
        for surface in ('chat', 'workspace', 'workspaces', 'chats', 'home'):
            await service.dispatch('view.update', {'patch': {'workSurface': surface}})
            assert all(service.state['view'][key] == value for key, value in choices.items())
    resumed = service.clients.attach('home-resumed', resume='home-continuity', kind='web')
    assert resumed['view']['workSurface'] == 'home'
    assert all(resumed['view'][key] == value for key, value in choices.items())
    independent = service.clients.attach('home-independent', kind='web')
    assert 'workHomeQuery' not in independent['view']
    assert 'workHomePages' not in independent['view']
    assert 'workWorkspaceChats' not in independent['view']
