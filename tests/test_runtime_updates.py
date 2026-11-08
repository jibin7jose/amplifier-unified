"""A worker lock must advance through Updates and survive a rollback unchanged."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web.runtime import RuntimeManager
from amplifier_web.updates import active_release


@pytest.mark.parametrize('unsafe', [False, True])
def test_windows_prepare_project_routes_creation_and_payload_through_pins(tmp_path, monkeypatch, unsafe):
    from contextlib import contextmanager
    from amplifier_web import windows_files
    home = tmp_path / 'app'
    project = home / 'runtime' / 'candidate'
    receipt = tmp_path / 'recorded.lock'
    receipt.write_bytes(b'version = 1\n')
    writes, objects = [], {}
    @contextmanager
    def directory(path, *, owned_root, create=False):
        assert owned_root == home
        if unsafe:
            raise PermissionError('unsafe existing directory')
        class Pinned:
            def read_private(self, name, *, limit):
                assert limit == 1024 * 1024
                if (path, name) not in objects:
                    raise FileNotFoundError(name)
                return objects[path, name]
            def publish_private(self, name, value, *, replace=False):
                writes.append((path, name, value, replace))
                objects[path, name] = value
        yield Pinned()
    monkeypatch.setattr(environments, '_WINDOWS', True)
    monkeypatch.setattr(environments, 'project_path', lambda *args, **kwargs: project)
    monkeypatch.setattr(environments, 'receipt_directory', lambda *args: receipt.parent)
    # Its explicit recorded path is runtime.lock.
    (receipt.parent / 'runtime.lock').write_bytes(receipt.read_bytes())
    objects[receipt.parent, 'runtime.lock'] = receipt.read_bytes()
    monkeypatch.setattr(windows_files, 'directory', directory)
    monkeypatch.setattr(Path, 'mkdir', lambda *args, **kwargs: pytest.fail('ordinary mkdir before private creation'))
    if unsafe:
        with pytest.raises(PermissionError):
            environments.prepare_project(home, content=b'[project]\n')
        assert not writes
    else:
        assert environments.prepare_project(home, content=b'[project]\n') == project
        assert writes == [(project, 'pyproject.toml', b'[project]\n', True),
                          (project, 'uv.lock', b'version = 1\n', False)]
        assert environments.prepare_project(home, content=b'[project]\n') == project
        assert len(writes) == 2
        objects[project, 'uv.lock'] = b'changed'
        with pytest.raises(ValueError, match='recorded worker lock changed'):
            environments.prepare_project(home, content=b'[project]\n')
        assert objects[project, 'uv.lock'] == b'changed'


@pytest.mark.skipif(__import__('os').name != 'nt', reason='native Windows runtime private files')
def test_native_windows_prepare_project_private_before_payload_and_refuse_unsafe(tmp_path):
    from amplifier_web import windows_files
    import uuid
    home = tmp_path.resolve() / ('runtime-app-' + uuid.uuid4().hex)
    project = environments.prepare_project(home, content=b'[project]\nname="native-fixture"\n')
    with windows_files.directory(project, owned_root=home) as pinned:
        assert pinned.read_private('pyproject.toml', limit=1024) == b'[project]\nname="native-fixture"\n'
    # Ordinary existing creation is not promoted to private or repaired.
    unsafe = tmp_path.resolve() / ('ordinary-' + uuid.uuid4().hex)
    unsafe.mkdir()
    # Set an explicitly readable ACL (not an ownership-dependent mkdir guess).
    from amplifier_web.windows_files import _API
    import ctypes
    api = _API()
    descriptor = ctypes.c_void_p()
    api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f'O:{api.sid}D:P(A;;FA;;;{api.sid})(A;;FR;;;WD)', 1,
        ctypes.byref(descriptor), None))
    try:
        # This is synthetic fixture setup, never repair of an application root.
        set_security = ctypes.WinDLL('advapi32', use_last_error=True).SetFileSecurityW
        set_security.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
        set_security.restype = ctypes.c_int32
        api.ok(set_security(str(unsafe), 4 | 0x80000000, descriptor))
    finally:
        api.LocalFree(descriptor)
    with pytest.raises(PermissionError):
        environments.prepare_project(unsafe, content=b'must-not-publish')
    assert not (unsafe / 'runtime').exists()


def git(path, *args):
    return subprocess.check_output(['git', *args], cwd=path, text=True, stderr=subprocess.DEVNULL).strip()


class Diagnostics:
    def record(self, *args, **kwargs):
        pass

    async def run(self, name, function, *args, **kwargs):
        return await function(*args, **kwargs)


_FIXTURE_BUILD_BACKEND = r'''
"""Build the metadata-only fixture distributions without external build tools."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import re
import tomllib
import zipfile


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    name, version = project["name"], project["version"]
    normalized = re.sub(r"[-_.]+", "_", name)
    dist_info = f"{normalized}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.3", f"Name: {name}", f"Version: {version}",
    ]
    if "requires-python" in project:
        metadata.append("Requires-Python: " + project["requires-python"])
    metadata.extend("Requires-Dist: " + value for value in project.get("dependencies", []))
    files = {
        dist_info + "/METADATA": ("\n".join(metadata) + "\n\n").encode("utf-8"),
        dist_info + "/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: runtime-test-fixture\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n\n"
        ).encode("utf-8"),
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for path, payload in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
        writer.writerow((path, "sha256=" + digest, str(len(payload))))
    record_path = dist_info + "/RECORD"
    writer.writerow((record_path, "", ""))
    files[record_path] = record.getvalue().encode("utf-8")
    filename = f"{normalized}-{version}-py3-none-any.whl"
    destination = Path(wheel_directory)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination / filename, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for path, payload in files.items():
            wheel.writestr(path, payload)
    return filename


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    # These fixtures have no importable code: the metadata-only wheel is also
    # their complete editable payload. uv owns the real direct_url.json.
    return build_wheel(wheel_directory, config_settings, metadata_directory)
