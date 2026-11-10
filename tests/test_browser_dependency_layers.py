"""Browser seeds reuse locked dependencies while each task retains its own source."""
import hashlib
import base64
import json
from pathlib import Path

import pytest

from amplifier_web import test_dependency_layers as layers
from amplifier_web import test_environment_preparation as preparation
from amplifier_web import test_environment_prepare_guest as guest
from test_dependency_layers import LOCK, PROJECT, prepared, settle
from test_environment_preparation import archive


def manifests(prefix='tests/web/'):
    package = {'name': 'browser-fixture', 'version': '1.0.0', 'devDependencies': {'playwright': '1.63.0'}}
    lock = {'lockfileVersion': 3, 'packages': {'': package, 'node_modules/playwright': {
        'version': '1.63.0', 'resolved': 'https://registry.npmjs.org/playwright/-/playwright-1.63.0.tgz',
        'integrity': 'sha512-' + base64.b64encode(b'a' * 64).decode()}}}
    return {prefix + 'package.json': json.dumps(package), prefix + 'package-lock.json': json.dumps(lock)}


def test_current_repository_npm_inputs_remain_independently_eligible():
    root = Path(__file__).parents[1]
    delivery = json.loads((root / 'components/web-client.source.json').read_text())['delivery']
    client = delivery['path'] if delivery['type'] == 'directory' else '.build/web-client'
    projects = [{'directory': 'tests/web', 'key': 'tests-web', 'manifestDirectory': 'tests/web'},
                {'directory': '.build/web-client', 'key': 'web-client', 'manifestDirectory': client}]
    files = {directory + '/' + name: (root / directory / name).read_bytes()
             for directory in ('tests/web', client) for name in ('package.json', 'package-lock.json')}
    assert layers.browser_inputs(files, {'nodeDirectory': 'tests/web', 'nodeKey': 'tests-web',
                                        'nodeProjects': projects})
    host = json.loads(files['tests/web/package.json'])
    canonical = json.loads(files[client + '/package.json'])
    assert host['name'] != canonical['name']
    # Host fixtures use Vite, React and Playwright as explicit test tools;
    # the client still owns production dependencies and the production build.
    assert not host.get('dependencies')
    assert {'vite', 'playwright', '@playwright/test'} <= host['devDependencies'].keys()
    host_lock = json.loads(files['tests/web/package-lock.json'])
    for name, version in host['devDependencies'].items():
        assert version == host_lock['packages']['node_modules/' + name]['version'], name
    assert 'build' not in host['scripts']
    assert canonical['scripts']['build']


@pytest.mark.parametrize('algorithm,length', [('sha1', 20), ('sha256', 32), ('sha384', 48), ('sha512', 64)])
def test_npm_sri_variants_preserve_lockfile_hash_policy(algorithm, length):
    value = algorithm + '-' + base64.b64encode(b'a' * length).decode()
    assert layers.npm_integrity(value)
    assert not layers.npm_integrity(value + '!')
    assert not layers.npm_integrity(algorithm + '-eA==')


@pytest.mark.parametrize('prefix', ['', 'frontend/', 'tests/web/'])
def test_browser_reuses_manifest_only_seed_and_finalizes_each_source(prepared, tmp_path, prefix):
    run, calls, _ = prepared
    first, a = run('first-browser', profile='browser-test', extra=manifests(prefix))
    second, b = run('second-browser', body='new task source', profile='browser-test', extra=manifests(prefix))
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'finalize']
    assert set(calls[0]['contents']) == {'pyproject.toml', 'uv.lock', *manifests(prefix)}
    assert calls[1]['contents']['fixture.py'] == 'first'
    assert calls[2]['contents']['fixture.py'] == 'new task source'
    assert calls[1]['base'] == calls[2]['base']
    assert a['image'] != b['image'] and a['sourceSha256'] != b['sourceSha256']
    assert a['profileId'] == b['profileId'] == 'browser-test'
    assert layers.read(second / 'preparation/dependency-reuse.json')['hit'] is True
    settle(first)
    assert layers.inspect(tmp_path)['candidates'] == []
    settle(second)
    assert len(layers.inspect(tmp_path)['candidates']) == 1


def test_browser_release_version_uses_existing_node_and_python_dependencies(prepared):
    run, calls, _ = prepared
    first, before = run('first-release', profile='browser-test', extra=manifests())
    project = PROJECT.replace('version="1"', 'version="2"')
    lock = LOCK.replace('version="1"', 'version="2"', 1)
    second, after = run('next-release', profile='browser-test', extra=manifests(), project=project, lock=lock)
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'finalize']
    assert calls[1]['base'] == calls[2]['base']
    assert calls[2]['contents']['pyproject.toml'] == project
    assert calls[2]['contents']['uv.lock'] == lock
    assert before['lockfiles'] != after['lockfiles']
    assert layers.read(second / 'preparation/dependency-reuse.json')['hit'] is True
    assert layers.read(first / 'preparation/receipt.json') == before


