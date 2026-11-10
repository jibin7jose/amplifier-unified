"""Bundled resource demand owns bounded immutable projections, not execution."""
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace

import pytest

from amplifier_web import shell_resources as resources
from amplifier_web.service import AppError
from amplifier_web.shell_navigation_demand import NAVIGATION_RESOURCES


class Shell:
    def __init__(self):
        self.service = SimpleNamespace(instance_id='host',
            clients=SimpleNamespace(current=ContextVar('resource_client', default='one'),
                                    records={'one': {'kind': 'web'}}),
            state={'revision': 1, 'selectedSessionId': 'chat', 'selectedWorkspaceId': 'w', 'view': {}},
            directory=SimpleNamespace(observation_revision={'navigation':1,'attention':1,'workspace':1,'root_navigation':1}), queue_clients={}, queue_sessions={})
        self.saved = {'composition': {'instances': [{'id':'nav','package':'builtin.chats','slot':'navigation'}],
                                      'presentation': {}}, 'preview': None, 'views': {}, 'baselineView': {}}
        self.recent_limits, self.calls = {}, []
        self.payload = 'bounded'

    def client(self, identity):
        return self.saved

    def scoped_state(self, client, instance):
        return {**self.service.state, 'view': {**client['baselineView'], **client['views'].get(instance['id'], {}).get('view', {})}}

    def recent_key(self, client, instance):
        return (self.service.clients.current.get(), instance['id'])

    def navigation(self, client, instance, *, identity, navigation_resources, context_visible_ids=(), _page_only=False):
        self.calls.append(frozenset(navigation_resources))
        result = {'view': self.scoped_state(client, instance)['view'], 'viewRevision': client['views'].get(instance['id'], {}).get('viewRevision', 0),
                  'selectedSessionId': self.service.state['selectedSessionId'],
                  'sidebarNavigation': {'recentView': {}},
                  'attention': {'sessions': {key: 1 for key in context_visible_ids}}, 'payload': self.payload}
        for name in navigation_resources:
            page = {'items': [{'id': name, 'title': self.payload}], 'scope': {}}
            if name == 'pinnedNavigation': page['items'] = [{'kind':'session','row':{'id':name}}]
            if name.startswith('sidebar'): result['sidebarNavigation'][name[7:].lower()] = page
            else: result[name] = page
            if name == 'recentNavigation': result.update(recentShortcuts=page['items'], recentScope={})
        return result

    def manifest(self, package):
        return {}

    def snapshot(self, client, instance, *, identity):
        self.calls.append('custom')
        return {'revision':self.service.state['revision'], 'custom':True}


@pytest.fixture
def owner():
    return resources.ShellResources(Shell())


def register(owner, wanted=('recentNavigation',), demand=1):
    owner.register('one', demand, wanted, resources.scope_for(owner.shell.service.state))


def test_exact_bound_client_monotonic_demand_and_scope(owner):
    register(owner)
    register(owner)
    for bad in (0, -1, True, 1.2, 2**53, '1'):
        with pytest.raises(AppError): register(owner, demand=bad)
    with pytest.raises(AppError): register(owner, ('sidebarSearch',))
    with pytest.raises(AppError): register(owner, ('unknown',), 2)
    with pytest.raises(AppError): owner.register('one',2,[],['wrong',None,'chat',None,None])
    owner.shell.service.clients.current.set(None)
    with pytest.raises(AppError): owner.snapshot('one')
    owner.shell.service.clients.current.set('one')
    register(owner, (), 2)
    with pytest.raises(AppError): register(owner, demand=1)
    assert owner.snapshot('one')['instances']['nav']['resources'].keys() == {'context'}


def test_quiet_progress_reuses_pages_and_context_without_catalog_or_payload_work(owner, monkeypatch):
    register(owner, ('recentNavigation','sidebarSearch'))
    first = owner.snapshot('one'); old = deepcopy(first)
    calls = list(owner.shell.calls)
    owner.shell.service.state.update(revision=2, execution={'text':'unrelated progress'})
    # Reusing the eventual result is insufficient: the old path still rebuilt
    # every page key and walked the envelope before discovering equality.
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError('Settled progress rebuilt resource pages')
        patch.setattr(owner, '_snapshot', forbidden)
        assert owner.snapshot('one') is first
    assert owner.shell.calls == calls
    assert first == old
    owner.shell.saved['views']['nav'] = {'view': {'navRecentView': {'navFilter':'new'}}}
    second = owner.snapshot('one')
    assert owner.shell.calls[len(calls):] == [frozenset({'sidebarSearch'}),frozenset()]
    assert first == old
    assert second['instances']['nav']['resources']['recentNavigation']['value'] is first['instances']['nav']['resources']['recentNavigation']['value']
    assert set(second['instances']['nav']['resources']['context']['value']['attention']['sessions']) == {'recentNavigation','sidebarSearch'}


