"""Persistent directory queries never require residency of dormant metadata."""
import json
import sqlite3
import time
import uuid

import pytest

from amplifier_web.native_catalog import NativeCatalog, CatalogRows, SCALAR_COLUMNS, row_scalars
from amplifier_web.native_history import NativeHistory
from amplifier_web.session_files import project_slug
from test_native_history import session, write_json


def seed(catalog, workspace, number=27000):
    project = project_slug(workspace)
    wid = uuid.uuid5(uuid.NAMESPACE_URL, str(workspace)).hex
    summary = {'id': wid, 'nativeProject': project, 'path': str(workspace), 'name': workspace.name,
               'available': True, 'sessionCount': number, 'workerSessionCount': 0, 'internalSessionCount': 0}
    columns = ','.join(('namespace', 'project', 'identity', 'value', *SCALAR_COLUMNS))
    def rows():
        for position in range(number):
            identity = f'saved-{position:05d}'
            row = {'id': uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{project}/{identity}').hex,
                   'nativeProject': project, 'nativeIdentity': identity, 'workspaceId': wid,
                   'workspace': str(workspace), 'title': 'Straße ' + str(position),
                   'description': 'Kept history', 'sessionKind': 'root', 'parentId': None,
                   'createdAt': position, 'updatedAt': position, 'recentActivityAt': position}
            yield (catalog.namespace, project, identity, json.dumps(row), *row_scalars(row, summary))
    with catalog.connect() as db:
        db.execute('INSERT INTO native_projects VALUES(?,?,?)', (catalog.namespace, project, json.dumps({'workspace': summary})))
        db.executemany('INSERT INTO native_rows (' + columns + ') VALUES(' + ','.join('?' for _ in range(4 + len(SCALAR_COLUMNS))) + ')', rows())
    return project, summary


@pytest.mark.parametrize('count', [3, 27000])
def test_exact_identity_counts_use_project_scope_and_do_not_decode_bodies(tmp_path, monkeypatch, count):
    from contextlib import contextmanager

    catalog = NativeCatalog(tmp_path/'catalog.sqlite3', tmp_path/'native')
    project, _ = seed(catalog, tmp_path/'one', count)
    other, _ = seed(catalog, tmp_path/'two', 2)
    alias = catalog.get(project, 'saved-00000')['id']
    with catalog.connect() as db:
        db.execute("UPDATE native_rows SET kind='worker' WHERE project=? AND identity='saved-00001'", (project,))
        db.execute("UPDATE native_rows SET value='invalid body: counts must not read it'")
    instructions = []
    original = catalog.connect
    @contextmanager
    def observed():
        with original() as db:
            instructions.append(0)
            def progress():
                instructions[-1] += 1
                return instructions[-1] > 5000
            db.set_progress_handler(progress, 1)
            yield db
    monkeypatch.setattr(catalog, 'connect', observed)
    assert catalog.count_identities(project, {'saved-00000', alias, 'saved-00001', 'missing'}) == {'root':1,'worker':1}
    assert catalog.count_identities(other, {'saved-00000', alias}) == {'root':1}
    assert catalog.count_identities(project, set()) == {}
    # This also catches a scalar scan that would pass the invalid-body check.
    assert len(instructions) == 3 and max(instructions) < 1000


