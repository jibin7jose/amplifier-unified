"""Prepared dependency provenance, confinement and interruption contracts."""
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import threading
import time

import pytest

from amplifier_web import test_environment_initialize_guest as initializer
from amplifier_web import test_environment_preparation as preparation
from amplifier_web import test_environment_profiles as profiles


def archive(root, files=None):
    files = files or {'pyproject.toml': '[project]\nname="fixture"\nversion="1"', 'uv.lock': 'version=1'}
    path = root / 'source.tar'
    with tarfile.open(path, 'w') as stream:
        for name, value in files.items():
            value = value.encode()
            item = tarfile.TarInfo(name)
            item.size = len(value)
            stream.addfile(item, io.BytesIO(value))
    return {'archive_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def config(root):
    return {'backend': 'docker', 'image': 'python@sha256:' + '1' * 64,
            'dockerHost': 'unix:///fixture.sock', 'admissionRoot': str(root / 'admission')}


@pytest.fixture
def backend(monkeypatch, tmp_path):
    identity = {'backend': 'docker', 'daemon': 'fixture'}
    (tmp_path / 'backend-binding.json').write_text(json.dumps({'backendIdentity': identity}))
    monkeypatch.setattr(preparation, 'backend_identity', lambda *args: identity)
    monkeypatch.setattr(preparation, 'memory_available', lambda *args: True)
    def build(config, folder, plan, env):
        return 'sha256:' + '2' * 64, json.dumps({'preparationId': plan['preparationId'], 'sourceSha256': plan['sourceSha256'], 'executionContract': {**plan['executionContract'], 'pythonBuild': {'available': False, 'reason': 'This source declares no Python build backend.'}}})
    monkeypatch.setattr(preparation, '_docker', build)
    return identity


def test_profiles_preserve_small_check_and_fit_browser_workload():
    assert profiles.get()['maxTimeoutSeconds'] == 300
    browser = profiles.get('browser-test')
    assert browser['defaultTimeoutSeconds'] > 11 * 60
    assert browser['memoryMiB'] >= 4096 and browser['shmMiB'] >= 1024
    assert browser['environment']['HOME'] == '/tmp/unified-test/home'
    assert browser['environment']['XDG_CACHE_HOME'] == '/tmp/unified-test/cache'
    assert browser['environment']['AMPLIFIER_TEST_ARTIFACTS'] == '/artifacts'
    browser['environment']['PATH'] = 'mutated'
    assert profiles.get('python-project')['environment']['PATH'] != 'mutated'


@pytest.mark.parametrize('value', [0, 1801, True, '1200'])
def test_invalid_time_budgets_rejected(value):
    with pytest.raises(ValueError):
        profiles.validate_timeout('browser-test', value)


def test_plan_binds_source_locks_and_fixed_recipe(tmp_path):
    snapshot = archive(tmp_path)
    value = preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project')
    assert value['sourceSha256'] == snapshot['archive_sha256']
    assert [item['path'] for item in value['lockfiles']] == ['pyproject.toml', 'uv.lock']
    assert len(value['recipeSha256']) == 64
    assert preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project') == value
    with pytest.raises(ValueError, match='changed'):
        preparation.plan(tmp_path / 'source.tar', {'archive_sha256': 'wrong'}, 'python-project')


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'frontend/node_modules/lib.js', '.venv/bin/python', '.unified-prepared-dependencies.json'])
def test_untrusted_archive_layout_rejected(tmp_path, name):
    snapshot = archive(tmp_path, {'pyproject.toml': 'project', 'uv.lock': 'lock', name: 'data'})
    with pytest.raises(ValueError):
        preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project')


def test_browser_requires_playwright_in_committed_lock(tmp_path):
    snapshot = archive(tmp_path, {'pyproject.toml': 'project', 'uv.lock': 'lock', 'frontend/package.json': '{}',
        'frontend/package-lock.json': json.dumps({'packages': {'node_modules/playwright': {'version': '1.63.0'}}})})
    value = preparation.plan(tmp_path / 'source.tar', snapshot, 'browser-test')
    assert value['nodeDirectory'] == 'frontend' and value['playwrightVersion'] == '1.63.0'
    assert len(value['lockfiles']) == 4
    snapshot = archive(tmp_path)
    with pytest.raises(ValueError, match='Playwright'):
        preparation.plan(tmp_path / 'source.tar', snapshot, 'browser-test')


