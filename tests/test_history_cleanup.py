"""Reviewed native history cleanup preserves project files and sibling sessions."""
import json
from pathlib import Path
import pytest
from amplifier_web.service import AppService, AppError
from amplifier_web.session_files import sessions_dir, project_slug
from amplifier_web.managed_deletion import removed

@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'cli'))
    service = AppService(tmp_path/'app', workspace=tmp_path/'project')
    yield service
    await service.close()

async def seed(app, tmp_path, name='Saved session'):
    workspace=tmp_path/'project';workspace.mkdir(exist_ok=True)
    (workspace/'source.txt').write_text('working files stay')
    await app.dispatch('session.create', {'title':name, 'workspace':str(workspace)})
    row=app._session();sid=row['id']
    path=sessions_dir(workspace)/sid;path.mkdir(parents=True,exist_ok=True)
    (path/'metadata.json').write_text(json.dumps({'session_id':sid,'working_dir':str(workspace),'title':name}))
    (path/'transcript.jsonl').write_text('{"role":"user","content":"saved"}\n')
    row['messages']=[{'id':'saved-'+sid,'role':'user','text':'Saved private turn'}]
    app._publish_changes(sessions={sid})
    return sid,path,{'project':project_slug(workspace),'nativeId':sid}

async def confirm(app, selection):
    review=(await app.dispatch('storage.historyDeletePreview',selection))['result']
    return review,{**selection,'confirmationToken':review['confirmationToken']}

async def test_single_session_keeps_sibling_project_and_cli_configuration(app,tmp_path):
    sid,path,selection=await seed(app,tmp_path)
    sibling,other,_=await seed(app,tmp_path,'Keep this session')
    cli=tmp_path/'cli';(cli/'settings.yaml').write_text('providers: []\n')
    review,args=await confirm(app,selection)
    assert review['summary']['conversationCount']==1
    assert path.exists()
    result=(await app.dispatch('storage.historyDelete',args))['result']
    assert result['deleted'] and not result['cleanupPending']
    assert not path.exists() and other.exists()
    assert app._session(sibling)['title']=='Keep this session'
    assert (tmp_path/'project/source.txt').exists() and (cli/'settings.yaml').read_text()=='providers: []\n'
    assert removed(app.db,sid) and not removed(app.db,workspace=str(tmp_path/'project'))
    # A new chat in the same project is still permitted; same-token retries do no more work.
    await app.dispatch('session.create',{'title':'New project work','workspace':str(tmp_path/'project')})
    assert (await app.dispatch('storage.historyDelete',args))['result']==result

async def test_project_deletes_all_reviewed_sessions_and_survives_restart(app,tmp_path):
    first,path,selection=await seed(app,tmp_path)
    second,other,_=await seed(app,tmp_path,'Second session')
    selection.pop('nativeId')
    review,args=await confirm(app,selection)
    assert review['summary']['conversationCount']==2
    result=(await app.dispatch('storage.historyDelete',args))['result']
    assert not result['cleanupPending'] and not path.exists() and not other.exists()
    assert not any(row['id'] in {first,second} for row in app.state['sessions'])
    await app.close()
    reopened=AppService(app.data_dir,workspace=tmp_path/'project')
    try:
        assert removed(reopened.db,first) and not removed(reopened.db,workspace=str(tmp_path/'project'))
        assert (await reopened.dispatch('storage.historyDelete',args))['result']==result
    finally:await reopened.close()


