import asyncio
import json
import sys

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.worker_diagnostics import save_startup_failure


@pytest.mark.asyncio
async def test_startup_stderr_survives_cleanup_without_becoming_public(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    events = []
    async def emit(kind, data):
        events.append((kind, data))
    fixture = "import sys; sys.stdin.readline(); print('Dependency setup failed: secret-fixture-value', file=sys.stderr, flush=True); raise SystemExit(1)"
    manager = RuntimeManager(command=[sys.executable, '-c', fixture])
    try:
        with pytest.raises(RuntimeError, match='Startup details were saved locally') as error:
            await manager.start({'id': 'probe'}, emit)
        assert not manager.workers
        files = list((tmp_path / 'logs/workers').glob('startup-*.log'))
        assert len(files) == 1
        assert 'Dependency setup failed' in files[0].read_text()
        assert files[0].stat().st_mode & 0o777 == 0o600
        assert files[0].parent.stat().st_mode & 0o777 == 0o700
        assert str(files[0]) in str(error.value)
        assert error.value.diagnostic_path == files[0]
        assert next(data for kind, data in events if kind == 'runtime.error')['diagnosticReceipt'] == files[0].name
        assert 'secret-fixture-value' not in json.dumps(events)
        assert 'secret-fixture-value' not in str(error.value)
        assert not any(kind == 'assistant.message' for kind, _ in events)
    finally:
        await manager.close()


def test_diagnostic_is_bounded_and_optional(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    assert save_startup_failure({'stderr': []}, 1) is None
    path = save_startup_failure({'stderr': ['x' * 100_000]}, 1)
    assert path.stat().st_size < 61_000
    def fail(*_, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr('amplifier_web.host.config.write_private', fail)
    assert save_startup_failure({'stderr': ['failed']}, 1) is None


def handled_worker(*, stderr='', detail='private startup detail', receipt=None, module=True):
    event = {'type': 'runtime.error', 'error': detail}
    if module:
        event.update(code='module_load_failed', moduleFailures=[{
            'module': 'tool-fixture', 'type': 'tool', 'reason_code': 'validation_failed'}])
    if receipt:
        event['diagnosticReceipt'] = receipt
    return ('import sys,json\njson.loads(sys.stdin.readline())\n'
            f'print({stderr!r}, file=sys.stderr, flush=True)\n'
            f'print({json.dumps(event)!r}, flush=True)\n')


@pytest.mark.asyncio
@pytest.mark.parametrize('has_stderr', [True, False])
async def test_handled_module_error_keeps_private_receipt_even_without_stderr(tmp_path, monkeypatch, has_stderr):
    from amplifier_web.module_failures import ConfiguredModuleError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    monkeypatch.setenv('API_TOKEN', 'synthetic-inherited-secret')
    private = ('protocol_compliance: BackendError: backend handshake failed '
               'api_key="synthetic-config-secret" Authorization: Bearer synthetic-bearer-secret '
               'https://name:synthetic-url-secret@example.invalid API_TOKEN=synthetic-inherited-secret')
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(
        stderr=private if has_stderr else '', detail=private)], startup_timeout=3)
    events = []
    async def emit(kind, data):
        events.append((kind, data))
    try:
        with pytest.raises(ConfiguredModuleError) as error:
            await manager.start({'id': 'handled-fixture'}, emit)
        path = error.value.diagnostic_path
        assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
        content = path.read_text()
        assert 'protocol_compliance' in content and 'BackendError' in content
        assert 'handled-fixture' in content
        for secret in ('synthetic-config-secret', 'synthetic-bearer-secret',
                       'synthetic-url-secret', 'synthetic-inherited-secret'):
            assert secret not in content
            assert secret not in json.dumps(events) + str(error.value)
        payload = next(data for kind, data in events if kind == 'runtime.error')
        assert payload['diagnosticReceipt'] == path.name
        assert 'backend handshake failed' not in json.dumps(events)
        assert len(list(path.parent.glob('startup-*.log'))) == 1
        assert not manager.workers
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_receipt_write_failure_keeps_original_module_failure(tmp_path, monkeypatch):
    from amplifier_web.module_failures import ConfiguredModuleError
    from amplifier_web.host.config import write_private
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    failed_receipts = []
    def fail(path, *args, **kwargs):
        # Fail diagnostic publication only. Generation leases must still be
        # writable so the intended handled worker failure can actually occur.
        if path.parent == tmp_path / 'logs/workers':
            failed_receipts.append(path)
            raise OSError('synthetic disk error')
        return write_private(path, *args, **kwargs)
    monkeypatch.setattr('amplifier_web.host.config.write_private', fail)
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(stderr='failed check')])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        with pytest.raises(ConfiguredModuleError, match='tool-fixture') as error:
            await manager.start({'id': 'disk-failure'}, emit)
        assert not hasattr(error.value, 'diagnostic_path')
        assert failed_receipts
        assert 'diagnosticReceipt' not in next(data for kind, data in events if kind == 'runtime.error')
        assert 'synthetic disk error' not in json.dumps(events)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_generic_startup_exception_is_private_and_bad_receipt_is_not_followed(tmp_path, monkeypatch):
    from amplifier_web.runtime import RuntimeStartupError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(
        detail='UnexpectedMountError: private implementation detail', receipt='../../other.log', module=False)])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        with pytest.raises(RuntimeStartupError) as error:
            await manager.start({'id': 'generic-failure'}, emit)
        path = error.value.diagnostic_path
        assert path.parent == tmp_path / 'logs/workers'
        assert 'UnexpectedMountError' in path.read_text()
        assert 'private implementation detail' not in json.dumps(events) + str(error.value)
    finally:
        await manager.close()


def test_receipt_redaction_and_bound_survive_large_and_unprintable_errors(tmp_path, monkeypatch):
    from amplifier_web.worker_diagnostics import StartupCapture, MAX_DETAIL_BYTES
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    path = save_startup_failure({'stderr': ['x' * 100_000]}, None, failure='\x01' * 100_000)
    assert path.stat().st_size <= MAX_DETAIL_BYTES
    class BadError(Exception):
        def __str__(self): raise ValueError('Cannot format exception')
    capture = StartupCapture()
    try:
        assert capture.save(BadError(), 'bad-string') is None
        monkeypatch.setattr('amplifier_web.worker_diagnostics.traceback.extract_tb', lambda _: 1 / 0)
        assert capture.save(RuntimeError('original'), 'bad-traceback') is None
    finally:
        capture.close()


