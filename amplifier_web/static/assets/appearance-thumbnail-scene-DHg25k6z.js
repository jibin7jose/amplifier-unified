import{R as a}from"./client-5nj4ZH6s.js";import{d as o,s as i,e,u as r,f as p,r as s,g as l,h as d}from"./app-RgkFzoVQ.js";const m=`html,body,#root{margin:0;min-height:100%;width:100%}body{background:var(--boot-bg,#e8eeff)}button,input,textarea,select{font:inherit}button:disabled{cursor:not-allowed!important;opacity:.48}.boot{font:16px system-ui;padding:64px;color:var(--boot-ink,#263459)}.boot img{width:56px}.boot button{padding:10px 18px;margin-top:12px;border-radius:12px;border:1px solid #ccc;background:white}
.a-updates{margin-top:24px;padding-top:20px;border-top:1px solid var(--border,#dce3ef)}
.a-update-options{display:grid;gap:12px}.a-update-options label{display:flex;align-items:center;gap:10px;margin:0}.a-update-options input[type=checkbox]{width:18px;height:18px;flex-shrink:0}.a-update-list{padding:0;list-style:none;max-height:300px;overflow:auto}.a-update-list li{display:grid;gap:3px;padding:12px 0;border-bottom:1px solid var(--border,#dce3ef);overflow-wrap:anywhere}.a-update-list small,.a-update-list span{opacity:.75}.a-update-count{font-size:var(--a-text-base,14px);padding:3px 7px;border-radius:12px;background:#e0e7ff;color:#3730a3}.a-settings-dot{width:7px;height:7px;border-radius:50%;background:#6750e8;position:absolute;top:4px;right:4px}

.a-diagnostic-streams{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.55rem;margin:.8rem 0}.a-diagnostic-destinations{display:grid;gap:.5rem;margin:.8rem 0}

.a-diagnostic-streams .a-inline-checkbox{margin:0!important;min-height:44px;align-items:center}
`,c=`#amp-one .a-sidebar-section{margin:12px 0 20px;min-width:0}
#amp-one .a-sidebar-section-heading{display:flex;align-items:center;gap:6px;margin-bottom:6px}
#amp-one .a-sidebar-section-heading>button:first-child{display:flex;align-items:center;gap:7px;min-width:0;flex:1;padding:8px 6px;text-align:left;background:transparent;border:0;color:var(--a-muted);font-size:var(--a-text-sm,13px);font-weight:600}
#amp-one .a-sidebar-section-heading>button:first-child:hover{color:var(--a-ink);background:var(--a-soft)}
#amp-one .a-sidebar-section-heading small{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:400}
#amp-one .a-sidebar-section-heading svg{width:15px;height:15px;flex-shrink:0}
#amp-one .a-sidebar-section-heading svg.is-open{transform:rotate(90deg)}
#amp-one .a-sidebar-section-heading>.a-icon{width:28px;height:28px;flex-shrink:0}
#amp-one .a-sidebar-section [hidden]{display:none!important}
#amp-one .a-sidebar-filters{margin:4px 0 10px}
#amp-one .a-sidebar-filters>summary{padding:6px;color:var(--a-muted);cursor:pointer;font-size:var(--a-text-sm,13px)}
#amp-one .a-sidebar-filters[open]>summary{margin-bottom:10px}
#amp-one .a-sidebar-workspace-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:8px 6px 12px;color:var(--a-muted);font-size:var(--a-text-sm,13px)}
#amp-one .a-pinned-chats .a-reorder-grip,#amp-one .a-pinned-drag .a-reorder-grip{width:20px;height:32px;flex:0 0 20px;padding:2px;opacity:.55;margin-right:2px}
#amp-one .a-pinned-chats .a-reorder-grip svg,#amp-one .a-pinned-drag .a-reorder-grip svg{width:15px;height:18px}
#amp-one .a-pinned-chats .a-nav-chat:hover .a-reorder-grip,#amp-one .a-pinned-chats .a-reorder-grip:focus{opacity:1}
#amp-one .a-pinned-drag .a-nav-chat{margin:0;background:transparent}
#amp-one .a-pinned-drag .a-navigation-more{opacity:1;visibility:visible}
#amp-one .a-pinned-chats .a-workspace-row,#amp-one .a-pinned-drag .a-workspace-row{display:flex;align-items:center;min-width:0}
#amp-one .a-pinned-chats .a-workspace-select,#amp-one .a-pinned-drag .a-workspace-select{flex:1;min-width:0}

#amp-one .a-nav-chat-rename .a-nav-name-help{flex-basis:100%;color:var(--a-muted);white-space:normal}

/* Reclaim hidden action space without removing controls from keyboard order.
   Hover, focus and an open details panel restore the existing action widths.
   Detailed mode, narrow and touch navigation keep their existing targets. */
#amp-one[data-interface-detail="detailed"] .a-quiet-sidebar :is(.a-nav-chat,.a-workspace-row)>.a-navigation-more{opacity:1;visibility:visible}
@media(min-width:761px) and (hover:hover) and (pointer:fine){
 #amp-one:not([data-interface-detail="detailed"]) .a-quiet-sidebar :is(.a-nav-chat,.a-workspace-row):not(:hover,:focus-within,.is-peeking)>:is(.a-navigation-more,.a-workspace-new-chat){width:0;min-width:0;flex-basis:0;padding-inline:0;margin-inline:0;overflow:hidden;opacity:0}
}
.a-chat-shortcut{position:absolute;left:4px;top:50%;transform:translateY(-50%);z-index:2;display:grid;place-items:center;width:22px;height:24px;border:1px solid var(--a-line);border-radius:6px;background:var(--a-surface,#fff);color:var(--a-ink);box-shadow:0 1px 4px #0002;font:600 12px/1 system-ui;pointer-events:none}
`,g=`#amp-one .a-nav-slot .a-nav-chat:not(.a-nav-chat-rename),#amp-one .a-nav-slot .a-workspace-row{height:60px;min-height:60px;max-height:60px;gap:0}
#amp-one .a-nav-slot .a-nav-chat-select,#amp-one .a-nav-slot .a-workspace-row>.a-workspace-select{height:100%;gap:7px;min-width:0;padding:6px}
#amp-one .a-nav-slot button.a-nav-chat-select>span.a-nav-chat-label,#amp-one .a-nav-slot .a-workspace-label{min-width:0;flex:1;overflow:hidden;white-space:nowrap}
#amp-one .a-nav-slot .a-nav-chat-label>span,#amp-one .a-nav-slot .a-workspace-label>span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;line-height:21px}
#amp-one .a-nav-slot .a-nav-chat-label>.a-nav-chat-workspace,#amp-one .a-nav-slot .a-workspace-label>small{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;overflow-wrap:normal;margin-top:1px;line-height:18px;font-size:var(--a-text-xs,12px);color:var(--a-muted)}
#amp-one .a-nav-slot .a-navigation-more{width:26px;min-width:26px;height:32px;opacity:0;flex-shrink:0;margin-right:2px}
#amp-one .a-nav-slot .a-navigation-more svg{width:15px;height:15px}
#amp-one .a-nav-slot .a-workspace-new-chat{width:28px;min-width:28px;height:32px;flex:0 0 28px;padding:4px;opacity:0}
#amp-one .a-nav-slot .a-workspace-new-chat svg{width:16px;height:16px}
#amp-one .a-workspace-select>.a-workspace-pin-mark{width:12px;height:12px;flex:0 0 12px;color:var(--a-muted)}
#amp-one .a-nav-slot .a-workspace-row:hover>.a-workspace-new-chat,#amp-one .a-nav-slot .a-workspace-row:focus-within>.a-workspace-new-chat,#amp-one .a-nav-slot .a-workspace-row.is-peeking>.a-workspace-new-chat{opacity:1}
#amp-one .a-nav-slot .a-nav-chat:hover .a-navigation-more,#amp-one .a-nav-slot .a-workspace-row:hover .a-navigation-more,#amp-one .a-nav-slot .is-peeking>.a-navigation-more,#amp-one .a-nav-slot .a-nav-chat:focus-within .a-navigation-more,#amp-one .a-nav-slot .a-workspace-row:focus-within .a-navigation-more{opacity:1}
#amp-one .a-nav-slot .a-nav-chat.is-peeking,#amp-one .a-nav-slot .a-workspace-row.is-peeking{background:var(--a-soft)}
#amp-one .a-nav-slot .a-nav-chat.is-selected,#amp-one .a-nav-slot .a-workspace-row.is-selected{background:var(--a-tint)}
#amp-one .a-navigation-status{display:inline-flex;align-items:center;justify-content:center;flex:0 0 17px;width:17px;height:19px;color:var(--a-muted)}
#amp-one .a-navigation-status svg{width:15px;height:15px}
#amp-one .a-navigation-status[data-kind="working"],#amp-one .a-navigation-status[data-kind="unread"]{color:var(--a-accent)}
#amp-one .a-navigation-status[data-kind="attention"]{color:var(--a-danger)}
#amp-one .a-navigation-unread{width:6px;height:6px;border-radius:50%;background:currentColor}
#amp-one .a-nav-slot .a-navigation-age{flex:0 0 26px;width:26px;text-align:right;font-size:var(--a-text-xs,12px);color:var(--a-muted);font-variant-numeric:tabular-nums;white-space:nowrap}
#amp-one .a-navigation-flyout{position:fixed;z-index:60;width:min(370px,calc(100vw - 20px));max-height:calc(100dvh - 20px);overflow:auto;background:var(--a-surface);color:var(--a-ink);border:1px solid var(--a-line);box-shadow:0 12px 40px #0002;border-radius:13px;padding:18px;text-align:left;white-space:normal;font-size:var(--a-text-base,14px)}
#amp-one .a-navigation-detail-heading{display:flex;align-items:center;gap:6px;font-size:var(--a-text-xs,12px);color:var(--a-muted);text-transform:uppercase;letter-spacing:.06em}
#amp-one .a-navigation-detail-heading svg{width:14px;height:14px}
#amp-one .a-navigation-flyout h3{font-size:var(--a-text-lg,16px);font-weight:600;line-height:1.5;margin:9px 0 14px;overflow-wrap:anywhere}
#amp-one .a-navigation-flyout dl{display:grid;grid-template-columns:86px minmax(0,1fr);gap:9px;margin:14px 0;font-size:var(--a-text-sm,13px)}
#amp-one .a-navigation-flyout dt{color:var(--a-muted)}
#amp-one .a-navigation-flyout dd{margin:0;min-width:0;overflow-wrap:anywhere}
#amp-one .a-navigation-detail-status{display:flex;gap:8px;align-items:center;padding:10px;background:var(--a-soft);border-radius:8px;font-size:var(--a-text-base,14px)}
#amp-one .a-navigation-copy{border-top:1px solid var(--a-line);padding-top:9px;margin-top:12px}
#amp-one .a-navigation-copy>div{display:flex;align-items:center;justify-content:space-between;color:var(--a-muted);font-size:var(--a-text-xs,12px);text-transform:uppercase;letter-spacing:.04em}
#amp-one .a-navigation-copy .a-icon{width:26px;height:26px}
#amp-one .a-navigation-copy svg{width:13px;height:13px}
#amp-one .a-navigation-copy code{display:block;font:var(--a-text-sm,13px)/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;white-space:normal;user-select:text;background:transparent;padding:0}
#amp-one .a-navigation-copy small{display:block;font-size:var(--a-text-xs,12px);color:var(--a-muted);margin-top:4px}
#amp-one .a-navigation-actions{display:flex;flex-wrap:wrap;gap:4px;padding-top:12px;margin-top:14px;border-top:1px solid var(--a-line)}
#amp-one .a-navigation-actions>button{padding:7px 6px;font-size:var(--a-text-sm,13px);gap:5px;border-radius:6px}
#amp-one .a-navigation-actions>button:hover{background:var(--a-soft)}
#amp-one .a-navigation-actions svg{width:13px;height:13px}
#amp-one .a-navigation-flyout form{margin-top:12px}
#amp-one .a-navigation-flyout input{min-width:0;width:100%;border:1px solid var(--a-line);border-radius:7px;padding:8px;background:var(--a-surface);color:var(--a-ink)}
#amp-one .a-navigation-flyout .a-nav-chat-rename{max-height:none;height:auto}
#amp-one .a-navigation-flyout .a-nav-chat-rename input{flex-basis:100%}
#amp-one .a-nav-slot .a-navigation-back{color:var(--a-muted);gap:5px;font-size:var(--a-text-sm,13px);padding:6px 3px}
#amp-one .a-nav-slot .a-navigation-back svg{width:13px;height:13px}
#amp-one .a-nav-slot .a-navigation-workspace{display:flex;gap:7px;align-items:center;padding:7px 4px}
#amp-one .a-nav-slot .a-navigation-workspace>svg{width:18px;height:18px;color:var(--a-accent);flex-shrink:0}
#amp-one .a-nav-slot .a-navigation-workspace>div{min-width:0;flex:1}
#amp-one .a-nav-slot .a-navigation-workspace>.a-navigation-more{opacity:1}
#amp-one .a-nav-slot .a-navigation-workspace strong,#amp-one .a-nav-slot .a-navigation-workspace small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#amp-one .a-nav-slot .a-navigation-workspace strong{font-size:var(--a-text-base,14px)}
#amp-one .a-nav-slot .a-navigation-workspace small{font-size:var(--a-text-xs,12px);color:var(--a-muted)}
#amp-one .a-nav-slot .a-navigation-filters,#amp-one .a-nav-slot .a-workspace-modes{display:flex;align-items:center;gap:5px;margin:5px 0}
#amp-one .a-nav-slot .a-navigation-filters button,#amp-one .a-nav-slot .a-workspace-modes button{padding:5px 6px;gap:4px;font-size:var(--a-text-xs,12px);border-radius:6px;color:var(--a-muted)}
#amp-one .a-nav-slot .a-navigation-filters button[aria-pressed="true"],#amp-one .a-nav-slot .a-workspace-modes button[aria-pressed="true"]{background:var(--a-tint);color:var(--a-accent)}
#amp-one .a-nav-slot .a-navigation-filters svg{width:12px;height:12px}
#amp-one .a-nav-slot .a-workspace-modes .a-icon{margin-left:auto}
#amp-one .a-nav-slot .a-workspace-explorer{border-bottom:0;padding-bottom:0;margin-bottom:0}
#amp-one .a-nav-slot .a-workspace-breadcrumbs{flex-wrap:nowrap;overflow:visible}
#amp-one .a-nav-slot .a-workspace-breadcrumbs>button{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;overflow-wrap:normal}
#amp-one .a-nav-slot .a-workspace-ancestors[data-open="true"]{flex:0 0 auto}
#amp-one .a-nav-slot .a-workspace-ancestors>div{position:absolute;top:100%;left:0;z-index:4;width:220px;max-width:calc(100vw - 70px);max-height:260px;overflow:auto;box-shadow:0 8px 24px #0002}
#amp-one[data-density="compact"] .a-nav-slot .a-nav-chat:not(.a-nav-chat-rename),#amp-one[data-density="compact"] .a-nav-slot .a-workspace-row{height:52px;min-height:52px;max-height:52px}
@media(hover:none),(pointer:coarse){#amp-one .a-nav-slot .a-navigation-more{opacity:1;width:40px;min-width:40px;height:44px}#amp-one .a-navigation-flyout button{min-height:44px;min-width:36px}#amp-one .a-nav-slot .a-navigation-filters button,#amp-one .a-nav-slot .a-workspace-modes button,#amp-one .a-nav-slot .a-navigation-back{min-height:44px}#amp-one .a-navigation-flyout input{font-size:16px}}
@media(hover:none),(pointer:coarse){#amp-one .a-nav-slot .a-workspace-new-chat{opacity:1;width:44px;min-width:44px;height:44px;flex-basis:44px}}

#amp-one .a-navigation-status[data-attention="blocked"],#amp-one .a-navigation-status[data-attention="decision"]{color:var(--a-warning,#b78020)}
`,x=`/* Preserve source soft line breaks in incoming user/peer Markdown without
   rewriting text, interpreting literal backslash-n, or changing copy bytes. */
#amp-one .a-markdown.a-markdown-user-content p,
#amp-one .a-markdown.a-markdown-user-content li{white-space:pre-wrap}
`,v=i.renderToStaticMarkup(a.createElement(a.Fragment,null,a.createElement("header",{"data-part":"header"},a.createElement("strong",{className:"a-brand"},"Amplifier"),a.createElement("span",{className:"a-caption"},"A place for ideas")),a.createElement("div",{className:"a-thumbnail-workspace"},a.createElement("aside",{"data-part":"navigation"},a.createElement("div",{className:"a-nav-rail"},a.createElement("div",{role:"group","aria-label":"Sidebar view"},a.createElement("button",{"aria-pressed":"true"},"Browse"),a.createElement("button",{"aria-pressed":"false"},"Status")),a.createElement("p",{className:"a-nav-eyebrow"},"Pinned"),a.createElement("div",{className:"a-nav-chat is-selected"},a.createElement("button",{className:"a-nav-chat-select"},a.createElement("span",{className:"a-nav-chat-title"},"A new idea"))),a.createElement("div",{className:"a-nav-chat"},a.createElement("button",{className:"a-nav-chat-select"},a.createElement("span",{className:"a-nav-chat-title"},"Weekend plans"))))),a.createElement("main",{"data-part":"conversation"},a.createElement("div",{className:"a-messages","data-part":"messages"},a.createElement("article",{className:"a-message a-user"},a.createElement("div",{className:"a-message-body"},a.createElement(e,{text:"Make room for a new idea.",userContent:!0}))),a.createElement("article",{className:"a-message a-assistant"},a.createElement("div",{className:"a-message-body"},a.createElement(e,{text:'## Start with something small\n\nA clear space for **your next idea**, with a useful [reference](#sample).\n\n```js\nconst next = "Make it real";\n```'})))),a.createElement("form",{"data-part":"composer"},a.createElement("div",{"data-part":"composer-surface"},a.createElement("div",{className:"a-markdown-composer",role:"textbox"},"Ask Amplifier…"),a.createElement("div",{className:"a-thumbnail-composer-actions"},a.createElement("span",null,"＋"),a.createElement("button",{className:"a-send","aria-label":"Send message"},"↑")))))))),h=[m,r,p,s,l,d,c,g,x].join(`
`),f=(n,t)=>o({css:n,scheme:t,hostCss:h,markup:v});export{f as renderAppearanceThumbnail};
