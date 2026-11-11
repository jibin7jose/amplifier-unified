"""Human-reviewed independent creation, exact receipts and no-replay boundaries."""
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from amplifier_web.service import AppError, AppService
from test_live_clients import command, snapshot
from test_service import Runtime

PREFIX = 'session.handoff.'


def test_action_descriptions_distinguish_independent_handoffs_from_commissioned_work():
    from amplifier_web.new_chat_handoffs import definitions
    from amplifier_web.service import schema, string

    actions = definitions(schema, string)
    start = actions[PREFIX + 'start'][0]
    propose = actions[PREFIX + 'propose'][0]
    read = actions[PREFIX + 'read'][0]
    assert 'does not commission a worker' in start
    assert 'use coordination.create for subordinate work' in start
    assert 'does not commission a worker or establish a completion return' in propose
    assert 'not that its work completed' in read
    assert 'not that its work completed or that its result will return to the source manager' in read


@pytest.fixture
async def handoff(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    await app.dispatch('session.create', {'title': 'Source'})
    source = app.state['selectedSessionId']
    await app.dispatch('session.create', {'title': 'Peer', 'select': False})
    peer = next(row['id'] for row in app.state['sessions'] if row['id'] != source)
    for client, sid in [('owner', source), ('other', peer)]:
        app.clients.attach(client)
        await command(app, client, 'session.select', {'id': sid})
        await command(app, client, 'view.update', {'patch': {'draft': 'Unsent ' + client}})
        await command(app, client, 'attachment.add', {'sessionId': sid, 'name': client + '.txt', 'base64': 'a2VlcA=='})
    queue = asyncio.Queue(maxsize=1)
    app.queues.add(queue); app.queue_clients[queue] = 'owner'; app.queue_sessions[queue] = source
    target = tmp_path / 'target'; target.mkdir()
    args = {'requestId': 'handoff-one', 'sessionId': source, 'clientId': 'owner',
            'workspace': str(target), 'title': 'Read target notes', 'text': 'Read README.md, then wait.',
            'bundle': 'anchors', 'selection': {'instance': 'synthetic', 'model': 'chosen'}}
    yield app, runtime, source, peer, args
    if not app.closed:
        await app.close()


async def propose(app, source, args):
    return (await app.app_bridge('dispatch', {'action': PREFIX + 'propose', 'args': args}, source))['result']


async def start(app, source, args, **transport):
    source_row = app._session(source, hydrate=False)
    source_row.setdefault('collaborationGeneration', {'id': 'human-turn', 'inputIds': ['user-request'], 'terminal': False})
    return (await app.app_bridge('dispatch', {'action': PREFIX + 'start', 'args': args,
        '_runtimeSessionId': source, '_generationId': 'human-turn',
        '_inputBindings': [{'inputId': 'user-request'}], **transport}, source))['result']


async def confirm(app, row, **kwargs):
    return (await command(app, 'owner', PREFIX + 'confirm', {
        'requestId': row['requestId'], 'reviewHash': row['reviewHash']}, **kwargs))['result']


async def finish(app, row):
    for _ in range(300):
        result = app.handoffs.load(row['requestId'])
        if result['status'] not in {'creating', 'created', 'delivering'}:
            return result
        await asyncio.sleep(.01)
    pytest.fail('Handoff did not settle: ' + json.dumps(result))


def private_view(app, client):
    state = snapshot(app, client)
    selected = state['selectedSessionId']
    row = next(s for s in state['sessions'] if s['id'] == selected)
    return selected, state['view']['draft'], copy.deepcopy(row['draftAttachments'])


async def test_direct_request_creates_and_starts_once_without_confirmation(handoff):
    app, runtime, source, peer, args = handoff
    before = {client: private_view(app, client) for client in ('owner', 'other')}
    row = await start(app, source, args)
    assert row['mode'] == 'direct' and row['status'] == 'creating'
    assert (await start(app, source, args))['targetSessionId'] == row['targetSessionId']
    result = await finish(app, row)
    assert result['status'] == result['delivery'] == 'accepted'
    target = app._session(result['targetSessionId'])
    assert target['workspace'] == args['workspace'] and target['bundle'] == args['bundle']
    assert target['selection'] == args['selection']
    assert not target.get('parentSessionId') and not target.get('collaboration')
    assert not target.get('assignmentId')
    assert target['sessionKind'] == 'root' and target['workingDirectory'] == args['workspace']
    assert (await start(app, source, args))['status'] == 'accepted'
    assert len(app.state['sessions']) == 3 and runtime.sent == [(target['id'], args['text'], result['inputId'])]
    assert len([m for m in target['messages'] if m.get('role') == 'user']) == 1
    assert snapshot(app, 'other')['handoffs'] == []
    for client in before:
        assert private_view(app, client) == before[client]
    with pytest.raises(AppError, match='different handoff'):
        await start(app, source, {**args, 'text': 'Changed task'})


async def test_direct_start_cannot_upgrade_pending_review_or_change_source(handoff):
    app, runtime, source, peer, args = handoff
    row = await propose(app, source, args)
    with pytest.raises(AppError, match='different handoff'):
        await start(app, source, args)
    with pytest.raises(AppError, match='calling conversation'):
        await start(app, peer, {**args, 'requestId': 'wrong-source'})
    assert app.handoffs.load(row['requestId'])['status'] == 'pending'
    assert len(app.state['sessions']) == 2 and not runtime.sent


async def test_direct_publication_failure_is_retained_without_retry_effects(handoff, monkeypatch):
    app, runtime, source, _, args = handoff
    with monkeypatch.context() as patch:
        patch.setattr(app, '_publish_changes', Mock(side_effect=RuntimeError('publication failed')))
        with pytest.raises(RuntimeError, match='publication failed'):
            await start(app, source, args)
    result = await start(app, source, args)
    assert result['status'] == 'unknown' and result['creation'] == result['delivery'] == 'not_started'
    assert not runtime.sent and len(app.state['sessions']) == 2


@pytest.mark.parametrize('transport', [
    {'_runtimeSessionId': 'actual-child'},
    {'_generationId': 'stale-generation'},
    {'_generationId': None},
    {'_runtimeSessionId': None},
])
async def test_direct_start_requires_actual_current_root_transport(handoff, transport):
    app, runtime, source, _, args = handoff
    with pytest.raises(AppError):
        await start(app, source, args, **transport)
    assert app.handoffs.load(args['requestId']) is None
    assert not runtime.sent and len(app.state['sessions']) == 2


async def test_direct_start_refuses_terminal_generation_but_retained_receipts_are_readable(handoff):
    app, runtime, source, _, args = handoff
    row = await start(app, source, args)
    await finish(app, row)
    app._session(source)['collaborationGeneration']['terminal'] = True
    with pytest.raises(AppError, match='current transport-bound root'):
        await start(app, source, {**args, 'requestId': 'later-request'})
    assert (await start(app, source, args))['status'] == 'accepted'
    assert len(runtime.sent) == 1


async def test_direct_start_queued_before_source_stop_has_no_effects(handoff):
    app, runtime, source, _, args = handoff
    async with app.lock:
        task = asyncio.create_task(start(app, source, args))
        await asyncio.sleep(0)
        source_row = app._session(source)
        source_row['interruptionRevision'] = source_row.get('interruptionRevision', 0) + 1
        source_row['status'] = 'stopping'
    with pytest.raises(AppError, match='source conversation was stopped'):
        await task
    assert app.handoffs.load(args['requestId']) is None
    assert not runtime.sent and len(app.state['sessions']) == 2


async def test_direct_start_rechecks_admission_after_waiting_for_lock(handoff):
    app, runtime, source, _, args = handoff
    live = True
    def admission():
        if not live:
            raise AppError('Connection revoked', 403)
    async with app.lock:
        task = asyncio.create_task(command(app, 'owner', PREFIX + 'start', args, admission_check=admission))
        await asyncio.sleep(0)
        live = False
    with pytest.raises(AppError, match='revoked'):
        await task
    assert app.handoffs.load(args['requestId']) is None
    assert not runtime.sent and len(app.state['sessions']) == 2


async def test_direct_start_cannot_escape_stopped_assignment(handoff, monkeypatch):
    app, runtime, source, _, args = handoff
    monkeypatch.setattr(app.assignments, 'blocked', lambda session: 'This assignment was stopped.')
    with pytest.raises(AppError, match='assignment was stopped'):
        await start(app, source, args)
    assert app.handoffs.load(args['requestId']) is None
    assert not runtime.sent and len(app.state['sessions']) == 2


async def test_review_create_and_send_once_preserves_clients_and_has_no_source_authority(handoff, monkeypatch):
    app, runtime, source, peer, args = handoff
    before = {client: private_view(app, client) for client in ('owner', 'other')}
    source_before = copy.deepcopy(app._session(source))
    publish = Mock(wraps=app._publish_changes); monkeypatch.setattr(app, '_publish_changes', publish)
    row = await propose(app, source, args)
    assert row['status'] == 'pending' and row['creation'] == row['delivery'] == 'not_started'
    assert not runtime.sent and len(app.state['sessions']) == 2
    assert publish.call_args.kwargs == {'sessions': (source,), 'globals': (), 'detail_only': True}
    assert 'owner' not in row and 'configurationHash' not in row
    assert snapshot(app, 'owner')['handoffs'] == [row]
    assert snapshot(app, 'other')['handoffs'] == []
    assert (await propose(app, source, args)) == row
    first = await confirm(app, row, command_id='confirm-first')
    assert first['status'] == 'creating'
    second = await confirm(app, row, command_id='confirm-second')
    assert second['targetSessionId'] == first['targetSessionId']
    completed = await finish(app, row)
    assert completed['status'] == completed['delivery'] == 'accepted'
    assert completed['creation'] == 'created'
    assert completed['revision'] > first['revision'] > row['revision']
    assert len(app.state['sessions']) == 3 and len(runtime.sent) == 1
    target = app._session(completed['targetSessionId'])
    assert runtime.sent[0] == (target['id'], args['text'], completed['inputId'])
    assert target['workspace'] == args['workspace'] and target['bundle'] == args['bundle']
    assert target['selection'] == args['selection']
    assert not target.get('parentSessionId') and not target.get('collaboration')
    assert not target.get('assignmentId')
    assert target['sessionKind'] == 'root' and target['workingDirectory'] == args['workspace']
    assert not (app.data_dir / 'sessions' / target['id'] / 'configuration.json').exists()
    assert len([m for m in target['messages'] if m.get('role') == 'user']) == 1
    assert app._session(source)['messages'] == source_before['messages']
    for client in before:
        assert private_view(app, client) == before[client]
    read = (await command(app, 'owner', PREFIX + 'read', {'requestId': row['requestId']}))['result']
    assert read['status'] == 'accepted'
    await confirm(app, row)
    assert len(runtime.sent) == 1


@pytest.mark.parametrize('action', ['confirm', 'cancel'])
async def test_agent_cannot_confirm_or_cancel_and_other_client_cannot_own_review(handoff, action):
    app, runtime, source, peer, args = handoff
    row = await propose(app, source, args)
    values = {'requestId': row['requestId'], **({'reviewHash': row['reviewHash']} if action == 'confirm' else {})}
    with pytest.raises(AppError, match='human client'):
        await app.app_bridge('dispatch', {'action': PREFIX + action, 'args': values}, source)
    with pytest.raises(AppError, match='another client'):
        await command(app, 'other', PREFIX + action, values)
    with pytest.raises(AppError, match='another client'):
        await command(app, 'other', PREFIX + 'read', {'requestId': row['requestId']})
    with pytest.raises(AppError, match='another conversation'):
        await app.app_bridge('dispatch', {'action': PREFIX + 'read', 'args': {'requestId': row['requestId']}}, peer)
    with pytest.raises(AppError, match='raw agent session.create'):
        await app.app_bridge('dispatch', {'action': 'session.create', 'args': {'workspace': args['workspace']}}, source)
    assert not runtime.sent and len(app.state['sessions']) == 2


async def test_proposal_payload_and_confirmation_cannot_change_review(handoff):
    app, runtime, source, peer, args = handoff
    row = await propose(app, source, args)
    with pytest.raises(AppError, match='different handoff'):
        await propose(app, source, {**args, 'text': 'Different work'})
    with pytest.raises(AppError, match='exact target'):
        await confirm(app, {**row, 'reviewHash': '0' * 64})
    await command(app, 'owner', 'session.select', {'id': peer})
    with pytest.raises(AppError, match='Open the source chat'):
        await confirm(app, row)
    assert not runtime.sent and len(app.state['sessions']) == 2


async def test_cancel_is_durable_terminal_and_never_authorizes_later_confirmation(handoff):
    app, runtime, source, _, args = handoff
    row = await propose(app, source, args)
    cancelled = (await command(app, 'owner', PREFIX + 'cancel', {'requestId': row['requestId']}))['result']
    assert cancelled['status'] == 'cancelled'
    assert (await confirm(app, row))['status'] == 'cancelled'
    assert not runtime.sent and len(app.state['sessions']) == 2


@pytest.mark.parametrize('drift', ['removed', 'replaced', 'symlink', 'settings', 'settings_symlink'])
async def test_changed_target_or_configuration_rejects_before_creation(handoff, tmp_path, drift):
    app, runtime, source, _, args = handoff
    target = Path(args['workspace'])
    if drift == 'settings_symlink':
        (target / '.amplifier').mkdir()
        original = tmp_path / 'settings-a.yaml'; original.write_text('mode: reviewed\n')
        replacement = tmp_path / 'settings-b.yaml'; replacement.write_text('mode: reviewed\n')
        (target / '.amplifier' / 'settings.yaml').symlink_to(original)
    row = await propose(app, source, args)
    if drift == 'removed':
        target.rmdir()
    elif drift in {'replaced', 'symlink'}:
        target.rename(tmp_path / 'old-target')
        if drift == 'replaced':
            target.mkdir()
        else:
            replacement = tmp_path / 'replacement'; replacement.mkdir(); target.symlink_to(replacement, target_is_directory=True)
    elif drift == 'settings':
        (target / '.amplifier').mkdir(); (target / '.amplifier' / 'settings.yaml').write_text('overrides:\n  tool-filesystem:\n    config:\n      denied_write_paths: [\"**\"]\n')
    else:
        link = target / '.amplifier' / 'settings.yaml'; link.unlink(); link.symlink_to(replacement)
    with pytest.raises(AppError, match='reviewed workspace or configuration'):
        await confirm(app, row)
    assert app.handoffs.load(row['requestId'])['status'] == 'pending'
    assert not runtime.sent and len(app.state['sessions']) == 2


async def test_global_update_pause_refuses_confirmation_without_effects(handoff):
    app, runtime, source, _, args = handoff
    row = await propose(app, source, args)
    app.state['updates']['phase'] = 'activating'
    with pytest.raises(AppError, match='update is activating'):
        await confirm(app, row)
    app.state['updates']['phase'] = 'idle'
    assert not runtime.sent and len(app.state['sessions']) == 2


@pytest.mark.parametrize('direct', [False, True])
async def test_stop_between_creation_and_input_prevents_delivery(handoff, monkeypatch, direct):
    app, runtime, source, _, args = handoff
    row = None if direct else await propose(app, source, args)
    original = app.dispatch
    async def dispatch(action, *values, **kwargs):
        result = await original(action, *values, **kwargs)
        if action == 'session.create':
            await original('conversation.stop', {'sessionId': result['sessionId']})
        return result
    monkeypatch.setattr(app, 'dispatch', dispatch)
    if direct:
        row = await start(app, source, args)
    else:
        await confirm(app, row)
    result = await finish(app, row)
    assert result['status'] == 'failed' and result['creation'] == 'created'
    assert result['delivery'] == 'not_started' and not runtime.sent
    assert not app._session(result['targetSessionId'])['messages']


async def test_revoked_confirmation_waiting_for_service_lock_has_no_effects(handoff):
    app, runtime, source, _, args = handoff
    row = await propose(app, source, args)
    live = True
    def admission():
        if not live:
            raise AppError('Connection revoked', 403)
    async with app.lock:
        task = asyncio.create_task(confirm(app, row, admission_check=admission))
        await asyncio.sleep(0)
        live = False
    with pytest.raises(AppError, match='revoked'):
        await task
    assert app.handoffs.load(row['requestId'])['status'] == 'pending'
    assert not runtime.sent and len(app.state['sessions']) == 2


@pytest.mark.parametrize('direct', [False, True])
async def test_unknown_delivery_never_replays_and_exact_late_receipt_reconciles(handoff, monkeypatch, direct):
    app, runtime, source, _, args = handoff
    async def uncertain(session, text, identity, emit):
        runtime.sent.append((session['id'], text, identity))
        raise TimeoutError('Lost acknowledgement')
    monkeypatch.setattr(runtime, 'send', uncertain)
    if direct:
        row = await start(app, source, args)
    else:
        row = await propose(app, source, args); await confirm(app, row)
    result = await finish(app, row)
    assert result['status'] == result['delivery'] == 'unknown'
    assert result['creation'] == 'created' and len(runtime.sent) == 1
    if direct:
        await start(app, source, args)
    else:
        await confirm(app, row)
    await command(app, 'owner', PREFIX + 'read', {'requestId': row['requestId']})
    assert len(runtime.sent) == 1
    await app.on_runtime_event('runtime.delivery', {'sessionId': result['targetSessionId'], 'inputId': result['inputId'], 'delivery': 'accepted'})
    accepted = app.handoffs.load(row['requestId'])
    assert accepted['status'] == accepted['delivery'] == 'accepted'
    assert accepted['revision'] > result['revision']
    assert len(runtime.sent) == 1


async def test_restart_keeps_unknown_partial_operation_without_creation_or_input_replay(handoff, tmp_path, monkeypatch):
    app, runtime, source, _, args = handoff
    row = await propose(app, source, args)
    entered = asyncio.Event()
    async def held(session, text, identity, emit):
        runtime.sent.append((session['id'], text, identity)); entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(runtime, 'send', held)
    await confirm(app, row); await asyncio.wait_for(entered.wait(), 3)
    await app.close()
    next_runtime = Runtime(); restored = AppService(app.data_dir, next_runtime, workspace=tmp_path)
    try:
        saved = restored.handoffs.load(row['requestId'])
        assert saved['status'] == 'unknown' and saved['creation'] == 'created'
        await command(restored, 'owner', PREFIX + 'read', {'requestId': row['requestId']})
        await confirm(restored, row)
        assert restored._session(saved['targetSessionId'], hydrate=False)['id'] == saved['targetSessionId']
        assert restored.db.execute('SELECT count(*) FROM commands WHERE id=?', (saved['createCommandId'],)).fetchone()[0] == 1
        assert not next_runtime.sent and not next_runtime.started
    finally:
        await restored.close()


async def test_pending_reviews_remain_bounded_and_visible_ahead_of_new_terminal_rows(handoff):
    app, runtime, source, _, args = handoff
    first = await propose(app, source, args)
    for index in range(8):
        row = await propose(app, source, {**args, 'requestId': f'cancel-{index}'})
        await command(app, 'owner', PREFIX + 'cancel', {'requestId': row['requestId']})
    rows = snapshot(app, 'owner')['handoffs']
    assert len(rows) == 8 and rows[0]['requestId'] == first['requestId']
    for index in range(7):
        await propose(app, source, {**args, 'requestId': f'pending-{index}'})
    with pytest.raises(AppError, match='Review or cancel'):
        await propose(app, source, {**args, 'requestId': 'overflow'})
    assert len(snapshot(app, 'owner')['handoffs']) == 8
    assert all(row['status'] == 'pending' for row in snapshot(app, 'owner')['handoffs'])
    assert not runtime.sent


async def test_review_normalizes_only_canonical_outer_whitespace(handoff):
    app, runtime, source, _, args = handoff
    raw = '\n  Read README.md.\n\nKeep this paragraph.  \n'
    row = await propose(app, source, {**args, 'text': raw})
    assert row['text'] == raw.strip()
    assert await propose(app, source, {**args, 'text': raw}) == row
    await confirm(app, row)
    result = await finish(app, row)
    assert result['status'] == 'accepted'
    assert runtime.sent[0][1] == row['text']


@pytest.mark.parametrize('drift', ['contents', 'symlink', 'removed'])
async def test_local_root_bundle_is_bound_without_preparation(handoff, tmp_path, drift):
    app, runtime, source, _, args = handoff
    target = Path(args['workspace']); (target / '.amplifier').mkdir()
    bundle = tmp_path / 'reviewed.yaml'; bundle.write_text('bundle:\n  name: reviewed\n')
    (target / '.amplifier' / 'settings.yaml').write_text('sources:\n  bundles:\n    reviewed: ' + str(bundle) + '\n')
    row = await propose(app, source, {**args, 'bundle': 'reviewed'})
    assert not runtime.started and not runtime.sent
    if drift == 'contents':
        bundle.write_text('bundle:\n  name: reviewed\ntools:\n  - module: changed\n')
    elif drift == 'symlink':
        replacement = tmp_path / 'other.yaml'; replacement.write_bytes(bundle.read_bytes())
        bundle.unlink(); bundle.symlink_to(replacement)
    else:
        bundle.unlink()
    with pytest.raises(AppError, match='reviewed workspace or configuration|chosen local bundle'):
        await confirm(app, row)
    assert not runtime.sent and len(app.state['sessions']) == 2


@pytest.mark.parametrize('scope', ['session_settings', 'mount_plan'])
async def test_unreviewed_session_configuration_before_send_refuses_delivery(handoff, monkeypatch, scope):
    from amplifier_web.shared_settings import settings_paths
    app, runtime, source, _, args = handoff
    row = await propose(app, source, args)
    original = app.dispatch
    async def dispatch(action, *values, **kwargs):
        # This boundary is after the first delivery guard, before canonical
        # send's locked admission. Model/control edits can settle while waiting.
        if action == 'conversation.send':
            file = (settings_paths(args['workspace'], session_id=row['targetSessionId'])['session']
                    if scope == 'session_settings' else app.data_dir / 'sessions' / row['targetSessionId'] / 'configuration.json')
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('overrides:\n  tool-filesystem:\n    config:\n      allowed_write_paths: [/changed]\n' if scope == 'session_settings' else '{}')
        return await original(action, *values, **kwargs)
    monkeypatch.setattr(app, 'dispatch', dispatch)
    await confirm(app, row)
    result = await finish(app, row)
    assert result['status'] == 'failed' and result['creation'] == 'created'
    assert result['delivery'] == 'not_started' and not runtime.sent
    assert not app._session(result['targetSessionId'])['messages']


async def test_detached_handoff_preserves_dirty_canvas_but_open_keeps_navigation_guard(handoff):
    app, runtime, source, _, args = handoff
    await command(app, 'owner', 'canvas.show', {'kind': 'text', 'content': 'Unfinished visible edit'})
    with app.clients.bind('owner'):
        current = app.canvas_views.summary('primary')
    target = {key: current[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await command(app, 'owner', 'canvas.views.dirty', {**target, 'dirty': True})
    before = copy.deepcopy(snapshot(app, 'owner')['canvasWorkspace'])
    row = await propose(app, source, args); await confirm(app, row)
    result = await finish(app, row)
    assert result['status'] == 'accepted' and len(runtime.sent) == 1
    assert snapshot(app, 'owner')['canvasWorkspace'] == before
    with pytest.raises(AppError, match='primary viewer edit'):
        await command(app, 'owner', 'session.select', {'id': result['targetSessionId']})
    assert snapshot(app, 'owner')['selectedSessionId'] == source


async def test_main_and_source_streams_receive_private_review_and_late_receipt(handoff, monkeypatch):
    app, runtime, source, _, args = handoff
    owner = asyncio.Queue(maxsize=1); other = asyncio.Queue(maxsize=1)
    for client, queue in [('owner', owner), ('other', other)]:
        app.queues.add(queue); app.queue_clients[queue] = client; app.queue_sessions[queue] = None
        snapshot(app, client)  # Warm the retained frame before the new DB row.
    row = await propose(app, source, args)
    assert owner.get_nowait()['handoffs'][0]['requestId'] == row['requestId']
    detail = next(q for q in app.queues if app.queue_sessions[q] == source)
    detail_frame = detail.get_nowait()
    assert detail_frame['sessions'][0]['id'] == source
    assert 'handoffs' not in detail_frame  # Session-detail readers do not inherit private client reviews.
    assert snapshot(app, 'other')['handoffs'] == []
    if not other.empty():
        assert other.get_nowait()['handoffs'] == []
    async def uncertain(session, text, identity, emit):
        runtime.sent.append((session['id'], text, identity))
        raise TimeoutError('Lost acknowledgement')
    monkeypatch.setattr(runtime, 'send', uncertain)
    await confirm(app, row); result = await finish(app, row)
    while not owner.empty():
        owner.get_nowait()
    await app.on_runtime_event('runtime.delivery', {'sessionId': result['targetSessionId'], 'inputId': result['inputId'], 'delivery': 'accepted'})
    assert owner.get_nowait()['handoffs'][0]['status'] == 'accepted'
    assert len(runtime.sent) == 1


@pytest.mark.parametrize('action', ['start', 'propose', 'read', 'confirm', 'cancel'])
async def test_restricted_companion_cannot_access_handoff_actions(aiohttp_client, tmp_path, action):
    from test_companion_authority import fixture, attach
    app, client, headers, _ = await fixture(aiohttp_client, tmp_path)
    native = {**headers, 'X-Amplifier-Client': 'native-review'}
    assert (await attach(client, native)).status == 200
    service = app['service']
    sessions_before = copy.deepcopy(service.state['sessions'])
    args = {'requestId': 'restricted-probe'}
    if action == 'confirm':
        args['reviewHash'] = '0' * 64
    if action in {'start', 'propose'}:
        args.update(sessionId='foreign', workspace=str(tmp_path), title='Forbidden', text='Never sent')
    response = await client.post('/api/actions', headers=native,
        json={'id': 'restricted-' + action, 'action': PREFIX + action, 'args': args})
    assert response.status == 403
    assert service.db.execute('SELECT count(*) FROM new_chat_handoffs').fetchone()[0] == 0
    assert service.state['sessions'] == sessions_before and not service.runtime.sent


async def test_resume_captures_exact_existing_reviews_without_future_cross_client_authority(handoff):
    app, runtime, source, _, args = handoff
    first = await propose(app, source, args)
    app.clients.attach('resumed', resume='owner')
    assert snapshot(app, 'resumed')['handoffs'] == [first]
    assert 'handoffReviews' not in snapshot(app, 'resumed')['client']
    second = await propose(app, source, {**args, 'requestId': 'later-owner-proposal'})
    assert [row['requestId'] for row in snapshot(app, 'resumed')['handoffs']] == [first['requestId']]
    with pytest.raises(AppError, match='another client'):
        await command(app, 'resumed', PREFIX + 'confirm', {'requestId': second['requestId'], 'reviewHash': second['reviewHash']})
    await command(app, 'resumed', PREFIX + 'confirm', {'requestId': first['requestId'], 'reviewHash': first['reviewHash']})
    completed = await finish(app, first)
    assert completed['status'] == 'accepted' and completed['executionClientId'] == 'resumed'
    await confirm(app, first)  # The old document only observes the same operation.
    assert len(runtime.sent) == 1
    app.clients.attach('again', resume='resumed')
    assert snapshot(app, 'again')['handoffs'][0]['status'] == 'accepted'
    assert len(runtime.sent) == 1


async def test_resume_during_delivery_keeps_same_receipt_and_execution_binding(handoff, monkeypatch):
    app, runtime, source, _, args = handoff
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(session, text, identity, emit):
        runtime.sent.append((session['id'], text, identity)); entered.set()
        await release.wait()
    monkeypatch.setattr(runtime, 'send', held)
    row = await propose(app, source, args); await confirm(app, row)
    await asyncio.wait_for(entered.wait(), 3)
    app.clients.attach('resumed-active', resume='owner')
    result = (await command(app, 'resumed-active', PREFIX + 'read', {'requestId': row['requestId']}))['result']
    assert result['reviewHash'] == row['reviewHash'] and result['creation'] == 'created'
    release.set(); completed = await finish(app, row)
    assert completed['executionClientId'] == 'owner' and completed['status'] == 'accepted'
    assert snapshot(app, 'resumed-active')['handoffs'][0]['status'] == 'accepted'
    await command(app, 'resumed-active', PREFIX + 'confirm', {'requestId': row['requestId'], 'reviewHash': row['reviewHash']})
    assert len(runtime.sent) == 1


async def test_navigation_before_repeated_resume_preserves_existing_pending_review(handoff):
    app, runtime, source, peer, args = handoff
    row = await propose(app, source, args)
    await command(app, 'owner', 'session.select', {'id': peer})
    app.clients.attach('resumed-away', resume='owner')
    assert snapshot(app, 'resumed-away')['handoffs'] == []
    app.clients.attach('resumed-again', resume='resumed-away')
    await command(app, 'resumed-again', 'session.select', {'id': source})
    assert snapshot(app, 'resumed-again')['handoffs'] == [row]
    await command(app, 'resumed-again', PREFIX + 'confirm', {'requestId': row['requestId'], 'reviewHash': row['reviewHash']})
    assert (await finish(app, row))['status'] == 'accepted'
    assert len(runtime.sent) == 1


async def test_resume_bound_never_drops_unfinished_refs_or_allows_future_lineage_access(handoff, monkeypatch):
    import amplifier_web.new_chat_handoffs as module
    monkeypatch.setattr(module, 'MAX_REVIEW_REFERENCES', 2)
    app, runtime, source, _, args = handoff
    async def uncertain(session, text, identity, emit):
        runtime.sent.append((session['id'], text, identity))
        raise TimeoutError('Lost acknowledgement')
    monkeypatch.setattr(runtime, 'send', uncertain)
    unknown = await propose(app, source, args); await confirm(app, unknown)
    assert (await finish(app, unknown))['status'] == 'unknown'
    pending = await propose(app, source, {**args, 'requestId': 'pending-review'})
    with pytest.raises(AppError, match='too many unfinished'):
        await propose(app, source, {**args, 'requestId': 'overflow-review'})
    app.clients.attach('limited-resume', resume='owner')
    refs = app.clients.records['limited-resume']['handoffReviews']
    assert {ref['requestId'] for ref in refs} == {unknown['requestId'], pending['requestId']}
    await command(app, 'limited-resume', PREFIX + 'cancel', {'requestId': pending['requestId']})
    # Repeated resumes retain the exact unknown receipt, never resend it.
    app.clients.attach('limited-again', resume='limited-resume')
    read = (await command(app, 'limited-again', PREFIX + 'read', {'requestId': unknown['requestId']}))['result']
    assert read['status'] == 'unknown' and len(runtime.sent) == 1


async def test_uncertain_receipt_stays_visible_until_source_unfinished_capacity_is_full(handoff, monkeypatch):
    app, runtime, source, _, args = handoff
    async def uncertain(session, text, identity, emit):
        runtime.sent.append((session['id'], text, identity))
        raise TimeoutError('Lost acknowledgement')
    monkeypatch.setattr(runtime, 'send', uncertain)
    row = await propose(app, source, args); await confirm(app, row)
    assert (await finish(app, row))['status'] == 'unknown'
    for index in range(7):
        await propose(app, source, {**args, 'requestId': f'new-pending-{index}'})
    with pytest.raises(AppError, match='Review or cancel'):
        await propose(app, source, {**args, 'requestId': 'hidden-unknown-overflow'})
    reviews = snapshot(app, 'owner')['handoffs']
    assert len(reviews) == 8
    assert any(review['requestId'] == row['requestId'] and review['status'] == 'unknown' for review in reviews)


async def test_confirm_publication_failure_retains_inspectable_receipt_without_scheduling(handoff, monkeypatch):
    app, runtime, source, _, args = handoff
    row = await propose(app, source, args)
    original = app._publish_changes
    def failure(**kwargs):
        monkeypatch.setattr(app, '_publish_changes', original)
        raise OSError('Injected publication failure')
    monkeypatch.setattr(app, '_publish_changes', failure)
    with pytest.raises(OSError, match='Injected'):
        await confirm(app, row)
    receipt = (await command(app, 'owner', PREFIX + 'read', {'requestId': row['requestId']}))['result']
    assert receipt['status'] == 'unknown' and receipt['creation'] == receipt['delivery'] == 'not_started'
    await confirm(app, row)
    await asyncio.sleep(0)
    assert len(app.state['sessions']) == 2 and not runtime.sent
