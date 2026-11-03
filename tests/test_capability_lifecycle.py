"""Expired permission cards clear without reviving authority or tool work."""
import copy
import time
import pytest

from test_configuration_authority import app
from test_coordination_generation import agent
from test_automatic_history import app_factory


@pytest.mark.parametrize('boundary', ['stop', 'expiry', 'workspace'])
async def test_stale_card_is_retired_and_late_allow_is_an_idempotent_noop(app, boundary):
    source = app.state['sessions'][0]
    result = await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Change shared file rules'}, 'old-request',
        _configurationWait=True)
    grant = result['result']
    if boundary == 'stop':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    elif boundary == 'workspace':
        source['workspace'] = str(app.data_dir / 'moved')
    else:
        grant['expiresAt'] = time.time()-1
        app.configuration_authority.save(grant)
    messages = copy.deepcopy(source['messages'])
    calls = list(app.runtime.calls)
    app._publish_changes(sessions={source['id']})
    assert source['approvals'][0]['status'] == 'expired'
    args = {'sessionId':source['id'], 'id':grant['id'], 'decision':'allow'}
    receipt = await app.dispatch('approval.respond', args, command_id='late-click')
    assert receipt['accepted'] and not receipt['decisionApplied']
    assert receipt['result']['status'] == 'expired'
    assert (await app.dispatch('approval.respond', args, command_id='late-click'))['duplicate']
    assert source['messages'] == messages and app.runtime.calls == calls
    assert not app.configuration_authority.valid(receipt['result'], source)


@pytest.mark.parametrize('native_wait', [False, True])
@pytest.mark.parametrize('dormant', [False, True])
async def test_restart_reconciles_exact_owner_without_starting_worker(app_factory, native_wait, dormant):
    app = app_factory()
    selected = app._new_session({'title':'Selected'})
    target = app._new_session({'title':'Waiting'})
    target['status'] = 'idle' if dormant else 'working'
    app.state['sessions'].extend([selected, target])
    app.state['selectedSessionId'] = selected['id']
    grant = {'id':'capability:saved', 'sessionId':target['id'], 'workspace':target['workspace'],
        'interruptionRevision':0, 'revision':1, 'status':'pending', 'createdAt':time.time(),
        'effects':['file_access'], 'scopes':['local'], 'reason':'Saved scope', 'nativeWait':native_wait}
    app.configuration_authority.save(grant)
    target['approvals'] = [{'id':grant['id'], 'status':'pending'}]
    app._save()
    await app.close()
    restored = app_factory(home=app.data_dir)
    current = restored._session(target['id'])
    assert current['approvals'][0]['status'] == ('expired' if native_wait else 'pending')
    assert not restored.runtime.started and not restored.runtime.sent
    await restored.close()


async def test_old_test_only_request_is_automatic_but_host_setup_and_mixed_requests_still_ask(app):
    source = app.state['sessions'][0]
    for effects, scopes, pending in [(['test_environments'], ['session'], False),
            (['test_host_setup'], ['global'], True),
            (['test_environments', 'file_access'], ['session'], True)]:
        result = await agent(app, source, 'capability.request', {'sessionId':source['id'],
            'effects':effects, 'scopes':scopes, 'reason':'Requested task'}, 'request-'+effects[-1])
        assert (result['result']['status'] == 'pending') is pending
    assert len(source['approvals']) == 2


async def test_upgrade_clears_old_test_card_without_granting_or_executing(app):
    source = app.state['sessions'][0]
    grant = {'id':'capability:old-test', 'sessionId':source['id'], 'workspace':source['workspace'],
        'interruptionRevision':0, 'revision':1, 'status':'pending', 'createdAt':time.time(),
        'effects':['test_environments'], 'scopes':['session'], 'reason':'Test', 'nativeWait':True}
    app.configuration_authority.save(grant)
    source['approvals'] = [{'id':grant['id'], 'status':'pending'}]
    calls = list(app.runtime.calls)
    app._publish_changes(sessions={source['id']})
    assert source['approvals'][0]['resolutionReason'] == 'approval_not_required'
    assert source['approvals'][0]['status'] == 'expired'
    assert app.runtime.calls == calls


