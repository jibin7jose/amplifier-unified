"""Opt-in real UpdateManager/uv/Collector/probe path with public Git object fixtures.

Only remote transport points to local mirrors; no provider/model calls. Registry
wheels are repacked from the qualified test environment, not claimed public-wheel
bytes. Routing's remote main is the PR candidate until its public merge is proven.
"""
import asyncio
import base64
import csv
import hashlib
import importlib.metadata as metadata
import io
import json
import os
import platform
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tomllib
import zipfile

import pytest
import yaml

from amplifier_web import app_updates, builtin_behaviors, runtime_environment, updates
from amplifier_web.host.config import PRECONFIGURED_BUNDLES
from amplifier_web.runtime_qualification import installed_graph, verify_recorded
from amplifier_web.service import AppService

ROOT=Path(__file__).resolve().parents[1]
FURL='https://github.com/microsoft/amplifier-foundation'
RURL='https://github.com/microsoft/amplifier-bundle-routing-matrix'
OLD='a52ad1558419c828c86c0139b9b4bc351e3a618f'
PRE_API='c67e3ada021a5bbb93df9567feb23e2a72c93f55'
NEW='82798febe57899497eccd659c8657f8bd8960138'
ROLD='64138b9c544cbaddad6c24fd57f955da2c4e03ba'
RNEW='574e864ec953d1729867becc7f1eef9c609f45ad'
OUT=Path('/artifacts/effective-full')
pytestmark=[pytest.mark.asyncio, pytest.mark.skipif(
    os.environ.get('AMPLIFIER_EFFECTIVE_UPDATE_NATIVE') != '1',
    reason='Requires isolated prepared Python image and pinned Git bundles; see ordered-updates.md')]



def git(root,*args):
    return subprocess.check_output(['git',*map(str,args)],cwd=root,text=True,stderr=subprocess.STDOUT).strip()