def test_warm_27000_directory_decodes_only_requested_rows(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', home)
    project, summary = seed(catalog, workspace)
    decoded = []
    original = NativeCatalog._row
    monkeypatch.setattr(NativeCatalog, '_row', staticmethod(lambda *args: (decoded.append(args[:2]), original(*args))[1]))
    monkeypatch.setattr(NativeCatalog, 'load', lambda *_: pytest.fail('warm directory must not fully restore cached rows/files'))
    index = NativeHistory(home, watch=True, cache_path=catalog.path, directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    index._reconcile_at = time.monotonic() + 900
    token, initial = index.scan_changes()
    assert initial['directoryReady'] and initial['reset'] and initial['sessions'] == []
    assert initial['sessionCount'] == 27000 and initial['workspaces'] == [summary]
    assert decoded == [] and index._files == {}
    assert isinstance(index._projects[project]['sessions'], CatalogRows)
    for number in range(6):
        assert index._catalog.get(project, f'saved-{number:05d}')['createdAt'] == number
    assert len(decoded) == 6
    assert index._catalog.count(query='STRASSE 269*') == 111
    assert len(decoded) == 6
    page = index._catalog.page(limit=25, query='STRASSE 269*')
    assert len(page) == 25 and len(decoded) == 31
    assert page[0]['nativeIdentity'] == 'saved-26999'
    same, quiet = index.scan_changes(since=token)
    assert same is token and quiet['sessions'] == [] and not quiet['reset']
    assert len(decoded) == 31
    index.close()


def test_native_directory_v1_migration_keeps_raw_metadata_and_scoped_aliases(tmp_path):
    home = tmp_path / 'home'
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', home)
    seed(catalog, tmp_path / 'one', 2)
    seed(catalog, tmp_path / 'two', 2)
    with catalog.connect() as db:
        project, identity, text = db.execute('SELECT project,identity,value FROM native_rows ORDER BY project,identity LIMIT 1').fetchone()
        named = {**json.loads(text), 'name': 'Canonical name', 'nameSource': 'manual', 'autoName': False,
                 'nameRevision': 7, 'namePolicyRevision': 9}
        db.execute('UPDATE native_rows SET value=? WHERE project=? AND identity=?', (json.dumps(named), project, identity))
        before = db.execute('SELECT project,identity,value FROM native_rows ORDER BY project,identity').fetchall()
        db.execute('UPDATE native_cache_version SET version=1')
        db.execute('UPDATE native_rows SET public_id=NULL,title_fold=NULL,hidden_id=NULL')
    migrated = NativeCatalog(catalog.path, home)
    assert migrated.count() == 4
    assert len(migrated.lookup('saved-00000')) == 2
    exact = migrated.lookup('saved-00000')[0]
    assert migrated.lookup(exact['id']) == [exact]
    with migrated.connect() as db:
        assert db.execute('SELECT project,identity,value FROM native_rows ORDER BY project,identity').fetchall() == before
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert db.execute('SELECT count(*) FROM native_rows WHERE hidden_id IS NULL').fetchone()[0] == 0
        assert db.execute('SELECT project,identity FROM native_rows ORDER BY ordinal').fetchall() == [(row[0], row[1]) for row in before]
        assert db.execute('SELECT has_name,name_source,auto_name FROM native_rows WHERE project=? AND identity=?',
                          (project, identity)).fetchone() == (1, 'manual', 0)
        assert db.execute('SELECT name_revision,name_policy_revision FROM native_rows WHERE project=? AND identity=?',
                          (project, identity)).fetchone() == (7, 9)


def test_older_compact_file_cache_lazily_refreshes_name_revisions_without_source_change(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'named', {'working_dir': str(workspace), 'name': 'Canonical name',
        'name_revision': 7, 'name_policy_revision': 9, 'name_source': 'manual', 'name_auto': True,
        'name_auto_revision': 7})
    cache = tmp_path / 'catalog.sqlite3'
    initial = NativeHistory(home, cache_path=cache)
    initial.scan()
    catalog = initial._catalog
    with catalog.connect() as db:
        key = str(path / 'metadata.json')
        raw = json.loads(db.execute('SELECT value FROM native_files WHERE namespace=? AND path=?',
                                   (catalog.namespace, key)).fetchone()[0])
        for field in ('_nativeFieldsVersion', 'name_revision', 'name_policy_revision', 'name_auto', 'name_auto_revision'):
            raw[1].pop(field, None)
        db.execute('UPDATE native_files SET value=? WHERE namespace=? AND path=?',
                   (json.dumps(raw), catalog.namespace, key))
    initial.close()
    before = (path / 'metadata.json').read_bytes()
    index = NativeHistory(home, watch=True, cache_path=cache, directory=True)
    project = project_slug(workspace)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    index._reconcile_at = time.monotonic() + 900
    token, warm = index.scan_changes()
    assert warm['metadataReads'] == 0
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project, 'named', 'metadata')}))
    _, changed = index.scan_changes(since=token)
    assert changed['metadataReads'] == 1
    current = index._catalog.get(project, 'named')
    assert current['nameRevision'] == 7 and current['namePolicyRevision'] == 9
    assert current['autoName'] is True
    assert (path / 'metadata.json').read_bytes() == before
    index.close()


