"""Opt-in canonical provider candidate qualification, with synthetic transport only.

The managed runner must install the exact reviewed candidate dependency graph.
Run explicitly: UNIFIED_CATALOG_CANDIDATES=1 python -m pytest -s \
    tests/test_candidate_model_catalog.py
Add UNIFIED_CATALOG_ACCEPTED=1 to select the separately retained merged graph.

This file never installs packages or calls a live service. It exercises the
actual SetupManager subprocess protocol and disk-backed host catalog. Captured
JSON/JUnit properties identify loaded class files/hashes, provenance and request
counts. The helper verifies installed Python bytes against the parent's retained
canonical_model_catalog_candidate.json manifest before admitting each probe.
The older six-adapter manifest remains historical evidence, not canonical proof.
The separate synthetic unsupported test is NOT another migrated adapter.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest
import yaml

from amplifier_web.host.config import write_private
from amplifier_web.next_message_configuration import validate_selection
from amplifier_web.provider_catalog import ProviderCatalog, model_key
from amplifier_web.session_files import amplifier_home
from amplifier_web.setup import SetupManager

pytestmark = pytest.mark.skipif(
    os.environ.get("UNIFIED_CATALOG_CANDIDATES") != "1",
    reason="Requires the explicitly installed immutable canonical provider graph",
)
TRANSPORT_FILE = Path(__file__).resolve().with_name("fixtures") / "catalog_candidate_transports.py"
_spec = importlib.util.spec_from_file_location("catalog_candidate_transports", TRANSPORT_FILE)
transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transport)  # Definitions only; install() runs ONLY in children.


def _records(path, event=None):
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return rows if event is None else [row for row in rows if row["event"] == event]


def _config(provider, tmp_path):
    config = {"model": transport.SELECTION_ONLY, "default_model": transport.SELECTION_ONLY,
              "priority": 7, "max_retries": 0, "retry_jitter": False}
    if provider in {"openai", "anthropic", "gemini", "chat-completions"}:
        config["api_key"] = "${CATALOG_CANDIDATE_KEY}"
    if provider in {"openai", "anthropic", "chat-completions"}:
        config["base_url"] = f"https://catalog.invalid/{provider}/v1"
    if provider == "anthropic":
        # Anthropic's SDK appends /v1/models, unlike OpenAI's /models route.
        config["base_url"] = "https://catalog.invalid/anthropic"
    if provider == "github-copilot":
        config["github_token"] = "${CATALOG_CANDIDATE_KEY}"
    if provider == "openai-chatgpt":
        token_path = tmp_path / "synthetic-chatgpt-tokens.json"
        write_private(token_path, json.dumps({
            "auth_mode": "oauth", "access_token": transport.SYNTHETIC_KEY,
            "account_id": "catalog-fixture-account", "plan_type": "plus",
            "expires_at": "2999-01-01T00:00:00+00:00",
        }))
        config.update(auth_mode="chatgpt_codex", token_file_path=str(token_path),
                      models_client_version="catalog-qualification")
    return config


async def test_actual_chatgpt_refresh_preserves_saved_and_mounted_account(
    tmp_path, monkeypatch, candidate_environment, record_property,
):
    from types import SimpleNamespace
    from amplifier_web.provider_catalog import configured_binding, mounted_catalog_keys
    from amplifier_web.runtime_controls import RuntimeControls
    monkeypatch.setenv("UNIFIED_CATALOG_PROVIDER", "openai-chatgpt")
    monkeypatch.setenv("UNIFIED_CATALOG_CHATGPT_REFRESH", "1")
    config = _config("openai-chatgpt", tmp_path)
    path = Path(config["token_file_path"])
    tokens = json.loads(path.read_text())
    tokens.update(expires_at="2020-01-01T00:00:00+00:00",refresh_token="synthetic-expired-refresh")
    path.write_text(json.dumps(tokens))
    row = {"id":"candidate","module":"provider-openai-chatgpt","config":config}
    write_private(amplifier_home()/"settings.yaml",yaml.safe_dump({"config":{"providers":[row]}}))
    home=tmp_path/"host";cache=ProviderCatalog(tmp_path/"catalog.json")
    manager=SetupManager(home,catalog=cache,probe_command=[sys.executable,str(TRANSPORT_FILE)])
    expected=configured_binding(home,tmp_path,"candidate")
    plan={"providers":[row]};sources={"candidate":None}
    captured={"home":str(home),"workspace":str(tmp_path),"sources":sources,
              "connectionKeys":mounted_catalog_keys(home,tmp_path,[row],sources,binding=True)}
    coordinator=SimpleNamespace(config=plan,session_state={},get=lambda name:{},
        get_capability=lambda name:captured if name=="web.provider_catalog" else None,
        register_capability=lambda *args:None)
    controls=RuntimeControls(SimpleNamespace(coordinator=coordinator,config=plan,session_id="fixture"),
                             SimpleNamespace(generation=None,queued_inputs=0))
    try:
        result=await manager.discover_catalog(tmp_path,instance="candidate")
        entry=next(iter(result["catalogs"].values()))
        assert entry["phase"]=="ready" and entry["models"]
        assert len(_records(candidate_environment,"refresh"))==1
        assert len(_records(candidate_environment,"request"))==2
        assert json.loads(path.read_text())["refresh_token"]=="synthetic-rotated-refresh"
        assert configured_binding(home,tmp_path,"candidate")==expected
        assert (await controls.perform("provider.verifyConnection",{
            "instance":"candidate","expectedConnectionKey":expected}))["verified"]
        assert mounted_catalog_keys(home,tmp_path,[row],sources,binding=True)["candidate"]==expected
        path.unlink()
        with pytest.raises(ValueError,match="credentials changed"):
            await controls.perform("provider.verifyConnection",{
                "instance":"candidate","expectedConnectionKey":expected})
        assert not _records(candidate_environment,"forbidden")
        record_property("providerClasses",json.dumps(_records(candidate_environment,"provider-class")))
    finally:
        await controls.close();await cache.close()


@pytest.fixture
def candidate_environment(tmp_path, monkeypatch):
    # A test command must not inherit host account choices, token paths, proxies
    # or SDK routing defaults. Only synthetic credentials are handed to probes.
    prefixes = ("OPENAI_", "ANTHROPIC_", "GOOGLE_", "GEMINI_", "CHATGPT_",
                "CHAT_COMPLETIONS_", "COPILOT_", "GITHUB_", "GH_", "AZURE_",
                "AMPLIFIER_PROVIDER_GITHUB_COPILOT_", "AMPLIFIER_GITHUB_COPILOT_")
    for name in list(os.environ):
        if name.startswith(prefixes) or name in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
            "all_proxy", "SKIP_SDK_CHECK",
        }:
            monkeypatch.delenv(name, raising=False)
    for name, folder in {
        "HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data", "CODEX_HOME": "codex",
    }.items():
        path = tmp_path / folder
        path.mkdir()
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("CATALOG_CANDIDATE_KEY", transport.SYNTHETIC_KEY)
    monkeypatch.setenv("UNIFIED_CATALOG_MODE", "success")
    monkeypatch.setenv("UNIFIED_CATALOG_STATUS", "401")
    log = tmp_path / "transport.jsonl"
    monkeypatch.setenv("UNIFIED_CATALOG_LOG", str(log))
    return log


@pytest.mark.parametrize("provider", transport.CANONICAL_PROVIDERS)
async def test_actual_adapter_probe_cache_restart_refresh_empty_and_errors(
    provider, tmp_path, monkeypatch, candidate_environment, record_property,
):
    """One bounded lifecycle per real adapter; no patched probe/discovery/cache."""
    log = candidate_environment
    monkeypatch.setenv("UNIFIED_CATALOG_PROVIDER", provider)
    row = {"id": "candidate", "module": "provider-" + provider,
           "config": _config(provider, tmp_path)}
    write_private(amplifier_home() / "settings.yaml", yaml.safe_dump({"config": {"providers": [row]}}))
    home, cache_path = tmp_path / "host", tmp_path / "catalog.json"
    caches = []

    def manager():
        cache = ProviderCatalog(cache_path)
        caches.append(cache)
        return SetupManager(home, catalog=cache, probe_command=[sys.executable, str(TRANSPORT_FILE)])

    def requests(expected):
        rows = _records(log, "request")
        assert len(rows) == expected, rows
        assert {entry["provider"] for entry in rows} <= {provider}
        assert not _records(log, "forbidden"), _records(log, "forbidden")
        return rows

    current = manager()
    args = {"id": "candidate"}
    workspace = str(tmp_path)
    try:
        schema = await current.probe("providers.schema", {**args, "module": row["module"]}, workspace)
        assert isinstance(schema["providerMetadata"]["configSchema"], dict)
        requests(0)  # Metadata/schema must not make a catalog or inference call.

        result = await current.cached_probe("providers.models", args, workspace)
        expected_ids = transport.DEFAULT_IDS[provider]
        assert [model["id"] for model in result["models"]] == expected_ids
        assert result["modelsProvenance"] == "provider-api"
        assert result["modelsSupported"] is True
        assert transport.SELECTION_ONLY not in expected_ids
        assert transport.SYNTHETIC_KEY not in json.dumps(result)
        requests(1)
        identity = current.catalog_key(args, workspace)
        key = model_key(identity)
        selected = {"instance": "candidate", "model": transport.SELECTION_ONLY}
        validate_selection(selected, {"providers": [{"id": "candidate", "sharedCatalogKey": identity}],
                                      "catalogs": {identity: current.catalog.entry(key)}})
        assert transport.SELECTION_ONLY not in [m['id'] for m in current.catalog.entry(key)['models']]

        assert await current.cached_probe("providers.models", args, workspace) == result
        requests(1)
        await current.catalog.close()
        current = manager()  # Disk reload, not a reused in-memory cache object.
        assert await current.cached_probe("providers.models", args, workspace) == result
        requests(1)
        refreshed = await current.cached_probe("providers.models", {**args, "refresh": True}, workspace)
        assert refreshed == result
        requests(2)

        # A failed refresh is visibly failed, never returned as legacy fallback.
        # The last good rows are retained as history, not relabeled fresh success.
        for index, (mode, status) in enumerate(
            [("http-error", "401"), ("http-error", "503"), ("malformed", "200")], start=3,
        ):
            monkeypatch.setenv("UNIFIED_CATALOG_MODE", mode)
            monkeypatch.setenv("UNIFIED_CATALOG_STATUS", status)
            with pytest.raises(ValueError) as raised:
                await current.cached_probe("providers.models", {**args, "refresh": True}, workspace)
            assert transport.SYNTHETIC_KEY not in str(raised.value)
            entry = current.catalog.entry(key)
            assert entry["phase"] == "error"
            assert entry["models"] == result["models"]
            assert entry["diagnostic"]["stage"] == "models", entry
            assert "Synthetic catalog failure" not in entry["error"]
            requests(index)
            with pytest.raises(ValueError):
                await current.cached_probe("providers.models", args, workspace)
            requests(index)  # Reopen does not silently retry an error.

        monkeypatch.setenv("UNIFIED_CATALOG_MODE", "empty")
        empty = await current.cached_probe("providers.models", {**args, "refresh": True}, workspace)
        assert empty["models"] == []
        assert empty["modelsSupported"] is True and empty["modelsProvenance"] == "provider-api"
        assert current.catalog.entry(key)["phase"] == "ready"
        requests(6)
        await current.catalog.close()
        current = manager()
        assert (await current.cached_probe("providers.models", args, workspace))["models"] == []
        requests(6)  # Genuine empty replaces success on disk too.

        evidence = _records(log, "provider-class")
        assert evidence
        assert all(item["module"].startswith(item["package"]) for item in evidence)
        assert all(item["listingDefinedBy"].startswith(item["package"]) for item in evidence)
        assert len({(item["class"], item["file"], item["sha256"]) for item in evidence}) == 1
        assert len({item["pid"] for item in evidence}) == 7  # Schema + six actual subprocess probes.
        assert all(item["pid"] != os.getpid() for item in evidence)
        assert all(len(item["sha256"]) == 64 for item in evidence)
        assert all(len(item["candidateCommit"]) == 40 and item["verifiedSourceFiles"] > 0 for item in evidence)
        assert transport.SYNTHETIC_KEY not in log.read_text()
        report = {"provider": provider, "classEvidence": evidence[0],
                  "provenance": "provider-api", "transportRequests": requests(6),
                  "scope": "synthetic transport; real installed adapter and Unified probe/cache",
                  "limits": "No service entitlement, inference, GUI or Copilot CLI/wire validation"}
        record_property("candidate_catalog_evidence", json.dumps(report, sort_keys=True))
        print("CANDIDATE_CATALOG_EVIDENCE " + json.dumps(report, sort_keys=True))
    finally:
        for cache in caches:
            await cache.close()


@pytest.mark.parametrize("provider", ["openai", "openai-chatgpt"])
async def test_actual_gpt_configured_scopes_share_binding_not_inventory(
    provider, tmp_path, monkeypatch, candidate_environment,
):
    """No copied filter algorithm: real provider config selects each inventory."""
    monkeypatch.setenv('UNIFIED_CATALOG_PROVIDER',provider)
    manager=SetupManager(tmp_path/'host',catalog=ProviderCatalog(tmp_path/'catalog.json'),
                         probe_command=[sys.executable,str(TRANSPORT_FILE)])
    row={'id':'candidate','module':'provider-'+provider,'config':_config(provider,tmp_path)}
    workspace=str(tmp_path)
    identities=[];bindings=[]
    try:
        for filters, expected in [
            (None,transport.DEFAULT_IDS[provider]),
            ({},sorted(transport.MODEL_IDS[provider]) if provider=='openai' else transport.MODEL_IDS[provider]),
            ({'include':['GPT-6*'],'exclude':['*mini'],'hide_dated':True},['gpt-6']),
            ({'include':[]},[]),
        ]:
            if filters is None:
                row['config'].pop('model_filters',None)
            else:
                row['config']['model_filters']=filters
            write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':[row]}}))
            identities.append(manager.catalog_key({'id':'candidate'},workspace))
            bindings.append(manager.catalog_key({'id':'candidate'},workspace,binding=True))
            result=await manager.cached_probe('providers.models',{'id':'candidate'},workspace)
            assert [m['id'] for m in result['models']]==expected
            assert result['modelsSupported'] is True and result['modelsProvenance']=='provider-api'
            info=result['providerMetadata']['modelListInfo']
            assert info['source']=='provider-api' and info['filters_keyword']=='filters'
        assert len(set(identities))==4 and len(set(bindings))==1
        assert not _records(candidate_environment,'forbidden')
    finally:await manager.catalog.close()


@pytest.mark.parametrize("provider,custom", [
    ("chat-completions", {"include": ["ACCOUNT*"]}),
    ("gemini", {"include": ["GEMINI-3.1*"], "chat_only": True, "latest_per_tier": False,
                "hide_dated": True}),
    ("github-copilot", {"include": ["GPT*"], "latest_per_family": False, "hide_dated": True}),
])
async def test_actual_provider_specific_selectors_are_opaque_to_host(
    provider, custom, tmp_path, monkeypatch, candidate_environment,
):
    monkeypatch.setenv("UNIFIED_CATALOG_PROVIDER", provider)
    manager = SetupManager(tmp_path / "host", catalog=ProviderCatalog(tmp_path / "catalog.json"),
                           probe_command=[sys.executable, str(TRANSPORT_FILE)])
    row = {"id": "candidate", "module": "provider-" + provider,
           "config": _config(provider, tmp_path)}
    custom_ids = {
        "chat-completions": ["account-model-one"],
        "gemini": ["gemini-3.1-flash", "gemini-3.1-flash-lite", "gemini-3.1-pro"],
        "github-copilot": ["gpt-5.4", "gpt-6", "gpt-6-mini"],
    }
    keys, bindings = [], []
    try:
        for filters, expected in [
            (None, transport.DEFAULT_IDS[provider]),
            ({}, sorted(transport.MODEL_IDS[provider])),
            (custom, custom_ids[provider]),
            ({"include": []}, []),
        ]:
            if filters is None:
                row["config"].pop("model_filters", None)
            else:
                row["config"]["model_filters"] = filters
            write_private(amplifier_home() / "settings.yaml",
                          yaml.safe_dump({"config": {"providers": [row]}}))
            keys.append(manager.catalog_key({"id": "candidate"}, str(tmp_path)))
            bindings.append(manager.catalog_key({"id": "candidate"}, str(tmp_path), binding=True))
            result = await manager.cached_probe("providers.models", {"id": "candidate"}, str(tmp_path))
            assert [model["id"] for model in result["models"]] == expected
            assert result["modelsProvenance"] == "provider-api"
            info = result["providerMetadata"]["modelListInfo"]
            assert set(custom) <= set(info["filters_schema"]["properties"])
        assert len(set(keys)) == 4 and len(set(bindings)) == 1
        assert not _records(candidate_environment, "forbidden")
    finally:
        await manager.catalog.close()


async def test_actual_anthropic_lifecycle_scope_is_provider_owned(
    tmp_path, monkeypatch, candidate_environment,
):
    """Real SDK query fields and local recommendations, not host-side policy."""
    monkeypatch.setenv('UNIFIED_CATALOG_PROVIDER','anthropic')
    manager=SetupManager(tmp_path/'host',catalog=ProviderCatalog(tmp_path/'catalog.json'),
                         probe_command=[sys.executable,str(TRANSPORT_FILE)])
    row={'id':'candidate','module':'provider-anthropic','config':_config('anthropic',tmp_path)}
    workspace=str(tmp_path);keys=[];bindings=[]
    cases=[
        (None,transport.DEFAULT_IDS['anthropic'],['active']),
        ({},sorted(transport.MODEL_IDS['anthropic']),['active','deprecated','retired']),
        ({'lifecycle':['retired']},['claude-opus-4-5'],['retired']),
        ({'lifecycle':['active'],'include':['CLAUDE-SONNET*'],'exclude':['*preview'],
          'latest_per_family':False,'hide_dated':False},
         ['claude-sonnet-5-4','claude-sonnet-5-5','claude-sonnet-5-5-20261101'],['active']),
    ]
    try:
        for filters,expected,lifecycle in cases:
            if filters is None:row['config'].pop('model_filters',None)
            else:row['config']['model_filters']=filters
            write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':[row]}}))
            keys.append(manager.catalog_key({'id':'candidate'},workspace))
            bindings.append(manager.catalog_key({'id':'candidate'},workspace,binding=True))
            result=await manager.cached_probe('providers.models',{'id':'candidate'},workspace)
            assert [m['id'] for m in result['models']]==expected
            assert result['modelsProvenance']=='provider-api'
            assert _records(candidate_environment,'request')[-1]['lifecycle']==lifecycle
        assert len(set(keys))==4 and len(set(bindings))==1
        assert not _records(candidate_environment,'forbidden')
    finally:await manager.catalog.close()


@pytest.mark.parametrize("field", ["model", "default_model"])
async def test_legacy_filtered_selection_changes_catalog_not_pinned_account(
    field, tmp_path, monkeypatch, candidate_environment,
):
    """The real legacy selector may use either field; never reuse its old rows."""
    from amplifier_web.management import Management
    from amplifier_web.service import AppService

    provider = "chat-completions"
    monkeypatch.setenv("UNIFIED_CATALOG_PROVIDER", provider)
    config = _config(provider, tmp_path)
    config.pop("model")
    config.pop("default_model")
    config.update(filtered=True)
    row = {"id": "candidate", "module": "provider-" + provider, "config": config}
    manager = SetupManager(tmp_path / "host", catalog=ProviderCatalog(tmp_path / "catalog.json"),
                           probe_command=[sys.executable, str(TRANSPORT_FILE)])
    service = AppService(tmp_path / "host", workspace=tmp_path)
    service.management = Management(service)
    keys, bindings = [], []
    selected = {"instance": "candidate", "model": transport.SELECTION_ONLY}
    try:
        for index, expected in enumerate(transport.MODEL_IDS[provider]):
            config[field] = expected
            write_private(amplifier_home() / "settings.yaml",
                          yaml.safe_dump({"config": {"providers": [row]}}))
            if index == 0:
                await service.dispatch("session.create", {"bundle": "anchors"})
                session = service._session()
                await service.dispatch("conversation.configure", {
                    "sessionId": session["id"], "expectedRevision": 0, "selection": selected,
                }, include_state=False)
                pinned_binding = session["modelConnectionKey"]
            key = manager.catalog_key({"id": "candidate"}, str(tmp_path))
            binding = manager.catalog_key({"id": "candidate"}, str(tmp_path), binding=True)
            keys.append(key)
            bindings.append(binding)
            result = await manager.cached_probe("providers.models", {"id": "candidate"}, str(tmp_path))
            # The pre-fix key returns the first selection's cached rows here.
            assert [model["id"] for model in result["models"]] == [expected]
            assert result["providerMetadata"]["modelListInfo"]["default_filters"][
                "legacy_filtered_model"] == expected
            assert result["modelsProvenance"] == "provider-api"
            assert (await manager.cached_probe("providers.models", {"id": "candidate"},
                                               str(tmp_path))) == result
            assert len(_records(candidate_environment, "request")) == index + 1
            assert session["selection"] == session["pendingModelSelection"] == selected
            assert session["modelConnectionKey"] == pinned_binding == binding
        assert len(set(keys)) == 2 and len(set(bindings)) == 1
        config["model_filters"] = {}
        write_private(amplifier_home() / "settings.yaml",
                      yaml.safe_dump({"config": {"providers": [row]}}))
        result = await manager.cached_probe("providers.models", {"id": "candidate"}, str(tmp_path))
        assert [model["id"] for model in result["models"]] == transport.MODEL_IDS[provider]
        assert len(_records(candidate_environment, "request")) == 3
        assert not _records(candidate_environment, "forbidden")
        assert service.runtime is None
        assert session["selection"] == session["pendingModelSelection"] == selected
        assert session["modelConnectionKey"] == pinned_binding
    finally:
        await manager.catalog.close()
        await service.close()


@pytest.mark.parametrize("provider,config,expected,all_ids", [
    ("azure-openai", {"base_url":"https://catalog.invalid/azure", "deployments":["Customer-Blue", "Research/Alias"]},
     ["Customer-Blue", "Research/Alias"], ["Customer-Blue", "Research/Alias"]),
    ("litellm", {"model_list":[{"model_name":"Router-Blue"}, "Team/Alias"]},
     ["Router-Blue", "Team/Alias"], ["Router-Blue", "Team/Alias"]),
    ("perplexity", {"api_key":"catalog-fixture-not-a-live-credential"},
     ["sonar", "sonar-pro", "sonar-reasoning-pro", "sonar-deep-research"],
     ["sonar", "sonar-pro", "sonar-reasoning", "sonar-reasoning-pro", "sonar-deep-research", "r1-1776"]),
    ("mock", {}, ["mock-model"], ["mock-model"]),
])
async def test_actual_local_origin_catalog_is_visible_without_api_verification(
    provider, config, expected, all_ids, tmp_path, monkeypatch, candidate_environment, record_property,
):
    if provider not in transport.LOCAL_PROVIDERS:
        pytest.skip("Provider is not part of the explicitly selected immutable graph")
    from amplifier_web.provider_health import generation, record, issue
    monkeypatch.setenv("UNIFIED_CATALOG_PROVIDER", provider)
    # LiteLLM's published opt-out keeps its bundled pricing data offline too;
    # it is not an alternative model inventory or a patched provider.
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    manager = SetupManager(tmp_path / "host", catalog=ProviderCatalog(tmp_path / "catalog.json"),
                           probe_command=[sys.executable, str(TRANSPORT_FILE)])
    row = {"id":"local", "module":"provider-"+provider, "config":deepcopy(config)}
    origin = transport.CANDIDATES["localProviders"][provider]["source"]
    keys = []
    try:
        for filters, ids in [(None, expected), ({}, all_ids), ({"include":[]}, [])]:
            if filters is not None:
                row["config"]["model_filters"] = filters
            write_private(amplifier_home() / "settings.yaml", yaml.safe_dump({"config":{"providers":[row]}}))
            record(manager.home, "local", generation(row["module"], row["config"]), authentication_required=True)
            key = manager.catalog_key({"id":"local"}, str(tmp_path))
            keys.append(key)
            result = await manager.cached_probe("providers.models", {"id":"local"}, str(tmp_path))
            assert [model["id"] for model in result["models"]] == ids
            assert result["modelsSupported"] is True and result["modelsProvenance"] == origin
            entry = manager.catalog.entry(model_key(key))
            assert entry["phase"] == "ready" and entry["provenance"] == origin
            assert await manager.cached_probe("providers.models", {"id":"local"}, str(tmp_path)) == result
            assert ProviderCatalog(manager.catalog.path).entry(model_key(key))["provenance"] == origin
            assert issue(manager.home, "local", row["module"], row["config"]), "Local listing cannot clear a known authentication failure"
        assert len(set(keys)) == 3
        with pytest.raises(ValueError, match="models"):
            await manager.probe("providers.test", {"id":"local"}, str(tmp_path))
        if provider in {"azure-openai", "litellm"}:
            row["config"] = {"base_url":"https://catalog.invalid/azure"} if provider=="azure-openai" else {}
            write_private(amplifier_home() / "settings.yaml", yaml.safe_dump({"config":{"providers":[row]}}))
            result = await manager.cached_probe("providers.models", {"id":"local"}, str(tmp_path))
            assert result["models"] == [] and result["modelsSupported"] is False
            assert result["modelsProvenance"] == "unsupported"
        assert not _records(candidate_environment, "request")
        assert not _records(candidate_environment, "forbidden")
        evidence = _records(candidate_environment, "provider-class")
        assert evidence
        record_property("local_origin_evidence", json.dumps({
            "provider":provider, "source":origin, "classEvidence":evidence[0],
            "networkRequests":0, "scope":"Installed provider listing, not inference or entitlement"}))
    finally:
        await manager.catalog.close()


async def test_synthetic_unsupported_manual_selection_is_not_six_adapter_evidence(
    tmp_path, candidate_environment, record_property,
):
    """Real host probe/cache with a synthetic legacy-only provider, NOT Azure."""
    log = tmp_path / "legacy-events.jsonl"
    module = tmp_path / "amplifier_module_provider_catalog_legacy_fixture.py"
    write_private(module, r'''
import json
import os
from pathlib import Path

def record(event):
    with Path(__file__).with_name("legacy-events.jsonl").open("a") as stream:
        stream.write(json.dumps({"event": event, "pid": os.getpid()}) + "\n")

class LegacyOnlyProvider:
    def __init__(self, config=None):
        self.config = config or {}
        record("construct")

    def get_info(self):
        return {"id": "catalog-legacy-fixture", "config_fields": [],
                "defaults": {"model": self.config.get("default_model")}}

    async def list_models(self):
        record("legacy-list")
        raise AssertionError("Unsupported discovery must not call legacy listing")

    async def close(self):
        record("close")
''')
    launcher = tmp_path / "legacy-probe.py"
    write_private(launcher, r'''
import json
import os
from pathlib import Path
import socket
import sys

log = Path(__file__).with_name("legacy-events.jsonl")
def record(event):
    with log.open("a") as stream:
        stream.write(json.dumps({"event": event, "pid": os.getpid()}) + "\n")

def forbid_network(event, args):
    if event == "socket.getaddrinfo" or (
        event in {"socket.connect", "socket.sendto"}
        and args[0].family in {socket.AF_INET, socket.AF_INET6}
    ):
        record("forbidden-network")
        raise AssertionError("No network in synthetic unsupported qualification")

sys.addaudithook(forbid_network)
sys.path.insert(0, sys.argv[1])
record("probe-start")
from amplifier_web.provider_probe import main
main()
''')
    write_private(amplifier_home() / "settings.yaml", yaml.safe_dump({"config": {"providers": [{
        "id": "manual", "module": "provider-catalog-legacy-fixture",
        "config": {"default_model": "configured-not-discovered"},
    }]}}))
    home, cache_path = tmp_path / "host", tmp_path / "legacy-catalog.json"
    caches = []

    def manager():
        cache = ProviderCatalog(cache_path)
        caches.append(cache)
        return SetupManager(home, catalog=cache, probe_command=[
            sys.executable, str(launcher), str(TRANSPORT_FILE.parents[2]),
        ])

    current = manager()
    workspace = str(tmp_path)
    selected = {"instance": "manual", "model": "explicit-deployment"}
    original = deepcopy(selected)
    try:
        # Actual cached_probe invokes actual probe, whose child imports the
        # temporary provider through the unmodified provider_class resolver.
        result = await current.cached_probe("providers.models", {"id": "manual"}, workspace)
        assert result["models"] == []
        assert result["modelsSupported"] is False
        assert result["modelsProvenance"] == "unsupported"
        events = _records(log)
        assert [row["event"] for row in events] == [
            "probe-start", "construct", "close", "construct", "close",
        ], events
        assert len({row["pid"] for row in events}) == 1
        assert events[0]["pid"] != os.getpid()

        catalog = await current.discover_catalog(workspace)
        assert [row["id"] for row in catalog["providers"]] == ["manual"]
        identity = catalog["providers"][0]["sharedCatalogKey"]
        entry = catalog["catalogs"][identity]
        assert entry["phase"] == "ready"
        assert entry["supported"] is False and entry["provenance"] == "unsupported"
        assert entry["models"] == []
        validate_selection(selected, catalog)
        assert selected == original
        # No advertised finite choices means unknown, not a rejection or a
        # synthesized capability. Keep explicit intent for provider invocation.
        with_effort={**selected,"effort":"not-advertised"}
        validate_selection(with_effort,catalog)
        assert with_effort=={**original,"effort":"not-advertised"}
        assert entry['models']==[]
        assert _records(log) == events  # Warm host reads do not start another probe.

        persisted = json.loads(cache_path.read_text())
        assert persisted["version"] == 2 and len(persisted["entries"]) == 1
        saved = next(iter(persisted["entries"].values()))
        assert saved["result"] == result
        assert not saved.get("error")
        await current.catalog.close()
        current = manager()  # New host cache object reads the actual saved file.
        reloaded = await current.discover_catalog(workspace)
        assert reloaded["catalogs"][identity] == entry
        assert (await current.cached_probe("providers.models", {"id": "manual"}, workspace)) == result
        validate_selection(selected, reloaded)
        assert selected == original
        assert _records(log) == events  # Persistence/reload also makes zero probes.
        record_property("synthetic_unsupported_evidence", json.dumps({
            "scope": "synthetic legacy-only provider; NOT real Azure adapter evidence",
            "provenance": reloaded["catalogs"][identity]["provenance"],
            "probeProcesses": 1, "legacyListCalls": 0,
            "cacheReloadVerified": True, "manualSelection": selected,
        }, sort_keys=True))
    finally:
        for cache in caches:
            await cache.close()
