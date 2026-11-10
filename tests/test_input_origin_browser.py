"""Ordinary worker bridge + real app actions; no browser, model or screen I/O."""
from copy import deepcopy
import asyncio
import time
from types import SimpleNamespace

import pytest

from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService


def coordinator(session_id):
    capabilities = {}
    return SimpleNamespace(session_id=session_id, get_capability=capabilities.get,
                           register_capability=capabilities.__setitem__)


@pytest.fixture
async def origin(tmp_path):
    app = AppService(tmp_path/'app', workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    for client in ('old-browser', 'input-browser'):
        app.clients.attach(client)
        with app.clients.bind(client):
            app.subscribe()
            await app.update_device({'clientId': client, 'sessionId': sid, 'controls': [{'label': client}]})
            visual = app.computer_visual.for_client(sid)
            await visual.grant_source({'sessionId': sid, 'source': {'kind': 'window', 'label': client}})
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=coordinator(sid))
    worker.runtime = SimpleNamespace(generation={'id': 'generation-1', 'input_ids': ['input-1']})
    worker.context_inputs = ['input-1']
    worker.context_bindings = {'input-1': {'clientId': 'input-browser', 'targets': []}}

    async def bridge(operation, args):
        # Runtime callback was created in the previous browser's request.
        with app.clients.bind('old-browser'):
            return await app.app_bridge(operation, args, sid)
    worker.bridge = bridge
    yield app, sid, worker
    for queue in list(app.queues):
        app.unsubscribe(queue)
    await app.close()


def main_bridge(worker):
    return worker.app_access_bridge(worker.session.coordinator)


async def action(bridge, name, **args):
    return await bridge('dispatch', {'action': name, 'args': args})


async def test_worker_origin_controls_state_navigation_and_screen_status(origin):
    app, sid, worker = origin
    bridge = main_bridge(worker)
    before = deepcopy(app.clients.records['old-browser'])
    state = await bridge('get_state', {'_inputClients': ['old-browser']})
    assert state['canvasContext']['clientId'] == 'input-browser'
    assert state['visibleUI']['clientId'] == 'input-browser'
    assert state['computerVisual']['available'] is True
    assert state['computerVisual']['source']['label'] == 'input-browser'
    assert state['inputOrigin'] == {'clientId': 'input-browser', 'status': 'client'}
    result = await action(bridge, 'view.update', patch={'runtimeDraft': {'section': 'screen-source'}})
    assert result['accepted'] and result['state']['clientId'] == 'input-browser'
    assert set(result['state']) == {'revision', 'sessionId', 'clientId', '_stateAccess'}
    assert result['state']['sessionId'] == sid
    request, = result['state']['_stateAccess']['readback']
    assert request == {'operation': 'get_state', 'args': {'path': '/view', 'clientId': 'input-browser'}}
    readback = await bridge(request['operation'], request['args'])
    assert next(row['value'] for row in readback['items'] if row['key'] == 'panel') == 'runtime'
    assert app.clients.records['input-browser']['view']['runtimeDraft']['tab'] == 'computer'
    assert app.clients.records['old-browser'] == before
    result = await action(bridge, 'computer.visual.status', sessionId=sid)
    assert result['result']['source']['label'] == 'input-browser'
    assert app.clients.current.get() is None


async def test_worker_chat_navigation_uses_current_input_not_runtime_birth_client(origin):
    app, sid, worker = origin
    await app.dispatch('session.create', {'title': 'Existing destination', 'select': False})
    destination = next(row['id'] for row in app._state['sessions'] if row['title'] == 'Existing destination')
    bridge = main_bridge(worker)
    before = deepcopy(app.clients.records['old-browser'])
    result = await bridge('dispatch', {'action': 'session.select', 'id': 'worker-navigation',
        'args': {'id': destination}})
    assert result['accepted'] and result['result']['clientId'] == 'input-browser'
    assert result['result']['sourceSessionId'] == sid
    assert result['result']['selectedSessionId'] == destination
    assert result['result']['rendering'] == 'unverified'
    assert app.clients.records['old-browser'] == before
    assert app.clients.records['input-browser']['selectedSessionId'] == destination
    request, = result['state']['_stateAccess']['readback']
    readback = await bridge(request['operation'], request['args'])
    assert readback['result']['status'] == 'selected'
    duplicate = await bridge('dispatch', {'action': 'session.select', 'id': 'worker-navigation',
        'args': {'id': destination}})
    assert duplicate['duplicate'] and duplicate['result'] == result['result']
    assert (await bridge('get_state', {}))['selectedSessionId'] == sid


