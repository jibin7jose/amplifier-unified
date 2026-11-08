"""Small navigation facts; never load transcripts or acknowledge attention."""
from collections import Counter
from functools import lru_cache
import re
from .session_activity import activity_flags


def task_blocked(state, sid):
    task = state.get('runtimeControl', {}).get(sid, {}).get('task.get', {}).get('task') or {}
    return task.get('status') == 'blocked'


def activity(session, unread=False, *, error_reviewed=False, blocked=False, task=None, db=None):
    task = task or session.get('task') or {}
    if any(row.get('status') in {None, 'pending'} for row in session.get('approvals', [])):
        return {'kind': 'attention', 'label': 'Approval requested'}
    if any(row.get('status') == 'pending' and row.get('required')
           for row in session.get('questions', [])):
        return {'kind': 'attention', 'label': 'Answer requested'}
    if any((row.get('delivery') or {}).get('status') in {'unknown', 'rejected'}
           for row in session.get('questions', [])):
        return {'kind': 'attention', 'label': 'Check answer delivery'}
    status = session.get('status', 'idle')
    flags = activity_flags(session)
    if flags['execution']:
        return {'kind': 'working', 'label': {'starting': 'Starting', 'stopping': 'Stopping'}.get(status, 'Working')}
    if flags['preparing']:
        return {'kind': 'working', 'label': 'Starting' if status == 'starting' else 'Preparing'}
    if (session.get('error') or status in {'error', 'failed'}) and not error_reviewed:
        return {'kind': 'attention', 'label': 'Needs attention'}
    if status == 'interrupted':
        return {'kind': 'attention', 'label': 'Outcome unknown'}
    if status in {'stopped', 'cancelled', 'paused'} or task.get('status') == 'paused':
        return {'kind': 'paused', 'label': 'Stopped' if status in {'stopped', 'cancelled'} else 'Paused'}
    if blocked or task.get('status') == 'blocked' or status == 'blocked':
        from .navigation_waiting import project
        waiting = project(db, session, task)
        if waiting:
            return {'kind': 'waiting', 'label': 'Waiting for another chat', 'waiting': waiting}
        return {'kind': 'attention', 'label': 'Work blocked'}
    if unread:
        return {'kind': 'unread', 'label': 'New response'}
    return {'kind': 'idle', 'label': 'Idle'}


def path_labels(paths):
    """Shortest unique suffixes, computed against the full available registry."""
    # Registry paths, unlike activity, rarely change between stream updates.
    # Return a copy so callers cannot mutate the shared cached labels.
    return dict(_path_labels(frozenset(paths)))


@lru_cache(maxsize=4)
def _path_labels(paths):
    parts = {path: tuple(part for part in re.split(r'[\\/]+', path) if part) for path in set(paths)}
    counts = Counter(suffix for chunks in parts.values() for size in range(1, len(chunks) + 1)
                     for suffix in [chunks[-size:]])
    labels = {}
    for path, chunks in parts.items():
        for size in range(1, len(chunks) + 1):
            if counts[chunks[-size:]] == 1:
                labels[path] = '/'.join(chunks[-size:])
                break
        else:
            labels[path] = path
    # A root workspace can itself be a suffix of another registration. Keep
    # the absolute label in that case, and never claim two paths are identical.
    duplicates = Counter(labels.values())
    return {path: path if duplicates[label] > 1 else label for path, label in labels.items()}
