import asyncio
import copy
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError
from amplifier_web import storage_cleanup as cleanup
from amplifier_web import storage_cleanup_measure as measurement
from amplifier_web import update_retention as retention
from test_update_retention import manager_fixture


@pytest.fixture
def app(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    value = manager.service
    value.data_dir = manager.home
    value.update_manager = manager
    value.db = sqlite3.connect(':memory:')
    value.db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    value.lock = asyncio.Lock()
    value.smart_tools = SimpleNamespace(state={'installations': [], 'servers': []})
    manager.wakeup = asyncio.Event()
    value.names, value.project = names, project
    return value


def settings(app, **changes):
    value = {**cleanup.DEFAULTS, **changes}
    cleanup.store(app, cleanup.POLICY, value)
    return value


def test_policy_plans_age_or_size_without_raising_total_disk_claim():
    candidates = [{'kind':'runner','id':str(i),'fileBytes':100} for i in range(3)]
    policy = {**cleanup.DEFAULTS, 'maxUnusedBytes':150}
    rows = cleanup.plan(candidates, policy, {'runner:0':10,'runner:1':20}, 100)
    assert [row['id'] for row in rows if row['selected']] == ['0','1']
    rows = cleanup.plan(candidates, {**policy,'maxUnusedBytes':0,'keepDays':0}, {}, 100)
    assert all(row['selected'] for row in rows)


async def test_passive_read_does_no_scan_or_retirement(app, monkeypatch):
    monkeypatch.setattr(retention, 'reclaim', AsyncMock(side_effect=AssertionError('scan')))
    value = await cleanup.dispatch(app, 'storage.cleanup.read', {}, 'ui', None)
    assert value['result']['policy']['enabled']
    assert not app.db.execute('SELECT * FROM commands').fetchall()


async def test_save_is_global_cas_and_reuses_exact_acknowledgement(app):
    args = {'expectedRevision':0, 'enabled':False, 'keepDays':2, 'maxUnusedBytes':123}
    first = await cleanup.dispatch(app, 'storage.cleanup.save', args, 'ui', 'original-save')
    repeated = await cleanup.dispatch(app, 'storage.cleanup.save', args, 'ui', 'original-save')
    assert repeated['duplicate'] and repeated['result'] == first['result']
    assert cleanup.policy(app)['revision'] == 1 and app.update_manager.wakeup.is_set()
    with pytest.raises(AppError, match='changed'):
        await cleanup.dispatch(app, 'storage.cleanup.save', args, 'ui', 'later-stale-save')
    with pytest.raises(AppError, match='different contents'):
        await cleanup.dispatch(app, 'storage.cleanup.save', {**args,'enabled':True}, 'ui', 'original-save')
    with pytest.raises(AppError, match='Only the user'):
        await cleanup.dispatch(app, 'storage.cleanup.save', {**args,'expectedRevision':1}, 'agent', 'agent-save')


async def test_preview_measures_shared_runtime_once_and_preserves_all_files(app):
    settings(app, keepDays=0)
    (app.project/'large.dat').write_bytes(b'work'*100)
    original = sorted(str(p) for p in app.data_dir.rglob('*'))
    saved = app.db.execute('SELECT * FROM commands').fetchall()
    value = await cleanup.preview(app)
    assert len(value['rows']) == 1 and value['rows'][0]['id'] == app.names[3]
    # Shared runtime cannot be removed with this unreferenced release.
    assert value['rows'][0]['fileBytes'] < 400
    assert value['protectedFileBytes'] >= 400
    assert sorted(str(p) for p in app.data_dir.rglob('*')) == original
    assert app.db.execute('SELECT * FROM commands').fetchall() == saved


async def test_automatic_cleanup_honors_disable_and_retention_then_keeps_receipt(app):
    settings(app, enabled=False, keepDays=0)
    assert (await cleanup.run(app, 'disabled', automatic=True))['status'] == 'disabled'
    target = app.update_manager.directory/'releases'/app.names[3]
    assert target.exists()
    settings(app, keepDays=7)
    result = await cleanup.run(app, 'retain', automatic=True)
    assert result['status'] == 'completed' and not result['effects'] and target.exists()
    settings(app, keepDays=0)
    result = await cleanup.run(app, 'remove', automatic=True)
    assert result['status'] == 'completed' and result['removedFileBytes'] > 0
    assert result['effects'][0]['status'] == 'completed' and not target.exists()
    assert app.project.exists()
    assert await cleanup.run(app, 'remove', automatic=True) == result
    assert cleanup.read(app)['lastCleanup'] == result


async def test_reference_appearing_after_preview_wins(app, monkeypatch):
    settings(app, keepDays=0)
    original = cleanup.preview
    async def race(value):
        report = await original(value)
        value.state['referenced'] += str(value.update_manager.directory/'releases'/value.names[3])
        return report
    monkeypatch.setattr(cleanup, 'preview', race)
    result = await cleanup.run(app, 'race', automatic=True)
    assert result['removedFileBytes'] == 0 and not result['effects']
    assert (app.update_manager.directory/'releases'/app.names[3]).exists()


async def test_policy_change_during_measurement_defers_removal(app, monkeypatch):
    settings(app, keepDays=0)
    original = cleanup.preview
    async def race(value):
        report = await original(value)
        settings(value, enabled=False, revision=1)
        return report
    monkeypatch.setattr(cleanup, 'preview', race)
    assert (await cleanup.run(app, 'race', automatic=True))['status'] == 'deferred'
    assert (app.update_manager.directory/'releases'/app.names[3]).exists()


async def test_changed_files_after_measurement_are_quarantined_not_deleted(app, monkeypatch):
    settings(app, keepDays=0)
    original = retention.retire
    def race(manager, folder, project, **kwargs):
        (folder/'new-user-work.txt').write_text('preserve this')
        return original(manager, folder, project, **kwargs)
    monkeypatch.setattr(retention, 'retire', race)
    result = await cleanup.run(app, 'changed-files', automatic=True)
    assert result['status'] == 'needs-inspection' and result['removedFileBytes'] == 0
    retained = list((app.update_manager.directory/'retired').glob('*/payload/release/new-user-work.txt'))
    assert len(retained) == 1 and retained[0].read_text() == 'preserve this'
    # A later pass cannot replay a partial quarantine.
    await cleanup.run(app, 'later-pass', automatic=True)
    assert retained[0].exists()


async def test_cancelled_attempt_keeps_unknown_receipt_and_same_request_does_not_replay(app, monkeypatch):
    settings(app, keepDays=0)
    original = retention.reclaim
    async def cancel(manager, **kwargs):
        if not kwargs.get('inspect_only'):
            kwargs['evidence']('attempting', {'kind':'ecosystem','id':app.names[3]})
            raise asyncio.CancelledError
        return await original(manager, **kwargs)
    monkeypatch.setattr(retention, 'reclaim', cancel)
    with pytest.raises(asyncio.CancelledError):
        await cleanup.run(app, 'cancelled', expected_revision=0)
    result = await cleanup.run(app, 'cancelled', expected_revision=0)
    assert result['status'] == 'interrupted'
    with pytest.raises(ValueError, match='different contents'):
        await cleanup.run(app, 'cancelled', expected_revision=1)


def test_measurement_rejects_redirected_roots_and_does_not_follow_child_links(tmp_path):
    original = tmp_path/'original'; original.mkdir()
    (original/'a').write_bytes(b'owned')
    os.link(original/'a', original/'b')
    external = tmp_path/'external'; external.mkdir(); (external/'work').write_text('keep')
    (original/'link').symlink_to(external, target_is_directory=True)
    result = measurement.measure([original])
    assert result['fileBytes'] == 5 and result['exclusiveFileBytes'] == 0 and result['linksNotFollowed'] == 1
    alias = tmp_path/'alias'; alias.symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError, match='redirected'):
        measurement.measure([alias])


