"""Read-only local NTFS files, not the private credential/ACL boundary.

Workspace files can have ordinary inherited ACLs. Open each lexical component
relative to its retained parent handle, never resolve a junction before checking
it. Deny data-write/delete sharing, reject reparses/hardlinks/namespace aliases,
and keep the complete chain until descriptor and path evidence agree. Release
only traversal pins before yielding the same leaf handle: this is a read-only
snapshot, not parent-directory ownership. Later opens repeat full validation.
Callers own byte/projection limits and immutable-resource hashes/path rechecks.
"""
from contextlib import contextmanager, ExitStack
import ctypes as C
import os
from pathlib import PureWindowsPath

from .windows_files import (
    _API, _INVALID, _component, _identity, _local,
    BOOL, DWORD, FILE_INFO, HANDLE, PVOID, WORD,
)


class _Unicode(C.Structure):
    _fields_ = [("length", WORD), ("maximum", WORD), ("buffer", C.c_wchar_p)]


class _Attributes(C.Structure):
    _fields_ = [("length", DWORD), ("root", HANDLE),
                ("name", C.POINTER(_Unicode)), ("flags", DWORD),
                ("security", PVOID), ("quality", PVOID)]


class _Status(C.Structure):
    _fields_ = [("status", PVOID), ("information", C.c_size_t)]


class _Basic(C.Structure):
    _fields_ = [("created", C.c_int64), ("accessed", C.c_int64),
                ("written", C.c_int64), ("changed", C.c_int64),
                ("attributes", DWORD)]


class _ReadAPI(_API):
    def __init__(self):
        super().__init__()
        kernel = C.WinDLL("kernel32", use_last_error=True)
        native = C.WinDLL("ntdll", use_last_error=True)
        self.basic = kernel.GetFileInformationByHandleEx
        self.basic.restype, self.basic.argtypes = BOOL, [HANDLE, C.c_int32, PVOID, DWORD]
        self.create = native.NtCreateFile
        self.create.restype = C.c_int32
        self.create.argtypes = [
            C.POINTER(HANDLE), DWORD, C.POINTER(_Attributes), C.POINTER(_Status),
            PVOID, DWORD, DWORD, DWORD, DWORD, PVOID, DWORD,
        ]
        self.dos_error = native.RtlNtStatusToDosError
        self.dos_error.restype, self.dos_error.argtypes = DWORD, [C.c_int32]

    def open_read(self, path, parent=None, *, directory):
        # FILE_LIST_DIRECTORY makes directory sharing effective; attributes-only
        # pins do not stop a rename. No ACL or security descriptor is changed.
        # Native synchronous opens require explicit SYNCHRONIZE in the raw
        # desired-access mask, before GENERIC_READ is expanded.
        access = 0x100081 if directory else 0x80100000
        if parent is None:
            handle = self.CreateFileW(str(path), access, 1, None, 3, 0x2200000, None)
            if handle == _INVALID:
                raise C.WinError(C.get_last_error())
        else:
            name = C.create_unicode_buffer(path.name)
            length = len(path.name.encode("utf-16-le"))
            text = _Unicode(length, length + 2, C.cast(name, C.c_wchar_p))
            attributes = _Attributes(C.sizeof(_Attributes), parent, C.pointer(text),
                                     0x1000, None, None)  # OBJ_DONT_REPARSE
            handle, status = HANDLE(), _Status()
            # FILE_OPEN, synchronous, OPEN_REPARSE_POINT, exact final type.
            result = self.create(C.byref(handle), access, C.byref(attributes),
                                 C.byref(status), None, 0, 1, 1,
                                 0x200020 | (1 if directory else 0x40), None, 0)
            if result < 0:
                raise C.WinError(self.dos_error(result))
            handle = handle.value
        try:
            self.info(handle, path, directory=directory)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def signature(self, handle, path=None):
        if path is not None:
            info = self.info(handle, path, directory=False)
        else:
            # Snapshot evidence must come from the retained object, not a
            # replacement pathname after a parent rename. Preserve the same
            # type/reparse/single-link checks as the pre-yield path validation.
            info = FILE_INFO()
            self.ok(self.GetFileInformationByHandle(handle, C.byref(info)))
            if info.attributes & (0x400 | 0x10) or info.links != 1:
                raise PermissionError("read snapshot is a reparse point, hardlink, or wrong type")
        basic = _Basic()
        self.ok(self.basic(handle, 0, C.byref(basic), C.sizeof(basic)))
        # ChangeTime, unlike Windows st_ctime (creation time), detects metadata
        # changes even when the last-write timestamp has been restored.
        return (info.volume, (info.index_high << 32) | info.index_low,
                (info.size_high << 32) | info.size_low, basic.written, basic.changed)


