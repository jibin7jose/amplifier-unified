"""Explicit validation links existing candidate, Operations and Output owners."""
import asyncio
import copy
import json
import shlex
import time
import uuid
from contextlib import asynccontextmanager

from amplifier_operations.coordination import fingerprint
from . import integration_evidence as evidence

ACTIONS = {'integration.validate', 'integration.collect'}


def definitions(schema, string):
    from .result_artifacts import REFERENCE_HELP
    sid = {'sessionId': string(200)}
    publication = schema({'status': {'enum': ['unknown', 'not_published', 'published', 'partial', 'failed']},
                          'reference': string(2000)}, ['status'])
    result = schema({'repositoryPath': string(4000),
                     'references': {'type': 'array', 'maxItems': 16,
                         'description': 'Only exact local file references are collected. ' + REFERENCE_HELP,
                         'items': string(4500)},
                     'publication': publication}, ['repositoryPath'])
    return {
        'integration.validate': ('Explicitly run one exact validation command in a listed repository through existing shell policy and Operations. Checks the entire saved candidate before admission. Stable command retries never rerun uncertain work. Does not merge or publish.',
            schema({**sid, 'captureId': string(200), 'repositoryPath': string(4000), 'command': string(16000),
                    'timeout': {'type': 'integer', 'minimum': 1, 'maximum': 3600}},
                   ['sessionId', 'captureId', 'repositoryPath', 'command'])),
        'integration.collect': ('Explicitly collect up to eight exact validation invocations for one candidate. Reads native operation outcomes, rechecks source, retains declared exact artifacts and records per-repository partial results. Publication values are labeled self-reports; no merge, publication or command execution occurs.',
            schema({**sid, 'captureId': string(200),
                    'validationIds': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'uniqueItems': True, 'items': string(200)},
                    'results': {'type': 'array', 'maxItems': 8, 'items': result}},
                   ['sessionId', 'captureId', 'validationIds'])),
    }


def authority(app, sid, *, admission=False):
    source = app._session(sid, hydrate=False)
    assignment = app.assignments.current(source)
    if admission:
        if source.get('status') == 'stopping' or source.get('configurationBusy') or source.get('fileAccessPending'):
            raise ValueError('Wait for Stop or configuration changes to settle before validating.')
        if source.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'taking-over'}:
            raise ValueError('This conversation is read-only on this host.')
        if assignment and (assignment['status'] != 'active' or
                           (assignment.get('integration') or {}).get('responsibleSessionId') != sid):
            raise ValueError('The assignment owner must explicitly name this member responsible for integration before validating.')
    return {'binding': evidence.binding(source), 'interruptionRevision': source.get('interruptionRevision', 0),
            'ownership': copy.deepcopy(source.get('ownership')),
            'assignment': None if not assignment else {'id': assignment['id'], 'revision': assignment['revision'],
                'status': assignment['status'], 'responsibleSessionId': (assignment.get('integration') or {}).get('responsibleSessionId')}}


def require_authority(app, sid, expected, *, admission=False):
    if authority(app, sid, admission=admission) != expected:
        raise ValueError('Integration responsibility, Stop or execution binding changed. Inspect the retained evidence.')


def save(app, identity, digest, value):
    app.db.commit()
    try:
        app.db.execute('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)', (identity, digest, json.dumps(value)))
        app._save_changes(sessions=())
    except BaseException:
        if app.db.in_transaction:
            app.db.rollback()
        raise


async def check(app, sid, capture_id):
    # Parent retries reuse only committed parent results. An incomplete attempt
    # must observe current source, not reuse a check saved before its rollback.
    return (await evidence.dispatch(app, 'integration.check', {'sessionId': sid, 'id': capture_id},
                                    'ui', str(uuid.uuid4()), None))['result']


@asynccontextmanager
async def bound_repositories(app, candidate):
    roots = [row['repositoryPath'] for row in candidate['repositories']]
    async with app.worktrees.mutations:
        async with asyncio.timeout(30):
            current = await asyncio.gather(*(asyncio.to_thread(app.worktrees.repository_path, candidate['binding'], root) for root in roots))
        if current != roots:
            raise ValueError('Repository bindings changed before retaining integration evidence.')
        yield


