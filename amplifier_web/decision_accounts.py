"""Project saved decision accounts onto package-owned Core hook modules.

The projection is transient. Persistence and child/resume credential binding use
the original logical provider declarations, never an additional account store.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .provider_isolation import identity

ACCOUNTS = "_decision_accounts"
ENVELOPE = "_decision_account"


@lru_cache(maxsize=1)
def _catalog_snapshot():
    # Worker dependencies are immutable for its lifetime. Discover once in a
    # separate interpreter, before any provider source is imported/authorized.
    # An absolute script also works when the app is bootstrapped, not installed
    # into the worker's environment. -I excludes cwd and injected PYTHONPATH.
    try:
        result = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("decision_account_catalog.py"))],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True,
                                env={name: value for name, value in os.environ.items()
                                     if name in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "TZ",
                                                 "TMPDIR", "TEMP", "TMP"}})
        if len(result.stdout) > 524288:
            return None
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def catalogs():
    value = _catalog_snapshot()
    if value is None:
        raise ValueError("Decision adapter metadata is unavailable in this worker")
    return copy.deepcopy(value)


def _hook(definition):
    return definition.get("module") if isinstance(definition, dict) else definition


def _applies(row, definition):
    config = row.get("config", {})
    return not isinstance(definition, dict) or isinstance(config, dict) and config.get("chat_enabled") is False


def logical_plan(value, *, definitions=None):
    """Remove only validated projection records; no credential lookup occurs here."""
    result = copy.deepcopy(value)
    def visit(node):
        nonlocal definitions
        if not isinstance(node, dict):
            return
        session = node.get("session", {})
        records = session.pop(ACCOUNTS, None) if isinstance(session, dict) else None
        hooks = node.get("hooks", [])
        projected = [row for row in hooks if isinstance(row, dict)
                     and ENVELOPE in (row.get("config") or {})]
        if records is None:
            if projected:
                raise ValueError("Decision hook has no authorized logical account declaration")
        else:
            if definitions is None:
                definitions = catalogs()
            if not isinstance(records, list):
                raise ValueError("Invalid logical decision account declarations")
            by_binding, keys = {}, set()
            for record in records:
                if not isinstance(record, dict) or set(record) != {"index", "provider", "binding_id"}:
                    raise ValueError("Invalid logical decision account declaration")
                row, binding = record["provider"], record["binding_id"]
                if (not isinstance(row, dict) or row.get("module") not in definitions
                        or not _applies(row, definitions[row.get("module")])
                        or not isinstance(binding, str) or not binding
                        or type(record["index"]) is not int or record["index"] < 0):
                    raise ValueError("Invalid logical decision account identity")
                key = identity(row)
                if binding in by_binding or key in keys:
                    raise ValueError("Duplicate logical decision account identity")
                by_binding[binding] = record
                keys.add(key)
            seen = set()
            for hook in projected:
                config = hook["config"]
                envelope = config[ENVELOPE]
                if not isinstance(envelope, dict) or set(envelope) != {
                        "provider_module", "instance_id", "source", "binding_id"}:
                    raise ValueError("Invalid decision account binding")
                binding = envelope["binding_id"]
                record = by_binding.get(binding)
                if record is None or binding in seen:
                    raise ValueError("Unknown or duplicate decision account binding")
                row = record["provider"]
                if (hook.get("module") != _hook(definitions[row["module"]])
                        or hook.get("source") != row.get("source")
                        or envelope != {"provider_module": row["module"], "instance_id": identity(row),
                                        "source": row.get("source"), "binding_id": binding}
                        or {k: v for k, v in config.items() if k != ENVELOPE} != row.get("config", {})):
                    raise ValueError("Decision hook does not match its logical account/source")
                seen.add(binding)
            node["hooks"] = [row for row in hooks if row not in projected]
            providers = node.setdefault("providers", [])
            if keys.intersection(identity(row) for row in providers):
                raise ValueError("Duplicate logical decision account identity")
            for record in sorted(records, key=lambda row: row["index"]):
                providers.insert(min(record["index"], len(providers)), record["provider"])
        # Child overlays also use "none" or a name list as inheritance selectors.
        # Only declaration mappings contain nested account projections.
        agents = node.get("agents", {})
        if isinstance(agents, dict):
            for child in agents.values():
                visit(child)

    visit(result)
    return result


def bind_sources(plan, sources):
    """Freeze explicit host source overrides in logical account declarations."""
    definitions = catalogs()
    def visit(node):
        for row in node.get("providers", []):
            if (row.get("module") in definitions and _applies(row, definitions[row["module"]])
                    and sources.get(row["module"])):
                row["source"] = sources[row["module"]]
        from .host.components import agent_roster
        for child in agent_roster(node.get("agents", {})).values():
            if isinstance(child, dict):
                visit(child)
    visit(plan)


def check_child_sources(parent, overlay):
    """A same-account overlay cannot carry inherited credentials to another source."""
    definitions = catalogs()
    for row in overlay.get("providers", []):
        for inherited in parent.get("providers", []):
            if identity(row) != identity(inherited):
                continue
            if not any(candidate.get("module") in definitions
                       and _applies(candidate, definitions[candidate["module"]])
                       for candidate in (row, inherited)):
                continue
            if (row.get("module") != inherited.get("module")
                    or row.get("source", inherited.get("source")) != inherited.get("source")):
                raise ValueError("A child decision account cannot change its inherited account source")


def restore_child_accounts(plan, retained, authorized):
    """Resume the saved decision choices, binding only the same current account."""
    from .provider_environment import rebind_provider_credentials
    definitions = catalogs()
    def selected(row):
        return row.get("module") in definitions and _applies(row, definitions[row["module"]])
    saved = [row for row in logical_plan(retained).get("providers", []) if selected(row)]
    current = [row for row in logical_plan(authorized).get("providers", []) if selected(row)]
    for row in saved:
        matches = [candidate for candidate in current
                   if candidate.get("enabled", True) and identity(candidate) == identity(row)
                   and candidate["module"] == row["module"] and candidate.get("source") == row.get("source")]
        if len(matches) != 1:
            raise ValueError("The saved child decision account/source is no longer authorized")
    rebound, _ = rebind_provider_credentials({"providers": saved}, {"providers": current})
    plan["providers"] = [row for row in plan.get("providers", []) if not selected(row)] + rebound["providers"]
    return plan


class AccountResolver:
    """Resolve a hook from its already-prepared exact provider package source."""
    def __init__(self, resolver, aliases):
        self.resolver, self.aliases = resolver, aliases

    def __getattr__(self, name):
        return getattr(self.resolver, name)

    def _module(self, module, source):
        if module not in {name for name, _ in self.aliases}:
            return None
        if not isinstance(source, str) or (module, source) not in self.aliases:
            raise ValueError("Decision hook source does not match its authorized account")
        return self.aliases[module, source]

    def resolve(self, module_id, source_hint=None, profile_hint=None):
        source = profile_hint if profile_hint is not None else source_hint
        module = self._module(module_id, source)
        if module is None:
            return self.resolver.resolve(module_id, source_hint=source)
        try:
            return _SelectedSource(self.resolver.resolve(module, source_hint=source).resolve())
        except Exception:
            raise ValueError("The authorized decision hook source is unavailable") from None

    async def async_resolve(self, module_id, source_hint=None, profile_hint=None):
        source = profile_hint if profile_hint is not None else source_hint
        module = self._module(module_id, source)
        if module is None:
            return await self.resolver.async_resolve(module_id, source_hint=source)
        try:
            selected = await self.resolver.async_resolve(module, source_hint=source)
            return _SelectedSource(selected.resolve())
        except Exception:
            # Core treats SourceNotFoundError as permission to use a globally
            # installed entrypoint. A bound account never grants that fallback.
            raise ValueError("The authorized decision hook source is unavailable") from None


class _SelectedSource:
    def __init__(self, path):
        self.path = path

    def resolve(self):
        return self.path

    def __str__(self):
        return str(self.path)


class DecisionAccounts:
    def __init__(self, plan, isolation, source_resolver, *, definitions=None):
        self.definitions = catalogs() if definitions is None else definitions
        self.isolation = isolation
        self.expected, self.outcomes, self.aliases = {}, {}, {}
        self.failures = []
        self.invalid_receipt = False
        self.plan = logical_plan(plan, definitions=self.definitions)
        self.records = []
        ordinary, hooks = [], self.plan.setdefault("hooks", [])
        for index, original in enumerate(self.plan.get("providers", [])):
            if (original.get("module") not in self.definitions
                    or not _applies(original, self.definitions[original.get("module")])):
                ordinary.append(original)
                continue
            row = copy.deepcopy(original)
            module, hook = row["module"], _hook(self.definitions[row["module"]])
            source = source_resolver(module, row.get("source"))
            row["source"] = source
            binding = uuid.uuid4().hex
            self.records.append({"index": index, "provider": row, "binding_id": binding})
            if any(identity(item["provider"]) == identity(row) for item in self.records[:-1]):
                raise ValueError("Duplicate logical decision account identity")
            if row.get("enabled", True) is False:
                continue
            failure = isolation.failure(original) if isolation else None
            if failure:
                self.failures.append(copy.deepcopy(failure))
                continue
            config = row.get("config", {})
            decisions = config.get("decisions") or {} if isinstance(config, dict) else None
            if not isinstance(decisions, dict):
                self._failure(row, ValueError("Decision account configuration must be an object"))
                continue
            if decisions.get("enabled") is not True:
                continue
            if not hook or not isinstance(source, str) or not source:
                self._failure(row, ValueError("Install a decision adapter with a supported hook entrypoint"))
                continue
            if source_resolver(hook, source) != source:
                raise ValueError("Decision hook source differs from its authorized account")
            if any(item.get("module") == hook and ENVELOPE not in (item.get("config") or {}) for item in hooks):
                raise ValueError("A decision account hook is already configured independently")
            envelope = {"provider_module": module, "instance_id": identity(row),
                        "source": source, "binding_id": binding}
            hooks.append({"module": hook, "source": source,
                          "config": {**copy.deepcopy(row.get("config", {})), ENVELOPE: envelope}})
            self.expected[binding] = (hook, row)
            self.aliases[(hook, source)] = module
        if self.records:
            self.plan["providers"] = ordinary
            self.plan.setdefault("session", {})[ACCOUNTS] = self.records

    def _failure(self, row, error):
        from .provider_failure_diagnostics import diagnose
        failure = (self.isolation.record(row, "provider_mount_failed", error=error) if self.isolation else
                   {"module": row["module"], "instance_id": identity(row), "type": "provider",
                    "reason_code": "provider_mount_failed", "diagnostic": diagnose(error, stage="mount")})
        self.failures.append(copy.deepcopy(failure))

    async def before_initialize(self, session):
        async def lifecycle(binding_id, hook_module, outcome, error=None):
            expected = self.expected.get(binding_id)
            if expected is None or expected[0] != hook_module or binding_id in self.outcomes:
                self.invalid_receipt = True
                raise ValueError("Unknown or repeated decision account lifecycle receipt")
            if outcome not in {"ready", "disabled", "unavailable", "cancelled", "unsafe"}:
                self.invalid_receipt = True
                raise ValueError("Unknown decision account lifecycle outcome")
            self.outcomes[binding_id] = outcome
            if outcome == "unavailable":
                self._failure(expected[1], error)
                return True  # Explicit policy, only after the hook completed rollback.
            return None
        session.coordinator.register_capability("decision.account_lifecycle", lifecycle)

    def require_complete(self):
        from .module_failures import ConfiguredModuleError
        failures = []
        for binding, (_, row) in self.expected.items():
            if self.invalid_receipt or self.outcomes.get(binding) not in {"ready", "disabled", "unavailable"}:
                failures.append({"module": row["module"], "instance_id": identity(row),
                                 "type": "provider", "reason_code": "provider_mount_failed"})
        if self.invalid_receipt and not failures and self.records:
            row = self.records[0]["provider"]
            failures.append({"module": row["module"], "instance_id": identity(row),
                             "type": "provider", "reason_code": "provider_mount_failed"})
        if failures:
            raise ConfiguredModuleError(failures)
