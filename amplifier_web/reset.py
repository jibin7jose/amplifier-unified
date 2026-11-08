"""Dependency-free emergency repair, also runnable with ``python3 reset.py``.

Only named, regenerable installation paths are retired. Unknown/user state is
never classified as disposable. Backups are kept until the owner removes them.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack, suppress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import ssl
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
import urllib.request
import urllib.error

REPOSITORY = "microsoft/amplifier-unified"
REGENERABLE = ("updates", "runtime", "foundation", "source-store")
UNIT = "amplifier-unified.service"
LABEL = "com.microsoft.amplifier-unified"


def find_uv():
    # Keep this small lookup independent of app modules: this file also repairs
    # damaged installations when run directly with the system Python.
    directories = [Path.home() / '.local/bin', Path.home() / '.cargo/bin']
    if sys.platform == 'darwin':
        directories += [Path('/opt/homebrew/bin'), Path('/usr/local/bin')]
    return shutil.which('uv') or shutil.which('uv', path=os.pathsep.join(map(str, directories)))


def options(parser):
    parser.add_argument("--verbose", action="store_true", help="Show technical repair details")
    parser.add_argument("--yes", "-y", action="store_true", help="Confirm the displayed repair plan")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be repaired without changing anything")
    parser.add_argument("--source", help="Override the latest published release with a trusted wheel or Git source")
    parser.add_argument("--no-start", action="store_true", help="Leave the managed service stopped after repair")
    parser.add_argument("--anonymous", action="store_true", help="Download public sources without GitHub sign-in or personal Git configuration")
    # Also accept the customary post-command spelling, retaining a global value.
    parser.add_argument("--data-dir", default=argparse.SUPPRESS, help="Unified data directory to repair")


def home_path(args):
    return Path(getattr(args, "data_dir", None) or os.environ.get("AMPLIFIER_WEB_HOME")
                or os.environ.get("AMPLIFIER_WEB_DATA_DIR") or Path.home() / ".amplifier-unified").expanduser().resolve()


_REPORT = None


class RepairReport:
    def __init__(self, home, verbose=False):
        directory = home / "reset-reports"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / (uuid.uuid4().hex + ".log")
        self.stream = os.fdopen(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w")
        self.verbose = verbose
        self.label = "Working"

    def write(self, text):
        self.stream.write(text + "\n")
        self.stream.flush()
        if self.verbose:
            print(text, flush=True)


def progress(text, *, done=False):
    if _REPORT:
        _REPORT.label = text
        _REPORT.write(("Completed: " if done else "Started: ") + text)
    print(("✓ " if done else "  ") + text, flush=True)


@contextmanager
def report_session(report):
    global _REPORT
    previous, _REPORT = _REPORT, report
    try:
        yield
    finally:
        _REPORT = previous


def user_service_environment():
    """Recover a missing login environment without changing users or services."""
    import stat
    env = os.environ.copy()
    directory = Path('/run/user') / str(os.getuid())
    try:
        info = directory.stat()
        bus = (directory / 'bus').stat()
        if info.st_uid == os.getuid() and bus.st_uid == os.getuid() and stat.S_ISSOCK(bus.st_mode):
            env['XDG_RUNTIME_DIR'] = str(directory)
            env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + str(directory / 'bus')
    except OSError:
        pass
    return env


def service_manager_help():
    import getpass
    import shlex
    return ("Linux's background service manager is unavailable for your account. "
            "Sign in directly as the account that installed Unified and try again. "
            "If this machine runs without a desktop login, an administrator can enable your background services with: "
            "sudo loginctl enable-linger " + shlex.quote(getpass.getuser()) +
            ". Then sign in again and retry. Do not run reset with sudo.")


def command(*args, env=None, capture=False, check=True, timeout=120):
    if Path(str(args[0])).name == "systemctl" and env is None:
        env = user_service_environment()
    collect = capture or _REPORT is not None
    if _REPORT:
        _REPORT.write("Command: " + " ".join(map(str, args)))
    process = subprocess.Popen(list(map(str, args)), env=env, text=True,
                               stdout=subprocess.PIPE if collect else None,
                               stderr=subprocess.PIPE if collect else None,
                               start_new_session=True)
    try:
        started = time.monotonic()
        while True:
            remaining = timeout - (time.monotonic() - started)
            try:
                stdout, stderr = process.communicate(timeout=max(.01, min(10, remaining)))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - started >= timeout:
                    raise
                if _REPORT:
                    elapsed = int(time.monotonic() - started)
                    print(f"  {_REPORT.label}… {elapsed // 60}m {elapsed % 60}s. Still working.", flush=True)
    except BaseException:
        # A timed-out installer must not keep writing after rollback begins.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # The parent can exit while a build child ignores SIGTERM.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    if _REPORT:
        detail = "[Process listing omitted]" if Path(str(args[0])).name == 'ps' else (stdout or '') + (stderr or '')
        _REPORT.write(f"Exit: {process.returncode}\n{detail}")
    result = subprocess.CompletedProcess(list(args), process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def repository_environment(env=None, *, anonymous=False):
    """Scope download policy to children; never edit the user's Git configuration.

    Kept here so the emergency repair script remains dependency-free.
    """
    env = dict(os.environ if env is None else env)
    env["GIT_TERMINAL_PROMPT"] = "0"
    anonymous = anonymous or env.get("AMPLIFIER_GITHUB_ACCESS") == "anonymous"
    if anonymous:
        env["AMPLIFIER_GITHUB_ACCESS"] = "anonymous"
        # Personal config can rewrite public HTTPS URLs to authenticated SSH,
        # inject Authorization headers, or use an account blocked by org policy.
        for key in list(env):
            if key.startswith("GIT_CONFIG_") or key in {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS"}:
                env.pop(key, None)
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        settings = [("credential.helper", ""), ("http.extraHeader", ""),
                    ("http.cookieFile", ""), ("core.askPass", "")]
    else:
        settings = [("credential.https://github.com.helper", "!gh auth git-credential")] if shutil.which("gh") else []
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    for key, value in settings:
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def repository_auth_failure(*details):
    """Classify in memory only; callers must not publish provider error text."""
    text = "\n".join(value.decode(errors="replace") if isinstance(value, bytes) else str(value or "") for value in details).lower()
    return any(marker in text for marker in (
        "saml", "single sign-on", "sso enforcement", "authentication failed", "could not read username",
        "could not read password", "invalid username or token", "permission denied (publickey)",
        "repository not found", "requested url returned error: 403", "requested url returned error: 401",
        "http 403", "http 401", "oauth token access", "terminal prompts disabled",
    ))


def anonymous_retry_environment(argv, env, stdout=b"", stderr=b""):
    """One anonymous retry for a failed GitHub read, never a remote write."""
    current = os.environ if env is None else env
    if current.get("AMPLIFIER_GITHUB_ACCESS") == "anonymous":
        return None
    args = [str(arg) for arg in argv]
    executable = Path(args[0]).name
    details = b"\n".join(value if isinstance(value, bytes) else str(value or "").encode() for value in (stdout, stderr))
    github = "github.com/" in " ".join(args).lower() or b"github.com" in details.lower()
    read = executable == "git" and any(arg in {"ls-remote", "fetch", "clone"} for arg in args[1:]) and not any(arg in {"push", "send-pack"} for arg in args[1:])
    install = executable in {"uv", "uv.exe"} and any(arg in {"install", "sync", "lock"} for arg in args[1:])
    # The no-model preparation child emits only a boolean classification, not
    # exception text. It may wrap Foundation's Git/uv error several times.
    probe = any(Path(arg).name == "update_probe.py" for arg in args[1:])
    if probe:
        for line in details.splitlines():
            if line.startswith(b"AMPLIFIER_UPDATE_PROBE="):
                try:
                    facts = json.loads(line.split(b"=", 1)[1])
                    if facts.get("repositoryAccessFailure") is True:
                        return repository_environment(current, anonymous=True)
                except (ValueError, AttributeError):
                    pass
    if github and (read or install) and repository_auth_failure(details):
        return repository_environment(current, anonymous=True)
    return None


def download_command(*args, env, timeout=120, **kwargs):
    """Repair downloads retry inside the existing backup/rollback boundary."""
    started = time.monotonic()
    try:
        return command(*args, env=env, capture=True, timeout=timeout, **kwargs)
    except subprocess.CalledProcessError as error:
        retry = anonymous_retry_environment(args, env, error.stdout, error.stderr)
        remaining = timeout - (time.monotonic() - started)
        if retry is None or remaining <= 0:
            raise
        progress("GitHub sign-in was rejected; retrying this public download anonymously")
        return command(*args, env=retry, capture=True, timeout=remaining, **kwargs)


def environment(*, anonymous=False):
    env = os.environ.copy()
    # Never inherit a worker's generation, a development Python path, or uv's
    # project overrides into a recovery installation. Keep tool/bin ownership.
    for key in list(env):
        if key.startswith(("PYTHON", "UV_")) and key not in {
            "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_PYTHON_INSTALL_DIR", "UV_NATIVE_TLS",
        }:
            env.pop(key, None)
    for key in ("VIRTUAL_ENV", "AMPLIFIER_UNIFIED_RELEASE", "AMPLIFIER_APP_GENERATION_EXEC"):
        env.pop(key, None)
    env["UV_NO_CONFIG"] = "1"
    env["UV_NO_CACHE"] = "1"
    return repository_environment(env, anonymous=anonymous)


def release_link_metadata(url):
    """Accept only this repository's canonical, stable published release link."""
    prefix = f"https://github.com/{REPOSITORY}/releases/tag/"
    tag = url.removeprefix(prefix)
    if not url.startswith(prefix) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("The public latest-release link did not resolve to a supported release.")
    return {"tag_name": tag, "html_url": url, "draft": False, "prerelease": False}


