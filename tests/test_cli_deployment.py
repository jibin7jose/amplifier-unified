from argparse import Namespace
import json
from pathlib import Path, PureWindowsPath
import sys
from types import SimpleNamespace

import pytest

from aiohttp import web
from amplifier_web import cli
from amplifier_web.cli import _config, _parse, _server_overrides
from amplifier_web.deployment import config_path, load_server_config, save_server_config


@pytest.mark.parametrize('command', ['run', 'continue', 'tool'])
def test_unattended_access_flags_are_explicit_and_exclusive(monkeypatch, command):
    arguments = [command, 'fixture-tool'] if command == 'tool' else [command]
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', *arguments, '--yolo'])
    assert _parse().access == 'full'
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', *arguments, '--access', 'ask'])
    assert _parse().access == 'ask'
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', *arguments, '--yolo', '--access', 'ask'])
    with pytest.raises(SystemExit):
        _parse()


def test_access_command_requires_explicit_target_for_nonglobal_policy(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', 'access', 'full'])
    assert _parse().scope == 'app'
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', 'access', 'full', '--scope', 'workspace'])
    with pytest.raises(SystemExit):
        _parse()
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', 'access', 'status', '--scope', 'session', '--session', 'target'])
    assert _parse().session == 'target'


def test_config_cli_sets_and_resets_server_setting(tmp_path, capsys):
    _config(Namespace(config_command="set", key="session_ttl_seconds", value="120"), tmp_path)
    assert load_server_config(tmp_path)["session_ttl_seconds"] == 120
    _config(Namespace(config_command="get", key="session_ttl_seconds"), tmp_path)
    assert capsys.readouterr().out == "120\n"
    _config(Namespace(config_command="reset", key="session_ttl_seconds"), tmp_path)
    assert load_server_config(tmp_path)["session_ttl_seconds"] == 604800


def test_serve_options_are_top_level_repeatable_and_overlay_nested_tls(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "amplifier-unified", "--bind", "127.0.0.1", "--bind", "192.0.2.5",
        "--public-origin", "https://BÜCHER.example:443", "--tls-cert", "leaf.crt",
        "--tls-key", "leaf.key", "--session-ttl", "120", "serve",
    ])
    args = _parse()
    config = load_server_config(tmp_path, overrides=_server_overrides(args))
    assert args.command == "serve"
    assert config["bind"] == ["127.0.0.1", "192.0.2.5"]
    assert config["public_origins"] == ["https://xn--bcher-kva.example"]
    assert config["tls"] == {"method": "ca", "cert": "leaf.crt", "key": "leaf.key"}
    assert config["session_ttl_seconds"] == 120


def test_host_alias_is_exclusive_with_bind_and_options_do_not_follow_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--host", "localhost", "serve"])
    assert _parse().host == "localhost"
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--host", "localhost", "--bind", "127.0.0.1", "serve"])
    with pytest.raises(SystemExit):
        _parse()
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "serve", "--bind", "127.0.0.1"])
    with pytest.raises(SystemExit):
        _parse()
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "service", "install", "--replace"])
    args = _parse()
    assert args.service_command == "install" and args.replace
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "setup-tls", "export"])
    assert _parse().mode == "export"


def test_tls_option_overlay_preserves_unmentioned_nested_values(tmp_path):
    config = load_server_config(tmp_path)
    save_server_config(tmp_path, {**config, "tls": {"method": "ca", "cert": "old.crt", "key": "old.key"}})
    overlaid = load_server_config(tmp_path, overrides={"tls": {"cert": "new.crt"}})
    assert overlaid["tls"] == {"method": "ca", "cert": "new.crt", "key": "old.key"}


def test_cli_starts_with_saved_server_extension_fields(tmp_path, monkeypatch):
    from amplifier_web import server
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    original = "port: 9321\ndebug:\n  profiling: true\n"
    path.write_text(original)
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(tmp_path),
                                     "--workspace", str(tmp_path), "--no-open", "serve"])
    monkeypatch.setattr("amplifier_web.settings_migration.migrate_settings", lambda *a, **kw: None)
    monkeypatch.setattr("amplifier_web.host.config.load_config", lambda *a, **kw: None)
    configurations = []
    app = object()
    def create_app(*args, server_config, **kwargs):
        configurations.append(server_config)
        return app
    monkeypatch.setattr(server, "create_app", create_app)
    launches = []
    monkeypatch.setattr(web, "run_app", lambda app, **kwargs: launches.append((app, kwargs)))

    cli.main()
    assert configurations[0]["debug"] == {"profiling": True}
    assert launches[0][0] is app
    assert launches[0][1]["port"] == 9321
    assert path.read_text() == original


