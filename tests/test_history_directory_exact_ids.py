"""Visible metadata reads visit requested owners, not the retained catalog."""
import json
import time
import uuid

import pytest

from amplifier_web.history_directory import PUBLIC_ID_BATCH
from amplifier_web.native_catalog import CatalogPatchRows, CatalogRows, NativeCatalog
from test_history_directory_navigation import directory_fixture
from test_native_directory import seed


def complete_query(directory, identities, state):
    ids = sorted({identity for identity in identities if isinstance(identity, str)})
    return directory.select('id IN (SELECT value FROM json_each(?))',
                            (json.dumps(ids),), limit=len(ids), state=state)


def native_fixture(tmp_path, count=12, locals=12):
    state, directory = directory_fixture(tmp_path, locals)
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    project, workspace = seed(catalog, tmp_path / 'workspace', count)
    directory.bind(catalog)
    return state, directory, catalog, project, workspace


def change_native(catalog, project, workspace, changed=(), removed=()):
    before = {'workspace': workspace, 'sessions': CatalogRows(catalog, project)}
    catalog.save({project: {**before, 'sessions': CatalogPatchRows(before['sessions'], changed, set(removed))}},
                 {project: before}, {}, set())


def test_history_tombstones_keep_native_scope_and_legacy_project_fences(tmp_path):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, 2, 0)
    other, other_workspace = seed(catalog, tmp_path/'other', 1)
    state['workspaces'] = [workspace, other_workspace]
    other_id = catalog.get(other, 'saved-00000')['id']
    tombstone = {'id':'review', 'project':project, 'workspace':workspace['path'],
                 'ids':['saved-00000'], 'preserveWorkspace':True,
                 'historySelection':{'project':project, 'nativeId':'saved-00000'}}
    directory.db.execute('INSERT INTO managed_deletions VALUES(?,?,?,?)',
                         ('review','token','confirmed',json.dumps(tombstone)))
    directory.db.commit()
    # The stale native index still contains the deleted root. Another project's
    # identical native ID remains a different conversation.
    expected = {'saved-00001', other_id}
    assert {row['id'] for row in directory.select(limit=10)} == expected
    assert {row['id'] for row in directory.chats(state, section='recent')['items']} == expected
    assert directory.root_count() == 2
    # Retained managed-chat/legacy receipts keep their whole-project fence.
    tombstone.pop('preserveWorkspace')
    directory.db.execute('UPDATE managed_deletions SET value=? WHERE id=?', (json.dumps(tombstone),'review'))
    directory.db.commit()
    assert {row['id'] for row in directory.select(limit=10)} == {other_id}
    assert directory.root_count() == 1


def test_scoped_history_suppression_uses_existing_local_identity_fallback(tmp_path):
    from amplifier_web.managed_deletion import CatalogSuppression

    state, directory, catalog, project, workspace = native_fixture(tmp_path, 2, 0)
    other, other_workspace = seed(catalog, tmp_path/'other', 1)
    state['workspaces'] = [workspace, other_workspace]
    # Local owners can lack nativeProject/nativeIdentity before discovery. The
    # established workspace/runtime aliases must agree in merge and SQL paths.
    rows = [{'id':'local-target','runtimeSessionId':'saved-00000',
             'workspace':workspace['path'],'sessionKind':'root'},
            {'id':'local-other','runtimeSessionId':'saved-00000',
             'workspace':other_workspace['path'],'sessionKind':'root'}]
    state['sessions'] = rows
    directory.sync(rows, state)
    tombstone = {'id':'review','project':project,'workspace':workspace['path'],
                 'ids':['saved-00000'],'preserveWorkspace':True}
    directory.db.execute('INSERT INTO managed_deletions VALUES(?,?,?,?)',
                         ('review','token','done',json.dumps(tombstone)))
    directory.db.commit()
    deleted = CatalogSuppression(directory.db)
    assert deleted.contains(rows[0]) and not deleted.contains(rows[1])
    assert {row['id'] for row in directory.select(limit=10)} == {'local-other','saved-00001'}
    assert directory.root_count() == 2


