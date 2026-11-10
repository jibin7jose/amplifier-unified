"""Verify an already completed manual Windows handoff; never perform one.

Only the exact settled pre-effect restart-capability refusal is reconcilable.
The proof is scoped to this installed payload, owner and physical data directory.
It grants no restart, feature-install, generation promotion or input replay.
"""
import asyncio
import copy
from contextlib import contextmanager, ExitStack
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit

PHASE = 'manual-handoff-reconciled'
RESULTS = 'manualHandoffResults'


def _require(condition, detail='The manual handoff proof or update target changed.'):
    if not condition:
        raise ValueError(detail)


def _state_witness(manager):
    state = manager.service.state['updates']
    from .update_health import pending_preparation
    _require(not manager.closed and state.get('pendingReplacement') is None
             and state.get('pendingRestart') is None and state.get('pendingRelease') is None
             and state.get('pendingRollback') is None
             and not state.get('pendingSmartTools') and not pending_preparation(manager.diagnostics.state)
             and state.get('phase') == 'error'
             and not (state.get('drain') or {}).get('phase') in {'waiting', 'draining', 'replacing', 'releasing'}
             and not any(row.get('phase') in {'queued', 'staging', 'qualified', 'activating'}
                         for row in state.get('featureResults', {}).values()),
             'An unfinished update, feature request or handoff still owns this installation.')
    pending = state.get('pendingApp')
    _require(isinstance(pending, dict) and not pending.get('featureSelection'))
    for key, pattern in (('revision', '[a-f0-9]{40}'), ('generation', '[a-f0-9]{32}'),
                         ('attemptId', '[a-f0-9]{32}')):
        _require(isinstance(pending.get(key), str) and re.fullmatch(pattern, pending[key]))
    failure = manager.diagnostics.state.get('lastFailure') or {}
    _require(failure.get('kind') == 'application' and failure.get('phase') == 'restart-capability'
             and failure.get('status') == 'failed' and failure.get('errorType') == 'RuntimeError'
             and failure.get('attemptId') == pending['attemptId']
             and failure.get('revision') == pending['revision']
             and failure == manager.diagnostics.state.get('latest'),
             'Only the exact settled pre-effect Windows refusal can be reconciled.')
    return copy.deepcopy({'pending': pending, 'failure': failure,
        'sequence': state.get('sequence'), 'configuration': manager.service.state.get('configurationRevision'),
        'featureResults': state.get('featureResults'), 'forceRestartResult': state.get('forceRestartResult'),
        'drain': state.get('drain'), 'intentRevision': state.get('intentRevision', 0)})


def require_target(manager, args):
    witness = _state_witness(manager)
    _require(args.get('hostInstanceId') == manager.service.instance_id
             and all(args.get(key) == witness['pending'][key]
                     for key in ('revision', 'generation', 'attemptId')),
             'The selected host or pending application changed.')
    return witness


def _read(path):
    from .windows_read_files import open_file
    with open_file(path) as stream:
        raw = stream.read(128 * 1024 * 1024 + 1)
    _require(len(raw) <= 128 * 1024 * 1024, 'Application proof input is too large.')
    return raw


def _native_binding(manager):
    from .browser_entry import current_owner_sid
    from .windows_files import directory
    from .auth import data_identity
    from .application_generations import bootstrap_identity
    from .app_updates import components, installed_extras, validated_extras
    _require(sys.platform == 'win32', 'Manual handoff reconciliation requires native Windows evidence.')
    distribution = metadata.distribution('amplifier-unified')
    from .app_release_artifacts import source_revision
    _require(Path(distribution.locate_file('amplifier_web')) == Path(__file__).parent
             and Path(__file__).is_relative_to(Path(sys.prefix))
             and distribution.version == manager.running_identity['version']
             and source_revision(distribution) == manager.running_identity['revision'],
             'The serving application is not this installed package.')
    with directory(manager.home, owned_root=manager.home), directory(sys.prefix, owned_root=sys.prefix):
        home_stat, prefix_stat = Path(manager.home).stat(), Path(sys.prefix).stat()
        directories = [[str(value.st_dev), str(value.st_ino)] for value in (home_stat, prefix_stat)]
        _require(all(value[1] != '0' for value in directories))
        return {'ownerSid': current_owner_sid(), 'dataIdentity': data_identity(manager.home),
                'directories': directories, 'installation': bootstrap_identity(sys.prefix),
                'version': distribution.version, 'revision': manager.running_identity['revision'],
                'graph': components.installed_graph(), 'extras': validated_extras(installed_extras()),
                'direct': json.loads(distribution.read_text('direct_url.json') or 'null')}


