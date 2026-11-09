"""Stdlib-only checks. Fake-kernel/POSIX results are NOT Windows qualification."""

import asyncio
import ctypes as c
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from amplifier_web import windows_jobs as jobs
from amplifier_web import windows_launch_guard as guard


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "amplifier_web/windows_launch_guard.py"
PYTHON = str(Path(sys.executable).absolute())
BASE_PYTHON = sys._base_executable


def frame(config):
    body = json.dumps(config).encode("utf-8")
    return struct.pack("!I", len(body)) + body


def config(code="pass", **changes):
    value = {"go": "one-use", "argv": [PYTHON, "-I", "-S", "-c", code],
             "cwd": str(Path(PYTHON).parent), "env": {}}
    value.update(changes)
    return value


def kernel():
    """Strict Win32 stubs with real ctypes outputs, not ctypes.wintypes on LP64."""
    k = SimpleNamespace(**{name: Mock(return_value=1) for name in (
        "CreateJobObjectW", "SetInformationJobObject", "QueryInformationJobObject",
        "SetHandleInformation", "GetHandleInformation", "OpenProcess",
        "AssignProcessToJobObject", "IsProcessInJob", "WaitForSingleObject",
        "GetExitCodeProcess", "TerminateProcess", "TerminateJobObject",
        "CloseHandle", "GetWindowsDirectoryW", "GetProcessTimes",
    )})
    k.CreateJobObjectW.return_value = 0x100000001
    k.OpenProcess.return_value = 0x200000002
    k.WaitForSingleObject.return_value = 258

    def query(job, kind, output, size, length):
        length._obj.value = size
        if kind == 9:
            output._obj.BasicLimitInformation.LimitFlags = jobs.KILL_ON_CLOSE
        else:
            output._obj.ActiveProcesses = 0
        return 1

    k.QueryInformationJobObject.side_effect = query

    def times(handle, created, *unused):
        created._obj[0], created._obj[1] = 17, 2
        return 1

    k.GetProcessTimes.side_effect = times
    return k


class ABITests(unittest.TestCase):
    def native(self):
        k = kernel()
        self.enterContext(patch.object(jobs.os, "name", "nt"))
        self.enterContext(patch.object(c, "WinDLL", return_value=k, create=True))
        self.enterContext(patch.object(c, "get_last_error", return_value=5, create=True))
        self.enterContext(patch.object(c, "WinError", side_effect=lambda e: OSError(e), create=True))
        return jobs._Native(), k

    def test_fixed_width_abi_layout(self):
        self.assertEqual(c.sizeof(jobs.DWORD), 4)
        self.assertEqual(c.sizeof(jobs.BOOL), 4)
        self.assertEqual(c.sizeof(jobs._Accounting), 48)
        self.assertEqual(jobs._Accounting.ActiveProcesses.offset, 40)
        self.assertEqual(c.sizeof(jobs._IOCounters), 48)
        if c.sizeof(c.c_void_p) == 8:
            self.assertEqual(c.sizeof(jobs._BasicLimit), 64)
            self.assertEqual(jobs._BasicLimit.MinimumWorkingSetSize.offset, 24)
            self.assertEqual(jobs._BasicLimit.Affinity.offset, 48)
            self.assertEqual(c.sizeof(jobs._ExtendedLimit), 144)
            self.assertEqual(jobs._ExtendedLimit.ProcessMemoryLimit.offset, 112)

    def test_public_member_verification_is_exact_read_only_and_closes_handle(self):
        native, k = self.native()
        owner = jobs.WindowsOwnedProcess(native, 1, 1)
        owner._job, owner.state = 41, "running"
        def member(process, job, value):
            value._obj.value = 1
            return 1
        k.IsProcessInJob.side_effect = member
        created = 17 | (2 << 32)
        owner.verify_member(123, created)
        k.OpenProcess.assert_called_once_with(0x00101000, False, 123)
        k.CloseHandle.assert_called_once_with(k.OpenProcess.return_value)
        k.TerminateProcess.assert_not_called()
        k.AssignProcessToJobObject.assert_not_called()
        k.TerminateJobObject.assert_not_called()
        k.CloseHandle.reset_mock()
        with self.assertRaisesRegex(OSError, "identity"):
            owner.verify_member(123, created + 1)
        k.CloseHandle.assert_called_once()
        k.IsProcessInJob.side_effect = lambda *args: 1  # leaves FALSE
        with self.assertRaisesRegex(OSError, "not a member"):
            owner.verify_member(123, created)
        k.WaitForSingleObject.return_value = 0
        with self.assertRaisesRegex(OSError, "exited"):
            owner.verify_member(123, created)
        for pid, timestamp in [(True, created), (0, created), (123, True), (123, 0)]:
            with self.subTest(pid=pid, timestamp=timestamp), self.assertRaises(ValueError):
                owner.verify_member(pid, timestamp)
        owner.state = "closed"
        with self.assertRaisesRegex(RuntimeError, "no longer running"):
            owner.verify_member(123, created)

    def test_public_member_failed_handle_close_remains_cleanup_counterevidence(self):
        native, k = self.native()
        owner = jobs.WindowsOwnedProcess(native, 1, 1)
        owner._job, owner.state = 41, "running"
        k.CloseHandle.return_value = 0
        with self.assertRaises(OSError):
            owner.verify_member(123, 18 | (2 << 32))
        self.assertEqual(native.unclosed_handles, [k.OpenProcess.return_value])

    def test_job_checked_noninherit_and_no_breakaway(self):
        native, k = self.native()
        native.configure(0x100000001)
        k.SetHandleInformation.assert_called_once_with(0x100000001, 1, 0)
        self.assertEqual(k.SetInformationJobObject.call_args.args[1], 9)
        self.assertEqual(
            k.SetInformationJobObject.call_args.args[2]._obj.BasicLimitInformation.LimitFlags,
            jobs.KILL_ON_CLOSE)
        self.assertIs(k.CreateJobObjectW.restype, c.c_void_p)
        self.assertEqual(k.AssignProcessToJobObject.argtypes, [c.c_void_p, c.c_void_p])
        self.assertEqual(k.QueryInformationJobObject.argtypes[-1], c.POINTER(c.c_uint32))
        self.assertEqual(native.active(0x100000001), 0)

    def test_query_length_limits_and_inherit_fail_closed(self):
        native, k = self.native()
        real_query = k.QueryInformationJobObject.side_effect

        def wrong_limits(*args):
            real_query(*args)
            args[2]._obj.BasicLimitInformation.LimitFlags |= 0x800
            return 1

        k.QueryInformationJobObject.side_effect = wrong_limits
        with self.assertRaisesRegex(OSError, "limits"):
            native.configure(1)
        k.QueryInformationJobObject.side_effect = lambda *args: 1
        with self.assertRaisesRegex(OSError, "ABI length"):
            native.configure(1)

        def inherited(handle, flags):
            flags._obj.value = 1
            return 1

        k.GetHandleInformation.side_effect = inherited
        with self.assertRaisesRegex(OSError, "inheritable"):
            native.configure(1)

    def test_handle_identity_exact_and_never_signals_reused_pid(self):
        native, k = self.native()
        ticks = 17 + (2 << 32)
        self.assertEqual(native.open_guard(123, ticks), 0x200000002)
        k.OpenProcess.assert_called_once_with(0x00101101, False, 123)
        self.assertEqual(guard.creation_time(k, 1), ticks)
        with self.assertRaisesRegex(OSError, "identity"):
            native.open_guard(123, ticks + 1)
        k.CloseHandle.assert_called_once_with(0x200000002)
        k.TerminateProcess.assert_not_called()
        for pid in (0, -1, True, 0x100000000):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                native.open_guard(pid, ticks)

    def test_unknown_wait_and_native_failure_are_not_exited(self):
        native, k = self.native()
        for status in (42, 0xFFFFFFFF, 0x80):
            k.WaitForSingleObject.return_value = status
            with self.assertRaises(OSError):
                native.exited(1)
        k.AssignProcessToJobObject.return_value = 0
        with self.assertRaises(OSError):
            native.check(k.AssignProcessToJobObject(1, 2))

    def test_unverified_handle_close_failure_is_retained_never_signaled(self):
        native, k = self.native()
        k.CloseHandle.return_value = 0
        with self.assertRaises(OSError):
            native.open_guard(123, 999)
        self.assertEqual(native.unclosed_handles, [0x200000002])
        k.TerminateProcess.assert_not_called()


