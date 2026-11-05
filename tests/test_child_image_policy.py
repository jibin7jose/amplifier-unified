"""Image authority remains narrower through real Foundation child composition."""
import asyncio
import copy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

# The worker runtime is installed separately from the application test extras.
# Match the existing child-lifecycle tests when only host dependencies are present.
pytest.importorskip('amplifier_module_loop_live', reason='Requires the standalone worker runtime')

from amplifier_foundation import Bundle
from amplifier_web.host.children import Children, child_plan
from amplifier_web.host.storage import SessionStore


def image(config, **identity):
    return {"module": "tool-image", "source": "fixture-image-source", "config": config, **identity}


@pytest.mark.parametrize("parent_paths,child_paths,expected", [
    (["."], ["images"], ["images"]),
    (["images"], ["."], ["images"]),
    (["images"], ["other"], []),
    (["."], [], []),
    ([], ["images"], []),
    (["images", "other"], ["images/nested", "elsewhere"], ["images/nested"]),
])
@pytest.mark.parametrize("kind", ["read", "write"])
def test_child_image_allow_lists_intersect_in_runtime_workspace(tmp_path, parent_paths, child_paths, expected, kind):
    key = f"allowed_{kind}_paths"
    parent = {"tools": [image({"backend": "images", "allow_paid": True, key: parent_paths})]}
    overlay = {"tools": [image({key: child_paths})]}
    before = copy.deepcopy((parent, overlay))
    plan = child_plan(parent, overlay, working_dir=tmp_path)
    assert plan["tools"][0]["config"][key] == [str(tmp_path / value) for value in expected]
    assert (parent, overlay) == before
    assert plan["tools"][0]["config"]["backend"] == "images"


@pytest.mark.parametrize("parent_paid,child_paid", [(True, False), (False, True), (False, None)])
def test_child_retains_paid_denial_and_both_file_denials(tmp_path, parent_paid, child_paid):
    parent = {"tools": [image({"backend": "images", "allow_paid": parent_paid,
                                "denied_read_paths": ["private"], "denied_write_paths": ["originals"]})]}
    config = {"backend": "child-backend", "denied_read_paths": ["private", "inputs"], "denied_write_paths": []}
    if child_paid is not None:
        config["allow_paid"] = child_paid
    plan = child_plan(parent, {"tools": [image(config)]}, working_dir=tmp_path)
    actual = plan["tools"][0]["config"]
    assert actual["allow_paid"] is False
    assert actual["backend"] == "child-backend"
    assert actual["denied_read_paths"] == [str(tmp_path / "private"), str(tmp_path / "inputs")]
    assert actual["denied_write_paths"] == [str(tmp_path / "originals")]


@pytest.mark.parametrize("value", [None, False, 0, 1, "true", "omitted"])
@pytest.mark.parametrize("boundary", ["parent", "spawn", "child"])
def test_effectively_disabled_image_declaration_cannot_gain_paid_authority(tmp_path, value, boundary):
    disabled = {} if value == "omitted" else {"allow_paid": value}
    parent = {"tools": [image(disabled if boundary == "parent" else {"allow_paid": True})]}
    if boundary == "spawn":
        parent["spawn"] = {"tools": [image(disabled)]}
    overlay = {"tools": [image(disabled if boundary == "child" else {"allow_paid": True})]}
    config = child_plan(parent, overlay, working_dir=tmp_path)["tools"][0]["config"]
    assert config["allow_paid"] is (boundary == "child" and value == "omitted")


def test_spawn_policy_and_new_instance_cannot_escape_parent_image_boundary(tmp_path):
    parent = {"tools": [image({"allow_paid": False, "allowed_write_paths": ["images"]})],
              "spawn": {"tools": [image({"allowed_write_paths": ["images/nested"]}, instance_id="spawn")]}}
    overlay = {"tools": [image({"allow_paid": True, "allowed_write_paths": ["."]}, instance_id="child")]}
    plan = child_plan(parent, overlay, working_dir=tmp_path)
    assert len(plan["tools"]) == 2  # Preserve existing instance composition, constrain both.
    for row in plan["tools"]:
        assert row["config"]["allow_paid"] is False
        assert row["config"]["allowed_write_paths"] == [str(tmp_path / "images/nested")]


@pytest.mark.parametrize("boundary", ["parent", "spawn", "child", "retained", "none"])
@pytest.mark.parametrize("new_instance", [False, True])
def test_image_routing_denial_cannot_be_cleared_at_any_child_boundary(tmp_path, boundary, new_instance):
    declarations = {
        name: image({"allow_paid": True, "routing_blocked": name == boundary, "backend": "legacy"})
        for name in ("parent", "spawn", "child", "retained")
    }
    if new_instance:
        declarations["spawn"]["instance_id"] = "spawn-image"
        declarations["child"]["instance_id"] = "child-image"
    parent = {"tools": [declarations["parent"]], "spawn": {"tools": [declarations["spawn"]]}}
    overlay = {"tools": [declarations["child"]]}
    retained = [declarations["retained"]]
    original = copy.deepcopy((parent, overlay, retained))
    plan = child_plan(parent, overlay, working_dir=tmp_path, retained_image_tools=retained)
    for row in plan["tools"]:
        assert row["config"]["routing_blocked"] is (boundary != "none")
        # Compatibility backends must not evade the denial either.
        assert row["config"]["allow_paid"] is (boundary == "none")
    assert (parent, overlay, retained) == original


