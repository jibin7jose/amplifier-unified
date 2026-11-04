"""Linux guest supervisor. Shipped as app-owned source, never loaded from a test.

Control records belong to root, outside the writable source/artifacts. Work runs
as nobody. A missing/abandoned reservation is uncertainty, never permission to
repeat a command. Host transport lifetime has no bearing on the guest deadline.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

CONTROL = Path('/var/lib/unified-jobs')
WORKSPACE = '/workspace'
SCRATCH = Path('/tmp/unified-test')
OUTPUT_BYTES = 8192
TERMINAL = {'passed', 'failed', 'cancelled', 'timed_out'}


def publish(path, value):
    temporary = path.with_name(path.name + '.new-' + str(os.getpid()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 262144:
            raise ValueError('The guest control record is unsafe.')
        return json.load(stream)


def process_identity(pid):
    try:
        value = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()
        if value[0] == 'Z':
            return None
        return {'pid': pid, 'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'startTicks': value[19]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def bound(payload):
    seconds, command = payload['timeoutSeconds'], payload['command']
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 2147483647:
        raise ValueError('The guest deadline must be between 1 and 2147483647 seconds.')
    if not isinstance(command, str) or not command or len(command) > 16384:
        raise ValueError('A bounded command is required.')
    value = {key: payload[key] for key in ('rootOwner', 'operationId', 'universeId', 'jobId')}
    if any(not isinstance(v, str) or not v or len(v) > 300 for v in value.values()):
        raise ValueError('A durable owner and job identity are required.')
    return {**value, 'commandSha256': hashlib.sha256(command.encode()).hexdigest(), 'timeoutSeconds': seconds}


def directory(payload):
    expected = bound(payload)
    CONTROL.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = CONTROL.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('The guest control directory must be private to the supervisor.')
    return CONTROL / hashlib.sha256(expected['jobId'].encode()).hexdigest(), expected


def unknown(expected, reason):
    return {'version': 1, 'id': expected['jobId'], 'binding': expected, 'state': 'unknown',
            'terminal': False, 'reason': reason}


def observe(payload):
    root, expected = directory(payload)
    if not root.exists():
        return {'job': unknown(expected, 'No durable guest admission is recorded; the command was not replayed.')}
    if root.is_symlink() or root.stat().st_uid != 0 or root.stat().st_mode & 0o077:
        raise ValueError('The guest job directory changed.')
    try:
        admission = read(root / 'admission.json')
        if admission['binding'] != expected:
            raise ValueError('This job identity belongs to another command or owner.')
        state = read(root / 'state.json')
    except FileNotFoundError:
        return {'job': unknown(expected, 'Guest admission or supervisor startup is incomplete; it was not repeated.')}
    if state.get('binding') != expected:
        raise ValueError('The saved job binding changed.')
    if state['state'] not in TERMINAL:
        supervisor = state.get('supervisor')
        if not supervisor or process_identity(supervisor['pid']) != supervisor:
            state = {**unknown(expected, 'The exact supervisor is no longer observed; no check or cleanup was repeated.'),
                     'result': state.get('result', {'stdout': '', 'stderr': '', 'exit_code': None, 'truncated': False})}
    result = state.pop('result', {'stdout': '', 'stderr': '', 'exit_code': None, 'truncated': False})
    return {'job': state, 'result': result}


def start(payload):
    root, expected = directory(payload)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        return observe(payload)
    publish(root / 'admission.json', {'binding': expected, 'createdAt': time.time()})
    # Exclusive directory and fsynced admission precede process creation. A lost
    # response or crash in this gap remains unknown, even for the same request.
    subprocess.Popen([sys.executable, '-I', '-c', 'SCRIPT='+repr(SCRIPT)+'; exec(SCRIPT)', 'supervise', json.dumps(payload)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, start_new_session=True)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if (root / 'state.json').exists() and read(root / 'state.json').get('state') != 'starting':
            break
        time.sleep(0.02)
    return observe(payload)


def cancel(payload):
    root, expected = directory(payload)
    result = observe(payload)
    if result['job']['terminal']:
        return result
    if not root.exists() or not (root / 'admission.json').exists():
        return result
    if read(root / 'admission.json')['binding'] != expected:
        raise ValueError('Cancellation belongs to another command or owner.')
    publish(root / 'cancel.json', {'binding': expected, 'requestedAt': time.time()})
    # The owner supervisor consumes this intent; this observer never signals an
    # arbitrary/reused PID and never interprets transport success as cancellation.
    result = observe(payload)
    result['job']['cancelRequested'] = True
    return result


def become_worker():
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
    # Disposable defaults belong to the workload, not the retained evidence
    # volume. Recreate them after a guest restart clears its temporary mount.
    SCRATCH.mkdir(mode=0o700, exist_ok=True)
    for name in ('home', 'cache'):
        (SCRATCH / name).mkdir(mode=0o700, exist_ok=True)


def worker_environment(payload):
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(SCRATCH / 'home'),
            'XDG_CACHE_HOME': str(SCRATCH / 'cache'), 'LANG': 'C.UTF-8',
            **payload.get('environment', {})}


def execute(payload):
    """Replace this foreground helper; the backend still owns bounds/receipts."""
    environment = worker_environment(payload)
    become_worker()
    os.chdir(WORKSPACE)
    os.execve('/bin/sh', ['/bin/sh', '-c', payload['command']], environment)


def supervise(payload):
    root, expected = directory(payload)
    if read(root / 'admission.json')['binding'] != expected:
        raise ValueError('The admitted job identity changed.')
    fd = os.open(root / 'supervisor.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    supervisor = process_identity(os.getpid())
    state = {'version': 1, 'id': expected['jobId'], 'binding': expected, 'state': 'starting',
             'terminal': False, 'startedAt': time.time(), 'supervisor': supervisor}
    publish(root / 'state.json', state)
    if (root / 'cancel.json').exists():
        state.update(state='cancelled', terminal=True, finishedAt=time.time(),
                     result={'stdout': '', 'stderr': '', 'exit_code': None, 'truncated': False})
        publish(root / 'state.json', state)
        return
    process = subprocess.Popen(['/bin/sh', '-c', payload['command']], cwd=WORKSPACE,
        env=worker_environment(payload), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        close_fds=True, start_new_session=True, preexec_fn=become_worker)
    state.update(state='running', process=process_identity(process.pid))
    publish(root / 'state.json', state)
    deadline = time.monotonic() + expected['timeoutSeconds']
    output, errors = bytearray(), bytearray()
    truncated, ending, terminate_at = False, None, None
    last_saved = time.monotonic()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, output)
        selector.register(process.stderr, selectors.EVENT_READ, errors)
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            code = process.poll()
            if ending is None and ((root / 'cancel.json').exists() or now >= deadline or code is not None):
                ending = ('cancelled' if (root / 'cancel.json').exists() else
                          'timed_out' if now >= deadline else 'passed' if code == 0 else 'failed')
                terminate_at = now + 2
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if terminate_at is not None and now >= terminate_at:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # A descendant that deliberately escapes the process group
                # cannot keep this supervisor pinned via inherited pipes.
                if now >= terminate_at + 2 and process.poll() is not None:
                    for key in list(selector.get_map().values()):
                        selector.unregister(key.fileobj)
                    truncated = True
                    break
            for key, _ in selector.select(0.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    room = max(0, OUTPUT_BYTES - len(key.data))
                    key.data.extend(chunk[:room])
                    truncated |= len(chunk) > room
            if now - last_saved >= 1:
                result = {'stdout': output.decode('utf-8', 'replace'), 'stderr': errors.decode('utf-8', 'replace'),
                          'exit_code': None, 'truncated': truncated}
                cancelled = ending == 'cancelled'
                if state.get('result') != result or state.get('cancelRequested') != cancelled:
                    state['result'] = result
                    state['cancelRequested'] = cancelled
                    publish(root / 'state.json', state)
                last_saved = now
    code = process.wait()
    process.stdout.close()
    process.stderr.close()
    # Quiesce any ordinary background process in the owned command group before
    # declaring terminal. Container cleanup contains deliberately detached work.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    state.update(state=ending or ('passed' if code == 0 else 'failed'), terminal=True, finishedAt=time.time(),
                 cancelRequested=ending == 'cancelled',
                 result={'stdout': output.decode('utf-8', 'replace'), 'stderr': errors.decode('utf-8', 'replace'),
                         'exit_code': code, 'truncated': truncated})
    publish(root / 'state.json', state)


def main():
    action, payload = sys.argv[1], json.loads(sys.argv[2])
    functions = {'job_start': start, 'job_read': observe, 'job_cancel': cancel, 'supervise': supervise,
                 'execute': execute}
    result = functions[action](payload)
    if action not in {'supervise', 'execute'}:
        print(json.dumps(result))


if __name__ == '__main__':
    # Host bootstrap supplies SCRIPT; a direct invocation is useful for Linux
    # acceptance and loads only this app-owned source, never a guest work file.
    if 'SCRIPT' not in globals():
        SCRIPT = Path(__file__).read_text()
    main()
