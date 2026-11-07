import asyncio
import copy
import hashlib
import json
import subprocess

import pytest

from amplifier_web.service import AppService, AppError
from test_service import Runtime


@pytest.fixture
async def app(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    service=AppService(tmp_path/'app',workspace=workspace,runtime=Runtime())
    await service.dispatch('session.create',{'title':'Output source'})
    service._message(service._session(),'assistant','A report and supporting dataset.','text')
    await service.dispatch('view.update',{'patch':{'draft':'Unsent draft'}})
    yield service
    await service.close()


async def test_exact_file_versions_lineage_receipts_unlink_and_resource_retention(app):
    sid=app._session()['id'];path=app._session()['workspace']
    from pathlib import Path
    file=Path(path)/'report.txt';file.write_text('Original report')
    original=copy.deepcopy(app._session()['messages'])
    args={'sessionId':sid,'kind':'file','title':'Report','path':'report.txt','messageId':original[0]['id']}
    first=(await app.dispatch('outputs.attach',args,command_id='attach-one'))['result']
    file.write_text('Corrected report')
    duplicate=(await app.dispatch('outputs.attach',args,command_id='attach-one'))['result']
    assert duplicate['id']==first['id'] and duplicate['duplicate']
    second=(await app.dispatch('outputs.attach',{**args,'parentId':first['id'],'expectedSha256':hashlib.sha256(b'Corrected report').hexdigest()}))['result']
    assert second['version']==2 and second['parentId']==first['id']
    await app.dispatch('outputs.unlink',{'sessionId':sid,'id':first['id'],'expectedRevision':1})
    assert len((await app.dispatch('outputs.list',{'sessionId':sid}))['result']['items'])==1
    from amplifier_web.resource_files import collect
    assert first['body']['$resource'] not in collect(app.db,app.state)
    read=(await app.dispatch('outputs.read',{'sessionId':sid,'id':first['id']}))['result']
    assert read['text']=='Original report' and not read['linked']
    assert file.read_text()=='Corrected report' and app._session()['messages']==original
    assert app.state['view']['draft']=='Unsent draft' and app.state['selectedSessionId']==sid and not app.runtime.sent
    with pytest.raises(AppError,match='changed'):
        await app.dispatch('outputs.relink',{'sessionId':sid,'id':first['id'],'expectedRevision':1})


async def test_scope_and_file_boundaries_are_shared_with_agent(app,tmp_path):
    sid=app._session()['id'];outside=tmp_path/'private.txt';outside.write_text('private')
    from pathlib import Path
    (Path(app._session()['workspace'])/'escape').symlink_to(outside)
    for path in [str(outside),'../private.txt','escape']:
        with pytest.raises(AppError,match='inside'):
            await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'file','title':'Escape','path':path})
    writing=(await app.app_bridge('dispatch',{'action':'outputs.write','args':{'title':'Memo','content':'Reusable memo','variant':'document'}},sid))['result']
    await app.dispatch('session.create',{'title':'Other'})
    other=app._session()['id']
    with pytest.raises(AppError,match='calling'):
        await app.app_bridge('dispatch',{'action':'outputs.read','args':{'sessionId':sid,'id':writing['id']}},other)
    with pytest.raises(AppError,match='another'):
        await app.dispatch('outputs.read',{'sessionId':other,'id':writing['id']})
    with pytest.raises(AppError,match='another|originating'):
        await app.dispatch('outputs.write',{'sessionId':other,'title':'Bad lineage','content':'Memo','variant':'standard','parentId':writing['id']})


async def test_external_references_truthful_versions_and_local_comments(app):
    sid=app._session()['id']
    for url in ['javascript:alert(1)','https://user:secret@example.test/x','file:///tmp/a']:
        with pytest.raises(AppError):
            await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'pull_request','title':'PR','url':url})
    row=(await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'pull_request','title':'PR','url':'https://example.test/pull/7'}))['result']
    assert row['versionEvidence']=='unversioned_reference' and 'body' not in row
    result=(await app.dispatch('outputs.comment',{'sessionId':sid,'id':row['id'],'body':'Review the failure branch.'},command_id='comment-one'))['result']
    assert result['externalPosted'] is False
    assert (await app.dispatch('outputs.comment',{'sessionId':sid,'id':row['id'],'body':'Review the failure branch.'},command_id='comment-one'))['result']['duplicate']
    assert len((await app.dispatch('outputs.read',{'sessionId':sid,'id':row['id']}))['result']['comments'])==1


