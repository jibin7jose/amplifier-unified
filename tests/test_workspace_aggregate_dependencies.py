"""Workspace totals follow their facts, independently of chat page labels."""
from copy import deepcopy

import pytest

from amplifier_web.native_catalog import NativeCatalog
from amplifier_web.shell_resources import scope_for
from test_shell_modules import service, settled_resources


@pytest.mark.parametrize('kind', ['worker', 'internal', 'root'])
async def test_title_only_native_change_reuses_workspace_aggregates(service, tmp_path, monkeypatch, kind):
    workspace = deepcopy(service.state['workspaces'][0])
    native_workspace = {**workspace, 'nativeProject': 'proof', 'sessionCount': 1,
                        'workerSessionCount': int(kind == 'worker'),
                        'internalSessionCount': int(kind == 'internal')}
    root = {'id': 'native-root', 'nativeIdentity': 'native-root', 'nativeProject': 'proof',
            'sessionKind': 'root', 'workspaceId': workspace['id'], 'workspace': workspace['path'],
            'title': 'Saved root', 'createdAt': 1, 'recentActivityAt': 1}
    child = {**root, 'id': 'native-child', 'nativeIdentity': 'native-child',
             'parentId': 'native-root', 'sessionKind': kind, 'title': 'Saved child'}
    initial = {'workspace': native_workspace, 'sessions': [root] if kind == 'root' else [root, child]}
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home')
    catalog.save({'proof': initial}, {}, {}, ())
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces', 'sharedHistory'})
    service.directory.bind(catalog)
    client = service.clients.attach('proof-client')
    client.update(selectedSessionId='alpha', selectedWorkspaceId=workspace['id'])
    aggregates, reads = [], []
    queries = service.shell.resources.queries
    factory = queries.factory
    def observed_factory(*args, **kwargs):
        projector = factory(*args, **kwargs)
        project = projector.project
        async def observed(inputs, **options):
            reads.append((inputs['operation'], options['key']))
            result = await project(inputs, **options)
            if inputs['operation'] == 'workspace_counts' and result.get('status') == 'complete':
                aggregates.append(deepcopy(result['value']))
            return result
        projector.project = observed
        return projector
    monkeypatch.setattr(queries, 'factory', observed_factory)
    with service.clients.bind('proof-client'):
        owner = service.shell.resources
        owner.register('proof-client', 1, ['sidebarRecent', 'workspaceShortcuts'], scope_for(service.state))
        first = await settled_resources(service, 'proof-client')
        frozen = deepcopy(first)
        aggregate_before = len(aggregates)
        assert aggregate_before > 0
        reads_before = len(reads)
        evidence_before = service.performance_diagnostics.snapshot()['shellResourceRebuilds']['total']
        updated = deepcopy(initial)
        updated['sessions'][-1]['title'] = 'Renamed native record'
        catalog.save({'proof': updated}, {'proof': initial}, {}, ())
        service._publish_changes(globals={'workspaces', 'sharedHistory'})
        following = await settled_resources(service, 'proof-client')
        evidence = service.performance_diagnostics.snapshot()['shellResourceRebuilds']
        attempts = evidence['total'] - evidence_before
        # Pending/completed host attempts are not physical query executions.
        # Count exact ready resource changes and real worker reads separately.
        changed_pages = [(name, resource) for name, instance in first['instances'].items()
                         for resource, record in instance['resources'].items()
                         if following['instances'][name]['resources'][resource] != record]
        new_reads = reads[reads_before:]
        assert len(changed_pages) == (2 if kind == 'root' else 0)
        assert all(resource == 'sidebarRecent' for _, resource in changed_pages)
        if kind == 'root':
            assert 0 < len(new_reads) <= len(changed_pages)
            assert all(operation == 'chats' for operation, _ in new_reads)
            assert len({key for _, key in new_reads}) == len(new_reads), 'No query key may replay'
        else:
            assert attempts == 0 and not new_reads
        assert len(aggregates) == aggregate_before
        assert all(row == aggregates[0] for row in aggregates), 'aggregate facts did not change'
        if attempts:
            rebuilds = evidence['recent'][-attempts:]
            assert {row['resource'] for row in rebuilds} == {'sidebarRecent'}
            # Each of the two resource owners observes native invalidation,
            # then completes that same key without another invalidation.
            by_package = {}
            for row in rebuilds:
                by_package.setdefault(row['package'], []).append(row['reasons'])
            assert len(by_package) == 2
            assert all(reasons == [['navigation.native'], []] for reasons in by_package.values())
        for name, instance in first['instances'].items():
            before = instance['resources']
            after = following['instances'][name]['resources']
            assert after['workspaceShortcuts'] == before['workspaceShortcuts']
            if kind != 'root':
                assert after['sidebarRecent'] == before['sidebarRecent'], 'worker/internal change is absent from root pages'
            else:
                assert after['sidebarRecent'] != before['sidebarRecent'], 'root title remains visible and fresh'
        assert first == frozen
        print({'kind': kind, 'pageRebuildAttempts': attempts,
               'aggregateQueriesBefore': aggregate_before, 'aggregateQueriesAfter': len(aggregates),
               'aggregateFactsUnchanged': True})


