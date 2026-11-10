// Real built client and native-history fixture; no live data or model providers.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks/focus-navigation');
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 ['-u',join(root,'tests/fixtures/chat_library_server.py')],
 {env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',browser,page,failure;
fixture.stderr.on('data',chunk=>{logs=(logs+chunk).slice(-12000)});
const report={checks:[],errors:[],liveProvider:false};
try{
 const port=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout: '+logs)),30000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+logs))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const data=JSON.parse(line);if(data.port){clearTimeout(timer);resolve(data.port)}}catch{}});
 });
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1440,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(15000);page.on('pageerror',error=>report.errors.push(error.message));
 const actions=[];
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const info=async()=>{const response=await page.request.get(`http://127.0.0.1:${port}/api/fixture/info`);assert.equal(response.status(),200);return response.json()};
 const snapshot=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats.workHome);
 const ready=()=>expect(page.locator('.a-home-filters')).toBeVisible();
 const card=page.locator('.a-home-focus .a-work-card');
 const identity=()=>card.getAttribute('data-session-id');
 const next=page.locator('[data-focus-move=next]'),previous=page.locator('[data-focus-move=previous]');
 const filters=page.getByRole('group',{name:'Filter work'});
 const home=async()=>{await page.getByRole('navigation',{name:'Site navigation'}).getByRole('button',{name:'Home',exact:true}).click();await ready()};
 await page.goto(`http://127.0.0.1:${port}`);await ready();
 await page.getByRole('group',{name:'Work view'}).getByRole('button',{name:'Focus',exact:true}).click();
 await expect(card).toHaveCount(1);await expect(previous).toBeDisabled();
 const initial=await info(),selected=(await state()).selectedSessionId,first=await identity();
 const actionStart=actions.length;
 await next.click();await expect(card).not.toHaveAttribute('data-session-id',first);
 await previous.click();await expect(card).toHaveAttribute('data-session-id',first);
 const after=await info();
 assert.deepEqual(after.state.attention,initial.state.attention);
 assert.deepEqual(after.state.sessions.map(s=>({id:s.id,status:s.status,approvals:s.approvals})),initial.state.sessions.map(s=>({id:s.id,status:s.status,approvals:s.approvals})));
 assert.equal((await state()).selectedSessionId,selected);
 assert.deepEqual(after.runtimeStarts,initial.runtimeStarts);assert.deepEqual(after.runtimeSends,initial.runtimeSends);
 assert.equal(actions.slice(actionStart).filter(row=>/^(attention\.|session\.|conversation\.|approval\.)/.test(row.action)).length,0);
 report.checks.push('Next/Previous browse required attention without selection, read/approval/status changes, sends or worker starts');
 await page.screenshot({path:join(output,'focus-desktop.png')});

 await filters.getByRole('button',{name:/^Pick up again/}).click();await expect(card).toHaveAttribute('data-kind','idle');
 const savedPage=(await snapshot()).groups.idle;
 assert.ok(savedPage.pages>1);assert.ok(savedPage.items.length<=50);
 for(let i=1;i<savedPage.items.length;i++)await next.click();
 await expect(card).toHaveAttribute('data-session-id',savedPage.items.at(-1).id);
 await next.focus();await page.keyboard.press('Enter');
 await expect.poll(async()=>(await snapshot()).groups.idle.index).toBe(1);
 await expect(next).toBeFocused();
 const secondPage=(await snapshot()).groups.idle;
 await expect(card).toHaveAttribute('data-session-id',secondPage.items[0].id);
 await previous.click();await expect(card).toHaveAttribute('data-session-id',savedPage.items.at(-1).id);
 await expect(previous).toBeFocused();
 const retained=await identity();
 await page.reload();await ready();await expect(card).toHaveAttribute('data-session-id',retained);
 report.checks.push('Focus status filters stay single-item; keyboard paging crosses real bounded pages both ways and reload retains the cursor');

 await card.locator('.a-work-card-open').click();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await expect(composer).toBeVisible();await composer.fill('Keep this draft while browsing Focus');
 await expect.poll(async()=>(await state()).view.draft).toBe('Keep this draft while browsing Focus');
 // This older chat is beyond the bounded Recent list. Pin it explicitly, like
 // the reported UI, so the test actually has a visible selected row to inspect.
 await page.evaluate(id=>window.amplifier.dispatch('session.pin',{id,pinned:true}),retained);
 const openNavigation=page.getByRole('button',{name:'Open navigation',exact:true});
 if(await openNavigation.isVisible())await openNavigation.click();
 await expect(page.locator(`.a-nav-chat.is-selected[data-session-id="${retained}"]`).first()).toBeVisible();
 await home();await expect(card).toHaveAttribute('data-session-id',retained);
 await expect(page.locator('.a-nav-chat.is-selected')).toHaveCount(0);
 await expect(page.locator('.a-nav-chat-select[aria-current=page]')).toHaveCount(0);
 await expect(page.locator('header')).not.toContainText('Home');
 await expect(page).toHaveTitle('Amplifier');
 assert.equal((await state()).selectedSessionId,retained);
 assert.equal((await state()).view.draft,'Keep this draft while browsing Focus');
 await card.locator('.a-work-card-open').click();await expect(composer).toContainText('Keep this draft while browsing Focus');
 await home();
 report.checks.push('Home removes stale sidebar and title selection while retaining the exact chat, cursor and private draft');

 await filters.getByRole('button',{name:/^Needs you/}).click();
 const failed=initial.state.sessions.find(row=>row.nativeIdentity==='alpha-197');
 if(await identity()!==failed.id)await next.click();
 await expect(card).toHaveAttribute('data-session-id',failed.id);
 await card.locator('.a-work-card-open').click();
 await expect(page.getByRole('button',{name:'Continue conversation',exact:true})).toBeVisible();
 await home();
 await expect(page.getByRole('button',{name:'Continue conversation',exact:true})).toHaveCount(0);
 await card.locator('.a-work-card-open').click();
 await expect(page.getByRole('button',{name:'Continue conversation',exact:true})).toBeVisible();
 await home();
 report.checks.push('A retained chat error stays on its chat and returns there; Home does not leak its recovery actions');

 const listed=await page.evaluate(()=>window.amplifier.dispatch('workspace.list',{query:'playground'}));
 const project=listed.result.items.find(row=>row.path===initial.paths.two);
 assert.ok(project,'The registered research project is available through the workspace catalog');
 await page.getByLabel('Work scope',{exact:true}).selectOption(project.id);await ready();
 await filters.getByRole('button',{name:'Overview',exact:true}).click();
 await expect(card).toHaveAttribute('data-kind','idle');
 await expect(page.locator('.a-focus-navigation [role=status]')).toContainText('1 of 3');
 const scopedFirst=await identity();
 await next.click();await next.click();await expect(next).toHaveText('Start again');
 await next.click();await expect(card).toHaveAttribute('data-session-id',scopedFirst);
 await next.click();const scopedSecond=await identity();
 await page.getByRole('button',{name:'Open workspace',exact:true}).click();
 await expect(page.getByRole('group',{name:'Workspace view',exact:true})).toBeVisible();
 await expect(page.locator('.a-nav-chat.is-selected')).toHaveCount(0);
 await home();await expect(card).toHaveAttribute('data-session-id',scopedSecond);
 await page.getByLabel('Search your work',{exact:true}).fill('Beta 000');
 await expect(card.locator('h3')).toHaveText('Beta 000');await expect(next).toHaveCount(0);
 await page.getByRole('button',{name:'Clear work search',exact:true}).click();
 await expect(page.locator('.a-focus-navigation [role=status]')).toContainText('1 of 3');
 report.checks.push('Scope/search isolate passes; a project visit retains position; the last item explicitly restarts without hiding work');

 await next.click();await expect(card).toHaveAttribute('data-session-id',scopedSecond);
 await page.getByLabel('Search your work',{exact:true}).fill('no-matching-focus-work');
 await expect(page.getByRole('heading',{name:'No matching work.',exact:true})).toBeVisible();
 await page.reload();await ready();
 await page.getByRole('button',{name:'Clear work search',exact:true}).click();
 await expect(page.locator('.a-focus-navigation [role=status]')).toContainText('1 of 3');
 await next.click();
 await page.getByRole('group',{name:'Work view'}).getByRole('button',{name:'Brief',exact:true}).click();
 await page.getByLabel('Work scope',{exact:true}).selectOption('managed');
 await expect(page.getByRole('heading',{name:'A fresh place to begin.',exact:true})).toBeVisible();
 await page.getByLabel('Work scope',{exact:true}).selectOption(project.id);await ready();
 await page.getByRole('group',{name:'Work view'}).getByRole('button',{name:'Focus',exact:true}).click();
 await expect(page.locator('.a-focus-navigation [role=status]')).toContainText('1 of 3');
 await next.click();
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{workHomeScope:'focus-unavailable-project',workHomePages:{}}}));
 await expect(page.getByRole('alert')).toContainText('This workspace is unavailable.');
 await page.evaluate(id=>window.amplifier.dispatch('view.update',{patch:{workHomeScope:id,workHomePages:{}}}),project.id);
 await ready();await expect(page.locator('.a-focus-navigation [role=status]')).toContainText('1 of 3');
 report.checks.push('Empty searches survive reload without reviving an old pass; empty and unavailable scopes reset even when changed from Brief');

 await page.getByRole('button',{name:'See all 3',exact:true}).click();
 await expect(page.locator('.a-adaptive-home')).toHaveAttribute('data-view','brief');
 await expect(page.locator('.a-home-filtered .a-work-card')).toHaveCount(3);
 await page.getByRole('group',{name:'Work view'}).getByRole('button',{name:'Focus',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 await expect(page.locator('.a-work-brand')).toBeVisible();
 await expect(page.locator('header')).not.toContainText('Home');
 await expect(next).toBeVisible();await next.click();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:join(output,'focus-phone.png')});
 await page.setViewportSize({width:320,height:740});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.emulateMedia({forcedColors:'active'});
 await expect(next).toBeVisible();await page.screenshot({path:join(output,'focus-forced-colors.png')});
 report.checks.push('See all opens a list; Focus and its app identity fit narrow screens and forced colors');
 assert.deepEqual(report.errors,[]);
 report.build=JSON.parse(await readFile(join(root,'amplifier_web/static/build.json'),'utf8'));report.passed=true;
}catch(error){failure=error;report.passed=false;report.failure=error.stack;await page?.screenshot({path:join(output,'failure.png')}).catch(()=>{});report.text=await page?.locator('body').innerText().catch(()=>null)}
finally{
 await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){
  const exited=once(fixture,'exit');fixture.kill('SIGTERM');let timer;
  try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture cleanup timeout')),10000)})]);report.fixtureClosed=true}
  catch(error){fixture.kill('SIGKILL');await exited;failure ||= error;report.cleanupError=error.message;report.passed=false}
  finally{clearTimeout(timer)}
 }
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}
if(failure)throw failure;
