import copy
import json

import pytest

from amplifier_web.agent_receipts import compact_receipt, domain_receipt
from test_agent_canvas_scope import app, caller, command, publish


@pytest.mark.parametrize('accepted,status', [(True, 'pending'), (False, 'denied'), (True, 'unknown')])
def test_domain_receipt_keeps_all_nonstate_fields_without_effect_projection(accepted, status):
    original = {'accepted': accepted, 'operationId': 'exact-operation', 'revision': 7,
                'result': {'status': status}, 'effects': [{'id': 'one', 'type': 'effect', 'payload': 'kept'}],
                'state': {'private': 'must-not-copy'}}
    before = copy.deepcopy(original)
    result = domain_receipt(original, action='permissions.request', session_id='caller', revision=8)
    assert original == before
    assert {key: value for key, value in result.items() if key != 'state'} == {
        key: value for key, value in original.items() if key != 'state'}
    assert result['state']['revision'] == 8 and result['state']['sessionId'] == 'caller'
    assert result['state']['_stateAccess']['resultPaths'] == [
        '/permissions', '/managementResults/exact-operation']
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('accepted,status', [(True, 'pending'), (False, 'denied'), (True, 'unknown')])
def test_compact_receipt_preserves_domain_outcome_and_correlation(accepted, status):
    original = {'accepted': accepted, 'operationId': 'operation/one', 'revision': 41,
                'result': {'status': status, 'effect': 'unknown', 'error': {'code': 'approval_required'}},
                'effects': [{'id': 'effect-one', 'type': 'render', 'payload': 'large'}],
                'state': {'unrelated': 'x' * 30_000}}
    before = copy.deepcopy(original)
    actual = compact_receipt(original, action='permissions.request', session_id='chat',
                             revision=42, client_id='caller', command_id='command-one')
    assert original == before
    assert actual['accepted'] is accepted
    assert actual['result'] == original['result']
    assert actual['operationId'] == 'operation/one' and actual['revision'] == 41
    assert actual['commandId'] == 'command-one'
    assert actual['state']['revision'] == 42
    assert actual['state']['_stateAccess']['readback'] == [
        {'operation': 'get_state', 'args': {'path': '/managementResults/operation~1one', 'clientId': 'caller'}},
        {'operation': 'get_state', 'args': {'path': '/permissions', 'clientId': 'caller'}}]
    assert len(json.dumps(actual)) < 1500
    assert 'unrelated' not in json.dumps(actual)


@pytest.mark.parametrize('action,path', [('view.update', '/view'), ('settings.open', '/view'),
    ('canvas.tabClose', '/canvas'), ('call.end', '/voice')])
def test_relevant_readback_is_a_scoped_request_not_an_invented_completion(action, path):
    actual = compact_receipt({'accepted': True}, action=action, session_id='chat', revision=2)
    assert actual['state']['_stateAccess']['readback'] == [
        {'operation': 'get_state', 'args': {'path': path}}]
    assert 'result' not in actual and 'completed' not in actual and 'effects' not in actual


def test_specialized_readback_and_domain_result_are_preserved():
    original = {'accepted': True, 'operationId': 'smart-operation',
                'result': {'status': 'pending', 'details': {'arbitrary': 'domain data'}}}
    actual = compact_receipt(original, action='smartTools.call', session_id='chat', revision=1)
    assert actual['read'] == {'action': 'smartTools.readResult', 'args': {'operationId': 'smart-operation'}}
    assert actual['result'] == original['result']
    assert actual['state']['_stateAccess']['readback'] == []


def test_settings_credential_readback_keeps_the_exact_pending_operation():
    original = {'accepted': True, 'result': {'step': 'connect',
                'credentialCheck': {'status': 'pending', 'operationId': 'scan-one'}}}
    actual = compact_receipt(original, action='settings.connection.select',
                             session_id='chat', revision=4, client_id='caller')
    assert actual['result'] == original['result']
    assert actual['state']['_stateAccess']['readback'] == [
        {'operation': 'get_state', 'args': {'path': '/managementResults/scan-one', 'clientId': 'caller'}},
        {'operation': 'get_state', 'args': {'path': '/view', 'clientId': 'caller'}}]