def test_inventory_change_prevents_existing_runner_owner_removal(app):
    from amplifier_web.task_environments import remove_installation
    folder = app.data_dir/'smart-tools/installs'/('a'*24)
    folder.mkdir(parents=True); (folder/'original').write_text('code')
    row = {'id':'a'*24, 'paths':[str(folder)], **measurement.measure([folder])}
    (folder/'user-work').write_text('never remove')
    with pytest.raises(ValueError, match='changed'):
        remove_installation(app.data_dir, 'a'*24, folder, cleanup_inventory=row)
    assert (folder/'user-work').exists()


async def test_retired_task_evidence_and_working_files_are_never_candidates(app):
    folders = [app.data_dir/'workspace-resources/owned/test', app.data_dir/'workspaces/project', app.data_dir/'artifacts']
    for folder in folders:
        folder.mkdir(parents=True); (folder/'saved.txt').write_text('unique result')
    settings(app, keepDays=0, maxUnusedBytes=1)
    await cleanup.run(app, 'pressure', automatic=True)
    assert all((folder/'saved.txt').read_text() == 'unique result' for folder in folders)


async def test_bounded_batches_do_not_reset_unseen_retention_age(app, monkeypatch):
    settings(app, keepDays=7)
    cleanup.store(app, cleanup.OBSERVATION, {'ecosystem:unseen': 1, 'ecosystem:now-protected': 1})
    async def bounded(value):
        return {'rows': [], 'protected': [{'kind':'ecosystem','id':'unseen'}, {'kind':'ecosystem','id':'now-protected'}],
                'observedProtected': ['ecosystem:now-protected'], 'complete':False, 'selectedFileBytes':0}
    monkeypatch.setattr(cleanup, 'preview', bounded)
    await cleanup.run(app, 'batch', automatic=True)
    assert cleanup.record(app, cleanup.OBSERVATION) == {'ecosystem:unseen':1}


