import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService, validate_theme
from amplifier_web import theme_library
from test_canvas_apps import app, dispatch


async def test_bundled_library_previews_applies_and_reverts_without_changing_chat(app):
    original = dict(app.state['theme'])
    sid = app.state['selectedSessionId']
    listing = (await dispatch(app, 'theme.list', {}))['result']
    assert [row['name'] for row in listing['items']] == ['Amplifier', 'Aurora', 'Atelier', 'Graphite', 'Digital Rain', 'Lightline', 'Breadbin']
    assert not Path(listing['directory']).exists(), 'Browsing does not allocate user storage'
    for row in listing['items']:
        assert 'css' not in row
        value = (await dispatch(app, 'theme.read', {'id': row['id']}))['result']
        assert row['fingerprint'] == value['fingerprint']
        validate_theme(value['css'])
        await dispatch(app, 'theme.preview', {'name': value['name'], 'css': value['css']})
        assert app.state['theme'] == original
        assert not app.clients.records['two']['view'].get('themePreview')
        await dispatch(app, 'theme.apply', {'name': value['name'], 'css': value['css']})
        assert app.state['theme']['css'] == value['css']
        await dispatch(app, 'theme.revert', {})
        assert app.state['theme'] == original
    assert app.state['selectedSessionId'] == sid


async def test_saved_appearance_survives_restart_and_external_files_are_discovered(app, tmp_path):
    args = {'name': 'A shared look', 'css': '#amp-one{--a-accent:#123456}', 'description': 'Personal colors'}
    result = (await dispatch(app, 'theme.save', args, origin='agent'))['result']
    duplicate = (await dispatch(app, 'theme.save', args))['result']
    assert duplicate['id'] == result['id']
    root = Path(result['directory'])
    assert len(list(root.glob('*.css'))) == 1
    external = root / 'From a friend.amplifier.css'
    external.write_text('#amp-one{--a-accent:#654321}')
    listing = (await dispatch(app, 'theme.list', {}))['result']
    assert {r['name'] for r in listing['items']} >= {'A shared look', 'From a friend'}
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        value = (await restored.dispatch('theme.read', {'id': result['id']}))['result']
        assert value['name'] == args['name'] and value['css'].endswith(args['css'])
    finally:
        await restored.close()


async def test_bad_imports_do_not_replace_library_or_current_appearance(app, tmp_path):
    original = dict(app.state['theme'])
    with pytest.raises(AppError):
        await dispatch(app, 'theme.save', {'name': 'External', 'css': '@import "https://example.com/style.css";'})
    with pytest.raises(AppError):
        await dispatch(app, 'theme.read', {'id': 'saved:../outside.css'})
    root = app.data_dir / 'themes'
    root.mkdir()
    outside = tmp_path / 'outside.css'
    outside.write_text('#amp-one{color:red}')
    (root / 'linked.css').symlink_to(outside)
    (root / 'bad.css').write_text('@import "https://example.com/bad.css";')
    listing = (await dispatch(app, 'theme.list', {}))['result']
    assert len(listing['items']) == 7 and len(listing['errors']) == 2
    assert app.state['theme'] == original


async def test_name_cannot_escape_metadata_comment_and_reset_ends_preview(app):
    saved = (await dispatch(app, 'theme.save', {'name': 'Look */ unusual', 'css': '#amp-one{color:red}'}))['result']
    row = (await dispatch(app, 'theme.read', {'id': saved['id']}))['result']
    assert row['name'] == 'Look */ unusual'
    validate_theme(row['css'])
    await dispatch(app, 'theme.preview', {'name': row['name'], 'css': row['css']})
    await dispatch(app, 'theme.reset', {})
    assert app.clients.records['one']['view']['themePreview'] is False


