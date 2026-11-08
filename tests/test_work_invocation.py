"""Commissioned-input Work contract; original absent-API baseline is retained.

Use the real AppService, receipt transactions and existing synthetic transport
fixtures. These tests do not pretend to be native worker, tool:pre, nested-tool or
post-truncation delivery acceptance. No successful association is fabricated.
"""
import asyncio
import copy
import hashlib
import json
import sqlite3

import pytest

from amplifier_web.management import Management
from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, Runtime, agent_action, generation, settled, until
from test_peer_creation_operation import commission, initial_accepted, plan
from test_workspace_work import create, change_work, read, revise, source, workspace_id


async def prepared_work(app, owner, identity, **content):
    item = (await create(app, owner, command='capture-' + identity,
                         intent=identity, **content))['result']['item']
    return (await change_work(app, owner, 'link', item, 'builder-' + identity,
        linkedSessionId=owner['id'], role='executor', candidate='b' * 40))['result']['item']


def reference(item, **patch):
    return {'workId': item['id'], 'expectedRevision': item['revision'],
            'promiseRevision': item['promiseRevision'], 'candidate': item['candidate'],
            'role': 'executor', **patch}


def transport(actor, *, inputs=None, generation_id=None, principal=None):
    current = actor['collaborationGeneration']
    return {'_runtimeSessionId': principal or actor.get('runtimeSessionId') or actor['id'],
            '_generationId': generation_id or current['id'],
            '_inputBindings': [{'inputId': identity, 'clientId': None}
                               for identity in (current.get('inputIds', []) if inputs is None else inputs)]}


async def current_work(app, actor, **binding):
    value = await app.app_bridge('coordination.current', transport(actor, **binding), actor['id'])
    return value['work']


async def revalidate(app, actor, version):
    return await app.app_bridge('work.invocation.revalidate', {
        'version': version, **transport(actor)}, actor['id'])


async def commissioned(app, owner, item, identity='bound-commission'):
    plan(app, owner)
    created = await commission(app, owner, identity=identity,
                               workReferences=[reference(item)], returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    child = app._session(created['sessionId'])
    await generation(app, child, 'generation-' + identity, [created['initialInputId']])
    return created, child


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def retained_bytes(app):
    """Observe durable receipts, not an inferred in-memory success label."""
    return dict(app.db.execute('SELECT id, receipt FROM commands').fetchall())


def retained_source_event(app, work_id, message):
    events = [json.loads(raw) for raw in retained_bytes(app).values()]
    matches = [event for event in events
               if event.get('commandAction') == 'workspace.work.event'
               and event.get('workItemId') == work_id
               and event.get('source', {}).get('messageId') == message['messageId']
               and event.get('source', {}).get('sessionId') == message['sessionId']]
    assert len(matches) == 1
    return matches[0]


async def assert_available_commission(app, owner, item, identity):
    created, child = await commissioned(app, owner, item, identity)
    required = (await current_work(app, child))['required']
    assert required['status'] == 'available' and required['workCount'] == 1
    assert required['inputIds'] == [created['initialInputId']]
    assert required['associationRequestIds'] == [created['requestId']]
    assert required['version'] and required['effectAuthorized'] is False
    return created, child


async def test_new_work_establishes_candidate_through_executor_link_before_commission(app):
    owner = app.state['sessions'][0]
    intent = 'capture-intent-is-not-a-candidate'
    item = (await create(app, owner, intent=intent))['result']['item']
    assert item['candidate'] is None
    plan(app, owner)
    before = retained_bytes(app)
    session_ids = [row['id'] for row in app.state['sessions']]
    with pytest.raises(AppError, match='Work candidate changed'):
        await commission(app, owner, identity='unestablished-candidate',
                         workReferences=[reference(item, candidate=intent)])
    assert retained_bytes(app) == before
    assert [row['id'] for row in app.state['sessions']] == session_ids
    assert not app.runtime.inputs

    linked = (await change_work(app, owner, 'link', item, 'establish-candidate',
        linkedSessionId=owner['id'], role='executor', candidate='b' * 40))['result']['item']
    assert linked['candidate'] == 'b' * 40
    assert linked['revision'] == item['revision'] + 1
    assert linked['promiseRevision'] == item['promiseRevision']
    assert not app.runtime.inputs
    created, target = await assert_available_commission(app, owner, linked, 'established-candidate')
    association = app.collaboration.receipt(created['requestId'])['workAssociation']
    assert association['references'][0]['candidate'] == linked['candidate']
    assert association['references'][0]['expectedRevision'] == linked['revision']
    assert target['collaborationGeneration']['inputIds'] == [created['initialInputId']]


async def test_commission_seals_work_association_before_native_admission(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'bound')
    original = app.runtime.collaboration_input
    observed = []

    async def inspect_transaction(session, args, guard, emit):
        # A second connection sees only committed host receipts.
        with sqlite3.connect(app.db.execute('PRAGMA database_list').fetchone()[2]) as db:
            creation = json.loads(db.execute(
                "SELECT receipt FROM commands WHERE id='bound-commission'").fetchone()[0])
            request = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?',
                                           (args['inputId'],)).fetchone()[0])
            association = creation['workAssociation']
            assert association['version'] == 1
            assert association['requestId'] == creation['requestId']
            assert association['initialInputId'] == args['inputId']
            assert association['targetRootId'] == session['id']
            assert association['sourceRootId'] == owner['id']
            assert request['workAssociationRef']['requestId'] == creation['requestId']
            assert request['workAssociationRef']['version'] == 1
            bound = association['references'][0]
            assert bound['workId'] == item['id'] and bound['promiseRevision'] == 1
            assert bound['candidate'] == item['candidate']
            event = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?',
                                         (bound['linkEventId'],)).fetchone()[0])
            assert event['commandAction'] == 'workspace.work.event'
            assert event['workItemId'] == item['id']
            assert event['link']['sessionId'] == session['id']
            record = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?',
                                          (item['id'],)).fetchone()[0])['result']
            assert record['ownerSessionId'] == owner['id']
            assert any(link['sessionId'] == session['id'] for link in record['links'])
            observed.append(association['requestId'])
        return await original(session, args, guard, emit)

    app.runtime.collaboration_input = inspect_transaction
    created, child = await commissioned(app, owner, item)
    assert observed == [created['requestId']]
    before = retained_bytes(app)
    association_before = canonical(json.loads(before[created['requestId']])['workAssociation'])
    work_before = json.loads(before[item['id']])['result']
    link_events_before = {identity: raw for identity, raw in before.items()
                          if (value := json.loads(raw)).get('commandAction') == 'workspace.work.event'
                          and value.get('workItemId') == item['id'] and value.get('kind') == 'link'}
    inputs_before = copy.deepcopy(app.runtime.inputs)
    repeated = await commission(app, owner, identity='bound-commission',
                                workReferences=[reference(item)], returnNotification=False)
    assert repeated['duplicate']
    after = retained_bytes(app)
    assert after == before  # Work revision, immutable event IDs and all receipt bytes.
    assert canonical(json.loads(after[created['requestId']])['workAssociation']) == association_before
    assert json.loads(after[item['id']])['result']['revision'] == work_before['revision']
    assert {identity: after[identity] for identity in link_events_before} == link_events_before
    assert app.runtime.inputs == inputs_before and len(observed) == 1
    # Only the references differ under the SAME command ID. Dedup must reject
    # before interpreting current revisions or retaining any new relation/input.
    with pytest.raises(AppError, match='This command ID was already used with different contents') as conflict:
        await commission(app, owner, identity='bound-commission',
                         workReferences=[reference(item, role='reviewer')], returnNotification=False)
    assert conflict.value.status == 409
    assert 'Unknown action' not in str(conflict.value)
    assert retained_bytes(app) == before
    assert app.runtime.inputs == inputs_before and len(observed) == 1
    assert not (await current_work(app, child))['required']['effectAuthorized']


