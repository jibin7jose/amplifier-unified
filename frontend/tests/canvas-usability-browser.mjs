import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
// Real host and production assets with synthetic history; no model calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',data=>{output+=data;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1500,height:850},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create',{});
 const panel=page.locator('#workspace-canvas');
 // Overflow must reveal selected tabs without moving the surrounding page.
 const ids=[];
 for(let i=0;i<8;i++){await action('canvas.show',{kind:'markdown',title:'Document '+i+' with a long descriptive name',content:'# Note '+i});ids.push(await page.evaluate(()=>window.amplifier.getState().canvas.id))}
 const documentTabs=page.getByRole('tablist',{name:'Canvas views'}).locator('.a-canvas-document-tabs');
 const visibleTab=()=>expect.poll(()=>documentTabs.evaluate(strip=>{const active=strip.querySelector('[role="tab"][aria-selected="true"]');const a=active.getBoundingClientRect(),s=strip.getBoundingClientRect();return a.left>=s.left-1&&a.right<=s.right+1})).toBe(true);
 await visibleTab();
 await action('canvas.select',{id:ids[0]});await visibleTab();
 await action('canvas.select',{id:ids[7]});await visibleTab();
 await page.setViewportSize({width:390,height:844});await visibleTab();
 await expect(page.getByRole('tab',{selected:true})).toHaveAttribute('title','Document 7 with a long descriptive name');
 await page.setViewportSize({width:1500,height:850});
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 const downloadEvent=page.waitForEvent('download');
 await panel.getByRole('button',{name:'Download canvas source',exact:true}).click();
 assert.equal((await downloadEvent).suggestedFilename(),'Document 7 with a long descriptive name.md');

 // Document tabs keep their friendly title; Details retains the exact file identity.
 const state=await page.evaluate(()=>window.amplifier.getState());
 const workspace=state.workspaces.find(w=>w.id===state.selectedWorkspaceId).path;
 const {writeFile}=await import('node:fs/promises');
 await writeFile(workspace+'/actual-name.md','# File content');
 await action('canvas.show',{kind:'auto',path:workspace+'/actual-name.md',title:'Friendly file title'});
 await expect(documentTabs.getByRole('tab',{selected:true})).toHaveAttribute('title','Friendly file title');
 const filePath=panel.locator('.a-canvas-inspector details.a-canvas-file-path');
 const details=panel.getByRole('button',{name:'Canvas details',exact:true});
 await details.click();await expect(filePath).toBeHidden();
 await details.click();await expect(filePath).toBeVisible();
 await filePath.getByText('File details',{exact:true}).click();
 await expect(filePath.locator('code')).toHaveText([workspace+'/actual-name.md',workspace,'actual-name.md']);
 await expect(filePath.getByRole('button',{name:'Copy full path',exact:true})).toBeEnabled();
 await expect(filePath.getByRole('button',{name:'Copy relative path',exact:true})).toBeEnabled();
 // A sent Markdown attachment opens a preview and retains its download.
 await action('attachment.add',{sessionId:state.selectedSessionId,name:'Review notes.md',base64:Buffer.from('# Attached review\n\nThe original text.').toString('base64')});
 const attachment=await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).draftAttachments[0]);
 await action('conversation.send',{sessionId:state.selectedSessionId,text:'Review my notes',attachmentIds:[attachment.id]});
 const sentNote=page.locator('[data-message-id]').filter({hasText:'Review my notes'});
 await sentNote.locator('[data-action="canvas.openAttachment"]').filter({hasText:'Review notes.md'}).click();
 await expect(panel.getByRole('heading',{name:'Attached review'})).toBeVisible();
 await expect(sentNote.getByRole('link',{name:'Download Review notes.md',exact:true})).toHaveAttribute('href',attachment.url);
 await page.reload();await expect(panel.getByRole('heading',{name:'Attached review'})).toBeVisible();
 // Current Graphviz and tall embedded Mermaid rendering, independent of old reports.
 await action('canvas.show',{kind:'dot',title:'Graph check',content:'digraph G { root [label="Root"]; root -> worker; worker -> result; }'});
 await expect.poll(()=>panel.locator('.a-diagram-stage img').evaluateAll(nodes=>nodes.length===1&&nodes[0].complete&&nodes[0].naturalWidth>0)).toBe(true);
 await action('canvas.show',{kind:'markdown',title:'Tall diagram',content:'# Tall diagram\n\n```mermaid\nflowchart TD\n'+Array.from({length:25},(_,i)=>'N'+i+' --> N'+(i+1)).join('\n')+'\n```'});
 const diagram=panel.locator('.a-diagram-stage');
 await expect.poll(()=>diagram.locator('img').evaluateAll(nodes=>nodes.length===1&&nodes[0].complete&&nodes[0].naturalHeight>0)).toBe(true);
 const extent=await diagram.evaluate(el=>{el.scrollTop=el.scrollHeight;return {height:el.scrollHeight,viewport:el.clientHeight,top:el.scrollTop}});
 assert.ok(extent.height>extent.viewport&&extent.top>0,'Tall diagram must be scrollable to its bottom');
 await page.screenshot({path:'/tmp/unified-canvas-usability97.png'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({activeTabs:true,narrowTabs:true,filenameDetails:true,titledDownloads:true,markdownAttachment:true,reload:true,graphvizDecoded:true,tallMermaidScrollable:true,browserErrors:0}));
}finally{await browser?.close();fixture.kill('SIGTERM')}
