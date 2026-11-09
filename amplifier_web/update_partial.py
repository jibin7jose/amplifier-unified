"""Candidate-only retention for settled source acquisition failures.

This is not installer conflict partitioning. Groups are exact repository/ref
identities; one verified active graph backs retained copies and package versions.
The ordinary collector, resolver, freeze and activation still qualify the union.
"""
from __future__ import annotations

import asyncio
import json
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

RECEIPT = 'partial-sources.json'


def key(row):
    from .updates import source_key
    return source_key(row['url'], row['ref'])


def settled_fetch(error):
    from .update_diagnostics import CommandFailure
    facts = getattr(error, 'diagnostic_facts', {})
    return (isinstance(error, CommandFailure) and error.__dict__.get('source_fetch_failed') is True
            and type(facts.get('exitCode')) is int and facts['exitCode'] > 0
            and not facts.get('timedOut'))


def availability(row):
    return (row.get('status') == 'check_failed' and row.get('availabilityFailure') is True
            and row.get('eligible') and row.get('url') and row.get('ref')
            and re.fullmatch('[a-f0-9]{40}', row.get('current', '')))


class Preparation:
    def __init__(self, manager, stage, inventory):
        self.manager, self.stage = manager, Path(stage)
        self.inventory = inventory
        self.groups = {}
        for row in inventory:
            if row.get('url') and row.get('ref') and row.get('kind') != 'smart tool':
                self.groups.setdefault(key(row), []).append(row)
        self.retained = {}
        self.results = {}
        self.baseline = None
        self.policies = {}
        self.graph = {}
        self.pointer = None

    async def verify_baseline(self):
        if self.baseline is not None:
            return
        from .update_health import active_evidence, pointer_witness
        from .updates import active_release
        facts = await asyncio.to_thread(active_evidence, self.manager)
        if not facts.get('verifiedAt') or facts.get('activationUnknown'):
            raise ValueError('Partial acquisition needs a verified working component generation.')
        self.pointer = pointer_witness(self.manager)
        if active_release(self.manager.home).get('current') != facts['generation']:
            raise ValueError('The verified baseline is no longer current.')
        self.baseline = self.manager.directory / 'releases' / facts['generation']
        self.policies = json.loads((self.baseline / 'runtime-sources.json').read_text())
        self.graph = {row['name']: row for row in json.loads((self.baseline / 'runtime-installed.json').read_text())}

    async def recheck(self):
        if self.pointer is not None:
            from .update_health import pointer_witness
            if pointer_witness(self.manager) != self.pointer:
                raise ValueError('The active generation changed during partial preparation.')

    async def inherit_unchecked(self):
        """Carry exact unresolved outcomes across an unrelated tier promotion.

        This is the existing generation receipt, not another retry ledger.
        Re-observe current owned copies before retaining an unchecked source;
        installed presence by itself never authorizes retention.
        """
        from .updates import active_release
        generation = active_release(self.manager.home).get('current')
        if not generation:
            return
        prior = read(self.manager.directory / 'releases' / generation)
        selection = sorted({row['appSelection'] for row in self.inventory if row.get('appSelection')})
        if not prior or prior.get('selection') != selection:
            return
        missing = {key(value['source']): value for value in prior['results'].values()
                   if value['status'] in {'failed', 'dependency-held'} and key(value['source']) not in self.groups}
        if not missing:
            return
        rows = await self.manager.inventory_sources(indexed=True)
        for identity, previous in missing.items():
            copies = [dict(row, latest=previous.get('desired')) for row in rows
                      if row.get('url') and row.get('ref') and key(row) == identity]
            if not copies or any(row.get('current') != previous['revision'] for row in copies):
                raise ValueError('An unchecked retained source no longer matches its qualified identity.')
            self.groups[identity] = copies
            await self.retain(identity, previous['status'])

    async def retain(self, identity, status):
        """Bind all copies and packages, never just remove a failed inventory row."""
        from .updates import pinned, process, cache_changes
        from .staged_sources import owned_path, owned_git_checkout, require_exclusive_registry_owner
        await self.verify_baseline()
        await self.recheck()
        rows = self.groups[identity]
        if any(not r.get('eligible') or pinned(r.get('ref', '')) or r.get('status') in {'local_changes', 'local', 'pinned'} for r in rows):
            raise ValueError('A protected source cannot enter partial acquisition.')
        revisions = {r.get('current') for r in rows}
        if len(revisions) != 1 or not re.fullmatch('[a-f0-9]{40}', next(iter(revisions)) or ''):
            raise ValueError('Source copies disagree on the retained revision.')
        revision = next(iter(revisions))
        policies = {name: p for name, p in self.policies.items() if key(p) == identity}
        if not policies or any(p.get('revision') != revision or name not in self.graph for name, p in policies.items()):
            raise ValueError('A failed source has no exact qualified package baseline.')
        copies = [r for r in rows if r.get('path') and r.get('kind') == 'bundle / module']
        if not copies:
            raise ValueError('This source copy has no supported settled retention boundary.')
        for row in copies:
            if row.get('sharedSource'):
                from amplifier_foundation.sources.shared import SharedSourceStore
                from .update_sources import store_environment
                from .staged_sources import retarget_registry
                environment = store_environment(self.manager.home)
                store = SharedSourceStore(environment['AMPLIFIER_SOURCE_STORE'])
                binding = owned_path(self.stage / 'foundation' / row['path'], self.stage / 'foundation')
                data = json.loads(binding.read_text())
                if (key({'url': data['git_url'], 'ref': data['ref']}) != identity
                        or data['commit'] not in {revision, row.get('latest')}):
                    raise ValueError('The candidate source binding changed during preparation.')
                await asyncio.to_thread(store.verify, row['url'], revision)
                old = store.checkout(row['url'], data['commit'])
                target = await store.bind(binding.parent.parent, row['url'], row['ref'], revision)
                retarget_registry(self.stage / 'foundation/registry.json', row['url'], row['ref'], old, target)
                for policy in policies.values():
                    if not (target / policy.get('subdirectory', '') / 'pyproject.toml').is_file():
                        raise ValueError('The retained bound package source is missing.')
                continue
            target = owned_path(self.stage / 'foundation' / row['path'], self.stage / 'foundation')
            await owned_git_checkout(target, self.stage / 'foundation')
            data = json.loads((target / '.amplifier_cache_meta.json').read_text())
            head = await process('git', 'rev-parse', 'HEAD', cwd=target)
            dirty, artifacts = await cache_changes(target)
            if dirty or artifacts or key({'url': data['git_url'], 'ref': data.get('ref') or 'HEAD'}) != identity:
                raise ValueError('Retained source identity or content changed.')
            # Only this attempt's checked new revision or original revision may
            # exist here. A failed checkout/unknown write never reaches retain.
            if head not in {revision, row.get('latest')} or data.get('commit') != head:
                raise ValueError('Retained source revision drifted.')
            remote = await process('git', 'remote', 'get-url', 'origin', cwd=target)
            if key({'url': remote, 'ref': row['ref']}) != identity:
                raise ValueError('Retained source origin changed.')
            require_exclusive_registry_owner(self.stage / 'foundation/registry.json', row['url'], row['ref'], target)
            if head != revision:
                await process('git', '-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', revision, cwd=target)
                data['commit'] = revision
                (target / '.amplifier_cache_meta.json').write_text(json.dumps(data))
            # Every package path in the selected source must still exist at old.
            for policy in policies.values():
                manifest = owned_path(target / policy.get('subdirectory', '') / 'pyproject.toml', target)
                if not manifest.is_file():
                    raise ValueError('The retained package source is missing.')
        self.retained.update(policies)
        for row in rows:
            self.results[row['id']] = {'status': status, 'revision': revision,
                                      'desired': row.get('latest'),
                                      'source': {'url': row['url'], 'ref': row['ref']}}

    async def hold_dependents(self):
        """Inspect static changed-root requirements once per source group.

        Direct and optional constraints on retained packages select a hold.
        Unknown dynamic dependency metadata refuses before installation, not a
        speculative resolver subset search.
        """
        from .runtime_environment import package_name
        from .selected_runtime_dependencies import git_source
        from .runtime_policy_reconciliation import compatible_constraint
        from .updates import source_key
        selection = await collect(self.manager, self.stage, self.baseline.name)
        requirements = selection['runtimeRequirements']
        # Only roots the canonical Collector actually reached may contribute.
        # An unchanged local root need not authorize source retention; the final
        # union still qualifies it. A changed managed root without provenance
        # cannot be treated as independent.
        for identity, rows in self.groups.items():
            if any(row.get('status') == 'update' for row in rows):
                for name, policy in self.policies.items():
                    if key(policy) == identity and name in selection['batchPaths'] and name not in requirements:
                        raise ValueError('A changed root has no verifiable dependency declaration.')
        changed = True
        while changed:
            changed = False
            for identity, rows in self.groups.items():
                if any(r['id'] in self.results for r in rows) or not any(r.get('status') == 'update' for r in rows):
                    continue
                policies = {n: p for n, p in self.policies.items() if key(p) == identity and n in requirements}
                conflict = False
                for parent in policies:
                    data = requirements[parent]['project']
                    if {'dependencies', 'optional-dependencies'} & set(data.get('dynamic', [])):
                        raise ValueError('Partial preparation cannot infer dynamic dependency independence.')
                    optional = data.get('optional-dependencies', {})
                    if not isinstance(optional, dict) or any(not isinstance(values, list) for values in optional.values()):
                        raise ValueError('Partial preparation needs static optional dependency declarations.')
                    # The generated exact-source override replaces requirements,
                    # including extras activated by another selected root. Keep
                    # all declared optional constraints protective; do not invent
                    # an extras resolver or infer that an unobserved extra is off.
                    values = [*data.get('dependencies', []),
                              *(raw for entries in optional.values() for raw in entries)]
                    for raw in values:
                        requirement = Requirement(raw)
                        name = package_name(requirement.name)
                        retained = self.retained.get(name)
                        if retained is None:
                            continue
                        # Conservative marker/extras handling: do not hide a
                        # possible runtime constraint to make a mixture fit.
                        if requirement.specifier and not requirement.specifier.contains(self.graph[name]['version'], prereleases=True):
                            conflict = True
                        if requirement.url:
                            requested = git_source(requirement.url)
                            if (requested is None or source_key(requested['url'], 'HEAD')[0] != source_key(retained['url'], 'HEAD')[0]
                                    or requested['subdirectory'] != retained.get('subdirectory', '')
                                    or requested['ref'] not in {retained['ref'], retained['revision']}):
                                conflict = True
                    for dependency, constraints in requirements[parent].get('constraints', {}).items():
                        retained = self.retained.get(dependency)
                        if retained and any(not compatible_constraint(item, retained) for item in constraints):
                            conflict = True
                if conflict:
                    await self.retain(identity, 'dependency-held')
                    changed = True
        await self.recheck()

    async def finish(self):
        if not self.retained:
            return
        await self.hold_dependents()
        from .deployment import write_private
        for row in self.inventory:
            if row.get('status') == 'update' and row.get('kind') != 'smart tool' and row['id'] not in self.results:
                kind = 'git' if row.get('url') and not row.get('registryManaged') else 'registry' if row.get('registryManaged') else 'runtime'
                self.results[row['id']] = {'status': 'awaiting-adoption', 'revision': row['latest'], 'revisionKind': kind}
        write_private(self.stage / RECEIPT, json.dumps({'version': 1,
            'candidate': self.stage.name, 'attemptId': self.manager.diagnostics.state['attemptId'],
            'baseline': self.baseline.name, 'pointer': self.pointer,
            'selection': sorted({r['appSelection'] for r in self.inventory if r.get('appSelection')}),
            'retained': self.retained, 'results': self.results}), owned_root=self.manager.home)


