"""Run with .venv/bin/python -I -S tests/test_windows_files.py.

Portable tests check policy/ordering, not Windows kernel behavior. Native tests
use a new directory under the current profile, never repair a runner's temp ACL.
"""
from __future__ import annotations

import ctypes as C
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "amplifier_web/windows_files.py"
spec = importlib.util.spec_from_file_location("windows_files", SOURCE)
wf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wf)


class PolicyTests(unittest.TestCase):
    def test_win64_abi(self):
        self.assertEqual(C.sizeof(wf.DWORD), 4)
        self.assertEqual(C.sizeof(wf.BOOL), 4)
        self.assertEqual(C.sizeof(wf.FILE_INFO), 52)
        if C.sizeof(C.c_void_p) == 8:
            self.assertEqual(C.sizeof(wf.SECURITY_ATTRIBUTES), 24)
        self.assertIs(wf._SIGNATURES["CreateFileW"][0], C.c_void_p)
        self.assertIs(wf._SIGNATURES["GetCurrentProcess"][0], C.c_void_p)

    def test_rename_info_win64_layout_and_binding(self):
        self.assertEqual(C.sizeof(wf.FILE_RENAME_INFO._fields_[0][1]), 4)  # Flags, not BOOLEAN
        self.assertEqual(C.sizeof(wf.FILE_RENAME_INFO._fields_[-1][1]), 2)  # WCHAR, not host wchar
        if C.sizeof(C.c_void_p) == 8:
            self.assertEqual(wf.FILE_RENAME_INFO.RootDirectory.offset, 8)
            self.assertEqual(wf.FILE_RENAME_INFO.FileNameLength.offset, 16)
            self.assertEqual(wf.FILE_RENAME_INFO.FileName.offset, 20)
            self.assertEqual(C.sizeof(wf.FILE_RENAME_INFO), 24)
        result, arguments = wf._SIGNATURES["SetFileInformationByHandle"]
        self.assertIs(result, wf.BOOL)
        self.assertEqual(arguments, [wf.HANDLE, C.c_int32, wf.PVOID, wf.DWORD])
        self.assertNotIn("MoveFileExW", wf._SIGNATURES)

    def test_components_and_local_paths(self):
        for name in ("server.yaml", "session-secret", "control-token", "配置"):
            wf._component(name)
        for name in ("", ".", "..", "a/b", "a\\b", "NUL.txt", "NUL .txt", "COM¹",
                     "LPT9", "x:stream", "a ", "a.", "x~1", "a\0", "a?"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                wf._component(name)
        for name in (r"\\server\share\x", r"\\?\C:\x", r"\\.\C:\x",
                     r"C:x", r"C:\x\..\y", r"C:\x\.\y", "C:\\" + "a" * 256):
            with self.subTest(path=name), self.assertRaises(ValueError):
                wf._local(name)
        wf._local(r"C:\Users\Example\.amplifier-unified\config")
        wf._local("C:\\")

    def test_foreign_traversal_grants(self):
        # Only directory creation is exempt; file creation and attribute writes
        # can convert a pinned ancestor to a reparse point.
        wf._check_ace(0, 0, 4, "foreign", private=False)
        for mask in (2, 0x100, 0x40, 0x10000, 0x40000, 0x80000,
                     0x40000000, 0x10000000):
            with self.subTest(mask=mask), self.assertRaises(PermissionError):
                wf._check_ace(0, 0, mask, "foreign", private=False)
        with self.assertRaises(PermissionError):
            wf._check_ace(0, 0, 1, "foreign", private=True)

    def test_acl_policy_fails_closed(self):
        for kind, mask in ((5, 1), (0, 0x2000000)):
            with self.assertRaises(PermissionError):
                wf._check_ace(kind, 0, mask, "owner", private=True)
        for sid in ("owner", *wf._OS_AUTHORITIES, "S-1-3-4"):
            wf._check_ace(0, 0, 0x1F01FF, sid, private=True)
        wf._check_ace(1, 0, 0x1F01FF, "foreign", private=True)
        wf._check_ace(0, 8, 0x1F01FF, "S-1-3-0", private=False)
        with self.assertRaises(PermissionError):
            wf._check_ace(0, 8, 0x1F01FF, "foreign", private=True)

    def test_address_does_not_resolve_symlinks(self):
        with patch.object(Path, "resolve", side_effect=AssertionError("resolve")):
            self.assertTrue(wf.address("relative").is_absolute())

    def test_security_attributes_are_supplied_at_create(self):
        api = wf._API.__new__(wf._API)
        events = []

        @contextmanager
        def security(*, directory=False):
            events.append("private-dacl")
            yield wf.SECURITY_ATTRIBUTES(C.sizeof(wf.SECURITY_ATTRIBUTES), 123, False)

        def create(path, access, sharing, attributes, disposition, flags, template):
            attributes = C.cast(attributes, C.POINTER(wf.SECURITY_ATTRIBUTES)).contents
            self.assertEqual(attributes.descriptor, 123)
            self.assertFalse(attributes.inherit)
            self.assertEqual(disposition, 1)  # CREATE_NEW
            self.assertEqual(sharing & 2, 0)  # no writer sharing
            self.assertTrue(access & 0x10000)  # DELETE for retained-handle rename/cleanup
            events.append("create")
            return 100

        api.private_security, api.CreateFileW = security, create
        api.info = lambda *args, **kwargs: None
        api.open(wf.PureWindowsPath(r"C:\private\temporary"), create=True)
        self.assertEqual(events, ["private-dacl", "create"])

    def test_reopened_rename_source_has_delete_not_write_and_preserves_sharing(self):
        api = wf._API.__new__(wf._API)
        calls = []

        @contextmanager
        def security(*, directory=False):
            yield wf.SECURITY_ATTRIBUTES()

        def open_file(path, access, sharing, attributes, disposition, flags, template):
            self.assertTrue(access & 0x10000)  # DELETE, required for rename
            self.assertTrue(access & 0x80000000)  # READ, not WRITE
            self.assertFalse(access & 0x40000000)
            self.assertEqual(sharing, 5)  # READ | DELETE, never WRITE
            self.assertEqual(disposition, 3)  # OPEN_EXISTING, never create/truncate
            self.assertEqual(flags & 0x200000, 0x200000)  # OPEN_REPARSE_POINT
            calls.append(path)
            return 101

        api.private_security, api.CreateFileW = security, open_file
        api.info = lambda *args, **kwargs: None
        self.assertEqual(api.open(wf.PureWindowsPath(r"C:\private\temporary"), cleanup=True), 101)
        self.assertEqual(calls, [r"C:\private\temporary"])

    def test_existing_inherited_acl_and_wrong_owner_null_dacl(self):
        # Real ACL policy method with injected descriptor pointers. Native
        # inherited ACL coverage below tests the kernel's actual descriptor.
        api = wf._API.__new__(wf._API)
        api.sid = "user"
        owner = ["user"]
        present = [True]

        def security(handle, kind, fields, owner_out, group, dacl_out, sacl, sd_out):
            C.cast(owner_out, C.POINTER(wf.PVOID))[0] = 1
            C.cast(dacl_out, C.POINTER(wf.PVOID))[0] = 2 if present[0] else None
            return 0

        def control(sd, output, revision):
            C.cast(output, C.POINTER(wf.WORD))[0] = 4  # present, NOT protected
            return True

        api.GetSecurityInfo, api.GetSecurityDescriptorControl = security, control
        api.sid_text = lambda pointer: owner[0]
        api.GetAclInformation = lambda *args: True  # empty DACL: grants nobody
        api.LocalFree = lambda *args: None
        api.validate_acl(1, private=True)
        for candidate in ("foreign", *sorted(wf._OS_AUTHORITIES)):
            with self.subTest(owner=candidate):
                owner[0] = candidate
                # OS authorities are trusted traversal owners, not TokenUser
                # owners of private state (including on an elevated runner).
                with self.assertRaises(PermissionError):
                    api.validate_acl(1, private=True)
                if candidate in wf._OS_AUTHORITIES:
                    api.validate_acl(1, private=False)
        owner[0], present[0] = "user", False
        with self.assertRaises(PermissionError):
            api.validate_acl(1, private=True)

    def test_inherited_fixture_oracle_uses_structure_not_sddl_aliases(self):
        from types import SimpleNamespace
        api = wf._API.__new__(wf._API)
        api.sid = "S-1-5-21-100-200-300-500"
        state = {"owner": api.sid, "control": 4, "dacl": 2, "flags": 0x10}
        ace = wf.ACE()
        rendered = "O:LAD:AI(A;ID;FA;;;LA)"
        rendered_buffer = C.create_unicode_buffer(rendered)

        def security(handle, kind, fields, owner, group, dacl, sacl, descriptor):
            C.cast(owner, C.POINTER(wf.PVOID))[0] = 1
            C.cast(dacl, C.POINTER(wf.PVOID))[0] = state["dacl"]
            C.cast(descriptor, C.POINTER(wf.PVOID))[0] = 3
            return 0

        def control(descriptor, output, revision):
            C.cast(output, C.POINTER(wf.WORD))[0] = state["control"]
            return True

        def acl_info(dacl, output, size, kind):
            C.cast(output, C.POINTER(wf.ACL_INFO)).contents.count = 1
            return True

        def get_ace(dacl, index, output):
            ace.flags = state["flags"]
            C.cast(output, C.POINTER(wf.PVOID))[0] = C.addressof(ace)
            return True

        def convert(descriptor, revision, fields, output, length):
            C.cast(output, C.POINTER(wf.PVOID))[0] = C.addressof(rendered_buffer)
            return True

        api.GetSecurityInfo, api.GetSecurityDescriptorControl = security, control
        api.GetAclInformation, api.GetAce = acl_info, get_ace
        api.sid_text = lambda owner: state["owner"]
        api.open = lambda *a, **k: 10
        api.close = api.LocalFree = lambda value: None
        dll = SimpleNamespace(ConvertSecurityDescriptorToStringSecurityDescriptorW=convert)
        with patch.object(wf, "_API", return_value=api), patch.object(C, "WinDLL", return_value=dll, create=True):
            snapshot = lambda: NativeTests.security_text(self, SOURCE, inherited=True)
            self.assertEqual(snapshot(), rendered)  # LA alias is not a different owner.
            for field, value in (("owner", "S-1-5-32-544"), ("control", 0x1004),
                                 ("control", 0), ("dacl", 0), ("flags", 0)):
                with self.subTest(field=field, value=value), patch.dict(state, {field: value}):
                    # Unchanged plausible SDDL cannot hide a structurally bad fixture.
                    with self.assertRaises(AssertionError):
                        snapshot()

    def test_new_security_descriptor_is_protected_owner_and_system(self):
        api = wf._API.__new__(wf._API)
        api.sid = "S-1-5-21-100-200-300-1001"
        descriptors = []

        def convert(sddl, revision, output, length):
            descriptors.append(sddl)
            C.cast(output, C.POINTER(wf.PVOID))[0] = 123
            return True

        api.ConvertStringSecurityDescriptorToSecurityDescriptorW = convert
        api.LocalFree = lambda value: None
        with api.private_security(directory=True) as security:
            self.assertEqual(security.descriptor, 123)
        self.assertEqual(descriptors, [
            f"O:{api.sid}D:P(A;OICI;FA;;;{api.sid})(A;OICI;FA;;;SY)"])

    def test_exact_handle_cleanup_checks_identity_before_delete(self):
        api = wf._API.__new__(wf._API)
        api.info = lambda *args, **kwargs: wf.FILE_INFO(volume=1, links=1, index_low=99)
        deleted = []
        api.SetFileInformationByHandle = lambda *args: deleted.append(args) or True
        with self.assertRaises(PermissionError):
            api.discard(1, Path("owned-temp"), (1, 0, 98))
        self.assertFalse(deleted)
        api.discard(1, Path("owned-temp"), (1, 0, 99))
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0][:2], (1, 4))

    def test_broad_ancestor_is_checked_before_opening_any_child(self):
        events = []
        root = Path.cwd()
        target = root / "owned" / "child"

        class API:
            def GetDriveTypeW(self, path):
                return 3

            def open(self, path, **kwargs):
                events.append(("open", path))
                return 100

            def info(self, *args, **kwargs):
                return wf.FILE_INFO(volume=1, index_low=1)

            def GetVolumeInformationByHandleW(self, handle, a, b, c, d, e, fs, size):
                fs.value = "NTFS"
                return True

            ok = staticmethod(wf._API.ok)

            def validate_acl(self, handle, **kwargs):
                events.append(("acl", handle))
                raise PermissionError("untrusted attributes grant")

            def close(self, handle):
                events.append(("close", handle))

        with patch.object(wf, "_API", API), self.assertRaises(PermissionError):
            with wf.directory(target, owned_root=root / "owned", create=True):
                pass
        self.assertEqual([event[0] for event in events], ["open", "acl", "close"])

    @unittest.skipIf(os.name == "nt", "non-Windows refusal")
    def test_no_posix_privacy_emulation(self):
        with self.assertRaises(RuntimeError):
            with wf.directory(Path.cwd(), owned_root=Path.cwd()):
                pass


