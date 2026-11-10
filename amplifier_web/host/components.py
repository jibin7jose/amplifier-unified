"""One source per host-selected component, including late child declarations.

Only declared host behaviors and required host dependencies own sources. Bundle
configuration keeps normal later-overlay precedence and distinct mount instances.
This policy selects sources; Foundation activates them and Core validates imports.
"""
from __future__ import annotations

import copy
import importlib.metadata
import importlib.util
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


class HostComponentConflict(ValueError):
    def __init__(self):
        super().__init__('Host-selected components have incompatible identities or source ownership. '
                         'Check the app behavior module declarations and instance IDs; no session was started.')


def merge_modules(*groups):
    """Merge actual duplicates without losing instance_id-only declarations."""
    result, indexes = [], {}
    for rows in groups:
        if not isinstance(rows, list):
            raise HostComponentConflict()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('module'), str):
                raise HostComponentConflict()
            identity = row.get('id') or row.get('instance_id') or row['module']
            if not isinstance(identity, str) or (row.get('id') and row.get('instance_id')
                                               and row['id'] != row['instance_id']):
                raise HostComponentConflict()
            if identity in indexes:
                from amplifier_foundation import deep_merge
                index = indexes[identity]
                if result[index]['module'] != row['module']:
                    raise HostComponentConflict()
                result[index] = deep_merge(result[index], row)
            else:
                indexes[identity] = len(result)
                result.append(copy.deepcopy(row))
    return result


def agent_roster(value):
    """Access selectors are not declaration mappings; never rewrite the selector."""
    if isinstance(value, dict):
        return value
    if value in ("all", "none") or (
            isinstance(value, list) and all(isinstance(name, str) for name in value)):
        return {}
    raise ValueError('Agent declarations must be a mapping, "all", "none", or a list of names.')


def declarations(plan):
    for section in ('providers', 'tools', 'hooks'):
        yield from plan.get(section, [])
    for row in plan.get('session', {}).values():
        if isinstance(row, dict) and 'module' in row:
            yield row
    yield from plan.get('spawn', {}).get('tools', [])
    for agent in agent_roster(plan.get('agents', {})).values():
        if isinstance(agent, dict):
            yield from declarations(agent)


async def prepare_bundle(bundle, **kwargs):
    """Activate nested declarations without treating access selectors as rosters.

    Foundation's ordinary preparation visits immediate agents only; its batched
    walk assumes every nested agents value is a mapping. Flatten an activation-
    only copy, then retain the exact executable plan and original bundle.
    """
    projected = copy.copy(bundle)
    projected.agents = {}
    agent_names = {}

    def visit(values, path=()):
        for name, definition in agent_roster(values).items():
            if not isinstance(definition, dict):
                continue
            key = json.dumps((*path, name))
            agent_names[key] = name
            projected.agents[key] = {k: copy.deepcopy(v) for k, v in definition.items() if k != 'agents'}
            visit(definition.get('agents', {}), (*path, name))

    # Leave ordinary/mock bundles on their existing direct preparation path.
    if not any(isinstance(agent, dict) and 'agents' in agent for agent in getattr(bundle, 'agents', {}).values()):
        return await bundle.prepare(**kwargs)
    visit(bundle.agents)
    # Preparation-only keys must not leak into host provider failure policy or
    # retained diagnostics. Foundation reports the declared agent's own name.
    from dataclasses import replace
    def attribution(failure):
        return replace(failure, agent_name=agent_names.get(failure.agent_name, failure.agent_name))
    policy = kwargs.get('provider_failure_policy')
    if policy is not None:
        async def failed(failure):
            await policy(attribution(failure))
        kwargs['provider_failure_policy'] = failed
    prepared = await projected.prepare(**kwargs)
    if hasattr(prepared, 'provider_preparation_failures'):
        prepared.provider_preparation_failures = tuple(attribution(failure)
            for failure in prepared.provider_preparation_failures)
    prepared.bundle = bundle
    prepared.mount_plan = bundle.to_mount_plan()
    return prepared