async def test_update_and_delete_require_exact_read_and_preserve_applied_css(app, tmp_path):
    saved = (await dispatch(app, 'theme.save', {
        'name': 'Ocean', 'description': 'Original', 'css': '#amp-one{--a-accent:#123456}',
    }))['result']
    original = (await dispatch(app, 'theme.read', {'id': saved['id']}))['result']
    await dispatch(app, 'theme.apply', {'name': original['name'], 'css': original['css']})
    applied = dict(app.state['theme'])
    updated = (await dispatch(app, 'theme.update', {
        'id': saved['id'], 'expectedFingerprint': original['fingerprint'],
        'name': 'Ocean revised', 'description': 'Updated', 'css': original['css'] + '\n#amp-one{--a-accent:#654321}',
    }, origin='agent'))['result']
    assert updated['id'] == original['id'] and updated['fingerprint'] != original['fingerprint']
    assert updated['name'] == 'Ocean revised' and updated['description'] == 'Updated'
    assert app.state['theme'] == applied, 'Updating the library does not rewrite applied CSS'
    for action in ('theme.update', 'theme.delete'):
        args = {'id': saved['id'], 'expectedFingerprint': original['fingerprint']}
        if action == 'theme.update':
            args.update(name='Stale', css='#amp-one{color:red}')
        with pytest.raises(AppError, match='changed') as error:
            await dispatch(app, action, args)
        assert error.value.status == 409
    value = (await dispatch(app, 'theme.read', {'id': updated['id']}))['result']
    assert value['fingerprint'] == updated['fingerprint']
    assert value['css'].count(theme_library.MARKER) == 1
    deleted = (await dispatch(app, 'theme.delete', {
        'id': value['id'], 'expectedFingerprint': value['fingerprint'],
    }))['result']
    assert deleted['deleted'] and app.state['theme'] == applied
    with pytest.raises(AppError):
        await dispatch(app, 'theme.read', {'id': value['id']})
    assert not list((app.data_dir / 'themes').iterdir())
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        assert restored.state['theme'] == applied, 'Deleting the source file cannot remove a committed appearance'
    finally:
        await restored.close()


async def test_portable_metadata_roundtrip_imports_do_not_apply_or_overwrite(app):
    before = dict(app.state['theme'])
    meta = {'name': 'A name */ with unicode é', 'description': 'A portable description', 'author': 'Example author'}
    header = theme_library.MARKER + json.dumps(meta).replace('*/', '*\\/') + ' */\n'
    css = header + '#amp-one{--a-accent:#123456;background-image:url("data:image/png;base64,aGVsbG8=")}'
    imported = (await dispatch(app, 'theme.import', {'css': css, 'fallbackName': 'Downloaded file'}))['result']
    assert imported['name'] == meta['name'] and imported['description'] == meta['description']
    for _ in range(3):
        exported = await dispatch(app, 'theme.export', {'id': imported['id']}, origin='agent')
        content = exported['effects'][0]['content']
        assert content.count(theme_library.MARKER) == 1
        metadata, body = theme_library.metadata(content)
        assert metadata == meta and body.endswith('aGVsbG8=")}')
        repeated = (await dispatch(app, 'theme.import', {'css': content}))['result']
        assert repeated['id'] == imported['id'], 'An identical portable upload is idempotent'
    different = (await dispatch(app, 'theme.import', {'css': content + '\n#amp-one{color:red}'}))['result']
    assert different['id'] != imported['id'], 'A changed upload creates another entry, never an overwrite'
    assert (await dispatch(app, 'theme.read', {'id': imported['id']}))['result']['css'] == content
    assert app.state['theme'] == before
    assert len(list((app.data_dir / 'themes').glob('*.css'))) == 2