async def test_required_set_is_delivered_association_not_all_owned_work(app, monkeypatch):
    owner = app.state['sessions'][0]
    first = await prepared_work(app, owner, 'governing')
    unrelated = await prepared_work(app, owner, 'unrelated')
    created, child = await commissioned(app, owner, first)
    # A contributor relationship alone must not replace invocation provenance.
    await change_work(app, owner, 'link', unrelated, 'unrelated-contribution',
                     linkedSessionId=child['id'], role='reviewer', candidate=unrelated['candidate'])
    calls = copy.deepcopy(app.runtime.inputs)
    def forbidden(*args, **kwargs):
        pytest.fail('Invocation inspection hydrated history or started a worker')
    monkeypatch.setattr(app.history, 'ensure_loaded', forbidden)
    monkeypatch.setattr(Management, 'ensure_runtime', forbidden)
    work = await current_work(app, child)
    assert work['available']
    assert {row['id'] for row in work['discovery']['items']} == {first['id'], unrelated['id']}
    required = work['required']
    assert required['status'] == 'available' and required['workCount'] == 1
    assert required['associationRequestIds'] == [created['requestId']]
    page = (await agent_action(app, child, 'workspace.work.invocation', {
        'sessionId': child['id'], 'expectedVersion': required['version']}, 'manifest'))['result']
    assert {row['workId'] for row in page['items']} == {first['id']}
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 12 * 1024
    assert app.runtime.inputs == calls


@pytest.mark.parametrize('transition', ['complete', 'reassign'])
async def test_saved_owner_task_claim_is_rechecked_at_invocation_boundary(app, transition):
    from test_assignments import start
    from test_assignment_tasks import create as create_task, change as change_task
    owner = app.state['sessions'][0]
    await start(app)
    task = await create_task(app, owner)
    claimed = (await change_task(app, owner, 'claim', task))['result']['task']
    item = (await create(app, owner, command='task-backed-work'))['result']['item']
    item = (await change_work(app, owner, 'link', item, 'task-backed-link',
        linkedSessionId=owner['id'], role='executor', candidate='b' * 40,
        taskId=task['id'], claimId=claimed['claim']['id']))['result']['item']
    created, child = await commissioned(app, owner, item, 'task-backed-commission')
    before = (await current_work(app, child))['required']
    assert before['status'] == 'available'
    original = copy.deepcopy(app.collaboration.receipt(created['requestId'])['workAssociation'])
    proof = original['references'][0]['sourceClaims'][0]
    assert proof['taskRevision'] == claimed['revision'] and proof['claim']['id'] == claimed['claim']['id']
    await change_task(app, owner, transition, claimed, 'after-capture',
        **({'report': 'Original responsibility finished'} if transition == 'complete'
           else {'holderSessionId': child['id']}))
    after = (await current_work(app, child))['required']
    assert after['status'] == 'unavailable'
    assert 'claim' in after['coverage']['unsupportedInputs'][0]['reason'].lower()
    assert after['effectAuthorized'] is False
    checked = await revalidate(app, child, before['version'])
    assert checked['current'] is False and checked['effectAuthorized'] is False
    assert app.collaboration.receipt(created['requestId'])['workAssociation'] == original
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('patch', [
    {'expectedRevision': 999}, {'promiseRevision': 999}, {'candidate': 'c' * 40},
    {'workId': 'work:missing'},
], ids=['patch0', 'patch1', 'patch2', 'patch3'])
async def test_commission_rejects_unresolved_or_caller_forged_work_reference(app, patch):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'guarded')
    plan(app, owner)
    before = await read(app, owner, item['id'])
    count = len(app.state['sessions'])
    status, reason = {
        'expectedRevision': (409, '(?i)work.*revision'),
        'promiseRevision': (409, '(?i)promise.*revision'),
        'candidate': (409, '(?i)candidate'),
        'workId': (404, '(?i)work.*(unavailable|not found)'),
    }[next(iter(patch))]
    receipts_before = retained_bytes(app)
    with pytest.raises(AppError, match=reason) as rejected:
        await commission(app, owner, identity='invalid-work-commission',
                         workReferences=[reference(item, **patch)])
    assert rejected.value.status == status
    assert 'Unknown action' not in str(rejected.value)
    assert len(app.state['sessions']) == count and not app.runtime.inputs
    assert await read(app, owner, item['id']) == before
    assert retained_bytes(app) == receipts_before
    # Only the malformed reference field and command identity differ. The same
    # actor, item, candidate and otherwise identical commission must succeed.
    await assert_available_commission(app, owner, item, 'valid-work-commission')