@pytest.mark.asyncio
async def test_worker_captures_original_validation_error_before_safe_metadata_replaces_it(tmp_path, monkeypatch):
    import logging
    from types import SimpleNamespace
    from amplifier_web.module_failures import ConfiguredModuleError
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.shared_state import ActivationGate
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    monkeypatch.setenv('SYNTHETIC_TOKEN', 'synthetic-worker-secret')
    monkeypatch.setattr('amplifier_web.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_web.host.config.app_home', lambda: tmp_path / 'app')
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides', lambda *args: None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', lambda *args: None)
    runtime = SimpleNamespace(session_id='captured-fixture')
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime', SimpleNamespace(Runtime=lambda **kwargs: runtime))
    logger = logging.getLogger('amplifier_core.loader')
    original_handlers = list(logging.getLogger('amplifier_core').handlers)
    async def fail_prepare(*args, **kwargs):
        logger.error("Module 'tool-fixture' failed validation: protocol_compliance: BackendError: synthetic-worker-secret")
        raise ConfiguredModuleError([{'module': 'tool-fixture', 'type': 'tool', 'reason_code': 'validation_failed'}])
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', fail_prepare)
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker = Worker()
    worker.shared_store = SimpleNamespace()
    worker.shared_handle = SimpleNamespace()
    worker.activation_gate = ActivationGate()
    await worker.start({'id': 'captured-fixture', 'workspace': str(tmp_path)}, recover_bundle=False)
    error = next(event for event in events if event['type'] == 'runtime.error')
    assert error['code'] == 'module_load_failed'
    content = (tmp_path / 'app/logs/workers' / error['diagnosticReceipt']).read_text()
    assert 'protocol_compliance' in content and 'BackendError' in content
    assert 'synthetic-worker-secret' not in content + json.dumps(events)
    assert 'protocol_compliance' not in json.dumps(events)
    assert logging.getLogger('amplifier_core').handlers == original_handlers
    assert worker.execution is None and worker.shutdown.is_set()


@pytest.mark.asyncio
async def test_handled_startup_reference_survives_send_and_copy_diagnostics(tmp_path, monkeypatch):
    from amplifier_web.service import AppService, AppError
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(
        stderr='protocol_compliance: private backend error')])
    app = AppService(home, manager, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        with pytest.raises(AppError) as error:
            await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Keep this draft'}, command_id='input-fixture')
        assert error.value.code == 'worker_startup_failed'
        session = app._session(sid)
        receipt = session['diagnosticReceipt']
        assert session['messages'][-1]['text'] == 'Keep this draft'
        assert 'private backend error' not in json.dumps(app.browser_state())
        inspected = await app.dispatch('session.inspect', {'id': sid})
        assert inspected['result']['diagnosticReceipt'] == receipt
        assert 'private backend error' not in json.dumps(inspected)
        await app.close()
        app = AppService(home, RuntimeManager(command=[sys.executable, '-c', 'raise SystemExit(3)']), workspace=tmp_path)
        assert app._session(sid)['diagnosticReceipt'] == receipt
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'ready'})
        assert 'diagnosticReceipt' not in app._session(sid)
        inspected = await app.dispatch('session.inspect', {'id': sid})
        assert 'diagnosticReceipt' not in inspected['result']
        assert (home / 'logs/workers' / receipt).is_file()
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_manager_reuses_private_worker_receipt_instead_of_generic_fallback(tmp_path, monkeypatch):
    from amplifier_web.module_failures import ConfiguredModuleError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    receipt = save_startup_failure({'stderr': ['protocol_compliance: original private failure'],
                                    'runtime_id': 'worker-receipt'}, None, failure='ConfiguredModuleError')
    manager = RuntimeManager(command=[sys.executable, '-c', handled_worker(receipt=receipt.name)])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        with pytest.raises(ConfiguredModuleError) as error:
            await manager.start({'id': 'worker-receipt'}, emit)
        assert error.value.diagnostic_path == receipt
        assert len(list(receipt.parent.glob('startup-*.log'))) == 1
        assert 'original private failure' not in json.dumps(events)
    finally:
        await manager.close()


