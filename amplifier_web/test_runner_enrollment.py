"""Connect-and-check orchestration on the existing test scheduler and owners.

Registration is a settings request, not a chat or an alternative test queue.
The existing qualification helper owns the tiny check; the fixed remote
controller journals every actual effect. Passive Settings reads never connect.
"""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import re
import time
import uuid

from filelock import FileLock, Timeout

from . import registered_test_runners as runners
from . import managed_runner_qualification as qualification
from . import test_execution_settings as execution
from . import test_environment_profiles as profiles
from . import test_origin_files as files
from .test_environment_setup import settled_thread

TERMINAL = {'connected', 'failed', 'cancelled', 'changed'}
MESSAGES = {
    'connecting': 'Checking the saved connection and the machine’s testing setup.',
    'prepare': 'Sending a small, fixed test folder. Your work and credentials are not copied.',
    'capacity': 'Waiting for room on this test machine. Existing work can continue.',
    'start': 'Starting the small connection check within this machine’s limits.',
    'check': 'Checking isolation, execution and the returned result.',
    'stop': 'Stopping this connection check.',
    'remove': 'Retaining its result and removing its test environment.',
    'unknown': 'The last operation is not yet confirmed. Its original owner is retained; the check is not repeated.',
    'paused': 'Connection check saved. It will continue after the app update or restart; no new operation is starting.',
    'connected': 'Connection checked. Its result was retained and its test environment was removed.',
    'failed': 'The connection check did not pass. This request did not enable the machine.',
    'cancelled': 'Connection check cancelled. This request did not enable the machine.',
    'changed': 'The machine settings changed during this check. The checked result is retained; no settings were replaced.',
}


class AdmissionDeferred(Exception):
    """A saved owner remains valid, but this host may not admit new effects."""


def suspended(app, queue=None):
    from .updates import work_paused
    return (getattr(app, 'closed', False) or getattr(queue or app.test_queue, 'closing', False)
            or work_paused(getattr(app, 'state', {})))


async def admission(app, identity, task):
    # Run authority checks on the host loop, where the settings database lives.
    # This is the admission point for one bounded operation, not a lease for all
    # subsequent steps. A cancelled coroutine cannot authorize its live thread.
    async with app.lock:
        if suspended(app) or task.cancelling():
            raise AdmissionDeferred()
        record = read(app, identity)
        try:
            app.configuration_authority.check(record['authority'])
        except Exception:
            request_cancel(app, identity)
        if cancelled(app, identity):
            raise AdmissionDeferred()


def folder(app, *, create=False):
    path = Path(app.data_dir) / 'test-runner-enrollments'
    if create:
        with files.directory(path, create=True, private=True):
            pass
    return path


def path(app, identity):
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('Choose the saved connection check.')
    return folder(app) / (identity + '.json')


def read(app, identity):
    value = files.read(path(app, identity), 1024 * 1024)
    if value.get('version') != 1 or value.get('id') != identity or value.get('purpose') != 'runner-enrollment':
        raise ValueError('The saved connection check is invalid.')
    return value


def public(record):
    return {**{key: record[key] for key in ('id', 'requestId', 'name', 'connection', 'configPath', 'status', 'createdAt', 'updatedAt')},
            'detail': record.get('detail') or MESSAGES.get(record['status'], MESSAGES['prepare']),
            'canCancel': record['status'] not in TERMINAL,
            'canReconcile': record['status'] == 'unknown'}


def listing(app, value=None):
    value = value or runners.store(app)
    return [public(read(app, identity)) for identity in value.get('enrollments', [])]


def cancel_marker(app, identity):
    return path(app, identity).with_suffix('.cancel.json')


def cancelled(app, identity):
    try:
        return files.read(cancel_marker(app, identity)).get('cancelRequested') is True
    except FileNotFoundError:
        return False


def request_cancel(app, identity):
    record = read(app, identity)
    if record['status'] not in TERMINAL:
        files.replace(cancel_marker(app, identity), {'cancelRequested': True})