def test_initial_view_preferences_are_part_of_settled_identity(owner):
    register(owner, ('sidebarSearch',))
    first = owner.snapshot('one')
    old = deepcopy(first)
    bootstrap = owner.bootstrap_key('one')
    owner.shell.saved['baselineView'].update(navRecentView={'navFilter': 'initial filter'},
        workspaceDraft={'mode': 'rename', 'id': 'w', 'name': 'unfinished'})
    changed = owner.snapshot('one')
    view = changed['instances']['nav']['resources']['context']['value']['view']
    assert view['navRecentView']['navFilter'] == 'initial filter'
    assert view['workspaceDraft']['name'] == 'unfinished'
    assert changed is not first and first == old
    assert owner.bootstrap_key('one') == bootstrap


def test_only_demanded_grouped_pages_expire_the_settled_envelope(owner, monkeypatch):
    from amplifier_web import chat_navigation
    now = [1_800_000_000]
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: now[0])
    register(owner, ('sidebarSearch', 'recentNavigation'))
    first = owner.snapshot('one')
    calls = len(owner.shell.calls)
    assert owner.snapshot('one') is first
    now[0] += 60
    owner.snapshot('one')
    assert owner.shell.calls[calls:] == [frozenset({'sidebarSearch'})]
    register(owner, ('recentNavigation',), 2)
    hidden = owner.snapshot('one')
    calls = len(owner.shell.calls)
    now[0] += 60
    assert owner.snapshot('one') is hidden
    assert len(owner.shell.calls) == calls


@pytest.mark.parametrize('browser', [False, True])
def test_completed_envelope_rechecks_capacity_and_new_demand(owner, monkeypatch, browser):
    def snapshot():
        return owner.browser_projection('one')['shellResources'] if browser else owner.snapshot('one')

    register(owner)
    first = snapshot()
    monkeypatch.setattr(resources, 'MAX_TOTAL_BYTES', owner.total_bytes - 1)
    assert snapshot()['error']['code'] == 'shell_resource_limit'
    monkeypatch.setattr(resources, 'MAX_TOTAL_BYTES', owner.total_bytes + 100000)
    assert 'error' not in snapshot()
    register(owner, demand=2)
    assert snapshot()['demandId'] == 2
    assert first['demandId'] == 1


@pytest.mark.parametrize('recovery', [False, True])
def test_browser_projection_preserves_preview_recovery_and_scope(owner, recovery):
    owner.shell.saved['preview'] = {'id': 'preview', 'composition': {
        'instances': [{'id': 'preview-nav', 'package': 'builtin.chats', 'slot': 'navigation'},
                      {'id': 'custom', 'package': 'digest', 'slot': 'app.status'}],
        'presentation': {}}}
    unregistered = owner.browser_projection('one')
    assert unregistered == {'shellBootstrapKey': owner.bootstrap_key('one')}
    assert owner.shell.calls == []

    owner.register('one', 1, [], resources.scope_for(owner.shell.service.state), recovery=recovery)
    first = owner.browser_projection('one')
    envelope = first['shellResources']
    assert first['shellBootstrapKey'] == envelope['bootstrapKey'] == owner.bootstrap_key('one')
    assert set(envelope['instances']) == ({'workspaces', 'chats'} if recovery else {'preview-nav', 'custom'})
    if not recovery:
        assert envelope['instances']['custom']['resources']['customSnapshot']['value']['custom']

    calls = list(owner.shell.calls)
    owner.shell.service.state['selectedSessionId'] = 'other'
    assert owner.browser_projection('one') == {'shellBootstrapKey': first['shellBootstrapKey']}
    assert owner.shell.calls == calls
    owner.register('one', 2, [], resources.scope_for(owner.shell.service.state), recovery=recovery)
    after = owner.browser_projection('one')['shellResources']
    assert after['scope'][0] == 'other' and envelope['scope'][0] == 'chat'
    assert after['demandId'] == 2