def git_source(source):
    """Keep full repository identity; never infer ownership from a basename."""
    if not isinstance(source, str) or not source.startswith('git+'):
        return None
    base, _, fragment = source.partition('#')
    # Revision separators occur after the URL authority (which may contain @).
    parsed = urlsplit(base[4:])
    path, at, revision = parsed.path.rpartition('@')
    repository = base[:-(len(revision) + 1)] if at else base
    return repository.removesuffix('.git'), base, fragment


def installed_package_source(distribution_name, package_name):
    """Locate a host dependency without importing code from a cache checkout."""
    distribution = importlib.metadata.distribution(distribution_name)
    direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    if direct.get('dir_info', {}).get('editable') is True:
        parsed = urlsplit(direct.get('url', ''))
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            raise ValueError('The installed host component has unsupported local source metadata.')
        root = Path(unquote(parsed.path)).resolve()
        candidates = [root / package_name, root / 'src' / package_name]
    else:
        candidates = [Path(distribution.locate_file(package_name)).resolve()]
    candidates = [path for path in candidates if (path / '__init__.py').is_file()]
    spec = importlib.util.find_spec(package_name)
    if (len(candidates) != 1 or spec is None or not spec.origin
            or Path(spec.origin).resolve() != (candidates[0] / '__init__.py').resolve()):
        raise ValueError('The host component import does not match its installed distribution; its source was preserved.')
    return str(candidates[0])


class HostComponents:
    def __init__(self, sources=None, installed=None):
        self.sources = dict(sources or {})
        self.installed = dict(installed or {})
        self.repositories = {}
        self.roots = {}
        self.path_roots = {}

    def select(self, module, source):
        if module in self.installed:
            return
        if not isinstance(source, str) or not source:
            return
        self.sources[module] = source
        git = git_source(source)
        if git:
            self.repositories[git[0]] = git[1]
            for identity, value in self.sources.items():
                sibling = git_source(value)
                if sibling and sibling[0] == git[0]:
                    self.sources[identity] = git[1] + ('#' + sibling[2] if sibling[2] else '')

    def select_bundle(self, bundle, overrides):
        for row in declarations(bundle.to_mount_plan()):
            source = overrides.get(row['module']) or row.get('source')
            if isinstance(source, str) and source.startswith('./') and bundle.base_path:
                source = str((bundle.base_path / source).resolve())
            self.select(row['module'], source)
        self.roots.update(bundle.source_base_paths)
        if bundle.name and bundle.base_path:
            self.roots.setdefault(bundle.name, bundle.base_path)

    def select_defaults(self, bundle, base, overrides):
        """Preserve only base pins that collide with this default's declarations."""
        declared = getattr(bundle, '_host_default_sources', None)
        if declared is None:
            declared = [(row['module'], row.get('source'))
                        for row in declarations(bundle.to_mount_plan())]
        modules = {module for module, _ in declared}
        repositories = set()
        for module, source in declared:
            git = git_source(overrides.get(module) or source)
            if git:
                repositories.add(git[0])
        self.select_bundle(bundle, overrides)
        for row in declarations(base.to_mount_plan()):
            source = overrides.get(row['module']) or row.get('source')
            git = git_source(source)
            if row['module'] not in modules and (not git or git[0] not in repositories):
                continue
            # Non-Git sources collide by module only, never by a shared None
            # repository. Resolve relative base pins against their own bundle.
            if isinstance(source, str) and source.startswith('./') and base.base_path:
                source = str((base.base_path / source).resolve())
            self.select(row['module'], source)

    def source(self, module, source, *, installed=False):
        if installed and module in self.installed:
            return self.installed[module]()
        if module in self.sources:
            return self.sources[module]
        git = git_source(source)
        target = self.repositories.get(git[0]) if git else None
        if target:
            return target + ('#' + git[2] if git[2] else '')
        if isinstance(source, str):
            parsed = urlsplit(source)
            path = Path(unquote(parsed.path)) if parsed.scheme == 'file' else Path(source)
            if parsed.scheme in ('', 'file') and path.is_absolute():
                for old, chosen in self.path_roots.items():
                    if path.is_relative_to(old):
                        replacement = chosen / path.relative_to(old)
                        return (replacement.as_uri() + ('#' + parsed.fragment if parsed.fragment else '')
                                if parsed.scheme == 'file' else str(replacement))
        return source

    def owns(self, module, source):
        git = git_source(source)
        if module in self.sources or module in self.installed or (git and git[0] in self.repositories):
            return True
        if isinstance(source, str):
            parsed = urlsplit(source)
            path = Path(unquote(parsed.path)) if parsed.scheme == 'file' else Path(source)
            if parsed.scheme in ('', 'file') and path.is_absolute():
                return any(path.is_relative_to(root) for root in [*self.path_roots, *self.roots.values()])
        return False

    def normalize(self, plan):
        result = copy.deepcopy(plan)
        def visit(node):
            for section in ('providers', 'tools', 'hooks'):
                if section in node:
                    node[section] = merge_modules(node[section])
            if isinstance(node.get('spawn', {}).get('tools'), list):
                node['spawn']['tools'] = merge_modules(node['spawn']['tools'])
            for agent in agent_roster(node.get('agents', {})).values():
                if isinstance(agent, dict):
                    visit(agent)
        visit(result)
        for row in declarations(result):
            selected = self.source(row['module'], row.get('source'))
            if selected is not None:
                row['source'] = selected
        return result

    def apply(self, bundle):
        old_roots = dict(getattr(bundle, 'source_base_paths', {}))
        for namespace, root in self.roots.items():
            old = old_roots.get(namespace)
            if old and Path(old) != Path(root):
                self.path_roots[Path(old)] = Path(root)
        plan = self.normalize(bundle.to_mount_plan())
        for key in ('providers', 'tools', 'hooks', 'session', 'spawn', 'agents'):
            if key in plan:
                setattr(bundle, key, plan[key])
        # Foundation composes namespaces first-wins. Host-selected behaviors
        # must also own their package/resource roots, not only module source rows.
        for namespace, root in self.roots.items():
            old = old_roots.get(namespace)
            if old and Path(old) != Path(root):
                for key, path in bundle.context.items():
                    if key.startswith(namespace + ':') and Path(path).is_relative_to(old):
                        bundle.context[key] = Path(root) / Path(path).relative_to(old)
        if self.roots:
            bundle.source_base_paths.update(self.roots)
        bundle._host_components = self
        return bundle


