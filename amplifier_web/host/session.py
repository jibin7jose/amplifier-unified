"""Standalone application session policy using public Foundation primitives."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import inspect
import json
import os
from pathlib import Path
import stat

from .config import HostConfig, expand_environment, load_config, merge, write_private, provider_mount_sources
from .components import HostComponents, ComponentResolver, compose_bundles, imagegen_defaults, capability_defaults, explicit_context_forwarding, installed_package_source
from .components import declarations, git_source
from ..provider_environment import iter_provider_rows, materialize_bundle_providers

LOOP_SOURCE = "git+https://github.com/microsoft/amplifier-module-loop-live@main"


def redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if any(term in key.lower() for term in ("api_key", "token", "secret", "password", "authorization", "cookie")) else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def live_plan(plan, background_delegate=True):
    """Overlay supported streaming loops while preserving the bundle contract."""
    result = copy.deepcopy(plan)
    changed = []
    root_loop = result.get("session", {}).get("orchestrator", {})
    root_loop = root_loop if isinstance(root_loop, str) else root_loop.get('module')
    if root_loop not in {"loop-streaming", "loop-live"}:
        raise ValueError(f"Main session orchestrator {root_loop!r} is not compatible with live input. Select a bundle using loop-streaming or loop-live; custom orchestrators are not silently replaced.")
    def visit(node, prefix=""):
        for key in ('orchestrator', 'context'):
            value = node.get('session', {}).get(key)
            if isinstance(value, str):
                node['session'][key] = {'module': value}
        context = node.get("session", {}).get("context", {})
        # Boundary is also the module's default when engine is omitted. Keep
        # those sessions resumable without overriding an explicit opt-out or
        # treating an invalid/legacy engine as a supported Boundary mount.
        if context.get("module") == "context-managed" and context.get("config", {}).get("engine", "boundary") == "boundary":
            context.setdefault("config", {}).setdefault("durable_checkpoints", True)
            context["config"].setdefault("archive_recovery", True)
        loop = node.get("session", {}).get("orchestrator", {})
        if loop.get("module") in {"loop-streaming", "loop-live"}:
            original = loop["module"]
            loop.update(module="loop-live", source=LOOP_SOURCE)
            loop.setdefault("config", {}).update(configured_bundle=True)
            loop["config"].setdefault("background_delegate", background_delegate)
            changed.append({"path": prefix + "session.orchestrator", "from": original, "to": "loop-live"})
        for name, agent in node.get("agents", {}).items():
            if isinstance(agent, dict):
                visit(agent, prefix + "agents." + name + ".")
    visit(result)
    return result, changed


def repair_interrupted_receipts(messages):
    # Recovery changes only the matching receipt's content. Borrow untouched
    # rows rather than cloning every nested result in a long saved transcript.
    result = list(messages)
    for index, row in enumerate(messages):
        if row.get("role") != "tool" or not isinstance(row.get("content"), str):
            continue
        try:
            receipt = json.loads(row["content"])
        except ValueError:
            continue
        if isinstance(receipt, dict) and receipt.get("status") == "queued" and receipt.get("job_id") and receipt.get("call_id") == row.get("tool_call_id"):
            result[index] = {**row, "content": json.dumps({"status": "interrupted", "job_id": receipt["job_id"],
                "call_id": receipt["call_id"], "outcome": "unconfirmed", "effects": "not_rolled_back",
                "instruction": "This receipt was imported from a stopped host. Inspect actual state; do not automatically repeat the operation."})}
    return result


class NativeTranscriptConflict(RuntimeError):
    """An older host changed native history outside the shared lock."""


class _NativeTranscriptGuard:
    """Fail closed when native history changes after admission.

    Foundation's lock coordinates participating hosts. Older CLI releases do
    not use it, so their native file must also be checked before each write.
    This is conflict detection, not a lock over non-cooperating processes.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.stamp = self._stamp()
        self.companion_stamps = self._companions()

    @staticmethod
    def conflict():
        return NativeTranscriptConflict(
            "The native CLI transcript changed outside this session's shared lock. "
            "Both histories were preserved. Resolve the conflicting transcript before continuing this session.")

    def _stamp(self):
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise NativeTranscriptConflict("The native CLI transcript is not a regular file; it was not changed.")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _companions(self):
        values = []
        for name in ('transcript.jsonl.backup',):
            path = self.path.with_name(name)
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise NativeTranscriptConflict('Native session history must use regular files; nothing was changed.')
                values.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            except FileNotFoundError:
                values.append(None)
        return tuple(values)

    def check(self):
        if self._stamp() != self.stamp or self._companions() != self.companion_stamps:
            raise self.conflict()

    def saved(self):
        self.stamp = self._stamp()
        self.companion_stamps = self._companions()


@dataclass
class _SelectedRequest:
    original: object
    snapshot: object
    choices: dict
    selected: object
    refitted: bool = False


