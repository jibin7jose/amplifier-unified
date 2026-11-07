"""Explicit request-authorized file consumption, not a second artifact store."""
import copy
import hashlib
import os
import ntpath
from pathlib import Path, PureWindowsPath
import stat
import sys
import uuid

from . import retained_files, result_artifacts, test_origin_files
from .resource_files import settle_worker


def destination(root, value):
    # One portable spelling. In particular reject Windows ADS, devices, drive
    # relative names and trailing-dot/space aliases even on POSIX hosts.
    parts = value.split('/')
    if (not value or Path(value).is_absolute() or PureWindowsPath(value).drive
            or any(not part or part in {'.', '..'} or part.casefold() == '.git'
                   or part.endswith((' ', '.')) or ntpath.isreserved(part)
                   or any(ord(c) < 32 or c in '\\:<>"|?*' for c in part) for part in parts)):
        raise ValueError('Choose a relative file path inside your current working folder, using / separators.')
    return Path(root).absolute().joinpath(*parts)


def identity(info):
    return tuple(getattr(info, key) for key in retained_files.EVIDENCE)


def regular(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or os.name != 'nt' and info.st_uid != os.getuid()):
        raise ValueError('The destination must be an owned regular file, not a link or alias.')


def existing(parent, name, record):
    try:
        before = parent.stat(name)
    except FileNotFoundError:
        return None
    regular(before)
    with parent.open(name, private=os.name == 'nt') as stream:
        opened = os.fstat(stream.fileno())
        regular(opened)
        if identity(before) != identity(opened) or opened.st_size != record['bytes']:
            raise ValueError('Destination already exists with different bytes or changed identity; nothing was overwritten.')
        digest, remaining = hashlib.sha256(), record['bytes']
        while remaining:
            block = stream.read(min(remaining, retained_files.CHUNK))
            if not block:
                raise ValueError('The existing destination was truncated during verification.')
            digest.update(block)
            remaining -= len(block)
        if (stream.read(1) or digest.hexdigest() != record['sha256'] or identity(opened) != identity(os.fstat(stream.fileno()))
                or identity(opened) != identity(parent.stat(name))):
            raise ValueError('Destination already exists with different bytes or changed identity; nothing was overwritten.')
    parent.assert_current()
    return identity(opened)


def write_verified(stream, sources, record, data):
    if sources is not None:
        retained_files.chunks_verified(sources, record, stream.write)
    else:
        if data is None or len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('Retained file content no longer matches its evidence hash.')
        stream.write(data)
    stream.flush()
    os.fsync(stream.fileno())
    info = os.fstat(stream.fileno())
    regular(info)
    return identity(info)


def cleanup(parent, name, owned, error):
    """Clean exclusive random staging; never call this on the public path."""
    try:
        info = parent.stat(name)
        if (info.st_dev, info.st_ino) != owned[:2]:
            raise ValueError('The owned path was replaced; its replacement was preserved.')
        parent.unlink(name)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        detail = f'Owned artifact cleanup failed at {parent.path / name}: {exc}'
        if error is None:
            raise ValueError(detail) from exc
        error.add_note(detail)
        # AppError transports its message, not Python traceback notes.
        error.args = (str(error) + ' ' + detail,)


def publication_warning(error, target):
    detail = f'Publication may have occurred at {target}; inspect it before retrying. No public file was removed.'
    error.add_note(detail)
    error.args = (str(error) + ' ' + detail,)


