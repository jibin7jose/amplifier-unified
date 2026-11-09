"""Generation activation preserves work and shares prepared artifacts."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_runtime_retention import SCRIPT

from amplifier_web.runtime import RuntimeManager
from amplifier_web.updates import foundation_home


@pytest.fixture
async def manager(tmp_path):
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    manager = RuntimeManager(command=[sys.executable, "-c", SCRIPT])
    manager.home = tmp_path / "app"
    manager.home.mkdir()
    manager.retention.wake = lambda: None
    manager.emit = emit
    manager.events = events
    yield manager
    await manager.close()


def promote(manager, value):
    root = manager.home / "updates"
    root.mkdir(exist_ok=True)
    (root / "active.json").write_text(json.dumps({"current": value}))
    manager.promote_generation(value)


async def test_running_work_keeps_generation_idle_adopts_before_send(manager):
    session = {"id": "chat", "workspace": "/tmp"}
    await manager.send(session, "first", "one", manager.emit)
    first = manager.workers["chat"]["process"]
    promote(manager, "a" * 32)
    await manager.retention.sweep()
    assert first.returncode is None
    assert manager.generation_usage()[0]["updatePending"]
    await manager._request("chat", "park")
    # No periodic sweep: admission itself must observe the pending generation.
    assert (await manager.send(session, "second", "two", manager.emit))["accepted"]
    assert first.returncode == 0
    assert manager.workers["chat"]["generation"] == "a" * 32
    assert not manager.generation_usage()[0]["updatePending"]


async def test_active_voice_defers_only_its_worker(manager):
    for sid in ("voice", "idle"):
        await manager.prewarm({"id": sid, "workspace": "/tmp"}, manager.emit)
    voice = manager.workers["voice"]["process"]
    idle = manager.workers["idle"]["process"]
    promote(manager, "b" * 32)
    manager.retention.protected = lambda sid: sid == "voice"
    await manager.retention.sweep()
    assert voice.returncode is None and idle.returncode == 0
    manager.retention.protected = lambda sid: False
    await manager.retention.sweep()
    assert voice.returncode == 0


async def test_lease_exists_until_acknowledged_retirement(manager):
    from amplifier_web.generation_leases import references

    promote(manager, "a" * 32)
    await manager.prewarm({"id": "chat", "workspace": "/tmp"}, manager.emit)
    assert references(manager.home) == {"a" * 32}
    promote(manager, "b" * 32)
    await manager.retention.sweep()
    assert references(manager.home) == set()


def test_pinned_worker_sources_ignore_new_host_pointer(tmp_path, monkeypatch):
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates/active.json").write_text(json.dumps({"current": "b" * 32}))
    monkeypatch.setenv("AMPLIFIER_UNIFIED_RELEASE", "a" * 32)
    assert (
        foundation_home(tmp_path)
        == tmp_path / "updates/releases" / ("a" * 32) / "foundation"
    )
    monkeypatch.setenv("AMPLIFIER_UNIFIED_RELEASE", "")
    assert foundation_home(tmp_path) == tmp_path / "foundation"


async def test_inventory_reuses_index_and_invalidates_on_configuration_change(
    tmp_path, monkeypatch
):
    from amplifier_web.update_inventory_index import inventory

    home = tmp_path / "app"
    (home / "updates").mkdir(parents=True)
    manager = SimpleNamespace(
        home=home,
        directory=home / "updates",
        service=SimpleNamespace(state={"settings": {"bundle": "work"}}),
    )
    scan = AsyncMock(return_value=[{"id": "one"}])
    assert await inventory(manager, scan) == [{"id": "one"}]
    assert await inventory(manager, scan) == [{"id": "one"}]
    assert scan.await_count == 1 and manager.inventory_evidence["source"] == "index"
    shared = Path(os.environ["AMPLIFIER_HOME"])
    shared.mkdir(exist_ok=True)
    (shared / "settings.yaml").write_text("bundle: {active: foundation}")
    await inventory(manager, scan)
    assert scan.await_count == 2
    monkeypatch.setattr("amplifier_web.__version__", "next-fixture-release")
    await inventory(manager, scan)
    assert scan.await_count == 3  # New inventory rules cannot reuse old app results.


def test_offered_profiles_ignore_history_and_behavior_names(tmp_path):
    from amplifier_web.bundles import offered_profiles

    config = SimpleNamespace(
        registry_home=tmp_path,
        registrations={"work": "source", "foundation": "source", "addon": "source"},
        settings={
            "bundle": {"added": {"custom": "file:///bundle"}, "app": []},
            "web_bundles": {
                "entries": [
                    {"name": "foundation", "role": "standalone", "enabled": False}
                ]
            },
        },
    )
    stale = {"work": {}, "old-chat-only": {}, "addon": {}, "amplifier-dev": {}, "exp-delegation": {}}
    (tmp_path / 'registry.json').write_text(json.dumps({'bundles': stale}))
    assert offered_profiles(config, stale) == [
        "custom",
        "work",
    ]
    assert offered_profiles(config) == ["custom", "work"]
    # Namespace/dependency registrations cannot admit retired default profiles.
    # Explicit user standalone additions remain separate; cache stays untouched.
    config.registrations['amplifier-dev'] = 'configured-source'
    assert offered_profiles(config) == ["custom", "work"]
    assert json.loads((tmp_path / 'registry.json').read_text())['bundles'] == stale


@pytest.mark.parametrize(
    "changed,expected",
    [
        ("tools/example/source.json", "data"),
        ("scripts/install.py", "worker"),
        ("bundle.md", "worker"),
        ("legacy-three-roots", "worker"),
    ],
)
async def test_only_proven_catalog_data_bypasses_preparation(
    tmp_path, changed, expected
):
    from amplifier_web.update_plan import CATALOG, build

    home = tmp_path / "app"
    old = "a" * 32
    stage = home / "updates/releases" / ("b" * 32)
    receipt = home / "updates/releases" / old
    before = receipt / "foundation/cache/catalog"
    after = stage / "foundation/cache/catalog"
    for folder in (before, after):
        folder.mkdir(parents=True)
    (home / "updates/active.json").write_text(json.dumps({"current": old}))
    (receipt / "runtime-project.json").write_text("{}")
    (receipt / "runtime-installed.json").write_text("[]")
    from amplifier_web.runtime_profiles import configuration_key

    (receipt / "profiles-qualified.json").write_text(
        json.dumps(
            {
                "profiles": ["anchors", "anchors-amp-dev", "work"] + ([] if changed == "legacy-three-roots" else ["work-amp-dev"]),
                "configuration": configuration_key({}),
            }
        )
    )
    qualified_before = (receipt / "profiles-qualified.json").read_bytes()
    if changed == "legacy-three-roots":
        changed = "tools/example/source.json"

    def git(folder, *args):
        return subprocess.check_output(
            ["git", *args], cwd=folder, text=True, stderr=subprocess.DEVNULL
        ).strip()

    for folder, value in ((before, "{}"), (after, '{"new":true}')):
        git(folder, "init", "-b", "main")
        git(folder, "config", "user.email", "test@example.invalid")
        git(folder, "config", "user.name", "Fixture")
        target = folder / changed
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
        git(folder, "add", ".")
        git(folder, "commit", "-m", "fixture")
    row = {
        "url": CATALOG,
        "ref": "main",
        "kind": "bundle / module",
        "path": "cache/catalog",
    }
    plan = await build(SimpleNamespace(home=home), stage, [row])
    assert plan["mode"] == expected
    assert (receipt / "profiles-qualified.json").read_bytes() == qualified_before


def test_runtime_configuration_key_ignores_credentials_models_and_connection_ids():
    from amplifier_web.runtime_profiles import configuration_key

    def settings(identity, key, model):
        return {
            "config": {
                "providers": [
                    {
                        "module": "provider-openai",
                        "id": identity,
                        "config": {"api_key": key, "default_model": model},
                    }
                ]
            }
        }

    assert configuration_key(settings("one", "old", "first")) == configuration_key(
        settings("two", "new", "second")
    )
    assert configuration_key(
        {"sources": {"modules": {"tool-one": "new"}}}
    ) != configuration_key({})


@pytest.mark.parametrize('bundle', ['work', 'Work', 'WORK'])
async def test_offered_profile_reuses_generation_after_host_pointer_changes(
    tmp_path, monkeypatch, bundle
):
    from amplifier_web import runtime_profiles
    from amplifier_web.host import config as config_module

    home = tmp_path / "app"
    generation = "a" * 32
    receipt = home / "updates/releases" / generation
    receipt.mkdir(parents=True)
    (receipt / "profiles-qualified.json").write_text(
        json.dumps(
            {
                "profiles": ["work"],
                "configuration": runtime_profiles.configuration_key({}),
            }
        )
    )
    captured = {}

    def read(workspace, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            settings={},
            workspace=tmp_path,
            home=home,
            registry_home=kwargs["registry_home"],
            config_home=tmp_path / "shared",
            active_bundle="work",
            registrations={"work": "git+https://example.invalid/work"},
            module_sources={},
            bundle_sources={},
        )

    monkeypatch.setattr(config_module, "read_config", read)
    # A later host promotion cannot silently redirect this admission's registry.
    (home / "updates/active.json").write_text(json.dumps({"current": "b" * 32}))
    assert (
        await runtime_profiles.ensure(
            home, generation, {"id": "chat", "workspace": str(tmp_path), "bundle": bundle}
        )
        == generation
    )
    assert captured["registry_home"] == receipt / "foundation"


@pytest.mark.parametrize("missing_builtin", [False, True])
@pytest.mark.parametrize("caller_policy", [None, " /caller/custom policy.txt "])
async def test_profile_qualification_is_coalesced_and_installed_outside_serving_runtime(
    tmp_path, monkeypatch, missing_builtin, caller_policy
):
    from amplifier_web import (
        runtime_environment,
        runtime_profiles,
        runtime_qualification,
        updates,
    )
    from amplifier_web.host import config as config_module

    home = tmp_path / "app"
    generation = "a" * 32
    receipt = home / "updates/releases" / generation
    (receipt / "foundation").mkdir(parents=True)
    (receipt / "profiles-qualified.json").write_text(
        json.dumps(
            {
                "profiles": ["anchors", "anchors-amp-dev", "work"],
                "configuration": runtime_profiles.configuration_key({}),
            }
        )
    )
    qualified_before = (receipt / "profiles-qualified.json").read_bytes()
    parent = home / "runtime" / ("q-" + "b" * 32)
    parent.mkdir(parents=True)
    (parent / "pyproject.toml").write_text("project = {}")
    (parent / "uv.lock").write_text("version = 1")
    (receipt / "runtime-project.json").write_text(json.dumps({"project": parent.name}))
    shared = tmp_path / "shared"
    shared.mkdir()

    def read(workspace, **kwargs):
        return SimpleNamespace(
            settings={} if missing_builtin else {
                "sources": {
                    "modules": {
                        "tool-example": "git+https://example.invalid/module@main"
                    }
                }
            },
            workspace=tmp_path,
            home=home,
            config_home=shared,
            active_bundle="work",
            registrations={"work": "git+https://example.invalid/work"},
            module_sources={},
            bundle_sources={},
        )

    monkeypatch.setattr(config_module, "read_config", read)
    calls = []
    if caller_policy is None:
        monkeypatch.delenv("UV_OVERRIDE", raising=False)
    else:
        monkeypatch.setenv("UV_OVERRIDE", caller_policy)
    monkeypatch.setenv("UV_CONSTRAINT", "/caller/constraints.txt")
    caller_environment = dict(os.environ)
    probe_environments = []

    async def process(*args, **kwargs):
        calls.append(args)
        if "--install-overrides" in args:
            probe_environments.append(dict(kwargs["env"]))
            assert kwargs["env"].get("UV_OVERRIDE") == caller_policy
            assert kwargs["env"]["UV_CONSTRAINT"] == "/caller/constraints.txt"
        await asyncio.sleep(0.01)
        return ""

    monkeypatch.setattr(updates, "process", process)

    async def overrides(project, path, *, owned_root):
        assert owned_root == home
        path.write_text("")
        return path

    monkeypatch.setattr(runtime_qualification, "prepare_overrides", overrides)

    async def freeze(manager, selected, project):
        final = home / "runtime" / ("q-" + selected)
        final.mkdir()
        target = runtime_environment.receipt_directory(home, selected)
        (target / "runtime-project.json").write_text(
            json.dumps({"project": final.name})
        )
        return final

    monkeypatch.setattr(runtime_qualification, "freeze", freeze)
    monkeypatch.setattr(runtime_qualification, "verify_recorded", lambda *args: None)
    session = {
        "id": "chat",
        "runtimeSessionId": "native-chat",
        "workspace": str(tmp_path),
        "bundle": "work-amp-dev" if missing_builtin else "work",
    }
    runtime_plan = {"changes": []}
    if not missing_builtin:
        projects = tmp_path / "projects"
        environment = projects / "HelloHistory/venv/bin"
        environment.mkdir(parents=True)
        interpreter = tmp_path / "external-python"
        interpreter.write_text("synthetic interpreter")
        (environment / "python3.14").symlink_to(interpreter)
        runtime_plan = {"hooks": [{"module": "hooks-context-intelligence", "config": {
            "include_paths": [str(projects)], "source": str(projects),
            "nested": {"module": "ordinary-data", "source": str(projects)}}}]}
        module = tmp_path / "local-hook"
        module.mkdir()
        code = module / "__init__.py"
        code.write_text("version = 1")
        runtime_plan["hooks"].append({"module": "hooks-local", "source": str(module)})
        edit = home / "sessions/native-chat/configuration.json"
        edit.parent.mkdir(parents=True)
        edit.write_text(json.dumps(runtime_plan))
    progress = []
    async def report(detail):
        progress.append((detail, len(calls)))
    first, second = await asyncio.gather(
        *(runtime_profiles.ensure(home, generation, session, progress=report) for _ in range(2))
    )
    assert first == second and first != generation
    assert len(calls) == 3  # One uv sync, one union install, one read-only mount.
    assert str(parent) not in calls[0]
    assert ("--runtime-plan" in calls[1]) == (not missing_builtin)
    assert ("--runtime-plan" in calls[2]) == (not missing_builtin)
    assert "--read-only" in calls[2]
    assert ('Preparing this chat’s tools…', 0) in progress
    assert ('Checking this chat’s tools…', 1) in progress
    assert ('Verifying this chat’s setup…', 2) in progress
    assert all(str(tmp_path) not in detail for detail, _ in progress)
    if missing_builtin:
        for command in calls[1:]:
            assert command[command.index(str(tmp_path)) + 1] == "work-amp-dev"
        stage = home / "updates/releases" / first
        assert json.loads((stage / "profiles.json").read_text()) == ["work-amp-dev"]
        assert json.loads((stage / "profiles-qualified.json").read_text())["profiles"] == ["work-amp-dev"]
    else:
        assert json.loads(
            (home / "updates/releases" / first / "runtime-plan.json").read_text()
        ) == runtime_plan
    assert (receipt / "profiles-qualified.json").read_bytes() == qualified_before
    assert await runtime_profiles.ensure(home, generation, session) == first
    assert len(calls) == 3
    assert len(probe_environments) == 2
    assert dict(os.environ) == caller_environment
    if not missing_builtin:
        code.write_text("version = 2")
        changed = await runtime_profiles.ensure(home, generation, session)
        assert changed != first  # Actual module edits still invalidate qualification.
        assert len(calls) == 6
        (module / "external-link").symlink_to(interpreter)
        with pytest.raises(ValueError, match="External source symlink"):
            await runtime_profiles.ensure(home, generation, session)
