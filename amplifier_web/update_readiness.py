"""Confirm a replacement host through its listener and qualified installation.

Identity is captured before this process can replace its installation. Restart
receipts belong to one attempt; a healthy newer host does not retroactively
turn an old failed systemctl command into a successful command.
"""
from __future__ import annotations

import asyncio
import copy
import json
from importlib import metadata
from pathlib import Path
import re
import sys
import time
import uuid

import aiohttp

from . import __version__

LEGACY_RESTART_ERROR = 'The app installed, but the managed service could not restart. Run amplifier-unified service restart.'


def running_identity():
    revision = None
    try:
        distribution = metadata.distribution('amplifier-unified')
        # Do not attest a different installed distribution when running a checkout.
        if Path(distribution.locate_file('amplifier_web')).resolve() == Path(__file__).parent.resolve():
            source = json.loads(distribution.read_text('direct_url.json') or '{}')
            value = source.get('vcs_info', {}).get('commit_id')
            if isinstance(value, str) and re.fullmatch('[a-f0-9]{40}', value):
                revision = value
    except (metadata.PackageNotFoundError, OSError, ValueError, AttributeError):
        pass
    return {'version': __version__, 'revision': revision, 'instanceId': uuid.uuid4().hex}


def valid_target(value):
    from .app_features import valid_restart_selection
    return (isinstance(value, dict)
            and isinstance(value.get('version'), str) and re.fullmatch(r'\d+\.\d+\.\d+', value['version'])
            and isinstance(value.get('revision'), str) and re.fullmatch('[a-f0-9]{40}', value['revision'])
            and isinstance(value.get('attemptId'), str) and re.fullmatch('[a-f0-9]{32}', value['attemptId'])
            and valid_restart_selection(value))


def recovery_candidate(manager):
    state = manager.service.state['updates']
    from .app_replacement import pending as replacement_pending, qualified
    if replacement_pending(state):
        target = state['pendingReplacement']
        return copy.deepcopy(target) if valid_target(target) and qualified(target) else None
    pending = state.get('pendingRestart')
    if pending:
        return copy.deepcopy(pending) if valid_target(pending) else None
    # Older updaters erase the marker after systemd terminates their child.
    # Recover only that precise failure, corroborated by its validation receipt
    # and successful replacement probe. Never infer readiness from version alone.
    if state.get('phase') != 'error' or state.get('error') != LEGACY_RESTART_ERROR:
        return None
    diagnostics = state.get('diagnostics', {})
    failure = diagnostics.get('lastFailure') or {}
    if (failure.get('kind') != 'application' or failure.get('phase') != 'service-restart'
            or failure.get('status') not in {'failed', 'interrupted'}
            or failure.get('attemptId') != diagnostics.get('attemptId')
            or failure.get('revision') != diagnostics.get('revision')):
        return None
    revision = failure.get('revision', '')
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision):
        return None
    directory = manager.directory / 'applications' / revision
    paths = [directory / 'validated.json']
    # New update attempts keep separate immutable receipts. Match the precise
    # failed attempt, never a newer candidate for the same app version/revision.
    try:
        paths.extend(child / 'validated.json' for child in directory.iterdir()
                     if not child.is_symlink() and child.is_dir()
                     and re.fullmatch('[a-f0-9]{32}', child.name))
    except OSError:
        pass
    matches = []
    for path in paths:
        try:
            candidate = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (not valid_target(candidate) or candidate['attemptId'] != failure.get('attemptId')
                or candidate['revision'] != revision):
            continue
        if path.parent != directory and candidate.get('generation') != path.parent.name:
            continue
        matches.append(candidate)
    if len(matches) != 1:
        return None
    validated = matches[0]
    if not any(event.get('attemptId') == validated['attemptId'] and event.get('revision') == revision
               and event.get('kind') == 'application' and event.get('phase') == 'replacement-probe'
               and event.get('status') == 'succeeded' and event.get('probe', {}).get('ok') is True
               and event.get('probe', {}).get('version') == validated['version']
               for event in diagnostics.get('events', [])):
        return None
    return {**validated, 'legacy': True}


def same_target(first, second):
    return bool(first and second and all(first.get(key) == second.get(key)
                for key in ('attemptId', 'version', 'revision', 'sourceInstanceId', 'legacy', 'featureSelection', 'dependencyDigest', 'qualification')))


