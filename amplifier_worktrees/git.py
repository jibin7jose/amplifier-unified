"""Bounded Git operations; no shell strings or automatic recovery replay."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import uuid

from filelock import FileLock

MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 2000


class CheckoutNotStarted(ValueError):
    """Creation refused before its durable admission or any checkout mutation."""


def source_binding(expected_revision, expected_commit, mode):
    if mode not in {'clean', 'carry_dirty'}:
        raise ValueError('Choose clean or carry_dirty explicitly')
    if expected_commit is not None:
        if expected_revision is not None or mode != 'clean':
            raise ValueError('expectedCommit is only for clean creation; do not combine it with sourceRevision')
        if (not isinstance(expected_commit, str) or len(expected_commit) not in {40, 64}
                or any(c not in '0123456789abcdef' for c in expected_commit)):
            raise ValueError('expectedCommit must be a full Git commit ID from clean inspection')
    elif not isinstance(expected_revision, str) or not expected_revision:
        raise ValueError('Provide expectedCommit for clean creation or the inspected sourceRevision for a snapshot')


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()).hexdigest()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _git(path, *arguments, data=None, overrides=(), allow_missing=False, limit=MAX_BYTES):
    """Fixed argv only. Disable repository hooks and external diff helpers."""
    command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', *overrides, '-C', str(path), *arguments]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        result = subprocess.run(command, input=data, stdout=output, stderr=error, timeout=30, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        if output.tell() > limit or error.tell() > limit: raise ValueError('Git output exceeds the managed operation limit')
        output.seek(0); error.seek(0)
        raw, detail = output.read(), error.read().decode('utf-8', 'replace')
        if result.returncode and not (allow_missing and result.returncode == 1): raise ValueError(detail.strip()[:4000] or 'Git could not complete this operation')
        return raw


def git(path, *arguments, data=None):
    # Status/diff run clean filters, checkout runs smudge/process filters, and
    # apply --index can also convert content. Disable all configured drivers for
    # every managed operation without changing source or global configuration.
    keys = _git(path, 'config', '--null', '--name-only', '--get-regexp',
                r'^filter\..*\.(clean|smudge|process|required)$', allow_missing=True, limit=65536)
    drivers = {key.rsplit('.', 1)[0] for key in keys.decode().split('\0') if key}
    overrides = []
    for driver in sorted(drivers):
        for suffix, value in (('clean', ''), ('smudge', ''), ('process', ''), ('required', 'false')):
            overrides.extend(('-c', driver + '.' + suffix + '=' + value))
    return _git(path, *arguments, data=data, overrides=overrides)


def text(path, *args): return git(path, *args).decode().strip()


def bounded_ref(value):
    if not isinstance(value, str) or not value or len(value) > 500 or value.startswith('-') or any(ord(c) < 32 for c in value):
        raise ValueError('Provide a normal Git ref or commit')
    return value


def common(path):
    root = Path(text(path, 'rev-parse', '--show-toplevel')).resolve()
    directory = Path(text(root, 'rev-parse', '--git-common-dir'))
    return root, (directory if directory.is_absolute() else root / directory).resolve()


def relative(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or '.git' in path.parts: raise ValueError('Unsafe worktree file path')
    return path


def untracked(root):
    names = git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    names = [name for name in names if name]
    if len(names) > MAX_FILES: raise ValueError('Too many untracked files to preserve in one manifest')
    rows, total = [], 0
    for name in names:
        path = root / relative(name)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode): value, kind = os.readlink(path).encode(), 'symlink'
        elif stat.S_ISREG(info.st_mode):
            if info.st_size > MAX_BYTES: raise ValueError('An untracked file exceeds the managed copy limit')
            value, kind = path.read_bytes(), 'file'
        else: raise ValueError('Only regular files and symbolic links can be preserved')
        total += len(value)
        if total > MAX_BYTES: raise ValueError('Untracked contents exceed the managed copy limit')
        rows.append({'path': name, 'kind': kind, 'sha256': digest(value), 'bytes': len(value), 'mode': stat.S_IMODE(info.st_mode)})
    return rows


def snapshot(path):
    root, repository = common(path)
    head = text(root, 'rev-parse', '--verify', 'HEAD^{commit}')
    status = git(root, 'status', '--porcelain=v1', '-z').decode()
    cached = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--binary', '--cached', 'HEAD', '--')
    changes = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--binary', '--')
    files = untracked(root)
    revision = digest([head, status, digest(cached), digest(changes), files])
    worktrees = []
    for group in git(root, 'worktree', 'list', '--porcelain', '-z').decode().split('\0\0'):
        if not group: continue
        row = {}
        for entry in group.split('\0'):
            if entry:
                key, _, value = entry.partition(' '); row[key] = value or True
        worktrees.append(row)
    return {'root': str(root), 'repository': str(repository), 'head': head, 'branch': text(root, 'branch', '--show-current'), 'dirty': bool(status), 'status': status.replace('\0', '\n'), 'sourceRevision': revision, 'untracked': files, 'worktrees': worktrees,
            'filterPolicy': 'disabled; raw Git and worktree bytes, including LFS pointers or locally materialized contents'}, cached, changes


class GitWorktrees:
    def __init__(self, directory, *, execution_host=None):
        self.execution_host = copy.deepcopy(execution_host)
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def record_path(self, identity):
        try: uuid.UUID(identity)
        except (ValueError, TypeError): raise ValueError('Invalid worktree identity') from None
        return self.directory / 'records' / (identity + '.json')

    def get(self, identity):
        path = self.record_path(identity)
        if not path.exists(): raise ValueError('Unknown managed worktree')
        return json.loads(path.read_text())

    def records(self): return [json.loads(path.read_text()) for path in sorted((self.directory / 'records').glob('*.json'))]
    def save(self, record): atomic(self.record_path(record['id']), record)

    @contextmanager
    def lock(self, repository):
        path = Path(repository) / 'amplifier-managed-worktrees.lock'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(path), timeout=30): yield

    def inspect(self, path, *, mode=None, ref='HEAD'):
        root, repository = common(path)
        with self.lock(repository):
            if mode == 'clean':
                commit = text(root, 'rev-parse', '--verify', '--end-of-options', bounded_ref(ref) + '^{commit}')
                return {'root': str(root), 'repository': str(repository), 'head': commit,
                        'expectedCommit': commit, 'ref': ref, 'sourceKind': 'commit'}
            if mode not in {None, 'carry_dirty'}:
                raise ValueError('Choose clean or carry_dirty explicitly')
            return snapshot(root)[0]

    @contextmanager
    def command_lock(self, action, command_id):
        # Receipts/checkouts share one storage namespace even when their Git
        # repositories have different locks. The same command cannot race two
        # repositories into overwriting one retained ownership record.
        path = self.directory / 'command-locks' / digest([action, command_id])
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(path), timeout=30): yield

    def create(self, source, *, command_id, expected_revision=None, expected_commit=None, mode='clean', ref=None, branch=None, session_id=None, admit=None):
        with self.command_lock('create', command_id):
            admitted = False

            def admission(effect):
                def commit():
                    nonlocal admitted
                    # Even an ambiguous receipt-write failure must stay unknown.
                    admitted = True
                    return effect()
                return admit(commit) if admit else commit()

            try:
                return self._create(source, command_id=command_id, expected_revision=expected_revision,
                    expected_commit=expected_commit, mode=mode, ref=ref,
                    branch=branch, session_id=session_id, admit=admission)
            except Exception as exc:
                identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'managed-worktree:' + command_id))
                if not admitted and not self.record_path(identity).exists():
                    raise CheckoutNotStarted(str(exc)) from exc
                raise

    def _create(self, source, *, command_id, expected_revision=None, expected_commit=None, mode='clean', ref=None, branch=None, session_id=None, admit=None):
        source_binding(expected_revision, expected_commit, mode)
        ref = ref if ref is not None else expected_commit or 'HEAD'
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'managed-worktree:' + command_id))
        signature = digest([str(source), expected_revision, mode, ref, branch, session_id])
        if expected_commit is not None:
            signature = digest([str(source), {'expectedCommit': expected_commit}, mode, ref, branch, session_id])
        root, repository = common(source)
        if self.directory.is_relative_to(root): raise ValueError('Managed checkout storage must be outside the source checkout')
        with self.lock(repository):
            path = self.record_path(identity)
            if path.exists():
                record = self.get(identity)
                if record['signature'] != signature: raise ValueError('The command ID already has different contents')
                result = {**record, 'duplicate': True}
                return admit(lambda: result) if admit else result
            if expected_commit is None:
                before, cached, changes = snapshot(root)
                if before['sourceRevision'] != expected_revision:
                    raise ValueError('The source checkout changed; sourceRevision must be the inspected snapshot digest, not a Git commit. Use expectedCommit for clean creation.')
            else:
                before, cached, changes = {}, b'', b''
            commit = text(root, 'rev-parse', '--verify', '--end-of-options', bounded_ref(ref) + '^{commit}')
            if expected_commit is not None and commit != expected_commit:
                raise ValueError('The selected ref no longer matches expectedCommit; inspect the committed ref again')
            if mode == 'carry_dirty' and commit != before['head']: raise ValueError('Carry changes requires the exact current source HEAD')
            if expected_commit is None and git(root, 'ls-files', '-u'): raise ValueError('Resolve source index conflicts before creating a managed checkout')
            if mode == 'carry_dirty' and any(row.startswith(b'160000 ') for row in git(root, 'ls-files', '--stage', '-z').split(b'\0')):
                raise ValueError('Carry changes with submodules is not supported; their separate working state must be preserved explicitly')
            if mode == 'carry_dirty':
                for line in git(root, 'ls-files', '--debug').decode().splitlines():
                    if 'flags: ' in line and int(line.rsplit('flags: ', 1)[1], 16) & 0x60008000:
                        raise ValueError('Carry changes cannot preserve intent-to-add, skip-worktree or assume-unchanged index flags; resolve them explicitly first')
            if branch:
                bounded_ref(branch); git(root, 'check-ref-format', 'refs/heads/' + branch)
            target = self.directory / 'checkouts' / identity
            binding = {'expectedCommit': expected_commit} if expected_commit is not None else {'sourceRevision': before['sourceRevision']}
            record = {'id': identity, 'revision': 1, 'signature': signature, 'sessionId': session_id, 'executionHost': copy.deepcopy(self.execution_host), 'source': str(root), 'repository': str(repository), 'path': str(target), 'head': commit, 'branch': branch, 'mode': mode, 'status': 'creating', 'owned': True, 'createdAt': time.time(), **binding, 'manifest': {'sourceUnchanged': True, 'stagedPatchSha256': digest(cached), 'unstagedPatchSha256': digest(changes), 'untracked': before['untracked'] if mode == 'carry_dirty' else [], 'ignoredFilesIncluded': False}}
            evidence = self.directory / 'manifests' / identity
            record['manifest']['evidenceDirectory'] = str(evidence)
            # The host may commit this short durable admission under its state
            # lock. All Git lock waits and read-only validation precede it.
            # Once admitted, preserve and settle this exact operation even if
            # its caller stops; never replay an uncertain filesystem effect.
            if admit: admit(lambda: self.save(record))
            else: self.save(record)
            evidence.mkdir(parents=True, exist_ok=False, mode=0o700)
            try:
                if mode == 'carry_dirty':
                    (evidence / 'staged.patch').write_bytes(cached); (evidence / 'unstaged.patch').write_bytes(changes)
                    for row in before['untracked']:
                        original = root / relative(row['path'])
                        data = os.readlink(original).encode() if row['kind'] == 'symlink' else original.read_bytes()
                        if digest(data) != row['sha256']: raise ValueError('Source changed while capturing untracked evidence')
                        (evidence / row['sha256']).write_bytes(data)
                for saved in evidence.iterdir():
                    saved.chmod(0o600)
                    with saved.open('rb') as stream: os.fsync(stream.fileno())
                if expected_commit is None and snapshot(root)[0]['sourceRevision'] != before['sourceRevision']: raise ValueError('The source checkout changed during capture')
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                git(root, 'worktree', 'add', *(['-b', branch] if branch else ['--detach']), str(target), commit)
                if mode == 'carry_dirty':
                    if cached: git(target, 'apply', '--index', '--binary', '-', data=cached)
                    if changes: git(target, 'apply', '--binary', '-', data=changes)
                    for row in before['untracked']:
                        destination = target / relative(row['path'])
                        if not destination.parent.resolve().is_relative_to(target): raise ValueError('Untracked path traverses a symbolic link outside the checkout')
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        if destination.exists() or destination.is_symlink(): raise ValueError('An untracked destination already exists')
                        data = (evidence / row['sha256']).read_bytes()
                        if row['kind'] == 'symlink': destination.symlink_to(data.decode())
                        else: destination.write_bytes(data); destination.chmod(row['mode'])
                record.update(status='ready', revision=2)
            except Exception as exc:
                record.update(status='partial', revision=2, error=str(exc)[:4000])
                self.save(record)
                raise ValueError('Managed checkout creation stopped; the source and any partial checkout are preserved. ' + str(exc)) from exc
            self.save(record)
            return record

    def attach(self, path, *, source, command_id, session_id=None, admit=None):
        with self.command_lock('attach', command_id):
            return self._attach(path, source=source, command_id=command_id, session_id=session_id, admit=admit)

    def _attach(self, path, *, source, command_id, session_id=None, admit=None):
        target, repository = common(path)
        _, expected = common(source)
        if repository != expected: raise ValueError('The checkout must belong to this task’s repository')
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'attached-worktree:' + command_id))
        with self.lock(repository):
            if self.record_path(identity).exists():
                record = self.get(identity)
                if record['path'] != str(target) or record['sessionId'] != session_id or record['source'] != str(Path(source).resolve()):
                    raise ValueError('The command ID already names another checkout or source')
                return admit(lambda: record) if admit else record
            checked = snapshot(target)[0]
            if str(target) not in [row.get('worktree') for row in checked['worktrees']]: raise ValueError('Git does not register that worktree')
            record = {'id': identity, 'revision': 1, 'sessionId': session_id, 'executionHost': copy.deepcopy(self.execution_host), 'path': str(target), 'source': str(Path(source).resolve()), 'repository': str(repository), 'head': checked['head'], 'branch': checked['branch'], 'status': 'ready', 'owned': False, 'mode': 'attached', 'createdAt': time.time(), 'manifest': {'sourceUnchanged': True}}
            if admit: admit(lambda: self.save(record))
            else: self.save(record)
            return record

    def status(self, identity, *, reconcile=True):
        record = self.get(identity)
        if record['status'] == 'removed': return record
        if reconcile and record['status'] in {'archive_pending', 'archive_unknown'}:
            with self.lock(record['repository']):
                record = self.get(identity)
                for operation in record.get('archiveOperations', {}).values():
                    if operation['status'] in {'pending', 'unknown'}:
                        self._settle_relocation(record, operation)
        try: record['checkout'] = self.inspect(record['path'])
        except (ValueError, OSError) as exc: record['inspectionError'] = str(exc)[:4000]
        return record

    def relocate(self, identity, expected_revision, command_id, *, restore=False):
        """Retain an entire owned checkout; never classify ignored files as waste."""
        action = 'restore' if restore else 'archive'
        with self.command_lock(action, command_id):
            record = self.get(identity)
            with self.lock(record['repository']):
                record = self.get(identity)
                operations = record.setdefault('archiveOperations', {})
                signature = digest([action, identity, expected_revision])
                if command_id in operations:
                    operation = operations[command_id]
                    if operation['signature'] != signature:
                        raise ValueError('This archive request already has different contents')
                    if operation['status'] in {'pending', 'unknown'}:
                        self._settle_relocation(record, operation)
                    return {**record, 'operation': copy.deepcopy(operation), 'duplicate': True}
                if record['revision'] != expected_revision:
                    raise ValueError('The worktree record changed; inspect it again')
                if not record['owned']:
                    raise ValueError('Only app-created checkouts can be archived or restored here')
                expected = 'archived' if restore else 'ready'
                if record['status'] != expected:
                    raise ValueError('Inspect the checkout before archiving or restoring it')
                source = Path(record['path'])
                target = self.directory / ('checkouts' if restore else 'archives') / identity
                if source.is_symlink() or target.exists() or target.is_symlink():
                    raise ValueError('The checkout path changed or its destination already exists; files preserved')
                root, repository = common(source)
                if root != source or str(repository) != record['repository']:
                    raise ValueError('Git worktree ownership changed')
                # Reuse Git registration as commit retention, including detached
                # HEAD. The entire directory moves; no file traversal or cleanup.
                observed = source.stat()
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.parent.is_symlink() or target.parent.resolve() != target.parent:
                    raise ValueError('Archive storage changed; files preserved')
                operation = {'id': command_id, 'signature': signature, 'action': action,
                    'status': 'pending', 'source': str(source), 'target': str(target),
                    'directoryIdentity': [observed.st_dev, observed.st_ino],
                    'createdAt': time.time(), 'originalStatus': expected}
                operations[command_id] = operation
                record.update(status='archive_pending', revision=record['revision'] + 1)
                self.save(record)  # Durable intent before the move.
                try:
                    git(source, 'worktree', 'move', str(source), str(target))
                except BaseException:
                    self._settle_relocation(record, operation)
                    raise
                self._settle_relocation(record, operation)
                return {**record, 'operation': copy.deepcopy(operation)}

    def _settle_relocation(self, record, operation):
        """Read actual paths and Git registration; reconciliation never moves files."""
        source, target = Path(operation['source']), Path(operation['target'])
        def owns(path):
            try:
                if path.is_symlink():
                    return False
                info = path.stat()
                root, repository = common(path)
                return ([info.st_dev, info.st_ino] == operation['directoryIdentity']
                    and root == path and str(repository) == record['repository']
                    and ('worktree ' + str(path) + '\0') in git(path, 'worktree', 'list', '--porcelain', '-z').decode())
            except (OSError, ValueError):
                return False
        if not source.exists() and not source.is_symlink() and owns(target):
            operation['status'] = 'applied'
            record.update(path=str(target), status='ready' if operation['action'] == 'restore' else 'archived')
        elif not target.exists() and not target.is_symlink() and owns(source):
            operation['status'] = 'not_applied'
            record['status'] = operation['originalStatus']
        else:
            operation['status'] = 'unknown'
            record['status'] = 'archive_unknown'
        operation['observedAt'] = time.time()
        record['revision'] += 1
        self.save(record)

    def remove(self, identity, expected_revision, command_id=None):
        record = self.get(identity)
        with self.lock(record['repository']):
            record = self.get(identity)
            if command_id and record.get('removeCommand') == {'id': command_id, 'expectedRevision': expected_revision}: return {**record, 'duplicate': True}
            if record['revision'] != expected_revision: raise ValueError('The worktree record changed; inspect it again')
            if record['status'] == 'removed': return record
            if record['status'] != 'ready': raise ValueError('Restore and inspect this checkout before removal')
            if not record['owned']: raise ValueError('Only app-created checkouts can be removed here')
            root, repository = common(record['path'])
            if str(repository) != record['repository'] or root != Path(record['path']): raise ValueError('Git worktree ownership changed')
            checked = snapshot(root)[0]
            if checked['dirty'] or git(root, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z'):
                raise ValueError('This checkout has changed or ignored files; preserve them before removal')
            referenced = git(root, 'for-each-ref', '--contains', checked['head'], '--format=%(refname)', 'refs/heads', 'refs/tags', 'refs/remotes')
            other_head = any(row.get('HEAD') == checked['head'] and row.get('worktree') != str(root) for row in checked['worktrees'])
            if not referenced.strip() and not other_head:
                raise ValueError('Detached commits have no retained branch, tag or other worktree; preserve them before removal')
            git(root, 'worktree', 'remove', str(root))
            record.update(status='removed', revision=record['revision']+1, removedAt=time.time(), removeCommand={'id': command_id, 'expectedRevision': expected_revision})
            self.save(record); return record
