"""Bounded process-local timings, never conversation content or persisted state.

Operation timings are inclusive and can overlap. Event-loop delay includes host
suspension and CPU scheduling; it is evidence of delay, not a cause attribution.
Stack sampling remains the separate opt-in profiling facility.
"""
import asyncio
from collections import deque
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
from functools import lru_cache
import hashlib
import inspect
import os
from pathlib import Path
import re
import threading
import time
import uuid


# Observation only. Neither context participates in Work or permission decisions.
TOOL_TIMING = ContextVar('unified_tool_timing', default=None)
BRIDGE_TIMING = ContextVar('unified_bridge_timing', default=None)
# Explicit opt-in, inherited at startup by both host and worker. Ordinary work
# must not pay for extra telemetry frames before overhead is qualified there.
TIMING_ENABLED = os.environ.get('AMPLIFIER_RUNTIME_TIMING') == '1'
TIMING_MARKS = frozenset({
    'start', 'origin.start', 'origin.end', 'check.start', 'check.end',
    'body.start', 'body.end', 'end', 'write.start', 'write.end', 'received',
    'resumed', 'dequeued', 'handler.start', 'handler.end', 'reply.start',
    'reply.end', 'lock.start', 'lock.end', 'checkpoint.start', 'checkpoint.end',
    'history.refresh.start', 'history.refresh.end', 'history.copy.start',
    'history.copy.end', 'history.read.start', 'history.read.end',
    'context.start', 'context.end', 'store.start', 'store.end',
    'continuity.start', 'continuity.end', 'host.observed',
})
PREPARATION_PHASES = frozenset({'provider.hooks', 'context.view', 'context.factory',
                               'surface.prepare', 'provider.budget', 'provider.refit'})


@lru_cache(maxsize=1)
def clock_domain():
    """Comparable only on a verified common Linux boot AND time namespace.

    Failure means unknown, not a guessed clock offset. Wall time is never used
    for phase subtraction. Hashes avoid exporting host/namespace identifiers.
    """
    try:
        if time.get_clock_info('monotonic').implementation != 'clock_gettime(CLOCK_MONOTONIC)':
            return None
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        namespace = os.readlink('/proc/self/ns/time')
        offsets = Path('/proc/self/timens_offsets').read_text()
        return hashlib.sha256(f'{boot}\n{namespace}\n{offsets}'.encode()).hexdigest()
    except (OSError, ValueError):
        return None


