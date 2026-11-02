import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web import decision_accounts as accounts
from amplifier_web.module_failures import ConfiguredModuleError
from amplifier_web.provider_environment import credential_bindings, rebind_provider_credentials
from amplifier_web.provider_isolation import ProviderIsolation
from amplifier_web.runtime_controls import public_config


DEFINITIONS = {"provider-fixture": "hooks-fixture-decisions"}
SOURCE = "git+https://example.test/fixture@exact"


def account(name="one", *, enabled=True, key="synthetic-secret-one"):
    return {"module": "provider-fixture", "instance_id": name, "source": SOURCE,
            "config": {"api_key": key, "decisions": {"enabled": enabled,
                "backends": {name: {"enabled": True, "model": "test"}}}}}


@pytest.fixture(autouse=True)
def definitions(monkeypatch):
    monkeypatch.setattr(accounts, "catalogs", lambda: DEFINITIONS)


def project(plan, *, sources=None):
    return accounts.DecisionAccounts(plan, ProviderIsolation(),
        lambda module, source: (sources or {}).get(module, source))


def fixture_plan():
    return {"session": {"orchestrator": {"module": "loop-streaming"}},
            "providers": [{"module": "provider-ordinary", "config": {"api_key": "ordinary-secret"}},
                          account(), account("two", key="synthetic-secret-two")],
            "hooks": [{"module": "hooks-logging"}],
            "agents": {"worker": {"providers": [account("child", key="child-secret")]}}}


def test_runtime_projection_roundtrips_without_persisting_secrets_or_tokens():
    original = fixture_plan()
    adapter = project(original)
    assert [row["module"] for row in adapter.plan["providers"]] == ["provider-ordinary"]
    assert len(adapter.expected) == 2
    assert adapter.plan["agents"] == original["agents"]  # Child binds in its own host.
    assert accounts.logical_plan(adapter.plan) == original
    snapshot = credential_bindings(adapter.plan)
    text = json.dumps(snapshot)
    assert "secret" not in text and "binding_id" not in text and "_decision_" not in text
    rebound, bound = rebind_provider_credentials(snapshot, original)
    assert bound and rebound == original
    assert "_decision_" not in json.dumps(public_config(adapter.plan))
    assert original == fixture_plan()


@pytest.mark.parametrize("change", ["source", "instance", "module", "missing", "disabled", "duplicate"])
def test_saved_projected_account_cannot_borrow_another_authorized_identity(change):
    original = fixture_plan()
    saved = credential_bindings(project(original).plan)
    authorized = copy.deepcopy(original)
    row = authorized["providers"][1]
    if change == "source": row["source"] += "-other"
    elif change == "instance": row["instance_id"] = "other"
    elif change == "module": row["module"] = "provider-other"
    elif change == "missing": authorized["providers"].remove(row)
    elif change == "disabled": row["enabled"] = False
    else: authorized["providers"].append(copy.deepcopy(row))
    with pytest.raises(ValueError, match="authorized host credential"):
        rebind_provider_credentials(saved, authorized)


@pytest.mark.parametrize("change", ["hook", "source", "binding", "credentials", "duplicate"])
def test_forged_projection_rejected_before_credential_rebinding(change):
    adapter = project(fixture_plan())
    forged = copy.deepcopy(adapter.plan)
    row = forged["hooks"][1]
    if change == "hook": row["module"] = "hooks-other"
    elif change == "source": row["source"] = "git+https://untrusted.test/other"
    elif change == "binding": row["config"][accounts.ENVELOPE]["binding_id"] = "unrecognized"
    elif change == "credentials": row["config"]["api_key"] = "different-secret"
    else: forged["hooks"].append(copy.deepcopy(row))
    with pytest.raises(ValueError):
        rebind_provider_credentials(forged, fixture_plan())


def test_optional_disabled_account_is_saved_without_a_runtime_mount():
    plan = {"providers": [account(enabled=False)], "hooks": []}
    adapter = project(plan)
    assert adapter.plan["providers"] == [] and adapter.plan["hooks"] == []
    assert not adapter.expected and not adapter.failures
    restored = accounts.logical_plan(adapter.plan)
    assert restored["providers"] == plan["providers"]
    adapter.require_complete()


def test_environment_binding_and_effective_source_survive_a_new_projection():
    logical = {"providers": [account(key="${EXACT_DECISION_KEY}")], "hooks": [], "session": {}}
    accounts.bind_sources(logical, {"provider-fixture": SOURCE + "-selected"})
    snapshot = credential_bindings(project(logical).plan)
    assert snapshot == logical and snapshot["providers"][0]["config"]["api_key"] == "${EXACT_DECISION_KEY}"
    rebound, bound = rebind_provider_credentials(snapshot, logical)
    assert bound and rebound == logical
    new_projection = project(rebound)
    assert set(new_projection.expected).isdisjoint(project(logical).expected)
    assert next(iter(new_projection.expected.values()))[1]["source"] == SOURCE + "-selected"


