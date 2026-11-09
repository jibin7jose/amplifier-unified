"""Host-only and transitive components follow new resolution, not old versions."""
import copy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_component_graph as graph, app_updates
from test_app_updates import prepared_activation, synthetic_release_artifact, synthetic_artifact_graph, synthetic_tool_install

APP = {'name':'amplifier-unified','version':'99.0.0','url':app_updates.SOURCE,'revision':'a'*40}
NATIVE = {'name':'amplifier-module-tool-computer-use','version':'1.0.0',
          'url':'https://github.com/microsoft/amplifier-bundle-computer-use',
          'revision':'b'*40,'subdirectory':'modules/tool-computer-use'}
CORE = {'name':'amplifier-core','version':'1.6.1'}
COMPONENTS = sorted([APP, NATIVE, CORE, {'name':'six','version':'1.17.0'}], key=lambda row:row['name'])


def raw_rows(rows):
    result=[]
    for row in rows:
        direct=None
        if row.get('url'):
            direct={'url':row['url'],'vcs_info':{'vcs':'git','commit_id':row['revision'],'requested_revision':'main'}}
            if row.get('subdirectory'):direct['subdirectory']=row['subdirectory']
        result.append({'name':row['name'],'version':row['version'],'direct':direct})
    return result


async def test_isolated_graph_probe_captures_transitive_and_optional_sources():
    process=AsyncMock(return_value=json.dumps(raw_rows(COMPONENTS)))
    assert await graph.read_graph(process,'/fixture/python')==COMPONENTS
    assert process.call_args.args[1:3]==('-I','-c')
    locked=graph.requirements(COMPONENTS)
    assert 'amplifier-core==1.6.1' in locked
    assert '@'+('b'*40)+'#subdirectory=modules/tool-computer-use' in locked
    assert 'amplifier-unified' not in locked


def test_current_ui_use_graph_round_trip_keeps_exact_source_and_refuses_tampering():
    component = {'name': 'amplifier-bundle-ui-use', 'version': '0.1.0.dev0',
                 'url': 'https://github.com/microsoft/amplifier-bundle-ui-use',
                 'revision': '5e3b08b8e06a6095314200476d387508ddb32f67'}
    current = graph.normalized(raw_rows([CORE, component]))
    assert graph.validate_graph(current) == current
    assert component['url'] + '@' + component['revision'] in graph.requirements(current)
    for patch in ({'url': 'file:///private/local'},
                  {'revision': 'main'}, {'subdirectory': '../private'}):
        with pytest.raises(ValueError):
            graph.validate_graph(graph.normalized(raw_rows([CORE, {**component, **patch}])))


@pytest.mark.parametrize('change', ['credentials','local','unknown-vcs','missing-commit','traversal','duplicate','bad-version'])
def test_bad_provenance_is_rejected_before_creating_resolver_input(change):
    rows=raw_rows(COMPONENTS)
    native=next(row for row in rows if row['name']==NATIVE['name'])
    if change=='credentials':native['direct']['url']='https://secret@github.com/microsoft/amplifier-bundle-computer-use'
    if change=='local':native['direct']={'url':'file:///private/edited-source','dir_info':{'editable':True}}
    if change=='unknown-vcs':native['direct']['vcs_info']['vcs']='other'
    if change=='missing-commit':native['direct']['vcs_info']['commit_id']='main'
    if change=='traversal':native['direct']['subdirectory']='../../private'
    if change=='duplicate':rows.append(copy.deepcopy(native))
    if change=='bad-version':native['version']='1.0.0\n--index-url https://unexpected'
    with pytest.raises(ValueError):graph.normalized(rows)


async def test_same_version_changed_git_commit_and_new_core_release_are_updates(monkeypatch):
    monkeypatch.setattr(graph,'installed_graph',lambda:COMPONENTS)
    monkeypatch.setattr(graph,'index_latest',AsyncMock(return_value='1.7.0'))
    process=AsyncMock(return_value='c'*40+'\trefs/heads/main\n')
    updates=await graph.updates(process,{})
    assert {row['name'] for row in updates}=={NATIVE['name'],CORE['name']}
    assert next(row for row in updates if row['name']==NATIVE['name'])['latest']=='c'*40
    assert len(process.call_args_list)==1
    assert APP['url'] not in process.call_args.args  # Application still follows its release.