def read(stage):
    path = Path(stage) / RECEIPT
    if not path.exists():
        return None
    from .staged_sources import owned_path
    owned_path(path, Path(stage))
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or value.get('version') != 1
            or value.get('candidate') != Path(stage).name
            or not isinstance(value.get('retained'), dict) or not value['retained']
            or not re.fullmatch('[a-f0-9]{32}', value.get('attemptId', ''))
            or not re.fullmatch('[a-f0-9]{32}', value.get('baseline', ''))
            or not isinstance(value.get('results'), dict)):
        raise ValueError('Invalid partial source preparation receipt.')
    for policy in value['retained'].values():
        if (not isinstance(policy, dict) or not re.fullmatch('[a-f0-9]{40}', policy.get('revision', ''))
                or not isinstance(policy.get('subdirectory', ''), str)):
            raise ValueError('Invalid retained source identity.')
        key(policy)
    for result in value['results'].values():
        if not isinstance(result, dict) or result.get('status') not in {'failed', 'dependency-held', 'awaiting-adoption'}:
            raise ValueError('Invalid partial source result.')
        revision = result.get('revision')
        kind = result.get('revisionKind', 'git')
        pattern = {'git': '[a-f0-9]{40}', 'registry': r'[0-9]+(?:\.[0-9A-Za-z]+)*(?:[-+][0-9A-Za-z.]+)?',
                   'runtime': '(?:[a-f0-9]{64}|required)'}.get(kind)
        if not pattern or not isinstance(revision, str) or not re.fullmatch(pattern, revision):
            raise ValueError('Invalid partial result revision.')
        if result['status'] != 'awaiting-adoption':
            key(result['source'])
    return value