@pytest.mark.parametrize('decision', ['allow', 'deny'])
async def test_note_is_retained_and_sent_with_live_capability_decision_once(app, decision):
    from test_coordination_generation import until
    source = app.state['sessions'][0]
    calls = []
    async def approval(sid, identity, selected, comment=None):
        calls.append((sid, identity, selected, comment))
    app.runtime.approval = approval
    grant = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Requested change'}, 'noted',
        _configurationWait=True))['result']
    args = {'sessionId':source['id'], 'id':grant['id'], 'decision':decision,
            'comment':'Use the existing workspace folder instead.'}
    receipt = await app.dispatch('approval.respond', args, command_id='with-note')
    await until(lambda: len(calls) == 1)
    assert receipt['result']['comment'] == args['comment']
    assert calls[0][2:] == (decision, args['comment'])
    messages = [m for m in source['messages'] if m.get('approvalId') == grant['id']]
    assert len(messages) == 1 and args['comment'] in messages[0]['text']
    await app.dispatch('approval.respond', args, command_id='with-note')
    assert len([m for m in source['messages'] if m.get('approvalId') == grant['id']]) == 1


@pytest.mark.parametrize('boundary,word', [('stop', 'stopped'), ('expiry', 'expiry'), ('workspace', 'workspace')])
async def test_retired_request_explains_actual_boundary(app, boundary, word):
    source = app.state['sessions'][0]
    grant = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Review the file rules'}, 'reasoned'))['result']
    if boundary == 'stop':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    elif boundary == 'workspace':
        source['workspace'] = str(app.data_dir / 'different-workspace')
    else:
        grant['expiresAt'] = time.time() - 1
        app.configuration_authority.save(grant)
    calls = list(app.runtime.calls)
    app._publish_changes(sessions={source['id']})
    card = source['approvals'][0]
    assert card['status'] == 'expired'
    assert word in card['detail'].lower()
    assert 'resume the chat' not in card['detail'].lower()
    assert app.runtime.calls == calls


async def test_missing_request_is_unknown_not_a_claim_of_no_approval(app):
    source = app.state['sessions'][0]
    source['approvals'] = [{'id':'capability:missing', 'status':'pending', 'prompt':'Saved request'}]
    app._publish_changes(sessions={source['id']})
    card = source['approvals'][0]
    assert card['resolutionReason'] == 'request_unavailable'
    assert 'unavailable' in card['detail'].lower()
    assert 'no approval was applied' not in card['detail'].lower()
    assert not app.configuration_authority.rows(source['id'])


async def test_pending_card_after_restart_never_revokes_already_active_grant(app):
    source = app.state['sessions'][0]
    row = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['modules'], 'scopes':['project'], 'reason':'Reviewed module'}, 'active',
        _configurationWait=True))['result']
    await app.dispatch('approval.respond', {'sessionId':source['id'], 'id':row['id'],
        'decision':'allow'}, command_id='allowed')
    before = copy.deepcopy(app.configuration_authority.record(row['id']))
    source['approvals'][0].update(status='pending', _hostRestarted=True)
    app._publish_changes(sessions={source['id']})
    assert app.configuration_authority.record(row['id']) == before
    assert app.configuration_authority.valid(before, source)
    assert source['approvals'][0]['status'] == 'allow'


async def request_view(app, source, identity):
    return (await app.dispatch('capability.read', {'sessionId':source['id'], 'id':identity}))['result']['request']


