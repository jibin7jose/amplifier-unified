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
            from .app_release_artifacts import source_revision
            revision = source_revision(distribution)
    except (metadata.PackageNotFoundError, OSError, ValueError, AttributeError):
        pass
    return {'version': __version__, 'revision': revision, 'instanceId': uuid.uuid4().hex}


def _observed_artifact(manager, qualification, installation=None):
    """Bind an existing qualification to the actual serving installation."""
    from . import app_component_graph as components, application_generations
    from .app_release_artifacts import distribution_row
    from .app_updates import validated_extras
    artifact = qualification.get('artifact')
    if not artifact:
        return None
    app = components.validate_graph([qualification['app']])[0]
    validated_extras(qualification['extras'])
    if not re.fullmatch('[a-f0-9]{64}', qualification.get('dependencyDigest', '')):
        return None
    if any(app.get(key) != manager.running_identity.get(key) for key in ('version', 'revision')):
        return None
    distribution = metadata.distribution('amplifier-unified')
    if Path(distribution.locate_file('amplifier_web')).resolve() != Path(__file__).parent.resolve():
        return None
    observed = components.normalized([distribution_row(distribution)], artifact=artifact)[0]
    if observed != app or 'delivery' not in observed:
        return None
    binding = application_generations.bootstrap_identity(sys.prefix)
    if installation is not None and installation != binding:
        return None
    return {'artifact': copy.deepcopy(artifact), 'app': observed,
            'installation': binding, 'qualification': copy.deepcopy(qualification)}


def capture_artifact(manager):
    """Capture receipt-backed archive eligibility once, before replacing files.

    A source stamp describes source; it cannot create delivery qualification.
    Pending effects have no invented destination binding. A new host observes
    that binding, while confirmed results/generations must retain their old one.
    """
    from . import application_generations, app_component_graph as components
    from .app_replacement import qualified
    state = manager.service.state['updates']
    for key in ('pendingReplacement', 'pendingRestart', 'applicationResult'):
        target = state.get(key)
        if not isinstance(target, dict):
            continue
        try:
            if key == 'applicationResult':
                if (target.get('phase') not in {'restart-ack', 'restart-superseded', 'restart-reconcile'}
                        or not isinstance(target.get('installation'), dict)):
                    continue
            elif (not valid_target(target) or not qualified(target)
                    or target.get('sourceInstanceId') == manager.running_identity['instanceId']):
                continue
            if any(target.get(field) != manager.running_identity.get(field) for field in ('version', 'revision')):
                continue
            observed = _observed_artifact(manager, target.get('qualification') or {}, target.get('installation'))
            if observed:
                return observed
        except (AttributeError, KeyError, TypeError, ValueError, OSError, metadata.PackageNotFoundError):
            continue
    try:
        active = application_generations.active(manager.home)
        if not active or Path(sys.prefix).resolve() != active['prefix'].resolve():
            return None
        validated = json.loads((active['folder'] / 'validated.json').read_text())
        if not isinstance(validated.get('installation'), dict) or not validated.get('artifact'):
            return None
        graph = components.validate_graph(validated['componentGraph'])
        if components.digest(graph) != validated['componentDigest']:
            return None
        app = next(row for row in graph if row['name'] == 'amplifier-unified')
        proof = {'app': app, 'extras': validated['extras'], 'artifact': validated['artifact'],
                 'dependencyDigest': components.digest([row for row in graph if row['name'] != 'amplifier-unified'])}
        return _observed_artifact(manager, proof, validated['installation'])
    except (AttributeError, KeyError, TypeError, ValueError, OSError, StopIteration, metadata.PackageNotFoundError):
        return None


def artifact_context(manager):
    """Private delivery context; never expose it through listener identity."""
    captured = getattr(manager, '_running_artifact', None)
    if not captured:
        return None
    try:
        current = _observed_artifact(manager, captured['qualification'], captured['installation'])
        return copy.deepcopy(current['artifact']) if current else None
    except (AttributeError, KeyError, TypeError, ValueError, OSError, metadata.PackageNotFoundError):
        return None


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
        if app.get('delivery'):
            candidate['qualification']['artifact'] = artifact_context(manager)
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
            archive_result = {}
            proof = installed.get('qualification') or {}
            if (proof.get('app') or {}).get('delivery'):
                try:
                    if not proof.get('artifact') or artifact_context(manager) != proof['artifact']:
                        return False
                    if not matches_running(manager, installed):
                        return False
                    observed = _observed_artifact(manager, proof, manager._running_artifact['installation'])
                    if not observed:
                        return False
                    archive_result = {'qualification': copy.deepcopy(proof),
                                      'installation': observed['installation']}
                except (AttributeError, KeyError, TypeError, ValueError, OSError, metadata.PackageNotFoundError):
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
                'verifiedAt': time.time(), **archive_result}
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


