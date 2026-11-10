"""Prepare explicit client-integration prerequisites; the central runner owns pytest.

Each lane uses a disposable test environment. In particular, CLI interoperability
never installs amplifier-app-cli into the application's normal host/runtime.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


LANES = {
    "browser-headless": {
        "selectors": [
            "tests/test_browser_auth.py::test_external_link_then_same_origin_login",
            "tests/test_browser_auth.py::test_service_worker_forwarded_external_entry",
            "tests/test_setup_page.py::test_renderer_fits_narrow_and_wide_chromium_viewports",
        ],
        "modules": ["playwright.async_api"],
        "manual": False,
    },
    "browser-installed": {
        "selectors": ["tests/test_browser_auth.py::test_installed_pwa_launch"],
        "modules": ["playwright.async_api"],
        "manual": True,
    },
    "cli-interop": {
        "selectors": [
            "tests/test_session_handoff.py::test_cli_unified_cli_preserves_native_history_and_fences_old_worker",
            "tests/test_session_portability.py::test_installed_cli_round_trip",
            "tests/test_shared_configuration_interop.py::test_cli_and_web_read_session_provider_choice_and_unknown_fields",
        ],
        "modules": ["amplifier_app_cli.session_handoff", "amplifier_module_loop_live.runtime"],
        "manual": False,
    },
    "filesystem-aliases": {
        "selectors": [
            "tests/test_file_source_guard.py::test_concurrent_creation_does_not_clobber_aliases[case]",
            "tests/test_file_source_guard.py::test_concurrent_creation_does_not_clobber_aliases[unicode]",
        ],
        "modules": ["amplifier_module_tool_filesystem", "amplifier_module_tool_apply_patch"],
        "manual": False,
    },
    "uv-reset": {
        "selectors": ["tests/test_reset.py::test_real_uv_repairs_modified_packages_and_missing_dependencies"],
        "modules": [],
        "manual": False,
    },
}


def describe(lane: str, state: Path) -> dict:
    state = state.resolve()
    env = {
        "HOME": str(state / "home"),
        "TMPDIR": str(state / "tmp"),
        "AMPLIFIER_HOME": str(state / "home/amplifier"),
        "AMPLIFIER_WEB_HOME": str(state / "home/unified"),
        "AMPLIFIER_SESSION_STATE_HOME": str(state / "home/session-state"),
        "UV_CACHE_DIR": str(state / "cache"),
        "UV_TOOL_DIR": str(state / "tools"),
        "UV_TOOL_BIN_DIR": str(state / "bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if lane.startswith("browser-"):
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(state / "browsers")
    if lane == "browser-installed":
        env["AMPLIFIER_TEST_INSTALLED_PWA"] = "1"
    if lane == "cli-interop":
        env["AMPLIFIER_CLI_TEST_ENV"] = str(state / "env")
    if lane == "uv-reset":
        uv = shutil.which("uv")
        if uv:
            env["AMPLIFIER_RESET_TEST_UV"] = str(Path(uv).resolve())
    return {"lane": lane, **LANES[lane], "state_dir": str(state),
            "interpreter": str(state / "env/bin/python"), "environment": env,
            "expected_cases": len(LANES[lane]["selectors"]), "allowed_skips": []}


def checked_source(path: Path) -> dict:
    path = path.resolve()
    if not (path / "pyproject.toml").is_file():
        raise ValueError(f"Source has no pyproject.toml: {path}")
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True)
    if dirty:
        raise ValueError(f"Integration dependency must be a clean, revision-bound checkout: {path}")
    return {"path": str(path), "revision": revision}


def browser_prepare_commands(repo: Path, uv: str, interpreter: str, *, install_dependencies=False) -> list[list[str]]:
    """Reuse the locked browser setup without replacing an installed-wheel env."""
    tools = repo / "tests/web"
    # An explicitly supplied older source keeps its own recorded lock.
    if not tools.exists() and (repo / "frontend/package-lock.json").is_file():
        tools = repo / "frontend"
    lock = json.loads((tools / "package-lock.json").read_text())
    version = lock["packages"]["node_modules/playwright"]["version"]
    install = [interpreter, "-m", "playwright", "install"]
    if install_dependencies:
        install.append("--with-deps")
    return [[uv, "pip", "install", "--python", interpreter, f"playwright=={version}"],
            [*install, "chromium"]]


def prepare(lane: str, state: Path, repo: Path, *, python: str, cli_source: Path | None,
            loop_source: Path | None, install_browser_dependencies: bool = False,
            filesystem_source: Path | None = None, apply_patch_source: Path | None = None) -> dict:
    plan = describe(lane, state)
    state = Path(plan["state_dir"])
    if state.exists():
        raise ValueError(f"Preparation needs a new disposable state directory: {state}")
    uv = shutil.which("uv")
    if not uv:
        raise ValueError("uv must be installed before preparing client integrations")
    sources = {}
    if lane == "cli-interop":
        if cli_source is None or loop_source is None:
            raise ValueError("CLI interoperability requires explicit --cli-source and --loop-source checkouts")
        sources = {"cli": checked_source(cli_source), "loop_live": checked_source(loop_source)}
    if lane == "filesystem-aliases":
        if filesystem_source is None or apply_patch_source is None:
            raise ValueError("Filesystem integration requires explicit --filesystem-source and --apply-patch-source checkouts")
        sources = {"filesystem": checked_source(filesystem_source), "apply_patch": checked_source(apply_patch_source)}
    state.mkdir(parents=True)
    for name in ("home", "tmp", "cache", "evidence", "bin"):
        (state / name).mkdir()
    env = {**os.environ, **plan["environment"], "UV_PROJECT_ENVIRONMENT": str(state / "env"),
           "UV_NO_BUILD_PACKAGE": "amplifier-core"}
    env.pop("VIRTUAL_ENV", None)
    commands = []

    def run(args: list[str]) -> None:
        commands.append(args)
        subprocess.run(args, cwd=repo, env=env, check=True)

    # The host baseline stays pinned to the repository lock. Extra integration
    # packages are installed only in this disposable environment and recorded.
    run([uv, "sync", "--locked", "--group", "dev", "--python", python])
    interpreter = plan["interpreter"]
    if lane.startswith("browser-"):
        for args in browser_prepare_commands(repo, uv, interpreter,
                                             install_dependencies=install_browser_dependencies):
            run(args)
    if lane in {"cli-interop", "filesystem-aliases"}:
        run([uv, "pip", "install", "--python", interpreter,
             *[record["path"] for record in sources.values()]])
    packages = subprocess.check_output([uv, "pip", "freeze", "--python", interpreter], env=env, text=True)
    (state / "evidence/packages.txt").write_text(packages)
    plan.update({"sources": sources, "setup_commands": commands})
    (state / "evidence/preparation.json").write_text(json.dumps(plan, indent=2) + "\n")
    return plan


def alias_capabilities(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    result = {}
    with tempfile.TemporaryDirectory(prefix="alias-preflight-", dir=directory) as temporary:
        root = Path(temporary)
        for name, (original, alias) in {
            "case": ("File.txt", "file.txt"), "unicode": ("caf\u00e9.txt", "cafe\u0301.txt")
        }.items():
            first, second = root / original, root / alias
            first.write_text("owned filesystem preflight")
            result[name] = second.exists() and os.path.samefile(first, second)
            first.unlink()
    return result


def preflight(lane: str, state: Path) -> dict:
    plan = describe(lane, state)
    prerequisites = {}
    for module in LANES[lane]["modules"]:
        try:
            spec = importlib.util.find_spec(module)
        except ModuleNotFoundError:
            spec = None
        if spec is None:
            raise ValueError(f"Required integration module is missing: {module}")
        prerequisites[module] = spec.origin
    if lane.startswith("browser-"):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as driver:
            executable = Path(driver.chromium.executable_path)
            if not executable.is_file():
                raise ValueError(f"The Python Playwright Chromium binary is missing: {executable}")
            prerequisites["chromium"] = str(executable)
        prerequisites["playwright_version"] = importlib.metadata.version("playwright")
    if lane == "browser-installed":
        if os.environ.get("AMPLIFIER_TEST_INSTALLED_PWA") != "1":
            raise ValueError("Installed PWA acceptance requires explicit AMPLIFIER_TEST_INSTALLED_PWA=1")
        if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise ValueError("Installed PWA acceptance requires an actual desktop display; headless smoke is separate")
        prerequisites["desktop_platform"] = sys.platform
        prerequisites["limits"] = "Actual install/launch/relaunch; does not establish native dragging or physical mobile acceptance"
    if lane == "cli-interop":
        declared = os.environ.get("AMPLIFIER_CLI_TEST_ENV")
        if not declared or Path(declared).resolve() != Path(sys.prefix).resolve() or sys.prefix == sys.base_prefix:
            raise ValueError("CLI interoperability must run in its explicitly designated disposable test virtualenv")
        prerequisites["cli_version"] = importlib.metadata.version("amplifier-app-cli")
    if lane == "filesystem-aliases":
        capabilities = alias_capabilities(state / "tmp")
        if not all(capabilities.values()):
            raise ValueError(f"Designated filesystem lane must support both aliases, not skip them: {capabilities}")
        prerequisites["filesystem_aliases"] = capabilities
    if lane == "uv-reset":
        uv = os.environ.get("AMPLIFIER_RESET_TEST_UV")
        if not uv or not Path(uv).is_file() or not os.access(uv, os.X_OK):
            raise ValueError("Reset acceptance requires AMPLIFIER_RESET_TEST_UV pointing to the uv executable")
        prerequisites["uv"] = subprocess.check_output([uv, "--version"], text=True).strip()
    return {**plan, "preflight": "ready", "prerequisites": prerequisites,
            "actual_interpreter": sys.executable}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("describe", "prepare", "preflight"))
    parser.add_argument("--lane", choices=tuple(LANES), required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", default="3.13")
    parser.add_argument("--cli-source", type=Path)
    parser.add_argument("--loop-source", type=Path)
    parser.add_argument("--filesystem-source", type=Path)
    parser.add_argument("--apply-patch-source", type=Path)
    parser.add_argument("--install-browser-dependencies", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.lane, args.state_dir, args.repo, python=args.python,
                             cli_source=args.cli_source, loop_source=args.loop_source,
                             install_browser_dependencies=args.install_browser_dependencies,
                             filesystem_source=args.filesystem_source, apply_patch_source=args.apply_patch_source)
        else:
            result = (describe if args.command == "describe" else preflight)(args.lane, args.state_dir)
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + "\n")
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
