"""Serial background browser checks, with durable coverage and owned-process cleanup.

No shell, retry, parallel execution, or success-on-error. Each scenario owns a
Linux process group and inherited environment tag (Chromium can detach). Leaked
processes fail that scenario; uncertain cleanup leaves later scenarios unrun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_directory(path, *, create=False):
    """Pin existing directories without following symlinks, including parents.

    The evidence directory and its parents remain caller-owned during execution.
    This rejects planted/replaced entries; it is not isolation from a hostile
    same-UID writer renaming directories or hardlinking open inodes concurrently.
    """
    path = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in path.parts[1:]:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise OSError('Evidence directory is not privately writable by this caller')
        return fd
    except BaseException:
        os.close(fd)
        raise


def ensure_evidence_directory(path):
    os.close(evidence_directory(path, create=True))


def regular_evidence(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
        raise OSError('Evidence must be a caller-owned, single-link regular file')
    return info


def evidence_entry(directory, name):
    try:
        return regular_evidence(os.stat(name, dir_fd=directory, follow_symlinks=False))
    except FileNotFoundError:
        return None


def file_identity(info):
    return (info.st_dev, info.st_ino) if info is not None else None


class EvidenceFile:
    """Only this runner's evidence files; never truncate an existing entry."""
    def __init__(self, path, *, create=False):
        self.name = path.name
        self.directory = evidence_directory(path.parent)
        fd = None
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
            fd = os.open(self.name, flags | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=self.directory)
            self.identity = file_identity(regular_evidence(os.fstat(fd)))
            if file_identity(evidence_entry(self.directory, self.name)) != self.identity:
                raise OSError('Evidence entry changed during open')
            self.stream = os.fdopen(fd, 'r+b' if create else 'rb', buffering=0)
        except BaseException:
            if fd is not None:
                os.close(fd)
            os.close(self.directory)
            raise

    def check(self):
        info = regular_evidence(os.fstat(self.stream.fileno()))
        if (file_identity(info) != self.identity
                or file_identity(evidence_entry(self.directory, self.name)) != self.identity):
            raise OSError('Evidence identity changed')
        return info

    def write(self, data):
        self.check()
        return self.stream.write(data)

    def read(self, size):
        self.check()
        return self.stream.read(size)

    def seek(self, offset):
        self.check()
        return self.stream.seek(offset)

    def unlink(self):
        self.check()
        os.unlink(self.name, dir_fd=self.directory)

    def fileno(self):
        self.check()
        return self.stream.fileno()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.stream.close()
        os.close(self.directory)


def read_evidence(path, limit, *, expected=None):
    file = EvidenceFile(path)
    try:
        before = file.check()
        if expected is not None and list(file.identity) != expected:
            raise OSError('Evidence does not match the owned writer')
        data = file.read(limit + 1)
        after = file.check()
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise OSError('Evidence changed during bounded read')
        return data
    finally:
        file.close()


def publish_evidence(path, data):
    # Random exclusive temp, verified destination, atomic rename; no write ever
    # follows a link or truncates an existing inode (including results.tmp).
    temporary = EvidenceFile(path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp'), create=True)
    try:
        previous = evidence_entry(temporary.directory, path.name)
        remaining = memoryview(data)
        while remaining:
            size = temporary.write(remaining[:65536])
            if not size:
                raise OSError('Short evidence write')
            remaining = remaining[size:]
        temporary.check()
        if file_identity(evidence_entry(temporary.directory, path.name)) != file_identity(previous):
            raise OSError('Evidence destination changed before publication')
        os.replace(temporary.name, path.name, src_dir_fd=temporary.directory,
                   dst_dir_fd=temporary.directory)
    finally:
        try:
            if file_identity(evidence_entry(temporary.directory, temporary.name)) == temporary.identity:
                temporary.unlink()
        finally:
            temporary.close()


def save(output, report):
    publish_evidence(output / 'results.json', (json.dumps(report, indent=2) + '\n').encode())


OWNER_ENV = 'AMPLIFIER_BROWSER_CHECK_OWNER'
ANCESTOR_ENV = 'AMPLIFIER_BROWSER_CHECK_ANCESTORS'
MAX_OWNER_ANCESTORS = 32


def owner_lineage(environment):
    """Bounded inherited cleanup lineage, not authority against same-UID forgery."""
    owner = environment.get(OWNER_ENV)
    encoded = environment.get(ANCESTOR_ENV, '')
    if not isinstance(encoded, str) or len(encoded) > MAX_OWNER_ANCESTORS * 33 - 1:
        raise OSError('Process ownership ancestry exceeds inspection bound')
    ancestors = encoded.split(',') if encoded else []
    if (len(ancestors) > MAX_OWNER_ANCESTORS
            or any(not re.fullmatch(r'[0-9a-f]{32}', value) for value in ancestors)
            or len(set(ancestors)) != len(ancestors)
            or owner is not None and (not isinstance(owner, str) or not re.fullmatch(r'[0-9a-f]{32}', owner))
            or (ancestors and owner is None) or owner in ancestors):
        raise OSError('Invalid process ownership lineage')
    return [*ancestors, *([owner] if owner is not None else [])]


def child_ownership(environment, token):
    lineage = owner_lineage(environment)
    if len(lineage) > MAX_OWNER_ANCESTORS:
        raise OSError('Nested process ownership limit reached; no child launched')
    if not re.fullmatch(r'[0-9a-f]{32}', token) or token in lineage:
        raise OSError('A child needs a fresh local owner')
    # Never reuse a parent's local token: an inner cleanup searches only its own
    # fresh token, while outer cleanup can still find detached nested children.
    return {OWNER_ENV: token, ANCESTOR_ENV: ','.join(lineage)}


def environment_owns(environment, token):
    fields = {}
    for entry in environment.split(b'\0'):
        key, _, value = entry.partition(b'=')
        if key not in (OWNER_ENV.encode(), ANCESTOR_ENV.encode()):
            continue
        name = key.decode('ascii')
        if name in fields:
            raise OSError('Duplicate process ownership field')
        try:
            fields[name] = value.decode('ascii')
        except UnicodeDecodeError:
            raise OSError('Invalid process ownership encoding') from None
    return token in owner_lineage(fields)


def process_identities():
    """Snapshot only scalar process identities before a scenario can inherit its tag."""
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] != 'Z':
                result[int(entry.name)] = fields[19]
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