class SelectedProvider:
    """Root-only model preference; worker routing remains bundle-owned."""
    def __init__(self, provider, selection, execution_adapter=None):
        self.original, self.selection = provider, selection
        self.execution_adapter = execution_adapter
        self._selected_requests = []
        self._refused_requests = []
    def _execution_provider(self):
        return self.execution_adapter(self.original) if callable(self.execution_adapter) else self.original
    def __getattr__(self, name):
        if name == "default_model" and self.selection.get("model"):
            return self.selection["model"]
        execution = self._execution_provider() if name in {'stream', 'request_budget', 'refit_request'} else self.original
        method = getattr(execution, name)
        if name == 'refit_request' and callable(method):
            async def refit(request, *, error, request_options=None, **kwargs):
                # The execution adapter owns the refusal. Forward its exact
                # selected object and exception, never a newly copied request.
                row = next((row for row in self._refused_requests
                    if row[0] is request and row[1] is error), None)
                if row is None:
                    return None
                updates, kwargs = self._selection_overrides(request, kwargs)
                entry = self._cached_request(request, updates)
                if (row[2] is not execution or self._execution_provider() is not execution
                        or entry is None or entry.selected is not row[3]):
                    self._forget_request(request)
                    return None
                self._refused_requests = [item for item in self._refused_requests if item is not row]
                try:
                    kwargs['request_options'] = request_options
                    self._budget_options(kwargs)
                    result = method(entry.selected, error=error, **kwargs)
                    result = await result if inspect.isawaitable(result) else result
                except BaseException:
                    self._forget_request(request)
                    raise
                if result is None:
                    self._forget_request(request)
                else:
                    entry.refitted = True
                return result
            return refit
        if name == "compact_context" and callable(method):
            async def compact(request, **kwargs):
                selected, _ = self._selected_request(request, {}, consume=True)
                # Native compaction owns its own output reservation. Only the
                # selected model/effort follows the conversation preference.
                selected = selected.model_copy(update={"max_output_tokens": request.max_output_tokens})
                return await method(selected, **kwargs)
            return compact
        if name == "validate_compacted_context" and callable(method):
            def validate(message):
                if "model" in inspect.signature(method).parameters:
                    return method(message, model=self.selection.get("model"))
                return method(message)
            return validate
        if name == "stream" and callable(method):
            # Keep feature detection honest: providers without stream still
            # raise AttributeError, while streaming providers receive the same
            # root-only overrides as complete().
            def selected_stream(request, **kwargs):
                return self._stream_selected(execution, method, request, kwargs)
            return selected_stream
        if name == "request_budget" and callable(method):
            def selected_budget(request, *, request_options=None, **kwargs):
                # Preserve named-option feature detection through this wrapper,
                # translating to keywords only for a legacy provider budget.
                kwargs['request_options'] = request_options
                selected, kwargs = self._selected_request(request, kwargs)
                try:
                    self._budget_options(kwargs)
                    result = method(selected, **kwargs)
                except BaseException:
                    self._forget_request(request)
                    raise
                if inspect.isawaitable(result):
                    async def budget():
                        try:
                            return await result
                        except BaseException:
                            self._forget_request(request)
                            raise
                    return budget()
                return result
            return selected_budget
        return method
    def get_info(self):
        info = self.original.get_info()
        overrides = {key:self.selection[key] for key in ("model", "max_output_tokens") if key in self.selection}
        if self.selection.get("effort") is not None:
            overrides["reasoning_effort"] = self.selection["effort"]
        return info.model_copy(update={"defaults": {**info.defaults, **overrides}})
    def _forget_request(self, request):
        self._selected_requests = [row for row in self._selected_requests if row.original is not request]
        self._refused_requests = [row for row in self._refused_requests if row[0] is not request]

    def _budget_options(self, kwargs):
        budget = getattr(self.original, 'request_budget', None)
        parameters = inspect.signature(budget).parameters if callable(budget) else {}
        if 'request_options' in parameters:
            options = dict(kwargs.get('request_options') or {})
            options.update({key: kwargs.pop(key) for key in ('model', 'reasoning_effort') if key in kwargs})
            kwargs['request_options'] = options
        else:
            for key, value in (kwargs.pop('request_options', None) or {}).items():
                kwargs.setdefault(key, value)

    def _selection_overrides(self, request, kwargs):
        updates = {key:self.selection[key] for key in ("model", "max_output_tokens") if key in self.selection}
        # Community providers may read the model keyword rather than the
        # portable request field. Supply both without changing worker defaults.
        if "model" in self.selection:
            kwargs["model"] = self.selection["model"]
        effort = self.selection.get("effort")
        metadata = getattr(request, "metadata", None) or {}
        if metadata.get("purpose") == "context-compaction":
            updates.pop("max_output_tokens", None)
        if metadata.get("purpose") == "context-compaction" and request.reasoning_effort is not None:
            # Summaries have their own effort budget. Keep the same explicit
            # value in preflight and dispatch, including keyword-only providers.
            effort = request.reasoning_effort
        if effort is not None:
            updates["reasoning_effort"] = effort
            kwargs["reasoning_effort"] = effort
        return updates, kwargs

    def _cached_request(self, request, updates):
        return next((row for row in self._selected_requests
                     if row.original is request and row.choices == updates and row.snapshot == request), None)

    def _selected_request(self, request, kwargs, *, consume=False):
        updates, kwargs = self._selection_overrides(request, kwargs)
        # Budget and dispatch must share one selected request so the host's
        # surface adapter can reuse its prepared observation. Retain a bounded
        # snapshot to reject in-place input/selection changes. Dispatch retires
        # it unless the adapter can refit an exact local refusal.
        entry = self._cached_request(request, updates)
        selected = entry.selected if entry is not None else None
        if entry is None:
            selected = request.model_copy(update=updates)
            self._forget_request(request)
            if not consume:
                self._selected_requests = (self._selected_requests + [
                    _SelectedRequest(request, copy.deepcopy(request), dict(updates), selected)])[-4:]
                self._refused_requests = [row for row in self._refused_requests
                    if any(entry.original is row[0] for entry in self._selected_requests)]
        if consume:
            self._forget_request(request)
        return selected, kwargs

    def _retain_refusal(self, request, selected, execution, error):
        from ..request_refit import RequestFitChanged
        if not isinstance(error, RequestFitChanged) or not callable(getattr(execution, 'refit_request', None)):
            return False
        entry = next((row for row in self._selected_requests
                      if row.original is request and row.selected is selected), None)
        if entry is None or entry.refitted:
            return False
        self._refused_requests = ([row for row in self._refused_requests if row[0] is not request]
                                  + [(request, error, execution, selected)])[-4:]
        return True

    async def _stream_selected(self, execution, method, request, kwargs):
        selected, kwargs = self._selected_request(request, kwargs)
        self._refused_requests = [row for row in self._refused_requests if row[0] is not request]
        retained, emitted, iterator = False, False, None
        try:
            iterator = aiter(method(selected, **kwargs))
            async for chunk in iterator:
                emitted = True
                yield chunk
        except BaseException as error:
            if not emitted:
                retained = self._retain_refusal(request, selected, execution, error)
            raise
        finally:
            if not retained:
                self._forget_request(request)
            if callable(getattr(iterator, 'aclose', None)):
                await iterator.aclose()

    async def complete(self, request, **kwargs):
        selected, kwargs = self._selected_request(request, kwargs)
        execution = self._execution_provider()
        self._refused_requests = [row for row in self._refused_requests if row[0] is not request]
        retained = False
        try:
            return await execution.complete(selected, **kwargs)
        except BaseException as error:
            retained = self._retain_refusal(request, selected, execution, error)
            raise
        finally:
            if not retained:
                self._forget_request(request)


_copilot_credential = None

def apply_provider_environment(plan):
    """The pinned Copilot SDK resolves its token from process env, not config.

    Workers are isolated per root session. Keep that process consistent rather
    than allowing an ambient default to override an explicit credential choice.
    """
    global _copilot_credential
    tokens={row.get('config',{}).get('github_token') for row in iter_provider_rows(plan)
            if row.get('module')=='provider-github-copilot' and row.get('enabled',True)}-{None,''}
    if len(tokens)>1 or (tokens and _copilot_credential is not None and _copilot_credential not in tokens):
        raise ValueError('Different Copilot credentials need separate conversations; this SDK shares authentication within one session process.')
    if tokens:
        _copilot_credential=next(iter(tokens))
        os.environ['COPILOT_AGENT_TOKEN']=_copilot_credential