def compose_bundles(base, overlay):
    """Retain Foundation resources/provenance and host instance merge semantics."""
    result = base.compose(overlay)
    for section in ('providers', 'tools', 'hooks'):
        setattr(result, section, merge_modules(getattr(base, section), getattr(overlay, section)))
    return result


def imagegen_defaults(base, behavior):
    """Add image discovery without replacing an explicit root tool's policy.

    The canonical host behavior adds a capability; module settings still choose
    its configuration. Persisting the default in an explicit app list must not
    change that policy. These tools expose fixed names, so a named module instance
    also counts as an existing mount. Other overlays keep normal precedence.
    """
    base, behavior = copy.deepcopy(base), copy.deepcopy(behavior)
    existing = {row['module'] for row in base.tools}
    skill_paths = []
    for row in behavior.tools:
        if row['module'] == 'tool-skills':
            skill_paths.extend(row.get('config', {}).get('skills', []))
    if 'tool-skills' in existing:
        for row in base.tools:
            if row['module'] == 'tool-skills':
                config = row.setdefault('config', {})
                config['skills'] = list(dict.fromkeys([*config.get('skills', []), *skill_paths]))
    behavior.tools = [row for row in behavior.tools
                      if row['module'] not in {'tool-image', 'tool-skills'} or row['module'] not in existing]
    return base, behavior


