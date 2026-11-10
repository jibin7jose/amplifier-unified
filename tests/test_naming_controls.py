import asyncio
import ctypes as C
from copy import deepcopy
import io
import json
import os
from pathlib import PureWindowsPath
import sys
from types import SimpleNamespace

import pytest
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_web.host.storage import SessionStore
from amplifier_web.naming import accept_generated, automatic_metadata, directory_for, read
from amplifier_web.service import AppError, AppService

@pytest.fixture(autouse=True)
def lifecycle_owned_by_host(monkeypatch):
    monkeypatch.setattr('amplifier_web.host.naming.claim_root_lifecycle', lambda _: None)


class NamingRuntime:
    def __init__(self, home):
        self.home = home
        self.started = self.calls = 0
        self.release = asyncio.Event()
        self.ready = asyncio.Event()

    async def start(self, source, emit, *, preserve_emit=False):
        self.started += 1
        self.source = source
        await emit('runtime.status', {'sessionId': source['id'], 'status': 'starting'})

    async def control(self, identity, operation, arguments):
        assert operation == 'session.naming'
        self.calls += 1
        snapshot = read(directory_for(self.home, self.source))
        self.ready.set()
        await self.release.wait()
        return {**snapshot, 'name': 'A regenerated title', 'description': 'Current conversation'}

    async def close(self): pass


@pytest.fixture
async def named(tmp_path):
    runtime = NamingRuntime(tmp_path)
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {'title': 'Chosen title'})
    session = app._session()
    app._message(session, 'user', 'Some conversation context')
    SessionStore.for_app(tmp_path, tmp_path).save(session['id'], [{'role': 'user', 'content': 'Some conversation context'}], {})
    yield app, runtime, session
    runtime.release.set()
    await app.close()


async def settled(app, session):
    for _ in range(200):
        if session.get('naming', {}).get('status') != 'working': return
        await asyncio.sleep(.01)
    raise AssertionError('Naming did not settle')


async def test_auto_toggle_is_durable_without_model_call(named, tmp_path):
    app, runtime, session = named
    original = deepcopy(session['messages'])
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    assert session['autoName'] is True
    assert automatic_metadata(read(directory_for(app.data_dir, session)))
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': False})
    assert not session['autoName']
    assert runtime.started == runtime.calls == 0
    assert session['messages'] == original
    await app.close()
    restored = AppService(tmp_path, workspace=tmp_path)
    try: assert restored._session()['autoName'] is False
    finally: await restored.close()


@pytest.mark.parametrize('enabled', [False, True])
async def test_explicit_regeneration_preserves_policy_history_and_status(named, enabled):
    app, runtime, session = named
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': enabled})
    session.update(status='error', error='An earlier failed turn')
    before = deepcopy(session['messages'])
    args = {'id': session['id'], 'regenerate': True}
    await app.dispatch('session.naming', args, command_id='once')
    duplicate = await app.dispatch('session.naming', args, command_id='once')
    assert duplicate['duplicate']
    await runtime.ready.wait()
    with pytest.raises(AppError, match='already'):
        await app.dispatch('session.naming', args, command_id='duplicate-click')
    runtime.release.set()
    await settled(app, session)
    assert session['title'] == 'A regenerated title'
    assert session['autoName'] is enabled
    assert session['status'] == 'error' and session['error'] == 'An earlier failed turn'
    assert session['messages'] == before
    assert runtime.calls == 1


@pytest.mark.parametrize('newer', ['rename', 'policy', 'external'])
async def test_newer_edit_wins_over_delayed_regeneration(named, newer):
    app, runtime, session = named
    await app.dispatch('session.naming', {'id': session['id'], 'regenerate': True})
    await runtime.ready.wait()
    if newer == 'rename':
        await app.dispatch('session.rename', {'id': session['id'], 'title': 'Keep my newer name'})
    elif newer == 'external':
        SessionMetadataStore(directory_for(app.data_dir, session)).set_name('Keep my newer name')
    else:
        await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    runtime.release.set()
    await settled(app, session)
    assert session['naming']['status'] == 'conflict'
    assert session['title'] == ('Chosen title' if newer == 'policy' else 'Keep my newer name')


