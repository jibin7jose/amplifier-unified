"""The agent and sidebar share ordering, eligibility, pins and scoped pages."""
from amplifier_web.state_records import load as load_saved_state
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import chat_navigation
from amplifier_web.agent_state import overview
from amplifier_web.native_history import NativeHistory
from amplifier_web.service import AppService, AppError
from test_automatic_history import app_factory, native_session, native_rows, files_snapshot, finish_actions
from test_service import Runtime


def state_fixture():
    return {'workspaces':[
        {'id':'one','path':'/projects/one/shared','name':'Personal notes','available':True},
        {'id':'two','path':'/projects/two/shared','name':'shared','available':True},
        {'id':'gone','path':'/projects/gone','name':'gone','available':False},
        {'id':'unknown','path':None,'name':'unknown','available':True}],
        'selectedWorkspaceId':'one','selectedSessionId':None,'view':{'navChatScope':'workspace'},
        'pinnedSessionIds':[], 'sessions':[]}


def chat(identity,workspace='one',recent=1,**fields):
    return {'id':identity,'title':identity,'description':'','workspaceId':workspace,
            'status':'idle','sessionKind':'root','recentActivityAt':recent,**fields}


def ids(page):return [row['id'] for row in page['items']]


@pytest.mark.parametrize('key,values', [
    ('navGroup', ['recent', 'workspace', 'status']),
    ('navAge', ['24h', '7d', '14d', '30d', '90d', '1y', 'all']),
])
def test_catalog_view_preferences_validate_direct_and_nested(key, values):
    for value in values:
        for patch in [{key: value}, {'navRecentView': {key: value}}]:
            assert chat_navigation.view_patch(patch) == patch
    for value in [None, False, 0, 'invalid']:
        with pytest.raises(ValueError):
            chat_navigation.view_patch({key: value})


@pytest.mark.parametrize('days,title', [(1, 'Last 24 hours'), (7, '1–7 days ago'),
    (14, '7–30 days ago'), (30, '7–30 days ago'), (90, '30–90 days ago'),
    (365, '90 days–1 year ago'), (366, 'Over 1 year ago')])
def test_date_groups_are_independent_of_slider_stops(days, title):
    assert chat_navigation.list_group('recent', False, 1_800_000_000-days*86400,
                                     None, None, 'idle', 1_800_000_000)[1] == title


@pytest.mark.parametrize('section', ['search', 'workspace', 'recent', 'shortcuts'])
def test_warm_catalog_pin_changes_rebuild_age_membership_and_group_metadata(monkeypatch, section):
    from amplifier_web.state_projections import StateProjections
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: 1_800_000_000)
    state = state_fixture()
    state['view'].update(navChatScope='all', navAge='24h', navGroup='status')
    state['sessions'] = [chat('fresh', recent=1_800_000_000), chat('expired', recent=1),
                         chat('old-pin', recent=2)]
    state['pinnedSessionIds'] = ['old-pin']
    projections = StateProjections()
    first = projections.chats(state, section=section)
    for pins in [['expired'], [], ['old-pin', 'expired']]:
        state['pinnedSessionIds'] = pins
        projections.pins_changed(state)
        actual = projections.chats(state, section=section)
        assert actual == chat_navigation.snapshot(state, section=section)
        if section in {'search', 'workspace'}:
            assert set(ids(actual)) == {'fresh', *pins}
            assert all(row['group']['id'] == 'pin' for row in actual['items'] if row['pinned'])
        else:
            assert set(ids(actual)) == {'fresh', *pins}
    assert first['items'][0]['pinned'] == (section in {'search', 'workspace'})


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('scope', ['workspace', 'all'])
def test_pick_up_again_accepts_direct_and_nested_filters_without_changing_attention(nested, scope):
    filters = {'navStatusFilter': 'idle'}
    patch = {'navRecentView': filters} if nested else filters
    assert chat_navigation.view_patch(patch) == patch
    state = state_fixture()
    state['view'].update(navChatScope=scope, **filters)
    state['sessions'] = [chat('quiet-one', recent=8), chat('quiet-two', 'two', 7),
                         chat('working', recent=6, status='working'),
                         chat('approval', recent=5, approvals=[{'status': 'pending'}]),
                         chat('unread', recent=4), chat('archived', recent=3)]
    state['attention'] = {'sessions': {'unread': {'kind': 'response'}}}
    state['conversationOrganization'] = {'archived': {'archived': True}}
    before = deepcopy(state)
    page = chat_navigation.snapshot(state, section='search' if scope == 'all' else 'workspace')
    assert ids(page) == (['quiet-one', 'quiet-two'] if scope == 'all' else ['quiet-one'])
    assert page['scope']['statusFilter'] == 'idle'
    assert all(row['activity']['kind'] == 'idle' for row in page['items'])
    assert page['activityCounts'] == {'attention': 1, 'working': 1, 'unread': 1,
                                      'idle': 2 if scope == 'all' else 1}
    assert state == before
    invalid = {'navStatusFilter': 'unsupported'}
    with pytest.raises(ValueError):
        chat_navigation.view_patch({'navRecentView': invalid} if nested else invalid)


def test_shared_session_id_search_keeps_app_keys_and_workspace_scope():
    state = state_fixture()
    shared_id = '11111111-1111-4111-8111-111111111111'
    state['sessions'] = [chat('internal-one', runtimeSessionId=shared_id),
                         chat('internal-two', 'two', nativeIdentity=shared_id),
                         chat('22222222-2222-4222-8222-222222222222')]
    state['selectedSessionId'] = 'internal-one'
    state['pinnedSessionIds'] = ['internal-one']
    state['view'].update(navChatScope='all', navFilter=shared_id[:8])
    before = deepcopy(state)
    page = chat_navigation.snapshot(state)
    assert ids(page) == ['internal-one', 'internal-two']
    assert page['items'][0]['runtimeSessionId'] == shared_id
    assert page['items'][0]['pinned'] is True
    assert state == before
    state['view']['navChatScope'] = 'workspace'
    assert ids(chat_navigation.snapshot(state)) == ['internal-one']
    state['view']['navFilter'] = '22222222'
    assert ids(chat_navigation.snapshot(state)) == ['22222222-2222-4222-8222-222222222222']


