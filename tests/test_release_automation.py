"""Release identity and package checks, with only isolated local Git repositories."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

import pytest

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
        for path in (root/'pyproject.toml',root/'amplifier_web/__init__.py',root/'amplifier_web/release-notes.json'):
            source.add(path,'amplifier_unified-1.2.3/'+str(path.relative_to(root)))
    return dist


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
    with pytest.raises(ValueError,match='validated source/assets'):release.verify_dist(repository,dist,'1.2.3')
    (repository/'amplifier_web/static/index.html').write_text('<script src="/assets/missing.js"></script>')
    distributions(repository)
    with pytest.raises(ValueError,match='missing asset'):release.verify_dist(repository,dist,'1.2.3')


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
        calls.append(args)
        return revision if args[:2]==('git','rev-parse') else 'b'*40+'\trefs/tags/v1.2.3'
    monkeypatch.setattr(release,'run',run)
    with pytest.raises(ValueError,match='refusing to move'):release.publish(repository,'example/app','v1.2.3',revision,dist)
    assert len(calls)==2


def test_new_release_publishes_only_after_draft_assets_are_uploaded(repository,monkeypatch):
    dist=distributions(repository);revision=git(repository,'rev-parse','HEAD');calls=[]
    def run(*args,**kwargs):
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
    def run(*args,**kwargs):calls.append(args);return revision
    monkeypatch.setattr(release,'run',run)
    with pytest.raises(ValueError,match='validated source/assets'):
        release.publish(repository,'example/app','v1.2.3',revision,dist)
    assert calls==[('git','rev-parse','HEAD')]


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