def local_interface_addresses():
    """Snapshot assigned numeric IPs from libc getifaddrs, without DNS/commands.

    Linux and macOS share ifaddrs layout but not sockaddr family layout.
    Windows is deliberately unsupported; its host qualification is separate.
    """
    import ctypes as c
    import ipaddress
    import socket

    if sys.platform not in {'linux', 'darwin'}:
        raise NotImplementedError('Local interface enumeration supports Linux and macOS only.')

    class Ifaddrs(c.Structure):
        pass

    Ifaddrs._fields_ = [('next', c.POINTER(Ifaddrs)), ('name', c.c_char_p),
                       ('flags', c.c_uint), ('addr', c.c_void_p),
                       ('netmask', c.c_void_p), ('destination', c.c_void_p),
                       ('data', c.c_void_p)]
    libc = c.CDLL(None, use_errno=True)
    try:
        get, free = libc.getifaddrs, libc.freeifaddrs
    except AttributeError as error:
        raise NotImplementedError('getifaddrs is unavailable.') from error
    get.argtypes, get.restype = [c.POINTER(c.POINTER(Ifaddrs))], c.c_int
    free.argtypes, free.restype = [c.POINTER(Ifaddrs)], None
    head = c.POINTER(Ifaddrs)()
    if get(c.byref(head)) != 0:
        raise OSError(c.get_errno(), 'Unable to enumerate local interfaces.')
    hosts = []
    try:
        node = head
        while node:
            entry = node.contents
            node = entry.next
            if not entry.addr:
                continue
            header = c.string_at(entry.addr, 2)
            family = header[1] if sys.platform == 'darwin' else int.from_bytes(header, sys.byteorder)
            if family == socket.AF_INET:
                host = socket.inet_ntop(family, c.string_at(entry.addr + 4, 4))
            elif family == socket.AF_INET6:
                packed = bytearray(c.string_at(entry.addr + 8, 16))
                scope = int.from_bytes(c.string_at(entry.addr + 24, 4), sys.byteorder)
                # Darwin can embed the interface index in link-local s6_addr[2:4].
                if sys.platform == 'darwin' and packed[0] == 0xfe and packed[1] & 0xc0 == 0x80:
                    scope = scope or int.from_bytes(packed[2:4], 'big')
                    packed[2:4] = b'\0\0'
                host = socket.inet_ntop(family, bytes(packed))
                if ipaddress.ip_address(host).is_link_local:
                    scope = scope or socket.if_nametoindex(entry.name.decode(errors='surrogateescape'))
                if scope:
                    host += '%' + str(scope)
            else:
                continue
            ip = ipaddress.ip_address(host)
            if not ip.is_unspecified and not ip.is_multicast:
                hosts.append(host)
    finally:
        free(head)
    return list(dict.fromkeys(hosts))


def local_probe_hosts(bind, *, wildcard_hosts=None):
    """Resolve once and admit only numeric addresses owned by this computer.

    A bind name is not authority to send a control credential through DNS.
    An ordinary ephemeral bind (without freebind/reuse options) establishes local
    address ownership, not listener readiness. Wildcard candidates come only
    from a local interface snapshot (or explicitly partial loopback fallback).
    """
    import ipaddress
    import socket

    if bind in {'0.0.0.0', '::'}:
        if wildcard_hosts is None:
            try:
                wildcard_hosts = local_interface_addresses()
            except (OSError, NotImplementedError):
                wildcard_hosts = ['127.0.0.1', '::1']
        version = 4 if bind == '0.0.0.0' else 6
        hosts = []
        for host in wildcard_hosts:
            if ipaddress.ip_address(host).version == version:
                hosts.extend(local_probe_hosts(host))
        return list(dict.fromkeys(hosts))
    host = bind
    try:
        # Numerical candidates must never consult DNS, including scoped IPv6.
        numeric = False
        try:
            ipaddress.ip_address(host)
            numeric = True
        except ValueError:
            pass
        addresses = socket.getaddrinfo(host, 0, type=socket.SOCK_STREAM,
                                       **({'flags': socket.AI_NUMERICHOST} if numeric else {}))
    except OSError:
        return []
    hosts = []
    for family, kind, protocol, _, address in addresses:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        ip = ipaddress.ip_address(address[0])
        # IPv4-mapped sockets can use IPv4 nonlocal-bind policy even when the
        # IPv6 sysctl is disabled. They are not an independent local identity.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            continue
        if ip.is_unspecified or ip.is_multicast:
            continue
        if sys.platform == 'linux' and not ip.is_loopback:
            # Linux can globally permit nonlocal binds. In that configuration
            # a successful ephemeral bind is no longer local-address evidence.
            try:
                if Path(f'/proc/sys/net/ipv{ip.version}/ip_nonlocal_bind').read_text().strip() != '0':
                    continue
            except OSError:
                continue
        try:
            with socket.socket(family, kind, protocol) as candidate:
                candidate.bind(address)
        except OSError:
            continue
        value = str(ip)
        if family == socket.AF_INET6 and address[3] and '%' not in value:
            value += '%' + str(address[3])
        hosts.append(value)
    return list(dict.fromkeys(hosts))


