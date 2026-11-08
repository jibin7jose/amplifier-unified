"""Host-owned setup receipts; all backend effects are controlled here."""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import test_environment_setup as setup
from amplifier_web import task_environments as owner


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.sys, 'platform', 'linux')
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    value = SimpleNamespace(data_dir=home, smart_tools=SimpleNamespace(state={'installations': []}, install=AsyncMock(return_value={'id': 'a' * 24, 'commit': owner.DTU_COMMIT, 'extras': []})))
    monkeypatch.setattr(owner, 'configuration', lambda _: (None, 'disabled', 'Not set up'))
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda _, value: (value, 'ready', 'Verified'))
    monkeypatch.setattr(setup, 'inspect_base_image', lambda _: None)
    monkeypatch.setattr(setup, 'discover', lambda: [{'id': 'docker', 'label': 'Docker', 'available': True, 'reason': 'Available', 'executable': '/usr/bin/docker', 'dockerHost': 'unix:///run/docker.sock'}, {'id': 'incus', 'label': 'Incus', 'available': False, 'reason': 'Unavailable'}])
    return value


def args(**extra):
    return {'workspaceId': 'workspace', 'sessionId': 'session', 'requestId': 'setup', 'backend': 'docker', **extra}


def test_shared_schema_allows_docker_default_and_explicit_incus():
    import jsonschema
    definitions = setup.definitions(
        lambda fields, required: {'type': 'object', 'properties': fields, 'required': required},
        lambda maximum: {'type': 'string', 'maxLength': maximum})
    schema = definitions['workspace.environments.setup'][1]
    assert schema['properties']['backend']['default'] == 'docker'
    jsonschema.validate({key: value for key, value in args().items() if key != 'backend'}, schema)
    jsonschema.validate(args(backend='incus'), schema)


async def settle(app):
    await asyncio.gather(*list(app._test_setup_tasks.values()))
    latest = setup.read_json(setup.root(app) / 'latest.json')
    return setup.read_json(setup.root(app) / (latest['id'] + '.json'))


async def test_passive_read_does_not_create_setup_or_install(app):
    result = await setup.read(app)
    assert result['status'] == 'disabled' and result['operation'] is None
    assert not (app.data_dir / 'test-runner-setup').exists()
    app.smart_tools.install.assert_not_called()
    assert 'executable' not in result['backends'][0]
    assert 'dockerHost' not in result['backends'][0]


async def test_setup_returns_before_work_and_reuses_exact_managed_core(app, monkeypatch):
    # A qualified older installation remains available for its existing owners;
    # new setup must still request the current reviewed snapshot.
    previous = {'id': 'b' * 24, 'commit': '8f644b3d8d8b89f0bb3ae9853bb531017dc98a04',
                'status': 'installed', 'extras': []}
    app.smart_tools.state['installations'].append(previous)
    entered, release = asyncio.Event(), asyncio.Event()
    async def backend(base, selected, owned, record, step):
        await step('checking_profile', 'Checking')
        entered.set()
        await release.wait()
        return {**base, 'dockerHost': selected['dockerHost'], 'image': owner.APPROVED_IMAGE}, {'probeRemoved': True, 'offline': True}
    monkeypatch.setattr(setup, 'setup_docker', backend)
    first = await setup.request(app, args(), {'sessionId': 'session'})
    assert first['operation']['state'] == 'accepted'
    await entered.wait()
    duplicate = await setup.request(app, args(), {'sessionId': 'session'})
    assert duplicate['duplicate'] and duplicate['operation']['id'] == first['operation']['id']
    with pytest.raises(ValueError, match='already in progress'):
        await setup.request(app, args(requestId='other'), {'sessionId': 'session'})
    with pytest.raises(ValueError, match='another intent'):
        await setup.request(app, args(backend='incus'), {'sessionId': 'session'})
    release.set()
    result = await settle(app)
    assert result['state'] == 'completed'
    assert result['proof']['probeRemoved']
    app.smart_tools.install.assert_awaited_once_with({'repository': owner.REPOSITORY, 'ref': owner.DTU_COMMIT, 'path': '.', 'extras': []})
    config = app.data_dir / 'test-environments.json'
    assert config.stat().st_mode & 0o777 == 0o600
    assert json.loads(config.read_text())['commit'] == owner.DTU_COMMIT
    assert app.smart_tools.state['installations'] == [previous]


