import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const recovered=i=>({id:'recovered-'+i,role:'user',text:'Retained result for job '+i,createdAt:i+1,observation:{source:'local-job-recovery',id:'job-'+i,recovery:{job_id:'job-'+i,call_id:'call-'+i,status:i===0?'cancelled':'returned',outcome:i===0?'unconfirmed':'tool_report_unverified',reason:'restored_evidence'}}});
const messages=[{id:'user',role:'user',createdAt:1789990000,text:'Review the retained work'},...Array.from({length:35},(_,i)=>recovered(i)),{id:'response',role:'assistant',createdAt:1789990100,text:'The saved results are available.'},recovered(35),recovered(36),{id:'quote',role:'user',createdAt:1789990200,text:'Recovered work update'}];
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser,vite;const errors=[],calls=[];
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.port){clearTimeout(timer);resolve(value)}}catch{}})});
 const target=`http://127.0.0.1:${ready.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(target+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 const {alpha}=await control({op:'heavy',messages:1,otherMessages:1,chars:50,nodes:0});
 await control({op:'patch',sessions:{[alpha]:{messages,execution:{turns:[],nodes:[{id:'saved-tool',kind:'tool',sessionId:alpha,toolCallId:'call-34',output:'Retained worker report',label:'Research'}]}}}});
 vite=await createServer({configFile:false,root:root+'frontend',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:headers});page.on('pageerror',e=>errors.push(e.message));
 page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST')calls.push(request.postDataJSON())});
 await page.goto(vite.resolvedUrls.local[0]);await showFixtureConversation(page);
 await expect(page.locator('[data-message-id="response"]')).toBeVisible();
 await expect(page.locator('[data-message-id="quote"]')).toBeVisible();
 await expect(page.locator('details.a-recovery-group')).toHaveCount(0);
 await expect(page.getByText('Saved work notices',{exact:false})).toHaveCount(0);
 await expect(page.locator('[data-message-id^="recovered-"]')).toHaveCount(0);
 const saved=await fetch(target+'/api/state',{headers}).then(response=>response.json());
 assert.equal(saved.sessions.find(row=>row.id===alpha).messages.filter(row=>row.observation?.source==='local-job-recovery').length,37,'canonical recovery records are preserved');
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/tmp/unified-recovery-group-mobile.png'});
 const effects=calls.filter(call=>['conversation.send','session.recover'].includes(call.action)||call.action==='runtime.control'&&!['configuration.providers','configuration.catalog'].includes(call.args.operation));
 assert.deepEqual(effects,[],'Opening saved evidence must not execute or replay work');
 assert.deepEqual(errors,[]);
 console.log('Recovery presentation passed: all37records retained, noinlinechatnotices, normalmessages visible, noexecutionandmobilewidth.');
}finally{await browser?.close();await vite?.close();fixture.kill('SIGTERM')}
