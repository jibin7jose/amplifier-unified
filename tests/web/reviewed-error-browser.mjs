// Packaged UI, shared actions and disposable histories; no provider calls.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 ['-u',fileURLToPath(new URL('../../tests/fixtures/chat_library_server.py',import.meta.url))],
 {env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',browser,page;
fixture.stderr.on('data',value=>logs+=value);
const ready=new Promise((resolve,reject)=>{
 let output='';fixture.stdout.on('data',value=>{output+=value;const line=output.split('\n').find(value=>value.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
 fixture.once('error',reject);fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${logs}`)));
});
try{
 const port=await ready;
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const api=(path,body)=>page.evaluate(async([path,body])=>{
  const headers={'X-Amplifier-Client':window.amplifier.getState().client.id};
  const response=await fetch(path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw Error(await response.text());return response.json();
 },[path,body]);
 const info=()=>api('/api/fixture/info');
 const query=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const recent=page.locator('[data-sidebar-section=recent]'),details=page.locator('.a-navigation-flyout');
 const row=id=>recent.locator('.a-nav-chat[data-session-id="'+id+'"]');
 await page.goto(`http://127.0.0.1:${port}`);
 await page.locator('.a-home-filters').waitFor();
 const openNavigation=page.getByRole('button',{name:'Open navigation',exact:true});
 if(await openNavigation.isVisible())await openNavigation.click();
 await recent.locator('.a-nav-chat').first().waitFor();
 const before=await info(),failed=before.state.sessions.find(row=>row.nativeIdentity==='alpha-197');
 const workspace=state=>state.workspaceExplorer.rows.find(row=>row.path===failed.workspace);
 assert.equal(workspace(before.state).activityCounts.attention,2);
 await row(failed.id).hover();await row(failed.id).getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await expect(details.locator('.a-navigation-detail-status')).toHaveText('Needs attention');
 await details.getByRole('button',{name:'Mark error reviewed',exact:true}).click();
 await expect.poll(async()=>(await info()).state.attention.items.find(row=>row.id==='session:'+failed.id).read).toBe(true);
 await expect(details.locator('.a-navigation-detail-status')).toHaveText('Idle');
 assert.equal((await query()).recentNavigation.items.find(item=>item.id===failed.id).activity.kind,'idle');
 const reviewed=await info(),saved=reviewed.state.sessions.find(row=>row.id===failed.id);
 assert.equal(saved.error,failed.error);assert.equal(saved.status,failed.status);assert.deepEqual(saved.messages,failed.messages);
 assert.equal(workspace(reviewed.state).activityCounts.attention,1);
 const agent=await api('/api/fixture/agent',{args:{action:'shell.query',args:{clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId:'chats'}}});
 assert.equal(agent.result.recentNavigation.items.find(item=>item.id===failed.id).activity.kind,'idle');
 await page.keyboard.press('Escape');await page.reload();await row(failed.id).waitFor();
 await expect.poll(async()=>(await query()).recentNavigation.items.find(item=>item.id===failed.id).activity.kind).toBe('idle');
 const approval=before.state.sessions.find(row=>row.nativeIdentity==='alpha-199');
 await row(approval.id).hover();await row(approval.id).getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await expect(details.locator('.a-navigation-detail-status')).toHaveText('Approval requested');
 assert.equal(await details.getByRole('button',{name:'Mark error reviewed',exact:true}).count(),0);
 await page.keyboard.press('Escape');
 const completed=before.state.sessions.find(row=>row.nativeIdentity==='alpha-198');
 const completedNotice=(await info()).state.attention.items.find(item=>item.sessionId===completed.id);
 // Establish the reviewed prerequisite explicitly; the public action below must
 // make an actual read-to-unread transition, not assert an already-unread row.
 await api('/api/actions',{action:'attention.read',args:{ids:[completedNotice.id],fingerprints:{[completedNotice.id]:completedNotice.fingerprint}}});
 await expect.poll(async()=>(await info()).state.attention.items.find(item=>item.sessionId===completed.id)?.read).toBe(true);
 const beforeUnread=await info();
 await row(completed.id).hover();await row(completed.id).getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await details.getByRole('button',{name:'Mark unread',exact:true}).click();
 await expect.poll(async()=>(await info()).state.attention.items.find(item=>item.sessionId===completed.id)?.read).toBe(false);
 const afterUnread=await info();
 assert.deepEqual(afterUnread.state.attention.items.filter(item=>item.sessionId!==completed.id),beforeUnread.state.attention.items.filter(item=>item.sessionId!==completed.id),'Mark unread changes only the intended conversation attention');
 assert.equal(afterUnread.state.selectedSessionId,beforeUnread.state.selectedSessionId);
 assert.deepEqual(afterUnread.state.sessions.find(row=>row.id===completed.id).messages,completed.messages);
 // The same flyout now offers the inverse action for the actual unread state.
 await row(completed.id).hover();await row(completed.id).getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await expect(details.getByRole('button',{name:'Mark unread',exact:true})).toHaveCount(0);
 await details.getByRole('button',{name:'Mark read',exact:true}).click();
 await expect.poll(async()=>(await info()).state.attention.items.filter(item=>item.sessionId===completed.id).every(item=>item.read)).toBe(true);
 await row(completed.id).hover();await row(completed.id).getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await expect(details.getByRole('button',{name:'Mark unread',exact:true})).toBeVisible();
 await page.keyboard.press('Escape');
 await page.getByRole('group',{name:'Sidebar view'}).getByRole('button',{name:'Status',exact:true}).click();
 const moreSaved=page.getByRole('button',{name:'Show more pick up again chats',exact:true});
 await moreSaved.click();
 const statusRow=page.locator('.a-status-sidebar .a-nav-chat[data-session-id="'+completed.id+'"]');
 await statusRow.hover();await statusRow.getByRole('button',{name:/Details and actions/}).click();
 await details.getByRole('button',{name:'Mark unread',exact:true}).click();
 const unreadRow=page.locator('[data-status-group=unread] .a-nav-chat[data-session-id="'+completed.id+'"]');
 await expect(unreadRow).toBeVisible();
 await expect(unreadRow.locator('.a-navigation-status')).toHaveAttribute('data-kind','unread');
 await unreadRow.hover();await unreadRow.getByRole('button',{name:/Details and actions/}).click();
 await expect(details.getByRole('button',{name:'Mark read',exact:true})).toBeVisible();
 await expect(details.getByRole('button',{name:'Mark unread',exact:true})).toHaveCount(0);
 await page.keyboard.press('Escape');
 // Home uses the same receipts, including explicit manual unread marks.
 await page.getByRole('group',{name:'Work view'}).getByRole('button',{name:'Brief',exact:true}).click();
 const card=page.locator('.a-work-card[data-session-id="'+completed.id+'"]');
 await card.getByRole('button',{name:/Mark response reviewed/}).click();
 await expect.poll(async()=>(await info()).state.attention.items.filter(item=>item.sessionId===completed.id).every(item=>item.read)).toBe(true);
 assert.deepEqual((await info()).state.sessions.find(row=>row.id===completed.id).messages,completed.messages);
 const final=await info();assert.deepEqual(final.runtimeStarts,[]);assert.deepEqual(final.runtimeSends,[]);assert.deepEqual(errors,[]);
 console.log('PASS: exact review clears chat/workspace markers, survives reload, retains errors/approvals; Browse and Status offer the current read action; Home clears manual unread; no work starts.');
}finally{
 await browser?.close();
 if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
