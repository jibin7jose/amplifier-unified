"""Standalone child lifecycle on public Foundation and loop-live mechanisms."""
from __future__ import annotations

import asyncio
import contextvars
import copy
import inspect
from dataclasses import replace
from functools import partial
from pathlib import Path
import uuid

from .model_selection import (
    inherited_selection, child_routing, child_preferences,
    pinned_delegate_input, prepare_default_routing, ProviderSelectionError,
    automatic_default_routing, routing_hooks,
)
from .components import merge_modules, compose_bundles
from amplifier_module_loop_live.host import HostAdapter
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_module_loop_live.scope import JOB_CALL

_CHILD_ADMISSION = contextvars.ContextVar("amplifier_unified_child_admission", default=None)

_PERSISTENT = contextvars.ContextVar("amplifier_unified_persistent_child", default=False)
_PREFERENCES_SOURCE = contextvars.ContextVar("amplifier_unified_preferences_source", default=None)
_CALL_ROLE = contextvars.ContextVar("amplifier_unified_call_role", default=None)
_SUBPROCESS_UNSUPPORTED = (
    "Subprocess child sessions are not supported by Unified yet. "
    "Use an in-process recipe step (omit spawn_mode: subprocess) or run this recipe in amplifier-app-cli."
)


def _module_id(row):
    return row.get("module") or row.get("id")


def _desktop_ceiling(policies):
    """Union effective ComputerTool restrictions, including remote defaults."""
    from .config import expand_environment
    ceiling = {}
    for policy in policies:
        config = expand_environment(policy)
        # Computer Use's registry selects the remote path for a truthy target.
        # Leave target parsing/errors to that registry: never mount, probe, or
        # rewrite an invalid target into a usable local/default configuration.
        remote = bool(config.get("target"))
        read_only = config.get("read_only")
        read_only = remote if read_only is None else bool(read_only)
        gate_writes = config.get("gate_writes")
        gate_writes = remote and not read_only if gate_writes is None else bool(gate_writes)
        for key, restricted in (("read_only", read_only), ("gate_writes", gate_writes)):
            if restricted:
                ceiling[key] = True
    return ceiling


def _restrict_child_tools(rows, policies, working_dir, modules):
    """Permission lists are boundaries, not additive configuration lists."""
    writers = [row for row in rows if row.get("module") in modules]
    if not writers and modules != {"tool-image"}:
        return {}
    from .config import expand_environment
    root = Path(working_dir or Path.cwd()).expanduser().resolve()

    def paths(values):
        values = expand_environment(values)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("File-access paths must be lists of strings.")
        return [((path if path.is_absolute() else root / path).resolve())
                for value in values for path in (Path(value).expanduser(),)]

    restrictions = {}
    for kind in ("read", "write"):
        allowed, denied = f"allowed_{kind}_paths", f"denied_{kind}_paths"
        limits = [paths(policy[allowed]) for policy in policies if allowed in policy
                  and not (kind == "read" and policy[allowed] is None)]
        if limits:
            effective = limits[0]
            for limit in limits[1:]:
                effective = [a if a.is_relative_to(b) else b
                             for a in effective for b in limit
                             if a.is_relative_to(b) or b.is_relative_to(a)]
            restrictions[allowed] = list(dict.fromkeys(str(path) for path in effective))
        if any(denied in policy for policy in policies):
            restrictions[denied] = list(dict.fromkeys(str(path) for policy in policies
                for path in paths(policy.get(denied, []))))
    if any(policy.get("allow_paid") is False for policy in policies):
        restrictions["allow_paid"] = False
    if modules == {"tool-image"} and any(policy.get("routing_blocked") is True for policy in policies):
        restrictions["routing_blocked"] = True
        # Explicit compatibility backends also obey allow_paid. A child must
        # not escape a routing denial by switching to that older dispatch path.
        restrictions["allow_paid"] = False
    # Changing a declaration's instance ID must not bypass the parent boundary.
    for row in writers:
        row.setdefault("config", {}).update(copy.deepcopy(restrictions))
    return restrictions


