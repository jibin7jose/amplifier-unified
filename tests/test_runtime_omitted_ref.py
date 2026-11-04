"""Foundation declaration defaults must survive real collection and preflight."""
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from amplifier_foundation.paths.resolution import parse_uri
from amplifier_foundation.sources.shared import SharedSourceStore
from amplifier_web import update_sequence, updates
from amplifier_web.host.config import HostConfig
from amplifier_web.runtime_environment import ProtectedRuntimeSource
from amplifier_web.runtime_policy_reconciliation import Collector
from amplifier_web.runtime_source_alignment import checked_batch_sources, verify_checked_selection
from test_runtime_checked_cache import git

URL = 'https://example.invalid/approval'
NAME = 'amplifier-module-hooks-approval'


@pytest.fixture
async def source(tmp_path, monkeypatch):
    """Real Git metadata and shared-store binding, never hand-built reports."""
    stage = tmp_path / 'app/updates/releases' / ('c' * 32)
    root = stage / 'foundation/cache/approval'
    root.mkdir(parents=True)
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'remote', 'add', 'origin', URL)
    for subdir in ('', 'modules/approval'):
        path = root / subdir
        path.mkdir(parents=True, exist_ok=True)
        (path / 'pyproject.toml').write_text('[project]\nname="' + NAME + '"\nversion="1"\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'synthetic approval module')
    commit = git(root, 'rev-parse', 'HEAD')
    marker = root / '.amplifier_cache_meta.json'
    marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': commit}))
    config = HostConfig(stage, stage, {}, stage / 'foundation')
    store = SharedSourceStore(tmp_path / 'app/source-store')
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)

    async def collect(*, shared=True, declaration=None, binding_ref='main', subdir=''):
        selected = root
        if shared:
            selected = await store.bind(config.registry_home / 'cache', URL, binding_ref, commit, existing=root)
            monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(tmp_path / 'app/source-store'))
        else:
            marker.write_text(json.dumps({'git_url': URL, 'ref': binding_ref, 'commit': commit}))
        collector = Collector()
        # Observe through the host's real effective-module-source hook.
        from amplifier_web.host.session import module_source
        config._source_policy_observer = collector
        uri = declaration if declaration is not None else 'git+' + URL
        if subdir:
            uri += '#subdirectory=' + subdir
        assert module_source(config, False, 'hooks-approval', uri, None) == uri
        batch = SimpleNamespace(names={NAME: selected / subdir})
        policy = {NAME: {'url': URL, 'ref': binding_ref, 'revision': commit, 'subdirectory': subdir}}
        return batch, collector, policy

    return SimpleNamespace(collect=collect, root=root, config=config, stage=stage,
                           commit=commit, marker=marker)


@pytest.mark.parametrize('ref', [None, 'HEAD', 'main', 'master', 'feature/topic', 'v1.2.3', 'a' * 40])
@pytest.mark.parametrize('subdir', ['', 'modules/approval'])
def test_git_declarations_match_canonical_resolver_without_aliasing_explicit_refs(ref, subdir):
    uri = 'git+' + URL + (('@' + ref) if ref is not None else '')
    if subdir:
        uri += '#subdirectory=' + subdir
    canonical = parse_uri(uri)
    assert canonical.ref == (ref if ref is not None else 'main')
    assert canonical.subpath == subdir
    assert update_sequence.git_source(uri) == {'url': URL, 'ref': canonical.ref}
    assert updates.source_key(URL, 'HEAD') != updates.source_key(URL, 'main')


@pytest.mark.parametrize('shared', [False, True])
@pytest.mark.parametrize('subdir', ['', 'modules/approval'])
async def test_omitted_ref_real_collector_preflight_selects_main(source, shared, subdir):
    batch, collector, policy = await source.collect(shared=shared, subdir=subdir)
    report = await checked_batch_sources(batch, collector, source.config, policy)
    verify_checked_selection(policy, report, exact=True)
    assert report['packages'][NAME].items() >= {
        'status': 'selected', 'url': URL, 'ref': 'main',
        'revision': source.commit, 'subdirectory': subdir}.items()
    assert len(report['packages'][NAME]['manifestSha256']) == 64


@pytest.mark.parametrize('shared', [False, True])
async def test_old_omitted_head_interpretation_reproduces_real_unbound_refusal(source, monkeypatch, shared):
    current = update_sequence.git_source
    def old_parser(uri):
        parsed = current(uri)
        if parsed and '@' not in urlsplit(uri[4:]).path:
            return {**parsed, 'ref': 'HEAD'}
        return parsed
    monkeypatch.setattr(update_sequence, 'git_source', old_parser)
    batch, collector, policy = await source.collect(shared=shared)
    assert (await collector.report(batch, source.config))['packages'][NAME] == {'status': 'unbound'}
    with pytest.raises(ProtectedRuntimeSource) as caught:
        await checked_batch_sources(batch, collector, source.config, policy)
    assert caught.value.diagnostic_facts['reason'] == 'runtime-source-unbound'


