import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from amplifier_web.message_delivery import saved_delivery
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppService, AppError
from amplifier_web.session_files import sessions_dir
from test_service import Runtime


class RecoveryRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.evidence = 'unknown'
        self.retries = []

    async def delivery(self, session, input_id):
        return self.evidence

    async def retry(self, session, text, input_id, emit):
        self.retries.append(copy.deepcopy(session))
        await self.send(session, text, input_id, emit)
        return {'accepted': True}


class HeldPostRuntime:
    """Hold the real dispatch outside its lock; no worker or model is started."""
    def __init__(self):
        self.sent, self.entered, self.release, self.outcomes = [], {}, {}, {}
        self.steered = []
        self.evidence = 'unknown'

    def expect(self, identity, outcome='accepted'):
        self.entered[identity], self.release[identity] = asyncio.Event(), asyncio.Event()
        self.outcomes[identity] = outcome

    async def send(self, session, text, input_id, emit):
        from amplifier_web.runtime import SessionInUseError, RuntimeOperationPending, RuntimeStartupError
        self.sent.append((session['id'], text, input_id))
        self.entered[input_id].set()
        await self.release[input_id].wait()
        outcome = self.outcomes[input_id]
        if outcome == 'rejected':
            raise SessionInUseError({'app': 'amplifier-cli', 'pid': 123})
        if outcome == 'unknown':
            raise RuntimeOperationPending('Synthetic held acknowledgement')
        if outcome == 'failed':
            raise RuntimeStartupError('Synthetic startup failure')
        return {'accepted': True}

    async def steer(self, session, text, input_id, emit):
        self.steered.append(input_id)
        await self.send(session, text, input_id, emit)
        return {'accepted': self.outcomes[input_id] != 'held', 'disposition':
                'held' if self.outcomes[input_id] == 'held' else 'queued'}

    async def delivery(self, session, input_id):
        return self.evidence

    async def close(self):
        pass


@pytest.fixture
async def held_post(tmp_path, monkeypatch):
    from amplifier_web import chat_navigation
    runtime = HeldPostRuntime()
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    await app.dispatch('session.create', {'title': 'Old root'})
    root = app._session()
    root.update(recentActivityAt=10, navigationActivityAt=10)
    clock = [20]
    monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: clock[0]))
    yield app, runtime, clock
    if not getattr(app, '_test_closed', False):
        await app.close()


async def start_post(app, runtime, identity, outcome='accepted', *, origin='ui', caller_session_id=None, **args):
    runtime.expect(identity, outcome)
    task = asyncio.create_task(app.dispatch('conversation.send',
        {'sessionId': app._session()['id'], 'text': identity, **args}, origin=origin, command_id=identity,
        caller_session_id=caller_session_id))
    entered = asyncio.create_task(runtime.entered[identity].wait())
    try:
        # Admission can fail before runtime entry: collect the real exception,
        # not a five-second timeout hiding an unauthorized fixture input.
        done, _ = await asyncio.wait({task, entered}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
        if entered not in done:
            await asyncio.wait_for(entered, 5)
        return task
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    finally:
        entered.cancel()
        await asyncio.gather(entered, return_exceptions=True)


async def reject_post(runtime, identity, task):
    runtime.release[identity].set()
    with pytest.raises(AppError) as exc:
        await task
    assert exc.value.code == 'session_busy'


def post_receipt(app, identity):
    return json.loads(app.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()[0])


@pytest.mark.parametrize('text', [
    '    print("indented")\n    return 1\n',
    'First line  \nSecond line  ',
    '\n\n```python\n    print("fenced")\n\n```\n\n',
    '\n  caf\u00e9 e\u0301 \u6771\u4eac  \n\n',
    '\r\n```text\r\n    literal\r\n```\r\n',
    'Ordinary message',
], ids=['indented-code', 'hard-break', 'fence-blank-lines', 'unicode', 'crlf', 'ordinary'])
async def test_send_preserves_exact_text_in_message_storage_and_runtime_payload(held_post, text):
    """Service/storage boundary only; the synthetic runtime does not mount the native loop."""
    from amplifier_web.session_projection import view_path
    app, runtime, _ = held_post
    root = app._session()
    identity = 'exact-text'
    args = {'sessionId': root['id'], 'text': text}
    original_args = copy.deepcopy(args)
    app.state['view']['draft'] = text
    runtime.expect(identity)
    runtime.release[identity].set()

    receipt = await app.dispatch('conversation.send', args, command_id=identity)

    assert receipt['accepted'] and receipt['delivery'] == 'accepted'
    assert args == original_args
    assert runtime.sent == [(root['id'], text, identity)]
    assert len(root['messages']) == 1
    message = root['messages'][0]
    assert message['text'] == text and message['inputId'] == identity
    assert message['delivery']['status'] == 'accepted'
    saved = json.loads(view_path(app.data_dir, root).read_text())
    assert saved['messages'][0]['text'] == text
    assert app.state['view']['draft'] == ''

    duplicate = await app.dispatch('conversation.send', args, command_id=identity)
    assert duplicate['duplicate'] and duplicate['delivery'] == 'accepted'
    # Even a whitespace-only payload change must retain the original fingerprint fence.
    with pytest.raises(AppError, match='different contents'):
        await app.dispatch('conversation.send', {**args, 'text': text + ' '}, command_id=identity)
    assert runtime.sent == [(root['id'], text, identity)] and len(root['messages']) == 1


@pytest.mark.parametrize('text', ['', '   ', '\t\r\n \n', '\u00a0\u2003'],
                         ids=['empty', 'spaces', 'tabs-newlines', 'unicode-space'])
async def test_send_rejects_blank_text_without_consuming_draft_or_unrequested_attachment(held_post, text):
    app, runtime, _ = held_post
    root = app._session()
    app.state['view']['draft'] = text
    await app.dispatch('attachment.add', {'sessionId': root['id'], 'name': 'unsent.txt', 'base64': 'aGVsbG8='})
    attachments = copy.deepcopy(app.clients.attachments(root))
    before = copy.deepcopy(root)

    with pytest.raises(AppError, match='Enter a message or attach a file'):
        await app.dispatch('conversation.send', {'sessionId': root['id'], 'text': text},
                           command_id='blank-text')

    assert root == before and not runtime.sent
    assert app.state['view']['draft'] == text
    assert app.clients.attachments(root) == attachments
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('blank-text',)).fetchone() is None


@pytest.mark.parametrize('text', ['', ' \t\r\n'], ids=['empty', 'whitespace'])
async def test_send_blank_text_with_attachment_retains_existing_fallback(held_post, text):
    app, runtime, _ = held_post
    root = app._session()
    await app.dispatch('attachment.add', {'sessionId': root['id'], 'name': 'reference.txt', 'base64': 'aGVsbG8='})
    attachment = copy.deepcopy(app.clients.attachments(root)[0])
    identity = 'attachment-only'
    runtime.expect(identity)
    runtime.release[identity].set()

    receipt = await app.dispatch('conversation.send', {
        'sessionId': root['id'], 'text': text, 'attachmentIds': [attachment['id']]},
        command_id=identity)

    expected = 'Please review the attached files.'
    assert receipt['delivery'] == 'accepted'
    assert runtime.sent == [(root['id'], expected, identity)]
    assert root['messages'][0]['text'] == expected
    assert root['messages'][0]['attachments'] == [attachment]
    assert app.clients.attachments(root) == []


