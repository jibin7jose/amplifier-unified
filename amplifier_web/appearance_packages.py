"""Bounded, non-executable ZIP appearances. Archives are never extracted.

The ZIP is canonical library storage; compiled CSS is a bounded runtime view.
Unused declared assets may exceed the runtime limit but still travel on export.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import io
import json
import posixpath
import stat
import struct
import unicodedata
import zipfile
import zlib

import tinycss2

CSS_LIMIT = 1_000_000
PACKAGE_LIMIT = 4_000_000
PACKAGE_BASE64_LIMIT = ((PACKAGE_LIMIT + 2) // 3) * 4
TOTAL_LIMIT = 8_000_000
MEMBER_LIMIT = 2_000_000
MEMBER_COUNT = 128
COMPRESSION_RATIO = 200
NOTICE_KEY = 'packageNotices'
MEDIA = {
    'image/png': ('.png',),
    'image/jpeg': ('.jpg', '.jpeg'),
    'image/webp': ('.webp',),
    'font/woff': ('.woff',),
    'font/woff2': ('.woff2',),
}

# Only explicit literal image constructors/annotations and numeric/color
# expressions are admitted inside URL-bearing image functions. Unknown future
# substitutions fail closed rather than hiding URL strings from the visitor.
LITERAL_IMAGE_FUNCTIONS = frozenset({
    'url', 'image', 'image-set', '-webkit-image-set', 'src', 'type', 'format',
    'linear-gradient', 'radial-gradient', 'conic-gradient',
    'repeating-linear-gradient', 'repeating-radial-gradient', 'repeating-conic-gradient',
    'rgb', 'rgba', 'hsl', 'hsla', 'hwb', 'lab', 'lch', 'oklab', 'oklch',
    'color', 'color-mix', 'light-dark', 'calc', 'min', 'max', 'clamp',
})


def literal_image_functions(node, depth=0):
    """Reject substitutions at any depth within a URL-bearing image function."""
    from .errors import AppError
    if depth > 64:
        raise AppError('Image function nesting exceeds 64 levels.')
    for argument in node.arguments:
        if argument.type == 'function':
            if argument.lower_name not in LITERAL_IMAGE_FUNCTIONS:
                raise AppError('Use literal assets in image functions; unsupported substitutions are not allowed.')
            literal_image_functions(argument, depth + 1)
        elif argument.type in ('() block', '[] block', '{} block'):
            raise AppError('Use literal assets in image functions, not substitution blocks.')


def reject(message):
    from .errors import AppError
    raise AppError('Invalid appearance package: ' + message)


def path_name(value):
    if isinstance(value, str):
        try:
            value.encode('utf-8')
        except UnicodeError:
            reject('archive paths must be UTF-8 text.')
    if (not isinstance(value, str) or not value or len(value) > 240
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in value for c in '\\:%?#')
            or any(part in ('', '.', '..') for part in value.split('/'))
            or unicodedata.normalize('NFC', value) != value):
        reject('use canonical relative archive paths without traversal or URL syntax.')
    return value


def text(value, label, limit, *, empty=False):
    if (not isinstance(value, str) or (not empty and not value.strip())
            or len(value) > limit or '\0' in value):
        reject(f'{label} must be bounded UTF-8 text.')
    try:
        value.encode('utf-8')
    except UnicodeError:
        reject(f'{label} must be UTF-8 text.')
    return value


def license_text(value):
    text(value, 'license notice', MEMBER_LIMIT)
    if (len(value.encode('utf-8')) > MEMBER_LIMIT
            or any(ord(c) < 32 and c not in '\t\r\n' for c in value)):
        reject('a license notice exceeds 2 MB.')
    return value


def license_path(value):
    value = path_name(value)
    if not value.startswith('licenses/') or not value.endswith('.txt'):
        reject('license files must be .txt files under licenses/.')
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            reject('JSON contains a duplicate key.')
        result[key] = value
    return result


def manifest_value(raw):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                           parse_constant=lambda _: reject('JSON constants must be standard JSON.'))
    except (ValueError, UnicodeError, RecursionError):
        reject('appearance.json must be UTF-8 JSON without a BOM.')
    if not isinstance(value, dict):
        reject('appearance.json must be an object.')
    required = {'type', 'formatVersion', 'name', 'stylesheet'}
    if not required <= value.keys() or value.keys() - required - {'description', 'author', 'assets', 'licenses'}:
        reject('appearance.json contains missing or unsupported fields.')
    if value['type'] != 'appearance':
        reject('the manifest type must be appearance.')
    if type(value['formatVersion']) is not int or value['formatVersion'] != 1:
        reject('unsupported formatVersion; only version 1 is supported.')
    text(value['name'], 'name', 100)
    for key, limit in (('description', 240), ('author', 240)):
        if key in value:
            text(value[key], key, limit, empty=key == 'description')
    stylesheet = path_name(value['stylesheet'])
    if not stylesheet.endswith('.css') or stylesheet.startswith(('assets/', 'licenses/')):
        reject('stylesheet must name a .css file outside assets/ and licenses/.')
    assets, licenses = value.get('assets', []), value.get('licenses', [])
    if not isinstance(assets, list) or not isinstance(licenses, list):
        reject('assets and licenses must be arrays.')
    if len(assets) + len(licenses) + 2 > MEMBER_COUNT:
        reject('an appearance supports at most 128 archive members.')
    used = {'appearance.json'.casefold(), stylesheet.casefold()}
    for name in licenses:
        name = license_path(name)
        if name.casefold() in used:
            reject('manifest paths must be unique, including case.')
        used.add(name.casefold())
    for asset in assets:
        if (not isinstance(asset, dict) or not {'path', 'mediaType'} <= asset.keys()
                or asset.keys() - {'path', 'mediaType', 'license'}):
            reject('each asset requires path and mediaType, with an optional license.')
        name = path_name(asset['path'])
        media = asset['mediaType']
        if (not name.startswith('assets/') or not isinstance(media, str)
                or media not in MEDIA or not name.lower().endswith(MEDIA[media])):
            reject('assets must declare PNG, JPEG, WebP, WOFF or WOFF2 and a matching extension.')
        if name.casefold() in used:
            reject('manifest paths must be unique, including case.')
        used.add(name.casefold())
        if 'license' in asset and license_path(asset['license']) not in licenses:
            reject('asset license must reference a declared license file.')
        if media.startswith('font/') and 'license' not in asset:
            reject('each font asset requires a declared license file.')
    return value


def check_asset(data, media):
    valid = False
    if media == 'image/png':
        valid = len(data) >= 24 and data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR'
    elif media == 'image/jpeg':
        valid = len(data) >= 4 and data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9')
    elif media == 'image/webp':
        valid = (len(data) >= 16 and data[:4] == b'RIFF' and data[8:12] == b'WEBP'
                 and struct.unpack('<I', data[4:8])[0] + 8 == len(data))
    elif media in ('font/woff', 'font/woff2'):
        valid = (len(data) >= (44 if media == 'font/woff' else 48)
                 and data[:4] == (b'wOFF' if media == 'font/woff' else b'wOF2')
                 and struct.unpack('>I', data[8:12])[0] == len(data))
    if not valid:
        reject('an asset does not match its declared media type/signature.')


def decode_input(value):
    if not isinstance(value, str) or not value or len(value) > PACKAGE_BASE64_LIMIT:
        reject(f'packageBase64 must be at most {PACKAGE_BASE64_LIMIT:,} base64 characters (4 MB ZIP).')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        reject('packageBase64 must be strict base64.')
    if len(raw) > PACKAGE_LIMIT:
        reject('the decoded ZIP package exceeds 4 MB.')
    return raw


def member_spans(raw, members, central_start):
    """Validate local records against central metadata before decoding any data.

    These bounded packages use ordinary ZIP32 records. Data descriptors are
    supported, but gaps, overlaps, ZIP64, and mismatched local metadata are not.
    """
    ordered = sorted(members, key=lambda member: member.header_offset)
    if not ordered or not 0 < central_start <= len(raw):
        reject('the ZIP has no valid local member records.')
    spans, cursor = [], 0
    for index, member in enumerate(ordered):
        offset = member.header_offset
        boundary = ordered[index + 1].header_offset if index + 1 < len(ordered) else central_start
        if offset != cursor or offset < 0 or offset + 30 > boundary or boundary > central_start:
            reject('local ZIP records overlap, are truncated, or contain undeclared gaps.')
        (signature, version, flags, method, _time, _date, crc, compressed, expanded,
         name_size, extra_size) = struct.unpack_from('<4s5H3I2H', raw, offset)
        start = offset + 30 + name_size + extra_size
        end = start + member.compress_size
        if (signature != b'PK\x03\x04' or version != member.extract_version or version > 20
                or flags != member.flag_bits or flags & ~0x080e or method != member.compress_type
                or start > boundary or end > boundary):
            reject('local ZIP header metadata is unsupported, inconsistent or truncated.')
        expected_name = member.orig_filename.encode('utf-8' if flags & 0x800 else 'cp437')
        if raw[offset + 30:offset + 30 + name_size] != expected_name:
            reject('local and central ZIP names differ.')
        expected = (member.CRC, member.compress_size, member.file_size)
        if flags & 8:
            if any(local not in (0, actual) for local, actual in zip((crc, compressed, expanded), expected)):
                reject('local ZIP sizes/checksum conflict with the central directory.')
            descriptor = raw[end:boundary]
            if len(descriptor) == 16 and descriptor[:4] == b'PK\x07\x08':
                descriptor = descriptor[4:]
            if len(descriptor) != 12 or struct.unpack('<3I', descriptor) != expected:
                reject('ZIP data descriptor is missing or inconsistent.')
        elif (crc, compressed, expanded) != expected or end != boundary:
            reject('local ZIP sizes/checksum conflict or member data has an undeclared gap.')
        if method == zipfile.ZIP_STORED and member.compress_size != member.file_size:
            reject('STORED member compressed and expanded lengths must agree.')
        spans.append((member, start, end))
        cursor = boundary
    return spans


def decode_member(raw, member, start, end, remaining):
    """Read true STORED/DEFLATE output, independent of ZipExtFile's size clipping."""
    limit = min(MEMBER_LIMIT, member.file_size, remaining,
                max(1, member.compress_size) * COMPRESSION_RATIO)
    data = memoryview(raw)[start:end]
    if member.compress_type == zipfile.ZIP_STORED:
        body = bytes(data)
    else:
        decoder = zlib.decompressobj(-15)
        body = decoder.decompress(data, limit + 1)
        if len(body) > limit or decoder.unconsumed_tail:
            reject('expanded archive contents exceed their bounds.')
        if not decoder.eof or decoder.unused_data:
            reject('DEFLATE stream is truncated or contains trailing data.')
    if len(body) > limit or len(body) != member.file_size:
        reject('actual expanded member length does not match its declared bounds.')
    if zlib.crc32(body) & 0xffffffff != member.CRC:
        reject('the ZIP member has an invalid checksum.')
    return body


