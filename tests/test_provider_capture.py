"""Passive post-admission attribution, never an execution permission."""
import asyncio
import gc
import json
import weakref
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest
from amplifier_web import provider_capture as capture
from amplifier_web.surface_delivery import SurfaceProvider
from test_context_inspection import observed, request, evidence


class NoWorkDelivery:
    """The fitted owner still serializes, but no Work obligation is present."""
    async def prepare(self, value, provider):return value
    def work_request(self, value):return None
    async def revalidate(self, value):return value.model_copy(deep=True)
    async def finalize_work(self, value, **kw):return value
    def commit(self, value):pass


def chain(tmp_path):
    observer, events, provider = observed(tmp_path, recording=False)
    return observer, events, provider, SurfaceProvider(provider, NoWorkDelivery())


@pytest.mark.parametrize('size',[32,1_000_100,4_000_100])
async def test_owner_serialization_shared_one_complete_final_encoding_no_work(tmp_path,monkeypatch,size):
    observer,events,provider,surface=chain(tmp_path)
    value=request('Z'*size);await surface.request_budget(value)
    ticket=surface._capture_tickets[-1]
    assert ticket.fitted is surface.prepared[-1][2]  # no deepcopy/second fitted encoding
    # Count only the extra final-owner comparison, not unrelated Work/provider
    # serialization. Adding other retained candidates must not multiply it.
    for _ in range(3):await surface.request_budget(request('Other'))
    counts={'calls':0,'characters':0};original=ChatRequest.model_dump_json
    def encode(self,*a,**kw):
        result=original(self,*a,**kw);counts['calls']+=1;counts['characters']+=len(result);return result
    monkeypatch.setattr(ChatRequest,'model_dump_json',encode)
    await surface.complete(value)
    assert counts['calls']==1
    assert counts['characters']==len(original(value))
    await observer.drain();header=observer.store.read()['selected']
    assert header['budget']['measurement']['input_tokens']==980
    assert header['limits']=={'modelWindow':8000,'requestInputAllowance':5000,'reservedOutput':200,'configuredContextBudget':6000,'compactionThreshold':4800}
    assert header['budgetBinding']['association']=='fitted_owner_ticket'
    assert ticket.fitted is None and ticket.decision is None
    assert not list(observer.store.directory.glob('*.txt'))
    evidence('passive-ticket-work-'+str(size),{'size':size,'retainedCandidates':4,'postAdmissionEncoding':counts,'fittedRepresentationShared':True,'providerCalls':len(provider.calls)})


@pytest.mark.parametrize('mutation',['metadata-suffix','admission-nested','options','option-type','unsupported'])
async def test_post_await_mutation_suppresses_numbers_not_execution(tmp_path,mutation):
    observer,events,provider,surface=chain(tmp_path)
    value=request('Z'*1_000_100);options={'native':{'limit':1}}
    await surface.request_budget(value,request_options=options)
    actual={}
    before=surface.delivery.finalize_work
    async def finalize(value,**kw):actual['value']=value;return await before(value,**kw)
    surface.delivery.finalize_work=finalize
    if mutation=='metadata-suffix':
        async def info():
            await asyncio.sleep(0)
            actual['value'].messages[1].content+='suffix'
            return {'id':'fixture','defaults':{'model':'fixture'}}
        provider.get_info=info
    else:
        async def admission(row):
            await asyncio.sleep(0)
            if mutation=='admission-nested':actual['value'].messages[1].metadata={'deep':{'changed':True}}
            elif mutation=='options':options['native']['limit']=2
            elif mutation=='option-type':options['native']['limit']=True
            else:options['native']['limit']=object()
        events.admission_guard=admission
    await surface.complete(value,**options);await observer.drain()
    header=observer.store.read()['selected']
    assert header['budget']=={}
    assert header['budgetStatus']==('binding_unsupported' if mutation=='unsupported' else 'request_changed')
    assert len(provider.calls)==1