def _staged(manager, pending):
    from . import app_release_artifacts as artifacts, app_component_graph as components
    from .app_updates import validated_extras
    folder = manager.directory / 'applications' / pending['revision'] / pending['generation']
    raw = _read(folder / 'validated.json')
    value = json.loads(raw)
    _require(all(value.get(key) == pending[key] for key in ('revision', 'generation', 'attemptId')))
    _require(value.get('version') == artifacts.stable_version(pending.get('latest'))
             and not value.get('featureSelection'))
    extras = validated_extras(value.get('extras'))
    _require(extras == validated_extras(pending.get('extras')))
    graph = components.validate_graph(value.get('componentGraph'))
    _require(components.digest(graph) == value.get('componentDigest'))
    artifact = artifacts.validate_descriptor(value.get('artifact'), value['version'])
    _require(Path(artifact['path']).parent == folder)
    artifacts.verify(artifact, value['version'], value['revision'])
    app = next((row for row in graph if row['name'] == 'amplifier-unified'), {})
    _require(app.get('revision') == value['revision'] and app.get('version') == value['version']
             and app.get('delivery') == {key: artifact[key] for key in ('url', 'sha256')})
    return value, hashlib.sha256(raw).hexdigest()


def _payload(proof):
    from .app_installation_proof import verify_payload
    from .app_release_artifacts import verify
    verify(proof['artifact'], proof['binding']['version'], proof['binding']['revision'])
    with _pinned_reader() as read:
        result = verify_payload(proof['artifact']['path'], metadata.distribution('amplifier-unified'),
            Path(sys.prefix), Path(sys.prefix) / 'Scripts', read_bytes=read, sha256=proof['artifact']['sha256'])
        # Repeat membership as well as contents while the first pass retains
        # its leaf handles. Unknown additions are not silently imported.
        _require(verify_payload(proof['artifact']['path'], metadata.distribution('amplifier-unified'),
            Path(sys.prefix), Path(sys.prefix) / 'Scripts', read_bytes=read,
            sha256=proof['artifact']['sha256']) == result)
        return result


@contextmanager
def _pinned_reader():
    """One off-loop audit: deny writes and recheck names, not a new registry."""
    from .windows_read_files import open_file, stream_signature
    with ExitStack() as stack:
        observed = {}
        def read(path):
            path = Path(path)
            if path not in observed:
                stream = stack.enter_context(open_file(path))
                observed[path] = (stream, stream_signature(stream))
            stream, signature = observed[path]
            _require(stream_signature(stream) == signature)
            stream.seek(0)
            raw = stream.read(128 * 1024 * 1024 + 1)
            _require(len(raw) <= 128 * 1024 * 1024)
            return raw
        yield read
        for path, (stream, signature) in observed.items():
            _require(stream_signature(stream) == signature)
            with open_file(path) as current:
                _require(stream_signature(current) == signature)


