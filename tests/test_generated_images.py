import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.host.session import _apply_host_policy
from test_output_images import png


def receipt(root, name, *, inputs=None):
    image = png(3 if inputs else 2, 2)
    (root / (name + '.png')).write_bytes(image)
    row = {'schema': 'amplifier.image.receipt.v1', 'status': 'completed',
        'requestId': name, 'requestHash': 'a' * 64, 'backend': 'fixture', 'model': 'fixture-image',
        'operation': 'edit' if inputs else 'generate', 'inputs': inputs or [],
        'artifact': {'path': name + '.png', 'sha256': hashlib.sha256(image).hexdigest(), 'bytes': len(image),
                     'width': 3 if inputs else 2, 'height': 2, 'mimeType': 'image/png', 'mode': 'RGB'}}
    (root / (name + '.json')).write_text(json.dumps(row))
    return row, image


def routed_receipt(root, name, *, inputs=None, response_api=False):
    row, image = receipt(root, name, inputs=inputs)
    row.update(planHash='b' * 64, providerInstance='fixture',
               models=[{'purpose': 'image', 'requested': 'fixture-image', 'resolved': None}],
               upstream={'request_id': 'request-fixture', 'response_id': None, 'image_call_id': None},
               dispatchState='response_received', reasonCode='completed')
    if response_api:
        row['models'].insert(0, {'purpose': 'controller', 'requested': 'fixture-controller',
                                 'resolved': 'fixture-controller-version'})
        row['upstream'].update(response_id='response-fixture', image_call_id='image-fixture')
    (root / (name + '.json')).write_text(json.dumps(row))
    return row, image


