"""Saved-operation continuation and qualified loaded-source comparisons."""
import asyncio
import copy
import json

import pytest

from amplifier_web.configuration_readback import matches, source_aliases
from amplifier_web.configuration_recovery import receipt_path
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import AppError
from test_configuration_authority import revoke
from test_queued_configuration_boundary import (
    boundary, ControlsTransport, operation, plan, queue, reach_apply, recovery, settle, scheduler_barrier,
)


def qualified_sources(home, *, reused=False, parent_id='parent'):
    """Qualified receipt shape, including retained older editable source paths."""
    base = home / 'updates/releases'
    parent, derived = base / parent_id, base / 'derived'
    relative = 'cache/bundle-fixture/modules/tool-fixture'
    identity = {'url': 'https://example.invalid/module', 'ref': 'main',
        'revision': 'a' * 40, 'dirty': False, 'trackedContentSha256': 'b' * 64,
        'subdirectory': 'modules/tool-fixture'}
    for root in (parent, derived):
        root.mkdir(parents=True, exist_ok=True)
        (root / 'validated.json').write_text(json.dumps(
            {'parentGeneration': parent_id} if root == derived else {}))
        (root / 'profiles-qualified.json').write_text('{}')
        installed = base / 'retained-older' if reused and root == parent else root
        (root / 'runtime-installed.json').write_text(json.dumps([{
            'name': 'amplifier-module-tool-fixture',
            'path': str(installed / 'foundation' / relative), 'cacheSource': identity}]))
    expected, actual = plan(2), plan(2)
    expected['tools'][0]['source'] = str(parent / 'foundation' / relative)
    actual['tools'][0]['source'] = str(derived / 'foundation' / relative)
    worker = {'runtimeOwnerId': 'mounted-owner', 'generation': parent_id, 'source_generation': 'derived'}
    return expected, actual, worker


@pytest.mark.parametrize('reused', [False, True])
def test_qualified_source_relocation_retains_exact_identity(tmp_path, reused):
    expected, actual, row = qualified_sources(tmp_path, reused=reused)
    assert not matches(expected, actual)
    aliases = source_aliases(tmp_path, row, expected, actual)
    assert aliases == {expected['tools'][0]['source']: actual['tools'][0]['source']}
    assert matches(expected, actual, sources=aliases)
    # The same path inside module configuration is data, not a declaration.
    expected['tools'][0]['config']['source'] = expected['tools'][0]['source']
    actual['tools'][0]['config']['source'] = actual['tools'][0]['source']
    assert not matches(expected, actual, sources=aliases)


@pytest.mark.parametrize('same_parent', [False, True])
def test_second_edit_recognizes_unchanged_sources_in_previous_qualified_generation(tmp_path, same_parent):
    expected, actual, row = qualified_sources(tmp_path, reused=True)
    base = tmp_path / 'updates/releases'
    previous = base / 'previous-profile'
    previous.mkdir()
    (previous / 'profiles-qualified.json').write_text('{}')
    (previous / 'validated.json').write_text(json.dumps({
        'parentGeneration': 'parent' if same_parent else 'unrelated-parent'}))
    (previous / 'runtime-installed.json').write_bytes((base / 'parent/runtime-installed.json').read_bytes())
    expected['tools'][0]['source'] = expected['tools'][0]['source'].replace('/parent/foundation/', '/previous-profile/foundation/')
    aliases = source_aliases(tmp_path, row, expected, actual)
    assert matches(expected, actual, sources=aliases)