def public_release_link(timeout=30):
    # GitHub's documented latest-release link is independent of its REST API.
    # Read the redirect target, not HTML, and still resolve the tag to a commit.
    request = urllib.request.Request(f"https://github.com/{REPOSITORY}/releases/latest",
        headers={"User-Agent": "amplifier-unified-reset"}, method="HEAD")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return release_link_metadata(response.geturl())


def download_failure(error):
    """Useful diagnostics without echoing proxy URLs, headers or response bodies."""
    code = getattr(error, "code", None)
    if code:
        headers = getattr(error, "headers", None) or {}
        if code == 429 or (code == 403 and headers.get("X-RateLimit-Remaining") == "0"):
            return f"HTTP {code} (GitHub rate limit reached)"
        return f"HTTP {code}"
    reason = getattr(error, "reason", error)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed"
    if isinstance(reason, ssl.SSLError):
        return "TLS connection failed"
    if isinstance(reason, socket.gaierror):
        return "DNS lookup failed"
    if isinstance(reason, TimeoutError):
        return "request timed out"
    if isinstance(error, ValueError):
        return "invalid release metadata"
    return "network connection failed"


def release_metadata(env):
    request = urllib.request.Request(f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "amplifier-unified-reset"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("Release metadata is too large")
        result = json.loads(data)
        if not isinstance(result, dict) or not result.get("tag_name"):
            raise ValueError("Invalid release metadata")
        return result
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        api_failure = download_failure(error)
        if _REPORT:
            _REPORT.write("Public release API: " + api_failure)
        try:
            return public_release_link()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as link_error:
            link_failure = download_failure(link_error)
            if _REPORT:
                _REPORT.write("Public latest-release link: " + link_failure)
        if env.get("AMPLIFIER_GITHUB_ACCESS") != "anonymous" and shutil.which("gh"):
            try:
                return json.loads(command("gh", "api", f"repos/{REPOSITORY}/releases/latest",
                    env=env, capture=True, timeout=30).stdout)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        raise RuntimeError(f"Could not find the latest public release. Release API: {api_failure}; "
                           f"release link: {link_failure}. Check network/certificate settings, "
                           "or use --source with a trusted release wheel or exact Git revision.") from error


def release_source(env):
    release = release_metadata(env)
    tag = release.get("tag_name", "")
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("No supported published release was found; use --source with a trusted release wheel.")
    remote = f"https://github.com/{REPOSITORY}"
    output = download_command("git", "ls-remote", remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}",
                     env=env, timeout=60).stdout
    rows = dict((ref, sha) for sha, ref in (line.split() for line in output.splitlines()))
    sha = rows.get(f"refs/tags/{tag}^{{}}", rows.get(f"refs/tags/{tag}", ""))
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("The published release could not be resolved to an exact commit.")
    return f"git+{remote}@{sha}"


