import {expect} from '@playwright/test';

// Keep the single model panel and its pending choices mounted. Only the bundle
// selector needs disclosure; connection, model and effort share the main panel.
export async function showComposerOption(page,section){
 if(!['model','effort','provider','bundle'].includes(section))throw Error('Unknown composer option: '+section);
 const dialog=page.getByRole('dialog',{name:'Model for this chat',exact:true});
 await expect(dialog).toBeVisible();
 if(section==='bundle'){
  const advanced=dialog.locator('details').filter({has:page.locator('summary',{hasText:/^Advanced$/})});
  if(await advanced.getAttribute('open')===null)await advanced.locator('summary').click();
 }
 // A loading model list or unsupported effort can show status instead of a
 // form control. The caller owns assertions about that section's capabilities.
 await expect(dialog.locator('[data-composer-page="'+section+'"]')).toBeVisible();
}