@pytest.mark.parametrize('recovery', [False, True])
def test_browser_projection_prunes_idle_resources_but_keeps_active_registration(owner, monkeypatch, recovery):
    clock = [0.0]
    monkeypatch.setattr(resources, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    owner.register('one', 1, [], resources.scope_for(owner.shell.service.state), recovery=recovery)
    first = owner.browser_projection('one')
    owner.shell.service._client_snapshots = {'one': first}
    owner.shell.service._client_snapshot_preferences = {'one': 'preferences'}
    owner.shell.service.queue_clients = {'queue': 'one'}
    owner.shell.service.queue_sessions = {'queue': None}
    clock[0] = 100
    assert owner.browser_projection('one')['shellResources'] is first['shellResources']
    owner.shell.service.queue_clients = {}
    assert 'shellResources' in owner.browser_projection('one')
    calls = list(owner.shell.calls)
    clock[0] = 160
    retired = owner.browser_projection('one')
    assert retired == {'shellBootstrapKey': first['shellBootstrapKey']}
    assert owner.shell.calls == calls and owner.total_bytes == 0 and 'one' not in owner.clients
    assert owner.shell.service._client_snapshots == {}
    assert owner.shell.service._client_snapshot_preferences == {}
    # A subsequent unregistered frame uses the saved composition again.
    assert owner.browser_projection('one') == {'shellBootstrapKey': owner.bootstrap_key('one')}


def test_browser_projection_requires_bound_web_client_without_registration(owner):
    owner.shell.service.clients.current.set(None)
    with pytest.raises(AppError):
        owner.browser_projection('one')
    owner.shell.service.clients.current.set('one')
    owner.shell.service.clients.records['one']['kind'] = 'tui'
    with pytest.raises(AppError):
        owner.browser_projection('one')


def test_scope_mismatch_does_no_projection_until_exact_new_demand(owner):
    register(owner);before=owner.snapshot('one');calls=len(owner.shell.calls)
    owner.shell.service.state['selectedSessionId']='other'
    assert owner.snapshot('one') is None
    assert len(owner.shell.calls)==calls
    register(owner,demand=2)
    after=owner.snapshot('one')
    assert after['scope'][0]=='other' and before['scope'][0]=='chat'
    assert after['demandId']==2


def test_semantic_catalog_global_and_owner_changes_invalidate_without_bootstrap_churn(owner):
    register(owner, ('recentNavigation','sidebarPinned'))
    first=owner.snapshot('one');bootstrap=owner.bootstrap_key('one');calls=len(owner.shell.calls)
    owner.shell.saved['views']['nav']={'view':{'navPinnedPage':2},'viewRevision':1}
    owner.snapshot('one')
    assert owner.bootstrap_key('one')==bootstrap
    assert any('sidebarPinned' in call for call in owner.shell.calls[calls:])
    calls=len(owner.shell.calls);owner.invalidate({'locationListing'});owner.snapshot('one')
    assert owner.shell.calls[calls:]==[frozenset()]
    calls=len(owner.shell.calls);owner.shell.service.directory.observation_revision.update(navigation=2,root_navigation=2);owner.snapshot('one')
    assert owner.shell.calls[calls:]==[frozenset({'recentNavigation'}),frozenset({'sidebarPinned'}),frozenset()]
    owner.shell.saved['views']['nav']['generation']=1
    assert owner.bootstrap_key('one')!=bootstrap
    assert first['instances']['nav']['generation']==0


def test_custom_snapshot_is_preserved_builtin_non_navigation_is_not_projected(owner):
    owner.shell.saved['composition']['instances'].append({'id':'custom','slot':'navigation','package':'digest'})
    register(owner,())
    result=owner.snapshot('one')
    assert set(result['instances'])=={'nav','custom'}
    assert result['instances']['custom']['resources']['customSnapshot']['value']['custom']
    assert owner.shell.calls==[frozenset(),'custom']
    owner.shell.saved['views']['custom'] = {'viewRevision': 7, 'view': {'tab': 'changed'}}
    assert owner.snapshot('one')['instances']['custom']['resources']['customSnapshot']['value']['viewRevision'] == 7


def test_size_failure_is_bounded_memoized_and_does_not_destroy_prior_snapshot(owner,monkeypatch):
    register(owner);prior=owner.snapshot('one');prior_bytes=owner.total_bytes
    monkeypatch.setattr(resources,'MAX_CLIENT_BYTES',1000)
    owner.shell.payload='x'*2000;owner.shell.service.directory.observation_revision['navigation']=2
    error=owner.snapshot('one');calls=len(owner.shell.calls)
    assert error['error']['code']=='shell_resource_limit' and error['instances']=={}
    assert owner.total_bytes==prior_bytes
    owner.shell.service.state['revision']+=1
    owner.shell.recent_limits[('another-client', 'other-instance')] = 100
    assert owner.snapshot('one') is error and len(owner.shell.calls)==calls
    assert prior['instances']['nav']['resources']['context']['value']['payload']=='bounded'
    owner.shell.payload='small';owner.shell.service.directory.observation_revision['navigation']=3
    assert 'error' not in owner.snapshot('one')


def test_inactive_client_grace_reclaims_capacity_but_active_stream_never_evicted(owner,monkeypatch):
    clock=[0.0];monkeypatch.setattr(resources.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(resources,'MAX_CLIENTS',1)
    register(owner);baseline=owner.snapshot('one')
    owner.shell.service._client_snapshots = {'one': {'shellResources': baseline}}
    owner.shell.service._client_snapshot_preferences = {'one': 'preferences'}
    owner.shell.service.queue_clients={'queue':'one'};owner.shell.service.queue_sessions={'queue':None}
    clock[0]=100;assert owner.registered('one')
    assert owner.shell.service._client_snapshots['one']['shellResources'] is baseline
    owner.shell.service.clients.records['two']={'kind':'web'};owner.shell.service.clients.current.set('two')
    with pytest.raises(AppError): owner.register('two',1,[],resources.scope_for(owner.shell.service.state))
    owner.shell.service.queue_clients={};clock[0]=200;assert owner.registered('one')
    clock[0]=259;assert owner.registered('one')
    clock[0]=260;assert not owner.registered('one') and owner.total_bytes==0
    assert owner.shell.service._client_snapshots == {}
    assert owner.shell.service._client_snapshot_preferences == {}
    owner.register('two',1,[],resources.scope_for(owner.shell.service.state))


@pytest.mark.parametrize('resource',sorted(NAVIGATION_RESOURCES))
def test_resource_field_split_preserves_partial_snapshot_contract(owner,resource):
    register(owner,(resource,));result=owner.snapshot('one')['instances']['nav']['resources']
    assert set(result)=={resource,'context'}
    if resource.startswith('sidebar'):
        assert set(result[resource]['value']['sidebarNavigation'])=={resource[7:].lower()}
    else:
        assert resource in result[resource]['value']
    assert result['context']['value']['sidebarNavigation']=={'recentView':{}}


from test_shell_modules import service


async def test_browser_frames_prepare_shell_once_and_observe_saved_client_changes(service, monkeypatch):
    from amplifier_web import shell_components

    identity = 'resource-preparation'
    service.clients.attach(identity)
    with service.clients.bind(identity):
        owner = service.shell.resources
        owner.register(identity, 1, [], resources.scope_for(service.state))
        read_client = service.shell.client
        calls = {}
        for target, attribute, label in ((service.shell, 'client', 'read'),
                (shell_components, 'resolved', 'resolve'), (owner, '_bootstrap', 'bootstrap'),
                (owner, '_prune', 'prune')):
            original = getattr(target, attribute)

            def observe(*args, _original=original, _label=label, **kwargs):
                calls[_label] = calls.get(_label, 0) + 1
                return _original(*args, **kwargs)

            monkeypatch.setattr(target, attribute, observe)

        first = service.browser_state()
        old = deepcopy(first['shellResources'])
        assert calls == {'read': 1, 'resolve': 1, 'bootstrap': 1, 'prune': 1}
        assert first['shellBootstrapKey'] == first['shellResources']['bootstrapKey']
        calls.clear()
        service.state['revision'] += 1
        quiet = service.browser_state()
        assert calls == {'read': 1, 'resolve': 1, 'bootstrap': 1, 'prune': 1}
        assert quiet['shellResources'] is first['shellResources']

        client = read_client(identity)
        client['views']['chats'] = {'generation': 1, 'viewRevision': 1, 'view': {'navFilter': 'changed'}}
        service.shell.put('client', identity, client)
        service.state['revision'] += 1
        calls.clear()
        changed = service.browser_state()
        assert calls == {'read': 1, 'resolve': 1, 'bootstrap': 1, 'prune': 1}
        assert changed['shellBootstrapKey'] != first['shellBootstrapKey']
        assert changed['shellBootstrapKey'] == changed['shellResources']['bootstrapKey']
        chats = changed['shellResources']['instances']['chats']
        assert chats['generation'] == 1
        assert chats['resources']['context']['value']['view']['navFilter'] == 'changed'
        assert first['shellResources'] == old


@pytest.mark.parametrize('wanted', [frozenset(sorted(NAVIGATION_RESOURCES)[:16]),
                                    frozenset(sorted(NAVIGATION_RESOURCES)[16:])])
async def test_real_navigation_resources_reassemble_exact_existing_snapshot(service, wanted):
    service.clients.attach('resource-real')
    with service.clients.bind('resource-real'):
        owner = resources.ShellResources(service.shell)
        # Exercise every resource without exceeding the public demand bound.
        owner.register('resource-real',1,sorted(wanted),resources.scope_for(service.state))
        envelope = owner.snapshot('resource-real')
        client = service.shell.client('resource-real')
        for instance in client['composition']['instances']:
            expected = service.shell.navigation(client,instance,identity='resource-real',
                navigation_resources=wanted)
            actual = {}
            for item in envelope['instances'][instance['id']]['resources'].values():
                for key,value in item['value'].items():
                    if key=='navigationAge':
                        assert value=='all'
                        continue
                    if key=='sidebarNavigation': actual.setdefault(key,{}).update(value)
                    else: actual[key]=value
            expected.pop('navigationResources')
            assert actual==expected
            assert 'draft' not in actual['view']


async def test_attention_only_saved_changes_refresh_visible_unselected_notices(service):
    service._save_changes(sessions={'alpha', 'beta'})
    service.clients.attach('resource-attention')
    with service.clients.bind('resource-attention'):
        owner = resources.ShellResources(service.shell)
        service.shell.client('resource-attention')['baselineView']['navChatScope'] = 'all'
        owner.register('resource-attention', 1, ['recentNavigation'], resources.scope_for(service.state))
        first = owner.snapshot('resource-attention')
        old = deepcopy(first)
        before = service.directory.observation_revision
        row = service._session('beta', hydrate=False)
        assert service.state.get('selectedSessionId') != 'beta'
        row['manualUnread'] = 'manual-one'
        service._save_changes(sessions={'beta'})
        after = service.directory.observation_revision
        assert after['navigation'] == before['navigation']
        assert after['attention'] != before['attention']
        second = owner.snapshot('resource-attention')
        assert second != first and first == old
        context = second['instances']['chats']['resources']['context']['value']
        assert context['attention']['sessions']['beta'] > 0
        row.update(error='A provider error', errorAt=1)
        service._save_changes(sessions={'beta'})
        errored = owner.snapshot('resource-attention')
        nav = service.directory.observation_revision['navigation']
        row['errorAt'] = 2
        service._save_changes(sessions={'beta'})
        assert service.directory.observation_revision['navigation'] == nav
        refreshed = owner.snapshot('resource-attention')
        assert refreshed != errored
        assert refreshed['instances']['chats']['resources']['context']['revision'] > errored['instances']['chats']['resources']['context']['revision']


async def test_settled_client_filters_and_unfinished_edits_remain_independent(service):
    from test_shell_modules import command, settled_resources
    owner = service.shell.resources
    for identity in ('filter-one', 'filter-two'):
        service.clients.attach(identity)
        with service.clients.bind(identity):
            owner.register(identity, 1, [], resources.scope_for(service.state))
    first = await settled_resources(service, 'filter-one')
    peer = await settled_resources(service, 'filter-two')
    frozen = deepcopy(peer)
    await command(service, 'shell.view.update', clientId='filter-one', instanceId='chats',
        patch={'navFilter': 'alpha', 'workspaceDraft': {'mode': 'rename', 'id': 'one', 'name': 'My unfinished name'}},
        dirty=True)
    changed = await settled_resources(service, 'filter-one')
    context = changed['instances']['chats']['resources']['context']['value']
    assert context['view']['navFilter'] == 'alpha'
    assert context['view']['workspaceDraft']['name'] == 'My unfinished name'
    assert context['viewRevision'] > first['instances']['chats']['resources']['context']['value']['viewRevision']
    assert await settled_resources(service, 'filter-two') is peer
    assert peer == frozen
    service.clients.records['filter-one']['view']['draft'] = 'composer is unrelated'
    assert await settled_resources(service, 'filter-one') is changed


async def test_settled_scope_tracks_selected_resident_admission_without_semantic_change(service):
    selected = service.state['sessions'][0]
    selected['location'] = {'kind': 'managed'}
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.clients.attach('resident-scope').update(selectedSessionId='alpha', selectedWorkspaceId='one')
    with service.clients.bind('resident-scope'):
        owner = service.shell.resources
        owner.register('resident-scope', 1, [], resources.scope_for(service.state))
        first = owner.snapshot('resident-scope')
        assert first['instances']['chats']['resources']['context']['value']['view']['navChatScope'] == 'all'
        before = service.directory.observation_revision
        # Like resident retirement, leave the exact durable owner available in
        # the directory. Selection and canonical navigation facts stay equal.
        service._state['sessions'] = [row for row in service._state['sessions'] if row['id'] != 'alpha']
        service.projections.invalidate(state=service._state)
        assert service.directory.observation_revision == before
        absent = owner.snapshot('resident-scope')
        assert absent['instances']['chats']['resources']['context']['value']['view'].get('navChatScope', 'workspace') == 'workspace'
        service._state['sessions'].append(selected)
        service.projections.invalidate(state=service._state)
        assert service.directory.observation_revision == before
        admitted = owner.snapshot('resident-scope')
        assert admitted['instances']['chats']['resources']['context']['value']['view']['navChatScope'] == 'all'
        assert first['instances']['chats']['resources']['context']['value']['view']['navChatScope'] == 'all'


async def test_settled_context_observes_committed_workspace_and_shared_history_changes(service):
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.clients.attach('workspace-epoch').update(selectedSessionId='alpha', selectedWorkspaceId='one')
    with service.clients.bind('workspace-epoch'):
        owner = service.shell.resources
        owner.register('workspace-epoch', 1, [], resources.scope_for(service.state))
        first = owner.snapshot('workspace-epoch')
        old = deepcopy(first)
        workspace = next(row for row in service.state['workspaces'] if row['id'] == 'one')
        workspace.update(name='Renamed workspace', path='/changed/canonical/path')
        service.state['sharedHistory'].update(refreshing=True, issueCount=2)
        service._save_changes(globals={'workspaces', 'sharedHistory'})
        changed = owner.snapshot('workspace-epoch')
        context = changed['instances']['chats']['resources']['context']['value']
        assert context['workspaces'][0]['name'] == 'Renamed workspace'
        assert context['workspaces'][0]['path'] == '/changed/canonical/path'
        assert context['sharedHistory']['refreshing'] is True
        assert context['sharedHistory']['issueCount'] == 2
        assert first == old and changed is not first


async def test_archive_restore_refreshes_settled_workspace_counts_and_preserves_chat(service):
    from test_shell_modules import settled_resources
    service.state['view']['navWorkspaceMode'] = 'recent'
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.clients.attach('archive-count')
    with service.clients.bind('archive-count'):
        service.shell.client('archive-count')['baselineView']['navWorkspaceMode'] = 'recent'
        service.shell.resources.register('archive-count', 1,
            ['workspaceExplorer', 'workspaceShortcuts'], resources.scope_for(service.state))
    first = await settled_resources(service, 'archive-count')
    preserved = deepcopy(first)
    def counts(envelope):
        values = envelope['instances']['workspaces']['resources']
        explorer = values['workspaceExplorer']['value']['workspaceExplorer']['rows']
        shortcuts = values['workspaceShortcuts']['value']['workspaceShortcuts']
        return [next(row['chatCount'] for row in rows if row['workspaceId'] == 'one')
                for rows in (explorer, shortcuts)]
    assert counts(first) == [1, 1]
    for action, expected in [('session.archive', 0), ('session.restore', 1)]:
        await service.dispatch(action, {'id': 'alpha'})
        current = await settled_resources(service, 'archive-count')
        assert counts(current) == [expected, expected]
        assert service._session('alpha')['title'] == 'alpha'
        assert first == preserved


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
async def test_unavailable_custom_package_does_not_break_builtin_resources(service, damage):
    from test_shell_modules import stage, record_validation, prepare, command
    digest = await stage(service)
    record_validation(service, digest)
    service.clients.attach('resource-custom')
    composition = deepcopy(service.shell.client('resource-custom')['composition'])
    composition['instances'].append({'id':'optional','package':digest,'slot':'navigation'})
    change = await prepare(service, composition, client='resource-custom')
    await command(service, 'shell.changes.apply', clientId='resource-custom', changeId=change, expectedRevision=0)
    with service.clients.bind('resource-custom'):
        owner = service.shell.resources
        owner.register('resource-custom',1,[],resources.scope_for(service.state))
        first = owner.snapshot('resource-custom')
        assert 'customSnapshot' in first['instances']['optional']['resources']
        path = service.shell.directory / (digest + '.mjs')
        if damage == 'missing': path.unlink()
        else: path.write_text('corrupted source')
        service.state['revision'] += 1
        after = owner.snapshot('resource-custom')
        assert after['instances']['optional']['resources'] == {}
        assert 'context' in after['instances']['chats']['resources']
        bootstrap = service.shell.inspect('resource-custom', bundled=True)
        assert 'error' in bootstrap['packages'][digest]
        assert bootstrap['shellResources']['instances']['chats']['resources']['context']


def test_global_budget_never_discards_active_owner_and_recovers_after_idle_retirement(owner, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(resources.time, 'monotonic', lambda: clock[0])
    register(owner)
    first = owner.snapshot('one')
    used = owner.total_bytes
    monkeypatch.setattr(resources, 'MAX_TOTAL_BYTES', used + 1)
    owner.shell.service.clients.records['two'] = {'kind': 'web'}
    owner.shell.service.clients.current.set('two')
    owner.register('two', 1, ['recentNavigation'], resources.scope_for(owner.shell.service.state))
    limited = owner.snapshot('two')
    assert limited['error']['code'] == 'shell_resource_limit'
    assert owner.total_bytes == used and owner.clients['one']['envelope'] is first
    owner.shell.service.queue_clients = {'queue': 'two'}
    owner.shell.service.queue_sessions = {'queue': None}
    clock[0] = 61
    recovered = owner.snapshot('two')
    assert 'error' not in recovered and not owner.registered('one')
    assert 0 < owner.total_bytes <= resources.MAX_TOTAL_BYTES


async def test_retired_browser_baseline_cannot_reconnect_as_registered(service, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(resources, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    service.clients.attach('resource-retired')
    with service.clients.bind('resource-retired'):
        owner = service.shell.resources
        scope = resources.scope_for(service.state)
        owner.register('resource-retired', 1, [], scope)
        old = service.browser_state()
        assert 'shellResources' in old
        clock[0] = 61
        assert not owner.registered('resource-retired')
        assert 'resource-retired' not in service._client_snapshots
        fresh = service.browser_state()
        assert fresh is not old and 'shellResources' not in fresh
        assert 'resource-retired' in service.clients.records
        owner.register('resource-retired', 2, [], scope)
        assert owner.snapshot('resource-retired')['demandId'] == 2


def test_bootstrap_tracks_descriptor_revision_and_preview_identity_only(owner):
    client = owner.shell.saved
    client['revision'] = 0
    baseline = owner.bootstrap_key('one')
    client['preview'] = {'id': 'preview-one', 'composition': deepcopy(client['composition'])}
    preview = owner.bootstrap_key('one')
    assert preview != baseline
    client['preview']['id'] = 'preview-two'
    assert owner.bootstrap_key('one') != preview
    # Applying an identical effective composition still replaces the preview
    # descriptor and advances the CAS revision used by subsequent commands.
    client['preview'] = None
    client['revision'] = 1
    applied = owner.bootstrap_key('one')
    assert applied not in {baseline, preview}
    client['reported'] = {'instances': {'nav': 'ready'}}
    client['views']['nav'] = {'generation': 0, 'viewRevision': 99, 'view': {'navFilter': 'private filter'}}
    owner.shell.service.state.update(revision=999, execution={'text': 'progress'})
    assert owner.bootstrap_key('one') == applied
