"""Branch-tracking worker dependencies, with a separate lock for each update.

A lock records an installed environment. It is not an update policy: the updater
compares its Git revisions with the branches in the manifest and refreshes it in
an isolated generation. Checks never create or modify an environment.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tomllib
from urllib.parse import parse_qs, urlsplit, urlunsplit

_WINDOWS = os.name == 'nt'


def manifest_path():
    return Path(__file__).with_name('runtime_deps') / 'pyproject.toml'


def declared_manifest(*, extras=None):
    """New worker policy follows installed support, never inference credentials.

    Recorded generations still use their exact manifest. Adding a support pack
    offers the ordinary conversation-component update; it does not alter an
    active worker or claim that its decision backend has been configured.
    """
    content = manifest_path().read_bytes()
    if extras is None:
        from .app_updates import installed_extras
        extras = installed_extras()
    features = tomllib.loads((Path(__file__).with_name('runtime_deps') / 'features.toml').read_text())['features']
    selected = [requirement for feature in sorted(extras) for requirement in features.get(feature, [])]
    if not selected:
        return content
    from packaging.requirements import Requirement
    value = tomllib.loads(content.decode())
    requirements = value.setdefault('project', {}).setdefault('dependencies', [])
    sources = value.setdefault('tool', {}).setdefault('uv', {}).setdefault('sources', {})
    for requirement in selected:
        parsed = Requirement(requirement)
        name = package_name(parsed.name)
        requirements[:] = [old for old in requirements if package_name(Requirement(old).name) != name]
        # Keep dependency selection separate from source policy so the normal
        # update path can preserve an explicitly installed fork or fixed ref.
        source, separator, revision = (parsed.url or '').removeprefix('git+').rpartition('@')
        if not separator or not source.startswith('https://'):
            raise ValueError('Optional worker support requires a declared Git source.')
        sources.setdefault(name, {'git': source, 'rev': revision})
        requirements.append(parsed.name + ('[' + ','.join(sorted(parsed.extras)) + ']' if parsed.extras else ''))
    return ('\n'.join(json.dumps(key) + ' = ' + toml_value(row) for key, row in value.items()) + '\n').encode()


def receipt_directory(home, generation=None):
    root = Path(home) / 'updates'
    return root / 'releases' / generation if generation else root / 'baseline-runtime'


def manifest_content(home, generation=None):
    recorded = receipt_directory(home, generation) / 'runtime.toml'
    if recorded.exists():
        return recorded.read_bytes()
    return declared_manifest()


def project_path(home, generation=None, *, content=None):
    alias = receipt_directory(home, generation) / 'runtime-project.json'
    if alias.exists():
        if content is not None and content != manifest_content(home, generation):
            raise ValueError('Qualified project manifest changed')
        value = json.loads(alias.read_text())
        name = value.get('project', '')
        if not isinstance(name, str) or not re.fullmatch(r'q-[a-f0-9]{32}(?:-[a-f0-9]{8})?', name):
            raise ValueError('Invalid qualified worker project identity')
        target = Path(home) / 'runtime' / name
        if target.is_symlink() or not target.resolve().is_relative_to((Path(home) / 'runtime').resolve()):
            raise ValueError('Qualified worker project is outside its owner')
        return target
    digest = hashlib.sha256(manifest_content(home, generation) if content is None else content)
    if generation:
        digest.update(generation.encode())
    return Path(home) / 'runtime' / digest.hexdigest()[:16]


def prepare_project(home, generation=None, *, content=None):
    content = manifest_content(home, generation) if content is None else content
    project = project_path(home, generation, content=content)
    if _WINDOWS:
        from .windows_files import directory
        # Never ordinary-mkdir the owned tree before private creation. Existing
        # unsafe trees are refused, not repaired or reused after a failed check.
        with directory(project, owned_root=home, create=True) as pinned:
            try:
                existing = pinned.read_private('pyproject.toml', limit=1024 * 1024)
            except FileNotFoundError:
                existing = None
            if existing != content:
                pinned.publish_private('pyproject.toml', content, replace=True)
            receipt = receipt_directory(home, generation) / 'runtime.lock'
            if receipt.exists():
                with directory(receipt.parent, owned_root=home) as recorded:
                    lock_bytes = recorded.read_private(receipt.name, limit=1024 * 1024)
                try:
                    existing_lock = pinned.read_private('uv.lock', limit=1024 * 1024)
                except FileNotFoundError:
                    pinned.publish_private('uv.lock', lock_bytes)
                else:
                    if existing_lock != lock_bytes:
                        raise ValueError('The recorded worker lock changed; its update receipt was preserved.')
        return project
    project.mkdir(parents=True, exist_ok=True)
    target = project / 'pyproject.toml'
    if not target.exists() or target.read_bytes() != content:
        from .deployment import write_private
        write_private(target, content.decode(), owned_root=home)
    receipt = receipt_directory(home, generation) / 'runtime.lock'
    if receipt.exists():
        lock = project / 'uv.lock'
        if lock.exists() and lock.read_bytes() != receipt.read_bytes():
            raise ValueError('The recorded worker lock changed; its update receipt was preserved.')
        if not lock.exists():
            shutil.copy2(receipt, lock)
    return project


def locked_sources(project):
    project = Path(project)
    path = project if project.is_file() else project / 'uv.lock'
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    return {row['name']: urlsplit(row['source']['git']) for row in data.get('package', [])
            if row.get('source', {}).get('git')}


def package_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def installed_sources(project, *, graph=None, tracked=()):
    """Read worker metadata without importing modules or executing its Python.

    The lock cannot describe modules installed later by Foundation. Conversely a
    lock entry must not conceal an installed local override or a different Git
    revision. PEP 610 metadata is the installed source of truth when available.
    """
    from .runtime_qualification import installed_graph
    result = {}
    tracked = set(locked_sources(project)) | set(tracked)
    configured = {package_name(name) for name in
                  tomllib.loads(declared_manifest().decode()).get('tool', {}).get('uv', {}).get('sources', {})}
    for record in installed_graph(project) if graph is None else graph:
        name = record['name']
        direct = record.get('directUrl') or {}
        vcs = direct.get('vcs_info') or {}
        cached = record.get('cacheSource')
        # Bundle packages need not use the amplifier- prefix. Include every
        # installed Git/cache component, while leaving ordinary registry
        # dependencies to the resolver instead of treating them as overrides.
        if not (name.startswith('amplifier-') or name in tracked or name in configured
                or cached or vcs.get('vcs') == 'git'):
            continue
        if cached:
            result[name] = {'url': cached['url'], 'ref': cached['ref'], 'current': cached['revision'],
                            'subdirectory': cached['subdirectory'] if cached['subdirectory'] != '.' else '',
                            'provenance': 'installed Foundation cache module', 'cacheManaged': True,
                            **({'override': True} if cached['dirty'] else {})}
        elif vcs.get('vcs') == 'git':
            result[name] = {'url': direct['url'], 'ref': vcs.get('requested_revision', ''),
                            'current': vcs.get('commit_id', ''),
                            'subdirectory': direct.get('subdirectory', ''), 'provenance': 'installed Git distribution'}
        else:
            result[name] = {'current': record['version'], 'provenance': 'installed local or registry override',
                            'override': True, **({'trackedSource': True} if name in tracked else {})}
    return result


def shared_build_policies(home, generation, graph):
    """A shared build's commit marker is identity, not its update policy.

    Restore only an unambiguous binding in this generation, for an owned clean
    build view at that exact revision. Never infer a branch from a package name,
    the app default, another generation, or a user's ordinary local checkout.
    """
    from .update_sources import store_environment
    environment = store_environment(home)
    if not environment:
        return {}
    builds = Path(environment['AMPLIFIER_SOURCE_STORE']).resolve() / 'builds'
    bindings = {}
    base = receipt_directory(home, generation) / 'foundation/cache/.source-bindings'
    for path in base.glob('*.json'):
        if path.is_symlink():
            raise ValueError('External shared source binding')
        data = json.loads(path.read_text())
        bindings.setdefault((data['git_url'], data['commit']), set()).add(data['ref'])
    result = {}
    for record in graph:
        cached = record.get('cacheSource')
        if not cached or cached.get('dirty') or not record.get('path'):
            continue
        root = Path(record['path']).resolve()
        for _ in Path(cached['subdirectory']).parts:
            root = root.parent
        if root.parent != builds or root.is_symlink():
            continue
        metadata = json.loads((root / '.amplifier_cache_meta.json').read_text())
        if not metadata.get('buildInput'):
            continue
        refs = bindings.get((cached['url'], cached['revision']), set())
        if len(refs) == 1:
            result[record['name']] = {'url': cached['url'], 'ref': next(iter(refs)),
                'revision': cached['revision'],
                'subdirectory': '' if cached['subdirectory'] == '.' else cached['subdirectory']}
    return result


def inventory(home, *, installed=None, graph=None):
    from .updates import active_release, safe_label, pinned
    generation = active_release(home).get('current')
    project = project_path(home, generation)
    recorded = receipt_directory(home, generation) / 'runtime.lock'
    locked = locked_sources(project) if (project / 'uv.lock').exists() or not recorded.exists() else locked_sources(recorded)
    sources = tomllib.loads(declared_manifest().decode()).get('tool', {}).get('uv', {}).get('sources', {})
    sources = {package_name(name): source for name, source in sources.items()}
    policies = receipt_directory(home, generation) / 'runtime-sources.json'
    recorded_policies = json.loads(policies.read_text()) if policies.exists() else {}
    from .runtime_qualification import installed_graph
    graph = installed_graph(project) if graph is None else graph
    # Earlier shared-build receipts did not preserve branch policy. Recover it
    # from their exact private binding without rewriting the recorded graph.
    effective_policies = {**shared_build_policies(home, generation, graph), **recorded_policies}
    observed = {}
    for name, resolved in locked.items():
        name = package_name(name)
        query = parse_qs(resolved.query)
        source = sources.get(name, {})
        policy = recorded_policies.get(name, {})
        if policy.get('url') != urlunsplit((resolved.scheme, resolved.netloc, resolved.path, '', '')):
            policy = {}
        observed[name] = {'url': urlunsplit((resolved.scheme, resolved.netloc, resolved.path, '', '')),
                          'ref': policy.get('ref') or next(iter(query.get('branch') or query.get('rev') or query.get('tag') or ['']), '') or source.get('branch') or source.get('rev') or '',
                          'current': resolved.fragment, 'subdirectory': next(iter(query.get('subdirectory', [''])), ''),
                          'provenance': 'worker resolution lock',
                          **({'policyOrigin': policy['policyOrigin']} if policy.get('policyOrigin') else {})}
    for name, source in (installed_sources(project, graph=graph, tracked=locked) if installed is None else installed).items():
        resolved = locked.get(name)
        policy = effective_policies.get(name)
        if (policy and source.get('current') == (policy.get('revision') or (resolved.fragment if resolved else None))
                and source.get('url') == policy.get('url') and source.get('subdirectory', '') == policy.get('subdirectory', '')):
            source['ref'] = policy['ref']
            if policy.get('policyOrigin'):
                source['policyOrigin'] = policy['policyOrigin']
        observed[name] = source
    rows = []
    base = receipt_directory(home, generation) / 'runtime-base.toml'
    baseline = base.read_bytes() if base.exists() else manifest_content(home, generation)
    current_manifest = declared_manifest()
    if baseline != current_manifest:
        rows.append({'id': 'runtime:environment', 'kind': 'runtime environment',
                     'label': 'Conversation worker environment', 'status': 'update', 'eligible': True,
                     'current': hashlib.sha256(baseline).hexdigest(),
                     'latest': hashlib.sha256(current_manifest).hexdigest(),
                     'usage': 'configured', 'usageEvidence': ['Updated application dependency declarations']})
    from .runtime_registry_updates import managed_source
    core = managed_source(project, receipt_directory(home, generation), baseline, graph, current_manifest)
    if core is not None:
        observed['amplifier-core'] = core
    elif observed.get('amplifier-core', {}).get('override'):
        # An unexplained registry/local Core is not authority to replace a user pin.
        observed['amplifier-core']['trackedSource'] = True
    for name, source in sorted(observed.items()):
        ref = source.get('ref', '')
        override = source.get('override', False)
        registry_migration = core_registry_migration(name, source, baseline)
        eligible = bool(source.get('registryManaged') or not override and not registry_migration and ref and not pinned(ref) and source.get('current'))
        policy_evidence = []
        if ref and pinned(ref) and not source.get('policyOrigin'):
            # Legacy install receipts cannot tell an authored pin from uv's
            # normalized commit. Preserve it and surface the missing provenance.
            from .runtime_source_policy import origin
            source['policyOrigin'] = origin({**source, 'revision': source.get('current', '')}, 'unrecorded')
        if (source.get('policyOrigin') or {}).get('kind') == 'unrecorded':
            policy_evidence.append('Version pin origin is not recorded; source policy needs confirmation')
        rows.append({'id': 'runtime:' + name, 'package': name, 'kind': 'runtime dependency',
                     'label': safe_label(source['url']) if source.get('url') else name,
                     **source, 'ref': ref, 'status': 'not_checked' if eligible or registry_migration else 'local' if override else 'pinned',
                     **({'registryMigration': True} if registry_migration else {}),
                     'eligible': eligible, 'usage': 'configured',
                     'usageEvidence': ['Conversation worker environment', source['provenance'], *policy_evidence]})
    from .runtime_policy_reconciliation import annotate_retained, pending
    annotate_retained(home, generation, rows)
    policy_refresh = pending(home, generation, rows)
    if policy_refresh and not any(row.get('id') == 'runtime:environment' for row in rows):
        rows.insert(0, {'id': 'runtime:environment', 'kind': 'runtime environment',
                       'label': 'Refresh included components', 'status': 'update', 'eligible': True,
                       'current': 'legacy-policy', 'latest': policy_refresh['configuration'],
                       'usage': 'configured', 'usageEvidence': ['Check versions used by your current setup']})
    return rows


def core_registry_migration(name, source, baseline):
    """Replace only the old app-owned Core default in the next generation.

    An installed fork, fixed ref, editable source or dirty cache remains protected.
    The previous base manifest is policy evidence; frozen receipts stay untouched.
    """
    if name != 'amplifier-core':
        return False
    current = tomllib.loads(manifest_path().read_text()).get('tool', {}).get('uv', {})
    previous = tomllib.loads(baseline.decode()).get('tool', {}).get('uv', {}).get('sources', {}).get(name, {})
    upstream = 'https://github.com/microsoft/amplifier-core'
    return (name in current.get('no-build-package', [])
            and name not in current.get('sources', {})
            and previous.get('git') == upstream and (previous.get('rev') or previous.get('branch')) == 'main'
            and not previous.get('subdirectory') and not previous.get('tag')
            and source.get('url') == upstream and source.get('ref') == 'main'
            and not source.get('subdirectory') and not source.get('override') and not source.get('cacheManaged'))


class ProtectedRuntimeSource(ValueError):
    """A fixed reason and package name, never source paths or exception text."""
    def __init__(self, package, reason='protected-runtime-source'):
        super().__init__('A worker dependency source could not be verified; preserve its source configuration before updating.')
        self.diagnostic_facts = {'errorType': 'ValueError', 'reason': reason, 'package': package}


async def update_inventory(home):
    """Legacy update policy is separate from immutable qualification evidence.

    Pre-0.20 workers may have generated-only changes in editable module caches.
    Recognize those using the same read-only classifier as bundle staging. A
    fresh worker resolves the recorded Git source into its own generation; no
    active cache, editable path, fingerprint or old receipt is rewritten.
    """
    from .updates import active_release, cache_changes, foundation_home, process
    from .runtime_qualification import installed_graph
    generation = active_release(home).get('current')
    receipt = receipt_directory(home, generation)
    project = project_path(home, generation)
    graph = await asyncio.to_thread(installed_graph, project)
    policy = installed_sources(project, graph=graph, tracked=locked_sources(receipt / 'runtime.lock'))
    # Frozen generations retain their exact graph rules, including bytecode.
    if not (receipt / 'runtime-installed.json').exists():
        roots = [(foundation_home(home) / 'cache', Path(home).resolve())]
        if generation:
            roots.append((receipt / 'shared-config/cache', Path(home).resolve()))
        else:
            from .session_files import amplifier_home
            roots.append((amplifier_home() / 'cache', amplifier_home().resolve()))
        checked, indexes = {}, {}
        for record in graph:
            cached = record.get('cacheSource')
            if not cached or not cached['dirty'] or record['name'] not in policy:
                continue
            root = Path(record['path'])
            for _ in Path(cached['subdirectory']).parts:
                root = root.parent
            # A metadata file alone must not make an external local override
            # disposable. Only check managed caches, never symlink escapes.
            managed = any(base.resolve().is_relative_to(owner) and root.is_relative_to(base.resolve()) and not base.is_symlink()
                          and not any(path.is_symlink() for path in (root, *root.parents)
                                      if path.is_relative_to(base.resolve())) for base, owner in roots)
            if not managed:
                continue
            if root not in checked:
                indexes[root] = await process('git', 'ls-files', '--stage', '-z', cwd=root, timeout=10, raw=True)
                checked[root] = await cache_changes(root)
            protected, artifacts = checked[root]
            if not protected and artifacts:
                policy[record['name']].pop('override', None)
        # Bind the classification to the exact source identity/content we read.
        # stage() calls this again, so edits after Check now are also protected.
        if checked and await asyncio.to_thread(installed_graph, project) != graph:
            raise ProtectedRuntimeSource('', 'runtime-source-changed')
        for root, index in indexes.items():
            if await process('git', 'ls-files', '--stage', '-z', cwd=root, timeout=10, raw=True) != index:
                raise ProtectedRuntimeSource('', 'runtime-source-changed')
    if active_release(home).get('current') != generation:
        raise ProtectedRuntimeSource('', 'runtime-source-changed')
    return inventory(home, installed=policy, graph=graph)


async def update_manifest(home):
    observed = await update_inventory(home)
    return observed, augmented_manifest(declared_manifest(), observed)


def toml_value(value):
    """Serialize the app-owned generated manifest's basic TOML values."""
    if isinstance(value, dict):
        return '{ ' + ', '.join(json.dumps(key) + ' = ' + toml_value(item) for key, item in value.items()) + ' }'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(item) for item in value) + ']'
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    raise ValueError('Unsupported generated runtime manifest value')


