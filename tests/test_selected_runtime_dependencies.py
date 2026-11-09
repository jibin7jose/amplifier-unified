import importlib.metadata
import json
import shutil
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest
from amplifier_web import selected_runtime_dependencies as dependencies
from amplifier_web import runtime_policy_reconciliation as migration
from amplifier_web import runtime_environment as runtime
from amplifier_web import runtime_qualification as qualification
from amplifier_web import updates

URL = 'https://example.invalid/component'
OLD = 'a' * 40
NEW = 'b' * 40
NAME = 'fixture-component'


def manifest(requirements, **project):
    value = {'project': {'name': 'selected-root', 'version': '1', 'dependencies': requirements, **project}}
    return '\n'.join(json.dumps(k) + ' = ' + runtime.toml_value(v) for k, v in value.items()).encode()


def dist(name=NAME, requirements=(), url=URL, revision=OLD, subdirectory='', **direct):
    record = {'url': url, 'vcs_info': {'vcs': 'git', 'commit_id': revision},
              'subdirectory': subdirectory, **direct} if url else {}
    return SimpleNamespace(metadata={'Name': name}, version='1.0', requires=list(requirements),
                           read_text=lambda key: json.dumps(record) if key == 'direct_url.json' else None)


@pytest.mark.parametrize('case', ['selected', 'unselected', 'inactive', 'conditional', 'extra-inactive',
    'extra-active', 'pin', 'fork', 'subdirectory', 'local', 'duplicate', 'optional-conflict', 'dynamic'])
def test_only_selected_exact_dependency_closure_can_recover(case):
    requirement = NAME + ' @ git+' + URL + '@main'
    bridge_requirements = [requirement]
    roots = ['bridge']
    records = [dist(), dist('bridge', bridge_requirements, url=None)]
    extra = {}
    if case == 'unselected': roots = []
    if case == 'inactive': roots = ['bridge ; python_version < "1"']
    if case == 'conditional': records[1].requires = [requirement + ' ; python_version > "1"']
    if case in {'extra-inactive', 'extra-active'}:
        roots = ['bridge[use]'] if case == 'extra-active' else ['bridge']
        records[1].requires = ['middle ; extra == "use"']
        records.append(dist('middle', [requirement], url=None))
    if case == 'pin': records[1].requires = [requirement.replace('@main', '@' + OLD)]
    if case == 'fork': records[0] = dist(url=URL + '-fork')
    if case == 'subdirectory': records[0] = dist(subdirectory='another')
    if case == 'local': records[0] = dist(vcs_info={}, dir_info={'editable': True})
    if case == 'duplicate': records.append(dist())
    if case == 'optional-conflict': extra['optional-dependencies'] = {'unused': [requirement.replace('@main', '@' + OLD)]}
    if case == 'dynamic': extra['dynamic'] = ['dependencies']
    result = dependencies.selected({'selected-root': manifest(roots, **extra)}, distributions=records)
    assert (result.get(NAME, {}).get('status') == 'selected') is (case in {'selected', 'extra-active'})


def test_selected_manifest_git_dependency_does_not_need_module_reference():
    result = dependencies.selected({'selected-root': manifest([NAME + ' @ git+' + URL + '@main'])},
                                   distributions=[dist()])
    assert result[NAME]['ref'] == 'main'
    assert result[NAME]['revision'] == OLD
    assert result[NAME]['sourceKind'] == 'dependency'
    candidate = {'kind': 'runtime dependency', 'package': NAME, 'url': URL, 'ref': OLD, 'current': OLD,
                 'policyOrigin': {'kind': 'unresolved-reference', 'originalRef': 'main'}}
    selected, _ = migration.selected_policies(migration.legacy_candidates([candidate]), {'packages': result})
    assert selected[NAME]['previousRevision'] == OLD
    result[NAME]['revision'] = NEW
    assert not migration.selected_policies(migration.legacy_candidates([candidate]), {'packages': result})[0]


