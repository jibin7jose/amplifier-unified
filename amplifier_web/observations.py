"""Qualified MCP observations; pending reads never enter the conversation runtime."""
import asyncio
import copy
from contextvars import ContextVar
import json
import time
import uuid

import jsonschema

from amplifier_scheduling.store import fingerprint
from .observation_contract import CONTRACT, bounded, check, qualify, text, validate_result, validate_schema
from .observation_store import ObservationStore

CONFIG = ('observerId', 'target', 'scope', 'sourceId', 'args', 'intervalSeconds', 'durationSeconds', 'readTimeoutSeconds')


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    identity = {**sid, 'id': string(200)}
    config = {'observerId': string(200), 'target': {'type': 'object'}, 'scope': {'type': 'object'}, 'sourceId': string(2000), 'args': {'type': 'object'},
        'intervalSeconds': {'type': 'integer', 'minimum': 15, 'maximum': 3600}, 'durationSeconds': {'type': 'integer', 'minimum': 30, 'maximum': 86400},
        'readTimeoutSeconds': {'type': 'integer', 'minimum': 1, 'maximum': 30}}
    config['presentationRequest'] = {'anyOf': [{'type': 'null'}, {'type': 'object'}]}
    review = {'previewHash': string(100), 'requestId': string(200), 'sourceMessageId': string(200)}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 1}}
    return {
        'observation.qualify': ('Record source qualification of an installed, connected read-only observer through an operator action or standing Full access. Installed-code and evidence checks remain required; submitted review assertions are not independent verification.', schema({**sid, 'descriptor': {'type': 'object'}})),
        'observation.observers': ('Read qualified observation contracts and current eligibility without connecting or preparing a provider.', schema(sid)),
        'observation.preview': ('Preview a quiet watch against persisted task and current standing authority. Does not prepare a worker or provider.', schema({**sid, **config, 'sourceMessageId': string(200)}, [*sid, *CONFIG])),
        'observation.create': ('Arm the exact reviewed quiet observation. Full access needs no human sourceMessageId; Ask when needed retains human request provenance. Stable requestId returns the original watch on retry, never rearms it.', schema({**sid, **config, **review}, [*sid, *CONFIG, 'previewHash', 'requestId'])),
        'observation.request': ('Read an earlier watch creation by stable request identity without replay.', schema({**sid, 'requestId': string(200)})),
        'observation.list': ('Read quiet watches for this conversation.', schema(sid)),
        'observation.read': ('Read exact quiet watch and bounded audit; never run the observer.', schema(identity)),
        'observation.report': ('Read the same durable observation report; source callbacks cannot author results through this action.', schema(identity)),
        'observation.pause': ('Pause checks and suppress an unadmitted handoff. Watched work is not stopped.', schema({**identity, **revision, 'requestId': string(200)})),
        'observation.cancel': ('Cancel this watch without cancelling or rolling back watched work.', schema({**identity, **revision, 'requestId': string(200)})),
        'observation.resume': ('Resume a nonterminal watch after fresh review under still-valid Full authority or renewed human authority. Full access cannot renew a stopped or revoked watch. Unknown/terminal handoffs never replay.', schema({**identity, **revision, **config, **review}, [*identity, *revision, *CONFIG, 'previewHash', 'requestId'])),
    }


