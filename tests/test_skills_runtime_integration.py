"""Opt-in, offline installed-runtime skill loading through real Core/Foundation.

The parent owns the isolated guest and installs non-editable Unified, Core,
Foundation, context-simple, and these two pinned tool distributions:
  amplifier-bundle-skills@2f58408a879535df36da5c92d2b073f49271ade4
    #subdirectory=modules/tool-skills
  amplifier-module-tool-filesystem@663f26a878ae71e136f439fa73ded52b2eb24174

Example INSIDE the prepared guest, with the venv outside the source checkout:
  UNIFIED_SKILLS_RUNTIME=1 UNIFIED_SKILLS_CHECKOUT=/workspace \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /opt/skills-venv/bin/python -I -B -m pytest \
  --noconftest --import-mode=importlib -p no:cacheprovider -c /dev/null -q -s \
  /workspace/tests/test_skills_runtime_integration.py

Unset opt-in skips. Once opted in, missing packages, provenance, or resources
FAIL. No installation, source sys.path injection, model inference, or network.
The only synthetic runtime component is a finite orchestrator, necessary because
PreparedBundle.spawn executes its child. Tools, coordinators, sessions, mention
resolution, lifecycle hooks, and child spawning are real. PreparedBundle uses
already installed module paths: this does not qualify dependency activation,
Unified's complete host bootstrap/native delegation, or paid-provider transport.
The Git-shaped source is a seeded synthetic cache hit, NOT a fetched repository.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


PINS = {
    "amplifier-module-tool-skills": "2f58408a879535df36da5c92d2b073f49271ade4",
    "amplifier-module-tool-filesystem": "663f26a878ae71e136f439fa73ded52b2eb24174",
}
COMPANION = 'print("SKILLS_COMPANION_OK")\n'
ORCHESTRATOR = '''
"""Synthetic finite driver; never constructs or calls a provider."""
__amplifier_module_type__ = "orchestrator"

class SkillsProbe:
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        assert not providers, "No provider may be mounted in this probe"
        verify = coordinator.get_capability("skills-probe.verify")
        assert verify is not None, "Child verification callback is missing"
        await verify(coordinator)
        await hooks.emit("orchestrator:complete", {"status": "success", "turn_count": 0})
        return "skills-child-verified"

async def mount(coordinator, config=None):
    await coordinator.mount("orchestrator", SkillsProbe())
'''


def _installed(package, distribution, checkout):
    """Verify actual installed origins and wheel RECORD bytes, never importorskip."""
    dist = importlib.metadata.distribution(distribution)
    assert dist.read_text("WHEEL") and dist.read_text("RECORD"), distribution
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable"), distribution
    if distribution in PINS:
        assert direct.get("vcs_info", {}).get("commit_id") == PINS[distribution], (
            f"{distribution} must retain PEP 610 Git provenance for {PINS[distribution]}"
        )
    root = Path(dist.locate_file(package)).resolve(strict=True)
    assert not root.is_relative_to(checkout), f"{package} imported from checkout"
    spec = importlib.util.find_spec(package)
    assert spec is not None and spec.origin, f"Missing installed {package}"
    assert Path(spec.origin).resolve() == root / "__init__.py", spec.origin
    hashes = {}
    for entry in dist.files or ():
        relative = str(entry)
        if not relative.startswith(package + "/"):
            continue
        # Check executable module bytes and the real shipped skill resources.
        if not (relative.endswith((".py", ".so")) or "/bundle_data/skills/" in relative):
            continue
        path = Path(dist.locate_file(entry)).resolve(strict=True)
        assert path.is_relative_to(root) and entry.hash and entry.hash.mode == "sha256"
        data = path.read_bytes()
        digest = hashlib.sha256(data)
        assert len(data) == entry.size
        assert base64.urlsafe_b64encode(digest.digest()).decode().rstrip("=") == entry.hash.value
        hashes[relative] = digest.hexdigest()
    assert package + "/__init__.py" in hashes
    module = importlib.import_module(package)
    assert Path(module.__file__).resolve() == root / "__init__.py"
    return module, {
        "package": package, "version": dist.version, "path": str(root),
        "commit": direct.get("vcs_info", {}).get("commit_id"),
        "entry_sha256": hashes[package + "/__init__.py"],
        "verified_files": len(hashes),
        "manifest_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
    }


def _skill(root, name, body):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Synthetic {name} control.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return directory


async def _call(coordinator, tool, **arguments):
    result = await coordinator.get("tools")[tool].execute(arguments)
    assert result.success, (tool, arguments, result.error, result.output)
    return result.output


async def _absent(coordinator, name):
    tool = coordinator.get("tools")["load_skill"]
    search = await tool.execute({"search": name})
    assert search.success and search.output["message"] == f"No skills matching '{name}'"
    result = await tool.execute({"skill_name": name})
    assert not result.success and result.error["message"].startswith(f"Skill '{name}' not found.")
    assert "denied" not in result.error["message"].lower()


async def _read_exact(coordinator, path):
    result = await _call(coordinator, "read_file", file_path=str(path))
    expected = path.read_text(encoding="utf-8").splitlines()
    assert result["lines_read"] == result["total_lines"] == len(expected)
    assert result["offset"] == 1 and Path(result["file_path"]).resolve() == path.resolve()
    assert result["content"] == "\n".join(f"{n:6d}\t{line}" for n, line in enumerate(expected, 1))
    return result


def test_installed_skills_root_child_and_companions(tmp_path, monkeypatch, caplog):
    opt_in = os.environ.get("UNIFIED_SKILLS_RUNTIME")
    if opt_in is None:
        pytest.skip("Opt in only in the parent-owned guest with UNIFIED_SKILLS_RUNTIME=1")
    assert opt_in == "1", "UNIFIED_SKILLS_RUNTIME must be exactly 1 when enabled"
    assert Path("/.dockerenv").is_file() or Path("/run/systemd/container").is_file(), (
        "Skills qualification is guest-only; do not run it on the host"
    )
    assert sys.flags.isolated and not sys.flags.optimize, "Use python -I, without -O"
    checkout = Path(os.environ["UNIFIED_SKILLS_CHECKOUT"]).resolve(strict=True)
    assert (checkout / "pyproject.toml").is_file()
    assert not Path(sys.prefix).resolve().is_relative_to(checkout), (
        "Install the qualification runtime outside the source checkout"
    )
    for name, relative in {
        "HOME": "home", "AMPLIFIER_HOME": "home/.amplifier",
        "AMPLIFIER_WEB_HOME": "app", "AMPLIFIER_SESSION_STATE_HOME": "sessions",
        "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data",
    }.items():
        directory = tmp_path / relative
        directory.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(directory))
    for name in (
        "AMPLIFIER_SKILLS_DIR", "AMPLIFIER_SOURCE_STORE", "PYTHONPATH", "PYTHONHOME",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "AZURE_OPENAI_API_KEY", "SSH_AUTH_SOCK",
    ):
        monkeypatch.delenv(name, raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    caplog.set_level(logging.WARNING)
    forbidden = []
    admitted_processes = []
    allowed_command = None
    guard_active = True

    def audit(event, args):
        if not guard_active:
            return
        if event == "subprocess.Popen":
            if allowed_command is not None and list(args[1]) == allowed_command:
                admitted_processes.append(list(args[1]))
                return
        elif event in {"socket.connect", "socket.sendto", "socket.bind"}:
            if args[0].family not in {socket.AF_INET, socket.AF_INET6}:
                return
            # urllib3's import-time IPv6 capability probe is not application I/O.
            if event == "socket.bind" and args[-1] == ("::1", 0):
                raise OSError("Offline fixture disables IPv6 bind probes")
        elif event not in {
            "socket.getaddrinfo", "socket.gethostbyname", "os.system",
            "os.posix_spawn", "os.exec", "os.fork", "os.forkpty",
        }:
            return
        forbidden.append(event)
        raise RuntimeError(f"Offline skills fixture forbids {event}")

    sys.addaudithook(audit)
    try:
        modules, provenance = {}, []
        for package, distribution in (
            ("amplifier_web", "amplifier-unified"),
            ("amplifier_core", "amplifier-core"),
            ("amplifier_foundation", "amplifier-foundation"),
            ("amplifier_module_context_simple", "amplifier-module-context-simple"),
            ("amplifier_module_tool_skills", "amplifier-module-tool-skills"),
            ("amplifier_module_tool_filesystem", "amplifier-module-tool-filesystem"),
        ):
            module, proof = _installed(package, distribution, checkout)
            modules[package] = module
            provenance.append(proof)
        from amplifier_core import AmplifierSession, HookResult
        from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
        from amplifier_foundation.mentions import BaseMentionResolver

        packaged = Path(modules["amplifier_web"].__file__).parent / "bundle_data"
        unified_records = {
            str(entry) for entry in importlib.metadata.distribution("amplifier-unified").files or ()
        }
        for relative in (
            "skills/amplifier-shell/SKILL.md", "skills/amplifier-shell/presentation.md",
            "skills/coordinate-work/SKILL.md", "skills/coordinate-work/references/protocol.md",
        ):
            assert (packaged / relative).is_file(), f"Missing installed Unified resource: {relative}"
            assert "amplifier_web/bundle_data/" + relative in unified_records, relative

        local = workspace / ".amplifier/skills"
        user = Path.home() / ".amplifier/skills"
        control = _skill(local, "fixture-control", "WORKSPACE-FIRST")
        _skill(user, "fixture-control", "USER-SHADOWED")
        _skill(user, "fixture-user-only", "USER-ONLY")
        companion = control / "safe_companion.py"
        companion.write_text(COMPANION, encoding="utf-8")
        invalid = local / "fixture-invalid/SKILL.md"
        invalid.parent.mkdir()
        invalid.write_text("---\nname: fixture-invalid\ndescription: [\n---\nINVALID\n", encoding="utf-8")

        # Seed only a synthetic *cache hit*. Do not invoke the private resolver,
        # replace it, clone a repository, or describe this as fetch coverage.
        remote_base = "https://example.invalid/skills-fixture.git@fixture-cache"
        remote = "git+" + remote_base + "#subdirectory=skills"
        cache = tmp_path / "skills-cache"
        cache_root = cache / ("skills-fixture-" + hashlib.sha256(remote_base.encode()).hexdigest()[:16])
        cached = _skill(cache_root / "skills", "fixture-cached", "CACHED-NOT-FETCHED")
        (cache_root / ".amplifier_cache_meta.json").write_text('{"synthetic_cache_fixture": true}\n')
        probe_module = tmp_path / "amplifier_module_loop_skills_probe"
        probe_module.mkdir()
        (probe_module / "__init__.py").write_text(ORCHESTRATOR, encoding="utf-8")
        paths = {
            name: Path(modules["amplifier_module_" + name.replace("-", "_")].__file__).parent
            for name in ("context-simple", "tool-skills", "tool-filesystem")
        }
        paths["loop-skills-probe"] = probe_module
        resolver = BundleModuleResolver(paths)
        sources = [str(local), str(user), remote, "@unified:skills"]
        base_config = {"visibility": {"placement": "request"}, "cache_dir": str(cache)}
        filesystem_config = {"allowed_read_paths": [str(tmp_path), str(packaged)],
                             "allowed_write_paths": [str(workspace)]}
        observations = []

        def prepared(skills_config):
            bundle = Bundle(
                name="skills-probe", base_path=workspace,
                source_base_paths={"unified": packaged},
                session={"orchestrator": {"module": "loop-skills-probe"},
                         "context": {"module": "context-simple"}},
                tools=[{"module": "tool-skills", "config": skills_config},
                       {"module": "tool-filesystem", "config": filesystem_config}],
            )
            return PreparedBundle(bundle.to_mount_plan(), resolver, bundle)

        async def controls(coordinator):
            assert not coordinator.get("providers"), "No provider may be mounted"
            tools = coordinator.get("tools")
            assert isinstance(tools["load_skill"], modules["amplifier_module_tool_skills"].SkillsTool)
            assert isinstance(tools["read_file"], modules["amplifier_module_tool_filesystem"].ReadTool)
            listing = await _call(coordinator, "load_skill", list=True)
            names = [row["name"] for row in listing["skills"]]
            assert names.count("fixture-control") == 1 and "fixture-user-only" in names
            loaded = await _call(coordinator, "load_skill", skill_name="fixture-control")
            assert "WORKSPACE-FIRST" in loaded["content"] and "USER-SHADOWED" not in loaded["content"]
            assert Path(loaded["skill_directory"]).resolve() == control
            assert Path(loaded["loaded_from"]).resolve() == local
            user_loaded = await _call(coordinator, "load_skill", skill_name="fixture-user-only")
            assert "USER-ONLY" in user_loaded["content"]
            info = await _call(coordinator, "load_skill", info="fixture-control")
            assert Path(info["path"]) == control / "SKILL.md"
            await _absent(coordinator, "fixture-invalid")
            await _absent(coordinator, "fixture-nonexistent")
            return Path(loaded["skill_directory"])

        async def shipped(coordinator):
            for name, heading in (("amplifier-shell", "# Amplifier Unified shell"),
                                  ("coordinate-work", "# Coordinate work")):
                loaded = await _call(coordinator, "load_skill", skill_name=name)
                assert heading in loaded["content"]
                directory = Path(loaded["skill_directory"]).resolve()
                assert directory == packaged / "skills" / name
                await _read_exact(coordinator, directory / "SKILL.md")
                if name == "amplifier-shell":
                    await _read_exact(coordinator, directory / "presentation.md")
                else:
                    assert "references/protocol.md" in loaded["content"]
                    assert "# Coordination protocol reference" not in loaded["content"]
                    await _read_exact(coordinator, directory / "references/protocol.md")
            loaded = await _call(coordinator, "load_skill", skill_name="fixture-cached")
            assert loaded["loaded_from"] == remote and "CACHED-NOT-FETCHED" in loaded["content"]
            assert Path(loaded["skill_directory"]) == cached

        async def request_boundary(coordinator, *, includes_unified):
            # Exercise real registered hooks, NOT a provider completion. Request
            # placement makes the first pre-provider catalog directly observable.
            result = await coordinator.hooks.emit("provider:request", {"messages": []})
            assert result.action == "inject_context"
            assert "fixture-control" in result.context_injection
            assert ("coordinate-work" in result.context_injection) is includes_unified
            assert ("amplifier-shell" in result.context_injection) is includes_unified

        def observe_mount(label, child_check=None):
            async def before_initialize(session):
                assert isinstance(session, AmplifierSession) and not session.initialized
                mention = session.coordinator.get_capability("mention_resolver")
                assert isinstance(mention, BaseMentionResolver), f"{label}: resolver absent before mount"
                assert mention.resolve("@unified:skills") == packaged / "skills"

                async def discovered(event, data):
                    observations.append({
                        "case": label, "session_id": session.session_id,
                        "parent_id": session.parent_id,
                        "skill_names": list(data["skill_names"]),
                        "resolver_at_mount": isinstance(
                            session.coordinator.get_capability("mention_resolver"), BaseMentionResolver),
                    })
                    return HookResult()

                session.coordinator.hooks.register(
                    "skills:discovered", discovered, name="skills-probe-mount-observer")
                if child_check is not None:
                    session.coordinator.register_capability("skills-probe.verify", child_check)
            return before_initialize

        async def exercise():
            nonlocal allowed_command
            # Default discovery, not an explicit reconstruction of the defaults.
            default = prepared(copy.deepcopy(base_config))
            session = await default.create_session(session_cwd=workspace)
            try:
                directory = await controls(session.coordinator)
                await _absent(session.coordinator, "coordinate-work")
                # Invalid YAML and a nonexistent name both surface as not-found;
                # only the invalid source has a discovery diagnostic and a file.
                assert any("Invalid YAML" in record.getMessage() and str(invalid) in record.getMessage()
                           for record in caplog.records)
                assert not (local / "fixture-nonexistent").exists()
                executable = directory / "safe_companion.py"
                await _read_exact(session.coordinator, executable)
                assert not executable.is_symlink() and executable.read_text() == COMPANION
                allowed_command = [sys.executable, "-I", "-S", "-B", str(executable)]
                try:
                    result = subprocess.run(allowed_command, cwd=workspace, capture_output=True,
                                            text=True, timeout=5, check=True)
                finally:
                    allowed_command = None
                assert result.stdout == "SKILLS_COMPANION_OK\n" and result.stderr == ""
            finally:
                await session.cleanup()

            configured = prepared({**base_config, "skills": sources})
            original_plan = copy.deepcopy(configured.mount_plan)
            parent = await configured.create_session(
                session_cwd=workspace, before_initialize=observe_mount("prepared-root"))
            try:
                await controls(parent.coordinator)
                await shipped(parent.coordinator)  # BEFORE the first provider:request.
                await request_boundary(parent.coordinator, includes_unified=True)

                children = []

                async def verify_child(coordinator):
                    children.append(coordinator)
                    assert coordinator is not parent.coordinator
                    await controls(coordinator)
                    await shipped(coordinator)
                    await request_boundary(coordinator, includes_unified=True)
                    await _read_exact(coordinator, control / "safe_companion.py")

                child = await configured.spawn(
                    Bundle(name="skills-child"), "Run only the deterministic skills checks",
                    parent_session=parent, session_cwd=workspace,
                    before_initialize=observe_mount("prepared-child", verify_child),
                )
                assert child["status"] == "success" and child["output"] == "skills-child-verified"
                assert len(children) == 1 and children[0].session_id == child["session_id"]
                assert child["session_id"] != parent.session_id
                assert configured.mount_plan == original_plan
            finally:
                await parent.cleanup()

            # Known namespace, same controls/cache, only the source declaration is
            # omitted. Absence is intentional, not evidence of a broken loader.
            omitted = prepared({**base_config, "skills": sources[:-1]})
            session = await omitted.create_session(
                session_cwd=workspace, before_initialize=observe_mount("source-omitted"))
            try:
                await controls(session.coordinator)
                await _absent(session.coordinator, "coordinate-work")
                await _absent(session.coordinator, "amplifier-shell")
                await request_boundary(session.coordinator, includes_unified=False)
                # The host must not demand an optional skill that this real
                # session just reported absent. Observe its actual injected text.
                from amplifier_web.app_guidance import install_app_access

                async def bridge(operation, args):
                    raise AssertionError("Guidance injection must not dispatch app actions")

                await install_app_access(session.coordinator, bridge)
                guidance = await session.coordinator.hooks.emit("provider:request", {"messages": []})
                assert "search for coordinate-work and load it when found" in guidance.context_injection
                assert "A missing skill is not a coordination failure" in guidance.context_injection
                assert "Preserve selected sources and exclusions" in guidance.context_injection
            finally:
                await session.cleanup()

            # Embedding-host timing control: Core initializes without Foundation's
            # early resolver, then receives a REAL resolver before its first event.
            # Do not patch any loader, coordinator, resolver, or tool implementation.
            session = AmplifierSession(copy.deepcopy(original_plan))
            try:
                await session.coordinator.mount("module-source-resolver", resolver)
                session.coordinator.register_capability("session.working_dir", str(workspace))
                await session.initialize()
                assert session.coordinator.get_capability("mention_resolver") is None
                await controls(session.coordinator)
                await _absent(session.coordinator, "coordinate-work")
                session.coordinator.register_capability("mention_resolver", BaseMentionResolver(
                    base_path=workspace, bundles={"unified": Bundle(name="unified", base_path=packaged)}))
                await request_boundary(session.coordinator, includes_unified=True)
                await shipped(session.coordinator)
                # The one-shot merge must not duplicate or lose the selected local skill.
                await request_boundary(session.coordinator, includes_unified=True)
                await controls(session.coordinator)
            finally:
                await session.cleanup()

        asyncio.run(asyncio.wait_for(exercise(), timeout=60))
        by_case = {row["case"]: row for row in observations}
        assert len(observations) == len(by_case) == 3, observations
        for label in ("prepared-root", "prepared-child"):
            assert by_case[label]["resolver_at_mount"]
            assert {"coordinate-work", "amplifier-shell"} <= set(by_case[label]["skill_names"])
        assert "coordinate-work" not in by_case["source-omitted"]["skill_names"]
        assert by_case["prepared-child"]["parent_id"] == by_case["prepared-root"]["session_id"]
        assert len(admitted_processes) == 1
        # Core must not have replaced any installed package with checkout modules.
        for proof in provenance:
            root = Path(proof["path"])
            for name, module in tuple(sys.modules.items()):
                if name == proof["package"] or name.startswith(proof["package"] + "."):
                    assert Path(module.__file__).resolve().is_relative_to(root), name
        assert not forbidden, f"Forbidden effects, including caught attempts: {forbidden}"
        print("SKILLS_RUNTIME_PROBE " + json.dumps({
            "status": "passed", "installed": provenance, "mounts": observations,
            "cases": ["default-first-wins", "invalid-vs-nonexistent", "safe-companion-executed",
                      "prepared-root", "prepared-child", "source-omitted", "deferred-namespace"],
            "cached_source": "synthetic cache hit; not fetched",
            "model_calls": 0, "forbidden_effects": forbidden,
            "limits": "No full Unified host bootstrap, native delegation, dependency activation, or provider call",
        }, sort_keys=True))
    finally:
        guard_active = False
        assert not forbidden, f"Forbidden effects, including caught attempts: {forbidden}"