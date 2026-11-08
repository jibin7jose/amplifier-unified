"""Safe diagnostics survive worker IPC and persisted application state."""
from contextlib import contextmanager
import json
import os
import sys
from types import SimpleNamespace

import pytest

from amplifier_web.module_failures import ConfiguredModuleError, REMEDIATION, persist_failures, safe_failures
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService


@pytest.fixture
def windows_report_boundary(tmp_path, monkeypatch):
    """Actual report/writer functions; only the native directory is modeled."""
    from amplifier_web import deployment, windows_files
    from amplifier_web.host import config
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setattr(config, 'os', SimpleNamespace(name='nt', environ=os.environ))
    monkeypatch.setattr(deployment, '_WINDOWS', True)
    events, publications = [], {}
    state = SimpleNamespace(refusal=None, publish_error=None, close_error=None)

    def publish(name, content, *, replace):
        assert name == 'module-load-failures.json' and isinstance(content, bytes)
        assert replace is True
        events.append('publish')
        if state.publish_error:
            raise state.publish_error
        publications[name] = content

    @contextmanager
    def directory(path, *, owned_root, create=False):
        # The app home must be authoritative, never the caller's report parent.
        assert owned_root == home and create is True
        events.append(('directory', path, owned_root))
        if not path.is_relative_to(owned_root):
            raise ValueError('outside app home')
        if state.refusal:
            raise state.refusal
        events.append('private-create-or-validate')
        try:
            yield SimpleNamespace(publish_private=publish)
        finally:
            events.append('close')
            if state.close_error:
                raise state.close_error

    def no_posix_fallback(*args, **kwargs):
        pytest.fail('Windows report must not fall back to chmod/path writes')

    monkeypatch.setattr(windows_files, 'directory', directory)
    monkeypatch.setattr(deployment, 'write_file', no_posix_fallback)
    return SimpleNamespace(home=home, events=events, publications=publications, state=state)


def _write_module_report(operation, directory):
    from amplifier_web.module_failures import clear_failures
    if operation == 'clear':
        return clear_failures(directory)
    return persist_failures(directory, [{
        'module': 'tool-fixture', 'type': 'tool', 'reason_code': 'missing_source',
        'error': 'private-token', 'source': 'https://private-token'}])


@pytest.mark.parametrize('operation', ['clear', 'persist'])
def test_windows_module_report_binds_app_root_before_publication(windows_report_boundary, operation):
    model = windows_report_boundary
    directory = model.home / 'runtime-reports' / 'fixture'
    result = _write_module_report(operation, directory)
    saved = json.loads(model.publications['module-load-failures.json'])
    assert model.events == [
        ('directory', directory, model.home), 'private-create-or-validate', 'publish', 'close']
    assert saved == ([] if operation == 'clear' else result.failures)
    assert 'private-token' not in json.dumps(saved)
    if operation == 'clear':
        assert result is None
    else:
        assert isinstance(result, ConfiguredModuleError)
    assert not model.home.exists()  # No ordinary mkdir/write or ACL repair.


@pytest.mark.parametrize('operation', ['clear', 'persist'])
@pytest.mark.parametrize('refusal', ['missing-ancestor', 'unsafe-home', 'outside-home'])
def test_windows_module_report_refusal_is_not_repaired_or_swallowed(
        windows_report_boundary, operation, refusal):
    model = windows_report_boundary
    directory = model.home / 'runtime-reports' / 'fixture'
    error = (FileNotFoundError('missing traversal ancestor') if refusal == 'missing-ancestor'
             else PermissionError('unsafe existing app home'))
    if refusal == 'outside-home':
        directory = model.home.parent / 'foreign-reports'
        expected = ValueError
    else:
        model.state.refusal = error
        expected = type(error)
    with pytest.raises(expected) as caught:
        _write_module_report(operation, directory)
    if refusal != 'outside-home':
        assert caught.value is error
    assert model.events == [('directory', directory, model.home)]
    assert not model.publications and not model.home.exists()
    assert not directory.exists()


@pytest.mark.parametrize('operation', ['clear', 'persist'])
@pytest.mark.parametrize('boundary', ['publish_error', 'close_error'])
def test_windows_module_report_io_failure_never_becomes_success(
        windows_report_boundary, operation, boundary):
    model = windows_report_boundary
    old = b'[{"module":"old-failure"}]'
    model.publications['module-load-failures.json'] = old
    error = OSError('modeled private publication/close failure')
    setattr(model.state, boundary, error)
    with pytest.raises(OSError) as caught:
        _write_module_report(operation, model.home / 'runtime-reports' / 'fixture')
    assert caught.value is error and model.events[-1] == 'close'
    if boundary == 'publish_error':
        assert model.publications['module-load-failures.json'] == old
    # A post-publication close failure is still failure, never a false rollback
    # claim. The existing private primitive owns its exact publication semantics.
    assert not model.home.exists()