def child_plan(parent, overlay, *, tool_inheritance=None, hook_inheritance=None, working_dir=None,
               retained_image_tools=None, retained_image_policy=None):
    """Compose immutable agent settings and honor explicit inheritance policies."""
    from amplifier_foundation import deep_merge
    from ..decision_accounts import logical_plan, check_child_sources
    parent, overlay = logical_plan(parent), logical_plan(overlay)
    check_child_sources(parent, overlay)
    agent_filter = overlay.pop("agents", None)
    from ..provider_recording import apply_provider_recording
    # Compose explicit child choices before reapplying host defaults. Otherwise
    # an inherited provenance marker can wrongly claim a child's raw:true.
    apply_provider_recording(parent, enabled=False)
    apply_provider_recording(overlay, enabled=False)
    if isinstance(agent_filter, dict):
        apply_provider_recording({'agents': agent_filter}, enabled=False)
    agents = parent.get("agents", {})
    spawn = parent.get("spawn") or {}
    if retained_image_tools is not None and not isinstance(retained_image_tools, list):
        raise ValueError("Saved child tools must be a list of module declarations.")
    desktop_policies = [parent.get("_unified_desktop_policy", {})]
    for rows in (parent.get("tools", []), spawn.get("tools", []), retained_image_tools or []):
        desktop_policies.extend(row.get("config", {}) for row in rows
                                if isinstance(row, dict) and row.get("module") == "tool-computer-use")
    # Partial overlays inherit same-instance configuration. Preserve their
    # explicit restrictions now, but derive defaults only after that merge.
    desktop_policies.extend({key: row.get("config", {}).get(key)
                             for key in ("read_only", "gate_writes")}
                            for row in overlay.get("tools", [])
                            if isinstance(row, dict) and row.get("module") == "tool-computer-use")
    image_policies = [parent.get("_unified_image_policy", {})]
    if retained_image_policy is not None:
        image_policies.append(retained_image_policy)
    if any(not isinstance(policy, dict) for policy in image_policies):
        raise ValueError("Inherited image policy must be a dictionary.")
    for policy in image_policies:
        if any(key in policy and type(policy[key]) is not bool
               for key in ("allow_paid", "routing_blocked")):
            raise ValueError("Inherited image policy flags must be booleans.")
    file_policies = []
    patch_policies = []
    search_configs = []
    for index, rows in enumerate((parent.get("tools", []), spawn.get("tools", []), overlay.get("tools", []),
                                  retained_image_tools or [])):
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("module") == "tool-search":
                search_configs.append(copy.deepcopy(row.get("config", {})))
            if not isinstance(row, dict) or row.get("module") not in {"tool-image", "tool-filesystem", "tool-apply-patch"}:
                continue
            config = row.get("config", {})
            if not isinstance(config, dict):
                raise ValueError("File tool configuration must be dictionaries.")
            policy = {key: copy.deepcopy(value) for key, value in config.items()
                      if key in {"allowed_read_paths", "allowed_write_paths", "denied_read_paths", "denied_write_paths"}}
            if row["module"] == "tool-image":
                if index != 2 or "allow_paid" in config:
                    # Mounted/replacement declarations are disabled unless literally
                    # True. Only an overlay omission means inherit the parent grant.
                    policy["allow_paid"] = config.get("allow_paid") is True
                if config.get("routing_blocked") is True:
                    policy["routing_blocked"] = True
            if row["module"] == "tool-apply-patch":
                patch_policies.append(policy)
            else:
                image_policies.append(policy)
                if row["module"] == "tool-filesystem":
                    file_policies.append(policy)
    if isinstance(spawn.get("tools"), list):
        parent["tools"] = copy.deepcopy(spawn["tools"])
    if spawn.get("exclude_tools"):
        parent["tools"] = [item for item in parent.get("tools", []) if _module_id(item) not in spawn["exclude_tools"]]
    result = deep_merge(parent, overlay)
    for section, policy in (("providers", None), ("tools", tool_inheritance), ("hooks", hook_inheritance)):
        inherited = parent.get(section, [])
        declared = overlay.get(section, [])
        if not isinstance(inherited, list) or not isinstance(declared, list):
            raise ValueError(f"{section} must be a list of module declarations")
        if policy:
            allow = policy.get(f"inherit_{section}")
            exclude = policy.get(f"exclude_{section}", [])
            if allow is not None:
                inherited = [item for item in inherited if _module_id(item) in allow]
            elif exclude:
                inherited = [item for item in inherited if _module_id(item) not in exclude]
            if section == "hooks":
                # Approval/security gate modules are not optional child UI hooks.
                gates = [item for item in parent.get("hooks", []) if any(word in str(_module_id(item)).lower() for word in ("approval", "permission", "security"))]
                inherited = merge_modules(inherited, gates)
        if section == "providers":
            # Foundation merges by legacy id; Rust Core mounts by instance_id.
            # Preserve both aliases so distinct provider instances never collapse.
            for item in inherited + declared:
                if item.get("instance_id") and not item.get("id"):
                    item["id"] = item["instance_id"]
                if item.get("id") and not item.get("instance_id"):
                    item["instance_id"] = item["id"]
        result[section] = merge_modules(inherited, declared)
    if retained_image_tools is not None and not any(
            isinstance(row, dict) and row.get("module") == "tool-image" for row in retained_image_tools):
        result["tools"] = [row for row in result.get("tools", []) if row.get("module") != "tool-image"]
    _restrict_child_tools(result.get("tools", []), file_policies, working_dir, {"tool-filesystem"})
    _restrict_child_tools(result.get("tools", []), [*file_policies, *patch_policies], working_dir, {"tool-apply-patch"})
    # Carry only the effective restrictions, even through an image-free child.
    # Compose from ancestor/saved authority, not an overlay's internal marker.
    # Paths become absolute here so a later child's cwd cannot relocate them.
    result["_unified_image_policy"] = _restrict_child_tools(
        result.get("tools", []), image_policies, working_dir, {"tool-image"})
    from ..read_policy import policy_paths, restrict_search
    for row in result.get("tools", []):
        if row.get("module") == "tool-search":
            row["config"] = restrict_search(row.get("config", {}), files=file_policies,
                searches=search_configs, paths=lambda values: policy_paths(values, working_dir or Path.cwd()))
    # Child overlays and instance aliases cannot grant desktop writes that the
    # parent did not opt into. Explicit root settings remain the authority.
    desktop_policies.extend(row.get("config", {}) for row in result.get("tools", [])
                            if row.get("module") == "tool-computer-use")
    desktop_ceiling = _desktop_ceiling(desktop_policies)
    # Retain ceilings even when an intermediate child omits the desktop tool.
    # Neither an overlay nor a new instance ID can erase ancestor restrictions.
    result["_unified_desktop_policy"] = desktop_ceiling
    for row in result.get("tools", []):
        if row.get("module") == "tool-computer-use":
            row.setdefault("config", {}).update(desktop_ceiling)
    if agent_filter == "none":
        result["agents"] = {}
    elif isinstance(agent_filter, list):
        result["agents"] = {key: copy.deepcopy(value) for key, value in agents.items() if key in agent_filter}
    elif isinstance(agent_filter, dict):
        result["agents"] = {**agents, **agent_filter}
    else:
        result["agents"] = copy.deepcopy(agents)
    return copy.deepcopy(result)


