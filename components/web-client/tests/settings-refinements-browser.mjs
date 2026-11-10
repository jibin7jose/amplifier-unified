import {fixturePython} from './fixture-python.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const out=process.env.SETTINGS_SCREENSHOTS||'/tmp/settings-refinements';mkdirSync(out,{recursive:true});
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/settings_refinements_server.py',import.meta.url))]);
let browser,page,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),30000);fixture.stdout.on('data',data=>{const m=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0])}})});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:940},colorScheme:'dark',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page=await context.newPage();const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error('Browser error:',e.stack)});
 await page.goto(base);await openSettingsPage(page,'ai-connections');
 const root=page.locator('[data-part=ai-connections]');
 await root.getByRole('button',{name:/ChatGPT Subscription.*Account connected/}).click();
 await expect(root.getByText('Your ChatGPT account is connected.',{exact:true})).toBeVisible();
 await expect(root.getByRole('button',{name:'Sign in',exact:true})).toHaveCount(0);
 await page.screenshot({path:out+'/connection-dark.png'});
 await root.getByRole('button',{name:'Remove connection',exact:true}).click();
 await expect(root.getByRole('button',{name:/ChatGPT Subscription/})).toHaveCount(0);
 await openSettingsPage(page,'providers');
 await expect(page.locator('.a-collection-list').getByText('chatgpt-connected',{exact:true})).toHaveCount(0);
 const before=await page.locator('.a-collection-row').count();
 await page.getByRole('button',{name:'Remove',exact:true}).click();
 await expect(page.locator('.a-collection-row')).toHaveCount(before-1);
 await openSettingsPage(page,'ai-connections');
 await root.getByRole('button',{name:'Connect another service',exact:true}).click();
 await root.getByRole('button',{name:/OpenAI API Use an API/}).click();
 await expect(root.getByText('Use the key already on this host',{exact:true})).toBeVisible();
 await expect(root.getByLabel(/Use the key already on this host/)).toBeChecked();
 await root.getByLabel(/Use a different key/).check();await expect(root.getByLabel('API key',{exact:true})).toBeVisible();
 await root.getByLabel(/Use the key already on this host/).check();
 await expect(root.getByLabel('API key',{exact:true})).toHaveCount(0);
 await page.screenshot({path:out+'/environment-key-dark.png'});
 await root.getByRole('button',{name:'Save and check connection',exact:true}).click();
 await root.getByRole('button',{name:'Finish setup',exact:true}).waitFor();
 await root.getByLabel('Model',{exact:true}).selectOption('fixture-alternative');await root.getByRole('button',{name:'Finish setup',exact:true}).click();
 await expect(root.getByText('Model saved. Existing model rules are unchanged.',{exact:true})).toBeVisible();
 // Sidebar categories always start at their root, including the active category.
 await root.getByRole('button',{name:'Connect another service',exact:true}).click();await page.locator('[data-settings-section=ai]').click();
 await expect(root.getByRole('button',{name:'Connect another service',exact:true})).toBeVisible();
 await root.getByRole('button',{name:'Connect another service',exact:true}).click();await root.getByRole('button',{name:/GitHub Copilot Subscription/}).click();
 await expect(root.getByText('Use GitHub CLI sign-in',{exact:true})).toBeVisible();await expect(root.getByLabel(/Use GitHub CLI sign-in/)).toBeChecked();
 await page.screenshot({path:out+'/copilot-dark.png'});
 await root.getByRole('button',{name:'Save and check connection',exact:true}).click();await root.getByRole('button',{name:'Finish setup',exact:true}).waitFor();
 await openSettingsPage(page,'voice');await expect(page.getByLabel('Fallback voice model')).toHaveCount(0);
 await page.getByRole('button',{name:'Choose a voice',exact:true}).click();
 await expect(page.getByRole('radiogroup',{name:'Speaking voice'}).getByRole('radio')).toHaveCount(22);
 await page.getByRole('radio',{name:/Willow English/}).click();
 await expect(page.getByText('English · Irish influence · feminine',{exact:true})).toBeVisible();await page.screenshot({path:out+'/voice-dark.png'});
 await page.getByLabel('Voice model',{exact:true}).selectOption('gpt-realtime-2.1');
 await page.getByRole('button',{name:'Choose a voice',exact:true}).click();
 await expect(page.getByRole('radiogroup',{name:'Speaking voice'}).getByRole('radio')).toHaveCount(10);
 await expect(page.getByRole('radio',{name:/Marin Recommended/})).toBeChecked();
 await page.getByRole('button',{name:'Close voice list',exact:true}).click();
 await page.getByLabel('Let me interrupt by speaking').click();await expect(page.getByLabel('Let me interrupt by speaking')).not.toBeChecked();
 await openSettingsPage(page,'appearance');await page.getByRole('radio',{name:/Everything/}).check();
 await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail','detailed');
 await page.screenshot({path:out+'/appearance-dark.png'});
 await openSettingsPage(page,'notifications');await page.getByText('Set up phone notifications',{exact:true}).click();
 await page.getByRole('button',{name:'Create a name',exact:true}).click();await expect(page.getByLabel(/^Topic name/)).toHaveValue(/^amplifier-/);
 await page.getByText('Server & account',{exact:true}).click();await page.getByLabel(/^Access token/).fill('fixture-ntfy-token');
 await page.getByRole('button',{name:'Save phone connection',exact:true}).click();await expect(page.getByText('Notification preferences saved.',{exact:true})).toBeVisible();await expect(page.getByLabel(/^Access token/)).toHaveValue('');
 await page.screenshot({path:out+'/notifications-dark.png'});
 await openSettingsPage(page,'updates');
 const navCount=Number(await page.locator('[data-settings-section=updates] .a-attention-badge').innerText());
 const counts=await page.locator('.a-updates>details>summary .a-attention-badge').allTextContents();assert.equal(counts.reduce((n,v)=>n+Number(v),0),navCount);
 await page.screenshot({path:out+'/updates-dark.png'});
 let screenshots=6;
 for(const width of [390,1280])for(const scheme of ['light','dark']){
  await page.setViewportSize({width,height:940});await page.emulateMedia({colorScheme:scheme});
  for(const section of ['ai-connections','smart-tools','appearance','voice','notifications','privacy','updates','advanced']){
   await openSettingsPage(page,section);assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1),section+' overflow '+width);
   await page.screenshot({path:`${out}/${section}-${width}-${scheme}.png`});screenshots++;
  }
 }
 assert.deepEqual(errors,[]);const state=await page.evaluate(()=>window.amplifier.getState());assert.ok(!JSON.stringify(state).includes('fixture-environment-key'));assert.ok(!JSON.stringify(state).includes('fixture-github-cli-token'));assert.ok(!JSON.stringify(state).includes('fixture-ntfy-token'));
 const report={providerRemoval:true,accountStatus:true,categoryReset:true,environmentChoice:true,githubCliChoice:true,modelVoiceSelection:true,notificationSave:true,badgeTrail:true,screenshots,browserErrors:errors};writeFileSync(out+'/results.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){if(page)await page.screenshot({path:out+'/failure.png'});console.error(stderr.slice(-2000));throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
