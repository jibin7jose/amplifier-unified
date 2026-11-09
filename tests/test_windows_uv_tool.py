"""Portable refusal oracles; real NTFS/uv qualification is separate."""
from contextlib import contextmanager
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from amplifier_web import windows_uv_tool


class WindowsUvLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # The fixture, unlike a caller's untrusted installation address, may
        # choose its real temporary spelling up front. GitHub's Windows TEMP
        # contains RUNNER~1; macOS /var is a symlink to /private/var.
        self.root = Path(temporary.name).resolve()
        self.prefix = self.root / "tools/amplifier-unified"
        self.bin = self.root / "bin"
        (self.prefix / "Scripts").mkdir(parents=True)
        self.bin.mkdir()
        self.launcher = self.prefix / "Scripts/amplifier-unified.exe"
        self.public = self.bin / self.launcher.name
        self.receipt = self.prefix / "uv-receipt.toml"
        self.write_receipt()
        self.launcher.write_bytes(b"MZactual-copied-uv-launcher")
        self.public.write_bytes(self.launcher.read_bytes())
        self.reads = []
        self.pins = []

    def write_receipt(self, *, name="amplifier-unified", package="amplifier-unified", path=None):
        # JSON string syntax is valid TOML for these ordinary test paths.
        import json
        self.receipt.write_text('[tool]\n[[tool.entrypoints]]\n'
                                f'name = "{name}"\nfrom = "{package}"\n'
                                'install-path = ' + json.dumps(str(path or self.public)) + '\n')

    @contextmanager
    def directory(self, path, *, owned_root):
        self.assertIn((path, owned_root), [
            (self.prefix, self.prefix.parent), (self.launcher.parent, self.prefix.parent),
            (self.bin, self.bin)])
        self.pins.append(path)
        outer = self

        class Read:
            def read_private(self, name, *, limit):
                outer.assertEqual(len(outer.pins), 3)
                outer.assertEqual(limit, 1024 * 1024)
                outer.reads.append((path, name))
                raw = (path / name).read_bytes()
                if len(raw) > limit:
                    raise ValueError("oversized private read")
                return raw
        try:
            yield Read()
        finally:
            self.assertEqual(self.pins.pop(), path)

    def validate(self, executable=None):
        with patch.object(windows_uv_tool.windows_files, "directory", self.directory):
            windows_uv_tool.validate_launcher(self.prefix, self.bin, str(executable or self.public))
        self.assertEqual(self.pins, [])

    def test_real_copies_require_joined_receipt_and_bounded_private_reads(self):
        self.assertFalse(self.launcher.samefile(self.public))
        self.validate()
        self.assertEqual(len(self.reads), 6)

    def test_explicit_environment_entrypoint_requires_same_public_receipt(self):
        self.validate(self.launcher)

    def test_foreign_selected_launcher_refuses_even_when_bytes_equal(self):
        foreign = self.root / "foreign.exe"
        foreign.write_bytes(self.launcher.read_bytes())
        with self.assertRaisesRegex(ValueError, "outside"):
            self.validate(foreign)
        self.assertEqual(self.reads, [])

    def test_missing_or_corrupt_receipt_never_adopts_equal_bytes(self):
        self.receipt.unlink()
        with self.assertRaises(FileNotFoundError):
            self.validate()
        self.receipt.write_text("not valid toml")
        with self.assertRaises(ValueError):
            self.validate()
        for body in ('tool = "invalid"', '[tool]\nentrypoints = "invalid"',
                     '[tool]\nentrypoints = [1, 2]'):
            self.receipt.write_text(body)
            with self.assertRaisesRegex(ValueError, "no valid entry points"):
                self.validate()

    def test_receipt_other_path_package_name_or_duplicate_refuses(self):
        for arguments in ({"path": self.root / "elsewhere.exe"},
                          {"package": "other-package"}, {"name": "other-tool"}):
            with self.subTest(arguments=arguments):
                self.write_receipt(**arguments)
                with self.assertRaisesRegex(ValueError, "does not own"):
                    self.validate()
        self.write_receipt()
        with self.receipt.open("a") as file:
            file.write('[[tool.entrypoints]]\nname="amplifier-unified"\n')
        with self.assertRaisesRegex(ValueError, "does not own"):
            self.validate()

    def test_changed_empty_or_oversized_copy_refuses(self):
        for data in (b"MZforeign", b"", b"MZ" + b"x" * (1024 * 1024)):
            with self.subTest(size=len(data)):
                self.public.write_bytes(data)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_equal_non_executable_files_are_not_launchers(self):
        self.public.write_bytes(b"not an executable")
        self.launcher.write_bytes(self.public.read_bytes())
        with self.assertRaisesRegex(ValueError, "differs"):
            self.validate()

    def test_private_backend_refusals_are_not_bypassed(self):
        for error in (PermissionError("reparse/hardlink/ACL"), OSError("native read failed")):
            with self.subTest(error=type(error).__name__):
                with patch.object(windows_uv_tool.windows_files, "directory", side_effect=error):
                    with self.assertRaises(type(error)):
                        windows_uv_tool.validate_launcher(self.prefix, self.bin, str(self.public))

    def test_joined_files_changed_during_verification_refuse(self):
        original = self.directory
        for target in (self.receipt, self.launcher, self.public):
            with self.subTest(target=target.name):
                self.write_receipt()
                self.launcher.write_bytes(b"MZactual-copied-uv-launcher")
                self.public.write_bytes(self.launcher.read_bytes())
                reads = 0

                @contextmanager
                def changing(path, *, owned_root):
                    nonlocal reads
                    with original(path, owned_root=owned_root) as pinned:
                        read = pinned.read_private
                        def mutate(name, *, limit):
                            nonlocal reads
                            reads += 1
                            if reads == 4:
                                target.write_bytes(target.read_bytes() + b"changed")
                            return read(name, limit=limit)
                        pinned.read_private = mutate
                        yield pinned
                with patch.object(windows_uv_tool.windows_files, "directory", changing):
                    with self.assertRaisesRegex(ValueError, "changed"):
                        windows_uv_tool.validate_launcher(self.prefix, self.bin, str(self.public))


class InstalledTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.files = WindowsUvLauncherTests()
        self.files.setUp()
        self.addCleanup(self.files.doCleanups)

    async def target(self, *, platform="nt", running=None, generation=None, supports=True):
        """Execute the maintained async owner without importing the whole service."""
        import ast
        import builtins
        import json
        from types import SimpleNamespace
        file = Path(__file__).resolve().parents[1] / "amplifier_web/app_updates.py"
        node = next(n for n in ast.parse(file.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "installed_target")
        f = self.files
        imports = {
            "application_generations": SimpleNamespace(active=lambda home: generation,
                                                       supports=lambda prefix: supports),
            "host.config": SimpleNamespace(app_home=lambda: f.root / "unused"),
            "windows_uv_tool": windows_uv_tool,
        }

        def load(name, *args, **kwargs):
            if name in imports:
                return imports[name]
            raise AssertionError("Unexpected import: " + name)

        async def process(*args, **kwargs):
            self.assertEqual(args[:3], (str(f.root / "uv.exe"), "tool", "dir"))
            return str(f.bin if "--bin" in args else f.prefix.parent)

        class AbsentMetadata:
            PackageNotFoundError = ModuleNotFoundError
            @staticmethod
            def distribution(name):
                raise ModuleNotFoundError(name)

        selected = f.public if platform == "nt" else f.bin / "amplifier-unified"
        def which(name):
            if name == "uv":
                return str(f.root / "uv.exe")
            expected = "amplifier-unified.exe" if platform == "nt" else "amplifier-unified"
            self.assertEqual(name, expected)
            return str(selected)
        namespace = {
            "__builtins__": {**vars(builtins), "__import__": load},
            "Path": Path, "os": SimpleNamespace(name=platform),
            "sys": SimpleNamespace(prefix=str(running or f.prefix)), "__version__": "fixture",
            "shutil": SimpleNamespace(which=which),
            "process": process, "git_environment": lambda: {}, "json": json,
            "metadata": AbsentMetadata,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(file), "exec"), namespace)
        with patch.object(windows_uv_tool.windows_files, "directory", f.directory):
            return await namespace["installed_target"]()

    async def test_windows_owned_copied_launcher_accepted_by_actual_owner(self):
        result = await self.target()
        self.assertEqual(result[1], str(self.files.public))
        self.assertEqual(result[2], self.files.prefix / "Scripts/python.exe")
        self.assertEqual(result[3]["installation"], str(self.files.prefix))

    async def test_windows_manual_venv_rejected_before_receipt_reads(self):
        with self.assertRaisesRegex(ValueError, "not running from the active uv tool"):
            await self.target(running=self.files.root / "manual")
        self.assertEqual(self.files.reads, [])

    async def test_active_generation_still_needs_supported_tool_and_receipt(self):
        candidate = self.files.root / "active-generation"
        result = await self.target(running=candidate, generation={"prefix": candidate})
        self.assertEqual(result[2], candidate / "Scripts/python.exe")
        with self.assertRaisesRegex(ValueError, "not running from the active uv tool"):
            await self.target(running=candidate, generation={"prefix": candidate}, supports=False)
        self.files.receipt.unlink()
        with self.assertRaises(FileNotFoundError):
            await self.target(running=candidate, generation={"prefix": candidate})

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics run on POSIX, not a Windows symlink grant")
    async def test_posix_symlink_accepted_without_windows_copy_exception(self):
        f = self.files
        (f.prefix / "bin").mkdir()
        launcher = f.prefix / "bin/amplifier-unified"
        launcher.write_bytes(b"posix-entrypoint")
        (f.bin / "amplifier-unified").symlink_to(launcher)
        self.assertEqual((await self.target(platform="posix"))[1], str(f.bin / "amplifier-unified"))
        self.assertEqual(f.reads, [])

    async def test_posix_copy_still_rejected_even_with_matching_bytes(self):
        f = self.files
        (f.prefix / "bin").mkdir()
        (f.prefix / "bin/amplifier-unified").write_bytes(b"posix-entrypoint")
        (f.bin / "amplifier-unified").write_bytes(b"posix-entrypoint")
        with self.assertRaisesRegex(ValueError, "not running from the active uv tool"):
            await self.target(platform="posix")
        self.assertEqual(f.reads, [])