@pytest.mark.parametrize('patch,reason', [
    ({'role': 'administrator'}, "(?i)administrator.*not one of"),
    ({'hostObserved': True}, '(?i)additional properties.*hostObserved'),
], ids=['patch4', 'patch5'])
async def test_commission_rejects_malformed_work_reference_schema(app, patch, reason):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'schema-guarded')
    plan(app, owner)
    receipts_before = retained_bytes(app)
    count = len(app.state['sessions'])
    with pytest.raises(AppError, match=reason) as rejected:
        await commission(app, owner, identity='invalid-work-schema',
                         workReferences=[reference(item, **patch)])
    assert rejected.value.status == 400
    assert 'Unknown action' not in str(rejected.value)
    assert len(app.state['sessions']) == count and not app.runtime.inputs
    assert retained_bytes(app) == receipts_before
    await assert_available_commission(app, owner, item, 'valid-work-schema')


async def test_linked_nonowner_cannot_commission_as_work_owner(app):
    owner, peer = app.state['sessions']
    item = await prepared_work(app, owner, 'owned')
    item = (await change_work(app, owner, 'link', item, 'peer-reviewer',
        linkedSessionId=peer['id'], role='reviewer', candidate=item['candidate']))['result']['item']
    await generation(app, peer, 'peer-generation', ['peer-input'])
    plan(app, peer)
    before = await read(app, owner, item['id'])
    receipts_before = retained_bytes(app)
    with pytest.raises(AppError, match='owner') as rejected:
        await commission(app, peer, identity='not-owner-commission', workReferences=[reference(item)])
    assert rejected.value.status == 403
    assert await read(app, owner, item['id']) == before and not app.runtime.inputs
    assert retained_bytes(app) == receipts_before
    await assert_available_commission(app, owner, item, 'actual-owner-commission')


@pytest.mark.parametrize('change', ['promise', 'source_stop', 'unchanged'])
async def test_creation_revalidates_work_after_await_before_initial_input(app, monkeypatch, change):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'changing-before-input')
    plan(app, owner)
    entered, release = asyncio.Event(), asyncio.Event()
    identity = 'delayed-work-commission'
    dispatch, finish_create = app.dispatch, app.collaboration.finish_create
    tasks = []

    async def track_finish(request_id):
        if request_id == identity:
            tasks.append(asyncio.current_task())
        return await finish_create(request_id)

    async def hold_created_root(action, *args, **kwargs):
        result = await dispatch(action, *args, **kwargs)
        command = kwargs.get('command_id', args[2] if len(args) > 2 else None)
        if action == 'session.create' and command == identity + ':create':
            # Hold the RETURN from the actual awaited root creation. The root
            # and its receipt already exist; association/send must not exist yet.
            with sqlite3.connect(app.db.execute('PRAGMA database_list').fetchone()[2]) as db:
                root = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?',
                                            (command,)).fetchone()[0])
                creation = json.loads(db.execute('SELECT receipt FROM commands WHERE id=?',
                                                (identity,)).fetchone()[0])
                assert root['accepted'] and root['sessionId'] == result['sessionId']
                assert creation['phase'] == 'creating_root'
                assert not creation.get('workAssociation')
                assert not db.execute('SELECT 1 FROM commands WHERE id=?',
                                      (creation['initialInputId'],)).fetchone()
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(app.collaboration, 'finish_create', track_finish)
    monkeypatch.setattr(app, 'dispatch', hold_created_root)
    try:
        created = await commission(app, owner, identity=identity,
                                   workReferences=[reference(item)], returnNotification=False)
        await asyncio.wait_for(entered.wait(), 2)
        assert len(tasks) == 1 and not tasks[0].done() and not app.runtime.inputs
        if change == 'source_stop':
            await app.dispatch('conversation.stop', {'sessionId': owner['id']},
                               command_id='stop-during-work-creation')
        elif change == 'promise':
            await revise(app, owner, item, 'scope-before-initial-input',
                         source=source(app, owner, 'The promise changed before admission.'),
                         constraints=['New current condition.'])
    finally:
        # Always release and join this exact creation task, even when an oracle
        # fails. A timeout cancels/joins only our fixture task, never peer tasks.
        release.set()
        for task in tasks:
            try:
                await asyncio.wait_for(asyncio.shield(task), 5)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
    if change == 'unchanged':
        await initial_accepted(app, created['initialInputId'])
        retained = app.collaboration.receipt(created['requestId'])
        assert retained['workAssociation']['initialInputId'] == created['initialInputId']
        assert len(app.runtime.inputs) == 1
        return
    await until(lambda: app.collaboration.receipt(created['requestId'])['delivery']
                in {'suppressed', 'unknown'})
    retained = app.collaboration.receipt(created['requestId'])
    assert retained['delivery'] == 'suppressed'
    assert not retained.get('workAssociation')
    assert not app.runtime.inputs
    initial = app.db.execute('SELECT receipt FROM commands WHERE id=?',
                             (created['initialInputId'],)).fetchone()
    if initial:
        assert json.loads(initial[0]).get('delivery') not in {'accepted', 'applied', 'submitting'}
    assert retained['requestId'] == created['requestId']  # No replacement request.