async def test_explicit_navigation_and_canvas_remain_valid_but_cannot_move_consent(origin):
    app, sid, worker = origin
    bridge = main_bridge(worker)
    await action(bridge, 'view.update', clientId='old-browser', patch={'runtimeDraft': {'section': 'capture'}})
    assert app.clients.records['old-browser']['view']['runtimeDraft']['tab'] == 'computer'
    await action(bridge, 'canvas.show', kind='text', title='Saved', content='fixture')
    artifact = app._state['canvasArtifacts'][-1]['id']
    selected = await action(bridge, 'canvas.select', id=artifact, clientId='old-browser')
    assert selected['accepted'] and selected['state']['clientId'] == 'old-browser'
    assert set(selected['state']) == {'revision', 'sessionId', 'clientId', '_stateAccess'}
    assert selected['state']['sessionId'] == sid
    request, = selected['state']['_stateAccess']['readback']
    assert request == {'operation': 'get_state', 'args': {'path': '/canvas', 'clientId': 'old-browser'}}
    readback = await bridge(request['operation'], request['args'])
    assert next(row['value'] for row in readback['items'] if row['key'] == 'id') == artifact
    explicit = await bridge('get_state', {'clientId': 'old-browser'})
    assert explicit['canvas']['id'] == artifact
    assert explicit['computerVisual']['available'] is False
    await action(bridge, 'canvas.visibility', clientId='old-browser', sessionId=sid, canvasId=artifact, open=False)
    assert not app.clients.records['old-browser']['canvas']['open']
    grants = {client: deepcopy(visual.grant) for client, visual in app.computer_visual.clients.items()}
    for name in ('computer.visual.status', 'computer.visual.capture', 'computer.visual.revoke'):
        with pytest.raises(AppError, match='cannot be borrowed'):
            await action(bridge, name, sessionId=sid, clientId='old-browser')
    assert all(visual.grant and visual.pending is None for visual in app.computer_visual.clients.values())
    assert {client: visual.grant for client, visual in app.computer_visual.clients.items()} == grants


@pytest.mark.parametrize('clients', [[], [None], ['input-browser', None], ['input-browser', 'old-browser'], ['missing-browser']])
async def test_unknown_or_mixed_origins_do_not_fall_back_to_another_client(origin, clients):
    app, sid, worker = origin
    worker.context_inputs = [f'input-{i}' for i in range(len(clients))]
    worker.runtime.generation['input_ids'] = list(worker.context_inputs)
    worker.context_bindings = {f'input-{i}': {'clientId': value, 'targets': []} for i, value in enumerate(clients) if value is not None}
    bridge = main_bridge(worker)
    before = deepcopy(app.clients.records)
    state = await bridge('get_state', {})
    assert state['canvasContext']['clientId'] is None
    assert state['canvasContext']['status'] == 'detached'
    assert state['computerVisual']['available'] is False
    assert 'source' not in state['computerVisual'] and 'visibleUI' not in state
    assert state['inputOrigin']['status'] == 'unavailable'
    assert (await bridge('get_state', {'path': '/devices'}))['items'] == []
    # Explicit Canvas/state inspection is allowed, but is not that browser's consent.
    assert not (await bridge('get_state', {'clientId': 'old-browser'}))['computerVisual']['available']
    with pytest.raises(AppError):
        await action(bridge, 'view.update', patch={'runtimeDraft': {'section': 'capture'}})
    for explicit in (None, 'old-browser', 'input-browser'):
        with pytest.raises(AppError):
            await action(bridge, 'computer.visual.status', sessionId=sid, **({'clientId': explicit} if explicit else {}))
    assert app.clients.records == before