def test_all_chats_uses_only_available_roots_and_pins_then_actual_recency():
    state=state_fixture();state['view']['navChatScope']='all'
    state['sessions']=[chat('old-pin',recent=2),chat('newest',recent=90),chat('same-first',recent=50),
        chat('same-second','two',50),chat('new-pin','two',3),chat('gone','gone',1000),
        chat('unknown','unknown',1000),chat('child',recent=1000,sessionKind='worker'),
        chat('internal',recent=1000,sessionKind='internal',status='working'),
        chat('fork','two',4,parentId='newest')]
    state['pinnedSessionIds']=['old-pin','new-pin','child','gone']
    state['sessions'][0]['updatedAt']=99999
    page=chat_navigation.snapshot(state)
    assert ids(page)==['old-pin','new-pin','newest','same-first','same-second','fork']
    assert page['scope']=={'mode':'all','workspaceId':None,'filter':'','selectedSessionId':None,'age':'all'}
    assert page['items'][0]['workspace']=='/projects/one/shared'
    assert page['items'][0]['pinned'] and not page['items'][2]['pinned']
    assert set(page['items'][0])=={'id','title','description','status','workspace','workspaceId','pinned','recentActivityAt','workspaceName','workspaceLabel','activity','runtimeSessionId','createdAt','agentCreated'}
    state['view']['navChatScope']='workspace'
    assert ids(chat_navigation.snapshot(state))==['old-pin','newest','same-first']


def test_path_only_legacy_chat_remains_visible_and_missing_selection_is_empty():
    state=state_fixture()
    state['sessions']=[{'id':'legacy','title':'Legacy','workspace':'/projects/one/shared','createdAt':42}]
    assert ids(chat_navigation.snapshot(state))==['legacy']
    state['selectedWorkspaceId']='gone'
    page=chat_navigation.snapshot(state)
    assert page['total']==0 and page['scope']['workspaceId'] is None
    state['view']['navChatScope']='all'
    assert ids(chat_navigation.snapshot(state))==['legacy']


@pytest.mark.parametrize('query,expected',[
    ('*/two/*',['beta']),('Personal*',['alpha']),('ALPHA',['alpha']),
    ('special description',['beta']),('bet?',['beta']),('[ab]*',['beta','alpha']),
    ('no match',[]),
])
def test_search_matches_names_ids_descriptions_and_full_paths(query,expected):
    state=state_fixture();state['view'].update(navChatScope='all',navFilter=query)
    state['sessions']=[chat('alpha',recent=1,title='Alpha title'),chat('beta','two',2,description='Special description')]
    assert ids(chat_navigation.snapshot(state))==expected


def test_scoped_paging_is_bounded_and_all_starts_with_pins_and_recent():
    state=state_fixture()
    state['sessions']=[chat(str(i),recent=1000-i) for i in range(250)]
    state['selectedSessionId']='220'
    page=chat_navigation.snapshot(state)
    assert (page['index'],page['start'],page['end'],page['total'],page['pages'])==(2,200,250,250,3)
    state['view']['navChatPage']={**page['scope'],'index':1}
    assert len(chat_navigation.snapshot(state)['items'])==100
    state['view']['navChatScope']='all'
    page=chat_navigation.snapshot(state)
    assert page['index']==0 and ids(page)[0]=='0'
    state['view']['navChatPage']={**page['scope'],'index':2}
    assert chat_navigation.snapshot(state)['index']==2
    state['view']['navFilter']='22?'
    page=chat_navigation.snapshot(state)
    assert page['index']==0 and page['total']==10


async def test_pin_preference_persists_without_native_writes_or_runtime_work(tmp_path,app_factory):
    directory=native_session(tmp_path/'native','root')
    app=app_factory();await app.history.refresh()
    native=native_rows(app)[0]
    before=files_snapshot(directory);recent=native['recentActivityAt']
    app.state['view']['navChatPage']={'index':9}
    await app.dispatch('session.pin',{'id':native['id'],'pinned':True})
    await app.dispatch('session.pin',{'id':native['id'],'pinned':True})
    await app.history.refresh()
    assert app.state['pinnedSessionIds']==[native['id']]
    assert 'navChatPage' not in app.state['view']
    assert app._session(native['id'])['recentActivityAt']==recent
    assert before==files_snapshot(directory)
    assert not app.runtime.started and not app.runtime.sent
    assert not any(event[1]['data'].get('action')=='session.pin' for event in app.observed_diagnostics)
    saved=load_saved_state(app.db)
    indexed=next(row for row in saved['sessions'] if row['id']==native['id'])
    assert indexed['$native'] and indexed['recentActivityAt']==recent
    data_dir=app.data_dir;workspace=app.default_workspace
    await app.close()
    restored=app_factory(home=data_dir,workspace=Path(workspace))
    assert restored.state['pinnedSessionIds']==[native['id']]
    assert restored._session(native['id'])['recentActivityAt']==recent
    await restored.dispatch('session.pin',{'id':native['id'],'pinned':False})
    await restored.dispatch('session.pin',{'id':native['id'],'pinned':False})
    assert not restored.state['pinnedSessionIds']
    assert before==files_snapshot(directory)


