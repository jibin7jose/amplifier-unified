"""Historical catalog size must not multiply admitted JSON extraction work."""
from collections import Counter
import sqlite3

import pytest

from amplifier_web.history_directory import COLUMNS
from amplifier_web.history_directory_query import chats, workspace_counts
from amplifier_web.native_catalog import NativeCatalog
from test_history_directory_navigation import directory_fixture
from test_native_directory import seed


@pytest.mark.parametrize('automatic_index', [True, False])
@pytest.mark.parametrize('operation', ['workspace_counts', 'shortcuts'])
def test_catalog_extracts_each_live_scalar_once_per_statement(tmp_path, automatic_index, operation):
    state, directory = directory_fixture(tmp_path, 8)
    # This workload measures all 500 chats. The navigation fixture otherwise
    # includes two archived chats, which workspace counts correctly exclude.
    state['conversationOrganization']['archived'].clear()
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    project, workspace = seed(catalog, tmp_path / 'workspace', 500)
    for number, row in enumerate(state['sessions']):
        row.update(workspace=workspace['path'], workspaceId=workspace['id'],
                   nativeProject=project, nativeIdentity=f'saved-{number:05d}',
                   runtimeSessionId=f'saved-{number:05d}', historyManaged=True)
    directory.bind(catalog)
    directory.sync(state['sessions'], state)
    directory.db.commit()
    state['sessions'] = state['sessions'][:2]
    state['workspaces'] = [workspace]
    state['selectedWorkspaceId'] = workspace['id']
    state['selectedSessionId'] = state['sessions'][0]['id']
    state['view'].update(navChatScope='all', navArchive='all')
    # This remains metadata, not transcript history. Manager summaries can be
    # wide even when only a few rows are admitted. VM-step counts alone do not
    # account for reparsing those strings inside each catalog-row lookup.
    for row in state['sessions']:
        row['coordinationReference'] = {'detail': 'x' * 32_000}
    directory.db.execute('PRAGMA automatic_index=' + ('ON' if automatic_index else 'OFF'))
    extractions = Counter()
    with sqlite3.connect(':memory:') as builtin:
        def extract(value, path):
            if isinstance(value, str) and value.startswith('["s'):
                extractions[path] += 1
            # Count calls without replacing SQLite's extraction semantics.
            return builtin.execute('SELECT json_extract(?,?)', (value, path)).fetchone()[0]
        directory.db.create_function('json_extract', 2, extract, deterministic=True)
        if operation == 'workspace_counts':
            result = workspace_counts(directory, state, archive='all')
            assert result[workspace['path']]['chatCount'] == 500
            statements = 1
        else:
            result = chats(directory, state, section='shortcuts')
            assert result['total'] == 500 and len(result['items']) == 20
            statements = 2  # Activity counts, then the bounded page.
        assert extractions
        assert sum(extractions.values()) <= len(state['sessions']) * len(COLUMNS) * statements
    directory.db.close()