async def test_duplicate_work_references_do_not_create_duplicate_association(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'duplicate')
    plan(app, owner)
    before = await read(app, owner, item['id'])
    with pytest.raises(AppError, match='distinct|duplicate') as rejected:
        await commission(app, owner, identity='duplicate-work-references',
                         workReferences=[reference(item), reference(item)])
    assert rejected.value.status == 400
    assert await read(app, owner, item['id']) == before
    assert not app.runtime.inputs
    await assert_available_commission(app, owner, item, 'single-work-reference')


@pytest.mark.parametrize('binding,status,reason', [
    ({'principal': 'synthetic-child'}, 403, '(?i)child|principal|root'),
    ({'generation_id': 'old-generation'}, 409, '(?i)generation'),
], ids=['binding0', 'binding1'])
async def test_invocation_read_rejects_borrowed_or_stale_root_binding(app, binding, status, reason):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'bound')
    _, child = await commissioned(app, owner, item)
    inputs = copy.deepcopy(app.runtime.inputs)
    request = {'action': 'workspace.work.invocation', 'args': {'sessionId': child['id']}}
    valid = await app.app_bridge('dispatch', {**request, **transport(child)}, child['id'])
    assert valid['result']['status'] == 'available' and valid['result']['workCount'] == 1
    with pytest.raises(AppError, match=reason) as rejected:
        await app.app_bridge('dispatch', {**request, **transport(child, **binding)}, child['id'])
    assert rejected.value.status == status
    assert 'Unknown action' not in str(rejected.value)
    again = await app.app_bridge('dispatch', {**request, **transport(child)}, child['id'])
    assert again['result']['version'] == valid['result']['version']
    assert app.runtime.inputs == inputs


async def test_transport_input_not_observed_delivered_cannot_select_relevant_work(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'bound')
    _, child = await commissioned(app, owner, item)
    assert (await current_work(app, child))['required']['status'] == 'available'
    required = (await current_work(app, child, inputs=['not-delivered']))['required']
    assert required['status'] == 'unavailable'
    assert required['reason'] and required['version'] is None
    assert required['effectAuthorized'] is False


async def test_mixed_delivered_inputs_cannot_hide_unsupported_lineage(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'mixed')
    created, child = await assert_available_commission(app, owner, item, 'mixed-bound-commission')
    unsupported = 'ordinary-followup-without-work-association'
    queued = await agent_action(app, owner, 'coordination.send', {
        'sessionId': child['id'], 'text': 'An ordinary untyped follow-up.', 'mode': 'queue'}, unsupported)
    assert queued['delivery'] == 'queued' and child['status'] == 'working'
    assert [entry[1] for entry in app.runtime.inputs] == [created['initialInputId']]
    # The existing runtime fixture leaves accepted work busy. Settle that exact
    # generation through the host's lifecycle events; do not bypass queue guards
    # or write a successful admission/delivery into its receipt.
    previous_generation = child['collaborationGeneration']['id']
    await app.on_runtime_event('runtime.generation', {
        'sessionId': child['id'], 'rootSessionId': child['id'],
        'generation_id': previous_generation, 'event': 'generation.finished',
        'input_ids': [created['initialInputId']],
        'disposition': 'manager_turn_finished', 'active_job_ids': []})
    assert child['collaborationGeneration']['terminal'] is True
    assert child['collaborationGeneration']['completed'] is True
    await app.on_runtime_event('runtime.status', {'sessionId': child['id'], 'status': 'idle'})
    await settled(app, unsupported, 'accepted')
    assert [entry[1] for entry in app.runtime.inputs] == [created['initialInputId'], unsupported]
    assert not app.collaboration.receipt(unsupported).get('workAssociationRef')
    # Both inputs are truly host-observed as delivered. A forged transport-only
    # input or an undelivered queue entry would miss the subset-dropping defect.
    await generation(app, child, 'mixed-delivered-generation',
                     [created['initialInputId'], unsupported])
    active_inputs = child['collaborationGeneration']['inputIds']
    assert {created['initialInputId'], unsupported} <= set(active_inputs)
    inputs_before = copy.deepcopy(app.runtime.inputs)
    required = (await current_work(app, child))['required']
    assert required['status'] == 'unavailable' and required['version'] is None
    assert required['reason'] and required['effectAuthorized'] is False
    assert set(required['inputIds']) == {created['initialInputId'], unsupported}
    assert required['coverage']['complete'] is False
    assert required['coverage']['supportedInputIds'] == [created['initialInputId']]
    assert required['coverage']['unsupportedInputs'] == [
        {'inputId': unsupported, 'reason': 'untyped_followup'}]
    assert app.runtime.inputs == inputs_before


