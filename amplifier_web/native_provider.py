"""Opt-in binding to the provider-owned native transport; no wire protocol here."""
import copy
import json
import inspect
import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlparse

from .host.config import app_home, write_private
from .session_files import validate_id

_STEER_INPUT = ContextVar('unified_native_steer_input', default=None)
_STEER_GENERATION = ContextVar('unified_native_steer_generation', default=None)


def _official_endpoint(provider):
    """Inspect the SDK's effective route without triggering lazy client creation."""
    try:
        client = getattr(provider, "_client", None)
        if client is not None:
            # An injected/initialized client is the actual dispatch route. An
            # absent URL is unknown, not permission to use config or defaults.
            value = getattr(client, "base_url", None)
        else:
            value = getattr(provider, "base_url", None)
            if value is None:
                value = os.environ.get("OPENAI_BASE_URL")
            if value is None:
                value = "https://api.openai.com/v1"
        from httpx import URL
        from openai._base_client import URL as SDKURL
        if isinstance(value, (URL, SDKURL)):
            value = str(value)
        elif not isinstance(value, str):
            return False  # Do not qualify arbitrary objects through __str__.
        endpoint = urlparse(value)
        return (endpoint.scheme == "https" and endpoint.netloc == "api.openai.com"
                and endpoint.path.rstrip('/') == '/v1'
                and not endpoint.query and not endpoint.fragment and not endpoint.params)
    except (ValueError, TypeError, AttributeError):
        return False