class StandaloneHostAdapter(HostAdapter):
    def __init__(self, registry=None):
        self.registry = registry

    async def prepare_execution(self, loop, coordinator, providers):
        if coordinator and coordinator.get_capability("live.child_mode") == "finite":
            return None, providers, coordinator.session_id
        if coordinator:
            from ..native_provider import install_native
            providers = await install_native(loop, coordinator, providers)
        return coordinator.get_capability("live.runtime") if coordinator else None, providers, None

    def finite_finished(self, scope, coordinator, status, result=""):
        if scope and self.registry:
            self.registry.finished(scope, status, result)


class Children:
    def __init__(self, root, store, approvals, host_adapter=None):
        self.root = root
        self.store = store
        self.approvals = approvals
        self.rows = {}
        self.admissions = {}
        self.admission_gate = None
        self.prepared = {}
        self.sessions = {}
        self.reserved = 0
        self.host_adapter = host_adapter or StandaloneHostAdapter(self)
        self.host_adapter.registry = self

    @staticmethod
    def _public(row):
        return {key: copy.deepcopy(value) for key, value in row.items() if not key.startswith("_") and key not in {"runtime", "task"}}

    def snapshot(self):
        return [self._public(row) for row in self.rows.values()]

    def _emit(self, row, event=None):
        payload = self._public(row)
        if event:
            payload["event"] = event
        self.root.inbox.put_nowait(("child_event", payload))

    def finished(self, identity, status, result=""):
        row = self.rows.get(identity)
        if not row:
            return
        report = str(result)[-20000:]
        if report and (report != row.get("report") or not row.get("reportId")):
            row.update(report=report, reportId=str(uuid.uuid4()), reports=row.get("reports", 0) + 1, reportSourceChars=len(str(result)), reportTruncated=len(str(result)) > 20000, reportWindow="tail")
        row["status"] = status
        if row.get("runtime"):
            row["runtime"].closed = True
        # Finite delegate results already return through the original tool/job
        # receipt. A second lifecycle observation would make the manager answer
        # the same result twice. Still publish UI status without another turn.
        self._emit(row, "session.closed" if row.get("persistent") else "execution.finished")

    def _runtime(self, row):
        def observe(event):
            kind = event["type"]
            if kind == "session.ready":
                row.update(status="running", steering=event.get("steering"), native=event.get("native", False))
            elif kind == "input.delivered":
                row["status"] = "running"
            elif kind == "session.idle":
                row.update(status="idle", report=event.get("text", "")[-20000:], reportId=str(uuid.uuid4()), reports=row["reports"] + 1, reportSourceChars=len(event.get("text", "")), reportTruncated=len(event.get("text", "")) > 20000, reportWindow="tail")
                self.root.inbox.put_nowait(("child_report", self._public(row)))
            elif kind == "session.closed":
                row["status"] = event.get("status", "interrupted")
            if kind in {"session.ready", "input.delivered", "session.idle", "session.closed", "steering.accepted", "steering.deferred"}:
                self._emit(row, kind)
        return Runtime(row["sessionId"], observer=observe, max_input_chars=200000)

    async def install(self, session, prepared):
        from .prompt_events import install
        install(session.coordinator)
        from .mentions import install as install_mentions
        install_mentions(session.coordinator)
        self.prepared[session.session_id] = prepared
        self.sessions[session.session_id] = session
        coordinator = session.coordinator
        from .approvals import Approvals
        coordinator.register_capability("approval.interactive",
            isinstance(self.approvals, Approvals) and self.approvals.interactive)
        coordinator.register_capability("session.spawn", self.spawn)
        async def resume(sub_session_id, instruction, parent_session=None, provider_preferences=None, model_role=None):
            return await self.resume(sub_session_id, instruction, parent_session or session, provider_preferences, model_role)
        coordinator.register_capability("session.resume", resume)
        coordinator.register_capability("live.children", self)
        coordinator.register_capability("live.host", self.host_adapter)
        delegate = (coordinator.get("tools") or {}).get("delegate")
        if delegate and not isinstance(delegate, PersistentDelegate):
            await coordinator.mount("tools", PersistentDelegate(delegate, self, coordinator), name="delegate")
            await coordinator.mount("tools", WorkerControl(self), name="live_worker")

    async def spawn(self, agent_name, instruction, parent_session, agent_configs=None, sub_session_id=None,
                    tool_inheritance=None, hook_inheritance=None, orchestrator_config=None,
                    parent_messages=None, provider_preferences=None, self_delegation_depth=0,
                    session_metadata=None, use_subprocess=False, **kwargs):
        if kwargs:
            raise ValueError("Unsupported child session options: " + ", ".join(kwargs))
        # Community recipes always supply this flag, including False. Keep
        # ordinary children on the existing approval/checkpoint/live-host path.
        if not isinstance(use_subprocess, bool):
            raise ValueError("use_subprocess must be a boolean")
        if use_subprocess:
            raise ValueError(_SUBPROCESS_UNSUPPORTED)
        configs = agent_configs or parent_session.coordinator.config.get("agents", {})
        if agent_name == "self":
            overlay = {}
        elif agent_name in configs and isinstance(configs[agent_name], dict):
            overlay = copy.deepcopy(configs[agent_name])
        else:
            raise ValueError(f"Unknown agent: {agent_name}")
        identity = sub_session_id or str(uuid.uuid4())
        return await self._execute(identity, instruction, parent_session, agent_name, overlay,
            tool_inheritance=tool_inheritance, hook_inheritance=hook_inheritance,
            orchestrator_config=orchestrator_config, parent_messages=parent_messages,
            provider_preferences=provider_preferences, self_delegation_depth=self_delegation_depth,
            session_metadata=session_metadata)

    async def resume(self, sub_session_id, instruction, parent_session, provider_preferences=None, model_role=None):
        if sub_session_id in self.rows and self.rows[sub_session_id].get("status") in {"starting", "running", "idle", "stopping"}:
            raise ValueError("This child is already active; use live_worker to message or steer it")
        saved = self.store.load(sub_session_id) or self.store.import_cli(sub_session_id)
        if not saved:
            raise FileNotFoundError("No saved child session exists with this ID")
        messages, metadata = saved
        actual_parent = metadata.get("parent_id") or metadata.get("parent_session_id")
        if actual_parent and actual_parent != parent_session.session_id:
            raise ValueError("This saved session belongs to a different parent")
        overlay = copy.deepcopy(metadata.get("agent_overlay", {}))
        if model_role is not None:
            overlay["model_role"] = model_role
        retained_preferences = metadata.get("provider_preferences")
        if model_role is not None and metadata.get("preferences_source") == "role":
            retained_preferences = None
        call_role = _CALL_ROLE.get()
        from_delegate = call_role is not None and call_role[0] is parent_session.coordinator
        call_source = _PREFERENCES_SOURCE.get() if from_delegate else None
        if (model_role is not None and (not from_delegate or call_source == "role")
                and metadata.get("preferences_source") == "default_fallback"):
            # An actual new role request replaces a derived fallback just as it
            # replaces derived preferences. A delegate replaying its cached role
            # on ordinary resume is not a new request.
            metadata = {**metadata, "preferences_source": "role"}
            metadata.pop("effective_selection", None)
        if call_source == "role" and (
                (metadata.get("preferences_source") == "explicit" and retained_preferences)
                or overlay.get("provider_preferences") or overlay.get("providers")):
            # The delegate may already have resolved its call role. That derived
            # list still cannot override a saved explicit pin/provider declaration.
            provider_preferences = None
        if provider_preferences:
            source = (call_source or metadata.get("preferences_source", "explicit")) if from_delegate else "explicit"
            metadata = {**metadata, "preferences_source": source}
        return await self._execute(sub_session_id, instruction, parent_session,
            metadata.get("agent_name", "resumed"), overlay, provider_preferences=provider_preferences or retained_preferences,
            parent_messages=messages, resumed=True, session_metadata=metadata)

    async def _execute(self, identity, instruction, parent, agent, overlay, **kwargs):
        # Capture before preparation's first await. A checkout move must retain
        # existing preparation too, not just children that already have UI rows.
        dispatch = (getattr(parent.coordinator, "_tool_dispatch_contexts", {}).get(asyncio.current_task())
                    or getattr(parent.coordinator, "_tool_dispatch_context", {}) or {})
        call_id = JOB_CALL.get() or dispatch.get("tool_call_id") or (
            (kwargs.get("session_metadata") or {}).get("tool_call_id") if not kwargs.get("resumed") else None)
        entry = {"sessionId": identity, "parentSessionId": parent.session_id,
                 "callId": call_id, "runId": str(uuid.uuid4()), "task": asyncio.current_task()}
        if self.admission_gate:
            self.admission_gate(entry)
        self.admissions[entry["runId"]] = entry
        token = _CHILD_ADMISSION.set((self, entry))
        try:
            return await self._execute_admitted(identity, instruction, parent, agent, overlay,
                                                admission=entry, **kwargs)
        finally:
            self.admissions.pop(entry["runId"], None)
            _CHILD_ADMISSION.reset(token)

    async def _execute_admitted(self, identity, instruction, parent, agent, overlay, *, admission, tool_inheritance=None,
                       hook_inheritance=None, orchestrator_config=None, parent_messages=None,
                       provider_preferences=None, self_delegation_depth=0, session_metadata=None, resumed=False):
        # Capture the invoking assignment before any provider/session setup can
        # await newer parent work. The bound installer never recaptures it.
        capture_access = parent.coordinator.get_capability("web.app_access.install")
        install_access = capture_access() if capture_access else None
        from amplifier_foundation import Bundle, ProviderPreference, apply_provider_preferences_with_resolution
        from amplifier_core import HookResult
        if identity in self.rows and self.rows[identity].get("status") in {"starting", "running", "idle", "stopping"} and self.rows[identity].get("task") and not self.rows[identity]["task"].done():
            raise ValueError("A child with this identity is already active")
        prepared = self.prepared.get(parent.session_id)
        if prepared is None:
            raise RuntimeError("Parent bundle is not attached to the standalone host")
        call_role = _CALL_ROLE.get()
        call_source = (_PREFERENCES_SOURCE.get()
                       if call_role and call_role[0] is parent.coordinator else None)
        if call_role and call_role[0] is parent.coordinator and call_role[1]:
            # Delegate resolves call roles before spawn and can pass None for
            # an empty route. Retain that role so it cannot look unspecified,
            # fall back under an explicit matrix, or resolve the agent's old role.
            overlay = {**overlay, "model_role": call_role[1]}
        working_dir = getattr(parent.coordinator, "get_capability", lambda _: None)("session.working_dir")
        cwd = Path(working_dir or Path.cwd())
        retained_plan = (session_metadata or {}).get("mount_plan") if resumed else None
        retained_image_policy = retained_plan.get("_unified_image_policy", {}) if isinstance(retained_plan, dict) else {}
        if not isinstance(retained_image_policy, dict):
            raise ValueError("Saved child image policy must be a dictionary.")
        plan = child_plan(parent.coordinator.config, overlay, tool_inheritance=tool_inheritance,
                          hook_inheritance=hook_inheritance, working_dir=cwd,
                          retained_image_tools=retained_plan.get("tools", []) if isinstance(retained_plan, dict) else None,
                          retained_image_policy=retained_image_policy)
        if isinstance(retained_plan, dict):
            from ..decision_accounts import restore_child_accounts
            current_overlay = parent.coordinator.config.get("agents", {}).get(agent, {}) if agent != "self" else {}
            authorized = child_plan(parent.coordinator.config, current_overlay, working_dir=cwd)
            plan = restore_child_accounts(plan, retained_plan, authorized)
        if isinstance(retained_plan, dict):
            # A resumed child must not lose its prior write ceiling merely
            # because its immediate parent currently omits the tool.
            # child_plan also retains declarations from older checkpoints that
            # predate the marker; neither representation can weaken the other.
            retained_desktop = _desktop_ceiling([retained_plan.get("_unified_desktop_policy", {})])
            ceiling = plan.setdefault("_unified_desktop_policy", {})
            ceiling.update(retained_desktop)
            for tool in plan.get("tools", []):
                if tool.get("module") == "tool-computer-use":
                    tool.setdefault("config", {}).update(ceiling)
        components = getattr(getattr(prepared, "bundle", None), "_host_components", None)
        if components is not None:
            # Late and resumed overlays follow the same supported-loop policy
            # as declared agents; custom child orchestrators remain untouched.
            if ('loop-live' in components.installed and plan.get('session', {}).get('orchestrator', {}).get('module')
                    in {'loop-live', 'loop-streaming'}):
                from .session import live_plan
                plan, _ = live_plan(plan)
            plan = components.normalize(plan)
        # Never silently run an agent requesting process isolation in-process.
        if plan.get("spawn_mode") == "subprocess":
            raise ValueError(_SUBPROCESS_UNSUPPORTED)
        self.store.directory(identity)
        while len(self.rows) >= 256 and identity not in self.rows:
            oldest = next((key for key, value in self.rows.items() if value["status"] in {"completed", "cancelled", "error", "interrupted"}), None)
            if oldest is None:
                raise RuntimeError("The active worker limit has been reached")
            del self.rows[oldest]
        if orchestrator_config:
            session_config = plan.setdefault("session", {})
            orchestrator = session_config.setdefault("orchestrator", {})
            if not isinstance(orchestrator, dict):
                raise ValueError("Agent orchestrator must be configured as a module declaration")
            orchestrator.setdefault("config", {}).update(copy.deepcopy(orchestrator_config))
        routing_policy = parent.coordinator.get_capability("web.default_routing_policy") is True
        routing_saved = session_metadata if resumed else None
        automatic_routing = routing_policy and automatic_default_routing(
            parent.coordinator, overlay, saved=routing_saved)
        preferences_source = ((None if resumed else call_source)
                              or ((session_metadata or {}).get("preferences_source", "explicit")
                                  if provider_preferences else
                                  "explicit" if overlay.get("provider_preferences") else "role"))
        if routing_policy:
            if not provider_preferences and not overlay.get("provider_preferences") and overlay.get("providers"):
                from .model_selection import prioritize_agent_providers
                plan = prioritize_agent_providers(plan, overlay["providers"])
            provider_preferences = await child_preferences(
                parent.coordinator, overlay, provider_preferences, saved=routing_saved)
            # The child selection below is authoritative. Do not retain a parent
            # role or an old agent pin for a lifecycle hook to reapply afterward.
            plan.pop("provider_preferences", None)
            plan.pop("model_role", None)
        preferences = [ProviderPreference.from_dict(p) if isinstance(p, dict) else p for p in provider_preferences or []]
        default_fallback = bool(automatic_routing and overlay.get("model_role")
                                and not preferences and not overlay.get("providers"))
        selection = inherited_selection(parent, overlay, preferences, session_metadata,
                                        default_fallback=default_fallback)
        if default_fallback:
            preferences_source = "default_fallback"
        from ..provider_recording import apply_provider_recording, install_request_redaction
        if preferences:
            apply_provider_recording(plan, enabled=False)
            selected_plan = await apply_provider_preferences_with_resolution(plan, preferences, parent.coordinator)
            if routing_policy and selected_plan is plan:
                raise ProviderSelectionError("No requested child provider/model is available; no default was substituted.")
            plan = selected_plan
        apply_provider_recording(plan)
        overlay_bundle = Bundle.from_dict({key: value for key, value in overlay.items() if key != "agents"}, base_path=prepared.bundle.base_path)
        overlay_bundle.instruction = overlay.get("instruction") or (overlay.get("system") or {}).get("instruction")
        effective = compose_bundles(prepared.bundle, overlay_bundle)
        # Preserve the qualified file boundaries in the prepared bundle as well
        # as its mount plan, including child tool-inheritance exclusions.
        file_modules = {"tool-image", "tool-filesystem", "tool-apply-patch", "tool-search", "tool-computer-use"}
        effective.tools = [row for row in effective.tools if row.get("module") not in file_modules]
        effective.tools.extend(copy.deepcopy(row) for row in plan.get("tools", [])
                               if row.get("module") in file_modules)
        effective.agents = copy.deepcopy(plan.get("agents", {}))
        if components is not None:
            components.apply(effective)
        child_prepared = replace(prepared, mount_plan=plan, bundle=effective)
        parent_isolation = parent.coordinator.get_capability("web.provider_isolation")
        isolation = None
        if parent_isolation is not None:
            from ..provider_isolation import ProviderIsolation
            from ..provider_environment import materialize_bundle_providers
            from types import SimpleNamespace
            isolation = ProviderIsolation()
            isolation.preparation = copy.deepcopy(parent_isolation.preparation)
            # Validate this child's effective declaration, not a stale parent
            # outcome. An explicit repair may differ even for the same account.
            await materialize_bundle_providers(
                SimpleNamespace(providers=plan.get("providers", []), agents=plan.get("agents", {})),
                child_prepared, isolation=isolation)
        from ..decision_accounts import DecisionAccounts, AccountResolver
        decision_accounts = DecisionAccounts(child_prepared.mount_plan, isolation, lambda module, source: source)
        child_prepared.mount_plan = decision_accounts.plan
        if decision_accounts.aliases:
            child_prepared.resolver = AccountResolver(child_prepared.resolver, decision_accounts.aliases)
        persistent = _PERSISTENT.get()
        call_id = admission["callId"]
        row = {"sessionId": identity, "parentSessionId": parent.session_id, "callId": call_id, "runId": admission["runId"],
               "agent": agent, "status": "starting", "persistent": persistent, "report": "", "reports": 0,
               "routing": child_routing(parent, overlay, preferences, selection,
                                        preferences_source=preferences_source if routing_policy else None),
               "task": asyncio.current_task()}
        self.rows[identity] = row
        self._emit(row)
        child = None
        ledger = None
        unregister = None
        from ..provider_environment import credential_bindings
        metadata = {**(session_metadata or {}), "parent_id": parent.session_id, "agent_name": agent,
                    "tool_call_id": call_id,
                    "agent_overlay": copy.deepcopy(overlay), "workspace": str(cwd), "persistent": persistent,
                    "provider_preferences": [p.to_dict() for p in preferences],
                    "preferences_source": preferences_source, "mount_plan": credential_bindings(plan),
                    "automatic_default_routing": bool(
                        automatic_routing and routing_hooks(plan) == routing_hooks(parent.coordinator.config))}
        if selection:
            metadata["effective_selection"] = selection
        else:
            # SessionStore merges metadata: omission would resurrect a prior
            # fallback snapshot after an explicit/role selection replaced it.
            metadata["effective_selection"] = None
        continuity = None
        checkpoint_ready = False
        async def checkpoint(status=None):
            # Setup callbacks and failure handling can run before saved history
            # is fully restored. Never publish that empty or partial context.
            if not child or not checkpoint_ready:
                return
            context = child.coordinator.get("context")
            messages = await context.get_messages() if context else []
            self.store.save(identity, messages, {**metadata, "status": status or row["status"]})
            if continuity:
                continuity.save()
        completion = {}
        async def completed(event, data):
            completion.update(data)
            return HookResult()
        try:
            initialization = ({"before_initialize": partial(
                prepare_default_routing, automatic=metadata["automatic_default_routing"])}
                if routing_policy else {})
            if isolation or decision_accounts.records:
                routing_callback = initialization.get("before_initialize")
                async def initialize_with_isolation(constructing):
                    nonlocal child
                    child = constructing  # Also own cleanup before create_session returns.
                    if isolation:
                        await isolation.before_initialize(constructing)
                    if decision_accounts.records:
                        await decision_accounts.before_initialize(constructing)
                    if routing_callback:
                        await routing_callback(constructing)
                initialization["before_initialize"] = initialize_with_isolation
            from .approvals import Approvals
            approvals = (self.approvals.for_context(lambda: child.coordinator.get('context') if child else None)
                         if isinstance(self.approvals, Approvals) else self.approvals)
            child = await child_prepared.create_session(session_id=identity, parent_id=parent.session_id,
                approval_system=approvals, display_system=getattr(parent.coordinator, "display_system", None),
                session_cwd=cwd, is_resumed=resumed, **initialization)
            decision_accounts.require_complete()
            coordinator = child.coordinator
            from ..read_policy import validate_mounted_read_policy
            validate_mounted_read_policy(coordinator)
            from ..session_files import capture_dir
            history_workspace = parent.coordinator.get_capability('web.history_workspace') or cwd
            install_request_redaction(coordinator, directory=capture_dir(history_workspace, identity))
            coordinator.register_capability('web.history_workspace', str(history_workspace))
            if selection:
                from .session import SelectedProvider
                providers = coordinator.get("providers") or {}
                provider_identity = selection.get("instance")
                if provider_identity not in providers:
                    raise ValueError("The inherited provider instance is unavailable in this child")
                child_loop = coordinator.get("orchestrator")
                if not hasattr(child_loop, "root_provider"):
                    raise ValueError("The child orchestrator cannot apply the inherited model selection")
                child_loop.root_provider = SelectedProvider(providers[provider_identity], selection)
            if isolation:
                from ..provider_isolation import require_selected
                require_selected(coordinator)
            coordinator.register_capability("live.child", True)
            coordinator.register_capability("web.child.resumed", resumed)
            coordinator.register_capability("live.child_mode", "persistent" if persistent else "finite")
            coordinator.register_capability("web.worker_run", row["runId"])
            coordinator.register_capability("self_delegation_depth", self_delegation_depth)
            coordinator.register_capability("live.checkpoint", checkpoint)
            from ..context_continuity import install as install_continuity
            from ..runtime_controls import override_path
            continuity = install_continuity(coordinator, identity, override_path(identity).parent, checkpoint)
            async def compacted(event, data):
                await checkpoint()
                return HookResult()
            coordinator.hooks.register("context:compaction_finished", compacted, name="unified-child-context-checkpoint")
            for capability in ("model_role_resolver", "web.activity.install"):
                value = parent.coordinator.get_capability(capability)
                if routing_policy and capability == "model_role_resolver" and coordinator.get_capability(capability) is not None:
                    continue
                if value is not None:
                    coordinator.register_capability(capability, value)
            activity_installer = coordinator.get_capability("web.activity.install")
            if activity_installer:
                installed = activity_installer(coordinator)
                if inspect.isawaitable(installed):
                    await installed
            if persistent:
                from amplifier_module_loop_live.job_store import JobStore
                ledger = JobStore(self.store.directory(identity) / "live-jobs")
                coordinator.register_capability("live.jobs", ledger)
                row["runtime"] = self._runtime(row)
                coordinator.register_capability("live.runtime", row["runtime"])
            else:
                coordinator.register_capability("live.runtime", None)
            if parent_messages is not None:
                await coordinator.get("context").set_messages(copy.deepcopy(parent_messages))
            await self.install(child, child_prepared)
            # Authority-bearing closures belong to the invoking coordinator.
            # Install before execution, including finite/custom orchestrators
            # that never call loop-live's prepare_execution hook. The installer
            # also propagates itself for nested and resumed children.
            if install_access:
                await install_access(coordinator)
            unregister = coordinator.hooks.register("orchestrator:complete", completed, name="unified-child-completion", priority=999)
            if ledger and parent_messages is not None:
                # Recover evidence only after original history and admission
                # setup succeed. A pending dispatch becomes interrupted, never
                # replayed; failed recovered-context restoration still must not
                # overwrite the canonical transcript or metadata.
                recovered_messages, recovered = ledger.recover(parent_messages)
                coordinator.register_capability("live.recovered_jobs", [item["job_id"] for item in recovered])
                if recovered_messages != parent_messages:
                    await coordinator.get("context").set_messages(recovered_messages)
            checkpoint_ready = True
            row["status"] = "running"
            self._emit(row)
            await checkpoint()
            from .mentions import expand_input
            instruction = await expand_input(coordinator, instruction,
                max_chars=row['runtime'].max_input_chars if persistent else None)
            if persistent:
                owner = asyncio.create_task(child.execute(""))
                row["_owner"] = owner
                await row["runtime"].submit(Input("user", instruction))
                try:
                    output = await owner
                finally:
                    if not owner.done():
                        owner.cancel()
                        await asyncio.gather(owner, return_exceptions=True)
            else:
                output = await child.execute(instruction)
            terminal = row["status"] if persistent and row["status"] in {"cancelled", "interrupted", "error"} else "completed"
            self.finished(identity, terminal, output)
            await checkpoint(terminal)
            return {"output": output, "session_id": identity, "status": completion.get("status", "success"),
                    "turn_count": completion.get("turn_count", 1), "metadata": completion.get("metadata", {})}
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            self.finished(identity, status)
            await checkpoint(status)
            raise
        finally:
            if unregister:
                unregister()
            if child:
                await child.cleanup()
            if ledger:
                ledger.close()
            self.sessions.pop(identity, None)
            self.prepared.pop(identity, None)
            row.pop("task", None)
            row.pop("_owner", None)
            row.pop("runtime", None)
            row.pop('_input_sources', None)

    async def control(self, identity, action, text="", input_id=None, attachments=()):
        row = self.rows.get(identity)
        if not row or row["status"] in {"completed", "cancelled", "error", "interrupted"}:
            raise ValueError("Worker is no longer active")
        if action not in {"message", "steer", "finish", "cancel"}:
            raise ValueError("Unknown worker action")
        if not row["persistent"]:
            if action != "cancel":
                raise ValueError("This finite worker accepts cancellation; resume it for a new instruction")
            row["status"] = "stopping"
            row["task"].cancel()
            self._emit(row)
            return {"accepted": True, "sessionId": identity, "action": action, "effects": "not_rolled_back"}
        if action == "finish" and row["status"] != "idle":
            raise ValueError("Wait for the worker's report before finishing, or cancel active work")
        runtime = row.get("runtime")
        if not runtime:
            raise ValueError("Worker is still preparing; try again when it is ready")
        if action in {'message', 'steer'}:
            from .mentions import expand_input
            source = (action, text, tuple(attachments))
            previous = runtime.accepted.get(input_id)
            sources = row.setdefault('_input_sources', {})
            if previous and input_id in sources:
                if sources[input_id] != source:
                    raise ValueError('Command identity reused with different content')
                text = previous.text
            else:
                text = await expand_input(self.sessions[identity].coordinator, text, max_chars=runtime.max_input_chars)
        command = Input("stop" if action in {"finish", "cancel"} else "steer" if action == "steer" else "user", text,
            target=action if action in {"finish", "cancel"} else None, attachments=tuple(attachments), **({"id": input_id} if input_id else {}))
        try:
            await runtime.submit(command)
        finally:
            if action in {'message', 'steer'} and runtime.accepted.get(command.id) == command:
                # Kept with the runtime's bounded input receipts, never exported.
                sources[command.id] = copy.deepcopy(source)
        if action in {"finish", "cancel"}:
            row["status"] = "stopping"
        self._emit(row)
        return {"accepted": True, "sessionId": identity, "action": action, "inputId": command.id, "completed": False, "effects": "not_rolled_back"}


