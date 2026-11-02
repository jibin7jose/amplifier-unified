"""Bind provider-owned decision models to optional consumers. No credential I/O."""
import copy
import re
from importlib.metadata import entry_points
from .image_setup import row_id


def catalogs():
    result = {}
    for entry in entry_points(group='amplifier.decision_backends'):
        try:
            value = entry.load()()
            if value.get('schema_version') != 2 or not callable(value.get('validate')):
                continue
            module = value.get('provider_module')
            if not isinstance(module, str) or module in result:
                continue
            rows = value.get('backends', [])
            if len(rows) == 1 and rows[0].get('configuration_key') == 'decisions':
                row = copy.deepcopy(rows[0])
                runtime = value.get('runtime') or {}
                if (set(runtime) == {'kind', 'module', 'when'} and runtime.get('kind') == 'hook'
                        and isinstance(runtime.get('module'), str) and re.fullmatch(r'hooks-[a-z0-9-]+', runtime['module'])
                        and runtime.get('when') == {'chat_enabled': False}
                        and runtime['when']['chat_enabled'] is False):
                    row['decision_only_when'] = {'chat_enabled': False}
                result[module] = (row, value['validate'])
        except Exception:
            continue  # A broken optional provider cannot break ordinary setup.
    return result


def _backends(row):
    config = row.get('config', {}).get('decisions') or {}
    if not isinstance(config, dict):
        return {}
    if isinstance(config.get('backends'), dict):
        return config['backends']
    return {config['id']: config} if config.get('id') else {}


def describe(settings, providers=None):
    definitions = catalogs()
    providers = providers if providers is not None else settings.get('config', {}).get('providers', [])
    disabled = settings.get('configurator', {}).get('disabled', {}).get('providers', [])
    available, backends = [], {}
    for row in providers:
        identity = row_id(row)
        definition = definitions.get(row.get('module'))
        if not definition or row.get('enabled') is False or identity in disabled:
            continue
        public = copy.deepcopy(definition[0])
        public.update(id=identity, module=row['module'], label=f"{public['label']} · {identity}")
        configured = {item.get('model') for item in _backends(row).values() if isinstance(item, dict) and item.get('model')}
        if not public.get('default_model') and len(configured) == 1:
            public['default_model'] = next(iter(configured))
        available.append(public)
        for name, backend in _backends(row).items():
            if isinstance(backend, dict):
                backends[name] = {key: backend[key] for key in ('model', 'endpoint', 'enabled') if key in backend}
                backends[name]['provider'] = identity
    value = settings.get('decision_models') or {}
    selection = {purpose: {key: row[key] for key in ('mode', 'backend', 'allow_external_state') if key in row}
                 for purpose, row in value.get('selection', {}).items() if isinstance(row, dict)}
    services = [{'module': module, 'name': row['label'], 'decisionOnly': True,
                 **({'decisionOnlyWhen': copy.deepcopy(row['decision_only_when'])}
                    if row.get('decision_only_when') else {}),
                 **copy.deepcopy(row.get('connection', {}))}
                for module, (row, _) in definitions.items()
                if row.get('decision_only') is True or row.get('decision_only_when')]
    return {'catalog': available, 'services': services, 'backends': backends, 'selection': selection,
            'takesEffect': 'new_sessions', 'activation': 'requires_ui_use_behavior'}


def configure(manager, args, workspace, scope):
    purpose, mode = args.get('purpose'), args.get('mode')
    if purpose not in {'browser', 'computer'} or mode not in {'standard', 'decision'}:
        raise ValueError('Choose browser or computer and ordinary or decision selection')
    effective = manager.config(workspace)
    binding = {'mode': 'standard', 'backend': None, 'allow_external_state': False}
    selected, backend = None, None
    if mode == 'decision':
        choice = args.get('backend') or {}
        if not isinstance(choice, dict) or set(choice) - {'provider', 'model', 'endpoint'}:
            raise ValueError('Select a saved connection; credentials belong to that connection')
        selected = next((row for row in effective.providers if row_id(row) == choice.get('provider')), None)
        available = {row['id'] for row in describe(effective.settings, effective.providers)['catalog']}
        if not selected or choice.get('provider') not in available:
            raise ValueError('This decision connection is unavailable. Refresh your connections.')
        if args.get('allowExternalState') is not True:
            raise ValueError('Choose whether observations may be sent to this decision service')
        definition = catalogs()[selected['module']]
        backend = {**definition[1]({k: v for k, v in choice.items() if k != 'provider'}), 'enabled': True}
        identity = 'ui-' + purpose + '-' + row_id(selected)
        binding = {'mode': 'decision', 'backend': identity, 'allow_external_state': True}
    from .shared_settings import overlay, settings_paths, read_yaml
    paths = settings_paths(workspace, shared_home=manager.store.shared_home, global_only=manager.global_only)

    def preview(settings, stop=None):
        result, rows = {}, []
        for level, path in paths.items():
            value = settings if level == scope else read_yaml(path)
            result = overlay(result, value)
            for row in value.get('config', {}).get('providers', []):
                key = row.get('id') or row.get('module')
                index = next((i for i, other in enumerate(rows) if (other.get('id') or other.get('module')) == key), None)
                if index is None: rows.append(copy.deepcopy(row))
                else: rows[index] = overlay(rows[index], row)
            if level == stop: break
        result.setdefault('config', {})['providers'] = rows
        return result

    def save(settings):
        if selected:
            inherited = preview(settings, stop=scope)
            if not any(row_id(row) == row_id(selected) for row in inherited['config']['providers']):
                raise ValueError('This connection belongs to the workspace. Save in its workspace scope.')
            rows = settings.setdefault('config', {}).setdefault('providers', [])
            row = next((row for row in rows if row_id(row) == row_id(selected)), None)
            if row is None:
                row = {'module': selected['module'], 'config': {}}
                if selected.get('id') or selected.get('instance_id'):
                    row['id'] = row_id(selected)
                rows.append(row)
            config = row.setdefault('config', {})
            old = config.get('decisions') or {}
            models = copy.deepcopy(old.get('backends') or {})
            if old.get('id') and 'backends' not in old:
                models[old['id']] = {k: v for k, v in old.items() if k != 'id'}
            models[binding['backend']] = backend
            config['decisions'] = {**old, 'enabled': True, 'backends': models}
        settings.setdefault('decision_models', {}).setdefault('selection', {})[purpose] = binding
        combined = preview(settings)
        if combined.get('decision_models', {}).get('selection', {}).get(purpose) != binding:
            raise ValueError('This workspace overrides decision preferences. Save in its workspace scope.')
        if selected:
            row = next((r for r in combined['config']['providers'] if row_id(r) == row_id(selected)), {})
            if (_backends(row).get(binding['backend']) != backend or
                    row.get('config', {}).get('decisions', {}).get('enabled') is not True):
                raise ValueError('This workspace overrides decision models. Save in its workspace scope.')
    manager.store.update(workspace, scope, save)
    effective = manager.config(workspace)
    return {'decisionModels': describe(effective.settings, effective.providers), 'configurationChanged': True,
            'decisionSetupCompletion': {'purpose': purpose, 'mode': mode, 'scope': scope, 'takesEffect': 'new_sessions'}}


def apply(bundle, settings):
    """Provider configuration already merges normally; bind only present tools."""
    value = settings.get('decision_models')
    if not isinstance(value, dict):
        return
    from .shared_settings import overlay
    for row in bundle.tools:
        if row.get('module') == 'tool-ui-use':
            row['config'] = overlay(row.get('config', {}), {'selection': copy.deepcopy(value.get('selection', {}))})
