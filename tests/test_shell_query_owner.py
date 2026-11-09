"""Cold directory reads must not own the host loop or command lock."""
import asyncio
import errno
from copy import deepcopy
from pathlib import Path
import sys
import threading
import time

import pytest

from amplifier_web.history_projection_process import ProcessProjector
from amplifier_web.shell_resources import page_value, scope_for
from amplifier_web.shell_query_model import ShellQueryModel
from test_shell_modules import service


class HeldModel(ShellQueryModel):
    def project(self, inputs, *, check_budget):
        (self.home / 'query-started').touch()
        deadline = time.monotonic() + 8
        while not (self.home / 'query-release').exists():
            check_budget()
            if time.monotonic() > deadline:
                raise TimeoutError()
            time.sleep(.005)
        return super().project(inputs, check_budget=check_budget)


def held_projector(home, **kwargs):
    kwargs['_command'] = (sys.executable, str(Path(__file__).resolve()), 'worker')
    return ProcessProjector(home, **kwargs)


def prepare(service, identity='browser'):
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    client = service.clients.attach(identity)
    client.update(selectedSessionId='alpha', selectedWorkspaceId='one')
    return service.shell.resources


def snapshot(service, wanted=('recentNavigation',), identity='browser', demand=1):
    with service.clients.bind(identity):
        owner = service.shell.resources
        owner.register(identity, demand, wanted, scope_for(service.state))
        return owner.snapshot(identity)


async def settle(service, **kwargs):
    for _ in range(12):
        value = snapshot(service, **kwargs)
        task = service.shell.resources.queries.runner
        if task is None:
            assert not any(row.get('pending') for row in value['instances'].values()), value
            return value
        await asyncio.wait_for(asyncio.shield(task), 15)
    raise AssertionError('Directory query did not settle')


async def started(service):
    async with asyncio.timeout(8):
        while not (service.data_dir / 'query-started').exists():
            await asyncio.sleep(.005)


async def test_cold_snapshot_never_runs_query_on_host_and_releases_loop_lock(service, monkeypatch):
    owner = prepare(service)
    owner.queries.factory = held_projector
    def forbidden(*args, **kwargs):
        raise AssertionError('Cold navigation SQL executed on the host loop')
    monkeypatch.setattr(service.directory, 'chats', forbidden)
    async with service.lock:
        first = snapshot(service)
    for entry in first['instances'].values():
        assert entry['pending']['recentNavigation']['status'] == 'pending'
        assert 'recentNavigation' not in entry['resources']
        assert entry['resources']['context']['value']['selectedSessionId'] == 'alpha'
    await started(service)
    # The worker is demonstrably blocked, yet another controller can acquire
    # the canonical lock and ten independent scheduling turns make progress.
    async with asyncio.timeout(.5):
        async with service.lock:
            service._state['view']['draft'] = 'still editable'
        for _ in range(10):
            await asyncio.sleep(.001)
    assert not owner.queries.runner.done()
    (service.data_dir / 'query-release').touch()
    complete = await settle(service)
    assert complete['instances']['chats']['resources']['recentNavigation']['value']['recentNavigation']['total'] == 2
    assert service._state['view']['draft'] == 'still editable'
    receipts = service.performance_diagnostics.snapshot()['shellQueries']
    assert any(row['operation'] == 'chats' and row['accepted'] and row['decodedRows'] == 2
               for row in receipts)


async def test_delayed_grouped_completion_keeps_payload_clock_not_notification_clock(service, monkeypatch):
    from amplifier_web import chat_navigation
    now = [1_800_000_000]
    monkeypatch.setattr(chat_navigation.time, 'time', lambda: now[0])
    for row in service.state['sessions']:
        row.update(recentActivityAt=now[0]-86400, navigationActivityAt=now[0]-86400)
    owner = prepare(service)
    client = service.shell.client('browser')
    client['baselineView']['navRecentView'] = {'navAge': '24h'}
    service.shell.put('client', 'browser', client)
    owner.queries.factory = held_projector
    notified = []
    original = service.shell.notify
    def notify(identity):
        notified.append(chat_navigation.list_clock())
        original(identity)
    monkeypatch.setattr(service.shell, 'notify', notify)
    snapshot(service, wanted=('sidebarSearch',))
    await started(service)
    assert all(job['inputs']['state']['_navigationListClock'] == now[0]
               for job in owner.queries.jobs.values())
    now[0] += 60
    (service.data_dir / 'query-release').touch()
    await asyncio.wait_for(asyncio.shield(owner.queries.runner), 15)
    assert notified and set(notified) == {now[0]}
    complete = await settle(service, wanted=('sidebarSearch',))
    page = complete['instances']['chats']['resources']['sidebarSearch']['value']['sidebarNavigation']['search']
    assert page['total'] == 0 and page['items'] == []


