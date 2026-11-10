import test from 'node:test';
import assert from 'node:assert/strict';
import {actionContent,actionHeading,contentUnavailable,diffRows,cleanSummary} from '../src/execution-content.js';
const node=(label,input,output,phase='completed')=>({label,input:JSON.stringify(input),output:JSON.stringify(output),phase});
test('stopped tool keeps retained output without presenting it as successful completion',()=>{
 const action=actionContent(node('bash',{command:'bounded-effect'},{stdout:'partial result'},'stopped'));
 assert.equal(action.failed,true);assert.equal(action.done,false);assert.equal(action.running,false);
 assert.equal(action.phase,'stopped');assert.equal(action.out.stdout,'partial result');
});
test('bash keeps real command, stdout, stderr and exit status including failure',()=>{
 const a=actionContent(node('bash',{command:'python -m pytest -q'},{success:false,output:{stdout:'collected 2 items\n1 failed',stderr:'trace',returncode:1}}));
 assert.equal(a.kind,'command');assert.equal(a.title,'Run');assert.equal(a.target,'python -m pytest -q');assert.equal(a.exit,1);assert.equal(a.out.stderr,'trace');assert.match(a.preview,/1 failed · exit 1/);
});
test('read, app and todo preserve actual content instead of boilerplate',()=>{
 const read=actionContent(node('read_file',{file_path:'README.md',offset:2,limit:5},{success:true,output:{content:'2  actual text'}}));
 assert.equal(read.kind,'read');assert.equal(read.content,'2  actual text');assert.equal(read.target,'README.md');
 const todo=actionContent(node('todo',{todos:[{content:'Inspect',status:'completed'},{content:'Review',status:'pending'}]},{success:true,output:{completed:1,count:2}}));
 assert.equal(todo.kind,'tasks');assert.equal(todo.preview,'1 of 2 complete');assert.equal(todo.tasks[1].content,'Review');
 const app=actionContent(node('app_control',{action:'session.rename',title:'Review'},{success:true,output:{title:'Review'}}));
 assert.equal(app.kind,'app');assert.equal(app.target,'session.rename');assert.equal(app.args.title,'Review');
 assert.equal(actionContent(node('custom',{action:{wrong:'type'},operation:'inspect'},null)).target,'inspect');
});
test('patches have accurate counts and only report line numbers actually present',()=>{
 const a=actionContent(node('apply_patch',{patch:'*** Begin Patch\n*** Update File: code.py\n@@\n old\n-before\n+after\n+more\n*** End Patch'},{success:true}));
 assert.equal(actionContent(node('apply_patch',{patch:'*** Begin Patch\n*** Delete File: old.py\n*** End Patch'},{success:true})).title,'Deleted');
 assert.equal(a.target,'code.py');assert.equal(a.added,2);assert.equal(a.removed,1);
 assert.equal(a.rows.find(r=>r.type==='add').new,null);
 const rows=diffRows('--- a/file\n+++ b/file\n@@ -12,2 +15,2 @@\n context\n-old\n+new');
 assert.equal(diffRows('@@ -1 +1 @@\n--- heading\n+++ heading').filter(r=>r.type==='remove'||r.type==='add').length,2);
 assert.equal(rows.find(r=>r.type==='remove').old,13);assert.equal(rows.find(r=>r.type==='add').new,16);
 assert.equal(actionContent(node('apply_patch',{patch:'-old\n+new'},{success:false},'error')).title,'Edit');
});
test('unknown, missing, truncated, string and malicious-looking content remain literal',()=>{
 const legacy=actionContent({label:'bash',phase:'completed',summary:'Tool completed. Expand any delegated actions below for their progress and results.'});
 assert.equal(legacy.kind,'generic');assert.equal(legacy.preview,'');
 assert.equal(cleanSummary('A real report'),'A real report');
 assert.equal(actionContent({label:'fixture[read]',summary:'Completed fixture[read] · inspect',input:'{"path": "truncated'}).preview,'inspect');
 const unknown=actionContent(node('custom_tool',{thing:'<script>bad()</script>'},{success:true,output:'<img src=x onerror=bad()>'}));
 assert.equal(unknown.kind,'generic');assert.match(unknown.output,/onerror/);
 assert.equal(actionContent(node('bash',{command:'true'},'')).output,'');
});
test('collapsed command and code hints survive truncated input without replacing full payloads',()=>{
 const code='const result = await tools.search({query: "review"});\n'+ '/* long */'.repeat(300);
 const row={label:'tool_exec',phase:'completed',input:'{"code": "truncated',inputSummary:{code:code.slice(0,179)+'…'}};
 const heading=actionHeading(row);
 assert.equal(heading.kind,'code');assert.equal(heading.tool,'tool_exec');assert.ok(heading.target.length<=180);assert.match(heading.target,/tools.search/);
 assert.equal(actionContent(row).kind,'generic','A header hint is not the complete input');
 const full=actionContent(row,JSON.stringify({code}));assert.equal(full.code,code);
 assert.equal(actionHeading(row,JSON.stringify({code})).target.length,180);
 const command=actionHeading(node('bash',{command:'echo first\nprintf second'},null));
 assert.equal(command.target,'echo first printf second');
});
test('collapsed failures show the actual error and exit beside the command',()=>{
 const row=node('bash',{command:'python checks.py'},{success:false,output:{stdout:'noise'.repeat(1000),stderr:'trace\nAssertionError: missing result',returncode:7}},'error');
 const header=actionHeading(row);assert.equal(header.target,'python checks.py');assert.equal(header.detail,'AssertionError: missing result · Exit 7');
 assert.equal(actionHeading({...row,error:JSON.stringify({message:'worker timed out'})}).detail,'worker timed out · Exit 7');
 const compact={...row,output:'{"stdout": "truncated',outcomeSummary:{error:'AssertionError: missing result',exitCode:7}};
 assert.equal(actionHeading(compact).detail,header.detail);
 assert.equal(actionHeading(node('bash',{command:'true'},{stdout:'',returncode:0})).detail,'Exit 0');
 assert.equal(actionHeading({label:'tool_exec',phase:'error'}).detail,'','Do not invent a missing error');
});
test('bounded misses and ambiguous calls do not claim a request was never recorded',()=>{
 assert.match(contentUnavailable({contentAvailability:{status:'not-loaded'}},'Request'),/not in the loaded saved work/);
 assert.match(contentUnavailable({contentAvailability:{status:'ambiguous'}},'Request'),/could not be matched/);
 assert.doesNotMatch(contentUnavailable({},'Request'),/not recorded/);
});
test('UI actions keep their tool identity while app dispatch exposes its nested action',()=>{
 for(const tool of ['browser','computer','ui_use']){
  const action=actionContent(node(tool,{action:'click'},{success:true}));
  assert.equal(action.kind,'generic');assert.equal(action.title,tool);assert.equal(action.target,'click');
 }
 const app=actionContent(node('app_control',{operation:'dispatch',args:{action:'canvas.show'}},{success:true}));
 assert.equal(app.kind,'app');assert.equal(app.title,'App action');assert.equal(app.target,'dispatch → canvas.show');
});