@pytest.mark.parametrize('count', [270, 27000])
def test_six_exact_summaries_have_bounded_sql_work_in_mixed_catalog(tmp_path, count):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, count, 187)
    for index, row in enumerate(state['sessions']):
        row.update(nativeIdentity=f'saved-{index:05d}', nativeProject=project,
                   historyManaged=True, titleSource='native')
    directory.sync(state['sessions'], state)
    directory.db.commit()
    state['sessions'] = state['sessions'][:6]
    wanted = ['s0', 's3', 's100', *(f'saved-{count-offset:05d}' for offset in (1, 2, 3))]
    baseline_vm = 0
    def baseline_progress():
        nonlocal baseline_vm
        baseline_vm += 1000
        return 0
    directory.db.set_progress_handler(baseline_progress, 1000)
    baseline_started = time.monotonic()
    try:
        expected = complete_query(directory, wanted, state)
    finally:
        directory.db.set_progress_handler(None, 0)
    baseline_ms = (time.monotonic()-baseline_started)*1000
    assert len(expected) == 6
    # Any accidental payload read outside the page fails, independently of the
    # VM budget which catches full scalar scans without JSON decoding.
    native_keys = [f'saved-{index:05d}' for index in (0, 3, 100, count-1, count-2, count-3)]
    with catalog.connect() as db:
        db.execute('UPDATE native_rows SET value=? WHERE identity NOT IN (SELECT value FROM json_each(?))',
                   ('not JSON', json.dumps(native_keys)))
    directory.db.execute('UPDATE history_local_rows SET summary=? WHERE id NOT IN (SELECT value FROM json_each(?))',
                         ('not JSON', json.dumps(wanted)))
    directory.db.commit()
    decoded = []
    original = directory.decode
    directory.decode = lambda row: (decoded.append(row[0]), original(row))[1]
    instructions = 0
    def progress():
        nonlocal instructions
        instructions += 1000
        return instructions > 150000
    changes = directory.db.total_changes
    directory.db.set_progress_handler(progress, 1000)
    started = time.monotonic()
    try:
        actual = directory.summary_ids(wanted, state=state)
    finally:
        directory.db.set_progress_handler(None, 0)
    assert actual == expected
    assert set(decoded) == set(wanted) and len(decoded) == 6
    assert directory.db.total_changes == changes and not directory.db.in_transaction
    assert len(state['sessions']) == 6
    print(json.dumps({'nativeRows': count, 'localOverrides': 187, 'requested': 6,
                      'vm': instructions, 'ms': (time.monotonic()-started)*1000,
                      'unscopedVm': baseline_vm, 'unscopedMs': baseline_ms}))


def test_exact_ids_do_not_resolve_native_aliases_or_resurrect_overlaid_rows(tmp_path):
    state, directory, catalog, first, workspace = native_fixture(tmp_path, 2, 1)
    second, _ = seed(catalog, tmp_path / 'other', 1)
    owner = state['sessions'][0]
    owner.update(id='app-owner', nativeIdentity='saved-00000', nativeProject=first, historyManaged=True)
    directory.sync([owner], state)
    directory.db.commit()
    state['sessions'] = []
    first_alias = catalog.get(first, 'saved-00000')['id']
    second_alias = catalog.get(second, 'saved-00000')['id']
    unique_alias = catalog.get(first, 'saved-00001')['id']
    ids = ['app-owner', first_alias, second_alias, 'saved-00000', 'saved-00001', unique_alias]
    expected = complete_query(directory, ids, state)
    assert {row['id'] for row in expected} == {'app-owner', second_alias, 'saved-00001'}
    assert directory.summary_ids(ids, state=state) == expected
    assert directory.summary_ids([first_alias, unique_alias, 'saved-00000'], state=state) == []
    assert directory.select(public_ids=[first_alias, unique_alias, 'saved-00000'], state=state) == []
    with pytest.raises(ValueError, match='more than one project'):
        directory.resolve('saved-00000')
    # Current live placement replaces the same saved app ID. Its old native
    # pair must stop suppressing the native row; its new pair must suppress it.
    owner.update(nativeIdentity='saved-00001')
    state['sessions'] = [owner]
    expected = complete_query(directory, ids, state)
    assert {row['id'] for row in expected} == {'app-owner', first_alias, second_alias}
    assert directory.summary_ids(ids, state=state) == expected