async def test_exact_existing_query_oracle_and_independent_full_api(service, tmp_path):
    from amplifier_web.native_catalog import NativeCatalog
    from test_native_directory import seed
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    _, workspace = seed(catalog, tmp_path / 'workspace', 135)
    service.state['workspaces'].append(workspace)
    service.state.update(pinnedSessionIds=['alpha', 'saved-00005'], pinnedWorkspaceIds=['two'])
    service.state['sessions'][1]['collaboration'] = {'creatorSessionId': 'alpha', 'requestId': 'beta'}
    prepare(service)
    service._save_changes(sessions={'beta'}, globals={'pinnedSessionIds', 'pinnedWorkspaceIds'})
    service.directory.bind(catalog)
    wanted = ('recentNavigation', 'pinnedNavigation', 'workspaceShortcuts', 'sidebarSearch', 'sidebarWorkspace')
    actual = await settle(service, wanted=wanted)
    with service.clients.bind('browser'):
        client = service.shell.client('browser')
        from amplifier_web.shell_components import resolved
        for instance in resolved(client['composition']):
            if instance['id'] not in actual['instances']:
                continue
            # The independent explicit API still runs its exact full projection.
            expected = service.shell.navigation(client, instance, identity='browser', navigation_resources=wanted)
            for name in wanted:
                assert actual['instances'][instance['id']]['resources'][name]['value'] == page_value(expected, name)


async def test_pending_retains_only_same_query_and_status_revision_orders_equal_values(service):
    owner = prepare(service)
    first = await settle(service)
    frozen = deepcopy(first)
    before = first['instances']['chats']
    owner.queries.factory = held_projector
    await owner.queries.projector.close()
    owner.queries.projector = None
    # A conservative external invalidation can leave exact visible values equal.
    service.projections.invalidate()
    owner.invalidate()
    pending = snapshot(service)
    entry = pending['instances']['chats']
    assert entry['resources']['recentNavigation'] == before['resources']['recentNavigation']
    assert entry['pending']['recentNavigation']['status'] == 'pending'
    assert entry['stateRevision'] > before['stateRevision']
    await started(service)
    (service.data_dir / 'query-release').touch()
    ready = (await settle(service))['instances']['chats']
    assert ready['resources']['recentNavigation'] == before['resources']['recentNavigation']
    assert ready['stateRevision'] > entry['stateRevision'] and ready['pending'] == {}
    assert first == frozen
    # Filter changes are a new query even when the envelope's scope5 is equal.
    client = service.shell.client('browser')
    client['baselineView']['navRecentView'] = {'navFilter': 'no match'}
    service.shell.put('client', 'browser', client)
    changed = snapshot(service, wanted=('sidebarSearch',), demand=2)
    assert 'sidebarSearch' not in changed['instances']['chats']['resources']
    assert changed['instances']['chats']['pending']['sidebarSearch']['status'] == 'pending'


async def test_late_source_change_and_replaced_demand_never_install_stale_page(service):
    owner = prepare(service)
    owner.queries.factory = held_projector
    snapshot(service)
    await started(service)
    service.state['sessions'][0]['title'] = 'New canonical title'
    service._save_changes(sessions={'alpha'})
    client = service.clients.records['browser']
    client.update(selectedSessionId='beta', selectedWorkspaceId='two')
    snapshot(service, demand=2)
    (service.data_dir / 'query-release').touch()
    result = await settle(service, demand=2)
    page = result['instances']['chats']['resources']['recentNavigation']['value']['recentNavigation']
    assert page['scope']['selectedSessionId'] == 'beta'
    assert next(row for row in page['items'] if row['id'] == 'alpha')['title'] == 'New canonical title'
    assert not owner.queries.jobs


async def test_settled_search_tracks_committed_workspace_names_and_paths(service):
    from test_shell_modules import command
    prepare(service)
    client = service.shell.client('browser')
    client['baselineView']['navRecentView'] = {'navFilter': 'Personal*'}
    service.shell.put('client', 'browser', client)
    wanted = ('sidebarSearch',)
    first = await settle(service, wanted=wanted)
    def page(value):
        return value['instances']['chats']['resources']['sidebarSearch']['value']['sidebarNavigation']['search']
    assert [row['id'] for row in page(first)['items']] == ['alpha']
    frozen = deepcopy(first)
    workspace = service.state['workspaces'][0]
    workspace['name'] = 'Renamed notebook'
    service._save_changes(globals={'workspaces'})
    renamed = await settle(service, wanted=wanted)
    assert page(renamed)['items'] == []
    await command(service, 'shell.view.update', clientId='browser', instanceId='chats',
        patch={'navRecentView': {'navFilter': '*/changed/*'}})
    assert page(await settle(service, wanted=wanted))['items'] == []
    workspace['path'] = '/changed/canonical/workspace'
    service._save_changes(globals={'workspaces'})
    moved = await settle(service, wanted=wanted)
    assert [row['id'] for row in page(moved)['items']] == ['alpha']
    assert page(moved)['items'][0]['workspaceName'] == 'Renamed notebook'
    assert first == frozen