def capability_defaults(base, behavior, defaults):
    """Add discovery and missing modules without overriding prior choices.

    A default behavior is not a user configuration overlay. Fixed-name tools
    and hooks already declared by a root/earlier behavior keep their identity,
    source and configuration. Only additive discovery paths are extended.
    Scoped module settings are applied afterward by the host.
    """
    base, behavior = copy.deepcopy(base), copy.deepcopy(behavior)

    def prepare(node):
        for row in declarations(node):
            if row['module'] in defaults:
                row.setdefault('config', {}).update(copy.deepcopy(defaults[row['module']]))

    plan = behavior.to_mount_plan()
    # Selection follows duplicate removal. Retain the original collision set,
    # including a default whose only declaration was an existing root module.
    behavior._host_default_sources = [(row['module'], row.get('source'))
                                      for row in declarations(plan)]
    prepare(plan)
    for section in ('tools', 'hooks', 'agents', 'spawn'):
        if section in plan:
            setattr(behavior, section, plan[section])
    for section in ('tools', 'hooks'):
        existing = getattr(base, section)
        additions = []
        for row in getattr(behavior, section):
            previous = [item for item in existing if item['module'] == row['module']]
            if not previous:
                additions.append(row)
                continue
            # Preserve explicit empty/restrictive configurations too. These two
            # paths add capability discovery; settings can still replace them.
            path_key = {'tool-skills': 'skills', 'hooks-mode': 'search_paths'}.get(row['module'])
            if path_key:
                for item in previous:
                    config = item.setdefault('config', {})
                    config[path_key] = list(dict.fromkeys([
                        *config.get(path_key, []), *row.get('config', {}).get(path_key, [])]))
        setattr(behavior, section, additions)
    # A default can introduce a specialist, not reconfigure a same-named one.
    behavior.agents = {name: agent for name, agent in behavior.agents.items()
                       if name not in base.agents}
    return base, behavior


def explicit_context_forwarding(bundle, settings, default_instances=()):
    """Legacy forwarding needs configuration, not just ambient credentials.

    The canonical logging behavior carries the ambient placeholders itself.
    Existing literal/custom bindings and scoped scalar/destination overrides
    are explicit selections. Empty destinations remain a deliberate opt-out.
    """
    ambient = {
        "context_intelligence_server_url": "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL:}",
        "context_intelligence_api_key": "${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY:}",
    }
    for row in bundle.hooks:
        if row.get("module") != "hook-context-intelligence":
            continue
        config = row.setdefault("config", {})
        patches = [item.get("config", {}) for section in ("modules", "config")
                   for item in settings.get(section, {}).get("hooks", [])
                   if item.get("module") == row["module"]]
        overrides = settings.get("overrides", {})
        patches.extend(overrides.get(key, {}).get("config", {}) for key in
                       (row["module"], row.get("id") or row.get("instance_id")) if key)
        scoped_legacy = any(any(key in patch for key in ambient) for patch in patches)
        identity = row.get("id") or row.get("instance_id") or row["module"]
        explicit_legacy = any(config.get(key) and config[key] != placeholder
                              for key, placeholder in ambient.items())
        if (identity in default_instances and scoped_legacy
                and not any("destinations" in patch for patch in patches)
                and config.get("destinations") == {}):
            # Scoped explicit legacy settings are applied next. Drop only the
            # host's empty default, not a nonempty selected destination map.
            config.pop("destinations", None)
        elif "destinations" not in config:
            if not scoped_legacy and not explicit_legacy:
                config["destinations"] = {}
        if not scoped_legacy and not explicit_legacy:
            for key, placeholder in ambient.items():
                if config.get(key) == placeholder:
                    config[key] = ""


class ComponentResolver:
    """Apply the same source rule when a dynamic child lazily adds a module."""
    def __init__(self, resolver, components):
        self.resolver, self.components = resolver, components

    def __getattr__(self, name):
        return getattr(self.resolver, name)

    def resolve(self, module_id, source_hint=None, profile_hint=None):
        hint = profile_hint if profile_hint is not None else source_hint
        selected = self.components.source(module_id, hint, installed=True)
        source = self.resolver.resolve(module_id, selected)
        from .context_source import equivalent_context_source
        return equivalent_context_source(module_id, selected, source)

    async def async_resolve(self, module_id, source_hint=None, profile_hint=None):
        hint = profile_hint if profile_hint is not None else source_hint
        selected = self.components.source(module_id, hint, installed=True)
        source = await self.resolver.async_resolve(module_id, selected)
        from .context_source import equivalent_context_source
        return equivalent_context_source(module_id, selected, source)
