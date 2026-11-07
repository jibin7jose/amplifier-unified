"""Capacity views over authoritative execution receipts; no second usage ledger."""
from __future__ import annotations

import copy
import asyncio
import hashlib
import inspect
import json
import math
import time

from .token_usage import with_gross_tokens
from .model_admission_identity import input_id

METRICS = ('inputTokens', 'outputTokens', 'totalTokens', 'grossInputTokens', 'grossTotalTokens', 'reasoningTokens', 'cacheReadTokens', 'cacheWriteTokens', 'costUsd')
LIVE = {'running', 'working', 'starting', 'queued', 'retrying', 'pending'}


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    return {
        'capacity.read': ('Read durable model-call receipts and task-wide consumption, including actual descendants. Unknown totals are explicitly partial; reading never starts work.', schema({**sid, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500}}, ['sessionId'])),
        'capacity.set': ('Set a revision-bound cumulative task budget. Total tokens include cache writes once; cache reads are already included in input. Limits pause future model calls at admission, never roll back in-flight effects. Updating does not send input or resume a paused task.', schema({**sid,
            'expectedRevision': {'type': 'integer', 'minimum': 0}, 'maxTotalTokens': {'type': ['integer', 'null'], 'minimum': 1},
            'maxCostUsd': {'type': ['number', 'null'], 'exclusiveMinimum': 0},
            'warningFraction': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1},
            'unknownPolicy': {'enum': ['pause', 'allow']}, 'includeEstimatedCost': {'type': 'boolean'},
            'enabled': {'type': 'boolean'}}, ['sessionId', 'expectedRevision'])),
        'capacity.quota': ('Read provider-owned quota/reset information if supported. No purchase, redemption, or guessed account balance.', schema(sid, ['sessionId'])),
    }


def aggregate(calls):
    calls = [{**row, 'usage': with_gross_tokens(row.get('usage') or {})} for row in calls]
    result = {}
    for key in METRICS:
        rows = [(row, row.get('usage', {}).get(key)) for row in calls]
        known = [(row, value) for row, value in rows if number(value)]
        pending = sum(row.get('phase') in LIVE and not row.get('endedAt') for row, value in rows if not number(value))
        result[key] = {'value': sum(value for _, value in known) if known else None,
                       'unit': 'USD' if key == 'costUsd' else 'tokens', 'knownCalls': len(known),
                       'pendingCalls': pending, 'unknownCalls': len(rows) - len(known) - pending,
                       'status': 'empty' if not rows else 'pending' if not known and pending == len(rows) else 'unknown' if not known else 'partial' if len(known) < len(rows) else 'known'}
        if key == 'costUsd':
            result[key]['estimatedValue'] = sum(value for row, value in known if row.get('usage', {}).get('costType') == 'estimated')
            result[key]['estimatedCalls'] = sum(row.get('usage', {}).get('costType') == 'estimated' for row, _ in known)
            result[key]['source'] = 'provider-reported or explicitly attributed estimate; no host price table'
        elif key in ('grossInputTokens', 'grossTotalTokens'):
            result[key]['source'] = 'Core input includes cache reads; add reported cache writes once' + (' and output' if key == 'grossTotalTokens' else '')
    # Voice duration covers session receipts only. Backend model calls do not
    # become missing voice measurements, and a numeric update is not finality.
    voice = [row for row in calls if 'voiceSeconds' in row['usage'] or 'usageFinalized' in row['usage'] or
             (row.get('provider') == 'OpenAI voice' and row.get('id', '').endswith(':session'))]
    if voice:
        known = [row for row in voice if number(row['usage'].get('voiceSeconds'))]
        pending = sum(row.get('phase') in LIVE and not row.get('endedAt') for row in voice)
        incomplete = sum(not (row.get('phase') in LIVE and not row.get('endedAt')) and
                         row['usage'].get('usageFinalized') is not True for row in voice)
        result['voiceSeconds'] = {
            'value': sum(row['usage']['voiceSeconds'] for row in known) if known else None,
            'unit': 'seconds', 'knownCalls': len(known), 'pendingCalls': pending,
            'incompleteCalls': incomplete, 'unknownCalls': sum(not number(row['usage'].get('voiceSeconds')) and
                not (row.get('phase') in LIVE and not row.get('endedAt')) for row in voice),
            'status': 'partial' if incomplete or known and len(known) < len(voice) else
                      'pending' if pending else 'known' if known else 'unknown',
            'source': 'provider-reported voice session duration; incomplete sessions retain the last observed value',
        }
    return result


