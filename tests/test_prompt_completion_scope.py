"""Completion-hook model work retains accounting without becoming chat output."""
import asyncio
from types import SimpleNamespace

import pytest
from amplifier_core import HookRegistry, HookResult
from amplifier_core.message_models import ChatResponse, TextBlock, Usage

from amplifier_web.execution_events import CALL_PURPOSE, ExecutionEvents
from amplifier_web.host.prompt_events import install
from amplifier_web.runtime_worker import Worker


def host(monkeypatch, hooks=None):
    hooks, capabilities, observed = hooks or HookRegistry(), {}, []

    async def messages():
        return [{'role': 'user', 'content': 'A user request'},
                {'role': 'assistant', 'content': [{'type': 'text', 'text': 'A real answer'}]}]

    co = SimpleNamespace(session_id='root', hooks=hooks,
        get_capability=capabilities.get, register_capability=capabilities.__setitem__,
        get=lambda key: {'context': SimpleNamespace(get_messages=messages), 'providers': {}}.get(key))
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=co)
    worker.runtime = SimpleNamespace(session_id='root', generation={'id': 'generation'})
    worker.telemetry = ExecutionEvents('root', observed.append)
    worker.telemetry.turn_id = 'input'
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', observed.append)
    worker.install_activity(co)
    install(co)
    return co, worker, observed


async def complete(co):
    await co.hooks.emit('prompt:submit', {'session_id': 'root', 'prompt': 'A user request'})
    await co.hooks.emit('orchestrator:complete', {'session_id': 'root', 'status': 'complete'})

@pytest.mark.parametrize('identity,event_type', [('root','runtime.activity'), ('child','worker.activity')])
async def test_model_activity_uses_same_thinking_label_without_changing_phase(monkeypatch, identity, event_type):
    co, worker, observed = host(monkeypatch)
    if identity != 'root':
        co = SimpleNamespace(session_id=identity, hooks=HookRegistry(), get=lambda _: {},
            get_capability=lambda _: None, register_capability=lambda *args: None)
        worker.install_activity(co)
    observed.clear()
    await co.hooks.emit('provider:request', {'session_id':identity})
    activity = next(row for row in observed if row.get('type')==event_type)
    assert activity['phase']=='model'
    assert activity['detail']=='Thinking...'
    await co.hooks.emit('provider:retry', {'session_id':identity,'attempt':2,'max_retries':3})
    retry = [row for row in observed if row.get('type')==event_type][-1]
    assert retry['phase']=='retrying'
    assert retry['retryAttempt']==2 and retry['retryMax']==3
    assert 'retrying (2 of 3)' in retry['detail']


async def test_completion_evaluation_is_private_but_usage_and_response_payload_survive(monkeypatch):
    co, worker, observed = host(monkeypatch)
    visibility, payloads = [], []

    class Provider:
        def get_info(self):
            return SimpleNamespace(id='fixture', defaults={'model': 'offline'})

        async def complete(self, request):
            visibility.append(co.get_capability('live.public_stream')())
            return ChatResponse(content=[TextBlock(text='auxiliary decision')],
                usage=Usage(input_tokens=4, output_tokens=2, total_tokens=6))

    provider = worker.telemetry.instrument_provider('root', Provider())

    async def evaluate(event, data):
        payloads.append({key: data[key] for key in ('session_id', 'prompt', 'response')})
        await provider.complete(SimpleNamespace(model='offline'))
        return HookResult()

    co.hooks.register('prompt:complete', evaluate, name='completion-evaluation')
    await provider.complete(SimpleNamespace(model='offline'))
    await complete(co)
    await provider.complete(SimpleNamespace(model='offline'))
    assert visibility == [True, False, True]
    assert payloads == [{'session_id': 'root', 'prompt': 'A user request', 'response': 'A real answer'}]
    rows = [item['event'] for item in observed if item.get('type') == 'execution.event'
            and item['event']['kind'] == 'llm' and item['event']['phase'] == 'completed']
    assert len(rows) == 3
    assert [row['usage']['totalTokens'] for row in rows] == [6, 6, 6]
    assert [row['turnId'] for row in rows] == ['input'] * 3
    assert rows[1]['purpose'] == 'prompt_completion'
    assert rows[0]['purpose'] is None and rows[2]['purpose'] is None
    assert CALL_PURPOSE.get() is None


@pytest.mark.parametrize('exception', [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize('prior', [None, {'label': 'Outer owner', 'purpose': 'outer'}])
async def test_failed_or_cancelled_completion_restores_prior_scope(monkeypatch, exception, prior):
    class InterruptedHooks:
        def __init__(self):
            self.registry = HookRegistry()
            self.callbacks = {}

        def register(self, event, callback, **kwargs):
            self.callbacks[event] = callback
            return self.registry.register(event, callback, **kwargs)

        async def emit(self, event, data):
            if event == 'prompt:complete':
                assert CALL_PURPOSE.get()['purpose'] == 'prompt_completion'
                raise exception('completion interrupted')
            return await self.registry.emit(event, data)

    hooks = InterruptedHooks()
    co, _, _ = host(monkeypatch, hooks)
    # Invoke the host callback itself so HookRegistry's own hook-error handling
    # cannot hide an exception before the scope-reset assertion.
    observe = hooks.callbacks['orchestrator:complete']
    await observe('prompt:submit', {'session_id': 'root', 'prompt': 'A user request'})
    token = CALL_PURPOSE.set(prior)
    try:
        with pytest.raises(exception, match='completion interrupted'):
            await observe('orchestrator:complete', {'session_id': 'root', 'status': 'complete'})
        assert CALL_PURPOSE.get() is prior
    finally:
        CALL_PURPOSE.reset(token)
    assert co.get_capability('live.public_stream')()


async def test_background_completion_task_keeps_private_scope_without_affecting_other_task(monkeypatch):
    co, _, _ = host(monkeypatch)
    release = asyncio.Event()
    tasks = []

    async def delayed():
        await release.wait()
        return co.get_capability('live.public_stream')()

    async def evaluate(event, data):
        tasks.append(asyncio.create_task(delayed()))
        return HookResult()

    co.hooks.register('prompt:complete', evaluate, name='background-evaluation')
    try:
        await complete(co)
        assert co.get_capability('live.public_stream')()
        release.set()
        assert await tasks[0] is False
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
