"""One global policy over existing qualified storage-retirement owners.

This owner chooses when to ask existing owners to reclaim unused code. It does
not delete files itself, and never treats source/result archives as a cache.
"""
import asyncio
import json
import sqlite3
from pathlib import Path
import time
import uuid

from amplifier_operations.coordination import fingerprint
from .errors import AppError

DEFAULTS = {'revision': 0, 'enabled': True, 'keepDays': 7, 'maxUnusedBytes': 10 * 1024**3,
            'workspaceArchiveKeepDays': None, 'operationOutputKeepDays': None}
POLICY = 'storage-cleanup-policy'
OBSERVATION = 'storage-cleanup-observation'
LATEST = 'storage-cleanup-latest'


def definitions(schema, string):
    return {
        'storage.cleanup.read': ('Read global automatic cleanup settings and the last receipt without scanning or starting work.', schema({}, [])),
        'storage.cleanup.evidence': ('Read one bounded page of saved-test historical sizes or Smart Tool receipt/body accounting. Does not open bodies, measure current files, start work or delete evidence.', schema({'owner': {'enum': ['tests', 'smartTools']}, 'before': {'type': 'integer', 'minimum': 1, 'maximum': 2**53-1}}, [])),
        'storage.cleanup.save': ('Save global retention under standing Full or user control. Optional temporary workspace and terminal operation-output expiry apply prospectively. Chats, saved outputs, request identities and uncertain work stay.', schema({
            'expectedRevision': {'type': 'integer', 'minimum': 0}, 'enabled': {'type': 'boolean'},
            'keepDays': {'type': 'integer', 'minimum': 0, 'maximum': 3650},
            'maxUnusedBytes': {'type': 'integer', 'minimum': 0, 'maximum': 2**53-1},
            'workspaceArchiveKeepDays': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 3650},
            'operationOutputKeepDays': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 3650},
        }, ['expectedRevision', 'enabled', 'keepDays', 'maxUnusedBytes'])),
        'storage.cleanup.applyPreset': ('Enable standing automatic cleanup for future temporary queued-test workspace copies (seven days) or settled operation output (30 days). Preserves other retention choices and existing promises. Available to the user and Full callers.', schema({
            'expectedRevision': {'type': 'integer', 'minimum': 0},
            'preset': {'enum': ['temporary-test-workspaces', 'temporary-operation-output']},
        }, ['expectedRevision', 'preset'])),
        'storage.cleanup.preview': ('Measure a bounded set of unused code copies and protected storage. Never starts workers or deletes files. Incomplete measurements are reported.', schema({}, [])),
        'storage.cleanup.run': ('Apply the saved cleanup policy under standing Full or user control. Each owner rechecks unused code before removal. Reuse the request ID to observe an uncertain outcome without replay.', schema({
            'expectedRevision': {'type': 'integer', 'minimum': 0}}, ['expectedRevision'])),
    }


def record(app, key, default=None):
    row = app.db.execute('SELECT receipt FROM commands WHERE id=?', (key,)).fetchone()
    return json.loads(row[0]).get('result') if row else default


def store(app, key, value):
    app.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',
        (key, key, json.dumps({'commandAction': key, 'result': value})))
    app.db.commit()


def policy(app):
    return {**DEFAULTS, **(record(app, POLICY, {}) or {})}


def public(value):
    return {key: item for key, item in value.items() if key not in {'_authority', '_observations'}} if value is not None else None


def read(app):
    return {'policy': policy(app), 'lastCleanup': public(record(app, LATEST)),
            'scope': 'All workspaces on this Amplifier installation. Unused code and dependencies, explicitly temporary retired test workspace copies, and opted-in settled operation output are eligible.',
            'preserved': 'Chats, working folders, saved test sources/results and outputs, request identities, current and rollback versions, uncertain work and unresolved task references are kept.'}


def request_maintenance(app):
    """Wake the single policy owner; a standalone qualifier never removes code."""
    manager = getattr(app, 'update_manager', None)
    if manager is not None:
        manager._storage_policy_due = True
        manager.wakeup.set()


