"""Provider and routing editors for shared Amplifier configuration."""
from __future__ import annotations
import copy
from contextlib import contextmanager
import asyncio
import json
import signal
import uuid
import time
import shutil
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit,urlunsplit,parse_qsl
import os
from pathlib import Path
import re
import shlex
import yaml
from filelock import FileLock
from .preferences import SettingsStore
from .shared_settings import atomic_write, routing_dirs, overlay
from .host.config import credential_environment_source, expand_environment, load_config, write_private
from .bundles import SECRET_KEYS, validate_uri

KNOWN_PROVIDER_SOURCES=json.loads(Path(__file__).with_name('provider_sources.json').read_text())

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')

ROUTING_BUNDLE = 'git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main'

ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
PROVIDER_ENV = {
    'provider-openai': ('OPENAI_API_KEY',),
    'provider-jev': ('TYPESAFE_API_KEY',),
    'provider-local-decisions': ('LOCAL_DECISIONS_API_KEY',),
    'provider-azure-openai': ('AZURE_OPENAI_API_KEY',),
    'provider-chat-completions': ('CHAT_COMPLETIONS_API_KEY',),
    'provider-anthropic': ('ANTHROPIC_API_KEY',),
    'provider-gemini': ('GOOGLE_API_KEY','GEMINI_API_KEY'),
    'provider-github-copilot': ('GITHUB_TOKEN','COPILOT_AGENT_TOKEN','COPILOT_GITHUB_TOKEN','GH_TOKEN'),
}

def credential_field(module):
    return 'github_token' if module=='provider-github-copilot' else 'api_key'


def key_preview(module, value, env_name, *, configured=False):
    """Identify the service's configured key, never serialize a complete secret.

    This is configuration for a new provider, not a claim about an already
    mounted conversation or the validity of the credential at its issuer.
    """
    if module == 'provider-openai-chatgpt':
        return None  # OAuth access/refresh tokens are not API keys.
    # Display the mounted provider's fallback order, which can differ from its
    # preferred setup variable. Explicit Copilot config is promoted by Unified
    # into COPILOT_AGENT_TOKEN before mounting. Explicit API keys take
    # precedence over ambient defaults, including Compatible API.
    if configured:
        if not value:
            defaults = {
                'provider-github-copilot': ('COPILOT_AGENT_TOKEN', 'COPILOT_GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN'),
                'provider-azure-openai': ('AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_KEY'),
                'provider-ollama': ('OLLAMA_API_KEY',),
                'provider-vllm': ('VLLM_API_KEY',),
            }.get(module, PROVIDER_ENV.get(module, ()))
            env_name = next((name for name in defaults if os.environ.get(name)), env_name)
    source = 'configuration'
    reference = re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', value) if isinstance(value, str) else None
    if reference:
        env_name = reference.group(1)
    elif value:
        env_name = ''
    if env_name:
        source = credential_environment_source(env_name)
        value = os.environ.get(env_name)
    elif isinstance(value, str):
        try:
            value = expand_environment(value)
        except ValueError:
            value = None
    elif not value:
        return {'status': 'provider-managed', 'source': 'provider-managed', 'masked': None, 'envVar': ''}
    available = isinstance(value, str) and bool(value)
    # Short or unusual credentials get no characters exposed. Never include
    # length, a reusable digest, or a reveal/copy-full-key path in public state.
    masked = None
    if available:
        masked = value[:6] + '…' + value[-4:] if len(value) >= 16 and value.isascii() and value.isprintable() else '••••'
    return {'status': 'available' if available else 'missing', 'source': source,
            'masked': masked, 'envVar': env_name}


def environment_credential(module,raw=None,env_var=None):
    configured=raw is not None and env_var is None
    raw=raw or {}
    defaults=PROVIDER_ENV.get(module,())
    value=raw.get(credential_field(module))
    reference=re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',value) if isinstance(value,str) else None
    if env_var is not None and (not isinstance(env_var,str) or not ENV_NAME.fullmatch(env_var)):
        raise ValueError('Use an environment variable name containing letters, numbers and underscores, starting with a letter or underscore.')
    chosen=env_var or (reference.group(1) if reference else next((name for name in defaults if os.environ.get(name)),defaults[0] if defaults else ''))
    return {'module':module,'field':credential_field(module),'defaultEnvVar':defaults[0] if defaults else '',
            'alternatives':list(defaults[1:]),'envVar':chosen,'available':bool(chosen and os.environ.get(chosen)),
            'explicit':bool(reference),'hasStoredKey':bool(value and not reference),
            'supported':module!='provider-openai-chatgpt',
            'preview':key_preview(module,value,chosen,configured=configured)}

def github_cli_token():
    """Read host CLI credentials only; never return them to interface state/logs."""
    binary=shutil.which('gh')
    if not binary:return None
    try:
        result=subprocess.run([binary,'auth','token','--hostname','github.com'],capture_output=True,text=True,timeout=4,check=False)
        token=result.stdout.strip()
        return token if result.returncode==0 and token and len(token)<16000 else None
    except (OSError,subprocess.TimeoutExpired):return None


def chatgpt_auth_mode(value=None):
    # The previous public spelling remains a read alias; new choices use Codex.
    return 'chatgpt_codex' if value in (None, 'legacy_codex') else value


def chatgpt_token_path(config):
    default='~/.amplifier/chatgpt-plan/default.json' if config.get('auth_mode')=='chatgpt_plan' else '~/.amplifier/openai-chatgpt-oauth.json'
    value=config.get('token_file_path')
    return default if value is None else value


