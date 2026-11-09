"""Fixed hosted-CI public selected-source qualification, never automatic pytest discovery.

Only synthetic user data/profile selection is authored here. All executable modules
come from official Git/registry origins. Loop's exact private lock checkout is the
sole declared transport exception. No manager inventory/validate/guard is stubbed.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest
import yaml

from amplifier_web import app_updates, builtin_behaviors, runtime_environment, updates
from amplifier_web.host.config import PRECONFIGURED_BUNDLES
from amplifier_web.runtime_qualification import installed_graph, verify_recorded
from amplifier_web.update_diagnostics import PROBE_PREFIX
from amplifier_web.service import AppService

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ['PUBLIC_SELECTED_WORK'])
OUT = Path(os.environ['PUBLIC_SELECTED_OUTPUT'])
FURL = 'https://github.com/microsoft/amplifier-foundation'
RURL = 'https://github.com/microsoft/amplifier-bundle-routing-matrix'
OLD = 'a52ad1558419c828c86c0139b9b4bc351e3a618f'
NEW = 'c67e3ada021a5bbb93df9567feb23e2a72c93f55'
ROLD = '64138b9c544cbaddad6c24fd57f955da2c4e03ba'
RNEW = '9d9d1cb17ac958a011c99b80096034663fd245b3'
pytestmark = pytest.mark.asyncio


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    # Only callers' structured graph/receipt facts, never exception or private
    # dependency source text, reach the public artifact directory.
    text = json.dumps(value, indent=2, sort_keys=True)
    for path, label in ((WORK, '$WORK'), (ROOT, '$SOURCE')):
        text = text.replace(str(path), label)
    (OUT / (name + '.json')).write_text(text + '\n')


def git(root, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=root, text=True,
                                   stderr=subprocess.STDOUT, timeout=120).strip()


def acquire_baseline(home):
    project = runtime_environment.prepare_project(home)
    assert (project / 'pyproject.toml').read_bytes() == runtime_environment.manifest_path().read_bytes()
    # Start with the actual host lock's historic Foundation resolution, not fake
    # remote main or an invented worker manifest. uv updates the project/root and
    # resolves every additional canonical worker dependency normally.
    shutil.copyfile(ROOT / 'uv.lock', project / 'uv.lock')
    result = subprocess.run(['uv', 'sync', '--project', str(project), '--python', '3.13'],
                            capture_output=True, timeout=600)
    log = home / 'baseline-private.log'; log.write_bytes(result.stdout + result.stderr)
    save(home.name + '-baseline-acquisition', {'exitCode': result.returncode,
        'rawLogSha256': digest(log), 'rawLogBytes': log.stat().st_size,
        'manifestSha256': digest(project / 'pyproject.toml'), 'hostLockSha256': digest(ROOT / 'uv.lock')})
    assert result.returncode == 0, 'Public baseline acquisition failed (private log hash retained)'
    sources = runtime_environment.locked_sources(project)
    assert sources['amplifier-foundation'].fragment == OLD, 'Historical lock input did not retain the expected public Foundation resolution'
    return project


def initialize_source(home, url, revision, ref='main'):
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler
    cache = GitSourceHandler()._get_cache_path(parse_uri('git+' + url + '@' + ref), home / 'foundation/cache')
    cache.parent.mkdir(parents=True, exist_ok=True)
    git(home, 'clone', url, cache)
    git(cache, 'checkout', '--detach', revision)
    (cache / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'git_url': url, 'ref': ref, 'commit': revision}))
    assert git(cache, 'remote', 'get-url', 'origin') == url
    return cache


def public_graph(project):
    graph = installed_graph(project)
    # ModuleInstaller may produce editable entries for verified official cache
    # sources; retain the full bounded installed origin, not just package names.
    for row in graph:
        direct = row.get('directUrl') or {}
        url = direct.get('url', '')
        assert 'fixture.invalid' not in url and 'repacked' not in url
    return graph


@pytest.mark.parametrize('case', ['transition', 'retain-pin', 'incompatible-pin'])
async def test_public_selected_source_manager(tmp_path, monkeypatch, case):
    assert os.environ.get('PUBLIC_SELECTED_WORK') and sys.version_info[:2] == (3, 13)
    state = WORK / case; state.mkdir()
    home = state / 'app'; home.mkdir()
    shared = state / 'shared'; shared.mkdir()
    monkeypatch.chdir(state)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    monkeypatch.delenv('AMPLIFIER_UNIFIED_RELEASE', raising=False)
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    monkeypatch.delenv('UV_PROJECT_ENVIRONMENT', raising=False)
    # Mount a genuine official provider, but its only endpoint is an unused
    # loopback port and the key is fixed synthetic text. No account is supplied.
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'qualification-not-a-real-key')
    assert git(state, 'ls-remote', RURL, 'refs/heads/main').split()[0] == RNEW
    assert git(state, 'ls-remote', FURL, 'refs/heads/main').split()[0] == NEW
    project = acquire_baseline(home)
    routing_ref = 'main' if case == 'transition' else RNEW if case == 'retain-pin' else ROLD
    routing_before = ROLD if case == 'transition' else routing_ref
    roots = {'routing': initialize_source(home, RURL, routing_before, routing_ref),
             'foundation': initialize_source(home, FURL, OLD)}
    profile = state / 'profile.yaml'
    profile.write_text(yaml.safe_dump({'bundle': {'name': 'public-qualification'},
        'session': {'orchestrator': {'module': 'loop-streaming'}, 'context': {'module': 'context-simple'}},
        'providers': [{'module': 'provider-anthropic',
            'source': 'git+https://github.com/microsoft/amplifier-module-provider-anthropic@main',
            'config': {'api_key': 'qualification-not-a-real-key', 'base_url': 'http://127.0.0.1:9',
                       'default_model': 'claude-sonnet-4-5'}}]}))
    settings = {'bundle': {'active': 'public-qualification',
        'added': {'public-qualification': str(profile)},
        'app': ['git+' + RURL + '@' + routing_ref + '#subdirectory=behaviors/routing.yaml']},
        'web_bundles': {'excluded': list(builtin_behaviors.DEFAULT_BEHAVIORS),
            'entries': [{'name': name, 'uri': uri, 'role': 'standalone', 'enabled': False}
                        for name, uri in PRECONFIGURED_BUNDLES.items()]}}
    if case != 'transition':
        # Explicit synthetic user choice. Pin the actual module as well as its
        # declaring behavior: old YAML's own module source is otherwise @main.
        settings['sources'] = {'modules': {'hooks-routing':
            'git+' + RURL + '@' + routing_ref + '#subdirectory=modules/hooks-routing'}}
    (shared / 'settings.yaml').write_text(yaml.safe_dump(settings))
    beforelock = (project / 'uv.lock').read_bytes()
    beforeheads = {name: git(path, 'rev-parse', 'HEAD') for name, path in roots.items()}
    save(case + '-inputs', {'workerManifestSha256': digest(runtime_environment.manifest_path()),
        'baselineLockSha256': digest(project / 'uv.lock'), 'routingRef': routing_ref,
        'beforeHeads': beforeheads, 'expectedRoutingMain': RNEW, 'expectedFoundationMain': NEW,
        'profile': yaml.safe_load(profile.read_text()), 'settings': settings,
        'baselineResolvedGraph': tomllib.loads(beforelock.decode())['package']})

    class Runtime:
        workers = {}
        async def close(self): pass
        def has_pending_operations(self): return False
    app = AppService(home, Runtime(), workspace=state)
    manager = updates.UpdateManager(app); app.update_manager = manager
    # App publication is outside this component repair. All component discovery,
    # remote resolution, selection, preparation and activation are real owners.
    async def application(): return {'id': 'application', 'status': 'current'}
    monkeypatch.setattr(app_updates, 'check', application)
    create = asyncio.create_subprocess_exec
    probes = []
    async def capture(*args, **kwargs):
        child = await create(*args, **kwargs)
        if any(str(arg).endswith('update_probe.py') for arg in args):
            communicate = child.communicate
            async def read(*a, **k):
                out, err = await communicate(*a, **k)
                for line in (out or b'').decode(errors='replace').splitlines():
                    if line.startswith(PROBE_PREFIX):
                        probes.append(json.loads(line.removeprefix(PROBE_PREFIX)))
                return out, err
            child.communicate = read
        return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
    try:
        await manager.check(fresh=True)
        save(case + '-checked', manager.inventory)
        assert app.state['updates']['phase'] == 'available'
        assert app.state['updates']['sequence']['stage'] == 'included'
        foundation = next(r for r in manager.inventory if r.get('package') == 'amplifier-foundation')
        assert foundation['current'] == OLD and foundation['latest'] == NEW
        await manager.install()
        save(case + '-diagnostics', manager.diagnostics.state)
        save(case + '-probes', probes)
        assert (project / 'uv.lock').read_bytes() == beforelock
        assert {n: git(p, 'rev-parse', 'HEAD') for n, p in roots.items()} == beforeheads
        if case == 'incompatible-pin':
            failure = manager.diagnostics.state['lastFailure']
            assert failure.get('package') == 'amplifier-foundation'
            assert failure.get('reason') in {'runtime-source-constraint', 'protected-runtime-source'}
            assert not any(p.get('dependenciesPrepared') for p in probes)
            assert not updates.active_release(home).get('current')
            assert not app.state['updates'].get('pendingRelease')
            return
        assert app.state['updates']['phase'] in {'staged', 'installed'}, 'Public graph qualification failed'
        generation = app.state['updates'].get('pendingRelease') or updates.active_release(home)['current']
        stage = home / 'updates/releases' / generation
        final = runtime_environment.project_path(home, generation)
        verify_recorded(final, stage)
        graph = public_graph(final)
        foundation = next(row for row in graph if row['name'] == 'amplifier-foundation')
        assert foundation['directUrl']['vcs_info']['commit_id'] == NEW
        selection = json.loads((stage / 'selected-source-declarations.json').read_text())
        routing = selection['packages']['amplifier-module-hooks-routing']
        assert routing['revision'] == RNEW
        if case == 'retain-pin':
            assert routing['ref'] == RNEW and routing['status'] == 'explicit-pin'
        assert (stage / 'profiles-qualified.json').exists()
        assert updates.active_release(home).get('current') == generation
        save(case + '-qualified', {'installedGraph': graph, 'selection': selection,
            'workerManifestSha256': digest(stage / 'runtime-base.toml'),
            'runtimeManifest': (final / 'pyproject.toml').read_text(),
            'runtimeLock': tomllib.loads((final / 'uv.lock').read_text()),
            'profiles': json.loads((stage / 'profiles-qualified.json').read_text())})
        await manager.rollback()
        assert updates.active_release(home).get('current') is None
        assert runtime_environment.project_path(home) == project
        assert (project / 'uv.lock').read_bytes() == beforelock
        save(case + '-rollback', {'active': updates.active_release(home),
                                 'baselineLockUnchanged': True, 'beforeHeads': beforeheads})
    finally:
        save(case + '-probes', probes)
        save(case + '-diagnostics', manager.diagnostics.state)
        # Preserve the actual canonical provenance on failed assertions too.
        # Receipts contain identities/hashes, not private source bodies.
        for stage in (home / 'updates/releases').glob('*'):
            for name in ('selected-source-declarations.json', 'runtime-sources.json',
                         'profiles-qualified.json', 'runtime-installed.json'):
                path = stage / name
                if path.is_file():
                    save(case + '-' + stage.name + '-' + path.stem, json.loads(path.read_text()))
        await app.close()
