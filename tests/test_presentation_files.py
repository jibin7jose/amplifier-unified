"""Ordinary presentation ABI/control flow plus real POSIX files, not native proof."""
import asyncio
import ctypes as C
import os
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from amplifier_web import presentation_files as presentation


class _Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


@pytest.fixture
def windows_model(monkeypatch):
    """Bind the real helper to a bounded kernel model; never touch Windows ACLs."""
    model = SimpleNamespace(
        events=[], failures={}, last_error=0, handle=0x1234567887654321,
        data=bytearray(), partial=None, bad_count=None, published=False,
        removed=False, api=None, counter=0, opens=[],
    )

    def failed(operation):
        failure = model.failures.get(operation)
        if isinstance(failure, BaseException):
            raise failure
        if failure is not None:
            model.last_error = failure
            return True
        return False

    def create(path, access, sharing, security, disposition, flags, template):
        model.events.append('open')
        model.opens.append((path, access, sharing, security, disposition, flags, template))
        return presentation._INVALID if failed('open') else model.handle

    def write(handle, buffer, size, count, overlapped):
        assert handle == model.handle and overlapped is None and 0 < size <= 65536
        model.events.append('write')
        if failed('write'):
            return False
        length = min(size, model.partial) if model.partial is not None else size
        if model.bad_count is not None:
            length = model.bad_count
        C.cast(count, C.POINTER(C.c_uint32))[0] = length
        if 0 < length <= size:
            model.data.extend(C.string_at(buffer, length))
        return True

    def flush(handle):
        assert handle == model.handle
        model.events.append('flush')
        return not failed('flush')

    def information(handle, kind, buffer, size):
        assert handle == model.handle
        if kind == 22:
            model.events.append('rename')
            info = C.cast(buffer, C.POINTER(presentation._RenameInfo)).contents
            assert size >= C.sizeof(presentation._RenameInfo) + info.FileNameLength
            assert info.Flags == 3 and info.RootDirectory is None
            model.destination = C.string_at(
                C.cast(buffer, C.c_void_p).value + presentation._RenameInfo.FileName.offset,
                info.FileNameLength).decode('utf-16-le', errors='surrogatepass')
            if failed('rename'):
                return False
            model.published = True
            if model.failures.get('rename_after') is not None:
                raise model.failures['rename_after']
        else:
            assert kind == 21 and size == 4
            assert C.cast(buffer, C.POINTER(C.c_uint32)).contents.value == 3
            model.events.append('remove')
            if failed('remove'):
                return False
            model.removed = True
            if model.failures.get('remove_after') is not None:
                raise model.failures['remove_after']
        return True

    def close(handle):
        assert handle == model.handle
        model.events.append('close')
        return not failed('close')

    kernel = SimpleNamespace(**{name: _Function(call) for name, call in (
        ('CreateFileW', create), ('WriteFile', write), ('FlushFileBuffers', flush),
        ('SetFileInformationByHandle', information), ('CloseHandle', close))})
    model.kernel = kernel
    errors = {}
    def win_error(code):
        if code not in errors:
            kind = (FileNotFoundError if code in {2, 3} else FileExistsError if code in {80, 183}
                    else PermissionError if code in {5, 32} else OSError)
            error = kind(13 if code in {5, 32} else 2 if code in {2, 3} else 22, 'fixture')
            error.winerror = code
            errors[code] = error
        return errors[code]
    model.errors = errors

    def load(name, *, use_last_error):
        assert name == 'kernel32' and use_last_error is True
        return kernel  # No private API, token query, ACL helper or native fallback exists.
    monkeypatch.setattr(C, 'WinDLL', load, raising=False)
    monkeypatch.setattr(C, 'get_last_error', lambda: model.last_error, raising=False)
    monkeypatch.setattr(C, 'WinError', win_error, raising=False)
    monkeypatch.setattr(presentation, 'os', SimpleNamespace(name='nt'))
    real_api = presentation._WindowsAPI
    def api():
        model.api = real_api()
        return model.api
    monkeypatch.setattr(presentation, '_WindowsAPI', api)
    def identity():
        model.counter += 1
        return SimpleNamespace(hex=f'{model.counter:032x}')
    monkeypatch.setattr(presentation, 'uuid', SimpleNamespace(uuid4=identity))
    return model


