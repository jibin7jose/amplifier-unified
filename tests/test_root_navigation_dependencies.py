"""Root navigation responds only to its actual canonical dependencies."""
from copy import deepcopy

import pytest

from amplifier_web.native_catalog import NativeCatalog
from amplifier_web.shell_resources import scope_for
from test_shell_modules import service, settled_resources


@pytest.mark.parametrize('resource,section', [('sidebarSearch', 'search'), ('sidebarWorkspace', 'workspace'),
    ('workHome', None), ('workStatusIdle', None), ('recentNavigation', None), ('workspaceShortcuts', None)])
@pytest.mark.parametrize('age,seconds', [('24h', 86400), ('14d', 1209600)])
async def test_grouped_cutoff_expires_through_settled_browser_delivery(service, monkeypatch, resource, section, age, seconds):
    from amplifier_web import chat_navigation
    now = [1_800_000_000]
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: now[0])
    for row in service.state['sessions']:
        row.update(recentActivityAt=now[0]-seconds, navigationActivityAt=now[0]-seconds)
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    client = service.clients.attach('age-client')
    client.update(selectedSessionId='alpha', selectedWorkspaceId='one')
    client['view']['navAge'] = age
    with service.clients.bind('age-client'):
        owner = service.shell.resources
        owner.register('age-client', 1, [resource], scope_for(service.state))
        await settled_resources(service, 'age-client')
        first = service.browser_state()
        def page(frame):
            value = frame['shellResources']['instances']['chats']['resources'][resource]['value']
            if section:
                return value['sidebarNavigation'][section]
            if resource == 'workHome':
                return value['workHome']['groups']['idle']
            if resource == 'workspaceShortcuts':
                assert value['navigationAge'] == age
                rows = value['workspaceShortcuts']
                return {'total': sum(row['chatCount'] for row in rows), 'items': [row for row in rows if row['chatCount']]}
            return value[resource]
        assert page(first)['total'] > 0
        assert service.browser_state() is first
        revision = service.state['revision']
        now[0] += 60
        # The browser snapshot and settled resource fast paths must both see time.
        changed = service.browser_state()
        assert changed is not first
        await settled_resources(service, 'age-client')
        service._client_snapshots.pop('age-client', None)
        expired = service.browser_state()
        assert page(expired)['total'] == 0
        assert page(expired)['items'] == []
        assert service.state['revision'] == revision
        assert service.browser_state() is expired


@pytest.mark.parametrize('kind', ['worker', 'internal'])
async def test_nonroot_native_title_does_not_build_root_pages(service, tmp_path, kind):
    workspace = deepcopy(service.state['workspaces'][0])
    root = {'id': 'native-root', 'nativeIdentity': 'native-root', 'nativeProject': 'proof',
            'sessionKind': 'root', 'workspaceId': workspace['id'], 'workspace': workspace['path'],
            'title': 'Saved root', 'createdAt': 1, 'recentActivityAt': 1}
    child = {**root, 'id': 'native-child', 'nativeIdentity': 'native-child',
             'parentId': 'native-root', 'sessionKind': kind, 'title': 'Saved child'}
    initial = {'workspace': workspace, 'sessions': [root, child]}
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home')
    catalog.save({'proof': initial}, {}, {}, ())
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.directory.bind(catalog)
    client = service.clients.attach('proof-client')
    client.update(selectedSessionId='alpha', selectedWorkspaceId=workspace['id'])
    with service.clients.bind('proof-client'):
        owner = service.shell.resources
        owner.register('proof-client', 1, ['sidebarRecent', 'pinnedNavigation', 'workspaceShortcuts'], scope_for(service.state))
        first = await settled_resources(service, 'proof-client')
        before = service.performance_diagnostics.snapshot()['shellResourceRebuilds']['total']
        updated = deepcopy(initial)
        updated['sessions'][1]['title'] = 'Changed worker title'
        catalog.save({'proof': updated}, {'proof': initial}, {}, ())
        service._publish_changes(globals={'sharedHistory'})
        following = owner.snapshot('proof-client')
        assert service.performance_diagnostics.snapshot()['shellResourceRebuilds']['total'] == before
        for name, instance in first['instances'].items():
            for resource in ('sidebarRecent', 'pinnedNavigation', 'workspaceShortcuts'):
                assert following['instances'][name]['resources'][resource] == instance['resources'][resource]