async def admit(app, sid, identity, command):
    """Called only from the worker's existing control path after shell approval."""
    async with app.lock:
        run = copy.deepcopy(evidence.receipt(app, sid, identity))
        if run.get('kind') != 'validation' or run.get('operationCommand') != command or run.get('phase') != 'admitting':
            raise ValueError('This exact integration invocation is no longer awaiting admission.')
        require_authority(app, sid, run['authority'], admission=True)
    current = await check(app, sid, run['captureId'])
    async with bound_repositories(app, current), app.lock:
        require_authority(app, sid, run['authority'], admission=True)
        latest = evidence.receipt(app, sid, identity)
        if latest.get('phase') != 'admitting' or current['status'] != 'matched':
            raise ValueError('The exact candidate no longer matches. No validation command was admitted.')
        latest['admissionCheckId'] = current['id']
        latest['admittedAt'] = time.time()
        latest['phase'] = 'admitted'
        value = app.collaboration.receipt(identity)
        value['result'] = latest
        app.collaboration.save(identity, value)
        app._save_changes(sessions=())
    return {'admitted': True, 'validationId': identity}


async def submit(app, identity):
    async with app.lock:
        value = app.collaboration.receipt(identity)
        run = value['result']
        sid = run['sessionId']
    try:
        # Operations keeps the request receipt; this record retains only its
        # identity and exact declared invocation, never a second process log.
        require_authority(app, sid, run['authority'], admission=True)
        result = await app.operations.mutate('operations.submit', {
            'sessionId': sid, 'requestId': run['operationRequestId'],
            'command': run['operationCommand'], 'timeout': run['timeout'],
        }, run['origin'], integration_id=identity)
        phase = result['state']
    except BaseException:
        # An exception cannot establish whether spawn occurred. The original
        # Operations receipt/observation remains authoritative on later reads.
        phase = 'outcome_unknown'
    async with app.lock:
        value = app.collaboration.receipt(identity)
        value['result'].update(phase=phase, updatedAt=time.time())
        app.collaboration.save(identity, value)
        app._save_changes(sessions=())


def operation(app, sid, run):
    try:
        request = app.operations.requests.read(sid, run['operationRequestId'])
    except ValueError:
        return {'requestState': 'unknown', 'state': 'outcome_unknown'}
    result = {'requestId': request['requestId'], 'requestState': request['state']}
    if not request.get('operationId'):
        return {**result, 'state': 'rejected' if request['state'] == 'rejected' else 'outcome_unknown'}
    if not run.get('admissionCheckId'):
        return {**result, 'state': 'outcome_unknown', 'detail': 'No confirmed post-approval candidate admission.'}
    try:
        observed = app.operations.status(sid, request['operationId'])
    except ValueError:
        return {**result, 'state': 'outcome_unknown', 'operationId': request['operationId']}
    # The native owner's identity must agree with the saved admission. A
    # caller-supplied operation ID or an unrelated later success is insufficient.
    expected_runtime = run['authority']['binding'].get('runtimeSessionId') or run['authority']['binding'].get('nativeIdentity') or sid
    if (observed.get('source') != 'process' or observed.get('runtimeSessionId') != expected_runtime
            or not request.get('admission', {}).get('owner_id')
            or observed.get('ownerId') != request['admission']['owner_id']):
        return {**result, 'state': 'outcome_unknown', 'detail': 'Native operation identity differs from admission.'}
    return {**result, **{key: observed.get(key) for key in (
        'id', 'revision', 'state', 'source', 'runtimeSessionId', 'ownerId', 'returncode',
        'outputComplete', 'captureComplete', 'latestCursor', 'droppedOutputBytes', 'updatedAt')}}


def outcome(native, before, after):
    if native['state'] in {'failed', 'cancelled', 'rejected'}:
        return 'failed'
    if before != 'matched' or after == 'mismatch':
        return 'mismatch'
    if (native['state'] != 'completed' or native.get('returncode') != 0 or
            native.get('outputComplete') is not True or native.get('captureComplete') is not True
            or native.get('droppedOutputBytes', 0) != 0 or after != 'matched'):
        return 'unknown'
    return 'passed'