async def test_external_write_without_generation_is_rejected_and_retry_backs_off(service):
    import sqlite3
    owner = prepare(service)
    owner.queries.factory = held_projector
    snapshot(service)
    await started(service)
    with sqlite3.connect(service.data_dir / 'app.sqlite3') as db:
        db.execute("UPDATE history_local_rows SET title='External repair' WHERE id='alpha'")
    (service.data_dir / 'query-release').touch()
    await owner.queries.runner
    result = snapshot(service)
    assert result['instances']['chats']['pending']['recentNavigation']['status'] == 'pending'
    assert not any(key[0] == 'directory-chats' for key in service.projections.values)
    assert owner.queries.runner is None
    for _ in range(10):
        assert snapshot(service) == result
    retries = [row['task'] for row in owner.queries.retries.values()]
    assert len(retries) == 1
    await asyncio.gather(*retries)
    assert owner.queries.runner is not None  # no new client read was necessary
    await owner.queries.runner
    assert not snapshot(service)['instances']['chats']['pending']


async def test_shared_query_survives_one_client_departure_and_close_reaps_worker(service):
    owner = prepare(service)
    owner.queries.factory = held_projector
    service.clients.attach('peer').update(selectedSessionId='alpha', selectedWorkspaceId='one')
    snapshot(service)
    snapshot(service, identity='peer')
    assert len(owner.queries.jobs) == 1
    await started(service)
    snapshot(service, wanted=(), demand=2)
    (service.data_dir / 'query-release').touch()
    await settle(service, identity='peer')
    process = owner.queries.projector._process
    root = owner.queries.projector._root
    await owner.close()
    assert process.returncode == 0
    assert not root.exists()
    assert not owner.queries.jobs and owner.queries.runner is None


async def test_releasing_last_interest_retires_blocked_worker_without_query_completion(service):
    owner = prepare(service)
    owner.queries.factory = held_projector
    snapshot(service)
    await started(service)
    process = owner.queries.projector._process
    spool = owner.queries.projector._root
    snapshot(service, wanted=(), demand=2)
    async with asyncio.timeout(5):
        await owner.queries.runner
        if owner.queries.retiring is not None:
            await owner.queries.retiring
    assert process.returncode == 0 and not spool.exists()
    assert not owner.queries.jobs
    assert not (service.data_dir / 'query-release').exists()


@pytest.mark.parametrize('resource', ['recentNavigation', 'workStatusIdle'])
async def test_returning_demand_waits_for_its_retiring_query_without_retry(service, resource):
    owner = prepare(service)
    closing, release_close = asyncio.Event(), asyncio.Event()
    projectors = []

    class GatedClose(ProcessProjector):
        async def close(self):
            closing.set()
            await release_close.wait()
            await super().close()

    def factory(home, **kwargs):
        kwargs['_command'] = (sys.executable, str(Path(__file__).resolve()), 'worker')
        projector = GatedClose(home, **kwargs)
        projectors.append(projector)
        return projector

    owner.queries.factory = factory
    try:
        snapshot(service, wanted=(resource,))
        await started(service)
        process, spool = projectors[0]._process, projectors[0]._root
        snapshot(service, wanted=(), demand=2)
        retiring = owner.queries.retiring
        await asyncio.wait_for(closing.wait(), 5)
        # The user returns to exactly the same page before its canceled read
        # has stopped. This is renewed demand, not a failed catalog read.
        snapshot(service, wanted=(resource,), demand=3)
        assert len(projectors) == 1
        release_close.set()
        await asyncio.wait_for(asyncio.shield(retiring), 5)
        assert process.returncode == 0 and not spool.exists()
        (service.data_dir / 'query-release').touch()
        result = await settle(service, wanted=(resource,), demand=3)
        page = result['instances']['chats']['resources'][resource]['value'][resource]
        assert page['total'] == 2
        assert len(projectors) == 2
        assert not owner.queries.failures
        assert service.performance_diagnostics.counters.get('shell.query.retry', 0) == 0
    finally:
        release_close.set()


