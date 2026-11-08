"""Changed native metadata stays scoped through durable publication."""
from amplifier_web.state_records import load as load_saved_state
import json
import sqlite3
import time

import pytest

from amplifier_web.native_history import NativeHistory
from amplifier_web.session_files import project_slug
from test_native_history import session, write_json
from test_automatic_history import app_factory, native_session


async def test_workspace_only_delta_refreshes_admitted_native_availability(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'native-workspace'
    for number in range(20):
        native_session(workspace, f'available-{number}')
    app = app_factory()
    index = app.history.index
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))

    async def finish():
        for _ in range(100):
            await app.history.refresh(force=False)
            assert app.state['sharedHistory']['error'] is None
            if app.state['sharedHistory']['reconciliation']['phase'] == 'complete':
                return
        pytest.fail('bounded discovery did not finish')

    await finish()
    owner = app._session('available-0', hydrate=False)
    app._publish_changes(sessions=[owner['id']])
    await app.history.refresh(force=False)
    assert owner['workspaceAvailable'] is True and owner['historyReadOnlyReason'] is None
    residents = {row['id'] for row in app.state['sessions']}
    decoded = []
    original = index._catalog.get
    monkeypatch.setattr(index._catalog, 'get', lambda project, identity:
        (decoded.append(identity), original(project, identity))[1])
    # Start a normal recovery one yield at a time. The workspace-only change
    # necessarily arrives after the initial non-forced slice, with no session
    # payload delta and no local settings epoch to trigger a full merge.
    index._recovery_budget = 1
    moved = tmp_path / 'moved-workspace'
    workspace.rename(moved)
    index._reconcile_at = 0
    await finish()
    assert owner['workspaceAvailable'] is False
    assert owner['historyReadOnlyReason'] == 'The original workspace folder is no longer available.'
    moved.rename(workspace)
    index._reconcile_at = 0
    await finish()
    assert owner['workspaceAvailable'] is True and owner['historyReadOnlyReason'] is None
    assert decoded and set(decoded) == {'available-0'}
    assert {row['id'] for row in app.state['sessions']} == residents
    assert app.runtime.started == [] and app.runtime.sent == []


def test_persistent_cache_reuses_compact_metadata_without_reading_bodies(tmp_path):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, 'saved-' + str(n),
                     {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Saved ' + str(n)})
             for n in range(80)]
    before = {(path / 'transcript.jsonl'): (path / 'transcript.jsonl').read_bytes() for path in paths}
    cache = tmp_path / 'app' / 'native-catalog.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    token, initial = first.scan_changes()
    assert initial['reset'] and len(initial['sessions']) == 80
    first.close()
    second = NativeHistory(home, cache_path=cache)
    _, restarted = second.scan_changes()
    assert len(restarted['sessions']) == 80 and restarted['metadataReads'] == 0
    assert not restarted['issues']
    # Changed while stopped: only the changed compact source is reparsed.
    write_json(paths[9] / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Changed'})
    third = NativeHistory(home, cache_path=cache)
    _, changed = third.scan_changes()
    assert changed['metadataReads'] == 1
    assert next(row for row in changed['sessions'] if row['nativeIdentity'] == 'saved-9')['title'] == 'Changed'
    assert all(path.read_bytes() == body for path, body in before.items())
    assert cache.stat().st_mode & 0o777 == 0o600


def test_missed_notifications_recover_in_bounded_slices_and_keep_sources(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}',
                     {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Original'})
             for n in range(160)]
    index = NativeHistory(home, watch=True, cache_path=tmp_path / 'cache.sqlite3')
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    write_json(paths[-1] / 'metadata.json',
               {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Missed event'})
    before = (paths[-1] / 'transcript.jsonl').read_bytes()
    index._recovery_budget = 12
    index._reconcile_at = 0
    observed = []
    for _ in range(100):
        base, delta = index.scan_changes(since=base)
        assert delta['reconciliation']['steps'] <= 12
        observed.extend(delta['sessions'])
        if delta['reconciliation']['phase'] == 'complete':
            break
    else:
        pytest.fail('Bounded reconciliation did not complete')
    assert any(row['title'] == 'Missed event' for row in observed)
    assert (paths[-1] / 'transcript.jsonl').read_bytes() == before
    assert index._catalog is not None
    index.close()


def test_transient_catalog_save_failure_reconnects_without_restart(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, cache_path=tmp_path / 'cache.sqlite3')
    base, _ = index.scan_changes()
    def locked(*args):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(index._catalog, 'save', locked)
    write_json(path / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'New'})
    base, failed = index.scan_changes(since=base)
    assert index._catalog is None and {'kind': 'unavailable-catalog-cache'} in failed['issues']
    index._cache_retry_at = 0
    _, recovered = index.scan_changes(since=base)
    assert index._catalog is not None and not index._cache_error
    assert index.scan()['sessions'][0]['title'] == 'New'
    assert {'kind': 'unavailable-catalog-cache'} not in recovered['issues']


async def test_scoped_tombstone_keeps_last_counts_during_cache_failure_and_recovers(
        tmp_path, app_factory, monkeypatch):
    workspace = tmp_path/'workspace'
    native_session(workspace, 'deleted-root')
    sibling = native_session(workspace, 'sibling-root')
    app = app_factory()
    await app.history.refresh(force=True)
    index = app.history.index
    project = project_slug(workspace)
    tombstone = {'id':'review','project':project,'workspace':str(workspace),
                 'ids':['deleted-root'],'preserveWorkspace':True}
    app.db.execute('INSERT INTO managed_deletions VALUES(?,?,?,?)',
                   ('review','token','confirmed',json.dumps(tombstone)))
    app.db.commit()
    await app.history.refresh(force=True)
    shared = app.state['sharedHistory']
    assert shared['error'] is None and shared['sessionCount'] == 1
    assert {row['id'] for row in app.directory.select(limit=10)} == {'sibling-root'}
    counts = {key: shared[key] for key in
              ('projectCount','sessionCount','workerSessionCount','internalSessionCount')}
    def blocked(*args):
        raise sqlite3.OperationalError('Synthetic cache write unavailable')
    monkeypatch.setattr(index._catalog, 'save', blocked)
    metadata = sibling/'metadata.json'
    write_json(metadata, {**json.loads(metadata.read_text()),'name':'After cache recovery'})
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project,'sibling-root','metadata')}))
    await app.history.refresh(force=False)
    assert index._catalog is None
    assert shared['error'] is None
    assert {'kind':'unavailable-catalog-cache'} in shared['issues']
    assert {key:shared[key] for key in counts} == counts
    await app.history.refresh(force=False)
    assert shared['reconciliation']['phase'] == 'unavailable'
    assert {key:shared[key] for key in counts} == counts
    index._cache_retry_at = 0
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    await app.history.refresh(force=True)
    assert index._catalog is not None and shared['error'] is None
    assert {'kind':'unavailable-catalog-cache'} not in shared['issues']
    assert shared['sessionCount'] == 1
    page = app.directory.chats({**app.state, 'view':{
        **app.state.get('view', {}),'navChatScope':'all'}}, section='recent')
    assert [(row['id'],row['title']) for row in page['items']] == [('sibling-root','After cache recovery')]
    assert app.runtime.started == [] and app.runtime.sent == []