def probe_targets(manager, *, diagnostics=None):
    """Connect only to configured local bind addresses, with exact TLS pinning.

    Pin the configured leaf certificate so custom CA/DNS deployments work
    without routing a control token through a proxy or disabling TLS checks.
    """
    import ipaddress
    from .deployment import is_loopback, resolve_path
    config = manager.service.server_config
    secure = config['tls']['method'] != 'none'
    pin = None
    if secure:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        certificate = x509.load_pem_x509_certificate(resolve_path(manager.home, config['tls']['cert']).read_bytes())
        pin = aiohttp.Fingerprint(certificate.fingerprint(hashes.SHA256()))
    urls = []
    scope = 'Locally assigned addresses resolved from configured binds.'
    complete = True
    interfaces = None
    if any(bind in {'0.0.0.0', '::'} for bind in config['bind']):
        try:
            interfaces = local_interface_addresses()
            scope += ' Wildcard binds cover the assigned numeric interfaces from a local getifaddrs snapshot.'
        except (OSError, NotImplementedError):
            interfaces = ['127.0.0.1', '::1']
            complete = False
            scope += ' PARTIAL: local interface enumeration unavailable; wildcard binds test loopback only, not all interfaces.'
    for bind in config['bind']:
        hosts = local_probe_hosts(bind, wildcard_hosts=interfaces)
        if not hosts:
            complete = False
        if bind in {'0.0.0.0', '::'}:
            expected = {host for host in interfaces
                        if ipaddress.ip_address(host).version == (4 if bind == '0.0.0.0' else 6)}
            if expected - set(hosts):
                complete = False
        for host in hosts:
            if not secure and not is_loopback(host):
                complete = False
                continue
            host = '[' + host + ']' if ':' in host else host
            urls.append(f"{'https' if secure else 'http'}://{host}:{manager.service.port}/api/health")
    if diagnostics is not None:
        if not complete:
            scope += ' PARTIAL: some candidates could not be enumerated or admitted by local ownership/TLS checks.'
        diagnostics.update(listenerScope=scope + ' Only matching authenticated responses are verified; this is not proof of remote client reachability.',
                           listenerScopeComplete=complete)
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
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        async with asyncio.timeout_at(deadline):
            successor = await qualify_successor(manager, target)
            # Resolver/syscall preparation must not stall the event loop or
            # receive a fresh timeout budget after a slow DNS lookup.
            urls, pin = await asyncio.to_thread(probe_targets, manager)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as client:
                while not manager.closed and asyncio.get_running_loop().time() < deadline:
                    for url in urls:
                        if asyncio.get_running_loop().time() >= deadline:
                            raise TimeoutError
                        try:
                            async with client.get(url, ssl=pin, allow_redirects=False, headers={
                                    'Authorization': 'Bearer ' + token, 'Host': f'localhost:{manager.service.port}'}) as response:
                                value = await response.json() if response.status == 200 else None
                                if asyncio.get_running_loop().time() >= deadline:
                                    raise TimeoutError
                                if response.status == 200 and await confirm_readiness(manager, value, expected=target, command_id=command_id, successor=successor):
                                    return
                        except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                            pass
                    await asyncio.sleep(interval)
        issue = ('TimeoutError', 'failed')
    except asyncio.CancelledError:
        issue = ('CancelledError', 'interrupted')
        raise
    except TimeoutError:
        issue = ('TimeoutError', 'failed')
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
