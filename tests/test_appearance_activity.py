"""Presentation facts reuse live lifecycle owners without reading dormant rows."""
from types import SimpleNamespace

import pytest

from amplifier_web.appearance_activity import snapshot
from amplifier_web.browser_state import SessionIndex


def test_other_workspace_and_delegate_activity_is_not_limited_to_selected_chat():
    rows = [{'id': 'selected', 'status': 'idle', 'workspaceId': 'one'},
            {'id': 'other', 'status': 'working', 'workspaceId': 'two'},
            {'id': 'worker', 'status': 'running', 'sessionKind': 'worker', 'parentId': 'other'}]
    state = {'selectedSessionId': 'selected', 'sessions': rows}
    index = SessionIndex(state)
    assert snapshot(state, index) == dict(execution=True, preparing=False, loading=False,
                                         operations=False, active=True)
    for row in rows:
        row['status'] = 'idle'
    assert index.patch(state, {'other', 'worker'})
    assert snapshot(state, index)['active'] is False


@pytest.mark.parametrize('patch,field', [({'status': 'starting'}, 'preparing'),
    ({'status': 'stopping'}, 'execution'), ({'configurationBusy': True}, 'preparing'),
    ({'preparation': {'status': 'preparing'}}, 'preparing'), ({'historyLoading': True}, 'loading')])
def test_existing_lifecycle_flags_define_activity(patch, field):
    state = {'sessions': [{'id': 'chat', 'status': 'idle', **patch}]}
    value = snapshot(state, SessionIndex(state))
    assert value[field] is True and value['active'] is True


@pytest.mark.parametrize('phase,active', [('queued', True), ('pending', True), ('preparing', True),
    ('working', True), ('ready', False), ('completed', False), ('error', False), ('unknown', False)])
def test_canonical_operation_phase_not_saved_task_or_read_traffic(phase, active):
    state = {'sessions': [], 'actionStatus': {'test.operation': {'phase': phase}},
             'task': {'status': 'blocked'}, 'sharedHistory': {'refreshing': True}}
    assert snapshot(state, SessionIndex(state))['active'] is active


@pytest.mark.parametrize('action', [
    'providers.list', 'bundles.list', 'providers.credentials', 'providers.schema',
    'providers.models', 'providers.imageModels', 'providers.decisionModels',
    'routing.list', 'routing.show', 'configuration.defaults', 'bundle.preview',
])
@pytest.mark.parametrize('phase', ['queued', 'working'])
def test_routine_metadata_progress_stays_quiet_without_hiding_real_work(action, phase):
    state = {'sessions': [], 'actionStatus': {action: {'phase': phase}}}
    index = SessionIndex(state)
    assert snapshot(state, index)['operations'] is False
    assert snapshot(state, index)['active'] is False
    state['actionStatus']['providers.save'] = {'phase': 'working'}
    assert snapshot(state, index)['operations'] is True
    assert snapshot(state, index)['active'] is True
    state['actionStatus']['providers.save']['phase'] = 'ready'
    assert snapshot(state, index)['active'] is False


@pytest.mark.parametrize('action,target,active', [
    ('runtime.control', {'operation': 'configuration.catalog'}, False),
    ('runtime.control', {'operation': 'configuration.apply'}, True),
    ('providers.test', {}, True),
    ('providers.testMessage', {}, True),
    ('bundle.switch', {}, True),
])
def test_read_exclusion_is_not_a_provider_or_runtime_prefix_waiver(action, target, active):
    state = {'sessions': [], 'actionStatus': {action: {'phase': 'working', 'target': target}}}
    assert snapshot(state, SessionIndex(state))['active'] is active


def test_internal_bookkeeping_does_not_light_the_scanner():
    state = {'sessions': [{'id': 'internal', 'sessionKind': 'internal', 'status': 'working'}]}
    assert snapshot(state, SessionIndex(state))['active'] is False


def test_projection_visits_only_indexed_active_rows_not_session_library():
    class Unreadable:
        def __iter__(self):
            raise AssertionError('Appearance projection scanned dormant history')
    state = {'sessions': Unreadable()}
    index = SimpleNamespace(active={'worker'}, by_id={'worker': {'status': 'working'}})
    assert snapshot(state, index)['active'] is True
