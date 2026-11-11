"""Real update owner/sequence with synthetic native observations.

These are scheduling, fence and proof-composition tests. Native private files,
TokenUser, uv launcher, wheel transport and the actual installed host are separate
qualification; no native handoff or component promotion is claimed here.
"""
import asyncio
import copy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from amplifier_web import app_updates, manual_app_handoff as handoff, updates, update_sequence, update_health
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager


class Runtime:
    async def close(self):
        pass


@pytest.fixture
async def manager(tmp_path, monkeypatch):
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    manager.running_identity.update(version='0.71.4', revision='b' * 40)
    pending = {'revision': 'a' * 40, 'generation': 'c' * 32, 'attemptId': 'd' * 32,
               'latest': 'v0.71.3', 'extras': []}
    manager.diagnostics.begin('application', pending['revision'], pending['attemptId'], application_extras=[])
    manager.diagnostics.record('restart-capability', 'failed', errorType='RuntimeError')
    service.state['updates'].update(phase='error', error='retained refusal', pendingApp=pending,
        application={'id': 'application', 'current': '0.71.4', 'runningRevision': 'b' * 40,
                     'latest': 'v0.72.0', 'revision': 'e' * 40, 'status': 'update'},
        appAvailable=True, sequence={'stage': 'application', 'install': True})
    manager._storage_last_pass = time.time()
    manager._storage_policy_due = manager._update_health_initial = False
    service.state['settings']['updates'].update(autoCheck=False, autoInstall=False)
    yield manager
    await service.close()


def target(manager):
    return {'hostInstanceId': manager.service.instance_id,
            **{key: manager.service.state['updates']['pendingApp'][key]
               for key in ('revision', 'generation', 'attemptId')}}


@pytest.fixture
def proof(manager, monkeypatch):
    binding = {'version': '0.71.4', 'revision': 'b' * 40, 'graph': [], 'extras': [],
               'ownerSid': 'fixture-owner', 'dataIdentity': 'fixture-data',
               'directories': [['1', '2'], ['1', '3']], 'installation': {'digest': 'f' * 64}}
    value = {'binding': binding, 'payload': {'members': 100, 'recordDigest': '1' * 64},
             'stagedDigest': '2' * 64, 'launcher': 'fixture-launcher', 'tlsPin': '3' * 64}
    async def qualify(*args):
        return copy.deepcopy(value)
    monkeypatch.setattr(handoff, 'qualify', qualify)
    monkeypatch.setattr(handoff, '_native_binding', lambda manager: copy.deepcopy(binding))
    monkeypatch.setattr(handoff, '_payload', lambda proof: copy.deepcopy(value['payload']))
    monkeypatch.setattr(handoff, '_staged', lambda *args: ({}, value['stagedDigest']))
    monkeypatch.setattr(handoff, '_read', Path.read_bytes)
    return value


async def test_exact_refusal_retires_only_pending_app_and_preserves_truth(manager, proof):
    state = manager.service.state['updates']
    before, args = copy.deepcopy(state), target(manager)
    result = await handoff.reconcile(manager, args, 'request-one')
    assert result['phase'] == handoff.PHASE and result['status'] == 'succeeded'
    assert state['pendingApp'] is None
    assert state['application'] == before['application'] and state['appAvailable']
    assert state['diagnostics'] == before['diagnostics']
    assert 'applicationResult' not in state and not updates.active_release(manager.home)
    assert state['sequence']['nextStage'] == 'included' and state['sequence']['install']
    assert update_health.current(state)['status'] == 'action-required'
    assert update_health.current(state, manual_verified='request-one')['status'] == 'available'