class FramingTests(unittest.TestCase):
    def pipe(self, data):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        # Fixtures here are small enough for an anonymous pipe's capacity.
        os.write(write_fd, data)
        os.close(write_fd)
        return read_fd

    def test_exact_binary_boundary_no_read_ahead(self):
        payload = b"\x00\x1a\r\n" + "snow 雪".encode()
        fd = self.pipe(frame(config()) + payload)
        self.assertEqual(guard.read_config(fd, "one-use")["go"], "one-use")
        self.assertEqual(os.read(fd, 4096), payload)

    def test_eof_partial_header_body_wrong_go_and_bounds(self):
        for data, error in (
            (b"", EOFError), (b"\0", EOFError),
            (struct.pack("!I", 10) + b"{}", EOFError),
            (struct.pack("!I", 0), ValueError),
            (struct.pack("!I", guard.MAX_CONFIG_BYTES + 1), ValueError),
            (frame(config(go="wrong")), ValueError),
            (struct.pack("!I", 2) + b"[]", ValueError),
        ):
            with self.subTest(data=data[:8]), self.assertRaises(error):
                guard.read_config(self.pipe(data), "one-use")

    def test_explicit_arguments_environment_and_cwd(self):
        for changes in (
            {"argv": ["python", "-c", "pass"]}, {"argv": []},
            {"argv": [PYTHON, "\0"]}, {"cwd": "relative"},
            {"env": {"X": 3}}, {"env": {"X": "a", "x": "b"}},
            {"env": {"X=Y": "a"}}, {"env": {"X": "\0"}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                guard.validate_payload(config(**changes))
        for seconds in (0, -1, float("inf"), float("nan"), 31, True, "1"):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                jobs._seconds(seconds)

    def test_status_partial_coalesced_bounded_and_eof(self):
        owner = jobs.WindowsOwnedProcess(None, 1, 1)
        fd = self.pipe(b'{"event":"ready"}\n{"event":"started"}\n')
        owner._control = fd
        self.assertEqual(owner._poll_status()["event"], "ready")
        self.assertEqual(owner._poll_status()["event"], "started")
        with self.assertRaises(EOFError):
            owner._poll_status()
        owner._control = self.pipe(b"x" * (guard.MAX_STATUS_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "limit"):
            owner._poll_status()


# Execute the REAL guard main on POSIX with only the Win32 sensing shim replaced.
# The actual timer, exact reads, subprocess execution and binary handoff remain
# real. These checks do not qualify Win32 inheritance, assignment or kill-on-close.
PORTABLE_GUARD = r"""
import ctypes, os, runpy, subprocess, sys, types
path, fd, seconds = sys.argv[1:]
def times(handle, created, *unused):
    created._obj[0] = 17
    return 1
k = types.SimpleNamespace(GetCurrentProcess=lambda: 1, GetProcessTimes=times,
                          OpenProcess=lambda *a: 2, CloseHandle=lambda h: 1)
ctypes.WinDLL = lambda *a, **kw: k
sys.modules['msvcrt'] = types.SimpleNamespace(
    open_osfhandle=lambda h, flags: h, setmode=lambda *a: None)
os.O_BINARY = 0
sys.argv = [path, fd, 'one-use', seconds]
runpy.run_path(path, run_name='__main__')
"""


@unittest.skipIf(os.name == "nt", "POSIX real-bootstrap shim only, not NT evidence")
class PortableGuardTests(unittest.TestCase):
    def launch(self, seconds="1", env=None):
        rd, wr = os.pipe()
        self.addCleanup(os.close, rd)
        process = subprocess.Popen(
            [PYTHON, "-I", "-S", "-c", PORTABLE_GUARD, str(GUARD), str(wr), seconds],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(wr,), cwd=Path(PYTHON).parent, env=env)
        os.close(wr)

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

        self.addCleanup(cleanup)
        # select bounds readiness; it works on POSIX anonymous pipes only.
        import select
        self.assertTrue(select.select([rd], [], [], 5)[0])
        ready = json.loads(os.read(rd, 1024))
        self.assertEqual(ready["event"], "ready")
        return process

    def test_no_payload_startup_import_before_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            (Path(directory) / "sitecustomize.py").write_text(
                f"open({str(marker)!r}, 'w').write('site')")
            code = f"open({str(marker)!r}, 'w').write('payload')"
            process = self.launch(env={"PYTHONPATH": directory, "PYTHONHOME": directory})
            # No full GO frame: no payload, even with a malicious workspace.
            process.stdin.write(frame(config(code, cwd=directory))[:4])
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=5), 124)
            self.assertFalse(marker.exists())

    def test_timeout_even_if_parent_pipe_writer_remains_open(self):
        process = self.launch("0.1")
        stray = os.dup(process.stdin.fileno())
        try:
            process.stdin.close()
            # communicate cannot flush an already-closed stream.
            process.stdin = None
            self.assertEqual(process.wait(timeout=5), 124)
        finally:
            os.close(stray)

    def test_eof_exits_without_launching(self):
        process = self.launch()
        out, err = process.communicate(timeout=5)
        self.assertEqual((process.returncode, out, err), (125, b"", b""))

    def test_real_payload_unicode_argv_environment_cwd_binary_handoff(self):
        with tempfile.TemporaryDirectory(prefix="owned process 雪 ") as directory:
            payload = b"\x00\x1a\r\n" + "tail 雪".encode()
            code = (
                "import json,os,sys; "
                "os.write(1,json.dumps([sys.argv[1:],os.getcwd(),os.environ['ONLY']],"
                "ensure_ascii=False).encode()+b'\\n'); "
                "os.write(1,sys.stdin.buffer.read()); os.write(2,b'error\\r\\n')"
            )
            process = self.launch()
            cfg = config(code, cwd=directory, env={"ONLY": "value 雪"})
            cfg["argv"].extend(["arg with spaces", 'quote " slash\\', "雪"])
            out, err = process.communicate(frame(cfg) + payload, timeout=5)
            self.assertEqual(process.returncode, 0, err)
            header, data = out.split(b"\n", 1)
            arguments, cwd, value = json.loads(header)
            self.assertEqual(arguments, cfg["argv"][5:])
            # macOS getcwd expands /var's /private/var alias. Check the actual
            # directory identity, not the temporary-directory path spelling.
            self.assertTrue(os.path.samefile(cwd, directory))
            self.assertEqual(value, "value 雪")
            self.assertEqual(data, payload)
            self.assertEqual(err, b"error\r\n")


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.object(jobs, "_failed_owners", []))
        self.k = kernel()
        self.native = SimpleNamespace(
            k=self.k, check=lambda v: v or self.fail("unexpected native failure"),
            active=Mock(return_value=0), exited=Mock(return_value=True))
        self.owner = jobs.WindowsOwnedProcess(self.native, 0.03, 0.03)
        self.owner._job = 0x100000001
        self.owner._guard_handle = 0x200000002
        self.owner._assigned = True
        self.owner._process = SimpleNamespace(stdin=Mock())
        # These tests isolate native accounting. Real-pipe tests below exercise
        # the actual asyncio lifetime bridge, rather than teaching mocks its API.
        self.owner._finalize_io = AsyncMock()

    async def test_cleanup_waits_for_active_zero_closes_once(self):
        self.native.active.side_effect = [2, 1, 0]
        result = await self.owner.close()
        self.assertEqual(result.active_processes, 0)
        self.assertEqual(self.owner.state, "closed")
        self.assertEqual(self.k.CloseHandle.call_args_list[0].args, (0x200000002,))
        self.assertEqual(await self.owner.close(), result)
        self.assertEqual(self.k.TerminateJobObject.call_count, 1)
        self.assertEqual(self.native.active.call_count, 3)

    async def test_failed_cleanup_retains_lifeline_blocks_admission_no_false_stopped(self):
        self.native.active.return_value = 1
        with self.assertRaises(jobs.CleanupError) as raised:
            await self.owner.close()
        self.assertIs(raised.exception.owner, self.owner)
        self.assertEqual(self.owner.state, "cleanup_failed")
        self.assertEqual(jobs._failed_owners, [self.owner])
        self.assertEqual(self.owner._job, 0x100000001)
        self.k.CloseHandle.assert_not_called()
        with self.assertRaises(jobs.CleanupError):
            await jobs.WindowsOwnedProcess.start([], cwd=".", env={}, trusted_python=PYTHON)
        with self.assertRaises(jobs.CleanupError):
            await self.owner.close()
        self.assertEqual(self.k.TerminateJobObject.call_count, 1)

    async def test_repeated_cancellation_shields_same_finalizer(self):
        self.owner._cleanup_seconds = 1
        self.native.active.side_effect = [1, 1, 1, 1, 0]
        closing = asyncio.create_task(self.owner.close())
        for _ in range(4):
            await asyncio.sleep(0.005)
            closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertEqual(self.owner.state, "closed")
        self.assertEqual(self.k.TerminateJobObject.call_count, 1)

    async def test_cancelled_wait_kills_rather_than_waiting_for_long_root(self):
        self.owner._monitor_task = asyncio.create_task(asyncio.sleep(60))
        task = asyncio.create_task(self.owner.wait())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.owner.state, "closed")
        self.owner._monitor_task.cancel()
        await asyncio.gather(self.owner._monitor_task, return_exceptions=True)

    async def test_monitor_root_exit_does_not_wait_for_stdout_eof(self):
        self.owner._poll_status = Mock(return_value={"event": "exit", "returncode": 0})
        self.owner._process.stdout = Mock()
        result = await self.owner._monitor()
        self.assertEqual(result.payload_returncode, 0)
        self.owner._process.stdout.assert_not_called()
        self.assertEqual(self.k.TerminateJobObject.call_count, 1)

    async def test_normal_exit_does_not_kill_guard_between_exit_frame_and_exit(self):
        self.owner._payload_returncode = 0
        exited = False
        self.native.exited.side_effect = lambda handle: exited
        self.native.active.side_effect = lambda job: 0 if exited else 1
        def terminate(job, code):
            self.assertTrue(exited, "normal cleanup killed the guard after its EXIT frame")
            return 1
        self.k.TerminateJobObject.side_effect = terminate
        async def finish_guard():
            nonlocal exited
            await asyncio.sleep(0.01)
            exited = True
        finishing = asyncio.create_task(finish_guard())
        result = await self.owner._finish()
        await finishing
        self.assertEqual(result.payload_returncode, 0)
        self.k.TerminateJobObject.assert_called_once()

    async def test_stalled_guard_after_exit_frame_is_still_bounded_and_reclaimed(self):
        self.owner._payload_returncode = 0
        exited = False
        self.native.exited.side_effect = lambda handle: exited
        def terminate(job, code):
            nonlocal exited
            exited = True
            return 1
        self.k.TerminateJobObject.side_effect = terminate
        start = asyncio.get_running_loop().time()
        await self.owner._finish()
        elapsed = asyncio.get_running_loop().time() - start
        self.assertGreaterEqual(elapsed, self.owner._cleanup_seconds)
        self.assertLess(elapsed, 1)
        self.k.TerminateJobObject.assert_called_once()

    async def test_force_close_interrupts_normal_guard_exit_grace(self):
        self.owner._payload_returncode = 0
        self.owner._cleanup_seconds = 10
        exited = False
        self.native.exited.side_effect = lambda handle: exited
        def terminate(job, code):
            nonlocal exited
            exited = True
            return 1
        self.k.TerminateJobObject.side_effect = terminate
        finishing = asyncio.create_task(self.owner._finish())
        await asyncio.sleep(0.01)
        self.k.TerminateJobObject.assert_not_called()
        await asyncio.wait_for(self.owner.close(), 1)
        await finishing
        self.k.TerminateJobObject.assert_called_once()

    async def test_query_or_termination_failure_retains_owner(self):
        for target in ("query", "termination", "close"):
            with self.subTest(target=target):
                owner = jobs.WindowsOwnedProcess(self.native, 0.03, 0.03)
                owner._finalize_io = AsyncMock()
                owner._job, owner._assigned = 123, True
                if target == "query":
                    self.native.active.side_effect = OSError("query")
                elif target == "termination":
                    self.k.TerminateJobObject.side_effect = OSError("terminate")
                else:
                    self.k.CloseHandle.side_effect = OSError("close")
                with self.assertRaises(jobs.CleanupError):
                    await owner.close()
                self.assertEqual(owner._job, 123)
                self.native.active.side_effect = None
                self.k.TerminateJobObject.side_effect = None
                self.k.CloseHandle.side_effect = None

    async def test_unclosed_unverified_handle_blocks_success_without_pid_signaling(self):
        self.native.unclosed_handles = [987]
        with self.assertRaises(jobs.CleanupError):
            await self.owner.close()
        self.assertEqual(self.owner.state, "cleanup_failed")
        self.k.TerminateProcess.assert_not_called()
        self.k.CloseHandle.assert_not_called()


class LaunchOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.object(jobs, "_failed_owners", []))
        self.k = kernel()
        self.events = []
        self.process = SimpleNamespace(
            pid=321, returncode=0, stdin=SimpleNamespace(
                write=Mock(side_effect=lambda data: self.events.append("GO")),
                drain=AsyncMock(), close=Mock()),
            stdout=object(), stderr=object())
        self.native = SimpleNamespace(
            k=self.k, check=lambda v: v or self.fail("native failure"),
            configure=Mock(side_effect=lambda h: self.events.append("configured")),
            open_guard=Mock(side_effect=lambda *a: self.events.append("identity") or 2),
            member=Mock(side_effect=lambda *a: self.events.append("membership") or True),
            active=Mock(return_value=0), exited=Mock(return_value=True),
            bootstrap_env=Mock(return_value={"SystemRoot": "synthetic"}))
        self.k.AssignProcessToJobObject.side_effect = (
            lambda *a: self.events.append("assigned") or 1)
        self.enterContext(patch.object(jobs, "_Native", return_value=self.native))
        self.enterContext(patch.dict(sys.modules, {
            "msvcrt": SimpleNamespace(get_osfhandle=lambda fd: fd)}))
        self.enterContext(patch.object(jobs.os, "set_handle_inheritable", create=True))
        self.enterContext(patch.object(
            jobs.subprocess, "STARTUPINFO", SimpleNamespace, create=True))
        self.status = self.enterContext(patch.object(
            jobs.WindowsOwnedProcess, "_status", new_callable=AsyncMock))
        self.status.side_effect = [
            {"event": "ready", "pid": 321, "created": 111},
            {"event": "started", "pid": 654, "created": 222}]
        self.enterContext(patch.object(
            jobs.WindowsOwnedProcess, "_poll_status",
            return_value={"event": "exit", "returncode": 0}))
        self.enterContext(patch.object(
            jobs.WindowsOwnedProcess, "_finalize_io", new_callable=AsyncMock))

        async def create(*args, **kwargs):
            self.events.append("spawn")
            self.spawn_args, self.spawn_kwargs = args, kwargs
            return self.process

        self.create = self.enterContext(patch.object(
            jobs.asyncio, "create_subprocess_exec", side_effect=create))

    async def start(self):
        return await jobs.WindowsOwnedProcess.start(
            [PYTHON, "-c", "pass"], cwd=ROOT, env={"ONLY": "payload"},
            trusted_python=BASE_PYTHON, gate_timeout=1, cleanup_timeout=1)

    async def test_full_order_bootstrap_and_handle_allowlist(self):
        owner = await self.start()
        result = await owner.wait()
        self.assertEqual(self.events, [
            "configured", "spawn", "identity", "assigned", "membership", "GO"])
        self.assertEqual(self.spawn_args[:3], (BASE_PYTHON, "-I", "-S"))
        self.assertEqual(Path(self.spawn_args[3]), GUARD)
        self.assertEqual(self.spawn_kwargs["cwd"], str(Path(BASE_PYTHON).parent))
        self.assertEqual(self.spawn_kwargs["env"], {"SystemRoot": "synthetic"})
        self.assertTrue(self.spawn_kwargs["close_fds"])
        self.assertNotIn("creationflags", self.spawn_kwargs)
        handles = self.spawn_kwargs["startupinfo"].lpAttributeList
        self.assertEqual(list(handles), ["handle_list"])
        self.assertNotIn(0x100000001, handles["handle_list"])
        body = self.process.stdin.write.call_args.args[0]
        cfg = json.loads(body[4:])
        self.assertEqual(cfg["env"], {"ONLY": "payload"})
        self.assertEqual(cfg["argv"], [PYTHON, "-c", "pass"])
        self.assertEqual(owner.identity, jobs.ProcessIdentity(321, 111, 654, 222))
        self.assertEqual(result.active_processes, 0)

    async def test_configuration_failure_prevents_even_guard_spawn(self):
        self.native.configure.side_effect = OSError("set/query failure")
        with self.assertRaisesRegex(OSError, "set/query"):
            await self.start()
        self.create.assert_not_called()
        self.process.stdin.write.assert_not_called()
        self.k.CloseHandle.assert_called_once_with(0x100000001)

    async def test_redirector_or_other_interpreter_rejected_before_job_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            redirector = Path(directory) / "python.exe"
            redirector.write_bytes(b"synthetic redirector, never execute")
            with self.assertRaisesRegex(ValueError, "host base interpreter"):
                await jobs.WindowsOwnedProcess.start(
                    [PYTHON, "-c", "pass"], cwd=ROOT, env={},
                    trusted_python=redirector)
            self.create.assert_not_called()
            self.k.CreateJobObjectW.assert_not_called()

    async def test_explicit_base_is_used_without_replacing_payload_argv(self):
        owner = await jobs.WindowsOwnedProcess.start(
            [PYTHON, "-c", "pass"], cwd=ROOT, env={},
            trusted_python=BASE_PYTHON)
        await owner.wait()
        self.assertEqual(self.spawn_args[0], BASE_PYTHON)
        self.assertEqual(
            json.loads(self.process.stdin.write.call_args.args[0][4:])["argv"][0],
            PYTHON)

    async def test_missing_host_base_never_falls_back_to_supplied_python(self):
        with patch.object(sys, "_base_executable", None):
            with self.assertRaisesRegex(ValueError, "host base interpreter"):
                await self.start()
        self.create.assert_not_called()
        self.k.CreateJobObjectW.assert_not_called()

    async def test_unsupported_runtime_rejected_before_native_allocation(self):
        for version, implementation in (
            ((3, 11), "cpython"), ((3, 12), "cpython"),
            ((3, 14), "cpython"), ((3, 13), "pypy"),
        ):
            with self.subTest(version=version, implementation=implementation), patch.object(
                    sys, "version_info", version), patch.object(
                    sys, "implementation", SimpleNamespace(name=implementation)):
                with self.assertRaisesRegex(RuntimeError, "requires CPython 3.13"):
                    await self.start()
        self.create.assert_not_called()
        self.k.CreateJobObjectW.assert_not_called()

    async def test_ready_pid_mismatch_remains_fail_closed(self):
        self.status.side_effect = [{"event": "ready", "pid": 654, "created": 111}]
        with self.assertRaisesRegex(OSError, "PID mismatch"):
            await self.start()
        self.native.open_guard.assert_not_called()
        self.process.stdin.write.assert_not_called()
        self.k.AssignProcessToJobObject.assert_not_called()

    async def test_identity_assignment_or_membership_failure_never_go_no_fallback(self):
        for failure in ("identity", "assignment", "membership"):
            with self.subTest(failure=failure):
                self.status.side_effect = [{"event": "ready", "pid": 321, "created": 111}]
                if failure == "identity":
                    self.native.open_guard.side_effect = OSError("identity failed")
                elif failure == "assignment":
                    self.k.AssignProcessToJobObject.side_effect = OSError("nested job refused")
                else:
                    self.native.member.side_effect = lambda *a: False
                with self.assertRaises(OSError):
                    await self.start()
                self.process.stdin.write.assert_not_called()
                self.native.open_guard.side_effect = lambda *a: 2
                self.k.AssignProcessToJobObject.side_effect = None
                self.native.member.side_effect = None
        self.assertEqual(self.create.call_count, 3)  # One guard, no retry, per launch.

    async def test_repeated_create_cancellation_retains_spawn_until_cleanup(self):
        began, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            began.set()
            await release.wait()
            return self.process

        self.create.side_effect = delayed
        starting = asyncio.create_task(self.start())
        await began.wait()
        starting.cancel()
        await asyncio.sleep(0)
        starting.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertEqual(self.create.call_count, 1)
        self.process.stdin.write.assert_not_called()  # Cancellation before GO.
        self.k.TerminateJobObject.assert_called_once_with(0x100000001, 1)
        self.assertEqual(self.k.CloseHandle.call_count, 2)