class WindowsSpellingTests(unittest.TestCase):
    """PureWindowsPath exposes case folding even on a POSIX test host."""
    def validate(self, *, selected=None, receipt=None):
        import json
        from pathlib import PureWindowsPath
        prefix = PureWindowsPath(r"C:\Private\tools\amplifier-unified")
        bin_root = PureWindowsPath(r"C:\Private\bin")
        public = bin_root / "amplifier-unified.exe"
        raw = ('[tool]\n[[tool.entrypoints]]\nname="amplifier-unified"\n'
               'from="amplifier-unified"\ninstall-path='
               + json.dumps(receipt or str(public))).encode()

        def address(value):
            value = str(value).replace("/", "\\")
            return PureWindowsPath(value[0].upper() + value[1:])

        @contextmanager
        def directory(path, *, owned_root):
            class Read:
                def read_private(self, name, *, limit):
                    return raw if name == "uv-receipt.toml" else b"MZmatching-copies"
            yield Read()
        with (patch.object(windows_uv_tool.windows_files, "address", address),
              patch.object(windows_uv_tool.windows_files, "directory", directory)):
            windows_uv_tool.validate_launcher(prefix, bin_root, selected or str(public))

    def test_selected_case_alias_is_rejected_before_expected_path_reads(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            self.validate(selected=r"C:\Private\BIN\amplifier-unified.exe")

    def test_receipt_case_alias_does_not_own_expected_public_path(self):
        with self.assertRaisesRegex(ValueError, "does not own"):
            self.validate(receipt=r"C:\Private\BIN\amplifier-unified.exe")

    def test_only_drive_spelling_is_normalized_by_existing_address_owner(self):
        self.validate(selected=r"c:\Private\bin\amplifier-unified.exe",
                      receipt=r"c:\Private\bin\amplifier-unified.exe")


@unittest.skipUnless(os.name == "nt", "requires native Win64 private-file backend")
class NativeWindowsUvLauncherTests(unittest.TestCase):
    def test_native_private_copy_and_hardlink_refusal(self):
        import json
        import shutil
        import uuid
        root = Path(tempfile.gettempdir()).resolve() / ("uv-copy-" + uuid.uuid4().hex)
        backend = windows_uv_tool.windows_files
        with backend.directory(root, owned_root=root, create=True):
            pass
        self.addCleanup(shutil.rmtree, root)
        prefix, bin_root = root / "tools/amplifier-unified", root / "bin"
        public = bin_root / "amplifier-unified.exe"
        launcher = prefix / "Scripts" / public.name
        for path in (prefix, launcher.parent, bin_root):
            with backend.directory(path, owned_root=root, create=True):
                pass
        receipt = ('[tool]\n[[tool.entrypoints]]\nname="amplifier-unified"\n'
                   'from="amplifier-unified"\ninstall-path=' + json.dumps(str(public)) + '\n')
        for path, data in ((prefix / "uv-receipt.toml", receipt.encode()),
                           (launcher, b"MZnative-private-test-copy"),
                           (public, b"MZnative-private-test-copy")):
            with backend.directory(path.parent, owned_root=root) as pinned:
                pinned.publish_private(path.name, data)
        self.assertFalse(launcher.samefile(public))
        windows_uv_tool.validate_launcher(prefix, bin_root, str(public))
        public.unlink()
        os.link(launcher, public)
        with self.assertRaises(PermissionError):
            windows_uv_tool.validate_launcher(prefix, bin_root, str(public))


if __name__ == "__main__":
    unittest.main()