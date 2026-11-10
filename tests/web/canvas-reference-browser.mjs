import {readComposerDraft} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--canvas-versions'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1500,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],quotes=[],draftSaves=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!request.url().endsWith('/api/actions'))return;const data=request.postDataJSON();if(!data)return;if(data.action==='canvas.reference'||data.action==='canvas.views.command'&&data.args.action==='canvas.reference')quotes.push(data);if(data.action==='view.update'&&'draft' in (data.args.patch||{}))draftSaves.push(data)});
 await page.goto(url);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const select=async locator=>locator.evaluate(element=>{element.closest('.a-canvas-preview').focus();const range=document.createRange();range.selectNodeContents(element);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'))});
 const button=page.getByRole('button',{name:'Reference in chat',exact:true});
 await action('session.create');
 const sessionId=(await state()).selectedSessionId;
 const source='# Document\n\nA **bold** &amp; 😀 `code`.\n\nRepeated passage.\n\nRepeated passage.';
 const artifact=(await action('canvas.show',{kind:'markdown',title:'Reference document',content:source})).result;
 await action('view.update',{patch:{draft:'Keep my draft  ',canvasControlsPinned:true,canvasControlsExpanded:true}});
 await expect(button).toBeDisabled();
 const before=(await state()).revision,saves=draftSaves.length;
 await select(page.locator('.a-canvas-preview p').filter({hasText:'A bold & 😀 code.'}));
 await expect(button).toBeEnabled();
 // Selecting is local: no action, draft write or model call.
 assert.equal(quotes.length,0);assert.equal(draftSaves.length,saves);assert.equal((await state()).view.draft,'Keep my draft  ');
 await button.focus();await page.keyboard.press('Enter');
 await expect(composer).toHaveDraft(/Keep my draft  \n\nFrom \[Reference document · Version 1\]/);
 assert.match(await readComposerDraft(composer),/> A bold \\\& 😀 code\\\./);
 assert.equal(quotes.length,1);
 assert.equal(quotes[0].args.args.version,1);
 // The second identical phrase is located by its own offsets, not find(text).
 await select(page.locator('.a-canvas-preview p').filter({hasText:'Repeated passage.'}).nth(1));
 await button.click();await expect(composer).toHaveDraft(/Repeated passage/);
 assert.equal(quotes[1].args.args.spans[0].start,[...source.slice(0,source.lastIndexOf('Repeated passage.'))].length);
 // A debounced edit is flushed before quote insertion. The composer is locked
 // only while its explicit insertion is in flight, including a slow save.
 let release,seen;const gate=new Promise(resolve=>release=resolve),waiting=new Promise(resolve=>seen=resolve);
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body.action==='view.update'&&body.args.patch?.draft==='Fresh unsaved draft'){seen();await gate}await route.continue()});
 await composer.fill('Fresh unsaved draft');
 await select(page.locator('.a-canvas-preview p').filter({hasText:'A bold & 😀 code.'}));
 await button.click();await waiting;await expect(composer).toHaveAttribute('aria-readonly','true');release();
 await expect(composer).toHaveDraft(/^Fresh unsaved draft\n\nFrom /);await expect(composer).not.toHaveAttribute('aria-readonly','true');await page.unroute('**/api/actions');
 const kept=await readComposerDraft(composer);
 await action('canvas.versions.revise',{id:artifact.id,expectedRevision:1,content:'# New version\n\nNewer facts.'});
 await action('canvas.select',{id:artifact.id,version:1});
 await select(page.locator('.a-canvas-preview p').filter({hasText:'Repeated passage.'}).nth(0));
 await button.click();await expect(composer).toHaveDraft(kept+'From [Reference document · Version 1]('+artifact.reference+'):\n\n> Repeated passage\\.\n\n');
 await page.reload();await composer.waitFor();await expect(composer).toHaveDraft(/version=1/);await expect(page.getByRole('combobox',{name:'Artifact version'})).toHaveValue('1');
 // Plain text quotes markup literally, and multi-paragraph selection works.
 const plain='**literal** &amp;\nNext line.\n😀';
 await action('canvas.show',{kind:'text',title:'Plain',content:plain});
 await select(page.locator('.a-canvas-plain'));await button.click();await expect(composer).toHaveDraft(/> \\\*\\\*literal\\\*\\\* \\\&amp;/);
 await action('canvas.show',{kind:'text',content:'x'.repeat(4001)});await select(page.locator('.a-canvas-plain'));await expect(button).toBeDisabled();await expect(page.getByText('Select at most 4,000 characters to reference.')).toBeVisible();
 // A selection crossing into the chat/composer is never accepted.
 await page.evaluate(()=>{const start=document.querySelector('.a-canvas-plain').firstChild.firstChild,end=document.querySelector('.a-composer');const range=document.createRange();range.setStart(start,0);range.setEndAfter(end);const selected=window.getSelection();selected.removeAllRanges();selected.addRange(range);document.dispatchEvent(new Event('selectionchange'))});
 await expect(button).toBeDisabled();
 // A late navigation can overtake a held quote request; the server refuses to
 // place the quote into the new chat, keeping the original draft intact.
 const text=(await action('canvas.show',{kind:'text',content:'Late quote'})).result;
 const originalDraft=await readComposerDraft(composer);let releaseQuote,seenQuote;
 const quoteGate=new Promise(resolve=>releaseQuote=resolve),quoteWaiting=new Promise(resolve=>seenQuote=resolve);
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body.action==='canvas.views.command'&&body.args.action==='canvas.reference'){seenQuote();await quoteGate}await route.continue()});
 await select(page.locator('.a-canvas-plain'));await button.click();await quoteWaiting;
 // Use the separate navigation queue, as an actual chat-list click does.
 await action('session.draft');releaseQuote();await expect(composer).not.toHaveAttribute('aria-readonly','true');
 assert.equal((await state()).view.draft,'');await page.unroute('**/api/actions');
 await action('session.select',{id:sessionId});await expect(composer).toHaveDraft(originalDraft);
 assert.deepEqual((await page.request.get(url+'/fixture')).ok(),true);
 const fixtureState=await (await page.request.get(url+'/fixture')).json();assert.equal(fixtureState.sent.length,0);
 assert.deepEqual(errors,[]);
 await page.screenshot({path:'/tmp/canvas-reference-browser.png',fullPage:true});
 console.log('Canvas text reference: passive selection, keyboard action, exact repeated-source spans, formatting/Unicode, slow draft flush, historical version/reload, plain text, bounds and late navigation passed; zero sends.');
}finally{await browser?.close();fixture.kill()}
