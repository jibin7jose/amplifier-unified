"""Read-only local bundle lookup shared by preparation and source inventory."""
from pathlib import Path
import re

from ..session_files import amplifier_home


class AmbiguousBundleReferenceError(ValueError):
    """A bare ID matched more than one registration; no source was selected."""


def configured_bundle_reference(config, reference, find):
    """Select a root/app reference without loading it; composition owns this rule."""
    reference = canonical_bundle_reference(config, reference)
    replacement = config.resolve_source(reference)
    if replacement is None:
        registered = find(reference)
        if registered:
            replacement = config.resolve_source(registered)
    reference = replacement or reference
    candidate = local_bundle_path(config, reference)
    return str(candidate) if candidate is not None else reference


def canonical_bundle_reference(config, reference):
    """Resolve a bare registered ID without changing paths, URLs or exact aliases.

    New IDs use lowercase kebab-case. Historical display-name casing is accepted
    only when it identifies one registration; guessing between aliases is unsafe.
    """
    if not re.fullmatch(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', reference):
        return reference
    if local_bundle_path(config, reference) is not None:
        return reference
    registrations = config.registrations
    if reference in registrations:
        return reference
    matches = [name for name in registrations if name.casefold() == reference.casefold()]
    if len(matches) > 1:
        raise AmbiguousBundleReferenceError('Ambiguous bundle ID casing; choose an exact registered bundle ID.')
    return matches[0] if matches else reference


def local_bundle_path(config, reference):
    if reference.startswith(('git+', 'https://', 'http://', 'ssh://')):
        return None
    candidate = Path(reference.removeprefix('file://')).expanduser()
    absolute = candidate.is_absolute()
    if not absolute:
        candidate = config.workspace / candidate
    # Bare names are also registry aliases (notably the default "work"). An
    # unrelated workspace folder must not shadow one merely by existing. Keep
    # explicit paths authoritative so malformed local bundles still fail at
    # their requested path rather than silently switching to another bundle.
    explicit = absolute or reference.startswith(('file://', './', '../')) or '/' in reference
    manifest = candidate.is_dir() and any((candidate / name).is_file()
        for name in ('bundle.md', 'bundle.yaml', 'bundle.yml'))
    if candidate.exists() and (explicit or manifest or candidate.is_file() and candidate.suffix in {'.md', '.yaml', '.yml'}):
        return candidate
    if absolute or reference.startswith('file://'):
        return None
    # Retain existing Unified precedence, then support the standard CLI project
    # and user bundle directories. Never fetch or inspect included bundles here.
    roots = (config.home / 'bundles', config.workspace / '.amplifier-unified/bundles',
             config.workspace / '.amplifier/bundles',
             (config.config_home or amplifier_home()) / 'bundles')
    for root in roots:
        for suffix in ('', '.md', '.yaml', '.yml'):
            path = root / (reference + suffix)
            if path.is_file() or path.is_dir() and any((path / name).is_file()
                    for name in ('bundle.md', 'bundle.yaml', 'bundle.yml')):
                return path
    return None
