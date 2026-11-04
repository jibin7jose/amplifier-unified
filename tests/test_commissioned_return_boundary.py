"""Later commissioned reports keep evidence and execution authority separate."""
import asyncio
import copy

import pytest

from amplifier_operations.coordination import peer_input
from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import (
    Runtime, app, agent_action, finish, generation, settled,
)
from test_peer_creation_operation import commission, initial_accepted, plan


async def commissioned_later_generation(app, *, with_task=True):
    source = app.state['sessions'][0]
    if with_task:
        source['task'] = {'id': 'same-objective', 'revision': 1, 'continuationEpoch': 1,
                          'status': 'active', 'objective': 'Review independent candidates.'}
    plan(app, source)
    created = await commission(app, source)
    await initial_accepted(app, created['initialInputId'])
    target = app._session(created['sessionId'])
    await generation(app, target, 'recipient-' + created['initialInputId'], ['current-followup'])
    return source, target, created


async def later_report(app, target, created):
    return await agent_action(app, target, 'coordination.reply', {
        'requestId': created['initialInputId'], 'kind': 'result', 'outcome': 'success',
        'references': ['urn:fixture:candidate'],
        'text': 'Return the existing candidate; no effects repeated.'}, 'later-report')


async def test_commissioned_return_survives_sibling_and_same_objective_progress(app):
    source, target, created = await commissioned_later_generation(app)
    sibling = await commission(app, source, 'sibling', returnNotification=False)
    await initial_accepted(app, sibling['initialInputId'])
    source['task'].update(revision=3, corrections=[{'text': 'Track sibling progress separately.'}])
    before = copy.deepcopy(source['task'])
    returned = await later_report(app, target, created)
    assert returned['accepted'] and returned['result']['responseRevision'] == 1
    assert returned['result']['status'] == 'staged' and not returned['result']['qualified']
    await finish(app, target, created['initialInputId'], input_ids=['current-followup'])
    request = app.collaboration.receipt(created['initialInputId'])
    assert request['response']['qualified']
    notification = app.collaboration.receipt(created['subscription']['continuationId'])
    assert notification['dependencySatisfied'] and notification['delivery'] == 'queued'
    assert source['task'] == before  # Report delivery is not task completion.
    assert len(app.runtime.inputs) == 2  # Both original workers; no re-admission.


@pytest.mark.parametrize('phase', ['preparation', 'initial_input'])
async def test_progress_still_fences_unperformed_creation_and_initial_input(app, monkeypatch, phase):
    source = app.state['sessions'][0]
    source['task'] = {'id': 'same-objective', 'revision': 1, 'continuationEpoch': 1,
                      'status': 'active', 'objective': 'Review independent candidates.'}
    plan(app, source)
    entered, release = asyncio.Event(), asyncio.Event()
    method = 'finish_create' if phase == 'preparation' else 'drain'
    original = getattr(app.collaboration, method)

    async def delayed(identity):
        entered.set()
        await release.wait()
        return await original(identity)

    monkeypatch.setattr(app.collaboration, method, delayed)
    created = await commission(app, source)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        source['task']['revision'] = 3
    finally:
        release.set()
    receipt_id = created['requestId'] if phase == 'preparation' else created['initialInputId']
    receipt = await settled(app, receipt_id, 'suppressed')
    assert 'requesting assignment changed' in receipt['detail']
    if phase == 'preparation':
        assert not app.runtime.inputs
        assert len(app.state['sessions']) == 2
    else:
        # The failed initial input is never delivered to its worker. Its exact
        # host-attributed return to the commissioner is a separate input.
        continuation = created['subscription']['continuationId']
        notice = await settled(app, continuation, 'accepted')
        assert [(sid, identity) for sid, identity, _ in app.runtime.inputs] == [(source['id'], continuation)]
        assert continuation != created['initialInputId']
        assert notice['requestId'] == notice['inputId'] == continuation
        assert notice['senderSessionId'] == created['sessionId']
        assert notice['target']['sessionId'] == source['id']
        assert notice['origin'] == 'system' and notice['mode'] == 'auto'
        assert notice['dependencyRequestId'] == created['initialInputId']
        assert notice['dependencySatisfied'] is False and notice['continuationReason'] == 'failed'
        assert notice['outcomeSnapshot']['status'] == 'observed'
        assert notice['outcomeSnapshot']['outcome'] == 'failed' and notice['outcomeSnapshot']['qualified'] is False
        assert notice['admission']['accepted'] is True
        message, = [row for row in source['messages'] if row.get('inputId') == continuation]
        assert message['peerEnvelope']['replyToRequestId'] == created['initialInputId']
        assert message['peerEnvelope']['recipientSessionId'] == source['id']
        assert app.runtime.inputs[0][2] == peer_input(message['peerEnvelope'], message['text'])
        assert app.collaboration.receipt(receipt_id)['delivery'] == 'suppressed'


