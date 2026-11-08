"""Task-scoped explicit target selection and durable remote request admission.

Only configured endpoint metadata and immutable transfer digests are stored in
the app database. SSH authentication stays with the host's existing SSH setup.
"""

import asyncio
from datetime import datetime, timezone
import json
import re

from amplifier_publishing import PublishingError
from amplifier_publishing.remote import SSHClient, canonical, digest


def definitions(schema, string):
    task = {'sessionId': string(200)}
    target = {'targetId': {**string(100), 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$'}}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 0}}
    return {
        'publishing.target.list': ('List this task’s configured publishing targets and explicit selection. Does not connect or install anything.', schema(task)),
        'publishing.target.save': ('Save this task’s explicit existing SSH service configuration. This does not connect, install, select or deploy. An edit invalidates the prior inspection and selection of that configuration.', schema({**task, **target, **revision, 'label': {**string(200), 'minLength': 1}, 'hostname': {**string(253), 'minLength': 1}, 'username': string(64), 'python': {**string(4000), 'minLength': 1}, 'socketPath': {**string(4000), 'minLength': 1}, 'expectedBind': string(32)}, ['sessionId', 'targetId', 'expectedRevision', 'label', 'hostname', 'python', 'socketPath'])),
        'publishing.target.inspect': ('Read the configured service’s durable identity and private capabilities over SSH, binding that inspection to the current configuration revision. Does not install or publish.', schema({**task, **target, **revision})),
        'publishing.target.select': ('Explicitly select an inspected target for this task using its exact configuration revision and observed serviceId. Existing admitted requests retain their original target.', schema({**task, **target, **revision, 'serviceId': string(200)}, ['sessionId', 'targetId', 'expectedRevision'])),
        'publishing.target.remove': ('Unregister an unused, unselected target from this task, retaining configuration history. Targets with admitted publishing requests stay available for lifecycle control and audit. This does not stop remote sites or remove remote files.', schema({**task, **target, **revision})),
    }


def _now():
    return datetime.now(timezone.utc).isoformat()


def _identifier(value, name, limit=200):
    if not isinstance(value, str) or len(value) > limit or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', value):
        raise PublishingError('invalid_argument', f'{name} must be a bounded plain identifier')
    return value


class PublishingTargets:
    """All database access stays on the app event loop; only SSH runs in workers."""

    def __init__(self, app, local_target, *, read_only=False):
        self.app = app
        self.local_target = local_target
        self.lock = asyncio.Lock()
        self.read_only = read_only
        if read_only:
            return
        app.db.execute('CREATE TABLE IF NOT EXISTS publishing_targets (session TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(session,id))')
        app.db.execute('CREATE TABLE IF NOT EXISTS publishing_target_selection (session TEXT PRIMARY KEY, body TEXT NOT NULL)')
        app.db.execute('CREATE TABLE IF NOT EXISTS publishing_target_requests (session TEXT, request TEXT, signature TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(session,request))')
        # A committed admission is not evidence that its remote operation failed.
        for sid, rid, signature, body in app.db.execute('SELECT session,request,signature,body FROM publishing_target_requests').fetchall():
            record = json.loads(body)
            if record['state'] == 'running':
                record.update(state='unknown', completedAt=_now(), error={'code': 'unknown_outcome', 'message': 'The app ended before recording this remote outcome. The operation was not replayed.'})
                self._put_request(record, signature, commit=False)
        app.db.commit()

    def rows(self, table, columns, suffix='', params=()):
        """Read an optional retained table without creating or recovering it."""
        if self.read_only and not self.app.db.execute(
                'SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone():
            return []
        return self.app.db.execute(f'SELECT {columns} FROM {table} {suffix}', params).fetchall()

    def _local(self):
        return {**self.local_target(), 'id': 'loopback', 'kind': 'loopback', 'label': 'This server (loopback)', 'revision': 0, 'inspection': None, 'removed': False}

    def _row(self, sid, target_id, *, removed=False):
        if target_id == 'loopback':
            return self._local()
        row = self.app.db.execute('SELECT body FROM publishing_targets WHERE session=? AND id=?', (sid, target_id)).fetchone()
        target = json.loads(row[0]) if row else None
        if target is None or (target.get('removed') and not removed):
            raise PublishingError('not_found', 'Publishing target is not available in this task')
        return target

    def _selection(self, sid):
        rows = self.rows('publishing_target_selection', 'body', 'WHERE session=?', (sid,))
        row = rows[0] if rows else None
        return json.loads(row[0]) if row else {'targetId': 'loopback', 'revision': 0, 'targetRevision': 0, 'serviceId': None}

    def list(self, sid):
        _identifier(sid, 'sessionId')
        retained = []
        for identity, raw in self.rows('publishing_targets', 'id,body', 'WHERE session=? ORDER BY id', (sid,)):
            row = json.loads(raw)
            if not isinstance(row, dict) or row.get('id') != identity:
                raise PublishingError('retained_unreadable', 'Retained target differs from its database identity')
            retained.append(row)
        targets = [self._local()] + [row for row in retained if not row.get('removed')]
        selected = self._selection(sid)
        target = next((t for t in targets if t['id'] == selected['targetId']), None)
        if target is None:
            raise PublishingError('target_unavailable', 'Selected publishing target is unavailable; explicitly select loopback or another inspected target')
        return {'targets': targets, 'selectedTargetId': selected['targetId'], 'selectionRevision': selected['revision'], 'selectedTargetRevision': selected['targetRevision'], 'selectedServiceId': selected['serviceId'], 'target': target,
                **({'retainedTargets': retained, 'retainedRequests': self._requests(sid),
                    'historicalOnly': True, 'liveness': 'unconfirmed'} if self.read_only else {})}

    @staticmethod
    def _revision(target, expected):
        if type(expected) is not int or expected < 0:
            raise PublishingError('invalid_argument', 'expectedRevision must be a nonnegative integer')
        if target['revision'] != expected:
            raise PublishingError('stale_revision', f"Target configuration changed; current revision is {target['revision']}")

    @staticmethod
    def _client(target, *, inspected=True):
        inspection = target.get('inspection')
        if inspected and (not inspection or inspection['revision'] != target['revision']):
            raise PublishingError('inspection_required', 'Inspect this exact target configuration before using it')
        options = dict(hostname=target['hostname'], username=target.get('username'), python=target['python'], socket_path=target['socketPath'], expected_bind=target['expectedBind'])
        if inspected:
            options['expected_service_id'] = inspection['serviceId']
        return SSHClient(**options)

    def resolve(self, sid, target_id=None, *, require_selected=False):
        selected = self._selection(sid)
        target = self._row(sid, target_id if target_id is not None else selected['targetId'])
        if target['id'] == 'loopback':
            return target
        inspection = target.get('inspection')
        if not inspection or inspection['revision'] != target['revision']:
            raise PublishingError('inspection_required', 'Inspect this exact target configuration before using it')
        if require_selected or target_id is None:
            if selected['targetId'] != target['id'] or selected['targetRevision'] != target['revision'] or selected['serviceId'] != inspection['serviceId']:
                raise PublishingError('selection_required', 'Explicitly select this inspected configuration and service identity before publishing')
        return target

    def _put_target(self, sid, target):
        self.app.db.execute('INSERT OR REPLACE INTO publishing_targets VALUES (?,?,?)', (sid, target['id'], canonical(target)))
        self.app.db.commit()

    def save(self, sid, args):
        target_id = _identifier(args['targetId'], 'targetId', 100)
        if target_id == 'loopback':
            raise PublishingError('invalid_target', 'The built-in loopback target cannot be edited')
        expected = args['expectedRevision']
        row = self.app.db.execute('SELECT body FROM publishing_targets WHERE session=? AND id=?', (sid, target_id)).fetchone()
        previous = json.loads(row[0]) if row else {'revision': 0}
        if previous.get('removed'):
            raise PublishingError('target_removed', 'This target ID is retained for history; save a new target ID')
        self._revision(previous, expected)
        if any(r['targetId'] == target_id for r in self._requests(sid)):
            raise PublishingError('target_in_use', 'This target has retained publishing requests; save changed configuration under a new target ID')
        label = args['label']
        if not isinstance(label, str) or not label.strip() or len(label) > 200 or any(ord(c) < 32 for c in label):
            raise PublishingError('invalid_argument', 'Target label must be a short plain label')
        target = {'id': target_id, 'kind': 'ssh', 'label': label.strip(), 'revision': previous['revision'] + 1,
                  'hostname': args['hostname'], 'username': args.get('username') or None, 'python': args['python'],
                  'socketPath': args['socketPath'], 'expectedBind': args.get('expectedBind', '127.0.0.1'),
                  'inspection': None, 'removed': False, 'updatedAt': _now()}
        self._client(target, inspected=False)  # Validate only; constructors never connect.
        self._put_target(sid, target)
        return self.list(sid)

    async def inspect(self, sid, args):
        target = self._row(sid, args['targetId'])
        self._revision(target, args['expectedRevision'])
        if target['id'] == 'loopback':
            return self.list(sid)
        capabilities = await asyncio.to_thread(self._client(target, inspected=False).verify_target)
        service_id = _identifier(capabilities.get('serviceId'), 'serviceId')
        # A caller outside the serialized dispatch may have edited while SSH ran.
        current = self._row(sid, target['id'])
        self._revision(current, target['revision'])
        if current.get('inspection') and current['inspection']['serviceId'] != service_id and any(r['targetId'] == target['id'] for r in self._requests(sid)):
            raise PublishingError('target_mismatch', 'The service identity changed for a target with retained publishing requests; its original identity was preserved')
        current['inspection'] = {'revision': current['revision'], 'serviceId': service_id, 'capabilities': capabilities, 'inspectedAt': _now()}
        current['accessPolicy'] = capabilities['accessPolicy']
        self._put_target(sid, current)
        return self.list(sid)

    def select(self, sid, args):
        target = self._row(sid, args['targetId'])
        self._revision(target, args['expectedRevision'])
        service_id = None
        if target['id'] != 'loopback':
            inspection = target.get('inspection')
            if not inspection or inspection['revision'] != target['revision']:
                raise PublishingError('inspection_required', 'Inspect this exact target configuration before selecting it')
            service_id = inspection['serviceId']
            if args.get('serviceId') != service_id:
                raise PublishingError('service_mismatch', 'Select using the exact serviceId returned by the current inspection')
        elif args.get('serviceId') is not None:
            raise PublishingError('invalid_argument', 'The local loopback target has no remote serviceId')
        previous = self._selection(sid)
        selection = {'targetId': target['id'], 'targetRevision': target['revision'], 'serviceId': service_id}
        if all(previous.get(key) == value for key, value in selection.items()):
            return self.list(sid)
        selection.update(revision=previous['revision'] + 1, selectedAt=_now())
        self.app.db.execute('INSERT OR REPLACE INTO publishing_target_selection VALUES (?,?)', (sid, canonical(selection)))
        self.app.db.commit()
        return self.list(sid)

    def remove(self, sid, args):
        target = self._row(sid, args['targetId'])
        self._revision(target, args['expectedRevision'])
        if target['id'] == 'loopback' or self._selection(sid)['targetId'] == target['id']:
            raise PublishingError('target_selected', 'Explicitly select another target before unregistering this one')
        if any(r['targetId'] == target['id'] for r in self._requests(sid)):
            raise PublishingError('target_in_use', 'This target has retained publishing requests; keep it registered for lifecycle control and audit')
        target.update(removed=True, revision=target['revision'] + 1, removedAt=_now())
        self._put_target(sid, target)
        return self.list(sid)

    async def dispatch(self, action, args):
        if self.read_only and action != 'publishing.target.list':
            from .publishing import UNAVAILABLE_REASON
            return {'accepted': False, 'effect': 'none', 'code': 'controller_unavailable',
                    'available': False, 'reason': UNAVAILABLE_REASON}
        fields = {'list': set(), 'save': {'targetId', 'label', 'hostname', 'python', 'socketPath', 'expectedRevision'}, 'inspect': {'targetId', 'expectedRevision'}, 'select': {'targetId', 'expectedRevision'}, 'remove': {'targetId', 'expectedRevision'}}
        operation = action.removeprefix('publishing.target.')
        optional = {'save': {'username', 'expectedBind'}, 'select': {'serviceId'}}.get(operation, set())
        required = fields.get(operation)
        if required is None or not required | {'sessionId'} <= set(args) or set(args) - (required | optional | {'sessionId'}):
            raise PublishingError('invalid_argument', 'Unknown target action or missing/extra fields')
        sid = _identifier(args['sessionId'], 'sessionId')
        # The caller’s task authorization remains enforced by the shared app
        # action dispatcher. This also rejects nonexistent/deleted task IDs.
        self.app._session(sid)
        async with self.lock:
            if operation == 'list':
                return self.list(sid)
            if operation == 'inspect':
                return await self.inspect(sid, args)
            return getattr(self, operation)(sid, args)

    def _requests(self, sid):
        records = []
        for request, raw in self.rows('publishing_target_requests', 'request,body', 'WHERE session=? ORDER BY rowid', (sid,)):
            row = json.loads(raw)
            if not isinstance(row, dict) or row.get('sessionId') != sid or row.get('requestId') != request:
                raise PublishingError('retained_unreadable', 'Retained request differs from its database ownership')
            records.append(row)
        return records

    def lookup_request(self, sid, request_id):
        row = self.app.db.execute('SELECT body FROM publishing_target_requests WHERE session=? AND request=?', (sid, request_id)).fetchone()
        return json.loads(row[0]) if row else None

    async def resume_request(self, binding):
        """Return (handled, result) before capture; unknowns only read receipts."""
        record = self.lookup_request(binding['sessionId'], binding['requestId'])
        if record is None or record['signature'] != binding['signature']:
            raise PublishingError('request_conflict', 'Request binding differs from its durable admission')
        if record['state'] == 'bound':
            return False, None
        if record['state'] in {'running', 'unknown'}:
            await self.reconcile(record['sessionId'], record['requestId'])
            record = self.lookup_request(record['sessionId'], record['requestId'])
        return True, self._stored_result(record)

    def capture_failed(self, binding, exc):
        """Persist a local capture failure before any remote import can start."""
        record = self.lookup_request(binding['sessionId'], binding['requestId'])
        if record is None or record['signature'] != binding['signature']:
            raise PublishingError('request_conflict', 'Capture binding differs from its durable admission')
        if record['state'] != 'bound':
            return self._receipt(record)
        code = exc.code if isinstance(exc, PublishingError) else 'invalid_argument'
        message = str(exc)[:1000] if isinstance(exc, (PublishingError, ValueError)) else 'Local immutable capture failed before remote import'
        record.update(state='unknown' if code == 'unknown_outcome' else 'failed', completedAt=_now(), captureFailed=True,
                      error={'code': code, 'message': message})
        self._put_request(record)
        return self._receipt(record)

    def _put_request(self, record, signature=None, *, commit=True):
        if signature is None:
            signature = record['signature']
        self.app.db.execute('INSERT OR REPLACE INTO publishing_target_requests VALUES (?,?,?,?)', (record['sessionId'], record['requestId'], signature, canonical(record)))
        if commit:
            self.app.db.commit()

    def bind_request(self, sid, action, args, *, fallback_target_id=None):
        """Synchronously freeze endpoint/identity before local capture or SSH.

        A legacy local receipt can supply fallback_target_id='loopback' on its
        first admission to this registry. Stored bindings always take precedence.
        """
        _identifier(sid, 'sessionId')
        rid = _identifier(args['requestId'], 'requestId')
        previous = self.app.db.execute('SELECT signature,body FROM publishing_target_requests WHERE session=? AND request=?', (sid, rid)).fetchone()
        if previous:
            record = json.loads(previous[1])
            if args.get('targetId', record['targetId']) != record['targetId']:
                raise PublishingError('request_conflict', 'This request was admitted for another target and cannot be retargeted')
            signature = digest({'action': action, 'args': {**args, 'sessionId': sid, 'targetId': record['targetId']}})
            if signature != previous[0]:
                raise PublishingError('request_conflict', 'requestId was already used with different publishing arguments')
            return record
        if fallback_target_id and args.get('targetId', fallback_target_id) != fallback_target_id:
            raise PublishingError('request_conflict', 'This request already belongs to the original local target')
        target = self.resolve(sid, fallback_target_id or args.get('targetId'), require_selected=True)
        if target['id'] != 'loopback':
            # This is the UI/agent's observation, not values resolved on its
            # behalf at submission. An unused target ID may have been edited and
            # selected for another service since that observation was rendered.
            if type(args.get('targetRevision')) is not int or not isinstance(args.get('serviceId'), str):
                raise PublishingError('target_observation_required', 'New remote operations require the targetRevision and serviceId from the inspected target you observed; no operation was admitted')
            if args['targetRevision'] != target['revision'] or args['serviceId'] != target['inspection']['serviceId']:
                raise PublishingError('stale_target', 'The observed target revision or service identity changed; refresh and explicitly submit to the intended target. No operation was admitted')
        signature = digest({'action': action, 'args': {**args, 'sessionId': sid, 'targetId': target['id']}})
        record = {'id': digest([sid, rid]), 'sessionId': sid, 'requestId': rid, 'action': action.removeprefix('publishing.'),
                  'siteId': args.get('siteId'), 'releaseId': args.get('releaseId'), 'targetId': target['id'], 'targetRevision': target['revision'],
                  'serviceId': target['inspection']['serviceId'] if target['id'] != 'loopback' else None, 'target': target,
                  'signature': signature, 'transferDigest': None, 'state': 'bound', 'createdAt': _now(), 'completedAt': None, 'result': None, 'error': None}
        self._put_request(record, signature)
        return record

    @staticmethod
    def _receipt(record):
        hidden = {'target', 'signature', 'transferDigest', 'rejectedResult'}
        if record.get('remoteReceiptVerified') is not True:
            hidden.add('remoteReceipt')
        return {key: value for key, value in record.items() if key not in hidden}

    def receipts(self, sid, target_id=None):
        result = []
        for record in self._requests(sid):
            if record['targetId'] == 'loopback' or (target_id is not None and record['targetId'] != target_id):
                continue
            try:
                self._validate_cached_result(record)
            except PublishingError:
                pass  # Return the persisted unknown diagnostic.
            result.append(self._receipt(record))
        return result

    def owns_records(self, sid):
        return any(self.rows(table, '1', 'WHERE session=? LIMIT 1', (sid,))
                   for table in ('publishing_targets', 'publishing_target_requests', 'publishing_target_selection'))

    def _validate_cached_result(self, record):
        if record['state'] != 'succeeded':
            return
        try:
            self._client(record['target']).validate_result(record['action'], record['result'])
        except PublishingError as exc:
            # Retain the original evidence privately, while no longer projecting
            # a pre-correction receipt as a successful, safe serving endpoint.
            record['rejectedResult'] = record['result']
            record['result'] = None
            raise self._reconciliation_failure(record, exc) from exc

    def _stored_result(self, record):
        if record['state'] == 'succeeded':
            self._validate_cached_result(record)
            return record['result']
        error = record.get('error') or {'code': 'unknown_outcome', 'message': 'Remote outcome is not confirmed; no operation was replayed'}
        code = 'unknown_outcome' if record['state'] in {'running', 'unknown'} else error['code']
        raise PublishingError(code, error['message'], receipt=PublishingTargets._receipt(record))

    def _reconciliation_failure(self, record, exc):
        """A failed read cannot turn an uncertain mutation into a known failure."""
        if not (exc.code == 'unknown_outcome' and exc.receipt and record.get('reconciliationError') and exc.receipt.get('reconciliationError') == record['reconciliationError']):
            record['reconciliationError'] = {'code': exc.code, 'message': str(exc)[:1000]}
        record.update(state='unknown', remoteReceiptVerified=False,
                      error={'code': 'unknown_outcome', 'message': 'The original operation remains unknown because its receipt could not be verified. No operation was replayed.'})
        self._put_request(record)
        return PublishingError('unknown_outcome', record['error']['message'], receipt=self._receipt(record))

    def _observe_receipt(self, record, receipt):
        try:
            return self._verify_receipt(record, receipt)
        except PublishingError as exc:
            if record['state'] in {'running', 'unknown'}:
                raise self._reconciliation_failure(record, exc) from exc
            raise

    def _verify_receipt(self, record, receipt):
        if not isinstance(receipt, dict) or receipt.get('sessionId') != record['sessionId'] or receipt.get('requestId') != record['requestId'] or receipt.get('id') != record['id']:
            raise PublishingError('invalid_response', 'Remote receipt does not match the admitted request')
        if receipt.get('action') != ('import' if record['action'] == 'build' else record['action']):
            raise PublishingError('request_conflict', 'Remote receipt action differs from this admitted request')
        if record.get('siteId') and receipt.get('siteId') != record['siteId']:
            raise PublishingError('request_conflict', 'Remote receipt site differs from this admitted request')
        if record.get('releaseId') and record['action'] != 'build' and receipt.get('releaseId') != record['releaseId']:
            raise PublishingError('request_conflict', 'Remote receipt release differs from this admitted request')
        # A request ID and release can match an older operation with different
        # review text, expected revision, or transfer bytes. Only the service's
        # durably admitted exact guarded RPC digest proves this request.
        expected_digest = record.get('rpcPayloadDigest')
        if not expected_digest or receipt.get('rpcPayloadDigest') != expected_digest or receipt.get('serviceId') != record['serviceId']:
            raise PublishingError('unknown_outcome', 'Remote receipt does not prove this exact submitted payload and service identity; the request remains unknown', receipt=self._receipt(record))
        state = receipt.get('state')
        if state not in {'running', 'unknown', 'succeeded', 'failed'}:
            raise PublishingError('invalid_response', 'Remote receipt has an invalid outcome')
        if state == 'succeeded' and record['action'] == 'build' and (not isinstance(receipt.get('result'), dict) or receipt['result'].get('manifestDigest') != record.get('manifestDigest')):
            raise PublishingError('integrity_error', 'Remote import receipt differs from the admitted immutable manifest')
        # Exact RPC proof cannot replace target-policy checks. Non-success
        # receipts can also carry URL-bearing result metadata; validate it before
        # marking any receipt verified or making that metadata visible.
        self._client(record['target']).validate_result(record['action'], receipt)
        record['remoteReceipt'] = receipt
        record['remoteReceiptVerified'] = True
        record.pop('reconciliationError', None)
        record['siteId'] = receipt.get('siteId') or record.get('siteId')
        record['releaseId'] = receipt.get('releaseId') or record.get('releaseId')
        record['state'] = 'unknown' if state == 'running' else state
        record['result'] = (receipt['result'] if record['action'] == 'build' else receipt) if state == 'succeeded' else None
        record['error'] = receipt.get('error') if state != 'succeeded' else None
        record['completedAt'] = receipt.get('completedAt') or _now()
        self._put_request(record)
        return record

    async def reconcile(self, sid, request_id):
        row = self.app.db.execute('SELECT body FROM publishing_target_requests WHERE session=? AND request=?', (sid, request_id)).fetchone()
        if not row:
            raise PublishingError('not_found', 'Publishing request is not available in this task')
        record = json.loads(row[0])
        if record['targetId'] == 'loopback':
            raise PublishingError('invalid_target', 'Local receipts are owned by the local publisher')
        try:
            receipt = await asyncio.to_thread(self._client(record['target']).request, {'method': 'receipt', 'sessionId': sid, 'requestId': request_id})
            if receipt is not None:
                self._observe_receipt(record, receipt)
        except PublishingError as exc:
            if record['state'] in {'running', 'unknown'}:
                raise self._reconciliation_failure(record, exc) from exc
            raise
        return self._receipt(record)

    async def _execute(self, record, payload):
        if record['targetId'] == 'loopback':
            raise PublishingError('invalid_target', 'Local operations must use the local publisher')
        rpc_digest = digest({**payload, 'expectedServiceId': record['serviceId']})
        if record.get('rpcPayloadDigest') and record['rpcPayloadDigest'] != rpc_digest:
            raise PublishingError('request_conflict', 'RPC payload differs from this request’s durable admission')
        if record['state'] in {'unknown', 'running'}:
            await self.reconcile(record['sessionId'], record['requestId'])
            refreshed = self.app.db.execute('SELECT body FROM publishing_target_requests WHERE session=? AND request=?', (record['sessionId'], record['requestId'])).fetchone()
            return self._stored_result(json.loads(refreshed[0]))
        if record['state'] in {'succeeded', 'failed'}:
            return self._stored_result(record)
        # Only a new, unsent admission gets a proof expectation. Never backfill
        # legacy running/unknown records from today's reconstructed arguments.
        record.update(state='running', startedAt=_now(), rpcPayloadDigest=rpc_digest)
        self._put_request(record)  # Commit before starting the SSH subprocess.
        try:
            result = await asyncio.to_thread(self._client(record['target']).request, payload)
            if record['action'] == 'build':
                if not isinstance(result, dict) or result.get('manifestDigest') != record.get('manifestDigest') or result.get('siteId') != record['siteId'] or result.get('sessionId') != record['sessionId']:
                    raise PublishingError('unknown_outcome', 'Remote import returned a result that does not match the admitted manifest; inspect its receipt')
            else:
                try:
                    if not isinstance(result, dict) or result.get('state') != 'succeeded':
                        raise PublishingError('invalid_response', 'Remote success response has no successful operation receipt')
                    self._observe_receipt(record, result)
                except PublishingError as exc:
                    raise PublishingError('unknown_outcome', 'Remote response did not match the admitted operation; inspect its receipt') from exc
            record.update(state='succeeded', result=result, completedAt=_now(), error=None)
            if isinstance(result, dict):
                record['siteId'] = result.get('siteId') or record.get('siteId')
                record['releaseId'] = (result.get('id') if record['action'] == 'build' else result.get('releaseId')) or record.get('releaseId')
        except PublishingError as exc:
            state = 'unknown' if exc.code == 'unknown_outcome' or (exc.receipt and exc.receipt.get('state') in {'running', 'unknown', 'succeeded'}) else 'failed'
            record.update(state=state, completedAt=_now(), error={'code': 'unknown_outcome' if state == 'unknown' else exc.code, 'message': str(exc)})
            if state == 'unknown' and exc.code != 'unknown_outcome':
                record['reconciliationError'] = {'code': exc.code, 'message': str(exc)[:1000]}
            if exc.receipt:
                record['remoteReceipt'] = exc.receipt
        except Exception:
            record.update(state='unknown', completedAt=_now(), error={'code': 'unknown_outcome', 'message': 'Remote outcome could not be recorded; no operation was replayed'})
        self._put_request(record)
        return self._stored_result(record)

    async def import_release(self, args, exported, binding=None):
        sid = args['sessionId']
        # Re-read the durable binding so a stale in-memory result cannot replay.
        record = self.bind_request(sid, 'publishing.build', args)
        if binding is not None and (binding['targetId'], binding['serviceId']) != (record['targetId'], record['serviceId']):
            raise PublishingError('request_conflict', 'Import binding differs from the admitted target')
        if set(exported) != {'sessionId', 'siteId', 'manifest', 'manifestDigest', 'files'} or exported['sessionId'] != sid or exported['siteId'] != args['siteId']:
            raise PublishingError('invalid_argument', 'Export must contain only this task’s exact static release transfer')
        transfer_digest = digest(exported)
        if record['transferDigest'] is not None and record['transferDigest'] != transfer_digest:
            raise PublishingError('request_conflict', 'Imported bytes differ from this request’s admitted immutable transfer')
        if record['transferDigest'] is None:
            record['transferDigest'] = transfer_digest
            record['manifestDigest'] = exported['manifestDigest']
            self._put_request(record)
        return await self._execute(record, {'method': 'import', 'requestId': args['requestId'], **exported})

    def _merge_receipts(self, sid, target, remote_receipts):
        if not isinstance(remote_receipts, list):
            raise PublishingError('invalid_response', 'Remote receipts must be a list')
        indexed = {r['requestId']: r for r in remote_receipts if isinstance(r, dict) and isinstance(r.get('requestId'), str)}
        owned = set()
        for record in self._requests(sid):
            if record['targetId'] != target['id'] or record['serviceId'] != target['inspection']['serviceId']:
                continue
            owned.add(record['requestId'])
            if record['state'] == 'succeeded':
                try:
                    self._validate_cached_result(record)
                except PublishingError:
                    # Project the retained unknown record; do not immediately
                    # replace it with another read of a rejected cached result.
                    indexed[record['requestId']] = self._receipt(record)
                    continue
            remote = indexed.get(record['requestId'])
            if remote and record['state'] in {'running', 'unknown'}:
                try:
                    self._observe_receipt(record, remote)
                except PublishingError as exc:
                    # Keep the local request visible as unknown; an unrelated or
                    # unproven remote receipt cannot become its successful row.
                    self._reconciliation_failure(record, exc)
            indexed[record['requestId']] = self._receipt(record)
        client = self._client(target)
        for request_id, record in indexed.items():
            if request_id not in owned:
                client.validate_result('receipt', record)
        return list(indexed.values())

    async def dispatch_remote(self, action, args, binding=None):
        sid = args['sessionId']
        method = action.removeprefix('publishing.')
        if method in {'list', 'status', 'logs'}:
            target = self.resolve(sid, args.get('targetId'))
            if target['id'] == 'loopback':
                raise PublishingError('invalid_target', 'Local operations must use the local publisher')
            client = self._client(target)
            if method == 'status':
                return await asyncio.to_thread(client.request, {'method': 'status', 'sessionId': sid, 'siteId': args['siteId']})
            receipts = await asyncio.to_thread(client.request, {'method': 'receipts', 'sessionId': sid})
            records = self._merge_receipts(sid, target, receipts)
            if method == 'logs':
                return {'items': [r for r in records if r.get('siteId') == args['siteId']], 'target': target}
            sites = await asyncio.to_thread(client.request, {'method': 'list', 'sessionId': sid})
            releases = await asyncio.to_thread(client.request, {'method': 'releases', 'sessionId': sid})
            return {**self.list(sid), 'target': target, 'sites': sites, 'releases': releases, 'receipts': records}
        permitted = {'preview': {'releaseId'}, 'review': {'releaseId', 'note'}, 'deploy': {'siteId', 'releaseId', 'expectedRevision'}, 'rollback': {'siteId', 'releaseId', 'expectedRevision'}, 'stop': {'siteId', 'expectedRevision'}, 'remove': {'siteId', 'expectedRevision'}}
        if method not in permitted:
            raise PublishingError('invalid_argument', 'Unknown remote publishing action')
        record = self.bind_request(sid, action, args)
        if binding is not None and (binding['targetId'], binding['serviceId']) != (record['targetId'], record['serviceId']):
            raise PublishingError('request_conflict', 'Operation binding differs from the admitted target')
        payload = {'method': method, 'sessionId': sid, 'requestId': args['requestId'], **{key: args[key] for key in permitted[method]}}
        return await self._execute(record, payload)
