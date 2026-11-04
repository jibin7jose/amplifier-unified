"""Real Git/import caches must not permanently pin an unused generation."""
import json
import marshal
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys

import pytest

from amplifier_web import runtime_qualification, update_retention as retention
from test_update_retention import manager_fixture


VERIFY_GENERATION = retention.verify_generation


def git(source, *args):
    return subprocess.check_output(['git', *args], cwd=source, text=True).strip()


def imported_generation(tmp_path, monkeypatch, *, subdirectory=False):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    folder = manager.directory / 'releases' / names[3]
    source = folder / 'foundation/cache/fixture'
    source.mkdir(parents=True)
    (source / 'fixture.py').write_text('VALUE = 42\n')
    if subdirectory:
        (source / 'package').mkdir()
        (source / 'package/__init__.py').write_text('VALUE = 1\n')
    git(source, 'init', '-q', '-b', 'main')
    git(source, 'config', 'user.name', 'Fixture Author')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'Fixture source')
    git(source, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (source / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': 'https://example.invalid/fixture', 'ref': 'main'}))
    metadata = project / '.venv/lib' / ('python' + '.'.join(map(str, sys.version_info[:2]))) / 'site-packages/fixture-1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n')
    (metadata / 'direct_url.json').write_text(json.dumps({
        'url': (source / 'package' if subdirectory else source).as_uri(), 'dir_info': {'editable': True}}))
    (folder / 'runtime-installed.json').write_text(json.dumps(runtime_qualification.installed_graph(project)))
    monkeypatch.setattr(retention, 'verify_generation', VERIFY_GENERATION)
    env = {**os.environ, 'PYTHONPATH': str(source)}
    env.pop('PYTHONDONTWRITEBYTECODE', None)
    subprocess.run([sys.executable, '-c', 'import fixture; assert fixture.VALUE == 42'],
                   cwd=source, env=env, check=True)
    cached, = (source / '__pycache__').glob('*.pyc')
    runtime_qualification.verify_recorded(project, folder)
    return manager, names, project, folder, source, cached


@pytest.mark.parametrize('variant', ['import', 'opt-1', 'opt-2', 'checked-hash', 'unchecked-hash'])
async def test_normal_import_bytecode_does_not_pin_verified_unused_generation(tmp_path, monkeypatch, variant):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if variant.startswith('opt-'):
        cached = Path(py_compile.compile(str(source / 'fixture.py'), optimize=int(variant[-1]), doraise=True))
    elif variant.endswith('-hash'):
        mode = (py_compile.PycInvalidationMode.CHECKED_HASH if variant == 'checked-hash'
                else py_compile.PycInvalidationMode.UNCHECKED_HASH)
        py_compile.compile(str(source / 'fixture.py'), invalidation_mode=mode, doraise=True)
    assert cached.exists()
    preview = await retention.reclaim(manager, inspect_only=True)
    assert [row['id'] for row in preview['candidates']] == [names[3]]
    assert cached.exists()  # Inspection must not normalize or remove files.
    result = await retention.reclaim(manager, selected={names[3]})
    assert result['removed'] == 1
    assert not folder.exists()
    assert project.exists()  # Shared with current and rollback generations.
    assert all((manager.directory / 'releases' / name).exists() for name in names[:3])


@pytest.mark.parametrize('extra', ['notes.txt', 'untracked.py', 'fixture.egg-info/PKG-INFO',
                                  '.venv/pyvenv.cfg', 'fixture.pyc', '__pycache__/unknown.cpython-313.pyc'])
async def test_unknown_files_still_protect_generation(tmp_path, monkeypatch, extra):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    target = source / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(cached.read_bytes() if extra.endswith('.pyc') else b'retain me')
    assert (await retention.reclaim(manager))['removed'] == 0
    assert target.exists() and cached.exists()


@pytest.mark.parametrize('change', ['modified-source', 'local-commit', 'untracked-source',
                                   'symlink-cache', 'symlink-directory', 'malformed-bytecode',
                                   'backup-suffix', 'foreign-tag', 'bad-magic', 'header-only',
                                   'missing-source-proof'])
async def test_bytecode_exception_requires_unchanged_verified_tracked_source(tmp_path, monkeypatch, change):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if change == 'modified-source':
        (source / 'fixture.py').write_text('VALUE = 99\n')
    elif change == 'local-commit':
        git(source, 'checkout', '-qb', 'unpublished')
        (source / 'private.txt').write_text('keep this local work')
        git(source, 'add', 'private.txt')
        git(source, 'commit', '-qm', 'Unpublished work')
        git(source, 'checkout', '-q', 'main')
    elif change == 'untracked-source':
        (source / 'unknown.py').write_text('VALUE = 42\n')
        cached.with_name('unknown.' + cached.name.split('.', 1)[1]).write_bytes(cached.read_bytes())
    elif change in {'symlink-cache', 'symlink-directory'}:
        outside = tmp_path / 'other-owned-fixture'
        outside.mkdir()
        destination = outside / cached.name
        cached.rename(destination)
        if change == 'symlink-cache':
            cached.symlink_to(destination)
        else:
            cached.parent.rmdir()
            cached.parent.symlink_to(outside, target_is_directory=True)
    elif change == 'malformed-bytecode':
        cached.write_text('A note with a misleading filename must be retained.')
    elif change in {'backup-suffix', 'foreign-tag'}:
        renamed = cached.with_suffix('.backup') if change == 'backup-suffix' else cached.with_name('fixture.cpython-999.pyc')
        cached.rename(renamed)
        cached = renamed
    elif change == 'bad-magic':
        cached.write_bytes(b'XX\r\n' + cached.read_bytes()[4:])
    elif change == 'header-only':
        cached.write_bytes(cached.read_bytes()[:16])
    else:
        # A cache unrelated to the installed graph has no tracked-content
        # proof even though the real recorded graph still verifies.
        direct, = project.glob('.venv/lib/python*/site-packages/*/direct_url.json')
        direct.write_text(json.dumps({'url': 'https://example.invalid/fixture',
                                     'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40}}))
        (folder / 'runtime-installed.json').write_text(json.dumps(runtime_qualification.installed_graph(project)))
        runtime_qualification.verify_recorded(project, folder)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert folder.exists() and cached.exists()


async def test_subdirectory_install_proof_covers_tracked_siblings(tmp_path, monkeypatch):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch, subdirectory=True)
    graph = json.loads((folder / 'runtime-installed.json').read_text())
    assert graph[0]['path'] == str(source / 'package')
    assert graph[0]['cacheSource']['subdirectory'] == 'package'
    assert [row['id'] for row in (await retention.reclaim(manager, inspect_only=True))['candidates']] == [names[3]]
    # The real graph digest is rooted above the package subdirectory. Changing
    # this tracked sibling invalidates the recorded source, not just Git status.
    (source / 'fixture.py').write_text('VALUE = 99\n')
    with pytest.raises(ValueError):
        runtime_qualification.verify_recorded(project, folder)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert cached.exists() and folder.exists()


@pytest.mark.parametrize('guard', ['lease', 'configuration', 'process', 'rollback'])
async def test_import_cache_does_not_waive_generation_protection(tmp_path, monkeypatch, guard):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if guard == 'lease':
        from amplifier_web.generation_leases import acquire
        acquire(manager.home, names[3], os.getpid())
    elif guard == 'configuration':
        manager.service.state['referenced'] += str(folder)
    elif guard == 'process':
        monkeypatch.setattr(retention, 'process_references', lambda home: str(folder))
    else:
        (manager.directory / 'active.json').write_text(json.dumps({'current': names[0], 'previous': names[3]}))
    assert (await retention.reclaim(manager))['removed'] == 0
    assert cached.exists()


@pytest.mark.parametrize('mode', ['preview', 'selected', 'automatic'])
@pytest.mark.parametrize('change', ['cache', 'source', 'unknown'])
async def test_post_proof_edits_cannot_become_the_retirement_baseline(tmp_path, monkeypatch, mode, change):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    probe = retention._probe
    changed = {}
    async def race(operation, values, **kwargs):
        result = await probe(operation, values, **kwargs)
        if operation == 'bytecode' and result is True and not changed:
            target = {'cache': cached, 'source': source / 'fixture.py', 'unknown': source / 'private-notes.txt'}[change]
            contents = b'VALUE = 99\n' if change == 'source' else b'preserve post-proof work'
            target.write_bytes(contents)
            changed.update(path=target, contents=contents)
        return result
    monkeypatch.setattr(retention, '_probe', race)
    options = {'inspect_only': True} if mode == 'preview' else {'selected': {names[3]}} if mode == 'selected' else {}
    result = await retention.reclaim(manager, **options)
    assert changed and result['removed'] == 0
    assert not result.get('candidates')
    assert folder.exists() and changed['path'].read_bytes() == changed['contents']
    assert not list((manager.directory / 'retired').glob('*/payload'))


async def test_post_proof_same_size_edit_with_restored_mtime_stays_protected(tmp_path, monkeypatch):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    target = source / 'fixture.py'
    original = target.stat()
    probe = retention._probe
    changed = False
    async def race(operation, values, **kwargs):
        nonlocal changed
        result = await probe(operation, values, **kwargs)
        if operation == 'bytecode' and result is True and not changed:
            target.write_bytes(b'VALUE = 99\n')
            os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
            assert target.stat().st_size == original.st_size
            assert target.stat().st_mtime_ns == original.st_mtime_ns
            changed = True
        return result
    monkeypatch.setattr(retention, '_probe', race)
    result = await retention.reclaim(manager, selected={names[3]})
    assert changed and result['removed'] == 0
    assert folder.exists() and target.read_bytes() == b'VALUE = 99\n'


def unique_runtime(manager, folder, project):
    unique = project.with_name('q-' + 'b' * 32)
    shutil.copytree(project, unique)
    (folder / 'runtime-project.json').write_text(json.dumps({'project': unique.name}))
    return unique


async def test_unchanged_unique_runtime_retires_with_the_verified_generation(tmp_path, monkeypatch):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    unique = unique_runtime(manager, folder, project)
    result = await retention.reclaim(manager, selected={names[3]})
    assert result['removed'] == 1
    assert not folder.exists() and not unique.exists() and project.exists()


@pytest.mark.parametrize('timing', ['after-proof', 'after-admission-measure'])
@pytest.mark.parametrize('direction', ['becomes-shared', 'becomes-unique'])
async def test_runtime_sharing_change_defers_the_entire_candidate(tmp_path, monkeypatch, timing, direction):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    target = unique_runtime(manager, folder, project) if direction == 'becomes-shared' else project
    probe = retention._probe
    proved = changed = False
    async def race(operation, values, **kwargs):
        nonlocal proved, changed
        result = await probe(operation, values, **kwargs)
        if operation == 'bytecode' and result is True:
            proved = True
        boundary = (operation == 'bytecode' if timing == 'after-proof' else operation == 'measure')
        if proved and boundary and not changed:
            if direction == 'becomes-shared':
                rows, destination = names[:1], target.name
            else:
                rows, destination = names[:3], 'q-' + 'c' * 32
            for name in rows:
                (manager.directory / 'releases' / name / 'runtime-project.json').write_text(json.dumps({'project': destination}))
            changed = True
        return result
    monkeypatch.setattr(retention, '_probe', race)
    result = await retention.reclaim(manager, selected={names[3]})
    assert proved and changed and result['removed'] == 0
    assert folder.exists() and target.exists() and cached.exists()


async def test_timestamp_preserving_edit_at_retirement_is_quarantined(tmp_path, monkeypatch):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    target = source / 'fixture.py'
    original = target.stat()
    retire = retention.retire
    def race(*args, **kwargs):
        target.write_bytes(b'VALUE = 99\n')
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
        return retire(*args, **kwargs)
    monkeypatch.setattr(retention, 'retire', race)
    result = await retention.reclaim(manager, selected={names[3]})
    assert result['removed'] == 0
    kept = list((manager.directory / 'retired').glob('*/payload/release/foundation/cache/fixture/fixture.py'))
    assert len(kept) == 1 and kept[0].read_bytes() == b'VALUE = 99\n'
    assert (await retention.reclaim(manager))['removed'] == 0
    assert kept[0].exists() and project.exists()


def receipt_only_cache(source, project, folder):
    """A bundle can be imported without being an installed distribution."""
    direct, = project.glob('.venv/lib/python*/site-packages/*/direct_url.json')
    direct.unlink()
    metadata = json.loads((source / '.amplifier_cache_meta.json').read_text())
    metadata['commit'] = git(source, 'rev-parse', 'HEAD')
    (source / '.amplifier_cache_meta.json').write_text(json.dumps(metadata))
    (folder / 'runtime-installed.json').write_text(json.dumps(runtime_qualification.installed_graph(project)))


async def test_uninstalled_bundle_bytecode_uses_recorded_source_proof(tmp_path, monkeypatch):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    receipt_only_cache(source, project, folder)
    assert not retention._verified_cache(source, json.loads((folder / 'runtime-installed.json').read_text()))
    assert [r['id'] for r in (await retention.reclaim(manager, inspect_only=True))['candidates']] == [names[3]]
    assert cached.exists()
    assert (await retention.reclaim(manager, selected={names[3]}))['removed'] == 1
    assert not folder.exists()


@pytest.mark.parametrize('change', ['assume-unchanged', 'skip-worktree', 'staged', 'wrong-commit',
                                   'no-remote', 'local-head', 'hidden-non-python', 'executable-bit'])
async def test_receipt_only_source_cannot_hide_user_changes(tmp_path, monkeypatch, change):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if change == 'hidden-non-python':
        (source / 'notes.txt').write_text('published notes')
        git(source, 'add', 'notes.txt')
        git(source, 'commit', '-qm', 'Published notes')
        git(source, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    receipt_only_cache(source, project, folder)
    if change in {'assume-unchanged', 'skip-worktree'}:
        git(source, 'update-index', '--' + change, 'fixture.py')
        (source / 'fixture.py').write_text('VALUE = 99\n')
        assert not git(source, 'status', '--porcelain', '--untracked-files=no')
    elif change == 'hidden-non-python':
        git(source, 'update-index', '--assume-unchanged', 'notes.txt')
        (source / 'notes.txt').write_text('private notes')
    elif change in {'staged', 'local-head'}:
        (source / 'fixture.py').write_text('VALUE = 99\n')
        git(source, 'add', 'fixture.py')
        if change == 'local-head':
            git(source, 'commit', '-qm', 'Local work')
    elif change == 'wrong-commit':
        data = json.loads((source / '.amplifier_cache_meta.json').read_text())
        data['commit'] = '0' * 40
        (source / '.amplifier_cache_meta.json').write_text(json.dumps(data))
    elif change == 'no-remote':
        git(source, 'update-ref', '-d', 'refs/remotes/origin/main')
    elif change == 'executable-bit':
        if os.name == 'nt':
            pytest.skip('POSIX executable mode')
        git(source, 'update-index', '--assume-unchanged', 'fixture.py')
        (source / 'fixture.py').chmod(0o755)
    assert (await retention.reclaim(manager))['removed'] == 0
    assert folder.exists() and cached.exists()


def owning_interpreter(project, executable):
    python = project / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([executable, '-m', 'venv', '--without-pip', str(project / '.venv')], check=True)
    return python


@pytest.mark.parametrize('installed', [True, False])
async def test_generated_cache_from_other_supported_python_is_disposable(tmp_path, monkeypatch, installed):
    other = os.environ.get('RETENTION_OTHER_PYTHON')
    if not other:
        pytest.skip('Qualification supplies an independently installed second Python minor')
    assert subprocess.check_output([other, '-I', '-S', '-c', 'import sys; print(sys.implementation.cache_tag)'],
                                   text=True).strip() != sys.implementation.cache_tag
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if not installed:
        receipt_only_cache(source, project, folder)
    owning_interpreter(project, other)
    cached.unlink()
    # Real child-generated cache; its source must never execute during cleanup.
    subprocess.run([other, '-I', '-S', '-c',
                    'import py_compile, sys; py_compile.compile(sys.argv[1], doraise=True)',
                    str(source / 'fixture.py')], check=True)
    foreign, = (source / '__pycache__').glob('*.pyc')
    assert sys.implementation.cache_tag not in foreign.name
    preview = await retention.reclaim(manager, inspect_only=True)
    assert [r['id'] for r in preview['candidates']] == [names[3]]
    assert foreign.exists()
    assert (await retention.reclaim(manager, selected={names[3]}))['removed'] == 1


@pytest.mark.parametrize('payload', ['different-code', 'appended-payload', 'damaged-code'])
async def test_conventional_cache_payload_is_disposable_without_execution(tmp_path, monkeypatch, payload):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    marker = tmp_path / 'must-not-execute'
    if payload == 'different-code':
        cached.write_bytes(cached.read_bytes()[:16] + marshal.dumps(
            compile(f"from pathlib import Path; Path({str(marker)!r}).touch()", str(source / 'fixture.py'), 'exec')))
    elif payload == 'appended-payload':
        cached.write_bytes(cached.read_bytes() + b'hand edited cache bytes are disposable')
    else:
        cached.write_bytes(cached.read_bytes()[:16] + b'c')
    assert (await retention.reclaim(manager))['removed'] == 1
    assert not marker.exists()


async def test_supported_cache_tag_must_match_header(tmp_path, monkeypatch):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    other = 'cpython-313' if sys.implementation.cache_tag != 'cpython-313' else 'cpython-314'
    cached.rename(cached.with_name('fixture.' + other + '.pyc'))
    assert (await retention.reclaim(manager))['removed'] == 0
    assert folder.exists()