def test_nonloopback_serve_is_rejected_before_socket_binding(tmp_path, monkeypatch):
    args = Namespace(port=None, workspace=str(tmp_path), no_open=True, bind=["192.0.2.5"], host=None,
                     public_origin=None, tls_cert=None, tls_key=None, session_ttl_seconds=None)
    monkeypatch.setattr(web, "run_app", lambda *args, **kwargs: pytest.fail("unsafe configuration bound a socket"))
    with pytest.raises(ValueError, match="Non-loopback"):
        cli._serve(args, tmp_path)


def test_expected_service_failure_is_a_concise_cli_error(tmp_path, monkeypatch):
    from amplifier_web import deployment_service
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path), 'service', 'install'])
    def fail(*args, **kwargs):
        raise RuntimeError('Definition has changed. Use --replace to save a backup.')
    monkeypatch.setattr(deployment_service, 'install', fail)
    with pytest.raises(SystemExit, match='Service install did not complete:.*--replace') as captured:
        cli.main()
    assert captured.value.__suppress_context__


def test_service_cli_reports_reused_definition_and_backup(tmp_path, monkeypatch, capsys):
    from amplifier_web import deployment_service
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path), 'service', 'install'])
    monkeypatch.setattr(deployment_service, 'install', lambda *a, **kw: {'status': 'current', 'path': '/fixture/service'})
    cli.main()
    assert 'already matches' in capsys.readouterr().out
    monkeypatch.setattr(deployment_service, 'install', lambda *a, **kw: {'status': 'replaced', 'path': '/fixture/service', 'backup': '/fixture/service.backup-1'})
    cli.main()
    assert 'Previous definition saved at: /fixture/service.backup-1' in capsys.readouterr().out


@pytest.mark.parametrize('installed', [False, True])
@pytest.mark.parametrize('public_url', [None, 'https://configured-public.example'])
def test_default_launch_installs_or_starts_and_opens_only_when_ready(tmp_path, monkeypatch, installed, public_url):
    from amplifier_web import deployment_service as service, installation_health as health
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path)])
    monkeypatch.setattr(service, 'managed_launchd', lambda home: installed)
    calls = []
    monkeypatch.setattr(service, 'install', lambda *a: calls.append('install'))
    monkeypatch.setattr(service, 'command', lambda name, data_dir=None: calls.append(name))
    async def ready(home, wait):
        assert wait == 60
        calls.append('ready')
        return {'ok': True, 'url': 'http://127.0.0.1:8941', 'message': 'Ready',
                'publicUrls': [public_url] if public_url else [], 'publicUrlsVerified': False}
    monkeypatch.setattr(health, 'status', ready)
    monkeypatch.setattr(cli.webbrowser, 'open', lambda url: calls.append(url))
    cli.main()
    assert calls == ['start' if installed else 'install', 'ready', public_url or 'http://127.0.0.1:8941']


def test_default_launch_failure_does_not_open_browser(tmp_path, monkeypatch):
    from amplifier_web import deployment_service as service, installation_health as health
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path)])
    monkeypatch.setattr(service, 'managed_launchd', lambda home: True)
    monkeypatch.setattr(service, 'command', lambda name, data_dir=None: None)
    async def unavailable(*a, **kw):
        return {'ok': False, 'message': 'Not ready'}
    monkeypatch.setattr(health, 'status', unavailable)
    monkeypatch.setattr(cli.webbrowser, 'open', lambda url: pytest.fail('Opened unready app'))
    with pytest.raises(SystemExit):
        cli.main()


@pytest.mark.parametrize('ready', [True, False])
def test_service_start_checks_readiness_and_exit_status(tmp_path, monkeypatch, ready, capsys):
    from amplifier_web import deployment_service as service, installation_health as health
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(tmp_path), 'service', 'start'])
    calls = []
    monkeypatch.setattr(service, 'command', lambda name, data_dir=None: calls.append(name))
    async def check(home, wait):
        assert home == tmp_path and wait == 60
        return {'ok': ready, 'message': 'Ready' if ready else 'Still starting'}
    monkeypatch.setattr(health, 'status', check)
    if ready:
        cli.main()
    else:
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 1
    assert calls == ['start']
    assert ('Ready' if ready else 'Still starting') in capsys.readouterr().out