def test_excluded_image_is_not_reintroduced_and_other_module_lists_stay_additive(tmp_path):
    parent = {"tools": [image({"allow_paid": True}),
                        {"module": "tool-skills", "config": {"skills": ["existing"]}}]}
    overlay = {"tools": [{"module": "tool-skills", "config": {"skills": ["added"]}}]}
    plan = child_plan(parent, overlay, tool_inheritance={"exclude_tools": ["tool-image"]}, working_dir=tmp_path)
    assert plan["tools"] == [{"module": "tool-skills", "config": {"skills": ["existing", "added"]}}]


@pytest.mark.parametrize("boundary", ["spawn", "tool_inheritance"])
@pytest.mark.parametrize("excluded_name", ["tool-image", "image_generate", "parent-image"])
def test_inheritance_filter_does_not_erase_paid_denial_on_explicit_child_image(
        tmp_path, boundary, excluded_name):
    parent = {
        "tools": [
            image({"allow_paid": False}, instance_id="parent-image"),
            {"module": "tool-skills", "config": {"skills": ["existing"]}},
        ]
    }
    overlay = {"tools": [
        {"module": "tool-skills", "config": {"skills": ["added"]}},
        image({"allow_paid": True}, instance_id="child-image"),
    ]}
    policy = {"exclude_tools": [excluded_name]}
    if boundary == "spawn":
        parent["spawn"] = copy.deepcopy(policy)
    original = copy.deepcopy((parent, overlay, policy))
    plan = child_plan(
        parent, overlay, working_dir=tmp_path,
        tool_inheritance=policy if boundary == "tool_inheritance" else None,
    )
    # Inheritance filtering is not an irrevocable ban on explicit declarations.
    # Paid denial is a separate boundary and cannot disappear with that filter.
    images = [row for row in plan["tools"] if row.get("module") == "tool-image"]
    assert any(row.get("instance_id") == "child-image" for row in images)
    assert all(row["config"]["allow_paid"] is False for row in images)
    assert [row for row in plan["tools"] if row.get("module") != "tool-image"] == [
        {"module": "tool-skills", "config": {"skills": ["existing", "added"]}}
    ]
    assert (parent, overlay, policy) == original


def test_unexcluded_image_redeclaration_keeps_existing_child_composition(tmp_path):
    parent = {"tools": [image({"allow_paid": True}, instance_id="parent-image")]}
    overlay = {"tools": [image({"allow_paid": True}, instance_id="child-image")]}
    original = copy.deepcopy((parent, overlay))
    plan = child_plan(parent, overlay, working_dir=tmp_path)
    assert {row["instance_id"] for row in plan["tools"]} == {"parent-image", "child-image"}
    assert all(row["config"]["allow_paid"] is True for row in plan["tools"])
    assert (parent, overlay) == original


@pytest.mark.parametrize("allowed", [[], ["images"]])
def test_inherited_image_obeys_child_filesystem_policy_without_image_redeclaration(tmp_path, allowed):
    parent = {"tools": [image({"backend": "images", "allow_paid": True,
                                "allowed_write_paths": ["."], "denied_write_paths": ["private"]})]}
    overlay = {"tools": [{"module": "tool-filesystem", "config": {
        "allowed_write_paths": allowed, "denied_write_paths": ["images/locked"]}}]}
    plan = child_plan(parent, overlay, working_dir=tmp_path)
    actual = plan["tools"][0]["config"]
    assert actual["allowed_write_paths"] == [str(tmp_path / path) for path in allowed]
    assert actual["denied_write_paths"] == [str(tmp_path / "private"), str(tmp_path / "images/locked")]
    assert actual["allow_paid"] is True


@pytest.mark.parametrize("invalid", ["images", [None], {"path": "images"}])
def test_malformed_child_image_path_policy_fails_before_mount(tmp_path, invalid):
    with pytest.raises(ValueError, match="lists of strings"):
        child_plan({"tools": [image({"allowed_read_paths": ["."]})]},
                   {"tools": [image({"allowed_read_paths": invalid})]}, working_dir=tmp_path)