async def test_return_survives_recreation_without_replaying_commissioned_work(app):
    source, target, created = await commissioned_later_generation(app)
    source['task']['revision'] = 3
    await later_report(app, target, created)
    # Crash boundary: terminal report is durable before its independent notice.
    original = app.collaboration.enqueue_continuation
    app.collaboration.enqueue_continuation = lambda request: None
    await finish(app, target, created['initialInputId'], input_ids=['current-followup'])
    app.collaboration.enqueue_continuation = original
    source['status'] = 'idle'
    source['collaborationGeneration']['terminal'] = True
    app._save()
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        assert not reopened.runtime.inputs
        creation = reopened.collaboration.receipt(created['requestId'])
        assert creation['sourceTaskContinuationEpoch'] == 1
        assert reopened.collaboration.receipt(created['initialInputId'])['response']['status'] == 'sealed'
        reopened.collaboration.start()
        continuation = created['subscription']['continuationId']
        await settled(reopened, continuation, 'accepted')
        assert [(row[0], row[1]) for row in reopened.runtime.inputs] == [(source['id'], continuation)]
        reopened.collaboration.start()
        assert len(reopened.runtime.inputs) == 1
        assert reopened.collaboration.receipt(created['initialInputId'])['delivery'] == 'accepted'
        assert reopened._session(source['id'])['task']['status'] == 'active'
    finally:
        await reopened.close()


async def test_commissioned_return_without_explicit_task_still_works(app):
    source, target, created = await commissioned_later_generation(app, with_task=False)
    assert not source.get('task')
    assert (await later_report(app, target, created))['accepted']
    await finish(app, target, created['initialInputId'], input_ids=['current-followup'])
    assert app.collaboration.receipt(created['subscription']['continuationId'])['dependencySatisfied']


async def test_legacy_creation_keeps_return_when_exact_revision_is_unchanged(app):
    _, target, created = await commissioned_later_generation(app)
    operation = app.collaboration.receipt(created['requestId'])
    operation.pop('sourceTaskContinuationEpoch', None)
    app.collaboration.save(created['requestId'], operation)
    assert (await later_report(app, target, created))['accepted']


@pytest.mark.parametrize('change', [
    'objective', 'pause_resume', 'stop', 'placement', 'legacy', 'blocked', 'completed',
])
async def test_commissioned_return_keeps_authority_and_legacy_boundaries(app, change):
    source, target, created = await commissioned_later_generation(app)
    source['task']['revision'] = 3
    if change == 'objective':
        source['task'].update(id='replacement', continuationEpoch=3)
    elif change == 'pause_resume':
        source['task']['continuationEpoch'] = 3
    elif change == 'stop':
        source['interruptionRevision'] = 1
    elif change == 'placement':
        source['workingDirectory'] = source['workspace'] + '/different'
    elif change in {'blocked', 'completed'}:
        source['task']['status'] = change
    else:
        operation = app.collaboration.receipt(created['requestId'])
        operation.pop('sourceTaskContinuationEpoch', None)
        app.collaboration.save(created['requestId'], operation)
    before = copy.deepcopy(source['task'])
    with pytest.raises(AppError):
        await later_report(app, target, created)
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='later-report'").fetchone()
    assert source['task'] == before and len(app.runtime.inputs) == 1


async def test_retained_report_remains_readable_after_authority_changes(app):
    source, target, created = await commissioned_later_generation(app)
    source['task']['revision'] = 3
    await later_report(app, target, created)
    await finish(app, target, created['initialInputId'], input_ids=['current-followup'])
    source['task'].update(revision=4, continuationEpoch=4, status='blocked')
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    assert app.collaboration.receipt(created['subscription']['continuationId'])['delivery'] == 'suppressed'
    retained = await app.dispatch('coordination.result', {'requestId': created['requestId']})
    assert retained['result']['qualified']
    assert source['task']['status'] == 'blocked' and len(app.runtime.inputs) == 1


async def test_later_commissioned_report_then_atomic_optin_correction(app):
    source, target, created = await commissioned_later_generation(app)
    source['task']['revision'] = 2
    await later_report(app, target, created)
    await finish(app, target, created['initialInputId'], input_ids=['current-followup'])
    first_return = created['subscription']['continuationId']
    assert app.collaboration.receipt(first_return)['delivery'] == 'queued'

    target['status'] = 'idle'
    followup = await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Correct the adjacent invariant.',
        'returnNotification': True}, 'optin-correction')
    second_return = followup['subscription']['continuationId']
    assert second_return != first_return
    assert followup['returnState']['subscription']['registered'] is True
    assert 'creationOperationId' not in followup
    assert followup['subscription']['taskContinuationEpoch'] == 1
    await settled(app, 'optin-correction', 'accepted')
    await generation(app, target, 'recipient-optin-correction', ['optin-correction'])
    source['task']['revision'] = 3
    before = copy.deepcopy(source['task'])
    await agent_action(app, target, 'coordination.reply', {
        'requestId': 'optin-correction', 'kind': 'result', 'outcome': 'success',
        'references': ['urn:fixture:corrected-candidate'],
        'text': 'Adjacent invariant corrected.'}, 'correction-report')
    await finish(app, target, 'optin-correction')
    for identity in (first_return, second_return):
        source['status'] = 'idle'
        await app.collaboration.drain(source['id'])
        await settled(app, identity, 'accepted')
    for identity in (created['initialInputId'], 'optin-correction'):
        saved = await app.dispatch('coordination.result', {'requestId': identity}, include_state=False)
        assert saved['result']['qualified']
        assert saved['result']['receipt']['response']['requestId'] == identity
        assert saved['result']['returnState']['notification']['status'] == 'accepted'
    await app.collaboration.drain(source['id'])
    assert [row[1] for row in app.runtime.inputs] == [
        created['initialInputId'], 'optin-correction', first_return, second_return]
    assert source['task'] == before
