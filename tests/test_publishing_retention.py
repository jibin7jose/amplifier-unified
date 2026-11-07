"""Reader/pin/admission contracts before publishing body deletion is implemented."""
import copy
from datetime import datetime, timedelta, timezone
import shutil

import pytest

from amplifier_publishing import Publisher, PublishingError
from test_publishing_library import publisher, source, review, deploy, http


def capture(owner, source, request='capture', **policy):
    return owner.build(source, site_id='site-one', session_id='session-one', request_id=request, retention=policy)


def current(owner, release):
    return next(row for row in owner.releases(release['sessionId']) if row['id'] == release['id'])


def expired_fixture(owner, release):
    # Construct the future effect owner's completed result. This batch implements
    # readers and deliberate recapture, NOT a deletion operation or a scheduler.
    shutil.rmtree(owner._content / release['id'])
    state = owner._get('retention', release['id'])
    state.update(availability='expired', expiredAt='2026-01-01T00:00:00+00:00', revision=state['revision']+1)
    owner._put('retention', state)


def test_policy_is_prospective_and_exact_old_requests_keep_their_fingerprint(publisher, source):
    legacy = publisher.build(source, site_id='site-one', session_id='session-one', request_id='legacy')
    assert 'retention' not in legacy
    duplicate_content = capture(publisher, source)
    assert duplicate_content == legacy and publisher._get('retention', legacy['id']) is None
    assert publisher.build(source, site_id='site-one', session_id='session-one', request_id='legacy') == legacy
    with pytest.raises(PublishingError, match='different action or payload'):
        publisher.build(source, site_id='site-one', session_id='session-one', request_id='legacy', retention={})
    (source/'index.html').write_text('New explicitly temporary bytes')
    fresh = capture(publisher, source, 'new')
    assert fresh['retention']['policy'] == {'keepDays':30, 'rollbackKeepDays':30, 'rollbackKeepCount':2}
    assert fresh['retention']['payloadGeneration'] == 1 and fresh['retention']['availability'] == 'available'
    assert publisher._get('retention', legacy['id']) is None


@pytest.mark.parametrize('settings', [{'keepDays':0}, {'keepDays':True}, {'rollbackKeepCount':-1}, {'unknown':1}, '30'])
def test_invalid_policy_has_a_durable_exact_failure(publisher, source, settings):
    for _ in range(2):
        with pytest.raises(PublishingError) as error:
            publisher.build(source, site_id='site-one', session_id='session-one', request_id='invalid', retention=settings)
        assert error.value.code == 'invalid_argument' and error.value.receipt['state'] == 'failed'
    assert publisher.releases('session-one') == []
    assert len(publisher.receipts('session-one')) == 1


def test_pin_is_one_revisioned_owner_action_with_historical_exact_retry(publisher, source):
    release = capture(publisher, source)
    args = {'session_id':'session-one', 'request_id':'pin', 'expected_revision':0, 'pinned':True}
    pinned = publisher.pin(release['id'], **args)
    assert pinned['result']['retention']['pinned'] is True
    assert publisher.pin(release['id'], **args) == pinned
    with pytest.raises(PublishingError) as error:
        publisher.pin(release['id'], **{**args, 'request_id':'stale'})
    assert error.value.code == 'stale_revision'
    with pytest.raises(PublishingError) as error:
        publisher.pin(release['id'], **{**args, 'request_id':'other', 'session_id':'other-session'})
    assert error.value.code == 'not_found'
    publisher.pin(release['id'], **{**args, 'request_id':'unpin', 'expected_revision':1, 'pinned':False})
    assert current(publisher, release)['retention']['pinned'] is False
    assert publisher.pin(release['id'], **args) == pinned
    assert capture(publisher, source) == release
    assert publisher._get('release', release['id'])['createdAt'] == release['createdAt']


def test_pin_does_not_opt_durable_legacy_release_into_expiry(publisher, source):
    release = publisher.build(source, site_id='site-one', session_id='session-one', request_id='legacy')
    pinned = publisher.pin(release['id'], session_id='session-one', request_id='pin', expected_revision=0, pinned=True)
    assert pinned['result']['retention']['policy'] is None
    assert capture(publisher, source, 'new-explicit-temporary')['retention']['policy'] is None


