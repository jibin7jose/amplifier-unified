"""Exact passive delivery recovery through AppService, with no real worker."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web.service import AppService
from test_service import Runtime


INPUT_ID = 'receiptless-input'


class ProbeRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.probes = []
        self.retries = []
        self.evidence = 'unknown'
        self.on_probe = None
        self.stop_seen = asyncio.Event()

    async def delivery(self, session, input_id):
        self.probes.append((copy.deepcopy(session), input_id))
        if self.on_probe is not None:
            await self.on_probe()
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence

    async def retry(self, *args, **kwargs):
        self.retries.append((args, kwargs))
        raise AssertionError('A delivery check must never retry.')

    async def stop(self, sid, *, reason='stopped'):
        await super().stop(sid, reason=reason)
        self.stop_seen.set()


@pytest.fixture
async def delivery_app(tmp_path):
    runtime = ProbeRuntime()
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    try:
        yield app, runtime
    finally:
        try:
            assert runtime.started == []
            assert runtime.sent == []
            assert runtime.retries == []
        finally:
            await app.close()


def saved_receipt(app):
    row = app.db.execute('SELECT receipt FROM commands WHERE id=?', (INPUT_ID,)).fetchone()
    return row[0] if row else None


def put_receipt(app, value):
    raw = json.dumps(value)
    app.db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?)',
                   (INPUT_ID, 'fixture-admission', raw))
    app.db.commit()
    return raw


def send_receipt(app, **changes):
    return {'sessionId': app._session()['id'], 'inputId': INPUT_ID,
            'accepted': True, 'delivery': 'unknown', **changes}


def saved_message(app):
    return app._message(app._session(), 'user', 'Retained input', 'chat',
                        inputId=INPUT_ID, delivery={'status': 'unknown'})


async def check(app):
    sid = app._session()['id']
    response = await app.dispatch('conversation.delivery',
        {'sessionId': sid, 'inputId': INPUT_ID}, include_state=False)
    return response['result']


@pytest.mark.parametrize('identity_field', [None, 'runtimeSessionId', 'nativeIdentity'])
async def test_native_confirmation_without_host_evidence_does_not_fabricate_records(delivery_app, identity_field):
    app, runtime = delivery_app
    session = app._session()
    if identity_field:
        session[identity_field] = 'saved-native-source'
    before = copy.deepcopy(session['messages'])
    runtime.evidence = 'accepted'

    result = await check(app)

    assert result['delivery'] == 'accepted'
    assert result['sessionId'] == session['id'] and result['inputId'] == INPUT_ID
    assert result['source'] == {
        'workspace': session['workspace'],
        'nativeIdentity': session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'],
    }
    assert len(runtime.probes) == 1
    probed, input_id = runtime.probes[0]
    assert input_id == INPUT_ID and probed['id'] == session['id']
    assert probed['workspace'] == session['workspace']
    if identity_field:
        assert probed[identity_field] == 'saved-native-source'
    assert saved_receipt(app) is None
    assert session['messages'] == before


@pytest.mark.parametrize('evidence', ['unknown', 'failed', RuntimeError('Probe unavailable')])
async def test_receiptless_missing_evidence_is_not_non_delivery(delivery_app, evidence):
    app, runtime = delivery_app
    runtime.evidence = evidence

    result = await check(app)

    assert result['delivery'] == 'not_saved'
    assert 'does not prove non-delivery' in result['message']
    assert 'does not resend' in result['message']
    assert 'try sending' not in result['message']
    assert len(runtime.probes) == 1
    assert saved_receipt(app) is None
    assert app._session()['messages'] == []


@pytest.mark.parametrize('action', [None, 'conversation.send'])
async def test_exact_positive_receipt_needs_no_runtime_probe(delivery_app, action):
    app, runtime = delivery_app
    receipt = send_receipt(app, delivery='accepted')
    if action:
        receipt['commandAction'] = action
    put_receipt(app, receipt)

    assert (await check(app))['delivery'] == 'accepted'
    assert runtime.probes == []
    assert app._session()['messages'] == []
    assert json.loads(saved_receipt(app)) == receipt


@pytest.mark.parametrize('raw', ['null', '[]', '{invalid JSON'])
@pytest.mark.parametrize('resident_message', [False, True])
async def test_positive_probe_preserves_corrupt_receipt_bytes(delivery_app, monkeypatch, raw, resident_message):
    app, runtime = delivery_app
    message = saved_message(app) if resident_message else None
    original = put_receipt(app, None)
    db = app.db
    if raw == '{invalid JSON':
        # SQLite's JSON expression index correctly refuses invalid JSON. Model
        # only a corrupt legacy read, never disable the production index.
        class CorruptRead:
            def __getattr__(self, name):
                return getattr(db, name)

            def execute(self, sql, args=()):
                if sql == 'SELECT receipt FROM commands WHERE id=?' and args == (INPUT_ID,):
                    return SimpleNamespace(fetchone=lambda: (raw,))
                assert not (sql.startswith('UPDATE commands SET receipt=') and args[-1:] == (INPUT_ID,))
                return db.execute(sql, args)

        monkeypatch.setattr(app, 'db', CorruptRead())
    else:
        original = put_receipt(app, json.loads(raw))
    runtime.evidence = 'accepted'

    assert (await check(app))['delivery'] == 'accepted'
    assert len(runtime.probes) == 1
    assert saved_receipt(app) == raw
    assert db.execute('SELECT receipt FROM commands WHERE id=?', (INPUT_ID,)).fetchone()[0] == original
    if message:
        assert message['delivery']['status'] == 'accepted'
        assert message['text'] == 'Retained input'
    else:
        assert app._session()['messages'] == []


@pytest.mark.parametrize('invalid', [
    {'delivery': {}}, {'accepted': False}, {'status': 409},
    {'status': 'failed'}, {'code': 'worker_startup_failed'}, {'error': 'refused'},
    {'accepted': 1},
])
async def test_contradictory_receipt_is_not_positive_evidence(delivery_app, invalid):
    app, runtime = delivery_app
    raw = put_receipt(app, {**send_receipt(app, delivery='accepted'), **invalid})

    assert (await check(app))['delivery'] != 'accepted'
    assert saved_receipt(app) == raw
    assert len(runtime.probes) == 1


async def test_expired_receipt_detail_can_use_native_proof_without_rewriting_receipt(delivery_app):
    app, runtime = delivery_app
    receipt = send_receipt(app)
    receipt.pop('delivery')
    receipt['detailExpired'] = True
    raw = put_receipt(app, receipt)
    runtime.evidence = 'accepted'

    assert (await check(app))['delivery'] == 'accepted'
    assert len(runtime.probes) == 1
    assert saved_receipt(app) == raw
    assert app._session()['messages'] == []


@pytest.mark.parametrize('conflict', [
    {'sessionId': 'foreign-session'}, {'inputId': 'another-input'},
    {'action': 'conversation.stop'}, {'commandAction': 'session.delete'},
])
@pytest.mark.parametrize('resident_message', [False, True])
async def test_conflicting_receipt_is_protected_from_probe_and_rewrite(delivery_app, conflict, resident_message):
    app, runtime = delivery_app
    if resident_message:
        saved_message(app)
    before = copy.deepcopy(app._session()['messages'])
    raw = put_receipt(app, send_receipt(app, **conflict))
    runtime.evidence = 'accepted'

    assert (await check(app))['delivery'] == 'unknown'
    assert runtime.probes == []
    assert saved_receipt(app) == raw
    assert app._session()['messages'] == before


async def test_foreign_receipt_arriving_during_probe_is_never_updated(delivery_app):
    app, runtime = delivery_app
    message = saved_message(app)
    raw = None

    async def change_receipt():
        nonlocal raw
        async with app.lock:
            raw = put_receipt(app, send_receipt(app, sessionId='foreign-session'))

    runtime.on_probe = change_receipt
    runtime.evidence = 'accepted'

    assert (await check(app))['delivery'] == 'unknown'
    assert saved_receipt(app) == raw
    assert message['delivery']['status'] == 'unknown'


@pytest.mark.parametrize('field', ['workspace', 'runtimeSessionId', 'nativeIdentity'])
@pytest.mark.parametrize('resident_message', [False, True])
async def test_source_change_during_probe_rejects_old_native_proof(delivery_app, tmp_path, field, resident_message):
    app, runtime = delivery_app
    session = app._session()
    original = {field: session[field]} if field in session else {}
    replacement = str(tmp_path / 'different-source') if field == 'workspace' else 'different-native'
    if resident_message:
        saved_message(app)
        put_receipt(app, send_receipt(app))
    before = copy.deepcopy(app._session()['messages'])
    receipt = saved_receipt(app)

    async def change_source():
        async with app.lock:
            app._session()[field] = replacement

    runtime.on_probe = change_source
    runtime.evidence = 'accepted'

    try:
        assert (await check(app))['delivery'] != 'accepted'
        assert len(runtime.probes) == 1
        assert session[field] == replacement
        assert saved_receipt(app) == receipt
        assert session['messages'] == before
    finally:
        session.pop(field, None)
        session.update(original)


@pytest.mark.parametrize('field,value', [
    ('workspace', None), ('workspace', ''), ('workspace', 'relative-source'),
    ('runtimeSessionId', '../invalid-native'),
])
async def test_missing_or_invalid_source_anchor_never_probes(delivery_app, field, value):
    app, runtime = delivery_app
    session = app._session()
    original = {field: session[field]} if field in session else {}
    session[field] = value
    runtime.evidence = 'accepted'
    # Exercise the service's canonical reconciler directly: the intentionally
    # invalid fixture source is not suitable for dispatch's history loader.
    try:
        result = await app._check_delivery(session['id'], INPUT_ID)
        assert result['sessionId'] == session['id'] and result['inputId'] == INPUT_ID
        assert result['delivery'] != 'accepted'
        assert runtime.probes == []
        assert saved_receipt(app) is None
    finally:
        session.pop(field, None)
        session.update(original)


async def test_late_exact_acceptance_wins_an_unknown_probe(delivery_app):
    app, runtime = delivery_app
    message = saved_message(app)
    put_receipt(app, send_receipt(app))

    async def accept_current():
        async with app.lock:
            app._delivery(app._session(), INPUT_ID, 'accepted')
            app._publish_changes(sessions={app._session()['id']})

    runtime.on_probe = accept_current
    runtime.evidence = 'unknown'

    assert (await check(app))['delivery'] == 'accepted'
    assert message['delivery']['status'] == 'accepted'
    assert json.loads(saved_receipt(app))['delivery'] == 'accepted'


@pytest.mark.parametrize('disposition', ['held', 'queued_for_update', 'steering'])
@pytest.mark.parametrize('during_probe', [False, True])
async def test_check_preserves_held_and_steering_dispositions(delivery_app, disposition, during_probe):
    app, runtime = delivery_app
    message = saved_message(app)
    raw = put_receipt(app, send_receipt(app, delivery='unknown' if during_probe else 'accepted'))

    def protect():
        message['delivery'] = {'status': 'unknown' if disposition == 'steering' else disposition}
        if disposition == 'steering':
            message['steering'] = {'generationId': 'original-run', 'disposition': 'unknown'}

    if during_probe:
        async def protect_during_probe():
            async with app.lock:
                protect()
        runtime.on_probe = protect_during_probe
    else:
        protect()
    runtime.evidence = 'accepted'

    result = await check(app)

    assert result['delivery'] == message['delivery']['status']
    assert result['delivery'] != 'accepted'
    if disposition == 'steering':
        assert result['steering'] == message['steering']
    assert saved_receipt(app) == raw
    assert len(runtime.probes) == int(during_probe)


@pytest.mark.parametrize('disposition', ['held', 'queued_for_update', 'steering'])
@pytest.mark.parametrize('during_probe', [False, True])
async def test_check_preserves_protected_receipt_when_message_is_absent_or_stale(delivery_app, disposition, during_probe):
    app, runtime = delivery_app
    if during_probe:
        saved_message(app)
    before = copy.deepcopy(app._session()['messages'])
    receipt = send_receipt(app, delivery='unknown' if disposition == 'steering' else disposition)
    if disposition == 'steering':
        receipt['steering'] = {'generationId': 'original-run', 'disposition': 'unknown'}
    raw = None

    if during_probe:
        async def protect_receipt():
            nonlocal raw
            async with app.lock:
                raw = put_receipt(app, receipt)
        runtime.on_probe = protect_receipt
    else:
        raw = put_receipt(app, receipt)
    runtime.evidence = 'accepted'

    result = await check(app)

    assert result['delivery'] == receipt['delivery']
    if disposition == 'steering':
        assert result['steering'] == receipt['steering']
    assert saved_receipt(app) == raw
    assert app._session()['messages'] == before
    assert len(runtime.probes) == int(during_probe)


@pytest.mark.parametrize('status', ['sending', 'unknown', 'failed'])
async def test_nonterminal_and_failed_results_retain_exact_correlation(delivery_app, status):
    app, runtime = delivery_app
    message = saved_message(app)
    message['delivery']['status'] = status
    put_receipt(app, send_receipt(app, delivery=status))

    assert (await check(app))['delivery'] == status
    assert len(runtime.probes) == 1


async def test_stop_during_passive_confirmation_never_starts_or_resumes_work(delivery_app):
    app, runtime = delivery_app
    session = app._session()
    message = saved_message(app)
    put_receipt(app, send_receipt(app))
    before_revision = session.get('interruptionRevision', 0)

    async def stop_during_probe():
        await app.dispatch('conversation.stop', {'sessionId': session['id']},
                           command_id='explicit-stop', include_state=False)
        await asyncio.wait_for(runtime.stop_seen.wait(), 2)

    runtime.on_probe = stop_during_probe
    runtime.evidence = 'accepted'

    assert (await check(app))['delivery'] == 'accepted'
    assert message['delivery']['status'] == 'accepted'
    assert session['interruptionRevision'] == before_revision + 1
    assert session['lastInterruption']['commandId'] == 'explicit-stop'
    assert session['status'] in {'stopping', 'stopped'}
    assert runtime.stopped == [session['id']]
    assert runtime.started == runtime.sent == runtime.retries == []