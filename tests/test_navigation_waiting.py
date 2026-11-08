"""Exact saved waiting relationships are presentation, never continuation."""
from copy import deepcopy
import json
import sqlite3

import pytest

from amplifier_web.navigation_waiting import initialize, input_boundary, project, wait_epoch
from amplifier_web.navigation_summary import activity
from test_collaborative_workspaces import app


def task():
    return {'id': 'objective', 'revision': 2, 'continuationEpoch': 2,
            'dependencyWaitEpoch': 1, 'status': 'blocked',
            'blockProvenance': {'version': 1, 'origin': 'agent', 'dependency': True, 'revision': 2}}


def session():
    return {'id': 'manager', 'status': 'idle', 'interruptionRevision': 0,
            'executionRevision': 3, 'collaborationGeneration': {'id': 'generation', 'inputIds': ['input']}}


def receipt(sid='worker', **changes):
    return {'commandAction': 'coordination.send', 'protocol': 2, 'senderSessionId': 'manager',
            'sourceGenerationId': 'generation', 'sourceInputIds': ['input'],
            'target': {'sessionId': sid, 'title': 'Worker ' + sid}, 'delivery': 'applied',
            'subscription': {'notificationVersion': 2, 'supported': True, 'status': 'waiting',
                             'open': True, 'taskId': 'objective', 'taskContinuationEpoch': 1,
                             'interruptionRevision': 0},
            **changes}


@pytest.fixture
def db():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY, receipt TEXT)')
    initialize(db)
    yield db
    db.close()


def put(db, row, identity='request'):
    db.execute('INSERT OR REPLACE INTO commands VALUES(?,?)', (identity, json.dumps(row)))


def test_saved_preblock_wait_and_many_not_arbitrary_child(db):
    before = deepcopy(task())
    assert project(db, session(), before) is None
    put(db, receipt())
    value = project(db, session(), before)
    assert value['targets'] == [{'sessionId': 'worker', 'title': 'Worker worker'}]
    assert activity(session(), task=before, db=db)['kind'] == 'waiting'
    put(db, receipt('second'), 'two')
    assert len(project(db, session(), before)['targets']) == 2
    assert before == task()


@pytest.mark.parametrize('field,value', [
    ('senderSessionId','other'), ('protocol',1), ('delivery','unknown'),
    ('delivery','rejected'), ('delivery','expired'), ('delivery','submitting'),
    ('sourceGenerationId',None), ('sourceInputIds',[]), ('cancellation',{'requested':True}),
    ('response',{'status':'staged','outcome':'success'}),
    ('response',{'status':'qualified','outcome':'deferred'}),
    ('response',{'status':'qualified','outcome':'failed'}),
    ('terminalOutcome',{'outcome':'unknown'}),
])
def test_inexact_settled_or_unknown_requests_are_not_live_waits(db, field, value):
    put(db, receipt(**{field:value}))
    assert project(db, session(), task()) is None


@pytest.mark.parametrize('field,value', [
    ('taskId','old-objective'), ('taskContinuationEpoch',0), ('interruptionRevision',1),
    ('status','claimed'), ('status','suppressed'), ('open',False), ('supported',False),
    ('notificationVersion',1),
])
def test_old_or_missing_subscription_never_binds(db, field, value):
    row = receipt()
    row['subscription'][field] = value
    put(db, row)
    assert project(db, session(), task()) is None


@pytest.mark.parametrize('patch', [
    {'interruptionRevision':1},
    {'collaborationGeneration':{'id':'new', 'inputIds':['input']}},
    {'collaborationGeneration':{'id':'generation','inputIds':['input','new-input']}},
    {'status':'stopped'}, {'status':'error'}, {'status':'interrupted'},
])
def test_new_input_generation_or_stop_never_resurrects_targets(db, patch):
    put(db, receipt())
    assert project(db, {**session(), **patch}, task()) is None


def test_attention_and_execution_precedence(db):
    put(db, receipt())
    for patch, expected in [
        ({'status':'working'}, 'working'), ({'approvals':[{'status':'pending'}]}, 'attention'),
        ({'questions':[{'status':'pending','required':True}]}, 'attention'),
        ({'status':'error'}, 'attention'), ({'status':'stopped'}, 'paused')]:
        assert activity({**session(), **patch}, task=task(), db=db)['kind'] == expected
    assert activity(session(), task={**task(),'status':'paused'}, db=db)['kind'] == 'paused'
    assert activity(session(), task={**task(),'blockProvenance':None}, db=db)['kind'] == 'attention'


