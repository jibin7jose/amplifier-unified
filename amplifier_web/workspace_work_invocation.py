"""Commissioned-input Work lineage in existing receipts, never an effect grant.

Only this first producer is implemented here. Content delivery and dependent-tool
admission belong to the runtime adapter; current facts are not proof of either.
"""
from __future__ import annotations

import copy
from contextlib import asynccontextmanager
import hashlib
import json
import time
import uuid

from amplifier_operations.coordination import fingerprint
from .collaboration import BINDING, CREATION, PRINCIPAL
from .task_binding import continuation_epoch, dependency_blocked
from .child_execution import current as child_execution, generation as execution_generation
from .workspace_work import CONTEXT_BYTES, EVENT, RECORD, _decode, _encode
from .workspace_work_evidence import _link_current, mutate
from .work_projection import input_identity_bytes

PROMISE_KEYS = ('interpretation', 'criteria', 'constraints', 'commitment',
                'promiseRevision', 'originalSourceRef', 'promiseEventRef')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def references_schema(schema, string):
    revision = {'type': 'integer', 'minimum': 1}
    return {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': schema({
        'workId': {**string(200), 'minLength': 1}, 'expectedRevision': revision,
        'promiseRevision': revision, 'candidate': {**string(100), 'minLength': 1,
            'description': 'Copy the current saved Work candidate. If candidate is null, establish an executor link with workspace.work.link first. intentKey and command/request IDs do not establish a candidate.'},
        'role': {'enum': ['executor', 'reviewer']}},
        ['workId', 'expectedRevision', 'promiseRevision', 'candidate', 'role'])}


def definitions(schema, string):
    return {
        'workspace.work.invocation': ('Read exact commissioned-input Work requirements, not an effect grant. Unsupported or incomplete lineage remains unavailable.', schema({
            'sessionId': string(200), 'expectedVersion': string(64), 'after': string(2048),
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, ['sessionId'])),
        'workspace.work.promise': ('Read exact current promise JSON in bounded Unicode pages. A successful read is not proof of fitted-request delivery.', schema({
            'workspaceId': string(100), 'id': string(200),
            'expectedRevision': {'type': 'integer', 'minimum': 1},
            'expectedPromiseRevision': {'type': 'integer', 'minimum': 1},
            'offset': {'type': 'integer', 'minimum': 0},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 2000}},
            ['workspaceId', 'id', 'expectedRevision', 'expectedPromiseRevision'])),
    }


