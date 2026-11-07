"""Prospective publishing body policy; Publisher retains storage and effect authority.

These facts do not expire or remove files. Immutable release records and request
receipts stay unchanged when a body is pinned or deliberately captured again.
"""
from datetime import datetime, timedelta


DEFAULTS = {'keepDays': 30, 'rollbackKeepDays': 30, 'rollbackKeepCount': 2}


def policy(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {*DEFAULTS, 'policyRevision'}:
        raise ValueError('Choose a publishing retention policy with known fields')
    result = {**DEFAULTS, **value}
    for key, minimum, maximum in [('keepDays', 1, 3650), ('rollbackKeepDays', 1, 3650),
                                  ('rollbackKeepCount', 0, 100), ('policyRevision', 0, 2**53-1)]:
        if key in result and (type(result[key]) is not int or not minimum <= result[key] <= maximum):
            raise ValueError('Choose valid publishing retention days, rollback count and policy revision')
    return result


def capture(release, settings, now, *, previous=None):
    """A new body generation, never a reinterpretation of an old release ID."""
    previous = previous or {}
    return {'id': release['id'], 'sessionId': release['sessionId'], 'siteId': release['siteId'], 'version': 1,
            'policy': settings, 'payloadGeneration': previous.get('payloadGeneration', 0) + 1,
            'materializedAt': now, 'revision': previous.get('revision', -1) + 1,
            'pinned': False, 'availability': 'available', 'unusedSince': now}


def projection(state, *, identity=False):
    fields = {'version', 'policy', 'payloadGeneration', 'materializedAt', 'revision', 'pinned',
              'availability', 'unusedSince', 'supersededAt', 'rollbackUntil', 'expiredAt',
              'retirementPhase', 'retirementRequestId'}
    if identity:
        fields |= {'id', 'sessionId', 'siteId'}
    return {key: value for key, value in state.items() if key in fields}


def available(state):
    return state is None or state.get('availability') == 'available'


def valid_projection(state):
    """Validate the bounded release metadata carried by historical receipts."""
    required = {'version', 'policy', 'payloadGeneration', 'materializedAt', 'revision', 'pinned', 'availability'}
    dates = {'materializedAt', 'unusedSince', 'supersededAt', 'rollbackUntil', 'expiredAt'}
    optional = {'retirementPhase', 'retirementRequestId'}
    if not isinstance(state, dict) or not required <= state.keys() or state.keys() - required - dates - optional:
        return False
    if type(state['version']) is not int or state['version'] != 1 or type(state['pinned']) is not bool:
        return False
    if any(type(state[key]) is not int or state[key] < minimum for key, minimum in [('revision', 0), ('payloadGeneration', 1)]):
        return False
    if not isinstance(state['availability'], str) or state['availability'] not in {'available', 'expired', 'unknown', 'retiring'}:
        return False
    if 'retirementPhase' in state and (not isinstance(state['retirementPhase'], str) or state['retirementPhase'] not in {'prepared', 'detached', 'held', 'complete', 'admission-required'}):
        return False
    if 'retirementRequestId' in state and (not isinstance(state['retirementRequestId'], str) or not 1 <= len(state['retirementRequestId']) <= 200):
        return False
    if state['availability'] == 'expired' and 'expiredAt' not in state:
        return False
    try:
        if policy(state['policy']) != state['policy']:
            return False
        for key in dates & state.keys():
            value = state[key]
            if not isinstance(value, str) or len(value) > 64 or datetime.fromisoformat(value).tzinfo is None:
                return False
    except (ValueError, TypeError):
        return False
    return True


def rollback_deadline(state, now):
    settings = state.get('policy') if state else None
    if settings is None:
        return None  # A legacy/durable body has no invented retention deadline.
    return (datetime.fromisoformat(now) + timedelta(days=settings['rollbackKeepDays'])).isoformat()


def rollback_candidates(site, states, now):
    """Current choices, separate from immutable previousReleaseId/history."""
    result = []
    for item in site.get('rollbackHistory', []):
        state = states[item['releaseId']]
        if not available(state):
            continue
        if (state and state.get('pinned')) or item['retainUntil'] is None or datetime.fromisoformat(item['retainUntil']) > datetime.fromisoformat(now):
            result.append(item['releaseId'])
    return result