def service_commands(home):
    """Recognize only our generated service, with an exact data-directory arg."""
    import plistlib
    import shlex

    if sys.platform == "darwin":
        path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
        if not path.exists():
            return None
        data = plistlib.loads(path.read_bytes())
        if data.get("Label") != LABEL or data.get("AmplifierUnifiedMarker") != "amplifier-unified launch agent v1":
            raise ValueError("The launch agent is unmanaged; stop it manually before repairing its installation.")
        args = data.get("ProgramArguments", [])
        stop = ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]
        start = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]
        loaded = ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]
    elif sys.platform == "linux":
        path = Path.home() / ".config/systemd/user" / UNIT
        if not path.exists():
            return None
        text = path.read_text()
        if "# Managed by amplifier-unified; do not edit." not in text:
            raise ValueError("The service is unmanaged; stop it manually before repairing its installation.")
        lines = [line[len("ExecStart="):] for line in text.splitlines() if line.startswith("ExecStart=")]
        args = shlex.split(lines[0]) if len(lines) == 1 else []
        stop = ["systemctl", "--user", "stop", UNIT]
        start = ["systemctl", "--user", "start", UNIT]
        loaded = None
    else:
        raise ValueError("Reset supports Linux, WSL and macOS. Use WSL for Windows installations.")
    if "--data-dir" not in args or args.index("--data-dir") + 1 >= len(args):
        raise ValueError("The service data directory could not be verified.")
    owned = Path(args[args.index("--data-dir") + 1]).expanduser().resolve()
    if owned != home:
        raise ValueError(f"The service uses {owned}. Run reset --data-dir with that directory.")
    return stop, start, loaded