class NativeProviderHost:
    def __init__(self, coordinator, loop, directory):
        self.coordinator, self.loop = coordinator, loop
        self.directory = directory
        self.provider = None
        self.identity = None
        self.reason = "Native provider transport is not enabled for this bundle."
        self.send_bindings = {}
        self.response_origins = {}
        self.open_responses = set()
        self.response_overflow = False
        self.state = {"version": 1, "sessionId": coordinator.session_id, "receipts": []}
        self.path = directory / "native-provider-state.json"
        self.checkpoint_path = directory / "native-provider-checkpoint.json"
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            if saved.get("sessionId") == coordinator.session_id:
                self.state = saved
                changed = False
                for row in self.state.get("sends", []):
                    if row.get("outcome") in {"submitting", "sent", "running", "pending"}:
                        row["outcome"] = "unknown"
                        changed = True
                if self.state.get("outcome") in {"submitting", "sent", "running", "pending"}:
                    self.state["outcome"] = "unknown"
                    self.state["reason"] = "Host restarted without a terminal provider receipt; no work was replayed."
                    changed = True
                if changed:
                    self.save()
        coordinator.register_capability("web.native_provider", self)

    def save(self):
        write_private(self.path, json.dumps(self.state, ensure_ascii=False))

    def _remember_response(self, identity, owner):
        self.response_origins = {k: v for k, v in self.response_origins.items()
            if v[0].attempts.get(v[1]['id']) is v[1]}
        if owner and (identity in self.response_origins or len(self.response_origins) < 128):
            self.response_origins[identity] = owner
            owner[0].native_response(owner[1], identity)
        # Exhausted attribution memory must not destroy an unrelated response.
        # Absence is unavailable coverage, never a reused last-request proof.

    async def send_lifecycle(self, kind, data):
        """One stable sink; never replace a parent's callback with a steer's."""
        from .coordination_delivery import WORK_ATTEMPT
        from .execution_events import ADMITTED_CALL, AdmittedProviderCall
        active = WORK_ATTEMPT.get()
        admission = ADMITTED_CALL.get()
        if kind == 'native.submitting':
            if not isinstance(admission, AdmittedProviderCall) or admission.session_id != self.coordinator.session_id:
                raise ValueError("Native send lacks its host provider admission")
            delivery, attempt = active or (None, None)
            if attempt and attempt['origin'] is not None and not delivery.origin_current(attempt):
                raise ValueError("Native send origin changed")
            if (data.get('operation') == 'response.steer'
                    and data.get('input_id') != _STEER_INPUT.get()):
                raise ValueError("Native steer is not its admitted input")
            if len(self.send_bindings) >= 128:
                recorded = {row['send_id'] for row in self.state.get('sends', [])}
                terminal = {row['send_id'] for row in self.state.get('sends', [])
                            if row['outcome'] in {'not_sent', 'completed', 'applied', 'failed'}}
                self.send_bindings = {k: v for k, v in self.send_bindings.items()
                                      if k in recorded and k not in terminal}
            if len(self.send_bindings) >= 128 or data['send_id'] in self.send_bindings:
                raise ValueError("Native send binding is ambiguous or full")
            attempt_id = attempt['id'] if attempt else uuid.uuid4().hex
            generation = getattr(self.coordinator.get_capability('live.runtime'), 'generation', None) or {}
            if (data.get('operation') == 'response.steer' and _STEER_GENERATION.get() is not None
                    and generation.get('id') != _STEER_GENERATION.get()):
                raise ValueError("Native steering generation changed before submission")
            callback = self.bind_send_lifecycle(
                attempt_id=attempt_id, generation_id=generation.get('id'), admitted_call_id=admission.id)
            self.send_bindings[data['send_id']] = {
                'delivery': delivery, 'attempt': attempt, 'callback': callback, 'record': copy.deepcopy(data),
                'admission': admission, 'generationId': generation.get('id'), 'attemptId': attempt_id}
            if attempt:
                attempt['sendId'] = data['send_id']
            await callback(kind, data)
            return
        if kind == 'native.not_sent':
            binding = self.send_bindings.get(data.get('send_id'))
            if binding is None:
                raise ValueError("Native terminal has no original send binding")
            await binding['callback'](kind, data)
            if binding['attempt']:
                binding['attempt']['status'] = 'not_sent'
            return
        bindings = list(self.send_bindings.values())
        if kind.startswith('steering.'):
            bindings = [b for b in bindings if b['record'].get('operation') == 'response.steer'
                        and b['record'].get('input_id') == data.get('input_id')]
        elif kind == 'native.outcome_unknown':
            bindings = [b for b in bindings if
                (b['admission'] is admission and b['record'].get('operation') == 'response.create')
                or (b['record'].get('operation') == 'response.steer'
                    and data.get('input_id') is not None and b['record'].get('input_id') == data['input_id'])]
        else:
            bindings = [b for b in bindings if b['admission'] is admission
                        and b['record'].get('operation') == 'response.create']
        for binding in bindings:
            delivery, attempt = binding['delivery'], binding['attempt']
            await binding['callback'](kind, data)
            if kind == 'native.outcome_unknown' and attempt:
                delivery.finish_work(attempt, failed=True)
            if kind == 'steering.applied' and attempt:
                self._remember_response(data['response_id'], (delivery, attempt))
        if kind == 'native.response.created':
            identity = data.get('response_id')
            owner = self.response_origins.get(identity, active)
            self._remember_response(identity, owner)
            # This connection serializes response.created / terminal phases.
            # It is an event-ordered response identity, not a last model request.
            epoch = getattr(self.provider, 'epoch', None)
            if len(self.open_responses) < 128:
                self.open_responses.add((epoch, identity))
            else:
                self.response_overflow = True
        if kind == 'native.outcome_unknown':
            self.open_responses.clear()
            self.response_overflow = False

    async def prepare_send(self, kind, *, input_id=None):
        from amplifier_module_provider_openai.native import NativeSendAdmission
        from .execution_events import ADMITTED_CALL
        admission = ADMITTED_CALL.get()
        matches = [b for b in self.send_bindings.values() if b['admission'] is admission
                   and b['record']['operation'] == kind and b['record'].get('input_id') == input_id]
        if len(matches) != 1:
            raise ValueError("Native send has no unique captured admission")
        binding = matches[0]
        delivery, attempt = binding['delivery'], binding['attempt']
        if kind == 'response.steer' and input_id != _STEER_INPUT.get():
            raise ValueError("Native steer input changed")
        if attempt:
            await delivery.prepare_native(attempt)
        def admit(envelope):
            runtime = self.coordinator.get_capability('live.runtime')
            generation = getattr(runtime, 'generation', None) or {}
            cancellation = getattr(self.coordinator, 'cancellation', None)
            activation = self.coordinator.get_capability('live.activation')
            if (not self.binding_current() or generation.get('id') != binding['generationId']
                    or getattr(runtime, 'stop_requested', False) or getattr(runtime, 'closed', False)
                    or activation is not None and not activation.current_valid
                    or getattr(cancellation, 'is_cancelled', False)):
                return False
            if not attempt:
                return envelope.attempt_id == binding['attemptId'] and envelope.kind == kind
            return delivery.admit_native(attempt, envelope, send_id=binding['record']['send_id'])
        return NativeSendAdmission(attempt_id=binding['attemptId'], admit=admit)

    def work_trace(self, kind, value):
        if kind != 'native.receive':
            return  # Pre-send tracing is never promoted to actual-send proof.
        terminal = value.get('response', {})
        response_id = terminal.get('id') if isinstance(terminal, dict) and terminal else value.get('response_id')
        if value.get('type') == 'response.output_item.done' and response_id is None:
            frames = [identity for epoch, identity in self.open_responses
                      if epoch == getattr(self.provider, 'epoch', None)]
            response_id = frames[0] if len(frames) == 1 and not self.response_overflow else None
            value = {**value, 'response_id': response_id}
        owner = self.response_origins.get(response_id)
        if owner:
            owner[0].native_work_event(owner[1], kind, value)
        else:
            # Unattributed output still consumes its call IDs. It cannot reuse
            # an earlier response's identical name/args to obtain that proof.
            delivery = self.coordinator.get_capability('web.surface_delivery')
            if delivery is not None:
                items = terminal.get('output', []) if terminal else [value.get('item')]
                delivery.finish_work(None, {'content': [
                    {'type': 'tool_call', 'id': item.get('call_id')}
                    for item in items if isinstance(item, dict) and item.get('type') == 'function_call']})
        if value.get('type') in {'response.completed', 'response.incomplete', 'response.failed'}:
            self.open_responses.discard((getattr(self.provider, 'epoch', None), response_id))

    def bind_send_lifecycle(self, *, attempt_id, generation_id, admitted_call_id):
        """Capture an existing host admission before an opted-in transport call.

        This consumes receipts, not Work applicability or effect permission.
        The caller supplies its actual host identities, never model arguments.
        Legacy installation stays unchanged until its admission owner opts in.
        Route submitting/not_sent to their exact operation binding. Keep each
        live binding for later phases; non-owned phases are observation no-ops,
        not permission to replace a parent's sink with a steer's callback.
        """
        if any(not isinstance(value, str) or not value or len(value) > 200
               for value in (attempt_id, generation_id, admitted_call_id)):
            raise ValueError("Native send requires an exact host admission binding")
        binding = {"attemptId": attempt_id, "generationId": generation_id,
                   "admittedCallId": admitted_call_id, "identity": copy.deepcopy(self.identity)}

        async def observe(kind, data):
            previous = self.state
            state = copy.deepcopy(previous)
            rows = state.setdefault("sends", [])
            if kind in {"native.submitting", "native.not_sent"}:
                keys = ("send_id", "operation", "epoch", "response_id", "input_id")
                identity = {key: data.get(key) for key in keys}
                operation = identity["operation"]
                if (operation not in {"response.create", "response.steer"}
                        or any(not isinstance(identity[key], str) or not identity[key]
                               or len(identity[key]) > 200 for key in ("send_id", "epoch"))
                        or any(value is not None and (not isinstance(value, str) or not value
                                                     or len(value) > 200)
                               for value in (identity["response_id"], identity["input_id"]))
                        or (operation == "response.create" and identity["input_id"] is not None)
                        or (operation == "response.steer" and
                            (identity["input_id"] is None or identity["response_id"] is None))
                        or data.get("execution_replayed") is not False):
                    raise ValueError("Native send receipt identity is invalid")
                matches = [row for row in rows if row["send_id"] == identity["send_id"]]
                if kind == "native.submitting":
                    generation = getattr(self.coordinator.get_capability("live.runtime"), "generation", None) or {}
                    if (matches or data.get("attempt_id") is not None
                            or self.selected_identity() != binding["identity"]
                            or generation.get("id") != binding["generationId"]):
                        raise ValueError("Native send admission binding changed")
                    if any(all(row.get(key) == value for key, value in binding.items())
                           and row["operation"] == operation
                           and (operation == "response.create" or row["input_id"] == identity["input_id"])
                           for row in rows):
                        raise ValueError("Native send already exists for this admission binding")
                    # Never evict an unresolved send to make room for another.
                    if len(rows) >= 128:
                        terminal = next((row for row in rows if row["outcome"] in
                                         {"not_sent", "completed", "applied", "failed"}), None)
                        if terminal is None:
                            raise ValueError("Native send receipt capacity requires reconciliation")
                        rows.remove(terminal)
                    row = {**identity, **copy.deepcopy(binding), "outcome": "submitting"}
                    rows.append(row)
                    if operation == "response.create":
                        state.update(requestId=attempt_id, generationId=generation_id,
                                     admittedCallId=admitted_call_id,
                                     identity=copy.deepcopy(binding["identity"]), outcome="submitting")
                else:
                    if (len(matches) != 1 or any(matches[0].get(key) != value for key, value in identity.items())
                            or any(matches[0].get(key) != value for key, value in binding.items())
                            or matches[0]["outcome"] != "submitting"
                            or data.get("attempt_id") not in (None, attempt_id)
                            or data.get("reason") not in ("cancelled", "rejected")):
                        raise ValueError("Native terminal does not match an unresolved unsent operation")
                    row = matches[0]
                    row.update(outcome="not_sent", reason=data["reason"])
                    if (operation == "response.create" and state.get("requestId") == attempt_id
                            and state.get("admittedCallId") == admitted_call_id):
                        state.update(outcome="not_sent", reason=data["reason"])
                row["updatedAt"] = time.time()
            else:
                owned = [row for row in rows if all(row.get(key) == value for key, value in binding.items())
                         and row["outcome"] in {"submitting", "sent", "running", "pending"}]
                if kind.startswith("steering."):
                    owned = [row for row in owned if row["operation"] == "response.steer"
                             and row["input_id"] == data.get("input_id")]
                elif kind in {"native.request", "native.response.created", "native.completed"}:
                    owned = [row for row in owned if row["operation"] == "response.create"]
                elif kind == "native.outcome_unknown":
                    # A parent may fail while a steer is still awaiting prep.
                    # Only the provider's identified in-flight input can turn a
                    # steer unknown; preserve the other steer for its not_sent.
                    owned = [row for row in owned if row["operation"] == "response.create"
                             or row["input_id"] == data.get("input_id")]
                phases = {"native.request": "sent", "steering.sent": "sent",
                          "native.response.created": "running", "native.completed": "completed",
                          "native.outcome_unknown": "unknown", "steering.accepted": "pending",
                          "steering.pending": "pending",
                          "steering.applied": "applied", "steering.failed": "failed"}
                if kind not in phases:
                    return  # Other transport observations do not identify a send outcome.
                if kind in phases:
                    if not owned:
                        # One provider emits interleaved create/steer phases.
                        # A receipt consumer must not destroy another operation's
                        # terminal response. Its original owner still observes it.
                        return
                    if kind != "native.outcome_unknown" and len(owned) != 1:
                        raise ValueError("Native phase lacks an exact unresolved send")
                    response = data.get("response_id")
                    if kind in {"native.response.created", "native.completed", "steering.sent",
                                "steering.accepted", "steering.applied"}:
                        if not isinstance(response, str) or not response or len(response) > 200:
                            raise ValueError("Native phase lacks an exact response identity")
                    if kind == "native.completed":
                        if (response != owned[0].get("currentResponseId")
                                or type(data.get("pending_steering", False)) is not bool):
                            raise ValueError("Native completion does not match its observed response")
                    if kind in {"steering.sent", "steering.accepted", "steering.pending"}:
                        if response != owned[0]["response_id"]:
                            raise ValueError("Native steering parent changed")
                    if kind == "steering.pending":
                        if owned[0]["outcome"] != "pending" or not owned[0].get("steerId"):
                            raise ValueError("Native pending input precedes acceptance")
                        owned[0]["awaitingRequiredInput"] = True
                    if kind == "steering.applied":
                        if (owned[0]["outcome"] != "pending" or response == owned[0]["response_id"]
                                or not data.get("steer_id") or data.get("steer_id") != owned[0].get("steerId")):
                            raise ValueError("Native steering successor is not its accepted input")
                        owned[0]["awaitingRequiredInput"] = False
                    if kind == "steering.accepted":
                        steer_id = data.get("steer_id")
                        if (owned[0]["outcome"] != "sent" or not isinstance(steer_id, str)
                                or not steer_id or len(steer_id) > 200):
                            raise ValueError("Native steering acceptance is not bound")
                        owned[0]["steerId"] = steer_id
                    if kind == "steering.failed" and owned[0]["outcome"] not in {"sent", "pending"}:
                        raise ValueError("Native steering failure precedes send")
                    for row in owned:
                        if kind == "native.response.created":
                            row["currentResponseId"] = response
                        row["outcome"] = phases[kind]
                        if kind == "native.outcome_unknown":
                            reason = data.get("reason")
                            # Upstream also supplies exception class names. Do
                            # not publish arbitrary callback or exception text.
                            row["reason"] = reason if reason in (
                                "steer_send", "cancelled", "connection_closed",
                                "pending_connection_closed") else "transport_unknown"
                        row["updatedAt"] = time.time()
                    if (kind.startswith("native.") and state.get("requestId") == attempt_id
                            and state.get("admittedCallId") == admitted_call_id and owned):
                        # The create operation is terminal even when a distinct
                        # steer still needs a successor. Keep that uncertainty in
                        # the logical summary and the steer's own receipt only.
                        state["outcome"] = ("pending" if kind == "native.completed"
                                            and data.get("pending_steering") else owned[0]["outcome"])
                        if kind == "native.outcome_unknown":
                            state["reason"] = owned[0]["reason"]
                        if kind in {"native.response.created", "native.completed"}:
                            state["responseId"] = data.get("response_id")
            # Only bounded identity/phase metadata; never payload or arbitrary
            # observer error text. A failed write cannot appear durable in RAM.
            receipt = {"id": str(uuid.uuid4()), "event": kind, "requestId": attempt_id,
                       "time": time.time(), **{key: data.get(key) for key in
                       ("send_id", "operation", "epoch", "input_id", "response_id", "steer_id")
                       if isinstance(data.get(key), str) and len(data[key]) <= 200}}
            state["receipts"] = (state.get("receipts", []) + [receipt])[-128:]
            state["updatedAt"] = time.time()
            self.state = state
            try:
                self.save()
            except BaseException:
                self.state = previous
                raise

        return observe

    def selected_mount(self):
        providers = self.coordinator.get("providers") or {}
        selected = self.loop._select_provider(providers)
        current, visited, link = selected, set(), None
        while current is not None and id(current) not in visited:
            for name, mounted in providers.items():
                if current is mounted:
                    return selected, mounted, name, link
            visited.add(id(current))
            link, current = current, getattr(current, "original", None)
        return selected, None, None, None

    def selected_identity(self):
        selected, original, instance, _ = self.selected_mount()
        try:
            info = selected.get_info() if selected else None
        except (ValueError, TypeError, AttributeError):
            info = None
        if inspect.isawaitable(info):
            if inspect.iscoroutine(info):
                info.close()
            info = None
        defaults = (info.get("defaults", {}) if isinstance(info, dict) else getattr(info, "defaults", {})) or {}
        model = defaults.get("model")
        if not isinstance(model, str) or not model:
            model = None
        elif callable(getattr(original, "_assemble_initial_responses_params", None)):
            # Status has no ChatRequest yet. Use the provider's pure planner for
            # the configured selection, including its final SDK JSON overlay.
            # Dispatch still qualifies its own actual final request below.
            try:
                from amplifier_core.message_models import ChatRequest
                from amplifier_module_provider_openai import OpenAIProvider
                from amplifier_module_provider_openai.native import NativeResponsesProvider
                if isinstance(original, OpenAIProvider):
                    params, _, _ = OpenAIProvider._assemble_initial_responses_params(
                        original, ChatRequest(messages=[], model=model), model=model)
                    model = NativeResponsesProvider._native_wire_params(params).get("model")
            except (ImportError, ValueError, TypeError, AttributeError):
                model = None  # Malformed/unavailable planning is not eligibility.
        if not isinstance(model, str) or not model:
            model = None
        return {"instance": instance, "model": model,
                "selection": copy.deepcopy(getattr(selected, "selection", {}))}

    def binding_current(self):
        return (getattr(self.loop, "config", {}).get("native_provider") is True
                and self.selected_mount()[1] is self.provider
                and _official_endpoint(self.provider)
                and self.selected_identity() == self.identity)

    def active_response(self):
        """Actual steering readiness, not configured/model eligibility."""
        runtime = self.coordinator.get_capability("live.runtime")
        provider = self.provider
        if (provider is None or not self.binding_current()
                or getattr(provider, "owner", None) is not self.loop
                or not getattr(provider, "response_id", None)
                or getattr(provider, "inflight", None)
                or getattr(provider, "awaiting_successor", None)
                or getattr(provider, "_steer_preparing", False)
                or not getattr(runtime, "generation", None)
                or getattr(runtime, "closed", False) or getattr(runtime, "stop_requested", False)
                or getattr(runtime, "steering_closed", False)):
            return None
        return {"responseId": provider.response_id, "generationId": runtime.generation["id"]}

    async def submit_steering(self, command, generation_id):
        """Generation-bound UI/peer input, never an ordinary inbox fallback.

        loop-live deliberately does not offer its request-boundary capability
        for a native mount. This host bridge uses the existing provider send
        admission instead, without weakening the loop's boundary-only contract.
        """
        runtime = self.coordinator.get_capability("live.runtime")
        if (not isinstance(generation_id, str) or not 1 <= len(generation_id) <= 128
                or command.kind != "steer" or command.source != "user"
                or not isinstance(command.id, str) or not 1 <= len(command.id) <= 128
                or not command.text.strip() or len(command.text) > runtime.max_input_chars
                or command.target_generation_id not in (None, generation_id)):
            raise ValueError("An exact generation and valid steering input are required")
        command = replace(command, target_generation_id=generation_id)
        previous = runtime.accepted.get(command.id)
        if previous:
            if previous != command:
                raise ValueError("Command identity reused with different content")
            return copy.deepcopy(runtime.steering_outcomes[command.id])
        if (not self.provider or not self.binding_current() or runtime.closed or runtime.stop_requested
                or runtime.steering_closed or (runtime.generation or {}).get("id") != generation_id
                or command.attachments):
            return {"accepted": False, "reason": "The native run changed or cannot accept this input."}
        if self.active_response() is None:
            return {"accepted": False, "mode": "native", "disposition": "held",
                    "reason": "No active native response is ready for live direction. Running tools are not interrupted; nothing was sent or queued for a later turn."}
        if len(runtime.accepted) >= 2000:
            raise RuntimeError("Session input limit reached; start a new session")
        # Reserve once before any await. Neither duplicate control calls nor a
        # lost acknowledgement may publish this command into a later generation.
        runtime.accepted[command.id] = command
        outcome = {"accepted": True, "inputId": command.id, "generationId": generation_id,
                   "disposition": "unknown", "mode": "native"}
        runtime.steering_outcomes[command.id] = outcome
        sent = await self.provider.steer_live(command)
        if not sent:
            outcome.update(accepted=False, disposition="held",
                           reason="No native response is ready for steering. Nothing was sent.")
        return copy.deepcopy(runtime.steering_outcomes[command.id])

    def status(self):
        current = self.selected_identity()
        selected = self.provider is not None and self.binding_current()
        status = self.provider.native_status() if selected else {"supported": False, "reason": self.reason if not self.provider else "The selected provider or native configuration changed; remount to enable its native transport."}
        return {**status, "enabled": bool(getattr(self.loop, "config", {}).get("native_provider") is True),
                "identity": current, "model": current["model"], "steering": "native" if selected else "request_boundary",
                "activeNativeResponse": self.active_response(),
                "receipt": copy.deepcopy({k: v for k, v in self.state.items() if k != "receipts"}),
                "receipts": copy.deepcopy(self.state.get("receipts", [])[-32:]),
                "originalsAvailable": True}

    async def lifecycle(self, kind, data):
        if self.selected_identity() != self.identity:
            raise ValueError("Native provider identity changed during an owned request")
        generation = getattr(self.coordinator.get_capability("live.runtime"), "generation", None) or {}
        if kind == "native.submitting":
            self.state.update(requestId=str(uuid.uuid4()), outcome="submitting", identity=self.identity,
                              generationId=generation.get("id"), updatedAt=time.time())
        elif kind == "native.response.created":
            self.state.update(outcome="running", responseId=data.get("response_id"))
        elif kind == "native.completed":
            self.state.update(outcome="pending" if data.get("pending_steering") else "completed", responseId=data.get("response_id"))
        elif kind == "native.outcome_unknown":
            self.state.update(outcome="unknown", reason=data.get("reason"))
        # Receipts contain identities and phases only, never model input/output,
        # hidden reasoning, credentials, or encrypted compaction payloads.
        allowed = {"input_id", "response_id", "steer_id", "accepted", "execution_replayed", "reason"}
        receipt = {"id": str(uuid.uuid4()), "event": kind, "requestId": self.state.get("requestId"),
                   "time": time.time(), **{k: v for k, v in data.items() if k in allowed}}
        self.state["receipts"] = (self.state.get("receipts", []) + [receipt])[-128:]
        self.state["updatedAt"] = time.time()
        self.save()  # A failed submitting receipt prevents the external send.

    async def compact(self):
        if not self.provider or not self.binding_current():
            raise ValueError(self.status().get("reason", "Native provider unavailable"))
        checkpoint = self.coordinator.get_capability("live.checkpoint")
        if checkpoint:
            await checkpoint()  # Originals are durable before deriving any state.
        canonical = await self.coordinator.get("context").get_messages()
        revision = copy.deepcopy(canonical)
        observe = self.coordinator.get_capability("web.provider_call")
        if not callable(observe):
            raise ValueError("Native compaction capacity accounting is unavailable")
        result = await observe(self.provider, SimpleNamespace(model=self.identity["model"]),
            lambda: self.provider.native_compact(canonical=canonical, identity=self.identity),
            label="Compact provider context")
        if await self.coordinator.get("context").get_messages() != revision or not self.binding_current():
            self.provider._discard_checkpoint("history_changed_during_compaction")
            raise ValueError("History changed during compaction; provider state was discarded")
        record = self.provider.native_export_checkpoint()
        write_private(self.checkpoint_path, json.dumps({"sessionId": self.coordinator.session_id, "record": record}, ensure_ascii=False))
        return {**self.status(), "checkpoint": result["checkpoint"]}


