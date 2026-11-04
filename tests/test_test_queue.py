"""Real dispatch/journals with controlled physical backend effects."""
import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from amplifier_web import task_environments as environments
from amplifier_web.service import AppError
from amplifier_web.test_queue import PENDING, TestQueue
from test_configuration_authority import app, grant, revoke
from test_coordination_generation import agent, until
from test_task_environments import backend, scope


@pytest.fixture
def queue(app, backend, monkeypatch):
    # Tick deterministically so races can be placed at actual await boundaries.
    monkeypatch.setattr(app.test_queue, 'start', lambda: None)
    monkeypatch.setattr(environments, 'capacity', lambda *args: {'available': True, 'counts': {'active': 0, 'stopped_retained': 15}})
    return app.test_queue


async def submit(app, identity='check', **extra):
    return await app.dispatch('workspace.tests.submit', {**scope(app), 'requestId': identity,
        'profileId': environments.PROFILE, 'command': 'python -V', 'notify': False, **extra})


async def drive(queue):
    await queue.tick()
    await asyncio.gather(*queue.tasks.values())


def record(queue, app, identity='check'):
    return queue.get(queue.identity(scope(app)['sessionId'], identity))


async def test_check_runs_once_exports_and_releases_ownership(app, queue, backend):
    await submit(app)
    await drive(queue)
    receipt = record(queue, app)
    assert receipt['job']['status'] == 'passed'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']
    row = environments.get(app.data_dir, scope(app)['sessionId'], receipt['job']['resourceId'])
    assert row['status'] == 'retired' and Path(environments.storage.directory(app.data_dir, row)).is_dir()
    assert (await submit(app))['duplicate']
    await submit(app, 'second')
    await drive(queue)
    assert record(queue, app, 'second')['job']['status'] == 'passed'
    assert len(backend) == 10


async def test_deep_delegated_work_can_submit_a_check_without_human_continue(app, queue, backend):
    source = app.state['sessions'][0]
    app.collaboration.insert('ancestor', 'retained-peer-input', {
        'commandAction': 'coordination.send', 'target': {'sessionId': source['id']},
        'causalDepth': 40, 'delivery': 'accepted'})
    source['collaborationGeneration']['inputIds'] = ['ancestor']
    args = {**scope(app), 'requestId': 'deep-check', 'profileId': environments.PROFILE,
            'command': 'python -V', 'notify': False}
    result = await agent(app, source, 'workspace.tests.submit', args, 'deep-submit')
    assert result['accepted']
    receipt = record(queue, app, 'deep-check')
    assert receipt['causalDepth'] == 41 and receipt['sourceInputIds'] == ['ancestor']
    await drive(queue)
    assert record(queue, app, 'deep-check')['job']['status'] == 'passed'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']


async def test_full_capacity_waits_without_model_polling_then_runs(app, queue, backend, monkeypatch):
    available = False
    monkeypatch.setattr(environments, 'capacity', lambda *args: {'available': available, 'blockers': [{'code': 'execution_capacity'}]})
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'queued' and not backend
    available = True
    queue.backoff.clear()  # Controlled clock: the external capacity retry becomes due.
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'


async def test_waiting_cancel_and_stop_never_start_backend(app, queue, backend):
    await submit(app)
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'cancelled' and not backend
    await submit(app, 'stopped')
    app.state['sessions'][0]['interruptionRevision'] = 42
    await drive(queue)
    assert record(queue, app, 'stopped')['job']['status'] == 'cancelled' and not backend


