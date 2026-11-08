"""Per-user service integration for Amplifier Unified."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import shlex
import subprocess
import sys
import time

from .deployment import write_file, write_private

MARKER = "# Managed by amplifier-unified; do not edit."
UNIT_NAME = "amplifier-unified.service"
LAUNCHD_LABEL = "com.microsoft.amplifier-unified"
LAUNCHD_MARKER = "amplifier-unified launch agent v1"


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def launchd_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def managed_unit(data_dir: Path | None = None) -> bool:
    path = unit_path()
    if not path.is_file():
        return False
    content = path.read_text(errors="replace")
    return MARKER in content and (data_dir is None or str(Path(data_dir).expanduser().resolve()) in content)


def managed_launchd(data_dir: Path | None = None) -> bool:
    path = launchd_path()
    try:
        with path.open("rb") as stream:
            contents = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException):
        return False
    if (contents.get("Label") != LAUNCHD_LABEL
            or contents.get("AmplifierUnifiedMarker") != LAUNCHD_MARKER):
        return False
    if data_dir is None:
        return True
    command = contents.get("ProgramArguments", [])
    return str(Path(data_dir).expanduser().resolve()) in command


def _launchd_plist() -> dict:
    try:
        with launchd_path().open("rb") as stream:
            contents = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        raise RuntimeError("Amplifier Unified's launch agent could not be read.") from error
    if (contents.get("Label") != LAUNCHD_LABEL
            or contents.get("AmplifierUnifiedMarker") != LAUNCHD_MARKER):
        raise RuntimeError("Amplifier Unified's generated launch agent is not installed.")
    return contents


def _systemctl(*args: str, check: bool = True):
    if sys.platform != "linux" or not shutil.which("systemctl"):
        raise RuntimeError("This command requires Linux systemd user services.")
    from .reset import user_service_environment
    return subprocess.run(["systemctl", "--user", *args], check=check, env=user_service_environment())


def _launchctl(*args: str, check: bool = True, **kwargs):
    if sys.platform != "darwin" or not shutil.which("launchctl"):
        raise RuntimeError("This command requires macOS launchd user services.")
    return subprocess.run(["launchctl", *args], check=check, **kwargs)


def _backup(path: Path, content: str | bytes) -> Path:
    timestamp = int(time.time())
    for suffix in range(1_000):
        separator = "" if suffix == 0 else f"-{suffix}"
        backup = path.with_suffix(path.suffix + f".backup-{timestamp}{separator}")
        try:
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode() if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        return backup
    raise RuntimeError(f"Could not create a unique backup for {path}")


def _service_values(data_dir: Path, workspace: str | Path | None) -> tuple[list[str], str, str, Path, Path]:
    # The runtime discovers uv by PATH, just like the installer's shell. A
    # fixed short PATH loses Snap, Homebrew, or custom installs. Preserve
    # lexical paths: /snap/bin/uv is a dispatcher, not its resolved target.
    from .host_dependencies import tool_path
    service_path = tool_path()
    if any(ord(char) < 32 or ord(char) == 127 for char in service_path):
        raise ValueError("PATH must not contain control characters.")
    # Freeze the installer's resolved root. A user service need not inherit the
    # shell's XDG_STATE_HOME, and two roots would mean two unrelated locks.
    from .shared_state import shared_state_home
    state_home = str(shared_state_home())
    if any(ord(char) < 32 or ord(char) == 127 for char in state_home):
        raise ValueError("Shared session state path must not contain control characters.")
    data_dir = Path(data_dir).expanduser().resolve()
    workspace = Path(workspace or Path.cwd()).expanduser().resolve()
    executable = shutil.which("amplifier-unified")
    command = [executable] if executable else [sys.executable, "-m", "amplifier_web"]
    command += ["--no-open", "--data-dir", str(data_dir), "--workspace", str(workspace), "serve"]
    return command, service_path, state_home, data_dir, workspace


def _install_systemd(data_dir: Path, workspace: str | Path | None, *, replace: bool) -> dict:
    command, service_path, state_home, _, _ = _service_values(data_dir, workspace)
    # Escape systemd's quoted assignment and percent specifiers, not shell syntax.
    service_path = service_path.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    state_home = state_home.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    path = unit_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        previous = path.read_text()
    except FileNotFoundError:
        previous = None
    access = os.environ.get("AMPLIFIER_GITHUB_ACCESS")
    anonymous_line = 'Environment="AMPLIFIER_GITHUB_ACCESS=anonymous"'
    anonymous = access == "anonymous" or (access is None and anonymous_line in (previous or ""))
    access_line = "\n" + anonymous_line if anonymous else ""
    content = f"""{MARKER}
[Unit]
Description=Amplifier Unified
After=network.target

