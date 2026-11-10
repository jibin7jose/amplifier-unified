"""CI-only cold default-command check; not browser/default-bundle acceptance.

Run with a noneditable candidate host Python and its exact wheel. No worker
project, lock, venv, uv process or prepare_project double is supplied. Network
resolution/builds from declared sources are allowed in this GitHub setup check;
only the conversation provider is scripted. Private logs stay under --root.

The cold journey is uninstrumented. Retained measurement helpers and closed
schemas support historical failure evidence/tests only; the entry no longer
creates a worker import hook or calls those helpers.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import importlib.metadata as metadata
import json
import marshal
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import tomllib
import types
import zipfile


APP_FILES = ("__init__.py", "runtime.py", "runtime_worker.py", "runtime_bootstrap.py",
             "runtime_environment.py", "windows_jobs.py", "windows_launch_guard.py",
             "worker_diagnostics.py")
IMPORTS = {
    "amplifier_core": "amplifier-core",
    "amplifier_foundation": "amplifier-foundation",
    "amplifier_module_loop_live": "amplifier-module-loop-live",
    "amplifier_ui_use": "amplifier-bundle-ui-use",
    "amplifier_module_tool_ui_use": "amplifier-bundle-ui-use",
}
CHECKS = ("installedHost", "coldDefaultCommand", "workerIdentity", "scriptedTurn",
          "workerProvenance", "ownedJobCleanup")
CHECKPOINTS = frozenset({
    "runtime_preflight", "input_paths", "private_root", "private_log", "fixture_home", "journey_imports",
    "installed_import", "installed_distribution", "installed_direct_url", "installed_origin",
    "installed_members", "installed_manifest", "journey", "complete",
    "outer_run", "outer_close", "parent_output_json", "parent_output_schema",
    "inner_report_schema", "runtime_start", "input_admission",
})
ERROR_CLASSES = {kind: kind.__name__ for kind in (
    AssertionError, OSError, PermissionError, FileNotFoundError, NotADirectoryError,
    IsADirectoryError, ImportError, ModuleNotFoundError, ValueError, TypeError,
    AttributeError, KeyError, RuntimeError, NotImplementedError, TimeoutError,
    UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError,
    subprocess.TimeoutExpired, subprocess.CalledProcessError,
)}
CODE_LABELS = {
    "cold_main": (__name__, "main"),
    "cold_journey": (__name__, "journey"),
    "cold_installed_host": (__name__, "installed_host"),
    "cold_outer": (__name__, "run_owned_probe"),
    "cold_start": (__name__, "observe_default_start"),
    "private_directory": ("amplifier_web.windows_files", "PinnedDirectory.__init__"),
    "private_acl": ("amplifier_web.windows_files", "_API.validate_acl"),
    "private_file_info": ("amplifier_web.windows_files", "_API.info"),
    "runtime_start": ("amplifier_web.runtime", "RuntimeManager.start"),
    "runtime_start_locked": ("amplifier_web.runtime", "RuntimeManager._start_locked"),
    "runtime_identify": ("amplifier_web.runtime", "_WindowsWorkerOwner.identify"),
    "runtime_readline": ("amplifier_web.runtime", "_BoundedWorkerLines.readline"),
    "runtime_write": ("amplifier_web.runtime", "RuntimeManager._write"),
    "service_send": ("amplifier_web.service", "AppService._send"),
    "service_dispatch": ("amplifier_web.service", "AppService.dispatch"),
    "service_dispatch_action": ("amplifier_web.service", "AppService._dispatch"),
    "naming_persist": ("amplifier_web.naming", "persist"),
    "naming_policy": ("amplifier_web.naming", "set_automatic"),
    "naming_metadata_set": ("amplifier_foundation.session.metadata", "SessionMetadataStore.set_name"),
    "naming_metadata_save": ("amplifier_foundation.session.history", "SessionHistoryStore._save_metadata_unlocked"),
    "naming_metadata_write": ("amplifier_foundation.session.history", "SessionHistoryStore._write_prepared"),
    "naming_backup_write": ("amplifier_foundation.io.files", "write_with_backup"),
    "naming_atomic_write": ("amplifier_foundation.io.files", "_write_atomic"),
    "naming_private_write": ("amplifier_foundation.session._windows_history", "write_prepared"),
    "runtime_send": ("amplifier_web.runtime", "RuntimeManager.send"),
    "runtime_input_start": ("amplifier_web.runtime", "RuntimeManager._start_for_input"),
    "runtime_reply": ("amplifier_web.runtime", "RuntimeManager._reply"),
    "runtime_profiles": ("amplifier_web.runtime_profiles", "ensure"),
    "worker_start": ("__main__", "Worker.start"),
    "worker_bridge": ("__main__", "Worker.bridge"),
    "manager_prepare": ("amplifier_web.host.session", "prepare_manager"),
    "bundle_prepare": ("amplifier_foundation.bundle._dataclass", "Bundle.prepare"),
    "session_create": ("amplifier_foundation.bundle._prepared", "PreparedBundle.create_session"),
    "module_resolve": ("amplifier_foundation.bundle._prepared", "BundleModuleResolver.async_resolve"),
    "module_activate": ("amplifier_foundation.modules.activator", "ModuleActivator.activate_all"),
    "module_activate_one": ("amplifier_foundation.modules.activator", "ModuleActivator.activate"),
    "module_install": ("amplifier_foundation.modules.activator", "ModuleActivator._install_dependencies"),
    "module_install_uncached": ("amplifier_foundation.modules.activator", "ModuleActivator._install_dependencies_uncached"),
    "source_resolve": ("amplifier_foundation.sources.resolver", "SimpleSourceResolver.resolve"),
    "git_resolve": ("amplifier_foundation.sources.git", "GitSourceHandler.resolve"),
    "git_resolve_locked": ("amplifier_foundation.sources.git", "GitSourceHandler._resolve_locked"),
    "file_resolve": ("amplifier_foundation.sources.file", "FileSourceHandler.resolve"),
    "filelock_acquire": ("filelock.asyncio", "BaseAsyncFileLock.acquire"),
    "thread_wait": ("asyncio.threads", "to_thread"),
    "shared_resolve": ("amplifier_foundation.sources.shared", "resolve_shared_source"),
    "shared_store_resolve": ("amplifier_foundation.sources.shared", "SharedSourceStore.resolve"),
    "shared_ensure": ("amplifier_foundation.sources.shared", "SharedSourceStore.ensure"),
    "shared_io": ("amplifier_foundation.sources.shared", "complete_io"),
    "core_initialize": ("amplifier_core.session", "AmplifierSession.initialize"),
    "core_initialize_session": ("amplifier_core._session_init", "initialize_session"),
    "core_load": ("amplifier_core.loader", "ModuleLoader.load"),
    "core_validate": ("amplifier_core.loader", "ModuleLoader._validate_module"),
}
# Exact public protocol constants only. Never publish arbitrary exception prose,
# paths, provider responses or a digest of unknown text.
ADMISSION_REASONS = {
    "Session is not ready": "worker_not_ready",
    "Session is not running": "worker_not_running",
    "The session is parked and has no active shared writer.": "worker_parked",
    "The session has not finished loading successfully.": "worker_not_loaded",
    "The conversation already has a worker. Retry the bundle change after it settles.": "replacement_has_worker",
    "Already started": "worker_already_started",
    "Session is closed": "loop_closed",
    "This session activation has been released or superseded.": "activation_released",
}
# Worker replies intentionally frame the original exception type; the host
# preserves that string in its RuntimeError. Match only these exact constants.
ADMISSION_REASONS.update({"RuntimeError: " + text: reason
                          for text, reason in tuple(ADMISSION_REASONS.items())})
STARTUP_PHASES = frozenset({"runtime-setup", "bundle-preparation", "session-mount", "ready"})
ACTIVATION_MODULES = frozenset({"loop-live", "context-simple", "hooks-session-naming", "other"})
# Existing root/child facts can approach 4 KiB. Eight bounded operation counters
# and code fingerprints need explicit headroom, not an unbounded private log.
WORKER_DIAGNOSTIC_BYTES = 16 * 1024
THREAD_OPERATIONS = {
    "receipt_reuse": ("amplifier_foundation.modules.preparation", "PreparationReceipt.reusable"),
    "receipt_record": ("amplifier_foundation.modules.preparation", "PreparationReceipt.record"),
    "shared_verify": ("amplifier_foundation.sources.shared", "SharedSourceStore.verify"),
    "shared_clean": ("amplifier_foundation.sources.shared", "clean_checkout"),
    "git_clone": ("amplifier_foundation.sources.git", "GitSourceHandler._clone_at_commit"),
    "shared_build": ("amplifier_foundation.sources.shared", "build_view"),
    # Nested callables are selected from the exact loaded parent's co_consts.
    "shared_remote": ("amplifier_foundation.sources.shared", "remote_revision.<read>"),
    "shared_publish": ("amplifier_foundation.sources.shared", "SharedSourceStore.ensure.<publish>"),
}
SUBPROCESS_EVENTS = frozenset({"run", "first_communicate", "kill", "drain_communicate", "wait"})
# Retain the outer lifeline and its scratch identity when the primitive cannot
# confirm cleanup. A public FAIL record never authorizes a retry or deletion.
FAILED_OUTER_OWNERS = []
PROVIDER = r'''
import hashlib, importlib, importlib.metadata as metadata, json, os, sys
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
__amplifier_module_type__ = "provider"
class Provider:
    name = "fixture"
    def __init__(self, config=None, *, coordinator=None):
        # Metadata is read before mount, without a coordinator or live config.
        # Only mount binds the actual worker for the provenance-producing turn.
        self.coordinator, self.config = coordinator, config or {}
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Scripted fixture", defaults={"model":"fixture"})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def complete(self, request, **kwargs):
        if (self.coordinator.session_id == "test-session"
                or not any(m.role == "user" and "DEFAULT_COMMAND_TURN" in str(m.content) for m in request.messages)):
            return ChatResponse(content=[TextBlock(text="Fixture validation")])
        config = self.config
        prefix = Path(config["project"]) / ".venv"
        assert Path(sys.prefix).resolve() == prefix.resolve()
        assert Path(sys.executable).samefile(prefix / "Scripts/python.exe")
        app = importlib.import_module("amplifier_web")
        app_dir = Path(app.__file__).resolve().parent
        assert app_dir == Path(config["app"]).resolve()
        assert Path(sys.modules["__main__"].__file__).resolve() == app_dir / "runtime_worker.py"
        # Actual shipped bootstrap exposes app packages, NOT the host's whole
        # site-packages directory or distribution metadata to worker resolution.
        assert app_dir.parent not in {Path(p).resolve() for p in sys.path if p}
        try: metadata.distribution("amplifier-unified")
        except metadata.PackageNotFoundError: pass
        else: raise AssertionError("Host distribution metadata leaked into worker")
        for name, expected in config["appHashes"].items():
            assert hashlib.sha256((app_dir / name).read_bytes()).hexdigest() == expected
        imports = {}
        for package, distribution in config["imports"].items():
            module = importlib.import_module(package)
            origin = Path(module.__file__).resolve()
            dist = metadata.distribution(distribution)
            assert origin.is_relative_to(prefix.resolve())
            assert origin in {Path(dist.locate_file(f)).resolve() for f in dist.files or ()}
            direct = json.loads(dist.read_text("direct_url.json") or "{}")
            assert not direct.get("dir_info", {}).get("editable")
            imports[package] = {"version":dist.version,
                "commit":direct.get("vcs_info", {}).get("commit_id"),
                "sha256":hashlib.sha256(origin.read_bytes()).hexdigest()}
        proof = {"pid":os.getpid(), "imports":imports, "appHashes":config["appHashes"]}
        # Exactly one real request, separate from Core's validation request.
        with Path(config["proof"]).open("x", encoding="utf-8") as stream:
            json.dump(proof, stream)
        return ChatResponse(content=[TextBlock(text="Default command caf\u00e9 verified")])
async def mount(coordinator, config=None):
    await coordinator.mount("providers", Provider(config, coordinator=coordinator), name="fixture")
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def empty_inner_report():
    return {"schema": 1, "status": "FAIL", "stage": "preflight",
            "checks": dict.fromkeys(CHECKS, False), "wheelSha256": None,
            "manifestSha256": None, "lockSha256": None, "appHashes": {}, "workerImports": {}}


def checkpoint(report, name):
    assert name in CHECKPOINTS
    report["diagnostic"] = {"checkpoint": name, "errors": []}


def failure_facts(error, name):
    """Fixed labels only; no exception text, filenames, symbols or frame locals."""
    try:
        return _failure_facts(error, name)
    except BaseException:
        # A diagnostic fault cannot skip the caller's retained cleanup/report.
        # Original outcome remains FAIL; an unknown class is not a cause claim.
        return {"checkpoint": name, "errors": [{"class": "other", "frames": []}]}


def _failure_facts(error, name):
    assert name in CHECKPOINTS
    witness = None
    if name == "input_admission" and type(error) is RuntimeError:
        from amplifier_web.worker_diagnostics import runtime_failure_witness
        witness = runtime_failure_witness(error)
    codes = {}
    for label, (module_name, qualname) in CODE_LABELS.items():
        value = sys.modules.get(module_name)
        for part in qualname.split("."):
            value = getattr(value, part, None) if value is not None else None
        code = getattr(value, "__code__", None)
        if isinstance(code, types.CodeType):
            # Code equality is structural. Retain each exact loaded object so
            # an equal-but-distinct clone cannot claim its diagnostic label.
            codes[id(code)] = (code, label)
    rows, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(rows) < 4:
        seen.add(id(error))
        kind = type(error)
        row = {"class": ERROR_CLASSES.get(kind, "other"), "frames": []}
        if kind is RuntimeError and name == "input_admission":
            args = BaseException.__getattribute__(error, "args")
            row["reason"] = (ADMISSION_REASONS.get(args[0], "other")
                             if len(args) == 1 and type(args[0]) is str else "other")
        if kind in ERROR_CLASSES and isinstance(error, OSError):
            for key in ("errno", "winerror"):
                number = getattr(error, key, None)
                if type(number) is int and -(2**31) <= number < 2**32:
                    row[key] = number
        if kind is subprocess.CalledProcessError and type(error.returncode) is int:
            if -(2**31) <= error.returncode < 2**32:
                row["returncode"] = error.returncode
        tb, scanned = BaseException.__getattribute__(error, "__traceback__"), 0
        while tb is not None and scanned < 128:
            scanned += 1
            code = tb.tb_frame.f_code
            known = codes.get(id(code))
            if known is not None and known[0] is code:
                row["frames"].append({"source": known[1],
                    "offset": max(0, min(tb.tb_lineno - code.co_firstlineno, 10**6))})
                row["frames"] = row["frames"][-5:]
            tb = tb.tb_next
        rows.append(row)
        cause = BaseException.__getattribute__(error, "__cause__")
        error = cause if cause is not None else (
            None if BaseException.__getattribute__(error, "__suppress_context__")
            else BaseException.__getattribute__(error, "__context__"))
    return {"checkpoint": name, "errors": rows,
            **({"runtimeFailure": witness} if witness is not None else {})}


def validate_diagnostic(value):
    assert type(value) is dict and {"checkpoint", "errors"} <= set(value) <= {"checkpoint", "errors", "runtimeFailure"}
    assert type(value["checkpoint"]) is str and value["checkpoint"] in CHECKPOINTS
    if "runtimeFailure" in value:
        from amplifier_web.worker_diagnostics import validate_runtime_failure_witness
        assert value["checkpoint"] == "input_admission"
        assert validate_runtime_failure_witness(value["runtimeFailure"]) is not None
    assert type(value["errors"]) is list and len(value["errors"]) <= 4
    for row in value["errors"]:
        assert type(row) is dict and {"class", "frames"} <= set(row) <= {"class", "frames", "errno", "winerror", "returncode", "reason"}
        assert type(row["class"]) is str and row["class"] in {*ERROR_CLASSES.values(), "other"}
        if "reason" in row:
            assert value["checkpoint"] == "input_admission" and row["class"] == "RuntimeError"
            assert type(row["reason"]) is str and row["reason"] in {*ADMISSION_REASONS.values(), "other"}
        if "returncode" in row:
            assert row["class"] == "CalledProcessError"
        for key in ("errno", "winerror", "returncode"):
            if key in row:
                assert type(row[key]) is int and -(2**31) <= row[key] < 2**32
        assert type(row["frames"]) is list and len(row["frames"]) <= 5
        for frame in row["frames"]:
            assert type(frame) is dict and set(frame) == {"source", "offset"}
            assert type(frame["source"]) is str and frame["source"] in CODE_LABELS
            assert type(frame["offset"]) is int and 0 <= frame["offset"] <= 10**6


def validate_worker_startup(value):
    assert type(value) is dict and set(value) - {
        "activationChildren", "activationTruncated", "foundation", "codeHashes",
        "threadOperations", "operationsTruncated"} == {
        "diagnosticStatus", "observed", "taskDone", "awaiting", "errors"}
    assert type(value["diagnosticStatus"]) is str and value["diagnosticStatus"] in {"PENDING", "PASS", "FAIL"}
    assert type(value["observed"]) is bool and type(value["taskDone"]) is bool
    if value["diagnosticStatus"] == "PASS":
        assert value["observed"]
    validate_diagnostic({"checkpoint": "runtime_start", "errors": [
        {"class": "other", "frames": value["awaiting"]}]})
    validate_diagnostic({"checkpoint": "runtime_start", "errors": value["errors"]})
    if "activationChildren" in value or "activationTruncated" in value:
        assert type(value["activationTruncated"]) is bool
        children = value["activationChildren"]
        assert type(children) is list and 1 <= len(children) <= 4
        for child in children:
            assert type(child) is dict and set(child) == {
                "module", "taskDone", "cancelled", "awaiting", "errors"}
            assert type(child["module"]) is str and child["module"] in ACTIVATION_MODULES
            assert type(child["taskDone"]) is bool and type(child["cancelled"]) is bool
            assert type(child["awaiting"]) is list and len(child["awaiting"]) <= 3
            assert type(child["errors"]) is list and len(child["errors"]) <= 1
            validate_diagnostic({"checkpoint": "runtime_start", "errors": [
                {"class": "other", "frames": child["awaiting"]}, *child["errors"]]})
            assert all(len(row["frames"]) <= 2 for row in child["errors"])
    if "foundation" in value:
        row = value["foundation"]
        assert type(row) is dict and set(row) == {"status", "version", "commit"}
        assert type(row["status"]) is str and row["status"] in {"not_loaded", "missing", "invalid", "no_vcs", "verified"}
        assert row["version"] is None or (type(row["version"]) is str and
            re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", row["version"]))
        assert row["commit"] is None or (type(row["commit"]) is str and
            re.fullmatch(r"[0-9a-f]{40,64}", row["commit"]))
        if row["status"] == "verified":
            assert row["version"] is not None and row["commit"] is not None
        elif row["status"] != "no_vcs":
            assert row["version"] is None and row["commit"] is None
        else:
            assert row["version"] is not None and row["commit"] is None
    if "codeHashes" in value:
        hashes = value["codeHashes"]
        assert type(hashes) is dict and len(hashes) <= 16
        assert all(type(key) is str and key in CODE_LABELS | THREAD_OPERATIONS
                   and type(raw) is str and re.fullmatch("[0-9a-f]{64}", raw)
                   for key, raw in hashes.items())
    if "threadOperations" in value or "operationsTruncated" in value:
        assert type(value["operationsTruncated"]) is bool
        assert type(value["threadOperations"]) is list and len(value["threadOperations"]) <= 8
        for row in value["threadOperations"]:
            assert type(row) is dict and set(row) - {"subprocessLifecycle"} == {
                "operation", "module", "queued", "entered", "returned", "failed", "errors"}
            assert type(row["operation"]) is str and row["operation"] in {*THREAD_OPERATIONS, "other"}
            assert type(row["module"]) is str and row["module"] in ACTIVATION_MODULES
            assert all(type(row[key]) is int and 0 <= row[key] <= 10**6
                       for key in ("queued", "entered", "returned", "failed"))
            assert row["entered"] <= row["queued"]
            assert row["returned"] + row["failed"] <= row["entered"]
            assert type(row["errors"]) is list and len(row["errors"]) <= 1
            validate_diagnostic({"checkpoint": "runtime_start", "errors": row["errors"]})
            assert all(error["frames"] == [] for error in row["errors"])
            if "subprocessLifecycle" in row:
                assert row["operation"] == "shared_remote"
                validate_subprocess_lifecycle(row["subprocessLifecycle"])


def validate_subprocess_lifecycle(value):
    assert type(value) is dict and set(value) == {"status", "timeoutSeen", "events", "codeHashes"}
    assert type(value["status"]) is str and value["status"] in {"PENDING", "PASS", "FAIL", "refused"}
    assert type(value["timeoutSeen"]) is bool
    assert type(value["codeHashes"]) is dict and set(value["codeHashes"]) <= {"run", "communicate", "kill", "wait"}
    assert all(type(raw) is str and re.fullmatch("[0-9a-f]{64}", raw)
               for raw in value["codeHashes"].values())
    assert type(value["events"]) is dict and set(value["events"]) <= SUBPROCESS_EVENTS
    for row in value["events"].values():
        assert type(row) is dict and set(row) == {"entered", "exited", "entryBucket", "exitBucket"}
        assert all(type(row[key]) is int and 0 <= row[key] <= 10**6 for key in ("entered", "exited"))
        assert row["exited"] <= row["entered"]
        assert type(row["entryBucket"]) is int and 0 <= row["entryBucket"] <= 8
        assert row["exitBucket"] is None or (type(row["exitBucket"]) is int and 0 <= row["exitBucket"] <= 8)
        assert (row["exitBucket"] is None) == (row["exited"] == 0)
        if row["exitBucket"] is not None:
            assert row["exitBucket"] >= row["entryBucket"]
    if value["timeoutSeen"]:
        assert "kill" in value["events"]
    if "drain_communicate" in value["events"]:
        assert value["timeoutSeen"]


@contextmanager
def observe_shared_subprocess(function, publish):
    """Thread-local read-only profiling of one exact shared_remote invocation.

    Only original loaded run/communicate/kill/wait code is observed. The run
    frame must descend from this exact read callable; method frames must bind
    its retained process object. Never inspect arguments/output or signal it.
    'exited' includes exceptional unwind, not successful return or process exit.
    Elapsed buckets: <1, <5, <30, <40, <60, <300, <900, <1200, >=1200 seconds.
    """
    facts = {"status": "PENDING", "timeoutSeen": False, "events": {}, "codeHashes": {}}
    def send():
        try:
            publish({**facts, "events": {key: dict(row) for key, row in facts["events"].items()},
                     "codeHashes": dict(facts["codeHashes"])})
        except BaseException:
            facts["status"] = "FAIL"

    try:
        previous = sys.getprofile()
        codes = {name: getattr(subprocess.run if name == "run" else getattr(subprocess.Popen, name),
                               "__code__", None) for name in ("run", "communicate", "kill", "wait")}
        assert all(type(code) is types.CodeType for code in codes.values())
        facts["codeHashes"] = {name: code_fingerprint(code) for name, code in codes.items()}
        target = function.__code__
        started = time.monotonic()
    except BaseException:
        facts["status"] = "FAIL"
        send()
        yield
        return
    if previous is not None:
        facts["status"] = "refused"
        send()
        yield
        return
    run_frame = process = None
    active = {}

    def event(name, entering):
        elapsed = max(0, time.monotonic() - started)
        bucket = sum(elapsed >= bound for bound in (1, 5, 30, 40, 60, 300, 900, 1200))
        row = facts["events"].setdefault(name, {
            "entered": 0, "exited": 0, "entryBucket": bucket, "exitBucket": None})
        key = "entered" if entering else "exited"
        row[key] = min(row[key] + 1, 10**6)
        row["entryBucket" if entering else "exitBucket"] = bucket
        send()

    def profile(frame, kind, arg):
        nonlocal run_frame, process
        try:
            if kind == "call":
                code = frame.f_code
                if code is codes["run"]:
                    caller = frame.f_back
                    for _ in range(8):
                        if caller is None or caller.f_code is target:
                            break
                        caller = caller.f_back
                    if caller is None or caller.f_code is not target:
                        return
                    if run_frame is not None:
                        facts["status"] = "FAIL"  # Unexpected second run, no relabel.
                        send()
                        return
                    run_frame = frame
                    name = "run"
                elif run_frame is not None and (
                        frame.f_back is run_frame or
                        (code is codes["wait"] and id(frame.f_back) in active
                         and active[id(frame.f_back)][0] is frame.f_back
                         and active[id(frame.f_back)][1] in {"first_communicate", "drain_communicate"})):
                    candidate = frame.f_locals.get("self")
                    retained = run_frame.f_locals.get("process")
                    if retained is None or candidate is not retained:
                        return
                    if process is None and code is codes["communicate"]:
                        process = retained
                    if candidate is not process:
                        return
                    if code is codes["communicate"]:
                        timeout = frame.f_locals.get("timeout")
                        if "first_communicate" not in facts["events"] and type(timeout) in {int, float} and timeout == 35:
                            name = "first_communicate"
                        elif facts["timeoutSeen"] and timeout is None and "kill" in facts["events"]:
                            name = "drain_communicate"
                        else:
                            facts["status"] = "FAIL"
                            send()
                            return
                    elif code is codes["kill"]:
                        name = "kill"
                        facts["timeoutSeen"] |= type(run_frame.f_locals.get("exc")) is subprocess.TimeoutExpired
                    elif code is codes["wait"]:
                        name = "wait"
                    else:
                        return
                else:
                    return
                active[id(frame)] = (frame, name)
                event(name, True)
            elif kind == "return":
                retained = active.pop(id(frame), None)
                if retained is not None and retained[0] is frame:
                    event(retained[1], False)
        except BaseException:
            facts["status"] = "FAIL"
            send()

    facts["status"] = "PASS"
    installed = False
    try:
        try:
            sys.setprofile(profile)
            installed = True
        except BaseException:
            facts["status"] = "FAIL"
        send()
        yield
    finally:
        try:
            if installed and sys.getprofile() is profile:
                sys.setprofile(previous)
            elif installed:
                facts["status"] = "FAIL"  # Do not overwrite somebody else's hook.
        except BaseException:
            facts["status"] = "FAIL"
        send()


def loaded_code(module, qualname):
    value = sys.modules.get(module)
    for part in qualname.split("."):
        if part.startswith("<"):
            parent = getattr(value, "__code__", None)
            return next((code for code in getattr(parent, "co_consts", ())
                         if type(code) is types.CodeType and code.co_name == part[1:-1]), None)
        value = getattr(value, part, None) if value is not None else None
    return getattr(value, "__code__", None)


def code_fingerprint(code):
    """Interpreter-specific code digest, with all filenames normalized."""
    def normalize(value):
        return value.replace(co_filename="<loaded-code>", co_consts=tuple(
            normalize(item) if type(item) is types.CodeType else item for item in value.co_consts))
    return digest(marshal.dumps(normalize(code)))


def worker_foundation():
    """Read only the already loaded worker package and its own distribution."""
    empty = {"status": "not_loaded", "version": None, "commit": None}
    module = sys.modules.get("amplifier_foundation")
    if module is None:
        return empty
    try:
        dist = metadata.distribution("amplifier-foundation")
        origin = Path(module.__file__).resolve()
        assert origin.is_relative_to(Path(sys.prefix).resolve())
        assert origin == Path(dist.locate_file("amplifier_foundation/__init__.py")).resolve()
        version = dist.version
        assert type(version) is str and re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", version)
        raw = dist.read_text("direct_url.json")
        if raw is None:
            return {"status": "no_vcs", "version": version, "commit": None}
        assert len(raw) <= 8192
        direct = json.loads(raw)
        assert type(direct) is dict and not direct.get("dir_info", {}).get("editable")
        vcs = direct.get("vcs_info")
        if vcs is None:
            return {"status": "no_vcs", "version": version, "commit": None}
        commit = vcs.get("commit_id")
        assert vcs.get("vcs") == "git" and type(commit) is str and re.fullmatch(r"[0-9a-f]{40,64}", commit)
        return {"status": "verified", "version": version, "commit": commit}
    except metadata.PackageNotFoundError:
        return {**empty, "status": "missing"}
    except Exception:
        return {**empty, "status": "invalid"}


def install_worker_start_observer(path):
    """Diagnostic-only profile callback; schedule snapshots AFTER coroutine yield.

    No task, timer, worker wrapper, command override or early app import. Only
    the exact loaded Worker.start can arm a snapshot. Child snapshots require
    exact activate coroutines listed in its trusted activate_all gather frame.
    The bounded await walks emit labels/offsets, never arbitrary task data.
    """
    facts = {"diagnosticStatus": "PENDING", "observed": False, "taskDone": False, "awaiting": [], "errors": []}
    previous = None
    failed = False
    root_task = None
    child_rows = {}  # Retain at most four exact task identities, never emit IDs.
    operations = {}  # At most eight fixed operation/module counter rows.
    fingerprint_codes = {}

    def persist():
        nonlocal previous
        raw = json.dumps(facts, sort_keys=True)
        assert len(raw.encode("utf-8")) <= WORKER_DIAGNOSTIC_BYTES
        if raw != previous:
            # The parent fixture privately created this file before payload.
            with path.open("r+", encoding="utf-8") as stream:
                stream.seek(0)
                stream.write(raw)
                stream.truncate()
            previous = raw

    def code_for(module, qualname):
        return loaded_code(module, qualname)

    def fingerprint(label, code):
        hashes = facts.setdefault("codeHashes", {})
        if label in hashes:
            assert fingerprint_codes[label] is code  # Rebinding is not one provenance.
            return
        if label not in hashes and len(hashes) < 16:
            hashes[label] = code_fingerprint(code)
            fingerprint_codes[label] = code

    def codes_now():
        codes = {}
        for label, (module, qualname) in CODE_LABELS.items():
            code = code_for(module, qualname)
            if isinstance(code, types.CodeType):
                codes[id(code)] = (code, label)
        return codes

    def frames_now(task, codes):
        value, frames = task.get_coro(), []
        for _ in range(64):
            if type(value) is not types.CoroutineType:
                break
            code, frame = value.cr_code, value.cr_frame
            known = codes.get(id(code))
            if frame is not None and known is not None and known[0] is code:
                fingerprint(known[1], code)
                frames.append({"source": known[1],
                    "offset": max(0, min(frame.f_lineno - code.co_firstlineno, 10**6))})
            value = value.cr_await
        return frames

    def scoped_child(task, frame):
        if root_task is None or root_task.done() or root_task.cancelling():
            return False
        code = code_for("amplifier_foundation.modules.activator", "ModuleActivator.activate_all")
        value = root_task.get_coro()
        for _ in range(64):
            if type(value) is not types.CoroutineType:
                break
            if value.cr_code is code and value.cr_frame is not None:
                scope = value.cr_frame.f_locals
                # Only identity comparisons on the exact trusted gather frame.
                # Do not discover all_tasks or read arbitrary task names/args.
                tasks = scope.get("tasks")
                return (scope.get("self") is frame.f_locals.get("self")
                        and type(tasks) is list and len(tasks) <= 128
                        and any(coro is task.get_coro() for coro in tasks))
            value = value.cr_await
        return False

    def activation_module(task):
        if root_task is None or root_task.done() or root_task.cancelling():
            return None
        value = task.get_coro()
        code = code_for("amplifier_foundation.modules.activator", "ModuleActivator.activate")
        if (type(value) is types.CoroutineType and value.cr_code is code
                and value.cr_frame is not None and scoped_child(task, value.cr_frame)):
            name = value.cr_frame.f_locals.get("module_name")
            return name if type(name) is str and name in ACTIVATION_MODULES else "other"
        # complete_io deliberately creates a child task. Follow ONLY the exact
        # trusted coroutine's retained task edge, never global task discovery.
        complete = code_for("amplifier_foundation.sources.shared", "complete_io")
        for child, row in child_rows.values():
            value = child.get_coro()
            for _ in range(64):
                if type(value) is not types.CoroutineType:
                    break
                if (value.cr_code is complete and value.cr_frame is not None
                        and value.cr_frame.f_locals.get("task") is task):
                    return row["module"]
                value = value.cr_await
        return None

    def operation_event(row, event, error=None):
        nonlocal failed
        try:
            row[event] = min(row[event] + 1, 10**6)
            if error is not None:
                row["errors"] = error
            persist()
        except BaseException:
            failed = True
            facts["diagnosticStatus"] = "FAIL"
            try:
                persist()
            except BaseException:
                pass

    def lifecycle_event(row, value):
        nonlocal failed
        try:
            validate_subprocess_lifecycle(value)
            row["subprocessLifecycle"] = value
            if value["status"] != "PASS":
                failed = True
                facts["diagnosticStatus"] = "FAIL"
            persist()
        except BaseException:
            failed = True
            facts["diagnosticStatus"] = "FAIL"

    def observe_thread_call(frame):
        """CPython 3.13 write-through locals, on the ORIGINAL to_thread entry.

        Original to_thread still owns context propagation, executor submission
        and cancellation. The sole callable wrapper reports entry/return/error
        and invokes the identical function/args exactly once. No thread starts
        here. Unknown callable code is labelled other, never introspected.
        """
        module = activation_module(asyncio.current_task())
        if module is None:
            return
        function = frame.f_locals["func"]
        code = (function.__code__ if type(function) in {
            types.FunctionType, types.MethodType} else None)
        # On coroutine resume do not wrap our own callable a second time.
        if code is observed_call_code:
            return
        label = "other"
        for name, (owner, qualname) in THREAD_OPERATIONS.items():
            if code is not None and code is code_for(owner, qualname):
                label = name
                fingerprint(label, code)
                break
        key = (module, label)
        row = operations.get(key)
        if row is None:
            if len(operations) == 8:
                facts["operationsTruncated"] = True
                return
            row = {"module": module, "operation": label, "queued": 0, "entered": 0,
                   "returned": 0, "failed": 0, "errors": []}
            operations[key] = row
            facts.setdefault("threadOperations", []).append(row)
            facts.setdefault("operationsTruncated", False)
        loop = asyncio.get_running_loop()
        frame.f_locals["func"] = make_observed(function, loop, row)
        operation_event(row, "queued")

    def make_observed(function, loop, row):
        def observed_call(*args, **kwargs):
            def signal(event, error=None):
                try:
                    loop.call_soon_threadsafe(operation_event, row, event, error)
                except BaseException:
                    pass  # A closed loop cannot change the underlying call.
            signal("entered")
            try:
                if (row["operation"] == "shared_remote"
                        and type(function) is types.FunctionType
                        and function.__code__ is code_for(*THREAD_OPERATIONS["shared_remote"])):
                    def lifecycle(value):
                        loop.call_soon_threadsafe(lifecycle_event, row, value)
                    with observe_shared_subprocess(function, lifecycle):
                        result = function(*args, **kwargs)
                else:
                    result = function(*args, **kwargs)
            except BaseException as error:
                safe = failure_facts(error, "runtime_start")["errors"][:1]
                for item in safe:
                    item["frames"] = []
                signal("failed", safe)
                raise
            else:
                signal("returned")
                return result
        return observed_call

    # Retain the exact wrapper code identity without constructing a task/thread.
    observed_call_code = make_observed(None, None, None).__code__

    def snapshot_child(task, row):
        nonlocal failed
        try:
            row.update(taskDone=task.done(), cancelled=task.cancelled())
            frames = frames_now(task, codes_now())
            if frames and not task.cancelling() and not root_task.cancelling():
                row["awaiting"] = frames[-3:]
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    row["errors"] = failure_facts(error, "runtime_start")["errors"][:1]
                    for error_row in row["errors"]:
                        error_row["frames"] = error_row["frames"][-2:]
            persist()
        except BaseException:
            failed = True
            facts["diagnosticStatus"] = "FAIL"
            try:
                persist()
            except BaseException:
                pass

    def snapshot(task):
        nonlocal failed
        try:
            facts.update(diagnosticStatus="FAIL" if failed else "PASS",
                         observed=True, taskDone=task.done())
            if facts.get("foundation", {}).get("status", "not_loaded") == "not_loaded":
                facts["foundation"] = worker_foundation()
            frames = frames_now(task, codes_now())
            # Stop cancels the real start task, whose finally also awaits
            # cleanup. Preserve the pre-cancellation wait, not that teardown.
            if frames and not task.cancelling():
                facts["awaiting"] = frames[-5:]
            if task.done() and not task.cancelled():
                # Reading the settled task does not change its return/raise.
                # Capture a startup error that escaped before runtime.error.
                error = task.exception()
                if error is not None:
                    facts["errors"] = failure_facts(error, "runtime_start")["errors"]
            persist()
        except BaseException:
            failed = True
            facts["diagnosticStatus"] = "FAIL"
            try:
                persist()
            except BaseException:
                pass  # Missing/unreadable final evidence fails in the parent.
        finally:
            try:
                if task.done() and sys.getprofile() is profile:
                    sys.setprofile(None)
            except BaseException:
                pass  # A diagnostic finalizer has no execution authority.

    def profile(frame, event, arg):
        nonlocal failed, root_task
        try:
            if (event == "call" and frame.f_code is code_for("asyncio.threads", "to_thread")):
                observe_thread_call(frame)
            elif (event == "return" and frame.f_code.co_name == "start"
                    and frame.f_code is code_for("__main__", "Worker.start")):
                # This local is inspected only on the exact trusted function.
                # Its value is not serialized or used for execution authority.
                task = frame.f_locals["self"].start_task
                root_task = task
                asyncio.get_running_loop().call_soon(snapshot, task)
            elif (event == "return" and frame.f_code.co_name == "activate"
                    and frame.f_code is code_for(
                        "amplifier_foundation.modules.activator", "ModuleActivator.activate")):
                task = asyncio.current_task()
                known = child_rows.get(id(task))
                if known is not None and known[0] is task:
                    row = known[1]
                elif task is not None and scoped_child(task, frame):
                    if len(child_rows) == 4:
                        facts["activationTruncated"] = True
                        persist()
                        return
                    name = frame.f_locals.get("module_name")
                    row = {"module": name if type(name) is str and name in ACTIVATION_MODULES else "other",
                           "taskDone": False, "cancelled": False, "awaiting": [], "errors": []}
                    child_rows[id(task)] = (task, row)
                    facts.setdefault("activationChildren", []).append(row)
                    facts.setdefault("activationTruncated", False)
                else:
                    return
                asyncio.get_running_loop().call_soon(snapshot_child, task, row)
        except BaseException:
            failed = True
            facts["diagnosticStatus"] = "FAIL"
            try:
                persist()
            except BaseException:
                pass

    if sys.getprofile() is not None:
        facts["diagnosticStatus"] = "FAIL"  # Never replace an existing profiler.
        persist()
        return
    sys.setprofile(profile)


def read_worker_startup(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(WORKER_DIAGNOSTIC_BYTES + 1)
        assert len(raw) <= WORKER_DIAGNOSTIC_BYTES
        value = json.loads(raw)
        validate_worker_startup(value)
        return value
    except Exception:
        return {"diagnosticStatus": "FAIL", "observed": False, "taskDone": False, "awaiting": [], "errors": []}


def observe_lock(project, report):
    """Hash an existing failed-start lock; existence is not graph acceptance."""
    try:
        path = project / "uv.lock"
        try:
            with path.open("rb") as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
        except FileNotFoundError:
            report["lockObservation"] = {"status": "missing", "sha256": None}
            return
        assert len(raw) <= 4 * 1024 * 1024
        tomllib.loads(raw.decode("utf-8"))
        report["lockObservation"] = {"status": "present", "sha256": digest(raw)}
    except Exception:
        report["lockObservation"] = {"status": "invalid", "sha256": None}


def parse_inner_output(raw, *, outer_failure=None):
    """An empty/malformed child result is not evidence of inner preflight."""
    try:
        report = json.loads(raw)
    except Exception as error:
        report = empty_inner_report()
        report["diagnostic"] = outer_failure or failure_facts(error, "parent_output_json")
        return report
    try:
        validate_inner_report(report)
    except Exception as error:
        report = empty_inner_report()
        report["diagnostic"] = failure_facts(error, "parent_output_schema")
    return report


STARTUP_FAILURE_CLASSES = frozenset({
    "Exception", "RuntimeError", "AssertionError", "ValueError", "TypeError", "KeyError",
    "AttributeError", "ImportError", "ModuleNotFoundError", "OSError", "PermissionError",
    "FileNotFoundError", "NotADirectoryError", "IsADirectoryError", "NotImplementedError",
    "TimeoutError", "CancelledError", "TimeoutExpired", "CalledProcessError",
    "UnicodeError", "UnicodeDecodeError", "UnicodeEncodeError", "ModuleActivationError", "other",
})


def validate_startup_failure_facts(value):
    assert type(value) is dict and set(value) == {"errors", "truncated"}
    assert type(value["truncated"]) is bool
    assert type(value["errors"]) is list and 1 <= len(value["errors"]) <= 4
    os_errors = {"OSError", "PermissionError", "FileNotFoundError", "NotADirectoryError",
                 "IsADirectoryError", "TimeoutError"}
    for row in value["errors"]:
        assert type(row) is dict and {"class"} <= set(row) <= {"class", "errno", "winerror", "returncode"}
        assert type(row["class"]) is str and row["class"] in STARTUP_FAILURE_CLASSES
        for key in ("errno", "winerror", "returncode"):
            if key in row:
                assert type(row[key]) is int and -(2**31) <= row[key] < 2**32
                assert (row["class"] == "CalledProcessError" if key == "returncode"
                        else row["class"] in os_errors)


def read_startup_failure(home, error):
    """Only the host-selected receipt, using native private handle validation.

    No stale search, path-based file read or raw receipt content is exported.
    Invalid/missing facts remain an observation of failure, never acceptance.
    """
    missing = {"status": "missing", "facts": None}
    try:
        path = getattr(error, "diagnostic_path", None)
        if path is None:
            return missing
        assert isinstance(home, Path) and isinstance(path, Path)
        assert re.fullmatch(r"startup-[a-f0-9]{32}\.log", path.name)
        parent = home / "logs" / "workers"
        # Exact lexical components, not case-folded containment or a rebindable
        # resolve() proof. The native boundary validates every actual object.
        assert path.parts[:-1] == parent.parts
        from amplifier_web.windows_files import directory
        with directory(parent, owned_root=home, create=False) as pinned:
            raw = pinned.read_private(path.name, limit=60_000)
        assert type(raw) is bytes and len(raw) <= 60_000
        header = json.loads(raw.partition(b"\n\n")[0])
        assert type(header) is dict
        if "startupFailureFacts" not in header:
            return missing
        facts = header["startupFailureFacts"]
        validate_startup_failure_facts(facts)
        return {"status": "present", "facts": facts}
    except FileNotFoundError:
        return missing
    except BaseException:
        return {"status": "invalid", "facts": None}


def validate_inner_report(report):
    """Only bounded fixed fields may leave the private test directory."""
    assert type(report) is dict
    assert set(report) - {"diagnostic", "startup", "workerStartup", "lockObservation", "startupFailureFacts"} == set(empty_inner_report())
    if "startupFailureFacts" in report:
        assert report["status"] == "FAIL"
        observed = report["startupFailureFacts"]
        assert type(observed) is dict and set(observed) == {"status", "facts"}
        assert type(observed["status"]) is str and observed["status"] in {"present", "missing", "invalid"}
        if observed["status"] == "present":
            validate_startup_failure_facts(observed["facts"])
        else:
            assert observed["facts"] is None
    if "lockObservation" in report:
        lock = report["lockObservation"]
        assert type(lock) is dict and set(lock) == {"status", "sha256"}
        assert type(lock["status"]) is str and lock["status"] in {"missing", "invalid", "present"}
        if lock["status"] == "present":
            assert type(lock["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", lock["sha256"])
        else:
            assert lock["sha256"] is None
    if "diagnostic" in report:
        validate_diagnostic(report["diagnostic"])
    if "workerStartup" in report:
        validate_worker_startup(report["workerStartup"])
    if "startup" in report:
        startup = report["startup"]
        assert type(startup) is dict and set(startup) == {
            "diagnosticStatus", "verifiedWorkerObserved", "phases",
            "projectManifestExists", "lockExists", "venvPythonExists"}
        assert type(startup["diagnosticStatus"]) is str and startup["diagnosticStatus"] in {"PENDING", "PASS", "FAIL"}
        assert type(startup["phases"]) is list and len(startup["phases"]) <= len(STARTUP_PHASES)
        assert all(type(phase) is str and phase in STARTUP_PHASES for phase in startup["phases"])
        assert len(set(startup["phases"])) == len(startup["phases"])
        assert all(type(startup[key]) is bool for key in (
            "verifiedWorkerObserved", "projectManifestExists", "lockExists", "venvPythonExists"))
    assert report["schema"] == 1 and report["status"] in {"PASS", "FAIL"}
    assert report["stage"] in {"preflight", "fixture", "preparation", "turn", "stop", "complete"}
    assert set(report["checks"]) == set(CHECKS)
    assert all(type(v) is bool for v in report["checks"].values())
    for name in ("wheelSha256", "manifestSha256", "lockSha256"):
        assert report[name] is None or re.fullmatch(r"[0-9a-f]{64}", report[name])
    assert set(report["appHashes"]) <= set(APP_FILES)
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in report["appHashes"].values())
    assert set(report["workerImports"]) <= set(IMPORTS)
    for row in report["workerImports"].values():
        assert set(row) == {"version", "commit", "sha256"}
        assert re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", row["version"])
        assert row["commit"] is None or re.fullmatch(r"[0-9a-f]{40,64}", row["commit"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
    if report["status"] == "PASS":
        # Historical instrumented FAIL records remain readable above. A new
        # cold qualification must not be granted from a worker-injected run.
        assert "workerStartup" not in report
        assert "startup" not in report or report["startup"]["diagnosticStatus"] == "PASS"
        assert "diagnostic" not in report or not report["diagnostic"]["errors"]
        assert "runtimeFailure" not in report.get("diagnostic", {})
        assert all(report["checks"].values()) and report["stage"] == "complete"
        assert all(report[name] is not None for name in ("wheelSha256", "manifestSha256", "lockSha256"))
        assert set(report["appHashes"]) == set(APP_FILES)
        assert set(report["workerImports"]) == set(IMPORTS)


def validate_report(report):
    """The public result requires parent-observed cleanup, not child testimony."""
    assert set(report) - {"diagnostic", "startup", "workerStartup", "lockObservation", "startupFailureFacts"} == set(empty_inner_report()) | {"outerJob"}
    assert report["schema"] == 2
    validate_inner_report({key: (1 if key == "schema" else report[key])
                           for key in report if key != "outerJob"})
    outer = report["outerJob"]
    assert set(outer) == {"status", "timedOut", "activeProcesses", "treeEmpty",
                          "ioFinalized", "outputDiscarded"}
    assert outer["status"] in {"confirmed", "unknown"}
    assert all(type(outer[key]) is bool for key in
               ("timedOut", "treeEmpty", "ioFinalized", "outputDiscarded"))
    assert (outer["activeProcesses"] is None or
            type(outer["activeProcesses"]) is int and 0 <= outer["activeProcesses"] <= 10**9)
    if outer["status"] == "confirmed":
        assert outer["activeProcesses"] == 0 and outer["treeEmpty"] and outer["ioFinalized"]
    if report["status"] == "PASS":
        assert outer["status"] == "confirmed" and not outer["timedOut"] and not outer["outputDiscarded"]


async def run_owned_probe(argv, *, cwd, env, scratch, timeout=1200, output_limit=64 * 1024):
    """Own the redirector AND actual host/inner jobs until bounded native cleanup.

    No subprocess.run/communicate/PID signals. stdout is a bounded single JSON
    result (overflow fails); stderr is continuously drained with a bounded tail.
    WindowsOwnedProcess owns its finite launch and close deadlines. Unknown
    cleanup keeps its lifeline/scratch identity, not a claim that effects ended.
    """
    from amplifier_web.windows_jobs import CleanupError, WindowsOwnedProcess
    owner = result = group = None
    tasks = []
    stdout, stderr = bytearray(), bytearray()
    timed_out = succeeded = cleanup_confirmed = False
    failure = None

    async def read_stdout():
        while chunk := await owner.stdout.read(4096):
            room = output_limit - len(stdout)
            stdout.extend(chunk[:room])
            if len(chunk) > room:
                raise ValueError("outer probe result exceeded its bound")

    async def read_stderr():
        while chunk := await owner.stderr.read(4096):
            stderr.extend(chunk)
            del stderr[:-output_limit]

    try:
        try:
            owner = await WindowsOwnedProcess.start(
                argv, cwd=cwd, env=env, trusted_python=sys._base_executable,
                cleanup_timeout=5)
        except CleanupError as error:
            owner = error.owner  # start may fail while retaining native handles
            raise
        owner.stdin.close()
        tasks = [asyncio.create_task(owner.wait()), asyncio.create_task(read_stdout()),
                 asyncio.create_task(read_stderr())]
        group = asyncio.gather(*tasks)
        async with asyncio.timeout(timeout):
            result, _, _ = await asyncio.shield(group)
        succeeded = result.payload_returncode == 0 and not result.output_discarded
    except TimeoutError as error:
        timed_out = True
        failure = failure_facts(error, "outer_run")
    except Exception as error:
        failure = failure_facts(error, "outer_run")
    finally:
        if owner is not None:
            try:
                # CleanupError from start already records a settled, failed
                # finalizer. Do not retry that native operation.
                if owner.state != "cleanup_failed":
                    result = await owner.close()
                    cleanup_confirmed = (result.active_processes == 0
                                         and owner.tree_empty and owner.io_finalized)
            except Exception as error:
                cleanup_confirmed = False
                failure = failure_facts(error, "outer_close")
            finally:
                if not cleanup_confirmed:
                    FAILED_OUTER_OWNERS.append({"owner": owner, "scratch": scratch})
        # No second unbounded EOF/communicate after deadline. Pending reads and
        # observers are settled only AFTER the retained native close finishes.
        for task in tasks:
            if not task.done():
                task.cancel()
        if group is not None:
            await asyncio.gather(group, *tasks, return_exceptions=True)
    outer = {"status": "confirmed" if cleanup_confirmed else "unknown",
             "timedOut": timed_out,
             "activeProcesses": result.active_processes if result is not None else None,
             "treeEmpty": bool(owner is not None and owner.tree_empty),
             "ioFinalized": bool(owner is not None and owner.io_finalized),
             "outputDiscarded": bool(owner is not None and owner.output_discarded)}
    return {"stdout": bytes(stdout), "stderr": bytes(stderr), "outerJob": outer,
            "succeeded": succeeded and cleanup_confirmed and not timed_out and not outer["outputDiscarded"],
            "tasksSettled": all(task.done() for task in tasks), "failure": failure}


def installed_host(wheel, report=None):
    """Bind used app/manifest bytes to the installed candidate wheel, not checkout."""
    if report is not None:
        checkpoint(report, "installed_import")
    import amplifier_web
    app = Path(amplifier_web.__file__).resolve().parent
    if report is not None:
        checkpoint(report, "installed_distribution")
    dist = metadata.distribution("amplifier-unified")
    if report is not None:
        checkpoint(report, "installed_direct_url")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    assert direct.get("url") == wheel.resolve().as_uri()
    assert not direct.get("dir_info", {}).get("editable")
    if report is not None:
        checkpoint(report, "installed_origin")
    assert app.is_relative_to(Path(sys.prefix).resolve())
    hashes = {}
    if report is not None:
        checkpoint(report, "installed_members")
    with zipfile.ZipFile(wheel) as archive:
        for name in (*APP_FILES, "runtime_deps/pyproject.toml", "runtime_deps/compatibility.txt",
                     "runtime_deps/features.toml"):
            raw = archive.read("amplifier_web/" + name)
            assert (app / name).read_bytes() == raw
            assert Path(dist.locate_file("amplifier_web/" + name)).resolve() == app / name
            if name in APP_FILES:
                hashes[name] = digest(raw)
    return app, hashes


def verify_worker_lock(project, imports):
    """Bind every required observed import to the actual uv-installed resolution."""
    lock = tomllib.loads((project / "uv.lock").read_text(encoding="utf-8"))
    for package, distribution in IMPORTS.items():
        rows = [row for row in lock["package"] if row["name"] == distribution]
        assert len(rows) == 1 and rows[0]["version"] == imports[package]["version"]
        source = rows[0]["source"]
        if "git" in source:
            assert imports[package]["commit"] == source["git"].rsplit("#", 1)[1]
        else:
            assert "registry" in source and imports[package]["commit"] is None


def prepare_fixture_home(root):
    """Create and bind isolated homes BEFORE imports can evaluate Path.home()."""
    from amplifier_web.windows_files import directory
    home, shared, workspace = root / "app", root / "shared", root / "workspace"
    user = root / "user"
    for path in (home, shared, workspace, user, user / "roaming", user / "local",
                 user / "cache", root / "ownership", root / "captures"):
        with directory(path, owned_root=root, create=True):
            pass
    # Do not inherit a real profile to fix missing-home imports. Each consumer
    # sees only privately created scratch; the worker project stays absent.
    os.environ.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(shared),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "captures"),
        USERPROFILE=str(user), HOME=str(user),
        APPDATA=str(user / "roaming"), LOCALAPPDATA=str(user / "local"),
        XDG_CONFIG_HOME=str(user), XDG_CACHE_HOME=str(user / "cache"))


async def observe_default_start(runtime, session, emit, report, project):
    """Observe the unchanged default start, never replace its result or failure.

    A verified owner is recorded only after production's identity check has
    assigned its worker PID. The first bundle-preparation phase is the existing
    worker-start bootstrap marker on that same protocol, not a new entrypoint.
    File existence is merely a preparation breadcrumb, not installed provenance.
    """
    facts = {"diagnosticStatus": "PENDING", "verifiedWorkerObserved": False, "phases": [],
             "projectManifestExists": False, "lockExists": False, "venvPythonExists": False}
    report["startup"] = facts
    failed = False

    def observe_row():
        owner = runtime.workers.get(session["id"], {}).get("windows_owner")
        if owner is not None and type(owner.process.pid) is int and owner.process.pid > 0:
            facts["verifiedWorkerObserved"] = True

    async def observed_emit(kind, data):
        nonlocal failed
        try:
            observe_row()
            if kind == "runtime.status":
                phase = data.get("phase")
                if type(phase) is str and phase in STARTUP_PHASES and phase not in facts["phases"]:
                    facts["phases"].append(phase)
        except BaseException:
            failed = True
        # Observation cannot suppress a callback, its return, error or cancel.
        return await emit(kind, data)

    try:
        return await runtime.start(session, observed_emit)
    except BaseException as error:
        # Read only the receipt selected by the original raised host error.
        # Any observation fault must preserve that same failure/cancellation.
        try:
            report["startupFailureFacts"] = read_startup_failure(runtime.home, error)
        except BaseException:
            report["startupFailureFacts"] = {"status": "invalid", "facts": None}
        raise
    finally:
        try:
            observe_row()
            facts["projectManifestExists"] = (project / "pyproject.toml").is_file()
            facts["lockExists"] = (project / "uv.lock").is_file()
            facts["venvPythonExists"] = (project / ".venv/Scripts/python.exe").is_file()
        except BaseException:
            failed = True
        try:
            observe_lock(project, report)
        except BaseException:
            failed = True
            report["lockObservation"] = {"status": "invalid", "sha256": None}
        facts["diagnosticStatus"] = "FAIL" if failed else "PASS"


async def journey(root, wheel, report):
    checkpoint(report, "fixture_home")
    prepare_fixture_home(root)
    checkpoint(report, "journey_imports")
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.deployment import write_private
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_environment import manifest_path, project_path
    from amplifier_web.runtime_profiles import ensure
    from amplifier_web.service import AppService
    from amplifier_web.session_files import sessions_dir
    from amplifier_web.windows_files import directory
    from amplifier_foundation.session.history import SessionHistoryStore

    app, hashes = installed_host(wheel, report)
    checkpoint(report, "installed_manifest")
    report.update(wheelSha256=digest(wheel.read_bytes()), appHashes=hashes,
                  manifestSha256=digest(manifest_path().read_bytes()))
    report["checks"]["installedHost"] = True
    checkpoint(report, "journey")
    report["stage"] = "fixture"
    home, shared, workspace = root / "app", root / "shared", root / "workspace"
    settings = {"bundle": {"app": []}, "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)}}
    write_private(shared / "settings.yaml", json.dumps(settings), owned_root=shared)
    provider = root / "provider/amplifier_module_provider_default_fixture"
    with directory(provider, owned_root=root, create=True):
        pass
    write_private(provider / "__init__.py", PROVIDER, owned_root=root)
    project = project_path(home)
    assert not project.exists()  # No pre-resolved project or installed worker graph.
    bundle = root / "bundle.yaml"
    write_private(bundle, json.dumps({
        "bundle": {"name": "default-command-fixture"},
        "session": {"orchestrator": {"module": "loop-live", "config": {
            "use_streaming": False, "min_delay_between_calls_ms": 0}},
            "context": {"module": "context-simple",
                "source": "git+https://github.com/microsoft/amplifier-module-context-simple@main"}},
        "providers": [{"module": "provider-default-fixture", "source": str(provider.parent),
            "config": {"project": str(project), "app": str(app), "appHashes": hashes,
                       "imports": IMPORTS, "proof": str(root / "worker-proof.json")}}],
    }), owned_root=root)
    runtime = RuntimeManager(startup_timeout=900, retention={"prewarm_on_select": False})
    assert runtime.command is None
    runtime.home = home
    service = AppService(home, runtime=runtime, workspace=workspace)
    runtime.app_bridge = service.app_bridge
    owners = []
    try:
        await service.dispatch("session.create", {"bundle": str(bundle)})
        session = service._session()
        sid = session["id"]
        # Exercise the actual legacy no-generation ensure branch, not a stub.
        assert await ensure(home, None, session) is None
        report["stage"] = "preparation"
        checkpoint(report, "runtime_start")
        # Only the host observes the normal start. Do not create an import hook,
        # alter worker PYTHONPATH, install a profiler or wrap worker callables.
        await observe_default_start(runtime, session, service.on_runtime_event, report, project)
        assert report["startup"]["diagnosticStatus"] == "PASS"
        checkpoint(report, "journey")
        row = runtime.workers[sid]
        owner = row["windows_owner"]
        owners.append(owner)
        assert row["ready"].done() and row["ready"].exception() is None
        assert row["generation"] is None and row["source_generation"] is None
        # Calling the unchanged builder again must identify the very same
        # project/manifest/worker. It is not installed as a replacement command.
        command = runtime._command()
        assert command[1] == "run" and command[-1] == str(app / "runtime_worker.py")
        assert Path(RuntimeManager.project_path(command)) == project
        assert command[-3:-1] == ["3.13", "python"]
        assert (project / "pyproject.toml").read_bytes() == manifest_path().read_bytes()
        assert (project / ".venv/Scripts/python.exe").is_file()
        assert (project / "uv.lock").is_file()
        report["lockSha256"] = digest((project / "uv.lock").read_bytes())
        report["checks"]["coldDefaultCommand"] = True
        assert row["native_worker_pid"] == row["process"].pid
        assert row["process"].pid != owner.job.identity.guard_pid
        owner.job.verify_member(row["process"].pid, owner.created)
        report["checks"]["workerIdentity"] = True
        report["stage"] = "turn"
        checkpoint(report, "input_admission")
        await service.dispatch("conversation.send", {"sessionId": sid, "text": "DEFAULT_COMMAND_TURN"},
                               command_id="default-command-input")
        checkpoint(report, "journey")
        history_store = SessionHistoryStore(sessions_dir(workspace) / sid)
        async with asyncio.timeout(90):
            while True:
                assert service._session(sid).get("status") != "error"
                # The first idle/parked notification may precede delivery of
                # this input. Require this turn's actual saved response too.
                if row.get("parked"):
                    history = history_store.load_messages()
                    if any(m["role"] == "assistant" and "Default command caf\u00e9 verified" in str(m.get("content"))
                           for m in history):
                        break
                await asyncio.sleep(.05)
        report["checks"]["scriptedTurn"] = True
        proof = json.loads((root / "worker-proof.json").read_text(encoding="utf-8"))
        assert set(proof) == {"pid", "imports", "appHashes"} and proof["pid"] == row["process"].pid
        assert proof["appHashes"] == hashes and set(proof["imports"]) == set(IMPORTS)
        verify_worker_lock(project, proof["imports"])
        report["workerImports"] = proof["imports"]
        report["checks"]["workerProvenance"] = True
        report["stage"] = "stop"
        await runtime.stop(sid)
        assert owner.result is not None and owner.result.active_processes == 0
        assert owner.job.tree_empty and owner.job.io_finalized
        assert sid not in runtime.workers
        assert digest((project / "uv.lock").read_bytes()) == report["lockSha256"]
        report["checks"]["ownedJobCleanup"] = True
    finally:
        for row in runtime.workers.values():
            if row.get("windows_owner") and row["windows_owner"] not in owners:
                owners.append(row["windows_owner"])
        await service.close()
        # close() gathers failures; only the retained owners' proof counts.
        assert not runtime.workers and not runtime._preparations
        assert all(o.result is not None and o.result.active_processes == 0
                   and o.job.tree_empty and o.job.io_finalized for o in owners)
    report.update(status="PASS", stage="complete")
    checkpoint(report, "complete")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    report = empty_inner_report()
    checkpoint(report, "runtime_preflight")
    try:
        assert sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
        assert sys.version_info[:2] == (3, 13) and struct.calcsize("P") == 8
        checkpoint(report, "input_paths")
        assert not args.root.exists() and args.wheel.is_file()
        checkpoint(report, "private_root")
        from amplifier_web.windows_files import directory
        with directory(args.root, owned_root=args.root, create=True):
            pass
        checkpoint(report, "private_log")
        with (args.root / "private-probe.log").open("x", encoding="utf-8") as logs:
            with redirect_stdout(logs), redirect_stderr(logs):
                asyncio.run(journey(args.root, args.wheel, report))
    except Exception as error:
        report["status"] = "FAIL"  # Never publish exception text/paths/private logs.
        report["diagnostic"] = failure_facts(error, report["diagnostic"]["checkpoint"])
    try:
        validate_inner_report(report)
    except Exception as error:
        # Do not emit even a partially valid arbitrary worker-supplied record.
        report = empty_inner_report()
        report["diagnostic"] = failure_facts(error, "inner_report_schema")
        validate_inner_report(report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