def test_presentation_win64_layout_and_minimal_bindings(windows_model):
    model = windows_model
    presentation._WindowsAPI()
    assert C.sizeof(C.c_void_p) == 8
    assert C.sizeof(presentation._RenameInfo) == 24
    assert [getattr(presentation._RenameInfo, name).offset for name in
            ('Flags', 'RootDirectory', 'FileNameLength', 'FileName')] == [0, 8, 16, 20]
    expected = {
        'CreateFileW': (C.c_void_p, [C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p,
                                    C.c_uint32, C.c_uint32, C.c_void_p]),
        'WriteFile': (C.c_int32, [C.c_void_p, C.c_void_p, C.c_uint32,
                                C.POINTER(C.c_uint32), C.c_void_p]),
        'FlushFileBuffers': (C.c_int32, [C.c_void_p]),
        'SetFileInformationByHandle': (C.c_int32, [C.c_void_p, C.c_int32, C.c_void_p, C.c_uint32]),
        'CloseHandle': (C.c_int32, [C.c_void_p]),
    }
    assert set(presentation._SIGNATURES) == set(expected)
    for name, (result, args) in expected.items():
        function = getattr(model.kernel, name)
        assert function.restype is result and function.argtypes == args
    assert not model.events


@pytest.mark.parametrize('text', ['', 'UTF-8 snow \u96ea\n', 'x' * 65539])
def test_presentation_windows_write_flush_rename_same_handle(windows_model, tmp_path, text):
    model = windows_model
    target = tmp_path / 'ordinary name.json'
    presentation.atomic_write(target, text)
    assert bytes(model.data) == text.encode('utf-8')
    assert model.events[0] == 'open' and model.events[-3:] == ['flush', 'rename', 'close']
    assert all(event == 'write' for event in model.events[1:-3])
    assert model.published and not model.removed
    assert model.api.publication == 'confirmed'
    temporary, *flags = model.opens[0]
    assert Path(temporary).parent == target.parent
    assert Path(temporary).name == '.presentation-00000000000000000000000000000001.tmp'
    assert flags == [0x40010000, 7, None, 1, 0x80, None]
    assert model.destination == str(target.absolute())
    assert model.counter == 1  # CREATE_NEW collision is not a UUID retry loop.


def test_presentation_windows_partial_writes_advance_exact_bytes(windows_model, tmp_path):
    model = windows_model
    model.partial = 3
    text = 'multibyte \u96ea\U00010348\r\n'
    presentation.atomic_write(tmp_path / 'view.json', text)
    assert bytes(model.data) == text.encode('utf-8')
    assert model.events.count('write') == (len(text.encode('utf-8')) + 2) // 3
    assert model.events[-3:] == ['flush', 'rename', 'close']


@pytest.mark.parametrize('count', [0, 999])
def test_presentation_invalid_write_count_discards_exact_temporary(windows_model, tmp_path, count):
    model = windows_model
    model.bad_count = count
    with pytest.raises(OSError, match='write length'):
        presentation.atomic_write(tmp_path / 'view.json', 'small')
    assert model.events == ['open', 'write', 'remove', 'close']
    assert not model.published and model.removed


@pytest.mark.parametrize('operation', ['write', 'flush', 'rename'])
@pytest.mark.parametrize('code', [5, 32, 50])
def test_presentation_failure_keeps_error_and_cleans_by_handle(windows_model, tmp_path, operation, code):
    model = windows_model
    model.failures[operation] = code
    with pytest.raises(OSError) as caught:
        presentation.atomic_write(tmp_path / 'view.json', 'new')
    assert caught.value is model.errors[code]
    assert model.events[-2:] == ['remove', 'close']
    assert model.events.count(operation) == 1
    assert model.events.count('open') == 1
    assert not model.published and model.removed
    if operation == 'rename' and code == 50:
        assert 'No fallback' in ' '.join(caught.value.__notes__)


@pytest.mark.parametrize('code', [80, 183, 2, 3, 5, 32])
def test_presentation_failed_open_owns_nothing_no_collision_retry(windows_model, tmp_path, code):
    model = windows_model
    model.failures['open'] = code
    with pytest.raises(OSError) as caught:
        presentation.atomic_write(tmp_path / 'view.json', 'new')
    assert caught.value is model.errors[code]
    assert model.events == ['open'] and model.counter == 1
    assert not model.published and not model.removed


@pytest.mark.parametrize('primary', [PermissionError('fixture'), asyncio.CancelledError()])
def test_presentation_cleanup_failures_do_not_lose_original(windows_model, tmp_path, primary):
    model = windows_model
    model.failures.update(write=primary, remove=50, close=6)
    with pytest.raises(type(primary)) as caught:
        presentation.atomic_write(tmp_path / 'view.json', 'new')
    assert caught.value is primary
    notes = ' '.join(primary.__notes__)
    assert 'temporary removal unconfirmed' in notes and 'winerror=50' in notes
    assert 'handle close unconfirmed' in notes and 'winerror=6' in notes
    assert model.events == ['open', 'write', 'remove', 'close']