def archive_files(raw):
    if not raw or len(raw) > PACKAGE_LIMIT or not raw.startswith(b'PK\x03\x04'):
        reject('choose a ZIP appearance within the 4 MB raw ZIP limit.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > MEMBER_COUNT:
                reject('an appearance supports at most 128 archive members.')
            seen, directories, files = set(), set(), {}
            total = 0
            # Validate ALL advertised bounds and types before opening any member.
            for member in members:
                original = member.orig_filename
                is_dir = original.endswith('/')
                name = path_name(original[:-1] if is_dir else original)
                kind = stat.S_IFMT(member.external_attr >> 16)
                if (name.casefold() in seen or member.flag_bits & (1 | 64)
                        or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or kind not in (0, stat.S_IFDIR if is_dir else stat.S_IFREG)
                        or (not is_dir and member.external_attr & 0x10)):
                    reject('duplicate paths, links, special files, encryption or unsupported compression.')
                seen.add(name.casefold())
                if (member.file_size > MEMBER_LIMIT or member.file_size < 0
                        or member.file_size > max(1, member.compress_size) * COMPRESSION_RATIO):
                    reject('an archive member exceeds 2 MB or the compression ratio limit.')
                total += member.file_size
                if total > TOTAL_LIMIT:
                    reject('uncompressed archive contents exceed 8 MB.')
                if is_dir and member.file_size:
                    reject('directory entries must be empty.')
            spans = member_spans(raw, members, archive.start_dir)
            actual_total = 0
            for member, start, end in spans:
                body = decode_member(raw, member, start, end, TOTAL_LIMIT - actual_total)
                actual_total += len(body)
                if member.is_dir():
                    directories.add(member.filename[:-1])
                    continue
                files[member.filename] = body
            # Directory records are optional, but cannot smuggle unrelated contents.
            parents = {name[:index] for name in files for index, char in enumerate(name) if char == '/'}
            if not directories <= parents:
                reject('the archive contains an undeclared directory.')
            return files
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError,
            EOFError, OSError, ValueError, struct.error, zlib.error) as exc:
        reject('the ZIP is malformed or has an invalid checksum: ' + str(exc)[:120])


