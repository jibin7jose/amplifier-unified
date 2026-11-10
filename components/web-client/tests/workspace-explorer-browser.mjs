// Real discovery, actions, and persisted view state. No account or model calls.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/workspace_explorer_server.py',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';
fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const ready=new Promise((resolve,reject)=>{
 let output='';
 fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
 fixture.once('error',reject);
 fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${fixtureLog}`)));
});
let vite,browser,page;
try{
 const port=await ready,target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const api=(path,body)=>page.evaluate(async([path,body])=>{
  const headers={'X-Amplifier-Client':window.amplifier.getState().client.id};
  const response=await fetch(path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw Error(await response.text());return response.json();
 },[path,body]);
 const info=()=>api('/api/fixture/info');
 const agent=async(action,args)=>{
  const clientId=await page.evaluate(()=>window.amplifier.shellClientId);
  if(action==='view.update'){
   await api('/api/fixture/agent',{args:{action:'shell.view.update',args:{clientId,instanceId:'chats',patch:{navChatScope:'workspace',navWorkspaceList:true}}}});
   return api('/api/fixture/agent',{args:{action:'shell.view.update',args:{...args,clientId,instanceId:'workspaces',patch:{navWorkspaceMode:'folders',...args.patch}}}});
  }
  return api('/api/fixture/agent',{args:action==='workspace.select'?{action:'shell.command',args:{clientId,instanceId:'workspaces',action,args}}:{action,args}});
 };
 const row=path=>page.locator('.a-workspace-row').filter({has:page.getByRole('button',{name:'Open chats in '+path,exact:true})});
 const browse=path=>page.getByRole('button',{name:'Browse '+path,exact:true});
 const selected=()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const waitPath=path=>page.waitForFunction(path=>window.amplifier.getShellState()?.snapshots?.workspaces?.workspaceExplorer?.path===path,path);
 const showPath=async path=>{await agent('view.update',{patch:{navWorkspacePath:path,navWorkspaceFilter:'',navWorkspacePage:1}});await page.locator('.a-workspace-explorer').waitFor();await waitPath(path)};

 await page.goto(vite.resolvedUrls.local[0]);
 await page.locator('.a-workspace-explorer').waitFor();
 const initial=await info(),paths=initial.paths,first=initial.initialSession;
 assert.equal(await selected(),first);
 assert.equal(initial.directories.new,false);
 await showPath(paths.root);
 assert.deepEqual((await page.locator('.a-workspace-row').evaluateAll(rows=>rows.map(row=>row.dataset.workspacePath))).sort(),[paths.dev,paths.research].sort());
 // A branch has one action and never implicitly selects or starts a chat.
 assert.equal(await browse(paths.dev).count(),1);
 assert.equal(await page.getByRole('button',{name:'Open chats in '+paths.dev,exact:true}).count(),0);
 await browse(paths.dev).click();await waitPath(paths.dev);
 assert.equal(await selected(),first);
 const developmentRows=await page.locator('.a-workspace-row').evaluateAll(rows=>rows.map(row=>row.dataset.workspacePath));
 assert.deepEqual(developmentRows.sort(),[paths.mixed,paths.playgroundOne,paths.workerOnly,paths.empty].sort());
 assert.equal(await page.getByRole('checkbox',{name:/only folders/i}).count(),0);
 for(const key of ['missing','unresolved','unrelated'])assert.equal(developmentRows.includes(paths[key]),false,key+' must not enter the explorer');

 // Project roots with deeper workspaces offer separate selection and browsing.
 assert.equal(await row(paths.mixed).count(),1);
 assert.equal(await browse(paths.mixed).count(),1);
 assert.equal(await browse(paths.playgroundOne).count(),0,'a leaf root has no drill-in control');
 await row(paths.playgroundOne).getByRole('button',{name:'Open chats in '+paths.playgroundOne,exact:true}).click();
 await page.waitForFunction(path=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId)?.workspace===path,paths.playgroundOne);
 await page.getByRole('button',{name:'All workspaces',exact:true}).click();await showPath(paths.dev);
 await row(paths.mixed).getByRole('button',{name:'Open chats in '+paths.mixed,exact:true}).click();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,first);
 await page.getByRole('button',{name:'All workspaces',exact:true}).click();await showPath(paths.dev);await browse(paths.mixed).click();await waitPath(paths.mixed);
 assert.equal(await selected(),first);
 await browse(paths.mixed+'/extensions').click();await waitPath(paths.mixed+'/extensions');
 assert.equal(await browse(paths.nested).count(),0);
 assert.equal(await row(paths.nested).count(),1);
 const ancestorToggle=page.getByRole('button',{name:'Show ancestor folders',exact:true});
 assert.equal(await ancestorToggle.getAttribute('aria-expanded'),'false');
 await agent('view.update',{patch:{navWorkspaceAncestorsOpen:true}});
 await page.waitForFunction(()=>document.querySelector('.a-workspace-ancestors')?.dataset.open==='true');
 assert.equal(await ancestorToggle.getAttribute('aria-expanded'),'true');
 assert.equal(await browse(paths.root).isVisible(),true);
 await ancestorToggle.click();
 await page.waitForFunction(()=>window.amplifier.getShellState()?.snapshots?.workspaces?.view.navWorkspaceAncestorsOpen===false);
 assert.equal(await browse(paths.root).count(),0);
 await page.getByRole('button',{name:'Go to parent workspace folder',exact:true}).click();await waitPath(paths.mixed);
 assert.equal(await selected(),first);
 await page.screenshot({path:'/tmp/amplifier-workspace-explorer-desktop.png'});

 // The exact UI state is persisted and available through the agent bridge.
 const agentPath=await agent('shell.query',{clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId:'workspaces'});
 assert.equal(agentPath.result.workspaceExplorer.path,paths.mixed);
 assert.equal(agentPath.result.view.navWorkspacePath,paths.mixed);
 await page.reload();await page.locator('.a-workspace-explorer').waitFor();await waitPath(paths.mixed);
 assert.equal(await selected(),first);
 const nestedWorkspace=(await info()).state.workspaces.find(workspace=>workspace.path===paths.nested);
 await agent('workspace.select',{id:nestedWorkspace.id});
 await page.waitForFunction(id=>window.amplifier.getState().selectedWorkspaceId===id,nestedWorkspace.id);
 await api('/api/fixture/agent',{args:{action:'shell.view.update',args:{clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId:'chats',patch:{navWorkspaceList:false}}}});
 await page.locator('.a-workspace-chat-view .a-nav-chat').waitFor();
 assert.match(await page.locator('.a-workspace-chat-view .a-nav-chat').innerText(),/nested-chat/);

 // Search matches full paths across branches, including duplicate leaf names.
 await showPath(paths.root);
 const search=page.getByRole('searchbox',{name:'Filter workspaces',exact:true});
 await search.fill('*/playground');
 await expect.poll(()=>page.locator('.a-workspace-row').evaluateAll(rows=>rows.map(row=>row.dataset.workspacePath).sort())).toEqual([paths.playgroundOne,paths.playgroundTwo].sort());
 assert.equal(new Set(await page.locator('.a-workspace-result-path').allTextContents()).size,2,'duplicate leaf names retain distinct parent labels');
 for(const key of ['playgroundOne','playgroundTwo']){
  await row(paths[key]).getByRole('button',{name:/Details and actions/}).click();
  assert.ok((await page.locator('.a-navigation-flyout').innerText()).includes(paths[key]),'details expose the complete path');
  await page.keyboard.press('Escape');
 }
 await row(paths.playgroundTwo).getByRole('button',{name:'Open chats in '+paths.playgroundTwo,exact:true}).click();
 await page.waitForFunction(path=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId)?.workspace===path,paths.playgroundTwo);
 await page.getByRole('button',{name:'All workspaces',exact:true}).click();
 await agent('view.update',{patch:{navWorkspaceFilter:'*/playground',navWorkspacePage:1}});
 await expect.poll(()=>page.locator('.a-workspace-row').evaluateAll(rows=>rows.map(row=>row.dataset.workspacePath).sort())).toEqual([paths.playgroundOne,paths.playgroundTwo].sort());
 assert.equal(await search.inputValue(),'*/playground');
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight),'narrow viewport must not scroll the document');
 const overflow=await row(paths.playgroundTwo).evaluate(element=>({scroll:element.scrollWidth,width:element.clientWidth}));
 assert.ok(overflow.scroll<=overflow.width+1,'path labels stay within fixed-height narrow rows');
 await page.screenshot({path:'/tmp/amplifier-workspace-explorer-narrow.png'});
 await page.setViewportSize({width:1280,height:900});
 assert.deepEqual((await info()).runtimeStarts,[],'browsing saved workspaces never mounts their runtimes');

 // Attaching a folder only prepares the draft.
 // No chat, model, or existing-file mutation occurs until the first send.
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 const picker=page.getByRole('combobox',{name:'Workspace',exact:true});
 await picker.selectOption(':attach:');
 const form=page.getByRole('form',{name:'Use existing folder'});
 await form.getByRole('textbox',{name:/Folder on/}).fill(paths.existing);
 await form.getByRole('button',{name:'Use folder',exact:true}).click();
 await page.waitForFunction(path=>window.amplifier.getState().view.newSessionDraft.workspace===path,paths.existing);
 let current=await info();
 assert.equal(current.existingContents,'Existing workspace contents must stay intact.\n');
 assert.equal(current.state.sessions.filter(chat=>chat.workspace===paths.existing&&chat.sessionKind!=='worker').length,0);
 assert.equal(current.state.selectedSessionId,null);
 // Explicit agent workspace creation remains compatible and does not commit a chat.
 await agent('workspace.create',{path:paths.existing});
 current=await info();
 assert.equal(current.state.sessions.filter(chat=>chat.workspace===paths.existing&&chat.sessionKind!=='worker').length,0);
 assert.deepEqual(current.runtimeStarts,[]);
 assert.deepEqual(current.runtimeSends,[]);
 assert.deepEqual(errors,[]);
 console.log('Workspace explorer browser checks passed: real native discovery, registered empty folders and root-chat paths, split root/browse actions, leaf constraints, full-path wildcard search, narrow truncation, persisted agent/UI navigation, existing folder attachment, no model work.');
}catch(error){
 await page?.screenshot({path:'/tmp/amplifier-workspace-explorer-failure.png'}).catch(()=>{});
 if(fixtureLog)console.error(fixtureLog);
 throw error;
}finally{
 await browser?.close();await vite?.close();
 if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