@pytest.mark.parametrize('fault', [None, 'outside', 'unsafe', 'publish', 'close'])
def test_native_diagnostic_writer_models_explicit_home_before_payload(tmp_path, monkeypatch, fault):
    """Actual writer bodies, fake native boundary; no process or native pass."""
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace
    from amplifier_web import deployment, worker_diagnostics as diagnostics
    from amplifier_web.host import config
    home, explicit = tmp_path / 'app', tmp_path / 'explicit-app'
    monkeypatch.setattr(config, 'app_home', lambda: home)
    monkeypatch.setattr(config, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(deployment, '_WINDOWS', True)
    writes, opened = [], []
    @contextmanager
    def directory(path, *, owned_root, create):
        opened.append((path, owned_root, create))
        if not path.is_relative_to(owned_root) or fault == 'unsafe':
            raise PermissionError('unsafe existing directory; not repaired')
        def publish(name, raw, *, replace):
            assert replace is True and path.is_relative_to(owned_root)
            if fault == 'publish':
                raise OSError('publication failed')
            writes.append((path / name, raw, owned_root))
        yield SimpleNamespace(publish_private=publish)
        if fault == 'close':
            raise OSError('close failed after publication')
    monkeypatch.setattr(deployment.windows_files, 'address', Path)
    monkeypatch.setattr(deployment.windows_files, 'directory', directory)
    # Frozen old call shape fails before any native directory/payload operation.
    with pytest.raises(NotImplementedError, match='explicit owned_root'):
        deployment.write_private(home / 'logs/workers/before.log', 'private')
    assert not opened and not writes
    capture = diagnostics.StartupCapture()
    original = RuntimeError('original failure')
    original.__cause__ = TimeoutError('original cause')
    try:
        saved = capture.save(original, 'synthetic')
    finally:
        capture.close()
    success = fault in {None, 'outside'}
    assert (saved is not None) == success
    assert original.__cause__.args == ('original cause',)
    if success:
        header = json.loads(writes[0][1].partition(b'\n\n')[0])
        assert header['startupFailureFacts'] == {
            'errors': [{'class': 'RuntimeError'}, {'class': 'TimeoutError'}], 'truncated': False}
    result = diagnostics.save_startup_failure({'stderr': ['explicit private receipt']}, 1, home=explicit)
    assert (result is not None) == success
    if success:
        assert writes[-1][2] == explicit and result.parent == explicit / 'logs/workers'
    before = len(writes)
    target = tmp_path / 'foreign' if fault == 'outside' else home / 'runtime-reports/synthetic'
    diagnostics.StartupTimings().save(target, 'failed')
    if fault == 'outside':
        assert len(writes) == before  # Canonical app root, never destination parent.
    assert all(root in {home, explicit} and path.is_relative_to(root)
               for path, _, root in writes)
    assert not home.exists() and not explicit.exists()  # No real directory/ACL edit.


@pytest.mark.parametrize('kind', ['timeout', 'process', 'os', 'cancel', 'unknown', 'subclass', 'clone', 'forged'])
def test_private_startup_exception_facts_use_exact_classes_not_prose(monkeypatch, kind):
    import subprocess
    import types
    from amplifier_web import worker_diagnostics as diagnostics
    module = types.ModuleType('amplifier_foundation.modules.activator')
    activation = type('ModuleActivationError', (RuntimeError,), {})
    module.ModuleActivationError = activation
    monkeypatch.setitem(sys.modules, module.__name__, module)
    private = 'private URL token command SID path/runtime/id'
    choices = {
        'timeout': subprocess.TimeoutExpired([private], 35, output=private, stderr=private),
        'process': subprocess.CalledProcessError(128, [private], output=private, stderr=private),
        'os': PermissionError(13, private),
        'cancel': asyncio.CancelledError(private),
        'unknown': type('PrivateSecretClass', (Exception,), {})(private),
        'subclass': type('InheritedTimeout', (subprocess.TimeoutExpired,), {})([private], 35),
        'clone': type('ModuleActivationError', (RuntimeError,), {})(private),
        'forged': RuntimeError('ModuleActivationError: TimeoutExpired: CalledProcessError ' + private),
    }
    root = activation(private)
    root.__cause__ = choices[kind]
    facts = diagnostics._startup_exception_facts(root)
    expected = {'timeout': 'TimeoutExpired', 'process': 'CalledProcessError', 'os': 'PermissionError',
                'cancel': 'CancelledError', 'forged': 'RuntimeError'}.get(kind, 'other')
    assert facts['errors'][0] == {'class': 'ModuleActivationError'}
    assert facts['errors'][1]['class'] == expected
    assert facts['truncated'] is False
    assert private not in json.dumps(facts)
    assert facts['errors'][1] == ({'class': expected, 'returncode': 128} if kind == 'process' else
                                  {'class': expected, 'errno': 13} if kind == 'os' else {'class': expected})


def test_private_startup_exception_facts_bound_chain_cycle_context_and_numeric_fields():
    import subprocess
    from amplifier_web.worker_diagnostics import _startup_exception_facts
    chain = [RuntimeError('never export') for _ in range(8)]
    for left, right in zip(chain, chain[1:]):
        left.__cause__ = right
    assert _startup_exception_facts(chain[0]) == {'errors': [{'class': 'RuntimeError'}] * 4, 'truncated': True}
    chain[0].__cause__ = chain[0]
    assert _startup_exception_facts(chain[0]) == {'errors': [{'class': 'RuntimeError'}], 'truncated': True}
    error = ValueError('outer')
    error.__context__ = TimeoutError('hidden')
    error.__suppress_context__ = True
    assert _startup_exception_facts(error)['errors'] == [{'class': 'ValueError'}]
    error.__suppress_context__ = False
    assert _startup_exception_facts(error)['errors'] == [{'class': 'ValueError'}, {'class': 'TimeoutError'}]
    for number in (True, '128', 2**32, -(2**31) - 1):
        assert _startup_exception_facts(subprocess.CalledProcessError(number, 'private'))['errors'] == [
            {'class': 'CalledProcessError'}]
    # Even a hostile metaclass cannot compare equal to a known exception class.
    class EqualType(type):
        def __eq__(self, other): return True
        def __hash__(self): return hash(RuntimeError)
    class Impostor(Exception, metaclass=EqualType):
        pass
    assert _startup_exception_facts(Impostor('private'))['errors'] == [{'class': 'other'}]


@pytest.mark.parametrize('fault', [OSError, asyncio.CancelledError])
def test_private_startup_facts_fault_and_write_fault_never_replace_original(tmp_path, monkeypatch, fault):
    from amplifier_web import worker_diagnostics as diagnostics
    from amplifier_web.host import config
    def failed(*args, **kwargs):
        raise fault('private diagnostic fault')
    monkeypatch.setattr(config, 'write_private', failed)
    capture = diagnostics.StartupCapture()
    error = asyncio.CancelledError('original cancellation')
    try:
        assert capture.save(error, 'synthetic') is None
        monkeypatch.setattr(diagnostics, '_startup_exception_facts', failed)
        assert capture.save(RuntimeError('original failure'), 'synthetic') is None
        assert diagnostics.StartupTimings().save(tmp_path, 'failed') is None
    finally:
        capture.close()
    assert error.args == ('original cancellation',)


def _command_facts_fixture():
    return {'errors': [{'class': 'RuntimeError', 'frames': [
        {'source': 'worker.command_serial', 'offset': 17}]}], 'truncated': False}


def _command_traceback(function, *, count=1, offset=7, args=None):
    """Use an unstarted coroutine's actual code; no command or worker runs."""
    import types
    coroutine = function(*(args if args is not None else (None, {})))
    try:
        frame = coroutine.cr_frame
        result = None
        for _ in range(count):
            result = types.TracebackType(result, frame, 0, function.__code__.co_firstlineno + offset)
        return result
    finally:
        coroutine.close()


def test_command_failure_facts_never_read_secret_prose_subclass_properties_or_locals():
    from amplifier_web.worker_diagnostics import command_failure_facts, validate_command_failure_facts
    touched = []
    class PrivateSecretError(RuntimeError):
        def __str__(self):
            touched.append('str')
            raise AssertionError('private string')
        def __repr__(self):
            touched.append('repr')
            raise AssertionError('private repr')
        def __getattribute__(self, key):
            touched.append(key)
            raise AssertionError('private attribute')
        @property
        def __traceback__(self):
            touched.append('traceback descriptor')
            raise AssertionError('private traceback')
        @property
        def __cause__(self):
            touched.append('cause descriptor')
            raise AssertionError('private cause')
    secret = 'private-token https://private.invalid /private/secret.py secret-argument'
    error = PrivateSecretError(secret, object())
    BaseException.__cause__.__set__(error, ValueError(secret))
    facts = command_failure_facts(error)
    assert facts == {'errors': [{'class': 'other', 'frames': []},
                                {'class': 'ValueError', 'frames': []}], 'truncated': False}
    assert validate_command_failure_facts(facts) == facts
    assert not touched and secret not in json.dumps(facts)
    class EqualType(type):
        def __eq__(self, other): raise AssertionError('class equality called')
        def __hash__(self): raise AssertionError('class hashed')
    class Impostor(RuntimeError, metaclass=EqualType):
        pass
    assert command_failure_facts(Impostor(secret))['errors'] == [{'class': 'other', 'frames': []}]


def test_command_failure_facts_known_types_do_not_format_arguments_or_process_payloads():
    import subprocess
    from amplifier_web.worker_diagnostics import command_failure_facts
    class SecretArgument:
        def __str__(self): raise AssertionError('argument formatted')
        def __repr__(self): raise AssertionError('argument represented')
    argument = SecretArgument()
    for error, label in (
            (RuntimeError(argument), 'RuntimeError'),
            (asyncio.CancelledError(argument), 'CancelledError'),
            (subprocess.TimeoutExpired(argument, argument, output=argument, stderr=argument), 'TimeoutExpired'),
            (type('TimeoutExpired', (Exception,), {})(argument), 'other')):
        assert command_failure_facts(error) == {
            'errors': [{'class': label, 'frames': []}], 'truncated': False}


@pytest.mark.parametrize('method,label', [
    ('command', 'worker.command'), ('_command_serial', 'worker.command_serial')])
def test_command_failure_frame_labels_require_loaded_code_identity_not_equal_clones(method, label):
    import types
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.worker_diagnostics import command_failure_facts
    function = getattr(Worker, method)
    error = RuntimeError('private argument')
    error.__traceback__ = _command_traceback(function)
    assert command_failure_facts(error) == {
        'errors': [{'class': 'RuntimeError', 'frames': [{'source': label, 'offset': 7}]}],
        'truncated': False}
    clone = function.__code__.replace()
    assert clone == function.__code__ and clone is not function.__code__
    copied = types.FunctionType(clone, function.__globals__, function.__name__)
    error.__traceback__ = _command_traceback(copied)
    assert command_failure_facts(error)['errors'][0]['frames'] == []
    # Matching names and arbitrary traceback filenames are never exported.
    copied = types.FunctionType(clone.replace(co_filename='/private/token.py'), function.__globals__)
    error.__traceback__ = _command_traceback(copied)
    assert command_failure_facts(error)['errors'][0]['frames'] == []
    assert '/private/token.py' not in json.dumps(command_failure_facts(error))


def test_command_frame_owner_supports_script_main_without_imports(monkeypatch):
    import builtins
    import types
    from amplifier_web import runtime_worker, worker_diagnostics as diagnostics
    error = RuntimeError('private')
    error.__traceback__ = _command_traceback(runtime_worker.Worker._command_serial)
    script = types.ModuleType('__main__')
    script.__file__ = runtime_worker.__file__
    script.Worker = runtime_worker.Worker
    monkeypatch.delitem(sys.modules, 'amplifier_web.runtime_worker')
    monkeypatch.setitem(sys.modules, '__main__', script)
    def no_import(*args, **kwargs):
        raise AssertionError('diagnostics must not import packages')
    monkeypatch.setattr(builtins, '__import__', no_import)
    facts = diagnostics.command_failure_facts(error)
    assert facts == {'errors': [{'class': 'RuntimeError', 'frames': [
        {'source': 'worker.command_serial', 'offset': 7}]}], 'truncated': False}
    script.__file__ = '/private/unrelated/runtime_worker.py'
    assert diagnostics.command_failure_facts(error)['errors'][0]['frames'] == []
    monkeypatch.setitem(sys.modules, 'amplifier_web.runtime_worker', types.SimpleNamespace(Worker=script.Worker))
    assert diagnostics.command_failure_facts(error)['errors'][0]['frames'] == []


def test_command_failure_chain_context_cycles_and_empty_input_are_bounded():
    from amplifier_web.worker_diagnostics import command_failure_facts
    rows = [{'class': 'RuntimeError', 'frames': []}]
    chain = [RuntimeError('private') for _ in range(8)]
    for left, right in zip(chain, chain[1:]):
        left.__cause__ = right
    assert command_failure_facts(chain[0]) == {'errors': rows * 4, 'truncated': True}
    chain[3].__cause__ = None
    assert command_failure_facts(chain[0]) == {'errors': rows * 4, 'truncated': False}
    chain[0].__cause__ = chain[0]
    assert command_failure_facts(chain[0]) == {'errors': rows, 'truncated': True}
    error = RuntimeError('private')
    error.__context__ = TimeoutError('private context')
    assert command_failure_facts(error)['errors'] == rows + [{'class': 'TimeoutError', 'frames': []}]
    error.__suppress_context__ = True
    assert command_failure_facts(error) == {'errors': rows, 'truncated': False}
    error.__cause__ = ValueError('private cause')
    assert command_failure_facts(error)['errors'] == rows + [{'class': 'ValueError', 'frames': []}]
    assert command_failure_facts(None) == {'errors': [], 'truncated': True}


@pytest.mark.parametrize('count,truncated', [(5, False), (6, True), (128, True), (129, True)])
def test_command_frame_retention_is_bounded(count, truncated):
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.worker_diagnostics import command_failure_facts
    error = RuntimeError('private')
    error.__traceback__ = _command_traceback(Worker._command_serial, count=count)
    assert command_failure_facts(error) == {
        'errors': [{'class': 'RuntimeError', 'frames': [
            {'source': 'worker.command_serial', 'offset': 7}] * 5}], 'truncated': truncated}


def test_command_frame_scan_stops_after_128_unapproved_nodes():
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.worker_diagnostics import command_failure_facts
    async def unknown(self, data):
        raise AssertionError('must not run')
    error = RuntimeError('private')
    error.__traceback__ = _command_traceback(unknown, count=128)
    assert command_failure_facts(error) == {
        'errors': [{'class': 'RuntimeError', 'frames': []}], 'truncated': False}
    last = error.__traceback__
    while last.tb_next is not None:
        last = last.tb_next
    last.tb_next = _command_traceback(Worker.command)
    assert command_failure_facts(error) == {
        'errors': [{'class': 'RuntimeError', 'frames': []}], 'truncated': True}


@pytest.mark.parametrize('offset', [-1, 0, 1_000_000, 1_000_001])
def test_command_frame_offset_bounds(offset):
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.worker_diagnostics import command_failure_facts
    error = RuntimeError('private')
    error.__traceback__ = _command_traceback(Worker.command, offset=offset)
    valid = 0 <= offset <= 1_000_000
    assert command_failure_facts(error) == {
        'errors': [{'class': 'RuntimeError', 'frames': [
            {'source': 'worker.command', 'offset': offset}] if valid else []}], 'truncated': not valid}


@pytest.mark.parametrize('number', [-(2**31), 0, 2**32 - 1, -(2**31) - 1, 2**32, True, 1.0, '13', None])
def test_command_numeric_facts_and_validator_require_exact_bounded_integers(number):
    import subprocess
    from amplifier_web.worker_diagnostics import command_failure_facts, validate_command_failure_facts
    valid = type(number) is int and -(2**31) <= number < 2**32
    os_error = OSError('private path')
    os_error.errno = number
    os_error.winerror = number
    for error, label, keys in (
            (os_error, 'OSError', ('errno', 'winerror')),
            (subprocess.CalledProcessError(number, 'private command', output='private'), 'CalledProcessError', ('returncode',))):
        expected = {'errors': [{'class': label, 'frames': [],
                               **({key: number for key in keys} if valid else {})}], 'truncated': False}
        assert command_failure_facts(error) == expected
        assert validate_command_failure_facts(expected) == expected
        wire = {'errors': [{'class': label, 'frames': [], **{key: number for key in keys}}], 'truncated': False}
        assert (validate_command_failure_facts(wire) is not None) == valid


def test_command_facts_validator_deep_copies_and_round_trips_closed_schema():
    from amplifier_web.worker_diagnostics import validate_command_failure_facts
    original = _command_facts_fixture()
    result = validate_command_failure_facts(json.loads(json.dumps(original)))
    assert result == original
    direct = validate_command_failure_facts(original)
    direct['errors'][0]['frames'][0]['offset'] = 99
    direct['errors'][0]['frames'].append({'source': 'worker.command', 'offset': 0})
    direct['errors'].append({'class': 'other', 'frames': []})
    assert original == _command_facts_fixture()


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(secret='private'),
    lambda v: v.pop('errors'),
    lambda v: v.update(errors=()),
    lambda v: v.update(errors=[{}] * 5),
    lambda v: v.update(truncated=0),
    lambda v: v.update(errors=[None]),
    lambda v: v['errors'][0].update(message='private'),
    lambda v: v['errors'][0].pop('frames'),
    lambda v: v['errors'][0].update(**{'class': 'PrivateSecretError'}),
    lambda v: v['errors'][0].update(**{'class': 'x' * 8193}),
    lambda v: v['errors'][0].update(frames=[{}] * 6),
    lambda v: v['errors'][0].update(frames=()),
    lambda v: v['errors'][0].update(frames=[None]),
    lambda v: v['errors'][0].update(errno=13),
    lambda v: v['errors'][0].update(returncode=1),
    lambda v: v['errors'][0]['frames'][0].update(file='/private/path'),
    lambda v: v['errors'][0]['frames'][0].pop('source'),
    lambda v: v['errors'][0]['frames'][0].update(source='/private/path'),
    lambda v: v['errors'][0]['frames'][0].update(offset=True),
    lambda v: v['errors'][0]['frames'][0].update(offset=-1),
    lambda v: v['errors'][0]['frames'][0].update(offset=1_000_001),
    lambda v: v['errors'][0]['frames'][0].update(offset=1.0),
])
def test_command_facts_validator_refuses_malformed_nested_unknown_and_oversize_values(mutation):
    from amplifier_web.worker_diagnostics import validate_command_failure_facts
    value = _command_facts_fixture()
    mutation(value)
    assert validate_command_failure_facts(value) is None


