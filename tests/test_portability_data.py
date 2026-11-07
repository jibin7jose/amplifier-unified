"""Portable data rejects malformed executable state before destination mutation."""
import base64
import copy
import json
import sqlite3
from threading import RLock
from types import SimpleNamespace

import pytest

from amplifier_outputs.registry import OutputRegistry
from amplifier_web import portability_data as data
from amplifier_worktrees.git import digest


def packed(raw):
    return {'data': base64.b64encode(raw).decode(), 'bytes': len(raw), 'sha256': digest(raw)}


def packed_json(value):
    return packed(json.dumps(value).encode())


@pytest.fixture
def payload():
    resource = {'encoding': 'utf8', 'data': 'Original immutable output'}
    key = digest(json.dumps(resource, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    return {
        'session': {'id': 'task-fixture', 'bundle': 'work', 'messages': [], 'title': 'Fixture task'},
        'nativeIdentity': 'native-fixture',
        'native': {'transcript.jsonl': packed_json({'role': 'user', 'content': 'Saved fixture history'}),
                   'metadata.json': packed_json({'session_id': 'native-fixture', 'bundle': 'work'})},
        'intent': {'bundle': 'work', 'selection': {'provider': 'fixture', 'model': 'fixture-model'},
                   'controls': {'configurator': {'disabled': {'tools': ['tool-fixture']}}}},
        'outputs': [{'id': 'output-fixture', 'sessionId': 'task-fixture', 'createdAt': 1,
                     'body': {'$resource': key}, 'sha256': digest(resource['data'].encode())}],
        'comments': [{'id': 'comment-fixture', 'outputId': 'output-fixture', 'body': 'Keep this review'}],
        'outputReceipts': [{'id': 'output-command-fixture', 'fingerprint': 'fixture-fingerprint',
                            'value': {'id': 'output-fixture', 'sessionId': 'task-fixture'}}],
        'canvas': [{'id': 'canvas-fixture', 'sessionId': 'task-fixture'}],
        'observations': {'liveJobs': [], 'operations': [], 'operationRequests': [], 'questions': [],
                         'workers': [], 'approvals': [], 'schedules': [], 'scheduleRuns': []},
        'resources': {key: resource}, 'origin': {'historyHome': 'fixture-source', 'executionDirectory': 'fixture-source'},
    }


@pytest.fixture
def app():
    db, operations = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    OutputRegistry(db)
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('CREATE TABLE questions (id TEXT PRIMARY KEY, session_id TEXT, created REAL, value TEXT)')
    operations.execute('CREATE TABLE operation_requests (session_id TEXT, id TEXT, signature TEXT, value TEXT, PRIMARY KEY(session_id,id))')
    result = SimpleNamespace(db=db, state={'canvasArtifacts': []},
        operations=SimpleNamespace(journal=SimpleNamespace(db=operations, lock=RLock())))
    yield result
    operations.close()
    db.close()


def snapshot(app):
    return {
        'tables': {name: app.db.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                   for name in ('output_records', 'output_comments', 'output_receipts', 'state_resources', 'questions')},
        'requests': app.operations.journal.db.execute('SELECT * FROM operation_requests ORDER BY rowid').fetchall(),
        'canvas': copy.deepcopy(app.state['canvasArtifacts']),
    }


def test_valid_payload_preserves_configurator_intent_and_exact_native_bytes(payload):
    original = copy.deepcopy(payload)
    data.validate(payload)
    assert payload == original
    assert data.decode_file(payload['native']['transcript.jsonl']) == b'{"role": "user", "content": "Saved fixture history"}'


@pytest.mark.parametrize('alteration', [None, 'public-hash', 'resource-hash', 'missing-body', 'missing-nested'])
def test_shared_canvas_body_validation_and_exact_receipt_install(app, payload, alteration):
    body = {'content': '<p>Résumé 🌳</p>', 'surface': {'nested': [1, {'label': 'é'}]}}
    reference = digest(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    public = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    row = payload['outputs'][0]
    row.update(kind='canvas', bodyFormat='canvas-json-v1', body={'$resource': reference},
               canvasSource={'$resource': reference}, sha256=digest(public), bytes=len(public))
    payload['resources'] = {reference: body}
    payload['outputReceipts'][0]['value'] = copy.deepcopy(row)
    if alteration == 'public-hash':
        row['sha256'] = '0' * 64
    elif alteration == 'resource-hash':
        body['content'] = 'Changed source'
    elif alteration == 'missing-body':
        payload['resources'].clear()
    elif alteration == 'missing-nested':
        body['surface']['asset'] = {'$resource': '0' * 64}
        updated = digest(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        payload['resources'] = {updated: body}
        row.update(body={'$resource': updated}, canvasSource={'$resource': updated},
                   sha256=digest(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()))
        payload['outputReceipts'][0]['value'] = copy.deepcopy(row)
    if alteration:
        before = snapshot(app)
        with pytest.raises(ValueError):
            data.validate(payload)
        assert snapshot(app) == before
        return
    original = copy.deepcopy(payload)
    data.validate(payload)
    data.install_outputs(app, payload)
    installed = snapshot(app)
    data.install_outputs(app, copy.deepcopy(payload))
    assert snapshot(app) == installed and payload == original
    assert json.loads(app.db.execute('SELECT value FROM output_receipts').fetchone()[0]) == row


@pytest.mark.parametrize('name', ['metadata.json', 'metadata.json.backup'])
@pytest.mark.parametrize('metadata', [None, [], 'invalid', 42])
def test_metadata_requires_an_object_with_controlled_rejection(payload, name, metadata):
    payload['native'][name] = packed_json(metadata)
    with pytest.raises(ValueError):
        data.validate(payload)


@pytest.mark.parametrize('metadata', [
    {'session_id': 'another-native-task'}, {'bundle': 'different-bundle'},
    {'bundle_name': {'executable': 'unreviewed'}}, {'bundle_name': 'bundle:different-bundle'},
])
def test_native_metadata_identity_and_bundle_must_match_intent(payload, metadata):
    payload['native']['metadata.json'] = packed_json(metadata)
    with pytest.raises(ValueError):
        data.validate(payload)


@pytest.mark.parametrize('configurator', [
    None, [], {'overrides': {'provider-fixture': {'source': 'unreviewed-source'}}},
    {'disabled': []}, {'disabled': {'unknown-modules': []}},
    {'disabled': {'providers': 'provider-fixture'}}, {'disabled': {'tools': [False]}},
])
def test_configurator_cannot_transport_executable_overrides_or_malformed_disabling(payload, configurator):
    payload['intent']['controls']['configurator'] = configurator
    with pytest.raises(ValueError):
        data.validate(payload)


@pytest.mark.parametrize('location', ['native', 'checkpoint', 'job'])
@pytest.mark.parametrize('raw', [
    b'{"api\\u005fkey":"synthetic fixture value"}',
    b'{"safe":true}\n{"api\\u005fkey":"synthetic fixture value"}\n',
    b'{"api\\u005fkey":"synthetic fixture value","api_key":""}',
])
def test_secret_fields_cannot_hide_in_encoded_json_jsonl_or_duplicate_keys(payload, location, raw):
    value = packed(raw)
    if location == 'native':
        payload['native']['events.jsonl'] = value
    elif location == 'checkpoint':
        payload['contextCheckpoint'] = value
    else:
        payload['observations']['liveJobs'] = [{'name': 'job-fixture.json', 'file': value}]
    with pytest.raises(ValueError):
        data.validate(payload)


@pytest.mark.parametrize('raw', [b'{broken json', b'{}\nnot json\n', b'{"a":1,"a":2}'])
def test_checkpoint_requires_valid_unambiguous_json(payload, raw):
    payload['contextCheckpoint'] = packed(raw)
    with pytest.raises(ValueError):
        data.validate(payload)


@pytest.mark.parametrize('section', ['session', 'intent', 'native', 'observations', 'resources'])
def test_malformed_required_sections_are_controlled_rejections(payload, section):
    payload[section] = None
    with pytest.raises(ValueError):
        data.validate(payload)


@pytest.mark.parametrize('collision', ['output', 'comment', 'canvas', 'output-receipt', 'question', 'operation-request'])
def test_all_destination_collisions_are_rejected_before_any_import(app, payload, collision):
    if collision == 'output':
        app.db.execute('INSERT INTO output_records VALUES (?,?,?,?)',
            ('output-fixture', 'other-task', 2, '{"original":"retained"}'))
    elif collision == 'comment':
        app.db.execute('INSERT INTO output_comments VALUES (?,?,?)',
            ('comment-fixture', 'other-output', '{"original":"retained"}'))
    elif collision == 'canvas':
        app.state['canvasArtifacts'].append({'id': 'canvas-fixture', 'sessionId': 'other-task'})
    elif collision == 'output-receipt':
        app.db.execute('INSERT INTO output_receipts VALUES (?,?,?)',
            ('output-command-fixture', 'different-fingerprint', '{"original":"retained"}'))
    elif collision == 'question':
        payload['observations']['questions'] = [{'id': 'question-fixture', 'sessionId': 'task-fixture', 'createdAt': 1}]
        app.db.execute('INSERT INTO questions VALUES (?,?,?,?)',
            ('question-fixture', 'other-task', 2, '{"original":"retained"}'))
    else:
        payload['observations']['operationRequests'] = [{'id': 'request-fixture', 'signature': 'fixture-signature',
            'record': {'sessionId': 'task-fixture', 'requestId': 'request-fixture', 'state': 'outcome_unknown'}}]
        app.operations.journal.db.execute('INSERT INTO operation_requests VALUES (?,?,?,?)',
            ('task-fixture', 'request-fixture', 'different-signature', '{"original":"retained"}'))
    data.validate(payload)
    before = snapshot(app)
    with pytest.raises(ValueError):
        data.install_outputs(app, payload)
    assert snapshot(app) == before
    # A later unrelated publish/commit must not make a partial import visible.
    app.db.commit()
    app.operations.journal.db.commit()
    assert snapshot(app) == before


@pytest.mark.parametrize('alteration', ['content-hash', 'foreign-output', 'missing-evidence', 'foreign-comment', 'foreign-canvas', 'duplicate-output'])
def test_output_evidence_and_lineage_are_validated_before_import(payload, alteration):
    if alteration == 'content-hash':
        payload['outputs'][0]['sha256'] = '0' * 64
    elif alteration == 'foreign-output':
        payload['outputs'][0]['sessionId'] = 'another-task'
    elif alteration == 'missing-evidence':
        payload['outputs'][0]['evidenceIds'] = ['missing-output']
    elif alteration == 'foreign-comment':
        payload['comments'][0]['outputId'] = 'another-output'
    elif alteration == 'foreign-canvas':
        payload['canvas'][0]['sessionId'] = 'another-task'
    else:
        payload['outputs'].append(copy.deepcopy(payload['outputs'][0]))
    with pytest.raises(ValueError):
        data.validate(payload)