@pytest.mark.parametrize('change', ['local', 'workspace', 'link', 'credentials', 'unhashed', 'lock-type', 'manifest-type', 'escape', 'extra-source'])
def test_unsupported_node_sources_keep_existing_cold_path(prepared, change):
    files = manifests()
    package = json.loads(files['tests/web/package.json'])
    lock = json.loads(files['tests/web/package-lock.json'])
    entry = lock['packages']['node_modules/playwright']
    if change == 'local':
        package['devDependencies']['helper'] = 'file:../helper'
    elif change == 'workspace':
        package['workspaces'] = ['packages/*']
    elif change == 'link':
        entry['link'] = True
    elif change == 'credentials':
        entry['resolved'] = 'https://token@registry.example/pkg.tgz'
    elif change == 'unhashed':
        entry.pop('integrity')
    elif change == 'lock-type':
        lock['lockfileVersion'] = 1
    elif change == 'manifest-type':
        package = []
    elif change == 'escape':
        lock['packages']['node_modules/../outside'] = dict(entry)
    else:
        lock['packages']['packages/local'] = dict(entry)
    files.update({'tests/web/package.json': json.dumps(package), 'tests/web/package-lock.json': json.dumps(lock)})
    run, calls, _ = prepared
    root, result = run('unsupported-browser', profile='browser-test', extra=files)
    assert result['status'] == 'ready'
    assert [call['mode'] for call in calls] == [None]
    assert not (root / 'preparation/dependency-borrow.json').exists()


def test_browser_only_project_stays_cold(tmp_path):
    snapshot = archive(tmp_path, manifests(''))
    plan = preparation.plan(tmp_path / 'source.tar', snapshot, 'browser-test')
    assert layers.inputs(tmp_path / 'source.tar', plan) is None


@pytest.mark.parametrize('path', ['.npmrc', 'tests/web/.npmrc', 'tests/web/npm-shrinkwrap.json'])
def test_project_npm_settings_or_overriding_lock_keep_cold_behavior(prepared, path):
    run, calls, _ = prepared
    run('npm-settings', profile='browser-test', extra={**manifests(), path: 'project-specific'})
    assert [call['mode'] for call in calls] == [None]


@pytest.mark.parametrize('filename', ['tests/web/package.json', 'tests/web/package-lock.json'])
def test_node_manifest_or_lock_change_creates_a_different_seed(prepared, filename):
    run, calls, _ = prepared
    files = manifests()
    run('original-browser', profile='browser-test', extra=files)
    changed = json.loads(files[filename])
    changed['description'] = 'changed frozen manifest'
    files[filename] = json.dumps(changed)
    run('changed-browser', profile='browser-test', extra=files)
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'layer', 'finalize']
    assert calls[1]['base'] != calls[3]['base']


def selected_client_manifests(delivery='directory'):
    directory = 'components/web-client' if delivery == 'directory' else '.build/web-client'
    record = {'delivery': {'type': delivery, **({'path': directory} if delivery == 'directory' else {})}}
    result = {**manifests(), **manifests(directory + '/'),
              'components/web-client.source.json': json.dumps(record)}
    if delivery == 'repository':
        result[directory + '/.client-source.json'] = json.dumps(record)
    return result


@pytest.mark.parametrize('delivery', ['directory', 'repository'])
def test_selected_client_and_host_tools_share_one_manifest_only_seed(prepared, delivery):
    run, calls, _ = prepared
    files = selected_client_manifests(delivery)
    first, before = run('first-client', profile='browser-test', extra=files)
    second, after = run('changed-source-client', body='changed host source', profile='browser-test', extra=files)
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'finalize']
    assert calls[1]['base'] == calls[2]['base']
    expected = {name for name in files if name.endswith(('package.json', 'package-lock.json'))}
    assert set(calls[0]['contents']) == {'pyproject.toml', 'uv.lock', *expected}
    assert len(before['lockfiles']) == len(after['lockfiles']) == 6
    assert [project['directory'] for project in before['intent']['nodeProjects']] == ['tests/web', '.build/web-client']
    assert layers.read(second / 'preparation/dependency-reuse.json')['hit'] is True


@pytest.mark.parametrize('directory', ['tests/web', 'components/web-client'])
def test_either_owned_node_lock_changes_combined_seed(prepared, directory):
    run, calls, _ = prepared
    files = selected_client_manifests()
    run('original-owners', profile='browser-test', extra=files)
    path = directory + '/package-lock.json'
    lock = json.loads(files[path]); lock['description'] = 'new exact dependency selection'
    files[path] = json.dumps(lock)
    run('changed-owner-lock', profile='browser-test', extra=files)
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'layer', 'finalize']


