"""Terminal launcher and deployment operations; bundled assets need no Node."""
from __future__ import annotations

import argparse
import asyncio
import errno
import os
import sys
from pathlib import Path
import threading
import webbrowser

from . import __version__
from . import browser_entry


def _data_dir(value: str | None) -> Path:
    from .host.config import app_home
    if value and browser_entry.is_windows():
        from .windows_files import address
        return address(value)
    return Path(value).expanduser().resolve() if value else app_home()


def _connection_options(parser):
    parser.add_argument("--port", type=int)
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--data-dir")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    binding = parser.add_mutually_exclusive_group()
    binding.add_argument("--bind", action="append", default=None, help="Address to bind (may be repeated)")
    binding.add_argument("--host", help="Alias for one --bind address")
    parser.add_argument("--public-origin", action="append", default=None, help="Exact public HTTP(S) origin (may be repeated)")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--session-ttl", dest="session_ttl_seconds", type=int)


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="amplifier-unified")
    _connection_options(parser)
    parser.add_argument("--version", action="version", version="amplifier-unified " + __version__)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("serve", help="Run the authenticated web host")
    subcommands.add_parser("open", help="Open an already running owned web host")
    for name in ("run", "continue"):
        command = subcommands.add_parser(name, help="Run one task using the shared Amplifier host")
        command.add_argument("prompt", nargs="?", default="")
        command.add_argument("--resume")
        command.add_argument("--bundle", "-B")
        command.add_argument("--provider", "-p")
        command.add_argument("--model", "-m")
        command.add_argument("--max-tokens", type=int)
        command.add_argument("--output-format", choices=["text", "json", "json-trace"], default="text")
        command.add_argument("--timeout", type=int, default=3600)
        _access_options(command)
    command = subcommands.add_parser("tool", help="Invoke one configured tool with normal policy checks")
    command.add_argument("name")
    command.add_argument("--args", default="{}")
    command.add_argument("--bundle", "-B")
    command.add_argument("--resume")
    command.add_argument("--timeout", type=int, default=3600)
    _access_options(command)
    access = subcommands.add_parser("access", help="Read or save standing access without starting work")
    access.add_argument("mode", nargs="?", choices=["status", "ask", "full", "inherit"], default="status")
    access.add_argument("--scope", choices=["app", "workspace", "session"], default="app")
    access.add_argument("--session", help="Exact chat ID; required for workspace or session scope")
    access.add_argument("--json", action="store_true")
    tui = subcommands.add_parser("tui", help="Attach the optional terminal client to an existing service")
    tui.add_argument('tui_command', nargs='?', choices=['open', 'install', 'status'], default='open')
    tui.add_argument('--setup-file', help='Install a private setup file downloaded from the service setup page')
    tui.add_argument("--server", default=os.environ.get("AMPLIFIER_UNIFIED_URL"))
    for flag in ("token-file", "ca-file", "client", "state-dir"):
        tui.add_argument("--" + flag)
    tui.add_argument("--workspace", dest="tui_workspace", help="Workspace path on the host (defaults to the launch directory)")
    selection = tui.add_mutually_exclusive_group()
    selection.add_argument("--session", help="Native CLI or host conversation ID, unique prefix, or latest")
    selection.add_argument("--resume", nargs="?", const="", metavar="ID",
                           help="Open this directory's resume picker, or resume an ID or unique prefix")
    selection.add_argument("--new", action="store_true")
    tui.add_argument("--list-sessions", action="store_true")
    config = subcommands.add_parser("config", help="Manage server configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("list")
    get = config_commands.add_parser("get"); get.add_argument("key")
    set_value = config_commands.add_parser("set"); set_value.add_argument("key"); set_value.add_argument("value")
    reset = config_commands.add_parser("reset"); reset.add_argument("key", nargs="?", default="all")
    service = subcommands.add_parser("service", help="Manage the systemd or launchd user service")
    service.add_argument("service_command", choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"])
    service.add_argument("--replace", action="store_true", help="Back up and replace an existing generated unit (install only)")
    setup_tls = subcommands.add_parser("setup-tls", help="Create or inspect the app-owned local CA")
    setup_tls.add_argument("mode", nargs="?", choices=["status", "default", "force", "export"], default="default")
    reset = subcommands.add_parser("reset", help="Reinstall a clean app and runtime while preserving user data")
    from .reset import options
    options(reset)
    doctor = subcommands.add_parser("doctor", help="Check installation and settings")
    for diagnostic in (doctor, service):
        diagnostic.add_argument("--verbose", action="store_true", help="Show technical details")
        diagnostic.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    service.add_argument("--wait", type=int, choices=range(0, 121), default=0, metavar="SECONDS", help="Wait up to this many seconds for service readiness (status only)")
    completion = subcommands.add_parser("completion", help="Print shell completion setup")
    completion.add_argument("shell", choices=["bash", "zsh", "fish"])
    args = parser.parse_args()
    if args.command == "access" and args.scope != "app" and not args.session:
        parser.error("access --scope workspace/session requires --session ID")
    return args


def _access_options(parser):
    access = parser.add_mutually_exclusive_group()
    access.add_argument("--access", choices=["ask", "full", "inherit"],
                        help="Save this task's access preference before starting work")
    access.add_argument("--yolo", dest="access", action="store_const", const="full",
                        help="Accept the risks of Full access for this task; persists until changed")


def _server_overrides(args: argparse.Namespace) -> dict:
    """Translate top-level serving options without replacing nested TLS settings."""
    overrides = {
        "port": args.port,
        "public_origins": args.public_origin,
        "session_ttl_seconds": args.session_ttl_seconds,
    }
    if args.bind is not None:
        overrides["bind"] = args.bind
    elif args.host is not None:
        overrides["bind"] = [args.host]
    tls = {key: value for key, value in {
        "cert": args.tls_cert,
        "key": args.tls_key,
    }.items() if value is not None}
    if args.tls_cert is not None and args.tls_key is not None:
        tls["method"] = "ca"
    if tls:
        overrides["tls"] = tls
    return {key: value for key, value in overrides.items() if value is not None}


def _print_completion(shell: str) -> None:
    words = "serve open tui run continue tool access doctor reset config service setup-tls completion --port --workspace --data-dir --no-open --bind --host --public-origin --tls-cert --tls-key --session-ttl --version"
    if shell == "bash":
        print('complete -W "' + words + '" amplifier-unified')
    elif shell == "zsh":
        print('#compdef amplifier-unified\n_arguments "1:command:(serve open tui run continue tool access doctor reset config service setup-tls)"')
    else:
        print('complete -c amplifier-unified -f -a "serve open tui run continue tool access doctor reset config service setup-tls"')


def _config(args, data_dir: Path) -> None:
    import yaml
    from .deployment import DEFAULT_SERVER, load_server_config, save_server_config, setting_get, setting_set
    config = load_server_config(data_dir)
    if args.config_command == "list":
        print(yaml.safe_dump(config, sort_keys=False), end="")
    elif args.config_command == "get":
        value = setting_get(config, args.key)
        if isinstance(value, (dict, list)):
            print(yaml.safe_dump(value, sort_keys=False), end="")
        else:
            print(value)
    elif args.config_command == "set":
        save_server_config(data_dir, setting_set(config, args.key, yaml.safe_load(args.value)))
    else:
        if args.key == "all":
            save_server_config(data_dir, DEFAULT_SERVER)
        else:
            save_server_config(data_dir, setting_set(config, args.key, setting_get(DEFAULT_SERVER, args.key)))


def _doctor(data_dir: Path) -> None:
    from .installation_health import doctor, display
    result = doctor(data_dir)
    display(result)
    if not result['ok']:
        raise SystemExit(1)


def _doctor_details(data_dir: Path) -> None:
    from .deployment import load_server_config
    config = load_server_config(data_dir)
    try:
        import pam  # noqa: F401
        pam_state = "available"
    except ImportError:
        pam_state = "unavailable (install python-pam and the system PAM library)"
    from .tls import ca_bytes, ca_fingerprint, tls_ready
    print(f"Amplifier Unified {__version__}")
    print(f"Configuration: {data_dir / 'config' / 'server.yaml'}")
    print("PAM:", pam_state)
    print("TLS local CA:", "configured" if ca_bytes(data_dir) else "not configured")
    print("TLS listener:", "ready" if tls_ready(data_dir, config) else "not configured or not startable")
    print("CA SHA-256 fingerprint:", ca_fingerprint(data_dir) or "not configured")
    print("Binds:", ", ".join(config["bind"]))
    print("Port:", config["port"])
    from .shared_state import shared_state_home
    print("Shared session state:", shared_state_home())
    print("Standalone hosts must share the state root and canonical workspace. Connected clients use the service API.")


def _setup_tls(data_dir: Path, mode: str) -> None:
    from .tls import ca_bytes, ca_fingerprint, exported_ca_bytes, setup_local_ca, tls_ready
    if mode == "export":
        import sys
        sys.stdout.buffer.write(exported_ca_bytes(data_dir))
        return
    from .deployment import load_server_config
    config = load_server_config(data_dir)
    if mode == "status":
        print("Local CA:", "configured" if ca_bytes(data_dir) else "not configured")
        print("TLS:", config["tls"]["method"])
        print("TLS listener:", "ready" if tls_ready(data_dir, config) else "not configured or not startable")
        print("CA SHA-256 fingerprint:", ca_fingerprint(data_dir) or "not configured")
        return
    setup_local_ca(data_dir, config, force=mode == "force")
    print("Created app-owned local CA and leaf certificate. Restart the service to use HTTPS.")
    print("CA SHA-256 fingerprint:", ca_fingerprint(data_dir))


def _serve(args, data_dir: Path) -> None:
    from .reset import repair_lock
    with repair_lock(data_dir, shared=True):
        _serve_unlocked(args, data_dir)


def _serve_unlocked(args, data_dir: Path) -> None:
    from aiohttp import web
    from .deployment import load_server_config
    overrides = _server_overrides(args)
    config = load_server_config(data_dir, overrides=overrides)
    os.environ["AMPLIFIER_WEB_HOME"] = str(data_dir)
    from .settings_migration import migrate_settings
    migrate_settings(data_dir, [args.workspace])
    from .host.config import load_config
    load_config(args.workspace)
    from .server import create_app
    from .tls import ssl_context
    secure = ssl_context(data_dir, config)
    scheme = "https" if secure else "http"
    browser_host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(config['bind'][0], config['bind'][0])
    if ':' in browser_host:
        browser_host = f'[{browser_host}]'
    url = (config["public_origins"][0] if config["public_origins"] else
           f"{scheme}://{browser_host}:{config['port']}")
    timer = None
    listener_ready = False

    def listening(_message):
        # aiohttp calls this only after every configured socket has bound.
        # Application startup signals run earlier and cannot establish this.
        nonlocal timer, listener_ready
        listener_ready = True
        print(f"Amplifier Unified · listening on {', '.join(config['bind'])}, port {config['port']} ({scheme})\n"
              f"Workspace: {args.workspace}\nState: {data_dir}")
        if config['public_origins']:
            for origin in config['public_origins']:
                print(f"Configured public address (not checked): {origin}")
        else:
            print(f"Open: {url}")
        if not args.no_open:
            timer = threading.Timer(1.5, lambda: _open_browser_safely(args, data_dir, config)
                                    if browser_entry.is_windows() else webbrowser.open(url))
            timer.daemon = True
            timer.start()

    from .mcp_oauth import SafeAccessLogger
    from amplifier_publishing import PublishingError
    try:
        web.run_app(create_app(data_dir, workspace=args.workspace, server_config=config),
                    host=config["bind"], port=config["port"], ssl_context=secure, print=listening, access_log_class=SafeAccessLogger, access_log_format='%a %t "%r" %s %b')
    except OSError as exc:
        guidance = {
            errno.EADDRINUSE: 'The address or port is already in use. Check the existing service with amplifier-unified service status, or choose another port with --port.',
            errno.EADDRNOTAVAIL: 'The configured bind address is not available on this computer. Check its current network addresses and your bind setting.',
            errno.EACCES: 'Permission was denied. Check permission to use the configured address and port; choose an allowed port with --port if needed.',
        }.get(exc.errno)
        # A filesystem error during application startup is not a bind failure.
        if listener_ready or guidance is None or exc.filename is not None:
            raise
        raise SystemExit(
            f"Could not start Amplifier on {', '.join(config['bind'])}, port {config['port']}.\n"
            f"{guidance}\nNo running process was stopped or replaced."
        ) from None
    except PublishingError as exc:
        # The same library error can also mean unsupported OS locking. Only an
        # actual held-lock conflict establishes that another process owns it.
        if exc.code != 'root_busy' or not isinstance(exc.__cause__, BlockingIOError):
            raise
        from shlex import quote
        raise SystemExit(
            f"This Amplifier state folder is already in use: {data_dir}\n"
            f"Configured address: {url}\n"
            f"Check the existing service with amplifier-unified --data-dir {quote(str(data_dir))} service status. "
            "No running process was stopped or replaced."
        ) from None
    finally:
        if timer is not None:
            timer.cancel()


def _open_browser(args, data_dir, config):
    if args.no_open:
        print("Browser opening disabled; no browser grant was issued.")
        return
    if browser_entry.is_windows():
        url = asyncio.run(browser_entry.owner_browser_url(data_dir, config))
    else:
        from types import SimpleNamespace
        from .update_readiness import probe_targets
        urls, _ = probe_targets(SimpleNamespace(home=data_dir, service=SimpleNamespace(
            server_config=config, port=config["port"])))
        url = (config["public_origins"] or [urls[0].removesuffix("/api/health")])[0]
    try:
        opened = webbrowser.open(url)
    except Exception:
        # Some browser backends include the URL (and grant) in exceptions.
        raise browser_entry.BrowserEntryUnavailable("Could not open the browser. " + browser_entry.REOPEN) from None
    if not opened:
        raise browser_entry.BrowserEntryUnavailable("The browser did not accept the open request. " + browser_entry.REOPEN)


def _open_browser_safely(args, data_dir, config):
    try:
        _open_browser(args, data_dir, config)
    except (OSError, ValueError, RuntimeError):
        print("Owner browser entry is unavailable. Verify private storage and the owned local host, then run amplifier-unified open.", file=sys.stderr)


def _tui(args, data_dir):
    from .terminal_cli import install, saved
    if args.tui_command == 'install':
        return install(args, data_dir)
    if args.setup_file:
        raise ValueError('--setup-file is only valid with tui install.')
    managed = saved()
    if args.tui_command == 'status':
        print('Terminal connected to ' + managed['server'] if managed else 'No managed terminal installation. Run amplifier-unified tui install.')
        return
    # Connected Terminal uses this scope for Resume, latest and paged listings.
    args.tui_workspace = args.tui_workspace or str(Path.cwd().resolve())
    if managed:
        import subprocess
        options = []
        if not args.server:
            args.server = managed['server']
            args.token_file = args.token_file or managed['tokenFile']
            args.ca_file = args.ca_file or managed.get('caFile')
        for key in ('server', 'token_file', 'ca_file', 'client', 'state_dir', 'session'):
            if getattr(args, key):
                options += ['--' + key.replace('_', '-'), str(getattr(args, key))]
        if args.tui_workspace:
            options += ['--workspace', args.tui_workspace]
        if args.resume is not None:
            options += ['--resume'] + ([args.resume] if args.resume else [])
        for key in ('new', 'list_sessions'):
            if getattr(args, key):
                options += ['--' + key.replace('_', '-')]
        print('Connecting to ' + args.server, flush=True)
        raise SystemExit(subprocess.call([str(Path(managed['environment']) / 'bin/python'), '-m', 'amplifier_tui.connected', *options]))
    try:
        from amplifier_tui.connected import main as launch
    except ModuleNotFoundError as exc:
        if exc.name not in {"amplifier_tui", "amplifier_tui.connected"}:
            raise
        raise SystemExit("Terminal client is optional. Run amplifier-unified tui install, or open /setup/terminal on your Unified service.") from None
    options = []
    server = args.server
    if not server:
        from .deployment import load_server_config
        config = load_server_config(data_dir)
        secure = config["tls"]["method"] != "none"
        server = f"{'https' if secure else 'http'}://127.0.0.1:{config['port']}"
        if not args.token_file and not os.environ.get("AMPLIFIER_UNIFIED_TOKEN"):
            options += ["--token-file", str(data_dir / "config/auth/control-token")]
        ca = data_dir / "config/tls/ca.crt"
        if secure and not args.ca_file and ca.is_file():
            options += ["--ca-file", str(ca)]
    options += ["--server", server]
    for key in ("token_file", "ca_file", "client", "state_dir", "session"):
        if getattr(args, key):
            options += ["--" + key.replace("_", "-"), str(getattr(args, key))]
    if args.tui_workspace:
        options += ["--workspace", args.tui_workspace]
    if args.resume is not None:
        options += ["--resume"] + ([args.resume] if args.resume else [])
    for key in ("new", "list_sessions"):
        if getattr(args, key):
            options += ["--" + key.replace("_", "-")]
    return launch(options)


def _open_background_service(args, data_dir):
    from . import deployment_service as service
    from .installation_health import status, display
    import subprocess
    try:
        print('Starting Amplifier Unified in the background…', flush=True)
        if service.managed(data_dir):
            service.command('start', data_dir)
        else:
            service.install(data_dir, args.workspace)
        result = asyncio.run(status(data_dir, wait=60))
        display(result)
        if not result['ok']:
            raise SystemExit(1)
        if not args.no_open and result.get('url'):
            # Preserve the user's chosen browser address. The status report
            # explicitly separates it from the verified host listener.
            webbrowser.open((result.get('publicUrls') or [result['url']])[0])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Background setup did not complete: {exc}\nRun amplifier-unified serve to run in this terminal.') from None


def main():
    # Recognize recovery without importing application dependencies or following
    # a potentially broken generation pointer. All other commands retain the
    # active generation's parser (including options added since the bootstrap).
    probe = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    _connection_options(probe)
    probe.add_argument("command", nargs="?")
    try:
        recovery, _ = probe.parse_known_args(sys.argv[1:])
    except argparse.ArgumentError:
        recovery = None
    if recovery is not None and recovery.command == "reset":
        from .reset import run
        return run(_parse())
    from .host_dependencies import prepare_environment
    prepare_environment()
    from .application_generations import delegate
    delegate()
    args = _parse()
    from .deployment import load_server_config
    data_dir = _data_dir(args.data_dir)
    if args.command == "tui":
        try:
            return _tui(args, data_dir)
        except (OSError, ValueError) as exc:
            raise SystemExit(str(exc)) from None
    if args.command == "completion":
        _print_completion(args.shell)
        return
    if args.command == "config":
        _config(args, data_dir)
        return
    if args.command == "doctor":
        from .installation_health import doctor, display
        result = doctor(data_dir)
        display(result, as_json=args.json, verbose=args.verbose)
        if args.verbose and not args.json:
            _doctor_details(data_dir)
        if not result['ok']:
            raise SystemExit(1)
        return
    if args.command == "setup-tls":
        _setup_tls(data_dir, args.mode)
        return
    if args.command == "service":
        from . import deployment_service
        import subprocess
        try:
            if args.replace and args.service_command != "install":
                raise ValueError("--replace is only valid with service install.")
            if args.service_command == "install":
                result = deployment_service.install(data_dir, args.workspace, replace=args.replace)
                if result['status'] == 'current':
                    print(f"Service configuration already matches: {result['path']}\n"
                          "After a package update, use amplifier-unified service restart to load the new version.")
                else:
                    print(f"Service {result['status']}: {result['path']}")
                if result.get('backup'):
                    print(f"Previous definition saved at: {result['backup']}")
            elif args.service_command == "status":
                from .installation_health import status, display
                result = asyncio.run(status(data_dir, wait=args.wait))
                display(result, as_json=args.json, verbose=args.verbose)
                if args.verbose and not args.json:
                    deployment_service.command("status", data_dir)
                if not result['ok']:
                    raise SystemExit(1)
            elif args.service_command == "uninstall":
                deployment_service.uninstall(data_dir)
            else:
                deployment_service.command(args.service_command, data_dir)
                if args.service_command in {'start', 'restart'}:
                    from .installation_health import status, display
                    result = asyncio.run(status(data_dir, wait=60))
                    display(result, as_json=args.json, verbose=args.verbose)
                    if not result['ok']:
                        raise SystemExit(1)
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
            raise SystemExit(f"Service {args.service_command} did not complete: {exc}") from None
        return
    background_launch = args.command is None and not _server_overrides(args)
    if background_launch and sys.platform == 'win32':
        raise SystemExit(
            "Windows default launcher remains unavailable pending native owner-browser and "
            "task/host qualification. No service was installed or started.")
    config = load_server_config(data_dir, overrides=_server_overrides(args))
    args.port, args.data_dir = config["port"], str(data_dir)
    if args.command == "open":
        # Explicit foreground-owner entry only. Default Windows launch and
        # Scheduler/installer activation remain independently gated above.
        try:
            return _open_browser(args, data_dir, config)
        except (OSError, ValueError):
            raise SystemExit("Cannot open the owned host. " + browser_entry.REOPEN) from None
        except browser_entry.BrowserEntryUnavailable as exc:
            raise SystemExit(str(exc)) from None
    if args.command in {"run", "continue", "tool", "access"}:
        os.environ["AMPLIFIER_WEB_HOME"] = str(data_dir)
        from .settings_migration import migrate_settings
        migrate_settings(data_dir, [args.workspace])
        from .host.config import load_config
        load_config(args.workspace)
        from .headless import run
        try:
            raise SystemExit(asyncio.run(run(args, config=config)))
        except (RuntimeError, ValueError, TimeoutError) as exc:
            print(str(exc) or "The task exceeded its wait timeout.", file=sys.stderr)
            raise SystemExit(1)
    # Explicit serving options preserve the existing foreground launch contract.
    # The ordinary first launch installs/starts the per-user background service.
    if background_launch:
        _open_background_service(args, data_dir)
    else:
        _serve(args, data_dir)


if __name__ == "__main__":
    main()
