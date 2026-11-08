"""Private passive fit tickets; not admission, Work authority or provider API.

The fitted owner lends its existing immutable serialization. ExecutionEvents
claims one dispatch before metadata/admission awaits and validates it once at its
existing post-admission observer point. No request is retained strongly here.
"""
from __future__ import annotations

import asyncio
import contextvars
import math
import weakref
from contextlib import contextmanager
from dataclasses import dataclass

DISPATCH = contextvars.ContextVar('amplifier_web_budget_capture_dispatch', default=None)
FITTING = contextvars.ContextVar('amplifier_web_budget_capture_fit', default=None)
TRACE = contextvars.ContextVar('amplifier_web_capture_trace', default=None)
_HOST_WRAPPERS = weakref.WeakKeyDictionary()


def observe(operation, *args):
    """Optional attribution failure cannot change execution or fit authority."""
    try:
        return operation(*args)
    except Exception:
        return None


def purpose_code(purpose, request):
    """Fixed diagnostic vocabulary, not a foreground/authority decision."""
    known = {'context-compaction': 'context_compaction',
             'context_compaction': 'context_compaction', 'image_generation': 'image_generation'}
    if type(purpose) is dict:
        value = purpose.get('purpose')
        if type(value) is str and len(value) <= 64 and value in known:return known[value]
        # These are existing host labels; never export a supplied label.
        label = purpose.get('label')
        if type(label) is str and label == 'Session naming':return 'title'
        if type(label) is str and label == 'Memory consolidation':return 'memory_consolidation'
        if value is not None or purpose:return 'unknown'
    metadata = getattr(request, 'metadata', None)
    value = metadata.get('purpose') if type(metadata) is dict else None
    return (known.get(value, 'unknown') if type(value) is str and len(value) <= 64
            else 'unspecified' if value is None else 'unknown')


def note_fit(owner, request, status):
    """Four weak notes on the existing fit owner, never a persistent registry."""
    note = (weakref.ref(request), weakref.ref(asyncio.current_task()),
            weakref.ref(owner.original), status)
    notes = getattr(owner, '_capture_fit_notes', ())
    owner._capture_fit_notes = [n for n in notes if n[0]() is not None
                               and not (n[0]() is request and n[1]() is asyncio.current_task())][-3:] + [note]


@dataclass(eq=False)
class AttemptTrace:
    provider: object  # weak; diagnostic-only, cannot be passed to claim/consume
    fit: str
    take: str
    candidates: int
    used: bool = False


def describe_take(owner, request, ticket):
    """Read bounded identity metadata only; the authoritative take already ran."""
    weakref.ref(request)  # Unsupported identity cannot inherit a fit note.
    provider = weakref.ref(owner.original)
    task = asyncio.current_task()
    rows = [t for t in owner._capture_tickets if t.request() is request]
    notes = [n for n in getattr(owner, '_capture_fit_notes', ())
             if n[0]() is request and n[1]() is task and n[2]() is owner.original]
    fit = notes[-1][3] if len(notes) == 1 else 'not_observed'
    if ticket is not None:
        take = ticket.reason if not ticket.valid else 'taken'
    elif any(t.task() is not task for t in rows) and not any(t.task() is task for t in rows):
        fit, take = 'not_observed', 'task_mismatch'
    elif sum(not t.taken and t.task() is task for t in rows) > 1:
        take = 'ambiguous'
    elif any(t.taken and t.task() is task for t in rows):
        take = 'already_taken'
    else:
        take = 'no_ticket'
    # No note can outlive this exact dispatch interval or become its retry's note.
    owner._capture_fit_notes = [n for n in getattr(owner, '_capture_fit_notes', ())
                               if n[0]() is not None and not (n[0]() is request and n[1]() is task)]
    return AttemptTrace(provider, fit, take, 0 if take == 'task_mismatch' else min(4, len(rows)))


@dataclass(eq=False)
class TraceScope:
    attempt: AttemptTrace | None
    request: object
    provider: object
    task: object
    active: bool = True


