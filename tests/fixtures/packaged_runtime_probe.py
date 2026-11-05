"""Opt-in published-wheel Mac seam: actual RuntimeManager command, IPC and resume.

Run outside the checkout with the installed host Python -I. Only synthetic bundle,
provider and hook inputs are authored here. No app/worker implementation is patched.
Dependency preparation uses the installed worker manifest with explicit test pins;
the source store is still empty when the first production worker starts.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import traceback
from urllib.parse import unquote, urlsplit
import zipfile

VERSION = "0.55.0"
WHEEL_SHA256 = "11501f2c9718663c2bd4a4bfee6e0e08d182537c65341b9707d653e116d8bee1"
FOUNDATION = "8834b895b74ff5ed047a9fd39e52b3506b06c2fa"
SHARED_SHA256 = "02f284dd67a137a04ccd2e37ca674a59c436d27d2e008b6425d29189884feb69"
LOOP = "670f405f711658c3c38e81dbd50a35d3d8c2ac3c"
FOUNDATION_URL = "https://github.com/microsoft/amplifier-foundation"
LOOP_URL = "https://github.com/microsoft/amplifier-module-loop-live"

# Imported by a real worker from a newly published immutable Git source.
HOOK = '''import hashlib, importlib.metadata as metadata, json, os, sys
from pathlib import Path
import amplifier_foundation.sources.shared as shared
import amplifier_web
__amplifier_module_type__ = "hook"
async def mount(coordinator, config=None):
    distribution = metadata.distribution("amplifier-foundation")
    record = {
        "pid": os.getpid(), "python": sys.executable, "prefix": sys.prefix,
        "hook": str(Path(__file__).resolve()),
        "foundation": str(Path(shared.__file__).resolve()),
        "sharedSha256": hashlib.sha256(Path(shared.__file__).read_bytes()).hexdigest(),
        "directUrl": json.loads(distribution.read_text("direct_url.json")),
        "app": str(Path(amplifier_web.__file__).resolve()),
        "worker": str(Path(sys.modules["__main__"].__file__).resolve()),
        "hooks": [row["module"] for row in coordinator.config.get("hooks", [])],
        "rootSessionId": coordinator.config.get("root_session_id"),
        "coordinatorType": type(coordinator).__module__ + "." + type(coordinator).__qualname__,
    }
    with Path(config["records"]).open("a") as stream:
        stream.write(json.dumps(record) + "\\n")
'''
PROVIDER = '''from pathlib import Path
from amplifier_core.models import ProviderInfo
__amplifier_module_type__ = "provider"
class FixtureProvider:
    name = "fixture"
    def __init__(self, config): self.config = config
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model":"offline"})
    def get_config_schema(self): return {"fields": []}
    async def list_models(self): return []
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        with Path(self.config["calls"]).open("a") as stream: stream.write("model called\\n")
        raise AssertionError("Packaged cache acceptance must never generate")
async def mount(coordinator, config=None):
    await coordinator.mount("providers", FixtureProvider(config), name="fixture")
'''
ROWS = [
    {"role": "user", "content": "Preserve this saved conversation", "metadata": {"_seq": 0}},
    {"role": "assistant", "content": "Saved answer; do not replay", "metadata": {"_seq": 1}},
]


def require(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(argv, *, cwd=None, capture=False, timeout=600):
    print("+", " ".join(map(str, argv)), flush=True)
    return subprocess.check_output(list(map(str, argv)), cwd=cwd, text=True, timeout=timeout).strip() if capture else subprocess.run(
        list(map(str, argv)), cwd=cwd, check=True, timeout=timeout)


def git(path, *args):
    return command(["git", "-C", path, *args], capture=True, timeout=30)


def clean_environment(work, loop_source):
    # An allow-list rather than redaction: no provider keys, GitHub tokens,
    # inherited Amplifier homes, Python paths or user Git configuration.
    result = {"PATH": os.environ["PATH"], "HOME": str(work / "home"),
              "LANG": "en_US.UTF-8", "TMPDIR": str(work / "tmp"),
              "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
              "UV_CACHE_DIR": str(work / "uv-cache"), "UV_NO_CONFIG": "1",
              "UV_NO_BUILD_PACKAGE": "amplifier-core", "UV_PYTHON_DOWNLOADS": "never",
              "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
              "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file:https",
              "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{loop_source.as_uri()}.insteadOf",
              "GIT_CONFIG_VALUE_0": LOOP_URL,
              "AMPLIFIER_HOME": str(work / "home/amplifier"),
              "AMPLIFIER_WEB_HOME": str(work / "home/unified"),
              "AMPLIFIER_SOURCE_STORE": str(work / "home/sources"),
              "AMPLIFIER_SESSION_STATE_HOME": str(work / "home/session-state"),
              "XDG_CONFIG_HOME": str(work / "home/config"),
              "XDG_CACHE_HOME": str(work / "home/cache"),
              "XDG_DATA_HOME": str(work / "home/data")}
    return result


def pinned_manifest(content):
    """Preserve the shipped graph, pin only this acceptance's selected inputs."""
    data = tomllib.loads(content.decode())
    uv = data["tool"]["uv"]
    sources = uv["sources"]
    for name in ("amplifier-foundation", "amplifier-module-hooks-session-naming"):
        source = sources[name]
        require(source["git"] == FOUNDATION_URL, "Unexpected Foundation source")
        source.pop("branch", None)
        source.pop("tag", None)
        source["rev"] = FOUNDATION
    sources["amplifier-module-loop-live"] = {"git": LOOP_URL, "rev": LOOP}
    overrides = uv["override-dependencies"]
    require(sum(row.startswith("amplifier-foundation @ ") for row in overrides) == 1,
            "Expected exactly one shipped Foundation override")
    uv["override-dependencies"] = [
        f"amplifier-foundation @ git+{FOUNDATION_URL}@{FOUNDATION}"
        if row.startswith("amplifier-foundation @ ") else row for row in overrides]
    return data


