import {settingsSections} from '../src/settings-navigation.js';
// The menu's attention badge adds its own accessible unread-item label.
// Keep an anchored match so unrelated Settings actions cannot satisfy it.
export const settingsButton=scope=>scope.getByRole('button',{name:/^Settings(?: \d+ unread items)?$/});
const click=(_label,control,options)=>control.click(options);
// Instrumented callers supply every pointer/keyboard/touch activation. Their
// navigation must reach visible controls; it never falls back to dispatch.
export async function openSettingsDialog(page,{activate=click}={}){
 await page.waitForFunction(()=>window.amplifier?.getState());
 if(await page.evaluate(()=>['settings','appearance'].includes(window.amplifier.getState().view?.panel))){await page.locator('.a-settings-experience').waitFor({state:'visible'});return;}
 if(await page.locator('.a-settings-experience').isVisible())return;
 const settings=settingsButton(page);
 if(await settings.isVisible()){await activate('Open Settings from navigation footer',settings);return;}
 const options=page.getByRole('button',{name:'App options',exact:true});
 try{
  if(!await options.isVisible())await activate('Open navigation',page.getByRole('button',{name:'Open navigation',exact:true}),{timeout:1500});
  if(await settings.isVisible()){await activate('Open Settings from navigation footer',settings);return;}
  await activate('Open App options',options,{timeout:1500});
 }catch(error){
  // Ordinary fixture clicks may race onboarding. Instrumented callers own
  // their gesture/assertion failures even when that Settings surface arrives.
  if(activate===click&&await page.locator('.a-settings-experience').isVisible())return;
  throw error;
 }
 await activate('Open Settings from App options',settingsButton(page.getByRole('group',{name:'App options',exact:true})));
}
export async function openSettingsSection(page,id,options={}){
 if(!settingsSections.some(section=>section.id===id))throw new Error('Unknown settings test section: '+id);
 const activate=options.activate||click;
 await openSettingsDialog(page,options);
 const panel=page.locator('.a-settings-experience');
 await page.waitForFunction(()=>{const root=document.querySelector('.a-settings-experience');return !!root&&root.dataset.compact===String((root.closest('.a-overlay')||root).clientWidth<960)});
 for(let depth=0;await panel.getAttribute('data-compact')==='true'&&await panel.getAttribute('data-settings-index')!=='true';depth++){
  if(depth>=8)throw new Error('Settings Back did not reach index');
  const before=await panel.getAttribute('data-settings-route');
  await activate('Back toward Settings index',panel.locator('.a-settings-mobile-head button').first());
  await page.waitForFunction(previous=>document.querySelector('.a-settings-experience')?.dataset.settingsRoute!==previous,before);
 }
 await activate('Open '+id+' Settings',panel.locator(`[data-settings-section="${id}"]`));
 await panel.locator('.a-settings-page-content:not([hidden])').waitFor({state:'visible'});
}
export async function openSettingsPage(page,destination,options={}){
 const section=settingsSections.find(section=>section.pages.some(([id])=>id===destination));
 if(!section)throw new Error('Unknown settings test destination: '+destination);
 const activate=options.activate||click;
 await openSettingsSection(page,section.id,options);
 if(section.pages[0][0]!==destination){
  const link=page.locator(`.a-settings-page-content:not([hidden]) [data-settings-destination="${destination}"]`);
  const group=link.locator('xpath=ancestor::details');
  if(await group.count()&&!await link.isVisible())await activate('Expand '+destination+' Settings group',group.locator('summary'));
  if(await link.isVisible())await activate('Open '+destination+' Settings page',link);
  else if(options.activate)throw new Error('Settings destination has no visible control: '+destination);
  else await page.evaluate(destination=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsExpanded:[destination]}}),destination);
 }

 await page.locator(`.a-settings-experience[data-settings-page="${destination}"]`).waitFor();
}

export async function openPhoneNotificationSettings(page){
 if(!await page.locator('#notify-topic').isVisible())await page.getByText(/^(Set up|Manage) phone notifications$/).click();
 if(!await page.locator('#notify-token').isVisible())await page.getByText('Server & account',{exact:true}).click();
}