def test_child_image_environment_paths_resolve_before_intersection(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_CHILD_TEST_DIR", "images")
    plan = child_plan({"tools": [image({"allowed_read_paths": [str(tmp_path)]})]},
                      {"tools": [image({"allowed_read_paths": ["${IMAGE_CHILD_TEST_DIR}"]})]}, working_dir=tmp_path)
    assert plan["tools"][0]["config"]["allowed_read_paths"] == [str(tmp_path / "images")]


@pytest.mark.parametrize("omission", ["inheritance", "spawn", "retained"])
@pytest.mark.parametrize("restriction,value", [
    ("allow_paid", False), ("routing_blocked", True),
    ("allowed_read_paths", []), ("allowed_write_paths", ["images"]),
    ("denied_read_paths", ["private"]), ("denied_write_paths", ["originals"]),
])
def test_image_free_intermediate_keeps_ancestor_restriction(tmp_path, omission, restriction, value):
    parent = {"tools": [image({"allow_paid": True, restriction: value})]}
    options = {"working_dir": tmp_path}
    if omission == "inheritance":
        options["tool_inheritance"] = {"exclude_tools": ["tool-image"]}
    elif omission == "spawn":
        parent["spawn"] = {"tools": []}
    else:
        options["retained_image_tools"] = []
    original = copy.deepcopy(parent)
    middle = child_plan(parent, {}, **options)
    assert middle["tools"] == []
    saved_middle = copy.deepcopy(middle)
    grandchild = child_plan(middle, {
        # An explicit tool is allowed, but neither a new identity nor an
        # attempted replacement of the internal ceiling can widen authority.
        "_unified_image_policy": {},
        "spawn": {},
        "tools": [image({"allow_paid": True, "routing_blocked": False,
                        "allowed_read_paths": [str(tmp_path)], "allowed_write_paths": [str(tmp_path)],
                        "denied_read_paths": [], "denied_write_paths": [],
                        "backend": "explicit-child-backend"}, instance_id="grandchild-image")],
    }, working_dir=tmp_path / "different-cwd")
    actual, = grandchild["tools"]
    expected = [str(tmp_path / path) for path in value] if isinstance(value, list) else value
    assert actual["config"][restriction] == expected
    if restriction == "routing_blocked":
        assert actual["config"]["allow_paid"] is False
    assert actual["config"]["backend"] == "explicit-child-backend"
    assert parent == original and middle == saved_middle


def test_image_free_intermediate_without_restrictions_does_not_ban_redeclaration(tmp_path):
    middle = child_plan({"tools": [image({"allow_paid": True})]}, {},
                        tool_inheritance={"exclude_tools": ["tool-image"]}, working_dir=tmp_path)
    assert middle["tools"] == []
    grandchild = child_plan(middle, {"tools": [image({"allow_paid": True})]}, working_dir=tmp_path)
    assert grandchild["tools"][0]["config"]["allow_paid"] is True
    assert not grandchild["tools"][0]["config"].get("routing_blocked")


def test_image_ceiling_never_carries_paid_grants_into_new_declarations(tmp_path):
    parent = {"tools": [image({"allow_paid": True})]}
    partial = child_plan(parent, {"tools": [image({"model_role": "other-role"})]}, working_dir=tmp_path)
    assert partial["tools"][0]["config"]["allow_paid"] is True
    middle = child_plan(parent, {}, tool_inheritance={"exclude_tools": ["tool-image"]}, working_dir=tmp_path)
    assert "allow_paid" not in middle["_unified_image_policy"]
    new = child_plan(middle, {"tools": [image({"model_role": "other-role"})]}, working_dir=tmp_path)
    # The mounted tool requires literal True; an omitted new grant stays omitted.
    assert new["tools"][0]["config"].get("allow_paid") is not True


@pytest.mark.parametrize("invalid", [None, [], "disabled"])
def test_malformed_inherited_image_ceiling_fails_closed(tmp_path, invalid):
    with pytest.raises(ValueError, match="image policy must be a dictionary"):
        child_plan({"tools": [], "_unified_image_policy": invalid},
                   {"tools": [image({"allow_paid": True})]}, working_dir=tmp_path)


@pytest.mark.parametrize("boundary", ["parent", "retained"])
@pytest.mark.parametrize("policy", [{"allow_paid": 0}, {"allow_paid": None}, {"routing_blocked": "true"}])
def test_malformed_image_ceiling_flags_cannot_disappear(tmp_path, boundary, policy):
    parent = {"tools": []}
    options = {}
    if boundary == "parent":
        parent["_unified_image_policy"] = policy
    else:
        options["retained_image_policy"] = policy
    with pytest.raises(ValueError, match="flags must be booleans"):
        child_plan(parent, {"tools": [image({"allow_paid": True})]}, working_dir=tmp_path, **options)


def test_image_ceiling_contains_restrictions_not_selection_or_credentials(tmp_path):
    middle = child_plan({"tools": [image({
        "allow_paid": False, "allowed_write_paths": ["images"],
        "backend": "parent-account", "model_role": "image-gen", "model": "parent-model",
        "provider_preferences": [{"provider": "account-a"}], "api_key": "not-policy",
    })]}, {}, tool_inheritance={"exclude_tools": ["tool-image"]}, working_dir=tmp_path)
    assert middle["_unified_image_policy"] == {
        "allow_paid": False, "allowed_write_paths": [str(tmp_path / "images")]}
    sibling = child_plan(middle, {"tools": [image({"allow_paid": True})]}, working_dir=tmp_path)
    sibling["_unified_image_policy"]["allowed_write_paths"].clear()
    assert middle["_unified_image_policy"]["allowed_write_paths"] == [str(tmp_path / "images")]


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize("current_scope", ["omitted", "broad", "narrow"])
async def test_image_free_native_child_checkpoint_and_resume_retains_ceiling(tmp_path, restricted, current_scope):
    """Real Foundation/Core config and checkpoint; no image mount or provider call.

    A finite test orchestrator drives the public nested spawn boundary. The
    grandchild is inspected before mounting the unavailable fixture image tool.
    """
    import importlib.util
    from amplifier_foundation.bundle import PreparedBundle, BundleModuleResolver

    workspace = tmp_path / "execution"
    workspace.mkdir()
    session_config = {"orchestrator": {"module": "loop-live"},
                      "context": {"module": "context-simple"}}
    config = {"allow_paid": not restricted, "backend": "parent-account"}
    expected = {}
    if restricted:
        config.update(routing_blocked=True, allowed_read_paths=[],
                      allowed_write_paths=["images"], denied_read_paths=["private"],
                      denied_write_paths=["originals"])
        expected = {"allow_paid": False, "routing_blocked": True, "allowed_read_paths": [],
                    "allowed_write_paths": [str(workspace / "images")],
                    "denied_read_paths": [str(workspace / "private")],
                    "denied_write_paths": [str(workspace / "originals")]}
    parent_plan = {"session": session_config, "tools": [image(config)]}
    initial_parent = copy.deepcopy(parent_plan)
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    resolver = BundleModuleResolver(paths)
    mounted, grandchildren = [], []

    class StopBeforeImageMount(Exception):
        pass

    class Finite:
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            assert not providers
            assert not any(row.get("module") == "tool-image" for row in coordinator.config["tools"])
            assert coordinator.config["_unified_image_policy"] == expected
            child = mounted[-1]
            with pytest.raises(StopBeforeImageMount):
                await coordinator.get_capability("session.spawn")(
                    agent_name="image-worker", instruction="must not dispatch", parent_session=child,
                    sub_session_id="grandchild-" + prompt,
                    agent_configs={"image-worker": {"_unified_image_policy": {},
                        "tools": [image({"allow_paid": True, "routing_blocked": False,
                                        "allowed_read_paths": [str(workspace)],
                                        "allowed_write_paths": [str(workspace)],
                                        "denied_read_paths": [], "denied_write_paths": [],
                                        "backend": "explicit-grandchild"}, instance_id="new-image")]}})
            await context.add_message({"role": "user", "content": prompt})
            await context.add_message({"role": "assistant", "content": "done " + prompt})
            return "done " + prompt

    @dataclass
    class Prepared(PreparedBundle):
        async def create_session(self, **kwargs):
            if kwargs["session_id"].startswith("grandchild-"):
                grandchildren.append(copy.deepcopy((self.mount_plan, self.bundle.tools)))
                raise StopBeforeImageMount
            assert self.mount_plan["tools"] == self.bundle.tools == []
            child = await super().create_session(**kwargs)
            assert child.coordinator.config["_unified_image_policy"] == expected
            await child.coordinator.mount("orchestrator", Finite())
            mounted.append(child)
            return child

    coordinator = SimpleNamespace(config=parent_plan, get=lambda _: None,
        get_capability=lambda name: str(workspace) if name == "session.working_dir" else None)
    parent = SimpleNamespace(session_id="parent", coordinator=coordinator)
    store = SessionStore(tmp_path / "history")
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), store, None)
    children.prepared["parent"] = Prepared(parent_plan, resolver, Bundle(
        name="parent", session=session_config, tools=copy.deepcopy(parent_plan["tools"])))
    await children.spawn("self", "first", parent, sub_session_id="middle",
                         tool_inheritance={"exclude_tools": ["tool-image"]})
    messages, saved = store.load("middle")
    assert saved["mount_plan"]["tools"] == []
    assert saved["mount_plan"]["_unified_image_policy"] == expected
    assert parent_plan == initial_parent
    # Lose every original declaration in the current parent. Resume must use
    # the saved restriction-only ceiling, not manufacture a tool or a grant.
    current = {"session": copy.deepcopy(session_config), "tools": []}
    if current_scope != "omitted":
        current_config = {"allow_paid": True, "routing_blocked": False,
                          "allowed_read_paths": [str(workspace)],
                          "allowed_write_paths": [str(workspace)],
                          "denied_read_paths": [], "denied_write_paths": []}
        if current_scope == "narrow":
            current_config.update(allow_paid=False, allowed_write_paths=[str(workspace / "images/nested")],
                                  denied_write_paths=[str(workspace / "images/current-deny")])
        current["tools"] = [image(current_config)]
        expected = {
            "allowed_read_paths": [] if restricted else [str(workspace)],
            "allowed_write_paths": [str(workspace / (
                "images/nested" if current_scope == "narrow" else "images"))] if restricted
                or current_scope == "narrow" else [str(workspace)],
            "denied_read_paths": [str(workspace / "private")] if restricted else [],
            "denied_write_paths": ([str(workspace / "originals")] if restricted else [])
                                  + ([str(workspace / "images/current-deny")] if current_scope == "narrow" else []),
            **({"allow_paid": False} if restricted or current_scope == "narrow" else {}),
            **({"routing_blocked": True} if restricted else {}),
        }
    before_current = copy.deepcopy(current)
    parent.coordinator.config = current
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), SessionStore(tmp_path / "history"), None)
    children.prepared["parent"] = Prepared(current, resolver, Bundle(
        name="current", session=session_config, tools=copy.deepcopy(current["tools"])))
    await children.resume("middle", "again", parent)
    resumed_messages, resumed = children.store.load("middle")
    assert resumed_messages[:len(messages)] == messages
    assert resumed_messages[-1]["content"] == "done again"
    assert resumed["mount_plan"]["tools"] == []
    assert resumed["mount_plan"]["_unified_image_policy"] == expected
    assert len(mounted) == len(grandchildren) == 2
    for index, (plan, bundle_tools) in enumerate(grandchildren):
        assert plan["tools"] == bundle_tools
        row, = bundle_tools
        assert row["config"]["backend"] == "explicit-grandchild"
        effective = saved["mount_plan"]["_unified_image_policy"] if index == 0 else expected
        for key, value in effective.items():
            assert row["config"][key] == value
        assert row["config"]["allow_paid"] is (not restricted and not (index == 1 and current_scope == "narrow"))
    assert current == before_current
    assert not children.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_prepared_child_mount_and_bundle_share_narrowed_policy_before_execution(tmp_path, monkeypatch, resumed):
    workspace = tmp_path / "execution"
    workspace.mkdir()
    elsewhere = tmp_path / "server"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    captured = {}

    class StopBeforeMount(Exception):
        pass

    @dataclass
    class Prepared:
        bundle: Bundle
        mount_plan: dict

        async def create_session(self, **kwargs):
            captured.update(plan=self.mount_plan, tools=self.bundle.tools, kwargs=kwargs)
            raise StopBeforeMount

    parent_plan = {"tools": [image({"backend": "images", "allow_paid": True,
                                     "allowed_read_paths": [str(workspace)],
                                     "allowed_write_paths": [str(workspace)],
                                     "denied_write_paths": [str(workspace / "private")]})]}
    coordinator = SimpleNamespace(config=parent_plan, get=lambda _: None,
        get_capability=lambda name: str(workspace) if name == "session.working_dir" else None)
    parent = SimpleNamespace(session_id="parent", coordinator=coordinator)
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), SessionStore(tmp_path / "history"), None)
    children.prepared["parent"] = Prepared(Bundle(name="parent", tools=copy.deepcopy(parent_plan["tools"])), parent_plan)
    overlay = {"tools": [image({"allow_paid": False, "allowed_read_paths": [],
                                "allowed_write_paths": ["images"], "denied_write_paths": ["images/no"]})]}
    with pytest.raises(StopBeforeMount):
        await children._execute("child", "must not execute", parent, "worker", overlay, resumed=resumed)
    mounted = captured["plan"]["tools"][0]["config"]
    assert mounted["allowed_write_paths"] == [str(workspace / "images")]
    assert mounted["allowed_read_paths"] == []
    assert mounted["denied_write_paths"] == [str(workspace / "private"), str(workspace / "images/no")]
    assert mounted["allow_paid"] is False
    assert captured["tools"] == captured["plan"]["tools"]
    assert captured["kwargs"]["session_cwd"] == workspace
    assert captured["kwargs"]["is_resumed"] is resumed
    assert not children.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_scope", ["absent", "restricted", "broad", "omitted-paid"])