@pytest.mark.parametrize('whole_history', [False, True])
@pytest.mark.parametrize('interrupted', [False, True])
async def test_reviewed_history_scope_keeps_catalog_siblings_and_future_sessions(
        app, tmp_path, monkeypatch, whole_history, interrupted):
    from amplifier_web import managed_deletion
    from test_automatic_history import native_session, files_snapshot

    workspace = tmp_path/'project'
    target = native_session(workspace, 'selected-root')
    worker = native_session(workspace, 'selected-worker', metadata={'parent_id':'selected-root'})
    sibling = native_session(workspace, 'sibling-root')
    (workspace/'source.txt').write_text('Independent project file')
    sibling_before = files_snapshot(sibling)
    app.subscribe()

    async def refresh(owner):
        await owner.history.refresh(force=True)
        for _ in range(100):
            shared = owner.state['sharedHistory']
            assert shared.get('error') is None, shared
            if shared.get('reconciliation', {}).get('phase') == 'complete':
                return
            await owner.history.refresh(force=False)
        pytest.fail('Synthetic history discovery did not complete')

    await refresh(app)

    def visible(owner, expected):
        assert {row['id'] for row in owner.directory.select(limit=20)} == expected
        roots = expected - {'selected-worker'}
        page = owner.directory.chats({**owner.state, 'view': {
            **owner.state.get('view', {}), 'navChatScope':'all'}}, section='recent')
        assert {row['id'] for row in page['items']} == roots
        assert page['total'] == owner.directory.root_count() == len(roots)
        assert owner.state['sharedHistory']['sessionCount'] == len(roots)
        assert owner.state['sharedHistory']['workerSessionCount'] == int('selected-worker' in expected)

    visible(app, {'selected-root','selected-worker','sibling-root'})
    selection = {'project':project_slug(workspace)}
    if not whole_history:
        selection['nativeId'] = 'selected-root'
    review, args = await confirm(app, selection)
    assert review['summary']['conversationCount'] == (3 if whole_history else 2)
    stage = managed_deletion._stage
    if interrupted:
        def unavailable(*args, **kwargs):
            raise OSError('Synthetic interrupted physical cleanup')
        monkeypatch.setattr(managed_deletion, '_stage', unavailable)
    result = (await app.dispatch('storage.historyDelete', args))['result']
    assert result['deleted'] and result['cleanupPending'] is interrupted
    assert target.exists() is interrupted and worker.exists() is interrupted
    retained = set() if whole_history else {'sibling-root'}
    # A pending purge leaves real native files and index rows behind. They must
    # stay suppressed without hiding unrelated rows in the same project.
    await refresh(app)
    visible(app, retained)
    future = native_session(workspace, 'future-root')
    future_before = files_snapshot(future)
    await refresh(app)
    visible(app, retained | {'future-root'})
    await app.close()
    monkeypatch.setattr(managed_deletion, '_stage', stage)
    reopened = AppService(app.data_dir, workspace=workspace)
    try:
        reopened.subscribe()
        await refresh(reopened)
        visible(reopened, retained | {'future-root'})
        assert not target.exists() and not worker.exists()
        assert files_snapshot(future) == future_before
        if not whole_history:
            assert files_snapshot(sibling) == sibling_before
        assert (workspace/'source.txt').read_text() == 'Independent project file'
        assert not (await reopened.dispatch('storage.historyDelete', args))['result']['cleanupPending']
    finally:
        await reopened.close()

async def test_refuses_active_changed_linked_and_unverified_scopes(app,tmp_path):
    sid,path,selection=await seed(app,tmp_path)
    app._session(sid)['status']='working'
    with pytest.raises(AppError,match='active work'):await confirm(app,selection)
    app._session(sid)['status']='idle'
    _,args=await confirm(app,selection)
    (path/'later').write_text('keep unreviewed change')
    with pytest.raises(AppError,match='changed'):await app.dispatch('storage.historyDelete',args)
    (path/'link').symlink_to(tmp_path/'cli/settings.yaml')
    with pytest.raises(AppError,match='symbolic'):await confirm(app,selection)
    (path/'link').unlink()
    with pytest.raises(AppError):await confirm(app,{'project':'../settings'})
    with pytest.raises(AppError):await confirm(app,{**selection,'nativeId':'../settings'})
    (path/'metadata.json').write_text(json.dumps({'working_dir':'/different/project'}))
    with pytest.raises(AppError,match='verified'):await confirm(app,selection)
    assert path.exists()

async def test_external_ownership_blocks_deletion(app,tmp_path):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    sid,path,selection=await seed(app,tmp_path)
    store=SharedSessionStore(str(tmp_path/'project'),sid)
    lock=store.acquire(app='test-cli',operation='test-run')
    try:
        _,args=await confirm(app,selection)
        with pytest.raises(AppError,match='Another application'):await app.dispatch('storage.historyDelete',args)
        assert path.exists()
    finally:lock.release()