@pytest.mark.parametrize('operation', ['clear', 'persist'])
@pytest.mark.skipif(os.name == 'nt', reason='actual POSIX private file permission semantics')
def test_posix_module_report_keeps_private_writer_behavior(tmp_path, monkeypatch, operation):
    from amplifier_web import deployment
    from amplifier_web.host import config
    original = deployment.write_file
    calls = []

    def observe(path, text):
        calls.append((path, text))
        return original(path, text)

    monkeypatch.setattr(config, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(deployment, '_WINDOWS', False)
    monkeypatch.setattr(deployment, 'write_file', observe)
    path = tmp_path / 'reports' / 'module-load-failures.json'
    result = _write_module_report(operation, path.parent)
    assert calls == [(path, path.read_text())]
    assert json.loads(path.read_text()) == ([] if operation == 'clear' else result.failures)
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('reason',list(REMEDIATION))
def test_diagnostics_allowlist_persistence(tmp_path,reason):
    original=[{'module':'tool-fixture','type':'tool','reason_code':reason,'error':'private-token','path':'/private-token','source':'https://private-token','guidance':'private-token'}]
    error=persist_failures(tmp_path,original)
    saved=json.loads((tmp_path/'module-load-failures.json').read_text())
    assert saved==error.failures==safe_failures(original)
    assert saved[0]['guidance']==REMEDIATION[reason]
    assert 'private-token' not in str(error)+json.dumps(saved)
    assert (tmp_path/'module-load-failures.json').stat().st_mode & 0o077 == 0


def test_unknown_and_legacy_data_never_echo_untrusted_fields():
    assert safe_failures([{'module':'https://private-token','type':{},'reason_code':{'token':'private-token'}}])==[
        {'module':'unknown','type':'unknown','reason_code':'unknown','guidance':REMEDIATION['unknown']}]
    assert safe_failures([{'module':'tool-fixture','type':'tool'}])[0]['reason_code']=='unknown'


def test_provider_instance_diagnostic_is_allowlisted_without_endpoint_or_raw_error():
    error=ConfiguredModuleError([{'module':'provider-fixture','type':'provider',
        'instance_id':'team-account','reason_code':'provider_configuration_failed',
        'error':'private-token','config':{'base_url':'https://private-token'}}])
    assert error.failures[0]['instance_id']=='team-account'
    assert '(team-account)' in str(error) and 'private-token' not in str(error)+str(error.failures)
    assert 'instance_id' not in safe_failures([{'instance_id':'https://private-token'}])[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [
    {'module':'tool-fixture','type':'tool','reason_code':'invalid_entry_point','error':'private-token'},
    {'module':'provider-fixture','type':'provider','instance_id':'account','reason_code':'provider_mount_failed',
     'diagnostic':{'stage':'mount','category':'unknown','errorType':'ValueError','message':'private-token'}}])
async def test_real_worker_protocol_preserves_only_safe_diagnostics(tmp_path, failure):
    failures=[failure]
    script='import sys,json\njson.loads(sys.stdin.readline())\nprint('+repr(json.dumps({'type':'runtime.error','code':'module_load_failed','moduleFailures':failures,'error':'private-token'}))+',flush=True)\n'
    runtime=RuntimeManager(command=[sys.executable,'-c',script],startup_timeout=3)
    events=[]
    async def emit(kind,data):events.append((kind,data))
    try:
        with pytest.raises(ConfiguredModuleError):
            await runtime.start({'id':'fixture','workspace':str(tmp_path),'bundle':'work'},emit)
        payload=next(data for kind,data in events if kind=='runtime.error')
        assert payload['moduleFailures']==safe_failures(failures)
        assert 'private-token' not in json.dumps(events)
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_service_retains_diagnostic_on_reload_and_clears_after_recovery(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    await app.dispatch('session.create',{})
    sid=app.state['selectedSessionId']
    await app.on_runtime_event('runtime.error',{'sessionId':sid,'error':'private-token',
        'moduleFailures':[{'module':'tool-fixture','type':'tool','reason_code':'invalid_package_layout','error':'private-token'}]})
    assert 'private-token' not in json.dumps(app._session(sid))
    await app.close()
    restored=AppService(tmp_path,workspace=tmp_path)
    try:
        assert restored._session(sid)['moduleFailures'][0]['reason_code']=='invalid_package_layout'
        assert 'package layout' in restored._session(sid)['error']
        restored._session(sid)['health'] = {'moduleFailures': restored._session(sid)['moduleFailures']}
        await restored.on_runtime_event('runtime.status',{'sessionId':sid,'status':'ready'})
        assert 'moduleFailures' not in restored._session(sid)
        assert 'moduleFailures' not in restored._session(sid)['health']
    finally:await restored.close()


def test_native_history_inspection_reads_only_safe_current_failure(tmp_path):
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_health import inspect_session
    from amplifier_web.module_failures import clear_failures
    session={'id':'native-fixture','runtimeSessionId':'native-fixture','workspace':str(tmp_path),'status':'idle'}
    directory=SessionStore.for_app(tmp_path,tmp_path).directory(session['id'])
    persist_failures(directory,[{'module':'tool-fixture','type':'tool','reason_code':'missing_source','error':'private-token'}])
    report=inspect_session(tmp_path,session)
    assert report['moduleFailures'][0]['reason_code']=='missing_source'
    assert 'private-token' not in json.dumps(report)
    clear_failures(directory)
    assert inspect_session(tmp_path,session)['moduleFailures']==[]


@pytest.mark.parametrize("current_exists", [False, True])
def test_current_worker_report_wins_over_native_history_and_clears(tmp_path, current_exists):
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_health import inspect_session
    from amplifier_web.module_failures import clear_failures
    session = {"id": "native-fixture", "workspace": str(tmp_path), "status": "error"}
    legacy = SessionStore.for_app(tmp_path, tmp_path).directory(session["id"])
    current = tmp_path / "runtime-reports" / session["id"]
    persist_failures(legacy, [{"module": "old-tool", "type": "tool", "reason_code": "missing_source"}])
    if current_exists:
        failure = persist_failures(current, [{"module": "current-hook", "type": "hook", "reason_code": "invalid_module_metadata"}])
        assert inspect_session(tmp_path, session)["moduleFailures"] == failure.failures
    else:
        assert inspect_session(tmp_path, session)["moduleFailures"][0]["module"] == "old-tool"
    clear_failures(current)
    assert inspect_session(tmp_path, session)["moduleFailures"] == []
