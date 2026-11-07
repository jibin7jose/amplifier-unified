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


def observe(operation, *args):
    """Optional attribution failure cannot change execution or fit authority."""
    try:
        return operation(*args)
    except Exception:
        return None


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
    fitted: str | None  # same string already owned by SurfaceProvider.prepared
    options: object
    decision: dict | None = None
    valid: bool = True
    taken: bool = False

    def close(self):
        self.valid = False
        self.fitted = None
        self.decision = None


def invalidate(owner, request):
    for ticket in owner._capture_tickets:
        if ticket.request() is request:ticket.close()
    owner._capture_tickets[:] = [t for t in owner._capture_tickets if t.valid and t.request() is not None]


def begin_fit(owner, original, prepared, options):
    entry = next((row for row in owner.prepared if row[0] is original and row[1] is prepared), None)
    if entry is None or not isinstance(entry[2], str):return None
    try:
        ticket = FitTicket(weakref.ref(original), weakref.ref(asyncio.current_task()),
                           weakref.ref(owner.original), entry[2], normalized_options(options))
    except TypeError:
        return None
    owner._capture_tickets.append(ticket)
    while len(owner._capture_tickets) > 4:owner._capture_tickets.pop(0).close()
    return ticket


def finish_fit(ticket, decision):
    if ticket is not None and ticket.valid:
        try:
            from .context_inspection import budget_public
            ticket.decision = budget_public(decision)
        except Exception:
            ticket.close()


def take(owner, request):
    rows = [t for t in owner._capture_tickets if t.valid and not t.taken
            and t.request() is request and t.task() is asyncio.current_task()]
    if len(rows) != 1:return None
    ticket = rows[0]
    if ticket.provider() is not owner.original or not any(
            row[0] is request and row[2] is ticket.fitted for row in owner.prepared):
        ticket.close()  # Its fitted generation was evicted or replaced.
        return None
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
def dispatch(ticket, provider, request):
    # The token is scoped to an awaited entry/anext/aclose, never an outward yield.
    value = None
    if ticket is not None:
        try:value = Dispatch(ticket, provider, weakref.ref(request), weakref.ref(asyncio.current_task()))
        except TypeError:pass
    token = DISPATCH.set(value)
    try:yield
    finally:DISPATCH.reset(token)


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
    if value.provider is not provider and execution.provider_wrappers.get(id(provider)) is not value.provider:return None
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
            return {'budget': {}, 'status': status}
        value.consumed = True
        ticket = value.ticket
        if not ticket.valid or ticket.decision is None:return {'budget': {}, 'status': status}
        actual_options = normalized_options(options)
        if ticket.options is None or actual_options is None:
            status = 'binding_unsupported'
        elif actual_options == ticket.options:
            # ONE complete current encoding, independent of retained candidates.
            # Exact equality to the already-owned full fitted representation,
            # never Work's pre-admission digest or an inspector prefix hash.
            if request.model_dump_json() == ticket.fitted:
                budget = ticket.decision
                status = 'captured' if budget else 'not_reported'
        return {'budget': budget, 'status': status}
    except Exception:
        return {'budget': {}, 'status': 'binding_unsupported'}
    finally:
        value.ticket.close()