@pytest.mark.parametrize('projection', [
    'initial-only', 'last-eight', 'shortened-seventeen', 'empty-seventeen',
    'complete-sixteen', 'complete-seventeen'])
async def test_shortened_delivered_input_projection_is_explicitly_incomplete(app, projection):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'projection')
    created, child = await assert_available_commission(app, owner, item, 'projection-commission')
    delivered = [created['initialInputId']]
    followups = 16 if projection in {'shortened-seventeen', 'empty-seventeen', 'complete-seventeen'} else 15 if projection == 'complete-sixteen' else 8
    for index in range(followups):
        identity = f'projection-followup-{index}'
        queued = await agent_action(app, owner, 'coordination.send', {
            'sessionId': child['id'], 'text': 'Ordinary untyped follow-up.', 'mode': 'queue'}, identity)
        assert queued['delivery'] == 'queued'
        await app.on_runtime_event('runtime.generation', {
            'sessionId': child['id'], 'rootSessionId': child['id'],
            'generation_id': child['collaborationGeneration']['id'], 'event': 'generation.finished',
            'input_ids': delivered, 'disposition': 'manager_turn_finished', 'active_job_ids': []})
        await app.on_runtime_event('runtime.status', {'sessionId': child['id'], 'status': 'idle'})
        await settled(app, identity, 'accepted')
        delivered.append(identity)
    assert [row[1] for row in app.runtime.inputs] == delivered
    await generation(app, child, 'complete-delivered-set', delivered)
    before = retained_bytes(app)
    supplied = (delivered[:1] if projection == 'initial-only' else []
                if projection == 'empty-seventeen' else delivered
                if projection.startswith('complete-') else delivered[-8:])
    result = (await current_work(app, child, inputs=supplied))['required']
    assert result['status'] == 'unavailable' and result['version'] is None
    assert result['effectAuthorized'] is False
    assert set(result['inputIds']) == set(delivered)
    if projection in {'complete-sixteen', 'complete-seventeen'}:
        assert 'reason' not in result['coverage']
        assert result['coverage']['supportedInputIds'] == [created['initialInputId']]
        assert {row['inputId'] for row in result['coverage']['unsupportedInputs']} == set(delivered[1:])
    else:
        assert result['coverage']['reason'] == 'input_projection_incomplete'
        assert result['coverage']['missingInputIds'] == sorted(set(delivered) - set(supplied))
    assert result['coverage']['complete'] is False
    assert retained_bytes(app) == before
    complete = (await current_work(app, child))['required']
    assert complete['status'] == 'unavailable' and complete['version'] is None
    assert set(complete['inputIds']) == set(delivered)
    assert complete['coverage']['supportedInputIds'] == [created['initialInputId']]
    assert {row['inputId'] for row in complete['coverage']['unsupportedInputs']} == set(delivered[1:])


async def test_large_native_identity_is_preserved_without_qualifying_unsupported_lineage(app):
    from amplifier_web.coordination_delivery import _work_reference
    owner = app.state['sessions'][0]
    ids = [f'host-observed-input-{index:04}-' + 'x' * 100 for index in range(257)]
    await generation(app, owner, 'large-native-input-set', ids)
    before = retained_bytes(app)
    work = await current_work(app, owner)
    required = work['required']
    assert len(json.dumps(work).encode()) > 12 * 1024
    assert work['available'] and required['status'] == 'unavailable'
    assert required['inputIds'] == ids
    assert required['coverage']['complete'] is False
    assert [row['inputId'] for row in required['coverage']['unsupportedInputs']] == ids
    assert _work_reference(work) == work
    # Manifest metadata also remains complete; no body, grant or association is
    # fabricated for inputs that have no commissioned Work relationship.
    page = (await agent_action(app, owner, 'workspace.work.invocation', {
        'sessionId': owner['id']}, 'large-manifest'))['result']
    assert page['inputIds'] == ids and not page['items']
    assert page['status'] == 'unavailable' and page['effectAuthorized'] is False
    assert retained_bytes(app) == before