async def qualify_successor(manager, target):
    """Independently qualify a published install that supersedes an old restart.

    Never accept a version comparison as installation proof or turn the old
    restart command into a success. Keep uncertain replacements and additive
    feature requests behind their existing exact qualification boundaries.
    """
    from . import app_updates
    from .app_features import running_application
    from .app_replacement import pending, qualified, matches_running
    from .update_diagnostics import probe_record
    identity = manager.running_identity
    if (pending(manager.service.state['updates']) or not qualified(target)
            or target.get('featureSelection') or target.get('legacy')
            or target.get('sourceInstanceId') == identity['instanceId']
            or not app_updates.version_tuple(identity['version'])
            or app_updates.version_tuple(identity['version']) <= app_updates.version_tuple(target['version'])):
        return None
    try:
        app = running_application(manager)
        extras = app_updates.installed_extras()
        if not set(target['qualification']['extras']) <= set(extras):
            return None
        tag = 'v' + identity['version']
        release = json.loads(await app_updates.github_read(
            f'repos/{app_updates.REPOSITORY}/releases/tags/{tag}', timeout=15))
        if (release.get('tag_name') != tag or release.get('draft') is not False
                or release.get('prerelease') is not False):
            return None
        output = await app_updates.process('git', 'ls-remote', app_updates.SOURCE,
            'refs/tags/' + tag, 'refs/tags/' + tag + '^{}', env=app_updates.git_environment(), timeout=15)
        rows = [line.split() for line in output.splitlines()]
        revision = next((row[0] for row in rows if len(row) == 2 and row[1] == 'refs/tags/' + tag + '^{}'),
                        next((row[0] for row in rows if len(row) == 2 and row[1] == 'refs/tags/' + tag), None))
        if revision != identity['revision'] or revision == target['revision']:
            return None
        candidate = {**target, 'version': identity['version'], 'revision': revision,
            'qualification': {'app': app, 'extras': extras,
                'dependencyDigest': app_updates.components.digest(app_updates.components.installed_graph())}}
        if not matches_running(manager, candidate):
            return None
        output = await manager.diagnostics.run('restart-current-installation-probe', app_updates.process,
            sys.executable, '-I', '-c', app_updates.PROBE, *extras, timeout=30)
        report = probe_record(output)
        if (not report or report.get('ok') is not True or report.get('isolated') is not True
                or report.get('stage') != 'complete' or report.get('version') != identity['version']
                or not matches_running(manager, candidate)):
            return None
        return candidate
    except (AttributeError, ValueError, OSError, RuntimeError, TimeoutError, metadata.PackageNotFoundError):
        return None