def test_expired_readers_and_old_retries_never_materialize_content(publisher, source):
    release = capture(publisher, source)
    reviewed = review(publisher, release)
    expired_fixture(publisher, release)
    saved = copy.deepcopy(publisher.receipts('session-one'))
    assert current(publisher, release)['retention']['availability'] == 'expired'
    assert capture(publisher, source) == release
    assert review(publisher, release) == reviewed
    assert publisher.receipts('session-one') == saved
    for operation in [lambda: publisher.export_release(release['id'], 'session-one'),
                      lambda: publisher.preview(release['id'], session_id='session-one', request_id='new-preview'),
                      lambda: publisher.review(release['id'], session_id='session-one', request_id='new-review'),
                      lambda: publisher.pin(release['id'], session_id='session-one', request_id='new-pin', expected_revision=1, pinned=True)]:
        with pytest.raises(PublishingError) as error:
            operation()
        assert error.value.code == 'release_expired'
    assert not (publisher._content / release['id']).exists()
    assert (source/'index.html').read_text() == '<h1>Version one</h1>'


def test_new_deliberate_capture_restores_same_identity_as_new_body_generation(publisher, source, monkeypatch):
    import amplifier_publishing.publisher as module
    release = capture(publisher, source, keepDays=9)
    original_receipt = copy.deepcopy(publisher.receipts('session-one')[0])
    reviewed = review(publisher, release)
    expired_fixture(publisher, release)
    monkeypatch.setattr(module, '_now', lambda:'2026-11-08T00:00:00+00:00')
    restored = capture(publisher, source, 'deliberate-new-build', keepDays=40, policyRevision=3)
    for name in ('id', 'manifestDigest', 'createdAt', 'files', 'totalBytes'):
        assert restored[name] == release[name]
    assert restored['retention']['payloadGeneration'] == 2
    assert restored['retention']['materializedAt'] == '2026-11-08T00:00:00+00:00'
    assert restored['retention']['policy']['keepDays'] == 40
    assert restored['retention']['policy']['policyRevision'] == 3
    assert restored['retention']['availability'] == 'available' and 'expiredAt' not in restored['retention']
    assert publisher.receipts('session-one')[0] == original_receipt
    assert capture(publisher, source, keepDays=9) == release
    assert publisher.export_release(release['id'], 'session-one')['manifestDigest'] == release['manifestDigest']
    assert review(publisher, release) == reviewed
    assert restored['review'] == reviewed['result']['review']
    assert http(deploy(publisher, restored)['result']['url'])[2] == b'<h1>Version one</h1>'


def test_unknown_restoration_and_unexpected_content_never_get_overwritten(publisher, source, monkeypatch):
    release = capture(publisher, source)
    expired_fixture(publisher, release)
    destination = publisher._content / release['id']
    destination.mkdir();(destination/'unrecognized').write_text('keep')
    with pytest.raises(PublishingError) as error:
        capture(publisher, source, 'unsettled')
    assert error.value.code == 'release_unavailable' and (destination/'unrecognized').read_text() == 'keep'
    (destination/'unrecognized').unlink();destination.rmdir()
    original = publisher._put
    def fail(kind, record):
        if kind == 'release':
            raise OSError('Commit interrupted after materialization')
        return original(kind, record)
    monkeypatch.setattr(publisher, '_put', fail)
    with pytest.raises(PublishingError) as error:
        capture(publisher, source, 'interrupted-restore')
    assert error.value.code == 'unknown_outcome'
    assert current(publisher, release)['retention']['availability'] == 'unknown'
    assert error.value.receipt['releaseId'] == release['id']
    monkeypatch.setattr(publisher, '_put', original)
    with pytest.raises(PublishingError) as error:
        capture(publisher, source, 'cannot-repeat')
    assert error.value.code == 'release_unavailable'
    assert (destination/'files/index.html').is_file()


def test_backup_carries_expired_metadata_without_fabricating_missing_content(publisher, source, tmp_path):
    expired = capture(publisher, source)
    expired_fixture(publisher, expired)
    (source/'index.html').write_text('Still available')
    available = capture(publisher, source, 'available')
    snapshot = tmp_path/'backup'
    report = publisher.snapshot(snapshot)
    assert report['releaseCount'] == 2 and report['availableReleaseCount'] == report['expiredReleaseCount'] == 1
    with Publisher(snapshot) as restored:
        assert current(restored, expired)['retention']['availability'] == 'expired'
        assert not (restored._content/expired['id']).exists()
        assert restored.receipts('session-one') == publisher.receipts('session-one')
        assert restored.export_release(available['id'], 'session-one')['manifestDigest'] == available['manifestDigest']
        assert restored._sites == restored._previews == {}
    (publisher._content/available['id']/'files/index.html').unlink()
    with pytest.raises(PublishingError) as error:
        publisher.snapshot(tmp_path/'missing-is-not-expired')
    assert error.value.code == 'integrity_error'


