"""Fresh human input can settle a dead root without replaying uncertain work."""
import asyncio
import copy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_foundation.session import SharedSessionStore, SessionBusyError
from amplifier_web.execution import ensure_turn
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError, AppService
from test_service import Runtime


@pytest.fixture
async def failed(tmp_path):
    runtime = Runtime()
    runtime.send = AsyncMock()
    runtime.steer = AsyncMock(side_effect=TimeoutError('Unconfirmed transport'))
    manager = RuntimeManager()
    runtime.stopped_boundary = manager.stopped_boundary
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    ensure_turn(session, 'original', 'Preserved work')
    await app.on_runtime_event('runtime.generation', {'sessionId': session['id'],
        'event': 'generation.started', 'generation_id': 'lost', 'initial_input_id': 'original'})
    await app.on_runtime_event('runtime.error', {'sessionId': session['id'],
        'error': 'Amplifier worker communication failed (OSError). Work was not replayed.',
        'errorType': 'WorkerCommunicationError'})
    session['workers'] = [
        {'id': 'native', 'status': 'running', 'runtimeOwnerId': 'dead-owner', 'report': 'Earlier progress'},
        {'id': 'done', 'status': 'completed', 'runtimeOwnerId': 'dead-owner', 'report': 'Saved result'},
        {'id': 'independent', 'status': 'running'},
    ]
    session['draft'] = 'Keep this draft'
    await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'Old correction',
        'expectedGenerationId': 'lost'}, command_id='uncertain')
    runtime.steer.reset_mock()
    assert session['messages'][-1]['steering']['disposition'] == 'unknown'
    yield app, runtime, manager, session
    await app.close()


async def fresh(app, **extra):
    return await app.dispatch('conversation.send', {'sessionId': app._session()['id'],
        'text': 'Inspect saved work and continue without repeating uncertain effects.',
        'preserveDraft': True, **extra}, command_id='fresh')


async def test_dead_row_recovers_only_fresh_input_and_commits_under_writer_lease(failed, monkeypatch, tmp_path):
    app, runtime, _, session = failed
    failure = copy.deepcopy(session['failure'])
    original_turn = copy.deepcopy(session['execution']['turns'][0])
    old_message = copy.deepcopy(session['messages'][-1])
    old_receipt = app.db.execute("SELECT receipt FROM commands WHERE id='uncertain'").fetchone()[0]
    job = tmp_path / 'live-jobs' / 'job.json'
    job.parent.mkdir()
    job.write_text('{"status":"pending","effects":"not_rolled_back"}')
    original_job = job.read_bytes()
    published = []
    publish = app._publish_changes
    def guarded_publish(*args, **kwargs):
        if app.db.execute("SELECT 1 FROM commands WHERE id='fresh'").fetchone() and not published:
            with pytest.raises(SessionBusyError):
                SharedSessionStore(session['workspace'], session['id']).acquire(app='other-writer')
            published.append(True)
        return publish(*args, **kwargs)
    monkeypatch.setattr(app, '_publish_changes', guarded_publish)
    result = await fresh(app)
    assert result['delivery'] == 'accepted'
    assert result['stoppedGenerationRecovery']['generationId'] == 'lost'
    assert result['stoppedGenerationRecovery']['workerAbsent'] is True
    assert result['stoppedGenerationRecovery']['inputsReplayed'] is False
    assert published and session['collaborationGeneration']['terminal']
    assert not session['collaborationGeneration']['completed']
    assert session['workers'][0]['status'] == 'interrupted'
    assert session['workers'][0]['report'] == 'Earlier progress'
    assert session['workers'][1]['status'] == 'completed'
    assert session['workers'][2]['status'] == 'running'
    assert session['messages'][0] == old_message
    assert session['failure'] == failure and session['draft'] == 'Keep this draft'
    assert app.db.execute("SELECT receipt FROM commands WHERE id='uncertain'").fetchone()[0] == old_receipt
    assert job.read_bytes() == original_job
    assert next(t for t in session['execution']['turns'] if t['id'] == 'original') == original_turn
    runtime.send.assert_awaited_once()
    runtime.steer.assert_not_awaited()
    assert (await fresh(app))['duplicate']
    runtime.send.assert_awaited_once()