async def test_pin_rejects_workers_deletion_cleans_preference_and_unpin_is_idempotent(tmp_path,app_factory):
    app=app_factory()
    await app.dispatch('session.create',{'location':{'kind':'managed'}})
    root=app._session()
    worker={**deepcopy(root),'id':'worker','sessionKind':'worker','nativeParentId':root['id']}
    app.state['sessions'].append(worker)
    with pytest.raises(AppError,match='top-level'):
        await app.dispatch('session.pin',{'id':worker['id'],'pinned':True})
    with pytest.raises(AppError):
        await app.dispatch('session.pin',{'id':root['id'],'pinned':'yes'})
    with pytest.raises(AppError):
        await app.dispatch('session.pin',{'id':'','pinned':True})
    await app.dispatch('session.pin',{'id':root['id'],'pinned':True})
    preview=(await app.dispatch('session.deletePreview',{'id':root['id']}))['result']
    await app.dispatch('session.delete',{'id':root['id'],'confirmationToken':preview['confirmationToken']});await finish_actions(app)
    assert root['id'] not in app.state['pinnedSessionIds']
    await app.dispatch('session.pin',{'id':root['id'],'pinned':False})
    assert not app.state['pinnedSessionIds']


@pytest.mark.parametrize('patch',[
    {'navChatScope':'invalid'},{'navChatScope':[]},{'navChatScope':None},
    {'navFilter':['bad']},{'navChatPage':{'index':True}},
    {'navChatPage':{'index':-1,'mode':'all','filter':''}},
])
async def test_invalid_shared_navigation_controls_are_rejected(app_factory,patch):
    app=app_factory()
    with pytest.raises(AppError):await app.dispatch('view.update',{'patch':patch})


async def test_real_conversation_activity_updates_recency_but_navigation_and_lifecycle_do_not(app_factory,monkeypatch):
    app=app_factory();await app.dispatch('session.create',{})
    root=app._session();root['recentActivityAt']=10
    clock=[20];monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:clock[0]))
    await app.dispatch('session.select',{'id':root['id']})
    await app.dispatch('session.rename',{'id':root['id'],'title':'Renamed'})
    await app.dispatch('session.pin',{'id':root['id'],'pinned':True})
    await app.dispatch('view.update',{'patch':{'navChatScope':'all'}})
    for status in ('starting','ready','idle','stopped','interrupted'):
        await app.on_runtime_event('runtime.status',{'sessionId':root['id'],'status':status})
    await app.on_runtime_event('session.naming',{'sessionId':root['id'],'name':'Automatic name'})
    await app.on_runtime_event('execution.event',{'sessionId':root['id'],'id':'name-call','kind':'llm','label':'Session naming','phase':'completed'})
    assert root['recentActivityAt']==10
    await app.dispatch('conversation.send',{'text':'An actual user message'})
    assert root['recentActivityAt']==20
    clock[0]=30
    await app.on_runtime_event('assistant.delta',{'sessionId':root['id'],'text':'Working'})
    assert root['recentActivityAt']==30
    clock[0]=40
    await app.on_runtime_event('runtime.tool',{'sessionId':root['id'],'tool':'read_file','phase':'pre'})
    assert root['recentActivityAt']==40
    clock[0]=50
    await app.on_runtime_event('assistant.message',{'sessionId':root['id'],'text':'Done'})
    assert root['recentActivityAt']==50
    before=root['recentActivityAt'];root['status']='working';app._save()
    data_dir=app.data_dir;workspace=app.default_workspace;await app.close()
    restored=AppService(data_dir,Runtime(),workspace=workspace)
    try:
        assert restored._session()['status']=='interrupted'
        assert restored._session()['recentActivityAt']==before
        agent=overview(restored.get_state(),root['id'])
        assert 'chatNavigation' in agent and agent['session']['pinned']
        assert 'session.pin' in agent['_stateAccess']['chats']
    finally:await restored.close()


async def test_rejected_send_does_not_make_chat_recent(app_factory,monkeypatch):
    from amplifier_web.runtime import SessionInUseError
    app=app_factory();await app.dispatch('session.create',{})
    session=app._session();session['recentActivityAt']=10
    monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:20))
    async def reject(*args,**kwargs):raise SessionInUseError({'pid':123,'host':'fixture'})
    monkeypatch.setattr(app.runtime,'send',reject)
    with pytest.raises(AppError,match='in use'):
        await app.dispatch('conversation.send',{'text':'Not admitted'})
    assert session['recentActivityAt']==10 and not session['messages']


@pytest.mark.parametrize('bounded', [False, True])
async def test_native_recency_uses_transcript_not_rename_and_refresh_keeps_web_activity(tmp_path,app_factory,monkeypatch,bounded):
    directory=native_session(tmp_path/'native','root')
    transcript=directory/'transcript.jsonl'
    os.utime(transcript,(100,100))
    app=app_factory()
    if bounded:
        # A delayed watcher still discovers canonical changes in bounded slices.
        monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, set()))
        app.history.index._recovery_budget=1
    async def refresh():
        for _ in range(16):
            await app.history.refresh()
            assert app.state['sharedHistory']['error'] is None
            if app.state['sharedHistory']['reconciliation']['phase']=='complete':
                return
        pytest.fail('The small native fixture did not finish bounded reconciliation')
    await refresh()
    row=app._session(native_rows(app)[0]['id']);assert row['recentActivityAt']==100
    metadata=json.loads((directory/'metadata.json').read_text())
    metadata.update(name='Renamed externally',updated_at=9999999999)
    (directory/'metadata.json').write_text(json.dumps(metadata))
    await refresh()
    assert row['recentActivityAt']==100
    os.utime(transcript,(200,200));await refresh()
    assert row['recentActivityAt']==200
    row.update(historyManaged=False,recentActivityAt=300)
    await refresh()
    assert row['recentActivityAt']==300


