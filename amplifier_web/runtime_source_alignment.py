"""Keep unpublished bundle copies consistent with the checked worker graph.

Bundle preparation installs its root packages explicitly, so its local roots
take precedence over uv dependency overrides. Align matching managed sources
before composition; never repair an installed worker after qualification fails.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path


from .staged_sources import owned_path as _owned, owned_git_checkout, retarget_registry, require_exclusive_registry_owner


def verify_checked_selection(checked, selection, *, exact=False):
    """Check explicit batch choices, never infer selection from installed extras."""
    from .runtime_environment import ProtectedRuntimeSource
    from .updates import source_key
    packages = selection['packages']
    for name in selection['batchPaths']:
        policy = checked.get(name)
        if policy is None:
            continue  # Ordinary dependencies remain governed by overrides and freeze.
        source = packages.get(name, {})
        if source.get('status') == 'authored-constraint':
            raise ProtectedRuntimeSource(name, 'runtime-source-constraint')
        if (source.get('status') not in {'selected', 'explicit-pin'}
                or any(not source.get(field) for field in ('url', 'ref', 'revision'))
                or source_key(source['url'], source['ref']) != source_key(policy['url'], policy['ref'])
                or source.get('subdirectory', '') != policy.get('subdirectory', '')):
            reason = {'unbound': 'runtime-source-unbound',
                      'conflicting': 'runtime-source-conflict'}.get(
                          source.get('status'), 'protected-runtime-source')
            raise ProtectedRuntimeSource(name, reason)
        if (exact or source['status'] == 'explicit-pin') and source['revision'] != policy['revision']:
            raise ValueError('A selected module differs from its checked revision before installation.')


async def checked_batch_sources(batch, collector, config, checked):
    """Read the actual batch just collected; never install or rewrite its paths."""
    selection = await collector.report(batch, config)
    verify_checked_selection(checked, selection)
    return selection


def protected_selection(root, selection):
    if selection is None:
        return False
    root = Path(root)
    protected = [Path(path) for path in selection.get('protectedPaths', [])]
    protected += [Path(path) for name, path in selection['batchPaths'].items()
                  if selection['packages'].get(name, {}).get('status') != 'selected']
    return any(path == root or path.is_relative_to(root) or root.is_relative_to(path)
               for path in protected)


async def align_checked_sources(manager, stage, *, selection=None):
    from .updates import cache_changes, pinned, process, safe_label, source_key

    stage = Path(stage)
    expected = Path(manager.home) / 'updates/releases'
    _owned(stage, expected)
    if (stage / 'runtime.lock').exists():
        raise ValueError('A recorded worker generation cannot be refreshed.')
    checked_path = stage / 'runtime-sources.json'
    if not checked_path.exists():
        return
    checked = json.loads(checked_path.read_text())
    if selection is not None:
        verify_checked_selection(checked, selection)
    def protect_selected_root(root):
        if selection is None:
            return
        # A local/pinned sibling can share the mutable checkout. Preflight all
        # roots before any mutation, even when that sibling is not in checked.
        if protected_selection(root, selection):
            raise ValueError('A selected local or pinned source shares the candidate checkout.')
    policies = {}
    for policy in checked.values():
        key = source_key(policy['url'], policy['ref'])
        revision = policy['revision']
        if not re.fullmatch(r'[0-9a-f]{40}', revision):
            raise ValueError('A checked source requires an exact commit.')
        if key in policies and policies[key]['revision'] != revision:
            raise ValueError('Checked packages disagree on one repository revision.')
        policies[key] = policy

    base = stage / 'foundation'
    cache = base / 'cache'
    rows, bindings = [], []
    # Preflight all matching copies, including nested skill caches. A local
    # edit or unverifiable copy cannot be silently overwritten by this repair.
    for meta in sorted(cache.rglob('.amplifier_cache_meta.json')):
        _owned(meta, base)
        data = json.loads(meta.read_text())
        try:
            key = source_key(data['git_url'], data.get('ref') or 'HEAD')
        except (KeyError, TypeError, ValueError):
            continue  # An unrelated/local source is not a checked Git identity.
        policy = policies.get(key)
        if policy is None or pinned(data.get('ref') or 'HEAD'):
            continue
        root = meta.parent
        await owned_git_checkout(root, base)
        current = await process('git', 'rev-parse', 'HEAD', cwd=root, timeout=10)
        dirty, artifacts = await cache_changes(root)
        if dirty or current != data.get('commit'):
            raise ValueError('A staged managed source changed before preparation.')
        if current != policy['revision'] or artifacts:
            protect_selected_root(root)
            require_exclusive_registry_owner(base / 'registry.json', data['git_url'], data.get('ref') or 'HEAD', root)
            rows.append({
                'path': str(root.relative_to(base)), 'url': data['git_url'],
                'ref': data.get('ref') or 'HEAD', 'current': current,
                'latest': policy['revision'], 'label': safe_label(data['git_url']),
            })

    for path in sorted(cache.rglob('.source-bindings/*.json')):
        _owned(path, base)
        data = json.loads(path.read_text())
        policy = policies.get(source_key(data['git_url'], data['ref']))
        if policy is None or pinned(data['ref']) or data['commit'] == policy['revision']:
            continue
        bindings.append((path, data, policy))

    # Existing staging checks re-observe identity/edits immediately before
    # writes, restore only proven generated artifacts, and fetch exact commits.
    await manager.stage_cache_sources(stage, rows)
    if not bindings:
        return
    from .update_sources import store_environment
    from amplifier_foundation.sources.shared import SharedSourceStore
    environment = store_environment(manager.home)
    if not environment:
        raise ValueError('The checked source needs shared storage support.')
    store = SharedSourceStore(environment['AMPLIFIER_SOURCE_STORE'])
    for path, data, policy in bindings:
        _owned(path, base)
        if json.loads(path.read_text()) != data:
            raise ValueError('A shared source changed before preparation.')
        await asyncio.to_thread(store.verify, data['git_url'], data['commit'])
        old = store.checkout(data['git_url'], data['commit'])
        target = await store.bind(path.parent.parent, data['git_url'], data['ref'], policy['revision'])
        retarget_registry(base / 'registry.json', data['git_url'], data['ref'], old, target)
