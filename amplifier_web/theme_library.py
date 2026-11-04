"""Portable CSS appearances, with bundled examples and a persistent user library."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
import re
import threading

from . import appearance_packages as packages

LIMIT = 1_000_000
MARKER = '/* amplifier-appearance: '
PACKAGE_SUMMARY_CACHE_LIMIT = 128
_PACKAGE_SUMMARY_LOCK = threading.Lock()
TOKENS = ('bg', 'surface', 'soft', 'ink', 'muted', 'line', 'accent', 'tint', 'green', 'danger')
RESOURCE_PRESETS = {
    'digital-rain': {
        'name': 'Digital Rain',
        'description': 'Layered emerald code rain, with pale mint light and deep green dark modes.',
        'palette': {'bg': '#e8f2eb', 'surface': '#f8fcf9', 'ink': '#143522', 'accent': '#176c3b', 'line': '#a5c2ae'},
        'background': 'none',
        'radius': '8px', 'heading': '"SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace', 'tag': 'Animated',
    },
    'lightline': {
        'name': 'Lightline',
        'description': 'Luminous ivory and platinum by day; obsidian and cyan by night. Fine architectural light paths, warm personal messages and readable monospace conversation. Static and self-contained.',
        'palette': {'bg': '#efefec', 'surface': '#fffffc', 'ink': '#242a2e', 'accent': '#315d6b', 'line': '#a0a8aa'},
        'background': 'none',
        'radius': '8px', 'heading': 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', 'tag': 'Architectural',
    },
    'ready': {
        'name': 'Breadbin',
        'description': 'Warm ivory casing, a violet conversation screen and a restrained four-color header edge. Static and self-contained.',
        'palette': {'bg': '#d5c6a6', 'surface': '#f7f1e6', 'ink': '#332d28', 'accent': '#43367b', 'line': '#b6a88d'},
        'background': 'none',
        'radius': '5px', 'heading': 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'tag': 'Retro / Hardware',
    },
}
PRESETS = {
    'aurora': {
        'name': 'Aurora', 'description': 'Luminous color, rounded panels and a flowing aurora background.',
        'light': ['#e8eef7','#ffffff','#f2f6fc','#172945','#526888','#cbd8e8','#5144c8','#e9e5ff','#176951','#ad3454'],
        'dark': ['#11172a','#1c253a','#25314a','#eff4ff','#acbbd4','#3b4b67','#b8adff','#39315c','#7fdbc0','#ffadc0'],
        'background': ['radial-gradient(ellipse at 5% 85%, #a5e5da 0%, transparent 55%), radial-gradient(ellipse at 90% 8%, #c9b2f3 0%, transparent 60%), linear-gradient(130deg, #e8f1fc, #dbe2f7)', 'radial-gradient(ellipse at 5% 85%, #153f4e 0%, transparent 55%), radial-gradient(ellipse at 90% 8%, #463168 0%, transparent 60%), linear-gradient(130deg, #11172a, #182237)'],
        'radius': '22px', 'font': 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'heading': 'inherit', 'tag': 'Atmospheric',
        'geometry': {
            'panel-gap': '10px',
            'panel-radius': '22px',
            'panel-border-width': '1px',
            'panel-line': 'color-mix(in srgb,var(--a-line),transparent 12%)',
            'panel-shadow': '0 0 18px color-mix(in srgb,var(--a-accent),transparent 94%)',
            'navigation-pad': '20px',
            'navigation-art': 'radial-gradient(ellipse at 0 0,color-mix(in srgb,var(--a-accent),transparent 90%),transparent 70%)',
            'conversation-art': 'radial-gradient(ellipse at 100% 0,color-mix(in srgb,var(--a-accent),transparent 96%),transparent 65%)',
            'canvas-art': 'linear-gradient(110deg,color-mix(in srgb,var(--a-accent),transparent 94%),transparent)',
            'composer-radius': '24px',
            'card-radius': '16px',
            'control-radius': '12px',
            'message-radius': '20px',
            'message-gap': '26px',
            'composer-pad': '14px',
            'card-pad': '16px',
            'surface-shadow': '0 3px 14px color-mix(in srgb,var(--a-accent),transparent 94%)',
            'card-line': 'color-mix(in srgb,var(--a-line),transparent 25%)',
        },
    },
    'atelier': {
        'name': 'Atelier', 'description': 'Warm paper, editorial headings and quiet terracotta accents.',
        'light': ['#eee8dc','#fffcf5','#f5efe4','#302b25','#716557','#d9cebc','#954c35','#f4e1d5','#316653','#ac3d3a'],
        'dark': ['#24211d','#302b25','#3a332c','#f5ecdf','#c6b7a4','#51473b','#efaf8e','#523b2d','#94c4a9','#f3a09c'],
        'background': ['none', 'none'], 'radius': '9px',
        'font': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'heading': 'Georgia, "Times New Roman", serif', 'tag': 'Editorial',
        'geometry': {
            'panel-gap': '7px',
            'panel-radius': '10px',
            'panel-border-width': '1px',
            'panel-line': 'var(--a-line)',
            'panel-shadow': 'inset 0 2px 0 color-mix(in srgb,var(--a-line),transparent 65%)',
            'navigation-pad': '22px',
            'navigation-art': 'repeating-linear-gradient(0deg,transparent 0 31px,color-mix(in srgb,var(--a-line),transparent 85%) 31px 32px)',
            'navigation-art-size': '100% 100%',
            'conversation-art': 'linear-gradient(180deg,color-mix(in srgb,var(--a-soft),transparent 45%),transparent 140px)',
            'canvas-art': 'linear-gradient(180deg,var(--a-soft),transparent)',
            'composer-radius': '12px',
            'card-radius': '8px',
            'control-radius': '7px',
            'message-radius': '12px',
            'message-gap': '25px',
            'composer-pad': '13px',
            'card-pad': '15px',
            'surface-shadow': '0 2px 0 color-mix(in srgb,var(--a-line),transparent 50%)',
            'card-line': 'var(--a-line)',
        },
    },
    'graphite': {
        'name': 'Graphite', 'description': 'Precise edges, technical typography and a fine architectural grid.',
        'light': ['#e9ecee','#fbfcfc','#f0f3f4','#202b31','#596a74','#ccd5da','#176179','#deedf2','#286446','#a23845'],
        'dark': ['#14191c','#1e252a','#283238','#e4edf1','#a4b7c1','#40515a','#89d3e8','#223e48','#8bd3a3','#ffa8b2'],
        'background': ['linear-gradient(#788e9912 1px, transparent 1px), linear-gradient(90deg, #788e9912 1px, transparent 1px)', 'linear-gradient(#a4b7c110 1px, transparent 1px), linear-gradient(90deg, #a4b7c110 1px, transparent 1px)'],
        'radius': '5px', 'font': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'heading': '"SFMono-Regular", Consolas, "Liberation Mono", monospace', 'tag': 'Precise',
        'geometry': {
            'panel-gap': '4px',
            'panel-radius': '4px',
            'panel-border-width': '1px',
            'panel-line': 'color-mix(in srgb,var(--a-line),var(--a-muted) 12%)',
            'panel-shadow': 'none',
            'navigation-pad': '12px',
            'navigation-art': 'linear-gradient(color-mix(in srgb,var(--a-line),transparent 75%) 1px,transparent 1px),linear-gradient(90deg,color-mix(in srgb,var(--a-line),transparent 75%) 1px,transparent 1px)',
            'navigation-art-size': '28px 28px',
            'navigation-art-repeat': 'repeat',
            'composer-radius': '8px',
            'card-radius': '6px',
            'control-radius': '5px',
            'message-radius': '8px',
            'message-gap': '22px',
            'composer-pad': '11px',
            'card-pad': '12px',
            'surface-shadow': 'none',
            'card-line': 'color-mix(in srgb,var(--a-line),var(--a-muted) 12%)',
        },
    },
}


def definitions(schema, string):
    content = {'name': string(100), 'css': string(LIMIT), 'description': string(240)}
    target = {'id': string(240), 'expectedFingerprint': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}
    importing = schema({**content, 'fallbackName': string(100),
                        'packageBase64': string(packages.PACKAGE_BASE64_LIMIT)}, [])
    importing['oneOf'] = [{'required': ['css'], 'not': {'required': ['packageBase64']}},
                          {'required': ['packageBase64'], 'not': {'required': ['css']}}]
    return {
        'theme.list': ('List bundled and saved appearances without applying them.', schema()),
        'theme.read': ('Read a bundled or saved appearance, including its current fingerprint.', schema({'id': string(240)}, ['id'])),
        'theme.save': ('Validate and save a portable CSS appearance in the shared library without applying it.',
                       schema(content, ['name', 'css'])),
        'theme.import': ('Import exactly one of portable CSS (1 MB) or a base64 ZIP appearance (4 MB raw, at most 5,333,336 base64 characters) without applying or overwriting an entry. A root appearance.json identifies a ZIP; retains metadata unless supplied.',
                         importing),
        'theme.update': ('Update a saved appearance in place using the fingerprint from theme.read. Bundled appearances are read-only. Does not change applied CSS.',
                         schema({**content, **target}, ['id', 'expectedFingerprint', 'name', 'css'])),
        'theme.delete': ('Delete only a saved library entry using its current fingerprint. Applied CSS remains intact.',
                         schema(target, ['id', 'expectedFingerprint'])),
        'theme.export': ('Export a bundled or saved appearance by id, or the applied appearance when id is omitted. Includes portable metadata; never exports an unsaved draft or preview.',
                         schema({'id': string(240), 'format': {'enum': ['css', 'zip']}}, [])),
    }


def preset(service, identity):
    if identity in RESOURCE_PRESETS:
        return {'id': 'builtin:' + identity, 'source': 'built-in',
                **RESOURCE_PRESETS[identity],
                'css': files('amplifier_web').joinpath('appearances', identity + '.amplifier.css').read_text(encoding='utf-8')}
    if identity == 'default':
        return {'id': 'builtin:default', 'name': 'Amplifier', 'description': 'The original blue and lilac appearance.',
                'css': service.default_theme(), 'source': 'built-in', 'tag': 'Original',
                'palette': {'bg': '#edf1ff', 'surface': '#ffffff', 'ink': '#202e50', 'accent': '#5842df', 'line': '#e4e8f5'},
                'background': 'linear-gradient(135deg, #edf1ff, #dbd2ff)', 'radius': '16px', 'heading': 'inherit'}
    item = PRESETS[identity]
    rules = ['#amp-one{' + ';'.join('--a-' + key + ':' + value for key, value in item['geometry'].items()) + '}']
    for index, mode in enumerate(('light', 'dark')):
        palette = dict(zip(TOKENS, item[mode]))
        declarations = ';'.join('--a-' + key + ':' + value for key, value in palette.items()) + ';--a-radius:' + item['radius']
        rules.append(f'#amp-one[data-theme-scheme="{mode}"]{{{declarations};background-color:var(--a-bg);background-image:{item["background"][index]};background-size:{"28px 28px" if identity == "graphite" else "cover"};font-family:{item["font"]}}}')
    rules.append(f'#amp-one h1,#amp-one h2,#amp-one h3,#amp-one .a-brand{{font-family:{item["heading"]};letter-spacing:{"-.03em" if identity == "atelier" else "-.015em"}}}')
    if identity == 'graphite':
        rules.append('#amp-one .a-bubble,#amp-one .a-soft,#amp-one .a-primary{border-radius:6px}#amp-one .a-brand{font-size:20px}')
    return {'id': 'builtin:' + identity, 'name': item['name'], 'description': item['description'],
            'source': 'built-in', 'css': service.default_theme() + '\n' + '\n'.join(rules),
            'palette': dict(zip(TOKENS, item['light'])), 'background': item['background'][0],
            'radius': item['radius'], 'heading': item['heading'], 'tag': item['tag']}


def folder(service):
    return service.data_dir / 'themes'


@contextmanager
def directory(service, *, create=False, writing=False):
    """Anchor file operations to one real library directory, never a symlink."""
    from .service import AppError
    root = folder(service)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise AppError('Appearance not found.', 404) from None
    except OSError as exc:
        raise AppError('The appearance library must be a real directory, not a link.') from exc
    try:
        if create or writing:
            # Serialize cooperating library writers across host processes.
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor
    finally:
        os.close(descriptor)


def fingerprint(css):
    return hashlib.sha256(css.encode('utf-8')).hexdigest()


def metadata(css):
    """Remove existing portable headers rather than nesting them on each save."""
    meta = {}
    css = css.removeprefix('\ufeff')
    while css.startswith(MARKER):
        header = re.match(re.escape(MARKER) + r'(.*?) \*/(?:\r?\n|$)', css, re.DOTALL)
        if not header:
            break
        try:
            value = json.loads(header[1])
        except (ValueError, TypeError):
            break
        if not isinstance(value, dict):
            break
        # The outer header is authoritative for older, already-nested exports.
        meta = {**value, **meta}
        css = css[header.end():]
    return meta, css


def portable(css, name, description=None):
    from .service import AppError, validate_theme
    validate_theme(css)
    meta, body = metadata(css)
    name = name.strip()
    if not name:
        raise AppError('Give this appearance a name.')
    meta['name'] = name[:100]
    if description is not None:
        meta['description'] = description[:240]
    else:
        meta['description'] = str(meta.get('description') or '')[:240]
    # JSON stays inside a CSS comment, including unusual imported names.
    header = json.dumps(meta, ensure_ascii=True).replace('*/', '*\\/')
    # New package notices can quote HTML examples. Keep them as comment text
    # without changing the canonical bytes of ordinary legacy CSS metadata.
    if '</style' in header.lower():
        header = header.replace('<', '\\u003c')
    content = MARKER + header + ' */\n' + body
    if len(content.encode('utf-8')) > LIMIT:
        raise AppError('Choose an appearance smaller than 1 MB.')
    validate_theme(content)
    return content


def read_file(path, descriptor):
    from .service import AppError, validate_theme
    if path.name.endswith('.zip'):
        raw, package = read_package(path, descriptor)
        return package_row(path, raw, package)
    fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    with os.fdopen(fd, encoding='utf-8', newline='') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise AppError('This appearance is unavailable or exceeds 1 MB.')
        css = stream.read(LIMIT + 1)
    if len(css.encode('utf-8')) > LIMIT:
        raise AppError('Choose an appearance smaller than 1 MB.')
    validate_theme(css)
    meta, _ = metadata(css)
    name, description = meta.get('name'), meta.get('description', 'A saved appearance from your library.')
    return {'id': 'saved:' + path.name, 'name': str(name or path.stem.removesuffix('.amplifier'))[:100],
            'description': str(description)[:240],
            'css': css, 'fingerprint': fingerprint(css), 'source': 'saved', 'tag': 'Your library'}


def read_package_bytes(path, descriptor):
    from .service import AppError
    fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > packages.PACKAGE_LIMIT:
            raise AppError('This appearance is unavailable or exceeds the 4 MB raw ZIP limit.')
        raw = stream.read(packages.PACKAGE_LIMIT + 1)
    if len(raw) > packages.PACKAGE_LIMIT:
        raise AppError('This appearance exceeds the 4 MB raw ZIP limit.')
    return raw


def read_package(path, descriptor):
    raw = read_package_bytes(path, descriptor)
    return raw, packages.load(raw)


def package_metadata(package):
    return {'name': package.manifest['name'], 'description': package.manifest.get('description', ''),
            'source': 'saved', 'tag': 'Your library', **package.summary}


def package_row(path, raw, package):
    return {'id': 'saved:' + path.name, **package_metadata(package),
            'css': package.css, 'fingerprint': hashlib.sha256(raw).hexdigest()}


def package_listing_row(service, path, descriptor):
    """Cache only validated small metadata, never CSS, manifest or asset bodies.

    Each listing still performs one bounded no-follow read and SHA-256 per ZIP.
    Exact bytes, not mtime/size, witness invalidation. Cold/changed bytes get full
    archive/CSS validation; full theme.read always validates and compiles anew.
    Each service retains at most 128 successful metadata summaries until eviction
    or service disposal. Failed validation never populates this derived cache.
    """
    raw = read_package_bytes(path, descriptor)
    digest = hashlib.sha256(raw).hexdigest()
    with _PACKAGE_SUMMARY_LOCK:
        cache = getattr(service, '_appearance_package_summaries', None)
        if cache is None:
            cache = service._appearance_package_summaries = OrderedDict()
        cached = cache.get(digest)
        if cached is not None:
            cache.move_to_end(digest)
            return {'id': 'saved:' + path.name, 'fingerprint': digest, **cached}
    # Compile outside the metadata lock; concurrent cold misses may both validate.
    metadata_row = package_metadata(packages.load(raw))
    with _PACKAGE_SUMMARY_LOCK:
        cache[digest] = metadata_row
        cache.move_to_end(digest)
        while len(cache) > PACKAGE_SUMMARY_CACHE_LIMIT:
            cache.popitem(last=False)
    return {'id': 'saved:' + path.name, 'fingerprint': digest, **metadata_row}


def saved_name(identity):
    from .service import AppError
    name = identity.removeprefix('saved:')
    if (not identity.startswith('saved:') or Path(name).name != name
            or not name.endswith(('.css', '.zip')) or '/' in name or '\\' in name or '\0' in name):
        raise AppError('Choose a saved appearance from the library.', 404)
    return name


def read(service, identity):
    from .service import AppError
    if identity.startswith('builtin:') and identity[8:] in ('default', *PRESETS, *RESOURCE_PRESETS):
        row = preset(service, identity[8:])
        return {**row, 'fingerprint': fingerprint(row['css'])}
    name = saved_name(identity)
    try:
        with directory(service) as descriptor:
            return read_file(Path(name), descriptor)
    except (OSError, UnicodeError) as exc:
        raise AppError('Appearance not found.', 404) from exc


def listing(service):
    from .service import AppError
    rows = [preset(service, key) for key in ('default', *PRESETS, *RESOURCE_PRESETS)]
    errors = []
    root = folder(service)
    if root.exists() or root.is_symlink():
        with directory(service) as descriptor:
            for name in sorted(os.listdir(descriptor), key=str.casefold):
                if not name.endswith(('.css', '.zip')):
                    continue
                try:
                    rows.append(package_listing_row(service, Path(name), descriptor) if name.endswith('.zip')
                                else read_file(Path(name), descriptor))
                except (OSError, UnicodeError, AppError) as exc:
                    errors.append({'name': name, 'message': str(exc)})
    for row in rows:
        css = row.pop('css', None)
        if 'fingerprint' not in row:
            row['fingerprint'] = fingerprint(css)
    return {'items': rows, 'errors': errors, 'directory': str(root)}


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60] or 'appearance'


def publish(descriptor, name, content, *, replace=False):
    from .service import AppError
    temporary = '.' + uuid.uuid4().hex + '.tmp'
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content if isinstance(content, bytes) else content.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        else:
            try:
                os.link(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
            except FileExistsError:
                try:
                    existing = (read_package(Path(name), descriptor)[0] if isinstance(content, bytes)
                                else read_file(Path(name), descriptor)['css'])
                except (OSError, UnicodeError, AppError) as exc:
                    raise AppError('This library file is unavailable. Nothing was replaced.') from exc
                if existing != content:
                    raise AppError('A different appearance already uses this file name.') from None
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass


def summary(service, row):
    return {key: value for key, value in row.items() if key != 'css'} | {'directory': str(folder(service))}


def import_name(descriptor, name, content):
    """An updated entry may still own its old content-addressed file name."""
    from .service import AppError
    extension = '.zip' if isinstance(content, bytes) else '.amplifier.css'
    stem = name.removesuffix(extension)
    candidates = {name if index == 1 else f'{stem}-{index}{extension}': index
                  for index in range(1, 1001)}
    occupied = set()
    # Inspect only existing members of this bounded collision family. A deleted
    # earlier slot must not hide an identical later import or change its identity.
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if (index := candidates.get(entry.name)) is not None:
                occupied.add(index)
    for index in sorted(occupied):
        candidate = name if index == 1 else f'{stem}-{index}{extension}'
        try:
            existing = (read_package(Path(candidate), descriptor)[0] if isinstance(content, bytes)
                        else read_file(Path(candidate), descriptor)['css'])
        except FileNotFoundError:
            occupied.discard(index)
            continue
        except (OSError, UnicodeError, AppError) as exc:
            raise AppError('This library file is unavailable. Nothing was replaced.') from exc
        if existing == content:
            return candidate
    for index in range(1, 1001):
        if index not in occupied:
            return name if index == 1 else f'{stem}-{index}{extension}'
    raise AppError('Too many appearances share this import identity. Choose another name.')


def save(service, args):
    meta, _ = metadata(args['css'])
    name = args.get('name', str(meta.get('name') or args.get('fallbackName') or 'Imported appearance'))[:100]
    content = portable(args['css'], name, args.get('description'))
    name = metadata(content)[0]['name']
    digest = fingerprint(content)[:16]
    filename = f'{slug(name)}-{digest}.amplifier.css'
    with directory(service, create=True) as descriptor:
        filename = import_name(descriptor, filename, content)
        publish(descriptor, filename, content)
        return summary(service, read_file(Path(filename), descriptor))


def import_appearance(service, args):
    from .service import AppError
    if ('css' in args) == ('packageBase64' in args):
        raise AppError('Import exactly one of css or packageBase64.')
    if 'css' in args:
        return save(service, args)
    package = packages.load(packages.decode_input(args['packageBase64']))
    # A ZIP manifest always supplies a name; fallbackName is for metadata-free CSS.
    package = packages.with_metadata(package, args.get('name'), args.get('description'))
    content = packages.encode(package.files)
    filename = f"{slug(package.manifest['name'])}-{hashlib.sha256(content).hexdigest()[:16]}.zip"
    with directory(service, create=True) as descriptor:
        filename = import_name(descriptor, filename, content)
        publish(descriptor, filename, content)
        return summary(service, package_row(Path(filename), content, package))


def mutate(service, args, *, deleting=False):
    from .service import AppError
    filename = saved_name(args['id'])
    with directory(service, writing=True) as descriptor:
        try:
            package = None
            if filename.endswith('.zip'):
                raw, package = read_package(Path(filename), descriptor)
                previous = package_row(Path(filename), raw, package)
            else:
                previous = read_file(Path(filename), descriptor)
        except (OSError, UnicodeError) as exc:
            raise AppError('Appearance not found.', 404) from exc
        if previous['fingerprint'] != args['expectedFingerprint']:
            raise AppError('This appearance changed. Read it again before updating or deleting it.', 409)
        if deleting:
            os.unlink(filename, dir_fd=descriptor)
            os.fsync(descriptor)
            return {'id': args['id'], 'deleted': True, 'appliedAppearanceUnchanged': True}
        if package is not None:
            updated = packages.with_metadata(package, args['name'],
                                             args.get('description', previous['description']), args['css'])
            content = packages.encode(updated.files)
        else:
            content = portable(args['css'], args['name'], args.get('description', previous['description']))
        publish(descriptor, filename, content, replace=True)
        return summary(service, read_file(Path(filename), descriptor))


def export(service, identity=None, format='css'):
    from .service import AppError
    if format not in ('css', 'zip'):
        raise AppError('Choose css or zip for an appearance export.')
    if format == 'zip' and identity and identity.startswith('saved:') and saved_name(identity).endswith('.zip'):
        try:
            with directory(service) as descriptor:
                raw, package = read_package(Path(saved_name(identity)), descriptor)
        except (OSError, UnicodeError) as exc:
            raise AppError('Appearance not found.', 404) from exc
        return zip_export(identity, package.manifest['name'], raw)
    if identity:
        row = read(service, identity)
    else:
        row = service.state['theme']
    # Preserve the established no-id export: exact committed CSS, not a preview.
    css = portable(row['css'], row['name'], row.get('description')) if identity else row['css']
    if format == 'zip':
        package = packages.from_css(css, row['name'], row.get('description'))
        return zip_export(identity, row['name'], packages.encode(package.files))
    return {'id': identity, 'name': row['name'], 'css': css,
            'fingerprint': fingerprint(css), 'filename': slug(row['name']) + '.amplifier.css'}


def zip_export(identity, name, raw):
    import base64
    return {'id': identity, 'name': name, 'format': 'zip', 'filename': slug(name) + '.zip',
            'mimeType': 'application/zip', 'base64': base64.b64encode(raw).decode('ascii'),
            'fingerprint': hashlib.sha256(raw).hexdigest()}
