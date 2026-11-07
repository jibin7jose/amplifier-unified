"""Exact connection references and browser demand; never a history authority."""


def references(session, control):
    rows = list((session.get('runtimeReport') or {}).get('provider_choices', []))
    for name in ('configuration.catalog', 'configuration.providers'):
        rows.extend((control.get(name) or {}).get('providers', []))
    return {row['sharedCatalogKey'] for row in rows if row.get('sharedCatalogKey')}


def display_catalog(entry):
    """Closed controls need display/default metadata, not the option inventory."""
    metadata = entry.get('metadata') or {}
    fields = metadata.get('configSchema', {}).get('fields') or metadata.get('info', {}).get('config_fields', [])
    return {**{key: entry[key] for key in ('phase', 'error', 'supported', 'provenance', 'loadedAt', 'failedAt') if key in entry},
            'modelsDeferred': True, 'metadata': {
                'info': {key: value for key, value in metadata.get('info', {}).items()
                         if key in {'id', 'display_name', 'defaults'}},
                'configSchema': {'fields': [row for row in fields if row.get('id') == 'reasoning_effort']}}}


def project(state, *, settings=False, runtime=False):
    sid = state.get('selectedSessionId')
    session = next((row for row in state.get('sessions', []) if row['id'] == sid), {})
    control = state.get('runtimeControl', {}).get(sid, {})
    keys = references(session, control)
    view = state.get('view', {})
    picker = view.get('composerModel') or {}
    opened = picker.get('open') and picker.get('sessionId') == sid
    setup = state.get('setup') or {}
    # Draft controls and settings consume their own scoped setup inventory.
    if settings or sid is None:
        keys.update(row['sharedCatalogKey'] for row in setup.get('providerCatalogs', {}).values()
                    if row.get('sharedCatalogKey'))
    catalogs = state.get('modelCatalogs', {})
    state['modelCatalogs'] = {key: (catalogs[key] if opened or settings or runtime else display_catalog(catalogs[key]))
                              for key in keys if key in catalogs and isinstance(catalogs[key], dict)}
    if not settings and sid is not None:
        state['setup'] = {key: value for key, value in setup.items()
                          if key not in {'models', 'modelCatalogs', 'providerCatalogs', 'metadata'}}


def retain(service, incoming):
    """Current demand plus incoming data, evaluated only at catalog publication.

    Optimistic browser-only selections are not host state. If their cache entry
    retires before admission, the explicit picker request resolves it again.
    No cold message/execution bodies are accessed when collecting these keys.
    """
    from .history_demand import subscribed_sessions
    state = service._state
    index = service.projections.sessions(state)
    identities = subscribed_sessions(service) | index.active
    identities.add(state.get('selectedSessionId'))
    keys = set(incoming)
    for sid in identities & index.by_id.keys():
        keys.update(references(index.by_id[sid], state.get('runtimeControl', {}).get(sid, {})))
    keys.update(row['sharedCatalogKey'] for row in state.get('setup', {}).get('providerCatalogs', {}).values()
                if row.get('sharedCatalogKey'))
    return keys