def verify_identity(record, *, prefix, checkout):
    require(record["sharedSha256"] == SHARED_SHA256, "Foundation shared.py bytes differ")
    require(record["directUrl"]["vcs_info"]["commit_id"] == FOUNDATION, "Foundation commit differs")
    require(record["directUrl"]["url"].removesuffix(".git") == FOUNDATION_URL, "Foundation repository differs")
    origin = Path(record["foundation"]).resolve()
    require(origin.is_relative_to(prefix.resolve()), "Foundation is not installed in its interpreter")
    require(not origin.is_relative_to(checkout), "Foundation imported from checkout")


def verify_wheel_origin(direct, wheel):
    # uv records local wheels with an empty archive_info. The downloaded bytes
    # and installed-file comparisons are authoritative, not an invented hash.
    parsed = urlsplit(direct.get("url", ""))
    require(parsed.scheme == "file" and parsed.netloc in {"", "localhost"},
            "App did not install from the downloaded local wheel")
    require(Path(unquote(parsed.path)).resolve() == wheel.resolve(), "App installed from another wheel")
    require("archive_info" in direct and "dir_info" not in direct, "App is not an archive install")
    observed = direct["archive_info"].get("hashes", {}).get("sha256")
    require(observed in {None, WHEEL_SHA256}, "Installed wheel metadata hash differs")


def verify_lifecycle(events, calls):
    require(not any(event["type"] == "runtime.error" for event in events), "Runtime reported an error")
    require(not any(event.get("event", "").startswith("generation.") for event in events),
            "Generation unexpectedly started")
    require(not calls.exists(), "Unexpected model call, including during shutdown")


def select_worker_record(records, pid, session_id):
    # Core validates the hook by calling mount with a MockCoordinator before
    # mounting it on the real session. Validation is not runtime adoption proof.
    mounted = [row for row in records if row["pid"] == pid and row.get("rootSessionId") == session_id]
    require(len(mounted) == 1, "Expected exactly one session-bound hook mount for this worker")
    return mounted[0]


def inspect_object(root, origin, revision):
    """Exact tracked bytes/modes, seal and clean Git state; no chmod repair."""
    require(stat.S_IMODE(root.stat().st_mode) == 0o555, "Published root must be exactly 0555")
    require(git(root, "rev-parse", "HEAD") == revision, "Wrong published revision")
    files = git(origin, "ls-files").splitlines()
    inventory = {}
    for name in files:
        source, target = origin / name, root / name
        require(target.read_bytes() == source.read_bytes(), "Published bytes changed: " + name)
        expected = stat.S_IMODE(source.stat().st_mode) & ~0o222
        require(stat.S_IMODE(target.stat().st_mode) == expected, "Published mode changed: " + name)
        inventory[name] = {"sha256": digest(target), "mode": oct(expected)}
    for path in root.rglob("*"):
        require(not path.is_symlink(), "Unexpected source symlink")
        require(not path.stat().st_mode & 0o222, "Writable published descendant: " + str(path))
    require(not git(root, "status", "--porcelain", "--untracked-files=no"), "Published checkout became dirty")
    extras = git(root, "ls-files", "--others").splitlines()
    require(extras == [".amplifier_cache_meta.json"], "Unexpected published extras")
    # The metadata marker is an intentional untracked file.
    meta = json.loads((root / ".amplifier_cache_meta.json").read_text())
    require(meta["commit"] == revision and meta["immutable"] is True, "Bad publication metadata")
    return {"root": str(root), "rootMode": "0o555", "inode": root.stat().st_ino,
            "revision": revision, "files": inventory}