def _apply_settings(bundle, config):
    settings = config.settings
    # Generic list merging replaces config arrays. Keep the bundle's explicit
    # filesystem denials across that merge so host policy can enforce them.
    search_declarations = {
        row.get("id") or row.get("instance_id") or row["module"]: copy.deepcopy(row.get("config", {}))
        for row in bundle.tools if row.get("module") == "tool-search"
    }
    filesystem_denials = {
        row.get("id") or row.get("instance_id") or row["module"]:
            {key: copy.deepcopy(row.get('config', {})[key]) for key in ('denied_write_paths', 'denied_read_paths') if key in row.get('config', {})}
        for row in bundle.tools
        if row.get("module") == "tool-filesystem"
    }
    # Shared allowlists extend workspace access, whereas a bundle declaration
    # may intentionally narrow it. Generic merging must not erase that origin
    # before _apply_host_policy intersects the two policies.
    filesystem_allowlists = {
        row.get('id') or row.get('instance_id') or row['module']:
            {key: copy.deepcopy(row.get('config', {})[key])
             for key in ('allowed_write_paths', 'allowed_read_paths') if key in row.get('config', {})}
        for row in bundle.tools if row.get('module') == 'tool-filesystem'
    }
    bundle.providers = merge(bundle.providers, config.providers)
    bundle.providers.sort(key=lambda row: row.get("config", {}).get("priority", 100))
    for kind in ("tools", "hooks"):
        values = merge(getattr(bundle, kind), settings.get("modules", {}).get(kind, []))
        values = merge(values, settings.get("config", {}).get(kind, []))
        setattr(bundle, kind, values)
    bundle.session = merge(bundle.session, settings.get("config", {}).get("session", {}))
    routing = settings.get("routing", {})
    if routing or any(row.get("module") == "hooks-routing" for row in bundle.hooks):
        from ..shared_settings import routing_dirs
        for hook in bundle.hooks:
            if hook.get("module") == "hooks-routing":
                patch = {"custom_routing_dirs": [str(path) for path in routing_dirs(config.workspace, shared_home=getattr(config, "config_home", None), global_only=getattr(config, "global_only", False))]}
                if routing.get("matrix"):
                    patch["default_matrix"] = routing["matrix"]
                if routing.get("overrides"):
                    patch["overrides"] = routing["overrides"]
                hook["config"] = merge(hook.get("config", {}), patch)
    from ..decision_settings import apply as apply_decisions
    apply_decisions(bundle, settings)
    from .ui_use_config import configure_bundle
    configure_bundle(bundle)
    overrides = settings.get("overrides", {})
    unavailable_image_providers = []
    for kind in ("providers", "tools", "hooks"):
        values = []
        for row in getattr(bundle, kind):
            override_id = row.get("id") or row.get("instance_id")
            if kind == "providers" and not override_id:
                override_id = row["module"].removeprefix("provider-")
            override = merge(overrides.get(row.get("module"), {}), overrides.get(override_id, {}))
            if override.get("enabled") is False or (kind == "providers" and (row.get("id") or row.get("instance_id") or row["module"].removeprefix("provider-")) in settings.get("configurator", {}).get("disabled", {}).get("providers", [])):
                if kind == "providers":
                    # Removing a connection must not erase its old image intent
                    # and silently authorize another account through routing.
                    # Keep only the migration's image fields, never credentials.
                    effective_config = merge(row.get("config", {}), override.get("config", {}))
                    image = effective_config.get("image_generation")
                    if isinstance(image, dict) and image:
                        unavailable_image_providers.append({
                            **{key: row[key] for key in ("module", "id", "instance_id") if key in row},
                            "enabled": False,
                            "config": {"image_generation": {
                                key: copy.deepcopy(value) for key, value in image.items()
                                if key in {"enabled", "id", "model"}
                            }},
                        })
                continue
            row = merge(row, {key:value for key,value in override.items() if key in {"source", "config"}})
            identity = row.get("id") or row.get("instance_id") or row.get("module")
            if kind == "tools" and row.get("module") == "tool-search":
                from ..read_policy import restore_search_declaration
                restore_search_declaration(row.setdefault("config", {}), search_declarations.get(identity, {}))
            if kind == 'tools' and row.get('module') == 'tool-filesystem':
                original = filesystem_allowlists.get(identity, {})
                for key in ('allowed_write_paths', 'allowed_read_paths'):
                    row.setdefault('config', {}).pop(key, None)
                    if key in original:
                        row['config'][key] = original[key]
            if kind == "tools" and row.get("module") == "tool-filesystem" and identity in filesystem_denials:
                for key, original in filesystem_denials[identity].items():
                    effective = row.get("config", {}).get(key, [])
                    if any(not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)
                           for paths in (original, effective)):
                        raise ValueError('File-access paths must be lists of strings.')
                    row.setdefault("config", {})[key] = list(dict.fromkeys(original + effective))
            if kind == "providers" and row.get("id") and not row.get("instance_id"):
                row["instance_id"] = row["id"]
            if kind == "providers":
                if "source" in row:
                    row["source"] = expand_environment(row["source"])
                values.append(row)
            else:
                values.append(expand_environment(row))
        setattr(bundle, kind, values)
    # Foundation intentionally skips source-less rows during preparation and
    # will not let another account's prepared module stand in at mount time.
    # Bind the source while composing ordinary host settings, before that
    # boundary. Do not copy credentials/config or rewrite explicit sources.
    sources = provider_mount_sources(bundle.providers, settings)
    for row in bundle.providers:
        identity = row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-')
        source = sources[identity]
        if source:
            row['source'] = source
    # Context and orchestrator are modules too (e.g. context-simple.token_meter).
    for kind in ("context", "orchestrator"):
        row = bundle.session.get(kind)
        if isinstance(row, dict):
            override = overrides.get(row.get("module"), {})
            bundle.session[kind] = merge(row, {key: value for key, value in override.items() if key in {"source", "config"}})
    from ..decision_accounts import bind_sources
    bind_sources({"providers": bundle.providers, "agents": bundle.agents}, getattr(config, "module_sources", {}))
    # Host provenance only: this is not a declaration to mount the removed rows.
    bundle._host_unavailable_image_providers = unavailable_image_providers
    return bundle


def is_snapshot(bundle):
    # Foundation preserves the public SemVer version field; arbitrary metadata
    # keys are discarded on load. Build metadata is portable to other hosts.
    from ..bundles import SNAPSHOT_VERSION
    return getattr(bundle, "version", None) == SNAPSHOT_VERSION


def _expand_module_configuration(node, in_provider=False):
    """Resolve declared configuration, leaving provider config for schema materialization."""
    if isinstance(node, list):
        return [_expand_module_configuration(value, in_provider=in_provider) for value in node]
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    provider = in_provider or node.get("module", "").startswith("provider-")
    result = {}
    for key, value in node.items():
        if key in {"config", "source"}:
            result[key] = copy.deepcopy(value) if provider and key == "config" else expand_environment(value)
        elif key in {"instruction", "instructions", "system_prompt"}:
            result[key] = copy.deepcopy(value)
        else:
            result[key] = _expand_module_configuration(value, in_provider=provider)
    if result.get("module", "").startswith("provider-") and result.get("id") and not result.get("instance_id"):
        result["instance_id"] = result["id"]
    return result


