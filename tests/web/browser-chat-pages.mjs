// Feature fixtures exercise their preselected chat/panel. Fresh web clients now
// arrive at Home, so establish that surface explicitly without selecting a chat,
// clearing a draft, or dismissing the fixture's panel. Home arrival has its own
// acceptance coverage; these scenarios retain their original feature assertions.
// Their normal desktop fixture includes pinned navigation, as before Home arrival.
// A scenario can opt out explicitly when testing a different navigation state.
export async function showFixtureConversation(page,{navPinned=true}={}){
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 await page.evaluate(async navPinned=>{
  const selected=window.amplifier.getState().selectedSessionId;
  const result=await window.amplifier.dispatch('view.update',{patch:{workSurface:'chat',...(navPinned===undefined?{}:{navPinned})}});
  if(!result.accepted)throw Error('Could not open the fixture conversation surface');
  if(window.amplifier.getState().selectedSessionId!==selected)throw Error('Fixture selection changed');
 },navPinned);
}

// Enter the selected chat's current public controls, not a removed Settings page.
export async function openChatAdvancedPage(page,title){
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 // The fixture's persisted panel can arrive after the document and toolbar.
 // Wait for that accepted state to mount before inspecting or dismissing it.
 if(await page.evaluate(()=>!!window.amplifier.getState().view?.panel))await page.locator('[data-part="dialog"]:visible').waitFor();
 const current=page.getByRole('dialog',{name:title,exact:true});
 if(await current.isVisible())return;
 // Sidebar hover cards and composer popovers are also dialogs, but do not own
 // the main panel's Close button. Only dismiss the actual mounted panel.
 const dialog=page.locator('[data-part="dialog"]:visible');
 if(await dialog.count())await dialog.getByRole('button',{name:/^Close (panel|settings)$/i}).click();
 // Settings can be opened from Home. Advanced controls belong to the selected
 // conversation; enter it without changing selection or sidebar preference.
 await showFixtureConversation(page,{navPinned:await page.evaluate(()=>window.amplifier.getState().view.navPinned)});
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Advanced',exact:true}).click();
 await page.getByRole('navigation',{name:'Advanced chat options'}).getByRole('button',{name:new RegExp('^'+title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))}).click();
 await current.waitFor({state:'visible'});
}
