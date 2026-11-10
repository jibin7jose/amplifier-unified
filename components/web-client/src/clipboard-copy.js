// Invoke the browser write in the click's user gesture, before any await.
// There is deliberately no DOM/execCommand fallback or server round trip.
export function writeClipboardText(text,clipboard=globalThis.navigator?.clipboard){
 if(typeof text!=='string')throw new TypeError('Copy requires underlying text.');
 if(!clipboard?.writeText)throw Object.assign(new Error('Clipboard unavailable'),{name:'ClipboardUnavailable'});
 return clipboard.writeText(text);
}

export function clipboardNotice(error){
 return error?.name==='ClipboardUnavailable'
  ?'Clipboard is unavailable in this browser. Select the block text to copy it.'
  :error?.name==='NotAllowedError'||error?.name==='SecurityError'
   ?'Clipboard access was denied. Select the block text to copy it.'
   :'The block could not be copied. Select the block text to copy it.';
}