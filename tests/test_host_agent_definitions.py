"""File-backed agents cross composition, preparation and the native child boundary."""
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from amplifier_foundation import Bundle, BundleRegistry
from amplifier_web.host.session import compose_configured_bundle, _load_agent_definitions


@pytest.fixture(autouse=True)
def private_homes(tmp_path, monkeypatch):
    for key in ("HOME", "AMPLIFIER_HOME", "AMPLIFIER_WEB_HOME", "AMPLIFIER_SESSION_STATE_HOME"):
        monkeypatch.setenv(key, str(tmp_path / key.lower()))


def test_installed_probe_identity():
    """Opt-in managed execution must import installed wheels, never source."""
    if not os.environ.get("UNIFIED_AGENT_INSTALLED_PROBE"):
        return
    import hashlib
    import importlib.metadata
    import sys
    assert sys.flags.isolated
    assert Path("/.dockerenv").exists() or Path("/run/systemd/container").exists()
    identities = {}
    for package, name in (("amplifier_web", "amplifier-unified"),
                          ("amplifier_foundation", "amplifier-foundation"),
                          ("amplifier_core", "amplifier-core")):
        dist = importlib.metadata.distribution(name)
        assert dist.read_text("WHEEL") and dist.read_text("RECORD")
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        assert not direct.get("dir_info", {}).get("editable")
        origin = Path(importlib.util.find_spec(package).origin).resolve(strict=True)
        assert origin == Path(dist.locate_file(package + "/__init__.py")).resolve(strict=True)
        identities[name] = {"version": dist.version, "origin": str(origin),
                            "sha256": hashlib.sha256(origin.read_bytes()).hexdigest()}
    import amplifier_web.host.session as host
    source = Path(__file__).parents[1] / "amplifier_web/host/session.py"
    installed = Path(host.__file__).resolve(strict=True)
    assert installed != source.resolve()
    assert installed.read_bytes() == source.read_bytes()
    identities["sessionSha256"] = hashlib.sha256(installed.read_bytes()).hexdigest()
    print("SPECIALIST_INSTALLED " + json.dumps(identities, sort_keys=True))


@pytest.fixture
def specialist(tmp_path):
    root = tmp_path / "specialists"
    (root / "agents").mkdir(parents=True)
    (root / "behaviors").mkdir()
    (root / "bundle.yaml").write_text("bundle: {name: specialists}\n")
    behavior = root / "behaviors/analysts.yaml"
    behavior.write_text(yaml.safe_dump({
        "bundle": {"name": "specialists", "namespace_root": ".."},
        "agents": {"include": ["specialists:analyst"]},
    }))
    definition = root / "agents/analyst.md"
    definition.write_text("""---
meta:
  description: Fixture specialist
model_role: [reasoning, general]
tools:
  - module: tool-specialist
    source: ./modules/tool-specialist
---
You are the fixture specialist. Keep literal ${EXAMPLE}.
""")
    registry = BundleRegistry(home=tmp_path / "registry", strict=True)
    config = SimpleNamespace(
        app_bundles=[str(behavior)], settings={}, providers=[], module_sources={},
        workspace=tmp_path, home=tmp_path / "app", registry_home=tmp_path / "registry",
        config_home=None, resolve_source=lambda _: None,
    )
    return SimpleNamespace(root=root, behavior=behavior, definition=definition,
                           registry=registry, config=config)


async def compose(fixture, *, agents=None):
    root = Bundle(name="conversation", agents=agents or {})
    return await compose_configured_bundle(fixture.registry, root, fixture.config)


async def test_namespaced_file_loaded_after_behavior_namespace_selection(specialist):
    loaded = await compose(specialist)
    agent = loaded.to_mount_plan()["agents"]["specialists:analyst"]
    assert agent["description"] == "Fixture specialist"
    assert agent["model_role"] == ["reasoning", "general"]
    assert agent["instruction"] == "You are the fixture specialist. Keep literal ${EXAMPLE}."
    assert agent["tools"] == [{"module": "tool-specialist",
                              "source": str(specialist.root / "modules/tool-specialist")}]
    assert not loaded.tools, "Specialist tools must not be mounted in the parent"
    assert loaded.name == "conversation"