async def test_exact_dismiss_preserves_new_request_and_notifies_only_once(app):
    import asyncio
    source, other = app.state['sessions']
    args = {'sessionId':source['id'], 'effects':['file_access'], 'scopes':['local'], 'reason':'File rules'}
    old = (await agent(app, source, 'capability.request', args, 'old', _configurationWait=True))['result']
    source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    app._publish_changes(sessions={source['id']})
    epoch = source['interruptionRevision']
    await app.on_runtime_event('runtime.generation', {
        'sessionId': source['id'], 'rootSessionId': source['id'], 'event': 'generation.started',
        'generation_id': 'new-generation', 'interruptionRevision': epoch})
    await app.on_runtime_event('runtime.status', {
        'sessionId': source['id'], 'status': 'working', 'event': 'input.delivered', 'inputId': 'new-human'})
    assert source['collaborationGeneration']['id'] == 'new-generation'
    assert source['collaborationGeneration']['inputIds'] == ['new-human']
    assert source['collaborationGeneration']['interruptionRevision'] == epoch
    assert old['interruptionRevision'] != epoch  # The old request remains across Stop.
    fresh = (await agent(app, source, 'capability.request', args, 'fresh'))['result']
    live = (await app.dispatch('capability.grant', {**args, 'reason':'Separate usable grant'},
                              command_id='live'))['result']
    source['messages'].append({'id':'unknown-effect','role':'assistant','text':'Outcome unconfirmed.'})
    history, calls = copy.deepcopy(source['messages']), list(app.runtime.calls)
    app.state['selectedSessionId'] = other['id']
    observed = await request_view(app, source, old['id'])
    assert observed['canDismiss'] and not observed['canDecide']
    deliveries = []
    async def approval(*args, **kwargs):
        deliveries.append(args)
    app.runtime.approval = approval
    dismiss = {'sessionId':source['id'], 'id':old['id'], 'dismissRequest':True,
               'expectedRevision':observed['revision'], 'expectedRequestHash':observed['requestHash']}
    first = await app.dispatch('capability.revoke', dismiss, command_id='dismiss')
    await asyncio.sleep(0)
    await app.dispatch('capability.revoke', dismiss, command_id='dismiss')
    await asyncio.sleep(0)
    assert first['result']['status'] == 'revoked'
    assert len(deliveries) == 1 and deliveries[0][1:] == (old['id'], 'deny')
    assert app.configuration_authority.record(fresh['id'])['status'] == 'pending'
    assert app.configuration_authority.record(live['id']) == live
    assert source['messages'] == history and app.runtime.calls == calls
    assert app.state['selectedSessionId'] == other['id']
    assert (await request_view(app, source, old['id']))['dismissed']
    # The adjacent request can still be denied without touching the older result.
    await app.dispatch('approval.respond', {'sessionId':source['id'], 'id':fresh['id'],
        'decision':'deny'}, command_id='next-deny')
    assert app.configuration_authority.record(old['id'])['status'] == 'revoked'
    assert app.configuration_authority.record(live['id']) == live


async def test_dismiss_cannot_revoke_current_or_changed_scope(app):
    from amplifier_web.service import AppError
    source = app.state['sessions'][0]
    row = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['modules'], 'scopes':['project'], 'reason':'Module'}, 'current'))['result']
    view = await request_view(app, source, row['id'])
    assert view['canDecide'] and not view['canDismiss']
    with pytest.raises(AppError):
        await app.dispatch('capability.revoke', {'sessionId':source['id'], 'id':row['id'],
            'dismissRequest':True, 'expectedRevision':view['revision'],
            'expectedRequestHash':view['requestHash']}, command_id='not-stale')
    changed = app.configuration_authority.record(row['id'])
    changed['reason'] = 'Changed review text'
    app.configuration_authority.save(changed)
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('approval.respond', {'sessionId':source['id'], 'id':row['id'],
            'decision':'allow','expectedRequestHash':view['requestHash']}, command_id='late-scope')
    assert app.configuration_authority.record(row['id'])['status'] == 'pending'


async def test_expired_note_is_retained_without_runtime_delivery(app):
    source = app.state['sessions'][0]
    grant = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Requested change'}, 'expired-note',
        _configurationWait=True))['result']
    source['interruptionRevision'] = 2
    async def forbidden(*args, **kwargs):
        pytest.fail('Expired waiter must not be contacted')
    app.runtime.approval = forbidden
    args = {'sessionId':source['id'], 'id':grant['id'], 'decision':'deny', 'comment':'Use a local alternative.'}
    receipt = await app.dispatch('approval.respond', args, command_id='old-note')
    assert receipt['decisionApplied'] is False
    assert receipt['result']['comment'] == args['comment']
    await app.dispatch('approval.respond', args, command_id='old-note')
    assert len([m for m in source['messages'] if m.get('approvalId') == grant['id']]) == 1


async def test_missing_record_dismiss_is_audited_and_duplicate_safe(app):
    source = app.state['sessions'][0]
    source['approvals'] = [{'id':'capability:missing', 'status':'pending', 'prompt':'Original unknown request'}]
    view = await request_view(app, source, 'capability:missing')
    assert view['status'] == 'unavailable'
    assert view['scopeCovered'] is None
    args = {'sessionId':source['id'],'id':view['id'],'dismissRequest':True,
            'expectedRevision':0,'expectedRequestHash':view['requestHash']}
    first = await app.dispatch('capability.revoke', args, command_id='missing-dismiss')
    second = await app.dispatch('capability.revoke', args, command_id='missing-dismiss')
    assert second['duplicate'] and first['result']['status'] == 'unavailable'
    assert source['approvals'][0]['prompt'] == 'Original unknown request'
    assert source['approvals'][0]['dismissedAt']
    assert not app.configuration_authority.rows(source['id']) and not app.runtime.calls


