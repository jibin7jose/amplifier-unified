"""Reconcile legacy generated pins against selected authored sources once.

Runs only while preparing an unpublished generation. It never infers a branch
from an adjacent cache or rewrites a qualified/serving worker.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib

VERSION = 1
SELECTION_VERSION = 4
RECEIPT = 'runtime-policy-reconciliation.json'


def legacy_candidates(rows):
    return {row['package']: {key: row.get(key, '') for key in
                            ('url', 'ref', 'current', 'subdirectory')}
            for row in rows if row.get('kind') == 'runtime dependency'
            and not row.get('override') and row.get('url')
            and re.fullmatch('[a-f0-9]{40}', row.get('ref', ''))
            and row.get('ref') == row.get('current')
            and (row.get('policyOrigin') or {}).get('kind', 'unrecorded') in {'unrecorded', 'unresolved-reference'}}


def configuration(config):
    from .profile_catalog import configuration_key
    from .bundles import profile_candidates
    return hashlib.sha256(json.dumps({
        'sources': configuration_key(config),
        'profiles': profile_candidates(config),
    }, sort_keys=True).encode()).hexdigest()


def pending(home, generation, rows, *, honor_receipt=True):
    """Read-only update inventory. A failed probe never acknowledges the work."""
    candidates = legacy_candidates(rows)
    # Caller-supplied resolver policy is explicit source intent. Do not inspect
    # arbitrary external files or reinterpret it as an installer-generated pin.
    if any(os.environ.get(key) for key in ('UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT')):
        return None
    if not candidates:
        return None
    from .runtime_environment import receipt_directory, project_path
    from .host.config import read_config
    home = Path(home)
    # Separate resolver policy is an explicit override we cannot reconstruct.
    separate = project_path(home, generation) / 'uv.toml'
    if separate.exists() or separate.is_symlink():
        return None
    config = read_config(home, home=home, global_only=True)
    key = configuration(config)
    try:
        saved = json.loads((receipt_directory(home, generation) / RECEIPT).read_text())
        if (honor_receipt and saved.get('version') == VERSION
                and saved.get('selectionVersion') == SELECTION_VERSION and saved.get('configuration') == key
                and all(saved.get('remaining', {}).get(name) == row for name, row in candidates.items())):
            return None
    except (OSError, ValueError, TypeError):
        pass
    return {'version': VERSION, 'configuration': key, 'candidates': candidates}


def annotate_retained(home, generation, rows):
    """A matching completed selection probe narrows usage, never deletes data."""
    from .runtime_environment import receipt_directory
    from .host.config import read_config
    try:
        saved = json.loads((receipt_directory(home, generation) / RECEIPT).read_text())
        if (saved.get('version') != VERSION or saved.get('selectionVersion') != SELECTION_VERSION
                or saved.get('configuration') !=
                configuration(read_config(home, home=home, global_only=True))):
            return
        offered = saved.get('offeredPackages')
        if not isinstance(offered, list):
            return
        candidates = legacy_candidates(rows)
        for row in rows:
            name = row.get('package')
            if (name in candidates and saved.get('remaining', {}).get(name) == candidates[name]
                    and saved.get('decisions', {}).get(name) == 'unselected' and name not in offered):
                row['usage'] = 'unknown'
                row['usageEvidence'] = [
                    'Kept for existing chats; not selected by current offered profiles',
                    *row.get('usageEvidence', []),
                ]
    except (OSError, ValueError, TypeError):
        return


class Collector:
    """Observe effective root/include/module choices during collect-only prepare."""
    def __init__(self):
        self.references = set()
        self.local_sources = set()
        self.local_manifests = set()

    def observe(self, reference, config, registry=None, *, explicit_local=False):
        if not isinstance(reference, str):
            return
        from .update_sequence import git_source
        from .updates import source_key
        seen = set()
        while reference not in seen:
            seen.add(reference)
            source = git_source(reference)
            if source is not None:
                try:
                    self.references.add(source_key(source['url'], source['ref']))
                except ValueError:
                    pass
                return
            # Resolve only a reference actually used by composition, never all
            # available aliases or neighboring cached YAML files.
            registered = registry.find(reference) if registry is not None else None
            if not registered:
                registered = config.registrations.get(reference.split(':', 1)[0])
            if not isinstance(registered, str):
                if explicit_local:
                    # Use the resolver's URI grammar, including file fragments
                    # and workspace-relative paths. A local override remains
                    # local even when it points inside a branch-backed cache.
                    from amplifier_foundation.paths.resolution import parse_uri
                    parsed = parse_uri(reference)
                    if parsed.is_file:
                        path = Path(parsed.path).expanduser()
                        if not path.is_absolute():
                            path = Path(config.workspace) / path
                        if parsed.subpath:
                            path = path / parsed.subpath
                        path = path.resolve()
                        if path.is_file() and path.suffix.lower() in {'.yaml', '.yml', '.md'}:
                            self.local_manifests.add(path)
                        else:
                            self.local_sources.add(path)
                return
            reference = config.resolve_source(registered) or registered

    async def report(self, batch, config, *, allow_generated=False):
        from .runtime_environment import package_name
        from .updates import process, cache_changes, source_key
        from .staged_sources import owned_path, owned_git_checkout
        stage, cache = Path(config.home), Path(config.registry_home) / 'cache'
        store_path = os.environ.get('AMPLIFIER_SOURCE_STORE')
        store = Path(store_path) if store_path else None
        bindings = {}
        for path in cache.glob('.source-bindings/*.json'):
            owned_path(path, stage)
            data = json.loads(path.read_text())
            bindings.setdefault((source_key(data['git_url'], 'HEAD')[0], data['commit']), set()).add(data['ref'])
        inspected, packages, constraints, manifests = {}, {}, {}, {}
        constraint_origins = {}
        for name, source in sorted(batch.names.items()):
            name, source = package_name(name), Path(source)
            package = {'status': 'unresolved'}
            packages[name] = package
            if any(source == path or source.is_relative_to(path) for path in self.local_sources):
                package['status'] = 'local'
                continue
            allowed = stage if source.is_relative_to(stage) else store if store and source.is_relative_to(store / 'objects') else None
            if allowed is None:
                package['status'] = 'local'
                continue
            owned_path(source, allowed)
            root = source
            while root != allowed and not (root / '.git').exists():
                root = root.parent
            if root == allowed:
                continue
            # A local bundle file inside this source repository protects that
            # source choice. Do not widen it to the file's arbitrary parent,
            # which could also contain unrelated downloaded source caches.
            if any(path.is_relative_to(root) for path in self.local_manifests):
                package['status'] = 'local'
                continue
            if root not in inspected:
                await owned_git_checkout(root, allowed)
                meta = root / '.amplifier_cache_meta.json'
                owned_path(meta, allowed)
                data = json.loads(meta.read_text())
                head = await process('git', 'rev-parse', 'HEAD', cwd=root, timeout=10)
                dirty, artifacts = await cache_changes(root)
                if dirty or (artifacts and not allow_generated) or head != data.get('commit'):
                    raise ValueError('A selected source changed during policy reconciliation.')
                remote = await process('git', 'remote', 'get-url', 'origin', cwd=root, timeout=10)
                if source_key(remote, 'HEAD')[0] != source_key(data['git_url'], 'HEAD')[0]:
                    raise ValueError('A selected source has a different repository identity.')
                inspected[root] = data
            data = inspected[root]
            manifest = source / 'pyproject.toml'
            owned_path(manifest, allowed)
            content = manifest.read_bytes()
            if package_name(tomllib.loads(content.decode()).get('project', {}).get('name', '')) != name:
                raise ValueError('A selected source changed its package identity.')
            sibling_dev = selected_sibling_dev_sources(content, source, root, batch.names)
            sibling_runtime = selected_runtime_sibling_sources(content, source, root, data, batch.names)
            from .runtime_source_policy import constraint_declarations
            for dependency, declarations in constraint_declarations(
                    content, prepared_local_sources=sibling_runtime, runtime_batch=True).items():
                if dependency in sibling_dev:
                    continue  # --no-sources does not activate this selected sibling's dev edge.
                constraints.setdefault(dependency, []).extend(declarations)
                constraint_origins.setdefault(dependency, []).append({
                    'package': name, 'url': data['git_url'], 'revision': data['commit'],
                    'subdirectory': '' if source == root else source.relative_to(root).as_posix(),
                    'manifestSha256': hashlib.sha256(content).hexdigest(),
                    'declarations': declarations,
                })
            if any((path / 'uv.toml').exists() or (path / 'uv.toml').is_symlink() for path in (root, source)):
                package['status'] = 'separate-resolver-config'
                continue
            url = source_key(data['git_url'], 'HEAD')[0]
            refs = {ref for candidate, ref in self.references if candidate == url}
            if len(refs) != 1:
                package['status'] = 'conflicting' if refs else 'unselected'
                continue
            ref = next(iter(refs))
            from .updates import pinned
            unbound_status = 'explicit-pin' if pinned(ref) else 'unbound'
            if allowed == store:
                if ref not in bindings.get((url, data['commit']), set()):
                    package['status'] = unbound_status
                    continue
            elif source_key(data['git_url'], data.get('ref') or 'HEAD')[1] != ref:
                package['status'] = unbound_status
                continue
            package.update(status='selected', url=data['git_url'], ref=ref,
                           subdirectory=('' if source == root else source.relative_to(root).as_posix()),
                           revision=data['commit'], manifestSha256=hashlib.sha256(content).hexdigest())
            if pinned(ref):
                # Identity can prove an unchanged install without granting
                # moving-ref recovery or permission to rewrite this checkout.
                package['status'] = 'explicit-pin'
            # A verified pinned root still constrains its reachable dependencies.
            # Its own explicit-pin status remains ineligible for moving recovery.
            manifests[name] = content
        for name, package in packages.items():
            if package.get('status') not in {'selected', 'explicit-pin'} or name not in constraints:
                continue
            # Check each authored choice against the independently selected
            # identity. rev=main and branch=main can each agree with it without
            # declaring every rev a branch or discarding a conflicting tag.
            if any(not compatible_constraint(constraint, package) for constraint in constraints[name]):
                package.clear()
                package['status'] = 'authored-constraint'
            elif any(exact_constraint_pin(constraint) for constraint in constraints[name]):
                # An independently selected branch can satisfy an authored pin
                # at this exact commit, but that is never moving-ref authority.
                # The existing explicit-pin gate requires the checked revision
                # to match even before alignment; migration/root rewrites remain
                # protected. Do not replace the selected ref or remove the pin.
                package['status'] = 'explicit-pin'
            package['authoredConstraints'] = constraint_origins[name]
        from .selected_runtime_dependencies import selected
        dependencies = selected({name: content for name, content in manifests.items()
                                 if packages[name].get('status') in {'selected', 'explicit-pin'}})
        for name, package in dependencies.items():
            packages.setdefault(name, package)
        return {'version': VERSION, 'configuration': configuration(config), 'packages': packages,
                # Private preparation evidence, never copied into update diagnostics.
                'batchPaths': {name: str(path) for name, path in batch.names.items()},
                'protectedPaths': sorted(str(path) for path in self.local_sources | self.local_manifests),
                'observedReferences': [list(row) for row in sorted(self.references)],
                'localSources': sorted(map(str, self.local_sources)),
                'localManifests': sorted(map(str, self.local_manifests))}


def exact_constraint_pin(constraint):
    return bool(constraint and constraint.get('refKind') == 'rev'
                and isinstance(constraint.get('ref'), str)
                and re.fullmatch('[a-f0-9]{40}', constraint['ref']))


def compatible_constraint(constraint, package):
    """Restrictions cannot select a package or turn a pin into branch policy."""
    if (not constraint or constraint.get('refKind') not in {'branch', 'rev'}
            or any(constraint.get(field, '') != package.get(field)
                   for field in ('url', 'subdirectory'))):
        return False
    if exact_constraint_pin(constraint):
        return constraint['ref'] == package.get('revision')
    return constraint.get('ref') == package.get('ref')


def selected_sibling_dev_sources(content, source, root, selected):
    """Ignore only a proven selected sibling's local, dev-only uv source.

    A workspace self/member reference also needs exact membership, package and
    extra names. Optional, conditional, Git, external, unselected and resolver
    constraints remain protective. This does not select a package or authorize
    a new ref. Foundation installs these exact paths with --no-sources.
    """
    from packaging.requirements import Requirement, InvalidRequirement
    from .runtime_environment import package_name
    data = tomllib.loads(content.decode())
    project, uv = data.get('project', {}), data.get('tool', {}).get('uv', {})
    if ('dependencies' in project.get('dynamic', []) or 'optional-dependencies' in project.get('dynamic', [])
            or not isinstance(project.get('dependencies', []), list)):
        return set()
    groups = data.get('dependency-groups', {})
    dev = groups.get('dev', [])
    protected = list(project.get('dependencies', []))
    for values in project.get('optional-dependencies', {}).values():
        protected.extend(values)
    for group, values in groups.items():
        if group != 'dev':
            protected.extend(values)
    for key in ('override-dependencies', 'constraint-dependencies', 'build-constraint-dependencies', 'dev-dependencies'):
        protected.extend(uv.get(key, []))
    def requirement(value):
        try:
            return Requirement(value) if isinstance(value, str) else None
        except InvalidRequirement:
            return None
    parsed = [requirement(value) for value in protected]
    if any(value is None for value in parsed):
        return set()
    protected_names = {package_name(value.name) for value in parsed}
    result = set()
    sources = uv.get('sources', {})
    aliases = [package_name(name) for name in sources]
    for name, entry in sources.items():
        name = package_name(name)
        if (aliases.count(name) != 1 or name not in selected
                or name in protected_names or not isinstance(entry, dict)):
            continue
        workspace = entry == {'workspace': True}
        if workspace:
            # Only exact, explicitly listed members of this repository's root
            # workspace. Globs/exclusions and external or nested workspaces are
            # not inferred from an installed path.
            members = uv.get('workspace', {}).get('members', [])
            if (source != root or uv.get('workspace', {}).get('exclude')
                    or not isinstance(members, list) or not members
                    or any(not isinstance(member, str) or Path(member).is_absolute()
                           or '..' in Path(member).parts or any(c in member for c in '*?[')
                           for member in members)):
                continue
            target = Path(selected[name]).resolve()
            if target not in {(root / member).resolve() for member in members}:
                continue
        else:
            if (set(entry) != {'path', 'editable'} or entry['editable'] is not True
                    or not isinstance(entry['path'], str) or Path(entry['path']).is_absolute()):
                continue
            target = (source / entry['path']).resolve()
        target_root = next((path for path in (target, *target.parents) if (path / '.git').exists()), None)
        if (target != Path(selected[name]).resolve() or not target.is_relative_to(root)
                or target_root != root or (target == source and not workspace)):
            continue
        target_manifest = target / 'pyproject.toml'
        if target_manifest.is_symlink() or not target_manifest.is_file():
            continue
        target_project = tomllib.loads(target_manifest.read_text()).get('project', {})
        if package_name(target_project.get('name', '')) != name or target_project.get('dynamic'):
            continue
        declarations = [requirement(value) for value in dev]
        matching = [value for value in declarations if value and package_name(value.name) == name]
        if (not matching or any(value is None for value in declarations)
                or any(value.url or value.marker or value.specifier
                       or (value.extras and (not workspace or
                           not value.extras <= set(target_project.get('optional-dependencies', {}))))
                       for value in matching)):
            continue
        result.add(name)
    return result


def selected_runtime_sibling_sources(content, source, root, metadata, selected):
    """Prove a local uv development mapping denotes the selected Git sibling.

    Runtime and optional direct Git requirements remain restrictions. Only the
    redundant local tool.uv.sources entry is excluded from the --no-sources
    batch policy. Optional requirements never select a package. An ordinary
    local source, fork, pin or conditional mapping cannot qualify.
    """
    from packaging.requirements import Requirement, InvalidRequirement
    from .runtime_environment import package_name
    from .selected_runtime_dependencies import git_source
    from .updates import pinned, source_key
    data = tomllib.loads(content.decode())
    project = data.get('project', {})
    if project.get('dynamic'):
        return set()
    requirements = {}
    values = list(project.get('dependencies', []))
    for group in project.get('optional-dependencies', {}).values():
        values.extend(group)
    for value in values:
        try:
            requirement = Requirement(value)
        except (InvalidRequirement, TypeError):
            return set()
        name = package_name(requirement.name)
        requirements.setdefault(name, []).append(requirement)
    result = set()
    sources = data.get('tool', {}).get('uv', {}).get('sources', {})
    aliases = [package_name(name) for name in sources]
    for name, entry in sources.items():
        name = package_name(name)
        if (aliases.count(name) != 1 or name not in selected or name not in requirements or not isinstance(entry, dict)
                or set(entry) != {'path', 'editable'} or entry['editable'] is not True
                or not isinstance(entry['path'], str) or Path(entry['path']).is_absolute()):
            continue
        target = (source / entry['path']).resolve()
        target_root = next((path for path in (target, *target.parents) if (path / '.git').exists()), None)
        if (target != Path(selected[name]).resolve() or target_root != root
                or not target.is_relative_to(root)):
            continue
        target_manifest = target / 'pyproject.toml'
        if target_manifest.is_symlink() or not target_manifest.is_file():
            continue
        target_project = tomllib.loads(target_manifest.read_text()).get('project', {})
        if package_name(target_project.get('name', '')) != name or target_project.get('dynamic'):
            continue
        subdir = '' if target == root else target.relative_to(root).as_posix()
        valid = True
        for requirement in requirements[name]:
            declared = git_source(requirement.url)
            if (not declared or requirement.marker or requirement.extras or pinned(declared['ref'])
                    or source_key(declared['url'], declared['ref']) !=
                       source_key(metadata['git_url'], metadata.get('ref') or 'HEAD')
                    or declared['subdirectory'] != subdir):
                valid = False
        if valid:
            result.add(name)
    return result


def authored_constraints(content, *, runtime_batch=False):
    """Selected source manifests may pin another package despite a module URI.

    Unknown/conditional source declarations only restrict recovery here. They
    never authorize it; the effective bundle/module choices remain required.
    """
    from packaging.requirements import Requirement, InvalidRequirement
    from .runtime_environment import package_name
    from .runtime_source_policy import declarations
    data = tomllib.loads(content.decode())
    project, uv = data.get('project', {}), data.get('tool', {}).get('uv', {})
    values = list(project.get('dependencies', []))
    groups_to_read = [project.get('optional-dependencies', {})]
    if not runtime_batch:
        groups_to_read.append(data.get('dependency-groups', {}))
    for groups in groups_to_read:
        for group in groups.values():
            values.extend(group)
    for key in ('override-dependencies', 'constraint-dependencies', 'build-constraint-dependencies', 'dev-dependencies'):
        if runtime_batch and key == 'dev-dependencies':
            continue
        values.extend(uv.get(key, []))
    names = {package_name(name) for name in uv.get('sources', {})}
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            requirement = Requirement(value)
        except InvalidRequirement:
            continue
        if requirement.url and requirement.url.startswith('git+'):
            names.add(package_name(requirement.name))
    declared = declarations(content, runtime_batch=runtime_batch)
    return {name: declared.get(name) for name in names}


async def moving_revision(url, ref, process):
    """Use the shared, access-scoped reference-kind proof."""
    from .update_checks import moving_revision as checked_revision
    return await checked_revision(url, ref, process)


def selected_policies(candidates, report):
    """A selected source is evidence only for its exact distribution identity."""
    from .updates import source_key
    result, decisions = {}, {}
    for name, old in candidates.items():
        current = report.get('packages', {}).get(name, {})
        status = current.get('status', 'unselected')
        if status == 'selected':
            if (source_key(old['url'], 'HEAD')[0] != source_key(current['url'], 'HEAD')[0]
                    or old.get('subdirectory', '') != current.get('subdirectory', '')):
                status = 'different-source'
            else:
                result[name] = {key: current[key] for key in ('url', 'ref', 'subdirectory', 'revision', 'manifestSha256')}
                if current.get('sourceKind') == 'dependency':
                    if old['current'] != current['revision']:
                        decisions[name] = 'different-installed-revision'
                        result.pop(name)
                        continue
                    result[name].update(sourceKind='dependency', previousRevision=old['current'])
                status = 'selected'
        decisions[name] = status
    return result, decisions


async def reconcile(manager, stage, project, collect):
    """Collect → check exact refs → align copied roots → recollect → qualify."""
    from .deployment import write_private
    from .runtime_environment import receipt_directory, update_inventory
    from .runtime_source_policy import with_origin
    from .runtime_source_alignment import align_checked_sources
    from .updates import active_release, process
    from .staged_sources import owned_path
    home, stage, project = Path(manager.home), Path(stage), Path(project)
    owned_path(stage, home / 'updates/releases')
    if (stage / 'runtime.lock').exists():
        raise ValueError('A recorded generation cannot reconcile update policy.')
    old_generation = active_release(home).get('current')
    rows = await update_inventory(home)
    request = pending(home, old_generation, rows, honor_receipt=False)
    old_receipt = receipt_directory(home, old_generation) / RECEIPT
    if request is None:
        # Carry the acknowledgment forward when another component prompted an
        # update. Otherwise an unresolved extra would prompt again next time.
        if old_receipt.exists():
            write_private(stage / RECEIPT, old_receipt.read_text(), owned_root=home)
        return
    separate = project / 'uv.toml'
    if separate.exists() or separate.is_symlink():
        return
    first = await collect()
    if first.get('version') != VERSION or first.get('configuration') != request['configuration']:
        raise ValueError('Selected source settings changed during policy reconciliation.')
    selected, decisions = selected_policies(request['candidates'], first)
    checked_path = stage / 'runtime-sources.json'
    checked = json.loads(checked_path.read_text())
    latest = {}
    for name, source in list(selected.items()):
        key = (source['url'], source['ref'])
        if key not in latest:
            latest[key] = await moving_revision(*key, process)
        revision = latest[key]
        if revision is None:
            decisions[name] = 'fixed-or-ambiguous-ref'
            selected.pop(name)
            continue
        if not re.fullmatch('[a-f0-9]{40}', revision):
            raise ValueError('A selected source has no exact checked revision.')
        selected[name] = {**source, 'revision': revision}
        checked[name] = with_origin({key: selected[name][key] for key in
                                   ('url', 'ref', 'subdirectory', 'revision')},
                                   'selected-declaration', manifestSha256=source['manifestSha256'])
    if selected:
        write_private(checked_path, json.dumps(checked, indent=2) + '\n', owned_root=home)
        await align_checked_sources(manager, stage, selection=first)
        from .selected_runtime_dependencies import refresh
        await refresh(manager, stage, project, selected)
        second = await collect()
        verified, _ = selected_policies({name: {**old, 'current': selected[name]['revision']}
                                        if name in selected else old
                                        for name, old in request['candidates'].items()}, second)
        if second.get('configuration') != first['configuration'] or any(
                name not in verified or any(verified[name][field] != source[field]
                    for field in ('url', 'ref', 'subdirectory', 'revision')) for name, source in selected.items()):
            raise ValueError('The selected source changed after its checked refresh.')
        # New source manifests may differ from the old cached commit. Record
        # the current authored input; the existing freeze checks installed bytes.
        for name, source in selected.items():
            checked[name]['policyOrigin']['manifestSha256'] = verified[name]['manifestSha256']
        write_private(checked_path, json.dumps(checked, indent=2) + '\n', owned_root=home)
    if active_release(home).get('current') != old_generation:
        raise ValueError('The active generation changed during policy reconciliation.')
    result = {'version': VERSION, 'selectionVersion': SELECTION_VERSION, 'configuration': request['configuration'],
              'decisions': {name: ('migrated' if name in selected else status) for name, status in decisions.items()},
              'remaining': {name: old for name, old in request['candidates'].items() if name not in selected},
              'offeredPackages': sorted(first.get('packages', {})),
              'migrated': {name: checked[name] for name in selected}}
    write_private(stage / RECEIPT, json.dumps(result, indent=2) + '\n', owned_root=home)
    # Only this invocation may reuse its collection. A migration changes checked
    # policy, bindings and/or resolver input and always needs fresh composition.
    return {'selection': first, 'generation': old_generation} if not selected else None


def policy_inputs(stage, project):
    """Small explicit control inputs; no persistent cache or arbitrary settings."""
    paths = [Path(stage) / name for name in (
        'runtime-sources.json', 'runtime-install-overrides.txt', 'profiles.json')]
    paths += [Path(project) / name for name in ('pyproject.toml', 'uv.lock', 'uv.toml')]
    # Include declarations/bindings consumed by composition, not credential files
    # or every cached payload. Local/custom inputs cannot use this shortcut.
    for folder in ('config', 'shared-config', 'foundation/registry.json', 'foundation/cache/.source-bindings'):
        path = Path(stage) / folder
        paths.extend([path] if path.is_file() else sorted(
            p for p in path.rglob('*') if p.is_file() and p.suffix in {'.json', '.yaml', '.yml', '.toml'}))
    return ([(str(path), hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
             for path in paths] +
            [(key, hashlib.sha256(os.environ.get(key, '').encode()).hexdigest()) for key in
             ('UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT', 'AMPLIFIER_SOURCE_STORE')])


async def reuse_collection(manager, stage, project, observed, inputs):
    """Re-observe identities without launching a second composition process.

    Changed controls/config/source/bindings return no reuse. The normal fresh
    collection, alignment, exact guard, installer and profile probes still run.
    """
    from types import SimpleNamespace
    from .host.config import read_config
    from .updates import active_release
    selection = (observed or {}).get('selection')
    if not selection or 'observedReferences' not in selection:
        return None
    if selection.get('localSources') or selection.get('localManifests'):
        return None
    generation = active_release(manager.home).get('current')
    if generation != observed['generation'] or policy_inputs(stage, project) != inputs:
        return None
    config = read_config(Path(stage) / 'qualification-workspace', home=stage,
                         shared_home=Path(stage) / 'shared-config', global_only=True)
    if configuration(config) != selection.get('configuration'):
        return None
    collector = Collector()
    collector.references = {tuple(row) for row in selection['observedReferences']}
    collector.local_sources = {Path(path) for path in selection['localSources']}
    collector.local_manifests = {Path(path) for path in selection['localManifests']}
    try:
        current = await collector.report(SimpleNamespace(names=selection['batchPaths']), config)
    except (OSError, ValueError, KeyError):
        return None
    if (current != selection or policy_inputs(stage, project) != inputs
            or active_release(manager.home).get('current') != generation):
        return None
    return current
