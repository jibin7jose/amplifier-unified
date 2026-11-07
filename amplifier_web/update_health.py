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
        if (not activation or plan.get('mode') not in {'worker', 'data'}
                or marker.get('generationSchema') != 1 or marker.get('workerProtocol') != 1
                or not graph or any(not isinstance(row, dict) or not row.get('name') for row in graph)
                or not profiles.get('profiles')
                or not 0 < activation['startedAt'] <= marker['createdAt'] <= pointer['at'] <= activation['completedAt']):
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
        return {'generation': generation, 'verifiedAt': activation['completedAt'],
                'configuration': profiles['configuration'], 'attemptId': activation['attemptId'],
                'packages': {row['name'] for row in graph}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}  # Unknown evidence is not a healthy installed graph.


def preparation_key(manager, rows):
    """Private witness for expensive work, not permission to install or a URL log."""
    from .updates import active_release
    from . import __version__
    config, _ = configuration(manager)
    keys = ('id', 'kind', 'url', 'ref', 'current', 'latest', 'subdirectory', 'package',
            'status', 'eligible', 'policyOrigin', 'updateTier')
    selected = [{key: row[key] for key in keys if key in row} for row in rows]
    selected.sort(key=lambda row: json.dumps(row, sort_keys=True))
    data = [__version__, active_release(manager.home).get('current'), config,
            manager.service.state.get('configurationRevision'), selected]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


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


def failure_events(state):
    """Bounded old-state import; no scope matching, mutation, or recovery claims."""
    diagnostics = state.get('diagnostics', {})
    rows = [g.get('latest', {}) for g in diagnostics.get('activeFailures', {}).values()]
    rows += [diagnostics.get('lastFailure') or {}]
    overflow = diagnostics.get('failureOverflow') or {}
    if overflow.get('latest'):
        rows.append(overflow['latest'])
    return rows


def current(state, evidence=None):
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
    issues = [r for r in state.get('items', []) if r.get('status') in {'check_failed', 'local_changes'}]
    if issues:
        row = issues[0]
        return result('protected' if row['status'] == 'local_changes' else 'action-required',
                      'Source changes kept' if row['status'] == 'local_changes' else 'A source could not be checked',
                      row.get('detail') or 'The installed source is preserved. Review its source details before updating.',
                      action='sources', component=row.get('label'))
    if any((seq.get(tier) or {}).get('issues') for tier in ('included', 'other')):
        return result('action-required', 'Some sources need review',
                      'A component check is incomplete or a local change is protected. Review source details before updating.',
                      action='sources')
    failed = [row for row in failure_events(state) if row.get('status') in {'failed', 'interrupted'}]
    def currently_verified(row):
        # The current graph can prove a source-policy concern is no longer a
        # current blocker without matching historical URL batch labels. It
        # proves nothing about missing/unselected packages or unrelated effects.
        reason = row.get('reason') or row.get('probe', {}).get('reason')
        package = row.get('package') or row.get('probe', {}).get('package')
        return (row.get('kind') == 'ecosystem' and reason in RUNTIME_SOURCE_REASONS
                and package in evidence.get('packages', ())
                and 0 < row.get('at', 0) < evidence.get('verifiedAt', 0))
    remaining = [row for row in failed if not currently_verified(row)]
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
    if seq.get('stage') == 'complete' and app.get('status') == 'current':
        if not evidence:
            return result('unverified', 'Component verification is incomplete',
                          'The active component graph or configuration could not be verified. Current components are kept. Check for updates and inspect diagnostics if this remains.', action='check')
        return result('current', 'You’re up to date', 'The current application and qualified components are ready. Earlier attempts remain in history; configured fixed versions are kept.')
    return result('unchecked', 'Check for updates', 'The full desired component state has not been checked.', action='check')


def publish(manager):
    state = manager.service.state['updates']
    evidence = getattr(manager, '_active_update_evidence', {})
    # Settings mutations invalidate proof immediately without a graph scan on
    # each publication. Only an owner check/activation can establish it again.
    revision = manager.service.state.get('configurationRevision')
    if getattr(manager, '_update_health_configuration_revision', revision) != revision:
        evidence = {}
    state['currentStatus'] = current(state, evidence)


async def refresh(manager):
    import asyncio
    revision = manager.service.state.get('configurationRevision')
    evidence = await asyncio.to_thread(active_evidence, manager)
    if revision == manager.service.state.get('configurationRevision'):
        manager._active_update_evidence = evidence
        manager._update_health_configuration_revision = revision
    else:
        manager._active_update_evidence = {}