async def install_native(loop, coordinator, providers):
    """Wrap only an explicitly enabled, currently selected supported provider."""
    if coordinator.get_capability("web.native_provider"):
        return coordinator.get("providers") or providers
    sid = coordinator.session_id
    validate_id(sid)
    host = NativeProviderHost(coordinator, loop, app_home() / "sessions" / sid)
    if getattr(loop, "config", {}).get("native_provider") is not True:
        return providers
    try:
        from amplifier_module_provider_openai import OpenAIProvider
        from amplifier_module_provider_openai._capabilities import get_request_policy
        from amplifier_module_provider_openai.native import NativeResponsesProvider, NativeSendAdmission, NATIVE_REQUEST as PROVIDER_REQUEST
    except ImportError:
        host.reason = "The installed provider does not include its optional native transport."
        return providers
    selected, original, _, link = host.selected_mount()
    identity = host.selected_identity()
    if type(original) is not OpenAIProvider or not get_request_policy(identity["model"]).supports_steering:
        host.reason = "Native steering requires a selected OpenAI model with documented exact-ID steering support."
        return providers
    unresolved = (host.state.get("outcome") in {"unknown", "pending"}
                  or any(row.get("outcome") == "unknown" for row in host.state.get("sends", [])))
    if not _official_endpoint(original):
        host.reason = "This configured endpoint does not support the native OpenAI transport."
        if not unresolved:
            return providers
        # Keep only the provider's recovery fence when an endpoint changed over
        # restart. It cannot acquire a native owner on this route, and its
        # unresolved state refuses ordinary transport before any SDK dispatch.
    if "complete" in original.__dict__:
        host.reason = "The provider already has an instance wrapper; native transport must be installed before it."
        return providers
    from amplifier_module_loop_live.scope import LIVE_OWNER, NATIVE_REQUEST as LOOP_REQUEST

    class LoopNativeProvider(NativeResponsesProvider):
        """Bridge provider-owned native calls to the loop's recovery ledger."""

        def _native_eligible(self, params):
            # Never advertise one selected model while natively sending another.
            # The provider owns precedence and exact-ID eligibility; pending or
            # uncertain state still refuses its ordinary fallback.
            # Inspect only the final model field, not another copy of growing
            # request history. The provider's pure plan owns the payload.
            final = (params.get("extra_body") or {}).get("model", params.get("model"))
            return super()._native_eligible(params) and final == host.identity["model"]

        async def _emit(self, kind, **data):
            runtime = getattr(self.owner, "runtime", None)
            command = getattr(runtime, "accepted", {}).get(data.get("input_id"))
            if command and command.target_generation_id:
                data["target_generation_id"] = command.target_generation_id
                data["delivery"] = "native"
            await super()._emit(kind, **data)
            outcome = getattr(runtime, "steering_outcomes", {}).get(data.get("input_id"))
            if outcome and command and command.target_generation_id == outcome["generationId"]:
                phase = {"steering.sent": "queued", "steering.accepted": "queued", "steering.pending": "queued",
                         "steering.applied": "applied", "steering.failed": "held",
                         "native.outcome_unknown": "unknown"}.get(kind)
                if phase:
                    outcome["disposition"] = phase
                    outcome["phase"] = kind.removeprefix("steering.")

        async def complete(self, request, **kwargs):
            # Utility/ordinary requests must not inherit a caller's native job
            # attribution. The provider still owns all transport eligibility.
            token = LOOP_REQUEST.set(None)
            try:
                return await super().complete(request, **kwargs)
            finally:
                LOOP_REQUEST.reset(token)

        async def _native_response(self, params, **kwargs):
            # Async jobs are dispatched inside this boundary. Bind only when
            # the provider has actually admitted its own native request.
            token = LOOP_REQUEST.set(self if PROVIDER_REQUEST.get() is self else None)
            try:
                return await super()._native_response(params, **kwargs)
            finally:
                LOOP_REQUEST.reset(token)

        async def steer_live(self, command):
            # An independently admitted send, never the parent's proof. The
            # protocol has no separately fitted detail request for steering.
            from .coordination_delivery import WORK_ATTEMPT
            delivery = coordinator.get_capability('web.surface_delivery')
            observe = coordinator.get_capability('web.provider_call')
            if delivery is None or not callable(observe):
                raise ValueError("Native steering admission is unavailable")
            attempt = delivery.begin_steer()
            token = WORK_ATTEMPT.set((delivery, attempt))
            steer_token = _STEER_INPUT.set(getattr(command, 'id', None))
            generation_token = _STEER_GENERATION.set(getattr(command, 'target_generation_id', None))
            try:
                invoke = super().steer_live
                return await observe(self, SimpleNamespace(model=host.identity['model']),
                                     lambda: invoke(command), label="Steer provider context")
            except BaseException:
                if attempt['status'] != 'not_sent':
                    delivery.finish_work(attempt, failed=True)
                raise
            finally:
                _STEER_GENERATION.reset(generation_token)
                _STEER_INPUT.reset(steer_token)
                WORK_ATTEMPT.reset(token)

    def owned_loop():
        owner = LIVE_OWNER.get()
        return owner if (getattr(owner, "coordinator", None) is coordinator
                         and host.binding_current()) else None
    host.identity = identity
    host.provider = LoopNativeProvider.wrap(original, owner_getter=owned_loop,
        lifecycle=host.send_lifecycle, trace=host.work_trace, prepare_send=host.prepare_send)
    if unresolved:
        host.provider.request_uncertain = True
    if host.checkpoint_path.exists():
        try:
            saved = json.loads(host.checkpoint_path.read_text())
            if saved.get("sessionId") != sid:
                raise ValueError("session mismatch")
            host.provider.native_restore_checkpoint(saved["record"],
                canonical=await coordinator.get("context").get_messages(), identity=identity)
        except (OSError, ValueError, KeyError, TypeError):
            host.provider._discard_checkpoint("unavailable")
    await coordinator.mount("providers", host.provider, name=identity["instance"])
    if link is not None:
        link.original = host.provider
    return coordinator.get("providers") or providers