async def save(collaboration, source, args, origin, caller, *, admission_check=None):
    """Stage outside the app lock; recheck authority before atomic no-clobber link.

    No durable exactly-once promise: after a lost return, inspect the destination.
    An explicit repeat verifies identical existing bytes without transferring them.
    """
    from .errors import AppError
    from .task_resources import access
    from .workspace_work_effects import guard as work_guard

    app, sid = collaboration.service, source['id']
    if sid != args['sessionId']:
        collaboration.error('Save a retained result into the calling conversation only.')
    captured = result_artifacts.fence(source, {})
    ownership = copy.deepcopy(source.get('ownership'))
    root = source.get('workingDirectory') or source['workspace']

    def resolve():
        return result_artifacts.resolve(collaboration, sid, args['requestId'], args['reference'],
                                        args['responseRevision'], scope=args.get('scope', 'workspace'))

    def guard():
        if admission_check is not None:
            admission_check()
        current = collaboration.source(args, origin, caller, hydrate=False)
        if (result_artifacts.fence(current, {}) != captured or current.get('ownership') != ownership
                or any(current.get(key) for key in ('fileAccessPending', 'configurationBusy', 'configurationPending'))):
            collaboration.error('The caller stopped, moved or changed while saving the artifact.', 409)
        if origin not in {'ui', 'user'}:
            collaboration.active_binding(current)
            if reason := app.assignments.blocked(current):
                collaboration.error(reason, 409)
        if current.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
            collaboration.error('Continue this chat here before saving an artifact.', 409)
        if app.portability.write_context(sid) != transfer_context:
            collaboration.error('The task transfer context changed while saving the artifact.', 409)
        work_guard(app, 'coordination.artifact.save', args, origin, caller, final=True)
        if resolve() != proof:
            collaboration.error('The exact result changed while saving its retained version.', 409)

    async def writable(paths):
        reports = await access(app, sid, [{'path': str(path)} for path in paths])
        if any(row['access']['status'] != 'allowed' for row in reports):
            collaboration.error('Current mounted file tools do not confirm write access to the artifact destination.', 403)

    try:
        async with app.lock:
            transfer_context = app.portability.write_context(sid)
            proof = resolve()
            item, record, response = proof
            guard()
            if record is None:
                collaboration.error(item.get('detail', 'The exact retained file is unavailable.'), 409)
            target = destination(root, args['path'])
        temporary = '.artifact-' + uuid.uuid4().hex + '.tmp'
        await writable([target, target.parent / temporary])
        async with app.lock:
            guard()
        # Parent folders must already exist. PinnedDirectory refuses symlinks
        # and Windows reparse points and binds all subsequent file operations.
        with (test_origin_files.directory(root, private=os.name == 'nt') as root_directory,
              test_origin_files.directory(target.parent, private=os.name == 'nt') as parent):
            if os.name != 'nt' and any(directory.stat().st_uid != os.getuid() for directory in (root_directory, parent)):
                raise ValueError('The destination folder must belong to the current account.')
            owned = None
            published = False
            try:
                prior = await settle_worker(existing, parent, target.name, record)
                if prior is None:
                    sources = await retained_files.plan(app, record) if record.get('bodyFormat') == retained_files.FORMAT else None
                    data = None if sources is not None else await app.outputs.content_async(record)
                    # This random exclusive staging name is ours to clean even
                    # when verification raises or the awaiting task is cancelled.
                    async with app.lock:
                        guard()
                        root_directory.assert_current()
                        parent.assert_current()
                    with os.fdopen(parent.create(temporary), 'wb') as stream:
                        owned = identity(os.fstat(stream.fileno()))
                        staged = await settle_worker(write_verified, stream, sources, record, data)
                await writable([target, target.parent / temporary])
                async with app.lock:
                    guard()
                    root_directory.assert_current()
                    parent.assert_current()
                    if prior is not None:
                        if identity(parent.stat(target.name)) != prior:
                            raise ValueError('The verified existing destination changed before reuse.')
                        status = 'reused'
                    else:
                        regular(parent.stat(temporary))
                        if identity(parent.stat(temporary)) != staged:
                            raise ValueError('The staged artifact changed before publication.')
                        # link() fails if *anything* now occupies the destination.
                        # Never replace or delete a public destination. POSIX has
                        # no atomic unlink-if-inode; rollback could delete a racing
                        # writer's replacement. Post-link failures remain explicit.
                        parent.link(temporary, target.name)
                        published = True
                        try:
                            parent.sync()
                            guard()
                            root_directory.assert_current()
                            parent.assert_current()
                            linked = parent.stat(target.name)
                            if (linked.st_dev, linked.st_ino, linked.st_size, linked.st_mtime_ns) != staged[:4]:
                                raise ValueError('Artifact publication identity changed; inspect the destination before retrying.')
                        except BaseException as exc:
                            publication_warning(exc, target)
                            raise
                        status = 'saved'
                    return {'status': status, 'path': str(target), 'bytes': record['bytes'], 'sha256': record['sha256'],
                            'requestId': args['requestId'], 'responseRevision': args['responseRevision'],
                            'reference': args['reference'], 'outputId': item['outputId'],
                            'provenance': item['provenance'], 'responseStatus': response.get('status'),
                            'qualified': response.get('qualified', False), 'independentArtifactVerification': False,
                            'executionRevision': source.get('executionRevision', 0)}
            finally:
                if owned:
                    try:
                        cleanup(parent, temporary, owned, sys.exc_info()[1])
                    except BaseException as exc:
                        if published:
                            publication_warning(exc, target)
                        raise
    except (OSError, ValueError) as exc:
        message = str(exc)
        for note in getattr(exc, '__notes__', ()):
            if note not in message:
                message += ' ' + note
        error = AppError(message, 409)
        for note in getattr(exc, '__notes__', ()):
            error.add_note(note)
        raise error from exc