async def collect(manager, stage, generation):
    """Reuse collect-only profile preparation in the verified active interpreter.

    No uv sync, DependencyBatch.install or profile mount happens here. The
    candidate gets source observations only; serving sources remain untouched.
    """
    import os
    import uuid
    from .runtime_environment import project_path
    from .host.config import read_config, worker_environment
    from .bundles import profile_candidates
    from .deployment import write_private
    from .update_sources import store_environment, worker_supports_shared
    from .updates import process
    workspace = stage / 'qualification-workspace'
    workspace.mkdir(exist_ok=True)
    config = read_config(workspace, home=stage, shared_home=stage / 'shared-config', global_only=True)
    profiles = profile_candidates(config)
    if not profiles:
        raise ValueError('Partial preparation needs a selected complete profile.')
    profiles_file = stage / 'partial-profiles.json'
    output = stage / 'partial-selected-roots.json'
    write_private(profiles_file, json.dumps(profiles), owned_root=manager.home)
    project = project_path(manager.home, generation)
    python = project / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    shared = store_environment(manager.home) if worker_supports_shared(project) else {}
    env = {**worker_environment(), **shared,
           'AMPLIFIER_WEB_HOME': str(stage), 'AMPLIFIER_HOME': str(stage / 'shared-config'),
           'AMPLIFIER_UNIFIED_RELEASE': '', 'AMPLIFIER_INSTALL_PREPARATION': uuid.uuid4().hex,
           'AMPLIFIER_UPDATE_ATTEMPT': manager.diagnostics.state['attemptId']}
    if not shared:
        env.pop('AMPLIFIER_SOURCE_STORE', None)
    await manager.diagnostics.run('ecosystem-source-policy', process, str(python),
        str(Path(__file__).with_name('update_probe.py')), str(workspace), profiles[0],
        '--profiles', str(profiles_file), '--global-only', '--source-policy-only', str(output),
        env=env, timeout=900)
    return json.loads(output.read_text())


