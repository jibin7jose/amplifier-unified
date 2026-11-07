"""Actual install_app_access transform, including WorkRecoveringProvider.

The older native fixture intentionally remains as simplified fitting/refit
coverage; it did not qualify this production wrapper composition.
"""
import asyncio
import copy
import importlib.util
import json
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_web.app_guidance import install_app_access
from amplifier_web.context_inspection_adapter import ContextInspection
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.host.session import SelectedProvider
from amplifier_web.request_recovery import RecoveringProvider
from amplifier_web.surface_delivery import SurfaceProvider
from amplifier_web import provider_capture as capture
from test_context_inspection import config, request, evidence


@asynccontextmanager
async def production(tmp_path, *, legacy=False, immutable=False, streaming=True):
    plan={'session':{'orchestrator':{'module':'loop-streaming','config':{
        'min_delay_between_calls_ms':0,'max_iterations':2}},
        'context':{'module':'context-simple'}}}
    paths={name:Path(importlib.util.find_spec('amplifier_module_'+name.replace('-','_')).origin).parent
           for name in ('loop-streaming','context-simple')}
    session=await PreparedBundle(plan,BundleModuleResolver(paths),Bundle(name='production-ticket',session=plan['session'])).create_session(session_id='production-ticket')
    c=session.coordinator
    state=SimpleNamespace(budgets=[],transports=[],options=[],dispatch_options=[],actual=None,retry=False,calls=0,
                          allowance=10000,window=20000)
    class Provider:
        name='fixture';config={'raw':False}
        def __setattr__(self,name,value):
            if immutable and name in {'complete','request_budget','stream','_amplifier_web_observed'}:
                raise AttributeError('immutable provider fixture')
            object.__setattr__(self,name,value)
        def get_info(self):return ProviderInfo(id='fixture',display_name='Fixture',defaults={
            'model':'mount-default','context_window':state.window,'max_output_tokens':321})
        def parse_tool_calls(self,response):return []
        def decision(self,value,options,context_estimate=0):
            state.budgets.append(value.model_dump_json());state.options.append(copy.deepcopy(options))
            return {'estimated_input_tokens':100,'input_limit_tokens':state.allowance,'max_output_tokens':321,
                    'context_token_budget':context_estimate,
                    'context_window_tokens':state.window,'measurement':{'kind':'provider_count','input_tokens':100}}
        async def request_budget(self,value,*,request_options=None,context_estimate=0,**kwargs):
            return self.decision(value,{**(request_options or {}),**kwargs},context_estimate)
        async def complete(self,value,**kwargs):
            state.calls+=1;state.transports.append(value.model_dump_json());state.dispatch_options.append(copy.deepcopy(kwargs))
            if state.retry and state.calls==1:
                from amplifier_core.llm_errors import LLMTimeoutError
                raise LLMTimeoutError('synthetic timeout',provider='fixture',retryable=True)
            return ChatResponse(content=[TextBlock(text='Done')],usage=Usage(input_tokens=100,output_tokens=1,total_tokens=101))
        async def stream(self,value,**kwargs):
            state.calls+=1;state.transports.append(value.model_dump_json());state.dispatch_options.append(copy.deepcopy(kwargs))
            yield {'content':'Do'}
            yield {'content':'ne'}
    if not streaming:del Provider.stream
    if legacy:
        async def budget(self,value,*,context_estimate=0,**kwargs):
            assert 'request_options' not in kwargs
            return self.decision(value,kwargs,context_estimate)
        Provider.request_budget=budget
    raw=Provider()
    events=ExecutionEvents(session.session_id,lambda row:None)
    c.register_capability('web.provider_observe',lambda p:events.instrument_provider(session.session_id,p))
    async def bridge(operation,args):
        if operation=='context.manifest':return {'surfaces':[],'inputIds':[]}
        return {}  # No Work obligation; no host/vendor IO in this synthetic fixture.
    await install_app_access(c,bridge)
    transform=c.get_capability('web.provider_transform')
    selected=SelectedProvider(raw,{'model':'selected-model','effort':'high','max_output_tokens':321},transform)
    await c.mount('providers',selected,name='fixture')
    surface=selected._execution_provider()
    assert isinstance(surface,SurfaceProvider) and isinstance(surface.original,RecoveringProvider)
    assert type(surface.original).__name__=='WorkRecoveringProvider'
    observed=surface.original.original
    assert observed is raw or events.provider_wrappers[id(raw)] is observed
    home=tmp_path/'home';config(home,recording=False)
    inspector=ContextInspection(c,tmp_path/'capture',home);events.context_inspectors[session.session_id]=inspector
    begin=surface.delivery.begin_work
    def actual(value,**kwargs):
        state.actual=weakref.ref(value)
        state.no_work_binding=surface.delivery.work_request(value) is None
        return begin(value,**kwargs)
    surface.delivery.begin_work=actual
    try:yield session,selected,surface,observed,raw,events,inspector,state
    finally:
        await inspector.drain()
        await session.cleanup()


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('immutable',[False,True])
@pytest.mark.parametrize('size',[32,1_000_100])
async def test_production_transform_retains_modern_and_legacy_budget(tmp_path,legacy,immutable,size):
    async with production(tmp_path,legacy=legacy,immutable=immutable) as (session,selected,surface,observed,raw,events,inspector,state):
        value=request('P'*size)
        options={'native':{'limit':1},'temperature':.2}
        await selected.request_budget(value,request_options=options,context_estimate=7)
        assert state.options[-1]=={**options,'model':'selected-model','reasoning_effort':'high'}
        await selected.complete(value,**options);await inspector.drain()
        row=inspector.store.read()['selected']
        assert row['budgetStatus']=='captured'
        assert row['limits']['requestInputAllowance']==10000
        assert row['limits']['reservedOutput']==321
        assert row['limits']['modelWindow']==20000
        assert row['budget']['measurement']['input_tokens']==100
        assert state.transports==state.budgets and state.calls==1
        assert inspector.budgets==[]
        assert state.no_work_binding
        assert row['budgetBinding']['scopedPreflightCount']==0
        assert row['budgetBinding']['fitTicket']['observed'] is True
        evidence(f'production-{legacy}-{immutable}-{size}',{
            'chain':['SelectedProvider','SurfaceProvider','WorkRecoveringProvider',type(observed).__name__],
            'appGuidanceTransform':True,'immutable':immutable,'legacy':legacy,
            'characters':len(state.transports[0]),'budgetTransportEqual':True,'header':row,
            'observerIdentityMatches':observed is raw or events.provider_wrappers[id(raw)] is observed})


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('mutation',['suffix','nested','options','type'])
async def test_production_post_admission_mutation_is_not_attributed(tmp_path,legacy,mutation):
    async with production(tmp_path,legacy=legacy) as (session,selected,surface,observed,raw,events,inspector,state):
        value=request('M'*1_000_100);opts={'native':{'limit':1}}
        await selected.request_budget(value,request_options=opts)
        async def admission(row):
            await asyncio.sleep(0)
            if mutation=='suffix':state.actual().messages[-1].content+='AFTER_ADMISSION'
            elif mutation=='nested':state.actual().messages[-1].metadata['new']={'nested':'changed'}
            else:opts['native']['limit']=True if mutation=='type' else 2
        events.admission_guard=admission
        await selected.complete(value,**opts);await inspector.drain()
        assert state.calls==1
        row=inspector.store.read()['selected'];assert row['budgetStatus']=='request_changed' and row['budget']=={}