async def collect(app, args, origin, identity, digest, captured):
    files = {}
    try:
        return await _collect(app, args, origin, identity, digest, captured, files)
    finally:
        for batch in files.values():
            batch.close()


async def _collect(app, args, origin, identity, digest, captured, files):
    sid = args['sessionId']
    async with app.lock:
        candidate = copy.deepcopy(evidence.receipt(app, sid, args['captureId']))
        if candidate['kind'] != 'capture':
            raise ValueError('Choose a saved candidate capture.')
        runs = [copy.deepcopy(evidence.receipt(app, sid, key)) for key in args['validationIds']]
        roots = {row['repositoryPath'] for row in candidate['repositories']}
        if any(row['kind'] != 'validation' or row['captureId'] != candidate['id'] for row in runs):
            raise ValueError('Choose validation invocations of this exact candidate.')
        by_root = {row['repositoryPath']: row for row in runs}
        if len(by_root) != len(runs):
            raise ValueError('Choose one exact validation invocation per repository.')
        declared = {row['repositoryPath']: row for row in args.get('results', [])}
        if len(declared) != len(args.get('results', [])) or not set(declared) <= roots:
            raise ValueError('Declare each listed repository at most once.')
        if sum(len(row.get('references', [])) for row in declared.values()) > 16:
            raise ValueError('At most sixteen exact result references may be retained.')
        native = {root: operation(app, sid, run) for root, run in by_root.items()}
    after = await check(app, sid, candidate['id'])
    from .result_artifacts import prepare_files
    from . import retained_files
    for root, row in declared.items():
        files[root] = await retained_files.prepared(prepare_files, root, row.get('references', []))
        if len(files[root]) != len(set(row.get('references', []))):
            raise ValueError('Result files require local path@sha256:digest references.')
    # Bracket result capture too; artifacts can survive later source changes,
    # while the candidate match always reports this collection's observation.
    final = await check(app, sid, candidate['id'])
    async with bound_repositories(app, final), app.lock:
        require_authority(app, sid, captured)
        prior = app.collaboration.duplicate(identity, digest)
        if prior:
            return prior
        repositories = []
        app.db.commit()
        try:
            for expected in candidate['repositories']:
                root = expected['repositoryPath']
                run = by_root.get(root)
                observation = next(row['observed'] for row in final['checks'] if row['kind'] == 'repository' and row['repositoryPath'] == root)
                row = {'repositoryPath': root, 'resultingSource': observation, 'status': 'not_validated', 'candidateStatus': final['status'],
                       'artifacts': [], 'publication': {'status': 'unknown', 'evidence': 'not_observed'}}
                if run:
                    native_now = operation(app, sid, run)
                    # A changed/late terminal can be collected with a new ID;
                    # never certify a moving read under this observation.
                    if native_now != native[root]:
                        native[root] = {'state': 'outcome_unknown', 'detail': 'Operation changed during collection.'}
                    state = outcome(native[root], run['beforeStatus'], final['status'])
                    if after['status'] != 'matched' and state == 'passed': state = after['status']
                    if run['authority'] != captured and state == 'passed': state = 'unknown'
                    row.update(status=state, validationId=run['id'], invocation={'command': run['command'], 'timeout': run['timeout']}, operation=native[root])
                for item in files.get(root, []):
                    artifact = {key: value for key, value in item.items() if key not in {'resource', 'pieces', 'bodyFormat', 'path', 'filename'}}
                    if item['status'] == 'retained':
                        if app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] >= 10000:
                            raise ValueError('The retained output relationship limit is reached.')
                        body = await retained_files.adopt(app.db, item)
                        output = app.outputs.store.create(sid, {'kind': 'file', 'filename': item['filename'],
                            'title': item['filename'], 'body': body, 'bodyFormat': item['bodyFormat'], 'bytes': item['bytes'], 'sha256': item['sha256'],
                            'versionEvidence': 'snapshot', 'origin': {'source': origin},
                            'integrationResult': {'collectionId': identity, 'captureId': candidate['id'], 'repositoryPath': root,
                                                  'sourceRevision': observation.get('sourceRevision'), 'reference': item['reference']}})
                        artifact.update(outputId=output['id'], downloadUrl='/api/outputs/' + output['id'] + '/content')
                    row['artifacts'].append(artifact)
                if any(item['status'] != 'retained' for item in row['artifacts']) and row['status'] == 'passed':
                    row['status'] = 'incomplete'
                if declared.get(root, {}).get('publication'):
                    row['publication'] = {**declared[root]['publication'], 'evidence': 'reported', 'reportedBy': sid,
                                          'actor': origin, 'reportedAt': time.time(), 'ownerConfirmed': False}
                repositories.append(row)
            states = {row['status'] for row in repositories}
            status = 'mismatch' if 'mismatch' in {after['status'], final['status']} else 'passed' if states == {'passed'} else 'failed' if 'failed' in states else 'partial'
            result = {'id': identity, 'kind': 'collection', 'sessionId': sid, 'captureId': candidate['id'],
                      'capturedAt': time.time(), 'status': status, 'candidateStatus': final['status'], 'authority': captured, 'repositories': repositories,
                      'checkIds': [after['id'], final['id']], 'validationIds': args['validationIds'],
                      'limitations': ['Only the named commands were observed. Zero exit does not prove test quality or semantic integration.',
                                      'Repository and worker-file observations are not a hermetic subprocess environment.',
                                      'Publication reports are not owner-confirmed publication evidence.']}
            value = {'accepted': True, 'commandAction': 'integration.collect', 'result': result}
            app.collaboration.insert(identity, digest, value)
            app._save_changes(sessions=())
        except BaseException:
            if app.db.in_transaction: app.db.rollback()
            raise
    return value