def test_transient_catalog_retry_deadline_wakes_quiet_watcher(tmp_path, monkeypatch):
    from types import SimpleNamespace
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, cache_path=tmp_path / 'cache.sqlite3')
    index.scan_changes()
    info = (home / 'projects').stat()
    index._watch = SimpleNamespace(unchanged=lambda: True, close=lambda: None)
    index._watch_root = info.st_dev, info.st_ino
    index._known_input = frozenset()
    index._reconcile_at = time.monotonic() + 60
    index._catalog = None
    index._cache_error = True
    index._cache_retry_at = time.monotonic() + 30
    assert not index.needs_scan([])
    index._cache_retry_at = 0
    assert index.needs_scan([])
    index.close()


def test_incremental_aggregates_match_full_catalog_through_removal_and_recreation(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    root = session(home, workspace, 'saved-root', {'working_dir': str(workspace), 'bundle': 'anchors'})
    child = session(home, workspace, 'saved-child',
                    {'working_dir': str(workspace), 'bundle': 'anchors', 'parent_id': 'saved-root'})
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, initial = index.scan_changes(force=True)
    assert (initial['sessionCount'], initial['workerSessionCount']) == (1, 1)
    project = root.parent.parent
    retained = tmp_path / 'retained'
    project.rename(retained)
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project.name, 'saved-root', 'transcript')}))
    base, removed = index.scan_changes(since=base)
    assert (removed['sessionCount'], removed['workerSessionCount']) == (0, 0)
    retained.rename(project)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {project.name}))
    for _ in range(12):
        base, restored = index.scan_changes(since=base)
        if restored['projectCount'] == 1:
            break
    assert (restored['sessionCount'], restored['workerSessionCount']) == (1, 1)
    # Repeat the acknowledged base: counting is not repeated on consumer retries.
    _, repeated = index.scan_changes(since=base)
    assert (repeated['sessionCount'], repeated['workerSessionCount']) == (1, 1)
    index.close()