@pytest.mark.parametrize('kind', ['root', 'worker', 'internal'])
async def test_local_title_change_preserves_aggregate_and_cached_workspace_page(service, monkeypatch, kind):
    row = service.state['sessions'][0]
    row['sessionKind'] = kind
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    first = service.projections.workspaces(service.state)
    revision = service.directory.workspace_revision
    def forbidden(*args, **kwargs):
        raise AssertionError('Unchanged workspace facts must not rerun the aggregate')
    monkeypatch.setattr(service.directory, 'workspace_counts', forbidden)
    row['title'] = 'A different title'
    service._publish_changes(sessions={'alpha'})
    assert service.directory.workspace_revision == revision
    assert service.projections.workspaces(service.state) is first


@pytest.mark.parametrize('change', ['recent', 'workspace', 'working', 'error', 'unread', 'role'])
async def test_real_workspace_facts_change_and_match_fresh_authoritative_query(service, change):
    from amplifier_web.workspace_navigation import _index, snapshot
    row = service.state['sessions'][0]
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    first = deepcopy(service.projections.workspaces(service.state))
    revision = service.directory.workspace_revision
    if change == 'recent': row['recentActivityAt'] = 100
    elif change == 'workspace': row.update(workspaceId='two', workspace=service.state['workspaces'][1]['path'])
    elif change == 'working': row['status'] = 'working'
    elif change == 'error': row['error'] = 'An actionable failure'
    elif change == 'unread': row['manualUnread'] = 'unread-marker'
    else: row['sessionKind'] = 'worker'
    service._publish_changes(sessions={'alpha'})
    actual = service.projections.workspaces(service.state)
    expected = snapshot(service.state, index=_index(service.state,
        aggregates=service.directory.workspace_counts(service.state)))
    assert actual == expected
    assert actual != first
    assert service.directory.workspace_revision != revision


async def test_read_receipt_registry_archive_and_explicit_removal_remain_current(service):
    from amplifier_web.history_directory import notices
    row = service.state['sessions'][0]
    row['manualUnread'] = 'read-me'
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    first = service.projections.workspaces(service.state)
    notice = notices(row, service.state)[0]
    service.state.setdefault('attentionRead', {})[notice[2]] = notice[3]
    service._publish_changes(globals={'attentionRead'})
    read = service.projections.workspaces(service.state)
    assert read != first
    service.state['workspaces'][0]['archived'] = True
    service._publish_changes(globals={'workspaces'})
    archived = service.projections.workspaces(service.state)
    assert archived != read
    service.state['view']['navWorkspaceArchive'] = 'all'
    all_workspaces = service.projections.workspaces(service.state)
    service.state['sessions'].remove(row)
    service.forget_session_records({'alpha'})
    service._publish_full(reason='explicit test owner removal')
    assert service.projections.workspaces(service.state) != all_workspaces


