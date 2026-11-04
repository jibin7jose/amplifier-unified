#!/usr/bin/env python3
"""Prepare a private native-integration interpreter, never the serving runtime.

The canonical runtime project owns its usual dependencies. This adds the host,
test dependencies and modules needed by the integration owner. Use
integration_checks.py with the emitted environment.json to run tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
import subprocess
import tomllib

EXTRA_DEPENDENCIES = (
    "amplifier-unified",
    "amplifier-module-tool-filesystem @ git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
    "amplifier-module-tool-search @ git+https://github.com/microsoft/amplifier-module-tool-search@main",
    "amplifier-module-context-managed @ git+https://github.com/microsoft/amplifier-bundle-context-managed@main#subdirectory=modules/context-managed",
    "amplifier-module-tool-bash @ git+https://github.com/microsoft/amplifier-module-tool-bash@main",
    "amplifier-module-tool-exec @ git+https://github.com/microsoft/amplifier-module-tool-exec@main",
    "amplifier-module-hooks-routing @ git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main#subdirectory=modules/hooks-routing",
)
REQUIRED_MODULES = (
    "amplifier_core", "amplifier_foundation", "amplifier_module_loop_live",
    "amplifier_module_context_simple", "amplifier_module_context_managed.boundary",
    "amplifier_module_provider_openai.native", "amplifier_module_tool_bash",
    "amplifier_module_tool_exec", "amplifier_module_tool_filesystem",
    "amplifier_module_tool_search.grep", "amplifier_module_tool_search.glob",
    "amplifier_module_hooks_routing.resolver",
)


def integration_dev_dependencies(dependencies: list[str]) -> list[str]:
    """Keep the native lane's current module sources, not host fixture pins.

    Host skills and recovery tests deliberately pin filesystem/context-managed.
    That separate graph remains unchanged; this owner tests current sources.
    Refuse an unexpected source rather than silently discard its policy.
    """
    fixture_sources = {
        "amplifier-module-tool-filesystem": ("filesystem", r"amplifier-module-tool-filesystem@(?:main|[0-9a-f]{40})"),
        "amplifier-module-context-managed": ("context-managed", r"amplifier-bundle-context-managed@(?:main|[0-9a-f]{40})#subdirectory=modules/context-managed"),
    }
    result = []
    for value in dependencies:
        # This bootstrap runs before third-party packages are installed. Identify
        # only the distribution token; uv still validates all retained inputs.
        name = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", value)
        fixture = fixture_sources.get(re.sub(r"[-_.]+", "-", name[1]).lower()) if name else None
        if fixture is None:
            result.append(value)
            continue
        if not re.fullmatch(
                r"\s*@\s*git\+https://github\.com/microsoft/" + fixture[1] + r"\s*",
                value[name.end():]):
            raise ValueError(f"Review the host {fixture[0]} fixture source before native integration")
    return result


def ripgrep_identity() -> dict:
    """The read-policy owner exercises both real ripgrep and Python fallback."""
    executable = shutil.which("rg")
    if not executable:
        raise ValueError("Native integration requires ripgrep (rg); install it before preparation")
    version = subprocess.check_output([executable, "--version"], text=True).strip()
    return {"path": executable, "version": version}


def checkout_identity(path: Path, declared: dict) -> dict:
    """An authenticated CI checkout may supply the same private module source."""
    path = path.resolve()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != path:
        raise ValueError("Loop source must be the checkout root")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Loop source checkout must be clean")
    origin = git("remote", "get-url", "origin").removesuffix(".git")
    expected = declared["git"].removesuffix(".git")
    if origin not in (expected, expected.replace("https://github.com/", "git@github.com:")):
        raise ValueError("Loop source must come from the declared canonical repository")
    metadata = tomllib.loads((path / "pyproject.toml").read_text())
    if metadata["project"]["name"] != "amplifier-module-loop-live":
        raise ValueError("Loop source is not the declared module")
    return {"path": str(path), "commit": git("rev-parse", "HEAD"), "declared": declared}


def project(root: Path, profiling_source: str | None = None, loop_source: Path | None = None) -> str:
    """Extend canonical declarations instead of maintaining a second graph."""
    source = (root / "amplifier_web/runtime_deps/pyproject.toml").read_text()
    runtime = tomllib.loads(source)
    host = tomllib.loads((root / "pyproject.toml").read_text())
    if runtime["project"]["name"] != "amplifier-web-runtime":
        raise ValueError("Review the canonical runtime project before preparing integrations")
    if "amplifier-core" not in runtime["tool"]["uv"]["no-build-package"]:
        raise ValueError("The runtime must prohibit Core source builds")
    additions = [*EXTRA_DEPENDENCIES,
                 *integration_dev_dependencies(host["dependency-groups"]["dev"])]
    if profiling_source:
        additions.append("amplifier-profiling @ " + profiling_source)
    source = source.replace('name = "amplifier-web-runtime"',
                            'name = "amplifier-native-integration"', 1)
    source = source.replace("dependencies = [\n", "dependencies = [\n" +
                            "".join("  " + json.dumps(item) + ",\n" for item in additions), 1)
    if loop_source:
        declared = runtime["tool"]["uv"]["sources"]["amplifier-module-loop-live"]
        checkout_identity(loop_source, declared)
        lines = source.splitlines(keepends=True)
        matches = [i for i, line in enumerate(lines) if line.startswith("amplifier-module-loop-live = ")]
        if len(matches) != 1:
            raise ValueError("Review the canonical loop source declaration")
        lines[matches[0]] = "amplifier-module-loop-live = { path = " + json.dumps(str(loop_source.resolve())) + " }\n"
        source = "".join(lines)
    source += "\namplifier-unified = { path = " + json.dumps(str(root.resolve())) + ", editable = true }\n"
    generated = tomllib.loads(source)
    if generated["tool"]["uv"]["sources"]["amplifier-unified"]["path"] != str(root.resolve()):
        raise ValueError("The generated host source binding is invalid")
    return source


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def prepare(root: Path, work: Path, profiling_source: str | None, uv: str, python: str,
            state: Path | None = None, loop_source: Path | None = None) -> dict:
    ripgrep = ripgrep_identity()
    work = work.resolve()
    if work == root.resolve() or work in root.resolve().parents:
        raise ValueError("Preparation requires its own directory")
    work.mkdir(parents=True, exist_ok=True)
    generated = project(root, profiling_source, loop_source)
    loop_identity = checkout_identity(loop_source,
        tomllib.loads((root / "amplifier_web/runtime_deps/pyproject.toml").read_text())["tool"]["uv"]["sources"]["amplifier-module-loop-live"]) if loop_source else None
    manifest = work / "pyproject.toml"
    if manifest.exists() and manifest.read_text() != generated:
        raise ValueError("Use a fresh directory for a changed integration dependency graph")
    manifest.write_text(generated)
    subprocess.run([uv, "sync", "--project", str(work), "--python", python], check=True)
    interpreter = work / ".venv/bin/python"
    # Real imports must succeed before pytest has any opportunity to skip them.
    probe = """import importlib,importlib.metadata as m,json,sys
