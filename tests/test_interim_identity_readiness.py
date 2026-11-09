"""Interim B/C acceptance at the registered web boundary, not a fake owner.

Native capture and Runtime are synthetic; persistence, alias resolution, startup
refresh, window ownership and HTTP handlers are the real maintained owners.
"""
import asyncio
from contextlib import asynccontextmanager
import copy
import json
from pathlib import Path

import pytest

from amplifier_web.automatic_history import AutomaticHistory
from amplifier_web.server import create_app
from amplifier_web.service import AppService
from amplifier_web.session_files import append_event
from test_automatic_history import native_session
from test_service import Runtime
from test_tool_window_records import _host_placed_tool


async def _seed_alias(service):
    await service.dispatch('session.create', {'title': 'Interim alias owner'})
    session = service._session()
    public, native = session['id'], 'interim-native-root'
    native_session(Path(session['workspace']), native)
    # The real runtime-status reducer establishes the different native alias.
    await service.on_runtime_event('runtime.status', {
        'sessionId': public, 'runtimeSessionId': native, 'status': 'idle'})
    service._message(session, 'user', 'Inspect the saved fixture.', 'chat',
                     id='user', inputId='original')
    arguments = {'command': 'printf retained-alias-input'}
    output = 'retained-alias-output:' + '雪𝄞' * 600
    _, events, node = _host_placed_tool(session, arguments=arguments, result=output)
    # As at the runtime bridge, the host placement uses the public root while
    # canonical capture keeps the native root. Do not rewrite the issued token.
    for event in events:
        append_event(session['workspace'], native, event['event'], {
            **event['data'], 'session_id': native, 'timestamp': event['timestamp']})
    await service.dispatch('session.create', {'title': 'Unrelated root'})
    other = service._session()['id']
    service._state['selectedSessionId'] = None
    await service._flush_pending_progress()
    service._save()
    return {'public': public, 'native': native, 'other': other,
            'observation': node['toolObservationId'], 'call': node['toolCallId'],
            'expected': {'input': arguments, 'output': output}}


@asynccontextmanager
async def _web(home, workspace, authenticated_client, monkeypatch):
    """Join startup for B only; C deliberately does not use this helper."""
    completed = asyncio.Event()
    original = AutomaticHistory.refresh

    async def observed(history, *args, **kwargs):
        try:
            return await original(history, *args, **kwargs)
        finally:
            if history.service.data_dir == home:
                completed.set()

    with monkeypatch.context() as patch:
        patch.setattr(AutomaticHistory, 'refresh', observed)
        runtime = Runtime()
        application = await create_app(
            home, workspace=workspace, runtime=runtime, voice=False,
            background_updates=False, preload_providers=False, background_work=False)
        service, client = application['service'], None
        try:
            await asyncio.wait_for(completed.wait(), 10)
            assert not service.state['sharedHistory'].get('error')
            assert not service.state['sharedHistory'].get('issues')
            service.clients.attach('interim-browser', kind='web')
            client = await authenticated_client(application)
            client.session.headers['X-Amplifier-Client'] = 'interim-browser'
            yield service, client, runtime
        finally:
            if client is not None:
                await client.close()
            elif not service.closed:
                await service.close()


async def _get(client, params):
    response = await client.get('/api/conversation/detail', params=params)
    body = await response.json()
    assert response.status == 200, (response.status, body)
    return body


async def _open_work(service, client, saved, requested, window):
    groups = await _get(client, {'sessionId': requested, 'part': 'groups'})
    if window == 'saved':
        group, = [row['id'] for row in groups['items'] if row.get('observationWindow')]
    else:
        with service.clients.bind('interim-browser'):
            owner = service._session(requested, hydrate=False)
            projection = service.event_log_view.windows.project(owner)
        group, = [row['id'] for row in projection['execution']['segments']
                  if row.get('turnId') == 'original' and not row.get('observationWindow')]
    nodes = await _get(client, {'sessionId': requested, 'part': 'nodes', 'group': group})
    known = {row['id']: row['detailVersion'] for row in nodes['items']}
    node, = [row for row in nodes['items']
             if row.get('kind') == 'tool' and row.get('toolCallId') == saved['call']
             and (window == 'resident' or row.get('event') == 'tool:post')]
    if window == 'resident':
        assert node['sessionId'] == saved['public']
        assert node['rootSessionId'] == saved['public']
        assert node['toolObservationId'] == saved['observation']
        detail = await _get(client, {**node['toolDetail'], 'sessionId': requested})
        node = detail['item']
        assert node['sessionId'] == saved['public']
        assert node['rootSessionId'] == saved['public']
        assert node['contentAvailability']['status'] == 'linked'
    else:
        # Raw event observations retain their native source namespace, not
        # invented host root placement (EXECUTION-INSPECTION / _observation).
        # Public owner authority is observable in the issued field selectors
        # below and the positive alias / cross-root refusal HTTP checks.
        assert node['sessionId'] == saved['native']
        assert node['sourceSessionId'] == saved['native']
        assert node['observation'] is True and node['association'] == 'unverified'
        assert 'rootSessionId' not in node
    fields = {field: copy.deepcopy(node[field + 'Detail']) for field in ('input', 'output')}
    for field, selector in fields.items():
        # A field selector identifies canonical evidence, not necessarily the
        # host node. Preserve the opaque id/windowKey/digest as issued.
        assert selector['field'] == field and selector['sessionId'] == saved['public']
        assert selector['id'] and selector['windowKey'] and selector['digest']
        value = await _get(client, {**selector, 'sessionId': requested})
        assert value['nextOffset'] is None
        assert (json.loads(value['value']) if field == 'input' else value['value']) == saved['expected'][field]
    return group, fields, known


