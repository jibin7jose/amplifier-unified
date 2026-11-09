"""Real queue/resource/operation journals with controlled guest observations."""
# Reused pytest fixtures are imported by name and requested as test arguments.
# ruff: noqa: F401, F811
import asyncio

from filelock import FileLock
import pytest

from amplifier_web import task_environments as environments
from amplifier_web.test_queue import TestQueue
from test_configuration_authority import app
from test_coordination_generation import until
from test_task_environments import backend, scope, start
from test_test_queue import drive, queue, record, submit


@pytest.fixture
def running(monkeypatch, backend, queue):
    queue.poll_seconds = 0.05
    if hasattr(environments, 'dependencies'):
        # This fixture controls lifecycle/guest effects. Image preparation has
        # its own physical acceptance; never download/build from a unit fixture.
        monkeypatch.setattr(environments.dependencies, 'prepare', lambda config, root, snapshot, profile_id, request_id, **kwargs: {
            'status': 'ready', 'profileId': profile_id, 'sourceSha256': snapshot['archive_sha256'],
            'backend': config.get('backend', 'docker'), 'image': 'sha256:'+'a'*64})
        monkeypatch.setattr(environments.dependencies, 'cleanup', lambda *args: {'complete': True, 'status': 'removed'})
    invoke = environments.invoke
    states, starts = {}, []
    def guest(config, home, row, action, args):
        result = invoke(config, home, row, action, args)
        identity = args.get('requestId')
        if action == 'execute' and args.get('durableJob'):
            assert identity not in starts
            starts.append(identity)
            states[identity] = 'running'
        if action == 'job_cancel':
            states[identity] = 'cancelled'
        if action in {'execute', 'job_read', 'job_cancel'} and identity in states:
            state = states[identity]
            result['job'] = {'version': 1, 'id': identity, 'state': state,
                             'terminal': state in {'passed', 'failed', 'cancelled', 'timed_out'}}
            result['result']['exit_code'] = 0 if state == 'passed' else None
        return result
    monkeypatch.setattr(environments, 'invoke', guest)
    return states, starts


async def admitted(queue, app):
    await queue.tick()
    await until(lambda: record(queue, app)['job']['status'] == 'checking')
    await until(lambda: record(queue, app)['job'].get('check', {}).get('state') == 'running')


