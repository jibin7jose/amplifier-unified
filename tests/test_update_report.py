import asyncio
import json
import shutil
import subprocess

import pytest

from amplifier_web import runtime_environment as environments, update_report
from test_runtime_updates import environment


@pytest.fixture
def changed_worker(environment):
    manager, baseline, row, old, new, repo = environment
    generation = 'a' * 32
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    for src, dst in [('pyproject.toml', 'runtime.toml'), ('pyproject.toml', 'runtime-base.toml'), ('uv.lock', 'runtime.lock')]:
        shutil.copy2(baseline / src, receipt / dst)
    (receipt / 'runtime-project.json').write_text(json.dumps({'project': 'q-' + 'b' * 32}))
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    project = environments.project_path(manager.home, generation)
    project.mkdir(parents=True)
    shutil.copy2(baseline / 'uv.lock', project / 'uv.lock')
    (receipt / 'validated.json').write_text('{}')
    (receipt / 'runtime-sources.json').write_text(json.dumps({'fixture-runtime': {'url': repo.as_uri(), 'ref': 'main', 'subdirectory': ''}}))
    direct = {'url': repo.as_uri(), 'vcs_info': {'vcs': 'git', 'requested_revision': old, 'commit_id': old}}
    recorded = [{'name': 'fixture-runtime', 'version': '0.1.0', 'directUrl': direct}]
    (receipt / 'runtime-installed.json').write_text(json.dumps(recorded))
    metadata = project / '.venv/lib/python3.13/site-packages/fixture_runtime-0.1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Name: fixture-runtime\nVersion: 0.1.0\n')
    (metadata / 'direct_url.json').write_text(json.dumps({'url': repo.as_uri(), 'dir_info': {'editable': True}}))
    return manager, project, receipt, metadata, row


def test_report_explains_drift_without_disclosing_sources(changed_worker):
    manager, project, receipt, metadata, row = changed_worker
    result = update_report.runtime_report(manager.home)
    assert result['sourceChanges'][0]['recorded']['kind'] == 'git'
    assert result['sourceChanges'][0]['installed']['kind'] == 'editable'
    assert str(manager.home.parent) not in json.dumps(result)
    assert 'file:' not in json.dumps(result)
    assert 'recovery' not in result


