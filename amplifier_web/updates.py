"""App-owned ecosystem updates: read-only checks, isolated staging, atomic promotion.

Mutable Git sources in the Foundation cache and worker environment are
refreshable. User-supplied pins and local worktrees remain explicit choices.
"""
from __future__ import annotations
import asyncio
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import tomllib
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid
from .host.config import write_private

DEFAULT_CHECK_INTERVAL_HOURS = 4
CHECK_INTERVAL_HOURS = (1, 4, 8, 24)


def work_paused(state):
    updates = state.get('updates', {})
    from .app_replacement import pending
    return updates.get('phase') == 'activating' or bool(updates.get('pendingRestart')) or pending(updates)


def active_release(home):
    path = Path(home) / 'updates' / 'active.json'
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        value = {}
    for field in ('current','previous'):
        identity = value.get(field)
        if identity is not None and (not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}', identity)):
            raise ValueError('Invalid ecosystem release identity')
    return value


def selected_release(home):
    # Workers capture their source generation at spawn; the serving host follows
    # the current pointer. Never change a running worker's resolver underneath it.
    selected = os.environ.get('AMPLIFIER_UNIFIED_RELEASE')
    identity = (selected or None) if selected is not None else active_release(home).get('current')
    if identity is not None and not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('Invalid worker source generation')
    return identity


def foundation_home(home):
    home = Path(home)
    identity = selected_release(home)
    return home / 'updates' / 'releases' / identity / 'foundation' if identity else home / 'foundation'



def safe_label(url):
    parsed = urlsplit(url)
    return (parsed.hostname or 'Git source') + '/' + parsed.path.strip('/').removesuffix('.git')


def group_sources(items):
    """One repository/ref/revision/status per display row, with every role kept.

    Installation consumes the private inventory, never these presentation rows.
    Distinct refs, revisions, tiers and protected states must remain distinct.
    """
    groups = {}
    for item in items:
        row = {k: v for k, v in item.items() if k not in {'path', 'url', 'eligible', 'appSelection', 'availabilityFailure'}}
        if row.get('kind') not in {'bundle / module', 'runtime dependency', 'source group'} or not row.get('ref'):
            groups[('id', row['id'])] = row
            continue
        key = tuple(row.get(k) for k in ('label', 'ref', 'current', 'latest', 'status', 'updateTier'))
        members = row.get('members') or [row]
        if key not in groups:
            groups[key] = {**row, 'members': []}
        target = groups[key]
        known = {member['id'] for member in target['members']}
        target['members'].extend(member for member in members if member['id'] not in known)
    result = []
    for key, row in groups.items():
        members = row.get('members', [])
        if len(members) > 1:
            row['id'] = 'source:' + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
            row['kind'] = 'source group'
            row['cacheCopies'] = sum(member.get('cacheCopies', 1) for member in members if member.get('kind') == 'bundle / module')
            row['packageCount'] = len({member['package'] for member in members if member.get('package')})
            row['usage'] = 'configured' if any(member.get('usage') == 'configured' for member in members) else 'unknown'
            row['usageEvidence'] = sorted({evidence for member in members for evidence in member.get('usageEvidence', [])})
            row['members'] = sorted(members, key=lambda member: (member.get('package', ''), member['id']))
        elif members:
            row = {**members[0]}
        result.append(row)
    return result


def pinned(ref):
    return bool(re.fullmatch(r'[0-9a-fA-F]{7,40}', ref) or re.match(r'^(refs/tags/|v?\d+\.)', ref))


def source_key(url, ref):
    """Compare repository/ref identity, never infer that main and master agree."""
    parsed = urlsplit(url)
    if parsed.scheme not in {'https', 'http', 'ssh'} or not parsed.hostname or not isinstance(ref, str) or not ref:
        raise ValueError('Unsupported Git source')
    return (parsed._replace(path=parsed.path.rstrip('/').removesuffix('.git'), fragment='').geturl(),
            ref.removeprefix('refs/heads/'))


def configured_sources(service):
    """Positive configuration evidence only, NOT a reachability/garbage collector.

    Registry entries resolve selected names; their mere presence proves nothing.
    Dynamic/transitive dependencies and local bundle contents remain unknown.
    Never prepare a bundle, import credentials or mutate configuration here.
    """
    from .host.config import read_config
    from .shared_settings import SettingsReadCache
    import yaml
    sources, incomplete = {}, False
    settings_cache = getattr(service, "settings_cache", None) or SettingsReadCache()
    issues = getattr(service, 'source_issues', None)
    def issue(reason, *, workspace=None, session_id=None, reference=None, historical=False):
        nonlocal incomplete
        incomplete = True
        if issues is None:
            return
        # Report identities and locations, never arbitrary settings or exception
        # contents (which may include credentials).
        if isinstance(reference, str) and reference.startswith(('git+', 'https:', 'http:', 'ssh:')):
            reference = safe_label(reference.removeprefix('git+'))
        owner = next((session for session in state['sessions'] if session_id and
                      (session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']) == session_id
                      and session.get('workspace') == workspace), None)
        row = {'reason': reason, 'historical': bool((owner and owner.get('historyManaged') and owner['id'] not in selected_ids and owner.get('status') not in {'working', 'running'}) or (not owner and workspace and str(workspace) not in used_workspaces)), **({'appSessionId': owner['id']} if owner else {}), **({'workspace': str(workspace)} if workspace else {}),
               **({'sessionId': session_id} if session_id else {}),
               **({'reference': str(reference)[:300]} if reference else {})}
        if row not in issues:
            if len(issues) < 50:
                issues.append(row)
            elif not row['historical']:
                # Old selections cannot crowd an active configuration error
                # out of the bounded report.
                replace = next((index for index, item in enumerate(issues) if item.get('historical')), None)
                if replace is not None:
                    issues[replace] = row
    state, home = service.state, service.data_dir
    selected_ids = set(state.get('selectedSessionIds', [])) | {state.get('selectedSessionId')}
    used_workspaces = {state['settings']['workspace']} | {row.get('workspace') for row in state['sessions']
        if not row.get('historyManaged') or row['id'] in selected_ids or row.get('status') in {'working', 'running'}}
    selections = {(state['settings']['workspace'], state['settings']['bundle'], None)}
    # Native child/legacy history can be viewable without a resumable identity.
    # Its workspace is still inspected below; it is not a broken root selection.
    if not getattr(service, 'app_only', False):
        selections.update((s['workspace'], s['bundle'], s.get('runtimeSessionId') or s.get('nativeIdentity') or s['id'])
                          for s in state['sessions']
                          if not (s.get('historyManaged') and s.get('historyReadOnlyReason')))
        for workspace in state.get('workspaces', []):
            # Native history retains an unavailable, pathless workspace placeholder.
            # It is not a source-selection error, unlike malformed workspace rows.
            if (isinstance(workspace, Mapping)
                    and isinstance(workspace.get('nativeProject'), str)
                    and workspace.get('nativeProject')
                    and 'path' in workspace and workspace['path'] is None
                    and workspace.get('available') is False):
                continue
            path = workspace.get('path') if isinstance(workspace, Mapping) else None
            if not isinstance(path, str) or not path.strip():
                incomplete = True
                continue
            selections.add((path, None, None))

    for workspace, selected, session_id in selections:
        try:
            # Missing registrations and saved sessions remain available for
            # history. Their unavailable directories are not configuration errors.
            if not workspace:continue
            if not Path(workspace).expanduser().is_dir():continue
            config = read_config(workspace, home=home, session_id=session_id, settings_cache=settings_cache,
                                 global_only=getattr(service, "app_only", False))
            # Use the same settings authority as session startup. Retained
            # imported registry aliases must not masquerade as configured use.
            registrations = dict(config.registrations)

            def resolve(reference, evidence, seen=frozenset()):
                nonlocal incomplete
                if not isinstance(reference, str) or not reference or reference in seen:
                    issue('A source alias is empty or circular.', workspace=workspace, session_id=session_id, reference=reference)
                    return
                if reference.startswith('git+'):
                    parsed = urlsplit(reference[4:])
                    path, separator, ref = parsed.path.rpartition('@')
                    url = parsed._replace(path=path if separator else parsed.path, fragment='').geturl()
                    sources.setdefault(source_key(url, ref if separator else 'HEAD'), set()).add(evidence)
                    return
                # Match the host's local-before-registry preference. Local files
                # may contain arbitrary includes; don't guess their dependencies.
                from .host.bundle_paths import local_bundle_path
                if local_bundle_path(config, reference) is not None:
                    return
                namespace = reference.split(':', 1)[0]
                replacement = config.resolve_source(reference) or registrations.get(namespace)
                if replacement:
                    resolve(replacement, evidence, seen | {reference})
                else:
                    issue('This bundle name is not registered. Choose an available bundle in this conversation, or restore its source in workspace settings.', workspace=workspace, session_id=session_id, reference=reference, historical=evidence == 'Selected bundle')

            # Do not walk added/registered bundle lists: those are catalogs.
            if getattr(service, 'app_only', False):
                from .bundles import offered_profiles
                for profile in offered_profiles(config):
                    resolve(profile, 'Offered app profile')
                resolve(selected or config.active_bundle, 'Selected app profile')
            else:
                resolve(selected or config.active_bundle, 'Selected bundle')
            for reference in config.app_bundles:
                resolve(reference, 'Enabled app bundle')
            for reference in config.module_sources.values():
                resolve(reference, 'Module source configuration')

            def configured_git_values(value):
                # Module config can declare skill sources (and other plugins'
                # Git inputs). Only explicit Git URIs are positive evidence.
                if isinstance(value, dict):
                    for child in value.values():configured_git_values(child)
                elif isinstance(value, list):
                    for child in value:configured_git_values(child)
                elif isinstance(value, str) and value.startswith('git+'):
                    resolve(value, 'Git input in module configuration')
            configured_git_values(config.settings.get('config', {}))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, yaml.YAMLError):
            issue('Workspace or conversation settings could not be read. Review the settings files for this workspace.', workspace=workspace, session_id=session_id, reference=selected)
    return sources, incomplete


async def process(*args, cwd=None, env=None, timeout=90, raw=False):
    if (env if env is not None else os.environ).get('AMPLIFIER_GITHUB_ACCESS') == 'anonymous':
        from .reset import repository_environment
        env = repository_environment(env)
    elif sys.platform == 'win32' and str(args[0]).replace('\\', '/').rsplit('/', 1)[-1].lower() in {'git', 'git.exe'}:
        # Ordinary checkout/fetch must work in the same deep candidate paths as
        # anonymous reads, without changing credential helpers, EOL or includes.
        from .reset import git_filesystem_environment
        env = git_filesystem_environment(env)
    started=time.monotonic()
    proc = await asyncio.create_subprocess_exec(*map(str,args), cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=os.name != 'nt')
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as error:
        if proc.returncode is None:
            try:
                if os.name != 'nt':
                    import signal
                    os.killpg(proc.pid, signal.SIGTERM)
                else: proc.terminate()
            except ProcessLookupError:
                pass
            try: await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                try:
                    if os.name != 'nt': os.killpg(proc.pid, signal.SIGKILL)
                    else: proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        if isinstance(error,TimeoutError):
            from .update_diagnostics import CommandTimeout
            raise CommandTimeout(time.monotonic()-started) from None
        raise
    if proc.returncode:
        from .reset import anonymous_retry_environment
        retry = anonymous_retry_environment(args, env, stdout, stderr)
        remaining = timeout - (time.monotonic() - started)
        if retry is not None and remaining > 0:
            result = await process(*args, cwd=cwd, env=retry, timeout=remaining, raw=raw)
            if hasattr(result, 'diagnostic_facts'):
                result.diagnostic_facts['anonymousRetry'] = True
            return result
        from .update_diagnostics import CommandFailure,command_facts
        raise CommandFailure(command_facts(stdout,stderr,proc.returncode,time.monotonic()-started))
    if raw:return stdout
    from .update_diagnostics import CommandOutput,command_facts
    return CommandOutput(stdout.decode(errors='replace').strip(),command_facts(stdout,stderr,proc.returncode,time.monotonic()-started))