async def test_restart_during_check_observes_same_job_without_cleanup_or_replay(app, queue, running, backend):
    states, starts = running
    await submit(app, profileId='browser-test', timeoutSeconds=1200)
    await admitted(queue, app)
    receipt = record(queue, app)
    assert receipt['job']['profileId'] == 'browser-test'
    assert receipt['job']['timeoutSeconds'] == 1200
    row = environments.get(app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
    assert row['profileId'] == 'browser-test'
    from amplifier_web import test_execution_settings
    test_execution_settings.write(app, {'version': 1, 'queue': {}, 'profiles': {
        'browser-test': {'defaultTimeoutSeconds': 1, 'maxTimeoutSeconds': 1, 'cpus': 1}}})
    assert row['timingRequest']['maxTimeoutSeconds'] == 1800
    assert row['resourceRequest']['cpus'] == 4
    await queue.close()  # Host disappears; the independently admitted guest runs.
    assert len(starts) == 1 and not any(action == 'stop' for action, _ in backend)
    recovered = app.test_queue = TestQueue(app)
    states[starts[0]] = 'passed'
    await drive(recovered)
    assert record(recovered, app)['job']['status'] == 'passed'
    assert len(starts) == 1
    assert [action for action, _ in backend].count('job_read') == 1
    assert [action for action, _ in backend][-2:] == ['stop', 'remove']


async def test_cancel_observes_terminal_before_cleanup(app, queue, running, backend):
    queue.poll_seconds = 5
    await submit(app, profileId='python-project', timeoutSeconds=1800)
    await admitted(queue, app)
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
    await asyncio.wait_for(asyncio.gather(*queue.tasks.values()), 1)
    assert record(queue, app)['job']['status'] == 'cancelled'
    actions = [action for action, _ in backend]
    assert actions.index('job_cancel') < actions.index('stop') < actions.index('remove')
    assert len(running[1]) == 1
    assert not queue.job_wakeups


async def test_missing_supervisor_retains_owner_and_never_cleans_unknown_job(app, queue, running, backend):
    await submit(app)
    await admitted(queue, app)
    running[0][running[1][0]] = 'unknown'
    await asyncio.wait_for(asyncio.gather(*queue.tasks.values()), 4)
    receipt = record(queue, app)
    assert receipt['job']['status'] == 'unknown'
    row = environments.get(app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
    assert row['status'] != 'retired'
    assert not any(action in {'stop', 'remove'} for action, _ in backend)
    await submit(app)
    assert len(running[1]) == 1


async def test_two_root_jobs_keep_independent_identity_and_cancel(app, queue, running, backend):
    await submit(app)
    await admitted(queue, app)
    peer = app.state['sessions'][1]
    args = {**scope(app), 'sessionId': peer['id'], 'requestId': 'check',
            'profileId': 'linux-python', 'command': 'true', 'notify': False}
    await app.dispatch('workspace.tests.submit', args)
    await queue.tick()
    await until(lambda: len(running[1]) == 2)
    first, second = running[1]
    assert first != second
    running[0][second] = 'passed'
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
    await asyncio.wait_for(asyncio.gather(*queue.tasks.values()), 4)
    assert record(queue, app)['job']['status'] == 'cancelled'
    assert queue.get(queue.identity(peer['id'], 'check'))['job']['status'] == 'passed'


async def test_guest_admission_lost_response_recovers_exact_result(app, queue, running, backend, monkeypatch):
    invoke = environments.invoke
    def lost(config, home, row, action, args):
        result = invoke(config, home, row, action, args)
        if action == 'execute':
            running[0][args['requestId']] = 'passed'
            raise TimeoutError('Lost durable admission reply')
        return result
    monkeypatch.setattr(environments, 'invoke', lost)
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert len(running[1]) == 1
    assert [action for action, _ in backend].count('job_read') == 1


@pytest.mark.parametrize('preparation_pending', [False, True])
async def test_queued_execute_lock_refusal_preserves_resource_and_saved_result(app, queue, backend, preparation_pending):
    await submit(app)
    row = (await start(app))['resource']
    sid = row['sessionId']
    before = environments.save(app.data_dir, environments.get(app.data_dir, sid, row['id']),
        preparationPending=preparation_pending, pendingRequest='start')
    receipt = record(queue, app)
    receipt['job'].update(resourceId=row['id'], status='checking')
    queue.save(receipt)
    calls = list(backend)
    lock = app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')
    with FileLock(str(lock)):
        result = await queue.effect(receipt, environments.configuration(app)[0], 'execute')
        saved = app.operations.requests.read(sid, receipt['requestId'] + ':execute')
        assert saved == result
        assert environments.get(app.data_dir, sid, row['id']) == before
    assert result['state'] == 'failed'
    assert result['result']['status'] == 'not-started'
    assert result['result']['effectAdmitted'] is False
    assert result['result']['code'] == 'environment_lock_contended'
    assert 'durableJob' not in environments.get(app.data_dir, sid, row['id'])
    observed = (await app.dispatch('workspace.tests.result',
        {**scope(app), 'requestId': 'check'}))['result']
    assert observed['operation']['state'] == 'failed'
    assert observed['exitCode'] is None and observed['text'] == ''
    assert await queue.effect(receipt, environments.configuration(app)[0], 'execute') == saved
    assert app.operations.requests.read(sid, receipt['requestId'] + ':execute') == saved
    assert backend == calls
    assert environments.get(app.data_dir, sid, row['id']) == before


async def test_profile_timeout_limit_is_enforced_before_any_admission(app, queue, backend):
    with pytest.raises(ValueError, match='duration exceeds'):
        await submit(app, timeoutSeconds=301)
    assert not backend and not queue.rows()


async def test_old_receipt_uses_original_execute_and_recovery_path(app, queue, backend):
    await submit(app)
    saved = record(queue, app)
    saved['job'].pop('durableVersion')
    queue.save(saved)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']
    execute = next(args for action, args in backend if action == 'execute')
    assert 'durableJob' not in execute


async def test_direct_execute_cannot_overlap_running_queued_job(app, queue, running):
    await submit(app)
    await admitted(queue, app)
    receipt = record(queue, app)
    row = environments.get(app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
    args = {'requestId': 'overlap', 'expectedRevision': row['revision'], 'command': 'true', 'timeoutSeconds': 1}
    actor = receipt['job']['actor']
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.execute', args, actor)
    result = await asyncio.to_thread(environments.effect, app, row, 'execute', args,
        environments.configuration(app)[0], request, lambda: None)
    assert result['request']['state'] == 'failed'
    assert len(running[1]) == 1
    running[0][running[1][0]] = 'passed'
    await asyncio.gather(*queue.tasks.values())


async def test_passive_environment_read_does_not_call_running_guest_abandoned(app, queue, running):
    await submit(app)
    await admitted(queue, app)
    receipt = record(queue, app)
    identity = running[1][0]
    await asyncio.to_thread(environments.read, app, receipt['senderSessionId'], environments.configuration(app)[0])
    row = environments.get(app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
    assert row['pendingRequest'] == identity
    assert row['lastOperation']['state'] == 'running'
    assert row['environmentState'] == 'running'
    running[0][identity] = 'passed'
    await asyncio.gather(*queue.tasks.values())


async def test_restart_during_cleanup_observes_stopped_guest_without_job_read(app, queue, backend, monkeypatch):
    invoke = environments.invoke
    physical_stopped = False
    def lost_stop(config, home, row, action, args):
        nonlocal physical_stopped
        if action == 'stop':
            physical_stopped = True
            backend.append((action, args))
            raise TimeoutError('Stop committed but response was lost')
        if action == 'job_read':
            pytest.fail('A stopped guest cannot run a job observer')
        result = invoke(config, home, row, action, args)
        if action == 'read' and physical_stopped:
            result['status'] = 'stopped'
        return result
    monkeypatch.setattr(environments, 'invoke', lost_stop)
    effect = queue.effect
    async def interrupted(*args, **kwargs):
        result = await effect(*args, **kwargs)
        if args[2] == 'stop':
            raise asyncio.CancelledError
        return result
    monkeypatch.setattr(queue, 'effect', interrupted)
    await submit(app)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values(), return_exceptions=True)
    assert record(queue, app)['job']['status'] == 'stopping' and physical_stopped
    recovered = app.test_queue = TestQueue(app)
    await drive(recovered)
    assert record(recovered, app)['job']['status'] == 'passed'
    assert [action for action, _ in backend].count('stop') == 1
    assert [action for action, _ in backend][-2:] == ['read', 'remove']


async def test_unchanged_observation_does_not_rewrite_three_journals(app, queue, running, backend):
    await submit(app)
    await admitted(queue, app)
    receipt = record(queue, app)
    sid, identity = receipt['senderSessionId'], running[1][0]
    row = environments.get(app.data_dir, sid, receipt['job']['resourceId'])
    operation = app.operations.requests.read(sid, identity)
    await until(lambda: [action for action, _ in backend].count('job_read') >= 3)
    assert record(queue, app)['job']['updatedAt'] == receipt['job']['updatedAt']
    assert environments.get(app.data_dir, sid, row['id'])['revision'] == row['revision']
    assert app.operations.requests.read(sid, identity)['updatedAt'] == operation['updatedAt']
    running[0][identity] = 'passed'
    queue.wake_job(queue.identity(sid, 'check'))
    await asyncio.gather(*queue.tasks.values())