async def _refused(response):
    body = await response.json()
    assert response.status == 400, (response.status, body)
    assert body['accepted'] is False and body.get('error')
    assert 'value' not in body and 'items' not in body and 'order' not in body


@pytest.mark.parametrize('identity', ['public', 'native'])
@pytest.mark.parametrize('window', ['resident', 'saved'])
async def test_registered_web_reopen_alias_preserves_fields_and_window_ownership(
        tmp_path, authenticated_client, monkeypatch, identity, window):
    """Current aliases work; another root and pre-restart leases do not."""
    home = tmp_path / 'app'
    async with _web(home, tmp_path, authenticated_client, monkeypatch) as (service, client, runtime):
        saved = await _seed_alias(service)
        # Issue genuine pre-restart selectors through the canonical public ID.
        old_group, old_fields, old_known = await _open_work(
            service, client, saved, saved['public'], window)
        assert runtime.started == runtime.sent == []
    assert service.closed
    old_instance = service.instance_id

    async with _web(home, tmp_path, authenticated_client, monkeypatch) as (restored, client, runtime):
        assert restored.instance_id != old_instance
        requested = saved[identity]
        owner = restored._session(saved['public'], hydrate=False)
        assert owner['id'] != saved['native']
        assert owner['runtimeSessionId'] == saved['native']
        assert restored._session(saved['native'], hydrate=False) is owner

        # Test stale leases before opening any replacement page. A matching
        # saved root or call ID alone must not recreate old window authority.
        await _refused(await client.get('/api/conversation/detail', params={
            **old_fields['output'], 'sessionId': requested}))
        await _refused(await client.post('/api/conversation/work-sync', json={
            'sessionId': requested, 'group': old_group, 'known': old_known}))

        group, fields, known = await _open_work(restored, client, saved, requested, window)
        await _refused(await client.get('/api/conversation/detail', params={
            **fields['output'], 'sessionId': saved['other']}))
        await _refused(await client.post('/api/conversation/work-sync', json={
            'sessionId': saved['other'], 'group': group, 'known': known}))

        # Keep the positive alias sync oracle: saved-window sync must resolve
        # the same owner as detail GET, not compare a raw alias to a public ID.
        response = await client.post('/api/conversation/work-sync', json={
            'sessionId': requested, 'group': group, 'known': known})
        synced = await response.json()
        assert response.status == 200, (response.status, synced)
        assert synced['order'] == list(known)
        assert all(row['id'] in known for row in synced['items'])
        if window == 'saved':
            assert synced['items'] == []
        assert (await _get(client, {
            **fields['output'], 'sessionId': requested}))['value'] == saved['expected']['output']
        assert runtime.started == runtime.sent == []