async def test_age_aggregates_expire_and_track_warm_pin_membership(service, monkeypatch):
    from amplifier_web import chat_navigation
    now = [1_800_000_000]
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: now[0])
    for row in service.state['sessions']:
        row.update(recentActivityAt=now[0]-86400, navigationActivityAt=now[0]-86400)
    service.state['view'].update(navAge='24h', navWorkspaceMode='recent')
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    def counts():
        return {row['workspaceId']: row['chatCount']
                for row in service.projections.workspace_shortcuts(service.state)}
    assert counts() == {'one': 1, 'two': 1}
    now[0] += 60
    assert counts() == {'one': 0, 'two': 0}, 'Registry remains navigable at zero chats'
    service.state['pinnedSessionIds'] = ['alpha']
    service._publish_changes(globals={'pinnedSessionIds'})
    assert counts() == {'one': 1, 'two': 0}
    service.state['pinnedSessionIds'] = []
    service._publish_changes(globals={'pinnedSessionIds'})
    assert counts() == {'one': 0, 'two': 0}
    service.state['view']['navAge'] = 'all'
    assert counts() == {'one': 1, 'two': 1}


async def test_older_local_facts_derive_workspace_baseline_by_key_without_rewriting_catalog(service):
    from amplifier_web.history_directory_observations import initialize
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.db.execute('ALTER TABLE history_observation_rows DROP COLUMN workspace')
    service.db.execute("DELETE FROM history_observation_revisions WHERE collection='workspace'")
    service.db.commit()
    initialize(service.db)
    service.db.commit()
    before = service.directory.workspace_revision
    service.state['sessions'][0]['title'] = 'Title after additive upgrade'
    service._publish_changes(sessions={'alpha'})
    assert service.directory.workspace_revision == before
    assert service.db.execute("SELECT workspace FROM history_observation_rows WHERE id='beta'").fetchone()[0] is None


async def test_unsaved_overlay_and_eviction_follow_workspace_facts(service):
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    row = service.state['sessions'][0]
    before = service.directory.workspace_revision
    row['manualUnread'] = 'live-notice'
    assert service.directory.workspace_revision != before
    row.pop('manualUnread')
    assert service.directory.workspace_revision == before
    service.state['sessions'].remove(row)
    assert service.directory.workspace_revision == before


@pytest.mark.parametrize('count', [270, 27000])
async def test_title_change_reuses_large_workspace_aggregate_without_dormant_decode(service, tmp_path, monkeypatch, count):
    from amplifier_web.native_catalog import CatalogPatchRows, CatalogRows
    from test_native_directory import seed
    from test_history_directory_query_budget import measure_sql
    catalog = NativeCatalog(tmp_path / 'scale.sqlite3', tmp_path / 'native-home')
    project, workspace = seed(catalog, tmp_path / 'large-workspace', count)
    # The bulk SQL seed has no writer receipt; establish it before the warm baseline.
    catalog.save({}, {}, {}, ())
    service.state['workspaces'].append(workspace)
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.directory.bind(catalog)
    service.state['view']['navWorkspaceMode'] = 'recent'
    first, cold_vm, cold_ms = measure_sql(service, lambda: service.projections.workspaces(service.state))
    assert next(row for row in first['rows'] if row.get('workspaceId') == workspace['id'])['chatCount'] == count
    base = CatalogRows(catalog, project)
    prior = {'workspace': workspace, 'sessions': base}
    row = base.get('saved-00000')
    changed = {**prior, 'sessions': CatalogPatchRows(base, [{**row, 'title': 'Renamed'}])}
    catalog.save({project: changed}, {project: prior}, {}, ())
    service.projections.invalidate(state=service.state, session_ids=set())
    decoded = []
    decode = service.directory.decode
    monkeypatch.setattr(service.directory, 'decode', lambda raw: (decoded.append(raw[0]), decode(raw))[1])
    following, warm_vm, warm_ms = measure_sql(service, lambda: service.projections.workspaces(service.state))
    assert following is first
    assert set(decoded) <= {'alpha', 'beta'}
    assert warm_vm < 20000
    assert service.directory.workspace_counts(service.state)[workspace['path']]['chatCount'] == count
    print({'nativeRows': count, 'coldVm': cold_vm, 'coldMs': cold_ms,
           'renameVm': warm_vm, 'renameMs': warm_ms, 'decoded': len(decoded)})