async def test_cancel_during_allocation_cannot_be_overwritten(app, queue, backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = environments.prepare
    def delayed(*args):
        row = original(*args)
        entered.set()
        assert release.wait(5)
        return row
    monkeypatch.setattr(environments, 'prepare', delayed)
    await submit(app)
    await queue.tick()
    await until(entered.is_set)
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
    release.set()
    await asyncio.gather(*queue.tasks.values())
    receipt = record(queue, app)
    assert receipt['job']['status'] == 'cancelled' and receipt['job']['cancelRequested']
    assert receipt['subscription']['status'] == 'suppressed' and not backend
    row = environments.get(app.data_dir, scope(app)['sessionId'], receipt['job']['resourceId'])
    assert row['status'] == 'retired'


async def test_expiry_while_waiting_admission_does_not_start(app, queue, backend):
    await submit(app)
    await queue.admission.acquire()
    task = asyncio.create_task(queue.run(queue.identity(scope(app)['sessionId'], 'check'), environments.configuration(app)[0]))
    receipt = record(queue, app)
    receipt['job']['expiresAt'] = time.time()-1
    queue.save(receipt)
    queue.admission.release()
    await task
    assert record(queue, app)['job']['status'] not in PENDING and not backend


async def test_stop_cancels_owned_check_without_killing_queue(app, queue, backend):
    source = app.state['sessions'][0]
    args = {**scope(app), 'requestId': 'check', 'profileId': environments.PROFILE, 'command': 'true', 'notify': False}
    await agent(app, source, 'workspace.tests.submit', args, 'agent-submit')
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'cancelled' and not backend
    await submit(app, 'user-check')
    await drive(queue)
    assert record(queue, app, 'user-check')['job']['status'] == 'passed'


async def test_uncertain_command_is_not_repeated_and_environment_is_preserved(app, queue, backend, monkeypatch):
    invoke = environments.invoke
    def uncertain(config, home, row, action, args):
        if action == 'execute':
            backend.append((action, args))
            raise TimeoutError('command acknowledgement lost')
        if action == 'job_read':
            backend.append((action, args))
            return {'ok': True, 'job': {'id': args['requestId'], 'state': 'unknown', 'terminal': False}}
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', uncertain)
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'unknown'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'job_read']
    await submit(app)
    await drive(queue)
    assert len(backend) == 4


async def test_capacity_race_known_refusal_can_retry_without_orphan(app, queue, backend, monkeypatch):
    invoke, denied = environments.invoke, True
    def refuse_once(config, home, row, action, args):
        nonlocal denied
        if action == 'start' and denied:
            denied = False
            return {'ok': False, 'admissionDenied': True, 'status': 'not-started'}
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', refuse_once)
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'queued'
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert [action for action, _ in backend].count('execute') == 1


async def test_missing_prerequisite_is_known_failure_without_retry_or_retained_slot(app, queue, backend, monkeypatch):
    invoke = environments.invoke
    def unavailable(config, home, row, action, args):
        if action == 'start':
            backend.append((action, args))
            return {'ok': False, 'effectAdmitted': False, 'status': 'not-started',
                    'code': 'approved_image_missing', 'message': 'The approved image is unavailable.'}
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', unavailable)
    await submit(app)
    await drive(queue)
    receipt = record(queue, app)
    assert receipt['job']['status'] == 'failed'
    assert receipt['job']['launch']['state'] == 'failed'
    row = environments.get(app.data_dir, scope(app)['sessionId'], receipt['job']['resourceId'])
    assert row['status'] == 'retired' and not row['backendAdmitted']
    assert Path(environments.storage.directory(app.data_dir, row)).is_dir()
    await submit(app)
    await drive(queue)
    assert [action for action, _ in backend] == ['prepare', 'start']
    monkeypatch.setattr(environments, 'invoke', invoke)
    await submit(app, 'new-check')
    await drive(queue)
    assert record(queue, app, 'new-check')['job']['status'] == 'passed'


async def test_restart_after_check_uses_journal_and_only_cleans_up(app, queue, backend, monkeypatch):
    await submit(app)
    async def lost(*args, **kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(queue, 'cleanup', lost)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values(), return_exceptions=True)
    assert record(queue, app)['job']['status'] == 'stopping'
    # Fresh scheduler over the same durable journals: it cannot rerun a check.
    recovered = TestQueue(app)
    app.test_queue = recovered
    await drive(recovered)
    assert record(recovered, app)['job']['status'] == 'passed'
    assert [action for action, _ in backend].count('execute') == 1
    assert [action for action, _ in backend][-2:] == ['stop', 'remove']


async def test_cleanup_failure_never_claims_removal_or_replays_check(app, queue, backend, monkeypatch):
    invoke = environments.invoke
    def fail(config, home, row, action, args):
        if action == 'remove':
            return {'ok': False, 'status': 'unknown', 'message': 'export failed'}
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', fail)
    await submit(app)
    await drive(queue)
    receipt = record(queue, app)
    assert receipt['job']['status'] == 'unknown'
    assert environments.get(app.data_dir, scope(app)['sessionId'], receipt['job']['resourceId'])['status'] != 'retired'
    await drive(queue)
    assert [action for action, _ in backend].count('execute') == 1


