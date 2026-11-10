from copy import deepcopy
import json

import pytest

from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    await service.dispatch('session.create', {'title': 'First'})
    await service.dispatch('session.create', {'title': 'Second'})
    service.clients.attach('one')
    service.clients.attach('two')
    yield service
    await service.close()


async def select(app, sid, **kwargs):
    with app.clients.bind('one'):
        return await app.dispatch('session.select', {'id': sid}, **kwargs)


async def test_selection_is_local_durable_and_keeps_catalog_projections(app, monkeypatch):
    first, second = [next(row['id'] for row in app._state['sessions'] if row['title'] == title) for title in ('First', 'Second')]
    with app.clients.bind('one'):
        app.clients.draft(first, 'Private first draft')
        app.clients.draft(second, 'Private second draft')
        app.browser_state()
        own = app.subscribe()
    with app.clients.bind('two'):
        other_frame = app.browser_state()
        other = app.subscribe()
    index = app.projections.sessions(app.state)
    histories = deepcopy(app._state['sessions'])
    def forbidden(*args, **kwargs):
        pytest.fail('Selecting must not save or hydrate the full history/catalog')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', forbidden)
        patch.setattr(app.cold_display, 'hydrate', forbidden)
        patch.setattr(app.projections, 'invalidate', forbidden)
        result = await select(app, first, command_id='select-first')
    assert result['state']['selectedSessionId'] == first
    assert result['state']['view']['draft'] == 'Private first draft'
    assert own.get_nowait()['selectedSessionId'] == first
    assert other.empty()
    assert app.projections.sessions(app.state) is index
    assert app._client_snapshots['two']['sessions'] is other_frame['sessions']
    assert app._state['sessions'] == histories
    assert app.clients.records['two']['selectedSessionId'] == second
    stored = json.loads(app.db.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0])
    assert stored['selectedSessionId'] == first
    assert stored['drafts'][second] == 'Private second draft'
    app.unsubscribe(own); app.unsubscribe(other)
    await select(app, second)
    duplicate = await select(app, first, command_id='select-first')
    assert duplicate['duplicate']
    assert duplicate['state']['selectedSessionId'] == second


async def test_selection_failure_restores_client_and_receipt(app, monkeypatch):
    first = app._state['sessions'][0]['id']
    previous = deepcopy(app.clients.records['one'])
    revision = app._state['revision']
    def fail(*args, **kwargs):
        raise OSError('fixture disk error')
    with monkeypatch.context() as patch:
        patch.setattr(app.clients, 'save', fail)
        with pytest.raises(OSError):
            await select(app, first, command_id='failed-selection')
    assert app.clients.records['one'] == previous
    assert app._state['revision'] == revision
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('failed-selection',)).fetchone() is None
    assert (await select(app, first, command_id='failed-selection'))['accepted']


async def test_selection_keeps_pending_runtime_publication(app):
    first = app._state['sessions'][0]['id']
    app._publish_progress(session_ids={first}, detail_only=True, record_only=True)
    pending = app._progress_publish_task
    await select(app, first, include_state=False)
    assert app._progress_dirty
    assert app._progress_publish_task is pending
    assert not pending.cancelled()


async def test_dirty_canvas_still_rejects_selection(app, monkeypatch):
    first = app._state['sessions'][0]['id']
    previous = app.clients.records['one']['selectedSessionId']
    def dirty(*args):
        raise AppError('Finish the viewer edit', 409)
    monkeypatch.setattr(app.canvas_views, 'guard_transition', dirty)
    with pytest.raises(AppError, match='viewer edit'):
        await select(app, first)
    assert app.clients.records['one']['selectedSessionId'] == previous


async def test_missing_workspace_uses_normal_registration_path(app):
    from amplifier_web.client_navigation import accepts
    row = app._state['sessions'][0]
    with app.clients.bind('one'):
        assert accepts(app, row)
        assert not accepts(app, {**row, 'workspaceId': 'unregistered', 'workspace': '/unregistered'})
        app.state['canvas'] = {'kind': 'text', 'id': 'saved-artifact'}
        assert not accepts(app, row)