async def test_single_session_includes_descendant_workers_only(app,tmp_path):
    sid,path,selection=await seed(app,tmp_path)
    child=path.parent/'child-worker';child.mkdir()
    (child/'metadata.json').write_text(json.dumps({'working_dir':str(tmp_path/'project'),'parent_id':sid}))
    (child/'events.jsonl').write_text('worker history')
    sibling,other,_=await seed(app,tmp_path,'Unrelated')
    review,args=await confirm(app,selection)
    assert review['summary']['conversationCount']==2
    result=(await app.dispatch('storage.historyDelete',args))['result']
    assert not result['cleanupPending'] and not child.exists() and other.exists()


@pytest.mark.parametrize('child_id', ['child-worker', '0000000000000000-123456789abcdef0_self'])
async def test_worker_only_cleanup_refuses_without_ancestor_execution_exclusion(app, tmp_path, child_id):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    sid, path, selection = await seed(app, tmp_path)
    child = path.parent/child_id; child.mkdir()
    (child/'metadata.json').write_text(json.dumps({'working_dir': str(tmp_path/'project'), 'parent_id': sid}))
    (child/'transcript.jsonl').write_text('synthetic worker transcript')
    # Even a compatible worker ID must not be treated as independently owned.
    lease = SharedSessionStore(tmp_path/'project', sid).acquire(app='synthetic-active-ancestor')
    try:
        with pytest.raises(AppError, match='top-level conversation'):
            await confirm(app, {**selection, 'nativeId': child_id})
    finally:
        lease.release()
    assert path.exists() and child.exists()


async def test_shared_history_native_workers_stay_explicitly_refused(app, tmp_path):
    sid, path, selection = await seed(app, tmp_path)
    child = path.parent/'0000000000000000-123456789abcdef0_self'; child.mkdir()
    (child/'metadata.json').write_text(json.dumps({'working_dir': str(tmp_path/'project'), 'parent_id': sid}))
    for scope in (selection, {'project': selection['project']}):
        with pytest.raises(AppError, match='native worker identities'):
            await confirm(app, scope)
    assert path.exists() and child.exists()


@pytest.mark.parametrize('parent', ['missing-root', 'child-worker'])
async def test_project_cleanup_refuses_unverified_or_cyclic_ancestry(app, tmp_path, parent):
    _, path, selection = await seed(app, tmp_path)
    child = path.parent/'child-worker'; child.mkdir()
    (child/'metadata.json').write_text(json.dumps({'working_dir': str(tmp_path/'project'), 'parent_id': parent}))
    with pytest.raises(AppError, match='ancestry could not be verified'):
        await confirm(app, {'project': selection['project']})
    assert path.exists() and child.exists()


def test_large_root_does_not_starve_other_roots(tmp_path,monkeypatch):
    from amplifier_web.storage_catalog import inspect
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'cli'))
    history=tmp_path/'cli/projects/project/sessions/one';history.mkdir(parents=True)
    for i in range(30):(history/str(i)).write_bytes(b'x'*10)
    attachment=tmp_path/'app/attachments/file';attachment.parent.mkdir(parents=True);attachment.write_bytes(b'1234')
    result=inspect(tmp_path/'app',[],{'category':'sessions'},limit=8)
    row=next(r for r in result['rows'] if r['label']=='Attachments')
    assert row['bytes']==4 and not row['incomplete']
    assert result['incomplete']
    assert next(r for r in result['rows'] if r['label'].startswith('Shared'))['pending']
    assert result['measuring']  # Another slice can advance the unfinished history root.

async def test_old_project_folder_does_not_need_to_be_recreated(app,tmp_path):
    import shutil
    _,path,selection=await seed(app,tmp_path)
    shutil.rmtree(tmp_path/'project')
    _,args=await confirm(app,selection)
    result=(await app.dispatch('storage.historyDelete',args))['result']
    assert not result['cleanupPending'] and not path.exists() and not (tmp_path/'project').exists()

