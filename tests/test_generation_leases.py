"""Lease/parent creation checks; Win32 stubs are not native qualification."""

import ast
import builtins
from contextlib import contextmanager
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from amplifier_web import generation_leases


class _DirectoryPrepared(Exception):
    """Stop the constructor at the next, unrelated update-cache boundary."""


def _initialize_update_directory(home, *, native, directory=None):
    # Execute the maintained constructor body, not a copy of its mkdir branch.
    # Stop BEFORE cache/metadata/controller setup: no app, loop, Git or network.
    source = Path(__file__).resolve().parents[1] / "amplifier_web/updates.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "UpdateManager")
    initialize = next(node for node in owner.body
                      if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    reached = []

    def cache(path, *, owned_root):
        if owned_root != home:
            raise AssertionError("Update cache lost its canonical app data identity")
        reached.append(path)
        raise _DirectoryPrepared

    def imports(name, *args, **kwargs):
        if name == "windows_files":
            return SimpleNamespace(directory=directory)
        if name == "update_checks":
            return SimpleNamespace(CheckCache=cache)
        raise AssertionError("Unexpected constructor import: " + name)

    namespace = {
        "__builtins__": {**vars(builtins), "__import__": imports},
        "os": SimpleNamespace(name="nt" if native else "posix"),
        "asyncio": SimpleNamespace(Lock=lambda: None),
    }
    exec(compile(ast.Module(body=[initialize], type_ignores=[]), str(source), "exec"), namespace)
    try:
        namespace["__init__"](SimpleNamespace(), SimpleNamespace(data_dir=home))
    except _DirectoryPrepared:
        pass
    else:
        raise AssertionError("Constructor did not reach its next cache boundary")
    if reached != [home / "updates"]:
        raise AssertionError("Unexpected update-cache directory")


def _native_lease_writer(home):
    # Keep this file runnable in the -I -S stdlib lane. Recompile only the two
    # maintained writer declarations; their actual native security/publication
    # implementation is windows_files, not a test emulation. App-home is the
    # same explicit home as the installed-journey fixture.
    from amplifier_web import windows_files

    root = Path(__file__).resolve().parents[1] / "amplifier_web"
    private = None
    for filename in ("deployment.py", "host/config.py"):
        source = root / filename
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "write_private")
        namespace = {
            "Path": Path, "_WINDOWS": True, "windows_files": windows_files,
            "os": SimpleNamespace(name="nt"), "_write_private": private,
            "app_home": lambda: home,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        private = namespace["write_private"]
    return private


class UpdateLeaseParentTests(unittest.TestCase):
    def test_windows_uses_private_creation_before_cache_or_lease_payload(self):
        # Model an elevated token whose default owner is Administrators rather
        # than TokenUser. No kernel/ACL result is inferred from this model.
        owners = {}
        events = []

        class ModelPath:
            def __init__(self, path):
                self.path = PureWindowsPath(path)

            def __truediv__(self, part):
                return ModelPath(self.path / part)

            def __eq__(self, other):
                return isinstance(other, ModelPath) and self.path == other.path

            def mkdir(self, **kwargs):
                events.append("ordinary-mkdir")
                owners[self.path] = "token-default-owner"

        @contextmanager
        def private(path, *, owned_root, create):
            self.assertEqual(owned_root, home)
            self.assertEqual(path, home / "updates")
            self.assertIs(create, True)
            events.append("private-create")
            owners[path.path] = "TokenUser"
            yield
            events.append("pins-closed")

        home = ModelPath("C:/private/app")
        _initialize_update_directory(home, native=True, directory=private)
        self.assertEqual(events, ["private-create", "pins-closed"])
        self.assertEqual(owners[(home / "updates").path], "TokenUser")

    def test_windows_unsafe_existing_parent_refuses_before_cache_without_repair(self):
        home, target = MagicMock(), Mock()
        home.__truediv__.return_value = target
        error = PermissionError("existing directory is not private")
        events = []

        @contextmanager
        def private(path, *, owned_root, create):
            events.append((path, owned_root, create))
            raise error
            yield  # pragma: no cover - contextmanager shape

        with self.assertRaises(PermissionError) as caught:
            _initialize_update_directory(home, native=True, directory=private)
        self.assertIs(caught.exception, error)
        self.assertEqual(events, [(home / "updates", home, True)])
        target.mkdir.assert_not_called()

    def test_windows_pin_close_failure_prevents_cache_or_lease_payload(self):
        home, target = MagicMock(), Mock()
        home.__truediv__.return_value = target
        error = OSError("directory closure unconfirmed")

        @contextmanager
        def private(*args, **kwargs):
            yield
            raise error

        with self.assertRaises(OSError) as caught:
            _initialize_update_directory(home, native=True, directory=private)
        self.assertIs(caught.exception, error)
        target.mkdir.assert_not_called()

    def test_posix_parent_creation_keeps_existing_mkdir_contract(self):
        home, target = MagicMock(), Mock()
        home.__truediv__.return_value = target
        private = Mock(side_effect=AssertionError("Windows boundary on POSIX"))
        _initialize_update_directory(home, native=False, directory=private)
        target.mkdir.assert_called_once_with(parents=True, exist_ok=True, mode=0o700)
        private.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "native Windows TokenUser/NTFS required")
    def test_native_windows_update_parent_and_first_generation_lease(self):
        from amplifier_web import windows_files

        # Canonicalize ONLY the existing TEMP ancestor. The owned child does not
        # exist until the private boundary creates it; never repair a temp DACL.
        home = Path(tempfile.gettempdir()).resolve(strict=True) / ("lease-" + uuid.uuid4().hex)
        self.addCleanup(lambda: shutil.rmtree(home) if home.exists() else None)
        with windows_files.directory(home, owned_root=home, create=True):
            pass
        _initialize_update_directory(home, native=True, directory=windows_files.directory)
        updates = home / "updates"
        # The production boundary checks owner/DACL/namespace before any lease
        # payload. This is a bounded native seam, not full app/worker startup.
        with windows_files.directory(updates, owned_root=home) as pinned:
            self.assertFalse((updates / "worker-leases").exists())
        config = SimpleNamespace(write_private=_native_lease_writer(home))
        generation = "a" * 32
        with patch.dict(sys.modules, {"amplifier_web.host.config": config}):
            lease = generation_leases.acquire(home, generation, os.getpid())
        with windows_files.directory(lease.parent, owned_root=home) as pinned:
            self.assertEqual(json.loads(pinned.read_private(lease.name, limit=1024)),
                             {"schema": 1, "generation": generation, "pid": os.getpid()})
        self.assertEqual(generation_leases.references(home), {generation})
        self.assertTrue(lease.exists())
        # Existing safe update state still opens without mutation/recreation.
        _initialize_update_directory(home, native=True, directory=windows_files.directory)
        self.assertEqual(generation_leases.references(home), {generation})
        lease.unlink()
        self.assertEqual(generation_leases.references(home), set())


class GenerationLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def lease(self, pid=123):
        path = self.home / "updates/worker-leases/worker.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": 1, "generation": "source", "pid": pid}))
        return path

    def windows(self, *, handle=0x100000001, wait=258, error=0):
        kernel32 = SimpleNamespace(
            OpenProcess=Mock(return_value=handle),
            WaitForSingleObject=Mock(return_value=wait),
            CloseHandle=Mock(return_value=1),
        )
        kill = Mock(side_effect=AssertionError("Windows liveness must not signal"))
        self.enterContext(
            patch.object(generation_leases, "os", SimpleNamespace(name="nt", kill=kill))
        )
        load = self.enterContext(
            patch.object(ctypes, "WinDLL", return_value=kernel32, create=True)
        )
        self.enterContext(
            patch.object(ctypes, "get_last_error", return_value=error, create=True)
        )
        return kernel32, load, kill

    def test_windows_live_or_reused_pid_is_retained_without_signaling(self):
        path = self.lease()
        kernel32, load, kill = self.windows()

        self.assertEqual(generation_leases.references(self.home), {"source"})
        self.assertTrue(path.exists())
        load.assert_called_once_with("kernel32", use_last_error=True)
        kernel32.OpenProcess.assert_called_once_with(0x00100000, False, 123)
        kernel32.WaitForSingleObject.assert_called_once_with(0x100000001, 0)
        kernel32.CloseHandle.assert_called_once_with(0x100000001)
        kill.assert_not_called()
        self.assertEqual(
            kernel32.OpenProcess.argtypes,
            [ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint32],
        )
        self.assertIs(kernel32.OpenProcess.restype, ctypes.c_void_p)
        self.assertEqual(
            kernel32.WaitForSingleObject.argtypes,
            [ctypes.c_void_p, ctypes.c_uint32],
        )
        self.assertIs(kernel32.WaitForSingleObject.restype, ctypes.c_uint32)
        self.assertEqual(kernel32.CloseHandle.argtypes, [ctypes.c_void_p])
        self.assertIs(kernel32.CloseHandle.restype, ctypes.c_int32)

    def test_windows_exited_process_reaps_lease_and_closes_handle(self):
        path = self.lease()
        kernel32, _, kill = self.windows(wait=0)

        self.assertEqual(generation_leases.references(self.home), set())
        self.assertFalse(path.exists())
        kernel32.CloseHandle.assert_called_once_with(0x100000001)
        kill.assert_not_called()

    def test_windows_absent_process_reaps_lease_without_a_handle(self):
        path = self.lease()
        kernel32, _, kill = self.windows(handle=None, error=87)

        self.assertEqual(generation_leases.references(self.home), set())
        self.assertFalse(path.exists())
        kernel32.WaitForSingleObject.assert_not_called()
        kernel32.CloseHandle.assert_not_called()
        kill.assert_not_called()

    def test_windows_open_errors_retain_lease(self):
        path = self.lease()
        kernel32, _, _ = self.windows(handle=None)
        for error in (5, 0, 6, 8):
            with self.subTest(error=error), patch.object(
                ctypes, "get_last_error", return_value=error
            ):
                self.assertEqual(generation_leases.references(self.home), {"source"})
                self.assertTrue(path.exists())
        kernel32.WaitForSingleObject.assert_not_called()
        kernel32.CloseHandle.assert_not_called()

    def test_windows_wait_errors_retain_lease_and_close_handle(self):
        path = self.lease()
        kernel32, _, _ = self.windows()
        for result in (0xFFFFFFFF, 0x80, 42):
            with self.subTest(result=result):
                kernel32.WaitForSingleObject.return_value = result
                kernel32.CloseHandle.reset_mock()
                self.assertEqual(generation_leases.references(self.home), {"source"})
                self.assertTrue(path.exists())
                kernel32.CloseHandle.assert_called_once_with(0x100000001)

    def test_windows_wait_exception_retains_lease_and_closes_handle(self):
        path = self.lease()
        kernel32, _, _ = self.windows()
        kernel32.WaitForSingleObject.side_effect = OSError("wait unavailable")

        with self.assertRaisesRegex(OSError, "wait unavailable"):
            generation_leases.references(self.home)
        self.assertTrue(path.exists())
        kernel32.CloseHandle.assert_called_once_with(0x100000001)

    def test_windows_loader_failure_retains_lease(self):
        path = self.lease()
        _, load, kill = self.windows()
        load.side_effect = OSError("inspection unavailable")

        with self.assertRaisesRegex(OSError, "inspection unavailable"):
            generation_leases.references(self.home)
        self.assertTrue(path.exists())
        kill.assert_not_called()

    def test_windows_oversized_pid_is_not_truncated(self):
        path = self.lease(pid=0x100000000)
        kernel32, load, kill = self.windows()

        self.assertEqual(generation_leases.references(self.home), {"source"})
        self.assertTrue(path.exists())
        load.assert_not_called()
        kernel32.OpenProcess.assert_not_called()
        kill.assert_not_called()

    def test_posix_liveness_semantics_are_unchanged(self):
        for outcome, retained in (
            (None, True),
            (PermissionError(), True),
            (ProcessLookupError(), False),
        ):
            with self.subTest(outcome=outcome):
                path = self.lease()
                kill = Mock(side_effect=outcome)
                with patch.object(
                    generation_leases, "os", SimpleNamespace(name="posix", kill=kill)
                ):
                    self.assertEqual(
                        generation_leases.references(self.home),
                        {"source"} if retained else set(),
                    )
                kill.assert_called_once_with(123, 0)
                self.assertEqual(path.exists(), retained)

    def test_posix_unknown_error_fails_closed(self):
        path = self.lease()
        with patch.object(
            generation_leases,
            "os",
            SimpleNamespace(name="posix", kill=Mock(side_effect=OSError("unknown"))),
        ):
            with self.assertRaisesRegex(OSError, "unknown"):
                generation_leases.references(self.home)
        self.assertTrue(path.exists())

    def test_invalid_lease_fails_closed_before_process_check(self):
        for pid in (0, -1, True, "123"):
            with self.subTest(pid=pid):
                path = self.lease(pid=pid)
                with patch.object(generation_leases, "_process_exists") as exists:
                    with self.assertRaisesRegex(ValueError, "storage retained"):
                        generation_leases.references(self.home)
                exists.assert_not_called()
                self.assertTrue(path.exists())

    def test_real_subprocess_survives_checks_and_exited_lease_is_reaped(self):
        # Runs the real platform implementation, not Win32 stubs. The marker is
        # essential: Windows TerminateProcess(..., 0) can also return exit code 0.
        script = (
            "import sys; "
            "print('ready', flush=True); "
            "sys.stdin.read(); "
            "print('survived', flush=True)"
        )
        with subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as child:
            path = self.lease(pid=child.pid)
            try:
                for _ in range(3):
                    self.assertEqual(generation_leases.references(self.home), {"source"})
                    self.assertTrue(path.exists())
                output, errors = child.communicate("release\n", timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, errors)
            self.assertEqual(output.splitlines(), ["ready", "survived"])
        self.assertEqual(generation_leases.references(self.home), set())
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()