def test_aggregate_cache_tracks_reclassification_retry_and_old_consumers(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    metadata = {'working_dir': str(workspace), 'bundle': 'anchors'}
    path = session(home, workspace, 'saved-root', metadata)
    cache = tmp_path / 'cache.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    first.scan_changes(force=True)
    first.close()
    index = NativeHistory(home, watch=True, cache_path=cache)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    oldest, initial = index.scan_changes()
    assert (initial['sessionCount'], initial['workerSessionCount'], initial['internalSessionCount']) == (1, 0, 0)
    base = oldest
    variants = [
        ({'parent_id': 'other-root'}, (0, 1, 0)),
        ({'session_visibility': 'internal'}, (0, 0, 1)),
        ({}, (1, 0, 0)),
        ({'parent_id': 'other-root'}, (0, 1, 0)),
        ({}, (1, 0, 0)),
    ]
    for number, (extra, expected) in enumerate(variants):
        write_json(path / 'metadata.json', {**metadata, **extra, 'name': f'Change {number}'})
        monkeypatch.setattr(index, '_invalidations',
                            lambda: (True, {(project_slug(workspace), 'saved-root', 'metadata')}))
        if number == 1:
            def failed_save(*args):
                raise sqlite3.OperationalError('database is locked')
            monkeypatch.setattr(index._catalog, 'save', failed_save)
        base, changed = index.scan_changes(since=base)
        fields = ('sessionCount', 'workerSessionCount', 'internalSessionCount')
        assert tuple(changed[field] for field in fields) == expected
        if number == 1:
            assert index._catalog is None
            index._cache_retry_at = 0
            base, changed = index.scan_changes(since=base)
            assert index._catalog is not None
            assert tuple(changed[field] for field in fields) == expected
        _, retry = index.scan_changes(since=oldest)
        assert tuple(retry[field] for field in fields) == expected
        rows = [row for project in index._projects.values() for row in project['sessions']]
        assert expected == tuple(sum(row['sessionKind'] == kind for row in rows)
                                 for kind in ('root', 'worker', 'internal'))
    assert retry['reset']  # The old consumer exceeded the bounded base journal.
    _, forced = index.scan_changes(force=True)
    assert tuple(forced[field] for field in fields) == (1, 0, 0)
    index.close()


def test_suspended_unreadable_leaf_keeps_revision_rows_immutable(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'saved-root', {'bundle': 'anchors'})
    index = NativeHistory(home, watch=True, cache_path=tmp_path / 'cache.sqlite3')
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, initial = index.scan_changes(force=True)
    assert initial['sessions'][0]['workspace'] is None
    original_row = dict(initial['sessions'][0])
    retained_row = index._projects[path.parent.parent.name]['sessions'][0]
    index._recovery_budget = 5
    index._reconcile_at = 0
    index.scan_changes(since=base, known_workspaces=[str(workspace)])
    # Pause after the iterator selected this leaf, before its safety check.
    retained = path.parent / '.retained-root'
    path.rename(retained)
    observed = []
    for _ in range(12):
        token, delta = index.scan_changes(since=base, known_workspaces=[str(workspace)])
        observed.extend(delta['sessions'])
        if delta['reconciliation']['phase'] == 'complete':
            break
    assert retained_row == original_row
    assert any(row['workspace'] == str(workspace) and row['canResume'] for row in observed)
    with sqlite3.connect(index._catalog.path) as db:
        stored = json.loads(db.execute('SELECT value FROM native_rows').fetchone()[0])
    assert stored['workspace'] == str(workspace) and stored['canResume']
    # A second consumer still using the original revision receives the same row.
    _, retry = index.scan_changes(since=base, known_workspaces=[str(workspace)])
    assert any(row['workspace'] == str(workspace) for row in retry['sessions'])
    retained.rename(path)
    _, restored = index.scan_changes(since=token, known_workspaces=[str(workspace)], force=True)
    assert restored['sessions'][0]['workspace'] == str(workspace)
    index.close()


def test_unchanged_recovery_probes_inputs_without_rebuilding_rows(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    for number in range(80):
        session(home, workspace, f'saved-{number:03d}',
                {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, watch=True, cache_path=tmp_path / 'cache.sqlite3')
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    # The first pass learns the complete registered/workspace stamp.
    index._reconcile_at = 0
    for _ in range(60):
        base, delta = index.scan_changes(since=base, known_workspaces=[str(workspace)])
        if delta['reconciliation']['phase'] == 'complete':
            break
    builds, saves = [], []
    original_build, original_save = index._scan_project_steps, index._catalog.save
    def build(*args, **kwargs):
        builds.append(True)
        return original_build(*args, **kwargs)
    def save(*args, **kwargs):
        saves.append(True)
        return original_save(*args, **kwargs)
    monkeypatch.setattr(index, '_scan_project_steps', build)
    monkeypatch.setattr(index._catalog, 'save', save)
    index._reconcile_at = 0
    index._recovery_budget = 12
    for _ in range(60):
        base, delta = index.scan_changes(since=base, known_workspaces=[str(workspace)])
        assert delta['reconciliation']['steps'] <= 12
        if delta['reconciliation']['phase'] == 'complete':
            break
    else:
        pytest.fail('Unchanged bounded reconciliation did not complete')
    assert not builds and not saves
    assert delta['sessionCount'] == 80
    index.close()


def test_recovery_fastpath_rechecks_symlink_workspace_referent(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(workspace, target_is_directory=True)
    path = session(home, workspace, 'saved-root', {'working_dir': str(alias), 'bundle': 'anchors'})
    index = NativeHistory(home, watch=True, cache_path=tmp_path / 'cache.sqlite3')
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    def recover(base):
        index._reconcile_at = 0
        observed = []
        for _ in range(40):
            base, delta = index.scan_changes(since=base, known_workspaces=[])
            observed.extend(delta['sessions'])
            if delta['reconciliation']['phase'] == 'complete':
                return base, observed
        pytest.fail('Workspace recovery did not complete')
    base, _ = recover(base)
    base, _ = recover(base)
    workspace.rmdir()
    base, observed = recover(base)
    assert any(not row['canResume'] for row in observed)
    assert not index._projects[path.parent.parent.name]['workspace']['available']
    with sqlite3.connect(index._catalog.path) as db:
        stored = json.loads(db.execute('SELECT value FROM native_rows').fetchone()[0])
    assert not stored['canResume']
    workspace.mkdir()
    _, observed = recover(base)
    assert any(row['canResume'] for row in observed)
    index.close()


def test_registering_resolved_workspace_does_not_rebuild_unchanged_rows(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    session(home, workspace, 'saved-root', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    builds = []
    original = index._scan_project_steps
    def build(*args, **kwargs):
        builds.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(index, '_scan_project_steps', build)
    index._reconcile_at = 0
    for _ in range(12):
        base, delta = index.scan_changes(since=base, known_workspaces=[str(workspace)])
        if delta['reconciliation']['phase'] == 'complete':
            break
    assert not builds
    assert not delta['sessions'] and delta['sessionCount'] == 1
    index.close()


def test_removing_only_registered_workspace_resolver_rebuilds_rows(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'saved-root', {'bundle': 'anchors'})
    index = NativeHistory(home, watch=True, cache_path=tmp_path / 'cache.sqlite3')
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True, known_workspaces=[str(workspace)])
    index._reconcile_at = 0
    for _ in range(12):
        base, delta = index.scan_changes(since=base, known_workspaces=[str(workspace)])
        if delta['reconciliation']['phase'] == 'complete':
            break
    index._reconcile_at = 0
    observed = []
    for _ in range(12):
        base, delta = index.scan_changes(since=base, known_workspaces=[])
        observed.extend(delta['sessions'])
        if delta['reconciliation']['phase'] == 'complete':
            break
    assert any(row['workspace'] is None and not row['canResume'] for row in observed)
    assert index._projects[path.parent.parent.name]['workspace']['path'] is None
    assert {'kind': 'unresolved-workspace', 'nativeProject': path.parent.parent.name} in delta['issues']
    with sqlite3.connect(index._catalog.path) as db:
        stored = json.loads(db.execute('SELECT value FROM native_rows').fetchone()[0])
    assert stored['workspace'] is None and not stored['canResume']
    index.close()


def test_due_recovery_does_not_make_one_dirty_session_probe_whole_project(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}',
                     {'working_dir': str(workspace), 'bundle': 'anchors'}) for n in range(160)]
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    index._recovery_budget = 12
    index._reconcile_at = 0
    probes = []
    original = index._native_metadata
    monkeypatch.setattr(index, '_native_metadata',
                        lambda path, *args: (probes.append(path), original(path, *args))[1])
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'saved-007', 'transcript')}))
    with (paths[7] / 'transcript.jsonl').open('a') as source:
        source.write('changed\n')
    _, delta = index.scan_changes(since=base)
    assert len(probes) <= 13
    assert delta['reconciliation']['steps'] <= 12
    assert any(row['nativeIdentity'] == 'saved-007' for row in delta['sessions'])