def installed_app(wheel, checkout):
    import amplifier_web
    app = Path(amplifier_web.__file__).resolve().parent
    require(metadata.version("amplifier-unified") == VERSION, "Wrong installed app version")
    require(app.is_relative_to(Path(sys.prefix).resolve()), "App is not installed in host environment")
    require(not app.is_relative_to(checkout), "App imported from source checkout")
    direct = json.loads(metadata.distribution("amplifier-unified").read_text("direct_url.json"))
    verify_wheel_origin(direct, wheel)
    with zipfile.ZipFile(wheel) as archive:
        # Runtime-critical shipped files must match original release bytes.
        names = ("__init__.py", "runtime.py", "runtime_environment.py", "runtime_worker.py",
                 "runtime_bootstrap.py", "runtime_deps/pyproject.toml")
        hashes = {}
        for name in names:
            require((app / name).read_bytes() == archive.read("amplifier_web/" + name), "Changed installed app: " + name)
            hashes[name] = digest(app / name)
    return app, {"origin": str(app), "directUrl": direct, "files": hashes}


async def exercise(args, result):
    from amplifier_foundation.sources import shared
    from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS, app_behaviors
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_environment import manifest_path, prepare_project, receipt_directory, toml_value

    work, output = args.work, args.output
    app, result["app"] = installed_app(args.wheel, args.checkout)
    host = {"foundation": str(Path(shared.__file__).resolve()), "sharedSha256": digest(shared.__file__),
            "directUrl": json.loads(metadata.distribution("amplifier-foundation").read_text("direct_url.json")),
            "python": sys.executable, "prefix": sys.prefix}
    result["host"] = host
    verify_identity(host, prefix=Path(sys.prefix), checkout=args.checkout)
    home = Path(os.environ["AMPLIFIER_WEB_HOME"])
    receipt = receipt_directory(home)
    receipt.mkdir(parents=True)
    manifest = pinned_manifest(manifest_path().read_bytes())
    content = ("\n".join(json.dumps(k) + " = " + toml_value(v) for k, v in manifest.items()) + "\n").encode()
    (receipt / "runtime.toml").write_bytes(content)
    project = prepare_project(home)
    uv = shutil.which("uv")
    require(uv, "uv must already be installed by CI")
    command([uv, "sync", "--project", project, "--python", sys.executable])
    shutil.copy2(project / "uv.lock", receipt / "runtime.lock")
    shutil.copy2(project / "uv.lock", output / "runtime.lock")
    shutil.copy2(project / "pyproject.toml", output / "runtime.toml")
    overrides = work / "module-overrides.txt"
    overrides.write_text(
        f"amplifier-foundation @ git+{FOUNDATION_URL}@{FOUNDATION}\n"
        f"amplifier-module-hooks-session-naming @ git+{FOUNDATION_URL}@{FOUNDATION}#subdirectory=modules/hooks-session-naming\n")
    os.environ["UV_OVERRIDE"] = str(overrides)
    worker_python = project / ".venv/bin/python"
    context_source = command([worker_python, "-I", "-c",
        "import amplifier_module_context_simple as c; from pathlib import Path; print(Path(c.__file__).parent.parent)"], capture=True)
    result["workerProject"] = str(project)

    origin = work / "hook-origin"
    package = origin / "amplifier_module_hook_context_intelligence"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(HOOK)
    # The real Git resolver rejects repositories without a supported marker.
    # This source-only fixture needs no Python build or dependency installation.
    (origin / "bundle.md").write_text(
        "---\nbundle:\n  name: packaged-probe-hook\n  version: '1.0'\n---\n")
    (origin / "payload.txt").write_bytes(b"exact shared source bytes\n")
    (origin / "executable.sh").write_text("#!/bin/sh\nexit 0\n")
    (origin / "executable.sh").chmod(0o755)
    git(origin, "init", "-b", "main")
    git(origin, "add", ".")
    git(origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "immutable probe")
    revision = git(origin, "rev-parse", "HEAD")
    url = origin.as_uri()
    source = "git+" + url + "@" + revision
    store = shared.SharedSourceStore(os.environ["AMPLIFIER_SOURCE_STORE"])
    target = store.checkout(url, revision)
    require(not target.exists(), "Source store was not cold")
    require(not Path(os.environ["AMPLIFIER_SOURCE_STORE"]).exists(), "Source store was already populated")
    records, calls = work / "worker-records.jsonl", work / "model-calls.txt"
    provider = work / "provider"
    (provider / "amplifier_module_provider_packaged_probe").mkdir(parents=True)
    (provider / "amplifier_module_provider_packaged_probe/__init__.py").write_text(PROVIDER)
    workspace = work / "workspace"
    workspace.mkdir()
    bundle = workspace / "bundle.yaml"
    bundle.write_text(json.dumps({"bundle": {"name": "packaged-probe", "version": "1.0"},
        "session": {"orchestrator": {"module": "loop-live", "source": "git+" + LOOP_URL + "@main"},
                    "context": {"module": "context-simple", "source": context_source}},
        "providers": [{"module": "provider-packaged-probe", "source": str(provider), "config": {"calls": str(calls)}}],
        "hooks": [{"module": "hook-context-intelligence", "source": source, "config": {"records": str(records)}}]}))
    settings = {"bundle": {"active": str(bundle), "app": []}, "web_bundles": {"excluded": list(APPENDED_DEFAULTS)}}
    require(app_behaviors(settings) == [], "Automatic overlays would widen this check")
    Path(os.environ["AMPLIFIER_HOME"]).mkdir(parents=True)
    save(Path(os.environ["AMPLIFIER_HOME"]) / "settings.yaml", settings)
    sid = "packaged-cache-probe"
    store_history = SessionStore.for_app(home, workspace)
    transcript = store_history.directory(sid) / "transcript.jsonl"
    manager = RuntimeManager(startup_timeout=180, preparation_timeout=180)
    manager.home = home
    events, processes, retained_rows = [], [], []
    native_ready = asyncio.Event()
    result["runs"] = []
    async def emit(kind, payload):
        events.append({"type": kind, **payload})
        if payload.get("event") == "session.ready":
            native_ready.set()
        with (output / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(events[-1]) + "\n")
    try:
        previous_object = None
        for resumed in (False, True):
            if resumed:
                # Fixture history, not a model call or replay. First owner is reaped.
                store_history.save(sid, ROWS, {"bundle": str(bundle), "name_auto": False})
                original = transcript.read_bytes()
            started = time.monotonic()
            native_ready.clear()
            actual_command = manager._command(None, home=home)
            require(actual_command[-1] == str(app / "runtime_worker.py"), "Command does not launch installed worker")
            await manager.start({"id": sid, "workspace": str(workspace), "bundle": str(bundle)}, emit)
            row = manager.workers[sid]
            retained_rows.append(row)
            processes.append(row["process"])
            await asyncio.wait_for(native_ready.wait(), timeout=20)
            verify_lifecycle(events, calls)
            report = row["ready"].result()
            require(bool(report["resumed"]) == resumed, "Incorrect new/resumed readiness")
            hook_records = [json.loads(line) for line in records.read_text().splitlines()]
            save(output / "hook-mount-records.json", hook_records)
            identity = select_worker_record(hook_records, row["native_worker_pid"], sid)
            save(output / ("resumed-worker.json" if resumed else "fresh-worker.json"), identity)
            require(identity["pid"] == row["native_worker_pid"], "Hook not from IPC-ready worker")
            require(identity["pid"] != os.getpid(), "Worker was not a separate process")
            require(identity["worker"] == str(app / "runtime_worker.py"), "Worker imported from wrong app")
            require(identity["app"] == str(app / "__init__.py"), "Worker used another app")
            require(identity["hooks"] == ["hook-context-intelligence"], "Unexpected mounted hook declarations")
            verify_identity(identity, prefix=project / ".venv", checkout=args.checkout)
            require(Path(identity["hook"]).is_relative_to(target), "Hook bypassed shared source publication")
            provenance = await manager.provenance(sid, ["amplifier_foundation.sources.shared", "__main__"])
            require(provenance["status"] == "observed", "IPC provenance unavailable")
            shared_row = next(item for item in provenance["modules"] if item["module"] == "amplifier_foundation.sources.shared")
            require(shared_row["loaded"] and shared_row["currentFileSha256"] == SHARED_SHA256,
                    "IPC loaded Foundation identity mismatch")
            require(shared_row["origin"] == identity["foundation"], "IPC and hook origins differ")
            observed = inspect_object(target, origin, revision)
            require(store.verify(url, revision) == target, "Host could not verify worker publication")
            if previous_object:
                require(observed == previous_object, "New worker did not reuse exact source object")
            previous_object = observed
            history = await manager.control(sid, "history.snapshot")
            if resumed:
                require(history["messages"] == ROWS, "Mounted history differs")
                require(transcript.read_bytes() == original, "Resume rewrote saved transcript bytes")
            usage = await manager.control(sid, "usage.inspect")
            require(usage["calls"] == 0 and not calls.exists(), "Unexpected model generation")
            run = {"resumed": resumed, "readySeconds": round(time.monotonic() - started, 3),
                   "command": actual_command, "worker": identity, "provenance": provenance,
                   "object": observed, "readyReport": report, "modelCalls": usage["calls"],
                   "historySha256": digest(transcript), "historyPreserved": resumed}
            result["runs"].append(run)
            await manager.stop(sid)
            verify_lifecycle(events, calls)
            timing = home / "runtime-reports" / sid / "startup-timing.json"
            if timing.exists():
                shutil.copy2(timing, output / ("resumed-timing.json" if resumed else "fresh-timing.json"))
            (output / ("resumed-stderr.txt" if resumed else "fresh-stderr.txt")).write_text(
                "\n".join(row.get("stderr", [])))
            require(row["process"].returncode == 0, "Worker did not exit cleanly")
            run["returncode"] = row["process"].returncode
            if resumed:
                require(transcript.read_bytes() == original, "Shutdown changed resumed transcript")
        require(result["runs"][0]["worker"]["pid"] != result["runs"][1]["worker"]["pid"], "Resume reused first process")
        verify_lifecycle(events, calls)
        result["assertions"] = {"coldPublication": True, "exactBytesModes": True, "sourceReuse": True,
                                "freshReady": True, "newWorkerResume": True, "historyPreserved": True, "modelCalls": 0}
    except BaseException as error:
        diagnostic = getattr(error, "diagnostic_path", None)
        if diagnostic:
            diagnostic = Path(diagnostic).resolve()
            if diagnostic.is_relative_to(home) and diagnostic.is_file() and diagnostic.stat().st_size < 100_000:
                shutil.copy2(diagnostic, output / diagnostic.name)
        raise
    finally:
        # Capture bounded production diagnostics on failure; never upload homes.
        for name, row in manager.workers.items():
            (output / (name + "-stderr.txt")).write_text("\n".join(row.get("stderr", [])))
        await manager.close()
        for index, row in enumerate(retained_rows):
            (output / f"worker-{index}-stderr.txt").write_text("\n".join(row.get("stderr", [])))
        result["cleanup"] = {"remainingWorkers": list(manager.workers),
                             "processReturncodes": [proc.returncode for proc in processes],
                             "runnerOwnsDisposableFiles": str(work)}
        require(not manager.workers and all(proc.returncode is not None for proc in processes), "Worker cleanup is unconfirmed")
        for path in (home / "runtime-reports" / sid).glob("*startup*.json"):
            if path.stat().st_size < 100_000:
                shutil.copy2(path, output / path.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("work", "output", "wheel", "loop-source", "checkout"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name in ("work", "output", "wheel", "loop_source", "checkout"):
        setattr(args, name, getattr(args, name).resolve())
    require(sys.platform == "darwin", "This acceptance owner requires macOS")
    require(not Path.cwd().is_relative_to(args.checkout), "Run outside checkout")
    require(not Path(__file__).resolve().is_relative_to(args.checkout), "Copy the probe outside checkout")
    require(digest(args.wheel) == WHEEL_SHA256, "Published wheel checksum differs")
    require(git(args.loop_source, "rev-parse", "HEAD") == LOOP, "Private loop checkout differs")
    args.work.mkdir(mode=0o700)
    args.output.mkdir(mode=0o700)
    for path in (args.work / "home", args.work / "tmp"):
        path.mkdir(mode=0o700)
    result = {"status": "running", "platform": platform.platform(), "machine": platform.machine(),
              "python": sys.version, "uid": os.getuid(), "wheelSha256": digest(args.wheel),
              "foundationCommit": FOUNDATION, "loopCommit": LOOP, "skips": [],
              "limitations": ["Not a reproduction of the macOS 13 reporter restriction.",
                  "Older readers sharing this store do not enforce the new final-root completion seal.",
                  "Dependency environments are prepared before cold source publication/startup.",
                  "Synthetic no-generation bundle; not default-profile or paid-provider qualification."]}
    environment = clean_environment(args.work, args.loop_source)
    os.environ.clear()
    os.environ.update(environment)
    try:
        asyncio.run(exercise(args, result))
        result["status"] = "passed"
    except BaseException as error:
        result.update(status="failed", errorType=type(error).__name__, error=str(error))
        (args.output / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        save(args.output / "result.json", result)
        save(args.output / "artifact-hashes.json", {p.name: digest(p) for p in args.output.iterdir() if p.is_file()})
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
