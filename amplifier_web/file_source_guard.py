"""Cooperative source checks around the reference file tools, not an OS sandbox.

The tools retain parsing, permissions and mutation ownership. Observations belong
to one mounted coordinator; admission locks are shared by one app data home.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import stat
import time
import unicodedata

from filelock import FileLock, Timeout


_KNOWN = {
    ('amplifier_module_tool_filesystem.read', 'ReadTool'): 'read',
    ('amplifier_module_tool_filesystem.write', 'WriteTool'): 'write',
    ('amplifier_module_tool_filesystem.edit', 'EditTool'): 'edit',
    ('amplifier_module_tool_apply_patch.tool', 'ApplyPatchTool'): 'patch',
}
_MISSING = object()
_LIMIT = 512
_GUIDANCE = (' Before changing or deleting existing files with this tool, read them '
             'with read_file in this worker. If file_source_stale or file_source_unread '
             'is returned, read the file again and reconsider the change. New files '
             'and move destinations must be absent. Builds and verified generated-output '
             'imports use their source/build checks; they do not require reading every '
             'generated file into model context. Preserve local changes and verify '
             'inputs and output hashes through the established build workflow. '
             'Do not bypass an actual source conflict with a shell command, build '
             'or another writer.')


class SourceConflict(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _version(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise SourceConflict('file_source_unsupported', 'Only regular files support source checks.')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _lock_keys(paths):
    keys = set()
    for path in paths:
        # Case folding conservatively serializes case aliases, including creates
        # on case-insensitive filesystems. Existing hard links share an inode key.
        keys.add('path:' + unicodedata.normalize('NFC', str(path).casefold()))
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        keys.add(f'inode:{info.st_dev}:{info.st_ino}')
    # Never unlink lock files while another process may hold/wait on them.
    # Fixed stripes bound storage; occasional collisions only serialize briefly.
    return sorted({hashlib.sha256(key.encode()).hexdigest()[:3] for key in keys})


@asynccontextmanager
async def _locked(directory, paths, timeout=10):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    locks = []
    deadline = time.monotonic() + timeout
    try:
        for key in _lock_keys(paths):
            lock = FileLock(directory / (key + '.lock'), timeout=0)
            while True:
                try:
                    lock.acquire()
                    locks.append(lock)
                    break
                except Timeout:
                    if time.monotonic() >= deadline:
                        raise SourceConflict('file_source_busy', 'Another file operation is still running; retry after it finishes.')
                    await asyncio.sleep(0.025)
        yield
    finally:
        for lock in reversed(locks):
            lock.release()


class FileSourceGuard:
    def __init__(self, coordinator, directory):
        self.coordinator = coordinator
        self.directory = Path(directory)
        self.observed = OrderedDict()

    def remember(self, path, version):
        self.observed[path] = version
        self.observed.move_to_end(path)
        while len(self.observed) > _LIMIT:
            self.observed.popitem(last=False)

    def _file_path(self, tool, value):
        if not isinstance(value, str) or not value:
            raise SourceConflict('file_source_input', 'file_path is required.')
        if value.startswith('@'):
            resolver = self.coordinator.get_capability('mention_resolver')
            path = resolver.resolve(value) if resolver else None
            if path is None:
                raise SourceConflict('file_source_input', 'The file mention could not be resolved.')
            path = Path(path)
        else:
            path = Path(value).expanduser()
            if not path.is_absolute() and tool.working_dir:
                path = Path(tool.working_dir) / path
        return path.resolve()

    def _targets(self, tool, kind, arguments):
        """Use each mounted engine's actual parser and permission owner."""
        if kind != 'patch':
            path = self._file_path(tool, arguments.get('file_path'))
            if kind == 'read':
                if not tool._is_allowed(path):
                    raise SourceConflict('file_source_access', 'Access denied: file is outside allowed read paths.')
            else:
                allowed, message = tool._check_write_access(path)
                if not allowed:
                    raise SourceConflict('file_source_access', message or 'Access denied.')
            return {path: kind}
        engine = tool._engine
        engine_type = (type(engine).__module__, type(engine).__name__)
        if engine_type == ('amplifier_module_tool_apply_patch.engines.native', 'NativeEngine'):
            operation = {'create_file': 'create', 'update_file': 'edit', 'delete_file': 'delete'}.get(arguments.get('type'))
            if operation is None or not isinstance(arguments.get('path'), str) or not arguments['path']:
                raise SourceConflict('file_source_input', 'A supported operation and path are required.')
            operations = [(arguments['path'], operation)]
        elif engine_type == ('amplifier_module_tool_apply_patch.engines.function', 'FunctionEngine'):
            operations = []
            for op in engine._parse_patch(arguments.get('patch', '')):
                operations.append((op.path, {'add': 'create', 'update': 'edit', 'delete': 'delete'}[op.type]))
                if op.move_to:
                    operations.append((op.move_to, 'create'))
        else:
            raise SourceConflict('file_source_unsupported', 'This patch engine has no supported source-check adapter.')
        targets = {}
        for value, operation in operations:
            path = engine._resolve_path(value).resolve()
            # Native validation returns a pair; function validation raises.
            validation = engine._validate_path(path)
            if validation is not None and not validation[0]:
                raise SourceConflict('file_source_access', validation[1] or 'Access denied.')
            if path in targets:
                raise SourceConflict('file_source_input', 'A patch may touch each path only once; combine edits to that file.')
            targets[path] = operation
        return targets

    async def execute(self, tool, kind, arguments):
        from amplifier_core import ToolResult
        targets = {}
        invoked = False
        try:
            targets = self._targets(tool, kind, arguments)
            # Capture before waiting: a simultaneous write by THIS coordinator
            # must not silently inherit a version produced while it waited.
            expected = {path: self.observed.get(path, _MISSING) for path in targets}
            for path, operation in targets.items():
                if operation == 'write' and expected[path] is _MISSING and not path.exists():
                    expected[path] = None
            async with _locked(self.directory, targets):
                # Re-resolve aliases and recheck policy under admission.
                if targets != self._targets(tool, kind, arguments):
                    raise SourceConflict('file_source_stale', 'The file path changed. Read it again before editing.')
                if kind == 'read':
                    path = next(iter(targets))
                    if path.is_dir():
                        return await tool.execute(arguments)
                    before = _version(path)
                    result = await tool.execute(arguments)
                    if result.success and _version(path) == before and before is not None:
                        self.remember(path, before)
                    else:
                        self.observed.pop(path, None)
                    return result
                for path, operation in targets.items():
                    current = _version(path)
                    seen = expected[path]
                    if operation == 'create':
                        if current is not None:
                            raise SourceConflict('file_source_stale', f'Creation would replace an existing file: {path}. Read it and use an update.')
                    elif seen is _MISSING:
                        if operation != 'write' or current is not None:
                            raise SourceConflict('file_source_unread', f'Read this file with read_file before changing it: {path}.')
                    elif current != seen:
                        raise SourceConflict('file_source_stale', f'The file changed since this worker read or wrote it: {path}. Read it again and reconsider the change.')
                invoked = True
                result = await tool.execute(arguments)
                if result.success:
                    for path in targets:
                        self.remember(path, _version(path))
                else:
                    for path in targets:
                        self.observed.pop(path, None)
                    error = dict(result.error or {})
                    error.update(effect='unknown', message=str(error.get('message', 'File operation failed.'))
                                 + ' Inspect affected files before retrying; a failed operation may have partially applied.')
                    return ToolResult(success=False, output=result.output, error=error)
                return result
        except asyncio.CancelledError:
            if invoked:
                for path in targets:
                    self.observed.pop(path, None)
            raise
        except Exception as exc:
            if invoked:
                for path in targets:
                    self.observed.pop(path, None)
            error = {'code': getattr(exc, 'code', 'file_source_check_failed'), 'message': str(exc),
                     'effect': 'unknown' if invoked else 'none'}
            if (not invoked and error['code'] == 'file_source_access'
                    and self.coordinator.get_capability('web.child.resumed') is True):
                error['message'] += (
                    ' This worker retains the permissions saved when it was created. '
                    'If this folder was approved later, ask the parent to start a new worker '
                    'with that approval. Resuming this worker does not widen its access.')
            if invoked:
                error['message'] += ' The operation may have changed files. Inspect them before retrying.'
            return ToolResult(success=False, error=error)


class GuardedFileTool:
    def __init__(self, tool, guard, kind):
        self.tool, self.guard, self.kind = tool, guard, kind

    def __getattr__(self, name):
        return getattr(self.tool, name)

    @property
    def description(self):
        return self.tool.description + (_GUIDANCE if self.kind != 'read' else '')

    async def execute(self, arguments):
        return await self.guard.execute(self.tool, self.kind, arguments)


async def install_file_source_guard(coordinator, directory=None):
    """Mount once per coordinator, including fresh and resumed native children."""
    if coordinator.get_capability('web.file_source_guard'):
        return
    from .host.config import app_home
    guard = FileSourceGuard(coordinator, directory or app_home() / 'file-source-locks')
    for name, tool in list((coordinator.get('tools') or {}).items()):
        kind = _KNOWN.get((type(tool).__module__, type(tool).__name__))
        if kind:
            await coordinator.mount('tools', GuardedFileTool(tool, guard, kind), name=name)
    coordinator.register_capability('web.file_source_guard', guard)
