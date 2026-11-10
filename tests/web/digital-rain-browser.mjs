// Production host and renderer; synthetic messages, isolated data, no model calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Digital Rain fixture'});
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-digital-rain';
let browser,page,failure,phase='fixture';
const spacingMeasurements=[];
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}
  }catch{}});
 });
 await mkdir(out,{recursive:true});
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1600,height:1000},colorScheme:'light',
  reducedMotion:'no-preference',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const presentation=patch=>page.evaluate(async patch=>{
  const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,
   expectedRevision:state.revision,composition:{...state.effectiveComposition,
    presentation:{...state.effectiveComposition.presentation,...patch}}});
  const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,
   expectedRevision:state.revision,changeId:prepared.result.id});
  if(!applied.accepted)throw Error('Presentation was not applied');
 },patch);
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});
 await action('session.create');
 const input=page.getByRole('textbox',{name:'Message Amplifier'});
 await input.fill('A synthetic long message for appearance checks. '.repeat(90));
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.getByText('Synthetic first response',{exact:true}).waitFor();
 await input.fill('Retain this unsent draft.');
 const selected=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 // Reuse the fixture's registered folder and pin the selected chat so the real
 // sidebar includes both row kinds and the leading reorder grip.
 const workspacePath=await page.evaluate(()=>window.amplifier.getState().settings.workspace);
 const workspace=(await action('workspace.list')).result.items.find(row=>row.path===workspacePath);
 assert.ok(workspace,'Empty-host fixture registers its existing workspace');
 await action('session.pin',{id:selected,pinned:true});
 await action('workspace.pin',{id:workspace.id,pinned:true});
 const theme=(await action('theme.read',{id:'builtin:digital-rain'})).result;
 assert.equal(theme.name,'Digital Rain');
 const preset=(await action('theme.read',{id:'builtin:graphite'})).result;
 await action('theme.apply',{name:preset.name,css:preset.css});
 const geometry=()=>page.locator('[data-part="conversation"]').evaluate(el=>{
  const rail=el.querySelector('.a-conversation-rail'),messages=el.querySelector('[data-part="messages"]');
  const bounds=el.getBoundingClientRect(),box=messages.getBoundingClientRect();
  return {railPosition:rail&&getComputedStyle(rail).position,
   railTop:rail?rail.getBoundingClientRect().top-bounds.top:null,
   messagesBottom:box.bottom,composerTop:el.querySelector('form[data-part="composer"]').getBoundingClientRect().top};
 });
 const baseline=await geometry();
 const nav=page.locator('#amp-one [data-part="navigation"]'),rail=nav.locator(':scope > .a-nav-rail');
 const toggle=page.locator('#amp-one [data-part="header"] #navigation-toggle');
 const resize=async(width,height)=>{
  phase=`responsive layout ${width}`;
  await page.setViewportSize({width,height});
  // Observe both responsive consumers before opening: WorkspaceLayout publishes
  // data-narrow; WorkspaceRail changes its role at the same 900px breakpoint.
  await expect(page.locator('#amp-one [data-part="workspace"][data-pane-layout]'))
   .toHaveAttribute('data-narrow',String(width<=900));
  await expect.poll(()=>nav.getAttribute('role')).toBe(width<=900?'dialog':null);
 };
 const navigation=async native=>{
  phase=`${native?'Digital Rain':'Graphite'} navigation setup ${page.viewportSize().width}`;
  const draft=await page.evaluate(()=>window.amplifier.getState().view.draft);
  assert.equal((await action('view.update',{patch:{navExpanded:false,navPinned:false,navMode:'browse',navSectionsCollapsed:[]}})).accepted,true);
  await expect.poll(()=>page.evaluate(()=>{
   const {navExpanded,navPinned}=window.amplifier.getState().view;return {navExpanded,navPinned};
  })).toEqual({navExpanded:false,navPinned:false});
  await expect(nav).toHaveAttribute('hidden','');await expect(nav).toHaveAttribute('inert','');
  await expect(rail).toBeHidden();
  await expect(toggle).toBeVisible();await expect(toggle).toBeEnabled();
  await expect(toggle).toHaveAccessibleName('Open navigation');
  await expect(toggle).toHaveAttribute('aria-expanded','false');
  await expect(toggle).toHaveAttribute('aria-controls','workspace-navigation');
  // Use the real header control once, not a direct open that can precede the
  // resize commit. A failed open remains a failure, with the state logged below.
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded','true');
  await expect(nav).not.toHaveAttribute('hidden','');await expect(nav).not.toHaveAttribute('inert','');
  // The unpinned outer slot can have zero width while its absolute rail is open.
  await expect(rail).toBeVisible();
  // The desktop header also pins. Unpin without reopening so these measurements
  // retain the original floating-rail scenario on every width.
  if(page.viewportSize().width>900)
   assert.equal((await action('view.update',{patch:{navPinned:false}})).accepted,true);
  await expect(nav).toHaveAttribute('data-docked','false');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.navPinned)).toBe(false);
  await expect(toggle).toHaveAttribute('aria-expanded','true');await expect(rail).toBeVisible();
  phase=`${native?'Digital Rain':'Graphite'} navigation assertions ${page.viewportSize().width}`;
  const pinned=rail.locator('[data-sidebar-section="pinned"]');
  const chat=pinned.locator(`.a-nav-chat[data-session-id="${selected}"]`);
  const folder=pinned.locator('.a-workspace-row');
  await expect(chat).toBeVisible();await expect(folder).toHaveCount(1);await expect(folder).toBeVisible();
  await expect(chat.locator('.a-nav-chat-select')).toHaveAttribute('aria-current','page');
  for(const row of [chat,folder])await expect(row.locator(':scope > .a-reorder-grip')).toBeVisible();
  const rows=await pinned.locator('.a-nav-chat:not(.a-nav-chat-rename),.a-workspace-row').evaluateAll(nodes=>
   nodes.map(el=>{
    const s=getComputedStyle(el),r=el.getBoundingClientRect(),kind=el.classList.contains('a-workspace-row')?'workspace':'chat';
    const select=el.querySelector(':scope > .a-nav-chat-select,:scope > .a-workspace-select');
    const icon=select.querySelector('.a-navigation-status,:scope > svg'),label=select.querySelector('.a-nav-chat-label,.a-workspace-label');
    const grip=el.querySelector(':scope > .a-reorder-grip'),more=el.querySelector(':scope > .a-navigation-more');
    return {kind,width:r.width,start:s.paddingInlineStart,end:s.paddingInlineEnd,sizing:s.boxSizing,shadow:s.boxShadow,
     selected:el.classList.contains('is-selected'),
     layout:{height:r.height,minHeight:s.minHeight,maxHeight:s.maxHeight,blockStart:s.paddingBlockStart,blockEnd:s.paddingBlockEnd,
      selectHeight:select.getBoundingClientRect().height,moreHeight:more.getBoundingClientRect().height,
      gripHeight:grip?.getBoundingClientRect().height??null},
     iconWidth:icon.getBoundingClientRect().width,labelWidth:label.getBoundingClientRect().width,
     insets:{icon:icon.getBoundingClientRect().left-r.left,label:label.getBoundingClientRect().left-r.left,
      grip:grip?grip.getBoundingClientRect().left-r.left:null},labelEnd:r.right-label.getBoundingClientRect().right};
   }));
  assert.ok(rows.some(row=>row.kind==='chat'&&row.selected),'Selected chat is measured in mounted navigation');
  assert.ok(rows.some(row=>row.kind==='workspace'),'Workspace is measured in mounted navigation');
  if(native)for(const row of rows){
   assert.ok(row.width>0,'Measure actual rendered rows');
   assert.ok(row.iconWidth>0&&row.labelWidth>0,'Measure visible icon and title contents');
   assert.equal(row.sizing,'border-box');assert.equal(row.start,'8px');assert.equal(row.end,'4px');
   const original=native.rows.find(before=>before.kind===row.kind);
   assert.ok(original,'Native fixture has the same row/control kind');
   assert.deepEqual(row.layout,original.layout,'Horizontal inset preserves native row and control heights');
   if(row.selected)assert.ok(row.shadow.includes('3px 0px 0px 0px inset'),'Selected row retains its 3px accent stripe');
   for(const [part,inset] of Object.entries(row.insets))if(inset!==null)
    assert.ok(inset-3>=4.5,`${row.kind} ${part} clears the 3px stripe by at least 4.5px`);
   assert.ok(row.labelEnd>=3.5,'Label stays within the end padding');
  }
  // Tab from the real grip follows the native select/details controls; Enter
  // opens the real flyout. Do not fabricate focus or rename state with classes.
  const grip=chat.locator(':scope > .a-reorder-grip'),select=chat.locator('.a-nav-chat-select');
  const more=chat.getByRole('button',{name:/^Details and actions for /});
  await grip.focus();await page.keyboard.press('Tab');await expect(select).toBeFocused();
  if(native){
   assert.ok(await select.evaluate(el=>el.matches(':focus-visible')),'Keyboard selection remains focus-visible');
   await expect(select).toHaveCSS('outline-style','solid');await expect(select).toHaveCSS('outline-width','2px');
  }
  await page.keyboard.press('Tab');await expect(more).toBeFocused();await page.keyboard.press('Enter');
  const details=nav.locator('.a-navigation-flyout');await expect(details).toBeVisible();
  await details.getByRole('button',{name:/^Rename /}).click();
  const editor=details.locator('.a-nav-chat-rename'),name=editor.getByRole('textbox',{name:/^New name for /});
  await expect(name).toBeVisible();await expect(name).toBeFocused();
  const rename=await editor.evaluate(el=>{
   const s=getComputedStyle(el);return {start:s.paddingInlineStart,end:s.paddingInlineEnd,wrap:s.flexWrap,maxHeight:s.maxHeight};
  });
  if(native)assert.deepEqual(rename,native.rename,'Rename form is excluded from capsule padding and fixed row height');
  const title=await name.inputValue();await name.fill('Synthetic unsaved rename');
  await expect(editor.getByRole('button',{name:'Save conversation name',exact:true})).toBeEnabled();
  await editor.getByRole('button',{name:'Cancel conversation rename',exact:true}).click();
  await page.keyboard.press('Escape');await expect(details).toHaveCount(0);await expect(more).toBeFocused();
  await expect(chat.locator('.a-nav-chat-select')).toHaveAccessibleName(title);
  await expect(folder.locator('.a-workspace-select')).toBeEnabled();
  await expect(folder.locator('.a-workspace-new-chat')).toBeEnabled();
  if(native){
   const width=page.viewportSize().width,scheme=await page.locator('#amp-one').getAttribute('data-theme-scheme');
   spacingMeasurements.push({width,scheme,rows,rename,native});
   await writeFile(`${out}/navigation-spacing.json`,JSON.stringify(spacingMeasurements,null,2));
   if(width!==760)await page.screenshot({path:`${out}/digital-rain-navigation-${scheme}-${width}.png`});
  }
  assert.equal((await action('view.update',{patch:{navExpanded:false}})).accepted,true);
  await expect(toggle).toHaveAttribute('aria-expanded','false');
  await expect(nav).toHaveAttribute('hidden','');await expect(nav).toHaveAttribute('inert','');
  await expect(rail).toBeHidden();
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
  await expect(input).toHaveDraft(draft);
  return {rows,rename};
 };
 const nativeNavigation={};
 for(const width of [1600,760,390]){
  await resize(width,width===1600?1000:900);
  nativeNavigation[width]=await navigation();
 }
 await resize(1600,1000);
 phase='Digital Rain conversation appearance';
 await action('theme.apply',{name:theme.name,css:theme.css});
 for(const scheme of ['light','dark','system']){
  phase=`Digital Rain ${scheme} conversation appearance`;
  await presentation({scheme});
  const expected=scheme==='system'?'light':scheme;
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',expected);
  await expect.poll(()=>page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-ink').trim()))
   .toBe(expected==='light'?'#143522':'#e0f4e5');
  const styles=await page.locator('[data-part="conversation"]').evaluate(el=>{
   const style=node=>getComputedStyle(node),rain=getComputedStyle(el,'::before');
   return {images:rain.backgroundImage.match(/data:image\/png/g)?.length,animation:rain.animationName,
    form:style(el.querySelector('form[data-part="composer"]')).backgroundColor,
    configuration:style(el.querySelector('.a-compose-configuration')).backgroundColor,
    user:style(el.querySelector('.a-message.a-user')).backgroundColor,
    input:style(el.querySelector('[data-part="composer-surface"]')).backgroundColor,
    font:style(el.querySelector('.a-message.a-user p')).fontFamily};
  });
  assert.equal(styles.images,3);assert.equal(styles.animation,'digital-rain-drift');
  for(const key of ['form','configuration','user'])assert.equal(styles[key],'rgba(0, 0, 0, 0)',key);
  assert.notEqual(styles.input,'rgba(0, 0, 0, 0)');
  const assistant=await page.locator('.a-message.a-assistant:has(> .a-message-actions)').first().evaluate(el=>{
   const body=el.querySelector(':scope > .a-message-body'),actions=el.querySelector(':scope > .a-message-actions');
   const outer=getComputedStyle(el),inner=getComputedStyle(body),footer=getComputedStyle(actions);
   return {outer:outer.backgroundColor,border:outer.borderTopWidth,padding:outer.paddingTop,
    body:inner.backgroundColor,bodyBorder:inner.borderTopWidth,footer:footer.backgroundColor,
    bodyBottom:body.getBoundingClientRect().bottom,actionsTop:actions.getBoundingClientRect().top};
  });
  assert.equal(assistant.outer,'rgba(0, 0, 0, 0)');
  assert.equal(assistant.border,'0px');assert.equal(assistant.padding,'0px');
  assert.notEqual(assistant.body,'rgba(0, 0, 0, 0)');assert.equal(assistant.bodyBorder,'1px');
  assert.equal(assistant.footer,'rgba(0, 0, 0, 0)');
  assert.ok(assistant.actionsTop>=assistant.bodyBottom,'Assistant actions sit beneath the boxed body');
  assert.doesNotMatch(theme.css,/(?:-webkit-)?mask-image\s*:/,'Rain inherits native transcript edges');
  assert.match(styles.font,/mono|Menlo|Consolas/i);
  const after=await geometry();assert.equal(after.railPosition,'absolute');
  assert.ok(Math.abs(after.railTop-baseline.railTop)<16,'Navigator retains native top anchoring');
  assert.ok(Math.abs((after.composerTop-after.messagesBottom)-(baseline.composerTop-baseline.messagesBottom))<24,
   'Theme does not reserve an extra flex child beneath the transcript');
  const before=await page.locator('[data-part="conversation"]').evaluate(el=>getComputedStyle(el,'::before').backgroundPosition);
  await page.waitForFunction(before=>{
   const el=document.querySelector('[data-part="conversation"]');
   return getComputedStyle(el,'::before').backgroundPosition!==before;
  },before);
  await page.screenshot({path:`${out}/digital-rain-${scheme}.png`});
  if(scheme!=='system')await navigation(nativeNavigation[1600]);
 }
 phase='Digital Rain media modes';
 await page.emulateMedia({colorScheme:'dark'});
 await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','dark');
 await page.emulateMedia({reducedMotion:'reduce'});
 await expect.poll(()=>page.locator('[data-part="conversation"]').evaluate(el=>getComputedStyle(el,'::before').animationName)).toBe('none');
 await presentation({decorations:false});
 await expect.poll(()=>page.locator('[data-part="conversation"]').evaluate(el=>getComputedStyle(el,'::before').content)).toBe('none');
 await presentation({decorations:true});
 await page.emulateMedia({forcedColors:'active'});
 await expect.poll(()=>page.locator('[data-part="conversation"]').evaluate(el=>getComputedStyle(el,'::before').content)).toBe('none');
 await page.emulateMedia({forcedColors:'none',reducedMotion:'no-preference'});
 await input.fill('Long retained draft. '.repeat(100));
 for(const width of [760,390]){
  await resize(width,900);
  phase=`Digital Rain narrow conversation ${width}`;
  await expect(input).toBeVisible();await expect(page.locator('[data-part="composer-primary-action"]')).toBeInViewport();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const bubble=await page.locator('.a-message.a-assistant:has(> .a-message-actions)').first().evaluate(el=>{
   const body=el.querySelector(':scope > .a-message-body'),actions=el.querySelector(':scope > .a-message-actions');
   return {padding:getComputedStyle(body).paddingTop,bodyBottom:body.getBoundingClientRect().bottom,
    actionsTop:actions.getBoundingClientRect().top};
  });
  assert.equal(bubble.padding,'12px','Narrow assistant padding belongs to the body');
  assert.ok(bubble.actionsTop>=bubble.bodyBottom,'Narrow assistant actions remain below the body');
  for(const scheme of ['light','dark']){
   await presentation({scheme});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
   await navigation(nativeNavigation[width]);
  }
 }
 phase='Digital Rain switch-back and reload';
 await action('theme.apply',{name:preset.name,css:preset.css});
 assert.equal(await page.locator('[data-part="conversation"]').evaluate(el=>getComputedStyle(el,'::before').animationName),'none');
 await action('theme.apply',{name:theme.name,css:theme.css});
 await page.reload();await showFixtureConversation(page,{navPinned:false});
 await expect(input).toHaveDraft('Long retained draft. '.repeat(100));
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
 assert.deepEqual(errors,[]);
 console.log('Digital Rain passed: light/dark/system, three animated layers, native layout, transparent wrappers, fade, motion/decorations/forced-colors, narrow layouts, sidebar 8/4px insets and grip/icon/title clearance, native row/control heights, keyboard focus and rename exclusion, switch-back and reload with draft.');
}catch(error){
 failure=error;
 // One bounded stderr record; do not add screenshots or unbounded state dumps.
 // Keep the original assertion authoritative if diagnostic collection fails.
 if(page)try{
  const state=await page.evaluate(()=>{
   const view=window.amplifier?.getState()?.view||{};
   const describe=selector=>{
    const el=document.querySelector(selector);if(!el)return null;
    const style=getComputedStyle(el),rect=el.getBoundingClientRect();
    return {role:el.getAttribute('role'),hidden:el.hidden,inert:el.inert,
     blocked:!!el.closest('[hidden],[inert]'),label:el.getAttribute('aria-label'),
     expanded:el.getAttribute('aria-expanded'),controls:el.getAttribute('aria-controls'),
     display:style.display,visibility:style.visibility,width:rect.width,height:rect.height};
   };
   return {width:innerWidth,height:innerHeight,narrowMedia:matchMedia('(max-width: 900px)').matches,
    narrowLayout:document.querySelector('#amp-one [data-part="workspace"][data-pane-layout]')?.getAttribute('data-narrow'),
    scheme:document.querySelector('#amp-one')?.getAttribute('data-theme-scheme'),
    view:{navExpanded:view.navExpanded,navPinned:view.navPinned,navMode:view.navMode,
     canvasFocused:!!view.canvasFocused,panelOpen:!!view.panel,toolbarMenuOpen:!!view.toolbarMenuOpen},
    toggle:describe('#amp-one [data-part="header"] #navigation-toggle'),
    navigation:describe('#amp-one [data-part="navigation"]'),
    rail:describe('#amp-one [data-part="navigation"] > .a-nav-rail')};
  });
  console.error('Digital Rain failure state: '+JSON.stringify({phase,viewport:page.viewportSize(),...state}));
 }catch(diagnosticError){console.error('Digital Rain failure state unavailable: '+String(diagnosticError.message).slice(0,300))}
 throw error;
}
finally{await finishFixture({label:'Digital Rain fixture',close:[['Digital Rain browser',()=>browser?.close()]],stop,originalError:failure})}