@pytest.mark.parametrize('command', ['start', 'stop', 'restart', 'status', 'logs', 'uninstall'])
def test_service_cli_binds_every_operation_to_selected_data_root(tmp_path, monkeypatch, command):
    from amplifier_web import deployment_service as service, installation_health as health
    from amplifier_web import application_generations, host_dependencies
    monkeypatch.setattr(application_generations, 'delegate', lambda: None)
    monkeypatch.setattr(host_dependencies, 'prepare_environment', lambda: None)
    options = ['--verbose'] if command == 'status' else []
    monkeypatch.setattr(sys, 'argv', [
        'amplifier-unified', '--data-dir', str(tmp_path), 'service', command, *options])
    calls = []
    monkeypatch.setattr(service, 'command',
                        lambda name, data_dir=None: calls.append((name, data_dir)))
    monkeypatch.setattr(service, 'uninstall',
                        lambda data_dir=None: calls.append(('uninstall', data_dir)))
    async def ready(home, wait):
        assert home == tmp_path
        return {'ok': True, 'message': 'Synthetic ready host'}
    monkeypatch.setattr(health, 'status', ready)
    cli.main()
    assert calls == [(command, tmp_path)]


def test_background_launcher_uses_platform_neutral_owned_service_check(tmp_path, monkeypatch):
    from amplifier_web import deployment_service as service, installation_health as health
    calls = []
    monkeypatch.setattr(service, 'managed', lambda home: home == tmp_path)
    monkeypatch.setattr(service, 'managed_unit',
                        lambda *a: pytest.fail('Caller selected the systemd backend itself'))
    monkeypatch.setattr(service, 'managed_launchd',
                        lambda *a: pytest.fail('Caller selected the launchd backend itself'))
    monkeypatch.setattr(service, 'install', lambda *a: pytest.fail('Existing owned definition was ignored'))
    monkeypatch.setattr(service, 'command',
                        lambda name, data_dir=None: calls.append((name, data_dir)))
    async def ready(home, wait):
        assert home == tmp_path and wait == 60
        return {'ok': True, 'message': 'Synthetic ready host'}
    monkeypatch.setattr(health, 'status', ready)
    cli._open_background_service(Namespace(workspace=str(tmp_path), no_open=True), tmp_path)
    assert calls == [('start', tmp_path)]


@pytest.fixture
def windows_cli_transport(monkeypatch):
    """Real caller/backend; only native identity/COM and OS path spelling adapted.

    On POSIX, Path.resolve cannot resolve a Windows drive. The CLI's existing
    address helper is supplied the lexical Windows address here; this does not
    replace the parser, dispatch, root comparison, definition or health checks.
    On native Windows, the real address helper is used unchanged.
    """
    import aiohttp
    from amplifier_web import application_generations, deployment_service, host_dependencies
    from amplifier_web import installation_health, windows_files, windows_service
    root = Path(__file__).resolve().parents[1]
    for module in (cli, deployment_service, windows_service, windows_files):
        assert Path(module.__file__).resolve().is_relative_to(root), 'Wrong source checkout'
    sid = 'S-1-5-21-100-200-300-1001'
    value = windows_service.TaskSpec(
        sid, r'C:\Synthetic\Scripts\python.exe', r'C:\Synthetic\data-a',
        r'C:\Synthetic\workspace', r'C:\Synthetic\state', r'C:\Tools', 'a' * 32)
    state = {'xml': windows_service.task_xml(value), 'state': 4, 'exists': True, 'calls': []}
    monkeypatch.setattr(windows_files, '_API', lambda: SimpleNamespace(sid=sid))
    platform = SimpleNamespace(platform='win32')
    monkeypatch.setattr(deployment_service, 'sys', platform)
    monkeypatch.setattr(windows_service, 'sys', platform)
    monkeypatch.setattr(installation_health, 'sys', platform)
    if sys.platform != 'win32':
        monkeypatch.setattr(cli, '_data_dir', lambda path: PureWindowsPath(path))
    monkeypatch.setattr(application_generations, 'delegate', lambda: None)
    monkeypatch.setattr(host_dependencies, 'prepare_environment', lambda: None)
    monkeypatch.setattr(windows_service, '_powershell', lambda: r'C:\Windows\powershell.exe')
    monkeypatch.setattr(aiohttp.ClientSession, '_request',
                        lambda *a, **kw: pytest.fail('Service status must not send a health bearer'))
    monkeypatch.setattr(cli.webbrowser, 'open', lambda *a: pytest.fail('No browser permitted'))
    def run(argv, **kwargs):
        request = json.loads(kwargs['input'])
        operation = request['op']
        state['calls'].append(operation)
        assert request['sid'] == sid
        if operation != 'query':
            assert request['previous'] == state['xml']
        if operation == 'stop':
            state['state'] = 3
        elif operation == 'start':
            state['state'] = 4
        elif operation == 'remove':
            state['exists'] = False
        else:
            assert operation == 'query', 'No install/replace permitted'
        response = ({'exists': True, 'xml': state['xml'], 'state': state['state'],
                     'instances': int(state['state'] == 4), 'lastResult': 0}
                    if state['exists'] else {'exists': False})
        return SimpleNamespace(returncode=0, stdout=json.dumps(response).encode(), stderr=b'')
    monkeypatch.setattr(windows_service.subprocess, 'run', run)
    return state


