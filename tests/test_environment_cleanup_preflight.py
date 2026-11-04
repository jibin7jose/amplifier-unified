"""Cleanup may continue after a proven pre-dispatch failure, never a lost effect."""
import json
import sys

import pytest

from amplifier_web import managed_runner_updates, task_environments as owner
from test_configuration_authority import app
from test_task_environments import act, backend, start
from test_test_environment_worker import allocated

REAL_INVOKE = owner.invoke


@pytest.mark.parametrize('action', ['remove', 'finishCleanup'])
@pytest.mark.parametrize('boundary', ['qualification', 'retained-qualification', 'dependencies', 'executable'])
def test_cleanup_preflight_failure_never_starts_helper(tmp_path, monkeypatch, action, boundary):
    home, row = allocated(tmp_path)
    config = {'python': sys.executable}
    calls = []
    marker = 'private-preflight-diagnostic'

    def refuse(*args, **kwargs):
        raise ValueError(marker)

    def launch(*args, **kwargs):
        calls.append('Popen')
        raise FileNotFoundError(marker)

    monkeypatch.setattr(owner.subprocess, 'Popen', launch)
    if boundary == 'qualification':
        monkeypatch.setattr(managed_runner_updates, 'validate_execution', refuse)
    elif boundary == 'retained-qualification':
        config['originSnapshotVersion'] = 1
        config['remoteRunner'] = {'id': 'original'}
        monkeypatch.setattr(owner.registered_runners, 'qualify_retained', refuse)
        monkeypatch.setattr(owner.registered_runners, 'invoke', lambda *a: pytest.fail('remote dispatch'))
    elif boundary == 'dependencies':
        row.update(preparedDependencies={'status': 'ready'}, snapshot={}, profileId='browser-test')
        monkeypatch.setattr(owner.dependencies, 'validate_receipt', refuse)
    result = REAL_INVOKE(config, home, row, action, {'requestId': 'cleanup-preflight'})
    assert result['effectAdmitted'] is False and result['status'] == 'stopped'
    assert (result['phase'], result['code']) == ('preparing', 'cleanup_helper_not_started')
    assert calls == (['Popen'] if boundary == 'executable' else [])
    assert marker not in json.dumps(result)


@pytest.mark.parametrize('source', [
    'import sys; sys.exit(1)',
    "print('invalid-json')",
    "import os,signal; os.kill(os.getpid(),signal.SIGTERM)",
])
def test_started_cleanup_helper_failure_remains_uncertain(tmp_path, monkeypatch, source):
    home, row = allocated(tmp_path)
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'test_environment_worker.py').write_text(source)
    with pytest.raises(ValueError):
        REAL_INVOKE({'python': sys.executable}, home, row, 'remove', {'requestId': 'started-cleanup'})