def usage_snapshot(session, *, admission_only=False):
    """The root's persisted execution projection already includes mounted children.

    Ignore aggregateUsage on tools/workers. IDs denote whole-call replacement
    receipts, not deltas; producer revisions reject late cumulative snapshots.
    Never infer descendants from title, workspace, or a requested session ID.
    """
    root = session['id']
    tree = session.get('execution', {})
    nodes = tree.get('nodes', []) + tree.get('retiredUsageNodes', [])
    descendants = {root, session['id']}
    descendants.update(row.get('sessionId') for row in nodes if row.get('kind') == 'worker' and row.get('rootSessionId') == root)
    descendants.update(row.get('sessionId') for row in session.get('workers', []) if row.get('sessionId'))
    # Admission already authenticated the enclosing worker. Its child's topology
    # event may arrive later than the reservation; do not drop that held call.
    descendants.update(row.get('sessionId') for row in nodes if row.get('kind') == 'llm'
                       and row.get('rootSessionId') == root and row.get('admittedAt'))
    calls = {}
    rejected = 0
    for row in nodes:
        if row.get('kind') != 'llm':
            continue
        if row.get('rootSessionId') != root or row.get('sessionId') not in descendants or not row.get('id'):
            rejected += 1
            continue
        key = (row['sessionId'], row['id'])
        old = calls.get(key)
        if old and (row.get('revision', 0), bool(row.get('endedAt'))) < (old.get('revision', 0), bool(old.get('endedAt'))):
            continue
        if admission_only:
            # Private disabled-budget admission needs only current call status.
            # The caller holds app.lock and does not expose/mutate these aliases.
            calls[key] = row
            continue
        calls[key] = {k: copy.deepcopy(row[k]) for k in ('id', 'revision', 'producerId', 'budgetRevision', 'admittedAt', 'sessionId', 'provider', 'model', 'phase', 'startedAt', 'endedAt', 'usage', 'lifecycle', 'assignmentId', 'assignmentRevision', 'assignmentReservation') if k in row}
        if 'usage' in calls[key]:
            calls[key]['usage'] = with_gross_tokens(calls[key]['usage'] or {})
    if admission_only:
        return {'metrics': {}, 'receipts': list(calls.values())}
    # A late correction can be projected into the live tree after history edits
    # moved its original admission to retired usage. Keep that immutable binding
    # while selecting the latest usage revision; do not charge the call twice.
    for row in nodes:
        key = (row.get('sessionId'), row.get('id'))
        if key in calls and row.get('rootSessionId') == root and row.get('admittedAt'):
            for field in ('admittedAt', 'assignmentId', 'assignmentRevision', 'assignmentReservation'):
                if field in row:
                    calls[key].setdefault(field, copy.deepcopy(row[field]))
    receipts = sorted(calls.values(), key=lambda row: (row.get('startedAt') or 0, row['id']))
    groups = {}
    for row in receipts:
        groups.setdefault((row.get('provider') or 'unknown', row.get('model') or 'unknown'), []).append(row)
    return {'scope': 'root and observed descendants', 'sessionId': session['id'], 'source': 'persisted execution model-call receipts',
            'calls': len(receipts), 'excludedUnboundCalls': rejected, 'metrics': aggregate(receipts),
            'providers': [{'provider': provider, 'model': model, 'calls': len(rows), 'metrics': aggregate(rows)} for (provider, model), rows in sorted(groups.items())],
            'receipts': receipts, 'coverage': 'observed calls only; historical calls without telemetry and provider-internal retries may be unreported'}


