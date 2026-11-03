"""Allowlisted reproduction facts. Never serialize devices, drafts or errors."""
import hashlib
import json
import platform
import math
import re
import time
from datetime import datetime
from pathlib import Path
from . import __version__

DEVICE_FIELDS = {
    'frontendVersion': {'type':'string','pattern':r'^(unknown|dev|[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*))$','maxLength':60},
    'frontendBuild': {'type':'string','pattern':r'^(unknown|dev|[a-f0-9]{12,64})$'},
    'browser': {'enum':['Chrome','Edge','Firefox','Safari','Other']},
    'browserVersion': {'type':'string','pattern':r'^[0-9.]{0,40}$'},
    'deviceOS': {'enum':['Windows','macOS','Linux','Android','iOS','Other']},
    'width': {'type':'integer','minimum':0,'maximum':32768},
    'height': {'type':'integer','minimum':0,'maximum':32768},
    'pixelRatio': {'type':'number','minimum':0,'maximum':16},
    'colorPreference': {'enum':['dark','light']},
    'appearance': {'enum':['dark','light','system']},
    'resolvedAppearance': {'enum':['dark','light']},
    'standalone': {'type':'boolean'}, 'secureContext': {'type':'boolean'},
    'windowControlsOverlay': {'enum':['unsupported','hidden','visible','invalid-geometry','unavailable']},
    'windowChromeLayout': {'enum':['standard','integrated','stacked']},
    'online': {'type':'boolean'}, 'eventStream': {'enum':['unknown','connecting','open','reconnecting','closed']}, 'serviceWorkerControlled': {'type':'boolean'},
    'reducedMotion': {'type':'boolean'}, 'visible': {'type':'boolean'},
    'pageAgeSeconds': {'type':'integer','minimum':0,'maximum':31536000},
    'pendingActions': {'type':'integer','minimum':0,'maximum':100000},
    'oldestPendingMs': {'type':'integer','minimum':0,'maximum':31536000000},
}
DEVICE_SCHEMA={'type':'object','properties':DEVICE_FIELDS,'additionalProperties':False}

def build_facts():
    try:
        build=json.loads((Path(__file__).parent/'static/build.json').read_text())
    except (OSError,ValueError):
        build={}
    return {'appVersion':__version__,'osFamily':platform.system(), 'pythonVersion':platform.python_version(),
        'packagedFrontendVersion':build.get('version','unknown'),'packagedFrontendBuild':build.get('id','unknown')}

# Feedback is deliberately narrower than the local diagnostics UI: custom names,
# raw errors, correlation IDs and module/source configuration never leave here.
FAILURE_CATEGORIES = {'unknown', 'invalid_image', 'context_limit', 'tool_configuration',
    'authentication', 'rate_limit', 'worker_startup', 'worker_communication',
    'worker_exit', 'computer_capture_stop', 'request_preparation'}
ERROR_TYPES = {'Error', 'RuntimeError', 'RuntimeStartupError', 'ConfiguredModuleError',
    'WorkerCommunicationError', 'WorkerExitedError',
    'ContextLengthError', 'InvalidRequestError', 'AuthenticationError', 'RateLimitError',
    'BadRequestError', 'APIConnectionError', 'APITimeoutError', 'TimeoutError',
    'PermissionError', 'FileNotFoundError', 'ValueError', 'ModuleNotFoundError', 'ImportError'}
ACTIVITY_PHASES = {'idle', 'starting', 'preparing', 'model', 'tools', 'retrying',
    'compacting', 'stopping', 'stopped', 'error', 'completed', 'waiting', 'approval'}
MODULE_TYPES = {'tool', 'hook', 'provider', 'orchestrator', 'context', 'resolver'}
MODULE_REASONS = {'invalid_package_layout', 'missing_source', 'invalid_entry_point',
    'invalid_module_metadata', 'validation_failed', 'provider_schema_failed',
    'provider_configuration_failed', 'unknown'}


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return value if isinstance(value, list) else []


def _enum(value, allowed, missing='unknown'):
    return value if isinstance(value, str) and value in allowed else missing


def _count(value):
    return min(value, 1_000_000_000) if type(value) is int and value >= 0 else None


def _boolean(value):
    return value if type(value) is bool else None


def _identity(value, pattern):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def _age(value, now):
    if isinstance(value, str):
        if len(value) > 64:
            return None
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                return None
            value = stamp.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or value > now:
        return None
    return min(int(now - value), 31_536_000)


