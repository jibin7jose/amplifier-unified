import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from amplifier_web import storage_cleanup as cleanup, update_retention as retention
from test_storage_cleanup import app, settings


def orphan(app, name='q-' + 'b' * 32):
    project = app.data_dir / 'runtime' / name
    root = project / '.venv/lib/python3.13/site-packages'
    dist = root / 'example-1.0.dist-info'
    dist.mkdir(parents=True)
    (project / 'pyproject.toml').write_text('[project]\nname = "amplifier-web-runtime"\n')
    (project / 'uv.lock').write_text('version = 1\n')
    (project / 'qualification.json').write_text('{"retained": true}')
    (project / 'source.py').write_text('# saved source outside generated environment\n')
    (project / '.venv/pyvenv.cfg').write_text('uv = 0.9.0\n')
    (project / '.venv/.lock').touch()
    payload = b'print("installed code")\n'
    (root / 'example.py').write_bytes(payload)
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')
    (dist / 'RECORD').write_text(f'example.py,sha256={encoded},{len(payload)}\nexample-1.0.dist-info/RECORD,,\n')
    return project


@pytest.mark.parametrize('name', ['q-' + 'b' * 32, 'abcd' * 4, 'prepare-' + 'c' * 32])
async def test_policy_reclaims_orphan_venv_preserving_source_and_receipts(app, name):
    project = orphan(app, name)
    settings(app, keepDays=0)
    metadata = {p.name: p.read_bytes() for p in project.iterdir() if p.is_file()}
    result = await cleanup.run(app, 'remove-orphan')
    assert result['status'] == 'completed'
    assert not (project / '.venv').exists()
    assert {p.name: p.read_bytes() for p in project.iterdir() if p.is_file()} == metadata
    effect = next(r for r in result['effects'] if r['kind'] == 'runtime-environment')
    assert effect['id'] == name and effect['status'] == 'completed'
    assert await cleanup.run(app, 'remove-orphan') == result


async def test_existing_age_and_disabled_policy_apply_to_orphans(app):
    project = orphan(app)
    settings(app, enabled=False, keepDays=0)
    assert (await cleanup.run(app, 'disabled-orphan', automatic=True))['status'] == 'disabled'
    settings(app, keepDays=7, maxUnusedBytes=0)
    result = await cleanup.run(app, 'young-orphan', automatic=True)
    assert not any(r['kind'] == 'runtime-environment' for r in result['effects'])
    assert (project / '.venv').exists()
    seen = cleanup.record(app, cleanup.OBSERVATION)
    assert 'runtime-environment:' + project.name in seen


@pytest.mark.parametrize('protection', ['release', 'current', 'rollback', 'lease', 'config', 'source-graph', 'saved-module'])
async def test_references_keep_orphan_payloads(app, protection):
    from amplifier_web.runtime_environment import project_path
    from amplifier_web.generation_leases import acquire
    generation = 'e' * 32
    if protection in {'current', 'rollback', 'lease'}:
        project = orphan(app, project_path(app.data_dir, generation).name)
        if protection == 'lease':
            acquire(app.data_dir, generation, os.getpid())
        else:
            (app.update_manager.directory / 'active.json').write_text(json.dumps({
                'current' if protection == 'current' else 'previous': generation}))
    else:
        project = orphan(app)
    if protection == 'release':
        receipt = app.update_manager.directory / 'releases' / generation
        receipt.mkdir()
        (receipt / 'runtime-project.json').write_text(json.dumps({'project': project.name}))
    elif protection == 'config':
        config = app.data_dir / 'config/settings.yaml'
        config.parent.mkdir(parents=True)
        config.write_text('source: ' + str(project / '.venv/lib/python3.13/site-packages/example.py'))
    elif protection == 'source-graph':
        receipt = app.update_manager.directory / 'releases' / app.names[0] / 'runtime-installed.json'
        receipt.write_text(json.dumps([{'path': str(project / '.venv/lib/python3.13/site-packages')}]))
    elif protection == 'saved-module':
        config = app.data_dir / 'sessions/idle-archived/configuration.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'module': str(project / '.venv/lib/python3.13/site-packages')}))
    settings(app, keepDays=0)
    result = await cleanup.run(app, 'protected-orphan')
    assert (project / '.venv').is_dir()
    assert not any(r['kind'] == 'runtime-environment' for r in result['effects'])