class RenameTests(unittest.TestCase):
    def test_exact_source_handle_utf16_destination_and_explicit_flags(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                api = wf._API.__new__(wf._API)
                calls = []
                source = 0x1234567887654321
                target = wf.PureWindowsPath("C:/private/config/配置-😀.yaml")
                encoded = str(target).encode("utf-16-le")

                def rename(handle, kind, buffer, size):
                    calls.append("rename")
                    self.assertEqual(handle, source)
                    self.assertEqual(kind, 22)  # FileRenameInfoEx
                    info = wf.FILE_RENAME_INFO.from_buffer(buffer)
                    self.assertEqual(info.Flags, 3 if replace else 0)
                    self.assertIsNone(info.RootDirectory)
                    self.assertEqual(info.FileNameLength, len(encoded))
                    self.assertGreaterEqual(size, C.sizeof(wf.FILE_RENAME_INFO) + len(encoded))
                    self.assertEqual(C.string_at(
                        C.addressof(buffer) + wf.FILE_RENAME_INFO.FileName.offset, len(encoded)), encoded)
                    return True

                api.SetFileInformationByHandle = rename
                api.move(source, target, replace=replace)
                self.assertEqual(calls, ["rename"])

    def test_failed_rename_is_not_retried_or_fallback_and_collision_keeps_type(self):
        for code in (5, 32, 80, 183, 1, 50, 87, 120):
            with self.subTest(code=code):
                api = wf._API.__new__(wf._API)
                calls = []
                failure = FileExistsError("winner") if code in {80, 183} else OSError("native failure")
                failure.winerror = code

                def rename(*args):
                    calls.append("rename")
                    return False

                def checked(result):
                    self.assertFalse(result)
                    raise failure

                api.SetFileInformationByHandle, api.ok = rename, checked
                unsupported = code in {1, 50, 87, 120}
                with self.assertRaises(NotImplementedError if unsupported else type(failure)) as caught:
                    api.move(123, wf.PureWindowsPath("C:/private/server.yaml"), replace=True)
                if unsupported:
                    self.assertIn("Windows 10", str(caught.exception))
                    self.assertIn("no fallback", str(caught.exception))
                    self.assertIs(caught.exception.__cause__, failure)
                else:
                    self.assertIs(caught.exception, failure)
                self.assertEqual(calls, ["rename"])


class PublicationTests(unittest.TestCase):
    """Injected handles prove control flow, not NTFS semantics."""

    class API:
        sid = "owner"

        def __init__(self):
            self.events = []
            self.present = False
            self.flush_error = False
            self.rename_error = None
            self.discard_error = None
            self.post_error = False
            self.renamed = False
            self.bytes = bytearray()

        def open(self, path, *, directory=False, create=False, cleanup=False):
            self.events.append(("open", path.name, create))
            if not create and not cleanup and not self.present and not self.renamed:
                raise FileNotFoundError(path)
            return 10 if create or cleanup else 20

        def validate_acl(self, handle, *, private):
            self.events.append(("acl", handle))

        def info(self, handle, path, *, directory):
            if self.post_error and self.renamed:
                raise OSError("post-rename check")
            return wf.FILE_INFO(volume=1, links=1, index_low=handle)

        def write(self, handle, content):
            self.events.append(("write", handle))
            self.bytes.extend(content)

        def flush(self, handle):
            self.events.append(("flush", handle))
            if self.flush_error:
                raise OSError("flush failed")

        def move(self, handle, destination, *, replace):
            if handle != 10:
                raise AssertionError("rename must use the retained DELETE source handle")
            self.assert_not_closed_at_move()
            self.events.append(("move", replace))
            if self.rename_error:
                raise self.rename_error
            self.renamed = True

        def assert_not_closed_at_move(self):
            # The write handle was closed; the read/DELETE cleanup handle was
            # reopened and is still held. The prior target reader is held too.
            if len([event for event in self.events if event[0] == "open"]) < 3:
                raise AssertionError("missing source cleanup handle")
            if ("close", 20) in self.events:
                raise AssertionError("prior reader closed to work around rename failure")

        def discard(self, handle, path, identity):
            self.events.append(("discard", path.name))
            if self.discard_error is not None:
                raise self.discard_error

        def close(self, handle):
            self.events.append(("close", handle))

    def pinned(self, api):
        pinned = wf.PinnedDirectory.__new__(wf.PinnedDirectory)
        pinned.path = Path("private")
        pinned._api = api
        pinned._pins = [(Path("private"), 1, (1, 0, 1), True)]
        pinned._check = lambda: None
        return pinned

    def test_flush_failure_never_renames(self):
        api = self.API()
        api.flush_error = True
        with self.assertRaisesRegex(OSError, "flush"):
            self.pinned(api).publish_private("session-secret", b"credential")
        self.assertFalse(any(event[0] == "move" for event in api.events))
        self.assertTrue(any(event[0] == "discard" for event in api.events))

    def test_default_never_overwrites(self):
        api = self.API()
        api.present = True
        with self.assertRaises(FileExistsError):
            self.pinned(api).publish_private("control-token", b"new")
        self.assertFalse(any(event[0] == "write" for event in api.events))

    def test_acl_before_bytes_and_flush_before_move(self):
        api = self.API()
        self.pinned(api).publish_private("server.yaml", b"complete")
        self.assertEqual(api.bytes, b"complete")
        self.assertLess(api.events.index(("acl", 10)), api.events.index(("write", 10)))
        self.assertLess(api.events.index(("flush", 10)), api.events.index(("move", False)))
        self.assertFalse(any(event[0] == "discard" for event in api.events))
        # A completed publication exposes no live WRITE handle.
        close = api.events.index(("close", 10))
        self.assertLess(close, api.events.index(("move", False)))

    def test_racing_no_replace_error_is_preserved(self):
        api = self.API()
        api.rename_error = FileExistsError("winner")
        with self.assertRaises(FileExistsError):
            self.pinned(api).publish_private("control-token", b"loser")
        self.assertFalse(api.renamed)

    def test_collision_with_failed_cleanup_is_not_a_clean_race(self):
        api = self.API()
        collision = api.rename_error = FileExistsError("another creator won")
        cleanup = api.discard_error = PermissionError("exact temporary cleanup failed")
        with self.assertRaises(OSError) as caught:
            self.pinned(api).publish_private("server.yaml", b"loser")
        error = caught.exception
        self.assertNotIsInstance(error, FileExistsError)
        self.assertIn("temporary may remain", str(error))
        self.assertIsInstance(error.__cause__, ExceptionGroup)
        self.assertEqual(error.__cause__.exceptions, (collision, cleanup))
        self.assertFalse(api.renamed)
        self.assertEqual([event[0] for event in api.events].count("discard"), 1)
        self.assertEqual(api.events[-1], ("close", 10))

    def test_sharing_failure_has_no_delete_first_fallback(self):
        api = self.API()
        api.present = True
        api.rename_error = PermissionError("sharing conflict")
        with self.assertRaisesRegex(PermissionError, "sharing"):
            self.pinned(api).publish_private("server.yaml", b"new", replace=True)
        self.assertEqual([event for event in api.events if event[0] == "move"], [("move", True)])
        self.assertEqual(len([event for event in api.events if event[0] == "discard"]), 1)

    def test_unsupported_rename_cleans_temp_and_never_reports_publication(self):
        api = self.API()
        api.present = True
        api.rename_error = NotImplementedError("FileRenameInfoEx unsupported")
        with self.assertRaisesRegex(NotImplementedError, "unsupported"):
            self.pinned(api).publish_private("server.yaml", b"new", replace=True)
        self.assertFalse(api.renamed)
        self.assertEqual([event for event in api.events if event[0] == "move"], [("move", True)])
        self.assertEqual(len([event for event in api.events if event[0] == "discard"]), 1)

    def test_post_rename_failure_is_explicit_and_does_not_delete(self):
        api = self.API()
        api.post_error = True
        with self.assertRaisesRegex(OSError, "published"):
            self.pinned(api).publish_private("server.yaml", b"new", replace=True)
        self.assertFalse(any(event[0] == "discard" for event in api.events))

    def test_write_loops_on_partial_writes(self):
        api = wf._API.__new__(wf._API)
        written = bytearray()

        def write(handle, buffer, length, count, overlapped):
            size = min(length, 3)
            written.extend(C.string_at(buffer, size))
            C.cast(count, C.POINTER(wf.DWORD))[0] = size
            return True

        api.WriteFile = write
        api.write(1, b"complete credential")
        self.assertEqual(written, b"complete credential")
        api.WriteFile = lambda *args: True
        with self.assertRaisesRegex(OSError, "zero"):
            api.write(1, b"x")


@unittest.skipUnless(os.name == "nt", "requires native Windows local NTFS and profile ACLs")
class NativeTests(unittest.TestCase):
    def setUp(self):
        import uuid
        self.root = wf.address(Path.home() / (".unified-file-test-" + uuid.uuid4().hex))
        self.path = self.root / "config" / "auth"
        # Fail rather than silently weakening a broad runner/profile ancestor.
        with wf.directory(self.path, owned_root=self.root, create=True):
            pass
        self.addCleanup(self.cleanup)

    def cleanup(self):
        import shutil
        shutil.rmtree(self.root)

    def set_dacl(self, path, grant=""):
        """Fixture-only ACL changes, restricted to newly allocated test paths."""
        self.assertTrue(path.is_relative_to(self.root))
        api = wf._API()
        descriptor = wf.PVOID()
        api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;OICI;FA;;;{api.sid})(A;OICI;FA;;;SY){grant}",
            1, C.byref(descriptor), None))
        security = C.WinDLL("advapi32", use_last_error=True)
        function = security.SetFileSecurityW
        function.argtypes = [C.c_wchar_p, wf.DWORD, wf.PVOID]
        function.restype = wf.BOOL
        try:
            api.ok(function(str(path), 0x80000004, descriptor))
        finally:
            api.LocalFree(descriptor)

    def security_text(self, path, *, inherited=False):
        api = wf._API()
        handle = api.open(path, directory=path.is_dir())
        owner, dacl, descriptor = wf.PVOID(), wf.PVOID(), wf.PVOID()
        text = C.c_wchar_p()
        convert = C.WinDLL("advapi32", use_last_error=True).ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [wf.PVOID, wf.DWORD, wf.DWORD, C.POINTER(C.c_wchar_p), wf.PDWORD]
        convert.restype = wf.BOOL
        try:
            error = api.GetSecurityInfo(handle, 1, 5, C.byref(owner), None,
                                        C.byref(dacl), None, C.byref(descriptor))
            if error:
                raise C.WinError(error)
            if inherited:
                # SDDL may abbreviate an owner SID (for example LA). Check the
                # actual owner and inheritance structure, not display spelling.
                self.assertTrue(api.sid_text(owner) == api.sid, "fixture must be TokenUser-owned")
                control, revision = wf.WORD(), wf.DWORD()
                api.ok(api.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
                self.assertTrue(control.value & 4 and dacl, "fixture must have a non-null DACL")
                self.assertFalse(control.value & 0x1000, "fixture DACL must be unprotected")
                acl, inherited_aces = wf.ACL_INFO(), []
                api.ok(api.GetAclInformation(dacl, C.byref(acl), C.sizeof(acl), 2))
                for index in range(acl.count):
                    pointer = wf.PVOID()
                    api.ok(api.GetAce(dacl, index, C.byref(pointer)))
                    inherited_aces.append(bool(C.cast(pointer, C.POINTER(wf.ACE)).contents.flags & 0x10))
                self.assertTrue(any(inherited_aces), "fixture must have real inherited ACEs")
            api.ok(convert(descriptor, 1, 5, C.byref(text), None))
            return text.value
        finally:
            if text:
                api.LocalFree(C.cast(text, wf.PVOID))
            if descriptor:
                api.LocalFree(descriptor)
            api.close(handle)

    def junction(self, link, target):
        self.assertTrue(link.is_relative_to(self.root))
        self.assertTrue(target.is_relative_to(self.root))
        # mklink /J requires neither Developer Mode nor symlink privilege.
        # Separate arguments let list2cmdline quote spaces without backslash-
        # escaping embedded quotes that cmd.exe would treat as path characters.
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_safe_inherited_existing_directory_and_file(self):
        inherited = self.root / "inherited"
        target = inherited / "session-secret"
        api = wf._API()
        descriptor = wf.PVOID()
        # An elevated token can default to Administrators ownership. Specify
        # TokenUser at creation, but leave the DACL absent so the kernel
        # inherits the private parent's ACEs. Do not protect or repair it.
        api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{api.sid}", 1, C.byref(descriptor), None))
        try:
            security = wf.SECURITY_ATTRIBUTES(C.sizeof(wf.SECURITY_ATTRIBUTES), descriptor, False)
            api.ok(api.CreateDirectoryW(str(inherited), C.byref(security)))
            handle = api.CreateFileW(str(target), 0x40000000, 0, C.byref(security),
                                     1, 0x200000, None)  # CREATE_NEW | OPEN_REPARSE_POINT
            self.assertNotEqual(handle, wf._INVALID, "cannot create inherited fixture file")
            try:
                api.write(handle, b"safe-existing")
            finally:
                api.close(handle)
        finally:
            api.LocalFree(descriptor)
        before = {path: self.security_text(path, inherited=True) for path in (inherited, target)}
        with wf.directory(inherited, owned_root=self.root) as pinned:
            self.assertEqual(pinned.read_private("session-secret", limit=64), b"safe-existing")
        self.assertEqual({path: self.security_text(path, inherited=True) for path in before}, before)

    def test_foreign_read_grant_is_refused_unmodified(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"sentinel")
        target = self.path / "control-token"
        self.set_dacl(target, "(A;;FR;;;WD)")
        before = self.security_text(target)
        with wf.directory(self.path, owned_root=self.root) as pinned:
            with self.assertRaisesRegex(PermissionError, "foreign access"):
                pinned.read_private(target.name, limit=64)
            with self.assertRaisesRegex(PermissionError, "foreign access"):
                pinned.publish_private(target.name, b"bad", replace=True)
        self.assertEqual(self.security_text(target), before)
        self.assertEqual(target.read_bytes(), b"sentinel")

    def test_missing_child_below_broad_parent_creates_nothing(self):
        ancestor = self.root / "broad"
        ancestor.mkdir()
        self.set_dacl(ancestor, "(A;;0x100;;;WD)")  # FILE_WRITE_ATTRIBUTES
        before = self.security_text(ancestor)
        with self.assertRaisesRegex(PermissionError, "foreign access"):
            with wf.directory(ancestor / "app/config", owned_root=ancestor / "app", create=True):
                pass
        self.assertFalse((ancestor / "app").exists())
        self.assertEqual(self.security_text(ancestor), before)

    def test_intermediate_and_leaf_junctions_preserve_outside_sentinel(self):
        outside = self.root / "outside target"
        outside.mkdir()
        sentinel = outside / "control-token"
        sentinel.write_bytes(b"outside-sentinel")
        app = self.root / "app with spaces"
        with wf.directory(app, owned_root=app, create=True):
            pass
        intermediate = app / "config"
        self.junction(intermediate, outside)
        with self.assertRaisesRegex(PermissionError, "reparse point"):
            with wf.directory(intermediate / "auth", owned_root=app, create=True):
                pass
        self.assertFalse((outside / "auth").exists())
        intermediate.rmdir()
        leaf = self.path / "control-token"
        self.junction(leaf, outside)
        with wf.directory(self.path, owned_root=self.root) as pinned:
            with self.assertRaisesRegex(PermissionError, "reparse point"):
                pinned.read_private("control-token", limit=64)
            with self.assertRaisesRegex(PermissionError, "reparse point"):
                pinned.publish_private("control-token", b"bad", replace=True)
        self.assertEqual(sentinel.read_bytes(), b"outside-sentinel")
        leaf.rmdir()

    def test_profile_roundtrip_and_no_replace(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"first\n")
            with self.assertRaises(FileExistsError):
                pinned.publish_private("control-token", b"second\n")
            self.assertEqual(pinned.read_private("control-token", limit=64), b"first\n")
            with self.assertRaises(ValueError):
                pinned.read_private("control-token", limit=2)
            with self.assertRaises(FileNotFoundError):
                pinned.read_private("absent", limit=64)

    def test_private_acl_at_creation_before_write(self):
        api = wf._API()
        original = wf._API.write

        def checked_write(instance, handle, content):
            instance.validate_acl(handle, private=True)
            descriptor = wf.PVOID()
            control, revision = wf.WORD(), wf.DWORD()
            error = instance.GetSecurityInfo(handle, 1, 5, None, None, None, None, C.byref(descriptor))
            if error:
                raise C.WinError(error)
            try:
                instance.ok(instance.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
                self.assertTrue(control.value & 0x1000)  # protected BEFORE bytes
            finally:
                instance.LocalFree(descriptor)
            original(instance, handle, content)

        with patch.object(wf._API, "write", checked_write):
            with wf.directory(self.path, owned_root=self.root) as pinned:
                pinned.publish_private("session-secret", b"not-real-credentials")
        # New directories too, not merely a post-write chmod.
        for path in (self.root, self.root / "config", self.path):
            self.assertIn("D:P", self.security_text(path))
            handle = api.open(path, directory=True)
            try:
                api.validate_acl(handle, private=True)
            finally:
                api.close(handle)

    def test_case_alias_refused_without_modification(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"exact")
            with self.assertRaises((PermissionError, FileNotFoundError)):
                pinned.read_private("CONTROL-TOKEN", limit=64)
            self.assertEqual(pinned.read_private("control-token", limit=64), b"exact")

    def test_hardlink_rejected_without_modification(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"sentinel")
        alias = self.root / "alias"
        os.link(self.path / "control-token", alias)
        with wf.directory(self.path, owned_root=self.root) as pinned:
            with self.assertRaises(PermissionError):
                pinned.read_private("control-token", limit=64)
            with self.assertRaises(PermissionError):
                pinned.publish_private("control-token", b"changed", replace=True)
        self.assertEqual(alias.read_bytes(), b"sentinel")

    def test_pinned_reader_survives_replace_and_sharing_conflict_preserves_old(self):
        api = wf._API()
        target = self.path / "control-token"
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(target.name, b"old")
            handle = api.open(target)
            try:
                pinned.publish_private(target.name, b"new", replace=True)
                self.assertEqual(api.read(handle, limit=64), b"old")
            finally:
                api.close(handle)
            # Explicit non-delete-sharing handle should make replacement fail.
            blocking = api.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x200000, None)
            self.assertNotEqual(blocking, wf._INVALID)
            try:
                with self.assertRaises(OSError):
                    pinned.publish_private(target.name, b"bad", replace=True)
            finally:
                api.close(blocking)
            self.assertEqual(pinned.read_private(target.name, limit=64), b"new")

    def test_config_replacements_keep_each_open_private_reader_snapshot(self):
        api = wf._API()
        target = self.path / "server.yaml"
        snapshots = [b"port: 9001\n# old\n", b"port: 9002\n# middle\n", b"port: 9003\n# latest\n"]
        handles = []
        try:
            with wf.directory(self.path, owned_root=self.root) as pinned:
                pinned.publish_private(target.name, snapshots[0])
                handles.append(api.open(target))
                pinned.publish_private(target.name, snapshots[1], replace=True)
                handles.append(api.open(target))
                pinned.publish_private(target.name, snapshots[2], replace=True)
                self.assertEqual(pinned.read_private(target.name, limit=1024), snapshots[2])
                for handle, expected in zip(handles, snapshots):
                    api.validate_acl(handle, private=True)
                    self.assertEqual(api.read(handle, limit=1024), expected)
                self.assertEqual([path.name for path in self.path.iterdir()], ["server.yaml"])
        finally:
            for handle in handles:
                api.close(handle)

    def test_no_replace_kernel_collision_preserves_open_complete_credential_winner(self):
        # Force a winner between the missing-destination check and rename, not
        # merely the early FileExistsError fast path.
        original = wf._API.move
        target = self.path / "control-token"
        winner = b"complete-winner\n"
        handles = []

        def concurrent_winner(instance, handle, destination, *, replace):
            self.assertEqual(destination, target)
            self.assertFalse(replace)
            with wf.directory(self.path, owned_root=self.root) as other:
                # Avoid recursive injection for the winner's publication.
                with patch.object(wf._API, "move", original):
                    other.publish_private(target.name, winner)
            handles.append(instance.open(target))
            return original(instance, handle, destination, replace=replace)

        api = wf._API()
        try:
            with wf.directory(self.path, owned_root=self.root) as pinned:
                with patch.object(wf._API, "move", concurrent_winner):
                    with self.assertRaises(FileExistsError):
                        pinned.publish_private(target.name, b"loser\n")
                self.assertEqual(pinned.read_private(target.name, limit=1024), winner)
                self.assertEqual(api.read(handles[0], limit=1024), winner)
                self.assertEqual([path.name for path in self.path.iterdir()], ["control-token"])
        finally:
            for handle in handles:
                api.close(handle)

    def test_partial_temporary_write_failure_never_exposes_partial_destination(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"complete-old")
            original = wf._API.write

            def partial(instance, handle, content):
                original(instance, handle, content[:3])
                self.assertEqual(pinned.read_private("control-token", limit=64), b"complete-old")
                raise OSError("injected partial write failure")

            with patch.object(wf._API, "write", partial), self.assertRaises(OSError):
                pinned.publish_private("control-token", b"new-content", replace=True)
            self.assertEqual(pinned.read_private("control-token", limit=64), b"complete-old")
            self.assertEqual(sorted(path.name for path in self.path.iterdir()), ["control-token"])

    def test_subprocess_credential_race(self):
        script = """
import importlib.util, pathlib, sys, time
s = importlib.util.spec_from_file_location("wf", sys.argv[1])
w = importlib.util.module_from_spec(s); s.loader.exec_module(w)
root = pathlib.Path(sys.argv[2])
(root / ("ready-" + sys.argv[3])).write_text("ready")
deadline = time.monotonic() + 30
while not (root / "go").exists():
    if time.monotonic() > deadline: raise RuntimeError("race gate timed out")
    time.sleep(.01)
with w.directory(root / "config/auth", owned_root=root) as p:
    try: p.publish_private("control-token", sys.argv[3].encode())
    except FileExistsError: pass
    print(p.read_private("control-token", limit=100000).decode())
"""
        values = ["a" * 65537, "b" * 65537]
        # Do not put the large value on Windows' bounded command line.
        script = script.replace("sys.argv[3].encode()", "(sys.argv[3] * 65537).encode()")
        processes = [subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", script,
                                      str(SOURCE), str(self.root), value[0]],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for value in values]
        def settle():
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
        self.addCleanup(settle)
        import time
        deadline = time.monotonic() + 30
        while not all((self.root / ("ready-" + value[0])).exists() for value in values):
            if time.monotonic() > deadline or any(process.poll() is not None for process in processes):
                self.fail("credential contenders did not reach the race gate")
            time.sleep(.01)
        (self.root / "go").write_text("go")
        results = [process.communicate(timeout=30) for process in processes]
        for process, (_, error) in zip(processes, results):
            self.assertEqual(process.returncode, 0, error.decode())
        self.assertEqual(results[0][0], results[1][0])
        self.assertIn(results[0][0].decode().strip(), values)


if __name__ == "__main__":
    unittest.main()