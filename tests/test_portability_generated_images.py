"""Generated images transfer as their existing immutable saved output bodies."""
import base64
import copy
import json

import pytest

from amplifier_web import portability_data
from amplifier_worktrees.git import digest
from test_generated_images import receipt as image_receipt
from test_output_images import png
from test_portability import host_env, setup_hosts
from test_portability_data import payload as payload
from test_portability_install import app as app, snapshot
from test_portability_outputs import activate, release_to


async def test_generated_image_roundtrip_preserves_bytes_lineage_and_exact_retry(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        produced, original = image_receipt(root, 'generated')
        first_args = {'sessionId': sid, 'title': 'Generated', 'messageId': 'typed',
                      'receiptPath': 'generated.json'}
        first = (await app.dispatch('outputs.attachImage', first_args,
                                    command_id='generated-output'))['result']
        _, edited = image_receipt(root, 'edited', inputs=[{
            'path': 'generated.png', 'sha256': produced['artifact']['sha256'], 'role': 'target'}])
        edit_args = {**first_args, 'title': 'Edited', 'receiptPath': 'edited.json',
                     'parentId': first['id'], 'evidenceIds': [first['id']]}
        second = (await app.dispatch('outputs.attachImage', edit_args,
                                     command_id='edited-output'))['result']
        await app.dispatch('outputs.comment', {'sessionId': sid, 'id': second['id'],
                                              'body': 'Keep the exact edited image.'})
        await app.dispatch('outputs.unlink', {'sessionId': sid, 'id': first['id'],
                                             'expectedRevision': 1})
        rows = [app.outputs.store.read(row['id']) for row in (first, second)]
        bodies = {row['body']['$resource']: app.state_resource(row['body']['$resource']) for row in rows}
        canvases = copy.deepcopy([row for row in app.state['canvasArtifacts']
                                  if row['id'] in {first['canvasId'], second['canvasId']}])
        receipts = {identity: app.db.execute('SELECT fingerprint,value FROM output_receipts WHERE id=?',
                                            (identity,)).fetchone()
                    for identity in ('generated-output', 'edited-output')}
        # Neither transfer nor historical output retry may reread producer files.
        for name in ('generated.png', 'generated.json', 'edited.png', 'edited.json'):
            (root / name).unlink()
        for left, right, left_home, right_home, repository in (
                (app, target, source, destination, destrepo), (target, app, destination, source, root)):
            staged, released = await release_to(left, right, sid, left_home, right_home, repository, monkeypatch)
            host_env(monkeypatch, right_home)
            await activate(right, sid, staged, released)
            for expected, data in zip(rows, (original, edited)):
                current = right.outputs.store.read(expected['id'])
                assert current == expected and current['bodyFormat'] == 'canvas-image-v1'
                assert right.outputs.content(current) == data
                assert await right.outputs.content_async(current) == data
                key = current['body']['$resource']
                assert right.state_resource(key) == bodies[key]
            assert right.outputs.store.comments(second['id'])[0]['body'] == 'Keep the exact edited image.'
            for expected in canvases:
                current = next(row for row in right.state['canvasArtifacts'] if row['id'] == expected['id'])
                assert current['body'] == expected['body']
                assert current['publications'] == expected['publications']
            for identity, args, expected in (
                    ('generated-output', first_args, first), ('edited-output', edit_args, second)):
                transferred = right.db.execute('SELECT fingerprint,value FROM output_receipts WHERE id=?',
                                                (identity,)).fetchone()
                assert transferred[0] == receipts[identity][0]
                assert json.loads(transferred[1]) == json.loads(receipts[identity][1])
                duplicate = (await right.dispatch('outputs.attachImage', args, command_id=identity))['result']
                assert duplicate == {**expected, 'duplicate': True}
            assert not right.runtime.workers
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


@pytest.mark.parametrize(('alteration', 'error'), [
    (None, None), ('content-hash', 'evidence hash'), ('resource-hash', 'resource integrity'),
    ('missing-body', 'Malformed'), ('prefix', 'Saved image source'),
    ('base64', 'base64'), ('credential', 'credential'),
])
def test_generated_image_transfer_validation_preserves_hashes_and_scanning(app, payload, alteration, error):
    image = png(2, 2)
    if alteration == 'credential':
        image += b'Bearer synthetic-transfer-credential'
    body = {'content': 'data:image/png;base64,' + base64.b64encode(image).decode()}
    if alteration == 'prefix':
        body['content'] = body['content'].replace('image/png', 'image/jpeg')
    elif alteration == 'base64':
        body['content'] = 'data:image/png;base64,***'
    key = digest(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    row = payload['outputs'][0]
    row.update(bodyFormat='canvas-image-v1', body={'$resource': key}, sha256=digest(image), bytes=len(image))
    payload['resources'] = {key: body}
    payload['outputReceipts'][0]['value'] = copy.deepcopy(row)
    if alteration == 'content-hash':
        row['sha256'] = '0' * 64
    elif alteration == 'resource-hash':
        body['content'] += 'changed'
    elif alteration == 'missing-body':
        payload['resources'].clear()
    before = snapshot(app)
    if alteration:
        with pytest.raises(ValueError, match=error):
            portability_data.validate(payload)
        assert snapshot(app) == before
        return
    original = copy.deepcopy(payload)
    portability_data.validate(payload)
    portability_data.install_outputs(app, payload)
    assert app.outputs.content(app.outputs.store.read(row['id'])) == image
    installed = snapshot(app)
    portability_data.install_outputs(app, payload)
    assert snapshot(app) == installed and payload == original