@pytest.mark.parametrize('response_api', [False, True])
async def test_routed_receipts_preserve_provenance_and_edit_parent_after_reopen(tmp_path, response_api):
    app_home = tmp_path / 'app'
    app = AppService(app_home, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        first, image = routed_receipt(tmp_path, 'routed', response_api=response_api)
        original_receipt = (tmp_path / 'routed.json').read_bytes()
        generated = (await app.dispatch('outputs.attachImage', {
            'sessionId': sid, 'title': 'Routed image', 'receiptPath': 'routed.json'}))['result']
        for key in ('planHash', 'providerInstance', 'models', 'upstream', 'dispatchState', 'reasonCode'):
            assert generated['imageGeneration'][key] == first[key]
        assert generated['imageGeneration']['receiptSha256'] == hashlib.sha256(original_receipt).hexdigest()
        assert generated['imageGeneration']['provenance'] == 'producer-reported; local artifact bytes verified'
        edited, next_image = routed_receipt(tmp_path, 'routed-edit', response_api=response_api, inputs=[{
            'path': 'routed.png', 'sha256': first['artifact']['sha256'], 'role': 'target'}])
        args = {'sessionId': sid, 'title': 'Routed edit', 'receiptPath': 'routed-edit.json'}
        with pytest.raises(AppError, match='original image first'):
            await app.dispatch('outputs.attachImage', args)
        second = (await app.dispatch('outputs.attachImage', {**args, 'parentId': generated['id']}))['result']
        assert second['parentId'] == generated['id']
        assert (tmp_path / 'routed.json').read_bytes() == original_receipt
        await app.close()
        app = AppService(app_home, workspace=tmp_path)
        restored = (await app.dispatch('outputs.read', {'sessionId': sid, 'id': generated['id']}))['result']
        assert restored['imageGeneration'] == generated['imageGeneration']
        assert app.outputs.content(restored) == image
        restored_edit = (await app.dispatch('outputs.read', {'sessionId': sid, 'id': second['id']}))['result']
        assert restored_edit['parentId'] == generated['id']
        assert restored_edit['imageGeneration'] == second['imageGeneration']
        assert app.outputs.content(restored_edit) == next_image
    finally:
        await app.close()


@pytest.mark.parametrize(('field', 'value'), [
    ('planHash', 'not-a-hash'), ('providerInstance', ''), ('providerInstance', 'x' * 501),
    ('models', []), ('models', [None]),
    ('models', [{'purpose': 'image', 'requested': 'image', 'resolved': None}] * 5),
    ('models', [{'purpose': 'image', 'requested': 'image', 'resolved': None, 'credential': 'secret-marker'}]),
    ('models', [{'purpose': 'image', 'requested': 'image', 'resolved': {'credential': 'secret-marker'}}]),
    ('models', [{'purpose': 'unsupported', 'requested': 'image', 'resolved': None}]),
    ('models', [{'purpose': 'image', 'requested': 'image'}]),
    ('models', [{'purpose': 'controller', 'requested': 'controller', 'resolved': None}]),
    ('models', [{'purpose': 'image', 'requested': 'image', 'resolved': None}] * 2),
    ('upstream', []), ('upstream', {'request_id': 'x' * 513}),
    ('providerRequestId', 'x' * 513),
    ('upstream', {'credential': 'secret-marker'}),
    ('upstream', {'request_id': {'credential': 'secret-marker'}}),
    ('dispatchState', 'sent'), ('reasonCode', 'secret-marker'), ('reasonCode', 'x' * 65),
    ('status', 'unknown'), ('status', 'failed'), ('status', 'blocked'),
    ('credential', 'secret-marker'),
])
async def test_invalid_routed_receipt_metadata_is_not_accepted(tmp_path, field, value):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        row, _ = routed_receipt(tmp_path, 'bad-routed')
        row[field] = value
        (tmp_path / 'bad-routed.json').write_text(json.dumps(row))
        with pytest.raises(AppError) as error:
            await app.dispatch('outputs.attachImage', {
                'sessionId': sid, 'title': 'Bad routed image', 'receiptPath': 'bad-routed.json'})
        assert 'secret-marker' not in str(error.value)
        assert app.outputs.store.list(sid)['items'] == []
    finally:
        await app.close()


@pytest.mark.parametrize('corruption', ['hash', 'dimensions', 'outside', 'parent', 'size'])
async def test_routing_fields_do_not_bypass_existing_receipt_checks(tmp_path, corruption):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        row, image = routed_receipt(tmp_path, 'routed-corrupt')
        expected_error = None
        if corruption == 'hash':
            row['artifact']['sha256'] = '0' * 64
        elif corruption == 'dimensions':
            row['artifact']['width'] = 20
        elif corruption == 'outside':
            outside = tmp_path.parent / (tmp_path.name + '-outside.png')
            outside.write_bytes(image)
            row['artifact']['path'] = str(outside)
            expected_error = 'Output files must stay inside this conversation workspace'
        elif corruption == 'parent':
            row['operation'] = 'edit'
        elif corruption == 'size':
            row['options'] = {'oversized': 'x' * 64000}
        (tmp_path / 'routed-corrupt.json').write_text(json.dumps(row))
        with pytest.raises(AppError, match=expected_error):
            await app.dispatch('outputs.attachImage', {
                'sessionId': sid, 'title': 'Corrupt image', 'receiptPath': 'routed-corrupt.json'})
        assert app.outputs.store.list(sid)['items'] == []
    finally:
        await app.close()


@pytest.mark.parametrize('upstream', [None, {}, {'request_id': ''}, {'request_id': 'r' * 512}])
def test_routed_optional_identifiers_keep_observed_null_empty_and_bounded_values(tmp_path, upstream):
    from amplifier_web.image_receipts import read_image_receipt
    row, image = routed_receipt(tmp_path, 'identifiers')
    row.update(upstream=upstream, providerRequestId='r' * 512)
    row['models'][0]['resolved'] = ''
    (tmp_path / 'identifiers.json').write_text(json.dumps(row))
    observed, _, _, metadata = read_image_receipt(tmp_path, 'identifiers.json')
    assert observed == image
    assert metadata['upstream'] == upstream
    assert metadata['providerRequestId'] == 'r' * 512
    assert metadata['models'][0]['resolved'] == ''


async def test_shared_image_attachment_preserves_exact_bytes_parent_and_passive_state(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        app.state['view']['draft'] = 'Unsent'
        first, image = receipt(tmp_path, 'first')
        generated = (await app.app_bridge('dispatch', {'action': 'outputs.attachImage',
            'args': {'title': 'Original', 'receiptPath': 'first.json'}, 'id': 'image-attach'}, sid))['result']
        assert generated['sha256'] == first['artifact']['sha256']
        assert generated['imageGeneration']['provenance'] == 'producer-reported; local artifact bytes verified'
        edited, next_image = receipt(tmp_path, 'edit', inputs=[{
            'path': 'first.png', 'sha256': generated['sha256'], 'role': 'target'}])
        args = {'sessionId': sid, 'title': 'Edit', 'receiptPath': 'edit.json'}
        with pytest.raises(AppError, match='original image first'):
            await app.dispatch('outputs.attachImage', args)
        second = (await app.dispatch('outputs.attachImage', {**args, 'parentId': generated['id']}, command_id='attach-edit'))['result']
        duplicate = (await app.dispatch('outputs.attachImage', {**args, 'parentId': generated['id']}, command_id='attach-edit'))['result']
        assert duplicate['id'] == second['id']
        assert second['parentId'] == generated['id'] and second['version'] == 2
        assert (tmp_path / 'first.png').read_bytes() == image
        (tmp_path / 'edit.png').write_bytes(b'changed after snapshot')
        assert app.outputs.content(second) == next_image
        observed = await app.app_bridge('outputs.image.read', {'id': second['id'], 'sha256': second['sha256']}, sid)
        assert observed['width'] == 3 and observed['evidence'] == 'immutable-output-snapshot'
        assert app.state['selectedSessionId'] == sid and app.state['view']['draft'] == 'Unsent'
        with pytest.raises(AppError, match='PNG|receipt'):
            await app.dispatch('outputs.attachImage', {**args, 'parentId': generated['id']})
    finally:
        await app.close()


@pytest.mark.parametrize('corruption', ['unknown', 'hash', 'dimensions', 'parent', 'outside', 'extra', 'text_type', 'text_length'])
async def test_bad_receipts_are_not_accepted(tmp_path, corruption):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        row, _ = receipt(tmp_path, 'bad')
        if corruption == 'unknown':
            row['status'] = 'unknown'
        elif corruption == 'hash':
            row['artifact']['sha256'] = '0' * 64
        elif corruption == 'dimensions':
            row['artifact']['width'] = 20
        elif corruption == 'parent':
            row['operation'] = 'edit'
        elif corruption == 'outside':
            row['artifact']['path'] = str(tmp_path.parent / 'outside.png')
        elif corruption == 'text_type':
            row['text'] = {'unexpected': True}
        elif corruption == 'text_length':
            row['text'] = 'x' * 32001
        elif corruption == 'extra':
            row['credential'] = 'must not echo this value'
        (tmp_path / 'bad.json').write_text(json.dumps(row))
        with pytest.raises(AppError) as error:
            await app.dispatch('outputs.attachImage', {'sessionId': sid, 'title': 'Bad', 'receiptPath': 'bad.json'})
        assert 'must not echo' not in str(error.value)
        assert app.outputs.store.list(sid)['items'] == []
    finally:
        await app.close()


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('section', ['modules', 'config', 'overrides'])
def test_image_writer_inherits_effective_host_denials(tmp_path, child, section):
    from amplifier_foundation import Bundle
    settings = {'allowed_write_paths': [str(tmp_path / 'extra')], 'denied_write_paths': ['private']}
    settings = ({'overrides': {'tool-filesystem': {'config': settings}}} if section == 'overrides'
                else {section: {'tools': [{'module': 'tool-filesystem', 'config': settings}]}})
    declaration = {'module': 'tool-image', 'config': {'backend': 'fixture', 'denied_write_paths': ['images-private']}}
    bundle = Bundle(name='images', tools=[] if child else [declaration],
                    agents={'worker': {'tools': [declaration]}} if child else {})
    config = SimpleNamespace(settings=settings, workspace=tmp_path / 'history')
    _apply_host_policy(bundle, config, execution_workspace=tmp_path / 'checkout')
    row = bundle.agents['worker']['tools'][0] if child else bundle.tools[0]
    assert row['config']['backend'] == 'fixture'
    assert set(row['config']['denied_write_paths']) == {str(tmp_path / 'checkout/private'), str(tmp_path / 'checkout/images-private')}
    assert str(tmp_path / 'checkout') in row['config']['allowed_write_paths']


def test_declared_filesystem_denial_cannot_be_bypassed_by_image_tool(tmp_path):
    from amplifier_foundation import Bundle
    bundle = Bundle(name='images', tools=[{'module': 'tool-image'},
        {'module': 'tool-filesystem', 'config': {'denied_write_paths': ['private']}}],
        agents={'child': {'tools': [{'module': 'tool-image'}]}})
    _apply_host_policy(bundle, SimpleNamespace(settings={}, workspace=tmp_path))
    for row in (bundle.tools[0], bundle.agents['child']['tools'][0]):
        assert row['config']['denied_write_paths'] == [str(tmp_path / 'private')]


def test_child_declaration_denial_is_not_applied_to_parent_or_sibling(tmp_path):
    from amplifier_foundation import Bundle
    bundle = Bundle(name='images', tools=[{'module': 'tool-image'}, {'module': 'tool-filesystem'}],
        agents={'restricted': {'tools': [{'module': 'tool-image'},
            {'module': 'tool-filesystem', 'config': {'denied_write_paths': ['artifacts/images']}}]},
            'sibling': {'tools': [{'module': 'tool-image'}]}})
    _apply_host_policy(bundle, SimpleNamespace(settings={}, workspace=tmp_path))
    assert not bundle.tools[0]['config'].get('denied_write_paths')
    assert not bundle.agents['sibling']['tools'][0]['config'].get('denied_write_paths')
    assert bundle.agents['restricted']['tools'][0]['config']['denied_write_paths'] == [str(tmp_path / 'artifacts/images')]


def test_image_uploads_inherit_shared_and_declared_read_restrictions(tmp_path):
    from amplifier_foundation import Bundle
    bundle = Bundle(name='images', tools=[{'module': 'tool-image', 'config': {'allowed_read_paths': ['inputs'], 'denied_read_paths': ['inputs/own']}},
        {'module': 'tool-filesystem', 'config': {'denied_read_paths': ['inputs/private']}}])
    settings = {'config': {'tools': [{'module': 'tool-filesystem', 'config': {'denied_read_paths': ['inputs/shared']}}]}}
    _apply_host_policy(bundle, SimpleNamespace(settings=settings, workspace=tmp_path))
    row = bundle.tools[0]['config']
    assert set(row['denied_read_paths']) == {str(tmp_path / path) for path in ('inputs/own', 'inputs/private', 'inputs/shared')}
    assert row['allowed_read_paths'] == [str(tmp_path / 'inputs')]