def cleanup_failure(exc, phase, operation, *, pid=None, birth=None, ownership='unknown'):
    """Scalar evidence only; never serialize exceptions, paths, tokens or environ."""
    detail = {
        'phase': phase if phase in {'ownership', 'grace', 'initial_scan', 'termination',
                                   'term_wait', 'kill_wait', 'final_scan', 'wait'} else 'unknown',
        'operation': operation if operation in {'proc_iter', 'stat', 'uid_stat',
            'environ_open', 'environ_read', 'environ_close', 'post_denial_stat',
            'environ_bound', 'lineage', 'ownership_scan', 'pidfd_open',
            'identity_recheck', 'signal', 'pidfd_close', 'process_wait', 'retire_group'} else 'unknown',
        'errorType': ('PermissionError' if isinstance(exc, PermissionError) else
                      'OSError' if isinstance(exc, OSError) else
                      'TimeoutExpired' if isinstance(exc, subprocess.TimeoutExpired) else 'RuntimeError'),
        'ownership': 'proven' if ownership == 'proven' else 'unknown',
    }
    if type(getattr(exc, 'errno', None)) is int and 0 <= exc.errno <= 4095:
        detail['errno'] = exc.errno
    if type(pid) is int and 0 < pid <= 2**31 - 1:
        detail['pid'] = pid
    if isinstance(birth, str) and re.fullmatch(r'[0-9]{1,32}', birth):
        detail['startTime'] = birth
    return detail


def unknown_cleanup(exc):
    # An uninstrumented failure does not prove that no signals were attempted.
    # Exception attributes are not trusted receipts, even when shaped like one.
    return {
        'remaining': ['unknown'], 'terminated': None,
        'error': cleanup_failure(exc, 'unknown', 'retire_group')['errorType'],
        'failure': cleanup_failure(exc, 'unknown', 'retire_group'),
        'signalAttempts': None,
    }