@pytest.mark.parametrize('ancestor', ['project', 'sessions'])
def test_substituted_ancestors_never_admit_outside_reads(tmp_path, monkeypatch, ancestor):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, initial = index.scan_changes(force=True)
    project = saved.parent.parent
    substituted = project if ancestor == 'project' else saved.parent
    retained = substituted.with_name(substituted.name + '-retained')
    substituted.rename(retained)
    outside = tmp_path / 'outside'
    outside.mkdir()
    external = outside / 'sessions' if ancestor == 'project' else outside
    external.mkdir(exist_ok=True)
    (external / 'saved').mkdir()
    write_json(external / 'saved' / 'metadata.json', {'working_dir': str(workspace), 'name': 'Outside'})
    (external / 'saved' / 'transcript.jsonl').write_text('must not read')
    substituted.symlink_to(outside, target_is_directory=True)
    probes = []
    original = index._native_metadata
    monkeypatch.setattr(index, '_native_metadata',
                        lambda path, *args: (probes.append(path.resolve()), original(path, *args))[1])
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'saved', 'transcript')}))
    _, delta = index.scan_changes(since=base)
    assert all(not path.is_relative_to(outside) for path in probes)
    assert not any(row['title'] == 'Outside' for row in delta['sessions'])
    assert any(issue['kind'] == 'unreadable' for issue in delta['issues'])


def test_recovery_revalidates_leaf_after_suspension(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    index._recovery_budget = 3
    index._reconcile_at = 0
    base, _ = index.scan_changes(since=base)  # Suspended before the session read.
    outside = tmp_path / 'outside'
    outside.mkdir()
    write_json(outside / 'metadata.json', {'working_dir': str(workspace), 'name': 'Outside'})
    (outside / 'transcript.jsonl').write_text('outside')
    saved.rename(saved.with_name('saved-retained'))
    saved.symlink_to(outside, target_is_directory=True)
    probes = []
    original = index._native_metadata
    monkeypatch.setattr(index, '_native_metadata',
                        lambda path, *args: (probes.append(path.resolve()), original(path, *args))[1])
    for _ in range(8):
        base, _ = index.scan_changes(since=base)
    assert all(not path.is_relative_to(outside) for path in probes)
    index.close()


def test_hot_session_does_not_starve_missed_cold_recovery(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}',
                     {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Original'})
             for n in range(160)]
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    write_json(paths[-1] / 'metadata.json',
               {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Cold recovered'})
    index._recovery_budget = 12
    index._reconcile_at = 0
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'saved-000', 'transcript')}))
    seen = []
    for number in range(48):
        with (paths[0] / 'transcript.jsonl').open('a') as source:
            source.write(f'hot {number}\n')
        base, delta = index.scan_changes(since=base)
        seen.extend(delta['sessions'])
        if any(row['title'] == 'Cold recovered' for row in seen):
            break
    assert any(row['title'] == 'Cold recovered' for row in seen)
    index.close()


def test_suspended_recovery_cannot_restore_confirmed_removed_project(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    cache = tmp_path / 'cache.sqlite3'
    index = NativeHistory(home, watch=True, cache_path=cache)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    index._recovery_budget = 3
    index._reconcile_at = 0
    base, _ = index.scan_changes(since=base)
    project = saved.parent.parent
    project.rename(tmp_path / 'retained-project')
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'saved', 'transcript')}))
    removed = []
    for _ in range(5):
        base, delta = index.scan_changes(since=base)
        removed.extend(delta['removed'])
        assert project.name not in index._projects
        monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    assert (project.name, 'saved') in removed
    with sqlite3.connect(cache) as db:
        assert db.execute('SELECT count(*) FROM native_rows').fetchone()[0] == 0
    index.close()


