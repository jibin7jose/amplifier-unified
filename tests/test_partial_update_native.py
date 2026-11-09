"""Opt-in, offline UpdateManager qualification of A-old/B-new/C-old.

Run ONLY in the existing isolated prepared Linux guest, with networking disabled:

    AMPLIFIER_PARTIAL_UPDATE_NATIVE=1 /opt/unified-prepared/venv/bin/python \
        -m pytest -q -s tests/test_partial_update_native.py

Inputs: this checkout, prepared Python 3.13, bootstrap uv, Git, and the prepared
Core/Foundation/host-module distributions. No external Git bundles are needed.
Artifacts go to AMPLIFIER_PARTIAL_UPDATE_ARTIFACTS (default
/artifacts/partial-update-native), in a new per-run directory.

This is synthetic integration evidence, not public-package provenance, real
provider execution, active-turn adoption, or native Windows qualification.
Registry wheels are repacked installed payloads using the existing native update
fixture. Core and Foundation keep prepared native code with fixture Git/package
metadata. A/B/C and the no-model provider are deliberately tiny local repositories.
The application release check and source-store feature detection are explicit
fixture seams. Shared-store acquisition is NOT covered: this first slice exercises
the owned-Git-cache fetch boundary, where a real child exits unsuccessfully.

Inventory, Collector, checked-batch guard, dependency installation, freeze,
read-only native profile mounts, active_evidence, and generation promotion are
real. No qualification receipt or active pointer is manufactured by this test.
"""

import asyncio
import copy
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import uuid

import pytest
import yaml

from amplifier_web import (
    app_updates,
    builtin_behaviors,
    runtime_environment,
    update_health,
    update_partial,
    update_sources,
    updates,
)
from amplifier_web.host.config import PRECONFIGURED_BUNDLES
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_qualification import installed_graph, verify_recorded
from amplifier_web.runtime_source_alignment import verify_checked_selection
from amplifier_web.service import AppService
from amplifier_web.session_files import sessions_dir

from test_effective_behavior_update_native import (
    fixture_core,
    fixture_module,
    fixture_provider,
    git,
    repack_registry_wheels,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("AMPLIFIER_PARTIAL_UPDATE_NATIVE") != "1",
        reason="Opt-in synthetic native integration; requires the prepared offline guest",
    ),
]

URLS = {name: f"https://partial-fixture.invalid/{name}" for name in "abc"}
REFS = {"a": "main", "b": "stable", "c": "main"}
PACKAGES = {name: f"amplifier-module-tool-partial-{name}" for name in "abc"}
CORE_URL = "https://partial-fixture.invalid/amplifier-core"
FOUNDATION_URL = "https://partial-fixture.invalid/amplifier-foundation"
PROVIDER_URL = "https://partial-fixture.invalid/update-provider"
MOUNT_PREFIX = "PARTIAL_FIXTURE_MOUNT:"


def _json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def _tree(path):
    """Exact file-byte/mode/link witness; never follow links out of the fixture."""
    result = {}
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            entry = Path(directory) / name
            relative = entry.relative_to(path).as_posix()
            mode = stat.S_IMODE(entry.lstat().st_mode)
            if entry.is_symlink():
                result[relative] = ["link", mode, os.readlink(entry)]
            elif entry.is_file():
                with entry.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                result[relative] = ["file", mode, digest]
            else:
                result[relative] = ["directory", mode]
    return result


def _journal(manager):
    """Read this fixture's bounded, sanitized update journal, including rotation."""
    path = manager.diagnostics.path
    return [
        json.loads(line)
        for source in (path.with_suffix(".previous.jsonl"), path) if source.exists()
        for line in source.read_text().splitlines()
    ]


def _init_repo(root, ref="main"):
    git(root, "init", "-b", ref)
    git(root, "config", "user.name", "Partial update fixture")
    git(root, "config", "user.email", "partial-update@example.invalid")


