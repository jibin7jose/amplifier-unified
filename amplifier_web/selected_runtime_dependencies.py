"""Selected dependency closure for recovering legacy generated Git pins.

Only prepared root manifests seed the graph. Installed metadata supplies the
requirements of reachable dependencies; an unrelated installed extra is never
a root. Conditional Git declarations remain protective, not moving authority.
"""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import importlib.metadata
import json
import re
import tomllib
from urllib.parse import parse_qs, urlsplit, urlunsplit

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement


def git_source(value):
    if not value or not value.startswith('git+'):
        return None
    uri = urlsplit(value[4:])
    path, separator, ref = uri.path.rpartition('@')
    fragments = parse_qs(uri.fragment, keep_blank_values=True)
    if not separator or not ref or set(fragments) - {'subdirectory'} or len(fragments.get('subdirectory', [''])) != 1:
        return None
    return {'url': urlunsplit((uri.scheme, uri.netloc, path, uri.query, '')),
            'ref': ref, 'subdirectory': fragments.get('subdirectory', [''])[0]}


def selected(manifests, *, distributions=None, environment=None):
    from .runtime_environment import package_name
    from .runtime_policy_reconciliation import authored_constraints
    from .updates import source_key, pinned

    environment = default_environment() if environment is None else environment
    installed = {}
    duplicates = set()
    for dist in importlib.metadata.distributions() if distributions is None else distributions:
        name = package_name(dist.metadata['Name'])
        if name in installed:
            duplicates.add(name)
        installed[name] = dist

    roots, constraints, queue = {}, {}, deque()
    for name, content in manifests.items():
        project = tomllib.loads(content.decode()).get('project', {})
        # Static dependency declarations are the source of selection. Dynamic
        # build metadata needs a separately qualified build before it can prove it.
        if 'dependencies' in project.get('dynamic', []):
            continue
        values = project.get('dependencies', [])
        if not isinstance(values, list):
            continue
        roots[name] = values
        queue.append((name, frozenset()))
        for dependency, declaration in authored_constraints(content, runtime_batch=True).items():
            if dependency in constraints and constraints[dependency] != declaration:
                constraints[dependency] = None
            else:
                constraints[dependency] = declaration

    seen, reachable, declarations, evidence = set(), set(), defaultdict(set), defaultdict(set)
    rejected, identities = set(), {}
    while queue:
        name, extras = queue.popleft()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        dist = installed.get(name)
        if name in roots:
            values = roots[name]
        elif name in duplicates or dist is None:
            continue
        else:
            direct = json.loads(dist.read_text('direct_url.json') or '{}')
            # No dependency choices are inferred from an independently selected
            # editable/local checkout or an archive override.
            if direct and direct.get('vcs_info', {}).get('vcs') != 'git':
                continue
            values = dist.requires or []
        for value in values:
            try:
                requirement = Requirement(value)
            except (InvalidRequirement, TypeError):
                continue
            dependency = package_name(requirement.name)
            source = git_source(requirement.url)
            if requirement.url and source is None:
                rejected.add(dependency)
            if source is not None:
                # Even an inactive pin/alternate is a source choice we cannot
                # discard while recovering an old pin's missing origin.
                declarations[dependency].add(json.dumps(source, sort_keys=True))
                if requirement.marker is not None or pinned(source['ref']):
                    rejected.add(dependency)
            try:
                active = requirement.marker is None or any(requirement.marker.evaluate(
                    {**environment, 'extra': extra}) for extra in (extras or {''}))
            except (KeyError, ValueError):
                active = False
            if not active:
                continue
            reachable.add(dependency)
            target = installed.get(dependency)
            if target is None or dependency in duplicates:
                continue
            if requirement.specifier and not requirement.specifier.contains(target.version, prereleases=True):
                rejected.add(dependency)
                continue
            direct = json.loads(target.read_text('direct_url.json') or '{}')
            vcs = direct.get('vcs_info') or {}
            if source is not None:
                try:
                    same_repository = source_key(direct.get('url', ''), 'HEAD')[0] == source_key(source['url'], 'HEAD')[0]
                except ValueError:
                    same_repository = False
                if (vcs.get('vcs') != 'git' or not re.fullmatch('[a-f0-9]{40}', vcs.get('commit_id', ''))
                        or not same_repository
                        or direct.get('subdirectory', '') != source['subdirectory']):
                    rejected.add(dependency)
                    continue
                identities[dependency] = {**source, 'revision': vcs['commit_id']}
                evidence[dependency].add(json.dumps({'parent': name, 'requirement': value,
                    'requirements': values}, sort_keys=True))
            queue.append((dependency, frozenset(requirement.extras)))

    result = {}
    for name in reachable:
        result[name] = {'status': 'selected-dependency-unresolved'}
        source = identities.get(name)
        if source is None or name in rejected or len(declarations[name]) != 1:
            continue
        constraint = constraints.get(name)
        if name in constraints and (not constraint or constraint.get('refKind') == 'tag' or any(
                constraint.get(key, '') != source[key] for key in ('url', 'ref', 'subdirectory'))):
            continue
        result[name] = {'status': 'selected', **source, 'sourceKind': 'dependency',
                        'manifestSha256': hashlib.sha256('\n'.join(sorted(evidence[name])).encode()).hexdigest()}
    return result