def test_projection_bound_is_independent_of_retained_receipts(db):
    put(db, receipt())
    # Same sender's settled historical requests and unrelated pending senders.
    db.executemany('INSERT INTO commands VALUES(?,?)', [
        (str(i),json.dumps(receipt(delivery='rejected', text='private body'*1000))) for i in range(2000)])
    steps = 0
    def count():
        nonlocal steps
        steps += 1
        return 0
    db.set_progress_handler(count, 1)
    value = project(db, session(), task())
    db.set_progress_handler(None, 0)
    assert steps < 300, steps
    assert 'private body' not in json.dumps(value)
    for i in range(40):
        put(db, receipt(str(i)), 'pending'+str(i))
    value = project(db, session(), task())
    assert len(value['targets']) == 16 and value['hasMore']


def test_target_updates_only_visit_indexed_pending_dependencies(db):
    from amplifier_web.navigation_waiting import affected_sources
    put(db,receipt())
    put(db,receipt('other'), 'unrelated')
    put(db,receipt(delivery='rejected',senderSessionId='past'), 'past')
    assert affected_sources(db,'worker') == {'manager'}
    assert affected_sources(db,'missing') == set()


def test_pending_query_uses_covering_expressions_not_large_receipt_bodies(db):
    put(db, receipt(text='large private request body'*100000))
    statements=[]
    db.set_trace_callback(statements.append)
    assert project(db,session(),task())
    db.set_trace_callback(None)
    query=next(sql for sql in statements if 'FROM navigation_pending_requests' in sql)
    operations=db.execute('EXPLAIN '+query).fetchall()
    print('SQLITE',sqlite3.sqlite_version,'PLAN',json.dumps(operations))
    # Expression-index reads must not invoke JSON functions over retained text.
    assert not any(row[1] in {'Function','PureFunc'} and 'json_extract' in str(row[5]) for row in operations), operations
    assert all('FROM commands' not in sql for sql in statements)


def test_wait_epoch_uses_typed_contiguous_changes_and_stops_at_active_boundary():
    value = {**task(), 'changes': [
        {'revision':1,'origin':'agent','fields':{'status':'active','objective':'Goal'}},
        {'revision':2,'origin':'agent','fields':{'status':'blocked'}}]}
    assert wait_epoch(value) == 1
    value.update(revision=5,continuationEpoch=5, changes=[
        *value['changes'],
        {'revision':3,'origin':'ui','fields':{'status':'paused'}},
        {'revision':4,'origin':'ui','fields':{'status':'active'}},
        {'revision':5,'origin':'agent','fields':{'status':'blocked'}}])
    assert wait_epoch(value) == 4
    value['changes'] = value['changes'][:-2] + value['changes'][-1:]
    assert wait_epoch(value) is None
    value = {**task(), 'revision':3, 'changes': [
        {'revision':1,'origin':'agent','fields':{'status':'active','objective':'Goal'}},
        {'revision':3,'origin':'agent','fields':{'status':'blocked'}}],
        'corrections':[{'revision':2,'origin':'agent','text':'Retained correction'}]}
    assert wait_epoch(value) == 1
    value['corrections'] = []
    assert wait_epoch(value) is None


async def test_native_projection_retains_preblock_epoch_without_mutating_authority(tmp_path, monkeypatch):
    from test_task_continuity import controls, create
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    control = controls('navigation-wait')
    await create(control)
    await control.perform('task.block', {'commandId':'block','expectedRevision':1,
                          'origin':'agent','reason':'Any prose is irrelevant'})
    before = deepcopy(control.tasks.record())
    result = control.tasks.snapshot()
    assert result['task']['dependencyWaitEpoch'] == 1
    assert 'dependencyWaitEpoch' not in control.tasks.record()
    assert control.tasks.record() == before


