"""Standalone, not yet native-qualified Windows owned-process primitive.

API: await WindowsOwnedProcess.start(argv, cwd=..., env=..., trusted_python=...)
then use .stdin/.stdout/.stderr (asyncio binary pipes), .identity, await .wait()
or await .close(). close is forceful, finite and idempotent, not graceful cancel.
Normal wait preserves unread output. Force close may discard unread OS-pipe
output and reports output_discarded (potential truncation, not measured loss);
already buffered bytes remain readable.
wait/close require zero active job processes AND finalized I/O transports.
Cleanup failure retains the owner/handles and blocks further starts in this
module; never infer that an external effect was reversed by terminating a job.

Trust precondition: Python (including its DLLs/stdlib/configuration), its directory,
and this shipped guard file must be host-controlled, not workspace-supplied or
modifiable by the payload. -I -S and a minimal bootstrap environment exclude
workspace/site startup; this module does not establish filesystem/ACL trust.
Job containment covers CreateProcess descendants, not external brokers/services.
trusted_python must explicitly name the running host's sys._base_executable
(same file). A Windows venv Scripts/python.exe is a redirector, not a direct
guard interpreter. No path is substituted or discovered from payload/venv config.
Runtime contract: CPython 3.13 only (matching this app's Python requirement).
3.11/3.12, 3.14+, and other implementations are rejected before native allocation;
the private I/O lifetime bridge is not claimed compatible with those runtimes.

Win32 contracts: learn.microsoft.com/en-us/windows/win32/procthread/job-objects
and api/{jobapi2,winnt,processthreadsapi}. Uses no job_list attribute: CPython
3.13's subprocess STARTUPINFO supports handle_list, not creation-time JOB_LIST.
"""

import asyncio
import ctypes as c
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys

from . import windows_launch_guard as guard


DWORD = c.c_uint32
BOOL = c.c_int32
HANDLE = c.c_void_p
KILL_ON_CLOSE = 0x2000


class _BasicLimit(c.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", c.c_int64), ("PerJobUserTimeLimit", c.c_int64),
        ("LimitFlags", DWORD), ("MinimumWorkingSetSize", c.c_size_t),
        ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", DWORD),
        ("Affinity", c.c_size_t), ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class _IOCounters(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimit(c.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit), ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
        ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t),
    ]


class _Accounting(c.Structure):
    _fields_ = [(name, c.c_int64) for name in (
        "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime",
        "ThisPeriodTotalKernelTime",
    )] + [(name, DWORD) for name in (
        "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses",
        "TotalTerminatedProcesses",
    )]