def test_node_only_browser_project_does_not_require_a_dummy_python_project(tmp_path):
    snapshot = archive(tmp_path, {'package.json': '{}',
        'package-lock.json': json.dumps({'packages': {'node_modules/playwright': {'version': '1.63.0'}}})})
    value = preparation.plan(tmp_path / 'source.tar', snapshot, 'browser-test')
    assert value['pythonProject'] is False and value['nodeDirectory'] == ''
    assert len(value['lockfiles']) == 2


def test_prepare_retains_exact_image_and_never_rebuilds_ready_request(tmp_path, backend, monkeypatch):
    snapshot = archive(tmp_path)
    receipt = preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request')
    assert receipt['image'] == 'sha256:' + '2' * 64
    assert not (tmp_path / 'preparation/context').exists()
    monkeypatch.setattr(preparation, '_docker', lambda *args: pytest.fail('unexpected replay'))
    assert preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request') == receipt
    with pytest.raises(ValueError, match='different'):
        preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'another')


def test_interrupted_build_is_not_automatically_replayed(tmp_path, backend, monkeypatch):
    snapshot = archive(tmp_path)
    def fail(*args):
        raise TimeoutError('interrupted')
    monkeypatch.setattr(preparation, '_docker', fail)
    with pytest.raises(TimeoutError):
        preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request')
    monkeypatch.setattr(preparation, '_docker', lambda *args: pytest.fail('unexpected replay'))
    with pytest.raises(ValueError, match='unfinished'):
        preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request')
    assert json.loads((tmp_path / 'preparation/status.json').read_text())['status'] == 'unknown'


def test_changed_backend_refuses_preparation(tmp_path, backend, monkeypatch):
    snapshot = archive(tmp_path)
    monkeypatch.setattr(preparation, 'backend_identity', lambda *args: {'backend': 'docker', 'daemon': 'another'})
    with pytest.raises(ValueError, match='backend changed'):
        preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request')


def test_host_credentials_and_proxy_config_are_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic')
    monkeypatch.setenv('HTTPS_PROXY', 'synthetic')
    monkeypatch.setenv('DOCKER_CONFIG', '/private/host/config')
    value = preparation._environment(tmp_path, config(tmp_path))
    assert not {'OPENAI_API_KEY', 'HTTPS_PROXY', 'AWS_SECRET_ACCESS_KEY'} & value.keys()
    assert value['DOCKER_CONFIG'] == str(tmp_path / 'docker-config')
    assert value['HOME'] == str(tmp_path)


@pytest.mark.parametrize('stopped', [True, False])
def test_cancelled_only_after_builder_stop_confirmation(tmp_path, backend, monkeypatch, stopped):
    snapshot = archive(tmp_path)
    def cancel(*args):
        raise preparation.PreparationCancelled()
    monkeypatch.setattr(preparation, '_docker', cancel)
    monkeypatch.setattr(preparation, 'stop_builder', lambda *args: stopped)
    with pytest.raises(ValueError):
        preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request')
    value = json.loads((tmp_path / 'preparation/status.json').read_text())
    assert value['status'] == ('cancelled' if stopped else 'unknown')


def test_real_wait_observes_cancellation_promptly_and_bounds_log(tmp_path):
    def cancel():
        time.sleep(0.2)
        preparation.write(tmp_path / 'cancel.json', {'cancelRequested': True})
    thread = threading.Thread(target=cancel)
    thread.start()
    start = time.monotonic()
    with pytest.raises(preparation.PreparationCancelled):
        preparation.run([sys.executable, '-c', 'import time; print("x"*200000, flush=True); time.sleep(20)'],
                        {'PATH': '/usr/bin:/bin'}, tmp_path / 'build.log')
    thread.join()
    assert time.monotonic() - start < 3
    assert (tmp_path / 'build.log').stat().st_size <= preparation.LOG_BYTES


