"""Exercise direct-script imports in a wheel-like host/runtime layout."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import textwrap

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "amplifier_web"


def test_apply_settings_does_not_require_server_dependencies():
    # Use a fresh interpreter so a server module imported by another test cannot
    # mask an accidental dependency. Workers intentionally do not install aiohttp.
    runner = textwrap.dedent("""
        import importlib.util, sys
        from types import SimpleNamespace
        sys.path.insert(0, sys.argv[1])
        sys.modules['aiohttp'] = None
        assert importlib.util.find_spec('aiohttp') is None
        from amplifier_web.host.session import _apply_settings
        config = SimpleNamespace(settings={}, providers=[])
        ordinary = SimpleNamespace(tools=[], providers=[], hooks=[], session={}, agents={})
        assert _apply_settings(ordinary, config).tools == []
        configured = SimpleNamespace(
            tools=[{'module': 'tool-ui-use'}, {'module': 'tool-computer-use'}],
            providers=[], hooks=[], session={}, agents={},
        )
        result = _apply_settings(configured, config)
        assert result.tools[0]['config']['browser']['host_adapter'] is True
        assert result.tools[1]['config']['observed_control'] is True
        assert sys.modules['aiohttp'] is None
        assert 'amplifier_web.ui_use' not in sys.modules
        assert 'amplifier_web.device_connections' not in sys.modules
    """)
    result = subprocess.run(
        [sys.executable, '-I', '-c', runner, str(PACKAGE.parent)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("entrypoint,refresh", [("update_probe.py", False), ("update_probe.py", True), ("runtime_worker.py", False)])
def test_worker_scripts_do_not_expose_host_site_packages(tmp_path, entrypoint, refresh):
    outer = tmp_path / "host-site-packages"
    package = outer / "amplifier_web"
    package.mkdir(parents=True)
    runtime = tmp_path / "runtime-site-packages"
    runtime.mkdir()
    # The runtime owns Foundation; the isolated script may import its history
    # types without making the host site-packages directory visible.
    foundation = runtime / "amplifier_foundation" / "session"
    foundation.mkdir(parents=True)
    (foundation.parent / "__init__.py").write_text("")
    (foundation / "__init__.py").write_text("")
    (foundation / "history.py").write_text("class SessionHistoryStore: pass\n")
    (foundation / "jsonl.py").write_text("class TranscriptIndex: pass\n")
    for name in (entrypoint, "runtime_protocol.py", "message_delivery.py", "response_completion.py", "ownership.py", "__init__.py", "runtime_bootstrap.py", "update_diagnostics.py"):
        if (PACKAGE / name).exists():
            shutil.copy2(PACKAGE / name, package / name)
    shutil.copytree(PACKAGE.parent / "amplifier_operations", outer / "amplifier_operations")
    shutil.copytree(PACKAGE.parent / "amplifier_kernels", outer / "amplifier_kernels")
    (outer / "host_only_dependency.py").write_text("HOST_ONLY = True\n")
    metadata = outer / "amplifier_bundle_context_intelligence-0.1.3.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: amplifier-bundle-context-intelligence\nVersion: 0.1.3\n"
    )
    (outer / "runtime_dependency.py").write_text("SOURCE = 'host'\n")
    (runtime / "runtime_dependency.py").write_text("SOURCE = 'runtime'\n")
    checks = textwrap.dedent("""
        import importlib.util
        import importlib.metadata
        import runtime_dependency
        assert importlib.util.find_spec("host_only_dependency") is None, "host imports leaked"
        assert runtime_dependency.SOURCE == "runtime", "host shadows runtime dependency"
        try:
            importlib.metadata.distribution("amplifier-bundle-context-intelligence")
        except importlib.metadata.PackageNotFoundError:
            pass
        else:
            raise AssertionError("host distribution metadata leaked")
    """)
    (package / "host").mkdir()
    (package / "host" / "__init__.py").write_text("")
    (package / "host" / "session.py").write_text(
        checks + textwrap.dedent("""
        async def prepare_dependencies(*args, **kwargs):
            import sys
            assert not any(name.startswith("amplifier_module_loop_live") for name in sys.modules)
        class Session:
            async def cleanup(self): pass
        async def prepare_manager(*args, **kwargs):
            return Session(), None, {"standalone": True, "providers": ["test"]}
        """)
    )
    (package / "host" / "config.py").write_text(
        checks + "\nprint('ISOLATED', flush=True)\nraise SystemExit(0)\n"
    )
    # -I -S exclude the checkout, ambient PYTHONPATH and pytest's own venv.
    # Add only the runtime's
    # dependency path and the script directory, never the host site-packages root.
    runner = textwrap.dedent("""
        import asyncio, runpy, sys
        from pathlib import Path
        script = Path(sys.argv[1])
        sys.path.insert(0, sys.argv[2])
        sys.path.insert(0, str(script.parent))
        refresh = sys.argv[3] == "refresh"
        sys.argv = [str(script), "/unused", "anchors"] + (["--refresh-dependencies"] if refresh else [])
        if script.name == "update_probe.py":
            runpy.run_path(str(script), run_name="__main__")
        else:
            namespace = runpy.run_path(str(script), run_name="worker_test")
            worker = namespace["Worker"]()
            worker.start.__func__.__globals__["publish"] = lambda event: print(event)
            asyncio.run(worker.start({"id": "test"}))
    """)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", runner, str(package / entrypoint), str(runtime), "refresh" if refresh else "ordinary"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    if entrypoint == "update_probe.py":
        assert '"ok": true' in result.stdout, result.stdout
        if refresh:
            assert '"dependenciesPrepared": true' in result.stdout
            assert '"standalone"' not in result.stdout and '"providersPresent"' not in result.stdout
    else:
        assert "ISOLATED" in result.stdout, result.stdout


@pytest.fixture(scope="module")
def installed_app(tmp_path_factory):
    """Use a real non-editable wheel, not the checkout's import path."""
    root = tmp_path_factory.mktemp("installed-app")
    wheel_dir = root / "wheels"
    uv = shutil.which("uv")
    assert uv is not None, "Installed-wheel isolation checks require uv"
    command = [uv, "build", "--wheel", "--out-dir", str(wheel_dir)]
    contract_path = os.environ.get("AMPLIFIER_TEST_CONTRACT")
    if contract_path:
        contract = json.loads(Path(contract_path).read_text())
        configured = contract.get("pythonBuild", {}).get("command")
        if configured is not None:
            # Use the host's prepared backend environment and isolation choice.
            # Only the output directory belongs to this fixture. A malformed
            # contract or failed build must fail, never trigger another builder.
            command = shlex.split(configured)
            command[command.index("--outdir") + 1] = str(wheel_dir)
    subprocess.run(
        command,
        cwd=PACKAGE.parent, check=True, capture_output=True, text=True, timeout=60,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    host = root / "host"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(host)], check=True)
    python = host / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(wheels[0])],
        check=True, capture_output=True, text=True, timeout=60,
    )
    result = subprocess.run(
        [str(python), "-I", "-c", "import amplifier_web; print(amplifier_web.__file__)"],
        check=True, capture_output=True, text=True,
    )
    site = Path(result.stdout.strip()).parent.parent
    (site / "host_only_dependency.py").write_text("HOST_ONLY = True\n")
    return root, site


