"""Operation-bound configuration recovery; never infer no effect from absence.

The native receipt is private, authenticated by a per-attempt host secret, and
written independently of the transport reply. This is not a permission system:
every admission still checks the original authority and target at the host.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from .configuration_authority import fingerprint
from .host.config import write_private
from .runtime_controls import override_path, public_config


def definitions(schema, string):
    properties = {"sessionId": string(200), "operationId": string(200)}
    return {
        "configuration.operation.read": (
            "Observe an explicitly targeted retained configuration operation without starting a runtime. Current Full root operators may target another root in the same workspace. commandId reads only an exact recovery command's redacted admission; missing evidence is unknown, not permission to retry. No payload, grant or secret is returned.",
            schema({**properties, "includeRecovery": {"type": "boolean"},
                    "commandId": string(200)}, ["sessionId"])),
        **{f"configuration.operation.{name}": (
            description, schema({**properties, "expectedRevision": {"type": "integer", "minimum": 1}}))
            for name, description in {
                "reconcile": "Reconcile the exact operation with its sealed native receipt at an expected revision. Never applies or warms; absent/partial evidence stays unknown.",
                "resume": "Continue the original operation under its still-valid authority and target. Proven no effect may apply; a confirmed saved plan may only finish loaded readback or mount if its worker is absent. Exclusive idle admission; unknown or partial writes refuse. No new plan, grant or input.",
            }.items()},
    }


def caller_authority(management, args, origin, caller, *, active=False):
    """Keep grant administration owner-only; recovery has a narrow target route."""
    service = management.service
    if origin in {'ui', 'user'} or args['sessionId'] == caller:
        if origin not in {'ui', 'user'}:
            service.configuration_authority.source(args, origin, caller,
                active=active, allow_descendant=True)
        return None
    from .collaboration import BINDING
    if origin != 'agent' or not caller or not args.get('operationId') or '_childOrigin' in (BINDING.get() or {}):
        raise ValueError('Cross-conversation recovery requires an explicit operation and an authenticated Full root caller.')
    source = service.configuration_authority.source({'sessionId': caller}, origin, caller, active=True)
    target = service.collaboration.authorize(source, args, hydrate=False)
    authority = service.access_mode.capture_full(source, origin)
    if authority is None or authority.get('inputCoverage') != 'complete':
        raise ValueError('Cross-conversation recovery requires current Full access and authenticated assigned inputs.')
    from .task_binding import continuation_epoch
    task = source.get('task') or service.coordination.task(source['id'])
    authority.update(workspace=source['workspace'], effect='runtime_configuration', scope='session',
        interruptionRevision=source.get('interruptionRevision', 0),
        recoveryCaller={**management.configuration_target_binding(source),
            'nativeSessionId': source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id'],
            'configurationPendingRevision': source.get('configurationPendingRevision', 0),
            'taskId': task.get('id'), 'taskContinuationEpoch': continuation_epoch(task),
            'assignmentId': source.get('assignmentId')},
        recoveryTarget={**management.configuration_target_binding(target),
            'nativeSessionId': target.get('runtimeSessionId') or target.get('nativeIdentity') or target['id'],
            'operationId': args['operationId']})
    check_caller(service, authority)
    peers = service.configuration_authority.full_peer_authorities(source, authority['inputIds'])
    # Target identity is never used as the caller, even for an operator original.
    return {'chain': [authority, *peers]}


def check_caller(service, authority):
    """Accepted recovery may outlive a turn, not its caller's execution controls."""
    saved = authority['recoveryCaller']
    source = service._session(saved['sessionId'], hydrate=False)
    task = source.get('task') or service.coordination.task(source['id'])
    from .task_binding import continuation_epoch
    from .updates import work_paused
    if (service.management.configuration_target_binding(source) != {
            key: saved[key] for key in ('sessionId', 'workspace', 'workingDirectory',
                                      'executionRevision', 'interruptionRevision')}
            or (source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']) != saved['nativeSessionId']
            or source.get('configurationPendingRevision', 0) != saved['configurationPendingRevision']
            or source.get('status') in {'stopped', 'stopping', 'interrupted'}
            or source.get('configurationBusy') or source.get('fileAccessPending')
            or service.management.unresolved_configuration(source)
            or source.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}
            or work_paused(service.state) or service.assignments.blocked(source)
            or task.get('id') != saved['taskId']
            or continuation_epoch(task) != saved['taskContinuationEpoch']
            or source.get('assignmentId') != saved['assignmentId']
            or task.get('status') in {'paused', 'blocked', 'completed'}):
        raise ValueError('The recovery caller was stopped, moved, fenced or its task authority changed.')
    target_saved = authority['recoveryTarget']
    target = service._session(target_saved['sessionId'], hydrate=False)
    if (service.management.configuration_target_binding(target) != {
            key: target_saved[key] for key in ('sessionId', 'workspace', 'workingDirectory',
                                             'executionRevision', 'interruptionRevision')}
            or (target.get('runtimeSessionId') or target.get('nativeIdentity') or target['id']) != target_saved['nativeSessionId']
            or target.get('pendingConfiguration', {}).get('operationId') != target_saved['operationId']
            or target.get('configurationOperationFence') not in {None, target_saved['operationId']}
            or target.get('status') in {'stopped', 'stopping', 'interrupted'}
            or target.get('fileAccessPending') or service.assignments.blocked(target)
            or target.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}):
        raise ValueError('The recovery target was stopped, moved or fenced by a different owner.')


