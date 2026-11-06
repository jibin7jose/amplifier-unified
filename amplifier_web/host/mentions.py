"""CLI-compatible input mentions using Foundation's shared expansion mechanism."""
from copy import copy
from pathlib import Path

from amplifier_foundation.mentions import BaseMentionResolver, expand_mentions_in_instruction
from amplifier_foundation.paths.resolution import get_amplifier_home


def include_instruction_files(bundle, config_home=None, execution_workspace=None, *, workspace=None):
    """Bind the host's optional instruction files without freezing their contents.

    Call after composition and runtime overrides, before preparing an ordinary
    root. Foundation reads context Paths and their nested mentions afresh on
    each request. Authored mentions retain their original meaning. Exported
    snapshot bundles retain their frozen instructions. The host supplies the
    associated workspace separately from a task's checkout. Never discover it
    by walking parent directories or borrowing another session's working path.
    """
    from amplifier_foundation.mentions import parse_mentions
    instruction = getattr(bundle, 'instruction', None) or ''
    declared = set(parse_mentions(instruction))
    result = copy(bundle)
    context = dict(getattr(bundle, 'context', None) or {})
    pending = getattr(bundle, '_pending_context', None) or {}
    # Every prepared namespace shares these context keys. Avoid retargeting an
    # author's reference, including one that does not currently resolve.
    reserved = {mention[1:].split(':', 1)[1] for mention in declared if ':' in mention}
    pending_keys = {key.split(':', 1)[-1] for key in pending}
    home = Path(config_home if config_home is not None else Path.home() / '.amplifier').expanduser().resolve()
    execution = Path(execution_workspace if execution_workspace is not None else Path.cwd()).expanduser().resolve()
    associated = Path(workspace).expanduser().resolve() if workspace is not None else execution
    defaults = {}
    paths = [('__unified_instruction_global', home / 'AGENTS.md')]
    if associated != execution:
        paths += [
            ('__unified_instruction_associated_project', associated / '.amplifier/AGENTS.md'),
            ('__unified_instruction_associated_workspace', associated / 'AGENTS.md'),
        ]
    paths += [
        ('__unified_instruction_project', execution / '.amplifier/AGENTS.md'),
        ('__unified_instruction_workspace', execution / 'AGENTS.md'),
    ]
    for stem, path in paths:
        alias, suffix = stem, 1
        while alias in pending_keys or alias in reserved or (alias in context and context[alias] != path):
            suffix += 1
            alias = f'{stem}_{suffix}'
        defaults[alias] = path
    # Authored instruction mentions still lead. Automatic files then precede
    # existing context entries, as the old appended mentions did. Paths avoid
    # requiring a parseable root-bundle name or adding an active namespace.
    result.context = {**defaults, **context}
    return result


class AppMentionResolver:
    """Add app shortcuts without composing additional bundles or their tools."""

    def __init__(self, foundation, workspace):
        self.foundation = foundation
        self.workspace = Path(workspace)

    def resolve(self, mention):
        return self.resolve_relative(mention, self.workspace)

    def resolve_relative(self, mention, relative_to):
        """Scope local references without retargeting project/user shortcuts."""
        if not mention.startswith('@') or '..' in mention:
            return None
        body = mention[1:]
        if body.startswith('user:'):
            root, relative = get_amplifier_home(), body[5:]
        elif body.startswith('project:'):
            root, relative = self.workspace / '.amplifier', body[8:]
        elif body.startswith('~/'):
            root, relative = Path.home(), body[2:]
        elif ':' in body:
            # Only the prepared composition owns bundle namespaces. Never
            # reinterpret a missing namespace as a local filename.
            return self.foundation.resolve(mention) if self.foundation else None
        else:
            # Retain Foundation's plain-path and omitted-.md behavior used by
            # filesystem tools, scoped to the workspace or referring file.
            return BaseMentionResolver(base_path=relative_to).resolve(mention)
        if not relative:
            return None
        path = root / relative
        return path.resolve() if path.exists() else None


def install(coordinator):
    resolver = coordinator.get_capability('mention_resolver')
    if not isinstance(resolver, AppMentionResolver):
        workspace = coordinator.get_capability('session.working_dir') or Path.cwd()
        coordinator.register_capability('mention_resolver', AppMentionResolver(resolver, workspace))


async def expand_input(coordinator, text, *, max_chars=None):
    if '@' not in text:
        return text
    resolver = coordinator.get_capability('mention_resolver')
    if resolver is None:
        return text
    # A fresh deduplicator includes only this input's references and sees file
    # edits on later turns. Do not prepend a session's entire cached context.
    expanded = await expand_mentions_in_instruction(text, resolver=resolver,
        relative_to=Path(coordinator.get_capability('session.working_dir') or Path.cwd()))
    if max_chars is not None and len(expanded) > max_chars:
        raise ValueError('The message and referenced files exceed this session input limit. Reference smaller files.')
    return expanded