@pytest.mark.parametrize('block', ['native_owner', 'process', 'unobserved_exit', 'admission',
    'preparation', 'handoff', 'remote', 'configuration', 'missing_proof'])
async def test_absence_is_required_before_any_new_input(failed, block):
    app, runtime, manager, session = failed
    held = None
    if block == 'native_owner':
        held = SharedSessionStore(session['workspace'], session['id']).acquire(app='external')
    elif block in {'process', 'unobserved_exit'}:
        manager.workers[session['id']] = {'process': SimpleNamespace(returncode=None if block == 'process' else 1)}
    elif block == 'admission':
        manager._locks[session['id']] = asyncio.Lock()
        await manager._locks[session['id']].acquire()
    elif block == 'preparation':
        manager._preparations[session['id']] = {}
    elif block in {'handoff', 'remote'}:
        manager.bind_execution_state(lambda sid: {'hostMatches': block != 'remote',
            'fenced': block == 'handoff', 'revision': 0, 'directory': session['workspace']})
    elif block == 'configuration':
        session['configurationBusy'] = True
    else:
        runtime.stopped_boundary = None
    old = copy.deepcopy(session['collaborationGeneration'])
    try:
        with pytest.raises(AppError):
            await fresh(app)
        assert session['collaborationGeneration'] == old
        assert len(session['messages']) == 1
        runtime.send.assert_not_awaited()
        runtime.steer.assert_not_awaited()
        assert not app.db.execute("SELECT 1 FROM commands WHERE id='fresh'").fetchone()
    finally:
        if held:
            held.release()


async def test_explicit_generation_target_does_not_become_new_work(failed):
    app, runtime, _, session = failed
    await fresh(app, expectedGenerationId='lost', allowNextTurn=True)
    runtime.steer.assert_awaited_once()
    runtime.send.assert_not_awaited()
    assert not session['collaborationGeneration']['terminal']


async def test_invalid_input_cannot_settle_generation(failed):
    app, runtime, _, session = failed
    with pytest.raises(AppError, match='Enter a message'):
        await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': ' '})
    assert not session['collaborationGeneration']['terminal']
    runtime.send.assert_not_awaited()


@pytest.mark.parametrize('exited', [True, False])
async def test_reader_failure_settles_only_confirmed_process_exit(exited):
    manager = RuntimeManager()
    process = await asyncio.create_subprocess_exec(sys.executable, '-c',
        'pass' if exited else 'import time; time.sleep(30)', stdout=asyncio.subprocess.PIPE)
    events = []
    async def emit(kind, payload):
        events.append((kind, payload))
    ready = asyncio.get_running_loop().create_future()
    ready.set_result({})
    row = {'process': process, 'emit': emit, 'ready': ready, 'closing': False,
        'pending': {}, 'runtimeOwnerId': 'exact-owner', 'backgroundCalls': {'call'}}
    try:
        if exited:
            await process.wait()
        process.stdout.set_exception(OSError('Read failed'))
        await manager._read('root', row)
        assert events[0][0] == 'runtime.error'
        assert events[0][1]['errorType'] == 'WorkerCommunicationError'
        ended = [payload for kind, payload in events if kind == 'runtime.ended']
        assert bool(ended) is exited
        if exited:
            assert ended == [{'sessionId': 'root', 'status': 'interrupted',
                'runtimeOwnerId': 'exact-owner', 'backgroundCallIds': ['call']}]
            await manager._execution_ended('root', row, 'interrupted')
            assert len(events) == 2
        else:
            assert process.returncode is None and not row.get('executionEnded')
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