def trace_scope(attempt, provider, request):
    # Independent weak references; never supply an unproven identity to capture.
    def reference(value):
        try:return weakref.ref(value)
        except TypeError:return None
    return TraceScope(attempt, reference(request), reference(provider),
                      reference(asyncio.current_task()))


def entry_trace(execution, provider, request, purpose):
    """Freeze pre-await observation for THIS call, never a latest/global marker."""
    result = {'purpose': purpose_code(purpose, request), 'dispatch': 'not_observed',
              'fit': 'not_observed', 'take': 'not_observed', 'candidates': 0}
    scope = TRACE.get()
    if scope is None or not scope.active:return result
    if scope.task is None or scope.task() is not asyncio.current_task():
        result['dispatch'] = 'task_mismatch'
    elif scope.request is None or scope.provider is None:
        result['dispatch'] = 'identity_unproven'
    elif scope.request() is not request:
        result['dispatch'] = 'request_mismatch'
    elif not matches_provider(execution, scope.provider(), provider):
        result['dispatch'] = 'provider_mismatch'
    elif scope.attempt is None:
        result['dispatch'] = 'identity_unproven'
    elif scope.attempt.used:
        result['dispatch'] = 'already_observed'
    elif scope.attempt.provider() is not scope.provider():
        result['dispatch'] = 'provider_mismatch'
    else:
        scope.attempt.used = True  # Before metadata/admission may reenter.
        result.update(dispatch='observed', fit=scope.attempt.fit, take=scope.attempt.take,
                      candidates=scope.attempt.candidates)
    return result


def public_trace(entry, claimed, consumed):
    """Independent from captured_budget; must never disable legacy fallback."""
    allowed = {
        'purpose': {'unspecified', 'unknown', 'title', 'memory_consolidation',
                    'context_compaction', 'image_generation'},
        'dispatch': {'not_observed', 'observed', 'unavailable', 'task_mismatch', 'request_mismatch',
                     'provider_mismatch', 'identity_unproven', 'already_observed'},
        'fit': {'not_observed', 'created', 'fitted_value_unavailable', 'identity_unsupported'},
        'take': {'not_observed', 'taken', 'task_mismatch', 'no_ticket', 'ambiguous',
                 'already_taken', 'provider_changed', 'evicted_fit', 'superseded_fit',
                 'diagnostic_failure', 'consumed'},
    }
    result = {'version': 1}
    for key, values in allowed.items():
        value = entry.get(key) if type(entry) is dict else None
        result[key] = value if type(value) is str and value in values else 'not_observed' if key != 'purpose' else 'unknown'
    if entry is None:result['dispatch'] = 'unavailable'
    count = entry.get('candidates') if type(entry) is dict else None
    result['candidates'] = count if type(count) is int and 0 <= count <= 4 else 0
    result['claim'] = 'claimed' if claimed is not None else 'not_claimed'
    statuses = {'captured', 'not_reported', 'ticket_unavailable', 'request_changed', 'binding_unsupported'}
    status = consumed.get('status') if type(consumed) is dict else None
    result['consume'] = status if type(status) is str and status in statuses else 'not_observed'
    return result


def bind_wrapper(wrapper, original):
    """Called only where the host constructs its recovery wrapper."""
    _HOST_WRAPPERS[wrapper] = (weakref.ref(wrapper), weakref.ref(original))


def provider_chain(provider):
    """Exact registered edges only; arbitrary plugin .original is not followed."""
    seen, chain = set(), []
    for _ in range(8):
        if provider is None or id(provider) in seen:return None
        seen.add(id(provider))
        chain.append(weakref.ref(provider))
        edge = _HOST_WRAPPERS.get(provider)
        if edge is None:return tuple(chain)
        # WeakKeyDictionary follows key equality; the registration must still
        # belong to this exact object, never a plugin's overloaded equality.
        if edge[0]() is not provider:return None
        original = edge[1]()
        if original is None or vars(provider).get('original') is not original:return None
        provider = original
    return None


def same_chain(chain, provider):
    current = provider_chain(provider)
    return bool(chain and current and len(chain) == len(current)
                and all(a() is not None and a() is b() for a, b in zip(chain, current)))