async def dispatch(app, action, args, origin, command_id, caller):
    from .service import AppError
    try:
        sid = args['sessionId']
        identity = command_id or str(uuid.uuid4())
        if len(identity) > 160:
            raise ValueError('Validation command identity must be at most 160 characters.')
        digest = fingerprint([action, args, origin])
        async with app.lock:
            source = app.collaboration.source({'sessionId': sid}, origin, caller, hydrate=False)
            if source['id'] != sid:
                raise ValueError('Integration actions belong to the calling conversation.')
            prior = app.collaboration.duplicate(identity, digest)
            if prior: return prior
            captured = authority(app, sid, admission=action == 'integration.validate')
            candidate = copy.deepcopy(evidence.receipt(app, sid, args['captureId']))
            if candidate['kind'] != 'capture': raise ValueError('Choose a saved candidate capture.')
        if action == 'integration.collect':
            async with asyncio.timeout(300):
                return await collect(app, args, origin, identity, digest, captured)
        root = args['repositoryPath']
        if root not in {row['repositoryPath'] for row in candidate['repositories']}:
            raise ValueError('Choose one exact repository from the saved candidate.')
        if not args['command'].strip(): raise ValueError('Choose an explicit validation command.')
        before = await check(app, sid, candidate['id'])
        async with app.lock:
            require_authority(app, sid, captured, admission=True)
            prior = app.collaboration.duplicate(identity, digest)
            if prior: return prior
            result = {'id': identity, 'kind': 'validation', 'sessionId': sid, 'captureId': candidate['id'],
                'repositoryPath': root, 'command': args['command'], 'timeout': args.get('timeout', 300),
                'operationCommand': 'cd -- ' + shlex.quote(root) + ' && (\n' + args['command'] + '\n)',
                'operationRequestId': 'integration:' + identity, 'authority': captured,
                'origin': origin, 'capturedAt': time.time(), 'beforeCheckId': before['id'], 'beforeStatus': before['status'],
                'status': 'pending' if before['status'] == 'matched' else before['status'],
                'phase': 'admitting' if before['status'] == 'matched' else 'not_started'}
            value = {'accepted': True, 'commandAction': action, 'result': result}
            save(app, identity, digest, value)
            if result['phase'] == 'admitting': app._task(submit(app, identity))
            return copy.deepcopy(value)
    except (ValueError, OSError, TimeoutError) as exc:
        raise AppError(str(exc) or 'Integration preparation timed out.', 409) from None