@pytest.mark.parametrize('action', ['outputs.list', 'outputs.read', 'outputs.image', 'outputs.images'])
async def test_saved_output_reads_do_not_traverse_conversation_bodies(app, monkeypatch, action):
    from pathlib import Path
    from test_output_images import png
    source = app._session()
    rows = []
    for index in range(2):
        path = Path(source['workspace']) / f'page-{index}.png'
        path.write_bytes(png(width=3 + index))
        rows.append((await app.dispatch('outputs.attach', {
            'sessionId': source['id'], 'kind': 'file', 'title': path.name, 'path': path.name},
            include_state=False))['result'])

    class UnrelatedHistory(list):
        def __iter__(self):
            raise AssertionError('A saved output read traversed unrelated chat history')

        def __deepcopy__(self, memo):
            raise AssertionError('A saved output read copied unrelated chat history')

    original = source['messages']
    source['messages'] = UnrelatedHistory(original)
    def no_hydration(row):
        raise AssertionError('A saved output read hydrated unrelated chat history')
    monkeypatch.setattr(app.cold_display, 'hydrate', no_hydration)
    args = {'sessionId': source['id']}
    if action in {'outputs.read', 'outputs.image'}:
        args['id'] = rows[0]['id']
    if action == 'outputs.image':
        args['sha256'] = rows[0]['sha256']
    if action == 'outputs.images':
        args['images'] = [{'id': row['id'], 'sha256': row['sha256']} for row in rows]
    try:
        receipt = await app.dispatch(action, args, include_state=False)
        assert receipt['accepted']
        result = receipt['result']
        if action == 'outputs.list':
            assert {row['id'] for row in result['items']} == {row['id'] for row in rows}
        elif action == 'outputs.images':
            assert [row['id'] for row in result['images']] == [row['id'] for row in rows]
        else:
            assert result['id'] == rows[0]['id'] and result['sha256'] == rows[0]['sha256']
        assert not app.runtime.sent and not app.runtime.started
    finally:
        source['messages'] = original


@pytest.mark.parametrize('change', ['execution', 'message', 'transfer'])
async def test_output_snapshot_rechecks_authority_after_read(app, monkeypatch, change):
    import amplifier_web.outputs as outputs
    source = app._session()
    mid = source['messages'][0]['id']
    source['executionRevision'] = 7
    fence = ['initial']
    monkeypatch.setattr(app.portability, 'write_context', lambda sid: fence[0])
    async def read_snapshot(*args):
        if change == 'execution':
            source['executionRevision'] += 1
        elif change == 'message':
            source['messages'].clear()
        else:
            fence[0] = 'transferred'
        return {'text': 'Retained diff', 'mode': 'unstaged'}
    monkeypatch.setattr(outputs, 'git_snapshot', read_snapshot)
    with pytest.raises(AppError, match='execution folder changed|original message changed|task moved'):
        await app.dispatch('outputs.review', {'sessionId': source['id'], 'mode': 'unstaged',
                                            'messageId': mid}, include_state=False)
    assert not app.outputs.store.list(source['id'])['items']


async def test_actual_git_diff_exact_lines_and_no_source_mutations(app):
    from pathlib import Path
    root=Path(app._session()['workspace']);sid=app._session()['id']
    def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout
    git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.test')
    file=root/'design.txt';file.write_text('before\n');git('add','design.txt');git('commit','-qm','Initial')
    head=git('rev-parse','HEAD');file.write_text('after\n');(root/'untracked.txt').write_text('Not in review')
    record=(await app.dispatch('outputs.review',{'sessionId':sid,'mode':'unstaged'}))['result']
    read=(await app.dispatch('outputs.read',{'sessionId':sid,'id':record['id']}))['result']
    assert '-before' in read['text'] and '+after' in read['text'] and 'untracked' not in read['text']
    comment={'sessionId':sid,'id':record['id'],'body':'Use a clearer name.','path':'design.txt','line':1,'side':'right'}
    assert (await app.dispatch('outputs.comment',comment))['result']['sha256']==record['sha256']
    with pytest.raises(AppError,match='exact saved diff'):
        await app.dispatch('outputs.comment',{**comment,'line':999})
    with pytest.raises(AppError):await app.dispatch('outputs.review',{'sessionId':sid,'mode':'branch','base':'--help'})
    assert file.read_text()=='after\n' and git('rev-parse','HEAD')==head
    file.write_text('later\n')
    assert '+after' in (await app.dispatch('outputs.read',{'sessionId':sid,'id':record['id']}))['result']['text']


