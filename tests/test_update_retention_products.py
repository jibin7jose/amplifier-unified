"""Disposable build output requires source proof, not directory-name guesses."""
import json
from pathlib import Path
import py_compile
import shutil

import pytest

from amplifier_web import update_retention as retention
from test_update_retention_bytecode import git, imported_generation, receipt_only_cache


def fetched_cache(tmp_path, source, project, folder, suffix=''):
    upstream = tmp_path / ('download-origin' + suffix.rstrip('/'))
    git(source, 'clone', '--bare', str(source), str(upstream))
    receipt_only_cache(source, project, folder)
    commit = git(source, 'rev-parse', 'HEAD')
    url = upstream.as_uri() + ('/' if suffix.endswith('/') else '')
    git(source, 'update-ref', '-d', 'refs/remotes/origin/main')
    git(source, 'fetch', '--depth=1', url, commit)
    git(source, 'checkout', '--detach', commit)
    metadata = json.loads((source / '.amplifier_cache_meta.json').read_text())
    metadata['git_url'] = url
    (source / '.amplifier_cache_meta.json').write_text(json.dumps(metadata))


@pytest.mark.parametrize('suffix', ['', '.git', '.git/'])
async def test_actual_detached_sha_download_is_proven_without_remote_branch(tmp_path, monkeypatch, suffix):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    fetched_cache(tmp_path, source, project, folder, suffix)
    assert not git(source, 'for-each-ref', 'refs/remotes')
    assert (await retention.reclaim(manager))['removed'] == 1
    assert not folder.exists() and project.exists()


@pytest.mark.parametrize('change', ['url', 'commit', 'symlink', 'source', 'unpublished-head'])
async def test_detached_download_proof_keeps_changed_or_unproven_source(tmp_path, monkeypatch, change):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    fetched_cache(tmp_path, source, project, folder)
    fetch = source / '.git/FETCH_HEAD'
    if change == 'url':
        fetch.write_text(fetch.read_text().replace('file://', 'https://'))
    elif change == 'commit':
        fetch.write_text('0' * 40 + fetch.read_text()[40:])
    elif change == 'symlink':
        outside = tmp_path / 'fetch-copy'
        fetch.rename(outside)
        fetch.symlink_to(outside)
    else:
        (source / 'fixture.py').write_text('VALUE = 43\n')
        if change == 'unpublished-head':
            git(source, 'add', 'fixture.py')
            git(source, 'commit', '-qm', 'Private work')
            metadata = json.loads((source / '.amplifier_cache_meta.json').read_text())
            metadata['commit'] = git(source, 'rev-parse', 'HEAD')
            (source / '.amplifier_cache_meta.json').write_text(json.dumps(metadata))
        else:
            git(source, 'update-index', '--assume-unchanged', 'fixture.py')
    assert (await retention.reclaim(manager))['removed'] == 0
    assert folder.exists() and cached.exists()


@pytest.mark.parametrize('layout', ['flat', 'src'])
async def test_exact_build_lib_copy_and_import_cache_are_disposable(tmp_path, monkeypatch, layout):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    if layout == 'src':
        (source / 'src').mkdir()
        shutil.copyfile(source / 'fixture.py', source / 'src/other.py')
        git(source, 'add', 'src/other.py')
        git(source, 'commit', '-qm', 'Published src layout')
        git(source, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        from amplifier_web import runtime_qualification
        (folder / 'runtime-installed.json').write_text(json.dumps(runtime_qualification.installed_graph(project)))
    relative = 'other.py' if layout == 'src' else 'fixture.py'
    original = source / ('src/' + relative if layout == 'src' else relative)
    built = source / 'build/lib' / relative
    built.parent.mkdir(parents=True)
    shutil.copy2(original, built)
    py_compile.compile(str(built), doraise=True)
    assert (await retention.reclaim(manager))['removed'] == 1
    assert not folder.exists() and project.exists()


@pytest.mark.parametrize('change', ['edited-copy', 'unknown-file', 'symlink', 'private-metadata', 'private-env'])
async def test_build_names_do_not_authorize_unique_or_changed_content(tmp_path, monkeypatch, change):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    built = source / 'build/lib/fixture.py'
    built.parent.mkdir(parents=True)
    shutil.copy2(source / 'fixture.py', built)
    if change == 'edited-copy':
        built.write_text('VALUE = 99\n')
    elif change == 'symlink':
        built.unlink()
        built.symlink_to(source / 'fixture.py')
    else:
        relative = {'unknown-file': 'build/lib/private.txt', 'private-metadata': 'fixture.egg-info/PKG-INFO',
                    'private-env': '.venv/pyvenv.cfg'}[change]
        extra = source / relative
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text('Unique work must not be deleted.')
    preview = await retention.reclaim(manager, inspect_only=True)
    assert not preview['candidates']
    assert preview['retentionReasons'][names[3]] == 'unverified_generated_files'
    assert folder.exists() and built.exists()


async def test_missing_cache_proof_has_a_bounded_reason(tmp_path, monkeypatch):
    manager, names, project, folder, source, cached = imported_generation(tmp_path, monkeypatch)
    receipt_only_cache(source, project, folder)
    git(source, 'update-ref', '-d', 'refs/remotes/origin/main')
    preview = await retention.reclaim(manager, inspect_only=True)
    assert not preview['candidates']
    assert preview['retentionReasons'][names[3]] == 'unverified_source'
    assert str(source) not in json.dumps(preview['retentionReasons'])
