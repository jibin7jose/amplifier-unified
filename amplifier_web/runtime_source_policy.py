"""Keep authored update policy distinct from a qualified install's exact commit.

Only the prepared manifest or an exact owned cache declaration can establish a
new policy. A nearby checkout, current upstream HEAD, or a package name cannot
explain a legacy pin. Such pins remain intact with their missing origin visible.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tomllib
from urllib.parse import parse_qs, urlsplit, urlunsplit

from packaging.requirements import InvalidRequirement, Requirement


def origin(policy, kind, **evidence):
    return {'kind': kind, **{key: policy.get(key, '') for key in
                            ('url', 'ref', 'subdirectory', 'revision')}, **evidence}


def with_origin(policy, kind, **evidence):
    return {**policy, 'policyOrigin': origin(policy, kind, **evidence)}


def carried_origin(policy, previous=None):
    """Rebind evidence only while the chosen repository/ref/subdirectory agree."""
    old = previous or {}
    valid = (isinstance(old, dict) and old.get('kind') and
             all(old.get(key, '') == policy.get(key, '') for key in ('url', 'ref', 'subdirectory')))
    return origin(policy, old['kind'] if valid else 'checked-source',
                  **({key: old[key] for key in ('manifestSha256', 'originalRef', 'resolution') if key in old} if valid else {}))


def _declaration_evidence(content, *, prepared_local_sources=(), runtime_batch=False):
    """Parse source identity once; selection and restrictions are distinct."""
    from .runtime_environment import package_name
    data = tomllib.loads(content.decode())
    found, eligible, selected_names = {}, set(), set()
    restrictions, conditional_names = {}, set()
    uv = data.get('tool', {}).get('uv', {})

    def restrict(name, source):
        restrictions.setdefault(package_name(name), []).append(source)

    def add(name, source, selected, *, conditional=False):
        name = package_name(name)
        restrict(name, None if conditional else source)
        if name in found and found[name] != source:
            found[name] = None  # Includes same-spelling branch/tag conflicts.
        else:
            found[name] = source
        if selected:
            eligible.add(name)

    def requirement(value, selected=False):
        if not isinstance(value, str):
            return
        try:
            parsed_requirement = Requirement(value)
        except InvalidRequirement:
            return
        selected = selected and parsed_requirement.marker is None
        if parsed_requirement.marker is not None or parsed_requirement.extras:
            conditional_names.add(package_name(parsed_requirement.name))
        if selected:
            selected_names.add(package_name(parsed_requirement.name))
        if not parsed_requirement.url or not parsed_requirement.url.startswith('git+'):
            if parsed_requirement.url:
                restrict(parsed_requirement.name, None)
            return
        parsed = urlsplit(parsed_requirement.url[4:])
        path, separator, ref = parsed.path.rpartition('@')
        fragments = parse_qs(parsed.fragment)
        constraint_fragments = parse_qs(parsed.fragment, keep_blank_values=True)
        malformed_constraint = (bool(set(constraint_fragments) - {'subdirectory'})
                                or len(constraint_fragments.get('subdirectory', [''])) != 1)
        if not separator or not ref or len(fragments.get('subdirectory', [''])) != 1:
            restrict(parsed_requirement.name, None)
            return
        add(parsed_requirement.name, {'url': urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, '')),
                       'ref': ref, 'refKind': 'rev',
                       'subdirectory': fragments.get('subdirectory', [''])[0]}, selected,
            conditional=parsed_requirement.marker is not None or bool(parsed_requirement.extras)
                        or malformed_constraint)

    for value in data.get('project', {}).get('dependencies', []):
        requirement(value, selected=True)
    for group in data.get('project', {}).get('optional-dependencies', {}).values():
        for value in group:
            requirement(value)
    # Foundation installs this explicit package batch with uv pip install, not
    # uv sync or --group. Contributor groups cannot select or constrain it,
    # including through their markers/extras. Generic sync policy stays intact.
    if not runtime_batch:
        defaults = uv.get('default-groups', [])
        defaults = set(defaults) if isinstance(defaults, list) and all(isinstance(name, str) for name in defaults) else set()
        for name, group in data.get('dependency-groups', {}).items():
            for value in group:
                requirement(value, selected=name in defaults)
    for collection in ('override-dependencies', 'constraint-dependencies', 'build-constraint-dependencies', 'dev-dependencies'):
        if runtime_batch and collection == 'dev-dependencies':
            continue
        for value in uv.get(collection, []):
            requirement(value)  # Overrides constrain resolution; they do not select a package.
    for name, source in uv.get('sources', {}).items():
        if package_name(name) in prepared_local_sources:
            continue  # Caller proved this local development mapping is the selected Git package.
        # Every alternative can disqualify a ref, even when its selection is
        # unknown. Neither an inactive marker nor an unused extra authorizes it.
        entries = source if isinstance(source, list) else [source]
        if not entries:
            found[package_name(name)] = None
            restrict(name, None)
        for entry in entries:
            refs = ([(key, entry[key]) for key in ('branch', 'rev', 'tag') if entry.get(key)]
                    if isinstance(entry, dict) else [])
            if (not isinstance(entry, dict) or not entry.get('git') or len(refs) != 1
                    or not isinstance(refs[0][1], str)):
                found[package_name(name)] = None
                restrict(name, None)
                continue
            selected = (package_name(name) in selected_names
                        and not any(key in entry for key in ('marker', 'extra', 'group')))
            add(name, {'url': entry['git'], 'ref': refs[0][1], 'refKind': refs[0][0],
                       'subdirectory': entry.get('subdirectory', '')}, selected,
                conditional=bool(set(entry) - {'git', 'branch', 'rev', 'tag', 'subdirectory'}))
    for name in conditional_names & restrictions.keys():
        restrict(name, None)
    if {'dependencies', 'optional-dependencies'} & set(data.get('project', {}).get('dynamic', [])):
        for name in list(restrictions):
            restrict(name, None)  # Build-generated requirements have not been proved here.
    return found, eligible, restrictions


def declarations(content, *, runtime_batch=False):
    """Recover only unconditional selected policy, never optional-source authority."""
    found, eligible, _ = _declaration_evidence(content, runtime_batch=runtime_batch)
    return {name: value for name, value in found.items() if value is not None and name in eligible}


def constraint_declarations(content, *, prepared_local_sources=(), runtime_batch=False):
    """All source restrictions, including unselected optional/dev declarations.

    An optional compatible declaration does not select a package, nor does its
    lack of selection make its identity unknown. Keep every alternative so a
    later matching declaration cannot erase a pin, fork or unknown restriction.
    runtime_batch matches Foundation's explicit pip-install batch, which never
    installs contributor dependency groups; it is not generic sync policy.
    """
    return _declaration_evidence(content, prepared_local_sources=prepared_local_sources,
                                 runtime_batch=runtime_batch)[2]


def owned_cache_path(home, cache_root, installed, subdirectory):
    """Require lexical ownership and reject links below the app data owner."""
    home = Path(home).absolute()
    installed = Path(installed).absolute()
    relative = Path(subdirectory)
    if relative.is_absolute() or '..' in relative.parts or not installed.is_relative_to(home):
        return False
    root = installed
    for _ in relative.parts:
        root = root.parent
    if root.parent != cache_root.absolute():
        return False
    # resolve() alone would bless an external cache reached through a linked
    # foundation/cache (or linked generation), including profile freeze paths.
    current = installed
    while current != home.parent:
        if current.is_symlink():
            return False
        current = current.parent
    return True


def freeze_policies(home, generation, project, graph):
    """Capture policy before an immutable manifest pins every installed byte.

    The caller still overlays checked policies with its strict installed-identity
    comparison. This helper cannot excuse a changed revision during qualification.
    """
    from . import runtime_environment as environments
    manifest = Path(project) / 'pyproject.toml'
    content = manifest.read_bytes() if manifest.is_file() else b''
    # uv.toml takes precedence over [tool.uv]. This helper has no proof that a
    # separate resolver configuration is represented by the prepared manifest.
    # Retain the resolved/installed ref instead of inferring a moving branch.
    separate_config = Path(project) / 'uv.toml'
    declared = {} if separate_config.exists() or separate_config.is_symlink() else declarations(content)
    digest = hashlib.sha256(content).hexdigest()
    locked = environments.locked_sources(project)
    policies = {name: with_origin(policy, 'owned-shared-binding') for name, policy in
                environments.shared_build_policies(home, generation, graph).items()}
    cache_root = environments.receipt_directory(home, generation) / 'foundation/cache'
    for row in graph:
        name = row['name']
        cached = row.get('cacheSource') or {}
        if cached and not cached.get('dirty') and row.get('path') and name not in policies:
            if owned_cache_path(home, cache_root, row['path'], cached['subdirectory']):
                policy = {'url': cached['url'], 'ref': cached['ref'], 'revision': cached['revision'],
                          'subdirectory': '' if cached['subdirectory'] == '.' else cached['subdirectory']}
                policies[name] = with_origin(policy, 'owned-cache-declaration')
        direct = row.get('directUrl') or {}
        vcs = direct.get('vcs_info') or {}
        if vcs.get('vcs') != 'git':
            continue
        requested = vcs.get('requested_revision') or vcs['commit_id']
        policy = {'url': direct['url'], 'ref': vcs['commit_id'],
                  'revision': vcs['commit_id'], 'subdirectory': direct.get('subdirectory', '')}
        # PEP 610's requested_revision has no ref kind. Even a familiar name can
        # be a tag; installed bytes alone never authorize following a branch.
        kind = 'installed-reference' if re.fullmatch('[a-f0-9]{40}', requested) else 'unresolved-reference'
        evidence = {'originalRef': requested} if kind == 'unresolved-reference' else {}
        resolved = locked.get(name)
        declaration = declared.get(name)
        if resolved is not None:
            query = parse_qs(resolved.query, keep_blank_values=True)
            refs = [value for key in ('branch', 'rev', 'tag') for value in query.get(key, [])]
            exact = (urlunsplit((resolved.scheme, resolved.netloc, resolved.path, '', '')) == policy['url']
                     and resolved.fragment == policy['revision']
                     and query.get('subdirectory', ['']) == [policy['subdirectory']] and len(refs) == 1
                     and (requested == refs[0] or
                          (requested == policy['revision'] and re.fullmatch('[a-f0-9]{40}', policy['revision']))))
            if exact:
                kind, evidence = 'resolution-reference', {}
                if query.get('tag'):
                    kind, evidence = 'tag-reference', {'originalRef': refs[0]}
                elif not re.fullmatch('[a-f0-9]{40}', refs[0]):
                    kind, evidence = 'unresolved-reference', {'originalRef': refs[0]}
                # A universal lock can collapse a selected pin and inactive
                # branch to one branch entry. Only selected authored intent
                # (or the caller's exact checked receipt) grants moving policy.
                normalized = (query.get('rev') == [policy['revision']]
                              and re.fullmatch('[a-f0-9]{40}', policy['revision']))
                if (declaration and declaration['url'] == policy['url']
                        and declaration['subdirectory'] == policy['subdirectory']
                        and (refs[0] == declaration['ref'] or normalized)):
                    if declaration['refKind'] == 'tag':
                        policy['ref'] = policy['revision']
                        kind, evidence = 'tag-reference', {'originalRef': declaration['ref']}
                    elif declaration['refKind'] == 'branch' and not query.get('tag'):
                        policy['ref'] = declaration['ref']
                        kind, evidence = 'prepared-manifest', {'manifestSha256': digest}
                    elif declaration['refKind'] == 'rev' and not query.get('branch') and not query.get('tag'):
                        if not re.fullmatch('[a-f0-9]{40}', declaration['ref']):
                            kind, evidence = 'unresolved-reference', {'originalRef': declaration['ref']}
        policies[name] = with_origin(policy, kind, **evidence)
    return policies


async def qualify_cache_policies(policies, checked, runner):
    """Prove newly inferred cache refs without changing any checked policy.

    The installed revision remains the payload authority. A remote branch proves
    update intent only at that same commit; a moved ref is not an install result.
    """
    import asyncio
    from .update_checks import GIT_CHECK_CONCURRENCY, ref_policy
    semaphore = asyncio.Semaphore(GIT_CHECK_CONCURRENCY)
    async def read(url, ref):
        async with semaphore:
            return await ref_policy(url, ref, runner)
    pending = {}
    for name, policy in policies.items():
        if name in checked or (policy.get('policyOrigin') or {}).get('kind') not in {
                'owned-cache-declaration', 'owned-shared-binding'}:
            continue
        if re.fullmatch('[a-f0-9]{40}', policy['ref']):
            continue
        key = (policy['url'], policy['ref'])
        if key not in pending:
            pending[key] = asyncio.create_task(read(*key))
    try:
        outcomes = dict(zip(pending, await asyncio.gather(*pending.values(), return_exceptions=True)))
    finally:
        for task in pending.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending.values(), return_exceptions=True)
    for name, policy in list(policies.items()):
        if name in checked or (policy.get('policyOrigin') or {}).get('kind') not in {
                'owned-cache-declaration', 'owned-shared-binding'}:
            continue
        key = (policy['url'], policy['ref'])
        if key not in outcomes:
            continue
        result = outcomes[key]
        if isinstance(result, BaseException) and not isinstance(result, (OSError, ValueError, RuntimeError, TimeoutError)):
            raise result
        if not isinstance(result, BaseException) and result.get('kind') == 'branch' and result.get('revision') == policy['revision']:
            policies[name] = with_origin(policy, 'verified-cache-branch')
        else:
            reason = ('unavailable' if isinstance(result, BaseException) else
                      'changed-revision' if result.get('kind') == 'branch' else result['kind'])
            policies[name] = with_origin({**policy, 'ref': policy['revision']},
                'tag-reference' if reason == 'tag' else 'unresolved-reference',
                originalRef=policy['ref'], resolution=reason)
    return policies