def command_status(service, record, identity):
    """One exact ledger projection, never an arbitrary receipt/plan read."""
    result = {'commandId': identity, 'status': 'unconfirmed', 'accepted': None,
              'reason': 'No target-bound recovery receipt is available. Do not infer no effect or retry.'}
    row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    receipt = json.loads(row[0]) if row else {}
    binding = receipt.get('configurationRecovery') or {}
    if (binding.get('sessionId') != record['request']['sessionId']
            or binding.get('operationId') != record['id']
            or binding.get('action') not in {'configuration.operation.reconcile', 'configuration.operation.resume'}):
        return result
    return {**result, 'status': 'accepted' if receipt.get('accepted') is True else 'rejected',
        'accepted': receipt.get('accepted') is True,
        **{key: binding[key] for key in ('action', 'sessionId', 'operationId', 'expectedRevision')},
        'operationRevision': (receipt.get('result') or {}).get('revision'),
        'phase': (receipt.get('result') or {}).get('state', {}).get('phase'),
        'errorCategory': binding.get('errorCategory'),
        'reason': 'Retained command admission only; current operation and native evidence remain authoritative.'}


def suppress_input(session, receipt=None):
    """Hold old queued inputs, not new explicit work after operator recovery."""
    marker = session.get('pendingConfiguration', {}).get('recoveryNoInput')
    return (isinstance(marker, dict)
        and marker.get('generationId') == (session.get('collaborationGeneration') or {}).get('id')
        and (receipt is None or receipt.get('dependencyRequestId')
             or receipt.get('acceptedAt', 0) <= marker.get('acceptedAt', 0)))


def recovery_child(service, session, request=None):
    """Route an exact live Full child call to this operation's recovery owner.

    An unresolved outcome blocks ordinary execution, not observation or explicit
    recovery of that same settled operation. This grants no effect: command()
    still verifies the retained request, authority, revision and sealed evidence.
    The request is the actual tool body, never an argument claiming a bypass.
    """
    if request is None:
        from .workspace_work_effects import APP_EFFECT
        request = APP_EFFECT.get()
        if not request or request.get('sessionId') != session['id']:
            return None
    if request.get('toolName') != 'app_control':
        return None
    tool = request.get('toolInput')
    if not isinstance(tool, dict) or tool.get('operation') not in {'dispatch', 'action.dispatch'}:
        return None
    dispatch = tool.get('args')
    if not isinstance(dispatch, dict) or dispatch.get('action') not in {
            'configuration.operation.read', 'configuration.operation.reconcile', 'configuration.operation.resume'}:
        return None
    args = dispatch.get('args')
    if not isinstance(args, dict) or args.get('sessionId') != session['id']:
        return None
    pending = session.get('pendingConfiguration') or {}
    operation = args.get('operationId')
    if operation is None and dispatch['action'] == 'configuration.operation.read':
        operation = pending.get('operationId')
    if (not operation or operation != pending.get('operationId')
            or pending.get('phase') != 'error' and not (
                pending.get('phase') == 'applying' and pending.get('stage') == 'interrupted')
            or session.get('configurationOperationFence') not in {None, operation}
            or session.get('configurationBusy') and session.get('configurationOperationFence') != operation
            or not service.access_mode.full(session)):
        return None
    task = service.management.pending_config_tasks.get(session['id'])
    if task is not None and not task.done() or service.management.lock.locked():
        return None
    from .child_execution import live_assignment
    return live_assignment(service, session)