def test_command_validators_reject_container_scalar_subclasses_and_cycles():
    from amplifier_web.worker_diagnostics import validate_command_failure_facts
    class BadDict(dict):
        def __iter__(self): raise AssertionError('private iterator')
    class BadList(list):
        def __iter__(self): raise AssertionError('private iterator')
    class BadString(str):
        def __eq__(self, other): raise AssertionError('private comparison')
        def __hash__(self): raise AssertionError('private hash')
    class Int(int):
        pass
    assert validate_command_failure_facts(BadDict(_command_facts_fixture())) is None
    for key, value in (('class', BadString('RuntimeError')), ('frames', BadList()),
                       ('frames', [{'source': BadString('worker.command'), 'offset': 0}]),
                       ('frames', [{'source': 'worker.command', 'offset': Int(0)}])):
        facts = _command_facts_fixture()
        facts['errors'][0][key] = value
        assert validate_command_failure_facts(facts) is None
    cyclic = {'errors': [], 'truncated': False}
    cyclic['errors'].append(cyclic)
    assert validate_command_failure_facts(cyclic) is None
    facts = _command_facts_fixture()
    facts['errors'][0]['frames'][0] = facts
    assert validate_command_failure_facts(facts) is None
    class HostileKey:
        def __hash__(self): return 0
        def __eq__(self, other): raise AssertionError('key compared')
    assert validate_command_failure_facts({HostileKey(): False, 'errors': []}) is None


