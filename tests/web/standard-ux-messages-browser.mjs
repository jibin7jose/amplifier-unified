import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('An owned AMPLIFIER_TEST_ARTIFACTS directory is required');
const evidence=new StandardUxEvidence(output,{baseline:'1d993e0bb32bc90b75fc66f56f1b9014c2d63f87',sourceReceipt:'execution-source.json',data:'synthetic-standard',runtime:'Existing empty-host synthetic provider',timings:'shared-DTU observations, not controlled performance'});
const variants=[
 {id:'desktop-pointer',width:1366,height:900,mode:'pointer'},
 {id:'desktop-keyboard',width:1366,height:900,mode:'keyboard'},
 {id:'narrow-touch',width:390,height:844,mode:'touch'},
 {id:'narrow-keyboard',width:390,height:844,mode:'keyboard'},
].filter(row=>!process.env.STANDARD_UX_VARIANTS||process.env.STANDARD_UX_VARIANTS.split(',').includes(row.id));
const launch={headless:true,args:['--no-zygote','--single-process','--disable-gpu']};
await mkdir(output,{recursive:true});
let failed=false;

for(const variant of variants){
 let browser,peerBrowser,page,fixture,fixtureLogs='';
 const errors=[],calls=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','pipe']});
  fixture.stderr.on('data',chunk=>{fixtureLogs+=chunk;});
  const url=await new Promise((resolve,reject)=>{
   let text='';const timer=setTimeout(()=>reject(Error('Synthetic message fixture did not become ready')),15000);
   fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Synthetic fixture exited '+code))});
   fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
  });
  browser=await chromium.launch(launch);
  page=await browser.newPage({viewport:{width:variant.width,height:variant.height},hasTouch:variant.mode==='touch',isMobile:variant.mode==='touch',permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
  evidence.page=page;
  const act=async(label,locator)=>evidence.action(label,async()=>{
   if(variant.mode==='keyboard'){await locator.focus();await locator.press('Enter')}
   else if(variant.mode==='touch')await locator.tap();
   else await locator.click();
  });
  const input=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
  const fixtureState=async()=>{const result=await page.request.get(url+'/fixture');evidence.assertion('Synthetic observation endpoint succeeds',result.status(),200);return result.json()};
  const state=()=>page.evaluate(()=>window.amplifier.getState());
  let sessionId,userId,assistantId;
  const probe=async(id,workflows,run)=>{
   const result=await evidence.probe(variant.id+'.'+id,workflows,async()=>{
    await run();return {variant,...await evidence.snapshot(page,variant.id+'.'+id,'#amp-one')};
   });
   if(result.status!=='passed')throw Error('Dependent message checks stopped after '+id);
  };
  await probe('create',['create.draft','composer.send'],async()=>{
   await evidence.action('Open clean synthetic app',()=>page.goto(url));await expect(input).toBeEditable();
   await evidence.action('Type first exact message',()=>input.fill('Message audit original — exact text.'));
   await act('Submit first message',page.getByRole('button',{name:'Send message',exact:true}));
   await expect(page.locator('.a-assistant')).toContainText('Synthetic first response');
   const current=await state();sessionId=current.selectedSessionId;
   userId=await page.locator('.a-user').first().getAttribute('data-message-id');
   assistantId=await page.locator('.a-assistant').first().getAttribute('data-message-id');
   evidence.assertion('Exactly one intended synthetic input',(await fixtureState()).sent.length,1);
   evidence.assertion('Canonical message identities are distinct',!!userId&&!!assistantId&&userId!==assistantId);
  });
  const user=()=>page.locator(`[data-message-id="${userId}"]`);
  const assistant=()=>page.locator(`[data-message-id="${assistantId}"]`);
  await probe('copy',['messages.copy'],async()=>{
   await act('Copy original message Markdown',user().getByRole('button',{name:'Copy message as Markdown',exact:true}));
   await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('Message audit original — exact text.');
   evidence.assertion('Clipboard has exact original bytes',await page.evaluate(()=>navigator.clipboard.readText()),'Message audit original — exact text.');
   evidence.assertion('Copy does not submit input',(await fixtureState()).sent.length,1);
  });
  await probe('reply-clear',['messages.reply','composer.draft-retention'],async()=>{
   await evidence.action('Type independent unsent draft',()=>input.fill('Keep this independent draft'));
   await act('Quote assistant message',assistant().getByRole('button',{name:'Reply to message',exact:true}));
   await expect(page.getByLabel('Quoted reply', {exact:true})).toContainText('Synthetic first response');
   const quote=(await state()).view.draftDocument.segments.find(row=>row.type==='quote').quote;
   evidence.assertion('Independent commentary kept',(await state()).view.draftDocument.segments.filter(row=>row.type==='text').map(row=>row.text).filter(Boolean),['Keep this independent draft']);
   evidence.assertion('Quote binds exact message',quote.messageId,assistantId);
   evidence.assertion('Quote binds exact chat',quote.sessionId,sessionId);
   await act('Remove quote',page.getByRole('button',{name:'Remove quote',exact:true}));
   await expect(page.getByLabel('Quoted reply',{exact:true})).toHaveCount(0);
   await expect(input).toHaveDraft('Keep this independent draft');
   evidence.assertion('Quote and clear never send',(await fixtureState()).sent.length,1);
  });
  await probe('reactions',['messages.reaction'],async()=>{
   // A second process preserves actual independent-browser storage on ARM.
   peerBrowser=await chromium.launch(launch);
   const peer=await peerBrowser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
   await evidence.action('Open second independent browser',()=>peer.goto(url));
   await peer.waitForFunction(()=>window.amplifier?.getState());
   await evidence.action('Bind observing browser to exact saved chat',()=>peer.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sessionId));
   await expect(peer.locator(`[data-message-id="${assistantId}"]`)).toBeVisible();
   const peerMessage=peer.locator(`[data-message-id="${assistantId}"]`);
   for(const label of ['Thumbs up','Heart','Laugh','Celebrate','Surprised','Sad','Thumbs down','Check']){
    await act('Open reactions for '+label,assistant().getByRole('button',{name:'React to message',exact:true}));
    const picker=assistant().getByRole('group',{name:'Choose reaction',exact:true});
    await expect(picker.getByRole('button')).toHaveCount(8);
    await act('Choose '+label,picker.getByRole('button',{name:label,exact:true}));
    await expect(assistant().getByRole('button',{name:'Remove '+label+' reaction',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(peerMessage.getByRole('button',{name:'Remove '+label+' reaction',exact:true})).toBeVisible();
    await act('Remove '+label,assistant().getByRole('button',{name:'Remove '+label+' reaction',exact:true}));
    await expect(peerMessage.getByRole('button',{name:'Remove '+label+' reaction',exact:true})).toHaveCount(0);
    evidence.assertion(label+' shared add/remove observed',true);
   }
   await peerBrowser.close();peerBrowser=null;
   await expect(input).toHaveDraft('Keep this independent draft');
   evidence.assertion('Reactions create no input',(await fixtureState()).sent.length,1);
  });
  await probe('edit-cancel',['messages.edit'],async()=>{
   await act('Open saved user editor',user().getByRole('button',{name:'Edit message',exact:true}));
   const editor=page.getByRole('textbox',{name:'Edit your message',exact:true});
   await expect(editor).toHaveValue('Message audit original — exact text.');
   await evidence.action('Change unsubmitted edit',()=>editor.fill('Must never replace the original'));
   await act('Cancel message edit',page.locator('.a-message-editor').getByRole('button',{name:'Cancel',exact:true}));
   await expect(editor).toHaveCount(0);await expect(user()).toContainText('Message audit original — exact text.');
   await expect(input).toHaveDraft('Keep this independent draft');
   evidence.assertion('Cancel admits no edit or fork',calls.filter(row=>['message.edit','session.fork'].includes(row.action)).length,0);
  });
  await probe('export-range',['messages.export'],async()=>{
   await act('Open Chat menu',page.getByRole('button',{name:'Chat',exact:true}));
   await act('Open Share or save',page.getByRole('button',{name:'Share or save',exact:true}));
   const dialog=page.getByRole('dialog',{name:'Share or save',exact:true});
   await evidence.action('Choose exact message range',()=>dialog.getByLabel('Export scope',{exact:true}).selectOption('range'));
   await evidence.action('Choose original starting message',()=>dialog.getByLabel('Starting message',{exact:true}).selectOption(userId));
   await evidence.action('Choose original ending message',()=>dialog.getByLabel('Ending message',{exact:true}).selectOption(userId));
   await act('Preview exact range',dialog.getByRole('button',{name:'Preview Markdown',exact:true}));
   const download=page.waitForEvent('download');
   await act('Download reviewed Markdown',dialog.getByRole('button',{name:'Download Markdown',exact:true}));
   const stream=await(await download).createReadStream(),chunks=[];let size=0;
   for await(const chunk of stream){size+=chunk.length;if(size>65536)throw Error('Synthetic range export exceeded expected bound');chunks.push(chunk)}
   const markdown=Buffer.concat(chunks).toString('utf8');
   evidence.assertion('Downloaded range includes original exact text',markdown.includes('Message audit original — exact text.'));
   evidence.assertion('Downloaded range excludes unselected response',markdown.includes('Synthetic first response'),false);
   evidence.assertion('Export does not submit input',(await fixtureState()).sent.length,1);
   await act('Close Share or save',page.getByRole('button',{name:'Close panel',exact:true}));
   await expect(input).toHaveDraft('Keep this independent draft');
  });
  await probe('final-privacy',['accessibility.privacy'],async()=>{
   evidence.assertion('No browser exceptions',errors,[]);
   evidence.assertion('Selected chat retained',(await state()).selectedSessionId,sessionId);
   evidence.assertion('One deliberate input only',(await fixtureState()).sent.length,1);
   evidence.assertion('No hidden model provisioning',(await fixtureState()).foundationCachePresent,false);
  });
 }catch(error){failed=true;console.error(variant.id,error.message);if(fixtureLogs)console.error(fixtureLogs)}
 finally{
  await peerBrowser?.close();await browser?.close();
  if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}
 }
}
await evidence.save();
if(failed||evidence.probes.some(row=>row.status==='failed'))process.exitCode=1;
