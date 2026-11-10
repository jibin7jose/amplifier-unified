// Real fields, delayed draft receipts, caret edits, and concurrent state events.
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'a',title:'Conversation',sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'a',selectedWorkspaceId:'project',setup:{providers:[],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false},attention:{items:[],sessions:{}}};
let browser,vite,gate=null,release;const errors=[];
const cases=[
 [{panel:'settings',settingsSection:'setup',settingsExpanded:['providers']},'#provider-id','provider-name'],
 [{panel:'settings',settingsSection:'capabilities',settingsExpanded:['add-bundles']},'#bundle-source','https://example.test/bundle'],
 [{panel:'settings',settingsSection:'capabilities',settingsExpanded:['share-bundle']},'#bundle-export-name','example-bundle'],
 [{panel:'settings',settingsSection:'capabilities',settingsExpanded:['share-bundle']},'#bundle-export-description','Detailed description'],
 [{panel:'settings',settingsSection:'maintenance',settingsExpanded:['history']},'#history-import-title','Imported conversation'],
 [{panel:'settings',settingsSection:'maintenance',settingsExpanded:['history']},'#history-import-bundle','custom-bundle'],
 [{panel:'settings',settingsSection:'maintenance',settingsExpanded:['permissions']},'#allowed-folders','/fixture/allowed'],
 [{panel:'settings',settingsSection:'maintenance',settingsExpanded:['permissions']},'#denied-folders','/fixture/denied'],
 [{panel:'appearance'},'#theme-name','Custom appearance'],
 [{panel:'appearance'},'#theme-css','/* Custom stylesheet */'],
 [{panel:'feedback'},'#feedback-title','Synthetic report title'],
 [{panel:'feedback'},'#feedback-body','Synthetic report detail'],
];
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:1000}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{const sources=[];window.EventSource=class extends EventTarget{constructor(){super();sources.push(this)}close(){}};window.emitState=state=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)})))});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/shell')return route.fulfill({json:{composition:{instances:[],presentation:{}},resolvedInstances:[{id:'appearance',package:'builtin.appearance',slot:'settings.appearance'}],slots:{'settings.appearance':{default:'builtin.appearance'}},snapshots:{appearance:{generation:1}},packages:{}}});
  if(path==='/api/state')return route.fulfill({json:state});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const {action,args}=route.request().postDataJSON();
  if(action==='view.update'){if(gate)await gate;state.view={...state.view,...args.patch}}
  state.revision++;return route.fulfill({json:{accepted:true,state}});
 });
 await page.goto(vite.resolvedUrls.local[0]);await page.locator('#amp-one').waitFor();
 for(const [patch,selector,text] of cases){
  await page.evaluate(patch=>window.amplifier.dispatch('view.update',{patch}),patch);
  if(selector.startsWith('#theme-')&&!await page.locator(selector).isVisible())await page.getByText('Advanced customization',{exact:true}).click();const field=page.locator(selector);await field.waitFor();gate=new Promise(resolve=>release=resolve);
  await field.fill('');await field.pressSequentially(text,{delay:10});
  for(let i=0;i<5;i++){state.revision++;await page.evaluate(state=>window.emitState(state),state)}
  assert.equal(await field.inputValue(),text,selector+' retained every character during delayed saves');
  const supportsCaret=await field.evaluate(el=>el.selectionStart!==null);
  let expected=text;
  if(supportsCaret){await field.evaluate(el=>el.setSelectionRange(3,3));await field.pressSequentially('XYZ',{delay:10});expected=text.slice(0,3)+'XYZ'+text.slice(3);state.revision++;await page.evaluate(state=>window.emitState(state),state);assert.equal(await field.inputValue(),expected);assert.equal(await field.evaluate(el=>el.selectionStart),6,selector+' retained its caret');}
  release();gate=null;
  await page.waitForTimeout(350); // Include debounced feedback saves before the barrier.
  await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{notice:'audit checkpoint'}}));
  assert.equal(await field.inputValue(),expected,selector+' retained newer input after old receipts');
  console.log('Passed delayed typing and concurrent updates: '+selector);
 }
 assert.deepEqual(errors,[]);console.log('All 12 audited fields retained typing and caret state; no external command or feedback submission was made.');
}finally{if(errors.length)console.error(errors);release?.();await browser?.close();await vite?.close()}