@pytest.mark.parametrize("explicit", [
    {"instruction": "", "description": "", "model_role": "", "tools": [], "hooks": [],
     "providers": [], "session": {}, "enabled": False},
    {"instruction": "Inline instructions", "model_role": "fast",
     "tools": [{"module": "tool-inline", "source": "/chosen/source", "enabled": False}]},
])
async def test_inline_fields_including_empty_and_disabled_are_not_filled(specialist, explicit):
    # A later inline overlay still has a resource namespace supplied by the
    # earlier behavior. Foundation's default false-y merge would restore it.
    selected = await specialist.registry.load(str(specialist.behavior))
    selected.agents["specialists:analyst"].update(copy.deepcopy(explicit))
    specialist.config.app_bundles = []
    loaded = await compose_configured_bundle(specialist.registry, selected, specialist.config)
    agent = loaded.agents["specialists:analyst"]
    for key, value in explicit.items():
        assert agent[key] == value


@pytest.mark.parametrize("explicit", [{}, {"instruction": "Inline only", "tools": []}])
async def test_inline_only_agent_does_not_need_a_file(specialist, explicit):
    specialist.config.app_bundles = []
    loaded = await compose(specialist, agents={"inline": explicit})
    assert loaded.agents == {"inline": explicit}


async def test_inline_replacements_are_validated_after_file_defaults(specialist):
    specialist.definition.write_text("---\ntools: invalid\nagents: none\n---\nFile body\n")
    selected = await specialist.registry.load(str(specialist.behavior))
    selected.agents["specialists:analyst"].update(tools=[], agents={})
    specialist.config.app_bundles = []
    loaded = await compose_configured_bundle(specialist.registry, selected, specialist.config)
    assert loaded.agents["specialists:analyst"]["tools"] == []
    assert loaded.agents["specialists:analyst"]["agents"] == {}


@pytest.mark.parametrize("content, message", [
    (None, "Missing file-backed"),
    ("---\nmeta: [broken\n---\nBody", "Could not load"),
    ("---\ntools: invalid\n---\nBody", "Malformed file-backed"),
    ("---\nmeta: {description: ''}\n---\n", "Could not load"),
    ("---\nagents: invalid\n---\nBody", "Could not load"),
])
async def test_missing_and_malformed_file_definitions_refuse_composition(specialist, content, message):
    if content is None:
        specialist.definition.unlink()
    else:
        specialist.definition.write_text(content)
    with pytest.raises(ValueError, match=message + ".*specialists:analyst"):
        await compose(specialist)


async def test_snapshot_never_reads_or_repairs_moving_agent_files(specialist, monkeypatch):
    from amplifier_web.bundles import SNAPSHOT_VERSION
    original = {"specialists:analyst": {
        "name": "saved", "instruction": "Frozen", "model_role": "fast", "tools": []}}
    root = Bundle(name="saved", version=SNAPSHOT_VERSION, agents=copy.deepcopy(original),
                  source_base_paths={"specialists": specialist.root})
    def forbidden(*args):
        raise AssertionError("Snapshot must not resolve or read agent definitions")
    monkeypatch.setattr(Bundle, "load_agent_metadata", forbidden)
    monkeypatch.setattr(Bundle, "resolve_agent_path", forbidden)
    loaded = await compose_configured_bundle(specialist.registry, root, specialist.config)
    assert loaded.agents == original
    # Older incomplete snapshots are not silently repaired either.
    root.agents = {"specialists:analyst": {"name": "specialists:analyst"}}
    loaded = await compose_configured_bundle(specialist.registry, root, specialist.config)
    assert loaded.agents == root.agents


async def test_excluded_behavior_does_not_restore_its_agents(specialist):
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.host.config import HostConfig
    config = HostConfig(specialist.config.home, specialist.config.workspace, {
        "web_bundles": {"excluded": [*DEFAULT_BEHAVIORS, str(specialist.behavior)]},
    }, specialist.config.registry_home)
    assert str(specialist.behavior) not in config.app_bundles
    loaded = await compose_configured_bundle(specialist.registry, Bundle(name="root"), config)
    assert loaded.agents == {}


