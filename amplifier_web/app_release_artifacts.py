"""Verified release inputs owned by an existing application update attempt.

Source stamps describe source, not publication. Only the updater's verified
download context can qualify an installed local wheel as official delivery.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
import zipfile

REPOSITORY = 'microsoft/amplifier-unified'
SOURCE = 'https://github.com/' + REPOSITORY
STAMP = 'amplifier_web/release-source.json'
STAMP_LIMIT = 16_384
CHECKSUM_LIMIT = 65_536


def stable_version(value):
    value = value.removeprefix('v') if isinstance(value, str) else ''
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Choose a stable application release')
    return value


def filename(version):
    return 'amplifier_unified-' + stable_version(version) + '-py3-none-any.whl'


def validate_stamp(value, version, revision=None):
    """Validate source facts, including fork records; this grants no eligibility."""
    if (not isinstance(value, dict) or set(value) != {'schema', 'repository', 'revision', 'version'}
            or type(value.get('schema')) is not int or value['schema'] != 1
            or not isinstance(value.get('repository'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value['repository'])
            or not isinstance(value.get('revision'), str)
            or not re.fullmatch('[a-f0-9]{40}', value['revision'])
            or value.get('version') != stable_version(version)
            or revision is not None and value['revision'] != revision):
        raise ValueError('Application source record does not match the selected release')
    return value


def validate_delivery(value, version):
    url = SOURCE + '/releases/download/v' + stable_version(version) + '/' + filename(version)
    if (not isinstance(value, dict) or set(value) != {'url', 'sha256'}
            or value.get('url') != url or not isinstance(value.get('sha256'), str)
            or not re.fullmatch('[a-f0-9]{64}', value['sha256'])):
        raise ValueError('Invalid official application artifact')
    return {'url': url, 'sha256': value['sha256']}


def validate_descriptor(value, version):
    if not isinstance(value, dict) or set(value) != {'url', 'sha256', 'path'}:
        raise ValueError('Invalid application artifact receipt')
    delivery = validate_delivery({key: value[key] for key in ('url', 'sha256')}, version)
    path = value.get('path')
    if (not isinstance(path, str) or not Path(path).is_absolute()
            or '..' in Path(path).parts or Path(path).name != filename(version)):
        raise ValueError('Invalid application artifact path')
    return {**delivery, 'path': path}


def _stamp(distribution):
    try:
        with Path(distribution.locate_file(STAMP)).open('rb') as stream:
            raw = stream.read(STAMP_LIMIT + 1)
        return json.loads(raw) if len(raw) <= STAMP_LIMIT else None
    except (OSError, ValueError, AttributeError):
        return None


def distribution_row(distribution):
    row = {'name': distribution.metadata['Name'], 'version': distribution.version,
           'direct': json.loads(distribution.read_text('direct_url.json') or 'null')}
    direct = row['direct']
    vcs = direct.get('vcs_info', {}) if isinstance(direct, dict) else {}
    revision = vcs.get('commit_id') if isinstance(vcs, dict) else None
    if (re.sub(r'[-_.]+', '-', row['name'].lower()) == 'amplifier-unified'
            and not (isinstance(revision, str) and re.fullmatch('[a-f0-9]{40}', revision))):
        row['stamp'] = _stamp(distribution)
    return row


def source_revision(distribution):
    """Observed source fact only, with the legacy VCS interpretation unchanged."""
    try:
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
        revision = direct.get('vcs_info', {}).get('commit_id')
        if isinstance(revision, str) and re.fullmatch('[a-f0-9]{40}', revision):
            return revision
        return validate_stamp(_stamp(distribution), distribution.version)['revision']
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _verify_archive(path, value, version, revision):
    try:
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != value['sha256']:
                raise ValueError('Application release checksum verification failed')
        with zipfile.ZipFile(path) as archive:
            if archive.namelist().count(STAMP) != 1 or archive.getinfo(STAMP).file_size > STAMP_LIMIT:
                raise ValueError('Application release source record is missing or invalid')
            stamp = validate_stamp(json.loads(archive.read(STAMP)), version, revision)
            if stamp['repository'] != REPOSITORY:
                raise ValueError('Application release source repository does not match')
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeError) as error:
        raise ValueError('Application release artifact could not be verified') from error


def verify(descriptor, version, revision):
    """Recheck retained bytes before a later installation; never consult latest."""
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('Application release requires an exact source revision')
    value = validate_descriptor(descriptor, version)
    _verify_archive(Path(value['path']), value, version, revision)
    return value


def requirement(descriptor, extras=()):
    """The caller owns extra selection; uv receives the verified local file."""
    source = Path(descriptor['path']).as_uri()
    return 'amplifier-unified[' + ','.join(extras) + '] @ ' + source if extras else source


@asynccontextmanager
async def _get(client, url):
    """Follow release-asset redirects without permitting an HTTP downgrade."""
    for _ in range(6):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Application release requires HTTPS delivery')
        async with client.stream('GET', url, follow_redirects=False) as response:
            if response.status_code not in {301, 302, 303, 307, 308}:
                yield response
                return
            location = response.headers.get('Location')
            if not location:
                break
            url = urljoin(url, location)
    raise ValueError('Application release download redirected too many times')


async def prepare(folder, version, revision, *, delivery=None):
    # Source/graph readers stay standard-library-only, including old targets.
    import httpx

    version = stable_version(version)
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('Application release requires an exact source revision')
    folder = Path(folder).resolve()
    target = folder / filename(version)
    part = target.with_suffix(target.suffix + '.part')
    # The generation owns this name. Do not overwrite another attempt's input.
    if target.exists() or part.exists():
        raise ValueError('Application artifact path is already in use')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    owned_part = False
    try:
        timeout = httpx.Timeout(120, connect=15, read=30)
        # Match github_read's public-first transport and configured proxy/CA
        # policy. httpx does not implicitly add netrc authentication.
        async with asyncio.timeout(120), httpx.AsyncClient(timeout=timeout) as client:
            if delivery is None:
                base = SOURCE + '/releases/download/v' + version + '/'
                async with _get(client, base + 'SHA256SUMS') as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes(8192):
                        data.extend(chunk)
                        if len(data) > CHECKSUM_LIMIT:
                            raise ValueError('Application checksum manifest is too large')
                matches = [row[0] for line in data.decode('utf-8').splitlines()
                           if len(row := line.split()) == 2 and row[1].lstrip('*') == target.name]
                if len(matches) != 1:
                    raise ValueError('Application checksum manifest has no unique wheel entry')
                delivery = {'url': base + target.name, 'sha256': matches[0]}
            delivery = validate_delivery(delivery, version)
            async with _get(client, delivery['url']) as response:
                response.raise_for_status()
                with part.open('xb') as stream:
                    owned_part = True
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        stream.write(chunk)
        # The installer sees the completed name only after checksum/source proof.
        verification = asyncio.create_task(asyncio.to_thread(_verify_archive, part, delivery, version, revision))
        try:
            await asyncio.shield(verification)
        except asyncio.CancelledError:
            # A read-only verifier owns the file until it settles. In particular,
            # Windows cannot remove the partial input while that read is open.
            try:
                await verification
            except Exception:
                pass
            raise
        part.rename(target)
        owned_part = False
        return {**delivery, 'path': str(target)}
    except (httpx.HTTPError, asyncio.TimeoutError, OSError, UnicodeError) as error:
        raise ValueError('Application release download failed; the running app is unchanged') from error
    finally:
        if owned_part:
            part.unlink(missing_ok=True)
