import React from 'react';
import './new-chat.css';

export function newChatSetup(state){
 const surface=state?.view?.workSurface||'chat';
 const id=surface==='workspace'?state?.view?.workWorkspaceId:surface==='chat'?state?.selectedWorkspaceId:null;
 const workspace=state?.workspaces?.find(row=>row.id===id)?.path||'';
 const draft=state?.view?.newSessionDraft;
 const path=draft?.workspace??workspace;
 return {title:'',workspace,bundle:'',selection:{},...draft,location:draft?.location||{kind:path?'workspace':'managed'}};
}

export function draftDefaultsKey(setup){
 return JSON.stringify([setup.workspace,setup.bundle||'',...(setup.location?.kind==='managed'?['managed']:[])]);
}

export function draftDefaults(state){
 const setup=newChatSetup(state);
 return state.draftDefaults?.[draftDefaultsKey(setup)]||{};
}

const starters=[
 {title:'Summarize a file',detail:'Find the main points and next steps.',prompt:'Summarize the file I attach. Highlight the main points, decisions, and next steps. If I have not attached a file yet, ask me for it.'},
 {title:'Draft an email',detail:'Turn your intent into a clear message.',prompt:'Help me draft an email. Ask me who it is for, what I want to say, and the tone I would like.'},
 {title:'Organize my notes',detail:'Make a clear plan from rough notes.',prompt:'Help me organize my notes into a summary and action list. Ask me to paste or attach my notes, and keep any missing owners or dates marked as unknown.'},
];

export function NewChatSetup({draft='',onChoose}){
 return <section className="a-new-chat-setup" aria-label="New chat"><img src="/branding/icons/amplifier-icon-128.png" alt=""/><h2>What shall we work on?</h2><p>Bring an idea, a question, or a file.</p>{!draft.trim()&&onChoose&&<><div className="a-chat-starters" aria-label="Ideas to get started">{starters.map(item=><button type="button" className="a-soft" key={item.title} onClick={()=>onChoose(item.prompt)}><strong>{item.title}</strong><span>{item.detail}</span></button>)}</div><p className="a-caption">Choose an idea, edit the message, then send when you’re ready.</p></>}</section>;
}