def test_directory_targeted_change_and_retry_dont_enumerate_project(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n}', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Original'}) for n in range(8)]
    cache = tmp_path / 'catalog.sqlite3'
    old = NativeHistory(home, cache_path=cache)
    old.scan(); old.close()
    index = NativeHistory(home, watch=True, cache_path=cache, directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    index._reconcile_at = time.monotonic() + 900
    token, _ = index.scan_changes()
    project = project_slug(workspace)
    write_json(paths[2] / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Updated'})
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project, 'saved-2', 'metadata')}))
    monkeypatch.setattr(CatalogRows, '__iter__', lambda *_: pytest.fail('targeted metadata change must use point lookup'))
    changed, delta = index.scan_changes(since=token)
    assert not delta['reset'] and [row['title'] for row in delta['sessions']] == ['Updated']
    assert delta['sessionCount'] == 8
    assert index._catalog.get(project, 'saved-2')['title'] == 'Updated'
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    retried, retry = index.scan_changes(since=token)
    assert retried is changed and retry['sessions'] == delta['sessions']
    assert index.scan_changes(since=changed)[1]['sessions'] == []
    index.close()


def test_directory_recovery_is_bounded_and_does_not_retain_completed_rows(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Original'}) for n in range(40)]
    cache = tmp_path / 'catalog.sqlite3'
    initial = NativeHistory(home, cache_path=cache); initial.scan(); initial.close()
    write_json(paths[-1] / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Missed change'})
    index = NativeHistory(home, watch=True, cache_path=cache, directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    index._recovery_budget = 4
    token = None
    for _ in range(100):
        token, delta = index.scan_changes(since=token)
        assert delta['reconciliation']['steps'] <= 4
        if delta['reconciliation']['phase'] == 'complete':
            break
    else:
        pytest.fail('bounded discovery did not complete')
    project = project_slug(workspace)
    assert index._catalog.get(project, 'saved-039')['title'] == 'Missed change'
    assert isinstance(index._projects[project]['sessions'], CatalogRows)
    assert len(index._files) <= 1024
    assert not index._directory_recovery_changes
    assert len(json.dumps(index._project_inputs[project][0])) < 4096
    index.close()


def test_wal_directory_reader_keeps_snapshot_while_discovery_updates(tmp_path):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', home)
    project, _ = seed(catalog, workspace, 2)
    reader = sqlite3.connect(f'file:{catalog.path}?mode=ro', uri=True)
    try:
        reader.execute('BEGIN')
        assert reader.execute('SELECT count(*) FROM native_rows').fetchone()[0] == 2
        with catalog.connect() as db:
            db.execute('DELETE FROM native_rows WHERE identity=?', ('saved-00000',))
        assert reader.execute('SELECT count(*) FROM native_rows').fetchone()[0] == 2
        assert catalog.count(project=project) == 1
    finally:
        reader.close()


def test_directory_recovery_preserves_hot_updates_while_finding_missed_row(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Original'}) for n in range(40)]
    cache = tmp_path / 'catalog.sqlite3'
    initial = NativeHistory(home, cache_path=cache); initial.scan(); initial.close()
    index = NativeHistory(home, watch=True, cache_path=cache, directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    index._reconcile_at = time.monotonic() + 900
    token, _ = index.scan_changes()
    write_json(paths[-1] / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Cold found'})
    index._reconcile_at = 0
    index._recovery_budget = 4
    project = project_slug(workspace)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project, 'saved-000', 'metadata')}))
    for number in range(100):
        name = 'Hot ' + str(number)
        write_json(paths[0] / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'work', 'name': name})
        token, _ = index.scan_changes(since=token)
        assert index._catalog.get(project, 'saved-000')['title'] == name
        if index._recovery_status['phase'] == 'complete':
            break
    else:
        pytest.fail('hot target starved bounded recovery')
    assert index._catalog.get(project, 'saved-039')['title'] == 'Cold found'
    index.close()


def test_failed_directory_write_retries_without_losing_new_metadata(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Before'})
    cache = tmp_path / 'catalog.sqlite3'
    initial = NativeHistory(home, cache_path=cache); initial.scan(); initial.close()
    index = NativeHistory(home, watch=True, cache_path=cache, directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    index._reconcile_at = time.monotonic() + 900
    token, _ = index.scan_changes()
    before = index._catalog.generations()
    def blocked(*args):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(index._catalog, 'save', blocked)
    write_json(path / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'After'})
    project = project_slug(workspace)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project, 'saved', 'metadata')}))
    token, failed = index.scan_changes(since=token)
    assert not failed['directoryReady'] and {'kind': 'unavailable-catalog-cache'} in failed['issues']
    assert NativeCatalog(cache, home).get(project, 'saved')['title'] == 'Before'
    assert NativeCatalog(cache, home).generations() == before
    index._cache_retry_at = 0
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    _, recovered = index.scan_changes(since=token)
    assert recovered['directoryReady'] and index._catalog.get(project, 'saved')['title'] == 'After'
    assert index._catalog.generations()['revision'] == before['revision'] + 1
    assert not index._directory_unsaved
    index.close()


