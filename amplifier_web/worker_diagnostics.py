"""Private startup receipts and closed, payload-free command failure facts."""
from datetime import datetime, timezone
from contextlib import contextmanager
import asyncio
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
import time
import types
import uuid

MAX_DETAIL_BYTES = 60_000
_SECRET_NAME = re.compile(r'(?i)(api.?key|password|secret|authorization|cookie|credential|token)')
_SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)(["']?(?:api[_-]?key|password|client[_-]?secret|access[_-]?token|refresh[_-]?token|id[_-]?token|authorization|cookie)["']?\s*[:=]\s*)
    (?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')
_RECEIPT = re.compile(r'^startup-[a-f0-9]{32}\.log$')


class StartupTimings:
    """Fixed-label private clocks, without arguments, configuration or history.

    Nested phases overlap. CPU is process CPU, including any worker threads;
    neither clock establishes provider latency or the host's ready observation.
    """
    PHASES = frozenset({'worker.bootstrap', 'worker.imports', 'worker.ownership',
        'worker.recovery', 'manager.prepare', 'manager.config', 'manager.history.load',
        'manager.bundle.load', 'manager.plan', 'manager.bundle.prepare',
        'manager.providers', 'manager.history.repair', 'manager.session.create',
        'manager.context.restore', 'manager.children', 'manager.settings',
        'manager.provider.info', 'manager.checkpoint', 'manager.report',
        'worker.controls.restore', 'worker.app.access', 'worker.ownership.register',
        'worker.support', 'worker.ready'})
    MAX_PHASES = 32

    def __init__(self, *, started=None):
        self.started = started or (time.monotonic_ns(), time.process_time_ns())
        self.phases = []
        self.attempt = uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()

    def _finish(self, row, started):
        row.update(elapsedMs=round((time.monotonic_ns() - started[0]) / 1e6, 3),
                   cpuMs=round((time.process_time_ns() - started[1]) / 1e6, 3))

    @contextmanager
    def phase(self, name, *, started=None):
        started = started or (time.monotonic_ns(), time.process_time_ns())
        row = {'phase': name, 'status': 'running',
               'offsetMs': round((started[0] - self.started[0]) / 1e6, 3)}
        if name in self.PHASES and len(self.phases) < self.MAX_PHASES:
            self.phases.append(row)
        try:
            yield
        except BaseException as error:
            row.update(status='cancelled' if isinstance(error, asyncio.CancelledError) else 'failed',
                       errorType=type(error).__name__[:100])
            raise
        else:
            row['status'] = 'succeeded'
        finally:
            self._finish(row, started)

    def snapshot(self, outcome='completed'):
        result = {'schemaVersion': 1, 'outcome': outcome,
                  'attemptId': self.attempt, 'workerPid': os.getpid(), 'startedAt': self.started_at,
                  'phases': [dict(row) for row in self.phases]}
        self._finish(result, self.started)
        return result

    def save(self, directory, outcome):
        try:
            from .host.config import write_private
            write_private(Path(directory) / 'startup-timing.json', json.dumps(self.snapshot(outcome)),
                          owned_root=_home())
        except BaseException:
            pass  # Diagnostic storage must not change startup or cleanup outcomes.


def redact_diagnostic(value):
    """Best-effort secret removal, including secrets inherited by this process.

    Receipts remain private because arbitrary module exception text can contain
    sensitive context beyond recognizable credentials. Never publish this text.
    """
    return diagnostic_redactor()(value)


def diagnostic_redactor():
    """Snapshot known secrets once for one diagnostic traversal, never globally."""
    secrets = tuple(secret for name, secret in os.environ.items()
                    if _SECRET_NAME.search(name) and len(secret) >= 4)

    def redact(value):
        text = str(value)
        for secret in secrets:
            text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', text)
        text = re.sub(r'(?i)(bearer\s+)[^\s"\']+', r'\1[REDACTED]', text)
        text = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
        text = _SECRET_ASSIGNMENT.sub(r'\1[REDACTED]', text)
        text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b', '[REDACTED]', text)
        return text

    return redact


def _bounded(text, limit=MAX_DETAIL_BYTES):
    return text.encode('utf-8', errors='replace')[-limit:].decode('utf-8', errors='ignore') if limit else ''


def _home():
    from .host.config import app_home
    return app_home()


def receipt_path(name, *, home=None):
    """Resolve only a receipt created in this instance, never a worker-sent path."""
    if not isinstance(name, str) or not _RECEIPT.fullmatch(name):
        return None
    path = (Path(home) if home is not None else _home()) / 'logs' / 'workers' / name
    try:
        return path if path.is_file() and not path.is_symlink() else None
    except OSError:
        return None


def diagnostic_reference(error):
    name = getattr(getattr(error, "diagnostic_path", None), "name", None)
    return {"diagnosticReceipt": name} if isinstance(name, str) and _RECEIPT.fullmatch(name) else {}


def setup_failure_detail(error):
    """Best-effort private formatting must never suppress the original failure."""
    try:
        message = str(error)
    except Exception:
        message = 'Exception text could not be formatted.'
    try:
        return json.dumps({'errorType': type(error).__name__, 'message': message,
            'facts': getattr(error, 'diagnostic_facts', {})}, default=str)
    except Exception:
        return json.dumps({'message': message})


_COMMAND_ERROR_KINDS = (
    Exception, RuntimeError, AssertionError, ValueError, TypeError, KeyError,
    AttributeError, ImportError, ModuleNotFoundError, OSError, PermissionError,
    FileNotFoundError, NotADirectoryError, IsADirectoryError, NotImplementedError,
    TimeoutError, EOFError, BrokenPipeError, ConnectionError, ConnectionAbortedError,
    ConnectionRefusedError, ConnectionResetError, asyncio.CancelledError,
    subprocess.TimeoutExpired, subprocess.CalledProcessError,
    UnicodeError, UnicodeDecodeError, UnicodeEncodeError,
)
_COMMAND_ERROR_CLASSES = tuple((kind, kind.__name__) for kind in _COMMAND_ERROR_KINDS)
_COMMAND_CLASS_LABELS = frozenset(label for _, label in _COMMAND_ERROR_CLASSES) | {'other'}
_COMMAND_OS_LABELS = frozenset(label for kind, label in _COMMAND_ERROR_CLASSES
                             if issubclass(kind, OSError))
_READER_FRAME_SOURCES = (
    ('amplifier_web.runtime', 'RuntimeManager', '_read', 'runtime.reader'),
    ('amplifier_web.runtime', '_BoundedWorkerLines', 'readline', 'runtime.readline'),
    ('amplifier_web.runtime', None, '_wait_worker', 'runtime.wait'),
    ('amplifier_web.runtime', '_WindowsWorkerOwner', 'wait', 'runtime.windows_wait'),
    ('amplifier_web.runtime_event_batches', 'BufferedLines', 'next', 'runtime.buffered_next'),
    ('amplifier_web.service', 'AppService', 'on_runtime_event', 'service.event'),
    ('amplifier_web.session_projection', None, 'persist', 'storage.projection'),
    ('amplifier_web.resource_files', None, 'write_prepared', 'storage.resource'),
    ('amplifier_web.host.storage', 'SessionStore', '_atomic', 'storage.atomic'),
    ('amplifier_web.naming', None, 'persist', 'storage.naming'),
)
_COMMAND_FRAME_LABELS = frozenset({'worker.command', 'worker.command_serial',
                                 *(label for _, _, _, label in _READER_FRAME_SOURCES)})
_COMMAND_MAX_ERRORS = 4
_COMMAND_MAX_FRAMES = 5
_COMMAND_FRAME_SCAN = 128
_COMMAND_MAX_BYTES = 8192


def _command_frame_codes():
    """Read already-loaded owners only; never import a runtime to diagnose it.

    The production worker is launched as a script. Its __main__ binding must
    identify our sibling script before its exact code objects can be approved.
    Paths/names alone never identify a traceback frame.
    """
    codes = []
    for name in ('amplifier_web.runtime_worker', '__main__'):
        module = sys.modules.get(name)
        if type(module) is not types.ModuleType:
            continue
        namespace = vars(module)
        if name == '__main__':
            path = namespace.get('__file__')
            if type(path) is not str or path != os.path.join(os.path.dirname(__file__), 'runtime_worker.py'):
                continue
        worker = namespace.get('Worker')
        if type(worker) is not type:
            continue
        methods = vars(worker)
        for method, label in (('command', 'worker.command'),
                              ('_command_serial', 'worker.command_serial')):
            function = methods.get(method)
            if type(function) is types.FunctionType:
                codes.append((function.__code__, label))
    for module_name, class_name, method, label in _READER_FRAME_SOURCES:
        module = sys.modules.get(module_name)
        if type(module) is not types.ModuleType:
            continue
        namespace = vars(module)
        if class_name is not None:
            owner = namespace.get(class_name)
            if type(owner) is not type:
                continue
            namespace = vars(owner)
        function = namespace.get(method)
        # Only this approved static method has a descriptor to unwrap. Never
        # invoke arbitrary descriptors or inspect a shared decorator wrapper.
        if label == 'storage.atomic' and type(function) is staticmethod:
            function = function.__func__
        if type(function) is types.FunctionType:
            codes.append((function.__code__, label))
    return codes


def _command_number(value):
    return type(value) is int and -(2**31) <= value < 2**32


def command_failure_facts(error):
    """Return bounded facts, never exception prose, paths, arguments or locals.

    At most four causes/unsuppressed contexts and 128 traceback nodes per error
    are inspected. Only five approved frames per error are retained, in traceback
    order; offsets are source lines relative to the approved code's first line.
    Any lost chain/frame or diagnostic failure sets truncated. No input is retried.
    """
    try:
        # The base descriptors bypass even hostile subclass properties.
        BaseException.__traceback__.__get__(error)
        codes = _command_frame_codes()
        rows, seen, truncated = [], set(), False
        while error is not None and id(error) not in seen and len(rows) < _COMMAND_MAX_ERRORS:
            seen.add(id(error))
            kind = type(error)
            label = next((label for known, label in _COMMAND_ERROR_CLASSES if kind is known), 'other')
            row = {'class': label, 'frames': []}
            if label in _COMMAND_OS_LABELS:
                for key in ('errno', 'winerror'):
                    try:
                        number = BaseException.__getattribute__(error, key)
                    except AttributeError:
                        continue
                    if _command_number(number):
                        row[key] = number
            if kind is subprocess.CalledProcessError:
                number = BaseException.__getattribute__(error, '__dict__').get('returncode')
                if _command_number(number):
                    row['returncode'] = number
            frame = BaseException.__traceback__.__get__(error)
            for _ in range(_COMMAND_FRAME_SCAN):
                if frame is None:
                    break
                code = frame.tb_frame.f_code
                source = next((label for known, label in codes if code is known), None)
                if source is not None:
                    offset = frame.tb_lineno - code.co_firstlineno
                    if 0 <= offset <= 1_000_000 and len(row['frames']) < _COMMAND_MAX_FRAMES:
                        row['frames'].append({'source': source, 'offset': offset})
                    else:
                        truncated = True
                frame = frame.tb_next
            if frame is not None:
                truncated = True
            rows.append(row)
            cause = BaseException.__cause__.__get__(error)
            error = cause if cause is not None else (
                None if BaseException.__suppress_context__.__get__(error)
                else BaseException.__context__.__get__(error))
        return {'errors': rows, 'truncated': truncated or error is not None}
    except BaseException:
        return {'errors': [], 'truncated': True}


def _command_keys(value, required, optional=()):
    # Reject large/subclassed containers and hostile keys before lookup or copy.
    return (type(value) is dict and len(required) <= len(value) <= len(required) + len(optional)
            and all(type(key) is str and len(key) <= 32
                    and (key in required or key in optional) for key in value)
            and all(key in value for key in required))


def validate_command_failure_facts(value):
    """Return a deep, closed validated copy, or None; never format bad input."""
    try:
        if not _command_keys(value, ('errors', 'truncated')):
            return None
        errors, truncated = value['errors'], value['truncated']
        if type(errors) is not list or len(errors) > _COMMAND_MAX_ERRORS or type(truncated) is not bool:
            return None
        rows = []
        for error in errors:
            if not _command_keys(error, ('class', 'frames'), ('errno', 'winerror', 'returncode')):
                return None
            label, frames = error['class'], error['frames']
            if type(label) is not str or len(label) > 32 or label not in _COMMAND_CLASS_LABELS:
                return None
            if type(frames) is not list or len(frames) > _COMMAND_MAX_FRAMES:
                return None
            row = {'class': label, 'frames': []}
            for key in ('errno', 'winerror', 'returncode'):
                if key in error:
                    if not _command_number(error[key]):
                        return None
                    if (key == 'returncode' and label != 'CalledProcessError'
                            or key != 'returncode' and label not in _COMMAND_OS_LABELS):
                        return None
                    row[key] = error[key]
            for frame in frames:
                if not _command_keys(frame, ('source', 'offset')):
                    return None
                source, offset = frame['source'], frame['offset']
                if (type(source) is not str or len(source) > 32 or source not in _COMMAND_FRAME_LABELS
                        or type(offset) is not int or not 0 <= offset <= 1_000_000):
                    return None
                row['frames'].append({'source': source, 'offset': offset})
            rows.append(row)
        result = {'errors': rows, 'truncated': truncated}
        # Serialize only the bounded, rebuilt tree, never the untrusted input.
        return result if len(json.dumps(result).encode('utf-8')) <= _COMMAND_MAX_BYTES else None
    except BaseException:
        return None


_READER_ENUMS = {
    'reason': ('eof', 'exception', 'cancelled'),
    'phase': ('read', 'dispatch', 'wait_exit', 'report_exit', 'settle_execution'),
    'frame': ('none', 'ready', 'reply', 'progress', 'status', 'error', 'execution', 'bridge', 'other'),
    'stopReason': ('none', 'stopped', 'configuration', 'shutdown', 'retention', 'other'),
    'cleanup': ('not_requested', 'unconfirmed', 'confirmed', 'failed'),
}


def _validated_reader(value):
    """Copy a closed termination snapshot, not an input/outcome assertion."""
    if not _command_keys(value, ('reason', 'phase', 'frame', 'exitObserved', 'exitCode',
                                 'closing', 'stopReason', 'cleanup')):
        return None
    result = {}
    for key, choices in _READER_ENUMS.items():
        item = value[key]
        if type(item) is not str or len(item) > 32 or item not in choices:
            return None
        result[key] = item
    for key in ('exitObserved', 'closing'):
        if type(value[key]) is not bool:
            return None
        result[key] = value[key]
    code = value['exitCode']
    if code is not None and (not _command_number(code) or not result['exitObserved']):
        return None
    # An observed exit with no code is possible for the Windows owner.
    result['exitCode'] = code
    return result


def validate_runtime_failure_witness(value):
    """Copy a closed causal envelope; reader facts do not prove input outcomes."""
    try:
        if not _command_keys(value, ('schema', 'origin', 'factsStatus', 'errors', 'truncated'), ('reader',)):
            return None
        if type(value['schema']) is not int or value['schema'] != 1:
            return None
        origin, status = value['origin'], value['factsStatus']
        if type(origin) is not str or len(origin) > 32 or type(status) is not str or len(status) > 32:
            return None
        if (origin, status) not in (
                ('worker_reply', 'valid'), ('worker_reply', 'invalid'),
                ('reader_disconnect', 'valid'), ('reader_disconnect', 'not_applicable')):
            return None
        reader = None
        if origin == 'reader_disconnect':
            reader = _validated_reader(value.get('reader'))
            if reader is None or status != ('not_applicable' if reader['reason'] == 'eof' else 'valid'):
                return None
        elif 'reader' in value:
            return None
        facts = validate_command_failure_facts({'errors': value['errors'], 'truncated': value['truncated']})
        if facts is None or status != 'valid' and (facts['errors'] or facts['truncated']):
            return None
        result = {'schema': 1, 'origin': origin, 'factsStatus': status, **facts}
        if reader is not None:
            result['reader'] = reader
        return result if len(json.dumps(result).encode('utf-8')) <= _COMMAND_MAX_BYTES else None
    except BaseException:
        return None


def runtime_failure_witness(error):
    """Read only a normal RuntimeError's validated host-attached witness."""
    if type(error) is not RuntimeError:
        return None
    try:
        return validate_runtime_failure_witness(
            BaseException.__getattribute__(error, '__dict__').get('_runtime_failure_witness'))
    except BaseException:
        return None


def _startup_exception_facts(error):
    """Closed, payload-free facts while the original exception still exists.

    Exact class identity only. Unknown/subclass names and their properties never
    become labels; exception messages, arguments, commands and frames stay private.
    """
    kinds = tuple((kind, kind.__name__) for kind in (
        Exception, RuntimeError, AssertionError, ValueError, TypeError, KeyError,
        AttributeError, ImportError, ModuleNotFoundError, OSError, PermissionError,
        FileNotFoundError, NotADirectoryError, IsADirectoryError, NotImplementedError,
        TimeoutError, asyncio.CancelledError, subprocess.TimeoutExpired,
        subprocess.CalledProcessError, UnicodeError, UnicodeDecodeError, UnicodeEncodeError,
    ))
    module = sys.modules.get('amplifier_foundation.modules.activator')
    activation = vars(module).get('ModuleActivationError') if type(module) is types.ModuleType else None
    rows, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(rows) < 4:
        seen.add(id(error))
        kind = type(error)
        label = next((label for known, label in kinds if kind is known), 'other')
        row = {'class': 'ModuleActivationError' if kind is activation else label}
        if label != 'other' and isinstance(error, OSError):
            for key in ('errno', 'winerror'):
                number = BaseException.__getattribute__(error, key) if hasattr(error, key) else None
                if type(number) is int and -(2**31) <= number < 2**32:
                    row[key] = number
        if kind is subprocess.CalledProcessError:
            number = error.returncode
            if type(number) is int and -(2**31) <= number < 2**32:
                row['returncode'] = number
        rows.append(row)
        cause = BaseException.__getattribute__(error, '__cause__')
        error = cause if cause is not None else (
            None if BaseException.__getattribute__(error, '__suppress_context__')
            else BaseException.__getattribute__(error, '__context__'))
    return {'errors': rows, 'truncated': isinstance(error, BaseException)}


def save_startup_failure(row, exit_code, *, failure=None, home=None, exception=None):
    """Save stderr, or a metadata-only receipt for a handled startup failure."""
    try:
        from .host.config import write_private
        stderr = _bounded(redact_diagnostic(''.join(row.get('stderr', []))))
        if not stderr and failure is None:
            return None
        record = {'time': datetime.now(timezone.utc).isoformat(), 'exitCode': exit_code,
                  'phase': redact_diagnostic(row.get('phase', 'runtime-setup'))[:100]}
        identity = row.get('runtime_id')
        if isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', identity):
            record['sessionId'] = identity
        if failure is not None:
            record['failure'] = _bounded(redact_diagnostic(failure), 8_000)
        if exception is not None:
            record['startupFailureFacts'] = _startup_exception_facts(exception)
        # Bound the complete file, including JSON escaping of arbitrary errors.
        header = json.dumps(record)
        stderr = _bounded(stderr, max(0, MAX_DETAIL_BYTES - len(header.encode('utf-8')) - 2))
        root = Path(home) if home is not None else _home()
        path = root / 'logs' / 'workers' / f'startup-{uuid.uuid4().hex}.log'
        write_private(path, header + '\n\n' + stderr, owned_root=root)
        return path
    except BaseException:
        return None  # Diagnostics must never replace the original startup error.


class StartupCapture(logging.Handler):
    """Capture the original Core validation error before safe metadata replaces it.

    No prompt/config dumps or frame locals are collected. Core's loader error
    includes the failed contract check and underlying exception. If INFO logging
    is already enabled, mount source lines are retained too. This handler never
    changes application log levels and is removed once startup finishes.
    """
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []
        self._loggers = [logging.getLogger(name) for name in ('amplifier_core', 'amplifier_foundation')]
        for logger in self._loggers:
            logger.addHandler(self)

    def emit(self, record):
        try:
            message = record.getMessage()
            if record.levelno < logging.WARNING and '[module:mount]' not in message:
                return
            self.lines.append(_bounded(redact_diagnostic(f'{record.name}: {message}\n')))
            self.lines = [_bounded(''.join(self.lines))]
        except Exception:
            pass

    def save(self, exc, session_id):
        try:
            # Traceback locations only: source lines and locals can contain secrets.
            locations = [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                         for frame in traceback.extract_tb(exc.__traceback__)[-20:]]
            detail = json.dumps({'errorType': type(exc).__name__,
                                 'message': redact_diagnostic(exc), 'frames': locations})
            return save_startup_failure({'stderr': self.lines, 'runtime_id': session_id,
                                         'phase': 'session-mount'}, None, failure=detail, exception=exc)
        except BaseException:
            return None

    def close(self):
        for logger in self._loggers:
            logger.removeHandler(self)
        super().close()