def binding(record):
    """Allocate privately; neither this key nor the envelope enters public state."""
    return {
        "version": 1, "operationId": record["id"], "attemptId": str(uuid.uuid4()),
        "sessionId": record["request"]["sessionId"], "requestHash": record["requestHash"],
        "payloadHash": record["request"]["payloadHash"],
        **({"action": record["request"]["source"]["action"]} if record["request"]["source"].get("action") else {}),
        "number": len(record.get("attempts", [])) + 1, "key": secrets.token_hex(32),
    }


def receipt_path(attempt, *, home=None):
    for key in ("operationId", "attemptId"):
        if str(uuid.UUID(attempt[key])) != attempt[key]:
            raise ValueError("Invalid configuration effect identity")
    if attempt["version"] != 1 or len(bytes.fromhex(attempt["key"])) != 32:
        raise ValueError("Invalid configuration effect binding")
    base = override_path(attempt["sessionId"]).parent
    if home is not None:
        # Host reads use the owning service's home, not process-global settings.
        base = Path(home) / "sessions" / attempt["sessionId"]
    return base / "configuration-effects" / (
        attempt["operationId"] + "-" + attempt["attemptId"] + ".json")


def seal(attempt, effect, **details):
    body = {key: value for key, value in attempt.items() if key != "key"}
    body.update(effect=effect, **details)
    return {"body": body, "signature": hmac.new(bytes.fromhex(attempt["key"]),
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()}


def evidence(attempt, *, home=None):
    """Read only. Missing, corrupt, foreign or forged receipts are NOT no-effect."""
    path = receipt_path(attempt, home=home)
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if not isinstance(record, dict) or not isinstance(record.get("body"), dict):
        raise ValueError("Invalid native configuration receipt.")
    body = record["body"]
    expected = {key: value for key, value in attempt.items() if key != "key"}
    if any(body.get(key) != value for key, value in expected.items()):
        raise ValueError("The native configuration receipt has a different binding.")
    signed = seal(attempt, body["effect"], **{key: value for key, value in body.items()
        if key not in expected and key != "effect"})
    if not hmac.compare_digest(signed["signature"], record["signature"]):
        raise ValueError("The native configuration receipt could not be authenticated.")
    if body["effect"] not in {"none", "unknown", "saved_not_mounted"}:
        raise ValueError("Unrecognized native configuration effect.")
    return body


async def native_apply(controls, config, attempt, authorize, stop_epoch, *, operation="configuration.apply"):
    """Called under the worker command lock, with no await after final admission.

    An exclusive claim prevents duplicate transport delivery (including after
    process restart) from entering the settings writer again. A crash during the
    claim/receipt writes is deliberately unknown, never a reusable no-effect.
    """
    if (operation not in {"configuration.apply", "configuration.patchPlacement.bind"}
            or attempt.get("action", "configuration.apply") != operation
            or attempt["nativeSessionId"] != controls.session.session_id
            or attempt.get("workerPid") not in {None, os.getpid()}
            or fingerprint(config) != attempt["payloadHash"]):
        raise ValueError("The configuration attempt does not match its native target or payload.")
    path = receipt_path(attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("This native configuration attempt already has retained evidence.")
    claim = path.with_suffix(".claim")
    fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)  # Existence is an exclusion fence, never proof of an effect.
    write_private(path, json.dumps(seal(attempt, "unknown")))
    epoch = stop_epoch()
    try:
        allowed = await authorize({key: value for key, value in attempt.items() if key != "key"})
        if not allowed.get("admitted") or epoch != stop_epoch():
            raise ValueError("Original configuration authority or Stop boundary changed.")
        controls.require_idle()
        placement = operation == "configuration.patchPlacement.bind"
        plan = controls.prepare_patch_placement(config) if placement else controls.prepare_configuration(config)
    except BaseException:
        write_private(path, json.dumps(seal(attempt, "none", terminal=True)))
        raise
    # No suspension from authority/idle checks to the two synchronous writes.
    # A failure in either write leaves the durable unknown receipt unchanged.
    if placement:
        controls.save_patch_placement(plan)
    else:
        controls.save_configuration(plan)
    write_private(path, json.dumps(seal(attempt, "saved_not_mounted", terminal=True)))
    return {"requiresRestart": True} if placement else {"requiresRestart": True, "configuration": public_config(plan)}


def prepare_attempt(management, session, operation):
    record = management.configuration_record(session["id"], operation)
    if record["state"]["effect"] != "none" or record["state"]["stage"] != "preparing":
        raise ValueError("Only a proven unperformed configuration can enter native dispatch.")
    attempt = binding(record)
    row = getattr(management.service.runtime, "workers", {}).get(session["id"], {})
    worker_pid = row.get("native_worker_pid")
    if row.get("process") is not None and (type(worker_pid) is not int or worker_pid <= 0):
        # Refuse before claiming an attempt or marking its effect unknown. The
        # launcher's PID cannot stand in for an unconfirmed native worker.
        raise ValueError("The conversation worker identity was not confirmed. No configuration change was dispatched.")
    attempt.update(nativeSessionId=row.get("runtime_id", session["id"]),
        workerPid=worker_pid)
    record.setdefault("attempts", []).append(attempt)
    write_private(management.configuration_record_path(session["id"], operation), json.dumps(record))
    management.configuration_transition(session, operation, stage="dispatching",
        effect="unknown", attemptId=attempt["attemptId"])
    return attempt


def latest_attempt(record):
    attempts = record.get("attempts", [])
    if not attempts:
        return None
    attempt = attempts[-1]
    if (attempt.get("operationId") != record["id"] or attempt.get("requestHash") != record["requestHash"]
            or attempt.get("action", "configuration.apply") != record["request"]["source"].get("action", "configuration.apply")
            or attempt.get("payloadHash") != record["request"]["payloadHash"]
            or attempt.get("sessionId") != record["request"]["sessionId"]
            or attempt.get("number") != len(attempts)
            or attempt.get("attemptId") != record["state"].get("attemptId")):
        raise ValueError("The native attempt is not bound to the original configuration operation.")
    receipt_path(attempt)  # Validate safe file and seal identities before reading.
    return attempt


def authorize(management, sid, args):
    """Host-only worker bridge; public control arguments cannot authorize this."""
    session = management.service._session(sid)
    record = management.configuration_record(sid, args.get("operationId"))
    attempt = latest_attempt(record)
    if (not attempt or args != {key: value for key, value in attempt.items() if key != "key"}
            or record["state"]["stage"] != "dispatching"
            or session.get("pendingConfiguration", {}).get("operationId") != record["id"]
            or session.get("configurationOperationFence") != record["id"]
            or not session.get("configurationBusy")):
        raise ValueError("The configuration attempt is no longer admitted.")
    management.check_configuration_operation(session, record)
    return {"admitted": True}


def public(record, observed=None):
    fields = {"phase", "stage", "effect", "attemptId", "revision", "at"}
    return {
        "operationId": record["id"], "commandId": record["request"]["commandId"],
        "sessionId": record["request"]["sessionId"], "revision": record["revision"],
        "state": {key: value for key, value in record["state"].items() if key in fields},
        "history": [{key: value for key, value in row.items() if key in fields} for row in record["history"][-32:]],
        **({"historyTruncated": True} if len(record["history"]) > 32 else {}),
        "nativeEffect": observed["effect"] if observed else "unconfirmed",
        "attempts": len(record.get("attempts", [])), "workReplayed": False,
    }


def check_saved(management, session, record, attempt=None, observed=None):
    """Prove the original write completed and its saved plan is unchanged."""
    management.check_configuration_operation(session, record)
    attempt = latest_attempt(record) if attempt is None else attempt
    observed = evidence(attempt, home=management.service.data_dir) if attempt and observed is None else observed
    if (record['request']['source'].get('action', 'configuration.apply') != 'configuration.apply'
            or record['state']['effect'] != 'saved_not_mounted'
            or not attempt or not observed or observed['effect'] != 'saved_not_mounted'
            or observed.get('terminal') is not True):
        raise ValueError('Continuation requires the original terminal native save receipt; unknown or partial work stays held.')
    native = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
    if attempt.get('nativeSessionId') != native:
        raise ValueError('The saved configuration native target changed.')
    from .session_files import validate_id
    validate_id(native)
    path = management.service.data_dir / 'sessions' / native / 'configuration.json'
    saved = json.loads(path.read_text())
    if not management.configuration_readback_matches(record['request']['config'], saved):
        raise ValueError('The saved configuration changed after application; its operation remains unresolved.')


async def finish_saved(management, identity, operation):
    """Continue readback/mount only, never dispatch the original settings write."""
    from .configuration_authority import CURRENT
    from .configuration_readback import inspect_loaded
    service = management.service

    def guard(*, workspace=None, scope=None):
        current = service._session(identity)
        record = management.configuration_record(identity, operation)
        check_saved(management, current, record)
        if workspace is not None or scope is not None:
            for authority in [record['request']['authority'], *record.get('resumptions', [])]:
                service.configuration_authority.check(authority, workspace=workspace, scope=scope)
        if (current.get('pendingConfiguration', {}).get('operationId') != operation
                or current.get('configurationOperationFence') != operation
                or not current.get('configurationBusy') or record['state']['phase'] != 'applying'
                or record['state']['stage'] != 'reading'):
            raise ValueError('The saved configuration continuation is no longer admitted.')

    # Keep the same management serialization and retained authority ceilings as
    # ordinary application. Queue acceptance itself admits no runtime effect.
    async with management.lock:
        token = CURRENT.set(guard)
        try:
            guard()
            session = management.session({'sessionId': identity})
            runtime = service.runtime
            row = getattr(runtime, 'workers', {}).get(identity)
            if row is None or row.get('process') is not None and row['process'].returncode is not None:
                # A present worker is only observed. A different loaded plan
                # cannot authorize retiring it or silently replacing its code.
                await management.ensure_runtime(session, restart_guard=guard,
                    saved_configuration_operation=operation)
            guard()
            expected = management.configuration_record(identity, operation)['request']['config']
            inspected, readback_guard = await inspect_loaded(management, session, expected, guard)
            async with service.lock:
                readback_guard()
                current = service._session(identity)
                current['configuration'] = inspected
                controls = service.state.setdefault('runtimeControl', {}).setdefault(identity, {})
                controls['configuration.inspect'] = inspected
                controls['configuration.apply'] = {'configuration': inspected, 'requiresRestart': False}
                management.configuration_transition(current, operation, phase='ready', stage='readback',
                    effect='mounted', error=None, recoveryRequired=False,
                    detail='Changes are applied to the loaded session.', appliedAt=time.time())
                management.queued_path(identity).unlink(missing_ok=True)
                management.settle_configuration_fence(current, operation)
                from .configuration_continuation import publish_outcome
                publish_outcome(management, current, operation)
                service._publish_changes(sessions={identity})
        finally:
            CURRENT.reset(token)


def validate_recovery(management, session, record, attempt, observed, action, args, origin, operator=None):
    """One validation path for mutations and observational UI availability."""
    service = management.service
    pending = session.get("pendingConfiguration", {})
    if pending.get("operationId") != record["id"] or args["expectedRevision"] != record["revision"]:
        raise ValueError("The original configuration operation revision changed.")
    interrupted = (action.endswith(".reconcile")
        and record["state"]["phase"] == "applying"
        and record["state"]["stage"] == "interrupted")
    if record["state"]["phase"] != "error" and not interrupted:
        raise ValueError("Only a settled failed configuration operation can be reconciled or resumed.")
    task = management.pending_config_tasks.get(session["id"])
    if task is not None and not task.done() or management.lock.locked():
        raise ValueError("The original configuration task has not settled.")
    if (session.get('configurationOperationFence') not in {None, record['id']}
            or session.get('configurationBusy') and session.get('configurationOperationFence') != record['id']):
        raise ValueError('Another configuration operation owns this conversation.')
    if operator is not None:
        service.configuration_authority.check(operator)
        if (not management.configuration_idle({**session, 'pendingConfiguration': {}, 'configurationBusy': False})
                or session.get('status') in {'stopped', 'stopping', 'interrupted'}
                or service.assignments.blocked(session)):
            raise ValueError('Targeted operator recovery requires the original target to be exclusively idle.')
        if session.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
            raise ValueError('The original target does not own execution here.')
        if attempt and attempt.get('nativeSessionId') != (
                session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']):
            raise ValueError('The original native configuration target changed.')
    management.check_configuration_operation(session, record)
    recovery_authority = operator
    if origin not in {"ui", "user"} and operator is None:
        authority = record["request"]["authority"]
        if record["request"]["authorization"] != "grant":
            if not service.access_mode.full(session):
                raise ValueError("Full access is required to continue an operator-started configuration operation.")
            # The original UI/internal request stays immutable. Existing
            # resumptions retain current Full as an additional effect boundary;
            # neither an old revoked grant nor a stopped request is renewed.
            recovery_authority = service.configuration_authority.capture(
                "configuration.apply", {"sessionId": session["id"]}, origin, session["id"])
        # Original Full was checked above; only narrow grants need an
        # additional peer-lineage ceiling for this recovery.
        elif "accessRevision" not in authority:
            grant = service.configuration_authority.record(authority["grantId"])
            peers = service.configuration_authority.peer_authorities(
                session, grant, authority["effect"], authority["scope"])
            recovery_authority = {**authority, "peerGrants": authority.get("peerGrants", []) + peers}
    effect = record["state"]["effect"]
    if action.endswith(".reconcile"):
        if attempt:
            if not observed:
                raise ValueError("Native evidence is absent; the operation remains unknown.")
            if observed["effect"] == "none" and observed.get("terminal") is not True:
                raise ValueError("Native no-effect evidence is not terminal; the operation remains held.")
            # Never downgrade acknowledged save/mount to an older no-effect receipt.
            if effect in {"saved_not_mounted", "mounted"} and observed["effect"] != effect:
                raise ValueError("Native evidence conflicts with retained saved-state evidence.")
            effect = observed["effect"]
        elif effect != "none":
            raise ValueError("Legacy operation lacks native binding; its outcome remains unknown.")
    elif action.endswith(".resume"):
        if effect == 'saved_not_mounted':
            check_saved(management, session, record, attempt, observed)
        elif (record["state"]["effect"] != "none"
                or attempt and (record["state"]["stage"] != "reconciled"
                    or not observed or observed["effect"] != "none" or not observed.get("terminal"))):
            raise ValueError("Resume requires proven pre-write no-effect evidence; unknown or saved work is not repeated.")
    else:
        raise ValueError("Unknown configuration recovery action")
    return effect, recovery_authority


def recovery_status(management, session, record, attempt, observed, origin, operator=None):
    from .errors import AppError
    status = {"canReconcile": False, "canResume": False,
              "reason": "This change remains held. Its original permission, target or evidence is not currently valid."}
    for verb, key in (("reconcile", "canReconcile"), ("resume", "canResume")):
        try:
            if origin not in {"ui", "user"} and operator is None:
                management.service.configuration_authority.source(
                    {"sessionId": session["id"]}, origin, session["id"], active=True, allow_descendant=True)
            validate_recovery(management, session, record, attempt, observed,
                "configuration.operation." + verb, {"expectedRevision": record["revision"]}, origin, operator)
            status[key] = True
        except (ValueError, OSError, KeyError, TypeError, AttributeError, AppError):
            pass  # Never publish raw errors, private paths, plan or authority.
    if status["canResume"]:
        status["reason"] = ("Settings were saved. Resume checks the loaded session, or loads the saved plan if its worker is absent; it never repeats the settings write."
            if record['state']['effect'] == 'saved_not_mounted' else
            "No settings write occurred. You can explicitly resume the original change; its permission will be checked again.")
    elif status["canReconcile"]:
        status["reason"] = "Check the saved outcome before deciding what can happen next. Checking never applies changes."
    elif record["state"]["phase"] in {"queued", "applying"} and record["state"].get("stage") != "interrupted":
        status["reason"] = "The original change is queued or still applying. Acceptance is not confirmation that it is mounted."
        if record['request']['source'].get('continuation'):
            from .configuration_continuation import intent_reason
            if not intent_reason(management.service, record):
                status['reason'] = 'Waiting for the settings change; this task will continue automatically after verification.'
    elif record["state"]["phase"] == "ready" and record["state"]["effect"] == "mounted":
        status["reason"] = "Changes are applied to the loaded session."
    return status


def command(management, action, args, origin, caller, *, operator=None):
    """Run under service.lock; use the existing shared command ledger for dedup."""
    service = management.service
    if operator is None:
        operator = caller_authority(management, args, origin, caller, active=not action.endswith(".read"))
    else:
        service.configuration_authority.source({'sessionId': caller}, origin, caller, active=True)
        service.configuration_authority.check(operator)
    if args.get('commandId') and not args.get('operationId'):
        raise ValueError('An exact command read requires an explicit operation.')
    session = service._session(args["sessionId"])
    pending = session.get("pendingConfiguration", {})
    operation = args.get("operationId") or pending.get("operationId")
    include_recovery = action.endswith(".read") and args.get("includeRecovery")
    try:
        record = management.configuration_record(session["id"], operation)
        attempt = latest_attempt(record)
        observed = evidence(attempt, home=service.data_dir) if attempt else None
        if include_recovery:
            result = public(record, observed)
            result["recovery"] = recovery_status(management, session, record, attempt, observed, origin, operator)
            if args.get('commandId'):
                result['command'] = command_status(service, record, args['commandId'])
            return result
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        if include_recovery:
            return {"sessionId": session["id"], "operationId": operation,
                    "recovery": {"canReconcile": False, "canResume": False,
                        "reason": "The original operation or its evidence cannot be verified. Changes remain held; nothing was retried."}}
        raise
    if action.endswith(".read"):
        result = public(record, observed)
        if args.get('commandId'):
            if not args.get('operationId'):
                raise ValueError('An exact command read requires an explicit operation.')
            result['command'] = command_status(service, record, args['commandId'])
        return result
    effect, recovery_authority = validate_recovery(
        management, session, record, attempt, observed, action, args, origin, operator)
    from .collaboration import BINDING, PRINCIPAL
    native_binding = (BINDING.get() or {}) if origin not in {"ui", "user"} else {}
    input_ids = [row["inputId"] for row in native_binding.get("_inputBindings", []) if row.get("inputId")]
    # Attribute this explicit recovery in private transition history. The
    # original request and its authority remain immutable, including its actor.
    actor = service._session(caller, hydrate=False) if operator is not None else session
    recovery_source = {"action": action, "origin": origin, "sessionId": actor["id"],
        "runtimeSessionId": PRINCIPAL.get() if origin not in {"ui", "user"} else None,
        "generationId": native_binding.get("_generationId"),
        "interruptionRevision": actor.get("interruptionRevision", 0),
        "inputIds": input_ids, "inputCoverage": "complete" if input_ids else "unavailable"}
    if recovery_authority is not None and "accessRevision" in recovery_authority:
        recovery_source["accessRevision"] = recovery_authority["accessRevision"]
    elif operator is not None:
        recovery_source["accessRevision"] = operator['chain'][0]['accessRevision']
    if operator is not None:
        # Reconcile can release a proven no-effect fence. Hold older inputs
        # before that release too, not only when Resume is later accepted.
        record['recoveryNoInput'] = True
        session['pendingConfiguration']['recoveryNoInput'] = {
            'generationId': (session.get('collaborationGeneration') or {}).get('id'),
            'acceptedAt': time.time()}
        write_private(management.configuration_record_path(session['id'], operation), json.dumps(record))
    if action.endswith(".reconcile"):
        # Cancellation leaves its original applying/interrupted evidence intact
        # until the host task settles and an explicit reconciliation observes
        # this attempt. Classification is not permission to replay the write.
        record = management.configuration_transition(session, operation, phase="error", stage="reconciled",
            effect=effect, recoveryRequired=False, recovery_source=recovery_source)
        management.settle_configuration_fence(session, operation)
    elif action.endswith(".resume"):
        if recovery_authority is not None:
            # Preserve current request lineage as an additional ceiling, never
            # replace the original authority or source binding.
            record.setdefault("resumptions", []).append(recovery_authority)
            write_private(management.configuration_record_path(session["id"], operation), json.dumps(record))
        record = management.configuration_transition(session, operation,
            phase="queued", stage="readback_queued" if effect == 'saved_not_mounted' else "queued",
            effect=effect, error=None, recoveryRequired=False,
            recovery_source=recovery_source)
        # Queue acceptance is not native admission. Accepted work may finish;
        # apply_queued rechecks authority and atomically claims the idle fence.
        management.schedule_configuration(session["id"])
    else:
        raise ValueError("Unknown configuration recovery action")
    return public(record, observed)
