// Same behavioral assertion against the candidate or an exact old module copied
// beside it in the isolated guest. Missing imports are errors, not a falsifier.
import assert from 'node:assert/strict';
const module=await import(process.env.GFM_COMPOSER_MODULE||'../src/markdown-composer.js');
const source='| Name | Status |\n| --- | --- |\n| **Editor** | Ready |';
const doc=module.parseComposer(source);
assert.equal(doc.firstChild.type.name,'table','GFM_TABLE_MUST_BE_RICH');
assert.ok(module.parseComposer(module.composerMarkdown(doc)).eq(doc),'GFM_TABLE_ROUNDTRIP');
const raw='A[^note]\n\n[^note]: retain this definition\n\n> [!NOTE]\n> raw alert';
assert.equal(module.composerMarkdown(module.parseComposer(raw)),raw,'GITHUB_EXTENSION_SOURCE_MUST_SURVIVE');
console.log('GFM table structure, roundtrip and extension-source falsifier passed');