def timing_identity(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', value) else None


def timing_metadata(value):
    """Bounded, detached wire facts. They are never authority or tool results."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ('invocationId', 'toolCallId', 'runtimeSessionId', 'generationId', 'bridgeId', 'operation'):
        item = timing_identity(value.get(key))
        if item is not None:
            result[key] = item
    if type(value.get('kind')) is str and value['kind'] in {'tool', 'bridge', 'host.bridge', 'checkpoint', 'preparation'}:
        result['kind'] = value['kind']
    if result.get('kind') == 'preparation' and result.get('operation') not in PREPARATION_PHASES:
        result.pop('operation', None)
    if result.get('kind') == 'preparation' and type(value.get('iteration')) is int and 0 <= value['iteration'] < 2**31:
        result['iteration'] = value['iteration']
    if type(value.get('outcome')) is str and value['outcome'] in {'completed', 'failed', 'cancelled', 'denied'}:
        result['outcome'] = value['outcome']
    domain = value.get('clockDomain')
    if isinstance(domain, str) and re.fullmatch('[a-f0-9]{64}', domain):
        result['clockDomain'] = domain
    marks = value.get('marks')
    if isinstance(marks, dict):
        result['marks'] = {key: item for key, item in marks.items()
                           if key in TIMING_MARKS and type(item) is int and 0 <= item < 2**63}
    return result


class PhaseTiming:
    def __init__(self, **facts):
        self.facts = timing_metadata(facts)
        self.facts.pop('clockDomain', None)
        domain = clock_domain()
        if domain:
            self.facts['clockDomain'] = domain
        self.marks = {}
        self.outcome = 'completed'
        self.task = asyncio.current_task()
        self.active = True

    def mark(self, name):
        if name in TIMING_MARKS:
            self.marks[name] = time.monotonic_ns()

    def snapshot(self, outcome=None):
        return {**self.facts, 'marks': dict(self.marks), 'outcome': outcome or self.outcome}


def active_bridge():
    timing = BRIDGE_TIMING.get()
    return timing if timing and timing.active and timing.task is asyncio.current_task() else None


def active_tool(coordinator=None):
    scope = TOOL_TIMING.get()
    if (scope and scope['active'] and scope['task'] is asyncio.current_task()
            and (coordinator is None or scope['coordinator'] is coordinator)):
        # A context inherited by a nested/background task is not its call ID.
        dispatch = getattr(scope['coordinator'], '_tool_dispatch_contexts', {}).get(asyncio.current_task())
        if dispatch is scope['dispatch'] and dispatch.get('tool_call_id') == scope['call']:
            return scope['timing']
    return None


@contextmanager
def tool_scope(coordinator, name):
    """One invocation, including origin revalidation bridges, with no extra RPC."""
    emit = coordinator.get_capability('web.performance.emit')
    dispatch = getattr(coordinator, '_tool_dispatch_contexts', {}).get(asyncio.current_task())
    call = timing_identity(dispatch.get('tool_call_id')) if isinstance(dispatch, dict) else None
    timing = PhaseTiming(kind='tool', operation=name,
                         runtimeSessionId=coordinator.session_id,
                         **({'toolCallId': call} if call else {}),
                         invocationId=uuid.uuid4().hex) if TIMING_ENABLED and callable(emit) else None
    if timing:
        source = coordinator.get_capability('web.performance.scope')
        if callable(source):
            timing.facts.update({key: value for key, value in timing_metadata(source()).items()
                                 if key in {'runtimeSessionId', 'generationId'}})
    scope = {'timing': timing, 'task': asyncio.current_task(), 'coordinator': coordinator,
             'dispatch': dispatch, 'call': call, 'active': True}
    token = TOOL_TIMING.set(scope if timing and call else None)
    outcome = 'completed'
    try:
        if timing:
            timing.mark('start')
        yield timing
    except BaseException as exc:
        outcome = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
        raise
    finally:
        scope['active'] = False
        TOOL_TIMING.reset(token)
        if timing:
            timing.mark('end')
            try:
                emit(timing.snapshot(outcome if outcome != 'completed' else None))
            except Exception:
                pass  # Observability must not replace an outcome.


def mark(timing, name):
    if timing is not None:
        timing.mark(name)


def preparation_timing(coordinator, operation):
    """Existing opt-in transport, fixed phases only; never inspect request data."""
    if not TIMING_ENABLED or operation not in PREPARATION_PHASES:
        return None, None
    emit = coordinator.get_capability('web.performance.emit')
    if not callable(emit):
        return None, None
    timing = PhaseTiming(kind='preparation', operation=operation,
                         runtimeSessionId=coordinator.session_id,
                         invocationId=uuid.uuid4().hex)
    scope = coordinator.get_capability('web.performance.scope')
    try:
        if callable(scope):
            timing.facts.update({key: value for key, value in timing_metadata(scope()).items()
                                 if key in {'runtimeSessionId', 'generationId'}})
    except Exception:
        pass
    timing.mark('start')
    return timing, emit


def finish_preparation(timing, emit, outcome='completed'):
    if timing is not None:
        timing.mark('end')
        try:
            emit(timing.snapshot(outcome))
        except Exception:
            pass


def time_preparation(coordinator, operation, method):
    if not TIMING_ENABLED:
        return method

    @wraps(method)
    async def observed(*args, **kwargs):
        timing, emit = preparation_timing(coordinator, operation)
        outcome = 'completed'
        try:
            return await method(*args, **kwargs)
        except BaseException as exc:
            outcome = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
            raise
        finally:
            finish_preparation(timing, emit, outcome)
    return observed


def install_preparation_timing(coordinator):
    """Observe the existing Python context and native hook boundaries once.

    Arbitrary context implementations remain supported: only ordinary Python
    objects exposing the current request-view/factory boundary are wrapped.
    Native hook callbacks may run in different Python task contexts. Emit
    separate boundary observations, not an incorrectly paired elapsed span.
    A short-circuit has no end observation. Correlation requires matching
    session/generation/iteration and proof of one foreground request window.
    """
    if not TIMING_ENABLED or coordinator.get_capability('web.performance.preparation'):
        return
    coordinator.register_capability('web.performance.preparation', True)
    context = coordinator.get('context')
    if isinstance(getattr(context, '__dict__', None), dict):
        method = getattr(context, 'get_messages_for_request', None)
        if inspect.iscoroutinefunction(method):
            try:
                context.get_messages_for_request = time_preparation(coordinator, 'context.view', method)
            except (AttributeError, TypeError):
                pass  # A read-only/native context does not expose this probe.
        factory = vars(context).get('_system_prompt_factory')
        if inspect.iscoroutinefunction(factory):
            try:
                context._system_prompt_factory = time_preparation(coordinator, 'context.factory', factory)
            except (AttributeError, TypeError):
                pass
    from amplifier_core import HookResult
    def boundary(name, data):
        timing, emit = preparation_timing(coordinator, 'provider.hooks')
        if timing is not None:
            timing.marks.clear()
            timing.mark(name)
            iteration = data.get('iteration')
            if type(iteration) is int and 0 <= iteration < 2**31:
                timing.facts['iteration'] = iteration
            try:
                emit(timing.snapshot())
            except Exception:
                pass
    async def start(event, data):
        boundary('start', data)
        return HookResult()
    async def end(event, data):
        boundary('end', data)
        return HookResult()
    coordinator.hooks.register('provider:request', start, priority=-10000,
                               name='unified-preparation-start')
    coordinator.hooks.register('provider:request', end, priority=10000,
                               name='unified-preparation-end')


class PerformanceDiagnostics:
    def __init__(self):
        self.operations = {}
        self.counters = {}
        self.slow = deque(maxlen=60)
        self.lag = deque(maxlen=60)
        self.resource_rebuilds = deque(maxlen=60)
        self.projection_failures = deque(maxlen=16)
        self.shell_queries = deque(maxlen=60)
        self.runtime_timings = deque(maxlen=120)
        self.storage_collection = None
        self.resource_rebuild_counts = {'total': 0, 'byReason': {}, 'byResource': {}}
        self.lock = threading.Lock()
        self.task = None
        self.started = time.time()

    @contextmanager
    def measure(self, name, *, synchronous=False, session_id=None):
        started, wall = time.monotonic(), time.time()
        cpu = time.thread_time() if synchronous else None
        try:
            yield
        finally:
            elapsed = (time.monotonic() - started) * 1000
            row = {'operation': name, 'at': wall, 'elapsedMs': round(elapsed, 2)}
            if cpu is not None:
                row['threadCpuMs'] = round((time.thread_time() - cpu) * 1000, 2)
            if isinstance(session_id, str):
                row['sessionId'] = session_id[:200]
            with self.lock:
                if name in self.operations or len(self.operations) < 64:
                    summary = self.operations.setdefault(name, {'count': 0, 'totalMs': 0, 'maxMs': 0})
                    summary['count'] += 1
                    summary['totalMs'] += elapsed
                    summary['maxMs'] = max(summary['maxMs'], elapsed)
                if elapsed >= 100:
                    self.slow.append(row)

    def increment(self, name, amount=1):
        with self.lock:
            if name in self.counters or len(self.counters) < 64:
                self.counters[name] = self.counters.get(name, 0) + amount

    def record_runtime_timing(self, session_id, value):
        row = timing_metadata(value)
        if row.get('kind') and row.get('marks'):
            row['sessionId'] = session_id
            row['observedAt'] = time.time()
            if row['kind'] == 'preparation':
                # Compare this receipt with worker marks only when domains
                # match. Wall clocks and an absent domain prove no IPC delay.
                row['hostClockDomain'] = clock_domain()
                row['marks']['host.observed'] = time.monotonic_ns()
            with self.lock:
                self.runtime_timings.append(row)

    def record_lag(self, delay):
        if delay >= 0.1:
            with self.lock:
                self.lag.append({'at': time.time(), 'delayMs': round(delay * 1000, 2)})

    def record_storage_collection(self, *, outcome, reason, elapsed_ms, catchups,
                                  indexedPruned=0, indexedFilesRemoved=0,
                                  indexedBytesRemoved=0, unindexedFilesRemoved=0):
        """Latest bounded collector facts; no artifact identities or paths.

        Byte counts describe successful indexed-file unlinks, not filesystem free
        space. Interrupted/cancelled passes can have partially removed files;
        only completed-pass counts establish measured reclamation here.
        """
        with self.lock:
            self.storage_collection = {'at': time.time(), 'outcome': outcome,
                'reason': reason, 'elapsedMs': round(elapsed_ms, 2), 'rootCatchups': catchups,
                'indexedPruned': indexedPruned, 'indexedFilesRemoved': indexedFilesRemoved,
                'indexedBytesRemoved': indexedBytesRemoved, 'unindexedFilesRemoved': unindexedFilesRemoved,
                'removalCountsComplete': outcome == 'completed'}

    def record_resource_rebuild(self, resource, package, reasons, globals_changed):
        """Call-site enums only; page keys and their values never enter diagnostics."""
        with self.lock:
            self.resource_rebuild_counts['total'] += 1
            for field, names in (('byReason', reasons), ('byResource', (resource,))):
                counts = self.resource_rebuild_counts[field]
                for name in names:
                    if name in counts or len(counts) < 64:
                        counts[name] = counts.get(name, 0) + 1
            self.resource_rebuilds.append({'at': time.time(), 'resource': resource,
                'package': package, 'reasons': tuple(reasons), 'changedGlobals': tuple(globals_changed)})

    def record_projection_failure(self, reason, failure, *, session_id=None):
        """Fixed worker facts and the host's requested chat ID, never sources."""
        from .history_projection_failure import sanitize_failure
        from .history_projection_process import REASONS
        value = sanitize_failure(failure)
        if value is not None and reason in REASONS:
            # Identity comes from the admitting host, not the worker's payload.
            # Multiple queued reads may finish after the viewer switches chats.
            if isinstance(session_id, str):
                value['sessionId'] = session_id[:200]
            with self.lock:
                self.projection_failures.append({'at': time.time(), 'reason': reason, **value})

    def record_shell_query(self, operation, section, queued_at, started_at, result, *, accepted,
                           completed_at=None, disposition=None):
        """Bounded scheduling facts; no page keys, query text or source paths."""
        if operation not in {'chats', 'workspace_counts', 'pinned_navigation'}:
            return
        section = section if section in {'recent', 'shortcuts', 'pinned', 'search'} else None
        stats = result.get('stats', {})
        row = {'at': time.time(), 'operation': operation, 'section': section,
               'queueWaitMs': round(max(0, started_at - queued_at) * 1000, 2),
               'readMs': round(max(0, time.monotonic() - started_at) * 1000, 2),
               'accepted': bool(accepted)}
        from .history_projection_process import REASONS
        reason = result.get('reason')
        if isinstance(reason, str) and reason in REASONS:
            row['reason'] = reason
        if disposition in ('accepted', 'no_interest', 'retired', 'stale', 'retry'):
            row['disposition'] = disposition
        if completed_at is not None:
            row['ownerWaitMs'] = round(max(0, time.monotonic() - completed_at) * 1000, 2)
        for key in ('vmSteps', 'decodedRows', 'elapsedMs', 'threadCpuMs'):
            value = stats.get(key)
            if type(value) in (int, float) and 0 <= value < float('inf'):
                row[key] = round(value, 2)
        from .history_projection_failure import sanitize_failure
        failure = sanitize_failure(result.get('failure'))
        if failure is not None:
            row['failure'] = failure
        with self.lock:
            self.shell_queries.append(row)

    async def _heartbeat(self):
        while True:
            started = time.monotonic()
            await asyncio.sleep(0.2)
            self.record_lag(max(0, time.monotonic() - started - 0.2))

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._heartbeat())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    def snapshot(self):
        with self.lock:
            return {'schemaVersion': 1, 'startedAt': self.started,
                    'counters': dict(self.counters),
                    'operations': {name: {key: round(value, 2) if key != 'count' else value
                                         for key, value in values.items()}
                                   for name, values in self.operations.items()},
                    'slowOperations': [dict(row) for row in self.slow],
                    'runtimeTimings': [{**row, 'marks': dict(row['marks'])} for row in self.runtime_timings],
                    'eventLoopDelays': [dict(row) for row in self.lag],
                    'historyProjectionFailures': [dict(row) for row in self.projection_failures],
                    'shellQueries': [{**row, **({'failure': dict(row['failure'])}
                                      if 'failure' in row else {})} for row in self.shell_queries],
                    'storageCollection': dict(self.storage_collection) if self.storage_collection is not None else None,
                    'shellResourceRebuilds': {'total': self.resource_rebuild_counts['total'],
                        'byReason': dict(self.resource_rebuild_counts['byReason']),
                        'byResource': dict(self.resource_rebuild_counts['byResource']),
                        'recent': [{**row, 'reasons': list(row['reasons']),
                                    'changedGlobals': list(row['changedGlobals'])}
                                   for row in self.resource_rebuilds]},
                    'limitations': ['Inclusive operations overlap; do not add their durations.',
                                    'Loop delays include host suspension and scheduling; they do not identify the cause.',
                                    'No request bodies, prompts, credentials, paths or stack locals are collected.']}


