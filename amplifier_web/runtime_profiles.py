"""Prepare a custom configuration on explicit execution, never while browsing.

Offered profiles share the qualified app environment. A workspace override gets
its own immutable candidate; it must never pip-install into that shared runtime.
Identical effective configurations share the same prepared environment afterward.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace


def configuration_key(settings, *roots):
    # Provider IDs, keys, models and prompts are runtime values. They must not
    # create a new Python graph for every conversation or credential rotation.
    from .builtin_behaviors import app_behaviors

    def declarations(value):
        if isinstance(value, dict):
            return {
                key: declarations(item)
                for key, item in value.items()
                if key not in {"config", "instruction", "context", "id", "instance_id"}
                or key == "context"
                and isinstance(item, dict)
                and "module" in item
            }
        if isinstance(value, list):
            return [declarations(item) for item in value]
        return value

    # Match Config.registrations precedence. Re-declaring a built-in bundle,
    # or an added alias already shadowed by sources.bundles, changes no source.
    # Retain every other alias: lazy agents can resolve it after startup.
    from .host.config import FOUNDATION_SOURCE, PRECONFIGURED_BUNDLES
    defaults = {"foundation": FOUNDATION_SOURCE, **PRECONFIGURED_BUNDLES}
    bundle_sources = settings.get("sources", {}).get("bundles", {})
    added = {
        name: source for name, source in settings.get("bundle", {}).get("added", {}).items()
        if name not in bundle_sources and (name not in defaults or source != defaults[name])
    }
    # Removing the last source through Settings leaves an empty category.
    # Empty module/bundle maps declare no code and must reuse the same graph.
    sources = {key: rows for key, rows in settings.get("sources", {}).items()
               if not (key in {"modules", "bundles"} and rows == {})}
    value = {
        "sources": sources,
        "bundles": added,
        "behaviors": app_behaviors(settings),
        "routing": bool(settings.get("routing")),
        "modules": declarations(settings.get("config", {})),
        "overrides": {
            key: row.get("source")
            for key, row in settings.get("overrides", {}).items()
            if isinstance(row, dict) and row.get("source")
        },
    }
    text = json.dumps(value, sort_keys=True, default=str)
    for root in sorted(
        {str(Path(root).resolve()) for root in roots}, key=len, reverse=True
    ):
        text = text.replace(root, "$APP")
    return hashlib.sha256(text.encode()).hexdigest()


def module_source_references(plan):
    """Follow Foundation's mount-plan declarations, never module configuration.

    A hook's include_paths (or even config.source) is runtime data. Only the
    source field of an actual module declaration identifies installable code.
    Agent definitions use the same declaration sections as the root plan.
    """
    if not isinstance(plan, dict):
        return
    session = plan.get("session", {})
    if isinstance(session, dict):
        for section in ("orchestrator", "context"):
            row = session.get(section)
            if isinstance(row, dict) and isinstance(row.get("source"), str):
                yield row["source"]
    for section in ("providers", "tools", "hooks"):
        for row in plan.get(section, []) or []:
            if isinstance(row, dict) and isinstance(row.get("source"), str):
                yield row["source"]
    # Lazy spawn declarations and explicit bundle includes are build inputs too.
    # Never descend into a module's config or the top-level context-file map.
    spawn = plan.get("spawn") or {}
    if isinstance(spawn, dict):
        for row in spawn.get("tools", []) or []:
            if isinstance(row, dict) and isinstance(row.get("source"), str):
                yield row["source"]
    for include in plan.get("includes", []) or []:
        yield include.get("bundle") if isinstance(include, dict) else include
    agents = plan.get("agents", {})
    if isinstance(agents, dict):
        for definition in agents.values():
            yield from module_source_references(definition)


def _local_build_inputs(config, edited, local, bundle):
    from .builtin_behaviors import app_behaviors

    # Settings source maps and module rows have explicit source semantics.
    # Walking arbitrary values (even after dropping "config") would still
    # mistake project_dir, base_path, source_base_paths and resources for code.
    declarations = [
        bundle,
        *config.module_sources.values(),
        *config.bundle_sources.values(),
        *config.settings.get("bundle", {}).get("added", {}).values(),
        *app_behaviors(config.settings),
        *module_source_references(config.settings.get("config", {})),
        *module_source_references(edited),
    ]
    inputs = {local.resolve()} if local else set()
    for value in declarations:
        if not isinstance(value, str) or not value.startswith(
            ("/", "./", "../", "~", "file://")
        ):
            continue
        # Preserve upstream URI decoding and fragment handling. The fragment
        # selects content within the declared source, not a literal pathname.
        from urllib.parse import unquote, urlsplit
        path = Path(unquote(urlsplit(value).path) if value.startswith("file://")
                    else value.split("#", 1)[0]).expanduser()
        if not path.is_absolute():
            path = config.workspace / path
        # Generation-owned sources are already immutable and identified by
        # the parent generation. Hash only external user-owned inputs here.
        if path.exists() and not path.resolve().is_relative_to(config.home.resolve()):
            inputs.add(path.resolve())
    return inputs


def _write_attempt(stage, attempt):
    from .host.config import write_private

    write_private(stage / "profile-attempt.json", json.dumps(attempt))


async def _run_phase(stage, attempt, phase, function, *args, **kwargs):
    """Keep bounded timing facts, never commands, environment or output bodies."""
    started = time.monotonic()
    row = {"phase": phase, "status": "running", "startedAt": time.time()}
    attempt.setdefault("timings", []).append(row)
    attempt["phase"] = phase
    _write_attempt(stage, attempt)
    try:
        result = await function(*args, **kwargs)
    except BaseException as error:
        row.update(status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                   elapsedMs=round((time.monotonic() - started) * 1000, 3),
                   errorType=type(error).__name__)
        try:
            _write_attempt(stage, attempt)
        except Exception as recording_error:
            error.add_note(f"Preparation timing write failed ({type(recording_error).__name__}).")
        raise
    row.update(status="succeeded", elapsedMs=round((time.monotonic() - started) * 1000, 3))
    _write_attempt(stage, attempt)
    return result


@asynccontextmanager
async def _profile_attempt(home, generation, selected, stage, index):
    """Retain diagnostics; cancelled to_thread writers may still be running."""
    started = time.monotonic()
    attempt = {
        "generation": selected,
        "parentGeneration": generation,
        "status": "preparing",
        "errorType": None,
        "startedAt": time.time(),
        "paths": {
            "receipt": str(stage),
            "foundation": str(stage / "foundation"),
            "sharedConfig": str(stage / "shared-config"),
            "preparationProject": str(Path(home) / "runtime" / ("prepare-" + selected)),
            "qualifiedProject": None,
            "profileIndex": str(index),
        },
        "cleanup": {
            "owner": "runtime environment owner",
            "responsibility": (
                "Retain failed/cancelled artifacts. Remove only disposable owned "
                "paths after all preparation threads and processes have stopped; "
                "qualified projects may be shared. Never replay this attempt."
            ),
        },
    }
    try:
        _write_attempt(stage, attempt)
        yield attempt
        attempt.update(status="succeeded", finishedAt=time.time(),
                       elapsedMs=round((time.monotonic() - started) * 1000, 3))
        _write_attempt(stage, attempt)
    except BaseException as error:
        attempt.update(
            status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
            errorType=type(error).__name__,
            finishedAt=time.time(),
            elapsedMs=round((time.monotonic() - started) * 1000, 3),
        )
        try:
            _write_attempt(stage, attempt)
        except Exception as recording_error:
            error.add_note(
                f"Profile attempt status write failed ({type(recording_error).__name__}); "
                f"artifacts retained at {stage}. Runtime environment owner must "
                "wait for preparation threads/processes before cleanup."
            )
        # Only these admission markers belong to this coroutine. Do not remove
        # a directory: a cancelled copy/freeze/verification thread can outlive it.
        try:
            for marker in ("validated.json", "profiles-qualified.json"):
                (stage / marker).unlink(missing_ok=True)
            if index.exists() and json.loads(index.read_text()).get("generation") == selected:
                index.unlink()
        except Exception as cleanup_error:
            error.add_note(
                f"Profile admission-marker cleanup failed ({type(cleanup_error).__name__}); "
                f"inspect retained attempt at {stage} before admitting it."
            )
        raise


async def ensure(home, generation, session, *, progress=None):
    started = time.monotonic()
    from .host.config import read_config, write_private
    from .runtime_environment import project_path, receipt_directory
    from .updates import process

    if not generation:
        return generation  # Legacy first-start setup keeps its existing contract.
    receipt = receipt_directory(home, generation)
    descriptor = receipt / "profiles-qualified.json"
    if not descriptor.exists():
        return generation  # A pre-migration generation has not opted into immutability.
    async def report(detail):
        if progress is not None:
            await progress(detail)

    await report("Checking this chat’s setup…")
    config = read_config(
        session["workspace"],
        home=home,
        session_id=session.get("runtimeSessionId") or session["id"],
        registry_home=receipt / "foundation",
    )
    from .host.bundle_paths import canonical_bundle_reference
    bundle = canonical_bundle_reference(config, session.get("bundle") or config.active_bundle)
    key = configuration_key(config.settings, home, receipt)
    # Explicit local bundles can shadow a named offering. Their content is not
    # represented by the app's Git generation and must be qualified separately.
    from .host.bundle_paths import local_bundle_path

    local = local_bundle_path(config, bundle)
    from .session_files import validate_id

    runtime_id = (
        session.get("runtimeSessionId")
        or session.get("nativeIdentity")
        or session["id"]
    )
    validate_id(runtime_id)
    edited_path = Path(home) / "sessions" / runtime_id / "configuration.json"
    # A root replacement intentionally clears the previous root's module plan.
    # Qualify the candidate graph, not stale overrides from the old root.
    edited = json.loads(edited_path.read_text()) if edited_path.exists() and not session.get('bundleReplacement') else None
    # Local overrides are mutable user inputs. Content, not merely the path,
    # participates in qualification so edits cannot reuse an obsolete wheel.
    local_inputs = _local_build_inputs(config, edited, local, bundle)
    if local_inputs:
        from amplifier_foundation.modules.preparation import source_signature

        contents = []
        for path in sorted(local_inputs):
            content = await asyncio.to_thread(
                source_signature, path if path.is_dir() else path.parent
            )
            contents.append([str(path), content])
        key = hashlib.sha256((key + json.dumps(contents)).encode()).hexdigest()
    if edited is not None:
        key = hashlib.sha256(
            (key + json.dumps(edited, sort_keys=True)).encode()
        ).hexdigest()
    offered = json.loads(descriptor.read_text())
    if (
        not local_inputs
        and edited is None
        and bundle in offered["profiles"]
        and key == offered["configuration"]
    ):
        return generation
    if os.name == "nt":
        # The baseline/previously offered graph keeps its existing route. Custom
        # qualification also calls freeze/update subprocess owners and copies
        # credential snapshots; those are not native-qualified yet. Refuse
        # BEFORE directories, locks, private snapshots or subprocess effects.
        # Do not fall back to the shared graph for changed declarations.
        raise RuntimeError(
            "Custom runtime preparation is unavailable on Windows. "
            "This chat's changed dependency declarations were not installed; "
            "use the existing offered runtime or qualify the custom graph separately."
        )
    # The profile key contains no prompts, histories or credentials in plaintext.
    identity = hashlib.sha256(
        json.dumps([generation, bundle, key]).encode()
    ).hexdigest()
    index = Path(home) / "updates/profiles" / (identity + ".json")
    from filelock import AsyncFileLock

    index.parent.mkdir(parents=True, exist_ok=True)
    await report("Waiting for this chat’s setup to finish…")
    waiting = time.monotonic()
    async with AsyncFileLock(str(index) + ".lock"), AsyncExitStack() as attempts:
        lock_wait_ms = round((time.monotonic() - waiting) * 1000, 3)
        if index.exists():
            selected = json.loads(index.read_text())["generation"]
            target = receipt_directory(home, selected)
            status_file = target / "profile-attempt.json"
            completed = (
                not status_file.exists()  # Previously qualified profiles have no stamp.
                or json.loads(status_file.read_text()).get("status") == "succeeded"
            )
            if completed and (target / "validated.json").exists() and project_path(
                home, selected
            ).exists():
                return selected
        selected = uuid.uuid4().hex
        stage = receipt_directory(home, selected)
        stage.mkdir(parents=True, mode=0o700)
        attempt = await attempts.enter_async_context(
            _profile_attempt(home, generation, selected, stage, index)
        )
        attempt.update(preflightMs=round((waiting - started) * 1000, 3), lockWaitMs=lock_wait_ms)
        from .update_storage import copy_snapshot

        await report("Preparing this chat’s tools…")
        await _run_phase(
            stage, attempt, "source-snapshot", asyncio.to_thread,
            copy_snapshot, receipt / "foundation", stage / "foundation"
        )
        registry = stage / "foundation/registry.json"
        if registry.exists():
            write_private(
                registry,
                registry.read_text().replace(
                    str(receipt / "foundation"), str(stage / "foundation")
                ),
            )
        shared = stage / "shared-config"
        shared.mkdir(mode=0o700)
        import yaml

        def relocate(value):
            if isinstance(value, dict):
                return {k: relocate(v) for k, v in value.items()}
            if isinstance(value, list):
                return [relocate(v) for v in value]
            if isinstance(value, str):
                return value.replace(
                    str(receipt / "foundation"), str(stage / "foundation")
                )
            return value

        write_private(
            shared / "settings.yaml", yaml.safe_dump(relocate(config.settings))
        )
        # Only setup uses this private snapshot. Real worker credentials and
        # conversations continue through their ordinary shared authorities.
        keys = config.config_home / "keys.env"
        if keys.exists():
            shutil.copy2(keys, shared / "keys.env")
        for routing in ("routing",):
            source = config.config_home / routing
            if source.is_dir():
                shutil.copytree(source, shared / routing)
        # Preserve relative source semantics while all probe history/output and
        # settings remain in the candidate's private home and shared root.
        workspace = config.workspace
        profile = str(local) if local else bundle
        profiles = stage / "profiles.json"
        write_private(profiles, json.dumps([profile]))
        plan_flags = []
        if edited is not None:
            plan_file = stage / "runtime-plan.json"
            write_private(plan_file, json.dumps(edited))
            plan_flags = ["--runtime-plan", str(plan_file)]
        project = Path(home) / "runtime" / ("prepare-" + selected)
        project.mkdir(parents=True)
        current = project_path(home, generation)
        shutil.copy2(current / "pyproject.toml", project / "pyproject.toml")
        shutil.copy2(current / "uv.lock", project / "uv.lock")
        from .runtime_qualification import freeze, prepare_overrides, verify_recorded
        from .update_sources import store_environment

        uv = shutil.which("uv")
        await _run_phase(stage, attempt, "runtime-prepare", process,
            uv,
            "sync",
            "--locked",
            "--project",
            str(project),
            "--python",
            "3.13",
            timeout=900,
        )
        overrides = await _run_phase(stage, attempt, "installer-policy", prepare_overrides,
            project, stage / "runtime-install-overrides.txt"
        )
        env = {
            **os.environ,
            **store_environment(home),
            "AMPLIFIER_WEB_HOME": str(stage),
            "AMPLIFIER_HOME": str(shared),
            "AMPLIFIER_UNIFIED_RELEASE": "",
            "AMPLIFIER_INSTALL_PREPARATION": uuid.uuid4().hex,
        }
        command = [
            uv,
            "run",
            "--locked",
            "--no-sync",
            "--project",
            str(project),
            "--python",
            "3.13",
            "python",
            str(Path(__file__).with_name("update_probe.py")),
        ]
        await report("Checking this chat’s tools…")
        await _run_phase(stage, attempt, "dependency-prepare", process,
            *command,
            str(workspace),
            profile,
            "--profiles",
            str(profiles),
            "--install-overrides",
            str(overrides),
            *plan_flags,
            env=env,
            timeout=900,
        )

        class Diagnostics:
            async def run(self, phase, function, *args, **kwargs):
                attempt["phase"] = phase
                if "--project" in args:
                    attempt["paths"]["qualifiedProject"] = str(
                        args[args.index("--project") + 1]
                    )
                _write_attempt(stage, attempt)
                return await _run_phase(stage, attempt, phase, function, *args, **kwargs)

            def record(self, *args, **kwargs):
                pass

        manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
        await report("Saving this chat’s prepared setup…")
        final = await _run_phase(stage, attempt, "runtime-freeze", freeze, manager, selected, project)
        attempt["paths"]["qualifiedProject"] = str(final)
        _write_attempt(stage, attempt)
        overrides = stage / "runtime-install-overrides.txt"
        command[command.index("--project") + 1] = str(final)
        await report("Verifying this chat’s setup…")
        await _run_phase(stage, attempt, "compatibility-probe", process,
            *command,
            str(workspace),
            profile,
            "--global-only",
            "--read-only",
            "--install-overrides",
            str(overrides),
            *plan_flags,
            env=env,
            timeout=900,
        )
        await _run_phase(stage, attempt, "recorded-verification", asyncio.to_thread,
                         verify_recorded, final, stage)
        write_private(
            stage / "validated.json",
            json.dumps(
                {
                    "generationSchema": 1,
                    "workerProtocol": 1,
                    "parentGeneration": generation,
                    "hostVersion": __import__("amplifier_web").__version__,
                    "sources": 1,
                    "createdAt": time.time(),
                }
            ),
        )
        write_private(
            stage / "profiles-qualified.json",
            json.dumps({"profiles": [bundle], "configuration": key}),
        )
        write_private(
            index, json.dumps({"generation": selected, "parentGeneration": generation})
        )
        return selected