def lock(app):
    if not hasattr(app, '_storage_cleanup_lock'):
        app._storage_cleanup_lock = asyncio.Lock()
    return app._storage_cleanup_lock


def capture(app, source, origin):
    authority = app.access_mode.capture_full(source, origin)
    if authority is None:
        raise AppError('Only the user or a calling conversation with Full access can change cleanup settings or request cleanup.', 403)
    return {**authority, 'workspace': source['workspace'],
            'nativeSessionId': source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id'],
            'executionRevision': source.get('executionRevision', 0),
            'interruptionRevision': source.get('interruptionRevision', 0)}


def check(app, authority):
    if authority is None:
        return  # User controls and durable automatic policy keep their existing lifetime.
    try:
        source = app._session(authority['sessionId'], hydrate=False)
        valid = (source['id'] == authority['sessionId']
                 and app.access_mode.full(source, authority['accessRevision'])
                 and not app.assignments.blocked(source)
                 and source['workspace'] == authority['workspace']
                 and (source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']) == authority['nativeSessionId']
                 and all(source.get(key, 0) == authority[key] for key in ('executionRevision', 'interruptionRevision')))
    except AppError:
        valid = False
    if not valid:
        raise ValueError('Cleanup authority changed or its work was stopped. No further removals were started.')


def retained_run(app, identity, expected_revision, source_id=None):
    previous = record(app, 'storage-cleanup-run:' + identity)
    if previous is not None and (previous.get('expectedRevision') != expected_revision
            or (previous.get('_authority') or {}).get('sessionId') != source_id):
        raise ValueError('This cleanup request ID was used with different contents or a different calling conversation.')
    return previous


def saved_command(app, identity, digest):
    saved = app.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
    if saved:
        if saved[0] != digest:
            raise AppError('This command ID was used with different contents or a different calling conversation.', 409)
        return {**public(json.loads(saved[1])), 'duplicate': True}


def observed_since(value, default):
    return value.get('unusedSince', default) if isinstance(value, dict) else value


def observations(seen, candidates, now, *, protected=(), present=None):
    """Reuse the existing ledger, not an unbounded fresh filesystem scan.

    Legacy timestamps retain their age but contribute no invented size.
    Unseen observations survive bounded passes; known protected/absent
    copies do not contribute pressure. Measurements never authorize removal.
    """
    protected = set(protected)
    present = present or {}
    result = {key: value for key, value in seen.items() if key not in protected
              and (key.split(':', 1)[0] not in present or key in present[key.split(':', 1)[0]])}
    for row in candidates:
        key = row['kind'] + ':' + row['id']
        result[key] = {'unusedSince': max(observed_since(result.get(key, now), now), row.get('lastUsedAt', 0)),
                       'fileBytes': row['fileBytes'], 'measuredAt': now}
    return result


def observed_bytes(seen):
    return sum(row.get('fileBytes', 0) for row in seen.values() if isinstance(row, dict))


def plan(candidates, settings, seen, now):
    rows = [{**item, 'unusedSince': max(observed_since(seen.get(item['kind'] + ':' + item['id'], now), now), item.get('lastUsedAt', 0))} for item in candidates]
    measured = {row['kind'] + ':' + row['id'] for row in rows}
    total = sum(item['fileBytes'] for item in rows) + observed_bytes({key: value for key, value in seen.items() if key not in measured})
    remaining = total
    for row in sorted(rows, key=lambda item: (item['unusedSince'], item['kind'], item['id'])):
        expired = now - row['unusedSince'] >= settings['keepDays'] * 86400
        pressure = bool(settings['maxUnusedBytes'] and remaining > settings['maxUnusedBytes'])
        row['selected'] = expired or pressure
        row['reason'] = ('Retention time reached' if expired else 'Unused copies measured across cleanup batches exceed the saved size target') if row['selected'] else 'Kept by your retention settings'
        if row['selected']:
            remaining -= row['fileBytes']
    return rows


def unresolved(app):
    held = set()
    for value, in app.db.execute("SELECT receipt FROM commands WHERE id LIKE 'storage-cleanup-run:%' AND json_extract(receipt,'$.result.status') IN ('running','interrupted','needs-inspection')"):
        for effect in json.loads(value)["result"].get("effects", []):
            if effect.get("status") == "attempting" and effect.get('recovery', {}).get('disposition') not in {'removed', 'not-detached'}:
                held.add((effect["kind"], effect["id"]))
    return held


def runtime_recovery(app, item):
    """Append exact detached-payload resolution; preserve the failed command."""
    resolution = {key: item[key] for key in ('retirementId', 'disposition')}
    resolution['resolvedAt'] = time.time()
    for key, raw in app.db.execute("SELECT id,receipt FROM commands WHERE id LIKE 'storage-cleanup-run:%' AND json_extract(receipt,'$.result.status') IN ('running','interrupted','needs-inspection')").fetchall():
        saved = json.loads(raw)['result']
        matched = False
        for effect in saved.get('effects', []):
            if (effect.get('status') == 'attempting' and effect.get('kind') == 'runtime-environment'
                    and effect.get('id') == item.get('project') and effect.get('stamp') == item.get('stamp')):
                effect['recovery'] = resolution
                matched = True
        if matched:
            store(app, key, saved)


async def preview(app):
    from . import update_retention, managed_runner_updates, test_dependency_layers, workspace_archive_retention, runtime_environment_retention
    manager = app.update_manager
    settings = policy(app)
    # Restore only scan position, never a prior eligibility decision. Preview
    # remains read-only; the policy run persists its next cursor in its receipt.
    saved = record(app, LATEST, {}) or {}
    for key, attribute in (('generationCursor', '_retention_cursor'), ('retiredCursor', '_retired_cursor'), ('runtimeCursor', '_runtime_environment_retention_cursor')):
        if not hasattr(manager, attribute) and isinstance(saved.get(key), str):
            setattr(manager, attribute, saved[key])
    generations = await update_retention.reclaim(manager, inspect_only=True)
    runtimes = await runtime_environment_retention.reclaim(manager, inspect_only=True)
    runner_inspection = {'protectedIds': [], 'complete': True}
    try:
        runners = await asyncio.wait_for(managed_runner_updates.retire(app, inspect_only=True, inspection=runner_inspection), 15)
        runner_complete = runner_inspection['complete']
    except (TimeoutError, ValueError, OSError):
        runners, runner_complete = [], False
    try:
        dependencies = await asyncio.to_thread(test_dependency_layers.inspect, app.data_dir)
    except (TimeoutError, ValueError, OSError):
        dependencies = {'candidates': [], 'protectedIds': [], 'complete': False}
    try:
        archives = await asyncio.to_thread(workspace_archive_retention.inspect, app.data_dir, settings, saved.get('workspaceArchiveCursor', 0))
    except (TimeoutError, ValueError, OSError, sqlite3.Error):
        archives = {'candidates': [], 'complete': False, 'workspaceArchiveCursor': saved.get('workspaceArchiveCursor', 0)}
    held = unresolved(app)
    candidates = [row for row in generations.get('candidates', []) + runtimes['candidates'] + (runners or []) + dependencies['candidates'] if (row['kind'], row['id']) not in held]
    now = time.time()
    observed_protected = (['ecosystem:' + key for key in generations.get('protectedIds', [])]
        + [runtime_environment_retention.KIND + ':' + key for key in runtimes['protectedIds']]
        + ['runner:' + key for key in runner_inspection['protectedIds']]
        + ['test-dependencies:' + key for key in dependencies['protectedIds']]
        + [kind + ':' + identity for kind, identity in held])
    folders = update_retention._folders(manager)
    present = {'ecosystem': {'ecosystem:' + row.name for row in folders},
               'runner': {'runner:' + row['id'] for row in app.smart_tools.state.get('installations', [])}}
    if 'presentIds' in runtimes:
        present[runtime_environment_retention.KIND] = {runtime_environment_retention.KIND + ':' + key for key in runtimes['presentIds']}
    if dependencies['complete']:
        present['test-dependencies'] = {'test-dependencies:' + row['id'] for row in dependencies['candidates']} | {
            'test-dependencies:' + key for key in dependencies['protectedIds']}
    seen = observations(record(app, OBSERVATION, {}) or {}, candidates, now,
                        protected=observed_protected, present=present)
    rows = plan(candidates, settings, seen, now)
    # Workspace promises have their own captured duration, never code-cache pressure.
    rows.extend(row for row in archives['candidates'] if (row['kind'], row['id']) not in held)
    protected = []
    eligible = {(row['kind'], row['id']) for row in rows}
    # This is metadata enumeration, not a scan of workspaces or chat history.
    for folder in folders:
        if ('ecosystem', folder.name) not in eligible:
            code = generations.get('retentionReasons', {}).get(folder.name)
            reasons = {
                'unverified_generation': 'The installed component generation could not be verified',
                'unverified_source': 'The recorded source download or tracked files could not be verified',
                'local_source_changes': 'Local source edits or unpublished commits are kept',
                'unverified_generated_files': 'Extra files could not be proved disposable installation output',
                'changed_during_inspection': 'References or files changed, or their final check could not finish',
            }
            protected.append({'kind': 'ecosystem', 'id': folder.name, 'label': 'Component version kept',
                              'reason': reasons.get(code, 'Current, rollback, referenced, changed, or not fully verified'),
                              **({'reasonCode': code} if code in reasons else {})})
    for item in app.smart_tools.state.get('installations', []):
        if item.get('runnerCandidate') and ('runner', item['id']) not in eligible:
            protected.append({'kind': 'runner', 'id': item['id'], 'label': 'Test runner version kept',
                              'reason': 'Current, rollback, referenced, changed, or not fully verified'})
    for identity in dependencies['protectedIds']:
        protected.append({'kind': 'test-dependencies', 'id': identity, 'label': 'Reusable test dependencies kept',
                          'reason': 'Referenced, changed, or not fully verified'})
    for identity in runtimes['protectedIds']:
        protected.append({'kind': runtime_environment_retention.KIND, 'id': identity, 'label': 'Worker dependencies kept',
                          'reason': 'Referenced, modified, or not fully verified'})
    # Preserve all test artifacts and engine roots. A terminal chat is not
    # proof that a native launch cannot still finish, nor an archive grant.
    for row in protected:
        if (row['kind'], row.get('id')) in held:
            row['reason'] = 'An earlier cleanup has an unconfirmed outcome; its files are kept for inspection'
    protected.append({'kind': 'evidence', 'label': 'Saved work and test evidence',
                      'reason': 'Source and result archives, project folders, histories, legacy/manual workspaces and uncertain work stay protected'})
    protected_measured = 0
    protected_complete = True
    measured_paths = {path for row in candidates for path in row.get('paths', [])}
    from .runtime_environment import project_path
    for index, row in enumerate(protected):
        if not row.get('id') or index >= 4 or row['kind'] in {'test-dependencies', runtime_environment_retention.KIND}:
            row['fileBytes'] = None
            protected_complete = False
            continue
        paths = ([str(manager.directory / 'releases' / row['id']), str(project_path(manager.home, row['id']))]
                 if row['kind'] == 'ecosystem' else [str(Path(app.data_dir) / 'smart-tools/installs' / row['id'])])
        paths = [path for path in paths if path not in measured_paths and Path(path).exists()]
        try:
            measured = await update_retention._probe('measure', {'paths': paths}, timeout=5)
            row['fileBytes'] = measured['fileBytes']
            protected_measured += row['fileBytes']
            measured_paths.update(paths)
        except (ValueError, OSError, TimeoutError):
            row['fileBytes'] = None
            protected_complete = False
    complete = generations.get('complete', False) and runtimes['complete'] and runner_complete and dependencies['complete'] and archives['complete']
    return {'policy': settings, 'workspaceArchiveCursor': archives['workspaceArchiveCursor'], 'rows': [{k: v for k, v in row.items() if k not in {'paths', 'stamp'}} for row in rows],
            **{key: generations[key] for key in ('generationCursor', 'retiredCursor') if key in generations},
            **({'runtimeCursor': runtimes['runtimeCursor']} if 'runtimeCursor' in runtimes else {}),
            **({'retentionReasons': generations['retentionReasons']} if generations.get('retentionReasons') else {}),
            **({'referenceFailure': generations['referenceFailure']} if generations.get('referenceFailure') else {}),
            'protected': protected[:128], 'protectedCount': len(protected),
            'observedProtected': observed_protected,
            '_observations': seen,
            'observedUnusedFileBytes': observed_bytes(seen),
            'observedUnusedCount': sum(isinstance(row, dict) for row in seen.values()),
            'observationNote': 'Earlier measurements can be out of date; this is not a complete inventory. Each selected copy is checked again before removal.',
            'protectedFileBytes': protected_measured, 'protectedMeasurementComplete': protected_complete,
            'unusedFileBytes': sum(row['fileBytes'] for row in rows),
            'selectedFileBytes': sum(row['fileBytes'] for row in rows if row['selected']),
            'complete': complete, 'measuredAt': time.time(),
            'measurementNote': 'File content, not guaranteed disk space freed. Shared runtime folders are counted once; hard-linked files are counted once per copy, and shared or copy-on-write blocks can remain. Some protected storage is deliberately left unmeasured.',
            'detail': generations.get('reason') or (None if runner_complete else 'Runner inspection reached its time limit; those copies were kept.')}


async def run(app, identity, *, expected_revision=None, automatic=False, authority=None):
    from . import update_retention, managed_runner_updates, test_dependency_layers, workspace_archive_retention, runtime_environment_retention
    async with lock(app):
        key = 'storage-cleanup-run:' + identity
        previous = retained_run(app, identity, expected_revision, (authority or {}).get('sessionId'))
        if previous is not None:
            return previous  # Re-observe the same attempt; never submit it anew.
        check(app, authority)
        settings = policy(app)
        operation_cursor = (record(app, LATEST, {}) or {}).get('operationOutputCursor')
        if expected_revision is not None and settings['revision'] != expected_revision:
            raise ValueError('Cleanup settings changed. Reload them before cleaning up.')
        if automatic and not settings['enabled']:
            return {'status': 'disabled', 'removedFileBytes': 0}
        report = await preview(app)
        check(app, authority)
        if policy(app) != settings or update_retention.maintenance_blocked(app.update_manager):
            return {'status': 'deferred', 'removedFileBytes': 0, 'detail': 'Settings or update activity changed. Existing files were kept.'}
        now = time.time()
        seen = report.get('_observations')
        if seen is None:
            seen = observations(record(app, OBSERVATION, {}) or {}, report['rows'], now,
                                protected=report.get('observedProtected', []))
        store(app, OBSERVATION, seen)
        result = {'id': identity, 'expectedRevision': expected_revision, 'status': 'running', 'startedAt': now, 'policy': settings,
                  'operationOutputCursor': operation_cursor,
                  **{key: report[key] for key in ('generationCursor', 'retiredCursor', 'runtimeCursor', 'retentionReasons', 'referenceFailure', 'workspaceArchiveCursor') if key in report},
                  'selectedFileBytes': report['selectedFileBytes'], 'removedFileBytes': 0,
                  'effects': [], 'completeInspection': report['complete']}
        if authority is not None:
            result['_authority'] = authority
        store(app, key, result)
        store(app, LATEST, result)
        chosen = {(row['kind'], row['id']): row for row in report['rows'] if row['selected']}
        def evidence(status, item):
            if status == 'reconciled':
                runtime_recovery(app, item)
                result.setdefault('recoveries', []).append({key: item[key] for key in ('kind', 'project', 'retirementId', 'disposition')})
                store(app, key, result)
                store(app, LATEST, result)
                return
            if status == 'attempting':
                try:
                    check(app, authority)
                except ValueError as exc:
                    result.update(status='interrupted', detail=str(exc))
                    raise
                if policy(app) != settings:
                    raise ValueError('Cleanup settings changed before removal; files were kept.')
            effect = next((row for row in result['effects'] if (row['kind'], row['id']) == (item['kind'], item['id'])), None)
            if effect is None:
                effect = {**item, 'fileBytes': item.get('fileBytes', chosen[(item['kind'], item['id'])]['fileBytes'])}
                result['effects'].append(effect)
            effect['status'] = status
            if status == 'completed':
                seen.pop(item['kind'] + ':' + item['id'], None)
                store(app, OBSERVATION, seen)
            result['removedFileBytes'] = sum(row['fileBytes'] for row in result['effects'] if row['status'] == 'completed')
            store(app, key, result)
            store(app, LATEST, result)
        try:
            await update_retention.reclaim(app.update_manager,
                selected={identity for kind, identity in chosen if kind == 'ecosystem'}, evidence=evidence)
            if result['status'] == 'running' and policy(app) == settings:
                await runtime_environment_retention.reclaim(app.update_manager,
                    selected={identity for kind, identity in chosen if kind == runtime_environment_retention.KIND}, evidence=evidence)
            if result['status'] == 'running' and policy(app) == settings:
                await managed_runner_updates.retire(app,
                    selected={identity for kind, identity in chosen if kind == 'runner'}, evidence=evidence)
            if result['status'] == 'running' and policy(app) == settings:
                loop = asyncio.get_running_loop()
                async def dependency_evidence(status, item):
                    evidence(status, item)
                def record_dependency(status, item):
                    # SQLite/policy stay on their owning app loop. The native
                    # remover calls this again at its final deletion boundary.
                    asyncio.run_coroutine_threadsafe(dependency_evidence(status, item), loop).result(timeout=10)
                await asyncio.to_thread(test_dependency_layers.retire, app.data_dir,
                    {identity: row for (kind, identity), row in chosen.items() if kind == 'test-dependencies'}, record_dependency)
            if result['status'] == 'running' and policy(app) == settings:
                loop = asyncio.get_running_loop()
                async def archive_callback(status, item):
                    if result['status'] != 'running':
                        raise ValueError('Storage cleanup stopped before workspace expiry.')
                    if status == 'guard':
                        from .task_environments import require_resolved_removal
                        require_resolved_removal(app, item)
                    else:
                        evidence(status, item)
                def archive_record(status, item):
                    asyncio.run_coroutine_threadsafe(archive_callback(status, item), loop).result(timeout=10)
                await asyncio.to_thread(workspace_archive_retention.retire, app.data_dir, settings,
                    {identity: row for (kind, identity), row in chosen.items() if kind == 'test-workspace'},
                    archive_record, lambda row: archive_record('guard', row))
            if (result['status'] == 'running' and settings['enabled']
                    and settings.get('operationOutputKeepDays') is not None and policy(app) == settings):
                result['operationOutput'] = await app.operations.expire_outputs(settings, after=operation_cursor, authority=authority)
                result['operationOutputCursor'] = result['operationOutput']['nextCursor']
            if result['status'] == 'running':
                result['status'] = 'needs-inspection' if any(row['status'] == 'attempting' for row in result['effects']) else 'completed'
        finally:
            if result['status'] == 'running':
                result['status'] = 'interrupted'
            result['finishedAt'] = time.time()
            store(app, key, result)
            store(app, LATEST, result)
        return result


async def dispatch(app, action, args, origin, command_id, caller_session_id=None):
    if action == 'storage.cleanup.read':
        return {'accepted': True, 'result': read(app)}
    if action == 'storage.cleanup.evidence':
        from .saved_test_storage import read as evidence
        try:
            if args.get('owner', 'tests') == 'smartTools':
                if getattr(app, 'smart_tools', None) is None:
                    raise AppError('Smart Tool storage is not available yet.', 503)
                return {'accepted': True, 'result': app.smart_tools.retention_accounting(args.get('before'))}
            if args.get('owner', 'tests') != 'tests':
                raise ValueError('Choose a supported storage evidence owner.')
            return {'accepted': True, 'result': await asyncio.to_thread(evidence, app, args.get('before'))}
        except ValueError as exc:
            raise AppError(str(exc), 400) from None
    if action == 'storage.cleanup.preview':
        async with lock(app):
            return {'accepted': True, 'result': public(await preview(app))}
    source = None
    if origin not in {'ui', 'user'}:
        if origin not in {'agent', 'system'} or not caller_session_id:
            raise AppError('Only the user or a calling conversation with Full access can change cleanup settings or request cleanup.', 403)
        source = app._session(caller_session_id, hydrate=False)
    identity = command_id or str(uuid.uuid4())
    if action == 'storage.cleanup.run':
        try:
            previous = retained_run(app, identity, args['expectedRevision'], source['id'] if source else None)
            if previous is not None:
                return {'accepted': True, 'result': public(previous)}
            authority = capture(app, source, origin) if source else None
            return {'accepted': True, 'result': public(await run(app, identity,
                expected_revision=args['expectedRevision'], authority=authority))}
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
    digest = fingerprint([action, args] + ([{'sourceSessionId': source['id']}] if source else []))
    saved = saved_command(app, identity, digest)
    if saved:
        return saved
    authority = capture(app, source, origin) if source else None
    async with app.lock:
        saved = saved_command(app, identity, digest)
        if saved:
            return saved
        try:
            check(app, authority)
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
        current = policy(app)
        if args['expectedRevision'] != current['revision']:
            raise AppError('Cleanup settings changed. Reload them before saving.', 409)
        if action == 'storage.cleanup.applyPreset':
            if args.get('preset') not in {'temporary-test-workspaces', 'temporary-operation-output'}:
                raise AppError('Choose a supported cleanup preset.', 400)
            value = {name: current[name] for name in ('keepDays', 'maxUnusedBytes')}
            value['enabled'] = True
            days = 7 if args['preset'] == 'temporary-test-workspaces' else current['workspaceArchiveKeepDays']
            operation_days = 30 if args['preset'] == 'temporary-operation-output' else current['operationOutputKeepDays']
        else:
            value = {name: args[name] for name in ('enabled', 'keepDays', 'maxUnusedBytes')}
            days = args.get('workspaceArchiveKeepDays', current.get('workspaceArchiveKeepDays'))
            operation_days = args.get('operationOutputKeepDays', current.get('operationOutputKeepDays'))
        if (type(value['enabled']) is not bool or type(value['keepDays']) is not int or not 0 <= value['keepDays'] <= 3650
                or type(value['maxUnusedBytes']) is not int or not 0 <= value['maxUnusedBytes'] <= 2**53-1):
            raise AppError('Choose valid cleanup settings.', 400)
        if days is not None and (type(days) is not int or not 1 <= days <= 3650):
            raise AppError('Choose a temporary test workspace retention between 1 and 3650 days, or disable it.', 400)
        value['workspaceArchiveKeepDays'] = days
        if operation_days is not None and (type(operation_days) is not int or not 1 <= operation_days <= 3650):
            raise AppError('Choose operation output retention between 1 and 3650 days, or disable it.', 400)
        value['operationOutputKeepDays'] = operation_days
        value['revision'] = current['revision'] + 1
        receipt = {'accepted': True, 'commandAction': action, 'result': {**read(app), 'policy': value}}
        if authority is not None:
            receipt['_authority'] = authority
        try:
            app.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)', (POLICY, POLICY, json.dumps({'result': value})))
            app.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, digest, json.dumps(receipt)))
            app.db.commit()
        except BaseException:
            app.db.rollback()
            raise
        request_maintenance(app)
        return public(receipt)