@pytest.mark.parametrize('operation', ['write', 'flush'])
def test_presentation_cancel_before_publication_still_cleans(windows_model, tmp_path, operation):
    model = windows_model
    cancellation = asyncio.CancelledError()
    model.failures[operation] = cancellation
    with pytest.raises(asyncio.CancelledError) as caught:
        presentation.atomic_write(tmp_path / 'view.json', 'new')
    assert caught.value is cancellation
    assert model.events[-2:] == ['remove', 'close']
    assert model.removed and not model.published


@pytest.mark.parametrize('operation', ['rename', 'rename_after'])
def test_presentation_uncertain_rename_never_discards_possible_publication(windows_model, tmp_path, operation):
    model = windows_model
    cancellation = asyncio.CancelledError()
    model.failures[operation] = cancellation
    with pytest.raises(asyncio.CancelledError) as caught:
        presentation.atomic_write(tmp_path / 'view.json', 'new')
    assert caught.value is cancellation
    assert 'publication=unconfirmed' in ' '.join(cancellation.__notes__)
    assert model.published is (operation == 'rename_after')
    assert model.events == ['open', 'write', 'flush', 'rename', 'close']
    assert not model.removed


def test_presentation_close_failure_reports_completed_publication(windows_model, tmp_path):
    model = windows_model
    model.failures['close'] = 6
    with pytest.raises(OSError) as caught:
        presentation.atomic_write(tmp_path / 'view.json', 'new')
    assert caught.value is model.errors[6]
    assert 'publication=confirmed' in ' '.join(caught.value.__notes__)
    assert model.published and not model.removed and model.events.count('close') == 1


def test_presentation_remove_exact_leaf_posix_disposition(windows_model, tmp_path):
    model = windows_model
    path = tmp_path / 'ordinary-link.json'
    presentation.remove(path)
    assert model.opens == [(str(path.absolute()), 0x10000, 7, None, 3, 0x200000, None)]
    assert model.events == ['open', 'remove', 'close']
    assert model.removed and model.api.removal == 'confirmed'
    # No BACKUP_SEMANTICS directory opening; no IGNORE_READONLY_ATTRIBUTE.


@pytest.mark.parametrize('missing_ok', [True, False])
@pytest.mark.parametrize('code', [2, 3, 5, 32])
def test_presentation_remove_missing_only_at_open(windows_model, tmp_path, missing_ok, code):
    model = windows_model
    model.failures['open'] = code
    if missing_ok and code in {2, 3}:
        assert presentation.remove(tmp_path / 'absent', missing_ok=missing_ok) is None
    else:
        with pytest.raises(OSError) as caught:
            presentation.remove(tmp_path / 'absent', missing_ok=missing_ok)
        assert caught.value is model.errors[code]
    assert model.events == ['open']


@pytest.mark.parametrize('code', [2, 5, 32, 50, 87, 120])
def test_presentation_remove_refusal_closes_without_fallback(windows_model, tmp_path, code):
    model = windows_model
    model.failures['remove'] = code
    with pytest.raises(OSError) as caught:
        presentation.remove(tmp_path / 'view.json', missing_ok=True)
    assert caught.value is model.errors[code]
    assert model.events == ['open', 'remove', 'close'] and not model.removed
    if code in {50, 87, 120}:
        assert 'No fallback' in ' '.join(caught.value.__notes__)


def test_presentation_remove_wrong_type_is_not_missing(windows_model, tmp_path):
    model = windows_model
    error = IsADirectoryError('directory')
    model.failures['open'] = error
    with pytest.raises(IsADirectoryError) as caught:
        presentation.remove(tmp_path / 'directory')
    assert caught.value is error and model.events == ['open']


def test_presentation_remove_cancel_preserves_uncertain_effect_and_close_failure(windows_model, tmp_path):
    model = windows_model
    cancellation = asyncio.CancelledError()
    model.failures.update(remove_after=cancellation, close=6)
    with pytest.raises(asyncio.CancelledError) as caught:
        presentation.remove(tmp_path / 'view.json')
    assert caught.value is cancellation
    notes = ' '.join(cancellation.__notes__)
    assert 'removal=unconfirmed' in notes and 'handle close unconfirmed' in notes
    assert model.removed and model.events == ['open', 'remove', 'close']