@pytest.mark.parametrize('change', ['title', 'working', 'notice', 'read'])
async def test_nonroot_live_changes_reuse_root_page(service, change):
    from amplifier_web.history_directory import notices
    row = service.state['sessions'][0]
    row.update(sessionKind='worker', manualUnread='old-marker')
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.state['view']['navChatScope'] = 'all'
    first = service.projections.chats(service.state, section='shortcuts')
    if change == 'title': row['title'] = 'Worker renamed'
    elif change == 'working': row['status'] = 'working'
    elif change == 'notice': row['manualUnread'] = 'new-marker'
    else:
        notice = notices(row, service.state)[0]
        service.state.setdefault('attentionRead', {})[notice[2]] = notice[3]
    service._publish_changes(sessions={'alpha'}, globals={'attentionRead'})
    assert service.projections.chats(service.state, section='shortcuts') is first


@pytest.mark.parametrize('change', ['title', 'description', 'recent', 'working', 'notice', 'read',
                                    'workspace', 'role', 'archive', 'pins', 'registry', 'remove'])
async def test_root_changes_rebuild_and_equal_fresh_query(service, change):
    from amplifier_web.history_directory import notices
    row = service.state['sessions'][0]
    row['manualUnread'] = 'read-this'
    service.state['view'].update(navChatScope='all', navShowAgentCreated=True)
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    first = service.projections.chats(service.state, section='shortcuts')
    frozen = deepcopy(first)
    if change == 'title': row['title'] = 'Changed root'
    elif change == 'description': row['description'] = 'Findable new words'
    elif change == 'recent': row['recentActivityAt'] = 100
    elif change == 'working': row['status'] = 'working'
    elif change == 'notice': row['manualUnread'] = 'new-notice'
    elif change == 'read':
        notice = notices(row, service.state)[0]
        service.state.setdefault('attentionRead', {})[notice[2]] = notice[3]
    elif change == 'workspace': row.update(workspaceId='two', workspace=service.state['workspaces'][1]['path'])
    elif change == 'role': row['sessionKind'] = 'worker'
    elif change == 'archive': service.state.setdefault('conversationOrganization', {})['archived'] = {'alpha': True}
    elif change == 'pins': service.state['pinnedSessionIds'] = ['alpha']
    elif change == 'registry': service.state['workspaces'][0]['name'] = 'Changed workspace'
    else:
        service.state['sessions'].remove(row)
        service.forget_session_records({'alpha'})
    service._publish_changes(sessions={'alpha'}, globals={'attentionRead', 'conversationOrganization', 'pinnedSessionIds', 'workspaces'})
    following = service.projections.chats(service.state, section='shortcuts')
    assert following is not first
    assert following == service.directory.chats(service.state, section='shortcuts')
    assert first == frozen


async def test_root_overlay_eviction_and_lazy_upgrade(service):
    from amplifier_web.history_directory_observations import initialize
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.db.execute('ALTER TABLE history_observation_rows DROP COLUMN root_navigation')
    service.db.execute("DELETE FROM history_observation_revisions WHERE collection='root_navigation'")
    service.db.commit()
    initialize(service.db)
    service.db.commit()
    row = service.state['sessions'][0]
    before = service.directory.root_navigation_revision
    row['title'] = 'Unsaved root label'
    assert service.directory.root_navigation_revision != before
    row['title'] = 'alpha'
    # Restore the exact stored label, then eviction is not a membership change.
    stored = service.db.execute("SELECT json_extract(summary,'$.title') FROM history_local_rows WHERE id='alpha'").fetchone()[0]
    row['title'] = stored
    assert service.directory.root_navigation_revision == before
    service.state['sessions'].remove(row)
    assert service.directory.root_navigation_revision == before
    assert service.db.execute("SELECT root_navigation FROM history_observation_rows WHERE id='beta'").fetchone()[0] is None


