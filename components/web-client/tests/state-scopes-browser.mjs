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
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const api=(path,body)=>page.evaluate(async([path,body])=>{
  const headers={'X-Amplifier-Client':window.amplifier.getState().client.id};
  const response=await fetch(path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw Error(await response.text());return response.json();
 },[path,body]);
 const info=()=>api('/api/fixture/info');
 const agent=async(action,args)=>api('/api/fixture/agent',{args:action==='view.update'?{action:'shell.view.update',args:{...args,clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId:'chats',patch:{navRecentView:args.patch}}}:{action,args}});
 const state=()=>page.evaluate(()=>({...window.amplifier.getState(),...window.amplifier.getShellState().snapshots.chats,chatNavigation:window.amplifier.getShellState().snapshots.chats.sidebarNavigation.recent,view:{...window.amplifier.getState().view,...window.amplifier.getShellState().snapshots.chats.view}}));
 const row=id=>page.locator(`[data-sidebar-section=pinned] .a-nav-chat[data-session-id="${id}"], [data-sidebar-section=recent] .a-nav-chat[data-session-id="${id}"]`);

 await page.goto(vite.resolvedUrls.local[0]);
 await page.locator('[data-sidebar-section=recent] .a-nav-chat').first().waitFor();
 const first=await info(),initial=first.initialSession;
 const selected=(await state()).chatNavigation.items.find(chat=>chat.title==='Beta 002');
 assert.ok(selected);
 await row(selected.id).getByRole('button',{name:/Details and actions/}).click();
 await page.locator('.a-navigation-flyout').getByRole('button',{name:'Pin Beta 002',exact:true}).click();
 await page.waitForFunction(id=>window.amplifier.getState().pinnedSessionIds.includes(id),selected.id);
 assert.equal((await state()).selectedSessionId,initial);
 await agent('session.rename',{id:selected.id,title:'Saved scoped title'});
 await page.waitForFunction(id=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.pinned.items.find(row=>row.id===id)?.title==='Saved scoped title',selected.id);
 assert.deepEqual((await info()).runtimeStarts,[]);
 assert.deepEqual((await info()).runtimeSends,[]);
 const restart=async()=>{
  const generation=(await info()).generation;
  await api('/api/fixture/restart',{});
  let restarted=false;
  for(let i=0;i<100;i++){
   try {const r=await page.request.get(target+'/api/fixture/info');if(r.ok()&&(await r.json()).generation>generation){restarted=true;break}}catch{}
   await new Promise(resolve=>setTimeout(resolve,100));
  }
  assert.ok(restarted,'service restarted');
  await page.reload();await page.locator('[data-sidebar-section=recent] .a-nav-chat').first().waitFor();
 };
 await restart();
 assert.ok((await state()).pinnedSessionIds.includes(selected.id));
 assert.equal((await state()).sidebarNavigation.pinned.items.find(row=>row.id===selected.id)?.title,'Saved scoped title');
 await row(selected.id).getByRole('button',{name:/Details and actions/}).click();
 await page.locator('.a-navigation-flyout').getByRole('button',{name:'Unpin Saved scoped title',exact:true}).click();
 await page.waitForFunction(id=>!window.amplifier.getState().pinnedSessionIds.includes(id),selected.id);
 await restart();
 assert.equal((await state()).pinnedSessionIds.includes(selected.id),false);
 await agent('session.select',{id:selected.id});
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,selected.id);
 await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill('Verify the saved chat still works.');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.getByText('Fixture reply to a real submitted turn.',{exact:true}).waitFor();
 assert.deepEqual((await info()).runtimeSends,[selected.id]);
 assert.deepEqual(errors,[]);
 console.log('Scoped persistence browser checks passed: UI pin/unpin, rename, restart durability, no metadata runtime starts, fresh synthetic message.');
}catch(error){
 if(fixtureLog)console.error(fixtureLog);
 throw error;
}finally{
 await browser?.close();await vite?.close();
 if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
