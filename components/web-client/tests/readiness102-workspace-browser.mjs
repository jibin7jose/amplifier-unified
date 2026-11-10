import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/readiness102_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,browser2,vite;
try{
 const target=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.UNIFIED_BROWSER_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const options={viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}};
 browser2=await chromium.launch({headless:true,args:process.env.UNIFIED_BROWSER_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const first=await browser.newContext(options),second=await browser2.newContext(options);
 const page=await first.newPage(),peer=await second.newPage(),errors=[];
 for(const p of [page,peer])p.on('pageerror',error=>errors.push(error.message));
 const url=vite.resolvedUrls.local[0];
 const dispatch=(p,action,args={})=>p.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const view=(p,patch)=>dispatch(p,'view.update',{patch});
 for(const p of [page,peer]){await p.goto(url);await showFixtureConversation(p,{navPinned:true});await p.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId)}
 const info=await page.evaluate(async()=>await(await fetch('/api/fixture/readiness')).json());
 for(const p of [page,peer]){await dispatch(p,'session.select',{id:info.sessionId});await view(p,{panel:null,workSurface:'chat'})}
 await dispatch(page,'session.markUnread',{id:info.sessionId});
 const unread=p=>p.evaluate(id=>(window.amplifier.getState().attention?.items||[]).find(row=>row.id==='unread:'+id),info.sessionId);
 await expect.poll(async()=>!!(await unread(peer))&&!((await unread(peer)).read)).toBe(true);
 await peer.bringToFront();await peer.waitForTimeout(1200);
 assert.equal((await unread(peer)).read,false,'an already open peer must not acknowledge a new manual mark');
 await dispatch(peer,'session.select',{id:info.sessionId});
 await peer.locator('.a-messages').evaluate(element=>element.scrollTop=element.scrollHeight);
 await expect.poll(async()=>(await unread(peer))?.read,{timeout:10000}).toBe(true);
 await expect.poll(async()=>(await unread(page))?.read).toBe(true);
 await page.bringToFront();
 await view(page,{workSurface:'workspace',workWorkspaceId:info.workspaceId,workWorkspaceTab:'chats'});
 await expect(page.getByText('1 of 1 steps done',{exact:true})).toBeVisible();
 await page.getByText('1 of 1 steps done',{exact:true}).click();
 await expect(page.locator('.a-workspace-plan').getByText('Review the demo completed',{exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),info.sessionId);
 await dispatch(page,'workspace.archive',{id:info.workspaceId});
 await view(page,{workSurface:'workspaces'});
 const filter=page.getByRole('combobox',{name:'Workspace archive filter'});
 await filter.selectOption('archived');
 await page.getByRole('searchbox',{name:'Filter workspaces'}).fill('Cleanup demo');
 await expect(page.locator('.a-work-browser .a-workspace-row').filter({hasText:'Cleanup demo'})).toBeVisible();
 await page.reload();await expect(filter).toHaveValue('archived');
 const row=page.locator('.a-work-browser .a-workspace-row').filter({hasText:'Cleanup demo'});
 await row.getByRole('button',{name:/details|options/i}).click();
 await page.getByRole('button',{name:'Restore workspace',exact:true}).click();
 await filter.selectOption('active');
 await expect(row).toBeVisible();
 await view(page,{panel:'settings',settingsSection:'privacy',settingsExpanded:['storage']});
 await expect(page.getByRole('region',{name:'Storage usage'})).toBeVisible();
 const storage=page.getByRole('region',{name:'Storage usage'});
 await expect(storage.locator('.a-storage-categories li')).toHaveCount(6);
 await expect(storage.locator('.a-storage-items')).toHaveCount(0);
 assert.equal(await storage.locator('.a-storage-categories button').first().evaluate(el=>getComputedStyle(el).display),'grid');
 await page.screenshot({path:'/tmp/unified-storage-overview.png'});
 await page.getByRole('button',{name:/^Backups /}).click();
 await expect(storage.getByText('Newest backup · kept',{exact:true})).toBeVisible();
 await storage.getByRole('button',{name:/^Review cleanup for Private backup/}).click();
 await storage.getByRole('button',{name:'Review backup contents',exact:true}).click();
 await expect(storage.getByText(/The newest backup is kept/)).toBeVisible();
 await storage.getByRole('button',{name:'Permanently delete backup',exact:true}).click();
 await expect(storage.getByRole('status').filter({hasText:/Backup removed/})).toHaveText(/Backup removed/);
 let stored=await page.evaluate(async()=>await(await fetch('/api/fixture/readiness')).json());
 assert.equal(stored.oldBackupExists,false);assert.equal(stored.newBackupExists,true);
 await storage.getByRole('button',{name:'Close',exact:true}).click();
 await storage.getByRole('navigation',{name:'Storage navigation'}).getByRole('button',{name:'Storage',exact:true}).click();
 await page.getByRole('button',{name:/^Session data /}).click();
 await storage.getByRole('button',{name:'Show items I can review for cleanup'}).click();
 await storage.getByRole('button',{name:'Review cleanup for Storage managed chat',exact:true}).click();
 await expect(storage.getByRole('button',{name:'Delete permanently',exact:true})).toBeVisible();
 await storage.getByRole('button',{name:'Keep chat',exact:true}).click();
 await storage.getByRole('navigation',{name:'Storage navigation'}).getByRole('button',{name:'Storage',exact:true}).click();
 await page.getByRole('button',{name:/^Workspace files/}).click();
 await expect(storage.locator('.a-storage-items li')).toHaveCount(5);
 assert.equal(await storage.locator('.a-storage-items li').first().getByRole('button').first().textContent(),'Storage example 6');
 await storage.getByRole('combobox',{name:'Sort storage'}).selectOption('name');
 await expect(storage.locator('.a-storage-items li').first()).toContainText('Cleanup demo');
 await storage.getByRole('combobox',{name:'Sort storage'}).selectOption('size');
 await storage.getByRole('button',{name:/Show more/}).click();
 // The remembered fixture cwd is outside the workspace root and must be excluded.
 await expect(storage.locator('.a-storage-items li')).toHaveCount(8);
 await expect(storage.getByText(/The root itself and folders outside it are not scanned/)).toBeVisible();
 await storage.locator('.a-storage-heading').scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/unified-storage-category.png'});
 await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('#amp-one').evaluate(el=>el.scrollWidth<=el.clientWidth));
 await storage.locator('.a-storage-heading').scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/unified-storage-mobile.png'});
 await page.setViewportSize({width:1280,height:900});
 await storage.getByRole('searchbox',{name:'Filter storage'}).fill('Cleanup demo');

 await page.getByRole('button',{name:'Cleanup demo',exact:true}).click();
 await page.getByRole('button',{name:'nested',exact:true}).click();
 await storage.getByRole('navigation',{name:'Storage navigation'}).getByRole('button',{name:'Cleanup demo',exact:true}).click();
 await page.getByRole('button',{name:'Review workspace file cleanup',exact:true}).click();
 await expect(page.getByText('App-managed workspace',{exact:true})).toBeVisible();
 const remove=page.getByRole('button',{name:'Permanently delete workspace files'});
 await expect(remove).toBeDisabled();
 await page.getByRole('textbox',{name:'Confirm workspace folder path'}).fill(info.workspace);
 // The receipt must survive the post-delete progressive scan replacing the
 // workspace detail component, not merely flash before that response arrives.
 const remeasured=page.waitForResponse(async response=>{
  if(response.request().method()!=='POST'||!response.url().includes('/api/actions'))return false;
  const request=response.request().postDataJSON();
  if(request?.action!=='storage.inspect'||!request.args?.rootId)return false;
  try{return (await response.json()).result?.scanning===false}catch{return false}
 });
 await remove.click();
 await remeasured;
 await expect(page.getByRole('status').filter({hasText:'Workspace files deleted'})).toBeVisible();
 assert.equal(await page.evaluate(async()=> (await(await fetch('/api/fixture/readiness')).json()).filesExist),false);
 await view(page,{panel:null,workSurface:'chat'});
 await dispatch(page,'session.select',{id:info.sessionId});
 await expect(page.getByText('This conversation will be retained.',{exact:true})).toBeVisible();
 await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('#amp-one').evaluate(el=>el.scrollWidth<=el.clientWidth));
 assert.deepEqual(errors,[]);
 console.log('Readiness102 workspace browser passed: two-device unread acknowledgement, recorded plan expansion, archive/search/reload/restore, storage drilldown and exact-path cleanup with retained chat, dark/reduced-motion narrow layout.');
}finally{await browser2?.close();await browser?.close();await vite?.close();fixture.kill();}