@pytest.mark.parametrize("chat_enabled", [None, True, False, 0])
def test_only_metadata_declared_false_condition_projects_a_mixed_account(chat_enabled):
    row = account()
    if chat_enabled is not None:
        row["config"]["chat_enabled"] = chat_enabled
    definition = {"provider-fixture": {"module": "hooks-fixture-decisions", "when": {"chat_enabled": False}}}
    adapter = accounts.DecisionAccounts({"providers": [row]}, ProviderIsolation(),
                                        lambda module, source: source, definitions=definition)
    assert bool(adapter.expected) is (chat_enabled is False)
    assert (adapter.plan["providers"] == []) is (chat_enabled is False)
    assert accounts.logical_plan(adapter.plan, definitions=definition)["providers"] == [row]


def test_conflicting_hook_source_and_duplicate_accounts_fail_before_mount():
    with pytest.raises(ValueError, match="source differs"):
        project({"providers": [account()]}, sources={"hooks-fixture-decisions": SOURCE + "-other"})
    with pytest.raises(ValueError, match="Duplicate logical"):
        project({"providers": [account(), account()]})


def test_preflight_failure_is_retained_without_interpreting_invalid_config_or_mounting():
    row = account()
    row["config"] = "invalid-configuration"
    isolation = ProviderIsolation()
    failure = isolation.record(row, "provider_configuration_failed", error=ValueError("synthetic-secret"))
    adapter = accounts.DecisionAccounts({"providers": [row]}, isolation, lambda module, source: source)
    assert adapter.failures == [failure] and not adapter.expected
    assert adapter.plan["providers"] == [] and adapter.plan["hooks"] == []
    assert accounts.logical_plan(adapter.plan)["providers"] == [row]
    assert "synthetic-secret" not in repr(adapter.failures)
    adapter.require_complete()


async def callback(adapter):
    values = {}
    await adapter.before_initialize(SimpleNamespace(coordinator=SimpleNamespace(
        register_capability=lambda key, value: values.__setitem__(key, value))))
    return values["decision.account_lifecycle"]


@pytest.mark.asyncio
async def test_exact_receipts_keep_safe_unavailable_distinct_from_unfinished_mount():
    adapter = project(fixture_plan())
    report = await callback(adapter)
    first, second = list(adapter.expected)
    await report(first, "hooks-fixture-decisions", "ready")
    with pytest.raises(ConfiguredModuleError): adapter.require_complete()
    assert await report(second, "hooks-fixture-decisions", "unavailable", ValueError("synthetic-secret")) is True
    adapter.require_complete()
    assert [row["instance_id"] for row in adapter.failures] == ["two"]
    assert "synthetic-secret" not in repr(adapter.failures)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unsafe", "cancelled", "unknown", "duplicate", "wrong-binding"])
async def test_unsafe_or_untrusted_receipt_never_allows_initialization(outcome):
    adapter = project({"providers": [account()]})
    report = await callback(adapter)
    binding = next(iter(adapter.expected))
    if outcome in {"unsafe", "cancelled"}:
        await report(binding, "hooks-fixture-decisions", outcome)
    else:
        if outcome == "duplicate": await report(binding, "hooks-fixture-decisions", "ready")
        with pytest.raises(ValueError):
            await report("wrong" if outcome == "wrong-binding" else binding,
                         "hooks-fixture-decisions", "unknown" if outcome == "unknown" else "ready")
    with pytest.raises(ConfiguredModuleError): adapter.require_complete()


@pytest.mark.asyncio
async def test_source_resolver_uses_prepared_provider_package_for_hook():
    calls = []
    class Resolver:
        async def async_resolve(self, module, source_hint=None):
            calls.append((module, source_hint))
            return SimpleNamespace(resolve=lambda: "exact-selected-package")
    resolver = accounts.AccountResolver(Resolver(), {("hooks-fixture-decisions", SOURCE): "provider-fixture"})
    assert (await resolver.async_resolve("hooks-fixture-decisions", source_hint=SOURCE)).resolve() == "exact-selected-package"
    assert calls == [("provider-fixture", SOURCE)]


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_missing_selected_source_never_authorizes_core_entrypoint_fallback(deferred):
    from amplifier_core.module_sources import ModuleNotFoundError as SourceNotFoundError
    def missing():
        raise SourceNotFoundError("synthetic-private-path")
    class Resolver:
        async def async_resolve(self, module, source_hint=None):
            return SimpleNamespace(resolve=missing) if deferred else missing()
    resolver = accounts.AccountResolver(Resolver(), {("hooks-fixture-decisions", SOURCE): "provider-fixture"})
    with pytest.raises(ValueError, match="authorized decision hook source is unavailable"):
        await resolver.async_resolve("hooks-fixture-decisions", source_hint=SOURCE)
    with pytest.raises(ValueError, match="does not match"):
        await resolver.async_resolve("hooks-fixture-decisions", source_hint=SOURCE + "-other")
    with pytest.raises(SourceNotFoundError):
        selected = await resolver.async_resolve("hooks-unrelated", source_hint=SOURCE)
        selected.resolve()