def test_command_fact_faults_fail_closed_and_serialized_limit_is_enforced(monkeypatch):
    from amplifier_web import worker_diagnostics as diagnostics
    original = RuntimeError('private original')
    def failed():
        raise asyncio.CancelledError('private diagnostic fault')
    monkeypatch.setattr(diagnostics, '_command_frame_codes', failed)
    assert diagnostics.command_failure_facts(original) == {'errors': [], 'truncated': True}
    assert original.args == ('private original',)
    facts = _command_facts_fixture()
    size = len(json.dumps(facts).encode('utf-8'))
    monkeypatch.setattr(diagnostics, '_COMMAND_MAX_BYTES', size)
    assert diagnostics.validate_command_failure_facts(facts) == facts
    monkeypatch.setattr(diagnostics, '_COMMAND_MAX_BYTES', size - 1)
    assert diagnostics.validate_command_failure_facts(facts) is None


def _reader_snapshot_fixture(reason='eof'):
    return {'reason': reason, 'phase': 'read', 'frame': 'none', 'exitObserved': False,
            'exitCode': None, 'closing': False, 'stopReason': 'none', 'cleanup': 'not_requested'}


def _reader_witness_fixture(reason='eof'):
    facts = {'errors': [], 'truncated': False} if reason == 'eof' else _command_facts_fixture()
    return {'schema': 1, 'origin': 'reader_disconnect',
            'factsStatus': 'not_applicable' if reason == 'eof' else 'valid',
            **facts, 'reader': _reader_snapshot_fixture(reason)}


@pytest.mark.parametrize('origin,status,reason', [
    ('worker_reply', 'valid', None), ('worker_reply', 'invalid', None),
    ('reader_disconnect', 'not_applicable', 'eof'),
    ('reader_disconnect', 'valid', 'exception'), ('reader_disconnect', 'valid', 'cancelled')])
def test_runtime_failure_witness_is_closed_and_accessor_returns_deep_copy(origin, status, reason):
    from amplifier_web.worker_diagnostics import runtime_failure_witness, validate_runtime_failure_witness
    facts = _command_facts_fixture() if status == 'valid' else {'errors': [], 'truncated': False}
    witness = {'schema': 1, 'origin': origin, 'factsStatus': status, **facts}
    if origin == 'reader_disconnect':
        witness['reader'] = _reader_snapshot_fixture(reason)
    error = RuntimeError('private original message')
    error._runtime_failure_witness = witness
    result = runtime_failure_witness(error)
    assert result == witness == validate_runtime_failure_witness(witness)
    if status == 'valid':
        result['errors'][0]['frames'][0]['offset'] = 99
        assert witness['errors'][0]['frames'][0]['offset'] == 17
    if origin == 'reader_disconnect':
        result['reader']['phase'] = 'wait_exit'
        assert witness['reader']['phase'] == 'read'
    result['errors'].append({'class': 'other', 'frames': []})
    assert len(witness['errors']) == (1 if status == 'valid' else 0)
    assert error.args == ('private original message',)
    assert runtime_failure_witness(RuntimeError('private unannotated')) is None
    class Subclass(RuntimeError):
        pass
    unknown = Subclass('private')
    unknown._runtime_failure_witness = witness
    assert runtime_failure_witness(unknown) is None


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(schema=True),
    lambda v: v.update(schema=2),
    lambda v: v.update(schema=1.0),
    lambda v: v.update(message='private'),
    lambda v: v.pop('origin'),
    lambda v: v.update(origin='private'),
    lambda v: v.update(factsStatus='private'),
    lambda v: v.update(factsStatus='not_applicable'),
    lambda v: v.update(origin='reader_disconnect'),
    lambda v: v.update(reader=_reader_snapshot_fixture()),
    lambda v: v.update(factsStatus='invalid'),
    lambda v: v.update(errors=[], truncated=True, factsStatus='invalid'),
    lambda v: v.update(origin='reader_disconnect', factsStatus='not_applicable', reader=_reader_snapshot_fixture()),
    lambda v: v.update(origin='reader_disconnect', factsStatus='not_applicable', errors=[], truncated=True,
                       reader=_reader_snapshot_fixture()),
    lambda v: v.update(origin='reader_disconnect', factsStatus='invalid', errors=[], reader=_reader_snapshot_fixture()),
    lambda v: v['errors'][0]['frames'][0].update(source='private' * 8192),
])
def test_runtime_failure_witness_refuses_invalid_shapes_and_status_combinations(mutation):
    from amplifier_web.worker_diagnostics import runtime_failure_witness, validate_runtime_failure_witness
    witness = {'schema': 1, 'origin': 'worker_reply', 'factsStatus': 'valid', **_command_facts_fixture()}
    mutation(witness)
    assert validate_runtime_failure_witness(witness) is None
    error = RuntimeError('private')
    error._runtime_failure_witness = witness
    assert runtime_failure_witness(error) is None


@pytest.mark.parametrize('origin', ['worker_reply', 'reader_disconnect'])
def test_runtime_failure_witness_enforces_complete_serialized_size(monkeypatch, origin):
    from amplifier_web import worker_diagnostics as diagnostics
    facts = {'errors': [{'class': 'ConnectionAbortedError', 'errno': -(2**31), 'winerror': 2**32 - 1,
                         'frames': [{'source': 'worker.command_serial', 'offset': 1_000_000}] * 5}] * 4,
             'truncated': True}
    witness = {'schema': 1, 'origin': origin, 'factsStatus': 'valid', **facts}
    if origin == 'reader_disconnect':
        witness['reader'] = _reader_snapshot_fixture('exception')
    assert diagnostics.validate_runtime_failure_witness(witness) == witness
    assert len(json.dumps(witness).encode('utf-8')) < 8192
    size = len(json.dumps(witness).encode('utf-8'))
    monkeypatch.setattr(diagnostics, '_COMMAND_MAX_BYTES', size)
    assert diagnostics.validate_runtime_failure_witness(witness) == witness
    monkeypatch.setattr(diagnostics, '_COMMAND_MAX_BYTES', size - 1)
    assert diagnostics.validate_command_failure_facts(facts) == facts
    assert diagnostics.validate_runtime_failure_witness(witness) is None


