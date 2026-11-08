"""Bounded execution links and attributed evidence; no execution or claim owner."""
from __future__ import annotations

import copy
import time


def definitions(schema, string):
    target = {'workspaceId': string(100), 'sessionId': string(200), 'id': string(200),
              'expectedRevision': {'type': 'integer', 'minimum': 1}}
    required = ['workspaceId', 'sessionId', 'id', 'expectedRevision']
    candidate = {**string(100), 'minLength': 1}
    return {
        'workspace.work.publication': ('Retain an explicitly attributed publication report for the current exact promise and candidate. Never publishes, fetches references, verifies delivery or records host-confirmed shipment.', schema({**target,
            'promiseRevision': {'type': 'integer', 'minimum': 1}, 'candidate': candidate,
            'outcome': {'enum': ['published', 'failed', 'unknown', 'not_run']},
            'destination': {**string(1000), 'minLength': 1},
            'references': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'uniqueItems': True,
                           'items': {**string(1000), 'minLength': 1}},
            'report': {**string(2000), 'minLength': 1}},
            [*required, 'promiseRevision', 'candidate', 'outcome', 'destination', 'references', 'report'])),
        'workspace.work.resource': ("Append the current linked owner's bounded resource/cleanup report. Explicit attribution only; no resource scan, verified teardown claim or lifecycle action.", schema({**target,
            'resourceId': {**string(500), 'minLength': 1}, 'kind': {**string(100), 'minLength': 1},
            'reportedState': {'enum': ['active', 'unknown', 'reaped', 'observed_absent']},
            'evidenceReference': {**string(2000), 'minLength': 1}}, [*required, 'resourceId', 'kind', 'reportedState', 'evidenceReference'])),
        'workspace.work.link': ('Owner links an existing same-workspace execution or reviewer without starting or granting it authority. An executor link sets the current candidate; before commissioning new Work, link the accountable owner as executor and use the returned candidate and revisions. Links are not claims.', schema({**target,
            'linkedSessionId': string(200), 'role': {'enum': ['executor', 'reviewer']},
            'candidate': candidate, 'taskId': string(200), 'claimId': string(200)}, [*required, 'linkedSessionId', 'role', 'candidate'])),
        'workspace.work.report': ('Append an attributed result for an exact promise/candidate. A report alone never means independent verification. Optional checkReceiptId must be this actor\'s exact retained integration collection.', schema({**target,
            'promiseRevision': {'type': 'integer', 'minimum': 1}, 'candidate': candidate,
            'outcome': {'enum': ['passed', 'failed', 'unknown', 'not_run']},
            'report': {**string(4000), 'minLength': 1},
            'criteria': {'type': 'array', 'maxItems': 32, 'uniqueItems': True, 'items': string(100)},
            'checkReceiptId': string(200),
            'references': {'type': 'array', 'maxItems': 16, 'items': string(2000)}},
            [*required, 'promiseRevision', 'candidate', 'outcome', 'report', 'criteria'])),
        'workspace.work.decision': ('Authenticated human records acceptance or rejection of an exact promise/candidate. Agents cannot supply human decisions. Does not grant tool permissions.', schema({**target,
            'promiseRevision': {'type': 'integer', 'minimum': 1}, 'candidate': candidate,
            'decision': {'enum': ['accepted', 'rejected']}}, [*required, 'promiseRevision', 'candidate', 'decision'])),
        'workspace.work.disposition': ('Owner explicitly closes or reopens a promise, retaining all prior dispositions and unresolved effects. Does not stop, resume or replay execution.', schema({**target,
            'disposition': {'enum': ['open', 'closed']},
            'basis': {'enum': ['satisfied', 'cancelled', 'superseded', 'reopened']},
            'reason': {**string(2000), 'minLength': 1}}, [*required, 'disposition', 'basis', 'reason'])),
    }


def _link_current(work, link, workspace):
    # A declaration made before Stop cannot authorize a later report.
    try:
        row = work.service._session(link['sessionId'], hydrate=False)
    except Exception:
        return False
    if row.get('workspace') != workspace['path'] or row.get('interruptionRevision', 0) != link['interruptionRevision']:
        return False
    if link.get('taskId'):
        value = work.service.assignments.current(row)
        task = work.service.assignments.tasks.get(value, link['taskId'])
        if not task or value['id'] != link['assignmentId']:
            return False
        claim = work.service.assignments.tasks.claim_status(value, task)
        if not claim or claim['id'] != link['claimId'] or claim['sessionId'] != row['id'] or claim['status'] != 'current':
            return False
    return not work.service.assignments.blocked(session=row)


