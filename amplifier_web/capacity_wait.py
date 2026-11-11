"""Bounded FIFO waits before model invocation; admitted receipts own usage."""
from __future__ import annotations

import asyncio
import copy

from .capacity import admission
from .collaboration import BINDING, PRINCIPAL
from .management import Management
from .handoff_admission import finishing_turn
from .model_capacity_settings import policy
from .model_admission_identity import input_id
from .child_execution import current as child_execution


async def admit(service, sid, args):
    row = args.get('call') or {}
    binding = BINDING.get() or {}
    has_child_scope = '_childOrigin' in binding
    child_scope = copy.deepcopy(binding.get('_childOrigin'))
    def child_guard(current):
        if not has_child_scope:
            return None
        live, reason = child_execution(service, current, child_id=row.get('sessionId'), explain=True)
        if live is None:
            return f'{reason} No provider request was sent.'
        if live != child_scope:
            reason = 'The authenticated child execution binding changed while waiting.'
            return f'{reason} No provider request was sent.'
        if input_id(row) not in live['inputIds']:
            reason = 'The child input is outside its authorized assignment.'
            return f'{reason} No provider request was sent.'
    if not args.get('waitForCapacity') or row.get('lifecycle') == 'background':
        return await admission(service, sid, args, guard=child_guard)
    async with service.lock:
        session = service._session(sid)
        assignment = service.assignments.current(session)
        if assignment is None:
            direct = True
        else:
            direct = False
            active = session.get('collaborationGeneration') or {}
            expected = ((BINDING.get() or {}).get('_generationId'), tuple(active.get('inputIds', [])),
                        session.get('interruptionRevision', 0), session.get('configurationPendingRevision', 0))
            queue = getattr(service, '_model_admission_waiters', None)
            if queue is None:
                queue = service._model_admission_waiters = []
            if any(item['sid'] == sid and item['id'] == row.get('id') for item in queue):
                return {'allowed': False, 'state': 'cancelled',
                        'reasons': ['This model call is already waiting; it was not submitted again.']}
            ticket = {'sid': sid, 'id': row.get('id'), 'assignment': assignment['id'], 'visible': False}
            runtime_workers = getattr(service.runtime, 'workers', None)
            native = runtime_workers.get(sid) if isinstance(runtime_workers, dict) else None
    if direct:
        return await admission(service, sid, args, guard=child_guard)

    def guard(current):
        if reason := child_guard(current):
            return reason
        active = current.get('collaborationGeneration') or {}
        drain = args.get('_handoffDrain')
        draining = current.get('configurationBusy') and finishing_turn(
            service, current, row, drain, args.get('_nativeChild'))
        now = (active.get('id'), tuple(active.get('inputIds', [])),
               current.get('interruptionRevision', 0), current.get('configurationPendingRevision', 0))
        if (PRINCIPAL.get() != (current.get('runtimeSessionId') or sid)
                or (not has_child_scope and not draining and (not expected[0] or now != expected
                    or active.get('terminal') or input_id(row) not in expected[1]))
                or now[2:] != expected[2:]
                or current.get('status') in {'stopped', 'stopping', 'interrupted', 'error'}):
            return 'The input or generation changed while waiting; no provider request was sent.'
        if (current.get('configurationBusy') and not draining
                or current.get('fileAccessPending') or Management.unresolved_configuration(current)):
            return 'Conversation access or configuration changed while waiting; no provider request was sent.'
        if (current.get('failure') or {}).get('category') in {'content_filter', 'computer_capture_stop'}:
            return 'A safety halt prevents this waiting request from continuing.'
        value = service.assignments.current(current)
        if not value or value['id'] != ticket['assignment'] or value['status'] != 'active':
            return 'The assignment stopped or membership changed; no provider request was sent.'
        if isinstance(runtime_workers, dict) and (runtime_workers.get(sid) is not native
                or native is None or native.get('closing') or native.get('yielded') or native.get('yielding')
                or native['process'].returncode is not None):
            return 'The originating worker is no longer available; no provider request was sent.'
        if row.get('sessionId') not in {sid, current.get('runtimeSessionId')}:
            children = current.get('execution', {}).get('nodes', [])
            child = next((n for n in reversed(children) if n.get('kind') == 'worker'
                          and n.get('sessionId') == row.get('sessionId')), None)
            if (child and (not child_scope or child.get('runId') == child_scope['runId'])
                    and child.get('phase') in {'completed', 'cancelled', 'error', 'interrupted'}):
                return 'The originating child finished while waiting; no provider request was sent.'
        if service.closed:
            return 'The host is closing; no provider request was sent.'

    def turn():
        first = next((item for item in queue if item['assignment'] == ticket['assignment']), None)
        return first is None or first is ticket

    def enqueue(current):
        # Admission called this while holding the same lock used to reserve
        # calls. The queue bound applies only after a call actually must wait.
        if ticket in queue:
            return None
        maximum = policy(service)['maxWaitingCalls']
        if maximum and len(queue) >= maximum:
            return {'allowed': False, 'state': 'wait_expired',
                    'reasons': ['Too many model requests are already waiting. No provider request was sent.']}
        queue.append(ticket)

    started = asyncio.get_running_loop().time()

    def deadline():
        timeout = policy(service)['waitTimeoutSeconds']
        if timeout and asyncio.get_running_loop().time() >= started + timeout:
            return {'allowed': False, 'state': 'wait_expired',
                    'reasons': ['Waiting for model capacity timed out. No provider request was sent; saved results and budgets are unchanged.']}

    visible = False
    try:
        while True:
            event = service.coordination.signal.event
            result = await admission(service, sid, args, guard=guard, queue_turn=turn, on_wait=enqueue, before_reserve=deadline)
            if result.get('state') != 'waiting':
                return result
            timeout = policy(service)['waitTimeoutSeconds']
            remaining = started + timeout - asyncio.get_running_loop().time() if timeout else None
            if remaining is not None and remaining <= 0:
                return {'allowed': False, 'state': 'wait_expired',
                        'reasons': ['Waiting for model capacity timed out. No provider request was sent; saved results and budgets are unchanged.']}
            if not visible:
                async with service.lock:
                    current = service._session(sid)
                    if reason := guard(current):
                        return {'allowed': False, 'state': 'cancelled', 'reasons': [reason]}
                    prior = next((item for item in queue if item['sid'] == sid and item['visible']), None)
                    ticket['previousActivity'] = (prior['previousActivity'] if prior
                                                  else copy.deepcopy(current.get('activity')))
                    ticket['visible'] = True
                    service._activity(current, 'capacity', 'Waiting for model capacity. Work will continue automatically.')
                    service._publish_changes(sessions={sid}, detail_only=True)
                    visible = True
            try:
                await asyncio.wait_for(event.wait(), min(remaining, 5) if remaining is not None else 5)
            except TimeoutError:
                pass  # Recheck worker liveness even without a publication.
    finally:
        async with service.lock:
            if ticket in queue:
                queue.remove(ticket)
            current = service._session(sid)
            other_visible = any(item['sid'] == sid and item['visible'] for item in queue)
            if (visible and not other_visible and guard(current) is None
                    and (current.get('activity') or {}).get('phase') == 'capacity'):
                if ticket['previousActivity'] is None:
                    current.pop('activity', None)
                else:
                    current['activity'] = ticket['previousActivity']
                service._publish_changes(sessions={sid}, detail_only=True)
            service.coordination.notify()
