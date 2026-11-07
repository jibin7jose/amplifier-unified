"""Inspect composed defaults without mounting hooks, tools, or a session.

This probe reports effective selection only. Configured connection inventory and
model discovery belong to SetupManager and the host's shared ProviderCatalog.
"""
from __future__ import annotations
import asyncio
import inspect
import json
import os
from pathlib import Path
import sys
if not __package__:
    from runtime_bootstrap import bootstrap_app_package
    bootstrap_app_package()

async def query(request):
    if request.get('replacement') is not None:
        from amplifier_web.bundle_selection import inspect_replacement
        options = request['replacement']
        checked, _ = await inspect_replacement(request['home'], request['workspace'], request['sessionId'], request['bundle'],
            selection=options.get('selection'), execution_workspace=options.get('workingDirectory'),
            replace_saved_selection=options.get('replaceSavedSelection', False))
        return checked
    from amplifier_web.host.config import load_config
    from amplifier_web.host.session import load_root_bundle
    from amplifier_web.provider_environment import close_provider, construct_provider, construct_schema_provider, config_schema, materialize_provider_config, provider_class
    from amplifier_web.provider_probe import public
    workspace=Path(request['workspace']).expanduser().resolve()
    existing=workspace
    while not existing.is_dir() and existing.parent!=existing:existing=existing.parent
    config=load_config(existing,home=request['home'],session_id=request.get('sessionId'),global_only=bool(request.get('globalOnly')))
    config.workspace=workspace
    identity=request.get('sessionId')
    chosen=request['bundle']
    if identity:
        from amplifier_web.session_files import sessions_dir, validate_id
        validate_id(identity)
        directory=sessions_dir(workspace)/identity
        metadata_path=directory/'metadata.json'
        if metadata_path.exists() and not request.get('desiredBundle'):
            metadata=json.loads(metadata_path.read_text())
            chosen=(metadata.get('bundle_name') or metadata.get('bundle') or chosen).removeprefix('bundle:')
    _,bundle,_=await load_root_bundle(config,chosen,execution_workspace=workspace)
    if identity and not request.get('desiredBundle'):
        edited=Path(request['home'])/'sessions'/identity/'configuration.json'
        if edited.exists():
            from amplifier_web.host.session import apply_runtime_plan
            bundle=apply_runtime_plan(bundle,json.loads(edited.read_text()),config,workspace)
    rows=[]
    for row in bundle.providers:
        if row.get('enabled') is False:continue
        identity=row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')
        raw=row.get('config',{})
        result={'id':identity,'module':row['module'],'priority':raw.get('priority',100)}
        stage='import'
        try:
            cls=provider_class(row['module'])
            stage='schema'
            schema_provider=construct_schema_provider(cls,raw)
            try:schema=await config_schema(schema_provider)
            finally:await close_provider(schema_provider)
            stage='configuration'
            values=materialize_provider_config(raw,schema)
            stage='construction'
            provider=construct_provider(cls,values)
            try:
                stage='metadata'
                info=provider.get_info()
                if inspect.isawaitable(info):info=await info
                info=public(info)
                result.update(info={key:info[key] for key in ('id','display_name','defaults') if key in info},
                    effort=values.get('reasoning_effort'),
                    priority=getattr(provider,'priority',getattr(provider,'config',{}).get('priority',100)),
                    configSchema=public({'fields':[field for field in schema.get('fields',[]) if field.get('id')=='reasoning_effort']}))
            finally:await close_provider(provider)
        except Exception as exc:
            # Keep the failed connection in place. Never choose a different
            # account implicitly, or disclose credentials in exception text.
            result['diagnostic']={'connection':identity,'stage':stage,'category':type(exc).__name__[:80]}
        rows.append(result)
    rows.sort(key=lambda row:row['priority'])
    first=rows[0] if rows else {}
    defaults=first.get('info',{}).get('defaults',{})
    return {'providers':rows,'effective':{'instance':first.get('id'),'model':defaults.get('model') or defaults.get('default_model'),'effort':defaults.get('reasoning_effort') or first.get('effort')}}

def main():
    output=os.fdopen(os.dup(sys.stdout.fileno()),'w')
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno());sys.stdout=sys.stderr
    try:result=asyncio.run(query(json.loads(sys.stdin.read())))
    except Exception as exc:result={'error':type(exc).__name__}
    output.write(json.dumps(result)+'\n');output.flush()
if __name__=='__main__':main()