def adopted_sources(tmp_path, change=None):
    parent = 'b' * 32
    expected, actual, worker = qualified_sources(tmp_path, reused=True, parent_id=parent)
    base = tmp_path / 'updates/releases'
    prior = base / ('a' * 32)
    prior.mkdir()
    (prior / 'validated.json').write_text('{}')
    (prior / 'profiles-qualified.json').write_text('{}')
    original = json.loads((base / parent / 'runtime-installed.json').read_text())
    # A saved operation can outlive multiple updates, not just active.previous.
    (tmp_path / 'updates/active.json').write_text(json.dumps({
        'current': 'd' * 32 if change == 'inactive' else parent, 'previous': 'c' * 32}))
    expected['tools'][0]['source'] = expected['tools'][0]['source'].replace(
        f'/{parent}/foundation/', f'/{prior.name}/foundation/')
    if change == 'pin':
        original[0]['cacheSource']['ref'] = 'v1.0.0'
    old = copy.deepcopy(original)
    if change == 'old-dirty':
        old[0]['cacheSource']['dirty'] = True
    (prior / 'runtime-installed.json').write_text(json.dumps(old))
    if change == 'old-unqualified':
        (prior / 'profiles-qualified.json').unlink()
    installed = copy.deepcopy(original)
    installed[0]['cacheSource'].update(revision='e' * 40, trackedContentSha256='f' * 64)
    if change in {'ref', 'repository', 'subdirectory'}:
        installed[0]['cacheSource'][{'repository': 'url'}.get(change, change)] = 'different'
    if change == 'package':
        installed[0]['name'] = 'different-package'
    (base / parent / 'runtime-installed.json').write_text(json.dumps(installed))
    if change == 'derived-revision':
        installed[0]['cacheSource']['revision'] = '9' * 40
    if change == 'derived-digest':
        installed[0]['cacheSource']['trackedContentSha256'] = '8' * 64
    (base / 'derived/runtime-installed.json').write_text(json.dumps(installed))
    if change == 'configuration':
        actual['tools'][0]['config']['limit'] = 99
    return expected, actual, worker


@pytest.mark.parametrize('change', [None, 'pin', 'ref', 'repository',
    'subdirectory', 'package', 'derived-revision', 'derived-digest', 'old-unqualified',
    'old-dirty', 'configuration'])
def test_saved_plan_follows_only_already_adopted_mutable_source(tmp_path, change):
    expected, actual, worker = adopted_sources(tmp_path, change)
    assert matches(expected, actual, sources=source_aliases(tmp_path, worker, expected, actual)) is (change is None)


def test_worker_source_readback_survives_later_component_promotion(tmp_path):
    expected, actual, worker = adopted_sources(tmp_path, 'inactive')
    # The exact worker captured its qualified parent before another update
    # became active. Promotion changes new workers, not this mounted graph.
    assert matches(expected, actual, sources=source_aliases(tmp_path, worker, expected, actual))


@pytest.mark.parametrize('change', ['parent', 'missing', 'digest', 'revision', 'repository',
    'subdirectory', 'dirty', 'ambiguous', 'outside', 'different-cache', 'traversal'])
def test_source_relocation_requires_qualified_exact_provenance(tmp_path, change):
    expected, actual, row = qualified_sources(tmp_path)
    receipt = tmp_path / 'updates/releases/derived'
    installed = receipt / 'runtime-installed.json'
    records = json.loads(installed.read_text())
    if change == 'parent':
        (receipt / 'validated.json').write_text('{"parentGeneration":"another"}')
    elif change == 'missing':
        installed.unlink()
    elif change == 'outside':
        records[0]['path'] = '/unowned/foundation/cache/bundle-fixture/modules/tool-fixture'
    elif change == 'different-cache':
        actual['tools'][0]['source'] += '-different'
    elif change == 'traversal':
        expected['tools'][0]['source'] += '/../../modules/tool-fixture'
    elif change == 'ambiguous':
        records.append(copy.deepcopy(records[0]))
    else:
        key = {'digest': 'trackedContentSha256', 'repository': 'url'}.get(change, change)
        records[0]['cacheSource'][key] = True if change == 'dirty' else 'c' * (64 if change == 'digest' else 40)
    if change != 'missing':
        installed.write_text(json.dumps(records))
    assert not source_aliases(tmp_path, row, expected, actual)


@pytest.mark.parametrize('change', ['config', 'module', 'disabled', 'provider-order', 'secret-omitted'])
def test_source_relocation_cannot_hide_changed_loaded_plan(tmp_path, change):
    expected, actual, row = qualified_sources(tmp_path)
    if change == 'config':
        actual['tools'][0]['config']['limit'] = 1
    elif change == 'module':
        actual['tools'][0]['module'] = 'another-tool'
    elif change == 'disabled':
        actual['tools'][0]['enabled'] = False
    elif change == 'secret-omitted':
        expected['tools'][0]['config']['api_key'] = '[REDACTED]'
    else:
        expected['providers'] = [{'module': 'provider-fixture', 'id': 'one'},
                                 {'module': 'provider-fixture', 'id': 'two'}]
        actual['providers'] = list(reversed(expected['providers']))
    aliases = source_aliases(tmp_path, row, expected, actual)
    assert aliases
    assert not matches(expected, actual, sources=aliases)


