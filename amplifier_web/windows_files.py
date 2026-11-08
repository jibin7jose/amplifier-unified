"""Bounded private config files on native Windows local fixed NTFS.

This is not a general filesystem, settings migration, or durable authority
checkpoint backend. Pins deny rename/delete; ACL validation, not sharing, blocks
foreign WRITE_ATTRIBUTES/reparse conversion. Trusted OS administrators and other
processes running as the current TokenUser remain inside the trust boundary.
Publication flushes the file, not a directory/power-loss durability promise.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
import os
from pathlib import Path, PureWindowsPath
import re
import uuid

DWORD, WORD, BYTE = C.c_uint32, C.c_uint16, C.c_ubyte
BOOL, HANDLE, PVOID = C.c_int32, C.c_void_p, C.c_void_p
PDWORD = C.POINTER(DWORD)


class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [("length", DWORD), ("descriptor", PVOID), ("inherit", BOOL)]


class FILETIME(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class FILE_INFO(C.Structure):
    _fields_ = [
        ("attributes", DWORD), ("creation", FILETIME), ("access", FILETIME),
        ("write", FILETIME), ("volume", DWORD), ("size_high", DWORD),
        ("size_low", DWORD), ("links", DWORD), ("index_high", DWORD), ("index_low", DWORD),
    ]


class FILE_RENAME_INFO(C.Structure):
    # FileRenameInfoEx selects the DWORD Flags union arm. WCHAR is UTF-16,
    # including on POSIX hosts checking this Win64 layout.
    _fields_ = [("Flags", DWORD), ("RootDirectory", HANDLE),
                ("FileNameLength", DWORD), ("FileName", WORD * 1)]


class ACL_INFO(C.Structure):
    _fields_ = [("count", DWORD), ("used", DWORD), ("free", DWORD)]


class ACE(C.Structure):
    _fields_ = [("kind", BYTE), ("flags", BYTE), ("size", WORD), ("mask", DWORD)]


# Explicit Win64 pointer returns and fixed-width scalar arguments. Do not use
# ctypes.wintypes' host-dependent c_ulong when testing layouts on POSIX.
_SIGNATURES = {
    "CreateFileW": (HANDLE, [C.c_wchar_p, DWORD, DWORD, C.POINTER(SECURITY_ATTRIBUTES), DWORD, DWORD, HANDLE]),
    "CreateDirectoryW": (BOOL, [C.c_wchar_p, C.POINTER(SECURITY_ATTRIBUTES)]),
    "CloseHandle": (BOOL, [HANDLE]),
    "GetCurrentProcess": (HANDLE, []),
    "GetFileInformationByHandle": (BOOL, [HANDLE, C.POINTER(FILE_INFO)]),
    "GetFinalPathNameByHandleW": (DWORD, [HANDLE, C.c_wchar_p, DWORD, DWORD]),
    "GetVolumeInformationByHandleW": (BOOL, [HANDLE, C.c_wchar_p, DWORD, PDWORD, PDWORD, PDWORD, C.c_wchar_p, DWORD]),
    "GetDriveTypeW": (DWORD, [C.c_wchar_p]),
    "ReadFile": (BOOL, [HANDLE, PVOID, DWORD, PDWORD, PVOID]),
    "WriteFile": (BOOL, [HANDLE, PVOID, DWORD, PDWORD, PVOID]),
    "FlushFileBuffers": (BOOL, [HANDLE]),
    "SetFileInformationByHandle": (BOOL, [HANDLE, C.c_int32, PVOID, DWORD]),
    "LocalFree": (PVOID, [PVOID]),
    "OpenProcessToken": (BOOL, [HANDLE, DWORD, C.POINTER(HANDLE)]),
    "GetTokenInformation": (BOOL, [HANDLE, DWORD, PVOID, DWORD, PDWORD]),
    "ConvertSidToStringSidW": (BOOL, [PVOID, C.POINTER(C.c_wchar_p)]),
    "ConvertStringSecurityDescriptorToSecurityDescriptorW": (BOOL, [C.c_wchar_p, DWORD, C.POINTER(PVOID), PDWORD]),
    "GetSecurityInfo": (DWORD, [HANDLE, DWORD, DWORD, C.POINTER(PVOID), C.POINTER(PVOID), C.POINTER(PVOID), C.POINTER(PVOID), C.POINTER(PVOID)]),
    "GetSecurityDescriptorControl": (BOOL, [PVOID, C.POINTER(WORD), PDWORD]),
    "GetAclInformation": (BOOL, [PVOID, PVOID, DWORD, DWORD]),
    "GetAce": (BOOL, [PVOID, DWORD, C.POINTER(PVOID)]),
    "IsValidSid": (BOOL, [PVOID]),
}
_SECURITY_FUNCTIONS = frozenset({
    "OpenProcessToken", "GetTokenInformation", "ConvertSidToStringSidW",
    "ConvertStringSecurityDescriptorToSecurityDescriptorW", "GetSecurityInfo",
    "GetSecurityDescriptorControl", "GetAclInformation", "GetAce", "IsValidSid",
})
_OS_AUTHORITIES = frozenset({
    "S-1-5-18",  # SYSTEM
    "S-1-5-32-544",  # Administrators
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # TrustedInstaller
})
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$", re.I)
_INVALID = C.c_void_p(-1).value
_MUTATION = 0x500D0156  # generic all/write, delete, owner/DACL, data/EA/attributes/delete-child
_KNOWN_MASK = 0xF01F01FF
_MAX_CONTENT = 1024 * 1024


def _component(name: str) -> None:
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or len(name.encode("utf-16-le")) // 2 > 255
            or name.endswith((".", " "))
            or any(char in name for char in '<>:"/\\|?*~')
            or any(ord(char) < 32 for char in name)
            or _RESERVED.fullmatch(name.split(".")[0].rstrip(" "))):
        raise ValueError("unsafe Windows private-file component")


def _local(value: str) -> PureWindowsPath:
    path = PureWindowsPath(value)
    if (not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
            or len(path.parts) > 64 or len(value.encode("utf-16-le")) // 2 > 240):
        raise ValueError("private files require a bounded absolute local Windows drive path")
    # Validate lexical components before PurePath can discard a dot segment.
    if len(value) > 3:
        for part in value.replace("/", "\\")[3:].split("\\"):
            _component(part)
    return path


def address(value: str | os.PathLike) -> Path:
    """Expand and anchor without resolve(), stat(), or following any links.

    Keep '..' and alias checks ahead of filesystem access on Windows. Passing an
    already-resolved unsafe path cannot recover its original spelling.
    """
    raw = os.path.expanduser(os.fspath(value))
    if os.name == "nt":
        parsed = PureWindowsPath(raw)
        if ((parsed.drive and not parsed.is_absolute())
                or (raw.startswith(("\\", "/")) and not parsed.drive)):
            raise ValueError("drive-relative/root-relative private Windows paths are unsupported")
        if not parsed.is_absolute():
            raw = str(Path.cwd()) + "\\" + raw
        _local(raw)
        raw = raw[0].upper() + raw[1:]  # drive spelling is not a distinct identity
    elif not Path(raw).is_absolute():
        raw = str(Path.cwd() / raw)
    return Path(raw)


def _check_ace(kind: int, flags: int, mask: int, sid: str, *, private: bool) -> None:
    if kind not in {0, 1} or flags & ~0x1F or mask & ~_KNOWN_MASK:
        raise PermissionError("unsupported Windows private-file ACE or access mask")
    if kind == 1:  # deny cannot broaden rights
        return
    if sid in {"owner", "S-1-3-4", *_OS_AUTHORITIES}:
        return
    if flags & 8 and not private:
        # Traversal ancestors can carry creator/inherit-only grants. Existing
        # children are checked separately; newly owned children are protected.
        return
    # Above owned_root, FILE_ADD_SUBDIRECTORY (4) alone is safe; FILE_ADD_FILE
    # (2) and FILE_WRITE_ATTRIBUTES (0x100) can permit reparse conversion.
    if (mask != 0 if private else bool(mask & (_MUTATION & ~4))):
        raise PermissionError("Windows private-file DACL grants foreign access")


def _identity(info: FILE_INFO) -> tuple[int, int, int]:
    return info.volume, info.index_high, info.index_low


class _API:
    def __init__(self):
        if os.name != "nt" or C.sizeof(HANDLE) != 8:
            raise RuntimeError("private-file backend requires native Win64 local NTFS")
        kernel = C.WinDLL("kernel32", use_last_error=True)
        security = C.WinDLL("advapi32", use_last_error=True)
        for name, (result, arguments) in _SIGNATURES.items():
            function = getattr(security if name in _SECURITY_FUNCTIONS else kernel, name)
            function.restype, function.argtypes = result, arguments
            setattr(self, name, function)
        self.sid = self._token_user()

    @staticmethod
    def ok(result):
        if not result:
            raise C.WinError(C.get_last_error())

    def close(self, handle):
        self.ok(self.CloseHandle(handle))

    def sid_text(self, pointer):
        self.ok(self.IsValidSid(pointer))
        text = C.c_wchar_p()
        self.ok(self.ConvertSidToStringSidW(pointer, C.byref(text)))
        try:
            return text.value
        finally:
            self.LocalFree(C.cast(text, PVOID))

    def _token_user(self):
        token = HANDLE()
        self.ok(self.OpenProcessToken(self.GetCurrentProcess(), 8, C.byref(token)))
        try:
            size = DWORD()
            result = self.GetTokenInformation(token, 1, None, 0, C.byref(size))
            if result or C.get_last_error() != 122:
                raise OSError("cannot size Windows TokenUser")
            buffer = C.create_string_buffer(size.value)
            self.ok(self.GetTokenInformation(token, 1, buffer, size, C.byref(size)))
            return self.sid_text(C.cast(buffer, C.POINTER(PVOID))[0])
        finally:
            self.close(token)

    @contextmanager
    def private_security(self, *, directory=False):
        descriptor = PVOID()
        inheritance = "OICI" if directory else ""
        # Protected at creation, with no inherited ordinary-user grants.
        self.ok(self.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{self.sid}D:P(A;{inheritance};FA;;;{self.sid})(A;{inheritance};FA;;;SY)",
            1, C.byref(descriptor), None))
        try:
            yield SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
        finally:
            self.LocalFree(descriptor)

    def validate_acl(self, handle, *, private):
        owner, dacl, descriptor = PVOID(), PVOID(), PVOID()
        error = self.GetSecurityInfo(handle, 1, 5, C.byref(owner), None,
                                     C.byref(dacl), None, C.byref(descriptor))
        if error:
            raise C.WinError(error)
        try:
            control, revision = WORD(), DWORD()
            self.ok(self.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
            trusted_owners = {self.sid} if private else {self.sid, *_OS_AUTHORITIES}
            if self.sid_text(owner) not in trusted_owners or not control.value & 4 or not dacl:
                raise PermissionError("Windows private files require trusted ownership and a non-null DACL")
            # Existing safe inherited ACLs are accepted, never chmod-repaired.
            acl = ACL_INFO()
            self.ok(self.GetAclInformation(dacl, C.byref(acl), C.sizeof(acl), 2))
            for index in range(acl.count):
                pointer = PVOID()
                self.ok(self.GetAce(dacl, index, C.byref(pointer)))
                ace = C.cast(pointer, C.POINTER(ACE)).contents
                if ace.size < C.sizeof(ACE) + 8:
                    raise PermissionError("truncated Windows private-file ACE")
                sid = PVOID(pointer.value + C.sizeof(ACE))
                # Bound the SID before asking Windows to parse it.
                subauthorities = C.c_ubyte.from_address(sid.value + 1).value
                if 8 + 4 * subauthorities != ace.size - C.sizeof(ACE):
                    raise PermissionError("unsupported Windows private-file ACE layout")
                principal = self.sid_text(sid)
                _check_ace(ace.kind, ace.flags, ace.mask,
                           "owner" if principal == self.sid else principal, private=private)
        finally:
            self.LocalFree(descriptor)

    def info(self, handle, path, *, directory):
        info = FILE_INFO()
        self.ok(self.GetFileInformationByHandle(handle, C.byref(info)))
        if (info.attributes & 0x400 or bool(info.attributes & 0x10) != directory
                or not directory and info.links != 1):
            raise PermissionError("private-file path is a reparse point, hardlink, or wrong type")
        buffer = C.create_unicode_buffer(32768)
        length = self.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise OSError("cannot validate final Windows private-file path")
        actual = buffer.value.removeprefix("\\\\?\\")
        # Exact component spelling rejects short aliases, case-sensitive
        # collisions, redirected namespaces and spelling ambiguity. Drive case
        # itself has no distinct Windows namespace identity.
        if actual[2:] != str(path)[2:] or actual[:2].lower() != str(path)[:2].lower():
            raise PermissionError("Windows private-file path has an alternate namespace/case alias")
        return info

    def open(self, path, *, directory=False, create=False, cleanup=False):
        _local(str(path))
        # Pins: read attributes/control, deny WRITE and DELETE sharing.
        # Readers: allow DELETE for atomic replacement, never WRITE/partial reads.
        access = 0x20080 | (0 if directory else 0x80000000)
        share = 1 if directory else 5
        if create:
            access = 0x20080 | 0x40000000 | 0x10000  # write + DELETE for exact-handle cleanup
        elif cleanup:
            access |= 0x10000
        with self.private_security(directory=directory) as security:
            handle = self.CreateFileW(str(path), access, share, C.byref(security),
                                      1 if create else 3, 0x2200000, None)
            error = C.get_last_error() if handle == _INVALID else 0
        if handle == _INVALID:
            raise C.WinError(error)
        try:
            self.info(handle, path, directory=directory)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def read(self, handle, *, limit):
        result = bytearray()
        while len(result) <= limit:
            buffer, count = C.create_string_buffer(min(65536, limit + 1 - len(result))), DWORD()
            self.ok(self.ReadFile(handle, buffer, len(buffer), C.byref(count), None))
            if not count.value:
                return bytes(result)
            if count.value > len(buffer):
                raise OSError("invalid Windows read count")
            result.extend(buffer.raw[:count.value])
        raise ValueError("Windows private file exceeds read limit")

    def write(self, handle, content):
        offset = 0
        while offset < len(content):
            chunk, count = content[offset:offset + 65536], DWORD()
            self.ok(self.WriteFile(handle, C.create_string_buffer(chunk), len(chunk), C.byref(count), None))
            if not 0 < count.value <= len(chunk):
                raise OSError("zero or invalid Windows private-file write")
            offset += count.value

    def flush(self, handle):
        self.ok(self.FlushFileBuffers(handle))

    def move(self, handle, destination, *, replace):
        """Rename the retained DELETE source handle inside its pinned directory.

        Requires Windows 10 1607+ or Windows 11 local NTFS FileRenameInfoEx.
        REPLACE_IF_EXISTS | POSIX_SEMANTICS keeps existing target readers valid;
        new opens see the complete replacement. Initialization uses flags=0:
        an existing credential wins, never a replacement. No copy/delete,
        legacy rename, sharing relaxation or retry fallback is attempted.

        https://learn.microsoft.com/windows/win32/api/winbase/ns-winbase-file_rename_info
        https://learn.microsoft.com/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information
        """
        _local(str(destination))
        encoded = str(destination).encode("utf-16-le")
        buffer = C.create_string_buffer(C.sizeof(FILE_RENAME_INFO) + len(encoded))
        info = FILE_RENAME_INFO.from_buffer(buffer)
        info.Flags = 3 if replace else 0  # REPLACE_IF_EXISTS | POSIX_SEMANTICS only on replacement
        info.FileNameLength = len(encoded)  # Bytes, excluding a NUL terminator.
        C.memmove(C.addressof(buffer) + FILE_RENAME_INFO.FileName.offset, encoded, len(encoded))
        # RootDirectory stays NULL: this absolute target's parent is pinned and
        # the source identity/volume were checked immediately before this call.
        # Rename is same-volume only; the kernel cannot silently copy it.
        try:
            self.ok(self.SetFileInformationByHandle(handle, 22, buffer, len(buffer)))
        except OSError as exc:
            if getattr(exc, "winerror", None) in {1, 50, 87, 120}:
                raise NotImplementedError(
                    "Private publication requires Windows 10 1607+ or Windows 11 local NTFS "
                    "FileRenameInfoEx semantics; no fallback is attempted"
                ) from exc
            raise

    def discard(self, handle, path, identity):
        if _identity(self.info(handle, path, directory=False)) != identity:
            raise PermissionError("temporary private-file identity changed; cleanup refused")
        # FileDispositionInfo deletes this exact still-open object, not whatever
        # happens to occupy a pathname after a failed publication.
        delete = BYTE(1)
        self.ok(self.SetFileInformationByHandle(handle, 4, C.byref(delete), C.sizeof(delete)))


def current_process_sid() -> str:
    """Read the process TokenUser through the existing Win64 API.

    Initialization binds native functions and queries/closes the process token;
    it does not open paths, create private storage or alter security descriptors.
    Account-policy validation belongs to the caller.
    """
    return _API().sid


class PinnedDirectory:
    """Only live inside directory(); no independent access after pins close."""

    def __init__(self, path, *, owned_root, create):
        self.path, self._api, self._pins = address(path), _API(), []
        root = address(owned_root)
        if not self.path.is_relative_to(root):
            raise ValueError("Windows private directory is outside owned_root")
        chain = list(reversed([self.path, *self.path.parents]))
        try:
            if self._api.GetDriveTypeW(str(chain[0])) != 3:
                raise PermissionError("Windows private files require a local fixed NTFS volume")
            volume = None
            for part in chain:
                private = part.is_relative_to(root)
                try:
                    handle = self._api.open(part, directory=True)
                except FileNotFoundError:
                    if not create or not private:
                        raise
                    # Every earlier parent was pinned AND ACL-validated before
                    # creating/opening this child, including WRITE_ATTRIBUTES.
                    with self._api.private_security(directory=True) as security:
                        if not self._api.CreateDirectoryW(str(part), C.byref(security)):
                            error = C.get_last_error()
                            if error != 183:  # another private creator won
                                raise C.WinError(error)
                    handle = self._api.open(part, directory=True)
                try:
                    info = self._api.info(handle, part, directory=True)
                except BaseException:
                    self._api.close(handle)
                    raise
                self._pins.append((part, handle, _identity(info), private))
                if volume is None:
                    filesystem = C.create_unicode_buffer(32)
                    self._api.ok(self._api.GetVolumeInformationByHandleW(
                        handle, None, 0, None, None, None, filesystem, len(filesystem)))
                    if filesystem.value != "NTFS":
                        raise PermissionError("Windows private files require NTFS")
                    volume = info.volume
                if info.volume != volume:
                    raise PermissionError("Windows private path crosses volumes")
                self._api.validate_acl(handle, private=private)
        except BaseException:
            self._close()
            raise

    def _check(self):
        if not self._pins:
            raise RuntimeError("Windows private directory pins are closed")
        for path, handle, identity, private in self._pins:
            if _identity(self._api.info(handle, path, directory=True)) != identity:
                raise PermissionError("Windows private directory identity changed")
            self._api.validate_acl(handle, private=private)

    def _close(self):
        failure = None
        while self._pins:
            try:
                self._api.close(self._pins.pop()[1])
            except OSError as exc:
                failure = failure or exc
        if failure is not None:
            raise failure

    def _existing(self, name):
        handle = self._api.open(self.path / name)
        try:
            self._api.validate_acl(handle, private=True)
            info = self._api.info(handle, self.path / name, directory=False)
            if info.volume != self._pins[-1][2][0]:
                raise PermissionError("Windows private file crosses volumes")
            return handle
        except BaseException:
            self._api.close(handle)
            raise

    def read_private(self, name: str, *, limit: int) -> bytes:
        _component(name)
        if type(limit) is not int or not 0 <= limit <= _MAX_CONTENT:
            raise ValueError("private-file read limit must be between zero and 1 MiB")
        self._check()
        handle = self._existing(name)
        try:
            return self._api.read(handle, limit=limit)
        finally:
            self._api.close(handle)

    def publish_private(self, name: str, content: bytes, *, replace: bool = False) -> None:
        """Publish a complete file, never delete-first or overwrite by default.

        A reported error is not a promise of an unchanged destination: errors
        after a successful rename explicitly report publication.
        """
        _component(name)
        if not isinstance(content, bytes) or len(content) > _MAX_CONTENT:
            raise ValueError("private-file publication requires at most 1 MiB of bytes")
        if type(replace) is not bool:
            raise ValueError("replace must be a boolean")
        self._check()
        existing = None
        published = False
        try:
            try:
                existing = self._existing(name)
            except FileNotFoundError:
                pass
            if existing is not None and not replace:
                raise FileExistsError(str(self.path / name))
            temporary = self.path / f".private-{uuid.uuid4().hex}.tmp"
            handle = self._api.open(temporary, create=True)
            renamed = False
            identity = None
            try:
                identity = _identity(self._api.info(handle, temporary, directory=False))
                if identity[0] != self._pins[-1][2][0]:
                    raise PermissionError("Windows temporary file crosses volumes")
                self._api.validate_acl(handle, private=True)
                self._api.write(handle, content)
                self._api.flush(handle)  # mandatory; failure must not publish
                # Close WRITE access before publication. Otherwise a racing
                # read/no-replace creator (which denies WRITE sharing) could
                # receive a sharing error during the completed rename window.
                self._api.close(handle)
                handle = None
                handle = self._api.open(temporary, cleanup=True)
                self._api.validate_acl(handle, private=True)
                self._check()
                if _identity(self._api.info(handle, temporary, directory=False)) != identity:
                    raise PermissionError("Windows temporary file identity changed")
                self._api.move(handle, self.path / name, replace=replace)
                renamed = published = True
                self._api.info(handle, self.path / name, directory=False)
                self._api.validate_acl(handle, private=True)
            except BaseException as exc:
                if renamed:
                    raise OSError("private file was published; post-publication verification failed") from exc
                if identity is not None and handle is not None:
                    try:
                        self._api.discard(handle, temporary, identity)
                    except OSError as cleanup:
                        # A collision is recoverable only after successful exact
                        # cleanup. Do not let FileExistsError handlers swallow a
                        # retained temporary as normal winner convergence.
                        raise OSError(
                            f"Private publication and cleanup failed; temporary may remain: {temporary}"
                        ) from BaseExceptionGroup(
                            "private publication and exact temporary cleanup failed", [exc, cleanup])
                raise
            finally:
                try:
                    if handle is not None:
                        self._api.close(handle)
                except OSError as exc:
                    if renamed:
                        raise OSError("private file was published; closing its handle failed") from exc
                    raise
        finally:
            if existing is not None:
                try:
                    self._api.close(existing)
                except OSError as exc:
                    if published:
                        raise OSError("private file was published; closing its prior handle failed") from exc
                    raise


@contextmanager
def directory(path: str | os.PathLike, *, owned_root: str | os.PathLike, create: bool = False):
    """Pin every ancestor, validate before each child, optionally create privately.

    Missing paths raise FileNotFoundError; unsafe existing objects are never
    repaired. Only directories at/below the explicit owned_root may be created.
    """
    pinned = PinnedDirectory(path, owned_root=owned_root, create=create)
    try:
        yield pinned
    finally:
        pinned._close()