async def test_selected_namespace_and_module_source_win(specialist, tmp_path):
    from amplifier_web.host.components import HostComponents
    old = tmp_path / "unselected"
    (old / "agents").mkdir(parents=True)
    (old / "agents/analyst.md").write_text("WRONG SOURCE")
    bundle = Bundle(name="root", agents={"specialists:analyst": {"name": "specialists:analyst"}},
                    source_base_paths={"specialists": old})
    components = HostComponents({"tool-specialist": "/explicit/module"})
    components.roots["specialists"] = specialist.root
    components.apply(bundle)
    _load_agent_definitions(bundle)
    components.apply(bundle)
    agent = bundle.agents["specialists:analyst"]
    assert "fixture specialist" in agent["instruction"]
    assert agent["tools"][0]["source"] == "/explicit/module"


async def test_agent_only_module_honors_configured_source_during_preparation(specialist):
    from amplifier_web.host.session import module_source
    specialist.config.module_sources = {"tool-specialist": "/explicit/module"}
    loaded = await compose(specialist)
    row = loaded.agents["specialists:analyst"]["tools"][0]
    assert row["source"] == "/explicit/module"
    assert module_source(specialist.config, False, row["module"], row["source"],
                         loaded._host_components) == "/explicit/module"


async def test_parent_relative_file_source_does_not_follow_final_behavior(specialist, tmp_path):
    specialist.definition.write_text(specialist.definition.read_text().replace(
        "./modules/tool-specialist", "../modules/tool-specialist"))
    other = tmp_path / "other"
    other.mkdir()
    overlay = other / "bundle.yaml"
    overlay.write_text("bundle: {name: other}\n")
    specialist.config.app_bundles.append(str(overlay))
    loaded = await compose(specialist)
    assert loaded.base_path == other
    assert loaded.agents["specialists:analyst"]["tools"][0]["source"] == str(
        tmp_path / "modules/tool-specialist")


async def test_loaded_file_tool_obeys_host_denials_and_child_ceiling(specialist):
    from amplifier_web.host.children import child_plan
    specialist.definition.write_text("""---
tools:
  - module: tool-filesystem
    config:
      allowed_write_paths: [narrow]
      denied_write_paths: [narrow/private]
---
Keep restrictions.
""")
    specialist.config.settings = {"overrides": {"tool-filesystem": {
        "config": {"denied_write_paths": ["narrow/host-private"]}}}}
    loaded = await compose(specialist)
    child = child_plan(loaded.to_mount_plan(), loaded.agents["specialists:analyst"],
                       working_dir=specialist.config.workspace)
    config = child["tools"][0]["config"]
    assert config["allowed_write_paths"] == [str(specialist.config.workspace / "narrow")]
    assert set(config["denied_write_paths"]) == {
        str(specialist.config.workspace / "narrow/private"),
        str(specialist.config.workspace / "narrow/host-private"),
    }


async def test_retained_incomplete_child_overlay_is_not_hydrated(specialist, monkeypatch):
    from unittest.mock import AsyncMock
    from amplifier_web.host.children import Children
    saved_overlay = {"name": "specialists:analyst"}
    store = SimpleNamespace(load=lambda _: ([], {
        "parent_id": "parent", "agent_name": "specialists:analyst",
        "agent_overlay": copy.deepcopy(saved_overlay),
    }))
    children = Children(None, store, None)
    execute = AsyncMock(return_value={"output": "not executed in this boundary test"})
    monkeypatch.setattr(children, "_execute", execute)
    parent = SimpleNamespace(session_id="parent")
    await children.resume("old-child", "new explicit input", parent)
    assert execute.call_args.args[4] == saved_overlay
    assert "tools" not in execute.call_args.args[4]