async def test_revoke_before_external_effect_never_installs(app, monkeypatch):
    calls = 0
    async def guard():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError('Permission was revoked')
    await setup.request(app, args(), {'sessionId': 'session'}, guard)
    result = await settle(app)
    assert result['state'] == 'failed'
    app.smart_tools.install.assert_not_called()
    assert not (app.data_dir / 'test-environments.json').exists()


async def test_omitted_backend_is_docker_and_duplicate_keeps_same_intent(app, monkeypatch):
    async def backend(base, selected, owned, record, step):
        assert base['backend'] == 'docker'
        return {**base, 'dockerHost': selected['dockerHost']}, {'probeRemoved': True}
    monkeypatch.setattr(setup, 'setup_docker', backend)
    request = {key: value for key, value in args().items() if key != 'backend'}
    await setup.request(app, request, {'sessionId': 'session'})
    result = await settle(app)
    assert result['state'] == 'completed' and result['backend'] == 'docker'
    assert result['intent'] == args()
    assert (await setup.request(app, args(), {'sessionId': 'session'}))['duplicate']
    app.smart_tools.install.assert_awaited_once()


async def test_default_never_replaces_existing_incus_binding(app, monkeypatch):
    existing = {'backend': 'incus', 'installationId': 'old', 'incus': {'project': 'retained'}}
    monkeypatch.setattr(owner, 'configuration', lambda _: (existing, 'ready', 'Existing Incus'))
    request = {key: value for key, value in args().items() if key != 'backend'}
    await setup.request(app, request, {'sessionId': 'session'})
    result = await settle(app)
    assert result['state'] == 'failed'
    assert 'does not silently replace' in result['detail']
    app.smart_tools.install.assert_not_called()
    assert existing == {'backend': 'incus', 'installationId': 'old', 'incus': {'project': 'retained'}}


async def test_unknown_side_effect_never_replays_and_blocks_new_intent(app, monkeypatch):
    calls = []
    async def backend(*values):
        await values[-1]('checking_profile', 'Checking')
        calls.append('attempted')
        raise TimeoutError('The check outcome is unknown')
    monkeypatch.setattr(setup, 'setup_docker', backend)
    await setup.request(app, args(), {'sessionId': 'session'})
    result = await settle(app)
    assert result['state'] == 'outcome_unknown'
    duplicate = await setup.request(app, args(), {'sessionId': 'session'})
    assert duplicate['operation']['state'] == 'outcome_unknown'
    with pytest.raises(ValueError, match='uncertain result'):
        await setup.request(app, args(requestId='new'), {'sessionId': 'session'})
    assert calls == ['attempted']
    assert not (app.data_dir / 'test-environments.json').exists()


async def test_current_configuration_change_is_not_overwritten(app, monkeypatch):
    async def backend(base, selected, owned, record, step):
        setup.write_json(app.data_dir / 'test-environments.json', {'enabled': False})
        return base, {'probeRemoved': True}
    monkeypatch.setattr(setup, 'setup_docker', backend)
    await setup.request(app, args(), {'sessionId': 'session'})
    result = await settle(app)
    assert result['state'] == 'outcome_unknown'
    assert json.loads((app.data_dir / 'test-environments.json').read_text()) == {'enabled': False}


async def test_already_saved_exact_probe_can_reconcile_without_effects(app, monkeypatch):
    folder = setup.root(app)
    identity = '1234'
    # Construct a completed-publication / interrupted-receipt boundary.
    intent = args()
    import hashlib
    identity = hashlib.sha256(json.dumps([intent['sessionId'], intent['requestId']], separators=(',', ':')).encode()).hexdigest()[:32]
    config = {'enabled': True, 'backend': 'docker'}
    record = {'id': identity, 'intent': intent, 'actor': {}, 'backend': 'docker', 'state': 'outcome_unknown', 'candidateConfiguration': config, 'proof': {'probeRemoved': True}}
    setup.write_json(folder / (identity + '.json'), record)
    setup.write_json(app.data_dir / 'test-environments.json', config)
    monkeypatch.setattr(setup, 'readiness', AsyncMock(return_value={'status': 'ready'}))
    result = await setup.request(app, intent, {})
    assert result['operation']['state'] == 'completed' and result['duplicate']
    app.smart_tools.install.assert_not_called()


