"""Subprocess checks for the browser fixture's isolated metadata boundary."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest
import yaml


SOURCE = Path(__file__).resolve().parents[1]
FIXTURE = SOURCE / 'tests/fixtures/provider_native_ui_server.py'
DRIVER = '''
import importlib.util, json, os, subprocess, sys
from pathlib import Path
fixture_path, source, root, operation, backend = sys.argv[1:]
root = Path(root)
if backend == 'fork-exec': subprocess._USE_POSIX_SPAWN = False
spawn_events = []
def observe_spawn(event, args):
    if event in {'subprocess.Popen', 'os.posix_spawn'}:
        spawn_events.append(event)
        (root / 'launch-events.json').write_text(json.dumps(spawn_events))
sys.addaudithook(observe_spawn)
if operation.startswith('changed-spawn-'):
    original_spawn = os.posix_spawn
    def changed_spawn(executable, argv, environment, **kwargs):
        if operation == 'changed-spawn-environment':
            environment = {**environment, 'OPENAI_API_KEY': 'synthetic-secret'}
        if operation == 'changed-spawn-argument': argv = [*argv, 'unapproved']
        if operation == 'changed-spawn-executable': executable = '/bin/true'
        return original_spawn(executable, argv, environment, **kwargs)
    os.posix_spawn = changed_spawn
spec = importlib.util.spec_from_file_location('provider_native_ui_server', fixture_path)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
application, policy = fixture.package_origin(root)
sys.path.insert(0, source)
from amplifier_web import decision_accounts
if operation in {'changed-helper', 'wrong-installed-origin'}:
    helper = root / 'decision_account_catalog.py'
    helper.write_bytes((application / helper.name).read_bytes())
    policy['accountCatalog'] = fixture.file_identity(helper)
    if operation == 'wrong-installed-origin':
        policy.update(mode='installed', applicationInstallRoot=source,
            applicationPackages=['amplifier_web'], dependencyPrefixes=[sys.prefix],
            wheelFiles={'amplifier_web/decision_account_catalog.py':
                        fixture.file_identity(application / helper.name)})
        sys.argv[0] = fixture_path
    fixture.save(root / 'origin-policy.json', policy)
fixture.install_guard(root)
if operation == 'catalog' or operation.startswith('changed-spawn-'):
    print(json.dumps(decision_accounts.catalogs()))
else:
    command = [sys.executable, '-I', policy['accountCatalog']['path']]
    environment = {'LANG': 'C.UTF-8'}
    if operation == 'credentials': environment['OPENAI_API_KEY'] = 'synthetic-secret'
    if operation == 'pythonpath': environment['PYTHONPATH'] = source
    if operation == 'extra-argument': command.append('unapproved')
    if operation == 'other-program': command = [sys.executable, '-c', 'raise SystemExit(0)']
    if operation == 'direct-bootstrap': command = [sys.executable, '-I', fixture_path, '--account-catalog', str(root)]
    if operation == 'direct-posix-bootstrap':
        command = [sys.executable, '-I', fixture_path, '--account-catalog', str(root)]
        os.posix_spawn(command[0], command, environment)
        raise AssertionError('Direct spawn must not reach the helper')
    if operation == 'changed-helper':
        helper.write_text('raise SystemExit("changed helper must not run")')
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment, check=True)
'''
METADATA = '''
def catalog():
    return {'schema_version': 2, 'validate': lambda value: None,
            'provider_module': 'provider-catalog-fixture',
            'backends': [{'decision_only': True}],
            'runtime': {'kind': 'hook', 'module': 'hooks-catalog-fixture'}}
'''


@pytest.fixture
def isolated_catalog(tmp_path):
    if sys.platform != 'linux':
        pytest.skip('The native browser fixture requires Linux process identity')
    prefix = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
    python = prefix / 'bin/python'
    site = prefix / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
    # Only the parent's account caller imports YAML; do not expose the prepared
    # environment's unrelated decision entrypoints to this isolated catalog.
    (site / 'yaml').symlink_to(Path(yaml.__file__).parent, target_is_directory=True)
    metadata = site / 'catalog_fixture-1.0.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: catalog-fixture\nVersion: 1.0\n')
    (metadata / 'entry_points.txt').write_text('[amplifier.decision_backends]\nfixture = catalog_fixture:catalog\n')
    module = site / 'catalog_fixture.py'
    module.write_text(METADATA)
    driver = tmp_path / 'driver.py'
    driver.write_text(DRIVER)
    root = tmp_path / 'evidence'
    root.mkdir()
    # A hostile cwd/PYTHONPATH module must never replace installed metadata.
    shadow = tmp_path / 'catalog_fixture.py'
    shadow.write_text('raise RuntimeError("unapproved source import")\n')

    def run(operation='catalog', backend='default'):
        return subprocess.run([str(python), str(driver), str(FIXTURE), str(SOURCE), str(root), operation, backend],
            cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(tmp_path),
                               'OPENAI_API_KEY': 'synthetic-parent-secret'},
            capture_output=True, text=True, timeout=20)
    return run, root, module


def records(root, pattern):
    return [json.loads(line) for path in root.glob(pattern) for line in path.read_text().splitlines()]


@pytest.mark.parametrize('backend', ['default', 'fork-exec'])
def test_real_catalog_keeps_isolation_metadata_and_reaping(isolated_catalog, backend):
    run, root, _ = isolated_catalog
    result = run(backend=backend)
    assert result.returncode == 0, result.stderr
    events = json.loads((root / 'launch-events.json').read_text())
    expected_spawn = (backend == 'default' and subprocess._USE_POSIX_SPAWN
                      and getattr(subprocess, '_HAVE_POSIX_SPAWN_CLOSEFROM', False))
    assert events == (['subprocess.Popen', 'os.posix_spawn'] if expected_spawn else ['subprocess.Popen'])
    assert json.loads(result.stdout) == {'provider-catalog-fixture': 'hooks-catalog-fixture'}
    spawned, reaped = records(root, 'account-catalog-*.jsonl')
    assert spawned['event'] == 'spawned' and reaped['event'] == 'reaped'
    assert reaped['returncode'] == 0 and reaped['pid'] == spawned['pid']
    assert not Path(f'/proc/{spawned["pid"]}').exists()
    guard = json.loads((root / f'guard-ready-{spawned["pid"]}.json').read_text())
    assert guard['isolated'] and guard['allowedCommands'] == []
    assert 'PYTHONPATH' not in guard['environmentNames']
    assert 'OPENAI_API_KEY' not in guard['environmentNames']
    loaded = json.loads((root / f'loaded-{spawned["pid"]}.json').read_text())
    assert loaded['script']['sha256'] == hashlib.sha256((SOURCE / 'amplifier_web/decision_account_catalog.py').read_bytes()).hexdigest()
    assert loaded['violations'] == [] and not loaded.get('error')


@pytest.mark.parametrize('operation', ['credentials', 'pythonpath', 'extra-argument', 'other-program', 'direct-bootstrap', 'direct-posix-bootstrap'])
def test_catalog_exception_does_not_admit_other_launches(isolated_catalog, operation):
    run, root, _ = isolated_catalog
    result = run(operation)
    assert result.returncode != 0
    assert 'Unexpected isolated account catalog launch contract' in result.stderr or 'unlisted subprocess' in result.stderr
    assert records(root, 'account-catalog-*.jsonl') == []


@pytest.mark.parametrize('operation', ['changed-helper', 'wrong-installed-origin'])
def test_changed_or_out_of_install_helper_is_not_executed(isolated_catalog, operation):
    run, root, module = isolated_catalog
    effect = root / 'unapproved-metadata-import'
    module.write_text(f'from pathlib import Path\nPath({str(effect)!r}).touch()\n' + METADATA)
    result = run(operation)
    assert result.returncode != 0
    if operation == 'changed-helper':
        assert 'source identity changed' in result.stderr
        assert records(root, 'account-catalog-*.jsonl') == []
    else:
        spawned, reaped = records(root, 'account-catalog-*.jsonl')
        assert reaped['returncode'] != 0
        assert not Path(f'/proc/{spawned["pid"]}').exists()
        refusal, = records(root, 'origin-refused-*.jsonl')
        assert 'Checkout application import refused' in refusal['error']
    assert not effect.exists()


@pytest.mark.parametrize('operation', ['network', 'subprocess'])
def test_real_metadata_cannot_use_network_or_spawn_children(isolated_catalog, operation):
    run, root, module = isolated_catalog
    effect = root / 'unapproved-effect'
    if operation == 'network':
        body = "import socket; socket.create_connection(('198.51.100.1', 9), timeout=0.1)"
    else:
        body = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {('from pathlib import Path; Path(' + repr(str(effect)) + ').touch()')!r}])"
    module.write_text('def catalog():\n    ' + body + '\n')
    result = run()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}  # The real helper drops failed metadata.
    assert not effect.exists()
    refusal = records(root, 'guard-*.jsonl')
    assert len(refusal) == 1
    assert refusal[0]['event'] == ('socket.getaddrinfo' if operation == 'network' else 'subprocess.Popen')


@pytest.mark.skipif(not (subprocess._USE_POSIX_SPAWN and getattr(subprocess, '_HAVE_POSIX_SPAWN_CLOSEFROM', False)),
                    reason='This interpreter does not use posix_spawn with the unchanged close_fds contract')
@pytest.mark.parametrize('operation', ['changed-spawn-environment', 'changed-spawn-argument', 'changed-spawn-executable'])
def test_authorized_catalog_spawn_cannot_change_identity_or_environment(isolated_catalog, operation):
    run, root, _ = isolated_catalog
    result = run(operation)
    assert result.returncode != 0
    assert 'unlisted subprocess' in result.stderr
    assert records(root, 'account-catalog-*.jsonl') == []
    refusal, = records(root, 'guard-*.jsonl')
    assert refusal['event'] == 'os.posix_spawn'
