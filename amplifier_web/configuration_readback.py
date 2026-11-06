"""Compare loaded plans using the worker's qualified source provenance.

Only module declarations may relocate. Configuration values and arbitrary paths
remain exact; a similar cache name is never evidence that the code is the same.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from .host.components import declarations


def matches(expected, actual, *, sources=None):
    """Normalize only public redaction, default enabled rows and proven sources."""
    from .runtime_controls import PLAN_KEYS, SECTIONS, public_config
    from .configuration_authority import fingerprint

    def normalized(value, aliases):
        value = {key: copy.deepcopy(item) for key, item in public_config(value).items() if key in PLAN_KEYS}
        for section in SECTIONS:
            for row in value.setdefault(section, []):
                row.setdefault('enabled', True)
        for row in declarations(value):
            if row.get('source') in aliases:
                row['source'] = aliases[row['source']]
        return value

    # Redacted public equality is not proof of the underlying secret bytes.
    return fingerprint(normalized(expected, sources or {})) == fingerprint(normalized(actual, {}))


def source_aliases(home, worker, expected, actual):
    """Read bounded qualification receipts for this exact worker's generation.

    Sources must be identical, or match the already adopted component update for
    the same mutable source choice. Never trust caller-supplied path mappings.
    Missing or ambiguous evidence leaves the original source comparison intact.
    """
    aliases = {}
    generation, selected = worker.get('generation'), worker.get('source_generation')
    if not generation or not selected or selected == generation:
        return aliases
    home = Path(home)
    base = home / 'updates/releases'
    if any(not isinstance(value, str) or Path(value).name != value or value in {'.', '..'}
           for value in (generation, selected)):
        return aliases
    try:
        target = base / selected
        parent = base / generation
        if json.loads((target / 'validated.json').read_text()).get('parentGeneration') != generation:
            return aliases
        if not (target / 'profiles-qualified.json').is_file():
            return aliases

        def records(root):
            rows = json.loads((root / 'runtime-installed.json').read_text())
            found = {}
            for row in rows:
                source = row.get('cacheSource') or {}
                if (not row.get('path') or source.get('dirty') is not False
                        or not source.get('url') or not source.get('ref')
                        or not re.fullmatch('[a-f0-9]{40}', source.get('revision', ''))
                        or not re.fullmatch('[a-f0-9]{64}', source.get('trackedContentSha256', ''))):
                    continue
                path = Path(row['path'])
                if not path.is_relative_to(base) or '..' in path.parts:
                    continue
                parts = path.relative_to(base).parts
                if len(parts) < 4 or parts[1:3] != ('foundation', 'cache'):
                    continue
                # A qualified environment can retain an older generation's
                # editable paths. This receipt owns that exact installed graph;
                # only its foundation-relative declaration is relocated.
                relative = Path(*parts[2:])
                identity = (row['name'], source['url'], source['ref'], source['revision'],
                            source.get('subdirectory'), source['trackedContentSha256'])
                found[relative] = identity if relative not in found else None
            return found

        if not (parent / 'validated.json').is_file() or not (parent / 'profiles-qualified.json').is_file():
            return aliases
        from .updates import active_release, pinned

        adopted = records(parent)
        current = active_release(home).get('current') == generation
        previous = {generation: adopted}
        new = records(target)
        loaded = {row.get('source') for row in declarations(actual)}
        for row in declarations(expected):
            source = row.get('source')
            if (not isinstance(source, str) or '..' in Path(source).parts
                    or not Path(source).is_relative_to(base)):
                continue
            parts = Path(source).relative_to(base).parts
            if len(parts) < 4 or parts[1:3] != ('foundation', 'cache'):
                continue
            prior = parts[0]
            if prior not in previous:
                root = base / prior
                # A saved edit can outlive multiple component updates. Its old
                # qualification still proves which source choice was saved.
                if (not (root / 'profiles-qualified.json').is_file()
                        or not isinstance(json.loads((root / 'validated.json').read_text()), dict)):
                    continue
                previous[prior] = records(root)
            old = previous[prior]
            relative = Path(*parts[2:])
            replacement = str(target / 'foundation' / relative)
            if replacement not in loaded:
                continue
            candidates = [path for path in old if relative == path or relative.is_relative_to(path)]
            if not candidates:
                continue
            path = max(candidates, key=lambda path: len(path.parts))
            before, after = old[path], new.get(path)
            if before is None or after is None:
                continue
            same_choice = before[:3] == after[:3] and before[4] == after[4]
            adopted_update = (current and same_choice and not pinned(before[2])
                              and after == adopted.get(path))
            if before == after or adopted_update:
                aliases[source] = replacement
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    return aliases


async def inspect_loaded(management, session, expected, guard):
    """Inspect the captured existing owner; never prepare or start a worker."""
    runtime, sid = management.service.runtime, session['id']
    workers = getattr(runtime, 'workers', {})
    row = workers.get(sid)
    def ownership():
        return tuple(row.get(key) for key in ('runtimeOwnerId', 'generation', 'source_generation')) if row is not None else None
    owner = ownership()

    def same_owner():
        guard()
        if workers.get(sid) is not row or ownership() != owner:
            raise ValueError('The loaded configuration owner changed before readback completed.')

    same_owner()
    inspected = await runtime.control(sid, 'configuration.inspect', {}, guard=same_owner)
    same_owner()
    if not isinstance(inspected.get('plan'), dict):
        raise ValueError('Saved configuration has no confirmed loaded-plan readback.')
    sources = source_aliases(management.service.data_dir, row or {}, expected, inspected['plan'])
    if not matches(expected, inspected['plan'], sources=sources):
        raise ValueError('Saved configuration does not match loaded-plan readback. Its operation remains unresolved.')
    return inspected, same_owner