def matches_provider(execution, mounted, provider):
    chain = provider_chain(mounted)
    return bool(chain and (chain[-1]() is provider or
                execution.provider_wrappers.get(id(provider)) is chain[-1]()))


def budget_options(nested, kwargs):
    """Mirror effective budget option merge: explicit keywords win.

    context_estimate is count input, not a dispatch option. No unsupported
    container is coerced into an empty value.
    """
    if nested is None:nested = {}
    if type(nested) is not dict or type(kwargs) is not dict:return None
    if len(nested) > 1024 or len(kwargs) > 1024:return None
    return normalized_options({**nested, **{k: v for k, v in kwargs.items()
                               if k not in {'context_estimate', 'request_options'}}})


def normalized_options(options):
    """Type-sensitive, bounded JSON-like values; no coercion or opaque fallback."""
    nodes, chars = 4096, 65536
    def visit(value, depth=0):
        nonlocal nodes, chars
        nodes -= 1
        if nodes < 0 or depth > 16:
            raise ValueError('option bound')
        kind = type(value)
        if value is None:return ('none',)
        if kind is bool:return ('bool', value)
        if kind is int:
            if value.bit_length() > 256:raise ValueError('option bound')
            return ('int', value)
        if kind is float:
            if not math.isfinite(value):raise ValueError('option type')
            return ('float', value.hex())
        if kind is str:
            chars -= len(value)
            if chars < 0:raise ValueError('option bound')
            return ('str', value)
        if kind not in (list, dict) or len(value) > 1024:
            raise ValueError('option type')
        if kind is list:return ('list', tuple(visit(v, depth+1) for v in value))
        if any(type(k) is not str for k in value):raise ValueError('option key')
        return ('dict', tuple((visit(k, depth+1), visit(value[k], depth+1)) for k in sorted(value)))
    try:
        if options is None:options = {}
        if type(options) is not dict:return None
        return visit(options)
    except (ValueError, RecursionError):
        return None


@dataclass(eq=False)
class FitTicket:
    request: object  # weak reference to the outer request
    task: object     # weak reference to its budget execution task
    provider: object # exact mounted budget owner, not a name/default-model guess
    chain: object
    fitted: str | None  # same string already owned by SurfaceProvider.prepared
    options: object
    decision: dict | None = None
    valid: bool = True
    taken: bool = False
    reason: str | None = None

    def close(self, reason='consumed'):
        if self.reason is None:self.reason = reason
        self.valid = False
        self.fitted = None
        self.decision = None


def invalidate(owner, request):
    for ticket in owner._capture_tickets:
        if ticket.request() is request:ticket.close('superseded_fit')
    owner._capture_tickets[:] = [t for t in owner._capture_tickets if t.valid and t.request() is not None]
    # An explicit new fit must not inherit the prior attempt's diagnostic note
    # if preparation fails before begin_fit is reached.
    owner._capture_fit_notes = [n for n in getattr(owner, '_capture_fit_notes', ())
                               if n[0]() is not request and n[0]() is not None]


def begin_fit(owner, original, prepared, options, kwargs=None):
    entry = next((row for row in owner.prepared if row[0] is original and row[1] is prepared), None)
    if entry is None or not isinstance(entry[2], str):
        observe(note_fit, owner, original, 'fitted_value_unavailable')
        return None
    try:
        ticket = FitTicket(weakref.ref(original), weakref.ref(asyncio.current_task()),
                           weakref.ref(owner.original), provider_chain(owner.original),
                           entry[2], budget_options(options, kwargs or {}))
    except TypeError:
        observe(note_fit, owner, original, 'identity_unsupported')
        return None
    owner._capture_tickets.append(ticket)
    while len(owner._capture_tickets) > 4:owner._capture_tickets.pop(0).close('evicted_fit')
    observe(note_fit, owner, original, 'created')
    return ticket


def finish_fit(ticket, decision):
    if ticket is not None and ticket.valid:
        try:
            from .context_inspection import budget_public
            ticket.decision = budget_public(decision)
        except Exception:
            ticket.close('diagnostic_failure')


