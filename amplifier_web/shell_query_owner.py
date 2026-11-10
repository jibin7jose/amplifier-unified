"""One bounded cold-query owner for opted-in built-in shell resources.

The host selects operations and captures scalar inputs synchronously. No client
SQL, commands, runtime or transcript body enters this lane. Results are disposable
cache entries; durable sources and current demand must still agree at completion.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import re
import sqlite3
import sys
import time

from .history_projection_process import ProcessProjector
from .shell_query_model import durable_stamp, file_id, query_collections
from .shell_query_worker import INPUT_LIMIT, OUTPUT_LIMIT
from .performance_diagnostics import count, measure

MAX_JOBS = 8
QUERY_GLOBALS = frozenset({'workspaces', 'pinnedSessionIds', 'pinnedWorkspaceIds',
    'pinnedItemIds', 'pinOrderCustomized', 'conversationOrganization', 'attentionRead',
    'hiddenNativeSessions', 'hiddenNativeWorkspaces'})
WORKSPACE_FIELDS = frozenset({'id', 'path', 'name', 'available', 'archived'})
VIEW_FIELDS = frozenset({'navChatScope', 'navSort', 'navFilter', 'navStatusFilter',
    'navLocationFilter', 'navArchive', 'navCollection', 'navChatPage', 'navPinnedPage',
    'navShowAgentCreated', 'navRecentLimit', 'navRecentRevision', 'navWorkspaceArchive', 'navGroup', 'navAge'})


class InputLimit(ValueError):
    pass


ESCAPED = re.compile(r'["\\\x00-\x1f]')


class InputBudget:
    """Bound copying and JSON string accounting without encoding a whole input."""
    def __init__(self):
        self.remaining = INPUT_LIMIT - 512

    def charge(self, amount):
        self.remaining -= amount
        if self.remaining < 0:
            raise InputLimit()

    def copy(self, value, depth=0):
        if depth > 16:
            raise InputLimit()
        if isinstance(value, str):
            if len(value) > self.remaining:
                raise InputLimit()
            self.charge(2)
            # Workspace registries can be large but mostly short strings. Exact
            # escaping cost avoids rejecting a valid multi-workspace registry;
            # chunking bounds temporary encoding even for one enormous scalar.
            for start in range(0, len(value), 8192):
                chunk = value[start:start + 8192]
                try:
                    encoded_size = len(chunk.encode('utf-8'))
                except UnicodeError:
                    raise InputLimit() from None
                self.charge(encoded_size + sum(1 if item.group() in '\\"\b\f\n\r\t' else 5
                                               for item in ESCAPED.finditer(chunk)))
            return value  # immutable; never decode scalar summaries here
        if value is None or type(value) in (bool, int, float):
            self.charge(32)
            return value
        if isinstance(value, dict):
            self.charge(2)
            result = {}
            for key, item in value.items():
                self.charge(2)
                result[self.copy(key, depth + 1)] = self.copy(item, depth + 1)
            return result
        if isinstance(value, (tuple, list)):
            self.charge(2)
            result = []
            for item in value:
                self.charge(1)
                result.append(self.copy(item, depth + 1))
            return result
        raise InputLimit()


def capture_workspaces(rows, budget):
    # Copy only the query shape, never expanded configuration or other owners.
    return [budget.copy({key: row[key] for key in WORKSPACE_FIELDS if key in row}) for row in rows]


def capture_inputs(directory, state, operation, args, fence, *, workspace_capture=capture_workspaces):
    budget = InputBudget()
    frozen = budget.copy({field: state[field] for field in QUERY_GLOBALS
                          if field in state and field not in ('workspaces', 'conversationOrganization')})
    # Workspace registration metadata is already resident, but configuration and
    # other expanded fields are not part of the query. Allocate only this shape.
    frozen['workspaces'] = workspace_capture(state.get('workspaces', ()), budget)
    archived = {}
    for identity in state.get('conversationOrganization', {}).get('archived', {}):
        archived[budget.copy(identity)] = None
        budget.charge(8)
    frozen['conversationOrganization'] = {'archived': archived}
    frozen.update(budget.copy({'selectedSessionId': state.get('selectedSessionId'),
        'selectedWorkspaceId': state.get('selectedWorkspaceId'), 'revision': state.get('revision', 0),
        'view': {name: value for name, value in state.get('view', {}).items() if name in VIEW_FIELDS},
        **({'_navigationListClock': state['_navigationListClock']} if '_navigationListClock' in state else {})}))
    inputs = budget.copy({'operation': operation, 'args': args, 'live': directory._live_values,
        'native': {'path': directory._native_path, 'namespace': directory._native_namespace},
        'durable': fence[0], 'files': fence[-1]})
    inputs['notices'] = []
    for row in directory._live_notices:
        inputs['notices'].append(budget.copy(row[:5]))  # predicates, not notice body
    inputs['state'] = frozen
    return inputs


class PendingQuery(Exception):
    def __init__(self, retry_after=0):
        self.retry_after = retry_after


class ShellQueryOwner:
    def __init__(self, resources, *, projector_factory=ProcessProjector):
        self.resources = resources
        self.service = resources.shell.service
        self.factory = projector_factory
        self.enabled = sys.platform.startswith('linux')
        self.context = None
        self.jobs = {}
        self.failures = {}
        self.retries = {}
        self.auto_retried = {}
        self.capacity_waiters = {}
        self.projector = None
        self.runner = None
        self.active = None
        self.idle = None
        self.retiring = None
        self.closed = False
        self.closing = None
        self.workspace_snapshot = None

    def invalidate_workspaces(self):
        self.workspace_snapshot = None

    def _capture_workspaces(self, rows, budget):
        # Scoped views share the canonical registry. Alternate caller-owned
        # registries have no commit epoch and must be captured independently.
        if rows is not getattr(self.service, '_state', {}).get('workspaces'):
            return capture_workspaces(rows, budget)
        epoch = self.resources._epochs({'workspaces'})
        cached = self.workspace_snapshot
        if cached is not None and cached[0] is rows and cached[1] == epoch:
            budget.charge(cached[3])
            return cached[2]
        self.invalidate_workspaces()
        before = budget.remaining
        value = capture_workspaces(rows, budget)
        # Retain one source identity witness and one bounded detached value.
        # Queued inputs borrow only the value. Replacement never mutates it,
        # including while the projector serializes an older job off-thread.
        self.workspace_snapshot = (rows, epoch, value, before - budget.remaining)
        return value

    @contextmanager
    def resource(self, identity, instance, name, key):
        previous = self.context
        self.context = (identity, self.resources.clients[identity], instance, name, key)
        try:
            yield
        finally:
            self.context = previous

    def _current(self, interest):
        identity, registration, instance, name, key = interest
        if self.resources.clients.get(identity) is not registration:
            return False
        if registration.get('keys', {}).get(instance, {}).get(name) != key:
            return False
        from .shell_resources import scope_for
        if identity not in self.service.clients.records:
            return False
        with self.service.clients.bind(identity):
            return scope_for(self.service.state) == registration['scope']

    def _fence(self, operation='chats'):
        directory = self.service.directory
        semantic = directory.observation_revision
        fields = {'workspaces', 'pinnedSessionIds', 'conversationOrganization'}
        if operation == 'pinned_navigation':
            fields.update({'pinnedWorkspaceIds', 'pinnedItemIds', 'pinOrderCustomized'})
        # Native cache writers own their semantic generations. Its unrelated
        # file/staging commits and child progress must not starve root reads.
        # App writes use this connection, so data_version catches out-of-band
        # local mutations without rejecting ordinary canonical saves.
        return (durable_stamp(directory.db, directory._native_namespace, operation, directory._live_values),
                directory.db.execute('PRAGMA main.data_version').fetchone()[0],
                tuple(semantic[name] for name in query_collections(operation)),
                directory._native_path, directory._native_namespace,
                self.resources._epochs(fields),
                [file_id(self.service.data_dir / 'app.sqlite3'),
                 file_id(directory._native_path) if directory._native_path else None])

    def read(self, key, operation, state, args):
        if self.closed:
            raise PendingQuery(30000)
        now = time.monotonic()
        failure = self.failures.get(key)
        retry = self.retries.get(key)
        if retry is not None:
            interest = self.context
            retry['interests'][(interest[0], interest[2], interest[3])] = interest
        if failure:
            failure['interests'] = {key: item for key, item in failure['interests'].items() if self._current(item)}
            failure['interests'][(self.context[0], self.context[2], self.context[3])] = self.context
            raise PendingQuery(failure['delay'])
        # A snapshot during an unfinished command cannot export its uncommitted
        # directory to a separate connection. The commit publication retries it.
        if self.service.db.in_transaction:
            raise PendingQuery()
        interest = self.context
        job = self.jobs.get(key)
        if job is not None:
            job['interests'][(interest[0], interest[2], interest[3])] = interest
            raise PendingQuery()
        if len(self.jobs) >= MAX_JOBS:
            # Registrations already have a fixed 32-client cap. Remember only
            # one wakeup per client; the next complete state sees all its pages.
            self._prune_capacity()
            self.capacity_waiters[interest[0]] = interest
            raise PendingQuery(1000)
        directory = self.service.directory
        try:
            fence = self._fence(operation)
        except (OSError, sqlite3.Error):
            self._fail(key, 5000, { (interest[0], interest[2], interest[3]): interest })
            raise PendingQuery(5000) from None
        try:
            inputs = capture_inputs(directory, state, operation, args, fence,
                workspace_capture=self._capture_workspaces)
        except InputLimit:
            self._fail(key, 5000, { (interest[0], interest[2], interest[3]): interest })
            raise PendingQuery(5000) from None
        self.jobs[key] = {'inputs': inputs, 'fence': fence, 'queued_at': time.monotonic(),
            'interests': {(interest[0], interest[2], interest[3]): interest}}
        count(self.service, 'shell.query.queued')
        if self.idle is not None:
            self.idle.cancel(); self.idle = None
        if self.runner is None:
            # Payloads retain their exact query minute. Completion notifications
            # must observe current time, not inherit the caller's display clock.
            # Preserve every other context (including client and authority).
            from .chat_navigation import frozen_list_clock
            with frozen_list_clock(None):
                self.runner = asyncio.create_task(self._run())
        raise PendingQuery()

    def _fail(self, key, delay, interests, *, wait=5):
        failure = {'until': time.monotonic() + wait, 'delay': delay, 'interests': dict(interests)}
        self.failures[key] = failure
        while len(self.failures) > 256:
            self.failures.pop(next(iter(self.failures)))
        return failure

    def retry(self, identity):
        """Explicit validated read request; ordinary snapshots never clear errors."""
        failures = [(key, value) for key, value in self.failures.items()
                    if any(item[0] == identity and self._current(item) for item in value['interests'].values())]
        if any(value['delay'] and time.monotonic() < value['until'] for _, value in failures):
            from .errors import AppError
            raise AppError('Navigation is still recovering. Try again shortly.', 429)
        for key, failure in failures:
            self.failures.pop(key, None)
            self.auto_retried.pop(key, None)
            timer = self.retries.get(key)
            if timer is not None:
                timer['task'].cancel()

    def _new_projector(self):
        return self.factory(self.service.data_dir,
            _command=(sys.executable, '-m', 'amplifier_web.shell_query_worker'),
            _timeout=12, _input_limit=INPUT_LIMIT, _output_limit=OUTPUT_LIMIT,
            _spool_limit=64*1024*1024, _max_jobs=1)

    def prune(self):
        """Drop obsolete queued work; retire an active read with no audience.

        A second client's matching interest preserves shared work. Replacement
        reads wait for retirement, so a canceled demand cannot accumulate child
        processes or detached database transactions.
        """
        self._prune_capacity()
        for key, retry in list(self.retries.items()):
            if not any(self._current(item) for item in retry['interests'].values()):
                retry['task'].cancel()
                if self.failures.get(key) == retry['failure']:
                    self.failures.pop(key, None)
        for key, job in list(self.jobs.items()):
            if any(self._current(item) for item in job['interests'].values()):
                continue
            if key != self.active:
                self.jobs.pop(key, None)
            elif self.projector is not None and self.retiring is None:
                job['retiring'] = True
                projector, self.projector = self.projector, None
                self.retiring = asyncio.create_task(projector.close())
        self._wake_capacity()

    async def _run(self):
        try:
            while self.jobs and not self.closed:
                if self.retiring is not None:
                    await asyncio.shield(self.retiring)
                    self.retiring = None
                    # Navigation can remove queued demand while close yields.
                    continue
                key = next(iter(self.jobs))
                job = self.jobs[key]
                if not any(self._current(item) for item in job['interests'].values()):
                    self.jobs.pop(key, None)
                    self._wake_capacity()
                    continue
                if self.projector is None:
                    self.projector = self._new_projector()
                self.active = key
                started_at = time.monotonic()
                try:
                    with measure(self.service, 'shell.query.read'):
                        result = await self.projector.project(job['inputs'],
                            key=hashlib.sha256(repr(key).encode()).hexdigest())
                except asyncio.CancelledError:
                    if self.closed or asyncio.current_task().cancelling():
                        raise
                    # Closing an abandoned projector cancels its internal job,
                    # not this owner. Continue queued replacement demand only
                    # after its exact child has exited.
                    result = {'status': 'retry', 'reason': 'worker_stopped'}
                except Exception:
                    result = {'status': 'retry', 'reason': 'worker_failed'}
                job['completed_at'] = time.monotonic()
                async with self.service.lock:
                    self.active = None
                    self.jobs.pop(key, None)
                    interested = {item[0] for item in job['interests'].values() if self._current(item)}
                    if not interested or self.closed:
                        self._diagnose(job, started_at, result, accepted=False, disposition='no_interest')
                        if not self.closed:
                            self._wake_capacity()
                        continue
                    if job.get('retiring'):
                        # Demand can return while its abandoned read is closing.
                        # Capture a fresh current read; an intentional retirement
                        # is not a catalog failure. The loop awaits the exact old
                        # process before starting any replacement.
                        self._diagnose(job, started_at, result, accepted=False, disposition='retired')
                        for identity in interested:
                            with self.service.clients.bind(identity):
                                self.resources.snapshot(identity)
                            self.resources.shell.notify(identity)
                        self._wake_capacity()
                        continue
                    try:
                        current = not self.service.db.in_transaction and self._fence(job['inputs']['operation']) == job['fence']
                    except (OSError, sqlite3.Error):
                        current = False
                    accepted = current and result.get('status') == 'complete' and result.get('durable') == job['fence'][0]
                    self._diagnose(job, started_at, result, accepted=accepted,
                        disposition='accepted' if accepted else 'stale' if not current or (
                            result.get('status') == 'complete') else 'retry')
                    if accepted:
                        values = self.service.projections.values
                        if len(values) >= 256:
                            values.clear()
                        values[key] = result['value']
                        self.auto_retried.pop(key, None)
                        count(self.service, 'shell.query.completed')
                        for field in ('vmSteps', 'decodedRows'):
                            number = result.get('stats', {}).get(field, 0)
                            if type(number) is int and number >= 0:
                                count(self.service, 'shell.query.' + field, number)
                    else:
                        count(self.service, 'shell.query.retry')
                        changed = not current or result.get('reason') == 'source_changed'
                        automatic = changed and key not in self.auto_retried and len(self.retries) < MAX_JOBS
                        # One delayed read handles a source that went quiet after
                        # rejection. Further changes/errors require explicit Retry.
                        failure = self._fail(key, 0 if automatic else 5000, job['interests'], wait=1 if automatic else 5)
                        if automatic:
                            self.auto_retried[key] = True
                            while len(self.auto_retried) > 256:
                                self.auto_retried.pop(next(iter(self.auto_retried)))
                            retry = {'interests': dict(job['interests']), 'failure': failure}
                            retry['task'] = asyncio.create_task(self._retry_changed(key, retry))
                            self.retries[key] = retry
                    self._wake_capacity()
                    for identity in interested:
                        self.resources.shell.notify(identity)
        finally:
            self.active = None
            self.runner = None
            if not self.closed and self.projector is not None:
                self.idle = asyncio.create_task(self._retire_idle())

    def _prune_capacity(self):
        self.capacity_waiters = {identity: interest for identity, interest in self.capacity_waiters.items()
                                 if self._current(interest)}

    def _diagnose(self, job, started_at, result, *, accepted, disposition):
        diagnostics = getattr(self.service, 'performance_diagnostics', None)
        if diagnostics is not None:
            diagnostics.record_shell_query(job['inputs']['operation'], job['inputs']['args'].get('section'),
                job['queued_at'], started_at, result, accepted=accepted,
                completed_at=job.get('completed_at'), disposition=disposition)

    def _wake_capacity(self):
        if len(self.jobs) >= MAX_JOBS:
            return
        waiters, self.capacity_waiters = self.capacity_waiters, {}
        for identity, interest in waiters.items():
            if self._current(interest):
                self.resources.shell.notify(identity)

    async def _retry_changed(self, key, retry):
        try:
            await asyncio.sleep(1)
            async with self.service.lock:
                interested = {item[0] for item in retry['interests'].values() if self._current(item)}
                if self.closed or not interested or self.failures.get(key) != retry['failure']:
                    return
                self.retries.pop(key, None)
                self.failures.pop(key, None)
                for identity in interested:
                    # Snapshot only registers the current read. It never waits
                    # for the worker or replays the action that changed a view.
                    with self.service.clients.bind(identity):
                        self.resources.snapshot(identity)
                    self.resources.shell.notify(identity)
        finally:
            if self.retries.get(key) is retry:
                self.retries.pop(key, None)

    async def _retire_idle(self):
        try:
            await asyncio.sleep(30)
            # No task can enqueue between this synchronous ownership transfer
            # and closing. New reads wait for that close before a replacement.
            if not self.jobs and self.projector is not None:
                projector, self.projector = self.projector, None
                self.retiring = asyncio.create_task(projector.close())
                await asyncio.shield(self.retiring)
                self.retiring = None
        except asyncio.CancelledError:
            raise
        finally:
            if self.idle is asyncio.current_task():
                self.idle = None

    async def close(self):
        if self.closing is None:
            self.closed = True
            self.invalidate_workspaces()
            self.closing = asyncio.create_task(self._close())
        await asyncio.shield(self.closing)

    async def _close(self):
        retries = [retry['task'] for retry in self.retries.values()]
        for task in retries:
            task.cancel()
        await asyncio.gather(*retries, return_exceptions=True)
        self.retries.clear()
        self.auto_retried.clear()
        if self.idle is not None:
            self.idle.cancel()
            await asyncio.gather(self.idle, return_exceptions=True)
        if self.projector is not None:
            await self.projector.close()
            self.projector = None
        if self.retiring is not None:
            await asyncio.shield(self.retiring)
            self.retiring = None
        if self.runner is not None:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
            self.runner = None
        self.jobs.clear()
        self.capacity_waiters.clear()
        self.failures.clear()