@pytest.mark.parametrize('change', ['modified', 'untracked', 'checkout', 'redirected', 'script'])
async def test_changed_or_unowned_source_is_retained(app, change):
    from amplifier_web import runtime_environment_retention as environments
    project = orphan(app)
    root = project / '.venv/lib/python3.13/site-packages'
    if change == 'modified':
        (root / 'example.py').write_text('my valuable edit')
    elif change == 'untracked':
        (root / 'my_module.py').write_text('saved source')
    elif change == 'checkout':
        (project / '.venv/src/.git').mkdir(parents=True)
    elif change == 'script':
        (project / '.venv/bin').mkdir()
        (project / '.venv/bin/my_script.py').write_text('saved source')
    else:
        (project / 'uv.lock').unlink()
        elsewhere = app.data_dir / 'other.lock'
        elsewhere.write_text('version = 1')
        (project / 'uv.lock').symlink_to(elsewhere)
    report = await environments.reclaim(app.update_manager, inspect_only=True)
    assert not report['candidates'] and project.name in report['protectedIds']
    assert (project / '.venv').is_dir()


def test_modified_owned_console_script_is_retained(app):
    from amplifier_web import runtime_environment_retention as environments
    project = orphan(app)
    script = project / '.venv/bin/example'
    script.parent.mkdir()
    payload = b'#!/usr/bin/env python\n'
    script.write_bytes(payload)
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')
    record = project / '.venv/lib/python3.13/site-packages/example-1.0.dist-info/RECORD'
    record.write_text(record.read_text() + f'../../../bin/example,sha256={encoded},{len(payload)}\n')
    assert environments.inspect_payload(project)['fileBytes'] > 0
    script.write_text('my changed entrypoint')
    with pytest.raises(ValueError, match='changed'):
        environments.inspect_payload(project)


def test_reference_snapshot_ignores_history_writes_but_tracks_new_settings(app):
    from amplifier_web import runtime_environment_retention as environments
    from amplifier_web.session_files import amplifier_home
    native = amplifier_home() / 'projects/example/sessions/old'
    native.mkdir(parents=True)
    (native / 'events.jsonl').write_text('old events')
    snapshot = environments.reference_snapshot(app.data_dir, [])
    # Ordinary history/artifact activity is not a source-reference change.
    (native / 'events.jsonl').write_text('new events')
    (native / 'new-history-segment').mkdir()
    assert environments.references_unchanged(snapshot)
    # A settings file appearing in an already-known session is a real change.
    (native / 'settings.yaml').write_text('module: ./somewhere')
    assert not environments.references_unchanged(snapshot)


def test_source_receipts_larger_than_old_snapshot_limit_remain_covered(app):
    from amplifier_web import runtime_environment_retention as environments
    project = orphan(app)
    receipt = app.update_manager.directory / 'releases' / app.names[0] / 'runtime-installed.json'
    receipt.write_text(json.dumps({'proof': 'x' * (33 * 1024 * 1024),
                                  'source': str(project / '.venv')}))
    snapshot = environments.reference_snapshot(app.data_dir, [])
    assert project.name in snapshot['referenced']
    assert environments.references_unchanged(snapshot)


async def test_empty_selection_does_not_repeat_reference_inventory(app, monkeypatch):
    from amplifier_web import runtime_environment_retention as environments
    orphan(app)
    async def forbidden(*args, **kwargs):
        pytest.fail('No orphan selected; no second reference inventory needed')
    monkeypatch.setattr(retention, '_probe', forbidden)
    assert not (await environments.reclaim(app.update_manager, selected=set()))['removed']


@pytest.mark.parametrize('boundary', ['references', 'measurement'])
async def test_reference_returning_during_inspection_prevents_detach(app, monkeypatch, boundary):
    from amplifier_web import runtime_environment_retention as environments
    project = orphan(app)
    original = retention._probe
    async def probe(operation, values, **kwargs):
        result = await original(operation, values, **kwargs)
        if operation == ('runtime-payload' if boundary == 'references' else 'runtime-identity'):
            config = app.data_dir / 'sessions/appeared/configuration.json'
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps({'source': str(project)}))
        return result
    monkeypatch.setattr(retention, '_probe', probe)
    await environments.reclaim(app.update_manager, selected={project.name})
    assert (project / '.venv').is_dir()
    assert not list((app.update_manager.directory / 'retired').glob('*'))


