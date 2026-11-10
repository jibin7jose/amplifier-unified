"""Portable boundary models and separately selected real Win64/NTFS controls."""
import asyncio
import ctypes as C
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from amplifier_web import outputs, resource_files as resources
from amplifier_web import windows_read_files as native


@pytest.mark.parametrize("value", [
    "../outside.txt", "./report.txt", "folder//report.txt", "folder/../report.txt",
    "C:report.txt", r"\report.txt", r"\\host\share\report.txt",
    r"\\?\C:\work\report.txt", "report.txt:stream", "NUL.txt", "report.txt.",
    "report.txt ", "folder~/report.txt", r"D:\work\report.txt",
])
def test_lexical_workspace_refusals_precede_native_io(monkeypatch, value):
    monkeypatch.setattr(native, "_ReadAPI", lambda: pytest.fail("native I/O"))
    with pytest.raises(ValueError):
        native.workspace_path(r"C:\work", value)


def test_unicode_spaces_and_exact_containment_are_lexical(monkeypatch):
    monkeypatch.setattr(native, "_ReadAPI", lambda: pytest.fail("native I/O"))
    assert native.workspace_path(r"c:\work space", "résumé/雪.txt") == PureWindowsPath(
        r"C:\work space\résumé\雪.txt")
    with pytest.raises(ValueError, match="inside"):
        native.workspace_path(r"C:\work", r"C:\work-other\report.txt")
    with pytest.raises(ValueError, match="Choose a file"):
        native.workspace_path(r"C:\work", r"C:\work")


@pytest.mark.parametrize("value", [
    r"C:\case-root\workspace\outside.txt",
    r"C:\Case-root\Workspace\outside.txt",
    r"c:\case-root\WORKSPACE\outside.txt",
])
def test_case_only_root_alias_refuses_before_native_io(monkeypatch, value):
    monkeypatch.setattr(native, "_ReadAPI", lambda: pytest.fail("native I/O"))
    monkeypatch.setattr(native, "open_file", lambda *args: pytest.fail("native file open"))
    monkeypatch.setattr(outputs, "os", SimpleNamespace(name="nt"))
    with pytest.raises(ValueError, match="inside"):
        outputs.file_bytes(r"C:\case-root\Workspace", value)
    # Normalize drive spelling only, not any configured-root component.
    assert str(native.workspace_path(r"C:\case-root\Workspace",
                                      r"c:\case-root\Workspace\inside.txt")) == (
        r"C:\case-root\Workspace\inside.txt")


def test_win64_native_structure_layouts():
    assert C.sizeof(native.HANDLE) == 8
    assert C.sizeof(native.DWORD) == 4
    assert C.sizeof(native.WORD) == 2
    assert C.sizeof(native._Unicode) == 16
    assert [getattr(native._Unicode, field).offset for field in
            ("length", "maximum", "buffer")] == [0, 2, 8]
    assert C.sizeof(native._Attributes) == 48
    assert [getattr(native._Attributes, field).offset for field in
            ("length", "root", "name", "flags", "security", "quality")] == [0, 8, 16, 24, 32, 40]
    assert C.sizeof(native._Status) == 16
    assert native._Status.information.offset == 8
    assert C.sizeof(native._Basic) == 40
    assert [getattr(native._Basic, field).offset for field in
            ("created", "accessed", "written", "changed", "attributes")] == [0, 8, 16, 24, 32]


def test_native_read_bindings_have_explicit_fixed_width_signatures(monkeypatch):
    class Function:
        def __call__(self, *args):
            pytest.fail("Binding must not execute a file operation")
    kernel = SimpleNamespace(GetFileInformationByHandleEx=Function())
    library = SimpleNamespace(NtCreateFile=Function(), RtlNtStatusToDosError=Function())
    loaded = []
    def load(name, *, use_last_error):
        assert use_last_error is True
        loaded.append(name)
        return {"kernel32": kernel, "ntdll": library}[name]
    monkeypatch.setattr(native._API, "__init__", lambda self: None)
    monkeypatch.setattr(C, "WinDLL", load, raising=False)
    api = native._ReadAPI()
    assert loaded == ["kernel32", "ntdll"]
    assert api.basic.restype is native.BOOL
    assert api.basic.argtypes == [native.HANDLE, C.c_int32, native.PVOID, native.DWORD]
    assert api.create.restype is C.c_int32
    assert api.create.argtypes == [
        C.POINTER(native.HANDLE), native.DWORD, C.POINTER(native._Attributes),
        C.POINTER(native._Status), native.PVOID, native.DWORD, native.DWORD,
        native.DWORD, native.DWORD, native.PVOID, native.DWORD,
    ]
    assert api.dos_error.restype is native.DWORD
    assert api.dos_error.argtypes == [C.c_int32]


@pytest.mark.parametrize("directory", [False, True])
def test_relative_native_open_uses_parent_no_reparse_and_read_only_flags(directory):
    api = object.__new__(native._ReadAPI)
    calls, closed = [], []
    api.info = lambda *args, **kwargs: None
    api.close = closed.append

    def create(handle, access, attributes, status, allocation, attributes_mask,
               sharing, disposition, options, ea, ea_size):
        attrs = C.cast(attributes, C.POINTER(native._Attributes)).contents
        name = attrs.name.contents
        calls.append((access, attrs.root, attrs.flags, name.buffer, name.length, name.maximum,
                      attrs.length, attrs.security, attrs.quality,
                      attributes_mask, sharing, disposition, options, allocation, ea, ea_size))
        C.cast(handle, C.POINTER(native.HANDLE))[0] = 702
        return 0

    api.create = create
    path = PureWindowsPath(r"C:\work\résumé.txt")
    assert api.open_read(path, 701, directory=directory) == 702
    assert calls == [(0x100081 if directory else 0x80100000, 701, 0x1000,
                      path.name, len(path.name.encode("utf-16-le")), len(path.name.encode("utf-16-le")) + 2,
                      48, None, None,
                      0, 1, 1, 0x200020 | (1 if directory else 0x40), None, None, 0)]
    assert not closed
    api.info = lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("reparse"))
    with pytest.raises(PermissionError, match="reparse"):
        api.open_read(path, 701, directory=directory)
    assert closed == [702]


