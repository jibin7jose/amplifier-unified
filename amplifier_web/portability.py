"""Shared UI/agent staged task transfer; destination execution is always explicit."""
import asyncio
import copy
from contextlib import asynccontextmanager
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid

from amplifier_portability import TransferNode
from amplifier_portability.protocol import confirm_durable, durable_directory
from amplifier_portability.capsule import capture_workspace, read_capsule, restore_workspace, write_capsule, _local_path
from amplifier_worktrees.git import digest
from . import portability_data as data
from .host_identity import local_host_identity
from .portability_policy import completion_receipt, effective_effort, readiness_policy, validate_policy


def controller_available():
    return sys.platform != 'win32'


UNAVAILABLE_REASON = 'Task transfer is unavailable on native Windows. Retained evidence is read-only; ownership and directory durability are not qualified. Nothing is replayed.'


class RetainedTransfers:
    """Read ownership evidence without keys, recovery, locks or directory creation."""
    identity = None

    def __init__(self, directory):
        self.directory = Path(directory).absolute()

    def records(self, sid=None):
        directory = self.directory / 'receipts'
        for path in reversed([directory, *directory.parents]):
            try:
                info = path.lstat()
            except FileNotFoundError:
                return []
            if (not stat.S_ISDIR(info.st_mode) or
                    getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError('Retained transfer storage must be a real directory')
        rows = []
        for path in sorted(directory.iterdir()):
            if path.suffix != '.json':
                continue
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024 or
                    getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError('Retained transfer evidence must be a bounded regular file')
            if sys.platform == 'win32':
                # A cancelled/active journal can admit ordinary writes. Read it
                # through the existing private-file ACL/handle boundary, never
                # by treating a copied, unprotected Windows file as authority.
                from .windows_files import directory as private_directory
                with private_directory(directory, owned_root=self.directory) as pinned:
                    raw = pinned.read_private(path.name, limit=1024 * 1024)
                row = json.loads(raw)
            else:
                row = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(row, dict) or row.get('id') != path.stem or
                    str(uuid.UUID(row['id'])) != row['id'] or
                    not isinstance(row.get('sessionId'), str) or not row['sessionId'] or
                    type(row.get('generation')) is not int or row['generation'] < 1 or
                    type(row.get('createdAt')) not in (int, float) or not math.isfinite(row['createdAt']) or
                    row.get('phase') not in {'preparing', 'prepared', 'releasing', 'released',
                        'staging', 'ready', 'activating', 'active', 'cancelled', 'discarded', 'unknown'}):
                raise ValueError('Malformed retained transfer ownership evidence')
            rows.append(row)
        return [row for row in rows if sid is None or row['sessionId'] == sid]

    def get(self, identity):
        row = next((row for row in self.records() if row['id'] == identity), None)
        if row is None:
            raise ValueError('Retained transfer is not available')
        return row

    def fenced(self, sid):
        rows = self.records(sid)
        latest = max(rows, key=lambda row: (row['generation'], row['createdAt'])) if rows else None
        return latest is not None and latest['phase'] not in {'active', 'cancelled'}

    def peers(self):
        return None  # Pairing trust is not inspected on an unsupported controller.


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    receipt = {**sid, 'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1}}
    return {
        'portability.inspect': ('Inspect this host identity, paired destinations and durable transfer receipts. No task or provider starts.', schema(sid, [])),
        'portability.export': ('Stage this task for a paired host after reviewed content approval. Fence and save its writer; export canonical history, output lineage and explicit configuration intent with bounded Git changes. No secrets/settings or work replay. Pending/unknown transfers remain fenced.', schema({**sid, 'destination': string(64), 'sourceRevision': string(64), 'expectedExecutionRevision': {'type': 'integer', 'minimum': 0}, 'mode': {'enum': ['clean', 'carry_dirty']}, 'reviewedContent': {'const': True}})),
        'portability.stage': ('Verify a signed task package from a paired host, stage a fresh checkout against an already provisioned exact repository commit, and probe destination-owned provider/account/runtime. Returns a signed readiness file; cannot start the task.', schema({'path': string(4000), 'repository': string(4000)})),
        'portability.release': ('Commit irreversible source ownership release against the destination signed readiness receipt. Preserve source history in a private archive. Source stays fenced if acknowledgement is lost; no automatic rollback.', schema({**receipt, 'path': string(4000)})),
        'portability.activate': ('Activate the staged task only with its exact signed source release. Retain task/native/output identities; never resume input, schedules or uncertain effects automatically.', schema({**receipt, 'path': string(4000)})),
        'portability.cancel': ('Deliberately cancel an unreleased source transfer and retain its evidence. Impossible once release has begun. This restores source admission only; no input or uncertain effect is replayed.', schema({**receipt, 'evidence': {**string(4000), 'minLength': 1}})),
        'portability.discard': ('Retain a signed source cancellation for an unactivated destination stage. Leaves files and native fence intact; cannot execute the staged task or undo a released owner.', schema({**receipt, 'path': string(4000)})),
        'portability.evidence': ('Read retained transfer evidence as bounded JSON text without executing, replaying or reclassifying unknown work.', schema({**sid, 'id': string(100), 'section': {'enum': ['operations', 'operationRequests', 'liveJobs', 'workers', 'approvals', 'questions', 'schedules', 'scheduleRuns']}, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 4000}}, ['sessionId', 'id', 'section'])),
    }