# Content identity of the reviewed wiki-weaver hook, NOT a dependency/revision pin.
# Unknown hook implementations remain protected until separately reviewed.
_WIKI_VERSION_HOOK_SHA256 = 'd0733aa7fecaadf307fdeb42dd202c4fec1a8255e56b24bb23777cbd003620f3'
_WIKI_VERSION_HEADER = b'''"""Single source of truth for the wiki-weaver package version.

Kept as a leaf module with no imports so that any submodule can safely
import __version__ without risk of triggering a circular import through
the wiki_weaver package __init__.

This value is baked in at wheel-build time by the git-version hatchling
build hook (see hatch_build.py at the repo root) -- it is the commit date
+ short SHA of the exact commit this wheel was built from, not the build
date. Do not hand-edit; it is overwritten on the next build.
"""

'''


async def cache_checkout_options(root):
    """Read the validated local EOL scalar, never personal transport policy."""
    from .runtime_qualification import _installed_git_environment, _installed_checkout_environment
    isolated = _installed_git_environment()
    checkout = await asyncio.to_thread(_installed_checkout_environment, root, isolated)
    if checkout is not isolated:
        # Reuse installed provenance's validated scalar, not personal auth,
        # includes or rewrites. process() re-sanitizes anonymous environments,
        # so carry only this local-checkout value as an explicit Git argument.
        last = int(checkout['GIT_CONFIG_COUNT']) - 1
        return ['-c', 'core.autocrlf=' + checkout[f'GIT_CONFIG_VALUE_{last}']]
    return []


async def cache_changes(root):
    """Return protected changes and proven cache artifacts, without editing files.

    Imported caches used to flatten symlinks, and some upstreams track generated
    bytecode. Wiki-weaver's reviewed build hook also bakes a version into source.
    Only unstaged, exactly verified generated forms may be restored later,
    inside an isolated staging copy. Untracked files are never removed.
    """
    root = Path(root).resolve()
    options = await cache_checkout_options(root)
    records = (await process('git',*options,'--no-optional-locks','status','--porcelain=v1','-z','--untracked-files=no',cwd=root,timeout=10,raw=True)).split(b'\0')
    changed = None
    if options and any(record.startswith(b' M ') for record in records):
        # With optional index writes disabled, status may keep a stat-only M
        # after changing EOL interpretation. Ask Git for actual worktree edits.
        # Staged/type/deleted changes below remain protected independently.
        changed = set((await process('git',*options,'--no-optional-locks','diff',
            '--no-ext-diff','--no-textconv','--name-only','-z','--',
            cwd=root,timeout=10,raw=True)).split(b'\0'))
    protected, artifacts = [], []

    async def head_entry(path, revision='HEAD'):
        value = await process('git','--literal-pathspecs','ls-tree','-z',revision,'--',path,cwd=root,timeout=10,raw=True)
        entries = value.split(b'\0')
        if len(entries)!=2 or not entries[0]:return None
        metadata, name = entries[0].split(b'\t',1)
        mode, kind, identity = metadata.decode('ascii').split()
        return (mode,identity) if kind=='blob' and os.fsdecode(name)==path else None

    def regular(path, mode=None):
        candidate = root/path
        try:
            if not candidate.resolve(strict=True).is_relative_to(root):return False
            if any(parent.is_symlink() for parent in [candidate,*candidate.parents] if parent!=root and parent.is_relative_to(root)):return False
            current = candidate.stat().st_mode
            return stat.S_ISREG(current) and (mode is None or bool(current&stat.S_IXUSR)==(mode=='100755'))
        except (OSError,ValueError):return False

    def bytecode_header(value):
        return len(value)>=16 and value[2:4]==b'\r\n' and int.from_bytes(value[4:8],'little')&~3==0

    async def wiki_generated_version():
        # Never execute/import a cached hook or evaluate Python source. Verify its
        # exact reviewed bytes and reproduce only that implementation's output.
        version_path = 'wiki_weaver/_version.py'
        originals = {}
        try:
            captured = await process('git','rev-parse','--verify','HEAD^{commit}',cwd=root,timeout=10,raw=True)
            if not re.fullmatch(rb'(?:[0-9a-f]{40}|[0-9a-f]{64})\n',captured):return False
            revision = captured[:-1].decode('ascii')
            for name in (version_path, 'hatch_build.py', 'pyproject.toml'):
                entry = await head_entry(name, revision)
                if not entry or entry[0] not in {'100644','100755'} or not regular(name,entry[0]):return False
                mode, identity = entry
                indexed = await process('git','--literal-pathspecs','ls-files','--stage','-z','--',name,cwd=root,timeout=10,raw=True)
                if indexed != f'{mode} {identity} 0\t{name}\0'.encode():return False
                original = await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True)
                if name != version_path and (root/name).read_bytes() != original:return False
                originals[name] = original
            if hashlib.sha256(originals['hatch_build.py']).hexdigest() != _WIKI_VERSION_HOOK_SHA256:return False
            project = tomllib.loads(originals['pyproject.toml'].decode('utf-8'))
            if project['project']['name'] != 'wiki-weaver':return False
            if project['build-system'] != {'requires':['hatchling'], 'build-backend':'hatchling.build'}:return False
            if project['tool']['hatch']['build']['hooks']['custom'] != {'path':'hatch_build.py'}:return False
            original = originals[version_path]
            if not original.startswith(_WIKI_VERSION_HEADER):return False
            if not re.fullmatch(rb'__version__ = "[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]{4,64}"\n',original[len(_WIKI_VERSION_HEADER):]):return False
            # Match the hook's actual Git semantics (including core.abbrev), not
            # an assumed seven-character prefix or a wall-clock/author date.
            # Bind all awaited reads to one commit; a moving HEAD cannot mix
            # one commit's tree/date with another commit's abbreviation.
            date = await process('git','log','-1','--format=%cd','--date=format:%Y.%m.%d',revision,cwd=root,timeout=10,raw=True)
            sha = await process('git','rev-parse','--short',revision,cwd=root,timeout=10,raw=True)
            if not re.fullmatch(rb'[0-9]{4}\.[0-9]{2}\.[0-9]{2}\n',date):return False
            if not re.fullmatch(rb'[0-9a-f]{4,64}\n',sha) or not captured[:-1].startswith(sha[:-1]):return False
            expected = _WIKI_VERSION_HEADER + b'__version__ = "' + date[:-1] + b'-' + sha[:-1] + b'"\n'
            if (root/version_path).read_bytes() != expected:return False
            return await process('git','rev-parse','--verify','HEAD^{commit}',cwd=root,timeout=10,raw=True) == captured
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError):
            return False

    index = 0
    while index<len(records):
        record=records[index];index+=1
        if not record:continue
        change=record[:2].decode('ascii');path=os.fsdecode(record[3:])
        # Renames/copies have a second NUL-delimited name, even with whitespace.
        if 'R' in change or 'C' in change:index+=1
        if change == ' M' and changed is not None and os.fsencode(path) not in changed:
            continue
        if change not in {' M',' T'}:
            protected.append(path);continue
        if path=='wiki_weaver/_version.py':
            generated=change==' M' and await wiki_generated_version()
            (artifacts if generated else protected).append(path)
            continue
        entry=await head_entry(path)
        if not entry or not regular(path):
            protected.append(path);continue
        mode,identity=entry
        generated=False
        if change==' M' and mode in {'100644','100755'} and regular(path,mode) and '__pycache__' in Path(path).parts and re.fullmatch(r'.+\.cpython-\d+(?:\.opt-\d+)?\.pyc',Path(path).name):
            original=await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True)
            with (root/path).open('rb') as file:header=file.read(16)
            generated=bytecode_header(original) and bytecode_header(header)
        elif change==' T' and mode=='120000':
            link=os.fsdecode(await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True))
            # A direct, tracked regular target only: no outside paths or chains.
            target=Path(os.path.normpath(str(Path(path).parent/link)))
            if not Path(link).is_absolute() and '..' not in target.parts and regular(target):
                target_entry=await head_entry(target.as_posix())
                if target_entry and target_entry[0] in {'100644','100755'} and regular(target,target_entry[0]) and regular(path,target_entry[0]):
                    hashes=(await process('git','hash-object','--no-filters','--',path,str(target),cwd=root,timeout=10)).splitlines()
                    generated=hashes==[target_entry[1],target_entry[1]]
        (artifacts if generated else protected).append(path)
    return protected,artifacts