@unittest.skipUnless(os.name == "posix", "real POSIX pipes with fake job only")
class RealPipeFinalizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.object(jobs, "_failed_owners", []))
        loop = asyncio.get_running_loop()
        errors = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: errors.append(context))

        async def check_callbacks():
            await asyncio.sleep(0)
            loop.set_exception_handler(previous)
            self.assertEqual(errors, [], "asyncio callback error during pipe finalization")

        # Runs after each test-owned fallback (cleanup stack is LIFO).
        self.addAsyncCleanup(check_callbacks)

    async def make_owner(self, code):
        process = await asyncio.create_subprocess_exec(
            PYTHON, "-I", "-S", "-B", "-c", code,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        transport = process._transport

        async def fallback():
            if process.returncode is None:
                process.kill()  # Exact unreaped test child, not PID lookup.
            transport.close()
            await asyncio.wait_for(process.wait(), 5)
            await asyncio.sleep(0)

        self.addAsyncCleanup(fallback)
        k = kernel()

        def terminate(*args):
            if process.returncode is None:
                process.kill()
            return 1

        k.TerminateJobObject.side_effect = terminate
        native = SimpleNamespace(
            k=k, check=lambda ok: ok or self.fail("native failure"),
            active=lambda job: int(process.returncode is None),
            exited=lambda handle: process.returncode is not None)
        owner = jobs.WindowsOwnedProcess(native, 1, 1)
        owner._job, owner._guard_handle, owner._assigned = 1, 2, True
        owner._process = process
        return owner, transport, k

    async def paused(self, owner):
        deadline = asyncio.get_running_loop().time() + 5
        while not (owner.stdout._paused and owner.stderr._paused):
            self.assertLess(asyncio.get_running_loop().time(), deadline,
                            "both real streams must reach flow-control pause")
            await asyncio.sleep(0.001)

    async def test_force_close_finalizes_saturated_unread_transports(self):
        code = (
            "import os,threading,time; "
            "threading.Thread(target=lambda:os.write(1,b'o'*300000)).start(); "
            "threading.Thread(target=lambda:os.write(2,b'e'*300000)).start(); "
            "time.sleep(60)"
        )
        owner, transport, k = await self.make_owner(code)
        await self.paused(owner)
        owner.stdin.write(b"x" * 300000)  # Also exercise pending stdin writes.
        result = await asyncio.wait_for(owner.close(), 3)
        self.assertTrue(transport.is_closing(), "closed owner retained process transport")
        self.assertIsNone(transport._proc, "connection_lost did not finish")
        for fd in (0, 1, 2):
            pipe = transport.get_pipe_transport(fd)
            self.assertTrue(pipe.is_closing(), fd)
            self.assertTrue(pipe.get_extra_info("pipe").closed, fd)
        self.assertTrue(owner.io_finalized)
        self.assertTrue(result.output_discarded)
        self.assertEqual(owner.state, "closed")
        self.assertEqual(result.active_processes, 0)
        self.assertEqual(k.TerminateJobObject.call_count, 1)
        self.assertEqual(await owner.close(), result)

    async def test_normal_wait_retains_unread_output_after_root_exit(self):
        # Linux pipe capacity lets this child exit with its reader already paused.
        size = 131073
        owner, transport, _ = await self.make_owner(
            f"import os,threading; "
            f"t=threading.Thread(target=lambda:os.write(1,b'o'*{size})); t.start(); "
            f"os.write(2,b'e'*{size}); t.join()")
        await self.paused(owner)
        deadline = asyncio.get_running_loop().time() + 5
        while owner._process.returncode is None:
            self.assertLess(asyncio.get_running_loop().time(), deadline)
            await asyncio.sleep(0.001)
        owner._poll_status = Mock(return_value={"event": "exit", "returncode": 0})
        owner._monitor_task = asyncio.create_task(owner._monitor())
        result = await asyncio.wait_for(owner.wait(), 3)
        self.assertTrue(transport.is_closing(), "wait left paused transport alive")
        self.assertIsNone(transport._proc)
        self.assertEqual(await owner.stdout.read(), b"o" * size)
        self.assertEqual(await owner.stderr.read(), b"e" * size)
        self.assertTrue(owner.io_finalized)
        self.assertFalse(result.output_discarded)

    async def test_real_pipe_finalization_failure_retains_owner_and_observed_flags(self):
        owner, transport, k = await self.make_owner(
            "import os,threading; "
            "t=threading.Thread(target=lambda:os.write(1,b'o'*131073)); t.start(); "
            "os.write(2,b'e'*131073); t.join()")
        await self.paused(owner)
        deadline = asyncio.get_running_loop().time() + 5
        while owner._process.returncode is None:
            self.assertLess(asyncio.get_running_loop().time(), deadline)
            await asyncio.sleep(0.001)
        owner._cleanup_seconds = 0.03
        owner._poll_status = Mock(return_value={"event": "exit", "returncode": 0})
        # Simulate a backend that cannot resume its actual paused pipe. The
        # original transport is left intact for the test-owned fallback.
        with patch.object(transport.get_pipe_transport(1), "resume_reading"), patch.object(
                transport.get_pipe_transport(2), "resume_reading"):
            owner._monitor_task = asyncio.create_task(owner._monitor())
            with self.assertRaises(jobs.CleanupError) as raised:
                await owner.wait()
        self.assertIs(raised.exception.owner, owner)
        self.assertEqual(owner.state, "cleanup_failed")
        self.assertTrue(owner.tree_empty)
        self.assertFalse(owner.io_finalized)
        self.assertFalse(owner.output_discarded)
        self.assertEqual((owner._job, owner._guard_handle), (1, 2))
        k.CloseHandle.assert_not_called()
        with self.assertRaises(jobs.CleanupError):
            await owner.close()
        self.assertEqual(k.TerminateJobObject.call_count, 1)
        with self.assertRaises(jobs.CleanupError):
            await jobs.WindowsOwnedProcess.start([], cwd=ROOT, env={}, trusted_python=BASE_PYTHON)

    async def test_saturated_close_repeated_cancellation_uses_one_io_finalizer(self):
        owner, transport, k = await self.make_owner(
            "import os,threading,time; "
            "threading.Thread(target=lambda:os.write(1,b'o'*300000)).start(); "
            "threading.Thread(target=lambda:os.write(2,b'e'*300000)).start(); time.sleep(60)")
        await self.paused(owner)
        began, release = asyncio.Event(), asyncio.Event()
        finalize = owner._finalize_io
        calls = 0

        async def delayed(deadline):
            nonlocal calls
            calls += 1
            began.set()
            await release.wait()
            await finalize(deadline)

        owner._finalize_io = delayed
        closing = asyncio.create_task(owner.close())
        await asyncio.wait_for(began.wait(), 5)
        for _ in range(3):
            closing.cancel()
            await asyncio.sleep(0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertEqual(calls, 1)
        self.assertEqual(k.TerminateJobObject.call_count, 1)
        self.assertTrue(owner.io_finalized)
        self.assertTrue(transport.is_closing())
        result = await owner.close()
        self.assertTrue(result.output_discarded)


# Run a separate, isolated owner process and kill it with os._exit at each
# interval. Finite sleepers are a last test safety bound, not cleanup evidence.
PARENT_DEATH = r"""
import asyncio, ctypes as c, json, os, sys, time
from pathlib import Path
root, phase, marker = sys.argv[1:]
sys.path.insert(0, root)
from amplifier_web import windows_jobs as j
from amplifier_web import windows_launch_guard as g
seen = []
def die():
    print(json.dumps(seen), flush=True)
    os._exit(0)
configure = j._Native.configure
def configured(self, job):
    configure(self, job)
    if phase == 'configured': die()
j._Native.configure = configured
create = asyncio.create_subprocess_exec
async def created(*args, **kw):
    proc = await create(*args, **kw)
    if phase == 'spawned':
        n = j._Native()
        h = n.check(n.k.OpenProcess(0x1000, False, proc.pid))
        seen.append([proc.pid, g.creation_time(n.k, h)])
        n.check(n.k.CloseHandle(h))
        die()
    return proc
asyncio.create_subprocess_exec = created
open_guard = j._Native.open_guard
def opened(self, pid, ticks):
    h = open_guard(self, pid, ticks)
    seen.append([pid, ticks])
    if phase == 'identity': die()
    return h
j._Native.open_guard = opened
member = j._Native.member
def assigned(self, job, process):
    yes = member(self, job, process)
    assert yes
    if phase == 'assigned': die()
    return yes
j._Native.member = assigned
status = j.WindowsOwnedProcess._status
async def gate(self, deadline):
    if phase == 'go' and self._assigned: die()
    return await status(self, deadline)
j.WindowsOwnedProcess._status = gate
payload = r'''
import ctypes as c, json, os, subprocess, sys, time
k=c.WinDLL('kernel32',use_last_error=True)
k.OpenProcess.argtypes=[c.c_uint32,c.c_int32,c.c_uint32]
k.OpenProcess.restype=c.c_void_p
k.CloseHandle.argtypes=[c.c_void_p]
k.GetProcessTimes.argtypes=[c.c_void_p]+[c.POINTER(c.c_uint32*2)]*4
k.GetProcessTimes.restype=c.c_int32
def identity(pid):
    h=k.OpenProcess(0x1000,False,pid)
    assert h
    times=[(c.c_uint32*2)() for _ in range(4)]
    assert k.GetProcessTimes(h,*(c.byref(v) for v in times))
    k.CloseHandle(h)
    return [pid,times[0][0]+(times[0][1]<<32)]
child=subprocess.Popen([sys.executable,'-I','-S','-c','import time; time.sleep(30)'])
from pathlib import Path
path=Path(sys.argv[1])
temporary=path.with_suffix('.tmp')
temporary.write_text(json.dumps([identity(os.getpid()),identity(child.pid)]))
temporary.replace(path)
time.sleep(30)
'''
async def main():
    n=j._Native()
    owner=await j.WindowsOwnedProcess.start(
        [sys.executable,'-I','-S','-c',payload,marker], cwd=str(Path(sys.executable).parent),
        env=n.bootstrap_env(), trusted_python=sys._base_executable, gate_timeout=4)
    deadline=time.monotonic()+10
    while not Path(marker).exists():
        assert time.monotonic()<deadline
        await asyncio.sleep(0.01)
    seen.extend(json.loads(Path(marker).read_text()))
    if phase == 'cleanup-failure':
        def failed_query(job):
            raise OSError('injected accounting failure after native termination')
        owner._native.active=failed_query
        try:
            await owner.close()
        except j.CleanupError as exc:
            assert exc.owner is owner
        else:
            raise AssertionError('cleanup incorrectly succeeded')
        assert owner.state=='cleanup_failed' and owner._job is not None
        try:
            await j.WindowsOwnedProcess.start([],cwd='.',env={},trusted_python=sys.executable)
        except j.CleanupError:
            pass
        else:
            raise AssertionError('new admission after failed cleanup')
    die()
asyncio.run(main())
"""


@unittest.skipUnless(os.name == "nt", "native Windows Job/handle qualification pending")
class NativeWindowsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        loop = asyncio.get_running_loop()
        errors = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: errors.append(context))

        async def check_callbacks():
            await asyncio.sleep(0)
            loop.set_exception_handler(previous)
            self.assertEqual(errors, [], "native proactor callback error")

        self.addAsyncCleanup(check_callbacks)

    async def test_venv_redirector_rejected_base_guard_preserves_venv_payload(self):
        import venv

        native = jobs._Native()
        with tempfile.TemporaryDirectory(prefix="owned venv ") as directory:
            await asyncio.to_thread(venv.EnvBuilder(with_pip=False).create, directory)
            python = str(Path(directory) / "Scripts/python.exe")
            argv = [python, "-I", "-c",
                    "import os,sys,json; print(json.dumps([os.getpid(),sys.executable,"
                    "sys.prefix]),flush=True); sys.stdin.buffer.readline()"]
            with patch.object(asyncio, "create_subprocess_exec", new_callable=AsyncMock) as spawn:
                with self.assertRaisesRegex(ValueError, "host base interpreter"):
                    await jobs.WindowsOwnedProcess.start(
                        argv, cwd=directory, env=native.bootstrap_env(), trusted_python=python)
                spawn.assert_not_called()
            owner = await jobs.WindowsOwnedProcess.start(
                argv, cwd=directory, env=native.bootstrap_env(), trusted_python=BASE_PYTHON)
            try:
                pid, executable, prefix = json.loads(
                    await asyncio.wait_for(owner.stdout.readline(), 10))
                self.assertEqual(Path(executable), Path(python))
                self.assertEqual(Path(prefix), Path(directory))
                handle = native.check(native.k.OpenProcess(0x1000, False, pid))
                try:
                    self.assertTrue(native.member(owner._job, handle))
                    created = guard.creation_time(native.k, handle)
                finally:
                    native.check(native.k.CloseHandle(handle))
                owner.verify_member(pid, created)
                with self.assertRaisesRegex(OSError, "identity"):
                    owner.verify_member(pid, created + 1)
                foreign = native.check(native.k.OpenProcess(0x1000, False, os.getpid()))
                try:
                    foreign_created = guard.creation_time(native.k, foreign)
                finally:
                    native.check(native.k.CloseHandle(foreign))
                with self.assertRaisesRegex(OSError, "not a member"):
                    owner.verify_member(os.getpid(), foreign_created)
                owner.stdin.write(b"release\n")
                await owner.stdin.drain()
                self.assertEqual((await asyncio.wait_for(owner.wait(), 10)).payload_returncode, 0)
            finally:
                await owner.close()
            with self.assertRaisesRegex(RuntimeError, "no longer running"):
                owner.verify_member(pid, created)
            # Exercise sys._base_executable supplied by an actual venv HOST,
            # not only a venv used as the already-contained payload.
            host_probe = r"""
import asyncio, os, sys
sys.path.insert(0, sys.argv[1])
from amplifier_web.windows_jobs import WindowsOwnedProcess, _Native
async def main():
    assert not os.path.samefile(sys.executable, sys._base_executable)
    args = [sys.executable, '-I', '-S', '-c', 'print("venv payload", flush=True)']
    try:
        await WindowsOwnedProcess.start(
            args, cwd=sys.argv[2], env=_Native().bootstrap_env(),
            trusted_python=sys.executable)
    except ValueError:
        pass
    else:
        raise AssertionError('host venv redirector accepted')
    owner = await WindowsOwnedProcess.start(
        args, cwd=sys.argv[2], env=_Native().bootstrap_env(),
        trusted_python=sys._base_executable)
    try:
        result = await owner.wait()
        assert result.payload_returncode == 0 and result.active_processes == 0
        assert owner.io_finalized
        assert await owner.stdout.read() == b'venv payload\r\n'
    finally:
        await owner.close()
asyncio.run(main())
"""
            proof = await asyncio.to_thread(
                subprocess.run, [python, "-I", "-S", "-B", "-c", host_probe,
                                 str(ROOT), directory],
                cwd=Path(BASE_PYTHON).parent, env=native.bootstrap_env(),
                capture_output=True, timeout=30)
            self.assertEqual(proof.returncode, 0, proof.stderr)

    async def test_saturated_close_releases_native_handles_while_owners_retained(self):
        k = c.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.argtypes = []
        k.GetCurrentProcess.restype = c.c_void_p
        k.GetProcessHandleCount.argtypes = [c.c_void_p, c.POINTER(c.c_uint32)]
        k.GetProcessHandleCount.restype = c.c_int32

        def count():
            value = c.c_uint32()
            if not k.GetProcessHandleCount(k.GetCurrentProcess(), c.byref(value)):
                raise c.WinError(c.get_last_error())
            return value.value

        # Warm the proactor's lazy process/pipe machinery before the baseline.
        warm = await self.launch("pass")
        await warm.wait()
        await asyncio.sleep(0.05)
        baseline = count()
        owners = [warm]
        for _ in range(3):
            owner = await self.launch(
                "import os,threading,time; "
                "threading.Thread(target=lambda:os.write(1,b'o'*300000)).start(); "
                "threading.Thread(target=lambda:os.write(2,b'e'*300000)).start(); time.sleep(60)")
            owners.append(owner)
            deadline = asyncio.get_running_loop().time() + 10
            while not (owner.stdout._paused and owner.stderr._paused):
                self.assertLess(asyncio.get_running_loop().time(), deadline)
                await asyncio.sleep(0.001)
            owner.stdin.write(b"x" * 300000)
            result = await asyncio.wait_for(owner.close(), 10)
            self.assertTrue(owner.io_finalized)
            self.assertTrue(result.output_discarded)
            self.assertEqual(result.active_processes, 0)
        deadline = asyncio.get_running_loop().time() + 3
        while count() > baseline and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        self.assertLessEqual(count(), baseline, "native handles retained after close")
        self.assertEqual(len(owners), 4)  # No GC/owner deletion used as cleanup.

    async def test_normal_wait_preserves_native_paused_output_and_finalizes(self):
        # One byte above limit*2: reaching pause requires reading the last byte,
        # so the writer can finish regardless of proactor read chunk sizes.
        size = 131073
        owner = await self.launch(
            "import os,threading; "
            f"t=threading.Thread(target=lambda:os.write(1,b'o'*{size})); t.start(); "
            f"os.write(2,b'e'*{size}); t.join()")
        transport = owner._process._transport
        # Read only after wait: the finalizer must resume the paused transports
        # without a competing consumer or lost buffered/tail bytes.
        result = await asyncio.wait_for(owner.wait(), 10)
        self.assertEqual(result.payload_returncode, 0)
        self.assertEqual(result.active_processes, 0)
        self.assertFalse(result.output_discarded)
        self.assertTrue(owner.io_finalized)
        self.assertTrue(transport.is_closing())
        self.assertIsNone(transport._proc)
        self.assertEqual(await owner.stdout.read(), b"o" * size)
        self.assertEqual(await owner.stderr.read(), b"e" * size)

    async def launch(self, code, *, cwd=None, env=None, arguments=()):
        owner = await jobs.WindowsOwnedProcess.start(
            [BASE_PYTHON, "-I", "-S", "-c", code, *arguments],
            cwd=cwd or str(Path(BASE_PYTHON).parent),
            env=jobs._Native().bootstrap_env() if env is None else env,
            trusted_python=BASE_PYTHON)
        self.addAsyncCleanup(owner.close)
        return owner

    async def test_immediate_membership_and_identity(self):
        code = r"""
import ctypes as c, json, os, sys
k=c.WinDLL('kernel32',use_last_error=True)
k.GetCurrentProcess.restype=c.c_void_p
k.IsProcessInJob.argtypes=[c.c_void_p,c.c_void_p,c.POINTER(c.c_int32)]
k.IsProcessInJob.restype=c.c_int32
v=c.c_int32()
assert k.IsProcessInJob(k.GetCurrentProcess(),None,c.byref(v)) and v.value
os.write(1,json.dumps({'pid':os.getpid(),'member':bool(v.value)}).encode()+b'\n')
sys.stdin.buffer.readline()
"""
        owner = await self.launch(code)
        data = json.loads(await asyncio.wait_for(owner.stdout.readline(), 10))
        self.assertEqual(data["pid"], owner.identity.payload_pid)
        self.assertNotEqual(owner.identity.guard_pid, owner.identity.payload_pid)
        self.assertGreater(owner.identity.payload_created, 0)
        native = jobs._Native()
        handle = native.check(native.k.OpenProcess(0x1000, False, owner.identity.payload_pid))
        try:
            self.assertEqual(guard.creation_time(native.k, handle), owner.identity.payload_created)
            self.assertTrue(native.member(owner._job, handle), "payload not in this exact job")
        finally:
            native.check(native.k.CloseHandle(handle))
        owner.stdin.write(b"release\n")
        await owner.stdin.drain()
        result = await asyncio.wait_for(owner.wait(), 10)
        self.assertEqual(result.payload_returncode, 0)
        self.assertEqual(result.active_processes, 0)

    async def test_job_handle_never_inherited_by_payload(self):
        code = r"""
import ctypes as c, os, sys
k=c.WinDLL('kernel32',use_last_error=True)
k.QueryInformationJobObject.argtypes=[c.c_void_p,c.c_int32,c.c_void_p,c.c_uint32,c.c_void_p]
k.QueryInformationJobObject.restype=c.c_int32
raw,size=map(int,sys.stdin.buffer.readline().split())
buffer=c.create_string_buffer(size)
assert k.QueryInformationJobObject(None,9,buffer,size,None), 'query itself failed'
assert not k.QueryInformationJobObject(raw,9,buffer,size,None), 'job handle leaked'
os.write(1,b'no inherited job handle\n')
"""
        owner = await self.launch(code)
        owner.stdin.write(f"{owner._job} {c.sizeof(jobs._ExtendedLimit)}\n".encode())
        await owner.stdin.drain()
        self.assertEqual(await asyncio.wait_for(owner.stdout.readline(), 10),
                         b"no inherited job handle\n")
        self.assertEqual((await asyncio.wait_for(owner.wait(), 10)).payload_returncode, 0)

    async def test_native_gate_eof_and_timeout_ignore_workspace_startup(self):
        import msvcrt

        native = jobs._Native()
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            for name in ("sitecustomize", "subprocess", "threading", "ctypes", "json"):
                (Path(directory) / f"{name}.py").write_text(
                    f"from pathlib import Path; Path({str(marker)!r}).touch()")
            for mode in ("eof", "stray"):
                with self.subTest(mode=mode):
                    rd, wr = os.pipe()
                    stray = None
                    process = None
                    try:
                        handle = msvcrt.get_osfhandle(wr)
                        os.set_handle_inheritable(handle, True)
                        startup = subprocess.STARTUPINFO(
                            lpAttributeList={"handle_list": [handle]})
                        process = subprocess.Popen(
                            [BASE_PYTHON, "-I", "-S", str(GUARD), str(handle), "one-use", "2"],
                            startupinfo=startup, close_fds=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=Path(BASE_PYTHON).parent,
                            env=dict(native.bootstrap_env(), PYTHONPATH=directory,
                                     PYTHONHOME=directory))
                        os.close(wr)
                        wr = None
                        os.set_blocking(rd, False)
                        reader = jobs.WindowsOwnedProcess(None, 1, 1)
                        reader._control = rd
                        ready = await reader._status(asyncio.get_running_loop().time() + 10)
                        self.assertEqual(ready["pid"], process.pid)
                        if mode == "stray":
                            # An incomplete config cannot admit payload code.
                            process.stdin.write(frame(config(
                                f"from pathlib import Path; Path({str(marker)!r}).touch()",
                                cwd=directory))[:4])
                            process.stdin.flush()
                            stray = os.dup(process.stdin.fileno())
                        process.stdin.close()
                        process.stdin = None
                        await asyncio.to_thread(process.wait, timeout=10)
                        self.assertEqual(process.returncode, 124 if mode == "stray" else 125)
                        self.assertFalse(marker.exists())
                    finally:
                        if stray is not None:
                            os.close(stray)
                        if wr is not None:
                            os.close(wr)
                        os.close(rd)
                        if process is not None:
                            if process.poll() is None:
                                process.kill()  # Exact Popen creation handle, not PID lookup.
                            await asyncio.to_thread(process.communicate, timeout=10)

    async def test_grandchild_retaining_pipes_killed_on_success_foreign_sentinel_survives(self):
        sentinel = subprocess.Popen([PYTHON, "-I", "-S", "-c", "import time; time.sleep(60)"])
        try:
            code = (
                "import subprocess,sys,json,os; "
                "p=subprocess.Popen([sys.executable,'-I','-S','-c',"
                "'import time; time.sleep(60)']); "
                "os.write(1,(str(p.pid)+'\\n').encode())"
            )
            owner = await self.launch(code)
            child_pid = int(await asyncio.wait_for(owner.stdout.readline(), 10))
            result = await asyncio.wait_for(owner.wait(), 10)
            self.assertEqual(result.payload_returncode, 0)
            self.assertEqual(result.active_processes, 0)
            self.assertIsNone(sentinel.poll())
            self.assertNotEqual(child_pid, sentinel.pid)
            self.assertEqual(await asyncio.wait_for(owner.stdout.read(), 10), b"")
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=10)

    async def test_binary_unicode_environment_spaces_and_cwd(self):
        with tempfile.TemporaryDirectory(prefix="owned process 雪 ") as directory:
            code = (
                "import os,sys,json; os.write(1,json.dumps([sys.argv[1:],"
                "os.getcwd(),dict(os.environ)],ensure_ascii=False).encode()+b'\\n'); "
                "os.write(1,sys.stdin.buffer.read()); os.write(2,b'err\\r\\n')"
            )
            env = dict(jobs._Native().bootstrap_env(), OWNED_TEST="value 雪")
            owner = await self.launch(code, cwd=directory, env=env,
                                      arguments=["a b", 'quote " and \\', "雪"])
            tail = b"\x00\x1a\r\n" + "tail 雪".encode()
            owner.stdin.write(tail)
            await owner.stdin.drain()
            owner.stdin.close()
            out_task = asyncio.create_task(owner.stdout.read())
            err_task = asyncio.create_task(owner.stderr.read())
            result = await asyncio.wait_for(owner.wait(), 10)
            out, err = await asyncio.gather(out_task, err_task)
            header, body = out.split(b"\n", 1)
            values = json.loads(header)
            self.assertEqual(values[0], ["a b", 'quote " and \\', "雪"])
            self.assertEqual(Path(values[1]), Path(directory))
            self.assertEqual(values[2]["OWNED_TEST"], "value 雪")
            self.assertEqual((body, err), (tail, b"err\r\n"))
            self.assertEqual(result.payload_returncode, 0)

    async def test_cancellation_closes_live_tree(self):
        owner = await self.launch("import time; time.sleep(60)")
        waiting = asyncio.create_task(owner.wait())
        await asyncio.sleep(0)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertEqual(owner.state, "closed")
        self.assertEqual((await owner.close()).active_processes, 0)

    async def test_repeated_cancellation_during_native_creation(self):
        began, release = asyncio.Event(), asyncio.Event()
        create = asyncio.create_subprocess_exec
        cleanup = jobs.WindowsOwnedProcess._cleanup
        observed = []

        async def delayed(*args, **kwargs):
            process = await create(*args, **kwargs)
            began.set()
            await release.wait()
            return process

        async def checked(owner):
            result = await cleanup(owner)
            observed.append((owner.state, result.active_processes))
            return result

        with patch.object(asyncio, "create_subprocess_exec", delayed), patch.object(
                jobs.WindowsOwnedProcess, "_cleanup", checked):
            starting = asyncio.create_task(jobs.WindowsOwnedProcess.start(
                [PYTHON, "-I", "-S", "-c", "raise AssertionError('GO after cancellation')"],
                cwd=Path(PYTHON).parent, env=jobs._Native().bootstrap_env(),
                trusted_python=BASE_PYTHON))
            try:
                await asyncio.wait_for(began.wait(), 10)
                starting.cancel()
                await asyncio.sleep(0)
                starting.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await starting
                self.assertEqual(observed, [("closed", 0)])
            finally:
                release.set()
                if not starting.done():
                    starting.cancel()
                await asyncio.gather(starting, return_exceptions=True)

    async def test_parent_death_at_each_interval(self):
        native = jobs._Native()
        phases = ("configured", "spawned", "identity", "assigned", "go", "started",
                  "cleanup-failure")
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "payload.json"
                result = await asyncio.to_thread(
                    subprocess.run,
                    [PYTHON, "-I", "-S", "-c", PARENT_DEATH, str(ROOT), phase, str(marker)],
                    capture_output=True, timeout=20, cwd=Path(PYTHON).parent,
                    env=native.bootstrap_env())
                self.assertEqual(result.returncode, 0, result.stderr)
                identities = json.loads(result.stdout)
                if phase in ("configured", "spawned", "identity", "assigned"):
                    self.assertFalse(marker.exists(), "payload executed before GO")
                if marker.exists():
                    identities.extend(json.loads(marker.read_text()))
                for pid, ticks in identities:
                    handle = native.k.OpenProcess(0x00101000, False, pid)
                    if not handle:
                        self.assertEqual(c.get_last_error(), 87, "unknown process identity")
                        continue
                    try:
                        if guard.creation_time(native.k, handle) != ticks:
                            continue  # Original creation is gone; never signal replacement.
                        deadline = time.monotonic() + 8
                        while not native.exited(handle):
                            self.assertLess(time.monotonic(), deadline, "owned process survived")
                            await asyncio.sleep(0.01)
                    finally:
                        native.check(native.k.CloseHandle(handle))


if __name__ == "__main__":
    unittest.main()