@pytest.mark.parametrize('boundary', ['stop','placement','new-decision'])
async def test_reviewed_decision_rechecks_after_commit_lock_await(app, boundary):
    import asyncio
    from amplifier_web.service import AppError
    source = app.state['sessions'][0]
    row = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['modules'], 'scopes':['session'], 'reason':'Reviewed scope'}, 'racing'))['result']
    view = await request_view(app, source, row['id'])
    await app.lock.acquire()
    task = asyncio.create_task(app.dispatch('approval.respond', {'sessionId':source['id'],
        'id':row['id'], 'decision':'allow', 'expectedRequestHash':view['requestHash']}, command_id='race'))
    await asyncio.sleep(0)
    if boundary == 'stop':
        source['interruptionRevision'] = 1
    elif boundary == 'placement':
        source['executionRevision'] = source.get('executionRevision',0)+1
    else:
        row.update(status='denied',revision=2)
        app.configuration_authority.save(row)
    app.lock.release()
    if boundary == 'stop':
        assert not (await task)['decisionApplied']
    else:
        with pytest.raises(AppError):
            await task
    assert app.configuration_authority.record(row['id'])['status'] != 'active'
    assert not app.runtime.calls


async def test_configuration_change_does_not_expire_unbounded_raw_request(app):
    source = app.state['sessions'][0]
    row = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['modules'], 'scopes':['session'], 'reason':'No TTL'}, 'raw-request'))['result']
    source['configurationRevision'] = 123
    app._publish_changes(sessions={source['id']})
    view = await request_view(app, source, row['id'])
    assert view['canDecide'] and view['expiresAt'] is None
    await app.dispatch('approval.respond', {'sessionId':source['id'],'id':row['id'],'decision':'allow',
        'expectedRequestHash':view['requestHash']}, command_id='current-allowed')
    assert app.configuration_authority.valid(app.configuration_authority.record(row['id']), source)


async def test_full_access_policy_revision_keeps_live_waiter_for_its_owner(app):
    from test_access_mode import save
    source = app.state['sessions'][0]
    async def fail(*args, **kwargs):
        raise RuntimeError('Not delivered')
    app.runtime.approval = fail
    row = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['modules'], 'scopes':['session'], 'reason':'Live full access'}, 'full-wait',
        _configurationWait=True))['result']
    await save(app)
    await app.access_mode.settle(source['id'])
    grant = app.configuration_authority.record(row['id'])
    # An unchanged effective Full access mode with a newer policy revision.
    grant['accessRevision'] = 'previous-policy-revision'
    app.configuration_authority.save(grant)
    app._publish_changes(sessions={source['id']})
    assert source['approvals'][0]['status']=='pending'
    deliveries=[]
    async def delivered(*args):
        deliveries.append(args)
    app.runtime.approval=delivered
    await app.access_mode.settle(source['id'])
    assert source['approvals'][0]['status']=='allow'
    assert len(deliveries)==1
    assert app.configuration_authority.valid(app.configuration_authority.record(row['id']),source)


async def test_dismiss_does_not_forward_history_only_note_or_replay_old_commands(app):
    import asyncio
    from test_coordination_generation import until
    source = app.state['sessions'][0]
    args = {'sessionId':source['id'],'effects':['file_access'],'scopes':['session'],'reason':'Old'}
    row = (await agent(app, source, 'capability.request', args, 'original-request', _configurationWait=True))['result']
    row['expiresAt'] = time.time()-1
    app.configuration_authority.save(row)
    note = {'sessionId':source['id'],'id':row['id'],'decision':'deny','comment':'Historical only'}
    await app.dispatch('approval.respond', note, command_id='old-decision')
    deliveries = []
    async def approval(*args, **kwargs):
        deliveries.append((args,kwargs))
        return {'accepted':True}
    app.runtime.approval = approval
    view = await request_view(app,source,row['id'])
    await app.dispatch('capability.revoke', {'sessionId':source['id'],'id':row['id'],'dismissRequest':True,
        'expectedRevision':view['revision'],'expectedRequestHash':view['requestHash']}, command_id='dismiss-noted')
    await until(lambda: len(deliveries)==1)
    await agent(app, source, 'capability.request', args, 'original-request', _configurationWait=True)
    await app.dispatch('approval.respond', note, command_id='old-decision')
    await asyncio.sleep(.01)
    assert len(deliveries)==1 and deliveries[0][1]=={}
    assert app.configuration_authority.record(row['id'])['comment']=='Historical only'
    assert app.configuration_authority.record(row['id'])['dismissalDelivery']=='delivered'