@pytest.mark.parametrize('end', ['disconnect', 'switch'])
async def test_stale_origin_fails_even_when_another_browser_is_connected(origin, end):
    app, sid, worker = origin
    if end == 'disconnect':
        for queue in list(app.queues):
            if app.queue_clients.get(queue) == 'input-browser':
                app.unsubscribe(queue)
    else:
        with app.clients.bind('input-browser'):
            await app.dispatch('session.create', {})
    bridge = main_bridge(worker)
    state = await bridge('get_state', {})
    assert state['canvasContext']['clientId'] is None and not state['computerVisual']['available']
    with pytest.raises(AppError, match='connected'):
        await action(bridge, 'computer.visual.status', sessionId=sid)
    with pytest.raises(AppError, match='connected'):
        await action(bridge, 'view.update', patch={'runtimeDraft': {'section': 'capture'}})


async def test_delegate_keeps_assigned_origin_after_parent_input_and_binding_changes(origin):
    app, sid, worker = origin
    delegated = worker.app_access_bridge(coordinator('actual-child'))
    worker.context_inputs = ['input-2']
    worker.runtime.generation = {'id': 'generation-2', 'input_ids': ['input-2']}
    worker.context_bindings = {'input-2': {'clientId': 'old-browser', 'targets': []}}
    assert (await main_bridge(worker)('get_state', {}))['canvasContext']['clientId'] == 'old-browser'
    assert (await delegated('get_state', {}))['canvasContext']['clientId'] == 'input-browser'
    result = await action(delegated, 'computer.visual.status', sessionId=sid)
    assert result['result']['source']['label'] == 'input-browser'


async def test_multiple_inputs_from_same_browser_and_context_filtering_still_work(origin):
    app, sid, worker = origin
    worker.context_inputs.append('input-2')
    worker.runtime.generation['input_ids'] = list(worker.context_inputs)
    worker.context_bindings['input-2'] = {'clientId': 'input-browser', 'targets': []}
    assert (await main_bridge(worker)('get_state', {}))['computerVisual']['available']
    delegated = worker.app_access_bridge(coordinator('actual-child'))
    captured = []
    async def inspect(operation, args):
        captured.append(args)
    worker.bridge = inspect
    await delegated('context.manifest', {})
    assert captured[-1]['_contextInputs'] == ['input-1', 'input-2']
    assert captured[-1]['_inputClients'] == ['input-browser', 'input-browser']
    assert all(value['targets'] == [] for value in captured[-1]['_contextBindings'])


async def test_saved_capture_read_cannot_cross_origin(origin):
    app, sid, worker = origin
    # A receipt identity from another browser is insufficient, even in one chat.
    app.computer_visual.clients['old-browser'].receipts['foreign-capture'] = {'id': 'foreign-capture'}
    with pytest.raises(AppError, match='another originating browser'):
        await main_bridge(worker)('computer.visual.read', {'captureId': 'foreign-capture'})


async def test_origin_capture_and_private_read_use_same_browser(origin):
    app, sid, worker = origin
    bridge = main_bridge(worker)
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'
    task = asyncio.create_task(action(bridge, 'computer.visual.capture', sessionId=sid))
    visual = app.computer_visual.clients['input-browser']
    for _ in range(100):
        if visual.pending or task.done():
            break
        await asyncio.sleep(.001)
    assert visual.pending and app.computer_visual.clients['old-browser'].pending is None
    with app.clients.bind('input-browser'):
        await visual.complete({'sessionId': sid, 'requestId': visual.pending['id'],
                               'grantId': visual.pending['grantId'], 'image': png, 'capturedAt': time.time()})
    result = await task
    retained = await bridge('computer.visual.read', {'captureId': result['result']['id']})
    assert retained['_image'] == png and retained['source']['label'] == 'input-browser'