async def test_registry_epoch_does_not_serialize_registry_per_query_or_retain_old_generations(service, monkeypatch):
    from amplifier_web import state_projections
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    first = service.projections.workspaces(service.state)
    original = state_projections.json.dumps
    registry = service.state['workspaces']
    def checked(value, *args, **kwargs):
        assert value is not registry, 'The registry has a committed scalar epoch'
        return original(value, *args, **kwargs)
    monkeypatch.setattr(state_projections.json, 'dumps', checked)
    for _ in range(30):
        service.projections.invalidate(state=service.state, session_ids=set())
        assert service.projections.workspaces(service.state) is first
    monkeypatch.setattr(state_projections.json, 'dumps', original)
    for number in range(12):
        registry[0]['name'] = f'Renamed workspace {number}'
        service._save_changes(globals={'workspaces'})
        page = service.projections.workspaces(service.state)
        assert page != first
        keys = [key for key in service.projections.values if key[0] in
                {'workspace-index', 'workspaces', 'workspace-shortcuts'}]
        assert len(keys) <= 2
        assert all(service.projections.values[key] is not first for key in keys)


async def test_worker_notice_and_read_receipt_do_not_invalidate_root_workspace_totals(service):
    from amplifier_web.history_directory import notices
    row = service.state['sessions'][0]
    row.update(sessionKind='worker', manualUnread='worker-notice')
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    before = service.directory.workspace_revision
    first = service.projections.workspaces(service.state)
    notice = notices(row, service.state)[0]
    service.state.setdefault('attentionRead', {})[notice[2]] = notice[3]
    service._publish_changes(globals={'attentionRead'})
    assert service.directory.workspace_revision == before
    assert service.projections.workspaces(service.state) is first
    row['manualUnread'] = 'another-worker-notice'
    service._publish_changes(sessions={'alpha'})
    assert service.directory.workspace_revision == before
    assert service.projections.workspaces(service.state) is first


def test_native_aggregate_generation_tracks_recency_membership_and_placement(tmp_path):
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    workspace = {'id': 'w', 'path': '/w', 'available': True}
    root = {'id': 'r', 'nativeIdentity': 'r', 'nativeProject': 'p', 'sessionKind': 'root',
            'workspaceId': 'w', 'workspace': '/w', 'recentActivityAt': 1, 'title': 'Root'}
    child = {**root, 'id': 'c', 'nativeIdentity': 'c', 'sessionKind': 'worker', 'parentId': 'r'}
    current = {'p': {'workspace': workspace, 'sessions': [root, child]}}
    catalog.save(current, {}, {}, ())
    def revision():
        with catalog.connect() as db:
            return db.execute('SELECT workspace FROM native_revisions WHERE namespace=?', (catalog.namespace,)).fetchone()[0]
    baseline = revision()
    for field, value, index, changes in [('title','Changed',0,False), ('recentActivityAt',20,1,False),
            ('recentActivityAt',20,0,True), ('sessionKind','internal',0,True)]:
        following = deepcopy(current)
        following['p']['sessions'][index][field] = value
        catalog.save(following, current, {}, ())
        assert revision() == baseline + int(changes)
        baseline = revision()
        current = following
    following = deepcopy(current)
    following['p']['workspace'].update(id='moved', path='/moved')
    catalog.save(following, current, {}, ())
    assert revision() == baseline + 1
    catalog.save({}, following, {}, ())
    assert revision() == baseline + 2


def test_native_generation_upgrade_does_not_decode_saved_rows(tmp_path, monkeypatch):
    from amplifier_web import native_catalog
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    with catalog.connect() as db:
        db.execute('ALTER TABLE native_revisions DROP COLUMN workspace')
    def forbidden(*args, **kwargs):
        raise AssertionError('Adding a counter does not require historical JSON')
    monkeypatch.setattr(native_catalog.json, 'loads', forbidden)
    upgraded = NativeCatalog(catalog.path, catalog.home)
    with upgraded.connect() as db:
        assert db.execute('SELECT workspace FROM native_revisions').fetchone() == (0,)
