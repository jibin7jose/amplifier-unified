// Disposable preview pages, never a transcript store. Cursor and rows are one
// unit: an older cursor may not be combined with a newer source revision.
function valid(result){
 if(!Array.isArray(result?.page?.items)||!result.source?.available||!result.page.revision)throw Error('Invalid source snapshot.');
 const ids=result.page.items.map(row=>row.id);
 if(ids.some(id=>!id)||new Set(ids).size!==ids.length)throw Error('Source message identities are not unique.');
 return result;
}
export function prependPeerPage(old,result){
 valid(result);
 if(old.page.revision!==result.page.revision)throw Error('Source changed while paging. Refresh before loading earlier messages.');
 const messages=[...result.page.items,...old.messages];
 if(new Set(messages.map(row=>row.id)).size!==messages.length)throw Error('Source page overlaps the retained range. Refresh before paging.');
 return {...old,messages,page:{...result.page,unavailableRanges:[...(result.page.unavailableRanges||[]),...(old.page.unavailableRanges||[])]}};
}
export async function refreshPeerPages(old,read){
 const latest=valid(await read());
 if(!old?.messages.length)return {...latest,messages:latest.page.items};
 // Same source revision: the oldest boundary is still valid. Refresh must not
 // replace it with latest.before or move already expanded original components.
 if(old.page.revision===latest.page.revision)return {...old,source:latest.source,capturedAt:latest.capturedAt};
 // New source revision invalidates every old cursor. Re-read only the retained
 // range (at most five bounded pages / 100 rows), then commit it atomically.
 // On a gap, rewrite, race or budget limit, the caller keeps the old snapshot.
 let next={...latest,messages:latest.page.items};
 const first=old.messages[0].id;
 for(let pages=1;!next.messages.some(row=>row.id===first);pages++){
  if(!next.page.hasEarlier||!next.page.before||pages>=5)throw Error('Refresh could not reconnect the retained range within five bounded pages. Close and reopen to start at the latest messages.');
  next=prependPeerPage(next,await read(next.page.before));
 }
 const oldIds=new Set(old.messages.map(row=>row.id)),retained=next.messages.filter(row=>oldIds.has(row.id)).map(row=>row.id);
 if(retained.length!==old.messages.length||retained.some((id,i)=>id!==old.messages[i].id))throw Error('The retained source range changed. Close and reopen to inspect its new order.');
 return next;
}