def _qualification(work, actor, item, args):
    identity = args.get('checkReceiptId')
    if not identity:
        return 'participant_report'
    receipt = work.receipts.receipt(identity)
    collection = receipt.get('result', {})
    if receipt.get('commandAction') != 'integration.collect' or collection.get('sessionId') != actor['id']:
        work.error('Use this verifier root\'s exact retained integration collection.', 403)
    historical = args['promiseRevision'] < item['promiseRevision']
    revised = item.get('verificationBoundaryAt', item['promiseRevisedAt'])
    boundary = item.get('verificationBoundaryRef', item['promiseEventRef'])
    promise_row = work.db.execute('SELECT rowid FROM commands WHERE id=?', (boundary,)).fetchone()
    collection_row = work.db.execute('SELECT rowid FROM commands WHERE id=?', (identity,)).fetchone()
    if not historical:
        if not promise_row or not collection_row or collection_row[0] <= promise_row[0]:
            work.error('The collection predates the current verification boundary.')
        if not isinstance(collection.get('capturedAt'), (int, float)) or collection['capturedAt'] < revised:
            work.error('The check receipt predates the current verification boundary; old evidence is not current verification.')
    validation_ids = collection.get('validationIds', [])
    if not validation_ids:
        work.error('A check collection needs its exact retained validation invocations.')
    for validation_id in validation_ids:
        validation = work.receipts.receipt(validation_id)
        run = validation.get('result', {})
        run_row = work.db.execute('SELECT rowid FROM commands WHERE id=?', (validation_id,)).fetchone()
        if (validation.get('commandAction') != 'integration.validate' or run.get('sessionId') != actor['id']
                or run.get('captureId') != collection.get('captureId')
                or not run_row or not isinstance(run.get('capturedAt'), (int, float))
                or not historical and (run_row[0] <= promise_row[0] or run['capturedAt'] < revised)):
            work.error('The executed validation was not bound after this verification boundary.')
    repositories = collection.get('repositories', [])
    matches = [r for r in repositories if r.get('resultingSource', {}).get('head') == args['candidate']]
    if len(matches) != 1:
        work.error('The check receipt does not bind this exact candidate.')
    row = matches[0]
    observed = row.get('status')
    if args['outcome'] == 'passed' and (
            collection.get('status') != 'passed' or collection.get('candidateStatus') != 'matched'
            or observed != 'passed' or row.get('candidateStatus') != 'matched'
            or row.get('resultingSource', {}).get('dirty') is not False
            or row.get('operation', {}).get('state') != 'completed'
            or row.get('operation', {}).get('returncode') != 0
            or not row.get('artifacts') or any(a.get('status') != 'retained' for a in row['artifacts'])):
        work.error('Passing verification requires exact retained passed checks and artifacts; missing/unknown is not success.')
    if args['outcome'] != 'passed' and observed == 'passed':
        work.error('The reported outcome conflicts with its check receipt.')
    if historical:
        return 'historical_check'
    executors = {link['sessionId'] for link in item.get('links', [])
                 if link['candidate'] == args['candidate'] and link['role'] == 'executor'}
    if not executors:
        work.error('Record the candidate execution responsibility before qualifying its check.')
    return 'owner_check' if actor['id'] in executors else 'independent_check'