'''


def write_fixture_project(path, name, dependencies=()):
    """Keep real PEP 517/660 builds and dependency metadata entirely offline."""
    (path / '_fixture_backend.py').write_text(_FIXTURE_BUILD_BACKEND, encoding='utf-8')
    (path / 'pyproject.toml').write_text(
        '[project]\nname=' + json.dumps(name) + '\nversion="0.1.0"\n'
        'dependencies=' + json.dumps(list(dependencies)) + '\n'
        '[build-system]\nrequires=[]\nbuild-backend="_fixture_backend"\nbackend-path=["."]\n',
        encoding='utf-8',
    )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    # This fixture exercises one synthetic Git dependency, independently of the
    # real support packs installed in the interpreter running the test suite.
    # uv forbids --refresh-package together with UV_OFFLINE even for file://
    # repositories. Permit local Git refresh, but keep registry access disabled;
    # the fixture backend has no build requirements or network dependencies.
    monkeypatch.setenv('UV_OFFLINE', 'false')
    monkeypatch.setenv('UV_NO_INDEX', 'true')
    monkeypatch.setattr('amplifier_web.app_updates.installed_extras', lambda: [])
    repo = tmp_path / 'remote'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    write_fixture_project(repo, 'fixture-runtime')
    (repo / 'marker.txt').write_text('first')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'first')
    old = git(repo, 'rev-parse', 'HEAD')
    manifest = tmp_path / 'pyproject.toml'
    manifest.write_text('[project]\nname="fixture-worker"\nversion="0.1.0"\n'
                        'requires-python=">=3.13"\ndependencies=["fixture-runtime"]\n'
                        '[tool.uv.sources]\nfixture-runtime={git="' + repo.as_uri() + '",branch="main"}\n')
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    monkeypatch.setattr('amplifier_web.update_sequence.included_sources', lambda: ({'fixture-runtime'}, []))
    home = tmp_path / 'app'
    current = environments.prepare_project(home)
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('uv is required for the local Git lock integration check')
    subprocess.run([uv, 'lock', '--project', str(current), '--python', '3.13'], check=True, capture_output=True)
    (repo / 'marker.txt').write_text('second')
    git(repo, 'commit', '-am', 'second')
    new = git(repo, 'rev-parse', 'HEAD')
    row = {**environments.inventory(home)[0], 'status': 'update', 'latest': new}
    return SimpleNamespace(home=home, diagnostics=Diagnostics()), current, row, old, new, repo


def test_inventory_is_read_only_and_branches_are_not_pins(environment):
    manager, current, row, old, new, repo = environment
    before = (current / 'uv.lock').read_bytes()
    assert row['current'] == old and row['latest'] == new
    assert row['ref'] == 'main' and row['eligible']
    assert environments.inventory(manager.home)[0]['current'] == old
    assert (current / 'uv.lock').read_bytes() == before


async def test_update_refreshes_worker_lock_and_rollback_reuses_old_environment(environment):
    manager, current, row, old, new, repo = environment
    generation = 'a' * 32
    release = manager.home / 'updates/releases' / generation
    release.mkdir(parents=True)
    project = await environments.stage(manager, generation, [row])
    assert environments.locked_sources(project)['fixture-runtime'].fragment == new
    assert environments.locked_sources(current)['fixture-runtime'].fragment == old
    pointer = manager.home / 'updates/active.json'
    pointer.write_text(json.dumps({'current': generation, 'previous': None}))
    command = RuntimeManager()._command(home=manager.home)
    assert Path(command[command.index('--project') + 1]) == project
    pointer.write_text(json.dumps({'current': None, 'previous': generation}))
    command = RuntimeManager()._command(home=manager.home)
    assert Path(command[command.index('--project') + 1]) == current
    assert environments.inventory(manager.home)[0]['current'] == old
    # Revalidation must reuse its recorded lock even after the remote moves.
    (repo / 'marker.txt').write_text('third')
    git(repo, 'commit', '-am', 'third')
    await environments.stage(manager, generation, [{**row, 'latest': git(repo, 'rev-parse', 'HEAD')}])
    assert environments.locked_sources(project)['fixture-runtime'].fragment == new


async def test_app_manifest_change_cannot_rewrite_rollback_receipt(environment):
    manager, current, row, old, new, repo = environment
    generation = 'c' * 32
    release = manager.home / 'updates/releases' / generation
    release.mkdir(parents=True)
    project = await environments.stage(manager, generation, [row])
    recorded = (release / 'runtime.lock').read_bytes()
    manifest = (release / 'runtime.toml').read_bytes()
    # A new application declares a different branch/dependency contract.
    git(repo, 'checkout', '-b', 'next')
    (repo / 'marker.txt').write_text('next branch')
    git(repo, 'commit', '-am', 'next')
    path = environments.manifest_path()
    path.write_text(path.read_text().replace('branch="main"', 'branch="next"'))
    await environments.stage(manager, generation, [])
    assert (release / 'runtime.lock').read_bytes() == recorded
    assert (release / 'runtime.toml').read_bytes() == manifest
    assert environments.project_path(manager.home, generation) == project
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    assert environments.inventory(manager.home)[0]['kind'] == 'runtime environment'
    assert environments.inventory(manager.home)[0]['status'] == 'update'
    # Deleting a disposable worker cache cannot discard its retained version.
    shutil.rmtree(project)
    assert environments.inventory(manager.home)[1]['current'] == new
    command = RuntimeManager()._command(home=manager.home)
    assert Path(command[command.index('--project') + 1]) == project
    assert '--locked' in command
    assert (project / 'uv.lock').read_bytes() == recorded
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': None, 'previous': generation}))
    command = RuntimeManager()._command(home=manager.home)
    assert Path(command[command.index('--project') + 1]) == current
    assert environments.inventory(manager.home)[1]['current'] == old


async def test_branch_movement_after_check_does_not_activate(environment):
    manager, current, row, old, new, repo = environment
    generation = 'b' * 32
    (manager.home / 'updates/releases' / generation).mkdir(parents=True)
    (repo / 'marker.txt').write_text('third')
    git(repo, 'commit', '-am', 'third')
    with pytest.raises(ValueError, match='source resolved differently') as caught:
        await environments.stage(manager, generation, [row])
    assert caught.value.diagnostic_facts == {
        'reason': 'runtime-resolution-mismatch', 'component': 'fixture-runtime',
        'resolutionMismatches': ['revision'], 'expectedRevision': new,
        'observedRevision': git(repo, 'rev-parse', 'HEAD'),
    }
    assert not active_release(manager.home)
    assert environments.locked_sources(current)['fixture-runtime'].fragment == old
    assert not (environments.receipt_directory(manager.home, generation) / 'runtime.lock').exists()


async def test_uv_commit_override_does_not_pin_the_next_update(environment, monkeypatch):
    """Exercise real uv normalization, locked installation and frozen replay."""
    import tomllib
    from urllib.parse import parse_qs
    from amplifier_web.runtime_qualification import freeze, verify_recorded
    from amplifier_web.updates import process

    manager, current, row, old, new, repo = environment
    baseline = (current / 'uv.lock').read_bytes()
    original = environments.update_manifest

    async def exact_candidate(home):
        observed, content = await original(home)
        data = tomllib.loads(content.decode())
        data['tool']['uv']['sources'].pop('fixture-runtime')
        data['tool']['uv']['override-dependencies'] = [
            'fixture-runtime @ git+' + repo.as_uri() + '@' + new]
        return observed, ('\n'.join(json.dumps(key) + ' = ' + environments.toml_value(value)
                                    for key, value in data.items()) + '\n').encode()

    monkeypatch.setattr(environments, 'update_manifest', exact_candidate)
    generation = '9' * 32
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    prepared = await environments.stage(manager, generation, [row], finalize=False)
    resolved = environments.locked_sources(prepared)['fixture-runtime']
    assert parse_qs(resolved.query)['rev'] == [new] and resolved.fragment == new
    await process(shutil.which('uv'), 'sync', '--locked', '--project', str(prepared), timeout=90)
    final = await freeze(manager, generation, prepared)
    verify_recorded(final, receipt)
    assert (current / 'uv.lock').read_bytes() == baseline
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    installed = next(item for item in environments.inventory(manager.home)
                     if item.get('package') == row['package'])
    assert installed['ref'] == 'main' and installed['eligible'] and installed['current'] == new
    frozen = (receipt / 'runtime.lock').read_bytes()

    monkeypatch.setattr(environments, 'update_manifest', original)
    (repo / 'marker.txt').write_text('next branch update')
    git(repo, 'commit', '-am', 'next')
    latest = git(repo, 'rev-parse', 'HEAD')
    next_generation = '8' * 32
    environments.receipt_directory(manager.home, next_generation).mkdir(parents=True)
    next_project = await environments.stage(manager, next_generation, [{**installed, 'latest': latest}])
    assert environments.locked_sources(next_project)[row['package']].fragment == latest
    assert (receipt / 'runtime.lock').read_bytes() == frozen
    verify_recorded(final, receipt)


def test_cold_inventory_does_not_prepare_worker(tmp_path):
    assert environments.inventory(tmp_path) == []
    assert not (tmp_path / 'runtime').exists()


async def test_update_manager_installs_runtime_only_and_manifest_updates(environment, monkeypatch):
    from amplifier_web import app_updates
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    manager, current, row, old, new, repo = environment
    closed = []
    class Runtime:
        async def close(self):
            closed.append(True)
    async def app_check():
        return {'id': 'application', 'status': 'current'}
    monkeypatch.setattr(app_updates, 'check', app_check)
    app = AppService(manager.home, Runtime(), workspace=manager.home.parent)
    updater = UpdateManager(app)
    async def validate(stage, release):
        await environments.stage(updater, release, [r for r in updater.inventory if r.get('status') == 'update'])
    updater.validate = validate
    try:
        await updater.check()
        assert any(r['id'] == 'runtime:fixture-runtime' and r['status'] == 'update' for r in updater.inventory)
        await updater.install()
        assert app.state['updates']['phase'] == 'installed'
        assert environments.inventory(manager.home)[0]['current'] == new
        assert closed
        # A changed application manifest is a local update, not a Git URL.
        path = environments.manifest_path()
        path.write_text(path.read_text().replace('version="0.1.0"', 'version="0.1.1"'))
        await updater.check()
        assert any(r['id'] == 'runtime:environment' and r['status'] == 'update' for r in updater.inventory)
        await updater.install()
        assert app.state['updates']['phase'] == 'installed'
        await updater.rollback()
        assert any(r['kind'] == 'runtime environment' for r in environments.inventory(manager.home))
    finally:
        await app.close()


@pytest.fixture
def installed_transitive(environment, request):
    child_name = getattr(request, "param", "amplifier-fixture-child")
    manager, current, row, old, new, repo = environment
    remote = manager.home.parent / 'modules'
    remote.mkdir()
    git(remote, 'init', '-b', 'main')
    git(remote, 'config', 'user.name', 'Fixture')
    git(remote, 'config', 'user.email', 'fixture@example.invalid')
    child = remote / 'child'
    child.mkdir()
    write_fixture_project(child, child_name)
    git(remote, 'add', '.')
    git(remote, 'commit', '-m', 'child')
    parent = manager.home.parent / 'parent'
    parent.mkdir()
    git(parent, 'init', '-b', 'main')
    git(parent, 'config', 'user.name', 'Fixture')
    git(parent, 'config', 'user.email', 'fixture@example.invalid')
    write_fixture_project(parent, 'amplifier-fixture-parent', [
        child_name + ' @ git+' + remote.as_uri() + '@main#subdirectory=child'])
    git(parent, 'add', '.')
    git(parent, 'commit', '-m', 'parent')
    uv = shutil.which('uv')
    subprocess.run([uv, 'sync', '--project', str(current)], check=True, capture_output=True)
    subprocess.run([uv, 'pip', 'install', '--python', str(current / '.venv/bin/python'), 'git+' + parent.as_uri() + '@main'], check=True, capture_output=True)
    initial = git(remote, 'rev-parse', 'HEAD')
    (child / 'marker.txt').write_text('new child')
    git(remote, 'add', '.')
    git(remote, 'commit', '-m', 'new child')
    latest = git(remote, 'rev-parse', 'HEAD')
    child_row = next(row for row in environments.inventory(manager.home) if row.get('package') == child_name)
    return manager, current, {**child_row, 'status': 'update', 'latest': latest}, initial, latest, remote


@pytest.mark.parametrize("installed_transitive", ["amplifier-fixture-child", "component-child"], indirect=True)
async def test_actual_transitive_git_distribution_survives_staging_and_frozen_replay(installed_transitive):
    manager, current, row, old, new, repo = installed_transitive
    before = (current / 'uv.lock').read_bytes()
    assert row['current'] == old and row['ref'] == 'main' and row['subdirectory'] == 'child'
    assert row['provenance'] == 'installed Git distribution' and row['eligible']
    assert row['package'] not in environments.locked_sources(current)
    generation = 'd' * 32
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    project = await environments.stage(manager, generation, [row])
    assert environments.locked_sources(project)[row['package']].fragment == new
    assert 'amplifier-fixture-parent' in environments.locked_sources(project)
    assert '@main#subdirectory=child' in (project / 'pyproject.toml').read_text()
    assert (current / 'uv.lock').read_bytes() == before
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    assert environments.project_path(manager.home, generation) == project
    assert not any(row['kind'] == 'runtime environment' for row in environments.inventory(manager.home))
    frozen = {name: (receipt / name).read_bytes() for name in ('runtime.toml', 'runtime.lock', 'runtime-base.toml')}
    (repo / 'child/marker.txt').write_text('drift after qualification')
    git(repo, 'commit', '-am', 'later')
    await environments.stage(manager, generation, [row])
    assert {name: (receipt / name).read_bytes() for name in frozen} == frozen


async def test_installed_transitive_branch_drift_rejected(installed_transitive):
    manager, current, row, old, new, repo = installed_transitive
    generation = 'e' * 32
    environments.receipt_directory(manager.home, generation).mkdir(parents=True)
    (repo / 'child/marker.txt').write_text('moved since check')
    git(repo, 'commit', '-am', 'drift')
    with pytest.raises(ValueError, match='source resolved differently'):
        await environments.stage(manager, generation, [row])
    assert not active_release(manager.home)
    assert not (environments.receipt_directory(manager.home, generation) / 'runtime.lock').exists()


async def test_installed_provenance_changed_since_check_rejected(installed_transitive):
    manager, current, row, old, new, repo = installed_transitive
    direct = next((current / '.venv').glob('lib/python*/site-packages/amplifier_fixture_child-*.dist-info/direct_url.json'))
    metadata = json.loads(direct.read_text())
    metadata['vcs_info']['commit_id'] = new
    direct.write_text(json.dumps(metadata))
    generation = 'f' * 32
    environments.receipt_directory(manager.home, generation).mkdir(parents=True)
    with pytest.raises(ValueError, match='changed since checking'):
        await environments.stage(manager, generation, [row])
    assert not active_release(manager.home)


def test_local_override_conceals_no_locked_git_source_and_is_never_rewritten(environment):
    manager, current, row, old, new, repo = environment
    metadata = current / '.venv/lib/python3.13/site-packages/amplifier_local-1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Name: amplifier-local\nVersion: 1.0\n')
    (metadata / 'direct_url.json').write_text(json.dumps({'url': 'file:///private/user-worktree', 'dir_info': {'editable': True}}))
    local = next(row for row in environments.inventory(manager.home) if row.get('package') == 'amplifier-local')
    assert not local['eligible'] and local['override'] and local['status'] == 'local'
    before = environments.manifest_path().read_bytes()
    assert environments.augmented_manifest(before, [local]) == before
    assert '/private/' not in json.dumps(local)


async def test_freeze_captures_post_preparation_graph_and_protects_untyped_policy(installed_transitive):
    from amplifier_web.runtime_qualification import freeze, installed_graph, verify_recorded
    manager, current, row, old, new, repo = installed_transitive
    generation = '1' * 32
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    # The installed environment is the synthetic result of module preparation.
    before = installed_graph(current)
    final = await freeze(manager, generation, current)
    verify_recorded(final, receipt)
    assert current.exists()  # The baseline is a rollback target, not disposable.
    assert json.loads((receipt / 'runtime-installed.json').read_text()) == installed_graph(final)
    assert before and final != current
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    child = next(row for row in environments.inventory(manager.home) if row.get('package') == 'amplifier-fixture-child')
    assert child['current'] == old and child['ref'] == old and not child['eligible']
    assert child['policyOrigin']['kind'] == 'unresolved-reference'
    assert child['policyOrigin']['originalRef'] == 'main'
    refresh = next(row for row in environments.inventory(manager.home) if row['kind'] == 'runtime environment')
    assert refresh['current'] == 'legacy-policy' and refresh['eligible']
    assert refresh['label'] == 'Refresh included components'
    frozen = (receipt / 'runtime.lock').read_bytes()
    await environments.stage(manager, generation, [{**child, 'latest': new}])
    assert (receipt / 'runtime.lock').read_bytes() == frozen
    with pytest.raises(ValueError, match='cannot be refreshed'):
        await freeze(manager, generation, final)
    # This fixture's transitive @main carries no selected branch-kind proof.
    # Only selected-source reconciliation can grant future branch updates; a
    # freeze or cold reopen must not guess from the reference's familiar name.
    shutil.rmtree(final / '.venv')
    cold = next(row for row in environments.inventory(manager.home) if row.get('package') == 'amplifier-fixture-child')
    assert cold['ref'] == old and cold['current'] == old and not cold['eligible']
    assert cold['policyOrigin'] == child['policyOrigin']


async def test_editable_foundation_cache_has_real_source_evidence_and_freezes(installed_transitive):
    from amplifier_web.runtime_qualification import freeze, installed_graph, verify_recorded
    manager, current, row, old, new, repo = installed_transitive
    (repo / '.amplifier_cache_meta.json').write_text(json.dumps({'git_url': repo.as_uri(), 'ref': 'main', 'commit': new}))
    subprocess.run([shutil.which('uv'), 'pip', 'install', '--python', str(current / '.venv/bin/python'), '--editable', str(repo / 'child')], check=True, capture_output=True)
    cached = next(row for row in environments.inventory(manager.home) if row.get('package') == 'amplifier-fixture-child')
    assert cached['cacheManaged'] and cached['ref'] == 'main' and cached['current'] == new
    assert cached['eligible'] and cached['subdirectory'] == 'child'
    generation = '5' * 32
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    final = await freeze(manager, generation, current)
    verify_recorded(final, receipt)
    child = next(row for row in installed_graph(final) if row['name'] == 'amplifier-fixture-child')
    assert child['directUrl']['dir_info']['editable'] and child['cacheSource']['revision'] == new
    # Mutation of a cached source after qualification is detected without
    # resetting the user's source or replacing its checked generation.
    (repo / 'child/marker.txt').write_text('changed after qualification')
    with pytest.raises(ValueError, match='changed after qualification'):
        verify_recorded(final, receipt)
    assert (repo / 'child/marker.txt').read_text() == 'changed after qualification'


async def test_lazy_install_cannot_replace_a_qualified_editable_dependency(installed_transitive):
    from amplifier_web.runtime_qualification import freeze, installed_graph, verify_recorded
    manager, current, row, old, new, repo = installed_transitive
    (repo / '.amplifier_cache_meta.json').write_text(json.dumps({'git_url': repo.as_uri(), 'ref': 'main', 'commit': new}))
    uv = shutil.which('uv')
    subprocess.run([uv, 'pip', 'install', '--python', str(current / '.venv/bin/python'), '--editable', str(repo / 'child')], check=True, capture_output=True)
    generation = '7' * 32
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    final = await freeze(manager, generation, current)
    overrides = receipt / 'runtime-install-overrides.txt'
    assert '-e ' + (repo / 'child').as_uri() in overrides.read_text()
    before = next(row for row in installed_graph(final) if row['name'] == 'amplifier-fixture-child')
    lazy = manager.home.parent / 'lazy'
    lazy.mkdir()
    write_fixture_project(lazy, 'amplifier-fixture-lazy', [
        'amplifier-fixture-child @ git+' + repo.as_uri() + '@main#subdirectory=child'])
    subprocess.run([uv, 'pip', 'install', '--python', str(final / '.venv/bin/python'), '--overrides', str(overrides), '--editable', str(lazy)], check=True, capture_output=True)
    after = next(row for row in installed_graph(final) if row['name'] == 'amplifier-fixture-child')
    assert before == after
    verify_recorded(final, receipt, allow_additions=True)
    with pytest.raises(ValueError, match='changed after qualification'):
        verify_recorded(final, receipt)
    (repo / 'child/marker.txt').write_text('local edit must survive')
    rows = environments.inventory(manager.home)
    with pytest.raises(ValueError, match='preserve its source configuration'):
        environments.augmented_manifest(environments.manifest_path().read_bytes(), rows)
    assert (repo / 'child/marker.txt').read_text() == 'local edit must survive'


@pytest.mark.parametrize('replacement', ['editable', 'registry'])
async def test_nonprefixed_installed_override_blocks_stale_git_lock_staging(environment, replacement):
    manager, current, update, old, new, repo = environment
    lock = current / 'uv.lock'
    lock.write_text(lock.read_text() + '\n[[package]]\nname="component-child"\nversion="0.1.0"\n'
                    'source={git=' + json.dumps(repo.as_uri() + '?branch=main#' + old) + '}\n')
    site = current / '.venv/lib/python3.13/site-packages'
    metadata = site / 'component_child-9.9.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Name: component-child\nVersion: 9.9\n')
    if replacement == 'editable':
        (metadata / 'direct_url.json').write_text(json.dumps({
            'url': 'file:///private/local-edits', 'dir_info': {'editable': True}}))
    unrelated = site / 'ordinary_dependency-1.0.dist-info'
    unrelated.mkdir()
    (unrelated / 'METADATA').write_text('Name: ordinary-dependency\nVersion: 1.0\n')
    before = {path: path.read_bytes() for path in (lock, *metadata.iterdir())}
    rows = environments.inventory(manager.home)
    actual = next(row for row in rows if row.get('package') == 'component-child')
    assert actual['current'] == '9.9' and actual['override'] and not actual['eligible']
    assert actual['provenance'] == 'installed local or registry override'
    assert not any(row.get('package') == 'ordinary-dependency' for row in rows)
    assert '/private/local-edits' not in json.dumps(actual)
    generation = '6' * 32
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    with pytest.raises(environments.ProtectedRuntimeSource) as caught:
        await environments.stage(manager, generation, [update])
    assert caught.value.diagnostic_facts['package'] == 'component-child'
    assert {path: path.read_bytes() for path in before} == before
    assert not (receipt / 'runtime.lock').exists()
    assert not active_release(manager.home)


async def test_successful_freeze_reclaims_only_its_disposable_preparation(installed_transitive):
    from amplifier_web.runtime_qualification import freeze, verify_recorded
    manager, current, *_ = installed_transitive
    generation = 'ab' * 16
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    preparation = current.with_name('prepare-' + generation)
    current.rename(preparation)
    final = await freeze(manager, generation, preparation)
    assert not preparation.exists()
    assert final.exists()
    verify_recorded(final, receipt)


async def test_identical_qualified_graph_reuses_environment_without_reinstallation(installed_transitive, monkeypatch):
    from amplifier_web.runtime_qualification import freeze, verify_recorded
    manager, current, row, old, new, repo = installed_transitive
    generations = ['8' * 32, '9' * 32]
    for generation in generations:
        environments.receipt_directory(manager.home, generation).mkdir(parents=True)
    first = await freeze(manager, generations[0], current)
    before = (first / 'uv.lock').read_bytes()
    events = []
    original = manager.diagnostics.run
    async def trace(name, function, *args, **kwargs):
        events.append(name)
        return await original(name, function, *args, **kwargs)
    manager.diagnostics.run = trace
    second = await freeze(manager, generations[1], current)
    assert first == second
    assert 'ecosystem-runtime-freeze-install' not in events
    assert (first / 'uv.lock').read_bytes() == before
    for generation in generations:
        receipt = environments.receipt_directory(manager.home, generation)
        assert environments.project_path(manager.home, generation) == first
        verify_recorded(first, receipt)
    # A corrupted retained environment is preserved, never synced or repaired.
    (first / 'uv.lock').write_bytes(before + b'\n# changed by another owner\n')
    generation = 'a1' * 16
    environments.receipt_directory(manager.home, generation).mkdir(parents=True)
    replacement = await freeze(manager, generation, current)
    assert replacement != first
    assert (first / 'uv.lock').read_bytes().endswith(b'# changed by another owner\n')