def _reader_frame_fixture(label):
    from amplifier_web import runtime, runtime_event_batches
    return {
        'runtime.reader': (runtime.RuntimeManager._read, (None, 'private-sid', {})),
        'runtime.readline': (runtime._BoundedWorkerLines.readline, (None,)),
        'runtime.wait': (runtime._wait_worker, ({},)),
        'runtime.windows_wait': (runtime._WindowsWorkerOwner.wait, (None,)),
        'runtime.buffered_next': (runtime_event_batches.BufferedLines.next, (None,)),
    }[label]


@pytest.mark.parametrize('label', [
    'runtime.reader', 'runtime.readline', 'runtime.wait', 'runtime.windows_wait', 'runtime.buffered_next'])
def test_reader_frames_require_exact_loaded_code_identity_and_never_import(monkeypatch, label):
    import builtins
    import types
    from amplifier_web import worker_diagnostics as diagnostics
    function, args = _reader_frame_fixture(label)
    error = RuntimeError('private-token /private/path https://private.invalid')
    error.__traceback__ = _command_traceback(function, args=args)
    clone = function.__code__.replace()
    assert clone == function.__code__ and clone is not function.__code__
    copied = types.FunctionType(clone, function.__globals__, function.__name__)
    cloned_error = RuntimeError('private clone')
    cloned_error.__traceback__ = _command_traceback(copied, args=args)
    secret_clone = types.FunctionType(
        clone.replace(co_filename='/private/token.py'), function.__globals__, function.__name__)
    secret_error = RuntimeError('private filename')
    secret_error.__traceback__ = _command_traceback(secret_clone, args=args)
    def no_import(*args, **kwargs):
        raise AssertionError('diagnostics must not import packages')
    with monkeypatch.context() as patch:
        patch.setattr(builtins, '__import__', no_import)
        facts = diagnostics.command_failure_facts(error)
        assert facts == {'errors': [{'class': 'RuntimeError', 'frames': [
            {'source': label, 'offset': 7}]}], 'truncated': False}
        assert diagnostics.validate_command_failure_facts(facts) == facts
        assert diagnostics.command_failure_facts(cloned_error)['errors'][0]['frames'] == []
        assert diagnostics.command_failure_facts(secret_error)['errors'][0]['frames'] == []
        # Unloaded canonical owners must not be imported or matched by filename.
        patch.delitem(sys.modules, 'amplifier_web.runtime')
        patch.delitem(sys.modules, 'amplifier_web.runtime_event_batches')
        assert diagnostics.command_failure_facts(error)['errors'][0]['frames'] == []
    assert 'private' not in json.dumps(facts)


def test_reader_frame_discovery_rejects_arbitrary_module_and_class_objects(monkeypatch):
    import types
    from amplifier_web import runtime, worker_diagnostics as diagnostics
    error = RuntimeError('private')
    error.__traceback__ = _command_traceback(runtime.RuntimeManager._read, args=(None, 'private', {}))
    def touched(*args):
        raise AssertionError('arbitrary owner inspected')
    class HostileModule(types.ModuleType):
        __getattribute__ = touched
    for module in (types.SimpleNamespace(RuntimeManager=runtime.RuntimeManager),
                   HostileModule('amplifier_web.runtime')):
        monkeypatch.setitem(sys.modules, 'amplifier_web.runtime', module)
        assert diagnostics.command_failure_facts(error)['errors'][0]['frames'] == []
    module = types.ModuleType('amplifier_web.runtime')
    class HostileType(type):
        __getattribute__ = touched
    module.RuntimeManager = HostileType('RuntimeManager', (), {'_read': runtime.RuntimeManager._read})
    monkeypatch.setitem(sys.modules, 'amplifier_web.runtime', module)
    assert diagnostics.command_failure_facts(error)['errors'][0]['frames'] == []


@pytest.mark.parametrize('reason', ['eof', 'exception', 'cancelled'])
@pytest.mark.parametrize('status', ['valid', 'invalid', 'not_applicable'])
def test_reader_witness_reason_requires_exact_facts_status(reason, status):
    from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
    value = _reader_witness_fixture(reason)
    value['factsStatus'] = status
    expected = 'not_applicable' if reason == 'eof' else 'valid'
    result = validate_runtime_failure_witness(value)
    assert (result is not None) == (status == expected)
    if result is not None:
        assert result == value


@pytest.mark.parametrize('reason', ['exception', 'cancelled'])
def test_reader_witness_reuses_bounded_causal_facts_without_changing_exception(reason):
    from amplifier_web.worker_diagnostics import command_failure_facts, validate_runtime_failure_witness
    error = asyncio.CancelledError('private cancellation') if reason == 'cancelled' else RuntimeError('private failure')
    error.__cause__ = PermissionError(13, 'private error', '/private/path')
    function, args = _reader_frame_fixture('runtime.reader')
    error.__traceback__ = _command_traceback(function, args=args)
    witness = {**_reader_witness_fixture(reason), **command_failure_facts(error)}
    assert validate_runtime_failure_witness(witness) == witness
    assert witness['errors'] == [
        {'class': 'CancelledError' if reason == 'cancelled' else 'RuntimeError',
         'frames': [{'source': 'runtime.reader', 'offset': 7}]},
        {'class': 'PermissionError', 'frames': [], 'errno': 13}]
    assert error.__cause__.filename == '/private/path'
    assert 'private' not in json.dumps(witness)
    # A failed fact capture remains explicitly truncated, not invented prose.
    witness.update(errors=[], truncated=True)
    assert validate_runtime_failure_witness(witness) == witness


@pytest.mark.parametrize('key,values', [
    ('phase', ('read', 'dispatch', 'wait_exit', 'report_exit', 'settle_execution')),
    ('frame', ('none', 'ready', 'reply', 'progress', 'status', 'error', 'execution', 'bridge', 'other')),
    ('stopReason', ('none', 'stopped', 'configuration', 'shutdown', 'retention', 'other')),
    ('cleanup', ('not_requested', 'confirmed', 'failed')),
    ('closing', (False, True)),
])
def test_reader_snapshot_accepts_each_closed_enum_and_boolean(key, values):
    from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
    for item in values:
        witness = _reader_witness_fixture('exception')
        witness['reader'][key] = item
        assert validate_runtime_failure_witness(witness) == witness


@pytest.mark.parametrize('observed', [False, True])
@pytest.mark.parametrize('code', [None, -(2**31), 0, 2**32 - 1, -(2**31) - 1, 2**32,
                                  False, True, 1.0, '0', [], {}])
def test_reader_exit_code_requires_exact_bounded_integer_and_observed_exit(observed, code):
    from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
    witness = _reader_witness_fixture()
    witness['reader'].update(exitObserved=observed, exitCode=code)
    valid = code is None or observed and type(code) is int and -(2**31) <= code < 2**32
    result = validate_runtime_failure_witness(witness)
    assert (result is not None) == valid
    if valid:
        assert result == witness  # Observed True plus None stays unknown, not zero.


@pytest.mark.parametrize('key', [
    'reason', 'phase', 'frame', 'exitObserved', 'exitCode', 'closing', 'stopReason', 'cleanup'])
def test_reader_snapshot_requires_every_field(key):
    from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
    witness = _reader_witness_fixture()
    del witness['reader'][key]
    assert validate_runtime_failure_witness(witness) is None