@pytest.mark.parametrize('guard', ['generation', 'configuration-revision', 'configuration-busy', 'file-access'])
async def test_send_exact_text_still_obeys_pre_admission_guards(held_post, guard):
    app, runtime, _ = held_post
    root = app._session()
    text = '    guarded code  \n\n'
    args = {'sessionId': root['id'], 'text': text}
    if guard == 'generation':
        args['expectedGenerationId'] = 'ended-generation'
    elif guard == 'configuration-revision':
        args['expectedConfigurationRevision'] = root.get('nextMessageRevision', 0) + 1
    elif guard == 'configuration-busy':
        root['configurationBusy'] = True
    else:
        root['fileAccessPending'] = {'status': 'pending'}
    app.state['view']['draft'] = text
    before = copy.deepcopy(root)

    with pytest.raises(AppError) as exc:
        await app.dispatch('conversation.send', args, command_id='guarded-text')

    assert exc.value.status == 409
    assert root == before and not runtime.sent
    assert app.state['view']['draft'] == text
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('guarded-text',)).fetchone() is None


@pytest.mark.parametrize('preserve_draft', [False, True], ids=['legacy-draft', 'browser-draft'])
@pytest.mark.parametrize('outcome', ['accepted', 'unknown'])
async def test_send_exact_text_delayed_receipts_preserve_draft_ownership_and_never_replay(
        held_post, preserve_draft, outcome):
    from amplifier_web.runtime import RuntimeOperationPending
    app, runtime, _ = held_post
    root = app._session()
    sid, identity = root['id'], 'delayed-text'
    text = '    caf\u00e9  \n\n'
    args = {'sessionId': sid, 'text': text, 'preserveDraft': preserve_draft}
    app.clients.attach('sender')
    app.clients.attach('other')
    with app.clients.bind('other'):
        await app.dispatch('view.update', {'patch': {'draft': 'Other browser draft'}})
        await app.dispatch('attachment.add', {'sessionId': sid, 'name': 'other.txt', 'base64': 'aGVsbG8='})
        other_attachments = copy.deepcopy(app.clients.attachments(root))
    with app.clients.bind('sender'):
        await app.dispatch('view.update', {'patch': {'draft': text}})
        await app.dispatch('attachment.add', {'sessionId': sid, 'name': 'sent.txt', 'base64': 'aGVsbG8='})
        submitted_attachment = copy.deepcopy(app.clients.attachments(root)[0])
        args['attachmentIds'] = [submitted_attachment['id']]
        task = await start_post(app, runtime, identity, outcome, **args)
    try:
        assert root['messages'][0]['text'] == text
        assert root['messages'][0]['delivery']['status'] == 'sending'
        with app.clients.bind('sender'):
            # A modern browser may have retyped the identical text after submit.
            newer_text = text if preserve_draft else 'Newer draft  \n'
            await app.dispatch('view.update', {'patch': {'draft': newer_text}})
            await app.dispatch('attachment.add', {'sessionId': sid, 'name': 'newer.txt', 'base64': 'aGVsbG8='})
            newer_attachment = copy.deepcopy(app.clients.attachments(root)[-1])
        with app.clients.bind('other'):
            duplicate = await app.dispatch('conversation.send', args, command_id=identity)
        assert duplicate['duplicate'] and duplicate['delivery'] == 'sending'
        assert runtime.sent == [(sid, text, identity)] and len(root['messages']) == 1
        runtime.release[identity].set()
        if outcome == 'unknown':
            with pytest.raises(RuntimeOperationPending):
                await task
            assert root['messages'][0]['delivery']['status'] == 'unknown'
            with app.clients.bind('sender'):
                assert app.clients.attachments(root) == [submitted_attachment, newer_attachment]
            checked = await app.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': identity})
            assert checked['result']['delivery'] == 'unknown'
            cached = await app.dispatch('conversation.send', args, command_id=identity)
            assert cached['duplicate'] and cached['delivery'] == 'unknown'
        else:
            assert (await task)['delivery'] == 'accepted'

        # Late/duplicate evidence reconciles this input, never submits it again.
        for _ in range(2):
            await app.on_runtime_event('runtime.delivery', {
                'sessionId': sid, 'inputId': identity, 'delivery': 'accepted'})
        with app.clients.bind('other'):
            cached = await app.dispatch('conversation.send', args, command_id=identity)
            assert cached['duplicate'] and cached['delivery'] == 'accepted'
            assert app.clients.attachments(root) == other_attachments
            assert app.state['view']['draft'] == 'Other browser draft'
        with app.clients.bind('sender'):
            assert app.clients.attachments(root) == [newer_attachment]
            assert app.state['view']['draft'] == newer_text
        assert runtime.sent == [(sid, text, identity)] and len(root['messages']) == 1
        assert root['messages'][0]['text'] == text
        assert root['messages'][0]['delivery']['status'] == 'accepted'
        assert root['messages'][0]['attachments'] == [submitted_attachment]
    finally:
        runtime.release[identity].set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('pending', [None, False, True])
async def test_rejected_ui_post_restores_exact_presence_and_draft_references(held_post, pending):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, _ = held_post
    root = app._session()
    root.pop('navigationActivityAt')
    if pending is not None:
        root['navigationActivityPending'] = pending
    app.state['view']['draft'] = 'Unsent next thought'
    await app.dispatch('attachment.add', {'sessionId': root['id'], 'name': 'kept.txt', 'base64': 'aGVsbG8='})
    references = copy.deepcopy(app.clients.attachments(root))
    before = {key: copy.deepcopy(root[key]) for key in
              ('navigationActivityAt', 'navigationActivityPending', 'recentActivityAt') if key in root}
    task = await start_post(app, runtime, 'A', 'rejected', attachmentIds=[references[0]['id']])
    assert navigation_activity(root) == 20
    await reject_post(runtime, 'A', task)
    assert {key: root[key] for key in before} == before
    assert 'navigationActivityAt' not in root
    assert ('navigationActivityPending' in root) == (pending is not None)
    assert root['messages'] == []
    assert app.clients.attachments(root) == references
    assert app.state['view']['draft'] == 'Unsent next thought'
    assert post_receipt(app, 'A')['navigationPost']['disposition'] == 'rejected'
    assert 'navigationPostAdmissions' not in root


@pytest.mark.parametrize('completion_order', [('A', 'B'), ('B', 'A')])
@pytest.mark.parametrize('equal_clocks', [False, True])
async def test_overlapping_rejected_posts_cannot_resurrect_predecessor(held_post, completion_order, equal_clocks):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    root['navigationActivityPending'] = False
    a = await start_post(app, runtime, 'A', 'rejected')
    if not equal_clocks:
        clock[0] = 30
    b = await start_post(app, runtime, 'B', 'rejected')
    assert navigation_activity(root) == clock[0]
    tasks = {'A': a, 'B': b}
    for identity in completion_order:
        await reject_post(runtime, identity, tasks[identity])
    assert navigation_activity(root) == root['recentActivityAt'] == 10
    assert root['navigationActivityPending'] is False and root['messages'] == []
    assert 'navigationPostAdmissions' not in root
    assert all(post_receipt(app, identity)['navigationPost']['disposition'] == 'rejected' for identity in tasks)


