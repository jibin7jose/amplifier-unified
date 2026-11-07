"""Requested event windows, separate from authoritative execution/accounting.

Only small source identity descriptors cross the process boundary. The host
retains at most eight bounded pages; it never merges these observations into an
execution tree, grants admission from them, or starts a runtime for a read.
"""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import hashlib
import secrets
import sys
import time

IDENTITY_FIELDS = ('id', 'workspace', 'nativeProject', 'nativeIdentity', 'runtimeSessionId')
MAX_PAGES = 8
MAX_JOBS = 4


class _SupersededRead(ValueError):
    """A newer publication owns this display; the discarded read did not fail."""


def source_identity(session):
    return {key: session[key] for key in IDENTITY_FIELDS if key in session}


def web_context(service):
    clients = getattr(service, 'clients', None)
    identity = clients.current.get() if clients is not None else None
    return identity is not None and clients.records.get(identity, {}).get('kind') == 'web'


class _ThreadedWindowProjector:
    """Bounded fallback until native process memory policy is qualified."""
    def __init__(self, home):
        from .event_window_worker import WindowModel
        self.model, self.tasks, self.closed = WindowModel(home), set(), False

    async def project(self, inputs, *, key):
        if self.closed or len(self.tasks) >= MAX_JOBS:
            return {'status': 'retry', 'reason': 'closed' if self.closed else 'queue_full'}
        def guard():
            if self.closed:
                raise InterruptedError()
        task = asyncio.create_task(asyncio.to_thread(self.model.project, inputs, check_budget=guard))
        self.tasks.add(task)
        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def close(self):
        self.closed = True
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


