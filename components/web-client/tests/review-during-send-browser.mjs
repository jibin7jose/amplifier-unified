// Real UI with controlled admission delay. No provider or external writes.
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';

let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{setupJourney:'done',scheme:'system',navPinned:true},sessions:['a','b'].map(id=>({id,title:'Conversation '+id,sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[],draft:''})),workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'a',selectedWorkspaceId:'project',setup:{providers:[{id:'fixture',module:'provider-fixture',enabled:true,config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false},attention:{items:[],sessions:{}}};
const calls=[],errors=[];let browser,vite,heldSend,heldReview,delayReview=false;
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,message){for(let i=0;i<100&&!check();i++)await pause(10);assert.ok(check(),message)}
function reviewed(args){for(const item of state.attention.items)if(args.ids.includes(item.id)&&args.fingerprints?.[item.id]===item.fingerprint)item.read=true}
async function receipt(route){state.revision++;await route.fulfill({json:{accepted:true,state:shell.state()}})}
const shell=createShellHttpFixture(()=>state);
try{
 vite=await createServer({configFile:false,cacheDir:fileURLToPath(new URL('../.vite-legacy-mocks',import.meta.url)),root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});const page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installFixtureEvents);
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url());
  const shellResponse=shell.response(route.request());
  if(shellResponse!==undefined)return route.fulfill({json:shellResponse});
  if(url.pathname==='/api/state')return route.fulfill({json:shell.state()});
  if(url.pathname==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(url.pathname!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=route.request().postDataJSON();calls.push(body);const {action,args}=body;
  if(action==='shell.report')return route.fulfill({json:{accepted:true}});
  if(action==='conversation.send'){heldSend={route,body};return}
  if(action==='view.update')state.view={...state.view,...args.patch};
  if(action==='attention.read'){
   if(delayReview){heldReview={route,body};return}
   reviewed(args);
  }
  return receipt(route);
 });
 const emit=async()=>{state.revision++;await page.evaluate(state=>window.emitState(state),shell.state())};
 await page.goto(vite.resolvedUrls.local[0]);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this send bound to conversation A.');
 await page.getByRole('button',{name:'Send message',exact:true}).click();await until(()=>heldSend,'Send request should be held');
 state.attention.items=[{id:'completion:b',sessionId:'b',title:'Work finished',label:'Conversation b',fingerprint:'completion-1',read:false}];await emit();
 // The header Activity button is removed; the shared review surface remains available.
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'activity'}}));
 await page.getByRole('button',{name:'Mark reviewed: Conversation b',exact:true}).click();
 await until(()=>state.attention.items[0].read,'Review must be acknowledged while the send is still pending');
 assert.equal(calls.filter(call=>call.action==='conversation.send').length,1);
 assert.equal(heldSend.body.args.sessionId,'a');

 // A late review of one completion cannot dismiss its newer replacement.
 delayReview=true;state.attention.items[0]={...state.attention.items[0],read:false,fingerprint:'completion-2'};await emit();
 await page.getByRole('button',{name:'Mark reviewed: Conversation b',exact:true}).click();await until(()=>heldReview,'Review should use its independent ordered lane');
 state.attention.items[0]={...state.attention.items[0],fingerprint:'completion-3'};await emit();
 reviewed(heldReview.body.args);await receipt(heldReview.route);
 assert.equal(state.attention.items[0].read,false);assert.equal(heldReview.body.args.fingerprints['completion:b'],'completion-2');
 await page.getByRole('button',{name:'Mark reviewed: Conversation b',exact:true}).waitFor();
 delayReview=false;await page.getByRole('button',{name:'Mark reviewed: Conversation b',exact:true}).click();await until(()=>state.attention.items[0].read,'New completion can be reviewed independently');

 state.sessions[0].messages=[{id:'sent',role:'user',text:heldSend.body.args.text,inputId:heldSend.body.id}];await receipt(heldSend.route);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 assert.equal(calls.filter(call=>call.action==='conversation.send').length,1);assert.deepEqual(errors,[]);
 console.log('Verified: review during a pending send, explicit original send target, newer completion protection, no duplicate send.');
}finally{await browser?.close();await vite?.close()}
