"""Content-free capture stages are evidence, never a second budget matcher."""
import asyncio
import gc
import json
import weakref
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest
from amplifier_web import provider_capture as capture
from amplifier_web.execution_events import CALL_PURPOSE
from amplifier_web.surface_delivery import SurfaceProvider
from test_provider_capture import chain, NoWorkDelivery
from test_context_inspection import observed, request, Provider, evidence


async def header(observer):
    await observer.drain()
    return observer.store.read()['selected']


@pytest.mark.parametrize('size',[32,1_000_100])
async def test_fixed_stage_trace_preserves_budget_and_work_bound(tmp_path,monkeypatch,size):
    observer,events,provider,surface=chain(tmp_path)
    value=request('SECRET'*size);await surface.request_budget(value)
    count=0;original=ChatRequest.model_dump_json
    def encode(self,*a,**kw):
        nonlocal count
        count+=1;return original(self,*a,**kw)
    monkeypatch.setattr(ChatRequest,'model_dump_json',encode)
    await surface.complete(value);row=await header(observer)
    assert count==1 and len(provider.calls)==1
    assert row['budgetStatus']=='captured' and row['budget']['measurement']['input_tokens']==980
    diag=row['captureDiagnostic']
    assert diag=={'version':1,'purpose':'unspecified','dispatch':'observed','fit':'created',
                 'take':'taken','candidates':1,'claim':'claimed','consume':'captured'}
    assert len(json.dumps(diag))<300 and 'SECRET' not in json.dumps(diag)
    assert surface._capture_fit_notes==[] and capture.TRACE.get() is None
    evidence('capture-stage-work-'+str(size),{'diagnostic':diag,'encodingCalls':count,'metadataBytes':len(json.dumps(diag))})


async def test_no_marker_does_not_assert_bypass_and_fallback_still_works(tmp_path):
    observer,events,provider=observed(tmp_path,recording=False)
    value=request();await provider.request_budget(value);await provider.complete(value)
    row=await header(observer)
    assert row['budgetStatus']=='captured' and row['budgetBinding']['association']=='same_object'
    assert row['captureDiagnostic']['dispatch']=='not_observed'
    assert row['captureDiagnostic']['fit']=='not_observed'


async def test_ticket_none_trace_does_not_suppress_legacy_fallback(tmp_path):
    observer,events,provider,surface=chain(tmp_path)
    class Unfitted:
        async def prepare(self,value,provider):return value
        def commit(self,value):pass
    surface.delivery=Unfitted()
    value=request();await surface.request_budget(value);await surface.complete(value)
    row=await header(observer)
    assert row['budgetStatus']=='captured' and row['budgetBinding']['association']=='same_object'
    assert row['captureDiagnostic']['fit']=='fitted_value_unavailable'
    assert row['captureDiagnostic']['take']=='no_ticket'
    assert row['captureDiagnostic']['claim']=='not_claimed'
    assert row['captureDiagnostic']['consume']=='not_observed'


@pytest.mark.parametrize('kind',['same','copy','task','provider'])
async def test_nested_entry_cannot_borrow_outer_diagnostic(tmp_path,kind):
    observer,events,provider,surface=chain(tmp_path);actual={};nested=False
    async def finalize(value,**kwargs):actual['request']=value;return value
    surface.delivery.finalize_work=finalize
    other=events.instrument_provider('root',Provider())
    async def admit(row):
        nonlocal nested
        if nested:return
        nested=True
        value=actual['request']
        operation=(other if kind=='provider' else provider).complete(value.model_copy(deep=True) if kind=='copy' else value)
        if kind=='task':await asyncio.create_task(operation)
        else:await operation
    events.admission_guard=admit
    value=request('PRIVATE'*100);await surface.request_budget(value);await surface.complete(value)
    await observer.drain();rows=observer.store.index()
    outer=next(r for r in rows if r['budgetStatus']=='captured')
    inner=next(r for r in rows if r is not outer)
    assert outer['captureDiagnostic']['dispatch']=='observed'
    assert inner['captureDiagnostic']['dispatch']=={'same':'already_observed','copy':'request_mismatch','task':'task_mismatch','provider':'provider_mismatch'}[kind]
    assert inner['captureDiagnostic']['fit']=='not_observed' and inner['captureDiagnostic']['candidates']==0
    assert inner['budget']=={}