def test_native_recency_falls_back_only_to_explicit_event_or_creation(tmp_path):
    directory=native_session(tmp_path/'native','root',metadata={'turn_count':1,'last_event_at':200,'created_at':100,'created':100,'started_at':100,'updated_at':99999})
    (directory/'transcript.jsonl').unlink()
    index=NativeHistory()
    assert index.scan()['sessions'][0]['recentActivityAt']==200
    metadata=json.loads((directory/'metadata.json').read_text());metadata.pop('last_event_at')
    (directory/'metadata.json').write_text(json.dumps(metadata))
    assert index.scan()['sessions'][0]['recentActivityAt']==200


async def test_voice_responses_without_transcripts_count_as_activity_but_aggregate_updates_do_not(app_factory,monkeypatch):
    app=app_factory();await app.dispatch('session.create',{})
    session=app._session();session['recentActivityAt']=10
    clock=[20];monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:clock[0]))
    await app.set_voice_status({'status':'connecting'})
    await app.record_voice_usage(session['id'],'call','session','fixture',{'input_tokens':0})
    assert session['recentActivityAt']==10
    await app.record_voice_usage(session['id'],'call','response','fixture',{},phase='running')
    assert session['recentActivityAt']==20
    clock[0]=30
    await app.record_voice_usage(session['id'],'call','response','fixture',{'output_tokens':12})
    assert session['recentActivityAt']==30
    clock[0]=40
    await app.record_voice_usage(session['id'],'call','response','fixture',{'output_tokens':12})
    await app.record_voice_usage(session['id'],'call','session','fixture',{'output_tokens':12})
    await app.set_voice_status({'status':'ended'})
    assert session['recentActivityAt']==30


@pytest.mark.parametrize('sort,expected', [('activity',['b','c','a']), ('created',['c','a','b']), ('name',['a','b','c'])])
def test_sort_choices_keep_pin_order(sort, expected):
    state=state_fixture()
    state['sessions']=[chat('p1',recent=1),chat('p2',recent=999),
        chat('a',recent=10,title='Alpha',createdAt=20),
        chat('b',recent=30,title='beta',createdAt=10),chat('c',recent=20,title='Charlie',createdAt=30)]
    state['pinnedSessionIds']=['p1','p2']
    state['view']['navSort']=sort
    assert ids(chat_navigation.snapshot(state))==['p1','p2',*expected]
    with pytest.raises(ValueError):chat_navigation.view_patch({'navSort':'invalid'})


async def test_progress_keeps_order_and_shell_key_until_ready(app_factory,monkeypatch):
    app=app_factory();await app.dispatch('session.create',{})
    root=app._session();root.update(recentActivityAt=10,navigationActivityAt=10)
    clock=[20];monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:clock[0]))
    await app.dispatch('conversation.send',{'text':'Keep working'})
    key=app.browser_state()['shellDataKey']
    for kind,payload in [('assistant.delta',{'text':'Progress'}),
        ('runtime.tool',{'tool':'read_file','phase':'pre'}),
        ('runtime.tool',{'tool':'read_file','phase':'post'}),
        ('assistant.message',{'text':'Still working'}),
        ('runtime.generation',{'event':'generation.finished','text':'Still working','active_job_ids':['worker']})]:
        clock[0]+=10
        await app.on_runtime_event(kind,{'sessionId':root['id'],**payload})
        assert chat_navigation.navigation_activity(root)==20
        assert app.browser_state()['shellDataKey']==key
    clock[0]=100
    await app.on_runtime_event('runtime.status',{'sessionId':root['id'],'status':'idle'})
    assert chat_navigation.navigation_activity(root)==100
    assert app.browser_state()['shellDataKey']!=key
    clock[0]=200
    await app.on_runtime_event('runtime.status',{'sessionId':root['id'],'status':'idle'})
    assert chat_navigation.navigation_activity(root)==100


@pytest.mark.parametrize('kind,payload',[
    ('runtime.error',{'error':'Provider failed'}),
    ('runtime.status',{'status':'stopped'}),
    ('approval.requested',{'id':'permission','tool':'write_file'}),
])
async def test_attention_boundaries_commit_activity(app_factory,monkeypatch,kind,payload):
    app=app_factory();await app.dispatch('session.create',{})
    root=app._session();root.update(recentActivityAt=10,navigationActivityAt=10)
    clock=[20];monkeypatch.setattr(chat_navigation,'time',SimpleNamespace(time=lambda:clock[0]))
    await app.dispatch('conversation.send',{'text':'Work'})
    clock[0]=30
    await app.on_runtime_event(kind,{'sessionId':root['id'],**payload})
    assert chat_navigation.navigation_activity(root)==30


async def test_history_refresh_does_not_move_running_chat(tmp_path,app_factory):
    directory=native_session(tmp_path/'native','root')
    transcript=directory/'transcript.jsonl';os.utime(transcript,(100,100))
    app=app_factory();await app.history.refresh()
    row=app._session(native_rows(app)[0]['id']);row.update(historyManaged=False,status='working')
    os.utime(transcript,(200,200));await app.history.refresh()
    assert row['recentActivityAt']==200
    assert chat_navigation.navigation_activity(row)==100


