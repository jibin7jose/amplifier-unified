"""Real legacy sibling migration, for an isolated Linux/Python 3.13 guest.

No root distribution, package-version bump, model, provider, or public network.
This exercises reconciliation and validate() with a no-model probe adapter.
The compatibility mount is explicitly outside this synthetic fixture. Pin/ref
controls remain in test_runtime_policy_reconciliation/test_runtime_checked_cache.
"""
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_policy_reconciliation as migration
from amplifier_web import runtime_qualification as qualification
from amplifier_web import runtime_source_alignment as alignment
from amplifier_web import updates
from amplifier_web.host.config import read_config
from test_runtime_checked_cache import BACKEND, Diagnostics, git


URL = 'https://example.invalid/legacy-siblings'
REFERENCE = 'git+' + URL + '@main'
PACKAGES = {
    'fixture-context': 'modules/context-fixture',
    'fixture-transcript': 'modules/transcript-fixture',
}
GENERATION = 'c' * 32
# Keep the existing backend's complete RECORD while making same-version
# revisions distinguishable by installed bytes, not just cache metadata.
MARKER_BACKEND = BACKEND.replace(
    "        # Installers need every payload entry to remove old-version metadata.",
    "        wheel.writestr(name + '.revision.txt', Path('revision.txt').read_bytes())\n"
    "        # Installers need every payload entry to remove old-version metadata.",
)


def payload_markers(project):
    sites = list((project / '.venv/lib').glob('python*/site-packages'))
    assert len(sites) == 1, 'This bounded fixture requires the managed Linux guest'
    return {name: (sites[0] / (name.replace('-', '_') + '.revision.txt')).read_text()
            for name in PACKAGES}


def source_files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and '.git' not in path.parts}


def assert_git_install(project, revision):
    graph = {row['name']: row for row in qualification.installed_graph(project)}
    assert set(graph) == set(PACKAGES)
    for name, subdirectory in PACKAGES.items():
        row = graph[name]
        assert row['version'] == '0.1.0'
        assert 'cacheSource' not in row
        assert row['directUrl'] == {
            'url': URL, 'subdirectory': subdirectory,
            'vcs_info': {'vcs': 'git', 'requested_revision': revision, 'commit_id': revision},
        }


