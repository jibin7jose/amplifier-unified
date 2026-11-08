from pathlib import Path
from types import SimpleNamespace
import os
import plistlib
import shlex
import shutil
import sys

import pytest

from amplifier_web import deployment_service
from amplifier_web.cli import _parse


@pytest.fixture(autouse=True)
def isolated_service_backend(tmp_path, monkeypatch):
    # These cases default to systemd; macOS cases select Darwin explicitly.
    # Never let the machine running the tests choose a real service manager.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(deployment_service, "unit_path", lambda: tmp_path / "unit.service")
    monkeypatch.setattr(deployment_service, "launchd_path", lambda: tmp_path / "agent.plist")
    def unexpected(*args, **kwargs):
        pytest.fail("Service manager calls must be mocked by the test")
    monkeypatch.setattr(deployment_service, "_systemctl", unexpected)
    monkeypatch.setattr(deployment_service, "_launchctl", unexpected)


def test_install_refuses_unmanaged_unit(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text("[Service]\n")
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    with pytest.raises(RuntimeError, match="unmanaged"):
        deployment_service.install(tmp_path / "data")


def test_install_replace_backs_up_unmanaged_unit_only_when_explicit(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text("[Service]\nExecStart=/old/host\n")
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    monkeypatch.setattr(deployment_service.shutil, "which", lambda name: "/usr/bin/amplifier-unified")
    deployment_service.install(tmp_path / "data", replace=True)
    backups = list(tmp_path.glob("amplifier-unified.service.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "[Service]\nExecStart=/old/host\n"
    assert deployment_service.MARKER in unit.read_text()


def test_generated_exec_start_uses_top_level_options_before_serve(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    monkeypatch.setattr(deployment_service.shutil, "which", lambda name: "/usr/bin/amplifier-unified" if name == "amplifier-unified" else None)
    data_dir, workspace = tmp_path / "data", tmp_path / "workspace"
    deployment_service.install(data_dir, workspace)
    # SIGTERM reaches the host first; its final SIGKILL still covers the cgroup.
    assert 'KillMode=mixed\n' in unit.read_text()
    exec_start = next(line.removeprefix("ExecStart=") for line in unit.read_text().splitlines()
                      if line.startswith("ExecStart="))
    command = shlex.split(exec_start)
    assert command == ["/usr/bin/amplifier-unified", "--no-open", "--data-dir", str(data_dir),
                       "--workspace", str(workspace), "serve"]
    monkeypatch.setattr(sys, "argv", command)
    args = _parse()
    assert args.command == "serve"
    assert args.data_dir == str(data_dir)
    assert args.workspace == str(workspace)


def test_install_replace_creates_private_timestamped_backup(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    monkeypatch.setattr(deployment_service.shutil, "which", lambda name: "/usr/bin/amplifier-unified")
    deployment_service.install(tmp_path / "data")
    original = unit.read_bytes()
    assert deployment_service.install(tmp_path / "data")['status'] == 'current'
    unit.write_text(unit.read_text().replace("RestartSec=5", "RestartSec=9"))
    customized = unit.read_bytes()
    with pytest.raises(RuntimeError, match="--replace"):
        deployment_service.install(tmp_path / "data")
    assert unit.read_bytes() == customized
    result = deployment_service.install(tmp_path / "data", replace=True)
    backups = list(tmp_path.glob("amplifier-unified.service.backup-*"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert backups[0].read_bytes() == customized and unit.read_bytes() == original
    assert result['backup'] == str(backups[0]) and result['status'] == 'replaced'


def test_repeat_systemd_install_starts_without_rewrite_backup_or_restart(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(deployment_service.shutil, "which", lambda name: "/usr/bin/amplifier-unified")
    first = deployment_service.install(tmp_path / 'data', tmp_path)
    path = Path(first['path'])
    original, modified = path.read_bytes(), path.stat().st_mtime_ns
    calls.clear()
    result = deployment_service.install(tmp_path / 'data', tmp_path)
    assert result['status'] == 'current'
    assert path.read_bytes() == original and path.stat().st_mtime_ns == modified
    assert not list(tmp_path.glob('*.backup-*'))
    assert calls == [('daemon-reload',), ('enable', deployment_service.UNIT_NAME), ('start', deployment_service.UNIT_NAME)]


def test_uninstall_absent_unit_does_not_disable_service(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deployment_service, "unit_path", lambda: tmp_path / "missing.service")
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: calls.append(args))
    deployment_service.uninstall()
    assert calls == []


def test_uninstall_unmanaged_unit_does_not_disable_service(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text("[Service]\n")
    calls = []
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match="unmanaged"):
        deployment_service.uninstall()
    assert calls == []


def test_uninstall_preserves_unit_when_stop_or_disable_fails(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text(deployment_service.MARKER + "\n[Service]\n")
    calls = []
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    def failing_systemctl(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("systemd user bus is unavailable")
    monkeypatch.setattr(deployment_service, "_systemctl", failing_systemctl)
    with pytest.raises(RuntimeError, match="user bus"):
        deployment_service.uninstall()
    assert unit.exists()
    assert calls == [("disable", "--now", deployment_service.UNIT_NAME)]


def test_current_process_must_have_systemd_invocation_and_our_cgroup(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment_service, "managed_unit", lambda data_dir: True)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert not deployment_service.current_process_is_unit_managed(tmp_path)
    monkeypatch.setenv("INVOCATION_ID", "unit-invocation")
    monkeypatch.setattr(deployment_service.Path, "read_text",
                        lambda path: "0::/user.slice/user-1000.slice/user@1000.service/app.slice/amplifier-unified.service\n")
    assert deployment_service.current_process_is_unit_managed(tmp_path)


def _unit_environment_path(unit):
    assignment = next(line.removeprefix("Environment=") for line in unit.read_text().splitlines()
                      if line.startswith("Environment="))
    # systemd accepts quoted assignments and expands %% to literal %.
    return shlex.split(assignment)[0].removeprefix("PATH=").replace("%%", "%")


@pytest.mark.parametrize("location", ["snap/bin", "custom tools/bin", "literal%h/bin", 'quote"and\\slash/bin'])
def test_installed_service_can_find_uv_from_installer_path(tmp_path, monkeypatch, location):
    uv_dir = tmp_path / location
    uv_dir.mkdir(parents=True)
    uv = uv_dir / "uv"
    # A lexical executable path matters for dispatchers such as /snap/bin/uv.
    dispatcher = tmp_path / "dispatcher"
    dispatcher.write_text("#!/bin/sh\nexit 0\n")
    dispatcher.chmod(0o755)
    uv.symlink_to(dispatcher)
    installer_path = str(uv_dir) + os.pathsep + os.defpath
    monkeypatch.setenv("PATH", installer_path)
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    deployment_service.install(tmp_path / "data", tmp_path)
    service_path = _unit_environment_path(unit)
    assert service_path.startswith(installer_path + os.pathsep)
    assert shutil.which("uv", path=service_path) == str(uv)
    # Exercise the actual runtime discovery, not only the generated text.
    monkeypatch.setenv("PATH", service_path)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "data"))
    from amplifier_web.runtime import RuntimeManager
    assert RuntimeManager()._command()[0] == str(uv)


def test_replacing_service_restarts_it_to_apply_new_environment(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text(deployment_service.MARKER + "\n[Service]\nEnvironment=PATH=/old/bin\n")
    calls = []
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setenv("PATH", "/snap/bin:/usr/bin:/bin")
    deployment_service.install(tmp_path / "data", tmp_path, replace=True)
    assert calls == [("daemon-reload",), ("enable", deployment_service.UNIT_NAME),
                     ("restart", deployment_service.UNIT_NAME)]
    assert _unit_environment_path(unit).startswith("/snap/bin:/usr/bin:/bin:")


def test_missing_path_uses_portable_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PATH", raising=False)
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    deployment_service.install(tmp_path / "data", tmp_path)
    assert set(_unit_environment_path(unit).split(os.pathsep)) >= {"/bin", "/usr/bin", str(Path.home() / ".local/bin"), str(Path.home() / ".cargo/bin")}


@pytest.mark.parametrize("value", ["/bin\n[Service]\nExecStart=/bad", "/bin\r", "/bin\x01"])
def test_path_control_characters_cannot_inject_unit_directives(tmp_path, monkeypatch, value):
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setenv("PATH", value)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: pytest.fail("invalid PATH"))
    with pytest.raises(ValueError, match="PATH"):
        deployment_service.install(tmp_path / "data", tmp_path)
    assert not unit.exists()


@pytest.mark.parametrize("selection", ["default", "xdg", "explicit"])
def test_service_pins_shell_shared_root_before_systemd_changes_environment(tmp_path, monkeypatch, selection):
    monkeypatch.delenv("AMPLIFIER_SESSION_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "owner")
    expected = tmp_path / "owner" / ".local" / "state" / "amplifier" / "sessions"
    if selection == "xdg":
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        expected = tmp_path / "state" / "amplifier" / "sessions"
    elif selection == "explicit":
        expected = tmp_path / 'shared %h "state"'
        monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(expected))
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    deployment_service.install(tmp_path / "data", tmp_path)
    line = next(row.removeprefix("Environment=") for row in unit.read_text().splitlines()
                if row.startswith('Environment="AMPLIFIER_SESSION_STATE_HOME='))
    resolved = shlex.split(line)[0].split("=", 1)[1].replace("%%", "%")
    assert resolved == str(expected)
    assert not expected.exists(), "Resolving the namespace must not create shared state"


def test_shared_state_control_characters_cannot_inject_unit_directives(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "bad\npath"))
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    with pytest.raises(ValueError, match="control characters"):
        deployment_service.install(tmp_path / "data", tmp_path)
    assert not unit.exists()


def _macos_launchctl(monkeypatch, calls):
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "print":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(deployment_service, "_launchctl", run)


def test_macos_install_writes_private_launch_agent_and_bootstraps(tmp_path, monkeypatch):
    plist = tmp_path / "Library" / "LaunchAgents" / "com.microsoft.amplifier-unified.plist"
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(deployment_service, "launchd_path", lambda: plist)
    monkeypatch.setattr(deployment_service.shutil, "which",
                        lambda name: "/opt/homebrew/bin/amplifier-unified" if name == "amplifier-unified" else "/bin/launchctl")
    monkeypatch.setenv("PATH", "/custom/bin:/usr/bin:/bin")
    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "shared-state"))
    _macos_launchctl(monkeypatch, calls)

    data_dir, workspace = tmp_path / "data", tmp_path / "workspace"
    deployment_service.install(data_dir, workspace)

    with plist.open("rb") as stream:
        contents = plistlib.load(stream)
    assert contents["Label"] == deployment_service.LAUNCHD_LABEL
    assert contents["AmplifierUnifiedMarker"] == deployment_service.LAUNCHD_MARKER
    assert contents["ProgramArguments"] == [
        "/opt/homebrew/bin/amplifier-unified", "--no-open", "--data-dir", str(data_dir),
        "--workspace", str(workspace), "serve",
    ]
    assert contents["WorkingDirectory"] == str(workspace)
    assert contents["EnvironmentVariables"]["PATH"].startswith("/custom/bin:/usr/bin:/bin:")
    assert contents["EnvironmentVariables"]["AMPLIFIER_SESSION_STATE_HOME"] == str(tmp_path / "shared-state")
    assert contents["EnvironmentVariables"]["AMPLIFIER_UNIFIED_LAUNCHD_LABEL"] == deployment_service.LAUNCHD_LABEL
    assert Path(contents["StandardOutPath"]).parent == data_dir / "logs"
    assert plist.stat().st_mode & 0o777 == 0o600
    assert ("bootout", f"gui/{os.getuid()}/{deployment_service.LAUNCHD_LABEL}") in [call[0] for call in calls]
    assert ("bootstrap", f"gui/{os.getuid()}", str(plist)) in [call[0] for call in calls]


def test_macos_install_refuses_unmanaged_launch_agent(tmp_path, monkeypatch):
    plist = tmp_path / "agent.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    with plist.open("wb") as stream:
        plistlib.dump({"Label": deployment_service.LAUNCHD_LABEL}, stream)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(deployment_service, "launchd_path", lambda: plist)
    with pytest.raises(RuntimeError, match="unmanaged"):
        deployment_service.install(tmp_path / "data")


@pytest.mark.parametrize('loaded', [True, False])
def test_repeat_macos_install_reuses_definition_and_only_bootstraps_if_unloaded(tmp_path, monkeypatch, loaded):
    calls = []
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(deployment_service.shutil, 'which', lambda name: '/usr/bin/amplifier-unified')
    _macos_launchctl(monkeypatch, calls)
    first = deployment_service.install(tmp_path / 'data', tmp_path)
    path = Path(first['path'])
    original, modified = path.read_bytes(), path.stat().st_mtime_ns
    calls.clear()
    monkeypatch.setattr(deployment_service, '_launchd_loaded', lambda uid: loaded)
    result = deployment_service.install(tmp_path / 'data', tmp_path)
    assert result['status'] == 'current'
    assert path.read_bytes() == original and path.stat().st_mtime_ns == modified
    assert not list(tmp_path.glob('*.backup-*'))
    assert [call[0][0] for call in calls] == ([] if loaded else ['bootstrap'])


def test_changed_macos_definition_is_preserved_until_explicit_private_backup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(deployment_service.shutil, 'which', lambda name: '/usr/bin/amplifier-unified')
    _macos_launchctl(monkeypatch, calls)
    first = deployment_service.install(tmp_path / 'data', tmp_path)
    path = Path(first['path'])
    original = path.read_bytes()
    calls.clear()
    with pytest.raises(RuntimeError, match='definition has changed'):
        deployment_service.install(tmp_path / 'new-data', tmp_path)
    assert path.read_bytes() == original and calls == []
    assert not (tmp_path / 'new-data').exists()
    result = deployment_service.install(tmp_path / 'new-data', tmp_path, replace=True)
    backup = Path(result['backup'])
    assert result['status'] == 'replaced' and backup.read_bytes() == original
    assert backup.stat().st_mode & 0o777 == 0o600


def test_macos_service_command_restarts_launch_agent(tmp_path, monkeypatch):
    plist = tmp_path / "agent.plist"
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(deployment_service, "launchd_path", lambda: plist)
    monkeypatch.setattr(deployment_service, "managed_launchd", lambda data_dir=None: True)
    _macos_launchctl(monkeypatch, calls)

    deployment_service.command("restart")

    commands = [call[0] for call in calls]
    assert commands[0] == ("bootout", f"gui/{os.getuid()}/{deployment_service.LAUNCHD_LABEL}")
    assert ("bootstrap", f"gui/{os.getuid()}", str(plist)) in commands


def test_macos_service_logs_uses_installed_launch_agent_paths(tmp_path, monkeypatch):
    plist = tmp_path / "agent.plist"
    stdout, stderr = tmp_path / "custom-data" / "logs" / "launchd.out.log", tmp_path / "custom-data" / "logs" / "launchd.err.log"
    plistlib.dump({
        "Label": deployment_service.LAUNCHD_LABEL,
        "AmplifierUnifiedMarker": deployment_service.LAUNCHD_MARKER,
        "StandardOutPath": str(stdout),
        "StandardErrorPath": str(stderr),
    }, plist.open("wb"))
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(deployment_service, "launchd_path", lambda: plist)
    monkeypatch.setattr(deployment_service, "managed_launchd", lambda data_dir=None: True)
    monkeypatch.setattr(deployment_service.subprocess, "run",
                        lambda args, **kwargs: calls.append((args, kwargs)))

    deployment_service.command("logs")

    assert calls == [(["tail", "-n", "100", str(stdout), str(stderr)], {"check": False})]


def test_macos_current_process_requires_managed_launch_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(deployment_service, "managed_launchd", lambda data_dir=None: True)
    monkeypatch.delenv("AMPLIFIER_UNIFIED_LAUNCHD_LABEL", raising=False)
    assert not deployment_service.current_process_is_unit_managed(tmp_path)
    monkeypatch.setenv("AMPLIFIER_UNIFIED_LAUNCHD_LABEL", deployment_service.LAUNCHD_LABEL)
    assert deployment_service.current_process_is_unit_managed(tmp_path)


def test_macos_managed_restart_uses_launchctl(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(deployment_service.os, "getuid", lambda: 501)
    assert deployment_service.managed_restart_command() == (
        "launchctl", "kickstart", "-k", "gui/501/" + deployment_service.LAUNCHD_LABEL,
    )


@pytest.mark.parametrize('loaded', [True, False])
def test_macos_start_reuses_registered_job_without_restarting(monkeypatch, loaded):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(deployment_service, 'managed_launchd', lambda: True)
    monkeypatch.setattr(deployment_service, '_launchd_loaded', lambda uid: loaded)
    calls = []
    monkeypatch.setattr(deployment_service, '_launchctl', lambda *a, **kw: calls.append(a) or SimpleNamespace(returncode=0))
    deployment_service.command('start')
    assert calls == ([('kickstart', f'gui/{os.getuid()}/{deployment_service.LAUNCHD_LABEL}')] if loaded else
                     [('bootstrap', f'gui/{os.getuid()}', str(deployment_service.launchd_path()))])


def test_macos_start_keeps_bootstrap_failure_visible(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(deployment_service, 'managed_launchd', lambda: True)
    monkeypatch.setattr(deployment_service, '_launchd_loaded', lambda uid: False)
    monkeypatch.setattr(deployment_service, '_launchctl', lambda *a, **kw: SimpleNamespace(returncode=5, stderr='Input/output error', stdout=''))
    with pytest.raises(RuntimeError, match='Input/output error'):
        deployment_service.command('start')


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_anonymous_download_mode_survives_service_replacement(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(deployment_service, '_systemctl', lambda *a, **kw: None)
    monkeypatch.setattr(deployment_service, '_launchctl', lambda *a, **kw: SimpleNamespace(returncode=1 if a[0] == 'print' else 0))
    monkeypatch.setattr(deployment_service.shutil, 'which', lambda _: '/fixture/amplifier-unified')
    def enabled():
        if platform == 'linux':
            return 'Environment="AMPLIFIER_GITHUB_ACCESS=anonymous"' in (tmp_path / 'unit.service').read_text()
        return plistlib.loads((tmp_path / 'agent.plist').read_bytes())['EnvironmentVariables'].get('AMPLIFIER_GITHUB_ACCESS') == 'anonymous'
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous')
    deployment_service.install(tmp_path / 'data', tmp_path, replace=True)
    assert enabled()
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS')
    deployment_service.install(tmp_path / 'data', tmp_path, replace=True)
    assert enabled()
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'auto')
    deployment_service.install(tmp_path / 'data', tmp_path, replace=True)
    assert not enabled()


def test_windows_dispatch_never_falls_through_to_systemd_or_launchd(tmp_path, monkeypatch):
    from amplifier_web import windows_service
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(windows_service, "install", lambda *a, **kw: calls.append(("install", a, kw)) or {"status": "installed"})
    monkeypatch.setattr(windows_service, "command", lambda *a: calls.append(("command", a)))
    monkeypatch.setattr(windows_service, "uninstall", lambda *a: calls.append(("uninstall", a)))
    monkeypatch.setattr(windows_service, "managed", lambda root: root == tmp_path)
    monkeypatch.setattr(windows_service, "current_process_is_managed", lambda root: False)
    monkeypatch.setattr(windows_service, "managed_restart_command", lambda: ("owned-restart",))
    assert deployment_service.install(tmp_path, tmp_path, replace=True)["status"] == "installed"
    deployment_service.command("stop", tmp_path)
    deployment_service.uninstall(tmp_path)
    assert deployment_service.managed(tmp_path)
    assert not deployment_service.current_process_is_unit_managed(tmp_path)
    assert deployment_service.managed_restart_command() == ("owned-restart",)
    assert calls == [("install", (tmp_path, tmp_path), {"replace": True}),
                     ("command", ("stop", tmp_path)), ("uninstall", (tmp_path,))]