async def saved_failure(b):
    """The actual native settings/receipt writer completes; readback is lost."""
    control = b.runtime.control
    async def lost(sid, name, args, **kwargs):
        if name == 'configuration.inspect':
            raise RuntimeError('Synthetic lost loaded-plan readback')
        return await control(sid, name, args, **kwargs)
    b.runtime.control = lost
    await queue(b)
    await reach_apply(b)
    await settle(b)
    b.runtime.control = control
    b.runtime.workers = {b.owner['id']: {'runtimeOwnerId': 'same-mounted-owner'}}
    saved = operation(b)
    assert saved['state']['effect'] == 'saved_not_mounted'
    assert saved['state']['phase'] == 'error' and b.owner['configurationBusy']
    return saved


async def finish_resume(b, *, operator=False):
    if operator:
        saved = operation(b)
        receipt = await b.app.dispatch('configuration.operation.resume', {
            'sessionId': b.owner['id'], 'operationId': saved['id'],
            'expectedRevision': saved['revision']}, origin='ui')
    else:
        receipt = await recovery(b, 'resume')
    task = b.app.management.pending_config_tasks.get(b.owner['id'])
    if task is not None:
        await asyncio.wait_for(task, 3)
    return receipt


async def test_saved_resume_observes_same_worker_without_write_start_or_input(boundary):
    b = boundary
    saved = await saved_failure(b)
    original = copy.deepcopy(saved)
    sealed = receipt_path(saved['attempts'][-1]).read_bytes()
    config = override_path(b.owner['id']).read_bytes()
    before = list(b.runtime.calls)
    observed = (await b.app.dispatch('configuration.operation.read',
        {'sessionId': b.owner['id'], 'includeRecovery': True}))['result']
    assert observed['recovery']['canResume'] and 'never repeats' in observed['recovery']['reason']
    assert b.runtime.calls == before
    receipt = await finish_resume(b)
    final = operation(b)
    assert receipt['result']['state']['effect'] == 'saved_not_mounted'
    assert final['id'] == original['id'] and final['request'] == original['request']
    assert final['attempts'] == original['attempts']
    assert final['history'][:len(original['history'])] == original['history']
    assert final['state']['phase'] == 'ready' and final['state']['effect'] == 'mounted'
    assert b.runtime.calls[len(before):] == ['configuration.inspect']
    assert not b.runtime.inputs and not b.owner['configurationBusy']
    assert b.owner['configuration']['plan']['tools'][0]['config']['limit'] == 2
    assert receipt_path(saved['attempts'][-1]).read_bytes() == sealed
    assert override_path(b.owner['id']).read_bytes() == config
    assert not b.app.management.queued_path(b.owner['id']).exists()


async def test_saved_resume_mounts_only_when_worker_absent(boundary):
    b = boundary
    saved = await saved_failure(b)
    b.runtime.workers.clear()
    before = list(b.runtime.calls)
    await finish_resume(b)
    assert b.runtime.calls[len(before):] == ['start', 'configuration.inspect'], operation(b)['state'].get('error')
    assert operation(b)['attempts'] == saved['attempts']
    assert operation(b)['state']['effect'] == 'mounted' and not b.runtime.inputs


@pytest.mark.parametrize('changed', ['saved', 'stop', 'authority', 'native-target'])
async def test_saved_resume_refuses_changed_original_boundary(boundary, changed):
    b = boundary
    saved = await saved_failure(b)
    before = list(b.runtime.calls)
    if changed == 'saved':
        override_path(b.owner['id']).write_text(json.dumps(plan(99)))
    elif changed == 'stop':
        b.owner['interruptionRevision'] = 1
    elif changed == 'native-target':
        b.owner['runtimeSessionId'] = 'another-native'
    else:
        await revoke(b.app, b.authorized)
    with pytest.raises(AppError):
        await recovery(b, 'resume')
    assert b.runtime.calls == before and b.owner['configurationBusy']
    assert operation(b)['attempts'] == saved['attempts']