async def test_actual_resume_preserves_saved_image_authority_and_current_parent_limits(tmp_path, saved_scope):
    workspace = tmp_path / "execution"
    workspace.mkdir()
    captured = {}

    class StopBeforeMount(Exception):
        pass

    @dataclass
    class Prepared:
        bundle: Bundle
        mount_plan: dict

        async def create_session(self, **kwargs):
            captured.update(plan=self.mount_plan, tools=self.bundle.tools, kwargs=kwargs)
            raise StopBeforeMount

    parent_plan = {"tools": [image({"backend": "images", "allow_paid": True,
                                     "allowed_write_paths": [str(workspace / "images")],
                                     "denied_write_paths": [str(workspace / "images/private")]})]}
    coordinator = SimpleNamespace(config=parent_plan, get=lambda _: None,
        get_capability=lambda name: str(workspace) if name == "session.working_dir" else None)
    parent = SimpleNamespace(session_id="parent", coordinator=coordinator)
    store = SessionStore(tmp_path / "history")
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), store, None)
    children.prepared["parent"] = Prepared(Bundle(name="parent", tools=copy.deepcopy(parent_plan["tools"])), parent_plan)
    saved_config = {"backend": "images", "allow_paid": False,
                    "allowed_write_paths": [str(workspace / "images/retained")],
                    "denied_write_paths": [str(workspace / "images/retained/originals")]}
    if saved_scope == "broad":
        saved_config.update(allow_paid=True, allowed_write_paths=[str(workspace)])
    elif saved_scope == "omitted-paid":
        saved_config.pop("allow_paid")
    saved_tools = [] if saved_scope == "absent" else [image(saved_config)]
    # An excluded image stays absent even with an empty saved overlay. An old
    # permissive overlay must also retain the recorded effective restrictions.
    store.save("child", [{"role": "user", "content": "Retained original"}], {
        "parent_id": "parent", "agent_name": "worker",
        "agent_overlay": {} if saved_scope == "absent" else {
            "tools": [image({"allow_paid": True, "allowed_write_paths": ["."]})]},
        "mount_plan": {"tools": saved_tools}})
    original = {path: path.read_bytes() for path in (tmp_path / "history").rglob("*") if path.is_file()}
    with pytest.raises(StopBeforeMount):
        await children.resume("child", "must not execute", parent)
    image_rows = [row for row in captured["plan"]["tools"] if row["module"] == "tool-image"]
    if saved_scope == "absent":
        assert image_rows == []
    else:
        config = image_rows[0]["config"]
        assert config["allowed_write_paths"] == [str(workspace / ("images" if saved_scope == "broad" else "images/retained"))]
        assert config["allow_paid"] is (saved_scope == "broad")
        assert set(config["denied_write_paths"]) == {str(workspace / "images/private"), str(workspace / "images/retained/originals")}
    assert [row for row in captured["tools"] if row["module"] == "tool-image"] == image_rows
    assert captured["kwargs"]["is_resumed"] is True
    assert original == {path: path.read_bytes() for path in (tmp_path / "history").rglob("*") if path.is_file()}


