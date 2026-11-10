"""Reusable workspace starter definitions; templates are data, never executable hooks."""
from __future__ import annotations

import copy
import json
import re
import uuid
from urllib.parse import urlsplit

from filelock import FileLock
from amplifier_worktrees.git import atomic
from .host.config import PRECONFIGURED_BUNDLES
from .bundle_selection import catalog_entry, defaults

DEVELOPMENT_INSTRUCTIONS = """# Development workspace

## The workspace is a container, not a project's source root

This is a lasting working area for related projects, conversations and working files. Each project lives in a child directory with its own Git repository. The workspace root is never the source root of a new project.

| Request | Working location |
| --- | --- |
| Get an existing repository | Clone into a named child directory of this workspace. |
| Start a new project | Create a child directory; initialize that project's Git there. |
| Publish a project | Work from that child repository; confirm remote and visibility first. |

Do not reuse an unrelated checkout elsewhere or scan parent/home directories for clones to borrow. Preserve unrelated files, branches and remotes. Always make the intended working directory explicit. Submodules are optional, not required.

## What belongs at the root

Use the root for cross-repository plans, design drafts, investigation results, working scripts, experiments, logs and handoff files. Do not mix these with source meant to become a published project or commit them into a child repo. Unlike a disposable session workspace, Unified keeps this folder and its chats until someone deliberately changes or removes them. Never promise automatic deletion or treat workspace removal as permission to delete files.

Typical layout:

    workspace/
      AGENTS.md               workspace-wide guidance
      SCRATCH.md              current shared context and detail links
      .amplifier/             host configuration and instruction entry point
      project-one/            its own .git and project conventions
      project-two/            its own .git and project conventions
      working-files/          cross-project drafts, experiments and outputs

## Each child repository has its own rules

Before changing a repo, read its applicable AGENTS.md and the contribution, architecture or verification guidance needed for the task. Reuse that context while its source and applicable scope are unchanged; changing from design to implementation or review is not by itself a reason to reread every file. Recheck when relevant guidance changes, you enter a new subtree, or a decision needs an exact rule. Fill the PR template from actual evidence. Capture durable lessons in the owning repository only when in scope, preserving its information boundaries.

## Checkpoints and worktrees

Use small, meaningful commits in the child repository for reversible checkpoints. Keep worktrees and branches scoped to that project; retain uncommitted work and do not reset, delete or publish it without authorization. Optional local root Git can checkpoint workspace notes and plans. When enabled by the selected starter, setup initializes it without making commits. Exclude child repositories, credentials, caches and disposable outputs, and stage explicit files rather than blindly adding the entire workspace. Root Git is never a substitute for committing or backing up project source in its child repository.

## Working memory

@./SCRATCH.md

SCRATCH.md is loaded into every request. Keep it a short current context and index: shared focus, decisions still relevant to several tasks, and links to the owning task or evidence. It is not a task database, event log, release ledger, copy of a transcript, or source of authority. Saved host tasks and receipts own status; actual user instructions and current permissions own authority. Do not copy their full records here or treat an old note as a current hold.

Workers keep implementation notes, failed attempts, detailed checks and receipts with their own task. The manager promotes only decisions that other workers need. Use ordinary paths or Markdown links for on-demand details; do not add instruction includes that load archives, logs or whole task folders into every request. Keep durable workspace conventions in AGENTS.md, not in SCRATCH.md.

Replace superseded current entries rather than appending a new dated account. Preserve unfinished work and useful history in a task file or archive outside automatic context; obsolete duplicate notes need not be retained. Read current contents before editing, update only your owned section, and preserve concurrent changes. Keep owner and next decision beside any unresolved shared blocker. A scratch edit neither completes a task nor resolves an unknown operation.

## Managers and independent workers

One manager coordinates many independent goal-driven workers when the work permits. Split the objective into useful deliverables with clear ownership, acceptance criteria, dependencies and existing authority. Each worker carries its deliverable through implementation, ordinary repairs, focused isolated checks and a reviewable result or PR. Use separate working copies for concurrent source writers through supported host placement; do not share a mutable checkout by accident.

The manager prioritizes, integrates, reviews and resolves cross-task decisions. Workers report a completed result or a concrete blocker requiring someone else, not every step or passing check. Routine repair and testing within the assigned objective do not need another approval. Reuse evidence when its relevant inputs are unchanged. Keep actual Stop, permissions, explicit limits and uncertain-effect recovery intact. Resource pressure may delay a dependent check; it does not require idling unrelated work.

## External resources and completion

Record externally created resources promptly using the host's workspace resource actions: exact identifier, owner, purpose and cleanup responsibility. Containers, previews and cloud resources do not disappear when a folder or registration does. Keep active, reaped (our confirmed teardown), and observed_absent (independent absence) distinct, with evidence. Inventory entries do not authorize teardown. Before finishing, reconcile resources, preserve remaining work, provide an explicit cleanup handoff where needed, and verify the promised result at the user-visible boundary. A local-only project must be backed up or published deliberately before any approved deletion of its folder.
"""