async def test_unchanged_history_check_reads_only_identity(app, monkeypatch):
    row = app._state['sessions'][0]
    row.update(nativeProject='fixture', nativeIdentity='native-id', historyLoaded=True, nativeRevision=[1, 2])
    seen = []
    def revision(source):
        seen.append(source)
        return [1, 2]
    def forbidden(*args, **kwargs):
        pytest.fail('An unchanged history check must not hydrate/copy display payloads')
    monkeypatch.setattr('amplifier_web.automatic_history.revision', revision)
    resolve = app._session
    def metadata_only(identity, *, hydrate=True):
        assert hydrate is False, 'An unchanged check may resolve metadata, never hydrate display'
        return resolve(identity, hydrate=False)
    with monkeypatch.context() as patch:
        patch.setattr(app, '_session', metadata_only)
        patch.setattr(app.cold_display, 'hydrate', forbidden)
        patch.setattr(app, '_publish', forbidden)
        await app.history.refresh_session(row['id'])
    assert len(seen) == 1
    assert set(seen[0]) == {'id', 'nativeProject', 'nativeIdentity', 'runtimeSessionId'}


async def test_saved_canvas_restores_without_shared_writes(app, monkeypatch):
    first, second = [next(row['id'] for row in app._state['sessions'] if row['title'] == title) for title in ('First', 'Second')]
    with app.clients.bind('one'):
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Keep this saved artifact'})
        saved_id = app.state['canvas']['id']
        shared = deepcopy(app._state['canvasArtifacts'])
        app.state['canvas']['view'] = {'scrollTop': 42}
        def forbidden(*args, **kwargs):
            pytest.fail('Saved canvas navigation must not rewrite shared artifacts')
        with monkeypatch.context() as patch:
            patch.setattr(app, '_save', forbidden)
            await app.dispatch('session.select', {'id': first})
            await app.dispatch('session.select', {'id': second})
        assert app.state['canvas']['id'] == saved_id
        assert app.state['canvas']['content'] == 'Keep this saved artifact'
        assert app.state['canvas']['view']['scrollTop'] == 42
        assert app._state['canvasArtifacts'] == shared


async def navigation(app, source, destination, *, command='agent-navigation', client='one', transport=None):
    return await app.app_bridge('dispatch', {'action': 'session.select', 'id': command,
        'responseMode': 'receipt', 'args': {'id': destination, **({'clientId': client} if client else {})},
        **(transport or {})}, source)


async def test_agent_navigation_binds_client_receipt_not_calling_snapshot(app, monkeypatch):
    first, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                     for title in ('First', 'Second')]
    with app.clients.bind('one'):
        app.clients.draft(source, 'Unsubmitted source draft')
        app.clients.draft(first, 'Private destination draft')
        own = app.subscribe()
    with app.clients.bind('two'):
        other = app.subscribe()
    before = deepcopy(app.clients.records['two'])
    sessions = deepcopy(app._state['sessions'])
    try:
        result = await navigation(app, source, first)
        assert result['accepted']
        proof = result['result']
        assert proof['clientId'] == 'one' and proof['sourceSessionId'] == source
        assert proof['selectedSessionId'] == first and proof['rendering'] == 'unverified'
        assert proof['selectionRevision'] == app.clients.records['one']['selectionRevision']
        with pytest.raises(AppError, match='different contents'):
            await navigation(app, first, first)
        assert 'selectedSessionId' not in result['state']  # caller != navigated client
        assert 'Private destination draft' not in json.dumps(result)
        assert own.get_nowait()['selectedSessionId'] == first and other.empty()
        assert app.clients.records['two'] == before and app._state['sessions'] == sessions
        assert app._state['selectedSessionId'] == source
        assert app.clients.records['one']['drafts'][source] == 'Unsubmitted source draft'
        # get_state still truthfully describes the caller, not another chat's private UI.
        caller_state = await app.app_bridge('get_state', {}, source)
        assert caller_state['selectedSessionId'] == source
        request = result['state']['_stateAccess']['readback'][0]
        readback = await app.app_bridge(request['operation'], request['args'], source)
        assert readback['result']['status'] == 'selected'
        assert readback['result']['selectedSessionId'] == first
        # Exact transport recovery reads the receipt; it never navigates again.
        with app.clients.bind('one'):
            await app.dispatch('session.select', {'id': source})
        duplicate = await navigation(app, source, first)
        assert duplicate['duplicate'] and duplicate['result'] == proof
        assert app.clients.records['one']['selectedSessionId'] == source
        changed = await app.app_bridge(request['operation'], request['args'], source)
        assert changed['result']['status'] == 'changed'
        app.unsubscribe(own)
        disconnected = await app.app_bridge(request['operation'], request['args'], source)
        assert disconnected['result']['status'] == 'disconnected'
        with pytest.raises(AppError, match='No selection receipt'):
            await app.app_bridge(request['operation'], request['args'], first)
    finally:
        app.unsubscribe(own); app.unsubscribe(other)