@pytest.mark.parametrize('value', [
    NAME + ' @ git+' + URL + '@' + OLD,
    NAME + ' @ git+' + URL + '@main ; python_version < "1"',
    NAME + ' @ file:///configured/local',
])
def test_inactive_or_local_competing_requirement_still_protects(value):
    direct = NAME + ' @ git+' + URL + '@main'
    result = dependencies.selected({'selected-root': manifest([direct, value])}, distributions=[dist()])
    assert result.get(NAME, {}).get('status') != 'selected'


@pytest.mark.parametrize('ref', ['tag-only', 'both', 'branch'])
async def test_real_selected_dependency_only_recovers_after_exact_branch_proof(tmp_path, monkeypatch, ref):
    from test_runtime_source_policy_resolver import BACKEND, git
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('Real resolver regression requires uv')
    repo = tmp_path / 'repo'; repo.mkdir()
    git(repo, 'init', '-b', 'main' if ref != 'tag-only' else 'other')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture-component"\nversion="1.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (repo / 'backend.py').write_text(BACKEND)
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'old')
    old = git(repo, 'rev-parse', 'HEAD')
    if ref != 'branch': git(repo, 'tag', 'main')
    (repo / 'revision.txt').write_text('new')
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'new')
    new = git(repo, 'rev-parse', 'HEAD')
    url = URL
    git_config = tmp_path / 'gitconfig'
    git_config.write_text('[url "' + repo.as_uri() + '"]\n\tinsteadOf = ' + url + '\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(git_config))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    home, generation = tmp_path / 'app', 'c' * 32
    stage = runtime.receipt_directory(home, generation); stage.mkdir(parents=True)
    baseline = b'[project]\nname="worker"\nversion="1"\n'
    base_manifest = tmp_path / 'app-runtime.toml'
    base_manifest.write_bytes(baseline)
    monkeypatch.setattr(runtime, 'manifest_path', lambda: base_manifest)
    # This resolver fixture owns its one-package graph. Optional support packs
    # installed in the test runner must not enter the later freeze manifest.
    monkeypatch.setattr(runtime, 'declared_manifest', lambda **_: baseline)
    content = runtime.augmented_manifest(baseline, [{'kind':'runtime dependency', 'package':NAME,
                                                   'url':url,'ref':old}])
    project = runtime.prepare_project(home, generation, content=content)
    original_manifest = tomllib.loads(content.decode())
    assert original_manifest['tool']['amplifier-unified']['generated-source-overrides'][NAME].endswith('@' + old)
    await updates.process(uv, 'sync', '--project', str(project), '--python', sys.executable, timeout=90)
    paths = [str(path) for path in (project / '.venv').glob('lib/python*/site-packages')]
    authored = manifest([NAME + ' @ git+' + url + '@main'])
    def collect():
        return {'version':1, 'configuration':'fixture', 'batchPaths': {}, 'protectedPaths': [],
                'packages': dependencies.selected(
            {'selected-root':authored}, distributions=importlib.metadata.distributions(path=paths))}
    first = collect()
    assert first['packages'][NAME]['revision'] == old
    candidate = {'kind':'runtime dependency','package':NAME,'url':url,'ref':old,'current':old,
                 'policyOrigin':{'kind':'unresolved-reference','originalRef':'main'}}
    monkeypatch.setattr(runtime, 'update_inventory', lambda _: value([candidate]))
    monkeypatch.setattr(migration, 'pending', lambda *a, **k: {'configuration':'fixture',
                          'candidates':migration.legacy_candidates([candidate])})
    stage.joinpath('runtime-sources.json').write_text(json.dumps({NAME:{
        'url':url,'ref':old,'revision':old,'subdirectory':''}}))
    manager = SimpleNamespace(home=home, stage_cache_sources=lambda *a: value(None))
    await migration.reconcile(manager, stage, project, lambda: value(collect()))
    saved = json.loads(stage.joinpath('runtime-sources.json').read_text())[NAME]
    reconciled_manifest = tomllib.loads(project.joinpath('pyproject.toml').read_text())
    rebound = reconciled_manifest['tool']['amplifier-unified']['generated-source-overrides'][NAME]
    assert rebound in reconciled_manifest['tool']['uv']['override-dependencies']
    assert rebound in reconciled_manifest['dependency-groups']['unified-managed-runtime']
    assert rebound.endswith('@' + (new if ref == 'branch' else old))
    if ref == 'branch':
        assert saved['ref'] == 'main' and saved['revision'] == new
        installed = next(row for row in qualification.installed_graph(project) if row['name'] == NAME)
        assert installed['directUrl']['vcs_info']['commit_id'] == new
        assert collect()['packages'][NAME]['revision'] == new
        from test_runtime_source_policy_resolver import Diagnostics
        final = await qualification.freeze(SimpleNamespace(home=home, diagnostics=Diagnostics()), generation, project)
        qualification.verify_recorded(final, stage)
        (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
        current = next(row for row in runtime.inventory(home) if row.get('package') == NAME)
        assert current['ref'] == 'main' and current['current'] == new and current['eligible']
        # A later check has moving policy, so it does not offer this migration again.
        assert NAME not in migration.legacy_candidates([current])
    else:
        assert saved['ref'] == old
        assert collect()['packages'][NAME]['revision'] == old


async def value(item):
    return item

@pytest.mark.parametrize('change', ['wrong-sha', 'changed-url', 'conditional', 'missing', 'recorded', 'separate-config'])
async def test_refresh_refuses_changed_or_explicit_manifest_choices(tmp_path, change):
    home, generation = tmp_path / 'app', 'd' * 32
    stage = runtime.receipt_directory(home, generation); stage.mkdir(parents=True)
    content = runtime.augmented_manifest(b'[project]\nname="worker"\nversion="1"\n',
        [{'kind':'runtime dependency','package':NAME,'url':URL,'ref':OLD}])
    project = runtime.prepare_project(home, generation, content=content)
    path = project / 'pyproject.toml'
    text = path.read_text()
    if change == 'wrong-sha': path.write_text(text.replace(OLD, NEW))
    if change == 'changed-url': path.write_text(text.replace(URL, URL + '-fork'))
    if change == 'conditional': path.write_text(text.replace('@' + OLD, '@' + OLD + ' ; python_version > \\"1\\"'))
    if change == 'missing': path.write_bytes(b'[project]\nname="worker"\nversion="1"\n')
    if change == 'recorded': (stage / 'runtime.lock').write_text('preserved')
    if change == 'separate-config': (project / 'uv.toml').write_text('')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        await dependencies.refresh(SimpleNamespace(home=home), stage, project, {NAME:{
            'sourceKind':'dependency','url':URL,'ref':'main','subdirectory':'',
            'previousRevision':OLD,'revision':NEW}})
    assert path.read_bytes() == before


from test_runtime_policy_reconciliation import selected as selected_source


@pytest.mark.parametrize('local_override', [False, True])
async def test_collector_uses_actual_selected_source_manifest_only(selected_source, monkeypatch, local_override):
    from test_runtime_policy_reconciliation import git
    s = selected_source
    child = 'nested-component'
    child_url = URL + '-nested'
    path = s.root / 'pyproject.toml'
    path.write_bytes(manifest([child + ' @ git+' + child_url + '@main']).replace(
        b'name" = "selected-root"', b'name" = "fixture-component"'))
    git(s.root, 'add', 'pyproject.toml')
    git(s.root, 'commit', '-m', 'selected dependency')
    metadata = json.loads(s.marker.read_text())
    metadata['commit'] = git(s.root, 'rev-parse', 'HEAD')
    s.marker.write_text(json.dumps(metadata))
    monkeypatch.setattr(importlib.metadata, 'distributions', lambda: [dist(child, url=child_url)])
    if local_override:
        local = s.stage / 'custom-module'
        local.mkdir()
        s.collector.local_sources.add(local)
        s.batch.names[child] = local
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][child]['status'] == ('local' if local_override else 'selected')
    if not local_override:
        assert report['packages'][child]['sourceKind'] == 'dependency'

@pytest.mark.parametrize('requirement,allowed', [('bridge==1.0', True), ('bridge==2.0', False),
                                               ('bridge>=2', False), ('bridge[unused]==1.0', True)])
def test_fixed_version_edges_must_match_the_current_selected_install(requirement, allowed):
    result = dependencies.selected({'selected-root': manifest([requirement])}, distributions=[
        dist('bridge', [NAME + ' @ git+' + URL + '@main'], url=None), dist()])
    assert (result.get(NAME, {}).get('status') == 'selected') is allowed

def test_prior_direct_only_receipt_is_reconsidered_once(selected_source, monkeypatch):
    from test_runtime_policy_reconciliation import row
    s = selected_source
    monkeypatch.setattr('amplifier_web.host.config.read_config', lambda *a, **kw: s.config)
    receipt = s.home / 'updates/baseline-runtime'
    receipt.mkdir()
    old = {'version': 1, 'configuration': migration.configuration(s.config),
           'remaining': migration.legacy_candidates([row()])}
    target = receipt / migration.RECEIPT
    target.write_text(json.dumps(old))
    assert migration.pending(s.home, None, [row()]) is not None
    old['selectionVersion'] = migration.SELECTION_VERSION
    target.write_text(json.dumps(old))
    assert migration.pending(s.home, None, [row()]) is None


@pytest.mark.parametrize('checked_ref',['main',OLD])
def test_matching_transitive_pin_retains_exact_identity_without_recovery(checked_ref):
    from amplifier_web.runtime_source_alignment import verify_checked_selection
    content=manifest([NAME+' @ git+'+URL+'@'+OLD])
    packages=dependencies.selected({'selected-root':content},distributions=[dist()])
    selection={'batchPaths':{'selected-root':'/selected/root'},'packages':packages}
    checked={NAME:{'url':URL,'ref':checked_ref,'revision':OLD,'subdirectory':''}}
    verify_checked_selection(checked,selection,exact=True)
    assert packages[NAME]['status']=='explicit-pin'
    candidate={'kind':'runtime dependency','package':NAME,'url':URL,'ref':OLD,'current':OLD,
               'policyOrigin':{'kind':'unresolved-reference','originalRef':'main'}}
    assert not migration.selected_policies(migration.legacy_candidates([candidate]),
                                           {'packages':packages})[0]
    checked[NAME]['revision']=NEW
    with pytest.raises(runtime.ProtectedRuntimeSource) as caught:
        verify_checked_selection(checked,selection)
    assert caught.value.diagnostic_facts['package']==NAME


@pytest.mark.parametrize('case',['different-install','fork','subdirectory','conditional',
                                'duplicate','conflict','tag','missing','unselected'])
def test_transitive_pin_retention_requires_complete_exact_evidence(case):
    from amplifier_web.runtime_source_alignment import verify_checked_selection
    content=manifest([NAME+' @ git+'+URL+'@'+OLD])
    records=[dist()]
    if case=='different-install':records=[dist(revision=NEW)]
    if case=='fork':records=[dist(url=URL+'-fork')]
    if case=='subdirectory':records=[dist(subdirectory='other')]
    if case=='conditional':content=manifest([NAME+' @ git+'+URL+'@'+OLD+' ; python_version > "1"'])
    if case=='duplicate':records.append(dist())
    if case=='conflict':content=manifest([NAME+' @ git+'+URL+'@'+OLD,NAME+' @ git+'+URL+'@main'])
    if case=='tag':content=manifest([NAME+' @ git+'+URL+'@v1.0.0'])
    if case=='missing':records=[]
    if case=='unselected':content=manifest([])
    packages=dependencies.selected({'selected-root':content},distributions=records)
    assert packages.get(NAME,{}).get('status')!='explicit-pin'
    if case!='unselected':
        with pytest.raises(runtime.ProtectedRuntimeSource):
            verify_checked_selection({NAME:{'url':URL,'ref':'main','revision':OLD}},
                                     {'batchPaths':{},'packages':packages},exact=True)
