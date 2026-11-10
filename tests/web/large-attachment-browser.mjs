import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'inherit'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(resolve=>setTimeout(resolve,100))}
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
page.on('pageerror',error=>errors.push(error.message));
try{
 await page.goto('http://127.0.0.1:8958/');await page.waitForSelector('#amp-one');
 // Exercise intake/storage capacity, independent of any provider's image decoder.
 const bytes=Buffer.alloc(22*1024*1024,1);
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'camera-sized.bin',mimeType:'application/octet-stream',buffer:bytes});
 await page.locator('.a-composer .a-attachment').waitFor();
 await page.getByRole('button',{name:'Add attachments',exact:true}).click();
 const addMenu=page.getByRole('dialog',{name:'Add to your message',exact:true});
 await addMenu.waitFor();assert.match(await addMenu.getByRole('button',{name:'Attach files',exact:true}).innerText(),/32 MB/);
 await addMenu.getByRole('button',{name:'Close attachment menu',exact:true}).click();
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this original file.');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.locator('.a-assistant h2').waitFor();
 assert.equal(await page.locator('.a-user .a-attachment').count(),1);
 await page.reload();await page.locator('.a-user .a-attachment').waitFor();
 const saved=await page.evaluate(async()=>{
  const state=window.amplifier.getState();
  const row=state.sessions.find(s=>s.id===state.selectedSessionId).messages.find(m=>m.attachments?.length).attachments[0];
  const data=new Uint8Array(await (await fetch(row.url)).arrayBuffer());
  return {size:row.size,bytes:data.length,unchanged:data.every(value=>value===1)};
 });
 assert.deepEqual(saved,{size:bytes.length,bytes:bytes.length,unchanged:true});
 assert.deepEqual(errors,[]);
 console.log('Large attachment browser passed: 22 MiB picker upload, send, history reload, exact original download, and 32 MB guidance.');
}finally{await browser.close();fixture.kill();}
