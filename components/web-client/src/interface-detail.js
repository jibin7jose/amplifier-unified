// Explicit collapse wins over the Everything default, including after a refresh.
export const detailOpen=(expanded,id,level)=>expanded.has(id)||(level==='detailed'&&!expanded.has('closed:'+id));
export function toggleDetail(expanded,id,level){const next=new Set(expanded),open=detailOpen(next,id,level);next.delete(id);next.delete('closed:'+id);next.add(open?'closed:'+id:id);return next;}