async def test_installer_and_qualifier_locks_retain_environment(app):
    from filelock import FileLock
    from amplifier_web import runtime_environment_retention as environments
    project = orphan(app)
    with environments.install_lock(project):
        assert not (await environments.reclaim(app.update_manager, selected={project.name}))['removed']
    with FileLock(project.with_name('.' + project.name + '.lock')):
        assert not (await environments.reclaim(app.update_manager, selected={project.name}))['removed']
    assert (project / '.venv').exists()


async def test_cancelled_detached_removal_preserves_receipt_and_resumes_same_payload(app, monkeypatch):
    from amplifier_web import runtime_environment_retention as environments
    project = orphan(app)
    entered = asyncio.Event()
    remove = retention.remove_retired
    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(retention, 'remove_retired', wait)
    task = asyncio.create_task(environments.reclaim(app.update_manager, selected={project.name}))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (project / '.venv').exists()
    entries = list((app.update_manager.directory / 'retired').iterdir())
    assert len(entries) == 1
    saved = json.loads((entries[0] / 'retirement.json').read_text())
    assert saved['kind'] == 'runtime-environment' and saved['phase'] == 'retired'
    # A subsequent use gets a different installation; retry only the detached
    # payload, never the re-created live path.
    replacement = project / '.venv'
    replacement.mkdir()
    (replacement / 'new-work').write_text('keep')
    await remove(app.update_manager, entries[0], timeout=5)
    assert (replacement / 'new-work').read_text() == 'keep'
    assert (project / 'source.py').exists() and not entries[0].exists()


async def test_automatic_policy_reconciles_interrupted_inventory_without_touching_new_venv(app, monkeypatch):
    project = orphan(app)
    settings(app, keepDays=0)
    entered = asyncio.Event()
    finish = retention.finish_runtime_retirement
    async def interrupted(*args, **kwargs):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(retention, 'finish_runtime_retirement', interrupted)
    task = asyncio.create_task(cleanup.run(app, 'cancelled-inventory', automatic=True))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    entries = list((app.update_manager.directory / 'retired').iterdir())
    # The normal generation candidate may have been retired already.
    entry = next(p for p in entries if json.loads((p / 'retirement.json').read_text())['kind'] == 'runtime-environment')
    assert json.loads((entry / 'retirement.json').read_text())['phase'] == 'retiring'
    prior = cleanup.record(app, 'storage-cleanup-run:cancelled-inventory')
    effect = next(r for r in prior['effects'] if r['kind'] == 'runtime-environment')
    assert prior['status'] == 'interrupted' and effect['status'] == 'attempting'
    assert ('runtime-environment', project.name) in cleanup.unresolved(app)
    replacement = project / '.venv'
    replacement.mkdir()
    (replacement / 'new-work').write_text('new installation must survive')
    monkeypatch.setattr(retention, 'finish_runtime_retirement', finish)
    result = await cleanup.run(app, 'later-automatic-pass', automatic=True)
    assert result['status'] == 'completed' and not entry.exists()
    assert (replacement / 'new-work').read_text() == 'new installation must survive'
    recovered = cleanup.record(app, 'storage-cleanup-run:cancelled-inventory')
    old = next(r for r in recovered['effects'] if r['kind'] == 'runtime-environment')
    assert recovered['status'] == 'interrupted' and old['status'] == 'attempting'
    assert old['stamp'] == effect['stamp'] and old['recovery']['disposition'] == 'removed'
    assert not cleanup.unresolved(app)
    assert result['recoveries'][0]['retirementId'] == entry.name