def test_exact_summary_keeps_canonical_parent_name_availability_and_filters(tmp_path):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, 4, 3)
    for index, row in enumerate(state['sessions']):
        row.update(nativeIdentity=f'saved-{index:05d}', nativeProject=project,
                   historyManaged=True, titleSource='native', historyLoaded=True,
                   nativeRevision=['older-loaded-revision'])
    directory.sync(state['sessions'], state)
    directory.db.commit()
    state['sessions'] = []
    child = catalog.get(project, 'saved-00000')
    child.update(parentId='saved-00001', sessionKind='worker', name='Canonical child',
                 title='Canonical child', nameRevision=7, namePolicyRevision=8,
                 transcriptRevision=['newer-canonical-revision'])
    workspace = {**workspace, 'available': False}
    change_native(catalog, project, workspace, [child])
    expected = complete_query(directory, ['s0'], state)
    actual = directory.summary_ids(['s0'], state=state)
    assert actual == expected
    assert actual[0]['parentId'] == 's1'  # Parent itself is outside requested IDs.
    assert actual[0]['title'] == 'Canonical child' and actual[0]['workspaceAvailable'] is False
    assert actual[0]['nativeRevision'] == ['older-loaded-revision']
    hidden = uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-native:{project}/saved-00000').hex
    for overlay in ({'hiddenNativeSessions': [hidden]}, {'hiddenNativeWorkspaces': [workspace['id']]}):
        scoped = {**state, **overlay}
        assert directory.summary_ids(['s0'], state=scoped) == complete_query(directory, ['s0'], scoped) == []
    directory.db.execute('INSERT INTO managed_deletions VALUES(?,?,?,?)',
        ('delete-child', 'token', 'confirmed', json.dumps({'id': 'delete-child', 'project': 'other', 'ids': ['s0']})))
    directory.db.commit()
    assert directory.summary_ids(['s0'], state=state) == complete_query(directory, ['s0'], state) == []
    assert state['sessions'] == []


def test_exact_id_batches_preserve_order_and_decode_only_final_requested_rows(tmp_path, monkeypatch):
    state, directory = directory_fixture(tmp_path, 400)
    state['sessions'] = []
    wanted = [f's{index}' for index in range(375, 11, -1)] + ['s20', None, 23, 'missing']
    expected = complete_query(directory, wanted, state)
    scopes = []
    original = directory.effective
    def effective(*args, **kwargs):
        batch = kwargs.get('public_ids')
        assert batch is not None and len(batch) <= PUBLIC_ID_BATCH
        scopes.append(batch)
        return original(*args, **kwargs)
    monkeypatch.setattr(directory, 'effective', effective)
    assert directory.summary_ids(wanted, state=state) == expected
    assert len(scopes) == 3 and sum(map(len, scopes)) == 365
    assert directory.summary_ids([None, 3], state=state) == []
    assert len(scopes) == 3


def test_exact_batch_snapshot_keeps_its_local_and_native_versions(tmp_path):
    state, directory, catalog, project, workspace = native_fixture(tmp_path)
    state['sessions'] = []
    ids = ['s1', 'saved-00001', 'saved-00002']
    before = directory.summary_ids(ids, state=state)
    reader = directory.read_snapshot(state)
    try:
        local = directory.resolve('s1')
        local['title'] = 'New local title'
        directory.sync([local], state)
        directory.db.commit()
        renamed = {**catalog.get(project, 'saved-00001'), 'title': 'New native title'}
        change_native(catalog, project, workspace, [renamed], ['saved-00002'])
        assert reader.summary_ids(ids) == before
        current = directory.summary_ids(ids, state=state)
        assert {row['id']: row['title'] for row in current} == {'s1': 'New local title', 'saved-00001': 'New native title'}
    finally:
        reader.close()
