"""Retain old image intent in new mount plans; no discovery or settings writes.

The source settings remain the rollback record. A versioned, credential-free
migration receipt accompanies the new plan. This is compatibility translation,
not a replacement image selector. Saved snapshots never enter this function.
"""
from copy import deepcopy
import hashlib
import json


def scoped_image_sources(workspace, *, shared_home=None, session_id=None, global_only=False):
    """Read only legacy mode provenance; never publish account/configuration data."""
    from .shared_settings import settings_paths, read_yaml
    rows = []
    for scope, path in settings_paths(workspace, shared_home=shared_home,
                                     session_id=session_id, global_only=global_only).items():
        settings = read_yaml(path)
        value = settings.get('image_generation', {})
        if isinstance(value, dict) and 'mode' not in value:
            continue
        mode = value.get('mode') if isinstance(value, dict) else None
        rows.append({'scope': scope, 'mode': mode if mode in ('off', 'automatic', 'selected') else 'unrecognized'})
    return rows


def scope_revision(store, workspace, scope, settings):
    # The token binds the selected file and whole record, not a caller-supplied path.
    value = [str(store.path(workspace, scope)), settings]
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def configuration_diagnostics(store, workspace, scope, *, global_only=False):
    rows = scoped_image_sources(workspace, shared_home=store.shared_home, global_only=global_only)
    items = []
    for row in rows:
        if row['mode'] == 'automatic':
            continue
        source_scope = row['scope']
        settings = store.read(workspace, source_scope)
        disabled = row['mode'] == 'off'
        items.append({'id': 'legacy-image-mode', 'module': 'tool-image',
            'sourceScope': source_scope, 'sourceKey': 'image_generation.mode',
            'state': 'disabled' if disabled else 'needs_review',
            'message': ('Image generation is disabled by a retained preference.' if disabled else
                        'Retained image intent needs review; no account or model choice will be changed here.'),
            'revision': scope_revision(store, workspace, source_scope, settings),
            'canReconcile': disabled and source_scope == scope,
            'resolution': 'inherit' if disabled else None})
    return {'items': items, 'scope': scope, 'takesEffect': 'new_sessions',
            'notice': 'Removing one retained preference grants no paid capability. Other scope restrictions, tool permissions and account/model pins still apply.'}


def reconcile_configuration(store, workspace, scope, args, *, global_only=False):
    """Explicit revision-checked removal of one old restriction, via SettingsStore."""
    if args.get('id') != 'legacy-image-mode' or args.get('resolution') != 'inherit' or args.get('confirm') is not True:
        raise ValueError('Confirm the supported scoped reconciliation explicitly.')
    def mutate(settings):
        if args.get('expectedRevision') != scope_revision(store, workspace, scope, settings):
            raise ValueError('Configuration changed; reload diagnostics before reconciling.')
        value = settings.get('image_generation')
        if not isinstance(value, dict) or value.get('mode') != 'off':
            raise ValueError('Only an explicit retained Off preference can be reconciled here.')
        # Preserve every other field, including unknown values and provider pins.
        del value['mode']
    before = args['expectedRevision']
    updated = store.update(workspace, scope, mutate)
    saved = store.read(workspace, scope)
    if saved != updated:
        raise ValueError('Configuration readback changed; inspect diagnostics before another action.')
    return {'diagnostics': configuration_diagnostics(store, workspace, scope, global_only=global_only),
            'reconciliation': {'id': args['id'], 'sourceScope': scope, 'sourceKey': 'image_generation.mode',
                'before': 'off', 'after': None, 'beforeRevision': before,
                'afterRevision': scope_revision(store, workspace, scope, saved), 'takesEffect': 'new_sessions'},
            'takesEffect': 'new_sessions'}


def apply_legacy_image_intent(plan, settings, *, unavailable_providers=(), scoped_sources=()):
    tools = [row for row in plan.get('tools', []) if row.get('module') == 'tool-image']
    if not tools:
        return None
    source = settings.get('image_generation', {})
    mode = source.get('mode') if isinstance(source, dict) else None
    # Removed provider declarations are evidence only, never mounted providers.
    rows = [*plan.get('providers', []), *unavailable_providers]
    legacy = []
    for row in rows:
        image = row.get('config', {}).get('image_generation')
        if isinstance(image, dict) and image:
            legacy.append((row, image))
    if not legacy and mode is None and not scoped_sources:
        return None
    # Retain only image-specific source fields, never broader provider config.
    receipt = {'version': 1, 'sourceMode': mode,
               'source': [{'providerInstance': row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-'),
                           'image': {k: deepcopy(v) for k, v in image.items() if k in {'enabled', 'id', 'model'}}}
                          for row, image in legacy], 'result': 'preserved'}
    if scoped_sources:
        receipt['scopes'] = deepcopy(list(scoped_sources))
    active = [(row, image) for row, image in legacy if image.get('enabled') is True and row.get('enabled') is not False]
    disabled = [(row, image) for row, image in legacy if image.get('enabled') is False]
    unavailable = [(row, image) for row, image in legacy if image.get('enabled') is True and row.get('enabled') is False]
    previous = plan.get('image_routing_migration', {})
    for tool in tools:
        config = tool.setdefault('config', {})
        if mode == 'off' or any(row['mode'] == 'off' for row in scoped_sources):
            config['allow_paid'] = False
            receipt['result'] = 'disabled'
        elif config.get('allow_paid') is False:
            receipt['result'] = 'disabled'
        elif mode not in {None, 'automatic', 'selected'} or any(row['mode'] == 'unrecognized' for row in scoped_sources):
            config['routing_blocked'] = True
            receipt['result'] = 'unsupported_legacy_mode'
        elif config.get('backend'):
            # Explicit old tool backend remains a bounded compatibility path.
            receipt['result'] = 'explicit_backend_preserved'
            if (previous.get('version') == 1 and previous.get('source') == receipt['source']
                    and previous.get('sourceMode') == mode
                    and previous.get('result') == 'legacy_backend_preserved'
                    and len(active) == 1
                    and config['backend'] == active[0][1].get('id', active[0][0]['module'].removeprefix('provider-'))):
                receipt['result'] = 'legacy_backend_preserved'
        elif unavailable:
            config['routing_blocked'] = True
            receipt['result'] = 'ambiguous_requires_review'
        elif len(active) == 1:
            # Existing registered provider backend preserves auto/pinned model
            # and exact account/transport without inventing a generic override.
            row, image = active[0]
            identity = image.get('id', row['module'].removeprefix('provider-'))
            config['backend'] = identity
            receipt['result'] = 'legacy_backend_preserved'
        elif mode == 'selected' or active or disabled or unavailable:
            # Old false values can mean deselection OR explicit refusal. With
            # no proof of which, do not enable those accounts via new routing.
            config['routing_blocked'] = True
            receipt['result'] = 'ambiguous_requires_review'
    plan['image_routing_migration'] = receipt
    return receipt