@pytest.mark.parametrize('command', ['start', 'stop', 'restart', 'status', 'uninstall'])
@pytest.mark.parametrize('matching', [False, True])
def test_windows_service_cli_real_chain_root_boundary(windows_cli_transport, monkeypatch, command, matching, capsys):
    state = windows_cli_transport
    original = state['xml']
    selected = r'C:\Synthetic\data-a' if matching else r'C:\Synthetic\data-b'
    monkeypatch.setattr(sys, 'argv', [
        'amplifier-unified', '--data-dir', selected, 'service', command,
        *(['--verbose'] if command == 'status' else [])])
    # Native health deliberately cannot report ready, even for the right task.
    exits = not matching or command in {'start', 'restart', 'status'}
    if exits:
        with pytest.raises(SystemExit):
            cli.main()
    else:
        cli.main()
    assert state['xml'] == original
    if not matching:
        assert set(state['calls']) == {'query'}, 'Wrong root reached a task mutation'
        assert state['exists'] and state['state'] == 4
    else:
        mutations = [op for op in state['calls'] if op != 'query']
        assert mutations == {
            'start': ['start'], 'stop': ['stop'], 'restart': ['stop', 'start'],
            'status': [], 'uninstall': ['remove'],
        }[command]
        assert state['exists'] == (command != 'uninstall')
        if command in {'start', 'restart', 'status'}:
            assert 'not readiness' in capsys.readouterr().out


@pytest.mark.parametrize('ambient_argv', [
    pytest.param(['pytest'], id='empty-runner-args'),
    pytest.param(['pytest', '-q', '-p', 'no:cacheprovider'], id='pytest-plugin-args'),
    pytest.param(['pytest', '--unexpected-runner-option'], id='unknown-runner-option'),
])
def test_windows_default_launcher_refuses_before_setup_or_browser(tmp_path, monkeypatch, ambient_argv):
    from amplifier_web import application_generations, deployment, deployment_service, host_dependencies
    monkeypatch.setattr(application_generations, 'delegate', lambda: None)
    monkeypatch.setattr(host_dependencies, 'prepare_environment', lambda: None)
    # Exercise the actual test body independently of the runner/conftest's argv.
    monkeypatch.setattr(sys, 'argv', ambient_argv)
    monkeypatch.setattr(cli, 'sys', SimpleNamespace(
        platform='win32', argv=['amplifier-unified', '--data-dir', str(tmp_path)]))
    # argparse defaults to its own global sys.argv, not the CLI's platform model.
    monkeypatch.setattr(sys, 'argv', cli.sys.argv)
    monkeypatch.setattr(deployment, 'load_server_config',
                        lambda *a, **kw: pytest.fail('Default launcher must refuse before configuration creation'))
    monkeypatch.setattr(deployment_service, 'install', lambda *a: pytest.fail('No automatic Windows setup'))
    monkeypatch.setattr(cli, '_open_background_service', lambda *a: pytest.fail('No default service launch'))
    monkeypatch.setattr(cli.webbrowser, 'open', lambda *a: pytest.fail('No browser'))
    with pytest.raises(SystemExit, match='Windows default launcher remains unavailable'):
        cli.main()
