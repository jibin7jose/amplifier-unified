import {showFixtureConversation} from './browser-chat-pages.mjs';
// Details inspect a row independently, then dismiss when navigation moves elsewhere.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
const source=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/chat_library_server.py',import.meta.url))],{env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',vite,browser,page,primary;
fixture.stderr.on('data',value=>logs+=value);
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/navigation-details-context162';
const ready=new Promise((resolve,reject)=>{let output='';fixture.stdout.on('data',value=>{output+=value;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('error',reject);fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${logs}`)))});
try{
 await mkdir(out,{recursive:true});
 const port=await ready,target=`http://127.0.0.1:${port}`;
 if(process.env.AMPLIFIER_TEST_DEV){vite=await createServer({configFile:false,root:source,cacheDir:out+'/vite-cache',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,req=>req.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen()}
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message));page.on('request',req=>{if(new URL(req.url()).pathname==='/api/shell/resources')requests.push(req.postDataJSON())});
 const shell=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const act=(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const recent=page.locator('[data-shell-instance="chats"] [data-sidebar-section="recent"]');
 const rows=recent.locator('.a-nav-chat[data-session-id]'),details=page.locator('.a-navigation-flyout');
 await page.goto(vite?.resolvedUrls.local[0]||target);await showFixtureConversation(page,{navPinned:true});await expect(rows).toHaveCount(20);
 const selected=(await shell()).selectedSessionId;
 const inspected=await rows.evaluateAll((rows,id)=>rows.find(row=>row.dataset.sessionId!==id)?.dataset.sessionId,selected);
 assert.ok(inspected);
 const row=recent.locator(`[data-session-id="${inspected}"]`),more=row.getByRole('button',{name:/Details and actions/});
 // Peeking at another chat is intentional and must not change selection.
 await row.hover();await expect(details).toBeVisible();
 assert.equal((await shell()).selectedSessionId,selected);
 const title=await details.locator('h3').textContent();
 // Pin/unpin are completed actions, not an implicit request to keep a hover
 // flyout locked. Restore the fixture's original pin state after both actions.
 const pin=()=>details.getByRole('button',{name:/^(Unpin|Pin) /});
 const wasPinned=(await pin().getAttribute('aria-label')).startsWith('Unpin ');
 await pin().click();
 await expect(details).toHaveCount(0);
 await page.mouse.move(1200,20);await expect(more).toBeFocused();
 await more.press('Enter');await expect(details).toBeVisible();
 assert.equal((await pin().getAttribute('aria-label')).startsWith('Unpin '),!wasPinned);
 await pin().click();await expect(details).toHaveCount(0);
 await page.mouse.move(1200,20);await more.press('Enter');await expect(details).toBeVisible();
 assert.equal((await pin().getAttribute('aria-label')).startsWith('Unpin '),wasPinned);
 assert.equal((await shell()).selectedSessionId,selected);
 await page.keyboard.press('Escape');await expect(details).toHaveCount(0);
 await more.focus();await page.keyboard.press('Enter');await expect(details).toBeVisible();
 await page.mouse.move(1200,20);
 await act('session.markUnread',{id:inspected});
 await expect(details.locator('h3')).toHaveText(title);
 // Keyboard selection has no outside pointerdown to dismiss the old panel.
 // Select a distinct visible row, keeping the inspected row mounted.
 const next=await rows.evaluateAll((rows,ids)=>rows.find(row=>!ids.includes(row.dataset.sessionId))?.dataset.sessionId,[selected,inspected]);
 const nextButton=recent.locator(`[data-session-id="${next}"] .a-nav-chat-select`);
 await nextButton.focus();await page.keyboard.press('Enter');
 await expect.poll(async()=>(await shell()).selectedSessionId).toBe(next);
 await expect(row).toBeVisible();
 await expect(details,'old chat actions close when the selected chat changes').toHaveCount(0);
 await expect(nextButton).toBeFocused();
 // A fresh explicit peek is still allowed without selecting its target.
 await more.focus();await page.keyboard.press('Enter');await expect(details).toBeVisible();
 await expect(details.locator('h3')).toHaveText(title);assert.equal((await shell()).selectedSessionId,next);
 await page.keyboard.press('Escape');await expect(more).toBeFocused();
 // Workspace browsing changes context even when selectedSessionId stays fixed.
 await more.focus();await page.keyboard.press('Enter');await expect(details).toBeVisible();
 await expect.poll(()=>page.evaluate(()=>{const state=window.amplifier.getShellState().snapshots.workspaces;return state.navigationResources?.includes('workspaceShortcuts')&&state.workspaceShortcuts?.some(item=>item.available!==false)})).toBe(true);
 const workspaces=await page.evaluate(()=>window.amplifier.getShellState().snapshots.workspaces.workspaceShortcuts);
 const workspace=workspaces.find(item=>item.available!==false)?.workspaceId;assert.ok(workspace);
 await act('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace,workWorkspaceTab:'chats'}});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.workSurface)).toBe('workspace');
 await expect(details,'old chat actions close when workspace browsing starts').toHaveCount(0);
 assert.equal((await shell()).selectedSessionId,next);
 const info=await page.evaluate(async()=>{const response=await fetch('/api/fixture/info');return response.json()});
 assert.deepEqual(info.runtimeStarts,[]);assert.deepEqual(info.runtimeSends,[]);assert.deepEqual(errors,[]);
 await page.screenshot({path:out+'/details-context.png'});
 await writeFile(out+'/result.json',JSON.stringify({passed:true,selected,inspected,next,workspace,pinActionsDismissed:true,requests:requests.length},null,2)+'\n');
 console.log('Navigation details close after confirmed pin actions, preserve passive peeking and current labels, close on keyboard chat/workspace navigation without stealing focus, and never start runtime work.');
}catch(error){primary=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{
 let failure;
 for(const cleanup of [()=>browser?.close(),()=>vite?.close(),async()=>{if(fixture.exitCode!==null)return;const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{const [code,signal]=await done;if(code!==0)throw Error(`Fixture cleanup ${code}/${signal}`)}finally{clearTimeout(timer)}}])try{await cleanup()}catch(error){failure??=error}
 if(logs)await writeFile(out+'/fixture.log',logs).catch(()=>{});
 if(!primary&&failure)throw failure;
}