@pytest.fixture
async def legacy_siblings(tmp_path, monkeypatch, request):
    from amplifier_foundation import Bundle
    from amplifier_foundation.modules import activator
    from amplifier_foundation.modules.batch import DependencyBatch
    from amplifier_foundation.sources.resolver import SimpleSourceResolver

    assert sys.version_info[:2] == (3, 13), 'Run in the owned Python 3.13 guest'
    uv = shutil.which('uv')
    assert uv, 'The managed guest must supply uv'
    assert MARKER_BACKEND != BACKEND
    explicit_local = getattr(request, 'param', False)
    home = tmp_path / 'app'
    stage = environments.receipt_directory(home, GENERATION)
    stage.mkdir(parents=True)
    shared = tmp_path / 'shared-settings'
    shared.mkdir()
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '0')
    monkeypatch.setenv('GIT_TERMINAL_PROMPT', '0')
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'file')
    for key in ('AMPLIFIER_SOURCE_STORE', 'UV_OFFLINE', 'UV_NO_CONFIG', 'UV_CONFIG_FILE', 'UV_OVERRIDE',
                'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT', 'UV_PROJECT_ENVIRONMENT',
                'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_DIR', 'GIT_WORK_TREE'):
        monkeypatch.delenv(key, raising=False)

    repo = tmp_path / 'origin'
    repo.mkdir()
    transport = tmp_path / 'gitconfig'
    transport.write_text('[url "' + repo.as_uri() + '"]\n insteadOf = ' + URL + '\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(transport))
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'bundle.md').write_text('---\nbundle:\n  name: legacy-siblings\n---\n')
    for name, subdirectory in PACKAGES.items():
        path = repo / subdirectory
        path.mkdir(parents=True)
        (path / 'pyproject.toml').write_text(
            '[project]\nname="' + name + '"\nversion="0.1.0"\ndependencies=[]\n'
            '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
        (path / 'backend.py').write_text(MARKER_BACKEND)
        (path / 'revision.txt').write_text('old payload\n')
    transcript = repo / PACKAGES['fixture-transcript'] / 'pyproject.toml'
    transcript.write_text(transcript.read_text() +
        '[tool.uv.sources]\nfixture-context={path="../context-fixture",editable=true}\n'
        '[dependency-groups]\ndev=["fixture-context"]\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'old installable siblings')
    old = git(repo, 'rev-parse', 'HEAD')

    # Resolve before main advances: this is an already materialized stale
    # candidate, not the existing late-resolution race fixture.
    cache = stage / 'foundation/cache'
    root = (await SimpleSourceResolver(cache_dir=cache).resolve(REFERENCE)).active_path
    assert git(root, 'rev-parse', 'HEAD') == old
    assert not (root / 'pyproject.toml').exists()
    settings = {'bundle': {'active': 'fixture', 'added': {'fixture': REFERENCE}}}
    if explicit_local:
        settings['sources'] = {'modules': {
            'context-fixture': './' + str((root / PACKAGES['fixture-context']).relative_to(stage))}}
    settings_file = shared / 'settings.yaml'
    settings_file.write_text(json.dumps(settings))  # JSON is also valid YAML.
    config = read_config(stage, home=stage, shared_home=shared, global_only=True)

    for subdirectory in PACKAGES.values():
        (repo / subdirectory / 'revision.txt').write_text('checked payload\n')
    git(repo, 'commit', '-am', 'new bytes, unchanged package versions')
    new = git(repo, 'rev-parse', 'HEAD')
    assert new != old and git(root, 'rev-parse', 'HEAD') == old

    # The only runner adaptation preserves the authored origin during identity
    # inspection. Fetch/ls-remote/uv remain real, redirected to the local fixture.
    # Git remote get-url otherwise expands insteadOf into a file:// identity.
    original_process = updates.process
    commands = []
    async def process(*args, **kwargs):
        commands.append(args)
        if args[:4] == ('git', 'remote', 'get-url', 'origin'):
            kwargs['env'] = {**os.environ, **kwargs.get('env', {}),
                             'GIT_CONFIG_GLOBAL': os.devnull}
        return await original_process(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    async def publish(**kwargs):
        pass
    manager.publish = publish
    manager.stage_cache_sources = lambda target, rows: updates.UpdateManager.stage_cache_sources(manager, target, rows)

    baseline_manifest = tmp_path / 'base.toml'
    baseline_manifest.write_text('[project]\nname="fixture-worker"\nversion="1"\n'
                                 'requires-python=">=3.13"\ndependencies=[]\n')
    monkeypatch.setattr(environments, 'manifest_path', lambda: baseline_manifest)
    pointer = home / 'updates/active.json'
    pointer.write_text(json.dumps({'current': None, 'previous': None}))
    failure = home / 'updates/prior-failure.json'
    failure.write_text('{"status":"failed","attemptId":"retained-synthetic-failure"}')
    baseline = environments.prepare_project(home)
    await updates.process(uv, 'sync', '--project', str(baseline), '--python', sys.executable, timeout=90)
    requirements = [name + ' @ git+' + URL + '@' + old + '#subdirectory=' + subdirectory
                    for name, subdirectory in PACKAGES.items()]
    await updates.process(uv, 'pip', 'install', '--python', str(baseline / '.venv/bin/python'),
                          '--no-sources', *requirements, timeout=90)
    assert_git_install(baseline, old)
    assert payload_markers(baseline) == dict.fromkeys(PACKAGES, 'old payload\n')
    baseline_receipt = environments.receipt_directory(home)
    assert not (baseline_receipt / 'runtime-sources.json').exists()
    assert not (baseline_receipt / 'runtime-installed.json').exists()

    # No patched inventory, graph, pending request, moving-ref proof, or checked
    # policy: the legacy rows must follow from the actual PEP 610 installation.
    rows = await environments.update_inventory(home)
    legacy = {row['package']: row for row in rows if row.get('package') in PACKAGES}
    assert set(legacy) == set(PACKAGES)
    for name, subdirectory in PACKAGES.items():
        row = legacy[name]
        assert {key: row[key] for key in ('url', 'ref', 'current', 'subdirectory', 'status', 'eligible')} == {
            'url': URL, 'ref': old, 'current': old, 'subdirectory': subdirectory,
            'status': 'pinned', 'eligible': False,
        }
        assert not row.get('override')
        assert row['policyOrigin'] == {
            'kind': 'unrecorded', 'url': URL, 'ref': old,
            'revision': old, 'subdirectory': subdirectory,
        }
    pending = migration.pending(home, None, rows)
    assert pending is not None
    assert pending['candidates'] == migration.legacy_candidates(rows) == {
        name: {'url': URL, 'ref': old, 'current': old, 'subdirectory': subdirectory}
        for name, subdirectory in PACKAGES.items()
    }
    assert pending['configuration'] == migration.configuration(config)

    project = await environments.stage(manager, GENERATION, [], finalize=False)
    assert project != baseline
    await updates.process(uv, 'sync', '--locked', '--project', str(project),
                          '--python', sys.executable, timeout=90)
    assert_git_install(project, old)
    checked_path = stage / 'runtime-sources.json'
    checked = json.loads(checked_path.read_text())
    assert set(checked) == set(PACKAGES)
    for name, subdirectory in PACKAGES.items():
        assert {key: checked[name][key] for key in ('url', 'ref', 'revision', 'subdirectory')} == {
            'url': URL, 'ref': old, 'revision': old, 'subdirectory': subdirectory}
        assert checked[name]['policyOrigin']['kind'] == 'unrecorded'
    assert not (stage / 'runtime.lock').exists()
    overrides = await qualification.prepare_overrides(project, stage / 'runtime-install-overrides.txt')
    preserved_files = [pointer, failure, settings_file,
                       baseline_receipt / 'runtime.toml', baseline_receipt / 'runtime.lock']
    preserved = {path: path.read_bytes() for path in preserved_files}
    baseline_graph = qualification.installed_graph(baseline)
    baseline_payload = qualification.package_content(baseline)

    original_activator = activator.ModuleActivator
    class OwnedActivator(original_activator):
        def __init__(self, *args, **kwargs):
            kwargs['install_python'] = str(project / '.venv/bin/python')
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(activator, 'ModuleActivator', OwnedActivator)
    collections = []
    async def collect():
        assert all(not entry.batch.installed for entry in collections)
        resolved = await SimpleSourceResolver(cache_dir=cache).resolve(REFERENCE)
        assert resolved.active_path == root
        collector = migration.Collector()
        collector.observe(REFERENCE, config)
        context_path = root / PACKAGES['fixture-context']
        if explicit_local:
            selected = config.module_sources['context-fixture']
            collector.observe(selected, config, explicit_local=True)
            context_path = (config.workspace / selected).resolve()
        bundle = Bundle(name='fixture', base_path=root,
            session={'context': {'module': 'context-fixture', 'source': str(context_path)}},
            tools=[{'module': 'transcript-fixture', 'source': str(root / PACKAGES['fixture-transcript'])}])
        batch = DependencyBatch()
        await bundle.prepare(strict=True, refresh_dependencies=True, dependency_batch=batch,
                             cache_dir=cache, install_overrides=overrides)
        assert batch.names == {name: root / subdirectory for name, subdirectory in PACKAGES.items()}
        assert batch.report()['resolverTransactions'] == 0
        report = await collector.report(batch, config, allow_generated=True)
        collections.append(SimpleNamespace(batch=batch, collector=collector, report=report))
        return report

    def assert_preserved():
        assert {path: path.read_bytes() for path in preserved} == preserved
        assert qualification.installed_graph(baseline) == baseline_graph
        assert qualification.package_content(baseline) == baseline_payload
        assert payload_markers(baseline) == dict.fromkeys(PACKAGES, 'old payload\n')
        assert not (baseline_receipt / 'runtime-sources.json').exists()
        assert not (baseline_receipt / 'runtime-installed.json').exists()

    return SimpleNamespace(home=home, stage=stage, project=project, root=root, old=old, new=new,
        config=config, manager=manager, collect=collect, collections=collections, commands=commands,
        checked=checked, checked_path=checked_path, assert_preserved=assert_preserved)


async def test_real_legacy_siblings_migrate_together_before_batch_install_and_freeze(legacy_siblings):
    c = legacy_siblings
    await migration.reconcile(c.manager, c.stage, c.project, c.collect)
    assert len(c.collections) == 2
    for entry, revision in zip(c.collections, (c.old, c.new), strict=True):
        assert {name: (entry.report['packages'][name]['status'], entry.report['packages'][name]['revision'])
                for name in PACKAGES} == dict.fromkeys(PACKAGES, ('selected', revision))
    receipt = json.loads((c.stage / migration.RECEIPT).read_text())
    assert receipt['decisions'] == dict.fromkeys(PACKAGES, 'migrated')
    assert receipt['remaining'] == {}
    assert receipt['offeredPackages'] == sorted(PACKAGES)
    assert receipt['selectionVersion'] == migration.SELECTION_VERSION
    checked = json.loads(c.checked_path.read_text())
    assert receipt['migrated'] == checked
    for name, subdirectory in PACKAGES.items():
        assert {key: checked[name][key] for key in ('url', 'ref', 'revision', 'subdirectory')} == {
            'url': URL, 'ref': 'main', 'revision': c.new, 'subdirectory': subdirectory}
        assert checked[name]['policyOrigin']['kind'] == 'selected-declaration'
        assert checked[name]['policyOrigin']['manifestSha256'] == c.collections[-1].report['packages'][name]['manifestSha256']
    assert_git_install(c.project, c.old)  # Reconciliation itself did not install.
    assert payload_markers(c.project) == dict.fromkeys(PACKAGES, 'old payload\n')

    selection = await c.collect()
    await alignment.align_checked_sources(c.manager, c.stage, selection=selection)
    await c.collect()  # Never install any batch composed before alignment.
    fresh = c.collections[-1]
    selection = await alignment.checked_batch_sources(fresh.batch, fresh.collector, c.config, checked)
    alignment.verify_checked_selection(checked, selection, exact=True)
    assert all(entry.batch is not fresh.batch for entry in c.collections[:-1])
    assert await fresh.batch.install() == {'sources': 2, 'packages': 2, 'resolverTransactions': 1}
    assert all(entry.batch.report()['resolverTransactions'] == 0 for entry in c.collections[:-1])
    installed = environments.installed_sources(c.project)
    for name, subdirectory in PACKAGES.items():
        assert {key: installed[name][key] for key in ('url', 'current', 'subdirectory')} == {
            'url': URL, 'current': c.new, 'subdirectory': subdirectory}
        assert not installed[name].get('override')
    assert payload_markers(c.project) == dict.fromkeys(PACKAGES, 'checked payload\n')
    assert {row['version'] for row in qualification.installed_graph(c.project)} == {'0.1.0'}

    # A separate adversarial receipt proves freeze still rejects precisely the
    # context revision mismatch; it never relabels installed B as checked A.
    rejected_generation = 'd' * 32
    rejected = environments.receipt_directory(c.home, rejected_generation)
    rejected.mkdir()
    wrong = {**checked, 'fixture-context': {**checked['fixture-context'], 'revision': c.old}}
    (rejected / 'runtime-sources.json').write_text(json.dumps(wrong))
    with pytest.raises(ValueError) as caught:
        await qualification.freeze(c.manager, rejected_generation, c.project)
    assert caught.value.diagnostic_facts == {
        'reason': 'runtime-installed-source-mismatch', 'package': 'fixture-context',
        'resolutionMismatches': ['revision'], 'expectedRevision': c.old, 'observedRevision': c.new,
    }
    assert not (rejected / 'runtime.lock').exists()
    assert not (rejected / 'runtime-installed.json').exists()
    assert json.loads(c.checked_path.read_text()) == checked

    final = await qualification.freeze(c.manager, GENERATION, c.project)
    qualification.verify_recorded(final, c.stage)
    assert json.loads(c.checked_path.read_text()) == checked
    assert payload_markers(final) == dict.fromkeys(PACKAGES, 'checked payload\n')
    assert {name: (row['url'], row['current'], row['subdirectory'])
            for name, row in environments.installed_sources(final).items()} == {
        name: (URL, c.new, subdirectory) for name, subdirectory in PACKAGES.items()}
    c.assert_preserved()


@pytest.mark.parametrize('legacy_siblings', [True], indirect=True, ids=['explicit-local-sibling'])
async def test_real_legacy_local_sibling_refuses_before_checkout_or_install(legacy_siblings):
    c = legacy_siblings
    before = source_files(c.root)
    graph = qualification.installed_graph(c.project)
    start = len(c.commands)
    with pytest.raises(environments.ProtectedRuntimeSource) as caught:
        await migration.reconcile(c.manager, c.stage, c.project, c.collect)
    assert caught.value.diagnostic_facts == {
        'errorType': 'ValueError', 'reason': 'protected-runtime-source', 'package': 'fixture-context'}
    assert len(c.collections) == 1
    report = c.collections[0].report
    assert report['packages']['fixture-context'] == {'status': 'local'}
    assert report['packages']['fixture-transcript']['status'] == 'selected'
    assert report['protectedPaths'] == [str(c.root / PACKAGES['fixture-context'])]
    assert c.collections[0].batch.report()['resolverTransactions'] == 0
    assert not c.collections[0].batch.installed
    assert not any({'fetch', 'checkout', 'restore'}.intersection(command)
                   for command in c.commands[start:] if command[0] == 'git')
    assert git(c.root, 'rev-parse', 'HEAD') == c.old
    assert source_files(c.root) == before
    assert qualification.installed_graph(c.project) == graph
    assert_git_install(c.project, c.old)
    assert payload_markers(c.project) == dict.fromkeys(PACKAGES, 'old payload\n')
    # Reconcile can write provisional checked policy before preflight refuses;
    # that is not a completed migration or a qualified generation.
    assert json.loads(c.checked_path.read_text())['fixture-context'] == c.checked['fixture-context']
    for name in (migration.RECEIPT, 'runtime.lock', 'runtime-installed.json', 'runtime-project.json'):
        assert not (c.stage / name).exists()
    c.assert_preserved()


@pytest.mark.parametrize('availability', ['full', 'included-only'])
async def test_validate_orders_real_legacy_sibling_collection_and_single_install(legacy_siblings, monkeypatch, availability):
    """Real validate/reconcile/uv/freeze; adapt only the profile probe boundary.

    The synthetic sibling modules have no provider or conversation loop. The
    post-freeze read-only compatibility mount is counted, not claimed tested.
    """
    from amplifier_web import bundles, update_sources, update_sequence
    from amplifier_web.host import config as host_config
    c = legacy_siblings
    c.manager.inventory = await environments.update_inventory(c.home)
    assert set(migration.legacy_candidates(c.manager.inventory)) == set(PACKAGES)
    assert not (environments.receipt_directory(c.home) / migration.RECEIPT).exists()
    if availability == 'included-only':
        from amplifier_web.service import AppService
        from test_update_sequence import Runtime
        service = AppService(c.home, Runtime(), workspace=c.config.workspace)
        manager = updates.UpdateManager(service)
        service.update_manager = manager
        included = {
            'id': 'included', 'kind': 'bundle / module', 'label': 'included',
            'url': URL, 'ref': 'main', 'current': c.old,
            'status': 'not_checked', 'eligible': True, 'updateTier': 'included',
            'appSelection': update_sequence.app_selection(c.home)['witness'],
        }
        rows = [included, *[{**row, 'updateTier': 'other'} for row in c.manager.inventory]]
        async def inventory(**kwargs):
            return rows
        monkeypatch.setattr(manager, 'inventory_sources', inventory)
        try:
            # Exercise the real early stop and persisted availability inventory,
            # not an empty list substituted for the missing legacy siblings.
            await manager.check(tier='included')
            assert service.state['updates']['sequence']['stage'] == 'included'
            assert service.state['updates']['available'] == 1
            c.manager.inventory = manager.inventory
            assert c.manager.inventory == [included]
            assert json.loads((c.home / 'updates/inventory.json').read_text()) == [included]
            assert not migration.legacy_candidates(c.manager.inventory)
        finally:
            await service.close()
    monkeypatch.setattr(bundles, 'profile_candidates', lambda config: ['fixture'])
    monkeypatch.setattr(bundles, 'offered_profiles', lambda config: ['fixture'])
    monkeypatch.setattr(host_config, 'read_config', lambda *args, **kwargs: c.config)
    monkeypatch.setattr(update_sources, 'worker_supports_shared', lambda project: False)
    process = updates.process
    events = []
    async def adapter(*args, **kwargs):
        if not any(str(arg).endswith('update_probe.py') for arg in args):
            return await process(*args, **kwargs)
        if '--source-policy-only' in args:
            events.append('collect')
            report = await c.collect()
            Path(args[args.index('--source-policy-only') + 1]).write_text(json.dumps(report))
        elif '--profiles' in args:
            assert '--checked-sources' in args
            checked = json.loads(Path(args[args.index('--checked-sources') + 1]).read_text())
            await c.collect()
            fresh = c.collections[-1]
            selected = await alignment.checked_batch_sources(fresh.batch, fresh.collector, c.config, checked)
            alignment.verify_checked_selection(checked, selected, exact=True)
            events.append('install')
            assert await fresh.batch.install() == {'sources': 2, 'packages': 2, 'resolverTransactions': 1}
        else:
            assert '--read-only' in args
            assert (c.stage / 'runtime-installed.json').exists()
            events.append('compatibility-boundary')
        return ''
    monkeypatch.setattr(updates, 'process', adapter)
    align = alignment.align_checked_sources
    async def observed_alignment(manager, stage, **kwargs):
        assert 'selection' in kwargs and events and events[-1] == 'collect'
        assert all(not entry.batch.installed for entry in c.collections)
        events.append('align')
        return await align(manager, stage, **kwargs)
    monkeypatch.setattr(alignment, 'align_checked_sources', observed_alignment)
    await updates.UpdateManager.validate(c.manager, c.stage, GENERATION)
    assert events.count('install') == 1
    assert events[-1] == 'compatibility-boundary'
    assert events.index('align') > events.index('collect')
    receipt = json.loads((c.stage / migration.RECEIPT).read_text())
    assert receipt['decisions'] == dict.fromkeys(PACKAGES, 'migrated')
    final = environments.project_path(c.home, GENERATION)
    qualification.verify_recorded(final, c.stage)
    assert payload_markers(final) == dict.fromkeys(PACKAGES, 'checked payload\n')
    assert all(entry.batch.report()['resolverTransactions'] == 0 for entry in c.collections[:-1])
    assert c.collections[-1].batch.report()['resolverTransactions'] == 1
    c.assert_preserved()
