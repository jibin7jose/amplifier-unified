// Real client rendering against synthetic HTTP/SSE state; no provider or history writes.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';

const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'output/sidebar-controls';
const baseline=process.env.AMPLIFIER_SIDEBAR_BASELINE==='1';
const results=[],errors=[],calls=[];
let browser,vite;
await mkdir(artifacts,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false}});
 await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 for(const scenario of [
  {name:'desktop-216',width:1280,navWidth:216},
  {name:'desktop-320',width:1280,navWidth:320},
  {name:'pinned-216',width:1280,navWidth:216,pinned:true},
  {name:'status-216',width:1280,navWidth:216,navMode:'status'},
  {name:'mobile-320',width:320,touch:true},
  {name:'mobile-390',width:390,touch:true},
  {name:'enlarged-text',width:1280,navWidth:216,enlarged:true},
  {name:'forced-colors',width:1280,navWidth:216,forced:true},
 ]){
  let state={revision:1,settings:{workspace:'/fixture',bundle:'anchors'},runtime:{available:true},
   view:{navPinned:true,navExpanded:true,navWidth:scenario.navWidth||216,navAge:'all',navMode:scenario.navMode||'browse'},
   sessions:[{id:'chat',sessionKind:'root',title:'Saved synthetic chat',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[]}],
   workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',
   setup:{providers:[{id:'fixture',module:'provider-fixture',enabled:true,config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
  if(scenario.pinned)state.pinnedNavigation={items:[{id:'chat:chat',kind:'chat',row:state.sessions[0]}],total:1};
  const shell=createShellHttpFixture(()=>state),page=await browser.newPage({viewport:{width:scenario.width,height:900},hasTouch:!!scenario.touch,isMobile:!!scenario.touch});
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(installFixtureEvents);
  await page.route('**/api/**',route=>{
   const request=route.request(),path=new URL(request.url()).pathname,response=shell.response(request);
   if(response!==undefined)return route.fulfill({json:response});
   if(path==='/api/state')return route.fulfill({json:shell.state()});
   if(path==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
   if(path==='/api/actions'){
    const body=request.postDataJSON();calls.push(body);
    if(body.action==='view.update')state={...state,revision:state.revision+1,view:{...state.view,...body.args.patch}};
    return route.fulfill({json:{accepted:true,state:shell.state(),result:{}}});
   }
   return route.fulfill({json:{ok:true}});
  });
  if(scenario.forced)await page.emulateMedia({forcedColors:'active'});
  await page.goto(vite.resolvedUrls.local[0]);
  await page.getByRole('slider',{name:'Filter conversations by age'}).waitFor();
  await page.locator('.a-sidebar-section').first().waitFor();
  if(scenario.enlarged)await page.addStyleTag({content:'#amp-one{--a-text-xs:24px;--a-text-sm:26px;--a-text-base:28px;font-size:28px}'});
  const slider=page.getByRole('slider',{name:'Filter conversations by age'});
  const metrics=await page.evaluate(()=>{
   const filter=document.querySelector('.a-chat-age-filter'),label=filter.querySelector('label'),range=filter.querySelector('input'),first=document.querySelector('.a-sidebar-section');
   const rect=node=>{const {x,y,width,height}=node.getBoundingClientRect();return {x,y,width,height}};
   const style=node=>{const s=getComputedStyle(node);return {fontSize:s.fontSize,lineHeight:s.lineHeight,color:s.color,marginTop:s.marginTop,marginBottom:s.marginBottom,height:s.height,padding:s.padding}};
   const buttons=[...document.querySelectorAll('.a-sidebar-modes button')].map(button=>({label:button.getAttribute('aria-label')||button.textContent,rect:rect(button),hit:button.contains(document.elementFromPoint(button.getBoundingClientRect().x+button.offsetWidth/2,button.getBoundingClientRect().y+button.offsetHeight/2))}));
   return {filter:rect(filter),label:rect(label),range:rect(range),first:rect(first),labelStyle:style(label),rangeStyle:style(range),firstStyle:style(first),gap:first.getBoundingClientRect().top-filter.getBoundingClientRect().bottom,sidebar:rect(document.querySelector('#workspace-navigation')),buttons,sectionMargins:[...document.querySelectorAll('.a-sidebar-section')].map(node=>getComputedStyle(node).marginTop)};
  });
  results.push({scenario:scenario.name,baseline,...metrics});
  await page.screenshot({path:`${artifacts}/${scenario.name}.png`});
  assert.equal(await slider.getAttribute('aria-valuetext'),'All activity dates');
  assert.equal(await page.locator('.a-chat-age-stops>span').count(),6);
  assert.equal(await page.locator('#workspace-navigation').getByRole('button',{name:'Home',exact:true}).count(),baseline?1:0);
  if(!baseline){
   assert.equal(await page.locator('.a-sidebar-new .lucide-message-square-plus').count(),1);
   assert.ok(metrics.gap<=16,JSON.stringify(metrics));
   assert.ok(metrics.range.height>= (scenario.touch?44:24));
   assert.ok(metrics.filter.x>=metrics.sidebar.x&&metrics.filter.x+metrics.filter.width<=metrics.sidebar.x+metrics.sidebar.width);
   assert.equal(await page.locator('.a-chat-age-filter').evaluate(node=>node.scrollWidth<=node.clientWidth),true);
   assert.ok(parseFloat(metrics.labelStyle.fontSize)<=(scenario.enlarged?24:12));
   assert.ok(metrics.sectionMargins.slice(1).every(margin=>margin==='16px'),'Later section rhythm is unchanged');
   assert.equal(metrics.buttons.length,3);
   for(const button of metrics.buttons){
    assert.ok(button.hit,JSON.stringify(button));
    assert.ok(button.rect.x>=metrics.sidebar.x&&button.rect.x+button.rect.width<=metrics.sidebar.x+metrics.sidebar.width);
   }
  }
  // Native keyboard semantics and meaningful values remain six-stop choices.
  await slider.focus();
  await slider.press('Home');
  await page.waitForFunction(()=>window.amplifier.getState().view.navAge==='24h');
  for(const age of ['7d','14d','30d','90d','all']){
   await slider.press('ArrowRight');
   await page.waitForFunction(age=>window.amplifier.getState().view.navAge===age,age);
  }
  assert.equal(await slider.evaluate(node=>document.activeElement===node),true,'Filter edits retain focus');
  await slider.press('Home');
  const box=await slider.boundingBox();
  if(scenario.touch)await page.touchscreen.tap(box.x+box.width-2,box.y+box.height/2);
  else await slider.click({position:{x:box.width-2,y:box.height/2}});
  await page.waitForFunction(()=>window.amplifier.getState().view.navAge==='all');
  // An API-saved year is shown truthfully until an explicit six-stop choice.
  state={...state,revision:state.revision+1,view:{...state.view,navAge:'1y'}};
  await page.evaluate(state=>window.emitFixtureState(state),shell.state());
  await page.waitForFunction(()=>document.querySelector('.a-chat-age-filter input').value==='9');
  assert.equal(await slider.getAttribute('aria-valuetext'),'Within the last year');
  assert.equal(await page.locator('.a-chat-age-filter strong').innerText(),'1y');
  await slider.press('End');
  await page.waitForFunction(()=>window.amplifier.getState().view.navAge==='all');
  if(scenario.touch){
   await slider.press('Escape');
   await page.waitForFunction(()=>!document.querySelector('#workspace-navigation').getClientRects().length);
   assert.equal(await page.getByRole('button',{name:'Home',exact:true}).isVisible(),true);
   assert.equal(await page.getByRole('button',{name:'Home',exact:true}).evaluate(node=>!!node.closest('[inert]')),false);
   await page.getByRole('button',{name:'Home',exact:true}).click();
   await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='home');
  }
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),'chat');
  const beforeDraft=calls.filter(call=>call.action==='session.draft').length;
  // Reopen the mobile drawer after Home, then use the same keyboard action.
  if(scenario.touch)await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  const newChat=page.locator('.a-sidebar-new');
  const drafted=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='session.draft');
  await newChat.focus();await newChat.press('Enter');await drafted;
  assert.equal(calls.filter(call=>call.action==='session.draft').length,beforeDraft+1,'New chat dispatches exactly once');
  await page.close();
 }
 assert.deepEqual(errors,[]);
 assert.ok(calls.every(call=>!['session.select','conversation.send','session.history','session.create'].includes(call.action)),'Passive controls never execute or reload history');
 console.log(JSON.stringify({scope:'synthetic client browser',baseline,scenarios:results,errors},null,2));
}finally{
 await writeFile(`${artifacts}/metrics.json`,JSON.stringify({baseline,results,errors},null,2)+'\n');
 await browser?.close();await vite?.close();
}