async def _offloop(function, *args):
    """A cancelled read-only audit must finish closing its handles, not commit."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


def _final_verify(manager, pending, proof):
    # manager.lock excludes the updater's other effects. Native readers reject
    # mutation during their I/O. This is not an atomic snapshot against a
    # same-account external writer after this read-only observation finishes.
    _require(_native_binding(manager) == proof['binding'])
    _require(_staged(manager, pending)[1] == proof['stagedDigest'])
    _require(_payload(proof) == proof['payload'])
    _require(_native_binding(manager) == proof['binding'])
    _require(_staged(manager, pending)[1] == proof['stagedDigest'])


async def _health(manager, binding):
    import aiohttp
    from .browser_entry import _owner_probe_targets, read_owner_control_token
    urls, pin = _owner_probe_targets(manager.home, manager.service.server_config)
    _require(isinstance(pin, aiohttp.Fingerprint) and len(pin.fingerprint) == 32)
    token = read_owner_control_token(manager.home, binding['ownerSid'])
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as client:
        for url in urls:
            _require(urlsplit(url).scheme == 'https' and urlsplit(url).hostname in {'127.0.0.1', '::1'})
            async with client.get(url, ssl=pin, allow_redirects=False,
                                  headers={'Authorization': 'Bearer ' + token}) as response:
                health = await response.json() if response.status == 200 else None
            if (isinstance(health, dict) and health.get('ok') is True
                    and health.get('app') == 'amplifier-unified'
                    and health.get('dataIdentity') == binding['dataIdentity']
                    and all(health.get(key) == manager.running_identity[key]
                            for key in ('version', 'revision', 'instanceId'))):
                return pin.fingerprint.hex()
    raise ValueError('The exact serving host has no authenticated pinned listener proof.')


async def qualify(manager, pending):
    """Produce proof from actual bytes, never from a caller's receipt assertion."""
    from . import app_updates, app_release_artifacts as artifacts
    from .app_feature_probe import DEPENDENCY_PROBE
    from .update_diagnostics import probe_record
    binding = await asyncio.to_thread(_native_binding, manager)
    staged, staged_digest = await asyncio.to_thread(_staged, manager, pending)
    current, previous = app_updates.version_tuple(binding['version']), app_updates.version_tuple(staged['version'])
    _require(current and previous and current >= previous)
    if current == previous:
        _require(binding['revision'] == staged['revision'])
        _require(binding['graph'] == [row for row in staged['componentGraph']
                                     if row['name'] != 'amplifier-unified'])
    else:
        _require(binding['revision'] != staged['revision'])
    _require(binding['extras'] == staged['extras'], 'Optional features changed during the manual handoff.')
    target = await app_updates.installed_target()
    _require(Path(target[2]).parent.parent == Path(sys.prefix)
             and target[3]['installation'] == str(Path(sys.prefix)))
    direct = binding['direct']
    _require(isinstance(direct, dict) and set(direct) == {'url', 'archive_info'})
    parsed = urlsplit(direct['url'])
    _require(parsed.scheme == 'file' and not parsed.netloc and not parsed.query and not parsed.fragment)
    path = unquote(parsed.path)
    if re.match(r'^/[A-Za-z]:/', path):
        path = path[1:]
    path = Path(path)
    _require(path.is_absolute() and path.name == artifacts.filename(binding['version']))
    # Authenticate this version's published wheel independently, including for
    # same-version handoffs. A newer install cannot borrow the old candidate.
    with tempfile.TemporaryDirectory(prefix='manual-proof-', dir=manager.directory) as temporary:
        published = await artifacts.prepare(Path(temporary), binding['version'], binding['revision'])
        descriptor = {**published, 'path': str(path)}
        archive_info = direct['archive_info']
        _require(isinstance(archive_info, dict))
        # uv may leave archive_info empty for an installed local wheel. Its
        # optional hash is corroboration, never the publication authority:
        # prepare authenticated the official bytes and _payload independently
        # checks that checksum and every installed member. Conflicts still fail.
        _require(not set(archive_info) - {'hash', 'hashes'})
        hashes = archive_info.get('hashes', {})
        _require(isinstance(hashes, dict))
        _require('sha256' not in hashes or hashes['sha256'] == published['sha256'])
        _require('hash' not in archive_info or archive_info['hash'] == 'sha256=' + published['sha256'])
        if current == previous:
            _require(published['sha256'] == staged['artifact']['sha256'])
        proof = {'binding': binding, 'artifact': descriptor, 'stagedDigest': staged_digest,
                 'launcher': str(target[1]), 'hostInstanceId': manager.service.instance_id}
        proof['payload'] = await _offloop(_payload, proof)
    output = await app_updates.process(target[2], '-I', '-c', app_updates.PROBE,
        *binding['extras'], **app_updates.browser_process_options(manager, binding['extras']), timeout=30)
    report = probe_record(output)
    _require(report and report.get('ok') is True and report.get('isolated') is True
             and report.get('stage') == 'complete' and report.get('version') == binding['version'])
    await app_updates.process(target[2], '-I', '-c', DEPENDENCY_PROBE, *binding['extras'], timeout=30)
    proof['tlsPin'] = await _health(manager, binding)
    _require(await app_updates.installed_target() == target)
    _require(await asyncio.to_thread(_native_binding, manager) == binding)
    _require(await _offloop(_payload, proof) == proof['payload'])
    return proof