@pytest.mark.parametrize('resource', ['recentNavigation', 'workStatusIdle'])
async def test_returning_demand_recovers_after_failed_spool_allocation(service, resource):
    owner = prepare(service)
    entered, release = threading.Event(), threading.Event()
    projectors = []

    class FailedAllocation(ProcessProjector):
        def _make_root(self):
            entered.set()
            assert release.wait(5)
            raise OSError(errno.ENOSPC, 'private spool path')

    def factory(home, **kwargs):
        kind = FailedAllocation if not projectors else ProcessProjector
        projector = kind(home, **kwargs)
        projectors.append(projector)
        return projector

    owner.queries.factory = factory
    try:
        snapshot(service, wanted=(resource,))
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.001)
        snapshot(service, wanted=(), demand=2)
        retiring = owner.queries.retiring
        assert retiring is not None
        snapshot(service, wanted=(resource,), demand=3)
        assert len(projectors) == 1 and projectors[0]._process is None
        release.set()
        await asyncio.wait_for(asyncio.shield(retiring), 5)
        result = await settle(service, wanted=(resource,), demand=3)
        page = result['instances']['chats']['resources'][resource]['value'][resource]
        assert page['total'] == 2 and len(projectors) == 2
        assert not owner.queries.failures and owner.queries.retiring is None
        failures = [row['failure'] for row in service.performance_diagnostics.snapshot()['shellQueries']
                    if 'failure' in row]
        assert len(failures) == 1
        assert failures[0]['stage'] == 'spool_setup' and failures[0]['errno'] == errno.ENOSPC
        process, spool = projectors[1]._process, projectors[1]._root
        await owner.close()
        assert process.returncode == 0 and not spool.exists()
        assert not list(service.data_dir.glob('projection-spool-*'))
    finally:
        release.set()


@pytest.mark.parametrize('resource', ['recentNavigation', 'workStatusIdle'])
async def test_departing_queued_demand_during_retirement_leaves_owner_usable(service, resource):
    owner = prepare(service)
    closing, release_close = asyncio.Event(), asyncio.Event()
    projectors = []

    class GatedClose(ProcessProjector):
        async def close(self):
            await super().close()
            closing.set()
            await release_close.wait()

    def factory(home, **kwargs):
        kwargs['_command'] = (sys.executable, str(Path(__file__).resolve()), 'worker')
        projector = GatedClose(home, **kwargs)
        projectors.append(projector)
        return projector

    owner.queries.factory = factory
    try:
        snapshot(service, wanted=(resource,))
        await started(service)
        process, spool = projectors[0]._process, projectors[0]._root
        replacement = 'workStatusIdle' if resource == 'recentNavigation' else 'recentNavigation'
        snapshot(service, wanted=(replacement,), demand=2)
        runner = owner.queries.runner
        await asyncio.wait_for(closing.wait(), 5)
        async with asyncio.timeout(5):
            while owner.queries.active is not None:
                await asyncio.sleep(0)
        # The old child has exited and the owner is awaiting its held close.
        # Remove the queued replacement while that await yields to navigation.
        assert process.returncode == 0 and not spool.exists()
        assert owner.queries.jobs and not runner.done()
        snapshot(service, wanted=(), demand=3)
        assert not owner.queries.jobs
        release_close.set()
        await asyncio.wait_for(asyncio.shield(runner), 5)
        assert owner.queries.runner is None and owner.queries.active is None
        assert len(projectors) == 1
        assert not owner.queries.failures
        assert service.performance_diagnostics.counters.get('shell.query.retry', 0) == 0

        # A later visit still creates one replacement and returns current rows.
        (service.data_dir / 'query-release').touch()
        result = await settle(service, wanted=(resource,), demand=4)
        page = result['instances']['chats']['resources'][resource]['value'][resource]
        assert page['total'] == 2 and len(projectors) == 2
    finally:
        release_close.set()


async def test_queue_is_bounded_and_input_has_no_transcript_or_private_draft(service):
    owner = prepare(service)
    owner.queries.factory = held_projector
    for number in range(9):
        identity = f'browser-{number}'
        service.clients.attach(identity).update(selectedSessionId='alpha', selectedWorkspaceId='one')
        client = service.shell.client(identity)
        client['baselineView']['navRecentView'] = {'navFilter': f'query-{number}'}
        service.shell.put('client', identity, client)
        frame = snapshot(service, wanted=('sidebarSearch',), identity=identity)
    assert len(owner.queries.jobs) == 8
    assert frame['instances']['chats']['pending']['sidebarSearch']['status'] == 'retry'
    for job in owner.queries.jobs.values():
        assert 'sessions' not in job['inputs']['state']
        assert 'draft' not in job['inputs']['state']['view']
        assert 'runtimeControl' not in job['inputs']['state']
    await owner.close()


async def test_worker_database_connections_are_read_only_and_uncommitted_input_is_not_exported(service, monkeypatch):
    import sqlite3
    from amplifier_web import history_directory_query
    owner = prepare(service)
    snapshot(service)
    inputs = next(iter(owner.queries.jobs.values()))['inputs']
    def forbidden_write(directory, state, **kwargs):
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            directory.db.execute("DELETE FROM history_local_rows WHERE id='alpha'")
        return {'readOnly': True}
    monkeypatch.setattr(history_directory_query, 'chats', forbidden_write)
    assert ShellQueryModel(service.data_dir).project(inputs)['value'] == {'readOnly': True}
    await owner.close()
    # A separate fresh owner must wait for canonical commit, never commit a
    # caller's transaction merely to make it visible to the query process.
    from amplifier_web.shell_query_owner import ShellQueryOwner
    owner.queries = ShellQueryOwner(owner)
    service.db.execute('BEGIN')
    pending = snapshot(service, wanted=('sidebarSearch',), demand=2)
    assert pending['instances']['chats']['pending']['sidebarSearch']['status'] == 'pending'
    assert not owner.queries.jobs and service.db.in_transaction
    service.db.rollback()