def test_synchronous_leaf_open_supplies_explicit_synchronize_before_nt_validation(monkeypatch):
    """Model raw synchronous-access validation, not generic-right expansion.

    Native CI reached this leaf call after successful directory traversal and
    returned ERROR_INVALID_PARAMETER. A portable model must reject the old
    raw GENERIC_READ-only mask rather than silently accepting every NtCreateFile.
    The unchanged native positive read remains the actual Windows oracle.
    """
    api = object.__new__(native._ReadAPI)
    invalid_parameter = -1073741811  # STATUS_INVALID_PARAMETER (0xC000000D)
    accesses, validated, closed = [], [], []
    def create(handle, access, attributes, status, allocation, attributes_mask,
               sharing, disposition, options, ea, ea_size):
        attrs = C.cast(attributes, C.POINTER(native._Attributes)).contents
        assert attrs.root == 701 and attrs.flags == 0x1000
        assert sharing == 1 and disposition == 1 and options == 0x200060
        accesses.append(access)
        if options & 0x30 and not access & 0x100000:
            return invalid_parameter
        C.cast(handle, C.POINTER(native.HANDLE))[0] = 702
        return 0
    def dos_error(status):
        assert status == invalid_parameter
        return 87
    def win_error(code):
        error = OSError(22, "native synchronous-open access contract")
        error.winerror = code
        return error
    api.create, api.dos_error = create, dos_error
    api.info = lambda handle, path, **kwargs: validated.append(handle)
    api.close = closed.append
    monkeypatch.setattr(C, "WinError", win_error, raising=False)
    assert api.open_read(PureWindowsPath(r"C:\work\report.txt"), 701, directory=False) == 702
    assert len(accesses) == 1
    assert accesses[0] & 0x80000000  # GENERIC_READ remains present
    assert accesses[0] & 0x100000  # SYNCHRONIZE is present before the native call
    assert validated == [702] and not closed


def test_failed_nt_open_is_translated_without_using_an_unowned_handle(monkeypatch):
    api = object.__new__(native._ReadAPI)
    api.info = lambda *args, **kwargs: pytest.fail("failed open must not validate a handle")
    api.close = lambda *args: pytest.fail("failed open has no owned handle")
    status, converted = -1073741772, []
    api.create = lambda *args: status  # STATUS_OBJECT_NAME_NOT_FOUND
    def dos_error(value):
        assert value == status
        return 2
    def win_error(value):
        converted.append(value)
        return FileNotFoundError("native file unavailable")
    api.dos_error = dos_error
    monkeypatch.setattr(C, "WinError", win_error, raising=False)
    with pytest.raises(FileNotFoundError, match="native file unavailable"):
        api.open_read(PureWindowsPath(r"C:\work\missing.txt"), 701, directory=False)
    assert converted == [2]


def test_change_time_not_creation_time_is_part_of_signature():
    api = object.__new__(native._ReadAPI)
    api.info = lambda *args, **kwargs: SimpleNamespace(
        volume=4, index_high=1, index_low=2, size_high=0, size_low=3)
    changed = [9]

    def basic(handle, kind, pointer, size):
        assert kind == 0 and size == C.sizeof(native._Basic)
        value = C.cast(pointer, C.POINTER(native._Basic)).contents
        value.created, value.accessed, value.written, value.changed = 5, 6, 7, changed[0]
        return True

    api.basic = basic
    assert api.signature(1, PureWindowsPath(r"C:\work\a")) == (4, (1 << 32) | 2, 3, 7, 9)
    changed[0] = 10
    assert api.signature(1, PureWindowsPath(r"C:\work\a"))[-1] == 10


@pytest.mark.parametrize("attributes,links", [(0, 1), (0x400, 1), (0x10, 1), (0, 2)])
def test_snapshot_signature_uses_retained_handle_and_refuses_unsafe_object(attributes, links):
    api = object.__new__(native._ReadAPI)
    api.info = lambda *args, **kwargs: pytest.fail("snapshot must not resolve a pathname")
    queries = []
    def info(handle, pointer):
        queries.append(handle)
        value = C.cast(pointer, C.POINTER(native.FILE_INFO)).contents
        value.volume, value.index_low, value.size_low = 7, 11, 15
        value.attributes, value.links = attributes, links
        return True
    def basic(handle, kind, pointer, size):
        value = C.cast(pointer, C.POINTER(native._Basic)).contents
        value.written, value.changed = 21, 22
        return True
    api.GetFileInformationByHandle, api.basic = info, basic
    if attributes or links != 1:
        with pytest.raises(PermissionError, match="reparse point, hardlink, or wrong type"):
            api.signature(701)
    else:
        assert api.signature(701) == (7, 11, 15, 21, 22)
    assert queries == [701]


@pytest.fixture
def native_model(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX descriptor model; real native controls are below")
    instances = []

    class API:
        def __init__(self):
            self.opened, self.closed = [], []
            instances.append(self)

        def local(self, path):
            return tmp_path.joinpath(*path.parts[1:])

        def GetDriveTypeW(self, root):
            return 3

        def GetVolumeInformationByHandleW(self, handle, *args):
            args[-2].value = "NTFS"
            return True

        def ok(self, result):
            assert result

        def open_read(self, path, parent=None, *, directory):
            flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK)
            fd = (os.open(self.local(path), flags) if parent is None else
                  os.open(path.name, flags, dir_fd=parent))
            self.opened.append((path, fd, parent, directory))
            try:
                self.info(fd, path, directory=directory)
            except BaseException:
                self.close(fd)
                raise
            return fd

        def info(self, handle, path, *, directory):
            import stat
            info = os.fstat(handle)
            if (bool(stat.S_ISDIR(info.st_mode)) != directory or
                    not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
                    or self.local(path).is_symlink()):
                raise PermissionError("reparse, hardlink or wrong type")
            return SimpleNamespace(volume=info.st_dev, index_high=0, index_low=info.st_ino)

        def signature(self, handle, path=None):
            if path is not None:
                self.info(handle, path, directory=False)
            info = os.fstat(handle)
            import stat
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("unsafe snapshot type or hardlink")
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

        def close(self, handle):
            self.closed.append(handle)
            os.close(handle)

    monkeypatch.setattr(native, "_ReadAPI", API)
    monkeypatch.setattr(os, "O_BINARY", 0, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=lambda handle, flags: handle))
    # Do not change os.name globally (that corrupts pathlib on a POSIX host).
    monkeypatch.setattr(outputs, "os", SimpleNamespace(name="nt", fstat=os.fstat))
    monkeypatch.setattr(resources, "os", SimpleNamespace(name="nt", fstat=os.fstat))
    (tmp_path / "work space").mkdir()
    return tmp_path, instances


def test_model_reads_the_opened_handle_and_closes_pins(native_model):
    root, instances = native_model
    path = root / "work space" / "雪 résumé.txt"
    path.write_bytes("Hello 雪".encode())
    assert outputs.file_bytes(r"C:\work space", path.name) == (
        "Hello 雪".encode(), r"C:\work space\雪 résumé.txt", path.name)
    for api in instances:
        assert len(api.opened) == 4  # drive, parent, payload and pre-yield identity open
        assert len(api.closed) == 3  # stream owns and closes the payload's descriptor
        for _, fd, _, _ in api.opened:
            with pytest.raises(OSError):
                os.fstat(fd)