def test_automatic_rollback_is_ordered_finite_and_pin_uses_same_release_owner(publisher, source, monkeypatch):
    import amplifier_publishing.publisher as module
    clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(module, '_now', lambda:clock.isoformat())
    releases=[]
    for number in range(4):
        (source/'index.html').write_text(str(number))
        release = capture(publisher, source, f'capture-{number}')
        review(publisher, release, f'review-{number}')
        deploy(publisher, release, number, f'deploy-{number}')
        releases.append(release)
        clock += timedelta(days=1)
    before=publisher.status('site-one','session-one')
    assert before['previousReleaseId'] == releases[2]['id']
    assert before['rollbackCandidates'] == [releases[2]['id'],releases[1]['id']]
    assert before['previousReleaseAvailable'] is True
    state=current(publisher,releases[1])['retention']
    publisher.pin(releases[1]['id'],session_id='session-one',request_id='pin-old',expected_revision=state['revision'],pinned=True)
    clock += timedelta(days=31)
    after=publisher.status('site-one','session-one')
    assert after['previousReleaseId'] == before['previousReleaseId']
    assert after['rollbackCandidates'] == [releases[1]['id']] and after['previousReleaseAvailable'] is False
    assert http(after['url'])[2] == b'3'
    assert all((publisher._content/r['id']).exists() for r in releases)  # Projection never deletes.
    expired_fixture(publisher,releases[2])
    assert publisher.status('site-one','session-one')['previousReleaseAvailable'] is False
    publisher.remove('site-one',session_id='session-one',request_id='remove',expected_revision=4)
    assert publisher.status('site-one','session-one')['previousReleaseId'] == releases[2]['id']
    assert (publisher._content/releases[1]['id']).exists()


def test_single_retention_read_does_not_walk_receipts_listeners_or_content(publisher, source, monkeypatch):
    release = capture(publisher, source)
    for number in range(1000):
        publisher._db.execute("INSERT INTO receipts VALUES(?,?,?,?)", ('unrelated',str(number),'fingerprint','{}'))
    changes = publisher._db.total_changes
    monkeypatch.setattr(publisher, '_all', lambda *_: pytest.fail('Historical collection read'))
    monkeypatch.setattr(publisher, '_verify', lambda *_: pytest.fail('Body read'))
    result = publisher.retention(release['id'], 'session-one')
    assert result['availability'] == 'available' and result['payloadGeneration'] == 1
    assert publisher._db.total_changes == changes
    with pytest.raises(PublishingError) as error:
        publisher.retention(release['id'], 'another-session')
    assert error.value.code == 'not_found'
    monkeypatch.undo()  # Fixture close still owns listener shutdown.


def test_explicit_previous_pin_is_preserved_when_automatic_count_is_zero(publisher, source):
    first = capture(publisher, source)
    review(publisher, first)
    deploy(publisher, first)
    publisher.pin(first['id'],session_id='session-one',request_id='pin',expected_revision=0,pinned=True)
    (source/'index.html').write_text('Next release')
    second = capture(publisher, source, 'second', rollbackKeepCount=0)
    review(publisher, second, 'review-second')
    deploy(publisher, second, 1, 'deploy-second')
    site = publisher.status('site-one','session-one')
    assert site['rollbackHistory'] == []
    assert site['previousReleaseAvailable'] is True and site['rollbackCandidates'] == [first['id']]


def test_restart_after_recapture_install_keeps_exact_unknown_identity(source, tmp_path, monkeypatch):
    owner = Publisher(tmp_path/'store')
    release = capture(owner, source)
    expired_fixture(owner, release)
    original = owner._put
    def crash(kind, record):
        if kind == 'release':
            raise KeyboardInterrupt('Process ended after installing the new body')
        return original(kind, record)
    monkeypatch.setattr(owner, '_put', crash)
    with pytest.raises(KeyboardInterrupt):
        capture(owner, source, 'crashed-recapture')
    owner._db.rollback()  # SQLite recovery discards only the uncommitted transaction.
    owner.close()
    with Publisher(tmp_path/'store') as restored:
        receipt = next(row for row in restored.receipts('session-one') if row['requestId']=='crashed-recapture')
        assert receipt['releaseId'] == release['id'] and receipt['state'] == 'unknown'
        assert restored.retention(release['id'],'session-one')['availability'] == 'unknown'
        with pytest.raises(PublishingError) as error:
            capture(restored, source, 'crashed-recapture')
        assert error.value.code == 'unknown_outcome'
        assert (restored._content/release['id']/'files/index.html').read_text() == '<h1>Version one</h1>'