def test_repository_client_requires_matching_prepared_inputs(tmp_path):
    files = selected_client_manifests('repository')
    files.pop('.build/web-client/package-lock.json')
    snapshot = archive(tmp_path, files)
    with pytest.raises(ValueError, match='web_client_build.py prepare'):
        preparation.plan(tmp_path / 'source.tar', snapshot, 'browser-test')
    files = selected_client_manifests('repository')
    files['.build/web-client/.client-source.json'] = json.dumps({'delivery': {'type': 'directory'}})
    snapshot = archive(tmp_path, files)
    with pytest.raises(ValueError, match='another selection'):
        preparation.plan(tmp_path / 'source.tar', snapshot, 'browser-test')


@pytest.mark.parametrize('client_version', ['1.63.0', '1.64.0'])
def test_guest_installs_and_retains_owned_closures_and_matching_browsers(tmp_path, monkeypatch, client_version):
    workspace, prefix = tmp_path / 'workspace', tmp_path / 'prepared'
    projects = [{'directory': 'tests/web', 'key': 'tests-web', 'manifestDirectory': 'tests/web'},
                {'directory': '.build/web-client', 'key': 'web-client', 'manifestDirectory': 'components/web-client'}]
    for project, version in zip(projects, ('1.63.0', client_version)):
        library = workspace / project['manifestDirectory'] / 'node_modules/playwright'
        library.mkdir(parents=True)
        (library / 'cli.js').write_text('browser driver')
        (library / 'package.json').write_text(json.dumps({'version': version}))
        (library.parent / 'owner.txt').write_text(project['key'])
    calls = []
    monkeypatch.setattr(guest, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(guest, 'PREFIX', prefix)
    actual = guest.prepare_node_projects({'nodeDirectory': 'tests/web', 'nodeKey': 'tests-web',
                                         'nodeProjects': projects}, workspace)
    installs = [kwargs['cwd'] for args, kwargs in calls if args[:2] == ('npm', 'ci')]
    assert installs == [workspace / 'tests/web', workspace / 'components/web-client']
    assert len([args for args, kwargs in calls if args[0] == 'node']) == (1 if client_version == '1.63.0' else 2)
    guest.retain_node_projects(actual, workspace)
    for project in projects:
        assert (prefix / 'node' / project['key'] / 'owner.txt').read_text() == project['key']
        assert not (workspace / project['manifestDirectory'] / 'node_modules').exists()


@pytest.fixture
def browser_wheels(tmp_path, monkeypatch):
    monkeypatch.setattr(guest, 'PREFIX', tmp_path)
    folder = tmp_path / 'browser-wheels'
    folder.mkdir()
    wheel = folder / 'playwright.whl'
    wheel.write_bytes(b'qualified immutable wheel')
    inventory = {'profileId': 'browser-test', 'node': {'version': 'v' + guest.NODE_VERSION, 'playwright': '1.63.0'},
                 'browserWheels': [{'filename': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}]}
    (tmp_path / 'inventory.json').write_text(json.dumps(inventory))
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return json.dumps({'fixture': '2', 'locked-dependency': '3'}) if '-c' in args else None
    monkeypatch.setattr(guest, 'run', run)
    return tmp_path, inventory, calls


def test_finalizer_restores_only_hashed_wheels_offline_without_changing_locked_packages(browser_wheels):
    root, inventory, calls = browser_wheels
    result = guest.restore_browser({'playwrightVersion': '1.63.0'}, 'uv')
    assert result == {key: inventory[key] for key in ('node', 'browserWheels')}
    install = next(call for call in calls if 'install' in call)
    assert '--no-index' in install and '--find-links' in install and '--constraint' in install
    assert (root / 'python-base-constraints.txt').read_text() == 'fixture==2\nlocked-dependency==3\n'


@pytest.mark.parametrize('change', ['bytes', 'extra-wheel', 'duplicate', 'traversal', 'symlink', 'version'])
def test_changed_retained_wheels_are_rejected_before_install(browser_wheels, change):
    root, inventory, calls = browser_wheels
    wheel = root / 'browser-wheels/playwright.whl'
    if change == 'bytes':
        wheel.write_bytes(b'changed')
    elif change == 'extra-wheel':
        (wheel.parent / 'unrecorded.whl').write_bytes(b'extra')
    elif change == 'duplicate':
        inventory['browserWheels'] *= 2
    elif change == 'traversal':
        inventory['browserWheels'][0]['filename'] = '../outside.whl'
    elif change == 'symlink':
        wheel.rename(root / 'outside.whl')
        wheel.symlink_to(root / 'outside.whl')
    else:
        inventory['node']['playwright'] = '1.62.0'
    (root / 'inventory.json').write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match='changed'):
        guest.restore_browser({'playwrightVersion': '1.63.0'}, 'uv')
    assert calls == []


def test_finalizer_refuses_locked_dependency_drift(browser_wheels, monkeypatch):
    observations = iter([{'locked': '1'}, {'locked': '2'}])
    monkeypatch.setattr(guest, 'run', lambda *args, **kwargs: json.dumps(next(observations)) if '-c' in args else None)
    with pytest.raises(ValueError, match='locked Python project dependency'):
        guest.restore_browser({'playwrightVersion': '1.63.0'}, 'uv')