async def request(app, action, args, authority):
    """Commit the settings intent before installing or dispatching any work."""
    from .errors import AppError
    async with app.lock:
        app.configuration_authority.check(authority)
        if action.endswith(('.cancel', '.reconcile')):
            record = read(app, args['id'])
            if action.endswith('.cancel'):
                request_cancel(app, record['id'])
            # Reconcile is a wake-up of the same owner, never a fresh check.
            app.test_queue.backoff.pop('enrollment:' + record['id'], None)
        else:
            request_id = args.get('requestId') or uuid.uuid4().hex
            identity = uuid.uuid5(uuid.NAMESPACE_URL, 'test-runner-enrollment:' + action + ':' + request_id).hex
            intent = {key: value for key, value in args.items() if key not in {'expectedRevision', 'requestId'}}
            try:
                previous = read(app, identity)
            except FileNotFoundError:
                previous = None
            if previous:
                if previous['intent'] != intent or previous['authority'] != authority:
                    raise AppError('This connection request already has different settings.', 409)
                value = runners.store(app)
                if identity not in value.get('enrollments', []):
                    # Recover the receipt-before-index publication boundary.
                    existing = value.setdefault('enrollments', [])
                    if len(existing) >= 32:
                        raise ValueError('Refresh the saved connection check list before continuing.')
                    existing.append(identity)
                    runners.publish(app, value)
            else:
                if suspended(app):
                    raise AppError('The app is updating or closing. Connect the test machine after it finishes.', 409)
                value = runners.store(app)
                if runners.digest(value) != args['expectedRevision']:
                    raise AppError('Test machines changed. Refresh before connecting.', 409)
                prior = next((r for r in value['runners'] if r['id'] == args.get('id')), None)
                if action.endswith('.refresh') and prior is None:
                    raise ValueError('Choose a registered test machine.')
                settings = prior or args
                name, connection = settings['name'].strip(), settings['connection']
                if not 1 <= len(name) <= 80 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,199}', connection):
                    raise ValueError('Give the machine a short name and choose an existing SSH connection without command options.')
                existing = [read(app, item) for item in value.get('enrollments', [])]
                active = [item for item in existing if item['status'] not in TERMINAL]
                if any(item['connection'] == connection for item in active):
                    raise ValueError('This connection already has an unfinished check. Inspect or cancel that check first.')
                if len(active) >= 16 or len(value['runners']) >= 16 and prior is None:
                    raise ValueError('At most 16 test machines can be connected at a time.')
                queue, resource, timing = execution.accepted(app, 'linux-python')
                resource = profiles.freeze('linux-python', {**resource, 'cpus': min(1, resource['cpus']), 'memoryMiB': min(512, resource['memoryMiB'])})
                now = time.time()
                record = {'version': 1, 'purpose': 'runner-enrollment', 'id': identity, 'requestId': request_id,
                          'intent': intent, 'authority': authority, 'prior': copy.deepcopy(prior),
                          'name': name, 'connection': connection, 'configPath': settings.get('configPath', ''),
                          'status': 'connecting', 'createdAt': now, 'updatedAt': now,
                          'expiresAt': now + queue['defaultWaitSeconds'] if queue['defaultWaitSeconds'] else None,
                          'resourceRequest': resource, 'timingRequest': timing,
                          'checkTimeoutSeconds': min(30, timing['maxTimeoutSeconds'])}
                folder(app, create=True)
                files.publish(path(app, identity), record)
                # Bounded index, immutable older evidence remains on disk.
                value['enrollments'] = [item['id'] for item in (active + [r for r in existing if r['status'] in TERMINAL][-15:])] + [identity]
                runners.publish(app, value)
    app.test_queue.wakeup.set()
    app.test_queue.start()
    return {'accepted': True, 'result': runners.snapshot(app)}


def step(app, identity, guard=None):
    """One bounded existing qualification step under its original owner lock."""
    record_path = path(app, identity)
    with FileLock(str(record_path.with_suffix('.lock')), timeout=0):
        record = read(app, identity)
        if record['status'] in TERMINAL or record.get('probeState') == 'qualified':
            return record
        stopped = cancelled(app, identity)
        if (record.get('expiresAt') is not None and record['expiresAt'] <= time.time()
                and not record.get('effects', {}).get('start')):
            stopped = True
            record['detail'] = 'The connection check expired while waiting. This request did not enable the machine.'
        if stopped and not record.get('runner'):
            qualification.save(record_path, record, status='cancelled')
            return record
        if not record.get('runner'):
            if guard:
                guard()
            # Content-addressed controller installation is idempotent. The
            # resulting exact identity is saved before any guest is allocated.
            candidate = runners.register(record['connection'], record['name'], record['configPath'])
            candidate['enabled'] = False
            prior = record.get('prior')
            if prior and prior['id'] != candidate['id']:
                qualification.save(record_path, record, status='failed', detail='This connection now identifies a different machine, account or backend. The saved machine was not replaced.')
                return record
            qualification.save(record_path, record, runner=candidate, status='prepare')
            return record
        config = runners.configuration(app, None, record['runner'])
        previous = record.get('probeState')
        result = (qualification.cancel(app, record_path, record, config) if stopped
                  else qualification.advance(app, record_path, record, config, admission=guard))
        status = ('cancelled' if stopped else 'failed') if result == 'failed' else 'unknown' if result == 'unknown' else record.get('probeState', 'prepare')
        qualification.save(record_path, record, status=status,
                           progressed=record.get('probeState') != previous,
                           detail=MESSAGES.get(status, MESSAGES['prepare']))
        return record


