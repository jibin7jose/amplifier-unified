import {showFixtureConversation} from './browser-chat-pages.mjs';
// Exact sidebar reading state while selection demand is deliberately held.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
const source=fileURLToPath(new URL('../../.build/web-client/',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/chat_library_server.py',import.meta.url))],{env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',vite,browser,page,release,primary;
fixture.stderr.on('data',value=>logs+=value);
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/sidebar-reading158';
const ready=new Promise((resolve,reject)=>{let output='';fixture.stdout.on('data',value=>{output+=value;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('error',reject);fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${logs}`)))});
try{
 await mkdir(out,{recursive:true});
 const port=await ready,target=`http://127.0.0.1:${port}`,built=process.env.AMPLIFIER_TEST_BUILT_CLIENT==='1';
 if(!built){vite=await createServer({configFile:false,root:source,cacheDir:'/tmp/sidebar-reading158-vite',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,req=>req.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen()}
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message));page.on('request',req=>{if(new URL(req.url()).pathname==='/api/shell/resources')requests.push(req.postDataJSON())});
 const shell=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const act=(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const rows=page.locator('[data-shell-instance="chats"] [data-sidebar-section="recent"] .a-nav-chat[data-session-id]');
 const older=page.locator('[data-shell-instance="chats"] [data-sidebar-section="recent"]').getByRole('button',{name:'Load older chats',exact:true});
 const rail=page.locator('.a-nav-content');
 await page.goto(built?target:vite.resolvedUrls.local[0]);await showFixtureConversation(page,{navPinned:true});await expect(rows).toHaveCount(20);
 // The Recent wrapper is a real focus-recovery target when its row disappears.
 // Its outline must use the theme, not Chromium's black/light native ring.
 const recent=page.locator('.a-recent-shortcuts');
 const focusStyles=[];
 for(const scheme of ['light','dark']){
  await page.evaluate(async scheme=>{
   const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
   const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,scheme}}});
   await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});
  },scheme);
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
  await page.keyboard.press('Tab');await recent.focus();
  await expect(recent).toBeFocused();
  const facts=await recent.evaluate(node=>{
   const style=getComputedStyle(node),probe=document.createElement('span');
   probe.style.color='var(--a-accent)';node.append(probe);
   const accent=getComputedStyle(probe).color;probe.remove();
   return {focusVisible:node.matches(':focus-visible'),outlineColor:style.outlineColor,outlineStyle:style.outlineStyle,outlineWidth:style.outlineWidth,outlineOffset:style.outlineOffset,accent};
  });
  focusStyles.push({scheme,...facts});await writeFile(out+'/focus-styles.json',JSON.stringify(focusStyles,null,2)+'\n');
  await page.screenshot({path:out+'/recent-focus-'+scheme+'.png'});
  assert.equal(facts.focusVisible,true);
  assert.equal(facts.outlineStyle,'solid');
  assert.equal(facts.outlineWidth,'2px');
  assert.equal(facts.outlineColor,facts.accent);
  assert.equal(facts.outlineOffset,'-2px','inset focus is not clipped by the sidebar scrollport');
 }
 // Pointer-initiated recovery must not leave the same keyboard outline behind.
 await page.getByRole('textbox',{name:'Message Amplifier'}).click();
 await recent.focus();
 const pointer=await recent.evaluate(node=>({focusVisible:node.matches(':focus-visible'),outlineStyle:getComputedStyle(node).outlineStyle}));
 assert.equal(pointer.focusVisible,false);assert.equal(pointer.outlineStyle,'none');
 await page.emulateMedia({forcedColors:'active'});await page.keyboard.press('Tab');await recent.focus();
 assert.equal(await recent.evaluate(node=>getComputedStyle(node).outlineStyle),'solid');
 await page.screenshot({path:out+'/recent-focus-forced-colors.png'});
 await page.emulateMedia({forcedColors:'none'});
 await older.click();await expect(rows).toHaveCount(40);await expect(page.getByText(/Recent changed during the read/)).toHaveCount(0);
 // Put an existing row near the middle. Select it without scrolling the rail.
 await rows.nth(23).scrollIntoViewIfNeeded();
 const reading=()=>rail.evaluate(node=>{
  const top=node.getBoundingClientRect().top,rows=[...node.querySelectorAll('[data-sidebar-section="recent"] .a-nav-chat[data-session-id]')];
  const visible=rows.find(row=>row.getBoundingClientRect().top>=top+10);
  return {top:node.scrollTop,height:node.scrollHeight,id:visible?.dataset.sessionId,offset:visible?.getBoundingClientRect().top-top,count:rows.length};
 });
 let held=0;let holding=true;const gate=new Promise(resolve=>release=resolve);
 await page.route('**/api/shell/resources',async route=>{if(holding){held++;await gate}await route.continue()});
 const before=await reading();assert.ok(before.top>200);
 await page.locator(`[data-sidebar-section="recent"] [data-session-id="${before.id}"] .a-nav-chat-select`).click();
 await expect.poll(()=>held).toBeGreaterThan(0);
 await expect(rows).toHaveCount(40);
 const pending=await reading();assert.ok(pending.top>200,'selection must not clamp the rail to zero');
 assert.ok(Math.abs(pending.offset-before.offset)<2,JSON.stringify({before,pending}));
 assert.equal((await shell()).navigationResources.includes('recentNavigation'),false,'retained rows never grant current coverage');
 await expect(page.getByText(/Recent changed during the read/)).toHaveCount(0);
 holding=false;release();await expect.poll(async()=>(await shell()).recentNavigation?.scope?.selectedSessionId).toBe(before.id);
 await expect(rows).toHaveCount(40);
 // Browsing another workspace changes main interest, not the global sidebar query.
 let unblock;const workspaceGate=new Promise(resolve=>unblock=resolve);release=unblock;holding=true;held=0;
 await page.unroute('**/api/shell/resources');await page.route('**/api/shell/resources',async route=>{if(holding){held++;await workspaceGate}await route.continue()});
 const beforeWorkspace=await reading();
 const workspaces=await page.evaluate(()=>window.amplifier.getShellState().snapshots.workspaces.workspaceShortcuts);
 const currentWorkspace=(await shell()).selectedWorkspaceId;
 const containingWorkspace=workspaces.find(row=>row.workspaceId===currentWorkspace);
 assert.ok(containingWorkspace,'selected chat has a registered containing workspace');
 const otherWorkspace=workspaces.find(row=>row.workspaceId!==currentWorkspace&&row.available!==false);
 assert.ok(otherWorkspace);const workspace=otherWorkspace.workspaceId;
 await act('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace,workWorkspaceTab:'chats'}});
 await expect.poll(()=>held).toBeGreaterThan(0);await expect(rows).toHaveCount(40);
 const workspacePending=await reading();assert.ok(Math.abs(workspacePending.offset-beforeWorkspace.offset)<2,JSON.stringify({beforeWorkspace,workspacePending}));
 holding=false;release();await expect.poll(async()=>(await shell()).navigationResources.includes('recentNavigation')).toBe(true);
 await older.click();await expect(rows).toHaveCount(60);await expect(page.getByText(/Recent changed during the read/)).toHaveCount(0);
 // A remembered workspace route must not leave its pinned row selected after
 // returning to a chat in another workspace, including the held demand phase.
 await act('workspace.pin',{id:workspace,pinned:true});
 await act('workspace.pin',{id:currentWorkspace,pinned:true});
 const workspaceButtons=rail.getByRole('button',{name:'Open chats in '+otherWorkspace.path,exact:true});
 const containingButtons=rail.getByRole('button',{name:'Open chats in '+containingWorkspace.path,exact:true});
 await expect.poll(()=>workspaceButtons.count()).toBeGreaterThan(0);
 await expect.poll(()=>containingButtons.count()).toBeGreaterThan(0);
 await expect.poll(()=>workspaceButtons.evaluateAll(nodes=>nodes.every(node=>node.getAttribute('aria-pressed')==='true'))).toBe(true);
 await expect.poll(()=>containingButtons.evaluateAll(nodes=>nodes.every(node=>node.getAttribute('aria-pressed')==='false'))).toBe(true);
 let releaseChat;const chatGate=new Promise(resolve=>releaseChat=resolve);release=releaseChat;holding=true;held=0;
 await page.unroute('**/api/shell/resources');await page.route('**/api/shell/resources',async route=>{if(holding){held++;await chatGate}await route.continue()});
 await act('session.select',{id:before.id});
 await expect.poll(()=>held).toBeGreaterThan(0);
 await act('view.update',{patch:{workSurface:'chat',workWorkspaceId:workspace}});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.workWorkspaceId)).toBe(workspace);
 await expect(rows).toHaveCount(60);
 await expect.poll(()=>workspaceButtons.evaluateAll(nodes=>nodes.length>0&&nodes.every(node=>node.getAttribute('aria-pressed')==='false'))).toBe(true);
 await expect.poll(()=>containingButtons.evaluateAll(nodes=>nodes.length>0&&nodes.every(node=>node.getAttribute('aria-pressed')==='false'))).toBe(true);
 holding=false;release();await expect.poll(async()=>(await shell()).navigationResources.includes('recentNavigation')).toBe(true);
 await expect.poll(()=>workspaceButtons.evaluateAll(nodes=>nodes.length>0&&nodes.every(node=>node.getAttribute('aria-pressed')==='false'))).toBe(true);
 await expect.poll(()=>containingButtons.evaluateAll(nodes=>nodes.length>0&&nodes.every(node=>node.getAttribute('aria-pressed')==='false'))).toBe(true);
 assert.deepEqual(errors,[]);assert.ok(requests.every(req=>req.resources.length<=4));
 await page.screenshot({path:out+'/sidebar.png'});
 await writeFile(out+'/result.json',JSON.stringify({passed:true,before,pending,beforeWorkspace,workspacePending,finalRows:60,resourcePosts:requests.length},null,2)+'\n');
 console.log('Sidebar retained 40 rows and reading anchor across held chat/workspace demand; current coverage fenced; explicit expansion reached60 without retry error.');
}catch(error){primary=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{
 release?.();let failure;
 for(const cleanup of [()=>browser?.close(),()=>vite?.close(),async()=>{if(fixture.exitCode!==null)return;const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{const [code,signal]=await done;if(code!==0)throw Error(`Fixture cleanup ${code}/${signal}`)}finally{clearTimeout(timer)}}])try{await cleanup()}catch(error){failure??=error}
 if(logs)await writeFile(out+'/fixture.log',logs).catch(()=>{});
 if(!primary&&failure)throw failure;
}
