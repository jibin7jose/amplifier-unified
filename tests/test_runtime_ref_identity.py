"""A resolver's commit spelling must not change a checked update policy."""
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

import pytest

from amplifier_web import runtime_environment as environments, runtime_qualification as qualification


REVISION = 'a' * 40
ROW = {'kind': 'runtime dependency', 'package': 'fixture-component',
       'url': 'https://example.invalid/components', 'ref': 'main',
       'subdirectory': 'module', 'current': 'b' * 40, 'latest': REVISION,
       'eligible': True}


def source(query, *, url=ROW['url'], revision=REVISION):
    return urlsplit(url + '?' + urlencode(query, doseq=True) + '#' + revision)


@pytest.mark.parametrize('ref', ['main', 'release/next', 'v1.2.3'])
def test_full_commit_normalization_preserves_checked_identity(ref):
    environments.verify_resolution(source({'rev': REVISION, 'subdirectory': 'module'}), {**ROW, 'ref': ref})


@pytest.mark.parametrize('query,changes,fields', [
    ({'rev': REVISION[:12]}, {}, ['ref']),
    ({'rev': 'c' * 40}, {}, ['ref']),
    ({'branch': REVISION}, {}, ['ref']),
    ({'tag': REVISION}, {}, ['ref']),
    ({'rev': 'other'}, {}, ['ref']),
    ({'rev': [REVISION, 'other']}, {}, ['ref']),
    ({'rev': REVISION, 'branch': 'other'}, {}, ['ref']),
    ({'rev': ''}, {}, ['ref']),
    ({'rev': REVISION}, {'revision': 'c' * 40}, ['ref', 'revision']),
    ({'rev': REVISION}, {'url': 'https://example.invalid/other'}, ['repository']),
    ({'rev': REVISION, 'subdirectory': 'other'}, {}, ['subdirectory']),
    ({'rev': REVISION, 'subdirectory': ['module', 'other']}, {}, ['subdirectory']),
])
def test_normalization_never_conceals_other_identity_changes(query, changes, fields):
    with pytest.raises(ValueError) as caught:
        environments.verify_resolution(source({'subdirectory': 'module', **query}, **changes), ROW)
    assert caught.value.diagnostic_facts['resolutionMismatches'] == fields


@pytest.mark.parametrize('key', ['branch', 'rev', 'tag'])
def test_matching_original_ref_is_unchanged(key):
    environments.verify_resolution(source({key: 'main', 'subdirectory': 'module'}), ROW)


@pytest.fixture
def normalized_runtime(tmp_path, monkeypatch):
    """Real stage/freeze receipts, controlled resolver and installed metadata."""
    manifest = tmp_path / 'manifest.toml'
    manifest.write_text('[project]\nname="fixture-worker"\nversion="1"\ndependencies=["fixture-component"]\n'
                        '[tool.uv.sources]\nfixture-component={git="' + ROW['url'] + '",rev="main",subdirectory="module"}\n')
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    monkeypatch.setattr(environments.shutil, 'which', lambda _: '/fixture/uv')
    lock = 'version=1\n[[package]]\nname="fixture-component"\nversion="1"\nsource={git="' + source({'rev': REVISION, 'subdirectory': 'module'}).geturl() + '"}\n'
    graph = [{'name': ROW['package'], 'version': '1', 'directUrl': {
        'url': ROW['url'], 'subdirectory': 'module',
        'vcs_info': {'vcs': 'git', 'requested_revision': REVISION, 'commit_id': REVISION}}}]

    async def manifest_input(home):
        return [dict(ROW)], manifest.read_bytes()

    async def resolver(*args, **kwargs):
        if args[1] == 'lock':
            project = Path(args[args.index('--project') + 1])
            (project / 'uv.lock').write_text(lock)
        return ''

    class Diagnostics:
        def record(self, *args, **kwargs):
            pass

        async def run(self, name, fn, *args, **kwargs):
            return await fn(*args, **kwargs)

    monkeypatch.setattr(environments, 'update_manifest', manifest_input)
    monkeypatch.setattr('amplifier_web.updates.process', resolver)
    monkeypatch.setattr(qualification, 'installed_graph', lambda _: graph)
    manager = SimpleNamespace(home=tmp_path / 'app', diagnostics=Diagnostics())
    generation = 'c' * 32
    receipt = environments.receipt_directory(manager.home, generation)
    receipt.mkdir(parents=True)
    return manager, generation, receipt, graph