async def test_large_promise_read_is_exact_paged_unicode_and_stale_read_refuses(app):
    owner = app.state['sessions'][0]
    constraints = ['Current condition ' + str(i) + ': ' + '\u03bb' * 900 for i in range(30)]
    interpretation = 'Retain the complete agreed scope: caf\u00e9, e\u0301 and \U0001d11e.\nNo omitted fields.'
    criteria = [{'id': 'retained', 'text': 'Retain every Unicode code point: \u03bb \U0001d11e.'},
                {'id': 'exact', 'text': 'Keep criteria order, quotes "like this", and\nline breaks.'}]
    original = source(app, owner, 'Original large-scope request: \u03bb \U0001d11e e\u0301.')
    item = await prepared_work(app, owner, 'large', original=original)
    event = retained_source_event(app, item['id'], original)
    assert event['kind'] == 'create' and event['promiseRevision'] == 1
    correction = source(app, owner, 'Use this complete expanded scope: \u03bb \U0001d11e e\u0301.')
    item = (await revise(app, owner, item, 'expanded-large-scope', source=correction,
                        interpretation=interpretation, criteria=criteria,
                        constraints=constraints, commitment='accepted'))['result']['item']
    promise_event = retained_source_event(app, item['id'], correction)
    assert promise_event['kind'] == 'revise' and promise_event['promiseRevision'] == 2
    assert event['id'] != promise_event['id']
    expected = {'interpretation': interpretation, 'criteria': criteria, 'constraints': constraints,
                'commitment': 'accepted', 'promiseRevision': 2,
                'originalSourceRef': event['id'], 'promiseEventRef': promise_event['id']}
    expected_text = canonical(expected)
    expected_hash = hashlib.sha256(expected_text.encode('utf-8')).hexdigest()
    _, child = await commissioned(app, owner, item)
    current = (await read(app, owner, item['id']))['item']
    args = {'workspaceId': workspace_id(app, owner), 'id': item['id'],
            'expectedRevision': current['revision'], 'expectedPromiseRevision': current['promiseRevision'],
            'limit': 1000}
    parts, offset = [], 0
    for _ in range(100):
        page = (await agent_action(app, child, 'workspace.work.promise',
                                  {**args, 'offset': offset}, 'promise-page'))['result']
        assert page['offset'] == offset
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 12 * 1024
        assert page['sha256'] == expected_hash
        assert page['total'] == len(expected_text)
        assert page['revision'] == current['revision'] and page['promiseRevision'] == 2
        assert page['workspaceId'] == args['workspaceId'] and page['id'] == item['id']
        assert page['originalSourceRef'] == event['id'] and page['promiseEventRef'] == promise_event['id']
        assert page['text'] == expected_text[offset:offset + args['limit']]
        parts.append(page['text'])
        end = offset + len(page['text'])
        assert page['truncated'] is (end < len(expected_text))
        assert page['nextOffset'] == (end if end < len(expected_text) else None)
        if end == len(expected_text):
            break
        assert end > offset
        offset = page['nextOffset']
    else:
        pytest.fail('Promise pagination made no bounded completion')
    text = ''.join(parts)
    assert len(parts) > 1 and text == expected_text
    assert hashlib.sha256(text.encode('utf-8')).hexdigest() == expected_hash
    assert json.loads(text) == expected  # Entire independently seeded scope, not a substring.
    await revise(app, owner, current, 'change-current-condition',
                 source=source(app, owner, 'A corrected current condition.'), constraints=['Corrected scope.'])
    with pytest.raises(AppError, match='changed|revision') as rejected:
        await agent_action(app, child, 'workspace.work.promise', args, 'stale-promise-page')
    assert rejected.value.status == 409
    revised = (await read(app, owner, item['id']))['item']
    fresh = (await agent_action(app, child, 'workspace.work.promise', {
        **args, 'expectedRevision': revised['revision'],
        'expectedPromiseRevision': revised['promiseRevision']}, 'fresh-promise-page'))['result']
    assert fresh['promiseRevision'] == 3 and fresh['revision'] == revised['revision']


async def test_required_source_manifest_keeps_original_and_earlier_corrections(app):
    owner = app.state['sessions'][0]
    original_text = 'Exact original request\n' + 'caf\u00e9|\U0001d11e|e\u0301|\u03bb\n' * 1000
    assert len(original_text) > 8000  # Greater than the existing source reader's default page.
    original = source(app, owner, original_text)
    item = await prepared_work(app, owner, 'corrected', original=original)
    expected_bodies = {retained_source_event(app, item['id'], original)['id']: original_text}
    for n in range(4):
        correction = 'Exact correction ' + str(n) + ': \u03bb \U0001d11e e\u0301\nKeep prior conditions.'
        message = source(app, owner, correction)
        item = (await revise(app, owner, item, 'correction-' + str(n),
            source=message,
            constraints=['Current condition ' + str(n)]))['result']['item']
        expected_bodies[retained_source_event(app, item['id'], message)['id']] = correction
    created, child = await commissioned(app, owner, item)
    header = (await current_work(app, child))['required']
    args = {'sessionId': child['id'], 'expectedVersion': header['version'], 'limit': 2}
    requirements = []
    for _ in range(10):
        page = (await agent_action(app, child, 'workspace.work.invocation', args, 'requirements'))['result']
        assert page['status'] == 'available' and page['version'] == header['version']
        assert page['workCount'] == 1 and page['requirementCount'] == 6
        assert len(page['items']) <= 2
        assert page['truncated'] is bool(page['nextAfter'])
        requirements.extend(page['items'])
        if not page['nextAfter']:
            break
        args['after'] = page['nextAfter']
    else:
        pytest.fail('Requirement manifest did not terminate')
    sources = [row for row in requirements if row['kind'] == 'source']
    assert len(requirements) == 6 and len(sources) == 5
    assert len([row for row in requirements if row['kind'] == 'promise']) == 1
    assert {row['readRef']['args']['sourceId'] for row in sources} == set(expected_bodies)
    observed_bodies, page_counts = {}, {}
    for row in sources:
        handle = row['readRef']
        assert handle['action'] == 'workspace.work.source'
        identity = handle['args']['sourceId']
        expected = expected_bodies[identity]
        digest = hashlib.sha256(expected.encode('utf-8')).hexdigest()
        assert row['contentSha256'] == digest
        parts, offset = [], 0
        for _ in range(100):
            page = (await agent_action(app, child, handle['action'],
                {**handle['args'], 'offset': offset, 'limit': 1000},
                'required-source'))['result']['source']
            assert page['offset'] == offset and page['total'] == len(expected)
            assert page['sha256'] == digest
            assert page['text'] == expected[offset:offset + 1000]
            parts.append(page['text'])
            end = offset + len(page['text'])
            assert page['truncated'] is (end < len(expected))
            assert page['nextOffset'] == (end if end < len(expected) else None)
            if end == len(expected):
                break
            assert end > offset
            offset = page['nextOffset']
        else:
            pytest.fail('Required source pagination did not terminate')
        observed_bodies[identity] = ''.join(parts)
        page_counts[identity] = len(parts)
        assert hashlib.sha256(observed_bodies[identity].encode('utf-8')).hexdigest() == digest
    assert observed_bodies == expected_bodies
    assert max(page_counts.values()) > 1
    assert header['inputIds'] == [created['initialInputId']]