async def test_native_tool_requests_compact_receipt_without_changing_raw_bridge_default(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_web.service import AppService
    from test_app_control_validation import mounted_tool
    from test_work_origin import worker_scope

    app = AppService(tmp_path, SimpleNamespace(close=AsyncMock()), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session_id = app._session()['id']
        async def bridge(operation, args):
            return await app.app_bridge(operation, args, session_id)
        args = {'action': 'session.rename', 'args': {'id': session_id, 'title': 'First'}}
        raw = await bridge('dispatch', args)
        assert 'view' in raw['state']
        worker, coordinator, _, worker_bridge = worker_scope()
        coordinator.session_id = session_id
        worker.bridge = bridge
        tool = await mounted_tool(worker_bridge)
        result = await tool.execute({'operation': 'dispatch', 'args': {
            'action': 'session.rename', 'id': 'rename-second',
            'args': {'id': session_id, 'title': 'Second'}}})
        assert result.success and result.output['accepted'] is True
        assert result.output['commandId'] == 'rename-second'
        assert app._session()['title'] == 'Second'
        assert 'view' not in result.output['state']
        assert result.output['state']['sessionId'] == session_id
        assert len(json.dumps(result.output)) < len(json.dumps(raw)) / 3
        # A retry with the same command is a receipt read, not another mutation.
        revision = app.state['revision']
        repeated = await tool.execute({'operation': 'dispatch', 'args': {
            'action': 'session.rename', 'id': 'rename-second',
            'args': {'id': session_id, 'title': 'Second'}}})
        assert repeated.success and repeated.output['revision'] == result.output['revision']
        assert app.state['revision'] == revision
    finally:
        await app.close()


@pytest.mark.parametrize('public_mode', [None, 'receipt'])
async def test_worker_receipt_hint_never_changes_identity_bound_arguments(public_mode):
    import asyncio
    from test_work_origin import worker_scope
    worker, coordinator, capabilities, bridge = worker_scope()
    seen = []
    async def observe(data):
        seen.append(copy.deepcopy(data))
        return {'complete': False, 'effectAuthorized': False}
    capabilities['web.work_effect_origin'] = observe
    coordinator._tool_dispatch_contexts = {asyncio.current_task(): {'tool_call_id': 'exact-call'}}
    original = {'action': 'session.pin', 'args': {'id': 'root', 'pinned': True}, 'id': 'pin-one'}
    if public_mode is not None:
        original['responseMode'] = public_mode
    body = {**copy.deepcopy(original), '_responseMode': 'forged'}
    before = copy.deepcopy(body)
    await bridge('dispatch', body)
    assert body == before
    assert seen == [{'tool_call_id': 'exact-call', 'tool_name': 'app_control',
                     'tool_input': {'operation': 'dispatch', 'args': original}}]
    sent = worker.bridge.call_args.args[1]
    assert sent['_responseMode'] == 'receipt'
    assert {k: v for k, v in sent.items() if not k.startswith('_')} == original


async def test_compact_canvas_receipt_readback_stays_with_calling_client(app):
    sid = caller(app)
    artifact = await publish(app, sid)
    before = copy.deepcopy(app.clients.records['other'])
    result = await app.app_bridge('dispatch', {'responseMode': 'receipt',
        'action': 'canvas.select', 'args': {'id': artifact}}, sid)
    assert result['accepted'] and result['state']['clientId'] == 'caller'
    assert set(result['state']) == {'revision', 'sessionId', 'clientId', '_stateAccess'}
    request = result['state']['_stateAccess']['readback'][0]
    readback = await app.app_bridge(request['operation'], request['args'], sid)
    assert next(row['value'] for row in readback['items'] if row['key'] == 'id') == artifact
    assert 'Default artifact' not in json.dumps(readback)
    assert app.clients.records['other'] == before


async def test_compact_mode_does_not_bypass_foreign_client_or_dirty_viewer_checks(app):
    from amplifier_web.service import AppError
    sid = caller(app)
    artifact = await publish(app, sid)
    before = copy.deepcopy(app.clients.records)
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'responseMode': 'receipt',
            'action': 'canvas.select', 'args': {'id': artifact, 'clientId': 'other'}}, sid)
    assert app.clients.records == before
    await command(app, 'caller', 'canvas.show', {'kind': 'text', 'content': 'Unsaved viewer'})
    with app.clients.bind('caller'):
        view = app.canvas_views.summary('primary')
    target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await command(app, 'caller', 'canvas.views.dirty', {**target, 'dirty': True})
    before = copy.deepcopy(app.clients.records)
    with pytest.raises(AppError, match='primary viewer edit'):
        await app.app_bridge('dispatch', {'responseMode': 'receipt',
            'action': 'canvas.select', 'args': {'id': artifact, 'clientId': 'caller'}}, sid)
    assert app.clients.records == before


async def test_compact_navigation_rechecks_calling_chat_after_waiting_for_lock(app, monkeypatch):
    import asyncio
    from amplifier_web.service import AppError
    sid = caller(app)
    artifact = await publish(app, sid)
    entered = asyncio.Event()
    original = app.dispatch
    async def observed(action, *args, **kwargs):
        if action == 'canvas.select':
            entered.set()
            assert kwargs['include_state'] is False
        return await original(action, *args, **kwargs)
    monkeypatch.setattr(app, 'dispatch', observed)
    async with app.lock:
        task = asyncio.create_task(app.app_bridge('dispatch', {'responseMode': 'receipt',
            'action': 'canvas.select', 'args': {'id': artifact}}, sid))
        await asyncio.wait_for(entered.wait(), 2)
        record = app.clients.records['caller']
        record['selectedSessionId'] = app.state['selectedSessionId']
        record['selectedWorkspaceId'] = app.state['selectedWorkspaceId']
        before = copy.deepcopy(record['canvas'])
    with pytest.raises(AppError, match='client changed chats'):
        await task
    assert record['canvas'] == before