@pytest.mark.parametrize('ref', ['HEAD', 'main', 'master', 'feature/topic', 'v1.2.3', 'commit'])
async def test_explicit_ref_requires_its_own_binding(source, ref):
    ref = source.commit if ref == 'commit' else ref
    batch, collector, policy = await source.collect(declaration='git+' + URL + '@' + ref, binding_ref=ref)
    report = await checked_batch_sources(batch, collector, source.config, policy)
    verify_checked_selection(policy, report, exact=True)
    assert report['packages'][NAME]['ref'] == ref
    assert report['packages'][NAME]['revision'] == source.commit


@pytest.mark.parametrize('case', ['wrong-repository', 'wrong-commit', 'wrong-subdirectory',
    'missing-binding', 'binding-repository', 'binding-commit', 'explicit-HEAD', 'conflicting-HEAD'])
async def test_omitted_ref_never_weakens_source_guard(source, case):
    batch, collector, policy = await source.collect()
    if case == 'wrong-repository':
        policy[NAME]['url'] += '-other'
    elif case == 'wrong-commit':
        policy[NAME]['revision'] = 'd' * 40
    elif case == 'wrong-subdirectory':
        policy[NAME]['subdirectory'] = 'other'
    elif case in {'missing-binding', 'binding-repository', 'binding-commit'}:
        binding = next((source.config.registry_home / 'cache/.source-bindings').glob('*.json'))
        if case == 'missing-binding':
            binding.unlink()
        else:
            data = json.loads(binding.read_text())
            data['git_url' if case == 'binding-repository' else 'commit'] = URL + '-other' if case == 'binding-repository' else 'd' * 40
            binding.write_text(json.dumps(data))
    else:
        if case == 'explicit-HEAD':
            collector.references.clear()
        collector.observe('git+' + URL + '@HEAD', source.config)
    before = {p: p.read_bytes() for p in source.stage.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        report = await checked_batch_sources(batch, collector, source.config, policy)
        verify_checked_selection(policy, report, exact=True)
    assert before == {p: p.read_bytes() for p in source.stage.rglob('*') if p.is_file()}


@pytest.mark.parametrize('status,reason', [('unbound', 'runtime-source-unbound'),
                                        ('conflicting', 'runtime-source-conflict')])
async def test_source_reason_survives_diagnostics_and_support_export(source, status, reason):
    from amplifier_web.service import AppService
    from amplifier_web.update_diagnostics import PROBE_PREFIX, failure_guidance, probe_failure, probe_record
    from amplifier_web.update_report import event
    from test_updates import Runtime
    service = AppService(source.stage / 'diagnostic-app', Runtime(), workspace=source.stage)
    manager = updates.UpdateManager(service)
    service.update_manager = manager
    batch, collector, policy = await source.collect(declaration='git+' + URL + '@HEAD')
    if status == 'conflicting':
        collector.observe('git+' + URL, source.config)
    async def preflight():
        await checked_batch_sources(batch, collector, source.config, policy)
    try:
        with pytest.raises(ProtectedRuntimeSource) as caught:
            await manager.diagnostics.run('ecosystem-source-preflight', preflight)
        exported = event(manager.diagnostics.state['lastFailure'])
        probe = probe_record(PROBE_PREFIX + json.dumps(probe_failure(caught.value, 'prepare')))
        for receipt in (exported, probe):
            assert receipt['reason'] == reason
            assert receipt['package'] == NAME
            assert 'does not establish local edits' in failure_guidance(receipt)
            assert URL not in json.dumps(receipt) and str(source.stage) not in json.dumps(receipt)
        assert 'protected changes' not in str(caught.value)
        assert 'reason' not in probe_record(PROBE_PREFIX + json.dumps({'ok': False, 'reason': '/private/not-a-reason'}))
    finally:
        await service.close()


def test_inventory_and_registry_retarget_keep_omitted_ref_and_explicit_head_distinct(tmp_path, monkeypatch):
    from amplifier_web import runtime_environment, builtin_behaviors
    from amplifier_web.staged_sources import retarget_registry
    manifest = tmp_path / 'pyproject.toml'
    manifest.write_text('[project]\ndependencies=[]\n')
    behaviors = tmp_path / 'behaviors'
    behaviors.mkdir()
    behaviors.joinpath('approval.yaml').write_text('hooks:\n- source: git+' + URL + '\n')
    monkeypatch.setattr(runtime_environment, 'manifest_path', lambda: manifest)
    monkeypatch.setattr(builtin_behaviors, 'resource_root', lambda: tmp_path)
    assert update_sequence.included_sources()[1] == [{'url': URL, 'ref': 'main'}]
    original, target = tmp_path / 'original', tmp_path / 'target'
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'bundles': {
        'omitted': {'uri': 'git+' + URL, 'local_path': str(original / 'sub')},
        'explicit': {'uri': 'git+' + URL + '@HEAD', 'local_path': str(original / 'sub')},
    }}))
    retarget_registry(registry, URL, 'main', original, target)
    rows = json.loads(registry.read_text())['bundles']
    assert rows['omitted']['local_path'] == str(target / 'sub')
    assert rows['explicit']['local_path'] == str(original / 'sub')