@pytest.mark.parametrize('change', ['promise', 'metadata', 'stop'])
async def test_currentness_revalidation_detects_changed_facts_without_authorizing_effect(app, change):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'revalidate')
    _, child = await commissioned(app, owner, item)
    version = (await current_work(app, child))['required']['version']
    before = await revalidate(app, child, version)
    assert before['current'] is True and before['effectAuthorized'] is False
    await asyncio.sleep(0)  # An actual request boundary, not a fabricated grant.
    current = (await read(app, owner, item['id']))['item']
    if change == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': child['id']}, command_id='stop-bound-child')
    else:
        patch = ({'source': source(app, owner, 'Correct the current conditions.'),
                  'constraints': ['New required condition']} if change == 'promise'
                 else {'blockedReason': 'A new blocker must remain current.'})
        await revise(app, owner, current, 'revise-before-effect', **patch)
    after = await revalidate(app, child, version)
    assert after['current'] is False and after['effectAuthorized'] is False
    assert len(app.runtime.inputs) == 1  # No input/effect replay by a read.


async def test_required_manifest_cursor_rejects_new_scope(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'cursor')
    _, child = await commissioned(app, owner, item)
    version = (await current_work(app, child))['required']['version']
    args = {'sessionId': child['id'], 'expectedVersion': version, 'limit': 1}
    first = (await agent_action(app, child, 'workspace.work.invocation', args, 'first-manifest'))['result']
    assert first['nextAfter']
    item = (await read(app, owner, item['id']))['item']
    await revise(app, owner, item, 'correction-between-pages',
                 source=source(app, owner, 'Change after page one.'), constraints=['Changed condition.'])
    with pytest.raises(AppError, match='changed|version'):
        await agent_action(app, child, 'workspace.work.invocation',
                           {**args, 'after': first['nextAfter']}, 'old-manifest-tail')


async def test_legacy_commission_has_unavailable_not_empty_authority(app):
    owner = app.state['sessions'][0]
    plan(app, owner)
    created = await commission(app, owner, returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    child = app._session(created['sessionId'])
    await generation(app, child, 'legacy-generation', [created['initialInputId']])
    required = (await current_work(app, child))['required']
    assert required['status'] == 'unavailable' and required['reason']
    assert required['version'] is None and required['effectAuthorized'] is False


async def test_missing_association_receipt_stays_unavailable_without_replay(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'missing')
    created, child = await commissioned(app, owner, item)
    app.db.execute('DELETE FROM commands WHERE id=?', (created['requestId'],))
    app.db.commit()  # Synthetic missing-receipt fault, not a public deletion route.
    required = (await current_work(app, child))['required']
    assert required['status'] == 'unavailable' and required['reason']
    assert required['effectAuthorized'] is False and len(app.runtime.inputs) == 1


async def test_restart_retains_lineage_but_does_not_replay_or_reuse_old_generation(app, monkeypatch):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'restart')
    created, child = await commissioned(app, owner, item)
    before = copy.deepcopy(app.collaboration.receipt(created['requestId'])['workAssociation'])
    binding = transport(child)
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail('Restart inspection hydrated history or started a worker')
        monkeypatch.setattr(restored.history, 'ensure_loaded', forbidden)
        monkeypatch.setattr(Management, 'ensure_runtime', forbidden)
        assert restored.collaboration.receipt(created['requestId'])['workAssociation'] == before
        result = await restored.app_bridge('coordination.current', binding, child['id'])
        assert result['work']['required']['status'] == 'unavailable'
        assert result['work']['required']['effectAuthorized'] is False
        assert not restored.runtime.inputs
    finally:
        await restored.close()


@pytest.mark.parametrize('failure_at', ['link_event', 'initial_receipt'])
async def test_initial_work_transaction_rolls_back_before_admission(app, monkeypatch, failure_at):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'atomic-failure')
    before = retained_bytes(app)
    work_receipts = {key: raw for key, raw in before.items()
                     if key == item['id'] or json.loads(raw).get('workItemId') == item['id']}
    insert = app.collaboration.insert
    injected = []

    def fail_after_write(identity, digest, receipt):
        insert(identity, digest, receipt)
        child_link = (receipt.get('commandAction') == 'workspace.work.event'
                      and receipt.get('kind') == 'link'
                      and receipt.get('link', {}).get('sessionId') != owner['id'])
        initial = identity == 'atomic-failure-commission:brief'
        if not injected and (child_link if failure_at == 'link_event' else initial):
            injected.append(identity)
            raise RuntimeError('Synthetic failure after staged ' + failure_at)

    monkeypatch.setattr(app.collaboration, 'insert', fail_after_write)
    plan(app, owner)
    created = await commission(app, owner, identity='atomic-failure-commission',
                               workReferences=[reference(item)], returnNotification=False)
    await until(lambda: app.collaboration.receipt(created['requestId'])['delivery'] == 'unknown')
    assert len(injected) == 1 and not app.runtime.inputs
    after = retained_bytes(app)
    assert {key: raw for key, raw in after.items()
            if key == item['id'] or json.loads(raw).get('workItemId') == item['id']} == work_receipts
    assert created['initialInputId'] not in after
    assert not json.loads(after[created['requestId']]).get('workAssociation')
    child = app._session(created['sessionId'])
    assert not any(message.get('inputId') == created['initialInputId'] for message in child['messages'])
    repeated = await commission(app, owner, identity='atomic-failure-commission',
                                workReferences=[reference(item)], returnNotification=False)
    assert repeated['duplicate'] and repeated['delivery'] == 'unknown'
    assert retained_bytes(app) == after and not app.runtime.inputs


