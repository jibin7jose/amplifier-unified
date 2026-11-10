import {openChatAdvancedPage,showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const origin='http://127.0.0.1:8967';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/native_provider_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let logs='';fixture.stdout.on('data',s=>logs+=s);fixture.stderr.on('data',s=>logs+=s);
for(let i=0;i<100;i++){try{if((await fetch(origin+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(origin);await page.waitForSelector('#amp-one');
 await showFixtureConversation(page);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Unsent native fixture draft');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='Unsent native fixture draft');
 const before=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await openChatAdvancedPage(page,'Context & recovery');
 await page.getByRole('button',{name:'Check provider support',exact:true}).click();
 await page.getByText(/Native direction is eligible for/).waitFor();
 await page.getByRole('button',{name:'Compact provider context',exact:true}).click();
 await page.getByText('Saved provider context: created.',{exact:true}).waitFor();
 const agent=await page.request.post(origin+'/fixture/agent-native');
 assert.equal(agent.status(),200);
 await page.waitForFunction(()=>!['working','running','pending','queued'].includes(window.amplifier.getState().management.phase));
 const state=await page.evaluate(()=>window.amplifier.getState());
 assert.equal(state.selectedSessionId,before);
 assert.equal(state.view.draft,'Unsent native fixture draft');
 assert.equal(state.runtimeControl[before]['native.status'].checkpoint,'created');
 assert.ok(!JSON.stringify(state).includes('OPAQUE_PRIVATE_FIXTURE'));
 await page.reload();await page.getByText('Saved provider context: created.',{exact:true}).waitFor();
 await page.screenshot({path:'/tmp/amplifier-native-provider-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await page.locator('[data-part="native-provider-settings"]').scrollIntoViewIfNeeded();
 assert.equal(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
 await page.screenshot({path:'/tmp/amplifier-native-provider-mobile.png',animations:'disabled'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({browserControls:true,agentSharedStatus:true,opaquePrivate:true,selectionPreserved:true,draftPreserved:true,reload:true,mobile:true,liveProvider:false}));
}catch(error){console.error(logs.slice(-3000));throw error}
finally{await browser.close();fixture.kill('SIGTERM')}
