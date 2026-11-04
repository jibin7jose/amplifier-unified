"""Small presentation facts over admitted lifecycle owners, not browser pages.

SessionIndex already maintains active identities incrementally. Do not hydrate
history, read transcripts, query the directory, or treat optional HTTP reads as
work merely to decorate the application.
"""
from .session_activity import activity_flags

_OPERATION_PHASES = {'queued', 'pending', 'starting', 'preparing', 'working', 'running', 'stopping'}


def snapshot(state, index):
    flags = dict(execution=False, preparing=False, loading=False)
    for identity in index.active:
        row = index.by_id[identity]
        if row.get('sessionKind') == 'internal':
            continue
        for name, value in activity_flags(row).items():
            flags[name] |= value
    # Canonical long-operation status, not polling/read traffic or DOM spinners.
    operations = any(row.get('phase') in _OPERATION_PHASES
                     for row in state.get('actionStatus', {}).values())
    return {**flags, 'operations': operations, 'active': operations or any(flags.values())}
