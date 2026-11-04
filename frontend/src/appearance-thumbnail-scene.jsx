import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {Markdown} from './markdown';
import {appearanceThumbnailDocument} from './appearance-thumbnail-document';
import base from './base.css?raw';
import unified from './unified.css?raw';
import shell from './work-shell.css?raw';
import refresh from './main-refresh.css?raw';
import chat from './chat-presentation.css?raw';
import composer from './compact-composer.css?raw';
import navigation from './sidebar-navigation.css?raw';
import detail from './navigation-details.css?raw';
import markdown from './markdown-content.css?raw';

// The same specimen and native class/data-part contract for built-ins, imports
// and agent-authored appearances. No saved chat, private data or model request.
const markup=renderToStaticMarkup(<>
 <header data-part="header"><strong className="a-brand">Amplifier</strong><span className="a-caption">A place for ideas</span></header>
 <div className="a-thumbnail-workspace">
  <aside data-part="navigation"><div className="a-nav-rail">
   <div role="group" aria-label="Sidebar view"><button aria-pressed="true">Browse</button><button aria-pressed="false">Status</button></div>
   <p className="a-nav-eyebrow">Pinned</p>
   <div className="a-nav-chat is-selected"><button className="a-nav-chat-select"><span className="a-nav-chat-title">A new idea</span></button></div>
   <div className="a-nav-chat"><button className="a-nav-chat-select"><span className="a-nav-chat-title">Weekend plans</span></button></div>
  </div></aside>
  <main data-part="conversation">
   <div className="a-messages" data-part="messages">
    <article className="a-message a-user"><div className="a-message-body"><Markdown text="Make room for a new idea." userContent/></div></article>
    <article className="a-message a-assistant"><div className="a-message-body"><Markdown text={'## Start with something small\n\nA clear space for **your next idea**, with a useful [reference](#sample).\n\n```js\nconst next = "Make it real";\n```'}/></div></article>
   </div>
   <form data-part="composer"><div data-part="composer-surface">
    <div className="a-markdown-composer" role="textbox">Ask Amplifier…</div>
    <div className="a-thumbnail-composer-actions"><span>＋</span><button className="a-send" aria-label="Send message">↑</button></div>
   </div></form>
  </main>
 </div>
</>);
const hostCss=[base,unified,shell,refresh,chat,composer,navigation,detail,markdown].join('\n');
export const renderAppearanceThumbnail=(css,scheme)=>appearanceThumbnailDocument({css,scheme,hostCss,markup});