def test_native_observation_generations_ignore_transcript_only_and_activity_metadata(tmp_path):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', home)
    project, workspace_row = seed(catalog, workspace, 1)
    original = catalog.get(project, 'saved-00000')
    old = {project: {'workspace': workspace_row, 'sessions': [original]}}
    updated = {**original, 'transcriptRevision': [123, 456], 'recentActivityAt': 22, 'turnCount': 5,
               'bundle': 'work', 'description': 'A new description', 'sessionPurpose': 'background'}
    new = {project: {'workspace': workspace_row, 'sessions': [updated]}}
    catalog.save(new, old, {}, ())
    assert catalog.generations() == {'revision': 1, 'observation_sessions': 0, 'observation_membership': 0, 'navigation': 1}
    renamed = {project: {'workspace': workspace_row, 'sessions': [{**updated, 'title': 'Renamed'}]}}
    catalog.save(renamed, new, {}, ())
    assert catalog.generations() == {'revision': 2, 'observation_sessions': 1, 'observation_membership': 0, 'navigation': 2}
    child = {project: {'workspace': workspace_row, 'sessions': [{**renamed[project]['sessions'][0], 'sessionKind': 'worker', 'parentId': 'parent'}]}}
    catalog.save(child, renamed, {}, ())
    assert catalog.generations() == {'revision': 3, 'observation_sessions': 2, 'observation_membership': 1, 'navigation': 3}
    unavailable = {project: {'workspace': {**workspace_row, 'available': False}, 'sessions': child[project]['sessions']}}
    catalog.save(unavailable, child, {}, ())
    assert catalog.generations() == {'revision': 4, 'observation_sessions': 2, 'observation_membership': 1, 'navigation': 4}
    relocated = {project: {'workspace': {**workspace_row, 'path': '/relocated'}, 'sessions': child[project]['sessions']}}
    catalog.save(relocated, unavailable, {}, ())
    assert catalog.generations() == {'revision': 5, 'observation_sessions': 3, 'observation_membership': 2, 'navigation': 5}
    transcript = {project: {'workspace': relocated[project]['workspace'], 'sessions': [
        {**relocated[project]['sessions'][0], 'transcriptRevision': [567, 890], 'turnCount': 8}]}}
    catalog.save(transcript, relocated, {}, ())
    assert catalog.generations() == {'revision': 6, 'observation_sessions': 3, 'observation_membership': 2, 'navigation': 5}
    description = {project: {'workspace': transcript[project]['workspace'], 'sessions': [
        {**transcript[project]['sessions'][0], 'description': 'Searchable updated description'}]}}
    catalog.save(description, transcript, {}, ())
    assert catalog.generations() == {'revision': 7, 'observation_sessions': 3, 'observation_membership': 2, 'navigation': 6}
    named = {project: {'workspace': description[project]['workspace'], 'sessions': [
        {**description[project]['sessions'][0], 'nameRevision': 2}]}}
    catalog.save(named, description, {}, ())
    # A newer revision can replace a local title even when this native title
    # stayed the same; sessions observers must not reuse the older authority.
    assert catalog.generations() == {'revision': 8, 'observation_sessions': 4, 'observation_membership': 2, 'navigation': 7}