async def reconcile(manager, args, request_id):
    state = manager.service.state['updates']
    row = state.setdefault(RESULTS, {}).get(request_id)
    if row and row.get('status') != 'queued':
        return copy.deepcopy(row)  # Durable exact retry: no download/probe/effect.
    async with manager.lock:
        try:
            _require(not manager.check_task or manager.check_task.done(),
                     'An application check is still in flight.')
            witness = require_target(manager, args)
            host = copy.deepcopy(manager.running_identity)
            proof = await qualify(manager, witness['pending'])
            proof_digest = await _offloop(_save_proof, manager, request_id, proof)
            await _offloop(_final_verify, manager, witness['pending'], proof)
            async with manager.service.lock:
                _require(require_target(manager, args) == witness)
                _require(manager.running_identity == host)
                result = {'requestId': request_id, 'status': 'succeeded', 'phase': PHASE,
                          'target': copy.deepcopy(args), 'verifiedAt': time.time(),
                          'version': proof['binding']['version'], 'revision': proof['binding']['revision']}
                before = copy.deepcopy(state)
                state[RESULTS][request_id] = result
                # Private payload is never copied into routine UI/agent state.
                # The public receipt retains one hash-bound proof reference.
                state['manualHandoff'] = {**result,
                    'proofDigest': proof_digest,
                    'refusal': witness['failure']}
                # Preserve every original diagnostic/candidate. This is neither
                # restart-ack nor archive eligibility for optional feature work.
                sequence = witness['sequence'] or {}
                state['sequence'] = {**sequence, 'stage': 'included', 'nextStage': 'included',
                    'install': sequence.get('install') is True, 'manualHandoff': request_id}
                state.update(pendingApp=None, phase='installed', error=None,
                    detail='The completed manual application handoff is verified. The original automatic restart refusal is retained.')
                manager._manual_handoff_verified = request_id
                from .update_health import publish
                publish(manager)
                try:
                    manager.service._publish_changes(globals={'updates'})
                except Exception:
                    # Publication can fail after SQLite committed. Read exactly
                    # this owner, not all stored conversations, before deciding.
                    try:
                        manager.service.db.rollback()
                        saved = _saved_updates(manager)
                    except Exception:
                        state.clear()
                        state.update(before)
                        manager._manual_handoff_verified = None
                        manager._manual_handoff_commit_unknown = True
                        state[RESULTS][request_id] = {'requestId': request_id, 'status': 'unknown',
                            'detail': 'The reconciliation commit could not be read. Update work is held until host reload; do not repeat this request.'}
                        return copy.deepcopy(state[RESULTS][request_id])
                    if (saved.get(RESULTS, {}).get(request_id) != result
                            or saved.get('manualHandoff') != state['manualHandoff']
                            or saved.get('pendingApp') is not None
                            or saved.get('sequence') != state['sequence']):
                        state.clear()
                        state.update(before)
                        manager._manual_handoff_verified = None
                        raise
                    # The exact commit landed. Keep the durable success, not a
                    # contradictory rejected result from a failed projection.
            manager.wakeup.set()
            return copy.deepcopy(result)
        except BaseException as error:
            # Only this request result changes on refusal. An uncertain verifier
            # never clears pending state or turns into an automatic retry.
            async with manager.service.lock:
                state[RESULTS][request_id] = {'requestId': request_id,
                    'status': 'interrupted' if isinstance(error, asyncio.CancelledError) else 'rejected',
                    'detail': 'Manual handoff verification did not complete. The pending update and original failure are unchanged.'}
                manager.service._publish_changes(globals={'updates'})
            if not isinstance(error, Exception):
                raise
            return copy.deepcopy(state[RESULTS][request_id])


