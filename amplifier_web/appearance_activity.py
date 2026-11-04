"""Small presentation facts over admitted lifecycle owners, not browser pages.

SessionIndex already maintains active identities incrementally. Do not hydrate
history, read transcripts, query the directory, or treat optional HTTP reads as
work merely to decorate the application.
"""
from .session_activity import activity_flags

_OPERATION_PHASES = {'queued', 'pending', 'starting', 'preparing', 'working', 'running', 'stopping'}
# Management publishes progress for these canonical metadata reads too. Their
# status is still available to the requesting control, but is not app-wide work.
_QUIET_READS = frozenset({
    'providers.list', 'bundles.list', 'providers.credentials', 'providers.schema',
    'providers.models', 'providers.imageModels', 'providers.decisionModels',
    'routing.list', 'routing.show', 'configuration.defaults', 'bundle.preview',
})


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
                     for action, row in state.get('actionStatus', {}).items()
                     if action not in _QUIET_READS
                     and not (action == 'runtime.control'
                              and row.get('target', {}).get('operation') == 'configuration.catalog'))
    return {**flags, 'operations': operations, 'active': operations or any(flags.values())}