def test_disposable_file_cache_failure_still_reads_exact_canonical_metadata(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Before'})
    cache = tmp_path / 'catalog.sqlite3'
    initial = NativeHistory(home, cache_path=cache); initial.scan(); initial.close()
    index = NativeHistory(home, watch=True, cache_path=cache, directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    index._reconcile_at = time.monotonic() + 900
    token, _ = index.scan_changes()
    def unavailable(*args):
        raise sqlite3.OperationalError('disposable file metadata is unavailable')
    monkeypatch.setattr(index._catalog, 'file', unavailable)
    write_json(path / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Canonical changed'})
    project = project_slug(workspace)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project, 'saved', 'metadata')}))
    _, delta = index.scan_changes(since=token)
    assert [row['title'] for row in delta['sessions']] == ['Canonical changed']
    assert index._catalog.get(project, 'saved')['title'] == 'Canonical changed'
    index.close()


@pytest.mark.parametrize('watch', [False, True])
def test_directory_force_without_cache_still_admits_only_a_bounded_slice(tmp_path, monkeypatch, watch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    for number in range(100):
        session(home, workspace, f'saved-{number}', {'working_dir': str(workspace), 'bundle': 'work'})
    index = NativeHistory(home, watch=watch, cache_path=tmp_path / 'catalog.sqlite3', directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (watch, set()))
    monkeypatch.setattr(index, '_directories', lambda *_: pytest.fail('ordinary forced refresh must not enumerate all history'))
    index._recovery_budget = 2
    _, first = index.scan_changes(force=True)
    assert first['reset'] and first['directoryReady']
    assert first['reconciliation']['phase'] == 'running'
    assert first['reconciliation']['steps'] <= 2 and first['metadataReads'] < 100
    assert first['sessions'] == []
    index.close()


def test_unavailable_directory_pauses_background_memory_growth_and_recovers_after_repair(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{number:03d}', {'working_dir': str(workspace), 'bundle': 'work'})
             for number in range(30)]
    originals = [(path / 'transcript.jsonl').read_bytes() for path in paths]
    cache = tmp_path / 'catalog.sqlite3'
    cache.write_bytes(b'preserve this rejected cache')
    index = NativeHistory(home, watch=True, cache_path=cache, directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    token = None
    for _ in range(20):
        token, delta = index.scan_changes(since=token, force=True)
        assert not delta['directoryReady'] and delta['reconciliation']['phase'] == 'unavailable'
        assert delta['metadataReads'] == 0 and delta['reconciliation']['steps'] == 0
        assert delta['issues'] == [{'kind': 'unavailable-catalog-cache'}]
        assert not index._projects and not index._files and not index._directory_unsaved
    assert cache.read_bytes() == b'preserve this rejected cache'
    cache.rename(tmp_path / 'rejected-cache.sqlite3')
    NativeCatalog(cache, home)
    index._cache_retry_at = 0
    index._recovery_budget = 3
    for _ in range(100):
        token, delta = index.scan_changes(since=token)
        assert delta['directoryReady'] and delta['metadataReads'] <= 3
        if delta['reconciliation']['phase'] == 'complete':
            break
    else:
        pytest.fail('Directory discovery did not resume after repairing its cache')
    assert index._catalog.count() == 30
    assert [(path / 'transcript.jsonl').read_bytes() for path in paths] == originals
    index.close()