def measured(name, *, session=False):
    """Labels are call-site constants. Optional identity is an existing chat ID."""
    def decorate(function):
        asynchronous = inspect.iscoroutinefunction(function)
        def measurement(owner, args):
            service = getattr(owner, 'service', owner)
            diagnostics = getattr(service, 'performance_diagnostics', None)
            identity = args[0] if session and args else None
            if isinstance(identity, dict):
                identity = identity.get('id')
            return (diagnostics.measure(name, synchronous=not asynchronous, session_id=identity)
                    if diagnostics is not None else nullcontext())
        if asynchronous:
            @wraps(function)
            async def wrapped(self, *args, **kwargs):
                with measurement(self, args):
                    return await function(self, *args, **kwargs)
        else:
            @wraps(function)
            def wrapped(self, *args, **kwargs):
                with measurement(self, args):
                    return function(self, *args, **kwargs)
        return wrapped
    return decorate


def measure(service, name, **kwargs):
    diagnostics = getattr(service, 'performance_diagnostics', None)
    return diagnostics.measure(name, **kwargs) if diagnostics is not None else nullcontext()


def count(service, name, amount=1):
    diagnostics = getattr(service, 'performance_diagnostics', None)
    if diagnostics is not None:
        diagnostics.increment(name, amount)


class TimedLock(asyncio.Lock):
    """Same cancellation/fairness behavior, with inclusive acquisition latency."""
    def __init__(self, service, name):
        super().__init__()
        self.service, self.name = service, name

    async def acquire(self):
        with measure(self.service, self.name):
            return await super().acquire()