class _Native:
    def __init__(self):
        if os.name != "nt":
            raise OSError("native Windows Job Objects required")
        self.k = c.WinDLL("kernel32", use_last_error=True)
        self.unclosed_handles = []
        signatures = {
            "CreateJobObjectW": ([HANDLE, c.c_wchar_p], HANDLE),
            "SetInformationJobObject": ([HANDLE, c.c_int32, HANDLE, DWORD], BOOL),
            "QueryInformationJobObject": (
                [HANDLE, c.c_int32, HANDLE, DWORD, c.POINTER(DWORD)], BOOL),
            "SetHandleInformation": ([HANDLE, DWORD, DWORD], BOOL),
            "GetHandleInformation": ([HANDLE, c.POINTER(DWORD)], BOOL),
            "OpenProcess": ([DWORD, BOOL, DWORD], HANDLE),
            "AssignProcessToJobObject": ([HANDLE, HANDLE], BOOL),
            "IsProcessInJob": ([HANDLE, HANDLE, c.POINTER(BOOL)], BOOL),
            "WaitForSingleObject": ([HANDLE, DWORD], DWORD),
            "GetExitCodeProcess": ([HANDLE, c.POINTER(DWORD)], BOOL),
            "TerminateProcess": ([HANDLE, DWORD], BOOL),
            "TerminateJobObject": ([HANDLE, DWORD], BOOL),
            "CloseHandle": ([HANDLE], BOOL),
            "GetWindowsDirectoryW": ([c.c_wchar_p, DWORD], DWORD),
        }
        for name, (args, result) in signatures.items():
            func = getattr(self.k, name)
            func.argtypes, func.restype = args, result

    @staticmethod
    def check(ok):
        if not ok:
            raise c.WinError(c.get_last_error())
        return ok

    def configure(self, job):
        # NULL security attributes already mean non-inheritable; set and query
        # independently. The handle must NEVER enter any child's handle_list.
        self.check(self.k.SetHandleInformation(job, 1, 0))
        flags = DWORD()
        self.check(self.k.GetHandleInformation(job, c.byref(flags)))
        if flags.value & 1:
            raise OSError("job handle is inheritable")
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = KILL_ON_CLOSE
        self.check(self.k.SetInformationJobObject(
            job, 9, c.byref(limits), c.sizeof(limits)))
        actual = self.query(job, 9, _ExtendedLimit)
        if actual.BasicLimitInformation.LimitFlags != KILL_ON_CLOSE:
            raise OSError("job limits not verified; breakaway forbidden")

    def query(self, job, kind, cls):
        value, length = cls(), DWORD()
        self.check(self.k.QueryInformationJobObject(
            job, kind, c.byref(value), c.sizeof(value), c.byref(length)))
        if length.value != c.sizeof(value):
            raise OSError("unexpected job information ABI length")
        return value

    def active(self, job):
        return self.query(job, 1, _Accounting).ActiveProcesses

    def exited(self, handle):
        status = self.k.WaitForSingleObject(handle, 0)
        if status not in (0, 258):
            raise OSError("unexpected process wait result", status)
        return status == 0

    def open_guard(self, pid, created):
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("invalid guard PID")
        # SET_QUOTA | TERMINATE | QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        handle = self.check(self.k.OpenProcess(0x00101101, False, pid))
        try:
            if guard.creation_time(self.k, handle) != created or self.exited(handle):
                raise OSError("guard creation identity changed or exited")
        except BaseException:
            try:
                self.check(self.k.CloseHandle(handle))
            except BaseException:
                # This handle is NOT identity-authorized for termination.
                # Preserve it as counterevidence to successful owner cleanup.
                self.unclosed_handles.append(handle)
                raise
            raise
        return handle

    def member(self, job, process):
        value = BOOL()
        self.check(self.k.IsProcessInJob(process, job, c.byref(value)))
        return bool(value.value)

    def bootstrap_env(self):
        directory = c.create_unicode_buffer(32768)
        size = self.k.GetWindowsDirectoryW(directory, len(directory))
        if not 0 < size < len(directory):
            raise OSError("cannot resolve trusted Windows directory")
        return {"SystemRoot": directory.value}


@dataclass(frozen=True)
class ProcessIdentity:
    guard_pid: int
    guard_created: int
    payload_pid: int | None = None
    payload_created: int | None = None


@dataclass(frozen=True)
class ProcessResult:
    payload_returncode: int | None
    guard_returncode: int | None
    active_processes: int  # Always observed zero, never merely requested.
    output_discarded: bool = False


class CleanupError(RuntimeError):
    def __init__(self, owner):
        super().__init__("job cleanup unconfirmed; owner retained, new admission blocked")
        self.owner = owner


# Retain kill-on-close lifelines on failed cleanup, even if a caller drops its
# exception. No scheduler/restart/recovery policy is implemented here.
_failed_owners = []