def _apply_host_policy(bundle, config, *, execution_workspace=None):
    """Host write boundaries cover file, patch and image tools, including snapshots."""
    from ..access_mode import restore_file_policy, full_file_policy
    restore_file_policy(bundle)
    settings = config.settings
    # A managed checkout can differ from the immutable history/config workspace.
    # Repository paths follow execution; unrelated explicitly allowed paths stay.
    workspace = str(Path(execution_workspace or config.workspace).expanduser().resolve())
    history_root = Path(config.workspace).expanduser().resolve()
    execution_root = Path(workspace)
    policy_keys = {"allowed_write_paths", "denied_write_paths", "allowed_read_paths", "denied_read_paths"}
    def paths(values):
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError('File-access paths must be lists of strings.')
        result = []
        for value in values:
            path = Path(value).expanduser()
            path = (path if path.is_absolute() else execution_root / path).resolve()
            if (execution_root != history_root and path.is_relative_to(history_root)
                    and not path.is_relative_to(execution_root)):
                path = execution_root / path.relative_to(history_root)
            result.append(path)
        return result

    # Settings support both legacy module lists and current config/overrides.
    # Resolve their precedence before sharing any writer's effective boundary.
    configured = merge(settings.get("modules", {}).get("tools", []), settings.get("config", {}).get("tools", []))
    policies = {"tool-filesystem": {}}
    overrides = settings.get("overrides", {})
    def discover(rows, children):
        for row in rows:
            if isinstance(row, dict) and row.get('module') == 'tool-filesystem':
                policies.setdefault(row.get('id') or row.get('instance_id') or 'tool-filesystem', {})
        for child in children.values():
            if isinstance(child, dict):
                discover(child.get('tools', []), child.get('agents', {}))
    discover(bundle.tools, bundle.agents)
    for row in configured:
        if isinstance(row, dict) and row.get("module") == "tool-filesystem":
            policies[row.get("id") or row.get("instance_id") or "tool-filesystem"] = row.get("config", {})
    for identity, values in policies.items():
        values = merge(values, overrides.get("tool-filesystem", {}).get("config", {}))
        if identity != "tool-filesystem":
            values = merge(values, overrides.get(identity, {}).get("config", {}))
        policy = expand_environment({key: value for key, value in values.items() if key in policy_keys
                                     and not (key == "allowed_read_paths" and value is None)})
        # Match the CLI's _ensure_cwd_in_write_paths policy. Shared allowed
        # directories extend project access; they must not replace it. Use an
        # absolute session workspace, never the server process's directory.
        if "allowed_write_paths" in policy:
            policy["allowed_write_paths"] = list(dict.fromkeys([workspace, *(str(path) for path in paths(policy["allowed_write_paths"]))]))
        policies[identity] = policy

    def restrict(current, policy):
        current, policy = copy.deepcopy(current), copy.deepcopy(policy)
        for value in (current, policy):
            if value.get("allowed_read_paths", False) is None:
                value.pop("allowed_read_paths")
        for kind in ('read', 'write'):
            allowed, denied = f'allowed_{kind}_paths', f'denied_{kind}_paths'
            if allowed in policy and allowed in current:
                shared, local = paths(policy[allowed]), paths(current[allowed])
                policy[allowed] = list(dict.fromkeys(str(a if a.is_relative_to(b) else b)
                    for a in shared for b in local if a.is_relative_to(b) or b.is_relative_to(a)))
            elif allowed in policy:
                policy[allowed] = [str(path) for path in paths(policy[allowed])]
            if denied in policy:
                policy[denied] = list(dict.fromkeys(str(path) for path in
                    paths(current.get(denied, [])) + paths(policy[denied])))
        return merge(current, policy)

    def apply(rows):
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or row.get("module") not in {"tool-filesystem", "tool-apply-patch", "tool-image"}:
                continue
            current = copy.deepcopy(row.get("config", {}))
            # Snapshot and child module settings have not been expanded yet.
            # Normalize paths only after resolving their environment references.
            for key in policy_keys & current.keys():
                current[key] = expand_environment(current[key])
            if current.get("allowed_read_paths", False) is None:
                current.pop("allowed_read_paths")
            if row["module"] == "tool-filesystem":
                identity = row.get("id") or row.get("instance_id") or "tool-filesystem"
                policy = policies.get(identity, policies["tool-filesystem"])
                # Instance overrides were resolved above before normalizing the
                # shared policy. Reapplying raw values here drops its defaults.
                # A declaration may deliberately be read-only or narrower than
                # the workspace. Defaults must not turn that into a new grant.
                row["config"] = restrict(current, policy)
                if "allowed_write_paths" not in row["config"]:
                    row["config"]["allowed_write_paths"] = [workspace]
                else:
                    row["config"]["allowed_write_paths"] = list(dict.fromkeys(
                        str(path) for path in paths(row["config"]["allowed_write_paths"])))
                # Shared settings may add restrictions, but must not erase
                # the declaration's explicitly denied directories.
                for key in ('denied_write_paths', 'denied_read_paths'):
                    if key in row['config']:
                        row['config'][key] = list(dict.fromkeys(str(path) for path in
                            paths(current.get(key, [])) + paths(policy.get(key, []))))
            else:
                # Intersect each effective filesystem policy with any explicitly
                # narrower patch policy, retaining every denied subtree.
                for policy in policies.values():
                    if policy:
                        current = restrict(current, policy)
                row["config"] = current
            # Modules resolve their allow/deny lists against process cwd, so
            # relative policy paths must always become execution-relative here.
            for key in policy_keys & row['config'].keys():
                row['config'][key] = list(dict.fromkeys(str(path) for path in paths(row['config'][key])))
    def agents(values):
        for agent in values.values():
            if isinstance(agent, dict):
                apply(agent.get("tools", []))
                agents(agent.get("agents", {}))
    apply(bundle.tools)
    agents(bundle.agents)
    # Patch and image APIs can create files without the filesystem tool.
    # Carry declaration-level restrictions as well as shared settings into
    # every image writer, including inherited child configurations.
    def writer_policies(rows, children, inherited):
        local = [{key: row.get('config', {})[key] for key in policy_keys & row.get('config', {}).keys()}
            for row in rows if isinstance(row, dict) and row.get('module') == 'tool-filesystem']
        effective = [*inherited, *local]
        for row in rows:
            if isinstance(row, dict) and row.get('module') in {'tool-image', 'tool-apply-patch'}:
                for policy in effective:
                    row['config'] = restrict(row.get('config', {}), policy)
        for child in children.values():
            if isinstance(child, dict):
                writer_policies(child.get('tools', []), child.get('agents', {}), effective)
    writer_policies(bundle.tools, bundle.agents, [])
    from ..read_policy import restrict_search
    configured_searches = {row.get('id') or row.get('instance_id') or row['module']: row.get('config', {})
        for row in configured if isinstance(row, dict) and row.get('module') == 'tool-search'}
    def search_policies(rows, children, inherited_files, inherited_searches):
        files = [*inherited_files, *policies.values(), *(row.get('config', {}) for row in rows
                 if isinstance(row, dict) and row.get('module') == 'tool-filesystem')]
        declarations = [row for row in rows if isinstance(row, dict) and row.get('module') == 'tool-search']
        for row in declarations:
            identity = row.get('id') or row.get('instance_id') or row['module']
            shared = merge(configured_searches.get(identity, {}), overrides.get('tool-search', {}).get('config', {}))
            if identity != 'tool-search':
                shared = merge(shared, overrides.get(identity, {}).get('config', {}))
            row['config'] = restrict_search(row.get('config', {}), files=files,
                searches=[*inherited_searches, shared], paths=paths)
        inherited = [*inherited_searches, *(row['config'] for row in declarations)]
        for child in children.values():
            if isinstance(child, dict):
                search_policies(child.get('tools', []), child.get('agents', {}), files, inherited)
    search_policies(bundle.tools, bundle.agents, [], [])
    return full_file_policy(bundle) if getattr(config, "access_mode", "ask") == "full" else bundle


async def compose_configured_bundle(registry, loaded, config, *, execution_workspace=None):
    """Snapshots are complete plans; ordinary roots inherit host composition."""
    from .model_selection import explicit_routing_settings, routing_hooks
    snapshot = is_snapshot(loaded)
    automatic_routing = False
    root_identity = {key: getattr(loaded, key) for key in ('name', 'version') if hasattr(loaded, key)}
    components = required_components()
    if not snapshot:
        from ..builtin_behaviors import (
            SHELL_BEHAVIOR_URI, IMAGEGEN_BEHAVIOR_URI, COMPUTER_USE_BEHAVIOR_URI,
            CONTEXT_INTELLIGENCE_BEHAVIOR_URI, ROUTING_BEHAVIOR_URI,
            default_behavior_uri, resolve_builtin_behavior,
        )
        from ..session_files import capture_dir, project_slug
        # App lists may contain saved defaults, but without explicit provenance
        # we cannot distinguish those from a deliberate selection. Stay strict.
        explicit_routing = bool(routing_hooks(loaded.to_mount_plan())) or any(
            default_behavior_uri(uri) == ROUTING_BEHAVIOR_URI
            for uri in config.settings.get("bundle", {}).get("app", []))
        context_repository = 'git+https://github.com/microsoft/amplifier-bundle-context-intelligence'
        # The capture fallback must share an explicit root sibling's revision,
        # even when no capability default registers the root's sources. Seed
        # only this repository before app overlays so later selections still win.
        for row in declarations(loaded.to_mount_plan()):
            source = config.module_sources.get(row['module']) or row.get('source')
            repository = git_source(source)
            if repository and repository[0] == context_repository:
                components.select(row['module'], source)
        context_defaults = set()
        for behavior in config.app_bundles:
            selected, _ = await load_configured_bundle(registry, config, resolve_builtin_behavior(behavior))
            canonical = default_behavior_uri(behavior)
            if routing_hooks(selected.to_mount_plan()):
                if (behavior == ROUTING_BEHAVIOR_URI and not explicit_routing
                        and config.resolve_source(behavior) is None):
                    automatic_routing = True
                else:
                    explicit_routing = True
                    automatic_routing = False
            if canonical == IMAGEGEN_BEHAVIOR_URI:
                loaded, selected = imagegen_defaults(loaded, selected)
            elif canonical == SHELL_BEHAVIOR_URI:
                # Shipped skills are additive discovery, not permission to
                # replace the selected loader, visibility or context budget.
                loaded, selected = capability_defaults(loaded, selected, {})
            elif canonical in {COMPUTER_USE_BEHAVIOR_URI, CONTEXT_INTELLIGENCE_BEHAVIOR_URI, ROUTING_BEHAVIOR_URI}:
                if not any(row.get("module") == "hook-context-intelligence" for row in loaded.hooks):
                    context_defaults.update(row.get("id") or row.get("instance_id") or row["module"]
                                            for row in selected.hooks if row.get("module") == "hook-context-intelligence")
                loaded, selected = capability_defaults(loaded, selected, {
                    'tool-computer-use': {'read_only': True},
                    'hook-context-intelligence': {
                        # Merely installing the behavior must not adopt ambient
                        # server credentials. Explicit scoped destinations win
                        # later, while existing hook declarations stay intact.
                        'destinations': {},
                        'context_intelligence_server_url': '',
                        'context_intelligence_api_key': '',
                        'base_path': str(capture_dir(config.workspace, 'root').parents[3]),
                        'project_slug': project_slug(config.workspace),
                    },
                })
            if canonical in {SHELL_BEHAVIOR_URI, COMPUTER_USE_BEHAVIOR_URI, CONTEXT_INTELLIGENCE_BEHAVIOR_URI, ROUTING_BEHAVIOR_URI}:
                components.select_defaults(selected, loaded, config.module_sources)
            else:
                components.select_bundle(selected, config.module_sources)
            loaded = compose_bundles(loaded, selected)
        if not any(row.get('module') == 'hook-context-intelligence' for row in loaded.hooks):
            from ..session_files import capture_dir, project_slug
            # The community hook owns kernel capture, discovery and metadata.
            # App destinations are explicit; ambient server keys never enable
            # forwarding merely because this default hook is present.
            loaded.hooks.append({'module': 'hook-context-intelligence',
                'source': context_repository + '@main#subdirectory=modules/hook-context-intelligence',
                'config': {'destinations': {}, 'base_path': str(capture_dir(config.workspace, 'root').parents[3]),
                           'project_slug': project_slug(config.workspace),
                           'additional_events': ['delegate:agent_spawned', 'delegate:agent_resumed', 'delegate:agent_completed', 'delegate:agent_cancelled', 'delegate:error']}})
            context_defaults.add("hook-context-intelligence")
        # Routing is now an ordinary opt-out-capable default. A retained matrix
        # setting must not silently restore a behavior the user disabled.
        explicit_context_forwarding(loaded, config.settings, context_defaults)
        from ..write_policy_refresh import WritePolicySource
        write_policy_source = WritePolicySource(loaded, config, execution_workspace=execution_workspace)
        loaded = _apply_settings(loaded, config)
        # Existing root defaults are explicit selections, not a new forced
        # version. Reuse their effective source for child/sibling declarations.
        for row in loaded.hooks:
            if row.get("module") in {"hook-context-intelligence", "hooks-routing"}:
                components.select(row["module"], config.module_sources.get(row["module"])
                                  or components.source(row["module"], row.get("source")))
    # Behaviors retain their own resource namespaces, but do not become the
    # selected conversation root through Foundation's last-overlay metadata.
    for key, value in root_identity.items():
        setattr(loaded, key, value)
    loaded = components.apply(loaded)
    if snapshot:
        from ..write_policy_refresh import WritePolicySource
        write_policy_source = WritePolicySource(loaded, config, snapshot=True,
                                               execution_workspace=execution_workspace)
    loaded._host_write_policy_source = write_policy_source
    # Credentials and safety policy remain local host responsibilities. They
    # do not add modules or replace the saved source/model/routing selections.
    loaded = _apply_host_policy(loaded, config, execution_workspace=execution_workspace)
    for key in (("providers", "tools", "hooks", "session", "agents") if snapshot else ("session", "agents")):
        setattr(loaded, key, _expand_module_configuration(getattr(loaded, key)))
    # This Python attribute is host provenance, not bundle/configuration data.
    # Recomputed from raw intent on root resume; never imported from snapshots.
    hooks = routing_hooks(loaded.to_mount_plan())
    loaded._host_automatic_routing = (
        automatic_routing and len(hooks) == 1
        and hooks[0].get("config", {}).get("default_matrix") == "balanced"
        and not explicit_routing_settings(config.settings, hooks))
    return loaded


