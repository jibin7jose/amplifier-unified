"""#1279: omitted refs survive two real qualifications and manager activations.

Run in the owned Linux/Python 3.13 guest. Git, Foundation resolution/composition,
batch install, uv, freeze and activation orchestration are real. The probe adapter
uses the guest's Foundation, not the synthetic target interpreter; compatibility
and Runtime.promote_generation are fixture boundaries, not live/native mounts.
"""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

from amplifier_web import __version__, bundles, update_checks, update_sources, updates
from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_qualification as qualification
from amplifier_web import runtime_source_alignment as alignment
from amplifier_web.host.config import HostConfig
from amplifier_web.runtime_policy_reconciliation import Collector
from amplifier_web.service import AppService
from test_runtime_checked_cache import BACKEND, git

URL = 'https://example.invalid/omitted-ref-cycle'
URI = 'git+' + URL  # This exact omitted-ref URI goes to BOTH resolver and observer.
PACKAGES = {'fixture-context': 'modules/context', 'fixture-transcript': 'modules/transcript'}
PAYLOAD_BACKEND = BACKEND.replace(
    "        # Installers need every payload entry to remove old-version metadata.",
    "        wheel.writestr(name + '.revision.txt', Path('revision.txt').read_bytes())\n"
    "        # Installers need every payload entry to remove old-version metadata.",
)


def files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and '.git' not in path.parts}