def notices(css):
    from .theme_library import metadata
    value = metadata(css)[0].get(NOTICE_KEY, {})
    if not isinstance(value, dict) or len(value) > MEMBER_COUNT - 2:
        reject('portable license notices must be a bounded object.')
    paths = set()
    for path, body in value.items():
        license_path(path)
        license_text(body)
        if path.casefold() in paths:
            reject('portable license notice paths must be unique, including case.')
        paths.add(path.casefold())
    return value


def merge_notices(*sources):
    """Merge validated notice maps without replacing existing attribution."""
    retained = {}
    paths = {}
    for source in sources:
        for name, notice in source.items():
            if name in retained and retained[name] != notice:
                reject('conflicting license notice bodies for ' + name)
            if name.casefold() in paths and paths[name.casefold()] != name:
                reject('license notice paths conflict by case.')
            retained[name] = notice
            paths[name.casefold()] = name
    if len(retained) > MEMBER_COUNT - 2:
        reject('too many retained license notices for a portable appearance.')
    return retained


def stylesheet_metadata(css):
    from .theme_library import metadata
    if not css.strip():
        reject('the stylesheet is empty.')
    if len(css.encode('utf-8')) > CSS_LIMIT:
        reject('the source stylesheet exceeds the 1 MB runtime limit.')
    if '</style' in css.lower():
        reject('a stylesheet must contain CSS only.')
    return metadata(css)