def installed_loop_source():
    return installed_package_source('amplifier-module-loop-live', 'amplifier_module_loop_live')


def installed_naming_source():
    return installed_package_source('amplifier-module-hooks-session-naming', 'amplifier_module_hooks_session_naming')


def required_components():
    # Any bundle naming hook and the app adapter must share one implementation.
    # Resolve the included dependency without changing saved mount declarations.
    return HostComponents({'loop-live': LOOP_SOURCE}, {'loop-live': installed_loop_source,
        'hooks-session-naming': installed_naming_source})


def module_source(config, snapshot, module, source, components=None):
    components = components or required_components()
    selected = components.source(module, source, installed=True)
    owned = components.owns(module, source)
    result = selected if owned else source if snapshot else config.module_sources.get(module) or source
    observer = getattr(config, '_source_policy_observer', None)
    if observer:
        # Observe the winning host declaration too. A resolved relative module
        # may still belong to its observed Git-backed behavior; only an actual
        # local override or installed host component adds local-only intent.
        explicit_local = bool(not snapshot and config.module_sources.get(module) == result)
        explicit_local |= owned and module in getattr(components, 'installed', {})
        observer.observe(result, config, explicit_local=explicit_local)
    return result


async def load_configured_bundle(registry, config, reference):
    """Apply the same source selections to roots and app behaviors as includes."""
    from .bundle_paths import configured_bundle_reference, local_bundle_path
    chosen = configured_bundle_reference(config, reference, registry.find)
    observer = getattr(config, '_source_policy_observer', None)
    if observer:
        observer.observe(chosen, config, registry, explicit_local=local_bundle_path(config, chosen) is not None)
    return await registry.load(chosen), chosen


def session_registry(config):
    """Settings own aliases; neither CLI nor former imported registries do.

    Older locked Foundation versions lack the public read_persisted option.
    Suppress their constructor read and save hooks before construction so an
    old imported alias cannot silently become a source-selection fallback.
    """
    from amplifier_foundation import BundleRegistry
    def resolve_include(reference):
        selected = config.resolve_source(reference)
        observer = getattr(config, '_source_policy_observer', None)
        if observer:
            observer.observe(selected or reference, config, explicit_local=selected is not None)
        return selected
    options = dict(home=config.registry_home, strict=True,
                   include_source_resolver=resolve_include)
    parameters = inspect.signature(BundleRegistry).parameters
    if 'persist' in parameters:
        options['persist'] = False
    if 'read_persisted' in parameters:
        return BundleRegistry(**options, read_persisted=False)

    class SessionRegistry(BundleRegistry):
        def _load_persisted_state(self):
            """Start from scoped settings even with older Foundation."""

        def save(self):
            """Session composition must never modify saved registrations."""

    return SessionRegistry(**options)


async def load_root_bundle(config, chosen, *, execution_workspace=None):
    """Compose in a session-local registry view; settings own registrations."""
    registry = session_registry(config)
    registry.register(config.registrations)
    loaded, chosen = await load_configured_bundle(registry, config, chosen)
    loaded = await compose_configured_bundle(registry, loaded, config, execution_workspace=execution_workspace)
    return registry, loaded, chosen


@dataclass
class ResolvedRoot:
    """A single apply's validated composition, never a cross-request cache."""
    config: HostConfig
    bundle: str
    root: tuple | None
    execution_workspace: Path | str | None = None

    def __post_init__(self):
        self.execution_workspace = Path(self.execution_workspace or self.config.workspace).expanduser().resolve()

    def take(self, config, bundle, *, execution_workspace=None):
        workspace = Path(execution_workspace or config.workspace).expanduser().resolve()
        if self.root is None or config != self.config or bundle != self.bundle or workspace != self.execution_workspace:
            raise ValueError("The bundle configuration changed while switching. Try again.")
        root, self.root = self.root, None
        return root


def apply_runtime_plan(loaded, edited, config, execution_workspace):
    from ..runtime_controls import validate_plan
    from ..decision_accounts import logical_plan
    edited = logical_plan(edited)
    validate_plan(edited)
    if "hooks" in edited:
        # Even a same-valued balanced selection is explicit. An effective saved
        # plan has no authority to restore the automatic-origin exception.
        loaded._host_automatic_routing = False
    from ..provider_environment import rebind_provider_credentials
    edited, bound = rebind_provider_credentials(edited, loaded.to_mount_plan())
    loaded._host_provider_credentials_bound = bound
    for key in ("providers", "tools", "hooks"):
        if key in edited:
            setattr(loaded, key, [{k:v for k,v in row.items() if k != "enabled"}
                for row in edited[key] if row.get("enabled", True)])
    for key in ("session", "agents", "context", "instruction"):
        if key in edited:
            setattr(loaded, key, edited[key])
    from ..write_policy_refresh import WritePolicySource
    # An edited live plan is already composed. Retain its explicit ceilings,
    # rather than trusting provenance imported from bundle data.
    loaded._host_write_policy_source = WritePolicySource(loaded, config, snapshot=True,
                                                        execution_workspace=execution_workspace)
    loaded = _apply_host_policy(loaded, config, execution_workspace=execution_workspace)
    for key in ("providers", "tools", "hooks", "session", "agents"):
        if key in edited:
            setattr(loaded, key, _expand_module_configuration(getattr(loaded, key)))
    return loaded


