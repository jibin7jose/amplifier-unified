"""Stdlib-only real-platform lock checks; Win32 stubs are not qualification."""

import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


RESET_PATH = Path(__file__).resolve().parents[1] / "amplifier_web/reset.py"
spec = importlib.util.spec_from_file_location("standalone_reset", RESET_PATH)
reset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset)

BUSY = (
    "A host or reset is already using this data directory. "
    "Stop manually launched hosts before retrying."
)
TIMEOUT = 10

# Load the emergency script directly with site disabled, outside the package.
# Do not depend on app imports, pytest, POSIX signals or select() on Windows pipes.
LOAD_RESET = """
import os
from pathlib import Path
import runpy
import sys

class NoAppImports:
    def find_spec(self, fullname, *args):
        if fullname.startswith("amplifier_"):
            raise AssertionError("Emergency lock imported an application module")

sys.meta_path.insert(0, NoAppImports())
repair_lock = runpy.run_path(sys.argv[1])["repair_lock"]
"""
HOLDER = LOAD_RESET + """
with repair_lock(Path(sys.argv[2]), shared=sys.argv[3] == "shared"):
    Path(sys.argv[4]).write_text("ready")
    if sys.stdin.read(1) == "x":
        os._exit(23)
print("released", flush=True)
"""
CONTENDER = LOAD_RESET + """
try:
    with repair_lock(Path(sys.argv[2]), shared=sys.argv[3] == "shared"):
        print("acquired", flush=True)
except RuntimeError as error:
    print(str(error), flush=True)
    sys.exit(2)
"""


class RepairLockProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "data"
        self.children = []

    def cleanup_child(self, child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=TIMEOUT)

    def holder(self, *, shared):
        ready = self.root / f"ready-{len(self.children)}"
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", HOLDER, str(RESET_PATH),
             str(self.home), "shared" if shared else "exclusive", str(ready)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=self.root,
        )
        self.children.append(child)
        self.addCleanup(self.cleanup_child, child)
        deadline = time.monotonic() + TIMEOUT
        while not ready.exists():
            if child.poll() is not None:
                output, errors = child.communicate(timeout=TIMEOUT)
                self.fail(f"Lock holder exited {child.returncode}: {output}\n{errors}")
            if time.monotonic() >= deadline:
                self.fail("Lock holder did not become ready within the timeout")
            time.sleep(.01)
        self.assertIsNone(child.poll())
        return child

    def finish(self, child, *, crash=False):
        output, errors = child.communicate("x" if crash else "q", timeout=TIMEOUT)
        self.assertEqual(child.returncode, 23 if crash else 0, errors)
        self.assertEqual(output.strip(), "" if crash else "released")
        self.assertEqual(errors, "")

    def contend(self, *, shared=False, blocked=False):
        # Contention itself must be timeout-bounded: a broken blocking backend
        # must fail this test rather than hanging the unittest parent.
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", CONTENDER, str(RESET_PATH),
             str(self.home), "shared" if shared else "exclusive"],
            capture_output=True, text=True, timeout=TIMEOUT, cwd=self.root,
        )
        self.assertEqual(result.returncode, 2 if blocked else 0, result.stderr)
        self.assertEqual(result.stdout.strip(), BUSY if blocked else "acquired")
        self.assertEqual(result.stderr, "")

    def test_two_shared_holders_block_exclusive_until_both_exit(self):
        first = self.holder(shared=True)
        second = self.holder(shared=True)
        path = self.home / ".reset.lock"
        identity = path.stat()
        self.contend(blocked=True)
        self.finish(first)
        self.contend(blocked=True)
        self.finish(second)
        self.contend()
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_ino, identity.st_ino)
        self.assertEqual(path.stat().st_size, 0)

    def test_exclusive_holder_refuses_shared_and_exclusive(self):
        child = self.holder(shared=False)
        self.contend(shared=True, blocked=True)
        self.contend(blocked=True)
        self.finish(child)
        self.contend(shared=True)

    def test_crashed_shared_or_exclusive_holder_releases_lock(self):
        for shared in (True, False):
            with self.subTest(shared=shared):
                child = self.holder(shared=shared)
                self.contend(blocked=True)
                self.finish(child, crash=True)
                self.contend()
                self.assertTrue((self.home / ".reset.lock").exists())

    def test_body_exception_releases_lock_without_unlink(self):
        with self.assertRaisesRegex(ValueError, "protected body failed"):
            with reset.repair_lock(self.home):
                raise ValueError("protected body failed")
        self.assertTrue((self.home / ".reset.lock").exists())
        child = self.holder(shared=False)
        self.finish(child)

    def test_standalone_script_help_needs_no_application_dependencies(self):
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(RESET_PATH), "--help"],
            capture_output=True, text=True, timeout=TIMEOUT, cwd=self.root,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", result.stdout)


class WindowsRepairLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.handle = 0x100000001 if ctypes.sizeof(ctypes.c_void_p) == 8 else 123
        self.kernel32 = SimpleNamespace(
            LockFileEx=Mock(return_value=1),
            UnlockFileEx=Mock(return_value=1),
        )
        self.loader = self.enterContext(
            patch.object(ctypes, "WinDLL", return_value=self.kernel32, create=True)
        )
        self.last_error = self.enterContext(
            patch.object(ctypes, "get_last_error", return_value=33, create=True)
        )
        # Preserve the native code explicitly on non-Windows hosts too.
        self.win_error = self.enterContext(patch.object(
            ctypes, "WinError", side_effect=lambda code: OSError(code, "Win32 failure"),
            create=True,
        ))
        self.crt = SimpleNamespace(get_osfhandle=Mock(return_value=self.handle))
        self.enterContext(patch.dict(sys.modules, msvcrt=self.crt))
        self.set_inheritable = self.enterContext(
            patch.object(reset.os, "set_inheritable", wraps=os.set_inheritable)
        )
        # Do not patch the real os.name: pathlib must keep its native flavor.
        self.enterContext(patch.object(
            reset, "os", SimpleNamespace(name="nt", set_inheritable=self.set_inheritable)
        ))

    def test_win32_abi_flags_range_and_checked_unlock(self):
        for shared, flags in ((True, 1), (False, 3)):
            with self.subTest(shared=shared):
                self.kernel32.LockFileEx.reset_mock()
                self.kernel32.UnlockFileEx.reset_mock()
                self.set_inheritable.reset_mock()
                with reset.repair_lock(self.home, shared=shared):
                    self.kernel32.UnlockFileEx.assert_not_called()
                    args = self.kernel32.LockFileEx.call_args.args
                    self.assertEqual(args[:5], (self.handle, flags, 0, 1, 0))
                    overlapped = args[5]._obj
                    pointer_size = ctypes.sizeof(ctypes.c_void_p)
                    self.assertEqual(ctypes.sizeof(overlapped), 32 if pointer_size == 8 else 20)
                    self.assertEqual(type(overlapped).Offset.offset, 2 * pointer_size)
                    self.assertEqual(type(overlapped).OffsetHigh.offset, 2 * pointer_size + 4)
                    self.assertEqual(type(overlapped).hEvent.offset, 2 * pointer_size + 8)
                    self.assertEqual(bytes(overlapped), bytes(ctypes.sizeof(overlapped)))
                    fd = self.crt.get_osfhandle.call_args.args[0]
                    self.set_inheritable.assert_called_once_with(fd, False)
                    self.assertFalse(os.get_inheritable(fd))
                    self.assertEqual(
                        self.kernel32.LockFileEx.argtypes,
                        [ctypes.c_void_p] + [ctypes.c_uint32] * 4
                        + [ctypes.POINTER(type(overlapped))],
                    )
                    self.assertEqual(
                        self.kernel32.UnlockFileEx.argtypes,
                        [ctypes.c_void_p] + [ctypes.c_uint32] * 3
                        + [ctypes.POINTER(type(overlapped))],
                    )
                    self.assertIs(self.kernel32.LockFileEx.restype, ctypes.c_int32)
                    self.assertIs(self.kernel32.UnlockFileEx.restype, ctypes.c_int32)
                unlock_args = self.kernel32.UnlockFileEx.call_args.args
                self.assertEqual(unlock_args[:4], (self.handle, 0, 1, 0))
                self.assertIs(unlock_args[4]._obj, overlapped)
                with self.assertRaises(OSError):
                    os.fstat(fd)  # The stream, not the helper, owns the handle.
        self.loader.assert_called_with("kernel32", use_last_error=True)

    def test_windows_never_imports_fcntl(self):
        with patch.dict(sys.modules, fcntl=None):
            with reset.repair_lock(self.home, shared=True):
                pass
        self.kernel32.LockFileEx.assert_called_once()

    def test_only_lock_contention_gets_the_public_runtime_error(self):
        self.kernel32.LockFileEx.return_value = 0
        with self.assertRaises(RuntimeError) as raised:
            with reset.repair_lock(self.home):
                self.fail("Contended lock entered its body")
        self.assertEqual(str(raised.exception), BUSY)
        self.win_error.assert_not_called()
        self.kernel32.UnlockFileEx.assert_not_called()
        fd = self.crt.get_osfhandle.call_args.args[0]
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_other_acquisition_errors_fail_closed_and_close_stream(self):
        self.kernel32.LockFileEx.return_value = 0
        for error in (5, 6, 87, 997):
            with self.subTest(error=error):
                self.last_error.return_value = error
                with self.assertRaises(OSError) as raised:
                    with reset.repair_lock(self.home):
                        self.fail("Failed lock entered its body")
                self.assertEqual(raised.exception.errno, error)
                self.kernel32.UnlockFileEx.assert_not_called()
                fd = self.crt.get_osfhandle.call_args.args[0]
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_noninheritability_failure_never_acquires(self):
        self.set_inheritable.side_effect = OSError("Cannot secure descriptor")
        with self.assertRaisesRegex(OSError, "Cannot secure descriptor"):
            with reset.repair_lock(self.home):
                self.fail("Unsecured lock entered its body")
        self.crt.get_osfhandle.assert_not_called()
        self.kernel32.LockFileEx.assert_not_called()
        fd = self.set_inheritable.call_args.args[0]
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_body_exception_still_unlocks(self):
        with self.assertRaisesRegex(ValueError, "body failed"):
            with reset.repair_lock(self.home):
                raise ValueError("body failed")
        self.kernel32.UnlockFileEx.assert_called_once()

    def test_unlock_failure_is_not_suppressed_and_stream_still_closes(self):
        self.kernel32.UnlockFileEx.return_value = 0
        self.last_error.return_value = 158  # ERROR_NOT_LOCKED
        with self.assertRaises(OSError) as raised:
            with reset.repair_lock(self.home):
                pass
        self.assertEqual(raised.exception.errno, 158)
        self.kernel32.UnlockFileEx.assert_called_once()
        fd = self.crt.get_osfhandle.call_args.args[0]
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertTrue((self.home / ".reset.lock").exists())


if __name__ == "__main__":
    unittest.main()