async def test_eight_work_manifest_pages_remain_bounded_and_complete(app):
    owner = app.state['sessions'][0]
    items = [await prepared_work(app, owner, 'many-' + str(n),
             title='\U0001d11e' * 200, interpretation='\u03bb' * 4000) for n in range(8)]
    plan(app, owner)
    created = await commission(app, owner, identity='eight-work-commission',
                               workReferences=[reference(item) for item in items], returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    child = app._session(created['sessionId'])
    await generation(app, child, 'eight-work-generation', [created['initialInputId']])
    packet = await current_work(app, child)
    assert len(json.dumps(packet, ensure_ascii=False).encode()) <= 12 * 1024
    header = packet['required']
    assert header['status'] == 'available' and header['workCount'] == 8 and header['requirementCount'] == 16
    args = {'sessionId': child['id'], 'expectedVersion': header['version'], 'limit': 3}
    all_rows = []
    for _ in range(16):
        result = await agent_action(app, child, 'workspace.work.invocation', args, 'eight-manifest')
        assert len(json.dumps(result, ensure_ascii=False).encode()) <= 12 * 1024
        page = result['result']
        assert page['items'] and page['version'] == header['version']
        all_rows.extend(page['items'])
        if page['nextAfter'] is None:
            break
        args['after'] = page['nextAfter']
    else:
        pytest.fail('Eight-item manifest did not make bounded progress')
    assert len(all_rows) == 16
    assert {(row['workId'], row['kind']) for row in all_rows} == {
        (item['id'], kind) for item in items for kind in ['promise', 'source']}


async def test_missing_middle_correction_is_incomplete_not_silently_omitted(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'missing-middle')
    middle = None
    for n in range(3):
        message = source(app, owner, 'Correction ' + str(n))
        item = (await revise(app, owner, item, 'middle-correction-' + str(n),
                            source=message, constraints=['Condition ' + str(n)]))['result']['item']
        if n == 1:
            middle = retained_source_event(app, item['id'], message)['id']
    _, child = await commissioned(app, owner, item)
    assert (await current_work(app, child))['required']['status'] == 'available'
    app.db.execute('DELETE FROM commands WHERE id=?', (middle,))
    app.db.commit()  # Labelled corrupt-history fault, never a positive association.
    required = (await current_work(app, child))['required']
    assert required['status'] == 'unavailable' and required['version'] is None
    assert 'source' in required['reason'] and required['coverage']['complete'] is False
    assert required['workCount'] is None and required['effectAuthorized'] is False
    assert len(app.runtime.inputs) == 1


async def test_association_link_corruption_fails_without_replay(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'corrupt-link')
    created, child = await commissioned(app, owner, item)
    assert (await current_work(app, child))['required']['status'] == 'available'
    operation = app.collaboration.receipt(created['requestId'])
    # Fault injection changes an existing canonical event, not a fabricated success.
    event_id = operation['workAssociation']['references'][0]['linkEventId']
    event = app.collaboration.receipt(event_id)
    event['link']['candidate'] = 'c' * 40
    app.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(event), event_id))
    app.db.commit()
    required = (await current_work(app, child))['required']
    assert required['status'] == 'unavailable' and required['version'] is None
    assert required['coverage']['complete'] is False and required['effectAuthorized'] is False
    assert len(app.runtime.inputs) == 1


async def test_managed_chat_preserves_coordination_when_work_is_unavailable(app):
    created = await app.dispatch('session.create', {'location': {'kind': 'managed'}})
    actor = app._session(created['sessionId'])
    assert actor['location']['kind'] == 'managed'
    assert actor['workspace'] not in {row['path'] for row in app.state['workspaces']}
    await generation(app, actor, 'managed-work-observation', ['managed-input'])
    before = retained_bytes(app)
    inputs = copy.deepcopy(app.runtime.inputs)
    expected = app.collaboration.current(actor['id'])
    observed = await app.app_bridge('coordination.current', transport(actor), actor['id'])
    assert observed['sessionId'] == actor['id']
    assert observed['requests'] == expected['requests'] and observed['grants'] == expected['grants']
    assert observed['awareness']['actor']['rootSessionId'] == actor['id']
    assert observed['assignment'] == app.assignments.summary(actor)
    assert observed['work']['available'] is False
    assert observed['work']['required']['status'] == 'unavailable'
    assert observed['work']['required']['effectAuthorized'] is False
    assert retained_bytes(app) == before and app.runtime.inputs == inputs


async def test_oversized_work_projection_preserves_other_coordination(app, monkeypatch):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'oversized-projection')
    _, child = await commissioned(app, owner, item)
    assert (await current_work(app, child))['required']['status'] == 'available'
    original = app.workspace_work.listing

    def oversized(*args, **kwargs):
        return {**original(*args, **kwargs), 'detail': 'x' * (12 * 1024)}

    monkeypatch.setattr(app.workspace_work, 'listing', oversized)
    before = retained_bytes(app)
    observed = await app.app_bridge('coordination.current', transport(child), child['id'])
    assert observed['awareness']['actor']['rootSessionId'] == child['id']
    assert 'requests' in observed and 'assignment' in observed
    assert observed['work']['available'] is False
    assert 'byte bound' in observed['work']['required']['reason']
    assert observed['work']['required']['effectAuthorized'] is False
    assert retained_bytes(app) == before and len(app.runtime.inputs) == 1
