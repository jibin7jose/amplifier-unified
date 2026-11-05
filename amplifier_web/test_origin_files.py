"""Small no-follow file operations for local remote-test receipts and snapshots.

POSIX pins directory descriptors. Windows pins each directory with a handle
that denies rename/deletion and rejects reparse points. No host credentials or
arbitrary destination paths come from the remote runner.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import stat
import uuid


def supported_private_files():
    return os.name == 'nt' or (os.name == 'posix' and all(
        hasattr(os, name) for name in ('getuid', 'O_NOFOLLOW', 'O_DIRECTORY')))


def _windows_private(handle):
    """Require this account's object owner and no ordinary shared write grant.

    SYSTEM and Administrators retain their normal OS authority. A null DACL,
    an unrecognized allow ACE, or a shared write grant is not private storage.
    Query the opened handle, never a path that could name a replacement.
    """
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    security = api.GetSecurityInfo
    security.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(pointer),
                         pointer, ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer)]
    security.restype = wintypes.DWORD
    local_free = kernel.LocalFree
    local_free.argtypes = [pointer]
    owner, acl, descriptor = pointer(), pointer(), pointer()
    if security(handle, 1, 5, ctypes.byref(owner), None, ctypes.byref(acl), None, ctypes.byref(descriptor)):
        raise ValueError('The retained file permissions could not be verified.')
    def sid_text(sid):
        convert = api.ConvertSidToStringSidW
        convert.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
        text = wintypes.LPWSTR()
        if not convert(sid, ctypes.byref(text)):
            raise ValueError('The retained file owner could not be verified.')
        try:
            return text.value
        finally:
            local_free(ctypes.cast(text, pointer))
    token = wintypes.HANDLE()
    open_token = api.OpenProcessToken
    open_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    current = kernel.GetCurrentProcess
    current.restype = wintypes.HANDLE
    get_token = api.GetTokenInformation
    get_token.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    try:
        if not open_token(current(), 8, ctypes.byref(token)):
            raise ValueError('The current Windows account could not be verified.')
        def token_sid(kind):
            length = wintypes.DWORD()
            get_token(token, kind, None, 0, ctypes.byref(length))
            value = ctypes.create_string_buffer(length.value)
            if not get_token(token, kind, value, length, ctypes.byref(length)):
                raise ValueError('The current Windows account could not be verified.')
            return sid_text(pointer.from_buffer(value).value)
        user = token_sid(1)  # TokenUser
        # Elevated Windows processes may create objects owned by Administrators.
        # Accept that only when it is this token's actual default object owner;
        # never accept an arbitrary owner group or merely group membership.
        default_owner = token_sid(4)  # TokenOwner
        valid_owners = {user}
        if default_owner == 'S-1-5-32-544':
            valid_owners.add(default_owner)
        if sid_text(owner) not in valid_owners or not acl:
            raise ValueError('Retained test records require private storage owned by this account.')
        class ACL(ctypes.Structure):
            _fields_ = [('revision', ctypes.c_ubyte), ('reserved', ctypes.c_ubyte),
                        ('size', ctypes.c_ushort), ('count', ctypes.c_ushort), ('reserved2', ctypes.c_ushort)]
        get_ace = api.GetAce
        get_ace.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
        # OWNER RIGHTS refers to the exact owner verified above, not another user.
        allowed = {user, 'S-1-3-4', 'S-1-5-18', 'S-1-5-32-544'}
        for index in range(ctypes.cast(acl, ctypes.POINTER(ACL)).contents.count):
            ace = pointer()
            if not get_ace(acl, index, ctypes.byref(ace)):
                raise ValueError('The retained file permissions could not be verified.')
            if ctypes.c_ubyte.from_address(ace.value + 1).value & 0x08:
                continue  # INHERIT_ONLY_ACE does not grant access to this object.
            kind = ctypes.c_ubyte.from_address(ace.value).value
            if kind in (1, 2, 3, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17):
                continue  # Deny/audit/label ACEs grant no ordinary write access.
            if kind != 0:
                raise ValueError('The retained file has unsupported Windows access grants.')
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            if mask & 0x500D0156 and sid_text(ace.value + 8) not in allowed:
                raise ValueError('Retained test records cannot be writable by other Windows users.')
    finally:
        if token:
            close(token)
        local_free(descriptor)


def _windows_handle(path, *, directory=False, private=False):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    create = api.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = api.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    # No FILE_SHARE_DELETE: a held parent cannot be renamed/replaced. Files
    # also deny concurrent writes; directories permit ordinary child creation.
    handle = create(str(path), 0x80000000, 3 if directory else 1, None, 3,
                    0x00200000 | (0x02000000 if directory else 0), None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    returned = False
    try:
        class Info(ctypes.Structure):
            _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
                        ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
                        ('volume', wintypes.DWORD), ('sizeHigh', wintypes.DWORD),
                        ('sizeLow', wintypes.DWORD), ('links', wintypes.DWORD),
                        ('indexHigh', wintypes.DWORD), ('indexLow', wintypes.DWORD)]
        inspect = api.GetFileInformationByHandle
        inspect.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
        inspect.restype = wintypes.BOOL
        info = Info()
        if not inspect(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise ValueError('The selected path is a link or changed file type.')
        if private:
            _windows_private(handle)
        if directory:
            returned = True
            return handle, close
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None
        return fd, os.close
    finally:
        if handle is not None and not returned:
            close(handle)


@contextmanager
def directory(path, *, create=False, private=False):
    if not supported_private_files():
        raise ValueError('Private test records are not supported on this platform.')
    path = Path(path).absolute()
    with ExitStack() as stack:
        if os.name == 'nt':
            current = Path(path.anchor)
            handle, close = _windows_handle(current, directory=True)
            stack.callback(close, handle)
            for index, part in enumerate(path.parts[1:], 1):
                current /= part
                if create:
                    try:
                        current.mkdir(mode=0o700)
                    except FileExistsError:
                        pass
                handle, close = _windows_handle(current, directory=True, private=private and index == len(path.parts)-1)
                stack.callback(close, handle)
        else:
            fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            for part in path.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                stack.callback(os.close, fd)
        if private and os.name != 'nt':
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Retained test storage must be private to this account.')
        yield PinnedDirectory(path, None if os.name == 'nt' else fd)


@contextmanager
def file(path, *, private=False):
    path = Path(path).absolute()
    with directory(path.parent) as parent:
        if os.name == 'nt':
            fd, _ = _windows_handle(path, private=private)
        else:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent.fd)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('The selected path is not a regular file.')
            yield stream


def private_info(info, limit):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or (os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
        raise ValueError('The retained record is not a private regular file.')


def read(path, limit=65536):
    with file(path, private=True) as stream:
        private_info(os.fstat(stream.fileno()), limit)
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError('The retained record exceeds its bound.')
        return json.loads(content)


def sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class PinnedDirectory:
    def __init__(self, path, fd):
        self.path, self.fd = path, fd

    def argument(self, name):
        return self.path / name if self.fd is None else name

    def stat(self, name=None):
        if name is None:
            return self.path.stat() if self.fd is None else os.fstat(self.fd)
        return os.stat(self.argument(name), dir_fd=self.fd, follow_symlinks=False)

    def readlink(self, name):
        return os.readlink(self.argument(name), dir_fd=self.fd)

    @contextmanager
    def open(self, name, *, private=False):
        """Open a regular file relative to this pinned, no-follow directory."""
        descriptor = (_windows_handle(self.argument(name), private=private)[0] if self.fd is None else
                      os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd))
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('The selected path is not a regular file.')
            yield stream

    def create(self, name):
        return os.open(self.argument(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600, dir_fd=self.fd)

    def unlink(self, name):
        os.unlink(self.argument(name), dir_fd=self.fd)

    def link(self, old, new):
        os.link(self.argument(old), self.argument(new), src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)

    def replace(self, old, new):
        os.replace(self.argument(old), self.argument(new), src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def assert_current(self):
        with directory(self.path) as current:
            before, after = self.stat(), current.stat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError('The selected destination directory changed.')

    def sync(self):
        if self.fd is not None:
            os.fsync(self.fd)



def replace(path, value):
    """Atomic replacement in an already private owner directory."""
    path = Path(path)
    with directory(path.parent, private=True) as parent:
        temporary = '.' + uuid.uuid4().hex + '.tmp'
        try:
            with os.fdopen(parent.create(temporary), 'wb') as stream:
                stream.write(json.dumps(value, sort_keys=True).encode())
                stream.flush()
                os.fsync(stream.fileno())
            parent.replace(temporary, path.name)
            parent.sync()
            parent.assert_current()
        finally:
            try:
                parent.unlink(temporary)
            except FileNotFoundError:
                pass


def publish(path, value):
    """Atomic immutable publication; a duplicate must have identical contents."""
    path = Path(path)
    with directory(path.parent, create=True, private=True) as parent:
        temporary = '.' + uuid.uuid4().hex + '.tmp'
        try:
            with os.fdopen(parent.create(temporary), 'wb') as stream:
                stream.write(json.dumps(value, sort_keys=True).encode())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                parent.link(temporary, path.name)
            except FileExistsError:
                pass
            parent.unlink(temporary)
            parent.sync()
            if read(path) != value:
                raise ValueError('The retained record belongs to another operation.')
        finally:
            try:
                parent.unlink(temporary)
            except FileNotFoundError:
                pass


def source_file(source, name, index_mode, maximum):
    """Read selected bytes without traversing a child symlink or junction."""
    from pathlib import PurePosixPath
    parts = PurePosixPath(name).parts
    if (not parts or PurePosixPath(name).is_absolute()
            or any(part in ('..', '.git') for part in parts)
            or (os.name == 'nt' and any(':' in part or '\\' in part or part.casefold() == '.git' or part.endswith((' ', '.')) for part in parts))):
        raise ValueError('A selected source path is outside the supported tree.')
    path = Path(source).joinpath(*parts)
    try:
        with directory(path.parent) as parent:
            before = parent.stat(path.name)
            if stat.S_ISLNK(before.st_mode):
                # Read the link itself, never its destination. Junctions are
                # different reparse types and are rejected by file().
                return '120000', os.fsencode(parent.readlink(path.name))
            descriptor = _windows_handle(path)[0] if parent.fd is None else os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent.fd)
            with os.fdopen(descriptor, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('A selected source file changed type.')
                if before.st_size > maximum:
                    raise ValueError('A selected source file exceeds its size bound.')
                data = stream.read(maximum + 1)
                after = os.fstat(stream.fileno())
            if len(data) > maximum or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError('The selected source changed while being read.')
            # Git for Windows represents symlinks as link-text files when
            # core.symlinks=false, and carries executable mode in its index.
            mode = index_mode if os.name == 'nt' and index_mode in ('120000', '100755') else '100755' if os.name != 'nt' and before.st_mode & 0o111 else '100644'
            return mode, data
    except FileNotFoundError:
        return None