async def test_native_root_revision_alias_and_suppression_controls(service, tmp_path):
    from amplifier_web.native_catalog import CatalogRows, CatalogPatchRows
    from test_native_directory import seed
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    project, workspace = seed(catalog, tmp_path / 'workspace', 3)
    service.state['workspaces'].append(workspace)
    service.state['view']['navChatScope'] = 'all'
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.directory.bind(catalog)
    base = CatalogRows(catalog, project)
    prior = {'workspace': workspace, 'sessions': base}
    first = service.projections.chats(service.state, section='shortcuts')
    changed = {**prior, 'sessions': CatalogPatchRows(base, [{**base.get('saved-00000'), 'title': 'Renamed native root'}])}
    catalog.save({project: changed}, {project: prior}, {}, ())
    service.projections.invalidate(state=service.state, session_ids=set())
    renamed = service.projections.chats(service.state, section='shortcuts')
    assert renamed != first
    assert renamed == service.directory.chats(service.state, section='shortcuts')
    before = service.directory.root_navigation_revision
    duplicate = {**base.get('saved-00000'), 'nativeProject': 'duplicate', 'sessionKind': 'worker', 'parentId': 'p'}
    catalog.save({'duplicate': {'workspace': workspace, 'sessions': [duplicate]}}, {}, {}, ())
    assert service.directory.root_navigation_revision != before
    with pytest.raises(ValueError, match='more than one project'):
        service.directory.resolve('saved-00000')
    service.projections.invalidate(state=service.state, session_ids=set())
    duplicates = service.projections.chats(service.state, section='shortcuts')
    assert duplicates == service.directory.chats(service.state, section='shortcuts')
    service.state['hiddenNativeWorkspaces'] = [workspace['id']]
    service._publish_changes(globals={'hiddenNativeWorkspaces'})
    hidden = service.projections.chats(service.state, section='shortcuts')
    assert hidden == service.directory.chats(service.state, section='shortcuts')
    assert hidden != duplicates


async def test_root_query_generations_retire_without_registry_serialization(service, monkeypatch):
    from amplifier_web import state_projections
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.state['view']['navChatScope'] = 'all'
    original = state_projections.json.dumps
    registry = service.state['workspaces']
    def checked(value, *args, **kwargs):
        assert value is not registry
        return original(value, *args, **kwargs)
    monkeypatch.setattr(state_projections.json, 'dumps', checked)
    for number in range(12):
        registry[0]['name'] = f'Workspace {number}'
        service._save_changes(globals={'workspaces'})
        service.projections.chats(service.state, section='shortcuts')
        assert len([key for key in service.projections.values if key[0] == 'directory-chats']) == 1


def test_native_root_generation_additive_upgrade_without_body_decode(tmp_path, monkeypatch):
    from amplifier_web import native_catalog
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    with catalog.connect() as db:
        db.execute('ALTER TABLE native_revisions DROP COLUMN root_navigation')
    def forbidden(*args, **kwargs):
        raise AssertionError('Counter migration must not read historical JSON')
    monkeypatch.setattr(native_catalog.json, 'loads', forbidden)
    upgraded = NativeCatalog(catalog.path, catalog.home)
    with upgraded.connect() as db:
        assert db.execute('SELECT root_navigation FROM native_revisions').fetchone() == (0,)


async def test_workspace_pin_reorder_does_not_accumulate_mixed_pages(service):
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.state['view']['navChatScope'] = 'all'
    for number in range(12):
        service.state['pinnedWorkspaceIds'] = ['one', 'two'] if number % 2 else ['two', 'one']
        service._save_changes(globals={'pinnedWorkspaceIds'})
        service.projections.workspace_pins_changed()
        actual = service.projections.pinned_navigation(service.state)
        assert actual == service.projections._pinned_navigation(service.state)
        assert len([key for key in service.projections.values if key[0] == 'pinned-navigation']) == 1


