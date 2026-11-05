"""Exact-account failure outcomes; synthetic effects only, native Core separately."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_host_session_resume import mounted_host

from amplifier_web.provider_environment import materialize_bundle_providers
from amplifier_web.provider_isolation import (
    ProviderIsolation, ProviderUnavailable, UnavailableProvider, identity,
    require_available, require_selected,
)


def spec(name, **config):
    return {"module": "provider-fixture", "instance_id": name, "source": "local:fixture",
            "config": {"default_model": "fixture-v1", **config}}


async def schema(module):
    return {"fields": [{"id": "api_key", "required": True, "field_type": "secret"}]}


@pytest.mark.asyncio
async def test_same_module_account_preflight_retains_rows_order_and_exact_identity():
    rows = [spec("broken", priority=1), spec("healthy", api_key="${KEY}", priority=20)]
    original = copy.deepcopy(rows)
    bundle = SimpleNamespace(providers=rows, agents={})
    prepared = SimpleNamespace(mount_plan={})
    policy = ProviderIsolation()
    await materialize_bundle_providers(bundle, prepared, environment={"KEY": "private"}, schema_loader=schema, isolation=policy)
    assert rows == original  # Host input is unchanged.
    assert [identity(row) for row in prepared.mount_plan["providers"]] == ["broken", "healthy"]
    assert bundle.providers[0] == original[0]
    assert bundle.providers[1]["config"]["api_key"] == "private"
    assert policy.failure(bundle.providers[0])["reason_code"] == "provider_configuration_failed"
    assert not policy.failure(bundle.providers[1])
    assert "private" not in str(policy.failures.values())


@pytest.mark.asyncio
async def test_preparation_failure_never_imports_other_installed_source():
    failed = spec("broken")
    policy = ProviderIsolation()
    await policy.preparation_failed(SimpleNamespace(provider_spec=failed, error=ValueError("secret")))
    loader = AsyncMock(side_effect=AssertionError("Failed source must not import an installed fallback"))
    bundle = SimpleNamespace(providers=[failed], agents={})
    prepared = SimpleNamespace(mount_plan={})
    await materialize_bundle_providers(bundle, prepared, schema_loader=loader, isolation=policy)
    loader.assert_not_called()
    assert policy.failure(failed)["reason_code"] == "provider_preparation_failed"
    assert bundle.providers == [failed]


@pytest.mark.asyncio
async def test_schema_failure_is_attributed_without_cross_account_credential_values():
    row = spec("broken", api_key="private")
    policy = ProviderIsolation()
    bundle = SimpleNamespace(providers=[row], agents={})
    await materialize_bundle_providers(bundle, SimpleNamespace(mount_plan={}),
        schema_loader=AsyncMock(side_effect=ValueError("private")), isolation=policy)
    failure = policy.failure(row)
    assert failure["instance_id"] == "broken" and failure["reason_code"] == "provider_schema_failed"
    assert "private" not in str(failure)


@pytest.mark.asyncio
async def test_child_can_repair_same_identity_without_inheriting_stale_failure():
    row = spec("account")
    parent = ProviderIsolation()
    parent.record(row, "provider_configuration_failed")
    child = ProviderIsolation()
    child.preparation = copy.deepcopy(parent.preparation)
    repaired = spec("account", api_key="${CHILD_KEY}")
    bundle = SimpleNamespace(providers=[repaired], agents={})
    await materialize_bundle_providers(bundle, SimpleNamespace(mount_plan={}),
        schema_loader=schema, environment={"CHILD_KEY": "child-private"}, isolation=child)
    assert parent.failure(row) and not child.failure(bundle.providers[0])
    assert bundle.providers[0]["config"]["api_key"] == "child-private"


@pytest.mark.asyncio
async def test_unavailable_marker_preserves_default_priority_and_refuses_every_effect():
    row = spec("account", priority=1, api_key="do-not-copy")
    failure = ProviderIsolation().record(row, "provider_mount_failed")
    marker = UnavailableProvider(row, failure)
    assert marker.priority == 1 and marker.default_model == "fixture-v1"
    assert "do-not-copy" not in str(vars(marker))
    assert marker.get_info().id == "fixture"
    for action in (marker.list_models(), marker.complete(None)):
        with pytest.raises(ProviderUnavailable) as error:
            await action
        assert error.value.failures[0]["instance_id"] == "account"
    with pytest.raises(ProviderUnavailable):
        async for _ in marker.stream(None):
            pytest.fail("A failed account emitted data")


def test_selected_and_removed_identity_do_not_substitute_healthy_connection():
    row = spec("broken", priority=1)
    marker = UnavailableProvider(row, ProviderIsolation().record(row, "provider_configuration_failed"))
    healthy = object()
    loop = SimpleNamespace(_select_provider=lambda _: marker)
    coordinator = SimpleNamespace(get=lambda name: {"providers": {"broken": marker, "healthy": healthy}, "orchestrator": loop}[name])
    with pytest.raises(ProviderUnavailable):
        require_selected(coordinator)
    with pytest.raises(ProviderUnavailable):
        require_available(coordinator, "broken")
    from amplifier_web.host.model_selection import ProviderSelectionError
    with pytest.raises(ProviderSelectionError):
        require_available(coordinator, "renamed")
    require_available(coordinator, "healthy")


@pytest.mark.parametrize("priority", [None, "${MISSING_PRIORITY}", "invalid", {}, float("nan")])
def test_unknown_failed_priority_cannot_silently_choose_a_healthy_default(priority, monkeypatch):
    monkeypatch.delenv("MISSING_PRIORITY", raising=False)
    row = spec("broken", **({} if priority is None else {"priority": priority}))
    marker = UnavailableProvider(row, ProviderIsolation().record(row, "provider_configuration_failed"))
    healthy = SimpleNamespace(priority=1)
    providers = {"healthy": healthy, "broken": marker}
    loop = SimpleNamespace(root_provider=None)
    loop._select_provider = lambda rows: loop.root_provider or min(rows.values(), key=lambda item: item.priority)
    coordinator = SimpleNamespace(get=lambda key: {"providers": providers, "orchestrator": loop}[key])
    with pytest.raises(ProviderUnavailable):
        require_selected(coordinator)
    loop.root_provider = healthy
    require_selected(coordinator)


def test_failed_explicit_priority_is_expanded_without_loading_credentials(monkeypatch):
    monkeypatch.setenv("ACCOUNT_PRIORITY", "23")
    row = spec("broken", priority="${ACCOUNT_PRIORITY}", api_key="${MISSING_KEY}")
    marker = UnavailableProvider(row, ProviderIsolation().record(row, "provider_configuration_failed"))
    assert marker.priority == 23 and row["config"]["priority"] == "${ACCOUNT_PRIORITY}"


@pytest.mark.parametrize("budget", [None, 123])
async def test_controls_reject_failed_select_and_default_reset_without_mutating_saved_choice(tmp_path, monkeypatch, budget):
    from amplifier_core.models import ProviderInfo
    from amplifier_web.runtime_controls import RuntimeControls
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    healthy = SimpleNamespace(priority=20, get_info=lambda: ProviderInfo(id="fixture", display_name="Healthy", defaults={"model": "m"}))
    row = spec("broken", priority=1, api_key="do-not-publish")
    failed = UnavailableProvider(row, ProviderIsolation().record(row, "provider_mount_failed"))
    providers = {"healthy": healthy, "broken": failed}
    loop = SimpleNamespace(root_provider=None)
    loop._select_provider = lambda rows: loop.root_provider or min(rows.values(), key=lambda provider: provider.priority)
    coordinator = SimpleNamespace(config={"providers": [row]}, session_state={},
        get=lambda key: {"providers": providers, "orchestrator": loop, "context": SimpleNamespace()}.get(key),
        get_capability=lambda _: None, register_capability=lambda *_: None)
    controls = RuntimeControls(SimpleNamespace(coordinator=coordinator, session_id="controls"), SimpleNamespace(generation=None, queued_inputs=0))
    controls.max_output_tokens = budget
    try:
        await controls.perform("provider.select", {"instance": "healthy", "model": "m"})
        selected = loop.root_provider
        saved = controls.state_path().read_bytes()
        for action, args in (("provider.select", {"instance": "broken", "model": "m"}), ("provider.reset", {})):
            with pytest.raises(ProviderUnavailable):
                await controls.perform(action, args)
            assert loop.root_provider is selected and controls.selection["instance"] == "healthy"
            assert controls.state_path().read_bytes() == saved
        public = await controls.perform("configuration.providers")
        assert next(row for row in public["providers"] if row["id"] == "broken")["available"] is False
        assert "do-not-publish" not in str(public)
    finally:
        await controls.close()


@pytest.mark.asyncio
async def test_unavailable_image_account_is_never_constructed_or_rewritten(monkeypatch):
    from amplifier_web.image_migration import apply_legacy_image_intent
    row = spec("broken", image_generation={"enabled": False}, api_key="private")
    plan = {"providers": [row], "tools": [{"module": "tool-image", "config": {"allow_paid": True}}]}
    before = copy.deepcopy(row)
    def load(_):
        pytest.fail("Unavailable account must not be constructed")
    monkeypatch.setattr("amplifier_web.provider_environment.provider_class", load)
    receipt = apply_legacy_image_intent(plan, {"image_generation": {"mode": "automatic"}})
    assert row == before
    assert receipt["result"] == "ambiguous_requires_review"
    assert plan["tools"][0]["config"]["routing_blocked"] is True


@pytest.mark.asyncio
async def test_host_healthy_selection_reports_unrelated_failure_without_hiding_choices(mounted_host, monkeypatch):
    from amplifier_web import provider_isolation
    h = mounted_host
    monkeypatch.setattr(provider_isolation, "supported", lambda: True)
    coordinator = h.session.coordinator
    original_get = coordinator.get
    healthy = coordinator.get("providers")["fixture"]
    row = spec("broken", priority=1)
    marker = UnavailableProvider(row, ProviderIsolation().record(row, "provider_mount_failed"))
    coordinator.get = lambda key: {"fixture": healthy, "broken": marker} if key == "providers" else original_get(key)
    coordinator.get("orchestrator").load_failures = [{"module": "provider-fixture", "type": "provider", "reason_code": "unknown"}]
    _, _, report = await h.prepare(selection={"instance": "fixture", "model": "offline"})
    assert report["selection"]["instance"] == "fixture"
    assert report["provider_failures"][0]["instance_id"] == "broken"
    assert report["module_load_failures"] == []
    assert next(row for row in report["provider_choices"] if row["id"] == "broken")["available"] is False
    assert callable(h.loaded.prepare.call_args.kwargs["provider_failure_policy"])
    assert callable(h.prepared.create_session.call_args.kwargs["before_initialize"])


@pytest.mark.asyncio
async def test_host_refused_saved_selection_preserves_history_without_execution(mounted_host, monkeypatch):
    from amplifier_web import provider_isolation
    h = mounted_host
    monkeypatch.setattr(provider_isolation, "supported", lambda: True)
    saved = [{"role": "user", "content": "Saved original"}, {"role": "assistant", "content": "Saved reply"}]
    h.store.save(h.runtime.session_id, saved, {"session_id": h.runtime.session_id, "bundle_name": "anchors"})
    files = {name: (h.store.directory(h.runtime.session_id) / name).read_bytes()
             for name in ("transcript.jsonl", "metadata.json")}
    coordinator = h.session.coordinator
    original_get = coordinator.get
    marker = UnavailableProvider(spec("broken"), ProviderIsolation().record(spec("broken"), "provider_configuration_failed"))
    coordinator.get = lambda key: {"broken": marker} if key == "providers" else original_get(key)
    coordinator.get("orchestrator")._select_provider = lambda _: coordinator.get("orchestrator").root_provider
    with pytest.raises(ProviderUnavailable):
        await h.prepare(selection={"instance": "broken", "model": "saved-model"})
    h.session.execute.assert_not_called()
    h.session.cleanup.assert_awaited_once()
    assert not h.writes
    assert {name: (h.store.directory(h.runtime.session_id) / name).read_bytes() for name in files} == files


def test_old_core_does_not_claim_isolation(monkeypatch):
    from amplifier_web import provider_isolation
    monkeypatch.setattr(provider_isolation.importlib.metadata, "version", lambda _: "2.0.1")
    assert not provider_isolation.supported()