SCRATCH_TEMPLATE = """# Current workspace context

This file is loaded into every request. Keep only current shared context and links to details; replace stale entries rather than appending history. It is not a task database, event log or source of authority. Host tasks and receipts own status; user instructions and current permissions own authority.

## Current focus and shared decisions

No shared decisions recorded yet. Keep only decisions that still affect several tasks; durable workspace conventions belong in AGENTS.md.

## Unresolved shared blockers

Record an owner, the missing decision or dependency, and a pointer to its current task. A scratch note does not resolve an unknown operation or complete work.

## Detail index

Link to task-owned notes and exact evidence only when another worker needs them. Use ordinary paths or Markdown links, not automatic instruction includes. Do not paste transcripts, full receipts, release history or duplicate task status here. Workers keep their own detail; the manager promotes only shared decisions. Before editing, reread and preserve other owners' current changes. Move useful superseded history outside automatic context; preserve unfinished work and discard obsolete duplicate notes.
"""

BUILTINS = [
    {'id': 'blank', 'name': 'Blank', 'description': 'An empty folder. Use your existing defaults.',
     'instructions': '', 'bundle': '', 'repositories': [], 'trackResources': False, 'scratch': False, 'rootGit': False},
    {'id': 'development', 'name': 'Development', 'description': 'A workspace container with agent guidance, working memory and child repositories.',
     'instructions': DEVELOPMENT_INSTRUCTIONS, 'bundle': '', 'repositories': [], 'trackResources': True, 'scratch': True, 'rootGit': False},
    {'id': 'amplifier-development', 'name': 'Amplifier development',
     'description': 'Amplifier, Core and Foundation as child repositories, with working memory and local workspace Git.',
     'instructions': DEVELOPMENT_INSTRUCTIONS, 'bundle': 'work-amp-dev', 'trackResources': True, 'scratch': True, 'rootGit': True,
     'repositories': [{'url': f'https://github.com/microsoft/{name}.git', 'directory': name, 'ref': ''}
                      for name in ('amplifier', 'amplifier-core', 'amplifier-foundation')]},
]
FIELDS = {'name', 'description', 'instructions', 'bundle', 'repositories', 'trackResources', 'scratch', 'rootGit'}


def repository(value):
    if not isinstance(value, dict) or set(value) - {'url', 'directory', 'ref'}:
        raise ValueError('A repository needs a source URL and a child folder name.')
    if any(not isinstance(value.get(key, ''), str) for key in ('url', 'directory', 'ref')):
        raise ValueError('Repository source, folder and ref must be text.')
    url = value.get('url', '').strip()
    parts = urlsplit(url)
    https = parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
    ssh = parts.scheme == 'ssh' and parts.hostname and not parts.password
    scp = bool(re.fullmatch(r'[\w.-]+@[\w.-]+:[\w./-]+', url))
    if not (https or ssh or scp) or parts.query or parts.fragment or any(c.isspace() for c in url) or len(url) > 2000:
        raise ValueError('Use an HTTPS or SSH Git URL without embedded credentials, query or fragment.')
    directory = value.get('directory', '').strip()
    if not directory:
        directory = url.rsplit('/', 1)[-1].removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', directory) or directory.lower() in {'con', 'prn', 'aux', 'nul'}:
        raise ValueError('Repository folders must be simple child names (letters, numbers, hyphens or underscores).')
    ref = value.get('ref', '').strip()
    if ref and (len(ref) > 200 or not re.fullmatch(r'[\w][\w./-]*', ref) or '..' in ref or ref.endswith(('/', '.', '.lock')) or '//' in ref):
        raise ValueError('Use a branch or tag name, or leave it blank for the remote default branch.')
    return {'url': url, 'directory': directory, 'ref': ref}


def definition(value, bundle_names=None):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError('Unknown starter field.')
    limits = {'name': 200, 'description': 1000, 'instructions': 16000, 'bundle': 2000}
    result = {}
    for key, limit in limits.items():
        item = value.get(key, '')
        if not isinstance(item, str) or len(item) > limit or '\0' in item:
            raise ValueError(f'Invalid starter {key}.')
        result[key] = item.strip()
    if not result['name']:
        raise ValueError('Name this workspace starter.')
    if result['bundle'] and result['bundle'] not in (bundle_names if bundle_names is not None else PRECONFIGURED_BUNDLES):
        raise ValueError('Choose a configured standalone bundle, or inherit the existing default.')
    repos = value.get('repositories', [])
    if not isinstance(repos, list) or len(repos) > 20:
        raise ValueError('A starter supports up to 20 repositories.')
    result['repositories'] = [repository(row) for row in repos]
    names = [row['directory'].casefold() for row in result['repositories']]
    if len(names) != len(set(names)):
        raise ValueError('Each repository needs a distinct folder name.')
    tracking = value.get('trackResources', False)
    if type(tracking) is not bool:
        raise ValueError('Resource tracking must be true or false.')
    result['trackResources'] = tracking
    if type(value.get('scratch', False)) is not bool:
        raise ValueError('Working memory must be true or false.')
    result['scratch'] = value.get('scratch', False)
    if type(value.get('rootGit', False)) is not bool:
        raise ValueError('Local workspace Git must be true or false.')
    result['rootGit'] = value.get('rootGit', False)
    return result


