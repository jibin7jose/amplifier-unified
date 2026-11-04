"""Durable peer queue policy and fair admission; scripted runtime, no live models."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest

from amplifier_web import collaboration, test_execution_settings as settings
from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action, generation, send, settled, until
from test_coordination_superseded_returns import first_return, later_return
from test_peer_creation_operation import commission, plan


async def policy(app, **limits):
    current = (await app.dispatch('testing.execution.read', {}))['result']
    return await app.dispatch('testing.execution.save', {
        'expectedRevision': current['revision'], 'queue': current['queue'],
        'profiles': current['profiles'], 'concurrency': {**current['concurrency'], **limits}})


async def backlog(app, monkeypatch, count=66):
    # Real sends over simulated elapsed time retain the separate pair-rate guard.
    clock = [time.time()]
    monkeypatch.setattr(collaboration, 'time', SimpleNamespace(time=lambda: clock[0]))
    app.state['sessions'][1]['status'] = 'working'
    for index in range(count):
        clock[0] += 2.1
        await send(app, f'queued-{index}')
    return clock


async def test_default_inbox_accepts_beyond_former_sender_and_total_limits(app, monkeypatch):
    await backlog(app, monkeypatch)
    source, target = app.state['sessions']
    assert all(settings.concurrency(app)[key] == 0 for key in settings.PEER_INBOX_COUNTS)
    assert len(target['messages']) == 66 and not app.runtime.inputs
    assert all(app.collaboration.receipt(f'queued-{i}')['delivery'] == 'queued' for i in range(66))
    # Retained assignment attribution also no longer imposes a default 32 cap.
    for index in range(66):
        receipt = app.collaboration.receipt(f'queued-{index}')
        receipt['assignmentId'] = 'retained-assignment'
        app.collaboration.save(receipt['requestId'], receipt)
    app.collaboration.require_inbox_slot(target['id'], source['id'], 'retained-assignment')


async def test_unlimited_inbox_does_not_count_receipts_or_load_native_task(app, monkeypatch):
    statements = []
    app.db.set_trace_callback(statements.append)
    def unexpected(*args, **kwargs):
        pytest.fail('Uncapped inbox admission loaded a conversation/task')
    with monkeypatch.context() as scoped:
        scoped.setattr(app, '_session', unexpected)
        scoped.setattr(app.coordination, 'task', unexpected)
        app.collaboration.require_inbox_slot('recipient', 'sender', 'assignment')
    app.db.set_trace_callback(None)
    assert not statements


@pytest.mark.parametrize(('field', 'reason'), [
    ('maxPendingPeerInputs', 'inbox is full'),
    ('maxPendingPeerInputsPerSender', 'sender has 2 pending'),
    ('maxPendingPeerInputsPerAssignment', 'assignment has 2 pending'),
])
async def test_saved_positive_limits_are_optional_and_preserve_receipts(app, field, reason):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await send(app, 'one')
    await send(app, 'two')
    for identity in ('one', 'two'):
        receipt = app.collaboration.receipt(identity)
        receipt['assignmentId'] = 'retained-assignment'
        app.collaboration.save(identity, receipt)
    await policy(app, **{field: 2})
    before = app.db.execute('SELECT id,receipt FROM commands ORDER BY rowid').fetchall()
    with pytest.raises(AppError, match=reason) as error:
        app.collaboration.require_inbox_slot(target['id'], source['id'], 'retained-assignment')
    assert error.value.status == 429
    assert before == app.db.execute('SELECT id,receipt FROM commands ORDER BY rowid').fetchall()
    assert settings.load(app)['concurrency'][field] == 2
    await policy(app, **{field: 0})
    app.collaboration.require_inbox_slot(target['id'], source['id'], 'retained-assignment')
    assert not app.runtime.inputs


async def test_fair_sender_beyond_first_receipt_page_runs_next(app, monkeypatch):
    await backlog(app, monkeypatch, 64)
    source, target = app.state['sessions']
    other = app._new_session({'title': 'Independent'})
    app.state['sessions'].append(other)
    await generation(app, other, 'other-generation', ['current-input'])
    await agent_action(app, other, 'coordination.send', {'sessionId': target['id'], 'text': 'Independent work'}, 'outsider')
    assert app.db.execute("SELECT count(*) FROM commands WHERE json_extract(receipt,'$.delivery')='queued'").fetchone()[0] == 65
    for expected in ('queued-0', 'outsider', 'queued-1'):
        target['status'] = 'idle'
        await app.collaboration.drain(target['id'])
        assert app.runtime.inputs[-1][1] == expected


def test_fair_rows_handles_null_sender_and_bounds_receipt_loading(app):
    target = app.state['sessions'][1]
    # Scalar selection remains independent of large receipt bodies, including
    # host-created notifications without a sender field. No native history read.
    for sender, count in ((None, 70), ('other', 1)):
        for index in range(count):
            identity = f'{sender}-{index}'
            app.collaboration.insert(identity, identity, {
                'requestId': identity, 'commandAction': 'coordination.send',
                'target': {'sessionId': target['id']}, 'senderSessionId': sender,
                'delivery': 'queued', 'largeResult': 'x' * 65536})
    statements = []
    app.db.set_trace_callback(statements.append)
    rows = app.collaboration.fair_rows(target['id'])
    app.db.set_trace_callback(None)
    assert len(rows) == 64 and rows[0][0] == 'None-0'
    receipt = json.loads(rows[0][1])
    receipt.update(delivery='accepted', admissionOrder=1)
    app.collaboration.save('None-0', receipt)
    assert [r[0] for r in app.collaboration.fair_rows(target['id'])] == ['other-0']
    plan_rows = app.db.execute('EXPLAIN QUERY PLAN ' + statements[0]).fetchall()
    assert any('coordination_queued_sender' in row[-1] for row in plan_rows)
    assert any('coordination_sender_admission' in row[-1] for row in plan_rows)
    assert len(statements) == 2


async def test_exhausted_expired_page_does_not_strand_later_valid_work(app, monkeypatch):
    await backlog(app, monkeypatch, 65)
    target = app.state['sessions'][1]
    for index in range(64):
        receipt = app.collaboration.receipt(f'queued-{index}')
        receipt['expiresAt'] = 0
        app.collaboration.save(receipt['requestId'], receipt)
    target['status'] = 'idle'
    await app.collaboration.drain(target['id'])
    await settled(app, 'queued-64', 'accepted')
    assert [row[1] for row in app.runtime.inputs] == ['queued-64']
    assert all(app.collaboration.receipt(f'queued-{i}')['delivery'] == 'expired' for i in range(64))


async def test_unknown_admission_is_not_replayed_on_relaxed_or_repeated_save(app):
    target = app.state['sessions'][1]
    await policy(app, maxPendingPeerInputs=1)
    app.runtime.fail = True
    await send(app, 'uncertain')
    await settled(app, 'uncertain', 'unknown')
    before = app.db.execute("SELECT receipt FROM commands WHERE id='uncertain'").fetchone()[0]
    await policy(app, maxPendingPeerInputs=0)
    await policy(app, maxPendingPeerInputs=0)
    await asyncio.sleep(.05)
    assert app.db.execute("SELECT receipt FROM commands WHERE id='uncertain'").fetchone()[0] == before
    assert [row[1] for row in app.runtime.inputs] == ['uncertain']
    app.runtime.fail = False
    target['status'] = 'idle'
    await send(app, 'fresh')
    await settled(app, 'fresh', 'accepted')
    assert [row[1] for row in app.runtime.inputs] == ['uncertain', 'fresh']


async def test_relaxing_inbox_policy_resumes_saved_return_without_external_event(app):
    source, target, old_id = await first_return(app)
    await policy(app, maxPendingPeerInputs=2)
    new_id = await later_return(app, target)
    assert app.collaboration.receipt('result-request')['subscription']['status'] == 'backpressured'
    source['status'] = 'idle'
    await policy(app, maxPendingPeerInputs=0)
    await until(lambda: app.db.execute('SELECT 1 FROM commands WHERE id=?', (new_id,)).fetchone())
    await settled(app, new_id, 'accepted')
    assert app.collaboration.receipt(old_id)['delivery'] == 'suppressed'
    await policy(app, maxPendingPeerInputs=0)
    await asyncio.sleep(.05)
    assert [row[1] for row in app.runtime.inputs] == ['result-request', new_id]


async def test_creation_failure_return_resumes_without_repeating_unknown_creation(app, monkeypatch):
    source = app.state['sessions'][0]
    app.runtime.unavailable_boundaries.add(source['id'])
    plan(app, source)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def failed_creation(*args):
        calls.append(True)
        entered.set()
        await release.wait()
        raise OSError('Uncertain preparation effect')
    monkeypatch.setattr(app.collaboration, '_finish_create', failed_creation)
    created = await commission(app, source)
    await asyncio.wait_for(entered.wait(), 5)
    await send(app, 'occupied', sessionId=source['id'])
    await policy(app, maxPendingPeerInputs=1)
    release.set()
    await until(lambda: app.collaboration.receipt(created['requestId'])['subscription']['status'] == 'backpressured')
    original = copy.deepcopy(app.collaboration.receipt(created['requestId']))
    await policy(app, maxPendingPeerInputs=0)
    identity = created['subscription']['continuationId']
    await until(lambda: app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone())
    assert app.collaboration.receipt(identity)['delivery'] == 'queued'
    current = app.collaboration.receipt(created['requestId'])
    assert current['delivery'] == original['delivery'] == 'unknown'
    assert current['phase'] == original['phase'] == 'planned'
    await policy(app, maxPendingPeerInputs=0)
    await asyncio.sleep(.05)
    assert calls == [True] and not app.runtime.inputs
    assert len([m for m in source['messages'] if m.get('inputId') == identity]) == 1
