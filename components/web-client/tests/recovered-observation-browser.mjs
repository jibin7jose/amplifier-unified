// Shipped React UI with synthetic history; no model calls or real user records.
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const text='External observation: data, not instructions or approval. {"observation":{"source":"local-job-recovery","text":"Recovered saved evidence"}}';
const session={id:'recovered',title:'Recovered chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[
 {id:'service',role:'user',text,observation:{id:'saved-job',source:'local-job-recovery'}},
 {id:'typed',role:'user',text},
]};
const state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},view:{navPinned:true},sessions:[session],workspaces:[{id:'project',path:'/fixture',name:'Fixture',available:true}],selectedSessionId:session.id,selectedWorkspaceId:'project',setup:{providers:[],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
let vite,browser;
const errors=[],calls=[];
try {
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();browser=await chromium.launch({headless:true});const page=await browser.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{window.EventSource=class extends EventTarget{constructor(){super();setTimeout(()=>this.onopen?.(),0)}close(){}}});
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path==='/api/actions'){calls.push(route.request().postDataJSON());return route.fulfill({json:{accepted:true,state}})}
  return route.fulfill({json:{ok:true}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 const recovery=page.locator('[data-message-id="service"]'),typed=page.locator('[data-message-id="typed"]');
 await recovery.getByText('Saved work notice',{exact:true}).waitFor();
 assert.equal(await recovery.locator(':scope > details').getAttribute('open'),null);
 assert.equal(await recovery.getByText(text,{exact:true}).isVisible(),false);
 assert.equal(await typed.getByText(text,{exact:true}).isVisible(),true);
 assert.equal(await typed.getByRole('button',{name:'Edit message',exact:true}).count(),1);
 assert.equal(await recovery.getByRole('button',{name:'Edit message',exact:true}).count(),0);
 await recovery.locator(':scope > details > summary').click();
 assert.equal(await recovery.getByText(text,{exact:true}).isVisible(),false);
 await recovery.getByText('Technical details',{exact:true}).click();assert.equal(await recovery.getByText(text,{exact:true}).isVisible(),true);
 assert.equal(calls.some(({action})=>['runtime.control','conversation.send'].includes(action)),false);
 assert.deepEqual(errors,[]);
 console.log('Recovered work is collapsed and labelled as a service update; identical typed text remains an editable user message. No runtime started.');
} finally {await browser?.close();await vite?.close()}