def test_model_case_distinct_sibling_is_outside_configured_root(native_model):
    root, instances = native_model
    configured = root / "case-root" / "Workspace"
    sibling = root / "case-root" / "workspace"
    configured.mkdir(parents=True)
    sibling.mkdir()
    inside = configured / "inside.txt"
    outside = sibling / "outside.txt"
    inside.write_bytes(b"inside configured workspace")
    outside.write_bytes(b"outside sibling bytes")
    assert not os.path.samefile(configured, sibling)
    with pytest.raises(ValueError, match="inside"):
        outputs.file_bytes(r"C:\case-root\Workspace", r"C:\case-root\workspace\outside.txt")
    assert not instances  # refusal precedes any native path/handle access
    expected = (inside.read_bytes(), r"C:\case-root\Workspace\inside.txt", "inside.txt")
    assert outputs.file_bytes(r"C:\case-root\Workspace", "inside.txt") == expected
    assert outputs.file_bytes(r"C:\case-root\Workspace",
                              r"c:\case-root\Workspace\inside.txt") == expected
    assert inside.read_bytes() == b"inside configured workspace"
    assert outside.read_bytes() == b"outside sibling bytes"


@pytest.mark.parametrize("failure", ["conversion", "fdopen", "signature", "caller", "post-check", "pin-close"])
def test_model_handle_ownership_closes_once_on_each_failure(native_model, monkeypatch, failure):
    root, instances = native_model
    (root / "work space" / "report.txt").write_bytes(b"complete")
    real_close, closed_fds = os.close, []
    real_fdopen = os.fdopen

    def close(fd):
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "close", close)
    if failure == "conversion":
        def conversion(*args):
            raise OSError("conversion")
        monkeypatch.setattr(sys.modules["msvcrt"], "open_osfhandle", conversion)
    elif failure == "fdopen":
        def fdopen(*args):
            raise OSError("fdopen")
        monkeypatch.setattr(os, "fdopen", fdopen)
    else:
        # Track the native handle's transfer to the actual file object. Its
        # close() closes the fd internally, not through os.close().
        def fdopen(fd, *args):
            stream = real_fdopen(fd, *args)
            native_close = stream.close
            def stream_close():
                closed_fds.append(fd)
                native_close()
            stream.close = stream_close
            return stream
        monkeypatch.setattr(os, "fdopen", fdopen)
    api_type = native._ReadAPI
    if failure == "pin-close":
        real_api_close = api_type.close
        failed = []
        def api_close(self, fd):
            real_api_close(self, fd)
            if not failed and any(handle == fd and directory
                                  for _, handle, _, directory in self.opened):
                failed.append(fd)
                raise OSError("pin-close")
        monkeypatch.setattr(api_type, "close", api_close)
    if failure in {"signature", "post-check"}:
        real_signature = api_type.signature
        calls = []
        def signature(self, *args):
            calls.append(args)
            if len(calls) == (1 if failure == "signature" else 4):
                raise OSError(failure)
            return real_signature(self, *args)
        monkeypatch.setattr(api_type, "signature", signature)
    with pytest.raises(OSError, match=failure):
        with native.open_file(r"C:\work space\report.txt") as stream:
            assert stream.read(100) == b"complete"
            if failure == "caller":
                raise OSError("caller")
    assert len(instances) == 1
    api = instances[0]
    assert len(api.opened) == (3 if failure in {"conversion", "fdopen", "signature"} else 4)
    assert sorted(closed_fds) == sorted(fd for _, fd, _, _ in api.opened)
    assert len(set(closed_fds)) == len(api.opened)
    for fd in closed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("change", ["data", "hardlink", "leaf-rebind"])
def test_model_refuses_changes_before_result_escapes(native_model, change):
    root, _ = native_model
    path = root / "work space" / "report.txt"
    path.write_bytes(b"original")
    with pytest.raises((OSError, ValueError)):
        with native.open_file(r"C:\work space\report.txt") as stream:
            assert stream.read(8) == b"original"
            if change == "data":
                path.write_bytes(b"modified!")
            elif change == "hardlink":
                os.link(path, root / "alias.txt")
            else:
                path.rename(root / "old.txt")
                path.write_bytes(b"original")


@pytest.mark.parametrize("change", ["directory-reparse", "leaf-rebind", "leaf-volume"])
def test_model_pre_yield_recheck_refuses_path_or_identity_change(native_model, monkeypatch, change):
    root, instances = native_model
    path = root / "work space" / "report.txt"
    path.write_bytes(b"original")
    api_type = native._ReadAPI
    real_signature = api_type.signature
    def signature(self, handle, selected=None):
        value = real_signature(self, handle, selected)
        if selected is not None and len(self.opened) == 3:
            if change == "directory-reparse":
                path.parent.rename(root / "old")
                path.parent.symlink_to(root / "old", target_is_directory=True)
            elif change == "leaf-rebind":
                path.rename(root / "old.txt")
                path.write_bytes(b"outside replacement")
            else:
                return (value[0] + 1, *value[1:])
        return value
    monkeypatch.setattr(api_type, "signature", signature)
    with pytest.raises((OSError, ValueError)):
        with native.open_file(r"C:\work space\report.txt"):
            pytest.fail("unsafe path escaped pre-yield validation")
    for api in instances:
        for _, fd, _, _ in api.opened:
            with pytest.raises(OSError):
                os.fstat(fd)


def test_model_snapshot_survives_parent_rebind_but_future_read_refuses(native_model):
    root, instances = native_model
    parent = root / "work space"
    path = parent / "report.txt"
    path.write_bytes(b"original snapshot")
    outside = root / "outside"
    outside.mkdir()
    (outside / path.name).write_bytes(b"outside replacement")
    with native.open_file(r"C:\work space\report.txt") as stream:
        # No traversal handle survives into caller code, and no pathname open
        # is needed for snapshot reads/signatures after a directory move.
        for _, fd, _, directory in instances[0].opened:
            if directory:
                with pytest.raises(OSError):
                    os.fstat(fd)
        parent.rename(root / "moved")
        parent.symlink_to(outside, target_is_directory=True)
        assert stream.read(100) == b"original snapshot"
        stream.seek(0)
        assert stream.read(100) == b"original snapshot"
        assert native.stream_signature(stream)[2] == len(b"original snapshot")
        with pytest.raises((OSError, ValueError)):
            outputs.file_bytes(r"C:\work space", "report.txt")
    assert (outside / path.name).read_bytes() == b"outside replacement"
    assert (root / "moved" / path.name).read_bytes() == b"original snapshot"