@pytest.mark.parametrize('limit', ['input', 'output'])
async def test_process_artifact_limit_is_retryable_without_immediate_resubmission(service, limit):
    owner = prepare(service)
    def limited(home, **kwargs):
        kwargs['_' + limit + '_limit'] = 64
        return ProcessProjector(home, **kwargs)
    owner.queries.factory = limited
    snapshot(service)
    await owner.queries.runner
    failed = snapshot(service)
    assert failed['instances']['chats']['pending']['recentNavigation'] == {'status':'retry', 'retryAfterMs':5000}
    assert 'recentNavigation' not in failed['instances']['chats']['resources']
    assert owner.queries.runner is None
    for _ in range(10):
        assert snapshot(service) == failed
    assert not owner.queries.jobs
    from amplifier_web.service import AppError
    with pytest.raises(AppError) as limited_retry:
        owner.queries.retry('browser')
    assert limited_retry.value.status == 429
    for failure in owner.queries.failures.values():
        failure['until'] = time.monotonic() - 1
    assert snapshot(service) == failed  # no implicit retry after cooldown
    owner.queries.retry('browser')
    snapshot(service)
    assert owner.queries.runner is not None
    await owner.queries.runner


async def test_actual_27000_query_preserves_loop_progress_exact_rows_and_bounded_decode(service, tmp_path):
    import json
    from amplifier_web.native_catalog import NativeCatalog
    from test_native_directory import seed
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    _, workspace = seed(catalog, tmp_path / 'workspace', 27000)
    service.state['workspaces'].append(workspace)
    prepare(service)
    service.directory.bind(catalog)
    # The exact full reader is the oracle, separately timed from asynchronous
    # delivery. No query or timing ceiling is weakened for the worker path.
    with service.clients.bind('browser'):
        client = service.shell.client('browser')
        instance = {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'}
        oracle = service.shell.navigation(client, instance, identity='browser',
            navigation_resources={'sidebarSearch'})['sidebarNavigation']['search']
    service.projections.invalidate()
    delays = []
    async def heartbeat():
        previous = time.monotonic()
        while True:
            await asyncio.sleep(.005)
            now = time.monotonic()
            delays.append((now-previous)*1000)
            previous = now
    task = asyncio.create_task(heartbeat())
    start = time.monotonic()
    try:
        result = await settle(service, wanted=('sidebarSearch',))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    actual = result['instances']['chats']['resources']['sidebarSearch']['value']['sidebarNavigation']['search']
    assert actual == oracle
    assert len(delays) >= 10
    assert max(delays) < 150  # A one-second query cannot own the event loop.
    counters = service.performance_diagnostics.counters
    assert counters['shell.query.decodedRows'] <= 100
    print(json.dumps({'dormantRoots':27000,'elapsedMs':(time.monotonic()-start)*1000,
        'heartbeatSamples':len(delays),'maxHeartbeatMs':max(delays),
        'workerDecodedRows':counters['shell.query.decodedRows'],
        'workerVmSteps':counters['shell.query.vmSteps'],'exactOracle':True}))


async def test_input_admission_copies_only_query_facts_and_rejects_before_large_copy(service, monkeypatch):
    from amplifier_web import shell_query_owner
    owner = prepare(service)
    class Private:
        def __deepcopy__(self, memo):
            raise AssertionError('Private payload copied')
    state = deepcopy(service._state)
    state['workspaces'][0]['configuration'] = Private()
    state['conversationOrganization'] = {'archived': {'beta': Private()}, 'collections': Private()}
    directory = service.directory
    fence = owner.queries._fence()
    captured = shell_query_owner.capture_inputs(directory, state, 'chats', {'section': None}, fence)
    assert set(captured['state']['workspaces'][0]) <= shell_query_owner.WORKSPACE_FIELDS
    assert captured['state']['conversationOrganization'] == {'archived': {'beta': None}}
    assert all(len(row) == 5 for row in captured['notices'])
    # An immutable scalar larger than the budget is refused before copying the
    # next row or constructing the complete input/output serialization.
    monkeypatch.setattr(shell_query_owner, 'INPUT_LIMIT', 128)
    with pytest.raises(shell_query_owner.InputLimit):
        shell_query_owner.capture_inputs(directory, state, 'chats', {}, fence)


async def test_distinct_cold_queries_share_registry_and_commit_replaces_it_without_mutating_jobs(service, monkeypatch):
    from amplifier_web import shell_query_owner

    owner = prepare(service)
    captures = []
    original = shell_query_owner.InputBudget.copy

    def capture(budget, value, depth=0):
        if isinstance(value, dict) and {'id', 'path', 'name'} <= value.keys():
            captures.append(value['id'])
        return original(budget, value, depth)

    monkeypatch.setattr(shell_query_owner.InputBudget, 'copy', capture)
    wanted = ('recentNavigation', 'sidebarSearch', 'workspaceShortcuts')
    snapshot(service, wanted=wanted)
    jobs = list(owner.queries.jobs.values())
    assert len(jobs) >= 2
    first = jobs[0]['inputs']['state']['workspaces']
    old = deepcopy(first)
    assert all(job['inputs']['state']['workspaces'] is first for job in jobs)
    assert captures == [row['id'] for row in service._state['workspaces']]
    assert first is not service._state['workspaces']
    assert all(frozen is not live for frozen, live in zip(first, service._state['workspaces']))
    assert all(set(row) <= shell_query_owner.WORKSPACE_FIELDS for row in first)

    service._state['workspaces'][0]['name'] = 'Committed replacement name'
    service._save_changes(globals={'workspaces'})
    assert owner.queries.workspace_snapshot is None
    snapshot(service, wanted=wanted)
    following = list(owner.queries.jobs.values())
    assert following
    current = following[0]['inputs']['state']['workspaces']
    assert all(job['inputs']['state']['workspaces'] is current for job in following)
    assert current is not first and current[0]['name'] == 'Committed replacement name'
    assert first == old
    assert captures == [row['id'] for row in service._state['workspaces']] * 2
    # No scheduling turn has started a worker; close still releases the cache
    # and all queued references without altering inputs already handed out.
    await owner.close()
    assert owner.queries.workspace_snapshot is None and not owner.queries.jobs
    assert first == old


@pytest.mark.parametrize('invalidation', ['workspaces', 'unknown'])
async def test_workspace_invalidation_releases_old_registry_before_next_query(service, invalidation):
    import gc
    import weakref
    from amplifier_web.shell_query_owner import capture_inputs

    owner = prepare(service)
    fence = owner.queries._fence()
    original_registry = service._state['workspaces']

    class Configuration:
        pass

    service._state['workspaces'] = [{'id': 'temporary', 'path': '/temporary',
        'name': 'Temporary workspace', 'available': True, 'configuration': Configuration()}]
    marker = weakref.ref(service._state['workspaces'][0]['configuration'])
    inputs = capture_inputs(service.directory, service._state, 'chats', {'section': None}, fence,
        workspace_capture=owner.queries._capture_workspaces)
    frozen = deepcopy(inputs)
    epoch = owner._epochs({'workspaces'})
    cache_identity = id(owner.queries.workspace_snapshot)
    owner.invalidate({'settings'})
    assert owner._epochs({'workspaces'}) == epoch
    assert id(owner.queries.workspace_snapshot) == cache_identity
    service._state['workspaces'] = original_registry
    owner.invalidate({'workspaces'} if invalidation == 'workspaces' else None)
    gc.collect()
    # Retained job input owns only the detached query shape. No subsequent
    # query or close should be needed to release excluded canonical payloads.
    assert marker() is None
    assert owner.queries.workspace_snapshot is None
    assert owner._epochs({'workspaces'}) != epoch
    assert inputs == frozen and 'configuration' not in inputs['state']['workspaces'][0]
    await owner.close()


@pytest.mark.parametrize('invalidation', ['unknown_projection', 'unknown_resources', 'list_replacement'])
async def test_workspace_input_snapshot_observes_non_commit_invalidation(service, invalidation):
    from amplifier_web.shell_query_owner import capture_inputs

    owner = prepare(service)

    def capture(state=None):
        return capture_inputs(service.directory, service._state if state is None else state, 'chats',
            {'section': None}, owner.queries._fence(), workspace_capture=owner.queries._capture_workspaces)

    first = capture()['state']['workspaces']
    old = deepcopy(first)
    if invalidation == 'list_replacement':
        service._state['workspaces'] = deepcopy(service._state['workspaces'])
    service._state['workspaces'][0]['available'] = False
    if invalidation == 'unknown_projection':
        epoch = owner._epochs({'workspaces'})
        service.projections.invalidate()
        assert owner._epochs({'workspaces'}) == epoch
        assert owner.queries.workspace_snapshot is None
    elif invalidation == 'unknown_resources':
        owner.invalidate()
    following = capture()['state']['workspaces']
    assert following is not first and following[0]['available'] is False
    assert first == old

    # A standalone or alternate registry has no canonical epoch. It cannot
    # borrow or replace the owner's cached facts, even with the same row IDs.
    alternate = {**service._state, 'workspaces': deepcopy(service._state['workspaces'])}
    alternate['workspaces'][0]['name'] = 'Alternate registry'
    assert capture(alternate)['state']['workspaces'][0]['name'] == 'Alternate registry'
    alternate['workspaces'][0]['name'] = 'Changed again without a canonical write'
    assert capture(alternate)['state']['workspaces'][0]['name'] == 'Changed again without a canonical write'
    assert capture()['state']['workspaces'] is following
    await owner.close()
    assert owner.queries.workspace_snapshot is None


async def test_cached_registry_is_fully_charged_to_each_query_input_limit(service, monkeypatch):
    from amplifier_web import shell_query_owner

    owner = prepare(service)
    service._state['workspaces'] = [{'id': str(number), 'path': '/workspace/' + 'x' * 200 + str(number),
        'name': 'Workspace ' + str(number), 'available': True, 'configuration': 'private configuration'}
        for number in range(32)]
    monkeypatch.setattr(shell_query_owner, 'INPUT_LIMIT', 32 * 1024)
    fence = owner.queries._fence()

    def capture(args, workspace_capture=owner.queries._capture_workspaces):
        return shell_query_owner.capture_inputs(service.directory, service._state, 'chats', args,
            fence, workspace_capture=workspace_capture)

    first = capture({})
    shared = first['state']['workspaces']
    charge = owner.queries.workspace_snapshot[3]
    assert capture({})['state']['workspaces'] is shared
    # The rest of this input fits on its own, but adding the registry must
    # reject both cold and cached captures at the same per-query boundary.
    args = {'payload': 'x' * (shell_query_owner.INPUT_LIMIT - 512 - charge // 2)}
    capture(args, workspace_capture=lambda rows, budget: [])
    for capture_registry in (shell_query_owner.capture_workspaces, owner.queries._capture_workspaces):
        with pytest.raises(shell_query_owner.InputLimit):
            capture(args, workspace_capture=capture_registry)
    assert owner.queries.workspace_snapshot[2] is shared
    assert all('configuration' not in row for row in shared)
    await owner.close()


async def test_source_change_gets_only_one_delayed_retry_and_close_cancels_timer(service):
    owner = prepare(service)
    class Changed:
        calls = 0
        def __init__(self, *args, **kwargs):
            pass
        async def project(self, *args, **kwargs):
            Changed.calls += 1
            return {'status': 'retry', 'reason': 'source_changed'}
        async def close(self):
            pass
    owner.queries.factory = Changed
    snapshot(service)
    await owner.queries.runner
    timer = next(iter(owner.queries.retries.values()))['task']
    await timer
    if owner.queries.runner is not None:
        await owner.queries.runner
    failed = snapshot(service)
    assert Changed.calls == 2 and not owner.queries.retries
    assert failed['instances']['chats']['pending']['recentNavigation'] == {'status': 'retry', 'retryAfterMs': 5000}
    for failure in owner.queries.failures.values():
        failure['until'] = time.monotonic() - 1
    for _ in range(10):
        assert snapshot(service) == failed
    assert Changed.calls == 2  # Backoff expiry alone does not grant another read.
    owner.queries.retry('browser')
    snapshot(service)
    await owner.queries.runner
    assert Changed.calls == 3 and owner.queries.retries
    # A different current read may own its own single delay, but shutdown must
    # cancel and await it; it cannot wake a closed host or create a new worker.
    snapshot(service, wanted=('sidebarSearch',), demand=2)
    await owner.queries.runner
    timer = next(iter(owner.queries.retries.values()))['task']
    await owner.close()
    assert timer.done() and not owner.queries.retries and owner.queries.runner is None
    assert Changed.calls == 4


@pytest.mark.parametrize('release_slot', ['completion', 'obsolete'])
async def test_ninth_current_client_is_woken_when_capacity_opens(service, monkeypatch, release_slot):
    owner = prepare(service)
    owner.queries.factory = held_projector
    notified = []
    original = service.shell.notify
    def record(identity):
        notified.append(identity)
        original(identity)
    monkeypatch.setattr(service.shell, 'notify', record)
    for number in range(9):
        identity = f'queue-{number}'
        service.clients.attach(identity).update(selectedSessionId='alpha', selectedWorkspaceId='one')
        client = service.shell.client(identity)
        client['baselineView']['navRecentView'] = {'navFilter': f'query-{number}'}
        service.shell.put('client', identity, client)
        frame = snapshot(service, wanted=('sidebarSearch',), identity=identity)
    assert frame['instances']['chats']['pending']['sidebarSearch']['status'] == 'retry'
    assert 'queue-8' not in notified
    await started(service)
    if release_slot == 'obsolete':
        snapshot(service, wanted=(), identity='queue-1', demand=2)
        assert 'queue-8' in notified  # no completed query or future publication needed
    (service.data_dir / 'query-release').touch()
    await owner.queries.runner
    assert 'queue-8' in notified
    complete = await settle(service, wanted=('sidebarSearch',), identity='queue-8')
    assert not complete['instances']['chats']['pending']


async def test_concurrent_child_progress_does_not_starve_cold_root_queries(service, tmp_path):
    from amplifier_web.native_catalog import NativeCatalog
    workspace = deepcopy(service.state['workspaces'][0])
    root = {'id': 'native-root', 'nativeIdentity': 'native-root', 'nativeProject': 'progress',
        'sessionKind': 'root', 'workspaceId': workspace['id'], 'workspace': workspace['path'],
        'title': 'Saved root', 'createdAt': 1, 'recentActivityAt': 1}
    child = {**root, 'id': 'native-child', 'nativeIdentity': 'native-child',
        'sessionKind': 'worker', 'parentId': 'native-root', 'title': 'Child'}
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    projects = {'progress': {'workspace': workspace, 'sessions': [root, child]}}
    catalog.save(projects, {}, {}, ())
    row = service.state['sessions'][1]
    row.update(sessionKind='worker', parentId='alpha')
    owner = prepare(service)
    service.directory.bind(catalog)
    owner.queries.factory = held_projector
    wanted = ('recentNavigation', 'workspaceShortcuts')
    snapshot(service, wanted=wanted)
    await started(service)
    for number in range(20):
        following = deepcopy(projects)
        following['progress']['sessions'][1].update(title=f'Child progress {number}', recentActivityAt=number + 2)
        catalog.save(following, projects, {}, ())
        projects = following
        row.update(title=f'Local child {number}', status='working', manualUnread=str(number))
        service._save_changes(sessions={'beta'})
        # Host scheduling continues while canonical writers update unrelated
        # child facts; they are deliberately newer than the frozen worker input.
        await asyncio.sleep(.001)
    (service.data_dir / 'query-release').touch()
    result = await settle(service, wanted=wanted)
    assert service.performance_diagnostics.counters.get('shell.query.retry', 0) == 0
    assert not owner.queries.retries
    with service.clients.bind('browser'):
        client = service.shell.client('browser')
        expected = service.shell.navigation(client, {'id': 'chats', 'package': 'builtin.chats'},
            identity='browser', navigation_resources=wanted)
    for name in wanted:
        assert result['instances']['chats']['resources'][name]['value'] == page_value(expected, name)


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
async def test_unqualified_platform_keeps_current_synchronous_navigation(service, monkeypatch, platform):
    from amplifier_web import shell_query_owner
    owner = prepare(service)
    await owner.close()
    monkeypatch.setattr(shell_query_owner.sys, 'platform', platform)
    owner.queries = shell_query_owner.ShellQueryOwner(owner)
    assert owner.queries.enabled is False
    def forbidden(*args, **kwargs):
        raise AssertionError('Unqualified platform started query process')
    owner.queries.factory = forbidden
    result = snapshot(service)
    assert not result['instances']['chats']['pending']
    assert result['instances']['chats']['resources']['recentNavigation']['value']['recentNavigation']['total'] == 2
    assert not owner.queries.jobs and owner.queries.runner is None


def test_input_preparation_accepts_5000_registration_rows_without_expanded_payloads():
    import json
    from types import SimpleNamespace
    from amplifier_web.shell_query_owner import capture_inputs, INPUT_LIMIT, InputBudget
    directory = SimpleNamespace(_live_values={}, _live_notices=[], _native_path=None, _native_namespace=None)
    state = {'workspaces': [{'id': str(number), 'path': '/workspace/' + 'x' * 180 + str(number),
        'name': 'Workspace ' + str(number), 'available': True, 'archived': False,
        'configuration': object()} for number in range(5000)]}
    captured = capture_inputs(directory, state, 'workspace_counts', {'archive': 'active'}, ([[], []], [None, None]))
    assert len(captured['state']['workspaces']) == 5000
    assert len(json.dumps(captured, ensure_ascii=False).encode()) < INPUT_LIMIT
    assert all('configuration' not in row for row in captured['state']['workspaces'])
    budget = InputBudget()
    text = '"\\\n\x01é𝄞' * 2000
    before = budget.remaining
    assert budget.copy(text) == text
    assert before - budget.remaining == len(json.dumps(text, ensure_ascii=False).encode())


if __name__ == '__main__':
    import argparse
    from amplifier_web.history_projection_worker import run
    from amplifier_web.shell_query_worker import INPUT_LIMIT, OUTPUT_LIMIT
    parser = argparse.ArgumentParser()
    parser.add_argument('worker')
    parser.add_argument('--spool')
    parser.add_argument('--cache-home')
    parser.add_argument('--memory-limit', type=int)
    args = parser.parse_args()
    run(args.spool, args.cache_home, model_factory=HeldModel, memory_limit=args.memory_limit,
        input_limit=INPUT_LIMIT, output_limit=OUTPUT_LIMIT)