async def refresh(manager, stage, project, selected):
    """Advance only proven generated entries in an unpublished candidate."""
    from .deployment import write_private
    from .runtime_environment import package_name, toml_value, locked_sources, verify_resolution
    from .staged_sources import owned_path
    from .updates import process
    import shutil
    from pathlib import Path

    selected = {name: row for name, row in selected.items() if row.get('sourceKind') == 'dependency'}
    if not selected:
        return
    project, stage = Path(project), Path(stage)
    owned_path(project, Path(manager.home) / 'runtime')
    owned_path(project / 'pyproject.toml', Path(manager.home) / 'runtime')
    if (stage / 'runtime.lock').exists() or (stage / 'runtime-installed.json').exists():
        raise ValueError('A recorded generation cannot refresh dependency policy.')
    if (project / 'uv.toml').exists() or (project / 'uv.toml').is_symlink():
        raise ValueError('Separate resolver policy protects this candidate.')
    path = project / 'pyproject.toml'
    data = tomllib.loads(path.read_text())
    sources = data.get('tool', {}).get('uv', {}).get('sources', {})
    changed = set()
    for key, value in sources.items():
        name = package_name(key)
        if name not in selected:
            continue
        row = selected[name]
        if (not isinstance(value, dict) or value.get('git') != row['url']
                or value.get('subdirectory', '') != row['subdirectory']
                or value.get('rev') != row['previousRevision']
                or set(value) - {'git', 'rev', 'subdirectory'}):
            raise ValueError('The generated dependency source changed before reconciliation.')
        value['rev'] = row['revision']
        changed.add(name)
    group = data.get('dependency-groups', {}).get('unified-managed-runtime', [])
    for index, value in enumerate(group):
        requirement = Requirement(value)
        name = package_name(requirement.name)
        if name not in selected:
            continue
        row = selected[name]
        old = git_source(requirement.url)
        if (requirement.marker or requirement.extras or old is None
                or any(old[key] != row[key] for key in ('url', 'subdirectory'))
                or old['ref'] != row['previousRevision'] or name in changed):
            raise ValueError('The generated dependency requirement changed before reconciliation.')
        uri = 'git+' + row['url'] + '@' + row['revision']
        if row['subdirectory']:
            uri += '#subdirectory=' + row['subdirectory']
        group[index] = name + ' @ ' + uri
        changed.add(name)
    if changed != set(selected):
        raise ValueError('A selected dependency lacks an exact generated source entry.')
    generated = data.get('tool', {}).get('amplifier-unified', {}).get('generated-source-overrides', {})
    overrides = data.get('tool', {}).get('uv', {}).get('override-dependencies', [])
    rebound = set()
    for index, value in enumerate(overrides):
        requirement = Requirement(value)
        name = package_name(requirement.name)
        if name not in selected:
            continue
        row = selected[name]
        old = git_source(requirement.url)
        if (generated.get(name) != value or name in rebound or requirement.marker or requirement.extras
                or old is None or any(old[key] != row[key] for key in ('url', 'subdirectory'))
                or old['ref'] != row['previousRevision']):
            raise ValueError('An authored or changed resolver override protects this dependency.')
        uri = 'git+' + row['url'] + '@' + row['revision']
        if row['subdirectory']:
            uri += '#subdirectory=' + row['subdirectory']
        overrides[index] = generated[name] = name + ' @ ' + uri
        rebound.add(name)
    if set(generated).intersection(selected) != rebound:
        raise ValueError('A generated resolver override is missing before reconciliation.')
    write_private(path, '\n'.join(json.dumps(key) + ' = ' + toml_value(value) for key, value in data.items()) + '\n')
    uv = shutil.which('uv')
    await process(uv, 'lock', '--project', str(project), '--python', '3.13',
                  *[arg for name in sorted(selected) for arg in ('--upgrade-package', name, '--refresh-package', name)],
                  timeout=900)
    locked = locked_sources(project)
    for name, row in selected.items():
        # The manifest deliberately requests the exact checked commit. Moving
        # policy stays separate; qualification later verifies the installed bytes.
        verify_resolution(locked.get(name), {'package': name, 'url': row['url'],
            'ref': row['revision'], 'subdirectory': row['subdirectory'], 'latest': row['revision']})
    await process(uv, 'sync', '--locked', '--project', str(project), '--python', '3.13', timeout=900)