async def test_interrupted_detach_with_changed_payload_keeps_unknown_evidence(app, monkeypatch):
    project = orphan(app)
    settings(app, keepDays=0)
    finish = retention.finish_runtime_retirement
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(retention, 'finish_runtime_retirement', interrupted)
    with pytest.raises(asyncio.CancelledError):
        await cleanup.run(app, 'unknown-detach', automatic=True)
    entry = next((app.update_manager.directory / 'retired').iterdir())
    saved = (entry / 'retirement.json').read_bytes()
    (entry / 'payload/runtime/local-work').write_text('must retain')
    monkeypatch.setattr(retention, 'finish_runtime_retirement', finish)
    result = await cleanup.run(app, 'retry-unknown-detach', automatic=True)
    assert (entry / 'payload/runtime/local-work').read_text() == 'must retain'
    assert (entry / 'retirement.json').read_bytes() == saved
    assert ('runtime-environment', project.name) in cleanup.unresolved(app)
    assert not result.get('recoveries')


async def test_pre_rename_failure_settles_empty_receipt_without_replaying_original(app, monkeypatch):
    project = orphan(app)
    settings(app, keepDays=0)
    rename = Path.rename
    def interrupted(path, target):
        if path == project / '.venv':
            raise OSError('rename interrupted')
        return rename(path, target)
    monkeypatch.setattr(Path, 'rename', interrupted)
    first = await cleanup.run(app, 'pre-rename-failure', automatic=True)
    assert first['status'] == 'needs-inspection' and (project / '.venv').exists()
    entry = next((app.update_manager.directory / 'retired').iterdir())
    assert not list((entry / 'payload').iterdir())
    # The existing unresolved attempt keeps this live path outside the new
    # selection. Normal maintenance settles only the exact empty retirement.
    second = await cleanup.run(app, 'settle-empty-retirement', automatic=True)
    assert not entry.exists() and (project / '.venv').exists()
    assert second['recoveries'][0]['disposition'] == 'not-detached'
    saved = cleanup.record(app, 'storage-cleanup-run:pre-rename-failure')
    old = next(r for r in saved['effects'] if r['kind'] == 'runtime-environment')
    assert old['status'] == 'attempting' and old['recovery']['disposition'] == 'not-detached'
    assert not cleanup.unresolved(app)


async def test_empty_retirement_receipt_callback_failure_can_retry(app):
    from amplifier_web import runtime_environment_retention as environments
    project = orphan(app)
    measured = environments.payload_identity(project)
    entry = retention.retire_runtime(app.update_manager, project, measured)
    (entry / 'payload/runtime').rename(project / '.venv')
    def unavailable(_):
        raise OSError('receipt store unavailable')
    with pytest.raises(OSError, match='receipt store'):
        await retention.remove_retired(app.update_manager, entry, timeout=5, resolved=unavailable)
    assert not (entry / 'payload').exists()
    resolved = []
    await retention.remove_retired(app.update_manager, entry, timeout=5, resolved=resolved.append)
    assert resolved[0]['disposition'] == 'not-detached'
    assert not entry.exists() and (project / '.venv').exists()


async def test_completed_evidence_failure_recovers_on_later_policy_pass(app, monkeypatch):
    project = orphan(app)
    settings(app, keepDays=0)
    store = cleanup.store
    def unavailable(app, key, result):
        if any(row.get('kind') == 'runtime-environment' and row.get('status') == 'completed'
               for row in result.get('effects', [])):
            raise OSError('completion receipt store unavailable')
        return store(app, key, result)
    monkeypatch.setattr(cleanup, 'store', unavailable)
    with pytest.raises(OSError, match='completion receipt'):
        await cleanup.run(app, 'failed-completion-receipt', automatic=True)
    assert not (project / '.venv').exists()
    prior = cleanup.record(app, 'storage-cleanup-run:failed-completion-receipt')
    effect = next(row for row in prior['effects'] if row['kind'] == 'runtime-environment')
    assert effect['status'] == 'attempting'
    entries = list((app.update_manager.directory / 'retired').iterdir())
    assert len(entries) == 1 and (entries[0] / 'retirement.json').is_file()
    assert not (entries[0] / 'payload').exists()
    replacement = project / '.venv'
    replacement.mkdir()
    (replacement / 'new-work').write_text('new installation must survive')
    monkeypatch.setattr(cleanup, 'store', store)
    result = await cleanup.run(app, 'recover-completion-receipt', automatic=True)
    assert result['status'] == 'completed' and not entries[0].exists()
    assert (replacement / 'new-work').read_text() == 'new installation must survive'
    recovered = cleanup.record(app, 'storage-cleanup-run:failed-completion-receipt')
    old = next(row for row in recovered['effects'] if row['kind'] == 'runtime-environment')
    assert old['status'] == 'attempting' and old['recovery']['disposition'] == 'removed'
    assert old['stamp'] == effect['stamp'] and not cleanup.unresolved(app)