@pytest.mark.parametrize('newer', ['accepted', 'ready', 'attention'])
async def test_old_rejection_cannot_overwrite_new_post_or_settlement_at_equal_clock(held_post, newer):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    a = await start_post(app, runtime, 'A', 'rejected')
    b = await start_post(app, runtime, 'B')
    runtime.release['B'].set()
    await b
    if newer != 'accepted':
        kind, payload = ('runtime.status', {'status': 'idle'}) if newer == 'ready' else (
            'approval.requested', {'id': 'approval', 'tool': 'write_file'})
        await app.on_runtime_event(kind, {'sessionId': root['id'], **payload})
    before = {key: copy.deepcopy(root[key]) for key in ('navigationActivityAt', 'navigationActivityPending')
              if key in root}
    await reject_post(runtime, 'A', a)
    assert navigation_activity(root) == clock[0]
    assert {key: root[key] for key in before} == before
    assert ('navigationActivityPending' in root) == ('navigationActivityPending' in before)
    assert [row['inputId'] for row in root['messages']] == ['B']
    assert post_receipt(app, 'B')['navigationPost']['disposition'] == 'accepted'


async def test_preinsert_refusals_and_duplicate_payload_never_promote(held_post):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    for args in ({'text': ''}, {'text': 'X', 'attachmentIds': ['missing']},
                 {'text': 'X', 'expectedGenerationId': 'ended'},
                 {'text': 'X', 'inputOrigin': 'ui'}):
        with pytest.raises(AppError):
            await app.dispatch('conversation.send', {'sessionId': root['id'], **args}, command_id='bad')
        assert navigation_activity(root) == 10 and not root['messages']
    with pytest.raises(AppError):
        await app.dispatch('conversation.send', {'sessionId': root['id'], 'text': 'Revision'},
                           expected_revision=app.state['revision'] - 1)
    task = await start_post(app, runtime, 'A')
    clock[0] = 99
    duplicate = await app.dispatch('conversation.send', {'sessionId': root['id'], 'text': 'A'}, command_id='A')
    assert duplicate['duplicate'] and navigation_activity(root) == 20
    with pytest.raises(AppError, match='different contents'):
        await app.dispatch('conversation.send', {'sessionId': root['id'], 'text': 'B'}, command_id='A')
    assert len(root['messages']) == len(runtime.sent) == 1 and navigation_activity(root) == 20
    runtime.release['A'].set()
    await task


@pytest.mark.parametrize('outcome', ['unknown', 'failed'])
async def test_retained_ui_post_restart_passive_check_and_exact_retry_never_repromote(held_post, tmp_path, outcome):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    sid = root['id']
    task = await start_post(app, runtime, 'A', outcome)
    runtime.release['A'].set()
    with pytest.raises(Exception):
        await task
    stable = navigation_activity(root)
    fence = root['messages'][-1]['navigationPost']['fence']
    assert root['messages'][-1]['navigationPost']['disposition'] == 'retained'
    assert root['messages'][-1]['delivery']['status'] == outcome
    await app.close()
    app._test_closed = True
    clock[0] = 200
    replacement = HeldPostRuntime()
    restored = AppService(tmp_path / 'app', replacement, workspace=tmp_path)
    try:
        root = restored._session(sid)
        assert navigation_activity(root) == stable and not replacement.sent
        assert root['messages'][-1]['navigationPost']['fence'] == fence
        cached = await restored.dispatch('conversation.send', {'sessionId': sid, 'text': 'A'}, command_id='A')
        assert cached['duplicate'] and not replacement.sent
        replacement.evidence = 'accepted'
        checked = await restored.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': 'A'})
        assert checked['result']['delivery'] == 'accepted'
        assert navigation_activity(root) == stable
        retried = await restored.dispatch('conversation.retry', {'sessionId': sid, 'inputId': 'A'})
        assert retried['result']['resent'] is False and not replacement.sent
        assert navigation_activity(root) == stable and len(root['messages']) == 1
        assert post_receipt(restored, 'A')['navigationPost']['fence'] == fence
    finally:
        await restored.close()


async def test_retained_post_multiple_restarts_do_not_commit_later_progress(held_post, tmp_path):
    from amplifier_web.chat_navigation import navigation_activity
    from amplifier_web.runtime import RuntimeOperationPending
    app, runtime, clock = held_post
    sid = app._session()['id']
    task = await start_post(app, runtime, 'A', 'unknown')
    clock[0] = 50
    await app.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'Later progress'})
    runtime.release['A'].set()
    with pytest.raises(RuntimeOperationPending):
        await task
    assert navigation_activity(app._session()) == 20
    await app.close()
    app._test_closed = True
    for _ in range(2):
        replacement = HeldPostRuntime()
        restored = AppService(tmp_path / 'app', replacement, workspace=tmp_path)
        try:
            root = restored._session(sid)
            assert navigation_activity(root) == 20 and root['recentActivityAt'] == 50
            assert root['messages'][0]['navigationPost']['disposition'] == 'retained'
            assert root['navigationPostActivity']['inputId'] == 'A' and not replacement.sent
        finally:
            await restored.close()


async def test_stale_and_held_human_steering_cannot_leave_phantom_promotion(held_post):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, _ = held_post
    root = app._session()
    root.update(status='working', collaborationGeneration={'id': 'live', 'terminal': False})
    with pytest.raises(AppError) as exc:
        await app.dispatch('conversation.send', {'sessionId': root['id'], 'text': 'Stale',
                                                'expectedGenerationId': 'old'})
    assert exc.value.code == 'steering_target_changed' and not root['messages']
    task = await start_post(app, runtime, 'correction', 'held', expectedGenerationId='live')
    assert navigation_activity(root) == 20
    runtime.release['correction'].set()
    receipt = await task
    assert receipt['steering']['disposition'] == 'held'
    assert root['status'] == 'working' and navigation_activity(root) == 10
    assert root['messages'][-1]['text'] == 'correction'
    assert root['messages'][-1]['navigationPost']['disposition'] == 'rejected'


def activity_fields(root):
    return {key: copy.deepcopy(root[key]) for key in
            ('navigationActivityAt', 'recentActivityAt', 'navigationActivityPending', 'navigationPostActivity')
            if key in root}