def address(value, *, base=None):
    """Lexical local path only: no stat, resolve, creation or content reads."""
    raw = os.path.expanduser(os.fspath(value))
    parsed = PureWindowsPath(raw)
    if (parsed.drive and not parsed.is_absolute()
            or raw.startswith(("\\", "/")) and not parsed.drive):
        raise ValueError("Output files require an absolute local Windows path.")
    if not parsed.is_absolute():
        # Validate before PurePath can silently discard dot/empty components.
        for part in raw.replace("/", "\\").split("\\"):
            _component(part)
        raw = str(base if base is not None else os.getcwd()) + "\\" + raw
    path = _local(raw)
    return PureWindowsPath(path.drive.upper() + str(path)[2:])


def workspace_path(root, value):
    root = address(root)
    path = address(value, base=root)
    # PureWindowsPath comparisons fold case, but NTFS directories can have
    # case-sensitive children. address() normalizes only the drive spelling;
    # every configured-root component must match exactly before any native I/O.
    root_parts, path_parts = root.parts, path.parts
    if path_parts[:len(root_parts)] != root_parts:
        raise ValueError("Output files must stay inside this conversation workspace.")
    if len(path_parts) == len(root_parts):
        raise ValueError("Choose a file.")
    return path


@contextmanager
def open_file(value):
    """Yield a seekable binary stream from the validated native handle.

    The returned stream owns the leaf handle; no second pathname content-open
    occurs. Traverse and recheck before yielding, then release directory pins so
    an independent atomic writer can publish another file. A parent move cannot
    redirect this stream. This does not authorize any future open through that
    name: each open validates the full path again. Missing files create nothing.
    """
    import msvcrt

    path = address(value)
    api = _ReadAPI()
    chain = list(reversed(path.parents))
    with ExitStack() as stack:
        if api.GetDriveTypeW(str(chain[0])) != 3:
            raise PermissionError("Output reads require a local fixed NTFS volume.")
        pins, parent, volume = [], None, None
        for part in chain:
            handle = api.open_read(part, parent, directory=True)
            stack.callback(api.close, handle)
            info = api.info(handle, part, directory=True)
            if volume is None:
                filesystem = C.create_unicode_buffer(32)
                api.ok(api.GetVolumeInformationByHandleW(
                    handle, None, 0, None, None, None, filesystem, len(filesystem)))
                if filesystem.value != "NTFS":
                    raise PermissionError("Output reads require NTFS.")
                volume = info.volume
            if info.volume != volume:
                raise PermissionError("Output path crosses volumes.")
            pins.append((part, handle, _identity(info)))
            parent = handle
        handle = api.open_read(path, parent, directory=False)
        # Transfer ownership exactly once, including fdopen failure.
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            api.close(handle)
            raise
        try:
            stream = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            before = api.signature(handle, path)
            if before[0] != volume:
                raise PermissionError("Output file crosses volumes.")
            # Pre-yield containment/identity proof while all traversal pins are
            # still held. Attribute/reparse changes must refuse before delivery.
            for part, pin, identity in pins:
                if _identity(api.info(pin, part, directory=True)) != identity:
                    raise ValueError("Output directory changed before reading.")
            # Confirm the exact leaf is still named by its retained parent.
            check = api.open_read(path, parent, directory=False)
            try:
                if api.signature(check, path) != before:
                    raise ValueError("File path changed before reading.")
            finally:
                api.close(check)
            stack.close()  # only traversal handles; stream retains the same leaf
            if api.signature(handle) != before:
                raise ValueError("File changed before reading.")
            stream._windows_read_signature = lambda: api.signature(handle)
            yield stream
            if api.signature(handle) != before:
                raise ValueError("File changed while reading.")


def stream_signature(stream):
    """Native identity/size/write/change evidence for an already open stream."""
    if stream.closed:
        raise ValueError("Output file is closed.")
    return stream._windows_read_signature()