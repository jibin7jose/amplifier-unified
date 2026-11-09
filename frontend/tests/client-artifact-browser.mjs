// Installed static package + a synthetic HTTP/SSE peer; no server checkout,
// Python runtime, real account, model, or live service participates.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {createShellHttpFixture} from './shell-http-fixture.mjs';

const packageDirectory=resolve(process.argv[2]||''),pairedRevision=process.argv[3];
if(!process.argv[2]||!pairedRevision)throw Error('Pass the installed package directory and expected paired-server revision');
const {webDirectory}=await import(pathToFileURL(join(packageDirectory,'index.mjs')));
const manifest=JSON.parse(await readFile(join(packageDirectory,'component.json')));
assert.equal(manifest.kind,'unified-web-static-artifact');
assert.equal(manifest.pairedServer.revision,pairedRevision);
const built=JSON.parse(await readFile(join(webDirectory,'build.json')));
assert.deepEqual(built,manifest.build);
const state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},
 view:{mode:'chat',workSurface:'chat',setupJourney:'done',navPinned:true,draft:''},
 sessions:[{id:'fixture-chat',title:'Artifact fixture',sessionKind:'root',workspace:'/fixture',workspaceId:'project',
  status:'idle',historyManaged:true,historyLoaded:true,messages:[{id:'saved',role:'assistant',text:'Retained fixture history'}],workers:[]}],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],
 selectedSessionId:'fixture-chat',selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},
 canvas:{open:false}};
const shell=createShellHttpFixture(()=>state),streams=new Set(),commands=[],errors=[];
let streamCount=0,browser;
const snapshot=()=>{const value=shell.state();return {...value,client:{...value.client,build:{serverVersion:built.version,frontend:built}}}};
const emit=()=>{state.revision++;for(const stream of streams)stream.write('event: state\ndata: '+JSON.stringify(snapshot())+'\n\n')};
const json=(response,value,status=200)=>{response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(value))};
const mime={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.ico':'image/x-icon','.png':'image/png','.wasm':'application/wasm'};
const server=createServer(async(request,response)=>{
 try{
  const url=new URL(request.url,'http://fixture');
  if(url.pathname==='/api/events'){
   streamCount++;response.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});
   streams.add(response);request.on('close',()=>streams.delete(response));
   response.write('event: state\ndata: '+JSON.stringify(snapshot())+'\n\n');return;
  }
  if(url.pathname.startsWith('/api/')){
   let text='';for await(const chunk of request)text+=chunk;
   const body=text?JSON.parse(text):{};
   const value=shell.response({url:()=>url.href,postDataJSON:()=>body});
   if(value!==undefined)return json(response,value);
   if(url.pathname==='/api/state')return json(response,snapshot());
   if(url.pathname==='/api/actions'&&request.method==='GET')return json(response,[]);
   if(url.pathname==='/api/actions'){
    commands.push(body);
    if(body.action==='view.update')Object.assign(state.view,body.args.patch);
    if(body.action==='conversation.send')state.sessions[0].messages.push({id:'accepted-message',inputId:body.id,role:'user',text:body.args.text,delivery:{status:'accepted'}});
    emit();return json(response,{accepted:true,state:snapshot()});
   }
   return json(response,{ok:true});
  }
  const file=resolve(webDirectory,'.'+(url.pathname==='/'?'/index.html':url.pathname));
  if(!file.startsWith(resolve(webDirectory)+'/'))return json(response,{error:'Not found'},404);
  const bytes=await readFile(file);response.writeHead(200,{'Content-Type':mime[extname(file)]||'application/octet-stream'});response.end(bytes);
 }catch(error){if(error.code!=='ENOENT')errors.push(error.message);json(response,{error:'Fixture request failed'},404)}
});

await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base='http://127.0.0.1:'+server.address().port;
try{
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base);
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeVisible();
 await expect(page.getByText('Retained fixture history',{exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().client.build.frontend.id),built.id);
 await composer.fill('Sent through the independent artifact');
 await page.locator('[data-part="composer-primary-action"]').click();
 await expect.poll(()=>commands.filter(row=>row.action==='conversation.send').length).toBe(1);
 await expect(page.getByText('Sent through the independent artifact',{exact:true})).toBeVisible();
 state.sessions[0].messages.push({id:'reply',role:'assistant',text:'Fixture response over real SSE'});emit();
 await expect(page.getByText('Fixture response over real SSE',{exact:true})).toBeVisible();
 await composer.fill('Private unfinished draft');
 await expect.poll(()=>state.view.draft).toBe('Private unfinished draft');
 const before=streamCount;for(const stream of streams)stream.end();
 await expect.poll(()=>streamCount,{timeout:15000}).toBeGreaterThan(before);
 await expect(composer).toHaveText('Private unfinished draft');
 await page.reload();await expect(composer).toHaveText('Private unfinished draft');
 await expect(page.getByText('Retained fixture history',{exact:true})).toBeVisible();
 await expect(page.getByText('Fixture response over real SSE',{exact:true})).toBeVisible();
 assert.equal(commands.filter(row=>row.action==='conversation.send').length,1,'Reconnect/reload never resubmits accepted work');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,source:manifest.source,pairedServer:manifest.pairedServer,build:built,
  qualification:'installed-static-artifact-with-synthetic-http-sse-peer',sends:1,sseConnections:streamCount,
  draftSurvivedReconnectAndReload:true,historyPreserved:true,limitations:['Synthetic peer only; actual paired server not qualified','No provider, native execution, hardware, deployment or cross-version qualification']}));
}finally{
 await browser?.close();for(const stream of streams)stream.end();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
}