def augmented_manifest(content, rows):
    """Carry actual transitive Git sources into a new generation's resolver.

    Sources remain branches. Resolved revisions live only in the lock/receipt.
    Explicit installed Git overrides retain their source/ref. Configured local
    modules still belong to the bundle loader; never guess an upstream for them.
    """
    from packaging.requirements import Requirement
    from .updates import pinned

    data = tomllib.loads(content.decode())
    sources = data.get('tool', {}).get('uv', {}).get('sources', {})
    declared = {package_name(name): name for name in sources}
    references, changed = [], False
    overrides = list(data.get('tool', {}).get('uv', {}).get('override-dependencies', []))
    generated = {}
    for row in rows:
        if row.get('kind') != 'runtime dependency':
            continue
        name = row['package']
        if row.get('override') and (name in declared or row.get('cacheManaged') or row.get('trackedSource')):
            raise ProtectedRuntimeSource(name)
        if row.get('registryMigration'):
            continue
        if row.get('override') or not row.get('url') or not row.get('ref'):
            continue
        if name in declared:
            source = sources[declared[name]]
            # Preserve a concrete installed override rather than silently
            # replacing it with the app's default repository/branch.
            if row.get('provenance') in {'installed Git distribution', 'installed Foundation cache module'} and (
                source.get('git') != row['url'] or (source.get('branch') or source.get('rev')) != row['ref']
                or source.get('subdirectory', '') != row.get('subdirectory', '')
            ):
                sources[declared[name]] = {'git': row['url'], 'rev': row['ref'],
                                          **({'subdirectory': row['subdirectory']} if row.get('subdirectory') else {})}
                changed = True
            continue
        uri = 'git+' + row['url'] + '@' + row['ref']
        if row.get('subdirectory'):
            uri += '#subdirectory=' + row['subdirectory']
        reference = name + ' @ ' + uri
        references.append(reference)
        if pinned(row['ref']):
            # A direct requirement alone cannot preserve a retained pin when a
            # module declares the same dependency at a moving Git ref. Apply
            # this existing choice transitively without changing authored policy.
            existing = [value for value in overrides if package_name(Requirement(value).name) == name]
            if existing and (len(existing) != 1 or Requirement(existing[0]) != Requirement(reference)):
                raise ProtectedRuntimeSource(name)
            if not existing:
                overrides.append(reference)
                generated[name] = reference
    if references:
        uv = data.setdefault('tool', {}).setdefault('uv', {})
        # This is the app-owned runtime manifest, never a user's project.
        if 'dependency-groups' in data or 'default-groups' in uv:
            raise ValueError('The runtime manifest already manages dependency groups; review its update policy.')
        uv['default-groups'] = ['unified-managed-runtime']
        data['dependency-groups'] = {'unified-managed-runtime': sorted(set(references))}
        changed = True
    if generated:
        owner = data.setdefault('tool', {}).setdefault('amplifier-unified', {})
        if 'generated-source-overrides' in owner:
            raise ValueError('The runtime manifest already owns generated source overrides.')
        # Exact provenance distinguishes these mirrors from an identical authored
        # override when a later, separately proven reconciliation advances a pin.
        owner['generated-source-overrides'] = generated
        data['tool']['uv']['override-dependencies'] = overrides
    if not changed:
        return content
    return ('# Generated worker sources; resolved revisions are recorded separately.\n' +
            '\n'.join(json.dumps(key) + ' = ' + toml_value(value) for key, value in data.items()) + '\n').encode()