def test_close_waits_for_inflight_recovery_slice(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    index = NativeHistory(tmp_path / 'native', watch=True)
    entered, release = threading.Event(), threading.Event()
    def blocked_recovery(known):
        entered.set()
        assert release.wait(3)
        yield
    monkeypatch.setattr(index, '_recover_projects', blocked_recovery)
    def scan():
        with index._lock:
            index._recovery_slice({})
    with ThreadPoolExecutor(max_workers=2) as workers:
        active = workers.submit(scan)
        assert entered.wait(3)
        closing = workers.submit(index.close)
        assert not closing.done()
        release.set()
        active.result(timeout=3)
        closing.result(timeout=3)
    assert index._recovery is None


def test_cached_load_can_cooperatively_stop_without_partial_snapshot(tmp_path, monkeypatch):
    from amplifier_web.native_catalog import NativeCatalog
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    for number in range(30):
        session(home, workspace, f'saved-{number}',
                {'working_dir': str(workspace), 'bundle': 'anchors'})
    cache = tmp_path / 'cache.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    first.scan_changes()
    first.close()
    restarted = NativeHistory(home, cache_path=cache)
    original = NativeCatalog._check_running
    probes = []
    def interrupted(catalog):
        probes.append(True)
        if len(probes) == 5:
            restarted._closing.set()
        original(catalog)
    monkeypatch.setattr(NativeCatalog, '_check_running', interrupted)
    with pytest.raises(InterruptedError):
        restarted.scan_changes()
    assert restarted._snapshot_revision is None
    assert restarted._projects == {}
    with sqlite3.connect(cache) as db:
        assert db.execute('SELECT count(*) FROM native_rows').fetchone()[0] == 30
    restarted.close()


@pytest.mark.parametrize('failure', [OSError, sqlite3.OperationalError])
async def test_refresh_error_publication_failure_does_not_terminate_discovery(tmp_path, app_factory, monkeypatch, failure):
    workspace = tmp_path / 'cli'
    native_session(workspace, 'saved-root')
    app = app_factory()
    publish = app._publish
    attempts = []
    def refused(**kwargs):
        attempts.append(kwargs)
        raise failure('Presentation storage unavailable')
    monkeypatch.setattr(app, '_publish', refused)
    # Both the ordinary save and the error report encounter the same disk fault.
    await app.history.refresh(force=False)
    assert len(attempts) == 2
    assert app.history._native_revision is None
    assert app.state['sharedHistory']['error']
    monkeypatch.setattr(app, '_publish', publish)
    await app.history.refresh(force=False)
    assert app.history._native_revision is not None
    assert app.state['sharedHistory']['error'] is None
    assert app.directory.resolve('saved-root')['nativeIdentity'] == 'saved-root'
    assert not any(row.get('nativeIdentity') == 'saved-root' for row in app.state['sessions'])


async def test_actual_save_failure_retains_discovery_dirty_union(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'cli'
    native_session(workspace, 'saved-root')
    app = app_factory()
    original = app._save
    attempts = []
    def refused(**kwargs):
        attempts.append(kwargs)
        raise sqlite3.OperationalError('Presentation database is locked')
    monkeypatch.setattr(app, '_save', refused)
    try:
        await app.history.refresh(force=False)
    finally:
        monkeypatch.setattr(app, '_save', original)
    assert len(attempts) == 2
    assert app.history._native_revision is None
    assert app._progress_dirty and app._progress_scope_known
    assert app._progress_session_ids is None  # Unknown full scope survives both errors.
    assert app.state['sharedHistory']['error']
    await app.history.refresh(force=False)
    assert app.history._native_revision is not None
    assert not app._progress_dirty
    assert app.state['sharedHistory']['error'] is None
    assert app.directory.resolve('saved-root')['nativeIdentity'] == 'saved-root'
    assert not any(row.get('nativeIdentity') == 'saved-root' for row in app.state['sessions'])


async def test_recovery_slice_counters_do_not_publish_unchanged_library(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'cli'
    for number in range(160):
        native_session(workspace, f'saved-{number:03d}')
    app = app_factory()
    await app.history.refresh()
    await app.history.refresh(force=False)
    index = app.history.index
    index._recovery_budget = 12
    index._reconcile_at = 0
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    publishes = []
    original = app._publish
    def observed(**kwargs):
        publishes.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(app, '_publish', observed)
    for _ in range(60):
        await app.history.refresh(force=False)
        if index._recovery_status['phase'] == 'complete':
            break
    assert index._recovery_status['phase'] == 'complete'
    assert len(publishes) <= 2  # Running and completed freshness, not slice counters.


def test_delta_is_detached_retryable_and_unknown_token_resets(tmp_path):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, 'saved-' + str(n), {'working_dir': str(workspace), 'bundle': 'anchors'})
             for n in range(80)]
    index = NativeHistory(home)
    index.scan_changes()  # Learn the workspace before measuring a change.
    base, initial = index.scan_changes()
    with (paths[7] / 'transcript.jsonl').open('a') as file:
        file.write('one external append\n')
    token, delta = index.scan_changes(since=base)
    assert not delta['reset'] and delta['base'] is base
    assert [row['nativeIdentity'] for row in delta['sessions']] == ['saved-7']
    delta['sessions'][0]['transcriptRevision'][0] = -1
    same_token, retry = index.scan_changes(since=base)
    assert same_token is token and len(retry['sessions']) == 1
    assert retry['sessions'][0]['transcriptRevision'][0] != -1
    _, quiet = index.scan_changes(since=token)
    assert quiet['sessions'] == [] and quiet['workspaces'] == []
    _, reset = index.scan_changes(since=object())
    assert reset['reset'] and len(reset['sessions']) == 80
    public = index.scan()
    assert len(public['sessions']) == 80
    public['sessions'][0]['title'] = 'Detached'
    assert index.scan()['sessions'][0]['title'] != 'Detached'


def test_corrupt_cache_is_reported_without_replacing_source_or_cache(tmp_path):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    cache = tmp_path / 'broken.sqlite3'
    cache.write_bytes(b'not a database')
    index = NativeHistory(home, cache_path=cache)
    _, delta = index.scan_changes()
    assert len(delta['sessions']) == 1
    assert {'kind': 'unavailable-catalog-cache'} in delta['issues']
    assert cache.read_bytes() == b'not a database'
    assert (path / 'transcript.jsonl').read_text() == 'private conversation body\n'

@pytest.mark.parametrize('workspace_value', [
    {'path': 123}, {'path': []}, {'path': 'relative/path'}, {'available': 'yes'},
])
def test_malformed_cached_workspace_recovers_on_repeated_refresh(tmp_path, workspace_value):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    canonical = {path: path.read_bytes() for path in saved.iterdir() if path.is_file()}
    cache = tmp_path / 'cache.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    first.scan_changes()
    first.close()
    with sqlite3.connect(cache) as db:
        name, raw = db.execute('SELECT project,value FROM native_projects').fetchone()
        value = json.loads(raw)
        value['workspace'].update(workspace_value)
        db.execute('UPDATE native_projects SET value=? WHERE project=?', (json.dumps(value), name))
    rejected = cache.read_bytes()
    restarted = NativeHistory(home, cache_path=cache)
    try:
        for force in (False, True):
            _, delta = restarted.scan_changes(force=force)
            assert [row['nativeIdentity'] for row in delta['sessions']] == ['saved']
            assert {'kind': 'unavailable-catalog-cache'} in delta['issues']
            assert cache.read_bytes() == rejected
            assert all(path.read_bytes() == data for path, data in canonical.items())
    finally:
        restarted.close()

def test_transcript_notifications_probe_only_affected_session_on_repeated_changes(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, 'saved-' + str(n), {'working_dir': str(workspace), 'bundle': 'anchors'})
             for n in range(80)]
    index = NativeHistory(home)
    index.scan_changes()
    token, _ = index.scan_changes()
    slug = project_slug(workspace)
    index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(slug, 'saved-7', 'transcript')}))
    reads = []
    original = index._native_metadata

    def read(directory, *args):
        reads.append(directory.name)
        return original(directory, *args)

    monkeypatch.setattr(index, '_native_metadata', read)
    for number in range(2):
        with (paths[7] / 'transcript.jsonl').open('a') as file:
            file.write('append ' + str(number) + '\n')
        token, delta = index.scan_changes(since=token)
        assert [row['nativeIdentity'] for row in delta['sessions']] == ['saved-7']
    assert reads == ['saved-7', 'saved-7']


