"""Content-free binding outcomes and the large native request witness limit."""
import asyncio
import hashlib
import json
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_web.context_inspection import fingerprint_request, MAX_TEXT_CHARS
from amplifier_web.context_inspection_adapter import ContextInspection
from amplifier_web.execution_events import ExecutionEvents
from test_context_inspection import observed, request, Provider, config, evidence


@pytest.mark.parametrize('copied', [False, True])
@pytest.mark.parametrize('shape,status,reason', [
    ('large', 'bounded', 'character_limit'),
    ('opaque', 'unsupported', 'value_type'),
    ('nodes', 'bounded', 'node_limit'),
    ('depth', 'bounded', 'depth_limit'),
    ('container', 'bounded', 'container_limit'),
])
async def test_unbindable_preflight_is_observed_without_associating_its_numbers(
        tmp_path, copied, shape, status, reason):
    observer, _, provider = observed(tmp_path, recording=False)
    value = request('x' * (MAX_TEXT_CHARS + 1) if shape == 'large' else 'synthetic private input')
    options = {}
    if shape == 'opaque':
        options = {'opaque': object()}
    elif shape == 'nodes':
        options = {'rows': [[None] * 4000 for _ in range(6)]}
    elif shape == 'depth':
        nested = None
        for _ in range(30):
            nested = [nested]
        options = {'nested': nested}
    elif shape == 'container':
        options = {'rows': [None] * 4097}
    await provider.request_budget(value, request_options=options)
    await provider.complete(value.model_copy(deep=True) if copied else value, **options)
    await observer.drain()
    result = observer.store.read()
    header = result['selected']
    assert header['budget'] == {}
    assert header['limits']['requestInputAllowance'] is None
    assert header['budgetStatus'] == ('binding_bounded' if status == 'bounded' else 'binding_unsupported')
    assert header['budgetBinding'] == {
        'scopedPreflightCount': 1,
        'dispatchFingerprint': {'status': status, 'reason': reason},
        'preflightFingerprints': [{'status': status, 'reason': reason}],
        'association': 'unproven',
    }
    serialized = json.dumps(header)
    assert 'signature' not in serialized and 'synthetic private input' not in serialized
    assert 'observed in this provider/task interval' in header['limitEvidence']['providerCount']['reason']
    assert len(provider.calls) == 1
    assert not list(observer.store.directory.glob('*.txt'))


async def test_absent_or_other_scope_never_scans_request_for_binding(tmp_path, monkeypatch):
    observer, events, provider = observed(tmp_path)
    value = request()
    await provider.request_budget(value)
    def forbidden(*args, **kwargs):
        raise AssertionError('unrelated preflight must not trigger a fingerprint traversal')
    monkeypatch.setattr('amplifier_web.context_inspection_adapter.fingerprint_request', forbidden)
    await asyncio.create_task(provider.complete(value.model_copy(deep=True)))
    other = events.instrument_provider('root', Provider())
    await other.complete(value.model_copy(deep=True))
    await observer.drain()
    assert len(observer.store.index()) == 2, 'safe_observe must not conceal a failed assertion'
    for header in observer.store.index():
        assert header['budgetStatus'] == 'not_captured'
        assert header['budgetBinding']['scopedPreflightCount'] == 0
        assert header['budgetBinding']['dispatchFingerprint'] == {
            'status': 'not_evaluated', 'reason': 'no_scoped_preflight'}
    observer.budgets.clear()
    await provider.complete(value)
    await observer.drain()
    assert len(observer.store.index()) == 3


@pytest.mark.parametrize('copied', [False, True])
async def test_complete_changed_suffix_or_options_is_not_bound(tmp_path, copied):
    observer, _, provider = observed(tmp_path)
    for changed_options in (False, True):
        value = request('x' * 130_000)  # Past per-item display bound, below binding bound.
        await provider.request_budget(value, request_options={'temperature': .2})
        sent = value.model_copy(deep=True) if copied else value
        if not changed_options:
            sent.messages[1].content += 'changed suffix'
        await provider.complete(sent, temperature=.3 if changed_options else .2)
        await observer.drain()
        row = observer.store.read()['selected']
        assert row['budget'] == {}
        assert row['budgetStatus'] == ('unmatched_preflight' if copied else 'request_changed')
        assert row['budgetBinding']['scopedPreflightCount'] == 1
        assert row['budgetBinding']['dispatchFingerprint']['status'] == 'complete'
        assert row['budgetBinding']['association'] == 'unproven'
        await provider.complete(sent, temperature=.2)
        await observer.drain()
        assert observer.store.read()['selected']['budgetStatus'] == 'not_captured'