names=json.loads(sys.argv[1])
modules={n:importlib.import_module(n).__file__ for n in names}
rows=sorted([{'name':d.metadata['Name'],'version':d.version,
 'direct':json.loads(d.read_text('direct_url.json') or 'null')} for d in m.distributions()],key=lambda r:r['name'])
if any(r['name'].lower().replace('_','-')=='amplifier-app-cli' for r in rows):
 raise RuntimeError('The application runtime must not contain amplifier-app-cli')
print(json.dumps({'python':sys.version,'modules':modules,'distributions':rows}))
"""
    modules = [*REQUIRED_MODULES, *(["amplifier_profiling"] if profiling_source else [])]
    result = subprocess.run([str(interpreter), "-I", "-c", probe, json.dumps(modules)],
                            text=True, capture_output=True, check=True)
    receipt = json.loads(result.stdout)
    if loop_source and checkout_identity(loop_source, loop_identity["declared"]) != loop_identity:
        raise ValueError("Loop source changed during environment preparation")
    from release_qualification import core_wheel
    receipt.update({"projectSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    "lockSha256": hashlib.sha256((work / "uv.lock").read_bytes()).hexdigest(),
                    "source": str(root.resolve()), "coreWheel": core_wheel(interpreter),
                    "profilingConfigured": bool(profiling_source), "loopCheckout": loop_identity,
                    "ripgrep": ripgrep, "modelCalls": False})
    write_json(work / "preparation.json", receipt)
    state = (state or work / "state").resolve()
    for name in ("home", "tmp"):
        (state / name).mkdir(parents=True, exist_ok=True)
    environment = {"UNIFIED_RUNTIME_PYTHON": str(interpreter), "RUN_HISTORY_EDIT_WORKER": "1",
                   "HOME": str(state / "home"), "TMPDIR": str(state / "tmp")}
    write_json(work / "environment.json", environment)
    return {"python": str(interpreter), "environment": str(work / "environment.json"),
            "preparation": str(work / "preparation.json")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare",))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--state", type=Path, help="Private home and temporary directory (default: WORK/state)")
    parser.add_argument("--loop-source", type=Path, help="Clean checkout of the declared loop-live repository")
    parser.add_argument("--profiling-source", help="Canonical package URL, required for the separate profiling lane")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--python", default="3.13")
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.work, args.profiling_source, args.uv, args.python,
                            args.state, args.loop_source), indent=2))


if __name__ == "__main__":
    main()
