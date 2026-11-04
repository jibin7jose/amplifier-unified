import test from 'node:test';
import assert from 'node:assert/strict';
import {appearanceThumbnailDocument,thumbnailScene} from '../src/appearance-thumbnail-document.js';
const make=(css='',scheme='light')=>appearanceThumbnailDocument({css,scheme,hostCss:'#amp-one{color:black}',markup:'<main>Synthetic specimen</main>'});
test('thumbnail generation is deterministic, scheme-specific and versioned',()=>{
 assert.equal(make('a{}'),make('a{}'));assert.notEqual(make('a{}'),make('a{}','dark'));
 assert.match(make('a{}','dark'),/data-theme-scheme="dark"/);
 assert.match(make(),new RegExp(`data-thumbnail-scene="${thumbnailScene.version}"`));
 assert.match(make(),/inert/);assert.match(make(),/animation:none!important/);
});
test('CSS cannot escape the style element or inject script/markup',()=>{
 const html=make('p::before{content:"</style><script>bad()</script>"}');
 assert.doesNotMatch(html,/<script>/);assert.match(html,/\\3c \/style>/);
 assert.equal((html.match(/<style>/g)||[]).length,3);
});
test('document denies network, execution, form submission and base changes',()=>{
 const html=make();
 for(const text of ["default-src 'none'","img-src data:","font-src data:","base-uri 'none'","form-action 'none'"])
  assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/script-src|https?:\/\//);
});
test('unknown modes and oversized/non-string CSS fail closed',()=>{
 assert.throws(()=>make('','system'),/color mode/);
 assert.throws(()=>make(null),/Invalid/);
 assert.throws(()=>make('x'.repeat(1000001)),/Invalid/);
});