@pytest.mark.parametrize('key', ['reason', 'phase', 'frame', 'stopReason', 'cleanup'])
@pytest.mark.parametrize('item', ['private-token/path', 'x' * 8193, None, 1, [], {}])
def test_reader_snapshot_rejects_unknown_prose_oversize_and_nonstring_enums(key, item):
    from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
    witness = _reader_witness_fixture()
    witness['reader'][key] = item
    assert validate_runtime_failure_witness(witness) is None


@pytest.mark.parametrize('key', ['exitObserved', 'closing'])
@pytest.mark.parametrize('item', [0, 1, 'false', None, [], {}])
def test_reader_snapshot_requires_exact_booleans(key, item):
    from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
    witness = _reader_witness_fixture()
    witness['reader'][key] = item
    assert validate_runtime_failure_witness(witness) is None


@pytest.mark.parametrize('mutation', [
    lambda v: v.pop('reader'),
    lambda v: v.update(reader=None),
    lambda v: v.update(reader=[]),
    lambda v: v.update(reader='private'),
    lambda v: v.update(inputOutcome='accepted'),
    lambda v: v.update(errors=_command_facts_fixture()['errors']),
    lambda v: v.update(truncated=True),
    lambda v: v['reader'].update(message='private'),
    lambda v: v['reader'].update(errors=[]),
    lambda v: v['reader'].update(exitCode=0),
])
def test_reader_witness_rejects_missing_or_extra_shape_and_eof_error_facts(mutation):
    from amplifier_web.worker_diagnostics import runtime_failure_witness, validate_runtime_failure_witness
    witness = _reader_witness_fixture()
    mutation(witness)
    assert validate_runtime_failure_witness(witness) is None
    error = RuntimeError('private original')
    error._runtime_failure_witness = witness
    assert runtime_failure_witness(error) is None


def test_reader_witness_rejects_hostile_subclasses_cycles_and_malformed_facts():
    from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
    class BadDict(dict):
        def __iter__(self): raise AssertionError('private iterator')
    class BadString(str):
        def __eq__(self, other): raise AssertionError('private comparison')
        def __hash__(self): raise AssertionError('private hash')
    class Int(int):
        pass
    witness = _reader_witness_fixture('exception')
    assert validate_runtime_failure_witness(BadDict(witness)) is None
    witness['reader'] = BadDict(witness['reader'])
    assert validate_runtime_failure_witness(witness) is None
    for key, item in (('reason', BadString('exception')), ('phase', BadString('read')),
                      ('exitCode', Int(0))):
        witness = _reader_witness_fixture('exception')
        witness['reader'].update(exitObserved=True)
        witness['reader'][key] = item
        assert validate_runtime_failure_witness(witness) is None
    witness = _reader_witness_fixture('exception')
    witness['reader']['phase'] = witness['reader']
    assert validate_runtime_failure_witness(witness) is None
    for errors in (None, (), [{}], [{'class': 'other', 'frames': [{'source': 'private', 'offset': 1}]}]):
        witness = _reader_witness_fixture('exception')
        witness['errors'] = errors
        assert validate_runtime_failure_witness(witness) is None
    # Even an empty/None reader key is forbidden on a worker-reply envelope.
    for reader in (None, {}, _reader_snapshot_fixture()):
        witness = {'schema': 1, 'origin': 'worker_reply', 'factsStatus': 'invalid',
                   'errors': [], 'truncated': False, 'reader': reader}
        assert validate_runtime_failure_witness(witness) is None


_STORAGE_FRAME_LABELS = (
    'service.event', 'storage.projection', 'storage.resource', 'storage.atomic', 'storage.naming',
)


def _storage_frame_fixture(monkeypatch, label):
    """Actual code objects; injected early failures prevent persistence or path IO."""
    from types import SimpleNamespace
    from amplifier_web import naming, resource_files, service, session_projection
    from amplifier_web.host import storage
    def failed(*args, **kwargs):
        raise PermissionError(13, 'private fixture token', '/private/fixture/path')
    class FailedState:
        __iter__ = failed
    monkeypatch.setattr(naming, 'directory_for', failed)
    monkeypatch.setattr(storage, 'tempfile', SimpleNamespace(mkstemp=failed))
    slot = vars(storage.SessionStore)['_atomic']
    assert type(slot) is staticmethod
    fixtures = {
        'service.event': (service.AppService, 'on_runtime_event', service.AppService.on_runtime_event,
                          (None, 'private-event', {})),
        'storage.projection': (session_projection, 'persist', session_projection.persist,
                               (None, FailedState(), {})),
        'storage.resource': (resource_files, 'write_prepared', resource_files.write_prepared,
                             (SimpleNamespace(mkdir=failed), None)),
        'storage.atomic': (storage.SessionStore, '_atomic', slot.__func__,
                           (SimpleNamespace(parent='/private/fixture'), 'private text')),
        'storage.naming': (naming, 'persist', naming.persist, (None, {})),
    }
    return fixtures[label]


def _storage_traceback(function, args, *, asynchronous=False):
    if asynchronous:
        return _command_traceback(function, args=args)
    try:
        function(*args)
    except PermissionError as error:
        frame = error.__traceback__
        while frame is not None:
            if frame.tb_frame.f_code is function.__code__:
                return frame
            frame = frame.tb_next
        raise AssertionError('fixture did not enter the selected code') from None
    raise AssertionError('fixture did not fail before persistence')


@pytest.mark.parametrize('label', _STORAGE_FRAME_LABELS)
def test_storage_frame_labels_require_exact_loaded_code_not_clones_or_imports(monkeypatch, label):
    import builtins
    import types
    from amplifier_web import worker_diagnostics as diagnostics
    _, _, function, args = _storage_frame_fixture(monkeypatch, label)
    frame = _storage_traceback(function, args, asynchronous=label == 'service.event')
    error = PermissionError(13, 'private token', '/private/path')
    error.winerror = 32
    error.__traceback__ = types.TracebackType(None, frame.tb_frame, 0, function.__code__.co_firstlineno + 7)
    clone = function.__code__.replace()
    assert clone == function.__code__ and clone is not function.__code__
    copied = types.FunctionType(clone, function.__globals__, function.__name__)
    copied.__kwdefaults__ = function.__kwdefaults__
    cloned_error = PermissionError(13, 'private clone')
    cloned_error.__traceback__ = _storage_traceback(copied, args, asynchronous=label == 'service.event')
    def no_import(*args, **kwargs):
        raise AssertionError('facts must not import their owners')
    with monkeypatch.context() as patch:
        patch.setattr(builtins, '__import__', no_import)
        facts = diagnostics.command_failure_facts(error)
        assert facts == {'errors': [{'class': 'PermissionError', 'errno': 13, 'winerror': 32,
                                    'frames': [{'source': label, 'offset': 7}]}], 'truncated': False}
        assert diagnostics.command_failure_facts(cloned_error)['errors'][0]['frames'] == []
        assert diagnostics.validate_command_failure_facts(facts) == facts
        for module in ('amplifier_web.service', 'amplifier_web.session_projection',
                       'amplifier_web.resource_files', 'amplifier_web.host.storage', 'amplifier_web.naming'):
            patch.delitem(sys.modules, module)
        assert diagnostics.command_failure_facts(error) == {
            'errors': [{'class': 'PermissionError', 'errno': 13, 'winerror': 32, 'frames': []}],
            'truncated': False}
    witness = {**_reader_witness_fixture('exception'), **facts}
    witness['reader'].update(phase='dispatch', frame='progress')
    validated = diagnostics.validate_runtime_failure_witness(witness)
    assert validated == witness
    validated['errors'][0]['frames'][0]['offset'] = 9
    validated['reader']['phase'] = 'read'
    assert witness['errors'][0]['frames'][0]['offset'] == 7
    assert witness['reader']['phase'] == 'dispatch'
    assert 'private' not in json.dumps(witness)
    assert len(json.dumps(witness).encode('utf-8')) <= 8192
    for number in (True, 13.0, '32', -(2**31) - 1, 2**32):
        invalid = diagnostics.validate_runtime_failure_witness(witness)
        invalid['errors'][0]['winerror'] = number
        assert diagnostics.validate_runtime_failure_witness(invalid) is None
    invalid = diagnostics.validate_runtime_failure_witness(witness)
    invalid['errors'][0]['frames'][0]['offset'] = True
    assert diagnostics.validate_runtime_failure_witness(invalid) is None
    witness['errors'][0]['frames'][0]['filename'] = '/private/path'
    assert diagnostics.validate_runtime_failure_witness(witness) is None


