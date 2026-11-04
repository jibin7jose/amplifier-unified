"""Removing one account suppresses that identity through real settings loading."""
import copy
from amplifier_foundation import Bundle

import pytest
import yaml

from amplifier_web.setup import SetupManager
from amplifier_web.host.session import _apply_settings


@pytest.mark.parametrize('identity_field', [None, 'id', 'instance_id'])
@pytest.mark.parametrize('scope', ['global', 'local'])
def test_remove_suppresses_declared_provider_and_preserves_sibling(tmp_path, identity_field, scope):
    manager = SetupManager(tmp_path / 'app')
    module = 'provider-openai-chatgpt'
    identity = 'deleted' if identity_field else 'openai-chatgpt'
    provider = {'module': module, **({identity_field: identity} if identity_field else {}),
                'config': {'login_on_mount': False, 'token_file_path': '/synthetic/missing.json'}}
    sibling = {'module': module, 'id': 'kept', 'config': {'token_file_path': '/synthetic/kept.json'}}
    manager.store.shared_home.mkdir(parents=True, exist_ok=True)
    (manager.store.shared_home / 'settings.yaml').write_text(yaml.safe_dump({
        'config': {'providers': [provider, sibling]}}))
    manager._provider_mutation({'id': identity}, str(tmp_path), scope, remove=True)
    config = manager.config(str(tmp_path))
    bundle = Bundle(name='provider-removal', providers=[copy.deepcopy(provider)])
    rows = _apply_settings(bundle, config).providers
    assert [row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')
            for row in rows] == ['kept']
    assert rows[0]['config']['token_file_path'] == '/synthetic/kept.json'
