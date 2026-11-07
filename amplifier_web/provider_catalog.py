"""Private connection catalogs: explicit refresh, restart reuse and single flight."""
import asyncio
import copy
from functools import lru_cache
import hashlib
import json
import os
import time
import uuid
from pathlib import Path


_UNQUALIFIED_GENERATION = uuid.uuid4().hex

class CatalogInvalidated(ValueError):
    """Captured configuration changed; no result or failure may be retained."""


def public_failure(exc):
    """Only bounded categories/stages, never provider exception bodies."""
    import re
    from .probe_diagnostics import ProbeProcessFailure, HostProbeFailure
    if isinstance(exc, (ProbeProcessFailure, HostProbeFailure)):
        return exc.public_message, dict(exc.diagnostic)
    supplied = getattr(exc, 'diagnostic', {}) or {}
    stage = supplied.get('stage', 'models')
    if stage not in {'preparation','import','schema','configuration','construction','metadata','models','probe'}:
        stage = 'probe'
    category = supplied.get('category', type(exc).__name__)
    if not isinstance(category, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', category):
        category = 'ProviderError'
    diagnostic = {'stage':stage,'category':category}
    return f'Connection discovery failed at {stage} ({category}). Refresh this connection to retry.', diagnostic


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@lru_cache(maxsize=64)
def _receipt_digest(path,signature):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generation_identity(home):
    """Read-only identity of the qualified component generation and its graph."""
    from .updates import active_release
    from .runtime_environment import receipt_directory
    generation=active_release(home).get('current')
    receipt=receipt_directory(home,generation)
    files=[]
    for path in (receipt/'runtime.lock',receipt/'runtime-installed.json',receipt/'runtime-sources.json'):
        try:
            info=path.stat()
            signature=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            files.append((path.name,_receipt_digest(str(path),signature)))
        except FileNotFoundError:files.append((path.name,None))
    # Before qualification there is no proof that an installed module stayed
    # at the same revision. Reuse locally, but not across worker/host restarts.
    qualified=any(name=='runtime-installed.json' and digest for name,digest in files)
    return fingerprint([generation,files,None if qualified else _UNQUALIFIED_GENERATION])


@lru_cache(maxsize=16)
def _qualified_provider_sources(home,identity):
    """Only installed provenance can equate an implicit import with a source."""
    from .updates import active_release
    from .runtime_environment import receipt_directory
    receipt=receipt_directory(home,active_release(home).get('current'))
    try:
        rows=json.loads((receipt/'runtime-installed.json').read_text())
        policy_file=receipt/'runtime-sources.json'
        policies=json.loads(policy_file.read_text()) if policy_file.exists() else {}
        result={}
        for row in rows:
            name=row['name']
            if not name.startswith('amplifier-module-provider-'):continue
            direct=row.get('directUrl',{});vcs=direct.get('vcs_info',{});cached=row.get('cacheSource',{})
            if cached and not cached.get('dirty'):
                url,ref,subdirectory=cached['url'],cached['ref'],cached.get('subdirectory','')
            elif vcs.get('vcs')=='git':
                url=direct['url'];ref=vcs.get('requested_revision') or vcs['commit_id']
                subdirectory=direct.get('subdirectory','')
                policy=policies.get(name,{})
                if policy.get('url')==url and policy.get('subdirectory','')==subdirectory:
                    ref=policy.get('ref') or ref
            else:continue  # Local/registry overrides cannot match a Git declaration.
            result[name.removeprefix('amplifier-module-')]='git+'+url+'@'+ref+(
                '#subdirectory='+subdirectory if subdirectory not in ('','.') else '')
        return result
    except (OSError,ValueError,TypeError,KeyError,AttributeError):return {}


def _chatgpt_account(contents, config):
    """Local binding witness, NOT token authentication or entitlement.

    The provider owns refresh/validation: Codex preserves account_id; Plan
    preserves (auth_mode, client_id, subject). Only these supported records can
    outlive token rotation. Unknown/malformed records retain byte identity.
    """
    from .setup import chatgpt_auth_mode
    try:
        tokens = json.loads(contents)
        mode = chatgpt_auth_mode(config.get('auth_mode'))
        stored = tokens.get('auth_mode')
        if stored == 'oauth':stored = 'chatgpt_codex'
        else:stored = chatgpt_auth_mode(stored)
        if mode != stored or mode not in {'chatgpt_codex', 'chatgpt_plan'}:return None
        keys = ('subject', 'client_id') if mode == 'chatgpt_plan' else ('account_id',)
        if any(not isinstance(tokens.get(k), str) or not tokens[k].strip()
               for k in (*keys, 'access_token')):return None
        # Missing/invalid refresh and expiration are not renewable credentials.
        if tokens.get('refresh_token') is not None and (
                not isinstance(tokens['refresh_token'], str) or not tokens['refresh_token'].strip()):return None
        from datetime import datetime
        import math
        expiry = tokens.get('expires_at')
        if mode == 'chatgpt_codex' and isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry.replace('Z', '+00:00')).timestamp()
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):return None
        if not tokens.get('refresh_token') and expiry <= time.time():return None
        return ['chatgpt-account-v1', mode, *(tokens[k] for k in keys)]
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def connection_key(workspace, module, config, source=None, *, home=None, _credential_generation=False):
    """Stable private connection binding shared by host and worker processes.

    The connection binding ignores selection defaults, model filters and priority. Every other provider
    setting, effective credential, credential file and source remains isolated.
    Supported ChatGPT OAuth files bind the stable account, not renewable tokens.
    Missing environment values retain their reference, never match a mounted
    provider with a different materialized value, and never leak in the digest.
    """
    from .setup import PROVIDER_ENV, environment_credential, chatgpt_token_path
    from .host.config import expand_environment
    raw = copy.deepcopy(config or {})
    for name in ('priority', 'model', 'default_model', 'reasoning_effort', 'model_filters'):
        raw.pop(name, None)
    credential = environment_credential(module, raw)
    if not raw.get(credential['field']) and credential['available']:
        raw[credential['field']] = '${' + credential['envVar'] + '}'
    def materialize(value):
        if isinstance(value, dict):return {key:materialize(item) for key,item in value.items()}
        if isinstance(value, list):return [materialize(item) for item in value]
        try:return expand_environment(value)
        except ValueError:return value
    raw = materialize(raw)
    if module == 'provider-openai-chatgpt':
        # Existing OAuth/Plan configurations may omit the constructor default.
        # Both implicit and explicit paths must observe rotation and removal.
        raw['token_file_path'] = chatgpt_token_path(raw)
    # Ambient fallback credentials can affect providers even with an explicit
    # constructor field. Preserve those dependencies in both caller processes.
    environment = {name:os.environ.get(name) for name in PROVIDER_ENV.get(module, ())}
    if module == 'provider-github-copilot' and raw.get('github_token'):
        # apply_provider_environment promotes this exact effective credential
        # before mounting. Normalize that same promotion without mutating env.
        environment['COPILOT_AGENT_TOKEN'] = raw['github_token']
    def fields(value, prefix=''):
        if isinstance(value, dict):
            for name, item in value.items():yield from fields(item, prefix+'.'+name)
        elif isinstance(value, list):
            for index, item in enumerate(value):yield from fields(item, prefix+'.'+str(index))
        else:yield prefix, value
    leaves = list(fields(raw))
    files = {}
    for name,value in leaves:
        if ('token' in name or 'credential' in name) and ('file' in name or 'path' in name) and isinstance(value,str):
            try:
                path = Path(value).expanduser()
                if not path.is_absolute():path = Path(workspace)/path
                if path.is_file():
                    info = path.stat()
                    if info.st_size < 1_000_000:
                        contents = path.read_bytes()
                        account = (_chatgpt_account(contents, raw)
                                   if module == 'provider-openai-chatgpt' and name == '.token_file_path'
                                   and not _credential_generation else None)
                        files[name] = account if account is not None else fingerprint(contents)
                    else:
                        files[name] = (info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            except (OSError,ValueError):
                files[name]='unreadable'  # Provider validation owns invalid paths.
    generation=generation_identity(home) if home is not None else None
    # Manifest defaults do not prove which implementation an implicit probe
    # imports. Unknown/local overrides deliberately keep a separate identity.
    source=source or (_qualified_provider_sources(str(home),generation).get(module) if home is not None else None)
    # Absolute/effective connection settings can share a catalog across projects.
    # Keep relative filesystem settings isolated by their resolution directory.
    relative=any(isinstance(value,str) and value and not Path(value).expanduser().is_absolute()
                 for name,value in leaves if any(part in name for part in ('path','file','directory')))
    scope=str(Path(workspace).resolve()) if relative or source and not str(source).startswith(('git+','https://','http://','/')) else None
    return fingerprint(['provider-connection-v1',scope,module,source,raw,environment,files])


def configuration_key(workspace, module, config, source=None, *, home=None):
    """Discovery reuse additionally requires the same qualified runtime.

    An unqualified process keeps its private cache salt: it must not reuse
    another process's discovery as evidence about an unknown implementation.
    That salt is deliberately NOT the identity used to admit a selection.
    """
    from .host.config import expand_environment
    scope = {}
    for name in ('model_filters', 'model', 'default_model'):
        if name not in (config or {}):continue
        value = config[name]
        try:value = expand_environment(value)
        except ValueError:pass  # Preserve unresolved references, as connection_key does.
        scope[name] = value
    # The selected recommendation scope is cached, not an unfiltered second
    # catalog. Providers may derive listing defaults from model/default_model.
    # Conservatively retain those constructor inputs without copying their
    # policy. Scope edits never change the saved account binding or exact ID.
    return fingerprint(['provider-catalog-v8',
        connection_key(workspace,module,config,source,home=home,_credential_generation=True),
        scope,
        generation_identity(home) if home is not None else None])


def mounted_catalog_keys(home,workspace,rows,sources,*,binding=False):
    identity = connection_key if binding else configuration_key
    return {row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-'):
        identity(workspace,row['module'],row.get('config',{}),
            sources[row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')],home=home)
        for row in rows}


def model_key(identity):
    return ('providers.models', identity)

def configured_binding(home, workspace, instance, *, global_only=False):
    from .setup import SetupManager
    manager = SetupManager(home, global_only=global_only, allow_missing_workspace=True)
    with manager.read_snapshot(workspace):
        if not any(row['id'] == instance and row['enabled'] for row in manager.provider_rows(workspace)):
            return None
        return manager.catalog_key({'id':instance}, workspace, binding=True)


def conversation_bindings(home, workspace, session_id, providers, *, global_only=False):
    """Report known conversation overrides separately from the shared inventory.

    No bundle preparation, provider import, transcript or worker is needed.
    The actual mount repeats the exact-identity check before input admission.
    """
    from .host.config import load_config
    config = load_config(workspace, home=home, session_id=session_id, global_only=global_only)
    rows = config.configured_connections
    path = Path(home)/'sessions'/session_id/'configuration.json'
    if path.exists():
        saved = json.loads(path.read_text())
        if 'providers' in saved:
            rows = saved['providers']
    by_id = {row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-'):row for row in rows}
    result = {}
    for provider in providers:
        row = by_id.get(provider['id'])
        if row is None or row.get('enabled') is False:
            result[provider['id']] = {'eligible':False,'reason':'This conversation does not include this configured connection.'}
            continue
        key = connection_key(workspace,row['module'],row.get('config',{}),
            row.get('source') or config.module_sources.get(row['module']),home=home)
        if key != provider.get('connectionKey'):
            result[provider['id']] = {'eligible':False,
                'reason':'This conversation overrides this connection. Its saved account or endpoint differs from the configured connection.'}
    return result


def require_mounted_binding(coordinator, instance, expected, *, catalog=False):
    if not expected:
        return
    captured = coordinator.get_capability('web.provider_catalog') or {}
    if captured.get('keys' if catalog else 'connectionKeys',{}).get(instance) != expected:
        raise ValueError('The mounted connection differs from the selected configured connection. No input was sent; review the conversation connection override.')
    if not catalog and captured.get('workspace') is not None:
        rows = coordinator.config.get('providers', [])
        row = next((row for row in rows if (
            row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')) == instance), None)
        current = connection_key(captured['workspace'], row['module'], row.get('config', {}),
            captured['sources'].get(instance), home=captured.get('home')) if row else None
        if current != expected:
            raise ValueError('The saved connection credentials changed or are unavailable. No input was sent; review the connection and sign in again if needed.')


class ProviderCatalog:
    def __init__(self, path=None, *, ttl=None, retry_after=None, clock=time.time,
                 max_entries=128, max_bytes=3_900_000, max_age=None):
        self.entries = {}
        self.loaded_at = {}
        self.failed_at = {}
        self.pending = {}
        self.load_limit = asyncio.Semaphore(3)
        self.path = Path(path) if path else None
        self.ttl, self.retry_after, self.clock = ttl, retry_after, clock
        self.saved = {}
        self.max_entries, self.max_bytes, self.max_age = max_entries, max_bytes, max_age
        self.sizes = {}
        if self.path:
            try:
                if self.path.stat().st_size <= 4_000_000:
                    data = json.loads(self.path.read_text())
                    if data.get('version') == 2:
                        self.saved = {key: row for key, row in data.get('entries', {}).items()
                                      if isinstance(row, dict) and isinstance(row.get('at'), (int, float))
                                      and (row.get('result') is None or isinstance(row.get('result'), dict))
                                      and self._retained(row['at'])}
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        self._trim()

    def _retained(self, at):
        return self.max_age is None or 0 <= self.clock() - at < self.max_age

    def _forget(self, key):
        self.entries.pop(key, None)
        self.loaded_at.pop(key, None)
        self.failed_at.pop(key, None)
        self.sizes.pop(key, None)

    def _trim(self):
        """Bound reusable discovery data, including direct puts and disk restores.

        Pending callers own their task's result even if it is too large to cache.
        Default retention is size bounded, not time based. A configured identity
        never expires merely because a day elapsed or the host restarted.
        """
        now = self.clock()
        # One identity budget spans lazy disk entries and resident failures.
        candidates = {identity: (row['at'], len(json.dumps(row).encode()) + len(identity) + 8)
                      for identity, row in self.saved.items()}
        for key in self.entries:
            identity = fingerprint(key)
            at = max(self.loaded_at.get(key, 0), self.failed_at.get(key, 0))
            candidates[identity] = (at, max(self.sizes.get(key, 0), candidates.get(identity, (0, 0))[1]))
        retained, size = set(), len(json.dumps({'version': 2, 'entries': {}}).encode())
        for identity, (at, cost) in sorted(reversed(list(candidates.items())), key=lambda item: item[1][0], reverse=True):
            if self._retained(at) and len(retained) < self.max_entries and size + cost <= self.max_bytes:
                retained.add(identity)
                size += cost
        self.saved = {identity: row for identity, row in self.saved.items()
                      if identity in retained and self._retained(row['at'])}
        for key in list(self.entries):
            if fingerprint(key) not in retained:
                self._forget(key)

    def peek(self, key):
        at = self.loaded_at.get(key, self.failed_at.get(key))
        if at is not None and not self._retained(at):
            self._forget(key)
            self.saved.pop(fingerprint(key), None)
        if key not in self.entries:
            row = self.saved.get(fingerprint(key))
            if row and self._retained(row['at']):
                self.entries[key] = (copy.deepcopy(row['result']), row.get('error'))
                if row.get('loadedAt') is not None:
                    self.loaded_at[key] = row['loadedAt']
                if row.get('failedAt') is not None:
                    self.failed_at[key] = row['failedAt']
                self.sizes[key] = len(json.dumps(row).encode()) + len(fingerprint(key)) + 8
            elif row:
                self.saved.pop(fingerprint(key), None)
        value, _ = self.entries.get(key, (None, None))
        return copy.deepcopy(value)

    def fresh(self, key):
        self.peek(key)
        return self._fresh(key)

    def _fresh(self, key):
        """Inspect freshness without copying the inventory a second time."""
        return key not in self.failed_at and key in self.loaded_at and (self.ttl is None or 0 <= self.clock() - self.loaded_at[key] < self.ttl)

    def discard(self, key):
        self._forget(key)
        self.saved.pop(fingerprint(key), None)
        self._persist()

    def put(self, key, result):
        """Accept an explicitly refreshed result with the same TTL as a load."""
        self.entries[key] = (copy.deepcopy(result), None)
        self.loaded_at[key] = self.clock()
        self.failed_at.pop(key, None)
        self.saved[fingerprint(key)] = {'at': self.loaded_at[key], 'loadedAt': self.loaded_at[key], 'result': copy.deepcopy(result)}
        self.sizes[key] = len(json.dumps(self.saved[fingerprint(key)]).encode()) + len(fingerprint(key)) + 8
        self._trim()
        self._persist()

    def _persist(self):
        if not self.path:
            return
        try:
            from .host.config import write_private
            write_private(self.path, json.dumps({'version': 2, 'entries': self.saved}))
        except OSError:
            pass  # A read-only cache must never prevent using a provider.

    async def get(self, key, loader, refresh=False):
        self.peek(key)
        if not refresh:
            if key in self.failed_at and (self.retry_after is None or self.clock() - self.failed_at[key] < self.retry_after):
                _, error = self.entries[key]
                raise ValueError(error)
            if self.fresh(key):
                return self.peek(key)
        if key not in self.pending:
            async def load():
                try:
                    async with self.load_limit:
                        result = await loader()
                    self.put(key, result)
                    return result
                except CatalogInvalidated:
                    self.discard(key)
                    raise
                except Exception as exc:
                    message, diagnostic = public_failure(exc)
                    self.entries[key] = (self.peek(key), message)
                    self.failed_at[key] = self.clock()
                    self.sizes[key] = len(json.dumps(self.entries[key]).encode()) + len(fingerprint(key)) + 8
                    self.saved[fingerprint(key)] = {
                        'at': self.failed_at[key], 'failedAt': self.failed_at[key],
                        'loadedAt': self.loaded_at.get(key),
                        'result': self.peek(key), 'error': message,
                        'diagnostic': diagnostic}
                    self._trim()
                    self._persist()
                    raise
                finally:
                    self.pending.pop(key, None)
            self.pending[key] = asyncio.create_task(load())
        return copy.deepcopy(await asyncio.shield(self.pending[key]))

    def entry(self, key):
        """One presentation shape for Settings, new chats and existing chats."""
        value = self.peek(key) or {}
        error = self.entries.get(key, (None, None))[1]
        return {
            'sharedCatalogKey': key[1], 'models': value.get('models', []),
            'metadata': value.get('providerMetadata'),
            'supported': value.get('modelsSupported', False),
            'provenance': value.get('modelsProvenance'),
            'diagnostic': self.saved.get(fingerprint(key), {}).get('diagnostic'),
            'loadedAt': self.loaded_at.get(key), 'failedAt': self.failed_at.get(key),
            'stale': key in self.loaded_at and not self._fresh(key),
            'phase': 'error' if error else 'ready' if key in self.loaded_at else 'unloaded',
            **({'error': error} if error else {}),
        }

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
