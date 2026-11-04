"""Opt-in shared model settings for an explicitly configured Amplifier runtime.

The host owns discovery and credential materialization. Portable tools keep
their domain bundle, instructions, actions and standalone configuration path.
This module implements a runtime extension, not a Smart Tools specification.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

from .config import HostConfig, _load_keys, app_home, configure_skill_cache, expand_environment
from .session import _apply_settings
from ..provider_environment import materialize_bundle_providers
from ..session_files import amplifier_home
from ..shared_settings import read_settings
from ..updates import foundation_home

ROUTING_SOURCE = "git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main#subdirectory=modules/hooks-routing"
_CREDENTIAL = re.compile(r"^(?:api[_-]?key|access[_-]?token|refresh[_-]?token|github[_-]?token|token|password|secret|authorization|credential|client_secret)$", re.I)


def _environment_digest(value):
    references = {}
    def visit(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if not _CREDENTIAL.fullmatch(str(key)):
                    visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, str):
            for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}", node):
                references[name] = os.environ.get(name)
    visit(value)
    return hashlib.sha256(json.dumps(references, sort_keys=True).encode()).hexdigest()


def _routing_files(directories):
    # Track custom matrix contents as well as selection. The runtime digest
    # must reject a changed file behind an otherwise unchanged path on resume.
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for directory in directories for path in sorted(Path(directory).glob("*.yaml"))
            if path.is_file()}


def resolve_config(config, *, workspace, session_id):
    """Return an explicit model plan; never persist expanded credentials."""
    workspace = Path(workspace).expanduser().resolve(strict=True)
    root = amplifier_home()
    settings = read_settings(workspace, shared_home=root, session_id=session_id)
    _load_keys(root / "keys.env")
    result = copy.deepcopy(config)
    # Reuse the regular host's provider instances, overrides and routing policy.
    # Only model configuration crosses this boundary. Domain bundles, tools,
    # instructions, session limits and agent declarations remain caller-owned.
    selected = {key: copy.deepcopy(settings[key]) for key in
                ("routing", "overrides", "configurator", "sources") if key in settings}
    selected["config"] = {"providers": settings.get("config", {}).get("providers", [])}
    for section in ("config", "modules"):
        rows = settings.get(section, {}).get("hooks", [])
        selected.setdefault(section, {})["hooks"] = [copy.deepcopy(row) for row in rows if row.get("module") == "hooks-routing"]
    host = HostConfig(root, workspace, selected, root / "cache", root)
    existing = [copy.deepcopy(row) for row in result.get("hooks", []) if row.get("module") == "hooks-routing"]
    for hook in existing:
        hook.setdefault("source", ROUTING_SOURCE)
        # Opting into shared policy must not retain a private matrix or role
        # override merely because the corresponding shared setting is absent.
        for key in ("default_matrix", "overrides", "custom_routing_dirs"):
            hook.get("config", {}).pop(key, None)
    model = SimpleNamespace(providers=[], tools=[], session={}, agents={},
                            hooks=existing or [{"module": "hooks-routing", "source": ROUTING_SOURCE}])
    _apply_settings(model, host)
    if not model.providers:
        raise ValueError("No shared provider is enabled; configure config.providers in the shared Amplifier settings")
    if not model.hooks:
        raise ValueError("Shared runtime model routing requires hooks-routing to be enabled")
    result["providers"] = model.providers
    result["hooks"] = [row for row in result.get("hooks", []) if row.get("module") != "hooks-routing"] + model.hooks
    sources = result.setdefault("module_sources", {})
    model_modules = {row["module"] for row in model.providers + model.hooks}
    # An explicit module source must remain authoritative over a stale source
    # in the private runtime configuration, including the routing hook itself.
    for row in model.providers + model.hooks:
        if row.get("source"):
            sources[row["module"]] = row["source"]
    sources.update({key: expand_environment(value) for key, value in host.module_sources.items() if key in model_modules})
    directories = model.hooks[0]["config"]["custom_routing_dirs"]
    result["config_adapter_state"] = {"routing_dirs": directories, "routing_files": _routing_files(directories),
                                      "environment_sha256": _environment_digest(result["providers"])}
    return result


async def prepare_bundle(config, bundle, *, is_child=False):
    """Use normal module preparation and provider schemas, without model calls."""
    state = config["config_adapter_state"]
    if _routing_files(state["routing_dirs"]) != state["routing_files"]:
        raise ValueError("Shared routing files changed during manager startup; retry with a stable configuration")
    # Bundle composition merges provider lists. Replace that merged list so a
    # private bundle default cannot reintroduce an unselected provider.
    if not is_child:
        bundle.providers = copy.deepcopy(config["providers"])
    route = next(row for row in config["hooks"] if row.get("module") == "hooks-routing")
    bundle.hooks = [row for row in bundle.hooks if row.get("module") != "hooks-routing"] + [copy.deepcopy(route)]
    sources = config.get("module_sources", {})
    # Shared model settings do not grant the CLI registry/cache authority over
    # this host. Domain resources still resolve from their configured sources.
    registry_home = foundation_home(app_home())
    configure_skill_cache(bundle, registry_home)
    prepared = await bundle.prepare(cache_dir=registry_home / "cache", strict=True,
                                    source_resolver=lambda module, source: sources.get(module, source))
    await materialize_bundle_providers(bundle, prepared)
    return prepared
