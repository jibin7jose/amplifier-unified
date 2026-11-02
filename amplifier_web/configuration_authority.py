"""Bounded configuration grants in the existing host command receipts."""
from contextvars import ContextVar
import asyncio
import copy
import hashlib
import json
import time
import uuid

# Only host code installs this callback; never accept it in action arguments.
CURRENT = ContextVar('configuration_authority', default=None)
EFFECTS = {
    'test_host_setup': 'Set up managed isolated testing on this host using its existing Docker or Incus service',
    'test_environments': 'Copy this task’s current Git working area into an isolated test environment, run checks, stop it, and remove it after retaining source and artifacts',
    'file_access': 'Change file-access settings',
    'connections': 'Change provider connections, executable sources and credentials',
    'host_defaults': 'Change app-wide defaults, update automation and background preparation policy',
    'model_defaults': 'Change saved model and routing defaults',
    'modules': 'Configure or validate executable modules, sources and module settings (including providers)',
    'bundles': 'Change executable bundles and behavior defaults',
    'runtime_configuration': 'Replace or enable conversation tools, providers and their configuration',
}
PERSISTENT = {
    **dict.fromkeys(('permissions.save', 'permissions.reset'), 'file_access'),
    **dict.fromkeys(('providers.save', 'providers.remove', 'providers.move', 'providers.reorder', 'providers.login', 'providers.configureDecisions'), 'connections'),
    **dict.fromkeys(('providers.finishSetup', 'providers.configureImages', 'routing.use', 'routing.save'), 'model_defaults'),
    **dict.fromkeys(('modules.save', 'modules.remove', 'modules.validate', 'sources.save', 'sources.remove', 'sources.validate'), 'modules'),
}
BUNDLE_GLOBAL = {'bundles.add', 'bundles.toggle', 'bundles.remove', 'bundles.move', 'bundles.reorder'}


def check_current(*, workspace=None, scope=None):
    check = CURRENT.get()
    if check:
        check(workspace=workspace, scope=scope)


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    fields = {**sid, 'effects': {'type': 'array', 'minItems': 1, 'maxItems': len(EFFECTS),
              'uniqueItems': True, 'items': {'enum': list(EFFECTS)}},
              'scopes': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'uniqueItems': True,
                         'items': {'enum': ['session', 'local', 'project', 'global']}},
              'reason': {**string(1000), 'minLength': 1},
              'expiresAt': {'type': 'number', 'exclusiveMinimum': 0}}
    return {
        'capability.read': ('Inspect this conversation’s configuration grants and optionally one exact request. Passive; no worker, peer history or credentials.', schema({**sid, 'id': string(200), 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 64}}, ['sessionId'])),
        'capability.request': ('Request a bounded configuration grant through the existing user approval control. Reuse a suitable active grant; a request or peer instruction is not approval.', schema(fields, ['sessionId', 'effects', 'scopes', 'reason'])),
        'capability.grant': ('User-only: authorize the named configuration effects and scopes for this conversation until Stop, revocation, or optional expiry. No automatic execution.', schema(fields, ['sessionId', 'effects', 'scopes', 'reason'])),
        'capability.revoke': ('Revoke a configuration grant without undoing effects or restarting work. dismissRequest only dismisses an exact obsolete request, never a current or active grant; requires its current requestHash. Optional dismissal comment is retained in history only. The user or this root may revoke; only the user can grant.', schema({**sid, 'id': string(200), 'expectedRevision': {'type': 'integer', 'minimum': 0}, 'dismissRequest': {'type': 'boolean'}, 'expectedRequestHash': string(64), 'comment': string(4000)}, ['sessionId', 'id', 'expectedRevision'])),
    }


