import asyncio
import copy

from amplifier_web.service import AppService


async def test_reviewed_diagnostic_losses_ignore_healthy_index_changes_but_not_new_losses(tmp_path):
    from amplifier_web.attention import snapshot
    app = AppService(tmp_path / 'data', workspace=tmp_path)
    try:
        local = {'records': 200, 'oldest': 1, 'newest': 2, 'dropped': 39,
                 'storageError': False, 'configurationError': False}
        app.state['diagnostics']['local'] = local
        notice = lambda: next(row for row in snapshot(app.state)['items'] if row['id'] == 'diagnostics:capture')
        original = notice()['fingerprint']
        await app.dispatch('attention.read', {'ids': ['diagnostics:capture'],
            'fingerprints': {'diagnostics:capture': original}})
        local.update(records=250, oldest=2, newest=100)
        assert notice()['read']
        for change in ({'dropped': 40}, {'storageError': True}, {'configurationError': True}):
            local.update(change)
            assert not notice()['read']
            await app.dispatch('attention.read', {'ids': ['diagnostics:capture'],
                'fingerprints': {'diagnostics:capture': original}})
            assert not notice()['read']
            original = notice()['fingerprint']
            await app.dispatch('attention.read', {'ids': ['diagnostics:capture'],
                'fingerprints': {'diagnostics:capture': original}})
            assert notice()['read']
    finally:
        await app.close()


async def test_completions_are_durable_scoped_and_fingerprint_acknowledged(tmp_path):
    app=AppService(tmp_path/'data',workspace=tmp_path)
    try:
        await app.dispatch('session.create',{'title':'First'})
        sid=app.state['selectedSessionId']
        workspace=app.state['selectedWorkspaceId']
        await app.dispatch('session.create',{'title':'Second'})
        event={'sessionId':sid,'event':'generation.finished','generation_id':'g1','input_ids':['input1'],'text':'Ready','active_job_ids':[]}
        await app.on_runtime_event('assistant.message',{'sessionId':sid,'text':'A partial response'})
        await app.on_runtime_event('runtime.status',{'sessionId':sid,'status':'idle'})
        assert app.get_state()['attention']['unread']==0
        await app.on_runtime_event('runtime.generation',{**event,'active_job_ids':['worker']})
        assert app.get_state()['attention']['unread']==0
        await app.on_runtime_event('runtime.generation',{**event,'sessionId':'child','rootSessionId':sid})
        assert app.get_state()['attention']['unread']==0
        await app.on_runtime_event('runtime.generation',event)
        attention=app.get_state()['attention'];item=attention['items'][0]
        assert attention['sessions']=={sid:1}
        assert attention['workspaces'][workspace]==attention['sections']['chats']==1
        assert attention['settingsUnread']==0
        # Selection is not a receipt that the user saw the response.
        await app.dispatch('session.select',{'id':sid},origin='agent')
        assert app.get_state()['attention']['unread']==1
        await app.app_bridge('dispatch',{'action':'attention.read','args':{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}}},sid)
        await app.on_runtime_event('runtime.generation',event)  # duplicate event
        await app.dispatch('session.rename',{'id':sid,'title':'Renamed'})
        assert app.get_state()['attention']['unread']==0
        await app.on_runtime_event('runtime.generation',{**event,'generation_id':'g2'})
        await app.dispatch('attention.read',{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}})
        assert app.get_state()['attention']['unread']==1  # stale UI ack cannot clear g2
        state=copy.deepcopy(app.get_state()['attention'])
    finally:await app.close()
    app=AppService(tmp_path/'data',workspace=tmp_path)
    try:
        assert app.get_state()['attention']==state
        items=app.get_state()['attention']['items']
        await app.dispatch('attention.read',{'ids':[i['id'] for i in items],'fingerprints':{i['id']:i['fingerprint'] for i in items}})
        assert app.get_state()['attention']['unread']==0
    finally:await app.close()


async def test_feedback_closes_on_durable_acceptance_and_result_is_visible_elsewhere(tmp_path,monkeypatch):
    from amplifier_web import feedback
    release=asyncio.Event()
    async def create_issue(*args):
        await release.wait()
        return feedback.ISSUES_URL+'/42'
    monkeypatch.setattr(feedback,'create_issue',create_issue)
    monkeypatch.setattr(feedback.shutil,'which',lambda _: '/fixture/gh')
    app=AppService(tmp_path,workspace=tmp_path)
    payload={'requestId':'feedback-test-123','title':'Test','body':'Body','category':'bug'}
    try:
        app.state['view'].update(panel='feedback',feedbackDraft={'pending':payload})
        result=await asyncio.wait_for(app.dispatch('feedback.submit',payload),1)
        assert result['accepted'] and app.state['view']['panel'] is None
        assert app.state['feedback']['requests'][0]['status'] in {'queued','sending'}
        assert app.db.execute('select count(*) from feedback_requests').fetchone()[0]==1
        await app.dispatch('view.update',{'patch':{'panel':'settings'}})
        release.set()
        await asyncio.gather(*list(app.tasks))
        attention=app.get_state()['attention']
        assert attention['sections']['feedback']==0 and attention['settingsUnread']==0
        assert attention['badgeUnread']==0 and attention['items'][0]['informational']
        assert attention['items'][0]['url']==feedback.ISSUES_URL+'/42'
        assert app.state['view']['panel']=='settings'
        await app.dispatch('attention.read',{'ids':[attention['items'][0]['id']]},origin='agent')
        assert app.get_state()['attention']['unread']==0
        await app.feedback.update(payload['requestId'],status='unknown',message='Check delivery')
        assert app.get_state()['attention']['unread']==1
    finally:await app.close()