async def test_single_native_change_does_not_serialize_unrelated_app_views(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'cli'
    native_session(workspace, 'saved-root')
    child = native_session(workspace, 'saved-child', metadata={'parent_id': 'saved-root'})
    app = app_factory()
    await app.dispatch('session.create', {})
    await app.history.refresh()
    await app.history.refresh(force=False)
    child_row = app._session('saved-child', hydrate=False)
    await app.history.refresh(force=False)  # Reconcile the explicitly admitted owner.
    app.projections.sessions(app.state)
    original_save = app._save
    scopes = []

    def save(*, session_ids=None):
        scopes.append(session_ids if session_ids is not None else app._publish_save_scope)
        return original_save(session_ids=session_ids)

    monkeypatch.setattr(app, '_save', save)
    from amplifier_web.browser_state import SessionIndex
    original_init = SessionIndex.__init__
    builds = []

    def build(self, state):
        builds.append(len(state['sessions']))
        original_init(self, state)

    monkeypatch.setattr(SessionIndex, '__init__', build)
    from amplifier_web.session_projection import view_path
    own = next(row for row in app.state['sessions'] if not row.get('historyManaged'))
    own_view = view_path(app.data_dir, own)
    before = own_view.read_bytes(), own_view.stat().st_mtime_ns
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, {project_slug(workspace)}))
    with (child / 'transcript.jsonl').open('a') as file:
        file.write(json.dumps({'role': 'assistant', 'content': 'One new answer'}) + '\n')
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert scopes == [{child_row['id']}]
    assert builds == []
    assert (own_view.read_bytes(), own_view.stat().st_mtime_ns) == before
    assert child_row['nativeRevision'][1] == (child / 'transcript.jsonl').stat().st_size
    assert app.history.index._catalog.count() >= 2


async def test_scoped_history_commit_does_not_drop_pending_runtime_progress(tmp_path, app_factory, monkeypatch):
    from amplifier_web.session_projection import view_path
    workspace = tmp_path / 'cli'
    saved = native_session(workspace, 'saved-root')
    app = app_factory()
    await app.dispatch('session.create', {})
    await app.history.refresh()
    await app.history.refresh(force=False)
    own = next(row for row in app.state['sessions'] if not row.get('historyManaged'))
    own['draft'] = 'Unrelated progress must reach the durable view'
    app._progress_dirty = True
    # A later labelled writer cannot narrow an earlier unknown mutation.
    native_id = app._session('saved-root', hydrate=False)['id']
    await app.on_runtime_event('assistant.delta', {'sessionId': native_id, 'text': 'later delta'})
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, {project_slug(workspace)}))
    with (saved / 'transcript.jsonl').open('a') as file:
        file.write(json.dumps({'role': 'assistant', 'content': 'A new answer'}) + '\n')
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert json.loads(view_path(app.data_dir, own).read_text())['draft'] == own['draft']
    assert app._progress_dirty is False


@pytest.mark.parametrize('change', ['missing', 'corrupt', 'worker', 'internal'])
def test_cached_root_cannot_authorize_resume_without_fresh_metadata(tmp_path, change):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    metadata = {'working_dir': str(workspace), 'bundle': 'anchors', 'parent_id': None}
    saved = session(home, workspace, 'saved', metadata)
    cache = tmp_path / 'app' / 'cache.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    source = first.scan()['sessions'][0]
    if change == 'missing':
        (saved / 'metadata.json').unlink()
    elif change == 'corrupt':
        (saved / 'metadata.json').write_text('{broken')
    else:
        write_json(saved / 'metadata.json', {**metadata,
                   'parent_id': 'other' if change == 'worker' else None,
                   'session_visibility': 'internal' if change == 'internal' else 'chat'})
    second = NativeHistory(home, cache_path=cache)
    second.scan_changes()
    before = {path: path.read_bytes() for path in saved.iterdir() if path.is_file()}
    with pytest.raises(ValueError):
        second.validate_resume(source)
    assert all(path.read_bytes() == data for path, data in before.items())


def test_transcript_only_notification_does_not_read_symlink_substitution(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home)
    index.scan_changes()
    token, _ = index.scan_changes()
    saved.rename(saved.parent / '.original')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'metadata.json').write_text('{"name":"Outside"}')
    saved.symlink_to(outside, target_is_directory=True)
    index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project_slug(workspace), 'saved', 'transcript')}))
    opened = []
    original = index._native_metadata
    monkeypatch.setattr(index, '_native_metadata', lambda directory, *args:
                        (opened.append(directory), original(directory, *args))[1])
    _, delta = index.scan_changes(since=token)
    assert saved not in opened
    assert delta['sessions'] == []
    assert delta['removed'] == [(project_slug(workspace), 'saved')]


