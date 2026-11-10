"""Reviewed cleanup of completed private backups; retain the newest backup."""
import asyncio
import json
from pathlib import Path
import re
import shutil
import os
import time
import uuid

from .managed_deletion import _safe, _inventory


def candidates(home):
    from .reset_retention import archive_ids, MARKER
    reset_archives = archive_ids(home)
    root = _safe(Path(home) / 'backups')
    if not root.is_dir():
        return []
    result = []
    for path in root.iterdir():
        if not re.fullmatch(r'[a-f0-9]{32}', path.name) or path.is_symlink() or not path.is_dir():
            continue
        if reset_archives is None or path.name in reset_archives or (path / MARKER).exists() or (path / MARKER).is_symlink():
            continue
        archive = path / 'private-state.tar.gz'
        if archive.is_file() and not archive.is_symlink():
            result.append(path)
    return sorted(result, key=lambda p: ((p / 'private-state.tar.gz').stat().st_mtime_ns, p.name), reverse=True)


async def dispatch(app, action, args):
    from .errors import AppError
    from .storage_usage import _remove
    try:
        async with app.lock:
            app.db.execute('CREATE TABLE IF NOT EXISTS storage_backup_cleanups (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
            identity = args['id']
            if not re.fullmatch(r'[a-f0-9]{32}', identity):
                raise ValueError('Choose a completed private backup.')
            saved = app.db.execute('SELECT value FROM storage_backup_cleanups WHERE id=?', (identity,)).fetchone()
            receipt = json.loads(saved[0]) if saved else None
            if receipt and receipt['status'] != 'preview':
                if action == 'storage.backupDelete' and args['confirmationToken'] == receipt['token']:
                    return {'accepted': True, 'result': receipt, 'effects': []}
                raise ValueError('This backup already has a cleanup receipt. Inspect the recorded original and cleanup folders before proceeding.')
            if getattr(app, 'backup_in_progress', False) or app.state.get('maintenance', {}).get('phase') in ('restoring', 'resetting', 'backing-up'):
                raise ValueError('Wait for backup or recovery work to finish before cleaning up backups.')
            paths = candidates(app.data_dir)
            path = _safe(app.data_dir / 'backups' / identity)
            if path not in paths or path == paths[0]:
                raise ValueError('Keep the newest backup. Only older completed private backups can be removed here.')
            if (not shutil.rmtree.avoids_symlink_attacks or os.rename not in os.supports_dir_fd
                    or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')):
                raise ValueError('Safe backup cleanup is unavailable on this host.')
            inventory = await asyncio.to_thread(_inventory, path)
            if action == 'storage.backupDeletePreview':
                receipt = {'id': identity, 'path': str(path), 'inventory': inventory,
                           'token': uuid.uuid4().hex, 'expiresAt': time.time() + 600, 'status': 'preview',
                           'description': 'Permanently delete this private backup and its recovery files. Current chats, workspace files and settings remain. The newest backup is kept.'}
                app.db.execute('INSERT OR REPLACE INTO storage_backup_cleanups VALUES (?,?)', (identity, json.dumps(receipt)))
                app.db.commit()
                return {'accepted': True, 'result': receipt, 'effects': []}
            if not receipt or args['confirmationToken'] != receipt['token'] or receipt['expiresAt'] < time.time() or inventory != receipt['inventory']:
                raise ValueError('The backup changed or its review expired. Review it again before deleting.')
            quarantine = path.with_name('.unified-backup-cleanup-' + receipt['token'])
            if quarantine.exists() or quarantine.is_symlink():
                raise ValueError('The cleanup destination already exists. Nothing was changed.')
            receipt.update(status='attempting', cleanupPath=str(quarantine))
            app.db.execute('UPDATE storage_backup_cleanups SET value=? WHERE id=?', (json.dumps(receipt), identity))
            app.db.commit()
            try:
                await asyncio.to_thread(_remove, path, inventory, quarantine)
                receipt.update(status='completed', deletedBytes=inventory['fileBytes'])
            except Exception:
                receipt.update(status='interrupted', deletedBytes=None,
                               message='Cleanup was interrupted. Inspect the recorded folders; deletion will not be retried.')
            app.db.execute('UPDATE storage_backup_cleanups SET value=? WHERE id=?', (json.dumps(receipt), identity))
            app.db.commit()
            return {'accepted': True, 'result': receipt, 'effects': []}
    except (ValueError, OSError) as error:
        raise AppError(str(error), 409) from None