def test_recent_origin_is_positive_bounded_and_never_ancestry_or_selection_authority():
    state = state_fixture()
    state['view']['navChatScope'] = 'all'
    evidence = {'creatorSessionId': 'creator', 'requestId': 'commission',
                'brief': 'private brief', 'grantId': 'private grant'}
    state['sessions'] = [
        chat('human', recent=10), chat('fork', recent=9, parentId='human'),
        chat('commissioned', recent=8, collaboration=evidence),
        chat('unknown', recent=7, collaboration={'creatorSessionId': 'creator'}),
        chat('worker', recent=99, sessionKind='worker', collaboration=evidence),
        chat('internal', recent=99, sessionKind='internal', collaboration=evidence)]
    before = deepcopy(state)
    recent = chat_navigation.snapshot(state, section='recent')
    assert ids(recent) == ['human', 'fork', 'unknown']
    assert recent['scope']['showAgentCreated'] is False
    assert state == before
    all_chats = chat_navigation.snapshot(state)
    assert ids(all_chats) == ['human', 'fork', 'commissioned', 'unknown']
    assert next(row for row in all_chats['items'] if row['id'] == 'commissioned')['agentCreated'] is True
    assert all('collaboration' not in row and 'brief' not in row and 'grantId' not in row
               for row in all_chats['items'])
    state['selectedSessionId'] = 'commissioned'
    assert ids(chat_navigation.snapshot(state, section='recent')) == ids(all_chats)
    state['pinnedSessionIds'] = ['commissioned']
    assert ids(chat_navigation.snapshot(state, section='pinned')) == ['commissioned']
    assert ids(chat_navigation.snapshot(state, section='recent')) == ids(all_chats)
    state['pinnedSessionIds'] = []
    state['selectedSessionId'] = None
    state['view']['navShowAgentCreated'] = True
    assert ids(chat_navigation.snapshot(state, section='recent')) == ids(all_chats)


@pytest.mark.parametrize('evidence', [None, {}, {'creatorSessionId': '', 'requestId': 'r'},
    {'creatorSessionId': 'c', 'requestId': None}, {'creatorSessionId': 'c', 'requestId': '  '}])
def test_unknown_creation_evidence_stays_visible(evidence):
    state = state_fixture()
    state['sessions'] = [chat('legacy', collaboration=evidence)]
    assert ids(chat_navigation.snapshot(state, section='recent')) == ['legacy']


def test_visibility_and_selection_have_independent_projection_cache_keys():
    from amplifier_web.state_projections import StateProjections
    state = state_fixture()
    state['sessions'] = [chat('agent', collaboration={'creatorSessionId': 'c', 'requestId': 'r'})]
    projections = StateProjections()
    visible = {**state, 'view': {**state['view'], 'navShowAgentCreated': True}}
    current = {**state, 'selectedSessionId': 'agent'}
    assert ids(projections.chats(state, section='recent')) == []
    assert ids(projections.chats(visible, section='recent')) == ['agent']
    assert ids(projections.chats(current, section='recent')) == ['agent']
    assert ids(projections.chats(state, section='recent')) == []
    with pytest.raises(ValueError):
        chat_navigation.view_patch({'navShowAgentCreated': 'true'})


def test_quiet_recent_filters_before_uniform_slicing_counts_and_never_forces_current_rank25():
    state = state_fixture()
    state['view']['navChatScope'] = 'all'
    evidence = {'creatorSessionId': 'creator', 'requestId': 'request', 'brief': 'Never public'}
    state['sessions'] = [chat(f'human-{i}', recent=130-i) for i in range(130)] + [
        chat('current-agent', recent=106.5, collaboration=evidence),
        chat('hidden-agent', recent=150, collaboration=evidence),
        chat('pinned-agent', recent=160, collaboration=evidence),
        chat('worker', recent=999, sessionKind='worker'),
        chat('internal', recent=999, sessionKind='internal')]
    state['selectedSessionId'] = 'current-agent'
    state['pinnedSessionIds'] = ['pinned-agent']
    before = deepcopy(state)
    page = chat_navigation.snapshot(state, section='shortcuts')
    assert ids(page) == [f'human-{i}' for i in range(20)]
    assert (page['total'], page['remaining'], page['end'], page['limit']) == (131, 111, 20, 20)
    assert state == before
    state['view']['navRecentLimit'] = 40
    page = chat_navigation.snapshot(state, section='shortcuts')
    assert ids(page)[24] == 'current-agent'
    assert 'hidden-agent' not in ids(page) and 'pinned-agent' not in ids(page)
    assert (page['total'], page['remaining'], page['end']) == (131, 91, 40)
    assert ids(chat_navigation.snapshot(state, section='pinned')) == ['pinned-agent']
    state['view'].update(navRecentLimit=100, navShowAgentCreated=True)
    page = chat_navigation.snapshot(state, section='shortcuts')
    assert (page['total'], page['remaining'], page['end']) == (133, 33, 100)
    assert ids(page)[:2] == ['pinned-agent', 'hidden-agent']
    assert all('collaboration' not in row for row in page['items'])
    state['sessions'] = state['sessions'][:7]
    page = chat_navigation.snapshot(state, section='shortcuts')
    assert (page['limit'], page['total'], page['end'], page['remaining']) == (100, 7, 7, 0)


@pytest.mark.parametrize('limit', [20, 40, 60, 80, 100, 120, 260])
def test_quiet_recent_limit_has_its_own_cache_and_no_full_browser_page_counter(limit):
    from amplifier_web.state_projections import StateProjections
    state = state_fixture()
    state['sessions'] = [chat(str(i), recent=250-i) for i in range(250)]
    state['view'].update(navChatScope='all', navRecentLimit=limit)
    projections = StateProjections()
    full = projections.chats(state)
    state['view']['navChatPage'] = {**full['scope'], 'index': 2}
    before = deepcopy(state)
    quiet = projections.chats(state, section='shortcuts')
    assert ids(quiet) == [str(i) for i in range(min(limit, 250))]
    assert (quiet['limit'], quiet['remaining']) == (limit, max(0, 250-limit))
    assert projections.chats(state)['index'] == 2
    other = {**state, 'view': {**state['view'], 'navRecentLimit': 20}}
    assert len(projections.chats(other, section='shortcuts')['items']) == 20
    assert len(projections.chats(state, section='shortcuts')['items']) == min(limit, 250)
    assert state == before