def test_initializer_copies_writable_node_modules_and_rejects_conflicting_source(tmp_path):
    prefix, workspace, artifacts = [tmp_path / name for name in ('prefix', 'workspace', 'artifacts')]
    for folder in (prefix / 'node/frontend', workspace / 'frontend'):
        folder.mkdir(parents=True)
    (prefix / 'node/frontend/dependency.js').write_text('dependency')
    (prefix / 'plan.json').write_text(json.dumps({'profileId': 'browser-test', 'preparationId': 'exact', 'nodeDirectory': 'frontend', 'nodeKey': 'frontend'}))
    initializer.initialize(prefix, workspace)
    assert not artifacts.exists()
    target = workspace / 'frontend/node_modules'
    assert not target.is_symlink() and (target / 'dependency.js').read_text() == 'dependency'
    (target / 'output').write_text('writable')
    initializer.initialize(prefix, workspace)
    (workspace / '.unified-prepared-dependencies.json').unlink()
    with pytest.raises(ValueError, match='already supplies'):
        initializer.initialize(prefix, workspace)


@pytest.mark.parametrize('failure', [False, True])
def test_initializer_prepares_selected_client_after_full_source_before_completion(tmp_path, failure):
    prefix, workspace = tmp_path / 'prefix', tmp_path / 'workspace'
    for folder in (prefix / 'node/frontend', prefix / 'venv/bin', prefix / 'node-runtime/bin',
                   workspace / 'frontend', workspace / 'components', workspace / 'scripts'):
        folder.mkdir(parents=True)
    (prefix / 'venv/bin/python').symlink_to(sys.executable)
    (prefix / 'node/frontend/dependency.js').write_text('dependency')
    (prefix / 'plan.json').write_text(json.dumps({'profileId': 'browser-test', 'preparationId': 'exact',
                                                'nodeDirectory': 'frontend', 'nodeKey': 'frontend'}))
    (workspace / 'components/web-client.source.json').write_text('{}')
    # A real subprocess verifies ordering and the prepared runtime environment,
    # without importing or executing a project during dependency-only seeding.
    (workspace / 'scripts/web_client_development.py').write_text(
        'import os, sys\nfrom pathlib import Path\n'
        'assert sys.argv[1:] == ["prepare-view"]\n'
        'assert Path("components/web-client.source.json").is_file()\n'
        'assert not Path("frontend/node_modules").exists()\n'
        'assert not Path(".unified-prepared-dependencies.json").exists()\n'
        f'assert os.environ["PATH"].split(os.pathsep)[0] == {str(prefix / "node-runtime/bin")!r}\n'
        f'sys.exit(3) if {failure!r} else None\n'
        'Path("frontend/src").mkdir()\n'
        'Path("frontend/src/selected.js").write_text("selected component")\n')
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            initializer.initialize(prefix, workspace)
        assert not (workspace / '.unified-prepared-dependencies.json').exists()
        assert not (workspace / 'frontend/node_modules').exists()
    else:
        initializer.initialize(prefix, workspace)
        assert (workspace / 'frontend/src/selected.js').read_text() == 'selected component'
        assert (workspace / 'frontend/node_modules/dependency.js').read_text() == 'dependency'
        initializer.initialize(prefix, workspace)  # Completed initialization is idempotent.


def test_python_project_does_not_run_client_preparation(tmp_path):
    prefix, workspace = tmp_path / 'prefix', tmp_path / 'workspace'
    prefix.mkdir()
    (workspace / 'components').mkdir(parents=True)
    (workspace / 'components/web-client.source.json').write_text('{}')
    (prefix / 'plan.json').write_text(json.dumps({'profileId': 'python-project', 'preparationId': 'exact'}))
    initializer.initialize(prefix, workspace)
    assert (workspace / '.unified-prepared-dependencies.json').is_file()


def test_docker_builder_has_bounded_resources_no_mounts_and_immutable_commit(tmp_path, monkeypatch):
    folder = tmp_path / 'preparation'
    (folder / 'context').mkdir(parents=True)
    commands = []
    def call(arguments, *args, **kwargs):
        commands.append(arguments)
        return 'sha256:' + '3' * 64 if 'commit' in arguments else '{}'
    monkeypatch.setattr(preparation, 'run', call)
    image, _ = preparation._docker(config(tmp_path), folder, {'baseImage': config(tmp_path)['image'], 'resourceName': 'owned', 'profileId': 'python-project'}, {})
    assert image == 'sha256:' + '3' * 64
    create = next(row for row in commands if 'create' in row)
    assert '--memory' in create and '--pids-limit' in create and '--cap-drop' in create
    assert not {'--volume', '--mount', '-v', '--privileged', '--env-file'} & set(create)
    assert ['stop', '--time', '5', 'owned'] == commands[-2][3:]
    assert ['rm', 'owned'] == commands[-1][3:]


