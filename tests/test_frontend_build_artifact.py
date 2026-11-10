"""Failed asset parity retains repair output without granting release proof."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('frontend_build_artifact', ROOT / 'scripts/frontend_build_artifact.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c',
        'user.email=fixture@example.invalid', *args], cwd=root, stderr=subprocess.DEVNULL)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    (root / 'components/web-client').mkdir(parents=True)
    (root / module.STATIC).mkdir(parents=True)
    (root / 'components/web-client/input.js').write_text('source')
    for name in ('tests/web/package.json', 'components/web-client.source.json', 'scripts/web_client_build.py'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')
    (root / 'package.json').write_text('{}')
    (root / '.gitignore').write_text('.env*\n.build/\n')
    (root / module.STATIC / 'old.js').write_text('committed output')
    git(root, 'init', '-q'); git(root, 'add', '.'); git(root, 'commit', '-qm', 'source')
    (root / module.STATIC / 'old.js').unlink()
    (root / module.STATIC / 'new.js').write_text('new output')
    return root


def capture(root, tmp_path):
    output = tmp_path / 'build-output'
    manifest = module.create(root, output, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2'})
    return output, manifest


def test_generated_changes_are_retained_for_exact_source_without_qualifying_it(source, tmp_path):
    output, manifest = capture(source, tmp_path)
    assert module.verify(source, output) == manifest
    assert manifest['qualified'] is False and manifest['workflow']['GITHUB_RUN_ID'] == '123'
    assert set(manifest['assets']) == {'new.js'}
    assert (output / 'static/new.js').read_text() == 'new output'
    assert not (output / 'components/web-client').exists()
    # Committing only the recovered assets changes HEAD but not build inputs.
    git(source, 'add', '.'); git(source, 'commit', '-qm', 'assets')
    assert module.verify(source, output) == manifest


@pytest.mark.parametrize('change', ['source-dirty', 'source-committed', 'new-source', 'version',
                                    'environment', 'client-environment', 'host-environment'])
def test_changed_build_inputs_cannot_reuse_old_output(source, tmp_path, change):
    output, _ = capture(source, tmp_path)
    path = '.build/web-client/.env.local' if change == 'client-environment' else ('tests/web/.env.local' if change == 'host-environment' else (
        'package.json' if change == 'version' else '.env.local' if change == 'environment' else (
        'components/web-client/new.js' if change == 'new-source' else 'components/web-client/input.js'))
    )
    (source / path).parent.mkdir(parents=True, exist_ok=True)
    (source / path).write_text('different')
    if change == 'source-committed':
        git(source, 'add', '.'); git(source, 'commit', '-qm', 'new source')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module.verify(source, output)


@pytest.mark.parametrize('change', ['modified', 'missing', 'extra', 'extra-root', 'traversal', 'qualified', 'link', 'directory-link'])
def test_corrupt_or_redirected_artifact_is_rejected_without_writing_source(source, tmp_path, change):
    output, manifest = capture(source, tmp_path)
    before = (source / module.STATIC / 'new.js').read_bytes()
    asset = output / 'static/new.js'
    if change == 'modified': asset.write_text('other bytes')
    elif change == 'missing': asset.unlink()
    elif change == 'extra': (output / 'static/extra.js').write_text('extra')
    elif change == 'extra-root': (output / 'script.py').write_text('not executed')
    elif change == 'traversal':
        manifest['assets']['../escape'] = manifest['assets'].pop('new.js')
    elif change == 'qualified': manifest['qualified'] = True
    elif change == 'link':
        asset.unlink(); asset.symlink_to(source / 'components/web-client/input.js')
    elif change == 'directory-link':
        (output / 'static/external').symlink_to(source / 'components/web-client', target_is_directory=True)
    (output / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError): module.verify(source, output)
    assert (source / module.STATIC / 'new.js').read_bytes() == before


def test_ignored_environment_inputs_are_hashed_not_exported(source, tmp_path):
    (source / '.env.local').write_text('VITE_LABEL=fixture-private-input')
    output, manifest = capture(source, tmp_path)
    assert '.env.local' in manifest['source']['envFiles']
    assert 'fixture-private-input' not in (output / 'manifest.json').read_text()
    assert module.verify(source, output) == manifest


def test_capture_refuses_dirty_source_and_leaves_no_partial_bundle(source, tmp_path):
    (source / 'components/web-client/input.js').write_text('uncommitted source')
    with pytest.raises(subprocess.CalledProcessError): capture(source, tmp_path)
    assert not (tmp_path / 'build-output').exists()


def test_capture_refuses_links_and_oversize_before_publishing(source, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'MAX_BYTES', 2)
    with pytest.raises(ValueError, match='size limit'): capture(source, tmp_path)
    assert not (tmp_path / 'build-output').exists()
    monkeypatch.setattr(module, 'MAX_BYTES', 10000)
    (source / module.STATIC / 'external').symlink_to(source / 'components/web-client', target_is_directory=True)
    with pytest.raises(ValueError, match='Linked'): capture(source, tmp_path)


def test_bundle_capture_does_not_overwrite_previous_output(source, tmp_path):
    output, manifest = capture(source, tmp_path)
    with pytest.raises(ValueError, match='already exists'): module.create(source, output, {})
    assert module.verify(source, output) == manifest


def test_workflow_retains_build_before_parity_without_weakening_proof():
    workflow = yaml.load((ROOT / '.github/workflows/work-workflows.yml').read_text(), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['integration']['steps']
    names = [step.get('name') for step in steps]
    build = next(step for step in steps if step.get('name') == 'Build frontend')
    assert build['id'] == 'frontend-build'
    for name in ('Retain frontend build output', 'Upload frontend build output'):
        step = next(step for step in steps if step.get('name') == name)
        assert step['if'] == "${{ !cancelled() && steps.frontend-build.outcome == 'success' }}"
        assert names.index('Build frontend') < names.index(name) < names.index('Verify committed frontend assets')
    parity = next(step for step in steps if step.get('name') == 'Verify committed frontend assets')
    assert parity['run'].splitlines() == ['npm run test:artifact-assets --prefix .build/web-client', 'git diff --exit-code -- amplifier_web/static']
    assert 'continue-on-error' not in parity
    proof = next(step for step in steps if step.get('name') == 'Record frontend proof')
    assert 'if' not in proof and names.index(proof['name']) > names.index(parity['name'])
    assert 'frontend-build-output' not in (ROOT / 'scripts/frontend_release_proof.py').read_text()


@pytest.fixture
def importable(source, tmp_path):
    # Include a directory and several originals to exercise partially reaped trees.
    git(source, 'restore', '--worktree', '--source=HEAD', '--', module.STATIC)
    (source / module.STATIC / 'new.js').unlink()
    (source / module.STATIC / 'nested').mkdir()
    (source / module.STATIC / 'nested/obsolete.js').write_text('obsolete')
    git(source, 'add', '.'); git(source, 'commit', '-qm', 'original assets')
    original = module.tree(source / module.STATIC)
    (source / module.STATIC / 'old.js').unlink()
    (source / module.STATIC / 'nested/obsolete.js').unlink()
    (source / module.STATIC / 'nested/new.js').write_text('replacement')
    output, manifest = capture(source, tmp_path)
    (source / module.STATIC / 'nested/new.js').unlink()
    git(source, 'restore', '--worktree', '--source=HEAD', '--', module.STATIC)
    return source, output, manifest, original


def recovery_path(source):
    return Path(git(source, 'rev-parse', '--absolute-git-dir').decode().strip()) / 'frontend-build-apply'


def test_apply_prunes_obsolete_assets_without_staging_or_qualifying(importable):
    source, output, manifest, original = importable
    index = git(source, 'ls-files', '--stage', '-z')
    inputs = module.source_identity(source)
    result, summary = module.apply(source, output)
    assert result == manifest and result['qualified'] is False
    assert summary == {'status': 'applied', 'recovered': False, 'added': 1, 'removed': 2, 'changed': 0}
    assert module.tree(source / module.STATIC) == module.asset_tree(manifest['assets'])
    assert git(source, 'ls-files', '--stage', '-z') == index
    assert module.source_identity(source) == inputs
    assert module.verify(source, output) == manifest
    assert not recovery_path(source).exists()
    # No-op must preserve even staged generated output, with no new recovery tree.
    git(source, 'add', module.STATIC)
    index = git(source, 'ls-files', '--stage', '-z')
    assert module.apply(source, output)[1] == {'status': 'unchanged', 'recovered': False}
    assert git(source, 'ls-files', '--stage', '-z') == index
    assert not recovery_path(source).exists()


@pytest.mark.parametrize('change', ['modified', 'staged', 'untracked', 'ignored', 'empty-directory', 'assume-unchanged', 'skip-worktree'])
def test_apply_preserves_local_generated_work(importable, change):
    source, output, _, _ = importable
    target = source / module.STATIC
    if change in {'assume-unchanged', 'skip-worktree'}:
        git(source, 'update-index', '--' + change, module.STATIC + '/old.js')
        (target / 'old.js').write_text('hidden local work')
    elif change in {'modified', 'staged'}:
        (target / 'old.js').write_text('local work')
        if change == 'staged':
            git(source, 'add', module.STATIC)
            # Worktree equals HEAD but the index still has valuable changes.
            git(source, 'restore', '--source=HEAD', '--worktree', '--', module.STATIC)
    elif change == 'empty-directory':
        (target / 'notes').mkdir()
    else:
        (target / 'local.txt').write_text('local work')
        if change == 'ignored':
            (source / '.git/info/exclude').write_text('local.txt\n')
    before, index = module.tree(target), git(source, 'ls-files', '--stage', '-z')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module.apply(source, output)
    assert module.tree(target) == before
    assert git(source, 'ls-files', '--stage', '-z') == index
    assert not recovery_path(source).exists()


@pytest.mark.parametrize('change', ['source', 'environment', 'artifact', 'overlap', 'parent-link', 'recovery-link'])
def test_apply_preflight_does_not_move_originals(importable, tmp_path, change):
    source, output, _, original = importable
    if change == 'source': (source / 'components/web-client/input.js').write_text('changed')
    elif change == 'environment': (source / '.env.local').write_text('changed')
    elif change == 'artifact': (output / 'static/nested/new.js').write_text('changed')
    elif change == 'overlap':
        moved = source / module.STATIC / 'artifact'
        output.rename(moved); output = moved
        original = module.tree(source / module.STATIC)
    elif change == 'parent-link':
        (source / 'amplifier_web').rename(tmp_path / 'outside')
        (source / 'amplifier_web').symlink_to(tmp_path / 'outside', target_is_directory=True)
    elif change == 'recovery-link': recovery_path(source).symlink_to(output, target_is_directory=True)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module.apply(source, output)
    assert module.tree(source / module.STATIC) == original


# Kill a separate importer at real filesystem boundaries. Retry uses the public
# CLI, with no fault injection or test-only switches in production code.
CRASH_IMPORT = '''
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('artifact', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
point = sys.argv[4]
rename, copy, remove, replace = Path.rename, m.shutil.copyfile, m.shutil.rmtree, Path.replace
def crash_rename(self, target):
    result = rename(self, target)
    if (point == 'old-moved' and Path(target).name == 'old'
            or point == 'new-moved' and self.name == 'new'):
        os._exit(73)
    return result
def crash_copy(src, dst, *args, **kwargs):
    if point == 'copy':
        Path(dst).write_bytes(b'partial')
        os._exit(73)
    return copy(src, dst, *args, **kwargs)
def crash_remove(path, *args, **kwargs):
    if point == 'cleanup' and Path(path).name == 'old':
        next(Path(path).rglob('*.js')).unlink()
        os._exit(73)
    return remove(path, *args, **kwargs)
def crash_record(self, target):
    if point == 'initial-record': os._exit(73)
    return replace(self, target)
Path.rename, Path.replace = crash_rename, crash_record
m.shutil.copyfile, m.shutil.rmtree = crash_copy, crash_remove
m.apply(Path(sys.argv[2]), Path(sys.argv[3]))
'''


def crash_apply(source, output, point):
    result = subprocess.run([sys.executable, '-c', CRASH_IMPORT, module.__file__, str(source), str(output), point],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr


@pytest.mark.parametrize('point', ['initial-record', 'copy', 'old-moved', 'new-moved', 'cleanup'])
def test_cli_recovers_killed_import_without_losing_originals(importable, point):
    source, output, manifest, original = importable
    index = git(source, 'ls-files', '--stage', '-z')
    crash_apply(source, output, point)
    recovery = recovery_path(source)
    if point == 'old-moved':
        assert not (source / module.STATIC).exists()
        assert module.tree(recovery / 'old') == original
    result = subprocess.run([sys.executable, module.__file__, 'apply', '--directory', str(output)],
                            cwd=source, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['qualified'] is False and receipt['workflowProvenanceVerified'] is False
    assert receipt['application']['recovered'] == (point != 'initial-record')
    assert module.tree(source / module.STATIC) == module.asset_tree(manifest['assets'])
    assert git(source, 'ls-files', '--stage', '-z') == index
    assert not recovery.exists()


@pytest.mark.parametrize('change', ['target', 'backup', 'index', 'source', 'artifact'])
def test_recovery_preserves_conflicting_work(importable, change):
    source, output, _, _ = importable
    crash_apply(source, output, 'new-moved')
    recovery = recovery_path(source)
    if change == 'target': (source / module.STATIC / 'nested/new.js').write_text('new local work')
    elif change == 'backup': (recovery / 'old/old.js').write_text('saved local work')
    elif change == 'index': git(source, 'add', module.STATIC)
    elif change == 'source': (source / 'components/web-client/input.js').write_text('source work')
    elif change == 'artifact': (output / 'static/nested/new.js').write_text('artifact work')
    before, saved = module.tree(source / module.STATIC), module.tree(recovery / 'old')
    index = git(source, 'ls-files', '--stage', '-z')
    with pytest.raises((ValueError, subprocess.CalledProcessError)) as caught:
        module.apply(source, output)
    assert str(recovery) in '\n'.join(caught.value.__notes__)
    # When only the index changed, restoring the original would move staged
    # work away from its matching tree. Recovery must leave both in place.
    assert module.tree(source / module.STATIC) == before
    assert module.tree(recovery / 'old') == saved
    assert git(source, 'ls-files', '--stage', '-z') == index


def test_failed_move_restores_original_and_can_retry(importable, monkeypatch):
    source, output, manifest, original = importable
    rename = Path.rename
    def fail_new(self, destination):
        if self == recovery_path(source) / 'new': raise OSError('simulated rename failure')
        return rename(self, destination)
    with monkeypatch.context() as context:
        context.setattr(Path, 'rename', fail_new)
        with pytest.raises(OSError, match='simulated'): module.apply(source, output)
    assert module.tree(source / module.STATIC) == original
    assert module.apply(source, output)[1]['recovered'] is True
    assert module.tree(source / module.STATIC) == module.asset_tree(manifest['assets'])


def test_apply_refuses_overlapping_invocation(importable):
    source, output, _, original = importable
    with module.apply_lock(recovery_path(source).with_suffix('.lock')):
        result = subprocess.run([sys.executable, module.__file__, 'apply', '--directory', str(output)],
                                cwd=source, capture_output=True, text=True, timeout=30)
        assert result.returncode != 0
    assert module.tree(source / module.STATIC) == original
    assert not recovery_path(source).exists()


def test_linked_worktree_recovers_using_its_own_git_metadata(importable, tmp_path):
    source, output, manifest, original = importable
    linked = tmp_path / 'linked'
    git(source, 'worktree', 'add', '--detach', str(linked), 'HEAD')
    try:
        crash_apply(linked, output, 'old-moved')
        assert recovery_path(linked) != recovery_path(source)
        assert not recovery_path(source).exists()
        assert module.apply(linked, output)[1]['recovered'] is True
        assert module.tree(linked / module.STATIC) == module.asset_tree(manifest['assets'])
        assert module.tree(source / module.STATIC) == original
    finally:
        # This test owns the generated diff and has asserted its full inventory.
        git(source, 'worktree', 'remove', '--force', str(linked))


@pytest.mark.parametrize('flag', ['assume-unchanged', 'skip-worktree'])
def test_hidden_authored_input_cannot_claim_exact_source(importable, tmp_path, flag):
    source, output, _, original = importable
    git(source, 'update-index', '--' + flag, 'components/web-client/input.js')
    (source / 'components/web-client/input.js').write_text('hidden authored changes')
    assert git(source, 'diff', 'HEAD', '--', 'components/web-client/input.js') == b''
    with pytest.raises(ValueError, match='Source index flags'):
        module.create(source, tmp_path / 'another-output', {})
    with pytest.raises(ValueError, match='Source index flags'):
        module.verify(source, output)
    with pytest.raises(ValueError, match='Source index flags'):
        module.apply(source, output)
    assert module.tree(source / module.STATIC) == original
    assert (source / 'components/web-client/input.js').read_text() == 'hidden authored changes'
    assert not recovery_path(source).exists()
