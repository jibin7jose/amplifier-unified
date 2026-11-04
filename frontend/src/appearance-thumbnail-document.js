export const thumbnailScene={version:1,width:800,height:520};
const styleText=value=>String(value).replaceAll('<','\\3c ');
// Markup is the fixed, bundled synthetic scene, never user messages or metadata.
// An opaque sandbox plus CSP keeps appearance CSS away from the parent app and
// prevents external images/fonts/network requests, forms and executable scripts.
export function appearanceThumbnailDocument({css,hostCss,markup,scheme}){
 if(!['light','dark'].includes(scheme))throw Error('Choose a thumbnail color mode.');
 if(typeof css!=='string'||css.length>1000000)throw Error('Invalid thumbnail appearance.');
 return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'"><meta name="color-scheme" content="${scheme}"><style>${styleText(hostCss)}</style><style>${styleText(css)}</style><style>
html,body{margin:0;width:800px;height:520px;overflow:hidden}
#amp-one.a-thumbnail-scene{width:800px;height:520px;min-width:0;min-height:0;margin:0;display:flex;flex-direction:column}
#amp-one.a-thumbnail-scene [data-part=header]{flex:none;min-height:48px;padding:10px 16px;display:flex;align-items:center;gap:12px}
#amp-one.a-thumbnail-scene .a-thumbnail-workspace{display:grid;grid-template-columns:180px minmax(0,1fr);flex:1;min-height:0}
#amp-one.a-thumbnail-scene [data-part=navigation]{position:relative;width:auto;min-width:0;height:auto;overflow:hidden;padding:12px;display:block}
#amp-one.a-thumbnail-scene .a-nav-rail{position:static;width:auto;height:100%;min-height:0;overflow:hidden}
#amp-one.a-thumbnail-scene [data-part=conversation]{display:flex;flex-direction:column;min-width:0;min-height:0;margin:8px}
#amp-one.a-thumbnail-scene [data-part=messages]{position:relative;flex:1;min-height:0;padding:20px;overflow:hidden}
#amp-one.a-thumbnail-scene .a-message{margin:0 0 16px;max-width:100%}
#amp-one.a-thumbnail-scene .a-message.a-user{display:flex;justify-content:flex-end}
#amp-one.a-thumbnail-scene [data-part=composer]{position:static;flex:none;margin:0 18px 16px}
#amp-one.a-thumbnail-scene [data-part=composer-surface]{padding:12px;display:grid;gap:10px}
#amp-one.a-thumbnail-scene .a-thumbnail-composer-actions{display:flex;justify-content:space-between;align-items:center}
#amp-one.a-thumbnail-scene *,#amp-one.a-thumbnail-scene *::before,#amp-one.a-thumbnail-scene *::after{animation:none!important;transition:none!important;caret-color:transparent!important}
</style></head><body><div id="amp-one" class="a-chat-shell a-approachable-shell a-thumbnail-scene" data-theme-scheme="${scheme}" data-decorations="on" style="color-scheme:${scheme}" inert data-thumbnail-scene="${thumbnailScene.version}">${markup}</div></body></html>`;
}