@contextmanager
def repair_lock(home, *, shared=False):
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / ".reset.lock"
    # Never unlink a lock: waiters must continue to agree on the same inode.
    with path.open("a") as stream:
        unlock = None
        try:
            if os.name == "nt":
                unlock = _lock_file_windows(stream, shared=shared)
            else:
                import fcntl
                fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A host or reset is already using this data directory. Stop manually launched hosts before retrying.") from None
        try:
            yield
        finally:
            if unlock is not None:
                unlock()


def _lock_file_windows(stream, *, shared):
    """Lock byte zero on the open file; return its checked unlock operation.

    Keep Win32 imports local and stdlib-only for standalone emergency repair.
    Closing the handle (including process death) also releases its kernel lock.
    """
    import ctypes
    import msvcrt

    class OVERLAPPED(ctypes.Structure):
        # ULONG_PTR and HANDLE are pointer-sized; DWORD stays 32-bit even on
        # Win64. The two offset DWORDs occupy the OVERLAPPED union's eight bytes.
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", ctypes.c_uint32),
            ("OffsetHigh", ctypes.c_uint32),
            ("hEvent", ctypes.c_void_p),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LockFileEx.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(OVERLAPPED),
    ]
    kernel32.LockFileEx.restype = ctypes.c_int32
    kernel32.UnlockFileEx.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.POINTER(OVERLAPPED),
    ]
    kernel32.UnlockFileEx.restype = ctypes.c_int32
    # Python opens non-inheritable descriptors; enforce that before locking so
    # spawned children cannot keep a host's lease alive after the host exits.
    os.set_inheritable(stream.fileno(), False)
    handle = msvcrt.get_osfhandle(stream.fileno())
    overlapped = OVERLAPPED()
    flags = 0x00000001 | (0 if shared else 0x00000002)  # FAIL_IMMEDIATELY | EXCLUSIVE
    if not kernel32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION: only contention is "already using".
            raise BlockingIOError(error, "The repair lock is held")
        raise ctypes.WinError(error)

    def unlock():
        if not kernel32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())

    return unlock


def reset_update_state(home, backup):
    """Retire saved update fences without rewriting chats, resources or receipts."""
    path = home / "app.sqlite3"
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError("The application database is external; refusing to alter it.")
    with sqlite3.connect(path, timeout=5) as db:
        with sqlite3.connect(backup / "app.sqlite3") as saved:
            db.backup(saved)
        (backup / "app.sqlite3").chmod(0o600)
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "state" in tables:
            row = db.execute("SELECT value FROM state WHERE id=1").fetchone()
            if row:
                state = json.loads(row[0])
                state.pop("updates", None)
                db.execute("UPDATE state SET value=? WHERE id=1", (json.dumps(state),))
        if "state_records" in tables:
            db.execute("DELETE FROM state_records WHERE kind='global' AND id='updates'")