async def prepare_dependencies(workspace, *, bundle=None, install_overrides=None, dependency_batch=None, global_only=False, runtime_plan=None, profile_catalog=None, source_policy_collector=None):
    """Prepare a fresh qualification worker before importing its live runtime.

    This first probe only installs configured dependencies. A separate process
    mounts the resulting frozen worker normally; source changes never mix an
    already imported wheel runtime with an editable cache in one interpreter.
    No conversation, history store, job recovery or model execution is opened.
    """
    config = load_config(workspace, global_only=global_only)
    if source_policy_collector is not None:
        config._source_policy_observer = source_policy_collector
    from .config import prepare_registry
    prepare_registry(config)
    execution_workspace = Path(config.workspace).expanduser().resolve(strict=True)
    os.chdir(execution_workspace)
    _, loaded, _ = await load_root_bundle(config, bundle or config.active_bundle,
                                          execution_workspace=execution_workspace)
    snapshot = is_snapshot(loaded)
    if runtime_plan is not None:
        loaded = apply_runtime_plan(loaded, runtime_plan, config, execution_workspace)
    if profile_catalog is not None:
        from ..profile_catalog import characteristics
        facts = characteristics(loaded)
        profile_catalog[bundle or config.active_bundle] = facts
        # Registration does not promise a usable Unified conversation. Keep
        # partial/custom-loop aliases for composition without preparing them as
        # standalone sessions. An unusable selected default remains an error.
        if not facts['complete'] or not facts['supportedLoop']:
            if (bundle or config.active_bundle) == config.active_bundle:
                if facts['hasLoop'] and not facts['supportedLoop']:
                    live_plan(loaded.to_mount_plan())  # Explain the unsupported choice.
                raise ValueError('The selected conversation profile is incomplete: ' + ', '.join(facts['missing']))
            return None
    adapted, _ = live_plan(loaded.to_mount_plan())
    components = getattr(loaded, '_host_components', None) or required_components()
    loaded.session = adapted['session']
    loaded.agents = adapted.get('agents', {})
    components.apply(loaded)
    from .config import configure_skill_cache
    configure_skill_cache(loaded, config.registry_home)
    return await loaded.prepare(strict=True, refresh_dependencies=True,
        **({"dependency_batch": dependency_batch} if dependency_batch is not None else {}),
        **({'install_overrides': Path(install_overrides)} if install_overrides is not None else {}),
        cache_dir=config.registry_home / 'cache',
        source_resolver=lambda module, source: module_source(config, snapshot, module, source, components))


def install_checkpoint_hooks(coordinator, runtime, checkpoint):
    """Keep automatic nested completion cheap; enclosing saves are unchanged."""
    import asyncio
    from amplifier_core import HookResult
    async def checkpoint_hook(event, data):
        if data.get("session_id", runtime.session_id) == runtime.session_id:
            from ..performance_diagnostics import PhaseTiming, TIMING_ENABLED
            emit = coordinator.get_capability('web.performance.emit')
            timing = (PhaseTiming(kind='checkpoint', toolCallId=data.get('tool_call_id'),
                runtimeSessionId=runtime.session_id,
                generationId=(runtime.generation or {}).get('id'))
                if TIMING_ENABLED and callable(emit) else None)
            outcome = 'completed'
            if timing:
                timing.mark('checkpoint.start')
            try:
                from ..coordination_delivery import nested_checkpoint
                await checkpoint(timing=timing, transcript_required=not (
                    event in {"tool:post", "tool:error"}
                    and nested_checkpoint(coordinator, data)))
            except BaseException as exc:
                outcome = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
                raise
            finally:
                if timing:
                    timing.mark('checkpoint.end')
                    try:
                        emit(timing.snapshot(outcome))
                    except Exception:
                        pass
        return HookResult()
    for event in ("tool:post", "tool:error", "orchestrator:complete", "context:compaction_finished"):
        coordinator.hooks.register(event, checkpoint_hook, name="unified-checkpoint-" + event)


