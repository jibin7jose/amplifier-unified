"""Large, deliberately delayed settings fixture; no external account or repository calls."""
import asyncio
from collections import Counter
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as base
from amplifier_web.host.config import write_private
import yaml
calls=Counter()
async def probe(self,action,args,workspace):
    await asyncio.sleep(2 if action=='providers.models' else .1)
    if action=='providers.testMessage':
        return {'messageTest': {'providerId':args['id'], 'model':'fixture-model', 'method':'provider.complete', 'reachable':args['id']!='two', 'response':'OK', 'error':'insufficient_quota', 'statusCode':429 if args['id']=='two' else None, 'elapsedMs':100}}
    row=next((row for row in self.config(workspace).providers if row.get('id')==args.get('id')), {})
    fields=[{'id':'reasoning_effort','display_name':'Reasoning effort',
             'choices':['none','low','medium','high'],'field_type':'choice'}]
    return {'models':[{'id':'fixture-model'},{'id':'fixture-alternative'}],
            'modelsProviderId':args.get('id'),'modelsSupported':True,'modelsProvenance':'simulated',
            'providerMetadata':{'module':row.get('module',args.get('module','provider-openai')),
                'modelListInfo':{'version':1,'source':'simulated'},
                'info':{'config_fields':fields},'configSchema':{'fields':fields}}}
base.SetupManager.probe=probe
async def catalog(self):
    rows=[{'id':f'tool-{i}','name':f'Tool {i:02d}','repository':f'https://example.com/tool-{i}','ref':'main','path':'.','description':('Research' if i<32 else 'Creation')+' specialist tool','version':'1.0'} for i in range(51)]
    await self._change(lambda state:state.update(catalog=rows))
    return {'items':rows}
async def install(self,args):
    calls[args['repository']]+=1
    await asyncio.sleep(.1)
    if args['repository'].endswith('tool-2') and calls[args['repository']]==1:raise ValueError('Temporary fixture failure. Retry this item.')
    row={**args,'id':args['repository'].rsplit('/',1)[-1],'name':args['repository'].rsplit('/',1)[-1],'status':'installed','binDir':'/fixture/bin','guidance':'Configure the documented MCP executable.'}
    await self._change(lambda state:state.update(installations=[item for item in state['installations'] if item['id']!=row['id']]+[row]))
    return row
base.SmartToolsManager.catalog=catalog
base.SmartToolsManager.install=install
async def main(home):
    app=await base.main(home)
    roles=['general','fast','coding','ui-coding','security-audit','reasoning','critique','creative','writing','research','vision','image-gen','critical-ops']
    matrix={'name':'balanced','unknownProfile':{'keep':True},'roles':{role:{'description':role.title(),'customRole':'keep','candidates':[{'provider':['one','two','three'][i%3],'model':'fixture-model','config':{'effort':'high','custom':'keep'},'unknownCandidate':i} for i in range(6)]} for role in roles}}
    write_private(home/'native/routing/balanced.yaml',yaml.safe_dump(matrix,sort_keys=False))
    manager=base.BundleManager(home)
    for i in range(6):
        result=await manager.perform('bundles.add',{'workspace':str(home/'workspace'),'uri':f'foundation:fixture-{i}','name':f'Capability {i}','role':'standalone' if i==4 else 'behavior'})
    await manager.perform('bundles.toggle',{'workspace':str(home/'workspace'),'id':next(row['id'] for row in result['bundles'] if row['name']=='Capability-5'),'enabled':False})
    return app
async def serve():
    with tempfile.TemporaryDirectory(prefix='amplifier-settings-collections-') as tmp:
        app=await main(Path(tmp));runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
        print(f'http://127.0.0.1:{port}',flush=True)
        try:await asyncio.Event().wait()
        finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(serve())
