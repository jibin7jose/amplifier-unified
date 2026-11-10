// A cursor proves where a requested page continues; unknown totals are not zero.
export function hasEarlierWindow(window){
 return window?.mode==='cursor'
  ?window.hasEarlier===true&&typeof window.before==='string'&&window.before.length>0
  :window?.offset>0;
}

export function returnedWindow(result){
 return result.mode==='cursor'?{mode:'cursor',before:result.before??null,
  hasEarlier:result.hasEarlier===true,total:null,offset:null,
  revision:result.revision,windowKey:result.windowKey,coverage:result.coverage,
  unavailableRanges:result.unavailableRanges}
  :{offset:result.offset,before:result.before};
}

export function validateHistoryPage(result,window){
 if(!Array.isArray(result?.items)||result.items.some(row=>!row||typeof row.id!=='string'))
  throw Error('The saved history page could not be read. Try again.');
 if(window?.mode==='cursor'&&(result.mode!=='cursor'||typeof result.hasEarlier!=='boolean'||
  result.hasEarlier&&(typeof result.before!=='string'||!result.before||result.before===window.before)))
  throw Error('The saved history page did not advance. Return to latest and try again.');
 return result;
}

// A bounded display page is ready independently of canonical execution hydration.
export function conversationHistoryPending(session,navigationPending=false,messages=session?.messages){
 if(navigationPending)return true;
 // Canonical history may hydrate again during live work. A readable page,
 // local outgoing message or streamed reply must stay mounted during that read.
 if(session?.historyError||messages?.length||session?.streaming)return false;
 if(session?.messageWindow?.mode==='cursor'||session?.messageWindow?.status==='ready')
  return !!session.historyLoading;
 return session?.historyLoaded===false||!!session?.historyLoading;
}