@pytest.mark.parametrize('completion_order', [('A', 'B'), ('B', 'A')])
@pytest.mark.parametrize('equal_clocks', [False, True])
@pytest.mark.parametrize('restart', [False, True])
@pytest.mark.parametrize('absent', [False, True])
async def test_retained_held_steering_refusal_chain_restores_exact_activity(
        held_post, tmp_path, monkeypatch, completion_order, equal_clocks, restart, absent):
    """The BA/unequal/no-restart/present case is the original 10/10/False defect.

    Unlike ownership-rejected ordinary sends, held steering retains BOTH bubbles.
    Run this unchanged on the original source and the candidate; never weaken the
    expected raw timestamp or pending presence to accommodate retained history.
    """
    from amplifier_web import service as service_module
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    monkeypatch.setattr(service_module, 'time', SimpleNamespace(time=lambda: clock[0]))
    root = app._session()
    sid = root['id']
    root.update(createdAt=10, navigationActivityPending=False, status='working',
                collaborationGeneration={'id': 'live', 'terminal': False})
    if absent:
        for key in activity_fields(root):
            root.pop(key)
    before = activity_fields(root)
    tasks, restored = {}, None
    try:
        tasks['A'] = await start_post(app, runtime, 'A', 'held', expectedGenerationId='live')
        if not equal_clocks:
            clock[0] = 30
        tasks['B'] = await start_post(app, runtime, 'B', 'held', expectedGenerationId='live')
        fences = {row['inputId']: row['navigationPost']['fence'] for row in root['messages']}
        first, last = completion_order
        runtime.release[first].set()
        assert (await tasks[first])['steering']['disposition'] == 'held'
        if first == 'B':
            assert navigation_activity(root) == root['recentActivityAt'] == 20
            assert root['navigationActivityPending'] is True
            assert root['navigationPostActivity']['inputId'] == 'A'
        assert len(root['messages']) == 2  # The refused row remains visible.
        remaining_chain = copy.deepcopy(root['navigationPostAdmissions'])
        if restart:
            # Simulate a process loss, not a replay of the unfinished transport.
            tasks[last].cancel()
            await asyncio.gather(tasks[last], return_exceptions=True)
            await app.close()
            app._test_closed = True
            replacement = HeldPostRuntime()
            restored = AppService(tmp_path / 'app', replacement, workspace=tmp_path)
            app = restored
            root = app._session(sid)
            assert root['navigationPostAdmissions'] == remaining_chain
            pending = next(row for row in root['messages'] if row['inputId'] == last)
            assert pending['steering']['disposition'] == pending['delivery']['status'] == 'unknown'
            assert pending['navigationPost']['disposition'] == 'pending'
            assert post_receipt(app, last)['navigationPost']['fence'] == fences[last]
            assert not replacement.sent
            await app.on_runtime_event('runtime.steering', {'sessionId': sid, 'input_id': last,
                'target_generation_id': 'live', 'event': 'steering.held'})
            assert not replacement.sent
        else:
            runtime.release[last].set()
            assert (await tasks[last])['steering']['disposition'] == 'held'
        assert activity_fields(root) == before
        assert navigation_activity(root) == 10
        if not absent:
            assert root['navigationActivityAt'] == root['recentActivityAt'] == 10
            assert root['navigationActivityPending'] is False
        assert [row['inputId'] for row in root['messages']] == ['A', 'B']
        assert all(row['steering']['disposition'] == 'held' and
                   row['navigationPost']['disposition'] == 'rejected' and
                   row['navigationPost']['fence'] == fences[row['inputId']] for row in root['messages'])
        assert 'navigationPostAdmissions' not in root
        assert all(post_receipt(app, identity)['navigationPost']['disposition'] == 'rejected'
                   for identity in tasks)
        # Cached commands/checks do not re-admit held steering or recover recency.
        for identity in tasks:
            cached = await app.dispatch('conversation.send', {'sessionId': sid, 'text': identity,
                'expectedGenerationId': 'live'}, command_id=identity)
            assert cached['duplicate'] and cached['steering']['disposition'] == 'held'
            checked = await app.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': identity})
            assert checked['result']['delivery'] == 'failed'
            with pytest.raises(AppError, match='cannot be resent'):
                await app.dispatch('conversation.retry', {'sessionId': sid, 'inputId': identity})
        assert activity_fields(root) == before
        assert len(runtime.sent) == 2
        if restored is not None:
            assert not restored.runtime.sent
    finally:
        for event in runtime.release.values():
            event.set()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        if restored is not None:
            await restored.close()


@pytest.mark.parametrize('newer', ['accepted', 'unknown', 'failed', 'fresh', 'ready', 'attention'])
async def test_held_steering_older_refusal_preserves_newer_admission_or_settlement(held_post, newer):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    root.update(status='working', collaborationGeneration={'id': 'live', 'terminal': False})
    tasks = {}
    try:
        tasks['A'] = await start_post(app, runtime, 'A', 'held', expectedGenerationId='live')
        clock[0] = 30
        outcome = newer if newer in {'unknown', 'failed'} else 'held' if newer == 'fresh' else 'accepted'
        tasks['B'] = await start_post(app, runtime, 'B', outcome, expectedGenerationId='live')
        if newer == 'fresh':
            clock[0] = 40
            tasks['C'] = await start_post(app, runtime, 'C', expectedGenerationId='live')
        else:
            runtime.release['B'].set()
            await tasks['B']
        if newer in {'ready', 'attention'}:
            clock[0] = 40
            kind, payload = ('runtime.status', {'status': 'idle'}) if newer == 'ready' else (
                'approval.requested', {'id': 'approval', 'tool': 'write_file'})
            await app.on_runtime_event(kind, {'sessionId': root['id'], **payload})
        before = activity_fields(root)
        for identity in ('B', 'A') if newer == 'fresh' else ('A',):
            runtime.release[identity].set()
            assert (await tasks[identity])['steering']['disposition'] == 'held'
        assert activity_fields(root) == before
        assert navigation_activity(root) == (40 if newer in {'fresh', 'ready', 'attention'} else 30)
        if newer in {'unknown', 'failed'}:
            assert root['messages'][1]['navigationPost']['disposition'] == 'retained'
            assert root['messages'][1]['steering']['disposition'] == 'unknown'
        if newer == 'fresh':
            runtime.release['C'].set()
            await tasks['C']
            assert activity_fields(root) == before
    finally:
        for event in runtime.release.values():
            event.set()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


@pytest.mark.parametrize('source', ['peer', 'agent', 'scheduler', 'host', 'progress'])
@pytest.mark.parametrize('equal_clock', [False, True])
@pytest.mark.parametrize('placement', ['between', 'after'])
async def test_held_steering_refusals_preserve_independent_raw_activity(
        held_post, source, equal_clock, placement):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    root.update(navigationActivityPending=False, status='working',
                collaborationGeneration={'id': 'live', 'terminal': False})
    tasks = {}
    try:
        tasks['A'] = await start_post(app, runtime, 'A', 'held', expectedGenerationId='live')
        if placement == 'after':
            clock[0] = 30
            tasks['B'] = await start_post(app, runtime, 'B', 'held', expectedGenerationId='live')
        if not equal_clock:
            clock[0] = 25 if placement == 'between' else 40
        if source == 'progress':
            await app.on_runtime_event('assistant.delta', {'sessionId': root['id'], 'text': 'Live progress'})
        elif source == 'host':
            # A UI host action is not an ordinary send, even with user role/UI origin.
            app._message(root, 'user', 'Host question answer', inputOrigin='ui', hostAction='question.answer')
            app._publish_changes(sessions={root['id']})
        else:
            tasks['independent'] = await start_post(app, runtime, 'independent', origin=source,
                                                   caller_session_id=root['id'], expectedGenerationId='live')
            runtime.release['independent'].set()
            await tasks['independent']
            assert 'navigationPost' not in root['messages'][-1]
        expected_raw = clock[0]
        if placement == 'between':
            clock[0] = 30
            tasks['B'] = await start_post(app, runtime, 'B', 'held', expectedGenerationId='live')
        for identity in ('B', 'A'):
            runtime.release[identity].set()
            assert (await tasks[identity])['steering']['disposition'] == 'held'
            assert identity in runtime.steered
            assert post_receipt(app, identity)['delivery'] == 'failed'
        assert navigation_activity(root) == 10
        assert root['recentActivityAt'] == expected_raw and root['navigationActivityPending'] is True
        clock[0] = 50
        await app.on_runtime_event('runtime.status', {'sessionId': root['id'], 'status': 'idle'})
        assert navigation_activity(root) == 50
    finally:
        for event in runtime.release.values():
            event.set()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


