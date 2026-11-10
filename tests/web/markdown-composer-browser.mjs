import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';
mkdirSync(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
 const url=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const r=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(r.status,200);return r.json()};
 const {alpha,beta}=await control({op:'heavy',messages:2,otherMessages:2,chars:30,nodes:0});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:headers}),errors=[],sends=[],serverErrors=[];
 page.on('response',r=>{if(r.status()>=500&&r.url().includes('/api/'))serverErrors.push(r.status()+' '+r.url())});
 page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.message)});page.on('request',r=>{if(r.url().endsWith('/api/actions'))try{const body=r.postDataJSON();if(body.action==='conversation.send')sends.push(body)}catch{}});
 await page.goto(url);await showFixtureConversation(page);const input=page.getByRole('textbox',{name:'Message Amplifier'});await input.waitFor();
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const draft=()=>page.evaluate(()=>window.amplifier.getState().view.draft);
 const clear=async()=>{await input.press('Control+a');await input.press('Backspace');await expect.poll(draft).toBe('');};
 const paste=text=>input.evaluate((el,text)=>{const clipboardData=new DataTransfer();clipboardData.setData('text/plain',text);el.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}))},text);
 // Use committed app presentation, not a colorScheme-only DOM override.
 const composerPresentation=async presentation=>page.evaluate(async presentation=>{
  const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation}});
  await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id});
 },presentation);
 const selectorEvidence=async(bar,label,{focused=false}={})=>{
  const select=bar.getByLabel('Text style');await expect(select).toBeVisible();
  const evidence=await select.evaluate(el=>{
   const popup=el.closest('.a-composer-format'),row=el.closest('.a-composer-format-row'),style=getComputedStyle(el);
   const rect=node=>{const {x,y,width,height}=node.getBoundingClientRect();return {x,y,width,height}};
   const current={select:rect(el),popup:rect(popup)};
   const paint={background:style.backgroundColor,ink:style.color,radius:style.borderRadius,boxSizing:style.boxSizing,appearance:style.appearance,padding:style.paddingTop,
    borders:['Top','Right','Bottom','Left'].map(side=>({width:style['border'+side+'Width'],style:style['border'+side+'Style'],color:style['border'+side+'Color']})),
    outline:{width:style.outlineWidth,style:style.outlineStyle,color:style.outlineColor,offset:style.outlineOffset},focusVisible:el.matches(':focus-visible')};
   // Resolve theme tokens in the same inheritance scope as the portal.
   const probe=document.createElement('span');probe.style.cssText='position:absolute;visibility:hidden;pointer-events:none;background:var(--a-soft);border:1px solid var(--a-line);color:var(--a-ink);outline:2px solid var(--a-accent)';
   popup.appendChild(probe);let palette;
   try{const tokens=getComputedStyle(probe);palette={soft:tokens.backgroundColor,line:tokens.borderTopColor,ink:tokens.color,accent:tokens.outlineColor,surface:getComputedStyle(popup).backgroundColor}}finally{probe.remove()}
   // Compare border boxes with the exact old select rule (0 border + 7px
   // padding), then restore immediately without editor/focus events.
   const inline=el.getAttribute('style');let baseline;
   try{el.style.border='0';el.style.padding='7px';baseline={select:rect(el),popup:rect(popup)}}finally{if(inline===null)el.removeAttribute('style');else el.setAttribute('style',inline)}
   const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d');
   const rgba=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data]};
   const luminance=color=>rgba(color).slice(0,3).map(value=>{const c=value/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4}).reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0);
   const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
   return {current,baseline,paint,palette,lineAlpha:rgba(palette.line)[3],textContrast:contrast(palette.ink,palette.soft),borderContrast:contrast(palette.line,palette.soft),
    row:rect(row),buttons:[...row.querySelectorAll('button')].map(rect),popupOverflow:popup.scrollWidth>popup.clientWidth,pageOverflow:document.documentElement.scrollWidth>innerWidth};
  });
  assert.equal(evidence.paint.boxSizing,'border-box');assert.equal(evidence.paint.padding,'6px');assert.equal(evidence.paint.radius,'8px');
  assert.notEqual(evidence.paint.appearance,'none','The native select arrow remains available');
  for(const border of evidence.paint.borders){assert.equal(border.width,'1px');assert.equal(border.style,'solid');assert.equal(border.color,evidence.palette.line)}
  assert.ok(evidence.lineAlpha>0,'The selector border is not transparent');
  assert.equal(evidence.paint.background,evidence.palette.soft);assert.notEqual(evidence.paint.background,evidence.palette.surface,'Selector tint differs from the toolbar surface');
  assert.equal(evidence.paint.ink,evidence.palette.ink);assert.ok(evidence.textContrast>=4.5,'Selector text remains readable against the tint');
  for(const part of ['select','popup'])for(const dimension of ['x','y','width','height']){
   assert.ok(Math.abs(evidence.current[part][dimension]-evidence.baseline[part][dimension])<=.5,`${label}: ${part} ${dimension} retains the old outer geometry`);
  }
  const box=evidence.current.select;assert.ok(box.width>0&&box.height>=36);
  for(const button of evidence.buttons)assert.ok(Math.abs(box.height-button.height)<=.5,'Selector retains the neighboring control height');
  for(const control of [box,...evidence.buttons])assert.ok(control.x>=evidence.row.x-.5&&control.x+control.width<=evidence.row.x+evidence.row.width+.5,'All controls fit the toolbar row');
  assert.equal(evidence.popupOverflow,false,'The selector does not overflow its toolbar');
  if(focused){await expect(select).toBeFocused();assert.equal(evidence.paint.focusVisible,true);assert.deepEqual(evidence.paint.outline,{width:'2px',style:'solid',color:evidence.palette.accent,offset:'1px'})}
  console.log('Composer selector evidence',label,JSON.stringify(evidence));return evidence;
 };
 const selectorThemes=async(bar,label)=>{
  const presentation=await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation);
  const before=await draft(),sent=sends.length,paints=[];
  try{
   for(const scheme of ['light','dark']){
    await composerPresentation({...presentation,scheme});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
    await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation.scheme)).toBe(scheme);
    await input.focus();await input.press('Control+a');await expect(bar).toBeVisible();
    await bar.getByRole('button',{name:'Strikethrough',exact:true}).focus();await page.keyboard.press('Tab');
    const evidence=await selectorEvidence(bar,`${label} ${scheme}`,{focused:true});paints.push(evidence.palette);
    if(label==='390px'){assert.equal(evidence.pageOverflow,false);const popup=evidence.current.popup;assert.ok(popup.x>=0&&popup.x+popup.width<=390)}
    assert.equal(await draft(),before,'Theme/focus checks preserve the unsent draft');assert.equal(sends.length,sent,'Theme/focus checks never send');
   }
   assert.notEqual(paints[0].soft,paints[1].soft);assert.notEqual(paints[0].line,paints[1].line);assert.notEqual(paints[0].ink,paints[1].ink);
  }finally{await composerPresentation(presentation)}
  // Escape from the keyboard-focused selector must dismiss without sending.
  await bar.getByLabel('Text style').press('Escape');await expect(bar).toHaveCount(0);await expect(input).toBeFocused();
  assert.equal(await draft(),before);assert.equal(sends.length,sent);
  await input.press('Control+a');await expect(bar).toBeVisible();
 };
 await input.pressSequentially('**bold** _emphasis_');await expect(input.locator('strong')).toHaveText('bold');await expect(input.locator('em')).toHaveText('emphasis');await expect.poll(draft).toBe('**bold** *emphasis*');assert.ok((await input.boundingBox()).height<=56,'A single-line draft stays compact');
 await input.press('Control+z');await expect(input.locator('em')).toHaveCount(0);await input.press('Control+Shift+z');await expect(input.locator('em')).toHaveCount(1);
 await input.press('Control+a');await input.press('Delete');await expect.poll(draft).toBe('');await input.pressSequentially('# Heading');await expect(input.locator('h1')).toHaveText('Heading');await input.press('Control+Enter');await input.pressSequentially('* One');await input.press('Control+Enter');await input.pressSequentially('Two');await expect(input.locator('ul li')).toHaveCount(2);
 await input.press('Control+Enter');await input.press('Backspace');await input.pressSequentially('Outside');await expect(input.locator(':scope > p')).toHaveText('Outside');
 await input.press('Shift+Enter');await input.pressSequentially('1. First');await input.press('Control+Enter');await input.pressSequentially('Second');await expect(input.locator('ol li')).toHaveCount(2);assert.equal(sends.length,0);
 const markdown=await draft();await expect.poll(async()=>{const state=await (await fetch(url+'/api/state',{headers:{...headers,'X-Amplifier-Client':await page.evaluate(()=>window.amplifier.getState().client.id)}})).json();return state.view?.draft}).toBe(markdown);
 await action('session.select',{id:beta});await expect.poll(draft).toBe('');await input.pressSequentially('Other **draft**');await action('session.select',{id:alpha});await expect(input.locator('h1')).toHaveText('Heading');await expect.poll(draft).toBe(markdown);
 await page.reload();await input.waitFor();await expect(input.locator('ol li')).toHaveCount(2);await expect.poll(draft).toBe(markdown);
 // External/agent draft changes render without replaying them as user edits.
 await action('view.update',{patch:{draft:'## From an agent\n\nKeep **this**.'},sessionId:alpha});await expect(input.locator('h2')).toHaveText('From an agent');await expect(input.locator('strong')).toHaveText('this');
 await clear();await input.pressSequentially('```js ');await input.pressSequentially('const text = "**literal**";');await input.press('Control+Enter');await input.pressSequentially('// next');await expect(input.locator('pre code')).toContainText('**literal**');await expect(input.locator('strong')).toHaveCount(0);
 // The opener is completed by ordinary Enter, not only a trailing space.
 await clear();await input.pressSequentially('```python');await input.press('Enter');
 await expect(input.locator('pre code')).toHaveCount(1);assert.equal(sends.length,0);
 await input.pressSequentially('  print("**literal**")');await input.press('Enter');await input.pressSequentially('next');
 await expect(input.locator('pre code')).toHaveText('  print("**literal**")\n  next');
 assert.equal(sends.length,0,'Enter inside code never sends');
 await input.press('Alt+Enter');await input.pressSequentially('Outside code');
 await expect(input.locator(':scope > p')).toHaveText('Outside code');assert.equal(sends.length,0);
 await clear();await input.pressSequentially('```');await input.press('Enter');await input.press('Control+z');
 await expect(input.locator('pre')).toHaveCount(0);await expect(input).toHaveText('```');
 await input.press('Control+Shift+z');await expect(input.locator('pre')).toHaveCount(1);
 await input.press('Backspace');await expect(input.locator('pre')).toHaveCount(0);
 await clear();await input.pressSequentially('```');await input.evaluate(el=>el.dispatchEvent(new InputEvent('beforeinput',{inputType:'insertParagraph',bubbles:true,cancelable:true})));
 await expect(input.locator('pre')).toHaveCount(1);assert.equal(sends.length,0,'virtual-keyboard fence boundary does not send');
 await clear();await paste('Hello **from paste**\n\n- A\n- B');await expect(input.locator('strong')).toHaveText('from paste');await expect(input.locator('li')).toHaveCount(2);
 await clear();await paste('<script>alert(1)</script>');await expect(input.locator('script')).toHaveCount(0);await expect(input).toHaveText('<script>alert(1)</script>');
 await clear();await input.evaluate(el=>{const dataTransfer=new DataTransfer();dataTransfer.setData('text/html','<a href="javascript:alert(1)">unsafe</a>');el.dispatchEvent(new DragEvent('drop',{dataTransfer,bubbles:true,cancelable:true}));});
 await expect(input.locator('a')).toHaveCount(0);await expect(input).toHaveText('<a href="javascript:alert(1)">unsafe</a>');
 const gfm='~~Before~~\n\n- [x] Done\n  - [ ] Nested\n\n| A | B |\n| :--- | ---: |\n| **bold** | `code` |';
 await clear();await paste(gfm);
 await expect(input.locator('s')).toHaveText('Before');await expect(input.locator('li[data-task]')).toHaveCount(2);await expect(input.locator('table th')).toHaveCount(2);
 const richDraft=await draft();assert.match(richDraft,/~~Before~~/);assert.match(richDraft,/\[x\] Done/);
 await action('session.select',{id:beta});await clear();await input.pressSequentially('Private other chat');
 await action('session.select',{id:alpha});await expect.poll(draft).toBe(richDraft);await page.reload();await input.waitFor();await expect(input.locator('table th')).toHaveCount(2);
 await expect.poll(draft).toBe(richDraft);
 // Unsupported website syntax/reference definitions remain editable raw source.
 const raw='Before[^n]\n\n[^n]: café e\u0301 東京\n    literal continuation\n\n> [!NOTE]\n> **Do not rewrite**\n\nAfter';
 await clear();await paste(raw);await expect(input.locator('.a-composer-markdown-source')).toHaveCount(1);await expect.poll(draft).toBe(raw);
 await input.press('Control+End');await input.pressSequentially(' edited');
 for(let cycle=0;cycle<2;cycle++){await action('session.select',{id:beta});await expect.poll(draft).toBe('Private other chat');await action('session.select',{id:alpha});await page.reload();await input.waitFor();await expect.poll(draft).toBe(raw+' edited');}
 await clear();await input.pressSequentially('Source mode');await input.press('Control+a');
 await page.getByRole('toolbar',{name:'Format selected text'}).getByLabel('Text style').selectOption('markdown_source');
 for(let cycle=0;cycle<2;cycle++){
  await action('session.select',{id:beta});await expect.poll(draft).toBe('Private other chat');await action('session.select',{id:alpha});
  await page.reload();await input.waitFor();await expect(input.locator('.a-composer-markdown-source')).toHaveCount(1);
  await input.press('Control+End');await input.press('Enter');assert.equal(sends.length,0);
 }
 // Selection toolbar preserves the selected range while controls receive focus.
 await clear();await input.pressSequentially('Selected words');await input.press('Control+a');
 const bar=page.getByRole('toolbar',{name:'Format selected text'});await expect(bar).toBeVisible();
 const contextPrevented=await input.evaluate(el=>{const event=new MouseEvent('contextmenu',{bubbles:true,cancelable:true});el.dispatchEvent(event);return event.defaultPrevented;});
 assert.equal(contextPrevented,false,'native browser copy/paste/spelling menu stays available');
 await input.press('Alt+F10');await expect(bar.getByRole('button',{name:'Link',exact:true})).toBeFocused();await page.keyboard.press('Escape');await expect(input).toBeFocused();
 await input.press('Control+a');await expect(bar).toBeVisible();
 await selectorThemes(bar,'desktop');
 assert.equal(await draft(),'Selected words');assert.equal(sends.length,0);
 await bar.getByRole('button',{name:'Bold',exact:true}).click();await expect(input.locator('strong')).toHaveText('Selected words');
 await bar.getByRole('button',{name:'Italic',exact:true}).click();await expect(input.locator('em')).toHaveText('Selected words');
 await bar.getByRole('button',{name:'Strikethrough',exact:true}).click();await expect(input.locator('s')).toHaveText('Selected words');
 await input.press('Control+z');await expect(input.locator('s')).toHaveCount(0);await expect(input.locator('strong')).toHaveText('Selected words');
 for(const [value,tag] of [['h1','h1'],['h2','h2'],['h3','h3'],['ordered_list','ol li'],['bullet_list','ul li'],['text','p']]){
  await bar.getByLabel('Text style').selectOption(value);await expect(input.locator(tag)).toHaveText('Selected words');
  await selectorEvidence(bar,`block ${value}`);await expect(input.locator('strong')).toHaveText('Selected words');await expect(input.locator('em')).toHaveText('Selected words');assert.equal(sends.length,0);
 }
 await bar.getByRole('button',{name:'Link',exact:true}).click();await page.getByRole('textbox',{name:'Link address'}).fill('javascript:alert(1)');await page.getByRole('button',{name:'Apply',exact:true}).click();await expect(page.getByRole('alert').filter({hasText:'Use an http'})).toBeVisible();
 await page.getByRole('textbox',{name:'Link address'}).fill('https://example.com/selected');await page.getByRole('textbox',{name:'Link address'}).press('Enter');await expect(input.locator('a')).toHaveAttribute('href','https://example.com/selected');assert.equal(sends.length,0);
 await bar.getByRole('button',{name:'Link',exact:true}).click();await page.getByRole('button',{name:'Remove link',exact:true}).click();await expect(input.locator('a')).toHaveCount(0);
 await input.press('Escape');await expect(bar).toHaveCount(0);
 await clear();await paste('https://example.com/raw and [Label](https://example.com/labeled)');await expect(input.locator('a')).toHaveCount(2);
 await clear();await input.pressSequentially('[Typed](https://example.com/typed) https://example.com/raw ');await expect(input.locator('a')).toHaveCount(2);
 await input.press('Control+a');await expect(bar).toBeVisible();await page.setViewportSize({width:390,height:844});await input.press('Control+a');await expect(bar).toBeVisible();
 await expect.poll(async()=>{const rect=await bar.boundingBox();return !!rect&&rect.x>=0&&rect.x+rect.width<=390}).toBe(true);await page.screenshot({path:join(output,'selection-toolbar-mobile.png')});
 await selectorThemes(bar,'390px');
 await page.setViewportSize({width:1280,height:900});await page.screenshot({path:join(output,'selection-toolbar-desktop.png')});await page.evaluate(()=>{document.querySelector('#amp-one').style.colorScheme='dark'});await input.press('Control+a');await expect(bar).toBeVisible();await page.screenshot({path:join(output,'selection-toolbar-dark.png')});
 await clear();await input.pressSequentially('Keep this prompt');await paste('Diagnostics\n'.repeat(1000));await expect(page.locator('.a-attachment')).toContainText('Pasted text');await expect(input).toHaveText('Keep this prompt');
 await page.getByRole('button',{name:'Remove Pasted text.txt',exact:true}).click();
 // Image and Markdown image pastes do not leak remote requests.
 const remote=[];page.on('request',r=>{if(r.url().includes('example.invalid'))remote.push(r.url())});
 await clear();await paste('![diagram](https://example.invalid/image.png)');await expect(input.locator('.a-composer-image-reference')).toHaveText('diagram');assert.deepEqual(remote,[]);
 await clear();await input.evaluate(el=>{const clipboardData=new DataTransfer();const bytes=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDioAAAAASUVORK5CYII='),c=>c.charCodeAt(0));clipboardData.items.add(new File([bytes],'clipboard.png',{type:'image/png'}));el.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}))});
 await expect(page.locator('.a-attachment')).toHaveCount(1);await page.getByRole('button',{name:'Remove clipboard.png',exact:true}).click();
 await clear();await input.pressSequentially('Do not send during composition');await input.evaluate(el=>{el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:229,isComposing:true,bubbles:true,cancelable:true}));el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:''}));});assert.equal(sends.length,0);
 await clear();await input.pressSequentially('Send **formatted**');await input.press('Enter');await expect.poll(()=>sends.length).toBe(1);assert.equal(sends[0].args.text,'Send **formatted**');await expect(input).toHaveText('');await expect(page.locator('.a-user strong').last()).toHaveText('formatted');await expect(page.locator('.a-assistant').last()).toContainText('Synthetic reply.');
 await paste(raw);await page.locator('[data-part="composer-primary-action"]').click();await expect.poll(()=>sends.length).toBe(2);
 assert.equal(sends[1].args.text,raw);await expect(input).toHaveText('');await expect(page.locator('.a-assistant').last()).toContainText('Synthetic reply.');
 await input.pressSequentially('# A live composer');await input.press('Shift+Enter');await input.pressSequentially('**Bold** and _emphasis_');await input.press('Shift+Enter');await input.pressSequentially('* First item');await input.press('Control+Enter');await input.pressSequentially('Second item');
 await page.screenshot({path:join(output,'unified-markdown-composer-desktop.png')});
 await page.setViewportSize({width:390,height:844});await expect.poll(async()=>(await input.boundingBox()).width).toBeGreaterThan(300);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok((await input.boundingBox()).height<=180);await page.screenshot({path:join(output,'unified-markdown-composer-mobile.png')});
 assert.deepEqual(serverErrors,[]);assert.deepEqual(errors,[]);
 writeFileSync(join(output,'markdown-composer.json'),JSON.stringify({status:'passed',checks:['typed-fence-enter','literal-code-indent-exit','fence-undo-redo-backspace','virtual-keyboard-boundary','gfm-rich-paste','raw-extension-source','two-reload-cycles','explicit-source-mode-reload-enter','other-chat-retention','native-context-menu','keyboard-toolbar','selected-strike-undo','inert-html-drop','raw-send','attachments','IME','desktop-mobile'],sends:sends.map(send=>send.args.text),pageErrors:errors,serverErrors},null,2));
 console.log('Markdown composer passed: live formatting, undo/redo, headings/lists and exit, draft switch/reload/agent update, literal code, Markdown and large paste, safe HTML, IME guard, Markdown send, desktop/mobile.');
}finally{await browser?.close();fixture.kill()}