async def test_registered_web_first_cold_read_survives_initial_history_refresh(
        tmp_path, authenticated_client, monkeypatch, record_property):
    """Retain the first HTTP outcome, even when the post-refresh control passes."""
    home = tmp_path / 'app'
    previous = AppService(home, Runtime(), workspace=tmp_path)
    try:
        saved = await _seed_alias(previous)
    finally:
        await previous.close()

    refresh_entered, allow_refresh, refresh_finished = (asyncio.Event() for _ in range(3))
    read_entered, release_read = asyncio.Event(), asyncio.Event()
    refreshes = []
    original_refresh = AutomaticHistory.refresh

    async def held_initial_refresh(history, *args, **kwargs):
        if history.service.data_dir != home:
            return await original_refresh(history, *args, **kwargs)
        refreshes.append(history.service.instance_id)
        if len(refreshes) != 1:
            return await original_refresh(history, *args, **kwargs)
        refresh_entered.set()
        await allow_refresh.wait()
        try:
            return await original_refresh(history, *args, **kwargs)
        finally:
            # The real refresh/publication runs while the first real window
            # result is in flight. Never replace either result or force ready.
            release_read.set()
            refresh_finished.set()

    monkeypatch.setattr(AutomaticHistory, 'refresh', held_initial_refresh)
    runtime = Runtime()
    application = await create_app(
        home, workspace=tmp_path, runtime=runtime, voice=False,
        background_updates=False, preload_providers=False, background_work=False)
    restored, client, first_read = application['service'], None, None
    try:
        await asyncio.wait_for(refresh_entered.wait(), 10)
        restored.clients.attach('interim-browser', kind='web')
        client = await authenticated_client(application)
        client.session.headers['X-Amplifier-Client'] = 'interim-browser'
        windows = restored.event_log_view.windows
        worker = windows._worker()
        original_project = worker.project
        reads = []

        async def held_first_result(inputs, *, key):
            result = await original_project(inputs, key=key)
            if inputs.get('operation') == 'window' and inputs['session']['id'] == saved['public']:
                reads.append(key)
                if len(reads) == 1:
                    read_entered.set()
                    await release_read.wait()
            return result

        monkeypatch.setattr(worker, 'project', held_first_result)
        params = {'sessionId': saved['public'], 'part': 'groups'}
        first_read = asyncio.create_task(client.get('/api/conversation/detail', params=params))
        await asyncio.wait_for(read_entered.wait(), 30)
        initial_history = copy.deepcopy(restored.state['sharedHistory'])
        initial_work = copy.deepcopy(windows.project(
            restored._session(saved['public'], hydrate=False))['workProjection'])
        assert initial_history['loading'] is True
        assert initial_work['current'] is False and initial_work['status'] != 'ready'
        record_property('initial_not_ready', json.dumps({
            'sharedHistory': initial_history, 'workProjection': initial_work}, sort_keys=True))

        allow_refresh.set()
        first = await asyncio.wait_for(asyncio.shield(first_read), 30)
        first_body = await first.json()
        first_work = copy.deepcopy(windows.project(
            restored._session(saved['public'], hydrate=False))['workProjection'])
        # Preserve before explicitly joining startup. The second success must
        # never overwrite or stand in for this first cold-read outcome.
        record_property('first_cold_response', json.dumps({
            'status': first.status, 'body': first_body, 'workProjection': first_work}, sort_keys=True))
        # Explicit not-ready is allowed by the current acceptance contract.
        # It must not masquerade as an accepted ready page. Preserve it before
        # the separate post-startup request, not a retry loop until green.
        if first.status == 200:
            assert first_work['status'] == 'ready' and first_work['current'] is True
            assert first_work['receipt']['sessionId'] == saved['public']
            assert first_body['items'] and first_body['coverage']['historyComplete'] is False
        else:
            assert first.status == 400, (first.status, first_body)
            assert first_body['accepted'] is False
            assert first_body['error'] == (
                'Invalid request: The requested conversation changed. Open it again.')
            assert not first_work['current']
            assert 'items' not in first_body and 'value' not in first_body
        await asyncio.wait_for(refresh_finished.wait(), 10)
        assert refreshes == [restored.instance_id]
        assert not restored.state['sharedHistory']['loading']
        assert not restored.state['sharedHistory'].get('error')
        assert not restored.state['sharedHistory'].get('issues')

        ready = await client.get('/api/conversation/detail', params=params)
        ready_body = await ready.json()
        ready_work = copy.deepcopy(windows.project(
            restored._session(saved['public'], hydrate=False))['workProjection'])
        record_property('after_initial_refresh', json.dumps({
            'status': ready.status, 'body': ready_body, 'workProjection': ready_work}, sort_keys=True))
        assert ready.status == 200, (ready.status, ready_body)
        assert ready_work['status'] == 'ready' and ready_work['current'] is True
        assert ready_work['receipt']['hostInstanceId'] == restored.instance_id
        assert ready_work['receipt']['sessionId'] == saved['public']
        assert ready_body['items'] and ready_body['coverage']['historyComplete'] is False
        group, = ready_body['items']
        nodes = await _get(client, {**params, 'part': 'nodes', 'group': group['id']})
        observed = [row for row in nodes['items']
                    if row.get('kind') == 'tool' and row.get('toolCallId') == saved['call']]
        assert [row['event'] for row in observed] == ['tool:pre', 'tool:post']
        assert ready_work['receipt']['itemCount'] == len(nodes['items'])
        assert (await _get(client, observed[-1]['outputDetail']))['value'] == saved['expected']['output']
        assert runtime.started == runtime.sent == []
        assert len(reads) == 2 and len(refreshes) == 1
    finally:
        allow_refresh.set()
        release_read.set()
        if first_read is not None:
            await asyncio.gather(first_read, return_exceptions=True)
        if client is not None:
            await client.close()
        elif not restored.closed:
            await restored.close()