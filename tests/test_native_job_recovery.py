"""Native wire calls cross the host boundary into the actual loop job ledger."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("amplifier_module_provider_openai.native")
pytest.importorskip("amplifier_module_loop_live")

from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_core.llm_errors import LLMError
from amplifier_module_loop_live.job_store import JobStore
from amplifier_module_loop_live.runtime import Runtime
from amplifier_module_loop_live.scope import LIVE_OWNER, NATIVE_REQUEST as LOOP_REQUEST
from amplifier_module_provider_openai import OpenAIProvider
from amplifier_module_provider_openai.native import NativeResponsesProvider, NATIVE_REQUEST as PROVIDER_REQUEST
from websockets.protocol import State

from amplifier_web.host.session import SelectedProvider
from amplifier_web.native_provider import install_native


@pytest.fixture
async def native_host(tmp_path, monkeypatch, request):
    async def deny_network(*args, **kwargs):
        raise AssertionError("Native recovery fixtures must not use a network transport")

    monkeypatch.setattr("httpx.AsyncClient.send", deny_network)
    monkeypatch.setattr("websockets.connect", deny_network)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "web"))
    session = AmplifierSession({
        "session": {
            "orchestrator": {"module": "loop-live", "config": {
                "configured_bundle": True, "native_provider": True,
                "background_delegate": True, "min_delay_between_calls_ms": 0,
            }},
            "context": {"module": "context-simple"},
        }, "providers": [],
    })
    await session.initialize()
    coordinator = session.coordinator
    loop = coordinator.get("orchestrator")
    options = getattr(request, "param", "gpt-6-astra")
    options = options if isinstance(options, dict) else {"model": options}
    model = options.get("model", "gpt-6-astra")
    provider = OpenAIProvider(api_key="fixture", config={
        "default_model": options.get("default_model", model),
        "extra_request_params": options.get("extra_request_params", {})})
    await coordinator.mount("providers", provider, name="configured")
    selected = SelectedProvider(provider, {
        "instance": "configured", "model": model, "effort": "high",
    })
    loop.root_provider = selected
    loop.coordinator = coordinator
    loop.context = coordinator.get("context")
    loop.tools = coordinator.get("tools")
    loop.hooks = coordinator.hooks
    loop.runtime = Runtime(session_id=coordinator.session_id)
    loop.runtime.generation = {"id": "native-recovery-generation", "input_ids": ["native-recovery-input"]}
    coordinator.register_capability("live.runtime", loop.runtime)
    ledger = JobStore(tmp_path / "jobs")
    coordinator.register_capability("live.jobs", ledger)
    await install_native(loop, coordinator, coordinator.get("providers"))
    native = coordinator.get_capability("web.native_provider").provider
    from amplifier_web.execution_events import ExecutionEvents
    telemetry = ExecutionEvents(coordinator.session_id, lambda event: None)
    selected.execution_adapter = lambda provider: telemetry.instrument_provider(coordinator.session_id, provider)
    # The real serializer and native frame processor run; no SDK/network call can.
    client = SimpleNamespace(base_url="https://api.openai.com/v1", close=AsyncMock())
    client.with_options = lambda **kwargs: client
    native._client = client
    native._provider_count_available = lambda: False
    native._guard_assembled_params_with_provider_count = AsyncMock(return_value=None)
    native._connect = AsyncMock()
    try:
        yield SimpleNamespace(session=session, loop=loop, coordinator=coordinator, native=native,
                              selected=selected, ledger=ledger, telemetry=telemetry)
    finally:
        for job in loop.jobs.values():
            loop.cancel_job(job)
        await asyncio.gather(*(job["task"] for job in loop.jobs.values()), return_exceptions=True)
        ledger.close()
        # The fixture has no real SDK client to close.
        native._client = None
        await session.cleanup()


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_native_frames_persist_exact_wire_call_through_real_loop_and_restart(native_host, outcome):
    fixture = native_host
    native, loop, ledger = fixture.native, fixture.loop, fixture.ledger
    executions, approvals = [], []

    class Delegate:
        name = "delegate"
        description = "Synthetic bounded work"
        input_schema = {"type": "object", "properties": {"instruction": {"type": "string"}}}

        async def execute(self, arguments):
            executions.append(copy.deepcopy(arguments))
            return ToolResult(success=True, output={"report": "original result"})

    async def before_tool(event, data):
        approvals.append(data["tool_call_id"])
        return HookResult()

    await fixture.coordinator.mount("tools", Delegate(), name="delegate")
    loop.tools = fixture.coordinator.get("tools")
    fixture.coordinator.hooks.register("tool:pre", before_tool, name="native-job-test")
    wire = {"type": "function_call", "id": "wire-item-1", "call_id": "native-call-1",
            "name": "delegate", "arguments": '{ "instruction" : "original work" }',
            "async": True, "status": "completed"}
    frames = [
        {"type": "response.created", "response": {"id": "response-1"}},
        {"type": "response.output_item.done", "item": wire},
    ]
    if outcome == "completed":
        frames.append({"type": "response.completed", "response": {
            "id": "response-1", "status": "completed", "model": "gpt-6-astra",
            "output": [wire], "usage": {"input_tokens": 1, "output_tokens": 1},
        }})
    elif outcome == "failed":
        frames.append({"type": "response.failed", "response": {"error": {"code": "fixture_failure"}}})

    async def receive():
        if frames:
            return json.dumps(frames.pop(0))
        raise asyncio.CancelledError

    socket = SimpleNamespace(state=State.OPEN, send=AsyncMock(), close=AsyncMock(), recv=receive)
    native.socket = socket
    request = ChatRequest(messages=[Message(role="user", content="synthetic work")], tools=[
        ToolSpec(name="delegate", parameters=Delegate.input_schema, description=Delegate.description),
    ], max_output_tokens=32)
    outer_loop, outer_provider = SimpleNamespace(async_calls={}), object()
    loop_token = LOOP_REQUEST.set(outer_loop)
    provider_token = PROVIDER_REQUEST.set(outer_provider)
    owner_token = LIVE_OWNER.set(loop)
    try:
        if outcome == "completed":
            await fixture.selected.complete(request)
        else:
            with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else LLMError):
                await fixture.selected.complete(request)
        assert LOOP_REQUEST.get() is outer_loop
        assert PROVIDER_REQUEST.get() is outer_provider
        assert native.owner is None
    finally:
        LIVE_OWNER.reset(owner_token)
        PROVIDER_REQUEST.reset(provider_token)
        LOOP_REQUEST.reset(loop_token)

    await asyncio.wait_for(asyncio.gather(*(job["task"] for job in loop.jobs.values())), 3)
    assert approvals == [wire["call_id"]] and executions == [{"instruction": "original work"}]
    assert socket.send.await_count == 1  # No retry after failure/cancellation.
    assert native.request_uncertain is (outcome != "completed")
    assert ledger.rows[wire["call_id"]]["native_call"] == wire
    ledger.close()
    reopened = JobStore(ledger.directory)
    try:
        recovered, reports = reopened.recover([])
        assistant = next(message for message in recovered if message["role"] == "assistant")
        assert assistant["metadata"]["converge_native_async_calls"] == [wire]
        assert reports[0]["status"] == "returned"
        assert reopened.recover(recovered) == (recovered, [])
        assert len(executions) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("fallback", ["utility", "no_thinking", "model", "foreign_owner", "selection"])
@pytest.mark.parametrize("outcome", ["returned", "failed", "cancelled"])
async def test_regular_call_clears_inherited_native_scope_and_restores_it(native_host, monkeypatch, fallback, outcome):
    fixture = native_host
    native, loop = fixture.native, fixture.loop
    observed = []

    async def ordinary(self, request, **kwargs):
        observed.append((LOOP_REQUEST.get(), PROVIDER_REQUEST.get()))
        if outcome == "failed":
            raise ValueError("ordinary failure")
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return "ordinary"

    monkeypatch.setattr(OpenAIProvider, "complete", ordinary)
    request = ChatRequest(messages=[Message(role="user", content="utility")])
    options = {}
    owner = loop
    if fallback == "utility":
        request.metadata = {"stream": False}
    elif fallback == "no_thinking":
        options["extended_thinking"] = False
    elif fallback == "model":
        fixture.selected.selection["model"] = "gpt-5.6-terra"
    elif fallback == "foreign_owner":
        owner = SimpleNamespace(coordinator=object())
    else:
        fixture.selected.selection["effort"] = "low"
    owner_token = LIVE_OWNER.set(owner)
    loop_token = LOOP_REQUEST.set(native)
    provider_token = PROVIDER_REQUEST.set(native)
    try:
        if outcome == "returned":
            assert await fixture.selected.complete(request, **options) == "ordinary"
        else:
            with pytest.raises(ValueError if outcome == "failed" else asyncio.CancelledError):
                await fixture.selected.complete(request, **options)
        assert observed == [(None, None)]
        assert LOOP_REQUEST.get() is native and PROVIDER_REQUEST.get() is native
        assert not native.request_uncertain and not fixture.ledger.rows
        assert not list(fixture.ledger.directory.glob("job-*.json"))
    finally:
        PROVIDER_REQUEST.reset(provider_token)
        LOOP_REQUEST.reset(loop_token)
        LIVE_OWNER.reset(owner_token)


async def test_private_native_method_without_provider_scope_cannot_claim_job_identity(native_host, monkeypatch):
    native = native_host.native
    observed = []

    async def response(self, params):
        observed.append(LOOP_REQUEST.get())
        return "private fixture"

    monkeypatch.setattr(NativeResponsesProvider, "_native_response", response)
    loop_token = LOOP_REQUEST.set(native)
    provider_token = PROVIDER_REQUEST.set(None)
    try:
        assert await native._native_response({}) == "private fixture"
        assert observed == [None]
        assert LOOP_REQUEST.get() is native
    finally:
        PROVIDER_REQUEST.reset(provider_token)
        LOOP_REQUEST.reset(loop_token)


ELIGIBLE_MODELS = ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra"]


@asynccontextmanager
async def steering_connection(fixture, tools=None):
    """Real installed host, loop runtime and serializer; only the socket is scripted."""
    from amplifier_web.coordination_delivery import CoordinationDelivery
    loop, c, native = fixture.loop, fixture.coordinator, fixture.native
    await loop.runtime.emit("generation.started", generation_id="native-generation",
                            initial_input_id="original-input")
    c.register_capability("web.provider_call", lambda provider, request, invoke, **kwargs:
        fixture.telemetry.provider_call(c.session_id, provider, request, invoke, **kwargs))
    async def deny_bridge(*args, **kwargs):
        raise AssertionError("Ordinary steering must not invent Work authority")
    c.register_capability("web.surface_delivery", CoordinationDelivery(None, deny_bridge))
    # The pinned loop correctly declines its boundary-only contract for native.
    c.register_capability("live.steering", {"version": 1, "mode": "unavailable"})
    await loop.context.add_message({"role": "user", "content": "Synthetic parent"})
    events = asyncio.Queue()
    socket = SimpleNamespace(state=State.OPEN, send=AsyncMock(), close=AsyncMock(), recv=events.get)
    native.socket = socket
    token = LIVE_OWNER.set(loop)
    task = asyncio.create_task(fixture.selected.complete(ChatRequest(
        messages=[Message(**m) for m in await loop.context.get_messages()],
        tools=tools, max_output_tokens=32)))
    LIVE_OWNER.reset(token)  # Control input is not executing inside the model's task.
    try:
        await events.put(json.dumps({"type": "response.created", "response": {"id": "parent"}}))
        await loop.runtime.wait_for(lambda e: e["type"] == "native.response.created", 3)
        yield task, events, socket, c.get_capability("web.native_provider")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def steer_from_worker(fixture):
    from amplifier_web.conversation_steering import submit
    return await submit(SimpleNamespace(coordinator=fixture.coordinator), fixture.loop.runtime, {
        "text": "Synthetic new direction", "inputId": "steer-input",
        "targetGenerationId": fixture.loop.runtime.generation["id"],
    }, None, lambda: 0)


def native_terminal(model, identity, output=()):
    return {"id": identity, "model": model, "status": "completed", "output": list(output),
            "usage": {"input_tokens": 3, "output_tokens": 2}}


@pytest.mark.parametrize("native_host", ELIGIBLE_MODELS, indirect=True)
@pytest.mark.parametrize("ending", ["applied", "disconnect"])
async def test_worker_native_steering_exact_models_and_restart(native_host, ending):
    fixture = native_host
    model = fixture.selected.selection["model"]
    async with steering_connection(fixture) as (task, events, socket, host):
        assert host.status()["supported"] and host.status()["model"] == model
        assert host.status()["activeNativeResponse"] == {"responseId": "parent", "generationId": "native-generation"}
        result = await steer_from_worker(fixture)
        assert result["accepted"] and result["disposition"] == "queued"
        assert result["mode"] == "native" and result["phase"] == "sent"
        assert host.status()["activeNativeResponse"] is None  # One steer is already in flight.
        # Duplicate admission returns its receipt, never another native send.
        assert await steer_from_worker(fixture) == result
        assert socket.send.await_count == 2
        payloads = [json.loads(call.args[0]) for call in socket.send.await_args_list]
        assert payloads[0]["type"] == "response.create" and payloads[0]["model"] == model
        assert payloads[1] == {"type": "response.steer", "previous_response_id": "parent",
                               "input": "Synthetic new direction"}
        await events.put(json.dumps({"type": "response.steer.accepted", "steer": {"id": "steer-1"}}))
        await fixture.loop.runtime.wait_for(lambda e: e["type"] == "steering.accepted", 3)
        assert host.state["sends"][-1]["outcome"] == "pending"
        assert fixture.loop.runtime.generation["input_ids"] == []
        assert not any(e["type"] == "steering.applied" for e in fixture.loop.runtime.events)
        # Snapshot the actual accepted bytes, not a fabricated restart record.
        crash_bytes = host.path.read_bytes()
        if ending == "applied":
            parent = native_terminal(model, "parent")
            parent.update(status="incomplete", incomplete_details={"reason": "steered"})
            await events.put(json.dumps({"type": "response.incomplete", "response": parent}))
            await events.put(json.dumps({"type": "response.created", "response": {"id": "successor"}}))
            await fixture.loop.runtime.wait_for(lambda e: e["type"] == "steering.applied", 3)
            assert host.state["sends"][-1]["outcome"] == "applied"
            applied = next(e for e in fixture.loop.runtime.events if e["type"] == "steering.applied")
            assert applied["target_generation_id"] == "native-generation"
            await events.put(json.dumps({"type": "response.completed",
                                        "response": native_terminal(model, "successor")}))
            await asyncio.wait_for(task, 3)
            assert fixture.loop.runtime.generation["input_ids"] == ["steer-input"]
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert host.state["sends"][-1]["outcome"] == "unknown"
            with pytest.raises(LLMError, match="explicit recovery"):
                await fixture.selected.complete(ChatRequest(
                    messages=[Message(role="user", content="Do not replay")], metadata={"stream": False}))
        assert socket.send.await_count == 2
        assert sum((m.get("metadata") or {}).get("live_input_id") == "steer-input"
                   for m in await fixture.loop.context.get_messages()) == 1
        assert not fixture.loop.pending and not fixture.loop.runtime.pending_steering
        # Inspect each real durable state with a fresh host, preserving final
        # application evidence while reconciling accepted crash bytes to unknown.
        from amplifier_web.native_provider import NativeProviderHost
        current = NativeProviderHost(fixture.coordinator, fixture.loop, host.directory)
        assert current.state["sends"][-1]["outcome"] == ending.replace("disconnect", "unknown")
        host.path.write_bytes(crash_bytes)
        for _ in range(2):
            restored = NativeProviderHost(fixture.coordinator, fixture.loop, host.directory)
            assert restored.state["sends"][-1]["outcome"] == "unknown"
            assert restored.state["sends"][-1]["steerId"] == "steer-1"
        assert socket.send.await_count == 2


async def test_configured_native_without_active_response_is_not_immediate_eligibility(native_host):
    host = native_host.coordinator.get_capability("web.native_provider")
    assert host.status()["steering"] == "native"  # Configured, not actual readiness.
    assert host.status()["activeNativeResponse"] is None
    result = await steer_from_worker(native_host)
    assert not result["accepted"] and result["disposition"] == "held"
    assert "No active native response" in result["reason"]
    assert not native_host.loop.runtime.accepted
    assert not native_host.loop.runtime.pending_steering


@pytest.mark.parametrize("native_host", ELIGIBLE_MODELS, indirect=True)
@pytest.mark.parametrize("decision", ["allow", "deny"])
async def test_pending_steering_returns_actual_approved_tool_result_on_same_connection(native_host, decision):
    fixture = native_host
    model = fixture.selected.selection["model"]
    approvals, executions = [], []
    class Lookup:
        name = "lookup"
        description = "Synthetic lookup"
        input_schema = {"type": "object", "properties": {}}
        async def execute(self, arguments):
            executions.append(arguments)
            return ToolResult(success=True, output={"value": "actual-result"})
    async def approve(event, data):
        approvals.append(data["tool_call_id"])
        return HookResult(action="deny", reason="Synthetic denial") if decision == "deny" else HookResult()
    await fixture.coordinator.mount("tools", Lookup(), name="lookup")
    fixture.coordinator.hooks.register("tool:pre", approve, name="native-pending-approval")
    spec = ToolSpec(name="lookup", description="Synthetic lookup", parameters=Lookup.input_schema)
    async with steering_connection(fixture, [spec]) as (task, events, socket, host):
        await steer_from_worker(fixture)
        await events.put(json.dumps({"type": "response.steer.accepted", "steer": {"id": "steer-1"}}))
        wire = {"type": "function_call", "id": "wire-lookup", "call_id": "call-lookup",
                "name": "lookup", "arguments": "{}", "status": "completed"}
        await events.put(json.dumps({"type": "response.completed",
                                    "response": native_terminal(model, "parent", [wire])}))
        await events.put(json.dumps({"type": "response.steer.pending", "steer": {"id": "steer-1"},
                                    "required_input": [{"type": "function_call_output", "call_id": "call-lookup"}]}))
        response = await asyncio.wait_for(task, 3)
        assert host.state["sends"][-1]["outcome"] == "pending"
        assert host.state["sends"][-1]["awaitingRequiredInput"] is True
        assert "required_input" not in host.path.read_text()  # Private tool details stay private.
        crash = host.directory / "pending-crash"
        crash.mkdir()
        (crash / host.path.name).write_bytes(host.path.read_bytes())
        from amplifier_web.native_provider import NativeProviderHost
        restored = NativeProviderHost(fixture.coordinator, fixture.loop, crash)
        assert restored.state["sends"][-1]["outcome"] == "unknown"
        assert restored.state["sends"][-1]["awaitingRequiredInput"] is True
        assert socket.send.await_count == 2
        fixture.coordinator.register_capability("web.native_provider", host)
        await fixture.loop.context.add_message({
            "role": "assistant", "content": [b.model_dump() for b in response.content],
            "tool_calls": [c.model_dump() for c in response.tool_calls], "metadata": response.metadata})
        await fixture.loop._execute_tool_with_result(response.tool_calls[0],
            fixture.coordinator.get("tools"), fixture.loop.context, fixture.coordinator.hooks,
            fixture.coordinator)
        canonical = await fixture.loop.context.get_messages()
        actual = next(m for m in canonical if m["role"] == "tool")
        token = LIVE_OWNER.set(fixture.loop)
        continuation = asyncio.create_task(fixture.selected.complete(ChatRequest(
            messages=[Message(**m) for m in canonical], tools=[spec], max_output_tokens=32)))
        LIVE_OWNER.reset(token)
        try:
            await events.put(json.dumps({"type": "response.created", "response": {"id": "successor"}}))
            await events.put(json.dumps({"type": "response.completed",
                                        "response": native_terminal(model, "successor")}))
            await asyncio.wait_for(continuation, 3)
        finally:
            if not continuation.done():
                continuation.cancel()
            await asyncio.gather(continuation, return_exceptions=True)
        payloads = [json.loads(call.args[0]) for call in socket.send.await_args_list]
        assert [p["type"] for p in payloads] == ["response.create", "response.steer", "response.create"]
        assert payloads[-1]["previous_response_id"] == "parent"
        assert payloads[-1]["input"] == [{"type": "function_call_output", "call_id": "call-lookup",
                                        "output": [{"type": "input_text", "text": actual["content"],
                                                    "prompt_cache_breakpoint": {"mode": "explicit"}}]}]
        assert approvals == ["call-lookup"] and len(executions) == (decision == "allow")
        assert host.state["sends"][1]["outcome"] == "applied"
        assert sum((m.get("metadata") or {}).get("live_input_id") == "steer-input"
                   for m in await fixture.loop.context.get_messages()) == 1


@pytest.mark.parametrize("change", ["selection", "config_model", "unsupported_model", "effort"])
async def test_pending_steering_never_moves_to_new_configuration_or_fallback(native_host, change, monkeypatch):
    fixture = native_host
    async with steering_connection(fixture) as (task, events, socket, host):
        await steer_from_worker(fixture)
        await events.put(json.dumps({"type": "response.steer.accepted", "steer": {"id": "steer-1"}}))
        await fixture.loop.runtime.wait_for(lambda e: e["type"] == "steering.accepted", 3)
        if change == "selection":
            fixture.selected.selection["model"] = "gpt-6-sol"
        elif change == "config_model":
            fixture.native.extra_request_params["extra_body"] = {"model": "gpt-6.1-terra"}
        elif change == "unsupported_model":
            fixture.native.extra_request_params["model"] = "gpt-5.6"
        else:
            fixture.selected.selection["effort"] = "low"
        # The active send cannot be replaced, even by another eligible model.
        assert host.status()["steering"] == "request_boundary"
        ordinary = AsyncMock(side_effect=AssertionError("No fallback or replay"))
        monkeypatch.setattr(OpenAIProvider, "complete", ordinary)
        with pytest.raises(LLMError):
            await fixture.selected.complete(ChatRequest(messages=[Message(role="user", content="new")]))
        ordinary.assert_not_awaited()
        assert socket.send.await_count == 2


@pytest.mark.parametrize("native_host", [
    {"default_model": "gpt-5.6", "model": "gpt-6-sol"},
    {"model": "gpt-5.6", "extra_request_params": {"model": "gpt-6.1-terra"}},
    {"model": "gpt-5.6", "extra_request_params": {
        "model": "gpt-6-astra", "extra_body": {"model": "gpt-6-luna"}}},
], indirect=True)
async def test_actual_native_wire_and_status_share_final_model_precedence(native_host):
    fixture = native_host
    async with steering_connection(fixture) as (task, events, socket, host):
        sent = json.loads(socket.send.call_args.args[0])
        assert host.status()["supported"] and host.status()["model"] == sent["model"]
        assert sent["model"] in ELIGIBLE_MODELS
        await events.put(json.dumps({"type": "response.completed",
                                    "response": native_terminal(sent["model"], "parent")}))
        await asyncio.wait_for(task, 3)
        assert socket.send.await_count == 1


@pytest.mark.parametrize("fault", ["budget", "stop", "generation", "selection", "endpoint", "opt_out"])
async def test_native_steering_rechecks_admission_after_await(native_host, fault):
    fixture = native_host
    async with steering_connection(fixture) as (task, events, socket, host):
        async def guard(row):
            if row["label"] == "Steer provider context":
                if fault == "budget":
                    raise ValueError("Synthetic budget denial")
                if fault == "stop":
                    fixture.loop.runtime.stop_requested = True
                elif fault == "generation":
                    fixture.loop.runtime.generation["id"] = "different-generation"
                elif fault == "selection":
                    fixture.selected.selection["model"] = "gpt-6-sol"
                elif fault == "endpoint":
                    fixture.native._client.base_url = "https://proxy.example/v1"
                elif fault == "opt_out":
                    fixture.loop.config["native_provider"] = False
        fixture.telemetry.admission_guard = guard
        with pytest.raises((LLMError, ValueError)):
            await steer_from_worker(fixture)
        assert socket.send.await_count == 1  # Only the parent.
        assert not fixture.loop.pending and not fixture.loop.runtime.pending_steering
        assert not any(row["operation"] == "response.steer" and row["outcome"] == "sent"
                       for row in host.state.get("sends", []))


async def test_accepted_native_state_reinstalls_in_fresh_core_without_fallback(native_host, tmp_path, monkeypatch):
    fixture = native_host
    async with steering_connection(fixture) as (task, events, socket, host):
        await steer_from_worker(fixture)
        await events.put(json.dumps({"type": "response.steer.accepted", "steer": {"id": "steer-1"}}))
        await fixture.loop.runtime.wait_for(lambda e: e["type"] == "steering.accepted", 3)
        crash = tmp_path / "crash" / "sessions" / fixture.coordinator.session_id
        crash.mkdir(parents=True)
        (crash / host.path.name).write_bytes(host.path.read_bytes())
        for _ in range(2):
            with monkeypatch.context() as environment:
                environment.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "crash"))
                fresh = AmplifierSession({
                    "session": {"orchestrator": {"module": "loop-live", "config": {"native_provider": True}},
                                "context": {"module": "context-simple"}}, "providers": [],
                }, session_id=fixture.coordinator.session_id)
                await fresh.initialize()
                try:
                    c = fresh.coordinator
                    loop = c.get("orchestrator")
                    original = OpenAIProvider(api_key="fixture", config={"default_model": "gpt-6-astra"})
                    await c.mount("providers", original, name="configured")
                    loop.root_provider = SelectedProvider(original, copy.deepcopy(fixture.selected.selection))
                    await install_native(loop, c, c.get("providers"))
                    restored = c.get_capability("web.native_provider")
                    assert restored.state["sends"][-1]["outcome"] == "unknown"
                    assert restored.provider.request_uncertain
                    with pytest.raises(LLMError, match="explicit recovery"):
                        await loop.root_provider.complete(ChatRequest(
                            messages=[Message(role="user", content="Do not replay")], metadata={"stream": False}))
                    assert restored.provider.socket is None and restored.provider._client is None
                finally:
                    await fresh.cleanup()
        assert socket.send.await_count == 2


async def test_full_live_loop_worker_steering_emits_final_text_once(native_host):
    """Exercise the actual execute owner, not just provider completion calls."""
    from amplifier_web.coordination_delivery import CoordinationDelivery
    from amplifier_module_loop_live.runtime import Input
    fixture = native_host
    c, runtime, native = fixture.coordinator, fixture.loop.runtime, fixture.native
    c.register_capability("web.provider_call", lambda provider, request, invoke, **kwargs:
        fixture.telemetry.provider_call(c.session_id, provider, request, invoke, **kwargs))
    async def deny_bridge(*args, **kwargs):
        raise AssertionError("No Work association")
    c.register_capability("web.surface_delivery", CoordinationDelivery(None, deny_bridge))
    events = asyncio.Queue()
    native.socket = SimpleNamespace(state=State.OPEN, send=AsyncMock(), close=AsyncMock(), recv=events.get)
    socket = native.socket
    task = asyncio.create_task(fixture.session.execute("Synthetic parent"))
    try:
        await events.put(json.dumps({"type": "response.created", "response": {"id": "parent"}}))
        await runtime.wait_for(lambda e: e["type"] == "native.response.created", 3)
        result = await steer_from_worker(fixture)
        assert result["accepted"] and result["disposition"] == "queued"
        await events.put(json.dumps({"type": "response.steer.accepted", "steer": {"id": "steer-1"}}))
        await events.put(json.dumps({"type": "response.incomplete", "response": {
            **native_terminal("gpt-6-astra", "parent"), "status": "incomplete",
            "incomplete_details": {"reason": "steered"}}}))
        await events.put(json.dumps({"type": "response.created", "response": {"id": "successor"}}))
        output = {"type": "message", "id": "final-message", "role": "assistant", "status": "completed",
                  "content": [{"type": "output_text", "text": "One final answer.", "annotations": []}]}
        await events.put(json.dumps({"type": "response.completed",
                                    "response": native_terminal("gpt-6-astra", "successor", [output])}))
        await runtime.wait_for(lambda e: e["type"] == "generation.finished", 3)
        public = [e["text"] for e in runtime.events if e["type"] == "assistant.message"]
        assert public == ["One final answer."]
        assert socket.send.await_count == 2
        messages = await fixture.loop.context.get_messages()
        assert sum((m.get("metadata") or {}).get("live_input_id") == "steer-input" for m in messages) == 1
        assert sum(m["role"] == "assistant" for m in messages) == 1
        # Pinned Streaming synthesizes final blocks; enabling provider progressive
        # blocks on this graph would duplicate them. Keep that opt-in off.
        assert native.progressive_output is False
        await runtime.submit(Input("stop", ""))
        await asyncio.wait_for(task, 3)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("operation", ["response.create", "response.steer"])
@pytest.mark.parametrize("change", ["client_url", "client_replacement", "configuration", "environment"])
async def test_effective_sdk_endpoint_rechecked_at_bound_final_admission(native_host, monkeypatch, operation, change):
    import httpx
    from openai import AsyncOpenAI
    fixture = native_host
    native = fixture.native
    host = fixture.coordinator.get_capability("web.native_provider")
    requests = []
    def deny_transport(request):
        requests.append(request)
        raise AssertionError("No HTTP request belongs to the native admission fixture")
    clients = [AsyncOpenAI(api_key="fixture", base_url="https://api.openai.com/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(deny_transport)))]
    native._client = clients[0]
    entered, release = asyncio.Event(), asyncio.Event()
    prepare = native.prepare_send
    async def paused_prepare(kind, *, input_id=None):
        admission = await prepare(kind, input_id=input_id)
        if kind == operation:
            entered.set()
            await release.wait()
        return admission
    native.prepare_send = paused_prepare

    async def change_route():
        await asyncio.wait_for(entered.wait(), 3)
        if change == "client_url":
            clients[0].base_url = "https://proxy.invalid/v1"
        elif change == "client_replacement":
            replacement = AsyncOpenAI(api_key="fixture", base_url="https://proxy.invalid/v1",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(deny_transport)))
            clients.append(replacement)
            native._client = replacement
        elif change == "configuration":
            # A live SDK client, not a later config value, owns this route.
            native.base_url = "https://proxy.invalid/v1"
        else:
            monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.invalid/v1")
        release.set()

    rejected = change in {"client_url", "client_replacement"}
    task = None
    try:
        if operation == "response.create":
            token = LIVE_OWNER.set(fixture.loop)
            task = asyncio.create_task(fixture.selected.complete(ChatRequest(
                messages=[Message(role="user", content="Synthetic parent")], max_output_tokens=32)))
            LIVE_OWNER.reset(token)
            socket = SimpleNamespace(state=State.OPEN, send=AsyncMock(), close=AsyncMock(),
                recv=AsyncMock(side_effect=[
                    json.dumps({"type": "response.created", "response": {"id": "parent"}}),
                    json.dumps({"type": "response.completed", "response": native_terminal("gpt-6-astra", "parent")})]))
            native.socket = socket
            await change_route()
            if rejected:
                with pytest.raises(LLMError):
                    await asyncio.wait_for(task, 3)
                assert socket.send.await_count == 0
                assert host.state["sends"][-1]["outcome"] == "not_sent"
                assert not host.status()["supported"]
            else:
                await asyncio.wait_for(task, 3)
                assert socket.send.await_count == 1
                assert host.status()["supported"]
        else:
            async with steering_connection(fixture) as (parent, events, socket, host):
                task = asyncio.create_task(steer_from_worker(fixture))
                await change_route()
                if rejected:
                    with pytest.raises(LLMError):
                        await asyncio.wait_for(task, 3)
                    assert socket.send.await_count == 1
                    assert host.state["sends"][-1]["outcome"] == "not_sent"
                    assert not host.status()["supported"]
                    # Active native work cannot become an ordinary fallback.
                    with pytest.raises(LLMError):
                        await fixture.selected.complete(ChatRequest(
                            messages=[Message(role="user", content="No fallback")], metadata={"stream": False}))
                else:
                    assert (await asyncio.wait_for(task, 3))["accepted"]
                    assert socket.send.await_count == 2 and host.status()["supported"]
        assert not requests
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        native._client = None
        for client in clients:
            await client.close()


@pytest.mark.parametrize("source", ["environment", "injected"])
async def test_changed_effective_endpoint_cannot_replay_unknown_native_after_restart(native_host, monkeypatch, source):
    import httpx
    from openai import AsyncOpenAI
    from amplifier_web.native_provider import NativeProviderHost
    fixture = native_host
    async with steering_connection(fixture) as (task, events, socket, host):
        await steer_from_worker(fixture)
        await events.put(json.dumps({"type": "response.steer.accepted", "steer": {"id": "steer-1"}}))
        await fixture.loop.runtime.wait_for(lambda e: e["type"] == "steering.accepted", 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert host.state["sends"][-1]["outcome"] == "unknown"
        requests = []
        def deny_transport(request):
            requests.append(request)
            raise AssertionError("Unknown native work must not fall back to HTTP")
        client = AsyncOpenAI(api_key="fixture", base_url="https://proxy.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(deny_transport)))
        try:
            fixture.native._client = client
            assert not host.status()["supported"]
            with pytest.raises(LLMError):
                await fixture.selected.complete(ChatRequest(
                    messages=[Message(role="user", content="Do not replay")], metadata={"stream": False}))
            c = fixture.coordinator
            # Restore through a fresh real Core owner and the actual installer.
            fresh = AmplifierSession({
                "session": {"orchestrator": {"module": "loop-live", "config": {"native_provider": True}},
                            "context": {"module": "context-simple"}}, "providers": [],
            }, session_id=c.session_id)
            await fresh.initialize()
            try:
                restored_c = fresh.coordinator
                loop = restored_c.get("orchestrator")
                if source == "environment":
                    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.invalid/v1")
                    original = OpenAIProvider(api_key="fixture", config={"default_model": "gpt-6-astra"})
                else:
                    original = OpenAIProvider(api_key="fixture", client=client, config={
                        "default_model": "gpt-6-astra", "base_url": "https://api.openai.com/v1"})
                await restored_c.mount("providers", original, name="configured")
                loop.root_provider = SelectedProvider(original, copy.deepcopy(fixture.selected.selection))
                await install_native(loop, restored_c, restored_c.get("providers"))
                restored = restored_c.get_capability("web.native_provider")
                assert isinstance(restored, NativeProviderHost) and not restored.status()["supported"]
                assert restored.provider is not None and restored.provider.request_uncertain
                with pytest.raises(LLMError):
                    await loop.root_provider.complete(ChatRequest(
                        messages=[Message(role="user", content="No restart replay")], metadata={"stream": False}))
                assert restored.state["sends"][-1]["outcome"] == "unknown"
                assert restored.provider._client is (None if source == "environment" else client)
                assert not requests and socket.send.await_count == 2
            finally:
                await fresh.cleanup()
        finally:
            fixture.native._client = None
            await client.close()
