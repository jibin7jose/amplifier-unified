// Production discovery/actions/persistence in a disposable service; runtime is synthetic.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/chat_library_server.py',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';
fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const ready=new Promise((resolve,reject)=>{
 let output='';
 fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
 fixture.once('error',reject);
 fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${fixtureLog}`)));
});
let vite,browser,page;
try{
 const port=await ready,target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,define:{__AMPLIFIER_BUILD__:JSON.stringify({id:"aaaaaaaaaaaaaaaa",version:"0.20.67"})},server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const api=(path,body)=>page.evaluate(async([path,body])=>{
  const headers={'X-Amplifier-Client':window.amplifier.getState().client.id};
  const response=await fetch(path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw Error(await response.text());return response.json();
 },[path,body]);


 // Drive stream lifecycle and the one-minute clock deterministically.
 // HTTP failure/recovery still uses real application requests and routing.
 await page.addInitScript(()=>{
  const Native=window.WebSocket;
  window.WebSocket=class extends Native{constructor(url,options){super(url,options);if(String(url).includes('/api/events'))window.fixtureEvents=this}};
 });
 await page.goto(vite.resolvedUrls.local[0]);
 await page.locator('[data-sidebar-section=recent] .a-nav-chat').first().waitFor();
 await page.getByText('Connected',{exact:true}).waitFor();
 await page.route('**/build.json',route=>route.fulfill({json:{version:'99.0.0',id:'bbbbbbbbbbbbbbbb'}}));
 await api('/api/fixture/update-status',{restarting:true});
 await page.waitForFunction(()=>!!window.amplifier.getState().updates.pendingRestart);
 await page.clock.install();
 await page.evaluate(()=>{window.fixtureEvents.close();window.fixtureEvents.onclose()});
 await page.getByText('Updating Amplifier—reconnecting…',{exact:true}).waitFor();
 // Run scheduled callbacks in order; fastForward models a suspended tab and
 // can defer React effects until after the grace-period clock has jumped.
 await page.clock.runFor(59000);
 assert.equal(await page.locator('[data-part=connection-notice]').getAttribute('role'),'status');
 await page.clock.runFor(1100);
 await page.getByRole('alert').filter({hasText:'Unable to reconnect to Amplifier'}).waitFor();
 await page.evaluate(()=>window.fixtureEvents.onopen());
 await page.locator('[data-part=connection-notice]').waitFor({state:'hidden'});
 // Restore the production stream after the controlled timeout exercise.
 await page.reload();
 await page.getByText('Connected',{exact:true}).waitFor();
 await api('/api/fixture/update-status',{restarting:false});
 await page.waitForFunction(()=>!window.amplifier.getState().updates.pendingRestart);

 await page.evaluate(()=>window.fixtureEvents.onerror());
 await page.locator('[data-part="connection-notice"]').getByText('Reconnecting to Amplifier…',{exact:true}).waitFor();
 assert.equal(await page.locator('.a-alert').filter({hasText:'Failed to fetch'}).count(),0);
 await page.evaluate(()=>window.fixtureEvents.onopen());
 await page.getByText('Connected',{exact:true}).waitFor({timeout:15000});
 await page.locator('[data-part="connection-notice"]').waitFor({state:'hidden'});
 await page.getByText('Update ready—reload to finish',{exact:true}).waitFor();
 await page.route('**/api/shell?*',route=>route.abort('connectionfailed'));
 await page.evaluate(()=>window.dispatchEvent(new Event('amplifier-reconnected')));
 await page.getByText('Reconnecting to Amplifier…',{exact:true}).waitFor();
 assert.equal(await page.getByRole('alert').count(),0);
 await page.unroute('**/api/shell?*');
 await page.evaluate(()=>window.dispatchEvent(new Event('amplifier-reconnected')));
 await page.locator('[data-part=connection-notice]').waitFor({state:'hidden'});

 let attempts=0;
 await page.route('**/api/actions',route=>{
  const body=route.request().postDataJSON();
  if((body?.args?.action||body?.action)==='session.pin'){attempts++;return route.abort('connectionfailed')}
  return route.continue();
 });
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Pin',exact:true}).click();
 await page.getByText('An action could not be confirmed',{exact:true}).waitFor();
 assert.equal(attempts,1);
 assert.equal(await page.locator('.a-alert').filter({hasText:'Failed to fetch'}).count(),0);
 await page.evaluate(()=>window.fixtureEvents.onerror());
 await page.getByText('Reconnecting to Amplifier…',{exact:true}).waitFor();
 await page.evaluate(()=>window.fixtureEvents.onopen());
 await page.getByText('Connected',{exact:true}).waitFor({timeout:15000});
 await page.getByText('An action could not be confirmed',{exact:true}).waitFor();
 assert.equal(attempts,1,'reconnection must not replay the mutation');
 await page.getByRole('button',{name:'Dismiss connection notice'}).click();
 await page.unroute('**/api/actions');
 await page.route('**/api/actions',route=>{
  const body=route.request().postDataJSON();
  return (body?.args?.action||body?.action)==='session.pin'?route.fulfill({status:403,json:{error:'Fixture permission denied'}}):route.continue();
 });
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Pin',exact:true}).click();
 await page.locator('.a-alert').filter({hasText:'Fixture permission denied'}).waitFor();
 assert.deepEqual(errors,[]);
 console.log('Reconnect browser checks passed: neutral update/reconnect, one-minute grace, recovery clears read interruption, lost action stays unconfirmed without replay, server rejection stays visible.');
}catch(error){
 if(page)console.error(await page.locator('[data-part=connection-notice]').allTextContents());
 if(fixtureLog)console.error(fixtureLog);
 throw error;
}finally{
 await browser?.close();await vite?.close();
 if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