async def test_same_release_can_offer_host_component_update(monkeypatch):
    monkeypatch.setattr(app_updates,'__version__','99.0.0')
    monkeypatch.setattr(app_updates.shutil,'which',lambda _: '/fixture/tool')
    monkeypatch.setattr(graph,'updates',AsyncMock(return_value=[{**NATIVE,'latest':'c'*40}]))
    async def process(*args,**kwargs):
        return json.dumps({'tag_name':'v99.0.0'}) if args[0]=='gh' else 'a'*40+'\trefs/tags/v99.0.0'
    monkeypatch.setattr(app_updates,'process',process)
    async def metadata(path, **kwargs):
        return await app_updates.process('gh', 'api', path)
    monkeypatch.setattr(app_updates, 'github_read', metadata)
    result=await app_updates.check()
    assert result['status']=='update'
    assert result['revision']==APP['revision'] and result['componentUpdates']


async def staged(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    # Exercise legacy reinstall, not promotion of the runner's installed app.
    previous_target = app_updates.installed_target
    async def legacy_target():
        uv, launcher, python, previous = await previous_target()
        return uv, launcher, python, {**previous, 'installation': str(tmp_path / 'legacy-bootstrap')}
    monkeypatch.setattr(app_updates, 'installed_target', legacy_target)
    service.state['updates']['application']=dict(service.state['updates']['pendingApp'])
    service.state['updates']['pendingApp']=None
    calls=[]
    async def process(*args,**kwargs):
        calls.append(args)
        synthetic_tool_install(args, kwargs)
        return '99.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr(app_updates.shutil,'which',lambda _: '/fixture/uv')
    monkeypatch.setattr(app_updates.artifacts, 'prepare', synthetic_release_artifact)
    async def observed(*args, artifact=None):
        return synthetic_artifact_graph(copy.deepcopy(COMPONENTS), artifact)
    monkeypatch.setattr(graph,'read_graph',AsyncMock(side_effect=observed))
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed',lambda _:True)
    await app_updates.stage(manager)
    pending=service.state['updates']['pendingApp']
    folder=manager.directory/'applications'/APP['revision']/pending['generation']
    python=folder/'tools/amplifier-unified/bin/python';python.parent.mkdir(parents=True,exist_ok=True);python.touch()
    return service,manager,folder,calls


async def test_new_resolution_refreshes_but_activation_uses_exact_reviewed_graph(tmp_path,monkeypatch):
    service,manager,folder,calls=await staged(tmp_path,monkeypatch)
    try:
        assert '--upgrade' in calls[0] and '--refresh' in calls[0]
        receipt=json.loads((folder/'validated.json').read_text())
        expected = synthetic_artifact_graph(COMPONENTS, receipt['artifact'])
        assert receipt['componentGraph']==expected
        assert receipt['componentDigest']==graph.digest(expected)
        assert calls[0][-1] == app_updates.artifacts.requirement(receipt['artifact'])
        await app_updates.activate(manager)
        installs=[call for call in calls if 'install' in call]
        assert '--overrides' in installs[1] and str(folder/'components.txt') in installs[1]
        assert '--refresh' not in installs[1] and '--upgrade' not in installs[1]
        assert service.state['updates']['pendingRestart']
    finally:await service.close()


@pytest.mark.parametrize('tamper',['candidate','resolver-file','receipt'])
async def test_changed_candidate_rejected_before_runtime_close_or_replacement(tmp_path,monkeypatch,tamper):
    service,manager,folder,calls=await staged(tmp_path,monkeypatch)
    close=AsyncMock();monkeypatch.setattr(service.runtime,'close',close)
    try:
        if tamper=='candidate':
            changed=copy.deepcopy(COMPONENTS);changed[1]['revision']='c'*40
            monkeypatch.setattr(graph,'read_graph',AsyncMock(return_value=changed))
        elif tamper=='resolver-file':(folder/'components.txt').write_text('six==0.0.0\n')
        else:
            receipt=json.loads((folder/'validated.json').read_text());receipt['componentDigest']='0'*64
            (folder/'validated.json').write_text(json.dumps(receipt))
        with pytest.raises(ValueError):await app_updates.activate(manager)
        close.assert_not_awaited()
        assert len([call for call in calls if 'install' in call])==1
        assert not (manager.directory/'previous-app.json').exists()
    finally:await service.close()


async def test_replacement_graph_mismatch_cannot_restart_or_claim_success(tmp_path,monkeypatch):
    service,manager,folder,calls=await staged(tmp_path,monkeypatch)
    candidate = json.loads((folder/'validated.json').read_text())['componentGraph']
    changed=copy.deepcopy(candidate);changed[1]['revision']='c'*40
    monkeypatch.setattr(graph,'read_graph',AsyncMock(side_effect=[candidate,changed]))
    restart=AsyncMock();monkeypatch.setattr(app_updates,'request_managed_restart',restart)
    try:
        await app_updates.activate(manager)
        restart.assert_not_awaited()
        assert service.state['updates']['phase']=='error'
        assert service.state['updates']['pendingApp'] is None
        assert not service.state['updates'].get('pendingRestart')
    finally:await service.close()


async def test_restage_same_release_preserves_previous_generation_and_legacy_receipt(tmp_path,monkeypatch):
    service,manager,first,calls=await staged(tmp_path,monkeypatch)
    before={path.name:path.read_bytes() for path in first.iterdir() if path.is_file()}
    legacy=(first.parent/'validated.json').read_bytes()
    try:
        await app_updates.stage(manager)
        second=first.parent/service.state['updates']['pendingApp']['generation']
        assert second!=first and (second/'validated.json').is_file()
        assert {path.name:path.read_bytes() for path in first.iterdir() if path.is_file()}==before
        assert (first.parent/'validated.json').read_bytes()==legacy
    finally:await service.close()


async def test_local_override_added_after_stage_is_preserved(tmp_path,monkeypatch):
    service,manager,folder,calls=await staged(tmp_path,monkeypatch)
    close=AsyncMock();monkeypatch.setattr(service.runtime,'close',close)
    checked=[]
    def changed():
        checked.append(True)
        raise ValueError('A local editable override now owns the dependency')
    monkeypatch.setattr(graph,'installed_graph',changed)
    before=(folder/'validated.json').read_bytes()
    try:
        with pytest.raises(ValueError):await app_updates.activate(manager)
        assert checked==[True]
        close.assert_not_awaited()
        assert len([call for call in calls if 'install' in call])==1
        assert service.state['updates']['pendingApp'] is None
        assert not service.state['updates'].get('pendingRestart')
        assert (folder/'validated.json').read_bytes()==before
    finally:await service.close()


async def test_invalid_new_candidate_can_be_explicitly_restaged_without_replaying(tmp_path,monkeypatch):
    service,manager,folder,calls=await staged(tmp_path,monkeypatch)
    (folder/'components.txt').write_text('six==0.0.0\n')
    before=(folder/'validated.json').read_bytes()
    try:
        with pytest.raises(ValueError,match='resolution changed'):await manager.install()
        assert service.state['updates']['pendingApp'] is None
        assert service.state['updates']['appAvailable']
        monkeypatch.setattr(manager,'busy',lambda:True)
        await manager.install()
        assert service.state['updates']['pendingApp']['generation']!=folder.name
        assert len([call for call in calls if 'install' in call])==2  # Both isolated candidates.
        assert not (manager.directory/'previous-app.json').exists()
        assert (folder/'validated.json').read_bytes()==before
    finally:await service.close()


async def test_same_version_component_update_survives_an_ordinary_restart(tmp_path):
    from amplifier_web import __version__
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    app={'id':'application','latest':'v'+__version__,'current':__version__,'status':'update',
         'componentUpdates':[{'name':'amplifier-core','version':'1.6.1','latest':'1.7.0'}]}
    service.state['updates']={'phase':'available','application':app,'items':[app],'available':1,'appAvailable':True}
    UpdateManager(service)
    try:
        assert service.state['updates']['application']['status']=='update'
        assert service.state['updates']['appAvailable']
        assert service.state['updates']['available']==1
    finally:await service.close()


@pytest.mark.parametrize('duplicate',[False,True])
async def test_restart_recovery_matches_unique_exact_generation_receipt(tmp_path,monkeypatch,duplicate):
    from test_update_readiness import legacy_state, make_manager, validated_record
    from amplifier_web import update_readiness
    service,manager=make_manager(tmp_path,monkeypatch,state=legacy_state())
    legacy=validated_record(manager)
    receipt=json.loads(legacy.read_text());receipt['generation']='f'*32
    generated=legacy.parent/receipt['generation']/'validated.json';generated.parent.mkdir()
    generated.write_text(json.dumps(receipt))
    if not duplicate:legacy.unlink()
    try:
        recovered=update_readiness.recovery_candidate(manager)
        if duplicate:assert recovered is None
        else:assert recovered['generation']==receipt['generation']
    finally:await service.close()


REAL_INSTALLED_GRAPH=graph.installed_graph

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['fork','custom-branch','local','credentials'])
async def test_existing_custom_source_is_preserved_after_stale_check(tmp_path,monkeypatch,kind):
    from types import SimpleNamespace
    service,manager,old_folder,calls=await staged(tmp_path,monkeypatch)
    direct={'url':'https://github.com/microsoft/amplifier-foundation','vcs_info':{'vcs':'git','commit_id':'d'*40,'requested_revision':'main'}}
    if kind=='fork':direct['url']='https://github.com/example/amplifier-foundation'
    elif kind=='custom-branch':direct['vcs_info']['requested_revision']='local-feature'
    elif kind=='local':direct={'url':'file:///fixture/editable-source','dir_info':{'editable':True}}
    elif kind=='credentials':direct['url']='https://secret@github.com/microsoft/amplifier-foundation'
    root=SimpleNamespace(metadata={'Name':'amplifier-unified'},version='99.0.0',read_text=lambda _:json.dumps({'url':'file:///fixture/application.whl','archive_info':{}}))
    dependency=SimpleNamespace(metadata={'Name':'amplifier-foundation'},version='1.0.0',read_text=lambda _:json.dumps(direct))
    monkeypatch.setattr(graph,'installed_graph',REAL_INSTALLED_GRAPH)
    monkeypatch.setattr(graph.metadata,'distributions',lambda:[root,dependency])
    service.state['updates']['pendingApp']=None
    before=(old_folder/'validated.json').read_bytes()
    close=AsyncMock(return_value=None);monkeypatch.setattr(service.runtime,'close',close)
    try:
        with pytest.raises(ValueError):await app_updates.stage(manager)
        close.assert_not_awaited()
        assert len([call for call in calls if 'install' in call])==1
        assert (old_folder/'validated.json').read_bytes()==before
        assert service.state['updates']['diagnostics']['lastFailure']['phase']=='host-components'
        diagnostics=json.dumps(service.state['updates']['diagnostics'])
        assert 'secret' not in diagnostics and 'editable-source' not in diagnostics
    finally:await service.close()