def test_presentation_remove_close_failure_reports_completed_removal(windows_model, tmp_path):
    model = windows_model
    model.failures['close'] = 6
    with pytest.raises(OSError) as caught:
        presentation.remove(tmp_path / 'view.json')
    assert caught.value is model.errors[6]
    assert 'removal=confirmed' in ' '.join(caught.value.__notes__)
    assert model.removed and model.events == ['open', 'remove', 'close']


@pytest.mark.parametrize('operation', ['write', 'remove'])
def test_presentation_missing_native_primitive_refuses_before_open(windows_model, tmp_path, operation):
    import errno
    del windows_model.kernel.SetFileInformationByHandle
    with pytest.raises(OSError, match='no fallback') as caught:
        if operation == 'write':
            presentation.atomic_write(tmp_path / 'view.json', 'new')
        else:
            presentation.remove(tmp_path / 'view.json')
    assert caught.value.errno == errno.ENOSYS
    assert isinstance(caught.value.__cause__, AttributeError)
    assert not windows_model.events


def test_presentation_utf16_rename_path_no_private_path_filter(windows_model):
    api = presentation._WindowsAPI()
    destination = PureWindowsPath(r'\\server\share\ordinary parent') / 'leaf-\u96ea-\U00010348.json'
    api.replace(windows_model.handle, destination)
    assert windows_model.destination == str(destination)
    assert windows_model.events == ['rename']


def test_presentation_input_errors_precede_file_creation(windows_model, tmp_path):
    with pytest.raises(ValueError, match='embedded null'):
        presentation.atomic_write('bad\0name', 'new')
    with pytest.raises(ValueError, match='embedded null'):
        presentation.remove('bad\0name')
    with pytest.raises(UnicodeEncodeError):
        presentation.atomic_write(tmp_path / 'view.json', '\ud800')
    assert not windows_model.events and windows_model.counter == 0


@pytest.mark.skipif(os.name == 'nt', reason='actual POSIX delegation, not Windows proof')
def test_presentation_posix_delegates_existing_atomic_writer(tmp_path, monkeypatch):
    from amplifier_web.host.storage import SessionStore
    real = SessionStore._atomic
    calls = []
    def observed(path, text):
        calls.append((path, text))
        return real(path, text)
    monkeypatch.setattr(SessionStore, '_atomic', staticmethod(observed))
    path = tmp_path / 'view.json'
    presentation.atomic_write(path, 'old')
    with path.open('r') as held:
        presentation.atomic_write(path, 'new \u96ea\n')
        assert held.read() == 'old'
    assert path.read_bytes() == 'new \u96ea\n'.encode('utf-8')
    assert path.stat().st_mode & 0o777 == 0o600
    assert calls == [(path, 'old'), (path, 'new \u96ea\n')]
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name == 'nt', reason='actual POSIX ordinary links/rollback')
def test_presentation_posix_parent_links_leaf_replacement_and_removal(tmp_path):
    directory = tmp_path / 'real'
    directory.mkdir()
    parent = tmp_path / 'parent-link'
    parent.symlink_to(directory, target_is_directory=True)
    other = tmp_path / 'other'
    other.write_text('keep')
    leaf = parent / 'view.json'
    leaf.symlink_to(other)
    presentation.atomic_write(leaf, 'presentation')
    assert not leaf.is_symlink() and leaf.read_text() == 'presentation'
    assert other.read_text() == 'keep'
    with leaf.open('r') as held:
        presentation.remove(leaf)
        assert not leaf.exists() and held.read() == 'presentation'
    leaf.symlink_to(other)
    presentation.remove(leaf)
    assert not leaf.is_symlink() and other.read_text() == 'keep'
    presentation.remove(leaf)
    with pytest.raises(FileNotFoundError):
        presentation.remove(leaf, missing_ok=False)
    with pytest.raises(IsADirectoryError):
        presentation.remove(directory)


@pytest.mark.skipif(os.name == 'nt', reason='actual POSIX failure preservation')
def test_presentation_posix_failures_do_not_create_parents_or_remove_directory(tmp_path):
    missing = tmp_path / 'missing' / 'view.json'
    with pytest.raises(FileNotFoundError):
        presentation.atomic_write(missing, 'new')
    assert not missing.parent.exists()
    target = tmp_path / 'directory'
    target.mkdir()
    with pytest.raises(OSError):
        presentation.atomic_write(target, 'new')
    assert target.is_dir()
    assert list(tmp_path.iterdir()) == [target]