async def test_disable_after_final_measurement_prevents_unadmitted_deletion(app, monkeypatch):
    settings(app, keepDays=0)
    original = retention._probe
    reclaim = retention.reclaim
    measured = 0
    removing = False
    async def removal_scope(manager, **kwargs):
        nonlocal removing
        removing = not kwargs.get('inspect_only', False)
        try:
            return await reclaim(manager, **kwargs)
        finally:
            removing = False
    async def boundary(operation, values, **kwargs):
        nonlocal measured
        result = await original(operation, values, **kwargs)
        if (operation == 'measure' and removing and app.runtime_lifecycle_lock.locked()
                and any(app.names[3] in path for path in values['paths'])):
            measured += 1
            settings(app, enabled=False, revision=1)
        return result
    monkeypatch.setattr(retention, 'reclaim', removal_scope)
    monkeypatch.setattr(retention, '_probe', boundary)
    result = await cleanup.run(app, 'late-disable', automatic=True)
    assert measured == 1 and not result['effects']
    assert (app.update_manager.directory/'releases'/app.names[3]).exists()


async def test_rollback_of_failed_settings_receipt_keeps_prior_policy(app):
    app.db.execute("CREATE TRIGGER reject_test_save BEFORE INSERT ON commands WHEN NEW.id='failed-save' BEGIN SELECT RAISE(FAIL, 'disk failure'); END")
    args={'expectedRevision':0,'enabled':False,'keepDays':0,'maxUnusedBytes':0}
    with pytest.raises(sqlite3.IntegrityError, match='disk failure'):
        await cleanup.dispatch(app,'storage.cleanup.save',args,'ui','failed-save')
    assert cleanup.policy(app) == cleanup.DEFAULTS