def evaluate(policy, usage):
    reasons, warnings = [], []
    for metric, limit_key in (('grossTotalTokens', 'maxTotalTokens'), ('costUsd', 'maxCostUsd')):
        limit = policy.get(limit_key)
        if not policy.get('enabled') or limit is None:
            continue
        row = usage['metrics'][metric]
        value = row['value'] or 0
        uncertain = row['unknownCalls']
        if metric == 'costUsd' and not policy.get('includeEstimatedCost'):
            value -= row['estimatedValue']
            uncertain += row['estimatedCalls']
        label = 'Total tokens (including cache writes)' if metric == 'grossTotalTokens' else 'Cost (USD)'
        if value >= limit:
            reasons.append(f'{label} limit reached ({value:g} of {limit:g})')
        elif value >= limit * policy.get('warningFraction', .8):
            warnings.append(f'{label} approaching limit ({value:g} of {limit:g})')
        if uncertain and policy.get('unknownPolicy', 'pause') == 'pause':
            reasons.append(f'{label} is unknown for {uncertain} settled call(s)')
    if policy.get('enabled') and policy.get('lastDenial'):
        reasons.extend(reason for reason in policy['lastDenial'].get('reasons', []) if reason not in reasons)
    return {'allowed': not reasons, 'state': 'paused' if reasons else 'warning' if warnings else 'active' if policy.get('enabled') else 'disabled',
            'reasons': reasons, 'warnings': warnings, 'inFlightCalls': sum(row.get('phase') in LIVE and not row.get('endedAt') for row in usage['receipts']),
            'enforcement': 'before each supported model call; existing calls and effects may finish and exceed the limit'}


class CapacityController:
    """One runtime control-state writer; edits are valid between provider calls."""
    def __init__(self, controls):
        self.controls = controls
        self.policy = {'revision': 0, 'enabled': False, 'maxTotalTokens': None, 'maxCostUsd': None,
                       'warningFraction': .8, 'unknownPolicy': 'pause', 'includeEstimatedCost': False}
        self.receipts = {}
        self.last_denial = None
        self.admit = None

    async def perform(self, operation, args):
        if operation == 'capacity.quota':
            return await self.quota()
        if operation == 'capacity.get':
            return copy.deepcopy(self.policy)
        if operation != 'capacity.set':
            raise ValueError('Unknown capacity control')
        identity = args.get('commandId')
        if not isinstance(identity, str) or not identity:
            raise ValueError('Capacity edits need a stable commandId')
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if identity in self.receipts:
            if self.receipts[identity]['fingerprint'] != fingerprint:
                raise ValueError('This budget command ID has different contents')
            return {**copy.deepcopy(self.policy), 'duplicate': True}
        if type(args.get('expectedRevision')) is not int or args['expectedRevision'] != self.policy['revision']:
            raise ValueError('The budget revision changed; inspect it before editing')
        previous, denial = self.policy, self.last_denial
        policy = copy.deepcopy(previous)
        for key in ('enabled', 'maxTotalTokens', 'maxCostUsd', 'warningFraction', 'unknownPolicy', 'includeEstimatedCost'):
            if key in args:
                policy[key] = args[key]
        for key in ('maxTotalTokens', 'maxCostUsd'):
            if policy[key] is not None and (not number(policy[key]) or policy[key] <= 0 or (key == 'maxTotalTokens' and type(policy[key]) is not int)):
                raise ValueError('Limits must be positive numbers or null')
        if not number(policy['warningFraction']) or not 0 < policy['warningFraction'] <= 1 or policy['unknownPolicy'] not in ('pause', 'allow'):
            raise ValueError('Invalid warning threshold or unknown-usage policy')
        if any(type(policy[key]) is not bool for key in ('enabled', 'includeEstimatedCost')):
            raise ValueError('Budget switches must be booleans')
        if policy['enabled'] and policy['maxTotalTokens'] is None and policy['maxCostUsd'] is None:
            raise ValueError('An enabled budget needs at least one limit')
        task = self.controls.tasks.record() or {}
        policy.update(revision=previous['revision'] + 1, updatedAt=time.time(), origin=args.get('origin'), commandId=identity,
                      taskId=task.get('id'), taskRevision=task.get('revision'), scope='conversation lifetime observed usage')
        self.policy, self.last_denial = policy, None
        self.receipts[identity] = {'fingerprint': fingerprint, 'revision': policy['revision']}
        try:
            self.controls.persist(task_only=True)
        except Exception:
            self.policy, self.last_denial = previous, denial
            self.receipts.pop(identity, None)
            raise
        return copy.deepcopy(policy)

    async def guard(self, row):
        # Always ask the host: it owns persisted usage and can reject stale
        # workers. A missing bridge cannot silently bypass an enabled budget.
        if self.admit is None:
            if self.policy.get('enabled'):
                raise ValueError('Task budget admission is unavailable')
            return
        result = await self.admit(row)
        if result['allowed']:
            row['budgetRevision'] = result['budgetRevision']
            row['admittedAt'] = result.get('admittedAt')
            if self.last_denial:
                # An assignment allowance may change without changing the
                # local conversation policy revision. Clear stale denial only
                # after authoritative admission, preserving limits and usage.
                self.last_denial = None
                self.controls.persist(task_only=True)
        if not result['allowed'] and result.get('state') == 'cancelled':
            raise asyncio.CancelledError('; '.join(result['reasons']))
        if not result['allowed'] and result.get('state') == 'wait_expired':
            # No provider call was made. A bounded queue expiry is not a saved
            # cumulative budget pause and must not poison later continuation.
            from .model_admission import ModelAdmissionWaitExpired
            raise ModelAdmissionWaitExpired('; '.join(result['reasons']))
        if not result['allowed']:
            self.last_denial = {**result, 'at': time.time(), 'callId': row['id']}
            self.controls.persist(task_only=True)
            from .model_admission import ModelAdmissionPaused
            raise ModelAdmissionPaused('Task budget paused future model calls: ' + '; '.join(result['reasons']))

    async def quota(self):
        rows = []
        for instance, provider in (self.controls.coordinator.get('providers') or {}).items():
            callback = getattr(provider, 'get_usage_limits', None)
            if not callable(callback):
                rows.append({'provider': instance, 'status': 'unsupported', 'remaining': None, 'resetsAt': None})
                continue
            try:
                value = callback()
                if inspect.isawaitable(value):
                    value = await asyncio.wait_for(value, timeout=10)
                # Provider-owned read-only capability. Never expose credentials,
                # arbitrary account metadata, or a provider response wholesale.
                windows = []
                for window in value.get('windows', [])[:30]:
                    if not isinstance(window, dict):
                        continue
                    safe = {key: window[key] if number(window.get(key)) else None for key in ('limit', 'used', 'remaining', 'resetsAt')}
                    safe.update({key: str(window[key])[:120] for key in ('name', 'unit') if isinstance(window.get(key), str)})
                    windows.append(safe)
                rows.append({'provider': instance, 'status': 'reported' if windows else 'unknown', 'source': 'provider.get_usage_limits', 'observedAt': time.time(), 'windows': windows})
            except Exception:
                rows.append({'provider': instance, 'status': 'unavailable', 'remaining': None, 'resetsAt': None})
        return {'providers': rows, 'scope': 'provider account capability; separate from task budget'}