async def test_ambiguous_complete_copies_do_not_gain_attribution(tmp_path):
    observer, _, provider = observed(tmp_path)
    first, second = request(), request()
    await provider.request_budget(first)
    await provider.request_budget(second)
    await provider.complete(first.model_copy(deep=True))
    await observer.drain()
    header = observer.store.read()['selected']
    assert header['budgetStatus'] == 'ambiguous' and header['budget'] == {}
    assert header['budgetBinding']['scopedPreflightCount'] == 2
    assert header['budgetBinding']['association'] == 'ambiguous'


def test_fingerprint_failure_work_stays_bounded_and_never_returns_prefix_hash():
    results = []
    for size in (MAX_TEXT_CHARS + 1, MAX_TEXT_CHARS * 8):
        work = {}
        result = fingerprint_request(request('x' * size), {}, work=work)
        assert result.signature is None
        assert result.public() == {'status': 'bounded', 'reason': 'character_limit'}
        assert work['encodedCharacters'] <= MAX_TEXT_CHARS
        assert work['nodes'] <= 20_000
        results.append(work)
    assert results[0] == results[1], 'a larger uninspectable string adds no scan work'
    complete = fingerprint_request(request('short'), {})
    assert complete.signature and complete.public() == {'status': 'complete', 'reason': None}
    evidence('fingerprint-work', {'largeCases': results, 'largerInputDoesNotIncreaseWork': True})


@pytest.mark.parametrize('mutation', [False, True])
async def test_large_real_native_chain_and_existing_witness_admission_gap(tmp_path, mutation):
    """The passive post-admission ticket, not the stale Work digest, binds budget.

    The Work digest is generated before an awaited admission callback. The
    callback below deliberately mutates a synthetic suffix after final fit; a
    borrowed digest must not turn that into an attributed count.
    """
    from test_pretransport_refit_native import native_case
    session, state = await native_case('stale_visual', selected=True, preparation_context=True)
    home = tmp_path / 'home'
    config(home, recording=False)
    observer = ContextInspection(session.coordinator, tmp_path / 'capture', home)
    events = ExecutionEvents(session.session_id, lambda row: None)
    events.context_inspectors[session.session_id] = observer
    wrapper = session.coordinator.get('providers')['fixture']._execution_provider()
    raw = wrapper.original
    raw.config = {'raw': False}
    wrapper.original = events.instrument_provider(session.session_id, raw)
    text = 'L' * (MAX_TEXT_CHARS + 100)
    async def instructions():
        return text
    await session.coordinator.get('context').set_system_prompt_factory(instructions)
    # Capture a weak reference at the real fitted-owner boundary, not a second
    # observation snapshot or a fabricated request/receipt.
    begin_work = wrapper.delivery.begin_work
    current = {}
    def begin(value, **kwargs):
        attempt = begin_work(value, **kwargs)
        current.update(request=weakref.ref(value), attempt=attempt)
        return attempt
    wrapper.delivery.begin_work = begin
    async def admission(row):
        if mutation:
            value = current['request']()
            system = next(m for m in value.messages if m.role == 'system')
            system.content += 'ADMISSION-MUTATION'
        await asyncio.sleep(0)  # Existing admission boundary can yield.
    events.admission_guard = admission
    try:
        assert await session.execute('Use the large synthetic request, then finish.') == 'Done'
        await observer.drain()
        result = observer.store.read()
        assert len(state.transports) == 2 and len(state.measurements) == 3
        assert all(len(s) > MAX_TEXT_CHARS for s in state.transports)
        header = result['selected']
        assert header['budgetStatus'] == ('request_changed' if mutation else 'captured')
        if mutation:
            assert header['budget'] == {}
        else:
            assert header['limits']['requestInputAllowance'] == 10_000
            assert header['budget']['measurement']['input_tokens'] == 100
            assert header['limits']['reservedOutput'] == 321
        assert header['budgetBinding']['scopedPreflightCount'] == 0
        assert header['budgetBinding']['fitTicket']['observed'] is True
        actual = state.transports[-1]
        same = current['attempt']['requestSha256'] == hashlib.sha256(actual.encode()).hexdigest()
        assert same is (not mutation), 'pre-admission witness cannot prove mutable post-admission content'
        assert ('ADMISSION-MUTATION' in actual) is mutation
        evidence('large-native-' + ('admission-mutation' if mutation else 'unchanged'), {
            'boundary': 'actual Core/Foundation/SelectedProvider/SurfaceProvider/ExecutionEvents',
            'requestCharacters': len(actual), 'providerCalls': len(state.transports),
            'budgetCalls': len(state.measurements), 'header': result['selected'],
            'workDigestMatchesActualTransport': same, 'mutationAfterFinalFit': mutation,
            'providerApiCalls': 0, 'passivePostAdmissionTicket': True,
        })
    finally:
        await session.cleanup()