async def test_writing_versions_survive_restart_and_corrupt_content_fails(tmp_path):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    await app.dispatch('session.create',{})
    sid=app._session()['id']
    args={'sessionId':sid,'title':'Email','content':'First draft','variant':'email','subject':'Review'}
    first=(await app.dispatch('outputs.write',args))['result']
    second=(await app.dispatch('outputs.write',{**args,'content':'Revised draft','parentId':first['id'],'evidenceIds':[first['id']]}))['result']
    await app.close()
    restored=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        assert restored.outputs.content(first)==b'First draft' and restored.outputs.content(second)==b'Revised draft'
        path=tmp_path/'app/artifacts'/(first['body']['$resource']+'.json')
        path.write_text('{"encoding":"utf-8","data":"changed"}')
        with pytest.raises(AppError,match='hash'):
            await restored.dispatch('outputs.read',{'sessionId':sid,'id':first['id']})
    finally:await restored.close()


@pytest.mark.parametrize('kind', ['writing', 'canvas'])
async def test_download_requires_auth_and_serves_exact_inert_bytes(authenticated_client,tmp_path,kind):
    from amplifier_web.server import create_app
    app=await create_app(tmp_path/'app',workspace=tmp_path,runtime=Runtime(),voice=False,preload_providers=False,background_updates=False)
    service=app['service'];await service.dispatch('session.create',{})
    expected = b'<script>alert(1)</script>'
    if kind == 'canvas':
        await service.dispatch('canvas.show', {'kind': 'html', 'title': 'Inert', 'content': expected.decode()})
        canvas = service.state['canvasArtifacts'][-1]
        expected = json.dumps(service.state_resource(canvas['body']['$resource']), ensure_ascii=False, sort_keys=True).encode()
        record = (await service.dispatch('outputs.attach', {'sessionId': service._session()['id'],
            'kind': 'canvas', 'title': 'Inert', 'canvasId': canvas['id']}))['result']
    else:
        record=(await service.dispatch('outputs.write',{'sessionId':service._session()['id'],'title':'Inert','content':expected.decode(),'variant':'document'}))['result']
    client=await authenticated_client(app)
    response=await client.get('/api/outputs/'+record['id']+'/content')
    assert response.status==200 and await response.read()==expected
    assert response.headers['Content-Disposition'].startswith('attachment;') and "sandbox" in response.headers['Content-Security-Policy']
    client.session.cookie_jar.clear()
    response=await client.get('/api/outputs/'+record['id']+'/content',headers={'Authorization':''},allow_redirects=False)
    assert response.status in {401,403}


async def test_fork_carries_only_outputs_anchored_to_retained_messages(app):
    sid=app._session()['id'];message=app._session()['messages'][0]['id']
    args={'sessionId':sid,'title':'Anchored','content':'Saved content','variant':'document'}
    anchored=(await app.dispatch('outputs.write',{**args,'messageId':message}))['result']
    await app.dispatch('outputs.write',{**args,'title':'Unanchored'})
    original_count=len(app.outputs.store.list(sid)['items'])
    target={'id':'forked-session','messages':[{'id':message}]}
    app.outputs.fork(sid,target)
    values=app.outputs.store.list(target['id'])['items']
    assert len(values)==1 and values[0]['forkSource']['outputId']==anchored['id']
    assert values[0]['body']==anchored['body'] and len(app.outputs.store.list(sid)['items'])==original_count