async def test_other_budget_task_and_copied_request_do_not_borrow_fit_notes(tmp_path):
    observer,events,provider,surface=chain(tmp_path)
    value=request();await surface.request_budget(value)
    await asyncio.create_task(surface.complete(value));row=await header(observer)
    assert row['captureDiagnostic']['take']=='task_mismatch' and row['captureDiagnostic']['fit']=='not_observed'
    value=request();await surface.request_budget(value)
    await surface.complete(value.model_copy(deep=True));row=await header(observer)
    assert row['captureDiagnostic']['take']=='no_ticket' and row['captureDiagnostic']['fit']=='not_observed'


@pytest.mark.parametrize('change',['evict','swap','refit'])
async def test_reached_take_disposition(tmp_path,change):
    observer,events,provider,surface=chain(tmp_path);value=request();await surface.request_budget(value)
    old=surface._capture_tickets[-1]
    if change=='evict':surface.prepared.clear()
    elif change=='swap':surface.original=events.instrument_provider('root',Provider())
    else:await surface.request_budget(value)
    await surface.complete(value);row=await header(observer)
    assert row['captureDiagnostic']['take']=={'evict':'evicted_fit','swap':'provider_changed','refit':'taken'}[change]
    if change=='refit':assert not old.valid and row['budgetStatus']=='captured'
    else:assert not row['budget']


async def test_ambiguous_take_and_retry_are_not_new_fit_evidence(tmp_path):
    observer,events,provider,surface=chain(tmp_path);value=request();await surface.request_budget(value)
    surface._capture_tickets.append(surface._capture_tickets[-1])
    await surface.complete(value);row=await header(observer)
    assert row['captureDiagnostic']['take']=='ambiguous' and row['budget']=={}
    value=request();await surface.request_budget(value);await surface.complete(value);await header(observer)
    await surface.complete(value);row=await header(observer)
    assert row['captureDiagnostic']['take']=='already_taken' and row['captureDiagnostic']['fit']=='not_observed'
    assert not row['budget']


async def test_new_fit_invalidates_prior_note_even_when_preparation_fails(tmp_path):
    observer,events,provider,surface=chain(tmp_path);value=request();await surface.request_budget(value)
    surface.prepared.clear()
    async def fail(value,provider):raise ValueError('preparation failed')
    surface.delivery.prepare=fail
    with pytest.raises(ValueError):await surface.request_budget(value)
    assert not surface._capture_fit_notes
    trace=capture.describe_take(surface,value,None)
    assert trace.fit=='not_observed' and trace.take=='no_ticket'


async def test_identity_unsupported_and_closed_scope_are_unproven(tmp_path):
    observer,events,provider,surface=chain(tmp_path)
    unweak=SimpleNamespace(metadata={'purpose':'PRIVATE'})
    with capture.dispatch(None,provider,unweak):
        assert capture.DISPATCH.get() is None
        result=capture.entry_trace(events,provider,unweak,None)
        assert result['dispatch']=='identity_unproven' and result['fit']=='not_observed'
        scope=capture.TRACE.get()
    token=capture.TRACE.set(scope)
    try:assert capture.entry_trace(events,provider,unweak,None)['dispatch']=='not_observed'
    finally:capture.TRACE.reset(token)


