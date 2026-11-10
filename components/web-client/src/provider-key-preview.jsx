import React from 'react';

export function ProviderKeyPreview({credential}){
 const preview=credential?.preview;
 if(!preview)return null;
 const source=preview.source==='key-file'?'Private keys file (keys.env)':preview.source==='environment'?'Service environment':preview.source==='configuration'?'Provider configuration':'Provider managed';
 return <div className="a-provider-key-preview" data-part="provider-key-preview">
  <p>Service API key: <strong>{preview.status==='available'?<code>{preview.masked}</code>:preview.status==='missing'?'Not available':'Preview unavailable'}</strong></p>
  <p className="a-caption">{source}{preview.envVar&&<> · <code>{preview.envVar}</code></>}</p>
  <p className="a-caption">For new connections from this running service. Existing chats may still use an earlier key. A changed shell environment does not update a running service.</p>
 </div>;
}