@pytest.mark.parametrize("selector", ["none", "all", [], ["specialists:analyst"]])
async def test_file_access_selectors_survive_composition_and_descendant_ceiling(specialist, selector):
    from amplifier_web.host.children import child_plan
    from amplifier_web.host.session import live_plan
    from amplifier_web.host.config import configure_skill_cache
    from amplifier_web.provider_environment import iter_provider_rows, rebind_provider_credentials
    specialist.definition.write_text("---\n" + yaml.safe_dump({"agents": selector}) + "---\nSelector body\n")
    loaded = await compose(specialist)
    assert loaded.agents["specialists:analyst"]["agents"] == selector
    configure_skill_cache(loaded, specialist.config.registry_home)
    plan = loaded.to_mount_plan()
    plan["session"] = {"orchestrator": {"module": "loop-live"}}
    live_plan(plan)
    assert list(iter_provider_rows(plan)) == []
    bound, _ = rebind_provider_credentials(plan, plan)
    assert bound["agents"] == plan["agents"]
    child = child_plan(plan, plan["agents"]["specialists:analyst"])
    expected = {} if selector == "none" or selector == [] else plan["agents"]
    assert child["agents"] == expected
    grandchild = child_plan(child, {"agents": {
        "forbidden": {"instruction": "Must not erase an ancestor selector"}},
        "_unified_agent_ceiling": None})
    if selector != "all":
        assert "forbidden" not in grandchild["agents"]
    assert child_plan(child, {"agents": "all"})["agents"] == expected


@pytest.mark.parametrize("file_roster", [False, True])
async def test_nested_dictionary_rosters_use_selected_files_and_overrides(specialist, file_roster, monkeypatch):
    (specialist.root / "agents/deep.md").write_text("""---
model_role: [coding, general]
tools:
  - module: tool-deep
    source: ./modules/tool-deep
---
Deep file instructions.
""")
    nested = {"include": ["specialists:deep"]}
    if file_roster:
        # Foundation's public agent-file loader preserves meta fields, but
        # deliberately does not forward top-level dictionary rosters.
        specialist.definition.write_text("---\n" + yaml.safe_dump({"meta": {
            "agents": nested}}) + "---\nOuter file instructions\n")
    else:
        selected = await specialist.registry.load(str(specialist.behavior))
        selected.agents["specialists:analyst"]["agents"] = nested
        specialist.config.app_bundles = []
    specialist.config.module_sources = {"tool-deep": "/selected/deep"}
    calls = []
    original = Bundle.load_agent_metadata
    def counted(bundle):
        calls.extend(bundle.agents)
        return original(bundle)
    monkeypatch.setattr(Bundle, "load_agent_metadata", counted)
    loaded = await compose(specialist) if file_roster else await compose_configured_bundle(
        specialist.registry, selected, specialist.config)
    deep = loaded.agents["specialists:analyst"]["agents"]["specialists:deep"]
    assert deep["instruction"] == "Deep file instructions."
    assert deep["model_role"] == ["coding", "general"]
    assert deep["tools"][0]["source"] == "/selected/deep"
    assert calls.count("specialists:deep") == 1
    assert not loaded.tools


async def test_nested_cycle_refuses_atomically_with_bounded_file_reads(specialist, monkeypatch):
    specialist.definition.write_text("""---
meta:
  agents:
    include: [specialists:analyst]
---
Recursive file.
""")
    root = Bundle(name="cycle", agents={"specialists:analyst": {"name": "specialists:analyst"}},
                  source_base_paths={"specialists": specialist.root})
    before = copy.deepcopy(root.agents)
    calls = []
    original = Bundle.load_agent_metadata
    def counted(bundle):
        calls.extend(bundle.agents)
        return original(bundle)
    monkeypatch.setattr(Bundle, "load_agent_metadata", counted)
    with pytest.raises(ValueError, match="Cyclic.*specialists:analyst"):
        _load_agent_definitions(root)
    assert calls == ["specialists:analyst"]
    assert root.agents == before