@pytest.mark.parametrize('same_version', [False, True])
async def test_newer_availability_before_first_reconcile_preserves_manual_target(
        manager, proof, monkeypatch, same_version):
    monkeypatch.setattr(handoff, 'sys', SimpleNamespace(platform='win32'))
    state = manager.service.state['updates']
    if same_version:
        state['pendingApp'].update(latest='v0.71.4', revision='b' * 40)
        manager.diagnostics.begin('application', 'b' * 40, 'd' * 32, application_extras=[])
        manager.diagnostics.record('restart-capability', 'failed', errorType='RuntimeError')
    before = copy.deepcopy(state)
    async def forbidden(*args, **kwargs):
        pytest.fail('A newer release must not replace the completed manual launch target')
    monkeypatch.setattr(app_updates, 'stage', forbidden)
    monkeypatch.setattr(app_updates, 'activate', forbidden)
    monkeypatch.setattr(manager, 'check', forbidden)
    # Real owner path, before explicit reconciliation, including repeated ticks.
    await manager.app()
    await manager.app()
    assert state == before
    assert not state.get(handoff.RESULTS) and 'manualHandoff' not in state
    assert handoff.awaiting_verification(manager)
    result = await handoff.reconcile(manager, target(manager), 'manual-after-startup')
    assert result['status'] == 'succeeded'
    assert state['pendingApp'] is None and not handoff.awaiting_verification(manager)
    assert state['application'] == before['application']  # Newer availability survives.
    assert state['sequence']['stage'] == 'included' and state['sequence']['install']


@pytest.mark.parametrize('change', [
    'newer-pending', 'same-version-other-revision', 'missing-failure',
    'changed-attempt', 'changed-failure', 'malformed-target', 'malformed-version',
    'lower-running-version', 'unknown-restart', 'feature', 'non-windows',
])
def test_manual_target_hold_is_not_a_bypass_for_other_owners(manager, monkeypatch, change):
    monkeypatch.setattr(handoff, 'sys', SimpleNamespace(platform='win32'))
    state = manager.service.state['updates']
    if change == 'newer-pending':
        state['pendingApp']['latest'] = 'v0.72.0'
    elif change == 'same-version-other-revision':
        state['pendingApp']['latest'] = 'v0.71.4'
    elif change == 'missing-failure':
        manager.diagnostics.state.pop('lastFailure')
    elif change == 'changed-attempt':
        state['pendingApp']['attemptId'] = 'f' * 32
    elif change == 'changed-failure':
        manager.diagnostics.state['lastFailure'] = {
            **manager.diagnostics.state['lastFailure'], 'revision': 'f' * 40}
    elif change == 'malformed-target':
        state['pendingApp']['generation'] = 'not-a-generation'
    elif change == 'malformed-version':
        state['pendingApp']['latest'] = {'version': '0.71.3'}
    elif change == 'lower-running-version':
        manager.running_identity['version'] = '0.71.2'
    elif change == 'unknown-restart':
        state['pendingRestart'] = {'unknown': True}
    elif change == 'feature':
        state['pendingApp']['featureSelection'] = {'new': True}
    else:
        monkeypatch.setattr(handoff, 'sys', SimpleNamespace(platform='linux'))
    before = copy.deepcopy(state)
    assert not handoff.awaiting_verification(manager)
    assert state == before


async def test_manual_target_is_rechecked_after_awaited_availability(manager, monkeypatch):
    monkeypatch.setattr(handoff, 'sys', SimpleNamespace(platform='win32'))
    state = manager.service.state['updates']
    original = copy.deepcopy(state['pendingApp'])
    state['pendingApp']['latest'] = 'v0.72.0'
    async def check(**kwargs):
        state['pendingApp'] = original
    async def forbidden(*args, **kwargs):
        pytest.fail('The target changed while availability was awaited')
    monkeypatch.setattr(manager, 'check', check)
    monkeypatch.setattr(app_updates, 'stage', forbidden)
    monkeypatch.setattr(app_updates, 'activate', forbidden)
    await manager.app()
    assert state['pendingApp'] == original
    assert handoff.awaiting_verification(manager)