async def _settle(task):
    """Do not orphan an operation on repeated cancellation; re-raise afterwards."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be finite seconds")
    if not math.isfinite(value) or not 0 < value <= guard.MAX_GATE_SECONDS:
        raise ValueError("timeout must be finite and at most 30 seconds")
    return float(value)


def _host_base_python(supplied):
    """Qualify the explicit host path without executing a probe or following cfg.

    CPython 3.13.11 PC/venvlauncher.c creates a second process; Modules/getpath.py
    sets base_executable from real_executable before the launcher override.
    This narrow contract supports a venv HOST via its host-controlled base
    executable, not arbitrary Python installs or payload-provided interpreters.
    It is not an ACL, binary-signature, or installation-integrity check.
    """
    python = Path(supplied)
    base = getattr(sys, "_base_executable", None)
    if (not python.is_absolute() or not python.is_file()
            or (os.name == "nt" and python.suffix.lower() != ".exe")
            or not base or not Path(base).is_absolute() or not python.samefile(base)):
        raise ValueError("trusted_python must explicitly name the host base interpreter "
                         "(sys._base_executable), not a venv redirector")
    return python  # Preserve the supplied path; never silently substitute base.


def _require_runtime():
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13):
        raise RuntimeError("Windows owned-process requires CPython 3.13; "
                           "the asyncio lifetime bridge is version-specific")


class WindowsOwnedProcess:
    """One finite launch; retain this object until wait/close has settled."""

    def __init__(self, native, gate_seconds, cleanup_seconds):
        self._native = native
        self._gate_seconds, self._cleanup_seconds = gate_seconds, cleanup_seconds
        self._job = self._guard_handle = self._control = None
        self._process = self._monitor_task = self._finalizer = None
        self._buffer = bytearray()
        self._assigned = False
        self._abort = False
        self._force_close = False
        self._payload_returncode = None
        self.identity = None
        self.tree_empty = False
        self.io_finalized = False
        self.output_discarded = False
        self.state = "starting"

    @classmethod
    async def start(cls, argv, *, cwd, env, trusted_python,
                    gate_timeout=10.0, cleanup_timeout=5.0):
        """Start exactly once; inputs are copied before the first await.

        trusted_python must explicitly be the host-controlled sys._base_executable,
        NOT a Windows venv redirector or an interpreter from a workspace.
        Payload argv may still name its exact venv executable AFTER GO.
        Binary payload pipes are exposed unchanged only after identity,
        assignment and GO. The host must be CPython 3.13.
        """
        if _failed_owners:
            raise CleanupError(_failed_owners[0])
        _require_runtime()
        config = {"argv": list(argv), "cwd": os.fspath(cwd), "env": dict(env)}
        guard.validate_payload(config)
        python = _host_base_python(trusted_python)
        gate_seconds, cleanup_seconds = _seconds(gate_timeout), _seconds(cleanup_timeout)
        config["go"] = secrets.token_hex(32)
        body = json.dumps(config, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        if len(body) > guard.MAX_CONFIG_BYTES:
            raise ValueError("gate frame exceeds limit")
        owner = cls(_Native(), gate_seconds, cleanup_seconds)
        task = asyncio.create_task(owner._start(python, config["go"], body))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            owner._abort = True
            # Spawn must settle before cleanup: cancelling create_subprocess_exec
            # itself can lose the just-created child/transport.
            try:
                await _settle(task)
            finally:
                await owner.close()
            raise

    async def _start(self, python, token, body):
        import msvcrt

        write_fd = None
        try:
            k = self._native.k
            self._job = self._native.check(k.CreateJobObjectW(None, None))
            self._native.configure(self._job)  # Entirely BEFORE guard creation.
            self._control, write_fd = os.pipe()
            os.set_blocking(self._control, False)
            os.set_handle_inheritable(msvcrt.get_osfhandle(write_fd), True)
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [msvcrt.get_osfhandle(write_fd)]}
            try:
                self._process = await asyncio.create_subprocess_exec(
                    str(python), "-I", "-S",
                    str(Path(__file__).resolve().with_name("windows_launch_guard.py")),
                    str(msvcrt.get_osfhandle(write_fd)), token, str(self._gate_seconds),
                    executable=str(python), cwd=str(python.parent),
                    env=self._native.bootstrap_env(), close_fds=True,
                    startupinfo=startup, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            finally:
                os.close(write_fd)
                write_fd = None
            deadline = asyncio.get_running_loop().time() + self._gate_seconds
            ready = await self._status(deadline)
            if (ready.get("event") != "ready"
                    or type(ready.get("pid")) is not int
                    or ready["pid"] != self._process.pid
                    or type(ready.get("created")) is not int
                    or ready["created"] <= 0):
                raise OSError("guard handshake PID mismatch")
            self._guard_handle = self._native.open_guard(ready["pid"], ready["created"])
            self.identity = ProcessIdentity(ready["pid"], ready["created"])
            self._native.check(k.AssignProcessToJobObject(self._job, self._guard_handle))
            self._assigned = True  # Even if the subsequent verification fails.
            if not self._native.member(self._job, self._guard_handle):
                raise OSError("guard job membership not verified")
            if self._abort:
                raise asyncio.CancelledError
            self._process.stdin.write(struct.pack("!I", len(body)) + body)
            await asyncio.wait_for(self._process.stdin.drain(), self._gate_seconds)
            started = await self._status(deadline)
            if (started.get("event") != "started"
                    or type(started.get("pid")) is not int
                    or not 0 < started["pid"] <= 0xFFFFFFFF
                    or type(started.get("created")) is not int
                    or started["created"] <= 0):
                raise OSError("invalid payload identity")
            self.identity = ProcessIdentity(
                ready["pid"], ready["created"], started["pid"], started["created"])
            self.state = "running"
            self._monitor_task = asyncio.create_task(self._monitor())
            # Retrieve background errors even if the user never calls wait.
            self._monitor_task.add_done_callback(
                lambda t: None if t.cancelled() else t.exception())
            return self
        except BaseException:
            if write_fd is not None:
                os.close(write_fd)
            await self.close()
            raise

    @property
    def stdin(self):
        return self._process.stdin

    @property
    def stdout(self):
        return self._process.stdout

    @property
    def stderr(self):
        return self._process.stderr

    def verify_member(self, pid, created):
        """Verify a live descendant's exact kernel identity in THIS retained job.

        Read-only inspection, never adoption or authority to signal a PID.
        Useful when the payload is uv/a venv redirector rather than the worker.
        No handles escape this call; failed closure enters the owner's existing
        unclosed-inspection-handle policy, preventing successful cleanup.
        """
        if self.state != "running" or self._job is None or self._finalizer is not None:
            raise RuntimeError("owned job is no longer running")
        if type(created) is not int or created <= 0:
            raise ValueError("invalid process creation time")
        native = self._native
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("invalid process PID")
        # QUERY_LIMITED_INFORMATION | SYNCHRONIZE only. In particular this
        # reported identity never obtains SET_QUOTA or TERMINATE authority.
        handle = native.check(native.k.OpenProcess(0x00101000, False, pid))
        try:
            if guard.creation_time(native.k, handle) != created or native.exited(handle):
                raise OSError("reported worker creation identity changed or exited")
            if not native.member(self._job, handle):
                raise OSError("reported worker is not a member of its owned job")
        finally:
            try:
                native.check(native.k.CloseHandle(handle))
            except BaseException:
                native.unclosed_handles.append(handle)
                raise

    def _poll_status(self):
        if b"\n" not in self._buffer:
            try:
                chunk = os.read(self._control, guard.MAX_STATUS_BYTES + 1)
            except BlockingIOError:
                chunk = None
            if chunk == b"":
                raise EOFError("guard status pipe closed")
            if chunk:
                self._buffer.extend(chunk)
        end = self._buffer.find(b"\n")
        if end >= guard.MAX_STATUS_BYTES or (
                end < 0 and len(self._buffer) >= guard.MAX_STATUS_BYTES):
            raise ValueError("status frame exceeds limit")
        if end < 0:
            return None
        line = bytes(self._buffer[:end])
        del self._buffer[:end + 1]
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("invalid status frame")
        return value

    async def _status(self, deadline):
        while True:
            value = self._poll_status()
            if value is not None:
                return value
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("guard handshake timed out")
            await asyncio.sleep(0.01)

    async def _monitor(self):
        try:
            while self._finalizer is None:
                value = self._poll_status()
                if value is not None:
                    if value.get("event") != "exit" or type(value.get("returncode")) is not int:
                        raise ValueError("unexpected guard status")
                    self._payload_returncode = value["returncode"]
                    break
                if self._native.exited(self._guard_handle):
                    # One last pipe poll catches an EXIT
                    # written just before process exit, without waiting for EOF.
                    value = self._poll_status()
                    if (value is not None and value.get("event") == "exit"
                            and type(value.get("returncode")) is int):
                        self._payload_returncode = value["returncode"]
                    break
                await asyncio.sleep(0.01)
        except BaseException:
            self._force_close = True
            raise
        finally:
            await self._finish()
        return self._finalizer.result()

    async def wait(self):
        """Observe root exit and reclaim descendants, independent of pipe EOF."""
        try:
            return await asyncio.shield(self._monitor_task)
        except asyncio.CancelledError:
            await self.close()
            raise

    async def close(self):
        """Force termination and bounded pipe closure; OS-buffered output may drop.

        Can escalate an in-progress normal finalizer, but never starts a second
        one. Closed/failed results remain immutable on repeated calls.
        """
        if self._finalizer is None or not self._finalizer.done():
            self._force_close = True
        return await self._finish()

    async def _finish(self):
        if self._finalizer is None:
            self._finalizer = asyncio.create_task(self._cleanup())
        return await _settle(self._finalizer)

    async def _finalize_io(self, deadline):
        """Narrow CPython asyncio lifetime bridge, after verified tree/guard exit.

        Process has no public transport/close API, StreamReader has no close or
        wait_closed, and Process.wait can return before paused pipes disconnect.
        The private accesses here are intentionally confined: Process._transport,
        BaseSubprocessTransport._proc (cleared AFTER connection_lost), and on NT
        Popen._handle.Close (the transport's extra-info retains Popen otherwise).
        Source contracts checked against CPython v3.13.11 asyncio/{subprocess,
        base_subprocess,windows_events,proactor_events} and subprocess.Handle.
        _require_runtime restricts admission to CPython 3.13; missing private
        members fail closed. These source checks are not native qualification.
        All pipe operations use public Transport methods, not reader buffers or
        a second competing reader. No custom pipe engine or unbounded EOF wait.
        """
        if self._process is None:
            self.io_finalized = True
            return
        transport = self._process._transport
        popen = transport.get_extra_info("subprocess")
        pipes = [transport.get_pipe_transport(fd) for fd in (0, 1, 2)]
        if not hasattr(transport, "_proc") or popen is None:
            raise RuntimeError("unsupported asyncio subprocess lifetime layout")
        # close() alone flushes pending stdin indefinitely. After tree exit,
        # abort the write transport, including pending overlapped NT writes.
        if pipes[0] is not None and (
                not pipes[0].is_closing() or pipes[0].get_write_buffer_size()):
            pipes[0].abort()
        while True:
            for pipe in pipes[1:]:
                if pipe is None or pipe.is_closing():
                    continue
                if self._force_close:
                    self.output_discarded = True
                    pipe.close()
                else:
                    # All contained writers are dead. Retain only the remaining
                    # finite kernel-pipe tail in the existing StreamReaders.
                    # Resume even if reader flow control previously paused it.
                    pipe.resume_reading()
            if self._process.returncode is not None and transport._proc is None:
                transport.close()  # Cannot signal: asyncio observed process exit.
                if os.name == "nt":
                    popen._handle.Close()  # Idempotent CPython Handle, no PID kill.
                self.io_finalized = True
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("subprocess I/O finalization unconfirmed")
            await asyncio.sleep(0.01)

    async def _cleanup(self):
        try:
            if self._process is not None:
                self._process.stdin.close()
            native, k = self._native, self._native.k
            # EXIT reports the payload, not the guard's own process exit.
            # Reclaiming the job immediately can kill the trusted guard between
            # its final status write and return, manufacturing guard exit 1.
            # Only normal, confirmed payload exit earns this finite grace;
            # force close/cancellation still interrupts it. Descendants remain
            # contained and are reclaimed below, independent of pipe EOF.
            if self._payload_returncode is not None and self._guard_handle is not None:
                guard_deadline = asyncio.get_running_loop().time() + self._cleanup_seconds
                while not self._force_close and not native.exited(self._guard_handle):
                    if asyncio.get_running_loop().time() >= guard_deadline:
                        break
                    await asyncio.sleep(0.01)
            if self._assigned:
                native.check(k.TerminateJobObject(self._job, 1))
            elif self._guard_handle is not None and not native.exited(self._guard_handle):
                # Only an identity-verified retained handle, never a borrowed PID.
                native.check(k.TerminateProcess(self._guard_handle, 1))
            # Without a verified handle, EOF + the guard's independent finite
            # timer are the only permitted termination mechanism.
            deadline = asyncio.get_running_loop().time() + self._cleanup_seconds
            if self._process is not None and self._guard_handle is None:
                deadline += self._gate_seconds
            while True:
                active = native.active(self._job) if self._job is not None else 0
                exited = (
                    native.exited(self._guard_handle) if self._guard_handle is not None
                    else self._process is None or self._process.returncode is not None
                )
                if active == 0 and exited:
                    self.tree_empty = True
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("job or guard still active")
                await asyncio.sleep(0.01)
            if getattr(native, "unclosed_handles", ()):
                raise OSError("unverified inspection handle could not be closed")
            # Preserve the job lifeline and identity handles until transport
            # completion. is_closing() alone only means closure was requested.
            await self._finalize_io(deadline)
            code = None
            if self._guard_handle is not None:
                value = DWORD()
                native.check(k.GetExitCodeProcess(self._guard_handle, c.byref(value)))
                code = value.value
                native.check(k.CloseHandle(self._guard_handle))
                self._guard_handle = None
            elif self._process is not None:
                code = self._process.returncode
            if self._job is not None:
                native.check(k.CloseHandle(self._job))
                self._job = None
            if self._control is not None:
                os.close(self._control)
                self._control = None
            self.state = "closed"
            return ProcessResult(self._payload_returncode, code, active, self.output_discarded)
        except BaseException as exc:
            self.state = "cleanup_failed"
            _failed_owners.append(self)
            raise CleanupError(self) from exc