def test_directory_and_workspace_share_current_waiting_facts(tmp_path):
    from test_history_directory_navigation import directory_fixture
    from amplifier_web.history_directory_query import workspace_counts
    state, directory = directory_fixture(tmp_path, count=5)
    db = directory.db
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY, receipt TEXT)')
    initialize(db)
    owner = state['sessions'][1]
    owner.update(session(), workspace='/projects/alpha', workspaceId='a', sessionKind='root')
    state['runtimeControl'] = {'manager': {'task.get': {'task': task()}}}
    put(db, receipt())
    directory.sync([owner], state)
    assert db.execute("SELECT activity_kind FROM history_local_rows WHERE id='manager'").fetchone()[0] == 'waiting'
    counts = workspace_counts(directory, state)
    assert counts['/projects/alpha']['activityCounts']['waiting'] == 1
    before = directory.navigation_revision
    put(db, receipt('other'), 'second')
    directory.sync([owner], state)
    assert directory.navigation_revision > before, 'A second wait must invalidate retained context'
    before = directory.navigation_revision
    db.execute("DELETE FROM commands WHERE id='second'")
    put(db, receipt(response={'status':'staged','outcome':'success'}))
    directory.sync([owner], state)
    assert directory.navigation_revision > before
    assert db.execute("SELECT activity_label FROM history_local_rows WHERE id='manager'").fetchone()[0] == 'Work blocked'


@pytest.mark.parametrize('created', [False, True])
async def test_actual_producer_send_and_created_initial_brief(app, created):
    from test_collaborative_workspaces import agent_action, send, settled
    source, peer = app.state['sessions']
    active = {'id':'objective','revision':1,'status':'active','continuationEpoch':1}
    source['task'] = active
    app.state.setdefault('runtimeControl', {})[source['id']] = {'task.get': {'task':active}}
    if created:
        folder = app.data_dir/'sessions'/source['id']
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'effective-configuration.json').write_text(json.dumps({
            'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},
            'providers':[], 'tools':[], 'hooks':[]}))
        result = await agent_action(app, source, 'coordination.create',
            {'title':'Created worker','text':'Check the bounded fixture'}, 'create-wait')
        await settled(app, 'create-wait', 'created')
        identity = result['initialInputId']
        peer_id = result['sessionId']
    else:
        await send(app, 'ordinary-wait', returnNotification=True)
        identity, peer_id = 'ordinary-wait', peer['id']
    actual = await settled(app, identity, 'accepted')
    assert actual['commandAction'] == 'coordination.send'
    assert actual['subscription']['open'] is True
    if created:
        assert actual['creationOperationId'] == 'create-wait'
        assert app.collaboration.receipt('create-wait')['subscription']['status'] == 'linked'
    source.update(status='idle', task=task())
    app.state['runtimeControl'][source['id']]['task.get']['task'] = source['task']
    before = deepcopy(actual)
    waiting = project(app.db, source, source['task'])
    assert [row['sessionId'] for row in waiting['targets']] == [peer_id]
    assert app.collaboration.receipt(identity) == before
    assert source['task']['status'] == 'blocked'


def test_pause_new_task_and_settings_preparation_are_not_dependency_waits(db):
    put(db, receipt())
    for changed in [
        {**task(), 'id':'replacement'},
        {**task(), 'status':'paused'},
        {**task(), 'revision':5, 'continuationEpoch':5, 'dependencyWaitEpoch':4},
    ]:
        assert project(db, session(), changed) is None
    assert activity({**session(),'configurationBusy':True},task=task(),db=db)['kind'] == 'working'


def test_current_task_projection_is_content_free_and_bounded():
    row = {**task(), 'changes': [{'revision':i,'origin':'agent','fields':{}} for i in range(1,100)],
           'revision':99}
    assert wait_epoch(row) is None
    # No history walk when there is no canonical agent block.
    class NoHistory(list):
        def __iter__(self):
            raise AssertionError('walked unrelated task history')
    assert wait_epoch({'status':'paused','changes':NoHistory()}) is None


async def test_configuration_process_exit_retires_old_wait_without_new_input(app):
    from test_collaborative_workspaces import send, settled
    source = app.state['sessions'][0]
    active = {'id':'objective','revision':1,'status':'active','continuationEpoch':1}
    source['task'] = active
    app.state.setdefault('runtimeControl', {})[source['id']] = {'task.get': {'task':active}}
    await send(app, 'config-wait', returnNotification=True)
    await settled(app,'config-wait','accepted')
    source.update(status='idle',task=task())
    app.state['runtimeControl'][source['id']]['task.get']['task'] = source['task']
    assert project(app.db, source, source['task'])
    before_task = deepcopy(source['task'])
    await app.on_runtime_event('runtime.ended',{'sessionId':source['id'],'reason':'configuration'})
    source.update(status='idle',configurationBusy=False)
    assert project(app.db, source, source['task']) is None
    assert source['task'] == before_task