@pytest.mark.parametrize('count', [270, 2700, 27000])
async def test_scale_six_queries_reuse_100_observations(service, tmp_path, monkeypatch, count):
    from amplifier_web import chat_navigation
    # The reuse oracle changes child facts only, not relative-time inputs.
    monkeypatch.setattr(chat_navigation, 'list_clock', lambda: 1_800_000_000)
    from amplifier_web.native_catalog import CatalogRows, CatalogPatchRows
    from test_native_directory import seed
    from test_history_directory_query_budget import measure_sql
    import json
    catalog = NativeCatalog(tmp_path / 'scale.sqlite3', tmp_path / 'native')
    project, workspace = seed(catalog, tmp_path / 'workspace', count)
    service.state['workspaces'].append(workspace)
    worker = service.state['sessions'][0]
    worker.update(sessionKind='worker', parentId='beta')
    service.state['pinnedSessionIds'] = ['saved-00000']
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces', 'pinnedSessionIds'})
    service.directory.bind(catalog)
    states = []
    # Six fixed active queries; only dormant cardinality changes across scales.
    for section, patch in [('shortcuts', {}), ('pinned', {}), ('search', {'navFilter':'Straße'}),
            (None, {'navSort':'name'}), (None, {'navChatScope':'workspace'}),
            ('search', {'navStatusFilter':'idle'})]:
        states.append((section, {**service.state, 'selectedWorkspaceId': workspace['id'],
            'view': {**service.state['view'], 'navChatScope':'all', **patch}}))
    queries = []
    original = service.directory.chats
    def query(*args, **kwargs):
        queries.append(kwargs.get('section'))
        return original(*args, **kwargs)
    monkeypatch.setattr(service.directory, 'chats', query)
    def pages():
        return [service.projections.chats(state, section=section) for section, state in states]
    first, cold_vm, cold_ms = measure_sql(service, pages)
    assert len(queries) == 6 and first[0]['total'] == count + 1  # all native roots plus beta
    decoded = []
    decode = service.directory.decode
    monkeypatch.setattr(service.directory, 'decode', lambda raw: (decoded.append(raw), decode(raw))[1])
    samples = []
    for number in range(100):
        worker['title'] = f'Worker update {number}'
        service._save_changes(sessions={'alpha'})
        following, vm, elapsed = measure_sql(service, pages)
        assert all(a is b for a, b in zip(first, following))
        assert vm < 20000
        samples.append({'ms':elapsed, 'vm':vm})
    reuse_decoded = len(decoded)
    assert len(queries) == 6, 'No historical root query during 100 unrelated updates'
    assert len(decoded) <= 200, 'Only the two admitted overlay owners may be decoded'
    # Separately measure a true visible root change; it must invalidate all six.
    base = CatalogRows(catalog, project)
    prior = {'workspace':workspace, 'sessions':base}
    changed = {**prior, 'sessions':CatalogPatchRows(base, [{**base.get(f'saved-{count-1:05d}'), 'title':'Changed visible root'}])}
    catalog.save({project:changed}, {project:prior}, {}, ())
    service.projections.invalidate(state=service.state, session_ids=set())
    changed_pages, changed_vm, changed_ms = measure_sql(service, pages)
    assert len(queries) == 12
    for result, (section, state) in zip(changed_pages, states):
        assert result == original(state, section=section)
    times = sorted(row['ms'] for row in samples)
    print(json.dumps({'dormantRoots':count, 'activeQueries':6, 'observations':100,
        'cold':{'ms':cold_ms,'vm':cold_vm}, 'reuse':{'p50Ms':times[49],'p95Ms':times[94],
            'p99Ms':times[98],'maxMs':times[-1], 'maxVm':max(row['vm'] for row in samples),
            'rootQueryCalls':0, 'decodedOverlayRows':reuse_decoded},
        'realChange':{'ms':changed_ms,'vm':changed_vm}, 'samples':samples}))


async def test_search_clock_transition_invalidates_only_its_relative_time_identity(service, monkeypatch):
    from amplifier_web import chat_navigation
    now = [1_800_000_000]
    monkeypatch.setattr(chat_navigation, 'list_clock', lambda: now[0])
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    state = {**service.state, 'view': {**service.state['view'], 'navChatScope': 'all'}}
    first = service.projections.chats(state, section='search')
    shortcuts = service.projections.chats(state, section='shortcuts')
    assert service.projections.chats(state, section='search') is first
    now[0] += 60
    assert service.projections.chats(state, section='search') is not first
    assert service.projections.chats(state, section='shortcuts') is shortcuts


async def test_explicit_unknown_invalidation_drops_untracked_global_pages(service):
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    first = service.projections.chats(service.state)
    service.state['workspaces'][0]['available'] = False
    service.projections.invalidate()
    following = service.projections.chats(service.state)
    assert following is not first
    assert following == service.directory.chats(service.state)
