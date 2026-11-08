"""Prepared image guards are execution checks, independent of numeric capture."""
import asyncio
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest
from amplifier_web.surface_delivery import SurfaceProvider
from test_context_inspection import observed, request
from test_provider_capture import NoWorkDelivery


@pytest.mark.parametrize('boundary', ['metadata', 'admission'])
@pytest.mark.parametrize('mutation', ['source', 'owner', 'request', 'options'])
@pytest.mark.parametrize('observer_enabled', [False, True])
async def test_projection_refuses_post_await_change_without_work_or_ticket(
        tmp_path, boundary, mutation, observer_enabled):
    observer, events, provider = observed(tmp_path, recording=False)
    state = {'valid': True, 'value': None}
    def validate():
        if not state['valid']:raise ValueError('Prepared computer screenshot is no longer current')
    def project(value):return value, validate
    state['project'] = project
    coordinator = SimpleNamespace(get_capability=lambda name: state['project'])
    delivery = NoWorkDelivery()
    original = delivery.finalize_work
    async def finalize(value, **kwargs):
        state['value'] = value
        return await original(value, **kwargs)
    delivery.finalize_work = finalize
    surface = SurfaceProvider(provider, delivery, coordinator=coordinator)
    value = request('X'*1_000_100);before = value.model_dump_json()
    options = {'custom': {'limit': 1}}
    await surface.request_budget(value, request_options=options)
    # Numeric capture unavailable must not remove image authority validation.
    surface._capture_tickets.clear()
    if not observer_enabled:events.context_inspectors.clear()
    async def change():
        await asyncio.sleep(0)
        if mutation == 'source':state['valid'] = False
        elif mutation == 'owner':state['project'] = lambda value: (value, None)
        elif mutation == 'request':state['value'].messages[-1].content += 'changed'
        else:options['custom']['limit'] = 2
    if boundary == 'metadata':
        info = provider.get_info()
        async def get_info():await change();return info
        provider.get_info = get_info
    else:
        events.admission_guard = lambda row: change()
    with pytest.raises(ValueError, match='(screenshot|projection)'):
        await surface.complete(value, **options)
    assert provider.calls == []
    assert value.model_dump_json() == before
    assert any(row.get('phase') == 'error' for row in events.nodes.values())
    await observer.drain()


async def test_projection_encoding_shared_with_capture(tmp_path, monkeypatch):
    observer, events, provider = observed(tmp_path, recording=False)
    coordinator = SimpleNamespace(get_capability=lambda name: project)
    def project(value):return value, lambda: None
    surface = SurfaceProvider(provider, NoWorkDelivery(), coordinator=coordinator)
    value=request('X'*1_000_100)
    await surface.request_budget(value)
    count=0;original=ChatRequest.model_dump_json
    def encode(self,*args,**kwargs):
        nonlocal count
        count+=1
        return original(self,*args,**kwargs)
    monkeypatch.setattr(ChatRequest,'model_dump_json',encode)
    await surface.complete(value)
    assert count==1
    await observer.drain()
    assert observer.store.read()['selected']['budgetStatus']=='captured'


async def test_suspending_diagnostic_cannot_open_post_validation_gap(tmp_path):
    observer, events, provider = observed(tmp_path, recording=False)
    state={'called':False}
    async def unsafe_begin(*args):
        state['called']=True
        await asyncio.sleep(0)
        raise AssertionError('Must never suspend at final entry')
    observer.begin=unsafe_begin
    def project(value):return value, lambda: None
    surface=SurfaceProvider(provider,NoWorkDelivery(),
        coordinator=SimpleNamespace(get_capability=lambda name:project))
    value=request()
    await surface.request_budget(value);await surface.complete(value)
    assert not state['called'] and len(provider.calls)==1


async def test_recovery_revalidates_source_without_reusing_numeric_ticket(tmp_path,monkeypatch):
    from amplifier_web.request_recovery import RecoveringProvider
    from amplifier_web import request_recovery, provider_capture
    from amplifier_core.llm_errors import ProviderUnavailableError
    from test_context_inspection import Provider
    # Keep actual recovery policy; only finite backoff duration is zero in this test.
    monkeypatch.setattr(request_recovery,'DELAYS',(0,))
    observer,events,_=observed(tmp_path,recording=False)
    class RetryProvider(Provider):
        attempts=0
        async def complete(self,value,**kwargs):
            self.attempts+=1
            if self.attempts==1:raise ProviderUnavailableError('fixture unavailable',retryable=True)
            return await super().complete(value,**kwargs)
    raw=RetryProvider();observed_provider=events.instrument_provider('root',raw)
    validations=[]
    def project(value):return value,lambda:validations.append(True)
    c=SimpleNamespace(get_capability=lambda name:project if name=='computer_use.prepare_request' else None,
        hooks=SimpleNamespace(emit=lambda *args,**kwargs:asyncio.sleep(0)))
    recovery=RecoveringProvider(observed_provider,c);provider_capture.bind_wrapper(recovery,observed_provider)
    surface=SurfaceProvider(recovery,NoWorkDelivery(),coordinator=c)
    value=request();await surface.request_budget(value);await surface.complete(value)
    await observer.drain()
    assert raw.attempts==2 and len(validations)==2
    assert observer.store.read()['selected']['budget']=={}


async def test_projection_scope_rejects_reentry_and_releases_inherited_references():
    from amplifier_web import request_projection as projection
    import weakref, gc
    value=request();provider=SimpleNamespace()
    # Registered host identity is exact; this probe uses a weak-referenceable provider.
    from test_context_inspection import Provider
    provider=Provider();events=SimpleNamespace(provider_wrappers={})
    reference=weakref.ref(value)
    gate=asyncio.Event()
    async def child():
        scope=projection._CURRENT.get()
        with pytest.raises(ValueError,match='task'):
            projection.claim(events,provider,value)
        await gate.wait()
        assert not scope['active'] and scope['request'] is None
        assert scope['validator'] is None and scope['fitted'] is None
    with projection.dispatch(provider,value,value.model_dump_json(),lambda:None,{}):
        claim=projection.claim(events,provider,value)
        with pytest.raises(ValueError,match='identity'):
            projection.claim(events,provider,value)
        task=asyncio.create_task(child())
        await asyncio.sleep(0)
        projection.release(claim)
    gate.set();await task
    del value;gc.collect()
    assert reference() is None