def verify(prefix, env):
    python = prefix / "bin/python"
    command(python, "-I", "-c", "from amplifier_web.app_updates import PROBE; exec(PROBE)",
            env=env, timeout=120)


def assert_no_processes(prefix, home):
    """Older hosts/workers predate the shared lease; never remove their files."""
    output = command("ps", "-axo", "pid=,command=", capture=True).stdout
    roots = (str(prefix) + "/", str(home / "runtime") + "/", str(home / "updates/applications") + "/")
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or int(parts[0]) == os.getpid():
            continue
        text = parts[1]
        if any(root in text for root in roots) or (
            "amplifier_web" in text and str(home) in text
        ):
            if _REPORT:
                _REPORT.write(f"Installation still in use by process {parts[0]}")
            raise RuntimeError("Unified is still closing or is running in another terminal. Close that copy and try reset again.")


def stop_service(service, *, timeout=30):
    stop, _, loaded = service
    if loaded and command(*loaded, capture=True, check=False).returncode != 0:
        return False
    if stop[0] == 'systemctl':
        result = command(*stop, capture=True, check=False)
        if result.returncode:
            if any(text in (result.stderr or '').lower() for text in ('connect to bus', 'no medium found', 'bus connection')):
                raise RuntimeError(service_manager_help())
            raise RuntimeError("Unified could not be stopped. No installation files were changed. See the repair report for details.")
    else:
        command(*stop)
    if loaded:
        deadline = time.monotonic() + timeout
        while command(*loaded, capture=True, check=False).returncode == 0:
            if time.monotonic() >= deadline:
                raise RuntimeError("Unified is taking too long to close. No installation files were changed. Wait a moment and try reset again.")
            time.sleep(.25)
    return True


def start_service(service):
    _, start, loaded = service
    # Rollback must not bootstrap a launch agent which is already loaded.
    if loaded and command(*loaded, capture=True, check=False).returncode == 0:
        return
    command(*start)


def wait_until_unused(locks, prefix, home, *, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        try:
            assert_no_processes(prefix, home)
            locks.enter_context(repair_lock(home))
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.25)


def post_checks(prefix, home, *, wait=60):
    """Run both public diagnostics from the newly installed environment."""
    results = {}
    for name, args in (("doctor", ["doctor", "--json"]),
                       ("service status", ["service", "status", "--json", "--wait", str(wait)])):
        progress("Checking installation" if name == "doctor" else "Checking that Unified is responding")
        try:
            result = command(prefix / "bin/python", "-I", "-m", "amplifier_web", "--data-dir", home,
                             *args, env=environment(), capture=True, check=False, timeout=120)
        except (OSError, subprocess.SubprocessError):
            results[name] = {"ok": False, "message": f"The {name} check could not finish. See the repair report for details."}
            continue
        try:
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise ValueError("Unexpected check result")
        except (ValueError, TypeError):
            value = {"ok": False, "message": f"The {name} check could not finish. See the repair report for details."}
        value['ok'] = result.returncode == 0 and value.get('ok') is True
        results[name] = value
        if value['ok']:
            progress("Installation check passed" if name == "doctor" else "Unified is running and responding", done=True)
    return results


def tool_recovery_root(tool_root):
    # uv enumerates children of its tool directory as installed environments.
    # Keep recovery files outside that namespace, on the same filesystem so
    # moving an installation out and restoring it remain atomic renames.
    return tool_root.parent / (tool_root.name + "-amplifier-unified-recovery")


