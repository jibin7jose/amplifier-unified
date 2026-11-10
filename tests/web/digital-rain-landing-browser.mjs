// Built production client/host; private synthetic fixture, no real provider or user data.
// Run after building in the isolated check environment, never against a live host.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const out=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(out,'Set AMPLIFIER_TEST_ARTIFACTS to the owned evidence directory');
await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 [join(root,'tests/fixtures/empty_host_ui_server.py'),'--chat-controls'],
 {stdio:['ignore','pipe','pipe']});
const stop=ownFixture(fixture,{label:'Digital Rain landing fixture'});
let browser,page,failure,stderr='';
fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-12000)});
const report={runtime:'synthetic',liveProvider:false,cases:[],screenshots:[],errors:[]};
try{
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout: '+stderr)),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr))});
  fixture.stdout.on('data',chunk=>{
   text+=chunk;const lines=text.split('\n');text=lines.pop();
   for(const line of lines)try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}
  });
 });
 browser=await chromium.launch({headless:true,args:JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]'),
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 page=await browser.newPage({viewport:{width:1600,height:1000},colorScheme:'light',
  reducedMotion:'no-preference',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(15000);
 page.on('pageerror',error=>report.errors.push(error.message));
 const action=async(name,args={})=>{
  const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  assert.equal(result.accepted,true,`${name}: ${result.error||'not accepted'}`);
  return result;
 };
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const presentation=patch=>page.evaluate(async patch=>{
  const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,
   expectedRevision:state.revision,composition:{...state.effectiveComposition,
    presentation:{...state.effectiveComposition.presentation,...patch}}});
  if(!prepared.accepted)throw Error('Presentation preparation refused');
  const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,
   expectedRevision:state.revision,changeId:prepared.result.id});
  if(!applied.accepted)throw Error('Presentation application refused');
 },patch);
 await page.goto(url);
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 await expect(page.locator('.a-home-filters')).toBeVisible();
 const projects=[],chats=[],fixtureHome=dirname((await state()).workspaceDefaults.root);
 for(const name of ['Synthetic launch','Synthetic research']){
  const path=join(fixtureHome,name.toLowerCase().replaceAll(' ','-'));
  await mkdir(path);
  await action('workspace.add',{path,name});
  const project=(await action('workspace.list',{query:name})).result.items.find(row=>row.path===path);
  assert.ok(project,'Synthetic workspace registered');projects.push(project);
  for(const title of ['Planning notes','Review checklist']){
   const created=await action('session.create',{workspace:path,title:name+' — '+title,select:false});
   chats.push(created.sessionId||created.result.sessionId);
  }
 }
 const theme=(await action('theme.read',{id:'builtin:digital-rain'})).result;
 const graphite=(await action('theme.read',{id:'builtin:graphite'})).result;
 assert.equal(theme.name,'Digital Rain');
 const expectedImages=theme.css.match(/data:image\/png;base64,[A-Za-z0-9+/=]+/g)||[];
 assert.equal(expectedImages.length,3,'The appearance embeds exactly three shared PNGs');
 report.themeSha256=createHash('sha256').update(theme.css).digest('hex');
 report.build=JSON.parse(await readFile(join(root,'amplifier_web/static/build.json'),'utf8'));
 const apply=async appearance=>{
  await action('theme.apply',{name:appearance.name,css:appearance.css});
  await page.waitForFunction(css=>[...document.querySelectorAll('#amp-one style')]
   .some(node=>node.textContent===css),appearance.css);
 };
 const pane=page.locator('[data-part="work-browser"]:visible');
 const surfaces=['home','chats','workspaces','workspace'];
 const modes=['focus','brief','studio'];
 const enter=async(surface,mode)=>{
  await action('view.update',{patch:{workSurface:surface,workWorkspaceId:projects[0].id,
   workWorkspaceTab:'chats',workHomeScope:'all',workHomeFilter:'all',workHomeQuery:'',
   navPinned:false,navExpanded:false}});
  await expect(pane).toHaveAttribute('data-landing',surface);
  await pane.getByRole('group',{name:'Work view',exact:true})
   .getByRole('button',{name:mode[0].toUpperCase()+mode.slice(1),exact:true}).click();
  await expect(pane).toHaveAttribute('data-view',mode);
  await expect.poll(()=>pane.locator('.a-work-card,.a-landing-chat,.a-landing-workspace').count()).toBeGreaterThan(0);
  await expect(pane.locator('[aria-busy="true"]')).toHaveCount(0);
 };
 // Compare layout inputs, not text-dependent heights. Font wrapping may change;
 // never "repair" native layout by adding height, padding or scroll compensation.
 const geometry=()=>pane.evaluate(el=>{
  const selectors=[':scope','.a-home-content','.a-home-toolbar','.a-home-intro',
   '.a-work-view-switch','.a-home-query','.a-home-filter-bar','.a-work-group-items',
   '.a-work-card','.a-landing-collection','.a-landing-items','.a-landing-chat','.a-landing-workspace'];
  const properties=['display','position','flexGrow','flexShrink','flexBasis','boxSizing',
   'overflowX','overflowY','minHeight','minWidth','maxWidth','paddingTop','paddingRight',
   'paddingBottom','paddingLeft','marginTop','marginRight','marginBottom','marginLeft',
   'rowGap','columnGap','alignItems','justifyContent','containerType'];
  const styles={};
  for(const selector of selectors){
   const node=selector===':scope'?el:el.querySelector(selector);
   if(node){const s=getComputedStyle(node);styles[selector]=Object.fromEntries(properties.map(key=>[key,s[key]]))}
  }
  const rect=el.getBoundingClientRect();
  return {styles,x:rect.x,width:rect.width,top:rect.top,height:rect.height};
 });
 const rain=()=>pane.evaluate(el=>{
  const s=getComputedStyle(el);
  return {images:(s.backgroundImage.match(/data:image\/png/g)||[]).length,size:s.backgroundSize,
   animation:s.animationName,duration:s.animationDuration,timing:s.animationTimingFunction,
   iterations:s.animationIterationCount,image:s.backgroundImage,
   before:getComputedStyle(el,'::before').content,after:getComputedStyle(el,'::after').content};
 });
 const checkRain=async(animated=true)=>{
  const value=await rain();
  assert.equal(value.images,3,'Three PNG background layers on the actual scroller');
  for(const image of expectedImages)assert.ok(value.image.includes(image),'Landing reuses the shared PNGs');
  assert.equal(value.size,'512px 768px, 704px 960px, 896px 1152px');
  assert.equal(value.animation,animated?'digital-rain-drift':'none');
  if(animated){
   assert.equal(value.duration,'72s');assert.equal(value.timing,'linear');assert.equal(value.iterations,'infinite');
  }
  assert.ok(['none','normal'].includes(value.before),'No positioned landing overlay');
  assert.ok(['none','normal'].includes(value.after),'No extra landing layout child');
  return value;
 };
 const solidAndMono=()=>pane.evaluate(el=>{
  const visible=node=>!!node.getClientRects().length;
  const selectors=['.a-home-intro','.a-home-toolbar','.a-home-query','.a-home-filter-bar',
   '.a-home-filters','.a-work-group-heading','.a-home-recent','.a-work-card',
   '.a-landing-chat','.a-landing-workspace','.a-focus-caption','.a-work-empty',
   '.a-workspace-modes','.a-workspace-location','.a-workspace-explorer>.a-field',
   '.a-work-tabs','.a-nav-search','.a-search-tools','.a-search-result-summary',
   '.a-search-empty','.a-sidebar-filters','.a-caption','.a-home-content>p','.a-home-welcome'];
  const titles='.a-nav-chat-title,.a-nav-chat-title>span,'+
   '.a-workspace-label>span[data-scroll-title],.a-workspace-label>span[data-scroll-title]>span';
  return {surface:getComputedStyle(document.querySelector('#amp-one')).getPropertyValue('--a-surface').trim(),
   tint:getComputedStyle(document.querySelector('#amp-one')).getPropertyValue('--a-tint').trim(),
   soft:getComputedStyle(document.querySelector('#amp-one')).getPropertyValue('--a-soft').trim(),
   blocks:selectors.flatMap(selector=>[...el.querySelectorAll(selector)].filter(visible).map(node=>({
    selector,background:getComputedStyle(node).backgroundColor,image:getComputedStyle(node).backgroundImage}))),
   fonts:[...el.querySelectorAll('h1,h2,h3')].filter(visible).map(node=>getComputedStyle(node).fontFamily),
   titleFonts:[...el.querySelectorAll(titles)].filter(visible).map(node=>({
    selector:node.className||node.parentElement.className,font:getComputedStyle(node).fontFamily}))};
 });
 const rgb=hex=>{
  assert.match(hex,/^#[0-9a-f]{6}$/i,'Expected opaque six-digit palette token');
  return `rgb(${[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(', ')})`;
 };
 const checkReading=async()=>{
  const reading=await solidAndMono(),palette=[rgb(reading.surface),rgb(reading.tint)];
  assert.ok(reading.fonts.length>0,'Real landing headings rendered');
  for(const font of reading.fonts)assert.match(font,/mono|Menlo|Consolas/i);
  for(const title of reading.titleFonts)assert.match(title.font,/mono|Menlo|Consolas/i,
   `${title.selector} actual title span is monospace, including Focus layout`);
  for(const block of reading.blocks){
   // Native search chrome uses the theme's opaque inset surface. Preserve that
   // distinction instead of overriding production CSS to fit a surface-only oracle.
   const allowed=block.selector==='.a-nav-search'?[...palette,rgb(reading.soft)]:palette;
   assert.ok(allowed.includes(block.background),`${block.selector} must have a solid palette background: ${block.background}`);
   assert.equal(block.image,'none',`${block.selector} keeps rain behind its reading surface`);
  }
  report.readingSelectors=[...new Set([...(report.readingSelectors||[]),...reading.blocks.map(block=>block.selector)])];
  return reading;
 };
 const keyboardFocus=async()=>{
  await pane.getByRole('group',{name:'Work view',exact:true}).getByRole('button').first().focus();
  await page.keyboard.press('Tab');
  const focused=page.locator(':focus');
  await expect(focused).toBeVisible();await expect(focused).toBeInViewport();
  assert.equal(await focused.evaluate(el=>el.matches(':focus-visible')),true,'Keyboard focus-visible is active');
  const outline=await focused.evaluate(el=>{
   const s=getComputedStyle(el);return {style:s.outlineStyle,width:parseFloat(s.outlineWidth),color:s.outlineColor};
  });
  assert.notEqual(outline.style,'none');assert.ok(outline.width>=1,'Keyboard focus has a visible outline');
  assert.notEqual(outline.color,'rgba(0, 0, 0, 0)');
 };
 const emptyHome=async scheme=>{
  const search=pane.getByRole('searchbox',{name:'Search your work',exact:true});
  await search.fill('synthetic-no-match-7f39');await search.press('Tab');
  await expect(pane.locator('.a-home-welcome')).toContainText('No matching work.');
  const reading=await checkReading();
  assert.ok(reading.blocks.some(block=>block.selector==='.a-home-welcome'),'Actual empty reading surface checked');
  await checkRain();await keyboardFocus();
  const before=await motionPosition();await expect.poll(motionPosition).not.toBe(before);
  await page.emulateMedia({reducedMotion:'reduce'});
  await checkRain(false);await checkReading();await keyboardFocus();
  await page.emulateMedia({reducedMotion:'no-preference'});
  await search.fill('');await search.press('Tab');
  await expect.poll(()=>pane.locator('.a-work-card').count()).toBeGreaterThan(0);
  (report.emptySearch??=[]).push({scheme,surface:'home',animated:true,reducedMotion:true,keyboardFocus:true});
 };
 const otherWorkspaceTabs=async scheme=>{
  await enter('workspace','brief');
  for(const tab of ['work','files','details']){
   await action('view.update',{patch:{workWorkspaceTab:tab}});
   await expect(pane.getByRole('group',{name:'Workspace view',exact:true})
    .getByRole('button',{name:tab[0].toUpperCase()+tab.slice(1),exact:true}))
    .toHaveAttribute('aria-pressed','true');
   const value=await rain();
   assert.equal(value.images,0,`${tab} keeps its existing non-rain workspace surface`);
   assert.notEqual(value.animation,'digital-rain-drift');
  }
  await action('view.update',{patch:{workWorkspaceTab:'chats'}});
  await checkRain();
  (report.unaffectedTabs??=[]).push({scheme,tabs:['work','files','details']});
 };
 const motionPosition=()=>pane.evaluate(el=>getComputedStyle(el).backgroundPosition);
 const mediaChecks=async()=>{
  await keyboardFocus();
  const before=await motionPosition();
  await expect.poll(motionPosition).not.toBe(before);
  await page.emulateMedia({reducedMotion:'reduce'});
  await expect.poll(async()=>(await rain()).animation).toBe('none');
  await checkRain(false);
  assert.equal(await pane.evaluate(async el=>{
   const before=getComputedStyle(el).backgroundPosition;
   await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   return getComputedStyle(el).backgroundPosition===before;
  }),true,'Reduced-motion rain stays static across rendered frames');
  await keyboardFocus();
  await presentation({decorations:false});
  await expect(page.locator('#amp-one')).toHaveAttribute('data-decorations','off');
  await expect.poll(async()=>(await rain()).image).toBe('none');
  assert.equal((await rain()).animation,'none');
  await presentation({decorations:true});
  await page.emulateMedia({forcedColors:'active',reducedMotion:'no-preference'});
  await expect.poll(async()=>(await rain()).image).toBe('none');
  assert.equal((await rain()).animation,'none');
  await page.emulateMedia({forcedColors:'none'});
  await expect.poll(async()=>(await rain()).animation).toBe('digital-rain-drift');
 };
 for(const scheme of ['light','dark']){
  await presentation({scheme,decorations:true});
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
  for(const width of [1600,760,390]){
   await page.setViewportSize({width,height:1000});
   for(const surface of surfaces)for(const mode of modes){
    report.current={scheme,width,surface,mode};
    await enter(surface,mode);await apply(graphite);
    const baseline=await geometry();
    await apply(theme);
    const current=await geometry();
    assert.deepEqual(current.styles,baseline.styles,JSON.stringify(report.current)+' native container styles');
    assert.ok(Math.abs(current.x-baseline.x)<=2&&Math.abs(current.width-baseline.width)<=2,
     'Native horizontal container geometry (font-dependent heights are not compared)');
    await checkRain();
    const reading=await checkReading();
    if(surface!=='home')assert.ok(reading.titleFonts.length>0,'Actual chat/workspace title spans rendered');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page-level horizontal overflow');
    assert.ok(await pane.evaluate(el=>el.scrollWidth<=el.clientWidth+1),'No landing scroller horizontal overflow');
    await expect(pane.getByRole('group',{name:'Work view',exact:true})).toBeInViewport();
    if(mode==='brief'&&['home','workspace'].includes(surface)){
     const file=`digital-rain-${surface}-${scheme}-${width}.png`;
     await page.screenshot({path:join(out,file)});report.screenshots.push(file);
    }
    if(width===1600&&mode==='brief'){
     await mediaChecks();
     if(surface==='home')await emptyHome(scheme);
     if(surface==='workspace')await otherWorkspaceTabs(scheme);
    }
    await apply(graphite);
    assert.equal((await rain()).images,0,'Switch-back removes all Digital Rain PNGs');
    assert.notEqual((await rain()).animation,'digital-rain-drift','Switch-back removes rain animation');
    assert.deepEqual((await geometry()).styles,baseline.styles,'Graphite restores native container styles');
    report.cases.push({...report.current,baseline,current});
   }
  }
 }
 // A small chat regression check complements the dedicated digital-rain-browser.
 await page.setViewportSize({width:1600,height:1000});
 await action('session.select',{id:chats[0]});
 await showFixtureConversation(page,{navPinned:false});
 const input=page.getByRole('textbox',{name:'Message Amplifier'});
 await input.fill('Synthetic landing appearance regression check.');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.getByText('Synthetic first response',{exact:true}).waitFor();
 const chatGeometry=()=>page.locator('[data-part="conversation"]').evaluate(el=>{
  const rail=el.querySelector('.a-conversation-rail'),bounds=el.getBoundingClientRect();
  const messages=el.querySelector('[data-part="messages"]').getBoundingClientRect();
  const composer=el.querySelector('form[data-part="composer"]').getBoundingClientRect();
  return {railPosition:rail&&getComputedStyle(rail).position,
   railTop:rail?rail.getBoundingClientRect().top-bounds.top:null,gap:composer.top-messages.bottom};
 });
 const chatBaseline=await chatGeometry();
 await apply(theme);
 const chatRain=await page.locator('[data-part="conversation"]').evaluate(el=>{
  const s=getComputedStyle(el,'::before');
  return {image:s.backgroundImage,size:s.backgroundSize,animation:s.animationName,
   position:s.position,pointerEvents:s.pointerEvents};
 });
 assert.equal((chatRain.image.match(/data:image\/png/g)||[]).length,3);
 assert.equal(chatRain.animation,'digital-rain-drift');
 assert.equal(chatRain.position,'absolute');assert.equal(chatRain.pointerEvents,'none');
 const art=await page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--digital-rain-art'));
 const urls=art.match(/data:image\/png;base64,[A-Za-z0-9+/=]+/g)||[];
 assert.equal(urls.length,3);
 for(const image of urls)assert.ok(chatRain.image.includes(image),'Chat still uses the shared rain layers');
 assert.equal(await page.evaluate(async urls=>{
  await Promise.all(urls.map(src=>new Promise((resolve,reject)=>{
   const image=new Image();image.onload=()=>image.naturalWidth>0?resolve():reject(Error('Empty PNG'));
   image.onerror=()=>reject(Error('Undecodable rain PNG'));image.src=src;
  })));return true;
 },urls),true);
 const chatCurrent=await chatGeometry();
 assert.equal(chatCurrent.railPosition,chatBaseline.railPosition);
 if(chatBaseline.railTop!==null)assert.ok(Math.abs(chatCurrent.railTop-chatBaseline.railTop)<16);
 assert.ok(Math.abs(chatCurrent.gap-chatBaseline.gap)<24,'No extra transcript flex child');
 await expect(input).toBeVisible();
 await apply(graphite);
 assert.notEqual(await page.locator('[data-part="conversation"]').evaluate(el=>getComputedStyle(el,'::before').animationName),'digital-rain-drift');
 assert.deepEqual(report.errors,[]);
 report.chat={baseline:chatBaseline,current:chatCurrent,imagesDecoded:3};
 delete report.current;
}catch(error){
 failure=error;report.failure=error.stack||error.message;
 await page?.screenshot({path:join(out,'failure.png')}).catch(()=>{});
}finally{
 try{
  await finishFixture({label:'Digital Rain landing fixture',
   close:[['Digital Rain landing browser',()=>browser?.close()]],stop,originalError:failure});
 }catch(error){failure ||= error;report.cleanupError=error.message}
 report.passed=!failure;
 await writeFile(join(out,'report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({passed:report.passed,cases:report.cases.length,screenshots:report.screenshots,artifacts:out}));
}
if(failure)throw failure;