def continuing(manager):
    state = manager.service.state['updates']
    sequence = state.get('sequence') or {}
    result = state.get('manualHandoff') or {}
    return (sequence.get('manualHandoff') == result.get('requestId') is not None
            and sequence.get('stage') in {'included', 'other'}
            and result.get('phase') == PHASE)


def _proof_path(manager, request_id):
    return manager.directory / 'manual-handoffs' / (hashlib.sha256(request_id.encode()).hexdigest() + '.json')


def _save_proof(manager, request_id, proof):
    from .host.config import write_private
    encoded = json.dumps(proof, sort_keys=True)
    write_private(_proof_path(manager, request_id), encoded, owned_root=manager.home)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _saved_updates(manager):
    from .state_records import restore, EMPTY_BASE
    row = manager.service.db.execute(
        "SELECT kind,id,value FROM state_records WHERE kind='global' AND id='updates'").fetchone()
    _require(row is not None, 'The durable update owner could not be read.')
    return restore(EMPTY_BASE, [row])['updates']


def _proof(manager, result):
    raw = _read(_proof_path(manager, result['requestId']))
    _require(hashlib.sha256(raw).hexdigest() == result['proofDigest'])
    return json.loads(raw)


async def verify_current(manager, *, full=False):
    """Reverify current identity even after its sequence completed; no intent."""
    state = manager.service.state['updates']
    if (state.get('manualHandoff') or {}).get('phase') != PHASE:
        return False
    result = copy.deepcopy(manager.service.state['updates']['manualHandoff'])
    sequence = copy.deepcopy(manager.service.state['updates'].get('sequence'))
    intent = manager.service.state['updates'].get('intentRevision', 0)
    proof = await asyncio.to_thread(_proof, manager, result)
    _require(manager.service.state['updates'].get('pendingReplacement') is None
             and manager.service.state['updates'].get('pendingRestart') is None
             and manager.service.state['updates'].get('pendingRollback') is None
             and not manager.service.state['updates'].get('pendingApp'))
    verified = getattr(manager, '_manual_handoff_verified', None)
    manager._manual_handoff_verified = None
    _require(await asyncio.to_thread(_native_binding, manager) == proof['binding'])
    if full or verified != result['requestId']:
        from .app_updates import installed_target
        target = await installed_target()
        _require(str(target[1]) == proof['launcher'])
        _require(await _offloop(_payload, proof) == proof['payload'])
        _require(await _health(manager, proof['binding']) == proof['tlsPin'])
        _require(await asyncio.to_thread(_native_binding, manager) == proof['binding'])
    _require(manager.service.state['updates'].get('manualHandoff') == result
             and manager.service.state['updates'].get('sequence') == sequence
             and manager.service.state['updates'].get('intentRevision', 0) == intent)
    _require(manager.service.state['updates'].get('pendingReplacement') is None
             and manager.service.state['updates'].get('pendingRestart') is None
             and manager.service.state['updates'].get('pendingRollback') is None
             and not manager.service.state['updates'].get('pendingApp'))
    manager._manual_handoff_verified = result['requestId']
    return True


async def verify_continuation(manager, *, full=False):
    """Current proof grants component work only to this unfinished sequence."""
    if not continuing(manager):
        return False
    return await verify_current(manager, full=full) and continuing(manager)