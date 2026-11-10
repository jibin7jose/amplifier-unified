import React,{useId} from 'react';

export const chatGPTMode=value=>!value||value==='legacy_codex'?'chatgpt_codex':value;
export const CHATGPT_USAGE='https://chatgpt.com/settings/usage';
export function ChatGPTAccount({account}){
 if(!account)return null;
 return <div className="a-ai-hint">
  <p>{account.authMode==='chatgpt_plan'?(account.planEnabled?'Using ChatGPT plan':account.connected?'Signed in. Permission to use your ChatGPT plan is still needed.':'Sign in to use your ChatGPT plan.'):'This connection uses ChatGPT Codex with device sign-in.'}{account.email&&<> · {account.email}</>}</p>
  {account.authMode==='chatgpt_plan'&&<a className="a-link" href={CHATGPT_USAGE} target="_blank" rel="noopener noreferrer">Manage usage</a>}
 </div>;
}
export function ChatGPTSignInChoice({mode,onChange,disabled=false}){
 const id=useId();
 return <>
  <label htmlFor={id}>ChatGPT connection</label>
  <select id={id} value={chatGPTMode(mode)} disabled={disabled} onChange={e=>onChange(e.target.value)}>
   <option value="chatgpt_codex">ChatGPT Codex · device sign-in</option>
   <option value="chatgpt_plan">ChatGPT plan · browser sign-in</option>
  </select>
  {mode==='chatgpt_plan'?<><p>Continue with ChatGPT to authorize Amplifier Unified to use your plan. Use a browser on the computer running Amplifier.</p>
   <details className="a-everyday-disclosure"><summary>Using a remote host, such as Spark?</summary><p>Browser sign-in returns to the computer where the browser is open. For a remote host, sign in locally with the ChatGPT provider, transfer the credentials over SSH, and import them on the remote host. Keep its host identity separate; let the remote host own subsequent refreshes.</p><p>ChatGPT Codex device sign-in is the default for Unified.</p><a href="https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms" target="_blank" rel="noopener noreferrer">Remote-host sign-in guide</a><p><a href="https://github.com/microsoft/amplifier-module-provider-openai-chatgpt/blob/main/docs/CHATGPT_PLAN_SIGN_IN.md#self-hosted-server" target="_blank" rel="noopener noreferrer">Amplifier setup commands</a></p></details>
  </>:<p>Sign in with a one-time code. This is the default ChatGPT connection for Amplifier Unified.</p>}
 </>;
}
