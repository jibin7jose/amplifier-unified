"""Narrow, explicit conversation-owned patch placement; never infer a legacy pin's origin.

Configuration operations own authority, dispatch, receipts and recovery. This
module supplies only the field-specific comparison, preparation and projection.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

from .configuration_authority import fingerprint
from .runtime_controls import PLAN_KEYS, override_path
from .session_identity import native_id

READ = 'configuration.patchPlacement.read'
BIND = 'configuration.patchPlacement.bind'


def definitions(schema, string):
    target = {'sessionId': string(200), 'moduleId': string(200)}
    return {
        READ: ('Read this conversation\'s patch instance and its recorded, saved and observed placement. Qualified Full children may inspect their owner\'s placement; no worker start or full configuration projection.', schema(target)),
        BIND: ('Explicitly pin one patch instance to this root\'s recorded execution folder at the observed revision. Requires runtime_configuration/session authority. Full accepts the exact choice without a human sourceMessageId, including qualified children and trusted system callers; no automatic unpinning or replay.', schema({
            **target, 'expectedRevision': string(200),
            'expectedExecutionRevision': {'type': 'integer', 'minimum': 0},
            'choice': {'enum': ['bind-to-execution']}, 'sourceMessageId': string(200),
            'whenIdle': {'type': 'boolean', 'default': False}},
            [*target, 'expectedRevision', 'expectedExecutionRevision', 'choice'])),
    }


def digest(data):
    return hashlib.sha256(data).hexdigest()


def optional_digest(path):
    return digest(path.read_bytes()) if path.exists() else None


def patch_row(plan, module_id):
    rows = plan.get('tools', [])
    patches = [row for row in rows if row.get('module') == 'tool-apply-patch']
    if any(row.get('id') and row.get('instance_id') and row['id'] != row['instance_id'] for row in rows):
        raise ValueError('Conflicting module instance identifiers cannot be rebound.')
    matches = [row for row in rows if (row.get('id') or row.get('instance_id') or row.get('module')) == module_id]
    # apply_patch is one exported name. Multiple declarations cannot prove which
    # instance supplied it, even when their instance identifiers differ.
    if len(patches) != 1 or len(matches) != 1 or matches[0] is not patches[0]:
        raise ValueError('One unambiguous tool-apply-patch instance is required.')
    row = matches[0]
    if not row.get('enabled', True) or not isinstance(row.get('config', {}), dict):
        raise ValueError('The patch instance is disabled or malformed.')
    if row.get('config', {}).get('engine', 'native') not in {'native', 'function'}:
        raise ValueError('The patch engine is not supported for placement binding.')
    directory = row.get('config', {}).get('working_dir')
    if directory is not None and (not isinstance(directory, str) or len(directory) > 4000):
        raise ValueError('The patch directory setting is not a supported path value.')
    return row


def module_identity(row):
    return fingerprint({**{key: row.get(key) for key in ('id', 'instance_id', 'module', 'source')},
        'engine': row.get('config', {}).get('engine', 'native')})


def saved(path, module_id):
    if not path.exists():
        raise ValueError('No saved mount plan is available for this narrow repair. No runtime was started.')
    raw = path.read_bytes()
    plan = json.loads(raw)
    row = patch_row(plan, module_id)
    return plan, row, {'savedHash': digest(raw),
        'controlHash': optional_digest(path.with_name('control-state.json'))}


def target(service, session):
    execution = service.worktrees.execution_state(session['id'])
    if execution['fenced'] or not execution['hostMatches']:
        raise ValueError('The execution handoff is fenced or belongs to another host.')
    if not Path(execution['directory']).is_dir():
        raise ValueError('The recorded execution directory is unavailable.')
    return {**service.management.configuration_target_binding(session),
        'nativeSessionId': native_id(session), 'executionDirectory': execution['directory']}


def caller(service, args, origin, caller_id, *, active=True):
    session = service._session(args['sessionId'])
    if origin not in {'ui', 'user'}:
        from .collaboration import PRINCIPAL
        if origin != 'system' and not PRINCIPAL.get():
            service.collaboration.error('Patch placement requires its authenticated transport root.')
        service.configuration_authority.source(args, origin, caller_id,
            active=active, allow_descendant=True)
    return session


def retained_receipt(management, args, origin, caller_id, command_id, request_fingerprint):
    """Under service.lock: observe the exact receipt, never admit an effect.

    Ending a generation or revoking its write grant does not erase its receipt.
    The authenticated principal and immutable original request still bind this
    read. New commands continue through normal generation/authority admission.
    """
    service = management.service
    session = caller(service, args, origin, caller_id, active=False)
    previous = service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone()
    if previous is None:
        return None
    if previous[0] != request_fingerprint:
        from .errors import AppError
        raise AppError('This command ID was already used with different contents.', 409)
    receipt = json.loads(previous[1])
    result = receipt['result']
    record = management.configuration_record(session['id'], result['operationId'])
    request = record['request']
    if (result['sessionId'] != session['id'] or result['commandId'] != command_id
            or request['commandId'] != command_id or request['source'].get('action') != BIND
            or request['source']['origin'] != origin
            or request['config']['moduleId'] != args['moduleId']
            or origin not in {'ui', 'user'} and request['source']['callerSessionId'] != caller_id):
        raise ValueError('The retained binding receipt does not match its original root and action.')
    return {**receipt, 'duplicate': True}


def choice(session, args, origin, authority=None):
    if args.get('choice') != 'bind-to-execution':
        raise ValueError('An explicit bind-to-execution choice is required.')
    if origin not in {'ui', 'user'} and not (authority and 'accessRevision' in authority):
        message = next((row for row in session['messages']
            if row.get('id') == args.get('sourceMessageId')), None)
        if (not message or message.get('role') != 'user'
                or message.get('inputOrigin') not in {'ui', 'user', 'voice'}
                or any(message.get(key) for key in ('questionId', 'scheduledRunId', 'observationId'))):
            raise ValueError('Cite the actual human choice in this root, not assistant or peer evidence.')


def engine_observation(controls, module_id):
    """Inspect actual objects only; no discovery, import installation or remount."""
    from .file_source_guard import GuardedFileTool
    from amplifier_module_tool_apply_patch.tool import ApplyPatchTool
    from amplifier_module_tool_apply_patch.engines.native import NativeEngine
    from amplifier_module_tool_apply_patch.engines.function import FunctionEngine

    row = patch_row(controls.coordinator.config, module_id)
    tool = (controls.coordinator.get('tools') or {}).get('apply_patch')
    mounted = tool
    inspect = controls.coordinator.get_capability('web.work_inspect_tool')
    if callable(inspect):
        tool = inspect('apply_patch', mounted)
    guard = controls.coordinator.get_capability('web.file_source_guard')
    guarded = (isinstance(tool, GuardedFileTool) and tool.kind == 'patch'
        and tool.guard is guard and guard is not None and guard.coordinator is controls.coordinator)
    actual = tool.tool if guarded else tool
    if type(actual) is not ApplyPatchTool:
        raise ValueError('The mounted patch implementation cannot be verified.')
    engine = actual._engine
    kind = row.get('config', {}).get('engine', 'native')
    if type(engine) is not {'native': NativeEngine, 'function': FunctionEngine}[kind]:
        raise ValueError('The declared and mounted patch engines differ.')
    return {'status': 'observed', 'workingDirectory': str(engine._resolve_path('.').resolve()),
        'engine': kind, 'guarded': guarded, 'objectId': f'{os.getpid()}:{id(mounted)}:{id(engine)}',
        'identityHash': module_identity(row),
        'moduleHash': fingerprint(row),
        'liveHash': fingerprint({key: value for key, value in controls.coordinator.config.items() if key in PLAN_KEYS})}


async def observe(service, session, module_id):
    runtime = service.runtime
    row = getattr(runtime, 'workers', {}).get(session['id'])
    empty = {'status': 'unobserved', 'workingDirectory': None}
    if (not row or row['process'].returncode is not None or not row['ready'].done()
            or row['ready'].cancelled() or row['ready'].exception() is not None):
        return empty
    try:
        async with asyncio.timeout(10):
            # Non-control op: RuntimeManager never reactivates a retired worker,
            # and Worker never acquires a writer or unparks for this observation.
            value = await runtime._request(session['id'], 'dependencies', patchPlacement={'moduleId': module_id})
        if runtime.workers.get(session['id']) is not row or row['process'].returncode is not None:
            raise ValueError('Worker changed during placement observation.')
        return {**value, 'workerInstance': f"{row['process'].pid}:{row['started_at']}"}
    except (RuntimeError, ValueError, TimeoutError):
        return {'status': 'unknown', 'workingDirectory': None}


def host_saved(service, session, module_id):
    # Validate the host-owned native alias before constructing its private path.
    path = service.data_dir / 'sessions' / override_path(native_id(session)).parent.name / 'configuration.json'
    return saved(path, module_id)


async def read(management, args, origin, caller_id):
    service = management.service
    async with service.lock:
        session = caller(service, args, origin, caller_id, active=False)
        bound = target(service, session)
        plan, row, stamp = host_saved(service, session, args['moduleId'])
    mounted = await observe(service, session, args['moduleId'])
    async with service.lock:
        session = caller(service, args, origin, caller_id, active=False)
        if target(service, session) != bound or host_saved(service, session, args['moduleId'])[2] != stamp:
            raise ValueError('Placement changed during observation; read it again.')
        revision = str(uuid.uuid4())
        snapshots = management.placement_snapshots
        snapshots[revision] = {**stamp, 'target': bound, 'moduleId': args['moduleId'], 'mounted': mounted}
        while len(snapshots) > 64:
            snapshots.pop(next(iter(snapshots)))
        provenance = 'unknown'
        pending = session.get('pendingConfiguration', {})
        if pending.get('operationId'):
            try:
                from .configuration_recovery import evidence, latest_attempt
                record = management.configuration_record(session['id'], pending['operationId'])
                payload = record['request']['config']
                attempt = latest_attempt(record)
                sealed = evidence(attempt, home=service.data_dir) if attempt else None
                if (record['request']['source'].get('action') == BIND and sealed
                        and sealed['effect'] == 'saved_not_mounted' and sealed.get('terminal')
                        and payload['moduleId'] == args['moduleId']
                        and fingerprint(plan) == payload['resultPlanHash']):
                    provenance = 'explicit'
            except (ValueError, OSError, KeyError, TypeError):
                pass  # Missing provenance is not evidence of an injected default.
        return {'sessionId': session['id'], 'revision': revision,
            'executionDirectory': bound['executionDirectory'], 'executionRevision': bound['executionRevision'],
            'module': {'id': args['moduleId'], 'module': row['module'],
                'engine': row.get('config', {}).get('engine', 'native')},
            'saved': {'workingDirectory': row.get('config', {}).get('working_dir'), 'origin': provenance},
            'mounted': {key: mounted.get(key) for key in ('status', 'workingDirectory')}}


def admit(management, args, origin, caller_id, command_id, authority):
    """Under service.lock, after command dedup, before any asynchronous effect."""
    service = management.service
    session = caller(service, args, origin, caller_id)
    choice(session, args, origin, authority)
    bound = target(service, session)
    snapshot = management.placement_snapshots.get(args['expectedRevision'])
    plan, row, stamp = host_saved(service, session, args['moduleId'])
    if (not snapshot or snapshot['target'] != bound or snapshot['moduleId'] != args['moduleId']
            or args['expectedExecutionRevision'] != bound['executionRevision']
            or any(snapshot[key] != stamp[key] for key in stamp)):
        raise ValueError('The observed placement/configuration revision changed; no binding was applied.')
    if (not management.configuration_idle(session) and not args.get('whenIdle')
            or management.unresolved_configuration(session) or session.get('configurationBusy')):
        raise ValueError('Configuration is busy or unresolved; finish it before binding placement.')
    if snapshot['mounted']['status'] == 'unknown':
        raise ValueError('Mounted placement is unknown; no binding was applied.')
    result_plan = copy.deepcopy(plan)
    patch_row(result_plan, args['moduleId']).setdefault('config', {})['working_dir'] = bound['executionDirectory']
    payload = {**stamp, 'moduleId': args['moduleId'], 'moduleHash': fingerprint(row),
        'identityHash': module_identity(row),
        'directory': bound['executionDirectory'], 'resultPlanHash': fingerprint(result_plan),
        'mounted': snapshot['mounted']}
    source = {'origin': origin, 'callerSessionId': caller_id, 'action': BIND,
        'sourceMessageId': args.get('sourceMessageId'), 'choice': args['choice'],
        'generation': copy.deepcopy(session.get('collaborationGeneration')),
        'target': management.configuration_target_binding(session)}
    record = management.create_configuration_operation(session, payload, command_id, authority, source)
    management.schedule_configuration(session['id'])
    from .configuration_recovery import public
    return public(record)


def check_request(management, session, record):
    bound = target(management.service, session)
    if bound['executionDirectory'] != record['request']['config']['directory']:
        raise ValueError('The recorded execution placement changed.')


def prepare(controls, payload):
    """Runs synchronously inside the existing native effect admission fence."""
    plan, row, stamp = saved(override_path(controls.session.session_id), payload['moduleId'])
    if any(stamp[key] != payload[key] for key in stamp) or fingerprint(row) != payload['moduleHash']:
        raise ValueError('Saved patch configuration changed before binding.')
    observed = engine_observation(controls, payload['moduleId'])
    expected = payload['mounted']
    if (observed['identityHash'] != payload['identityHash']
            or expected['status'] == 'observed'
            and any(observed[key] != expected[key] for key in observed)):
        raise ValueError('Mounted patch configuration changed before binding.')
    if not observed['guarded']:
        raise ValueError('The mounted patch instance has no source freshness guard.')
    actual_directory = controls.coordinator.get_capability('session.working_dir')
    if str(Path(actual_directory).resolve()) != str(Path(payload['directory']).resolve()):
        raise ValueError('The native execution directory differs from the recorded target.')
    row.setdefault('config', {})['working_dir'] = payload['directory']
    if fingerprint(plan) != payload['resultPlanHash']:
        raise ValueError('The field-only configuration result differs from the retained request.')
    return plan


def verify_readback(management, session, payload, observed):
    plan, row, stamp = host_saved(management.service, session, payload['moduleId'])
    if (fingerprint(plan) != payload['resultPlanHash'] or stamp['controlHash'] != payload['controlHash']
            or observed.get('status') != 'observed' or not observed.get('guarded')
            or observed.get('identityHash') != module_identity(row)
            or observed.get('workingDirectory') != str(Path(payload['directory']).resolve())):
        raise ValueError('Saved patch placement has no matching actual engine readback; recovery remains held.')