[Service]
Type=simple
ExecStart={" ".join(shlex.quote(item) for item in command)}
Restart=on-failure
RestartSec=5
KillMode=mixed
Environment="PATH={service_path}"
Environment="AMPLIFIER_SESSION_STATE_HOME={state_home}"{access_line}

[Install]
WantedBy=default.target
"""
    if previous == content and not replace:
        _systemctl("daemon-reload")
        _systemctl("enable", UNIT_NAME)
        _systemctl("start", UNIT_NAME)
        return {"status": "current", "path": str(path)}
    backup = None
    if previous is not None:
        if MARKER not in previous and not replace:
            raise RuntimeError(f"Refusing to replace unmanaged unit: {path}. Review it first; --replace saves a backup before replacement.")
        if not replace:
            raise RuntimeError(f"The generated service definition has changed: {path}. Re-run the same install command with --replace to back it up and replace it.")
        backup = _backup(path, previous)
    write_private(path, content)
    _systemctl("daemon-reload")
    _systemctl("enable", UNIT_NAME)
    # enable --now leaves an already-running host's old environment in place.
    _systemctl("restart", UNIT_NAME)
    return {"status": "replaced" if previous is not None else "installed", "path": str(path),
            **({"backup": str(backup)} if backup else {})}


def _launchd_loaded(uid: int) -> bool:
    return _launchctl("print", f"gui/{uid}/{LAUNCHD_LABEL}", check=False,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _launchd_bootout(uid: int) -> None:
    _launchctl("bootout", f"gui/{uid}/{LAUNCHD_LABEL}", check=False,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while _launchd_loaded(uid):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"launchd did not stop {LAUNCHD_LABEL}; refusing to start a second host.")
        time.sleep(0.25)


def _launchd_bootstrap(uid: int) -> None:
    result = _launchctl("bootstrap", f"gui/{uid}", str(launchd_path()), check=False,
                        capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"launchctl could not start {LAUNCHD_LABEL}"
            + (f": {detail}" if detail else "")
            + f"\nCheck the service with: launchctl print gui/{uid}/{LAUNCHD_LABEL}"
        )


def _launchd_logs(data_dir: Path) -> tuple[Path, Path]:
    directory = data_dir / "logs"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    stdout, stderr = directory / "launchd.out.log", directory / "launchd.err.log"
    for path in (stdout, stderr):
        if not path.exists():
            write_file(path, "", mode=0o600)
        else:
            os.chmod(path, 0o600)
    return stdout, stderr


def _install_launchd(data_dir: Path, workspace: str | Path | None, *, replace: bool) -> dict:
    command, service_path, state_home, data_dir, workspace = _service_values(data_dir, workspace)
    path = launchd_path()
    try:
        previous = path.read_bytes()
    except FileNotFoundError:
        previous = None
    stdout, stderr = data_dir / "logs/launchd.out.log", data_dir / "logs/launchd.err.log"
    contents = {
        "Label": LAUNCHD_LABEL,
        "AmplifierUnifiedMarker": LAUNCHD_MARKER,
        "ProgramArguments": command,
        "WorkingDirectory": str(workspace),
        "EnvironmentVariables": {
            "PATH": service_path,
            "AMPLIFIER_SESSION_STATE_HOME": state_home,
            "AMPLIFIER_UNIFIED_LAUNCHD_LABEL": LAUNCHD_LABEL,
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(stdout),
        "StandardErrorPath": str(stderr),
    }
    current = None
    if previous is not None:
        try:
            current = plistlib.loads(previous)
        except (ValueError, plistlib.InvalidFileException):
            pass
    access = os.environ.get("AMPLIFIER_GITHUB_ACCESS")
    previous_access = (current or {}).get("EnvironmentVariables", {}).get("AMPLIFIER_GITHUB_ACCESS")
    if access == "anonymous" or (access is None and previous_access == "anonymous"):
        contents["EnvironmentVariables"]["AMPLIFIER_GITHUB_ACCESS"] = "anonymous"
    uid = os.getuid()
    if current == contents and not replace:
        if not _launchd_loaded(uid):
            _launchd_logs(data_dir)
            _launchd_bootstrap(uid)
        return {"status": "current", "path": str(path)}
    backup = None
    if previous is not None:
        if not managed_launchd() and not replace:
            raise RuntimeError(f"Refusing to replace unmanaged launch agent: {path}. Review it first; --replace saves a backup before replacement.")
        if not replace:
            raise RuntimeError(f"The generated launch agent definition has changed: {path}. Re-run the same install command with --replace to back it up and replace it.")
        backup = _backup(path, previous)
    _launchd_logs(data_dir)
    write_private(path, plistlib.dumps(contents, sort_keys=False))
    _launchd_bootout(uid)
    _launchd_bootstrap(uid)
    return {"status": "replaced" if previous is not None else "installed", "path": str(path),
            **({"backup": str(backup)} if backup else {})}


def install(data_dir: Path, workspace: str | Path | None = None, *, replace: bool = False) -> dict:
    if sys.platform == "win32":
        from . import windows_service
        return windows_service.install(data_dir, workspace, replace=replace)
    if sys.platform == "darwin":
        return _install_launchd(data_dir, workspace, replace=replace)
    return _install_systemd(data_dir, workspace, replace=replace)


def _uninstall_systemd() -> None:
    path = unit_path()
    if not path.exists():
        return
    if not managed_unit():
        raise RuntimeError(f"Refusing to remove unmanaged unit: {path}")
    _systemctl("disable", "--now", UNIT_NAME)
    path.unlink(missing_ok=True)
    _systemctl("daemon-reload")


def _uninstall_launchd() -> None:
    path = launchd_path()
    if not path.exists():
        return
    if not managed_launchd():
        raise RuntimeError(f"Refusing to remove unmanaged launch agent: {path}")
    _launchd_bootout(os.getuid())
    path.unlink(missing_ok=True)


def uninstall(data_dir: Path | None = None) -> None:
    if sys.platform == "win32":
        from . import windows_service
        windows_service.uninstall(data_dir)
    elif sys.platform == "darwin":
        _uninstall_launchd()
    else:
        _uninstall_systemd()


def current_process_is_unit_managed(data_dir: Path) -> bool:
    """Prove this process, rather than merely the installation, belongs to our unit."""
    if sys.platform == "win32":
        from . import windows_service
        return windows_service.current_process_is_managed(data_dir)
    if sys.platform == "darwin":
        return (os.environ.get("AMPLIFIER_UNIFIED_LAUNCHD_LABEL") == LAUNCHD_LABEL
                and managed_launchd(data_dir))
    if not os.environ.get("INVOCATION_ID") or not managed_unit(data_dir):
        return False
    try:
        cgroups = Path("/proc/self/cgroup").read_text().splitlines()
    except OSError:
        return False
    return any(UNIT_NAME in line.rsplit(":", 1)[-1].split("/") for line in cgroups)


def managed_restart_command() -> tuple[str, ...]:
    if sys.platform == "win32":
        from . import windows_service
        return windows_service.managed_restart_command()
    if sys.platform == "darwin":
        return ("launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}")
    return ("systemctl", "--user", "--no-block", "restart", UNIT_NAME)


def command(name: str, data_dir: Path | None = None) -> None:
    if sys.platform == "win32":
        from . import windows_service
        return windows_service.command(name, data_dir)
    if sys.platform == "darwin":
        if not managed_launchd():
            raise RuntimeError("Amplifier Unified's generated launch agent is not installed.")
        uid = os.getuid()
        if name == "start":
            if _launchd_loaded(uid):
                # Without -k, kickstart leaves a running process alone and
                # starts a registered job which is currently stopped.
                _launchctl("kickstart", f"gui/{uid}/{LAUNCHD_LABEL}")
            else:
                _launchd_bootstrap(uid)
        elif name == "stop":
            _launchd_bootout(uid)
        elif name == "restart":
            _launchd_bootout(uid)
            _launchd_bootstrap(uid)
        elif name == "status":
            _launchctl("print", f"gui/{uid}/{LAUNCHD_LABEL}", check=False)
        elif name == "logs":
            contents = _launchd_plist()
            stdout, stderr = (Path(contents["StandardOutPath"]),
                              Path(contents["StandardErrorPath"]))
            subprocess.run(["tail", "-n", "100", str(stdout), str(stderr)], check=False)
        else:
            raise ValueError(f"Unknown service command: {name}")
        return
    if not managed_unit():
        raise RuntimeError("Amplifier Unified's generated service is not installed.")
    if name == "start":
        _systemctl("start", UNIT_NAME)
    elif name == "stop":
        _systemctl("stop", UNIT_NAME)
    elif name == "restart":
        _systemctl("restart", UNIT_NAME)
    elif name == "status":
        _systemctl("status", UNIT_NAME, "--no-pager", check=False)
    elif name == "logs":
        if sys.platform != "linux" or not shutil.which("systemctl") or not shutil.which("journalctl"):
            raise RuntimeError("This command requires Linux systemd user services and journalctl.")
        subprocess.run(["journalctl", "--user", "-u", UNIT_NAME, "--no-pager"], check=False)
    else:
        raise ValueError(f"Unknown service command: {name}")


def managed(data_dir: Path | None = None) -> bool:
    """Platform-neutral caller seam; inspection never registers or starts."""
    if sys.platform == "win32":
        from . import windows_service
        return windows_service.managed(data_dir)
    return managed_launchd(data_dir) if sys.platform == "darwin" else managed_unit(data_dir)