def owned_members(group, token, known, preexisting=None, *, failure=None):
    """Return PID/start identities, including cooperatively tagged nested children.

    Tokens supplement group/identity checks for new same-UID descendants. They
    are not an antiforgery boundary against processes that copy or erase tags.
    """
    members = {}
    if failure is not None:
        failure.clear()
    try:
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            pid, birth, operation = int(entry.name), known.get(int(entry.name)), 'stat'
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                birth = fields[19]
                if fields[0] == 'Z':
                    continue
                in_group = int(fields[2]) == group
                if in_group or known.get(pid) == fields[19]:
                    members[pid] = fields[19]
                    continue
                # A process that predates this scenario cannot have inherited its
                # fresh token. PID reuse must still go through ownership discovery.
                if (preexisting or {}).get(pid) == fields[19]:
                    continue
                operation = 'uid_stat'
                if not in_group and entry.stat().st_uid != os.geteuid():
                    continue
                try:
                    operation = 'environ_open'
                    with (entry / 'environ').open('rb') as stream:
                        operation = 'environ_read'
                        environment = stream.read(1024 * 1024 + 1)
                        operation = 'environ_close'
                except PermissionError:
                    # Preserve the existing post-denial zombie check, not a retry.
                    denied_operation, operation = operation, 'post_denial_stat'
                    if (entry / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                        continue
                    operation = denied_operation
                    raise
                operation = 'environ_bound'
                if len(environment) > 1024 * 1024:
                    raise RuntimeError('Process ownership environment exceeds inspection bound')
                operation = 'lineage'
                if environment_owns(environment, token):
                    members[pid] = fields[19]
            except (FileNotFoundError, ProcessLookupError):
                pass
            except (OSError, RuntimeError) as exc:
                if failure is not None:
                    failure.update(cleanup_failure(exc, 'ownership', operation, pid=pid, birth=birth))
                raise
    except (OSError, RuntimeError) as exc:
        if failure is not None and not failure:
            failure.update(cleanup_failure(exc, 'ownership', 'proc_iter'))
        raise
    return members


def retire_group(group, token, preexisting=None, *, drain=None):
    known = {}
    drain_error = None
    initial, observed, phase = None, [], 'grace'
    attempts = {'attempted': 0, 'returned': 0, 'failed': 0, 'disappeared': 0,
                'items': [], 'omitted': 0}
    failure = {}

    def receipt(remaining):
        # Legacy terminated counts the initial orphan set, not signal outcomes.
        result = {'remaining': remaining, 'terminated': None if initial is None else len(initial)}
        if drain_error:
            result['drainError'] = drain_error
        if observed:
            result['observedBeforeTermination'] = observed
            if len(initial) > len(observed):
                result['omittedProcessDetails'] = len(initial) - len(observed)
        if attempts['attempted']:
            result['signalAttempts'] = attempts
        return result

    def inspect():
        nonlocal drain_error
        if drain:
            try:
                drain()
            except (OSError, RuntimeError) as exc:
                drain_error = cleanup_failure(exc, phase, 'ownership_scan')['errorType']
        failure.clear()
        try:
            current = owned_members(group, token, known, preexisting, failure=failure)
        except (OSError, RuntimeError) as exc:
            if not failure:
                failure.update(cleanup_failure(exc, phase, 'ownership_scan'))
            raise
        # Once ownership is proven, retain PID/start-time identity while the
        # process exits; Linux can revoke its environ before it becomes a zombie.
        known.update(current)
        return current
    try:
        # Fixtures commonly send termination in finally before Node itself exits.
        # Give that normal shutdown time to finish before declaring an orphan.
        grace = time.monotonic() + .5
        while inspect() and time.monotonic() < grace:
            time.sleep(.05)
        phase = 'initial_scan'
        initial = inspect()
        for pid, birth in initial.items():
            if len(observed) == 32:
                break
            row = {'pid': pid, 'startTime': birth}
            try:
                entry = Path('/proc') / str(pid)
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if fields[19] == birth:
                    executable = entry.joinpath('exe').readlink().name[:96]
                    fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                    if fields[19] == birth:
                        row.update(parentPid=int(fields[1]), processGroup=int(fields[2]),
                                   executable=executable)
                    else:
                        row['detail'] = 'identity_changed'
                else:
                    row['detail'] = 'identity_changed'
            except OSError as exc:
                row['detail'] = cleanup_failure(exc, phase, 'stat')['errorType']
            observed.append(row)

        for sig in (signal.SIGTERM, signal.SIGKILL):
            phase = 'termination'
            current = inspect()
            if not current:
                return receipt([])
            for pid, birth in current.items():
                operation = 'pidfd_open'
                ownership = 'proven'  # This PID/start pair was observed in the existing scan.
                try:
                    handle = os.pidfd_open(pid)
                    try:
                        operation = 'identity_recheck'
                        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                        if fields[19] == birth:
                            operation = 'signal'
                            attempt = {'pid': pid, 'startTime': birth, 'signal': sig.name, 'outcome': 'unknown'}
                            attempts['attempted'] += 1
                            if len(attempts['items']) < 32:
                                attempts['items'].append(attempt)
                            else:
                                attempts['omitted'] += 1
                            try:
                                signal.pidfd_send_signal(handle, sig)
                            except (FileNotFoundError, ProcessLookupError):
                                attempt['outcome'] = 'disappeared'
                                attempts['disappeared'] += 1
                                raise
                            except (OSError, RuntimeError):
                                attempt['outcome'] = 'failed'
                                attempts['failed'] += 1
                                raise
                            else:
                                attempt['outcome'] = 'returned'
                                attempts['returned'] += 1
                        else:
                            ownership = 'unknown'  # Reused identity: no signal was sent.
                    finally:
                        # Retain the operation that failed unless descriptor
                        # close itself fails (the existing exception precedence).
                        try:
                            os.close(handle)
                        except (OSError, RuntimeError):
                            operation = 'pidfd_close'
                            raise
                except (FileNotFoundError, ProcessLookupError):
                    pass
                except (OSError, RuntimeError) as exc:
                    failure.update(cleanup_failure(exc, phase, operation,
                                                  pid=pid, birth=birth, ownership=ownership))
                    raise
            deadline = time.monotonic() + 2
            phase = 'term_wait' if sig == signal.SIGTERM else 'kill_wait'
            while time.monotonic() < deadline and inspect():
                time.sleep(.05)
        phase = 'final_scan'
        return receipt(list(inspect()))
    except (OSError, RuntimeError) as exc:
        detail = failure or cleanup_failure(exc, phase, 'retire_group')
        # Only this invocation's local observations are exported, never an
        # exception-supplied report. A returned signal is not proof of exit;
        # a failed syscall is not proof that prior effects did not occur.
        return {**receipt(['unknown']), 'error': detail['errorType'],
                'failure': {**detail, 'phase': phase}, 'signalAttempts': attempts}


class BoundedLog:
    """Binary head + disk ring tail. Memory/read work is bounded per poll.

    While running, logCapture identifies the ring cursor; after close the .log
    contains head then chronological tail. Never decode, buffer all output, or
    stop draining when the retention limit is reached.
    """
    def __init__(self, path, limit):
        if type(limit) is not int or limit < 2:
            raise ValueError('Log bound must contain a head and tail')
        self.path, self.limit = path, limit
        self.head_limit, self.tail_limit = limit // 2, limit - limit // 2
        self.head = EvidenceFile(path, create=True)
        try:
            self.tail = EvidenceFile(path.with_suffix(path.suffix + '.tail'), create=True)
        except BaseException:
            self.head.close()
            raise
        self.observed = self.head_bytes = self.tail_bytes = self.cursor = 0
        self.eof = self.finalized = False
        self.pipe = None
        self.error = None
        self.discarded = 0

    def attach(self, pipe):
        self.pipe = pipe
        os.set_blocking(pipe.fileno(), False)

    def append(self, data):
        self.observed += len(data)
        if self.error:
            self.discarded += len(data)
            return
        try:
            self._append(data)
        except OSError as exc:
            self.error = type(exc).__name__
            # The failing chunk may be partially written; do not claim an exact
            # retained byte count after an I/O error.
            self.discarded += len(data)
            raise

    def _append(self, data):
        head = min(len(data), self.head_limit - self.head_bytes)
        if self.head.write(data[:head]) != head:
            raise OSError('Short log head write')
        self.head_bytes += head
        data = memoryview(data)[head:]
        while data:
            size = min(len(data), self.tail_limit - self.cursor)
            self.tail.seek(self.cursor)
            if self.tail.write(data[:size]) != size:
                raise OSError('Short log tail write')
            self.cursor = (self.cursor + size) % self.tail_limit
            self.tail_bytes = min(self.tail_limit, self.tail_bytes + size)
            data = data[size:]

    def drain(self):
        if self.pipe is None or self.eof:
            return
        # Even a continuously flooding child cannot starve deadline checks.
        for _ in range(4):
            try:
                data = os.read(self.pipe.fileno(), 65536)
            except BlockingIOError:
                return
            except OSError as exc:
                self.error = self.error or type(exc).__name__
                return
            if not data:
                self.eof = True
                return
            try:
                self.append(data)
            except OSError:
                pass  # Retain first error; drain/discard so cleanup can proceed.

    def receipt(self):
        return {'limitBytes': self.limit, 'bytesObserved': self.observed,
                'bytesRetained': None if self.error else self.head_bytes + self.tail_bytes,
                'captureError': self.error, 'discardedBytes': self.discarded,
                'truncated': self.observed > self.limit, 'eof': self.eof,
                'format': 'head-tail' if self.finalized else 'head-ring-tail',
                'tailCheckpointMayLag': not self.finalized,
                'headBytes': self.head_bytes, 'tailBytes': self.tail_bytes,
                'tailStart': self.cursor if self.tail_bytes == self.tail_limit else 0}

    def close(self):
        try:
            self.drain()
            if self.error:
                return
            start = self.cursor if self.tail_bytes == self.tail_limit else 0
            for offset, size in ((start, self.tail_bytes - start), (0, start)):
                self.tail.seek(offset)
                while size:
                    data = self.tail.read(min(size, 65536))
                    if not data:
                        raise OSError('Truncated log ring')
                    if self.head.write(data) != len(data):
                        raise OSError('Short finalized log write')
                    size -= len(data)
            self.finalized = True
            self.tail.unlink()
        except OSError as exc:
            self.error = self.error or type(exc).__name__
            raise
        finally:
            self.head.close()
            self.tail.close()
            if self.pipe is not None:
                self.pipe.close()


def interrupt_owned(process, token, preexisting):
    """SIGINT only the still-owned leader, pinned by PID/start time and pidfd."""
    members = owned_members(process.pid, token, {}, preexisting)
    birth = members.get(process.pid)
    if birth is None:
        return False
    try:
        handle = os.pidfd_open(process.pid)
        try:
            fields = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()
            if fields[19] == birth:
                signal.pidfd_send_signal(handle, signal.SIGINT)
                return True
        finally:
            os.close(handle)
    except (FileNotFoundError, ProcessLookupError):
        pass
    return False


def validate_plan(plan):
    rows = plan['scenarios']
    if plan.get('schemaVersion') != 1 or not 1 <= len(rows) <= 200:
        raise ValueError('Invalid browser plan')
    seen = set()
    for row in rows:
        name, command = row['id'], row['command']
        if (not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name)
                or name in seen or not command or not all(isinstance(x, str) for x in command)):
            raise ValueError('Invalid or duplicate scenario')
        seen.add(name)
    return rows


