"""A new desired-state cleanup can follow an interrupted local cleanup.

The original receipt stays unknown. Under the existing environment lock, the
host proves the old local controller cannot issue more work, observes the exact
stopped owner, and admits a new preservation/removal request. Private phase
files, stale inventory and caller-supplied assertions are not executor proof.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

KIND = 'settled-local-cleanup'


def binding(row, original):
    if (not isinstance(original, dict)
            or original.get('action') not in {'workspace.environments.remove', 'workspace.environments.finishCleanup'}
            or original.get('state') != 'outcome_unknown'
            or original.get('resourceId') != row.get('id')
            or original.get('sessionId') != row.get('sessionId')
            or not isinstance(original.get('requestId'), str) or not original['requestId']
            or row.get('backendType') != 'docker' or row.get('backendAdmitted') is not True
            or row.get('backendConfigRetained') is not True):
        return None
    identity, snapshot, owner = row.get('backendIdentity'), row.get('snapshot'), row.get('owner')
    if (not isinstance(identity, dict) or set(identity) != {'backend', 'daemon'}
            or identity.get('backend') != 'docker' or not isinstance(identity.get('daemon'), str)
            or not identity['daemon'] or not isinstance(snapshot, dict)
            or not re.fullmatch(r'[a-f0-9]{64}', str(snapshot.get('archive_sha256', '')))
            or any(not re.fullmatch(r'[a-f0-9]{40}', str(snapshot.get(k, '')))
                   for k in ('snapshot_commit', 'snapshot_tree'))
            or not isinstance(owner, dict) or owner.get('sessionId') != row.get('sessionId')
            or not isinstance(owner.get('principal'), str) or not owner['principal']):
        return None
    fields = ('id', 'sessionId', 'workspaceId', 'backendKey', 'backendInstallation',
              'universeId', 'launchOperationId', 'source')
    if any(not isinstance(row.get(key), str) or not row[key] for key in fields):
        return None
    digest = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return {'version': 1, 'kind': KIND, 'originalRequestId': original['requestId'],
            'originalSha256': digest, 'resourceId': row['id'],
            **{key: row[key] for key in fields if key != 'id'},
            'backendIdentity': identity, 'snapshot': snapshot, 'owner': owner}


def unreadable_managed_executor(path):
    """Unrelated protected services are not local cleanup executors.

    Managed cleanup is a Python helper with synchronous Docker subprocesses.
    Unknown/oversized argv stays uncertain; an unreadable matching helper or
    command remains a hold. This never ignores a readable process in the owner
    directory merely because its executable has a different name.
    """
    try:
        with (path / 'cmdline').open('rb') as stream:
            command = stream.read(65537)
        if not command or len(command) > 65536:
            return True
        arguments = [os.fsdecode(value) for value in command.split(b'\0') if value]
        executable = Path(arguments[0]).name
        return (executable.startswith(('python', 'docker'))
                or executable in {'sh', 'bash', 'dash', 'env', 'timeout', 'uv', 'sudo', 'su'}
                or any(Path(value).name in {'test_environment_worker.py', 'incus_environment_worker.py'}
                       for value in arguments))
    except (OSError, ValueError, IndexError):
        return True


def local_executor_quiescent(directory, identities=(), *, proc=Path('/proc')):
    """Inspect same-account processes while the environment lock is held.

    Managed local helpers run with this exact cwd; their synchronous Docker
    children inherit it. Include descendants/other commands with that cwd, not
    just a helper filename. A disappearing parent may have left a child: scan
    newly appearing PIDs before declaring the local boundary settled.
    """
    directory = Path(directory)
    if not directory.is_absolute() or not directory.is_dir() or any(part.is_symlink() for part in (directory, *directory.parents)):
        return False
    markers = (str(directory), *(value for value in identities if isinstance(value, str) and value))
    seen = set()
    for _ in range(3):
        current = set()
        try:
            for path in proc.iterdir():
                if not path.name.isdecimal():
                    continue
                try:
                    if path.stat().st_uid != os.getuid():
                        continue
                    state = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                    if state[0] == 'Z':
                        continue
                    identity = (path.name, state[19])  # PID reuse is a new executor.
                    try:
                        cwd = os.readlink(path / 'cwd')
                    except PermissionError:
                        if unreadable_managed_executor(path):
                            return False
                        cwd = None
                    if cwd is not None and (cwd == str(directory) or cwd.startswith(str(directory) + '/')):
                        return False
                    # Compose/copy/removal children may change cwd. Their exact
                    # project, object or owner-path arguments still identify them.
                    with (path / 'cmdline').open('rb') as stream:
                        command = stream.read(65537)
                    if len(command) > 65536:
                        return False
                    arguments = [os.fsdecode(value) for value in command.split(b'\0') if value]
                    if any(marker in argument for marker in markers for argument in arguments):
                        return False
                    if (path / 'stat').read_text().rsplit(')', 1)[1].split()[19] != identity[1]:
                        return False
                    current.add(identity)
                except (FileNotFoundError, ProcessLookupError):
                    continue
            if current <= seen:
                return True
            seen = current
        except (OSError, ValueError, IndexError):
            return False
    return False


def qualify(app, row, original, config, args):
    """Current host observations qualify a NEW action, not the old outcome."""
    from . import task_environments as host
    proof = binding(row, original)
    if (not proof or row.get('guestRemoved') or row.get('status') == 'retired'
            or row.get('environmentState') != 'stopped' or config.get('remoteRunner')
            or config.get('backend', 'docker') != 'docker'
            or host.config_key(config) != row.get('backendKey')):
        return None
    target = host.host_resources.local_target(config.get('dockerHost'), {'dockerd'})
    if not target.get('supported'):
        return None
    directory = host.storage.directory(app.data_dir, row)
    identities = (row['universeId'], row['launchOperationId'])
    if not local_executor_quiescent(directory, identities):
        raise ValueError('The previous local cleanup process may still be active. Keep its original receipt and inspect again after it finishes.')
    result = host.invoke(config, app.data_dir, row, 'reconcile', {**args, 'effectAction': KIND})
    if (result.get('ok') is not True or not host.recovery.verify(row, result)
            or result.get('status') != 'stopped'
            or result.get('observation', {}).get('cleanupStoppedVerified') is not True):
        raise ValueError('Fresh cleanup requires the exact original stopped environment. No removal was started.')
    containers = result['observation'].get('containerIds')
    if (not isinstance(containers, list) or not containers
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{12,64}', value) for value in containers)):
        raise ValueError('The fresh cleanup container identities are unavailable. No removal was started.')
    identities += tuple(containers)
    if not local_executor_quiescent(directory, identities):
        raise ValueError('The local cleanup executor changed during inspection. No removal was started.')
    return proof
