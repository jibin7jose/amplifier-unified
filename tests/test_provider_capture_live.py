"""Synthetic large requests through the mounted Live / Boundary owners.

No private prompt, provider SDK or network. Observation tracing contains only
identity booleans, counts and fixed statuses, never the synthetic request body.
"""
import asyncio
import ast
import copy
import hashlib
import importlib.util
import inspect
import json
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
from amplifier_web import provider_capture as capture
from test_context_inspection import config, evidence


@pytest.mark.parametrize('measured', [False, True])
@pytest.mark.parametrize('manager', [False, True])
async def test_live_boundary_large_budget(tmp_path, monkeypatch, measured, manager):
    modules=('loop-live','context-managed')
    paths={name:Path(importlib.util.find_spec('amplifier_module_'+name.replace('-','_')).origin).parent for name in modules}
    plan={'session':{'orchestrator':{'module':'loop-live','config':{'max_iterations':2,'min_delay_between_calls_ms':0}},
                     'context':{'module':'context-managed','config':{'token_meter':'actual'}}}}
    session=await PreparedBundle(plan,BundleModuleResolver(paths),Bundle(name='live-ticket',session=plan['session'])).create_session(session_id='live-ticket')
    c=session.coordinator;trace=[];state=SimpleNamespace(budgets=[],wire=[],options=[])
    observe=capture.observe
    def traced(operation,*args):
        result=observe(operation,*args)
        trace.append({'operation':operation.__name__,'resultNone':result is None,'resultFalse':result is False})
        return result
    monkeypatch.setattr(capture,'observe',traced)
    class Provider:
        name='fixture';config={'raw':False}
        def get_info(self):return ProviderInfo(id='fixture',display_name='Fixture',capabilities=['request_budget:provider_count'] if measured else [],defaults={'model':'fixture','context_window':2_000_000,'max_output_tokens':321})
        def parse_tool_calls(self,response):return []
        async def request_budget(self,value,*,request_options=None,context_estimate=0,**kwargs):
            state.budgets.append(hashlib.sha256(value.model_dump_json().encode()).hexdigest());state.options.append(copy.deepcopy(request_options))
            return {'estimated_input_tokens':100,'input_limit_tokens':1_000_000,'max_output_tokens':321,'context_token_budget':context_estimate,'context_window_tokens':2_000_000,'measurement':{'kind':'provider_count','input_tokens':100}}
        async def complete(self,value,**kwargs):
            state.wire.append({'sha256':hashlib.sha256(value.model_dump_json().encode()).hexdigest(),'characters':len(value.model_dump_json()),'options':kwargs})
            return ChatResponse(content=[TextBlock(text='Done')],usage=Usage(input_tokens=100,output_tokens=1,total_tokens=101))
    raw=Provider();await c.mount('providers',raw,name='fixture')
    events=ExecutionEvents(session.session_id,lambda row:None)
    async def bridge(operation,args):return {'surfaces':[],'inputIds':[]} if operation=='context.manifest' else {}
    await install_app_access(c,bridge)
    loop=c.get('orchestrator');loop.root_provider=SelectedProvider(raw,{'model':'selected','effort':'high','max_output_tokens':321})
    # Execute the exact production ObservedHost class, not a hand-flattened
    # provider chain. Its closure's external activity/checkpoint/bridge IO is
    # replaced with local observation; prepare_execution itself is unchanged.
    from amplifier_web import runtime_worker
    tree=ast.parse(Path(runtime_worker.__file__).read_text())
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name=='ObservedHost')
    from amplifier_module_loop_live.host import HostAdapter
    original_host=HostAdapter()
    from amplifier_module_loop_live.runtime import Runtime, Input
    finished=asyncio.Event()
    def observe_runtime(event):
        if event['type']=='generation.finished':finished.set()
    runtime=Runtime(session.session_id,observer=observe_runtime)
    host=runtime_worker.Worker();host.telemetry=events;host.runtime=runtime
    host.install_collaboration_checkpoint=lambda c:None
    host.app_access_bridge=lambda c:bridge
    monkeypatch.setattr(runtime_worker,'publish',lambda value:None)
    if manager:c.register_capability('live.runtime',runtime)
    scope={'original_host':original_host,'host':host,'install_app_access':install_app_access}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),runtime_worker.__file__,'exec'),scope)
    c.register_capability('live.host',scope['ObservedHost']())
    home=tmp_path/'home';config(home,recording=False)
    inspector=ContextInspection(c,tmp_path/'capture',home);events.context_inspectors[session.session_id]=inspector
    async def instructions():return 'X'*1_000_100
    await c.get('context').set_system_prompt_factory(instructions)
    execution=None
    try:
        if manager:
            execution=asyncio.create_task(session.execute(''))
            await runtime.submit(Input('user','Complete without tools.'))
            await asyncio.wait_for(finished.wait(),20)
            await runtime.submit(Input('stop'))
            assert await execution=='Done'
        else:
            assert await session.execute('Complete without tools.')=='Done'
        await inspector.drain();row=inspector.store.read()['selected']
        evidence(f'live-boundary-{measured}-{manager}',{'loop':type(loop).__name__,'context':type(c.get('context')).__name__,'trace':trace,'wire':state.wire,'budgetHashes':state.budgets,'options':state.options,'header':row})
        assert row['budgetStatus']=='captured'
        assert row['budget']['measurement']['input_tokens']==100
        assert state.wire[-1]['sha256']==state.budgets[-1]
        assert row['captureDiagnostic']['dispatch']=='observed'
        assert row['captureDiagnostic']['fit']=='created'
        assert row['captureDiagnostic']['claim']=='claimed'
        assert row['captureDiagnostic']['consume']=='captured'
    finally:
        if execution is not None and not execution.done():
            await runtime.submit(Input('stop'))
            await asyncio.wait_for(execution,5)
        await inspector.drain();await session.cleanup()
