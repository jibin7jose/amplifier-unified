"""Synthetic connection metadata for already-configured browser journeys."""
from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import patch


PROVIDER_ID = 'fixture'
PROVIDER_MODULE = 'provider-fixture'
MODEL_ID = 'fixture-model'


def seed_connection(home):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    (home / 'settings.yaml').write_text(json.dumps({'config': {'providers': [
        {'module': PROVIDER_MODULE, 'id': PROVIDER_ID,
         'config': {'default_model': MODEL_ID}}]}}))


@contextmanager
def synthetic_discovery():
    """Replace external discovery only, before app startup and until cleanup.

    A synthetic conversation runtime does not replace draft bundle preparation
    or provider discovery. Geometry tests need neither; leave the production
    catalog/configuration owners in place without starting either real probe.
    This is fixture isolation, not production descendant-process containment.
    """
    from amplifier_web import draft_defaults
    from amplifier_web.setup import SetupManager

    original_resolve_defaults = draft_defaults.resolve_defaults

    def metadata():
        return {'module': PROVIDER_MODULE,
                'info': {'display_name': 'Fixture', 'defaults': {'model': MODEL_ID}},
                'configSchema': {'fields': []}}

    async def probe(self, action, args, workspace):
        assert action in {'providers.models', 'providers.schema'}, action
        assert args.get('id') in {None, PROVIDER_ID}, args
        assert args.get('module') in {None, PROVIDER_MODULE}, args
        if action == 'providers.schema':
            assert args.get('module') == PROVIDER_MODULE, args
            return {'providerMetadata': metadata()}
        assert args.get('id') == PROVIDER_ID, args
        return {'models': [{'id': MODEL_ID}], 'modelsSupported': True,
                'modelsProvenance': 'fixture', 'providerMetadata': metadata()}

    async def resolve_defaults(home, workspace, bundle=None, app_bundle=None, **kwargs):
        if kwargs.get('catalog'):
            # The original catalog branch uses SetupManager, not a bundle probe.
            return await original_resolve_defaults(home, workspace, bundle, app_bundle, **kwargs)
        return {'bundle': bundle or 'work', 'globalOnly': kwargs.get('global_only', False),
                'effective': {'instance': PROVIDER_ID, 'model': MODEL_ID},
                'providers': [{'id': PROVIDER_ID, **metadata()}]}

    with patch.object(SetupManager, 'probe', probe), \
            patch.object(draft_defaults, 'resolve_defaults', resolve_defaults):
        yield