async def test_dismiss_releases_exact_native_waiter_without_a_note_or_another_turn(app, monkeypatch):
    import asyncio
    from amplifier_web import runtime_worker
    from test_coordination_generation import until
    monkeypatch.setattr(runtime_worker, 'publish', lambda event: None)
    worker = runtime_worker.Worker()
    source = app.state['sessions'][0]
    row = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['modules'], 'scopes':['session'], 'reason':'Waiting tool'}, 'native-dismiss',
        _configurationWait=True))['result']
    waiter = asyncio.create_task(worker.ask('Waiting tool', ['allow','deny'], identity=row['id']))
    await asyncio.sleep(0)
    future = worker.approvals[row['id']][0]
    count = []
    async def approval(sid, identity, decision, **kwargs):
        count.append(identity)
        await worker._command_serial({'op':'approval','id':'dismiss-native',
                                      'approval_id':identity,'decision':decision,**kwargs})
        return {'accepted':True}
    app.runtime.approval = approval
    source['interruptionRevision'] = 1
    view = await request_view(app,source,row['id'])
    args = {'sessionId':source['id'],'id':row['id'],'dismissRequest':True,
        'expectedRevision':view['revision'],'expectedRequestHash':view['requestHash'],
        'comment':'Retain this draft note only'}
    await app.dispatch('capability.revoke',args,command_id='native-dismiss-decision')
    await until(lambda: future.done())
    decision = await waiter
    assert decision == 'deny'
    assert not getattr(decision,'comment',None)
    await app.dispatch('capability.revoke',args,command_id='native-dismiss-decision')
    await asyncio.sleep(.01)
    assert count == [row['id']] and not worker.approvals and not app.runtime.calls
    assert source['approvals'][0]['dismissalComment'] == 'Retain this draft note only'


@pytest.mark.parametrize('committed', [False,True])
async def test_dismiss_storage_failure_preserves_unknown_and_never_replays_delivery(app, monkeypatch, committed):
    source = app.state['sessions'][0]
    row = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['modules'], 'scopes':['session'], 'reason':'Original'}, 'lost-dismiss',
        _configurationWait=True))['result']
    source['interruptionRevision'] = 1
    view = await request_view(app,source,row['id'])
    publish = app._publish_changes
    def fail(**kwargs):
        if committed:
            publish(**kwargs)
        raise OSError('Lost storage acknowledgement')
    monkeypatch.setattr(app,'_publish_changes',fail)
    args = {'sessionId':source['id'],'id':row['id'],'dismissRequest':True,
        'expectedRevision':view['revision'],'expectedRequestHash':view['requestHash']}
    with pytest.raises(OSError):
        await app.dispatch('capability.revoke',args,command_id='lost-dismiss-decision')
    monkeypatch.setattr(app,'_publish_changes',publish)
    now = await request_view(app,source,row['id'])
    assert now['dismissed'] is committed
    if committed:
        assert now['dismissalDelivery']=='unconfirmed'
        assert (await app.dispatch('capability.revoke',args,command_id='lost-dismiss-decision'))['duplicate']
    else:
        assert now['canDismiss'] and now['status']=='expired'
    assert not app.runtime.calls


async def test_failed_stale_note_save_can_retry_without_duplicating_history(app, monkeypatch):
    source = app.state['sessions'][0]
    grant = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Requested change'}, 'failed-note'))['result']
    source['interruptionRevision'] = 1
    before = copy.deepcopy(source['messages'])
    publish = app._publish_changes
    def fail(**kwargs):
        raise OSError('Storage unavailable')
    monkeypatch.setattr(app, '_publish_changes', fail)
    args = {'sessionId':source['id'], 'id':grant['id'], 'decision':'deny', 'comment':'Use the local copy.'}
    with pytest.raises(OSError):
        await app.dispatch('approval.respond', args, command_id='note-retry')
    assert source['messages'] == before
    monkeypatch.setattr(app, '_publish_changes', publish)
    await app.dispatch('approval.respond', args, command_id='note-retry')
    assert len([m for m in source['messages'] if m.get('approvalId') == grant['id']]) == 1
