"""Current update projection owned by UpdateManager, not a second incident ledger.

Qualification is observed at owner boundaries, never by a renderer. Old attempt
receipts remain facts about those attempts; current health does not resolve them.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .update_diagnostics import RUNTIME_SOURCE_REASONS, failure_guidance

DETERMINISTIC = RUNTIME_SOURCE_REASONS - {'runtime-source-changed'} | {
    'host-component-conflict', 'missing-configured-environment', 'incompatible-conversation-loop'}


def configuration(manager):
    from .host.config import read_config
    from .runtime_profiles import configuration_key
    config = read_config(manager.home, home=manager.home, global_only=True)
    return configuration_key(config.settings, manager.home), config


def active_evidence(manager):
    """Read existing immutable qualification and actual graph; never prepare it."""
    from .updates import active_release
    from .runtime_environment import project_path
    from .runtime_qualification import verify_recorded
    from .runtime_profiles import configuration_key
    try:
        pointer = active_release(manager.home)
        generation = pointer.get('current')
        if not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{32}', generation):
            return {}
        stage = manager.directory / 'releases' / generation
        marker = json.loads((stage / 'validated.json').read_text())
        profiles = json.loads((stage / 'profiles-qualified.json').read_text())
        graph = json.loads((stage / 'runtime-installed.json').read_text())
        plan = json.loads((stage / 'plan.json').read_text())
        batches = manager.diagnostics.state.get('completedBatches', [])
        activation = next((b for b in reversed(batches)
            if b.get('revision') == generation and b.get('phase') == 'ecosystem-activation'), None)
        if activation is None:
            # Older bounded summaries stored the revision only in the exact
            # terminal event. This is receipt identity, not incident matching.
            for batch in reversed(batches):
                event = next((row for row in manager.diagnostics.state.get('events', [])
                    if row.get('revision') == generation and row.get('phase') == 'ecosystem-activation'
                    and row.get('status') == 'succeeded' and row.get('attemptId') == batch.get('attemptId')
                    and row.get('at') == batch.get('completedAt')), None)
                if event:
                    activation = {**batch, 'revision': generation}
                    break
        if (plan.get('mode') not in {'worker', 'data'}
                or marker.get('generationSchema') != 1 or marker.get('workerProtocol') != 1
                or not graph or any(not isinstance(row, dict) or not row.get('name') for row in graph)
                or not profiles.get('profiles')
                or not 0 < marker['createdAt'] <= pointer['at']
                or activation and not 0 < activation['startedAt'] <= marker['createdAt'] <= pointer['at'] <= activation['completedAt']):
            return {}
        _, config = configuration(manager)
        if profiles.get('configuration') != configuration_key(config.settings, manager.home, stage):
            return {}
        project = project_path(manager.home, generation)
        if (project / 'uv.lock').read_bytes() != (stage / 'runtime.lock').read_bytes():
            return {}
        verify_recorded(project, stage)
        # An observation that raced a new pointer/configuration is not evidence
        # for whichever installation won that race.
        if active_release(manager.home) != pointer:
            return {}
        _, current_config = configuration(manager)
        if profiles['configuration'] != configuration_key(current_config.settings, manager.home, stage):
            return {}
        from urllib.parse import urlsplit
        components = {row['name'] for row in graph}
        for row in graph:
            source = (row.get('cacheSource') or {}).get('url') or (row.get('directUrl') or {}).get('url')
            if isinstance(source, str):
                components.add(urlsplit(source).path.rstrip('/').rsplit('/', 1)[-1].removesuffix('.git'))
        if activation is None:
            # The durable pointer precedes runtime promotion. It proves which
            # graph is selected, not that promotion completed. Losing bounded
            # history therefore yields a neutral limitation, never healthy proof.
            return {'generation': generation, 'activationUnknown': True,
                    'qualifiedAt': marker['createdAt'], 'configuration': profiles['configuration'],
                    'packages': {row['name'] for row in graph}, 'components': components}
        return {'generation': generation, 'verifiedAt': activation['completedAt'],
                'startedAt': activation['startedAt'],
                'configuration': profiles['configuration'], 'attemptId': activation['attemptId'],
                'packages': {row['name'] for row in graph}, 'components': components}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}  # Unknown evidence is not a healthy installed graph.


def preparation_key(manager, rows):
    """Private witness for expensive work, not permission to install or a URL log."""
    from .updates import active_release
    from . import __version__
    source_key, config = configuration(manager)
    keys = ('id', 'kind', 'url', 'ref', 'current', 'latest', 'subdirectory', 'package',
            'status', 'eligible', 'policyOrigin', 'updateTier')
    selected = []
    for row in rows:
        value = {key: row[key] for key in keys if key in row}
        if (row.get('status') == 'check_failed' and row.get('availabilityFailure') is True
                and row.get('sourceUpdate') in {'failed', 'dependency-held'}
                and isinstance(row.get('latest'), str) and re.fullmatch('[a-f0-9]{40}', row['latest'])
                and row.get('current') != row['latest']):
            # A receipt-derived retained presentation is the same desired graph
            # as its pre-suppression update row, not a new qualification input.
            # Unknown fresh failures never receive this classification.
            value['status'] = 'update'
        selected.append(value)
    selected.sort(key=lambda row: json.dumps(row, sort_keys=True))
    # The dependency graph key deliberately omits module values/identities.
    # Qualification consumes them through host.session._apply_settings; fixing
    # those values (or disabling a failing module) must permit a new attempt.
    # Hash only runtime configuration and the canonical offered-profile set,
    # not unrelated preferences or the publication counter. Values stay private.
    from .bundles import profile_candidates
    qualification = {key: config.settings.get(key) for key in
        ('config', 'modules', 'overrides', 'configurator', 'routing', 'decision_models')}
    data = [__version__, active_release(manager.home).get('current'), source_key,
            qualification, config.active_bundle, profile_candidates(config), selected]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def retain_environment_failure(error, stage, attempt):
    """Private candidate evidence from the actual canonical expansion failure."""
    from pathlib import Path
    from .host.config import MissingConfiguredEnvironment, write_private
    if not isinstance(error, MissingConfiguredEnvironment):
        return
    stage = Path(stage)
    if not re.fullmatch('[a-f0-9]{32}', stage.name) or not re.fullmatch('[a-f0-9]{32}', attempt or ''):
        return
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,255}', error.reference):
        return
    write_private(stage / 'missing-environment.json', json.dumps({
        'candidate': stage.name, 'attemptId': attempt, 'reference': error.reference}),
        owned_root=stage)


def environment_repaired(manager, batch):
    """Only the bound missing-environment failure can use this availability fact."""
    import os
    import stat
    from .host.config import missing_environment_available
    failure = manager.diagnostics.state.get('lastFailure') or {}
    if ((batch.get('retry') or {}).get('mode') != 'changed-inputs'
            or failure.get('attemptId') != batch.get('attemptId')
            or (failure.get('reason') or failure.get('probe', {}).get('reason')) != 'missing-configured-environment'):
        return False
    proof = batch.get('preparation') or {}
    candidate = proof.get('candidate')
    if not isinstance(candidate, str) or not re.fullmatch('[a-f0-9]{32}', candidate):
        return False
    stage = manager.directory / 'releases' / candidate
    try:
        from .staged_sources import owned_path
        path = owned_path(stage / 'missing-environment.json', manager.directory / 'releases')
        if [stage.stat().st_dev, stage.stat().st_ino] != proof.get('directoryIdentity'):
            return False
        if pointer_witness(manager) != proof.get('activeBefore'):
            return False
        if os.name == 'nt':
            from .windows_files import directory
            with directory(stage, owned_root=manager.directory / 'releases') as pinned:
                raw = pinned.read_private(path.name, limit=2048)
        else:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_nlink != 1 or info.st_mode & 0o077):
                    return False
                raw = source.read(2049)
                if len(raw) > 2048:
                    return False
        evidence = json.loads(raw)
        if (not isinstance(evidence, dict) or set(evidence) != {'candidate', 'attemptId', 'reference'}
                or evidence['candidate'] != candidate or evidence['attemptId'] != batch.get('attemptId')):
            return False
        return missing_environment_available(evidence['reference'])
    except (OSError, ValueError, TypeError, KeyError):
        return False  # Absent/expired/malformed evidence never grants retry.


def retry_decision(batch, key, *, now=None):
    retry = batch.get('retry') or {}
    if retry.get('mode') == 'inspect':
        return 'inspect'  # Changed inputs cannot prove an uncertain effect absent.
    if retry.get('inputKey') != key:
        return None
    if retry.get('mode') in {'changed-inputs', 'inspect'}:
        return retry['mode']
    if retry.get('retryAt', 0) > (time.time() if now is None else now):
        return 'backoff'
    return None


def record_retry(batch, key, failure, previous=None):
    reason = failure.get('reason') or failure.get('probe', {}).get('reason')
    previous = previous or {}
    count = previous.get('attempts', 0) + 1 if previous.get('inputKey') == key else 1
    mode = ('inspect' if failure.get('status') == 'interrupted' else
            'changed-inputs' if reason in DETERMINISTIC else 'backoff')
    batch['retry'] = {'inputKey': key, 'mode': mode, 'attempts': count,
                      'retryAt': time.time() + min(3600, 60 * 2 ** min(count - 1, 6))}


def pointer_witness(manager):
    """Exact durable pointer bytes, including absence; no moving-ref inference."""
    from .updates import active_release
    active_release(manager.home)  # Reject malformed identities, not just hash them.
    path = manager.directory / 'active.json'
    if path.is_symlink():
        raise ValueError('The component pointer is not an owned file.')
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def pending_preparation(diagnostics):
    """One unresolved component operation, independent of the display batch."""
    if 'componentPreparation' in diagnostics:
        return diagnostics['componentPreparation']
    batch = diagnostics.get('batch', {})
    return batch if (batch.get('retry') or {}).get('mode') == 'inspect' else None


def retain_preparation(diagnostics):
    """Preserve exact existing proof before another domain replaces its batch."""
    import copy
    if 'componentPreparation' in diagnostics:
        return
    batch = diagnostics.get('batch', {})
    if (batch.get('retry') or {}).get('mode') != 'inspect':
        return
    terminal = next((row for row in reversed(diagnostics.get('events', []))
        if row.get('attemptId') == batch.get('attemptId') and
        row.get('phase') == 'ecosystem-stage' and row.get('status') == 'interrupted'), None)
    diagnostics['componentPreparation'] = copy.deepcopy({
        'attemptId': batch.get('attemptId'), 'kind': batch.get('kind'),
        'revision': diagnostics.get('revision'), 'retry': batch.get('retry'),
        'preparation': batch.get('preparation'), 'terminal': terminal})


async def candidate_copy(proof, operation, *args):
    """Cancellation must not pretend a to_thread copy has stopped.

    Only the initial, candidate-only snapshot copies use this helper. If the
    owned thread does not settle promptly, retain the inspection fence instead.
    No subsequent fetch, installer or native initialization is started here.
    """
    import asyncio
    proof['copySettled'] = False
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(asyncio.shield(task), 5)
        except (Exception, asyncio.CancelledError):
            pass
        raise
    finally:
        proof['copySettled'] = task.done() and not task.cancelled()


def reconcile_preparation(manager):
    """Permit fresh intent only after a proven inactive, settled copy attempt.

    Called under the update lock. Never resumes the old candidate or infers
    settlement for legacy/unknown fetch, validation, activation or rollback.
    """
    diagnostics = manager.diagnostics.state
    batch = diagnostics.get('componentPreparation') or {}
    proof = batch.get('preparation') or {}
    state = manager.service.state['updates']
    candidate = proof.get('candidate')
    if (diagnostics.get('storageUnavailable') or batch.get('kind') != 'ecosystem'
            or not batch.get('attemptId')
            or not isinstance(candidate, str) or not re.fullmatch('[a-f0-9]{32}', candidate)
            or batch.get('revision') != candidate
            or proof.get('boundary') != 'copy-only' or proof.get('copySettled') is not True
            or not proof.get('interruptedAt') or 'activeBefore' not in proof
            or any(state.get(key) for key in ('pendingApp', 'pendingRelease', 'pendingRollback',
                                              'pendingRestart', 'pendingReplacement', 'pendingSmartTools'))
            or state.get('phase') == 'activating'):
        return False
    try:
        from .updates import active_release
        stage = manager.directory / 'releases' / candidate
        if (stage.is_symlink() or stage.resolve() != manager.directory.resolve() / 'releases' / candidate
                or [stage.stat().st_dev, stage.stat().st_ino] != proof.get('directoryIdentity')
                or any((stage / name).exists() for name in ('plan.json', 'validated.json'))
                or candidate in (active_release(manager.home).get('current'), active_release(manager.home).get('previous'))
                or pointer_witness(manager) != proof['activeBefore']):
            return False
        latest = batch.get('terminal') or {}
        if (latest.get('attemptId') != batch['attemptId'] or latest.get('status') != 'interrupted'
                or latest.get('phase') != 'ecosystem-stage'):
            return False
        if any(row.get('attemptId') == batch['attemptId'] and
               row.get('phase') in {'ecosystem-activation', 'ecosystem-rollback'}
               for row in diagnostics.get('events', [])):
            return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
    manager.diagnostics.record('ecosystem-preparation-reconciled', 'succeeded')
    if diagnostics.get('storageUnavailable'):
        return False
    diagnostics.pop('componentPreparation', None)
    if diagnostics.get('batch', {}).get('attemptId') == batch['attemptId']:
        diagnostics['batch']['retry'] = {**batch['retry'], 'mode': 'fresh-intent', 'reconciledAt': time.time()}
    return True


def failure_events(state):
    """Bounded old-state import; no scope matching, mutation, or recovery claims."""
    diagnostics = state.get('diagnostics', {})
    rows = [g.get('latest', {}) for g in diagnostics.get('activeFailures', {}).values()]
    rows += [diagnostics.get('lastFailure') or {}]
    overflow = diagnostics.get('failureOverflow') or {}
    if overflow.get('latest'):
        rows.append(overflow['latest'])
    return rows


def current(state, evidence=None, *, manual_verified=None):
    """Pure projection of operation/desired/active state; no IO and no read flags."""
    evidence = evidence or {}
    seq = state.get('sequence') or {}
    app = state.get('application') or {}
    diagnostics = state.get('diagnostics') or {}
    batch = diagnostics.get('batch') or {}
    retry = batch.get('retry') or {}
    phase = state.get('phase')
    def result(status, title, detail, *, action=None, component=None):
        return {'status': status, 'title': title, 'detail': detail,
                'actionRequired': status in {'action-required', 'protected', 'unverified'},
                'action': action, 'component': component}
    if state.get('pendingReplacement') is not None:
        return result('unverified', 'Installation needs verification',
                      'The application replacement outcome is not confirmed. Work remains protected; inspect the update diagnostics before another installation.', action='diagnostics')
    if state.get('pendingRestart'):
        failed = phase in {'error', 'interrupted'} or state['pendingRestart'].get('requestStatus') == 'rejected'
        return result('action-required' if failed else 'waiting',
                      'Restart needs attention' if failed else 'Waiting for restart confirmation',
                      'A healthy restarted application must confirm this exact update. No installation is replayed.',
                      action='diagnostics' if failed else None)
    if phase in {'checking', 'staging', 'validating', 'activating'}:
        return result('working', 'Checking for updates' if phase == 'checking' else 'Updating Amplifier',
                      state.get('detail') or 'Preparing and checking the requested update.')
    if state.get('pendingApp') or state.get('pendingRelease'):
        return result('action-required' if state.get('error') else 'waiting',
                      'Update needs attention' if state.get('error') else 'Update ready; waiting for work',
                      state.get('error') or 'The qualified update continues automatically when its activation boundary is ready.',
                      action='diagnostics' if state.get('error') else None)
    if pending_preparation(diagnostics):
        return result('action-required', 'Component preparation was interrupted',
                      'Automatic component work is paused. A deliberate update can start a new candidate only after settled copy-only preparation is verified; other uncertain effects require diagnostic review.',
                      action='diagnostics')
    outcomes = state.get('componentResults') or []
    retained = [row for row in outcomes if row.get('status') in {'failed', 'dependency-held'}]
    if retained and not state.get('available'):
        updated = sum(row.get('status') == 'updated' for row in outcomes)
        return result('action-required', 'Some components kept their working versions',
                      f'{updated} source records updated; {len(retained)} retained after a source failure or dependency hold. '
                      'New work uses the qualified combination. Check manually to retry; prior failures remain in history.',
                      action='sources')
    local_changes = [r for r in state.get('items', []) if r.get('status') == 'local_changes']
    issues = [r for r in state.get('items', []) if r.get('status') == 'check_failed']
    if issues:
        row = issues[0]
        return result('action-required', 'A source could not be checked',
                      row.get('detail') or 'The installed source is preserved. Review its source details before updating.',
                      action='sources', component=row.get('label'))
    if any((seq.get(tier) or {}).get('issues', 0) > sum(
            row.get('updateTier') == tier for row in local_changes) for tier in ('included', 'other')):
        return result('action-required', 'Some sources need review',
                      'A component check is incomplete or a local change is protected. Review source details before updating.',
                      action='sources')
    failed = [row for row in failure_events(state) if row.get('status') in {'failed', 'interrupted'}]
    application_result = state.get('applicationResult') or {}
    application_batch = next((row for row in reversed(diagnostics.get('completedBatches', []))
        if row.get('kind') == 'application'
        and row.get('phase') == 'restart-ack'
        and row.get('attemptId') == application_result.get('attemptId')
        and row.get('revision') == application_result.get('revision')), {})
    application_verified = (application_result.get('phase') == 'restart-ack'
        and application_result.get('revision') == app.get('runningRevision')
        and application_result.get('version') == app.get('current')
        and 0 < application_batch.get('startedAt', 0)
            <= application_result.get('verifiedAt', 0) <= application_batch.get('completedAt', 0))
    def currently_verified(row):
        manual = state.get('manualHandoff') or {}
        if (manual.get('phase') == 'manual-handoff-reconciled'
                and manual_verified is not None and manual_verified == manual.get('requestId')
                and manual.get('revision') == app.get('runningRevision')
                and manual.get('version') == app.get('current')
                and row == manual.get('refusal')):
            return True  # Exact historical pre-effect refusal, not generic recovery.
        # A later complete, applicable operation proves current readiness for
        # generic preparation/OS failures too, not just a reason-code allowlist.
        # Missing packages and unknown app feature scope remain unverified.
        package = row.get('package') or row.get('probe', {}).get('package')
        if row.get('kind') == 'ecosystem':
            return (bool(evidence) and (not package or package in evidence.get('packages', ()))
                    and (not row.get('component') or row['component'] in evidence.get('components', ()))
                    and 0 < row.get('at', 0) < evidence.get('startedAt', 0))
        if row.get('kind') == 'application' and application_verified:
            from .update_diagnostics import sanitized_application_extras
            requested = sanitized_application_extras(row.get('applicationExtras'))
            installed = sanitized_application_extras(application_batch.get('applicationExtras'))
            return (requested is not None and installed is not None and set(requested) <= set(installed)
                    and 0 < row.get('at', 0) < application_batch['startedAt'])
        return False
    def older_preparation(row):
        # An old preparation failure for this verified graph is history even if
        # the completion receipt has aged out. Activation failures, unknown scope
        # and any later occurrence still require inspection.
        package = row.get('package') or row.get('probe', {}).get('package')
        component = row.get('component')
        return (evidence.get('activationUnknown') and row.get('kind') == 'ecosystem'
                and row.get('status') == 'failed'
                and row.get('phase') in {'ecosystem-stage', 'ecosystem-validation',
                                        'ecosystem-source-policy', 'ecosystem-source-preflight'}
                and (package or component)
                and (not package or package in evidence.get('packages', ()))
                and (not component or component in evidence.get('components', ()))
                and 0 < row.get('at', 0) < evidence.get('qualifiedAt', 0))
    remaining = [row for row in failed if not currently_verified(row) and not older_preparation(row)]
    if state.get('error') or phase in {'error', 'interrupted'} or remaining:
        failure = max(remaining, key=lambda row: row.get('at', 0), default=diagnostics.get('lastFailure') or {})
        reason = failure.get('reason') or failure.get('probe', {}).get('reason')
        component = failure.get('package') or failure.get('probe', {}).get('package') or failure.get('component')
        if retry.get('mode') == 'backoff' and retry.get('retryAt', 0) > time.time() and phase != 'interrupted':
            return result('retrying', 'Update will retry later', 'The current installation is kept. A transient failure is waiting for a bounded retry; details remain in history.')
        if reason in DETERMINISTIC:
            return result('protected', 'Component source needs review',
                          failure_guidance(failure) or 'A source or configuration conflict prevented this update. Current components remain active. Review source settings or share diagnostics; unchanged automatic attempts are paused.',
                          action='sources', component=component)
        return result('action-required', 'Update could not finish',
                      failure_guidance(failure) or state.get('error') or 'The prior attempt has no applicable verified outcome. Current installation is retained; inspect diagnostics before retrying.',
                      action='diagnostics', component=component)
    if any(row.get('failure') for row in state.get('pendingSmartTools', [])):
        return result('action-required', 'Some tools were not updated',
                      'Their current versions remain installed. Review the failed tool to retry or keep its current version.',
                      action='tools')
    if state.get('available') or seq.get('nextStage') or any(
            (seq.get(tier) or {}).get('available') for tier in ('included', 'other')):
        return result('available', 'Updates available', 'The app and eligible components can update while preserving configured pins and local sources.', action='install')
    if local_changes or seq.get('stage') == 'complete' and app.get('status') == 'current':
        if evidence.get('activationUnknown'):
            return result('unchecked', 'Current components kept',
                          'The installed graph matches its recorded qualification. Its older activation receipt is no longer retained, so full activation verification is unavailable. No reinstall is requested.')
        if not evidence:
            return result('unverified', 'Component verification is incomplete',
                          'The active component graph or configuration could not be verified. Current components are kept. Check for updates and inspect diagnostics if this remains.', action='check')
        if local_changes:
            return result('unchecked', 'Custom sources kept',
                          'Local source changes are kept as configured and are not replaced automatically. Source details remain available; no repair is required for intentional customization.')
        return result('current', 'You’re up to date', 'The current application and qualified components are ready. Earlier attempts remain in history; configured fixed versions are kept.')
    return result('unchecked', 'Check for updates', 'The full desired component state has not been checked.', action='check')


def publish(manager):
    state = manager.service.state['updates']
    evidence = getattr(manager, '_active_update_evidence', {})
    # Settings mutations invalidate proof immediately without a graph scan on
    # each publication. Only an owner check/activation can establish it again.
    revision = manager.service.state.get('configurationRevision')
    if getattr(manager, '_update_health_configuration_revision', revision) != revision:
        if current(state, evidence)['status'] in {'current', 'unchecked'}:
            state['currentStatus'] = {'status': 'unchecked', 'title': 'Checking current configuration',
                'detail': 'Update health will refresh against the changed settings. Existing components remain active.',
                'actionRequired': False, 'action': None, 'component': None}
            return
        evidence = {}
    state['currentStatus'] = current(state, evidence,
        manual_verified=getattr(manager, '_manual_handoff_verified', None))


async def refresh(manager):
    import asyncio
    revision = manager.service.state.get('configurationRevision')
    evidence = await asyncio.to_thread(active_evidence, manager)
    if revision == manager.service.state.get('configurationRevision'):
        manager._active_update_evidence = evidence
        manager._update_health_configuration_revision = revision
    else:
        manager._active_update_evidence = {}