def legacy_image_plan():
    return {
        "tools": [image({"backend": "images-a", "allow_paid": True})],
        "providers": [{
            "module": "provider-openai", "instance_id": "account-a", "source": "fixture-provider-source",
            "config": {
                "api_key": "synthetic-old-credential", "default_model": "chat-model",
                "base_url": "https://api.example.invalid/v1", "project": "project-a",
                "image_generation": {"enabled": True, "id": "images-a", "model": "image-model-a"},
            },
        }],
    }


@pytest.fixture
def resume_at_image_boundary(tmp_path):
    """Exercise public resume and actual plan composition, stopping before mount."""
    async def resume(saved_plan, current_plan, *, overlay=None, error=None, preferences=None, isolation=False):
        captured = {}

        class StopBeforeMount(Exception):
            pass

        @dataclass
        class Prepared:
            bundle: Bundle
            mount_plan: dict
            resolver: object = None

            async def create_session(self, **kwargs):
                captured.update(plan=self.mount_plan, tools=self.bundle.tools,
                                resolver=self.resolver, kwargs=kwargs)
                raise StopBeforeMount

        original_plans = copy.deepcopy((saved_plan, current_plan, overlay))
        capabilities = {"session.working_dir": str(tmp_path)}
        if isolation:
            capabilities["web.provider_isolation"] = SimpleNamespace(preparation={})
        coordinator = SimpleNamespace(config=current_plan, get=lambda _: None,
            get_capability=capabilities.get)
        parent = SimpleNamespace(session_id="parent", coordinator=coordinator)
        store = SessionStore(tmp_path / "history")
        children = Children(SimpleNamespace(inbox=asyncio.Queue()), store, None)
        children.prepared["parent"] = Prepared(Bundle(
            name="parent", tools=copy.deepcopy(current_plan.get("tools", [])),
            providers=copy.deepcopy(current_plan.get("providers", []))), current_plan)
        store.save("child", [{"role": "user", "content": "Retained image work"}], {
            "parent_id": "parent", "agent_name": "worker", "agent_overlay": overlay or {},
            "mount_plan": saved_plan,
        })
        original_files = {path: path.read_bytes() for path in (tmp_path / "history").rglob("*") if path.is_file()}
        if error:
            with pytest.raises(ValueError, match=error):
                await children.resume("child", "must not dispatch", parent, provider_preferences=preferences)
            assert captured == {}
            assert children.rows == {}  # Refusal precedes session creation/events.
        else:
            with pytest.raises(StopBeforeMount):
                await children.resume("child", "must not dispatch", parent, provider_preferences=preferences)
            assert [row for row in captured["tools"] if row["module"] == "tool-image"] == [
                row for row in captured["plan"]["tools"] if row["module"] == "tool-image"
            ]
            assert captured["kwargs"]["is_resumed"] is True
        assert not children.sessions
        assert (saved_plan, current_plan, overlay) == original_plans
        assert original_files == {
            path: path.read_bytes() for path in (tmp_path / "history").rglob("*") if path.is_file()
        }
        return captured

    return resume


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_resume_compares_materialized_late_image_provider_binding(
        resume_at_image_boundary, monkeypatch, changed):
    async def schema(*args, **kwargs):
        return {"fields": []}

    monkeypatch.setattr("amplifier_web.provider_environment.provider_config_schema", schema)
    saved = legacy_image_plan()
    current = copy.deepcopy(saved)
    current["providers"][0]["config"]["base_url"] = "${FIXTURE_IMAGE_ENDPOINT}"
    current["providers"][0]["config"]["api_key"] = "synthetic-current-credential"
    endpoint = "https://other.example.invalid/v1" if changed else saved["providers"][0]["config"]["base_url"]
    monkeypatch.setenv("FIXTURE_IMAGE_ENDPOINT", endpoint)
    result = await resume_at_image_boundary(
        saved, current, isolation=True,
        error="Saved child image provider binding has changed" if changed else None,
    )
    if not changed:
        assert result["plan"]["providers"][0]["config"]["base_url"] == endpoint
        assert result["plan"]["providers"][0]["config"]["api_key"] == "synthetic-current-credential"


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [
    ("backend", "images-b"),
    ("backend", None),
    ("model_role", "another-image-role"),
    ("model_role", None),
    ("provider_preferences", [{"provider": "account-b", "model": "image-model-b"}]),
    ("provider_preferences", None),
    ("model", "builtin-image-model-b"),
])
async def test_resume_refuses_changed_or_lost_saved_image_selection(resume_at_image_boundary, key, value):
    saved = legacy_image_plan()
    config = saved["tools"][0]["config"]
    if key in {"model_role", "provider_preferences"}:
        config.pop("backend")
        config.update(model_role="saved-specialist-role",
                      provider_preferences=[{"provider": "account-a", "model": "image-model-a"}])
    elif key == "model":
        config.update(builtin="gptimage", model="builtin-image-model-a")
    current = copy.deepcopy(saved)
    if value is None:
        current["tools"][0]["config"].pop(key)
    else:
        current["tools"][0]["config"][key] = value
    await resume_at_image_boundary(saved, current, error="Saved child image selection has changed")