async def test_real_failed_helper_launch_can_continue_without_rewriting_request(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop-before-cleanup'))['resource']
    simulated = owner.invoke
    # The controlled backend uses /managed/python, which is intentionally absent.
    monkeypatch.setattr(owner, 'invoke', REAL_INVOKE)
    failed = await act(app, 'remove', row, 'failed-helper-launch')
    assert failed['request']['state'] == 'failed'
    assert failed['resource']['status'] == 'stopped'
    assert failed['request']['result']['code'] == 'cleanup_helper_not_started'
    calls = []

    def finish(config, home, current, action, args):
        calls.append(action)
        return simulated(config, home, current, 'remove', args)

    monkeypatch.setattr(owner, 'invoke', finish)
    completed = await act(app, 'finishCleanup', failed['resource'], 'fresh-cleanup',
                          previousRequestId='failed-helper-launch')
    assert completed['request']['state'] == 'completed'
    assert completed['resource']['status'] == 'removed'
    assert app.operations.requests.read(row['sessionId'], 'failed-helper-launch') == failed['request']
    assert calls == ['finishCleanup']
    duplicate = await act(app, 'finishCleanup', failed['resource'], 'fresh-cleanup',
                          previousRequestId='failed-helper-launch')
    assert duplicate['request'] == completed['request'] and calls == ['finishCleanup']


@pytest.mark.parametrize('phase,code', [
    ('preparing', 'cleanup_helper_not_started'),
    ('preserving', 'preservation_failed'),
    ('preserving', 'retention_storage'),
    ('verifying', 'archive_verification_failed'),
])
@pytest.mark.parametrize('boundary', ['journal', 'projection'])
async def test_host_persistence_failure_preserves_returned_predispatch_receipt(
        app, backend, monkeypatch, phase, code, boundary):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop-before-refusal'))['resource']
    refusal = {'ok': False, 'status': 'stopped', 'effectAdmitted': False,
               'phase': phase, 'code': code, 'message': 'Removal was not dispatched.'}
    monkeypatch.setattr(owner, 'invoke', lambda *a: dict(refusal))
    original_finish, original_project = app.operations.requests.finish, owner.project_result
    injected = []

    def finish(record, state, **fields):
        if boundary == 'journal' and state == 'failed' and not injected:
            injected.append(True)
            raise OSError('private journal error')
        return original_finish(record, state, **fields)

    def project(*args):
        if boundary == 'projection' and not injected:
            injected.append(True)
            raise OSError('private projection error')
        return original_project(*args)

    monkeypatch.setattr(app.operations.requests, 'finish', finish)
    monkeypatch.setattr(owner, 'project_result', project)
    failed = await act(app, 'remove', row, 'refused-cleanup')
    assert injected == [True]
    assert failed['request']['state'] == 'failed'
    assert failed['request']['result'] == refusal
    assert failed['resource']['status'] == 'stopped'
    assert 'private' not in json.dumps(failed['request'])


@pytest.mark.parametrize('phase,effect', [('verifying', False), ('removing', False),
                                          ('preserving', True), ('preserving', None)])
async def test_storage_failure_needs_exact_predispatch_proof(app, backend, monkeypatch, phase, effect):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'storage-stop'))['resource']
    monkeypatch.setattr(owner, 'invoke', lambda *a: {
        'ok': False, 'status': 'stopped', 'phase': phase,
        'code': 'retention_storage', 'effectAdmitted': effect})
    failed = await act(app, 'remove', row, 'unproven-storage')
    original = failed['request']
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    monkeypatch.setattr(owner, 'invoke', lambda *a: pytest.fail('unproven cleanup invoked'))
    if effect is not False:
        from amplifier_web.service import AppError
        with pytest.raises(AppError, match='original removal remains unresolved'):
            await act(app, 'finishCleanup', failed['resource'], 'refused-storage',
                      previousRequestId='unproven-storage')
    else:
        refused = await act(app, 'finishCleanup', failed['resource'], 'refused-storage',
                            previousRequestId='unproven-storage')
        assert refused['request']['state'] == 'failed'
    assert app.operations.requests.read(row['sessionId'], 'unproven-storage') == original
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before


async def test_cleanup_helper_loss_with_retained_preparation_remains_unknown(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'unknown-stop'))['resource']
    row = owner.save(app.data_dir, owner.get(app.data_dir, row['sessionId'], row['id']),
                     preparationPending=True)
    calls = []
    def lost(*args):
        calls.append('remove')
        raise TimeoutError('helper outcome is unavailable')
    monkeypatch.setattr(owner, 'invoke', lost)
    unknown = await act(app, 'remove', row, 'lost-helper')
    assert calls == ['remove']
    assert unknown['request']['state'] == 'outcome_unknown'
    assert unknown['request']['result'].get('effectAdmitted') is not False
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['preparationPending'] is True


@pytest.mark.parametrize('message,code', [
    ('The local cleanup executor changed during inspection. No removal was started.', 'cleanup_executor_changed'),
    ('The fresh cleanup inspection changed before admission.', 'cleanup_qualification_changed'),
    ('The original removal remains unresolved. Inspect its retained evidence; a new request cannot repeat it.', 'original_cleanup_unresolved'),
    ('The original removal admission cannot be rebound or replayed.', 'cleanup_admission_changed'),
    ('The approved backend changed. Restore its exact configuration before managing this environment.', 'backend_configuration_changed'),
    ('The retained environment directory changed; no external action was attempted.', 'environment_directory_changed'),
    ('private /source/path or credential-like detail', 'cleanup_admission_refused'),
])
async def test_unstarted_cleanup_has_bounded_refusal_reason(app, backend, monkeypatch, message, code):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'reason-stop'))['resource']
    def refuse(*args):
        raise ValueError(message)
    monkeypatch.setattr(owner.recovery, 'bind_configuration', refuse)
    result = await act(app, 'remove', row, 'local-refusal')
    assert result['request']['state'] == 'failed'
    assert result['request']['result']['effectAdmitted'] is False
    assert result['request']['result']['code'] == code
    assert message not in json.dumps(result['request'])
    assert [name for name, _ in backend] == ['prepare', 'start', 'stop']
