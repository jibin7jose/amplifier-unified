"""Application controls on public Core/Foundation interfaces, shared by UI and agent."""
from __future__ import annotations

import asyncio
import copy
from contextlib import nullcontext
from dataclasses import asdict, is_dataclass
import inspect
import json
from pathlib import Path
import re
import subprocess
import uuid

from .host.config import app_home, write_private

REDACTED = "[REDACTED]"
SECTIONS = ("providers", "tools", "hooks")
PLAN_KEYS = {*SECTIONS, "session", "agents", "spawn", "context", "instruction"}


def credential_field(key, *, module=None):
    """Classify a configuration field with its owning module, never a bare key."""
    return (any(term in str(key).lower() for term in
                ("api_key", "secret", "password", "authorization", "cookie",
                 "access_token", "refresh_token", "github_token", "copilot_agent_token"))
            or module == "tool-team-pulse" and key == "key")


def public_config(value, *, module=None, metadata=False):
    if is_dataclass(value):
        value = asdict(value)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): REDACTED if credential_field(key, module=module)
                else public_config(item, module=value.get("module") if key == "config" else None,
                                   metadata=metadata)
                for key, item in value.items()
                if key not in {"_decision_account", "_decision_accounts"}
                and not (metadata and value.get("field_type") == "secret" and key in {"default", "value"})}
    if isinstance(value, (list, tuple)):
        return [public_config(item, metadata=metadata) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def identity(row):
    return row.get("instance_id") or row.get("id") or row.get("module")


_MISSING_SETTING = object()


def restore_redactions(value, old, *, _config=False):
    if value == REDACTED:
        if old is _MISSING_SETTING:
            raise ValueError("A redacted value cannot be used for a new setting")
        return copy.deepcopy(old)
    if isinstance(value, dict):
        if not _config and (isinstance(value.get("module"), str)
                            or isinstance(old, dict) and isinstance(old.get("module"), str)) and (
                not isinstance(old, dict) or identity(value) != identity(old)
                or value.get("module") != old.get("module")
                or value.get("source") != old.get("source")):
            old = _MISSING_SETTING
        return {key:restore_redactions(item, old.get(key, _MISSING_SETTING)
                                      if isinstance(old, dict) else _MISSING_SETTING,
                                      _config=_config or key == "config" and isinstance(value.get("module"), str))
                for key,item in value.items()}
    if isinstance(value, list):
        # Only module collections can reorder by identity. Arrays inside a
        # matched configuration retain their structural positions.
        if _config or not any(isinstance(row, dict) and "module" in row
                   for row in [*value, *(old if isinstance(old, list) else [])]):
            return [restore_redactions(row, old[i] if isinstance(old, list) and i < len(old)
                                       else _MISSING_SETTING, _config=_config) for i, row in enumerate(value)]
        def previous(row):
            matches = [item for item in old if isinstance(item, dict)
                       and identity(item) == identity(row)
                       and item.get("module") == row.get("module")
                       and item.get("source") == row.get("source")] if isinstance(old, list) and identity(row) else []
            return matches[0] if len(matches) == 1 else _MISSING_SETTING
        return [restore_redactions(row, previous(row) if isinstance(row, dict) else _MISSING_SETTING) for row in value]
    return copy.deepcopy(value)


def override_path(session_id):
    # Session IDs originate in Core or the application; reject path syntax here
    # as well because this helper owns a private configuration file.
    if not session_id or Path(session_id).name != session_id or session_id in {".", ".."}:
        raise ValueError("Invalid session identity")
    return app_home() / "sessions" / session_id / "configuration.json"


def validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError("Configuration must be a mount-plan object")
    for section in SECTIONS:
        rows = plan.get(section, [])
        if not isinstance(rows, list):
            raise ValueError(f"{section} must be a module list")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("module"), str) or not row["module"]:
                raise ValueError(f"Each {section} entry needs a module name")
            key = identity(row)
            if key in seen:
                raise ValueError(f"Duplicate module instance: {key}")
            seen.add(key)
            if not isinstance(row.get("config", {}), dict) or not isinstance(row.get("enabled", True), bool):
                raise ValueError("Module config must be an object and enabled a boolean")
    session = plan.get("session", {})
    if not isinstance(session, dict) or not isinstance(plan.get("agents", {}), dict):
        raise ValueError("Session and agents must be objects")
    loop = session.get("orchestrator", {}).get("module")
    if loop not in {"loop-live", "loop-streaming"}:
        raise ValueError("The main session requires loop-live or loop-streaming")
    if not session.get("context", {}).get("module"):
        raise ValueError("A context module is required")
    if not any(row.get("enabled", True) for row in plan.get("providers", [])):
        raise ValueError("At least one provider must remain enabled")