def compile_css(css, manifest, files):
    """Rewrite URL tokens/URL-bearing string forms, not text or regex matches."""
    from .service import validate_theme
    from .theme_library import portable, MARKER
    meta, body = stylesheet_metadata(css)
    try:
        rules = tinycss2.parse_stylesheet(body, skip_comments=False, skip_whitespace=False)
    except RecursionError:
        reject('CSS nesting exceeds the parser limit.')
    assets = {asset['path']: asset['mediaType'] for asset in manifest.get('assets', [])}
    growth, urls = len(css.encode('utf-8')), {}

    def resolve(value):
        nonlocal growth
        if value.startswith('data:'):
            try:
                header, encoded = value.split(',', 1)
                media = header.removeprefix('data:').removesuffix(';base64')
                if media not in MEDIA or header != f'data:{media};base64':
                    reject('only embedded PNG, JPEG, WebP, WOFF or WOFF2 are supported.')
                check_asset(base64.b64decode(encoded, validate=True), media)
            except (ValueError, binascii.Error):
                reject('embedded assets must use valid base64.')
            return value
        # Resolve a relative URL against the stylesheet. No schemes, query strings,
        # percent aliases, backslashes or absolute paths can enter the lookup.
        if (not value or value.startswith('/') or any(c in value for c in '\\:%?#')
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            reject('CSS references must name declared relative assets.')
        path = posixpath.normpath(posixpath.join(posixpath.dirname(manifest['stylesheet']), value))
        if path not in assets:
            reject('CSS references an undeclared asset: ' + value[:120])
        length = len(assets[path]) + 13 + ((len(files[path]) + 2) // 3 * 4)
        growth += max(0, length - len(value.encode('utf-8')))
        if growth > CSS_LIMIT:
            reject('compiled CSS exceeds the 1 MB runtime limit.')
        if path not in urls:
            urls[path] = f'data:{assets[path]};base64,' + base64.b64encode(files[path]).decode('ascii')
        return urls[path]

    def visit(nodes, depth=0):
        if depth > 64:
            reject('CSS nesting exceeds 64 levels.')
        for index, node in enumerate(nodes):
            if node.type == 'error':
                reject('the stylesheet contains invalid CSS: ' + node.message)
            if ((node.type == 'at-rule' and node.lower_at_keyword in {'import', 'namespace', 'document', 'charset'})
                    or (node.type == 'at-keyword' and node.value.lower() in {'import', 'namespace', 'document', 'charset'})):
                reject('CSS imports and external at-rules are unsupported.')
            if node.type == 'url':
                value = resolve(node.value)
                nodes[index] = tinycss2.parse_component_value_list('url("' + value + '")')[0]
                continue
            if node.type == 'function':
                if node.lower_name in {'expression', '-moz-binding', 'paint'}:
                    reject('executable CSS functions are unsupported.')
                if node.lower_name == 'url':
                    args = [arg for arg in node.arguments if arg.type not in ('whitespace', 'comment')]
                    if len(args) != 1 or args[0].type != 'string':
                        reject('url() must contain one literal asset reference.')
                    value = resolve(args[0].value)
                    node.arguments = tinycss2.parse_component_value_list('"' + value + '"')
                    continue
                if node.lower_name in {'image-set', '-webkit-image-set', 'image', 'src'}:
                    literal_image_functions(node)
                    for i, argument in enumerate(node.arguments):
                        if argument.type == 'string':
                            value = resolve(argument.value)
                            node.arguments[i] = tinycss2.parse_component_value_list('"' + value + '"')[0]
            for attr in ('prelude', 'content', 'arguments'):
                children = getattr(node, attr, None)
                if children:
                    visit(children, depth + 1)
    visit(rules)
    retained = merge_notices(notices(css), {
        name: files[name].decode('utf-8') for name in manifest.get('licenses', [])
    })
    if sum(len(body.encode('utf-8')) for body in retained.values()) > CSS_LIMIT:
        reject('compiled CSS including license notices exceeds the 1 MB runtime limit.')
    if retained:
        meta[NOTICE_KEY] = retained
    if 'author' in manifest:
        meta['author'] = manifest['author']
    header = MARKER + json.dumps(meta, ensure_ascii=True).replace('*/', '*\\/').replace('<', '\\u003c') + ' */\n'
    compiled = header + tinycss2.serialize(rules)
    if len(compiled.encode('utf-8')) > CSS_LIMIT:
        reject('compiled CSS including license notices exceeds the 1 MB runtime limit.')
    validate_theme(compiled)
    return portable(compiled, manifest['name'], manifest.get('description', ''))


@dataclass(frozen=True)
class Package:
    manifest: dict
    files: dict
    css: str

    @property
    def summary(self):
        return {'format': 'zip', 'packageVersion': 1,
                'assetCount': len(self.manifest.get('assets', [])),
                'licenseCount': len(self.manifest.get('licenses', []))}


def validate_files(files):
    if (len(files) > MEMBER_COUNT or any(len(data) > MEMBER_LIMIT for data in files.values())
            or sum(map(len, files.values())) > TOTAL_LIMIT):
        reject('package contents exceed archive bounds.')
    if 'appearance.json' not in files:
        reject('a root appearance.json manifest is required.')
    manifest = manifest_value(files['appearance.json'])
    declared = {'appearance.json', manifest['stylesheet'], *manifest.get('licenses', []),
                *(asset['path'] for asset in manifest.get('assets', []))}
    if set(files) != declared:
        reject('every archive file must be declared, with no missing files.')
    for asset in manifest.get('assets', []):
        check_asset(files[asset['path']], asset['mediaType'])
    try:
        css = files[manifest['stylesheet']].decode('utf-8').removeprefix('\ufeff')
        for name in manifest.get('licenses', []):
            license_text(files[name].decode('utf-8'))
    except UnicodeError:
        reject('stylesheets and license notices must be UTF-8.')
    return Package(manifest, files, compile_css(css, manifest, files))


def load(raw):
    return validate_files(archive_files(raw))


def encode(files):
    """Deterministic ZIP bytes, with bounded DEFLATE and no container metadata."""
    if len(files) > MEMBER_COUNT or sum(map(len, files.values())) > TOTAL_LIMIT:
        reject('package contents exceed archive bounds.')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in sorted(files.items()):
            if len(data) > MEMBER_LIMIT:
                reject('an archive member exceeds 2 MB.')
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            # Highly repetitive CSS must not create exports our own reader rejects.
            compressed = len(zlib.compress(data))
            entry.compress_type = (zipfile.ZIP_DEFLATED if len(data) <= max(1, compressed - 6) * COMPRESSION_RATIO
                                   else zipfile.ZIP_STORED)
            archive.writestr(entry, data)
    raw = output.getvalue()
    if len(raw) > PACKAGE_LIMIT:
        reject('the package exceeds the 4 MB raw ZIP limit.')
    return raw


def with_metadata(package, name=None, description=None, css=None):
    from .theme_library import metadata, MARKER
    manifest, files = dict(package.manifest), dict(package.files)
    if name is not None:
        manifest['name'] = name.strip()
    if description is not None:
        manifest['description'] = description
    if css is not None:
        meta, body = stylesheet_metadata(css)
        previous, _ = metadata(package.css)
        retained = merge_notices(notices(package.css), notices(css))
        if retained:
            meta[NOTICE_KEY] = retained
        if 'author' not in meta and 'author' in previous:
            meta['author'] = previous['author']
        # Preserve attribution carried only by the validated portable header,
        # not just the license files declared in the original manifest.
        header = MARKER + json.dumps(meta, ensure_ascii=True).replace('*/', '*\\/').replace('<', '\\u003c') + ' */\n'
        css = header + body
        files[manifest['stylesheet']] = css.encode('utf-8')
    files['appearance.json'] = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return validate_files(files)


def from_css(css, name, description=None):
    """A CSS-only package keeps embedded assets and full portable license notices."""
    from .theme_library import metadata, portable
    css = portable(css, name, description)
    meta, _ = metadata(css)
    retained = notices(css)
    manifest = {'type': 'appearance', 'formatVersion': 1, 'name': meta['name'],
                'description': meta.get('description', ''), 'stylesheet': 'theme.css',
                'assets': [], 'licenses': sorted(retained)}
    if 'author' in meta:
        manifest['author'] = meta['author']
    files = {'theme.css': css.encode('utf-8'),
             **{path: body.encode('utf-8') for path, body in retained.items()}}
    files['appearance.json'] = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return validate_files(files)