async def test_diagnostics_action_reads_without_changing_state(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    service.update_manager = UpdateManager(service)
    before = service.state['revision']
    result = await service.dispatch('updates.diagnostics', {}, include_state=False)
    assert result['accepted'] and result['effects'] == []
    assert result['result']['schema'] == 'amplifier-update-diagnostics-v1'
    assert service.state['revision'] == before
    assert not service.update_manager.lock.locked()
    await service.close()

@pytest.mark.parametrize('stage,reason',[
    ('release_revision','git_missing'),('release_metadata','timeout'),
    ('SECRET','timeout'),('release_metadata','SECRET'),([],{}),
])
async def test_release_check_report_has_only_closed_boundary_facts(stage,reason,tmp_path,monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=service.update_manager=UpdateManager(service)
    service.state['updates']['application']={'status':'check_failed','checkStage':stage,'checkReason':reason,
        'detail':'SECRET stderr credential /private/path'}
    monkeypatch.setattr(update_report,'runtime_report',lambda home:{})
    try:
        before=json.dumps(service.state['updates'],sort_keys=True)
        result=await service.dispatch('updates.diagnostics',{},include_state=False)
        report=result['result']
        assert json.dumps(service.state['updates'],sort_keys=True)==before
        assert 'SECRET' not in json.dumps(report) and '/private/path' not in json.dumps(report)
        if isinstance(stage,str) and stage.startswith('release_') and reason in ('git_missing','timeout'):
            assert report['releaseCheck']=={'checkStage':stage,'checkReason':reason}
        else:assert 'releaseCheck' not in report
    finally:await service.close()


def test_source_redaction_handles_credential_urls_and_custom_hosts():
    source = update_report.source({'name': 'amplifier-fixture', 'version': '1.0', 'directUrl': {
        'url': 'https://private-account:secret@internal-host/private/repo?access_token=secret',
        'vcs_info': {'vcs': 'git', 'commit_id': 'f' * 40}}})
    text = json.dumps(source)
    assert all(value not in text for value in ('private-account', 'secret', 'internal-host', '/repo'))
    assert source['revision'] == 'f' * 40


async def test_failure_report_remains_available_when_runtime_cannot_be_inspected(changed_worker, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.updates import UpdateManager
    manager, *_ = changed_worker
    manager.requires_idle = UpdateManager.requires_idle
    manager.lock = asyncio.Lock()
    manager.service = SimpleNamespace(state={'updates': {'error': 'private exception', 'diagnostics': {
        'lastFailure': {'phase': 'ecosystem-runtime-preflight', 'errorType': 'ValueError', 'reason': 'protected-runtime-source',
                        'package': 'amplifier-fixture', 'message': 'secret', 'path': '/private/path'},
        'events': []}}})
    def fail(home): raise OSError('secret path')
    monkeypatch.setattr(update_report, 'runtime_report', fail)
    report = await update_report.collect(manager)
    assert report['runtime']['unavailable']
    assert report['lastFailure']['reason'] == 'protected-runtime-source'
    assert all(value not in json.dumps(report) for value in ('secret', '/private/path', 'private exception'))


async def test_report_contains_bounded_scheduler_state_and_check_history(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = service.update_manager = UpdateManager(service)
    service.state['updates'].update(phase='installed', detail='private path', available=0, lastCheck=123,
        sequence={'stage':'included', 'nextStage':'other', 'install':True, 'private':'secret'},
        recentChecks=[{'startedAt': n, 'finishedAt': n+1, 'elapsedMs':1000, 'requests':2, 'secret':'private path'} for n in range(8)])
    monkeypatch.setattr(update_report, 'runtime_report', lambda home: {})
    report = await update_report.collect(manager)
    assert report['updateState']['nextStage'] == 'other'
    assert report['updateState']['installRequested'] is True
    assert report['updateState']['revision'] == service.state['revision']
    assert len(report['recentChecks']) == 5
    assert report['recentChecks'][0]['startedAt'] == 3
    assert 'private path' not in json.dumps(report) and 'secret' not in json.dumps(report)
    await service.close()


async def test_gate_observation_revision_precedes_async_inventory(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = service.update_manager = UpdateManager(service)
    revision = service.state['revision']
    original_to_thread = asyncio.to_thread

    async def inventory_boundary(function, *args):
        service.state['revision'] += 1
        service.state['voicePreviewBusy'] = True
        return {}

    monkeypatch.setattr(update_report.asyncio, 'to_thread', inventory_boundary)
    try:
        report = await update_report.collect(manager)
    finally:
        monkeypatch.setattr(update_report.asyncio, 'to_thread', original_to_thread)
    assert report['activationGate']['stateRevision'] == revision
    assert report['activationGate']['blocked'] is False
    assert service.state['revision'] == revision + 1 and manager.busy()
    await service.close()


async def test_failed_gate_observation_is_unavailable_not_clear(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = service.update_manager = UpdateManager(service)
    def fail():
        raise ValueError('SECRET_OBSERVER /private/path')
    service.runtime.has_pending_operations = fail
    monkeypatch.setattr(update_report, 'runtime_report', lambda home: {})
    report = await update_report.collect(manager)
    assert report['activationGate']['unavailable']
    assert report['activationGate']['blocked'] is None
    assert report['activationGate']['reasonCode'] == 'observation-failed'
    assert 'SECRET_OBSERVER' not in json.dumps(report) and '/private/path' not in json.dumps(report)
    with pytest.raises(ValueError, match='SECRET_OBSERVER'):
        manager.busy()  # Diagnostics did not weaken or replace admission.
    service.runtime.has_pending_operations = lambda: False
    await service.close()