class RuntimeControls:
    def __init__(self, session, runtime, telemetry=None, *, preserve_configuration=False, tool_invocation=None):
        self.session, self.runtime, self.telemetry = session, runtime, telemetry
        self.preserve_configuration = preserve_configuration
        self._restoring = False
        self._tool_invocation = tool_invocation
        self.coordinator = session.coordinator
        self.configurator = self.coordinator.get_capability("web.configurator")
        self.prepared = self.coordinator.get_capability("web.prepared")
        self.agent_definitions = copy.deepcopy({
            **(self.coordinator.get_capability("web.agent_definitions") or {}),
            **self.coordinator.config.get("agents", {})})
        self.lock = asyncio.Lock()
        self.selection = None
        # Session preparation has already applied the new-chat root choice.
        # Adopt that explicit choice before readback/persistence; a budget-only
        # wrapper is still automatic provider selection. Saved controls below
        # take precedence, including an explicit reset to bundle defaults.
        from .host.session import SelectedProvider
        root_provider = getattr(self.coordinator.get("orchestrator"), "root_provider", None)
        if (isinstance(root_provider, SelectedProvider)
                and root_provider.selection.get("instance") and root_provider.selection.get("model")):
            self.selection = {key:copy.deepcopy(root_provider.selection[key])
                              for key in ("instance", "model", "effort") if key in root_provider.selection}
        self.selection_cleared = False
        self.max_output_tokens = None
        self.logins = {}
        self.kernels = None
        self.kernel_install = None
        from .provider_catalog import ProviderCatalog
        self.model_catalog=ProviderCatalog()
        self.catalog_revision=str(uuid.uuid4())
        self.coordinator.register_capability("web.controls.persist", self.persist)
        from .task_continuity import TaskController
        self.tasks = TaskController(self)
        from .capacity import CapacityController
        self.capacity = CapacityController(self)

    async def close(self):
        if self.kernels:
            await self.kernels.shutdown()
        await self.model_catalog.close()
        tasks = [row["task"] for row in self.logins.values() if not row["task"].done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)

    def state_path(self):
        return override_path(self.session.session_id).with_name("control-state.json")

    async def restore(self, *, selection_override=None):
        if not self.state_path().exists():
            return
        saved = json.loads(self.state_path().read_text())
        # A cold mount initially contains module defaults. Intermediate task,
        # budget and provider restoration must not publish that partial state.
        # On failure/cancellation retain the original controls for recovery.
        self._restoring = True
        try:
            await self._restore_saved(saved, selection_override=selection_override)
        finally:
            self._restoring = False
        self.persist()

    async def _restore_saved(self, saved, *, selection_override=None):
        if self.configurator:
            await self.configurator.apply_saved_settings(saved.get("configurator", {}))
        self.coordinator.session_state["goal"] = saved.get("goal")
        self.coordinator.session_state["task"] = saved.get("task")
        self.tasks.receipts = saved.get("taskReceipts", {})
        self.tasks.history = saved.get("taskHistory", [])
        self.capacity.policy.update(saved.get("capacity", {}))
        self.capacity.receipts = saved.get("capacityReceipts", {})
        self.capacity.last_denial = saved.get("capacityLastDenial")
        task = self.tasks.record()
        if task:
            task['appliedRevision'] = None
            self.tasks.apply()
        # Older snapshots recorded None for module-managed/automatic limits.
        # Absence of an override must leave the newly mounted module's default
        # intact, rather than passing null to the explicit budget-edit API.
        budget={key:value for key,value in saved.get("budget",{}).items() if value is not None}
        if budget:
            await self._perform("budget.set", budget)
        if selection_override is not None:
            await self._perform("provider.select", selection_override)
        elif saved.get("selection"):
            await self._perform("provider.select", saved["selection"])
        elif "selection" in saved:
            await self._perform("provider.reset", {})
        if saved.get("mode"):
            await self.mode("mode.set", {"name":saved["mode"]})

    def persist(self, *, task_only=False):
        if self.preserve_configuration or self._restoring:
            return  # A scoped placement mount restores controls without rewriting them.
        self.tasks.retain_live_goal()
        snapshot = self.configurator.snapshot() if self.configurator else {}
        loop, context = self.coordinator.get("orchestrator"), self.coordinator.get("context")
        budget = {key:getattr(target, attr) for key,target,attr in
                  (("maxIterations",loop,"max_iterations"),("contextTokens",context,"max_tokens")) if getattr(target,attr,None) is not None}
        if self.max_output_tokens is not None:
            budget["maxOutputTokens"] = self.max_output_tokens
        previous = json.loads(self.state_path().read_text()) if self.state_path().exists() else {}
        write_private(self.state_path(), json.dumps({"configurator":{"disabled":{key:row.get("disabled",[]) for key,row in snapshot.items()}},
            "capacity":self.capacity.policy,"capacityReceipts":self.capacity.receipts,"capacityLastDenial":self.capacity.last_denial,
            "goal":self.coordinator.session_state.get("goal"),"task":self.coordinator.session_state.get("task"),"taskReceipts":self.tasks.receipts,"taskHistory":self.tasks.history,"mode":self.coordinator.session_state.get("active_mode"),
            "selection":None if self.selection_cleared else self.selection or previous.get("selection"),"budget":budget},default=str))
        if task_only:
            return
        plan = {key:value for key,value in self.coordinator.config.items() if key in PLAN_KEYS}
        disabled_agents = snapshot.get("agents", {}).get("disabled", [])
        if disabled_agents:
            # This is a restorable configuration, not the live availability
            # map. Never temporarily enable an agent or persist a redacted
            # inspector summary in place of its exact declaration.
            plan["agents"] = dict(plan.get("agents", {}))
            for name in disabled_agents:
                if name not in self.agent_definitions:
                    raise ValueError(f"Disabled agent {name!r} has no retained definition")
                plan["agents"][name] = self.agent_definitions[name]
        from .decision_accounts import logical_plan
        plan = logical_plan(plan)
        if self.coordinator.get_capability("web.provider_credentials_bound"):
            from .provider_environment import credential_bindings
            plan = credential_bindings(plan)
        write_private(self.state_path().with_name("effective-configuration.json"),json.dumps(plan,default=str))

    def require_idle(self):
        if getattr(self.runtime, "generation", None) or getattr(self.runtime, "queued_inputs", 0):
            raise ValueError("Wait for the current response before changing session configuration")
        loop = self.coordinator.get("orchestrator")
        if any(not job["task"].done() for job in getattr(loop, "jobs", {}).values()):
            raise ValueError("Wait for delegated work to finish before changing session configuration")
        children = self.coordinator.get_capability("live.children")
        if children and any(row.get("status") in {"starting", "running", "working", "idle"} for row in children.rows.values()):
            raise ValueError("Finish or stop the active workers before changing session configuration")

    def configuration(self):
        from .decision_accounts import logical_plan
        plan = logical_plan(self.coordinator.config)
        stored = override_path(self.session.session_id)
        saved = json.loads(stored.read_text()) if stored.exists() else None
        if saved:
            for section in SECTIONS:
                disabled = [row for row in saved.get(section, []) if row.get("enabled") is False]
                current = {identity(row) for row in plan.get(section, [])}
                plan.setdefault(section, []).extend(row for row in disabled if identity(row) not in current)
        snapshot = self.configurator.snapshot() if self.configurator else {}
        modules = []
        for section in SECTIONS:
            for row in plan.get(section, []):
                key = identity(row)
                enabled = row.get("enabled", True) and key not in snapshot.get(section, {}).get("disabled", [])
                row["enabled"] = enabled
                modules.append({"section": section, "module": row["module"], "id": key, "enabled": enabled,
                                "source": row.get("source"), "config": public_config(row.get("config", {}), module=row["module"])})
        provenance = {}
        if self.configurator:
            for section in ("tools", "providers", "hooks", "agents", "context", "behaviors"):
                method = getattr(self.configurator, section + "_list", None)
                if method:
                    provenance[section] = public_config(method())
        return {"bundle": plan.get("bundle_name"), "plan": public_config(plan), "modules": modules,
            "disabledModules": {key:value.get("disabled", []) for key,value in snapshot.items()},
            "provenance": provenance, "capabilities": self.capabilities()}

    def capabilities(self):
        return {"configuration": True, "configurationApplyRequiresRestart": True,
            "modes": bool(self.coordinator.session_state.get("mode_discovery")),
            "goals": hasattr(self.coordinator.get("orchestrator"), "goal_stall_threshold"),
            "skills": bool(self.coordinator.get_capability("skills_discovery")),
            "tools": True, "usage": self.telemetry is not None}

    async def checkpoint(self):
        self.persist()
        callback = self.coordinator.get_capability("live.checkpoint")
        if callback:
            await callback()

    async def perform(self, operation, args=None):
        args = args or {}
        if not isinstance(args, dict):
            raise ValueError("Control arguments must be an object")
        if operation.startswith("kernels."):
            if self.kernels is None:
                from .computation import install
                if self.kernel_install is None:
                    self.kernel_install = asyncio.create_task(install(self))
                self.kernels = await asyncio.shield(self.kernel_install)
            arguments = {key: value for key, value in args.items() if key != "actor"}
            arguments["action"] = operation.removeprefix("kernels.")
            return await self.invoke({"name": "compute", "arguments": arguments},
                bound_arguments=arguments, checkpoint=False,
                provenance={"source": operation, "actor": args.get("actor", "ui")})
        if operation == "operations.submit":
            arguments = {"action": "start", "command": args.get("command"),
                         "question_ids": args.get("questionIds", []),
                         **{key: args[key] for key in ("timeout", "pty") if key in args}}
            return await self.invoke({"name": "bash", "arguments": arguments},
                bound_arguments=arguments, checkpoint=False,
                integration_id=args.get('integrationId'),
                provenance={"source": operation, "actor": args.get("actor", "ui")})
        if operation in {"operations.cancel", "operations.write"}:
            coordinator = self.coordinator
            target = args.get("runtimeSessionId")
            if target != self.session.session_id:
                children = coordinator.get_capability("live.children")
                child = children.sessions.get(target) if children else None
                if child is None:
                    raise ValueError("The operation's owning session is no longer mounted")
                coordinator = child.coordinator
            arguments = {"action": "terminate" if operation == "operations.cancel" else "write", "process_id": args.get("processId")}
            if operation == "operations.write":
                arguments.update(stdin=args.get("stdin", ""), close_stdin=args.get("closeStdin", False))
            return await self.invoke({"name": "bash", "arguments": arguments},
                coordinator=coordinator, bound_arguments=arguments,
                expected_process_owner=(args.get("processId"), args.get("ownerId")),
                provenance={"source": operation, "actor": args.get("actor", "ui"),
                            "operation_id": args.get("operationId")}, checkpoint=False)
        async with self.lock:
            return await self._perform(operation, args)

    def prepare_configuration(self, config):
        from .decision_accounts import logical_plan
        previous = logical_plan(self.coordinator.config)
        stored = override_path(self.session.session_id)
        if stored.exists():
            saved = json.loads(stored.read_text())
            for section in SECTIONS:
                current = {identity(row) for row in previous.get(section, [])}
                previous.setdefault(section, []).extend(
                    row for row in saved.get(section, []) if identity(row) not in current)
        plan = restore_redactions(config, previous)
        validate_plan(plan)
        return {key: value for key, value in plan.items() if key in PLAN_KEYS}

    def save_configuration(self, plan):
        write_private(override_path(self.session.session_id), json.dumps(plan, indent=2))
        # A full mount-plan edit supersedes previous live module toggles.
        if self.state_path().exists():
            controls = json.loads(self.state_path().read_text())
            disabled = controls.get('configurator', {}).get('disabled', {})
            for section in SECTIONS:
                disabled.pop(section, None)
            write_private(self.state_path(), json.dumps(controls))

    def prepare_patch_placement(self, payload):
        from .patch_placement import prepare
        return prepare(self, payload)

    def save_patch_placement(self, plan):
        # Field-only repair must not clear toggles, provider selection or other
        # control-state. The existing operation receipt owns save uncertainty.
        write_private(override_path(self.session.session_id), json.dumps(plan, indent=2))

    async def _perform(self, operation, args):
        if operation == 'memory.consolidate':
            from .memory_consolidation import generate
            return await generate(self, args)
        if operation.startswith("capacity."):
            return await self.capacity.perform(operation, args)
        if operation.startswith('task.'):
            return await self.tasks.perform(operation, args)
        if operation in {"native.status", "native.compact"}:
            native = self.coordinator.get_capability("web.native_provider")
            if native is None:
                return {"supported": False, "steering": "request_boundary", "reason": "Native transport is unavailable in this runtime."}
            if operation == "native.status":
                return native.status()
            self.require_idle()
            result = await native.compact()
            await self.checkpoint()
            return result
        if operation == "configuration.inspect":
            return self.configuration()
        if operation == "history.snapshot":
            self.require_idle()
            await self.checkpoint()
            return {"messages":await self.coordinator.get("context").get_messages()}
        if operation == "configuration.exportResources":
            return self.export_resources()
        if operation in {"configuration.providers", "configuration.providerModels", "configuration.providerTest", "configuration.providerLogin"}:
            return await self.provider_control(operation,args)
        if operation == "configuration.apply":
            self.require_idle()
            plan = self.prepare_configuration(args.get("config"))
            self.save_configuration(plan)
            return {"requiresRestart": True, "configuration": public_config(plan)}
        if operation == "configuration.toggle":
            self.require_idle()
            section, name, enabled = args.get("section"), args.get("name"), args.get("enabled")
            if section not in {"tools", "providers", "agents", "context"} or not isinstance(enabled, bool):
                raise ValueError("Live toggles support tools, providers, agents, and context; edit hooks with configuration.apply")
            if not self.configurator:
                raise ValueError("Session configurator is unavailable")
            if section == "providers" and not enabled and len(self.coordinator.get("providers")) <= 1:
                raise ValueError("At least one provider must remain enabled")
            singular = {"tools":"tool", "providers":"provider", "agents":"agent", "context":"context"}[section]
            suffix = "_enable" if enabled else "_disable"
            if section == "tools" and any(identity(row)==name for row in self.coordinator.config.get("tools",[])):
                function = getattr(self.configurator,"tool" + suffix + "_module")
            else:
                function = getattr(self.configurator, singular + suffix)
            if section == "agents" and not enabled and name in self.coordinator.config.get("agents", {}):
                # Retain runtime edits/additions, not only the initial bundle.
                self.agent_definitions[name] = copy.deepcopy(self.coordinator.config["agents"][name])
            result = function(name)
            if inspect.isawaitable(result):
                await result
            await self.checkpoint()
            return self.configuration()
        if operation == "catalog.inspect":
            discovery = self.coordinator.get_capability("skills_discovery")
            skills = discovery.list_skills() if discovery else []
            return {"tools": [{"name":name,"description":getattr(tool,"description", ""),
                                "inputSchema":public_config(getattr(tool,"input_schema", {}))}
                               for name,tool in (self.coordinator.get("tools") or {}).items()],
                    "agents": public_config(self.coordinator.config.get("agents", {})),
                    "skills": [{"name":row[0],"description":row[1] if len(row)>1 else ""} for row in skills],
                    "capabilities": self.capabilities()}
        if operation == "usage.inspect":
            result = self.telemetry.usage() if self.telemetry else {"calls": 0, "usage": {"costType":"unavailable"}, "trace": []}
            messages = await self.coordinator.get("context").get_messages()
            result.update(messages=len(messages), userTurns=sum(row.get("role")=="user" for row in messages),
                          contextCharacters=sum(len(str(row.get("content", ""))) for row in messages))
            return result
        if operation == "context.clear":
            self.require_idle()
            await self.coordinator.get("context").clear()
            self.coordinator.session_state["goal"] = None
            await self.checkpoint()
            return {"cleared":True}
        if operation.startswith("mode."):
            return await self.mode(operation, args)
        if operation in {"goals.get", "goals.set", "goals.clear"}:
            if operation != "goals.get":
                if self.tasks.record() and self.tasks.record()["status"] != "completed":
                    raise ValueError("Use the saved task controls to update or pause its goal")
                self.require_idle()
                if not self.capabilities()["goals"]:
                    raise ValueError("This orchestrator does not support goal continuation")
                condition = args.get("condition")
                cap = args.get("maxTurns")
                if operation == "goals.set" and (not isinstance(condition, str) or not condition.strip()):
                    raise ValueError("A nonempty goal condition is required")
                if cap is not None and (type(cap) is not int or cap < 1):
                    raise ValueError("maxTurns must be a positive integer or null")
                self.coordinator.session_state["goal"] = None if operation == "goals.clear" else {
                    "condition":condition,"cap":cap,"turns_used":0,"last_reason":None,"reasons":[],"continuations":0}
                await self.checkpoint()
            return {"goal":public_config(self.coordinator.session_state.get("goal"))}
        if operation in {"budget.get", "budget.set"}:
            loop = self.coordinator.get("orchestrator")
            context = self.coordinator.get("context")
            if operation == "budget.set":
                self.require_idle()
                if "maxOutputTokens" in args and (type(args["maxOutputTokens"]) is not int or args["maxOutputTokens"] < 1):
                    raise ValueError("maxOutputTokens must be a positive integer")
                for key, target, attr in (("maxIterations",loop,"max_iterations"),("contextTokens",context,"max_tokens")):
                    if key in args:
                        value = args[key]
                        if type(value) is not int or (value < 1 and not (key == "maxIterations" and value == -1)):
                            raise ValueError(f"{key} must be a positive integer" + (" or -1 for unlimited" if key == "maxIterations" else ""))
                        if not hasattr(target, attr):
                            raise ValueError(f"The mounted module does not expose {key}")
                if "maxOutputTokens" in args:
                    from .host.session import SelectedProvider
                    providers = self.coordinator.get("providers") or {}
                    current = (getattr(loop,"root_provider",None) or loop._select_provider(providers)) if providers else None
                    if current is None:
                        raise ValueError("No provider is available for an output token limit")
                for key, target, attr in (("maxIterations",loop,"max_iterations"),("contextTokens",context,"max_tokens")):
                    if key in args:
                        setattr(target, attr, args[key])
                if "maxOutputTokens" in args:
                    selection = dict(current.selection) if isinstance(current,SelectedProvider) else {}
                    original = current.original if isinstance(current,SelectedProvider) else current
                    selection["max_output_tokens"] = args["maxOutputTokens"]
                    loop.root_provider = SelectedProvider(original,selection,self.coordinator.get_capability('web.provider_transform'))
                    self.max_output_tokens = args["maxOutputTokens"]
                self.persist()
            return {"maxIterations":getattr(loop,"max_iterations",None), "contextTokens":getattr(context,"max_tokens",None),
                    "maxOutputTokens":self.max_output_tokens,
                    "scope":"current session", "contextNote":"The provider context window can further constrain the effective budget."}
        if operation == "provider.reset":
            self.require_idle()
            from .host.session import SelectedProvider
            loop = self.coordinator.get('orchestrator')
            previous = getattr(loop, 'root_provider', None)
            loop.root_provider = None
            try:
                providers = self.coordinator.get('providers') or {}
                if providers:
                    from .provider_isolation import require_selected
                    require_selected(self.coordinator)
                if self.max_output_tokens is not None and providers:
                    automatic = loop._select_provider(providers)
                    if automatic is not None:
                        loop.root_provider = SelectedProvider(automatic, {'max_output_tokens': self.max_output_tokens},self.coordinator.get_capability('web.provider_transform'))
            except Exception:
                loop.root_provider = previous
                raise
            self.selection=None
            self.selection_cleared=True
            self.persist()
            return {'selection':None,'scope':'main session'}
        if operation == "provider.select":
            self.require_idle()
            from .host.session import SelectedProvider
            providers = self.coordinator.get("providers") or {}
            args = {**args,"instance":args.get("instance") or args.get("provider")}
            if args.get("instance") not in providers or not isinstance(args.get("model"), str) or not args["model"].strip():
                from .host.model_selection import ProviderSelectionError
                raise ProviderSelectionError("Select an available provider instance and model")
            from .provider_isolation import require_available
            require_available(self.coordinator, args["instance"])
            selected = {key:args[key] for key in ("instance","model","effort") if key in args}
            effective = {**selected, **({"max_output_tokens":self.max_output_tokens} if self.max_output_tokens else {})}
            self.coordinator.get("orchestrator").root_provider = SelectedProvider(providers[args["instance"]], effective,self.coordinator.get_capability('web.provider_transform'))
            self.selection = selected
            self.selection_cleared = False
            self.persist()
            return {"selection":selected,"scope":"main session"}
        if operation == "tool.invoke":
            self.require_idle()
            return await self.invoke(args, generic=True,
                provenance={"source": "tool.invoke", "actor": args.get("actor", "ui")})
        raise ValueError(f"Unsupported runtime control: {operation}")

    async def provider_control(self, operation, args):
        providers = self.coordinator.get("providers") or {}
        if operation == "configuration.providers":
            from .provider_isolation import UnavailableProvider
            rows = []
            for name,provider in providers.items():
                info = provider.get_info()
                if inspect.isawaitable(info):
                    info = await info
                schema = getattr(provider,"get_config_schema",None)
                schema = schema() if callable(schema) else {"fields":getattr(info,"config_fields",[])}
                if inspect.isawaitable(schema):
                    schema = await schema
                from .provider_catalog import fingerprint
                mounted=next((row for row in getattr(self.session,'config',{}).get('providers',[]) if (row.get('id') or row.get('instance_id') or row.get('module','').removeprefix('provider-'))==name),{})
                catalog_key=fingerprint([self.catalog_revision,name,mounted or getattr(provider,'config',{}),public_config(info, metadata=True)])
                shared={}
                captured=self.coordinator.get_capability('web.provider_catalog')
                if mounted and isinstance(captured,dict) and name in captured.get('keys',{}):
                    from .provider_catalog import configuration_key
                    current=configuration_key(captured['workspace'],mounted['module'],mounted.get('config',{}),captured['sources'][name],home=captured['home'])
                    if current==captured['keys'][name]:shared['sharedCatalogKey']=current
                rows.append({"id":name,**({"available":False,"failure":provider.failure} if isinstance(provider,UnavailableProvider) else {}),"module":mounted.get('module'),"catalogKey":catalog_key,**shared,"info":public_config(info, metadata=True),"configSchema":public_config(schema, metadata=True),
                    "supports":{"models":callable(getattr(provider,"list_models",None)),
                                "test":callable(getattr(provider,"list_models",None)),
                                "login":callable(getattr(provider,"login",None))}})
            loop=self.coordinator.get('orchestrator')
            selected=loop._select_provider(providers) if providers else None
            original=getattr(selected,'original',selected)
            name=next((key for key,value in providers.items() if value is original),None)
            info=selected.get_info() if selected is not None else None
            if inspect.isawaitable(info):info=await info
            defaults=(info.get('defaults',{}) if isinstance(info,dict) else getattr(info,'defaults',{})) or {}
            selected_config=next((row.get('config',{}) for row in getattr(self.session,'config',{}).get('providers',[]) if (row.get('id') or row.get('instance_id') or row.get('module','').removeprefix('provider-'))==name),{})
            effective={'instance':name,'model':defaults.get('model') or defaults.get('default_model'),'effort':(self.selection or {}).get('effort') or defaults.get('reasoning_effort') or selected_config.get('reasoning_effort')}
            from .host.model_selection import delegation_routing
            return {"delegationRouting":delegation_routing(self.coordinator),"catalogRevision":self.catalog_revision,"providers":rows,'selection':self.selection,'effective':effective,'pinned':bool(self.selection)}
        name = args.get("provider") or args.get("instance")
        provider = providers.get(name)
        if provider is None:
            raise ValueError("Provider instance is not mounted")
        if operation in {"configuration.providerModels", "configuration.providerTest"}:
            method = getattr(provider,"list_models",None)
            if not callable(method):
                if operation=='configuration.providerModels':return {'provider':name,'models':[],'supported':False}
                raise ValueError("This provider does not expose a model-list API for connection testing")
            async def load():
                result=method()
                if inspect.isawaitable(result):result=await asyncio.wait_for(result,120)
                return public_config(result)
            from .provider_catalog import fingerprint
            key=(name,id(provider),fingerprint(getattr(provider,'config',{})))
            result=await load() if operation=='configuration.providerTest' else await self.model_catalog.get(key,load,refresh=args.get('refresh',False))
            if operation == "configuration.providerTest":
                return {"provider":name,"reachable":True,"modelCount":len(result),"method":"provider.list_models"}
            return {"provider":name,"models":result,'supported':True}
        method = getattr(provider,"login",None)
        if not callable(method):
            raise ValueError("This provider does not expose an interactive login API; configure its credential fields instead")
        if name not in self.logins or args.get("restart"):
            self.require_idle()
            previous = self.logins.get(name)
            if previous and not previous["task"].done():
                raise ValueError("A login is already in progress")
            row = {"provider":name,"loginId":str(uuid.uuid4()),"phase":"waiting","instructions":[]}
            def display(text):
                row["instructions"] = (row["instructions"] + [str(text)[:2000]])[-20:]
            async def login():
                try:
                    parameters = inspect.signature(method).parameters
                    kwargs = {"print_fn":display} if "print_fn" in parameters else {}
                    response = method(**kwargs)
                    if inspect.isawaitable(response):
                        response = await response
                    row["phase"] = "completed" if response else "failed"
                except asyncio.CancelledError:
                    row["phase"] = "cancelled"
                    raise
                except Exception as exc:
                    row.update(phase="failed",error=f"Provider login failed ({type(exc).__name__}). Check its login instructions and try again.")
            row["task"] = asyncio.create_task(login())
            self.logins[name] = row
            await asyncio.sleep(0)
        return {key:value for key,value in self.logins[name].items() if key != "task"}

    def export_resources(self):
        registry = self.coordinator.get_capability("web.registry")
        bundle = self.prepared.bundle if self.prepared else None
        result = {"instruction":getattr(bundle,"instruction",None) or "", "context":[], "namespaces":[], "warnings":[]}
        resolver = self.coordinator.get_capability("model_role_resolver")
        # This metadata belongs to the pinned routing-matrix implementation.
        # Other resolver implementations need not expose their strategy; never
        # fabricate a matrix by flattening model resolution results.
        matrix = getattr(resolver,"_matrix_roles",None)
        if isinstance(matrix,dict):
            result["routingMatrix"] = {"name":getattr(resolver,"name",None),"roles":public_config(matrix)}
        elif resolver:
            result["warnings"].append("The active model routing strategy does not expose an exportable matrix.")
        roots = []
        for namespace,state in (registry.get_state() if registry else {}).items():
            if not state.local_path or not state.uri.startswith("git+"):
                continue
            path = Path(state.local_path).resolve()
            if path.is_file():
                path = path.parent
            uri = state.uri
            try:
                commit = subprocess.run(["git","-C",str(path),"rev-parse","HEAD"],capture_output=True,text=True,check=True,timeout=2).stdout.strip()
                repository = Path(subprocess.run(["git","-C",str(path),"rev-parse","--show-toplevel"],capture_output=True,text=True,check=True,timeout=2).stdout.strip()).resolve()
                base = uri.split("#",1)[0]
                if "@" in base.split("github.com/",1)[-1]:
                    base = base.rsplit("@",1)[0]
                uri = base + "@" + commit
                roots.append((repository,uri))
            except (subprocess.SubprocessError, OSError):
                roots.append((path,uri))
        if "routingMatrix" in result:
            import yaml
            module_path = Path(inspect.getfile(type(resolver))).resolve()
            for root,_ in roots:
                balanced = root / "routing" / "balanced.yaml"
                if module_path.is_relative_to(root) and balanced.is_file():
                    value = yaml.safe_load(balanced.read_text()) or {}
                    result["routingMatrix"]["baseRoles"] = list(value.get("roles",{}))
                    break
        namespaces = {}
        # Packaged behavior resources live outside Foundation's Git cache.
        from .builtin_behaviors import resource_root, SHELL_BEHAVIOR_URI
        builtin_root = resource_root().resolve()
        if any(Path(value).resolve() == builtin_root for value in
               (getattr(bundle, "source_base_paths", {}) or {}).values()):
            roots.append((builtin_root, SHELL_BEHAVIOR_URI.split("#", 1)[0]))
            result["warnings"].append("Unified skill sources in this export follow the repository's main branch; pin that source to a published revision for reproducible skill content.")
        for namespace,value in (getattr(bundle,"source_base_paths",{}) or {}).items():
            path = Path(value).resolve()
            match = next(((root,uri) for root,uri in roots if path.is_relative_to(root)),None)
            if match:
                relative = str(path.relative_to(match[0]))
                uri = match[1].split("#",1)[0] + ("#subdirectory=" + relative if relative != "." else "")
                namespaces[namespace] = path
                result["namespaces"].append({"namespace":namespace,"uri":uri,"localRoot":str(path)})
        pending = list((getattr(bundle,"context",{}) or {}).items())
        reference = re.compile(r"@([\w.-]+):([\w./-]+)")
        def references(text):
            for namespace,relative in reference.findall(text):
                if namespace in namespaces:
                    candidate = (namespaces[namespace] / relative.rstrip(".")).resolve()
                    if candidate.is_relative_to(namespaces[namespace]) and candidate.is_file():
                        pending.append((namespace + ":" + relative, candidate))
        references(result["instruction"])
        references(json.dumps(self.coordinator.config.get("agents",{})))
        seen = set()
        total = 0
        while pending and len(seen) < 500:
            name,value = pending.pop(0)
            path = Path(value).resolve()
            if name in seen:
                continue
            seen.add(name)
            match = next(((root,uri) for root,uri in sorted(roots,key=lambda row:len(str(row[0])),reverse=True) if path.is_relative_to(root)),None)
            if not match or not path.is_file() or path.stat().st_size > 1_000_000:
                result["warnings"].append(f"Context {name} is not a portable cached bundle resource; it was not embedded.")
                continue
            text = path.read_text()
            total += len(text.encode())
            if total > 8_000_000:
                raise ValueError("Bundle resources exceed the 8 MB portable export limit")
            result["context"].append({"name":name,"text":text,"uri":match[1],"relativePath":str(path.relative_to(match[0]))})
            references(text)
        if pending:
            raise ValueError("Bundle references exceed the 500 file portable export limit")
        return result

    async def mode(self, operation, args):
        state = self.coordinator.session_state
        discovery = state.get("mode_discovery")
        if operation == "mode.list":
            rows = discovery.list_modes() if discovery else []
            return {"available":bool(discovery), "active":state.get("active_mode"), "modes":[
                {"name":row[0],"description":row[1] if len(row)>1 else "","source":row[2] if len(row)>2 else ""} for row in rows]}
        if operation not in {"mode.set", "mode.clear"}:
            raise ValueError("Unknown mode operation")
        self.require_idle()
        previous = state.get("active_mode")
        if operation == "mode.clear":
            if previous:
                await self.coordinator.hooks.emit("mode:cleared", {"name":previous,"previous_mode":previous})
            state["active_mode"] = None
        else:
            definition = discovery.find(args.get("name")) if discovery else None
            if not definition:
                raise ValueError("Mode is not available in this bundle")
            name = args["name"]
            if name != previous:
                payload = {key:getattr(definition,key) for key in ("description","default_action","safe_tools","warn_tools","confirm_tools","block_tools") if hasattr(definition,key)}
                payload.update({"old":previous,"new":name,"from_mode":previous,"to_mode":name} if previous else {"name":name,"mode":name})
                await self.coordinator.hooks.emit("mode:changed" if previous else "mode:activated",payload)
                state["active_mode"] = name
        hooks = state.get("mode_hooks")
        if hooks and hasattr(hooks, "reset_warnings"):
            hooks.reset_warnings()
        await self.checkpoint()
        return await self.mode("mode.list", {})

    async def invoke(self, args, *, coordinator=None, bound_arguments=None, provenance=None, checkpoint=True, expected_process_owner=None, generic=False, integration_id=None):
        coordinator = coordinator or self.coordinator
        bound_arguments = copy.deepcopy(bound_arguments)
        import jsonschema
        name, arguments = args.get("name"), args.get("arguments", {})
        if generic:
            from .tool_authority import require_generic_tool
            require_generic_tool(name, arguments)
        tool = (coordinator.get("tools") or {}).get(name)
        if tool is None:
            raise ValueError("Tool is not mounted")
        jsonschema.validate(arguments, getattr(tool,"input_schema",{}))
        call = str(uuid.uuid4())
        # Capture the acquired worker owner before approval can await. Only the
        # final, policy-approved arguments enter its short execution scope.
        issuer = getattr(self, '_tool_invocation', None)
        invocation = issuer(self, coordinator, name, tool, call) if issuer else lambda _: nullcontext()
        data = {"tool_name":name,"tool_call_id":call,"tool_input":arguments,"source":"user", "tool_obj":tool, **(provenance or {})}
        hooks = coordinator.hooks
        pre = await hooks.emit("tool:pre", data)
        pre = await coordinator.process_hook_result(pre,"tool:pre",name)
        if pre.action == "deny":
            await hooks.emit("tool:error",{**data,"error":{"type":"Denied"}})
            return {"success":False,"error":{"message":pre.reason or "Denied by session policy"},"callId":call}
        if pre.action == "modify" and isinstance(pre.data,dict) and "tool_input" in pre.data:
            arguments = pre.data["tool_input"]
            jsonschema.validate(arguments,getattr(tool,"input_schema",{}))
            data = {**data,"tool_input":arguments}
        if generic:
            # Approval may modify an otherwise valid ordinary call. Recheck the
            # actual arguments before execution, including in-place changes.
            try:
                require_generic_tool(name, arguments)
            except ValueError:
                await hooks.emit("tool:error", {**data, "error": {"type": "Denied"}})
                raise
        if bound_arguments is not None and arguments != bound_arguments:
            await hooks.emit("tool:error", {**data, "error": {"type": "Denied"}})
            raise ValueError("A policy modification cannot redirect an identity-bound operation")
        from amplifier_module_loop_live.scope import JOB_CALL
        ownership = JOB_CALL.set(call)
        try:
            if integration_id is not None:
                admit = coordinator.get_capability('integration.admit')
                if not callable(admit):
                    raise ValueError('This runtime cannot enforce integration admission.')
                admission = await admit(integration_id, arguments['command'])
                if admission != {'admitted': True, 'validationId': integration_id}:
                    raise ValueError('The host did not confirm this exact validation invocation.')
                if (coordinator.get('tools') or {}).get(name) is not tool:
                    raise ValueError('The shell mount changed during integration admission.')
            if name == "bash" and arguments.get("question_ids"):
                # Older mounted modules must not silently ignore new dependency
                # arguments even if their JSON schema permits extra fields.
                properties = getattr(tool, "input_schema", {}).get("properties", {})
                if "question_ids" not in properties:
                    raise ValueError("The mounted shell cannot enforce question dependencies")
            if expected_process_owner is not None:
                checker = getattr(tool, "validate_process_owner", None)
                if checker is None and arguments.get("action") == "write":
                    raise ValueError("The mounted shell does not support identity-bound input")
                if checker is not None:
                    checker(*expected_process_owner)
            with invocation(arguments):
                result = await tool.execute(arguments)
            await hooks.emit("tool:post",{**data,"tool_result":result.model_dump() if hasattr(result,"model_dump") else result})
            if checkpoint:
                await self.checkpoint()
            return {"callId":call,"result":public_config(result)}
        except Exception as exc:
            await hooks.emit("tool:error",{**data,"error":{"type":type(exc).__name__}})
            raise
        finally:
            JOB_CALL.reset(ownership)