@pytest.mark.parametrize('legacy',[False,True])
async def test_production_small_nested_call_does_not_borrow_legacy_budget(tmp_path,legacy):
    async with production(tmp_path,legacy=legacy) as (session,selected,surface,observed,raw,events,inspector,state):
        value=request();await selected.request_budget(value)
        nested=False
        async def admission(row):
            nonlocal nested
            if not nested:
                nested=True
                await observed.complete(state.actual(),model='selected-model',reasoning_effort='high')
        events.admission_guard=admission
        await selected.complete(value);await inspector.drain()
        rows=inspector.store.index();assert len(rows)==2
        assert sum(row['budgetStatus']=='captured' for row in rows)==1
        assert inspector.budgets==[]


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('streaming',[False,True])
async def test_production_initialized_session_executes_large_request(tmp_path,legacy,streaming):
    async with production(tmp_path,legacy=legacy,streaming=streaming) as (session,selected,surface,observed,raw,events,inspector,state):
        # The real loop also reserves space for its injected host instructions.
        # Use honest synthetic capacity for its large local-estimate path; do not
        # weaken the loop's guard merely to preserve the direct-call fixture cap.
        state.allowance=1_000_000;state.window=2_000_000
        async def instructions():return 'L'*1_000_100
        await session.coordinator.get('context').set_system_prompt_factory(instructions)
        assert await session.execute('Finish without tools.')=='Done'
        await inspector.drain();row=inspector.store.read()['selected']
        evidence(f'production-session-{legacy}-{streaming}',{
            'header':row,'characters':len(state.transports[0]),'appGuidanceTransform':True,
            'providerCalls':state.calls,'budgetCalls':len(state.budgets),
            'budgetOptions':state.options,
            'dispatchOptions':[{k:v for k,v in options.items() if k!='tools'} for options in state.dispatch_options],
            'streamToolTypes':[type(tool).__name__ for tool in state.dispatch_options[-1].get('tools',[])],
            'budgetTransportEqual':state.transports[-1]==state.budgets[-1]})
        assert len(state.transports[0])>1_000_000
        if streaming:
            # The installed loop adds live tool objects only at stream entry,
            # after preflight with {}. Those opaque options must fail closed.
            actual=state.dispatch_options[-1]
            assert {k:v for k,v in actual.items() if k!='tools'}==state.options[-1]
            assert actual['tools'] and type(actual['tools'][0]).__name__=='AppControl'
            assert row['budgetStatus']=='binding_unsupported' and row['budget']=={}
            return
        assert state.dispatch_options[-1]==state.options[-1]
        assert row['budget']['measurement']['input_tokens']==100
        assert row['limits']['requestInputAllowance']==state.allowance and row['limits']['reservedOutput']==321
        assert row['limits']['modelWindow']==state.window