def test_cleanup_refuses_changed_image_owner(tmp_path, backend, monkeypatch):
    snapshot = archive(tmp_path)
    preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request')
    commands = []
    def call(arguments, *args, **kwargs):
        commands.append(arguments)
        if 'ls' in arguments:
            return 'sha256:' + '2' * 64
        return json.dumps([{'Id': 'sha256:' + '2' * 64, 'Config': {'Labels': {'io.amplifier.preparation': 'foreign'}}}])
    monkeypatch.setattr(preparation, 'run', call)
    assert not preparation.cleanup(config(tmp_path), tmp_path)['complete']
    assert not any('rm' in command for command in commands)


def test_cleanup_recovers_missing_receipt_after_image_was_removed(tmp_path, backend, monkeypatch):
    snapshot = archive(tmp_path)
    preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request')
    commands = []
    def call(arguments, *args, **kwargs):
        commands.append(arguments)
        return 'sha256:' + 'a' * 64 + '\n'
    monkeypatch.setattr(preparation, 'run', call)
    assert preparation.cleanup(config(tmp_path), tmp_path)['complete']
    assert not any('rm' in command for command in commands)


def test_bounded_control_output_cannot_claim_absence_after_truncation(tmp_path):
    with pytest.raises(ValueError, match='observation limit'):
        preparation.run([sys.executable, '-c', 'print("a" * 200000)'],
                        {'PATH': '/usr/bin:/bin'}, tmp_path / 'inspection.log', require_complete=True)


def test_malformed_cleanup_record_retains_uncertainty(tmp_path):
    folder = tmp_path / 'preparation'
    folder.mkdir()
    (folder / 'receipt.json').write_text('{')
    assert preparation.cleanup(config(tmp_path), tmp_path) == {
        'complete': False, 'status': 'unknown',
        'reason': 'The owned dependency preparation could not be inspected or removed; retained source and recovery records are unchanged.'}


def test_small_host_memory_accounts_for_existing_container_usage(tmp_path, monkeypatch):
    original_path = preparation.Path
    meminfo = tmp_path / 'meminfo'
    meminfo.write_text('MemTotal:       8388608 kB\nMemAvailable:   7340032 kB\n')
    monkeypatch.setattr(preparation, 'Path', lambda value: meminfo if value == '/proc/meminfo' else original_path(value))
    usage = ['3.5GiB / 8GiB']
    def call(arguments, *args, **kwargs):
        return json.dumps({'MemTotal': 8 * 1024 ** 3}) if 'info' in arguments else '\n'.join(usage)
    monkeypatch.setattr(preparation, 'run', call)
    assert not preparation.memory_available(config(tmp_path), tmp_path, 'python-project')
    usage[:] = ['1.0GiB / 8GiB']
    assert preparation.memory_available(config(tmp_path), tmp_path, 'python-project')


def test_incus_observations_scope_query_uri_without_unsupported_project_flag(tmp_path, monkeypatch):
    original_path = preparation.Path
    meminfo = tmp_path / 'meminfo'
    meminfo.write_text('MemTotal:       8388608 kB\nMemAvailable:   7340032 kB\n')
    monkeypatch.setattr(preparation, 'Path', lambda value: meminfo if value == '/proc/meminfo' else original_path(value))
    settings = {'backend': 'incus', 'incus': {'incusExecutable': '/qualified/incus',
        'project': 'owned-project', 'configDir': str(tmp_path), 'daemonFingerprint': 'exact-daemon'}}
    observed = []
    def query(arguments, *args, **kwargs):
        # Emulate the actual CLI contract that rejected the former invocation.
        assert '--project' not in arguments
        assert arguments[:2] == ['/qualified/incus', 'query']
        assert arguments[2].endswith('?project=owned-project')
        observed.append(arguments[2])
        return json.dumps({'memory': {'total': 8 * 1024 ** 3, 'used': 1024 ** 3}} if '/resources?' in arguments[2]
                          else {'environment': {'project': 'owned-project', 'certificate_fingerprint': 'exact-daemon'}})
    monkeypatch.setattr(preparation, 'run', query)
    assert preparation.backend_identity(settings, tmp_path, {}) == {
        'backend': 'incus', 'daemon': 'exact-daemon', 'project': 'owned-project'}
    assert preparation.memory_available(settings, tmp_path, 'python-project')
    assert observed == ['/1.0?project=owned-project', '/1.0/resources?project=owned-project']


