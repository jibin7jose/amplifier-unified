"""Global future-job execution policy; accepted jobs retain their own snapshot."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
from . import test_environment_profiles as profiles
from .private_policy_file import write as write_policy_file

PEER_INBOX_COUNTS = ('maxPendingPeerInputs', 'maxPendingPeerInputsPerSender',
                     'maxPendingPeerInputsPerAssignment')
CONCURRENCY_DEFAULTS = {'maxTaskChatsPerSource': 0, 'maxEnvironmentsPerChat': 0,
                        'maxOwnedResources': 0, 'maxOwnedResourcesPerChat': 0,
                        'maxResourceReservations': 0, 'maxResourceReservationsPerChat': 0,
                        **dict.fromkeys(PEER_INBOX_COUNTS, 0),
                        'maxPeerMessagesPerMinute': 0, 'maxPendingWatchesPerTarget': 0}

QUEUE_DEFAULTS = {'maxPending': 0, 'maxPendingPerChat': 0, 'defaultWaitSeconds': 0}
PROFILE_FIELDS = (*profiles.RESOURCE_FIELDS, *profiles.TIMING_FIELDS)


def definitions(schema, string):
    queue = schema({key: {'type': 'integer', 'minimum': 0,
                          'maximum': profiles.MAX_SECONDS} for key in QUEUE_DEFAULTS}, [])
    fields = schema({key: {'type': 'integer', 'minimum': 1,
                          'maximum': 1048576 if key in profiles.RESOURCE_FIELDS else profiles.MAX_SECONDS}
                     for key in PROFILE_FIELDS}, [])
    return {
        'testing.execution.read': ('Read global work concurrency, queue and profile defaults without starting work.', schema({}, [])),
        'testing.execution.save': ('Save global work concurrency, queue and test profile limits. Concurrency gates new admissions without stopping active work; accepted checks retain their resource and time budgets.', schema({
            'expectedRevision': string(64), 'queue': queue,
            'concurrency': schema({key: {'type': 'integer', 'minimum': 0, 'maximum': profiles.MAX_SECONDS} for key in CONCURRENCY_DEFAULTS}, []),
            'profiles': schema({item['id']: fields for item in profiles.catalog()}, [])}, ['expectedRevision', 'queue', 'profiles'])),
    }


def validate(value):
    if (not isinstance(value, dict) or not {'version', 'queue', 'profiles'} <= set(value)
            or set(value) - {'version', 'queue', 'profiles', 'concurrency'} or value['version'] != 1):
        raise ValueError('Test execution settings have an unsupported format.')
    concurrency = value.get('concurrency', {})
    if not isinstance(concurrency, dict) or set(concurrency) - CONCURRENCY_DEFAULTS.keys():
        raise ValueError('Choose supported work concurrency limits.')
    if any(type(item) is not int or not 0 <= item <= profiles.MAX_SECONDS
           for key, item in concurrency.items()):
        raise ValueError('Work concurrency limits must be nonnegative whole numbers; 0 means no count limit.')
    queue = value['queue']
    if not isinstance(queue, dict) or set(queue) - QUEUE_DEFAULTS.keys():
        raise ValueError('Choose supported queue limits.')
    for key, item in queue.items():
        if type(item) is not int or not 0 <= item <= profiles.MAX_SECONDS:
            raise ValueError('Queue limits must be nonnegative whole numbers; 0 means no count or waiting limit.')
    saved = value['profiles']
    if not isinstance(saved, dict) or set(saved) - {item['id'] for item in profiles.catalog()}:
        raise ValueError('Choose supported test profiles.')
    for identity, fields in saved.items():
        if not isinstance(fields, dict) or set(fields) - set(PROFILE_FIELDS):
            raise ValueError('Choose supported profile limits.')
        for key, item in fields.items():
            maximum = 1048576 if key in profiles.RESOURCE_FIELDS else profiles.MAX_SECONDS
            if type(item) is not int or not 1 <= item <= maximum:
                raise ValueError('Profile limits must be positive whole numbers within the supported numeric range.')
        profiles.resources(identity, profiles.freeze(identity, fields))
        profiles.freeze_timing(identity, fields)
    return {**value, 'concurrency': concurrency}


def load(app):
    path = Path(app.data_dir) / 'test-execution.json'
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Test execution settings must be a regular file, not a link.')
        handle = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return {'version': 1, 'queue': {}, 'profiles': {}, 'concurrency': {}}
    with os.fdopen(handle, 'rb') as stream:
        info = os.fstat(stream.fileno())
        after = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(after.st_mode)
                or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)
                or (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino)
                or (hasattr(os, 'getuid') and info.st_uid != os.getuid())
                or (os.name != 'nt' and info.st_mode & 0o077)
                or info.st_nlink != 1 or info.st_size > 65536):
            raise ValueError('Test execution settings must be a private regular file.')
        value = json.load(stream)
    return validate(value)


def concurrency(app_or_home):
    from types import SimpleNamespace
    app = app_or_home if hasattr(app_or_home, 'data_dir') else SimpleNamespace(data_dir=app_or_home)
    return {**CONCURRENCY_DEFAULTS, **load(app)['concurrency']}


def accepted(app, profile_id):
    policy = load(app)
    fields = policy['profiles'].get(profile_id, {})
    return ({**QUEUE_DEFAULTS, **policy['queue']}, profiles.freeze(profile_id, fields), profiles.freeze_timing(profile_id, fields))


def snapshot(app):
    policy = load(app)
    effective = []
    for item in profiles.catalog():
        fields = policy['profiles'].get(item['id'], {})
        effective.append({**item, 'preparationTimeoutSeconds': 1800, **fields})
    return {'revision': hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
            'queue': policy['queue'], 'profiles': policy['profiles'],
            'concurrency': policy['concurrency'], 'effectiveConcurrency': {**CONCURRENCY_DEFAULTS, **policy['concurrency']},
            'effectiveQueue': {**QUEUE_DEFAULTS, **policy['queue']}, 'effectiveProfiles': effective,
            'scope': 'All workspaces on this Amplifier installation. Queue and profile settings apply to new requests. Concurrency settings govern new admissions; lowering them never stops admitted work or discards retained owners.'}


def write(app, policy):
    path = Path(app.data_dir) / 'test-execution.json'
    write_policy_file(path, policy,
        size_error='The saved test execution settings exceed their supported bound.')


async def dispatch(app, action, args, authority):
    from .errors import AppError
    async with app.lock:
        try:
            current = snapshot(app)
            if action.endswith('.save'):
                app.configuration_authority.check(authority)
                if args['expectedRevision'] != current['revision']:
                    raise ValueError('Execution settings changed. Refresh before saving your edits.')
                value = validate({'version': 1, 'queue': args['queue'], 'profiles': args['profiles'],
                                  'concurrency': args.get('concurrency', current['concurrency'])})
                write(app, value)
                app.test_queue.backoff.clear()
                app.test_queue.wakeup.set()
                app.test_queue.start()
                previous = current['effectiveConcurrency']
                current = snapshot(app)
                effective = current['effectiveConcurrency']
                if any(previous[key] > 0 and (effective[key] == 0 or effective[key] > previous[key])
                       for key in PEER_INBOX_COUNTS):
                    app.collaboration.refresh_inboxes()
            return {'accepted': True, 'result': current}
        except (OSError, ValueError, TypeError) as exc:
            raise AppError(str(exc), 409) from None