def repack_registry_wheels(destination):
    """Preserve installed payload/metadata, remove only machine install records."""
    destination.mkdir()
    roots=[Path(sys.prefix)/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages',
           Path('/opt/unified-prepared/build-venv/lib')/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages']
    seen=set();proof=[]
    for root in roots:
        for dist in metadata.distributions(path=[str(root)]):
            name=dist.metadata['Name'].lower().replace('-','_')
            if name in seen or name in {'amplifier_unified','amplifier_foundation','amplifier_module_hooks_routing'}:continue
            seen.add(name)
            wheel=dist.read_text('WHEEL')
            if not wheel:continue
            tag=next(line[5:] for line in wheel.splitlines() if line.startswith('Tag: '))
            target=destination/f'{name}-{dist.version}-{tag}.whl'
            rows=[];bodyhash=hashlib.sha256()
            with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
                record=None
                for entry in dist.files or []:
                    path=Path(dist.locate_file(entry));relative=Path(str(entry))
                    if '..' in relative.parts or relative.suffix=='.pyc':continue
                    if relative.name=='RECORD':record=str(relative);continue
                    if relative.name in {'direct_url.json','INSTALLER','REQUESTED'}:continue
                    if not path.is_file():continue
                    data=path.read_bytes();archive.writestr(str(relative),data)
                    bodyhash.update(str(relative).encode()+b'\0'+data)
                    rows.append([str(relative),'sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='),str(len(data))])
                assert record,dist.metadata['Name']
                text=io.StringIO();csv.writer(text).writerows([*rows,[record,'','']]);archive.writestr(record,text.getvalue())
            proof.append({'package':name,'version':dist.version,'payloadSha256':bodyhash.hexdigest(),'wheelSha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    return proof


def fixture_provider(root, dependencies):
    root.mkdir()
    (root/'pyproject.toml').write_text('''[project]
name="amplifier-module-provider-update-fixture"
version="1.0.0"
dependencies=DEPENDENCIES
[build-system]
requires=["hatchling"]
build-backend="hatchling.build"
[tool.hatch.metadata]
allow-direct-references=true
[project.entry-points."amplifier.modules"]
provider-update-fixture="amplifier_module_provider_update_fixture:mount"
[tool.hatch.build.targets.wheel]
packages=["amplifier_module_provider_update_fixture"]
'''.replace('DEPENDENCIES',json.dumps(dependencies)))
    pkg=root/'amplifier_module_provider_update_fixture';pkg.mkdir()
    (pkg/'__init__.py').write_text('''from amplifier_core import ProviderInfo,ModelInfo
__amplifier_module_type__='provider'
class Provider:
 name='fixture'
 def get_info(self):return ProviderInfo(id='fixture',display_name='Offline update fixture',capabilities=[],defaults={'model':'fixture'})
 async def list_models(self):return [ModelInfo(id='fixture',display_name='Fixture',context_window=10000,max_output_tokens=100,capabilities=[])]
 async def complete(self,*a,**k):raise AssertionError('No model call allowed')
 def parse_tool_calls(self,response):return []
async def mount(coordinator,config=None):await coordinator.mount('providers',Provider(),name='fixture')
''')
    return root


def fixture_core(root, wheels):
    """Keep the prepared native Core bytes, with an explicit fixture Git policy."""
    root.mkdir()
    with zipfile.ZipFile(next(wheels.glob('amplifier_core-*.whl'))) as wheel:
        for name in wheel.namelist():
            if name.startswith('amplifier_core/'):
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(wheel.read(name))
    dist=metadata.distribution('amplifier-core')
    (root/'pyproject.toml').write_text(
        '[project]\nname="amplifier-core"\nversion='+json.dumps(dist.version)+
        '\ndependencies='+json.dumps(list(dist.requires or []))+
        '\n[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"'
        '\n[tool.hatch.build.targets.wheel]\npackages=["amplifier_core"]\n')
    git(root,'init','-b','main');git(root,'config','user.name','Fixture')
    git(root,'config','user.email','fixture@example.invalid')
    git(root,'add','.');git(root,'commit','-m','Qualified test image Core payload')
    return root


def fixture_module(root, wheels, distribution, package):
    """Use one real editable source for both preparation and mounted imports."""
    root.mkdir(parents=True)
    with zipfile.ZipFile(next(wheels.glob(distribution.replace('-','_')+'-*.whl'))) as wheel:
        for name in wheel.namelist():
            if name.startswith(package+'/'):
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(wheel.read(name))
    dist=metadata.distribution(distribution)
    (root/'pyproject.toml').write_text(
        '[project]\nname='+json.dumps(distribution)+'\nversion='+json.dumps(dist.version)+
        '\ndependencies='+json.dumps(list(dist.requires or []))+
        '\n[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"'+
        '\n[tool.hatch.metadata]\nallow-direct-references=true'+
        '\n[tool.hatch.build.targets.wheel]\npackages='+json.dumps([package])+'\n')
    return root


@pytest.mark.parametrize('case',['shared-rollback','missing-activation-api','old-pin','custom-pin','failed-qualification','dirty-source','retain-pin'])
async def test_actual_check_install_qualifies_selected_app_behavior(tmp_path,monkeypatch,case):
    routing_target=ROLD if case in {'old-pin','retain-pin'} else RNEW
    foundation_target=OLD if case=='retain-pin' else PRE_API if case=='missing-activation-api' else NEW
    positive=case=='shared-rollback'
    shared_case=case in {'shared-rollback','missing-activation-api'}
    global OUT
    artifacts=Path(os.environ.get('AMPLIFIER_EFFECTIVE_UPDATE_ROOT','/artifacts'))
    OUT=artifacts/'effective-full'/case
    # Keep executable/native inputs off noexec /tmp and outside the product
    # checkout: its pyproject must not become a fixture module's package root.
    work=artifacts/'effective-native';work.mkdir(parents=True,exist_ok=True)
    work=work/tmp_path.name;work.mkdir()
    OUT.mkdir(parents=True,exist_ok=True)
    uv=os.environ.get('AMPLIFIER_EFFECTIVE_UPDATE_UV','/opt/unified-prepared/bootstrap/bin/uv');assert Path(uv).is_file()
    home=work/'app';home.mkdir();shared=work/'shared';shared.mkdir()
    envhome=work/'process-home';envhome.mkdir()
    monkeypatch.setenv('HOME',str(envhome))
    monkeypatch.setenv('AMPLIFIER_HOME',str(shared));monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(home))
    monkeypatch.delenv('AMPLIFIER_UNIFIED_RELEASE',raising=False)
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE',raising=False)
    monkeypatch.delenv('UV_PROJECT_ENVIRONMENT',raising=False)
    monkeypatch.delenv('UV_OFFLINE',raising=False)
    monkeypatch.setenv('UV_NO_INDEX','1');monkeypatch.setenv('UV_NO_CONFIG','1')
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS','never');monkeypatch.setenv('UV_CACHE_DIR',str(work/'uv-cache'))
    # Keep GitHub branch resolution on the same local transport as Git fetch.
    # uv's API fast path would otherwise resolve today's public main first.
    monkeypatch.setenv('UV_NO_GITHUB_FAST_PATH','1')
    monkeypatch.setenv('PATH',str(Path(uv).parent)+':'+os.environ['PATH'])
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM','1');monkeypatch.setenv('PYTHONDONTWRITEBYTECODE','1')
    wheels=work/'wheels';proof=repack_registry_wheels(wheels);monkeypatch.setenv('UV_FIND_LINKS',str(wheels))
    (OUT/'registry-wheel-payloads.json').write_text(json.dumps(proof,indent=2))
    # The prepared bootstrap uv predates some UV_* aliases. Give every real
    # installer invocation the same explicit offline registry transport.
    installer_config=work/'uv.toml'
    installer_config.write_text('no-index=true\nfind-links=['+json.dumps(str(wheels))+']\n')
    binpath=work/'bin';binpath.mkdir()
    shim=binpath/'uv'
    shim.write_text('#!/bin/sh\nexec '+shlex.quote(uv)+' --config-file '+
                   shlex.quote(str(installer_config))+' "$@"\n')
    shim.chmod(0o755);uv=str(shim)
    monkeypatch.setenv('PATH',str(binpath)+':'+os.environ['PATH'])
    monkeypatch.delenv('UV_NO_CONFIG',raising=False)
    mirrors={};roots={};transport={}
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler
    for name,url,revision in [('foundation',FURL,OLD),('routing',RURL,ROLD)]:
        bundles=Path(os.environ.get('AMPLIFIER_EFFECTIVE_UPDATE_BUNDLES',str(ROOT/'evidence/foundation-transition')))
        bundle=bundles/f'{name}.bundle';mirror=work/(name+'-remote.git')
        git(work,'clone','--bare',bundle,mirror)
        if name=='foundation':git(mirror,'fetch',bundle,'refs/remotes/origin/main:refs/heads/main')
        git(mirror,'update-ref','refs/heads/main',revision)
        transport[url]=mirror.as_uri()
        mirrors[name]=mirror
        target=GitSourceHandler()._get_cache_path(parse_uri('git+'+url+'@main'),home/'foundation/cache')
        target.parent.mkdir(parents=True,exist_ok=True)
        git(work,'clone',mirror,target);git(target,'remote','set-url','origin',url);git(target,'checkout','--detach',revision)
        (target/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':url,'ref':'main','commit':revision}))
        roots[name]=target
    # Direct wheel files correctly count as a protected local Core override.
    # Use an explicit fixture Git declaration instead; no registry metadata or
    # protection is mocked. This is not public-Core provenance qualification.
    core=fixture_core(work/'fixture-core',wheels)
    core_url='https://fixture.invalid/amplifier-core'
    transport[core_url]=core.as_uri()
    transport_file=work/'git-transport.json';transport_file.write_text(json.dumps(transport))
    git_shim=binpath/'git'
    git_shim.write_text('#!'+sys.executable+'\n'+
        'import json,os,sys\nfrom pathlib import Path\n'+
        'args=sys.argv[1:]; flags=[]\n'+
        'if any(x in args for x in ("fetch","clone","ls-remote")):\n'+
        ' for url,mirror in json.loads(Path('+repr(str(transport_file))+').read_text()).items():\n'+
        '  flags.extend(["-c","url."+mirror+".insteadOf="+url])\n'+
        'os.execv("/usr/bin/git",["/usr/bin/git",*flags,*args])\n')
    git_shim.chmod(0o755)
    beforebindings={}
    if shared_case:
        from amplifier_foundation.sources.shared import SharedSourceStore
        store=SharedSourceStore(home/'source-store')
        await store.bind(home/'foundation/cache',RURL,'main',ROLD)
        beforebindings={str(p.relative_to(home)):p.read_bytes()
                        for p in (home/'foundation/cache/.source-bindings').glob('*.json')}
    skills=fixture_module(work/'skills',wheels,'amplifier-module-tool-skills','amplifier_module_tool_skills')
    context=fixture_module(work/'context-hook',wheels,'amplifier-module-hook-context-intelligence','amplifier_module_hook_context_intelligence')
    # A minimal actual worker graph retains installed required host mechanisms.
    manifest=work/'worker.toml'
    requirements=['packaging','amplifier-core','amplifier-foundation','amplifier-module-loop-live','amplifier-module-context-managed','amplifier-module-hooks-session-naming',
                  'amplifier-module-tool-skills @ '+skills.as_uri(),
                  'amplifier-module-hook-context-intelligence @ '+context.as_uri()]
    manifest.write_text('[project]\nname="update-fixture-worker"\nversion="1"\nrequires-python="==3.13.*"\ndependencies='+json.dumps(requirements)+
        '\n[tool.uv]\nenvironments='+json.dumps(["sys_platform == 'linux' and platform_machine == '"+platform.machine()+"'"])+
        '\noverride-dependencies=["amplifier-module-loop-streaming==1.0.0","amplifier-module-context-simple==1.0.0","amplifier-bundle-context-intelligence==0.2.0"]'+
        '\n[tool.uv.sources]\namplifier-foundation={git="'+FURL+'",branch="main"}'+
        '\namplifier-core={git="'+core_url+'",branch="main"}\n')
    monkeypatch.setattr(runtime_environment,'manifest_path',lambda:manifest)
    monkeypatch.setattr(app_updates,'installed_extras',lambda:[])
    project=runtime_environment.prepare_project(home)
    setup=subprocess.run([uv,'sync','--project',str(project),'--python',sys.executable],capture_output=True,text=True)
    (OUT/'baseline-sync.log').write_text(setup.stdout+setup.stderr)
    assert setup.returncode==0,setup.stdout+setup.stderr
    baseline=next(row for row in installed_graph(project) if row['name']=='amplifier-foundation')
    baseline_source=next(row['source']['git'] for row in tomllib.loads((project/'uv.lock').read_text())['package']
                         if row['name']=='amplifier-foundation')
    assert baseline_source==FURL+'?branch=main#'+OLD,baseline_source
    assert baseline['directUrl']['url']==FURL
    assert baseline['directUrl']['vcs_info']['commit_id']==OLD,baseline
    (OUT/'baseline-foundation.json').write_text(json.dumps({'lockSource':baseline_source,'installed':baseline},indent=2))
    dependencies=['amplifier-core @ git+'+core_url+'@main']
    if case=='custom-pin':
        dependencies.append('amplifier-foundation @ git+'+FURL+'@'+OLD)
    provider=fixture_provider(work/'provider',dependencies)
    if case=='failed-qualification':
        entry=provider/'amplifier_module_provider_update_fixture/__init__.py'
        entry.write_text(entry.read_text().replace(
            "async def mount(coordinator,config=None):await coordinator.mount('providers',Provider(),name='fixture')",
            "async def mount(coordinator,config=None):raise RuntimeError('fixture qualification refusal')"))
    provider_url='https://fixture.invalid/update-provider'
    git(provider,'init','-b','main');git(provider,'config','user.name','Fixture')
    git(provider,'config','user.email','fixture@example.invalid')
    git(provider,'add','.');git(provider,'commit','-m','Offline qualification provider')
    provider_revision=git(provider,'rev-parse','HEAD')
    transport[provider_url]=provider.as_uri()
    transport_file.write_text(json.dumps(transport))
    profile=work/'profile.yaml'
    profile.write_text(yaml.safe_dump({'bundle':{'name':'fixture'},'session':{'orchestrator':{'module':'loop-streaming'},'context':{'module':'context-simple'}},'providers':[{'module':'provider-update-fixture','source':'git+'+provider_url+'@main','config':{'default_model':'fixture'}}]}))
    # Installed host components remain real; explicit local module substitutions
    # isolate this fixture from unrelated default bundle repositories.
    site=next((project/'.venv/lib').glob('python*/site-packages'))
    value={'bundle':{'active':'fixture','added':{'fixture':str(profile)},'app':['git+'+RURL+'@main#subdirectory=behaviors/routing.yaml']},
           'web_bundles':{'excluded':list(builtin_behaviors.DEFAULT_BEHAVIORS),'entries':[{'name':n,'uri':uri,'role':'standalone','enabled':False} for n,uri in PRECONFIGURED_BUNDLES.items()]},
           'sources':{'modules':{'context-simple':str(site/'amplifier_module_context_managed'),'tool-skills':str(skills),
                                  'hook-context-intelligence':str(context)}}}
    (shared/'settings.yaml').write_text(yaml.safe_dump(value))
    beforelock=(project/'uv.lock').read_bytes();beforeheads={k:git(v,'rev-parse','HEAD') for k,v in roots.items()}
    if case=='dirty-source':
        manifest_file=roots['routing']/'modules/hooks-routing/pyproject.toml'
        manifest_file.write_text(manifest_file.read_text()+'\n# user edit retained\n')
    if case=='retain-pin':
        # Exercise an actual unrelated checked update while routing's authored
        # Foundation SHA and the checked/installed Foundation commit stay OLD.
        oldcore=git(core,'rev-parse','HEAD')
        (core/'revision.txt').write_text('new fixture component revision\n')
        git(core,'add','revision.txt');git(core,'commit','-m','Update independent fixture Core')
        newcore=git(core,'rev-parse','HEAD')
    git(mirrors['foundation'],'update-ref','refs/heads/main',foundation_target);git(mirrors['routing'],'update-ref','refs/heads/main',routing_target)
    class Runtime:
        workers={}
        async def close(self):pass
        def has_pending_operations(self):return False
    app=AppService(home,Runtime(),workspace=work);manager=updates.UpdateManager(app);app.update_manager=manager
    async def application():return {'id':'application','status':'current'}
    monkeypatch.setattr(app_updates,'check',application)
    observed=[];process=updates.process
    create=asyncio.create_subprocess_exec
    async def capture(*args,**kwargs):
        child=await create(*args,**kwargs)
        if any(str(arg).endswith('update_probe.py') for arg in args):
            communicate=child.communicate
            async def read(*a,**k):
                out,err=await communicate(*a,**k)
                index=len(list(OUT.glob('probe-*.log')))
                (OUT/f'probe-{index}.log').write_bytes((out or b'')+b'\nSTDERR\n'+(err or b''))
                return out,err
            child.communicate=read
        return child
    monkeypatch.setattr(asyncio,'create_subprocess_exec',capture)
    async def record(*args,**kwargs):
        observed.append({'command':[str(x) for x in args],'cwd':str(kwargs.get('cwd',''))})
        try:
            return await process(*args,**kwargs)
        except Exception as error:
            # Synthetic fixture diagnostics only, never private provider data.
            observed[-1]['error']=repr(error)
            for field in ('stderr','stdout','output'):
                text=getattr(error,field,None)
                if text:observed[-1][field]=str(text)[-12000:]
            raise
    monkeypatch.setattr(updates,'process',record)
    try:
        await manager.check(fresh=True)
        (OUT/'checked.json').write_text(json.dumps(manager.inventory,indent=2))
        assert app.state['updates']['phase']==('checked' if case=='dirty-source' else 'available'),app.state['updates']
        assert app.state['updates']['sequence']['stage']=='included'
        assert any(r.get('url')==RURL and r.get('kind')=='bundle / module'
                   and (r.get('status')=='local_changes' if case=='dirty-source' else r.get('latest')==routing_target)
                   for r in manager.inventory)
        assert any(r.get('package')=='amplifier-foundation' and r.get('latest')==foundation_target for r in manager.inventory)
        if case=='retain-pin':
            assert any(r.get('package')=='amplifier-core' and r.get('current')==oldcore
                       and r.get('latest')==newcore and r.get('status')=='update'
                       for r in manager.inventory)
        if shared_case:
            assert any(r.get('sharedSource') and r.get('url')==RURL
                       and r.get('updateTier')=='included' for r in manager.inventory)
        await manager.install()
        (OUT/'diagnostics.json').write_text(json.dumps(manager.diagnostics.state,indent=2))
        (OUT/'state.json').write_text(json.dumps(dict(app.state['updates']),indent=2))
        assert (project/'uv.lock').read_bytes()==beforelock
        assert {k:git(v,'rev-parse','HEAD') for k,v in roots.items()}==beforeheads
        assert all((home/path).read_bytes()==data for path,data in beforebindings.items())
        if not positive:
            assert app.state['updates']['phase']==('checked' if case=='dirty-source' else 'error'),app.state['updates']
            assert not updates.active_release(home).get('current')
            assert not app.state['updates'].get('pendingRelease')
            if case in {'old-pin','custom-pin'}:
                assert manager.diagnostics.state['lastFailure'].get('reason') in {
                    'runtime-source-constraint','protected-runtime-source'}
                assert manager.diagnostics.state['lastFailure'].get('package')=='amplifier-foundation'
                probes=[p.read_text() for p in OUT.glob('probe-*.log')]
                assert not any('"dependenciesPrepared": true' in text for text in probes)
            if case=='failed-qualification':
                assert manager.diagnostics.state['lastFailure']['phase']=='ecosystem-compatibility'
            if case in {'missing-activation-api','retain-pin'}:
                # A valid retained source policy is not API compatibility. Freeze
                # preserves its exact bytes; the final probe must refuse adoption.
                failure=manager.diagnostics.state['lastFailure']
                assert failure['phase']=='ecosystem-compatibility'
                assert failure['probe']['ok'] is False
                assert failure['probe']['errorType']=='RuntimeDependencyError'
                stages=list((home/'updates/releases').iterdir());assert len(stages)==1
                stage=stages[0];final=runtime_environment.project_path(home,stage.name)
                verify_recorded(final,stage)
                graph=installed_graph(final)
                foundation=next(row for row in graph if row['name']=='amplifier-foundation')
                assert foundation['directUrl']['vcs_info']['commit_id']==foundation_target
                if case=='retain-pin':
                    assert next(r for r in graph if r['name']=='amplifier-core')['directUrl']['vcs_info']['commit_id']==newcore
                    policy=json.loads((stage/'selected-source-declarations.json').read_text())
                    assert policy['packages']['amplifier-foundation']['status']=='explicit-pin'
                    assert policy['packages']['amplifier-foundation']['ref']==OLD
                (OUT/'installed-refused.json').write_text(json.dumps(graph,indent=2))
            if case=='dirty-source':
                assert manifest_file.read_text().endswith('# user edit retained\n')
                assert not list((home/'updates/releases').glob('*'))
            return
        assert app.state['updates']['phase'] in {'staged','installed'},app.state['updates'].get('error')
        generation=app.state['updates'].get('pendingRelease') or updates.active_release(home)['current']
        stage=home/'updates/releases'/generation
        final=runtime_environment.project_path(home,generation);verify_recorded(final,stage)
        graph=installed_graph(final)
        foundation=next(row for row in graph if row['name']=='amplifier-foundation')
        assert foundation['directUrl']['vcs_info']['commit_id']==foundation_target
        assert (stage/'profiles-qualified.json').is_file()
        assert (project/'uv.lock').read_bytes()==beforelock
        assert {k:git(v,'rev-parse','HEAD') for k,v in roots.items()}==beforeheads
        assert any('update_probe.py' in ' '.join(row['command']) for row in observed)
        (OUT/'installed.json').write_text(json.dumps(graph,indent=2))
        for name in ['runtime-sources.json','selected-source-declarations.json','profiles-qualified.json']:
            shutil.copy2(stage/name,OUT/name)
        assert all((home/path).read_bytes()==data for path,data in beforebindings.items())
        from amplifier_web.host.config import read_config
        selected=read_config(work,home=home,global_only=True)
        assert selected.registry_home==stage/'foundation'
        assert runtime_environment.project_path(home,updates.active_release(home)['current'])==final
        await manager.rollback()
        assert updates.active_release(home).get('current') is None
        assert runtime_environment.project_path(home)==project
        assert (project/'uv.lock').read_bytes()==beforelock
        assert all((home/path).read_bytes()==data for path,data in beforebindings.items())
        (OUT/'rollback.json').write_text(json.dumps({'pointer':updates.active_release(home),
            'baselineLockSha256':hashlib.sha256(beforelock).hexdigest(),'oldSources':beforeheads}))
    finally:
        (OUT/'commands.json').write_text(json.dumps(observed,indent=2))
        await app.close()