def troubleshooting(state, session, now):
    failure = _object(session.get('failure'))
    latest = next((row for row in reversed(_rows(session.get('messages')))
                   if isinstance(row, dict) and row.get('role') == 'user'), {})
    failure_input, latest_input = failure.get('inputId'), latest.get('inputId')
    relation = 'unknown'
    if isinstance(failure_input, str) and failure_input and isinstance(latest_input, str) and latest_input:
        if failure_input == latest_input:
            relation = 'latest'
        elif any(isinstance(row, dict) and row.get('role') == 'user' and row.get('inputId') == failure_input
                 for row in _rows(session.get('messages'))):
            relation = 'earlier'
    modules = _rows(session.get('moduleFailures'))
    by_type, by_reason = {}, {}
    for row in modules[:100]:
        row = _object(row)
        kind = _enum(row.get('type'), MODULE_TYPES)
        reason = _enum(row.get('reason_code'), MODULE_REASONS)
        by_type[kind] = by_type.get(kind, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
    updates = _object(state.get('updates'))
    return {
        'runningAppRevision': _identity(_object(updates.get('application')).get('runningRevision'), r'[a-f0-9]{40,64}'),
        # This is the host's active pointer, NOT an attestation of loaded workers.
        'hostActiveComponentGeneration': _identity(updates.get('release'), r'[a-f0-9]{32}'),
        'workerLoadedComponentGeneration': 'unverified',
        'activityPhase': _enum(_object(session.get('activity')).get('phase'), ACTIVITY_PHASES),
        'latestInputDelivery': _enum(_object(latest.get('delivery')).get('status'), {'accepted', 'failed', 'unknown', 'sending'}),
        'recordedFailure': {
            'present': bool(failure),
            'category': _enum(failure.get('category'), FAILURE_CATEGORIES),
            'errorType': _enum(failure.get('errorType'), ERROR_TYPES, 'other' if failure.get('errorType') else 'unknown'),
            'code': _enum(failure.get('code'), {'computer_result_not_image'}),
            'ageSeconds': _age(failure.get('recordedAt'), now),
            'inputRelation': relation,
        },
        'moduleFailures': {'total': len(modules), 'sampled': min(len(modules), 100),
                           'byType': by_type, 'byReason': by_reason},
    }


def snapshot(state, device=None, *, now=None):
    session = next((row for row in _rows(state.get('sessions'))
                    if isinstance(row, dict) and row.get('id') == state.get('selectedSessionId')), {})
    view = _object(state.get('view'))
    statuses = {}
    for row in _rows(session.get('workers')):
        key = _enum(_object(row).get('status'), {'idle', 'running', 'working', 'starting', 'queued',
            'completed', 'cancelled', 'error', 'failed', 'stopped', 'interrupted'}, 'other')
        statuses[key] = statuses.get(key, 0) + 1
    result = {**build_facts(), 'schemaVersion': 2, 'stateRevision': _count(state.get('revision')),
        'troubleshooting': troubleshooting(state, session, time.time() if now is None else now),
        'library': {'conversations': len(_rows(state.get('sessions'))), 'workspaces': len(_rows(state.get('workspaces'))),
            'availableWorkspaces': sum(_object(w).get('available') is True for w in _rows(state.get('workspaces'))),
            'scope': 'all' if view.get('navChatScope') == 'all' else 'workspace', 'filterActive': bool(view.get('navFilter')),
            'page': _count(_object(state.get('chatNavigation')).get('index'))},
        'conversation': {'status': _enum(session.get('status'), {'idle', 'working', 'running', 'starting', 'stopping', 'stopped', 'error', 'ready'}, 'other'),
            'kind': 'internal' if session.get('sessionKind') == 'internal' else ('worker' if session.get('sessionKind') == 'worker' or session.get('nativeParentId') else 'root'),
            'historyLoaded': _boolean(session.get('historyLoaded')), 'historyLoading': bool(session.get('historyLoading')),
            'historyError': bool(session.get('historyError')), 'runtimeError': bool(session.get('error')),
            'workspaceAvailable': _boolean(session.get('workspaceAvailable')), 'messages': len(_rows(session.get('messages'))),
            'retainedMessages': _count(session.get('sharedHistoryTotal')), 'toolNodes': len(_rows(_object(session.get('execution')).get('nodes'))),
            'workerStatuses': statuses, 'pendingApprovals': sum(_object(r).get('status') in (None, 'pending') for r in _rows(session.get('approvals')))},
        'presentation': {'appearance': _enum(view.get('scheme'), {'dark', 'light', 'system'}, 'system'),
            'skinFingerprint': hashlib.sha256(str(_object(state.get('theme')).get('css', '')).encode()).hexdigest()[:16],
            'canvasKind': _enum(_object(state.get('canvas')).get('kind'), {'html', 'markdown', 'text', 'image', 'code', 'json', 'jsonl', 'mermaid', 'dot', 'mcp-app', 'browser', 'a2ui', 'babylon'}, None)},
        'reportedOnlineViews': sum(_object(row).get('online') is True for row in _object(state.get('devices')).values())}
    if device:
        result['device'] = device
    return result


def markdown(facts):
    summary = facts.get('troubleshooting', {})
    failure = summary.get('recordedFailure', {})
    device = facts.get('device', {})
    # Only fixed/allowlisted facts feed the visible summary; JSON remains complete.
    lines = [f"App {facts.get('appVersion', 'unknown')} · {facts.get('osFamily', 'unknown')} host",
             f"Recorded failure: {failure.get('category', 'unknown')} · input relation: {failure.get('inputRelation', 'unknown')}",
             f"Latest input delivery: {summary.get('latestInputDelivery', 'unknown')} · activity: {summary.get('activityPhase', 'unknown')}",
             f"Browser event stream: {device.get('eventStream', 'unknown')}"]
    return ('\n\n### Reproduction diagnostics\n\n' + '\n\n'.join(lines)
            + '\n\n<details>\n<summary>Complete sanitized diagnostics</summary>\n\n```json\n'
            + json.dumps(facts, indent=2, sort_keys=True) + '\n```\n</details>')