@pytest.mark.parametrize('hold', [False, True])
async def test_automatic_check_and_install_before_reconcile_survives_owner_reload(manager, monkeypatch, hold):
    monkeypatch.setattr(handoff, 'sys', SimpleNamespace(platform='win32'))
    state = manager.service.state['updates']
    pending = copy.deepcopy(state['pendingApp'])
    failure = copy.deepcopy(manager.diagnostics.state['lastFailure'])
    identity = copy.deepcopy(manager.running_identity)
    async def available():
        return {'id': 'application', 'current': '0.71.4', 'latest': 'v0.72.0',
                'revision': 'e' * 40, 'status': 'update', 'canInstall': True}
    calls = []
    newer = {'revision': 'e' * 40, 'generation': 'f' * 32, 'attemptId': '1' * 32,
             'latest': 'v0.72.0', 'extras': []}
    async def stage(owner):
        calls.append('stage-B')
        state['pendingApp'] = copy.deepcopy(newer)
    async def activate(owner):
        calls.append('activate-B')
        owner.diagnostics.begin('application', newer['revision'], newer['attemptId'], application_extras=[])
        owner.diagnostics.record('restart-capability', 'failed', errorType='RuntimeError')
    async def no_components(*args, **kwargs):
        pytest.fail('No component admission is authorized before manual verification')
    monkeypatch.setattr(app_updates, 'check', available)
    monkeypatch.setattr(app_updates, 'stage', stage)
    monkeypatch.setattr(app_updates, 'activate', activate)
    monkeypatch.setattr(manager, 'inventory_sources', no_components)
    if not hold:
        # Scheduling-only predecessor discriminator. All other real owner paths
        # run unchanged; native staging observations are synthetic in this file.
        monkeypatch.setattr(handoff, 'awaiting_verification', lambda manager: False)
    await manager.check(automatic=True)
    assert state['application']['revision'] == 'e' * 40
    await manager.install(automatic=True)
    if not hold:
        assert calls == ['stage-B', 'activate-B']
        assert state['pendingApp'] == newer  # The exact A target has been lost.
        assert manager.diagnostics.state['lastFailure']['revision'] == newer['revision']
        return
    assert calls == []
    assert state['pendingApp'] == pending
    assert manager.diagnostics.state['lastFailure'] == failure
    assert state['sequence'] == {'stage': 'application', 'install': True}
    assert not state.get(handoff.RESULTS) and 'manualHandoff' not in state
    manager.service._save_changes(globals={'updates'})
    restored = UpdateManager(manager.service)
    restored.running_identity = identity
    # The hold derives from saved exact intent/refusal, never a transient flag.
    await restored.app()
    assert restored.service.state['updates']['pendingApp'] == pending
    assert restored.diagnostics.state['lastFailure'] == failure
    assert handoff.awaiting_verification(restored)


async def test_preventive_hold_never_borrows_missing_native_proof(manager, monkeypatch):
    monkeypatch.setattr(handoff, 'sys', SimpleNamespace(platform='win32'))
    state = manager.service.state['updates']
    pending = copy.deepcopy(state['pendingApp'])
    async def missing(*args):
        raise ValueError('fixture: official wheel or installed graph changed')
    monkeypatch.setattr(handoff, 'qualify', missing)
    assert handoff.awaiting_verification(manager)
    result = await handoff.reconcile(manager, target(manager), 'missing-native-proof')
    assert result['status'] == 'rejected'
    assert state['pendingApp'] == pending
    assert 'manualHandoff' not in state
    assert state['sequence']['stage'] == 'application'


def test_scheduling_hold_does_not_copy_release_notes_or_history(manager, monkeypatch):
    monkeypatch.setattr(handoff, 'sys', SimpleNamespace(platform='win32'))
    manager.service.state['updates']['pendingApp']['releaseNotes'] = [
        {'changes': ['retained history'] * 1000}]
    def forbidden(*args, **kwargs):
        pytest.fail('Scheduling must not copy pending release history')
    monkeypatch.setattr(handoff, 'copy', SimpleNamespace(deepcopy=forbidden))
    assert handoff.awaiting_verification(manager)


