"""Mutable app-wide admission policy, separate from exact cleanup identities."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat

from . import test_host_resources as host_resources
from .private_policy_file import write as write_policy_file

LIMITS = {'maxRunning': 0, 'maxInstances': 0, 'minFreeBytes': 10 * 1024**3}
MAXIMUM = 2**53 - 1


def definitions(schema, string):
    limits = schema({key: {'type': 'integer', 'minimum': 0, 'maximum': MAXIMUM}
                     for key in LIMITS}, [])
    return {
        'testing.machine.read': ('Read shared local-machine test budgets and current reservations. Does not launch work.', schema()),
        'testing.machine.save': ('Change shared local-machine resource budgets for future test admissions across cooperating installations. Active work continues; zero disables a ceiling.', schema({'expectedRevision': string(64), 'limits': schema({key: {'type': 'integer', 'minimum': 0, 'maximum': MAXIMUM} for key in host_resources.FIELDS}, [])}, ['expectedRevision', 'limits'])),
        'testing.capacity.read': ('Read global test capacity settings and the selected runner. Passive; does not probe or launch environments.', schema()),
        'testing.capacity.save': ('Change global test admission limits, optionally overriding the selected runner. Zero disables that limit. Affects future admissions across workspaces on this app installation; never stops active work. Requires global host-defaults authority.', schema({
            'expectedRevision': string(64), 'defaults': limits,
            'runnerId': string(64), 'runnerOverrides': limits},
            ['expectedRevision', 'defaults'])),
    }


def execution_key(config):
    """Only capacity integers are mutable; endpoint/code/source/auth stay bound."""
    inherited = config.get('capacityPolicyId')
    if inherited:
        if not isinstance(inherited, str) or len(inherited) != 64 or any(c not in '0123456789abcdef' for c in inherited):
            raise ValueError('The retained capacity policy identity is invalid.')
        return inherited
    value = copy.deepcopy(config)
    for key in LIMITS:
        value.pop(key, None)
        if isinstance(value.get('incus'), dict):
            value['incus'].pop(key, None)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_limits(value):
    if not isinstance(value, dict) or set(value) - LIMITS.keys():
        raise ValueError('Choose only the supported test capacity limits.')
    if any(type(item) is not int or not 0 <= item <= MAXIMUM for item in value.values()):
        raise ValueError('Test capacity limits must be whole numbers of zero or more.')
    return dict(value)


def load(app):
    path = Path(app.data_dir) / 'test-capacity.json'
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Test capacity settings must be a regular file, not a link.')
        handle = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return {'version': 1, 'defaults': {}, 'runners': {}}
    with os.fdopen(handle, 'rb') as stream:
        info = os.fstat(stream.fileno())
        after = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(after.st_mode)
                or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)
                or (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino)
                or (hasattr(os, 'getuid') and info.st_uid != os.getuid())
                or (os.name != 'nt' and info.st_mode & 0o077)
                or info.st_nlink != 1 or info.st_size > 65536):
            raise ValueError('Test capacity settings must be a private regular file.')
        value = json.load(stream)
    if not isinstance(value, dict) or set(value) != {'version', 'defaults', 'runners'} or value['version'] != 1:
        raise ValueError('Test capacity settings have an unsupported format.')
    validate_limits(value['defaults'])
    if not isinstance(value['runners'], dict) or len(value['runners']) > 64:
        raise ValueError('The saved test runner settings exceed their supported bound.')
    for identity, limits in value['runners'].items():
        if len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('The saved test runner identity is invalid.')
        validate_limits(limits)
    return value


def effective(config, policy):
    baseline = config.get('incus', {}) if config.get('backend') == 'incus' else config
    overrides = policy['runners'].get(execution_key(config), {})
    values, sources = {}, {}
    for key, default in LIMITS.items():
        values[key] = overrides.get(key, policy['defaults'].get(key, baseline.get(key, default)))
        sources[key] = ('runner' if key in overrides else 'global' if key in policy['defaults']
                        else 'configured' if key in baseline else 'backend-default')
    return values, sources


def apply(app, config, *, _qualification=False):
    """Called at the existing guarded admission point; never mutates its input."""
    value = copy.deepcopy(config)
    limits, _ = effective(value, load(app))
    target = value['incus'] if value.get('backend') == 'incus' else value
    target.update(limits)
    if _qualification:
        # The fixed updater probe has its own exact owner and physical-host
        # reservation. Ordinary job counts must not prevent repairing a runner.
        # This is private call context, never an action argument or saved policy.
        target.update(maxRunning=0, maxInstances=0)
    return value


def snapshot(app):
    from .task_environments import configuration
    policy = load(app)
    config, status, reason = configuration(app)
    revision = hashlib.sha256(json.dumps([policy, config, status], sort_keys=True).encode()).hexdigest()
    limits, sources = effective(config, policy) if config else ({**LIMITS, **policy['defaults']}, {})
    identity = execution_key(config) if config else None
    return {'revision': revision, 'defaults': policy['defaults'], 'effective': limits,
            'sources': sources, 'runnerId': identity,
            'runnerOverrides': policy['runners'].get(identity, {}),
            'runner': {'status': status, 'backend': config.get('backend', 'docker') if config else None,
                       'description': reason},
            'scope': 'All workspaces on this Amplifier installation. Runner overrides apply only to its selected backend connection.',
            'limitsScope': 'Docker limits share its daemon; Incus limits share its daemon and project. Other installations, users and container systems may have different policies.'}


def write(app, policy):
    path = Path(app.data_dir) / 'test-capacity.json'
    write_policy_file(path, policy,
        size_error='The saved test capacity settings exceed their supported bound.')


async def dispatch(app, action, args, authority):
    from .errors import AppError
    try:
        async with app.lock:
            if action.startswith('testing.machine.'):
                if action.endswith('.save'):
                    app.configuration_authority.check(authority)
                    current = host_resources.save(args['expectedRevision'], args['limits'])
                    app.test_queue.backoff.clear()
                    app.test_queue.wakeup.set()
                    app.test_queue.start()
                else:
                    current = host_resources.snapshot()
                return {'accepted': True, 'result': current}
            current = snapshot(app)
            if action.endswith('.save'):
                app.configuration_authority.check(authority)
                if args['expectedRevision'] != current['revision']:
                    raise AppError('Testing settings or the selected runner changed. Review the current values before saving.', 409)
                policy = load(app)
                policy['defaults'] = validate_limits(args['defaults'])
                if 'runnerOverrides' in args or 'runnerId' in args:
                    if not current['runnerId'] or args.get('runnerId') != current['runnerId']:
                        raise AppError('The selected test runner changed. Review its current settings.', 409)
                    overrides = validate_limits(args.get('runnerOverrides', {}))
                    if overrides:
                        if current['runnerId'] not in policy['runners'] and len(policy['runners']) >= 64:
                            raise ValueError('Too many retained runner overrides. Remove an override before adding another.')
                        policy['runners'][current['runnerId']] = overrides
                    else:
                        policy['runners'].pop(current['runnerId'], None)
                write(app, policy)
                # Old queued/owned backend hashes are deliberately unchanged.
                app.test_queue.backoff.clear()
                app.test_queue.wakeup.set()
                app.test_queue.start()
                current = snapshot(app)
        return {'accepted': True, 'result': current}
    except (OSError, ValueError, TypeError) as exc:
        raise AppError(str(exc), 409) from None
