import {showFixtureConversation} from './browser-chat-pages.mjs';
// Real app routes and PDF.js, synthetic files only. Run inside the project DTU.
import {spawn,execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile,mkdir} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifacts required');
const fixture=spawn(python,[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
function pdf(pages){
 const objects=['<< /Type /Catalog /Pages 2 0 R >>',`<< /Type /Pages /Count ${pages} /Kids [${Array.from({length:pages},(_,i)=>`${4+i*2} 0 R`).join(' ')}] >>`,'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
 for(let i=0;i<pages;i++){
  const content=`0.1 0.5 0.7 rg 30 40 180 90 re f\nBT /F1 24 Tf 30 340 Td (PDF page ${i+1} selectable text) Tj ET`;
  objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 400] /Resources << /Font << /F1 3 0 R >> >> /Contents ${5+i*2} 0 R >>`,`<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
 }
 let out='%PDF-1.4\n',offsets=[0];objects.forEach((object,i)=>{offsets.push(out.length);out+=`${i+1} 0 obj\n${object}\nendobj\n`});
 const xref=out.length;out+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`+offsets.slice(1).map(offset=>`${String(offset).padStart(10,'0')} 00000 n \n`).join('');out+=`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;return out;
}
let browser;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',data=>{output+=data;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1500,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create',{});
 const workspace=await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).workspace);
 const original=pdf(2);await writeFile(workspace+'/Report.pdf',original);await writeFile(workspace+'/Single.pdf',pdf(1));await writeFile(workspace+'/Damaged.pdf','%PDF-1.7\nnot a document');
 assert.equal((await action('canvas.show',{kind:'auto',path:'Report.pdf'})).accepted,true);
 const identity=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await expect(page.locator('.a-pdf-text')).toContainText('PDF page 1 selectable text');
 await expect(page.locator('.a-pdf-stage')).toHaveAttribute('aria-busy','false');
 const pixels=await page.locator('.a-pdf-page canvas').evaluate(canvas=>Array.from(canvas.getContext('2d').getImageData(60,canvas.height-80,1,1).data));assert.notDeepEqual(pixels.slice(0,3),[255,255,255]);
 await page.getByRole('button',{name:'Next PDF page'}).click();await expect(page.locator('.a-pdf-text')).toContainText('PDF page 2 selectable text');
 const selected=await page.locator('.a-pdf-text').evaluate(element=>{const range=document.createRange();range.selectNodeContents(element);getSelection().removeAllRanges();getSelection().addRange(range);return getSelection().toString()});assert.match(selected,/PDF page 2 selectable text/);
 await page.getByRole('button',{name:'Zoom PDF in',exact:true}).click();await expect(page.locator('.a-pdf-toolbar')).toContainText('125%');
 await page.getByRole('button',{name:'Fit width',exact:true}).click();
 await writeFile(workspace+'/Report.pdf','changed source');
 const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 const download=await page.request.get(url+`/api/canvas/${identity}/download?version=1`,{headers:{'X-Amplifier-Client':clientId}});
 assert.equal(download.headers()['content-type'],'application/pdf');assert.match(download.headers()['content-disposition'],/Report.pdf/);assert.equal((await download.body()).toString(),original);
 await page.reload();await expect(page.locator('.a-pdf-text')).toContainText('PDF page 1 selectable text');
 await mkdir(out,{recursive:true});await page.screenshot({path:out+'/multipage.png'});
 await action('canvas.show',{kind:'auto',path:'Single.pdf'});await expect(page.getByRole('button',{name:'Next PDF page'})).toBeDisabled();
 await action('canvas.show',{kind:'auto',path:'Damaged.pdf'});await expect(page.getByRole('alert').filter({hasText:'This PDF could not be opened'})).toBeVisible();await expect(page.getByRole('button',{name:'Download original PDF'})).toBeVisible();
 execFileSync(python,['-c',"from pypdf import PdfWriter; import sys; writer=PdfWriter(); writer.add_blank_page(width=500,height=400); writer.encrypt('secret'); writer.write(sys.argv[1])",workspace+'/Locked.pdf']);
 await action('canvas.show',{kind:'auto',path:'Locked.pdf'});await expect(page.getByRole('alert').filter({hasText:'password-protected'})).toBeVisible();await expect(page.getByRole('button',{name:'Download original PDF'})).toBeVisible();
 assert.deepEqual(errors,[]);console.log(JSON.stringify({status:'passed',checks:['multi/single page','raster layout','selectable text','page controls','zoom/fit','original download after source changes','reload','damaged fallback']}));
}finally{try{await browser?.close()}finally{if(fixture.exitCode===null){const done=new Promise(resolve=>fixture.once('exit',resolve));fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);await done.finally(()=>clearTimeout(timer))}}}
