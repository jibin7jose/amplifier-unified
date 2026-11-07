"""Bounded combined-candidate evidence in the existing command receipt owner."""
import asyncio
import copy
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import time
import uuid

from amplifier_operations.coordination import fingerprint

MAX_INSPECTION_SECONDS = 120


def definitions(schema, string):
    from .integration_validation import definitions as validation_definitions
    sid = {'sessionId': string(200)}
    identity = {**sid, 'id': string(200)}
    module_name = {**string(200), 'pattern': r'^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$'}
    modules = {'type': 'array', 'maxItems': 16, 'uniqueItems': True, 'items': module_name}
    repository = schema({'repositoryPath': string(4000), 'sourceRevision': string(100)}, ['repositoryPath', 'sourceRevision'])
    dependency = schema({'module': module_name, 'repositoryPath': string(4000), 'path': string(4000),
        'sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}, ['module', 'repositoryPath', 'path', 'sha256'])
    return {
        **validation_definitions(schema, string),
        'runtime.provenance': ('Inspect already-loaded Core/Foundation, mounted object types and explicit project modules in the running worker. Never imports requested code, starts a worker, or proves tests/isolation. Current file hashes are distinct from exact imported bytes.', schema({**sid, 'modules': modules}, ['sessionId'])),
        'integration.capture': ('Retain a bounded combined candidate from inspected repository sourceRevision values and expected loaded-module origins/hashes. Saves evidence only; never tests, imports, installs or changes dependencies. Mismatch and unknown remain explicit.', schema({**sid, 'purpose': string(500),
            'repositories': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': repository},
            'dependencies': {'type': 'array', 'maxItems': 16, 'items': dependency}}, ['sessionId', 'repositories', 'dependencies'])),
        'integration.check': ('Recheck an exact saved combined candidate for repository/dependency drift. A matching observation does not certify tests or imported bytecode. Exact command retries return the original check.', schema(identity, ['sessionId', 'id'])),
        'integration.read': ('Read one retained combined-candidate capture/check without inspecting source or starting work.', schema(identity, ['sessionId', 'id'])),
        'integration.list': ('List this conversation\'s retained combined-candidate receipts. Use nextBefore for older pages. Does not inspect source or start work.', schema({**sid, 'before': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, ['sessionId'])),
    }


def binding(session):
    return {key: session.get(key) for key in ('id', 'workspace', 'workingDirectory', 'executionRevision', 'runtimeSessionId', 'nativeIdentity')}


def receipt(app, sid, identity):
    row = app.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    value = json.loads(row[0]) if row else {}
    if (value.get('commandAction') not in {'integration.capture', 'integration.check', 'integration.validate', 'integration.collect'}
            or value.get('result', {}).get('sessionId') != sid):
        raise ValueError('Choose this conversation\'s exact candidate receipt.')
    return value['result']


async def observed_worker(app, sid, names):
    if not app.runtime or not hasattr(app.runtime, 'provenance'):
        return {'status': 'unknown', 'reason': 'This runtime has no loaded-source inspection adapter.'}
    try:
        async with asyncio.timeout(12):
            value = await app.runtime.provenance(sid, names)
        if (not isinstance(value, dict) or not isinstance(value.get('modules', []), list)
                or len(value.get('modules', [])) > 64
                or any(not isinstance(row, dict) or not isinstance(row.get('module'), str) for row in value.get('modules', []))
                or len(json.dumps(value, allow_nan=False).encode()) > 512 * 1024):
            raise ValueError('Invalid bounded worker report')
        return value
    except (TypeError, ValueError):
        return {'status': 'unknown', 'reason': 'Worker source inspection returned an invalid or over-limit report.'}
    except (TimeoutError, RuntimeError):
        return {'status': 'unknown', 'reason': 'Worker source inspection did not return.'}


async def repository_snapshot(app, path):
    try:
        async with asyncio.timeout(30):
            value = await asyncio.to_thread(app.worktrees.git.inspect, path)
        return {key: value[key] for key in ('root', 'head', 'sourceRevision', 'dirty')}
    except (ValueError, OSError, TimeoutError, subprocess.TimeoutExpired):
        return {'root': path, 'status': 'unknown', 'detail': 'Repository inspection failed or exceeded its time/file limits.'}


def compare(repositories, dependencies, observed, worker):
    checks = []
    for expected, actual in zip(repositories, observed):
        state = ('unknown' if not actual.get('sourceRevision') else
                 'matched' if expected['sourceRevision'] == actual['sourceRevision'] else 'mismatch')
        checks.append({'kind': 'repository', 'repositoryPath': expected['repositoryPath'], 'status': state,
                       'expectedSourceRevision': expected['sourceRevision'], 'observed': actual})
    loaded = {row['module']: row for row in worker.get('modules', [])}
    for dependency in dependencies:
        row = loaded.get(dependency['module']) or {}
        actual = row.get('origin')
        expected_path = str(Path(dependency['repositoryPath']) / dependency['path'])
        if row.get('loaded') is not True or worker.get('status') != 'observed' or worker.get('mounted') is not True or row.get('fileStatus') != 'observed':
            state = 'unknown'
        else:
            state = 'matched' if actual == expected_path and row.get('currentFileSha256') == dependency['sha256'] else 'mismatch'
        checks.append({'kind': 'dependency', 'module': dependency['module'], 'status': state,
                       'expectedOrigin': expected_path, 'expectedSha256': dependency['sha256'],
                       'observed': row, 'importedBytesVerified': False})
    states = {row['status'] for row in checks}
    return ('mismatch' if 'mismatch' in states else 'unknown' if 'unknown' in states else 'matched'), checks


async def _dispatch(app, action, args, origin, command_id, caller):
    from .errors import AppError
    sid = args['sessionId']
    try:
        async with app.lock:
            source = app._session(sid, hydrate=False)
            if origin not in {'ui', 'user'}:
                root = app.collaboration.source({}, origin, caller, hydrate=False)
                if root['id'] != sid:
                    raise ValueError('Inspect provenance from the calling conversation.')
            if action == 'integration.read':
                return {'accepted': True, 'result': receipt(app, sid, args['id'])}
            if action == 'integration.list':
                fields = ('id', 'kind', 'status', 'purpose', 'capturedAt', 'captureId')
                columns = ','.join("json_extract(receipt,'$.result." + key + "')" for key in fields)
                rows = app.db.execute("SELECT rowid," + columns + " FROM commands WHERE json_extract(receipt,'$.commandAction') IN ('integration.capture','integration.check','integration.validate','integration.collect') AND json_extract(receipt,'$.result.sessionId')=? AND rowid<? ORDER BY rowid DESC LIMIT 17", (sid, args.get('before', 9007199254740991))).fetchall()
                return {'accepted': True, 'result': {'items': [{key: value for key, value in zip(fields, row[1:]) if value is not None} for row in rows[:16]],
                    'nextBefore': rows[15][0] if len(rows) > 16 else None}}
            captured_binding = binding(source)
            captured_source = copy.deepcopy(captured_binding)
            if action != 'runtime.provenance':
                identity = command_id or str(uuid.uuid4())
                digest = fingerprint([action, args, origin])
                prior = app.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
                if prior:
                    if prior[0] != digest:
                        raise ValueError('This command identity already has different contents.')
                    return {**json.loads(prior[1]), 'duplicate': True}
                if action == 'integration.check':
                    candidate = receipt(app, sid, args['id'])
                    if candidate['kind'] != 'capture':
                        raise ValueError('Choose a capture receipt, not another check.')
                    repositories = copy.deepcopy(candidate['repositories'])
                    dependencies = copy.deepcopy(candidate['dependencies'])
                    purpose = candidate.get('purpose')
                else:
                    repositories = copy.deepcopy(args['repositories'])
                    dependencies = copy.deepcopy(args['dependencies'])
                    purpose = args.get('purpose')
        if action == 'runtime.provenance':
            result = await observed_worker(app, sid, args.get('modules', []))
        else:
            # Git discovery is outside app.lock. It performs no source edits.
            # Resolve explicit repository authority through the existing owner.
            async with asyncio.timeout(30):
                roots = await asyncio.gather(*(asyncio.to_thread(app.worktrees.repository_path, captured_source, row['repositoryPath']) for row in repositories))
            resolved = {row['repositoryPath']: root for row, root in zip(repositories, roots)}
            for row, root in zip(repositories, roots):
                row['repositoryPath'] = root
            if len(roots) != len(set(roots)):
                raise ValueError('Choose each repository once.')
            if len({row['module'] for row in dependencies}) != len(dependencies):
                raise ValueError('Choose each dependency module once.')
            for dependency in dependencies:
                root = resolved.get(dependency['repositoryPath'])
                if root is None and dependency['repositoryPath'] in roots:
                    root = dependency['repositoryPath']
                path = Path(dependency['path'])
                if root not in roots or path.is_absolute() or '..' in path.parts or '.git' in path.parts or not path.parts:
                    raise ValueError('Dependency paths must stay inside an explicitly listed repository.')
                expected_path = (Path(root) / path).resolve()
                if not expected_path.is_relative_to(root):
                    raise ValueError('The dependency path escapes its listed repository.')
                dependency.update(repositoryPath=root, path=str(expected_path.relative_to(root)))
            observed = await asyncio.gather(*(repository_snapshot(app, root) for root in roots))
            worker = await observed_worker(app, sid, [row['module'] for row in dependencies])
            expected_runtime = captured_binding.get('runtimeSessionId') or captured_binding.get('nativeIdentity') or sid
            if worker.get('runtimeSessionId') and worker['runtimeSessionId'] != expected_runtime:
                worker = {'status': 'unknown', 'reason': 'Observed worker identity differs from this execution binding.'}
            after = await asyncio.gather(*(repository_snapshot(app, root) for root in roots))
            for index, (before, current) in enumerate(zip(observed, after)):
                if before.get('sourceRevision') != current.get('sourceRevision'):
                    observed[index] = {'root': roots[index], 'status': 'unknown',
                                       'detail': 'Repository changed during the combined observation.'}
            status, checks = compare(repositories, dependencies, observed, worker)
            # A check must also match the exact captured placement, not merely
            # an equivalent HEAD in a replacement execution folder.
            if action == 'integration.check' and candidate['binding'] != captured_binding:
                status = 'mismatch'
                checks.append({'kind': 'execution', 'status': 'mismatch', 'detail': 'Execution binding changed since capture.'})
            if action == 'integration.check' and dependencies and candidate['worker'].get('workerInstance') != worker.get('workerInstance'):
                if status != 'mismatch': status = 'unknown'
                checks.append({'kind': 'worker', 'status': 'unknown', 'detail': 'Worker instance changed since capture; source matching cannot prove the previous loaded state.'})
            result = {'id': identity, 'sessionId': sid, 'kind': 'check' if action == 'integration.check' else 'capture',
                      'status': status, 'capturedAt': time.time(), 'purpose': purpose,
                      'binding': captured_binding, 'repositories': repositories, 'dependencies': dependencies,
                      'checks': checks, 'worker': worker, 'testOutcome': 'not_run', 'isolationVerified': False,
                      'limitations': ['Only listed repositories and loaded modules are compared.',
                          'Ignored files outside explicit dependency observations and external process dependencies are not covered.',
                          'Matching current files do not prove exact imported bytes, semantic integration or test success.']}
            if action == 'integration.check':
                result['captureId'] = args['id']
        # Managed association can change without moving this session. Recheck
        # the canonical owner's current scope while its mutation lock prevents
        # archive/remove from racing between this check and the receipt commit.
        async with (app.worktrees.mutations if action != 'runtime.provenance' else nullcontext()):
            if action != 'runtime.provenance':
                async with asyncio.timeout(30):
                    current_roots = await asyncio.gather(*(asyncio.to_thread(app.worktrees.repository_path, captured_source, root) for root in roots))
                if roots != current_roots:
                    raise ValueError('Repository bindings changed during inspection. No candidate was retained.')
            async with app.lock:
                current = app._session(sid, hydrate=False)
                if binding(current) != captured_binding:
                    raise ValueError('The execution binding changed during inspection. No candidate was retained; inspect again.')
                if action == 'runtime.provenance':
                    return {'accepted': True, 'result': result}
                prior = app.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
                if prior:
                    if prior[0] != digest:
                        raise ValueError('This command identity already has different contents.')
                    return {**json.loads(prior[1]), 'duplicate': True}
                value = {'accepted': True, 'commandAction': action, 'result': result}
                app.db.commit()
                try:
                    app.db.execute('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)', (identity, digest, json.dumps(value)))
                    app._save_changes(sessions=())
                except BaseException:
                    if app.db.in_transaction:
                        app.db.rollback()
                    raise
                return value
    except (TimeoutError, subprocess.TimeoutExpired):
        raise AppError('Repository resolution exceeded its time limit. No candidate was retained.', 409) from None
    except (ValueError, OSError) as exc:
        raise AppError(str(exc), 409) from None


async def dispatch(app, action, args, origin, command_id, caller):
    from .integration_validation import ACTIONS, dispatch as validation_dispatch
    if action in ACTIONS:
        return await validation_dispatch(app, action, args, origin, command_id, caller)
    from .errors import AppError
    try:
        async with asyncio.timeout(MAX_INSPECTION_SECONDS):
            return await _dispatch(app, action, args, origin, command_id, caller)
    except TimeoutError:
        raise AppError('Combined inspection exceeded its time limit. Read retained observations before retrying.', 409) from None