class Observations:
    def __init__(self, app, *, clock=time.time):
        self.app, self.clock = app, clock
        self.store = ObservationStore(app.data_dir / 'observations.sqlite3')
        self.input_bindings = ContextVar('observation_input_bindings', default=None)
        self.pending = None
        self.attached = False

    def wake(self):
        if not self.attached and getattr(self.app, 'operations', None):
            self.app.operations.register_source('observation', self.operation_records, self.operation_record)
            self.attached = True
        if self.pending is None or self.pending.done(): self.pending = asyncio.create_task(self.tick())

    async def close(self):
        if self.pending:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)

    def operation_records(self, sid):
        return [self.shape(row) for row in self.store.rows('watch', sid)]

    def operation_record(self, sid, identity):
        try: return self.shape(self.store.get('watch', identity.removeprefix('observation:'), sid))
        except ValueError: return None

    def shape(self, row):
        handoffs = [item for item in self.store.rows('outbox', row['sessionId']) if item['watchId'] == row['id']]
        state = {'active':'running', 'ended':'completed', 'needs_review':'outcome_unknown'}.get(row['status'], row['status'])
        if handoffs and handoffs[-1]['phase'] == 'waiting_worker': state = 'waiting_input'
        elif handoffs and handoffs[-1]['phase'] == 'unknown': state = 'outcome_unknown'
        return {'id': 'observation:' + row['id'], 'sessionId': row['sessionId'], 'source': 'observation', 'kind': 'quiet-observation',
            'state': state,
            'revision': fingerprint([row['revision'], handoffs]), 'controlAvailable': False, 'evidence': {**copy.deepcopy(row), 'handoffs': handoffs}, 'createdAt': row['createdAt'], 'updatedAt': row['updatedAt']}

    def task(self, sid):
        from .runtime_controls import override_path
        session = self.app._session(sid)
        native = session.get('runtimeSessionId') or session.get('nativeIdentity') or sid
        path = override_path(native).with_name('control-state.json')
        try:
            if path.stat().st_size > 2_000_000: raise ValueError('Persisted task authority is too large')
            record = json.loads(path.read_text())['task']
            if not record or not record.get('id') or not isinstance(record.get('revision'), int): raise ValueError('Missing persisted task authority')
            return record
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError('Save an explicit task before creating a quiet observation; no worker was prepared') from exc

    def configuration(self, args):
        from .observation_presentation import request
        config = bounded({key: args[key] for key in CONFIG})
        config['presentationRequest'] = request(args.get('presentationRequest'))
        descriptor = self.store.get('observer', config['observerId'])
        check(self.app.smart_tools, descriptor)
        for key, schema in (('target', 'targetSchema'), ('scope', 'scopeSchema')):
            try: validate_schema(config[key], descriptor[schema])
            except jsonschema.ValidationError as exc: raise ValueError('Observation ' + key + ' does not match its qualified scope') from exc
        text(config['sourceId'], 'source identity')
        if descriptor['requestArgument'] in config['args']: raise ValueError('Observation identity arguments are host-owned')
        return config

    def reviewed(self, sid, config, args, origin):
        session, task = self.app._session(sid), self.task(sid)
        if task.get('status') != 'active': raise ValueError('The persisted task is not active')
        if session.get('configurationBusy') or session.get('fileAccessPending'): raise ValueError('The conversation has an unresolved configuration change')
        from .observation_presentation import grant
        presentation = grant(self, sid, args, origin)
        binding = {**config, 'presentationGrant': presentation, 'sessionId': sid, 'taskId': task['id'], 'taskRevision': task['revision'],
            'nativeSessionId': session.get('runtimeSessionId') or session.get('nativeIdentity') or sid,
            'executionRevision': session.get('executionRevision', 0), 'interruptionRevision': session.get('interruptionRevision', 0)}
        return {'previewHash': fingerprint([binding, self.full_authorization(sid, origin)]), 'binding': binding, 'handoff': 'one finite background explanation; no product tools', 'expiresInSeconds': config['durationSeconds']}

    def full_authorization(self, sid, origin):
        session = self.app._session(sid)
        if origin in {'agent', 'system', 'voice', 'user'}:
            authority = self.app.access_mode.capture_full(session, origin)
            if authority and 'inputIds' in authority:
                authority['inputIds'] = sorted(set(authority['inputIds']))
            return authority
        return None

    def authorization(self, sid, args, origin, previous=None):
        if origin == 'ui': return {'origin': 'ui', 'at': self.clock()}
        standing = self.full_authorization(sid, origin)
        if standing:
            session = self.app._session(sid)
            if previous:
                retained = previous.get('authorization', {})
                if (previous['interruptionRevision'] != session.get('interruptionRevision', 0)
                        or 'accessRevision' in retained and not self.app.access_mode.full(session, retained['accessRevision'])):
                    raise ValueError('The original Full access or Stop boundary changed; this watch cannot resume itself')
            return {**standing, 'at': self.clock()}
        if origin not in {'agent', 'voice', 'user'}: raise ValueError('Only a real user request can authorize a quiet watch')
        source = next((m for m in self.app._session(sid).get('messages', []) if m.get('id') == args.get('sourceMessageId')), None)
        if not source or source.get('role') != 'user' or source.get('inputOrigin') not in {'ui', 'user', 'voice'} or source.get('questionId') or source.get('scheduledRunId') or source.get('observationId'):
            raise ValueError('Cite the actual human request; background inputs and question replies do not authorize watches')
        if previous and previous.get('authorization', {}).get('messageId') == source['id']:
            raise ValueError('Resuming requires renewed human authority')
        return {'origin': origin, 'messageId': source['id'], 'textDigest': fingerprint(source.get('text', '')), 'at': self.clock()}

    def reason(self, watch):
        from .updates import work_paused
        if work_paused(self.app.state): return 'Work is paused for an application or ecosystem update'
        from .errors import AppError
        try: session = self.app._session(watch['sessionId'])
        except AppError: return 'The authorizing conversation is no longer available'
        if session.get('configurationBusy') or session.get('fileAccessPending'): return 'The execution configuration is changing'
        for key in ('executionRevision', 'interruptionRevision'):
            if session.get(key, 0) != watch[key]: return 'The user stopped or changed the execution context'
        if (session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']) != watch['nativeSessionId']: return 'The native conversation identity changed'
        task = self.task(watch['sessionId'])
        if (task['id'], task['revision'], task.get('status')) != (watch['taskId'], watch['taskRevision'], 'active'): return 'The persisted task authority changed'
        for identity in task.get('questionIds', []):
            question = self.app.questions.store.get(session['id'], identity)
            if question['required'] and question['status'] != 'answered': return 'A required decision remains unanswered'
        auth = watch['authorization']
        if 'accessRevision' in auth:
            if auth.get('sessionId') != session['id'] or not self.app.access_mode.full(session, auth['accessRevision']):
                return 'The saved Full access authority changed'
        elif auth.get('messageId'):
            source = next((m for m in session.get('messages', []) if m.get('id') == auth['messageId']), None)
            if not source or fingerprint(source.get('text', '')) != auth['textDigest']: return 'The authorizing human request is unavailable or changed'
        check(self.app.smart_tools, self.store.get('observer', watch['observerId']))
        return None

    async def dispatch(self, action, args, origin, command_id):
        sid, now = args['sessionId'], self.clock()
        self.app._session(sid)
        if action == 'observation.qualify':
            authorization = {'origin': origin, 'sessionId': sid} if origin == 'ui' else self.full_authorization(sid, origin)
            if authorization is None: raise ValueError('Source qualification requires a trusted operator action or Full access')
            descriptor = qualify(self.app.smart_tools, args['descriptor'])
            descriptor['qualifiedAt'] = now
            # This records who submitted the review, not independent attestation.
            descriptor['authorization'] = {**authorization, 'at': now}
            self.store.put('observer', descriptor)
            return {'observer': descriptor}
        if action == 'observation.observers':
            items = []
            for row in self.store.rows('observer'):
                try: check(self.app.smart_tools, row); reason = None
                except ValueError as exc: reason = str(exc)
                items.append({**row, 'eligible': reason is None, 'reason': reason})
            return {'observers': items}
        if action == 'observation.list': return {'watches': self.store.rows('watch', sid)}
        if action in {'observation.read', 'observation.report'}:
            watch = self.store.get('watch', args['id'], sid)
            return {'watch': watch, 'runs': [r for r in self.store.rows('run', sid) if r['watchId'] == watch['id']], 'handoffs': [r for r in self.store.rows('outbox', sid) if r['watchId'] == watch['id']]}
        if action == 'observation.request':
            rows = [row for row in self.store.rows('watch', sid) if row['requestId'] == args['requestId']]
            return {'watch': rows[0] if rows else None}
        if action == 'observation.preview': return self.reviewed(sid, self.configuration(args), args, origin)
        request = text(args.get('requestId'), 'request identity', 200)
        key = fingerprint([sid, action, request])
        intent = {k:v for k,v in args.items() if k != 'previewHash'} | {'origin': origin}
        with self.store.transaction():
            prior = self.store.command(key, intent)
            if prior:
                return {'watch': self.store.get('watch', prior['id'], sid), 'duplicate': True}
            previous = None if action == 'observation.create' else self.store.get('watch', args['id'], sid)
            if previous and previous['revision'] != args['expectedRevision']: raise ValueError('Observation revision changed')
            if action in {'observation.create', 'observation.resume'}:
                if previous and (previous.get('terminal') or previous['status'] not in {'paused', 'needs_review'}): raise ValueError('Only a nonterminal paused watch can be reviewed and resumed')
                if previous and any(row['watchId'] == previous['id'] for row in self.store.rows('outbox')): raise ValueError('A prior handoff cannot be rearmed')
                config = self.configuration(args)
                review = self.reviewed(sid, config, args, origin)
                if args['previewHash'] != review['previewHash']: raise ValueError('The quiet observation preview changed; review again')
                authorization = self.authorization(sid, args, origin, previous)
                if previous is None and len(self.store.rows('watch', sid)) >= 100: raise ValueError('This conversation already has 100 watches')
                watch = previous or {'id': str(uuid.uuid4()), 'requestId': request, 'createdAt': now, 'sequence': 0}
                watch.update(review['binding'], status='active', authorization=authorization, cursor=None, nextDue=now, expiresAt=now + config['durationSeconds'])
            elif action in {'observation.pause', 'observation.cancel'}:
                watch = previous
                watch['status'] = 'paused' if action.endswith('pause') else 'cancelled'
                self.store.suppress(watch['id'], now)
            else: raise ValueError('Unknown observation action')
            watch.update(revision=(previous['revision'] if previous else 0) + 1, updatedAt=now)
            self.store.put('watch', watch)
            self.store.save_command(key, intent, {'id': watch['id']})
            return {'watch': watch, 'duplicate': False}

    async def tick(self):
        # This timer owns only observation state. It never publishes app snapshots.
        if not self.store.acquire(self.clock()): return
        from .updates import work_paused
        if work_paused(self.app.state): return
        for watch in self.store.rows('watch'):
            if watch['status'] != 'active': continue
            if not self.store.acquire(self.clock()): return
            try: reason = self.reason(watch)
            except (ValueError, OSError) as exc: reason = str(exc)
            if reason:
                self.store.review(watch['id'], reason, self.clock()); continue
            self.store.expire(watch['id'], self.clock())
            run = self.store.claim(watch['id'], self.clock())
            if not run: continue
            def guard():
                current = self.store.get('watch', watch['id'])
                if current['revision'] != run['watchRevision'] or current['status'] != 'active' or self.clock() >= current['expiresAt']:
                    raise ValueError('The watch was revoked or expired before dispatch')
                reason = self.reason(current)
                if reason: raise ValueError(reason)
            try:
                outcome = await self.read(watch, run['id'], guard)
            except asyncio.CancelledError: raise
            except Exception:
                # Tool exception text can contain credentials/private paths; retain a safe fixed failure.
                outcome = {'contract': CONTRACT, 'status': 'observation_failed', 'target': watch['target'], 'source': {'id': watch['sourceId'], 'revision': None},
                    'semanticKey': None, 'observedAt': self.clock(), 'summary': 'The qualified observation could not establish the current recorded state. Review the connection and source; work was not replayed.', 'evidence': []}
            try: reason = self.reason(watch)
            except (ValueError, OSError) as exc: reason = str(exc)
            if reason: self.store.review(watch['id'], reason, self.clock())
            self.store.commit(run, outcome, self.clock())
        await self.handoffs()

    async def read(self, watch, occurrence, guard):
        descriptor = self.store.get('observer', watch['observerId'])
        guard()
        envelope = {'contract': CONTRACT, 'watchId': watch['id'], 'watchRevision': watch['revision'],
                    'occurrenceId': occurrence, 'target': watch['target'], 'scope': watch['scope'], 'cursor': watch.get('cursor')}
        result = await self.app.smart_tools.call_tool(descriptor['connectionId'], descriptor['toolName'],
            {**watch['args'], descriptor['requestArgument']: envelope}, origin='agent', timeout_seconds=watch['readTimeoutSeconds'],
            allowed_tools=[descriptor['toolName']], expected_configuration=descriptor['configurationKey'], dispatch_guard=guard)
        if result.get('isError'): raise ValueError('The qualified observer reported a read failure')
        outcome = validate_result(result.get('structuredContent'), watch)
        tool = check(self.app.smart_tools, descriptor)
        validate_schema(outcome, tool['outputSchema'])
        return outcome

    @staticmethod
    def handoff_args(watch, item):
        outcome = copy.deepcopy(item['outcome'])
        if watch.get('presentationGrant'):
            outcome['presentationReceipt'] = {'status': item.get('presentationPhase', 'not_requested'),
                **({'canvasId': item['canvasId']} if item.get('canvasId') else {}),
                **({'reference': outcome['presentation']['url']} if outcome.get('presentation') else {})}
        return {'inputId': item['inputId'], 'watchId': watch['id'], 'taskId': watch['taskId'], 'taskRevision': watch['taskRevision'], 'outcome': outcome}

    def authorize_admission(self, sid, args):
        item = self.store.get('outbox', args.get('inputId'), sid)
        watch = self.store.get('watch', item['watchId'], sid)
        expected = self.handoff_args(watch, item)
        if args != expected or item['phase'] != 'submitting' or watch['status'] != 'ended':
            return {'admitted': False, 'reason': 'The exact durable observation handoff is no longer authorized'}
        reason = self.reason(watch)
        return {'admitted': reason is None, 'reason': reason}

    async def handoffs(self):
        for item in self.store.rows('outbox'):
            if item['phase'] not in {'pending', 'waiting_worker'}: continue
            watch = self.store.get('watch', item['watchId'])
            if watch['status'] != 'ended':
                self.store.outbox_phase(item['id'], ['pending', 'waiting_worker'], 'suppressed', self.clock()); continue
            try: reason = self.reason(watch)
            except (ValueError, OSError) as exc: reason = str(exc)
            if reason:
                self.store.review(watch['id'], reason, self.clock()); continue
            session = self.app._session(watch['sessionId'])
            if session.get('status') in {'working', 'starting', 'running', 'busy', 'stopping'}: continue
            runtime = self.app.runtime
            if (not runtime or not hasattr(runtime, 'observation_input')
                    or hasattr(runtime, 'observation_available') and not runtime.observation_available(watch['sessionId'])):
                self.store.outbox_phase(item['id'], ['pending'], 'waiting_worker', self.clock(),
                    detail='Result retained. Resume this conversation explicitly to receive its background explanation; no provider was prepared.')
                continue
            current = self.store.outbox_phase(item['id'], ['pending', 'waiting_worker'], 'submitting', self.clock())
            if not current: continue
            def guard():
                latest = self.store.get('watch', watch['id'])
                if latest['status'] != 'ended' or self.store.get('outbox', item['id'])['phase'] != 'submitting': return 'The watch was revoked before admission'
                return self.reason(latest)
            from .observation_presentation import present
            await present(self, watch, current)
            args = self.handoff_args(watch, self.store.get('outbox', item['id']))
            try:
                receipt = await self.app.runtime.observation_input(watch['sessionId'], args, guard)
                phase = 'accepted' if receipt.get('accepted') else 'suppressed'
                self.store.outbox_phase(item['id'], ['submitting'], phase, self.clock(), receipt=bounded(receipt))
            except asyncio.CancelledError:
                self.store.outbox_phase(item['id'], ['submitting'], 'unknown', self.clock(), detail='Admission interrupted; no replay.')
                raise
            except Exception:
                self.store.outbox_phase(item['id'], ['submitting'], 'unknown', self.clock(), detail='Admission outcome is unknown; no replay.')