@pytest.mark.parametrize('deleted_slot', [1, 2])
async def test_reimport_reuses_identical_collision_after_deleting_earlier_slot(app, deleted_slot):
    original = (await dispatch(app, 'theme.save', {
        'name': 'Ocean', 'css': '#amp-one{color:red}',
    }))['result']
    exported = (await dispatch(app, 'theme.export', {'id': original['id']}))['result']['css']
    restored = original
    for index in range(1, deleted_slot + 1):
        updated = (await dispatch(app, 'theme.update', {
            'id': restored['id'], 'expectedFingerprint': restored['fingerprint'],
            'name': 'Ocean', 'css': f'#amp-one{{--collision:{index};color:blue}}',
        }))['result']
        restored = (await dispatch(app, 'theme.import', {'css': exported}))['result']
    await dispatch(app, 'theme.delete', {
        'id': updated['id'], 'expectedFingerprint': updated['fingerprint'],
    })
    for _ in range(2):
        repeated = (await dispatch(app, 'theme.import', {'css': exported}))['result']
        assert repeated['id'] == restored['id']
    root = app.data_dir / 'themes'
    identical = [path for path in root.glob('*.css') if path.read_text() == exported]
    assert [path.name for path in identical] == [restored['id'][6:]]
    assert not (root / updated['id'][6:]).exists(), 'Reimport must not refill an earlier gap'


def test_import_collision_lookup_opens_only_existing_family_members(app, monkeypatch):
    root = app.data_dir / 'themes'
    root.mkdir()
    name = 'ocean-example.amplifier.css'
    content = theme_library.portable('#amp-one{color:red}', 'Ocean')
    far_name = 'ocean-example-1000.amplifier.css'
    (root / far_name).write_text(content)
    (root / 'unrelated.css').write_text('#amp-one{color:blue}')
    # Out-of-range/noncanonical suffixes do not widen the bounded identity family.
    for suffix in ('1', '0002', '1001'):
        (root / f'ocean-example-{suffix}.amplifier.css').symlink_to(root / 'absent.css')
    opened = []
    read_file = theme_library.read_file

    def inspect(path, descriptor):
        opened.append(path.name)
        return read_file(path, descriptor)

    monkeypatch.setattr(theme_library, 'read_file', inspect)
    with theme_library.directory(app, writing=True) as descriptor:
        assert theme_library.import_name(descriptor, name, content) == far_name
    assert opened == [far_name], 'Do not probe missing slots or unrelated files'


async def test_export_builtin_by_id_and_current_without_id(app):
    before = dict(app.state['theme'])
    builtin = (await dispatch(app, 'theme.read', {'id': 'builtin:aurora'}))['result']
    await dispatch(app, 'theme.preview', {'name': builtin['name'], 'css': builtin['css']})
    current = await dispatch(app, 'theme.export', {})
    assert current['effects'][0]['content'] == before['css']
    assert current['effects'][0]['filename'] == 'amplifier-skin.css'
    exported = await dispatch(app, 'theme.export', {'id': builtin['id']})
    assert exported['effects'][0]['filename'] == 'aurora.amplifier.css'
    row = (await dispatch(app, 'theme.import', {'css': exported['result']['css']}))['result']
    assert row['source'] == 'saved' and row['name'] == builtin['name']
    assert row['description'] == builtin['description']
    assert app.state['theme'] == before


async def test_raw_import_fallback_name_and_explicit_metadata_override(app):
    raw = (await dispatch(app, 'theme.import', {
        'css': '#amp-one{color:red}', 'fallbackName': 'From a friend',
    }))['result']
    assert raw['name'] == 'From a friend'
    css = (await dispatch(app, 'theme.read', {'id': raw['id']}))['result']['css']
    copy = (await dispatch(app, 'theme.import', {'css': css, 'name': 'My copy', 'description': 'Mine'}))['result']
    assert copy['name'] == 'My copy' and copy['description'] == 'Mine'