def _commit(root, message):
    git(root, "add", ".")
    git(root, "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


def _foundation(root):
    """Repackage the installed Foundation library, not an unavailable Git bundle."""
    import amplifier_foundation

    source = Path(amplifier_foundation.__file__).resolve().parent
    assert (source / "modules/batch.py").is_file(), source
    root.mkdir()
    shutil.copytree(
        source,
        root / "amplifier_foundation",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    dist = metadata.distribution("amplifier-foundation")
    (root / "pyproject.toml").write_text(
        "[project]\nname=\"amplifier-foundation\"\nversion="
        + json.dumps(dist.version)
        + "\ndependencies="
        + json.dumps(list(dist.requires or []))
        + "\n[build-system]\nrequires=[\"hatchling\"]\nbuild-backend=\"hatchling.build\""
        + "\n[tool.hatch.metadata]\nallow-direct-references=true"
        + "\n[tool.hatch.build.targets.wheel]\npackages=[\"amplifier_foundation\"]\n"
    )
    _init_repo(root)
    revision = _commit(root, "Prepared Foundation library; synthetic packaging")
    return {
        "installedSource": str(source),
        "version": dist.version,
        "fixtureRevision": revision,
        "copiedPayload": _tree(root / "amplifier_foundation"),
    }


def _component(root, name, version, forbidden, *, optional=False):
    root.mkdir(exist_ok=True)
    package = "amplifier_module_tool_partial_" + name
    dependencies = ["amplifier-core"]
    minimum_a = 2 if version.startswith("2.") else 1
    if name == "c" and not optional:
        dependencies.append(f"{PACKAGES['a']}>={minimum_a},<{minimum_a + 1}")
    if name == "b" and optional:
        dependencies.append(f"{PACKAGES['c']}[feature]")
    (root / "pyproject.toml").write_text(
        "[project]\nname=" + json.dumps(PACKAGES[name])
        + "\nversion=" + json.dumps(version)
        + "\ndependencies=" + json.dumps(dependencies)
        + ("\n[project.optional-dependencies]\nfeature="
           + json.dumps([f"{PACKAGES['a']}>={minimum_a},<{minimum_a + 1}"]) if name == "c" and optional else "")
        + "\n[build-system]\nrequires=[\"hatchling\"]\nbuild-backend=\"hatchling.build\""
        + "\n[project.entry-points.\"amplifier.modules\"]\n"
        + f"tool-partial-{name}=\"{package}:mount\"\n"
        + "\n[tool.hatch.build.targets.wheel]\npackages=" + json.dumps([package]) + "\n"
    )
    source = root / package
    source.mkdir(exist_ok=True)
    dependency_check = (
        "    from amplifier_module_tool_partial_a import VERSION as a_version\n"
        + (f"    assert int(a_version.split('.')[0]) == {minimum_a}, a_version\n" if not optional else "")
        if name == "c" else "    a_version = None\n"
    )
    (source / "__init__.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "__amplifier_module_type__ = 'tool'\n"
        f"VERSION = {version!r}\n"
        "class Tool:\n"
        f"    name = 'partial-{name}'\n"
        "    description = 'Offline update qualification sentinel; never execute'\n"
        "    input_schema = {'type': 'object', 'properties': {}}\n"
        "    async def execute(self, input):\n"
        f"        Path({str(forbidden)!r}).write_text('Unexpected tool execution')\n"
        "        raise AssertionError('No tool execution is allowed')\n"
        "async def mount(coordinator, config=None):\n"
        + dependency_check
        + f"    await coordinator.mount('tools', Tool(), name='partial-{name}')\n"
        + "    print(" + repr(MOUNT_PREFIX) + " + json.dumps({"
        + f"'component': {name!r}, 'version': VERSION, 'aVersion': a_version"
        + "}), flush=True)\n"
    )
    (root / "bundle.yaml").write_text(yaml.safe_dump({
        "bundle": {"name": "partial-" + name, "version": version},
        "tools": [{
            "module": "tool-partial-" + name,
            "source": "git+" + URLS[name] + "@" + REFS[name],
        }],
    }))


def _cache(root, url, ref, revision, home):
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    target = GitSourceHandler()._get_cache_path(
        parse_uri("git+" + url + "@" + ref), home / "foundation/cache"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    git(root, "clone", "--branch", ref, root, target)
    git(target, "remote", "set-url", "origin", url)
    git(target, "checkout", "--detach", revision)
    _json(target / ".amplifier_cache_meta.json", {
        "git_url": url, "ref": ref, "commit": revision,
    })
    return target


def _transport(binpath, transport_file, failure_file, home):
    """Real Git for all reads/writes; one explicit settled transport failure."""
    shim = binpath / "git"
    shim.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        "args = sys.argv[1:]\nflags = []\n"
        f"failure = Path({str(failure_file)!r})\n"
        "if failure.exists() and 'fetch' in args:\n"
        "    fault = json.loads(failure.read_text())\n"
        "    cwd = Path.cwd().resolve()\n"
        "    meta = cwd / '.amplifier_cache_meta.json'\n"
        f"    candidate = cwd.is_relative_to(Path({str(home / 'updates/releases')!r}))\n"
        "    if (candidate and fault['url'] in args and fault['revision'] in args\n"
        "            and meta.is_file() and json.loads(meta.read_text())['git_url'] == fault['url']):\n"
        "        print('Synthetic settled A fetch failure before checkout', file=sys.stderr)\n"
        "        raise SystemExit(71)\n"
        "if any(command in args for command in ('fetch', 'clone', 'ls-remote')):\n"
        f"    for url, mirror in json.loads(Path({str(transport_file)!r}).read_text()).items():\n"
        "        flags.extend(['-c', 'url.' + mirror + '.insteadOf=' + url])\n"
        "os.execv('/usr/bin/git', ['/usr/bin/git', *flags, *args])\n"
    )
    shim.chmod(0o755)


def _source_rows(inventory, name):
    rows = [row for row in inventory if row.get("url") == URLS[name]]
    assert {row["kind"] for row in rows} >= {"bundle / module", "runtime dependency"}, rows
    assert all(row["eligible"] and row["ref"] == REFS[name] for row in rows), rows
    assert all(row["updateTier"] == "included" for row in rows), rows
    assert not any(row.get("sharedSource") for row in rows), rows
    return rows


def _assert_generation(manager, generation, revisions, versions):
    stage = manager.directory / "releases" / generation
    project = runtime_environment.project_path(manager.home, generation)
    verify_recorded(project, stage)
    assert (project / "uv.lock").read_bytes() == (stage / "runtime.lock").read_bytes()
    assert json.loads((stage / "plan.json").read_text())["mode"] == "worker"
    assert json.loads((stage / "profiles-qualified.json").read_text())["profiles"] == ["fixture"]
    policies = json.loads((stage / "runtime-sources.json").read_text())
    selection = json.loads((stage / "selected-source-declarations.json").read_text())
    graph = installed_graph(project)
    sources = runtime_environment.installed_sources(project, graph=graph)
    indexed = {row["name"]: row for row in graph}
    for name, package in PACKAGES.items():
        assert policies[package]["revision"] == revisions[name]
        assert policies[package]["ref"] == REFS[name]
        assert policies[package]["url"] == URLS[name]
        assert policies[package].get("policyOrigin"), policies[package]
        assert sources[package]["current"] == revisions[name]
        assert indexed[package]["version"] == versions[name]
        assert selection["packages"][package]["revision"] == revisions[name]
        assert selection["packages"][package]["status"] == "selected"
        assert Path(selection["batchPaths"][package]).is_relative_to(stage / "foundation")
    # This is also executed in the real --checked-sources probe before install.
    verify_checked_selection(policies, selection, exact=True)
    evidence = update_health.active_evidence(manager)
    assert evidence.get("generation") == generation, evidence
    assert evidence.get("verifiedAt") and not evidence.get("activationUnknown"), evidence
    return stage, project, selection


def _assert_probes(probes, generation, versions):
    selected = [row for row in probes if row["generation"] == generation]
    assert selected and all(row["returncode"] == 0 for row in selected), selected
    assert any("--source-policy-only" in row["command"] for row in selected)
    prepared = [row for row in selected if "--checked-sources" in row["command"]]
    mounted = [row for row in selected if "--read-only" in row["command"]]
    assert len(prepared) == len(mounted) == 1, selected
    assert '"dependenciesPrepared": true' in prepared[0]["stdout"]
    assert '"ok": true' in mounted[0]["stdout"]
    actual = [
        json.loads(line.split(MOUNT_PREFIX, 1)[1])
        for line in mounted[0]["stdout"].splitlines() if MOUNT_PREFIX in line
    ]
    assert {row["component"]: row["version"] for row in actual} == versions, actual
    # Capability checking can initialize a module more than once. Verify every
    # observed mount, not a guessed count of initialization hooks.
    assert all(row["version"] == versions[row["component"]] for row in actual), actual
    assert next(row for row in actual if row["component"] == "c")["aVersion"] == versions["a"]


@pytest.mark.parametrize("dependency_kind", ["required", "optional"])
async def test_settled_fetch_retains_qualified_a_holds_c_and_advances_moving_b(monkeypatch, dependency_kind):
    assert sys.platform.startswith("linux") and sys.version_info[:2] == (3, 13)
    bootstrap_uv = Path("/opt/unified-prepared/bootstrap/bin/uv")
    assert bootstrap_uv.is_file() and Path("/usr/bin/git").is_file()
    artifact_root = Path(os.environ.get(
        "AMPLIFIER_PARTIAL_UPDATE_ARTIFACTS", "/artifacts/partial-update-native"
    ))
    work = artifact_root / uuid.uuid4().hex
    work.mkdir(parents=True)
    print(f"Partial update synthetic-native artifacts: {work}")
    home, shared, envhome = work / "app", work / "shared", work / "process-home"
    for path in (home, shared, envhome):
        path.mkdir()
    forbidden = work / "unexpected-execution.txt"
    monkeypatch.setenv("HOME", str(envhome))
    monkeypatch.setenv("AMPLIFIER_HOME", str(shared))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    for key in (
        "AMPLIFIER_UNIFIED_RELEASE", "AMPLIFIER_SOURCE_STORE",
        "UV_PROJECT_ENVIRONMENT", "UV_OFFLINE", "UV_NO_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(envhome / ".gitconfig"))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    # The URL rewrite precedes protocol admission. Any unmapped Git network URL
    # fails locally rather than making this fixture depend on an external remote.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_CACHE_DIR", str(work / "uv-cache"))
    wheels = work / "wheels"
    _json(work / "registry-wheel-payloads.json", repack_registry_wheels(wheels))
    monkeypatch.setenv("UV_FIND_LINKS", str(wheels))
    config = work / "uv.toml"
    config.write_text("no-index=true\nfind-links=[" + json.dumps(str(wheels)) + "]\n")
    binpath = work / "bin"
    binpath.mkdir()
    uv = binpath / "uv"
    uv.write_text(
        "#!/bin/sh\nexec " + shlex.quote(str(bootstrap_uv)) + " --config-file "
        + shlex.quote(str(config)) + ' "$@"\n'
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(binpath) + ":" + str(bootstrap_uv.parent) + ":" + os.environ["PATH"])

    core = fixture_core(work / "core", wheels)
    foundation = work / "foundation"
    foundation_proof = _foundation(foundation)
    roots, old, transport = {}, {}, {
        CORE_URL: core.as_uri(), FOUNDATION_URL: foundation.as_uri(),
    }
    for name in "abc":
        root = work / ("source-" + name)
        _component(root, name, "1.0.0", forbidden, optional=dependency_kind == "optional")
        _init_repo(root, REFS[name])
        old[name] = _commit(root, "Working " + name + " version 1")
        roots[name] = root
        transport[URLS[name]] = root.as_uri()
        _cache(root, URLS[name], REFS[name], old[name], home)
    provider = fixture_provider(
        work / "provider", ["amplifier-core @ git+" + CORE_URL + "@main"]
    )
    provider_code = provider / "amplifier_module_provider_update_fixture/__init__.py"
    original_provider = provider_code.read_text()
    sentinel = "async def complete(self,*a,**k):raise AssertionError('No model call allowed')"
    assert original_provider.count(sentinel) == 1, "Maintained provider helper changed"
    provider_code.write_text(original_provider.replace(
        sentinel,
        "async def complete(self,*a,**k):\n"
        "  from pathlib import Path\n"
        f"  Path({str(forbidden)!r}).write_text('Unexpected model call')\n"
        "  raise AssertionError('No model call allowed')",
    ))
    _init_repo(provider)
    _commit(provider, "No-model qualification provider")
    transport[PROVIDER_URL] = provider.as_uri()
    transport_file, failure_file = work / "transport.json", work / "fetch-failure.json"
    _json(transport_file, transport)
    _transport(binpath, transport_file, failure_file, home)

    skills = fixture_module(
        work / "skills", wheels, "amplifier-module-tool-skills", "amplifier_module_tool_skills"
    )
    context = fixture_module(
        work / "context-hook", wheels,
        "amplifier-module-hook-context-intelligence", "amplifier_module_hook_context_intelligence",
    )
    manifest = work / "worker.toml"
    requirements = [
        "packaging", "amplifier-core", "amplifier-foundation",
        "amplifier-module-loop-live", "amplifier-module-context-managed",
        "amplifier-module-hooks-session-naming",
        "amplifier-module-tool-skills @ " + skills.as_uri(),
        "amplifier-module-hook-context-intelligence @ " + context.as_uri(),
        *PACKAGES.values(),
    ]
    sources = {
        # Match the direct dependency's rev spelling. uv treats branch=main
        # and direct @main as distinct constraints even for the same Git URL.
        "amplifier-core": {"git": CORE_URL, "rev": "main"},
        "amplifier-foundation": {"git": FOUNDATION_URL, "branch": "main"},
        **{PACKAGES[name]: {"git": URLS[name], "branch": REFS[name]} for name in "abc"},
    }
    manifest.write_text(
        "[project]\nname=\"partial-update-worker\"\nversion=\"1\"\nrequires-python=\"==3.13.*\""
        + "\ndependencies=" + json.dumps(requirements)
        + "\n[tool.uv]\nenvironments="
        + json.dumps([f"sys_platform == 'linux' and platform_machine == '{platform.machine()}'"])
        + '\noverride-dependencies=["amplifier-module-loop-streaming==1.0.0",'
        + '"amplifier-module-context-simple==1.0.0","amplifier-bundle-context-intelligence==0.2.0"]'
        + "\n[tool.uv.sources]\n"
        + "\n".join(json.dumps(name) + "=" + runtime_environment.toml_value(source)
                    for name, source in sources.items()) + "\n"
    )
    monkeypatch.setattr(runtime_environment, "manifest_path", lambda: manifest)
    monkeypatch.setattr(app_updates, "installed_extras", lambda: [])
    # Existing feature-detection seam. Never stub validation, policy, or freeze.
    monkeypatch.setattr(update_sources, "store_environment", lambda home: {})
    monkeypatch.setattr(update_sources, "worker_supports_shared", lambda project: False)

    seed_project = runtime_environment.prepare_project(home)
    setup = subprocess.run(
        [str(uv), "sync", "--project", str(seed_project), "--python", sys.executable],
        capture_output=True, text=True, timeout=900,
    )
    (work / "seed-sync.log").write_text(setup.stdout + setup.stderr)
    assert setup.returncode == 0, setup.stdout + setup.stderr
    site = next((seed_project / ".venv/lib").glob("python*/site-packages"))
    profile = work / "profile.yaml"
    profile.write_text(yaml.safe_dump({
        "bundle": {"name": "fixture"},
        "session": {
            "orchestrator": {"module": "loop-streaming"},
            "context": {"module": "context-simple"},
        },
        "providers": [{
            "module": "provider-update-fixture", "source": "git+" + PROVIDER_URL + "@main",
            "config": {"default_model": "fixture"},
        }],
    }))
    settings = shared / "settings.yaml"
    settings.write_text(yaml.safe_dump({
        "bundle": {
            "active": "fixture", "added": {"fixture": str(profile)},
            "app": ["git+" + URLS[name] + "@" + REFS[name] for name in "abc"],
        },
        "web_bundles": {
            "excluded": list(builtin_behaviors.DEFAULT_BEHAVIORS),
            "entries": [
                {"name": name, "uri": uri, "role": "standalone", "enabled": False}
                for name, uri in PRECONFIGURED_BUNDLES.items()
            ],
        },
        "sources": {"modules": {
            "context-simple": str(site / "amplifier_module_context_managed"),
            "tool-skills": str(skills), "hook-context-intelligence": str(context),
        }},
    }))
    settings_before, manifest_before = settings.read_bytes(), manifest.read_bytes()
    seed_before = _tree(seed_project)
    seed_sources_before = _tree(home / "foundation")
    # A real changed component triggers ordinary initial qualification; the seed
    # uv sync above is deliberately NOT claimed as the working baseline.
    (core / "fixture-revision.txt").write_text("Trigger first manager qualification\n")
    _commit(core, "Bootstrap the verified working generation")
    _json(work / "inputs.json", {
        "foundation": foundation_proof, "old": old, "refs": REFS,
        "manifestSha256": hashlib.sha256(manifest_before).hexdigest(),
        "limits": ["synthetic packaging", "owned Git cache only", "no model/tool execution",
                   "no busy-worker handoff", "no public artifact or Windows qualification"],
    })

    async def application_current():
        return {"id": "application", "label": "Synthetic current host", "status": "current"}

    monkeypatch.setattr(app_updates, "check", application_current)
    runtime = RuntimeManager()
    app = AppService(home, runtime, workspace=work)
    runtime.home = home
    manager = updates.UpdateManager(app)
    app.update_manager = manager
    commands, probes, promotions, publications = [], [], [], []
    original_process = updates.process
    original_spawn = asyncio.create_subprocess_exec
    original_promote = runtime.promote_generation
    original_publish = manager.publish

    async def record_process(*args, **kwargs):
        row = {"command": list(map(str, args)), "cwd": str(kwargs.get("cwd", ""))}
        commands.append(row)
        try:
            result = await original_process(*args, **kwargs)
            row["succeeded"] = True
            return result
        except BaseException as error:
            row["failure"] = dict(getattr(error, "diagnostic_facts", {}))
            raise

    async def record_spawn(*args, **kwargs):
        assert not any(str(arg).endswith("runtime_worker.py") for arg in args), args
        child = await original_spawn(*args, **kwargs)
        if any(str(arg).endswith("update_probe.py") for arg in args):
            generation = Path(kwargs["env"]["AMPLIFIER_WEB_HOME"]).name
            row = {"command": list(map(str, args)), "generation": generation}
            probes.append(row)
            communicate = child.communicate

            async def capture(*a, **kw):
                out, err = await communicate(*a, **kw)
                row.update(
                    stdout=(out or b"").decode(errors="replace"),
                    stderr=(err or b"").decode(errors="replace"),
                    returncode=child.returncode,
                )
                return out, err

            child.communicate = capture
        elif "lock" in args or "sync" in args:
            communicate = child.communicate
            async def capture_resolver(*a, **kw):
                out, err = await communicate(*a, **kw)
                index = len(list(work.glob("resolver-*.log")))
                (work / f"resolver-{index}.log").write_bytes((out or b"") + b"\nSTDERR\n" + (err or b""))
                return out, err
            child.communicate = capture_resolver
        return child

    def record_promotion(generation, **kwargs):
        assert updates.active_release(home)["current"] == generation
        verify_recorded(runtime_environment.project_path(home, generation),
                        manager.directory / "releases" / generation)
        promotions.append(generation)
        return original_promote(generation, **kwargs)

    async def record_publication(**kwargs):
        if kwargs.get("phase") == "staged":
            assert updates.active_release(home).get("current") == (
                promotions[-1] if promotions else None
            ), "An unpromoted candidate changed the active pointer"
        if kwargs.get("phase") in {"staged", "installed"}:
            publications.append(copy.deepcopy(kwargs))
        return await original_publish(**kwargs)

    monkeypatch.setattr(updates, "process", record_process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    monkeypatch.setattr(runtime, "promote_generation", record_promotion)
    monkeypatch.setattr(manager, "publish", record_publication)

    async def settle_cleanup():
        if manager.cleanup_task:
            await manager.cleanup_task

    try:
        await manager.check(fresh=True)
        _json(work / "baseline-checked.json", manager.inventory)
        assert app.state["updates"]["phase"] == "available", app.state["updates"]
        for name in "abc":
            assert all(row["current"] == old[name] for row in _source_rows(manager.inventory, name))
        await manager.install()
        assert app.state["updates"]["phase"] == "installed", app.state["updates"]
        baseline = updates.active_release(home)["current"]
        assert promotions == [baseline]
        old_versions = dict.fromkeys("abc", "1.0.0")
        baseline_stage, baseline_project, _ = _assert_generation(manager, baseline, old, old_versions)
        _assert_probes(probes, baseline, old_versions)
        await settle_cleanup()
        baseline_bytes = (_tree(baseline_stage), _tree(baseline_project))
        baseline_pointer = (manager.directory / "active.json").read_bytes()
        assert _tree(seed_project) == seed_before
        assert _tree(home / "foundation") == seed_sources_before

        # A saved, unmounted conversation is a byte-preservation sentinel, not a
        # fabricated running turn. No runtime input/recovery call is issued.
        history = sessions_dir(work) / "partial-native-history"
        history.mkdir(parents=True)
        _json(history / "metadata.json", {
            "session_id": history.name, "bundle": "fixture", "cwd": str(work),
        })
        (history / "transcript.jsonl").write_text(
            '{"role":"user","content":"Do not replay this saved input."}\n'
            '{"role":"assistant","content":"Previously completed synthetic work."}\n'
        )
        history_before = _tree(history)

        new = {}
        for name in "abc":
            _component(roots[name], name, "2.0.0", forbidden, optional=dependency_kind == "optional")
            new[name] = _commit(roots[name], "New " + name + " version 2")
        await manager.check(fresh=True)
        checked = copy.deepcopy(manager.inventory)
        _json(work / "partial-checked.json", checked)
        assert app.state["updates"]["phase"] == "available", app.state["updates"]
        for name in "abc":
            assert all(row["status"] == "update" and row["current"] == old[name]
                       and row["latest"] == new[name] for row in _source_rows(checked, name))
        assert (manager.directory / "active.json").read_bytes() == baseline_pointer
        # Availability really succeeded. Only the later staged A fetch exits 71.
        _json(failure_file, {"url": URLS["a"], "revision": new["a"]})
        start = len(commands)
        await manager.install()
        assert app.state["updates"]["phase"] == "installed", app.state["updates"]
        partial_generation = updates.active_release(home)["current"]
        assert updates.active_release(home)["previous"] == baseline
        assert promotions == [baseline, partial_generation]
        expected = {**old, "b": new["b"]}
        versions = {**old_versions, "b": "2.0.0"}
        stage, project, selection = _assert_generation(
            manager, partial_generation, expected, versions
        )
        _assert_probes(probes, partial_generation, versions)
        receipt = update_partial.read(stage)
        assert receipt["baseline"] == baseline
        assert receipt["pointer"] == hashlib.sha256(baseline_pointer).hexdigest()
        assert set(receipt["retained"]) == {PACKAGES["a"], PACKAGES["c"]}
        historical_failures = [
            event for event in _journal(manager)
            if event.get("attemptId") == receipt["attemptId"]
            and event["phase"] == "ecosystem-fetch" and event["status"] == "failed"
        ]
        assert len(historical_failures) == 1 and historical_failures[0]["exitCode"] == 71
        failure_event = historical_failures[0]
        assert any(event["phase"] == "ecosystem-activation" and event["status"] == "succeeded"
                   and event.get("attemptId") == receipt["attemptId"] for event in _journal(manager))
        for name, status in {"a": "failed", "b": "awaiting-adoption", "c": "dependency-held"}.items():
            for row in _source_rows(checked, name):
                assert receipt["results"][row["id"]]["status"] == status
                assert receipt["results"][row["id"]]["revision"] == expected[name]
        staged = next(row for row in publications if row.get("pendingRelease") == partial_generation)
        assert {row["status"] for row in staged["componentResults"]} == {
            "failed", "awaiting-adoption", "dependency-held",
        }
        assert {row["status"] for row in app.state["updates"]["componentResults"]} == {
            "failed", "updated", "dependency-held",
        }
        assert app.state["updates"]["currentStatus"]["status"] == "action-required"

        # The early hold comes from actual canonical collection of the NEW C
        # manifest; the final union is recollected with the retained OLD C.
        early = json.loads((stage / "partial-selected-roots.json").read_text())
        assert early["runtimeRequirements"][PACKAGES["c"]]["project"]["version"] == "2.0.0"
        requirement_project = early["runtimeRequirements"][PACKAGES["c"]]["project"]
        requirements = (requirement_project["optional-dependencies"]["feature"] if dependency_kind == "optional"
                        else requirement_project["dependencies"])
        assert f"{PACKAGES['a']}>=2,<3" in requirements
        if dependency_kind == "optional":
            assert f"{PACKAGES['c']}[feature]" in early["runtimeRequirements"][PACKAGES["b"]]["project"]["dependencies"]
            assert not any(item.startswith(PACKAGES['a']) for item in requirement_project["dependencies"])
        assert selection["runtimeRequirements"][PACKAGES["c"]]["project"]["version"] == "1.0.0"
        attempt_commands = commands[start:]
        precollect = next(i for i, row in enumerate(attempt_commands)
                          if str(stage / "partial-selected-roots.json") in row["command"])
        early_probe = next(row for row in probes
                           if str(stage / "partial-selected-roots.json") in row["command"])
        assert '"dependenciesPrepared": false' in early_probe["stdout"]
        assert MOUNT_PREFIX not in early_probe["stdout"]
        first_install = next(i for i, row in enumerate(attempt_commands)
                             if "sync" in row["command"] or "lock" in row["command"])
        assert precollect < first_install
        failed = [row for row in attempt_commands
                  if "fetch" in row["command"] and URLS["a"] in row["command"]]
        assert len(failed) == 1 and failed[0]["failure"]["exitCode"] == 71, failed
        assert not failed[0]["failure"].get("timedOut")
        assert any("checkout" in row["command"] and new["c"] in row["command"]
                   for row in attempt_commands)
        assert any("checkout" in row["command"] and old["c"] in row["command"]
                   for row in attempt_commands)
        assert len([row for row in attempt_commands
                    if "sync" in row["command"] and str(project) in row["command"]]) == 1
        await settle_cleanup()
        assert (_tree(baseline_stage), _tree(baseline_project)) == baseline_bytes
        assert _tree(seed_project) == seed_before
        assert _tree(home / "foundation") == seed_sources_before

        # Strict-source negative control is in-memory only: mismatched evidence
        # must still fail; never patch a frozen candidate to manufacture a pass.
        policies = json.loads((stage / "runtime-sources.json").read_text())
        wrong = copy.deepcopy(selection)
        wrong["packages"][PACKAGES["a"]]["revision"] = new["a"]
        with pytest.raises(ValueError):
            verify_checked_selection(policies, wrong, exact=True)

        pointer_before_auto = (manager.directory / "active.json").read_bytes()
        releases_before_auto = set((manager.directory / "releases").iterdir())
        before_auto = len(commands), len(probes), list(promotions)
        await manager.check(fresh=False)
        assert all(row["status"] == "check_failed"
                   for name in "ac" for row in _source_rows(manager.inventory, name))
        assert all(row["status"] == "current" for row in _source_rows(manager.inventory, "b"))
        assert not any(row.get("status") == "update" for row in manager.inventory)
        await manager.install(automatic=True)
        await manager.tick()
        await settle_cleanup()
        assert (manager.directory / "active.json").read_bytes() == pointer_before_auto
        assert set((manager.directory / "releases").iterdir()) == releases_before_auto
        assert len(probes) == before_auto[1] and promotions == before_auto[2]
        assert not any("fetch" in row["command"] or "sync" in row["command"]
                       or "lock" in row["command"] for row in commands[before_auto[0]:])
        assert next(event for event in _journal(manager)
                    if event["id"] == failure_event["id"]) == failure_event
        assert _tree(history) == history_before
        assert not runtime.workers and not forbidden.exists()

        # Retention evidence must not turn B's selected custom moving branch into
        # a permanent commit pin. Keep A's fault enabled for this second advance.
        retained_bytes = (_tree(stage), _tree(project))
        _component(roots["b"], "b", "3.0.0", forbidden, optional=dependency_kind == "optional")
        b_third = _commit(roots["b"], "Independent B advances again on stable")
        await manager.check(fresh=True)
        _json(work / "second-checked.json", manager.inventory)
        assert all(row["status"] == "update" and row["current"] == new["b"]
                   and row["latest"] == b_third for row in _source_rows(manager.inventory, "b"))
        await manager.install()
        assert app.state["updates"]["phase"] == "installed", app.state["updates"]
        second = updates.active_release(home)["current"]
        assert updates.active_release(home)["previous"] == partial_generation
        assert promotions == [baseline, partial_generation, second]
        second_stage, _, _ = _assert_generation(
            manager, second, {**old, "b": b_third}, {**old_versions, "b": "3.0.0"}
        )
        _assert_probes(probes, second, {**old_versions, "b": "3.0.0"})
        assert update_partial.read(second_stage)["baseline"] == partial_generation
        await settle_cleanup()
        assert (_tree(stage), _tree(project)) == retained_bytes
        assert settings.read_bytes() == settings_before
        assert manifest.read_bytes() == manifest_before
        assert next(event for event in _journal(manager)
                    if event["id"] == failure_event["id"]) == failure_event
        assert _tree(history) == history_before
        assert not runtime.workers and not forbidden.exists()
        # A later incompatible union must fail closed, rather than partitioning
        # installer/initialization failures into arbitrary component subsets.
        _component(roots["b"], "b", "4.0.0", forbidden, optional=dependency_kind == "optional")
        entry = roots["b"] / "amplifier_module_tool_partial_b/__init__.py"
        entry.write_text(entry.read_text().replace(
            "async def mount(coordinator, config=None):",
            "async def mount(coordinator, config=None):\n    raise RuntimeError('Incompatible candidate fixture')"))
        _commit(roots["b"], "Refuse incompatible B initialization")
        await manager.check(fresh=True)
        safe_pointer = (manager.directory / "active.json").read_bytes()
        await manager.install()
        assert app.state["updates"]["phase"] == "error"
        assert (manager.directory / "active.json").read_bytes() == safe_pointer
        assert promotions == [baseline, partial_generation, second]
        assert manager.diagnostics.state["batch"]["retry"]["mode"] == "changed-inputs"
        no_retry = len(commands), len(probes)
        await manager.install(automatic=True)
        assert (len(commands), len(probes)) == no_retry
        failed_key = manager.diagnostics.state["batch"]["retry"]["inputKey"]
        generations = set((manager.directory / "releases").iterdir())
        await manager.check(fresh=False, automatic=True)
        assert update_health.preparation_key(manager, manager.inventory) == failed_key
        after_check = len(commands), len(probes)
        await manager.install(automatic=True)
        assert (len(commands), len(probes)) == after_check
        assert set((manager.directory / "releases").iterdir()) == generations
        # Return to the previous qualified mixed generation by the ordinary
        # rollback path. No fresh resolver subset or host restart is introduced.
        await manager.rollback()
        assert updates.active_release(home)["current"] == partial_generation
        assert promotions == [baseline, partial_generation, second, partial_generation]
        verify_recorded(project, stage)
        assert _tree(history) == history_before
        assert next(event for event in _journal(manager)
                    if event["id"] == failure_event["id"]) == failure_event
        _json(work / "verified-result.json", {
            "baseline": baseline, "partial": partial_generation, "second": second,
            "revisions": {**old, "b": b_third}, "promotions": promotions,
            "historyUnchanged": True, "sharedStoreCovered": False,
            "dependencyKind": dependency_kind,
            "incompatibleUnionRefused": True, "unchangedFailureNotRetried": True,
            "rollback": partial_generation,
        })
    finally:
        try:
            _json(work / "commands.json", commands)
            _json(work / "probes.json", probes)
            _json(work / "publications.json", publications)
            _json(work / "diagnostics.json", manager.diagnostics.state)
            _json(work / "updates-state.json", dict(app.state["updates"]))
        finally:
            await app.close()