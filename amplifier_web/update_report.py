"""On-demand support report with an allowlist, not a dump of host state.

Do not add raw settings, environment, subprocess output, exception text, chat
content or local paths here. Source identities and structured failure receipts
are enough to distinguish changed installs from failed release preparation.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import platform
import re
import time
from urllib.parse import urlsplit

from .update_diagnostics import ERROR_TYPES, RECOVERY_REASONS, RESOLUTION_FIELDS, PROBE_PREFIX, probe_record


def scalar(value, pattern):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def source(row):
    if not row:
        return {'kind': 'missing'}
    direct = row.get('directUrl') or {}
    vcs = direct.get('vcs_info') or {}
    cached = row.get('cacheSource') or {}
    result = {'kind': 'cache' if cached else 'git' if vcs.get('vcs') == 'git' else
              'editable' if direct.get('dir_info', {}).get('editable') else 'directory' if row.get('path') else
              'direct' if direct else 'registry',
              'version': scalar(row.get('version'), r'[0-9][0-9A-Za-z.+-]{0,60}')}
    revision = cached.get('revision') or vcs.get('commit_id')
    if scalar(revision, r'[a-f0-9]{40,64}'):
        result['revision'] = revision
    if cached:
        result['dirty'] = bool(cached.get('dirty'))
    url = cached.get('url') or direct.get('url', '')
    parsed = urlsplit(url)
    # Public ecosystem repository names are useful; custom URLs/paths get only
    # a relationship fingerprint, never credentials, user names or hostnames.
    if parsed.hostname == 'github.com' and re.fullmatch(r'/microsoft/amplifier[a-z0-9._-]*', parsed.path):
        result['repository'] = 'microsoft/' + parsed.path.rsplit('/', 1)[-1]
    if url:
        result['sourceId'] = hashlib.sha256(url.encode()).hexdigest()[:16]
    return result


def event(row):
    result = {}
    from .update_diagnostics import sanitized_application_extras
    extras = sanitized_application_extras(row.get('applicationExtras'))
    if extras is not None:
        result['applicationExtras'] = extras
    for key in ('id', 'attemptId', 'revision', 'commandId', 'expectedRevision', 'observedRevision'):
        if scalar(row.get(key), r'[a-f0-9]{32,64}'):
            result[key] = row[key]
    for key in ('at', 'durationMs', 'exitCode', 'stdoutBytes', 'stderrBytes'):
        if type(row.get(key)) in (int, float) and abs(row[key]) < 10**15:
            result[key] = row[key]
    for key in ('expectedVersion', 'observedVersion'):
        if scalar(row.get(key), r'\d+\.\d+\.\d+'):
            result[key] = row[key]
    if scalar(row.get('phase'), r'(?:ecosystem|candidate|runtime|replacement|restart|target|recovery|smart-tool|smart-tools|activation|service|background|stage)[a-z-]{0,50}'):
        result['phase'] = row['phase']
    for key, allowed in (('kind', {'application', 'ecosystem', 'smart-tools', 'rollback'}),
                         ('status', {'started', 'accepted', 'rejected', 'uncertain', 'succeeded', 'failed', 'interrupted'}),
                         ('errorType', ERROR_TYPES), ('reason', RECOVERY_REASONS)):
        if isinstance(row.get(key), str) and row[key] in allowed:
            result[key] = row[key]
    if scalar(row.get('package'), r'amplifier-[a-z0-9-]{1,120}'):
        result['package'] = row['package']
    if isinstance(row.get('resolutionMismatches'), list):
        fields = sorted({value for value in row['resolutionMismatches']
                         if isinstance(value, str) and value in RESOLUTION_FIELDS})
        if fields:
            result['resolutionMismatches'] = fields
    if type(row.get('timedOut')) is bool:
        result['timedOut'] = row['timedOut']
    if isinstance(row.get('probe'), dict):
        result['probe'] = probe_record(PROBE_PREFIX + json.dumps(row['probe']))
    return result


def disposition(row):
    """Only fixed disposition facts and sanitized originals leave the owner."""
    if row.get('status') != 'acknowledged-uncertainty':
        return {}
    return {**event(row), 'phase': 'failure-disposition',
            'status': 'acknowledged-uncertainty', 'recoveryVerified': False,
            'target': scalar(row.get('target'), r'updates:failure(?:-overflow|:application:[a-f0-9]{64})'),
            'occurrence': scalar(row.get('occurrence'), r'[a-f0-9]{64}'),
            'occurrences': row.get('occurrences') if type(row.get('occurrences')) is int else None,
            'firstFailureAvailable': row.get('firstFailureAvailable') is True,
            'firstFailure': event(row.get('firstFailure', {})),
            'latestFailure': event(row.get('latestFailure', {}))}


def runtime_report(home):
    from .runtime_qualification import installed_graph
    from .runtime_environment import receipt_directory, project_path
    from .updates import active_release
    pointer = active_release(home)
    generation = pointer.get('current')
    receipt = receipt_directory(home, generation)
    graph = installed_graph(project_path(home, generation))
    saved = receipt / 'runtime-installed.json'
    expected = {row['name']: row for row in json.loads(saved.read_text())} if saved.is_file() else {}
    installed = {row['name']: row for row in graph}
    changes = [{'package': name, 'recorded': expected.get(name), 'installed': installed.get(name)}
               for name in sorted(expected.keys() | installed.keys())
               if saved.is_file() and expected.get(name) != installed.get(name)]
    result = {'generations': {key: scalar(pointer.get(key), r'[a-f0-9]{32}') for key in ('current', 'previous')},
              'packageCount': len(graph), 'recordedGraphAvailable': saved.is_file(),
              'sourceChanges': [{'package': row['package'] if scalar(row['package'], r'[a-z0-9-]{1,128}') else '<custom>',
                                 'recorded': source(row['recorded']), 'installed': source(row['installed'])}
                                for row in changes[:200]],
              'sourceChangeCount': len(changes),
              'packages': [{'package': row['name'], **source(row)} for row in graph[:500]
                           if scalar(row['name'], r'[a-z0-9-]{1,128}')]}
    receipt = receipt_directory(home, pointer.get('current'))
    result['receipts'] = {name: (receipt / name).is_file() for name in
        ('validated.json', 'runtime.lock', 'runtime-installed.json', 'runtime-sources.json', 'runtime-project.json', 'profiles-qualified.json')}
    from .host.config import read_config
    from .bundles import STANDALONE_PROFILES, offered_profiles
    config = read_config(home, home=home, global_only=True)
    registry_file = config.registry_home / 'registry.json'
    cached = json.loads(registry_file.read_text()).get('bundles', {}) if registry_file.is_file() else {}
    offered = offered_profiles(config)
    result['profiles'] = {
        'offeredCount': len(offered),
        'knownOffered': sorted(set(offered) & STANDALONE_PROFILES),
        'unregisteredCached': sorted((set(cached) & STANDALONE_PROFILES) - set(config.registrations)),
    }
    return result


async def collect(manager):
    from . import __version__
    from .update_blockers import assess
    # Snapshot only the update resource. Never serialize all service state.
    state = copy.deepcopy(manager.service.state['updates'])
    diagnostics = state.get('diagnostics', {})
    report = {'schema': 'amplifier-update-diagnostics-v1', 'capturedAt': time.time(),
              'application': {'version': __version__, 'python': platform.python_version(), 'platform': platform.system()},
              'privacy': 'Excludes credentials, settings values, environment, local paths, host/account names and conversation content.',
              'events': [event(row) for row in diagnostics.get('events', [])[-50:]],
              'lastFailure': event(diagnostics.get('lastFailure', {})),
              'failureDispositions': [disposition(row) for row in diagnostics.get('failureDispositions', [])[-20:]],
              'pending': {key: bool(state.get(key)) for key in ('pendingApp', 'pendingRelease', 'pendingRestart', 'pendingSmartTools')},
              'busy': manager.lock.locked()}
    # "busy" above is the updater lock, not the activation gate. Use the same
    # evaluator as busy()/blockers(), not cached labels or a second predicate.
    try:
        gate = assess(manager.service)
        report['activationGate'] = {
            **gate['observation'],
            'blockerCounts': {kind: sum(row['kind'] == kind for row in gate['blockers'])
                              for kind in sorted({row['kind'] for row in gate['blockers']})},
        }
    except Exception:
        # Failed observation is not quiescence. Do not change the safety gate's
        # exception behavior or expose private observer errors in this report.
        report['activationGate'] = {'schemaVersion': 1, 'observedAt': time.time(),
                                    'unavailable': True, 'blocked': None, 'reasonCode': 'observation-failed'}
    report['activationGate'].update(
        requiresIdle=manager.requires_idle(state),
        qualification='Point-in-time observation only; not evidence of no effects or permission to retry. '
                      'Initial runtime sample precedes per-hold checks. '
                      'Runtime and lock activity can change without a state revision.')
    sequence = state.get('sequence') or {}
    phases = {'idle', 'checking', 'available', 'checked', 'staging', 'validating', 'staged', 'app-staged', 'activating', 'installed', 'error', 'interrupted'}
    stages = {'application', 'included', 'other', 'complete'}
    report['updateState'] = {
        'phase': state.get('phase') if state.get('phase') in phases else 'unknown',
        'stage': sequence.get('stage') if sequence.get('stage') in stages else None,
        'nextStage': sequence.get('nextStage') if sequence.get('nextStage') in stages else None,
        'installRequested': sequence.get('install') is True,
        'hasError': bool(state.get('error')),
        'checkInProgress': bool(getattr(manager, 'check_task', None) and not manager.check_task.done()),
    }
    for name, value in [('revision', manager.service.state.get('revision')), ('lastCheck', state.get('lastCheck')),
                        ('lastAttempt', state.get('lastAttempt')), ('available', state.get('available'))]:
        if type(value) in (int, float) and 0 <= value < 10**15:
            report['updateState'][name] = value
    for key in ('checkTiming', 'adoption'):
        report[key] = {name: value for name, value in (state.get(key) or {}).items()
                       if name in {'elapsedMs', 'requests', 'cached', 'joined', 'pendingWorkers', 'activeWorkers', 'startedAt', 'finishedAt'}
                       and type(value) in (int, float) and 0 <= value < 10**15}
    report['recentChecks'] = [{name: value for name, value in row.items()
                             if name in {'elapsedMs', 'requests', 'cached', 'joined', 'startedAt', 'finishedAt'}
                             and type(value) in (int, float) and 0 <= value < 10**15}
                            for row in state.get('recentChecks', [])[-5:] if isinstance(row, dict)]
    try:
        report['runtime'] = await asyncio.to_thread(runtime_report, manager.home)
    except Exception as error:
        report['runtime'] = {'unavailable': True, 'errorType': type(error).__name__ if type(error).__name__ in ERROR_TYPES else 'Exception'}
    return report