class UpdateManager:
    def __init__(self, service):
        self.service = service
        self.home = service.data_dir
        self.directory = self.home / 'updates'
        if os.name == 'nt':
            # Worker generation leases share this parent. A mode-only mkdir
            # does not bind its owner to TokenUser; create privately BEFORE
            # any update or lease payload, and refuse unsafe existing trees.
            from .windows_files import directory
            with directory(self.directory, owned_root=self.home, create=True):
                pass
        else:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = asyncio.Lock()
        self.inventory = []
        from .update_checks import CheckCache
        self.check_cache = CheckCache(self.directory, owned_root=self.home)
        self.check_task = None
        from .shared_settings import SettingsReadCache
        self.settings_cache = SettingsReadCache(limit=512)
        self.task = None
        self.readiness_task = None
        self.closed = False
        self.cleanup_task = None
        self.wakeup = asyncio.Event()
        from .update_readiness import running_identity,valid_target,capture_artifact
        self.running_identity = running_identity()
        state = service.state.setdefault('updates', {})
        self._running_artifact = capture_artifact(self)
        from .app_replacement import pending as replacement_pending
        # An older process may have died after replacing its files but before
        # recording a restart target. Preserve uncertainty, never infer rollback.
        if not replacement_pending(state) and not valid_target(state.get('pendingRestart')) and state.get('phase') == 'activating' and state.get('pendingApp'):
            state['pendingReplacement'] = {'unqualified': True, 'detail': 'An older app replacement was interrupted without complete qualification evidence.'}
        restarted=state.get('pendingRestart')
        restart_repair=restarted is not None and not valid_target(restarted)
        if replacement_pending(state):
            if restart_repair:
                state.update(pendingRestart=None, error='The saved restart receipt was invalid. No restart success was inferred; replacement uncertainty remains fenced.')
            state.update(phase='activating', pendingApp=None,
                         detail='Application replacement may have changed this installation. Work remains paused until a healthy new host proves the qualified app and dependencies. If evidence is incomplete, repair and qualify the installation before reopening admission.')
        elif restart_repair:
            state.update(phase='interrupted',pendingRestart=None,pendingApp=None,
                         error='The saved restart receipt was invalid and has been retired. No restart success was inferred.',
                         detail='The interrupted restart was not acknowledged. Review the diagnostic receipt before retrying updates.')
        elif restarted:
            state.update(phase='activating', pendingApp=None,
                         detail='Checking that the restarted server is serving the installed release…')
        elif state.get('phase') in {'checking','staging','validating','activating'}:
            if state.get('phase')=='activating':state['pendingApp']=None
            state.update(phase='interrupted', detail='The update was interrupted; installed sources were not replayed.')
        state.setdefault('phase', 'idle')
        # A read-only manual verifier interrupted by host exit is not replayed.
        for result in state.get('manualHandoffResults', {}).values():
            if result.get('status') == 'queued':
                result.update(status='interrupted', detail='Manual verification was interrupted; no handoff success was inferred.')
        state['applicationCheckPending'] = False
        from .app_features import reconcile_requests
        reconcile_requests(state)
        state.setdefault('items', [])
        from .app_updates import version_tuple,application_state
        running_application=application_state()
        application={**running_application,**state.get('application',{}),'current':__import__('amplifier_web').__version__,'canInstall':running_application.get('canInstall',True)}
        from .release_notes import history
        application['releaseNotes']=history(application.get('releaseNotes',[]),application.get('latest') if version_tuple(application.get('latest')) else None)
        application['runningRevision'] = self.running_identity['revision']
        state['application']=application
        latest=version_tuple(application.get('latest'))
        current=version_tuple(__import__('amplifier_web').__version__)
        if latest and current and latest<=current and not (latest==current and application.get('componentUpdates')):
            application={**application,'current':__import__('amplifier_web').__version__,'status':'current','releaseBehind':latest<current,
                         'detail':'This installation is newer than the latest published release. Updates follow published releases, not the main branch.' if latest<current else 'The latest published application release is installed.'}
            state.update(application=application,appAvailable=False)
            state['items']=[application if row.get('id')=='application' else row for row in state['items']]
            state['available']=sum(row.get('status')=='update' for row in state['items'])
        state['items']=group_sources(state['items'])
        state['available']=sum(row.get('status')=='update' for row in state['items'])
        state.setdefault('lastCheck', None)
        state['release'] = active_release(self.home).get('current')
        state['canRollback'] = 'previous' in active_release(self.home)
        from .update_diagnostics import UpdateDiagnostics
        self.diagnostics=UpdateDiagnostics(self)
        from .update_health import publish as publish_health
        self._active_update_evidence = {}
        self._update_health_initial = True
        self._update_health_seen_revision = service.state.get('configurationRevision')
        publish_health(self)
        if restart_repair:
            self.diagnostics.record('restart-repair','failed',errorType='ValueError',preserve_last_failure=True)
        else:
            service._save_changes(globals={'updates'})
        from .update_restart import target
        state['forceRestartTarget'] = target(self)

    async def confirm_readiness(self, health, expected=None):
        from .update_readiness import confirm_readiness
        return await confirm_readiness(self, health, expected=expected)

    def awaiting_restart(self):
        from .update_readiness import recovery_candidate
        # A legacy erased-marker receipt must not permanently prevent repairs
        # after an unrelated manual upgrade. Only its active health check gates
        # another update; an actual pending handoff remains gated until verified.
        from .app_replacement import pending
        return bool(pending(self.service.state['updates']) or self.service.state['updates'].get('pendingRestart') or
                    (self.readiness_task and not self.readiness_task.done() and recovery_candidate(self)))

    async def publish(self, **values):
        async with self.service.lock:
            if values.get('error'):
                group = self.diagnostics.state.get('activeFailures', {}).get(
                    getattr(self.diagnostics, '_pending_failure', None))
                if group:
                    group['displayError'] = values['error']
                self.diagnostics._pending_failure = None
            self.service.state['updates'].update(values)
            state = self.service.state['updates']
            from .update_health import publish as publish_health
            publish_health(self)
            state['blockers'] = (self.app_blockers() if state.get('pendingApp')
                                 else self.blockers()) if self.requires_idle(state) else []
            from .update_restart import target
            state['forceRestartTarget'] = target(self)
            self.service._publish_changes(globals={'updates','settings'})

    def configuration_changed(self):
        """Invalidate the current projection on a source-settings publication."""
        revision = self.service.state.get('configurationRevision')
        if getattr(self, '_update_health_seen_revision', revision) == revision:
            self._update_health_seen_revision = revision
            return False
        self._update_health_seen_revision = revision
        self._update_health_initial = True
        from .update_health import publish as publish_health
        publish_health(self)
        self.wakeup.set()
        return True

    def notify_idle(self, *, settled_session_id=None):
        state = self.service.state['updates']
        if self.closed or state.get('phase') not in {'staged', 'app-staged'} or not self.requires_idle(state):
            return
        drain = state.get('drain') or {}
        boundary = drain.get('workers', {}).get(settled_session_id)
        if drain.get('phase') in {'waiting', 'draining'} and boundary:
            # A committed native park/retirement event can invalidate the saved
            # unready observation. Retain it even during an activation attempt;
            # the next owner tick must still obtain positive boundary proof.
            self.wakeup.set()
        elif not self.lock.locked() and not (self.app_busy() if state.get('pendingApp') else self.busy()):
            # Ordinary publications retain the idle-only signal. In particular,
            # the updater's own observations never poll an unready boundary.
            self.wakeup.set()

    @staticmethod
    def requires_idle(state):
        return bool(state.get('pendingApp') or state.get('pendingRelease') or any(
            not item.get('runnerSource') and not item.get('failure') for item in state.get('pendingSmartTools', [])))

    def blockers(self):
        from .update_blockers import blockers
        return blockers(self.service)

    def busy(self):
        return bool(self.blockers())

    def app_busy(self):
        return bool(self.app_blockers())

    def app_blockers(self):
        from .update_blockers import blockers
        return blockers(self.service, application=True)

    async def inventory_sources(self, *, indexed=False):
        """Checks use the app index; an explicit audit also inspects saved scopes."""
        if not indexed:
            return await self._scan_inventory()
        from .update_inventory_index import inventory
        return await inventory(self, lambda: self._scan_inventory(app_only=True))

    async def _scan_inventory(self, *, app_only=False):
        from .update_sequence import app_selection
        selection = await asyncio.to_thread(app_selection, self.home)
        base = foundation_home(self.home)
        # Capture only selection metadata before yielding. The history catalog
        # can change while filesystem/settings reads run outside the event loop.
        state = self.service.state
        snapshot = SimpleNamespace(data_dir=self.home, state={
            'selectedSessionId': state.get('selectedSessionId'),
            'selectedSessionIds': [client.get('selectedSessionId') for client in getattr(self.service.clients, 'records', {}).values()],
            'settings': {key: state['settings'][key] for key in ('workspace', 'bundle')},
            'sessions': [{key: row[key] for key in (
                'id', 'workspace', 'bundle', 'runtimeSessionId', 'nativeIdentity',
                'historyManaged', 'historyReadOnlyReason', 'status') if key in row}
                for row in state['sessions']],
            'workspaces': [{key: row[key] for key in ('nativeProject', 'path', 'available') if key in row}
                           if isinstance(row, Mapping) else row
                           for row in state.get('workspaces', [])],
        })
        snapshot.app_only = app_only
        snapshot.source_issues = []
        snapshot.settings_cache = self.settings_cache
        configured, incomplete = await asyncio.to_thread(configured_sources, snapshot)
        rows = []
        semaphore = asyncio.Semaphore(8)
        async def inspect(meta):
            async with semaphore:
                return await inspect_one(meta)
        async def inspect_one(meta):
            # Each cache may include nested skills copies. All are app-owned;
            # skip symlinked external worktrees and malformed metadata.
            root = meta.parent
            if root.is_symlink() or not root.resolve().is_relative_to(base.resolve()): return None
            try:
                data = json.loads(meta.read_text())
                url, ref = data['git_url'], data.get('ref') or 'HEAD'
                parsed = urlsplit(url)
                if parsed.scheme not in {'https','http','ssh'} or not parsed.hostname: return None
                relative = str(root.relative_to(base))
                identity = hashlib.sha256(relative.encode()).hexdigest()[:20]
                current = await process('git','rev-parse','HEAD',cwd=root,timeout=10)
                evidence = sorted(configured.get(source_key(url, ref), ()))
                dirty, _ = await cache_changes(root)
                return {'id':identity,'label':safe_label(url),'ref':ref,'current':current,
                    'status':'local_changes' if dirty else 'pinned' if pinned(ref) else 'not_checked',
                    'usage':'configured' if evidence else 'unknown','usageEvidence':evidence,
                    **({'detail':'Tracked source changes are preserved and block automatic updates. Builds can also modify tracked files (including version stamps); without verified provenance these changes are not discarded.'} if dirty else {}),
                    'path':relative,'url':url,'kind':'bundle / module','eligible':not dirty and not pinned(ref)}
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError):
                return {'id':hashlib.sha256(str(root).encode()).hexdigest()[:20],
                    'label':'Unrecognized cached source','status':'check_failed','eligible':False,'kind':'source',
                    'usage':'unknown','usageEvidence':[]}
        metadata = await asyncio.to_thread(lambda: sorted((base/'cache').rglob('.amplifier_cache_meta.json')))
        rows = [row for row in await asyncio.gather(*(inspect(meta) for meta in metadata)) if row]
        active_issues = [issue for issue in snapshot.source_issues if not issue.get('historical')]
        historical_issues = [issue for issue in snapshot.source_issues if issue.get('historical')]
        if incomplete and (active_issues or not snapshot.source_issues):
            rows.append({'id':'source-configuration','label':'Source configuration','status':'check_failed',
                'eligible':False,'kind':'configuration',
                'detail':'Some saved bundle selections or settings could not be resolved. This affects usage labels, not cached-source update checks. Review the affected sources below.',
                'sourceIssues': active_issues})
        if historical_issues:
            rows.append({'id': 'historical-source-configuration', 'label': 'Older conversation settings', 'status': 'historical',
                'eligible': False, 'kind': 'history', 'sourceIssues': historical_issues,
                'detail': 'These older conversations use bundles that are no longer registered. Their history is kept. Choose an available bundle if you resume one; cached-source updates are unaffected.'})
        from .update_sources import bindings
        rows.extend(await bindings(self.home, base, configured))
        from .runtime_environment import update_inventory
        rows.extend(await update_inventory(self.home))
        from .update_sequence import classify
        smart = getattr(self.service, 'smart_tools', None)
        if smart: rows.extend(smart.update_sources())
        if (await asyncio.to_thread(app_selection, self.home)) != selection:
            raise ValueError('App behavior selection changed during inventory')
        return classify(self.home, rows, selection=selection)

    async def command(self, action, args=None, command_id=None):
        if getattr(self, '_manual_handoff_commit_unknown', False):
            return  # Saved commit is unreadable; reload observes it, never replay.
        if action == 'reconcileManualHandoff':
            from .manual_app_handoff import reconcile
            return await reconcile(self, args, command_id)
        if self.awaiting_restart() and action not in {'featureInstall', 'forceRestart'}:return
        previous_error=self.service.state['updates'].get('error')
        previous_event=self.diagnostics.state.get('latest',{}).get('id')
        try:
            if action == 'forceRestart':
                from .app_updates import activate
                await activate(self, force=args)
            elif action == 'featureInstall':
                await self.featureInstall(args['feature'], args['hostInstanceId'], command_id)
            elif action in {'smartToolRollback', 'smartToolRetry', 'smartToolSkip'}:
                await getattr(self, action)(args['id'])
            elif action == 'app':
                if self.check_task and not self.check_task.done():
                    await asyncio.shield(self.check_task)
                async with self.lock:
                    if self.closed or self.awaiting_restart():return
                    sequence = {**self.service.state['updates'].get('sequence', {}),
                                'stage': 'application', 'install': False}
                    sequence.pop('nextStage', None)
                    intent = {'id': command_id or uuid.uuid4().hex, 'scope': 'app'}
                    pending = self.service.state['updates'].get('pendingApp')
                    await self.publish(applicationIntent=intent, sequence=sequence,
                        **({'pendingApp': {**pending, 'updateIntent': intent}} if pending else {}))
                await self.app()
            elif action == 'install':
                # A click during a check must not disappear at install()'s
                # busy guard. Join that check, then re-evaluate its inventory.
                if self.check_task and not self.check_task.done():
                    await asyncio.shield(self.check_task)
                    if self.service.state['updates'].get('phase') in {'error', 'interrupted'}:
                        return
                async with self.lock:
                    if self.closed or self.awaiting_restart():return
                    intent = {'id': command_id or uuid.uuid4().hex, 'scope': 'whole'}
                    pending = self.service.state['updates'].get('pendingApp')
                    await self.publish(applicationIntent=intent,
                        **({'pendingApp': {**pending, 'updateIntent': intent}} if pending else {}))
                if not self.closed and not self.awaiting_restart():
                    await self.install()
            else:
                await getattr(self, action)()
        except asyncio.CancelledError: raise
        except Exception as error:
            from .update_restart import RestartRejected
            if action == 'forceRestart' and isinstance(error, RestartRejected):
                await self.publish(forceRestartResult={'requestId': command_id, 'status': 'rejected', 'detail': str(error)})
                return
            # Feature requests own a durable per-request result. A rejected
            # concurrent request must not change an active update's work gate.
            if action == 'featureInstall':return
            if self.service.state['updates'].get('phase')=='error' and self.service.state['updates'].get('error') and self.service.state['updates']['error']!=previous_error:return
            from .update_diagnostics import exception_facts, failure_guidance
            last=self.diagnostics.state.get('latest',{})
            phase=last.get('phase',action)
            if last.get('status')!='failed' or last.get('id')==previous_event:
                self.diagnostics.record(phase,'failed',**exception_facts(error))
            await self.publish(phase='activating' if self.service.state['updates'].get('pendingRestart') else 'error',error=failure_guidance(self.diagnostics.state.get('lastFailure', {})) or 'Update failed during '+phase.replace('-',' ')+'. Review the diagnostic receipt; no conversation work was replayed.')
        finally:
            # Manual actions used to leave ready work behind a 60-second
            # polling sleep. Wake the owner; keep all admission/idle checks in
            # tick(), rather than installing in parallel with another update.
            self.wakeup.set()

    @staticmethod
    def independent_tool_results(state):
        pending = state.get('pendingSmartTools') or []
        # Failed attempts retain their current installation and explicit
        # retry/skip receipt; they are not unfinished activation work.
        return bool(pending) and all(row.get('runnerSource') or row.get('failure') for row in pending)

    async def check(self, *, tier='application', install=False, fresh=True, automatic=False):
        if getattr(self, '_manual_handoff_commit_unknown', False):return
        state = self.service.state['updates']
        if (self.closed or self.awaiting_restart() or state.get('pendingRelease') or (state.get('pendingSmartTools') and not self.independent_tool_results(state))
                or (state.get('pendingApp') or {}).get('featureSelection')):
            return  # A timing measurement must not change a pending handoff.
        if self.check_task and not self.check_task.done():
            # Join only an equivalent availability operation. A manual refresh
            # must not inherit a background TTL result, tier or install action.
            previous = self.check_task
            equivalent = getattr(self, 'check_request', None) == (tier, install, fresh, automatic)
            result = await asyncio.shield(previous)
            if equivalent:
                return result
            return await self.check(tier=tier, install=install, fresh=fresh, automatic=automatic)
        async def run():
            from .update_checks import checking
            ttl = self.service.state['settings']['updates'].get('intervalHours', DEFAULT_CHECK_INTERVAL_HOURS) * 3600
            started = time.monotonic()
            started_at = time.time()
            async with checking(self.check_cache, fresh=fresh, ttl=ttl):
                await self._check(tier=tier, install=install, fresh=fresh, automatic=automatic)
            timing = {'startedAt': started_at, 'finishedAt': time.time(),
                      'elapsedMs': round((time.monotonic()-started)*1000), **self.check_cache.stats}
            await self.publish(checkTiming=timing,
                               recentChecks=[*self.service.state['updates'].get('recentChecks', [])[-4:], timing])
        self.check_request = (tier, install, fresh, automatic)
        self.check_task = asyncio.create_task(run())
        return await asyncio.shield(self.check_task)

    async def _check(self, *, tier='application', install=False, fresh=True, automatic=False):
        from .update_sequence import summary
        from .update_health import pending_preparation
        async with self.lock:
            state = self.service.state['updates']
            # A capacity observation can own this lock when a refresh arrives.
            # Queue that refresh, then recheck admission against current state.
            if (self.closed or self.awaiting_restart() or state.get('pendingRelease')
                    or (state.get('pendingApp') or {}).get('featureSelection')
                    or (state.get('pendingSmartTools') and not self.independent_tool_results(state))):
                return
            if self.independent_tool_results(state):
                # Retained runner qualification and failed optional tools own
                # their rows independently of the new sequence/install intent.
                await self.publish(runnerAvailabilityCheck=True)
            if state.get('pendingApp'):
                if not state['pendingApp'].get('featureSelection'):
                    from .app_updates import check_staged
                    await check_staged(self)
                return
            from .manual_app_handoff import continuing, verify_continuation
            manual = continuing(self)
            if manual and tier == 'application' and automatic:
                tier = state['sequence']['stage']
                install = state['sequence'].get('install') is True
            if manual and tier != 'application':
                await verify_continuation(self)
            else:
                manual = False  # A fresh explicit check starts the usual app-first cycle.
            sequence = ({'stage': 'application', 'install': install,
                         'included': summary([], checked=False), 'other': summary([], checked=False)}
                        if tier == 'application' else {**state.get('sequence', {}), 'stage': tier, 'install': install})
            sequence.pop('nextStage', None)
            await self.publish(phase='checking', sequence=sequence, applicationIntent=None,
                               lastAttempt=time.time(),
                               detail='Checking the application release…' if tier == 'application' else 'Checking included components…' if tier == 'included' else 'Checking other components…', error=None)
            try:
                application = state.get('application', {})
                if tier == 'application':
                    from .app_updates import check as check_application
                    application = await check_application()
                    previous=self.service.state['updates'].get('application',{})
                    if application.get('status')=='check_failed':
                        from .release_notes import history
                        from .app_updates import version_tuple
                        application['releaseNotes']=history(previous.get('releaseNotes',[]),previous.get('latest') if version_tuple(previous.get('latest')) else None)
                        application['releaseNotesWarning']='The update check failed. Showing saved release notes; check again to confirm the latest release.'
                    elif application.get('releaseNotesWarning') and application.get('revision')==previous.get('revision'):
                        from .release_notes import history
                        application['releaseNotes']=history(previous.get('releaseNotes',[]),application.get('latest'))
                        application['releaseNotesWarning']='Release notes could not be refreshed. Showing saved release history.'
                    application['runningRevision'] = self.running_identity['revision']
                    app_available = application.get('status') == 'update'
                    # A selected app replacement still owns the next activation.
                    # An unavailable release check does not select a replacement:
                    # independently check the running app's component declarations.
                    self.inventory = []
                    write_private(self.directory/'inventory.json', '[]')
                    await self.publish(application=application, appAvailable=app_available,
                                       items=[application], available=int(app_available))
                    if app_available or application.get('status') not in {'current', 'check_failed', 'release_channel_needed'}:
                        await self.publish(phase='available' if app_available else 'error', lastCheck=time.time(),
                            error=None if app_available else application.get('detail') or 'The application check returned an unrecognized outcome. Check again before continuing.',
                            detail='App update available. Included components will be checked after the new app restarts.' if app_available else 'Component checks are waiting for the application check.')
                        return
                    tier = 'included'
                if pending_preparation(self.diagnostics.state):
                    await self.publish(phase='checked', application=application,
                        detail='Component checks are paused until the interrupted preparation is reconciled.')
                    return
                started = time.monotonic()
                rows = await self.inventory_sources(indexed=True)
                # Retained runner attempts already own these selections. Do not
                # rediscover them as installable work or stage a duplicate while
                # checking the app and its remaining component tiers.
                from .managed_runner_backend import skip_key
                retained = {skip_key(row['runnerSource']) for row in state.get('pendingSmartTools', [])
                            if row.get('runnerSource')}
                failed = {row['previous'] for row in state.get('pendingSmartTools', [])
                          if row.get('failure') and not row.get('runnerSource')}
                rows = [row for row in rows if not (row.get('runnerKind') and skip_key(row) in retained)
                        and not (row.get('kind') == 'smart tool' and row.get('installationId') in failed)]
                await self.publish(inventoryTimingMs=round((time.monotonic()-started)*1000), inventoryEvidence=getattr(self, 'inventory_evidence', None))
                remote_tasks = {}
                from .update_checks import git_revision, ref_policy
                async def remote(url, ref, prove_kind):
                    return await ref_policy(url, ref, process) if prove_kind else await git_revision(url, ref, process)
                async def check_row(row):
                    from .managed_runner_backend import backend, skip_key
                    if row.get('runnerKind'):
                        inspector = getattr(backend(row['runnerKind']), 'check_source', None)
                        if inspector is not None:
                            try:
                                await inspector(self.service, row)
                            except (ValueError, RuntimeError, TimeoutError):
                                row.update(status='check_failed', eligible=False)
                                return
                    if not row.get('eligible') or row.get('kind') == 'runtime environment': return
                    if row.get('registryManaged'):
                        from .runtime_registry_updates import check as check_registry
                        try:
                            await check_registry(row)
                        except (ValueError, RuntimeError, TimeoutError):
                            row['status'] = 'check_failed'
                        return
                    origin = row.get('policyOrigin') or {}
                    typed_policy = (origin.get('kind') in {'prepared-manifest', 'selected-declaration',
                                                          'verified-cache-branch', 'checked-source'}
                                    and all(origin.get(key, '') == row.get(key, '')
                                            for key in ('url', 'ref', 'subdirectory'))
                                    and origin.get('revision') == row.get('current'))
                    # PEP 610 requested_revision has no ref kind. Legacy/raw
                    # installed Git rows need the same proof as cache URIs;
                    # retain an exact already-qualified authored policy.
                    prove_kind = bool(row.get('cacheManaged') or row.get('sharedSource')
                        or row.get('kind') in {'bundle / module', 'included source'}
                        or row.get('kind') == 'runtime dependency' and not typed_policy)
                    key = (row['url'], row['ref'], prove_kind)
                    task = remote_tasks.setdefault(key, None)
                    if task is None:
                        task = remote_tasks[key] = asyncio.create_task(remote(*key))
                    try:
                        result = await task
                        if prove_kind:
                            if result['kind'] == 'unresolved':
                                row.update(status='check_failed', detail='This source reference could not be verified. Its installed version is preserved.')
                                row.pop('latest', None)
                                return
                            if result['kind'] != 'branch':
                                row.update(status='pinned', eligible=False,
                                    detail='This source reference is not an unambiguous branch. Its installed version is preserved.')
                                row.pop('latest', None)
                                return
                            result = result['revision']
                        row['latest'] = result
                        row['status'] = 'update' if row['latest'] != row.get('current') else 'current'
                        if (row.get('kind') == 'smart tool' and row['status'] == 'update'
                                and state.get('skippedSmartTools', {}).get(skip_key(row)) == row['latest']):
                            row.update(status='skipped', detail='This Smart Tool revision was skipped. A newer revision can still be offered.')
                    except (ValueError, RuntimeError, TimeoutError) as error:
                        row['status'] = 'check_failed'
                        from .update_diagnostics import CommandFailure
                        facts = getattr(error, 'diagnostic_facts', {})
                        row['availabilityFailure'] = bool(isinstance(error, CommandFailure)
                            and type(facts.get('exitCode')) is int and facts['exitCode'] > 0
                            and not facts.get('timedOut'))
                checked = []
                for stage in (['included', 'other'] if tier == 'included' else ['other']):
                    selected = [row for row in rows if row.get('updateTier', 'other') == stage]
                    sequence['stage'] = stage
                    await self.publish(sequence={**sequence}, detail='Checking included components…' if stage == 'included' else 'Checking other components…')
                    await asyncio.gather(*(check_row(row) for row in selected))
                    from .update_partial import retained_after_check, availability
                    await asyncio.to_thread(retained_after_check, self, selected, retry_retained=not automatic and fresh)
                    checked.extend(selected)
                    sequence.update(stage=stage)
                    sequence[stage] = summary(group_sources(selected))
                    protected = any(row.get('status') in {'check_failed', 'local_changes'}
                                    and not availability(row) for row in selected)
                    if sequence[stage]['available'] or (stage == 'included' and protected):break
                from .update_sequence import verify_app_selection
                await asyncio.to_thread(verify_app_selection, self, checked)
                self.inventory = checked
                write_private(self.directory/'inventory.json',json.dumps(checked))
                public = group_sources(checked)
                available = sum(row.get('status') == 'update' for row in public)
                from .update_partial import availability
                blocked = sequence['stage'] == 'included' and any(
                    row.get('status') in {'check_failed', 'local_changes'} and not availability(row)
                    for row in checked if row.get('updateTier') == 'included')
                failed_check = any(row.get('status') == 'check_failed' for row in checked)
                if not available and not blocked:sequence['stage'] = 'complete';sequence['install'] = False
                from .update_health import refresh as refresh_health
                await refresh_health(self)
                await self.publish(phase='error' if blocked and failed_check else 'checked' if blocked else 'available' if available else 'checked',
                    sequence=sequence, items=public+[application], application=application,
                    appAvailable=state.get('appAvailable', False) if manual else False,
                    includedCanProceed=bool(available and not blocked),
                    available=available + int(manual and state.get('appAvailable', False)), lastCheck=time.time(),
                    error='An included component could not be checked. Review its details before continuing.' if blocked and failed_check else None,
                    detail='Included components will be installed before checking the remaining sources.' if sequence['stage']=='included' and available else 'Component check complete.')
            except Exception:
                await self.publish(phase='error', error='Update check failed. Your installed sources are unchanged.')

    async def featureInstall(self, feature, hostInstanceId, request_id):
        from .app_features import FEATURES, record
        from .app_updates import stage, activate, installed_extras, prepare_browser
        request_id = request_id or uuid.uuid4().hex
        try:
            if hostInstanceId != self.service.instance_id:
                raise ValueError('The app host changed. Check setup again before installing.')
            if not isinstance(feature, str) or feature not in FEATURES:
                raise ValueError('Choose a supported optional feature.')
            state = self.service.state['updates']
            if (self.closed or self.lock.locked() or self.awaiting_restart()
                    or state.get('pendingApp') or state.get('pendingRelease') or state.get('pendingSmartTools')):
                raise ValueError('Another update is pending. Finish or inspect that update before adding a feature.')
            async with self.lock:
                if feature in installed_extras():
                    browser_receipt = None
                    if feature == 'managed-browser':
                        import sys
                        self.diagnostics.begin('application', self.running_identity.get('revision'))
                        await record(self, request_id, 'staging', feature=feature,
                            detail='Preparing the browser binary in this service’s own cache.')
                        browser_receipt = await prepare_browser(self, sys.executable, [feature])
                    await record(self, request_id, 'already_installed', feature=feature,
                        **({'managedBrowser':browser_receipt} if browser_receipt else {}),
                        detail='Browser launch verified using '+browser_receipt['selection']+' executable; refresh browser readiness.' if browser_receipt
                            else 'The optional package is already installed. Decision support also requires current conversation components.')
                    return
                await record(self, request_id, 'staging', feature=feature,
                    detail='Qualifying '+FEATURES[feature]+' against this same app and its existing components.')
                await stage(self, feature=feature, request_id=request_id)
            await activate(self)
        except asyncio.CancelledError:
            await record(self, request_id, 'interrupted', feature=feature, detail='The request was interrupted. Inspect update diagnostics before any retry.')
            raise
        except Exception as error:
            await record(self, request_id, 'error', feature=feature, detail=str(error) if isinstance(error, ValueError) else 'The feature request did not finish. Inspect update diagnostics before retrying; nothing was replayed.')
            raise

    async def app(self):
        if self.lock.locked() or self.awaiting_restart():return
        from .app_updates import stage,activate,supersedes_staged
        state = self.service.state['updates']
        pending = state.get('pendingApp')
        if (pending and not pending.get('featureSelection') and not self.app_busy()
                and time.time() - (state.get('pendingAppCheckAt') or 0) >= 60):
            # The idle window may arrive between scheduled checks. Refresh once
            # before activation; a recent check also avoids duplicate requests.
            await self.check(fresh=True)
        async with self.lock:
            if self.closed or self.awaiting_restart():return
            state = self.service.state['updates']
            pending = state.get('pendingApp')
            if not pending or supersedes_staged(state.get('application', {}), pending):
                await stage(self)
        await activate(self)

    async def install(self, *, automatic=False):
        if getattr(self, '_manual_handoff_commit_unknown', False):return
        if self.awaiting_restart() or self.lock.locked():return
        state = self.service.state['updates']
        from .manual_app_handoff import verify_continuation
        manual = await verify_continuation(self)
        if manual and automatic and state.get('sequence', {}).get('install') is not True:
            return  # Verification without saved Install intent installs nothing.
        from .update_health import pending_preparation, reconcile_preparation
        # Independent app/tool owners may proceed without clearing or replaying
        # component work. Their diagnostics.begin must retain this operation.
        if pending_preparation(self.diagnostics.state):
            independent = state.get('pendingApp') or state.get('appAvailable') or state.get('pendingSmartTools')
            if not independent:
                if automatic:
                    return
                async with self.lock:
                    if not reconcile_preparation(self):
                        raise ValueError('The interrupted update has no proof of settled candidate-only preparation. Inspect its diagnostics before another installation; its outcome is not replayed.')
        from .update_partial import availability
        if (state.get('sequence', {}).get('stage') == 'included'
                and state['sequence'].get('included', {}).get('issues')
                and (not self.inventory or any(row.get('status') in {'check_failed', 'local_changes'}
                    and not availability(row) for row in self.inventory if row.get('updateTier') == 'included'))):
            await self.publish(detail='Resolve the included component issues and check again before installing.')
            return
        if state.get('sequence'):
            await self.publish(sequence={**state['sequence'], 'install': True})
        if state.get('pendingSmartTools') and not self.independent_tool_results(state):
            await self.activateSmartTools()
            return
        if self.independent_tool_results(state) and any(state.get(key) for key in ('pendingApp', 'pendingRelease', 'appAvailable')):
            await self.publish(runnerAvailabilityCheck=True)
        if self.service.state['updates'].get('pendingApp'):
            await self.app()
            return
        if self.service.state['updates'].get('pendingRelease'):
            await self.activate()
            return
        if self.service.state['updates'].get('appAvailable') and not manual:
            await self.app()
            return
        pending = state.get('pendingSmartTools', [])
        if pending and all(item.get('failure') for item in pending) and not state.get('runnerAvailabilityCheck'):
            # Preserve explicit retry of the failed batch, but never inherit
            # that retry into a subsequent application/component sequence.
            for item in pending:
                await self.smartToolRetry(item.get('resultId', item['previous']))
            return
        if self.lock.locked(): return
        if not self.inventory:
            path=self.directory/'inventory.json'
            self.inventory=json.loads(path.read_text()) if path.exists() else []
        if self.independent_tool_results(state) and not any(
                row.get('status') == 'update' and row.get('eligible') for row in self.inventory):
            if any(not item.get('failure') for item in state['pendingSmartTools']):
                await self.activateSmartTools()
            return
        if self.independent_tool_results(state):
            await self.publish(runnerAvailabilityCheck=True)
        if any(row.get('kind')=='smart tool' and row.get('status')=='update' and row.get('eligible') for row in self.inventory) and not any(row.get('kind')!='smart tool' and row.get('status')=='update' and row.get('eligible') for row in self.inventory):
            await self.installSmartTools()
            return
        async with self.lock:
            if manual:
                if not await verify_continuation(self, full=True):
                    raise ValueError('The manual handoff continuation was superseded.')
            if not self.inventory:
                path=self.directory/'inventory.json'
                self.inventory=json.loads(path.read_text()) if path.exists() else []
            candidates=[r for r in self.inventory if r.get('status')=='update' and r.get('eligible') and r.get('kind')!='smart tool']
            if pending_preparation(self.diagnostics.state):
                if automatic:return
                if not reconcile_preparation(self):
                    raise ValueError('The interrupted component preparation must be reconciled before another installation.')
            if not candidates:
                await self.publish(detail='Check for updates before installing. No eligible updates are available.')
                return
            from .update_sequence import verify_app_selection
            await asyncio.to_thread(verify_app_selection, self, self.inventory)
            from .update_health import preparation_key, retry_decision, environment_repaired
            input_key = preparation_key(self, self.inventory)
            prior_retry = self.diagnostics.state.get('batch', {}).get('retry') or {}
            batch = self.diagnostics.state.get('batch', {})
            decision = retry_decision(batch, input_key)
            if automatic and decision and not (decision == 'changed-inputs' and environment_repaired(self, batch)):
                await self.publish()
                return  # No candidate directory, copy, resolution or installation.
            release=uuid.uuid4().hex
            self.diagnostics.begin('ecosystem',release,tier=state.get('sequence',{}).get('stage'),
                                   components=[row.get('package') or row.get('id') if not row.get('url') else
                                               safe_label(row['url']).rsplit('/',1)[-1] for row in candidates])
            self.diagnostics.state['batch']['inputKey'] = input_key
            stage_id=uuid.uuid4().hex
            self.diagnostics.record('ecosystem-stage','started',commandId=stage_id)
            stage=self.directory/'releases'/release
            stage.mkdir(parents=True,mode=0o700)
            from .update_health import pointer_witness, candidate_copy
            proof = {'candidate': release, 'activeBefore': pointer_witness(self),
                     'directoryIdentity': [stage.stat().st_dev, stage.stat().st_ino],
                     'boundary': 'copy-only', 'copySettled': True}
            self.diagnostics.state['batch']['preparation'] = proof
            source=foundation_home(self.home)
            phase='ecosystem-stage'
            partial = None
            try:
                await self.publish(phase='staging',detail='Preparing an isolated copy of the ecosystem…',error=None)
                if source.exists():
                    from .update_storage import copy_snapshot
                    await self.diagnostics.run('ecosystem-copy',candidate_copy,proof,copy_snapshot,source,stage/'foundation')
                else:
                    (stage/'foundation').mkdir()
                for name in ('config','routing'):
                    if (self.home/name).exists(): await candidate_copy(proof,shutil.copytree,self.home/name,stage/name)
                from .session_files import amplifier_home
                shared_home=amplifier_home()
                shared_stage=stage/'shared-config'
                shared_stage.mkdir(mode=0o700)
                for name in ('settings.yaml','keys.env','routing'):
                    original=shared_home/name
                    if original.is_dir():await candidate_copy(proof,shutil.copytree,original,shared_stage/name)
                    elif original.is_file():await candidate_copy(proof,shutil.copy2,original,shared_stage/name)
                # Beyond plain snapshots, preparation can invoke shared source
                # stores, installers and native hooks. Do not infer their effects
                # or settlement from a CancelledError.
                await asyncio.to_thread(verify_app_selection, self, self.inventory)
                proof['boundary'] = 'preparing'
                for config_file in (stage/'config').rglob('*.yaml'):
                    config_file.write_text(config_file.read_text().replace(str(source),str(stage/'foundation')))
                shared_settings = shared_stage/'settings.yaml'
                if shared_settings.exists():
                    write_private(shared_settings, shared_settings.read_text().replace(str(source),str(stage/'foundation'))
                                  .replace(str(self.home/'foundation'),str(stage/'foundation')))
                registry=stage/'foundation/registry.json'
                if registry.exists(): registry.write_text(registry.read_text().replace(str(source),str(stage/'foundation')))
                await asyncio.to_thread(verify_app_selection, self, self.inventory, stage=stage)
                # Snapshot shared configuration, never CLI registry or caches.
                # Candidate preparation resolves declarations into its own store.
                from .update_partial import stage_sources, read as partial_read, public_results
                partial_plan = await stage_sources(self, stage, candidates)
                partial = partial_read(stage)
                if partial and not any(row['status'] == 'awaiting-adoption' for row in partial['results'].values()):
                    raise ValueError('No independent source update remains to qualify.')
                await asyncio.to_thread(verify_app_selection, self, self.inventory, stage=stage)
                await self.publish(phase='validating',detail='Preparing dependencies in a separate runtime…')
                phase='ecosystem-validation'
                from .update_plan import build, reuse_runtime
                plan = await build(self, stage, candidates)
                write_private(stage/'plan.json', json.dumps(plan))
                await self.publish(plan={key: plan[key] for key in ('mode', 'reason', 'activation')})
                if plan['mode'] == 'data':
                    await self.diagnostics.run('ecosystem-runtime-reuse', reuse_runtime, self, stage, plan)
                else:
                    await self.validate(stage,release)
                await partial_plan.recheck()
                await asyncio.to_thread(verify_app_selection, self, self.inventory, stage=stage)
                write_private(stage/'validated.json',json.dumps({'generationSchema': 1, 'workerProtocol': 1, 'createdAt':time.time(),'sources':len(candidates),'hostVersion':__import__('amplifier_web').__version__, 'updateTier': self.service.state['updates'].get('sequence', {}).get('stage')}))
                self.diagnostics.clear_failure()
                self.diagnostics.record('ecosystem-stage','succeeded',commandId=stage_id)
                await self.publish(phase='staged',pendingRelease=release,
                    componentResults=public_results(partial),
                    detail='Update prepared; making it available to new conversation work.')
            except BaseException as error:
                from .update_diagnostics import exception_facts, failure_guidance
                interrupted=isinstance(error,asyncio.CancelledError)
                if interrupted:proof['interruptedAt'] = time.time()
                status='interrupted' if interrupted else 'failed'
                failure=self.diagnostics.state.get('lastFailure',{})
                if failure.get('attemptId')!=self.diagnostics.state.get('attemptId'):
                    self.diagnostics.record(phase,status,**{**getattr(error,'diagnostic_facts',{}),**exception_facts(error)})
                failure=self.diagnostics.state['lastFailure']
                from .update_health import record_retry
                record_retry(self.diagnostics.state['batch'], input_key, failure, prior_retry)
                if interrupted:
                    # Display may preserve a prior handled fetch failure. Actual
                    # cancellation, not that historical display choice, owns the
                    # unknown-operation fence and always wins over changed inputs.
                    self.diagnostics.state['batch']['retry']['mode'] = 'inspect'
                elif partial:
                    # A mixed candidate already contains a known source hold.
                    # Do not repeat its expensive qualification on unchanged
                    # automatic ticks. Uncertain effects retain inspect instead.
                    self.diagnostics.state['batch']['retry']['mode'] = 'changed-inputs'
                self.diagnostics.record('ecosystem-stage',status,commandId=stage_id,preserve_last_failure=True)
                from .update_health import retain_preparation
                retain_preparation(self.diagnostics.state)
                await self.publish(phase='interrupted' if interrupted else 'error',pendingRelease=None,
                    detail='Component update preparation '+('was interrupted' if interrupted else 'failed')+'. Current components remain active.',
                    error=failure_guidance(failure) or 'Ecosystem update '+('interrupted' if interrupted else 'failed')+' during '+failure['phase'].replace('-',' ')+'. Current sources remain active; no conversation work was replayed.')
                if not isinstance(error,Exception):raise
                return
        await self.activate()

    async def stage_cache_sources(self, stage, rows):
        # Each checkout belongs to the inactive candidate. Independent Git
        # downloads may overlap, but writes to the same checkout serialize.
        # Dependency resolution, qualification and promotion still follow them.
        semaphore = asyncio.Semaphore(4)
        targets = {}
        async def stage_one(row):
            target = (stage / 'foundation' / row['path']).resolve()
            lock = targets.setdefault(target, asyncio.Lock())
            async with semaphore, lock:
                try:
                    await prepare(row)
                except Exception as error:
                    # Policy/identity checks between subprocesses also need a
                    # primary receipt before sibling cancellation is recorded.
                    failure = self.diagnostics.state.get('lastFailure', {})
                    if failure.get('attemptId') != self.diagnostics.state.get('attemptId'):
                        from .update_diagnostics import exception_facts
                        self.diagnostics.record('ecosystem-source-preflight', 'failed', **exception_facts(error))
                    raise
        async def prepare(row):
            from .staged_sources import owned_git_checkout, owned_path, require_exclusive_registry_owner
            target=stage/'foundation'/row['path']
            async def guard():
                await owned_git_checkout(target, stage/'foundation')
                require_exclusive_registry_owner(stage/'foundation/registry.json', row['url'], row['ref'], target)
            await guard()
            phase='ecosystem-source-preflight'
            current=await self.diagnostics.run(phase,process,'git','rev-parse','HEAD',cwd=target)
            dirty,artifacts=await self.diagnostics.run(phase,cache_changes,target)
            if dirty or current!=row['current']: raise ValueError('Source changed since check')
            options = await cache_checkout_options(target)
            meta=owned_path(target/'.amplifier_cache_meta.json', stage/'foundation')
            data=json.loads(meta.read_text())
            if source_key(data['git_url'], data.get('ref') or 'HEAD') != source_key(row['url'], row['ref']):
                raise ValueError('Source identity changed since check')
            if artifacts:
                # Restore only verified tracked artifacts in this copy.
                # Rechecking here also protects edits made after check.
                await guard()
                await self.diagnostics.run(phase,process,'git',*options,'--literal-pathspecs','-c','core.hooksPath=/dev/null','restore','--source=HEAD','--worktree','--',*artifacts,cwd=target)
            await self.publish(detail='Downloading '+row['label']+'…')
            await guard()
            try:
                await self.diagnostics.run('ecosystem-fetch',process,'git','-c','core.hooksPath=/dev/null','fetch','--depth=1',row['url'],row['latest'],cwd=target)
            except Exception as error:
                # Only this exact pre-checkout boundary may be classified as a
                # settled source acquisition failure by partial preparation.
                error.source_fetch_failed = True
                raise
            await guard()
            await self.diagnostics.run('ecosystem-checkout',process,'git',*options,'-c','core.hooksPath=/dev/null','checkout','--detach',row['latest'],cwd=target)
            data.update(commit=row['latest'],cached_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
            owned_path(meta, stage/'foundation').write_text(json.dumps(data))
        tasks = [asyncio.create_task(stage_one(row)) for row in rows]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def validate(self,stage,release):
        from .runtime_environment import stage as stage_runtime, receipt_directory
        from .runtime_qualification import freeze, prepare_overrides, verify_recorded
        receipt=receipt_directory(self.home,release)
        fresh=not (receipt/'runtime.lock').exists()
        project=await stage_runtime(self, release, [row for row in self.inventory if row.get('eligible') and (row.get('status') == 'update' or
            (row.get('kind') == 'runtime dependency' and row.get('status') == 'current'))], finalize=not fresh)
        # Profile preparation follows the current app offering. Saved chats and
        # workspace-specific overlays qualify lazily when explicitly resumed.
        # Scanning historical workspace/bundle pairs made a catalog-only update
        # run dozens of identical dependency installers. Do not restore that loop.
        from .bundles import offered_profiles, profile_candidates
        from .host.config import read_config
        workspace = stage / 'qualification-workspace'
        workspace.mkdir(exist_ok=True)
        config = read_config(workspace, home=stage, shared_home=stage/'shared-config', global_only=True)
        profiles = profile_candidates(config) if fresh else offered_profiles(config)
        if not profiles:
            raise ValueError('No app-level conversation profiles are configured')
        configs = [(str(workspace), profile) for profile in profiles]
        profiles_file = stage / 'profiles.json'
        write_private(profiles_file, json.dumps(profiles))
        from .update_sources import store_environment, worker_supports_shared, adopt_clean_sources
        if fresh:
            # Materialize the candidate lock once, rather than uv-syncing before
            # every workspace probe. The serving environment is never targeted.
            await self.diagnostics.run('ecosystem-runtime-prepare', process, shutil.which('uv'), 'sync', '--locked',
                '--project', str(project), '--python', '3.13', timeout=900)
        shared = store_environment(self.home) if worker_supports_shared(project) else {}
        from .host.config import worker_environment
        env={**worker_environment(),**shared,'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_HOME':str(stage/'shared-config'),'AMPLIFIER_UNIFIED_RELEASE':'','AMPLIFIER_INSTALL_PREPARATION':uuid.uuid4().hex,
             'AMPLIFIER_UPDATE_ATTEMPT':self.diagnostics.state.get('attemptId', '')}
        if not shared:
            env.pop('AMPLIFIER_SOURCE_STORE', None)  # Preserve pinned/older workers' legacy source contract.
        async def probe(project, *, refresh=False):
            qualified=fresh or (receipt/'runtime-installed.json').exists()
            overrides=await self.diagnostics.run('ecosystem-runtime-policy',prepare_overrides,project,receipt/'runtime-install-overrides.txt',owned_root=self.home) if qualified else Path(__file__).parent/'runtime_deps/compatibility.txt'
            command=[shutil.which('uv'),'run','--locked','--no-sync','--project',str(project),'--python','3.13','python',str(Path(__file__).with_name('update_probe.py'))]
            flags=['--install-overrides',str(overrides)] if qualified else []
            probe_env = dict(env)
            if not qualified:
                # Historical workers lack the explicit installer-policy argument.
                probe_env['UV_OVERRIDE'] = str(overrides)
            flags.append('--global-only')
            if refresh:
                # Verify this actual collected batch before its one installation.
                await self.diagnostics.run('ecosystem-prepare', process, *command, str(workspace), profiles[0],
                    *flags, '--profiles', str(profiles_file),
                    '--checked-sources', str(receipt/'runtime-sources.json'), env=probe_env, timeout=900)
                return
            completed=0
            progress={'attemptId':getattr(self.diagnostics,'state',{}).get('attemptId'),
                      'phase':'prepare' if refresh else 'compatibility','total':len(configs),
                      'completed':0,'startedAt':time.time()}
            await self.publish(probeProgress=progress)
            async def run_one(workspace, bundle):
                nonlocal completed
                async with semaphore:
                    await self.diagnostics.run('ecosystem-prepare' if refresh else 'ecosystem-compatibility',process,*command,workspace,bundle,*flags,
                        env=probe_env,timeout=900)
                    completed+=1
                    await self.publish(probeProgress={**progress,'completed':completed,'lastCompletedAt':time.time()})
            # Installers remain serial. After freezing, mount isolated workers
            # without uv sync or Foundation dependency installation.
            if not refresh:
                flags.append('--read-only')
            semaphore = asyncio.Semaphore(1 if refresh else 4)
            tasks = [asyncio.create_task(run_one(*config)) for config in sorted(configs)]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        if fresh:
            from .runtime_policy_reconciliation import reconcile, policy_inputs, reuse_collection
            from .runtime_source_alignment import align_checked_sources
            collection_inputs = None
            async def collect_source_policy():
                nonlocal collection_inputs
                target = stage / 'selected-source-declarations.json'
                overrides = await self.diagnostics.run('ecosystem-runtime-policy', prepare_overrides,
                    project, receipt / 'runtime-install-overrides.txt', owned_root=self.home)
                command = [shutil.which('uv'), 'run', '--locked', '--no-sync', '--project', str(project),
                           '--python', '3.13', 'python', str(Path(__file__).with_name('update_probe.py'))]
                before = policy_inputs(stage, project)
                await self.diagnostics.run('ecosystem-source-policy', process, *command, str(workspace), profiles[0],
                    '--install-overrides', str(overrides), '--global-only', '--profiles', str(profiles_file),
                    '--source-policy-only', str(target), env=env, timeout=900)
                collection_inputs = before if policy_inputs(stage, project) == before else None
                return json.loads(target.read_text())
            # Availability inventory can contain only the checked tier.
            # Reconciliation admits work from the current full runtime inventory.
            selection = await self.diagnostics.run('ecosystem-source-policy', reconcile, self, stage, project, collect_source_policy)
            # Source paths first materialized during composition were absent
            # from the early cache scan. Align them before a fresh composition;
            # never install the batch captured before a binding/checkout moved.
            selection = await reuse_collection(self, stage, project, selection, collection_inputs)
            if selection is None:
                selection = await collect_source_policy()
            await self.diagnostics.run('ecosystem-source-preflight', align_checked_sources,
                                       self, stage, selection=selection)
            if shared:
                await self.diagnostics.run('ecosystem-source-sharing', adopt_clean_sources,
                                           self, stage, selection=selection)
            await self.publish(detail='Preparing changed dependencies in an isolated runtime…')
            # The refresh activator dies with each short-lived probe process.
            # Capture after dynamic module installation, then recreate an
            # ordinary resolver against the frozen graph before activation.
            await probe(project,refresh=True)
            # The composed characteristics distinguish reusable partial bundles
            # from roots. Compatibility probes mount only complete profiles.
            profiles = offered_profiles(config)
            if not profiles:
                raise ValueError('No complete app-level conversation profiles are configured')
            configs = [(str(workspace), profile) for profile in profiles]
            await self.publish(detail='Recording and verifying the exact worker dependencies…',probeProgress=None)
            project=await freeze(self,release,project)
        if (project/'.venv').exists():
            await asyncio.to_thread(verify_recorded,project,receipt)
        await self.publish(detail=f'Checking {len(configs)} offered conversation profiles…')
        await probe(project)
        await asyncio.to_thread(verify_recorded,project,receipt)
        from .runtime_profiles import configuration_key
        write_private(stage/'profiles-qualified.json', json.dumps({'profiles': profiles,
            'configuration': configuration_key(config.settings, self.home, stage)}))

    async def activate(self, rollback=False):
        async with self.service.runtime_lifecycle():
            await self._activate(rollback)

    async def _activate(self, rollback=False):
        if self.lock.locked() or self.awaiting_restart(): return
        async with self.lock:
            if self.closed: return
            from .update_health import pending_preparation
            if pending_preparation(self.diagnostics.state):
                raise ValueError('The interrupted component preparation must be reconciled before activation or rollback.')
            pointer=active_release(self.home)
            state=self.service.state['updates']
            retrying_rollback=rollback and 'pendingRollback' in state
            target=state.get('pendingRollback') if retrying_rollback else pointer.get('previous') if rollback else state.get('pendingRelease')
            if target is not None and (not isinstance(target,str) or not re.fullmatch(r'[a-f0-9]{32}',target)):
                raise ValueError('Invalid ecosystem release identity')
            if rollback and not retrying_rollback and 'previous' not in pointer: return
            if not rollback and not target: return
            if rollback and (not retrying_rollback or self.diagnostics.state.get('kind')!='rollback'):
                self.diagnostics.begin('rollback',target)
            if target and not (self.directory/'releases'/target/'validated.json').exists():
                raise ValueError('This ecosystem release has not passed validation')
            if target:
                marker=json.loads((self.directory/'releases'/target/'validated.json').read_text())
                if marker.get('hostVersion')!=__import__('amplifier_web').__version__:
                    await self.validate(self.directory/'releases'/target,target)
                    marker['hostVersion']=__import__('amplifier_web').__version__
                    write_private(self.directory/'releases'/target/'validated.json',json.dumps(marker))
            from .update_partial import read as partial_read, verify_pointer, adopted_items, public_results
            partial = partial_read(self.directory / 'releases' / target) if target and not rollback else None
            if pointer.get('current') != target:
                verify_pointer(self, partial)
            hot = bool(target and marker.get('generationSchema') == 1 and marker.get('workerProtocol') == 1
                       and callable(getattr(self.service.runtime, 'promote_generation', None)))
            self.diagnostics.capture_activation_retry(target, rollback=rollback)
            async with self.service.lock:
                if self.service.closed:
                    return
                if not hot and self.busy():
                    self.service.state['updates'].update(detail='Waiting for active work and calls to finish. Try rollback again when idle.' if rollback else 'Update ready; it will activate when work and calls finish.')
                    self.service._publish_changes(globals={'updates','settings'})
                    return
                if rollback:
                    # Persist the requested target before promotion.  A retry
                    # after committing active.json must not reinterpret the
                    # swapped pointer and roll forward again.
                    self.service.state['updates']['pendingRollback'] = target
                self.service.state['updates'].update(phase='activating',detail='Switching ecosystem version…')
                self.service._publish_changes(globals={'updates','settings'})
            candidate = None
            try:
                # New work is gated during this short phase. Old sessions remain
                # durable.  The pointer is committed only after replacement
                # construction succeeds, and the live manager is replaced before
                # its terminal close can leave this host unable to admit work.
                candidate = None if hot else self.service.runtime_candidate()
                # A promotion may have committed before a runtime replacement
                # or state publication failed.  Its durable pointer already
                # retains the rollback identity, so finish it without
                # rewriting previous to the target itself.
                if pointer.get('current') != target:
                    write_private(self.directory/'active.json',json.dumps({'current':target,'previous':pointer.get('current'),'at':time.time()}))
                    if not rollback and 'pendingRollback' in self.service.state['updates']:
                        # The pointer is now the durable rollback authority for
                        # this normal promotion.  Discard an older failed
                        # rollback intent before any later publication can fail.
                        async with self.service.lock:
                            self.service.state['updates'].pop('pendingRollback', None)
                            self.service._publish_changes(globals={'updates','settings'})
                if hot:
                    def protected(sid):
                        voice = self.service.state.get('voice', {})
                        from .update_drain import retains_worker
                        return (retains_worker(self.service, self.service.runtime, sid) or
                            voice.get('sessionId') == sid and voice.get('status') not in {None, 'idle', 'ended', 'error', 'disconnected'})
                    self.service.runtime.promote_generation(target, protected=protected)
                else:
                    await self.service.replace_runtime(candidate)
                if rollback:
                    async with self.service.lock:
                        self.service.state['settings']['updates']['autoInstall'] = False
                tier = marker.get('updateTier') if target else None
                current_items = self.service.state['updates'].get('items', [])
                items = adopted_items(current_items, partial) if partial else (
                    [row for row in current_items if row.get('id')=='application'] if rollback else
                    [{**row, **({'status':'current','current':row['latest']} if row.get('status')=='update' and row.get('id')!='application' and row.get('kind')!='smart tool' and (not tier or row.get('updateTier') == tier) else {})} for row in current_items])
                if partial:
                    from .update_partial import adopted_inventory
                    self.inventory = adopted_inventory(self.inventory, partial)
                    write_private(self.directory / 'inventory.json', json.dumps(self.inventory), owned_root=self.home)
                sequence = self.service.state['updates'].get('sequence')
                if sequence:
                    from .update_sequence import summary
                    tier_summary = summary([row for row in items if row.get('updateTier') == tier])
                    sequence = {**sequence}
                    if rollback:
                        sequence.update(stage='complete', install=False)
                        sequence.pop('nextStage', None)
                    elif tier == 'included':
                        sequence.update(nextStage='other', included=tier_summary)
                    elif any(row.get('kind')=='smart tool' and row.get('status')=='update' for row in items):
                        sequence.update(nextStage='other', other=tier_summary)
                    else:
                        sequence.update(stage='complete', install=False, other=tier_summary)
                await self.publish(phase='installed',release=target,pendingRelease=None,canRollback=True,items=items,error=None,
                    componentResults=public_results(partial, adopted=True),
                    includedCanProceed=False,
                    **({'sequence': sequence} if sequence else {}),
                    available=sum(row.get('status')=='update' for row in items),installedAt=time.time(),detail='Previous ecosystem restored. Automatic installation is now off.' if rollback else 'Update ready for new work. Active conversations adopt it after their current work finishes.')
                if rollback or 'pendingRollback' in self.service.state['updates']:
                    async with self.service.lock:
                        self.service.state['updates'].pop('pendingRollback', None)
                        self.service._publish_changes(globals={'updates','settings'})
                self.diagnostics.clear_failure()
                self.diagnostics.record('ecosystem-rollback' if rollback else 'ecosystem-activation','succeeded')
                from .update_health import refresh as refresh_health
                await refresh_health(self)
                await self.publish()  # Publish settled attention, not just the preceding installed status.
                self.inventory=[]
                if self.cleanup_task is None or self.cleanup_task.done():
                    self.cleanup_task = asyncio.create_task(self.retire_storage())
                # The release is live and the replacement runtime installed.
                # A stale inventory is recoverable; it must not rewrite this
                # successful activation as an installation failure.
                try:
                    write_private(self.directory/'inventory.json','[]')
                except OSError:
                    pass
            except asyncio.CancelledError:
                await self.service.discard_runtime(candidate)
                raise
            except Exception as error:
                await self.service.discard_runtime(candidate)
                from .update_diagnostics import exception_facts
                self.diagnostics.record('ecosystem-activation','failed',**exception_facts(error))
                await self.publish(phase='error',error='Could not activate the ecosystem update; review its diagnostic receipt before retrying.')

    async def installSmartTools(self):
        from .smart_tool_update_batch import stage
        await stage(self)

    async def activateSmartTools(self):
        from .smart_tool_update_batch import activate
        await activate(self)

    async def smartToolRetry(self, identity):
        from .smart_tool_update_batch import retry
        await retry(self, identity)

    async def smartToolSkip(self, identity):
        from .smart_tool_update_batch import skip
        await skip(self, identity)

    async def smartToolRollback(self, identity):
        if self.lock.locked(): raise ValueError('Wait for the current update to finish.')
        async with self.lock:
            state = self.service.state['updates']
            pending = list(state.get('pendingSmartTools', []))
            if any(state.get(key) for key in ('pendingApp', 'pendingRelease')) or any(not item.get('failure') for item in pending):
                raise ValueError('Finish or inspect the pending update before restoring a tool.')
            from .managed_runner_updates import rollback as runner_rollback
            if await asyncio.to_thread(runner_rollback, self.service, identity):
                async with self.service.lock:
                    self.service.state['settings']['updates']['autoInstall'] = False
                await self.publish(phase='installed', error=None, detail='The previous test runner is selected for future checks. Existing checks keep their exact runner.')
                self.inventory = []
                return
            row = next((item for item in self.service.smart_tools.state['installations'] if item['id'] == identity), {})
            previous = row.get('previousInstallationId')
            if not previous: raise ValueError('No previous Smart Tool revision is available.')
            self.diagnostics.begin('smart-tools')
            await self.publish(pendingSmartTools=[*pending, {'previous': identity, 'target': previous}], phase='staged', sequence={**state.get('sequence', {}), 'install': False})
            async with self.service.lock:
                self.service.state['settings']['updates']['autoInstall'] = False
        await self.activateSmartTools()

    async def rollback(self):
        await self.activate(rollback=True)

    async def tick(self):
        if getattr(self, '_manual_handoff_commit_unknown', False):return
        settings=self.service.state['settings'].get('updates',{})
        state=self.service.state['updates']
        from .update_drain import observe_waiters
        await observe_waiters(self)
        if self.awaiting_restart():return
        if getattr(self, '_update_health_initial', False):
            self._update_health_initial = False
            from .manual_app_handoff import verify_current
            try:
                await verify_current(self, full=True)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError):
                self._manual_handoff_verified = None
            from .update_health import refresh as refresh_health
            await refresh_health(self)
            await self.publish()
        from .update_drain import finish
        drain = state.get('drain') or {}
        if drain.get('phase') in {'draining', 'replacing', 'releasing'}:
            installed = state.get('applicationResult') or {}
            if all(installed.get(key) == drain.get('target', {}).get(key) for key in ('attemptId', 'revision')):
                await finish(self, 'installed')
            elif not state.get('pendingApp') or state.get('phase') in {'error', 'interrupted'} or drain.get('hostInstanceId') != self.service.instance_id:
                await finish(self, 'withdrawn')
        if (getattr(self, '_storage_policy_due', False) or time.time() - getattr(self, '_storage_last_pass', 0) >= 3600) and (not self.cleanup_task or self.cleanup_task.done()):
            self._storage_policy_due = False
            self._storage_last_pass = time.time()
            self.cleanup_task = asyncio.create_task(self.retire_storage())
        usage = getattr(self.service.runtime, 'generation_usage', lambda: [])()
        adoption = {'activeWorkers': len(usage), 'pendingWorkers': sum(row['updatePending'] for row in usage)}
        if state.get('adoption') != adoption:
            if state.get('adoption', {}).get('pendingWorkers') and not adoption['pendingWorkers']:
                if not self.cleanup_task or self.cleanup_task.done():
                    self.cleanup_task = asyncio.create_task(self.retire_storage())
            await self.publish(adoption=adoption)
        blockers = (self.app_blockers() if state.get('pendingApp')
                    else self.blockers()) if self.requires_idle(state) else []
        if state.get('blockers', []) != blockers:
            await self.publish(blockers=blockers)
        if (state.get('applicationIntent') or {}).get('scope') == 'app':
            # This explicit request replaced any older whole-install intent.
            # Preserve other prepared/uncertain work, but do not activate it.
            if state.get('phase') in {'error', 'interrupted'}:
                return  # Retained scope never authorizes retry of failed effects.
            if state.get('pendingApp'):
                if (settings.get('autoCheck', True)
                        and time.time()-max(state.get('lastCheck') or 0, state.get('lastAttempt') or 0)
                        >= settings.get('intervalHours', DEFAULT_CHECK_INTERVAL_HOURS)*3600):
                    await self.check(fresh=True, automatic=True)
                await self.app()
            elif state.get('appAvailable'):
                await self.app()
            elif (settings.get('autoCheck', True)
                    and time.time() - max(state.get('lastCheck') or 0, state.get('lastAttempt') or 0)
                    >= settings.get('intervalHours', DEFAULT_CHECK_INTERVAL_HOURS) * 3600):
                # A future ordinary check is a new policy cycle, not continuation
                # of the superseded whole-install command or its stale inventory.
                await self.check(fresh=False, automatic=True)
            return
        if state.get('pendingSmartTools') and not self.independent_tool_results(state):
            if (state.get('phase') not in {'error', 'interrupted'} or any(
                    row.get('runnerUpdate') and not row.get('failure') for row in state['pendingSmartTools'])):
                await self.activateSmartTools()
            return
        if state.get('pendingApp'):
            if state.get('phase') in {'error', 'interrupted'}:return
            if settings.get('autoCheck', True) and time.time()-max(state.get('lastCheck') or 0,state.get('lastAttempt') or 0)>=settings.get('intervalHours',DEFAULT_CHECK_INTERVAL_HOURS)*3600:
                await self.check(fresh=True, automatic=True)
            await self.app()
            return
        if state.get('pendingRelease'):
            from .update_health import pending_preparation
            if pending_preparation(self.diagnostics.state):return
            await self.activate()
            return
        if state.get('initialPreparation') and state.get('sequence', {}).get('stage') == 'complete':
            await self.publish(initialPreparation=False)
        sequence = state.get('sequence', {})
        from .update_health import pending_preparation
        if pending_preparation(self.diagnostics.state):
            # Health, drain, storage, worker adoption and independent app/tool
            # paths above still run. No component availability/install is replayed.
            if settings.get('autoCheck', True) and time.time()-max(state.get('lastCheck') or 0,state.get('lastAttempt') or 0)>=settings.get('intervalHours',DEFAULT_CHECK_INTERVAL_HOURS)*3600:
                await self.check(tier='application', fresh=False, automatic=True)
            if state.get('appAvailable') and settings.get('autoInstall', True):
                await self.app()
            return
        if sequence.get('nextStage') and state.get('phase') not in {'error','interrupted'}:
            # App-first checking deliberately skips component discovery. After
            # the new app starts, refresh those refs rather than inheriting a
            # cached result from before its required components were published.
            # Refresh later tiers too: an earlier tier may have stopped before
            # checking them, leaving only results from a previous update cycle.
            await self.check(tier=sequence['nextStage'], install=sequence.get('install', False),
                             fresh=True, automatic=True)
        elif settings.get('autoCheck',True) and time.time()-max(state.get('lastCheck') or 0,state.get('lastAttempt') or 0)>=settings.get('intervalHours',DEFAULT_CHECK_INTERVAL_HOURS)*3600:
            await self.check(fresh=False, automatic=True)
        state=self.service.state['updates']
        managed_preview=state.get('appAvailable') and state.get('application',{}).get('canInstall') is False
        sequence = state.get('sequence', {})
        install_intent = (sequence.get('install') if sequence.get('manualHandoff')
                          else settings.get('autoInstall', True) or sequence.get('install'))
        batch = self.diagnostics.state.get('batch', {})
        retry = batch.get('retry') or {}
        retry_due = (state.get('phase') == 'error' and batch.get('kind') == 'ecosystem'
                     and retry.get('mode') == 'backoff' and retry.get('retryAt', 0) <= time.time())
        if not managed_preview and install_intent and state.get('available',0) and (state.get('phase') == 'available' or retry_due):
            # The timer wakes the existing install owner, which rechecks source,
            # intent and uncertain-operation guards before preparing anything.
            await self.install(automatic=True)
            return
        if self.independent_tool_results(state) and (state.get('phase') not in {'error', 'interrupted'} or any(
                row.get('runnerUpdate') and not row.get('failure') for row in state['pendingSmartTools'])):
            await self.activateSmartTools()

    async def wait_for_work(self, delay):
        try:
            await asyncio.wait_for(self.wakeup.wait(), delay)
        except TimeoutError:
            pass

    async def loop(self):
        await self.wait_for_work(3)
        while not self.closed:
            self.wakeup.clear()
            previous_event=self.diagnostics.state.get('latest',{}).get('id')
            try:
                await self.tick()
            except asyncio.CancelledError: raise
            except Exception as error:
                from .update_diagnostics import exception_facts, failure_guidance
                last=self.diagnostics.state.get('latest',{})
                if last.get('status') not in {'failed','interrupted'} or last.get('id')==previous_event:
                    self.diagnostics.record('background-update','failed',**exception_facts(error))
                phase=self.diagnostics.state['latest']['phase']
                await self.publish(phase='activating' if self.service.state['updates'].get('pendingRestart') else 'error',error=failure_guidance(self.diagnostics.state.get('lastFailure', {})) or 'Background update failed during '+phase.replace('-',' ')+'. Review its diagnostic receipt; no work was replayed.')
            sequence = self.service.state['updates'].get('sequence', {})
            # A finished stage can continue on the next event-loop turn.
            # External commands interrupt the ordinary background cadence;
            # never make a user wait for its next timer tick.
            if (sequence.get('nextStage') and self.service.state['updates'].get('phase') == 'installed'
                    and not self.lock.locked() and not (self.check_task and not self.check_task.done())):
                self.wakeup.set()
            await self.wait_for_work(60)

    async def retire_storage(self):
        # Storage maintenance is outside the activation request. Worker leases
        # protect active generations; unrelated work need not delay reclamation.
        await asyncio.sleep(10)
        if self.closed or self.awaiting_restart():
            return
        started = time.monotonic()
        try:
            # Tool result retention is its own existing promise, independent of
            # whether unused-code cleanup is enabled in Settings.
            smart_tools = getattr(self.service, 'smart_tools', None)
            if smart_tools is not None:
                await smart_tools.expire_results()
            from .storage_cleanup import run
            storage = await run(self.service, uuid.uuid4().hex, automatic=True)
            await self.publish(storage={**storage, 'durationMs': round((time.monotonic() - started) * 1000)})
        except Exception:
            await self.publish(storage={'reason': 'Storage reclamation was deferred; existing files were kept.'})

    async def close(self):
        self.closed=True
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            await asyncio.gather(self.cleanup_task, return_exceptions=True)
        if self.check_task:
            self.check_task.cancel()
            await asyncio.gather(self.check_task, return_exceptions=True)
        await self.check_cache.close()
        if self.readiness_task:
            self.readiness_task.cancel()
            await asyncio.gather(self.readiness_task,return_exceptions=True)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