async def test_production_recovery_retries_without_reusing_consumed_ticket(tmp_path,monkeypatch):
    from amplifier_web import request_recovery
    monkeypatch.setattr(request_recovery,'DELAYS',(0,0))
    async with production(tmp_path,legacy=True) as (session,selected,surface,observed,raw,events,inspector,state):
        state.retry=True
        value=request('R'*1_000_100);await selected.request_budget(value)
        await selected.complete(value);await inspector.drain()
        rows=inspector.store.index();assert state.calls==2 and len(rows)==2
        assert sum(r['budgetStatus']=='captured' for r in rows)==1
        assert rows[0]['budget']=={}


@pytest.mark.parametrize('failure',['cancel','quota'])
async def test_production_admission_refusal_retires_without_execution(tmp_path,failure):
    async with production(tmp_path) as (session,selected,surface,observed,raw,events,inspector,state):
        value=request();await selected.request_budget(value)
        ticket=surface._capture_tickets[-1]
        async def admission(row):
            await asyncio.sleep(0)
            if failure=='cancel':raise asyncio.CancelledError()
            raise ValueError('quota refusal')
        events.admission_guard=admission
        with pytest.raises(asyncio.CancelledError if failure=='cancel' else ValueError):await selected.complete(value)
        assert state.calls==0 and ticket.fitted is None


@pytest.mark.parametrize('immutable',[False,True])
async def test_production_stream_identity_and_cross_task_next_close(tmp_path,immutable):
    async with production(tmp_path,legacy=True,immutable=immutable) as (session,selected,surface,observed,raw,events,inspector,state):
        value=request('S'*1_000_100);await selected.request_budget(value)
        iterator=selected.stream(value)
        assert not events.nodes
        await anext(iterator);assert capture.DISPATCH.get() is None
        await asyncio.create_task(anext(iterator));assert capture.DISPATCH.get() is None
        await asyncio.create_task(iterator.aclose());await inspector.drain()
        assert state.calls==1 and inspector.store.index()[0]['budgetStatus']=='captured'


def test_effective_option_merge_is_type_sensitive_and_explicit_wins():
    assert capture.budget_options({'x':1,'model':'nested'},{'model':'selected','context_estimate':99})==capture.normalized_options({'x':1,'model':'selected'})
    assert capture.budget_options({'x':1},{'x':True})!=capture.normalized_options({'x':1})
    assert capture.budget_options([],{'x':1}) is None


async def test_registered_identity_rejects_unknown_wrappers_swaps_cycles_and_depth(tmp_path):
    from test_provider_capture import chain
    inspector,events,raw,surface=chain(tmp_path)
    fake=SimpleNamespace(original=raw)
    # SimpleNamespace is deliberately not weak-referenceable/registrable. An
    # arbitrary .original never establishes this host's observation identity.
    with pytest.raises(TypeError):capture.provider_chain(fake)
    class Wrapper:
        def __init__(self,original):self.original=original
    unknown=Wrapper(raw)
    assert not capture.matches_provider(events,unknown,raw)
    known=Wrapper(raw);capture.bind_wrapper(known,raw)
    assert capture.matches_provider(events,known,raw)
    class EqualPlugin(Wrapper):
        def __hash__(self):return hash(known)
        def __eq__(self,other):return other is known or self is other
    assert not capture.matches_provider(events,EqualPlugin(raw),raw)
    known.original=Wrapper(raw)
    assert not capture.matches_provider(events,known,raw)
    cycle=Wrapper(None);cycle.original=cycle;capture.bind_wrapper(cycle,cycle)
    assert capture.provider_chain(cycle) is None
    current=raw
    for _ in range(8):
        wrapper=Wrapper(current);capture.bind_wrapper(wrapper,current);current=wrapper
    assert capture.provider_chain(current) is None