@pytest.mark.parametrize('limit', [0, 8, 21, 2**53, True, '20', None])
def test_recent_limit_rejects_unbounded_or_adaptive_saved_values(limit):
    with pytest.raises(ValueError):
        chat_navigation.view_patch({'navRecentLimit': limit})
    state = state_fixture()
    state['sessions'] = [chat(str(i)) for i in range(40)]
    state['view']['navRecentLimit'] = limit
    assert len(chat_navigation.snapshot(state, section='shortcuts')['items']) == 20


def test_recent_requested_steps_keep_scope_limit_at_101_and_catalog_shrink():
    state = state_fixture()
    state['sessions'] = [chat(str(i), recent=200-i) for i in range(101)]
    state['view']['navChatScope'] = 'all'
    for requested in (20, 40, 60, 80, 100, 120):
        assert chat_navigation.view_patch({'navRecentLimit': requested})['navRecentLimit'] == requested
        state['view']['navRecentLimit'] = requested
        page = chat_navigation.snapshot(state, section='shortcuts')
        assert (page['limit'], page['scope']['limit']) == (requested, requested)
        assert (page['end'], page['remaining']) == (min(101, requested), max(0, 101-requested))
        assert len(set(ids(page))) == len(page['items'])
    state['sessions'] = state['sessions'][:6]
    page = chat_navigation.snapshot(state, section='shortcuts')
    assert (page['limit'], page['total'], page['end'], page['remaining']) == (120, 6, 6, 0)


@pytest.mark.parametrize('origin', ['agent', 'peer', 'scheduler', 'user', 'legacy'])
async def test_non_ui_sends_keep_ready_position_even_with_user_role_and_chat_via(app_factory, monkeypatch, origin):
    app = app_factory()
    await app.dispatch('session.create', {})
    root = app._session()
    root.update(recentActivityAt=10, navigationActivityAt=10)
    monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: 20))
    # The real app bridge supplies its root caller outside action arguments.
    await app.dispatch('conversation.send', {'sessionId': root['id'], 'text': 'Input', 'via': 'chat'},
                       origin=origin, command_id='excluded', caller_session_id=root['id'])
    message = root['messages'][-1]
    assert message['role'] == 'user' and message['inputOrigin'] == origin
    assert 'navigationPost' not in message and 'navigationPostAdmissions' not in root
    assert chat_navigation.navigation_activity(root) == 10
    await app.on_runtime_event('runtime.status', {'sessionId': root['id'], 'status': 'idle'})
    assert chat_navigation.navigation_activity(root) == 20


@pytest.mark.parametrize('origin', ['agent', 'peer', 'legacy'])
@pytest.mark.parametrize('caller', ['missing', 'foreign'])
async def test_non_ui_send_authority_controls_use_same_dispatch_seam(app_factory, origin, caller):
    app = app_factory()
    await app.dispatch('session.create', {'title': 'Target'})
    root = app._session()
    await app.dispatch('session.create', {'title': 'Different caller'})
    foreign = app._session()
    before = deepcopy(app.state['sessions'])
    sent = deepcopy(app.runtime.sent)
    reason = 'explicitly' if caller == 'missing' else 'transport-bound root generation'
    with pytest.raises(AppError, match=reason) as exc:
        await app.dispatch('conversation.send', {'sessionId': root['id'], 'text': 'Not authorized'},
                           origin=origin, command_id='refused',
                           caller_session_id=None if caller == 'missing' else foreign['id'])
    assert exc.value.status == 403
    assert app.state['sessions'] == before
    assert app.runtime.sent == sent


async def test_ui_post_does_not_override_pins_explicit_sort_or_scope(app_factory, monkeypatch):
    app = app_factory()
    for title in ('Zebra', 'Alpha', 'Middle'):
        await app.dispatch('session.create', {'title': title})
    # Creation prepends rows; keep the named filter target independent of order.
    by_title = {row['title']: row for row in app.state['sessions']}
    target, pinned, other = (by_title[title] for title in ('Zebra', 'Alpha', 'Middle'))
    for index, root in enumerate(app.state['sessions']):
        root.update(recentActivityAt=10 + index, navigationActivityAt=10 + index, createdAt=10 + index)
    await app.dispatch('session.pin', {'id': pinned['id'], 'pinned': True})
    app.state['view'].update(navChatScope='all', navFilter='')
    def order(sort):
        return ids(chat_navigation.snapshot({**app.state, 'view': {**app.state['view'], 'navSort': sort}}))
    before = {sort: order(sort) for sort in ('name', 'created')}
    selection = app.state['selectedSessionId']
    monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: 50))
    await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'Human post'})
    assert order('activity') == [pinned['id'], target['id'], other['id']]
    assert {sort: order(sort) for sort in before} == before
    assert app.state['pinnedSessionIds'] == [pinned['id']]
    assert app.state['selectedSessionId'] == selection
    app.state['view']['navFilter'] = 'Middle'
    assert order('activity') == [other['id']]
    await app.dispatch('session.archive', {'id': target['id']})
    app.state['view']['navFilter'] = ''
    assert target['id'] not in order('activity')