def initial_report(plan, provenance):
    return {'schemaVersion': 1, 'source': provenance or {}, 'complete': False, 'passed': False,
            'scenarios': [{**row, 'status': 'unrun', 'reason': 'not_started'}
                          for row in validate_plan(plan)]}


APP004_UNIT_NAMES = (
    'chat reference metadata is a fixed bounded data shape',
    *(f'chat reference rejects {name}' for name in (
        'action', 'URL', 'foreign source', 'foreign message', 'missing target',
        'wrong version', 'invalid revision', 'unbounded label', 'control character',
        'missing availability')),
    'chat reference rejects duplicate, oversized and expanded envelopes',
    'copy uses an available message without another request',
    'copy reads an explicitly addressed off-page chat without changing selection',
    'missing scoped message reports failure instead of copying unrelated content',
    'copy retains prose and appends only the host-supplied inert reference fallback',
    'held route stays blocked through exercise and drains once before unregistration',
    'drain includes concurrent callbacks and waiting unroute joins a final protocol arrival',
    'callback errors become an awaited failure, not ignored or retried',
    'primary assertion survives successful release and cleanup',
    'primary error is retained with callback and teardown errors as secondary',
    'early-started action rejection stays observable until it is awaited',
    'cleanup failure alone fails and cannot replace a prior primary failure',
)
APP004_BOUNDARY = 'app004-same-document'


def initial_app004(provenance):
    # Fixed commands, not a second user-programmable plan or scenario selector.
    return {'schemaVersion': 1, 'source': provenance or {}, 'complete': False, 'passed': False,
            'checks': [
                {'id': 'units', 'status': 'unrun', 'reason': 'not_started', 'timeoutSeconds': 60,
                 'command': ['node', '--test', '--test-reporter=tap',
                             'tests/chat-reference.test.mjs', 'tests/message-copy.test.mjs',
                             'tests/chat-reference-harness.test.mjs']},
                {'id': 'reload-negative', 'status': 'unrun', 'reason': 'not_started',
                 'timeoutSeconds': 300, 'command': ['node', 'tests/chat-reference-browser.mjs']},
            ]}


def save_app004(output, report):
    folder = output / 'app004-qualification'
    ensure_evidence_directory(folder)
    save(folder, report)