async def test_external_change_fences_updates_and_invalid_edits_retain_original(app):
    saved = (await dispatch(app, 'theme.save', {'name': 'Draft', 'css': '#amp-one{color:red}'}))['result']
    path = app.data_dir / 'themes' / saved['id'][6:]
    with pytest.raises(AppError):
        await dispatch(app, 'theme.update', {
            'id': saved['id'], 'expectedFingerprint': saved['fingerprint'],
            'name': 'Invalid', 'css': '@import "https://example.com/style.css";',
        })
    assert theme_library.fingerprint(path.read_text()) == saved['fingerprint']
    path.write_text('#amp-one{color:blue}')
    for action in ('theme.update', 'theme.delete'):
        args = {'id': saved['id'], 'expectedFingerprint': saved['fingerprint']}
        if action == 'theme.update':
            args.update(name='Stale', css='#amp-one{color:red}')
        with pytest.raises(AppError, match='changed'):
            await dispatch(app, action, args)
    assert path.read_text() == '#amp-one{color:blue}'
    assert not list(path.parent.glob('*.tmp'))


@pytest.mark.parametrize('identity', ['builtin:default', 'builtin:digital-rain', 'saved:../outside.css', 'saved:..\\outside.css', 'saved:/tmp/outside.css'])
async def test_only_saved_local_entries_can_be_mutated(app, identity):
    for action in ('theme.delete', 'theme.update'):
        args = {'id': identity, 'expectedFingerprint': '0' * 64}
        if action == 'theme.update':
            args.update(name='No', css='#amp-one{color:red}')
        with pytest.raises(AppError):
            await dispatch(app, action, args)
    assert not (app.data_dir / 'themes').exists()


async def test_library_links_and_special_files_are_rejected(app, tmp_path):
    saved = (await dispatch(app, 'theme.save', {'name': 'Linked', 'css': '#amp-one{color:red}'}))['result']
    path = app.data_dir / 'themes' / saved['id'][6:]
    outside = tmp_path / 'outside.css'
    outside.write_text(path.read_text())
    path.unlink()
    path.symlink_to(outside)
    for action in ('theme.read', 'theme.export', 'theme.delete', 'theme.update'):
        args = {'id': saved['id']}
        if action in ('theme.delete', 'theme.update'):
            args['expectedFingerprint'] = saved['fingerprint']
        if action == 'theme.update':
            args.update(name='No', css='#amp-one{color:blue}')
        with pytest.raises(AppError):
            await dispatch(app, action, args)
    assert outside.read_text().endswith('#amp-one{color:red}') and path.is_symlink()
    if hasattr(os, 'mkfifo'):
        pipe = path.parent / 'pipe.css'
        os.mkfifo(pipe)
        try:
            listing = (await dispatch(app, 'theme.list', {}))['result']
            assert {row['name'] for row in listing['errors']} == {path.name, 'pipe.css'}
        finally:
            pipe.unlink(missing_ok=True)


async def test_symlink_library_directory_cannot_be_read_or_written(app, tmp_path):
    outside = tmp_path / 'other-library'
    outside.mkdir()
    (outside / 'outside.css').write_text('#amp-one{color:red}')
    (app.data_dir / 'themes').symlink_to(outside, target_is_directory=True)
    for action, args in [
        ('theme.list', {}),
        ('theme.read', {'id': 'saved:outside.css'}),
        ('theme.save', {'name': 'No', 'css': '#amp-one{color:blue}'}),
        ('theme.import', {'css': '#amp-one{color:blue}'}),
    ]:
        with pytest.raises(AppError, match='real directory'):
            await dispatch(app, action, args)
    assert [path.name for path in outside.iterdir()] == ['outside.css']


@pytest.mark.parametrize('css', [
    '@import "https://example.com/style.css";', '#amp-one{background:url(https://example.com/a.png)}',
    '#amp-one{background:url("file:///tmp/a.png")}', '#amp-one{background:url("data:image/svg+xml;base64,abc")}',
    '@font-face{src:url("https://example.com/font.woff")}', '</style><script>alert(1)</script>',
    '#amp-one{background:image-set("https://example.com/a.png" 1x)}',
    '#amp-one{--asset:"https://example.com/a.png";background:image-set(var(--asset) 1x)}',
    '#amp-one{background:image("https://example.com/a.png")}',
])
async def test_import_uses_the_same_self_contained_validator(app, css):
    before = dict(app.state['theme'])
    with pytest.raises(AppError):
        await dispatch(app, 'theme.import', {'css': css})
    assert not (app.data_dir / 'themes').exists() and app.state['theme'] == before


