"""Atomic ordinary presentation files, never history or credential authority.

The caller selects this boundary explicitly. SQLite's committed resource pointer
owns the truth; a presentation file may precede its commit or need rollback.
Windows uses exact-handle POSIX rename/removal so delete-sharing readers keep
their old object while new opens see the new namespace. This is NOT the private
ACL publisher or the strict immutable-resource reader. No ACL repair, traversal
policy, retries, delete-first replacement or legacy fallback belongs here.
"""
from contextlib import contextmanager
import ctypes as C
import errno
import os
from pathlib import Path
import uuid


_DWORD, _WORD = C.c_uint32, C.c_uint16
_BOOL, _HANDLE, _PVOID = C.c_int32, C.c_void_p, C.c_void_p
_INVALID = C.c_void_p(-1).value


class _RenameInfo(C.Structure):
    # FileRenameInfoEx selects the DWORD union arm. WCHAR is always UTF-16,
    # including when portable tests inspect this Win64 layout on POSIX.
    _fields_ = [('Flags', _DWORD), ('RootDirectory', _HANDLE),
                ('FileNameLength', _DWORD), ('FileName', _WORD * 1)]


_SIGNATURES = {
    'CreateFileW': (_HANDLE, [C.c_wchar_p, _DWORD, _DWORD, _PVOID,
                             _DWORD, _DWORD, _HANDLE]),
    'WriteFile': (_BOOL, [_HANDLE, _PVOID, _DWORD, C.POINTER(_DWORD), _PVOID]),
    'FlushFileBuffers': (_BOOL, [_HANDLE]),
    'SetFileInformationByHandle': (_BOOL, [_HANDLE, C.c_int32, _PVOID, _DWORD]),
    'CloseHandle': (_BOOL, [_HANDLE]),
}


class _WindowsAPI:
    def __init__(self):
        kernel = C.WinDLL('kernel32', use_last_error=True)
        for name, (result, args) in _SIGNATURES.items():
            try:
                function = getattr(kernel, name)
            except AttributeError as error:
                raise OSError(
                    errno.ENOSYS,
                    'Presentation publication requires native rename/removal support; no fallback'
                ) from error
            function.restype, function.argtypes = result, args
            setattr(self, name, function)
        self.publication = self.removal = 'not_attempted'

    @staticmethod
    def checked(result):
        if not result:
            raise C.WinError(C.get_last_error())

    def open(self, path, *, temporary):
        # NULL security attributes: non-inheritable handle, inherited ordinary
        # ACL on creation. Share all; no directory-backup flag, so a directory
        # target refuses. Existing rollback targets open the leaf reparse object
        # itself (unlink semantics), while ordinary parent links are followed.
        handle = self.CreateFileW(
            str(path), 0x40010000 if temporary else 0x10000, 7, None,
            1 if temporary else 3, 0x80 if temporary else 0x200000, None,
        )  # GENERIC_WRITE|DELETE / DELETE; CREATE_NEW / OPEN_EXISTING
        if handle == _INVALID:
            raise C.WinError(C.get_last_error())
        return handle

    def write(self, handle, content):
        offset = 0
        while offset < len(content):
            chunk = content[offset:offset + 65536]
            count = _DWORD()
            self.checked(self.WriteFile(
                handle, C.create_string_buffer(chunk), len(chunk), C.byref(count), None))
            if not 0 < count.value <= len(chunk):
                raise OSError('Invalid presentation write length')
            offset += count.value

    def flush(self, handle):
        self.checked(self.FlushFileBuffers(handle))

    def _information(self, handle, kind, buffer, size, effect):
        # Cancellation/another BaseException during the native call is NOT a
        # proven failed mutation. In particular, never discard a possibly
        # published source as though it were still just an unpublished temporary.
        setattr(self, effect, 'unconfirmed')
        result = self.SetFileInformationByHandle(handle, kind, buffer, size)
        setattr(self, effect, 'confirmed' if result else 'failed')
        if not result:
            error = C.WinError(C.get_last_error())
            if getattr(error, 'winerror', None) in {1, 50, 87, 120}:
                error.add_note(
                    'Required FileRenameInfoEx/FileDispositionInfoEx operation refused; '
                    'support may be unavailable. No fallback was attempted.')
            raise error

    def replace(self, handle, destination):
        # Absolute lexical path, not resolve(): follow parent links but replace
        # the named leaf, including a leaf symlink, as ordinary os.replace does.
        encoded = str(destination).encode('utf-16-le', errors='surrogatepass')
        buffer = C.create_string_buffer(C.sizeof(_RenameInfo) + len(encoded))
        info = _RenameInfo.from_buffer(buffer)
        info.Flags = 3  # FILE_RENAME_REPLACE_IF_EXISTS | FILE_RENAME_POSIX_SEMANTICS
        info.FileNameLength = len(encoded)
        C.memmove(C.addressof(buffer) + _RenameInfo.FileName.offset, encoded, len(encoded))
        self._information(handle, 22, buffer, len(buffer), 'publication')

    def discard(self, handle):
        # FILE_DISPOSITION_DELETE | FILE_DISPOSITION_POSIX_SEMANTICS. No
        # IGNORE_READONLY_ATTRIBUTE, ON_CLOSE, pathname delete or fallback.
        flags = _DWORD(3)
        self._information(handle, 21, C.byref(flags), C.sizeof(flags), 'removal')

    def close(self, handle):
        self.checked(self.CloseHandle(handle))