@pytest.mark.parametrize('changed', ['plan', 'path', 'owner', 'stop', 'authority'])
async def test_saved_resume_keeps_fence_when_readback_or_authority_changes(boundary, changed):
    b = boundary
    saved = await saved_failure(b)
    control = b.runtime.control
    async def changed_read(sid, name, args, **kwargs):
        result = await control(sid, name, args, **kwargs)
        if name == 'configuration.inspect':
            if changed == 'plan':
                result['plan']['tools'][0]['config']['limit'] = 99
            elif changed == 'path':
                result['plan']['tools'][0]['source'] = '/unverified/another-source'
            elif changed == 'owner':
                b.runtime.workers[sid] = {'runtimeOwnerId': 'replacement-owner'}
            elif changed == 'stop':
                b.owner['interruptionRevision'] = 1
            else:
                await revoke(b.app, b.authorized)
        return result
    b.runtime.control = changed_read
    before = list(b.runtime.calls)
    await finish_resume(b)
    assert operation(b)['state']['phase'] == 'error'
    assert operation(b)['state']['effect'] == 'saved_not_mounted'
    assert operation(b)['attempts'] == saved['attempts'] and b.owner['configurationBusy']
    assert b.runtime.calls[len(before):] == ['configuration.inspect']
    assert not b.runtime.inputs


@pytest.mark.parametrize('adopted, saved_readback, promoted', [
    (False, False, False), (True, False, False), (True, True, False),
    (True, False, True), (True, True, True)])
async def test_apply_and_resume_accept_qualified_source_resolution(boundary, adopted, saved_readback, promoted):
    b = boundary
    expected, loaded, row = (adopted_sources(b.app.data_dir) if adopted
                            else qualified_sources(b.app.data_dir, reused=True))
    b.runtime.workers = {b.owner['id']: row}
    control = b.runtime.control
    lose_readback = saved_readback
    async def relocated(sid, name, args, **kwargs):
        result = await control(sid, name, args, **kwargs)
        if name == 'configuration.inspect':
            if lose_readback:
                raise RuntimeError('Synthetic lost loaded-plan readback')
            result['plan']['tools'][0]['source'] = loaded['tools'][0]['source']
            if promoted:
                (b.app.data_dir / 'updates/active.json').write_text(json.dumps({
                    'current': 'd' * 32, 'previous': row['generation']}))
        return result
    b.runtime.control = relocated
    b.owner['status'] = 'working'
    await b.app.management.perform('configuration.apply',
        {'id': b.owner['id'], 'config': expected, 'whenIdle': True})
    b.queued_task = b.app.management.pending_config_tasks[b.owner['id']]
    await reach_apply(b)
    await settle(b)
    if saved_readback:
        saved = operation(b)
        assert saved['state']['effect'] == 'saved_not_mounted', saved['state']
        config = override_path(b.owner['id']).read_bytes()
        sealed = receipt_path(saved['attempts'][-1]).read_bytes()
        before = list(b.runtime.calls)
        lose_readback = False
        await finish_resume(b, operator=True)
        assert operation(b)['attempts'] == saved['attempts']
        assert b.runtime.calls[len(before):] == ['configuration.inspect']
        assert override_path(b.owner['id']).read_bytes() == config
        assert receipt_path(saved['attempts'][-1]).read_bytes() == sealed
        assert not b.runtime.inputs
    assert operation(b)['state']['effect'] == 'mounted'
    assert not b.owner['configurationBusy']
    assert b.runtime.calls.count('configuration.apply') == 1


async def test_inspect_held_worker_reads_current_plan_without_resolving_operation(boundary):
    b = boundary
    saved = await saved_failure(b)
    sealed = receipt_path(saved['attempts'][-1]).read_bytes()
    config = override_path(b.owner['id']).read_bytes()
    before = list(b.runtime.calls)
    # Read-only diagnostics must expose a real mismatch, not just the old
    # saved projection and not require a successful recovery first.
    b.runtime.coordinator.config['tools'][0]['config']['limit'] = 99
    await b.app.management.perform('configuration.inspect', {'id': b.owner['id']})
    assert b.owner['configuration']['plan']['tools'][0]['config']['limit'] == 99
    assert operation(b) == saved and b.owner['configurationBusy']
    assert b.runtime.calls[len(before):] == ['configuration.inspect']
    assert receipt_path(saved['attempts'][-1]).read_bytes() == sealed
    assert override_path(b.owner['id']).read_bytes() == config
    assert not b.runtime.inputs