class StarterCatalog:
    def __init__(self, home, bundles=None, sources=None, *, inherit_amplifier_default=False):
        self.path = home / 'workspace-starters.json'
        self.bundles = copy.deepcopy(bundles if bundles is not None else [catalog_entry(name) for name in PRECONFIGURED_BUNDLES])
        self.bundle_names = {row['value'] for row in self.bundles}
        self.sources = dict(sources or {})
        self.inherit_amplifier_default = inherit_amplifier_default

    def _read(self):
        if not self.path.exists():
            return {'revision': 0, 'items': [], 'commands': {}}
        try:
            value = json.loads(self.path.read_text())
            if not isinstance(value['items'], list) or type(value['revision']) is not int:
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError):
            raise ValueError('The workspace starter library is unreadable. Preserve it and repair it before saving.') from None

    def listing(self):
        value = self._read()
        revisions = {'blank': 1, 'development': 7, 'amplifier-development': 9}
        builtins = [{**copy.deepcopy(row), 'builtIn': True, 'revision': revisions[row['id']]} for row in BUILTINS]
        if self.inherit_amplifier_default:
            # Inherit deliberately saved defaults, not the automatic Work fallback.
            # Only future built-in snapshots change; saved custom choices do not.
            next(row for row in builtins if row['id'] == 'amplifier-development')['bundle'] = ''
        # Older custom definitions retain their behavior; no file migration.
        customs = [{**copy.deepcopy(row), 'scratch': row.get('scratch', False), 'rootGit': row.get('rootGit', False)} for row in value['items']]
        return {'revision': value['revision'], 'items': builtins + customs,
                'bundles': copy.deepcopy(self.bundles)}

    def snapshot(self, identity='blank'):
        row = next((row for row in self.listing()['items'] if row['id'] == identity), None)
        if row is None:
            raise ValueError('This starter is unavailable. Choose another starter.')
        definition({key: row[key] for key in FIELDS}, self.bundle_names)
        # A project/private standalone registration must remain resolvable from
        # the new workspace too. Retain only its chosen source, never providers.
        if row['bundle'] and row['bundle'] in self.sources:
            row['bundleSource'] = self.sources[row['bundle']]
        return row

    def command(self, action, args, command_id):
        if action == 'workspace.starters.list':
            return self.listing()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + '.lock'):
            value = self._read()
            fingerprint = json.dumps([action, args], sort_keys=True)
            prior = value.get('commands', {}).get(command_id) if command_id else None
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise ValueError('This request already saved a different starter change.')
                return prior['result']
            identity = args.get('id')
            old = next((row for row in value['items'] if row['id'] == identity), None)
            if action == 'workspace.starters.duplicate':
                source = self.snapshot(args['id'])
                fields = {key: source[key] for key in FIELDS}
                fields['name'] = args.get('name') or source['name'] + ' copy'
                row = {**definition(fields, self.bundle_names), 'id': str(uuid.uuid4()), 'builtIn': False, 'revision': 1}
                value['items'].append(row)
                result = row
            elif action == 'workspace.starters.save':
                if identity and not old:
                    raise ValueError('Built-in starters cannot be edited. Duplicate one to customize it.')
                if old and args.get('expectedRevision') != old['revision']:
                    raise ValueError('This starter changed. Reload it before saving.')
                row = {**definition(args['starter'], self.bundle_names), 'id': identity or str(uuid.uuid4()), 'builtIn': False,
                       'revision': old['revision'] + 1 if old else 1}
                value['items'] = [row if item['id'] == identity else item for item in value['items']] if old else value['items'] + [row]
                result = row
            elif action == 'workspace.starters.remove':
                if not old:
                    raise ValueError('Only custom starters can be deleted.')
                if args['expectedRevision'] != old['revision']:
                    raise ValueError('This starter changed. Reload it before deleting.')
                value['items'] = [item for item in value['items'] if item['id'] != identity]
                result = {'id': identity, 'removed': True}
            else:
                raise ValueError('Unknown starter action.')
            if len(value['items']) > 100:
                raise ValueError('The starter library supports up to 100 custom starters.')
            value['revision'] += 1
            if command_id:
                commands = value.setdefault('commands', {})
                commands[command_id] = {'fingerprint': fingerprint, 'result': result}
                value['commands'] = dict(list(commands.items())[-200:])
            atomic(self.path, value)
            return copy.deepcopy(result)


def configured_catalog(service):
    from .host.config import read_config
    from .bundles import offered_catalog
    config = read_config(service.state['settings']['workspace'], home=service.data_dir)
    inherited = defaults(service.data_dir, service.state['settings']['workspace'],
                         service.state['settings'].get('appBundle'), global_only=True)
    return StarterCatalog(service.data_dir, offered_catalog(config), {
        name: config.resolve_source(config.registrations[name]) or config.registrations[name]
        for name in config.settings.get('bundle', {}).get('added', {})
        if name in config.registrations
    }, inherit_amplifier_default=bool(inherited['app'] or inherited['shared']))
