"""Public execution metadata, correlated without retaining model payloads."""
from __future__ import annotations

import asyncio
import contextvars
from functools import wraps
import math
import inspect
import json
import time
import uuid
import weakref
import os
from dataclasses import dataclass
from .token_usage import with_gross_tokens

CALL_PURPOSE = contextvars.ContextVar('amplifier_web_call_purpose',default=None)
CURRENT_CALL = contextvars.ContextVar("amplifier_web_public_call", default=None)
CURRENT_PROVIDER = contextvars.ContextVar("amplifier_web_public_provider", default=None)
ADMITTED_CALL = contextvars.ContextVar("amplifier_web_admitted_provider_call", default=None)


@dataclass(frozen=True)
class AdmittedProviderCall:
    id: str
    root_id: str
    session_id: str
    guarded: bool

class _ImageOperationResult(Exception):
    def __init__(self, result):
        super().__init__("Image operation did not complete.")
        self.result = result


def public_usage(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {"costType": "unavailable"}
    if "normalized" in value and "provider" in value:
        # Optional image operations separate normalized evidence from native
        # usage. Do not infer costs from another modality's model price.
        value = value.get("normalized") or {}
    result = {}
    for source, target in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                           ("cache_read_tokens", "cacheReadTokens"), ("cache_write_tokens", "cacheWriteTokens"),
                           ("total_tokens", "totalTokens"), ("reasoning_tokens", "reasoningTokens")):
        number = value.get(source)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) and number >= 0:
            result[target] = int(number)
    if "totalTokens" not in result and "inputTokens" in result and "outputTokens" in result:
        result["totalTokens"] = result["inputTokens"] + result["outputTokens"]
    try:
        if isinstance(value["cost_usd"], bool):
            raise ValueError()
        cost = float(value["cost_usd"])
        if not math.isfinite(cost) or cost < 0:
            raise ValueError()
        result.update(costUsd=cost, costType="estimated" if value.get("cost_type") == "estimated" else "reported")
        if value.get("cost_source"):
            result["costSource"] = str(value["cost_source"])[:300]
    except (KeyError, TypeError, ValueError):
        result["costType"] = "unavailable"
    return with_gross_tokens(result)


