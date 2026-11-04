"""Fixed transport for foreground and durable guest commands (no host shell)."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shlex
import zlib

JOB_ACTIONS = {'job_start', 'job_read', 'job_cancel'}
TERMINAL = {'passed', 'failed', 'cancelled', 'timed_out'}


def arguments(payload):
    action = payload['action']
    if action not in JOB_ACTIONS | {'execute'}:
        raise ValueError('Unsupported guest action.')
    source = Path(__file__).with_name('test_job_guest.py').read_text()
    encoded = base64.b64encode(zlib.compress(source.encode())).decode()
    bootstrap = 'import base64,zlib; SCRIPT=zlib.decompress(base64.b64decode(' + repr(encoded) + ')).decode(); exec(SCRIPT)'
    # Foreground commands share workload policy, not the durable job lifecycle.
    keys = ('command',) if action == 'execute' else ('rootOwner', 'operationId', 'universeId', 'jobId', 'command', 'timeoutSeconds')
    values = {key: payload[key] for key in keys}
    values['environment'] = payload.get('jobEnvironment', {})
    return ['python3', '-I', '-c', bootstrap, action, json.dumps(values)]


def command(payload):
    return shlex.join(arguments(payload))


def result(payload, transport):
    if transport.get('exit_code') or transport.get('truncated'):
        raise ValueError('The durable job observer did not return a complete receipt.')
    value = json.loads(transport['stdout'])
    job = value.get('job', {})
    if job.get('id') != payload['jobId'] or job.get('version') != 1:
        raise ValueError('The durable job observer returned another identity.')
    binding = job.get('binding', {})
    if any(binding.get(key) != payload[key] for key in ('rootOwner', 'operationId', 'universeId', 'jobId', 'timeoutSeconds')):
        raise ValueError('The durable job owner binding changed.')
    if binding.get('commandSha256') != hashlib.sha256(payload['command'].encode()).hexdigest():
        raise ValueError('The durable job command binding changed.')
    if job.get('terminal') != (job.get('state') in TERMINAL):
        raise ValueError('The durable job terminal receipt is inconsistent.')
    if job.get('state') not in TERMINAL | {'starting', 'running', 'unknown'} or type(job.get('terminal')) is not bool:
        raise ValueError('The durable job state is invalid.')
    output = value.get('result', {})
    if any(not isinstance(output.get(key, ''), str) or len(output.get(key, '')) > 8192 for key in ('stdout', 'stderr')):
        raise ValueError('The durable job output exceeded its bound.')
    code = output.get('exit_code')
    if code is not None and type(code) is not int:
        raise ValueError('The durable job exit code is invalid.')
    if job.get('state') == 'passed' and code != 0 or job.get('state') == 'failed' and (code is None or code == 0):
        raise ValueError('The durable job exit code does not match its outcome.')
    return {'ok': True, 'status': 'running', 'universeId': payload['universeId'], **value}


def initialize_command(prepared=False):
    # No followed symlinks, including source-controlled links outside workspace.
    script = """import os
from pathlib import Path
Path('/var/lib/unified-jobs').mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod('/var/lib/unified-jobs', 0o700)
for root in ('/workspace', '/artifacts'):
    os.chown(root, 65534, 65534, follow_symlinks=False)
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            os.chown(os.path.join(parent, name), 65534, 65534, follow_symlinks=False)
"""
    prefix = ['python3 /opt/unified-prepared/initialize.py'] if prepared else []
    return ' && '.join([*prefix, shlex.join(['python3', '-I', '-c', script])])