class WorkWindows:
    def __init__(self, service):
        self.service = service
        self.key = secrets.token_bytes(32)
        self.pages = OrderedDict()
        self.current = OrderedDict()
        self.pending = {}
        self.status = OrderedDict()
        self.epochs = OrderedDict()
        self.epoch = 0
        self.generation = 0
        self.closed = False
        self.projector = None
        self.retry_at = {}
        from .resident_work_pages import ResidentWorkPages
        self.live_pages = ResidentWorkPages(self)
        from .tool_payload_windows import ToolPayloadWindows
        self.tool_payloads = ToolPayloadWindows(self)

    def _worker(self):
        if self.projector is None and not sys.platform.startswith('linux'):
            self.projector = _ThreadedWindowProjector(self.service.data_dir)
        if self.projector is None:
            from .history_projection_process import ProcessProjector
            self.projector = ProcessProjector(self.service.data_dir,
                _command=(sys.executable, '-m', 'amplifier_web.event_window_worker'),
                _input_limit=64 * 1024, _output_limit=4 * 1024 * 1024,
                _spool_limit=20 * 1024 * 1024, _max_jobs=MAX_JOBS, _timeout=20)
        return self.projector

    def _owner(self, identity):
        return self.service._session(identity, hydrate=False)

    def _check_current(self, identity, owner, source, fence):
        if (self.closed or getattr(self.service, 'closed', False)
                or self.epochs.get(identity) != fence):
            raise _SupersededRead('The requested conversation changed. Open it again.')
        from .errors import AppError
        try:
            current = self._owner(identity)
        except AppError as error:
            if error.status not in {404, 409}:
                raise
            raise _SupersededRead('The requested conversation changed. Open it again.') from None
        if current is not owner or source_identity(owner) != source:
            raise _SupersededRead('The requested conversation changed. Open it again.')

    @staticmethod
    def _window_key(source):
        # Identity only: source mutation invalidates the signed page cursor,
        # while transcript/window navigation never broadens the event source.
        return hashlib.sha256(repr(tuple(source.get(key) for key in IDENTITY_FIELDS)).encode()).hexdigest()

    def _notify(self, identity, *, notify=True):
        projections = getattr(self.service, 'projections', None)
        if projections is not None:
            projections.detail_bodies.pop(identity, None)
        event = getattr(self.service, 'event_log_view', None)
        if event is not None and notify:
            event._notify_work(identity)

    def _state(self, identity, value, *, notify=True):
        self.status.pop(identity, None)
        self.status[identity] = value
        while len(self.status) > MAX_PAGES:
            retired, _ = self.status.popitem(last=False)
            self.retry_at.pop(retired, None)
        self._notify(identity, notify=notify)

    def invalidate(self, identities=None, *, notify=True):
        for identity in set(self.current) | set(self.pending) | set(self.epochs):
            if identities is None or identity in identities:
                self.epoch += 1
                self.epochs.pop(identity, None)
                self.epochs[identity] = self.epoch
                while len(self.epochs) > 32:
                    self.epochs.popitem(last=False)
                current = self.status.get(identity)
                if current and current.get('current'):
                    self._state(identity, {**current, 'current': False, 'status': 'loading'},
                                notify=notify)

    def request(self, identity):
        if self.closed or getattr(self.service, 'closed', False):
            return None
        task = self.pending.get(identity)
        if task is not None:
            return task
        if len(self.pending) >= MAX_JOBS or self.retry_at.get(identity, 0) > time.monotonic():
            return None
        async def refresh():
            owner = None
            try:
                owner = self._owner(identity)
                source, fence = source_identity(owner), self._fence(identity)
                return await self.read(identity, _initial=True)
            except asyncio.CancelledError:
                raise
            except _SupersededRead:
                return None  # The existing interest loop retries the current owner next tick.
            except Exception:
                # A failure from an obsolete probe cannot replace newer status.
                if owner is not None:
                    try:
                        self._check_current(identity, owner, source, fence)
                    except _SupersededRead:
                        return None
                if not self.closed and self.status.get(identity, {}).get('status') != 'retrying':
                    self.retry_at[identity] = time.monotonic() + 5
                    self._state(identity, {'status': 'retrying', 'current': False, 'reason': 'worker_failed'})
                return None
        task = asyncio.create_task(refresh())
        self.pending[identity] = task
        def finished(done):
            if self.pending.get(identity) is done:
                self.pending.pop(identity, None)
            if not done.cancelled():
                done.exception()  # Fixed public status records failures; consume task errors.
        task.add_done_callback(finished)
        return task

    def _fence(self, identity):
        value = self.epochs.pop(identity, None)
        if value is None:
            self.epoch += 1
            value = self.epoch
        self.epochs[identity] = value
        while len(self.epochs) > 32:
            self.epochs.popitem(last=False)
        return value

    def retain_interest(self, identities):
        self.live_pages.retain_interest(identities)
        for identity, task in tuple(self.pending.items()):
            if identity not in identities:
                task.cancel()
        for mapping in (self.epochs, self.retry_at):
            for identity in tuple(mapping):
                if identity not in identities and identity not in self.current and identity not in self.pending:
                    mapping.pop(identity, None)

    async def read(self, identity, before=None, *, _initial=False):
        if self.closed or getattr(self.service, 'closed', False):
            raise ValueError('Work reading is closed.')
        owner = self._owner(identity)
        identity, source = owner['id'], source_identity(owner)
        window_key = self._window_key(source)
        fence = self._fence(identity)
        prior = self.current.get(identity)
        # Read only exact source stats before deciding whether this small page
        # needs rereading. No transcript, tree, directory scan or prefix digest.
        from .cold_display import raw
        if (_initial and prior is not None and self.pages.get(prior, {}).get('source') == source
                and self.pages[prior].get('savedExecutionReference') ==
                    raw(owner).get('_coldFields', {}).get('execution')
                and ('savedExecution' not in self.pages[prior]
                     or self.pages[prior].get('savedExecutionFence') == fence)):
            accepted_page = self.pages[prior]
            accepted_reference = deepcopy(accepted_page.get('savedExecutionReference'))
            matches = await asyncio.to_thread(self._matches, source, accepted_page.get('revisions'))
            self._check_current(identity, owner, source, fence)
            if (self.pages.get(prior) is not accepted_page
                    or raw(owner).get('_coldFields', {}).get('execution') != accepted_reference):
                raise _SupersededRead('The requested conversation changed. Open it again.')
            if matches:
                if prior not in self.pages or self.current.get(identity) != prior:
                    raise _SupersededRead('The requested conversation changed. Open it again.')
                current = self.status.get(identity)
                receipt = self.pages[prior]['receipt']
                if not current or not current.get('current') or current.get('receipt') != receipt:
                    self._state(identity, {'status': 'ready', 'current': True, 'receipt': receipt})
                return self._groups(self.pages[prior])
        if _initial:
            self._state(identity, {'status': 'loading', 'current': False})
        result = await self._worker().project({'operation': 'window', 'session': source,
            'windowKey': window_key, 'before': before, 'cursorKey': self.key.hex()},
            key=identity + ':' + str(before or 'latest'))
        self._check_current(identity, owner, source, fence)
        if result.get('status') != 'complete':
            if _initial:
                self.retry_at[identity] = time.monotonic() + 5
                self._state(identity, {'status': 'retrying', 'current': False,
                                      'reason': result.get('reason', 'worker_failed')})
            raise ValueError('Saved work is unavailable. Try again shortly.')
        page = result['page']
        matches = await asyncio.to_thread(self._matches, source, page.get('revisions'))
        self._check_current(identity, owner, source, fence)
        if not matches:
            if _initial:
                self.retry_at[identity] = time.monotonic() + 1
                self._state(identity, {'status': 'retrying', 'current': False, 'reason': 'source_changed'})
            raise ValueError('Saved work changed while it was read. Refresh this window.')
        # The existing saved view can retire its host placement metadata to a
        # resource. Read that owned resource only on this explicit window read,
        # not from passive projections or field chunks. Never hydrate the
        # authoritative ColdRecord or accept arbitrary event rows as placement.
        from .cold_display import raw
        saved_reference = deepcopy(raw(owner).get('_coldFields', {}).get('execution'))
        if saved_reference and 'execution' not in raw(owner):
            from .resource_files import detach, read_detached
            from .session_projection import stored_execution
            import json
            detached = detach(self.service.db, saved_reference['$resource'])
            def saved_work():
                tree = read_detached(detached, max_bytes=4 * 1024 * 1024)
                if not isinstance(tree, dict):
                    raise ValueError('Invalid saved Work placement.')
                tree = stored_execution(tree)
                # Keep within this page's existing output/retention budget.
                if len(json.dumps([page, tree], ensure_ascii=False).encode()) > 4 * 1024 * 1024:
                    raise ValueError('Saved Work placement exceeds this window budget.')
                return tree
            tree = await asyncio.to_thread(saved_work)
            self._check_current(identity, owner, source, fence)
            if (raw(owner).get('_coldFields', {}).get('execution') != saved_reference
                    or 'execution' in raw(owner)):
                raise _SupersededRead('The requested conversation changed. Open it again.')
            matches = await asyncio.to_thread(self._matches, source, page.get('revisions'))
            self._check_current(identity, owner, source, fence)
            if (not matches or raw(owner).get('_coldFields', {}).get('execution') != saved_reference
                    or 'execution' in raw(owner)):
                raise _SupersededRead('The requested conversation changed. Open it again.')
            page = {**page, 'savedExecution': tree, 'savedExecutionReference': saved_reference,
                    'savedExecutionFence': fence}
        page = {**page, 'sessionId': identity, 'source': source}
        group = 'event-window:' + page['generation']
        page['group'] = group
        page['providerRecords'] = self._provider_records(page)
        from .tool_window_records import records
        page['toolRecords'] = records(page)
        page['hasProviderRecords'] = any(row.get('requestDetail') or row.get('responseDetail') for row in page['items'])
        self.pages.pop(group, None)
        self.pages[group] = page
        while len(self.pages) > MAX_PAGES:
            retired, _ = self.pages.popitem(last=False)
            for sid, value in tuple(self.current.items()):
                if value == retired:
                    self.current.pop(sid, None)
                    self.status.pop(sid, None)
                    self.epochs.pop(sid, None)
                    self._notify(sid)
        if _initial or before is None:
            self.current.pop(identity, None)
            self.current[identity] = group
            self.generation += 1
            coverage = page.get('coverage', {})
            page['receipt'] = {
                'hostInstanceId': getattr(self.service, 'instance_id', None), 'sessionId': identity,
                'generation': self.generation, 'scope': 'recent-events', 'windowKey': window_key,
                'historyComplete': False, 'sourceCount': coverage.get('sourceCount', 0),
                'itemCount': len(page['items']), 'scannedBytes': coverage.get('scannedBytes', 0),
                'scannedEvents': coverage.get('scannedEvents', 0)}
            self._state(identity, {'status': 'ready', 'current': True, 'receipt': page['receipt']})
        return self._groups(page)

    @staticmethod
    def _matches(source, revisions):
        from .event_window import _source
        path, _, _ = _source(source)
        if not isinstance(revisions, list) or len(revisions) != 1 or revisions[0][0] != str(path):
            return False
        try:
            info = path.stat()
            actual = [[info.st_dev, info.st_ino], info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        except FileNotFoundError:
            actual = None
        return revisions[0][1] == actual

    @staticmethod
    def _groups(page):
        identity = page['group']
        coverage = page.get('coverage', {})
        group = {'id': identity, 'turnId': identity, 'anchorMessageId': None,
            'observationWindow': True, 'phase': 'recorded', 'label': 'Recent saved work',
            'nodeCounts': None, 'aggregateUsage': None, 'detailRevision': page['generation'],
            'coverage': coverage}
        return {'items': [group] if page['items'] or page.get('unavailableRanges') else [],
            'turns': [{'id': identity, 'phase': 'recorded', 'observationWindow': True}],
            'mode': 'cursor', 'offset': None, 'total': None, 'before': page.get('before'),
            'hasEarlier': page.get('before') is not None, 'revision': page['generation'],
            'windowKey': page['windowKey'], 'coverage': coverage,
            'unavailableRanges': page.get('unavailableRanges', [])}

    def resident_tree(self, session, *, touch=False):
        """Current resident metadata or the requested, still-owned saved view."""
        from .cold_display import raw
        resident = raw(session)
        if 'execution' in resident:
            return resident['execution']
        page = self.pages.get(self.current.get(session['id']))
        if (page is not None and page['source'] == source_identity(session)
                and page.get('savedExecutionReference') is not None
                and page.get('savedExecutionFence') == self.epochs.get(session['id'])
                and page['savedExecutionReference'] == resident.get('_coldFields', {}).get('execution')):
            if touch:
                self.pages.move_to_end(page['group'])
            return page.get('savedExecution', {})
        return {}

    def live_view(self, session):
        """Bounded current host evidence; never scan historical native nodes."""
        from .cold_display import raw
        resident = raw(session)
        tree = self.resident_tree(session)
        source = {key: value for key, value in resident.items() if not key.startswith('_cold')}
        source.update(messages=resident.get('messages', [])[-100:],
            generations=resident.get('generations', [])[-20:])
        if 'messages' not in resident:
            transcript = getattr(getattr(self.service, 'history', None), 'windows', None)
            if transcript is not None:
                source['messages'] = transcript.project(session).get('messages', [])
        source.update({
            'execution': {**{key: tree[key] for key in ('aggregateUsage', 'usage', 'plan', 'currentTurnId') if key in tree},
                'nodes': [self.live_node(session, row) for row in tree.get('nodes', [])[-100:]
                if row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory')],
                'turns': tree.get('turns', [])[-20:], 'currentTurnId': tree.get('currentTurnId')}})
        return source

    def live_node(self, session, node):
        if node.get('kind') == 'tool' and not node.get('canonicalHistory') and not node.get('nativeHistory'):
            from .tool_window_records import attach, linked
            page = self.pages.get(self.current.get(session['id']))
            fields = linked(page, session, node) if page and page['source'] == source_identity(session) else None
            return attach(node, fields)
        if node.get('kind') != 'llm':
            return node
        from .browser_detail import detail_version
        details = self.provider_details(session, node)
        semantic = {key: (bool(detail) if key == 'recordingsGroup' else detail)
                    for key, detail in details.items()}
        # A completed call may precede canonical flush. Wake its open detail
        # subscription when recordings arrive or their accepted page changes.
        # Exact field references must move to the new bounded page before an
        # older page expires; an unrelated shortcut alone has no such lease.
        return {**node, '_providerRecordsRevision': detail_version(semantic)}

    def project(self, session):
        page = self.pages.get(self.current.get(session['id']))
        rebound = page is not None and page['source'] != source_identity(session)
        if rebound:
            page = None
        groups = self._groups(page) if page else {'items': [], 'turns': [], 'mode': 'cursor',
            'offset': None, 'total': None, 'before': None, 'hasEarlier': False,
            'coverage': {'kind': 'event-window', 'historyComplete': False}}
        from .browser_detail import page as detail_page
        live_source = self.live_view(session)
        live = detail_page(live_source, 'groups')
        from .cold_display import raw
        if len(self.resident_tree(session).get('nodes', [])) > 100:
            # The tail is display evidence, not a complete current-turn count.
            live['items'] = [{**row, 'partialWindow': True, 'nodeCounts': None,
                              'aggregateUsage': None} for row in live['items']]
            live['turns'] = [{key: value for key, value in row.items()
                              if key not in {'nodeCounts', 'aggregateUsage'}}
                             for row in live['turns']]
        from .image_generation import project as image_work
        return {'execution': {**{key: value for key, value in live_source['execution'].items()
                                  if key not in {'nodes', 'turns'}},
                             'imageGeneration': image_work(live_source, live_source['execution']),
                             'nodes': [], 'turns': [*live['turns'], *groups['turns']],
                             'segments': [*live['items'], *groups['items']],
                             'detailsDeferred': True},
                'executionWindow': {'part': 'groups', **{key: value for key, value in groups.items() if key not in {'items', 'turns'}}},
                'workProjection': self.status.get(session['id'], {'status': 'loading', 'current': False})
                    if not rebound else {'status': 'loading', 'current': False}}

    def _page(self, identity, group):
        owner = self._owner(identity)  # Current canonical authority, not cache presence.
        if str(group).startswith('open-work:'):
            return self.live_pages.open_fields.page(owner, group)
        page = self.pages.get(group)
        if page is None or page['sessionId'] != identity or source_identity(owner) != page.get('ownerSource', page['source']):
            raise ValueError('This saved work window expired. Open the recent window again.')
        if '_toolScope' in page:
            self.tool_payloads.check(owner, page)
        self.pages.move_to_end(group)
        return page

    @staticmethod
    def _provider_records(page):
        calls = {}
        for row in page['items']:
            if row.get('kind') != 'llm' or not row.get('_hostCallId') or not row.get('requestId'):
                continue
            key = (row.get('sessionId'), row['_hostCallId'])
            calls.setdefault(key, []).append(row)
        result = {}
        for key, rows in calls.items():
            # A host call may contain retries. Do not combine attempts, choose
            # a terminal attempt by arrival, or guess through duplicate fields.
            attempts = {(row['requestId'], row.get('provider'), row.get('model')) for row in rows}
            if len(attempts) != 1:
                continue
            fields = {}
            for field in ('request', 'response'):
                matches = [row for row in rows if field + 'Detail' in row]
                if len(matches) > 1:
                    break
                if matches:
                    row = matches[0]
                    for suffix in ('Detail', 'Capture', 'Recording'):
                        if field + suffix in row:
                            fields[field + suffix] = deepcopy(row[field + suffix])
                    fields[field + 'Detail'].update(sessionId=page['sessionId'], windowKey=page['group'])
            else:
                result[key] = fields
        return result

    def provider_details(self, session, node):
        page = self.pages.get(self.current.get(session['id']))
        if page is None or page['source'] != source_identity(session) or not page['hasProviderRecords']:
            return {}
        identity = node.get('sessionId')
        if identity == session['id']:
            # Runtime delivery maps only the root coordinator ID to the app
            # owner. Undo exactly that alias for canonical capture lookup;
            # child IDs retain their own namespace.
            identity = page['source'].get('nativeIdentity') or page['source'].get('runtimeSessionId') or session['id']
        fields = page['providerRecords'].get((identity, node['id']), {})
        return {**deepcopy(fields), 'recordingsGroup': page['group']}

    def nodes(self, identity, group, *, revision=None, known=None):
        page = self._page(identity, group)
        if revision is not None and revision != page['generation']:
            raise ValueError('This saved work window changed. Open it again.')
        items = []
        for original in page['items']:
            row = {key: deepcopy(value) for key, value in original.items() if not key.startswith('_')}
            row.update(turnId=group, detailVersion=page['generation'], observationWindow=True)
            for field in ('input', 'output', 'error', 'request', 'response', 'delegateRequest', 'delegateResponse'):
                if field + 'Detail' in row:
                    row[field + 'Detail'].update(sessionId=identity, windowKey=group)
            if known is None or known.get(row['id']) != row['detailVersion']:
                items.append(row)
        return {'items': items, 'order': [row['id'] for row in page['items']],
                'offset': 0, 'total': len(page['items']), 'before': None,
                'group': group, 'revision': page['generation'], 'coverage': page['coverage']}

    async def read_tool(self, identity, args):
        return await self.tool_payloads.read(self._owner(identity), args)

    @staticmethod
    def field_failure(result):
        reason = result.get('reason')
        if reason == 'source_changed':
            raise ValueError('This saved field changed. Reopen its Work window.')
        if reason in {'queue_full', 'timeout'}:
            raise ValueError('Saved content is taking longer to read. Try again shortly.')
        raise ValueError('Saved content could not be read. Try again.')

    async def read_text(self, identity, args):
        owner = self._owner(identity)
        page = self._page(owner['id'], args.get('windowKey'))
        row = next((row for row in page['items'] if row['id'] == args.get('id')), None)
        field = args.get('field')
        reference = (row or {}).get('_eventFields', {}).get(field)
        detail = (row or {}).get(str(field) + 'Detail', {})
        if reference is None or args.get('digest') != detail.get('digest'):
            raise ValueError('This saved field is no longer available.')
        result = await self._worker().project({'operation': 'field', 'session': page['source'],
            'reference': reference, 'offset': int(args.get('offset', 0)),
            'limit': min(16384, max(1, int(args.get('limit', 16000))))}, key=owner['id'] + ':field:' + row['id'])
        if (self.closed or self._owner(owner['id']) is not owner or self._page(owner['id'], page['group']) is not page
                or source_identity(owner) != page.get('ownerSource', page['source'])):
            raise ValueError('This saved field expired.')
        if result.get('status') != 'complete':
            self.field_failure(result)
        if '_toolScope' in page:
            self.tool_payloads.check(owner, page)
        value = result['field']
        return {'value': value['text'], 'nextOffset': value['nextOffset'], 'total': value['total']}

    def recording(self, identity, args):
        owner = self._owner(identity)
        page = self._page(owner['id'], args.get('windowKey'))
        row = next((row for row in page['items'] if row['id'] == args.get('id') and row.get('kind') == 'llm'), None)
        field = args.get('field')
        reference = (row or {}).get('_eventFields', {}).get(field)
        recording = (row or {}).get(str(field) + 'Recording', {})
        if field not in {'request', 'response'} or reference is None or args.get('digest') != recording.get('sha256'):
            raise ValueError('This provider recording is no longer available.')
        return owner, page, row, reference

    def check_recording(self, owner, page):
        if (self.closed or self._owner(owner['id']) is not owner or self._page(owner['id'], page['group']) is not page
                or source_identity(owner) != page.get('ownerSource', page['source'])):
            raise ValueError('This provider recording expired. Reopen its saved work window.')

    async def close(self):
        self.closed = True
        tasks = tuple(self.pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.projector is not None:
            await self.projector.close()
        self.live_pages.groups.clear()
        self.pages.clear(); self.current.clear(); self.status.clear()
        self.epochs.clear(); self.retry_at.clear()