def pin_manifest(content, retained):
    """Exact candidate constraint, separate from the carried moving policy."""
    from .runtime_environment import toml_value, package_name
    data = tomllib.loads(content.decode())
    uv = data.setdefault('tool', {}).setdefault('uv', {})
    sources = uv.setdefault('sources', {})
    overrides = uv.setdefault('override-dependencies', [])
    generated = data['tool'].setdefault('amplifier-unified', {}).setdefault('generated-source-overrides', {})
    for name, policy in retained.items():
        source = sources.get(name)
        if source and (not isinstance(source, dict) or set(source) - {'git', 'rev', 'branch', 'subdirectory'}
                       or len(set(source) & {'rev', 'branch'}) != 1 or source.get('git') != policy['url']
                       or (source.get('rev') or source.get('branch')) not in {policy['ref'], policy['revision']}
                       or source.get('subdirectory', '') != policy.get('subdirectory', '')):
            raise ValueError('Retaining a source cannot override another selected source policy.')
        sources[name] = {'git': policy['url'], 'rev': policy['revision'],
                         **({'subdirectory': policy['subdirectory']} if policy.get('subdirectory') else {})}
        uri = 'git+' + policy['url'] + '@' + policy['revision']
        if policy.get('subdirectory'):
            uri += '#subdirectory=' + policy['subdirectory']
        value = name + ' @ ' + uri
        # The active graph and original moving selection were already verified;
        # narrow only matching generated/default source overrides in this candidate.
        for old in list(overrides):
            req = Requirement(old)
            if package_name(req.name) == name:
                from .selected_runtime_dependencies import git_source
                prior = git_source(req.url)
                if (req.marker or req.extras or prior is None or key(prior) != key(policy)
                        or prior.get('subdirectory', '') != policy.get('subdirectory', '')):
                    raise ValueError('An authored override protects the retained dependency.')
                overrides.remove(old)
        overrides.append(value)
        generated[name] = value
    return ('\n'.join(json.dumps(k) + ' = ' + toml_value(v) for k, v in data.items()) + '\n').encode()