async def test_http_ui_post_promotes_before_ack_and_preserves_other_client(authenticated_client, tmp_path, monkeypatch):
    import asyncio
    from amplifier_web.server import create_app
    from test_message_delivery import HeldPostRuntime
    runtime = HeldPostRuntime()
    runtime.expect('held-http')
    server = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=runtime,
                              voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(server)
    app = server['service']
    await app.history.close()
    for index in range(24):
        await app.dispatch('session.create', {'title': f'Root {index:02}', 'select': False})
    # All durable roots receive the fixture clock, including evicted owners.
    roots = app.directory.select(limit=100, state=app.state)
    for index, summary in enumerate(roots):
        app._session(summary['id'], hydrate=False).update(recentActivityAt=10 + index, navigationActivityAt=10 + index)
    target = app._session(roots[0]['id'], hydrate=False)
    app.state['view'].update(navChatScope='all')
    primary = app.clients.attach('primary-post')
    primary.update(selectedSessionId=target['id'], selectedWorkspaceId=app.state['selectedWorkspaceId'])
    observer = app.clients.attach('observer-post')
    observer.update(selectedSessionId=app.state['sessions'][1]['id'],
                    selectedWorkspaceId=app.state['selectedWorkspaceId'])
    observer['view'].update(draft='Keep my other draft', navRecentView={'navSort': 'name'})
    observer['attachments'][observer['selectedSessionId']] = [{'id': 'other-reference', 'name': 'other.txt'}]
    app._publish()
    before_observer = {key: deepcopy(observer[key]) for key in
                       ('selectedSessionId', 'view', 'attachments', 'canvas')}
    def shortcuts():
        with app.clients.bind('primary-post'):
            return app.shell.inspect('primary-post', snapshots=True)['snapshots']['chats']['recentShortcuts']
    assert target['id'] not in [row['id'] for row in shortcuts()]
    monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: 100))
    payload = {'id': 'held-http', 'action': 'conversation.send',
               'origin': 'agent',  # HTTP body cannot override the trusted adapter.
               'args': {'sessionId': target['id'], 'text': 'Human HTTP post'}}
    post = asyncio.create_task(client.post('/api/actions', json=payload,
                                          headers={'X-Amplifier-Client': 'primary-post'}))
    try:
        await asyncio.wait_for(runtime.entered['held-http'].wait(), 5)
        assert not post.done()
        assert [row['id'] for row in shortcuts()][0] == target['id']
        assert sum(row['id'] == target['id'] for row in shortcuts()) == 1
        assert chat_navigation.navigation_activity(target) == 100
        assert not any(row['role'] == 'assistant' for row in target['messages'])
        assert target['messages'][-1]['inputOrigin'] == 'ui'
        assert target['messages'][-1]['navigationPost']['disposition'] == 'pending'
        key = app.browser_state()['shellDataKey']
        navigation_before = app.directory.navigation_revision
        duplicate = await client.post('/api/actions', json=payload,
                                      headers={'X-Amplifier-Client': 'primary-post'})
        assert (await duplicate.json())['duplicate']
        assert app.directory.navigation_revision == navigation_before
        assert app.browser_state()['shellDataKey'] == key
        assert len(runtime.sent) == len(target['messages']) == 1
        assert {key: observer[key] for key in before_observer} == before_observer
    finally:
        runtime.release['held-http'].set()
        response = await post
        assert response.status == 200
    assert target['messages'][-1]['navigationPost']['disposition'] == 'accepted'


async def test_actual_scheduler_input_does_not_get_human_post_exception(tmp_path, monkeypatch):
    from test_schedules import fixture, schedule
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        await schedule(app, sid, now[0])
        root = app._session(sid)
        root.update(recentActivityAt=10, navigationActivityAt=10)
        monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: 20))
        now[0] += 61
        await app.schedules.tick()
        message = root['messages'][-1]
        assert message['role'] == 'user' and message['inputOrigin'] == 'scheduler'
        assert len(runtime.inputs) == 1 and 'navigationPost' not in message
        assert chat_navigation.navigation_activity(root) == 10
    finally:
        await app.close()


def boundary_post(root, identity, clock, at):
    """Trusted insertion boundary only; no service, runtime or transcript scan."""
    previous = chat_navigation.prepare_human_post(root)
    clock[0] = at
    message = {'inputId': identity, 'createdAt': at}
    root.setdefault('messages', []).append(message)
    chat_navigation.touch(root)
    chat_navigation.promote_human_post(root, message, previous)
    return message


def boundary_fields(root):
    return {key: deepcopy(root[key]) for key in
            ('navigationActivityAt', 'recentActivityAt', 'navigationActivityPending', 'navigationPostActivity')
            if key in root}


@pytest.mark.parametrize('previous', [
    {},
    {'recentActivityAt': 10},
    {'navigationActivityAt': 10, 'recentActivityAt': 10, 'navigationActivityPending': False},
    {'navigationActivityAt': None, 'recentActivityAt': None, 'navigationActivityPending': None},
    {'navigationActivityAt': 7, 'recentActivityAt': 10, 'navigationActivityPending': True,
     'navigationPostActivity': {'sessionId': 'root', 'inputId': 'prior', 'fence': 'prior-fence', 'activityAt': 7}},
])
@pytest.mark.parametrize('completion_order', [('A', 'B'), ('B', 'A')])
@pytest.mark.parametrize('equal_clocks', [False, True])
def test_boundary_retained_refusals_restore_presence_values_and_fences(
        monkeypatch, previous, completion_order, equal_clocks):
    root = {'id': 'root', 'createdAt': 10, 'messages': [], **deepcopy(previous)}
    before = boundary_fields(root)
    at = chat_navigation.navigation_activity(root)
    clock = [20]
    monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: clock[0]))
    boundary_post(root, 'A', clock, 20)
    boundary_post(root, 'B', clock, 20 if equal_clocks else 30)
    for identity in completion_order:
        message = next(row for row in root['messages'] if row['inputId'] == identity)
        chat_navigation.finish_human_post(root, message, 'rejected')
        # Persistence preserves rejected history and the remaining exact owner.
        root = json.loads(json.dumps(root))
    assert boundary_fields(root) == before
    assert chat_navigation.navigation_activity(root) == at
    assert len(root['messages']) == 2
    assert all(row['navigationPost']['disposition'] == 'rejected' for row in root['messages'])
    assert 'navigationPostAdmissions' not in root