@pytest.mark.parametrize('install', [False, True])
async def test_713_to_714_with_720_available_reaches_normal_component_stage(manager, proof, monkeypatch, install):
    state = manager.service.state['updates']
    state['sequence']['install'] = install
    # Even automatic preference must not create missing intent for this receipt.
    manager.service.state['settings']['updates']['autoInstall'] = True
    await handoff.reconcile(manager, target(manager), 'request-components')
    calls = []
    async def inventory(**kwargs):
        calls.append('inventory')
        return [{'id': 'included', 'kind': 'runtime environment', 'status': 'update',
                 'eligible': True, 'updateTier': 'included',
                 'appSelection': update_sequence.app_selection(manager.home)['witness']}]
    async def never_app():
        pytest.fail('The verified app leg must not retarget to 0.72.0')
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(manager, 'app', never_app)
    # Stop at the real component staging boundary, before any fixture install.
    def stage_selection(*args, **kwargs):
        calls.append('component-stage')
        raise ValueError('fixture: reached ordinary component selection')
    monkeypatch.setattr(update_sequence, 'verify_app_selection', lambda *args, **kwargs: None)
    await manager.check(tier='included', install=install, automatic=True)
    assert state['appAvailable'] and state['available'] == 2
    assert state['sequence']['stage'] == 'included'
    monkeypatch.setattr(update_sequence, 'verify_app_selection', stage_selection)
    async def installed_target():
        return ('uv', 'fixture-launcher', Path(sys.prefix) / 'Scripts/python.exe',
                {'installation': sys.prefix})
    async def health(*args):
        return proof['tlsPin']
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(handoff, '_health', health)
    if install:
        with pytest.raises(ValueError, match='ordinary component selection'):
            await manager.install(automatic=True)
        assert calls == ['inventory', 'component-stage']
    else:
        await manager.tick()
        await manager.install(automatic=True)
        assert calls == ['inventory']


@pytest.mark.parametrize('key,value', [
    ('pendingRestart', {}), ('pendingRestart', {'unknown': True}),
    ('pendingReplacement', {}), ('pendingRelease', 'later-generation'), ('pendingRollback', {}),
    ('pendingSmartTools', [{'unknown': True}]), ('phase', 'activating'),
    ('drain', {'phase': 'replacing'}),
    ('featureResults', {'feature': {'phase': 'queued'}})])
async def test_other_owners_are_never_cleared(manager, proof, key, value):
    state = manager.service.state['updates']
    args = target(manager)
    state[key] = value
    before = copy.deepcopy(state)
    assert (await handoff.reconcile(manager, args, 'refused'))['status'] == 'rejected'
    assert {k: v for k, v in state.items() if k != handoff.RESULTS} == before


@pytest.mark.parametrize('mutation', ['pending', 'failure', 'configuration', 'intent', 'feature', 'sequence'])
async def test_change_during_verification_refuses_without_retiring_new_state(manager, proof, monkeypatch, mutation):
    state = manager.service.state['updates']
    async def changed(*args):
        if mutation == 'pending':
            state['pendingApp']['generation'] = 'f' * 32
        elif mutation == 'failure':
            manager.diagnostics.record('restart-capability', 'failed', errorType='RuntimeError')
        elif mutation == 'configuration':
            manager.service.state['configurationRevision'] = 99
        elif mutation == 'intent':
            state['intentRevision'] = 99
        elif mutation == 'feature':
            state['pendingApp']['featureSelection'] = {'changed': True}
        else:
            state['sequence']['install'] = False
        return copy.deepcopy(proof)
    monkeypatch.setattr(handoff, 'qualify', changed)
    assert (await handoff.reconcile(manager, target(manager), 'raced'))['status'] == 'rejected'
    assert state['pendingApp'] and 'manualHandoff' not in state


@pytest.mark.parametrize('field', ['ownerSid', 'dataIdentity', 'directories', 'installation', 'graph', 'extras', 'revision'])
async def test_current_installation_change_at_commit_refuses(manager, proof, monkeypatch, field):
    changed = {**proof['binding'], field: 'changed'}
    monkeypatch.setattr(handoff, '_native_binding', lambda manager: changed)
    assert (await handoff.reconcile(manager, target(manager), 'changed'))['status'] == 'rejected'
    assert manager.service.state['updates']['pendingApp']


async def test_exact_retry_and_reload_do_not_reexecute_verifier(manager, proof, monkeypatch):
    args = target(manager)
    first = await handoff.reconcile(manager, args, 'exact')
    async def never(*args):
        pytest.fail('An exact retry must read the durable result')
    monkeypatch.setattr(handoff, 'qualify', never)
    assert await handoff.reconcile(manager, args, 'exact') == first
    manager.service._save_changes(globals={'updates'})
    restored = UpdateManager(manager.service)
    assert await handoff.reconcile(restored, args, 'exact') == first
    assert getattr(restored, '_manual_handoff_verified', None) is None
    calls = []
    async def installed_target():
        calls.append('launcher')
        return ('uv', 'fixture-launcher', Path(sys.prefix) / 'Scripts/python.exe', {'installation': sys.prefix})
    async def health(*args):
        calls.append('health')
        return proof['tlsPin']
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(handoff, '_health', health)
    assert await handoff.verify_continuation(restored)
    assert calls == ['launcher', 'health']
    proof['binding']['directories'] = [['copied', 'home']]
    with pytest.raises(ValueError):
        await handoff.verify_continuation(restored)
    assert restored._manual_handoff_verified is None