def take(owner, request):
    rows = [t for t in owner._capture_tickets if not t.taken
            and t.request() is request and t.task() is asyncio.current_task()]
    if len(rows) != 1:return None
    ticket = rows[0]
    if ticket.provider() is not owner.original or not same_chain(ticket.chain, owner.original):
        ticket.close('provider_changed')
    elif ticket.valid and not any(
            row[0] is request and row[2] is ticket.fitted for row in owner.prepared):
        ticket.close('evicted_fit')
    ticket.taken = True
    return ticket


@dataclass(eq=False)
class Dispatch:
    ticket: FitTicket
    provider: object
    request: object
    task: object
    claimed: bool = False


@contextmanager
def dispatch(ticket, provider, request, trace=None):
    # The token is scoped to an awaited entry/anext/aclose, never an outward yield.
    value = None
    if ticket is not None:
        try:value = Dispatch(ticket, provider, weakref.ref(request), weakref.ref(asyncio.current_task()))
        except TypeError:pass
    token = DISPATCH.set(value)
    observation = observe(trace_scope, trace, provider, request)
    trace_token = TRACE.set(observation)
    try:yield
    finally:
        if observation is not None:observation.active = False
        TRACE.reset(trace_token)
        DISPATCH.reset(token)


@contextmanager
def fitting(ticket, provider, request):
    value = None
    if ticket is not None:
        try:value = Dispatch(ticket, provider, weakref.ref(request), weakref.ref(asyncio.current_task()))
        except TypeError:pass
    token = FITTING.set(value)
    try:yield
    finally:FITTING.reset(token)


def owns_budget(execution, provider, request):
    value = FITTING.get()
    return bool(value is not None and value.task() is asyncio.current_task()
                and value.request() is request
                and matches_provider(execution, value.provider, provider))


@dataclass(eq=False)
class Claim:
    ticket: FitTicket
    provider: object
    execution: object
    session: str
    call: str
    task: object
    consumed: bool = False


def claim(execution, sid, provider, request, call_id):
    value = DISPATCH.get()
    if value is None or value.claimed or value.task() is not asyncio.current_task():return None
    if value.request() is not request:return None
    if not matches_provider(execution, value.provider, provider):return None
    # Claim before ANY admission/metadata await; inherited/nested tasks cannot
    # steal or use this ticket while its intended call is suspended.
    value.claimed = True
    return Claim(value.ticket, provider, execution, sid, call_id, value.task)


def consume(value, execution, row, provider, request, options):
    if value is None:return None
    budget, status = {}, 'request_changed'
    try:
        if (value.consumed or value.execution is not execution or value.provider is not provider
                or value.session != row['sessionId'] or value.call != row['id']
                or value.task() is not asyncio.current_task()):
            return {'budget': {}, 'status': 'ticket_unavailable', 'reason': 'dispatch_mismatch'}
        value.consumed = True
        ticket = value.ticket
        if not ticket.valid:
            return {'budget': {}, 'status': 'ticket_unavailable', 'reason': ticket.reason or 'unavailable'}
        if ticket.decision is None:
            return {'budget': {}, 'status': 'ticket_unavailable', 'reason': 'budget_unavailable'}
        if not same_chain(ticket.chain, ticket.provider()):
            return {'budget': {}, 'status': 'ticket_unavailable', 'reason': 'provider_changed'}
        actual_options = normalized_options(options)
        if ticket.options is None or actual_options is None:
            status = 'binding_unsupported'
        elif actual_options == ticket.options:
            # ONE complete current encoding, independent of retained candidates.
            # Exact equality to the already-owned full fitted representation,
            # never Work's pre-admission digest or an inspector prefix hash.
            from .request_projection import validated_encoding
            encoded = validated_encoding(request)
            if (encoded if encoded is not None else request.model_dump_json()) == ticket.fitted:
                budget = ticket.decision
                status = 'captured' if budget else 'not_reported'
        return {'budget': budget, 'status': status, 'reason': None if budget else status}
    except Exception:
        return {'budget': {}, 'status': 'binding_unsupported'}
    finally:
        value.ticket.close()