@pytest.mark.asyncio
@pytest.mark.parametrize("path,value", [
    (("config", "image_generation", "model"), "image-model-b"),
    (("config", "image_generation", "id"), "images-b"),
    (("config", "image_generation", "enabled"), False),
    (("instance_id",), "account-b"),
    (("source",), "other-provider-source"),
    (("enabled",), False),
    (("config", "base_url"), "https://other.example.invalid/v1"),
    (("config", "project"), "project-b"),
    (("config", "default_headers"), {"OpenAI-Organization": "organization-b"}),
])
async def test_resume_refuses_changed_legacy_image_model_or_account_binding(resume_at_image_boundary, path, value):
    saved = legacy_image_plan()
    current = copy.deepcopy(saved)
    target = current["providers"][0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    await resume_at_image_boundary(saved, current, error="Saved child image provider binding has changed")


@pytest.mark.asyncio
async def test_resume_does_not_restore_a_removed_legacy_provider(resume_at_image_boundary):
    saved = legacy_image_plan()
    current = copy.deepcopy(saved)
    current["providers"] = []
    await resume_at_image_boundary(saved, current, error="Saved child image provider binding has changed")


@pytest.mark.asyncio
async def test_saved_image_overlay_can_resume_exact_selection_with_unchanged_binding(resume_at_image_boundary):
    saved = legacy_image_plan()
    current = copy.deepcopy(saved)
    current["tools"][0]["config"]["backend"] = "images-b"
    overlay = {"tools": [image({"backend": "images-a"})]}
    captured = await resume_at_image_boundary(saved, current, overlay=overlay)
    assert captured["plan"]["tools"][0]["config"]["backend"] == "images-a"
    assert captured["plan"]["tools"][0]["config"]["allow_paid"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "routed", "builtin"])