def restore_observation(session):
    """A host restart loses observation, not proof of external cancellation."""
    reference = dict.get(session, '_coldFields', {}).get('execution', {})
    if (not dict.__contains__(session, 'execution')
            and reference.get('executionProjection') == 1
            and reference.get('pendingObservation') is False):
        return  # Compact metadata proves there is no running receipt to settle.
    changed = False
    tree = session.get('execution', {})
    for row in tree.get('nodes', []) + tree.get('retiredUsageNodes', []):
        if row.get('kind') == 'llm' and row.get('producerId') and row.get('phase') in LIVE:
            row['phase'] = 'outcome_unknown'
            changed = True
    if changed:
        from .execution import refresh_usage
        refresh_usage(session['execution'])


def saved_policy(service, sid):
    identity = service._session(sid).get('runtimeSessionId') or sid
    path = service.data_dir / 'sessions' / identity / 'control-state.json'
    if not path.exists():
        return {'revision': 0, 'enabled': False}
    try:
        saved = json.loads(path.read_text())
        policy = saved.get('capacity', {'revision': 0, 'enabled': False})
        denial = saved.get('capacityLastDenial')
        if denial and denial.get('budgetRevision') == policy['revision']:
            policy['lastDenial'] = denial
        return policy
    except (ValueError, OSError):
        # A corrupt policy must not silently disable a previously enabled guard.
        raise ValueError('Saved budget state is unreadable; repair before starting more work') from None