@pytest.mark.parametrize('kind', ['detached', 'ambiguous', 'foreign', 'saved-disconnected'])
async def test_agent_navigation_does_not_guess_a_client(app, kind):
    destination, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                           for title in ('First', 'Second')]
    with app.clients.bind('one'):
        own = app.subscribe()
    with app.clients.bind('two'):
        other = app.subscribe()
    if kind == 'saved-disconnected':
        app.unsubscribe(own)
    if kind == 'foreign':
        with app.clients.bind('one'):
            await app.dispatch('session.select', {'id': destination})
    before = deepcopy(app.clients.records)
    try:
        with pytest.raises(AppError) as error:
            await navigation(app, source, destination, client='one' if kind in {'foreign', 'saved-disconnected'} else None,
                             transport={'_inputClients': []})
        assert error.value.code == 'ui_client_required'
        assert app.clients.records == before
    finally:
        app.unsubscribe(own); app.unsubscribe(other)


async def test_agent_navigation_uses_origin_and_rechecks_after_lock(app, monkeypatch):
    import asyncio
    destination, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                           for title in ('First', 'Second')]
    with app.clients.bind('one'):
        own = app.subscribe()
    try:
        result = await navigation(app, source, destination, client=None,
            transport={'_inputClients': ['one']})
        assert result['result']['clientId'] == 'one'
        with app.clients.bind('one'):
            await app.dispatch('session.select', {'id': source})
        entered = asyncio.Event()
        dispatch = app.dispatch
        async def observed(action, *args, **kwargs):
            if action == 'session.select':
                entered.set()
            return await dispatch(action, *args, **kwargs)
        monkeypatch.setattr(app, 'dispatch', observed)
        async with app.lock:
            pending = asyncio.create_task(navigation(app, source, destination, command='wait-navigation'))
            await asyncio.wait_for(entered.wait(), 2)
            app.clients.records['one']['selectedSessionId'] = destination
        with pytest.raises(AppError, match='connected client'):
            await pending
    finally:
        app.unsubscribe(own)


async def test_agent_navigation_keeps_dirty_canvas(app):
    destination, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                           for title in ('First', 'Second')]
    with app.clients.bind('one'):
        own = app.subscribe()
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Unsaved editor'})
        view = app.canvas_views.summary('primary')
        target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
        await app.dispatch('canvas.views.dirty', {**target, 'dirty': True})
    before = deepcopy(app.clients.records['one'])
    try:
        with pytest.raises(AppError, match='viewer edit'):
            await navigation(app, source, destination)
        assert app.clients.records['one'] == before
    finally:
        app.unsubscribe(own)


async def test_raw_agent_selection_cannot_change_default_or_borrow_a_client(app):
    destination, source = [next(row['id'] for row in app._state['sessions'] if row['title'] == title)
                           for title in ('First', 'Second')]
    with app.clients.bind('one'):
        own = app.subscribe()
    before = deepcopy(app.clients.records)
    try:
        with pytest.raises(AppError, match='explicit connected'):
            await app.dispatch('session.select', {'id': destination}, origin='agent', caller_session_id=source)
        with app.clients.bind('one'):
            with pytest.raises(AppError, match='registered calling'):
                await app.dispatch('session.select', {'id': destination}, origin='agent')
            with pytest.raises(AppError, match='attached client'):
                await app.dispatch('session.select', {'id': destination, 'clientId': 'two'},
                                   origin='agent', caller_session_id=source)
        assert app._state['selectedSessionId'] == source
        assert app.clients.records == before
    finally:
        app.unsubscribe(own)