async def test_readiness_cache_expires_and_rechecks_exact_config(app, monkeypatch):
    config = {'enabled': True, 'backend': 'docker'}
    setup.write_json(app.data_dir / 'test-environments.json', config)
    monkeypatch.setattr(owner, 'configuration', lambda _: (config, 'ready', 'Configured'))
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *a: calls.append(a) or {'ok': True, 'capacity': {'blockers': []}})
    setup.retain_verification(app, config, {'probeRemoved': True})
    first = await setup.readiness(app)
    assert first['status'] == 'ready' and first['observedAt']
    assert await setup.readiness(app) == first
    assert len(calls) == 1
    app._test_setup_readiness['at'] -= 31
    await setup.readiness(app)
    assert len(calls) == 2
    setup.write_json(app.data_dir / 'test-environments.json', {**config, 'new': True})
    await setup.readiness(app)
    assert len(calls) == 3
    await setup.readiness(app, force=True)
    assert len(calls) == 4


async def test_platform_limit_never_installs(app, monkeypatch):
    monkeypatch.setattr(setup.sys, 'platform', 'darwin')
    with pytest.raises(ValueError, match='qualified on Linux'):
        await setup.request(app, args(), {})
    app.smart_tools.install.assert_not_called()


@pytest.mark.parametrize('kind', ['symlink', 'world-readable', 'hardlink'])
def test_private_config_publication_preserves_unsafe_existing_file(app, kind):
    target = app.data_dir / 'record.json'
    other = app.data_dir / 'other.json'
    other.write_text('{}')
    other.chmod(0o600)
    if kind == 'symlink':
        target.symlink_to(other)
    elif kind == 'hardlink':
        os.link(other, target)
    else:
        target.write_text('{}')
        target.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        setup.write_json(target, {'changed': True})
    assert other.read_text() == '{}'


