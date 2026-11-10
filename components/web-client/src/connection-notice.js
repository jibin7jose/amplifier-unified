// Transport interruption is distinct from a server rejection. A reconnect does
// not prove that a mutation whose acknowledgement was lost actually succeeded.
export const RECONNECT_GRACE_MS=60*1000;
export function transportFailure(error){return error?.code==='transport_unavailable'}
export function retainUnconfirmed(previous,next){
 if(previous?.unconfirmed&&!next?.unconfirmed)return previous;
 return next;
}
export function afterReconnect(failure){return failure?.unconfirmed?failure:null}
export function connectionNotice({connected,updates={},failure,delayed=false}){
 const failed=!!updates.error||['error','interrupted'].includes(updates.phase)||updates.pendingRestart?.requestStatus==='rejected';
 const updating=!failed&&(!!updates.pendingRestart||updates.phase==='activating'&&!!updates.pendingReplacement);
 if(!connected||failure&&!failure.unconfirmed)return {
  error:delayed,
  title:delayed?'Unable to reconnect to Amplifier':updating?'Updating Amplifier—reconnecting…':'Reconnecting to Amplifier…',
  detail:delayed?'The connection has not recovered after one minute. Check the server status or retry. This tab will keep trying to reconnect.':updating?'The app is restarting to finish the update. This tab will reconnect automatically.':'This tab will reconnect automatically.',
  retry:delayed,
 };
 if(failure?.unconfirmed)return {title:'An action could not be confirmed',detail:'The connection was interrupted before confirmation arrived. Check the latest state before retrying; nothing was automatically repeated.',dismiss:true};
 return null;
}