async def test_local_commit_during_marker_read_rebuilds_aliases(tmp_path, app_factory, monkeypatch):
    import threading
    import asyncio
    import amplifier_web.automatic_history as module
    workspace = tmp_path / 'cli'
    saved = native_session(workspace, 'saved-root')
    app = app_factory()
    await app.history.refresh()
    await app.history.refresh(force=False)
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, {project_slug(workspace)}))
    started, release = threading.Event(), threading.Event()
    original = module.catalog_locations
    calls = []

    def paused(snapshot):
        calls.append(len(snapshot['sessions']))
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return original(snapshot)

    monkeypatch.setattr(module, 'catalog_locations', paused)
    app.state['sharedHistory']['error'] = 'Earlier scan failed'
    refresh = asyncio.create_task(app.history.refresh(force=False))
    assert await asyncio.to_thread(started.wait, 5)
    async with app.lock:
        own = app._new_session({'workspace': str(workspace)})
        own.update(runtimeSessionId='new-root', nativeIdentity='new-root',
                   nativeProject=project_slug(workspace))
        app.state['sessions'].append(own)
        app._publish()
    native_session(workspace, 'new-root')
    release.set()
    await refresh
    await app.history.refresh(force=False)
    records = [row for row in app.state['sessions'] if row.get('nativeIdentity') == 'new-root']
    assert len(records) == 1 and records[0] is own
    assert app.state['sharedHistory']['error'] is None


async def test_tombstone_totals_are_complete_and_removal_is_published(tmp_path, app_factory, monkeypatch):
    import shutil
    first, second = tmp_path / 'first', tmp_path / 'second'
    saved = native_session(first, 'first-root')
    native_session(second, 'second-root')
    app = app_factory()
    await app.history.refresh()
    await app.history.refresh(force=False)
    monkeypatch.setattr('amplifier_web.managed_deletion.tombstones',
                        lambda db: [{'project': 'already-deleted'}])
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, {project_slug(first)}))
    with (saved / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'assistant', 'content': 'increment'}) + '\n')
    await app.history.refresh(force=False)
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['sessionCount'] == 2
    revision = app.state['revision']
    shutil.rmtree(saved.parent.parent)
    # A prior cooperative scan may still be suspended inside this project.
    # Ordinary refresh advances bounded recovery; await the confirmed removal
    # rather than requiring it in whichever single slice runs next.
    for _ in range(64):
        await app.history.refresh(force=False)
        assert app.state['sharedHistory']['error'] is None
        if app.state['sharedHistory']['sessionCount'] == 1:
            break
    assert app.state['sharedHistory']['sessionCount'] == 1
    assert app.state['revision'] > revision
    durable = load_saved_state(app.db)
    assert durable['sharedHistory']['sessionCount'] == 1


async def test_removed_project_during_pending_recovery_publishes_eventual_absence(
        tmp_path, app_factory, monkeypatch):
    import shutil

    first, second = tmp_path / 'first', tmp_path / 'second'
    saved = native_session(first, 'first-root')
    native_session(second, 'second-root')
    app = app_factory()
    index = app.history.index
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    for _ in range(64):
        await app.history.refresh(force=False)
        if app.state['sharedHistory']['reconciliation']['phase'] == 'complete':
            break
    assert app.state['sharedHistory']['sessionCount'] == 2
    assert app.state['sharedHistory']['reconciliation']['phase'] == 'complete'

    # Suspend warm discovery immediately after it observes this project's
    # session entry. Removing the project then reproduces an actual race with
    # the cooperative reader, without relying on wall time or machine load.
    observed = []
    directories = index._recovery_directories
    def observe(path):
        for directory in directories(path):
            if path == saved.parent:
                observed.append(directory)
            yield directory
    monkeypatch.setattr(index, '_recovery_directories', observe)
    index._recovery_budget = 1
    index._reconcile_at = 0
    for _ in range(64):
        await app.history.refresh(force=False)
        if observed:
            break
    assert observed == [saved]
    assert index._recovery is not None
    revision = app.state['revision']
    shutil.rmtree(saved.parent.parent)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {project_slug(first)}))

    for _ in range(64):
        await app.history.refresh(force=False)
        history = app.state['sharedHistory']
        assert history['error'] is None
        if history['sessionCount'] == 1:
            break
        # A suspended reader can retain an uncertain row for this slice, but
        # must expose that uncertainty until fresh absence proof retires it.
        assert any(issue.get('nativeProject') == project_slug(first)
                   for issue in history['issues'])
    assert app.state['sharedHistory']['sessionCount'] == 1
    assert app.state['revision'] > revision
    assert load_saved_state(app.db)['sharedHistory']['sessionCount'] == 1
    assert app.directory.resolve('first-root') is None
    assert app.directory.resolve('second-root') is not None
    assert app.state['sharedHistory']['issues'] == []
    assert app.runtime.started == [] and app.runtime.sent == []


async def test_empty_recovery_slice_avoids_rewalking_registered_workspace_catalog(
        tmp_path, app_factory, monkeypatch):
    from amplifier_web.state_records import load as load_saved_state

    native_session(tmp_path / 'cli', 'saved-root')
    app = app_factory()
    await app.history.refresh()
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, set()))
    # These independent registrations are navigation metadata, not a request to
    # inspect their histories. Every pass still samples at most64 real folders.
    path = app.state['settings']['workspace']
    app.state['workspaces'].extend({'id': f'dormant-{n}', 'name': f'Folder{n}',
                                   'path': path, 'available': True} for n in range(2048))
    app._publish()
    await app.history.refresh(force=False)  # Reconcile the real local mutation.

    class CountedWorkspaces(list):
        visits = 0
        def __iter__(self):
            self.visits += 1
            return super().__iter__()

    workspaces = CountedWorkspaces(app.state['workspaces'])
    app.state['workspaces'] = workspaces
    revision = app.state['revision']
    saved = load_saved_state(app.db)
    before = app.performance_diagnostics.snapshot()['counters'].get('catalog.merge.skipped', 0)
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    # One compact input pass is allowed; additional maps/overlay passes over all
    # registered folders must be reserved for a changed catalog or local overlay.
    assert workspaces.visits == 1
    assert app.state['revision'] == revision
    assert load_saved_state(app.db) == saved
    assert app.performance_diagnostics.snapshot()['counters']['catalog.merge.skipped'] == before + 1