def test_incus_publish_uses_positional_ownership_property_after_disconnect(tmp_path, monkeypatch):
    folder = tmp_path / 'preparation'
    (folder / 'context').mkdir(parents=True)
    config = {'backend': 'incus', 'incus': {'incusExecutable': '/qualified/incus',
        'project': 'owned-project', 'image': 'b' * 64, 'preparationNetwork': 'owned-network', 'preparationStorage': 'pool'}}
    commands = []
    def call(arguments, *args, **kwargs):
        commands.append(arguments)
        assert '--property' not in arguments  # Not part of the installed CLI contract.
        return json.dumps([{'fingerprint': 'c' * 64}]) if 'image' in arguments else '{}'
    monkeypatch.setattr(preparation, 'run', call)
    image, _ = preparation._incus(config, folder, {'resourceName': 'owned-builder', 'profileId': 'python-project'}, {})
    assert image == 'c' * 64
    publish = next(row for row in commands if 'publish' in row)
    assert publish[3:] == ['publish', 'owned-builder', '--alias', 'owned-builder', 'user.amplifier.preparation=owned-builder']
    disconnect = next(row for row in commands if 'remove' in row)
    assert commands.index(disconnect) < commands.index(publish)


def test_preparation_uses_frozen_total_build_deadline_and_resource_override(tmp_path, monkeypatch):
    folder = tmp_path / 'preparation'
    (folder / 'context').mkdir(parents=True)
    calls, clock = [], [10.0]
    monkeypatch.setattr(preparation.time, 'monotonic', lambda: clock[0])
    def call(arguments, *args, **kwargs):
        calls.append((arguments, kwargs['timeout']))
        clock[0] += 1000
        return 'sha256:' + '3' * 64 if 'commit' in arguments else '{}'
    monkeypatch.setattr(preparation, 'run', call)
    frozen = profiles.freeze_timing('python-project', {'preparationTimeoutSeconds': 2500})
    expected = {'baseImage': config(tmp_path)['image'], 'resourceName': 'owned', 'profileId': 'python-project',
        'timingRequest': frozen, 'resourceRequest': profiles.freeze('python-project', {'cpus': 5, 'memoryMiB': 8192})}
    with pytest.raises(TimeoutError, match='time limit'):
        preparation._docker(config(tmp_path), folder, expected, {})
    assert [seconds for _, seconds in calls] == [2500, 1500, 500]
    assert '8192m' in calls[0][0] and '5' in calls[0][0]


def test_timing_policy_does_not_change_dependency_recipe_identity(tmp_path, backend):
    snapshot = archive(tmp_path)
    before = preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project')
    timing = profiles.freeze_timing('python-project', {'preparationTimeoutSeconds': 4000})
    receipt = preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request', timing_request=timing)
    assert receipt['intent']['timingRequest'] == timing
    assert receipt['intent']['preparationId'] == before['preparationId']
    assert receipt['recipeSha256'] == before['recipeSha256']


def test_builder_wait_consumes_same_preparation_deadline(tmp_path, backend, monkeypatch):
    from contextlib import contextmanager
    snapshot = archive(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(preparation.time, 'monotonic', lambda: clock[0])
    @contextmanager
    def delayed_slot(*args, **kwargs):
        assert kwargs['seconds'] == 2000
        clock[0] += 1500
        yield
    monkeypatch.setattr(preparation, 'preparation_slot', delayed_slot)
    def build(config, folder, expected, env):
        assert expected['_deadline'] - clock[0] == 500
        return 'sha256:' + '2' * 64, json.dumps({'preparationId': expected['preparationId'], 'sourceSha256': expected['sourceSha256'], 'executionContract': {**expected['executionContract'], 'pythonBuild': {'available': False, 'reason': 'This source declares no Python build backend.'}}})
    monkeypatch.setattr(preparation, '_docker', build)
    timing = profiles.freeze_timing('python-project', {'preparationTimeoutSeconds': 2000})
    receipt = preparation.prepare(config(tmp_path), tmp_path, snapshot, 'python-project', 'request', timing_request=timing)
    assert '_deadline' not in receipt['intent']