async def test_explicit_terminal_roster_can_end_a_self_reference(specialist):
    specialist.definition.write_text("""---
meta:
  agents:
    specialists:analyst:
      agents: {}
---
Finite self reference.
""")
    loaded = await compose(specialist)
    assert loaded.agents["specialists:analyst"]["agents"]["specialists:analyst"]["agents"] == {}


@pytest.mark.parametrize("file_roster", [False, True])
async def test_nested_inline_sources_keep_file_provenance_not_final_overlay(specialist, tmp_path, file_roster):
    inline = {"helper": {"tools": [{"module": "tool-helper", "source": "./modules/helper"}],
                          "agents": {"deep": {"tools": [{"module": "tool-deep", "source": "../deep"}]}}}}
    if file_roster:
        specialist.definition.write_text("---\n" + yaml.safe_dump({"meta": {"agents": inline}}) + "---\nOuter\n")
    else:
        selected = await specialist.registry.load(str(specialist.behavior))
        selected.agents["specialists:analyst"]["agents"] = inline
        specialist.config.app_bundles = []
    other = tmp_path / "other"
    other.mkdir()
    overlay = other / "bundle.yaml"
    overlay.write_text("bundle: {name: unrelated}\n")
    specialist.config.app_bundles.append(str(overlay))
    loaded = await compose(specialist) if file_roster else await compose_configured_bundle(
        specialist.registry, selected, specialist.config)
    helper = loaded.agents["specialists:analyst"]["agents"]["helper"]
    assert helper["tools"][0]["source"] == (str(specialist.root / "modules/helper") if file_roster else "./modules/helper")
    assert helper["agents"]["deep"]["tools"][0]["source"] == (str(tmp_path / "deep") if file_roster else "../deep")
    assert loaded.base_path == other


async def test_current_ceiling_refuses_spawn_override_and_resume_without_history_mutation(monkeypatch):
    from unittest.mock import AsyncMock
    from amplifier_web.host.children import Children
    saved = ([{"role": "user", "content": "old"}], {
        "parent_id": "parent", "agent_name": "forbidden",
        "agent_overlay": {"instruction": "frozen", "agents": "all"},
    })
    before = copy.deepcopy(saved)
    children = Children(None, SimpleNamespace(load=lambda _: saved), None)
    execute = AsyncMock()
    monkeypatch.setattr(children, "_execute", execute)
    parent = SimpleNamespace(session_id="parent", coordinator=SimpleNamespace(config={
        "_unified_agent_ceiling": ["allowed"], "agents": {"allowed": {}},
    }))
    with pytest.raises(ValueError, match="access ceiling"):
        await children.spawn("forbidden", "new", parent, agent_configs={"forbidden": {}})
    with pytest.raises(ValueError, match="access ceiling"):
        await children.resume("old", "new", parent)
    execute.assert_not_called()
    assert saved == before


async def test_nested_preparation_preserves_provider_failure_attribution_and_plan(tmp_path):
    from amplifier_web.host.components import prepare_bundle
    spec = {"module": "provider-missing-fixture", "source": str(tmp_path / "missing-provider")}
    root = Bundle(name="failures", agents={"outer": {"agents": {
        "deep": {"providers": [spec], "agents": "none"}}}})
    before = root.to_mount_plan()
    failures = []
    async def failed(failure):
        failures.append(failure)
    prepared = await prepare_bundle(root, install_deps=False, strict=True,
                                    cache_dir=tmp_path / "cache", provider_failure_policy=failed)
    assert len(failures) == 1
    assert failures[0].agent_name == "deep" and failures[0].provider_spec == spec
    assert prepared.provider_preparation_failures == tuple(failures)
    assert prepared.mount_plan == before and root.to_mount_plan() == before
    assert not root.providers


TOOL = '''__amplifier_module_type__ = "tool"
from amplifier_core import ToolResult
class Specialist:
    name = "specialist_probe"
    description = "Offline specialist-only positive control"
    input_schema = {"type": "object", "properties": {}}
    async def execute(self, arguments):
        return ToolResult(success=True, output={"specialist": True})
async def mount(coordinator, config):
    await coordinator.mount("tools", Specialist(), name="specialist_probe")
'''