def verify_resolution(resolved, row):
    """Reject changed sources with evidence that is safe to copy for diagnosis."""
    expected = {'repository': row['url'], 'ref': row['ref'],
                'subdirectory': row.get('subdirectory', ''), 'revision': row['latest']}
    if resolved is None:
        mismatches = ['source']
    else:
        query = parse_qs(resolved.query, keep_blank_values=True)
        refs = [(key, value) for key in ('branch', 'rev', 'tag') for value in query.get(key, [])]
        ref = refs[0][1] if len(refs) == 1 else None
        # uv can replace a moving rev with its full checked commit. This is
        # identity equivalence only, never permission to follow a different ref
        # or accept a newer commit than the one offered by Check for updates.
        normalized = (refs == [('rev', row['latest'])] and
                      re.fullmatch(r'[0-9a-f]{40}', row['latest']) is not None and
                      resolved.fragment == row['latest'])
        subdirectories = query.get('subdirectory', [''])
        observed = {'repository': urlunsplit((resolved.scheme, resolved.netloc, resolved.path, '', '')),
                    'ref': row['ref'] if normalized else ref,
                    'subdirectory': subdirectories[0] if len(subdirectories) == 1 else None,
                    'revision': resolved.fragment}
        mismatches = [key for key in expected if expected[key] != observed[key]]
    if mismatches:
        error = ValueError('A runtime source resolved differently after checking; check for updates again.')
        # Field names and commits distinguish drift from other resolver
        # differences without exposing repository URLs, refs or local paths.
        error.diagnostic_facts = {
            'reason': 'runtime-resolution-mismatch', 'component': row['package'],
            'resolutionMismatches': mismatches, 'expectedRevision': row['latest'],
            **({'observedRevision': resolved.fragment} if resolved is not None else {}),
        }
        raise error
    policy = {'url': row['url'], 'ref': row['ref'], 'subdirectory': row.get('subdirectory', ''),
              'revision': row['latest']}
    from .runtime_source_policy import carried_origin
    policy['policyOrigin'] = carried_origin(policy, row.get('policyOrigin'))
    return policy


