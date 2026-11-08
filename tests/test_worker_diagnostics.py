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
