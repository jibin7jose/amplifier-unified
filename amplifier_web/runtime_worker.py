"""Private JSON-lines worker. Runs only inside the configured runtime environment."""
from __future__ import annotations
import asyncio
import copy
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import sys
import time
import uuid

try:
    from .runtime_protocol import MAX_MESSAGE_BYTES, encode_message
    from .message_delivery import contains_input
except ImportError:  # Executed directly inside the isolated runtime.
    from runtime_protocol import MAX_MESSAGE_BYTES, encode_message
    from message_delivery import contains_input

# Reserve a dedicated protocol descriptor before module imports. CLI displays and
# provider logs are routed to stderr, never mistaken for model/app events.
_PROTOCOL = None


def configure_protocol():
    global _PROTOCOL
    _PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr


def publish(data):
    _PROTOCOL.write(encode_message(data))
    _PROTOCOL.flush()


def publish_process_identity():
    """Identify this interpreter, not uv/the guard/a venv redirector."""
    if os.name != "nt":
        return
    import ctypes
    if __package__:
        from .windows_launch_guard import creation_time
    else:
        from windows_launch_guard import creation_time
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    publish({"type": "runtime.identity", "pid": os.getpid(),
             "created": creation_time(kernel, kernel.GetCurrentProcess())})


class Worker:
    def __init__(self):
        self.session = self.runtime = self.execution = None
        self.approvals = {}
        self.approval_contexts = {}
        self.approval_deliveries = {}
        self.operation_ids = set()
        self.operation_controls = 0
        self.stop_revision = 0
        self.bridges = {}
        self.bridge_timings = {}
        self.start_task = None
        self.tasks = set()
        self.shutdown = asyncio.Event()
        self.telemetry = None
        self.controls = None
        self.control_invocations = {}
        self.naming = None
        self.naming_park_task = None
        self.workspace = None
        self.home = None
        self.shared_store = None
        self.shared_handle = None
        self.activation_gate = None
        self.activation = None
        self.parked = False
        self.parked_history_stamp = None
        self.parked_config_stamp = None
        self.config_inputs = ()
        self.command_lock = asyncio.Lock()
        self.memory_task = None
        self.start_config = None
        self.remounting = False
        self.context_bindings = {}
        self.context_inputs = []
        self.root_generation_outcome = None
        self.failed_generation = None
        if __package__:
            from .response_completion import ResponseBlocks
        else:
            from response_completion import ResponseBlocks
        self.response_blocks = ResponseBlocks()
        self.terminal_response_refs = None
        self.terminal_checkpointed = False
        self.preparation_report = {}
        if __package__:
            from .ownership import WorkerOwnership
        else:
            from ownership import WorkerOwnership
        self.ownership = WorkerOwnership(self, lambda data: publish(data))

    async def ask(self, prompt, options, identity=None):
        identity = identity or str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        options = options or ["allow", "deny"]
        self.approvals[identity] = (future, options)
        from amplifier_web.host.approvals import REQUEST_CONTEXT
        if REQUEST_CONTEXT.get() is not None:
            self.approval_contexts[identity] = REQUEST_CONTEXT.get()
        publish({"type": "approval.requested", "id": identity, "prompt": prompt, "options": options})
        try:
            decision = await future
            publish({"type": "approval.resolved", "id": identity, "decision": decision})
            return decision
        except asyncio.CancelledError:
            publish({"type": "approval.resolved", "id": identity, "decision": "expired"})
            raise
        finally:
            self.approvals.pop(identity, None)
            self.approval_contexts.pop(identity, None)

    async def bridge(self, operation, args):
        from amplifier_web.performance_diagnostics import PhaseTiming, active_tool, TIMING_ENABLED
        timeout = 60
        if operation == "capacity.admit" and args.get('waitForCapacity'):
            # The host owns the configurable wait deadline; default waits
            # until capacity or cancellation. Teardown cancels this future.
            timeout = None
        if operation == "dispatch" and args.get("action") == "coordination.wait":
            values = args.get("args", {})
            wait_ms = values.get("waitMs", 0) if isinstance(values, dict) else None
            # The host starts its long-poll clock after transport/preprocessing.
            # Only valid bounded waits earn margin; host validation still applies.
            if type(wait_ms) is int and 0 <= wait_ms <= 60000:
                timeout = max(timeout, wait_ms / 1000 + 5)
        # These fields are host-produced, never accepted from tool arguments.
        args = {key: value for key, value in args.items()
                if key not in {'_handoffSnapshot', '_handoffDrain', '_nativeChild'}}
        if operation == 'capacity.admit':
            from amplifier_web.handoff_drain import witness, native_child
            args.update(_handoffDrain=witness(self, args.get('call') or {}),
                        _nativeChild=native_child(self, args.get('call') or {}))
        elif isinstance(args.get('_childOrigin'), dict):
            from amplifier_web.handoff_drain import native_child
            args['_nativeChild'] = native_child(self, {'sessionId': args['_childOrigin']['runtimeSessionId']})
        handoff_marker = None
        from amplifier_web.handoff_drain import own_target
        if (operation in {'dispatch', 'action.dispatch'} and args.get('action') == 'worktree.handoff'
                and own_target(self, args)):
            from amplifier_web.handoff_drain import begin
            snapshot = await begin(self, args.get('id'))
            if snapshot is not None:
                args['_handoffSnapshot'] = snapshot
                handoff_marker = self._handoff_boundary['id']
        identity = str(uuid.uuid4())
        if handoff_marker:
            self._handoff_boundary['drain'].setdefault('bridgeId', identity)
        future = asyncio.get_running_loop().create_future()
        self.bridges[identity] = future
        cancelled = False
        started = time.monotonic()
        generation = getattr(getattr(self, 'runtime', None), 'generation', None) or {}
        facts = {'type': 'runtime.bridge', 'bridgeId': identity, 'operation': operation,
                 'generation_id': generation.get('id'), 'timeoutSeconds': timeout,
                 'runtimeSessionId': args.get('_runtimeSessionId')}
        owner = active_tool()
        timing = PhaseTiming(kind='bridge', bridgeId=identity, operation=operation,
            runtimeSessionId=args.get('_runtimeSessionId'), generationId=generation.get('id'),
            **({key: owner.facts[key] for key in ('invocationId', 'toolCallId')
                if key in owner.facts} if owner else {})) if TIMING_ENABLED else None
        if timing:
            timing.mark('start')
            self.bridge_timings[identity] = timing
        outcome = 'completed'
        # Only operation names, never arguments or response bodies.
        action = args.get('action')
        if isinstance(action, str) and re.fullmatch(r'[a-z][a-z0-9_.]{0,79}', action):
            facts['action'] = action
        def diagnostic(phase, **extra):
            # Observability must not change a bridge result or mask its error.
            try:
                publish({**facts, 'phase': phase, **extra})
            except Exception:
                pass

        try:
            diagnostic('requested')
            if timing:
                timing.mark('write.start')
            message = {"op": "bridge", "id": identity, "operation": operation, "args": args,
                       **({'_timing': timing.snapshot()} if timing else {})}
            if handoff_marker:
                try:
                    encode_message(message)  # Definite local rejection before any action bytes.
                except Exception:
                    from amplifier_web.handoff_boundary import inspect
                    await inspect(self, handoff_marker, cancel=True)
                    raise
            publish(message)
            if timing:
                timing.mark('write.end')
            result = await asyncio.wait_for(future, timeout)
            if handoff_marker:
                try:
                    if isinstance(result, dict) and result.get('accepted') is True:
                        await self._finish_handoff_ack(result.get('result'), future)
                    elif isinstance(result, dict) and result.get('accepted') is False:
                        await self._finish_handoff_ack({'id': handoff_marker, 'phase': 'absent'}, future)
                except Exception as exc:
                    # Keep a known pending receipt; local setup failure does not
                    # turn it into a failed action or authorize a retry.
                    diagnostic('handoff-drain-unavailable', errorType=type(exc).__name__)
            if timing:
                timing.mark('resumed')
            diagnostic('completed', elapsedSeconds=round(time.monotonic() - started, 3))
            return result
        except TimeoutError as exc:
            outcome = 'failed'
            if timeout is None:
                raise
            diagnostic('timeout', elapsedSeconds=round(time.monotonic() - started, 3),
                       errorType='TimeoutError')
            raise TimeoutError(
                f"App bridge response timed out after {timeout:g} seconds "
                "(transport timeout, not normal coordination.wait expiry)."
            ) from exc
        except asyncio.CancelledError:
            outcome = 'cancelled'
            cancelled = True
            diagnostic('cancelled', elapsedSeconds=round(time.monotonic() - started, 3))
            raise
        except Exception as exc:
            if handoff_marker and isinstance(getattr(exc, '_handoff_disposition', None), dict):
                try:
                    await self._finish_handoff_ack(exc._handoff_disposition, future)
                except Exception as disposition_error:
                    diagnostic('handoff-drain-unavailable', errorType=type(disposition_error).__name__)
            outcome = 'failed'
            diagnostic('failed', errorType=type(exc).__name__,
                       elapsedSeconds=round(time.monotonic() - started, 3))
            raise
        finally:
            self.bridge_timings.pop(identity, None)
            if operation == 'capacity.admit' and args.get('waitForCapacity'):
                # A local future can disappear while this process stays alive.
                # Fence the exact host-side wait, including a reply in transit.
                publish({'op': 'bridge.cancel' if cancelled else 'bridge.received', 'id': identity})
            self.bridges.pop(identity, None)
            if not future.done():
                future.cancel()
            if timing:
                timing.mark('end')
                self.emit_timing(timing.snapshot(outcome))

    def _own_handoff_ack(self, receipt):
        """Retain the exact acknowledgement before its caller can be resumed."""
        from amplifier_web.handoff_drain import finish
        task = asyncio.create_task(finish(self, receipt))
        self.tasks.add(task)
        def settled(done):
            self.tasks.discard(done)
            if not done.cancelled() and (error := done.exception()) is not None:
                # A cancelled waiter cannot report this failure itself. Retain
                # the uncertain marker and a bounded diagnostic, never replay.
                try:
                    publish({'type': 'runtime.bridge', 'phase': 'handoff-drain-unavailable',
                             'errorType': type(error).__name__})
                except Exception:
                    pass
        task.add_done_callback(settled)
        return task

    async def _finish_handoff_ack(self, receipt, future):
        task = getattr(future, '_handoff_ack', None) or self._own_handoff_ack(receipt)
        return await asyncio.shield(task)

    def emit_timing(self, value):
        try:
            publish({'type': 'runtime.timing', 'timing': value})
        except Exception:
            pass

    async def admit_model(self, row):
        registry = self.session.coordinator.get_capability('live.children')
        child = registry.sessions.get(row.get('sessionId')) if registry else None
        lookup = child.coordinator.get_capability('web.child_execution') if child else None
        scope = lookup() if lookup else None
        if row.get('sessionId') != self.session.session_id and (scope is None or row.get('runId') != scope['runId']):
            return {'allowed': False, 'state': 'cancelled',
                    'reasons': ['The originating native child run is no longer current.']}
        result = await self.bridge('capacity.admit', {
            'call': row, 'waitForCapacity': True, '_runtimeSessionId': self.session.session_id,
            '_generationId': scope['generationId'] if scope else (self.runtime.generation or {}).get('id'),
            **({'_childOrigin': scope} if scope else {})})
        if scope is not None and lookup() != scope:
            if result.get('allowed'):
                # This exact call was reserved, but has not reached its provider.
                # Settle only that known admission; unknown bridge outcomes are
                # still retained by the host and never replayed here.
                self.telemetry.publish({**row, 'budgetRevision': result['budgetRevision'],
                    'admittedAt': result.get('admittedAt'), 'phase': 'cancelled', 'endedAt': time.time(),
                    'usage': {'inputTokens': 0, 'outputTokens': 0, 'totalTokens': 0,
                              'costUsd': 0, 'costSource': 'host: provider not invoked'}})
            return {'allowed': False, 'state': 'cancelled',
                    'reasons': ['The native child execution changed before provider invocation.']}
        return result

    def app_access_bridge(self, coordinator, *, assignment=None):
        lookup = getattr(coordinator, 'get_capability', None)
        existing = lookup('web.app_access.bridge') if callable(lookup) else None
        if callable(existing):
            # Loop preparation can evaluate this before the tool installer's
            # idempotency check. Preserve both the assigned bridge and its
            # nested-child installer instead of capturing a later root input.
            return existing
        # Workers inherit the originating inputs at assignment, never
        # another client's later input or another coordinator's receipts.
        canonical_inputs = (getattr(getattr(self, 'runtime', None), 'generation', None) or {}).get('input_ids')
        assigned = None if coordinator is self.session.coordinator else list(
            assignment[0] if assignment is not None else canonical_inputs or [])
        assigned_generation = (assignment[2] if assignment is not None else
            (getattr(getattr(self, 'runtime', None), 'generation', None) or {}).get('id'))
        assigned_stop = assignment[3] if assignment is not None else getattr(self, 'stop_revision', 0)
        assigned_context = None if assigned is None else copy.deepcopy(
            assignment[1] if assignment is not None else {i: self.context_bindings[i] for i in assigned if i in self.context_bindings})
        assigned_bindings = None if assigned is None else [{'inputId': i, 'clientId': assigned_context.get(i, {}).get('clientId')} for i in assigned]
        assigned_clients = None if assigned_bindings is None else [b['clientId'] for b in assigned_bindings]
        assigned_execution = (assignment[4] if assignment is not None and len(assignment) > 4 else
            {key: (self.start_config or {}).get(key, 0) for key in
             ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')})
        assigned_runtime, assigned_gate = self.runtime, self.activation_gate
        root_policy = self.session.coordinator.get_capability('web.write_policy')
        if root_policy is not None:
            coordinator.register_capability('web.write_policy', root_policy)
        assigned_run = coordinator.get_capability('web.worker_run') if callable(lookup) else None
        def child_origin():
            if assigned is None or not assigned_generation or not assigned_run:
                return None
            registry = self.session.coordinator.get_capability('live.children')
            row = registry.rows.get(coordinator.session_id) if registry else None
            child = registry.sessions.get(coordinator.session_id) if registry else None
            if (not row or row.get('runId') != assigned_run or not child or child.coordinator is not coordinator
                    or row.get('status') not in {'starting', 'running', 'working', 'idle'}
                    or self.runtime is not assigned_runtime or self.activation_gate is not assigned_gate
                    or not assigned_gate or not assigned_gate.current_valid
                    or self.stop_revision != assigned_stop or self.runtime.closed or self.runtime.stop_requested
                    or coordinator.cancellation.is_cancelled or self.parked or self.remounting
                    or self.ownership.yielding):
                return None
            return {'rootSessionId': self.session.coordinator.session_id,
                    'runtimeSessionId': coordinator.session_id, 'generationId': assigned_generation,
                    'inputIds': list(assigned), 'runId': assigned_run, **assigned_execution}
        coordinator.register_capability('web.child_execution', child_origin)
        def origin_scope():
            runtime = getattr(self, 'runtime', None)
            generation = getattr(runtime, 'generation', None) or {}
            gate = getattr(self, 'activation_gate', None)
            # The canonical generation holds every delivered input. Never turn
            # the bounded visual-context tail into complete Work membership.
            child = child_origin()
            ids = generation.get('input_ids') if assigned is None else assigned
            if (not runtime or assigned_run and not child
                    or not (child or generation.get('id')) or not gate or not gate.current_valid
                    or runtime.closed or runtime.stop_requested
                    or coordinator.cancellation.is_cancelled or not isinstance(ids, list)
                    or assigned is not None and not child and (generation.get('id') != assigned_generation
                                                or self.stop_revision != assigned_stop)):
                return None
            return {'rootSessionId': self.session.coordinator.session_id,
                    'runtimeSessionId': coordinator.session_id, 'generationId': child['generationId'] if child else generation['id'],
                    'inputIds': list(ids), 'inputCoverage': 'complete',
                    'stopRevision': self.stop_revision}
        coordinator.register_capability('web.work_origin_scope', origin_scope)
        def current_full_access():
            # Read the same current policy as the host, never a startup copy.
            # Descendants retain their own live assignment while the root owns
            # the policy identity. No content-coverage proof is required.
            if origin_scope() is None or self.home is None or self.workspace is None:
                return False
            from amplifier_web.access_mode import read_mode
            import sqlite3
            try:
                return read_mode(self.home, self.workspace, self.session.coordinator.session_id,
                    owner_session_id=(self.start_config or {}).get('ownerSessionId')) == 'full'
            except (OSError, ValueError, sqlite3.Error):
                return False
        coordinator.register_capability('web.work_full_access', current_full_access)
        coordinator.register_capability('web.performance.emit', self.emit_timing)
        coordinator.register_capability('web.performance.scope', lambda: {
            'runtimeSessionId': coordinator.session_id,
            'generationId': (getattr(self.runtime, 'generation', None) or {}).get('id')})
        watched = set()
        async def bridge(operation, args):
            if assigned_run and child_origin() is None:
                raise asyncio.CancelledError('The originating native child run is no longer current.')
            ids = self.context_inputs if assigned is None else assigned
            # Neither approval nor generation identity is model-supplied.
            args = {key: value for key, value in args.items()
                    if key not in {"_coordinationApproval", "_generationId", "_configurationWait", "_workOrigin",
                                   "_workDispatchGuarded", "_responseMode", "_childOrigin", "_runtimeOwnerId"}}
            if assigned is None:
                runtime = getattr(self, "runtime", None)
                generation = getattr(runtime, "generation", None) or {}
                args["_generationId"] = generation.get("id")
            elif origin_scope() is not None:
                args['_generationId'] = assigned_generation
            if child := child_origin():
                args['_childOrigin'] = child
            all_inputs = ((getattr(getattr(self, 'runtime', None), 'generation', None) or {}).get('input_ids', [])
                          if assigned is None else assigned)
            # Transport the complete native set, not the visual-context tail.
            # The host independently compares its canonical generation. Content
            # budgets must not truncate the identity used by that comparison.
            bound_ids = all_inputs if isinstance(all_inputs, list) else []
            bound_context = self.context_bindings if assigned is None else assigned_context
            args = {**args, '_runtimeSessionId': coordinator.session_id,
                    '_inputBindings': [{'inputId': i, 'clientId': bound_context.get(i, {}).get('clientId')} for i in bound_ids],
                    '_inputClients': [bound_context.get(i, {}).get('clientId') for i in bound_ids]}
            if operation in {'dispatch', 'work.effect.check'}:
                dispatch = getattr(coordinator, '_tool_dispatch_contexts', {}).get(asyncio.current_task())
                check = coordinator.get_capability('web.work_effect_origin')
                # The executing task, not model-supplied IDs, supplies this
                # observation. The service independently owns applicability.
                if dispatch and callable(check):
                    tool_name = args.get('toolName') if operation == 'work.effect.check' else 'app_control'
                    tool_input = args.get('toolInput') if operation == 'work.effect.check' else {
                        'operation': operation, 'args': {k: v for k, v in args.items() if not k.startswith('_')}}
                    args['_workOrigin'] = await check({**dispatch, 'tool_name': tool_name, 'tool_input': tool_input})
                if operation == 'work.effect.check':
                    programmatic = coordinator.get_capability('web.work_programmatic_guarded')
                    args['_workDispatchGuarded'] = bool(callable(programmatic) and programmatic())
            if operation == 'dispatch':
                # Host-owned response preference, added only after observing the
                # original call. Never alter identity-bound public arguments.
                args['_responseMode'] = 'receipt'
            if operation.startswith('context.'):
                context = self.context_bindings if assigned_context is None else assigned_context
                bindings = [context[i] for i in ids if i in context]
                if assigned is not None:
                    if operation == 'context.read':
                        watched.add(args.get('surfaceId'))
                    bindings = [{**b, 'targets': [t for t in b.get('targets', []) if t['surfaceId'] in watched]} for b in bindings]
                    if not bindings:
                        bindings = [{'clientId': 'detached-worker', 'targets': []}]
                args = {**args, '_contextInputs': ids,
                        '_contextBindings': bindings}
            if assigned is None and operation == 'dispatch' and args.get('action') == 'capability.request':
                # Register before publishing the host-owned card: an immediate
                # human decision must not race a yet-unregistered native wait.
                command = args.get('id') or str(uuid.uuid4())
                identity = 'capability:' + str(uuid.uuid5(uuid.NAMESPACE_URL, command))
                if identity in self.approvals:
                    raise ValueError('This configuration request is already waiting for a decision.')
                future = asyncio.get_running_loop().create_future()
                self.approvals[identity] = (future, ['allow', 'deny'])
                try:
                    result = await self.bridge(operation, {**args, 'id': command, '_configurationWait': True})
                    grant = result.get('result', {})
                    if grant.get('status') == 'pending':
                        expiry = grant.get('expiresAt')
                        try:
                            if expiry is None:
                                await future
                            else:
                                await asyncio.wait_for(future, max(0, expiry-time.time()))
                        except TimeoutError:
                            pass
                        # A decision is a wakeup, not authority. Revocation can
                        # overtake it; return the current canonical record.
                        current = await self.bridge('dispatch', {**args, 'action': 'capability.read',
                            'args': {'sessionId': grant['sessionId']}})
                        selected = next((row for row in current.get('result', {}).get('grants', []) if row['id'] == identity), None)
                        return {**result, 'result': selected or {**grant, 'usable': False}, 'waitCompleted': True}
                    return result
                finally:
                    self.approvals.pop(identity, None)
                    if not future.done():
                        future.cancel()
            return await self.bridge(operation, args)
        register = getattr(coordinator, "register_capability", None)
        if callable(register):
            register('web.app_access.bridge', bridge)
            async def effect_check(name, arguments):
                dispatch = getattr(coordinator, '_tool_dispatch_contexts', {}).get(asyncio.current_task())
                control = self.control_invocations.get(asyncio.current_task())
                runtime, gate = self.runtime, self.activation_gate
                generation = getattr(runtime, 'generation', None)
                epoch, before = self.stop_revision, copy.deepcopy(arguments)
                inputs = copy.deepcopy((generation or {}).get('input_ids'))
                child_scope = child_origin()
                if assigned_run and child_scope is None:
                    return {'allowed': False, 'effectAuthorized': False}
                def current_control():
                    return (control is not None and control['valid']()
                        and self.control_invocations.get(asyncio.current_task()) is control
                        and control['coordinator'] is coordinator and control['name'] == name
                        and control['arguments'] == arguments)
                if (not gate or not gate.current_valid or not runtime
                        or runtime.closed or runtime.stop_requested or coordinator.cancellation.is_cancelled):
                    return {'allowed': False, 'effectAuthorized': False}
                if control is not None:
                    if not current_control():
                        return {'allowed': False, 'effectAuthorized': False}
                    identity = control['callId']
                elif dispatch and (generation or child_scope):
                    identity = dispatch.get('tool_call_id')
                else:
                    return {'allowed': False, 'effectAuthorized': False}
                try:
                    # The bridge owns the bounded transport deadline. Await it
                    # in this task so the actual dispatch origin stays attached.
                    result = await bridge('work.effect.check', {
                        'toolName': name, 'toolInput': before, 'toolCallId': identity})
                except TimeoutError:
                    return {'allowed': False, 'effectAuthorized': False, 'checkFailure': 'timeout'}
                except ConnectionError:
                    return {'allowed': False, 'effectAuthorized': False, 'checkFailure': 'connection'}
                if (self.runtime is not runtime
                        or (child_origin() != child_scope if child_scope else
                            runtime.generation is not generation or (generation or {}).get('input_ids') != inputs)
                        or not gate.current_valid or self.stop_revision != epoch
                        or runtime.closed or runtime.stop_requested or coordinator.cancellation.is_cancelled
                        or (not current_control() if control is not None else
                            getattr(coordinator, '_tool_dispatch_contexts', {}).get(asyncio.current_task()) is not dispatch
                            or dispatch.get('tool_call_id') != identity)
                        or json.dumps(arguments, sort_keys=True) != json.dumps(before, sort_keys=True)):
                    return {'allowed': False, 'effectAuthorized': False}
                return result
            register('web.work_effect_check', effect_check)
            def capture_child_access():
                # Called at child invocation, before provider preparation can
                # await a later root generation or Stop. Descendants keep the
                # assignment they already received from their own parent.
                if root_policy is not None:
                    root_policy.refresh()
                if assigned is None:
                    generation = getattr(self.runtime, 'generation', None) or {}
                    inputs = list(generation.get('input_ids', []))
                    context = {i: copy.deepcopy(self.context_bindings[i]) for i in inputs if i in self.context_bindings}
                    assignment = (inputs, context, generation.get('id'), self.stop_revision,
                        {key: generation.get(key, (self.start_config or {}).get(key, 0)) for key in
                         ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')})
                else:
                    assignment = (assigned, assigned_context, assigned_generation, assigned_stop, assigned_execution)
                async def install_child_access(child_coordinator):
                    from amplifier_web.app_guidance import install_app_access
                    await install_app_access(child_coordinator, self.app_access_bridge(child_coordinator,
                                                                                      assignment=assignment))
                return install_child_access
            register("web.app_access.install", capture_child_access)
        if assigned is None and callable(register):
            async def peer_continuation_ready():
                runtime, gate = self.runtime, self.activation_gate
                generation = runtime.generation if runtime else None
                generation_id = generation.get("id") if generation else None
                epoch = self.stop_revision
                if (not generation or not gate or not gate.current_valid
                        or runtime.closed or runtime.stop_requested):
                    return False
                task = self.controls.tasks.record() or {}
                result = await bridge("coordination.continuation_ready", {
                    "taskId": task.get("id"), "taskRevision": task.get("revision")})
                return (result.get("ready") is True and gate.current_valid
                        and self.runtime is runtime and runtime.generation is generation
                        and generation.get("id") == generation_id
                        and self.stop_revision == epoch and not runtime.stop_requested
                        and not runtime.closed and not coordinator.cancellation.is_cancelled)
            register("web.peer_continuation_ready", peer_continuation_ready)
        return bridge

    def observe(self, event):
        """Publish lifecycle metadata, never provider reasoning or tool inputs."""
        event = dict(event)
        root_session_id = self.runtime.session_id if self.runtime else None
        event_session_id = event.get('sessionId') or event.get('session_id') or root_session_id
        event_root_id = event.get('rootSessionId') or event.get('root_session_id') or root_session_id
        if event.get('type', '').startswith('generation.') and event_session_id:
            event['sessionId'] = event_session_id
            event['rootSessionId'] = event_root_id
        root_generation = event_session_id == root_session_id and event_root_id == root_session_id
        if root_generation and event.get('type') == 'generation.started':
            # Explicit inputs carry the host epoch captured at transport
            # admission. Native child/job follow-ups inherit this process's
            # original startup epoch: Stop closes the worker, never renews it.
            binding = self.context_bindings.get(event.get('initial_input_id'), {})
            interruption = binding.get('interruptionRevision', (self.start_config or {}).get('interruptionRevision', 0))
            event['interruptionRevision'] = interruption
            generation = self.runtime.generation
            if generation and generation.get('id') == event.get('generation_id'):
                generation.setdefault('interruptionRevision', interruption)
                event['interruptionRevision'] = generation['interruptionRevision']
            self.root_generation_outcome = None
            self.failed_generation = None
            self.response_blocks.reset()
            self.terminal_response_refs = None
        if root_generation and event.get('type') == 'assistant.message':
            self.response_blocks.message(event)
        elif root_generation and event.get('type') in {'generation.finished', 'generation.failed', 'generation.detached'}:
            self.root_generation_outcome = {
                'generation.finished': 'completed',
                'generation.failed': 'error',
                'generation.detached': 'interrupted',
            }[event['type']]
            generation = getattr(self.runtime, 'generation', None)
            self.failed_generation = (
                {**{key: event.get(key) for key in ('generation_id', 'input_ids', 'error_type', 'error_category', 'error_stage')},
                 '_runtime': self.runtime, '_generation': generation, '_stopRevision': self.stop_revision}
                if event['type'] == 'generation.failed' and generation
                    and generation.get('id') == event.get('generation_id')
                    and generation.get('input_ids') == event.get('input_ids') else None)
        if self.controls:
            from amplifier_web.scheduled_input import finish as finish_scheduled_input
            finish_scheduled_input(self.controls, event)
            from amplifier_web.observation_input import finish as finish_observation_input
            finish_observation_input(self.controls, event)
        if root_generation and event.get('type') == 'generation.started':
            self.context_inputs = []
        if root_generation and event.get('type') in {'input.delivered', 'steering.applied'} and event.get('input_id'):
            if event['input_id'] not in self.context_inputs:
                self.context_inputs = (self.context_inputs + [event['input_id']])[-8:]
        from amplifier_web.handoff_drain import observe as observe_handoff_drain
        observe_handoff_drain(self, event)
        if self.naming:self.naming.observe(event)
        if self.telemetry:
            self.telemetry.lifecycle(event)
        loop = self.session.coordinator.get("orchestrator") if self.session else None
        if event.get("type", "").startswith("job.") and loop:
            job = loop.jobs.get(event.get("job_id"), {})
            arguments = (job.get("tool_call") or {}).get("arguments", {})
            if isinstance(arguments, dict) and isinstance(arguments.get("agent"), str):
                event["agent"] = arguments["agent"]
        if root_generation and event.get("type") == "generation.finished":
            anchor = getattr(self, "collaboration_terminal", None) or {}
            from amplifier_operations.coordination import fingerprint
            if (anchor.get("generationId") == event.get("generation_id")
                    and anchor.get("textDigest") == fingerprint(event.get("text", ""))):
                event["nativeTerminal"] = anchor
                refs = self.terminal_response_refs
                if refs and refs.get("generationId") == event.get("generation_id"):
                    event["finalMessageRefs"] = refs
        publish(event)
        if event.get("type") == "generation.finished" and loop:
            count = sum(not job["task"].done() for job in loop.jobs.values())
            if count:
                publish({"type": "runtime.activity", "phase": "waiting-workers", "activeWorkers": count,
                    "detail": f"Waiting for {count} delegated task{'s' if count != 1 else ''} to report back."})
        elif event.get("type") == "input.delivered":
            publish({"type": "runtime.activity", "phase": "model", "detail": "Preparing a model response."})

    def install_collaboration_checkpoint(self, coordinator):
        """Capture an exact native row at the already awaited checkpoint."""
        if coordinator is not self.session.coordinator or coordinator.get_capability("web.collaboration.checkpoint"):
            return
        checkpoint = coordinator.get_capability("live.checkpoint")
        if not checkpoint:
            return
        async def observed_checkpoint(*args, **kwargs):
            self.collaboration_terminal = None
            self.terminal_response_refs = None
            result = await checkpoint(*args, **kwargs)
            # The persistence owner applies preserve_system before returning
            # these rows; context indexes are not necessarily native indexes.
            rows = result if isinstance(result, list) else []
            if rows and rows[-1].get("role") == "assistant" and not rows[-1].get("tool_calls"):
                from amplifier_web.automatic_history import display_message
                from amplifier_operations.coordination import fingerprint
                native = display_message(rows[-1], len(rows) - 1, {"id": coordinator.session_id})
                generation = self.runtime.generation or {}
                if native and generation.get("id"):
                    content = rows[-1].get("content", "")
                    terminal_text = content if isinstance(content, str) else "".join(
                        block.get("text", "") for block in content if isinstance(block, dict)
                        and block.get("type") in {"text", "output_text"}) if isinstance(content, list) else ""
                    self.collaboration_terminal = {"messageId": native["id"], "nativeIndex": native["nativeIndex"],
                        "rootSessionId": coordinator.session_id, "generationId": generation["id"],
                        "nativeText": native["text"], "textDigest": fingerprint(terminal_text)}
                    self.terminal_response_refs = self.response_blocks.terminal(rows[-1], generation["id"])
            return result
        coordinator.register_capability("live.checkpoint", observed_checkpoint)
        coordinator.register_capability("web.collaboration.checkpoint", True)

    def install_activity(self, coordinator):
        from amplifier_core import HookResult
        # ask_user is delivered over the host's durable approval channel even
        # though this worker has no terminal. This advertises transport only;
        # each request still waits for its own explicit client response.
        coordinator.register_capability("approval.interactive", True)
        if coordinator.get_capability("web.activity"):
            # Live provider edits may mount a new immutable instance after the
            # hooks were installed. Preserve its admission wrapper as well.
            if self.telemetry:
                providers = coordinator.get("providers") or {}
                for name, provider in list(providers.items()):
                    providers[name] = self.telemetry.instrument_provider(coordinator.session_id, provider,
                        run_id=coordinator.get_capability("web.worker_run"))
            return
        coordinator.register_capability("web.activity", True)
        from amplifier_web.performance_diagnostics import install_preparation_timing
        install_preparation_timing(coordinator)
        if self.session is not None and coordinator is self.session.coordinator:
            async def response_block(event, data):
                from amplifier_web.execution_events import CALL_PURPOSE
                if not CALL_PURPOSE.get() and data.get("session_id", coordinator.session_id) == coordinator.session_id:
                    generation = self.runtime.generation or {}
                    self.response_blocks.block(event, data, generation.get("id"))
                return HookResult()
            # Run before loop-live's content-block observer (default priority).
            for event in ("content_block:start", "content_block:end"):
                coordinator.hooks.register(event, response_block, priority=-100,
                    name="amplifier-web-response-" + event)
            async def response_boundary(event, data):
                from amplifier_web.execution_events import CALL_PURPOSE
                if not CALL_PURPOSE.get() and data.get("session_id", coordinator.session_id) == coordinator.session_id:
                    self.response_blocks.reset()
                return HookResult()
            for event in ("provider:request", "llm:request", "tool:pre"):
                coordinator.hooks.register(event, response_boundary, priority=-100,
                    name="amplifier-web-response-boundary-" + event)
        # A resumed child reuses its session ID but owns a new coordinator.
        # Bind hooks to their originating run, never the mutable registry row.
        activity_run_id = coordinator.get_capability("web.worker_run")
        async def observe_operation(event):
            identity = (coordinator.session_id, event.get("operationId"))
            if event.get("phase") == "started":
                self.operation_ids.add(identity)
            try:
                from amplifier_web.computation import KERNEL_TRANSPORT
                if event.get("source") == "tool-bash" and KERNEL_TRANSPORT.get():
                    return {"capturedBy": "computation"}
                return await self.bridge("operations.observe", {
                    "runtimeSessionId": coordinator.session_id, "event": event})
            finally:
                if event.get("phase") == "finished":
                    self.operation_ids.discard(identity)
                    task = asyncio.create_task(self.park())
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
        coordinator.register_capability("operations.observe", observe_operation)
        async def admit_questions(question_ids):
            return await self.bridge("questions.admit", {"questionIds": question_ids})
        coordinator.register_capability("questions.admit", admit_questions)
        async def admit_integration(identity, command):
            return await self.bridge('integration.admit', {'id': identity, 'command': command})
        coordinator.register_capability('integration.admit', admit_integration)
        def public_stream():
            from amplifier_web.execution_events import CALL_PURPOSE
            return not CALL_PURPOSE.get()
        coordinator.register_capability("live.public_stream", public_stream)
        if self.telemetry:
            if (self.home is not None and self.workspace is not None
                    and coordinator.session_id == self.runtime.session_id
                    and coordinator.session_id not in self.telemetry.context_inspectors):
                from amplifier_web.context_inspection_adapter import ContextInspection
                from amplifier_web.session_files import capture_dir
                self.telemetry.context_inspectors[coordinator.session_id] = ContextInspection(
                    coordinator, capture_dir(self.workspace, coordinator.session_id), self.home)
            coordinator.register_capability('image.backend_invoke',
                lambda backend, arguments: self.telemetry.image_backend_call(
                    coordinator.session_id, backend, arguments, run_id=activity_run_id))
            coordinator.register_capability('web.provider_observe', lambda provider: self.telemetry.instrument_provider(coordinator.session_id, provider, run_id=activity_run_id))
            coordinator.register_capability('web.provider_call', lambda provider, request, invoke, **kwargs: self.telemetry.provider_call(coordinator.session_id, provider, request, invoke, run_id=activity_run_id, **kwargs))
            registry = coordinator.get_capability("live.children")
            if registry and coordinator.session_id in registry.rows:
                self.telemetry.lifecycle({"type":"child.updated", **registry.rows[coordinator.session_id]})
            providers = coordinator.get("providers") or {}
            for name, provider in list(providers.items()):
                providers[name] = self.telemetry.instrument_provider(coordinator.session_id, provider,
                        run_id=coordinator.get_capability("web.worker_run"))
        async def activity(event, data):
            identity = coordinator.session_id
            if data.get("session_id", identity) != identity:
                return HookResult()
            stamped = None
            if self.telemetry:
                stamped = self.telemetry.hook(identity, event, data)
            result = (HookResult(action='modify', data=stamped)
                      if event in {'tool:pre', 'tool:post', 'tool:error'} and isinstance(stamped, dict)
                      else HookResult())
            from amplifier_web.execution_events import CALL_PURPOSE
            if CALL_PURPOSE.get() or event.startswith("llm:"):
                # Naming calls retain their measured usage, but do not own the
                # conversation's busy indicator or a delegated worker's state.
                return result
            phase = "model"
            retry = {}
            detail = "Thinking..."
            if event == "context:compaction_started":
                phase, detail = "compacting", "Making room in the conversation. This can take a few minutes. You can keep sending updates."
                if data.get("recovery"):
                    detail = "Preparing recent context from saved history. This can take a few minutes. Older history remains available. You can keep sending updates."
            elif event == "context:compaction_progress":
                phase = "compacting"
                completed, remaining = data.get("completed_parts", 0), data.get("remaining_parts", 0)
                detail = (f"Recovering oversized history · {completed} of {completed + remaining} parts saved. This can take a few minutes."
                          if remaining else "Conversation context prepared; continuing work.")
            elif event == "context:compaction_finished":
                detail = "Conversation context prepared; continuing work." if data.get("outcome") == "completed" else "Context preparation " + str(data.get("outcome", "ended")) + "."
            elif event == "provider:retry":
                phase = "retrying"
                attempt, maximum = data.get("attempt"), data.get("max_retries")
                suffix = f" ({attempt} of {maximum})" if isinstance(attempt, int) and isinstance(maximum, int) else ""
                if suffix:
                    retry = {"retryAttempt": attempt, "retryMax": maximum}
                detail = "The model request was interrupted; retrying" + suffix + "."
            elif event.startswith("tool:"):
                phase = "tools"
                tool = data.get("tool_name")
                tool = tool[:100] if isinstance(tool, str) else "tool"
                detail = ("Running " if event == "tool:pre" else "Finished " if event == "tool:post" else "An error occurred in ") + tool + "."
            if identity == self.runtime.session_id:
                publish({"type": "runtime.activity", "phase": phase, "detail": detail, **retry})
            else:
                registry = coordinator.get_capability("live.children")
                row = registry.rows.get(identity, {}) if registry else {}
                publish({"type": "worker.activity", "workerId": identity, "phase": phase,
                    "detail": detail, "name": row.get("agent", "Worker"), "callId": row.get("callId"),
                    "runId": activity_run_id, "time": time.time(), **retry})
            return result
        for event in ("provider:request", "provider:retry", "tool:pre", "tool:post", "tool:error", "llm:request", "llm:response", "context:compaction_started", "context:compaction_progress", "context:compaction_finished"):
            # Exact tool observation identity must precede the canonical
            # context-intelligence logger (priority 100), independent of mount order.
            coordinator.hooks.register(event, activity, name="amplifier-web-activity-" + event,
                priority=-100 if event.startswith('tool:') else 0)

    async def preparation_progress(self, directory):
        while True:
            if (directory / "live-mount-plan.json").exists():
                publish({"type": "runtime.progress", "phase": "session-mount",
                    "detail": "Loading the configured providers, tools, and worker agents."})
                return
            await asyncio.sleep(2)

    async def commit_replacement(self, replacement, *, deferred, stop_revision):
        if deferred:
            await self.bridge('configuration.nextMessage.admit', {})
            # A Stop command can be processed after the host sent Allow but
            # before this continuation runs. No await separates this local
            # fence from deleting the journal.
            if self.stop_revision != stop_revision or self.shutdown.is_set():
                raise RuntimeError('Bundle application was stopped before commit.')
        replacement.commit()

    async def start(self, config, *, raise_errors=False, recover_bundle=True, resolved_root=None):
        started = (time.monotonic_ns(), time.process_time_ns())
        startup_timing = None
        report_directory = None
        outcome = "failed"
        progress = None
        startup_capture = None
        replacement = None
        stop_revision = self.stop_revision
        try:
            publish({"type": "runtime.progress", "phase": "bundle-preparation",
                "detail": "Loading your bundle and app behaviors; downloading or installing modules as needed."})
            # Load only app code, never the outer host's site-packages metadata.
            if __package__:
                from .runtime_bootstrap import bootstrap_app_package
            else:
                from runtime_bootstrap import bootstrap_app_package
            bootstrap_app_package()
            from amplifier_web.host.config import app_home
            from amplifier_web.worker_diagnostics import StartupCapture, StartupTimings
            startup_timing = StartupTimings(started=started)
            with startup_timing.phase("worker.bootstrap", started=started):
                pass
            startup_capture = StartupCapture()
            with startup_timing.phase('worker.imports'):
                from amplifier_web.host.session import prepare_manager
                from amplifier_web.execution_events import ExecutionEvents
                from amplifier_web.runtime_controls import RuntimeControls
                from amplifier_web.shared_state import ActivationGate, configuration_stamp
                from amplifier_module_loop_live.runtime import Runtime
            self.home = app_home()
            report_directory = self.home / "runtime-reports" / config["id"]
            from amplifier_web.runtime_qualification import active_install_overrides
            # Caller policy is inherited exactly, even when it names an app path.
            # Otherwise scope the app policy to Foundation's installer, never
            # the worker environment inherited by unrelated tool subprocesses.
            qualified_overrides = None
            install_overrides = None
            if not os.environ.get("UV_OVERRIDE"):
                qualified_overrides = active_install_overrides(self.home)
                install_overrides = (qualified_overrides if qualified_overrides is not None
                    else Path(__file__).with_name("runtime_deps") / "compatibility.txt")
            self.runtime = Runtime(session_id=config["id"], observer=self.observe, max_input_chars=200_000)
            self.telemetry = ExecutionEvents(config["id"], publish)
            workspace = Path(config.get("workspace") or config.get("workingDirectory") or os.getcwd()).expanduser().resolve(strict=True)
            self.workspace = workspace
            self.start_config = dict(config)
            # Execution locking lives in the isolated runtime. The outer host
            # uses the same Foundation native-history reader for navigation.
            with startup_timing.phase('worker.ownership'):
                if self.shared_store is None:
                    from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp
                    self.shared_store = SharedSessionStore(workspace, config["id"])
                    self.shared_store_stamp = file_stamp
                    self.activation_gate = ActivationGate()
                if self.shared_handle is None:
                    self.shared_handle = await asyncio.to_thread(
                        self.shared_store.acquire, app="amplifier-unified", pid=os.getpid())
            with startup_timing.phase('worker.recovery'):
                if recover_bundle:
                    from amplifier_web.bundle_selection import BundleTransaction
                    BundleTransaction(self.home, workspace, config["id"]).restore()
                from amplifier_web.history_revision import recover_pending
                recover_pending(self.home, workspace, config['id'])
                if config.get('bundleReplacement') is not None:
                    from amplifier_web.bundle_selection import begin_replacement
                    replacement, resolved_root = await begin_replacement(self.home, workspace, config,
                        admit=lambda: self.bridge('configuration.nextMessage.admit', {}))
            self.activation = self.activation_gate.activate()
            self.runtime.capture_activation = self.activation_gate.current
            # Always allow loading the saved transcript when one exists; the
            # adapter marks interrupted jobs as evidence, never replays them.
            progress = asyncio.create_task(self.preparation_progress(report_directory))
            # Saved controls are the resume authority, including an explicit
            # reset. Only a user/agent selection action may replace that choice.
            selection = config.get('selection')
            controls_path = self.home / 'sessions' / config['id'] / 'control-state.json'
            if config.get('replaceSavedSelection') is not True and controls_path.exists():
                saved_controls = json.loads(controls_path.read_text())
                if 'selection' in saved_controls:
                    selection = saved_controls['selection']
            with startup_timing.phase('manager.prepare'):
                self.session, self.runtime, report = await prepare_manager(workspace,
                    runtime=self.runtime, bundle=config.get("bundle") or None, ask=self.ask,
                    resume=True, application_host="Amplifier Web", selection=selection or None,
                    report_dir=report_directory, shared_handle=self.shared_handle,
                    shared_handle_getter=lambda: self.shared_handle,
                    write_guard=self.activation_gate.check_current, resolved_root=resolved_root,
                    execution_workspace=config.get("workingDirectory"), install_overrides=install_overrides,
                    owner_session_id=config.get("ownerSessionId"),
                    startup_timing=startup_timing)
            if qualified_overrides is not None:
                active_install_overrides(self.home, str(qualified_overrides))
            self.config_inputs = tuple(report.get("config_inputs", ()))
            from amplifier_web.attachments import encode
            self.session.coordinator.register_capability('live.attachments.encode',encode)
            with startup_timing.phase('worker.controls.restore'):
                self.controls = RuntimeControls(self.session, self.runtime, self.telemetry,
                    preserve_configuration=config.get('_preserveConfiguration') is True,
                    tool_invocation=self.tool_invocation)
                self.controls.capacity.admit = self.admit_model
                self.telemetry.admission_guard = self.controls.capacity.guard
                # Preserve app controls on native mounts. A legacy common snapshot
                # retains its previous restoration policy during one-time recovery.
                if report.get("history_source") != "legacy-checkpoint":
                    if config.get('replaceSavedSelection') is True:
                        await self.controls.restore(selection_override=config['selection'])
                    else:
                        await self.controls.restore()
                self.start_config.pop('replaceSavedSelection', None)
                self.start_config.pop('bundleReplacement', None)
                self.start_config.pop('_preserveConfiguration', None)
                self.controls.persist()
            if config.get("forkContext") and not report.get("resumed"):
                # Fork conversational context without tool receipts or runtime
                # ownership. Source operations must never be replayed.
                from types import SimpleNamespace
                messages = [{"role": row["role"], "content": encode(SimpleNamespace(
                    text=row.get("text", ""), attachments=row["attachments"]))
                    if row["role"] == "user" and row.get("attachments") else row.get("text", "")}
                    for row in config.get("messages", []) if row.get("role") in {"user", "assistant"} and (row.get("text") or row.get("attachments"))]
                if messages:
                    await self.session.coordinator.get("context").set_messages(messages)
                report["fork_context_messages"] = len(messages)
            host = self
            from amplifier_web.write_policy_refresh import install as install_write_policy
            policy_session, policy_runtime = self.session, self.runtime
            policy_gate, policy_epoch = self.activation_gate, self.stop_revision
            def policy_current():
                from amplifier_web.access_mode import read_mode
                source = policy_session.coordinator.get_capability('web.write_policy.source')
                return (self.session is policy_session and self.runtime is policy_runtime
                    and self.activation_gate is policy_gate and self.stop_revision == policy_epoch
                    and policy_gate.current_valid and not policy_runtime.closed
                    and not policy_runtime.stop_requested and not self.parked and not self.remounting
                    and not self.ownership.yielding and not policy_session.coordinator.cancellation.is_cancelled
                    and read_mode(self.home, self.workspace, policy_session.session_id,
                        owner_session_id=(self.start_config or {}).get('ownerSessionId')) == source.config.access_mode)
            source = self.session.coordinator.get_capability('web.write_policy.source')
            if source is not None:
                install_write_policy(self.session.coordinator, source,
                    self.session.session_id, current=policy_current)
            from amplifier_web.app_guidance import install_app_access
            with startup_timing.phase('worker.app.access'):
                await install_app_access(self.session.coordinator, self.app_access_bridge(self.session.coordinator))
                self.install_collaboration_checkpoint(self.session.coordinator)
            original_host = self.session.coordinator.get_capability("live.host")
            class ObservedHost:
                def __getattr__(self, name):
                    return getattr(original_host, name)
                async def prepare_execution(self, loop, coordinator, providers):
                    result = await original_host.prepare_execution(loop, coordinator, providers)
                    if coordinator:
                        host.install_activity(coordinator)
                        host.install_collaboration_checkpoint(coordinator)
                        await install_app_access(coordinator, host.app_access_bridge(coordinator))
                        from amplifier_web.host.session import SelectedProvider
                        transform = coordinator.get_capability('web.provider_transform')
                        if isinstance(getattr(loop,'root_provider',None), SelectedProvider):
                            loop.root_provider.execution_adapter = transform
                        runtime, selected, scope = result
                        return runtime, {name: transform(value) for name, value in selected.items()}, scope
                    return result
            # loop-live propagates this host through its existing ContextVar to
            # delegated sessions. Observe their public lifecycle without changing
            # spawning, provider routing, approvals, or execution ownership.
            self.session.coordinator.register_capability("live.host", ObservedHost())
            self.session.coordinator.register_capability("web.activity.install", self.install_activity)
            self.session.coordinator.register_capability("live.activation", self.activation_gate)
            self.session.coordinator.register_capability("web.context_recovery.epoch", lambda: self.stop_revision)
            self.session.coordinator.register_capability("live.park", self.park)
            from amplifier_web.host.naming import LiveSessionNaming
            completed=[identity for event in config.get('generations',[]) if event.get('event')=='generation.finished' for identity in event.get('input_ids',[]) if identity in {t['id'] for t in config.get('execution',{}).get('turns',[])}]
            self.naming=LiveSessionNaming(self.session.coordinator,self.home,publish,completed)
            from amplifier_web.loaded_provenance import capture_mount
            self.provenance_mount = capture_mount(self.session)
            with startup_timing.phase('worker.ownership.register'):
                await self.ownership.register()
            self.capture_manager_failures()
            self.execution = asyncio.create_task(self.session.execute(""))
            self.execution.add_done_callback(self.executed)
            self.parked_history_stamp = self.history_stamp()
            self.parked_config_stamp = configuration_stamp(
                workspace, config["id"], self.home, self.shared_store_stamp,
                extra_paths=self.config_inputs)
            report["tools"] = list(self.session.coordinator.get("tools"))
            # Keep absolute module sources and full mount plans on disk. The UI
            # gets a compact capability report, not credentials or config blobs.
            public = {key: report.get(key) for key in ("bundle", "root_bundle", "workspace", "session_id", "resumed", "providers",
                "tools", "agents", "provider_choices", "selection", "effective_selection", "delegationRouting", "steering", "capabilities", "fork_context_messages", "standalone", "settings_file")}
            if os.name == "nt":
                public["capabilities"] = {**(public.get("capabilities") or {}),
                    "session.handoff": False, "session.transfer_fence": False,
                    "session.authoritative_checkpoint": False}
            if replacement:
                await self.commit_replacement(replacement,
                    deferred=config.get('bundleReplacement', {}).get('deferred'), stop_revision=stop_revision)
                replacement = None
            from amplifier_web.support_diagnostics import runtime_versions
            with startup_timing.phase('worker.support'):
                public["support"] = runtime_versions()
            public.update(self.preparation_report)
            with startup_timing.phase('worker.ready'):
                publish({"type": "runtime.ready", "report": public,
                         "workerIdentity": {"pid": os.getpid(), "sessionId": self.session.session_id}})
            outcome = "completed"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            diagnostic = startup_capture.save(exc, config.get("id")) if startup_capture else None
            if raise_errors:
                if diagnostic:
                    exc.diagnostic_path = diagnostic
                raise
            error = {"type": "runtime.error", "error": f"{type(exc).__name__}: {exc}"}
            if diagnostic:
                error["diagnosticReceipt"] = diagnostic.name
            from amplifier_web.module_failures import ConfiguredModuleError
            if isinstance(exc, ConfiguredModuleError):
                error.update(code="module_load_failed", moduleFailures=exc.failures)
            if type(exc).__name__ == "SessionBusyError":
                error.update(code="session_busy", owner=getattr(exc, "owner", None))
            publish(error)
            self.shutdown.set()
        finally:
            try:
                if replacement:
                    # Stop partially mounted execution before restoring exact files.
                    try:
                        if self.execution and not self.execution.done():
                            self.execution.cancel()
                            await asyncio.gather(self.execution, return_exceptions=True)
                        if self.controls:
                            await self.controls.close()
                        if self.session:
                            await self.session.cleanup()
                    finally:
                        self.session = self.controls = self.naming = self.execution = None
                        replacement.restore()
                if startup_capture:
                    startup_capture.close()
                if progress:
                    progress.cancel()
                    await asyncio.gather(progress, return_exceptions=True)
            except BaseException as exc:
                outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                raise
            finally:
                if startup_timing and report_directory is not None:
                    startup_timing.save(report_directory, outcome)

    def defer_park_for_naming(self, activation):
        """Keep the writer until naming settles, without holding the live inbox."""
        if self.naming_park_task and not self.naming_park_task.done():
            return
        session, naming = self.session, self.naming
        pending = naming.pending
        async def settled():
            try:
                # Worker shutdown owns naming cleanup. Cancelling this waiter
                # must not cancel or replay an already admitted naming request.
                await asyncio.gather(asyncio.shield(pending), return_exceptions=True)
            finally:
                if self.naming_park_task is asyncio.current_task():
                    self.naming_park_task = None
            if (not self.shutdown.is_set() and self.session is session
                    and self.naming is naming and self.activation is activation):
                # Recheck foreground admission under command_lock. Naming may
                # also have scheduled a newer naming task while this one ended.
                await self.park(activation=activation)
        task = self.naming_park_task = asyncio.create_task(settled())
        self.tasks.add(task)
        def finished(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                self.executed(task)
        task.add_done_callback(finished)

    async def park(self, *, activation=None):
        """Checkpoint then relinquish only a settled manager activation."""

        async with self.command_lock:
            if self.ownership.yielding or self.parked or self.shared_handle is None:
                return
            loop = self.session.coordinator.get("orchestrator")
            if (self.approvals or self.bridges or self.operation_ids or self.operation_controls or self.runtime.queued_inputs
                    or not self.runtime.inbox.empty() or self.runtime.generation
                    or (loop and (loop.pending or loop._active_jobs()))):
                return
            activation = activation or self.activation
            self.activation_gate.check(activation)
            if self.naming and self.naming.pending and not self.naming.pending.done():
                self.defer_park_for_naming(activation)
                return
            token = self.activation_gate.bind(activation)
            try:
                checkpoint = self.session.coordinator.get_capability("live.checkpoint")
                if checkpoint:
                    await checkpoint(self.root_generation_outcome or "completed")
                    from amplifier_web.handoff_boundary import checkpoint_configuration
                    checkpoint_configuration(self)
                self.parked_history_stamp = self.history_stamp()
                from amplifier_web.shared_state import configuration_stamp
                self.parked_config_stamp = configuration_stamp(
                    self.workspace, self.runtime.session_id, self.home, self.shared_store_stamp,
                    extra_paths=self.config_inputs)
                self.activation_gate.release(activation)
                await asyncio.to_thread(self.shared_handle.release)
                self.shared_handle = None
                self.parked = True
            finally:
                self.activation_gate.reset(token)
        publish({"type": "runtime.parked", "session_id": self.runtime.session_id})

    async def checkpoint_terminal_outcome(self):
        """Persist only an observed failed/detached root turn before teardown."""
        outcome = self.root_generation_outcome
        if (self.terminal_checkpointed or outcome not in {"error", "interrupted"}
                or self.ownership.yielding or self.parked
                or self.shared_handle is None or self.session is None):
            return
        async with self.command_lock:
            outcome = self.root_generation_outcome
            if (self.terminal_checkpointed or outcome not in {"error", "interrupted"}
                    or self.ownership.yielding or self.parked
                    or self.shared_handle is None or self.session is None):
                return
            checkpoint = self.session.coordinator.get_capability("live.checkpoint")
            if checkpoint is None:
                return
            try:
                token = self.activation_gate.bind(self.activation)
            except RuntimeError:
                # Ownership release/cancellation may already have invalidated the
                # write token; its dedicated release flow owns that checkpoint.
                return
            try:
                await checkpoint(outcome)
                self.terminal_checkpointed = True
            finally:
                self.activation_gate.reset(token)

    def history_stamp(self):
        """Cheap native invalidation; never parse events or consult checkpoints."""
        from amplifier_web.session_files import sessions_dir
        directory = sessions_dir(self.workspace) / self.runtime.session_id
        return tuple(self.shared_store_stamp(directory / name) for name in
                     ("transcript.jsonl", "transcript.jsonl.backup", "metadata.json", "metadata.json.backup"))

    async def acquire_for_mutation(self):
        """Acquire after a parked loop wakes, before it can accept an input."""

        if not self.parked:
            return
        handle = await asyncio.to_thread(
            self.shared_store.acquire, app="amplifier-unified", pid=os.getpid())
        try:
            history_stamp = self.history_stamp()
            from amplifier_web.shared_state import configuration_stamp
            config_stamp = configuration_stamp(
                self.workspace, self.runtime.session_id, self.home, self.shared_store_stamp,
                extra_paths=self.config_inputs)
        except BaseException:
            await asyncio.to_thread(handle.release)
            raise
        if (self.parked_history_stamp and any(self.parked_history_stamp[:2])
                and not any(history_stamp[:2])):
            await asyncio.to_thread(handle.release)
            raise RuntimeError(
                "The native session transcript and its backup disappeared while this runtime was "
                "parked. Its previous context will not be replaced with legacy history.")
        if history_stamp != self.parked_history_stamp or config_stamp != self.parked_config_stamp:
            self.shared_handle = handle
            self.parked = False
            await self.remount()
            return
        self.shared_handle = handle
        self.activation = self.activation_gate.activate()
        self.parked = False
        await self.ownership.register()
        publish({"type": "runtime.reactivated", "session_id": self.runtime.session_id,
                 "reused": True})

    async def remount(self):
        """Replace stale mounted state after lock+stamp validation."""

        self.remounting = True
        try:
            if self.execution and not self.execution.done():
                self.execution.cancel()
                await asyncio.gather(self.execution, return_exceptions=True)
            if self.controls:
                await self.controls.close()
            if self.session:
                await self.session.cleanup()
            self.session = self.controls = self.naming = self.execution = None
            try:
                await self.start(self.start_config, raise_errors=True)
            except Exception as exc:
                from amplifier_web.runtime import RuntimeStartupError
                failure = RuntimeStartupError('The conversation worker could not reload before accepting the message.')
                from amplifier_web.host.model_selection import ProviderSelectionError
                if isinstance(exc, ProviderSelectionError):
                    from amplifier_web.session_health import failure_details
                    detail = failure_details(exc)
                    failure.args = (f"ProviderSelectionError: {detail['summary']} {detail['guidance']}",)
                failure.diagnostic_path = getattr(exc, 'diagnostic_path', None)
                failure.failures = getattr(exc, 'failures', [])
                raise failure from exc
        finally:
            self.remounting = False

    def tool_invocation(self, controls, coordinator, name, tool, identity):
        """Bind an explicit control to its acquired owner, never a model actor."""
        runtime, session, gate = self.runtime, self.session, self.activation_gate
        handle, activation, epoch = self.shared_handle, self.activation, self.stop_revision
        generation = getattr(runtime, 'generation', None)
        inputs = copy.deepcopy((generation or {}).get('input_ids'))
        task = asyncio.current_task()
        root = session.coordinator
        def valid():
            children = root.get_capability('live.children')
            child = children.sessions.get(coordinator.session_id) if children else None
            return (self.controls is controls and controls.coordinator is root and session.coordinator is root
                and controls.runtime is runtime and self.runtime is runtime and self.session is session
                and (coordinator is root or child is not None and child.coordinator is coordinator)
                and asyncio.current_task() is task and self.activation_gate is gate
                and gate is not None and gate.current_valid
                and handle is not None and self.shared_handle is handle and self.activation is activation
                and not self.parked and not self.ownership.yielding and not self.remounting
                and self.stop_revision == epoch and runtime.generation is generation
                and (generation or {}).get('input_ids') == inputs
                and not runtime.closed and not runtime.stop_requested
                and not coordinator.cancellation.is_cancelled
                and (coordinator.get('tools') or {}).get(name) is tool)
        @contextmanager
        def bind(arguments):
            if not valid() or task in self.control_invocations:
                raise ValueError('The tool control execution owner changed before invocation.')
            scope = {'valid': valid, 'coordinator': coordinator, 'name': name,
                     'arguments': copy.deepcopy(arguments), 'callId': identity}
            self.control_invocations[task] = scope
            try:
                yield
            finally:
                self.control_invocations.pop(task, None)
        return bind

    def bind_activation(self):
        """Bind the current capability to a command task, never to a global writer."""

        if self.parked or self.shared_handle is None:
            raise RuntimeError("The session is parked and has no active shared writer.")
        return self.activation_gate.bind(self.activation)

    def capture_manager_failures(self):
        """Observe the real loop exception before Core converts it to text.

        Do not change the loop's catch, classification, cancellation or return.
        A later generic execution exit still owns shutdown as before.
        """
        from functools import wraps
        loop = self.session.coordinator.get("orchestrator")
        original = getattr(loop, "execute", None)
        if not callable(original) or getattr(original, "_web_failure_capture", False):
            return
        @wraps(original)
        async def execute(*args, **kwargs):
            epoch = self.stop_revision
            runtime = self.runtime
            previous_failure = self.failed_generation
            try:
                return await original(*args, **kwargs)
            except Exception as error:
                try:
                    from amplifier_web.support_diagnostics import exception_chain, terminal_exception_capture
                    terminal = self.failed_generation or {}
                    generation = getattr(runtime, 'generation', None)
                    # Cleanup can replace the escaping wrapper. Only inspect the
                    # bounded actual exception chain, never flattened messages.
                    primary, secondary, seen = error, [], set()
                    while (isinstance(primary, Exception) and id(primary) not in seen
                           and len(secondary) < 4):
                        seen.add(id(primary))
                        if (type(primary).__module__ == 'amplifier_module_loop_live.failures'
                                and type(primary).__name__ == 'ManagerTurnError'):
                            break
                        secondary.append(exception_chain(primary)[0])
                        primary = primary.__cause__ or primary.__context__
                    if (type(primary).__module__ == 'amplifier_module_loop_live.failures'
                            and type(primary).__name__ == 'ManagerTurnError'
                            and isinstance(getattr(primary, 'failure', None), dict)
                            and all(primary.failure.get(key) == terminal.get(key) for key in
                                    ('error_type', 'error_category', 'error_stage'))
                            and terminal is not previous_failure
                            and terminal.get('error_category') == 'unknown'
                            and terminal.get('error_stage') == 'manager_turn'
                            and primary.__cause__ is not None and self.stop_revision == epoch
                            and self.runtime is runtime and not runtime.stop_requested
                            and terminal.get('_runtime') is runtime and terminal.get('_stopRevision') == epoch
                            and terminal['_generation'].get('id') == terminal.get('generation_id')
                            and terminal['_generation'].get('input_ids') == terminal.get('input_ids')
                            # The loop clears its generation after the terminal
                            # observation. A replacement is never this failure.
                            and (generation is None or generation is terminal.get('_generation'))
                            and not self.remounting and not self.ownership.yielding):
                        capture = terminal_exception_capture({
                            'generationId': terminal.get('generation_id'), 'inputIds': terminal.get('input_ids'),
                            'errorType': terminal.get('error_type'), 'exceptionChain': exception_chain(primary),
                            **({'secondaryFailure': {'stage': 'loop_exit', 'exceptionChain': secondary}}
                               if secondary else {})})
                        if capture:
                            publish({'type': 'runtime.error', 'error': 'The manager turn failed. Work was not replayed.',
                                     'terminalFailure': capture})
                except Exception:
                    # Observation is optional: closed transport or unsupported
                    # metadata must not replace the original loop exception.
                    pass
                raise
        execute._web_failure_capture = True
        try:
            loop.execute = execute
        except (AttributeError, TypeError):
            # Unsupported immutable plugins keep their original execution.
            # Absence of this optional observation is not a diagnosed cause.
            return

    def executed(self, task):
        if self.remounting or self.ownership.yielding:
            return
        if not task.cancelled():
            error = task.exception()
            if error:
                publish({"type": "runtime.error", "error": f"{type(error).__name__}: {error}. Work was not replayed.", "errorType": type(error).__name__})
        self.shutdown.set()

    async def command(self, data):
        """Serialize admission with parking and bind a per-work write token."""

        op = data.get("op")
        if op in {"approval", "coordination.approval"}:
            # A control can await a human decision while owning command_lock.
            # Both the independent question and its UI answer must bypass it;
            # neither reacquires a writer, remounts, or binds an activation.
            await self._command_serial(data)
            return
        if op == "stop":
            self.stop_revision += 1
        memory_control = op == 'control' and data.get('operation') == 'memory.consolidate'
        naming_control = op == 'control' and data.get('operation') == 'session.naming'
        steering_control = op == 'control' and data.get('operation') in {'coordination.steer', 'conversation.steer'}
        if op in {'send', 'retry', 'stop', 'resume', 'worker.message', 'worker.steer', 'worker.stop'}:
            # Auxiliary personalization must never delay foreground admission.
            # Cancellation cannot retract an already accepted provider request;
            # its source attempt is recorded interrupted and is not replayed.
            if self.memory_task and not self.memory_task.done():
                self.memory_task.cancel()
        if op == "park":
            await self.park()
            publish({"op": "reply", "id": data.get("id"), "result": {"parked": self.parked}})
            return
        if op == "retire":
            async with self.command_lock:
                loop = self.session.coordinator.get("orchestrator") if self.session else None
                settled = bool(self.parked and not self.ownership.yielding
                    and not self.approvals and not self.bridges and not self.operation_ids and not self.operation_controls and not self.remounting
                    and not self.runtime.queued_inputs and self.runtime.inbox.empty()
                    and not self.runtime.generation
                    and not (self.naming and self.naming.pending and not self.naming.pending.done())
                    and not (loop and (loop.pending or loop._active_jobs())))
                publish({"op": "reply", "id": data.get("id"), "result": {"retired": settled}})
                if settled:
                    self.shutdown.set()
            return
        if op not in {"send", "retry", "resume", "control", "worker.steer", "worker.stop", "worker.message", "approval"}:
            await self._command_serial(data)
            return
        try:
            async with self.command_lock:
                if self.ownership.yielding:
                    from amplifier_foundation.session import SessionBusyError
                    raise SessionBusyError(self.shared_handle.owner if self.shared_handle else None)
                # A memory call can finish admission while a foreground command
                # waits for this lock, so also cancel after acquiring it.
                if op in {'send', 'retry', 'resume', 'worker.message', 'worker.steer', 'worker.stop'} and self.memory_task and not self.memory_task.done():
                    self.memory_task.cancel()
                if steering_control and (self.parked or self.shared_handle is None):
                    # A parked owner has no live generation to steer. Do not
                    # reacquire/remount it just to discover that stale anchor.
                    publish({"op": "reply", "id": data.get("id"), "result": {
                        "accepted": False, "supported": False, "effect": "none",
                        "reason": "No active native owner is available for anchored steering."}})
                    return
                placement_control = op == 'control' and data.get('operation') == 'configuration.patchPlacement.bind'
                if placement_control:
                    # A parked worker may remount while acquiring its writer.
                    # Preserve controls during that preparation too, not only
                    # after acquisition or the explicit replacement startup.
                    if self.parked:
                        if not isinstance(self.start_config, dict):
                            raise ValueError('The parked placement owner has no verified startup binding.')
                        self.start_config['_preserveConfiguration'] = True
                    if self.controls:
                        self.controls.preserve_configuration = True
                try:
                    await self.acquire_for_mutation()
                finally:
                    if isinstance(self.start_config, dict):
                        self.start_config.pop('_preserveConfiguration', None)
                if self.controls:
                    # Placement controls and their settled checkpoint preserve
                    # unrelated saved controls. The next ordinary mutation
                    # resumes normal persistence; observations never change it.
                    self.controls.preserve_configuration = placement_control
                token = self.bind_activation()
                detached_cancel = memory_control or naming_control or op == "control" and (data.get("operation", "").startswith(("operations.", "kernels.")))
                if detached_cancel:
                    self.operation_controls += 1
                    if memory_control:
                        self.memory_task = asyncio.current_task()
                else:
                    try:
                        await self._command_serial(data)
                    finally:
                        self.activation_gate.reset(token)
            if detached_cancel:
                try:
                    await self._command_serial(data)
                finally:
                    self.operation_controls -= 1
                    self.activation_gate.reset(token)
                    if memory_control and self.memory_task is asyncio.current_task():
                        self.memory_task = None
            if op in {"control", "retry"}:
                # A control-only action or duplicate retry does not wake the inbox.
                # It must therefore schedule its own settled release.
                await self.park(activation=self.activation)
        except asyncio.CancelledError:
            if not memory_control:
                raise
            publish({'op':'reply', 'id':data.get('id'), 'result':{'interrupted':True}})
        except Exception as exc:
            reply = {"op": "reply", "id": data.get("id"),
                     "error": f"{type(exc).__name__}: {exc}"}
            from amplifier_web.runtime import RuntimeStartupError
            preparation_failed = isinstance(exc, RuntimeStartupError)
            if preparation_failed:
                from amplifier_web.worker_diagnostics import diagnostic_reference
                reply.update(code='worker_preparation_failed',
                    moduleFailures=getattr(exc, 'failures', []), **diagnostic_reference(exc))
            if type(exc).__name__ == "SessionBusyError":
                reply["code"] = "session_busy"
                reply["owner"] = getattr(exc, "owner", None)
            publish(reply)
            if preparation_failed:
                self.shutdown.set()
        finally:
            if memory_control and self.memory_task is asyncio.current_task():
                self.memory_task = None

    async def resolve_approval(self, data):
        identity = data['approval_id']
        future, options = self.approvals[identity]
        decision = data['decision']
        if future.done() or decision not in options:
            raise ValueError('Approval expired or decision is not offered')
        comment = data.get('comment', '')
        if not isinstance(comment, str) or len(comment) > 4000:
            raise ValueError('Permission guidance must be at most 4000 characters.')
        comment = comment.strip()
        contents = (decision, comment)
        existing = self.approval_deliveries.get(future)
        if existing:
            if existing[0] != contents:
                raise ValueError('A different decision is already being delivered for this approval')
            return await asyncio.shield(existing[1])

        context = self.approval_contexts.get(identity)
        if context is None and self.session:
            context = self.session.coordinator.get('context')
        if comment and context is None:
            raise ValueError('Permission guidance could not reach the active conversation.')
        stop_revision = self.stop_revision

        async def deliver():
            def current():
                return (self.approvals.get(identity, (None,))[0] is future
                        and not future.done() and self.stop_revision == stop_revision)
            try:
                if not current():
                    raise ValueError('Approval expired before guidance delivery')
                if comment:
                    await context.add_message({'role': 'user',
                        'content': f'Permission {decision}:\n\n{comment}',
                        'metadata': {'approval_id': identity, 'source': 'approval_feedback'}})
                if not current():
                    raise ValueError('Approval expired during guidance delivery')
                from amplifier_web.host.approvals import ApprovalDecision
                future.set_result(ApprovalDecision(decision, comment or None))
                return {'accepted': True, 'commentDelivered': bool(comment)}
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except Exception:
                # A context append may have taken effect before failing. End
                # this wait without approving or replaying the uncertain append.
                if not future.done():
                    future.set_exception(RuntimeError('Permission guidance delivery failed; no approval was applied.'))
                raise

        # Approval replies cannot take command_lock: the paused tool can own it.
        # Share one delivery across retries, shielding it from a lost reply.
        task = asyncio.create_task(deliver())
        self.approval_deliveries[future] = (contents, task)
        self.tasks.add(task)
        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        def retired(done):
            self.approval_deliveries.pop(done, None)
            if not task.done():
                task.cancel()
        future.add_done_callback(retired)
        return await asyncio.shield(task)

    def bind_input_context(self, identity, data, binding=None):
        """Keep original host admission metadata beside the input's context."""
        if identity in self.runtime.accepted:
            return
        self.context_bindings[identity] = {
            **(binding or {'clientId': None, 'targets': []}),
            'interruptionRevision': data.get('interruptionRevision', 0)}
        self.context_bindings = dict(list(self.context_bindings.items())[-64:])

    @contextmanager
    def write_policy_boundary(self):
        """Bind an existing writer for policy-only work; never acquire one."""
        if (self.parked or self.remounting or self.shared_handle is None
                or self.ownership.yielding or self.activation_gate is None):
            raise ValueError('The originating runtime is not active.')
        token = self.activation_gate.bind(self.activation)
        try:
            yield
        finally:
            self.activation_gate.reset(token)

    async def _command_serial(self, data):
        identity = data.get("id")
        try:
            op = data.get("op")
            if op == "bridge.result":
                from amplifier_web.handoff_drain import acknowledged_receipt
                receipt = acknowledged_receipt(self, data)
                # Ownership precedes set_result/set_exception: the caller can
                # be cancelled before it resumes to process this exact reply.
                acknowledgement = self._own_handoff_ack(receipt) if receipt is not None else None
                future = self.bridges.get(identity)
                if future and not future.done():
                    if acknowledgement is not None:
                        future._handoff_ack = acknowledgement
                    timing = getattr(self, 'bridge_timings', {}).get(identity)
                    if timing:
                        timing.mark('received')
                    if data.get("error"):
                        error = RuntimeError(data["error"])
                        if isinstance(data.get('_handoffDisposition'), dict):
                            error._handoff_disposition = data['_handoffDisposition']
                        future.set_exception(error)
                    else:
                        future.set_result(data.get("result"))
                elif acknowledgement is not None:
                    try:
                        await asyncio.shield(acknowledgement)
                    except Exception:
                        pass  # The retained owner reports failure and keeps uncertainty.
                return
            if op == "resume":
                if not self.session or not self.execution or self.execution.done():
                    raise RuntimeError('The session has not finished loading successfully.')
                result = {"accepted": True}
            elif op == "approval":
                result = await self.resolve_approval(data)
            elif op == "coordination.approval":
                prompt = data.get("prompt")
                decision = None
                if isinstance(prompt, str) and prompt.strip():
                    try:
                        decision = await asyncio.wait_for(self.ask(prompt, ["allow", "deny"],
                            **({"identity": data["approval_id"]} if data.get("approval_id") else {})), 50)
                    except TimeoutError:
                        pass
                result = {"allowed": decision == "allow", **({"pending": True} if decision in {None, "expired"} else {})}
            elif op == "start":
                if self.start_task:
                    raise RuntimeError("Already started")
                self.start_task = asyncio.create_task(self.start(data["session"]))
                return
            elif op == "stop":
                if self.runtime and not self.runtime.closed:
                    from amplifier_module_loop_live.runtime import Input
                    await self.runtime.submit(Input("stop"))
                else:
                    self.shutdown.set()
                return
            elif op == "dependencies":
                if 'patchPlacement' in data:
                    from amplifier_web.patch_placement import engine_observation
                    try:
                        result = (engine_observation(self.controls, data['patchPlacement']['moduleId'])
                            if self.controls else {'status': 'unobserved', 'workingDirectory': None})
                    except Exception:
                        # Passive and secret-free: no arbitrary module exception
                        # text, writer acquisition, checkpoint or remount.
                        result = {'status': 'unknown', 'workingDirectory': None}
                elif data.get('resourceAccess') is True:
                    from amplifier_web.resource_access import observe
                    inspected_session = self.session
                    if inspected_session and inspected_session.coordinator.get_capability('web.write_policy'):
                        try:
                            with self.write_policy_boundary():
                                result = await observe(inspected_session, data.get('paths', []))
                        except (ValueError, RuntimeError):
                            result = {'status': 'unknown', 'paths': {}}
                    else:
                        result = await observe(inspected_session, data.get('paths', []))
                    if self.session is not inspected_session:
                        result = {'status': 'unknown', 'paths': {}}
                elif data.get('integrationValidation') is True:
                    result = {'integrationValidation': 'admission-v1' if self.controls is not None else None}
                elif data.get('provenance') is True:
                    from amplifier_web.loaded_provenance import observe
                    inspected_session = self.session
                    result = await observe(inspected_session, data.get('modules', []), mounted=getattr(self, 'provenance_mount', None))
                    if self.session is not inspected_session:
                        result.update(status='unknown', identityStable=False)
                elif __package__:
                    from .artifact_runtime import discover
                else:
                    from artifact_runtime import discover
                if 'patchPlacement' not in data and data.get('provenance') is not True and data.get('resourceAccess') is not True and data.get('integrationValidation') is not True:
                    result = await discover('worker', **({'verify': True} if data.get('verifyImports') is True else {}))
            elif op == 'file_access.refresh':
                # No input, activation, unpark, remount or shared tool mutation.
                # The synchronous owner check and snapshot publication cannot
                # interleave with Stop or a concurrently preparing child.
                policy = self.session.coordinator.get_capability('web.write_policy') if self.session else None
                if policy is None:
                    raise ValueError('The live write-policy boundary is unavailable.')
                with self.write_policy_boundary():
                    result = policy.confirm(data['settingsRevision'])
            elif op == 'handoff.boundary':
                from amplifier_web.handoff_boundary import inspect
                from amplifier_web.handoff_drain import finish
                if not data.get('cancel'):
                    await finish(self, data.get('receipt'))
                result = await inspect(self, data['request_id'], data.get('cancel', False),
                    update=data.get('update') is True, configuration=data.get('configuration') is True,
                    expected=(data.get('receipt') or {}).get(
                        'configurationWait' if data.get('configuration') else 'updateWait'))
            elif op == "desktop.readiness":
                if not self.controls:
                    result = {"status": "unavailable", "reason": "Conversation tools are not mounted."}
                else:
                    from amplifier_web.desktop_readiness import worker_report
                    result = worker_report(self.controls)
            elif not self.session or not self.execution:
                raise RuntimeError("Session is not ready")
            elif op in {"delivery", "retry"} and (
                data['input_id'] in self.runtime.accepted or
                contains_input(
                    await self.session.coordinator.get('context').get_messages(), data['input_id'])
            ):
                result = {"accepted": True, "delivery": "accepted", "duplicate": True}
            elif op == "delivery":
                result = {"delivery": "unknown"}
            elif op in {"send", "retry"}:
                from amplifier_module_loop_live.runtime import Input
                from amplifier_web.message_interactions import prepare_input
                text = await prepare_input(self.session.coordinator, data['text'], data.get('reply_context'), max_chars=self.runtime.max_input_chars)
                self.bind_input_context(data['input_id'], data, data.get('context_binding'))
                input_id = await self.runtime.submit(Input(
                    "user", text, id=data["input_id"],
                    attachments=tuple(data.get("attachments", [])),
                    activation=self.activation))
                result = {"accepted": True, "inputId": input_id}
            elif op == "control":
                arguments = data.get("arguments", {})
                if data['operation'] in {'conversation.steer', 'schedule.submit', 'observation.submit',
                                         'coordination.submit', 'coordination.steer', 'coordination.deliver', 'history.edit'}:
                    input_id = arguments['operationId'] if data['operation'] == 'history.edit' else arguments['inputId']
                    self.bind_input_context(input_id, data, arguments.get('context_binding'))
                if data['operation'] in {'configuration.apply', 'configuration.patchPlacement.bind'}:
                    from amplifier_web.configuration_recovery import native_apply
                    # This envelope is supplied only by the host's retained
                    # operation. The public raw route strips all but config.
                    attempt = arguments.get('_configurationAttempt')
                    if not isinstance(attempt, dict):
                        raise ValueError('Configuration apply requires its original host operation binding.')
                    async with self.controls.lock:
                        result = await native_apply(self.controls, arguments.get('config'), attempt,
                            lambda value: self.bridge('configuration.effect.admit', value),
                            lambda: self.stop_revision, operation=data['operation'])
                elif data['operation'] == 'conversation.steer':
                    from amplifier_web.conversation_steering import submit
                    result = await submit(self.controls, self.runtime, arguments, self.activation,
                                          stop_epoch=lambda: self.stop_revision)
                elif data["operation"] == "schedule.submit":
                    from amplifier_web.scheduled_input import admit
                    result = await admit(self.controls, self.runtime, arguments, self.activation)
                elif data["operation"] == "observation.submit":
                    from amplifier_web.observation_input import admit
                    result = await admit(self.controls, self.runtime, arguments, self.activation,
                        authorize=lambda value: self.bridge("observation.admit", value))
                elif data["operation"] == "coordination.submit":
                    from amplifier_web.collaboration_input import admit
                    result = await admit(self.controls, self.runtime, arguments, self.activation,
                        authorize=lambda value: self.bridge("coordination.admit", value),
                        stop_epoch=lambda: self.stop_revision)
                elif data["operation"] == "coordination.deliver":
                    from amplifier_web.collaboration_input import deliver
                    result = await deliver(self.controls, self.runtime, arguments, self.activation,
                        authorize=lambda value: self.bridge("coordination.admit", value),
                        stop_epoch=lambda: self.stop_revision)
                elif data["operation"] == "coordination.steer":
                    from amplifier_web.collaboration_input import steer
                    result = await steer(self.controls, self.runtime, arguments, self.activation,
                        authorize=lambda value: self.bridge("coordination.admit", value),
                        stop_epoch=lambda: self.stop_revision)
                elif data["operation"] == "session.naming":
                    if not self.naming:
                        raise ValueError('Automatic naming is unavailable for this conversation.')
                    result = await self.naming.suggest()
                elif data["operation"] == "bundle.preview":
                    from amplifier_web.bundle_selection import preview
                    self.controls.require_idle()
                    self.bundle_preview = {**await asyncio.wait_for(preview(self.controls, self.workspace, arguments['bundle']), 150),
                                           'previewId': str(uuid.uuid4())}
                    result = self.bundle_preview
                elif data["operation"] == "history.edit":
                    if self.approvals or self.bridges or not self.runtime.inbox.empty():
                        raise ValueError('Finish pending interactions before editing history.')
                    from amplifier_web.history_revision import rewind
                    from amplifier_web.message_interactions import prepare_input
                    text = await prepare_input(self.session.coordinator, arguments['text'], arguments.get('reply_context'), max_chars=self.runtime.max_input_chars)
                    result = await rewind(self.controls, arguments)
                    publish({'type': 'history.revised', **result})
                    from amplifier_module_loop_live.runtime import Input
                    await self.runtime.submit(Input('user', text, id=arguments['operationId'],
                        attachments=tuple(arguments.get('attachments', [])), activation=self.activation))
                elif data["operation"] == "bundle.prepare":
                    from amplifier_web.bundle_selection import prepare_switch
                    result = await prepare_switch(self, arguments)
                else:
                    result = await self.controls.perform(data["operation"], arguments)
            elif op in {"worker.steer", "worker.stop", "worker.message"}:
                children = self.session.coordinator.get_capability("live.children")
                wid = data["worker_id"]
                if children and wid in children.rows:
                    result = await children.control(wid, "steer" if op == "worker.steer" else "message" if op == "worker.message" else "cancel", data.get("text", ""), input_id=data.get("input_id"))
                elif op == "worker.stop":
                    from amplifier_module_loop_live.runtime import Input
                    loop = self.session.coordinator.get("orchestrator")
                    job = loop.jobs.get(wid)
                    if job is None and children and wid in children.rows:
                        call = children.rows[wid].get("callId")
                        wid = next((k for k,v in loop.jobs.items() if v.get("call_id") == call), None)
                        job = loop.jobs.get(wid)
                    if not job or job["task"].done():
                        raise ValueError("Worker is no longer active")
                    await self.runtime.submit(Input("cancel_job", target=wid, activation=self.activation))
                    result = {"accepted": True, "completed": False}
                else:
                    raise ValueError("This finite worker cannot receive messages; ask the main session to revise its work")
            else:
                raise ValueError("Unknown runtime operation")
            if identity:
                publish({"op": "reply", "id": identity, "result": result})
        except Exception as exc:
            publish({"op": "reply", "id": identity, "error": f"{type(exc).__name__}: {exc}"})

    async def read(self):
        transport = None
        threaded_reader = None
        try:
            if sys.platform == "win32":
                # Child stdin is an ordinary synchronous pipe, not an overlapped
                # handle that the Windows Proactor loop can connect_read_pipe.
                if __package__:
                    from .worker_input import ThreadedPipeReader
                else:
                    from worker_input import ThreadedPipeReader
                reader = threaded_reader = ThreadedPipeReader.for_worker(sys.stdin, MAX_MESSAGE_BYTES)
            else:
                reader = asyncio.StreamReader(limit=MAX_MESSAGE_BYTES)
                protocol = asyncio.StreamReaderProtocol(reader)
                transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
            while line := await reader.readline():
                if len(line) > MAX_MESSAGE_BYTES:
                    raise ValueError("Runtime message exceeds the transport limit.")
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                except ValueError:
                    continue
                task = asyncio.create_task(self.command(data))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            publish({'type':'runtime.error','error':f'Amplifier worker communication failed ({type(exc).__name__}). A message could not be read; work was not replayed.'})
        finally:
            if threaded_reader is not None:
                threaded_reader.close()
            if transport is not None:
                transport.close()
            self.shutdown.set()

    async def run(self):
        if __package__:
            from .runtime_preparation import preparation_scope
        else:
            from runtime_preparation import preparation_scope
        # Bind before creating any worker task and retain through joined cleanup.
        # Foundation captures this policy for later lazy module activation.
        with preparation_scope() as self.preparation_report:
            await self._run()

    async def _run(self):
        # The host verifies membership/creation time before sending even start.
        # This carries no readiness or permission claim.
        publish_process_identity()
        read = asyncio.create_task(self.read())
        try:
            await self.shutdown.wait()
        finally:
            for task in [read, self.start_task, self.execution, *self.tasks]:
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in [read, self.start_task, self.execution, *self.tasks] if t), return_exceptions=True)
            try:
                await self.checkpoint_terminal_outcome()
            finally:
                if self.ownership.registration:
                    await self.ownership.registration.close()
                if self.controls:
                    from amplifier_web.observation_input import finish
                    finish(self.controls, {'type': 'generation.detached'})
                    await self.controls.close()
                if self.session:
                    await self.session.cleanup()
                if self.shared_handle is not None:
                    try:
                        await asyncio.to_thread(self.shared_handle.release)
                    finally:
                        self.shared_handle = None


if __name__ == "__main__":
    configure_protocol()
    asyncio.run(Worker().run())