async def test_conflicting_identity_and_other_root_are_rejected(app, queue):
    await submit(app)
    with pytest.raises(AppError, match='different arguments'):
        await submit(app, command='other-command')
    source, peer = app.state['sessions']
    with pytest.raises(AppError):
        await agent(app, source, 'workspace.tests.cancel', {**scope(app), 'sessionId': peer['id'], 'requestId': 'check'}, 'peer-cancel')


async def test_result_pages_read_exact_execution_without_duplicating_output_in_queue(app, queue, backend, monkeypatch):
    invoke = environments.invoke
    content = 'x'*12005
    def long_output(config, home, row, action, args):
        value = invoke(config, home, row, action, args)
        if action == 'execute':
            value['result']['stdout'] = content
        return value
    monkeypatch.setattr(environments, 'invoke', long_output)
    await submit(app)
    await drive(queue)
    assert 'result' not in record(queue, app)['job']['check']
    result, offset = '', 0
    while offset is not None:
        page = (await app.dispatch('workspace.tests.result', {**scope(app), 'requestId': 'check', 'offset': offset}))['result']
        assert len(page['text']) <= 6000 and page['exitCode'] == 0
        result += page['text']
        offset = page['nextOffset']
    assert result == content
    assert [action for action, _ in backend].count('execute') == 1


async def test_phase_save_failure_recovers_recorded_check_and_cleans_owned_resource(app, queue, backend, monkeypatch):
    save, failed = queue.save, False
    def fail_once(receipt):
        nonlocal failed
        if receipt['job']['status'] == 'stopping' and not failed:
            failed = True
            raise OSError('temporary queue journal write failure')
        save(receipt)
    monkeypatch.setattr(queue, 'save', fail_once)
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert [action for action, _ in backend].count('execute') == 1
    assert [action for action, _ in backend][-2:] == ['stop', 'remove']


async def test_scheduler_survives_one_failed_reconciliation(app, queue, monkeypatch):
    calls = 0
    async def tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            queue.wakeup.set()
            raise OSError('temporary observation failure')
        queue.closing = True
        queue.wakeup.set()
    monkeypatch.setattr(queue, 'tick', tick)
    await queue.loop()
    assert calls == 2


async def test_stale_root_cannot_cancel_a_current_saved_check(app, queue, backend):
    source = app.state['sessions'][0]
    await submit(app)
    with pytest.raises(AppError, match='generation'):
        await agent(app, source, 'workspace.tests.cancel', {**scope(app), 'requestId': 'check'},
                    'stale-cancellation', _generationId='retired-generation')
    assert record(queue, app)['job']['status'] == 'queued'


@pytest.mark.parametrize('character', ['\0', '界', '😀'])
async def test_result_page_fits_actual_encoded_delivery_budget(app, queue, backend, monkeypatch, character):
    invoke = environments.invoke

    def output(config, home, row, action, args):
        value = invoke(config, home, row, action, args)
        if action == 'execute':
            value['result']['stdout'] = character * 9000
        return value

    monkeypatch.setattr(environments, 'invoke', output)
    await submit(app)
    await drive(queue)
    result = await agent(app, app.state['sessions'][0], 'workspace.tests.result',
                         {**scope(app), 'requestId': 'check'}, 'read-output')
    size = len(json.dumps(result, ensure_ascii=False).encode())
    assert size < 32768, f'Actual compact app_control receipt is {size} encoded bytes'


@pytest.mark.parametrize('phase', ['start', 'execute', 'stop', 'remove'])
async def test_projection_failure_preserves_confirmed_queue_outcome(app, queue, backend, monkeypatch, phase):
    save, failed = environments.save, False

    def fail_projection(home, row, **changes):
        nonlocal failed
        operation = changes.get('lastOperation', {})
        if operation.get('action') == 'workspace.environments.' + phase and not failed:
            failed = True
            assert operation['state'] == 'completed'
            raise OSError('projection failed after exact operation committed')
        return save(home, row, **changes)

    monkeypatch.setattr(environments, 'save', fail_projection)
    await submit(app)
    await drive(queue)
    receipt = record(queue, app)
    assert failed and receipt['job']['status'] == 'passed'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']
    row = environments.get(app.data_dir, scope(app)['sessionId'], receipt['job']['resourceId'])
    assert row['status'] == 'retired' and not row.get('pendingRequest')