async def test_cooperating_concurrent_updates_cannot_overwrite_each_other(app):
    saved = (await dispatch(app, 'theme.save', {'name': 'Concurrent', 'css': '#amp-one{color:red}'}))['result']
    args = {'id': saved['id'], 'expectedFingerprint': saved['fingerprint'], 'name': saved['name']}
    def update(color):
        try:
            return theme_library.mutate(app, {**args, 'css': '#amp-one{color:' + color + '}'})
        except AppError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['blue', 'green']))
    assert sum(isinstance(row, AppError) for row in results) == 1
    assert next(row for row in results if isinstance(row, AppError)).status == 409


async def test_duplicate_save_does_not_follow_replaced_file_symlink(app, tmp_path):
    args = {'name': 'Duplicate', 'css': '#amp-one{color:red}'}
    saved = (await dispatch(app, 'theme.save', args))['result']
    path = app.data_dir / 'themes' / saved['id'][6:]
    outside = tmp_path / 'duplicate.css'
    outside.write_text(path.read_text())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(AppError, match='Nothing was replaced'):
        await dispatch(app, 'theme.save', args)
    assert path.is_symlink() and outside.read_text().endswith(args['css'])


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('bom', ['', '\ufeff'])
async def test_crlf_bom_and_legacy_nested_metadata_do_not_accumulate_headers(app, newline, bom):
    meta = {'name': 'Océan', 'description': 'Keep me', 'author': 'Example'}
    outer = theme_library.MARKER + json.dumps(meta, ensure_ascii=False) + ' */' + newline
    inner = theme_library.MARKER + '{"name":"Inner","description":"Older"} */' + newline
    body = '#amp-one{' + newline + 'color:red' + newline + '}'
    css = bom + outer + inner + body
    imported = (await dispatch(app, 'theme.import', {'css': css, 'fallbackName': 'Downloaded file'}))['result']
    for _ in range(3):
        row = (await dispatch(app, 'theme.read', {'id': imported['id']}))['result']
        assert row['name'] == meta['name'] and row['description'] == meta['description']
        assert row['css'].count(theme_library.MARKER) == 1
        assert theme_library.metadata(row['css']) == (meta, body)
        assert row['fingerprint'] == theme_library.fingerprint(row['css'])
        exported = (await dispatch(app, 'theme.export', {'id': row['id']}))['result']['css']
        repeated = (await dispatch(app, 'theme.import', {'css': exported}))['result']
        assert repeated['id'] == imported['id']


async def test_old_export_imports_after_in_place_update_without_overwriting(app):
    saved = (await dispatch(app, 'theme.save', {
        'name': 'Ocean', 'description': 'Portable colors', 'css': '#amp-one{color:red}',
    }))['result']
    exported = (await dispatch(app, 'theme.export', {'id': saved['id']}))['result']['css']
    updated = (await dispatch(app, 'theme.update', {
        'id': saved['id'], 'expectedFingerprint': saved['fingerprint'],
        'name': saved['name'], 'css': '#amp-one{color:blue}',
    }))['result']
    restored = (await dispatch(app, 'theme.import', {'css': exported}))['result']
    assert restored['id'] != updated['id']
    assert restored['id'] == updated['id'].removesuffix('.amplifier.css') + '-2.amplifier.css'
    assert restored['name'] == saved['name'] and restored['description'] == saved['description']
    for _ in range(2):
        repeated = (await dispatch(app, 'theme.import', {'css': exported}))['result']
        assert repeated['id'] == restored['id']
    current = (await dispatch(app, 'theme.read', {'id': updated['id']}))['result']
    assert current['fingerprint'] == updated['fingerprint'] and current['css'].endswith('color:blue}')
    assert (await dispatch(app, 'theme.read', {'id': restored['id']}))['result']['css'] == exported
    assert len(list((app.data_dir / 'themes').glob('*.css'))) == 2