async def test_runtime_reference_arriving_during_measurement_prevents_retirement(app, monkeypatch):
    settings(app, keepDays=0)
    original=retention._probe
    async def measure_boundary(operation, values, **kwargs):
        result=await original(operation,values,**kwargs)
        if operation=='measure' and any(app.names[3] in path for path in values['paths']):
            app.state['referenced'] += str(app.project)
        return result
    monkeypatch.setattr(retention,'_probe',measure_boundary)
    await retention.reclaim(app.update_manager,selected={app.names[3]})
    assert (app.update_manager.directory/'releases'/app.names[3]).exists() and app.project.exists()


async def test_selected_policy_does_not_sweep_unselected_detached_payloads(app):
    folder=app.update_manager.directory/'releases'/app.names[3]
    entry=retention.retire(app.update_manager,folder,app.project,remove_project=False)
    original=(entry/'payload/release/validated.json').read_bytes()
    result=await retention.reclaim(app.update_manager,selected=set())
    assert result['removed']==0 and (entry/'payload/release/validated.json').read_bytes()==original


async def test_size_target_accumulates_across_real_bounded_passes(app, monkeypatch):
    monkeypatch.setattr(retention, 'MAX_CANDIDATES', 1)
    directory = app.update_manager.directory / 'releases'
    source = directory / app.names[3]
    extras = ['5' * 32, '6' * 32]
    import shutil
    for identity in extras:
        shutil.copytree(source, directory / identity)
    size = measurement.measure([str(source)])['fileBytes']
    settings(app, keepDays=7, maxUnusedBytes=2 * size + 1)
    for number in range(2):
        result = await cleanup.run(app, 'bounded-' + str(number), automatic=True)
        assert result['removedFileBytes'] == 0
    saved = cleanup.record(app, cleanup.OBSERVATION)
    assert len(saved) == 2
    # The policy receipt resumes the bounded inspection after a host restart.
    del app.update_manager._retention_cursor
    result = await cleanup.run(app, 'over-target', automatic=True)
    assert result['removedFileBytes'] == size
    assert [row['id'] for row in result['effects']] == [extras[1]]
    assert source.exists() and (directory / extras[0]).exists()
    assert not (directory / extras[1]).exists()
    saved = cleanup.record(app, cleanup.OBSERVATION)
    assert 'ecosystem:' + extras[1] not in saved
    assert cleanup.observed_bytes(saved) == 2 * size
    assert await cleanup.run(app, 'over-target', automatic=True) == result


async def test_known_absent_and_newly_protected_copies_do_not_create_pressure(app):
    settings(app, keepDays=7, maxUnusedBytes=1024)
    cleanup.store(app, cleanup.OBSERVATION, {
        'ecosystem:' + app.names[0]: {'unusedSince':1, 'fileBytes':10000, 'measuredAt':1},
        'ecosystem:' + '0' * 32: {'unusedSince':1, 'fileBytes':10000, 'measuredAt':1}})
    report = await cleanup.dispatch(app, 'storage.cleanup.preview', {}, 'ui', None)
    value = report['result']
    assert '_observations' not in value
    assert value['observedUnusedFileBytes'] == value['unusedFileBytes']
    assert value['selectedFileBytes'] == 0
    assert 'not a complete inventory' in value['observationNote']
    await cleanup.run(app, 'prune-observations', automatic=True)
    saved = cleanup.record(app, cleanup.OBSERVATION)
    assert set(saved) == {'ecosystem:' + app.names[3]}


def test_legacy_age_and_cross_owner_measurements_remain_distinct():
    now = 1000000
    current = [{'kind':'ecosystem', 'id':'fresh', 'fileBytes':100}]
    legacy = {'ecosystem:fresh':now-8*86400, 'runner:legacy':1}
    assert cleanup.plan(current, cleanup.DEFAULTS, legacy, now)[0]['selected']
    assert cleanup.observed_bytes(legacy) == 0
    seen = {'runner:old':{'unusedSince':now, 'fileBytes':200, 'measuredAt':now-10},
            'test-dependencies:old':{'unusedSince':now, 'fileBytes':200, 'measuredAt':now-20}}
    rows = cleanup.plan(current, {**cleanup.DEFAULTS,'maxUnusedBytes':450}, seen, now)
    assert rows[0]['selected']
    assert cleanup.observed_bytes(seen) == 400


