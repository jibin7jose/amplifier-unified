"""Optional metadata has shared syntax but distinct display/mount policies."""
import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amplifier_web import decision_account_catalog as runtime
from amplifier_web import decision_settings as settings


def declaration():
    return {"schema_version": 2, "provider_module": "provider-fixture",
            "validate": Mock(side_effect=AssertionError("Discovery must not configure an account")),
            "backends": [{"configuration_key": "decisions", "decision_only": True, "label": "Original"}],
            "runtime": {"kind": "hook", "module": "hooks-fixture"}}


def catalog_entries(monkeypatch, values):
    entries = [SimpleNamespace(load=lambda value=value: lambda: value) for value in values]
    for owner in (runtime, settings):
        monkeypatch.setattr(owner, "entry_points", lambda **kwargs: entries)


def test_discovery_preserves_metadata_and_never_calls_configuration_validator(monkeypatch):
    value = declaration()
    original = copy.deepcopy(value["backends"])
    catalog_entries(monkeypatch, [value])
    assert runtime.discover() == {"provider-fixture": "hooks-fixture"}
    row, validate = settings.catalogs()["provider-fixture"]
    assert row == original[0] and row is not value["backends"][0]
    assert validate is value["validate"]
    validate.assert_not_called()
    row["label"] = "Changed view"
    assert value["backends"] == original


def test_duplicate_settings_stay_first_wins_but_runtime_mount_is_unavailable(monkeypatch):
    first, second = declaration(), declaration()
    second["backends"][0]["label"] = "Second"
    second["runtime"]["module"] = "hooks-other"
    catalog_entries(monkeypatch, [first, second])
    assert runtime.discover() == {"provider-fixture": None}
    row, validate = settings.catalogs()["provider-fixture"]
    assert row["label"] == "Original" and validate is first["validate"]


@pytest.mark.parametrize("when", [False, True, 0, None])
def test_only_exact_false_condition_enables_a_mixed_provider(monkeypatch, when):
    value = declaration()
    value["backends"][0]["decision_only"] = False
    value["runtime"]["when"] = {"chat_enabled": when}
    catalog_entries(monkeypatch, [value])
    mounted = runtime.discover()
    public = settings.catalogs()["provider-fixture"][0]
    if when is False:
        assert mounted == {"provider-fixture": {"module": "hooks-fixture", "when": {"chat_enabled": False}}}
        assert public["decision_only_when"] == {"chat_enabled": False}
    else:
        assert mounted == {}
        assert "decision_only_when" not in public


@pytest.mark.parametrize("patch", [
    {"schema_version": 1}, {"validate": None}, {"provider_module": None},
    {"backends": []}, {"backends": [{}, {}]}, {"backends": [None]},
])
def test_malformed_optional_declaration_cannot_hide_an_independent_provider(monkeypatch, patch):
    broken, good = declaration(), declaration()
    broken.update(patch)
    catalog_entries(monkeypatch, [broken, good])
    assert runtime.discover() == {"provider-fixture": "hooks-fixture"}
    assert settings.catalogs()["provider-fixture"][1] is good["validate"]


def test_unsupported_runtime_hint_does_not_hide_usable_settings_metadata(monkeypatch):
    first, second = declaration(), declaration()
    first["runtime"] = [1]
    catalog_entries(monkeypatch, [first, second])
    assert runtime.discover() == {"provider-fixture": "hooks-fixture"}
    row, validate = settings.catalogs()["provider-fixture"]
    assert validate is first["validate"] and "decision_only_when" not in row


def test_existing_runtime_name_bound_does_not_restrict_settings_metadata(monkeypatch):
    value = declaration()
    value["runtime"] = {"kind": "hook", "module": "hooks-" + "x" * 161,
                        "when": {"chat_enabled": False}}
    catalog_entries(monkeypatch, [value])
    assert runtime.discover() == {"provider-fixture": None}
    assert settings.catalogs()["provider-fixture"][0]["decision_only_when"] == {"chat_enabled": False}


def test_configuration_key_and_decision_only_eligibility_stay_with_their_consumers(monkeypatch):
    value = declaration()
    value["backends"][0]["configuration_key"] = "custom"
    catalog_entries(monkeypatch, [value])
    assert settings.catalogs() == {}
    assert runtime.discover() == {"provider-fixture": "hooks-fixture"}
