import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest

shared = pytest.importorskip('amplifier_foundation.sources.shared')
from amplifier_web.update_sources import adopt_clean_sources, bindings, stage_binding, store_environment


@pytest.mark.parametrize('case', [
    'readonly-object', 'writable-object', 'hardlink', 'reparse',
    'source-file', 'outside', 'directory', 'posix', 'second-failure',
])
def test_adopted_checkout_cleanup_only_repairs_exclusive_readonly_git_objects(tmp_path, monkeypatch, case):
    import stat
    from amplifier_web import update_sources
    base = tmp_path / 'stage/foundation'
    root = base / 'cache/checkout'
    path = root / '.git/objects/pack/pack-fixture.idx'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'git object index')
    sibling = tmp_path / 'retained'
    sibling.write_bytes(b'must survive')
    if case == 'hardlink':
        sibling.unlink()
        os.link(path, sibling)
    elif case == 'source-file':
        path = root / 'user.py'
        path.write_bytes(b'user = True')
    elif case == 'outside':
        path = sibling
    elif case == 'directory':
        path = path.parent
    original_stat, original_chmod = Path.lstat, os.chmod
    attributes = 0 if case == 'writable-object' else stat.FILE_ATTRIBUTE_READONLY
    if case == 'reparse':
        attributes |= stat.FILE_ATTRIBUTE_REPARSE_POINT
    def inspected(value, *args, **kwargs):
        info = original_stat(value, *args, **kwargs)
        if Path(value) != path:
            return info
        return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                               st_file_attributes=attributes)
    monkeypatch.setattr(Path, 'lstat', inspected)
    monkeypatch.setattr(update_sources, 'sys', SimpleNamespace(
        platform='linux' if case == 'posix' else 'win32'))
    calls = []
    def chmod(value, mode, **kwargs):
        calls.append(Path(value))
        assert kwargs == {'follow_symlinks': False}
        original_chmod(value, mode, **kwargs)
    monkeypatch.setattr(update_sources.os, 'chmod', chmod)
    failure = PermissionError('simulated Windows read-only unlink refusal')
    def rmtree(value, *, onexc=None):
        assert Path(value) == root
        if onexc is None:
            raise failure  # Original implementation cannot remove Git objects.
        if case == 'second-failure':
            def still_denied(value):
                raise failure
            # The retry function itself is os.unlink in the real rmtree callback.
            monkeypatch.setattr(update_sources.os, 'unlink', still_denied)
        function = os.rmdir if case == 'directory' else update_sources.os.unlink
        onexc(function, path, failure)
    monkeypatch.setattr(update_sources.shutil, 'rmtree', rmtree)
    if case == 'readonly-object':
        update_sources._remove_adopted_checkout(root, base)
        assert not path.exists() and calls == [path]
    else:
        with pytest.raises((PermissionError, ValueError)):
            update_sources._remove_adopted_checkout(root, base)
        assert path.exists()
        assert calls == ([path] if case == 'second-failure' else [])
    expected = b'git object index' if case == 'hardlink' else b'must survive'
    assert sibling.read_bytes() == expected


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def checkout(root, revision=None):
    root.mkdir(parents=True)
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'bundle.md').write_text('old')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'fixture')
    revision = git(root, 'rev-parse', 'HEAD')
    (root / '.amplifier_cache_meta.json').write_text(json.dumps({'git_url': 'https://example.invalid/repo', 'ref': 'main', 'commit': revision}))
    return revision


@pytest.mark.asyncio
async def test_owned_adoption_new_binding_and_rollback_keep_exact_snapshots(tmp_path, monkeypatch):
    home, stage = tmp_path / 'app', tmp_path / 'app/updates/releases/one'
    root = stage / 'foundation/cache/legacy'
    first = checkout(root)
    manager = SimpleNamespace(home=home)
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    await adopt_clean_sources(manager, stage)
    assert not root.exists()
    rows = await bindings(home, stage / 'foundation', {})
    assert len(rows) == 1 and rows[0]['current'] == first
    store = shared.SharedSourceStore(home / 'source-store')
    old = store.checkout(rows[0]['url'], first)
    repo = tmp_path / 'newer'
    subprocess.check_call(['git', 'clone', '--quiet', str(old), str(repo)])
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'bundle.md').write_text('new')
    git(repo, 'commit', '-am', 'new')
    second = git(repo, 'rev-parse', 'HEAD')
    await store.ensure(rows[0]['url'], second, existing=repo)
    from amplifier_web.update_storage import copy_snapshot
    new_stage = home / 'updates/releases/two'
    copy_snapshot(stage, new_stage)
    await stage_binding(manager, new_stage, {**rows[0], 'latest': second})
    assert (old / 'bundle.md').read_text() == 'old'
    assert (await bindings(home, stage / 'foundation', {}))[0]['current'] == first
    assert (await bindings(home, new_stage / 'foundation', {}))[0]['current'] == second


@pytest.mark.asyncio
async def test_dirty_duplicate_protects_entire_source_identity(tmp_path, monkeypatch):
    home, stage = tmp_path / 'app', tmp_path / 'app/updates/releases/one'
    one = stage / 'foundation/cache/one'
    checkout(one)
    two = stage / 'foundation/cache/two'
    from amplifier_web.update_storage import copy_snapshot
    copy_snapshot(one, two)
    (two / 'bundle.md').write_text('local edit')
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    await adopt_clean_sources(SimpleNamespace(home=home), stage)
    assert one.exists() and two.exists()
    assert not list((stage / 'foundation/cache/.source-bindings').glob('*.json'))
    assert (two / 'bundle.md').read_text() == 'local edit'


@pytest.mark.asyncio
async def test_imports_preserve_shared_object_and_worker_policy(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    source = tmp_path / 'source'
    checkout(source)
    (source / 'fixture_module.py').write_text('value = 42\n')
    git(source, 'add', 'fixture_module.py')
    git(source, 'commit', '-m', 'importable module')
    revision = git(source, 'rev-parse', 'HEAD')
    url = 'https://example.invalid/repo'
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '0')
    store = shared.SharedSourceStore(home / 'source-store')
    await store.ensure(url, revision, existing=source)
    obj = store.checkout(url, revision)
    environment = {**os.environ, **store_environment(home)}
    environment.pop('PYTHONPYCACHEPREFIX', None)
    from amplifier_web.host.config import worker_environment
    assert worker_environment()['PYTHONDONTWRITEBYTECODE'] == '1'
    for _ in range(2):
        subprocess.run([sys.executable, '-c', 'import fixture_module; assert fixture_module.value == 42'],
                       cwd=obj, env=environment, check=True)
        assert store.verify(url, revision) == obj
        assert not list(obj.rglob('*.pyc'))
    # The fix must not relax detection of either generated or edited code.
    environment.pop('PYTHONDONTWRITEBYTECODE')
    # Store objects are read-only. Deliberately permit this tampering control
    # for an ordinary CI user as well as root; import policy alone is tested above.
    obj.chmod(obj.stat().st_mode | 0o200)
    subprocess.run([sys.executable, '-c', 'import fixture_module'], cwd=obj, env=environment, check=True)
    assert list(obj.rglob('*.pyc'))
    # Restore publication's seal so verification reaches the dirty-content
    # guard rather than stopping at the deliberately writable fixture root.
    obj.chmod(obj.stat().st_mode & ~0o222)
    with pytest.raises(ValueError, match='contents changed'):
        store.verify(url, revision)