def run_bootstrap_check(installed_app, code):
    root, site = installed_app
    runner = """
import importlib.util, sys
from pathlib import Path
site = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("bootstrap_check", site / "amplifier_web/runtime_bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
paths = list(sys.path)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", textwrap.dedent(runner) + textwrap.dedent(code), str(site)],
        cwd=root, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("preloaded_web", [False, True])
def test_installed_worker_collaboration_imports_preserve_isolation(installed_app, preloaded_web):
    preload = """
spec = importlib.util.spec_from_file_location("amplifier_web", site / "amplifier_web/__init__.py",
                                           submodule_search_locations=[str(site / "amplifier_web")])
web = importlib.util.module_from_spec(spec)
sys.modules["amplifier_web"] = web
spec.loader.exec_module(web)
""" if preloaded_web else ""
    run_bootstrap_check(installed_app, preload + """
import importlib.metadata
from types import SimpleNamespace
web = bootstrap.bootstrap_app_package()
assert bootstrap.bootstrap_app_package() is web
from amplifier_web.collaboration_input import checkpoint_anchors, admit, steer
from amplifier_operations.coordination import fingerprint
import amplifier_operations
import amplifier_kernels
from amplifier_web.computation import KERNEL_TRANSPORT, Kernels, tool_schema
assert callable(Kernels) and 'execute' in tool_schema()['properties']['action']['enum']
assert KERNEL_TRANSPORT.get() is False
token = KERNEL_TRANSPORT.set(True)
assert KERNEL_TRANSPORT.get() is True
KERNEL_TRANSPORT.reset(token)
assert checkpoint_anchors([], SimpleNamespace(generation=None)) == []
assert callable(admit) and callable(steer) and callable(fingerprint)
assert Path(web.__file__).resolve() == site / "amplifier_web/__init__.py"
assert Path(amplifier_operations.__file__).resolve() == site / "amplifier_operations/__init__.py"
assert Path(amplifier_kernels.__file__).resolve() == site / "amplifier_kernels/__init__.py"
assert sys.path == paths and str(site) not in sys.path
assert importlib.util.find_spec("host_only_dependency") is None
try:
    importlib.metadata.distribution("amplifier-unified")
