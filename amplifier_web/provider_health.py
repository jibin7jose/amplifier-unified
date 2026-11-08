"""Credential-free, generation-bound account failures for the settings trail."""
import hashlib
import json
from pathlib import Path
from .deployment import write_private


def generation(module, config):
    # Hash the private configuration; never store or expose it in the receipt.
    token = config.get('token_file_path')
    if module == 'provider-openai-chatgpt' and not token:
        token = '~/.amplifier/chatgpt-plan/default.json' if config.get('auth_mode') == 'chatgpt_plan' else '~/.amplifier/openai-chatgpt-oauth.json'
    stamp = None
    if isinstance(token, str):
        try:
            st = Path(token).expanduser().stat()
            stamp = [st.st_ino, st.st_mtime_ns, st.st_size]
        except OSError: pass
    return hashlib.sha256(json.dumps([module, config, stamp], sort_keys=True, default=str).encode()).hexdigest()


def path(home, identity):
    return Path(home)/'provider-health'/(hashlib.sha256(identity.encode()).hexdigest()+'.json')


def record(home, identity, fingerprint, *, authentication_required):
    write_private(path(home, identity), json.dumps({'generation':fingerprint, 'authenticationRequired':authentication_required}),
                  owned_root=Path(home))


def issue(home, identity, module, config):
    try:
        row=json.loads(path(home,identity).read_text())
        return isinstance(row,dict) and row.get('generation') == generation(module,config) and row.get('authenticationRequired') is True
    except (OSError, ValueError): return False