async def stage(manager, generation, candidates, *, finalize=True):
    """Refresh the new environment only; retain the old lock for rollback."""
    from .updates import active_release, process
    previous = active_release(manager.home).get('current')
    current = project_path(manager.home, previous)
    previous_receipt = receipt_directory(manager.home, previous) / 'runtime.lock'
    # The pre-updater environment is also a rollback target (pointer = null).
    if previous is None and not previous_receipt.exists() and (current / 'uv.lock').exists():
        previous_receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(current / 'pyproject.toml', previous_receipt.with_name('runtime.toml'))
        shutil.copy2(current / 'uv.lock', previous_receipt)
    receipt = receipt_directory(manager.home, generation) / 'runtime.lock'
    if receipt.exists():
        observed, content = [], manifest_content(manager.home, generation)
    else:
        observed, content = await manager.diagnostics.run('ecosystem-runtime-preflight', update_manifest, manager.home)
    from .runtime_registry_updates import prepare_manifest, verify_candidate, RECEIPT as registry_receipt
    selected = [] if receipt.exists() else candidates
    content, registry_policy = prepare_manifest(content, observed, selected) if not receipt.exists() else (content, {})
    project = prepare_project(manager.home, generation, content=content)
    baseline = receipt if receipt.exists() else previous_receipt if previous_receipt.exists() else current / 'uv.lock'
    if baseline.exists():
        shutil.copy2(baseline, project / 'uv.lock')
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Install uv to prepare the Amplifier runtime.')
    # Revalidating a rollback or an older staged generation must not upgrade it.
    selected = [] if receipt.exists() else candidates
    baseline_sources = {row['package']: row for row in observed if row.get('kind') == 'runtime dependency'}
    for row in selected:
        if row.get('kind') == 'runtime dependency':
            installed = baseline_sources.get(row['package'])
            if not installed or installed.get('registryMigration') or any(installed.get(key, '') != row.get(key, '') for key in ('current', 'url', 'ref', 'subdirectory', 'registryManaged', 'registry', 'minimumVersion')):
                raise ValueError('Runtime dependencies changed since checking; check for updates again.')
    from .updates import pinned
    declared = tomllib.loads(content.decode()).get('tool', {}).get('uv', {}).get('sources', {})
    packages = sorted({row['package'] for row in observed if row.get('kind') == 'runtime dependency' and row.get('eligible')} |
                      {name for name, source in declared.items() if source.get('git') and not pinned(source.get('branch') or source.get('rev') or '')})
    # A phased update refreshes only the chosen tier. Preserve other resolved
    # sources until their own check; required transitive resolution still belongs
    # to uv. Older callers without tiers retain their existing refresh policy.
    if any(row.get('updateTier') for row in selected):
        packages = sorted({row['package'] for row in selected if row.get('kind') == 'runtime dependency'} |
                          ({name for name, source in declared.items() if source.get('git') and not pinned(source.get('branch') or source.get('rev') or '')}
                           if any(row.get('kind') == 'runtime environment' for row in selected) else set()))
    # A new generation also needs a lock when only bundle/module caches changed.
    flags = ['--locked'] if receipt.exists() else [arg for name in packages for arg in ('--upgrade-package', name, '--refresh-package', name)]
    await manager.diagnostics.run('ecosystem-runtime-lock', process, uv, 'lock', '--project', str(project),
                                  '--python', '3.13', *flags, timeout=900)
    verify_candidate(project, registry_policy)
    if registry_policy and not receipt.exists():
        from .deployment import write_private
        write_private(receipt.with_name(registry_receipt), json.dumps(registry_policy) + '\n', owned_root=manager.home)
    locked = locked_sources(project)
    policies = {}
    for row in selected:
        if row.get('kind') == 'runtime dependency' and not row.get('registryManaged'):
            installed = locked.get(row['package'])
            policies[row['package']] = verify_resolution(installed, row)
    declared = {package_name(name): source for name, source in declared.items()}
    for row in observed:
        if (row.get('kind') != 'runtime dependency' or row['package'] in policies
                or row.get('override') or not row.get('url') or not row.get('ref')):
            continue
        installed = locked.get(row['package'])
        source = declared.get(row['package'])
        if source and (source.get('git') != row['url']
                or (source.get('branch') or source.get('rev') or source.get('tag')) != row['ref']
                or source.get('subdirectory', '') != row.get('subdirectory', '')):
            continue  # A changed manifest's policy is not the old source policy.
        if installed is not None and installed.fragment == row.get('current'):
            try:
                # A different tier can leave a source unchanged while uv
                # normalizes its ref. Retain only that exact prior identity.
                policies[row['package']] = verify_resolution(installed, {**row, 'latest': row['current']})
            except ValueError:
                pass  # Never apply an old policy to a changed or ambiguous source.
    if not receipt.exists():
        # Handoff checked policy separately from uv's representation. Freeze
        # binds it again to installed identity; recorded generations stay intact.
        from .deployment import write_private
        write_private(receipt.with_name('runtime-sources.json'), json.dumps(policies, indent=2) + '\n', owned_root=manager.home)
    if finalize and not receipt.exists():
        receipt.with_name('runtime-base.toml').write_bytes(declared_manifest())
        shutil.copy2(project / 'pyproject.toml', receipt.with_name('runtime.toml'))
        shutil.copy2(project / 'uv.lock', receipt)
    return project