except importlib.metadata.PackageNotFoundError:
    pass
else:
    raise AssertionError("host metadata leaked")
""")


@pytest.mark.parametrize("name", ["amplifier_web", "amplifier_operations", "amplifier_kernels"])
def test_bootstrap_rejects_wrong_origin_before_loading(installed_app, name):
    run_bootstrap_check(installed_app, f"""
from types import ModuleType
foreign = ModuleType({name!r})
foreign.__file__ = str(site / "foreign/__init__.py")
sys.modules[{name!r}] = foreign
before = set(sys.modules)
try:
    bootstrap.bootstrap_app_package()
except ImportError as exc:
    assert {name!r} in str(exc)
else:
    raise AssertionError("wrong-origin package accepted")
assert sys.modules[{name!r}] is foreign
assert not (set(sys.modules) - before) & {{"amplifier_web", "amplifier_operations", "amplifier_kernels"}}
assert sys.path == paths
""")


@pytest.mark.parametrize("name", ["amplifier_web", "amplifier_operations", "amplifier_kernels"])
def test_bootstrap_rejects_blocked_module_entries(installed_app, name):
    run_bootstrap_check(installed_app, f"""
sys.modules[{name!r}] = None
before = set(sys.modules)
try:
    bootstrap.bootstrap_app_package()
except ImportError as exc:
    assert {name!r} in str(exc)
else:
    raise AssertionError("blocked module entry accepted")
assert sys.modules[{name!r}] is None
assert not (set(sys.modules) - before) & {{"amplifier_web", "amplifier_operations", "amplifier_kernels"}}
assert sys.path == paths
""")


@pytest.mark.parametrize("preloaded_web", [False, True])
def test_bootstrap_cleans_new_packages_when_initialization_fails(tmp_path, preloaded_web):
    site = tmp_path / "host"
    shutil.copytree(PACKAGE, site / "amplifier_web")
    operations = site / "amplifier_operations"
    operations.mkdir()
    (operations / "__init__.py").write_text("from . import partial\nraise RuntimeError('fixture failure')\n")
    (operations / "partial.py").write_text("")
    preload = """
spec = importlib.util.spec_from_file_location("amplifier_web", site / "amplifier_web/__init__.py",
                                           submodule_search_locations=[str(site / "amplifier_web")])
web = importlib.util.module_from_spec(spec)
sys.modules["amplifier_web"] = web
spec.loader.exec_module(web)
""" if preloaded_web else ""
    run_bootstrap_check((tmp_path, site), preload + """
before = set(sys.modules)
try:
    bootstrap.bootstrap_app_package()
except RuntimeError as exc:
    assert str(exc) == "fixture failure"
else:
    raise AssertionError("initialization failure swallowed")
assert not any(name == package or name.startswith(package + ".")
               for name in set(sys.modules) - before
               for package in ("amplifier_web", "amplifier_operations"))
assert before <= set(sys.modules)
assert sys.path == paths
""")
