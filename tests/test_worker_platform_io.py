"""Real pipe/subprocess checks; forced threaded cases are not Windows evidence."""

import asyncio
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from amplifier_web.runtime_protocol import MAX_MESSAGE_BYTES, encode_message
from amplifier_web.worker_input import ThreadedPipeReader
from amplifier_web import worker_input


ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 10
PROBE = r"""
import asyncio, os, sys
from pathlib import Path
from types import SimpleNamespace
root, backend, limit, mode, direct = sys.argv[1:]
sys.path.insert(0, root)
if direct == 'yes':
    sys.path.insert(0, str(Path(root) / 'amplifier_web'))
    import runtime_worker as module
else:
    from amplifier_web import runtime_worker as module
module.configure_protocol()
if backend == 'threaded':
    # Exercise the Windows branch on POSIX without changing asyncio's platform.
    module.sys = SimpleNamespace(platform='win32', stdin=sys.stdin)
if limit != 'default':
    module.MAX_MESSAGE_BYTES = int(limit)
class Probe(module.Worker):
    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
    async def command(self, data):
        op = data.get('op')
        if op == 'echo':
            print('python-log', flush=True)
            os.write(1, b'fd-log\n')
            module.publish({'op': 'reply', 'payload': data['payload']})
        elif op == 'hold':
            module.publish({'type': 'waiting'})
            await self.gate.wait()
            module.publish({'type': 'released'})
        elif op == 'release':
            self.gate.set()
        elif op == 'arm':
            if mode == 'shutdown':
                self.shutdown.set()
            else:
                self.target.cancel()
        else:
            await super().command(data)
async def main():
    worker = Probe()
    if mode == 'cancel-read':
        # This fixture deliberately bypasses Worker.run(), which owns the
        # production identity announcement. Keep the same once-only pre-read
        # announcement when testing read() in isolation.
        module.publish_process_identity()
    worker.target = asyncio.create_task(
        worker.read() if mode == 'cancel-read' else worker.run())
    # Worker.run emits its identity synchronously before its first await. Do
    # not let the fixture-only ready marker overtake that production frame.
    await asyncio.sleep(0)
    module.publish({'type': 'ready'})
    try:
        await worker.target
    except asyncio.CancelledError:
        pass
    module.publish({'type': 'exited', 'shutdown': worker.shutdown.is_set()})
asyncio.run(main())
"""