def app004_units(log):
    """Require the exact 23 flat Node test identities, not just process exit zero."""
    text = log.read_text()
    names = re.findall(r'^ok \d+ - (.+)$', text, re.M)
    counts = {key: re.findall(rf'^# {key} (\d+)$', text, re.M)
              for key in ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo')}
    expected = {key: [str(23 if key in ('tests', 'pass') else 0)] for key in counts}
    if (counts != expected or len(names) != 23 or set(names) != set(APP004_UNIT_NAMES)
            or re.search(r'^not ok\b|^Bail out!', text, re.M)):
        raise ValueError('unit_identity_or_count_mismatch')


def app004_negative(folder, token):
    """Only the deliberately broken genuine click at the exact assertion qualifies."""
    def read(name):
        path = folder / name
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('oversized_negative_evidence')
        return json.loads(path.read_text())
    outcome, routes = read('outcome.json'), read('route-lifecycle.json')
    proof, primary = outcome.get('identityBoundary', {}), outcome.get('primary', {})
    exit_state = outcome.get('fixtureExit', {})
    if (not token or outcome.get('qualificationToken') != token
            or outcome.get('negativeReload') is not True
            or proof.get('id') != APP004_BOUNDARY
            or not all(proof.get(key) is True for key in
                       ('attachmentAccepted', 'cardVisible', 'draftHeld', 'clickCompleted', 'attempted'))
            or proof.get('verdict') != 'failed'
            or not isinstance(proof.get('targetSessionId'), str) or not proof['targetSessionId']
            or proof.get('selectedSessionId') != proof['targetSessionId']
            or primary.get('name') != 'AssertionError' or primary.get('code') != 'ERR_ASSERTION'
            or primary.get('boundary') != APP004_BOUNDARY
            or outcome.get('browserErrors') != []
            or outcome.get('secondary') != [] or exit_state != {'code': 0, 'signal': None}
            or type(exit_state.get('code')) is not int):
        raise ValueError('wrong_negative_boundary_or_cleanup')
    before, after = proof.get('before', {}), proof.get('after', {})
    for marker in (before, after):
        if (set(marker) != {'document', 'timeOrigin', 'client'}
                or any(not isinstance(marker.get(key), str) or not marker[key]
                       for key in ('document', 'client'))
                or type(marker.get('timeOrigin')) not in (int, float)
                or not math.isfinite(marker['timeOrigin']) or marker['timeOrigin'] <= 0):
            raise ValueError('missing_identity_markers')
    if before['document'] == after['document'] or before['timeOrigin'] == after['timeOrigin']:
        raise ValueError('reload_not_observed')
    # The negative stops at the FIRST boundary, so only held-draft routing exists.
    if not isinstance(routes, list) or not routes or len(routes) > 10000:
        raise ValueError('missing_route_lifecycle')
    phases, started, completed = [], [], []
    for row in routes:
        if (not isinstance(row, dict) or row.get('route') != 'held-draft' or 'error' in row
                or row.get('phase') not in
                ('registered', 'started', 'completed', 'gate-released', 'drained', 'unregistered')):
            raise ValueError('failed_or_unexpected_route')
        phase = row['phase']
        if phase in ('started', 'completed'):
            if type(row.get('id')) is not int or row['id'] < 1:
                raise ValueError('invalid_route_identity')
            if phase == 'completed' and row['id'] not in started:
                raise ValueError('route_completed_without_start')
            (started if phase == 'started' else completed).append(row['id'])
        else:
            if row.get('id') is not None:
                raise ValueError('invalid_route_phase')
            phases.append(phase)
    if (phases != ['registered', 'gate-released', 'drained', 'unregistered']
            or routes[0]['phase'] != 'registered' or routes[-1]['phase'] != 'unregistered'
            or not started or len(set(started)) != len(started)
            or sorted(started) != sorted(completed)):
        raise ValueError('unfinished_route_lifecycle')


def app004_verdict(row, folder):
    """Preserve raw exits and failure diagnostics alongside the separate verdict."""
    try:
        if (row['reason'] or row['cleanup'] != {'remaining': [], 'terminated': 0}
                or type(row.get('exitCode')) is not int):
            raise ValueError('process_or_cleanup_failure')
        if row['id'] == 'units':
            if row.get('exitCode') != 0:
                raise ValueError('unit_process_failed')
            app004_units(folder / row['log'])
            files = [row['log']]
        else:
            if row.get('exitCode') != 1:
                raise ValueError('negative_exit_not_one')
            app004_negative(folder / 'negative', row['executionToken'])
            files = [row['log'], 'negative/outcome.json', 'negative/route-lifecycle.json']
        row['evidenceSha256'] = {name: digest(folder / name) for name in files}
        row['status'], row['verdict'] = 'passed', 'exact_units' if row['id'] == 'units' else 'expected_identity_failure'
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        row['status'], row['verdict'] = 'failed', 'rejected'
        row['verdictReason'] = str(exc)


def run_scenarios(root, plan, output, *, budget=1500, case_timeout=300, provenance=None,
                  app004_qualification=False, cwd=None, log_limit=None, interrupt_grace=0,
                  deadline=None, report_fields=None, junit_output=None):
    ensure_evidence_directory(output)
    report = initial_report(plan, provenance)
    report.update(report_fields or {})
    save(output, report)  # Even a killed job leaves its complete intended coverage.
    qualification = initial_app004(provenance) if app004_qualification else None
    if qualification:
        save_app004(output, qualification)
    deadline = time.monotonic() + budget if deadline is None else deadline
    stopped = None
    old_handlers = {}

    def interrupted(signum, _frame):
        nonlocal stopped
        stopped = 'cancelled'

    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.signal(sig, interrupted)
    try:
        entries = [(row, False) for row in report['scenarios']]
        if qualification:
            entries += [(row, True) for row in qualification['checks']]
        for row, extra in entries:
            folder = output / 'app004-qualification' if extra else output
            if extra:
                # Finish the normal receipt BEFORE extras, even if an extra is killed.
                report['complete'] = all(r['status'] in ('passed', 'failed') for r in report['scenarios'])
                report['passed'] = all(r['status'] == 'passed' for r in report['scenarios'])
                save(output, report)
                if any(r.get('cleanup') != {'remaining': [], 'terminated': 0} for r in report['scenarios']):
                    stopped = stopped or 'unsafe_normal_cleanup'
                if deadline - time.monotonic() < row['timeoutSeconds']:
                    stopped = stopped or 'budget_exhausted'
            if stopped or time.monotonic() >= deadline:
                stopped = stopped or 'budget_exhausted'
                break
            row.update(status='running', reason=None, log=row['id'] + '.log')
            if 'pytestSessionStatus' in report:
                report['pytestSessionStatus'] = 'running'
            (save_app004(output, qualification) if extra else save(output, report))
            print('START ' + row['id'], flush=True)
            started, process, token = time.monotonic(), None, uuid.uuid4().hex
            if extra:
                row['executionToken'] = token
                save_app004(output, qualification)
            preexisting = None
            capture = None
            junit_file = None
            try:
                env = {**os.environ, **{k: v.replace('{root}', str(root))
                                       for k, v in row.get('env', {}).items()}}
                # Read lineage from this runner, not row overrides: a scenario
                # cannot drop the outer owner through its configured environment.
                env.update(child_ownership(os.environ, token))
                if extra and row['id'] == 'reload-negative':
                    env.update(CHAT_REFERENCE_NEGATIVE_RELOAD='1', APP004_QUALIFICATION_TOKEN=token,
                               CHAT_REFERENCE_EVIDENCE_DIR=str(folder / 'negative'))
                preexisting = process_identities()
                command = row['command']
                inherited = {}
                if junit_output is not None:
                    # Pytest opens this owned descriptor, not a replaceable
                    # pathname. Its existing JUnit plugin/selection is unchanged.
                    junit_file = EvidenceFile(junit_output, create=True)
                    option = '--junitxml=output/python-checks/junit.pending.xml'
                    if command.count(option) != 1:
                        raise RuntimeError('Expected the fixed JUnit output argument')
                    command = [('--junitxml=/proc/self/fd/' + str(junit_file.fileno()))
                               if part == option else part for part in command]
                    inherited = {'pass_fds': (junit_file.fileno(),)}
                    row['junitFileIdentity'] = list(junit_file.identity)
                    row['executedCommand'] = command
                    save(output, report)
                if log_limit is not None:
                    capture = BoundedLog(folder / row['log'], log_limit)
                    process = subprocess.Popen(command, cwd=cwd or root / 'frontend', env=env,
                                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               start_new_session=True, **inherited)
                    capture.attach(process.stdout)
                    checkpoint = time.monotonic()
                    while process.poll() is None and not stopped:
                        capture.drain()
                        if capture.error:
                            row['reason'] = 'log_capture_failed'
                            break
                        now = time.monotonic()
                        if now >= min(deadline, started + case_timeout):
                            row['reason'] = 'timeout'
                            break
                        if now >= checkpoint:
                            row['logCapture'] = capture.receipt()
                            save(output, report)
                            checkpoint = now + 1
                        time.sleep(.01)
                    row['reason'] = row['reason'] or stopped
                    if row['reason'] and process.poll() is None and interrupt_grace:
                        # Persist the interruption BEFORE waiting for pytest's
                        # finalizer. A zero exit after this signal is not a pass.
                        row['logCapture'] = capture.receipt()
                        row['interrupt'] = {'signal': 'SIGINT', 'graceSeconds': interrupt_grace,
                                            'sent': False}
                        save(output, report)
                        row['interrupt']['sent'] = interrupt_owned(process, token, preexisting)
                        save(output, report)
                        grace = time.monotonic() + interrupt_grace
                        while process.poll() is None and time.monotonic() < grace:
                            capture.drain()
                            time.sleep(.01)
                    row['exitCode'] = process.poll()
                else:
                    with EvidenceFile(folder / row['log'], create=True) as log:
                        process = subprocess.Popen(command, cwd=cwd or root / 'frontend', env=env,
                                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True, **inherited)
                        while process.poll() is None and not stopped:
                            limit = row['timeoutSeconds'] if extra else case_timeout
                            if time.monotonic() >= min(deadline, started + limit):
                                row['reason'] = 'timeout'
                                break
                            time.sleep(.05)
                        row['exitCode'] = process.poll()
            except (OSError, RuntimeError) as exc:
                if isinstance(exc, RuntimeError) and capture is None:
                    raise
                row['reason'] = row['reason'] or type(exc).__name__
                row['executionError'] = type(exc).__name__
            finally:
                try:
                    cleanup = (retire_group(process.pid, token, preexisting,
                               **({'drain': capture.drain} if capture else {}))
                               if process else {'remaining': [], 'terminated': 0})
                except (OSError, RuntimeError) as exc:
                    cleanup = unknown_cleanup(exc)
                row['cleanup'] = cleanup
                if cleanup.get('drainError'):
                    row['reason'] = row['reason'] or 'log_capture_failed'
                if process and not cleanup['remaining']:
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired as exc:
                        row['cleanup'] = cleanup = {**cleanup, 'remaining': ['unknown'],
                            'error': 'WaitTimeout',
                            'failure': cleanup_failure(exc, 'wait', 'process_wait', pid=process.pid)}
                    if capture:
                        row['exitCode'] = process.returncode
                if capture:
                    try:
                        capture.close()
                    except OSError as exc:
                        row['reason'] = row['reason'] or 'log_capture_failed'
                        row['logError'] = type(exc).__name__
                    row['logCapture'] = capture.receipt()
                    if capture.error:
                        row['logError'] = capture.error
                        row['reason'] = row['reason'] or 'log_capture_failed'
                    if not capture.eof:
                        row['reason'] = row['reason'] or 'log_incomplete'
                if junit_file:
                    try:
                        junit_file.check()
                    except OSError as exc:
                        row['reason'] = row['reason'] or 'junit_identity_changed'
                        row['junitError'] = type(exc).__name__
                    finally:
                        junit_file.close()
                row['durationSeconds'] = round(time.monotonic() - started, 3)
                row['reason'] = row['reason'] or stopped
                row['status'] = ('passed' if row.get('exitCode') == 0 and not row['reason']
                                 and cleanup['terminated'] == 0 and not cleanup['remaining'] else 'failed')
                if extra:
                    app004_verdict(row, folder)
                if cleanup['remaining']:
                    stopped = 'unsafe_cleanup'
                (save_app004(output, qualification) if extra else save(output, report))
            print(row['status'].upper() + ' ' + row['id'], flush=True)
        for row, extra in entries:
            if row['status'] == 'unrun':
                row['reason'] = stopped or 'interrupted'
        report['complete'] = all(row['status'] in ('passed', 'failed') for row in report['scenarios'])
        report['passed'] = all(row['status'] == 'passed' for row in report['scenarios'])
        if 'pytestSessionComplete' in report:
            report['scenarioListComplete'] = report['complete']
            report['passed'] = False  # Python verdict also needs bounded JUnit evidence.
        save(output, report)
        if qualification:
            qualification['complete'] = all(r['status'] in ('passed', 'failed') for r in qualification['checks'])
            qualification['passed'] = all(r['status'] == 'passed' for r in qualification['checks'])
            qualification['normalPassed'] = report['passed']
            qualification['combinedPassed'] = report['passed'] and qualification['passed']
            save_app004(output, qualification)
        return 0 if report['passed'] and (not qualification or qualification['passed']) else 1
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


PYTHON_BUDGET_SECONDS = 75 * 60
PYTHON_LOG_BYTES = 16 * 1024 * 1024
PYTHON_JUNIT_BYTES = 8 * 1024 * 1024
PYTHON_COMMAND = ['.venv/bin/python', '-u', '-m', 'pytest', '-v', '--tb=short',
                  '--junitxml=output/python-checks/junit.pending.xml']


def python_plan():
    # No selectors, maxfail, retries or collection omissions.
    return {'schemaVersion': 1, 'scenarios': [{'id': 'pytest', 'command': PYTHON_COMMAND}]}


def junit_counts(root):
    """Validate pytest's flat JUnit suites, not arbitrary nested XML summaries.

    Pytest can emit multiple outcome children (e.g. setup/teardown errors) for
    one case. Count those children, not truthiness; declared tests counts cases.
    Properties and captured output are data, never extra testcases.
    """
    counter_names = {'tests': 'testcases', 'failures': 'failure', 'errors': 'error', 'skipped': 'skipped'}

    def declared(element, counts, *, required):
        for key, count in counter_names.items():
            value = element.get(key)
            if value is None and not required:
                continue
            if value is None or not re.fullmatch(r'[0-9]{1,10}', value):
                raise ValueError('missing_or_invalid_counter')
            if int(value) != counts[count]:
                raise ValueError('counter_mismatch')

    def auxiliary(element):
        if element.tag == 'properties':
            if any(child.tag != 'property' or len(child) for child in element):
                raise ValueError('invalid_properties')
        elif element.tag in {'system-out', 'system-err'}:
            if len(element):
                raise ValueError('nested_output')
        else:
            raise ValueError('unexpected_structure')

    if root.tag == 'testsuite':
        suites = [root]
    elif root.tag == 'testsuites' and len(root) and all(child.tag == 'testsuite' for child in root):
        suites = list(root)
    else:
        raise ValueError('unsupported_junit_root')
    total = dict.fromkeys(counter_names.values(), 0)
    for suite in suites:
        counts = dict.fromkeys(total, 0)
        for case in suite:
            if case.tag != 'testcase':
                auxiliary(case)
                continue
            if not case.get('name'):
                raise ValueError('case_identity_missing')
            counts['testcases'] += 1
            for item in case:
                if item.tag in {'failure', 'error', 'skipped'}:
                    if len(item):
                        raise ValueError('nested_outcome')
                    counts[item.tag] += 1
                else:
                    auxiliary(item)
        declared(suite, counts, required=True)
        for key in total:
            total[key] += counts[key]
    if root.tag == 'testsuites':
        declared(root, total, required=False)
    return total


def junit_evidence(path, retained, *, limit=PYTHON_JUNIT_BYTES, expected=None):
    """Bound input before parsing; counts describe observed XML, not completion."""
    try:
        data = read_evidence(path, limit, expected=expected)
    except FileNotFoundError:
        return {'status': 'missing', 'counts': None}
    except OSError as exc:
        return {'status': 'unreadable', 'error': type(exc).__name__, 'counts': None}
    if len(data) > limit:
        return {'status': 'oversized', 'limitBytes': limit, 'counts': None}
    # No entity expansion or external definitions from test-owned output.
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data or b'\0' in data:
        return {'status': 'malformed', 'counts': None}
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {'status': 'malformed', 'counts': None}
    try:
        counts = junit_counts(root)
        validation = {'status': 'parsed', 'counts': counts}
    except ValueError as exc:
        validation = {'status': 'invalid', 'reason': str(exc), 'counts': None}
    try:
        publish_evidence(retained, data)
    except OSError as exc:
        return {'status': 'retention_failed', 'error': type(exc).__name__, 'counts': None}
    return {**validation, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'coverage': 'validated declared/observed counts only; exit and interruption govern completion'}


def finish_python_report(output, report):
    row, = report['scenarios']
    # run_scenarios.complete means all scenario rows were attempted, not that
    # pytest finished its selected session. Exit 2/3/4/5 is not completion.
    report['pytestSessionComplete'] = (type(row.get('exitCode')) is int
        and row['exitCode'] in (0, 1) and not row.get('reason')
        and row.get('cleanup') == {'remaining': [], 'terminated': 0})
    report['pytestSessionStatus'] = ('not_started' if row['status'] == 'unrun' else
                                    'completed' if report['pytestSessionComplete'] else 'incomplete')
    report['junit'] = junit_evidence(output / 'junit.pending.xml', output / 'junit.xml',
                                   expected=row.get('junitFileIdentity'))
    counts = report['junit'].get('counts') or {}
    report['passed'] = (row['status'] == 'passed' and report['pytestSessionComplete']
        and report['junit']['status'] == 'parsed' and counts.get('testcases', 0) > 0
        and counts.get('failure') == counts.get('error') == 0)
    save(output, report)
    return 0 if report['passed'] else 1


def read_python_report(output):
    data = read_evidence(output / 'results.json', 1024 * 1024)
    if len(data) > 1024 * 1024:
        raise ValueError('Python execution receipt exceeds its bound')
    return json.loads(data)


def python_suite(root, output, provenance, deadline, *, initialize=False, finalize=False):
    """One full pytest invocation inside the existing scenario/process owner."""
    if not math.isfinite(deadline) or deadline <= 0:
        raise ValueError('A finite early-job monotonic deadline is required')
    ensure_evidence_directory(output)
    initial = initial_report(python_plan(), provenance)
    initial.update(deadlineMonotonic=deadline, budgetSeconds=PYTHON_BUDGET_SECONDS,
                   scenarioListComplete=False, pytestSessionComplete=False,
                   pytestSessionStatus='not_started',
                   junit={'status': 'not_started', 'counts': None},
                   retentionLimits={'logBytes': PYTHON_LOG_BYTES, 'junitBytes': PYTHON_JUNIT_BYTES,
                                    'interruptGraceSeconds': 30},
                   limits='Runner loss, disk failure, stuck preparation or external hard kill can prevent finalization/upload. The remaining job time is a reserve, not an enforced upload timeout. Evidence paths require caller-owned directories without concurrent hostile same-UID mutation.')
    if initialize:
        directory = evidence_directory(output)
        try:
            for name in ('results.json', 'pytest.log', 'pytest.log.tail', 'junit.pending.xml', 'junit.xml'):
                try:
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ValueError('Python receipt already exists; do not reset or replay it')
        finally:
            os.close(directory)
        save(output, initial)
        return 0
    saved = read_python_report(output)
    if saved['source'] != provenance or saved['deadlineMonotonic'] != deadline:
        raise ValueError('Python evidence source or original deadline changed')
    row, = saved['scenarios']
    if row['command'] != PYTHON_COMMAND:
        raise ValueError('Python selection changed')
    if finalize:
        if row['status'] == 'unrun':
            row['reason'] = ('budget_exhausted_before_pytest' if time.monotonic() >= deadline
                             else 'preparation_not_completed')
        elif row['status'] == 'running':
            row.update(status='failed', reason='execution_outcome_unknown',
                       cleanup={'remaining': ['unknown'], 'terminated': 0})
            saved['passed'] = False
        return finish_python_report(output, saved)
    if row['status'] != 'unrun' or row['reason'] != 'not_started':
        raise ValueError('Python execution already attempted or finalized; no replay')
    if time.monotonic() >= deadline:
        row['reason'] = 'budget_exhausted_before_pytest'
        return finish_python_report(output, saved)
    # Carry the early receipt's fields through every atomic running checkpoint.
    run_scenarios(root, python_plan(), output, provenance=provenance, cwd=root,
                  deadline=deadline, case_timeout=PYTHON_BUDGET_SECONDS,
                  log_limit=PYTHON_LOG_BYTES, interrupt_grace=30,
                  junit_output=output / 'junit.pending.xml',
                  report_fields={key: value for key, value in initial.items()
                                 if key not in {'scenarios', 'source', 'complete', 'passed', 'schemaVersion'}})
    return finish_python_report(output, read_python_report(output))


def attachment_consumption_plan(plan):
    """One fixed diagnostic; retain the ordinary plan and process owner."""
    expected = {'id': 'attachment-consumption',
                'command': ['node', 'tests/attachment-consumption-browser.mjs'],
                'env': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/attachment-consumption'}}
    rows = [row for row in validate_plan(plan) if row['id'] == expected['id']]
    if rows != [expected]:
        raise ValueError('Attachment consumption requires its exact registered owner')
    return {'schemaVersion': 1, 'scenarios': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget-seconds', type=int, default=1500)
    parser.add_argument('--initialize', action='store_true', help='Record unrun coverage before dependency setup')
    parser.add_argument('--python-suite', action='store_true', help='Full Python CI with an early job deadline')
    parser.add_argument('--finalize', action='store_true', help='Retain partial Python outcome; never execute')
    parser.add_argument('--app004-qualification', action='store_true',
                        help='Append fixed APP004 units and reload control, without reducing normal coverage')
    parser.add_argument('--attachment-consumption', action='store_true',
                        help='Run only the fixed registered attachment consumption diagnostic')
    args = parser.parse_args()
    if args.python_suite:
        if args.plan or args.app004_qualification or args.attachment_consumption or args.initialize and args.finalize:
            parser.error('Full Python uses its fixed plan, separately from browser modes')
    elif not args.plan or args.finalize:
        parser.error('Browser modes require --plan; --finalize is Python-only')
    if args.attachment_consumption and args.app004_qualification:
        parser.error('Attachment consumption and APP004 are separate selections')
    root = Path(__file__).resolve().parents[1]
    git = lambda *parts: subprocess.check_output(['git', *parts], cwd=root).strip()
    provenance = {'revision': git('rev-parse', 'HEAD').decode(),
                  'tree': git('rev-parse', 'HEAD^{tree}').decode(),
                  'trackedDiffSha256': hashlib.sha256(git('diff', '--binary', 'HEAD')).hexdigest(),
                  'planSha256': (hashlib.sha256(json.dumps(python_plan(), sort_keys=True).encode()).hexdigest()
                                 if args.python_suite else digest(args.plan)),
                  'runnerSha256': digest(Path(__file__)),
                  'runId': os.environ.get('GITHUB_RUN_ID'), 'runAttempt': os.environ.get('GITHUB_RUN_ATTEMPT')}
    build = root / 'amplifier_web/static/build.json'
    provenance['buildMetadataSha256'] = digest(build) if build.is_file() else None
    if args.python_suite:
        return python_suite(root, args.output.absolute(), provenance,
                            float(os.environ['PYTHON_CHECK_DEADLINE']),
                            initialize=args.initialize, finalize=args.finalize)
    plan = json.loads(args.plan.read_text())
    if args.attachment_consumption:
        plan = attachment_consumption_plan(plan)
        provenance['selection'] = 'attachment-consumption-only'
    if args.initialize:
        ensure_evidence_directory(args.output)
        save(args.output, initial_report(plan, provenance))
        if args.app004_qualification:
            save_app004(args.output, initial_app004(provenance))
        return 0
    return run_scenarios(root, plan, args.output.absolute(),
                         budget=args.budget_seconds, provenance=provenance,
                         app004_qualification=args.app004_qualification)


if __name__ == '__main__':
    raise SystemExit(main())