async def confirm_readiness(manager, health, expected=None, command_id=None, *, successor=None):
    """Consume an authenticated HTTP probe of this exact running host."""
    from .auth import data_identity
    async with manager.lock:
        target = recovery_candidate(manager)
        installed = successor or target
        identity = manager.running_identity
        if (not target or (expected is not None and not same_target(target, expected))
                or not isinstance(health, dict) or health.get('ok') is not True
                or health.get('app') != 'amplifier-unified'
                or health.get('dataIdentity') != data_identity(manager.home)
                or any(health.get(key) != identity[key] for key in ('version', 'revision', 'instanceId'))
                or any(identity[key] != installed[key] for key in ('version', 'revision'))
                or target.get('sourceInstanceId') == identity['instanceId']):
            return False
        if successor:
            from .app_updates import version_tuple
            from .app_replacement import pending
            if (target.get('featureSelection') or target.get('legacy') or pending(manager.service.state['updates'])
                    or not same_target({**successor, 'version': target['version'], 'revision': target['revision'],
                        'qualification': target.get('qualification')}, target)
                    or version_tuple(successor['version']) <= version_tuple(target['version'])):
                return False
        selected = target.get('featureSelection')
        if 'qualification' in installed:
            from .app_replacement import matches_running
            try:
                if not matches_running(manager, installed):
                    return False
            except (AttributeError, ValueError, OSError, metadata.PackageNotFoundError):
                return False
        if selected:
            # Same app version/revision alone cannot attest an added feature.
            from .app_features import validate_selection
            from .app_updates import installed_extras, components
            try:
                validate_selection(manager, selected, installed_extras(), installing=False)
                if components.digest(components.installed_graph()) != target.get('dependencyDigest'):
                    return False
            except (ValueError, OSError):
                return False
        from .app_replacement import pending as replacement_pending, verify_running
        uncertain = replacement_pending(manager.service.state['updates'])
        if uncertain:
            try:
                if not await verify_running(manager, target):
                    return False
            except (AttributeError, ValueError, OSError, RuntimeError, metadata.PackageNotFoundError):
                return False
        extras = (target.get('qualification') or {}).get('extras')
        if not successor and not target.get('legacy') and manager.diagnostics.browser_failures(extras):
            # The outgoing version may predate browser recovery tracking. Prove
            # the running browser works before retiring its old failure; a
            # healthy HTTP listener alone is not browser acceptance.
            from .app_updates import qualify_browser
            manager.diagnostics.begin('application', target['revision'], target['attemptId'], application_extras=extras)
            manager.diagnostics.state['batch']['verifiedBrowserFailures'] = {}
            try:
                await qualify_browser(manager, sys.executable)
            except (ValueError, OSError, RuntimeError, TimeoutError):
                pass  # Preserve browser attention; app health has its own receipt.
        async with manager.service.lock:
            state = manager.service.state['updates']
            # The isolated import check awaited another process. A changed
            # receipt or package inventory must never inherit its success.
            if (not same_target(recovery_candidate(manager), target)
                    or replacement_pending(state) != uncertain):
                return False
            if successor:
                try:
                    if not matches_running(manager, successor):
                        return False
                except (AttributeError, ValueError, OSError, metadata.PackageNotFoundError):
                    return False
            if uncertain:
                try:
                    if not matches_running(manager, target):
                        return False
                except (AttributeError, ValueError, OSError, metadata.PackageNotFoundError):
                    return False
            manager.diagnostics.begin('application', target['revision'], target['attemptId'],
                application_extras=(target.get('qualification') or {}).get('extras'))
            if successor:
                state['reconciliation'] = {'attemptId': target['attemptId'],
                    **{key: identity[key] for key in ('version', 'revision')}}
                state['reconciliation'].update(verifiedAt=time.time(),
                    supersedes={key: target[key] for key in ('version', 'revision')})
                detail = 'The running published release has been verified. The older pending restart was superseded; its original outcome remains in update history.'
                phase = 'restart-superseded'
            elif target.get('legacy'):
                # Keep the historical failure in the receipt; this is current
                # health reconciliation, not a fabricated historical restart-ack.
                state['reconciliation'] = {key: target[key] for key in ('attemptId', 'version', 'revision')}
                state['reconciliation']['verifiedAt'] = time.time()
                detail = 'The current installation is healthy. The previous restart command issue is retained in the update history; no additional restart is needed.'
                phase = 'restart-reconcile'
            else:
                manager.diagnostics.clear_failure()
                state.pop('reconciliation', None)
                detail = 'Application update installed and the restarted server is healthy.'
                phase = 'restart-ack'
            application = state.get('application', {})
            if not selected:
                application.pop('componentUpdates', None)
                application.update(status='current', current=identity['version'])
                if successor:
                    application.update(latest='v'+identity['version'], revision=identity['revision'],
                        releaseBehind=False, detail='The current published installation is verified.')
            state['items'] = [application if row.get('id') == 'application' else row for row in state.get('items', [])]
            state['available'] = sum(row.get('status') == 'update' for row in state['items'])
            state.update(phase='installed', pendingRestart=None, pendingReplacement=None, pendingApp=None, appAvailable=application.get('status')=='update' if selected else False,
                         installedAt=time.time(), error=None, detail=detail)
            # Keep the confirmed app result independently of subsequent component
            # attempts and the bounded diagnostic history. Consumers must match
            # its version/revision to the running installation, never the latest
            # available release or a prepared candidate.
            state['applicationResult'] = {'attemptId': target['attemptId'], 'phase': phase,
                'version': identity['version'], 'revision': identity['revision'],
                'verifiedAt': time.time()}
            if not selected:
                # Resume only after this exact restarted installation is healthy.
                # A manual Install request covers the remaining component phases.
                sequence = state.get('sequence') or {'install': manager.service.state['settings'].get('updates', {}).get('autoInstall', True)}
                state['sequence'] = {**sequence, 'stage': 'included', 'nextStage': 'included',
                                     'included': {'status':'waiting','available':0,'missing':0,'issues':0},
                                     'other': {'status':'waiting','available':0,'missing':0,'issues':0}}
                state['detail'] += ' Continuing with included components, then other sources.'
            if selected:
                row = state.setdefault('featureResults', {}).setdefault(selected['requestId'], {})
                row.update(requestId=selected['requestId'], feature=selected['feature'], phase='installed',
                           updatedAt=time.time(), detail='The restarted host is healthy and the exact qualified feature components are installed.')
                state['detail'] = row['detail']
            if command_id:
                manager.diagnostics.record('restart-readiness', 'superseded' if successor else 'succeeded', commandId=command_id,
                                           observedVersion=identity['version'], observedRevision=identity['revision'])
            manager.diagnostics.record(phase, 'succeeded', observedVersion=identity['version'],
                                       observedRevision=identity['revision'])
            manager.service._publish_changes(globals={'updates'})
        manager.wakeup.set()
        return True