async def test_omitted_ref_qualifies_and_activates_two_generations(tmp_path, monkeypatch):
    from amplifier_foundation import Bundle
    from amplifier_foundation.modules import activator
    from amplifier_foundation.modules.batch import DependencyBatch
    from amplifier_foundation.sources.resolver import SimpleSourceResolver
    from amplifier_foundation.sources.shared import SharedSourceStore

    assert sys.platform == 'linux' and sys.version_info[:2] == (3, 13), 'Run in the managed guest'
    uv = shutil.which('uv')
    assert uv
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)
    home, repo, shared = tmp_path / 'app', tmp_path / 'origin', tmp_path / 'shared'
    for path in (home, repo, shared):
        path.mkdir()
    (shared / 'settings.yaml').write_text('{}')
    for key, value in {
        'AMPLIFIER_WEB_HOME': str(home), 'AMPLIFIER_HOME': str(shared),
        'AMPLIFIER_SOURCE_STORE': str(home / 'source-store'), 'AMPLIFIER_UNIFIED_RELEASE': '',
        'XDG_CONFIG_HOME': str(tmp_path / 'config'), 'UV_CACHE_DIR': str(tmp_path / 'uv-cache'),
        'UV_NO_INDEX': '1', 'UV_PYTHON_DOWNLOADS': 'never', 'PYTHONDONTWRITEBYTECODE': '1',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_COUNT': '0',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file',
    }.items():
        monkeypatch.setenv(key, value)
    for key in ('UV_OFFLINE', 'UV_NO_CONFIG', 'UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT',
                'UV_BUILD_CONSTRAINT', 'UV_PROJECT_ENVIRONMENT', 'GIT_CONFIG',
                'GIT_CONFIG_PARAMETERS', 'GIT_DIR', 'GIT_WORK_TREE'):
        monkeypatch.delenv(key, raising=False)
    transport = tmp_path / 'gitconfig'
    transport.write_text('[url "' + repo.as_uri() + '"]\n insteadOf = ' + URL + '\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(transport))
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    git(repo, 'config', 'user.name', 'Fixture')
    (repo / 'bundle.md').write_text('---\nbundle:\n  name: omitted-ref-fixture\n---\n')
    for name, subdirectory in PACKAGES.items():
        path = repo / subdirectory
        path.mkdir(parents=True)
        (path / 'pyproject.toml').write_text(
            '[project]\nname="' + name + '"\nversion="0.1.0"\n'
            '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
        (path / 'backend.py').write_text(PAYLOAD_BACKEND)
        (path / 'revision.txt').write_text('0')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'baseline fixture')
    manifest = tmp_path / 'runtime-base.toml'
    manifest.write_text(
        '[project]\nname="fixture-runtime"\nversion="1"\nrequires-python=">=3.13"\n'
        'dependencies=' + json.dumps(list(PACKAGES)) + '\n[tool.uv.sources]\n' +
        '\n'.join(name + '={git="' + URL + '",branch="main",subdirectory="' + subdir + '"}'
                  for name, subdir in PACKAGES.items()))
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    monkeypatch.setattr(bundles, 'profile_candidates', lambda config: ['fixture'])
    monkeypatch.setattr(bundles, 'offered_profiles', lambda config: ['fixture'])
    # The in-process probe uses real shared-source-capable Foundation from pytest.
    monkeypatch.setattr(update_sources, 'worker_supports_shared', lambda project: True)
    state = SimpleNamespace(project=None, stage=None, batches=[], reports=[], events=[])
    original_activator = activator.ModuleActivator

    class OwnedActivator(original_activator):
        def __init__(self, *args, **kwargs):
            assert state.project.is_relative_to(home / 'runtime')
            kwargs['install_python'] = str(state.project / '.venv/bin/python')
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(activator, 'ModuleActivator', OwnedActivator)
    original_process = updates.process

    async def adapter(*args, **kwargs):
        if not any(str(arg).endswith('update_probe.py') for arg in args):
            if args[:4] == ('git', 'remote', 'get-url', 'origin'):
                # Transport rewriting must not relabel authored source identity.
                kwargs['env'] = {**os.environ, **(kwargs.get('env') or {}), 'GIT_CONFIG_GLOBAL': os.devnull}
            return await original_process(*args, **kwargs)
        project = state.project = Path(args[args.index('--project') + 1])
        if '--read-only' in args:
            qualification.verify_recorded(project, state.stage)
            assert (state.stage / 'runtime-installed.json').exists()
            state.events.append('compatibility-fixture')  # No conversation/provider mount.
            return ''
        config = HostConfig(state.stage, state.stage, {}, state.stage / 'foundation')
        cache = config.registry_home / 'cache'
        root = (await SimpleSourceResolver(cache_dir=cache).resolve(URI)).active_path
        collector = Collector()
        collector.observe(URI, config)  # Never seed Collector.references.
        assert not (root / 'pyproject.toml').exists()
        bundle = Bundle(name='fixture', base_path=root, tools=[
            {'module': name, 'source': str(root / subdir)} for name, subdir in PACKAGES.items()])
        batch = DependencyBatch()
        await bundle.prepare(strict=True, refresh_dependencies=True, dependency_batch=batch,
                             cache_dir=cache, install_overrides=Path(args[args.index('--install-overrides') + 1]))
        assert set(batch.names) == set(PACKAGES) and not batch.installed
        state.batches.append(batch)
        if '--source-policy-only' in args:
            report = await collector.report(batch, config)
            Path(args[args.index('--source-policy-only') + 1]).write_text(json.dumps(report))
            state.events.append('collect')
        else:
            checked = json.loads(Path(args[args.index('--checked-sources') + 1]).read_text())
            report = await alignment.checked_batch_sources(batch, collector, config, checked)
            alignment.verify_checked_selection(checked, report, exact=True)
            assert await batch.install() == {'sources': 2, 'packages': 2, 'resolverTransactions': 1}
            state.events.append('install')
        state.reports.append(report)
        return ''

    monkeypatch.setattr(updates, 'process', adapter)

    class Runtime:
        def __init__(self):
            self.promotions = []

        def promote_generation(self, generation, *, protected):
            assert updates.active_release(home)['current'] == generation
            self.promotions.append(generation)

        async def close(self):
            pass

    runtime = Runtime()
    app = AppService(home, runtime, workspace=tmp_path)
    manager = app.update_manager = updates.UpdateManager(app)
    store = SharedSourceStore(home / 'source-store')
    failed_attempt = manager.diagnostics.begin('ecosystem', attempt_id='a' * 32)
    manager.diagnostics.record('ecosystem-source-preflight', 'failed',
                               reason='runtime-source-unbound', package='amplifier-fixture')
    await app.diagnostics.flush()

    async def retained_failure():
        await app.diagnostics.flush()
        before = None
        while True:
            page = app.diagnostics.read(stream='updates', before=before, limit=30)
            for item in page['items']:
                if item['data'].get('attemptId') == failed_attempt:
                    return item
            before = page['nextBefore']
            assert before is not None, 'The durable prior failed attempt was lost'

    failure_record = await retained_failure()
    assert failure_record['data']['status'] == 'failed'
    retained = []
    try:
        # A real pre-updater installation supplies inventory; no fabricated graph
        # or checked policy, and no manual pointer promotion establishes baseline.
        baseline = environments.prepare_project(home)
        await updates.process(uv, 'sync', '--project', str(baseline), '--python', sys.executable, timeout=90)
        previous = None
        for cycle, generation in enumerate(('b' * 32, 'c' * 32), 1):
            for subdir in PACKAGES.values():
                (repo / subdir / 'revision.txt').write_text(str(cycle))
            git(repo, 'commit', '-am', 'same-version payload ' + str(cycle))
            checked = git(repo, 'rev-parse', 'HEAD')
            rows = await environments.update_inventory(home)
            assert {row.get('package') for row in rows} == set(PACKAGES)
            proof = await update_checks.ref_policy(URL, 'main', updates.process)
            assert proof['kind'] == 'branch' and proof['revision'] == checked
            for row in rows:
                assert row['eligible'] and row['ref'] == 'main' and row['current'] != checked
                row.update(latest=checked, status='update')
            manager.inventory = rows
            stage = state.stage = environments.receipt_directory(home, generation)
            (stage / 'shared-config').mkdir(parents=True)
            (stage / 'shared-config/settings.yaml').write_text('{}')
            if previous:
                shutil.copytree(previous / 'foundation', stage / 'foundation')
            state.batches, state.reports, state.events = [], [], []
            pointer = updates.active_release(home)
            manager.diagnostics.begin('ecosystem', generation)
            await manager.validate(stage, generation)
            assert updates.active_release(home) == pointer
            assert state.events == ['collect', 'install', 'compatibility-fixture']
            first, fresh = state.batches
            assert first is not fresh and not first.installed
            assert first.report()['resolverTransactions'] == 0 and fresh.report()['resolverTransactions'] == 1
            assert all(row['status'] == 'selected' and row['ref'] == 'main'
                       for report in state.reports for row in report['packages'].values())
            assert {row['revision'] for row in state.reports[0]['packages'].values()} == {
                retained[-1][5] if retained else checked}
            assert {row['revision'] for row in state.reports[1]['packages'].values()} == {checked}
            project = environments.project_path(home, generation)
            qualification.verify_recorded(project, stage)
            graph, payload = qualification.installed_graph(project), qualification.package_content(project)
            assert {row['name'] for row in graph} == set(PACKAGES)
            assert {row['cacheSource']['revision'] for row in graph} == {checked}
            assert all(row['version'] == '0.1.0' and not row['cacheSource']['dirty'] for row in graph)
            policies = json.loads((stage / 'runtime-sources.json').read_text())
            assert set(policies) == set(PACKAGES)
            assert all(row['ref'] == 'main' and row['revision'] == checked for row in policies.values())
            sites = list((project / '.venv/lib').glob('python*/site-packages'))
            assert len(sites) == 1
            for name in PACKAGES:
                assert (sites[0] / (name.replace('-', '_') + '.revision.txt')).read_text() == str(cycle)
            # Only after real freeze/proof: fixture records qualification admission.
            (stage / 'validated.json').write_text(json.dumps({
                'hostVersion': __version__, 'generationSchema': 1, 'workerProtocol': 1}))
            await manager.publish(phase='staged', pendingRelease=generation)
            await manager.activate()  # Production method owns both pointer writes.
            assert app.state['updates']['phase'] == 'installed'
            assert app.state['updates']['pendingRelease'] is None
            assert runtime.promotions == ['b' * 32, 'c' * 32][:cycle]
            active = updates.active_release(home)
            assert (active['current'], active['previous']) == (generation, previous.name if previous else None)
            # Maintenance is not under test; settle its task before another cycle.
            manager.cleanup_task.cancel()
            await asyncio.gather(manager.cleanup_task, return_exceptions=True)
            assert await retained_failure() == failure_record
            assert 'lastFailure' not in manager.diagnostics.state
            for old_stage, receipts, old_project, old_graph, old_payload, revision, source_files in retained:
                assert files(old_stage) == receipts
                qualification.verify_recorded(old_project, old_stage)
                assert qualification.installed_graph(old_project) == old_graph
                assert qualification.package_content(old_project) == old_payload != payload
                assert files(store.verify(URL, revision)) == source_files
            retained.append((stage, files(stage), project, graph, payload, checked, files(store.verify(URL, checked))))
            previous = stage
    finally:
        try:
            await app.close()
        finally:
            # Keep regular-file evidence exportable; disposable venv/cache links
            # are no longer executable evidence after teardown. Retain no sockets.
            for path in tmp_path.rglob('*'):
                if path.is_symlink() or path.is_socket():
                    path.unlink()