async def stage_sources(manager, stage, candidates):
    """Prepare groups independently; only known settled fetch exits can retain."""
    plan = Preparation(manager, stage, manager.inventory)
    await plan.inherit_unchecked()
    for identity, rows in plan.groups.items():
        if any(availability(row) for row in rows):
            status = 'dependency-held' if all(row.get('sourceUpdate') == 'dependency-held' for row in rows) else 'failed'
            await plan.retain(identity, status)
    # Keep mutations within each repository/ref coupled. Independent groups can
    # succeed even when another download exits; installation still happens once.
    for identity, rows in plan.groups.items():
        if any(row['id'] in plan.results for row in rows):
            continue
        selected = [row for row in rows if row in candidates]
        caches = []
        for row in selected:
            if row.get('kind') == 'included source':
                from .update_sequence import stage_missing
                await manager.diagnostics.run('ecosystem-fetch', stage_missing, manager, stage / 'foundation', row)
            elif row.get('kind') in {'runtime dependency', 'runtime environment'}:
                continue
            elif row.get('sharedSource'):
                from .update_sources import stage_binding
                await manager.diagnostics.run('ecosystem-fetch', stage_binding, manager, stage, row)
            else:
                caches.append(row)
        try:
            for row in caches:
                # No cancelling a sibling fetch into an inferred safe result.
                # A completed earlier copy can be restored exactly as a group.
                await manager.stage_cache_sources(stage, [row])
        except BaseException as error:
            if not settled_fetch(error):
                raise
            await plan.retain(identity, 'failed')
    await plan.finish()
    return plan


def verify_pointer(manager, partial):
    from .update_health import pointer_witness
    if partial and pointer_witness(manager) != partial['pointer']:
        raise ValueError('The active generation changed after partial preparation.')


def public_results(partial, *, adopted=False):
    if not partial:
        return []
    return [{'id': name, 'status': 'updated' if adopted and row['status'] == 'awaiting-adoption' else row['status'],
             'revision': row['revision']} for name, row in partial['results'].items()]


def adopted_items(items, partial):
    """Project only this receipt's outcomes, including grouped source members."""
    def apply(row):
        members = row.get('members')
        if members:
            updated = [apply(item) for item in members]
            statuses = {item.get('status') for item in updated}
            # Same-source grouping should never hide a retained failed member.
            status = 'check_failed' if 'check_failed' in statuses else next(iter(statuses)) if len(statuses) == 1 else row['status']
            return {**row, 'members': updated, 'status': status,
                    'current': updated[0].get('current', row.get('current'))}
        result = partial['results'].get(row.get('id'))
        if not result:
            return row
        if result['status'] == 'awaiting-adoption':
            return {**row, 'status': 'current', 'current': result['revision'], 'sourceUpdate': 'updated'}
        return {**row, 'status': 'check_failed', 'current': result['revision'],
                'sourceUpdate': result['status'],
                'detail': 'The working version was retained because its source download failed.'
                          if result['status'] == 'failed' else 'The working version was retained because a required source could not update.'}
    return [apply(row) for row in items]


def adopted_inventory(rows, partial):
    output = []
    for row in rows:
        result = partial['results'].get(row.get('id'))
        if not result:
            output.append(row)
        elif result['status'] == 'awaiting-adoption':
            output.append({**row, 'status': 'current', 'current': result['revision']})
        else:
            output.append({**row, 'status': 'check_failed', 'current': result['revision'],
                           'availabilityFailure': True, 'sourceUpdate': result['status']})
    return output


def retained_after_check(manager, rows, *, retry_retained):
    """A recheck is not permission to repeat unchanged partial preparation."""
    from .updates import active_release
    generation = active_release(manager.home).get('current')
    if not generation:
        return
    partial = read(manager.directory / 'releases' / generation)
    if not partial or partial.get('selection') != sorted({r['appSelection'] for r in rows if r.get('appSelection')}):
        return
    retained = {key(value['source']): value for value in partial['results'].values()
                if value['status'] in {'failed', 'dependency-held'}}
    for row in rows:
        prior = retained.get(key(row)) if row.get('url') and row.get('ref') else None
        if (not prior or prior['status'] not in {'failed', 'dependency-held'}
                or not row.get('url') or key(row) != key(prior['source'])
                or row.get('current') != prior['revision']):
            continue
        # A deliberate manual Check/Install may try again; automatic checks
        # require a genuinely new desired source revision.
        if not retry_retained and row.get('status') == 'update' and row.get('latest') == prior.get('desired'):
            row.update(status='check_failed', sourceUpdate=prior['status'], availabilityFailure=True,
                       detail='The working version is retained. Check manually to retry this source.')