async def test_bounded_pass_advances_past_modified_orphan(app, monkeypatch):
    from amplifier_web import runtime_environment_retention as environments
    bad = orphan(app, 'a' * 16)
    good = orphan(app, 'b' * 16)
    (bad / '.venv/lib/python3.13/site-packages/example.py').write_text('retain local edit')
    monkeypatch.setattr(retention, 'MAX_CANDIDATES', 1)
    assert not (await environments.reclaim(app.update_manager, inspect_only=True))['candidates']
    report = await environments.reclaim(app.update_manager, inspect_only=True)
    assert [r['id'] for r in report['candidates']] == [good.name]


async def test_orphan_preview_creates_no_files_or_locks(app):
    from amplifier_web import runtime_environment_retention as environments
    orphan(app)
    before = {str(p): p.read_bytes() for p in app.data_dir.rglob('*') if p.is_file()}
    assert (await environments.reclaim(app.update_manager, inspect_only=True))['candidates']
    after = {str(p): p.read_bytes() for p in app.data_dir.rglob('*') if p.is_file()}
    assert before == after


@pytest.mark.parametrize('mutation', ['existing-config', 'empty-session', 'new-alias', 'new-lease', 'new-workspace'])
async def test_reference_change_at_admission_is_retained(app, monkeypatch, mutation):
    from contextlib import asynccontextmanager
    from amplifier_web import runtime_environment_retention as environments
    from amplifier_web.generation_leases import acquire
    project = orphan(app)
    saved = app.data_dir / 'sessions/known/configuration.json'
    saved.parent.mkdir(parents=True)
    if mutation == 'existing-config':
        saved.write_text('{}')
    @asynccontextmanager
    async def lifecycle():
        if mutation in {'empty-session', 'existing-config'}:
            saved.write_text(json.dumps({'source': str(project)}))
        elif mutation == 'new-alias':
            path = app.update_manager.directory / 'releases' / ('f' * 32) / 'runtime-project.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'project': project.name}))
        elif mutation == 'new-lease':
            acquire(app.data_dir, 'f' * 32, os.getpid())
        else:
            app.state['configurationRevision'] = 1
        yield
    app.runtime_lifecycle = lifecycle
    assert not (await environments.reclaim(app.update_manager, selected={project.name}))['removed']
    assert (project / '.venv').exists()


async def test_populated_metadata_does_no_reads_or_probes_under_admission(app, monkeypatch, capsys):
    from contextlib import asynccontextmanager
    import time
    from amplifier_web import runtime_environment_retention as environments
    projects = [orphan(app, letter * 16) for letter in 'cd']
    for index in range(6155):
        path = app.data_dir / 'sessions' / str(index) / 'configuration.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'metadata': 'saved config ' * 150}))
    holding, durations, operations = False, [], []
    original = retention._probe
    async def probe(operation, values, **kwargs):
        assert not holding, 'No subprocess/full inventory while admission is held'
        operations.append(operation)
        return await original(operation, values, **kwargs)
    monkeypatch.setattr(retention, '_probe', probe)
    read = Path.read_text
    def read_text(path, *args, **kwargs):
        assert not holding, 'No configuration/body reads while admission is held'
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_text)
    @asynccontextmanager
    async def lifecycle():
        nonlocal holding
        started = time.perf_counter()
        holding = True
        try:
            yield
        finally:
            holding = False
            durations.append((time.perf_counter() - started) * 1000)
    app.runtime_lifecycle = lifecycle
    result = await environments.reclaim(app.update_manager, selected={p.name for p in projects})
    assert result['removed'] == 2
    assert operations.count('runtime-references') == 1
    assert len(durations) == 2
    # Timings are evidence, not a hardware-dependent test deadline. The stable
    # guard above prevents reintroducing full reads/probes in the critical path.
    with capsys.disabled():
        print('RUNTIME_RETENTION_TIMING=' + json.dumps({'savedConfigurations': 6155, 'admissionMs': durations}))
