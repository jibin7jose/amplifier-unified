"""Moving-source policy and exact qualification for the managed DTU core.

A configuration's immutable commit is evidence, not an update policy. Only an
app-owned setup declaration or this updater's exact retained chain authorizes
following main. Custom configurations and explicit pins remain owner-managed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from filelock import FileLock

from . import test_environment_setup as private

POLICY_VERSION = 1
KIND = 'managed-dtu-core'


class RunnerUpdateError(ValueError):
    def __init__(self, reason='managed-runner-qualification-failed'):
        super().__init__('The test runner update was not selected. The existing runner and its work are unchanged.')
        self.diagnostic_facts = {'reason': reason}


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def supported_private_files():
    return all(hasattr(os, name) for name in ('getuid', 'O_NOFOLLOW', 'O_DIRECTORY'))


def root(app, create=True):
    if not supported_private_files():
        raise ValueError('Automatic test runner updates are not supported on this platform.')
    path = Path(app.data_dir) / 'managed-runner-updates'
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('The managed runner update directory changed.')
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Runner updates require a private owned directory.')
    return path


def raw_configuration(app):
    return private.read_json(Path(app.data_dir) / 'test-environments.json')


def record_path(app, identity):
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
        raise ValueError('Invalid runner update identity.')
    return root(app) / (identity + '.json')


def setup_policy(app, raw):
    """Legacy migration requires the actual new-install setup receipt."""
    if not raw:
        return None
    from .managed_runner_binding import selection
    carried = selection(root(app, False), raw, private.read_json)
    if carried:
        policy = carried.get('policy')
        if isinstance(policy, dict):
            return policy
    folder = private.root(app, create=False)
    if not folder.exists():
        return None
    for path in folder.glob('*.json'):
        if not re.fullmatch('[a-f0-9]{32}\\.json', path.name):
            continue
        value = private.read_json(path) or {}
        if (value.get('state') == 'completed' and execution_binding(value.get('candidateConfiguration', {})) == execution_binding(raw)
                and 'priorConfiguration' in value and value['priorConfiguration'] is None
                and value.get('installationId') == raw.get('installationId')
                and value.get('proof', {}).get('probeRemoved') is True):
            policy = value.get('runnerSourcePolicy')
            if policy is not None:
                return policy if isinstance(policy, dict) else None
            from .task_environments import QUALIFIED_DTU_COMMITS, REPOSITORY
            if raw.get('commit') in QUALIFIED_DTU_COMMITS:
                return {'version': POLICY_VERSION, 'repository': REPOSITORY, 'ref': 'main',
                        'origin': 'app-setup', 'setupId': value['id']}
    return None


def update_sources(app):
    from .task_environments import REPOSITORY
    from .updates import pinned
    # Availability inventory must remain safe on native Windows, including an
    # app that has never configured a test runner. POSIX private-file and owner
    # guarantees are currently required for automatic generation promotion.
    if not supported_private_files():
        if not (Path(app.data_dir) / 'test-environments.json').exists():
            return []
        return [{'id': 'managed-runner:dtu', 'kind': 'smart tool', 'runnerKind': KIND,
                 'label': 'Managed test runner core', 'updateTier': 'other',
                 'url': REPOSITORY, 'ref': '', 'current': None, 'eligible': False,
                 'status': 'not_checked', 'usage': 'configured',
                 'detail': 'Automatic test runner updates are not supported on this platform. The configured runner is unchanged; its version has not been checked.'}]
    raw = raw_configuration(app)
    if not raw or not raw.get('enabled'):
        return []
    item = next((item for item in app.smart_tools.state['installations']
                 if item['id'] == raw.get('installationId')), None)
    if not item:
        return []
    policy = setup_policy(app, raw)
    eligible = bool(policy and policy.get('repository') == REPOSITORY and policy.get('version') == POLICY_VERSION
                    and isinstance(policy.get('ref'), str) and not pinned(policy['ref']))
    return [{'id': 'managed-runner:dtu', 'installationId': item['id'], 'kind': 'smart tool',
             'runnerKind': KIND, 'label': 'Managed test runner core', 'updateTier': 'other',
             'url': REPOSITORY, 'ref': policy.get('ref', '') if policy else '',
             'current': raw.get('commit'), 'eligible': eligible,
             'status': 'not_checked' if eligible else 'pinned' if policy else 'local',
             'usage': 'configured', 'usageEvidence': ['Isolated test source snapshots and execution'],
             'runnerSelection': key(raw), 'runnerPolicy': policy,
             'previousInstallationId': rollback_target(app, raw),
             'detail': ('New versions are tested on this configured machine before future checks use them.'
                        if eligible else 'This runner has an explicit or owner-managed source; automatic updates do not change it. Its version has not been checked.')}]


def runtime_identity(installation):
    python = installation['python']
    result = subprocess.run([python, '-I', '-B', str(Path(__file__).with_name('managed_runner_identity.py'))],
                            env={'PATH': os.defpath, 'HOME': str(Path(python).parent.parent), 'LANG': 'C.UTF-8'},
                            capture_output=True, timeout=120, check=True)
    if len(result.stdout) > 4096:
        raise RunnerUpdateError()
    value = json.loads(result.stdout)
    if (value.get('version') != 1 or not re.fullmatch('[a-f0-9]{64}', value.get('runtimeSha256', ''))
            or not isinstance(value.get('files'), int) or not value['files']):
        raise RunnerUpdateError()
    return value


def installation(app, raw):
    from .task_environments import REPOSITORY
    item = next((row for row in app.smart_tools.state['installations'] if row['id'] == raw.get('installationId')), None)
    if (not item or item.get('status') != 'installed' or item.get('commit') != raw.get('commit')
            or item.get('repository', '').removesuffix('.git') != REPOSITORY or item.get('extras')):
        raise RunnerUpdateError()
    return item


def qualification_path(home, config):
    return Path(home) / 'managed-runner-updates' / (
        'qualified-' + config['installationId'] + '-' + config['commit'] + '-'
        + key(execution_binding(config)) + '.json')


def trusted_installation(app, raw):
    """Exact private behavioral proof, not a version/schema allow-list."""
    installation(app, raw)
    from .task_environments import validate_configuration_value
    normalized, status, _ = validate_configuration_value(app, raw, _qualification=True)
    if status != 'ready':
        return False
    value = private.read_json(qualification_path(app.data_dir, normalized))
    return bool(value and value.get('version') == 1
        and value.get('proof', {}).get('probeRemoved') is True
        and value.get('proof', {}).get('artifactVerified') is True
        and value.get('proof', {}).get('checkPassed') is True
        and execution_binding(normalized) == execution_binding(value.get('normalized', {})))


def intent_id(source):
    return key([source['runnerSelection'], source['latest']])[:32]


async def stage(app, source):
    from . import task_environments as environments
    raw = raw_configuration(app)
    if (not raw or key(raw) != source.get('runnerSelection') or raw.get('installationId') != source['installationId']
            or setup_policy(app, raw) != source.get('runnerPolicy')):
        raise RunnerUpdateError('managed-runner-selection-changed')
    policy = source['runnerPolicy']
    from .updates import pinned
    if not policy or pinned(policy.get('ref', '')) or policy.get('repository') != environments.REPOSITORY:
        raise RunnerUpdateError('managed-runner-selection-changed')
    revision = source.get('latest')
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision):
        raise RunnerUpdateError()
    identity = intent_id(source)
    path = record_path(app, identity)
    record = private.read_json(path)
    if record is None:
        record = {'version': 1, 'id': identity, 'previous': raw, 'policy': policy,
                  'revision': revision, 'state': 'installing', 'createdAt': time.time()}
        private.write_json(path, record)
    elif record.get('previous') != raw or record.get('revision') != revision or record.get('policy') != policy:
        raise RunnerUpdateError()
    if record.get('candidate') and record.get('normalized') and record.get('runtime'):
        return {'id': record['candidate']['installationId'], 'runnerUpdate': identity}
    from .test_capacity_settings import execution_key
    previous_normalized, previous_status, _ = environments.validate_configuration_value(app, raw)
    if previous_status != 'ready' or not previous_normalized:
        raise RunnerUpdateError()
    target = await app.smart_tools.install({'repository': environments.REPOSITORY, 'ref': revision,
                                           'path': '.', 'extras': [], '_runner_qualification': True})
    if target.get('commit') != revision:
        raise RunnerUpdateError()
    candidate = {**raw, 'installationId': target['id'], 'commit': revision,
                 'runnerQualificationRequired': True,
                 'capacityPolicyId': execution_key(previous_normalized)}
    # Retain the exact installed candidate before validation/hash can fail.
    # Skip can then remove only this unused install through normal owner guards.
    record.update(candidate=candidate)
    private.write_json(path, record)
    # This validation checks paths/configuration, but is private qualification
    # staging only. The candidate is never published here.
    normalized, status, _ = environments.validate_configuration_value(app, candidate, _qualification=True)
    if status != 'ready' or not normalized:
        raise RunnerUpdateError()
    record.update(candidate=candidate, normalized=normalized, runtime=await asyncio.to_thread(runtime_identity, target),
                  state='qualifying')
    private.write_json(path, record)
    return {'id': target['id'], 'runnerUpdate': identity}


async def advance(app, identity):
    from . import managed_runner_qualification as qualification
    from . import task_environments as environments
    path = record_path(app, identity)
    # Use a separate lock, never app.lock or the all-chat idle barrier.
    with FileLock(str(path.with_suffix('.lock')), timeout=0):
        record = private.read_json(path)
        if not record or not record.get('candidate'):
            raise RunnerUpdateError()
        selected = raw_configuration(app)
        if selected == record['candidate'] and trusted_installation(app, selected):
            if await asyncio.to_thread(runtime_identity, installation(app, selected)) != record['runtime']:
                raise RunnerUpdateError('managed-runner-package-changed')
            record.update(state='selected', selectedAt=record.get('selectedAt', time.time()))
            private.write_json(path, record)
            from .storage_cleanup import request_maintenance
            request_maintenance(app)
            return {'complete': True}
        config = record['normalized']
        changed = (selected != record['previous'] or
                   setup_policy(app, record['previous']) != record['policy'] or
                   record.get('selectionChanged'))
        if changed:
            if 'start' in record.get('effects', {}):
                if await asyncio.to_thread(runtime_identity, installation(app, record['candidate'])) != record['runtime']:
                    raise RunnerUpdateError('managed-runner-package-changed')
            # A disabled/replaced runner must not start more work. Only settle
            # the original dispatch and preserve/clean its already-owned guest.
            state = await private.settled_thread(qualification.cancel, app, path, record, config)
            if state != 'failed':
                return {'complete': False, 'waiting': state,
                        'detail': 'The runner selection changed. Its original check is being reconciled and cleaned up.'}
            record.update(state='superseded')
            private.write_json(path, record)
            raise RunnerUpdateError('managed-runner-selection-changed')
        action = {'prepare': 'prepare', 'start': 'start', 'check': 'job_start',
                  'stop': 'stop', 'remove': 'remove'}.get(record.get('probeState', 'prepare'))
        if action and action not in record.get('effects', {}):
            if await asyncio.to_thread(runtime_identity, installation(app, record['candidate'])) != record['runtime']:
                raise RunnerUpdateError('managed-runner-package-changed')
        before = (record.get('probeState'), len(record.get('effects', {})))
        state = await private.settled_thread(qualification.advance, app, path, record, config)
        progressed = before != (record.get('probeState'), len(record.get('effects', {})))
        if state in {'waiting', 'unknown'}:
            return {'complete': False, 'waiting': state, 'progressed': progressed,
                    'detail': ('Waiting for the existing test runner capacity or qualification result.'
                               if state == 'waiting' else 'The test runner check has an unconfirmed result. Its exact owner and evidence are retained; no check is repeated.')}
        if state != 'qualified' or not record.get('proof'):
            raise RunnerUpdateError()
        runtime = await asyncio.to_thread(runtime_identity, installation(app, record['candidate']))
        if runtime != record['runtime']:
            raise RunnerUpdateError('managed-runner-package-changed')
        # Same physical setup lock/CAS protects concurrent user setup. Limits
        # remain whatever was frozen in this candidate; a changed config wins.
        with FileLock(str(private.root(app) / 'setup.lock'), timeout=0):
            if raw_configuration(app) != record['previous'] or setup_policy(app, record['previous']) != record['policy']:
                raise RunnerUpdateError('managed-runner-selection-changed')
            proof = {'version': 1, 'configuration': record['candidate'], 'runtime': runtime,
                     'proof': record['proof'], 'normalized': record['normalized'], 'updateId': identity}
            private.write_json(qualification_path(app.data_dir, record['normalized']), proof)
            # Exact old/new configurations remain retained independently of the
            # selected file, including rollback and owners admitted before CAS.
            for config_raw in (record['previous'], record['candidate']):
                private.write_json(root(app) / ('selection-' + key(config_raw) + '.json'),
                                   {'configuration': config_raw, 'policy': record['policy'], 'updateId': identity})
            private.retain_verification(app, record['candidate'], record['proof'])
            private.write_json(Path(app.data_dir) / 'test-environments.json', record['candidate'])
            record.update(state='selected', selectedAt=time.time())
            private.write_json(path, record)
        from .storage_cleanup import request_maintenance
        request_maintenance(app)
        return {'complete': True}


def referenced(home, identity):
    """Keep current, one rollback, and unsettled candidates; owners add a guard.

    Historical receipts remain evidence but do not indefinitely pin their code.
    remove_installation independently checks all live resource owners under its
    installation lock before deleting any unreferenced installation.
    """
    if not supported_private_files():
        return False
    path = Path(home) / 'managed-runner-updates'
    selected = private.read_json(Path(home) / 'test-environments.json') or {}
    if selected.get('installationId') == identity:
        return True
    if not path.exists():
        return False
    from .managed_runner_binding import rollback_record
    predecessor = rollback_record(path, selected, private.read_json)
    for item in path.glob('*.json'):
        if not re.fullmatch(r'[a-f0-9]{32}\.json', item.name):
            continue
        record = private.read_json(item) or {}
        rollback = predecessor is not None and record == predecessor
        unsettled = (record.get('state') not in {'selected', 'skipped', 'superseded'}
                     and record.get('probeState') != 'failed')
        values = ([record.get('previous')] if rollback else [])
        if unsettled:
            values += [record.get('previous'), record.get('candidate')]
        if any(isinstance(value, dict) and value.get('installationId') == identity for value in values):
            return True
    return False


async def retire(app, *, inspect_only=False, selected=None, evidence=None, inspection=None):
    """Bounded retirement of purpose-installed code, never user data/evidence."""
    from .task_environments import REPOSITORY
    if not supported_private_files():
        return
    folder = root(app, False)
    if not folder.exists():
        return
    known, records, candidates = set(), [], []
    for path in folder.glob('*.json'):
        if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
            continue
        record = private.read_json(path) or {}
        records.append(record)
        if record.get('state') in {'selected', 'skipped', 'superseded'} or record.get('probeState') == 'failed':
            known.update(value.get('installationId') for name in ('previous', 'candidate')
                         if isinstance(value := record.get(name), dict))
    if inspection is not None:
        inspection.update(protectedIds=[], complete=True)
    attempts = 0
    items = sorted(app.smart_tools.state['installations'], key=lambda item:item['id'])
    cursor = getattr(app, '_runner_retention_cursor', '')
    items = [item for item in items if item['id'] > cursor] + [item for item in items if item['id'] <= cursor]
    for item in items:
        if (item.get('runnerCandidate') is not True or item.get('id') not in known
                or item.get('repository', '').removesuffix('.git') != REPOSITORY
                or referenced(app.data_dir, item['id'])):
            if inspection is not None and item.get('runnerCandidate'):
                inspection['protectedIds'].append(item['id'])
            continue
        if selected is not None and item['id'] not in selected:
            continue
        if attempts >= 8:
            if inspection is not None:
                inspection['complete'] = False
            break
        attempts += 1
        app._runner_retention_cursor = item['id']
        try:
            if inspect_only or selected is not None:
                from .storage_cleanup_runner import inspect
                candidate = await inspect(app, item, records)
                if inspect_only:
                    candidates.append(candidate)
                    continue
                loop = asyncio.get_running_loop()
                async def admit(candidate=candidate):
                    from .update_retention import fresh_references
                    references = await fresh_references(app.update_manager, timeout=2)
                    if any(path in references for path in candidate['paths']):
                        raise ValueError('This runner became used before removal.')
                    if evidence:
                        evidence('attempting', candidate)
                def cleanup_guard():
                    # Called by the canonical owner only after both install locks
                    # and the initial filesystem measurement. The removal owner
                    # measures again after this yielding guard. Policy and receipt
                    # writes stay on their owning event loop.
                    future = asyncio.run_coroutine_threadsafe(admit(), loop)
                    try:
                        future.result(timeout=5)
                    except BaseException:
                        future.cancel()
                        raise
            # Existing guards cover live owners, connections and selected code.
            await app.smart_tools.uninstall(item['id'], **({'cleanup_inventory': candidate, 'cleanup_guard': cleanup_guard} if selected is not None else {}))
            if evidence:
                evidence('completed', {'kind': 'runner', 'id': item['id']})
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
            if inspection is not None:
                inspection['protectedIds'].append(item['id'])
            # A retained owner is a deferral, not update failure. A later
            # availability check retries under the same deletion guards.
            pass
    if inspect_only:
        return candidates


async def skip(app, identity):
    if not identity:
        return
    record = private.read_json(record_path(app, identity))
    if record and record.get('resourceId') and record.get('probeState') not in {'qualified', 'failed'}:
        raise RunnerUpdateError('managed-runner-qualification-failed')
    if record:
        record['state'] = 'skipped'
        private.write_json(record_path(app, identity), record)
        candidate = record.get('candidate') or {}
        item = next((row for row in app.smart_tools.state['installations']
                     if row.get('id') == candidate.get('installationId')), None)
        if (item and item.get('runnerCandidate') is True and item.get('commit') == record['revision']
                and not referenced(app.data_dir, item['id'])):
            await app.smart_tools.uninstall(item['id'])


def rollback_target(app, raw):
    from .managed_runner_binding import rollback_record
    record = rollback_record(root(app, False), raw, private.read_json)
    if record:
        return record['previous']['installationId']
    return None


def rollback(app, identity):
    if not supported_private_files():
        return False
    from .task_environments import validate_configuration_value
    raw = raw_configuration(app)
    if not raw or raw.get('installationId') != identity:
        return False
    previous = rollback_target(app, raw)
    if not previous:
        return False
    from .managed_runner_binding import rollback_record
    record = rollback_record(root(app, False), raw, private.read_json)
    restored = record['previous']
    config, status, _ = validate_configuration_value(app, restored)
    if not config or status != 'ready':
        raise RunnerUpdateError()
    validate_execution(app.data_dir, config)
    with FileLock(str(private.root(app) / 'setup.lock'), timeout=0):
        if raw_configuration(app) != raw:
            raise RunnerUpdateError('managed-runner-selection-changed')
        private.write_json(Path(app.data_dir) / 'test-environments.json', restored)
    return True


def validate_execution(home, config):
    from .task_environments import QUALIFIED_DTU_COMMITS
    if not config.get('runnerQualificationRequired') and (not config.get('commit') or config.get('commit') in QUALIFIED_DTU_COMMITS):
        return
    value = private.read_json(qualification_path(home, config))
    if not value or execution_binding(config) != execution_binding(value.get('normalized', {})) or runtime_identity(config) != value.get('runtime'):
        raise RunnerUpdateError('managed-runner-package-changed')


def execution_binding(config):
    from .managed_runner_binding import execution_binding as binding
    return binding(config)