async def test_empty_catalog_delta_still_refreshes_selected_transcript(
        tmp_path, app_factory, monkeypatch):
    saved = native_session(tmp_path / 'cli', 'saved-root')
    app = app_factory()
    await app.history.refresh()
    row = app._session('saved-root', hydrate=False)
    app.state['selectedSessionId'] = row['id']
    app._publish()
    await app.history.load(row['id'])
    index = app.history.index
    index._recovery_budget = 1
    index._reconcile_at = 0
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    await app.history.refresh(force=False)
    assert index._recovery is not None
    before = app.performance_diagnostics.snapshot()['counters'].get('catalog.merge.skipped', 0)
    # Simulate a missed watcher event: no metadata delta arrives, but open chat
    # demand must still see canonical transcript changes without a new turn.
    with (saved / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'assistant', 'content': 'Saved externally'}) + '\n')
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert row['messages'][-1]['text'] == 'Saved externally'
    assert not app.runtime.started and not app.runtime.sent
    assert app.performance_diagnostics.snapshot()['counters']['catalog.merge.skipped'] == before + 1
    assert index._recovery is not None  # Open demand did not finish the cold library first.


@pytest.mark.parametrize('watching', [True, False])
def test_uncached_background_discovery_is_bounded_and_eventually_complete(
        tmp_path, monkeypatch, watching):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}',
                     {'working_dir': str(workspace), 'bundle': 'anchors'}) for n in range(80)]
    originals = [(path / 'transcript.jsonl').read_bytes() for path in paths]
    index = NativeHistory(home, watch=True, cache_path=tmp_path / 'new.sqlite3')
    monkeypatch.setattr(index, '_invalidations', lambda: (watching, set()))
    def eager(*args, **kwargs):
        pytest.fail('Cold startup attempted an eager project scan')
    monkeypatch.setattr(index, '_scan_project', eager)
    index._recovery_budget = 12
    token, first = index.scan_changes()
    assert first['reset'] and first['reconciliation']['phase'] == 'running'
    assert first['sessionCount'] < len(paths) and first['metadataReads'] <= 12
    assert first['notificationsReady'] is watching
    observed = {row['nativeIdentity'] for row in first['sessions']}
    for _ in range(100):
        token, delta = index.scan_changes(since=token)
        assert delta['reconciliation']['steps'] <= 12
        observed.update(row['nativeIdentity'] for row in delta['sessions'])
        if delta['reconciliation']['phase'] == 'complete':
            break
    else:
        pytest.fail('Incremental startup did not discover the cold library')
    assert len(observed) == delta['sessionCount'] == len(paths)
    assert [(path / 'transcript.jsonl').read_bytes() for path in paths] == originals
    expected_delay = 900 if watching else 60
    assert expected_delay - 1 < index._reconcile_at - time.monotonic() <= expected_delay
    index.close()


def test_lost_watch_shortens_recovery_deadline_and_discovers_unregistered_chat(
        tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    session(home, workspace, 'saved-root', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, watch=True)
    clock = [1000.0]
    monkeypatch.setattr('amplifier_web.native_history.time.monotonic', lambda: clock[0])
    watching = [True]
    monkeypatch.setattr(index, '_invalidations', lambda: (watching[0], set()))
    token, first = index.scan_changes()
    assert first['reconciliation']['phase'] == 'complete'
    assert index._reconcile_at == 1900
    other = tmp_path / 'unregistered'
    other.mkdir()
    session(home, other, 'external-root', {'working_dir': str(other), 'bundle': 'anchors'})
    watching[0] = False
    token, missing = index.scan_changes(since=token)
    assert index._reconcile_at == 1060 and missing['sessionCount'] == 1
    assert missing['reconciliation']['steps'] == 0
    clock[0] += 15
    token, _ = index.scan_changes(since=token)
    assert index._reconcile_at == 1060  # Repeated watcher failures cannot defer discovery.
    clock[0] = 1060
    token, discovered = index.scan_changes(since=token)
    assert {row['nativeIdentity'] for row in discovered['sessions']} == {'external-root'}
    assert discovered['sessionCount'] == 2 and discovered['reconciliation']['phase'] == 'complete'
    assert index._reconcile_at == 1120
    index.close()


@pytest.mark.parametrize('change', ['availability', 'error', 'loading', 'issues', 'phase', 'counts', 'tombstone'])
async def test_empty_delta_preserves_catalog_availability_and_public_facts(
        tmp_path, app_factory, monkeypatch, change):
    native = tmp_path / 'cli'
    native_session(native, 'saved-root')
    app = app_factory()
    await app.history.refresh()
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, set()))
    await app.history.refresh(force=False)
    shared = app.state['sharedHistory']
    expected_count = shared['sessionCount']
    if change == 'availability':
        native.rmdir()
    elif change == 'error':
        shared['error'] = 'Earlier scan failed'
    elif change == 'loading':
        shared['loading'] = True
    elif change == 'issues':
        shared.update(issues=[{'kind': 'old'}], issueCount=1)
    elif change == 'phase':
        shared['reconciliation'] = {'phase': 'old', 'completedAt': None}
    elif change == 'counts':
        shared['sessionCount'] = -1
    else:
        monkeypatch.setattr('amplifier_web.managed_deletion.tombstones',
                            lambda db: [{'project': project_slug(native)}])
        expected_count = 0
    revision = app.state['revision']
    before = app.performance_diagnostics.snapshot()['counters'].get('catalog.merge.skipped', 0)
    await app.history.refresh(force=False)
    assert app.state['revision'] > revision
    assert app.performance_diagnostics.snapshot()['counters'].get('catalog.merge.skipped', 0) == before
    assert shared['loading'] is False and shared['error'] is None
    assert shared['issues'] == [] and shared['issueCount'] == 0
    assert shared['reconciliation']['phase'] != 'old'
    assert shared['sessionCount'] == expected_count
    if change == 'availability':
        workspace = next(item for item in app.state['workspaces'] if item.get('path') == str(native))
        assert workspace['available'] is False
        assert app.directory.resolve('saved-root')['nativeIdentity'] == 'saved-root'
    assert load_saved_state(app.db)['sharedHistory'] == shared