def mutate(work, verb, item, args, actor, origin, workspace):
    """Return immutable event fields. Caller owns the enclosing atomic transaction."""
    if item['revision'] != args['expectedRevision']:
        work.error('Work revision changed; inspect current state before changing it.')
    owner = item['ownerSessionId'] == actor['id']
    event_promise = item['promiseRevision']
    extra = {}
    if verb in {'link', 'disposition'} and not owner:
        work.error('Only the accountable Work owner can change responsibility or disposition.', 403)
    if verb == 'publication':
        if args['promiseRevision'] != item['promiseRevision'] or args['candidate'] != item.get('candidate'):
            work.error('Publication reports must name the current exact promise and candidate.')
        links = [link for link in item.get('links', [])
                 if link['sessionId'] == actor['id'] and link['candidate'] == args['candidate']
                 and link['promiseRevision'] == args['promiseRevision']]
        if not owner and not any(_link_current(work, link, workspace) for link in links):
            work.error('Publication reports require current linked responsibility.', 403)
        if not all(text.strip() for text in [args['destination'], args['report'], *args['references']]):
            work.error('Publication reports require nonempty destination, report and exact references.', 400)
        publication = {key: copy.deepcopy(args[key]) for key in
                       ('promiseRevision', 'candidate', 'outcome', 'destination', 'references', 'report')}
        publication.update(reportedBy=actor['id'], recordedAt=time.time(),
                           attribution='participant_report', hostObserved=False, independentlyVerified=False)
        item['delivery'] = 'reported_' + args['outcome']
        item['deliveryReport'] = copy.deepcopy(publication)
        extra['publication'] = publication
    elif verb == 'resource':
        if not owner and not any(l['sessionId'] == actor['id'] and _link_current(work, l, workspace) for l in item.get('links', [])):
            work.error('Resource reports belong to the accountable or linked current owner.', 403)
        resources = item.setdefault('resources', [])
        match = next((i for i, r in enumerate(resources) if r['resourceId'] == args['resourceId']
                      and r['ownerSessionId'] == actor['id']), None)
        if match is None and len(resources) >= 32:
            work.error('The bounded resource-reference capacity is reached; no prior evidence was discarded.', 429)
        resource = {key: args[key] for key in ('resourceId', 'kind', 'reportedState', 'evidenceReference')}
        resource.update(ownerSessionId=actor['id'], recordedAt=time.time(), attribution='owner_report',
                        independentlyVerified=False)
        if match is None:
            resources.append(resource)
        else:
            resources[match] = resource
        extra['resource'] = copy.deepcopy(resource)
    elif verb == 'link':
        if item['disposition'] != 'open':
            work.error('Reopen closed Work explicitly before linking execution.')
        row = work.service._session(args['linkedSessionId'], hydrate=False)
        if row.get('workspace') != workspace['path'] or row.get('sessionKind', 'root') != 'root':
            work.error('Link an existing root in this workspace.', 403)
        if row.get('status') in {'stopping', 'stopped'}:
            work.error('Stopped work cannot gain a new execution link.', 403)
        links = item.setdefault('links', [])
        identity = (row['id'], args['candidate'], args['role'], item['promiseRevision'])
        prior = next((l for l in links if (l['sessionId'], l['candidate'], l['role'], l['promiseRevision']) == identity), None)
        if prior:
            work.error('This exact relationship is already retained; inspect it before changing responsibility.')
        if len(links) >= 32 or args['role'] == 'executor' and sum(l['role'] == 'executor' for l in links) >= 8:
            work.error('The bounded execution-link capacity is reached; no links were discarded.', 429)
        link = {'sessionId': row['id'], 'candidate': args['candidate'], 'role': args['role'],
                'promiseRevision': item['promiseRevision'], 'interruptionRevision': row.get('interruptionRevision', 0),
                'recordedBy': actor['id'], 'recordedAt': time.time()}
        if bool(args.get('taskId')) != bool(args.get('claimId')):
            work.error('An execution task link needs both exact task and claim identities.', 400)
        if args.get('taskId'):
            value = work.service.assignments.current(row)
            task = work.service.assignments.tasks.get(value, args['taskId'])
            if not task or (work.service.assignments.current(actor) or {}).get('id') != value['id']:
                work.error('The execution task must belong to both linked assignment members.', 403)
            claim = work.service.assignments.tasks.claim_status(value, task)
            if not claim or claim['id'] != args['claimId'] or claim['sessionId'] != row['id'] or claim['status'] != 'current':
                work.error('The execution claim is stale or belongs to another holder.', 409)
            link.update(assignmentId=value['id'], taskId=task['id'], claimId=claim['id'])
        links.append(link)
        if args['role'] == 'executor' and item.get('candidate') != args['candidate']:
            item.update(candidate=args['candidate'], verification='not_verified', criterionEvidence={},
                        humanDecision='not_recorded', humanDecisionSequence=0,
                        latestOutcome='not_reported', execution='not_started',
                        delivery='not_recorded', deliveryReport=None)
        extra['link'] = copy.deepcopy(link)
    elif verb == 'report':
        event_promise = args['promiseRevision']
        if event_promise > item['promiseRevision']:
            work.error('The report names an unknown future promise.')
        links = [l for l in item.get('links', []) if l['sessionId'] == actor['id']
                 and l['candidate'] == args['candidate'] and l['promiseRevision'] == event_promise]
        if not owner and not any(_link_current(work, l, workspace) for l in links):
            work.error('A report needs its exact current linked execution/reviewer responsibility.', 403)
        if event_promise == item['promiseRevision'] and not set(args['criteria']) <= {c['id'] for c in item['criteria']}:
            work.error('Report criteria must belong to this exact promise.', 400)
        qualification = _qualification(work, actor, item, args)
        extra['evidence'] = {key: copy.deepcopy(args[key]) for key in
            ('candidate', 'outcome', 'report', 'criteria', 'checkReceiptId', 'references') if key in args}
        extra['evidence'].update(qualification=qualification, reportedBy=actor['id'],
                                criteriaValidated=event_promise == item['promiseRevision'],
                                source='host-bound collection' if args.get('checkReceiptId') else 'agent report')
        # Historical attempts stay in immutable receipts, never current verdicts.
        if event_promise == item['promiseRevision']:
            item['execution'] = 'reported'
            if not item.get('candidate'):
                item['candidate'] = args['candidate']
            if item['candidate'] == args['candidate']:
                item['latestOutcome'] = args['outcome']
                if args['outcome'] in {'failed', 'unknown', 'not_run'}:
                    item['verification'] = 'not_verified'
                    item['criterionEvidence'] = {}
                    item['counterevidenceSequence'] = item['eventCount'] + 1
                elif qualification != 'participant_report':
                    checks = item.setdefault('criterionEvidence', {})
                    for criterion in args['criteria']:
                        checks[criterion] = {'candidate': args['candidate'], 'qualification': qualification,
                                             'checkReceiptId': args['checkReceiptId'], 'reviewer': actor['id']}
                    required = {c['id'] for c in item['criteria']}
                    item['verification'] = ('independent_checks_passed' if required and all(
                        checks.get(c, {}).get('qualification') == 'independent_check' for c in required)
                        else 'owner_checks_recorded')
    elif verb == 'decision':
        if origin not in {'ui', 'user'}:
            work.error('Only an authenticated human action records a human decision.', 403)
        if args['promiseRevision'] != item['promiseRevision'] or args['candidate'] != item.get('candidate'):
            work.error('Human decision must name the current exact promise and candidate.')
        item['humanDecision'] = args['decision']
        item['humanDecisionSequence'] = item['eventCount'] + 1
        extra['decision'] = {key: args[key] for key in ('decision', 'candidate', 'promiseRevision')}
    elif verb == 'disposition':
        if not args['reason'].strip():
            work.error('A disposition needs a reason.', 400)
        if args['disposition'] == item['disposition']:
            work.error('The Work item already has that disposition.')
        if args['disposition'] == 'closed':
            if args['basis'] == 'reopened':
                work.error('Closing needs a closure basis.', 400)
            accepted_current = (item['humanDecision'] == 'accepted'
                                and item.get('humanDecisionSequence', 0) > item.get('counterevidenceSequence', 0))
            if args['basis'] == 'satisfied' and item['verification'] != 'independent_checks_passed' and not accepted_current:
                work.error('Satisfied closure requires qualified criteria or an actual human acceptance decision.')
            if args['basis'] == 'satisfied' and (work.blocked_by(item) or item.get('blockedReason')):
                work.error('Blocked dependencies must be resolved before satisfied closure.')
        elif args['basis'] != 'reopened':
            work.error('Opening a closed item needs an explicit corrective reopening.', 400)
        extra['disposition'] = {'previous': item['disposition'], 'current': args['disposition'],
                                'basis': args['basis'], 'reason': args['reason']}
        item['disposition'] = args['disposition']
        item['dispositionReason'] = args['reason']
        item['dispositionBasis'] = args['basis']
        if args['disposition'] == 'open':
            item.update(verification='not_verified', criterionEvidence={}, humanDecision='not_recorded',
                        humanDecisionSequence=0)
    else:
        work.error('Unsupported Work mutation.', 400)
    item.update(revision=item['revision'] + 1, eventCount=item['eventCount'] + 1, updatedAt=time.time())
    return extra, event_promise