def native_creation_time(pid):
    """Passively inspect the direct interpreter PID; never signal a Windows PID."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    dword, handle = ctypes.c_uint32, ctypes.c_void_p
    filetime = dword * 2
    signatures = {
        "OpenProcess": ([dword, ctypes.c_int32, dword], handle),
        "WaitForSingleObject": ([handle, dword], dword),
        "GetProcessTimes": ([handle] + [ctypes.POINTER(filetime)] * 4, ctypes.c_int32),
        "CloseHandle": ([handle], ctypes.c_int32),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    process = kernel.OpenProcess(0x00101000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not process:
        raise OSError("cannot inspect fixture worker identity")
    try:
        if kernel.WaitForSingleObject(process, 0) != 258:  # live, without waiting or signaling
            raise OSError("fixture worker already exited or liveness is unknown")
        times = [filetime() for _ in range(4)]
        if not kernel.GetProcessTimes(process, *(ctypes.byref(value) for value in times)):
            raise OSError("cannot inspect fixture worker creation time")
        return times[0][0] | (times[0][1] << 32)
    finally:
        if not kernel.CloseHandle(process):
            raise OSError("fixture identity handle closure failed")


class FixtureProtocol:
    """Require exactly one native identity; never skip arbitrary wire messages."""
    def __init__(self, identity=None):
        self.identity, self.consumed = identity, False

    def consume_identity(self, data):
        if self.identity is None:
            raise AssertionError("POSIX fixture must not announce a Windows identity")
        if self.consumed:
            raise AssertionError("duplicate fixture identity")
        if (not isinstance(data, dict) or set(data) != {"type", "pid", "created"}
                or data["type"] != "runtime.identity"
                or type(data["pid"]) is not int or data["pid"] <= 0
                or type(data["created"]) is not int or data["created"] <= 0
                or (data["pid"], data["created"]) != self.identity):
            raise AssertionError("missing or incorrect fixture identity")
        self.consumed = True

    def message(self, data):
        if self.identity is not None and not self.consumed:
            raise AssertionError("fixture identity was not validated before protocol messages")
        if isinstance(data, dict) and data.get("type") == "runtime.identity":
            raise AssertionError("unexpected or duplicate fixture identity")
        return data


class FixtureIdentityTests(unittest.TestCase):
    def test_wrong_missing_duplicate_and_posix_identity_fail_closed(self):
        identity = {"type": "runtime.identity", "pid": 123, "created": 456}
        for invalid in ({"type": "ready"}, {}, None,
                        {**identity, "pid": 124}, {**identity, "created": 457},
                        {**identity, "pid": True}, {**identity, "created": True},
                        {**identity, "created": 0}, {**identity, "extra": "not allowed"}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(AssertionError, "identity"):
                FixtureProtocol((123, 456)).consume_identity(invalid)
        protocol = FixtureProtocol((123, 456))
        with self.assertRaisesRegex(AssertionError, "not validated"):
            protocol.message({"type": "ready"})
        protocol.consume_identity(identity)
        self.assertEqual(protocol.message({"type": "ready"}), {"type": "ready"})
        with self.assertRaisesRegex(AssertionError, "duplicate"):
            protocol.consume_identity(identity)
        with self.assertRaisesRegex(AssertionError, "duplicate"):
            protocol.message(identity)
        posix = FixtureProtocol()
        self.assertEqual(posix.message({"type": "ready"}), {"type": "ready"})
        with self.assertRaisesRegex(AssertionError, "POSIX"):
            posix.consume_identity(identity)
        with self.assertRaisesRegex(AssertionError, "unexpected"):
            posix.message(identity)

    def test_creation_time_query_is_read_only_exact_and_closes_on_failure(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 0x100000001
        kernel.WaitForSingleObject.return_value = 258
        kernel.CloseHandle.return_value = 1
        def times(handle, created, *others):
            created._obj[0], created._obj[1] = 17, 2
            return 1
        kernel.GetProcessTimes.side_effect = times
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            self.assertEqual(native_creation_time(123), 17 | (2 << 32))
            kernel.OpenProcess.assert_called_once_with(0x00101000, False, 123)
            kernel.CloseHandle.assert_called_once_with(0x100000001)
            kernel.WaitForSingleObject.assert_called_once_with(0x100000001, 0)
            kernel.CloseHandle.reset_mock()
            kernel.GetProcessTimes.side_effect = lambda *args: 0
            with self.assertRaisesRegex(OSError, "creation time"):
                native_creation_time(123)
            kernel.CloseHandle.assert_called_once_with(0x100000001)
            kernel.CloseHandle.reset_mock()
            kernel.WaitForSingleObject.return_value = 0
            with self.assertRaisesRegex(OSError, "already exited"):
                native_creation_time(123)
            kernel.CloseHandle.assert_called_once_with(0x100000001)
        # The sensor never asks for termination/quota access or calls a signal.
        self.assertEqual({call[0] for call in kernel.method_calls},
                         {"OpenProcess", "WaitForSingleObject", "GetProcessTimes", "CloseHandle"})


class ThreadedReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        read_fd, self.write_fd = os.pipe()
        self.pipe = os.fdopen(read_fd, "rb", buffering=0)
        self.reader = ThreadedPipeReader(self.pipe, 16)

    async def asyncTearDown(self):
        self.reader.close()
        if self.write_fd is not None:
            os.close(self.write_fd)
        self.pipe.close()
        # Bounded test-only join after delivering EOF, never a production join.
        self.reader._thread.join(TIMEOUT)
        self.assertFalse(self.reader._thread.is_alive())

    async def test_binary_crlf_ctrl_z_and_eof(self):
        os.write(self.write_fd, b"a\r\n\x1a\nlast")
        os.close(self.write_fd)
        self.write_fd = None
        self.assertEqual(await self.reader.readline(), b"a\r\n")
        self.assertEqual(await self.reader.readline(), b"\x1a\n")
        self.assertEqual(await self.reader.readline(), b"last")
        self.assertEqual(await self.reader.readline(), b"")
        self.assertEqual(await self.reader.readline(), b"")

    async def test_exact_byte_limit_and_overflow_without_newline(self):
        os.write(self.write_fd, b"x" * 15 + b"\n" + b"x" * 17)
        self.assertEqual(await self.reader.readline(), b"x" * 15 + b"\n")
        with self.assertRaisesRegex(ValueError, "transport limit"):
            await self.reader.readline()

    async def test_one_pending_request_no_read_ahead_and_no_executor(self):
        real_readline = self.reader._stream.readline
        calls = []

        def counted_readline(limit):
            calls.append(limit)
            return real_readline(limit)

        with patch.object(self.reader._stream, "readline", counted_readline):
            os.write(self.write_fd, b"one\ntwo\nthree\n")
            self.assertEqual(await self.reader.readline(), b"one\n")
            # The thread has no next read request; extra pipe bytes cannot
            # enqueue callbacks or complete messages while the consumer pauses.
            self.assertEqual(calls, [17])
            self.assertEqual(self.reader._requests.maxsize, 1)
            self.assertIsNone(asyncio.get_running_loop()._default_executor)
            self.assertTrue(self.reader._thread.daemon)
            self.assertFalse(os.get_inheritable(self.reader._stream.fileno()))
            self.assertEqual(await self.reader.readline(), b"two\n")
            self.assertEqual(calls, [17, 17])

    async def test_cancellation_during_real_blocked_read_does_not_close_its_lock(self):
        entered = threading.Event()
        real_readline = self.reader._stream.readline

        def blocking_readline(limit):
            entered.set()
            return real_readline(limit)

        with patch.object(self.reader._stream, "readline", blocking_readline):
            task = asyncio.create_task(self.reader.readline())
            async with asyncio.timeout(TIMEOUT):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            # No bytes/EOF: the dedicated thread is in a real pipe read.
            other = asyncio.create_task(self.reader.readline())
            with self.assertRaisesRegex(RuntimeError, "pending read"):
                await other
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            self.reader.close()
            self.reader.close()
            self.assertIsNone(asyncio.get_running_loop()._default_executor)
            # Later completion must not touch a cancelled future.
            os.write(self.write_fd, b"late\n")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await self.reader.readline()


class ControlInputIsolationTests(unittest.TestCase):
    @contextmanager
    def native_model(self, fault=None):
        """Unavailable kernel/CRT only; real isolation body, no real descriptors."""
        import io
        import types
        fds = {0: "control"}
        inherit = {0: True}
        streams, calls = [], []
        failure = OSError("original synthetic failure")
        state = SimpleNamespace(standard="control", reads=0)
        class Stream:
            closed = False
            def __init__(self, fd): self.fd, self.contents = fd, io.BytesIO(b'init\nStop\n')
            def fileno(self): return self.fd
            def close(self):
                self.closed = True
                close(self.fd)
            def readline(self, limit):
                state.reads += 1
                return self.contents.readline(limit)
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        def check(stage):
            calls.append(stage)
            if stage == fault or isinstance(fault, tuple) and stage in fault:
                raise failure
        def dup(fd):
            check("dup")
            target = max(fds) + 1
            fds[target], inherit[target] = fds[fd], False
            return target
        def set_inheritable(fd, value):
            check("noninherit")
            inherit[fd] = True if fault == "still-inheritable" else value
        def fdopen(fd, mode):
            check("fdopen")
            self.assertEqual(mode, "rb")
            stream = Stream(fd)
            streams.append(stream)
            return stream
        def open_eof(path, mode):
            check("open-eof")
            self.assertEqual(path, "nul")
            self.assertEqual(mode, 0x8000)
            fd = max(fds) + 1
            fds[fd], inherit[fd] = "independent-eof", False
            return fd
        def dup2(src, dest, *, inheritable):
            check("dup2" if fds[src] == "independent-eof" else "restore")
            fds[dest], inherit[dest] = fds[src], inheritable
        def close(fd):
            check("close-eof" if fds[fd] == "independent-eof" else "close-control")
            del fds[fd]
        bindings = 0
        def bind(fd):
            nonlocal bindings
            bindings += 1
            check("bind" if bindings == 1 else "restore-bind")
            state.standard = fds[fd]
        model_os = SimpleNamespace(name="nt", O_RDONLY=0, O_BINARY=0x8000, devnull="nul",
            dup=dup, set_inheritable=set_inheritable, get_inheritable=lambda fd: inherit[fd],
            fdopen=fdopen, open=open_eof, dup2=dup2, close=close)
        crt = types.ModuleType("msvcrt")
        crt.setmode = lambda fd, mode: check("binary")
        with patch.object(worker_input, "os", model_os), \
             patch.object(worker_input, "_CONTROL_INPUT", None), \
             patch.object(worker_input, "_CONTROL_CLAIMED", False), \
             patch.object(worker_input, "_FAILED_CONTROL_INPUTS", []), \
             patch.object(worker_input, "_bind_standard_input", bind), \
             patch.dict(sys.modules, {"msvcrt": crt}):
            yield SimpleNamespace(fds=fds, inherit=inherit, streams=streams, calls=calls,
                failure=failure, state=state, pipe=SimpleNamespace(fileno=lambda: 0))

    def test_default_stdin_is_independent_eof_before_any_reader_and_setup_is_idempotent(self):
        with self.native_model() as m:
            # BEFORE: a duplicate still names the synchronously read control
            # object. An inheritable=False flag alone cannot fix default stdin.
            self.assertEqual(m.state.standard, "control")
            control = worker_input.isolate_control_input(m.pipe)
            self.assertEqual(m.fds[control.fileno()], "control")
            self.assertFalse(m.inherit[control.fileno()])
            self.assertEqual(m.fds[0], m.state.standard)
            self.assertEqual(m.state.standard, "independent-eof")
            self.assertEqual(m.state.reads, 0)
            calls = list(m.calls)
            self.assertIs(worker_input.isolate_control_input(m.pipe), control)
            self.assertEqual(m.calls, calls)
            self.assertEqual(control.readline(100), b"init\n")
            self.assertEqual(control.readline(100), b"Stop\n")
            self.assertEqual(control.readline(100), b"")
            control.close()
            with self.assertRaisesRegex(RuntimeError, "already closed"):
                worker_input.isolate_control_input(m.pipe)

    def test_fallible_setup_preserves_original_error_and_no_reader_or_descriptor_leak(self):
        for fault in ("dup", "noninherit", "binary", "fdopen", "open-eof", "dup2", "bind", "still-inheritable"):
            with self.subTest(fault=fault), self.native_model(fault) as m:
                with self.assertRaises(OSError) as caught:
                    worker_input.isolate_control_input(m.pipe)
                if fault == "still-inheritable":
                    self.assertIn("remained inheritable", str(caught.exception))
                else:
                    self.assertIs(caught.exception, m.failure)
                self.assertEqual(m.fds, {0: "control"})
                self.assertEqual(m.state.standard, "control")
                self.assertEqual(m.state.reads, 0)
                self.assertFalse(worker_input._FAILED_CONTROL_INPUTS)
                self.assertIsNone(worker_input._CONTROL_INPUT)

    def test_unknown_close_is_retained_and_not_retried_or_reopened(self):
        with self.native_model("close-eof") as m:
            with self.assertRaises(OSError) as caught:
                worker_input.isolate_control_input(m.pipe)
            self.assertIs(caught.exception, m.failure)
            self.assertEqual(m.calls.count("close-eof"), 1)
            self.assertEqual(m.fds[0], "control")
            self.assertTrue(worker_input._FAILED_CONTROL_INPUTS)
            before = list(m.calls)
            with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
                worker_input.isolate_control_input(m.pipe)
            self.assertEqual(before, m.calls)

    def test_failed_restore_retains_control_and_preserves_first_error_without_reader(self):
        with self.native_model(("bind", "restore-bind")) as m:
            with self.assertRaises(OSError) as caught:
                worker_input.isolate_control_input(m.pipe)
            self.assertIs(caught.exception, m.failure)
            self.assertIs(worker_input._FAILED_CONTROL_INPUTS[0], m.streams[0])
            self.assertFalse(m.streams[0].closed)
            self.assertEqual(m.state.reads, 0)
            calls = list(m.calls)
            with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
                worker_input.isolate_control_input(m.pipe)
            self.assertEqual(calls, m.calls)

    def test_bind_standard_input_uses_exact_win64_handles_and_confirms_binding(self):
        import types
        kernel = Mock()
        kernel.SetStdHandle.return_value = 1
        kernel.GetStdHandle.return_value = 0x100000001
        crt = types.ModuleType("msvcrt")
        crt.get_osfhandle = Mock(return_value=0x100000001)
        with patch.dict(sys.modules, {"msvcrt": crt}), \
             patch.object(ctypes, "WinDLL", return_value=kernel, create=True), \
             patch.object(ctypes, "get_last_error", return_value=5, create=True), \
             patch.object(ctypes, "WinError", side_effect=lambda code: OSError(code, "synthetic"), create=True):
            worker_input._bind_standard_input(0)
            kernel.SetStdHandle.assert_called_once_with(0xfffffff6, 0x100000001)
            self.assertEqual(kernel.SetStdHandle.argtypes, [ctypes.c_uint32, ctypes.c_void_p])
            self.assertIs(kernel.GetStdHandle.restype, ctypes.c_void_p)
            kernel.GetStdHandle.return_value = 4
            with self.assertRaisesRegex(OSError, "not confirmed"):
                worker_input._bind_standard_input(0)
            kernel.SetStdHandle.return_value = 0
            with self.assertRaises(OSError):
                worker_input._bind_standard_input(0)

    def test_worker_reader_owns_only_private_control_and_never_closes_pending_stream(self):
        for fault in (None, "loop", "thread-create", "thread-start"):
            with self.subTest(fault=fault), self.native_model() as m:
                started = []
                def create_thread(**kwargs):
                    if fault == "thread-create": raise m.failure
                    def start():
                        if fault == "thread-start": raise m.failure
                        self.assertEqual(m.fds[0], "independent-eof")
                        self.assertEqual(m.state.standard, "independent-eof")
                        self.assertEqual(m.state.reads, 0)
                        started.append(kwargs)
                    return SimpleNamespace(start=start)
                with patch.object(worker_input.asyncio, "get_running_loop",
                        side_effect=m.failure if fault == "loop" else None, return_value=SimpleNamespace()), \
                     patch.object(worker_input.threading, "Thread", side_effect=create_thread):
                    if fault is not None:
                        with self.assertRaises(OSError) as caught:
                            ThreadedPipeReader.for_worker(m.pipe, 16)
                        self.assertIs(caught.exception, m.failure)
                        if fault == "loop":
                            self.assertEqual(m.fds, {0: "control"})
                            self.assertFalse(m.streams)
                            self.assertFalse(worker_input._CONTROL_CLAIMED)
                            continue
                        self.assertTrue(m.streams[0].closed)
                    else:
                        reader = ThreadedPipeReader.for_worker(m.pipe, 16)
                        self.assertIs(reader._stream, m.streams[0])
                        self.assertEqual(m.calls.count("dup"), 1)  # No shared-object default stdin.
                        self.assertEqual(len(started), 1)
                        self.assertTrue(started[0]["daemon"])
                        reader.close()
                        reader.close()
                        self.assertFalse(reader._stream.closed)  # Daemon owns blocked read.
                    self.assertEqual(m.fds[0], "independent-eof")
                    self.assertEqual(m.state.standard, "independent-eof")
                    with self.assertRaisesRegex(RuntimeError, "already|closed"):
                        ThreadedPipeReader.for_worker(m.pipe, 16)

    def test_posix_factory_keeps_existing_constructor_without_stdin_isolation(self):
        class Portable(ThreadedPipeReader):
            def __init__(self, pipe, limit, **kwargs): self.args = pipe, limit, kwargs
        pipe = object()
        with patch.object(worker_input, "os", SimpleNamespace(name="posix")), \
             patch.object(worker_input, "isolate_control_input", side_effect=AssertionError("POSIX isolation")):
            self.assertEqual(Portable.for_worker(pipe, 32).args, (pipe, 32, {}))


class ControlInputDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_read_dispatches_init_commands_stop_and_preserves_cancel(self):
        from amplifier_web import runtime_worker
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                reader = SimpleNamespace(close=Mock())
                lines = [encode_message({"op": "start"}), encode_message({"op": "echo", "payload": "café"}),
                         encode_message({"op": "stop"}), b""]
                original = asyncio.CancelledError("original")
                async def readline():
                    if cancel: raise original
                    return lines.pop(0)
                reader.readline = readline
                received = []
                async def command(data): received.append(data)
                worker = SimpleNamespace(command=command, tasks=set(), shutdown=asyncio.Event())
                with patch.object(runtime_worker, "sys", SimpleNamespace(platform="win32", stdin=object())), \
                     patch.object(ThreadedPipeReader, "for_worker", return_value=reader) as factory:
                    if cancel:
                        with self.assertRaises(asyncio.CancelledError) as caught:
                            await runtime_worker.Worker.read(worker)
                        self.assertIs(caught.exception, original)
                    else:
                        await runtime_worker.Worker.read(worker)
                        await asyncio.sleep(0)
                        self.assertEqual([v["op"] for v in received], ["start", "echo", "stop"])
                        self.assertEqual(received[1]["payload"], "café")
                    factory.assert_called_once_with(runtime_worker.sys.stdin, MAX_MESSAGE_BYTES)
                reader.close.assert_called_once_with()
                self.assertTrue(worker.shutdown.is_set())


@unittest.skipUnless(os.name == "nt", "native private-control/default-stdin contract")
class NativeControlInputTests(unittest.IsolatedAsyncioTestCase):
    failed_owners = []  # Unknown native cleanup retains custody, never a retry.

    async def test_private_commands_survive_while_ordinary_child_sees_eof(self):
        """Native-selectable; no provider/install/network or redirector kill."""
        from amplifier_web.windows_jobs import CleanupError, WindowsOwnedProcess
        source = r'''
import asyncio, ctypes, json, msvcrt, os, sys
sys.path.insert(0, sys.argv[1])
from amplifier_web import runtime_worker
from amplifier_web.worker_input import ThreadedPipeReader
runtime_worker.configure_protocol()
async def main():
    reader = ThreadedPipeReader.for_worker(sys.stdin, 1024)
    request = None
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes, kernel.GetStdHandle.restype = [ctypes.c_uint32], ctypes.c_void_p
        assert kernel.GetStdHandle(0xfffffff6) == msvcrt.get_osfhandle(0)
        assert os.read(0, 1) == b"" and not os.get_inheritable(reader._stream.fileno())
        runtime_worker.publish({"type": "ready", "private": True, "defaultEof": True})
        first = json.loads(await reader.readline())
        assert first == {"op": "start", "text": "café"}
        runtime_worker.publish({"type": "command", "text": first["text"]})
        request = asyncio.create_task(reader.readline())
        while (reader._pending is None or not reader._requests.empty()):
            assert not request.done()
            await asyncio.sleep(.001)
        # Real ordinary child default stdin, not another owned-process pipe.
        # This child is already inside the retained outer Job's containment.
        child = await asyncio.create_subprocess_exec(
            sys._base_executable, "-I", "-S", "-B", "-c",
            "import sys; assert sys.stdin.buffer.read()==b''; print('eof')",
            stdin=None, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(3):
            out, err = await child.communicate()
        assert child.returncode == 0 and out.strip() == b"eof" and not err
        assert not request.done() and not reader._pending.done()
        runtime_worker.publish({"type": "child", "defaultEof": True, "controlPending": True})
        assert json.loads(await request) == {"op": "stop"}
        runtime_worker.publish({"type": "stopped"})
    finally:
        reader.close()
        if request is not None:
            request.cancel()
            try: await request
            except asyncio.CancelledError: pass
asyncio.run(main())
'''
        owner = result = stderr_task = None
        stderr = bytearray()
        async def drain():
            while block := await owner.stderr.read(4096):
                stderr.extend(block)
                self.assertLessEqual(len(stderr), 65536)
        async def receive():
            line = await owner.stdout.readline()
            self.assertLessEqual(len(line), 4096)
            self.assertTrue(line)
            return json.loads(line)
        try:
            async with asyncio.timeout(20):
                owner = await WindowsOwnedProcess.start(
                    [sys._base_executable, "-I", "-S", "-B", "-c", source, str(ROOT)],
                    cwd=str(ROOT), env=dict(os.environ), trusted_python=sys._base_executable,
                    gate_timeout=10, cleanup_timeout=5)
                stderr_task = asyncio.create_task(drain())
                self.assertEqual(await receive(), {"type": "ready", "private": True, "defaultEof": True})
                owner.stdin.write(encode_message({"op": "start", "text": "café"}))
                await owner.stdin.drain()
                self.assertEqual(await receive(), {"type": "command", "text": "café"})
                self.assertEqual(await receive(), {"type": "child", "defaultEof": True, "controlPending": True})
                owner.stdin.write(encode_message({"op": "stop"}))
                await owner.stdin.drain()
                self.assertEqual(await receive(), {"type": "stopped"})
                result = await owner.wait()  # Keep stdin open until natural exit.
                self.assertEqual(result.payload_returncode, 0)
                self.assertEqual(await owner.stdout.read(), b"")
                await stderr_task
                self.assertEqual(stderr, b"")
        except CleanupError as error:
            owner = error.owner
            raise
        finally:
            if owner is not None:
                try:
                    if owner.state != "cleanup_failed":
                        result = await owner.close()
                except BaseException:
                    if owner not in self.failed_owners:
                        self.failed_owners.append(owner)
                    raise
                if not (result is not None and result.active_processes == 0
                        and owner.tree_empty and owner.io_finalized):
                    self.failed_owners.append(owner)
                    self.fail("private-control probe cleanup was not confirmed")
                self.assertFalse(owner.output_discarded, "private-control probe output was discarded")
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
                done, pending = await asyncio.wait({stderr_task}, timeout=1)
                self.assertFalse(pending, "private-control probe reader did not settle")
                if not stderr_task.cancelled():
                    stderr_task.exception()


class WorkerSubprocessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.processes = []
        self.protocols = {}

    async def asyncTearDown(self):
        for proc in self.processes:
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.wait(), TIMEOUT)
            proc.stdin.close()
            await proc.stdout.read()
            await proc.stderr.read()

    async def spawn(self, backend, *, limit="default", mode="run", direct=False, model_identity=False):
        # Force a non-UTF-8 text stdout. Protocol bytes must still round-trip.
        env = {**os.environ, "PYTHONIOENCODING": "ascii:strict", "PYTHONUTF8": "0"}
        probe = PROBE
        if model_identity:
            self.assertNotEqual(os.name, "nt", "portable identity model is not native evidence")
            # Fake only the unavailable kernel identity sensor on POSIX. Keep
            # the real Worker.run/read, subprocess, payload and byte oracles.
            probe = probe.replace(
                "module.configure_protocol()\n",
                "module.configure_protocol()\n"
                "module.publish_process_identity = lambda: module.publish("
                "{'type':'runtime.identity','pid':os.getpid(),'created':17})\n")
        proc = await asyncio.create_subprocess_exec(
            # A Windows venv executable is a redirector: use its explicit base
            # interpreter so proc.pid is the actual stdlib-only Probe process.
            sys._base_executable if os.name == "nt" else sys.executable,
            "-I", "-S", "-B", "-c", probe, str(ROOT), backend, str(limit),
            mode, "yes" if direct else "no", cwd=ROOT, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=MAX_MESSAGE_BYTES)
        self.processes.append(proc)
        identity = ((proc.pid, native_creation_time(proc.pid)) if os.name == "nt"
                    else (proc.pid, 17) if model_identity else None)
        self.protocols[proc] = FixtureProtocol(identity)
        if identity is not None:
            self.protocols[proc].consume_identity(await self.read_frame(proc))
        self.assertEqual(await self.receive(proc), {"type": "ready"})
        return proc

    async def test_modeled_windows_identity_keeps_payload_and_eof_oracles(self):
        for backend in ("native", "threaded"):
            for mode in ("run", "cancel-read", "cancel-run"):
                with self.subTest(backend=backend, mode=mode):
                    proc = await self.spawn(backend, mode=mode, model_identity=os.name != "nt")
                    await self.send(proc, encode_message({"op": "echo", "payload": "identity café"}))
                    self.assertEqual(await self.receive(proc), {"op": "reply", "payload": "identity café"})
                    proc.stdin.close()
                    await self.exited(proc)

    async def read_frame(self, proc):
        line = await asyncio.wait_for(proc.stdout.readline(), TIMEOUT)
        self.assertTrue(line, "worker exited before sending a protocol line")
        self.assertNotIn(b"\r", line, "protocol must not translate LF to CRLF")
        return json.loads(line)

    async def receive(self, proc):
        return self.protocols[proc].message(await self.read_frame(proc))

    async def send(self, proc, data):
        proc.stdin.write(data)
        await asyncio.wait_for(proc.stdin.drain(), TIMEOUT)

    async def exited(self, proc):
        self.assertEqual(await self.receive(proc), {"type": "exited", "shutdown": True})
        # Deliberately keep parent stdin open until process exit: no EOF is
        # available to rescue a blocked reader or asyncio's executor shutdown.
        self.assertEqual(await asyncio.wait_for(proc.wait(), TIMEOUT), 0)
        self.assertEqual(await proc.stdout.read(), b"")
        stderr = await proc.stderr.read()
        self.assertNotIn(b"Traceback", stderr)
        return stderr

    async def stop(self, proc):
        await self.send(proc, b'{"op":"stop"}\n')
        return await self.exited(proc)

    async def test_unicode_crlf_and_separate_logs_with_ascii_text_stdout(self):
        payload = "\U0001f680 \u65e5\u672c\u8a9e \u0627\u0644\u0639\u0631\u0628\u064a\u0629"
        for backend in ("native", "threaded"):
            for direct in (False, True):
                with self.subTest(backend=backend, direct=direct):
                    proc = await self.spawn(backend, direct=direct)
                    message = encode_message({"op": "echo", "payload": payload})
                    await self.send(proc, message[:-1] + b"\r\n")
                    raw = await asyncio.wait_for(proc.stdout.readline(), TIMEOUT)
                    self.assertEqual(raw, encode_message({"op": "reply", "payload": payload}))
                    stderr = await self.stop(proc)
                    self.assertIn(b"python-log", stderr)
                    self.assertIn(b"fd-log", stderr)

    async def test_malformed_and_non_dict_messages_are_ignored(self):
        for backend in ("native", "threaded"):
            with self.subTest(backend=backend):
                proc = await self.spawn(backend)
                await self.send(proc, b'not json\n[]\nnull\n42\n"string"\n\xff\n\x1a\n'
                                + encode_message({"op": "echo", "payload": "valid"}))
                self.assertEqual(await self.receive(proc), {"op": "reply", "payload": "valid"})
                await self.stop(proc)

    async def test_eof_including_final_line_without_newline(self):
        for backend in ("native", "threaded"):
            for final_line in (b"", encode_message({"op": "echo", "payload": "last"})[:-1]):
                with self.subTest(backend=backend, final_line=bool(final_line)):
                    proc = await self.spawn(backend)
                    await self.send(proc, final_line)
                    proc.stdin.close()
                    if final_line:
                        self.assertEqual(await self.receive(proc), {"op": "reply", "payload": "last"})
                    await self.exited(proc)

    async def test_stop_shutdown_and_cancellation_with_stdin_still_open(self):
        for backend in ("native", "threaded"):
            for mode in ("run", "shutdown", "cancel-read", "cancel-run"):
                with self.subTest(backend=backend, mode=mode):
                    proc = await self.spawn(backend, mode=mode)
                    op = "stop" if mode == "run" else "arm"
                    # The next read cannot complete from this partial line.
                    await self.send(proc, encode_message({"op": op}) + b'{"partial":')
                    await self.exited(proc)

    async def test_command_wait_does_not_block_reply_dispatch(self):
        for backend in ("native", "threaded"):
            with self.subTest(backend=backend):
                proc = await self.spawn(backend)
                await self.send(proc, b'{"op":"hold"}\n')
                self.assertEqual(await self.receive(proc), {"type": "waiting"})
                await self.send(proc, b'{"op":"release"}\n')
                self.assertEqual(await self.receive(proc), {"type": "released"})
                await self.stop(proc)

    async def test_transport_byte_limit_includes_newline_and_rejects_unterminated_overflow(self):
        limit = 256
        empty = encode_message({"op": "echo", "payload": ""})
        exact = encode_message({"op": "echo", "payload": "x" * (limit - len(empty))})
        self.assertEqual(len(exact), limit)
        for backend in ("native", "threaded"):
            with self.subTest(backend=backend):
                proc = await self.spawn(backend, limit=limit)
                await self.send(proc, exact)
                self.assertEqual((await self.receive(proc))["payload"], "x" * (limit - len(empty)))
                await self.stop(proc)
            for oversized in (exact[:-1] + b" \n", b"x" * (limit + 1)):
                with self.subTest(backend=backend, terminated=oversized.endswith(b"\n")):
                    proc = await self.spawn(backend, limit=limit)
                    await self.send(proc, oversized)
                    error = await self.receive(proc)
                    self.assertEqual(error["type"], "runtime.error")
                    self.assertIn("ValueError", error["error"])
                    self.assertIn("not replayed", error["error"])
                    await self.exited(proc)

    async def test_threaded_reader_large_multibyte_message(self):
        proc = await self.spawn("threaded")
        payload = "\U0001f680\u65e5" * 300_000
        await self.send(proc, encode_message({"op": "echo", "payload": payload}))
        self.assertEqual(await self.receive(proc), {"op": "reply", "payload": payload})
        await self.stop(proc)


if __name__ == "__main__":
    unittest.main()