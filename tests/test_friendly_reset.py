import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import reset, installation_health as health


def test_macos_stop_waits_for_unload(monkeypatch):
    responses = iter([0, 0, 0, 1])
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=next(responses) if args == ('loaded',) else 0)
    monkeypatch.setattr(reset, 'command', run)
    monkeypatch.setattr(reset.time, 'sleep', lambda n: None)
    assert reset.stop_service((['stop'], ['start'], ['loaded']))
    assert calls == [('loaded',), ('stop',), ('loaded',), ('loaded',), ('loaded',)]


def test_macos_timeout_does_not_blindly_bootstrap(monkeypatch):
    calls = []
    monkeypatch.setattr(reset, 'command', lambda *a, **kw: calls.append(a) or SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match='taking too long'):
        reset.stop_service((['stop'], ['start'], ['loaded']), timeout=0)
    assert ('start',) not in calls
    reset.start_service((['stop'], ['start'], ['loaded']))
    assert ('start',) not in calls  # Rollback sees that the agent is still loaded.


def test_macos_unloaded_service_is_not_stopped_again(monkeypatch):
    calls = []
    monkeypatch.setattr(reset, 'command', lambda *a, **kw: calls.append(a) or SimpleNamespace(returncode=1))
    assert reset.stop_service((['stop'], ['start'], ['loaded'])) is False
    assert calls == [('loaded',)]


def test_checks_both_commands_even_when_doctor_fails(monkeypatch, tmp_path):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if 'doctor' in args else 0,
                               stdout=json.dumps({'ok': 'doctor' not in args}))
    monkeypatch.setattr(reset, 'command', run)
    results = reset.post_checks(tmp_path / 'install', tmp_path / 'data')
    assert len(calls) == 2 and 'doctor' in calls[0] and 'status' in calls[1]
    assert not results['doctor']['ok'] and results['service status']['ok']
    assert calls[0][0] == tmp_path / 'install/bin/python'
    assert calls[1][-1] == '60'


def test_invalid_check_output_is_not_success(monkeypatch, tmp_path):
    monkeypatch.setattr(reset, 'command', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='not a report'))
    results = reset.post_checks(tmp_path, tmp_path)
    assert not any(r['ok'] for r in results.values())


def test_private_report_keeps_raw_output_off_screen(tmp_path, capsys):
    report = reset.RepairReport(tmp_path)
    with reset.report_session(report):
        reset.command(sys.executable, '-c', 'print("INTERNAL_PROBE_JSON"); print("package==1.0")')
    report.stream.close()
    assert 'INTERNAL_PROBE_JSON' not in capsys.readouterr().out
    assert 'INTERNAL_PROBE_JSON' in report.path.read_text()
    assert report.path.stat().st_mode & 0o777 == 0o600


def test_verbose_also_shows_details(tmp_path, capsys):
    report = reset.RepairReport(tmp_path, verbose=True)
    with reset.report_session(report):
        reset.command(sys.executable, '-c', 'print("VERBOSE_DETAIL")')
    report.stream.close()
    assert 'VERBOSE_DETAIL' in capsys.readouterr().out


def test_doctor_reports_invalid_configuration(tmp_path):
    path = tmp_path / 'config/server.yaml'
    path.parent.mkdir()
    path.write_text('port: [broken')
    result = health.doctor(tmp_path)
    assert not result['ok'] and 'Settings need attention' in result['checks'][0]['message']


def test_doctor_reports_missing_signin_support(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'pam', None)
    result = health.doctor(tmp_path)
    assert not result['ok']
    assert any('Sign-in support needs repair' in row['message'] for row in result['checks'])


def test_doctor_default_is_plain_and_verbose_is_opt_in(tmp_path, capsys):
    result = health.doctor(tmp_path)
    assert result['ok']
    health.display(result)
    text = capsys.readouterr().out
    assert 'Settings are valid' in text and 'Sign-in support is available' in text
    assert 'fingerprint' not in text and 'PAM' not in text


@pytest.mark.parametrize('wrong', ['app', 'version', 'dataIdentity', 'revision', 'ok'])
def test_health_requires_the_right_installation(tmp_path, wrong):
    from amplifier_web.auth import data_identity
    expected = {'version': '1.2.3', 'revision': 'a' * 40}
    response = {'ok': True, 'app': 'amplifier-unified', 'version': '1.2.3',
                'revision': 'a' * 40, 'dataIdentity': data_identity(tmp_path)}
    assert health.matches_health(response, tmp_path, expected)
    response[wrong] = 'wrong'
    assert not health.matches_health(response, tmp_path, expected)


