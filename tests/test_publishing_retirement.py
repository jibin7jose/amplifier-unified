"""Actual local-body effects, recovery, readers and bounded owner admission."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading

import pytest

from amplifier_publishing import Publisher, PublishingError
import amplifier_publishing.publisher as publishing
import amplifier_publishing.retirement as retirement
from test_publishing_library import publisher, source, review, deploy, http
from test_publishing_retention import capture


@pytest.fixture
def clock(monkeypatch):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(publishing, '_now', lambda: now[0].isoformat())
    monkeypatch.setattr(retirement, '_now', lambda: now[0].isoformat())
    return now


def retire(owner, release, request='retire', **kwargs):
    return owner.retire(release['id'], session_id='session-one', request_id=request,
                        expected_revision=owner.retention(release['id'], 'session-one')['revision'],
                        policy=kwargs.pop('policy', {}), **kwargs)


def candidate(owner, release, **kwargs):
    return next(item for item in owner.retirement_candidates('session-one', policy=kwargs.pop('policy', {}), **kwargs)['items']
                if item['releaseId'] == release['id'])


def test_exact_owned_body_removed_history_and_source_retained(clock, publisher, source, tmp_path):
    release = capture(publisher, source)
    before = copy.deepcopy(publisher.receipts('session-one'))
    clock[0] += timedelta(days=31)
    assert candidate(publisher, release)['eligible']
    result = retire(publisher, release)
    assert result['state'] == 'succeeded' and result['result']['availability'] == 'expired'
    assert 'files' not in result['result'] and 'retirement' not in result['result']
    assert not (publisher._content/release['id']).exists()
    assert not list((publisher.root/'retiring').iterdir())
    assert publisher.receipt('session-one', 'capture') == before[0]
    assert (source/'index.html').read_text() == '<h1>Version one</h1>'
    assert publisher.retire(release['id'], session_id='session-one', request_id='retire', expected_revision=0, policy={}) == result
    assert capture(publisher, source) == release
    with pytest.raises(PublishingError, match='expired'):
        publisher.export_release(release['id'], 'session-one')
    publisher.snapshot(tmp_path/'backup')
    with Publisher(tmp_path/'backup') as backup:
        assert backup.receipt('session-one', 'retire') == result
        assert backup.retention(release['id'], 'session-one')['availability'] == 'expired'
    restored = capture(publisher, source, 'intentional-new-build')
    assert restored['id'] == release['id'] and restored['createdAt'] == release['createdAt']
    assert restored['retention']['payloadGeneration'] == 2
    assert publisher.receipt('session-one', 'retire') == result


def test_disabled_durable_and_longer_current_policy_never_admit(clock, publisher, source):
    durable = publisher.build(source, site_id='site-one', session_id='session-one', request_id='durable')
    (source/'index.html').write_text('temporary')
    temporary = capture(publisher, source)
    clock[0] += timedelta(days=31)
    assert candidate(publisher, temporary, policy=None)['hold'] == 'policy-disabled'
    assert candidate(publisher, temporary, policy={'keepDays':60})['hold'] == 'grace'
    for index, (release, settings) in enumerate([(durable, {}), (temporary, None), (temporary, {'keepDays':60})]):
        with pytest.raises(PublishingError) as error:
            retire(publisher, release, f'denied-{index}', policy=settings)
        assert error.value.receipt['state'] == 'failed'
        assert (publisher._content/release['id']).exists()
    assert publisher.retention(durable['id'], 'session-one')['policy'] is None


def test_current_stopped_and_preview_consumers_have_distinct_release_semantics(clock, publisher, source):
    release = capture(publisher, source)
    review(publisher, release); deploy(publisher, release)
    clock[0] += timedelta(days=100)
    assert candidate(publisher, release)['hold'] == 'current-or-resumable'
    publisher.stop('site-one', session_id='session-one', request_id='stop', expected_revision=1)
    assert candidate(publisher, release)['hold'] == 'current-or-resumable'
    publisher.remove('site-one', session_id='session-one', request_id='remove', expected_revision=2)
    assert candidate(publisher, release)['hold'] == 'grace'
    clock[0] += timedelta(days=31)
    publisher.preview(release['id'], session_id='session-one', request_id='preview')
    assert candidate(publisher, release)['hold'] == 'live-or-unsettled-preview'
    publisher.stop('site-one', session_id='session-one', request_id='stop-preview', expected_revision=3)
    # Stop preserves a site's resume reference even when previously removed.
    assert candidate(publisher, release)['hold'] == 'current-or-resumable'
    publisher.remove('site-one', session_id='session-one', request_id='remove-again', expected_revision=4)
    clock[0] += timedelta(days=31)
    assert retire(publisher, release)['state'] == 'succeeded'


def test_unpin_and_evicted_rollback_keep_full_promised_grace(clock, publisher, source):
    releases = []
    for number in range(4):
        (source/'index.html').write_text(str(number))
        release = capture(publisher, source, f'capture-{number}', keepDays=1)
        review(publisher, release, f'review-{number}'); deploy(publisher, release, number, f'deploy-{number}')
        releases.append(release)
        clock[0] += timedelta(days=1)
    first = releases[0]
    assert first['id'] not in publisher.status('site-one','session-one')['rollbackCandidates']
    assert candidate(publisher, first, policy={'keepDays':1})['hold'] == 'grace'
    state = publisher.retention(first['id'], 'session-one')
    publisher.pin(first['id'], session_id='session-one', request_id='pin', expected_revision=state['revision'], pinned=True)
    clock[0] += timedelta(days=40)
    assert candidate(publisher, first)['hold'] == 'pinned'
    state = publisher.retention(first['id'], 'session-one')
    publisher.pin(first['id'], session_id='session-one', request_id='unpin', expected_revision=state['revision'], pinned=False)
    assert candidate(publisher, first)['hold'] == 'grace'
    clock[0] += timedelta(days=31)
    assert retire(publisher, first)['state'] == 'succeeded'


def test_earlier_prospective_record_gets_fresh_grace_on_adoption(clock, source, tmp_path):
    root = tmp_path/'store'
    with Publisher(root) as owner:
        release = capture(owner, source)
        state = owner._get('retention', release['id']); state.pop('unusedSince'); owner._put('retention', state)
    clock[0] += timedelta(days=90)
    with Publisher(root) as owner:
        assert owner.retention(release['id'], 'session-one')['unusedSince'] == clock[0].isoformat()
        assert candidate(owner, release)['hold'] == 'grace'


def test_failed_preview_first_observed_by_resource_page_starts_fresh_grace(clock, publisher, source):
    release = capture(publisher, source)
    publisher.preview(release['id'], session_id='session-one', request_id='preview')
    clock[0] += timedelta(days=100)
    publisher._previews[release['id']].stop()
    resource = publisher.resources('session-one')['items'][0]
    assert resource['status'] == 'interrupted'
    assert candidate(publisher, release)['hold'] == 'grace'
    assert publisher.retention(release['id'], 'session-one')['unusedSince'] == clock[0].isoformat()


def test_explicit_remove_settles_site_fence_without_rewriting_old_unknown_receipt(clock, publisher, source, monkeypatch):
    release = capture(publisher, source); review(publisher, release)
    save = publisher._save_receipt
    with monkeypatch.context() as patch:
        def fail_completion(receipt, fingerprint):
            if receipt['action'] == 'deploy' and receipt['state'] == 'succeeded':
                raise OSError('Completion interrupted')
            return save(receipt, fingerprint)
        patch.setattr(publisher, '_save_receipt', fail_completion)
        with pytest.raises(PublishingError) as unknown:
            deploy(publisher, release)
    assert unknown.value.receipt['state'] == 'unknown'
    assert candidate(publisher, release)['hold'] == 'unresolved-site'
    revision = publisher.status('site-one', 'session-one')['revision']
    publisher.remove('site-one', session_id='session-one', request_id='settled-remove', expected_revision=revision)
    clock[0] += timedelta(days=31)
    assert retire(publisher, release)['state'] == 'succeeded'
    assert publisher.receipt('session-one', 'deploy-one') == unknown.value.receipt


def test_pin_during_preflight_wins_final_admission(clock, publisher, source, monkeypatch):
    release = capture(publisher, source)
    clock[0] += timedelta(days=31)
    original = retirement._open_directory
    def pin_then_open(path):
        if path == publisher._content:
            publisher.pin(release['id'], session_id='session-one', request_id='pin', expected_revision=0, pinned=True)
        return original(path)
    monkeypatch.setattr(retirement, '_open_directory', pin_then_open)
    with pytest.raises(PublishingError) as error:
        retire(publisher, release)
    assert error.value.code == 'stale_revision' and error.value.receipt['state'] == 'failed'
    assert (publisher._content/release['id']).is_dir()


@pytest.mark.parametrize('point', ['before-rename', 'after-rename', 'partial-unlink', 'before-expired', 'before-receipt'])
def test_restart_reconciles_exact_intent_without_replaying_request(clock, source, tmp_path, monkeypatch, point):
    root = tmp_path/'store'
    owner = Publisher(root)
    release = capture(owner, source)
    clock[0] += timedelta(days=31)
    with monkeypatch.context() as patch:
        if point == 'before-rename':
            patch.setattr(retirement.os, 'rename', lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        elif point == 'after-rename':
            save = retirement._save_state
            def save_state(owner, state):
                if state.get('retirement', {}).get('phase') == 'detached':
                    raise KeyboardInterrupt()
                return save(owner, state)
            patch.setattr(retirement, '_save_state', save_state)
        elif point == 'partial-unlink':
            unlink = retirement.os.unlink
            def unlink_then_crash(*args, **kwargs):
                unlink(*args, **kwargs)
                raise KeyboardInterrupt()
            patch.setattr(retirement.os, 'unlink', unlink_then_crash)
        elif point == 'before-expired':
            patch.setattr(retirement, '_finish', lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        else:
            save = owner._save_receipt
            def save_receipt(receipt, fingerprint):
                if receipt['action'] == 'retire' and receipt['state'] == 'succeeded':
                    raise KeyboardInterrupt()
                return save(receipt, fingerprint)
            patch.setattr(owner, '_save_receipt', save_receipt)
        with pytest.raises(KeyboardInterrupt):
            retire(owner, release)
    owner.close()
    with Publisher(root) as reopened:
        current = reopened.retention(release['id'], 'session-one')
        expected = 'available' if point == 'before-rename' else 'expired'
        assert current['availability'] == expected
        saved = reopened.receipt('session-one', 'retire')
        assert saved['state'] == 'unknown'
        with pytest.raises(PublishingError) as error:
            reopened.retire(release['id'], session_id='session-one', request_id='retire', expected_revision=0, policy={})
        assert error.value.receipt == saved
        assert not list((root/'retiring').iterdir())
        if point == 'before-rename':
            assert current['retirementPhase'] == 'admission-required'
            with pytest.raises(PublishingError):
                retire(reopened, release, 'disabled', policy=None)
            assert (reopened._content/release['id']).exists()
            assert retire(reopened, release, 'new-admission')['state'] == 'succeeded'
        assert reopened.receipt('session-one', 'retire') == saved


def test_interrupted_recovery_after_empty_container_removal_is_idempotent(clock, source, tmp_path, monkeypatch):
    owner = Publisher(tmp_path/'store'); release = capture(owner, source); clock[0] += timedelta(days=31)
    with monkeypatch.context() as patch:
        patch.setattr(retirement.os, 'rename', lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            retire(owner, release)
    with monkeypatch.context() as patch:
        patch.setattr(retirement, '_finish', lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            owner.reconcile_retirements()
    owner.close()
    with Publisher(tmp_path/'store') as reopened:
        assert reopened.retention(release['id'], 'session-one')['availability'] == 'available'
        assert (reopened._content/release['id']).exists()


def test_stop_before_and_after_detach_preserves_receipts_and_resumes_cleanup(clock, publisher, source, monkeypatch):
    release = capture(publisher, source); clock[0] += timedelta(days=31)
    stop = threading.Event(); stop.set()
    with pytest.raises(PublishingError) as before:
        retire(publisher, release, 'stopped-before', stop_event=stop)
    assert before.value.code == 'cancelled' and before.value.receipt['state'] == 'failed'
    stop.clear()
    unlink = retirement._unlink_tree
    with monkeypatch.context() as patch:
        def stop_unlink(fd, event):
            stop.set(); return unlink(fd, event)
        patch.setattr(retirement, '_unlink_tree', stop_unlink)
        with pytest.raises(PublishingError) as after:
            retire(publisher, release, stop_event=stop)
    assert after.value.code == 'cancelled' and after.value.receipt['state'] == 'unknown'
    assert publisher.retention(release['id'], 'session-one')['availability'] == 'unknown'
    stop.clear()
    assert publisher.reconcile_retirements(stop_event=stop)['items'][0]['phase'] == 'complete'
    assert publisher.receipt('session-one', 'retire') == after.value.receipt


def test_replaced_root_holds_but_owned_corruption_and_child_links_are_disposable(clock, publisher, source, tmp_path, monkeypatch):
    release = capture(publisher, source); clock[0] += timedelta(days=31)
    outside = tmp_path/'outside'; outside.write_text('keep')
    body = publisher._content/release['id']
    (body/'files/index.html').chmod(0o600); (body/'files/index.html').write_text('corrupted')
    (body/'extra').write_text('also owned'); (body/'external-link').symlink_to(outside)
    with monkeypatch.context() as patch:
        patch.setattr(retirement, '_unlink_tree', lambda *a: (_ for _ in ()).throw(OSError('interrupted')))
        with pytest.raises(PublishingError):
            retire(publisher, release)
    intent = publisher._get('retention', release['id'])['retirement']
    detached = publisher.root/'retiring'/intent['directory']/'body'
    saved = tmp_path/'saved-owned-body'; detached.rename(saved)
    detached.mkdir(); (detached/'foreign').write_text('keep replacement')
    assert publisher.reconcile_retirements()['items'][0]['phase'] == 'held'
    assert (detached/'foreign').read_text() == 'keep replacement'
    (detached/'foreign').unlink(); detached.rmdir(); saved.rename(detached)
    assert publisher.reconcile_retirements()['items'][0]['phase'] == 'complete'
    assert outside.read_text() == 'keep' and (source/'index.html').is_file()


@pytest.mark.parametrize('location', ['body', 'content', 'retiring'])
def test_symlinked_storage_roots_never_delete_the_target(clock, publisher, source, tmp_path, location):
    release = capture(publisher, source); clock[0] += timedelta(days=31)
    if location == 'body':
        path = publisher._content/release['id']
    elif location == 'content':
        path = publisher._content
    else:
        path = publisher.root/'retiring'; path.mkdir()
    saved = tmp_path/'original'; path.rename(saved)
    path.symlink_to(saved, target_is_directory=True)
    try:
        with pytest.raises(PublishingError):
            retire(publisher, release)
        assert saved.is_dir()
        assert (source/'index.html').read_text() == '<h1>Version one</h1>'
        assert publisher.retention(release['id'], 'session-one')['availability'] == 'available'
    finally:
        path.unlink(); saved.rename(path)


def test_active_http_body_read_protects_superseded_generation(clock, publisher, source, monkeypatch):
    first = capture(publisher, source); review(publisher, first); url = deploy(publisher, first)['result']['url']
    reading = threading.Event(); proceed = threading.Event()
    read = publishing._read_file
    def blocked(root, path, size):
        if first['id'] in str(root):
            reading.set(); assert proceed.wait(5)
        return read(root, path, size)
    with ThreadPoolExecutor() as pool, monkeypatch.context() as patch:
        patch.setattr(publishing, '_read_file', blocked)
        request = pool.submit(http, url)
        try:
            assert reading.wait(3)
            (source/'index.html').write_text('next')
            second = capture(publisher, source, 'next'); review(publisher, second, 'review-next'); deploy(publisher, second, 1, 'deploy-next')
            clock[0] += timedelta(days=31)
            assert candidate(publisher, first)['hold'] == 'active-reader'
        finally:
            proceed.set()
        assert request.result(3)[2] == b'<h1>Version one</h1>'
    assert candidate(publisher, first)['hold'] == 'grace'
    clock[0] += timedelta(days=31)
    assert retire(publisher, first)['state'] == 'succeeded'
    assert http(url)[2] == b'next'


def test_late_http_handler_cannot_enter_retired_body(clock, publisher, source, monkeypatch):
    release = capture(publisher, source)
    preview = publisher.preview(release['id'], session_id='session-one', request_id='preview')
    waiting = threading.Event(); proceed = threading.Event(); denied = threading.Event()
    body_reader = publisher._body_reader
    @contextmanager
    def late(server):
        waiting.set(); assert proceed.wait(5)
        try:
            with body_reader(server) as value:
                yield value
        except PublishingError as error:
            assert error.code == 'release_unavailable'
            denied.set(); raise
    with ThreadPoolExecutor() as pool, monkeypatch.context() as patch:
        patch.setattr(publisher, '_body_reader', late)
        request = pool.submit(http, preview['result']['url'])
        try:
            assert waiting.wait(3)
            publisher.stop('site-one', session_id='session-one', request_id='stop', expected_revision=0)
            clock[0] += timedelta(days=31)
            assert retire(publisher, release)['state'] == 'succeeded'
            patch.setattr(publishing, '_read_file', lambda *a: pytest.fail('Late handler read retired bytes'))
        finally:
            proceed.set()
        assert denied.wait(3)
        # Stop deliberately disconnects the actual HTTP socket.
        try:
            request.result(3)
        except OSError:
            pass


def test_slow_unlink_does_not_hold_owner_and_close_waits_for_worker(clock, publisher, source, tmp_path, monkeypatch):
    first = capture(publisher, source)
    (source/'index.html').write_text('live')
    live = capture(publisher, source, 'live'); review(publisher, live); url = deploy(publisher, live)['result']['url']
    clock[0] += timedelta(days=31)
    removing = threading.Event(); proceed = threading.Event()
    unlink = retirement._unlink_tree
    def slow(fd, event):
        removing.set(); assert proceed.wait(5)
        return unlink(fd, event)
    with ThreadPoolExecutor() as pool, monkeypatch.context() as patch:
        patch.setattr(retirement, '_unlink_tree', slow)
        effect = pool.submit(retire, publisher, first)
        try:
            assert removing.wait(3)
            assert http(url)[2] == b'live'
            state = publisher.retention(first['id'], 'session-one')
            assert state['availability'] == 'retiring' and 'retirement' not in state
            with pytest.raises(PublishingError) as backup:
                publisher.snapshot(tmp_path/'unsafe-backup')
            assert backup.value.code == 'release_unavailable'
            closed = pool.submit(publisher.close)
            assert not closed.done()
        finally:
            proceed.set()
        assert effect.result(3)['state'] == 'succeeded'
        closed.result(3)
    assert publisher._closed and not publisher._retirement_workers


def test_live_http_remains_independent_of_an_unrelated_slow_capture(clock, publisher, source, monkeypatch):
    release = capture(publisher, source)
    url = publisher.preview(release['id'], session_id='session-one', request_id='preview')['result']['url']
    (source/'index.html').write_text('next build')
    snapshot = publisher._snapshot
    building = threading.Event(); proceed = threading.Event()
    def blocked(source):
        building.set(); assert proceed.wait(5)
        return snapshot(source)
    with ThreadPoolExecutor() as pool, monkeypatch.context() as patch:
        patch.setattr(publisher, '_snapshot', blocked)
        build = pool.submit(capture, publisher, source, 'new-build')
        try:
            assert building.wait(3)
            assert http(url)[2] == b'<h1>Version one</h1>'
            assert not build.done()
        finally:
            proceed.set()
        assert build.result(3)['id'] != release['id']


def test_last_reader_completion_racing_final_admission_gets_fresh_grace(clock, publisher, source, monkeypatch):
    first = capture(publisher, source); review(publisher, first); deploy(publisher, first)
    lease = publisher._body_reader(publisher._sites['site-one']); lease.__enter__()
    (source/'index.html').write_text('next')
    second = capture(publisher, source, 'next'); review(publisher, second, 'review-next'); deploy(publisher, second, 1, 'deploy-next')
    clock[0] += timedelta(days=31)
    eligible = retirement._eligibility
    calls = [0]
    def complete_between_drain_and_check(*args):
        calls[0] += 1
        if calls[0] == 1:
            lease.__exit__(None,None,None)
        return eligible(*args)
    with monkeypatch.context() as patch:
        patch.setattr(retirement, '_eligibility', complete_between_drain_and_check)
        with pytest.raises(PublishingError) as error:
            retire(publisher, first)
    assert error.value.code == 'stale_revision'
    assert candidate(publisher, first)['hold'] == 'grace'
    assert (publisher._content/first['id']).exists()


@pytest.mark.parametrize('admitted', [False, True])
def test_close_settles_admitted_reader_and_rejects_handler_waiting_for_gate(clock, publisher, source, monkeypatch, admitted):
    release = capture(publisher, source)
    preview = publisher.preview(release['id'], session_id='session-one', request_id='preview')
    waiting = threading.Event(); proceed = threading.Event(); finished = threading.Event()
    original = publisher._body_reader
    @contextmanager
    def reader(server):
        try:
            if not admitted:
                waiting.set()
            with original(server) as body:
                if admitted:
                    waiting.set(); assert proceed.wait(5)
                yield body
        except PublishingError as error:
            assert not admitted and error.code == 'closed'
            raise
        finally:
            finished.set()
    with ThreadPoolExecutor() as pool, monkeypatch.context() as patch:
        patch.setattr(publisher, '_body_reader', reader)
        if admitted:
            request = pool.submit(http, preview['result']['url'])
            try:
                assert waiting.wait(3)
                closed = pool.submit(publisher.close)
                assert not closed.done()
            finally:
                proceed.set()
            closed.result(3)
        else:
            with publisher._readers_idle:
                request = pool.submit(http, preview['result']['url'])
                assert waiting.wait(3)
                publisher.close()
        assert finished.wait(3)
        try:
            request.result(3)
        except OSError:
            pass
    assert publisher._closed and not publisher._body_reads


def test_unresolved_effect_is_held_without_scanning_or_replaying(clock, publisher, source):
    release = capture(publisher, source); clock[0] += timedelta(days=31)
    uncertain = {'sessionId':'session-one', 'siteId':'site-one', 'requestId':'uncertain', 'action':'build', 'state':'unknown'}
    publisher._save_receipt(uncertain, 'fingerprint')
    assert candidate(publisher, release)['hold'] == 'unresolved-effect'
    with pytest.raises(PublishingError) as error:
        retire(publisher, release)
    assert error.value.receipt['state'] == 'failed'
    assert publisher.receipt('session-one','uncertain') == uncertain
    assert (publisher._content/release['id']).is_dir()


def test_candidate_pages_and_retirement_do_not_hydrate_history_or_manifest(clock, publisher, source, monkeypatch):
    releases = []
    for n in range(3):
        (source/'index.html').write_text(str(n)); releases.append(capture(publisher, source, str(n)))
    clock[0] += timedelta(days=31)
    def measured_page():
        instructions = [0]
        def progress():
            instructions[0] += 1
            return 0
        publisher._db.set_progress_handler(progress, 1)
        try:
            page = publisher.retirement_candidates('session-one', policy={}, limit=2)
            publisher.reconcile_retirements()
            return page, instructions[0]
        finally:
            publisher._db.set_progress_handler(None, 0)
    before, baseline = measured_page()
    publisher._db.execute('BEGIN IMMEDIATE')
    for n in range(2000):
        publisher._db.execute('INSERT INTO receipts VALUES(?,?,?,?)', ('session-one',f'historical-{n}','fingerprint',json.dumps(
            {'state':'unknown','action':'deploy','siteId':'site-one'})))
        publisher._put('retention', {'id':f'000-durable-{n}', 'sessionId':'session-one', 'siteId':'site-one', 'policy':None, 'availability':'available'})
    publisher._db.execute('COMMIT')
    after, grown = measured_page()
    assert after == before and grown <= baseline + 10
    with monkeypatch.context() as patch:
        for name in ('_all', '_release', '_verify'):
            patch.setattr(publisher, name, lambda *a, **k: pytest.fail('History or manifest hydration'))
        page = publisher.retirement_candidates('session-one', policy={}, limit=2)
        assert len(page['items']) == page['inspected'] == 2 and page['nextAfter']
        last = publisher.retirement_candidates('session-one', policy={}, limit=2, after=page['nextAfter'])
        assert len(last['items']) == 1 and last['nextAfter'] is None
        assert retire(publisher, releases[0])['state'] == 'succeeded'


def test_matching_reader_adopts_expired_backup_and_rejects_malformed_retire_receipt(clock, source):
    from amplifier_publishing.remote import SSHClient, MUTATIONS
    from amplifier_publishing.service import PublishingService
    from test_publishing_remote import unix_request
    with tempfile.TemporaryDirectory(prefix='retired-', dir='/tmp') as temporary:
        root = Path(temporary)
        with Publisher(root/'original') as owner:
            release = capture(owner, source); clock[0] += timedelta(days=31)
            receipt = retire(owner, release); saved = owner.receipts('session-one')
            owner.snapshot(root/'backup')
        socket = root/'admin'/'admin.sock'
        with PublishingService(root/'backup', socket):
            client = SSHClient(hostname='localhost', python='/usr/bin/python3', socket_path=str(socket), expected_bind='127.0.0.1')
            received = unix_request(socket, {'method':'receipts', 'sessionId':'session-one'})
            assert client.validate_result('receipts', received) == saved
            assert 'retire' not in MUTATIONS
            with pytest.raises(PublishingError) as error:
                unix_request(socket, {'method':'retire', 'sessionId':'session-one', 'releaseId':release['id']})
            assert error.value.code == 'invalid_argument'
            for field, value in [('availability','available'), ('retirementPhase',[]), ('retirementPhase',{}), ('retirementRequestId',1), ('id','wrong')]:
                malformed = copy.deepcopy(receipt); malformed['result'][field] = value
                with pytest.raises(PublishingError) as error:
                    client.validate_result('receipt', malformed)
                assert error.value.code == 'invalid_response'
