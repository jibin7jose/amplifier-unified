"""Strict, schema-aware environment materialization for provider configs."""
from __future__ import annotations

import asyncio
import copy
import importlib
import importlib.metadata
import inspect
import json
import re

from .host.config import expand_environment
from .provider_failure_diagnostics import ProviderConfigurationError, diagnose

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")


def provider_class(module_id):
    """Find the public provider class after its source has been prepared."""
    module = None
    for entry in importlib.metadata.entry_points(group="amplifier.modules"):
        if entry.name == module_id:
            loaded = entry.load()
            module = importlib.import_module(loaded.__module__)
            break
    if module is None:
        module = importlib.import_module("amplifier_module_" + module_id.replace("-", "_"))
    candidates = [getattr(module, name) for name in dir(module)
                  if name.endswith("Provider") and not name.startswith("_")]
    candidates = [candidate for candidate in candidates
                  if inspect.isclass(candidate) and callable(getattr(candidate, "get_info", None))
                  and not getattr(candidate, "_is_protocol", False)]
    if not candidates:
        raise ValueError("No public provider setup class")
    return candidates[0]


def construct_provider(cls, config):
    """Use the established provider-probe constructor contract."""
    parameters = inspect.signature(cls).parameters
    kwargs = {key: config.get(key) for key in ("api_key", "github_token") if key in parameters}
    if "base_url" in parameters and "base_url" in config:
        kwargs["base_url"] = config["base_url"]
    if "config" in parameters:
        kwargs["config"] = config
    return cls(**kwargs)


async def close_provider(provider):
    close = getattr(provider, "close", None) or getattr(provider, "aclose", None)
    if callable(close):
        try:
            value = close()
            if inspect.isawaitable(value):
                await asyncio.wait_for(value, 3)
        except Exception:
            pass


async def config_schema(provider, *, info=None):
    """Read the public schema without making a model-list or inference call."""
    if info is None:
        info = provider.get_info()
        if inspect.isawaitable(info):
            info = await info
    schema_method = getattr(provider, "get_config_schema", None)
    schema = schema_method() if callable(schema_method) else {"fields": _value(info).get("config_fields", [])}
    if inspect.isawaitable(schema):
        schema = await schema
    return _value(schema)


def construct_schema_provider(cls, config, *, environment=None):
    """Read metadata with empty config and only an explicitly saved endpoint.

    Some providers require their endpoint as a separate constructor argument,
    even for offline metadata. Never invent one or pass saved credentials here.
    Missing references stay subject to the schema-aware materializer below.
    """
    kwargs = {}
    if "base_url" in inspect.signature(cls).parameters and isinstance(config, dict):
        try:
            endpoint = expand_environment(config.get("base_url"), environment=environment)
        except ValueError:
            endpoint = None
        if endpoint:
            kwargs["base_url"] = endpoint
    parameters = inspect.signature(cls).parameters
    kwargs.update({key: None for key in ("api_key", "github_token") if key in parameters})
    if "config" in parameters:
        kwargs["config"] = {}
    return cls(**kwargs)


async def provider_config_schema(module_id, *, config=None, environment=None):
    """Construct a metadata-only provider, then close it without model calls."""
    provider = construct_schema_provider(provider_class(module_id), config, environment=environment)
    try:
        return await config_schema(provider)
    finally:
        await close_provider(provider)


def materialize_provider_config(config, schema, *, environment=None):
    """Expand provider config strictly, blanking only declared optional nonsecrets."""
    if not isinstance(config, dict):
        raise ProviderConfigurationError("Provider configuration must be a mapping", category='configuration')
    fields = _schema_fields(schema)
    result = copy.deepcopy(config)
    values = environment
    for name, value in result.items():
        match = _ENV_REFERENCE.fullmatch(value) if isinstance(value, str) else None
        field = fields.get(name)
        if (match and field and field["required"] is False and field["field_type"] != "secret"
                and _environment_value(values, match.group(1)) is None):
            result[name] = ""
        else:
            try:
                result[name] = expand_environment(value, environment=values)
            except ValueError as exc:
                raise ProviderConfigurationError(str(exc), category='missing_environment') from exc
    for field in fields.values():
        if field["required"] is True and field["field_type"] == "secret" and not result.get(field["id"]):
            raise ProviderConfigurationError("provider_environment.required_secret:" + field["id"],
                                             category='missing_credential')
    return result


async def materialize_bundle_providers(bundle, prepared, *, environment=None, schema_loader=None, isolation=None):
    """Materialize instances without pruning; opt-in failures remain non-executable."""
    from .module_failures import ConfiguredModuleError

    root = copy.deepcopy({"providers": bundle.providers, "agents": bundle.agents})
    rows = [row for row in iter_provider_rows(root) if row.get("enabled", True)]
    def schema_key(row):
        config = row.get("config", {})
        endpoint = config.get("base_url") if isinstance(config, dict) else None
        # Only the explicit endpoint may affect metadata construction. Never
        # reuse a different account's endpoint or include other saved config.
        return row["module"], json.dumps(endpoint) if schema_loader is None else None

    async def load(key):
        module, endpoint = key
        if schema_loader is not None:
            schema = await schema_loader(module)
        else:
            schema = await provider_config_schema(module, config={"base_url": json.loads(endpoint)}, environment=environment)
        _schema_fields(schema)
        return schema

    rows = [row for row in rows if isolation is None or not isolation.failure(row)]
    keys = dict.fromkeys(schema_key(row) for row in rows)
    schemas = dict(zip(keys, await asyncio.gather(*(load(key) for key in keys), return_exceptions=True)))
    failures = []
    for row in rows:
        schema = schemas[schema_key(row)]
        reason = "provider_schema_failed"
        try:
            if isinstance(schema, BaseException):
                raise schema
            reason = "provider_configuration_failed"
            row["config"] = materialize_provider_config(row.get("config", {}), schema, environment=environment)
        except Exception as exc:
            if isolation is not None:
                isolation.record(row, reason, error=exc)
            failures.append({"module": row["module"], "type": "provider", "reason_code": reason,
                             "instance_id": row.get("instance_id") or row.get("id"),
                             "diagnostic": diagnose(exc, stage='schema' if reason == 'provider_schema_failed' else 'configuration')})
    if failures and isolation is None:
        raise ConfiguredModuleError(failures) from None
    bundle.providers, bundle.agents = root["providers"], root["agents"]
    prepared.mount_plan["providers"] = copy.deepcopy(bundle.providers)
    prepared.mount_plan["agents"] = copy.deepcopy(bundle.agents)
    return prepared