async def test_docker_probe_is_offline_owned_and_absence_verified(app, monkeypatch, tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if 'run' in argv:
            return '{"python":"3.13.0","offline":true}'
        if 'info' in argv:
            return '{"ID":"daemon"}'
        return ''
    monkeypatch.setattr(setup, 'command', run)
    phases = []
    async def step(phase, detail):
        phases.append(phase)
    selected = {'executable': '/usr/bin/docker', 'dockerHost': 'unix:///run/docker.sock'}
    config, proof = await setup.setup_docker({'backend': 'docker'}, selected, tmp_path, {'id': 'f' * 32}, step)
    run = next(row for row in calls if 'run' in row)
    assert run[run.index('--network') + 1] == 'none'
    assert '--read-only' in run and '--rm' in run and '--label' in run
    assert not any(value in run for value in ['--mount', '-v'])
    assert config['image'] == owner.APPROVED_IMAGE and proof['probeRemoved']
    assert phases == ['preparing_image', 'checking_profile', 'checking_profile']


async def test_interrupted_setup_cleanup_is_owned_and_retains_image(app, monkeypatch):
    folder = setup.root(app)
    import hashlib
    identity = hashlib.sha256(json.dumps(['session', 'setup'], separators=(',', ':')).encode()).hexdigest()[:32]
    row = {'id': identity, 'intent': args(), 'actor': {}, 'backend': 'docker', 'state': 'outcome_unknown'}
    setup.write_json(folder / (identity + '.json'), row)
    setup.write_json(folder / 'latest.json', {'id': identity})
    observations = [
        {'backend': 'docker', 'absent': False, 'prefix': ['docker', '--host', 'unix:///owned.sock'], 'probe': {'name': 'owned-probe'}},
        {'backend': 'docker', 'absent': True},
    ]
    monkeypatch.setattr(setup, 'observe_partial', lambda *a: observations.pop(0))
    calls = []
    monkeypatch.setattr(setup, 'command', lambda command, **kwargs: calls.append(command) or '')
    await setup.cleanup(app, args(), {})
    result = await settle(app)
    assert result['state'] == 'recovered' and result['originalState'] == 'outcome_unknown'
    assert result['cleanup']['resourcesAbsent']
    assert [call[-2:] for call in calls] == [['stop', 'owned-probe'], ['rm', 'owned-probe']]
    duplicate = await setup.cleanup(app, args(), {})
    assert duplicate['duplicate'] and len(calls) == 2


async def test_cleanup_refuses_selected_ready_runner(app):
    folder = setup.root(app)
    import hashlib
    identity = hashlib.sha256(json.dumps(['session', 'setup'], separators=(',', ':')).encode()).hexdigest()[:32]
    row = {'id': identity, 'intent': args(), 'actor': {}, 'backend': 'docker', 'state': 'completed'}
    setup.write_json(folder / (identity + '.json'), row)
    with pytest.raises(ValueError, match='ready runner'):
        await setup.cleanup(app, args(), {})


async def test_reconcile_absent_partial_setup_does_not_repeat_install(app, monkeypatch):
    folder = setup.root(app)
    import hashlib
    identity = hashlib.sha256(json.dumps(['session', 'setup'], separators=(',', ':')).encode()).hexdigest()[:32]
    row = {'id': identity, 'intent': args(), 'actor': {}, 'backend': 'docker', 'state': 'outcome_unknown'}
    setup.write_json(folder / (identity + '.json'), row)
    monkeypatch.setattr(setup, 'observe_partial', lambda *a: {'absent': True})
    result = await setup.reconcile(app, args(), {})
    assert result['operation']['state'] == 'recovered'
    app.smart_tools.install.assert_not_called()


def test_capacity_defaults_use_host_memory_conservatively(monkeypatch):
    for gib, slots in [(8, 1), (16, 2), (128, 2)]:
        monkeypatch.setattr(setup.os, 'sysconf', lambda key: 4096 if key == 'SC_PAGE_SIZE' else gib * 1024 ** 3 // 4096)
        policy = setup.capacity_defaults()
        assert policy['maxRunning'] == slots
        assert policy['maxInstances'] == slots + 2
        assert policy['minFreeBytes'] == 4 * 1024 ** 3


def test_verified_native_setup_relies_on_resource_admission_not_fixed_counts():
    assert setup.capacity_defaults(resource_admission=True) == {
        'maxRunning': 0, 'maxInstances': 0, 'minFreeBytes': 4 * 1024**3}


async def test_shutdown_holds_setup_ownership_until_inflight_thread_settles(app, monkeypatch):
    import threading
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    def slow_effect():
        loop.call_soon_threadsafe(entered.set)
        release.wait(5)
    async def backend(base, selected, owned, record, step):
        await step('checking_profile', 'Checking')
        await setup.settled_thread(slow_effect)
        return base, {'probeRemoved': True}
    monkeypatch.setattr(setup, 'setup_docker', backend)
    await setup.request(app, args(), {})
    await entered.wait()
    closing = asyncio.create_task(setup.close(app))
    await asyncio.sleep(0)
    assert not closing.done()
    with pytest.raises(ValueError, match='already in progress'):
        await setup.request(app, args(requestId='new'), {})
    release.set()
    await closing
    result = setup.read_json(setup.root(app) / (setup.read_json(setup.root(app) / 'latest.json')['id'] + '.json'))
    assert result['state'] == 'outcome_unknown'


async def test_existing_config_needs_real_base_probe_without_configuration_change(app, monkeypatch):
    config = {'enabled': True, 'backend': 'docker'}
    setup.write_json(app.data_dir / 'test-environments.json', config)
    monkeypatch.setattr(owner, 'configuration', lambda _: (config, 'ready', 'Configured'))
    monkeypatch.setattr(owner, 'invoke', lambda *a: {'ok': True, 'capacity': {'blockers': []}})
    before = await setup.readiness(app)
    assert before['status'] == 'configured' and before['backendReachable']
    verify = AsyncMock(return_value={'probeRemoved': True, 'offline': True})
    monkeypatch.setattr(setup, 'verify_existing', verify)
    await setup.request(app, args(), {})
    result = await settle(app)
    assert result['state'] == 'completed'
    verify.assert_awaited_once()
    app.smart_tools.install.assert_not_called()
    assert setup.read_json(app.data_dir / 'test-environments.json') == config
    assert (await setup.readiness(app))['status'] == 'ready'


def test_unknown_probe_admission_is_not_settled_by_absence(app, monkeypatch):
    record = {'id': 'a' * 32, 'backend': 'docker'}
    folder = setup.root(app) / record['id']
    folder.mkdir(mode=0o700)
    setup.write_json(folder / 'probe.json', {'name': 'owned', 'dockerHost': 'unix:///owned.sock', 'daemon': 'original'})
    setup.write_json(folder / 'effect-docker-probe.json', {'kind': 'probe', 'state': 'outcome_unknown'})
    monkeypatch.setattr(setup, 'executable', lambda _: '/usr/bin/docker')
    monkeypatch.setattr(setup, 'command', lambda argv, **kw: '{"ID":"original"}' if 'info' in argv else '')
    with pytest.raises(ValueError, match='Absence alone'):
        setup.observe_partial(app, record)
    # Explicit cleanup may close absence only after this exact owned probe was
    # physically observed and then removed, not from an empty inventory alone.
    record['cleanup'] = {'observedResources': ['probe']}
    assert setup.observe_partial(app, record)['absent']


@pytest.mark.parametrize('installed_version', ['0.4.3', '0.4.2', '0.4.1'])
async def test_incus_setup_uses_exact_remote_image_and_supported_query_scope(app, monkeypatch, tmp_path, installed_version):
    from amplifier_web import incus_environment_worker as worker
    fingerprint, daemon = 'a' * 64, 'b' * 64
    calls = []
    monkeypatch.setattr(setup.shutil, 'which', lambda _: '/usr/bin/uv')
    monkeypatch.setattr(worker, 'package_digest', lambda _: worker.QUALIFIED_PACKAGES['0.4.3'])
    monkeypatch.setattr(worker, 'file_digest', lambda *a: ('c' * 64, 10))
    monkeypatch.setattr(worker, 'host_target', lambda *a: {'supported': True, 'machineId': 'fixture'})
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == '/usr/bin/uv':
            return ''
        if '-I' in argv:
            return json.dumps({'directory': '/approved/package', 'version': installed_version})
        if 'query' in argv:
            assert '--project' not in argv, 'Incus query rejects the global project flag'
            if '/1.0/instances/' in argv[-1]:
                assert '?project=unified-' in argv[-1]
                return '{"expanded_devices":{}}'
            return json.dumps({'environment': {'certificate_fingerprint': daemon, 'architectures': ['aarch64']}})
        if any(value.startswith('unified-images:') for value in argv):
            assert '--force-local' not in argv, 'Force-local discards private image remotes'
            if 'list' in argv:
                return json.dumps([{'fingerprint': fingerprint, 'type': 'container', 'architecture': 'aarch64'}, {'fingerprint': 'd' * 64, 'type': 'virtual-machine', 'architecture': 'aarch64'}])
            assert 'unified-images:' + fingerprint in argv and 'local:' in argv
            return ''
        if 'image' in argv and 'list' in argv:
            return json.dumps([{'fingerprint': fingerprint, 'cached': False, 'auto_update': False}])
        if 'storage' in argv:
            return '[{"name":"existing-pool","status":"Created"}]'
        if 'exec' in argv:
            return '{"python":"3.12.3"}'
        if 'list' in argv:
            return '[]'
        return ''
    monkeypatch.setattr(setup, 'command', run)
    record = {'id': 'f' * 32}
    async def step(phase, detail):
        record.update(phase=phase, detail=detail)
    if installed_version != '0.4.3':
        with pytest.raises(ValueError, match='approved package'):
            await setup.setup_incus({'backend': 'incus'}, {'executable': '/usr/bin/incus'}, tmp_path, record, step)
        assert not any(call[0] == '/usr/bin/incus' for call in calls)
        return
    config, proof = await setup.setup_incus({'backend': 'incus'}, {'executable': '/usr/bin/incus'}, tmp_path, record, step)
    assert config['incus']['image'] == fingerprint
    assert config['incus']['maxRunning'] == config['incus']['maxInstances'] == 0
    assert config['incus']['packageSha256'] == worker.QUALIFIED_PACKAGES['0.4.3']
    install = next(call for call in calls if 'install' in call)
    assert install[-1] == 'git+' + setup.LEGACY_REPOSITORY + '@' + setup.LEGACY_COMMIT
    assert config['incus']['preparationStorage'] == 'existing-pool'
    assert proof['probeRemoved'] and proof['offline']
    assert len([call for call in calls if 'copy' in call]) == 1
    profile = next(call for call in calls if 'profile' in call and 'device' in call)
    assert profile[profile.index('--project') + 1] == 'unified-' + record['id'][:20]
    assert profile[-2:] == ['path=/', 'pool=existing-pool']


async def test_explicit_same_setup_can_publish_verified_candidate_without_replay(app, monkeypatch):
    import hashlib
    identity = hashlib.sha256(json.dumps(['session', 'setup'], separators=(',', ':')).encode()).hexdigest()[:32]
    candidate = {'enabled': True, 'backend': 'docker'}
    record = {'id': identity, 'intent': args(), 'actor': {}, 'backend': 'docker', 'state': 'outcome_unknown',
              'priorConfiguration': None, 'candidateConfiguration': candidate, 'proof': {'probeRemoved': True, 'daemon': 'original'}}
    setup.write_json(setup.root(app) / (identity + '.json'), record)
    monkeypatch.setattr(owner, 'invoke', lambda *a: {'ok': True, 'capacity': {'scope': {'daemon': 'original'}, 'blockers': []}})
    result = await setup.request(app, args(), {})
    assert result['operation']['state'] == 'completed'
    assert setup.read_json(app.data_dir / 'test-environments.json') == candidate
    app.smart_tools.install.assert_not_called()


async def test_retained_setup_handles_capacity_denial_without_publishing(app, monkeypatch):
    import hashlib
    identity = hashlib.sha256(json.dumps(['session', 'setup'], separators=(',', ':')).encode()).hexdigest()[:32]
    candidate = {'enabled': True, 'backend': 'docker'}
    record = {'id': identity, 'intent': args(), 'actor': {}, 'backend': 'docker', 'state': 'outcome_unknown',
              'priorConfiguration': None, 'candidateConfiguration': candidate, 'proof': {'probeRemoved': True, 'daemon': 'original'}}
    setup.write_json(setup.root(app) / (identity + '.json'), record)
    monkeypatch.setattr(owner, 'invoke', lambda *a: {'ok': False, 'capacity': None, 'code': 'storage_unknown'})
    with pytest.raises(ValueError, match='backend identity could not be confirmed'):
        await setup.request(app, args(), {})
    assert not (app.data_dir / 'test-environments.json').exists()
    app.smart_tools.install.assert_not_called()


@pytest.mark.parametrize('state', ['recovered', 'outcome_unknown'])
async def test_cleaned_setup_cannot_republish_retained_candidate(app, monkeypatch, state):
    import hashlib
    identity = hashlib.sha256(json.dumps(['session', 'setup'], separators=(',', ':')).encode()).hexdigest()[:32]
    candidate = {'enabled': True, 'backend': 'docker'}
    record = {'id': identity, 'intent': args(), 'actor': {}, 'backend': 'docker', 'state': state, 'cleanup': {'state': 'failed'},
              'priorConfiguration': None, 'candidateConfiguration': candidate, 'proof': {'probeRemoved': True}}
    setup.write_json(setup.root(app) / (identity + '.json'), record)
    complete = AsyncMock()
    monkeypatch.setattr(setup, 'complete_retained', complete)
    result = await setup.request(app, args(), {})
    assert result['operation']['state'] == state
    complete.assert_not_awaited()
    assert not (app.data_dir / 'test-environments.json').exists()


def test_completion_capability_excludes_cleaned_or_unproven_setup():
    record = {'intent': args(), 'state': 'outcome_unknown', 'candidateConfiguration': {'enabled': True}, 'proof': {'probeRemoved': True}}
    assert setup.public(record)['canComplete']
    record['cleanup'] = {'state': 'completed'}
    assert not setup.public(record)['canComplete']
    del record['cleanup']
    record['proof']['probeRemoved'] = False
    assert not setup.public(record)['canComplete']