@pytest.mark.parametrize('wrong', ['session', 'input', 'fence'])
def test_boundary_equal_clock_cannot_substitute_another_post_identity(monkeypatch, wrong):
    root = {'id': 'root', 'createdAt': 10, 'navigationActivityAt': 10,
            'recentActivityAt': 10, 'navigationActivityPending': False, 'messages': []}
    clock = [20]
    monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: clock[0]))
    a = boundary_post(root, 'A', clock, 20)
    b = boundary_post(root, 'B', clock, 20)
    forged = deepcopy(b)
    forged['navigationPost'][{'session': 'sessionId', 'input': 'inputId', 'fence': 'fence'}[wrong]] = 'wrong'
    before = deepcopy(root)
    chat_navigation.finish_human_post(root, forged, 'rejected')
    assert root == before
    chat_navigation.finish_human_post(root, a, 'rejected')
    assert root['navigationPostActivity']['fence'] == b['navigationPost']['fence']
    assert root['navigationActivityAt'] == root['recentActivityAt'] == 20
    chat_navigation.finish_human_post(root, b, 'rejected')
    assert root['navigationActivityAt'] == root['recentActivityAt'] == 10
    assert root['navigationActivityPending'] is False


@pytest.mark.parametrize('completion_order', [('A', 'B'), ('B', 'A')])
def test_boundary_progress_between_posts_survives_rollback_without_refused_raw_time(
        monkeypatch, completion_order):
    root = {'id': 'root', 'createdAt': 10, 'navigationActivityAt': 10,
            'recentActivityAt': 10, 'navigationActivityPending': False, 'messages': []}
    clock = [20]
    monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: clock[0]))
    a = boundary_post(root, 'A', clock, 20)
    clock[0] = 25
    chat_navigation.runtime_activity(root, 'assistant.delta', {'text': 'Independent progress'})
    b = boundary_post(root, 'B', clock, 30)
    for identity in completion_order:
        chat_navigation.finish_human_post(root, {'A': a, 'B': b}[identity], 'rejected')
    assert root['navigationActivityAt'] == 10
    assert root['recentActivityAt'] == 25  # Not the refused B's 30 or the baseline's 10.
    assert root['navigationActivityPending'] is True
    clock[0] = 40
    chat_navigation.settle_activity(root)
    assert root['navigationActivityAt'] == 40


def test_boundary_legacy_progress_chain_retains_unknown_raw_ownership(monkeypatch):
    root = {'id': 'root', 'createdAt': 10, 'navigationActivityAt': 10,
            'recentActivityAt': 10, 'navigationActivityPending': False, 'messages': []}
    clock = [20]
    monkeypatch.setattr(chat_navigation, 'time', SimpleNamespace(time=lambda: clock[0]))
    message = boundary_post(root, 'A', clock, 20)
    # An old persisted boolean has no exact raw progress timestamp. Be conservative.
    root['navigationPostAdmissions']['progress'] = True
    chat_navigation.finish_human_post(root, message, 'rejected')
    assert root['navigationActivityAt'] == 10 and root['recentActivityAt'] == 20
    assert root['navigationActivityPending'] is True


def test_search_includes_matching_pins_without_promoting_unrelated_pins():
    state = state_fixture()
    state['view'].update(navChatScope='all', navFilter='portrait')
    state['sessions'] = [chat('portrait-pinned'), chat('unrelated-pin'),
                         chat('portrait-recent'), chat('portrait-worker', sessionKind='worker'),
                         chat('portrait-agent', collaboration={'creatorSessionId':'creator','requestId':'request'})]
    state['pinnedSessionIds'] = ['portrait-pinned', 'unrelated-pin']
    before = deepcopy(state)
    page = chat_navigation.snapshot(state, section='search')
    assert ids(page) == ['portrait-pinned', 'portrait-recent']
    assert page['scope']['section'] == 'search'
    assert page['scope']['showAgentCreated'] is False
    assert state == before
    assert ids(chat_navigation.snapshot(state, section='recent')) == ['portrait-pinned', 'portrait-recent']


@pytest.mark.parametrize('sort', ['activity', 'created', 'name'])
def test_pin_changes_do_not_change_recent_membership_order_or_cached_ties(sort):
    from amplifier_web.state_projections import StateProjections
    state = state_fixture()
    state['view'].update(navChatScope='all', navSort=sort)
    state['sessions'] = [chat('new', recent=20, createdAt=20),
                         chat('old-a', recent=10, createdAt=10, title='Same'),
                         chat('old-b', recent=10, createdAt=10, title='Same')]
    projections = StateProjections()
    expected = ids(projections.chats(state, section='recent'))
    for pins in [['old-b', 'old-a'], ['old-a', 'old-b'], ['old-a'], []]:
        state['pinnedSessionIds'] = pins
        projections.pins_changed(state)
        # Warm both ordering variants; never reuse a pin-first index for Recent.
        assert ids(projections.chats(state, section='pinned')) == pins
        for section in ['recent', 'shortcuts']:
            assert ids(projections.chats(state, section=section)) == expected
            assert ids(chat_navigation.snapshot(state, section=section)) == expected