async def test_unchanged_image_resume_uses_current_credentials_without_restoring_saved_plan(
        resume_at_image_boundary, mode):
    saved = legacy_image_plan()
    if mode == "routed":
        saved["tools"][0]["config"] = {
            "allow_paid": True, "model_role": "image-gen",
            "provider_preferences": [{"provider": "account-a", "model": "image-model-a"}],
        }
    elif mode == "builtin":
        saved["tools"][0]["config"].update(builtin="gptimage", model="image-model-a")
        saved["providers"] = []
    saved["session"] = {"saved_only": "must not be restored"}
    current = copy.deepcopy(saved)
    current["session"] = {"current_only": "retain current authority"}
    current["tools"].append({"module": "tool-filesystem", "config": {"allowed_write_paths": []}})
    if current["providers"]:
        current["providers"][0]["config"]["api_key"] = "synthetic-current-credential"
        # The composer supplies the equivalent Core/Foundation alias on resume.
        current["providers"][0]["id"] = "account-a"
    captured = await resume_at_image_boundary(saved, current)
    assert captured["plan"]["session"] == current["session"]
    assert captured["plan"]["tools"][0]["config"]["allowed_write_paths"] == []
    assert captured["plan"]["tools"][0]["config"]["allow_paid"] is True
    if current["providers"]:
        config = captured["plan"]["providers"][0]["config"]
        assert config["api_key"] == "synthetic-current-credential"
        assert config["image_generation"] == saved["providers"][0]["config"]["image_generation"]
    for key in ("backend", "model_role", "provider_preferences", "builtin", "model"):
        assert captured["plan"]["tools"][0]["config"].get(key) == saved["tools"][0]["config"].get(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["allow_paid", "routing_blocked"])
async def test_resume_keeps_retained_image_off_even_when_overlay_clears_it(resume_at_image_boundary, denial):
    saved = legacy_image_plan()
    saved["tools"][0]["config"][denial] = denial == "routing_blocked"
    current = legacy_image_plan()
    overlay = {"tools": [image({"allow_paid": True, "routing_blocked": False})]}
    captured = await resume_at_image_boundary(saved, current, overlay=overlay)
    config = captured["plan"]["tools"][0]["config"]
    assert config["allow_paid"] is False
    if denial == "routing_blocked":
        assert config["routing_blocked"] is True


@pytest.mark.asyncio
async def test_legacy_image_resume_ignores_unrelated_chat_provider_changes(resume_at_image_boundary):
    saved = legacy_image_plan()
    saved["providers"].append({
        "module": "provider-mock", "source": "fixture-chat-source", "config": {"default_model": "chat-a"},
    })
    current = copy.deepcopy(saved)
    current["providers"][1]["config"]["default_model"] = "chat-b"
    captured = await resume_at_image_boundary(saved, current)
    assert captured["plan"]["providers"][1]["config"]["default_model"] == "chat-b"


@pytest.mark.asyncio
async def test_resume_checks_image_binding_after_provider_preference_resolution(resume_at_image_boundary, monkeypatch):
    calls = []

    async def resolve(plan, preferences, coordinator):
        calls.append(preferences)
        result = copy.deepcopy(plan)
        result["providers"][0]["config"]["image_generation"]["model"] = "resolution-changed-image"
        return result

    monkeypatch.setattr("amplifier_foundation.apply_provider_preferences_with_resolution", resolve)
    saved = legacy_image_plan()
    await resume_at_image_boundary(saved, copy.deepcopy(saved),
        preferences=[{"provider": "account-a", "model": "chat-model"}],
        error="Saved child image provider binding has changed")
    assert len(calls) == 1