async def test_canvas_output_shares_immutable_body_across_edit_retry_fork_and_restart(app):
    from amplifier_web.resource_files import collect, prepare, put
    sid = app._session()['id']
    message = app._session()['messages'][0]['id']
    await app.dispatch('canvas.show', {'kind': 'html', 'title': 'Original', 'content': '<p>Résumé 🌳</p>'})
    canvas = copy.deepcopy(app.state['canvasArtifacts'][-1])
    original = app.state_resource(canvas['body']['$resource'])
    expected = json.dumps(original, ensure_ascii=False, sort_keys=True).encode()
    old_wrapper = prepare({'encoding': 'utf-8', 'data': expected.decode()}).identity
    args = {'sessionId': sid, 'kind': 'canvas', 'title': 'Saved', 'canvasId': canvas['id'], 'messageId': message}
    saved = (await app.dispatch('outputs.attach', args, command_id='saved-canvas'))['result']
    assert saved['body'] == saved['canvasSource'] == canvas['body']
    assert saved['bodyFormat'] == 'canvas-json-v1'
    assert saved['sha256'] == hashlib.sha256(expected).hexdigest()
    assert saved['bytes'] == len(expected)
    assert not app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (old_wrapper,)).fetchone()
    # The resource encoding has different whitespace from the public download.
    assert saved['body']['$resource'] != saved['sha256']
    assert app.outputs.content(saved) == await app.outputs.content_async(saved) == expected
    await app.dispatch('canvas.versions.revise', {'id': canvas['id'], 'expectedRevision': canvas['revision'], 'content': '<p>Changed</p>'})
    assert next(row for row in app.state['canvasArtifacts'] if row['id'] == canvas['id'])['body'] != saved['body']
    await app.dispatch('outputs.unlink', {'sessionId': sid, 'id': saved['id'], 'expectedRevision': 1})
    duplicate = (await app.dispatch('outputs.attach', args, command_id='saved-canvas'))['result']
    assert duplicate == {**saved, 'duplicate': True}
    assert saved['body']['$resource'] not in collect(app.db, app.state)
    linked = (await app.dispatch('outputs.relink', {'sessionId': sid, 'id': saved['id'], 'expectedRevision': 2}))['result']
    app.outputs.fork(sid, {'id': 'canvas-fork', 'messages': [{'id': message}]})
    forked = app.outputs.store.list('canvas-fork')['items'][0]
    assert forked['body'] == saved['body'] and forked['bodyFormat'] == saved['bodyFormat']
    assert app.outputs.content(forked) == expected
    app._save()
    await app.close()
    restored = AppService(app.data_dir, workspace=app.default_workspace, runtime=Runtime())
    try:
        read = (await restored.dispatch('outputs.read', {'sessionId': sid, 'id': saved['id']}))['result']
        assert read['text'].encode() == expected and read['revision'] == linked['revision']
        assert restored.outputs.content(restored.outputs.store.read(forked['id'])) == expected
        legacy = {**saved, 'body': put(restored.db, {'encoding': 'utf-8', 'data': expected.decode()})}
        legacy.pop('bodyFormat')
        assert restored.outputs.content(legacy) == expected
    finally:
        await restored.close()


async def test_canvas_edit_and_real_gc_between_capture_and_commit_keep_original_body(app, monkeypatch):
    from amplifier_web.storage_migration import _maintenance_async
    from amplifier_web.resource_files import put
    sid = app._session()['id']
    await app.dispatch('canvas.show', {'kind': 'html', 'title': 'Before edit', 'content': '<p>Original</p>'})
    canvas = copy.deepcopy(app.state['canvasArtifacts'][-1])
    reference = canvas['body']['$resource']
    expected = json.dumps(app.state_resource(reference), ensure_ascii=False, sort_keys=True).encode()
    captured, resume = asyncio.Event(), asyncio.Event()
    original_lock, original_read = app.lock, app.state_resource
    source_read = False

    def observe_source(identity):
        nonlocal source_read
        value = original_read(identity)
        if identity == reference and asyncio.current_task() is pending:
            source_read = True
        return value

    class InterleavingLock:
        def __getattr__(self, name):
            return getattr(original_lock, name)

        async def __aenter__(self):
            return await original_lock.__aenter__()

        async def __aexit__(self, *args):
            result = await original_lock.__aexit__(*args)
            if asyncio.current_task() is pending and source_read and not captured.is_set():
                captured.set()
                await resume.wait()
            return result

    monkeypatch.setattr(app, 'lock', InterleavingLock())
    monkeypatch.setattr(app, 'state_resource', observe_source)
    pending = asyncio.create_task(app.dispatch('outputs.attach', {'sessionId': sid, 'kind': 'canvas',
        'title': 'Snapshot before edit', 'canvasId': canvas['id']}, command_id='interleaved-canvas'))
    try:
        await asyncio.wait_for(captured.wait(), 10)
        assert not app.outputs.store.list(sid)['items']
        await app.dispatch('canvas.versions.revise', {'id': canvas['id'],
            'expectedRevision': canvas['revision'], 'content': '<p>Revised</p>'})
        current = next(row for row in app.state['canvasArtifacts'] if row['id'] == canvas['id'])
        assert current['body'] != canvas['body']
        assert any(version['body'] == canvas['body'] for version in current['versions'])
        unreferenced = put(app.db, {'unreferenced': 'Collect this fixture body'})
        app.db.commit()
        await _maintenance_async(app)
        assert not app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (unreferenced['$resource'],)).fetchone()
        assert app.state_resource(reference) == json.loads(expected)
    finally:
        resume.set()
        result = await asyncio.wait_for(pending, 10)
    assert result['result']['body'] == canvas['body']
    assert await app.outputs.content_async(result['result']) == expected