ORCHESTRATOR = '''__amplifier_module_type__ = "orchestrator"
import json
class Probe:
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        assert "fixture specialist" in coordinator.config["instruction"]
        assert coordinator.config["model_role"] == ["reasoning", "general"]
        verdict = await coordinator.approval_system.request_approval(
            "fixture denied effect", ["allow", "deny"], 1, "deny")
        assert verdict == "deny"
        result = await tools["specialist_probe"].execute({})
        assert result.success and result.output == {"specialist": True}
        await context.add_message({"role": "user", "content": prompt})
        await context.add_message({"role": "assistant", "content": "specialist verified"})
        return "specialist verified"
async def mount(coordinator, config):
    await coordinator.mount("orchestrator", Probe())
'''


async def test_real_preparation_and_fresh_native_child_expose_specialist_only_tool(specialist, tmp_path):
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals
    for module, source in (("tool-specialist", TOOL), ("loop-probe", ORCHESTRATOR)):
        folder = specialist.root / "modules" / module
        folder.mkdir(parents=True)
        (folder / "__init__.py").write_text(source)
    loaded = await compose(specialist)
    context = Path(importlib.util.find_spec("amplifier_module_context_simple").origin).parent
    loaded.session = {"orchestrator": {"module": "loop-probe", "source": str(specialist.root / "modules/loop-probe")},
                      "context": {"module": "context-simple", "source": str(context)}}
    # This fixture uses no default forwarding hook or provider. Preparation is
    # real (including the specialist-only module), not a mocked resolver.
    loaded.hooks = []
    prepared = await loaded.prepare(install_deps=False, strict=True, cache_dir=tmp_path / "cache")
    prepared.resolver.resolve("tool-specialist")
    async def deny(*args):
        return "deny"
    approvals = Approvals(None, deny)
    parent = await prepared.create_session(session_id="parent", approval_system=approvals)
    store = SessionStore(tmp_path / "sessions")
    try:
        assert "specialist_probe" not in parent.coordinator.get("tools")
        registry = await install_children(parent, prepared, Runtime("parent"), store, approvals)
        result = await registry.spawn("specialists:analyst", "inspect", parent, sub_session_id="child")
        assert result["output"] == "specialist verified"
        _, metadata = store.load("child")
        assert metadata["agent_overlay"]["model_role"] == ["reasoning", "general"]
        assert "fixture specialist" in metadata["agent_overlay"]["instruction"]
        # Moving files cannot change a complete overlay on resume.
        # Only this new explicit follow-up runs; no past input is replayed.
        specialist.definition.write_text("CHANGED DEFINITION MUST NOT BE READ")
        result = await registry.resume("child", "explicit follow-up", parent)
        assert result["output"] == "specialist verified"
        print("SPECIALIST_PROBE " + json.dumps({
            "preparation": "real", "child": "fresh-and-resumed",
            "parentHasTool": False, "childToolExecuted": True,
            "approvalDenied": True,
            "instructions": "file-backed", "modelRole": ["reasoning", "general"],
        }, sort_keys=True))
    finally:
        await parent.cleanup()


DRIVER = '''__amplifier_module_type__ = "orchestrator"
class Driver:
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        assert "specialist_probe" not in tools
        assert coordinator.config["instruction"] == "Outer instructions."
        deep = coordinator.config["agents"]["specialists:deep"]
        assert "fixture specialist" in deep["instruction"]
        spawn = coordinator.get_capability("session.spawn")
        result = await spawn("specialists:deep", "grandchild probe",
                             coordinator.session, sub_session_id="deep-child")
        assert result["output"] == "specialist verified"
        resume = coordinator.get_capability("session.resume")
        result = await resume("deep-child", "explicit grandchild follow-up")
        assert result["output"] == "specialist verified"
        return "grandchild verified"
async def mount(coordinator, config):
    await coordinator.mount("orchestrator", Driver())
'''