async def test_later_project_cleanup_keeps_earlier_tombstones(app,tmp_path):
    first,_,selection=await seed(app,tmp_path)
    selection.pop('nativeId');_,first_args=await confirm(app,selection)
    await app.dispatch('storage.historyDelete',first_args)
    second,_,_=await seed(app,tmp_path,'Later chat')
    _,second_args=await confirm(app,selection)
    await app.dispatch('storage.historyDelete',second_args)
    assert removed(app.db,first) and removed(app.db,second)
    assert (await app.dispatch('storage.historyDelete',first_args))['result']['deleted']

async def test_interrupted_confirmed_cleanup_recovers_without_removing_sibling(app,tmp_path,monkeypatch):
    from amplifier_web import managed_deletion
    first,path,selection=await seed(app,tmp_path)
    second,other,_=await seed(app,tmp_path,'Keep during recovery')
    _,args=await confirm(app,selection)
    original=managed_deletion._purge
    def interrupt(*args):raise OSError('Synthetic cleanup interruption')
    monkeypatch.setattr(managed_deletion,'_purge',interrupt)
    result=(await app.dispatch('storage.historyDelete',args))['result']
    assert result['deleted'] and result['cleanupPending']
    monkeypatch.setattr(managed_deletion,'_purge',original)
    await app.close()
    reopened=AppService(app.data_dir,workspace=tmp_path/'project')
    try:
        assert not path.exists() and other.exists()
        assert reopened._session(second)['title']=='Keep during recovery'
        assert not (await reopened.dispatch('storage.historyDelete',args))['result']['cleanupPending']
        assert (tmp_path/'project/source.txt').exists()
    finally:await reopened.close()

async def test_changed_project_set_and_wrong_scope_token_are_rejected(app,tmp_path):
    _,path,selection=await seed(app,tmp_path)
    project={'project':selection['project']};_,args=await confirm(app,project)
    _,other,_=await seed(app,tmp_path,'Added after preview')
    with pytest.raises(AppError,match='changed'):await app.dispatch('storage.historyDelete',args)
    with pytest.raises(AppError,match='Review'):await app.dispatch('storage.historyDelete',{**selection,'confirmationToken':args['confirmationToken']})
    assert path.exists() and other.exists()

async def test_malformed_or_special_metadata_is_refused_without_reading_it(app,tmp_path):
    import os
    _,path,selection=await seed(app,tmp_path)
    metadata=path/'metadata.json'
    metadata.write_text('[]')
    with pytest.raises(AppError,match='metadata could not be verified'):await confirm(app,selection)
    metadata.unlink();os.mkfifo(metadata)
    with pytest.raises(AppError,match='metadata could not be verified'):await confirm(app,selection)
    assert path.exists()

async def test_duplicate_titles_have_exact_review_identity_and_stale_activity_is_refused(app, tmp_path):
    first, first_path, selection = await seed(app, tmp_path, 'Same title')
    second, second_path, second_selection = await seed(app, tmp_path, 'Same title')
    metadata = first_path / 'metadata.json'
    value = json.loads(metadata.read_text())
    value['last_event_at'] = '2026-10-14T12:00:00Z'
    metadata.write_text(json.dumps(value))
    review, args = await confirm(app, selection)
    other, _ = await confirm(app, second_selection)
    assert review['title'] == other['title'] == 'Same title'
    assert review['reviewIdentity'] == {'scope': 'session', 'workspace': str(tmp_path/'project'),
                                        'sessionId': first, 'lastActivityAt': 1791979200.0}
    assert other['reviewIdentity'] == {'scope': 'session', 'workspace': str(tmp_path/'project'), 'sessionId': second}
    value['last_event_at'] = '2026-10-14T13:00:00Z'
    metadata.write_text(json.dumps(value))
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('storage.historyDelete', args)
    assert first_path.exists() and second_path.exists()
    project, _ = await confirm(app, {'project': selection['project']})
    assert project['reviewIdentity'] == {'scope': 'project', 'workspace': str(tmp_path/'project'), 'sessionId': None}