@pytest.mark.parametrize('purpose,metadata,expected',[
    (None,{},'unspecified'),(None,{'purpose':'context-compaction'},'context_compaction'),
    ({'purpose':'image_generation','label':'Image generation'}, {},'image_generation'),
    ({'label':'Session naming','lifecycle':'background'},{},'title'),
    ({'label':'Memory consolidation','lifecycle':'background'},{},'memory_consolidation'),
    ({'label':'PRIVATE LABEL'},{},'unknown'),(None,{'purpose':'PRIVATE'*100000},'unknown'),
])
async def test_purpose_is_fixed_and_does_not_change_filtering(tmp_path,purpose,metadata,expected):
    observer,events,provider,surface=chain(tmp_path);value=request();value.metadata=metadata
    assert capture.purpose_code(purpose,value)==expected
    token=CALL_PURPOSE.set(purpose)
    try:await provider.complete(value)
    finally:CALL_PURPOSE.reset(token)
    await observer.drain()
    # Preserve the existing auxiliary exclusion. Do not invent a header or
    # alter the row's purpose merely to expose the diagnostic classification.
    if purpose and (purpose.get('purpose') or purpose.get('lifecycle')=='background'):assert observer.store.index()==[]
    elif metadata.get('purpose')=='context-compaction':assert observer.store.index()==[]
    else:assert observer.store.index()[0]['captureDiagnostic']['purpose']==expected


@pytest.mark.parametrize('operation',['note_fit','describe_take','entry_trace','public_trace'])
async def test_observation_failures_do_not_change_execution_or_budget(tmp_path,monkeypatch,operation):
    observer,events,provider,surface=chain(tmp_path)
    def fail(*args):raise RuntimeError('SECRET failure text')
    monkeypatch.setattr(capture,operation,fail)
    value=request();await surface.request_budget(value);await surface.complete(value)
    row=await header(observer)
    assert row['budgetStatus']=='captured' and len(provider.calls)==1
    assert 'SECRET' not in json.dumps(row.get('captureDiagnostic'))


async def test_notes_are_weak_bounded_and_parallel_scopes_remain_independent(tmp_path):
    observer,events,provider,surface=chain(tmp_path)
    values=[request(str(i)) for i in range(8)]
    for v in values:await surface.request_budget(v)
    assert len(surface._capture_fit_notes)==4
    v=values[-1];reference=weakref.ref(v);surface.prepared.clear();values.clear();del v;gc.collect()
    assert reference() is None
    ready=asyncio.Event();entered=0
    async def invoke():
        nonlocal entered
        value=request();await surface.request_budget(value);entered+=1
        if entered==2:ready.set()
        await ready.wait();await surface.complete(value)
    await asyncio.gather(invoke(),invoke());await observer.drain()
    assert all(r['captureDiagnostic']['dispatch']=='observed' for r in observer.store.index())
    assert all(r['budgetStatus']=='captured' for r in observer.store.index())


async def test_stream_context_is_not_inherited_after_yield_or_reused(tmp_path):
    observer,events,provider,surface=chain(tmp_path)
    value=request();await surface.request_budget(value);it=surface.stream(value)
    assert capture.TRACE.get() is None
    await anext(it);assert capture.TRACE.get() is None
    await asyncio.create_task(it.aclose());await observer.drain();row=observer.store.index()[0]
    assert row['captureDiagnostic']['dispatch']=='observed' and row['budgetStatus']=='captured'
    await provider.complete(value);row=await header(observer)
    assert row['captureDiagnostic']['dispatch']=='not_observed' and row['budget']=={}


@pytest.mark.parametrize('failure',['cancel','quota'])
async def test_admission_failure_does_not_leave_trace(tmp_path,failure):
    observer,events,provider,surface=chain(tmp_path);value=request();await surface.request_budget(value)
    async def guard(row):
        if failure=='cancel':raise asyncio.CancelledError()
        raise ValueError('quota')
    events.admission_guard=guard
    with pytest.raises(asyncio.CancelledError if failure=='cancel' else ValueError):await surface.complete(value)
    assert capture.TRACE.get() is None and not provider.calls and not observer.store.index()
    events.admission_guard=None
    await surface.complete(value);row=await header(observer)
    assert row['captureDiagnostic']['fit']=='not_observed' and row['budget']=={}