async def finish(app, record):
    """Publish only this qualified target; unrelated settings edits are retained."""
    async with app.lock:
        value = runners.store(app)
        current = next((r for r in value['runners'] if r['id'] == record['runner']['id']), None)
        if current and current.get('lastCheck', {}).get('requestId') == record['id']:
            # Publication is already committed. A later cancel/revocation is
            # not a rollback; observe it without republishing or re-enabling.
            detail = ('Connection check completed and was saved. Use Pause on this machine to prevent new checks.'
                      if current['enabled'] else 'Connection check completed and was saved. This machine is paused.')
            qualification.save(path(app, record['id']), record, status='connected', detail=detail)
            return
        if suspended(app):
            qualification.save(path(app, record['id']), record, status='paused', detail=MESSAGES['paused'])
            return
        try:
            app.configuration_authority.check(record['authority'])
        except Exception:
            request_cancel(app, record['id'])
        if cancelled(app, record['id']):
            qualification.save(path(app, record['id']), record, status='cancelled', detail=MESSAGES['cancelled'])
            return
        prior = record.get('prior')
        runner = {**record['runner'], 'enabled': current['enabled'] if current else True,
                  'lastCheck': {'checkedAt': time.time(), 'status': 'passed', 'requestId': record['id'],
                                'profileId': 'linux-python', 'proof': record['proof']}}
        if (prior is None and current is not None) or (prior is not None and
                (current is None or {k:v for k,v in prior.items() if k != 'enabled'} != {k:v for k,v in current.items() if k != 'enabled'})):
            qualification.save(path(app, record['id']), record, status='changed', detail=MESSAGES['changed'])
            return
        elif current:
            value['runners'][value['runners'].index(current)] = runner
            runners.publish(app, value)
        elif len(value['runners']) < 16:
            value['runners'].append(runner)
            runners.publish(app, value)
        else:
            qualification.save(path(app, record['id']), record, status='changed', detail=MESSAGES['changed'])
            return
        qualification.save(path(app, record['id']), record, status='connected', detail=MESSAGES['connected'])


async def run(queue, identity):
    app = queue.app
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    def guard():
        asyncio.run_coroutine_threadsafe(admission(app, identity, task), loop).result()
    try:
        for _ in range(10):
            if suspended(app, queue):
                raise AdmissionDeferred()
            record = read(app, identity)
            try:
                app.configuration_authority.check(record['authority'])
            except Exception:
                request_cancel(app, identity)
            record = await settled_thread(step, app, identity, guard)
            if record.get('probeState') == 'qualified':
                await finish(app, record)
                queue.wakeup.set()
                return
            if record['status'] in TERMINAL:
                return
            if not record.get('progressed') and record['status'] != 'prepare':
                queue.backoff['enrollment:' + identity] = (time.time() + (30 if record['status'] == 'unknown' else 5), 5)
                return
        queue.wakeup.set()
    except AdmissionDeferred:
        record = read(app, identity)
        if suspended(app, queue):
            qualification.save(path(app, identity), record, status='paused', detail=MESSAGES['paused'])
        # Cancellation/revocation is cleaned by the same saved owner on the
        # next scheduler pass. No new installation, launch or check was admitted.
        queue.wakeup.set()
    except Timeout:
        pass  # The original in-flight owner still holds the lock.
    except Exception as error:
        # A retained guest/effect remains recoverable. Do not call an uncertain
        # result a failed check or silently install/launch another one.
        record = read(app, identity)
        state = 'unknown' if record.get('resourceId') else 'failed'
        qualification.save(path(app, identity), record, status=state,
            detail=MESSAGES[state] if state == 'unknown' else str(error) if isinstance(error, runners.ConnectionFailure) else
            'The machine could not be connected. Check its saved SSH connection and set up testing in that machine’s Amplifier Settings first.',
            errorType=type(error).__name__)
        queue.backoff['enrollment:' + identity] = (time.time() + 30, 5)


def schedule(queue):
    """Share the existing host test scheduler, without blocking ordinary jobs."""
    if suspended(queue.app, queue):
        return
    for identity in runners.store(queue.app).get('enrollments', []):
        key = 'enrollment:' + identity
        if key in queue.tasks or queue.backoff.get(key, (0, 0))[0] > time.time():
            continue
        try:
            active = read(queue.app, identity)['status'] not in TERMINAL
        except (OSError, ValueError, KeyError, TypeError):
            continue  # One damaged settings receipt cannot block ordinary checks.
        if active:
            queue.tasks[key] = asyncio.create_task(run(queue, identity))
