"""Bounded, redacted startup receipts. Their contents never enter public state."""
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
    text = str(value)
    for name, secret in os.environ.items():
        if _SECRET_NAME.search(name) and len(secret) >= 4:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', text)
    text = re.sub(r'(?i)(bearer\s+)[^\s"\']+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    text = _SECRET_ASSIGNMENT.sub(r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b', '[REDACTED]', text)
    return text


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