@pytest.mark.parametrize('outcome', ['unknown', 'failed'])
async def test_older_refusal_cannot_erase_newer_retained_delivery(held_post, outcome):
    from amplifier_web.runtime import RuntimeOperationPending
    app, runtime, clock = held_post
    root = app._session()
    a = await start_post(app, runtime, 'A', 'rejected')
    try:
        clock[0] = 30
        b = await start_post(app, runtime, 'B', outcome)
        runtime.release['B'].set()
        with pytest.raises(RuntimeOperationPending if outcome == 'unknown' else AppError):
            await b
        before = activity_fields(root)
        assert root['messages'][-1]['delivery']['status'] == outcome
        assert root['messages'][-1]['navigationPost']['disposition'] == 'retained'
        await reject_post(runtime, 'A', a)
        assert activity_fields(root) == before
        assert root['messages'][0]['inputId'] == 'B'
    finally:
        for event in runtime.release.values():
            event.set()
        await asyncio.gather(a, return_exceptions=True)


async def test_admitted_human_steering_stays_frozen_until_existing_run_is_ready(held_post):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    root.update(status='working', collaborationGeneration={'id': 'live', 'terminal': False})
    turns = copy.deepcopy(root.get('execution', {}).get('turns', []))
    task = await start_post(app, runtime, 'correction', expectedGenerationId='live')
    runtime.release['correction'].set()
    result = await task
    assert result['steering']['disposition'] == 'queued'
    assert navigation_activity(root) == 20
    assert root.get('execution', {}).get('turns', []) == turns
    clock[0] = 30
    await app.on_runtime_event('runtime.steering', {'sessionId': root['id'], 'input_id': 'correction',
        'target_generation_id': 'live', 'event': 'steering.applied'})
    assert root['messages'][-1]['steering']['disposition'] == 'applied'
    assert navigation_activity(root) == 20
    clock[0] = 40
    await app.on_runtime_event('assistant.delta', {'sessionId': root['id'], 'text': 'Working'})
    assert navigation_activity(root) == 20
    clock[0] = 50
    await app.on_runtime_event('runtime.status', {'sessionId': root['id'], 'status': 'idle'})
    assert navigation_activity(root) == 50 and 'navigationPostActivity' not in root


@pytest.mark.parametrize('acknowledged', [False, True])
@pytest.mark.parametrize('event', ['steering.applied', 'steering.held'])
async def test_restart_late_steering_preserves_admission_fence_without_replacement(
        held_post, tmp_path, acknowledged, event):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, _ = held_post
    root = app._session()
    sid = root['id']
    root.update(status='working', collaborationGeneration={'id': 'live', 'terminal': False})
    task = await start_post(app, runtime, 'restart-steering', expectedGenerationId='live')
    assert runtime.steered == ['restart-steering']
    if acknowledged:
        runtime.release['restart-steering'].set()
        assert (await task)['steering']['disposition'] == 'queued'
    else:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    fence = root['messages'][-1]['navigationPost']['fence']
    await app.close()
    app._test_closed = True
    replacement = HeldPostRuntime()
    restored = AppService(tmp_path / 'app', replacement, workspace=tmp_path)
    try:
        root = restored._session(sid)
        assert root['messages'][-1]['steering']['disposition'] == 'unknown'
        assert root['messages'][-1]['navigationPost']['fence'] == fence
        before = copy.deepcopy(root['messages'])
        before_activity = activity_fields(root)
        await restored.on_runtime_event('runtime.steering', {'sessionId': sid,
            'input_id': 'restart-steering', 'target_generation_id': 'wrong', 'event': event})
        assert root['messages'] == before and activity_fields(root) == before_activity
        await restored.on_runtime_event('runtime.steering', {'sessionId': sid,
            'input_id': 'restart-steering', 'target_generation_id': 'live', 'event': event})
        message = root['messages'][-1]
        assert message['navigationPost']['fence'] == fence
        assert message['steering']['disposition'] == ('applied' if event == 'steering.applied' else 'held')
        if event == 'steering.applied':
            assert navigation_activity(root) == 20
            assert message['navigationPost']['disposition'] == 'accepted'
            assert post_receipt(restored, 'restart-steering')['delivery'] == 'accepted'
        elif acknowledged:
            # Confirmed earlier admission is not absence: a late refusal cannot
            # erase its retained position, even after application uncertainty.
            assert navigation_activity(root) == 20
            assert message['navigationPost']['disposition'] == 'accepted'
        else:
            assert navigation_activity(root) == 10
            assert message['navigationPost']['disposition'] == 'rejected'
        assert not replacement.sent and not replacement.steered
    finally:
        await restored.close()


@pytest.mark.parametrize('source', ['host', 'progress'])
@pytest.mark.parametrize('equal_clock', [False, True])
@pytest.mark.parametrize('order', [('A', 'B'), ('B', 'A')])
@pytest.mark.parametrize('restart', [False, True])
async def test_independent_activity_intersects_both_refusal_orders_and_restart(
        held_post, tmp_path, source, equal_clock, order, restart):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    sid = root['id']
    root.update(status='working', navigationActivityPending=False,
                collaborationGeneration={'id': 'live', 'terminal': False})
    tasks, restored = {}, None
    try:
        tasks['A'] = await start_post(app, runtime, 'A', 'held', expectedGenerationId='live')
        if not equal_clock:
            clock[0] = 30
        tasks['B'] = await start_post(app, runtime, 'B', 'held', expectedGenerationId='live')
        if not equal_clock:
            clock[0] = 40
        if source == 'progress':
            await app.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'Independent progress'})
        else:
            app._message(root, 'user', 'Independent host answer', inputOrigin='ui', hostAction='question.answer')
            app._publish_changes(sessions={sid})
        expected_raw = clock[0]  # Equal clocks still retain independent activity.
        first, last = order
        runtime.release[first].set()
        assert (await tasks[first])['steering']['disposition'] == 'held'
        if restart:
            tasks[last].cancel()
            await asyncio.gather(tasks[last], return_exceptions=True)
            await app.close()
            app._test_closed = True
            restored = AppService(tmp_path / 'app', HeldPostRuntime(), workspace=tmp_path)
            app = restored
            root = app._session(sid)
            await app.on_runtime_event('runtime.steering', {'sessionId': sid, 'input_id': last,
                'target_generation_id': 'live', 'event': 'steering.held'})
            assert not app.runtime.sent and not app.runtime.steered
        else:
            runtime.release[last].set()
            assert (await tasks[last])['steering']['disposition'] == 'held'
        assert navigation_activity(root) == 10
        assert root['recentActivityAt'] == expected_raw
        assert root['navigationActivityPending'] is True
        assert runtime.steered == ['A', 'B']
        assert all(post_receipt(app, identity)['navigationPost']['disposition'] == 'rejected'
                   for identity in ('A', 'B'))
    finally:
        for release in runtime.release.values():
            release.set()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        if restored is not None:
            await restored.close()


async def test_pending_post_chain_is_bounded_and_refuses_before_insertion(held_post):
    from amplifier_web.chat_navigation import MAX_POST_ADMISSIONS, navigation_activity
    app, runtime, _ = held_post
    root = app._session()
    tasks = []
    try:
        for index in range(MAX_POST_ADMISSIONS):
            tasks.append(await start_post(app, runtime, f'held-{index}'))
        snapshot = copy.deepcopy(root['messages'])
        at = navigation_activity(root)
        with pytest.raises(AppError) as exc:
            await app.dispatch('conversation.send', {'sessionId': root['id'], 'text': 'overflow'},
                               command_id='overflow')
        assert exc.value.code == 'post_admission_pending'
        assert root['messages'] == snapshot and navigation_activity(root) == at
        assert len(root['navigationPostAdmissions']['posts']) == MAX_POST_ADMISSIONS
        assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('overflow',)).fetchone() is None
    finally:
        for event in runtime.release.values():
            event.set()
        await asyncio.gather(*tasks)
    assert 'navigationPostAdmissions' not in root