@pytest.mark.parametrize('label', _STORAGE_FRAME_LABELS)
@pytest.mark.parametrize('descriptor_kind', ['hostile', 'static_subclass', 'classmethod', 'static'])
def test_storage_frame_registry_only_unwraps_exact_atomic_staticmethod(monkeypatch, label, descriptor_kind):
    from amplifier_web import worker_diagnostics as diagnostics
    owner, name, function, args = _storage_frame_fixture(monkeypatch, label)
    error = PermissionError(13, 'private descriptor')
    error.__traceback__ = _storage_traceback(function, args, asynchronous=label == 'service.event')
    touched = []
    class HostileDescriptor:
        def __get__(self, *args):
            touched.append('get')
            raise AssertionError('arbitrary descriptor invoked')
        @property
        def __func__(self):
            touched.append('func')
            raise AssertionError('arbitrary function wrapper inspected')
    class StaticSubclass(staticmethod):
        def __get__(self, *args):
            touched.append('subclass get')
            raise AssertionError('staticmethod subclass invoked')
        def __getattribute__(self, name):
            touched.append(name)
            raise AssertionError('staticmethod subclass inspected')
    descriptor = {'hostile': HostileDescriptor(), 'static_subclass': StaticSubclass(function),
                  'classmethod': classmethod(function), 'static': staticmethod(function)}[descriptor_kind]
    monkeypatch.setattr(owner, name, descriptor)
    facts = diagnostics.command_failure_facts(error)
    approved = label == 'storage.atomic' and descriptor_kind == 'static'
    assert facts['errors'][0]['frames'] == (
        [{'source': label, 'offset': error.__traceback__.tb_lineno - function.__code__.co_firstlineno}]
        if approved else [])
    assert facts['truncated'] is False
    assert not touched


def test_atomic_staticmethod_wrapped_nonfunction_is_never_inspected(monkeypatch):
    from amplifier_web import worker_diagnostics as diagnostics
    owner, name, function, args = _storage_frame_fixture(monkeypatch, 'storage.atomic')
    error = RuntimeError('private')
    error.__traceback__ = _storage_traceback(function, args)
    touched = []
    class Callable:
        def __call__(self, *args):
            touched.append('call')
            raise AssertionError('wrapper called')
        @property
        def __code__(self):
            touched.append('code')
            raise AssertionError('wrapper code inspected')
    monkeypatch.setattr(owner, name, staticmethod(Callable()))
    assert diagnostics.command_failure_facts(error) == {
        'errors': [{'class': 'RuntimeError', 'frames': []}], 'truncated': False}
    assert not touched


@pytest.mark.parametrize('offset', [-1, 0, 1_000_000, 1_000_001])
def test_storage_frame_offsets_keep_existing_closed_bounds(monkeypatch, offset):
    import types
    from amplifier_web import worker_diagnostics as diagnostics
    _, _, function, args = _storage_frame_fixture(monkeypatch, 'storage.atomic')
    frame = _storage_traceback(function, args)
    error = RuntimeError('private')
    error.__traceback__ = types.TracebackType(None, frame.tb_frame, 0, function.__code__.co_firstlineno + offset)
    valid = 0 <= offset <= 1_000_000
    facts = diagnostics.command_failure_facts(error)
    assert facts == {'errors': [{'class': 'RuntimeError', 'frames': [
        {'source': 'storage.atomic', 'offset': offset}] if valid else []}], 'truncated': not valid}
    wire = {'errors': [{'class': 'RuntimeError', 'frames': [
        {'source': 'storage.atomic', 'offset': offset}]}], 'truncated': False}
    assert (diagnostics.validate_command_failure_facts(wire) is not None) == valid


def test_real_atomic_replace_permission_error_keeps_only_closed_facts(tmp_path, monkeypatch):
    """Real atomic body and tmp files, synthetic replace failure; not a native Windows qualification."""
    from pathlib import Path
    from types import SimpleNamespace
    from amplifier_web.host import storage
    from amplifier_web import worker_diagnostics as diagnostics
    path = tmp_path / 'private-checkpoint.json'
    path.write_text('original', encoding='utf-8')
    secret = 'private token https://private.invalid body'
    failure = PermissionError(13, secret, str(path))
    failure.winerror = 32
    replaced = []
    def fail_replace(source, destination):
        assert Path(source).parent == tmp_path and destination == path
        assert Path(source).read_text(encoding='utf-8') == secret
        replaced.append((source, destination))
        raise failure
    # Scope the injection to this module. fchmod is not portable and permissions
    # are not under test; fdopen/write/flush/fsync/temp cleanup remain real.
    native_os = storage.os
    monkeypatch.setattr(storage, 'os', SimpleNamespace(
        name='posix', fdopen=native_os.fdopen, fchmod=lambda *args: None, fsync=native_os.fsync,
        replace=fail_replace, path=native_os.path, unlink=native_os.unlink))
    slot = vars(storage.SessionStore)['_atomic']
    assert type(slot) is staticmethod
    with pytest.raises(PermissionError) as caught:
        storage.SessionStore._atomic(path, secret)
    assert caught.value is failure and len(replaced) == 1
    assert path.read_text(encoding='utf-8') == 'original'
    assert list(tmp_path.iterdir()) == [path]
    frame = failure.__traceback__
    while frame is not None and frame.tb_frame.f_code is not slot.__func__.__code__:
        frame = frame.tb_next
    assert frame is not None
    offset = frame.tb_lineno - slot.__func__.__code__.co_firstlineno
    assert 0 <= offset <= 1_000_000
    facts = diagnostics.command_failure_facts(failure)
    assert facts == {'errors': [{'class': 'PermissionError', 'errno': 13, 'winerror': 32,
                                'frames': [{'source': 'storage.atomic', 'offset': offset}]}],
                     'truncated': False}
    witness = {**_reader_witness_fixture('exception'), **facts}
    witness['reader'].update(phase='dispatch', frame='progress', cleanup='confirmed')
    assert diagnostics.validate_runtime_failure_witness(witness) == witness
    assert str(tmp_path) not in json.dumps(witness) and secret not in json.dumps(witness)
    assert failure.filename == str(path) and failure.strerror == secret