async def test_pending_completed_removal_repairs_before_next_queue_check(app, queue, backend, monkeypatch):
    save = environments.save

    def fail_projection(home, row, **changes):
        if changes.get('lastOperation', {}).get('action') == 'workspace.environments.remove':
            raise OSError('removal projection unavailable')
        return save(home, row, **changes)

    monkeypatch.setattr(environments, 'save', fail_projection)
    await submit(app)
    await drive(queue)
    receipt = record(queue, app)
    assert receipt['job']['status'] == 'passed'
    row = environments.get(app.data_dir, scope(app)['sessionId'], receipt['job']['resourceId'])
    assert row['status'] != 'retired' and row.get('pendingRequest')
    monkeypatch.setattr(environments, 'save', save)
    calls = list(backend)
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert page['items'][0]['status'] == 'removed' and backend == calls
    await submit(app, 'after-repair')
    await drive(queue)
    assert record(queue, app, 'after-repair')['job']['status'] == 'passed'
    assert [action for action, _ in backend].count('execute') == 2


async def test_known_preservation_failure_uses_separate_cleanup_without_repeating_check(app, queue, backend, monkeypatch):
    original = environments.invoke
    def invoke(config, home, row, action, args):
        if action in {'remove','finishCleanup'}:
            backend.append((action, dict(args)))
            if action == 'remove':
                return {'ok': False, 'status': 'stopped', 'effectAdmitted': False,
                        'phase': 'verifying', 'code': 'archive_verification_failed'}
            return {'ok': True, 'status': 'removed'}
        return original(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', invoke)
    await submit(app)
    await drive(queue)
    receipt = record(queue, app)
    assert receipt['job']['status'] == 'passed'
    assert receipt['job']['previousRemoval']['state'] == 'failed'
    assert [action for action, _ in backend] == ['prepare','start','execute','stop','remove','finishCleanup']
    assert backend[-1][1]['previousRequestId'].endswith(':remove')
    await submit(app, 'next')
    await drive(queue)
    assert record(queue, app, 'next')['job']['status'] == 'passed'


@pytest.mark.parametrize('phase,code', [
    ('rechecking', 'ownership_conflict'), ('verifying', 'owner_unverified'),
    ('verifying', 'retention_storage'), ('verifying', 'unrecognized_failure'),
    ('verifying', None),
])
async def test_owner_recheck_failure_does_not_admit_cleanup_continuation(app, queue, backend, monkeypatch, phase, code):
    original = environments.invoke
    def invoke(config, home, row, action, args):
        if action == 'remove':
            backend.append((action, dict(args)))
            return {'ok': False, 'status': 'unknown', 'effectAdmitted': False,
                    'phase': phase, 'code': code}
        return original(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', invoke)
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'unknown'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']


async def test_workspace_expiry_intent_is_captured_once_and_keep_is_explicit(app, queue, backend):
    from amplifier_web import storage_cleanup
    storage_cleanup.store(app, storage_cleanup.POLICY,
        {**storage_cleanup.DEFAULTS, 'revision': 1, 'workspaceArchiveKeepDays': 7})
    await submit(app, 'temporary')
    await submit(app, 'retained', keepWorkspace=True)
    # Later policy edits cannot silently opt an already queued job in or shorten
    # its saved duration. Restart/receipt reuse retains the same admission choice.
    storage_cleanup.store(app, storage_cleanup.POLICY,
        {**storage_cleanup.DEFAULTS, 'revision': 2, 'workspaceArchiveKeepDays': 1})
    await drive(queue)
    receipt = record(queue, app, 'temporary')
    row = environments.get(app.data_dir, scope(app)['sessionId'], receipt['job']['resourceId'])
    assert row['workspaceArchivePolicy'] == {'version': 1, 'disposition': 'temporary', 'keepDays': 7, 'policyRevision': 1}
    assert (await submit(app, 'temporary'))['duplicate']
    await drive(queue)
    kept = record(queue, app, 'retained')
    assert kept['job']['workspaceArchivePolicy'] is None
    row = environments.get(app.data_dir, scope(app)['sessionId'], kept['job']['resourceId'])
    assert row.get('workspaceArchivePolicy') is None
