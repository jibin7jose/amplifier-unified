"""Legacy migration uses selected declarations and a new candidate only."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_policy_reconciliation as migration
from amplifier_web import runtime_environment as environments, updates
from amplifier_web.host.config import HostConfig
from test_runtime_checked_cache import Diagnostics
from test_runtime_ref_identity import normalized_runtime

URL = 'https://example.invalid/component'
OLD = 'a' * 40
NEW = 'b' * 40
NAME = 'fixture-component'


def row(**changes):
    return {'kind': 'runtime dependency', 'package': NAME, 'url': URL, 'ref': OLD,
            'current': OLD, 'subdirectory': '', 'policyOrigin': {'kind': 'unrecorded'}, **changes}


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def selected(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    stage = home / 'updates/releases' / ('c' * 32)
    root = stage / 'foundation/cache/fixture'
    root.mkdir(parents=True)
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'remote', 'add', 'origin', URL)
    (root / 'pyproject.toml').write_text('[project]\nname="' + NAME + '"\nversion="1"\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'old')
    old = git(root, 'rev-parse', 'HEAD')
    (root / 'version.txt').write_text('new')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'new')
    new = git(root, 'rev-parse', 'HEAD')
    repo = tmp_path / 'upstream'
    shutil.copytree(root, repo)
    git(root, 'checkout', '--detach', old)
    marker = root / '.amplifier_cache_meta.json'
    marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': old}))
    config = HostConfig(stage, stage, {}, stage / 'foundation')
    batch = SimpleNamespace(names={NAME: root})
    collector = migration.Collector()
    collector.observe('git+' + URL + '@main', config)
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    async def publish(**kw): pass
    manager.publish = publish
    manager.stage_cache_sources = lambda s, rows: updates.UpdateManager.stage_cache_sources(manager, s, rows)
    original = updates.process
    commands = []
    async def process(*args, **kwargs):
        commands.append(args)
        if 'fetch' in args or 'ls-remote' in args:
            args = tuple(str(repo) if a == URL else a for a in args)
        return await original(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    return SimpleNamespace(home=home, stage=stage, root=root, repo=repo, old=old, new=new,
        marker=marker, config=config, batch=batch, collector=collector, manager=manager, commands=commands)


@pytest.mark.parametrize('change', [{'override': True}, {'ref': 'main'}, {'ref': 'v1.2'},
    {'current': NEW}, {'policyOrigin': {'kind': 'prepared-manifest'}}, {'url': ''}])
def test_only_unrecorded_exact_commit_policies_are_candidates(change):
    assert migration.legacy_candidates([row(**change)]) == {}


async def test_actual_selected_batch_binds_package_and_branch(selected):
    s = selected
    report = await s.collector.report(s.batch, s.config)
    package = report['packages'][NAME]
    assert package['status'] == 'selected'
    assert package['revision'] == s.old
    assert package['ref'] == 'main'
    assert package['subdirectory'] == ''


@pytest.mark.parametrize('case', ['pin', 'conflict', 'unused', 'local', 'different-repository',
                                  'different-subdirectory', 'separate-config'])
async def test_explicit_or_unrelated_choices_are_not_migrated(selected, case):
    s = selected
    s.collector.references.clear()
    if case == 'pin':
        s.collector.observe('git+' + URL + '@' + s.old, s.config)
    elif case == 'conflict':
        for ref in ('main', 'stable'):
            s.collector.observe('git+' + URL + '@' + ref, s.config)
    elif case != 'unused':
        s.collector.observe('git+' + URL + '@main', s.config)
    if case == 'local':
        s.collector.observe(str(s.root), s.config, explicit_local=True)
    if case == 'separate-config':
        (s.root / 'uv.toml').write_text('')
    report = await s.collector.report(s.batch, s.config)
    old = row(ref=s.old, current=s.old)
    if case == 'different-repository': old['url'] += '-fork'
    if case == 'different-subdirectory': old['subdirectory'] = 'module'
    assert not migration.selected_policies(migration.legacy_candidates([old]), report)[0]


@pytest.mark.parametrize('case', ['dirty', 'wrong-head', 'redirected-git', 'linked-source', 'different-name'])
async def test_untrusted_cache_identity_cannot_establish_policy(selected, case, tmp_path):
    s = selected
    if case == 'dirty': (s.root / 'pyproject.toml').write_text('changed')
    elif case == 'wrong-head':
        s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': s.new}))
    elif case == 'redirected-git': git(s.root, 'config', 'core.worktree', str(s.repo))
    elif case == 'linked-source':
        external = tmp_path / 'external'
        s.root.rename(external); s.root.symlink_to(external, target_is_directory=True)
    else:
        s.batch.names = {'another-name': s.root}
    with pytest.raises(ValueError):
        await s.collector.report(s.batch, s.config)


@pytest.mark.parametrize('source_owner', ['module', 'host-component'])
async def test_stale_selected_root_advances_before_install_and_old_generation_is_untouched(selected, monkeypatch, source_owner):
    s = selected
    from amplifier_web import deployment
    original_write, receipt_writes = deployment.write_private, []
    def write_owned(path, value, *, owned_root=None):
        if Path(path).parent == s.stage:
            assert owned_root == s.home
            receipt_writes.append(Path(path).name)
        return original_write(path, value, owned_root=owned_root)
    monkeypatch.setattr(deployment, 'write_private', write_owned)
    if source_owner == 'host-component':
        from amplifier_foundation import Bundle
        from amplifier_web.host.components import HostComponents
        from amplifier_web.host.session import module_source
        components = HostComponents()
        components.select_bundle(Bundle(name='host-tools', tools=[{
            'module': 'fixture', 'source': 'git+' + URL + '@main'}]), {})
        s.collector.references.clear()
        s.config._source_policy_observer = s.collector
        assert module_source(s.config, False, 'fixture', 'git+' + URL + '@main', components) == 'git+' + URL + '@main'
    oldrow = row(ref=s.old, current=s.old)
    request = {'version': 1, 'configuration': migration.configuration(s.config),
               'candidates': migration.legacy_candidates([oldrow])}
    monkeypatch.setattr(environments, 'update_inventory', lambda home: _value([oldrow]))
    monkeypatch.setattr(migration, 'pending', lambda *args, **kw: request)
    (s.stage / 'runtime-sources.json').write_text(json.dumps({NAME: {
        'url': URL, 'ref': s.old, 'revision': s.old, 'subdirectory': ''}}))
    old_receipt = s.home / 'updates/baseline-runtime'
    old_receipt.mkdir()
    (old_receipt / 'runtime-sources.json').write_text('old receipt stays unchanged')
    project = s.home / 'runtime/preparation'
    project.mkdir(parents=True)
    reports = []
    async def collect():
        result = await s.collector.report(s.batch, s.config)
        reports.append(result)
        return result
    await migration.reconcile(s.manager, s.stage, project, collect)
    saved = json.loads((s.stage / 'runtime-sources.json').read_text())[NAME]
    assert saved['revision'] == s.new and saved['ref'] == 'main'
    assert saved['policyOrigin']['kind'] == 'selected-declaration'
    assert [r['packages'][NAME]['revision'] for r in reports] == [s.old, s.new]
    assert git(s.root, 'rev-parse', 'HEAD') == s.new
    assert (old_receipt / 'runtime-sources.json').read_text() == 'old receipt stays unchanged'
    receipt = json.loads((s.stage / migration.RECEIPT).read_text())
    assert receipt['decisions'] == {NAME: 'migrated'} and not receipt['remaining']
    assert receipt_writes == ['runtime-sources.json', 'runtime-sources.json', migration.RECEIPT]


async def _value(value): return value


def test_acknowledged_unselected_pin_does_not_prompt_again_for_same_configuration(selected, monkeypatch):
    s = selected
    oldrow = row()
    monkeypatch.setattr('amplifier_web.host.config.read_config', lambda *a, **kw: s.config)
    receipt = s.home / 'updates/baseline-runtime'
    receipt.mkdir()
    assert migration.pending(s.home, None, [oldrow])
    receipt.joinpath(migration.RECEIPT).write_text(json.dumps({
        'version': 1, 'selectionVersion': migration.SELECTION_VERSION, 'configuration': migration.configuration(s.config),
        'remaining': migration.legacy_candidates([oldrow])}))
    assert migration.pending(s.home, None, [oldrow]) is None
    s.config.settings['sources'] = {'modules': {'some-module': 'git+' + URL + '@main'}}
    assert migration.pending(s.home, None, [oldrow])


async def test_recorded_generation_is_never_reconciled(selected):
    selected.stage.joinpath('runtime.lock').write_text('frozen')
    with pytest.raises(ValueError, match='recorded'):
        await migration.reconcile(selected.manager, selected.stage, selected.home / 'runtime/a', lambda: None)


async def test_inactive_catalog_entries_are_never_observed(selected):
    s = selected
    s.config.settings['bundle'] = {'added': {'unused': 'git+' + URL + '@' + s.old}}
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['ref'] == 'main'


async def test_host_observes_effective_module_override(selected):
    from amplifier_web.host.session import module_source
    s = selected
    s.config._source_policy_observer = migration.Collector()
    s.config.settings['sources'] = {'modules': {'fixture': 'git+' + URL + '@' + s.old}}
    components = SimpleNamespace(source=lambda m, source, **kw: source, owns=lambda m, source: False)
    assert module_source(s.config, False, 'fixture', 'git+' + URL + '@main', components).endswith(s.old)
    assert s.config._source_policy_observer.references == {(URL, s.old)}

@pytest.mark.parametrize('case', ['tag-only', 'branch-and-tag', 'branch-only', 'missing'])
async def test_non_numeric_tags_and_same_name_ambiguity_never_become_branches(case):
    async def process(*args, **kwargs):
        lines = []
        if case in {'branch-only', 'branch-and-tag'}: lines.append(NEW + '\trefs/heads/stable')
        if case in {'tag-only', 'branch-and-tag'}: lines.append(OLD + '\trefs/tags/stable')
        return '\n'.join(lines)
    assert await migration.moving_revision(URL, 'stable', process) == (NEW if case == 'branch-only' else None)


@pytest.mark.parametrize('change', ['content', 'empty-commit'])
async def test_reconciliation_accepts_exact_new_branch_commit_even_without_content_change(selected, monkeypatch, change):
    s = selected
    if change == 'empty-commit':
        git(s.repo, 'checkout', '--detach', s.old)
        git(s.repo, 'checkout', '-B', 'main')
        git(s.repo, 'commit', '--allow-empty', '-m', 'new identity same contents')
        s.new = git(s.repo, 'rev-parse', 'HEAD')
    oldrow = row(ref=s.old, current=s.old)
    request = {'version': 1, 'configuration': migration.configuration(s.config),
               'candidates': migration.legacy_candidates([oldrow])}
    monkeypatch.setattr(environments, 'update_inventory', lambda home: _value([oldrow]))
    monkeypatch.setattr(migration, 'pending', lambda *args, **kw: request)
    (s.stage / 'runtime-sources.json').write_text(json.dumps({NAME: {
        'url': URL, 'ref': s.old, 'revision': s.old, 'subdirectory': ''}}))
    project = s.home / 'runtime/preparation'
    project.mkdir(parents=True)
    await migration.reconcile(s.manager, s.stage, project, lambda: s.collector.report(s.batch, s.config))
    assert json.loads((s.stage / 'runtime-sources.json').read_text())[NAME]['revision'] == s.new


async def test_changed_declaration_after_refresh_cannot_pass_into_install(selected, monkeypatch):
    s = selected
    oldrow = row(ref=s.old, current=s.old)
    request = {'version': 1, 'configuration': migration.configuration(s.config),
               'candidates': migration.legacy_candidates([oldrow])}
    monkeypatch.setattr(environments, 'update_inventory', lambda home: _value([oldrow]))
    monkeypatch.setattr(migration, 'pending', lambda *args, **kw: request)
    (s.stage / 'runtime-sources.json').write_text(json.dumps({NAME: {
        'url': URL, 'ref': s.old, 'revision': s.old, 'subdirectory': ''}}))
    project = s.home / 'runtime/preparation'
    project.mkdir(parents=True)
    count = 0
    async def collect():
        nonlocal count
        count += 1
        if count == 2: s.collector.references = {(URL, s.new)}
        return await s.collector.report(s.batch, s.config)
    with pytest.raises(ValueError, match='changed after'):
        await migration.reconcile(s.manager, s.stage, project, collect)
    assert not (s.stage / migration.RECEIPT).exists()


def test_optional_pin_is_protective_even_if_not_selected_to_authorize_recovery():
    raw = ('[project]\nname="another"\nversion="1"\n'
           '[project.optional-dependencies]\nunused=["' + NAME + ' @ git+' + URL + '@' + OLD + '"]\n').encode()
    assert NAME in migration.authored_constraints(raw)
    assert migration.authored_constraints(raw)[NAME] is None  # Protective, never selection authority.


@pytest.mark.parametrize('change', ['none', 'unselected', 'outside', 'optional', 'runtime', 'explicit-local', 'nested', 'dynamic'])
async def test_selected_sibling_dev_path_is_not_a_runtime_pin(selected, change):
    s = selected
    context = s.root / 'modules/context-fixture'
    transcript = s.root / 'modules/transcript-fixture'
    context.mkdir(parents=True); transcript.mkdir()
    (context / 'pyproject.toml').write_text('[project]\nname="fixture-context"\nversion="0.1"\n')
    edge = '../../../outside' if change == 'outside' else '../context-fixture'
    extra = ('dependencies=["fixture-context"]\n' if change == 'runtime' else '')
    extra += ('dynamic=["dependencies"]\n' if change == 'dynamic' else '')
    extra += ('[project.optional-dependencies]\nunused=["fixture-context"]\n' if change == 'optional' else '')
    (transcript / 'pyproject.toml').write_text(
        '[project]\nname="fixture-transcript"\nversion="0.1"\n' + extra +
        '[tool.uv.sources]\nfixture-context={path="' + edge + '",editable=true}\n'
        '[dependency-groups]\ndev=["fixture-context"]\n')
    git(s.root, 'add', 'modules')
    git(s.root, 'commit', '-m', 'sibling modules')
    head = git(s.root, 'rev-parse', 'HEAD')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': head}))
    s.batch.names = {'fixture-context': context, 'fixture-transcript': transcript}
    if change == 'unselected':
        s.batch.names.pop('fixture-context')
    if change == 'explicit-local':
        s.collector.local_sources.add(context)
    if change == 'nested':
        git(context, 'init', '-b', 'main')
        git(context, 'config', 'user.email', 'fixture@example.invalid')
        git(context, 'config', 'user.name', 'Fixture')
        git(context, 'remote', 'add', 'origin', URL + '-other')
        git(context, 'add', 'pyproject.toml')
        git(context, 'commit', '-m', 'separately owned nested package')
        nested_head = git(context, 'rev-parse', 'HEAD')
        (context / '.amplifier_cache_meta.json').write_text(json.dumps({
            'git_url': URL + '-other', 'ref': 'main', 'commit': nested_head}))
        s.collector.references.add(updates.source_key(URL + '-other', 'main'))
    report = await s.collector.report(s.batch, s.config)
    if change == 'none':
        assert report['packages']['fixture-context']['status'] == 'selected'
        assert report['packages']['fixture-context']['revision'] == head
    elif change != 'unselected':
        assert report['packages']['fixture-context']['status'] != 'selected'


async def test_preliminary_collection_allows_only_recognized_artifacts_before_alignment(selected):
    from amplifier_web import runtime_source_alignment as alignment
    from test_updates_cache import pyc
    s = selected
    cache = s.root / '__pycache__'
    cache.mkdir()
    artifact = cache / 'module.cpython-313.pyc'
    artifact.write_bytes(pyc('value = 1'))
    git(s.root, 'add', '__pycache__')
    git(s.root, 'commit', '-m', 'tracked fixture artifact')
    head = git(s.root, 'rev-parse', 'HEAD')
    git(s.root, 'push', str(s.repo), 'HEAD:refs/heads/artifact-fixture')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': head}))
    (s.stage / 'runtime-sources.json').write_text(json.dumps({NAME: {
        'url': URL, 'ref': 'main', 'revision': head, 'subdirectory': ''}}))
    artifact.write_bytes(pyc('value = 2'))
    with pytest.raises(ValueError, match='changed'):
        await s.collector.report(s.batch, s.config)
    preliminary = await s.collector.report(s.batch, s.config, allow_generated=True)
    await alignment.align_checked_sources(s.manager, s.stage, selection=preliminary)
    strict = await s.collector.report(s.batch, s.config)
    assert strict['packages'][NAME]['revision'] == head
    assert artifact.read_bytes() == pyc('value = 1')


@pytest.mark.parametrize('key', ['UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT'])
def test_explicit_external_resolver_policy_does_not_get_a_migration_prompt(selected, monkeypatch, key):
    monkeypatch.setenv(key, '/external/policy')
    assert migration.pending(selected.home, None, [row()]) is None


async def test_declaration_probe_collects_without_install_or_live_session(selected, monkeypatch):
    import asyncio
    import runpy
    import sys
    from amplifier_foundation.modules.batch import DependencyBatch
    from amplifier_web.host import session, config
    s = selected
    profiles = s.stage / 'profiles.json'
    profiles.write_text(json.dumps(['one', 'two']))
    target = s.stage / 'declarations.json'
    collected, prepared = [], []
    async def prepare(workspace, **kwargs):
        assert kwargs['global_only'] is True
        assert kwargs['source_policy_collector'] is not None
        assert kwargs['dependency_batch'] is not None
        kwargs['source_policy_collector'].observe('git+' + URL + '@main', s.config)
        kwargs['dependency_batch'].names[NAME] = s.root
        prepared.append(kwargs['bundle'])
    async def forbidden(*args, **kwargs):
        raise AssertionError('Collection must never install or mount a session')
    monkeypatch.setattr(session, 'prepare_dependencies', prepare)
    monkeypatch.setattr(session, 'prepare_manager', forbidden)
    monkeypatch.setattr(DependencyBatch, 'install', forbidden)
    monkeypatch.setattr(config, 'load_config', lambda *a, **kw: s.config)
    monkeypatch.setattr(sys, 'argv', ['probe', str(s.stage), 'one', '--profiles', str(profiles),
                                    '--source-policy-only', str(target)])
    with monkeypatch.context() as context:
        context.setattr(asyncio, 'run', lambda coroutine: collected.append(coroutine))
        runpy.run_module('amplifier_web.update_probe', run_name='__main__')
    assert len(collected) == 1
    await collected[0]
    assert prepared == ['one', 'two']
    assert json.loads(target.read_text())['packages'][NAME]['revision'] == s.old
    assert not (s.stage / 'foundation/profile-characteristics.json').exists()


def test_reconcile_reconsiders_receipted_pins_when_another_update_already_has_a_candidate(selected, monkeypatch):
    s = selected
    monkeypatch.setattr('amplifier_web.host.config.read_config', lambda *a, **kw: s.config)
    receipt = s.home / 'updates/baseline-runtime'
    receipt.mkdir()
    receipt.joinpath(migration.RECEIPT).write_text(json.dumps({
        'version': 1, 'selectionVersion': migration.SELECTION_VERSION, 'configuration': migration.configuration(s.config),
        'remaining': migration.legacy_candidates([row()])}))
    assert migration.pending(s.home, None, [row()]) is None
    assert migration.pending(s.home, None, [row()], honor_receipt=False)

def test_available_unselected_provider_does_not_authorize_legacy_pin_recovery():
    candidates = migration.legacy_candidates([row(package='amplifier-module-provider-vllm')])
    selected, decisions = migration.selected_policies(candidates, {'packages': {
        'selected-provider': {'status': 'selected', 'url': URL, 'ref': 'main',
                              'subdirectory': '', 'revision': NEW, 'manifestSha256': 'x'}}})
    assert selected == {}
    assert decisions == {'amplifier-module-provider-vllm': 'unselected'}


async def test_unrelated_update_and_freeze_preserve_unknown_origin_for_later_reconciliation(normalized_runtime, monkeypatch):
    from amplifier_web import runtime_qualification as qualification
    from test_runtime_ref_identity import ROW, REVISION
    manager, generation, receipt, graph = normalized_runtime
    original = environments.update_manifest
    async def unchanged(home):
        rows, content = await original(home)
        old = {**rows[0], 'current': REVISION, 'ref': REVISION, 'eligible': False,
               'policyOrigin': {'kind': 'unrecorded', 'url': ROW['url'], 'ref': REVISION,
                                'subdirectory': 'module', 'revision': REVISION}}
        return [old], content.replace(b'rev="main"', ('rev="' + REVISION + '"').encode())
    monkeypatch.setattr(environments, 'update_manifest', unchanged)
    project = await environments.stage(manager, generation, [{'kind': 'bundle', 'updateTier': 'other'}], finalize=False)
    await qualification.freeze(manager, generation, project)
    saved = json.loads((receipt / 'runtime-sources.json').read_text())[ROW['package']]
    assert saved['ref'] == REVISION
    assert saved['policyOrigin']['kind'] == 'unrecorded'
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    candidates = migration.legacy_candidates(environments.inventory(manager.home, graph=graph))
    assert ROW['package'] in candidates


@pytest.mark.parametrize('kind', ['unrecorded', 'unresolved-reference'])
def test_unresolved_reference_still_allows_selected_branch_proof(kind):
    assert NAME in migration.legacy_candidates([row(policyOrigin={'kind': kind, 'originalRef': 'stable'})])


@pytest.mark.parametrize('change', ['none', 'configuration', 'identity', 'present', 'old-receipt', 'protected'])
def test_retained_extra_usage_requires_matching_completed_profile_selection(selected, monkeypatch, change):
    s = selected
    monkeypatch.setattr('amplifier_web.host.config.read_config', lambda *a, **kw: s.config)
    rows = [row(usage='configured', usageEvidence=['Conversation worker environment'])]
    saved = {
        'version': 1, 'selectionVersion': migration.SELECTION_VERSION, 'configuration': migration.configuration(s.config),
        'remaining': migration.legacy_candidates(rows), 'decisions': {NAME: 'unselected'},
        'offeredPackages': ['another-package'],
    }
    if change == 'configuration': saved['configuration'] = 'old'
    if change == 'identity': saved['remaining'][NAME]['current'] = NEW
    if change == 'present': saved['offeredPackages'].append(NAME)
    if change == 'old-receipt': del saved['offeredPackages']
    if change == 'protected': saved['decisions'][NAME] = 'authored-constraint'
    receipt = s.home / 'updates/baseline-runtime'
    receipt.mkdir()
    receipt.joinpath(migration.RECEIPT).write_text(json.dumps(saved))
    migration.annotate_retained(s.home, None, rows)
    assert rows[0]['usage'] == ('unknown' if change == 'none' else 'configured')
    assert rows[0]['current'] == OLD

@pytest.mark.parametrize('form', ['relative', 'parent-relative', 'file-subdirectory'])
async def test_real_resolver_local_module_override_never_becomes_branch(selected, monkeypatch, form):
    from amplifier_foundation.sources.resolver import SimpleSourceResolver
    from amplifier_web.host.session import module_source
    s = selected
    monkeypatch.chdir(s.config.workspace)
    if form == 'relative':
        reference = './' + s.root.relative_to(s.config.workspace).as_posix()
    elif form == 'parent-relative':
        reference = '../' + s.config.workspace.name + '/' + s.root.relative_to(s.config.workspace).as_posix()
    else:
        reference = s.root.parent.as_uri() + '#subdirectory=' + s.root.name
    s.config._source_policy_observer = s.collector
    s.config.settings['sources'] = {'modules': {'fixture': reference}}
    components = SimpleNamespace(source=lambda module, source, **kw: source,
                                 owns=lambda module, source: False)
    effective = module_source(s.config, False, 'fixture', 'git+' + URL + '@main', components)
    resolved = await SimpleSourceResolver(cache_dir=s.config.registry_home / 'cache',
                                          base_path=s.config.workspace).resolve(effective)
    assert resolved.active_path == s.root
    s.batch.names[NAME] = resolved.active_path
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == 'local'
    assert migration.selected_policies(migration.legacy_candidates([row(ref=s.old, current=s.old)]), report)[0] == {}


@pytest.mark.parametrize('choice', ['branch', 'pin', 'local', 'installed', 'conflict'])
async def test_host_component_selection_obeys_source_policy_boundaries(selected, choice):
    from amplifier_foundation import Bundle
    from amplifier_web.host.components import HostComponents
    from amplifier_web.host.session import module_source
    s = selected
    s.collector.references.clear()
    s.config._source_policy_observer = s.collector
    chosen = (str(s.root) if choice in {'local', 'installed'} else
              'git+' + URL + '@' + (s.old if choice == 'pin' else 'main'))
    components = HostComponents(installed={'fixture': lambda: str(s.root)} if choice == 'installed' else None)
    components.select_bundle(Bundle(name='host-tools', tools=[{
        'module': 'fixture', 'source': chosen}]), {})
    # A host-owned source wins over an unrelated late module override; record
    # only the source that will actually be prepared, never the losing choice.
    s.config.settings['sources'] = {'modules': {'fixture': chosen if choice == 'local' else 'git+' + URL + '@unused'}}
    assert module_source(s.config, False, 'fixture', 'git+' + URL + '@old', components) == chosen
    if choice == 'conflict':
        s.collector.observe('git+' + URL + '@stable', s.config)
    report = await s.collector.report(s.batch, s.config)
    expected = {'branch': 'selected', 'pin': 'explicit-pin', 'local': 'local', 'installed': 'local', 'conflict': 'conflicting'}
    assert report['packages'][NAME]['status'] == expected[choice]
    policies, _ = migration.selected_policies(
        migration.legacy_candidates([row(ref=s.old, current=s.old)]), report)
    assert bool(policies) == (choice == 'branch')
    assert (URL, 'unused') not in s.collector.references


def test_previous_host_component_observation_receipt_is_rechecked(selected, monkeypatch):
    s = selected
    monkeypatch.setattr('amplifier_web.host.config.read_config', lambda *a, **kw: s.config)
    oldrow = row()
    receipt = s.home / 'updates/baseline-runtime'
    receipt.mkdir()
    receipt.joinpath(migration.RECEIPT).write_text(json.dumps({
        'version': 1, 'selectionVersion': 2, 'configuration': migration.configuration(s.config),
        'remaining': migration.legacy_candidates([oldrow]),
        'decisions': {NAME: 'unselected'}, 'offeredPackages': []}))
    assert migration.pending(s.home, None, [oldrow]) is not None


async def test_relative_host_module_keeps_its_observed_git_behavior_policy(selected):
    from amplifier_foundation import Bundle
    from amplifier_web.host.components import HostComponents
    from amplifier_web.host.session import module_source
    s = selected
    s.config._source_policy_observer = s.collector
    components = HostComponents()
    behavior = Bundle(name='host-tools', tools=[{'module': 'fixture', 'source': './'}])
    behavior.base_path = s.root
    components.select_bundle(behavior, {})
    assert module_source(s.config, False, 'fixture', './', components) == str(s.root)
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == 'selected'
    assert report['packages'][NAME]['ref'] == 'main'
    assert not s.collector.local_sources
