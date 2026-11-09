"""Shared binding identity survives adoption and the real update-probe child.

Synthetic sources, no model/provider execution or public network. The candidate
inherits prepared test dependencies but installers target only its own venv.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import builtin_behaviors, runtime_environment as environments
from amplifier_web import runtime_policy_reconciliation as policy
from amplifier_web import runtime_qualification as qualification
from amplifier_web import update_sources, updates
from amplifier_web.host.config import PRECONFIGURED_BUNDLES, read_config
from amplifier_web.runtime_source_alignment import verify_checked_selection
from test_runtime_checked_cache import BACKEND, Diagnostics, git

URL = 'https://example.invalid/shared-hook'
NAME = 'fixture-shared-hook'
SUBDIRECTORY = 'modules/hook-fixture'
GENERATION = 'e' * 32


@pytest.fixture
async def shared_source(tmp_path, monkeypatch):
    from amplifier_foundation.sources.shared import SharedSourceStore
    home = tmp_path / 'app'
    stage = environments.receipt_directory(home, GENERATION)
    root = stage / 'foundation/cache/legacy'
    root.mkdir(parents=True)
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'remote', 'add', 'origin', URL + '.git')
    module = root / SUBDIRECTORY
    module.mkdir(parents=True)
    (module / 'pyproject.toml').write_text(
        '[project]\nname="' + NAME + '"\nversion="1.0"\ndependencies=[]\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (module / 'backend.py').write_text(BACKEND)
    (root / 'bundle.md').write_text('---\nbundle:\n  name: shared-hook\n---\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'synthetic shared hook')
    revision = git(root, 'rev-parse', 'HEAD')
    (root / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': URL, 'ref': 'main', 'commit': revision}))
    store = SharedSourceStore(home / 'source-store')
    monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(store.root))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    # Seed the immutable object with .git. Adoption of the equivalent legacy
    # checkout overwrites this same canonical binding with the non-.git spelling.
    obj = await store.bind(stage / 'foundation/cache', URL + '.git', 'main', revision, existing=root)
    immutable = (obj / '.amplifier_cache_meta.json').read_bytes()
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    await update_sources.adopt_clean_sources(manager, stage)
    assert not root.exists()
    binding = next((stage / 'foundation/cache/.source-bindings').glob('*.json'))
    assert json.loads(binding.read_text()) == {'git_url': URL, 'ref': 'main', 'commit': revision}
    assert (obj / '.amplifier_cache_meta.json').read_bytes() == immutable
    config = read_config(stage, home=stage, shared_home=stage / 'shared-config', global_only=True)
    collector = policy.Collector()
    collector.observe('git+' + URL + '.git@main', config)
    batch = SimpleNamespace(names={NAME: obj / SUBDIRECTORY})
    checked = {NAME: {'url': URL + '.git', 'ref': 'main', 'revision': revision,
                      'subdirectory': SUBDIRECTORY}}
    return SimpleNamespace(home=home, stage=stage, obj=obj, revision=revision, store=store,
        manager=manager, binding=binding, immutable=immutable, config=config,
        collector=collector, batch=batch, checked=checked)


@pytest.mark.parametrize('binding_suffix,object_suffix', [('', '.git'), ('.git', ''), ('.git', '.git'), ('', '')])
async def test_shared_binding_uses_canonical_repository_identity(shared_source, monkeypatch, binding_suffix, object_suffix):
    c = shared_source
    # Exercise both insertion and lookup normalization, not only one direction.
    record = json.loads(c.binding.read_text())
    record['git_url'] = URL + binding_suffix
    c.binding.write_text(json.dumps(record))
    if not object_suffix:
        # A second real store publication supplies the opposite metadata spelling.
        other = c.home / 'opposite-store'
        from amplifier_foundation.sources.shared import SharedSourceStore
        store = SharedSourceStore(other)
        obj = await store.bind(c.stage / 'foundation/cache', URL, 'main', c.revision, existing=c.obj)
        monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(other))
        c.batch.names[NAME] = obj / SUBDIRECTORY
        c.binding.write_text(json.dumps(record))
    before = c.binding.read_bytes()
    report = await c.collector.report(c.batch, c.config)
    verify_checked_selection(c.checked, report, exact=True)
    assert report['packages'][NAME]['status'] == 'selected'
    assert c.binding.read_bytes() == before
    assert (c.obj / '.amplifier_cache_meta.json').read_bytes() == c.immutable


@pytest.mark.parametrize('change', ['host', 'repository', 'commit', 'ref', 'missing', 'subdirectory', 'local', 'dirty'])
async def test_shared_binding_cannot_authorize_another_identity(shared_source, change):
    c = shared_source
    data = json.loads(c.binding.read_text())
    if change == 'host': data['git_url'] = URL.replace('example.invalid', 'other.invalid')
    elif change == 'repository': data['git_url'] = URL + '-other.git'
    elif change == 'commit': data['commit'] = 'f' * 40
    elif change == 'ref': data['ref'] = 'stable'
    elif change == 'subdirectory': c.checked[NAME]['subdirectory'] = 'another-module'
    elif change == 'local': c.collector.local_sources.add(c.obj / SUBDIRECTORY)
    elif change == 'dirty':
        path = c.obj / SUBDIRECTORY / 'pyproject.toml'
        path.chmod(0o644)
        path.write_text(path.read_text() + '\n# protected edit\n')
    c.binding.write_text(json.dumps(data))
    before = c.binding.read_bytes()
    if change == 'missing':
        c.binding.unlink()
    if change == 'dirty':
        with pytest.raises(ValueError, match='changed'):
            await c.collector.report(c.batch, c.config)
    else:
        report = await c.collector.report(c.batch, c.config)
        with pytest.raises(environments.ProtectedRuntimeSource):
            verify_checked_selection(c.checked, report, exact=True)
    assert not c.binding.exists() if change == 'missing' else c.binding.read_bytes() == before


@pytest.mark.parametrize('choice', ['equivalent', 'host', 'repository', 'commit', 'ref', 'subdirectory'])
async def test_real_probe_installs_checked_shared_hook_after_adoption_and_freezes(
        shared_source, tmp_path, monkeypatch, record_property, choice):
    c = shared_source
    assert sys.version_info[:2] == (3, 13)
    uv = shutil.which('uv')
    assert uv, 'Requires the prepared Python 3.13 Docker fixture'
    for key in ('UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT',
                'UV_PROJECT_ENVIRONMENT', 'UV_OFFLINE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    # The existing prepared interpreter supplies Foundation/host dependencies;
    # no fixture install targets it. The hook's one batch install is isolated.
    project = c.home / 'runtime/preparation'
    project.mkdir(parents=True)
    manifest = tmp_path / 'worker.toml'
    manifest.write_text('[project]\nname="probe-fixture"\nversion="1"\nrequires-python=">=3.13"\ndependencies=[]\n')
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    (project / 'pyproject.toml').write_bytes(manifest.read_bytes())
    await updates.process(uv, 'lock', '--project', str(project), '--python', sys.executable)
    await updates.process(uv, 'venv', '--system-site-packages', '--python', sys.executable, str(project / '.venv'))
    # uv's system-site-packages means base CPython, not the invoking prepared
    # venv. Reuse those explicit dependency paths in this disposable interpreter.
    import site
    candidate_site = next((project / '.venv/lib').glob('python*/site-packages'))
    (candidate_site / 'prepared-dependencies.pth').write_text('\n'.join(site.getsitepackages()) + '\n')
    workspace = c.stage / 'qualification-workspace'
    workspace.mkdir()
    shared = c.stage / 'shared-config'
    shared.mkdir(exist_ok=True)
    local = tmp_path / 'local-modules'
    for name in ('context-fixture', 'provider-fixture'):
        folder = local / name
        folder.mkdir(parents=True)
        (folder / '__init__.py').write_text('# Dependency-free preparation-only fixture\n')
    profile = workspace / 'profile.yaml'
    from amplifier_web.bundles import SNAPSHOT_VERSION
    profile.write_text(json.dumps({
        'bundle': {'name': 'fixture', 'version': SNAPSHOT_VERSION},
        'session': {'orchestrator': {'module': 'loop-streaming'},
                    'context': {'module': 'context-fixture', 'source': str(local / 'context-fixture')}},
        'providers': [{'module': 'provider-fixture', 'source': str(local / 'provider-fixture')}],
        'hooks': [{'module': 'hook-fixture', 'source': 'git+' + URL + '.git@main#subdirectory=' + SUBDIRECTORY}],
    }))
    settings = {'bundle': {'active': 'fixture', 'added': {'fixture': str(profile)}},
                'web_bundles': {'excluded': list(builtin_behaviors.DEFAULT_BEHAVIORS),
                    'entries': [{'name': name, 'uri': uri, 'role': 'standalone', 'enabled': False}
                                for name, uri in PRECONFIGURED_BUNDLES.items()]}}
    (shared / 'settings.yaml').write_text(json.dumps(settings))
    profiles = c.stage / 'profiles.json'
    profiles.write_text('["fixture"]')
    checked = c.stage / 'runtime-sources.json'
    if choice == 'host': c.checked[NAME]['url'] = URL.replace('example.invalid', 'other.invalid')
    elif choice == 'repository': c.checked[NAME]['url'] = URL + '-other'
    elif choice == 'commit': c.checked[NAME]['revision'] = 'f' * 40
    elif choice == 'ref': c.checked[NAME]['ref'] = 'stable'
    elif choice == 'subdirectory': c.checked[NAME]['subdirectory'] = 'another-module'
    checked.write_text(json.dumps(c.checked))
    overrides = c.stage / 'install-overrides.txt'
    overrides.write_text('')
    env = {**os.environ, 'AMPLIFIER_WEB_HOME': str(c.stage), 'AMPLIFIER_HOME': str(shared),
           'AMPLIFIER_UNIFIED_RELEASE': '', 'AMPLIFIER_INSTALL_PREPARATION': 'shared-binding-fixture',
           'AMPLIFIER_UPDATE_ATTEMPT': 'shared-binding-fixture'}
    before = {c.binding: c.binding.read_bytes(), checked: checked.read_bytes(),
              c.obj / '.amplifier_cache_meta.json': c.immutable}
    probe = Path(updates.__file__).with_name('update_probe.py')
    command = [str(project / '.venv/bin/python'), str(probe), str(workspace), 'fixture',
               '--profiles', str(profiles), '--checked-sources', str(checked),
               '--install-overrides', str(overrides), '--global-only']
    record_property('probeCommand', json.dumps(command))
    identity = subprocess.check_output([command[0], '-c',
        'import importlib.metadata as m,json,sys; import amplifier_foundation; '
        'print(json.dumps({"python":sys.version,"executable":sys.executable,'
        '"foundation":m.version("amplifier-foundation"),"foundationPath":amplifier_foundation.__file__}))'],
        text=True, env=env)
    record_property('probeIdentity', identity.strip())
    record_property('unifiedSource', str(probe))
    # The actual child must encounter different raw URL spellings, not a test
    # that accidentally normalizes both records before reaching the collector.
    assert json.loads(c.binding.read_text())['git_url'] == URL
    assert json.loads(c.immutable)['git_url'] == URL + '.git'
    payload_before = qualification.package_content(project)
    result = await asyncio.to_thread(subprocess.run, command, env=env,
        text=True, capture_output=True, timeout=90)
    record_property('probeStdout', result.stdout)
    record_property('probeStderr', result.stderr)
    from amplifier_web.update_diagnostics import PROBE_PREFIX
    facts = [json.loads(line.removeprefix(PROBE_PREFIX)) for line in result.stdout.splitlines()
             if line.startswith(PROBE_PREFIX)][-1]
    assert {path: path.read_bytes() for path in before} == before
    if choice != 'equivalent':
        assert result.returncode == 1, result.stdout + result.stderr
        assert not facts['ok'] and facts['errorType'] == 'ProtectedRuntimeSource'
        assert qualification.package_content(project) == payload_before
        assert not qualification.installed_graph(project)
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert facts['ok'] and facts['stage'] == 'prepared'
    assert facts['packages'] == facts['resolverTransactions'] == 1
    assert facts['sources'] == 4  # Hook plus installed loop and two source-only fixture modules.
    installed = environments.installed_sources(project)
    assert installed[NAME]['current'] == c.revision
    assert installed[NAME]['subdirectory'] == SUBDIRECTORY
    final = await qualification.freeze(c.manager, GENERATION, project)
    qualification.verify_recorded(final, c.stage)
    assert environments.installed_sources(final)[NAME]['current'] == c.revision
    assert c.store.verify(URL, c.revision) == c.obj
