"""Origin-only checks run on Windows/macOS/Linux without Docker or DTU."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tarfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from amplifier_web import registered_test_runners as runners
from amplifier_web import task_environments as environments
from amplifier_web import test_environment_configs as configs
from amplifier_web import test_origin_files as files
from amplifier_web import test_origin_snapshot as snapshots
from amplifier_web import test_origin_storage as receipts
from amplifier_web import test_source_transfer as transfer


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args])


def test_pinned_binary_publication_and_no_clobber(tmp_path):
    payload = bytes(range(256)) * 4096 + b'\r\n\x1a\x00\xff'
    with files.directory(tmp_path, private=os.name == 'nt') as parent:
        with os.fdopen(parent.create('staging.bin'), 'wb') as stream:
            stream.write(payload)
        parent.link('staging.bin', 'saved.bin')
        with pytest.raises(FileExistsError):
            parent.link('staging.bin', 'saved.bin')
        parent.unlink('staging.bin')
        with parent.open('saved.bin', private=os.name == 'nt') as stream:
            assert stream.read() == payload
        parent.assert_current()
    assert (tmp_path / 'saved.bin').read_bytes() == payload


@pytest.fixture(autouse=True)
def windows_fixture_permissions(tmp_path):
    if os.name == 'nt':
        # Captured only on test failure; distinguishes a genuinely shared CI
        # temporary directory from a failure in the handle-based ACL reader.
        print(subprocess.check_output(['icacls', str(tmp_path)], text=True))


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / 'source'
    path.mkdir()
    git(path, 'init', '-q')
    git(path, 'config', 'user.email', 'test@example.invalid')
    git(path, 'config', 'user.name', 'Test')
    (path / 'tracked.txt').write_text('committed\n')
    (path / '.gitignore').write_text('private/\n.env\n.amplifier/\n')
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'initial')
    return path


def prepared_client(repository):
    record = {'delivery': {'type': 'repository'}, 'source': {'revision': 'a' * 40}}
    (repository / 'components').mkdir()
    (repository / 'components/web-client.source.json').write_text(json.dumps(record))
    (repository / '.gitignore').write_text('.build/\n')
    root = repository / '.build/web-client'
    root.mkdir(parents=True)
    (root / 'package.json').write_text('{"name":"client"}')
    (root / 'package-lock.json').write_text('{"lockfileVersion":3}')
    (root / '.client-source.json').write_text(json.dumps(record))
    (root / 'unrelated.js').write_text('This source is not part of manifest staging')
    (root / 'node_modules').mkdir()
    (root / 'node_modules/local.js').write_text('Do not transfer dependencies')
    return root


def test_repository_client_snapshot_includes_only_three_prepared_inputs(repository, tmp_path):
    prepared_client(repository)
    before = git(repository, 'status', '--porcelain=v1')
    result = transfer.export_snapshot(repository, tmp_path / 'client.tar',
                                      lambda: pytest.fail('repository selection must use canonical snapshot owner'))
    with tarfile.open(result['path']) as archive:
        selected = {name for name in archive.getnames() if name.startswith('.build/')}
    assert selected == {'.build/web-client/' + name for name in
                        ('package.json', 'package-lock.json', '.client-source.json')}
    assert git(repository, 'status', '--porcelain=v1') == before
    assert result['snapshot_tree'] and result['archive_sha256']
    assert not (repository / '.git/index.lock').exists()


@pytest.mark.parametrize('change', ['missing', 'different-selection', 'symlink'])
def test_repository_snapshot_refuses_missing_or_changed_preparation(repository, tmp_path, change):
    root = prepared_client(repository)
    if change == 'missing':
        (root / 'package-lock.json').unlink()
    elif change == 'different-selection':
        (root / '.client-source.json').write_text('{}')
    else:
        if os.name == 'nt':
            pytest.skip('Windows symlink privilege is not part of this source fixture')
        (root / 'package.json').unlink()
        (root / 'package.json').symlink_to(repository / 'tracked.txt')
    with pytest.raises(ValueError, match='web_client_build.py prepare'):
        snapshots.export_archive(repository, tmp_path / 'missing.tar')
    assert not (tmp_path / 'missing.tar').exists()


def test_ordinary_snapshot_keeps_existing_exporter(repository, tmp_path):
    calls = []
    expected = {'original': 'library-owned snapshot'}
    result = transfer.export_snapshot(repository, tmp_path / 'unused.tar',
                                      lambda: calls.append(True) or expected)
    assert result is expected and calls == [True]


def target():
    return {'id': 'target', 'name': 'Test machine', 'enabled': True,
            'connection': 'approved-machine', 'configPath': '', 'machineId': 'a'*32,
            'profiles': ['linux-python'], 'incus': {'project': 'test'}}


def test_remote_only_configuration_and_retained_cleanup_need_no_local_install(tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path)
    runners.publish(app, {'version': 1, 'runners': [target()]})
    monkeypatch.setattr(environments, 'SUPPORTED_HOST', False)
    config, state, reason = environments.configuration(app)
    assert state == 'ready' and 'does not need Docker' in reason
    assert 'python' not in config and 'installationId' not in config
    assert config['originSnapshotVersion'] == 1
    keys = runners.retain_candidates(app, config, 'linux-python')
    assert keys == [configs.key(config)]
    runners.publish(app, {'version': 1, 'runners': []})
    assert configs.resolve(app, {'backendKey': keys[0], 'backendConfigRetained': True}, None) == config
    assert environments.configuration(app)[0] is None


def test_remote_retention_refuses_mutated_snapshot_authority(tmp_path):
    config = runners.configuration(None, None, target())
    for key, value in [('originSnapshotVersion', 2), ('python', '/unapproved/python'), ('installationId', 'foreign')]:
        with pytest.raises(ValueError, match='snapshot'):
            runners.qualify_retained(None, {**config, key: value})


def test_snapshot_keeps_dirty_untracked_raw_bytes_excludes_ignored_and_git(repository, tmp_path):
    (repository / 'tracked.txt').write_bytes(b'dirty\r\n')
    (repository / 'new.txt').write_text('new\n')
    (repository / '.env').write_text('must not transfer')
    (repository / 'private').mkdir()
    (repository / 'private/secret').write_text('must not transfer')
    (repository / '.gitattributes').write_text('tracked.txt export-ignore\nnew.txt filter=do-not-run\n')
    before = (git(repository, 'status', '--porcelain=v1', '-z'), git(repository, 'rev-parse', 'HEAD'), (repository / '.git/index').read_bytes())
    first = snapshots.export_archive(repository, tmp_path / 'first.tar')
    second = snapshots.export_archive(repository, tmp_path / 'second.tar')
    assert first['archive_sha256'] == second['archive_sha256']
    assert first['snapshot_tree'] == second['snapshot_tree']
    assert first['snapshot_commit'] == second['snapshot_commit']
    with tarfile.open(first['path']) as archive:
        assert set(archive.getnames()) == {'.gitignore', '.gitattributes', 'tracked.txt', 'new.txt'}
        assert archive.extractfile('tracked.txt').read() == b'dirty\r\n'
    assert before == (git(repository, 'status', '--porcelain=v1', '-z'), git(repository, 'rev-parse', 'HEAD'), (repository / '.git/index').read_bytes())


def test_snapshot_rejects_concurrent_source_change(repository, tmp_path, monkeypatch):
    original = snapshots._tree_entries
    calls = []
    def changing(*args):
        result = original(*args)
        calls.append(1)
        if len(calls) == 1:
            (repository / 'tracked.txt').write_text('changed during export')
        return result
    monkeypatch.setattr(snapshots, '_tree_entries', changing)
    with pytest.raises(ValueError, match='changed during export'):
        snapshots.export_archive(repository, tmp_path / 'source.tar')
    assert not (tmp_path / 'source.tar').exists()


def test_snapshot_never_writes_checkout_and_never_overwrites_output(repository, tmp_path):
    with pytest.raises(ValueError, match='outside'):
        snapshots.export_archive(repository, repository / 'source.tar')
    path = tmp_path / 'source.tar'
    path.write_text('original')
    with pytest.raises(ValueError, match='already exists'):
        snapshots.export_archive(repository, path)
    assert path.read_text() == 'original'


def test_snapshot_preserves_symlink_text_never_destination(repository, tmp_path):
    secret = tmp_path / 'secret'
    secret.write_text('outside bytes')
    link = repository / 'link'
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip('Creating native symlinks requires Windows developer mode or privilege')
    with tarfile.open(snapshots.export_archive(repository, tmp_path / 'source.tar')['path']) as archive:
        assert archive.getmember('link').issym()
        assert archive.getmember('link').linkname == os.readlink(link)
        assert 'secret' not in archive.getnames()


def test_windows_junction_parent_is_not_followed(repository, tmp_path):
    if os.name != 'nt':
        pytest.skip('Real Windows reparse-point check')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret').write_text('outside bytes')
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(repository / 'junction'), str(outside)], check=True, capture_output=True)
    with pytest.raises((ValueError, OSError)):
        files.source_file(repository, 'junction/secret', '', 1024)


def test_private_receipts_are_immutable_and_owner_bound(tmp_path):
    payload = {'root': str(tmp_path), 'rootOwner': 'chat', 'operationId': 'launch'}
    backend = {'backend': 'incus', 'daemon': 'one', 'project': 'test'}
    receipts.verify_binding(payload, 'guest', backend)
    receipts.verify_binding(payload, 'guest', backend)
    with pytest.raises(ValueError, match='another operation'):
        receipts.verify_binding({**payload, 'rootOwner': 'peer'}, 'guest', backend)
    assert files.read(tmp_path / 'backend-binding.json')['rootOwner'] == 'chat'


def test_posix_parent_replacement_cannot_redirect_source_read_or_receipt(tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('Windows handles deny the parent rename itself')
    origin = tmp_path / 'origin'
    original = origin / 'nested'
    original.mkdir(parents=True, mode=0o700)
    (original / 'selected').write_text('owned')
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o700)
    (outside / 'selected').write_text('must not read')
    real = files.directory
    @contextmanager
    def swap(path, **options):
        with real(path, **options) as pinned:
            if Path(path) == original and not original.is_symlink():
                original.rename(origin / 'retained')
                original.symlink_to(outside, target_is_directory=True)
            yield pinned
    monkeypatch.setattr(files, 'directory', swap)
    assert files.source_file(origin, 'nested/selected', '', 1024)[1] == b'owned'
    original.unlink()
    (origin / 'retained').rename(original)
    with pytest.raises((ValueError, OSError)):
        files.publish(original / 'receipt.json', {'owned': True})
    assert not (outside / 'receipt.json').exists()
    assert json.loads((origin / 'retained/receipt.json').read_text()) == {'owned': True}


def test_windows_shared_write_acl_cannot_authorize_retained_records(tmp_path):
    if os.name != 'nt':
        pytest.skip('Real Windows ACL check')
    path = tmp_path / 'record.json'
    files.publish(path, {'owned': True})
    subprocess.run(['icacls', str(path), '/grant', '*S-1-1-0:(W)'], check=True, capture_output=True)
    with pytest.raises(ValueError, match='other Windows users'):
        files.read(path)


def test_portable_remote_storage_allocation_retains_identity(tmp_path):
    config = runners.configuration(None, None, target())
    row = environments.prepare(tmp_path, 'chat', 'workspace', {'sessionId': 'chat'}, str(tmp_path / 'source'),
                               configs.key(config), 'start', None, config)
    row = receipts.allocate(tmp_path, row, lambda: None)
    assert row['status'] == 'ready'
    assert receipts.observe(tmp_path, row)['observation'] == 'present'
    assert receipts.allocate(tmp_path, row, lambda: None)['directoryIdentity'] == row['directoryIdentity']


def test_source_transfer_does_not_spawn_local_runner(repository, tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path)
    config = runners.configuration(app, None, target())
    received = bytearray()
    effects = []
    def call(runner, payload, timeout=60):
        effects.append(payload['action'])
        if payload['action'] == 'source':
            import base64
            received.extend(base64.b64decode(payload['content']))
            return {'ok': True, 'offset': len(received)}
        assert payload['action'] == 'prepare'
        return {'ok': True, 'universeId': 'guest', 'backendIdentity': {'backend': 'incus', 'daemon': 'one', 'project': 'test'}, 'snapshot': payload['snapshot']}
    monkeypatch.setattr(runners, 'call', call)
    payload = {'action': 'prepare', 'root': str(tmp_path), 'source': str(repository), 'rootOwner': 'chat', 'operationId': 'launch'}
    result = runners.invoke(config, tmp_path, payload)
    assert result['ok'] and effects == ['source', 'prepare']
    assert bytes(received) == (tmp_path / 'source.tar').read_bytes()
    assert files.read(tmp_path / 'backend-binding.json')['universeId'] == 'guest'


def test_remote_posix_controller_path_and_strict_trust_survive_origin_platform(monkeypatch):
    observed = []
    monkeypatch.setattr(runners.shutil, 'which', lambda *args, **kw: 'ssh')
    monkeypatch.setattr(runners.subprocess, 'run', lambda command, **kwargs: observed.append(command) or SimpleNamespace(returncode=0, stdout=b'{"ok":true}'))
    runner = {**target(), 'controller': '/home/operator/controllers/' + 'a'*64 + '/amplifier_web/test_runner_controller.py', 'packageDigest': 'a'*64,
              'packageFiles': ['__init__.py', 'test_runner_controller.py']}
    assert runners.call(runner, {'action': 'capacity'})['ok']
    assert '-oStrictHostKeyChecking=yes' in observed[0]
    assert '-oBatchMode=yes' in observed[0]
    assert '-oForwardAgent=no' in observed[0]


def test_snapshot_output_parent_replacement_cannot_redirect_archive(repository, tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('Windows directory handles deny parent replacement')
    output = tmp_path / 'output'
    output.mkdir(mode=0o700)
    retained = tmp_path / 'retained'
    original = snapshots.directory
    @contextmanager
    def swap(path, **options):
        with original(path, **options) as pinned:
            if Path(path) == output:
                output.rename(retained)
                output.mkdir(mode=0o700)
            yield pinned
    monkeypatch.setattr(snapshots, 'directory', swap)
    with pytest.raises(ValueError, match='directory changed'):
        snapshots.export_archive(repository, output / 'source.tar')
    assert not list(output.iterdir())
    assert not list(retained.iterdir())


def test_snapshot_late_parent_swap_keeps_all_writes_at_pinned_owner(repository, tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('Windows directory handles deny parent replacement')
    output = tmp_path / 'output'
    output.mkdir(mode=0o700)
    retained = tmp_path / 'retained'
    create = files.PinnedDirectory.create
    def swap(parent, name):
        descriptor = create(parent, name)
        if parent.path == output:
            output.rename(retained)
            output.mkdir(mode=0o700)
        return descriptor
    monkeypatch.setattr(files.PinnedDirectory, 'create', swap)
    with pytest.raises(ValueError, match='directory changed'):
        snapshots.export_archive(repository, output / 'source.tar')
    assert not list(output.iterdir())
    assert [path.name for path in retained.iterdir()] == ['source.tar']
    with tarfile.open(retained / 'source.tar') as archive:
        assert archive.extractfile('tracked.txt').read() == b'committed\n'


def test_registration_store_rejects_shared_write_and_linked_authority(tmp_path):
    app = SimpleNamespace(data_dir=tmp_path)
    runners.publish(app, {'version': 1, 'runners': [target()]})
    assert runners.store(app)['runners'] == [target()]
    path = tmp_path / 'test-runners.json'
    if os.name == 'nt':
        subprocess.run(['icacls', str(path), '/grant', '*S-1-1-0:(W)'], check=True, capture_output=True)
        with pytest.raises(ValueError, match='other Windows users'):
            runners.store(app)
    else:
        path.chmod(0o666)
        with pytest.raises(ValueError, match='private'):
            runners.store(app)
        path.chmod(0o600)
    # A hard-linked authority is rejected even with private ACL/mode.
    original = tmp_path / 'original.json'
    path.rename(original)
    os.link(original, path)
    with pytest.raises(ValueError):
        runners.store(app)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX capability failure')
def test_missing_private_file_primitives_fail_before_opening(tmp_path, monkeypatch):
    monkeypatch.delattr(files.os, 'O_DIRECTORY')
    monkeypatch.setattr(files.os, 'open', lambda *_args, **_kwargs: pytest.fail('unsupported path open'))
    with pytest.raises(ValueError, match='not supported'):
        files.read(tmp_path / 'record.json')