async def prepare_manager(workspace, *, runtime=None, bundle=None, background_delegate=True,
                          ask=None, report_dir=None, resume=False, selection=None,
                          application_host="Amplifier Unified", shared_handle=None,
                          shared_handle_getter=None, shared_snapshot=None,
                          write_guard=None, resolved_root=None, execution_workspace=None, owner_session_id=None,
                          refresh_dependencies=False, install_overrides=None, qualification_readonly=False, global_only=False, runtime_plan=None, startup_timing=None, ephemeral_mcp_servers=None, **kwargs):
    from ..worker_diagnostics import StartupTimings
    startup_timing = startup_timing or StartupTimings()
    if refresh_dependencies and resume:
        raise ValueError("Dependency refresh is limited to a new isolated qualification session.")
    from amplifier_foundation import SessionConfigurator
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_module_loop_live.job_store import JobStore
    from amplifier_core import HookResult
    from .approvals import Approvals
    from .children import install_children, StandaloneHostAdapter
    from .storage import SessionStore

    runtime = runtime or Runtime()
    with startup_timing.phase('manager.config'):
        config = load_config(workspace, session_id=runtime.session_id, global_only=global_only,
            **({'owner_session_id': owner_session_id} if owner_session_id is not None else {}))
        from .config import prepare_registry
        prepare_registry(config)
    # Registry/cache ownership is passed explicitly below. AMPLIFIER_HOME must
    # remain the community data root, including for mounted CI logging hooks.
    execution_workspace = Path(execution_workspace or config.workspace).expanduser().resolve(strict=True)
    os.chdir(execution_workspace)
    from ..session_files import capture_dir
    if not os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'):
        os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'] = str(capture_dir(config.workspace, 'root').parents[3])
    store = SessionStore.for_app(config.home, config.workspace)
    # Native history is authoritative, including an intentionally empty file.
    # A common checkpoint is read only for an explicitly legacy-only session;
    # a stale or corrupt checkpoint cannot prevent opening valid native history.
    native_guard = _NativeTranscriptGuard(store.directory(runtime.session_id) / "transcript.jsonl")
    with startup_timing.phase('manager.history.load'):
        saved = store.load(runtime.session_id) if resume else None
        history_source = "native" if saved is not None else "new"
        if resume and saved is None:
            if shared_snapshot is None and shared_handle is not None:
                shared_snapshot = shared_handle.read()
            if shared_snapshot is not None:
                def legacy_value(name, default=None):
                    return shared_snapshot.get(name, default) if isinstance(shared_snapshot, dict) else getattr(shared_snapshot, name, default)
                messages, metadata = legacy_value("messages"), legacy_value("metadata", {})
                if not isinstance(messages, list) or not isinstance(metadata, dict):
                    raise ValueError("The legacy shared session checkpoint is malformed.")
                saved = messages, {**metadata, "bundle": legacy_value("bundle")}
                history_source = "legacy-checkpoint"
            else:
                store._migrate(runtime.session_id)
                # Migration intentionally writes once; take its stamp before the
                # read so a concurrent older CLI write cannot become our baseline.
                native_guard.saved()
                saved = store.load(runtime.session_id)
                if saved is None and shared_handle is None:
                    saved = store.import_cli(runtime.session_id, workspace=config.workspace)
                if saved is not None:
                    history_source = "native"
        native_guard.check()
    saved_bundle = (saved[1].get("bundle_name") or saved[1].get("bundle")) if saved else None
    if isinstance(saved_bundle, str):
        saved_bundle = saved_bundle.removeprefix("bundle:")
    if saved_bundle is not None and (not isinstance(saved_bundle, str) or not saved_bundle.strip()):
        raise ValueError("The saved session has no resolvable bundle.")
    from .bundle_paths import canonical_bundle_reference
    chosen = canonical_bundle_reference(config, saved_bundle or bundle or config.active_bundle)
    bundle_identity = chosen
    directory = Path(report_dir or config.home / "runtime-reports" / runtime.session_id)
    with startup_timing.phase('manager.bundle.load'):
        registry, loaded, chosen = (resolved_root.take(config, chosen, execution_workspace=execution_workspace) if resolved_root is not None
                                   else await load_root_bundle(config, chosen, execution_workspace=execution_workspace))
    with startup_timing.phase('manager.plan'):
        snapshot = is_snapshot(loaded)
        from ..runtime_controls import override_path
        edited_path = override_path(runtime.session_id)
        if runtime_plan is not None or edited_path.exists():
            edited = runtime_plan if runtime_plan is not None else json.loads(edited_path.read_text())
            loaded = apply_runtime_plan(loaded, edited, config, execution_workspace)
        if not snapshot:
            from .mentions import include_instruction_files
            loaded = include_instruction_files(loaded, config_home=config.settings_file.parent,
                                               execution_workspace=execution_workspace,
                                               workspace=config.workspace)
        baseline = loaded.to_mount_plan()
        from ..provider_recording import apply_provider_recording
        apply_provider_recording(baseline, home=config.home)
        loaded.providers = baseline.get('providers', [])
        loaded.agents = baseline.get('agents', {})
        adapted, replacements = live_plan(baseline, background_delegate)
        # Modify the public Bundle fields before prepare(): loop-live and every
        # agent-specific source go through Foundation's normal activation mechanism.
        loaded.session = adapted["session"]
        loaded.agents = adapted.get("agents", {})
        components = getattr(loaded, "_host_components", None) or required_components()
        components.apply(loaded)
        from .config import configure_skill_cache
        configure_skill_cache(loaded, config.registry_home)
        adapted = components.normalize(adapted)
        write_private(directory / "baseline-mount-plan.json", json.dumps(redact(baseline), indent=2, default=str))
        write_private(directory / "live-mount-plan.json", json.dumps(redact(adapted), indent=2, default=str))
    def progress(action, detail):
        # The callback's detail can be a URL with credentials. Publish only the
        # documented phase, never arbitrary source strings or installer logs.
        if runtime.observer:
            runtime.observer({"type": "runtime.progress", "phase": "bundle-preparation",
                "detail": "Preparing community modules (" + str(action).replace("_", " ")[:60] + ")."})
    from ..provider_isolation import ProviderIsolation, UnavailableProvider, supported
    isolation = ProviderIsolation() if supported() else None
    preparation_policy = ({"provider_failure_policy": isolation.preparation_failed} if isolation else {})
    if refresh_dependencies:
        preparation_policy["refresh_dependencies"] = True
    if install_overrides is not None:
        preparation_policy["install_overrides"] = Path(install_overrides)
    # Activate modules from the same generation as the bundle registry. The
    # shared AMPLIFIER_HOME still owns history/settings, not app module caches.
    with startup_timing.phase('manager.bundle.prepare'):
        prepared = await loaded.prepare(strict=True, install_deps=not (qualification_readonly or os.environ.get("AMPLIFIER_RUNTIME_IMMUTABLE") == "1"), **preparation_policy,
            cache_dir=config.registry_home / "cache",
            source_resolver=lambda module, source: module_source(config, snapshot, module, source, components),
            progress_callback=progress)
    if getattr(prepared, "resolver", None) is not None:
        prepared.resolver = ComponentResolver(prepared.resolver, components)
    with startup_timing.phase('manager.providers'):
        await materialize_bundle_providers(loaded, prepared, **({"isolation": isolation} if isolation else {}))
        # Translate old explicit image intent only in new unbound plans. No account
        # discovery or provider ranking belongs in the host. Snapshots stay intact.
        if not snapshot and not getattr(loaded, '_host_provider_credentials_bound', False):
            from ..image_migration import apply_legacy_image_intent
            apply_legacy_image_intent(
                prepared.mount_plan, config.settings,
                scoped_sources=config.image_intent_sources,
                unavailable_providers=getattr(loaded, '_host_unavailable_image_providers', ()))
        environment_plan = prepared.mount_plan
        if isolation:
            environment_plan = copy.deepcopy(environment_plan)
            for row in iter_provider_rows(environment_plan):
                if isolation.failure(row):
                    row["enabled"] = False
        apply_provider_environment(environment_plan)
    from ..decision_accounts import DecisionAccounts, AccountResolver, logical_plan
    decision_accounts = DecisionAccounts(prepared.mount_plan, isolation,
        lambda module, source: module_source(config, snapshot, module, source, components))
    prepared.mount_plan = decision_accounts.plan
    if decision_accounts.aliases:
        prepared.resolver = AccountResolver(prepared.resolver, decision_accounts.aliases)
    from ..session_files import project_slug
    prepared.mount_plan.update(application_host=application_host, root_session_id=runtime.session_id,
        project_slug=project_slug(config.workspace),
        bundle_name=chosen, project_dir=str(config.workspace), project_name=config.workspace.name)
    with startup_timing.phase('manager.history.repair'):
        messages = saved[0] if saved else None
        jobs = JobStore(store.base_dir / runtime.session_id / "live-jobs")
        recovered = []
        if jobs.rows:
            if not resume:
                jobs.close()
                raise RuntimeError("Existing job evidence requires explicit resume")
            messages, recovered = jobs.recover(messages or [])
        if messages is not None:
            messages = repair_interrupted_receipts(messages)
    approvals = Approvals(runtime, ask)
    session = None
    from ..provider_catalog import mounted_catalog_keys
    catalog_sources={row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-'):
        module_source(config,snapshot,row['module'],row.get('source'),components)
        for row in prepared.mount_plan.get('providers',[])}
    catalog_keys=mounted_catalog_keys(config.home,config.workspace,prepared.mount_plan.get('providers',[]),catalog_sources)
    connection_keys=mounted_catalog_keys(config.home,config.workspace,prepared.mount_plan.get('providers',[]),catalog_sources,binding=True)
    try:
        from .initialization import initialization_hooks
        from .model_selection import prepare_default_routing
        routing = (partial(prepare_default_routing,
                           automatic=getattr(loaded, "_host_automatic_routing", False))
                   if not snapshot and any(row.get("module") == "hooks-routing" for row in loaded.hooks) else None)
        def retain_session(constructing):
            nonlocal session
            session = constructing
        initialization = initialization_hooks(isolation=isolation, decision_accounts=decision_accounts,
                                              routing=routing, retain=retain_session,
                                              ephemeral_servers=ephemeral_mcp_servers)
        with startup_timing.phase('manager.session.create'):
            session = await prepared.create_session(session_id=runtime.session_id,
                session_cwd=execution_workspace, approval_system=approvals, is_resumed=messages is not None,
                **initialization)
        decision_accounts.require_complete()
        coordinator = session.coordinator
        if ephemeral_mcp_servers is not None:
            ready = coordinator.get_capability("mcp.ephemeral_ready")
            if ready != sorted(ephemeral_mcp_servers):
                raise RuntimeError("The selected bundle did not install the requested ephemeral MCP servers. No task input was sent.")
        source = getattr(loaded, '_host_write_policy_source', None)
        if source is not None:
            coordinator.register_capability('web.write_policy.source', source)
        coordinator.register_capability("approval.interactive", approvals.interactive)
        from ..read_policy import validate_mounted_read_policy
        validate_mounted_read_policy(coordinator)
        from ..provider_recording import install_request_redaction
        from ..session_files import capture_dir
        install_request_redaction(coordinator, directory=capture_dir(config.workspace, runtime.session_id))
        coordinator.register_capability('web.history_workspace', str(config.workspace))
        coordinator.register_capability('web.provider_credentials_bound',
                                        bool(decision_accounts.records) or getattr(loaded, '_host_provider_credentials_bound', False))
        # Freeze credential/file/source/generation identity before provider
        # construction. A later rotation cannot relabel an old mounted result.
        coordinator.register_capability('web.provider_catalog', {
            'home':str(config.home),'workspace':str(config.workspace),
            'sources':catalog_sources,'keys':catalog_keys,'connectionKeys':connection_keys})
        coordinator.register_capability("live.runtime", runtime)
        coordinator.register_capability("live.jobs", jobs)
        coordinator.register_capability("live.recovered_jobs", [row["job_id"] for row in recovered])
        adapter = StandaloneHostAdapter()
        coordinator.register_capability("live.host", adapter)
        register = coordinator.get_capability("approval.register_provider")
        if register:
            register(approvals)
        with startup_timing.phase('manager.context.restore'):
            if messages is not None:
                await coordinator.get("context").set_messages(messages)
        with startup_timing.phase('manager.children'):
            await install_children(session, prepared, runtime, store, approvals, host_adapter=adapter)
        configurator = SessionConfigurator(session, prepared)
        coordinator.register_capability("web.configurator", configurator)
        coordinator.register_capability("web.prepared", prepared)
        coordinator.register_capability("web.registry", registry)
        # Foundation removes disabled agents from the live map. Retain the
        # exact mounted declarations before shared settings can disable them.
        coordinator.register_capability("web.agent_definitions",
                                        copy.deepcopy(coordinator.config.get("agents", {})))
        with startup_timing.phase('manager.settings'):
            if not snapshot:
                await configurator.apply_saved_settings(config.settings.get("configurator") or {})
            configurator.take_snapshot()
        providers = coordinator.get("providers") or {}
        if not providers:
            raise RuntimeError("No provider is configured. Add config.providers to the shared Amplifier settings.yaml.")
        with startup_timing.phase('manager.provider.info'):
            choices = []
            for identity, provider in providers.items():
                info = provider.get_info()
                if inspect.isawaitable(info):
                    info = await info
                defaults = getattr(info, "defaults", {}) or {}
                choices.append({"id": identity, "provider": getattr(info, "id", identity),
                    "display_name": getattr(info, "display_name", None),
                    **({"available": False, "failure": provider.failure} if isinstance(provider, UnavailableProvider) else {}),
                    "model": defaults.get("model"), "effort": defaults.get("reasoning_effort") or next((row.get("config",{}).get("reasoning_effort") for row in prepared.mount_plan.get("providers",[]) if (row.get("instance_id") or row.get("id") or row["module"].removeprefix("provider-"))==identity),None), "sharedCatalogKey":catalog_keys.get(identity), "models": []})
        # Keep public connection choices available even when a saved ID prevents
        # startup. Reading this receipt never chooses a connection or runs work.
        write_private(directory / "provider-choices.json", json.dumps({
            "session_id": runtime.session_id, "workspace": str(config.workspace),
            "provider_choices": choices, "selection": selection}, default=str))
        loop = coordinator.get("orchestrator")
        if selection:
            identity = selection.get("instance")
            if identity not in providers or not selection.get("model"):
                from .model_selection import ProviderSelectionError
                raise ProviderSelectionError("Root selection needs an available provider instance and model")
            loop.root_provider = SelectedProvider(providers[identity], selection)
        from ..provider_isolation import require_selected
        require_selected(coordinator)
        selected = loop._select_provider(providers)
        effective = selection or next((row for row in choices if providers[row["id"]] is selected), None)
        metadata = {**({key:saved[1][key] for key in ("fork","preserve_system") if key in saved[1]} if saved else {}),
            "session_id": runtime.session_id, "parent_id": None,
            "bundle_name": bundle_identity, "working_dir": str(config.workspace),
            "created": (saved[1].get("created") if saved else None) or datetime.now(UTC).isoformat(),
            "application_host": application_host, "config": redact(logical_plan(session.config))}
        continuity = None
        published_anchors = None
        def recovery_write_guard():
            # A recovery write uses the same native ownership checks, without
            # saving active state over a separately verified candidate.
            if write_guard:
                write_guard()
            held = shared_handle_getter() if shared_handle_getter else shared_handle
            if shared_handle_getter and held is None:
                raise RuntimeError("Native writer ownership is unavailable")
            native_guard.check()
            if held is not None:
                held.check()
        async def checkpoint(status="in_progress", *, timing=None, transcript_required=True):
            nonlocal published_anchors
            if write_guard:
                write_guard()
            persist_controls = coordinator.get_capability("web.controls.persist")
            if persist_controls:
                persist_controls()
            if not transcript_required:
                # Inner tools have not appended a conversation result. Keep
                # authority and control persistence at every boundary; the
                # existing enclosing/terminal saves retain context changes.
                held = shared_handle_getter() if shared_handle_getter else shared_handle
                native_guard.check()
                if held is not None:
                    held.check()
                return
            if timing:timing.mark('context.start')
            transcript = await coordinator.get("context").get_messages()
            if timing:timing.mark('context.end')
            held = shared_handle_getter() if shared_handle_getter else shared_handle
            native_guard.check()
            if held is not None:
                # Cross-host ownership remains Foundation's responsibility;
                # native transcript/metadata are the only newly written history.
                held.check()
            if timing:timing.mark('store.start')
            canonical = store.save(runtime.session_id, transcript, {**metadata, "status": status,
                "last_updated": datetime.now(UTC).isoformat(),
                "turn_count": sum(row.get("role") == "user" for row in transcript)})
            if timing:timing.mark('store.end')
            native_guard.saved()
            if continuity:
                if timing:timing.mark('continuity.start')
                continuity.save()
                if timing:timing.mark('continuity.end')
            from ..collaboration_input import checkpoint_anchors
            anchors = checkpoint_anchors(canonical, runtime)
            if anchors and anchors != published_anchors:
                # Transcript persistence above is unconditional. This bounded
                # notification carries message/generation identity, not a save
                # heartbeat; unchanged tool checkpoints add no host authority.
                await runtime.emit("collaboration.checkpoint", generation_id=anchors[-1]["generationId"],
                                   messageAnchors=anchors)
                published_anchors = anchors  # A failed emit remains retryable.
            return canonical
        coordinator.register_capability("live.checkpoint", checkpoint)
        from ..context_continuity import install as install_continuity
        continuity = install_continuity(coordinator, runtime.session_id, edited_path.parent,
                                       checkpoint, writer_guard=recovery_write_guard)
        install_checkpoint_hooks(coordinator, runtime, checkpoint)
        async def cleanup_jobs():
            jobs.close()
        coordinator.register_cleanup(cleanup_jobs)
        with startup_timing.phase('manager.checkpoint'):
            await checkpoint()
        failures = getattr(loop, "load_failures", [])
        fatal_failures = [row for row in failures if not isolation or row.get("type") != "provider"]
        if fatal_failures:
            from ..module_failures import persist_failures
            raise persist_failures(directory, fatal_failures)
        from ..module_failures import clear_failures
        clear_failures(directory)
        # Stamp only explicit, local bundle resources actually consumed by this
        # mount. Registry caches and reports are intentionally excluded because
        # they are rewritten by normal preparation.
        # Native history has its own worker stamp; these are additional mount
        # inputs, separate from files changed by normal saves.
        config_inputs = []
        for reference in (chosen, *config.app_bundles):
            path = Path(reference).expanduser()
            if not path.is_absolute():
                path = config.workspace / path
            if path.is_file():
                config_inputs.append(str(path.resolve()))
        from .model_selection import delegation_routing
        report = {"delegationRouting": delegation_routing(coordinator), "bundle": chosen, "root_bundle": bundle_identity, "workspace": str(config.workspace),
            "contextIntelligence": {"enabled": bool((coordinator.get_capability('context_intelligence._hook_state') or {}).get('unregister_fns'))},
            "session_id": runtime.session_id, "resumed": messages is not None,
            "providers": list(providers), "tools": list(coordinator.get("tools") or {}),
            "agents": list(prepared.mount_plan.get("agents", {})), "provider_choices": choices,
            "selection": selection, "effective_selection": effective, "replaced": replacements,
            "steering": "request_boundary", "native": False, "standalone": True,
            "settings_file": str(config.settings_file),
            "capabilities": {name: coordinator.get_capability(name) is not None for name in
                ("session.spawn", "session.resume", "mention_resolver", "model_role_resolver")},
            "module_load_failures": fatal_failures,
            "provider_failures": isolation.outcomes(coordinator) if isolation else [],
            "decision_account_failures": decision_accounts.failures}
        report["config_inputs"] = config_inputs
        report["history_source"] = history_source
        report["execution_workspace"] = str(execution_workspace)
        with startup_timing.phase('manager.report'):
            write_private(directory / "mounted.json", json.dumps(redact(report), indent=2, default=str))
            registry.save()
        return session, runtime, report
    except BaseException:
        if session:
            await session.cleanup()
        jobs.close()
        raise