@pytest.mark.asyncio
@pytest.mark.parametrize('service', ['running', 'failed', 'starting'])
async def test_status_checks_authenticated_local_health(aiohttp_server, monkeypatch, tmp_path, service):
    from aiohttp import web
    from amplifier_web.auth import data_identity
    from amplifier_web import update_readiness, __version__
    token = tmp_path / 'config/auth/control-token'
    token.parent.mkdir(parents=True)
    if service != 'starting':
        token.write_text('synthetic-token')
    async def endpoint(request):
        assert request.headers['Authorization'] == 'Bearer synthetic-token'
        return web.json_response({'ok': True, 'app': 'amplifier-unified', 'version': __version__,
                                  'dataIdentity': data_identity(tmp_path)})
    app = web.Application()
    app.router.add_get('/api/health', endpoint)
    server = await aiohttp_server(app)
    monkeypatch.setattr(update_readiness, 'probe_targets', lambda manager, **kw: ([str(server.make_url('/api/health'))], None))
    monkeypatch.setattr(update_readiness, 'running_identity', lambda: {'version': __version__, 'revision': None})
    monkeypatch.setattr(health, 'service_state', lambda home: 'running' if service == 'starting' and token.exists() else service)
    if service == 'starting':
        import asyncio
        asyncio.get_running_loop().call_later(.1, lambda: token.write_text('synthetic-token'))
    result = await health.status(tmp_path, wait=2 if service == 'starting' else 0)
    assert result['ok'] == (service != 'failed')
    assert result['responding'] is True


@pytest.mark.asyncio
async def test_status_without_service_gives_setup_step(tmp_path, monkeypatch):
    monkeypatch.setattr(health, 'service_state', lambda home: 'not configured')
    result = await health.status(tmp_path)
    assert not result['ok'] and 'service install' in result['nextStep']


def test_delayed_worker_exit_is_waited_for(monkeypatch, tmp_path):
    from contextlib import ExitStack
    remaining = [True, True, False]
    def check(*args):
        if remaining.pop(0):
            raise RuntimeError('still stopping')
    monkeypatch.setattr(reset, 'assert_no_processes', check)
    monkeypatch.setattr(reset.time, 'sleep', lambda n: None)
    with ExitStack() as locks:
        reset.wait_until_unused(locks, tmp_path / 'install', tmp_path / 'data')
    assert not remaining


def test_doctor_cli_failed_check_returns_failure(monkeypatch, tmp_path, capsys):
    from amplifier_web import cli
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path), 'doctor', '--json'])
    monkeypatch.setattr(health, 'doctor', lambda home: {'ok': False, 'checks': [{'ok': False, 'message': 'Check settings'}]})
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)['ok'] is False


def test_service_status_cli_failed_health_returns_failure(monkeypatch, tmp_path, capsys):
    from amplifier_web import cli
    async def status(home, wait=0):
        assert wait == 0
        return {'ok': False, 'message': 'Not ready', 'service': 'failed'}
    monkeypatch.setattr(health, 'status', status)
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path), 'service', 'status', '--json'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)['service'] == 'failed'


def test_linux_stop_explains_missing_user_bus(monkeypatch):
    monkeypatch.setattr(reset, 'command', lambda *a, **kw: SimpleNamespace(returncode=1, stderr='Failed to connect to bus: No medium found'))
    with pytest.raises(RuntimeError, match='loginctl enable-linger') as failure:
        reset.stop_service((['systemctl', '--user', 'stop', reset.UNIT], [], None))
    assert 'Do not run reset with sudo' in str(failure.value)


def test_user_service_environment_recovers_only_owned_socket(monkeypatch):
    import stat
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('DBUS_SESSION_BUS_ADDRESS', raising=False)
    monkeypatch.setattr(reset.os, 'getuid', lambda: 1234)
    monkeypatch.setattr(reset.Path, 'stat', lambda self: SimpleNamespace(st_uid=1234, st_mode=stat.S_IFSOCK if self.name=='bus' else stat.S_IFDIR))
    env = reset.user_service_environment()
    assert env['XDG_RUNTIME_DIR'] == '/run/user/1234'
    assert env['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/run/user/1234/bus'
    monkeypatch.setattr(reset.Path, 'stat', lambda self: SimpleNamespace(st_uid=999, st_mode=stat.S_IFSOCK))
    assert 'XDG_RUNTIME_DIR' not in reset.user_service_environment()


def test_failed_doctor_launch_still_runs_service_status(monkeypatch, tmp_path):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if 'doctor' in args:
            raise subprocess.TimeoutExpired(args, 120)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}')
    monkeypatch.setattr(reset, 'command', run)
    result = reset.post_checks(tmp_path, tmp_path)
    assert len(calls) == 2
    assert not result['doctor']['ok'] and result['service status']['ok']