def account_status(config):
    # Match the provider's token-file contract, without starting a login or refresh.
    mode=chatgpt_auth_mode(config.get('auth_mode'))
    status={'authMode':mode,'connected':False,'planEnabled':False}
    path=chatgpt_token_path(config)
    if not isinstance(path,str):return status
    try:
        file=Path(path).expanduser()
        if file.stat().st_size>1_000_000:return status
        tokens=json.loads(file.read_text())
        if not isinstance(tokens,dict) or not tokens.get('access_token'):return status
        stored_mode=chatgpt_auth_mode(tokens.get('auth_mode'))
        if stored_mode=='oauth':stored_mode='chatgpt_codex'
        if mode not in {'chatgpt_codex','chatgpt_plan'} or stored_mode!=mode:return status
        if mode=='chatgpt_plan':
            if not tokens.get('subject') or not tokens.get('client_id'):return status
            connected=bool(tokens.get('refresh_token')) or float(tokens.get('expires_at',0))>time.time()
            status['refreshable']=bool(tokens.get('refresh_token')) and float(tokens.get('expires_at',0))<=time.time()
            status.update(connected=connected,planEnabled=connected and isinstance(tokens.get('scopes'),list) and 'chatgpt.tokens.use.direct' in tokens['scopes'])
            # Identity was verified by the provider at sign-in. Never decode or
            # publish a JWT from the app process just to render settings.
            if connected and isinstance(tokens.get('email'),str):status['email']=tokens['email'][:320]
        else:
            connected=bool(tokens.get('refresh_token'))
            if not connected:
                expiry=datetime.fromisoformat(tokens.get('expires_at','').replace('Z','+00:00'))
                connected=expiry.astimezone(timezone.utc)>datetime.now(timezone.utc)
            status['connected']=connected
            try:
                expiry=datetime.fromisoformat(tokens.get('expires_at','').replace('Z','+00:00'))
                if expiry.tzinfo is None:expiry=expiry.replace(tzinfo=timezone.utc)
                status['refreshable']=bool(tokens.get('refresh_token')) and expiry.timestamp()<=time.time()
            except (ValueError,TypeError):status['refreshable']=bool(tokens.get('refresh_token'))
        return status
    except (OSError,ValueError,TypeError,OverflowError,AttributeError):return status


def account_connected(config):
    return account_status(config)['connected']


def login_url(value):
    """Only expose authorization navigation, never tokens or identity hints."""
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or parsed.hostname not in {'auth.openai.com','chatgpt.com','platform.openai.com'} or parsed.username or parsed.password or parsed.fragment:return None
        if any(key.lower() in {'id_token_hint','access_token','refresh_token','id_token','code','client_secret'} for key,_ in parse_qsl(parsed.query)):return None
        return value
    except ValueError:return None


def safe_name(value):
    if not isinstance(value,str) or not NAME.fullmatch(value) or '..' in value:
        raise ValueError('Use a name containing letters, numbers, dots, dashes, or underscores.')
    return value

def redact(value):
    if isinstance(value,dict):
        return {k: ('[REDACTED]' if k.lower().replace('-','_') in SECRET_KEYS or k.lower().endswith(('_key','_token','_secret','_password')) else redact(v)) for k,v in value.items()}
    if isinstance(value,list): return [redact(v) for v in value]
    return value

def public_source(value):
    if isinstance(value,str) and value.startswith(('https://','http://','git+https://')):
        parsed=urlsplit(value.removeprefix('git+'))
        if parsed.username or parsed.password or parsed.query:
            return ('git+' if value.startswith('git+') else '')+urlunsplit((parsed.scheme,parsed.hostname or '',parsed.path,'',parsed.fragment))
    return value


def validate_matrix(value):
    if not isinstance(value,dict) or not isinstance(value.get('roles'),dict):
        raise ValueError('The routing matrix must contain a roles mapping.')
    for required in ('general','fast'):
        if required not in value['roles']: raise ValueError('The routing matrix requires the '+required+' role.')
    for role,row in value['roles'].items():
        safe_name(role)
        if not isinstance(row,dict) or not isinstance(row.get('description'),str) or not isinstance(row.get('candidates'),list) or not row['candidates']:
            raise ValueError('Each role needs a description and at least one candidate.')
        for candidate in row['candidates']:
            if not isinstance(candidate,dict) or not isinstance(candidate.get('provider'),str) or not isinstance(candidate.get('model'),str):
                raise ValueError('Every candidate needs a provider and model; base is only valid in overrides.')
            if not candidate['provider'] or not candidate['model']: raise ValueError('Provider and model cannot be blank.')
            if 'config' in candidate and not isinstance(candidate['config'],dict): raise ValueError('Candidate config must be a mapping.')
    return copy.deepcopy(value)