async def test_rejected_post_does_not_erase_equal_clock_progress_pending_settlement(held_post):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, clock = held_post
    root = app._session()
    task = await start_post(app, runtime, 'A', 'rejected')
    await app.on_runtime_event('assistant.delta', {'sessionId': root['id'], 'text': 'Separate live progress'})
    await reject_post(runtime, 'A', task)
    assert navigation_activity(root) == 10
    assert root['navigationActivityPending'] is True and root['recentActivityAt'] == 20
    clock[0] = 30
    await app.on_runtime_event('runtime.status', {'sessionId': root['id'], 'status': 'idle'})
    assert navigation_activity(root) == 30


async def test_positive_exact_admission_evidence_wins_late_ownership_refusal(held_post):
    from amplifier_web.chat_navigation import navigation_activity
    app, runtime, _ = held_post
    root = app._session()
    task = await start_post(app, runtime, 'A', 'rejected')
    await app.on_runtime_event('runtime.delivery', {'sessionId': root['id'], 'inputId': 'A', 'delivery': 'accepted'})
    runtime.release['A'].set()
    receipt = await task
    assert receipt['delivery'] == 'accepted'
    assert navigation_activity(root) == 20 and len(root['messages']) == 1
    assert root['messages'][0]['delivery']['status'] == 'accepted'
    assert post_receipt(app, 'A')['navigationPost']['disposition'] == 'accepted'


@pytest.fixture
async def recovery(tmp_path):
    runtime = RecoveryRuntime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    sender = runtime.send
    runtime.send = AsyncMock(side_effect=RuntimeError('Reply lost'))
    args = {'sessionId': app._session()['id'], 'text': 'Original request', 'preserveDraft': True}
    with pytest.raises(RuntimeError):
        await app.dispatch('conversation.send', args, command_id='original')
    runtime.send = sender
    yield app, runtime, args
    if not getattr(app, '_test_closed', False):
        await app.close()


def recovery_args(app):
    return {'sessionId': app._session()['id'], 'inputId': 'original'}


async def test_check_is_passive_and_explains_uncertainty(recovery):
    app, runtime, _ = recovery
    for _ in range(2):
        result = await app.dispatch('conversation.delivery', recovery_args(app), origin='agent', caller_session_id=app._session()['id'])
        assert result['result']['delivery'] == 'unknown'
        assert 'does not resend' in result['result']['message']
    assert runtime.sent == [] and runtime.retries == [] and runtime.started == []
    assert app._session()['status'] == 'error'
    with pytest.raises(AppError, match='Confirm'):
        await app.dispatch('conversation.retry', recovery_args(app), origin='agent', caller_session_id=app._session()['id'])


async def test_check_reconciles_positive_evidence_and_original_receipt(recovery):
    app, runtime, args = recovery
    runtime.evidence = 'accepted'
    result = await app.dispatch('conversation.delivery', recovery_args(app))
    assert result['result']['delivery'] == 'accepted'
    duplicate = await app.dispatch('conversation.send', args, command_id='original')
    assert duplicate['delivery'] == 'accepted'
    result = await app.dispatch('conversation.retry', recovery_args(app))
    assert result['result']['resent'] is False
    assert runtime.retries == []


@pytest.mark.parametrize('prior,evidence,expected', [
    ('accepted', 'unknown', 'accepted'),
    ('unknown', 'accepted', 'accepted'),
    ('unknown', 'unknown', 'unknown'),
])
async def test_check_exact_receipt_outside_message_window(recovery, prior, evidence, expected):
    app, runtime, _ = recovery
    sid = app._session()['id']
    if prior == 'accepted':
        app._delivery(app._session(), 'original', 'accepted')
    app._session()['messages'] = []
    runtime.evidence = evidence
    # No full-history read or unrelated message is needed to prove this input.
    result = await app._check_delivery(sid, 'original')
    assert result['delivery'] == expected
    assert app._session()['messages'] == []
    assert runtime.sent == [] and runtime.retries == [] and runtime.started == []


async def test_delivery_receipt_never_borrows_other_chat_or_generic_acceptance(recovery):
    app, runtime, _ = recovery
    sid = app._session()['id']
    app._session()['messages'] = []
    record = app.db.execute('SELECT receipt FROM commands WHERE id=?', ('original',)).fetchone()
    receipt = json.loads(record[0])
    assert receipt['sessionId'] == sid and receipt['inputId'] == 'original'
    for change in ({'sessionId': 'other', 'delivery': 'accepted'}, {'inputId': 'other', 'delivery': 'accepted'},
                   {'delivery': 'unknown', 'accepted': True}):
        app.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps({**receipt, **change}), 'original'))
        runtime.evidence = 'unknown'
        result = await app._check_delivery(sid, 'original')
        assert result['delivery'] != 'accepted'
    assert not runtime.sent and not runtime.retries


@pytest.mark.parametrize('invalid', [
    None, [], {'delivery': {}}, {'accepted': False}, {'status': 409},
    {'status': 'failed'}, {'code': 'worker_startup_failed'}, {'error': 'refused'},
    {'commandAction': 'session.delete'}, {'action': 'conversation.stop'},
    {'commandAction': []}, {'accepted': 1},
])
async def test_delivery_rejects_malformed_or_contradictory_saved_receipt(recovery, invalid):
    app, runtime, _ = recovery
    sid = app._session()['id']
    app._session()['messages'] = []
    runtime.evidence = 'unknown'
    base = {'sessionId': sid, 'inputId': 'original', 'accepted': True, 'delivery': 'accepted'}
    receipt = {**base, **invalid} if isinstance(invalid, dict) else invalid
    app.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), 'original'))
    result = await app._check_delivery(sid, 'original')
    assert result['delivery'] != 'accepted'
    assert not runtime.sent and not runtime.retries and not runtime.started


@pytest.mark.parametrize('raw', ['null', '[]', '{invalid JSON'])
async def test_positive_delivery_preserves_malformed_receipt_bytes(recovery, raw, monkeypatch):
    app, runtime, _ = recovery
    session = app._session()
    message = session['messages'][-1]
    assert message['inputId'] == 'original' and message.get('navigationPost')
    message['attachments'] = [{'id': 'kept', 'name': 'reference.txt'}]
    before = copy.deepcopy(message)
    db = app.db
    if raw.startswith('{invalid'):
        # The current indexed store rejects invalid JSON on write. Exercise a
        # corrupt legacy read at the exact receipt seam, without removing indexes.
        with pytest.raises(sqlite3.OperationalError, match='malformed JSON'):
            db.execute('UPDATE commands SET receipt=? WHERE id=?', (raw, 'original'))
        original_bytes = db.execute('SELECT receipt FROM commands WHERE id=?', ('original',)).fetchone()[0]

        class CorruptReceiptRead:
            def __getattr__(self, name):
                return getattr(db, name)

            def execute(self, sql, args=()):
                if sql == 'SELECT receipt FROM commands WHERE id=?' and args == ('original',):
                    return SimpleNamespace(fetchone=lambda: (raw,))
                assert not (sql.startswith('UPDATE commands SET receipt=') and args[-1:] == ('original',))
                return db.execute(sql, args)

        monkeypatch.setattr(app, 'db', CorruptReceiptRead())
    else:
        db.execute('UPDATE commands SET receipt=? WHERE id=?', (raw, 'original'))
        original_bytes = raw
    runtime.evidence = 'accepted'
    result = await app._check_delivery(session['id'], 'original')
    assert result['delivery'] == 'accepted'
    assert message['delivery']['status'] == 'accepted'
    for key in ('id', 'inputId', 'text', 'attachments'):
        assert message.get(key) == before.get(key)
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('original',)).fetchone()[0] == raw
    assert db.execute('SELECT receipt FROM commands WHERE id=?', ('original',)).fetchone()[0] == original_bytes
    assert not runtime.sent and not runtime.retries and not runtime.started
    # A later unknown observation cannot erase the independently confirmed message.
    runtime.evidence = 'unknown'
    assert (await app._check_delivery(session['id'], 'original'))['delivery'] == 'accepted'
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('original',)).fetchone()[0] == raw


