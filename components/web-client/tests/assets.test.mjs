import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
const root=fileURLToPath(new URL('../../amplifier_web/static/',import.meta.url));
test('packaged page loads its real stylesheet without JavaScript boot',()=>{
 const html=readFileSync(join(root,'index.html'),'utf8');
 const link=html.match(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>/);
 assert.ok(link,'stylesheet link is present in HTML');assert.ok(!link[0].includes('media="not all"'));
 const css=readFileSync(join(root,link[1]),'utf8');assert.ok(css.includes('#amp-one'));assert.ok(css.includes('.a-layout'));assert.ok(css.includes('radial-gradient'));assert.ok(!css.includes('@scope'));
 for(const [,path] of html.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g))assert.ok(existsSync(join(root,path)),`bundled ${path} exists`);
});
test('shared single-file Amplifier Unified theme matches the built-in theme',()=>{
 const css=readFileSync(join(root,'unified.amplifier.css'),'utf8');
 assert.equal(css,readFileSync(new URL('../src/unified.css',import.meta.url),'utf8'));
 assert.match(css,/data:image\/png;base64,/);assert.ok(css.includes('.a-dialog'));assert.ok(css.includes('.a-call'));
 assert.ok(!/@import\b/.test(css));
});