async def dispatch(service, operation, args, origin, command_id, include_state):
    from .errors import AppError
    import uuid
    sid = args['sessionId']
    await service.history.ensure_loaded(sid)
    session = service._session(sid)
    if operation != 'capacity.read':
        if not service.runtime or not service.management:
            raise AppError('The Amplifier runtime is unavailable')
        await service.management.ensure_runtime(session)
        forwarded = {key: value for key, value in args.items() if key != 'sessionId'}
        forwarded.update(commandId=command_id or str(uuid.uuid4()), origin=origin)
        try:
            result = await service.runtime.control(sid, operation, forwarded)
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
        if operation == 'capacity.quota':
            return {'accepted': True, 'result': result}
    async with service.lock:
        try:
            policy = saved_policy(service, sid)
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
        usage = usage_snapshot(session)
        result = {'budget': policy, 'usage': usage, 'admission': evaluate(policy, usage), 'observedAt': time.time()}
        offset, limit = args.get('offset', 0), args.get('limit', 100)
        usage['receiptCount'] = len(usage['receipts'])
        usage['receipts'] = usage['receipts'][offset:offset + limit]
        usage['offset'] = offset
        usage['nextOffset'] = offset + limit if offset + limit < usage['receiptCount'] else None
        service.state.setdefault('runtimeControl', {}).setdefault(sid, {})['capacity.read'] = result
        service._publish_changes(sessions={sid})
        return {'accepted': True, 'result': result, **({'state': service.browser_state()} if include_state else {})}


def fresh_human_input(service, session, row, denial):
    """Recheck a derived pause only for a new host-admitted human input."""
    active = session.get('collaborationGeneration') or {}
    identity = input_id(row)
    if (not active.get('id') or active.get('terminal') or not identity
            or identity not in active.get('inputIds', [])
            or row.get('lifecycle') == 'background'
            or row.get('sessionId') != session['id'] or not number(denial.get('at'))):
        return False
    message = next((m for m in reversed(session.get('messages', []))
                    if m.get('role') == 'user' and (m.get('inputId') == identity
                        or m.get('inputOrigin') == 'voice' and m.get('voiceInputId') == identity)), None)
    if (not message or message.get('questionId') or message.get('scheduledRunId')
            or message.get('createdAt', 0) <= denial['at']):
        return False
    if message.get('inputOrigin') in {'ui', 'user', 'voice'}:
        return True
    saved = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    receipt = json.loads(saved[0]) if saved else {}
    return bool(message.get('inputOrigin') == 'peer'
        and receipt.get('commandAction') == 'coordination.send' and receipt.get('humanResume')
        and receipt.get('accepted') is True and receipt.get('inputId') == identity
        and receipt.get('target', {}).get('sessionId') == session['id']
        and receipt.get('interruptionRevision') == session.get('interruptionRevision', 0)
        and receipt.get('delivery') in {'accepted', 'applied', 'submitting'})