class WorkInvocation:
    def __init__(self, service):
        self.service = service
        self.work = service.workspace_work
        self.receipts = service.collaboration
        self.db = service.db

    def task_binding(self, row):
        task = row.get('task') or self.service.coordination.task(row['id'])
        return ({'taskId': task['id'], 'revision': task['revision'],
                 'continuationEpoch': continuation_epoch(task), 'status': task.get('status')}
                if task.get('id') else None)

    def dependency_binding(self, row, expected):
        """An exact agent dependency-block transition does not revoke its child.

        Reuse canonical change history; no missing/replaced task or intervening
        instruction, human block, pause or resume can qualify this exception.
        """
        task = row.get('task') or self.service.coordination.task(row['id'])
        if (not expected or expected.get('status') != 'active'
                or expected.get('taskId') != task.get('id') or not dependency_blocked(task)):
            return False
        revision = expected.get('revision')
        if type(revision) is not int:
            return False
        changes = [change for change in task.get('changes', []) if change.get('revision', 0) > revision]
        for change in changes:
            fields = change.get('fields') or {}
            if (change.get('revision') != revision + 1 or change.get('origin') != 'agent'
                    or not fields or set(fields) - {'status', 'blockedReason', 'blockProvenance'}
                    or fields.get('status', 'blocked') != 'blocked'):
                return False
            revision += 1
        return bool(changes) and revision == task['revision']

    def root_facts(self, row, *, admitted_child=False, expected_task=None):
        from .updates import work_paused
        task = self.task_binding(row)
        dependent = admitted_child and dependency_blocked(row.get('task') or self.service.coordination.task(row['id']))
        if dependent and self.dependency_binding(row, expected_task):
            task = copy.deepcopy(expected_task)
        if (row.get('sessionKind', 'root') != 'root' or row.get('_deleting')
                or work_paused(self.service.state)
                or row.get('status') in {'stopped', 'stopping', 'error', 'interrupted'}
                or task and (task.get('status') in {'paused', 'completed'}
                             or task.get('status') == 'blocked' and not dependent)):
            self.work.error('Work root is stopped or inactive.', 403)
        assignment = self.service.assignments.current(row)
        reason = self.service.assignments.blocked(row)
        if reason:
            self.work.error(reason, 403)
        return {'sessionId': row['id'], 'workspace': row['workspace'],
                'interruptionRevision': row.get('interruptionRevision', 0),
                'task': task, 'assignmentId': (assignment or {}).get('id'),
                'assignmentRevision': (assignment or {}).get('revision')}

    def owner_claims(self, item, source, workspace):
        claims = []
        for link in item.get('links', []):
            if (link.get('sessionId') == source['id'] and link.get('taskId')
                    and link.get('candidate') == item.get('candidate')
                    and link.get('promiseRevision') == item['promiseRevision']):
                if not _link_current(self.work, link, workspace):
                    self.work.error('The commissioning owner claim is stale.')
                assignment = self.service.assignments.current(source)
                task = assignment['tasks'][link['taskId']]
                claim = self.service.assignments.tasks.claim_status(assignment, task)
                claims.append({'link': copy.deepcopy(link), 'taskRevision': task.get('revision'),
                               'claim': copy.deepcopy(claim)})
        return claims

    def validate_references(self, source, references):
        self.root_facts(source)
        workspace = self.work.workspace({'sessionId': source['id']}, 'agent', source['id'])
        if len({ref['workId'] for ref in references}) != len(references):
            self.work.error('Work references must name distinct items.', 400)
        items = []
        for ref in references:
            raw = self.db.execute('SELECT receipt FROM commands WHERE id=?', (ref['workId'],)).fetchone()
            record = json.loads(raw[0]) if raw else {}
            item = copy.deepcopy(record.get('result', {}))
            if record.get('commandAction') != RECORD or item.get('workspaceId') != workspace['id']:
                self.work.error('Work item is unavailable in this workspace.', 404)
            if item['ownerSessionId'] != source['id']:
                self.work.error('Only the accountable Work owner can commission this promise.', 403)
            if item['revision'] != ref['expectedRevision']:
                self.work.error('Work revision changed before commissioning.')
            if item['promiseRevision'] != ref['promiseRevision']:
                self.work.error('Promise revision changed before commissioning.')
            if item.get('candidate') != ref['candidate']:
                self.work.error('Work candidate changed before commissioning.')
            if item['commitment'] != 'accepted' or item['disposition'] != 'open':
                self.work.error('Commissioning requires open accepted Work.')
            self.owner_claims(item, source, workspace)
            items.append(item)
        return workspace, items

    def plan_reason(self, operation):
        if not operation.get('workReferences') or operation.get('phase') == 'input_committed':
            return None
        from .errors import AppError
        try:
            source = self.service._session(operation['senderSessionId'], hydrate=False)
            self.validate_references(source, operation['workReferences'])
            if operation.get('sourceGenerationId'):
                active = source.get('collaborationGeneration') or {}
                if active.get('id') != operation['sourceGenerationId'] or active.get('terminal'):
                    return 'The commissioning root generation changed.'
        except (AppError, KeyError, TypeError, ValueError) as exc:
            return str(exc)
        return None

    @asynccontextmanager
    async def initial_transaction(self, action, identity):
        """The service lock is held. Only a creation-owned initial send enters.

        publish_receipt commits the normal message/session transaction. On an
        earlier failure restore both SQLite and its in-memory projections. A
        post-commit failure retains the operation rather than pretending rollback.
        """
        creation = CREATION.get()
        operation = (self.receipts.receipt(creation[4]['requestId'])
                     if creation and action == 'coordination.send'
                     and identity == creation[4]['requestId'] + ':brief' else {})
        if not operation.get('workReferences') or self.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
            yield
            return
        ids = {operation['senderSessionId'], operation['sessionId']}
        snapshots = [(row, copy.deepcopy(row)) for row in self.service.state['sessions'] if row['id'] in ids]
        pending = copy.deepcopy(self.work.pending_input_facts)
        self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            # The service save path may already have rolled SQLite back. A
            # committed initial receipt is the durable post-commit discriminator.
            if self.db.in_transaction or not self.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
                self.db.rollback()
                for row, snapshot in snapshots:
                    row.clear()
                    row.update(snapshot)
                self.work.pending_input_facts = pending
                self.service.projections.invalidate(state=self.service.state, session_ids=ids)
            raise

    def seal(self, operation, receipt, source, target):
        """No await/commit here: input, links and association share caller txn."""
        workspace, items = self.validate_references(source, operation['workReferences'])
        self.root_facts(target)
        refs = []
        for ref, item in zip(operation['workReferences'], items, strict=True):
            args = {'expectedRevision': item['revision'], 'linkedSessionId': target['id'],
                    'candidate': ref['candidate'], 'role': ref['role']}
            extra, promise = mutate(self.work, 'link', item, args, source, operation['origin'], workspace)
            event_id = 'work-event:' + str(uuid.uuid5(uuid.NAMESPACE_URL,
                'commissioned-work:' + operation['requestId'] + ':' + item['id']))
            event = {'id': event_id, 'commandAction': EVENT, 'kind': 'link', 'workItemId': item['id'],
                     'sequence': item['eventCount'], 'promiseRevision': promise,
                     'actor': {'origin': operation['origin'], 'sessionId': source['id']},
                     'recordedAt': time.time(), 'source': {}, 'changes': {}, **extra}
            self.receipts.insert(event_id, fingerprint(event), event)
            item['latestEventRef'] = event_id
            self.receipts.save(item['id'], {'commandAction': RECORD, 'result': item})
            self.work.advance(workspace)
            refs.append({**ref, 'linkEventId': event_id,
                         'sourceClaims': self.owner_claims(item, source, workspace)})
        association = {'version': 1, 'requestId': operation['requestId'],
                       'initialInputId': receipt['inputId'], 'sourceRootId': source['id'],
                       'targetRootId': target['id'], 'workspaceId': workspace['id'],
                       'capturedAt': time.time(), 'references': refs,
                       'sourceTaskBinding': self.task_binding(source),
                       'targetTaskBinding': self.task_binding(target)}
        operation['workAssociation'] = association
        receipt['workAssociationRef'] = {'requestId': operation['requestId'], 'version': 1}

    def bound_root(self, sid, *, require_generation=False):
        row = self.service._session(sid, hydrate=False)
        if child_execution(self.service, row) is None:
            row = self.receipts.source({}, 'agent', sid, hydrate=False)
        native = row.get('runtimeSessionId') or row.get('nativeIdentity') or row['id']
        if PRINCIPAL.get() != native and child_execution(self.service, row) is None:
            self.work.error('Invocation reads require the actual root principal.', 403)
        if require_generation:
            binding = BINDING.get() or {}
            active = execution_generation(self.service, row)
            if not binding.get('_generationId') or binding['_generationId'] != active.get('id') or active.get('terminal'):
                self.work.error('Invocation generation changed.', 409)
        return row

    def promise_text(self, item):
        return canonical({key: item[key] for key in PROMISE_KEYS})

    def sources(self, item):
        # Read only indexed source metadata, never retained bodies. No all-work
        # scan or hydration. Original/corrections retain their sequence identity.
        return self.db.execute("""SELECT id, json_extract(receipt,'$.sequence'),
            json_extract(receipt,'$.source.sha256'), json_extract(receipt,'$.promiseRevision') FROM commands
            WHERE json_extract(receipt,'$.commandAction')=?
            AND json_extract(receipt,'$.workItemId')=?
            AND json_extract(receipt,'$.promiseRevision')<=?
            AND json_extract(receipt,'$.source.sha256') IS NOT NULL
            ORDER BY json_extract(receipt,'$.sequence') LIMIT 4097""", (EVENT, item['id'], item['promiseRevision']))

    def association(self, input_id, target, workspace):
        request = self.receipts.receipt(input_id)
        if (request.get('commandAction') != 'coordination.send' or request.get('target', {}).get('sessionId') != target['id']
                or request.get('workspace') != workspace['path'] or request.get('delivery') not in {'accepted', 'applied'}):
            self.work.error('Input has no current delivered request association.')
        ref = request.get('workAssociationRef')
        if not ref:
            self.work.error('untyped_followup' if not request.get('creationOperationId') else 'legacy_commission')
        operation = self.receipts.receipt(ref['requestId'])
        association = operation.get('workAssociation') or {}
        if (operation.get('commandAction') != 'coordination.create' or operation.get('phase') != 'input_committed'
                or ref.get('version') != 1 or association.get('version') != 1
                or association.get('requestId') != operation['requestId']
                or association.get('initialInputId') != input_id or operation.get('initialInputId') != input_id
                or association.get('targetRootId') != target['id'] or operation.get('sessionId') != target['id']
                or association.get('sourceRootId') != request['senderSessionId']
                or operation.get('senderSessionId') != request['senderSessionId']
                or request.get('creationOperationId') != operation['requestId']
                or association.get('workspaceId') != workspace['id']):
            self.work.error('Commissioned input association is corrupt.')
        source = self.service._session(association['sourceRootId'], hydrate=False)
        admitted_child = child_execution(self.service, target) is not None
        owner_facts = self.root_facts(source, admitted_child=admitted_child,
                                      expected_task=association.get('sourceTaskBinding'))
        target_facts = self.root_facts(target, admitted_child=admitted_child,
                                       expected_task=association.get('targetTaskBinding'))
        if (source['workspace'] != workspace['path']
                or source.get('interruptionRevision', 0) != operation['sourceInterruptionRevision']
                or target.get('interruptionRevision', 0) != request['interruptionRevision']
                or association.get('sourceTaskBinding') != owner_facts['task']
                or association.get('targetTaskBinding') != target_facts['task']):
            self.work.error('Commissioned Work owner, task or Stop boundary changed.')
        refs = association.get('references')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            self.work.error('Commissioned Work references are unavailable.')
        if (len({ref['workId'] for ref in refs}) != len(refs)
                or [{key: ref[key] for key in ('workId', 'expectedRevision', 'promiseRevision', 'candidate', 'role')}
                    for ref in refs] != operation.get('workReferences')):
            self.work.error('Commissioned Work references conflict with the retained plan.')
        items = []
        for ref in refs:
            item = self.work.load(ref['workId'], workspace)
            event = self.receipts.receipt(ref['linkEventId'])
            link = event.get('link') or {}
            if (item['ownerSessionId'] != source['id'] or item['disposition'] != 'open'
                    or item['commitment'] != 'accepted' or item['promiseRevision'] != ref['promiseRevision']
                    or item.get('candidate') != ref['candidate'] or event.get('commandAction') != EVENT
                    or event.get('workItemId') != item['id'] or event.get('kind') != 'link'
                    or link.get('sessionId') != target['id'] or link.get('candidate') != ref['candidate']
                    or link.get('promiseRevision') != ref['promiseRevision'] or link.get('role') != ref['role']
                    or link not in item.get('links', []) or not _link_current(self.work, link, workspace)):
                self.work.error('Commissioned Work relationship changed.')
            if ref.get('sourceClaims') != self.owner_claims(item, source, workspace):
                self.work.error('Commissioned Work owner claim changed.')
            items.append(item)
        return association, items, [owner_facts, target_facts]

    def required(self, sid):
        from .errors import AppError
        target = self.bound_root(sid)
        binding, active = BINDING.get() or {}, execution_generation(self.service, target)
        header = {'status': 'unavailable', 'reason': None, 'version': None,
                  'rootSessionId': sid, 'generationId': active.get('id'), 'inputIds': [],
                  'associationRequestIds': [], 'workCount': None, 'requirementCount': None,
                  'requirementsRef': None, 'effectAuthorized': False,
                  'coverage': {'complete': False, 'supportedInputIds': [], 'unsupportedInputs': []}}
        coverage = header['coverage']
        try:
            self.bound_root(sid, require_generation=True)
            self.root_facts(target, admitted_child=child_execution(self.service, target) is not None)
            delivered = set(active.get('inputIds', []))
            inputs = binding.get('_inputBindings', [])
            if not isinstance(inputs, list) or any(not isinstance(row, dict) for row in inputs):
                self.work.error('Invocation input bindings are malformed.')
            requested = {row.get('inputId') for row in inputs}
            if (not requested <= delivered
                    or any(not isinstance(i, str) or not 1 <= len(i) <= 200 for i in requested)):
                self.work.error('Invocation inputs were not observed delivered.')
            header['inputIds'] = sorted(delivered)
            if not delivered:
                self.work.error('Invocation inputs were not observed delivered.')
            if requested != delivered:
                coverage.update(reason='input_projection_incomplete',
                                missingInputIds=sorted(delivered - requested),
                                deliveredInputCount=len(delivered), projectedInputCount=len(requested))
                self.work.error('Invocation input projection omits host-observed delivered inputs.')
            workspace = self.work.workspace({'sessionId': sid}, 'agent', sid)
            items, facts = {}, []
            for input_id in header['inputIds']:
                try:
                    association, found, root_facts = self.association(input_id, target, workspace)
                    facts.append([association, root_facts])
                    items.update((item['id'], item) for item in found)
                    coverage['supportedInputIds'].append(input_id)
                    header['associationRequestIds'].append(association['requestId'])
                except (AppError, KeyError, TypeError, ValueError) as exc:
                    coverage['unsupportedInputs'].append({'inputId': input_id, 'reason': str(exc)[:200]})
            if coverage['unsupportedInputs']:
                self.work.error('Invocation contains unsupported or unavailable input lineage.')
            if len(items) > 8:
                self.work.error('Invocation Work coverage exceeds the bounded projection.')
            count, content_facts = 0, []
            for item in items.values():
                hashes = hashlib.sha256()
                source_count, originals, promises = 0, 0, 0
                revisions = set()
                for identity, sequence, digest, revision in self.sources(item):
                    hashes.update(canonical([identity, sequence, digest, revision]).encode())
                    source_count += 1
                    revisions.add(revision)
                    originals += identity == item['originalSourceRef']
                    promises += identity == item['promiseEventRef']
                if source_count > 4096 or item['promiseRevision'] > 4096:
                    self.work.error('Required source history exceeds bounded inspection; no sources were omitted.')
                if originals != 1 or promises != 1 or revisions != set(range(1, item['promiseRevision'] + 1)):
                    self.work.error('Required original or correction source is unavailable.')
                count += 1 + source_count
                content_facts.append([item['id'], item['revision'], item['promiseRevision'],
                    hashlib.sha256(self.promise_text(item).encode()).hexdigest(), hashes.hexdigest(), source_count])
            version = fingerprint([sid, active.get('id'), header['inputIds'], facts, sorted(content_facts)])
            header.update(status='available', reason=None, version=version, workCount=len(items), requirementCount=count,
                requirementsRef={'action': 'workspace.work.invocation', 'args': {'sessionId': sid, 'expectedVersion': version}})
            coverage['complete'] = True
            return header, [items[key] for key in sorted(items)]
        except (AppError, KeyError, TypeError, ValueError) as exc:
            header.update(status='unavailable', version=None, reason=str(exc)[:200],
                          workCount=None, requirementCount=None, requirementsRef=None)
            coverage['complete'] = False
            return header, []

    def current(self, sid):
        from .errors import AppError
        try:
            target = self.bound_root(sid)
            required, _ = self.required(sid)
            workspace = self.work.workspace({'sessionId': sid}, 'agent', sid)
            limit = 20
            while True:
                discovery = self.work.listing(workspace, {'limit': limit}, owner=target['id'], context=True)
                packet = {'available': True, 'discovery': discovery, 'required': required}
                if len(json.dumps(packet, ensure_ascii=False).encode()) - input_identity_bytes(required) <= CONTEXT_BYTES:
                    return packet
                if limit <= 1:
                    self.work.error('Work current projection exceeds its byte bound.', 413)
                limit -= 1
        except AppError as exc:
            # Work is an optional projection of coordination, not a prerequisite
            # for managed-location chats or other host coordination observations.
            # Unavailable/oversized Work must not erase unrelated reference data.
            return {'available': False, 'discovery': {'status': 'unavailable', 'reason': str(exc)},
                    'required': {'status': 'unavailable', 'reason': str(exc), 'version': None,
                                 'coverage': {'complete': False}, 'effectAuthorized': False}}

    def manifest(self, sid, args):
        self.bound_root(sid, require_generation=True)
        header, items = self.required(sid)
        if args.get('expectedVersion') is not None and args['expectedVersion'] != header['version']:
            self.work.error('Invocation version changed; read current requirements.')
        offset = 0
        if args.get('after'):
            cursor = _decode(args['after'])
            if (not isinstance(cursor, list) or len(cursor) != 3 or cursor[:2] != [sid, header['version']]
                    or type(cursor[2]) is not int or not 0 <= cursor[2] <= (header['requirementCount'] or 0)):
                self.work.error('Invocation cursor or version changed.')
            offset = cursor[2]
        result = {**header, 'items': [], 'observedAt': time.time(), 'truncated': False, 'nextAfter': None}
        if header['status'] != 'available':
            return result
        def requirements():
            for item in items:
                row = {key: item[key] for key in ('revision', 'promiseRevision', 'candidate', 'ownerSessionId')}
                row['workId'] = item['id']
                common = {'workspaceId': item['workspaceId'], 'id': item['id']}
                yield {**row, 'kind': 'promise', 'contentSha256': hashlib.sha256(self.promise_text(item).encode()).hexdigest(),
                       'readRef': {'action': 'workspace.work.promise', 'args': {**common,
                           'expectedRevision': item['revision'], 'expectedPromiseRevision': item['promiseRevision']}}}
                for identity, _, digest, _ in self.sources(item):
                    yield {**row, 'kind': 'source', 'contentSha256': digest,
                           'readRef': {'action': 'workspace.work.source', 'args': {**common, 'sourceId': identity}}}
        for index, row in enumerate(requirements()):
            if index < offset:
                continue
            result['items'].append(row)
            end = offset + len(result['items'])
            result.update(truncated=end < header['requirementCount'],
                          nextAfter=_encode([sid, header['version'], end]) if end < header['requirementCount'] else None)
            if len(json.dumps({'accepted': True, 'result': result}, ensure_ascii=False).encode()) - input_identity_bytes(header) > CONTEXT_BYTES:
                result['items'].pop()
                break
            if len(result['items']) >= args.get('limit', 20):
                break
        end = offset + len(result['items'])
        result.update(truncated=end < header['requirementCount'],
                      nextAfter=_encode([sid, header['version'], end]) if end < header['requirementCount'] else None)
        if result['truncated'] and not result['items']:
            self.work.error('Invocation page cannot progress within its byte bound.', 413)
        return result

    def promise(self, args, origin, caller):
        workspace = self.work.workspace(args, origin, caller)
        item = self.work.load(args['id'], workspace)
        if (item['revision'], item['promiseRevision']) != (args['expectedRevision'], args['expectedPromiseRevision']):
            self.work.error('Promise or Work revision changed.')
        text = self.promise_text(item)
        offset = args.get('offset', 0)
        if offset > len(text):
            self.work.error('Promise offset is outside the current value.', 400)
        end = min(offset + args.get('limit', 2000), len(text))
        result = {key: item[key] for key in ('workspaceId', 'id', 'revision', 'promiseRevision', 'originalSourceRef', 'promiseEventRef')}
        result.update(offset=offset, total=len(text), sha256=hashlib.sha256(text.encode()).hexdigest())
        while True:
            result.update(text=text[offset:end], nextOffset=end if end < len(text) else None, truncated=end < len(text))
            if len(json.dumps({'accepted': True, 'result': result}, ensure_ascii=False).encode()) <= CONTEXT_BYTES:
                return result
            end -= 1
            if end <= offset:
                self.work.error('Promise page cannot progress within its byte bound.', 413)