async def test_background_result_respects_auto_policy_and_newer_cli_name(named):
    app, runtime, session = named
    directory = directory_for(app.data_dir, session)
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    before = read(directory)
    metadata, accepted = accept_generated(directory, {**before, 'name': 'Generated'})
    assert accepted and metadata['name'] == 'Generated'
    SessionMetadataStore(directory).set_name('A CLI name')
    assert not automatic_metadata(read(directory))
    _, accepted = accept_generated(directory, {**read(directory), 'name': 'Unwanted'})
    assert not accepted and read(directory)['name'] == 'A CLI name'


async def test_explicit_suggestion_requests_a_new_name_without_writing(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from amplifier_web.host.naming import LiveSessionNaming
    calls = []
    class Config:
        initial_trigger_turn = 2
        update_interval_turns = 5
        def __init__(self, **kwargs): pass
    class Hook:
        def __init__(self, coordinator, config): self.config = config
        async def _generate_name(self, sid, directory, is_update):
            # Upstream update mode only updates the description. Refresh must
            # request initial naming even when there is an existing title.
            calls.append(is_update)
            self._save_metadata(directory, {**self._load_metadata(directory), 'name': 'New suggestion'})
    monkeypatch.setitem(sys.modules, 'amplifier_module_hooks_session_naming',
        SimpleNamespace(SessionNamingHook=Hook, SessionNamingConfig=Config))
    coordinator = SimpleNamespace(session_id='chat', config={'project_dir':str(tmp_path), 'hooks':[{'module':'hooks-session-naming'}]},
        hooks=SimpleNamespace(register=lambda *args, **kwargs: None), register_cleanup=lambda *args: None)
    naming = LiveSessionNaming(coordinator, tmp_path, lambda event: None)
    naming.store.save('chat', [{'role':'user','content':'Context'}], {'name':'Manual original', 'name_source':'manual'})
    before = read(naming.directory)
    result = await naming.suggest()
    assert result['name'] == 'New suggestion'
    assert read(naming.directory) == before
    assert calls == [False]


async def test_same_manual_name_still_disables_auto(named):
    app, runtime, session = named
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    await app.dispatch('session.rename', {'id': session['id'], 'title': session['title']})
    assert not session['autoName']
    assert not automatic_metadata(read(directory_for(app.data_dir, session)))


async def test_checkpoint_cannot_restore_an_old_policy(named):
    app, runtime, session = named
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    rows, stale = store.load(session['id'])
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': False})
    store.save(session['id'], rows, stale)
    assert not automatic_metadata(read(directory_for(app.data_dir, session)))
    store.save(session['id'], rows + [{'role':'assistant','content':'A later checkpoint'}], stale)
    assert not automatic_metadata(read(directory_for(app.data_dir, session)))


async def test_auto_preference_before_first_turn_survives_native_save(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title':'Named before the first message'})
        session = app._session()
        await app.dispatch('session.naming', {'id':session['id'], 'automatic':True})
        store = SessionStore.for_app(tmp_path, tmp_path)
        store.save(session['id'], [{'role':'user','content':'The first message'}], {})
        assert automatic_metadata(read(directory_for(tmp_path, session)))
    finally:
        await app.close()


async def test_edit_while_runtime_prepares_wins_over_regeneration(named):
    app, runtime, session = named
    preparing, prepared = asyncio.Event(), asyncio.Event()
    original = runtime.start
    async def delayed(source, emit, **kwargs):
        preparing.set()
        await prepared.wait()
        await original(source, emit, **kwargs)
    runtime.start = delayed
    await app.dispatch('session.naming', {'id':session['id'], 'regenerate':True})
    await preparing.wait()
    await app.dispatch('session.rename', {'id':session['id'], 'title':'Chosen while preparing'})
    prepared.set()
    runtime.release.set()
    await settled(app, session)
    assert session['title'] == 'Chosen while preparing'
    assert session['naming']['status'] == 'conflict'


async def test_sidebar_projection_refreshes_naming_policy_and_progress(named):
    app, runtime, session = named
    from amplifier_web.state_projections import StateProjections
    projections = StateProjections()
    def row():
        return next(item for item in projections.chats(app.state)['items'] if item['id'] == session['id'])
    def saved():
        projections.shell_key(app.state)
        projections.invalidate()
    assert row()['titleSource'] == 'manual'
    saved()
    await app.dispatch('session.naming', {'id':session['id'], 'automatic':False})
    assert row()['autoName'] is False
    saved()
    await app.dispatch('session.naming', {'id':session['id'], 'regenerate':True})
    assert row()['naming'] == {'status':'working'}
    await runtime.ready.wait()
    saved()
    runtime.release.set()
    await settled(app, session)
    assert row()['naming']['status'] == 'ready'
    assert row()['autoName'] is False


@pytest.mark.parametrize('status', ['starting', 'working', 'running', 'stopping'])
async def test_manual_auto_name_during_work_preserves_foreground_and_newer_edit(named, status):
    app, runtime, session = named
    session['status'] = status
    before = deepcopy(session['messages'])
    await app.dispatch('session.naming', {'id': session['id'], 'regenerate': True})
    await runtime.ready.wait()
    assert session['status'] == status
    await app.dispatch('session.rename', {'id': session['id'], 'title': 'My title while working'})
    runtime.release.set()
    await settled(app, session)
    assert session['status'] == status and session['messages'] == before
    assert session['title'] == 'My title while working'
    assert session['naming']['status'] == 'conflict'


async def test_failed_suggestion_explains_the_naming_hooks_last_warning(monkeypatch, tmp_path):
    import logging
    import sys
    from types import SimpleNamespace
    from amplifier_web.host.naming import LiveSessionNaming
    class Config:
        initial_trigger_turn = 1
        update_interval_turns = 5
        def __init__(self, **kwargs): pass
    class Hook:
        def __init__(self, coordinator, config): self.config = config
        async def _generate_name(self, sid, directory, is_update):
            log = logging.getLogger('amplifier_module_hooks_session_naming')
            log.warning('First problem')
            log.warning('Naming provider rejected Bearer abcdefghijklmnop: model not found')
    monkeypatch.setitem(sys.modules, 'amplifier_module_hooks_session_naming',
        SimpleNamespace(SessionNamingHook=Hook, SessionNamingConfig=Config))
    coordinator = SimpleNamespace(session_id='chat', config={'project_dir': str(tmp_path), 'hooks': [{'module': 'hooks-session-naming'}]},
        hooks=SimpleNamespace(register=lambda *args, **kwargs: None), register_cleanup=lambda *args: None)
    naming = LiveSessionNaming(coordinator, tmp_path, lambda event: None)
    naming.store.save('chat', [{'role': 'user', 'content': 'Context'}], {})
    with pytest.raises(ValueError) as error:
        await naming.suggest()
    message = str(error.value)
    assert message.startswith('No new name was returned.')
    assert 'model not found' in message and 'First problem' not in message
    assert 'abcdefghijklmnop' not in message and '[REDACTED]' in message
    assert not logging.getLogger('amplifier_module_hooks_session_naming').handlers


async def test_first_naming_initialization_is_not_a_concurrent_edit(named):
    """An imported chat can have history/view but no native naming fields yet."""
    import json
    app, runtime, session = named
    directory = directory_for(app.data_dir, session)
    metadata = SessionMetadataStore(directory)
    raw = metadata.read()
    for key in list(raw):
        if key.startswith('name'):
            del raw[key]
    metadata.history._save_metadata_unlocked(raw)
    view = directory / 'unified' / 'view.json'
    view.parent.mkdir(exist_ok=True)
    view.write_text(json.dumps({**session, 'autoName': True}))
    session['autoName'] = True
    original_start = runtime.start
    async def prepare(source, emit, **kwargs):
        await original_start(source, emit, **kwargs)
        SessionStore._initial_naming_policy(directory, True)
        SessionStore.for_app(app.data_dir, session['workspace']).save(
            session['id'], [{'role': 'user', 'content': 'Some conversation context'}], {})
    runtime.start = prepare
    await app.dispatch('session.naming', {'id': session['id'], 'regenerate': True})
    await runtime.ready.wait()
    runtime.release.set()
    await settled(app, session)
    assert session['naming']['status'] == 'ready'
    assert session['title'] == 'A regenerated title'
    assert session['autoName'] is True


@pytest.fixture
def presentation_windows_model(tmp_path, monkeypatch):
    """Portable ABI/ownership model, not native sharing or ACL evidence."""
    from amplifier_web import naming

    payload = tmp_path / 'presentation.txt'
    payload.write_bytes(b'first\r\nsecond\rlast\n\x1a')
    model = SimpleNamespace(stage=None, error=PermissionError(13, 'fixture'),
                            calls=[], descriptors=[], closed=[], handles=[],
                            pending=set(), streams=[], wrapper_cleanup=[],
                            after_consume=None, handle=0x100000007)

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    def create(*args):
        model.calls.append(('create', args))
        return C.c_void_p(-1).value if model.stage == 'open' else model.handle

    def close_handle(handle):
        model.handles.append(handle)
        return 1

    kernel = SimpleNamespace(CreateFileW=Function(create), CloseHandle=Function(close_handle))
    model.kernel = kernel

    def load(name, *, use_last_error):
        model.calls.append(('load', (name, use_last_error)))
        return kernel

    def win_error(code):
        model.calls.append(('error', code))
        return model.error

    def transfer(handle, flags):
        model.calls.append(('transfer', (handle, flags)))
        if model.stage == 'transfer':
            raise model.error
        fd = os.open(payload, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        model.descriptors.append(fd)
        model.pending.add(fd)
        return fd

    def file_io(fd, *args, **kwargs):
        model.calls.append(('raw', (fd, args, kwargs)))
        if model.stage == 'raw':
            raise model.error
        stream = io.FileIO(fd, *args, **kwargs)
        model.streams.append(stream)
        if model.stage == 'raw_consumed':
            stream.close()
            raise model.error
        return stream

    def buffered_reader(raw):
        model.calls.append(('buffered', ()))
        stream = io.BufferedReader(raw)
        model.streams.append(stream)
        if model.stage == 'buffered':
            stream.close()  # A failed constructor can consume/close its input.
            raise model.error
        return stream

    def text_wrapper(buffered):
        model.calls.append(('text', ()))
        stream = io.TextIOWrapper(buffered)
        model.streams.append(stream)
        if model.stage == 'text':
            stream.close()  # Real wrapper close, not a synthetic fdopen failure.
            if model.after_consume is not None:
                model.after_consume()
            raise model.error
        return stream

    def close_fd(fd):
        model.closed.append(fd)
        model.wrapper_cleanup.append(all(stream.closed for stream in model.streams))
        os.close(fd)
        model.pending.remove(fd)

    monkeypatch.setattr(C, 'WinDLL', load, raising=False)
    monkeypatch.setattr(C, 'get_last_error', lambda: 32, raising=False)
    monkeypatch.setattr(C, 'WinError', win_error, raising=False)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(open_osfhandle=transfer))
    # Do not change os.name globally or let pathlib select the wrong platform.
    monkeypatch.setattr(naming, 'os', SimpleNamespace(
        name='nt', O_RDONLY=0, O_BINARY=0x8000, O_NOINHERIT=0x80,
        close=close_fd))
    monkeypatch.setattr(naming, 'io', SimpleNamespace(
        FileIO=file_io, BufferedReader=buffered_reader, TextIOWrapper=text_wrapper))
    yield model
    # Clean only still-owned resources, never a closed/reused descriptor number.
    for stream in model.streams:
        if not stream.closed:
            stream.close()
    for fd in model.pending:
        os.close(fd)


@pytest.mark.parametrize('path', [
    PureWindowsPath(r'..\ordinary folder\view.json'),
    PureWindowsPath(r'C:\ordinary\symlink\naming.json'),
    PureWindowsPath(r'C:relative\view.json'),
])
def test_presentation_windows_binding_and_stream_ownership(presentation_windows_model, path):
    from amplifier_web import naming

    model = presentation_windows_model
    with naming._open_presentation(path) as stream:
        fd = stream.fileno()
        assert stream.read() == 'first\nsecond\nlast\n\x1a'
        assert stream.readable() and not stream.writable()
        assert not os.get_inheritable(fd)
    assert stream.closed and not model.handles and model.closed == [fd]
    assert model.wrapper_cleanup == [True] and not model.pending
    with pytest.raises(OSError):
        os.fstat(fd)
    assert model.calls == [
        ('load', ('kernel32', True)),
        ('create', (str(path), 0x80000000, 7, None, 3, 0x80, None)),
        ('transfer', (model.handle, 0x8080)),
        ('raw', (fd, ('r',), {'closefd': False})),
        ('buffered', ()),
        ('text', ()),
    ]
    # NULL security attributes and O_NOINHERIT cover native and CRT ownership.
    # No OPEN_REPARSE_POINT, normalization, ACL repair or private-reader fallback.
    assert model.kernel.CreateFileW.restype is C.c_void_p
    assert model.kernel.CreateFileW.argtypes == [
        C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p,
        C.c_uint32, C.c_uint32, C.c_void_p,
    ]
    assert C.sizeof(C.c_uint32) == C.sizeof(C.c_int32) == 4
    assert model.kernel.CloseHandle.restype is C.c_int32
    assert model.kernel.CloseHandle.argtypes == [C.c_void_p]


@pytest.mark.parametrize('stage', ['open', 'transfer', 'raw', 'raw_consumed', 'buffered', 'text', 'body'])
@pytest.mark.parametrize('error_type', [PermissionError, FileNotFoundError, OSError])
def test_presentation_windows_errors_preserve_owner_and_exception(
        presentation_windows_model, stage, error_type):
    from amplifier_web import naming

    model = presentation_windows_model
    model.stage, model.error = stage, error_type(13, 'fixture')
    model.error.winerror = 32
    with pytest.raises(error_type) as caught:
        with naming._open_presentation(PureWindowsPath('view.json')):
            assert stage == 'body'
            raise model.error
    assert caught.value is model.error
    assert (caught.value.errno, caught.value.winerror) == (13, 32)
    assert model.handles == ([model.handle] if stage == 'transfer' else [])
    assert model.closed == model.descriptors
    assert len(model.descriptors) == (0 if stage in {'open', 'transfer'} else 1)
    assert model.wrapper_cleanup == [True] * len(model.descriptors)
    assert all(stream.closed for stream in model.streams) and not model.pending
    assert sum(name == 'create' for name, _ in model.calls) == 1  # no retry
    for fd in model.descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_presentation_consumed_wrapper_failure_cannot_close_reused_descriptor(
        presentation_windows_model, tmp_path):
    """A real closed wrapper never owns fd; later fd reuse cannot be closed by it."""
    from amplifier_web import naming

    model = presentation_windows_model
    unrelated = tmp_path / 'unrelated.txt'
    unrelated.write_bytes(b'unrelated retained file')
    other = reused = None
    live_after_consume = []

    def after_consume():
        nonlocal other
        original = model.descriptors[0]
        try:
            os.fstat(original)
        except OSError:
            live_after_consume.append(False)
        else:
            live_after_consume.append(True)
        # If a wrapper consumed the descriptor, this open could reuse its number
        # and the helper's cleanup would close an unrelated file. Do not mock IO.
        other = os.open(unrelated, os.O_RDONLY | getattr(os, 'O_BINARY', 0))

    model.stage, model.after_consume = 'text', after_consume
    try:
        with pytest.raises(PermissionError) as caught:
            with naming._open_presentation(PureWindowsPath('view.json')):
                pytest.fail('Failed text constructor yielded a stream')
        assert caught.value is model.error
        original = model.descriptors[0]
        assert live_after_consume == [True]
        assert other is not None and other != original
        assert model.closed == [original] and model.wrapper_cleanup == [True]
        with pytest.raises(OSError):
            os.fstat(original)
        assert os.read(other, 64) == b'unrelated retained file'
        # Deterministic reuse AFTER the context's single close. Retained partial
        # wrappers (including in exception tracebacks) cannot close the new owner.
        os.lseek(other, 0, os.SEEK_SET)
        os.dup2(other, original)
        reused = original
        for stream in reversed(model.streams):
            stream.close()
        assert os.read(reused, 64) == b'unrelated retained file'
        os.fstat(other)
        assert model.closed == [original] and not model.pending
    finally:
        if reused is not None:
            os.close(reused)
        if other is not None:
            os.close(other)


def test_presentation_early_stream_close_keeps_descriptor_until_context_exit(presentation_windows_model):
    from amplifier_web import naming

    model = presentation_windows_model
    with naming._open_presentation(PureWindowsPath('view.json')) as stream:
        fd = stream.fileno()
        stream.close()
        assert all(wrapper.closed for wrapper in model.streams)
        os.fstat(fd)
        assert not model.closed
    assert model.closed == [fd] and model.wrapper_cleanup == [True]
    with pytest.raises(OSError):
        os.fstat(fd)


def test_presentation_windows_embedded_null_refuses_before_native_open(presentation_windows_model):
    from amplifier_web import naming

    with pytest.raises(ValueError, match='embedded null'):
        with naming._open_presentation(PureWindowsPath('view\0.json')):
            pytest.fail('Invalid path yielded a stream')
    assert not presentation_windows_model.calls


def test_presentation_posix_keeps_ordinary_path_and_text_semantics(tmp_path, monkeypatch):
    from amplifier_web import naming

    path = tmp_path / 'presentation.txt'
    path.write_bytes(b'first\r\nsecond\rlast\n\x1a')
    expected = path.read_text()
    monkeypatch.setattr(naming, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(C, 'WinDLL', lambda *a, **k: pytest.fail('native call'), raising=False)
    with naming._open_presentation(path) as stream:
        assert stream.read() == expected
        assert not os.get_inheritable(stream.fileno())
    assert stream.closed
    if os.name != 'nt':
        link = tmp_path / 'presentation-link'
        link.symlink_to(path)
        with naming._open_presentation(link) as stream:
            assert stream.read() == link.read_text() == expected
    with pytest.raises(FileNotFoundError):
        with naming._open_presentation(tmp_path / 'absent'):
            pytest.fail('Missing path yielded a stream')
    assert not (tmp_path / 'absent').exists()


@pytest.mark.parametrize('legacy_text,view_text', [
    ('{"name":"Legacy","name_source":"manual","ignored":true}', '{"title":"View"}'),
    ('{}', '{"title":"View","titleSource":"generated","autoName":true}'),
    ('[]', '{"title":"Fallback","titleSource":"unknown"}'),
    ('{broken', 'null'),
    ('null', '{broken'),
])
def test_naming_presentation_sources_preserve_fields_and_fallback(
        tmp_path, monkeypatch, legacy_text, view_text):
    from amplifier_web import naming

    paths, streams = [], []
    def open_presentation(path):
        paths.append(path)
        stream = io.StringIO(legacy_text if path.name == 'naming.json' else view_text)
        streams.append(stream)
        return stream

    def forbidden(*args, **kwargs):
        pytest.fail('Presentation reads must not access native metadata or execution')

    monkeypatch.setattr(naming, '_open_presentation', open_presentation)
    monkeypatch.setattr(naming, 'SessionMetadataStore', forbidden)
    def decoded(text):
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    legacy = {key: value for key, value in decoded(legacy_text).items() if key in naming.FIELDS}
    view = decoded(view_text)
    source = legacy.get('name_source') or view.get('titleSource')
    assert naming.initial_name(tmp_path) == (
        legacy.get('name') or view.get('title'),
        source if source in {'manual', 'generated'} else 'fallback', legacy, view)
    assert paths == [tmp_path / 'naming.json', tmp_path / 'unified' / 'view.json']
    assert all(stream.closed for stream in streams)
    paths.clear()
    supplied = {'title': 'Already loaded'}
    assert naming.initial_name(tmp_path, supplied)[3] is supplied
    assert paths == [tmp_path / 'naming.json']  # no second view read
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize('stage', ['open', 'read'])
@pytest.mark.parametrize('error', [
    PermissionError(13, 'fixture'), FileNotFoundError(2, 'fixture'),
    ValueError('fixture'), UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'fixture'),
])
def test_naming_presentation_io_fallback_closes_streams(tmp_path, monkeypatch, stage, error):
    from amplifier_web import naming

    streams = []
    class BrokenRead(io.StringIO):
        def read(self, *args):
            raise error
    def open_presentation(path):
        if stage == 'open':
            raise error
        stream = BrokenRead()
        streams.append(stream)
        return stream
    monkeypatch.setattr(naming, '_open_presentation', open_presentation)
    assert naming.legacy(tmp_path) == {}
    assert naming.initial_name(tmp_path) == (None, 'fallback', {}, {})
    assert all(stream.closed for stream in streams)
