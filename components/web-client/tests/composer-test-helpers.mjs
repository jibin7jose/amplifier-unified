import {expect} from '@playwright/test';

// Drafts are Markdown; contenteditable textContent intentionally omits its syntax.
export const readComposerDraft=locator=>locator.evaluate(()=>window.amplifier.getState().view.draft||'');
expect.extend({
 async toHaveDraft(locator,expected,options={}){
  let error;
  try{
   const assertion=expect.poll(()=>readComposerDraft(locator),options);
   const target=this.isNot?assertion.not:assertion;
   if(expected instanceof RegExp)await target.toMatch(expected);else await target.toBe(expected);
  }catch(failure){error=failure;}
  return {pass:this.isNot?!!error:!error,message:()=>error?.message||'Draft matched',name:'toHaveDraft',expected};
 },
});

export async function clearComposer(locator){await locator.press('ControlOrMeta+a');await locator.press('Backspace');await expect(locator).toHaveDraft('');}