@pytest.fixture
def routed_image_decision_plans(monkeypatch):
    """Synthetic package metadata; projection and account validation stay real."""
    from amplifier_web import decision_accounts as accounts
    from amplifier_web.provider_environment import credential_bindings

    definitions = {"provider-fixture-decisions": {
        "module": "hooks-fixture-decisions", "when": {"chat_enabled": False}}}
    monkeypatch.setattr(accounts, "catalogs", lambda: definitions)
    original = legacy_image_plan()
    original["tools"][0]["config"] = {
        "allow_paid": True, "model_role": "image-gen",
        "provider_preferences": [{"provider": "account-a", "model": "image-model-a"}],
    }
    original["providers"][0]["config"].pop("image_generation")
    # Put the decision account first: ordinary resume restoration moves it behind
    # real providers, which must not change the image-binding comparison.
    original["providers"].insert(0, {
        "module": "provider-fixture-decisions", "id": "decision-a", "instance_id": "decision-a",
        "source": "fixture-decision-source",
        "config": {"api_key": "synthetic-old-decision-key", "chat_enabled": False,
                   "decisions": {"enabled": True, "backends": {
                       "browser": {"enabled": True, "model": "saved-decision-model"}}}},
    })
    projected = accounts.DecisionAccounts(original, None, lambda module, source: source)
    assert len(projected.expected) == 1
    saved = credential_bindings(projected.plan)
    assert accounts.ACCOUNTS not in saved["session"]
    assert [row["module"] for row in saved["providers"]] == [
        "provider-fixture-decisions", "provider-openai"]
    current = copy.deepcopy(original)
    current["providers"][0]["config"].update(
        api_key="synthetic-current-decision-key", decisions={"enabled": False})
    current["providers"][1]["config"]["api_key"] = "synthetic-current-image-key"
    return saved, current


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_projection", ["logical", "hooks"])
@pytest.mark.parametrize("image_credential", ["legacy-omitted", "host-binding"])
async def test_routed_image_resume_preserves_decision_projection_and_exact_account(
        resume_at_image_boundary, routed_image_decision_plans, parent_projection, image_credential):
    from amplifier_web import decision_accounts as accounts

    saved, current = routed_image_decision_plans
    if image_credential == "legacy-omitted":
        # Older SessionStore checkpoints omitted literal image credentials;
        # current credential_bindings snapshots retain the host-owned marker.
        saved["providers"][1]["config"].pop("api_key")
    else:
        assert saved["providers"][1]["config"]["api_key"] == {"$hostProviderCredential": True}
    if parent_projection == "hooks":
        current = accounts.DecisionAccounts(current, None, lambda module, source: source).plan
    captured = await resume_at_image_boundary(saved, current)
    plan = captured["plan"]
    provider, = plan["providers"]
    assert provider["instance_id"] == "account-a"
    assert provider["config"]["api_key"] == "synthetic-current-image-key"
    assert provider["config"]["base_url"] == saved["providers"][1]["config"]["base_url"]
    assert plan["tools"][0]["config"]["provider_preferences"] == [
        {"provider": "account-a", "model": "image-model-a"}]
    hook, = [row for row in plan["hooks"] if accounts.ENVELOPE in row.get("config", {})]
    assert hook["module"] == "hooks-fixture-decisions"
    assert hook["source"] == "fixture-decision-source"
    assert hook["config"]["api_key"] == "synthetic-current-decision-key"
    assert hook["config"]["decisions"] == saved["providers"][0]["config"]["decisions"]
    envelope = hook["config"][accounts.ENVELOPE]
    assert envelope["provider_module"] == "provider-fixture-decisions"
    assert envelope["instance_id"] == "decision-a"
    record, = plan["session"][accounts.ACCOUNTS]
    assert envelope["binding_id"] == record["binding_id"]
    assert record["provider"]["instance_id"] == "decision-a"
    assert isinstance(captured["resolver"], accounts.AccountResolver)
    assert captured["resolver"].aliases == {
        ("hooks-fixture-decisions", "fixture-decision-source"): "provider-fixture-decisions"}
    restored = accounts.logical_plan(plan)
    assert [row["instance_id"] for row in restored["providers"]] == ["account-a", "decision-a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["image-model-pin", "ordinary-provider-endpoint"])
async def test_routed_image_resume_rejects_changed_pin_beside_decision_projection(
        resume_at_image_boundary, routed_image_decision_plans, change):
    from amplifier_web import decision_accounts as accounts

    saved, current = routed_image_decision_plans
    # Use the legacy credential-free representation so a separate host-marker
    # mismatch cannot satisfy the changed-binding negative control.
    saved["providers"][1]["config"].pop("api_key")
    if change == "image-model-pin":
        current["tools"][0]["config"]["provider_preferences"][0]["model"] = "image-model-b"
        error = "Saved child image selection has changed"
    else:
        current["providers"][1]["config"]["base_url"] = "https://other.example.invalid/v1"
        error = "Saved child image provider binding has changed"
    current = accounts.DecisionAccounts(current, None, lambda module, source: source).plan
    await resume_at_image_boundary(saved, current, error=error)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "disabled"])
async def test_routed_image_resume_cannot_restore_unauthorized_decision_account(
        resume_at_image_boundary, routed_image_decision_plans, change):
    from amplifier_web import decision_accounts as accounts

    saved, current = routed_image_decision_plans
    decision = current["providers"][0]
    if change == "source":
        decision["source"] = "different-decision-source"
    else:
        decision["enabled"] = False
    current = accounts.DecisionAccounts(current, None, lambda module, source: source).plan
    await resume_at_image_boundary(
        saved, current, error="saved child decision account/source is no longer authorized")