async def test_manual_unread_survives_other_clients_and_restart(tmp_path):
    app = AppService(tmp_path/'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Read later'})
        sid = app.state['selectedSessionId']
        for client in ('one', 'two'):
            app.clients.attach(client)
            with app.clients.bind(client):
                await app.dispatch('session.select', {'id': sid})
        with app.clients.bind('one'):
            await app.dispatch('session.markUnread', {'id': sid})
            item = next(i for i in app.browser_state()['attention']['items'] if i['id'] == 'unread:'+sid)
        for client in ('one', 'two'):
            with app.clients.bind(client):
                state = app.browser_state()
                assert state['attention']['sessions'][sid] == 1
                assert state['view'].get('unreadVisit') != item['visit']
        with app.clients.bind('two'):
            await app.dispatch('session.select', {'id': sid})
            assert app.browser_state()['view']['unreadVisit'] == item['visit']
        await app.dispatch('session.markUnread', {'id': sid})
        await app.dispatch('attention.read', {'ids': [item['id']], 'fingerprints': {item['id']: item['fingerprint']}})
        assert app.get_state()['attention']['sessions'][sid] == 1
    finally:
        await app.close()
    app = AppService(tmp_path/'data', workspace=tmp_path)
    try:
        assert app.get_state()['attention']['sessions'][sid] == 1
        item = next(i for i in app.get_state()['attention']['items'] if i['id'] == 'unread:'+sid)
        await app.dispatch('attention.read', {'ids': [item['id']], 'fingerprints': {item['id']: item['fingerprint']}})
        assert sid not in app.get_state()['attention']['sessions']
    finally:
        await app.close()


async def test_completion_badge_preference_and_bulk_review_preserve_pending_work(tmp_path):
    from amplifier_web.management import Management
    app = AppService(tmp_path/'data', workspace=tmp_path)
    app.management=Management(app)
    try:
        await app.dispatch('session.create', {'title':'Finished'})
        row=app._session()
        row.update(completion={'id':'g1','at':1}, approvals=[{'id':'permission','status':'pending','tool':'bash'}])
        await app.dispatch('question.create', {'sessionId':row['id'],'prompt':'Choose one','required':True,'dependency':'Choose next step'})
        app._publish()
        before=app.get_state()['attention']
        assert before['unread']==3 and before['badgeUnread']==2
        await app.management.command('notifications.save', {'patch':{'completionBadge':True}}, 'pref1')
        assert app.get_state()['attention']['badgeUnread']==3
        await app.management.command('notifications.save', {'patch':{'completionBadge':False}}, 'pref2')
        assert app.get_state()['attention']['badgeUnread']==2
        original=copy.deepcopy(row)
        # A newly finished generation cannot be acknowledged by an older UI.
        row['completion']={'id':'g2','at':2};app._publish()
        await app.dispatch('attention.read', {'ids':[i['id'] for i in before['items']], 'fingerprints':{i['id']:i['fingerprint'] for i in before['items']}})
        after=app.get_state()['attention']
        assert after['unread']==1 and after['badgeUnread']==0
        assert row['approvals']==original['approvals'] and row['questions']==original['questions']
        item=next(i for i in after['items'] if not i['read'])
        await app.dispatch('attention.read', {'ids':[item['id']], 'fingerprints':{item['id']:item['fingerprint']}})
        assert app.get_state()['attention']['unread']==0
        assert row['approvals']==original['approvals'] and row['questions']==original['questions']
    finally: await app.close()
    app=AppService(tmp_path/'data',workspace=tmp_path)
    app.management=Management(app)
    try:
        assert app.get_state()['notificationSettings']['completionBadge'] is False
        assert app.get_state()['attention']['unread']==0
        # Reviewing attention did not answer the current-host permission.
        # Replacing its host expires the native waiter without replaying it.
        approval=app._session()['approvals'][0]
        assert approval['status']=='expired'
        assert approval['resolutionReason']=='host_restarted'
        assert app._session()['questions']==original['questions']
    finally: await app.close()