class SetupManager:
    def __init__(self,home,*,store=None,runtime_operation=None,progress=None,auth_command=None,probe_command=None,catalog=None,allow_missing_workspace=False,global_only=False):
        from .provider_catalog import ProviderCatalog
        self.catalog=catalog or ProviderCatalog()
        self.global_only=global_only
        self.allow_missing_workspace=allow_missing_workspace
        self.home=Path(home); self.store=store or SettingsStore(home)
        self.runtime_operation=runtime_operation
        self.progress=progress; self.auth_command=auth_command; self.probe_command=probe_command; self.logins={}

    def config(self,workspace):
        snapshot=getattr(self,'_config_snapshot',None)
        if snapshot and snapshot[0]==workspace:return snapshot[1]
        if self.allow_missing_workspace:
            workspace=Path(workspace).expanduser().resolve()
            while not workspace.is_dir() and workspace.parent!=workspace:
                workspace=workspace.parent
        return load_config(workspace,home=self.home,global_only=self.global_only)

    @contextmanager
    def read_snapshot(self,workspace):
        """Reuse one config only within a synchronous inventory computation."""
        previous=getattr(self,'_config_snapshot',None)
        self._config_snapshot=(workspace,self.config(workspace))
        try:yield
        finally:self._config_snapshot=previous

    def provider_rows(self,workspace):
        config=self.config(workspace)
        rows=[]
        for value in config.providers:
            identity=value.get('id') or value.get('instance_id') or value['module'].removeprefix('provider-')
            raw=value.get('config',{})
            refs=[]
            def scan(node):
                if isinstance(node,dict):
                    for key,item in node.items():
                        if key.lower().replace('-','_') in SECRET_KEYS or key.lower().endswith(('_api_key','_token','_secret','_password')):
                            refs.append(item)
                        else: scan(item)
            scan(raw)
            credential=environment_credential(value['module'],raw)
            configured=bool(refs) and all(isinstance(v,str) and bool(v) and (not v.startswith('${') or bool(os.environ.get(v[2:-1]))) for v in refs)
            if not refs:configured=credential['available']
            account=account_status(raw) if value['module']=='provider-openai-chatgpt' else None
            from .provider_health import issue
            needs_auth=issue(self.home,identity,value['module'],raw)
            if account and needs_auth:account.update(connected=False,needsAttention=True)
            connected=bool(account and account['connected'])
            if account:configured=connected and (account['authMode']!='chatgpt_plan' or account['planEnabled'])
            rows.append({'credential':credential,'id':identity,'module':value['module'],'source':public_source(value.get('source')),'config':redact(raw),
                'authenticationRequired':needs_auth,'account':account,'accountConnected':connected,'credentialsConfigured':configured,'keySource':'environment' if (not refs and credential['available']) or any(isinstance(v,str) and v.startswith('${') for v in refs) else ('configuration' if refs else 'provider-managed'), 'enabled':value.get('enabled',True) and identity not in config.settings.get('configurator',{}).get('disabled',{}).get('providers',[])})
        return rows

    def catalog_key(self,args,workspace):
        from .provider_catalog import configuration_key
        config=self.config(workspace)
        row=next((row for row in config.providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==args.get('id')),None)
        module=args.get('module') or (row or {}).get('module')
        raw=(row or {}).get('config',{})
        return configuration_key(workspace,module,raw,getattr(config,'module_sources',{}).get(module) or (row or {}).get('source'),home=self.home)

    async def cached_probe(self,action,args,workspace):
        from .provider_catalog import model_key
        identity=self.catalog_key(args,workspace)
        key=model_key(identity) if action=='providers.models' else (action,identity)
        if action=='providers.schema' and not args.get('refresh') and self.catalog.fresh(model_key(identity)):
            cached=self.catalog.peek(model_key(identity))
            if cached.get('providerMetadata'):
                return {'providerMetadata':cached['providerMetadata']}
        result=await self.catalog.get(key,lambda:self.probe(action,args,workspace),refresh=args.get('refresh',False))
        if identity!=self.catalog_key(args,workspace):
            self.catalog.discard(key)
            raise ValueError('Provider configuration changed during discovery. The new configuration is being refreshed.')
        if action=='providers.models':
            result={**result,'modelsProviderId':args['id'],'modelsSupported':result.get('modelsSupported',result.get('supported',True))}
        elif action=='providers.imageModels':
            result={**result,'imageModelsProviderId':args['id']}
        return result

    async def probe(self,action,args,workspace):
        from .runtime import RuntimeManager
        configured=self.config(workspace)
        row=next((row for row in configured.providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==args.get('id')),None)
        module=args.get('module') if action=='providers.schema' else (row or {}).get('module')
        if not module:raise ValueError('Save this provider before discovering models or testing it.')
        safe_name(module)
        raw=(row or {}).get('config',{}) if row and row['module']==module else {}
        # Metadata may need this exact instance's endpoint, never its secrets.
        # The isolated probe passes it only to a declared constructor argument.
        config=({key:copy.deepcopy(raw[key]) for key in ('base_url',) if key in raw}
                if action=='providers.schema' else copy.deepcopy(raw))
        if action!='providers.schema':
            credential=environment_credential(module,raw)
            field=credential['field']
            if not config.get(field) and credential['available']:config[field]='${'+credential['envVar']+'}'
        from .provider_health import generation, record
        credential_generation=generation(module,raw)
        command=self.probe_command or RuntimeManager()._command()[:-1]+[str(Path(__file__).with_name('provider_probe.py'))]
        env={**os.environ,'AMPLIFIER_WEB_HOME':str(self.home)}
        if module=='provider-github-copilot' and config.get('github_token'):env['COPILOT_AGENT_TOKEN']=config['github_token']
        probe_workspace=Path(workspace)
        while not probe_workspace.is_dir() and probe_workspace.parent!=probe_workspace:
            probe_workspace=probe_workspace.parent
        from .probe_diagnostics import communicate, stderr_tail, failure_detail
        from .probe_process import spawn, stop
        process=await spawn(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,cwd=probe_workspace)
        stderr_task=asyncio.create_task(stderr_tail(process.stderr))
        try:
            try:
                output,stderr=await asyncio.wait_for(asyncio.gather(communicate(process,json.dumps({'action':action,'model':args.get('model'),**({'prompt':args['prompt']} if action=='naming.complete' else {}),'module':module,'config':config,'source':getattr(configured,'module_sources',{}).get(module) or (row or {}).get('source'),'fallbackSource':KNOWN_PROVIDER_SOURCES.get(module),'registryHome':str(getattr(configured,'registry_home',self.home/'foundation'))}).encode()),stderr_task),90)
            except TimeoutError:
                raise ValueError('Provider check timed out after 90 seconds. Check connectivity and credentials, then retry.') from None
            try:result=json.loads(output)
            except (ValueError,UnicodeError):
                raise ValueError(
                    f'The provider check ended without a valid result (exit code {process.returncode}). '
                    + failure_detail(stderr)
                ) from None
            if action!='providers.schema' and generation(module,raw)==credential_generation:
                if not result.get('error') or result.get('authenticationRequired') is True:
                    record(self.home,args['id'],credential_generation,authentication_required=result.get('authenticationRequired') is True)
            if result.get('error'):raise ValueError(result['error'])
            if process.returncode:raise ValueError(f'The provider check could not finish (exit code {process.returncode}). '+failure_detail(stderr))
            if action=='naming.complete':
                if result.get('naming',{}).get('error'):raise ValueError(result['naming']['error'])
                return {'text':result.get('naming',{}).get('text','')}
            metadata={'module':module,'info':result['info'],'configSchema':result['configSchema'],'imageGeneration':result.get('imageGeneration')}
            response={'providerMetadata':metadata}
            if action=='providers.models':response.update(models=result.get('models',[]),modelsProviderId=args['id'],modelsSupported=result.get('modelsSupported',True))
            if action=='providers.imageModels':response.update(imageModels=result.get('imageModels',[]),imageModelsProviderId=args['id'],imageModelsSupported=result.get('imageModelsSupported',False))
            if action=='providers.testMessage':response['messageTest']={**result['messageTest'],'providerId':args['id'],'checkedAt':time.time()}
            if action=='providers.test':response['test']={**result['test'],'providerId':args['id']}
            return response
        finally:
            await stop(process, stderr_task)

    def _keys(self,updates):
        from .configuration_authority import check_current
        check_current()
        path=self.store.shared_home/'keys.env'
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with FileLock(str(path)+'.lock',timeout=10):
            lines=path.read_text().splitlines() if path.exists() else []
            names=set(updates)
            lines=[line for line in lines if line.removeprefix('export ').split('=',1)[0].strip() not in names]
            lines.extend(name+'='+shlex.quote(value) for name,value in updates.items())
            atomic_write(path,'\n'.join(lines)+'\n',private=True)
        from .host.config import _KEY_FILE_VALUES
        _KEY_FILE_VALUES.update(updates)
        # A long-running backend must see edited keys immediately. Child host
        # generations inherit these values; no browser state includes them.
        os.environ.update(updates)

    def _provider_mutation(self,args,workspace,scope,remove=False,*,image_mode=None):
        from .provider_catalog import fingerprint,configuration_key
        identity=safe_name(args.get('id') or args.get('module','').removeprefix('provider-'))
        effective=self.config(workspace)
        def signature(config):
            return fingerprint([config.providers,
                [configuration_key(workspace,row['module'],row.get('config',{}),config.module_sources.get(row['module']) or row.get('source'),home=self.home) for row in config.providers],
                config.settings.get('configurator',{}).get('disabled',{}).get('providers',[]), config.settings.get('image_generation')])
        before=signature(effective)
        existing=next((row for row in effective.providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==identity),None)
        def mutate(settings):
            if image_mode is not None:
                settings['image_generation']={'mode':image_mode}
            rows=settings.setdefault('config',{}).setdefault('providers',[])
            index=next((i for i,row in enumerate(rows) if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==identity),None)
            if remove:
                if not existing: raise ValueError('Provider instance does not exist.')
                row={'id':identity,'module':existing['module']}
            else:
                module=safe_name(args.get('module') or (existing or {}).get('module',''))
                if not module.startswith('provider-'): raise ValueError('Choose a provider module.')
                config=copy.deepcopy(args.get('config',{}))
                if not isinstance(config,dict): raise ValueError('Provider config must be a mapping.')
                old=(existing or {}).get('config',{})
                updates={}
                def private(node,path=()):
                    result={}
                    for key,value in node.items():
                        normalized=key.lower().replace('-','_')
                        secret=normalized in SECRET_KEYS or normalized.endswith(('_api_key','_token','_secret','_password'))
                        if secret and value in ('[REDACTED]','<redacted>'):
                            original=old
                            for part in (*path,key): original=original.get(part) if isinstance(original,dict) else None
                            if original is not None: result[key]=original
                        elif secret and value is not None and value!='' and not (isinstance(value,str) and re.fullmatch(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}',value)):
                            if not isinstance(value,str): raise ValueError('Credentials must be strings or environment references.')
                            env='AMPLIFIER_'+re.sub('[^A-Za-z0-9]','_',identity+'_'+ '_'.join((*path,key))).upper()
                            updates[env]=value; result[key]='${'+env+'}'
                        elif isinstance(value,dict): result[key]=private(value,(*path,key))
                        else: result[key]=value
                    return result
                field=credential_field(module)
                if args.get('apiKeyEnv') is not None:
                    if args.get('apiKey'):raise ValueError('Choose an environment variable or enter a private key, not both.')
                    credential=environment_credential(module,env_var=args['apiKeyEnv'])
                    if not credential['supported']:raise ValueError('This provider uses account sign-in instead of an API key.')
                    config[field]='${'+credential['envVar']+'}'
                elif args.get('apiKey') is not None:config[field]=args['apiKey']
                elif not config.get(field):
                    if old.get(field):config[field]=old[field]
                    else:
                        credential=environment_credential(module)
                        if credential['supported'] and credential['available']:config[field]='${'+credential['envVar']+'}' 
                if module=='provider-openai-chatgpt':
                    same_provider=existing is not None and existing['module']==module
                    if same_provider and 'auth_mode' not in config and old.get('auth_mode'):
                        config['auth_mode']=old['auth_mode']
                    token_path=config.get('token_file_path') or (old.get('token_file_path') if same_provider else None)
                    if token_path:
                        config['token_file_path']=token_path
                    elif same_provider:
                        # Legacy connections already use the provider's default OAuth
                        # file. Editing their model must not select a different account.
                        config.pop('token_file_path',None)
                    else:
                        # New accounts get distinct sign-ins, even if legacy tokens exist.
                        config['token_file_path']=str(self.store.shared_home/('openai-chatgpt-'+identity+'-oauth.json'))
                    config['login_on_mount']=False
                row={'id':identity,'module':module,'config':private(config)}
                source=args.get('source') or (existing or {}).get('source') or (KNOWN_PROVIDER_SOURCES.get(module) if module in {'provider-chat-completions','provider-azure-openai','provider-ollama','provider-vllm'} else None)
                if source:
                    # A model/config edit can retain a local source already
                    # configured through Sources. Only new source selections
                    # pass through this surface's URI admission policy.
                    unchanged_source = existing and existing['module'] == module and existing.get('source') == source
                    row['source'] = source if unchanged_source else validate_uri(source)
                if updates:self._keys(updates)
            if index is None:rows.append(row)
            else:rows[index]=row
            disabled=set(overlay(effective.settings,settings).get('configurator',{}).get('disabled',{}).get('providers',[]))
            if remove:disabled.add(identity)
            else:disabled.discard(identity)
            settings.setdefault('configurator',{}).setdefault('disabled',{})['providers']=sorted(disabled)
            # Clear the older Unified-only flag when editing an existing entry.
            settings.get('overrides',{}).get(identity,{}).pop('enabled',None)
        self.store.update(workspace,scope,mutate)
        with self.read_snapshot(workspace):
            current=self.config(workspace)
            changed=before!=signature(current)
            return {'providers':self.provider_rows(workspace),'takesEffect':'new_sessions','scope':scope,'configurationChanged':changed}

    def _routing_dirs(self,workspace):
        # Same first-hit precedence as the mounted routing hook.
        registry=getattr(self.config(workspace),'registry_home',self.home/'foundation')
        dirs=routing_dirs(workspace,shared_home=self.store.shared_home,global_only=self.global_only)
        from .host.bundle_paths import local_bundle_path
        config=self.config(workspace)
        selected=config.resolve_source(ROUTING_BUNDLE) or ROUTING_BUNDLE
        local=local_bundle_path(config,selected)
        if local:
            root=local if local.is_dir() else local.parent
            dirs.append(root/'routing')
        else:
            dirs.extend(sorted((registry/'cache').glob('amplifier-bundle-routing-matrix-*/routing')))
        return dirs

    async def ensure_routing_catalog(self,workspace):
        """Resolve the app's routing dependency once; subsequent reads use its cache.

        Never copy curated matrices into user configuration or manufacture roles.
        Foundation handles source overrides and cached repository resolution.
        """
        if any((directory/'balanced.yaml').is_file() for directory in self._routing_dirs(workspace)
               if directory not in routing_dirs(workspace,shared_home=self.store.shared_home,global_only=self.global_only)):
            return
        from .host.session import session_registry,load_configured_bundle
        config=self.config(workspace)
        async with asyncio.timeout(30):
            await load_configured_bundle(session_registry(config),config,ROUTING_BUNDLE)


    def routing(self,workspace):
        active=self.config(workspace).settings.get('routing',{}).get('matrix','balanced')
        rows=[]; seen=set(); roles=set()
        directories=self._routing_dirs(workspace)
        for directory in directories:
            for path in sorted(directory.glob('*.yaml')):
                if path.is_symlink() or path.stat().st_size>256*1024:continue
                name=path.stem
                if name in seen:continue
                try:
                    value=yaml.safe_load(path.read_text());validate_matrix(value)
                except (ValueError,yaml.YAMLError):continue
                seen.add(name);roles.update(value['roles'])
                rows.append({'name':name,'description':value.get('description',''),'source':'custom' if directory in routing_dirs(workspace,shared_home=self.store.shared_home,global_only=self.global_only) else 'bundle','active':active==name})
        return {'matrices':rows,'active':active,'roles':sorted(roles)}

    def matrix(self,workspace,name):
        safe_name(name)
        for directory in self._routing_dirs(workspace):
            path=directory/(name+'.yaml')
            if path.exists() and not path.is_symlink() and path.stat().st_size<=256*1024:
                return {**validate_matrix(yaml.safe_load(path.read_text())),'name':name}
        raise ValueError('The routing matrix does not exist.')

    def effective_routing(self, workspace, session_id=None, require_mounted=False):
        """Resolved routing dependency, or None when this owner is uncertain.

        A bundle can choose its own default. Never guess balanced for an
        already-mounted owner whose effective hook is unavailable.
        """
        previous = getattr(self, '_config_snapshot', None)
        try:
            config = load_config(workspace, home=self.home, session_id=session_id) if session_id else self.config(workspace)
            self._config_snapshot = (workspace, config)
            routing = copy.deepcopy(config.settings.get('routing', {}))
            name = routing.get('matrix')
            if session_id:
                override = self.home / 'sessions' / session_id / 'configuration.json'
                if override.exists():
                    plan = json.loads(override.read_text())
                    hook = next((row for row in plan.get('hooks', []) if row.get('module') == 'hooks-routing'), None)
                    if 'hooks' in plan and hook is None:
                        return {'routing': None, 'matrix': None}
                    if hook and (hook.get('config', {}).get('default_matrix', 'balanced') != (name or 'balanced')
                                 or hook.get('config', {}).get('custom_routing_dirs') !=
                                 [str(path) for path in routing_dirs(workspace, shared_home=self.store.shared_home)]):
                        # Saved runtime plans replace the normal merged hook.
                        # Do not misclassify that independently selected source
                        # as an unused preset; conservatively refresh its owner.
                        return None
            if not name and session_id:
                path = self.home / 'sessions' / session_id / 'effective-configuration.json'
                if path.exists():
                    plan = json.loads(path.read_text())
                    hook = next((row for row in plan.get('hooks', []) if row.get('module') == 'hooks-routing'), None)
                    if hook is None:
                        return {'routing': routing, 'matrix': None}
                    name = hook.get('config', {}).get('default_matrix', 'balanced')
                elif require_mounted:
                    return None
            if not name:
                hooks = config.settings.get('modules', {}).get('hooks', []) + config.settings.get('config', {}).get('hooks', [])
                name = next((row.get('config', {}).get('default_matrix') for row in reversed(hooks)
                             if row.get('module') == 'hooks-routing' and row.get('config', {}).get('default_matrix')), 'balanced')
            try:
                matrix = self.matrix(workspace, name)
            except ValueError:
                matrix = None
            if matrix is not None:
                matrix.pop('description', None)
            return {'routing': routing, 'matrix': matrix}
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            # A missing unrelated workspace or broken local preset must not
            # prevent saving valid settings. Retire only this uncertain owner.
            return None
        finally:
            self._config_snapshot = previous

    async def perform(self,action,args):
        workspace=args.get('workspace') or str(Path.cwd());scope=args.get('scope','global')
        self.store.path(workspace,scope) # Validate scope even on reads.
        if action=='providers.credentials':
            cli=bool(await asyncio.to_thread(github_cli_token)) if args['module']=='provider-github-copilot' else False
            self.config(workspace) # Load app-owned keys as well as the launch environment.
            return {'credentialCheck':{**environment_credential(args['module'],env_var=args.get('envVar')), 'githubCliAvailable':cli,'requestedEnvVar':args.get('envVar',''), 'checkedAt':time.time()}}
        if action=='providers.decisionModels':
            from .decision_settings import describe
            effective = self.config(workspace)
            return {'decisionModels': describe(effective.settings, effective.providers)}
        if action=='providers.configureDecisions':
            from .decision_settings import configure
            return configure(self, args, workspace, scope)
        if action=='providers.list':
            from .image_defaults import preference
            config = self.config(workspace)
            return {'providers':self.provider_rows(workspace),'providersWorkspace':str(workspace),'providersLoadedAt':time.time(), 'imagePreference': preference(config.settings, config.providers)}
        if action in {'providers.schema','providers.models','providers.imageModels','providers.test','providers.testMessage'}:
            return await (self.probe(action,args,workspace) if action in {'providers.test','providers.testMessage'} else self.cached_probe(action,args,workspace))
        if action in {'providers.move','providers.reorder'}:
            current=self.config(workspace)
            enabled={row['id'] for row in self.provider_rows(workspace) if row['enabled']}
            rows=[row for row in current.providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-')) in enabled]
            ids=[row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-') for row in rows]
            if action=='providers.reorder':
                ordered=args['ids']
                if args.get('expectedIds')!=ids or len(ordered)!=len(ids) or set(ordered)!=set(ids):
                    raise ValueError('Provider connections changed. Refresh the list before saving order.')
                changed=ordered!=ids
                ids=ordered
            else:
                identity=args['id'];before=args.get('beforeId')
                if identity not in ids or before is not None and before not in ids:raise ValueError('Refresh the provider list before reordering.')
                changed=identity!=before
                if changed:
                    ids.remove(identity);ids.insert(ids.index(before) if before else len(ids),identity)
            if changed:
                def reorder(settings):
                    scoped=settings.setdefault('config',{}).setdefault('providers',[])
                    for priority, key in enumerate(ids,1):
                        source=next(row for row in rows if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==key)
                        row=next((row for row in scoped if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==key),None)
                        if row is None:
                            row={k:source[k] for k in ('id','module') if k in source};scoped.append(row)
                        row.setdefault('config',{})['priority']=priority
                    settings.pop('provider_order',None)
                self.store.update(workspace,scope,reorder)
            return {'providers':self.provider_rows(workspace),'scope':scope}
        if action=='providers.save':
            if args.get('useGitHubCli'):
                if args.get('module')!='provider-github-copilot' or args.get('apiKey') or args.get('apiKeyEnv'):
                    raise ValueError('Choose one credential source for GitHub Copilot.')
                token=await asyncio.to_thread(github_cli_token)
                if not token:raise ValueError('GitHub CLI sign-in is no longer available. Sign in on the Amplifier host or enter a token.')
                args={**args,'apiKey':token}
            return self._provider_mutation(args,workspace,scope)
        if action=='providers.configureImages':
            from .image_setup import configure_images
            return await configure_images(self,args,workspace,scope)
        if action=='providers.finishSetup':
            identity=safe_name(args['id']);model=args['model'].strip()
            if not model:raise ValueError('Choose a model before finishing setup.')
            effective=self.config(workspace)
            row=next((value for value in effective.providers if (value.get('id') or value.get('instance_id') or value['module'].removeprefix('provider-'))==identity),None)
            if not row:raise ValueError('The connection changed. Refresh your connections and try again.')
            # A guided edit changes only the model. Preserve opaque provider fields,
            # credential references and custom routing, including a custom balanced file.
            routing=self.routing(workspace)
            initialize=bool(args.get('initializeRouting') and not effective.settings.get('routing',{}).get('matrix')
                and len([p for p in self.provider_rows(workspace) if p.get('enabled',True)])==1
                and not any(p['name']==routing['active'] and p['source']=='custom' for p in routing['matrices']))
            result=self._provider_mutation({**args,'module':row['module'],'config':{**row.get('config',{}),'default_model':model}},workspace,scope)
            if initialize:
                # The routing bundle owns role definitions and candidate policy.
                self.store.update(workspace,scope,lambda settings:settings.setdefault('routing',{}).update(matrix='balanced'))
                result.update(self.routing(workspace))
            result['setupCompletion']={'id':identity,'model':model,'routingCreated':False,'routingSelected':'balanced' if initialize else None,'takesEffect':'new_sessions'}
            return result
        if action=='providers.remove':return self._provider_mutation(args,workspace,scope,remove=True)
        if action=='providers.loginCancel':return await self.cancel_login(args['id'])
        if action=='providers.loginStatus':return {'providerId':args['id'],'login':self.login_state(args['id'])}
        if action=='providers.login':
            row=next((row for row in self.config(workspace).providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==args['id']),None)
            if row and row['module']=='provider-openai-chatgpt':return await self.start_login(row,args,workspace,scope)
        if action=='providers.login':
            if not self.runtime_operation:raise ValueError('This provider does not expose a standalone sign-in flow.')
            result=await self.runtime_operation('configuration.providerLogin',{'provider':args['id'],'sessionId':args.get('sessionId')})
            return {'providerId':args['id'],'login':{**result,'providerId':args['id']}}
        if action=='routing.list':
            error=None
            try:await self.ensure_routing_catalog(workspace)
            except Exception as exc:
                error='Built-in model rules could not be loaded ('+type(exc).__name__+'). Check connectivity and refresh. Your saved profiles are still available.'
            return {**self.routing(workspace),'routingCatalogError':error}
        if action=='routing.show':
            return {'matrix':self.matrix(workspace,args['name'])}
        if action in {'routing.save','routing.use'}:
            name=safe_name(args['name'])
            if action=='routing.save':
                value=args['matrix']
                if isinstance(value,str):value=yaml.safe_load(value)
                value=validate_matrix(value);value['name']=name
                if redact(value)!=value:raise ValueError('Routing matrices must not contain credentials; configure them on the provider.')
                directory=self.store.shared_home/'routing' if scope=='global' else Path(workspace)/'.amplifier'/('routing.local' if scope=='local' else 'routing')
                def save(settings):
                    atomic_write(directory/(name+'.yaml'),yaml.safe_dump(value,sort_keys=False))
                    if args.get('activate'):settings.setdefault('routing',{})['matrix']=name
                self.store.update(workspace,scope,save)
            else:
                self.matrix(workspace,name)
                def activate(settings):settings.setdefault('routing',{})['matrix']=name
                self.store.update(workspace,scope,activate)
            return {**self.routing(workspace),'matrix':self.matrix(workspace,name),'takesEffect':'new_sessions','scope':scope}
        raise ValueError('Unknown setup operation.')

    def login_state(self,identity):
        row=self.logins.get(identity)
        if not row:return {"providerId":identity,"status":"idle"}
        return {key:value for key,value in row.items() if key not in {"task","process"}}

    async def publish_login(self,identity,**values):
        result={**values,"providerId":identity,"login":self.login_state(identity)}
        if self.progress:await self.progress(result)
        return result

    async def start_login(self,provider,args,workspace,scope):
        from .host_dependencies import MissingHostDependency
        identity=args['id']; previous=self.logins.get(identity)
        if previous and not previous['task'].done():return await self.publish_login(identity)
        original=copy.deepcopy(provider)
        old=original.get('config',{})
        mode=chatgpt_auth_mode(args.get('authMode') or old.get('auth_mode'))
        if mode not in {'chatgpt_codex','chatgpt_plan'}:raise ValueError('Choose a supported ChatGPT sign-in method.')
        login_id=uuid.uuid4().hex
        profile_dir=self.home/'config'/'chatgpt-sign-in'/safe_name(identity)
        path=profile_dir/(login_id+'.json')
        source_path=Path(chatgpt_token_path(old)).expanduser()
        host_path=Path(old.get('host_file_path') or (source_path.parent/'host.json' if old.get('auth_mode')=='chatgpt_plan' else self.home/'config'/'chatgpt-host.json')).expanduser()
        from .provider_catalog import fingerprint
        registration_path=profile_dir/('registration-'+fingerprint([workspace,mode,str(source_path)])[:24]+'.json')
        config={**old,'auth_mode':mode,'token_file_path':str(path),'login_on_mount':False}
        if mode=='chatgpt_plan':config['host_file_path']=str(host_path)
        before=account_status(old)
        row={'providerId':identity,'loginId':login_id,'authMode':mode,'status':'starting','instructions':[]}
        self.logins[identity]=row
        async def run():
            process=None;completed=False;activated=False
            try:
                if self.auth_command:command=list(self.auth_command)
                else:
                    from .runtime import RuntimeManager
                    command=RuntimeManager()._command()[:-1]+[str(Path(__file__).with_name('provider_auth.py'))]
                process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True,env={**os.environ,'AMPLIFIER_WEB_HOME':str(self.home)})
                row['process']=process
                process.stdin.write((json.dumps({'module':provider['module'],'tokenFile':str(path),'authMode':mode,
                    'hostFile':str(host_path),'registrationFile':str(registration_path),
                    'sourceTokenFile':str(source_path) if mode==old.get('auth_mode')=='chatgpt_plan' else None,'enablePlan':bool(args.get('enablePlan'))})+'\n').encode());await process.stdin.drain();process.stdin.close()
                async with asyncio.timeout(900):
                    while line:=await process.stdout.readline():
                        try:event=json.loads(line)
                        except (ValueError,UnicodeError):continue
                        if event.get('status') not in {'waiting','completed','failed'}:continue
                        if event['status']=='completed':
                            completed=True
                        else:row['status']=event['status']
                        if event.get('instruction'):
                            text=str(event['instruction'])[:2000]
                            # Do not send provider stdout verbatim into persisted
                            # browser state. Only a vetted URL and device code.
                            for url in re.findall(r'https://[^\s<>]+',text):
                                if login_url(url):row['url']=url
                        code_match=re.search(r'(?i)enter\s+(?:the\s+)?code:\s*([A-Z0-9]+(?:-[A-Z0-9]+)+)',str(event.get('instruction','')))
                        if code_match:row['deviceCode']=code_match.group(1)
                        if event['status'] in {'completed','failed'}:
                            row.pop('deviceCode',None);row.pop('url',None);row['instructions']=[]
                        if event.get('error'):row['error']='Sign-in did not finish. Update the included ChatGPT provider if necessary, then try again.'
                        await self.publish_login(identity)
                    code=await process.wait()
                    if code or not completed or row['status']=='failed':
                        row.update(status='failed',error=row.get('error') or 'The provider login ended before authentication completed.')
                    else:
                        account=account_status(config)
                        if not account['connected']:raise ValueError('No authenticated account was saved.')
                        current=next((p for p in self.config(workspace).providers if (p.get('id') or p.get('instance_id') or p['module'].removeprefix('provider-'))==identity),None)
                        disabled=self.config(workspace).settings.get('configurator',{}).get('disabled',{}).get('providers',[])
                        if current!=original or identity in disabled:
                            row.update(status='failed',error='This connection changed during sign-in. Refresh its settings before trying again.')
                        else:
                            # Activate a separate credential profile only after
                            # successful exit and a current configuration check.
                            # Existing workers/shared custom files stay intact.
                            result=self._provider_mutation({**args,'module':provider['module'],'config':config},workspace,scope)
                            activated=True
                            row.update(status='completed',account=account,showPlanWelcome=account['planEnabled'] and not before['planEnabled'])
                            await self.publish_login(identity,providers=result['providers'],providersWorkspace=workspace,providersLoadedAt=time.time(),loginConfigurationChanged=True)
            except asyncio.CancelledError:row['status']='cancelled';raise
            except TimeoutError:row.update(status='expired',error='Sign-in expired. Start again.')
            except MissingHostDependency as exc:row.update(status='failed',error=str(exc))
            except Exception as exc:row.update(status='failed',error='Unable to run provider login ('+type(exc).__name__+').')
            finally:
                if row['status'] in {'completed','failed','expired','cancelled'}:
                    row.pop('deviceCode',None);row.pop('url',None);row['instructions']=[]
                if process and process.returncode is None:
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    try:await asyncio.wait_for(process.wait(),3)
                    except TimeoutError:
                        try:os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        await process.wait()
                if not activated:
                    try:
                        # Inspect effective and persisted rows, including
                        # disabled/shadowed connections. Only unlink this
                        # attempt-owned candidate after the worker has stopped.
                        # Source, registration, host and lock files stay intact;
                        # copied credentials are never remotely revoked here.
                        providers=list(self.config(workspace).providers)
                        for location in ('global','project','local'):
                            providers.extend(self.store.read(workspace,location).get('config',{}).get('providers',[]))
                        referenced=any(
                            Path(value['config']['token_file_path']).expanduser().resolve()==path.resolve()
                            for value in providers if value.get('config',{}).get('token_file_path')
                        )
                        if not referenced and path.resolve()!=source_path.resolve():
                            path.unlink(missing_ok=True)
                    except Exception:
                        row['cleanupWarning']='An abandoned sign-in file could not be safely removed.'
                await self.publish_login(identity)
        row['task']=asyncio.create_task(run())
        return await self.publish_login(identity)

    async def cancel_login(self,identity):
        row=self.logins.get(identity)
        if row and not row['task'].done():
            row['status']='cancelled'
            row['task'].cancel()
            await asyncio.gather(row['task'],return_exceptions=True)
        return await self.publish_login(identity)

    async def close(self):
        await asyncio.gather(*(self.cancel_login(identity) for identity in list(self.logins)),return_exceptions=True)
