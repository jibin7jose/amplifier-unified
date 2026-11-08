"""Counts use scalar facts, regardless of retained summary-body size."""
import pytest

from amplifier_web.history_directory_query import archived_count
from test_history_directory_navigation import directory_fixture


@pytest.mark.parametrize('kind,expected', [('roots', 120), ('children', 2), ('archived', 3)])
def test_count_queries_do_not_bind_retained_summary_bodies(tmp_path, kind, expected):
    state, directory = directory_fixture(tmp_path, 124)
    parent = state['sessions'][1]
    for index in (31, 62):
        state['sessions'][index]['parentId'] = parent['id']
    state['conversationOrganization']['archived'].update(s31=1, unknown=1)
    directory.sync(state['sessions'], state)
    directory.db.commit()
    # Most of the library is dormant. The admitted rows retain preparation
    # details, which are irrelevant to all three membership counts.
    state['sessions'] = state['sessions'][:6]
    for row in state['sessions']:
        row['preparation'] = {'detail': 'small'}
    readers = {
        'roots': lambda: directory.root_count(state),
        'children': lambda: directory.child_count(state, parent),
        'archived': lambda: archived_count(directory, state),
    }

    def read_with_statement_size():
        statements = []
        directory.db.set_trace_callback(statements.append)
        try:
            result = readers[kind]()
        finally:
            directory.db.set_trace_callback(None)
        assert all('retained detail' not in sql for sql in statements)
        return result, max((len(sql.encode('utf-8')) for sql in statements), default=0)

    small_count, small_statement = read_with_statement_size()
    assert small_count == expected
    for row in state['sessions']:
        row['preparation']['detail'] = 'retained detail ' * 16384
    large_count, large_statement = read_with_statement_size()
    assert large_count == expected
    # An unchanged scalar overlay may reuse the root count's warm fast path.
    assert large_statement <= small_statement
    assert len(state['sessions']) == 6

    # Narrow projections must still observe current membership. These mutate
    # the same admitted row, not a replacement object or a new library.
    if kind == 'roots':
        state['sessions'][0]['sessionKind'] = 'root'
        # Invalidate the membership memo: the production root-count path must
        # also omit retained bodies when it actually executes its count query.
        changed_count, changed_statement = read_with_statement_size()
        assert changed_count == expected + 1
        assert changed_statement <= small_statement
    elif kind == 'children':
        state['sessions'][0]['parentId'] = parent['id']
        assert readers[kind]() == expected + 1
    else:
        state['conversationOrganization']['archived'].pop('unknown')
        assert readers[kind]() == expected - 1
        state['conversationOrganization']['archived']['s0'] = 1
        assert readers[kind]() == expected - 1  # A worker is not a root.
        state['sessions'][0]['sessionKind'] = 'root'
        assert readers[kind]() == expected