@pytest.mark.parametrize('action', [None, 'conversation.send'])
async def test_delivery_accepts_valid_legacy_and_named_send_receipt(recovery, action):
    app, runtime, _ = recovery
    sid = app._session()['id']
    app._session()['messages'] = []
    runtime.evidence = 'unknown'
    receipt = {'sessionId': sid, 'inputId': 'original', 'accepted': True, 'delivery': 'accepted'}
    if action:
        receipt['commandAction'] = action
    app.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), 'original'))
    assert (await app._check_delivery(sid, 'original'))['delivery'] == 'accepted'
    assert not runtime.sent and not runtime.retries


async def test_retry_preserves_message_attachments_draft_and_identity(recovery):
    app, runtime, _ = recovery
    original = app._session()['messages'][-1]
    original['attachments'] = [{'id': 'kept', 'name': 'reference.txt'}]
    before = copy.deepcopy(original)
    app.state['view']['draft'] = 'Unsent next thought'
    args = {**recovery_args(app), 'confirmUncertain': True}
    result = await app.dispatch('conversation.retry', args, origin='agent', command_id='retry-command',
                                caller_session_id=app._session()['id'])
    assert result['result'] == {'delivery': 'accepted', 'resent': True}
    assert runtime.sent == [(app._session()['id'], 'Original request', 'original')]
    users = [m for m in app._session()['messages'] if m['role'] == 'user']
    assert len(users) == 1
    assert {k: users[0][k] for k in before if k not in {'delivery', 'navigationPost'}} == {
        k: v for k, v in before.items() if k not in {'delivery', 'navigationPost'}}
    assert users[0]['navigationPost']['fence'] == before['navigationPost']['fence']
    assert runtime.retries[0]['messages'][-1]['attachments'] == before['attachments']
    assert app.state['view']['draft'] == 'Unsent next thought'
    cached = await app.dispatch('conversation.retry', args, origin='agent', command_id='retry-command',
                                caller_session_id=app._session()['id'])
    assert cached['result'] == result['result']
    assert len(runtime.retries) == 1


async def test_concurrent_retry_is_admitted_once(recovery):
    app, runtime, _ = recovery
    entered, finish = asyncio.Event(), asyncio.Event()
    retry = runtime.retry
    async def delayed(*args):
        entered.set()
        await finish.wait()
        return await retry(*args)
    runtime.retry = delayed
    args = {**recovery_args(app), 'confirmUncertain': True}
    task = asyncio.create_task(app.dispatch('conversation.retry', args, command_id='one'))
    await entered.wait()
    duplicate = await app.dispatch('conversation.retry', args, command_id='one')
    assert duplicate['duplicate']
    with pytest.raises(AppError, match='settle'):
        await app.dispatch('conversation.retry', args, command_id='two')
    finish.set()
    await task
    assert len(runtime.retries) == 1


async def test_retry_after_restart_requires_explicit_action_and_not_outbox(recovery, tmp_path):
    app, _, _ = recovery
    sid = app._session()['id']
    await app.close()
    app._test_closed = True
    runtime = RecoveryRuntime()
    restored = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        assert runtime.sent == []
        await restored.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': 'original'})
        assert runtime.sent == []
        await restored.dispatch('conversation.retry', {'sessionId': sid, 'inputId': 'original', 'confirmUncertain': True},
                                origin='agent', caller_session_id=sid)
        assert runtime.sent[0][2] == 'original'
    finally:
        await restored.close()


async def test_unknown_input_not_saved_is_distinct_and_cannot_resend(recovery):
    app, runtime, _ = recovery
    args = {**recovery_args(app), 'inputId': 'missing'}
    assert (await app.dispatch('conversation.delivery', args))['result']['delivery'] == 'not_saved'
    with pytest.raises(AppError, match='not saved'):
        await app.dispatch('conversation.retry', {**args, 'confirmUncertain': True})
    assert not runtime.sent


async def test_retry_never_reorders_later_messages(recovery):
    app, runtime, _ = recovery
    app._message(app._session(), 'user', 'Later request', 'chat', inputId='later')
    with pytest.raises(AppError, match='latest'):
        await app.dispatch('conversation.retry', {**recovery_args(app), 'confirmUncertain': True})
    assert not runtime.sent


async def test_history_check_does_not_start_worker_or_infer_absence(tmp_path):
    session = {'id': 'fixture', 'workspace': str(tmp_path)}
    runtime = RuntimeManager()
    try:
        assert await runtime.delivery(session, 'original') == 'unknown'
        path = sessions_dir(tmp_path) / 'fixture' / 'transcript.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'role': 'user', 'content': 'private', 'metadata': {'amplifier_input': {'id': 'original'}}})+'\n')
        assert await runtime.delivery(session, 'original') == 'accepted'
        assert await runtime.delivery(session, 'absent') == 'unknown'
        assert not runtime.workers
    finally:
        await runtime.close()


@pytest.mark.parametrize('source', ['inbox', 'restored_context'])
async def test_worker_retry_never_resubmits_accepted_input(source):
    worker = Worker()
    worker.execution = object()
    worker.runtime = SimpleNamespace(accepted={'original': object()} if source == 'inbox' else {}, submit=AsyncMock())
    context = SimpleNamespace(get_messages=AsyncMock(return_value=[{'role': 'user', 'metadata': {'amplifier_input': {'id': 'original'}}}]))
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: context))
    with patch('amplifier_web.runtime_worker.publish') as publish:
        await worker._command_serial({'op': 'retry', 'id': 'r', 'input_id': 'original', 'text': 'Same input'})
    assert publish.call_args.args[0]['result']['duplicate'] is True
    worker.runtime.submit.assert_not_awaited()


async def test_duplicate_retry_restores_settled_turn(recovery):
    app, runtime, _ = recovery
    before = copy.deepcopy(app._session()['execution']['turns'][-1])
    runtime.retry = AsyncMock(return_value={'accepted': True, 'duplicate': True})
    result = await app.dispatch('conversation.retry', {**recovery_args(app), 'confirmUncertain': True})
    assert result['result']['resent'] is False
    assert app._session()['status'] == 'error'
    after = app._session()['execution']['turns'][-1]
    for key in ('phase', 'endedAt'):
        assert after.get(key) == before.get(key)
    assert not runtime.sent