def test_model_actual_resource_writer_runs_after_traversal_pins_close(native_model, monkeypatch):
    from amplifier_web.host.storage import SessionStore
    root, instances = native_model
    directory = root / "work space"
    first = resources.prepare({"text": "held A 雪"})
    second = resources.prepare({"text": "independent B résumé"})
    resources.write_prepared(directory, first)
    source = resources.Detached(PureWindowsPath(r"C:\work space"), first.identity,
                                json.dumps({"$blob": first.identity}))
    real_replace = os.replace
    publications = []
    def replace(*args, **kwargs):
        # Model the measured Windows conflict at the actual producer's replace,
        # not a successful writer double. Lifetime matters, not a relaxed mask.
        live_directories = [(path, fd) for api in instances
                            for path, fd, _, directory in api.opened
                            if directory and fd not in api.closed]
        if live_directories:
            raise PermissionError("atomic publication conflicts with retained traversal pin")
        publications.append(args)
        return real_replace(*args, **kwargs)
    monkeypatch.setattr(SessionStore._atomic.__globals__["os"], "replace", replace)
    def project(stream):
        assert stream.read(first.size + 1) == first.text.encode()
        resources.write_prepared(directory, second)
        stream.seek(0)
        return stream.read(first.size + 1)
    value, signature = resources.project_detached(source, project)
    assert value == first.text.encode() and signature[2] == first.size
    assert len(publications) == 1
    assert (directory / (second.identity + ".json")).read_bytes() == second.text.encode()
    assert resources.read_detached(source) == json.loads(first.text)


def test_model_resource_projection_still_refuses_changed_final_path(native_model):
    root, _ = native_model
    directory = root / "work space"
    prepared = resources.prepare({"text": "original A"})
    (directory / (prepared.identity + ".json")).write_bytes(prepared.text.encode())
    source = resources.Detached(PureWindowsPath(r"C:\work space"), prepared.identity,
                                json.dumps({"$blob": prepared.identity}))
    replacement = root / "outside"
    replacement.mkdir()
    (replacement / (prepared.identity + ".json")).write_bytes(b"outside payload")
    def project(stream):
        directory.rename(root / "moved")
        directory.symlink_to(replacement, target_is_directory=True)
        assert stream.read(prepared.size + 1) == prepared.text.encode()
        return "must not escape changed resource path"
    with pytest.raises((OSError, ValueError)):
        resources.project_detached(source, project)
    assert (replacement / (prepared.identity + ".json")).read_bytes() == b"outside payload"


def test_model_missing_oversize_and_bad_resource_preserve_files(native_model, monkeypatch):
    root, instances = native_model
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    with pytest.raises(FileNotFoundError):
        outputs.file_bytes(r"C:\work space", "missing.txt")
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == before
    path = root / "work space" / "report.txt"
    path.write_bytes(b"12345")
    monkeypatch.setattr(outputs, "MAX_FILE", 4)
    with pytest.raises(ValueError, match="up to8MB"):
        outputs.file_bytes(r"C:\work space", path.name)
    assert path.read_bytes() == b"12345"
    payload = {"text": "résumé" * 64}
    prepared = resources.prepare(payload)
    blob = root / "work space" / (prepared.identity + ".json")
    blob.write_bytes(prepared.text.encode())
    source = resources.Detached(PureWindowsPath(r"C:\work space"), prepared.identity,
                                json.dumps({"$blob": prepared.identity}))
    assert resources.read_detached(source, max_bytes=prepared.size) == payload
    projection, signature = resources.project_detached(source, lambda stream: stream.read(3))
    assert projection == prepared.text.encode()[:3] and signature[2] == prepared.size
    limit = len(source.indexed.encode()) + 16
    assert len(source.indexed.encode()) < limit < prepared.size
    opens_before_limit = len(instances)
    with pytest.raises(ValueError, match="Invalid or oversized artifact file"):
        resources.read_detached(source, max_bytes=limit)
    assert len(instances) == opens_before_limit + 1  # native payload open, not index preflight
    assert blob.read_bytes() == prepared.text.encode()
    blob.write_bytes(prepared.text.encode().replace(b"text", b"fail"))
    for read in [resources.read_detached, lambda source: resources.project_detached(source, lambda s: s.read(1))]:
        with pytest.raises(ValueError, match="identity"):
            read(source)
    assert b"fail" in blob.read_bytes()


def test_model_reads_are_bounded_and_hash_covers_bytes_skipped_by_projection(native_model, monkeypatch):
    root, _ = native_model
    prepared = resources.prepare({"text": "résumé" * 20_000})
    blob = root / "work space" / (prepared.identity + ".json")
    blob.write_bytes(prepared.text.encode())
    source = resources.Detached(PureWindowsPath(r"C:\work space"), prepared.identity,
                                json.dumps({"$blob": prepared.identity}))
    real_fdopen, requests = os.fdopen, []
    def fdopen(fd, *args):
        stream = real_fdopen(fd, *args)
        real_read = stream.read
        def read(size=-1):
            assert type(size) is int and 0 <= size <= prepared.size + 1
            requests.append(size)
            return real_read(size)
        stream.read = read
        return stream
    monkeypatch.setattr(os, "fdopen", fdopen)
    assert resources.read_detached(source, max_bytes=prepared.size) == json.loads(prepared.text)
    assert requests == [prepared.size + 1]
    requests.clear()
    def tail(stream):
        stream.seek(prepared.size - 5)
        return stream.read(5)
    assert resources.project_detached(source, tail)[0] == prepared.text.encode()[-5:]
    assert requests[0] == 5 and set(requests[1:]) == {64 * 1024}
    # The projected tail is identical, but corruption in its skipped prefix
    # must still prevent the projection from escaping.
    blob.write_bytes(prepared.text.encode().replace(b"text", b"fail"))
    with pytest.raises(ValueError, match="identity"):
        resources.project_detached(source, tail)


def test_inline_and_invalid_indexes_do_not_open_paths(monkeypatch):
    monkeypatch.setattr(resources, "_open_blob", lambda *args: pytest.fail("file I/O"))
    prepared = resources.prepare({"inline": "雪"})
    source = resources.Detached(None, prepared.identity, prepared.text)
    assert resources.read_detached(source) == {"inline": "雪"}
    assert resources.project_detached(source, lambda s: s.read(2))[0] == b'{"'
    bad = resources.Detached(Path("unused"), prepared.identity, '{"$blob":"' + "f" * 64 + '"}')
    with pytest.raises(ValueError, match="index"):
        resources.read_detached(bad)
    with pytest.raises(ValueError, match="index"):
        resources.project_detached(bad, lambda stream: pytest.fail("projector"))


native_only = pytest.mark.skipif(os.name != "nt", reason="native Win64 local NTFS control")