async def test_stage_freeze_and_next_inventory_preserve_checked_branch(normalized_runtime):
    manager, generation, receipt, graph = normalized_runtime
    prepared = await environments.stage(manager, generation, [ROW], finalize=False)
    final = await qualification.freeze(manager, generation, prepared)
    policies = json.loads((receipt / 'runtime-sources.json').read_text())
    assert policies[ROW['package']]['ref'] == 'main'
    assert policies[ROW['package']]['revision'] == REVISION
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    row = next(row for row in environments.inventory(manager.home) if row.get('package') == ROW['package'])
    assert row['ref'] == 'main' and row['eligible'] and row['current'] == REVISION
    # The install remains immutable and exactly pinned; only future policy moves.
    assert '@' + REVISION in (final / 'pyproject.toml').read_text()
    qualification.verify_recorded(final, receipt)
    before = {name: (receipt / name).read_bytes() for name in ('runtime.lock', 'runtime-sources.json')}
    await environments.stage(manager, generation, [{**ROW, 'latest': 'd' * 40}])
    assert before == {name: (receipt / name).read_bytes() for name in before}
    # A missing disposable installation does not lose the branch policy either.
    row = next(row for row in environments.inventory(manager.home, graph=[]) if row.get('package') == ROW['package'])
    assert row['ref'] == 'main' and row['eligible']


@pytest.mark.parametrize('registry_policy', [False, True])
@pytest.mark.parametrize('refuse', [False, True])
async def test_stage_receipts_use_explicit_data_owner(normalized_runtime, monkeypatch, registry_policy, refuse):
    from amplifier_web import deployment, runtime_registry_updates
    manager, generation, receipt, graph = normalized_runtime
    writes = []
    original = deployment.write_private
    if registry_policy:
        monkeypatch.setattr(runtime_registry_updates, 'prepare_manifest',
                            lambda content, *args: (content, {'fixture': 'checked'}))
        monkeypatch.setattr(runtime_registry_updates, 'verify_candidate', lambda *args: None)
    def private(path, value, *, owned_root=None):
        assert owned_root == manager.home
        assert Path(path).is_relative_to(manager.home)
        if Path(path).parent == receipt:
            writes.append(Path(path).name)
            if refuse:
                raise PermissionError('owned receipt refused')
        original(path, value, owned_root=owned_root)
    monkeypatch.setattr(deployment, 'write_private', private)
    if refuse:
        with pytest.raises(PermissionError, match='owned receipt refused'):
            await environments.stage(manager, generation, [ROW], finalize=False)
        assert not (receipt / 'runtime-sources.json').exists()
    else:
        await environments.stage(manager, generation, [ROW], finalize=False)
        assert writes == ([runtime_registry_updates.RECEIPT] if registry_policy else []) + ['runtime-sources.json']
        assert json.loads((receipt / 'runtime-sources.json').read_text())[ROW['package']]['revision'] == REVISION
    assert not (receipt / 'runtime.lock').exists()
    assert not (manager.home / 'updates/active.json').exists()