async def test_retry_ownership_failure_preserves_history_and_settles_state(recovery):
    from amplifier_web.runtime import SessionInUseError
    app, runtime, _ = recovery
    before = copy.deepcopy(app._session()['messages'])
    runtime.retry = AsyncMock(side_effect=SessionInUseError({'app': 'amplifier-cli', 'pid': 123}))
    args = {**recovery_args(app), 'confirmUncertain': True}
    with pytest.raises(AppError, match='owned elsewhere'):
        await app.dispatch('conversation.retry', args, command_id='retry-owned')
    assert app._session()['messages'] == before
    assert app._session()['ownership']['status'] == 'blocked'
    assert app._session()['execution']['turns'][-1]['phase'] == 'stopped'
    cached = await app.dispatch('conversation.retry', args, command_id='retry-owned')
    assert cached['result']['delivery'] == 'unknown'
    runtime.retry.assert_awaited_once()
    # Inspection still works when this host does not own the conversation.
    runtime.evidence = 'accepted'
    checked = await app.dispatch('conversation.delivery', recovery_args(app))
    assert checked['result']['delivery'] == 'accepted'


async def test_late_acceptance_during_check_is_never_downgraded(recovery):
    app, runtime, _ = recovery
    async def probe(*args):
        app._delivery(app._session(), 'original', 'accepted')
        return 'unknown'
    runtime.delivery = probe
    result = await app.dispatch('conversation.delivery', recovery_args(app))
    assert result['result']['delivery'] == 'accepted'


async def test_worker_retry_requires_ownership_but_check_does_not():
    from amplifier_foundation.session import SessionBusyError
    worker = Worker()
    worker.execution = object()
    worker.runtime = SimpleNamespace(accepted={'original': object()}, submit=AsyncMock())
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: None))
    worker.acquire_for_mutation = AsyncMock(side_effect=SessionBusyError({'app': 'amplifier-cli', 'pid': 123}))
    with patch('amplifier_web.runtime_worker.publish') as publish:
        await worker.command({'op': 'delivery', 'id': 'check', 'input_id': 'original'})
        assert publish.call_args.args[0]['result']['delivery'] == 'accepted'
        worker.acquire_for_mutation.assert_not_awaited()
        await worker.command({'op': 'retry', 'id': 'retry', 'input_id': 'original', 'text': 'Request'})
        assert publish.call_args.args[0]['code'] == 'session_busy'
    worker.acquire_for_mutation.assert_awaited_once()
    worker.runtime.submit.assert_not_awaited()


async def test_connected_client_recovery_uses_shared_passive_and_explicit_actions(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    runtime = RecoveryRuntime()
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    sender = runtime.send
    runtime.send = AsyncMock(side_effect=RuntimeError('Synthetic lost reply'))
    with pytest.raises(RuntimeError):
        await service.dispatch('conversation.send', {'sessionId': sid, 'text': 'Saved request'}, command_id='saved-input')
    runtime.send = sender
    attached = await client.post('/api/clients/attach', json={'clientId': 'recovery-client', 'kind': 'tui', 'protocolVersion': 1})
    assert attached.status == 200
    endpoint = f'/api/sessions/{sid}/commands'
    headers = {'X-Amplifier-Client': 'recovery-client'}
    async def action(name, identity, **args):
        return await client.post(endpoint, json={'id': identity, 'action': name, 'args': {'inputId': 'saved-input', **args}}, headers=headers)
    checked = await action('conversation.delivery', 'check-once')
    assert checked.status == 200
    assert (await checked.json())['result']['delivery'] == 'unknown'
    assert not runtime.sent and not runtime.retries
    denied = await action('conversation.retry', 'unconfirmed')
    assert denied.status == 409 and not runtime.retries
    wrong = await action('conversation.retry', 'wrong-target', sessionId='elsewhere', confirmUncertain=True)
    assert wrong.status == 400 and not runtime.retries
    resent = await action('conversation.retry', 'explicit-once', confirmUncertain=True)
    assert resent.status == 200 and (await resent.json())['result']['resent']
    duplicate = await action('conversation.retry', 'explicit-once', confirmUncertain=True)
    assert duplicate.status == 200 and (await duplicate.json())['duplicate']
    assert runtime.sent == [(sid, 'Saved request', 'saved-input')]


async def test_real_worker_startup_failure_has_durable_non_delivery_receipt(authenticated_client, tmp_path):
    import sys
    from amplifier_web.server import create_app
    runtime = RuntimeManager(command=[sys.executable, '-c', 'raise SystemExit(3)'], startup_timeout=3)
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    payload = {'action': 'conversation.send', 'args': {'sessionId': sid, 'text': 'Keep this request'}, 'id': 'startup-input'}
    response = await client.post('/api/actions', json=payload)
    data = await response.json()
    assert response.status == 503 and data['code'] == 'worker_startup_failed'
    assert data['receipt']['delivery'] == 'failed'
    assert service._session()['messages'][-1]['delivery']['status'] == 'failed'
    assert not runtime.workers
    checked = await service.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': 'startup-input'})
    assert checked['result']['delivery'] == 'failed'
    duplicate = await client.post('/api/actions', json=payload)
    assert (await duplicate.json())['receipt'] == data['receipt']
    assert not runtime.workers
    retry = {'action': 'conversation.retry', 'args': {'sessionId': sid, 'inputId': 'startup-input'}, 'id': 'startup-retry'}
    failed_retry = await client.post('/api/actions', json=retry)
    assert failed_retry.status == 503
    assert (await failed_retry.json())['receipt']['delivery'] == 'failed'
    assert service._session()['messages'][-1]['delivery']['status'] == 'failed'
    repeated_retry = await client.post('/api/actions', json=retry)
    repeated_data = await repeated_retry.json()
    assert repeated_data['result']['delivery'] == repeated_data['receipt']['delivery'] == 'failed'
    assert not runtime.workers
    replacement = RecoveryRuntime()
    service.runtime = replacement
    result = await service.dispatch('conversation.retry', {'sessionId': sid, 'inputId': 'startup-input'}, command_id='explicit-retry')
    assert result['result']['delivery'] == 'accepted'
    assert replacement.sent == [(sid, 'Keep this request', 'startup-input')]
    assert len([m for m in service._session()['messages'] if m['role'] == 'user']) == 1
    confirmed = await client.post('/api/actions', json=payload)
    confirmed_data = await confirmed.json()
    assert confirmed_data['accepted'] is True and confirmed_data['delivery'] == 'accepted'
    assert 'code' not in confirmed_data and len(replacement.sent) == 1
    await runtime.close()


async def test_retry_startup_failure_cannot_claim_earlier_attempt_was_not_delivered(recovery):
    from amplifier_web.runtime import RuntimeStartupError
    app, runtime, _ = recovery
    runtime.retry = AsyncMock(side_effect=RuntimeStartupError('Worker could not start.'))
    args = {**recovery_args(app), 'confirmUncertain': True}
    with pytest.raises(AppError) as failure:
        await app.dispatch('conversation.retry', args, command_id='failed-retry')
    assert failure.value.receipt['delivery'] == 'unknown'
    assert app._session()['messages'][-1]['delivery']['status'] == 'unknown'
    cached = await app.dispatch('conversation.retry', args, command_id='failed-retry')
    assert cached['accepted'] is False and cached['receipt']['delivery'] == 'unknown'
    runtime.retry.assert_awaited_once()