def test_cold_task_read_never_parses_retained_runtime_body(tmp_path):
    from test_history_directory_navigation import directory_fixture
    from amplifier_web.navigation_waiting import save_task, saved_task
    _, directory = directory_fixture(tmp_path, count=1)
    db = directory.db
    save_task(db,'manager',task())
    db.execute("INSERT OR REPLACE INTO state_records VALUES('runtime','manager',?)",
               (json.dumps({'history':['x'*2_000_000], 'task.get':{'task':task()}}),))
    queries=[]
    db.set_trace_callback(queries.append)
    assert saved_task(db,'manager') == task()
    db.set_trace_callback(None)
    assert len(queries)==1 and 'state_records' not in queries[0]
    assert input_boundary({'_navigationInput':['old','input'],
                           '_navigationEndedGeneration':'old'}) is None


async def test_actual_corrected_task_send_then_block_retains_exact_wait(app, monkeypatch):
    from test_task_continuity import controls, create
    from test_collaborative_workspaces import send, settled
    from amplifier_web.task_continuity import observe_task
    source=app.state['sessions'][0]
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    control=controls(source['id'])
    await create(control)
    await control.perform('task.update', {'commandId':'correction','expectedRevision':1,
                          'origin':'agent','correction':'Keep the same objective'})
    observe_task(app,source,control.tasks.canonical_snapshot())
    await send(app,'corrected-wait',returnNotification=True)
    await settled(app,'corrected-wait','accepted')
    await control.perform('task.block', {'commandId':'block-corrected','expectedRevision':2,
                          'origin':'agent','reason':'Pending exact result'})
    original=deepcopy(control.tasks.record())
    observe_task(app,source,control.tasks.canonical_snapshot())
    source['status']='idle'
    from amplifier_web.navigation_waiting import task_for
    assert source['_navigationTask']['dependencyWaitEpoch']==1
    assert 'dependencyWaitEpoch' not in source['task']
    assert project(app.db,source,task_for(app.state, source))['targets'][0]['sessionId']==app.state['sessions'][1]['id']
    assert control.tasks.record()==original


def test_waiting_and_paused_filter_shapes_remain_bounded():
    from amplifier_web.chat_navigation import view_patch
    for kind in ('waiting','paused'):
        view_patch({'navStatusFilter':kind,'workHomeFilter':kind,
                    'navStatusPages':{kind:1},'workHomePages':{kind:1},
                    'navStatusCollapsed':[kind]})


def test_derived_index_preserves_original_receipts_and_three_value_writers(tmp_path):
    path=tmp_path/'existing.sqlite3'
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    original=json.dumps(receipt(),sort_keys=True)
    db.execute('INSERT INTO commands VALUES(?,?,?)',('old','fingerprint',original))
    initialize(db)
    initialize(db)
    db.execute('INSERT INTO commands VALUES(?,?,?)',('new','next',original))
    db.commit();db.close()
    db=sqlite3.connect(path)
    assert db.execute('SELECT id,fingerprint,receipt FROM commands ORDER BY id').fetchall()==[
        ('new','next',original),('old','fingerprint',original)]
    assert project(db,session(),task())['targets'][0]['sessionId']=='worker'
    db.execute('DELETE FROM commands WHERE id=?',('new',))
    db.execute('UPDATE commands SET receipt=? WHERE id=?',
               (json.dumps(receipt(response={'status':'staged','outcome':'success'})),'old'))
    assert project(db,session(),task()) is None
    db.rollback()
    assert project(db,session(),task()), 'Derived state must roll back with the canonical receipt'
    db.execute('DELETE FROM commands')
    assert not db.execute('SELECT 1 FROM navigation_pending_requests').fetchone()
    db.close()


def test_first_population_is_atomic_and_reopen_does_not_reparse_receipts(tmp_path):
    db=sqlite3.connect(tmp_path/'failed-init.sqlite3')
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    original=json.dumps(receipt())
    db.execute('INSERT INTO commands VALUES(?,?,?)',('before','proof',original))
    db.commit()
    def deny_trigger(action,*args):
        return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_CREATE_TRIGGER else sqlite3.SQLITE_OK
    db.set_authorizer(deny_trigger)
    with pytest.raises(sqlite3.DatabaseError):
        initialize(db)
    db.set_authorizer(None)
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='navigation_pending_requests'").fetchone()
    assert db.execute('SELECT receipt FROM commands').fetchone()[0]==original
    initialize(db)
    assert project(db,session(),task())
    statements=[]
    db.set_trace_callback(statements.append)
    initialize(db)
    db.set_trace_callback(None)
    assert len(statements)==2 and all('sqlite_master' in sql for sql in statements)
    db.close()