async def test_cancelled_verification_is_not_replayed(manager, proof, monkeypatch):
    args = target(manager)
    async def cancelled(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(handoff, 'qualify', cancelled)
    with pytest.raises(asyncio.CancelledError):
        await handoff.reconcile(manager, args, 'cancelled')
    assert manager.service.state['updates']['pendingApp']
    assert (await handoff.reconcile(manager, args, 'cancelled'))['status'] == 'interrupted'


async def test_new_deliberate_check_restores_app_first(manager, proof, monkeypatch):
    await handoff.reconcile(manager, target(manager), 'completed')
    async def application():
        return {'id': 'application', 'status': 'update', 'latest': 'v0.72.0'}
    async def inventory(**kwargs):
        pytest.fail('A fresh cycle must check the app first')
    monkeypatch.setattr(app_updates, 'check', application)
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    await manager.check()
    assert manager.service.state['updates']['sequence']['stage'] == 'application'
    assert not handoff.continuing(manager)


def test_non_native_installation_cannot_supply_proof(manager):
    if sys.platform != 'win32':
        with pytest.raises(ValueError, match='native Windows'):
            handoff._native_binding(manager)


@pytest.fixture
def native_observations(manager, monkeypatch, tmp_path):
    """Only I/O observations are synthetic; run the actual qualify composition."""
    from amplifier_web import app_release_artifacts as artifacts
    wheel = tmp_path / artifacts.filename('0.71.4')
    binding = {'version': '0.71.4', 'revision': 'b' * 40, 'graph': [], 'extras': [],
               'ownerSid': 'fixture-owner', 'direct': {'url': wheel.as_uri(),
                   'archive_info': {'hashes': {'sha256': 'f' * 64}}}}
    staged = {'version': '0.71.3', 'revision': 'a' * 40, 'extras': [],
              'artifact': {'sha256': 'f' * 64}, 'componentGraph': []}
    calls = []
    monkeypatch.setattr(handoff, '_native_binding', lambda manager: copy.deepcopy(binding))
    monkeypatch.setattr(handoff, '_staged', lambda *args: (copy.deepcopy(staged), 'staged-digest'))
    monkeypatch.setattr(handoff, '_payload', lambda proof: {'members': 5})
    async def installed_target():
        calls.append('target')
        return ('uv', 'fixture-launcher', Path(sys.prefix) / 'Scripts/python.exe',
                {'installation': sys.prefix})
    async def prepare(folder, version, revision):
        calls.append(('official-wheel', version, revision))
        return {'url': artifacts.SOURCE + '/releases/download/v' + version + '/' + artifacts.filename(version),
                'sha256': 'f' * 64, 'path': str(folder / artifacts.filename(version))}
    async def process(python, isolated, code_arg, script, *args, **kwargs):
        assert isolated == '-I' and code_arg == '-c'
        calls.append('probe' if script == app_updates.PROBE else 'dependencies')
        return 'AMPLIFIER_UPDATE_PROBE=' + json.dumps({'ok': True, 'isolated': True,
                'stage': 'complete', 'version': binding['version']})
    async def health(*args):
        calls.append('health')
        return 'pin'
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(artifacts, 'prepare', prepare)
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr(handoff, '_health', health)
    return binding, staged, calls


@pytest.mark.parametrize('version', ['0.71.3', '0.71.4', '0.72.0'])
async def test_each_running_version_requires_its_own_official_payload(manager, native_observations, version, tmp_path):
    from amplifier_web import app_release_artifacts as artifacts
    binding, staged, calls = native_observations
    binding.update(version=version, revision=staged['revision'] if version == '0.71.3' else 'b' * 40)
    binding['direct']['url'] = (tmp_path / artifacts.filename(version)).as_uri()
    value = await handoff.qualify(manager, manager.service.state['updates']['pendingApp'])
    assert value['binding'] == binding
    assert calls == ['target', ('official-wheel', version, binding['revision']),
                     'probe', 'dependencies', 'health', 'target']


@pytest.mark.parametrize('mutation', ['older', 'same-version-revision', 'same-version-hash',
                                    'same-version-graph', 'extras', 'custom', 'malformed-hash', 'wrong-hash'])
async def test_invalid_manual_install_never_qualifies(manager, native_observations, mutation, tmp_path):
    from amplifier_web import app_release_artifacts as artifacts
    binding, staged, calls = native_observations
    if mutation == 'older':
        binding['version'] = '0.71.2'
    elif mutation.startswith('same-version'):
        binding.update(version='0.71.3', revision=staged['revision'])
        binding['direct']['url'] = (tmp_path / artifacts.filename('0.71.3')).as_uri()
        if mutation == 'same-version-revision':
            binding['revision'] = 'c' * 40
        elif mutation == 'same-version-hash':
            staged['artifact']['sha256'] = 'c' * 64
        else:
            staged['componentGraph'] = [{'name': 'different', 'version': '1.0'}]
    elif mutation == 'extras':
        binding['extras'] = ['tui']
    elif mutation == 'custom':
        binding['direct'] = {'url': 'https://example.invalid/custom.whl', 'archive_info': {}}
    elif mutation == 'malformed-hash':
        binding['direct']['archive_info'] = {'hashes': 'invalid'}
    else:
        binding['direct']['archive_info']['hashes']['sha256'] = 'c' * 64
    with pytest.raises(ValueError):
        await handoff.qualify(manager, manager.service.state['updates']['pendingApp'])
    assert 'probe' not in calls and manager.service.state['updates']['pendingApp']


async def test_uv_empty_archive_info_still_requires_official_and_payload_proof(manager, native_observations):
    binding, _, calls = native_observations
    binding['direct']['archive_info'] = {}
    proof = await handoff.qualify(manager, manager.service.state['updates']['pendingApp'])
    assert proof['artifact']['sha256'] == 'f' * 64
    assert ('official-wheel', binding['version'], binding['revision']) in calls
    assert 'probe' in calls and 'health' in calls


@pytest.mark.parametrize('boundary', ['probe', 'dependencies', 'health', 'final-binding', 'final-payload'])
async def test_failed_or_mutated_current_proof_remains_failure(manager, native_observations, monkeypatch, boundary):
    binding, _, calls = native_observations
    original_process, original_health = app_updates.process, handoff._health
    if boundary in {'probe', 'dependencies'}:
        async def failing(*args, **kwargs):
            is_probe = args[3] == app_updates.PROBE
            if is_probe == (boundary == 'probe'):
                raise ValueError('fixture failed')
            return await original_process(*args, **kwargs)
        monkeypatch.setattr(app_updates, 'process', failing)
    elif boundary == 'health':
        async def failing(*args):
            raise ValueError('fixture wrong listener')
        monkeypatch.setattr(handoff, '_health', failing)
    else:
        async def mutate(*args):
            if boundary == 'final-binding':
                binding['graph'] = [{'name': 'changed', 'version': '1'}]
            else:
                monkeypatch.setattr(handoff, '_payload', lambda proof: {'members': 6})
            return await original_health(*args)
        monkeypatch.setattr(handoff, '_health', mutate)
    with pytest.raises(ValueError):
        await handoff.qualify(manager, manager.service.state['updates']['pendingApp'])


@pytest.mark.parametrize('after_save', [False, True])
async def test_failed_publication_reads_commit_before_reporting_outcome(manager, proof, monkeypatch, after_save):
    state, args = manager.service.state['updates'], target(manager)
    manager.service._save_changes(globals={'updates'})
    before = copy.deepcopy(state['pendingApp'])
    publish = manager.service._publish_changes
    calls = 0
    def failure(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            if after_save:
                publish(**kwargs)
            raise OSError('fixture publication failure')
        return publish(**kwargs)
    monkeypatch.setattr(manager.service, '_publish_changes', failure)
    result = await handoff.reconcile(manager, args, 'publication')
    assert result['status'] == ('succeeded' if after_save else 'rejected')
    durable = handoff._saved_updates(manager)
    assert durable[handoff.RESULTS]['publication'] == result
    if after_save:
        assert state['pendingApp'] is None and durable['pendingApp'] is None
    else:
        assert state['pendingApp'] == durable['pendingApp'] == before
        assert 'manualHandoff' not in state and not handoff.continuing(manager)


async def test_unknown_commit_read_does_not_retry_or_enable_work(manager, proof, monkeypatch):
    args = target(manager)
    publish = manager.service._publish_changes
    def committed_but_unreadable(**kwargs):
        publish(**kwargs)
        raise OSError('projection failed')
    def unreadable(manager):
        raise OSError('readback failed')
    monkeypatch.setattr(manager.service, '_publish_changes', committed_but_unreadable)
    monkeypatch.setattr(handoff, '_saved_updates', unreadable)
    result = await handoff.reconcile(manager, args, 'unreadable')
    assert result['status'] == 'unknown' and manager._manual_handoff_commit_unknown
    assert manager.service.state['updates']['pendingApp']
    async def fail(*args, **kwargs):
        pytest.fail('Unknown commit cannot run updater work')
    monkeypatch.setattr(manager, 'app', fail)
    monkeypatch.setattr(manager, 'inventory_sources', fail)
    await manager.tick()
    await manager.install()
    await manager.check()
    assert await handoff.reconcile(manager, args, 'unreadable') == result


async def test_completed_receipt_revalidates_after_reload_without_recreating_intent(manager, proof, monkeypatch):
    await handoff.reconcile(manager, target(manager), 'completed-reload')
    state = manager.service.state['updates']
    state['sequence'].update(stage='complete', install=False)
    state['sequence'].pop('nextStage', None)
    state['phase'] = 'checked'
    restored = UpdateManager(manager.service)
    restored._storage_last_pass = time.time()
    restored._storage_policy_due = False
    before = copy.deepcopy(state['sequence'])
    calls = []
    async def installed_target():
        return ('uv', 'fixture-launcher', Path(sys.prefix) / 'Scripts/python.exe', {'installation': sys.prefix})
    async def health(*args):
        calls.append('health')
        return proof['tlsPin']
    async def no_install(*args, **kwargs):
        pytest.fail('Revalidating current installation is not installation intent')
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(handoff, '_health', health)
    monkeypatch.setattr(restored, 'install', no_install)
    await restored.tick()
    assert calls == ['health'] and state['sequence'] == before
    assert restored._manual_handoff_verified == 'completed-reload'
    assert not handoff.continuing(restored)


async def test_private_proof_is_not_published_in_updates(manager, proof):
    await handoff.reconcile(manager, target(manager), 'private-proof')
    state = manager.service.state['updates']
    encoded = json.dumps(state)
    assert 'fixture-owner' not in encoded and 'fixture-data' not in encoded
    assert 'fixture-launcher' not in encoded and 'directories' not in state['manualHandoff']
    assert 'proof' not in state['manualHandoff']
    assert handoff._proof(manager, state['manualHandoff']) == proof
    assert handoff._saved_updates(manager)['manualHandoff'] == state['manualHandoff']


@pytest.mark.parametrize('action', ['updates.check', 'updates.rollback'])
async def test_new_dispatched_intent_during_continuation_health_refuses(manager, proof, monkeypatch, action):
    await handoff.reconcile(manager, target(manager), 'intent')
    state = manager.service.state['updates']
    calls = []
    async def queued(*args):
        calls.append(args[0])
    monkeypatch.setattr(manager, 'command', queued)
    async def installed_target():
        return ('uv', 'fixture-launcher', Path(sys.prefix) / 'Scripts/python.exe', {'installation': sys.prefix})
    async def health(*args):
        await manager.service.dispatch(action, {}, command_id='new-intent')
        return proof['tlsPin']
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(handoff, '_health', health)
    before = copy.deepcopy(state['sequence'])
    with pytest.raises(ValueError):
        await handoff.verify_continuation(manager, full=True)
    assert state['sequence'] == before
    assert manager._manual_handoff_verified is None


async def test_public_action_exact_retry_retains_single_result(manager, proof, monkeypatch):
    done = asyncio.Event()
    command = manager.command
    async def observed(*args):
        try:
            return await command(*args)
        finally:
            done.set()
    monkeypatch.setattr(manager, 'command', observed)
    args = target(manager)
    first = await manager.service.dispatch('updates.reconcileManualHandoff', args, command_id='public-reconcile')
    await asyncio.wait_for(done.wait(), 10)
    assert manager.service.state['updates'][handoff.RESULTS]['public-reconcile']['status'] == 'succeeded'
    second = await manager.service.dispatch('updates.reconcileManualHandoff', args, command_id='public-reconcile')
    assert second['duplicate'] is True and first['requestId'] == second['requestId']
    assert first['accepted'] and second['accepted']
    assert second['state']['updates'][handoff.RESULTS]['public-reconcile']['status'] == 'succeeded'


@pytest.mark.parametrize('after_save', [False, True])
async def test_unknown_commit_survives_dispatch_and_full_shutdown_save(manager, proof, monkeypatch, after_save):
    from amplifier_web.service import AppError
    state = manager.service.state['updates']
    manager.service._save_changes(globals={'updates'})
    publish, saved_read = manager.service._publish_changes, handoff._saved_updates
    def failed(**kwargs):
        if after_save:
            publish(**kwargs)
        raise OSError('publication failure')
    monkeypatch.setattr(manager.service, '_publish_changes', failed)
    monkeypatch.setattr(handoff, '_saved_updates', lambda manager: (_ for _ in ()).throw(OSError('read refused')))
    result = await handoff.reconcile(manager, target(manager), 'commit-unknown')
    assert result['status'] == 'unknown'
    monkeypatch.setattr(manager.service, '_publish_changes', publish)
    durable_before = saved_read(manager)
    with pytest.raises(AppError, match='commit is unknown'):
        await manager.service.dispatch('updates.check', {}, command_id='after-unknown')
    manager.service._save_changes(globals={'updates', 'settings'})
    manager.service._save_full(reason='Test normal shutdown preservation')
    assert saved_read(manager) == durable_before
    assert bool(durable_before.get('manualHandoff')) == after_save
    assert bool(durable_before.get('pendingApp')) != after_save


@pytest.mark.parametrize('changed', [False, True])
async def test_final_scan_is_offloop_and_state_is_rechecked(manager, proof, monkeypatch, changed):
    import threading
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = handoff._final_verify
    def paused(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(handoff, '_final_verify', paused)
    task = asyncio.create_task(handoff.reconcile(manager, target(manager), 'offloop'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # This lock/action is available during the expensive package scan.
        async with asyncio.timeout(2), manager.service.lock:
            if changed:
                manager.service.state['updates']['intentRevision'] = 10
            assert not task.done()
    finally:
        release.set()
    result = await task
    assert result['status'] == ('rejected' if changed else 'succeeded')


async def test_cancelled_final_scan_settles_handles_before_return(manager, proof, monkeypatch):
    import threading
    entered, release, settled = asyncio.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    def paused(*args):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(5)
        finally:
            settled.set()
    monkeypatch.setattr(handoff, '_final_verify', paused)
    task = asyncio.create_task(handoff.reconcile(manager, target(manager), 'cancel-scan'))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert settled.is_set()
    assert manager.service.state['updates']['pendingApp']


def test_pinned_reader_rechecks_path_identity_and_closes_all_handles(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from amplifier_web import windows_read_files
    path = tmp_path / 'payload'
    path.write_bytes(b'bytes')
    signature, opened, closed = [1], [], []
    @contextmanager
    def open_file(name):
        with open(name, 'rb') as stream:
            stream.identity = signature[0]
            opened.append(stream)
            try:
                yield stream
            finally:
                closed.append(stream)
    monkeypatch.setattr(windows_read_files, 'open_file', open_file)
    monkeypatch.setattr(windows_read_files, 'stream_signature', lambda stream: stream.identity)
    with pytest.raises(ValueError):
        with handoff._pinned_reader() as read:
            assert read(path) == b'bytes'
            signature[0] = 2
    assert opened == closed or set(opened) == set(closed)
    assert all(stream.closed for stream in opened)