def repair(home, uv, tool_root, bin_root, source, env, service, no_start):
    prefix = tool_root / "amplifier-unified"
    launcher = bin_root / "amplifier-unified"
    backup = home / "reset-backups" / uuid.uuid4().hex
    backup.mkdir(parents=True, mode=0o700)
    recovery = tool_recovery_root(tool_root) / backup.name
    recovery.mkdir(parents=True, mode=0o700)
    tool_backup = recovery / "previous"
    moves = []
    had_launcher = launcher.exists() or launcher.is_symlink()
    # A public launcher may be a symlink or an installer-created script.
    if had_launcher:
        shutil.copy2(launcher, backup / "launcher", follow_symlinks=False)
    (backup / "manifest.json").write_text(json.dumps({
        "dataDir": str(home), "tool": str(prefix), "toolBackup": str(tool_backup),
        "launcher": str(launcher), "regenerable": list(REGENERABLE), "source": source,
    }, indent=2) + "\n")
    if _REPORT:
        _REPORT.write(f"Recovery backup: {backup}\nPrevious installation: {tool_backup}")
    stopped = False
    installing = False
    locks = ExitStack()
    try:
        if service:
            progress("Stopping Unified")
            stopped = stop_service(service)
        wait_until_unused(locks, prefix, home, timeout=30 if service else 0)
        progress("Unified is stopped", done=True)
        if prefix.exists() or prefix.is_symlink():
            prefix.rename(tool_backup)
            moves.append((prefix, tool_backup))
        # An empty target forces a genuinely fresh environment even if receipt
        # metadata or installed package files were manually modified.
        progress("Recovery backup saved", done=True)
        installing = True
        progress("Installing a fresh copy")
        download_command(uv, "tool", "install", "--force", "--python", "3.13", "--from", source, "amplifier-unified",
                env=env, timeout=1800)
        progress("Fresh copy installed", done=True)
        progress("Checking installed components")
        verify(prefix, env)
        progress("Installed components checked", done=True)
        for name in REGENERABLE:
            path = home / name
            if path.exists() or path.is_symlink():
                path.rename(backup / name)
                moves.append((path, backup / name))
        reset_update_state(home, backup)
    except BaseException:
        # Preserve failed candidates too, then put the exact old paths back.
        if installing and (prefix.exists() or prefix.is_symlink()):
            prefix.rename(recovery / "failed")
        for original, saved in reversed(moves):
            saved.rename(original)
        if had_launcher:
            if launcher.exists() or launcher.is_symlink():
                launcher.unlink()
            shutil.copy2(backup / "launcher", launcher, follow_symlinks=False)
        elif launcher.exists() or launcher.is_symlink():
            launcher.unlink()
        locks.close()
        if installing:
            print("The repair could not finish. Your previous installation was restored.")
        if stopped and not no_start:
            try:
                start_service(service)
            except (OSError, subprocess.SubprocessError):
                print("The previous installation also could not restart. See the repair report for details.")
        raise
    locks.close()
    progress("Downloaded components will rebuild when needed", done=True)
    start_error = None
    if not no_start:
        progress("Restarting Unified" if service else "Setting up Unified's background service")
        try:
            if service:
                start_service(service)
            else:
                command(prefix / "bin/python", "-I", "-m", "amplifier_web",
                        "--data-dir", home, "service", "install",
                        env=environment(), timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            start_error = error
    # Always run both checks, even if startup failed or was explicitly skipped.
    results = post_checks(prefix, home, wait=0 if no_start else 60)
    if not results['doctor']['ok']:
        print("\nUnified was reinstalled, but its settings or sign-in setup need attention.")
        for row in results['doctor'].get('checks', []):
            if not row.get('ok'):
                print(row['message'])
        raise RuntimeError("Run amplifier-unified doctor for the next steps. Your data and recovery backup are preserved.")
    if no_start:
        print("\nUnified was reinstalled. It has been left stopped.")
        print("To start it: amplifier-unified service start" if service else
              "To set it up: amplifier-unified service install")
    elif start_error or not results['service status']['ok']:
        raise RuntimeError("Unified was reinstalled, but could not be confirmed ready. Run amplifier-unified service status for guidance. Your data and recovery backup are preserved.")
    else:
        print("\nUnified is ready on this computer.")
        if results['service status'].get('url'):
            print("Host listener: " + results['service status']['url'])
        for url in results['service status'].get('publicUrls', []):
            print("Configured public address (not checked): " + url)
        if results['service status'].get('publicUrls'):
            print("Check DNS, network access and certificate trust on the device you will use.")
    print("Your chats, settings, sign-ins, and files are preserved.")
    print("The first message may take longer while components finish preparing.")
    return backup


def run(args):
    report = None
    try:
        if os.name == "nt":
            raise ValueError("Run reset inside WSL; native Windows hosting is not supported.")
        home = home_path(args)
        if home in {Path.home().resolve(), Path(home.anchor)}:
            raise ValueError("Use a dedicated Unified data directory, not a home or filesystem root.")
        service = service_commands(home)
        print("Amplifier Unified · " + ("Repair preview" if args.dry_run else "Repair"))
        print("\nWe’ll reinstall Unified and rebuild its downloaded components.")
        print("Your chats, settings, sign-ins, and files will be kept.")
        print("A recovery backup will also be saved.")
        if args.source:
            print("Install from: " + args.source)
        else:
            print("Install the latest released version.")
        if service:
            print("Unified will stop temporarily and " + ("stay stopped." if args.no_start else "restart automatically."))
        else:
            print("Close any copy of Unified running in another terminal before continuing.")
        print("The installation and service will be checked after repair.")
        print("This can take a few minutes.")
        if args.dry_run:
            print("\nNothing has changed. To proceed, run:")
            import shlex
            print("  amplifier-unified " + shlex.join([a for a in sys.argv[1:] if a != '--dry-run']) if __package__ else
                  "  python3 " + shlex.join([a for a in sys.argv if a != '--dry-run']))
            return
        if not args.yes:
            if not sys.stdin.isatty():
                raise ValueError("Use --yes to confirm reset in a non-interactive terminal, or --dry-run to preview.")
            if input("\nContinue? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("Reset cancelled.")
                return
        report = RepairReport(home, getattr(args, 'verbose', False))
        with report_session(report):
            try:
                perform(args, home, service)
            except BaseException:
                report.write(traceback.format_exc())
                raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, sqlite3.Error) as error:
        message = str(error)
        if isinstance(error, FileNotFoundError):
            tool = Path(error.filename or '').name
            message = ("GitHub CLI is needed to download this release. Install it and run gh auth login, then try reset again."
                       if tool == 'gh' else "A required file or tool could not be found. See the repair report for details.")
        elif isinstance(error, PermissionError):
            message = "Unified's files could not be accessed. Run reset using the account that installed Unified. See the repair report for details."
        elif isinstance(error, subprocess.SubprocessError):
            executable = Path(str(error.cmd[0])).name if getattr(error, 'cmd', None) else ''
            message = ("GitHub access could not be confirmed. Run gh auth login with an account that can access Amplifier Unified, then try again."
                       if executable == 'gh' else "A repair step could not finish. Check your internet connection and GitHub access, then try again. Technical details are in the repair report.")
        raise SystemExit("\nRepair needs attention. " + message) from None
    finally:
        if report:
            report.stream.close()
            print("\nRepair details and backup locations:\n  " + str(report.path), flush=True)


def perform(args, home, service):
    if os.environ.get("AMPLIFIER_SOURCE_STORE"):
        raise ValueError("Unset AMPLIFIER_SOURCE_STORE for reset. External/shared source stores are not reset.")
    uv = find_uv()
    if not uv:
        raise ValueError("uv is required. Install uv, then run reset again.")
    env = environment(anonymous=getattr(args, "anonymous", False))
    progress("Checking the installation location")
    tool_root = Path(command(uv, "tool", "dir", env=env, capture=True).stdout.strip()).resolve()
    bin_root = Path(command(uv, "tool", "dir", "--bin", env=env, capture=True).stdout.strip()).resolve()
    prefix = tool_root / "amplifier-unified"
    if prefix.is_symlink() or home.is_relative_to(prefix) or prefix.is_relative_to(home):
        raise ValueError("Tool installation and data directory must be separate, without a symlinked tool environment.")
    tool_root.mkdir(parents=True, exist_ok=True)
    bin_root.mkdir(parents=True, exist_ok=True)
    progress("Finding the latest release" if not args.source else "Preparing the selected installation")
    source = args.source or release_source(env)
    if args.source and Path(args.source).expanduser().exists():
        source = str(Path(args.source).expanduser().resolve())
    progress("Installation source found", done=True)
    with repair_lock(tool_recovery_root(tool_root)):
        repair(home, uv, tool_root, bin_root, source, env, service, args.no_start)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    options(parser)
    run(parser.parse_args())
