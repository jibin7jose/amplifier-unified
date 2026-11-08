// Labels describe existing mutually exclusive chat states, not new work items.
export const workGroups={
 attention:{title:'Needs you',empty:'Nothing needs your attention.',verb:'Review request',meaning:'chats needing your attention',resource:'workStatusAttention'},
 unread:{title:'Ready for you',empty:'No unread responses.',verb:'Read response',meaning:'chats with unread responses',resource:'workStatusUnread'},
 working:{title:'Working',empty:'Nothing running right now.',verb:'View progress',meaning:'chats currently running',resource:'workStatusWorking'},
 waiting:{title:'Waiting',empty:'No verified chat dependencies waiting.',verb:'View waiting chats',meaning:'chats waiting for another chat',resource:'workStatusWaiting'},
 paused:{title:'Paused',empty:'No paused or stopped chats.',verb:'Open chat',meaning:'paused or stopped chats',resource:'workStatusPaused'},
 idle:{title:'Pick up again',empty:'Your saved chats will appear here.',verb:'Continue chat',meaning:'other saved chats',resource:'workStatusIdle'}
};

export function workAction(kind,activity){
 if(kind!=='attention')return workGroups[kind].verb;
 if(activity.label==='Answer requested')return 'Review question';
 if(activity.label==='Approval requested')return 'Review approval';
 if(activity.label==='Work blocked')return 'See what’s blocked';
 return 'See what needs you';
}