@pytest.mark.parametrize('requested',['main','d'*40])
def test_root_wheel_excluded_and_canonical_git_receipts_accepted(monkeypatch,requested):
    from types import SimpleNamespace
    direct={'url':'https://github.com/microsoft/amplifier-foundation','vcs_info':{'vcs':'git','commit_id':'d'*40,'requested_revision':requested}}
    root=SimpleNamespace(metadata={'Name':'amplifier-unified'},version='99.0.0',read_text=lambda _:json.dumps({'url':'file:///fixture/application.whl','archive_info':{}}))
    dependency=SimpleNamespace(metadata={'Name':'amplifier-foundation'},version='1.0.0',read_text=lambda _:json.dumps(direct))
    third_party=SimpleNamespace(metadata={'Name':'legitimate-library'},version='1.0.0',read_text=lambda _:json.dumps({'url':'https://github.com/example/legitimate-library','vcs_info':{'vcs':'git','commit_id':'e'*40,'requested_revision':'main'}}))
    monkeypatch.setattr(graph.metadata,'distributions',lambda:[root,dependency,third_party])
    installed=REAL_INSTALLED_GRAPH()
    assert len(installed)==2 and all(row['name']!='amplifier-unified' for row in installed)
    assert installed[0]['revision']=='d'*40