async def test_pressure_does_not_override_a_new_reference_at_removal(app, monkeypatch):
    settings(app, keepDays=7, maxUnusedBytes=1)
    original = cleanup.preview
    async def race(value):
        report = await original(value)
        assert report['selectedFileBytes'] > 0
        value.state['referenced'] += str(value.update_manager.directory/'releases'/value.names[3])
        return report
    monkeypatch.setattr(cleanup, 'preview', race)
    result = await cleanup.run(app, 'pressure-race', automatic=True)
    assert result['removedFileBytes'] == 0 and not result['effects']
    assert (app.update_manager.directory/'releases'/app.names[3]).exists()


async def test_uncertain_receipt_stops_contributing_pressure_and_stays_held(app):
    settings(app, keepDays=7, maxUnusedBytes=1024)
    key = 'ecosystem:' + app.names[3]
    cleanup.store(app, cleanup.OBSERVATION, {key: {'unusedSince':1, 'fileBytes':10000,'measuredAt':1}})
    cleanup.store(app, 'storage-cleanup-run:uncertain', {
        'status':'needs-inspection', 'effects':[{'kind':'ecosystem','id':app.names[3],'status':'attempting'}]})
    report = await cleanup.preview(app)
    assert not report['rows'] and report['observedUnusedFileBytes'] == 0
    result = await cleanup.run(app, 'observe-uncertain', automatic=True)
    assert result['removedFileBytes'] == 0
    assert (app.update_manager.directory/'releases'/app.names[3]).exists()
    assert cleanup.record(app, 'storage-cleanup-run:uncertain')['status'] == 'needs-inspection'


async def test_retention_reason_survives_policy_receipt_without_exception_text(app, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise ValueError('Private source details must never escape in the receipt')
    monkeypatch.setattr(retention, 'verify_generation', unavailable)
    preview = await cleanup.preview(app)
    expected = {app.names[3]: 'unverified_generation'}
    assert preview['retentionReasons'] == expected
    assert next(row for row in preview['protected'] if row.get('id') == app.names[3])['reasonCode'] == 'unverified_generation'
    result = await cleanup.run(app, 'unverified', automatic=True)
    assert result['retentionReasons'] == expected
    assert cleanup.read(app)['lastCleanup']['retentionReasons'] == expected
    assert 'Private source' not in json.dumps(result)
    assert result['effects'] == []


async def test_workspace_retention_is_opt_in_validated_and_preserved_by_old_clients(app):
    assert cleanup.policy(app)['workspaceArchiveKeepDays'] is None
    base = {'expectedRevision': 0, 'enabled': True, 'keepDays': 7, 'maxUnusedBytes': 0}
    for value in (0, -1, True, 3651, '7'):
        with pytest.raises(AppError):
            await cleanup.dispatch(app, 'storage.cleanup.save', {**base, 'workspaceArchiveKeepDays': value}, 'ui', 'bad-'+str(value))
    await cleanup.dispatch(app, 'storage.cleanup.save', {**base, 'workspaceArchiveKeepDays': 7}, 'ui', 'opt-in')
    await cleanup.dispatch(app, 'storage.cleanup.save', {**base, 'expectedRevision': 1}, 'ui', 'old-client')
    assert cleanup.policy(app)['workspaceArchiveKeepDays'] == 7
    await cleanup.dispatch(app, 'storage.cleanup.save', {**base, 'expectedRevision': 2, 'workspaceArchiveKeepDays': None}, 'ui', 'disable')
    assert cleanup.policy(app)['workspaceArchiveKeepDays'] is None