def native_store(session):
    from amplifier_foundation.session import SharedSessionStore
    if not hasattr(SharedSessionStore, 'confirm_transfer_commit'):
        raise ValueError('Install Foundation with the durable transfer fence on every participating runtime first')
    return SharedSessionStore(session['workspace'], session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'])


def retained_native_fence(session):
    """A retained marker denies writes even when runtime admission is unavailable."""
    from .automatic_history import directory
    from .session_files import project_slug
    projects = {session['nativeProject']} if session.get('nativeProject') else set()
    if session.get('workspace'):
        projects.add(project_slug(session['workspace']))
    identities = {session.get('nativeIdentity'), session.get('runtimeSessionId')} - {None, ''}
    if not identities:
        identities.add(session['id'])
    # Both anchors matter when a history row and its runtime alias differ.
    for project in projects:
        for identity in identities:
            marker = directory({
                'nativeProject': project, 'nativeIdentity': identity,
                'runtimeSessionId': session.get('runtimeSessionId'),
                **({'id': session['id']} if 'id' in session else {}),
            }) / 'transfer-fence.json'
            try:
                marker.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ValueError('Cannot inspect this task\'s transfer fence.') from exc
            # Contents, permissions or a dangling marker link cannot grant
            # admission. Foundation validates evidence during actual transfer.
            return True
    return False


class Portability:
    def __init__(self, app):
        self.app = app
        self.available = controller_available()
        if self.available:
            self.node = TransferNode(app.data_dir / 'portability', local_host_identity()['label'])
            self.node.recover()
        else:
            self.node = RetainedTransfers(app.data_dir / 'portability')
        self.jobs = set()
        self.mutations = asyncio.Lock()

    def availability(self):
        return {'available': self.available, **({} if self.available else {
            'reason': UNAVAILABLE_REASON, 'historicalOnly': True})}

    def fenced(self, sid):
        if self.node.fenced(sid):
            return True
        session = next((row for row in self.app.state['sessions'] if row['id'] == sid), None)
        if session is None and getattr(self.app, 'directory', None) is not None:
            # Dormancy and hiding a workspace cannot remove a native transfer
            # fence. This point lookup needs identity/placement metadata only.
            session = self.app.directory.resolve(sid, include_hidden_workspaces=True)
        if session:
            workspace = session.get('workspace')
            if not self.available or session.get('nativeProject') or not workspace or not Path(workspace).expanduser().is_dir():
                # Indexed history remains addressable without an existing
                # workspace, including legacy worker IDs a runtime cannot use.
                # Marker presence only denies writes; never infer permission
                # from unreadable or malformed transfer evidence.
                if retained_native_fence(session):
                    return True
                if not self.available:
                    return False
                if not workspace or not Path(workspace).expanduser().is_dir():
                    return False
            from amplifier_foundation.session import SharedSessionStore
            if hasattr(SharedSessionStore, 'transfer_fence'):
                try:
                    store = SharedSessionStore(workspace, session.get('runtimeSessionId') or session.get('nativeIdentity') or sid)
                except ValueError:
                    if session.get('nativeProject'):
                        # Legacy history IDs and disappeared workspaces cannot
                        # construct a runtime store; known markers were checked.
                        return False
                    raise
                return store.transfer_fence() is not None
        return False

    def write_context(self, sid):
        """Capture without yielding; compare under the app lock before writing."""
        if self.fenced(sid):
            raise ValueError('This task is fenced for transfer; it cannot change or start work here.')
        rows = self.node.records(sid)
        return max(rows, key=lambda row: (row['generation'], row['createdAt']))['id'] if rows else None

    def require_voice_closed(self, sid):
        voice = self.app.state.get('voice', {})
        manager = getattr(self.app, 'voice_service', None)
        call = getattr(manager, 'call', None)
        live_call = (call and call.session_id == sid and
            (not call.closed or (getattr(call, 'closing', False) and not getattr(call, 'close_result', None))))
        if live_call or (voice.get('sessionId') == sid and voice.get('status') not in {None, 'disconnected', 'idle', 'ended', 'error', 'failed'}):
            raise ValueError('End the active voice call before moving the task')

    def sync(self):
        receipts = self.node.records()
        by_session = {}
        for row in receipts:
            by_session.setdefault(row['sessionId'], []).append(row)
        self.app.state['portability'] = {'host': self.node.identity, 'receipts': receipts,
                                         'availability': self.availability()}
        for session in self.app.state['sessions']:
            rows = by_session.get(session['id'])
            if rows:
                # Share only the read, not mutable projection values or authority.
                # Fence checks still read current ownership evidence from disk.
                session['portability'] = {'host': self.node.identity, 'receipts': copy.deepcopy(rows),
                                          'fenced': self.fenced(session['id']), 'availability': self.availability()}
                if self.fenced(session['id']):
                    session['configurationBusy'] = True

    def changed(self):
        self.sync()
        self.app._publish_full(reason='Task transfer reconciles imported sessions, ownership fences and schedules')

    def review_schedules(self, sid):
        for schedule in self.app.schedules.store.list(sid):
            self.app.schedules.store.review(sid, schedule['id'],
                'Task transfer requires explicit schedule review before any future execution.', time.time())

    def read(self, sid, identity):
        row = self.node.get(identity)
        if row['sessionId'] != sid:
            raise ValueError('This transfer belongs to another task')
        return row

    @asynccontextmanager
    async def publishing_guard(self, sid):
        publishing = getattr(self.app, 'publishing', None)
        if publishing is None:
            yield
            return
        # Match publishing/backup/deletion lock order: publishing, then app.
        async with publishing.lock:
            if publishing.owns_records(sid):
                raise ValueError('This task owns retained publishing state on its current host. Transfer of publishing controls is not supported; keep managing publishing from the source task.')
            yield

    async def dispatch(self, action, args, origin, command_id):
        # Journal inspection is supported; capsule evidence still uses the
        # protocol's POSIX contained reader and is not ported by this gate.
        if not self.available and action != 'portability.inspect':
            return {'accepted': False, 'effect': 'none', 'code': 'controller_unavailable',
                    **self.availability()}
        if action == 'portability.evidence':
            row = self.read(args['sessionId'], args['id'])
            path = row.get('package') or row['destinationState']['package']
            body = read_capsule(Path(path))['body']
            from amplifier_portability.protocol import encoded
            if digest(encoded(body)) != row['capsuleHash']:
                raise ValueError('The transfer evidence differs from its signed receipt')
            contents = json.dumps(body['payload']['observations'][args['section']], ensure_ascii=False)
            offset, limit = args.get('offset', 0), args.get('limit', 4000)
            return {'text': contents[offset:offset+limit], 'nextOffset': offset+limit if offset+limit < len(contents) else None,
                    'historicalOnly': True, 'inputsReplayed': False}
        if action == 'portability.inspect':
            sid = args.get('sessionId')
            if sid: self.app._session(sid)
            result = {'host': self.node.identity, 'peers': self.node.peers(), 'receipts': self.node.records(sid),
                      'availability': self.availability()}
            if not self.available:
                return result
            async with self.app.lock:
                if sid: self.app._session(sid)['portabilityInspection'] = result
                self.changed()
            return result
        async with self.mutations:
            if action == 'portability.export':
                async with self.publishing_guard(args['sessionId']):
                    return await self.export(args, command_id or str(uuid.uuid4()))
            if action == 'portability.stage':
                return await self.stage(args)
            if action == 'portability.release':
                async with self.publishing_guard(args['sessionId']):
                    return await self.release(args)
            if action == 'portability.activate':
                return await self.activate(args)
            if action == 'portability.cancel':
                return await self.cancel(args)
            if action == 'portability.discard':
                row = self.read(args['sessionId'], args['id'])
                result = self.node.discard(row['id'], read_capsule(Path(args['path'])), args['expectedRevision'])
                async with self.app.lock: self.changed()
                return result
            raise ValueError('Unknown portability action')

    async def export(self, args, command_id):
        session = self.app._session(args['sessionId'])
        native_store(session)  # Capability preflight before changing admission.
        if session.get('executionRevision', 0) != args['expectedExecutionRevision']:
            raise ValueError('The execution association changed; inspect it again')
        async with self.app.lock:
            self.require_voice_closed(session['id'])
        if session.get('configurationBusy') and not self.node.records(session['id']):
            raise ValueError('Another task configuration change is unresolved')
        # Reject known nonportable contents before stopping a writer. A second
        # exact capture after release remains authoritative for a running task.
        if not self.node.records(session['id']) or not self.fenced(session['id']):
            await self.app.history.ensure_loaded(session['id'])
            await asyncio.to_thread(capture_workspace, session.get('workingDirectory') or session['workspace'], args['sourceRevision'], args['mode'])
            async with self.app.lock:
                data.capture(self.app, session)
        async with self.app.lock:
            # Voice connect claims state.voice under this same lock. Recheck
            # after the awaited history/Git preflight, then fence atomically.
            self.require_voice_closed(session['id'])
            row = self.node.begin(session['id'], args['destination'], command_id, args)
            if row.get('duplicate'):
                return row
            session['configurationBusy'] = True
            self.review_schedules(session['id'])
            self.changed()
        job = asyncio.create_task(self.finish_export(row, copy.deepcopy(session), args))
        self.jobs.add(job); job.add_done_callback(self.jobs.discard)
        return row

    async def finish_export(self, row, session, args):
        try:
            await asyncio.sleep(0)
            if not self.app.runtime:
                raise ValueError('A runtime writer release is required')
            evidence = await self.app.runtime.quiesce_for_handoff(session, row['id'], transfer_destination=row['destination'])
            if not evidence.get('quiesced') or not evidence.get('transferFenced'):
                raise ValueError('The native writer did not confirm a durable transfer fence')
            # Quiescence stops producers, but cancelled bridge callers can leave
            # shielded journal writes queued. Include their evidence before the
            # snapshot; timeout/failure keeps this transfer unknown and fenced.
            pending = tuple(self.app.operations.pending)
            if pending:
                await asyncio.wait_for(asyncio.gather(*(asyncio.shield(task) for task in pending)), 30)
            await self.app.history.ensure_loaded(session['id'])
            workspace = await asyncio.to_thread(capture_workspace, session.get('workingDirectory') or session['workspace'], args['sourceRevision'], args['mode'])
            async with self.app.lock:
                current = self.app._session(session['id'])
                payload = data.capture(self.app, current)
                payload['workspace'] = workspace
                result = self.node.prepared(row['id'], payload)
                result['releaseEvidence'] = {key: evidence.get(key) for key in ('nativeSessionId', 'quiesced', 'transferFenced', 'inputsReplayed')}
                self.node.save(result)
                self.changed()
        except BaseException:
            async with self.app.lock:
                self.node.unknown(row['id']); self.changed()
            raise_if_cancelled()

    def destination_configuration(self, payload, workspace):
        # The probe runs from the destination repository's settings scope. No
        # transferred source overrides, settings or credentials are mounted.
        from .setup import SetupManager
        # Policy admission precedes checkout creation. Resolve destination-owned
        # ancestor settings without creating the checkout just to inspect them.
        manager = SetupManager(self.app.data_dir, allow_missing_workspace=True)
        intent = payload['intent']; selection = intent['selection']
        provider = selection.get('instance') or selection.get('provider')
        config = manager.config(workspace)
        if config.active_bundle != intent['bundle']:
            raise ValueError('Destination bundle differs from saved configuration intent; configure it locally first')
        row = next((r for r in config.providers if (r.get('id') or r.get('instance_id') or r['module'].removeprefix('provider-')) == provider), None)
        if row is None:
            raise ValueError('Configure the saved provider instance on the destination first')
        if row.get('enabled') is False:
            raise ValueError('The saved provider instance is disabled on the destination')
        return provider, row, config

    def destination_policy(self, payload, workspace):
        provider, row, _ = self.destination_configuration(payload, workspace)
        selection = payload['intent']['selection']
        return readiness_policy(row['module'], provider, selection['model'],
                                effective_effort(selection, row.get('config', {})))

    @staticmethod
    def require_policy_checks(checks, policy):
        if (checks.get('readinessPolicyHash') != digest(policy)
                or digest(checks.get('readinessPolicy')) != digest(policy)):
            raise ValueError('Destination check did not verify the admitted readiness policy')

    async def destination_checks(self, payload, workspace, policy):
        policy = validate_policy(policy)
        provider, row, config = self.destination_configuration(payload, workspace)
        selection = payload['intent']['selection']
        if (provider != policy['providerInstance'] or row['module'] != policy['providerModule']
                or selection['model'] != policy['model']
                or (selection.get('effort') is not None and selection['effort'] != policy['reasoningEffort'])):
            raise ValueError('Saved selection differs from the admitted readiness policy')
        from .setup import environment_credential
        from .runtime import RuntimeManager
        raw = copy.deepcopy(row.get('config', {}))
        credential = environment_credential(row['module'], raw)
        if not raw.get(credential['field']) and credential['available']:
            raw[credential['field']] = '${' + credential['envVar'] + '}'
        request = {'module': row['module'], 'config': raw, 'model': policy['model'], 'readinessPolicy': policy,
                   'source': config.module_sources.get(row['module']) or row.get('source'), 'registryHome': str(config.registry_home)}
        source = request['source']
        if source and not source.startswith('git+https://'):
            candidate = Path(source.removeprefix('file://')).expanduser()
            if not candidate.is_absolute() or candidate.resolve().is_relative_to(Path(workspace).resolve()):
                raise ValueError('The destination provider must use installed code or an independent destination-owned source')
        probe_directory = self.node.directory / 'probe'
        probe_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = RuntimeManager()._command()[:-1] + [str(Path(__file__).with_name('portability_probe.py'))]
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=probe_directory,
            env={**os.environ, 'AMPLIFIER_WEB_HOME': str(self.app.data_dir)}, start_new_session=True)
        try:
            # The admitted policy owns any explicit provider deadline. An outer
            # wall-clock default must not interrupt a healthy no-deadline probe.
            # Caller cancellation still reaches the owned-process cleanup below.
            output, _ = await process.communicate(json.dumps(request).encode())
            result = json.loads(output)
            if isinstance(result, dict) and result.get('code') == 'single_attempt_unsupported':
                raise ValueError('This provider version cannot perform a bounded readiness check; update it or use a supported provider.')
            if (process.returncode or result.get('error') or result.get('accountVerified') is not True
                    or result.get('runtimeTransferFence') is not True or result.get('method') != 'provider.complete'
                    or result.get('providerModule') != policy['providerModule'] or result.get('model') != policy['model']
                    or result.get('reasoningEffort') != policy['reasoningEffort']
                    or result.get('readinessPolicyHash') != digest(policy)):
                raise ValueError('Destination provider access or native runtime verification failed; inspect local configuration')
        finally:
            if process.returncode is None:
                import signal
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                await process.wait()
        return {'runtimeVerified': True, 'accountVerified': True, 'nativeFenceVerified': True,
                'credentialsOrigin': 'destination', 'method': result['method'],
                'accountVerificationScope': result['accountVerificationScope'],
                'provider': provider, 'model': policy['model'], 'reasoningEffort': policy['reasoningEffort'],
                'readinessPolicy': policy, 'readinessPolicyHash': digest(policy),
                'completionReceipt': completion_receipt(result.get('completionReceipt'), policy),
                'intentHash': digest(payload['intent']), 'checkedAt': time.time()}

    async def stage(self, args):
        envelope = read_capsule(Path(args['path']))
        body = self.node.verify(envelope, kind='capsule')
        payload = body['payload']; data.validate(payload)
        if body['sessionId'] != payload['session']['id']:
            raise ValueError('Task identity does not match its package')
        # A retry is a receipt read, even though native staging already exists.
        if self.node.path(body['id']).exists():
            return self.node.receive(envelope, args)
        existing = self.app.directory.resolve(body['sessionId'])
        if existing and not self.node.fenced(body['sessionId']):
            raise ValueError('This destination already has a writable task with that identity')
        from .session_files import amplifier_home
        identity = payload['nativeIdentity']
        if any(path for name in ('transcript.jsonl', 'transcript.jsonl.backup')
               for path in (amplifier_home() / 'projects').glob('*/sessions/' + identity + '/' + name)):
            raise ValueError('This native task already has canonical history on the destination')
        target = self.node.directory / 'checkouts' / body['id']
        policy = self.destination_policy(payload, target)
        row = self.node.receive(envelope, args, readiness_policy=policy)
        if row.get('duplicate'):
            return row
        try:
            restored = await asyncio.to_thread(restore_workspace, payload['workspace'], args['repository'], target)
            session = {**copy.deepcopy(payload['session']), 'workspace': str(target), 'workingDirectory': str(target),
                       'runtimeSessionId': identity, 'nativeIdentity': identity}
            store = native_store(session)
            # Staging must also fence standalone native consumers before any
            # transcript becomes discoverable on this host.
            held = store.acquire(app='task-transfer-stage')
            try:
                held.fence_transfer(row['id'], self.node.identity['id'], role='destination')
            finally:
                held.release()
            # History stays solely in the private signed package until source
            # release. Native history catalogs must not discover a staged copy.
            package = self.node.directory / 'packages' / (row['id'] + '.json')
            write_capsule(package, envelope)
            checks = await self.destination_checks(payload, target, policy)
            self.require_policy_checks(checks, policy)
            result = self.node.ready(row['id'], {'workspace': str(target), 'nativeIdentity': identity, 'package': str(package), 'checkout': restored}, checks)
            ready_path = self.node.directory / 'exchange' / (row['id'] + '.ready.json')
            write_capsule(ready_path, result['readyReceipt'])
            result['receiptPath'] = str(ready_path)
            self.node.save(result)
            async with self.app.lock:
                self.changed()
            return result
        except BaseException:
            self.node.unknown(row['id'])
            raise

    async def release(self, args):
        row = self.read(args['sessionId'], args['id'])
        ready = read_capsule(Path(args['path']))
        body = self.node.verify(ready, kind='ready', signer=row['destination'])
        self.node.match(row, body)
        if row['phase'] != 'released':
            session = self.app._session(row['sessionId'])
            package = read_capsule(Path(row['package']))
            from amplifier_portability.protocol import encoded
            if digest(encoded(package['body'])) != row['capsuleHash']:
                raise ValueError('The staged source package differs from its receipt')
            payload = package['body']['payload']
            if row['phase'] == 'prepared':
                async with self.app.lock:
                    # UI edits and local file changes after staging must not
                    # disappear at the irreversible ownership boundary.
                    current = data.capture(self.app, session)
                    if digest(current) != digest({key: value for key, value in payload.items() if key != 'workspace'}):
                        raise ValueError('Task history or outputs changed after export; keep both hosts fenced and inspect')
                    capture_workspace(session.get('workingDirectory') or session['workspace'], payload['workspace']['sourceRevision'], payload['workspace']['mode'])
            archive = self.node.directory / 'archives' / row['id']
            row = self.node.releasing(row['id'], ready, args['expectedRevision'], archive)
            # Source is irreversibly fenced in Foundation before its signed
            # release leaves this host. Originals leave native discovery but
            # remain private, byte-identical archival evidence.
            store = native_store(session)
            fence = store.transfer_fence()
            if not fence or fence['transfer_id'] != row['id'] or fence['role'] != 'source':
                raise ValueError('Source native fence no longer matches this release')
            if fence['phase'] != 'committed':
                held = store.acquire_transfer(row['id'], app='task-transfer-release')
                try:
                    held.commit_transfer()
                finally:
                    held.release()
            else:
                store.confirm_transfer_commit(row['id'], app='task-transfer-release')
            source = data.native_directory(self.app, session)
            durable_directory(archive)
            try:
                for name, value in payload['native'].items():
                    path = _local_path(source / name)
                    destination = _local_path(archive / name)
                    expected = data.decode_file(value)
                    if destination.exists():
                        if destination.read_bytes() != expected or path.exists():
                            raise ValueError('Source archive conflicts with the reviewed transfer')
                        confirm_durable(destination)
                        continue
                    if path.read_bytes() != expected:
                        raise ValueError('Source native history changed before archival')
                    durable_directory(destination.parent)
                    os.replace(path, destination)
                    for parent in (path.parent, destination.parent):
                        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                        try: os.fsync(fd)
                        finally: os.close(fd)
            except BaseException:
                self.node.unknown(row['id'])
                raise
        else:
            native_store(self.app._session(row['sessionId'])).confirm_transfer_commit(row['id'], app='task-transfer-release')
        result = self.node.release(row['id'], ready, row['revision'])
        path = self.node.directory / 'exchange' / (row['id'] + '.release.json')
        write_capsule(path, result['releaseCertificate'])
        result['receiptPath'] = str(path)
        self.node.save(result)
        async with self.app.lock:
            session = self.app._session(row['sessionId'])
            session.update(status='idle', deferRuntimeUntilInteraction=True, configurationBusy=True)
            self.changed()
        return result

    async def activate(self, args):
        row = self.read(args['sessionId'], args['id'])
        certificate = read_capsule(Path(args['path']))
        authorization = self.node.verify(certificate, kind='release', signer=row['source'])
        self.node.match(row, authorization)
        if authorization['readyHash'] != digest(row['readyReceipt']):
            raise ValueError('Source release does not acknowledge this exact destination readiness')
        if row['phase'] == 'unknown' and row.get('previousPhase') == 'activating':
            # Activation and its unknown outcome each advance the ready
            # revision once. Only the exact saved attempt can be acknowledged;
            # an uncertain provider request must never be submitted again.
            if row.get('releaseCertificate') != certificate or args['expectedRevision'] != row['revision'] - 2:
                raise ValueError('This activation attempt has different contents; inspect its uncertain receipt')
            return {**row, 'duplicate': True}
        # Recheck destination credentials/runtime immediately before ownership
        # admission. Import readiness is not a permanent account guarantee.
        destination = row['destinationState']
        envelope = read_capsule(Path(destination['package']))
        body = self.node.verify(envelope, kind='capsule', signer=row['source'])
        from amplifier_portability.protocol import encoded
        if digest(encoded(body)) != row['capsuleHash']:
            raise ValueError('The staged task package changed after readiness')
        payload = body['payload']; data.validate(payload)
        if row['phase'] == 'active':
            result = self.node.activating(row['id'], certificate, args['expectedRevision'])
            session = self.app._session(row['sessionId'])
            self.finish_native_activation(row, session)
            async with self.app.lock:
                session['configurationBusy'] = False; self.changed()
            return result
        if row['phase'] != 'ready' or row['revision'] != args['expectedRevision']:
            raise ValueError('Inspect the current ready transfer before activation')
        checks = self.node.readiness_checks(row)
        policy = validate_policy(row.get('readinessPolicy'), row.get('readinessPolicyHash'))
        if not row.get('readinessPolicyHash'):
            raise ValueError('Legacy readiness has no admitted policy; it cannot consume a new probe budget')
        self.require_policy_checks(checks, policy)
        capture_workspace(destination['workspace'], payload['workspace']['sourceRevision'], payload['workspace']['mode'])
        async with self.app.lock:
            data.preflight_install(self.app, payload)
        # Record the attempt before a provider probe can have external effects.
        # A crash here recovers as unknown, never as another ready admission.
        row = self.node.activating(row['id'], certificate, args['expectedRevision'])
        try:
            checks = await self.destination_checks(payload, Path(destination['workspace']), policy)
            self.require_policy_checks(checks, policy)
            capture_workspace(destination['workspace'], payload['workspace']['sourceRevision'], payload['workspace']['mode'])
            async with self.app.lock:
                data.preflight_install(self.app, payload)
            from .session_files import project_slug
            previous = self.app.worktrees.saved_owner(row['sessionId']) or {}
            session = {**copy.deepcopy(payload['session']), 'workspace': destination['workspace'],
                       'workingDirectory': destination['workspace'], 'runtimeSessionId': payload['nativeIdentity'],
                       'nativeIdentity': payload['nativeIdentity'], 'nativeProject': project_slug(destination['workspace']),
                       'executionRevision': max(payload['session'].get('executionRevision', 0), previous.get('executionRevision', 0)) + 1,
                       'executionHost': local_host_identity(),
                       'historyManaged': False, 'historyLoaded': True, 'shared': True, 'status': 'idle',
                       'workers': [], 'approvals': [], 'configurationBusy': True, 'deferRuntimeUntilInteraction': True,
                       'ownership': {'status': 'available'}, 'portabilityOrigin': payload['origin'],
                       'portabilityEvidence': {'transferId': row['id'], 'historicalOnly': True,
                           'counts': {key: len(records) for key, records in payload['observations'].items()}}}
            native = data.native_directory(self.app, session)
            for name, value in payload['native'].items():
                path = native / name
                if path.exists() or path.is_symlink():
                    raise ValueError('Native destination history changed after staging; originals were preserved')
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                raw = data.decode_file(value)
                if name in {'metadata.json', 'metadata.json.backup'}:
                    metadata = json.loads(raw)
                    metadata.update(working_dir=destination['workspace'], portabilityOrigin=payload['origin'])
                    raw = json.dumps(metadata, ensure_ascii=False).encode()
                write_private_bytes(path, raw)
            local = _local_path(self.app.data_dir / 'sessions' / payload['nativeIdentity'])
            # A returning task must not silently remount stale local session
            # overrides. Retain them locally; only reviewed portable controls
            # are restored over the destination's own global/project settings.
            if local.exists():
                previous_local = _local_path(self.node.directory / 'archives' / row['id'] / 'previous-local-state')
                durable_directory(previous_local.parent)
                if previous_local.exists():
                    raise ValueError('A prior local-state archive already exists; inspect activation before continuing')
                os.replace(local, previous_local)
                durable_directory(local.parent)
                durable_directory(previous_local.parent)
            durable_directory(local)
            controls = copy.deepcopy(payload['intent']['controls'])
            controls['selection'] = copy.deepcopy(payload['intent']['selection'])
            write_private_bytes(local / 'control-state.json', json.dumps(controls).encode())
            if payload.get('contextCheckpoint'):
                write_private_bytes(local / 'context-checkpoint.json', data.decode_file(payload['contextCheckpoint']))
            async with self.app.lock:
                self.review_schedules(session['id'])
                data.install_outputs(self.app, payload)
                data.install_receipts(self.app, payload)
                self.app.state['sessions'] = [s for s in self.app.state['sessions'] if s['id'] != session['id']] + [session]
                self.app.state['canvasArtifacts'] = [r for r in self.app.state.get('canvasArtifacts', []) if r.get('sessionId') != session['id']] + payload['canvas']
                self.changed()
            # Commit destination authority while the native fence still blocks
            # all execution. An explicit retry may finish only this final clear.
            result = self.node.active(row['id'])
            self.finish_native_activation(row, session)
            async with self.app.lock:
                session['configurationBusy'] = False
                self.changed()
            return result
        except BaseException:
            self.node.unknown(row['id'])
            raise

    def finish_native_activation(self, row, session):
        store = native_store(session)
        fence = store.transfer_fence()
        if fence is None:
            return  # Exact committed activation retry; never re-install history.
        if fence['role'] != 'destination' or fence['transfer_id'] != row['id']:
            raise ValueError('Destination native fence differs from the admitted transfer')
        held = store.acquire_transfer(row['id'], app='task-transfer-activate')
        try:
            held.clear_transfer()
        finally:
            held.release()

    async def cancel(self, args):
        row = self.read(args['sessionId'], args['id'])
        session = self.app._session(row['sessionId'])
        store = native_store(session)
        fence = store.transfer_fence()
        if fence and (fence['transfer_id'] != row['id'] or fence['role'] != 'source' or fence['phase'] != 'staged'):
            raise ValueError('The native source fence cannot be cancelled')
        result = self.node.cancel(row['id'], args['expectedRevision'], args['evidence'])
        if fence:
            held = store.acquire_transfer(row['id'], app='task-transfer-cancel')
            try: held.clear_transfer()
            finally: held.release()
        path = self.node.directory / 'exchange' / (row['id'] + '.cancel.json')
        write_capsule(path, result['cancelCertificate'])
        result['receiptPath'] = str(path); self.node.save(result)
        async with self.app.lock:
            session.update(configurationBusy=False, status='idle', deferRuntimeUntilInteraction=True)
            self.changed()
        return result

    async def close(self):
        for job in list(self.jobs):
            job.cancel()
        if self.jobs:
            await asyncio.gather(*self.jobs, return_exceptions=True)


def raise_if_cancelled():
    import sys
    if isinstance(sys.exception(), asyncio.CancelledError):
        raise sys.exception()


def write_private_bytes(path, raw):
    from .host.storage import SessionStore
    path = _local_path(path)
    durable_directory(path.parent)
    SessionStore._atomic(path, raw.decode('utf-8'))
    confirm_durable(path)