@pytest.mark.parametrize('refuse', [False, True])
async def test_freeze_receipts_use_explicit_data_owner(normalized_runtime, monkeypatch, refuse):
    from amplifier_web import deployment
    manager, generation, receipt, graph = normalized_runtime
    prepared = await environments.stage(manager, generation, [ROW], finalize=False)
    original, writes = deployment.write_private, []
    def private(path, value, *, owned_root=None):
        assert owned_root == manager.home
        assert Path(path).is_relative_to(manager.home)
        writes.append(Path(path).name)
        if refuse:
            raise PermissionError('owned receipt refused')
        original(path, value, owned_root=owned_root)
    monkeypatch.setattr(deployment, 'write_private', private)
    if refuse:
        with pytest.raises(PermissionError, match='owned receipt refused'):
            await qualification.freeze(manager, generation, prepared)
        assert not (receipt / 'runtime-project.json').exists()
        assert not (receipt / 'runtime.lock').exists()
    else:
        final = await qualification.freeze(manager, generation, prepared)
        assert writes == ['qualification.json', 'runtime-project.json',
                          'runtime-install-overrides.txt', 'runtime-install-policy.json']
        qualification.verify_recorded(final, receipt)
        qualification.verify_install_overrides(final, receipt / 'runtime-install-overrides.txt')
    assert not (manager.home / 'updates/active.json').exists()


@pytest.mark.parametrize('changed', ['url', 'subdirectory', 'commit_id'])
async def test_freeze_cannot_apply_checked_policy_to_a_different_install(normalized_runtime, changed):
    manager, generation, receipt, graph = normalized_runtime
    prepared = await environments.stage(manager, generation, [ROW], finalize=False)
    if changed == 'commit_id':
        graph[0]['directUrl']['vcs_info'][changed] = 'd' * 40
    else:
        graph[0]['directUrl'][changed] = 'different'
    with pytest.raises(ValueError, match='checked source policy'):
        await qualification.freeze(manager, generation, prepared)
    assert not (receipt / 'runtime.lock').exists()
    assert not (receipt / 'runtime-installed.json').exists()


async def test_stage_finalization_keeps_branch_policy_without_freeze(normalized_runtime):
    manager, generation, receipt, graph = normalized_runtime
    await environments.stage(manager, generation, [ROW])
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    row = next(row for row in environments.inventory(manager.home, graph=[]) if row.get('package') == ROW['package'])
    assert row['ref'] == 'main' and row['eligible']


@pytest.mark.parametrize('ref', ['main', REVISION])
async def test_tiered_update_preserves_unchanged_unselected_source_policy(normalized_runtime, monkeypatch, ref):
    manager, generation, receipt, graph = normalized_runtime
    original = environments.update_manifest

    async def unchanged(home):
        rows, content = await original(home)
        # The selected tier contains another source. This dependency is already
        # current and must retain its moving ref (or intentional explicit pin).
        content = content.replace(b'rev="main"', ('rev="' + ref + '"').encode())
        return [{**rows[0], 'current': REVISION, 'ref': ref}], content

    monkeypatch.setattr(environments, 'update_manifest', unchanged)
    prepared = await environments.stage(manager, generation, [{'kind': 'bundle', 'updateTier': 'other'}], finalize=False)
    await qualification.freeze(manager, generation, prepared)
    policy = json.loads((receipt / 'runtime-sources.json').read_text())[ROW['package']]
    assert policy['ref'] == ref
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    row = next(row for row in environments.inventory(manager.home) if row.get('package') == ROW['package'])
    assert row['ref'] == ref and row['eligible'] == (ref == 'main')


@pytest.mark.parametrize('change', ['commit', 'repository', 'subdirectory', 'manifest-ref'])
async def test_unselected_old_policy_cannot_relabel_changed_source(normalized_runtime, monkeypatch, change):
    manager, generation, receipt, graph = normalized_runtime
    original = environments.update_manifest

    async def unchanged(home):
        rows, content = await original(home)
        previous = {**rows[0], 'current': REVISION}
        if change == 'commit':
            previous['current'] = 'c' * 40
        elif change == 'repository':
            previous['url'] = 'https://example.invalid/other'
        elif change == 'subdirectory':
            previous['subdirectory'] = 'other'
        else:
            content = content.replace(b'rev="main"', b'rev="stable"')
        return [previous], content

    monkeypatch.setattr(environments, 'update_manifest', unchanged)
    await environments.stage(manager, generation, [{'kind': 'bundle', 'updateTier': 'other'}], finalize=False)
    assert ROW['package'] not in json.loads((receipt / 'runtime-sources.json').read_text())