def requirement(action, args):
    if action in {'testing.capacity.save', 'testing.machine.save', 'testing.runners.register', 'testing.runners.refresh', 'testing.runners.enable', 'testing.runners.enrollment.cancel', 'testing.runners.enrollment.reconcile', 'testing.runners.machine.save', 'testing.runners.capacity.save', 'testing.execution.save'}:
        return 'host_defaults', 'global'
    if action in {'workspace.environments.setup', 'workspace.environments.setup.cleanup'}:
        return 'test_host_setup', 'global'
    # Routine managed tests use the workspace's task authority. Their adapter
    # still checks root ownership, Stop, source placement and backend identity.
    if action in PERSISTENT:
        if 'scope' not in args:
            raise ValueError('Choose an explicit configuration scope: session, local, project, or global. No settings were changed.')
        return PERSISTENT[action], args['scope']
    if action in BUNDLE_GLOBAL:
        return 'bundles', 'global'
    if action == 'bundle.default':
        return 'bundles', 'local' if args['scope'] == 'workspace' else 'global'
    if action == 'bundle.save':
        return 'bundles', 'global'
    if action in {'bundle.switch', 'bundle.fork'} or action == 'conversation.configure' and 'bundle' in args:
        return 'bundles', 'session'
    if action in {'configuration.apply', 'configuration.patchPlacement.bind'}:
        return 'runtime_configuration', 'session'
    if action == 'runtime.control' and args.get('operation') == 'configuration.providerLogin':
        return 'connections', 'session'
    if action == 'runtime.control' and args.get('operation') in {'configuration.apply', 'configuration.toggle'}:
        return 'runtime_configuration', 'session'
    return None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ConfigurationAuthority:
    def __init__(self, service):
        self.service = service
        service.db.execute("""CREATE INDEX IF NOT EXISTS configuration_grants ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.result.sessionId'))""")
        for session in service.state['sessions']:
            self.reconcile(session)
        service.db.commit()

    def error(self, text, status=403):
        from .service import AppError
        raise AppError(text, status)

    async def notify_waiter(self, grant, *, dismissal=False):
        """Resolve a live native tool call only; never start/replay a turn."""
        respond = getattr(self.service.runtime, 'approval', None)
        if grant.get('dismissedAt') and not dismissal:
            return  # Neither old request nor decision retries replay dismissal.
        if not grant.get('nativeWait') or not callable(respond) or grant['status'] == 'pending':
            return
        try:
            result = await asyncio.wait_for(respond(grant['sessionId'], grant['id'],
                'allow' if grant['status'] == 'active' else 'deny',
                **({'comment': grant['comment']} if grant.get('comment') and not dismissal else {})), 5)
            if dismissal and isinstance(result, dict) and result.get('accepted') is True:
                async with self.service.lock:
                    current = self.record(grant['id'])
                    if current.get('dismissedAt') == grant.get('dismissedAt') and current['revision'] == grant['revision']:
                        current['dismissalDelivery'] = 'delivered'
                        self.save(current)
                        self.service._publish_changes(sessions={grant['sessionId']})
        except (RuntimeError, ValueError, KeyError, OSError, TimeoutError):
            # The saved decision remains authoritative after a lost worker or
            # acknowledgement. A later native request reads it without replay.
            pass

    def rows(self, sid, offset=0, limit=65):
        return [json.loads(row[0])['result'] for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='capability.record'
            AND json_extract(receipt,'$.result.sessionId')=?
            ORDER BY json_extract(receipt,'$.result.createdAt') DESC,id LIMIT ? OFFSET ?""", (sid, limit, offset))]

    def reconcile(self, session):
        """Retire obsolete cards and grants without approving or replaying work.

        Visit only this admitted owner's pending cards; dormant history stays cold.
        A native wait dies with its host, while a raw API grant can survive restart.
        """
        changed = False
        for card in session.get('approvals', []):
            if not card.get('id', '').startswith('capability:'):
                continue
            if card.get('status') == 'expired':
                # Older releases retired the record but hid its generic message.
                detail = self.retirement_detail(card.get('resolutionReason'))
                if card.get('detail') != detail:
                    card['detail'] = detail
                    changed = True
            if card.get('status') != 'pending':
                continue
            row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (card['id'],)).fetchone()
            receipt = json.loads(row[0]) if row else {}
            grant = receipt.get('result') if receipt.get('commandAction') == 'capability.record' else None
            reason = None
            if not grant or grant.get('sessionId') != session['id']:
                reason = 'request_unavailable'
            elif grant['status'] != 'pending':
                if (grant['status'] == 'active' and not card.get('_hostRestarted')
                        and grant['workspace'] == session['workspace']
                        and grant['interruptionRevision'] == session.get('interruptionRevision', 0)
                        and (grant.get('expiresAt') is None or grant['expiresAt'] > time.time())):
                    # Full access owns delivery to a still-live native waiter.
                    # Its policy revision may change while Full access remains
                    # effective; only that owner refreshes authority/delivery.
                    continue
                # A saved decision is not a pending request even when its worker
                # acknowledgement was lost. Never revoke that grant here.
                card.update(status={'active': 'allow', 'denied': 'deny'}.get(grant['status'], grant['status']))
                if grant.get('resolutionReason'):
                    card.update(resolutionReason=grant['resolutionReason'],
                                detail=self.retirement_detail(grant['resolutionReason']))
                card.pop('_hostRestarted', None)
                changed = True
                continue
            elif grant['workspace'] != session['workspace']:
                reason = 'workspace_changed'
            elif grant['interruptionRevision'] != session.get('interruptionRevision', 0):
                reason = 'conversation_stopped'
            elif grant.get('expiresAt') is not None and grant['expiresAt'] <= time.time():
                reason = 'expired'
            elif card.get('_hostRestarted') and grant.get('nativeWait'):
                reason = 'host_restarted'
            elif grant['effects'] == ['test_environments'] and grant['scopes'] == ['session']:
                reason = 'approval_not_required'
            card.pop('_hostRestarted', None)
            if reason:
                detail = self.retirement_detail(reason)
                card.update(status='expired', resolutionReason=reason, detail=detail)
                if grant and grant.get('sessionId') == session['id']:
                    grant.update(status='expired', revision=grant['revision'] + 1,
                                 resolvedAt=time.time(), resolutionReason=reason)
                    self.save(grant)
                changed = True
        return changed

    @staticmethod
    def retirement_detail(reason):
        descriptions = {
            'expired': 'The expiry time on this request has passed.',
            'conversation_stopped': 'This request belongs to an earlier run of this chat, which was stopped.',
            'workspace_changed': 'The workspace changed after this request was made.',
            'conversation_changed': 'The conversation boundary changed after this request was made.',
            'host_restarted': 'The app restarted and the original waiting operation ended.',
            'approval_not_required': 'Routine isolated testing no longer needs this permission request.',
        }
        if reason not in descriptions:
            return ('The saved request record is unavailable or its retirement reason is unknown. '
                    'Its approval and earlier effects cannot be confirmed here. Nothing is being retried.')
        return descriptions[reason] + ' This request cannot grant permission. Earlier operation outcomes are unchanged; nothing is being restarted or replayed.'

    def request_view(self, session, identity):
        card = next((row for row in session.get('approvals', []) if row.get('id') == identity), None)
        if not identity.startswith('capability:') or card is None:
            self.error('The configuration request was not found in this conversation.', 404)
        saved = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
        receipt = json.loads(saved[0]) if saved else {}
        grant = receipt.get('result') if receipt.get('commandAction') == 'capability.record' else None
        if grant and grant.get('sessionId') != session['id']:
            grant = None  # Do not disclose or modify another owner's record.
        status = grant['status'] if grant else 'unavailable'
        current = bool(grant and status == 'pending' and card.get('status') == 'pending'
                       and grant['workspace'] == session['workspace']
                       and grant['interruptionRevision'] == session.get('interruptionRevision', 0)
                       and (grant.get('expiresAt') is None or grant['expiresAt'] > time.time()))
        dismissed = bool(card.get('dismissedAt'))
        detail = card.get('detail')
        if current:
            detail = 'This request is current and awaiting your decision. Reviewing it does not start work.'
        elif status == 'active':
            detail = 'This request was already allowed. Current permission validity is shown in scope review; this does not confirm that any operation ran.'
        elif status in {'revoked', 'denied'} and not detail:
            detail = 'This permission was revoked.' if status == 'revoked' else 'This request was denied.'
        eligible = self.eligible(session)
        applicable = [row for row in eligible if self.valid(row, session)]
        covered = bool(grant and all(any(effect in row['effects'] and scope in row['scopes']
                     for row in applicable) for effect in grant['effects'] for scope in grant['scopes']))
        if not grant or not covered and len(eligible) >= 65:
            covered = None  # Missing original scope or a bounded coverage read.
        return {'id': identity, 'status': status, 'revision': grant['revision'] if grant else 0,
                'requestHash': fingerprint([session['id'], session['workspace'], session.get('interruptionRevision', 0),
                                           session.get('executionRevision', 0), receipt, card]),
                'canDecide': current, 'canDismiss': not dismissed and (status == 'expired' or grant is None),
                'dismissed': dismissed, 'resolutionReason': card.get('resolutionReason'),
                'detail': detail or self.retirement_detail(None), 'scopeCovered': covered,
                'reason': grant.get('reason') if grant else card.get('prompt'),
                'effects': grant.get('effects', []) if grant else [], 'scopes': grant.get('scopes', []) if grant else [],
                'dismissalDelivery': grant.get('dismissalDelivery') if grant else None,
                'expiresAt': grant.get('expiresAt') if grant else None}

    def dismiss_request(self, source, args, identity, digest):
        """Settle only a reviewed obsolete request; use the existing revoke receipt."""
        self.reconcile(source)
        view = self.request_view(source, args['id'])
        if not args.get('expectedRequestHash') or args['expectedRequestHash'] != view['requestHash'] or args['expectedRevision'] != view['revision']:
            self.error('The request changed. Review its current state before dismissing it.', 409)
        if not view['canDismiss']:
            self.error('Only an obsolete request can be dismissed. Current permission was not changed.', 409)
        card = next(row for row in source['approvals'] if row['id'] == args['id'])
        before = copy.deepcopy(card)
        self.service.db.commit()
        try:
            card.update(status='revoked', dismissedAt=time.time())
            if args.get('comment', '').strip():
                card['dismissalComment'] = args['comment'].strip()
            grant = self.record(args['id']) if view['revision'] else None
            if grant:
                grant.update(status='revoked', revision=grant['revision']+1, revokedAt=card['dismissedAt'],
                             dismissedAt=card['dismissedAt'],
                             dismissalDelivery='unconfirmed' if grant.get('nativeWait') else 'not_required')
                if card.get('dismissalComment'):
                    grant['dismissalComment'] = card['dismissalComment']
                self.save(grant)
            result = grant or {'id':args['id'], 'status':'unavailable', 'dismissed':True}
            receipt = {'accepted':True, 'commandAction':'capability.revoke', 'result':result,
                       'request':self.request_view(source, args['id'])}
            self.service.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, digest, json.dumps(receipt)))
            self.service._publish_changes(sessions={source['id']})
        except BaseException:
            self.service.db.rollback()
            if not self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
                card.clear()
                card.update(before)
            raise
        if grant:
            self.service._task(self.notify_waiter(grant, dismissal=True))
        return receipt

    def eligible(self, session):
        return [json.loads(row[0])['result'] for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='capability.record'
            AND json_extract(receipt,'$.result.sessionId')=?
            AND json_extract(receipt,'$.result.status') IN ('pending','active')
            AND json_extract(receipt,'$.result.workspace')=?
            AND json_extract(receipt,'$.result.interruptionRevision')=?
            AND (json_extract(receipt,'$.result.expiresAt') IS NULL OR json_extract(receipt,'$.result.expiresAt')>?)
            ORDER BY json_extract(receipt,'$.result.createdAt') DESC,id LIMIT 65""", (session['id'], session['workspace'], session.get('interruptionRevision', 0), time.time()))]

    def record(self, identity):
        row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
        value = json.loads(row[0]) if row else {}
        if value.get('commandAction') != 'capability.record':
            self.error('Configuration grant was not found.', 404)
        return value['result']

    def save(self, grant):
        self.service.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',
            (grant['id'], 'host-configuration-authority', json.dumps({'commandAction': 'capability.record', 'result': grant})))

    def valid(self, grant, session):
        return (grant['status'] == 'active' and grant['workspace'] == session['workspace']
                and grant['interruptionRevision'] == session.get('interruptionRevision', 0)
                and (grant.get('expiresAt') is None or grant['expiresAt'] > time.time())
                and (not grant.get('accessRevision') or self.service.access_mode.full(session, grant['accessRevision'])))

    def snapshot(self, session, offset=0, limit=64):
        rows = self.rows(session['id'], offset, limit+1)
        return {'sessionId': session['id'], 'accessMode': self.service.access_mode.snapshot(session)['mode'], 'grants': [{**row, 'usable': self.valid(row, session)} for row in rows[:limit]],
                'nextOffset': offset+limit if len(rows)>limit else None, 'limit': limit, 'truncated': len(rows) > limit, 'effects': EFFECTS,
                'scope': 'configuration only; no shell confinement or peer authority'}

    def source(self, args, origin, caller, *, active=False, allow_descendant=False):
        source = self.service.collaboration.source(args, origin, caller, allow_descendant=allow_descendant)
        if origin not in {'ui', 'user'}:
            if args.get('sessionId', caller) != caller:
                self.error('Configuration grants belong to the calling conversation.')
            if active:
                from .collaboration import BINDING, PRINCIPAL
                # Fresh trusted system work has an explicit conversation owner,
                # not an invented native generation. A transported worker still
                # has to satisfy its actual binding, even with system origin.
                system = (allow_descendant and origin == 'system' and not PRINCIPAL.get()
                          and not BINDING.get() and self.service.access_mode.full(source))
                if not system:
                    self.service.collaboration.active_binding(source, allow_descendant=allow_descendant)
        return source

    def continuation_inputs(self, original):
        """Unknown observation ancestry is not an empty, human-rooted input.

        Older creation receipts retained the exact capture under result metadata;
        use that host-owned provenance, never current inputs or matching prose.
        """
        kind = original.get('commandAction')
        lineage = original
        if kind == 'coordination.create' and 'sourceInputIds' not in original:
            metadata = original.get('result') or {}
            if (metadata.get('creatorSessionId') == original.get('senderSessionId')
                    and metadata.get('requestId') == original.get('requestId')):
                lineage = metadata
        inputs = lineage.get('sourceInputIds')
        if (kind not in {'coordination.send', 'coordination.watch', 'coordination.create', 'workspace.tests.submit', 'worktree.handoff'}
                or not isinstance(inputs, list) or len(inputs) > 128
                or any(not isinstance(item, str) or not item for item in inputs)
                or type(lineage.get('sourceInterruptionRevision')) is not int
                or (kind == 'worktree.handoff' or original.get('origin') not in {'ui', 'user'}) and (
                    not lineage.get('sourceGenerationId') or not inputs)):
            self.error('The saved return has no verified original input lineage. Ask the user to authorize this current request before changing configuration.')
        return inputs

    def peer_authorities(self, source, grant, effect, scope):
        """A lower-authority input cannot borrow a more privileged recipient.

        Human-reviewed current inputs and the recipient's own saved dependency
        continuations retain their original authority. Other peer requests carry
        the intersection of their authenticated senders, bounded through lineage.
        """
        checked, evidence = set(), []
        def visit(target, selected, inputs, depth=0):
            if depth > 8 or len(checked) > 64:
                self.error('Peer configuration lineage exceeds the supported bound. Ask the user to authorize this conversation’s current request.')
            for identity in inputs:
                key = (target['id'], identity)
                if key in checked or identity in selected.get('authorizedInputIds', []):
                    continue
                checked.add(key)
                if len(checked) > 64:
                    self.error('Peer configuration lineage exceeds the supported bound. Ask the user to authorize the current request.')
                raw = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
                receipt = json.loads(raw[0]) if raw else {}
                if receipt.get('commandAction') != 'coordination.send' or receipt.get('target', {}).get('sessionId') != target['id']:
                    continue  # Ordinary human/scheduled input, not a peer grant.
                if receipt.get('origin') in {'ui', 'user'}:
                    continue
                if receipt.get('origin') == 'system' and receipt.get('dependencyRequestId'):
                    original = self.service.collaboration.receipt(receipt['dependencyRequestId'])
                    if original.get('senderSessionId') == target['id']:
                        visit(target, selected, self.continuation_inputs(original), depth+1)
                        continue  # Exact saved wait owned by this root.
                sender = self.service._session(receipt['senderSessionId'], hydrate=False)
                inherited = next((row for row in self.eligible(sender) if self.valid(row, sender)
                                  and effect in row['effects'] and scope in row['scopes']), None)
                if inherited is None:
                    self.error('This peer request cannot borrow the recipient’s configuration authority. The user can authorize the current request with capability.request.')
                evidence.append({'grantId': inherited['id'], 'grantRevision': inherited['revision'], 'sessionId':sender['id']})
                visit(sender, inherited, receipt.get('sourceInputIds', []), depth+1)
        visit(source, grant, (source.get('collaborationGeneration') or {}).get('inputIds', []))
        return evidence

    def capture(self, action, args, origin, caller):
        if origin in {'ui', 'user'}:
            return None
        if action == 'settings.update':
            patch = args.get('patch', {})
            effects = []
            if 'bundle' in patch:
                effects.append('bundles')
            if set(patch) - {'bundle'}:
                effects.append('host_defaults')
            return {'chain': [self._capture(action, args, origin, caller, (effect, 'global')) for effect in effects]}
        if action == 'runtime.retention.update':
            return self._capture(action, args, origin, caller, ('host_defaults', 'global'))
        return self._capture(action, args, origin, caller)

    def _capture(self, action, args, origin, caller, needed=None):
        if origin in {'ui', 'user'}:
            return None
        try:
            needed = needed or requirement(action, args)
        except ValueError as exc:
            self.error(str(exc), 400)
        if needed is None:
            return None
        source = self.source({'sessionId': caller}, origin, caller, active=True, allow_descendant=True)
        from .configuration_target import resolve
        target = resolve(self.service, args)
        workspace = target['workspace'] if target else args.get('workspace') or source['workspace']
        if needed[1] != 'global' and workspace != source['workspace']:
            self.error('A configuration grant cannot change another workspace.')
        effect, scope = needed
        if self.service.access_mode.full(source):
            from .collaboration import BINDING, PRINCIPAL
            binding = BINDING.get() or {}
            # Missing or overflowed transport is not a proven empty assignment.
            input_ids = [row['inputId'] for row in binding.get('_inputBindings', []) if row.get('inputId')]
            return {'accessRevision': self.service.access_mode.snapshot(source)['revision'],
                    'sessionId': source['id'], 'workspace': source['workspace'],
                    'interruptionRevision': source.get('interruptionRevision', 0), 'effect': effect, 'scope': scope,
                    'origin': origin, 'runtimeSessionId': PRINCIPAL.get(),
                    'generationId': binding.get('_generationId'),
                    'inputIds': input_ids, 'inputCoverage': 'complete' if input_ids else 'unavailable'}
        from .service import AppError
        grant, peer_grants, failure = None, [], None
        for candidate in self.eligible(source):
            if effect not in candidate['effects'] or scope not in candidate['scopes'] or not self.valid(candidate, source):
                continue
            try:
                peer_grants = self.peer_authorities(source, candidate, effect, scope)
            except AppError as exc:
                failure = exc
                continue
            grant = candidate
            break
        if grant is None:
            if failure:
                raise failure
            self.error(f'Configuration authority is required for {effect} in {scope} scope. Use capability.request, or ask the user to perform this action.')
        from .collaboration import BINDING
        return {'grantId': grant['id'], 'grantRevision': grant['revision'], 'sessionId': source['id'],
                'workspace': source['workspace'], 'interruptionRevision': source.get('interruptionRevision', 0),
                'effect': effect, 'scope': scope, 'generationId': (BINDING.get() or {}).get('_generationId'),
                'peerGrants': peer_grants}

    def check(self, authority, *, workspace=None, scope=None):
        if authority is None:
            return
        if 'chain' in authority:
            for item in authority['chain']:
                self.check(item, workspace=workspace, scope=scope)
            return
        session = self.service._session(authority['sessionId'], hydrate=False)
        if 'accessRevision' in authority:
            if (not self.service.access_mode.full(session, authority['accessRevision'])
                    or session['workspace'] != authority['workspace']
                    or session.get('interruptionRevision', 0) != authority['interruptionRevision']
                    or scope is not None and scope != authority['scope']
                    or scope != 'global' and workspace is not None and str(workspace) != authority['workspace']):
                self.error('Full access changed or the conversation was stopped or moved. The delayed change was not repeated.')
            return
        grant = self.record(authority['grantId'])
        if (grant['sessionId'] != session['id'] or grant['revision'] != authority['grantRevision']
                or not self.valid(grant, session) or authority['effect'] not in grant['effects']
                or authority['scope'] not in grant['scopes'] or session['workspace'] != authority['workspace']
                or session.get('interruptionRevision', 0) != authority['interruptionRevision']):
            self.error('Configuration authority expired, was revoked, or the conversation was stopped or moved. The delayed change was not repeated.')
        for peer in authority.get('peerGrants', []):
            inherited = self.record(peer['grantId'])
            owner = self.service._session(peer['sessionId'], hydrate=False)
            if (inherited['sessionId'] != owner['id'] or inherited['revision'] != peer['grantRevision']
                    or not self.valid(inherited, owner)):
                self.error('The originating peer’s configuration authority changed. The delayed change was not repeated.')
        if scope is not None and scope != authority['scope']:
            self.error('The configuration effect exceeded its authorized scope.')
        if scope != 'global' and workspace is not None and str(workspace) != authority['workspace']:
            self.error('The configuration effect exceeded its authorized workspace.')

    async def dispatch(self, action, args, origin, command_id, caller):
        source = self.source(args, origin, caller, active=action != 'capability.read',
                             allow_descendant=action == 'capability.read')
        if action == 'capability.read':
            if self.reconcile(source):
                self.service._publish_changes(sessions={source['id']})
            result = self.snapshot(source, args.get('offset', 0), args.get('limit', 64))
            if args.get('id'):
                result['request'] = self.request_view(source, args['id'])
            return {'accepted': True, 'result': result}
        if action == 'capability.grant' and origin not in {'ui', 'user'}:
            self.error('Only the user can grant configuration authority.')
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source['id']])
        bound_keys = ('id', 'workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')
        captured = tuple(source.get(key) for key in bound_keys)
        async with self.service.lock:
            previous = self.service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if previous:
                if previous[0] != digest:
                    self.error('This command ID was already used with different contents.', 409)
                receipt = json.loads(previous[1])
                if args.get('dismissRequest'):
                    return {**receipt, 'duplicate': True}
                grant = self.record(receipt['result']['id'])
                self.service._task(self.notify_waiter(grant))
                return {**receipt, 'result': grant, 'duplicate': True}
            # Awaiting the shared commit lock can outlive this generation or
            # Stop/placement boundary. A stale proposal must not bind itself to
            # the newer task state merely because the dict changed in place.
            source = self.source(args, origin, caller, active=True)
            if origin not in {'ui', 'user'} and captured != tuple(source.get(key) for key in bound_keys):
                self.error('The task authority changed while waiting; no configuration request was saved.', 409)
            if action == 'capability.revoke' and args.get('dismissRequest'):
                return self.dismiss_request(source, args, identity, digest)
            # A failed transaction must not roll back earlier unrelated receipts.
            self.service.db.commit()
            before = copy.deepcopy(source.get('approvals', []))
            try:
                if action == 'capability.revoke':
                    grant = self.record(args['id'])
                    if grant['sessionId'] != source['id']:
                        self.error('The grant belongs to another conversation.')
                    if grant['revision'] != args['expectedRevision']:
                        self.error('The grant changed. Read its current revision.', 409)
                    grant.update(status='revoked', revision=grant['revision'] + 1, revokedAt=time.time())
                    for row in source.get('approvals', []):
                        if row['id'] == grant['id']:
                            row['status'] = 'revoked'
                else:
                    full = self.service.access_mode.full(source)
                    routine_test = args['effects'] == ['test_environments'] and args['scopes'] == ['session']
                    if not full and not routine_test and len(self.eligible(source)) >= 64:
                        self.error('This conversation already has 64 active or pending configuration grants.', 409)
                    if args.get('expiresAt', time.time() + 1) <= time.time():
                        self.error('Choose a future expiry.', 400)
                    grant = {key: copy.deepcopy(args[key]) for key in ('effects', 'scopes', 'reason', 'expiresAt') if key in args}
                    grant.update(id='capability:' + str(uuid.uuid5(uuid.NAMESPACE_URL, identity)), sessionId=source['id'],
                        workspace=source['workspace'], interruptionRevision=source.get('interruptionRevision', 0), revision=1,
                        status='active' if action == 'capability.grant' or full or routine_test else 'pending', createdAt=time.time(),
                        origin=origin, approvedBy='user' if action == 'capability.grant' else None,
                        authorizedInputIds=list((source.get('collaborationGeneration') or {}).get('inputIds', [])))
                    from .collaboration import BINDING
                    grant['nativeWait'] = action == 'capability.request' and (BINDING.get() or {}).get('_configurationWait') is True
                    if full and action == 'capability.request':
                        grant.update(approvedBy='full-access', accessRevision=self.service.access_mode.snapshot(source)['revision'])
                    if routine_test and action == 'capability.request' and not full:
                        grant.update(approvedBy='workspace-task')
                    if action == 'capability.request' and not full and not routine_test:
                        source.setdefault('approvals', []).append({'id': grant['id'], 'status': 'pending',
                            'title': 'Allow configuration changes?', 'prompt': grant['reason'],
                            'details': {'Changes': [EFFECTS[key] for key in grant['effects']], 'Scopes': grant['scopes'],
                                        'Applies to': 'This conversation only, until Stop, revocation, or expiry.',
                                        **({'Expires at': grant['expiresAt']} if grant.get('expiresAt') else {})}})
                self.save(grant)
                receipt = {'accepted': True, 'commandAction': action, 'result': grant}
                self.service.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, digest, json.dumps(receipt)))
                self.service._publish_changes(sessions={source['id']})
                self.service._task(self.notify_waiter(grant))
                return receipt
            except BaseException:
                self.service.db.rollback()
                retained = self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
                if not retained:
                    source['approvals'] = before
                raise

    def retain_comment(self, session, grant, args, *, expired=False):
        comment = args.get('comment', '').strip()
        if not comment:
            return
        grant['comment'] = comment
        self.save(grant)
        for card in session.get('approvals', []):
            if card['id'] == grant['id']:
                card['comment'] = comment
        decision = 'expired (note retained only)' if expired else ('allow' if grant['status'] == 'active' else 'deny')
        self.service._message(session, 'user', f'Permission {decision}:\n\n{comment}', 'permission',
                              approvalId=grant['id'], inputOrigin='approval')

    async def decide(self, args, origin, command_id):
        if origin not in {'ui', 'user'}:
            self.error('Only the user can answer a configuration approval.')
        sid = args.get('sessionId') or self.service.state.get('selectedSessionId')
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint(['approval.respond', {**args, 'sessionId': sid}, origin])
        async with self.service.lock:
            previous = self.service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if previous:
                if previous[0] != digest:
                    self.error('This command ID was already used with different contents.', 409)
                receipt = json.loads(previous[1])
                grant = self.record(receipt['result']['id'])
                if grant['status'] != 'expired':
                    self.service._task(self.notify_waiter(grant))
                return {**receipt, 'duplicate': True}
            grant = self.record(args['id'])
            if grant['sessionId'] != sid:
                self.error('The approval belongs to another conversation.')
            session = self.service._session(sid)
            # A stale browser can still post an old Allow/Deny click. Clear its
            # exact card and acknowledge the no-op, never revive that authority.
            self.reconcile(session)
            grant = self.record(args['id'])
            if grant['status'] == 'expired':
                before = copy.deepcopy(session.get('approvals', []))
                messages_before = list(session.get('messages', []))
                # Retirement is independent of the user's optional new note.
                self.service.db.commit()
                try:
                    self.retain_comment(session, grant, args, expired=True)
                    receipt = {'accepted': True, 'commandAction': 'approval.respond',
                               'decisionApplied': False, 'result': grant}
                    self.service.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, digest, json.dumps(receipt)))
                    self.service._publish_changes(sessions={sid})
                except BaseException:
                    self.service.db.rollback()
                    if not self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
                        session['approvals'] = before
                        session['messages'] = messages_before
                    raise
                return receipt
            if grant['status'] != 'pending':
                self.error('This configuration approval is no longer pending.', 409)
            if args.get('expectedRequestHash') and args['expectedRequestHash'] != self.request_view(session, args['id'])['requestHash']:
                self.error('The request changed. Review its current scope before deciding.', 409)
            if (grant['workspace'] != session['workspace'] or grant['interruptionRevision'] != session.get('interruptionRevision', 0)
                    or grant.get('expiresAt', time.time() + 1) <= time.time()):
                self.error('The configuration request expired or the conversation changed. Request current scope again.', 409)
            grant.update(status='active' if args['decision'] in {'allow', 'approve'} else 'denied', revision=grant['revision'] + 1,
                         approvedBy='user', decidedAt=time.time(), decisionCommandId=identity)
            before = copy.deepcopy(session.get('approvals', []))
            messages_before = list(session.get('messages', []))
            self.service.db.commit()
            try:
                self.retain_comment(session, grant, args)
                for row in session.get('approvals', []):
                    if row['id'] == grant['id']:
                        row['status'] = 'allow' if grant['status'] == 'active' else 'deny'
                self.save(grant)
                receipt = {'accepted': True, 'commandAction': 'approval.respond', 'result': grant}
                self.service.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, digest, json.dumps(receipt)))
                self.service._publish_changes(sessions={sid})
                self.service._task(self.notify_waiter(grant))
            except BaseException:
                self.service.db.rollback()
                retained = self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
                if not retained:
                    session['approvals'] = before
                    session['messages'] = messages_before
                raise
            return receipt
