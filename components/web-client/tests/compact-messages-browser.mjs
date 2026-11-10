import {fixturePython} from './fixture-python.mjs';
import {clearComposer} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
 const url=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const r=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(r.status,200);return r.json()};
 const {alpha}=await control({op:'heavy',active:false,messages:4,otherMessages:2,chars:50,nodes:0});
 const now=Date.now()/1000;
 const messages=[{id:'u0',role:'user',text:'My main concern was about keeping our running installation available. Please make sure a longer user message uses the available bubble width, with even padding on both sides, instead of wrapping in a narrow inner column.',via:'chat',createdAt:now-100},
 {id:'a0',role:'assistant',text:'Earlier response.',via:'chat',createdAt:now-95},
 {id:'u1',role:'user',text:'Ok, do it.',steering:{disposition:'queued'},via:'call',createdAt:now-90},
 {id:'observation',role:'user',text:'External observation: INTERNAL_JSON_MUST_NOT_APPEAR',observation:{source:'amplifier-delegate'},createdAt:now-70},
 {id:'a1',role:'assistant',text:'The work is complete. The compact message layout keeps the conversation easy to read.',via:'chat',createdAt:now-30}];
 const turns=[{id:'t1',anchorMessageId:'u1',startedAt:now-85,endedAt:now-75,phase:'completed'},
 {id:'t2',anchorMessageId:'observation',startedAt:now-65,endedAt:now-55,phase:'completed'},
 {id:'t3',anchorMessageId:'observation',startedAt:now-50,endedAt:now-40,phase:'completed'}];
 const nodes=turns.map((turn,i)=>({id:'step'+i,turnId:turn.id,anchorMessageId:turn.anchorMessageId,kind:'tool',label:'Synthetic step '+i,startedAt:turn.startedAt,endedAt:turn.endedAt,status:'completed',input:'{}',output:'Result '+i}));
 await control({op:'patch',sessions:{[alpha]:{messages,execution:{turns,nodes}}}});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:headers,colorScheme:'light'});
 const page=await context.newPage(),errors=[],details=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('part=nodes'))details.push(r.url())});
 await page.goto(url);const input=page.getByRole('textbox',{name:'Message Amplifier'});await input.waitFor();
 const user=page.locator('[data-message-id="u1"]'),body=user.locator('.a-message-body'),footer=user.locator('.a-message-actions');
 await expect(footer).toHaveCSS('opacity','1');await expect(page.locator('[data-message-id="a1"] .a-message-actions')).toHaveCSS('opacity','1');
 assert.equal(await page.locator('.a-msg-meta').count(),0);assert.equal(await page.getByText(/INTERNAL_JSON/).count(),0);
 const bounds=await body.boundingBox(),actions=await footer.boundingBox();assert.ok(bounds.height<70,JSON.stringify(bounds));assert.ok(actions.y>=bounds.y+bounds.height);
 await expect(footer.getByText('via call',{exact:true})).toBeVisible();
 const group=page.locator('[data-part="execution-group"]');await expect(group).toHaveCount(1);await expect(group.locator(':scope > button')).toContainText('Worked for 30s');
 assert.equal(details.length,0);await group.locator(':scope > button').click();await expect(group.getByText('Synthetic step 2',{exact:true})).toBeVisible();
 assert.equal(await group.locator('.a-execution-turn-line').count(),1);await group.locator(':scope > button').click();
 // Saved full CSS skins used to repaint the outer article after the base stylesheet.
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const presentation=patch=>page.evaluate(async patch=>{const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}});await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id})},patch);
 const {result:graphite}=await action('theme.read',{id:'builtin:graphite'});
 const legacy=await readFile(root+'tests/fixtures/legacy_theme.css','utf8');
 const savedGraphite=legacy+'\n'+graphite.css.slice(graphite.css.indexOf('#amp-one[data-theme-scheme="light"]'));
 const older=page.locator('[data-message-id="u0"]');
 const checkLayout=async()=>{
  await expect(user).toHaveCSS('background-color','rgba(0, 0, 0, 0)');await expect(user).toHaveCSS('padding-top','0px');
  const bubble=await body.boundingBox(),foot=await footer.boundingBox();
  assert.ok(bubble.height<=44,JSON.stringify(bubble));assert.ok(foot.y>=bubble.y+bubble.height);
  assert.ok(bubble.width<foot.width,'Footer metadata must not widen the painted bubble');
  await expect(footer).toHaveCSS('opacity','1');
  await input.hover();await expect(older.locator('.a-message-actions')).toHaveCSS('opacity','0');
  const geometry=await older.evaluate(el=>{const body=el.querySelector('.a-message-body').getBoundingClientRect(),text=el.querySelector('.a-detail-text .a-markdown>p').getBoundingClientRect();return {height:body.height,width:body.width,available:el.getBoundingClientRect().width,left:text.left-body.left,right:body.right-text.right}});
  assert.equal(geometry.left,14);assert.equal(geometry.right,14);assert.ok(geometry.width>=Math.min(geometry.available*.8,740)-1,JSON.stringify(geometry));
  const height=geometry.height;
  await older.hover();await expect(older.locator('.a-message-actions')).toHaveCSS('opacity','1');
  assert.equal(await older.locator('.a-message-body').evaluate(el=>el.getBoundingClientRect().height),height);
  const styles=await group.evaluate(el=>{const style=getComputedStyle(el),label=getComputedStyle(el.querySelector('.a-execution-label'));const probe=document.createElement('span');probe.style.color='var(--a-muted)';el.append(probe);const muted=getComputedStyle(probe).color;probe.remove();return {color:label.color,weight:label.fontWeight,line:style.borderBottomColor,muted}});
  assert.equal(styles.color,styles.muted);assert.equal(styles.weight,'400');assert.match(styles.line,/0\.55/);
 };
 const checkPrimary=async actionName=>{
  const primary=page.locator('[data-part="composer-primary-action"]');await expect(primary).toHaveAttribute('data-action',actionName);
  const colors=await primary.evaluate(el=>{const probe=document.createElement('span');probe.className='a-attention-badge';el.closest('#amp-one').append(probe);const button=getComputedStyle(el),badge=getComputedStyle(probe),icon=getComputedStyle(el.querySelector('svg'));const result={fg:button.color,bg:button.backgroundColor,badgeFg:badge.color,badgeBg:badge.backgroundColor,icon:icon.color};probe.remove();return result});
  assert.equal(colors.fg,colors.badgeFg);assert.equal(colors.bg,colors.badgeBg);assert.equal(colors.icon,colors.fg);
  const luminance=color=>color.match(/[\d.]+/g).slice(0,3).map(Number).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4).reduce((a,n,i)=>a+n*[.2126,.7152,.0722][i],0);
  const a=luminance(colors.fg),b=luminance(colors.bg);assert.ok((Math.max(a,b)+.05)/(Math.min(a,b)+.05)>=4.5,JSON.stringify(colors));
 };
 for(const scheme of ['light','dark']){
  await action('theme.apply',{name:'Saved Graphite',css:savedGraphite});await presentation({scheme});
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
  await checkLayout();await checkPrimary('call.start');
  await input.fill('Ready to send');await checkPrimary('conversation.send');await clearComposer(input);
  await control({op:'patch',sessions:{[alpha]:{status:'working'}}});await checkPrimary('conversation.stop');
  await control({op:'patch',sessions:{[alpha]:{status:'idle'}}});await checkPrimary('call.start');
  await page.screenshot({path:`/tmp/unified-chat-polish-${scheme}.png`});
 }
 await page.reload();await expect(body).toBeVisible();await checkLayout();await checkPrimary('call.start');
 await user.getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message'}).fill('Updated message');await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await input.fill('Please inspect this report.');
 const pasted='Diagnostic report 📋\r\n'.repeat(700);
 await input.evaluate((element,text)=>{const data=new DataTransfer();data.setData('text/plain',text);element.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}))},pasted);
 const attachment=page.locator('[data-part="composer"] .a-attachment');await expect(attachment).toHaveCount(1);await expect(attachment).toContainText('Pasted text');await expect(input).toHaveDraft('Please inspect this report.');
 const href=await attachment.locator('a').getAttribute('href');assert.equal(await (await fetch(url+href,{headers})).text(),pasted);
 await page.reload();await expect(page.locator('[data-part="composer"] .a-attachment')).toContainText('Pasted text');
 await page.screenshot({path:'/tmp/unified-compact-messages-desktop.png'});
 await page.setViewportSize({width:390,height:844});await expect(page.locator('[data-message-id="u1"] .a-message-actions')).toHaveCSS('opacity','1');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'/tmp/unified-compact-messages-mobile.png'});
 await page.getByRole('button',{name:'Remove Pasted text.txt',exact:true}).click();await expect(page.locator('[data-part="composer"] .a-attachment')).toHaveCount(0);
 assert.deepEqual(errors,[]);console.log('Compact messages passed: saved light/dark Graphite, external footers, long-message width and padding, hover stability, voice/send/stop badge colors and contrast, newest actions, grouped lazy work, editing, exact large paste, reload and mobile.');
}finally{await browser?.close();fixture.kill()}