def _cleanup_note(error, cleanup, operation):
    # Keep the original exception (including cancellation) as the primary
    # failure. Retain cleanup category/code without copying private path prose.
    code = getattr(cleanup, 'winerror', None)
    suffix = f', winerror={code}' if type(code) is int else ''
    error.add_note(f'{operation} unconfirmed ({type(cleanup).__name__}{suffix}); no retry.')


@contextmanager
def _owned(api, handle):
    failure = None
    try:
        yield
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            api.close(handle)  # One attempt, never retry a possibly closed handle.
        except BaseException as cleanup:
            if failure is not None:
                _cleanup_note(failure, cleanup, 'Presentation handle close')
            else:
                cleanup.add_note(
                    f'Presentation publication={api.publication}, removal={api.removal}; '
                    'handle close unconfirmed. Do not replay the operation.')
                raise


def _windows_path(path):
    path = Path(path)
    if '\0' in str(path):
        raise ValueError('embedded null character')
    return path.absolute()  # No resolve, ACL inspection, private-name or volume restriction.


def atomic_write(path, text):
    """Publish complete UTF-8 presentation, preserving existing permission errors.

    File flush is required, not a directory/power-loss guarantee. Failures after
    confirmed/uncertain rename are never rolled back by deleting the source.
    """
    if os.name != 'nt':
        from .host.storage import SessionStore
        return SessionStore._atomic(Path(path), text)
    destination = _windows_path(path)
    content = text.encode('utf-8')
    api = _WindowsAPI()
    temporary = destination.with_name(f'.presentation-{uuid.uuid4().hex}.tmp')
    handle = api.open(temporary, temporary=True)  # Collision owns nothing; never retry.
    with _owned(api, handle):
        try:
            api.write(handle, content)
            api.flush(handle)
            api.replace(handle, destination)
        except BaseException as error:
            if api.publication in {'confirmed', 'unconfirmed'}:
                error.add_note(
                    f'Presentation publication={api.publication}; source cleanup skipped '
                    'because it may be published. Inspect the outcome before retrying.')
            else:
                try:
                    api.discard(handle)  # Only our CREATE_NEW object, never its pathname.
                except BaseException as cleanup:
                    _cleanup_note(error, cleanup, 'Presentation temporary removal')
            raise


def remove(path, missing_ok=True):
    """Remove one presentation leaf for rollback; never erase canonical history.

    Only a missing open is suppressed. Permission, sharing, wrong-type and
    unsupported-operation failures remain failures even with missing_ok=True.
    """
    if os.name != 'nt':
        return Path(path).unlink(missing_ok=missing_ok)
    target = _windows_path(path)
    api = _WindowsAPI()
    try:
        handle = api.open(target, temporary=False)
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    with _owned(api, handle):
        try:
            api.discard(handle)
        except BaseException as error:
            if api.removal in {'confirmed', 'unconfirmed'}:
                error.add_note(
                    f'Presentation removal={api.removal}; inspect the outcome before retrying.')
            raise