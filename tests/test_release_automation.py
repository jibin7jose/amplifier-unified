"""Release identity and package checks, with only isolated local Git repositories."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import pytest
import yaml

spec=importlib.util.spec_from_file_location('application_release',Path(__file__).parents[1]/'scripts/release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


def git(root,*args):
    return subprocess.check_output(['git',*args],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def repository(tmp_path):
    (tmp_path/'amplifier_web/static/assets').mkdir(parents=True)
    (tmp_path/'pyproject.toml').write_text('[project]\nname="amplifier-unified"\nversion="1.2.3"\n')
    (tmp_path/'amplifier_web/__init__.py').write_text('__version__ = "1.2.3"\n')
    (tmp_path/'amplifier_web/static/index.html').write_text('<script src="/assets/app.js"></script>')
    (tmp_path/'amplifier_web/static/assets/app.js').write_text('console.log("packaged")')
    (tmp_path/'amplifier_web/release_notes.py').write_text((Path(__file__).parents[1]/'amplifier_web/release_notes.py').read_text())
    (tmp_path/'amplifier_web/release-notes.json').write_text(json.dumps({'schemaVersion':1,'releases':[{'version':'1.2.3','title':'Fixture release','changes':['A useful change.'],'notices':[{'id':'migration','title':'Review settings','detail':'Settings moved.','action':'Review your defaults.'}]}]}))
    git(tmp_path,'init','-b','main');git(tmp_path,'config','user.email','release-test@example.invalid');git(tmp_path,'config','user.name','Fixture')
    git(tmp_path,'add','.');git(tmp_path,'commit','-m','release')
    return tmp_path


def distributions(root):
    dist=root/'dist';dist.mkdir(exist_ok=True)
    with zipfile.ZipFile(dist/'amplifier_unified-1.2.3-py3-none-any.whl','w') as wheel:
        wheel.writestr('amplifier_unified-1.2.3.dist-info/METADATA','Name: amplifier-unified\nVersion: 1.2.3\n')
        for path in (root/'amplifier_web').rglob('*'):
            if path.is_file():wheel.write(path,path.relative_to(root))
    with tarfile.open(dist/'amplifier_unified-1.2.3.tar.gz','w:gz') as source:
        for path in [root/'pyproject.toml', *(path for path in (root/'amplifier_web').rglob('*') if path.is_file())]:
            source.add(path,'amplifier_unified-1.2.3/'+str(path.relative_to(root)))
    return dist


@pytest.fixture
def preparation(repository):
    (repository/'frontend').mkdir()
    (repository/'frontend/package.json').write_text('{}')  # Explicit historical bundled fixture.
    (repository/'uv.lock').write_text('''version = 1
revision = 3
[[package]]
name = "amplifier-unified"
version = "1.2.3" # root only
source = { editable = "." }
[[package]]
name = "other-dependency"
version = "1.2.3"
source = { registry = "https://pypi.org/simple" }
''')
    notes = repository/'authored-notes.json'
    notes.write_text(json.dumps({'version':'1.2.4','title':'Clearer release preparation',
                                'changes':['Prepare versions and authored notes together.']}))
    return repository, notes


def test_prepare_preserves_dirty_work_and_dependency_versions_without_git(preparation):
    root, notes = preparation
    project = root/'pyproject.toml'
    project.write_text(project.read_text() + 'description = "Uncommitted source change"\n')
    module = root/'amplifier_web/__init__.py'
    module.write_text(module.read_text() + 'CUSTOM = "Uncommitted source change"\n')
    history = json.loads((root/'amplifier_web/release-notes.json').read_text())
    history['releases'][0]['notices'][0]['previousTitles'] = ['Older editorial title']
    (root/'amplifier_web/release-notes.json').write_text(json.dumps(history))
    before = {name:(root/name).read_text() for name in ('pyproject.toml','amplifier_web/__init__.py','uv.lock')}
    shutil.rmtree(root/'.git')  # Queued source snapshots do not need Git metadata.
    result = release.prepare(root, '1.2.4', notes)
    assert result['buildStatus'] == 'not-run'
    assert len(result['changedFiles']) == 4
    for name in ('pyproject.toml','amplifier_web/__init__.py'):
        assert (root/name).read_text() == before[name].replace('1.2.3','1.2.4')
    assert (root/'uv.lock').read_text() == before['uv.lock'].replace('version = "1.2.3" # root only', 'version = "1.2.4" # root only')
    assert tomllib.loads((root/'uv.lock').read_text())['package'][1]['version'] == '1.2.3'
    updated = json.loads((root/'amplifier_web/release-notes.json').read_text())
    assert updated['releases'][1:] == history['releases']
    assert updated['releases'][0] == json.loads(notes.read_text())
    saved = {name:((root/name).read_bytes(), (root/name).stat().st_mtime_ns) for name in result['changedFiles']}
    assert release.prepare(root, '1.2.4', notes)['changedFiles'] == []
    assert saved == {name:((root/name).read_bytes(), (root/name).stat().st_mtime_ns) for name in saved}


@pytest.mark.parametrize('invalid', ['notes-version','empty-changes','existing-notes','future-history',
                                    'downgrade','stale-lock','duplicate-root','malformed-lock'])
def test_prepare_rejects_invalid_inputs_before_any_write(preparation, invalid):
    root, notes = preparation
    version = '1.2.4'
    entry = json.loads(notes.read_text())
    if invalid == 'notes-version':
        entry['version'] = '1.2.5'
    elif invalid == 'empty-changes':
        entry['changes'] = []
    elif invalid in {'existing-notes','future-history'}:
        path = root/'amplifier_web/release-notes.json'
        history = json.loads(path.read_text())
        history['releases'].insert(0, {**entry, 'version':'1.2.4' if invalid == 'existing-notes' else '1.2.5',
                                      'title':'Previously authored different notes'})
        path.write_text(json.dumps(history))
    elif invalid == 'downgrade':
        version = entry['version'] = '1.2.2'
    else:
        path = root/'uv.lock'
        content = path.read_text()
        if invalid == 'stale-lock':
            content = content.replace('"1.2.3" # root only', '"1.2.2" # root only')
        elif invalid == 'duplicate-root':
            content += '\n[[package]]\nname="amplifier-unified"\nversion="1.2.3"\nsource={virtual="."}\n'
        else:
            content = 'invalid preamble\n' + content
        path.write_text(content)
    notes.write_text(json.dumps(entry))
    names = ('pyproject.toml','amplifier_web/__init__.py','uv.lock','amplifier_web/release-notes.json')
    before = {name:(root/name).read_bytes() for name in names}
    with pytest.raises(ValueError):
        release.prepare(root, version, notes)
    assert before == {name:(root/name).read_bytes() for name in names}


@pytest.mark.parametrize('outcome', ['passed','failed','stale-version'])
def test_prepare_optional_build_runs_only_the_existing_build_once(preparation, monkeypatch, outcome):
    root, notes = preparation
    calls = []
    def build(command, cwd):
        calls.append((command, cwd))
        (root/'amplifier_web/static/build.json').write_text(json.dumps({
            'version':'1.2.3' if outcome == 'stale-version' else '1.2.4','id':'fixture-build'}))
        return subprocess.CompletedProcess(command, 9 if outcome == 'failed' else 0)
    monkeypatch.setattr(release.subprocess, 'run', build)
    if outcome == 'passed':
        result = release.prepare(root, '1.2.4', notes, build=True)
        assert result['buildStatus'] == 'passed'
        assert result['build']['version'] == '1.2.4'
        assert result['nextSteps'][0].startswith('Run uv sync --locked --dry-run')
        assert not any('Run buildCommand' in step for step in result['nextSteps'])
    else:
        with pytest.raises(ValueError, match='prepared metadata retained' if outcome == 'failed' else 'does not match'):
            release.prepare(root, '1.2.4', notes, build=True)
        assert release.prepare(root, '1.2.4', notes)['changedFiles'] == []
    assert calls == [(['npm','run','build','--prefix','frontend'], root)]
    assert release.version_at(root) == '1.2.4'


@pytest.mark.parametrize('valid_component', [True, False])
def test_prepare_verifies_selected_client_instead_of_matching_host_version(
        preparation, monkeypatch, valid_component):
    root, notes = preparation
    (root / 'components').mkdir()
    (root / 'components/web-client.source.json').write_text('{"fixture":true}')
    (root / 'tests/web').mkdir(parents=True)
    (root / 'tests/web/package.json').write_text('{}')
    (root / 'scripts').mkdir()
    (root / 'scripts/web_client_build.py').write_text('# Selected client builder fixture')
    calls = []

    def build(command, cwd):
        assert command == [sys.executable, 'scripts/web_client_build.py', 'build']
        assert cwd == root
        (root / 'amplifier_web/static/build.json').write_text(
            '{"version":"0.2.0","id":"independent-client"}')
        return subprocess.CompletedProcess(command, 0)

    def verify(selected):
        calls.append(selected)
        if not valid_component:
            raise ValueError('Installed client assets or provenance differ')

    import types
    monkeypatch.setitem(sys.modules, 'web_client_build', types.SimpleNamespace(verify=verify))
    monkeypatch.setattr(release.subprocess, 'run', build)
    if valid_component:
        result = release.prepare(root, '1.2.4', notes, build=True)
        assert result['version'] == '1.2.4'
        assert result['build']['version'] == '0.2.0'
    else:
        with pytest.raises(ValueError, match='assets or provenance differ'):
            release.prepare(root, '1.2.4', notes, build=True)
    assert calls == [root]


def test_prepare_cli_produces_metadata_without_installing_or_building(preparation):
    root, notes = preparation
    command = [sys.executable, str(Path(__file__).parents[1]/'scripts/release.py'), 'prepare']
    missing = subprocess.run(command, cwd=root, capture_output=True, text=True)
    assert missing.returncode == 2
    assert 'prepare requires --version and --notes-file' in missing.stderr
    result = subprocess.run([*command, '--version','1.2.4','--notes-file',str(notes)],
                            cwd=root, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload['version'] == '1.2.4'
    assert payload['buildStatus'] == 'not-run'
    assert payload['buildCommand'] == ['npm','run','build','--prefix','frontend']
    assert payload['nextSteps'][0].startswith('Run buildCommand once')
    assert payload['nextSteps'][1].startswith('Run uv sync --locked --dry-run')
    assert 'change PR where practical; push early' in payload['nextSteps'][2]
    assert 'While required CI runs' in payload['nextSteps'][3]
    assert 'Reuse independent feature review' in payload['nextSteps'][3]
    assert 'exact reviewed head through repository protections' in payload['nextSteps'][3]
    assert 'Live adoption is separate' in payload['nextSteps'][-1]
    assert payload['guidance'].endswith('#preparing-a-version-change')


def test_prepare_help_describes_the_routine_single_build_path():
    script = Path(__file__).parents[1]/'scripts/release.py'
    help_text = ' '.join(subprocess.check_output([sys.executable, str(script), '--help'], text=True).split())
    assert 'prepare --version X.Y.Z --notes-file /path/to/entry.json --build' in help_text
    assert 'uv sync --locked --dry-run --group dev --group artifacts' in help_text
    assert 'Coordinate one owner, version and publisher' in help_text
    assert 'reuse matching dependencies' in help_text
    assert 'Preparation never commits or publishes' in help_text


def test_prepare_cli_reports_invalid_notes_without_traceback_or_source_changes(preparation):
    root, notes = preparation
    before = {path: path.read_bytes() for path in [root/'pyproject.toml', root/'uv.lock',
              root/'amplifier_web/__init__.py', root/'amplifier_web/release-notes.json']}
    notes.write_text('{"version":')
    result = subprocess.run([sys.executable, str(Path(__file__).parents[1]/'scripts/release.py'),
                             'prepare', '--version', '1.2.4', '--notes-file', str(notes)],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 1 and not result.stdout
    assert result.stderr.startswith('Release preparation failed: ')
    assert 'Traceback' not in result.stderr
    assert before == {path: path.read_bytes() for path in before}


def test_automatic_publication_only_watches_the_dedicated_version_owner():
    workflow = yaml.load((Path(__file__).parents[1]/'.github/workflows/release.yml').read_text(),
                         Loader=yaml.BaseLoader)
    assert workflow['on']['push'] == {'branches':['main'], 'paths':['amplifier_web/__init__.py']}
    assert 'release_revision' in workflow['on']['workflow_dispatch']['inputs']
    resolution = next(step for step in workflow['jobs']['prepare']['steps'] if step.get('id') == 'release')
    assert resolution['env']['SELECTED_RELEASE_REVISION'] == '${{ inputs.release_revision }}'
    assert 'plan --revision "$SELECTED_RELEASE_REVISION"' in resolution['run']


@pytest.mark.parametrize('annotated',[False,True])
def test_release_plan_resumes_only_the_selected_commit(repository,annotated):
    original=git(repository,'rev-parse','HEAD')
    assert release.plan(repository)=={'version':'1.2.3','tag':'v1.2.3','revision':original,'existing_tag':False}
    git(repository,'tag',*(['-a','-m','release'] if annotated else []),'v1.2.3')
    assert release.plan(repository)['revision']==original
    (repository/'README.md').write_text('A fix that still needs a version bump')
    git(repository,'add','.');git(repository,'commit','-m','later')
    with pytest.raises(ValueError,match='selected commit'):
        release.plan(repository)
    assert git(repository,'rev-parse','v1.2.3^{commit}')==original
    git(repository,'checkout','--detach',original)
    assert release.plan(repository)['revision']==original
    (repository/'amplifier_web/__init__.py').write_text('__version__ = "1.2.4"\n')
    with pytest.raises(ValueError,match='matching stable versions'):release.plan(repository)


def test_release_refuses_a_tag_on_a_different_package_version(repository):
    git(repository,'tag','v1.2.4')
    (repository/'pyproject.toml').write_text('[project]\nversion="1.2.4"\n')
    (repository/'amplifier_web/__init__.py').write_text('__version__="1.2.4"\n')
    with pytest.raises(ValueError,match='immutable release tag'):release.plan(repository)


@pytest.mark.parametrize('annotated', [False, True])
@pytest.mark.parametrize('position', ['absent', 'same', 'older'])
def test_shallow_publication_fetches_only_exact_tag_and_preserves_identity(repository, annotated, position):
    original = git(repository, 'rev-parse', 'HEAD')
    git(repository, 'tag', 'unrelated-old-tag')
    (repository/'README.md').write_text('A later commit')
    git(repository, 'add', '.');git(repository, 'commit', '-m', 'later')
    current = git(repository, 'rev-parse', 'HEAD')
    git(repository, 'tag', 'unrelated-current-tag')
    if position != 'absent':
        git(repository, 'tag', *(['-a', '-m', 'release'] if annotated else []),
            'v1.2.3', original if position == 'older' else current)
    clone = repository.parent/(repository.name+'-shallow')
    subprocess.check_call(['git', 'clone', '-q', '--depth=1', '--no-tags', repository.as_uri(), str(clone)])
    assert git(clone, 'rev-parse', '--is-shallow-repository') == 'true'
    assert git(clone, 'tag', '--list') == ''
    if position == 'older':
        with pytest.raises(ValueError, match='selected commit'):
            release.plan(clone, fetch_tag=True)
    else:
        assert release.plan(clone, fetch_tag=True) == {
            'version':'1.2.3', 'tag':'v1.2.3', 'revision':current, 'existing_tag':position == 'same'}
    assert git(clone, 'tag', '--list').splitlines() == ([] if position == 'absent' else ['v1.2.3'])
    assert git(clone, 'rev-parse', 'HEAD') == current


def test_remote_tag_lookup_failure_is_not_treated_as_a_new_release(repository):
    git(repository, 'remote', 'add', 'origin', (repository/'unavailable.git').as_uri())
    with pytest.raises(ValueError, match='Release command failed: git ls-remote'):
        release.plan(repository, fetch_tag=True)


def test_manual_plan_can_qualify_merged_maintenance_commit_after_main_advances(repository):
    original = git(repository, 'rev-parse', 'HEAD')
    (repository/'pyproject.toml').write_text('[project]\nversion="2.0.0"\n')
    (repository/'amplifier_web/__init__.py').write_text('__version__="2.0.0"\n')
    git(repository, 'add', '.');git(repository, 'commit', '-m', 'next release')
    head = git(repository, 'rev-parse', 'HEAD')
    assert release.plan(repository, original) == {
        'version': '1.2.3', 'tag': 'v1.2.3', 'revision': original, 'existing_tag': False}
    assert git(repository, 'rev-parse', 'HEAD') == head
    assert release.plan(repository)['version'] == '2.0.0'
    git(repository, 'tag', 'v1.2.3', original)
    assert release.plan(repository, original)['existing_tag'] is True
    git(repository, 'tag', '-f', 'v1.2.3', head)
    with pytest.raises(ValueError, match='immutable release tag'):
        release.plan(repository, original)


@pytest.mark.parametrize('revision', ['main', 'abc123', '--all', 'f'*40])
def test_manual_plan_rejects_moving_invalid_and_unknown_references(repository, revision):
    with pytest.raises(ValueError):
        release.plan(repository, revision)


def test_manual_plan_rejects_unmerged_commit(repository):
    original = git(repository, 'rev-parse', 'HEAD')
    git(repository, 'checkout', '-b', 'unmerged')
    (repository/'unmerged.txt').write_text('not accepted')
    git(repository, 'add', '.');git(repository, 'commit', '-m', 'unmerged')
    other = git(repository, 'rev-parse', 'HEAD')
    git(repository, 'checkout', 'main')
    with pytest.raises(ValueError, match='ancestor'):
        release.plan(repository, other)
    assert git(repository, 'rev-parse', 'HEAD') == original


def test_manual_plan_rejects_tag_and_tree_object_shas(repository):
    git(repository, 'tag', '-a', '-m', 'annotated', 'candidate')
    for ref in ('candidate', 'HEAD^{tree}'):
        with pytest.raises(ValueError, match='ancestor'):
            release.plan(repository, git(repository, 'rev-parse', ref))


def test_release_distribution_verification_checks_source_assets_and_references(repository):
    dist=distributions(repository)
    release.verify_dist(repository,dist,'1.2.3')
    assert len((dist/'SHA256SUMS').read_text().splitlines())==2
    (repository/'amplifier_web/static/assets/app.js').write_text('different')
    with pytest.raises(ValueError,match='validated frontend bytes'):release.verify_dist(repository,dist,'1.2.3')
    (repository/'amplifier_web/static/index.html').write_text('<script src="/assets/missing.js"></script>')
    distributions(repository)
    with pytest.raises(ValueError,match='missing asset'):release.verify_dist(repository,dist,'1.2.3')


@pytest.mark.parametrize('kind', ['wheel', 'source'])
@pytest.mark.parametrize('change', ['missing', 'changed', 'extra'])
@pytest.mark.parametrize('asset', ['build.json', 'vendor/pdfjs/font.bin', 'licenses/vendor.txt'])
def test_release_requires_complete_frontend_bytes_in_both_formats(repository, kind, change, asset):
    """Files not directly referenced by index.html still belong to the release."""
    relative = 'amplifier_web/static/' + asset
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'validated content')
    dist = distributions(repository)
    if kind == 'wheel':
        target = next(dist.glob('*.whl'))
        with zipfile.ZipFile(target) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        name = relative
    else:
        target = next(dist.glob('*.tar.gz'))
        with tarfile.open(target) as archive:
            entries = {entry.name: archive.extractfile(entry).read() for entry in archive.getmembers()}
        name = 'amplifier_unified-1.2.3/' + relative
    if change == 'missing':
        del entries[name]
    elif change == 'changed':
        entries[name] = b'wrong content'
    else:
        entries[name + '.obsolete'] = b'old output'
    if kind == 'wheel':
        with zipfile.ZipFile(target, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
    else:
        with tarfile.open(target, 'w:gz') as archive:
            for name, data in entries.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='frontend'):
        release.verify_dist(repository, dist, '1.2.3')
    assert not (dist / 'SHA256SUMS').exists()


@pytest.fixture
def source_candidate(repository):
    with (repository / 'pyproject.toml').open('a') as stream:
        stream.write('\n[tool.hatch.build]\nartifacts = ["/amplifier_web/release-source.json"]\n')
    (repository / '.gitignore').write_text('/dist/\n/amplifier_web/release-source.json\n__pycache__/\n*.pyc\n')
    git(repository, 'add', '.'); git(repository, 'commit', '-m', 'source record packaging')
    return repository, 'example/app', git(repository, 'rev-parse', 'HEAD'), '1.2.3'


def test_source_record_uses_exact_selected_source_without_publication_claim(source_candidate):
    root, repository, revision, version = source_candidate
    assert release.stage_source(*source_candidate)['staged'] is True
    path = root / 'amplifier_web/release-source.json'
    data = path.read_bytes()
    assert json.loads(data) == {'schema': 1, 'repository': repository, 'revision': revision, 'version': version}
    assert git(root, 'status', '--porcelain') == ''
    assert git(root, 'check-ignore', str(path))
    assert release.stage_source(*source_candidate)['staged'] is True
    assert path.read_bytes() == data
    dist = distributions(root)
    path.unlink()  # The separate publisher derives expectations; it need not retain staged metadata.
    release.verify_dist(root, dist, version, repository, revision)


@pytest.mark.parametrize('change', ['tracked', 'staged', 'untracked', 'removed-opt-in'])
def test_source_record_rejects_dirty_candidate_before_write(source_candidate, change):
    root, *_ = source_candidate
    if change == 'untracked':
        (root / 'new-input.py').write_text('changed')
    elif change == 'removed-opt-in':
        (root / 'pyproject.toml').write_text('[project]\nname="amplifier-unified"\nversion="1.2.3"\n')
    else:
        (root / 'amplifier_web/static/assets/app.js').write_text('changed')
        if change == 'staged':
            git(root, 'add', '.')
    with pytest.raises(ValueError):
        release.stage_source(*source_candidate)
    assert not (root / 'amplifier_web/release-source.json').exists()


@pytest.mark.parametrize('index,value', [(1, None), (1, 'https://github.com/example/app'), (2, None), (2, 'main'), (2, 'b' * 40), (3, '1.2.4')])
def test_source_record_rejects_wrong_selected_identity(source_candidate, index, value):
    args = list(source_candidate); args[index] = value
    with pytest.raises(ValueError):
        release.stage_source(*args)
    assert not (source_candidate[0] / 'amplifier_web/release-source.json').exists()


@pytest.mark.parametrize('operation', ['stage', 'verify'])
def test_source_record_old_ancestor_cannot_select_legacy_contract_for_new_head(source_candidate, operation):
    root, repository, _, version = source_candidate
    old_revision = git(root, 'rev-parse', 'HEAD^')
    with pytest.raises(ValueError, match='selected checkout'):
        if operation == 'stage':
            release.stage_source(root, repository, old_revision, version)
        else:
            release.verify_dist(root, distributions(root), version, repository, old_revision)
    assert not (root / 'amplifier_web/release-source.json').exists()


@pytest.mark.parametrize('kind', ['wheel', 'source'])
@pytest.mark.parametrize('change', ['missing', 'malformed', 'schema', 'repository', 'revision', 'version', 'extra-field', 'different-bytes', 'duplicate'])
def test_source_record_rejects_missing_or_changed_archive_facts(source_candidate, kind, change):
    root, repository, revision, version = source_candidate
    release.stage_source(*source_candidate)
    dist = distributions(root)
    relative = 'amplifier_web/release-source.json'
    target = next(dist.glob('*.whl' if kind == 'wheel' else '*.tar.gz'))
    if kind == 'wheel':
        with zipfile.ZipFile(target) as archive:
            entries = [(name, archive.read(name)) for name in archive.namelist()]
        name = relative
    else:
        with tarfile.open(target) as archive:
            entries = [(entry.name, archive.extractfile(entry).read()) for entry in archive.getmembers()]
        name = 'amplifier_unified-1.2.3/' + relative
    data = dict(entries)[name]
    if change == 'duplicate':
        entries.append((name, data))
    else:
        entries = [(entry, value) for entry, value in entries if entry != name]
        if change == 'malformed':
            data = b'{'
        elif change == 'different-bytes':
            data = json.dumps(json.loads(data), indent=2).encode()
        elif change != 'missing':
            value = json.loads(data); value[change] = 'changed'; data = json.dumps(value).encode()
        if change != 'missing': entries.append((name, data))
    if kind == 'wheel':
        with zipfile.ZipFile(target, 'w') as archive:
            for name, data in entries: archive.writestr(name, data)
    else:
        with tarfile.open(target, 'w:gz') as archive:
            for name, data in entries:
                entry = tarfile.TarInfo(name); entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='source record'):
        release.verify_dist(root, dist, version, repository, revision)
    assert not (dist / 'SHA256SUMS').exists()


def test_source_record_old_candidate_replay_remains_unstamped(repository):
    result = release.stage_source(repository, 'example/app', git(repository, 'rev-parse', 'HEAD'), '1.2.3')
    assert result['staged'] is False
    assert not (repository / 'amplifier_web/release-source.json').exists()
    release.verify_dist(repository, distributions(repository), '1.2.3')


def test_source_record_cli_explains_missing_identity(source_candidate):
    script = Path(__file__).parents[1] / 'scripts/release.py'
    result = subprocess.run([sys.executable, str(script), 'stage-source'], cwd=source_candidate[0], capture_output=True, text=True)
    assert result.returncode != 0 and 'repository' in result.stderr and 'Traceback' not in result.stderr
    help_text = subprocess.check_output([sys.executable, str(script), '--help'], text=True)
    assert '--repository OWNER/REPO' in help_text and '--revision FULL_COMMIT_SHA' in help_text


def test_unstamped_editable_metadata_needs_no_release_or_frontend_setup(tmp_path):
    pytest.importorskip('hatchling.build')
    source = tmp_path / 'source'; source.mkdir()
    root = Path(__file__).parents[1]
    shutil.copyfile(root / 'pyproject.toml', source / 'pyproject.toml')
    (source / 'README.md').write_text('Editable metadata fixture')
    (source / 'amplifier_web').mkdir()
    shutil.copyfile(root / 'amplifier_web/__init__.py', source / 'amplifier_web/__init__.py')
    metadata = tmp_path / 'metadata'; metadata.mkdir()
    code = '''import os, sys
from hatchling.build import prepare_metadata_for_build_editable
os.environ['PATH'] = ''
def deny(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
        raise RuntimeError('Editable metadata attempted external work: ' + event)
sys.addaudithook(deny)
print(prepare_metadata_for_build_editable(sys.argv[1]))
'''
    result = subprocess.check_output([sys.executable, '-I', '-c', code, str(metadata)], cwd=source, text=True)
    assert (metadata / result.strip() / 'METADATA').is_file()
    assert not (source / 'amplifier_web/release-source.json').exists()
    assert not (source / 'amplifier_web/static').exists()


def test_release_workflow_stages_source_only_after_candidate_verification():
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/release.yml').read_text())
    steps = workflow['jobs']['prepare']['steps']
    build = next(step for step in steps if step.get('name') == 'Build and verify distributions')
    commands = build['run']
    assert commands.index('verify-candidate') < commands.index('stage-source') < commands.index('uv build')
    assert '--candidate "$RELEASE_CANDIDATE"' in commands
    assert '--repository "$RELEASE_REPOSITORY" --revision "$RELEASE_REVISION"' in commands
    assert build['env']['RELEASE_REPOSITORY'] == '${{ github.repository }}'
    assert build['env']['RELEASE_REVISION'] == '${{ steps.release.outputs.revision }}'


def test_released_sdist_rebuilds_complete_ui_without_node_or_network(tmp_path, request):
    """Exercise the real build backend and policy, outside the source checkout."""
    pytest.importorskip('hatchling.build')
    root = Path(__file__).parents[1]
    record = root / 'amplifier_web/release-source.json'
    original = record.read_bytes() if record.exists() else None
    request.addfinalizer(lambda: record.write_bytes(original) if original is not None else record.unlink(missing_ok=True))
    revision = git(root, 'rev-parse', 'HEAD')
    version = release.version_at(root)
    release.stage_source(root, 'microsoft/amplifier-unified', revision, version)
    expected_record = record.read_bytes()
    dist = tmp_path / 'dist'
    dist.mkdir()
    empty_path = tmp_path / 'empty-path'
    empty_path.mkdir()
    builder = tmp_path / 'build.py'
    builder.write_text('''import os, shutil, socket, subprocess, sys
from hatchling.build import build_sdist, build_wheel
os.environ["PATH"] = sys.argv[3]
assert all(shutil.which(name) is None for name in ("node", "npm", "git"))
def deny(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.spawn"}:
        raise RuntimeError("Build attempted network or an external process: " + event)
sys.addaudithook(deny)
for probe in (lambda: socket.create_connection(("127.0.0.1", 9)),
              lambda: subprocess.run([sys.executable, "-V"])):
    try:
        probe()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Build isolation probe was not denied")
print((build_sdist if sys.argv[1] == "sdist" else build_wheel)(sys.argv[2]))
''')
    command = [sys.executable, '-I', str(builder)]
    direct = tmp_path / 'direct-wheel'
    direct.mkdir()
    subprocess.run([*command, 'wheel', str(direct), str(empty_path)], cwd=root,
                   check=True, capture_output=True, text=True, timeout=120)
    subprocess.run([*command, 'sdist', str(dist), str(empty_path)], cwd=root,
                   check=True, capture_output=True, text=True, timeout=120)
    extracted = tmp_path / 'extracted'
    with tarfile.open(next(dist.glob('*.tar.gz'))) as archive:
        archive.extractall(extracted, filter='data')
    source = next(extracted.iterdir())
    assert not (source / '.git').exists()
    assert (source / 'amplifier_web/release-source.json').read_bytes() == expected_record
    subprocess.run([*command, 'wheel', str(dist), str(empty_path)], cwd=source,
                   check=True, capture_output=True, text=True, timeout=120)
    with zipfile.ZipFile(next(direct.glob('*.whl'))) as initial, zipfile.ZipFile(next(dist.glob('*.whl'))) as rebuilt:
        assert initial.read('amplifier_web/release-source.json') == expected_record
        assert {name: initial.read(name) for name in initial.namelist()} == {name: rebuilt.read(name) for name in rebuilt.namelist()}
    release.verify_dist(root, dist, version, 'microsoft/amplifier-unified', revision)
    assert len((dist / 'SHA256SUMS').read_text().splitlines()) == 2


@pytest.mark.parametrize('kind', ['wheel', 'source'])
def test_release_rejects_ci_checkout_files_before_checksumming(repository, kind):
    dist = distributions(repository)
    name = '.ci/recipes/README.md'
    if kind == 'wheel':
        with zipfile.ZipFile(next(dist.glob('*.whl')), 'a') as archive:
            archive.writestr(name, 'CI dependency fixture')
    else:
        target = next(dist.glob('*.tar.gz'))
        with tarfile.open(target) as archive:
            members = [(entry, archive.extractfile(entry).read()) for entry in archive.getmembers()]
        with tarfile.open(target, 'w:gz') as archive:
            for entry, data in members:
                archive.addfile(entry, io.BytesIO(data))
            entry = tarfile.TarInfo('amplifier_unified-1.2.3/' + name)
            data = b'CI dependency fixture'
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='CI checkout'):
        release.verify_dist(repository, dist, '1.2.3')
    assert not (dist / 'SHA256SUMS').exists()


def test_real_sdist_build_excludes_untracked_ci_checkouts(repository):
    """Reproduce release checkout contamination with the actual build policy."""
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('uv is required for the real source-distribution build')
    project = Path(__file__).parents[1]
    for name in ('pyproject.toml', '.gitignore'):
        shutil.copyfile(project / name, repository / name)
    (repository / 'README.md').write_text('Package build fixture')
    for dependency in ('loop-live', 'recipes'):
        checkout = repository / '.ci' / dependency
        checkout.mkdir(parents=True)
        (checkout / 'README.md').write_text('Untracked CI dependency fixture')
    dist = repository / 'built'
    subprocess.run([uv, 'build', '--sdist', '--out-dir', str(dist)], cwd=repository,
                   check=True, capture_output=True, text=True, timeout=120)
    with tarfile.open(next(dist.glob('*.tar.gz'))) as archive:
        names = archive.getnames()
    assert any(name.endswith('/amplifier_web/__init__.py') for name in names)
    assert not any('/.ci/' in name for name in names), names


@pytest.mark.parametrize('published',[True,False])
def test_release_publish_is_rerunnable_without_moving_tags_or_replacing_published_assets(repository,monkeypatch,published):
    dist=distributions(repository);revision=git(repository,'rev-parse','HEAD');calls=[]
    def run(*args,**kwargs):
        if args[:2] == ('git', 'show'): return git(repository, 'show', args[2])
        calls.append(args)
        if args[:3]==('git','rev-parse','HEAD'):return revision
        if args[:2]==('git','ls-remote'):return revision+'\trefs/tags/v1.2.3'
        if args[:3]==('gh','release','list'):return json.dumps([{'tagName':'v1.2.3','isDraft':not published},{'tagName':'v2.0.0','isDraft':False}])
        return ''
    monkeypatch.setattr(release,'run',run)
    release.publish(repository,'example/app','v1.2.3',revision,dist)
    assert not any(call[:2]==('git','push') for call in calls)
    if published:assert not any(call[:3] in {('gh','release','upload'),('gh','release','edit')} for call in calls)
    else:assert calls[-1][-1]=='--latest=false'


def test_release_never_moves_a_remote_tag(repository,monkeypatch):
    dist=distributions(repository);revision=git(repository,'rev-parse','HEAD');calls=[]
    def run(*args,**kwargs):
        if args[:2] == ('git', 'show'): return git(repository, 'show', args[2])
        calls.append(args)
        return revision if args[:2]==('git','rev-parse') else 'b'*40+'\trefs/tags/v1.2.3'
    monkeypatch.setattr(release,'run',run)
    with pytest.raises(ValueError,match='refusing to move'):release.publish(repository,'example/app','v1.2.3',revision,dist)
    assert len(calls)==3


def test_new_release_publishes_only_after_draft_assets_are_uploaded(repository,monkeypatch):
    dist=distributions(repository);revision=git(repository,'rev-parse','HEAD');calls=[]
    def run(*args,**kwargs):
        if args[:2] == ('git', 'show'): return git(repository, 'show', args[2])
        calls.append(args)
        if args[:3]==('git','rev-parse','HEAD'):return revision
        if args[:3]==('gh','release','list'):return '[]'
        return ''
    monkeypatch.setattr(release,'run',run)
    release.publish(repository,'example/app','v1.2.3',revision,dist)
    mutations=[call for call in calls if call[:2]==('git','push') or call[:3] in {('gh','release','create'),('gh','release','upload'),('gh','release','edit')}]
    assert [call[1] if call[0]=='git' else call[2] for call in mutations]==['push','create','upload','edit']
    assert '--draft' in mutations[1] and '--verify-tag' in mutations[1]
    assert '--notes-file' in mutations[1] and '--generate-notes' not in mutations[1]
    assert 'What to do:' in (dist/'RELEASE_NOTES.md').read_text()
    assert '--draft=false' in mutations[-1] and '--latest=true' in mutations[-1]
    assert not any('--force' in call for call in calls)


@pytest.mark.parametrize('remote_tag', ['absent', 'matching', 'different'])
def test_shallow_publication_preserves_remote_tag_identity(repository, monkeypatch, remote_tag):
    earlier = git(repository, 'rev-parse', 'HEAD')
    (repository / 'README.md').write_text('The already-qualified release commit')
    git(repository, 'add', '.'); git(repository, 'commit', '-m', 'candidate')
    revision = git(repository, 'rev-parse', 'HEAD')
    if remote_tag != 'absent':
        git(repository, 'tag', 'v1.2.3', revision if remote_tag == 'matching' else earlier)
    remote = repository / 'remote.git'
    git(repository, 'clone', '--bare', str(repository), str(remote))
    shallow = repository / 'publisher'
    git(repository, 'clone', '--depth=1', '--no-tags', remote.as_uri(), str(shallow))
    git(shallow, 'checkout', '--detach', revision)
    assert git(shallow, 'rev-parse', '--is-shallow-repository') == 'true'
    assert git(shallow, 'rev-list', '--count', 'HEAD') == '1'
    assert git(shallow, 'tag', '--list') == ''
    calls = []
    original_run = release.run

    def run(*args, **kwargs):
        if args[0] == 'gh':
            calls.append(args)
            return '[]' if args[:3] == ('gh', 'release', 'list') else ''
        return original_run(*args, **kwargs)

    monkeypatch.setattr(release, 'run', run)
    dist = distributions(shallow)
    if remote_tag == 'different':
        with pytest.raises(ValueError, match='refusing to move an immutable tag'):
            release.publish(shallow, 'example/app', 'v1.2.3', revision, dist)
        assert git(remote, 'rev-parse', 'v1.2.3^{commit}') == earlier
        assert not calls
    else:
        release.publish(shallow, 'example/app', 'v1.2.3', revision, dist)
        assert git(remote, 'rev-parse', 'v1.2.3^{commit}') == revision
        assert calls[-1][-2:] == ('--draft=false', '--latest=true')
    assert git(shallow, 'rev-parse', 'HEAD') == revision


def test_invalid_package_never_creates_a_tag_or_release(repository,monkeypatch):
    dist=distributions(repository);revision=git(repository,'rev-parse','HEAD');calls=[]
    (repository/'amplifier_web/static/assets/app.js').write_text('unvalidated bytes')
    def run(*args,**kwargs):
        if args[:2] == ('git', 'show'): return git(repository, 'show', args[2])
        calls.append(args); return revision
    monkeypatch.setattr(release,'run',run)
    with pytest.raises(ValueError,match='validated frontend bytes'):
        release.publish(repository,'example/app','v1.2.3',revision,dist)
    assert calls==[('git','rev-parse','HEAD'), ('git','rev-parse','HEAD')]


def test_release_requires_notes_for_exact_version_before_any_mutation(repository,monkeypatch):
    dist=distributions(repository);revision=git(repository,'rev-parse','HEAD');calls=[]
    path=repository/'amplifier_web/release-notes.json'
    path.write_text(path.read_text().replace('1.2.3','1.2.2'))
    monkeypatch.setattr(release,'run',lambda *args,**kwargs:calls.append(args) or revision)
    with pytest.raises(ValueError,match='published version'):
        release.publish(repository,'example/app','v1.2.3',revision,dist)
    assert calls==[('git','rev-parse','HEAD')]


def test_release_detects_unpacked_or_stale_notes(repository):
    dist=distributions(repository)
    path=repository/'amplifier_web/release-notes.json'
    path.write_text(path.read_text().replace('A useful change.','An additional change.'))
    with pytest.raises(ValueError,match='validated source/assets'):
        release.verify_dist(repository,dist,'1.2.3')


def test_historical_release_rerun_before_notes_supported(tmp_path):
    assert release.release_notes(tmp_path,'0.11.2') is None
    with pytest.raises(ValueError,match='required'):release.release_notes(tmp_path,'0.11.3')