@native_only
def test_native_inherited_workspace_utf8_spaces_and_no_private_acl_requirement(tmp_path):
    root = tmp_path.resolve() / "ordinary workspace"
    root.mkdir()
    path = root / "résumé 雪 𐍈.txt"
    path.write_bytes("Saved UTF-8 雪".encode())
    # Ordinary mkdir/write_bytes inherit the user's workspace ACLs. There is no
    # private credential helper, creation override or ACL repair in this test.
    assert outputs.file_bytes(root, path.name) == ("Saved UTF-8 雪".encode(), str(path), path.name)
    with native.open_file(path) as stream:
        assert stream.read(3) == b"Sav"
        stream.seek(0)
        assert stream.read(100) == path.read_bytes()
        assert native.stream_signature(stream)[2] == path.stat().st_size
        assert not os.get_inheritable(stream.fileno())
    with pytest.raises(ValueError, match="closed"):
        native.stream_signature(stream)


@native_only
def test_native_held_handles_block_data_write_leaf_and_parent_rename_then_release(tmp_path):
    """Leaf sharing stays strict; traversal lifetime is now snapshot-only.

    Traversal pins no longer promise parent ownership. The unchanged leaf
    sharing may still block its parent's move. If Windows permits that move,
    it cannot redirect this stream and a later unsafe open must still refuse.
    Independent B publication is unconditionally required by the overlap test.
    """
    root = tmp_path.resolve() / "work"
    root.mkdir()
    path = root / "report.txt"
    path.write_bytes(b"complete")
    moved, outside = root.with_name("moved"), root.with_name("outside")
    outside.mkdir()
    (outside / path.name).write_bytes(b"outside replacement")
    junction_created = False
    try:
        with native.open_file(path) as stream:
            for mutate in [lambda: path.write_bytes(b"partial"),
                           lambda: path.rename(root / "moved.txt")]:
                with pytest.raises(OSError):
                    mutate()
            try:
                root.rename(moved)
            except PermissionError as exc:
                assert exc.errno == 13 and exc.winerror in {5, 32}
                assert root.is_dir() and not moved.exists()
            else:
                result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(root), str(outside)],
                                        capture_output=True, timeout=10)
                assert result.returncode == 0, result.stderr.decode(errors="replace")
                junction_created = True
            assert stream.read(100) == b"complete"
            stream.seek(0)
            assert stream.read(100) == b"complete"
            assert native.stream_signature(stream)[2] == len(b"complete")
            if junction_created:
                with pytest.raises((OSError, ValueError)):
                    outputs.file_bytes(root, path.name)
    finally:
        if junction_created:
            root.rmdir()  # test-owned junction only, not its target
    assert (outside / path.name).read_bytes() == b"outside replacement"
    if not moved.exists():
        root.rename(moved)  # the same move must succeed after the leaf closes
    (moved / path.name).write_bytes(b"released")
    (moved / path.name).rename(moved / "released.txt")
    moved.rename(root)
    assert (root / "released.txt").read_bytes() == b"released"


