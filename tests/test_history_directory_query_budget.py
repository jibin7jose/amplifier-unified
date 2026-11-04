"""Typed directory query work stays linear in retained metadata and live owners."""
import json
import time

import pytest

from amplifier_web.history_directory import COLUMNS
from test_automatic_history import app_factory


def measure_sql(app, query):
    instructions = 0
    def progress():
        nonlocal instructions
        instructions += 1000
        return 0
    app.db.set_progress_handler(progress, 1000)
    start = time.monotonic()
    try:
        result = query()
    finally:
        app.db.set_progress_handler(None, 0)
    return result, instructions, (time.monotonic() - start) * 1000


@pytest.mark.parametrize('count,live', [(27000, 6), (1000, 1000)])
async def test_local_catalog_navigation_and_children_avoid_quadratic_live_overlay_join(app_factory, count, live):
    app = app_factory()
    template = app._new_session({'title': 'Seed owner'})
    rows = [{**template, 'id': f'owner-{number:05}', 'title': f'Owner {number:05}',
             'messages': [], 'workers': [], 'recentActivityAt': number,
             'navigationActivityAt': number} for number in range(live)]
    app._state['sessions'] = rows
    app._state['selectedSessionId'] = rows[0]['id']
    app._save()
    scalar = dict(zip(COLUMNS, app.db.execute('SELECT ' + ','.join(COLUMNS) +
        ' FROM history_local_rows WHERE id=?', (rows[0]['id'],)).fetchone()))
    def dormant():
        for number in range(live, count):
            identity = f'owner-{number:05}'
            values = {**scalar, 'id': identity, 'native_id': identity, 'alias_id': identity,
                'title': f'Owner {number:05}', 'recent': number, 'ordinal': f'{number:020}',
                'summary': json.dumps({**template, 'id': identity, 'title': f'Owner {number:05}',
                                      'messages': [], 'workers': [], 'recentActivityAt': number})}
            yield tuple(values[name] for name in COLUMNS)
    app.db.executemany('INSERT INTO history_local_rows (' + ','.join(COLUMNS) + ') VALUES (' +
                      ','.join('?' for _ in COLUMNS) + ')', dormant())
    app.db.commit()
    state = {**app._state, 'view': {**app._state['view'], 'navChatScope': 'all'}}
    decoded = []
    original = app.directory.decode
    app.directory.decode = lambda raw: (decoded.append(1), original(raw))[1]
    result, instructions, elapsed = measure_sql(app, lambda: app.directory.chats(state))
    assert result['total'] == count
    assert len(decoded) == len(result['items']) <= 100
    assert instructions < count * 1200 + live * 2000
    children, child_work, child_ms = measure_sql(app, lambda: app.directory.children(state, rows[0]))
    assert children['total'] == 0 and children['items'] == []
    assert child_work < count * 700 + live * 2000
    assert len(app._state['sessions']) == live
    print(json.dumps({'catalog': count, 'live': live, 'navigationVm': instructions,
                      'navigationMs': elapsed, 'childrenVm': child_work, 'childrenMs': child_ms}))


@pytest.mark.parametrize('automatic_index', [True, False])
async def test_mixed_native_catalog_with_large_registry_uses_bounded_lookup_work(app_factory, tmp_path, automatic_index):
    """Real-host shape: many workspaces plus native rows and local overrides.

    A six-owner/one-workspace scale fixture misses catalog×registry JSON joins.
    Abort by VM work so this regression fails promptly on the old query.
    """
    from amplifier_web.native_catalog import NativeCatalog
    from amplifier_web.session_files import amplifier_home
    from test_native_directory import seed
    app = app_factory()
    (tmp_path / 'catalog-workspace').mkdir()
    catalog = NativeCatalog(tmp_path / 'native-query.sqlite3', amplifier_home())
    project, workspace = seed(catalog, tmp_path / 'catalog-workspace', 27000)
    app.directory.bind(catalog)
    owners = []
    for number in range(187):
        row = app._new_session({'title': f'Local override {number}', 'workspace': workspace['path']})
        row.update(nativeIdentity=f'saved-{number:05d}', runtimeSessionId=f'saved-{number:05d}',
                   nativeProject=project, historyManaged=True, workspaceId=workspace['id'])
        owners.append(row)
    app.directory.sync(owners, app._state)
    app.db.commit()
    app._state['sessions'] = owners[:6]
    app._state['workspaces'] = [workspace, *[
        {'id':f'workspace-{number}', 'path':str(tmp_path / f'workspace-{number}'),
         'name':f'Workspace {number}', 'available':True} for number in range(1749)]]
    app._state['selectedWorkspaceId'] = workspace['id']
    app._state['selectedSessionId'] = owners[0]['id']
    state = {**app._state, 'view':{**app._state['view'], 'navChatScope':'all', 'navArchive':'all'},
             'pinnedSessionIds':[f'saved-{number:05d}' for number in range(500,1000)],
             'conversationOrganization':{'archived':{f'saved-{number:05d}':True for number in range(1000,1500)}}}
    decoded = []
    original = app.directory.decode
    app.directory.decode = lambda row: (decoded.append(1), original(row))[1]
    operations = 0
    def budget():
        nonlocal operations
        operations += 10000
        return operations > 30_000_000
    app.db.set_progress_handler(budget,10000)
    # SQLite versions differ in whether they synthesize an index for CTE
    # ownership lookups. Correct bounded work must not rely on that choice.
    app.db.execute('PRAGMA automatic_index=' + ('ON' if automatic_index else 'OFF'))
    start = time.monotonic()
    try:
        page = app.directory.chats(state)
        counts = app.directory.workspace_counts(state, archive='all')
    finally:
        app.db.set_progress_handler(None,0)
    assert page['total'] == 27000
    assert page['items'][0]['id'] == 'saved-00500' and page['items'][0]['pinned']
    assert counts[workspace['path']]['chatCount'] == 26500
    assert len(decoded) == len(page['items']) <= 100
    assert len(app._state['sessions']) == 6
    assert not app.runtime.started and not app.runtime.sent
    print(json.dumps({'nativeRows':27000,'localOverrides':187,'workspaces':1750,
                      'automaticIndex':automatic_index,
                      'pins':500,'archived':500,'vm':operations,
                      'ms':(time.monotonic()-start)*1000}))


async def test_empty_archive_and_unfiltered_children_skip_redundant_catalog_queries(app_factory):
    from amplifier_web.history_directory_query import archived_count
    app = app_factory()
    parent = app._new_session({'title': 'Parent'})
    child = app._new_session({'title': 'Matching child'})
    child.update(sessionKind='worker', parentId=parent['id'])
    app._state['sessions'] = [parent, child]
    app._save()
    state = {**app._state, 'conversationOrganization': {'archived': {}}}
    queries = []
    app.db.set_trace_callback(queries.append)
    try:
        assert archived_count(app.directory, state) == 0
        assert queries == []
        unfiltered = app.directory.children(state, parent)
        assert unfiltered['total'] == unfiltered['unfilteredTotal'] == 1
        assert [row['id'] for row in unfiltered['items']] == [child['id']]
        assert len([sql for sql in queries if sql.endswith(' SELECT COUNT(*) FROM child_rows')]) == 1
        assert not any(sql.endswith(' SELECT COUNT(*) FROM matched') for sql in queries)
        queries.clear()
        filtered = app.directory.children(state, parent, 'absent search text')
        assert filtered['total'] == 0 and filtered['unfilteredTotal'] == 1
        assert any(sql.endswith(' SELECT COUNT(*) FROM matched') for sql in queries)
    finally:
        app.db.set_trace_callback(None)