def probe_targets(manager):
    """Connect only to configured local bind addresses, with exact TLS pinning.

    Pin the configured leaf certificate so custom CA/DNS deployments work
    without routing a control token through a proxy or disabling TLS checks.
    """
    from .deployment import resolve_path
    config = manager.service.server_config
    secure = config['tls']['method'] != 'none'
    pin = None
    if secure:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        certificate = x509.load_pem_x509_certificate(resolve_path(manager.home, config['tls']['cert']).read_bytes())
        pin = aiohttp.Fingerprint(certificate.fingerprint(hashes.SHA256()))
    urls = []
    for host in config['bind']:
        host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
        host = '[' + host + ']' if ':' in host else host
        urls.append(f"{'https' if secure else 'http'}://{host}:{manager.service.port}/api/health")
    return list(dict.fromkeys(urls)), pin


async def wait_for_readiness(manager, token, *, timeout=60, interval=.25):
    target = recovery_candidate(manager)
    if not target:
        return
    # This task starts during aiohttp startup, but acknowledges only after an
    # actual request succeeds. on_startup itself precedes listener readiness.
    diagnostics = manager.diagnostics
    diagnostics.begin('application', target['revision'], target['attemptId'],
        application_extras=(target.get('qualification') or {}).get('extras'))
    historical_failure = diagnostics.state.get('lastFailure') if target.get('legacy') else None
    command_id = uuid.uuid4().hex
    diagnostics.record('restart-readiness', 'started', commandId=command_id,
                       expectedVersion=target['version'], expectedRevision=target['revision'])
    issue = None
    try:
        successor = await qualify_successor(manager, target)
        urls, pin = probe_targets(manager)
        deadline = asyncio.get_running_loop().time() + timeout
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as client:
            while not manager.closed and asyncio.get_running_loop().time() < deadline:
                for url in urls:
                    try:
                        async with client.get(url, ssl=pin, allow_redirects=False, headers={
                                'Authorization': 'Bearer ' + token, 'Host': f'localhost:{manager.service.port}'}) as response:
                            if response.status == 200 and await confirm_readiness(manager, await response.json(), expected=target, command_id=command_id, successor=successor):
                                return
                    except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                        pass
                await asyncio.sleep(interval)
        issue = ('TimeoutError', 'failed')
    except asyncio.CancelledError:
        issue = ('CancelledError', 'interrupted')
        raise
    except (OSError, ValueError):
        issue = ('ValueError', 'failed')
    finally:
        if issue:
            async with manager.lock:
                async with manager.service.lock:
                    if same_target(recovery_candidate(manager), target):
                        error_type, status = issue
                        diagnostics.record('restart-readiness', status, commandId=command_id, errorType=error_type,
                                           expectedVersion=target['version'], expectedRevision=target['revision'],
                                           observedVersion=manager.running_identity['version'],
                                           observedRevision=manager.running_identity['revision'])
                        if historical_failure:
                            diagnostics.state['lastFailure'] = historical_failure
                        else:
                            manager.service.state['updates'].update(phase='activating', error='The installed release has not been confirmed by the running server. Check the service status and update receipt before restarting.')
                        manager.service._publish_changes(globals={'updates'})