HOST_CREDENTIAL = {"$hostProviderCredential": True}


def credential_bindings(value, *, provider=False, module=None):
    """Retain references, not literal credentials, in inherited task snapshots."""
    from .runtime_controls import credential_field
    if isinstance(value, dict) and ("session" in value or "agents" in value):
        from .decision_accounts import logical_plan
        value = logical_plan(value)
    if isinstance(value, dict):
        if "module" in value:
            provider = str(value["module"]).startswith("provider-")
        result = {}
        for key, item in value.items():
            if credential_field(key, module=module):
                if item is None or isinstance(item, str) and _ENV_REFERENCE.fullmatch(item):
                    result[key] = item
                elif provider:
                    result[key] = copy.deepcopy(HOST_CREDENTIAL)
                # Other module secrets are not copied or rebound implicitly.
            else:
                result[key] = credential_bindings(
                    item, provider=provider, module=value.get("module") if key == "config" else None)
        return result
    if isinstance(value, list):
        return [credential_bindings(item, provider=provider) for item in value]
    return copy.deepcopy(value)


def rebind_provider_credentials(edited, authorized):
    """Resolve only the same host-composed instance/source and declared field.

    The saved marker grants no fallback to a module family's ambient account.
    Required-secret schema validation still runs after this in-memory resolution.
    """
    from .decision_accounts import logical_plan
    result = logical_plan(edited)
    authorized = logical_plan(authorized)
    bound = False

    def restore(value, original):
        if value == HOST_CREDENTIAL:
            if original is None or original == HOST_CREDENTIAL:
                raise ValueError("No authorized host credential binding for the inherited provider.")
            return copy.deepcopy(original)
        if isinstance(value, dict):
            return {key: restore(item, original.get(key) if isinstance(original, dict) else None)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [restore(item, original[i] if isinstance(original, list) and i < len(original) else None)
                    for i, item in enumerate(value)]
        return value

    def has_binding(value):
        if isinstance(value, dict):
            return value == HOST_CREDENTIAL or any(has_binding(item) for item in value.values())
        return isinstance(value, list) and any(has_binding(item) for item in value)

    def visit(node, host):
        nonlocal bound
        for row in node.get("providers", []):
            from .runtime_controls import credential_field
            # An explicit saved envref is already a credential binding; after
            # expansion it must remain a reference in the child's next snapshot.
            bound = bound or any(
                credential_field(key, module=row.get("module"))
                and isinstance(value, str) and _ENV_REFERENCE.fullmatch(value)
                for key, value in row.get("config", {}).items())
            if not has_binding(row.get("config", {})):
                continue
            key = row.get("instance_id") or row.get("id") or row["module"]
            matches = [old for old in host.get("providers", [])
                       if old.get("enabled", True) and old.get("module") == row["module"]
                       and (old.get("instance_id") or old.get("id") or old["module"]) == key
                       and old.get("source") == row.get("source")]
            if len(matches) != 1:
                raise ValueError("No authorized host credential binding for the inherited provider instance/source.")
            row["config"] = restore(row["config"], matches[0].get("config", {}))
            bound = True
        from .host.components import agent_roster
        for name, agent in agent_roster(node.get("agents", {})).items():
            if isinstance(agent, dict):
                visit(agent, agent_roster(host.get("agents", {})).get(name, {}))

    visit(result, authorized)
    return result, bound


def iter_provider_rows(node):
    """Yield root and nested provider declarations, validating their container shape."""
    if not isinstance(node, dict):
        raise ValueError("Provider declarations must be a mapping")
    rows = node.get("providers", [])
    if not isinstance(rows, list):
        raise ValueError("Provider declarations must be a list")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("module"), str):
            raise ValueError("Provider declarations need a module identifier")
        yield row
    from .host.components import agent_roster
    for agent in agent_roster(node.get("agents", {})).values():
        if isinstance(agent, dict):
            yield from iter_provider_rows(agent)


def _value(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    return value


def _schema_fields(schema):
    schema = _value(schema)
    if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        raise ValueError("provider_environment.unsupported_schema")
    fields = {}
    for field in schema["fields"]:
        if not isinstance(field, dict) or not isinstance(field.get("id"), str) or not field["id"]:
            raise ValueError("provider_environment.unsupported_schema")
        field_type = field.get("field_type")
        required = field.get("required")
        if (field_type not in ("text", "secret", "choice", "boolean")
                or not isinstance(required, bool) or field["id"] in fields):
            raise ValueError("provider_environment.unsupported_schema")
        fields[field["id"]] = field
    return fields


def _environment_value(environment, name):
    import os
    return (os.environ if environment is None else environment).get(name)
