"""Foreground prototype launcher; copied into a verified bootstrap capsule.

No service, scheduled task, credential copying or automatic trust-store edits.
The normal installed CLI owns migration, TLS initialization, serving and entry.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys
import sysconfig
import time


def connection_args(root: Path) -> list[str]:
    return [
        "--data-dir", str(root / "app"), "--workspace", str(root / "workspace"),
        "--port", "8942", "--bind", "127.0.0.1",
        "--public-origin", "https://127.0.0.1:8942",
    ]


def child_environment(root: Path, receipt: dict) -> dict[str, str]:
    result = dict(os.environ)
    if receipt.get("sourceMode") == "pinned-vcs":
        # Preserve the real Windows profile and the caller's provider credential
        # environment, but do not let package-manager redirects defeat this root.
        for name in list(result):
            if name.upper().startswith(("UV_", "PIP_")) or name.upper() in {
                "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR",
            }:
                del result[name]
        result["UV_NO_CONFIG"] = "1"
    result.update({
        "AMPLIFIER_WEB_HOME": str(root / "app"),
        "AMPLIFIER_HOME": str(root / "shared"),
        "AMPLIFIER_SESSION_STATE_HOME": str(root / "ownership"),
        "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(root / "shared" / "projects"),
        "AMPLIFIER_UNIFIED_IMPORT_HOME": str(root / "shared"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"),
        "UV_PYTHON_BIN_DIR": str(root / "python-bin"),
        "UV_CACHE_DIR": str(root / "cache"),
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "tool-bin"),
        "TEMP": str(root / "temp"), "TMP": str(root / "temp"),
        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    # Absolute retained helpers work after shell/PATH changes. No credentials
    # are persisted here: normal explicit launch environment remains inherited.
    result["PATH"] = os.pathsep.join([
        str(Path(receipt["uv"]).parent), str(Path(receipt["git"]).parent),
        result.get("PATH", ""),
    ])
    return result


def check_installation(root: Path, receipt: dict) -> Path:
    from amplifier_web.windows_files import directory

    if (sys.platform != "win32" or sys.version_info[:2] != (3, 13)
            or struct.calcsize("P") != 8 or sysconfig.get_config_var("Py_GIL_DISABLED")):
        raise RuntimeError("This prototype needs ordinary Windows CPython 3.13.")
    expected = root / "host" / "Scripts" / "python.exe"
    if Path(sys.executable).absolute() != expected.absolute():
        raise RuntimeError("Use this installation's absolute launcher.")
    if receipt["status"] != "prototype" or receipt["schemaVersion"] != 1:
        raise RuntimeError("Prototype installation receipt is not supported.")
    if importlib.metadata.version("amplifier-unified") != receipt["version"]:
        raise RuntimeError("Installed app version does not match the candidate receipt.")
    for relative in ("app", "shared", "ownership", "workspace", "export"):
        with directory(root / relative, owned_root=root, create=False):
            pass
    for name in ("uv", "git"):
        if not Path(receipt[name]).is_absolute() or not Path(receipt[name]).is_file():
            raise RuntimeError("A retained helper is missing; do not resync another install.")
    return root / "host" / "Scripts" / "amplifier-unified.exe"


def run_cli(launcher: Path, args: list[str], env: dict[str, str]) -> bytes:
    # argv lists use subprocess's CRT quoting, not PS5's legacy native binder.
    result = subprocess.run(
        [str(launcher), *args], env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False,
    )
    if result.returncode:
        # Never print arbitrary stderr/stdout: it may include transient grants.
        raise RuntimeError(f"Unified command {args[-1]} did not complete (exit {result.returncode}).")
    return result.stdout


def wait_for_listener(server, ca: Path, *, timeout: float = 90) -> None:
    context = ssl.create_default_context(cafile=str(ca))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("Foreground host exited before HTTPS was ready. Read its visible error.")
        try:
            with socket.create_connection(("127.0.0.1", 8942), timeout=1) as transport:
                with context.wrap_socket(transport, server_hostname="127.0.0.1"):
                    return
        except (OSError, ssl.SSLError):
            time.sleep(0.3)
    raise RuntimeError("HTTPS startup did not finish. Keep the host error; do not stop unrelated listeners.")


def browser_trust_ready() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8942), timeout=2) as transport:
            with ssl.create_default_context().wrap_socket(transport, server_hostname="127.0.0.1"):
                return True
    except (OSError, ssl.SSLError):
        return False


def keep_foreground(server, *, interrupted: bool = False) -> None:
    """Retain custody of the exact console child until real exit, never kill by port."""
    while server.poll() is None:
        try:
            server.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            continue
        except KeyboardInterrupt:
            interrupted = True
            # Both processes share the foreground console. The normal CLI also
            # receives Ctrl+C and owns graceful worker/descendant cleanup.
            print("\nWaiting for ordinary foreground shutdown...", flush=True)
    if server.returncode not in (0, 130, -2, 0xC000013A, -1073741510):
        raise RuntimeError(f"Foreground host exited unexpectedly (exit {server.returncode}).")
    if not interrupted and server.returncode:
        raise RuntimeError(f"Foreground host did not complete normally (exit {server.returncode}).")


def open_owned_browser(launcher: Path, root: Path, env: dict[str, str]) -> bool:
    try:
        run_cli(launcher, [*connection_args(root), "open"], env)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print("Owner browser entry is pending: the normal launcher refused or could not finish. "
              "Keep the visible host and its state; do not bypass that refusal.", flush=True)
        return False
    print("Owner browser open requested. Complete standard AI onboarding in the web UI.", flush=True)
    return True


def listener_present() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8942), timeout=1):
            return True
    except ConnectionRefusedError:
        return False
    except OSError as exc:
        raise RuntimeError("Cannot determine whether the trial listener is already occupied.") from exc


def launch(root: Path, receipt: dict) -> None:
    from amplifier_web.windows_files import directory

    launcher = check_installation(root, receipt)
    env = child_environment(root, receipt)
    scope = ["--data-dir", str(root / "app")]
    print("Experimental Unified foreground host. Keep this window open; Ctrl+C stops it.", flush=True)
    print("Existing installations and trust stores are not changed.", flush=True)
    if listener_present():
        # Reopen only through the existing pinned owner entry. An occupied port
        # does not authorize adoption, reconfiguration, termination or a second host.
        if not browser_trust_ready() or not open_owned_browser(launcher, root, env):
            raise RuntimeError("The occupied trial address could not be reopened as this owned host.")
        print("Existing owned host reopened; stop it from its original foreground window.", flush=True)
        return
    # Configuration uses the real CLI; no server or credential constructors.
    for key, value in (
        ("port", "8942"), ("bind", "[127.0.0.1]"),
        ("public_origins", "[https://127.0.0.1:8942]"),
    ):
        run_cli(launcher, [*scope, "config", "set", key, value], env)
    run_cli(launcher, [*scope, "setup-tls"], env)
    public_ca = run_cli(launcher, [*scope, "setup-tls", "export"], env)
    with directory(root / "export", owned_root=root, create=False) as pinned:
        pinned.publish_private("trial-ca.cer", public_ca, replace=True)
    ca = root / "export" / "trial-ca.cer"
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    fingerprint = x509.load_pem_x509_certificate(public_ca).fingerprint(hashes.SHA256())
    print("Trial CA SHA-256: " + ":".join(f"{byte:02X}" for byte in fingerprint), flush=True)
    print(f"Public CA for review: {ca}", flush=True)
    server = subprocess.Popen([str(launcher), *connection_args(root), "--no-open", "serve"], env=env)
    interrupted = False
    try:
        wait_for_listener(server, ca)
        if not browser_trust_ready():
            print(
                "\nWindows does not trust the trial CA. The browser must not bypass its warning.\n"
                "You may review this certificate and, only if permitted by your device policy,\n"
                "install it into Current User > Trusted Root Certification Authorities yourself.\n"
                "This security-sensitive action trusts certificates signed by this CA.\n"
                "Setup will not import it, elevate, change policy or automate your decision.",
                flush=True,
            )
            answer = input("Type REVIEW to open the public certificate, or Enter to keep the host without browser entry: ")
            if answer == "REVIEW":
                os.startfile(str(ca))
                input("After your own certificate decision, press Enter to recheck trust: ")
        if browser_trust_ready():
            # The existing launcher authenticates the pinned listener before
            # reading/sending its control credential and issues a one-use grant.
            open_owned_browser(launcher, root, env)
        else:
            print("Browser entry is pending trusted HTTPS. No credential was sent by this launcher.", flush=True)
        print("Keep this window open. Ctrl+C stops this foreground host. Relaunch uses the same private state.", flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print("\nWaiting for ordinary foreground shutdown...", flush=True)
    except (OSError, RuntimeError, EOFError, subprocess.SubprocessError):
        if server.poll() is None:
            # Do not silently enter a "shutdown wait" without a shutdown request,
            # or pretend a TLS readiness timeout means the real CLI has stopped.
            print("Startup/browser checks did not complete. The foreground host is still running. "
                  "Press Ctrl+C in this window to request its normal shutdown; retain visible errors.",
                  file=sys.stderr, flush=True)
            keep_foreground(server)
        raise
    keep_foreground(server, interrupted=interrupted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate only; no listener, TLS or browser changes")
    args = parser.parse_args()
    root = Path(__file__).absolute().parent.parent
    try:
        from amplifier_web.windows_files import directory
        with directory(root, owned_root=root, create=False) as pinned:
            receipt = json.loads(pinned.read_private("prototype.json", limit=65536))
        if args.check:
            check_installation(root, receipt)
            print("Prototype installed identity checked; not runtime or browser qualification.")
        else:
            launch(root, receipt)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
        print(
            "Prototype did not complete. Keep this private installation for review; "
            "your previous install was not replaced. Do not bypass TLS/ownership or retry unknown effects.",
            file=sys.stderr, flush=True,
        )
        if not args.check and sys.stdin.isatty():
            try:
                input("Press Enter to close this failed prototype launcher: ")
            except (EOFError, KeyboardInterrupt):
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())