@pytest.mark.parametrize('changed', ['absent', 'owner', 'stop'])
async def test_held_inspection_cannot_start_or_publish_from_changed_worker(boundary, changed):
    b = boundary
    saved = await saved_failure(b)
    before = list(b.runtime.calls)
    original = copy.deepcopy(b.owner.get('configuration'))
    control = b.runtime.control
    async def change_owner(sid, name, args, **kwargs):
        result = await control(sid, name, args, **kwargs)
        if changed == 'owner':
            b.runtime.workers[sid] = {'runtimeOwnerId': 'replacement'}
        elif changed == 'stop':
            b.owner['interruptionRevision'] = b.owner.get('interruptionRevision', 0) + 1
        return result
    b.runtime.control = change_owner
    if changed == 'absent':
        b.runtime.workers.clear()
    with pytest.raises(ValueError):
        await b.app.management.perform('configuration.inspect', {'id': b.owner['id']})
    assert b.owner.get('configuration') == original
    assert operation(b) == saved and b.owner['configurationBusy']
    assert b.runtime.calls[len(before):] == ([] if changed == 'absent' else ['configuration.inspect'])
    assert not b.runtime.inputs


async def test_saved_readback_rechecks_worker_after_waiting_for_commit_lock(boundary):
    b = boundary
    await saved_failure(b)
    entered, release = asyncio.Event(), asyncio.Event()
    control = b.runtime.control
    async def held(sid, name, args, **kwargs):
        result = await control(sid, name, args, **kwargs)
        entered.set()
        await release.wait()
        return result
    b.runtime.control = held
    await recovery(b, 'resume')
    task = b.app.management.pending_config_tasks[b.owner['id']]
    await asyncio.wait_for(entered.wait(), 3)
    async with b.app.lock:
        release.set()
        await scheduler_barrier()
        b.runtime.workers[b.owner['id']] = {'runtimeOwnerId': 'new-owner-before-commit'}
    await asyncio.wait_for(task, 3)
    assert operation(b)['state']['effect'] == 'saved_not_mounted' and b.owner['configurationBusy']
    assert 'owner changed' in operation(b)['state']['error']


async def test_queued_saved_readback_reload_retains_fence_until_explicit_resume(boundary, monkeypatch):
    from amplifier_web.management import Management
    from amplifier_web.service import AppService
    b = boundary
    saved = await saved_failure(b)
    monkeypatch.setattr(b.app.management, 'schedule_configuration', lambda identity: None)
    await recovery(b, 'resume')
    queued = operation(b)
    assert queued['state']['phase'] == 'queued' and queued['state']['stage'] == 'readback_queued'
    b.app._publish()
    await b.app.close()
    fresh = ControlsTransport(b.app.data_dir, b.owner['id'])
    fresh.block_start, fresh.needs_mount = False, True
    app = AppService(b.app.data_dir, fresh, workspace=b.app.default_workspace)
    fresh.app = app
    try:
        app.management = Management(app)
        current = app._session(b.owner['id'])
        restored = app.management.configuration_record(current['id'], saved['id'])
        assert restored['state']['phase'] == 'error' and restored['state']['stage'] == 'interrupted'
        assert restored['state']['effect'] == 'saved_not_mounted' and current['configurationBusy']
        assert not fresh.calls and not app.management.pending_config_tasks
        with pytest.raises(AppError, match='settings'):
            await app.dispatch('conversation.send', {'sessionId': current['id'], 'text': 'Still held'})
        await app.dispatch('configuration.operation.resume', {'sessionId': current['id'],
            'operationId': saved['id'], 'expectedRevision': restored['revision']})
        task = app.management.pending_config_tasks.get(current['id'])
        if task is not None:
            await asyncio.wait_for(task, 3)
        final = app.management.configuration_record(current['id'], saved['id'])
        assert final['request'] == saved['request'] and final['attempts'] == saved['attempts']
        assert final['state']['effect'] == 'mounted' and not current['configurationBusy']
        assert fresh.calls == ['start', 'fixture-mounted', 'configuration.inspect'] and not fresh.inputs
    finally:
        await app.close()