class ExecutionEvents:
    def __init__(self, root_id, emit):
        self.root_id, self.emit = root_id, emit
        self.producer_id = str(uuid.uuid4())
        self.turn_id = None
        self.admission_inputs = {}
        self.calls = {}
        self.children = {}
        self.last_foreground_provider = {}
        self.requests = weakref.WeakKeyDictionary()
        self.nodes = {}
        self.admission_guard = None
        self.provider_wrappers = {}
        self.streaming_calls = set()
        # Optional native projection; never used to assemble or recover context.
        self.context_inspectors = {}
        # Optional status-only observation of live accepted plans, not a plan
        # store. Never read logs or retain tool text/arguments for discovery.
        self.status_plan_inputs = {} if os.environ.get('AMPLIFIER_STATUS_DIR') else None

    def publish(self, row):
        # Observe both wrapped and hook-only provider paths. Optional naming or
        # compaction calls retain their own usage but do not relabel the worker.
        if row.get("kind") == "llm" and row.get("provider") and not CALL_PURPOSE.get() and row.get("lifecycle") != "background":
            sid = row.get("sessionId")
            self.last_foreground_provider[sid] = row["provider"]
            child = self.children.get(sid)
            if child:
                observed = {**child, "provider": row["provider"], "model": row.get("model")}
                if observed != child:
                    self.children[sid] = observed
                    self.publish(observed)
        row = {**row, "revision": self.nodes.get(row["id"], {}).get("revision", 0) + 1}
        row = {**row, "liveObservation": True}
        self.nodes[row["id"]] = row
        self.emit({"type": "execution.event", "event": dict(row)})

    def lifecycle(self, event):
        kind = event.get("type")
        sid = event.get('sessionId') or event.get('session_id') or self.root_id
        if sid == self.root_id and kind == 'generation.started':
            self.admission_inputs[sid] = event.get('initial_input_id')
        elif sid == self.root_id and kind == 'input.delivered' and not event.get('target_generation_id'):
            self.admission_inputs[sid] = event.get('input_id')
        if (sid == self.root_id and kind == "input.delivered" and event.get("source", "user") == "user"
                and not event.get('target_generation_id')):
            self.turn_id = event.get("input_id") or self.turn_id
        elif kind == "child.updated":
            identity = event.get("sessionId")
            if not identity:
                return
            call_id = event.get("callId")
            parent_sid = event.get("parentSessionId") or self.root_id
            parent = self.calls.get((parent_sid, call_id), {})
            previous = self.children.get(identity, {})
            if event.get("runId") and event["runId"] != previous.get("runId"):
                previous = {}
            # A returning root observation owns a new admission input while its
            # display ancestry stays on the original user turn. Existing child
            # runs retain their spawning input; a later root cannot renew them.
            self.admission_inputs[identity] = (self.admission_inputs.get(identity)
                if previous else parent.get('admissionInputId', self.admission_input(parent_sid)))
            from .delegate_exchange import worker_id
            row = {"id": worker_id(identity, call_id), "parentId": parent.get("id") or previous.get("parentId"),
                   "turnId": previous.get("turnId") or parent.get("turnId") or self.turn_id,
                   "sessionId": identity, "rootSessionId": self.root_id, "parentSessionId": parent_sid, "kind": "worker",
                   "phase": event.get("status", "running"), "label": event.get("agent") or "Worker",
                   "toolCallId": call_id, "startedAt": previous.get("startedAt", time.time())}
            if row["phase"] in {"completed", "cancelled", "error", "interrupted"}:
                row["endedAt"] = previous.get("endedAt") or time.time()
            else:
                row["endedAt"] = None
            if isinstance(event.get("report"),str) and event["report"]:
                row["summary"]=event["report"][:12000]
            elif previous.get("summary"):row["summary"]=previous["summary"]
            same_run = not event.get("runId") or event.get("runId") == previous.get("runId")
            if not same_run:
                self.last_foreground_provider.pop(identity, None)
            if not same_run or not previous:
                # Snapshot the spawning parent's observation. Later parent
                # model changes must not rewrite the cause of existing work.
                if self.last_foreground_provider.get(parent_sid):
                    row["parentProvider"] = self.last_foreground_provider[parent_sid]
            for key in ("provider", "model", "parentProvider"):
                if same_run and key in previous:
                    row[key] = previous[key]
            for key in ("routing", "runId"):
                if key in event:
                    if key == "routing":
                        from .host.model_selection import public_routing
                        row[key] = public_routing(event[key])
                    else:
                        row[key] = event[key]
                elif same_run and key in previous:
                    row[key] = previous[key]
            self.children[identity] = row
            self.publish(row)

    def parent(self, sid):
        child = self.children.get(sid)
        return (child["id"], child.get("turnId")) if child else (None, self.turn_id)

    def admission_input(self, sid):
        return self.admission_inputs.get(sid, self.admission_inputs.get(self.root_id, self.turn_id))

    async def _begin_provider_call(self, sid, provider, request, *, label=None, run_id=None, request_options=None):
        parent,turn = self.parent(sid)
        admission_input = self.admission_input(sid)
        purpose=CALL_PURPOSE.get()
        if purpose:parent,turn=None,purpose.get('turnId',turn)
        info = provider.get_info()
        if inspect.isawaitable(info):
            info = await info
        defaults = (info.get("defaults", {}) if isinstance(info, dict) else getattr(info,"defaults",{})) or {}
        provider_id = info.get("id") if isinstance(info, dict) else getattr(info, "id", type(provider).__name__)
        row = {"id":"llm:"+str(uuid.uuid4()),"parentId":parent,"turnId":turn,"sessionId":sid,
               "admissionInputId":admission_input,
               "rootSessionId":self.root_id,"producerId":self.producer_id,"kind":"llm","phase":"running","label":label or (purpose["label"] if purpose else "Model call"),
               "provider":str(provider_id)[:160],
               "model":str(getattr(request,"model",None) or defaults.get("model") or defaults.get("default_model") or "")[:160],
               "purpose": purpose.get("purpose") if purpose else None,
               "startedAt":time.time(),"lifecycle":"background" if purpose and purpose.get("lifecycle")=="background" else "turn"}
        if run_id is not None:
            row['runId'] = run_id  # Captured by this provider's native coordinator, not the current registry.
        if self.admission_guard:
            await self.admission_guard(row)
        # The outer fitted-request boundary retains its own attempt object.
        # Join it to this exact admitted call, not the mutable public trace ID.
        from .coordination_delivery import WORK_ATTEMPT
        active = WORK_ATTEMPT.get()
        if active and not purpose:
            delivery, attempt = active
            delivery.admit_work(attempt, row, guarded=self.admission_guard is not None)
        from .context_inspection_adapter import safe_observe
        await safe_observe(self.context_inspectors.get(sid), 'begin', row, request, provider, request_options, defaults)
        return row

    async def provider_call(self, sid, provider, request, invoke, *, label=None, run_id=None, request_options=None):
        """Observe one host-owned provider action through the same admission gate.

        Optional provider actions such as explicit compaction do not implement
        complete(), but still consume model capacity. Retain only their usage
        and call identity, exactly like ordinary completion calls.
        """
        metadata = getattr(request, "metadata", None) or {}
        purpose_token = None
        if isinstance(metadata, dict) and metadata.get("purpose") == "context-compaction":
            # Auxiliary output must never appear as a conversational response.
            # Keep its measured usage in the owning turn with a distinct label.
            purpose_token = CALL_PURPOSE.set({**(CALL_PURPOSE.get() or {}), "label": "Context compaction", "purpose": "context_compaction"})
            label = "Context compaction"
        try:
            return await self._observed_provider_call(sid, provider, request, invoke, label=label, run_id=run_id, request_options=request_options)
        finally:
            if purpose_token is not None:
                CALL_PURPOSE.reset(purpose_token)

    async def image_backend_call(self, sid, backend, arguments, *, run_id=None):
        """Keep retained explicit backend effects inside host admission too."""
        from types import SimpleNamespace
        descriptor = backend.describe()
        provider = SimpleNamespace(get_info=lambda: {
            "id": descriptor.get("provider", "legacy-image"),
            "defaults": {"model": descriptor.get("model")},
        })
        request = SimpleNamespace(model=descriptor.get("model"), metadata={})
        purpose = CALL_PURPOSE.set({**(CALL_PURPOSE.get() or {}),
                                   "label": "Image generation", "purpose": "image_generation"})
        try:
            return await self.provider_call(sid, provider, request,
                lambda: backend.generate(**arguments), label="Image generation", run_id=run_id)
        finally:
            CALL_PURPOSE.reset(purpose)

    async def _observed_provider_call(self, sid, provider, request, invoke, *, label=None, run_id=None, request_options=None):
        row = await self._begin_provider_call(sid, provider, request, label=label, run_id=run_id, request_options=request_options)
        from .context_inspection_adapter import safe_observe
        token = CURRENT_CALL.set(row["id"])
        owner = CURRENT_PROVIDER.set(id(provider))
        admitted = ADMITTED_CALL.set(AdmittedProviderCall(row['id'], self.root_id, sid,
                                                        self.admission_guard is not None))
        self.publish(row)
        try:
            response = await invoke()
            usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
            terminal = {**row, "phase": "completed", "endedAt": time.time(), "usage": public_usage(usage)}
            await safe_observe(self.context_inspectors.get(sid), 'finish', terminal)
            self.publish(terminal)
            return response
        except BaseException as exc:
            from .session_health import exception_details
            failure = {} if isinstance(exc, asyncio.CancelledError) else {'failure': exception_details(exc)}
            if isinstance(exc, _ImageOperationResult):
                # A received unsuccessful artifact can still report paid usage.
                # Retain only normalized public evidence, never its payload.
                failure['usage'] = public_usage(exc.result.get("usage"))
            terminal = {**row, "phase": "cancelled" if isinstance(exc, asyncio.CancelledError) else "error", "endedAt": time.time(), **failure}
            await safe_observe(self.context_inspectors.get(sid), 'finish', terminal)
            self.publish(terminal)
            raise
        finally:
            CURRENT_CALL.reset(token)
            CURRENT_PROVIDER.reset(owner)
            ADMITTED_CALL.reset(admitted)

    def instrument_provider(self, sid, provider, *, run_id=None):
        """Observe the public complete boundary; Core hook callbacks may use new tasks.

        Keeping request identity here avoids pairing concurrent responses by
        arrival order. SDK hook events still supply retry status through the
        task-local call identity inherited by their dispatch tasks.
        """
        if id(provider) in self.provider_wrappers:
            return self.provider_wrappers[id(provider)]
        if getattr(provider,"_amplifier_web_observed",False):
            return provider
        original = getattr(provider,"complete",None)
        if not callable(original):
            return provider
        @wraps(original)
        async def complete(request, **kwargs):
            if CURRENT_PROVIDER.get() == id(provider):
                return await original(request, **kwargs)
            return await self.provider_call(sid, provider, request, lambda: original(request, **kwargs), run_id=run_id, request_options=kwargs)

        original_budget = getattr(provider, 'request_budget', None)
        @wraps(original_budget)
        async def request_budget(request, **kwargs):
            decision = original_budget(request, **kwargs)
            decision = await decision if inspect.isawaitable(decision) else decision
            from .context_inspection_adapter import safe_observe
            await safe_observe(self.context_inspectors.get(sid), 'budget', request, kwargs, decision, provider)
            return decision

        original_compact = getattr(provider, "compact_context", None)
        async def compact_context(request, **kwargs):
            return await self.provider_call(sid, provider, request,
                lambda: original_compact(request, **kwargs), label="Context compaction", run_id=run_id)

        original_image = getattr(provider, "generate_image", None)
        async def generate_image(request, *, plan):
            from types import SimpleNamespace
            # Use the image model, not the conversation default, for admission
            # identity. No prompt, image bytes or opaque continuation is logged.
            observed = SimpleNamespace(model=plan.get("model"), metadata={})
            purpose = CALL_PURPOSE.set({**(CALL_PURPOSE.get() or {}),
                                       "label": "Image generation", "purpose": "image_generation"})
            try:
                async def invoke():
                    result = await original_image(request, plan=plan)
                    if result.get("status") != "completed":
                        # The method returned evidence but did not complete the
                        # requested artifact; the event must not paint success.
                        raise _ImageOperationResult(result)
                    return result
                try:
                    return await self.provider_call(sid, provider, observed, invoke,
                        label="Image generation", run_id=run_id)
                except _ImageOperationResult as exc:
                    return exc.result
            finally:
                CALL_PURPOSE.reset(purpose)

        original_stream = getattr(provider, "stream", None)
        async def stream(request, **kwargs):
            if CURRENT_PROVIDER.get() == id(provider):
                iterator = original_stream(request, **kwargs)
                try:
                    async for chunk in iterator:
                        yield chunk
                finally:
                    if callable(getattr(iterator, "aclose", None)):
                        await iterator.aclose()
                return
            row = await self._begin_provider_call(sid, provider, request, run_id=run_id, request_options=kwargs)
            self.streaming_calls.add(row["id"])
            self.publish(row)
            iterator = None
            phase = "completed"
            admission = AdmittedProviderCall(row['id'], self.root_id, sid, self.admission_guard is not None)
            async def scoped(awaitable):
                # Do not leak context across a yield or retain a token that
                # another task (timeout/close) would have to reset.
                token = CURRENT_CALL.set(row["id"])
                owner = CURRENT_PROVIDER.set(id(provider))
                admitted = ADMITTED_CALL.set(admission)
                try:
                    return await awaitable
                finally:
                    CURRENT_CALL.reset(token)
                    CURRENT_PROVIDER.reset(owner)
                    ADMITTED_CALL.reset(admitted)
            try:
                iterator = original_stream(request, **kwargs).__aiter__()
                while True:
                    try:
                        chunk = await scoped(anext(iterator))
                    except StopAsyncIteration:
                        break
                    # Stream usage is a whole-call cumulative snapshot, not a
                    # token delta. A later snapshot replaces the receipt.
                    usage = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
                    if usage is not None:
                        self.publish({**self.nodes[row["id"]], "usage": public_usage(usage)})
                    yield chunk
            except GeneratorExit:
                phase = "interrupted"
                raise
            except BaseException as exc:
                phase = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                raise
            finally:
                try:
                    if callable(getattr(iterator, "aclose", None)):
                        await scoped(iterator.aclose())
                except BaseException:
                    phase = "error"
                    raise
                finally:
                    terminal = {**self.nodes[row["id"]], "phase": phase, "endedAt": time.time()}
                    from .context_inspection_adapter import safe_observe
                    await safe_observe(self.context_inspectors.get(sid), 'finish', terminal)
                    self.publish(terminal)
                    self.streaming_calls.discard(row["id"])

        # Providers are plugin instances implementing the public complete
        # protocol. No SDK internals or request payloads are inspected.
        try:
            provider.complete = complete
            if callable(original_budget):
                provider.request_budget = request_budget
            if callable(original_compact):
                provider.compact_context = compact_context
            if callable(original_image):
                provider.generate_image = generate_image
            if callable(original_stream):
                provider.stream = stream
            provider._amplifier_web_observed = True
        except (AttributeError,TypeError):
            # Immutable provider instances still get the same authoritative
            # complete boundary via the host's ordinary provider registry.
            class ObservedProvider:
                _amplifier_web_observed = True
                def __getattr__(self, name): return getattr(provider, name)
            wrapper = ObservedProvider()
            wrapper.original = provider
            wrapper.complete = complete
            if callable(original_budget):
                wrapper.request_budget = request_budget
            if callable(original_compact):
                wrapper.compact_context = compact_context
            if callable(original_image):
                wrapper.generate_image = generate_image
            if callable(original_stream):
                wrapper.stream = stream
            self.provider_wrappers[id(provider)] = wrapper
            return wrapper
        return provider

    def hook(self, sid, event, data):
        if data.get("session_id", sid) != sid:
            return
        parent, turn = self.parent(sid)
        now = time.time()
        if event.startswith("tool:"):
            call = data.get("tool_call_id")
            if not call:
                return
            key = (sid, call)
            row = self.calls.get(key) or {"id": f"tool:{sid}:{call}", "parentId": parent, "turnId": turn,
                "admissionInputId": self.admission_input(sid),
                "sessionId": sid, "rootSessionId": self.root_id, "kind": "tool", "toolCallId": call,
                "label": str(data.get("tool_name") or "Tool")[:120], "startedAt": now}
            row = dict(row)
            # Bind this host update to ONE canonical event, not an inferred
            # attempt. Hook callbacks are separate tasks and repeated call IDs
            # cannot prove which earlier pre belongs to a late terminal.
            token = str(uuid.uuid4())
            data = {**data, 'unified_tool_observation_id': token}
            from .tool_observations import input_hints, metadata, outcome_summary
            input_key = next((key for key in ('tool_input', 'arguments', 'input', 'prompt') if key in data), None)
            row['tool'] = data.get('tool_name') or row.get('tool') or row.get('label')
            row.update(toolObservationId=token, **input_hints(row['tool'], data.get(input_key),
                       present=input_key is not None), outcomeSummary=outcome_summary(event, data))
            row.update(metadata(row))
            row["phase"] = {"tool:pre": "running", "tool:post": "completed", "tool:error": "error"}[event]
            # The observer supplies bounded inspection hints. Full tool
            # inputs/results are read from their native event-log records.
            row["liveObservation"] = True
            if event == "tool:pre":
                row["endedAt"] = None
            else:
                if row.get("endedAt") is None: row["endedAt"] = now
                result = data.get("result") if "result" in data else data.get("tool_result", {})
                if hasattr(result, "model_dump"): result = result.model_dump()
                if event == "tool:error" or isinstance(result, dict) and result.get("success") is False:
                    row["phase"] = "error"

            from .image_generation import observe
            observe(row, event, data)
            if self.status_plan_inputs is not None and row['label'] == 'todo':
                from .status_protocol.hook import accepted_plan
                if event == 'tool:pre':
                    args = data.get('tool_input') or {}
                    if isinstance(args, dict) and args.get('action') in {'create', 'update'} and len(self.status_plan_inputs) < 32:
                        self.status_plan_inputs[key] = accepted_plan({'todos': args.get('todos')})
                else:
                    proposed = self.status_plan_inputs.pop(key, None)
                    row['statusPlan'] = (accepted_plan(data.get('result', data.get('tool_result')), proposed)
                                         if row['phase'] == 'completed' else None)
            self.calls[key] = row
            self.publish(row)
            if event == 'tool:post' and row['label'] == 'image_generate':
                from .image_results import completed_receipt
                receipt = completed_receipt(data.get('result', data.get('tool_result')))
                if receipt and row.get('imageGeneration', {}).get('requestId') == receipt['requestId']:
                    # Private worker-to-host delivery; receipt paths never enter
                    # the compact public execution projection.
                    self.emit({'type': 'image.generated', 'nodeId': row['id'],
                               'receipt': receipt})
            return data
        elif event in {"llm:request", "llm:response", "provider:retry"}:
            current = CURRENT_CALL.get()
            if current:
                row = self.nodes.get(current)
                if row and event == "provider:retry":
                    self.publish({**row,"phase":"retrying"})
                elif row and current in self.streaming_calls and event == "llm:response" and data.get("usage") is not None:
                    self.publish({**row, "usage": public_usage(data["usage"])})
                return
            purpose = CALL_PURPOSE.get() or {}
            if purpose: parent, turn = None, purpose.get("turnId", turn)
            scope = {"purpose": purpose.get("purpose"), "label": purpose.get("label", "Model call"), "lifecycle": "background" if purpose.get("lifecycle")=="background" else "turn"}
            task = asyncio.current_task()
            if task is None:
                return
            row = self.requests.get(task)
            if event == "llm:request":
                row = {"id": "llm:" + str(uuid.uuid4()), "parentId": parent, "turnId": turn,
                    "sessionId": sid, "rootSessionId": self.root_id, "kind": "llm", "phase": "running",
                    **scope, "startedAt": now,
                    **{key:str(data[key])[:160] for key in ("provider", "model") if data.get(key)}}
                self.requests[task] = row
            elif row is None:
                # Providers may not implement request hooks. A response is still
                # useful, but never attach it to an unrelated concurrent call.
                if event != "llm:response":
                    return
                row = {"id": "llm:" + str(uuid.uuid4()), "parentId": parent, "turnId": turn,
                    "sessionId": sid, "rootSessionId": self.root_id, "kind": "llm", **scope}
            row = dict(row)
            if event == "llm:response":
                row.update(phase="error" if data.get("status") == "error" else "completed", endedAt=now,
                    usage=public_usage(data.get("usage")),
                    **{key:str(data[key])[:160] for key in ("provider", "model") if data.get(key)})
                self.requests.pop(task, None)
            elif event == "provider:retry":
                row["phase"] = "retrying"
            self.publish(row)

    def usage(self):
        calls = [row for row in self.nodes.values() if row["kind"] == "llm"]
        usages = [with_gross_tokens(row.get("usage", {})) for row in calls]
        totals = {key:sum(usage.get(key, 0) for usage in usages)
                  for key in ("inputTokens", "outputTokens", "totalTokens", "cacheReadTokens", "cacheWriteTokens", "grossInputTokens", "grossTotalTokens", "reasoningTokens")}
        priced = [row for row in calls if "costUsd" in row.get("usage", {})]
        totals.update(costUsd=sum(row["usage"]["costUsd"] for row in priced) if priced else None,
                      costType="reported" if priced and len(priced) == len(calls) else "partial" if priced else "unavailable")
        # Usage inspection remains a metadata trace; full tool fields are read
        # independently through conversation detail, never duplicated in totals.
        trace=[{key:value for key,value in row.items() if key not in {"input","output","error"}} for row in list(self.nodes.values())[-500:]]
        return {"calls": len(calls), "usage": totals, "trace": trace}