def test_child_composition_keeps_logical_accounts_before_snapshot():
    from amplifier_web.host.children import child_plan
    original = fixture_plan()
    result = child_plan(project(original).plan, {})
    assert [row["instance_id"] for row in result["providers"] if row["module"] == "provider-fixture"] == ["one", "two"]
    assert accounts.ACCOUNTS not in result.get("session", {})
    assert all(accounts.ENVELOPE not in row.get("config", {}) for row in result["hooks"])


def test_child_overlay_cannot_move_inherited_credentials_to_another_source():
    from amplifier_web.host.children import child_plan
    original = fixture_plan()
    with pytest.raises(ValueError, match="inherited account source"):
        child_plan(project(original).plan, {"providers": [{"module": "provider-fixture", "instance_id": "one",
            "source": SOURCE + "-other", "config": {"decisions": {"enabled": True}}}]})


def test_ordinary_mixed_chat_account_keeps_existing_child_source_policy(monkeypatch):
    from amplifier_web.host.children import child_plan
    monkeypatch.setattr(accounts, "catalogs", lambda: {"provider-fixture": {
        "module": "hooks-fixture-decisions", "when": {"chat_enabled": False}}})
    row = account()
    row["config"]["chat_enabled"] = True
    result = child_plan({"providers": [row]}, {"providers": [{"module": "provider-fixture", "instance_id": "one",
        "source": SOURCE + "-other"}]})
    assert result["providers"][0]["source"] == SOURCE + "-other"


def test_child_resume_restores_saved_decisions_and_rebinds_only_current_exact_account():
    saved = credential_bindings(fixture_plan())
    current = fixture_plan()
    current["providers"][1]["config"].update(api_key="rotated-private-key", decisions={"enabled": False})
    current["providers"].append(account("new-parent-account"))
    plan = accounts.restore_child_accounts(copy.deepcopy(current), saved, current)
    rows = [row for row in plan["providers"] if row["module"] == "provider-fixture"]
    assert [row["instance_id"] for row in rows] == ["one", "two"]
    assert rows[0]["config"]["api_key"] == "rotated-private-key"
    assert rows[0]["config"]["decisions"]["enabled"] is True
    current["providers"][1]["source"] += "-changed"
    with pytest.raises(ValueError, match="no longer authorized"):
        accounts.restore_child_accounts(copy.deepcopy(current), saved, current)


def test_real_core_child_decision_hook_and_resume(tmp_path):
    import os
    import subprocess
    from pathlib import Path
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    if not python:
        pytest.skip("Set UNIFIED_RUNTIME_PYTHON with installed decision hooks for actual-Core child probe")
    result = subprocess.run([python, str(Path(__file__).parent / "fixtures/decision_children_probe.py")],
                            text=True, capture_output=True, timeout=45)
    assert result.returncode == 0, result.stderr + result.stdout
    assert '"child_resume": true' in result.stdout
    assert '"rejected_source_switch": true' in result.stdout


def test_cold_worker_catalog_discovery_does_not_import_provider_packages():
    import os
    import subprocess
    from pathlib import Path
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    if not python:
        pytest.skip("Set UNIFIED_RUNTIME_PYTHON for installed cold-worker catalog discovery")
    root = str(Path(__file__).resolve().parents[1])
    script = """import json,sys
sys.path.insert(0, ROOT)
from amplifier_web.decision_accounts import catalogs
before = {name for name in sys.modules if name.startswith('amplifier_module_provider_')}
assert not before, before
value = catalogs()
assert value.get('provider-jev') == 'hooks-jev-decisions', value
assert value.get('provider-local-decisions') == 'hooks-local-decisions', value
# Azure is independently optional and may have no chat SDK in this thin env.
if 'provider-azure-openai' in value:
    assert value['provider-azure-openai'] == {'module':'hooks-azure-openai-decisions','when':{'chat_enabled':False}}, value
after = {name for name in sys.modules if name.startswith('amplifier_module_provider_')}
assert not after, after
assert catalogs() == value
print(json.dumps({'coldProviderImports': len(after), 'declaredHooks': value}))
""".replace("ROOT", repr(root))
    result = subprocess.run([python, "-I", "-c", script], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
    assert '"coldProviderImports": 0' in result.stdout


def test_catalog_helper_failure_is_cached_without_credentials_or_parent_imports(monkeypatch):
    import subprocess
    accounts._catalog_snapshot.cache_clear()
    calls = []
    def fail(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, 10)
    monkeypatch.setenv("SYNTHETIC_PRIVATE_API_KEY", "must-not-cross")
    monkeypatch.setattr(accounts.subprocess, "run", fail)
    try:
        assert accounts._catalog_snapshot() is None
        assert accounts._catalog_snapshot() is None
        assert len(calls) == 1 and calls[0][0][1] == "-I"
        assert "SYNTHETIC_PRIVATE_API_KEY" not in calls[0][1]["env"]
    finally:
        accounts._catalog_snapshot.cache_clear()
