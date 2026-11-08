"""Conversation policy over the portable, local publishing lifecycle."""

import asyncio
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import stat
import sys
from pathlib import Path

from amplifier_publishing import Publisher, PublishingError
from amplifier_publishing.remote import service_identity

from .host_identity import local_host_identity, require_local_host
from .publishing_targets import PublishingTargets, definitions as target_definitions


def controller_available():
    return sys.platform != 'win32'


UNAVAILABLE_REASON = 'Publishing is unavailable on native Windows. Lifetime locking, contained file access and durability are not qualified. Retained records are historical; listener liveness is unconfirmed.'


class RetainedPublisher:
    """Passive SQLite evidence, not a publisher or a release-byte verifier."""
    def __init__(self, root):
        self.root = Path(root).absolute()

    @contextmanager
    def connection(self):
        for directory in reversed([self.root, *self.root.parents]):
            try:
                info = directory.lstat()
            except FileNotFoundError:
                yield None
                return
            if (not stat.S_ISDIR(info.st_mode) or
                    getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise PublishingError('retained_unreadable', 'Retained publishing storage must be a real directory')
        path = self.root / 'state.sqlite3'
        try:
            before = path.lstat()
        except OSError as exc:
            raise PublishingError('retained_unreadable', 'Retained publishing database is unavailable; no records were omitted') from exc
        if (not stat.S_ISREG(before.st_mode) or
                getattr(before, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise PublishingError('retained_unreadable', 'Retained publishing database must be a regular file')

        def unchanged():
            now = path.lstat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns):
                raise PublishingError('retained_unreadable', 'Retained publishing evidence changed during inspection')
            for suffix in ('-wal', '-journal'):
                try:
                    path.with_name(path.name + suffix).lstat()
                except FileNotFoundError:
                    continue
                # Immutable avoids writes to WAL shared memory, but cannot read
                # WAL. Refuse rather than silently omit uncheckpointed records.
                raise PublishingError('retained_unreadable', 'Retained publishing has an uncheckpointed journal; inspect it on a supported controller')

        unchanged()
        try:
            db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
            try:
                db.execute('PRAGMA query_only=ON')
                db.execute('PRAGMA trusted_schema=OFF')
                yield db
                unchanged()
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise PublishingError('retained_unreadable', 'Cannot read retained publishing evidence; nothing was repaired or omitted') from exc

    def records(self, sid):
        with self.connection() as db:
            if db is None:
                return [], []
            records, receipts = [], []
            for kind, identity, raw in db.execute('SELECT kind,id,body FROM records'):
                row = json.loads(raw)
                if (kind not in {'site', 'release', 'review', 'preview'} or
                        not isinstance(row, dict) or row.get('id') != identity or
                        not isinstance(row.get('sessionId'), str) or not row['sessionId']):
                    raise PublishingError('retained_unreadable', 'Malformed retained publishing ownership evidence')
                if row['sessionId'] == sid:
                    records.append((kind, row))
            for owner, request, raw in db.execute('SELECT session,request,body FROM receipts WHERE session=?', (sid,)):
                row = json.loads(raw)
                if (not isinstance(row, dict) or row.get('sessionId') != owner or
                        row.get('requestId') != request or not isinstance(row.get('state'), str)):
                    raise PublishingError('retained_unreadable', 'Retained receipt differs from its database ownership')
                receipts.append(row)
            return records, receipts

    def inspect(self, sid):
        records, receipts = self.records(sid)
        reviews = {row['id']: row for kind, row in records if kind == 'review'}
        previews = {row['id']: row for kind, row in records if kind == 'preview'}
        return {
            'sites': [{**row, 'retainedStatus': row.get('status'), 'status': 'unconfirmed',
                       'previousUrl': row.get('url') or row.get('previousUrl'), 'url': None}
                      for kind, row in records if kind == 'site'],
            'releases': [{**row, 'review': reviews.get(row['id']), 'previewStatus': 'unconfirmed' if row['id'] in previews else 'none',
                          'previewUrl': None} for kind, row in records if kind == 'release'],
            'receipts': receipts, 'historicalOnly': True, 'liveness': 'unconfirmed',
        }

    def snapshot(self, destination):
        # Recovery requires verified release bytes, not a best-effort copy.
        raise PublishingError('controller_unavailable', UNAVAILABLE_REASON)

    def owns_records(self, sid):
        records, receipts = self.records(sid)
        return bool(records or receipts)

    def resources(self, sid, limit=25, before=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PublishingError('invalid_argument', 'Resource page size must be between 1 and 100')
        if before is not None and (not isinstance(before, list) or len(before) != 2 or
                before[0] not in {'site', 'preview'} or not isinstance(before[1], str)):
            raise PublishingError('invalid_argument', 'Invalid publishing resource cursor')
        with self.connection() as db:
            # Filter/page before decoding: a resource inventory must not load
            # every immutable file manifest or receipt in the publishing store.
            rows = [] if db is None else db.execute(
                "SELECT kind,id,body FROM records WHERE json_extract(body,'$.sessionId')=? "
                "AND kind IN ('site','preview')" + (" AND (kind,id)>(?,?)" if before else "") +
                " ORDER BY kind,id LIMIT ?", (sid, *(before or []), limit + 1)).fetchall()
            items = []
            for kind, identity, raw in rows[:limit]:
                row = json.loads(raw)
                if not isinstance(row, dict) or row.get('id') != identity or row.get('sessionId') != sid:
                    raise PublishingError('retained_unreadable', 'Malformed retained publishing ownership evidence')
                items.append({**{key: row.get(key) for key in ('id', 'sessionId', 'siteId', 'releaseId', 'updatedAt', 'accessPolicy')},
                              'kind': kind, 'owner': 'publishing', 'evidence': 'retained-record-only',
                              'status': 'unconfirmed', 'url': None, 'port': None})
            return {'items': items, 'nextCursor': list(rows[limit - 1][:2]) if len(rows) > limit else None,
                    'historicalOnly': True, 'liveness': 'unconfirmed'}


def definitions(schema, string):
    task = {'sessionId': string(200)}
    site = {**task, 'siteId': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9-]{0,62}$'}}
    request = {'requestId': {**string(200), 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$'}}
    release = {**task, 'releaseId': {**string(100), 'minLength': 1}}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 0}}
    actions = {
        'publishing.list': ('Inspect this task’s releases, deployments and durable receipts on the explicit or selected target. Remote reads use the inspected private service identity; no work is started or replayed.', schema(task)),
        'publishing.build': ('Capture an existing built static output directory inside this task’s local execution folder as an immutable release, importing its exact static bytes when the selected target is remote. Run the project’s own build first. No build command is executed, no model starts, and no site is deployed. Reuse requestId only for an exact retry.', schema({**site, **request, 'sourcePath': {**string(4000), 'minLength': 1}})),
        'publishing.preview': ('Start a separate loopback preview of an exact saved release. It has no access to the app origin. Preview is not review or deployment. Reuse requestId for exact retry and inspect current status separately.', schema({**release, **request})),
        'publishing.review': ('Record a review note on the exact immutable release after inspecting its content. This does not prove visual inspection or deploy it.', schema({**release, **request, 'note': {**string(4000), 'minLength': 1}})),
        'publishing.deploy': ('Explicitly activate a reviewed release on the selected, inspected target using its current revision. The target reports its actual bind and access policy; this action configures no authentication, TLS or public route. Historical receipts do not prove current liveness.', schema({**site, **release, **request, **revision})),
        'publishing.rollback': ('Explicitly restore a previously deployed, reviewed immutable release using the current site revision. Does not rerun a build or replay unknown effects.', schema({**site, **release, **request, **revision})),
        'publishing.status': ('Read current target deployment status, release identity, access policy and URL. Does not start a listener.', schema(site)),
        'publishing.logs': ('Read durable lifecycle receipts for this task and site. HTTP bodies, credentials and build environment values are not captured.', schema(site)),
        'publishing.stop': ('Stop this site’s target listeners, retaining immutable releases and durable receipts. Requires current revision.', schema({**site, **request, **revision})),
        'publishing.remove': ('Remove this target deployment and previews, retaining releases and receipts for audit. Source files are preserved. Requires current revision.', schema({**site, **request, **revision})),
    }
    for name, (description, spec) in list(actions.items()):
        spec['properties']['targetId'] = {**string(100), 'minLength': 1}
        if name not in {'publishing.list', 'publishing.status', 'publishing.logs'}:
            spec['properties']['targetRevision'] = {'type': 'integer', 'minimum': 0}
            spec['properties']['serviceId'] = {**string(200), 'minLength': 1}
            actions[name] = (description + ' New remote requests must include the targetRevision and serviceId returned by target inspection; exact retries keep their original arguments.', spec)
    actions.update(target_definitions(schema, string))
    return actions


class Publishing:
    def __init__(self, app):
        self.app = app
        self.available = controller_available()
        store_type = Publisher if self.available else RetainedPublisher
        self.store = store_type(app.data_dir / 'publishing')
        self.captures = {}
        self.capture_root = app.data_dir / 'publishing-captures'
        if self.capture_root.is_symlink():
            raise ValueError('Publishing capture storage cannot be a symbolic link.')
        if self.capture_root.exists():
            for path in self.capture_root.iterdir():
                identity = service_identity(path.name)
                self.captures[identity] = store_type(path)
        self.lock = asyncio.Lock()
        if self.available:
            self.app.db.execute('CREATE TABLE IF NOT EXISTS publishing_build_requests (session TEXT, request TEXT, signature TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(session,request))')
            self.app.db.commit()
        self.targets = PublishingTargets(app, local_target=self.target, read_only=not self.available)

    def availability(self):
        return {'available': self.available, **({} if self.available else {
            'reason': UNAVAILABLE_REASON, 'historicalOnly': True})}

    def require_available(self):
        if not self.available:
            raise PublishingError('controller_unavailable', UNAVAILABLE_REASON)

    @staticmethod
    def target():
        return {'id': 'loopback', 'kind': 'loopback', 'label': 'This server', 'accessPolicy': 'loopback-only', 'host': local_host_identity(),
                'detail': 'URLs are reachable only on this server.'}

    def snapshot(self, sid):
        return {'sites': self.store.list(sid), 'releases': self.store.releases(sid),
                'receipts': self.store.receipts(sid), 'target': self.target()}

    def owns_records(self, sid):
        if not self.available:
            return bool(self.store.owns_records(sid) or self.targets.owns_records(sid)
                        or any(store.owns_records(sid) for store in self.captures.values())
                        or self.targets.rows('publishing_build_requests', '1', 'WHERE session=? LIMIT 1', (sid,)))
        return bool(self.store.releases(sid) or self.store.list(sid) or self.store.receipts(sid)
                    or self.targets.owns_records(sid)
                    or any(store.releases(sid) or store.receipts(sid) for store in self.captures.values())
                    or self.app.db.execute('SELECT 1 FROM publishing_build_requests WHERE session=? LIMIT 1', (sid,)).fetchone())

    def capture_store(self, service_id):
        self.require_available()
        identity = service_identity(service_id)
        if identity not in self.captures:
            self.capture_root.mkdir(mode=0o700, exist_ok=True)
            self.captures[identity] = Publisher(self.capture_root / identity)
        return self.captures[identity]

    def build_source(self, session, args, store=None):
        """Bind logical UI requests to their first execution folder durably.

        A retry retrieves a receipt even when a handoff moved the execution
        folder or the original source disappeared. New requests still require
        source admission before any snapshot is made.
        """
        self.require_available()
        sid, rid = session['id'], args['requestId']
        store = store or self.store
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', rid):
            raise PublishingError('invalid_argument', 'requestId must be a bounded plain identifier')
        # Endpoint identity is frozen by the target registry. Omitting targetId
        # and explicitly naming that same endpoint are equivalent retries.
        logical_args = {key: value for key, value in args.items() if key != 'targetId'}
        signature = json.dumps(logical_args, sort_keys=True, separators=(',', ':'))
        previous = self.app.db.execute('SELECT signature,source FROM publishing_build_requests WHERE session=? AND request=?', (sid, rid)).fetchone()
        if previous:
            previous_args = json.loads(previous[0])
            previous_args.pop('targetId', None)
            if previous_args != logical_args:
                raise PublishingError('request_conflict', 'requestId was already used with different build arguments')
            if not any(r['requestId'] == rid for r in store.receipts(sid)):
                raise PublishingError('unknown_outcome', 'Build admission was recorded without a publishing receipt. It will not be replayed; inspect the retained request.')
            return Path(previous[1])
        if any(r['requestId'] == rid for r in store.receipts(sid)):
            raise PublishingError('request_conflict', 'requestId was already used by another publishing action')
        host = session.get('executionHost')
        if host:
            if host.get('scope') != 'local':
                raise ValueError('Capturing built output requires a local execution folder, including when the publishing target is remote.')
            require_local_host(host.get('id'))
        if session.get('configurationBusy') or session.get('_deleting'):
            raise ValueError('Wait for this task’s execution folder change to finish before publishing.')
        source = self.source(session, args['sourcePath'])
        self.app.db.execute('INSERT INTO publishing_build_requests VALUES (?,?,?,?)', (sid, rid, signature, str(source)))
        self.app.db.commit()
        return source

    def admission_receipts(self, sid, receipts):
        known = {r['requestId'] for r in receipts}
        for rid, signature in self.app.db.execute('SELECT request,signature FROM publishing_build_requests WHERE session=?', (sid,)):
            binding = self.targets.lookup_request(sid, rid)
            if binding and binding['targetId'] != 'loopback':
                continue
            if rid not in known:
                args = json.loads(signature)
                receipts.append({'id': hashlib.sha256((sid + ':' + rid).encode()).hexdigest(), 'requestId': rid,
                                 'sessionId': sid, 'siteId': args['siteId'], 'action': 'build', 'state': 'unknown',
                                 'error': {'code': 'unknown_outcome', 'message': 'Admission was saved without a publishing receipt; no work was replayed.'}})
        return receipts

    def source(self, session, value):
        relative = Path(value)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Choose a built output directory inside this task’s execution folder.')
        workspace = Path(session.get('workingDirectory') or session['workspace']).resolve()
        source = workspace / relative
        current = source
        while current != workspace:
            if current.is_symlink():
                raise ValueError('Publishing source paths cannot contain symbolic links.')
            current = current.parent
        if not source.resolve().is_relative_to(workspace):
            raise ValueError('Choose a built output directory inside this task’s execution folder.')
        return source

    async def dispatch(self, action, args, origin, command_id):
        # One serialized adapter path is shared by UI and agent callers. The
        # library supplies cross-process locking and durable request receipts.
        async with self.lock:
            session = self.app._session(args['sessionId'])
            sid = session['id']
            if not self.available:
                if action not in {'publishing.list', 'publishing.status', 'publishing.logs', 'publishing.target.list'}:
                    return {'accepted': False, 'effect': 'none', 'code': 'controller_unavailable',
                            **self.availability()}
                registry = {**self.targets.list(sid), 'availability': self.availability()}
                if action == 'publishing.target.list':
                    return registry
                target_id = args.get('targetId') or registry['selectedTargetId']
                target = next((row for row in registry['targets'] if row['id'] == target_id), None)
                if target is None:
                    raise PublishingError('not_found', 'Retained publishing target is not available in this task')
                remote = target_id != 'loopback'
                retained = [row for row in registry['retainedRequests'] if row['targetId'] == target_id]
                evidence = ({'sites': None, 'releases': None, 'receipts': retained}
                            if remote else self.store.inspect(sid))
                result = {**registry, **evidence, 'target': target, 'historicalOnly': True,
                          'liveness': 'unconfirmed', 'coverage': 'retained-admissions-only' if remote else 'retained-local-records',
                          'retainedCaptures': {identity: store.inspect(sid) for identity, store in self.captures.items()},
                          'retainedAdmissions': [{'requestId': rid, 'signature': signature, 'source': source}
                              for rid, signature, source in self.targets.rows('publishing_build_requests',
                                  'request,signature,source', 'WHERE session=?', (sid,))]}
                if action == 'publishing.status':
                    status = (next((row for row in evidence['sites'] if row['id'] == args['siteId']), None)
                              if not remote else {'id': args['siteId'], 'url': None, 'status': 'unconfirmed'})
                    if status is None:
                        raise PublishingError('not_found', 'Site is not available in this session')
                    return {**status, 'target': target, 'availability': self.availability(),
                            'historicalOnly': True, 'liveness': 'unconfirmed'}
                if action == 'publishing.logs':
                    result['items'] = [row for row in evidence['receipts'] if row.get('siteId') == args['siteId']]
                return result
            if action.startswith('publishing.target.'):
                return await self.targets.dispatch(action, args)
            reads = {'publishing.list', 'publishing.status', 'publishing.logs'}
            if action in reads:
                target = self.targets.resolve(sid, args.get('targetId'))
                if target['id'] != 'loopback':
                    return await self.targets.dispatch_remote(action, args)
            else:
                legacy_local = (not self.targets.lookup_request(sid, args['requestId']) and
                                (any(row['requestId'] == args['requestId'] for row in self.store.receipts(sid)) or
                                 self.app.db.execute('SELECT 1 FROM publishing_build_requests WHERE session=? AND request=?', (sid, args['requestId'])).fetchone()))
                binding = self.targets.bind_request(sid, action, args, fallback_target_id='loopback' if legacy_local else None)
                if binding['targetId'] != 'loopback':
                    if action != 'publishing.build':
                        return await self.targets.dispatch_remote(action, args, binding)
                    handled, result = await self.targets.resume_request(binding)
                    if handled:
                        return result
                    try:
                        capture = self.capture_store(binding['serviceId'])
                        source = self.build_source(session, args, capture)
                        release = await asyncio.to_thread(capture.build, source, site_id=args['siteId'], session_id=sid, request_id=args['requestId'])
                        exported = await asyncio.to_thread(capture.export_release, release['id'], sid)
                    except ValueError as exc:
                        self.targets.capture_failed(binding, exc)
                        raise
                    return await self.targets.import_release(args, exported, binding)
            if action == 'publishing.list':
                result = await asyncio.to_thread(self.snapshot, sid)
                result = {**self.targets.list(sid), **result}
                result['receipts'] = self.admission_receipts(sid, result['receipts'])
                return result
            if action == 'publishing.status':
                return await asyncio.to_thread(self.store.status, args['siteId'], sid)
            if action == 'publishing.logs':
                records = await asyncio.to_thread(self.store.receipts, sid)
                self.admission_receipts(sid, records)
                return {'items': [r for r in records if r.get('siteId') == args['siteId']], 'target': self.target()}
            kw = {'session_id': sid, 'request_id': args['requestId']}
            if action != 'publishing.build' and self.app.db.execute('SELECT 1 FROM publishing_build_requests WHERE session=? AND request=?', (sid, args['requestId'])).fetchone():
                raise PublishingError('request_conflict', 'requestId was already used by a build request')
            if action == 'publishing.build':
                result = await asyncio.to_thread(self.store.build, self.build_source(session, args), site_id=args['siteId'], **kw)
            elif action == 'publishing.review':
                result = await asyncio.to_thread(self.store.review, args['releaseId'], note=args['note'], **kw)
            elif action == 'publishing.preview':
                result = await asyncio.to_thread(self.store.preview, args['releaseId'], **kw)
            elif action in {'publishing.deploy', 'publishing.rollback'}:
                operation = self.store.deploy if action == 'publishing.deploy' else self.store.rollback
                result = await asyncio.to_thread(operation, args['releaseId'], site_id=args['siteId'], expected_revision=args['expectedRevision'], **kw)
            elif action in {'publishing.stop', 'publishing.remove'}:
                operation = self.store.stop if action == 'publishing.stop' else self.store.remove
                result = await asyncio.to_thread(operation, args['siteId'], expected_revision=args['expectedRevision'], **kw)
            else:
                raise ValueError('Unknown publishing action.')
            return result

    async def close(self):
        if not self.available:
            return  # No connection, lock or listener was acquired.
        async with self.lock:
            await asyncio.to_thread(self.store.close)
            for store in self.captures.values():
                await asyncio.to_thread(store.close)
