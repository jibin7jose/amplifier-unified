"""Directory pages match public navigation without admitting dormant records."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from amplifier_web.history_directory import HistoryDirectory
from amplifier_web import chat_navigation, workspace_navigation
from amplifier_web.history_directory_query import chats, pinned_navigation, workspace_counts


def directory_fixture(tmp_path, count=240):
    state = {'sessions': [], 'workspaces': [
        {'id':'a','name':'Alpha','path':'/projects/alpha','available':True},
        {'id':'b','name':'Beta','path':'/projects/beta','available':True},
        {'id':'gone','name':'Gone','path':'/projects/gone','available':False}],
        'view':{'navChatScope':'all'}, 'selectedSessionId':'s131', 'selectedWorkspaceId':'a',
        'pinnedSessionIds':['s10','s3','s140'], 'pinnedWorkspaceIds':['b'],
        'pinnedItemIds':['session:s10','workspace:b','session:s3','session:s140'],
        'attention':{'sessions':{'s4':True}}, 'attentionRead':{},
        'conversationOrganization':{'archived':{'s5':1,'s6':1},'collections':[]}}
    for i in range(count):
        workspace = 'gone' if i%29==0 else 'b' if i%3==0 else 'a'
        state['sessions'].append({'id':f's{i}','title':f'Chat {i%17:02} / {i}',
            'description':'special' if i%7==0 else '', 'workspaceId':workspace,
            'workspace':f'/projects/{dict(a="alpha",b="beta",gone="gone")[workspace]}',
            'createdAt':i+1,'recentActivityAt':count-i,'runtimeSessionId':f'n{i}',
            'status':'working' if i%23==0 else 'idle','sessionKind':'worker' if i%31==0 else 'root'})
    if count>4:
        state['sessions'][4]['manualUnread']='notice-four'
    db = sqlite3.connect(tmp_path/'app.sqlite3')
    db.execute('PRAGMA journal_mode=WAL')
    from amplifier_web.managed_deletion import initialize
    initialize(db)
    service = SimpleNamespace(db=db,_state=state,data_dir=tmp_path)
    directory = HistoryDirectory(service)
    directory.finish_migration()
    directory.sync(state['sessions'],state)
    # Explicit ordinals preserve original source-array ties through migration.
    db.executemany('UPDATE history_local_rows SET ordinal=? WHERE id=?',
                   [(f'{i:08}',row['id']) for i,row in enumerate(state['sessions'])])
    db.commit()
    return state,directory


@pytest.mark.parametrize('section',['recent','shortcuts'])
@pytest.mark.parametrize('sort',['activity','created','name'])
def test_directory_pin_changes_preserve_recent_membership_and_order(tmp_path, section, sort):
    state, directory = directory_fixture(tmp_path)
    state['view'].update(navSort=sort, navRecentLimit=100)
    from amplifier_web.state_projections import StateProjections
    projections = StateProjections(service=SimpleNamespace(directory=directory))
    first = projections.chats({**state, 'sessions': []}, section=section)
    preserved = deepcopy(first)
    expected = [row['id'] for row in first['items']]
    if sort == 'activity':
        assert 's3' in expected and 's10' in expected
        assert expected.index('s3') < expected.index('s10')
    for pins in [['s140','s3','s10'], ['s10','s3','s140'], ['s10'], []]:
        state['pinnedSessionIds'] = pins
        dormant = {**state, 'sessions': []}
        actual = projections.chats(dormant, section=section)
        assert [row['id'] for row in actual['items']] == expected
        assert actual == chat_navigation.snapshot(state, section=section)
        assert all(row['pinned'] == (row['id'] in pins) for row in actual['items'])
        assert [row['id'] for row in projections.chats(dormant, section='pinned')['items']] == pins
        assert dormant['sessions'] == []
        assert first == preserved


@pytest.mark.parametrize('section',[None,'recent','shortcuts','pinned','search','workspace'])
@pytest.mark.parametrize('view',[
    {}, {'navChatScope':'workspace'}, {'navSort':'name'}, {'navSort':'created'},
    {'navFilter':'special'}, {'navFilter':'*/beta*'}, {'navArchive':'archived'},
    {'navStatusFilter':'working'}, {'navStatusFilter':'unread'},
    {'navGroup':'recent'}, {'navGroup':'workspace'}, {'navGroup':'status'}, {'navAge':'24h'},
])
def test_directory_page_matches_complete_catalog(tmp_path,section,view):
    complete,directory = directory_fixture(tmp_path)
    complete['view'].update(view)
    expected = chat_navigation.snapshot(complete,section=section)
    working = {**complete,'sessions':[]}
    decoded=[]
    original=directory.decode
    directory.decode=lambda row: (decoded.append(row),original(row))[1]
    assert chats(directory,working,section=section)==expected
    assert len(decoded)==len(expected['items'])
    assert working['sessions']==[]


@pytest.mark.parametrize('section', [None, 'recent', 'shortcuts', 'workspace', 'search'])
def test_shared_age_filters_before_counts_page_and_workspace_aggregates(tmp_path, monkeypatch, section):
    now = 1_800_000_000
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: now)
    state, directory = directory_fixture(tmp_path)
    state['view']['navAge'] = '24h'
    for row in state['sessions']:
        row['navigationActivityAt'] = now-90000
    for row in state['sessions'][100:180]:
        row['navigationActivityAt'] = now-86400
    directory.sync(state['sessions'], state)
    dormant = {**state, 'sessions': []}
    actual = chats(directory, dormant, section=section)
    expected = chat_navigation.snapshot(state, section=section)
    assert actual == expected and actual['scope']['age'] == '24h'
    assert 50 < actual['total'] < 100
    if section is not None:
        assert actual['total'] > len(actual['items'])
    expected_workspaces = workspace_navigation._index(state)
    aggregates = workspace_counts(directory, dormant)
    assert workspace_navigation._index(dormant, aggregates=aggregates) == expected_workspaces
    assert sum(row['chatCount'] for row in aggregates.values()) == actual['total']
    state['pinnedSessionIds'] = []
    dormant['pinnedSessionIds'] = []
    assert chats(directory, dormant, section=section)['total'] == actual['total']-2


@pytest.mark.parametrize('group', ['recent', 'workspace', 'status'])
@pytest.mark.parametrize('age', ['24h', '7d', '30d', '90d', '1y', 'all'])
def test_group_and_age_query_filters_complete_catalog_before_paging(tmp_path, monkeypatch, group, age):
    now = 1_800_000_000
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: now)
    state, directory = directory_fixture(tmp_path, 240)
    state['view'].update(navGroup=group, navAge=age, navSort='name')
    # All 240 summaries are persisted; the query has no resident sessions.
    for i, row in enumerate(state['sessions']):
        at = now-((i % 6)*80_000)
        row.update(recentActivityAt=now, navigationActivityAt=at)
    state['sessions'][1].update(navigationActivityAt=now-86400)  # inclusive boundary
    state['sessions'][2].update(navigationActivityAt=now-86401)
    state['sessions'][7].update(navigationActivityAt=0, recentActivityAt=0, createdAt=0)
    boundaries = {}
    for position, (key, seconds) in enumerate(chat_navigation.AGE_SECONDS.items()):
        for delta in (-1, 0, 1):
            row = state['sessions'][40+position*3+delta+1]
            row.update(navigationActivityAt=now-seconds+delta)
            boundaries[(key, delta)] = row['id']
    state['sessions'][8].update(navigationActivityAt=now-366*86400)
    for position, invalid in enumerate([-1, float('inf'), float('nan')], 9):
        state['sessions'][position].update(navigationActivityAt=invalid, recentActivityAt=0, createdAt=0)
    directory.sync(state['sessions'], state)
    dormant = {**state, 'sessions': []}
    decoded = []
    original = directory.decode
    directory.decode = lambda row: (decoded.append(row['id']), original(row))[1]
    expected = chat_navigation.snapshot(state, section='search')
    actual = chats(directory, dormant, section='search')
    assert actual == expected
    assert len(decoded) == len(actual['items']) <= 50
    assert actual['items'][:3] == expected['items'][:3]
    collected = []
    for index in range(actual['pages']):
        state['view']['navChatPage'] = {**actual['scope'], 'index': index}
        page = chats(directory, dormant, section='search')
        assert page == chat_navigation.snapshot(state, section='search')
        collected.extend(page['items'])
    identities = [row['id'] for row in collected]
    assert len(identities) == len(set(identities)) == actual['total']
    assert identities[:3] == ['s10', 's3', 's140']  # age never hides/reorders pins
    assert 's1' in identities
    assert ('s2' in identities) == (age != '24h')
    assert ('s7' in identities) == (age == 'all')
    assert ('s8' in identities) == (age == 'all')
    # Invalid navigation dates fall back to the known zero/unknown date.
    for identity in ['s9', 's11']:
        assert (identity in identities) == (age == 'all')
    for (key, delta), identity in boundaries.items():
        seconds = chat_navigation.AGE_SECONDS[key]
        expected = age == 'all' or now-seconds+delta >= now-chat_navigation.AGE_SECONDS[age]
        assert (identity in identities) == expected, (age, key, delta)
    # Every group is contiguous across the entire catalog, not just each page.
    groups = [row['group']['id'] for row in collected]
    runs = [value for index, value in enumerate(groups) if not index or value != groups[index-1]]
    assert len(runs) == len(set(runs))
    assert dormant['sessions'] == []
    state['view'].update(navFilter='special', navStatusFilter='working')
    assert chats(directory, dormant, section='search') == chat_navigation.snapshot(state, section='search')


def test_group_and_age_projection_invalidates_changed_preferences_and_clock(tmp_path, monkeypatch):
    from amplifier_web.state_projections import StateProjections
    now = [1_800_000_000]
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: now[0])
    state, directory = directory_fixture(tmp_path, 20)
    state['sessions'][1].update(navigationActivityAt=now[0]-86400, recentActivityAt=now[0]-86400)
    directory.sync(state['sessions'], state)
    dormant = {**state, 'sessions': []}
    projections = StateProjections(service=SimpleNamespace(directory=directory))
    first = projections.chats(dormant, section='search')
    assert projections.chats(dormant, section='search') is first
    state['view'].update(navAge='24h', navGroup='status')
    filtered = projections.chats(dormant, section='search')
    assert filtered is not first and 's1' in [row['id'] for row in filtered['items']]
    now[0] += 60
    expired = projections.chats(dormant, section='search')
    assert 's1' not in [row['id'] for row in expired['items']]
    assert expired is projections.chats(dormant, section='search')


def test_query_uses_one_clock_for_order_and_decoration_across_minute(tmp_path, monkeypatch):
    now = 1_800_000_000
    state, directory = directory_fixture(tmp_path, 4)
    state['pinnedSessionIds'] = []
    state['view'].update(navGroup='recent', navSort='name')
    for index, row in enumerate(state['sessions'][1:]):
        row.update(title=chr(65+index), recentActivityAt=now,
                   navigationActivityAt=now-86400+(-1 if index == 2 else 0 if index == 0 else 60))
    directory.sync(state['sessions'], state)
    clock = iter([now, now+60])
    monkeypatch.setattr('amplifier_web.history_directory_query.list_clock', lambda: next(clock))
    page = chats(directory, {**state, 'sessions': []}, section='search')
    assert [row['title'] for row in page['items']] == ['A', 'B', 'C']
    assert [row['group']['id'] for row in page['items']] == ['date:0', 'date:0', 'date:1']


def test_directory_workspace_and_mixed_pins_match(tmp_path):
    complete,directory=directory_fixture(tmp_path)
    working={**complete,'sessions':[]}
    expected=workspace_navigation._index(complete)
    actual=workspace_navigation._index(working,aggregates=workspace_counts(directory,working))
    assert actual==expected
    scoped={**complete,'view':{**complete['view'],'navWorkspaceMode':'recent','navWorkspacePage':1}}
    workspaces=workspace_navigation.snapshot(scoped,index=expected,pinned_only=True)['rows']
    from amplifier_web.pinned_navigation import snapshot
    expected_pins=snapshot(complete,chat_navigation.catalog(complete)[0],workspaces)
    assert pinned_navigation(directory,working,workspaces)==expected_pins


@pytest.mark.parametrize('workspace_archive', ['active', 'archived', 'all'])
def test_workspace_counts_archive_and_restore_without_decoding_history(tmp_path, workspace_archive):
    complete, directory = directory_fixture(tmp_path, 20)
    complete['workspaces'][0]['archived'] = workspace_archive == 'archived'
    complete['view'].update(navWorkspaceMode='recent', navWorkspaceArchive=workspace_archive,
                            navArchive='all')
    complete['conversationOrganization']['archived'] = {'s4': 1}
    working = {**complete, 'sessions': []}
    directory.decode = lambda row: pytest.fail('Workspace aggregates must not decode chat history')
    first = workspace_counts(directory, working, archive=workspace_archive)['/projects/alpha']
    assert first['chatCount'] == 12
    assert first['unread'] == 0
    assert workspace_navigation._index(working, aggregates=workspace_counts(
        directory, working, archive=workspace_archive)) == workspace_navigation._index(complete)
    complete['conversationOrganization']['archived'].clear()
    restored = workspace_counts(directory, working, archive=workspace_archive)['/projects/alpha']
    assert restored['chatCount'] == 13 and restored['unread'] == 1
    assert working['sessions'] == []


def test_warm_workspace_projection_observes_archive_and_restore(tmp_path):
    from amplifier_web.state_projections import StateProjections
    complete, directory = directory_fixture(tmp_path, 20)
    complete['view']['navWorkspaceMode'] = 'recent'
    working = {**complete, 'sessions': []}
    directory.service._state = working
    projections = StateProjections(service=SimpleNamespace(directory=directory))
    directory.decode = lambda row: pytest.fail('Workspace count must not hydrate a chat')
    first = projections.workspaces(working)
    preserved = deepcopy(first)
    count = lambda page: next(row['chatCount'] for row in page['rows'] if row['workspaceId'] == 'a')
    for archive, expected in [({'s5': 1, 's6': 1, 's1': 1}, 11), ({'s5': 1, 's6': 1}, 12)]:
        working['conversationOrganization']['archived'] = archive
        current = projections.workspaces(working)
        assert count(current) == expected
        assert current is projections.workspaces(working)
        assert first == preserved and count(first) == 12


def test_large_directory_does_not_decode_outside_page(tmp_path):
    complete,directory=directory_fixture(tmp_path,27000)
    working={**complete,'sessions':[]}
    expected=chat_navigation.snapshot(complete,section='shortcuts')
    page_ids=[row['id'] for row in expected['items']]
    # Cold metadata outside the page cannot be consulted by a count/filter.
    directory.db.execute('UPDATE history_local_rows SET summary=? WHERE id NOT IN (SELECT value FROM json_each(?))',
                         ('not JSON',json.dumps(page_ids)))
    assert chats(directory,working,section='shortcuts')==expected
    assert working['sessions']==[]


def test_browser_keeps_unmaterialized_page_and_global_count(tmp_path):
    complete,directory=directory_fixture(tmp_path)
    working={**complete,'sessions':[]}
    from amplifier_web.browser_state import SessionIndex,navigation,snapshot
    derived=navigation(working,chats=lambda state:directory.chats(state),directory=directory)
    result=snapshot(working,derived,index=SessionIndex(working,directory=directory),client_id='client')
    visible={row['id'] for row in derived['chatNavigation']['items']}
    assert visible <= {row['id'] for row in result['sessions']}
    assert result['library']['sessionCount']==sum(row['sessionKind']=='root' for row in complete['sessions'])
    assert working['sessions']==[]


def test_directory_observation_snapshot_is_bounded_and_survives_writes(tmp_path):
    complete,directory=directory_fixture(tmp_path,400)
    from amplifier_web.client_observations import DirectoryFacts,Observations
    owner=SimpleNamespace(row_facts=Observations.row_facts)
    owner.row_facts=lambda row,read,pins:Observations.row_facts(owner,row,read,pins)
    reader=directory.read_snapshot({**complete,'sessions':[]})
    facts=DirectoryFacts(owner,reader,reader._frozen_state)
    first=facts[0]
    directory.db.execute("UPDATE history_local_rows SET title='Changed' WHERE id='s100'")
    directory.db.commit()
    assert facts[100][0]['title']==complete['sessions'][100]['title']
    assert facts[0]==first
    assert len(facts.block)<=64
    counts=facts.activity_counts()
    assert counts['roots']['executionCount']>0
    facts.close()
    assert reader._closed


def test_worker_pages_keep_scoped_parent_aliases(tmp_path):
    complete,directory=directory_fixture(tmp_path,1)
    parent={'id':'root','nativeIdentity':'shared','nativeProject':'project-a','workspace':'/projects/alpha','sessionKind':'root'}
    children=[{'id':f'child{i}','title':f'Child {i}','sessionKind':'worker',
               'nativeParentId':'shared','nativeProject':'project-a' if i<120 else 'project-b',
               'workspace':'/projects/alpha','recentActivityAt':i} for i in range(125)]
    directory.sync([parent,*children],complete)
    working={**complete,'sessions':[]}
    page=directory.children(working,parent,'',2)
    assert page['total']==120 and len(page['items'])==20
    assert all(row['nativeProject']=='project-a' for row in page['items'])
    assert directory.children(working,parent,'Child 11?',0)['total']==10
    assert working['sessions']==[]


def test_archived_count_includes_dormant_roots_not_workers(tmp_path):
    complete,directory=directory_fixture(tmp_path)
    from amplifier_web.conversation_library import projection
    complete['conversationOrganization']['archived'].update(s0=1,unknown=1)
    expected=projection(complete)
    assert projection({**complete,'sessions':[]},directory=directory)==expected


def test_windows_workspace_path_identity_parity(tmp_path):
    complete,directory=directory_fixture(tmp_path,0)
    complete['workspaces']=[{'id':'first','path':'C:\\Code\\App','available':True},
                            {'id':'second','path':'c:/code/app','available':True}]
    complete['sessions']=[{'id':'path-only','workspace':'C:/CODE/App','createdAt':1}]
    directory.sync(complete['sessions'],complete)
    working={**complete,'sessions':[]}
    assert workspace_navigation._index(working,aggregates=workspace_counts(directory,working))==workspace_navigation._index(complete)


def test_observation_filtered_query_and_activity_skip_idle_metadata(tmp_path):
    complete,directory=directory_fixture(tmp_path,27000)
    from amplifier_web.client_observations import DirectoryFacts,Observations
    owner=SimpleNamespace()
    owner.row_facts=lambda row,read,pins:Observations.row_facts(owner,row,read,pins)
    reader=directory.read_snapshot({**complete,'sessions':[]})
    original=reader.decode
    decoded=[]
    reader.decode=lambda row:(decoded.append(row),original(row))[1]
    facts=DirectoryFacts(owner,reader,reader._frozen_state)
    counts=facts.activity_counts()
    assert counts['roots']['executionCount']>0 and not decoded
    rows=facts.for_query({'collection':'sessions','scope':'roots','activity':'idle','pinned':'true','workspace':None})
    assert {rows[i][0]['id'] for i in range(len(rows))}==set(complete['pinnedSessionIds'])
    assert len(decoded)==3
    facts.close()


def observation_fixture(tmp_path,count=240):
    import contextvars
    from amplifier_web.client_observations import Observations
    complete,directory=directory_fixture(tmp_path,count)
    state={**complete,'sessions':[]}
    directory.service._state=state
    service=SimpleNamespace(_state=state,directory=directory,instance_id='host',
        projections=SimpleNamespace(),clients=SimpleNamespace(current=contextvars.ContextVar('client',default='one')))
    return complete,directory,Observations(service)


def test_observation_terminal_cursor_releases_reader_and_keeps_retry(tmp_path):
    complete,directory,observations=observation_fixture(tmp_path)
    first=observations.page('sessions',{'limit':'100'})
    reader=observations.snapshots[first['revision']]['rows'].directory
    assert first['nextCursor'] and not reader._closed
    directory.db.execute("UPDATE history_local_rows SET title='After cursor' WHERE id='s200'")
    directory.db.commit()
    second=observations.page('sessions',{'limit':'100','cursor':first['nextCursor']})
    final_token=second['nextCursor']
    final=observations.page('sessions',{'limit':'100','cursor':final_token})
    assert final['nextCursor'] is None and reader._closed
    assert next(row for row in final['items'] if row['id']=='s239')['title']==complete['sessions'][239]['title']
    assert observations.page('sessions',{'limit':'100','cursor':final_token})==final
    assert not observations.snapshots
    observations.close()


def test_expired_current_observation_snapshot_releases_reader(tmp_path,monkeypatch):
    complete,directory,observations=observation_fixture(tmp_path)
    first=observations.page('sessions',{'limit':'1'})
    reader=observations.snapshots[first['revision']]['rows'].directory
    import amplifier_web.client_observations as module
    now=module.time.monotonic()
    monkeypatch.setattr(module.time,'monotonic',lambda:now+module.SNAPSHOT_SECONDS+1)
    observations.reclaim()
    assert reader._closed and not observations.snapshots and not observations.leases
    observations.close()


def test_directory_observation_generations_ignore_admission_and_private_fields(tmp_path):
    complete,directory,observations=observation_fixture(tmp_path,20)
    baseline={key:observations.page(key,{'limit':'100'})['revision'] for key in ('sessions','attention')}
    activity=observations.activity()['revision']
    row=directory.resolve('s4')
    observations.service._state['sessions']=[row]
    observations.invalidate(session_ids={'s4'})
    assert observations.page('sessions',{'limit':'100'})['revision']==baseline['sessions']
    assert observations.page('attention',{'limit':'100'})['revision']==baseline['attention']
    assert observations.activity()['revision']==activity
    row['messages']=[{'text':'PRIVATE TRANSCRIPT'}]
    row['description']='Navigation-only description'
    observations.invalidate(session_ids={'s4'},detail_only=True)
    assert observations.page('sessions',{'limit':'100'})['revision']==baseline['sessions']
    observations.service._state['sessions']=[]
    observations.invalidate()
    assert observations.page('sessions',{'limit':'100'})['revision']==baseline['sessions']
    observations.close()


def test_dormant_pin_and_read_receipt_have_independent_generations(tmp_path):
    complete,directory,observations=observation_fixture(tmp_path,20)
    before=observations.page('sessions',{'limit':'100'})
    notice=observations.page('attention',{'limit':'100'})
    activity=observations.activity()
    observations.service._state['pinnedSessionIds']=[]
    observations.invalidate(session_ids=set())
    assert observations.page('sessions',{'limit':'100'})['revision']!=before['revision']
    assert observations.page('attention',{'limit':'100'})['revision']==notice['revision']
    assert observations.activity()['revision']==activity['revision']
    session_revision=observations.page('sessions',{'limit':'100'})['revision']
    item=notice['items'][0]
    observations.service._state['attentionRead']={item['id']:item['fingerprint']}
    observations.invalidate(session_ids=set())
    assert observations.page('sessions',{'limit':'100'})['revision']==session_revision
    assert observations.page('attention',{'limit':'100'})['revision']!=notice['revision']
    assert observations.activity()['attentionCount']==0
    assert observations.service._state['sessions']==[]
    observations.close()


def test_new_reader_revision_matches_commit_racing_snapshot_open(tmp_path,monkeypatch):
    complete,directory,observations=observation_fixture(tmp_path,20)
    observations.refresh()
    sampled=observations.revision_for('sessions')
    original=directory.read_snapshot
    def raced(state):
        row=directory.resolve('s1')
        row['title']='Committed between revision and reader'
        directory.sync([row],state)
        directory.db.commit()
        return original(state)
    monkeypatch.setattr(directory,'read_snapshot',raced)
    page=observations.page('sessions',{'limit':'1'})
    assert page['revision']!=sampled
    assert page['items'][0]['title']=='Committed between revision and reader'
    from amplifier_web.history_directory_observations import revision
    pinned=observations.snapshots[page['revision']]['rows'].directory
    assert page['revision']==observations.directory_key('sessions',revision(pinned,pinned._frozen_state))
    observations.close()


async def test_legacy_http_session_list_pages_dormant_metadata(tmp_path,aiohttp_client):
    import contextvars
    from aiohttp import web
    from amplifier_web.live_clients import setup_routes
    complete,directory=directory_fixture(tmp_path,240)
    working={**complete,'sessions':[]}
    service=SimpleNamespace(state=working,directory=directory,
        clients=SimpleNamespace(current=contextvars.ContextVar('client',default='generic')),
        _session=lambda *a,**k:pytest.fail('Summary list admitted a session'))
    app=web.Application()
    app['service']=service
    setup_routes(app,set())
    client=await aiohttp_client(app)
    response=await client.get('/api/sessions',params={'workspace':'/projects/alpha','offset':'100','limit':'3'})
    assert response.status==200
    result=await response.json()
    expected=[row for row in complete['sessions'] if row['workspace']=='/projects/alpha'][100:103]
    assert [row['id'] for row in result['items']]==[row['id'] for row in expected]
    assert result['nextOffset']==103
    assert working['sessions']==[]


def test_shell_key_tracks_durable_navigation_not_resident_membership(tmp_path):
    from amplifier_web.state_projections import StateProjections
    complete,directory,observations=observation_fixture(tmp_path,20)
    service=observations.service
    projections=StateProjections(service=service)
    before=projections.shell_key(service._state)
    service._state['sessions']=[directory.resolve('s4')]
    projections.invalidate(state=service._state)
    assert projections.shell_key(service._state)==before
    service._state['sessions']=[]
    projections.invalidate(state=service._state)
    assert projections.shell_key(service._state)==before
    observations.close()


def test_observation_streams_bounded_blocks_and_restarts_backward_reads(tmp_path):
    from amplifier_web.client_observations import DirectoryFacts
    complete,directory,observations=observation_fixture(tmp_path,240)
    reader=directory.read_snapshot(observations.service._state)
    facts=DirectoryFacts(observations,reader,reader._frozen_state)
    statements=[]
    reader.db.set_trace_callback(lambda sql: statements.append(sql) if 'ORDER BY ordinal,id LIMIT -1 OFFSET' in sql else None)
    assert facts[0][0]['id']=='s0'
    assert facts[64][0]['id']=='s64'
    assert facts[128][0]['id']=='s128'
    assert len(statements)==1 and len(facts.block)==64
    assert facts[0][0]['id']=='s0'
    assert facts[64][0]['id']=='s64'
    assert len(statements)==2 and len(facts.block)==64
    cursor=facts.cursor
    facts.close()
    assert reader._closed and facts.cursor is None and facts.block==[]
    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchone()
    observations.close()