async def admission(service, session_id, args, *, guard=None, queue_turn=None, on_wait=None, before_reserve=None):
    """Private worker-to-host gate; model actions cannot supply accounting data."""
    from .errors import AppError
    async with service.lock:
        session = service._session(session_id)
        row = copy.deepcopy(args.get('call', {}))
        if guard and (reason := guard(session)):
            return {'allowed': False, 'state': 'cancelled', 'reasons': [reason]}
        if row.get('rootSessionId') != (session.get('runtimeSessionId') or session_id):
            raise AppError('Budget admission belongs to the calling root', 409)
        for key in ('rootSessionId', 'sessionId'):
            if row.get(key) == session.get('runtimeSessionId'):
                row[key] = session_id
        nodes = session.get('execution', {}).get('nodes', [])
        producer = row.get('producerId')
        previous = session.get('capacityProducerId')
        if producer and producer != previous:
            # A new worker process cannot prove old provider outcomes. Retain
            # the existing receipts, visibly unknown, without resubmission.
            for prior in nodes:
                if prior.get('producerId') and prior.get('producerId') != producer and prior.get('kind') == 'llm' and prior.get('phase') in LIVE:
                    prior['phase'] = 'outcome_unknown'
            session['capacityProducerId'] = producer
            service._save_changes(sessions={session_id})
        policy = saved_policy(service, session_id)
        if any(prior.get('id') == row.get('id') for prior in nodes + session.get('execution', {}).get('retiredUsageNodes', [])):
            return {'allowed': False, 'state': 'paused', 'budgetRevision': policy['revision'],
                    'reasons': ['This model-call ID was already admitted; its outcome must not be replayed']}
        usage = usage_snapshot(session, admission_only=not policy.get('enabled'))
        effective = policy
        if policy.get('lastDenial') and fresh_human_input(service, session, row, policy['lastDenial']):
            # Saved denials are observations, not deliberate budget edits.
            # Both current policies still apply. The worker clears its cached
            # denial only after this admission and its durable receipt succeed.
            effective = {key: value for key, value in policy.items() if key != 'lastDenial'}
        result = {**evaluate(effective, usage), 'budgetRevision': policy['revision']}
        for key in ('assignmentId', 'assignmentRevision', 'assignmentReservation'):
            row.pop(key, None)  # Assignment authority is selected by the host.
        assignment = service.assignments.admission(session, row)
        if not assignment['allowed']:
            waiting = result['allowed'] and assignment.get('waiting', False)
            result.update(allowed=False, state='waiting' if waiting else 'paused',
                          reasons=result['reasons'] + assignment['reasons'])
        if result['allowed'] and queue_turn and not queue_turn():
            result.update(allowed=False, state='waiting',
                          reasons=['Earlier model requests are waiting for assignment capacity.'])
        if result.get('state') == 'waiting' and on_wait and (refusal := on_wait(session)):
            return refusal
        if result['allowed']:
            # The lock may have been contended beyond a finite waiting deadline.
            # Recheck at the reservation boundary; never alter an admitted call.
            if before_reserve and (refusal := before_reserve()):
                return refusal
            # Persist intent using the existing execution ID before any provider
            # request. A crash leaves pending/unknown evidence, never a replay.
            from .execution import ingest
            row.update(budgetRevision=policy['revision'], admittedAt=time.time())
            result['admittedAt'] = row['admittedAt']
            ingest(session, row)
            try:
                service._save_changes(sessions={session_id})
            except BaseException:
                # No provider request follows a failed admission. An in-memory
                # phantom must not be committed later; a lost acknowledgment
                # after commit must retain its authoritative reservation.
                retained = None
                try:
                    from .cold_display import load, MARKER
                    stored = service.db.execute("SELECT value FROM state_records WHERE kind='session' AND id=?", (session_id,)).fetchone()
                    saved = json.loads(stored[0]) if stored else {}
                    if saved.get('$viewPayload'):
                        saved = load(service.db, saved['$viewPayload'])
                    tree = saved.get('execution')
                    if tree is None and saved.get(MARKER, {}).get('execution'):
                        tree = load(service.db, saved[MARKER]['execution'])
                    if tree is not None:
                        retained = any(item.get('id') == row['id'] for item in tree.get('nodes', []) + tree.get('retiredUsageNodes', []))
                    elif stored and saved.get('id') == session_id and not saved.get('$view'):
                        retained = False
                except Exception:
                    pass  # An unreadable retained projection cannot free capacity.
                if retained is False:
                    session['execution']['nodes'] = [item for item in session['execution']['nodes'] if item.get('id') != row['id']]
                else:
                    for item in session['execution']['nodes']:
                        if item.get('id') == row['id']:
                            item['phase'] = 'outcome_unknown'
                from .execution import refresh_usage
                refresh_usage(session['execution'])
                raise
        return result


async def abandoned(service, session_id, args):
    """Private transport cancellation: preserve an admitted but unacknowledged call."""
    from .collaboration import PRINCIPAL
    from .execution import LIVE_PHASES, refresh_usage
    async with service.lock:
        session = service._session(session_id)
        row = args.get('call') or {}
        native = session.get('runtimeSessionId') or session_id
        if PRINCIPAL.get() != native or row.get('rootSessionId') != native:
            return {'retained': False}
        node = next((n for n in session.get('execution', {}).get('nodes', [])
                     if n.get('id') == row.get('id') and n.get('kind') == 'llm'
                     and n.get('producerId') == row.get('producerId')
                     and n.get('turnId') == row.get('turnId')), None)
        if node and node.get('admittedAt') and not node.get('endedAt') and node.get('phase') in LIVE_PHASES:
            node['phase'] = 'outcome_unknown'
            refresh_usage(session['execution'])
            service._publish_changes(sessions={session_id})
            return {'retained': True}
        return {'retained': False}