class PersistentDelegate:
    def __init__(self, original, registry, coordinator=None):
        self.original, self.registry, self.coordinator = original, registry, coordinator

    def __getattr__(self, key):
        return getattr(self.original, key)

    @property
    def input_schema(self):
        result = copy.deepcopy(self.original.input_schema)
        result["properties"]["persistent"] = {"type": "boolean", "description": "Keep this worker available for messages and steering after it reports. Default false. The delegate timeout still applies; use live_worker to finish or cancel."}
        return result

    async def execute(self, input):
        from amplifier_core import ToolResult
        persistent = input.get("persistent", False)
        if not isinstance(persistent, bool):
            return ToolResult(success=False, error={"message": "persistent must be a boolean"})
        if persistent and self.registry.reserved >= 4:
            return ToolResult(success=False, error={"message": "Four persistent workers are already active"})
        token = _PERSISTENT.set(persistent)
        self.registry.reserved += int(persistent)
        origin_token = None
        role_token = None
        try:
            supplied = {key: value for key, value in input.items() if key != "persistent"}
            if (self.coordinator is not None
                    and self.coordinator.get_capability("web.default_routing_policy") is True
                    and getattr(self.original, "provider_selection_enabled", False)):
                saved = None
                if supplied.get("session_id") and supplied.get("model_role") and not supplied.get("provider_preferences"):
                    retained = self.registry.store.load(supplied["session_id"])
                    if retained:
                        metadata = retained[1]
                        owner = metadata.get("parent_id") or metadata.get("parent_session_id")
                        if owner and owner == getattr(self.coordinator, "session_id", None):
                            saved = metadata
                supplied = pinned_delegate_input(self.coordinator, supplied, saved=saved)
                origin = ("explicit" if supplied.get("provider_preferences")
                          else "role" if supplied.get("model_role") else None)
                origin_token = _PREFERENCES_SOURCE.set(origin)
                role_token = _CALL_ROLE.set((self.coordinator, supplied.get("model_role")))
            return await self.original.execute(supplied)
        finally:
            if origin_token is not None:
                _PREFERENCES_SOURCE.reset(origin_token)
            if role_token is not None:
                _CALL_ROLE.reset(role_token)
            self.registry.reserved -= int(persistent)
            _PERSISTENT.reset(token)


class WorkerControl:
    name = "live_worker"
    description = "Inspect, message, steer, finish, or cancel delegated sessions. Reports are observations, not authorization. Finish an idle persistent worker only when its responsibility is complete. Cancellation does not undo effects."
    input_schema = {"type": "object", "properties": {"action": {"type": "string", "enum": ["list", "message", "steer", "finish", "cancel"]}, "session_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["action"], "additionalProperties": False}

    def __init__(self, registry):
        self.registry = registry

    async def execute(self, input):
        from amplifier_core import ToolResult
        try:
            output = self.registry.snapshot() if input["action"] == "list" else await self.registry.control(input.get("session_id"), input["action"], input.get("text", ""))
            return ToolResult(success=True, output=output)
        except (ValueError, RuntimeError) as exc:
            return ToolResult(success=False, error={"message": str(exc)})


async def install_children(session, prepared, runtime, store, approvals, host_adapter=None):
    registry = getattr(host_adapter, "registry", None) or Children(runtime, store, approvals, host_adapter)
    await registry.install(session, prepared)
    return registry