@native_only
def test_native_missing_directory_hardlink_and_limit_refuse_without_mutation(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    path = root / "report.txt"
    path.write_bytes(b"complete")
    with pytest.raises(FileNotFoundError):
        outputs.file_bytes(root, "missing.txt")
    assert not (root / "missing.txt").exists()
    with pytest.raises((OSError, ValueError)):
        outputs.file_bytes(root, str(root))
    alias = root / "alias.txt"
    os.link(path, alias)
    with pytest.raises((OSError, ValueError)):
        outputs.file_bytes(root, path.name)
    assert alias.read_bytes() == path.read_bytes() == b"complete"
    alias.unlink()
    monkeypatch.setattr(outputs, "MAX_FILE", 3)
    with pytest.raises(ValueError, match="up to8MB"):
        outputs.file_bytes(root, path.name)
    assert path.read_bytes() == b"complete"


@native_only
def test_native_junctions_refuse_before_resolve_even_when_target_is_inside(tmp_path):
    root = tmp_path.resolve() / "workspace"
    root.mkdir()
    target = root / "real"
    target.mkdir()
    (target / "report.txt").write_bytes(b"retained")
    link = root / "junction"
    result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    try:
        for selected_root, value in [(root, r"junction\report.txt"), (link, "report.txt")]:
            with pytest.raises((OSError, ValueError)):
                outputs.file_bytes(selected_root, value)
        assert (target / "report.txt").read_bytes() == b"retained"
    finally:
        link.rmdir()  # only this test's junction, never recurse into its target


@native_only
def test_native_resource_hash_projection_sync_resolution_and_corruption(tmp_path):
    root = tmp_path.resolve()
    directory = root / "artifacts"
    directory.mkdir()
    payload = {"text": "read-only 雪" * 64}
    prepared = resources.prepare(payload)
    path = directory / (prepared.identity + ".json")
    path.write_bytes(prepared.text.encode())
    source = resources.Detached(directory, prepared.identity, json.dumps({"$blob": prepared.identity}))
    db = sqlite3.connect(root / "app.sqlite3")
    try:
        assert resources.read_detached(source, max_bytes=prepared.size) == payload
        assert resources.resolve(db, prepared.identity, {"$blob": prepared.identity}) == payload
        limit = len(source.indexed.encode()) + 16
        assert len(source.indexed.encode()) < limit < prepared.size
        with pytest.raises(ValueError, match="Invalid or oversized artifact file"):
            resources.read_detached(source, max_bytes=limit)
        value, signature = resources.project_detached(source, lambda stream: stream.read(2))
        assert value == b'{"' and signature == resources.detached_signature(source)
        path.write_bytes(prepared.text.encode().replace(b"text", b"fail"))
        for read in [resources.read_detached,
                     lambda s: resources.project_detached(s, lambda stream: stream.read(1)),
                     lambda s: resources.resolve(db, s.identity, {"$blob": s.identity})]:
            with pytest.raises(ValueError, match="identity"):
                read(source)
        assert b"fail" in path.read_bytes()
    finally:
        db.close()


def test_resource_publication_of_distinct_blob_during_held_projection(tmp_path):
    """Actual producer/read overlap on POSIX and native Windows, not a double.

    The projector retains A's open read context, including native ancestor pins.
    B must publish through write_prepared -> SessionStore._atomic -> os.replace
    during that lifetime. Refusal is a compatibility failure, not expected output.
    """
    directory = tmp_path.resolve() / "artifacts"
    first = resources.prepare({"text": "original A 雪"})
    second = resources.prepare({"text": "independent B résumé"})
    assert first.identity != second.identity
    resources.write_prepared(directory, first)
    source = resources.Detached(directory, first.identity, json.dumps({"$blob": first.identity}))
    second_source = resources.Detached(directory, second.identity, json.dumps({"$blob": second.identity}))
    published = []
    def project(stream):
        original = stream.read(first.size + 1)
        assert original == first.text.encode()
        resources.write_prepared(directory, second)
        published.append(resources.read_detached(second_source))
        stream.seek(0)
        assert stream.read(first.size + 1) == original
        return original
    projection, signature = resources.project_detached(source, project)
    assert projection == first.text.encode()
    assert signature == resources.detached_signature(source)
    assert published == [json.loads(second.text)]
    assert resources.read_detached(second_source) == json.loads(second.text)
    assert resources.read_detached(source) == json.loads(first.text)
    assert (directory / (first.identity + ".json")).read_bytes() == first.text.encode()
    assert (directory / (second.identity + ".json")).read_bytes() == second.text.encode()


async def _output_roundtrip(root, workspace):
    """Real output/resource persistence; only unrelated host services omitted."""
    root.mkdir()
    path = workspace / "résumé 雪.txt"
    original = "Immutable saved output 雪".encode()
    path.write_bytes(original)
    session = {"id": "windows-output-source", "workspace": str(workspace)}

    def open_app():
        from amplifier_web.state_storage import resource
        db = sqlite3.connect(root / "app.sqlite3")
        db.execute("CREATE TABLE IF NOT EXISTS state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        app = SimpleNamespace(db=db, lock=asyncio.Lock(), state={},
                              _session=lambda *args, **kwargs: session, _save_changes=db.commit,
                              state_resource=lambda identity: resource(db, identity))
        return app, outputs.Outputs(app)

    app, store = open_app()
    try:
        receipt = await store.dispatch("outputs.attach", {
            "sessionId": session["id"], "kind": "file", "title": "UTF-8 output",
            "path": path.name, "expectedSha256": hashlib.sha256(original).hexdigest(),
        }, "agent", "windows-read-output")
        saved = receipt["result"]
        assert saved["sha256"] == hashlib.sha256(original).hexdigest()
    finally:
        app.db.close()
    path.write_bytes(b"source changed after snapshot")
    app, store = open_app()
    try:
        restored = await store.read({"sessionId": session["id"], "id": saved["id"]})
        assert restored["text"] == original.decode()
        assert await store.content_async(saved) == store.content(saved) == original
        assert path.read_bytes() == b"source changed after snapshot"
    finally:
        app.db.close()


@pytest.mark.asyncio
async def test_output_attach_and_reopen_real_resource_boundary(tmp_path):
    # Runs on both platforms, including the actual resource producer. CPython
    # 3.13 supports fchmod on Windows; its presence is not private-DACL proof.
    # Never stub resource writes to hide a native persistence failure.
    await _output_roundtrip(tmp_path.resolve() / "app", tmp_path.resolve())


class _PublicationRuntime:
    """Forbid execution; the service, reducers, SQLite and file writers are real."""
    def __init__(self):
        self.attempts = []
        self.workers = {}

    async def start(self, *args, **kwargs):
        self.attempts.append("start")
        raise AssertionError("Publication discriminator must not start a runtime")

    async def send(self, *args, **kwargs):
        self.attempts.append("send")
        raise AssertionError("Publication discriminator must not send input")

    async def control(self, *args, **kwargs):
        self.attempts.append("control")
        raise AssertionError("Publication discriminator must not execute controls")

    async def close(self):
        pass


@pytest.fixture
async def publication_app(tmp_path):
    from amplifier_web.service import AppService

    workspace = tmp_path.resolve() / "publication-workspace"
    workspace.mkdir()
    runtime = _PublicationRuntime()
    app = AppService(tmp_path.resolve() / "publication-app", runtime, workspace=workspace)
    try:
        await app.dispatch("session.create", {})
        # Settle startup's existing maintenance, not a second writer racing the
        # discriminator. No server, history polling or provider is started here.
        maintenance = getattr(app, "_storage_maintenance_task", None)
        if maintenance is not None:
            await asyncio.wait_for(asyncio.shield(maintenance), 10)
        assert app.history.task is None and not app.queues
        yield app
    finally:
        # Evidence is taken before shutdown, which may flush retained dirty
        # state. Shutdown is NOT the independently staged released-handle control.
        await app.close()
        assert not runtime.attempts


class _PublicationRead:
    """One owned reader; barriers extend a genuine open, never manufacture IO."""
    def __init__(self):
        self.opened = threading.Event()
        self.release = threading.Event()
        self.thread = None
        self.stream = None
        self.retained = None
        self.result = None
        self.failed = False
        self.opens = 0

    def hold(self, stream):
        self.opens += 1
        assert self.opens == 1
        self.stream = stream
        assert not stream.closed
        self.opened.set()
        assert self.release.wait(30), "Publication reader release timed out"
        # Read from the SAME descriptor after the competing publication. These
        # fixture bodies are small; do not turn the barrier into an unbounded read.
        self.retained = stream.read(64 * 1024)
        assert len(self.retained) < 64 * 1024
        stream.seek(0)
        return self.retained

    def start(self, read):
        def run():
            try:
                self.result = read()
            except BaseException:
                self.failed = True
            finally:
                self.opened.set()  # Fail promptly if the real reader never opened.
        self.thread = threading.Thread(target=run, name="publication-reader", daemon=True)
        self.thread.start()

    async def wait_open(self):
        assert await asyncio.to_thread(self.opened.wait, 10), "Publication reader did not open"
        assert not self.failed and self.opens == 1
        self.assert_held()

    def assert_held(self):
        assert self.thread.is_alive() and not self.release.is_set()
        assert self.stream is not None and not self.stream.closed

    async def finish(self):
        self.release.set()
        await asyncio.to_thread(self.thread.join, 10)
        assert not self.thread.is_alive(), "Publication reader did not join"
        assert not self.failed, "Production reader failed"
        assert self.stream is not None and self.stream.closed


def _publication_snapshot(app):
    """A separate connection observes committed authority, not pending live state."""
    from amplifier_web.state_records import load

    db = sqlite3.connect(app.data_dir / "app.sqlite3")
    try:
        return load(db), db.execute("SELECT id,value FROM state_resources ORDER BY id").fetchall()
    finally:
        db.close()


async def _observed_publication(app, event, reader, scope, record_property):
    from amplifier_web.worker_diagnostics import command_failure_facts, validate_command_failure_facts

    facts = {"errors": [], "truncated": False}
    try:
        await app.on_runtime_event(*event)
    except Exception as error:
        facts = command_failure_facts(error)
    assert validate_command_failure_facts(facts) is not None
    # Only call-site labels and the existing closed error schema enter CI
    # properties. No paths, session IDs, exception prose or arbitrary payloads.
    record_property("publication_sharing", json.dumps({
        "schema": 1, "reader": reader, "scope": scope,
        "platform": "windows" if os.name == "nt" else "posix",
        "result": "raised" if facts["errors"] or facts["truncated"] else "returned",
        "facts": facts,
    }, sort_keys=True))
    return facts


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,reader_kind", [
    pytest.param("held", "view", id="held-view"),
    pytest.param("held", "naming", id="held-naming"),
    pytest.param("released", "view", id="released-view"),
    pytest.param("released", "naming", id="released-naming"),
    pytest.param("ordinary", "view", id="ordinary-view"),
    pytest.param("ordinary", "naming", id="ordinary-naming"),
])
async def test_service_publication_with_real_mutable_reader(
        publication_app, monkeypatch, record_property, reader_kind, scope):
    """Characterize sharing, NOT the identity of the historical CI reader.

    Each released case has its own app/SQLite/target, not a retry of failed
    publication. The ordinary control selects the original Path.open reader
    and must still demonstrate WinError 5 at replacement on native Windows.
    View initialization is conditional on missing native naming;
    naming progress here deliberately replaces an EXISTING leaf, not first create.
    """
    from amplifier_web import naming
    from amplifier_web.session_projection import view_path

    app = publication_app
    session = app._session()
    sid = session["id"]
    view = view_path(app.data_dir, session)
    directory = view.parent.parent
    if reader_kind == "view":
        target = view
        read = lambda: naming.initial_name(directory)
        # No activityOnly/preparationProgress: this status takes the immediate
        # _publish_changes -> _save path, never the delayed progress publisher.
        event = ("runtime.status", {
            "sessionId": sid, "status": "working", "detail": "Sharing discriminator",
        })
        label = "naming.initial_name"
    else:
        await app.on_runtime_event("session.naming.progress", {
            "sessionId": sid, "completedInputs": [],
        })
        target = directory / "naming.json"
        read = lambda: naming.legacy(directory)
        event = ("session.naming.progress", {
            "sessionId": sid, "completedInputs": ["sharing-input"],
        })
        label = "naming.legacy"
    before = _publication_snapshot(app)
    original_bytes, original_text = target.read_bytes(), target.read_text()
    original_view = view.read_bytes()
    prior = next(row for row in before[0]["sessions"] if row["id"] == sid)
    reader = _PublicationRead()
    real_open = (lambda path: path.open("r")) if scope == "ordinary" else naming._open_presentation

    from contextlib import contextmanager
    @contextmanager
    def gated_open(path):
        with real_open(path) as stream:  # genuine reader, no OS flag injection
            if threading.current_thread() is reader.thread and path == target:
                reader.hold(stream)
            yield stream

    with monkeypatch.context() as patch:
        # Only this exact path in this owned reader thread pauses. The host's
        # rollback read and all atomic/native primitives are untouched.
        patch.setattr(naming, "_open_presentation", gated_open)
        if scope == "ordinary":
            # Preserve the demonstrated original reader/writer pair as the
            # baseline. The production cells use both new owners, not this path.
            from amplifier_web import presentation_files
            from amplifier_web.host.storage import SessionStore
            patch.setattr(presentation_files, "atomic_write", SessionStore._atomic)
        reader.start(read)
        try:
            await reader.wait_open()
            if scope == "released":
                await reader.finish()
            facts = await _observed_publication(app, event, label, scope, record_property)
            if scope != "released":
                reader.assert_held()
            after = _publication_snapshot(app)
            if os.name == "nt" and scope == "ordinary":
                assert not facts["truncated"] and len(facts["errors"]) == 1
                error = facts["errors"][0]
                assert (error["class"], error.get("errno"), error.get("winerror")) == (
                    "PermissionError", 13, 5)
                expected_sources = (["service.event", "storage.projection", "storage.atomic"]
                                    if reader_kind == "view" else ["service.event", "storage.atomic"])
                assert [frame["source"] for frame in error["frames"]] == expected_sources
                assert after == before
                assert target.read_bytes() == original_bytes and view.read_bytes() == original_view
                assert app._session_projection_refs[sid] == prior
                assert app.state["revision"] == before[0]["revision"]
                if reader_kind == "view":
                    assert app._progress_dirty and app._progress_session_ids == {sid}
                    # Rollback cannot replace this held leaf either; keep that
                    # uncertainty instead of treating the live mutation as saved.
                    assert app._state.get("_viewRecoveryPending") is True
            else:
                # The production reader permits replacement without losing its
                # original object. POSIX ordinary opens also permit replacement.
                assert facts == {"errors": [], "truncated": False}
                assert after[0]["revision"] == before[0]["revision"] + 1
                assert not app._progress_dirty
                assert target.read_bytes() != original_bytes
                if reader_kind == "view":
                    saved = next(row for row in after[0]["sessions"] if row["id"] == sid)
                    assert saved["$viewPayload"] != prior["$viewPayload"]
                    value = app.state_resource(saved["$viewPayload"]["$resource"])
                    assert value["status"] == "working"
                    assert value["progress"]["detail"] == "Sharing discriminator"
                else:
                    assert json.loads(target.read_text())["naming_completed_inputs"] == ["sharing-input"]
        finally:
            await reader.finish()
    assert reader.retained == original_text
    if reader_kind == "view":
        assert reader.result[3] == json.loads(original_text)
    else:
        assert reader.result == {"naming_completed_inputs": []}
    record_property("publication_sharing_verified", True)