@pytest.mark.parametrize("selector", ["none", "all", [], ["specialists:deep"]])
@pytest.mark.parametrize("batched", [False, True])
async def test_nested_installed_preparation_and_native_grandchild(specialist, tmp_path, selector, batched):
    import hashlib
    from amplifier_foundation.modules.batch import DependencyBatch
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.components import prepare_bundle
    from amplifier_web.host.children import install_children, child_plan
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals
    # Core refuses one module identity loaded from two source roots in a process.
    # These independent fixture sources must therefore have distinct identities.
    suffix = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    tool_id, loop_id, driver_id = (name + "-" + suffix for name in
                                   ("tool-nested", "loop-nested", "loop-driver"))
    deep = specialist.root / "agents/deep.md"
    deep.write_text("---\n" + yaml.safe_dump({
        "model_role": ["reasoning", "general"], "agents": selector,
        "tools": [{"module": tool_id, "source": "./wrong-unselected-module"}],
        "session": {"orchestrator": {"module": loop_id, "source": "./modules/" + loop_id}},
    }) + "---\nYou are the fixture specialist, nested definition.\n")
    specialist.definition.write_text("---\n" + yaml.safe_dump({
        "meta": {"agents": {"include": ["specialists:deep"]}},
    }) + "---\nOuter instructions.\n")
    for module, source in ((tool_id, TOOL), (loop_id, ORCHESTRATOR), (driver_id, DRIVER)):
        folder = specialist.root / "modules" / module
        folder.mkdir(parents=True)
        (folder / "__init__.py").write_text(source)
    selected_source = str(specialist.root / "modules" / tool_id)
    specialist.config.module_sources = {tool_id: selected_source}
    loaded = await compose(specialist)
    context = Path(importlib.util.find_spec("amplifier_module_context_simple").origin).parent
    loaded.session = {"orchestrator": {"module": driver_id, "source": str(specialist.root / "modules" / driver_id)},
                      "context": {"module": "context-simple", "source": str(context)}}
    loaded.hooks = []
    original = loaded.to_mount_plan()
    batch = DependencyBatch() if batched else None
    prepared = await prepare_bundle(loaded, install_deps=False, strict=True, cache_dir=tmp_path / "cache",
                                    **({"dependency_batch": batch} if batch is not None else {}))
    if batch is not None:
        await batch.install()  # Empty, local-module batch: no dependency process.
    assert prepared.bundle is loaded and prepared.mount_plan == original
    assert loaded.to_mount_plan() == original
    assert prepared.resolver.resolve(tool_id).resolve() == Path(selected_source)
    async def deny(*args):
        return "deny"
    approvals = Approvals(None, deny)
    parent = await prepared.create_session(session_id="parent", approval_system=approvals)
    store = SessionStore(tmp_path / "sessions")
    try:
        assert "specialist_probe" not in parent.coordinator.get("tools")
        children = await install_children(parent, prepared, Runtime("parent"), store, approvals)
        result = await children.spawn("specialists:analyst", "drive", parent, sub_session_id="outer-child")
        assert result["output"] == "grandchild verified"
        _, metadata = store.load("deep-child")
        overlay, plan = metadata["agent_overlay"], metadata["mount_plan"]
        assert overlay["agents"] == selector
        assert overlay["model_role"] == ["reasoning", "general"]
        assert "fixture specialist" in overlay["instruction"]
        assert overlay["tools"][0]["source"] == selected_source
        expected = set() if selector == "none" or selector == [] else (
            {"specialists:deep"} if isinstance(selector, list) else {"specialists:analyst", "specialists:deep"})
        assert set(plan["agents"]) == expected
        descendant = child_plan(plan, {"agents": {"blocked": {"instruction": "not authorized"}},
                                       "_unified_agent_ceiling": ["blocked"]})
        if selector != "all":
            assert "blocked" not in descendant["agents"]
        print("NESTED_SPECIALIST_PROBE " + json.dumps({
            "batched": batched, "selector": selector, "grandchildExecuted": True,
            "selectedSource": True, "parentHasTool": False, "approvalDenied": True,
        }, sort_keys=True))
    finally:
        await parent.cleanup()