def test_explicit_pin_outlives_automatic_history_without_offering_undeployed_bodies(publisher, source):
    releases = []
    for number in range(5):
        (source/'index.html').write_text(str(number))
        release = capture(publisher, source, f'capture-{number}')
        review(publisher, release, f'review-{number}')
        deploy(publisher, release, number, f'deploy-{number}')
        releases.append(release)
        if number == 0:
            publisher.pin(release['id'], session_id='session-one', request_id='pin-first', expected_revision=0, pinned=True)
    (source/'index.html').write_text('Never deployed')
    undeployed = capture(publisher, source, 'undeployed')
    publisher.pin(undeployed['id'], session_id='session-one', request_id='pin-undeployed', expected_revision=0, pinned=True)
    publisher.pin(releases[-1]['id'], session_id='session-one', request_id='pin-current', expected_revision=0, pinned=True)
    other = publisher.build(source, site_id='another-site', session_id='session-one', request_id='other-site', retention={})
    publisher.pin(other['id'], session_id='session-one', request_id='pin-other', expected_revision=0, pinned=True)
    site = publisher.status('site-one', 'session-one')
    assert site['rollbackCandidates'] == [releases[3]['id'], releases[2]['id'], releases[0]['id']]
    assert releases[0]['id'] not in [row['releaseId'] for row in site['rollbackHistory']]
    state = publisher.retention(releases[0]['id'], 'session-one')
    publisher.pin(releases[0]['id'], session_id='session-one', request_id='unpin-first', expected_revision=state['revision'], pinned=False)
    assert publisher.status('site-one', 'session-one')['rollbackCandidates'] == [releases[3]['id'], releases[2]['id']]


def test_failed_begin_after_intent_commit_preserves_unknown_request_without_body(publisher, source, monkeypatch):
    release = capture(publisher, source)
    expired_fixture(publisher, release)
    database = publisher._db
    class FailSecondBegin:
        begins = 0
        def __getattr__(self, name):
            return getattr(database, name)
        def execute(self, sql, *args):
            if sql == 'BEGIN IMMEDIATE':
                self.begins += 1
                if self.begins == 2:
                    raise OSError('Cannot begin the install transaction')
            return database.execute(sql, *args)
    monkeypatch.setattr(publisher, '_db', FailSecondBegin())
    with pytest.raises(PublishingError) as error:
        capture(publisher, source, 'failed-install-begin')
    assert error.value.code == 'unknown_outcome'
    assert error.value.receipt['releaseId'] == release['id']
    assert publisher.retention(release['id'], 'session-one')['availability'] == 'expired'
    assert not (publisher._content/release['id']).exists()
    with pytest.raises(PublishingError) as retry:
        capture(publisher, source, 'failed-install-begin')
    assert retry.value.receipt == error.value.receipt
    assert capture(publisher, source, 'new-deliberate-build')['retention']['payloadGeneration'] == 2


def test_matching_remote_reader_adopts_backup_with_pin_receipts_without_new_rpc(source):
    from amplifier_publishing.remote import SSHClient, MUTATIONS
    from amplifier_publishing.service import PublishingService
    from test_publishing_remote import unix_request
    # Use the existing short Unix-socket fixture's path convention.
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix='retention-', dir='/tmp') as temporary:
        root = Path(temporary)
        with Publisher(root/'original') as owner:
            release = capture(owner, source)
            pin = owner.pin(release['id'], session_id='session-one', request_id='pin', expected_revision=0, pinned=True)
            saved = owner.receipts('session-one')
            owner.snapshot(root/'backup')
        socket = root/'admin'/'admin.sock'
        with PublishingService(root/'backup', socket) as service:
            client = SSHClient(hostname='localhost', python='/usr/bin/python3', socket_path=str(socket), expected_bind='127.0.0.1')
            received = unix_request(socket, {'method':'receipts', 'sessionId':'session-one'})
            assert client.validate_result('receipts', received) == saved
            assert next(row for row in received if row['action']=='pin') == pin
            assert 'pin' not in MUTATIONS
            with pytest.raises(PublishingError) as error:
                unix_request(socket, {'method':'pin', 'sessionId':'session-one', 'requestId':'remote-pin', 'releaseId':release['id'], 'expectedRevision':1, 'pinned':False})
            assert error.value.code == 'invalid_argument'
            assert service.publisher.receipts('session-one') == saved
            for field, value in [('pinned',1), ('revision',True), ('payloadGeneration',0), ('availability','deleted'), ('materializedAt','today'), ('policy',{'keepDays':0})]:
                malformed = copy.deepcopy(pin)
                malformed['result']['retention'][field] = value
                with pytest.raises(PublishingError) as error:
                    client.validate_result('receipt', malformed)
                assert error.value.code == 'invalid_response'
            malformed = copy.deepcopy(pin)
            malformed['result'].pop('retention')
            with pytest.raises(PublishingError) as error:
                client.validate_result('receipt', malformed)
            assert error.value.code == 'invalid_response'
            malformed = copy.deepcopy(pin)
            malformed['result'].update(previewStatus='running', previewUrl='http://example.com/')
            with pytest.raises(PublishingError) as error:
                client.validate_result('receipt', malformed)
            assert error.value.code == 'target_mismatch'