@pytest.mark.asyncio
async def test_service_publication_during_held_immutable_resource(publication_app, record_property):
    """An old committed resource stays readable while the real service saves anew.

    project_detached's callback runs after traversal validation/pin release.
    This control does not characterize the shorter pre-yield traversal window.
    """
    from amplifier_web.session_projection import view_path

    app = publication_app
    session = app._session()
    sid = session["id"]
    before = _publication_snapshot(app)
    prior = next(row for row in before[0]["sessions"] if row["id"] == sid)
    source = resources.detach(app.db, prior["$viewPayload"]["$resource"])
    original = resources.read_detached(source)
    original_bytes = (source.directory / (source.identity + ".json")).read_bytes()
    reader = _PublicationRead()
    reader.start(lambda: resources.project_detached(source, reader.hold))
    try:
        await reader.wait_open()
        facts = await _observed_publication(app, ("runtime.status", {
            "sessionId": sid, "status": "working", "detail": "Sharing discriminator",
        }), "resource.project", "held", record_property)
        reader.assert_held()
        assert facts == {"errors": [], "truncated": False}
        after = _publication_snapshot(app)
        assert after[0]["revision"] == before[0]["revision"] + 1
        assert not app._progress_dirty
        saved = next(row for row in after[0]["sessions"] if row["id"] == sid)
        assert saved["$viewPayload"] != prior["$viewPayload"]
        value = app.state_resource(saved["$viewPayload"]["$resource"])
        assert value["status"] == "working"
        assert value["progress"]["detail"] == "Sharing discriminator"
        assert json.loads(view_path(app.data_dir, session).read_text()) == value
    finally:
        await reader.finish()
    projection, signature = reader.result
    assert projection == reader.retained == original_bytes
    assert signature == resources.detached_signature(source)
    assert resources.read_detached(source) == original
    record_property("publication_sharing_verified", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "restore-held", "remove-held", "restore-refused", "remove-refused",
])
async def test_service_view_rollback_with_real_presentation_reader(
        publication_app, monkeypatch, record_property, scenario):
    """Fail after canonical SQLite writes, not by faking the OS sharing result.

    The two refused cells separately inject a rollback-helper OSError to qualify
    uncertainty/GC handling. They do not claim a native restoration failure.
    Each case has its own app; no failed event is replayed to demonstrate success.
    """
    from amplifier_web import naming, presentation_files, state_records
    from amplifier_web.session_projection import view_path
    from amplifier_web.worker_diagnostics import command_failure_facts, validate_command_failure_facts

    app = publication_app
    session = app._session()
    sid = session["id"]
    session["interruptionRevision"] = 7
    app._save_changes(sessions={sid})
    view = view_path(app.data_dir, session)
    original_bytes, original_text = view.read_bytes(), view.read_text()
    original_messages = json.dumps(session["messages"], sort_keys=True)
    absent = scenario.startswith("remove")
    refused = scenario.endswith("refused")
    if absent:
        # Missing compatible presentation does not remove its committed SQLite
        # pointer. This attempt will create a new leaf, then must undo it.
        view.unlink()
    if refused:
        # An actual unreferenced resource makes a no-delete GC assertion useful.
        orphan = resources.put(app.db, {"rollback-control": "unreferenced resource"})
        app.db.commit()
        orphan_path = resources.root(app.db) / (orphan["$resource"] + ".json")
        assert orphan_path.is_file()
    before = _publication_snapshot(app)
    prior = next(row for row in before[0]["sessions"] if row["id"] == sid)
    assert prior["interruptionRevision"] == 7
    assert not app._progress_dirty and not app._state.get("_viewRecoveryPending")
    history = view.parent.parent
    assert not (history / "transcript.jsonl").exists() and not (history / "events.jsonl").exists()
    reader = _PublicationRead()
    writes, removals, commits, rollback_failures = [], [], [], []
    failure = sqlite3.OperationalError("Injected SQLite failure after state_records.save")
    rollback_error = OSError(5, "Injected rollback helper refusal")
    real_write, real_remove, real_save = presentation_files.atomic_write, presentation_files.remove, state_records.save

    def hold_presentation():
        with naming._open_presentation(view) as stream:
            return reader.hold(stream)  # genuine production open; retain its exact object

    def observed_write(path, text):
        if path == view and writes:
            reader.assert_held()
            assert not absent and text == original_text
            if refused:
                rollback_failures.append("restore")
                raise rollback_error
        result = real_write(path, text)
        if path == view:
            writes.append(text)
            if len(writes) == 1:
                assert text != original_text
                if absent:
                    # Called from synchronous publication after the genuine
                    # write, before state_records.save can fail. No loop work is
                    # needed by this thread to acknowledge the retained handle.
                    reader.start(hold_presentation)
                    assert reader.opened.wait(10) and not reader.failed
                    assert reader.opens == 1
                reader.assert_held()
        return result

    def observed_remove(path, *, missing_ok=True):
        assert path == view and absent and missing_ok is True
        reader.assert_held()
        removals.append("remove")
        if refused:
            rollback_failures.append("remove")
            raise rollback_error
        return real_remove(path, missing_ok=missing_ok)

    def fail_after_write(db, state, references, session_ids, *args, **kwargs):
        result = real_save(db, state, references, session_ids, *args, **kwargs)
        assert db is app.db and set(session_ids) == {sid}
        assert db.in_transaction and len(writes) == 1 and view.read_text() == writes[0]
        assert references[sid]["$viewPayload"] != prior["$viewPayload"]
        assert state_records.load(db)["revision"] == before[0]["revision"] + 1
        assert _publication_snapshot(app) == before  # Independent connection still sees committed authority.
        reader.assert_held()
        commits.append(result)
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(presentation_files, "atomic_write", observed_write)
        patch.setattr(presentation_files, "remove", observed_remove)
        patch.setattr(state_records, "save", fail_after_write)
        try:
            if not absent:
                reader.start(hold_presentation)
                await reader.wait_open()
            with pytest.raises(sqlite3.OperationalError) as caught:
                await app.on_runtime_event("runtime.status", {
                    "sessionId": sid, "status": "working", "detail": "Rollback discriminator",
                })
            facts = command_failure_facts(caught.value)
            assert validate_command_failure_facts(facts) is not None
            # The original injected SQL failure, not a rollback OSError, is
            # retained. This is synthetic failure-origin evidence, not a native
            # SQLite defect or the historical Windows failure's cause.
            record_property("publication_sharing", json.dumps({
                "schema": 1, "reader": "naming.presentation", "scope": scenario,
                "platform": "windows" if os.name == "nt" else "posix",
                "result": "raised", "facts": facts,
            }, sort_keys=True))
            assert caught.value is failure and len(commits) == 1
            assert failure.args == ("Injected SQLite failure after state_records.save",)
            assert not facts["truncated"] and len(facts["errors"]) == 1
            assert facts["errors"][0]["class"] == "other"  # sqlite3.OperationalError is not a public label.
            assert not app.db.in_transaction and _publication_snapshot(app) == before
            assert app._session_projection_refs[sid] == prior
            assert app.state["revision"] == app._saved_revision == before[0]["revision"]
            assert session["interruptionRevision"] == 7
            assert app._progress_dirty and app._progress_scope_known
            assert app._progress_session_ids == {sid}
            assert json.dumps(session["messages"], sort_keys=True) == original_messages
            assert not app.runtime.attempts and not app.runtime.workers
            reader.assert_held()
            if refused:
                assert rollback_failures == ["remove" if absent else "restore"]
                assert app._state.get("_viewRecoveryPending") is True
                assert str(view) not in app._view_cache
                assert view.read_text() == writes[0]
                files = {path.name: path.read_bytes() for path in resources.root(app.db).glob("*.json")}
                assert resources.marked_references(app.db, app._state) is None
                assert resources.collect(app.db, app._state) == []
                app.db.execute("BEGIN IMMEDIATE")
                try:
                    assert await resources.marked_references_async(app.db, app._state) is None
                finally:
                    app.db.rollback()
                assert _publication_snapshot(app) == before
                assert {path.name: path.read_bytes() for path in resources.root(app.db).glob("*.json")} == files
                assert orphan_path.is_file()
            else:
                assert not rollback_failures and not app._state.get("_viewRecoveryPending")
                if absent:
                    assert not view.exists() and removals == ["remove"] and len(writes) == 1
                else:
                    assert view.read_bytes() == original_bytes and writes == [writes[0], original_text]
                    assert not removals
            assert not (history / "transcript.jsonl").exists() and not (history / "events.jsonl").exists()
        finally:
            if reader.thread is not None:
                await reader.finish()
    assert reader.retained == reader.result == (writes[0] if absent else original_text)
    record_property("publication_sharing_verified", True)