async def test_claim_before_nested_metadata_and_admission_and_cross_task(tmp_path):
    observer,events,provider,surface=chain(tmp_path)
    value=request('X'*1_000_100);await surface.request_budget(value)
    nested=False
    async def info():
        nonlocal nested
        if not nested:
            nested=True
            # Inherited context cannot steal the claimed intended call, even for
            # the same provider and request in a child admission task.
            await asyncio.create_task(provider.complete(value))
        return {'id':'fixture','defaults':{'model':'fixture'}}
    provider.get_info=info
    await surface.complete(value);await observer.drain()
    headers=observer.store.index();assert len(headers)==2
    assert sum(bool(h['budget']) for h in headers)==1
    assert observer.store.read()['selected']['budgetStatus']=='captured'
    await surface.complete(value);await observer.drain()
    assert observer.store.read()['selected']['budget']=={}  # no retry reuse


async def test_refit_generation_supersession_and_diagnostic_failure(tmp_path,monkeypatch):
    observer,events,provider,surface=chain(tmp_path)
    value=request('X'*1_000_100);await surface.request_budget(value)
    first=surface._capture_tickets[-1]
    await surface.request_budget(value,request_options={'temperature':.5})
    assert not first.valid
    await surface.complete(value,temperature=.5);await observer.drain()
    assert observer.store.read()['selected']['budgetStatus']=='captured'
    value=request('X'*1_000_100);await surface.request_budget(value)
    original=ChatRequest.model_dump_json
    def failed(self,*a,**kw):raise ValueError('synthetic diagnostic serialization failure')
    monkeypatch.setattr(ChatRequest,'model_dump_json',failed)
    await surface.complete(value);await observer.drain()
    assert observer.store.read()['selected']['budget']=={}
    assert len(provider.calls)==2
    monkeypatch.setattr(ChatRequest,'model_dump_json',original)


@pytest.mark.parametrize('failure',['quota','cancel'])
async def test_admission_failure_retires_ticket_without_provider_or_retry(tmp_path,failure):
    observer,events,provider,surface=chain(tmp_path)
    value=request('X'*1_000_100);await surface.request_budget(value)
    ticket=surface._capture_tickets[-1]
    async def guard(row):
        await asyncio.sleep(0)
        if failure=='quota':raise ValueError('quota')
        raise asyncio.CancelledError()
    events.admission_guard=guard
    with pytest.raises(ValueError if failure=='quota' else asyncio.CancelledError):await surface.complete(value)
    assert provider.calls==[] and not ticket.valid and ticket.fitted is None
    events.admission_guard=None
    await surface.complete(value);await observer.drain()
    assert observer.store.read()['selected']['budget']=={}


async def test_stream_first_iteration_and_cross_task_next_close_have_no_leaked_ticket(tmp_path):
    observer,events,provider,surface=chain(tmp_path)
    value=request('X'*1_000_100);await surface.request_budget(value)
    iterator=surface.stream(value)
    assert not events.nodes and capture.DISPATCH.get() is None
    await anext(iterator)
    assert capture.DISPATCH.get() is None
    await observer.drain()
    # Cross-task close must not reset a token from a different task.
    await asyncio.create_task(iterator.aclose())
    await observer.drain()
    assert any(h['budgetStatus']=='captured' for h in observer.store.index())
    assert capture.DISPATCH.get() is None
    value=request('Y'*1_000_100);await surface.request_budget(value)
    it=surface.stream(value)
    await asyncio.create_task(anext(it))
    await asyncio.create_task(it.aclose());await observer.drain()
    assert observer.store.index()[-1]['budget']=={}  # budget task != first-entry task


def test_option_normalization_type_and_resource_bounds():
    assert capture.normalized_options({'x':1})!=capture.normalized_options({'x':True})
    assert capture.normalized_options({'x':1})!=capture.normalized_options({'x':1.0})
    assert capture.normalized_options({'x':(1,)}) is None
    assert capture.normalized_options({'x':float('nan')}) is None
    assert capture.normalized_options({'x':'a'*65537}) is None
    assert capture.normalized_options({'x':[0]*1025}) is None


async def test_no_strong_request_extension_and_ambiguous_copies(tmp_path):
    observer,events,provider,surface=chain(tmp_path)
    value=request('X'*1_000_100);reference=weakref.ref(value)
    await surface.request_budget(value)
    # Existing fitting owner retains its prepared input. Ticket must not add a
    # new request owner after that bounded entry is retired.
    surface.prepared.clear();del value;gc.collect()
    assert reference() is None
    first,second=request('X'*1_000_100),request('X'*1_000_100)
    await surface.request_budget(first);await surface.request_budget(second)
    await surface.complete(first.model_copy(deep=True));await observer.drain()
    assert observer.store.read()['selected']['budget']=={}
