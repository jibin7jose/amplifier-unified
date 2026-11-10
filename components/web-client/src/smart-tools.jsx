import {SettingsActions} from './settings-layout';
import {Collection,CollectionRow} from './settings-collections';
import {ActivityRegion,useRefreshValue} from './activity-region';
import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {ArrowLeft, ArrowRight, Box, Cable, Check, ChevronRight, Code2, Download, ExternalLink, Globe, Play, Plus, RefreshCw, Settings2, Trash2, Unplug} from 'lucide-react';
import {useListFilter} from './list-filter.jsx';
import {PathField, ResultNotice} from './settings-ui';
import {BlockCopy} from './block-copy.js';
import './smart-tools.css';

const defaults = {page:'home', serverId:'', toolName:'', repository:'', ref:'', path:'', extras:'', connection:{id:'', name:'', transport:'stdio', command:'', args:'', env:'', cwd:'', url:'', headers:'', auth:'environment', installationId:''}, values:{}, raw:false, json:'{}'};
const working = operation => ['running','working','queued','pending','connecting','installing'].includes(operation?.status || operation?.phase);
const failed = operation => ['failed','error','interrupted'].includes(operation?.status || operation?.phase) || !!operation?.error || operation?.result?.isError === true;
const pretty = value => JSON.stringify(value, null, 2);
const list = value => Array.isArray(value) ? value : Object.values(value || {});
const title = name => name.replace(/[_.-]+/g, ' ').replace(/^./, character => character.toUpperCase());
const sourceOf = row => row?.repository || row?.repo || row?.url || '';
const phaseOf = operation => working(operation) ? 'working' : failed(operation) ? 'error' : 'ready';
const actionMessages = {'smartTools.catalog':'Catalog refreshed', 'smartTools.inspect':'Source inspected', 'smartTools.install':'Installation complete', 'smartTools.configure':'Connection saved', 'smartTools.connect':'Connected', 'smartTools.disconnect':'Disconnected', 'smartTools.remove':'Connection removed', 'smartTools.call':'Tool finished', 'smartTools.open':'Opened in canvas', 'smartTools.reconnect':'Reconnected', 'smartTools.schemas':'Tool fields loaded', 'smartTools.discover':'Tool definitions found', 'smartTools.uninstall':'Package uninstalled', 'smartTools.authStart':'Sign-in started', 'smartTools.authCancel':'Sign-in cancelled', 'smartTools.authForget':'Local sign-in removed', 'smartTools.accountAccept':'Account change accepted; reconnect explicitly'};

function SetupRequirements({value}) {
  if(typeof value==='string')return <p>{value}</p>;
  if(!Array.isArray(value)||value.some(row=>typeof row!=='string'&&typeof row?.name!=='string'))return <pre>{pretty(value)}</pre>;
  return <ul className="a-smart-setup-requirements">{value.map((row,index)=>typeof row==='string'?<li key={index}>{row}</li>:<li key={index}><strong>{row.name}</strong>{row.optional&&<small> · Optional</small>}{typeof row.purpose==='string'&&<p>{row.purpose}</p>}{typeof row.install==='string'&&(/^https?:\/\//.test(row.install)?<a href={row.install} target="_blank" rel="noreferrer">Setup instructions<ExternalLink/></a>:<p>{row.install}</p>)}{Object.keys(row).some(key=>!['name','optional','purpose','install'].includes(key))&&<details><summary>More requirement details</summary><pre>{pretty(row)}</pre></details>}</li>)}</ul>;
}

function useEditor(state, act) {
  const shared = state.view?.smartToolsEditor;
  const [editor, setEditor] = useState({...defaults, ...shared});
  const current = useRef(editor);
  // Browser Back updates the shared route before this retained editor. Commit
  // its list/detail layout before paint so scroll restoration sees that route's content.
  useLayoutEffect(() => {if (shared) {current.current = {...defaults, ...shared}; setEditor(current.current);}}, [shared]);
  const edit = patch => {const next = {...current.current, ...patch}; current.current = next; setEditor(next); act('view.update', {patch:{smartToolsEditor:next}});};
  return [editor, edit];
}

function operationFor(operations, action, target={}) {
  return [...operations].reverse().find(operation => operation.action === action && Object.entries(target).every(([key,value]) => {
    const found = operation.target?.[key] ?? operation.args?.[key] ?? (key === 'id' ? operation.serverId : key === 'name' ? operation.tool || operation.name : operation[key]);
    if (operation.target || operation.args) return (found ?? '') === (value ?? '');
    return found == null || found === value;
  }));
}

function OperationNotice({operation, busyMessage}) {
  if (!operation) return null;
  const savedView = operation.action === 'smartTools.open' && operation.status === 'completed' && operation.result?.activated === false;
  const message = operation.error || (operation.result?.isError ? 'The tool reported an error. See its result below.' : working(operation) ? busyMessage || 'Working…' : savedView ? 'Saved in Canvas. Your current view is unchanged.' : actionMessages[operation.action] || 'Finished');
  return <ResultNotice phase={phaseOf(operation)} message={typeof message === 'string' ? message : pretty(message)} detail={operation.status === 'interrupted' ? 'The previous app run ended before this request finished. Review the tool state before retrying.' : undefined}/>;
}

function ToolResult({operation}) {
  if (!operation || working(operation) || operation.result == null) return null;
  const result = operation.result;
  const stored = !!(result && typeof result === 'object' && result.$resource);
  const preview = stored ? result.summary ?? result.preview ?? null : result;
  const output = typeof preview === 'string' ? preview : preview == null ? '' : pretty(preview);
  const shown = output.slice(0,24000), truncated = stored || output.length > shown.length || result?.truncated === true || result?.partial === true;
  return <div className="a-smart-result"><h4>Result</h4>{shown && <BlockCopy text={shown} identity={operation.id} label={truncated ? 'Copy shown output (truncated)' : 'Copy shown output'}><pre tabIndex={0}>{shown}</pre></BlockCopy>}{stored ? <p className="a-caption">Only the saved result preview is shown{typeof result.bytes === 'number' ? ` (${Math.ceil(result.bytes / 1024).toLocaleString()} KB saved)` : ''}. Copy includes this preview only.</p> : truncated && <p className="a-caption">{output.length > 24000 ? 'Showing the first 24,000 characters.' : 'Showing partial output.'} Copy includes only the shown output.</p>}</div>;
}

function parseEnvironment(text) {
  const entries = text.split('\n').map(line => line.trim()).filter(Boolean);
  const result = {};
  for (const entry of entries) {
    const match = /^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)$/.exec(entry);
    if (!match) throw new Error('Use CHILD_VARIABLE=APP_VARIABLE on each line. Enter variable names, not secret values.');
    if (Object.hasOwn(result, match[1])) throw new Error(`The variable ${match[1]} appears more than once.`);
    result[match[1]] = match[2];
  }
  return result;
}

function parseHeaders(text) {
  const result = {};
  for (const line of text.split('\n').map(value => value.trim()).filter(Boolean)) {
    const match = /^([A-Za-z][A-Za-z0-9-]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)$/.exec(line);
    if (!match) throw new Error('Use HEADER_NAME=APP_VARIABLE, never credential values.');
    if (Object.keys(result).some(key => key.toLowerCase()===match[1].toLowerCase())) throw new Error('Header names must be unique.');
    result[match[1]]=match[2];
  }
  return result;
}

function collectArguments(schema, values, raw, json) {
  if (raw || !schema?.properties || schema.oneOf || schema.anyOf || schema.$ref) {
    const parsed = JSON.parse(json);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Tool arguments must be a JSON object.');
    return parsed;
  }
  const result = {};
  for (const [name, field] of Object.entries(schema.properties)) {
    let value = values[name] ?? (field.default == null ? '' : field.enum || typeof field.default === 'object' ? pretty(field.default) : String(field.default));
    if (value === '') {if (schema.required?.includes(name)) throw new Error(`${field.title || title(name)} is required.`); continue;}
    if (field.enum) value = JSON.parse(value);
    else if (field.type === 'boolean') value = value === 'true';
    else if (field.type === 'integer' || field.type === 'number') {
      value = Number(value);
      if (!Number.isFinite(value) || field.type === 'integer' && !Number.isInteger(value)) throw new Error(`${field.title || title(name)} must be ${field.type === 'integer' ? 'a whole number' : 'a number'}.`);
    } else if (field.type === 'object' || field.type === 'array' || field.oneOf || field.anyOf || field.$ref || Array.isArray(field.type)) value = JSON.parse(value);
    result[name] = value;
  }
  return result;
}

function SchemaFields({schema, values, edit}) {
  return Object.entries(schema.properties || {}).map(([name, field]) => {
    const id = `smart-tool-field-${name}`, label = field.title || title(name), required = schema.required?.includes(name);
    const complex = field.type === 'object' || field.type === 'array' || field.oneOf || field.anyOf || field.$ref || Array.isArray(field.type);
    const defaultValue = field.default == null ? '' : field.enum || complex ? pretty(field.default) : String(field.default);
    const value = values[name] ?? defaultValue;
    const common = {id, value, 'data-action':'view.update', onChange:event => edit({...values, [name]:event.target.value}), 'aria-describedby':field.description ? id+'-hint' : undefined};
    return <div className="a-smart-field" key={name}><label htmlFor={id}>{label}{required && <span className="a-smart-required">Required</span>}</label>{field.enum ? <select {...common}><option value="">{required ? 'Choose…' : 'Tool default'}</option>{field.enum.map((choice,index) => <option key={index} value={JSON.stringify(choice)}>{typeof choice === 'string' ? choice : JSON.stringify(choice)}</option>)}</select> : field.type === 'boolean' ? <select {...common}><option value="">{required ? 'Choose…' : 'Tool default'}</option><option value="true">Yes</option><option value="false">No</option></select> : complex ? <textarea {...common} className="a-json-editor" spellCheck={false} placeholder={field.type === 'array' ? '[]' : '{}'}/> : field.type === 'number' || field.type === 'integer' ? <input {...common} type="number" step={field.type === 'integer' ? '1' : 'any'} min={field.minimum} max={field.maximum}/> : <textarea {...common} rows={2} placeholder={field.examples?.[0] == null ? '' : String(field.examples[0])}/>}{field.description && <p id={id+'-hint'} className="a-caption">{field.description}</p>}</div>;
  });
}

export function SmartToolsSettings({state, act, simple=false, navigateSettings}) {
  const [editor, edit] = useEditor(state, act);
  const firstVisit=useRef(false);
  const [local, setLocal] = useState(null), [error, setError] = useState(''), [opening, setOpening] = useState(null);
  const smart = state.smartTools || {}, servers = list(smart.servers), catalog = list(smart.catalog), installations = list(smart.installations).filter(row=>!row.supersededBy&&!row.stagedFrom), operations = list(smart.operations);
  const server = servers.find(item => item.id === editor.serverId);
  const tools = list(server?.tools).filter(item => (item._meta?.ui?.visibility || ['model','app']).includes('model')), selectedTool = tools.find(item => item.name === editor.toolName);
  const tool = selectedTool && {...selectedTool,...(server?.loadedSchemaRevision === server?.catalogRevision ? server?.loadedSchemas?.[editor.toolName] : {})};
  const schemaReady = !!tool?.inputSchema && server?.catalogState !== 'stale';
  const operation = (action, target={}) => local?.action === action ? local : operationFor(operations, action, target);
  const run = async (action, args={}) => {
    setError(''); setLocal({action, status:'pending', target:args});
    try {const receipt = await act(action, args); if (!receipt || receipt.accepted===false) throw new Error('The request could not be sent. Please try again.'); setLocal(null); return receipt;}
    catch (caught) {setLocal({action, status:'failed', error:caught.message, target:args}); return null;}
  };
  const navigate = (page, patch={}) => {setError('');setLocal(null);edit({page,catalogReview:false,catalogInstalling:false,returnPage:page==='source'&&editor.page==='catalog'?'catalog':null,...patch});};
  const connection = {...defaults.connection, ...editor.connection};
  const editConnection = patch => edit({connection:{...connection,...patch}});
  const activeOperation = [...operations].reverse().find(working);
  const anyPending = working(local) || operations.some(item => working(item) && (
    editor.page === 'source' ? item.target?.repository === editor.repository.trim() :
    editor.page === 'connection' ? item.action === 'smartTools.configure' && (item.target?.id || '') === connection.id :
    ['server','tool'].includes(editor.page) ? item.target?.id === server?.id : false
  ));
  const [shownServers, serverFilter] = useListFilter(state, act, 'smart-tool-connections', servers, row => [row.name,row.command,row.status], 'Filter connections');
  const [shownCatalog, catalogFilter, catalogQuery] = useListFilter(state, act, 'smart-tool-catalog', catalog, row => [row.name,row.id,row.description,sourceOf(row)], simple?'Search tools':'Filter catalog',{simple});
  const [shownTools, toolFilter] = useListFilter(state, act, 'smart-tool-actions-'+(server?.id || ''), tools, row => [row.name,row.title,row.description], 'Filter tools');
  const batch=operation('smartTools.installBatch'), batchBusy=working(batch);
  const installedCatalog=item=>installations.some(row=>sourceOf(row).replace(/\.git$/,'')===sourceOf(item).replace(/\.git$/,'')&&(row.path||'.')===(item.path||'.'));
  const selectedIds=editor.catalogSelection||[];
  const selectedItems=catalog.filter(item=>selectedIds.includes(item.id)&&!installedCatalog(item));
  const catalogPage=Math.max(1,Math.min(editor.catalogPage||1,Math.ceil(shownCatalog.length/40)||1));
  const paged=shownCatalog.length>50, pageItems=paged?shownCatalog.slice((catalogPage-1)*40,catalogPage*40):shownCatalog;
  const catalogItem=catalog.find(item=>item.id===editor.catalogId)||pageItems[0];
  const toggleCatalog=(id,checked)=>edit({catalogSelection:checked?[...new Set([...selectedIds,id])]:selectedIds.filter(key=>key!==id)});
  const selectCatalog=items=>edit({catalogSelection:[...new Set([...selectedIds,...items.filter(item=>!installedCatalog(item)).map(item=>item.id)])]});
  useEffect(()=>{edit({catalogPage:1});},[catalogQuery]);
  const back = editor.page === 'tool' ? 'server' : 'home';
  const openConnection = item => navigate('connection', {connection:item ? {id:item.id,name:item.name,command:item.command,args:(item.args||[]).join('\n'),env:Object.entries(item.env||{}).map(([key,value]) => key+'='+value).join('\n'),cwd:item.cwd||'',transport:item.transport||'stdio',url:item.url||'',headers:Object.entries(item.headers||{}).map(([key,value]) => key+'='+value).join('\n'),auth:item.auth||'environment',installationId:item.installationId||''} : defaults.connection});
  const sourceArgs = () => ({repository:editor.repository.trim(), ...(editor.ref.trim() ? {ref:editor.ref.trim()} : {}), ...(editor.path.trim() ? {path:editor.path.trim()} : {})});
  const sourceTarget = {repository:editor.repository.trim(),ref:editor.ref.trim(),path:editor.path.trim()};
  const inspectionOp = operation('smartTools.inspect', sourceTarget);
  const installationOp = operation('smartTools.install', sourceTarget);
  const sourceKey=JSON.stringify(sourceTarget);
  const inspection=useRefreshValue(sourceKey,inspectionOp?.result,working(inspectionOp)||failed(inspectionOp));
  const installed = installations.find(item => sourceOf(item) === editor.repository.trim() && (item.path||'.') === (editor.path||'.'));
  const installation = !working(installationOp) && !failed(installationOp) ? installationOp?.result || installed : installed;
  const callOperation = operation('smartTools.call', {id:server?.id,name:tool?.name,sessionId:state.selectedSessionId});
  const callResult=useRefreshValue(JSON.stringify([state.selectedSessionId,server?.id,tool?.name]),callOperation,working(callOperation)||failed(callOperation));
  const latestViewCall = [...operations].reverse().find(item => item.action === 'smartTools.call' && item.status === 'completed' && item.target?.id === server?.id && item.target?.name === tool?.name && item.target?.sessionId === state.selectedSessionId);
  const openView = async () => {
    const sessionId = state.selectedSessionId;
    const receipt = await run('smartTools.open', {id:server.id,tool:tool.name,...(sessionId ? {sessionId} : {}),...(latestViewCall ? {operationId:latestViewCall.id} : {})});
    if (receipt?.operationId) setOpening({id:receipt.operationId,sessionId,serverId:server.id,toolName:tool.name});
  };
  useEffect(() => {
    if (!opening) return;
    const completed = operations.find(item => item.id === opening.id);
    if (!completed || working(completed)) return;
    if (completed.status === 'completed' && completed.result?.activated !== false && state.selectedSessionId === opening.sessionId && state.view?.panel === 'settings' && editor.page === 'tool' && editor.serverId === opening.serverId && editor.toolName === opening.toolName) act('view.update', {patch:{panel:null}});
    setOpening(null);
  }, [opening, operations, state.selectedSessionId, state.view?.panel, editor.page, editor.serverId, editor.toolName]);
  useEffect(()=>{if(!simple||firstVisit.current)return;firstVisit.current=true;if(!state.view?.smartToolsEditor?.page){navigate('home');if(!catalog.length)run('smartTools.catalog');}},[simple]);
  const installSelected=()=>{edit({catalogReview:false,catalogInstalling:true});return run('smartTools.installBatch',{ids:selectedItems.map(item=>item.id),extrasById:Object.fromEntries(selectedItems.map(item=>[item.id,(editor.catalogExtras?.[item.id]||'').split(',').map(extra=>extra.trim()).filter(Boolean)]))});};
  const finishInstallation=item=>navigate('setup',{setupInstructions:false,repository:sourceOf(item),ref:item.ref||'',path:item.path||'',extras:(item.extras||[]).join(', ')});
  const toolSchema = tool?.inputSchema || {type:'object',properties:{}};
  const rawOnly = !toolSchema.properties || !!(toolSchema.oneOf || toolSchema.anyOf || toolSchema.$ref);
  const lastConnectionOperation = ['smartTools.configure','smartTools.connect','smartTools.reconnect','smartTools.disconnect','smartTools.remove','smartTools.authForget','smartTools.accountAccept'].map(action => operation(action, {id:server?.id})).filter(Boolean).sort((a,b) => (b.updatedAt || b.createdAt || Infinity) - (a.updatedAt || a.createdAt || Infinity))[0];

  const stageRef=useRef(null);
  const catalogStage=(editor.catalogReview?'review':editor.catalogInstalling||batchBusy?'install':'browse');
  useLayoutEffect(()=>{if(editor.page==='catalog'&&catalogStage!=='browse'){stageRef.current?.focus();stageRef.current?.scrollIntoView({block:'start'});}},[catalogStage,editor.page]);
  return <section className="a-settings-section a-smart-tools" data-part="smart-tools-settings" data-simple={simple}>
    {editor.page !== 'home' && (!simple||!['catalog'].includes(editor.page)) && <button type="button" className="a-link a-smart-back" data-action="view.update" onClick={() => navigate(back)}><ArrowLeft/>Back to {back === 'server' ? server?.name || 'connection' : 'Smart Tools'}</button>}
    {error && <ResultNotice phase="error" message={error}/>}

    {simple?<><p className="a-everyday-intro">Add tools that help Amplifier do more of your work.</p><div className="a-tool-browse-tabs"><button className="a-soft" disabled={batchBusy} aria-pressed={editor.page==='home'} data-action="view.update" onClick={()=>navigate('home')}>Installed</button><button className="a-soft" disabled={batchBusy} aria-pressed={editor.page==='catalog'} data-action="smartTools.catalog" onClick={()=>{navigate('catalog');if(!catalog.length)run('smartTools.catalog')}}>Tool catalog</button><button className="a-link" disabled={batchBusy} data-action="view.update" onClick={()=>navigate('source',{repository:'',ref:'',path:'',extras:''})}>Add from GitHub URL</button></div></>:<div className="a-collection-toolbar"><button className="a-soft" data-action="view.update" onClick={()=>navigate('home')}>Installed & connected</button><button className="a-soft" data-action="smartTools.catalog" onClick={()=>{navigate('catalog');if(!catalog.length)run('smartTools.catalog');}}><Globe/>Tool catalog</button><button className="a-soft" data-action="view.update" onClick={()=>navigate('source')}><Download/>Add from Git</button><button className="a-soft" data-action="view.update" onClick={()=>openConnection()}><Plus/>Add MCP connection</button></div>}
    {editor.page === 'catalog' && <ActivityRegion className={simple?"a-tool-catalog-simple":undefined} name="smart-tool-catalog" busy={working(operation('smartTools.catalog'))}>
      {catalogStage==='review'&&<section className="a-tool-batch-review" aria-label="Review installation"><h4 ref={stageRef} tabIndex={-1}>Install {selectedItems.length} tools?</h4><p>Installation adds the packages. Accounts and permissions are connected separately.</p><ul>{selectedItems.map(item=><li key={item.id}>{item.name||item.id}</li>)}</ul><div className="a-dialog-actions"><button className="a-primary" data-action="smartTools.installBatch" disabled={batchBusy||!selectedItems.length} onClick={installSelected}>Install selected ({selectedItems.length})</button><button className="a-soft" data-action="view.update" disabled={batchBusy} onClick={()=>edit({catalogReview:false})}>Cancel</button></div></section>}
      {catalogStage==='install'&&<section aria-label="Catalog installation results"><h4 ref={stageRef} tabIndex={-1}>{batchBusy?'Installing your tools…':failed(batch)?'Some tools need attention':'Installation complete'}</h4><OperationNotice operation={batch} busyMessage="Installing selected packages…"/><ul className="a-catalog-results">{(batch?.items||[]).map(item=><li key={item.id}><strong>{item.name}</strong> · {item.status}<small>{item.error}</small>{item.status==='completed'&&<button className="a-link" data-action="view.update" onClick={()=>finishInstallation(item.source)}>Finish setup</button>}</li>)}</ul>{!batchBusy&&batch?.items?.some(item=>item.status!=='completed')&&<button className="a-soft" data-action="smartTools.installBatch" onClick={()=>run('smartTools.installBatch',{retryOperationId:batch.id})}>Retry unfinished installations</button>}{!batchBusy&&<div className="a-dialog-actions"><button className="a-primary" onClick={()=>navigate('home')}>View installed tools</button><button className="a-soft" onClick={()=>navigate('catalog')}>Back to catalog</button></div>}</section>}
      {catalogStage==='browse'?<>
      <div className="a-catalog-controls"><div className="a-smart-heading">{!simple&&<h4>Smart Tools catalog</h4>}<button type="button" className="a-icon" aria-label="Refresh Smart Tools catalog" data-action="smartTools.catalog" disabled={working(operation('smartTools.catalog'))} onClick={()=>run('smartTools.catalog')}><RefreshCw/></button></div>
      {!simple&&<p>Select tools to install together. Each package keeps its own installation result and setup steps.</p>}
      {(!simple||working(operation('smartTools.catalog'))||failed(operation('smartTools.catalog')))&&<OperationNotice operation={operation('smartTools.catalog')} busyMessage="Loading the catalog…"/>}
      {catalogFilter}<div className="a-collection-toolbar"><span>{simple?(paged?`${(catalogPage-1)*40+1}–${Math.min(catalogPage*40,shownCatalog.length)} of ${shownCatalog.length} tools`:`${shownCatalog.length===catalog.length?'All ':''}${shownCatalog.length} tools`):`${selectedItems.length} selected across the catalog`}</span><button className="a-link" data-action="view.update" onClick={()=>selectCatalog(pageItems)}>Select {paged?'page':simple?'all available':'matching'}</button>{paged&&<button className="a-link" data-action="view.update" onClick={()=>selectCatalog(shownCatalog)}>Select all matching</button>}{(!simple||selectedIds.length>0)&&<button className="a-link" disabled={!selectedIds.length} data-action="view.update" onClick={()=>edit({catalogSelection:[]})}>Clear selection</button>}</div>
      </div>
      {(simple||selectedItems.length>0||batchBusy)&&<SettingsActions><span className="a-catalog-selection-summary">{selectedItems.length} selected{selectedItems.some(item=>!pageItems.includes(item))&&<small>{selectedItems.filter(item=>!pageItems.includes(item)).length} outside this view</small>}</span><button className="a-primary" disabled={!selectedItems.length||batchBusy} data-action="smartTools.installBatch" onClick={()=>edit({catalogReview:true})}><Download/>Review & install</button></SettingsActions>}
      <Collection label="catalog" detailOpen={!!editor.catalogDetail} onBack={()=>edit({catalogDetail:false})} list={simple?pageItems.map(item=><div className="a-tool-catalog-row" key={item.id}><input type="checkbox" id={'select-tool-'+item.id} aria-label={'Select '+(item.name||item.id)} checked={selectedIds.includes(item.id)} disabled={installedCatalog(item)} data-action="view.update" onChange={event=>toggleCatalog(item.id,event.target.checked)}/><label htmlFor={'select-tool-'+item.id}><strong>{item.name||item.id}</strong><small>{item.description||'Open details to learn more.'}</small>{installedCatalog(item)&&<span className="a-count">Installed</span>}</label><button className="a-icon" aria-label={'Details for '+(item.name||item.id)} data-action="view.update" onClick={()=>edit({catalogId:item.id,catalogDetail:true})}><ChevronRight/></button></div>):pageItems.map(item=><CollectionRow key={item.id} id={item.id} label={item.name||item.id} description={item.description||sourceOf(item)} badge={installedCatalog(item)?'Installed':undefined} checked={selectedIds.includes(item.id)} disabled={installedCatalog(item)} onCheck={checked=>toggleCatalog(item.id,checked)} selected={!simple&&item.id===catalogItem?.id} onSelect={()=>edit({catalogId:item.id,catalogDetail:true})}/>)}>
       {catalogItem?<><h4>{catalogItem.name||catalogItem.id}</h4><p>{catalogItem.description}</p><details className="a-everyday-disclosure" open={!simple||undefined}><summary>Source & installation options</summary><div><p className="a-caption a-wrap">{sourceOf(catalogItem)}</p><dl><dt>Version</dt><dd>{catalogItem.version||catalogItem.manifest?.version||'See manifest'}</dd><dt>Branch or revision</dt><dd>{catalogItem.ref||'Repository default'}</dd></dl>{catalogItem.manifest?.requires&&<><h5>Requirements from the tool author</h5><pre className="a-wrap">{typeof catalogItem.manifest.requires==='string'?catalogItem.manifest.requires:pretty(catalogItem.manifest.requires)}</pre></>}<label htmlFor="catalog-package-extras">Optional package extras</label><input id="catalog-package-extras" value={editor.catalogExtras?.[catalogItem.id]||''} placeholder="For example, mcp" data-action="view.update" onChange={e=>edit({catalogExtras:{...editor.catalogExtras,[catalogItem.id]:e.target.value}})}/><p className="a-caption">Comma-separated extras from this tool’s instructions. Applied only to this selected package.</p></div></details><div className="a-dialog-actions"><button className="a-soft" disabled={installedCatalog(catalogItem)} data-action="view.update" onClick={()=>toggleCatalog(catalogItem.id,!selectedIds.includes(catalogItem.id))}>{installedCatalog(catalogItem)?'Installed':selectedIds.includes(catalogItem.id)?'Deselect tool':'Select for installation'}</button><button className="a-link" data-action="smartTools.inspect" onClick={()=>{navigate('source',{returnPage:'catalog',repository:sourceOf(catalogItem),ref:catalogItem.ref||'',path:catalogItem.path||'',extras:''});run('smartTools.inspect',{repository:sourceOf(catalogItem),...(catalogItem.ref?{ref:catalogItem.ref}:{}),...(catalogItem.path?{path:catalogItem.path}:{})});}}>Inspect source and setup</button></div><p className="a-caption">Installing a package does not connect an account or grant access. Some tools need advanced setup before they can be used.</p></>:<p>No matching catalog items.</p>}
      </Collection>
      {paged&&<div className="a-catalog-pages"><button className="a-soft" data-action="view.update" disabled={catalogPage===1} onClick={()=>edit({catalogPage:catalogPage-1,catalogDetail:false})}>Previous</button><span>{(catalogPage-1)*40+1}–{Math.min(catalogPage*40,shownCatalog.length)} of {shownCatalog.length}</span><button className="a-soft" data-action="view.update" disabled={catalogPage*40>=shownCatalog.length} onClick={()=>edit({catalogPage:catalogPage+1,catalogDetail:false})}>Next</button></div>}
      {!catalog.length&&!working(operation('smartTools.catalog'))&&<p className="a-smart-empty">The catalog has not loaded yet. Refresh to try again, or add a tool from a Git URL.</p>}
      </>:null}
    </ActivityRegion>}
    {simple&&editor.page==='home'&&<section className="a-installed-tools" aria-label="Installed tools">
      {!installations.length&&!servers.length?<div className="a-ai-welcome"><Box aria-hidden="true"/><h4>No tools installed yet</h4><p>Find tools for your work in the catalog, or add one using its GitHub URL.</p><button className="a-primary" onClick={()=>{navigate('catalog');if(!catalog.length)run('smartTools.catalog')}}>Explore tool catalog</button></div>:<>
       {!!installations.length&&<div className="a-everyday-list">{installations.map((item,index)=><CollectionRow key={item.id||index} id={item.id||String(index)} label={item.name||item.manifest?.name||sourceOf(item)} description={servers.some(row=>row.installationId===item.id)?'Installed · connection configured':'Installed · finish setup'} onSelect={()=>finishInstallation(item)}/>)}</div>}
       {!!servers.length&&<><h4>Connected services</h4>{servers.length>6&&serverFilter}<div className="a-everyday-list">{shownServers.map(item=><CollectionRow key={item.id} id={item.id} label={item.name} description={item.connectionState||(item.error?'Needs attention':item.status||'Not connected')} onSelect={()=>navigate('server',{serverId:item.id})}/>)}</div></>}
      </>}
    </section>}
    {editor.page!=='catalog'&&!(simple&&editor.page==='home')&&<Collection label="configurations" detailOpen={editor.page!=='home'} onBack={()=>navigate('home')} list={<>{servers.length>6&&serverFilter}{servers.length>0&&<div className="a-smart-heading"><h4>Connections</h4><small>{servers.filter(item=>['ready','connected'].includes(item.status)).length} ready · {servers.length} configured</small></div>}{shownServers.map(item=><CollectionRow key={item.id} id={item.id} label={item.name} description={(item.connectionState||(item.error?'Needs attention':item.status||'Not connected'))+(item.tools?.length?` · ${item.tools.length} tools`:'')+(item.uiCapable||item.tools?.some(tool=>tool._meta?.ui?.resourceUri)?' · Canvas views':'')} selected={item.id===server?.id} onSelect={()=>navigate('server',{serverId:item.id})}/>)}<div className="a-smart-heading"><h4>{simple?'Installed tools':'Installed packages'}</h4><small>{installations.length}</small></div>{installations.map((item,index)=><CollectionRow key={item.id||index} id={item.id||String(index)} label={item.name||item.manifest?.name||sourceOf(item)} description={simple?(servers.some(server=>server.installationId===item.id)?'Installed · connection configured':'Installed · finish setup'):item.version||item.commit?.slice(0,8)||'Installed'} selected={editor.page==='source'&&sourceOf(item)===editor.repository} onSelect={()=>finishInstallation(item)}/>)}{!servers.length&&!installations.length&&<div className="a-smart-empty"><h4>No tools installed yet</h4><p>Choose a tool from the catalog, add a GitHub URL, or connect an MCP service.</p></div>}</>}>
    {editor.page==='home'&&<><h4>Your Smart Tools</h4><p>Choose a connection to browse its tools, run a tool, or open its interactive view. Installed packages have their own setup details.</p>{activeOperation&&<OperationNotice operation={activeOperation}/>}<OperationNotice operation={operation('smartTools.remove')}/></>}

    {editor.page==='setup'&&<><h4>{installed?.name||installed?.manifest?.name||'Finish setup'}</h4><p>The package is installed. A connection and any required account access are separate.</p>{servers.filter(row=>row.installationId===installed?.id&&!!installed?.id).map(row=><button key={row.id} className="a-soft" data-action="view.update" onClick={()=>navigate('server',{serverId:row.id})}>Open {row.name}</button>)}{!editor.setupInstructions&&!servers.some(row=>row.installationId===installed?.id&&!!installed?.id)&&<div className="a-ai-hint"><strong>Advanced setup required</strong><p>This tool does not provide a guided connection setup. Use its documented connection details before using it.</p><button className="a-soft" data-action="view.update" onClick={()=>edit({setupInstructions:true})}>Open setup instructions</button></div>}{editor.setupInstructions&&<section className="a-smart-setup-instructions" aria-label="Setup instructions"><h4>Set up {installed?.name||installed?.manifest?.name||'this tool'}</h4><p>{installed?.guidance&&typeof installed.guidance==='string'?installed.guidance:installed?.nextStep||'Follow the tool author’s instructions to connect the installed package.'}</p>{/^https?:\/\//.test(editor.repository)&&<p><a href={editor.repository} target="_blank" rel="noreferrer">Open the tool’s documentation<ExternalLink/></a></p>}{installed?.binDir&&<><p>Installed executables</p><code className="a-smart-path">{installed.binDir}</code></>}{installed?.manifest?.requires&&<><h5>Requirements from the tool author</h5><SetupRequirements value={installed.manifest.requires}/></>}<div className="a-dialog-actions"><button className="a-primary" data-action="view.update" onClick={()=>navigate('connection',{connection:{...defaults.connection,name:installed?.name||installed?.manifest?.name||'',installationId:installed?.id||''}})}>Configure MCP connection</button></div></section>}<button className="a-link" data-action="view.update" onClick={()=>navigate('source')}>Source & installation options</button><details className="a-everyday-disclosure"><summary>Installation details</summary><div><p>{installed?.description}</p><code className="a-wrap">{editor.repository}</code><p className="a-caption">{installed?.nextStep}</p></div></details></>}
    {editor.page === 'source' && <>
      <h4>{installed?`Set up ${installed.name||installed.manifest?.name||'tool'}`:simple?'Add from GitHub URL':'Add from Git'}</h4><p>{installed?'Review the installed tool’s instructions and configure its connection below.':simple?'Paste the repository link for a Smart Tool. Review its details before installing.':'Inspect the tool’s manifest and source revision, then install the Python package into its own environment.'}</p>
      <label htmlFor="smart-tool-repository">Repository URL</label><input id="smart-tool-repository" type="url" value={editor.repository} placeholder="https://github.com/owner/tool" data-action="view.update" onChange={event => edit({repository:event.target.value})}/>
      <details className="a-everyday-disclosure"><summary>Branch & folder options</summary><div className="a-form-grid"><div><label htmlFor="smart-tool-ref">Branch, tag or commit (optional)</label><input id="smart-tool-ref" value={editor.ref} placeholder="Repository default" data-action="view.update" onChange={event => edit({ref:event.target.value})}/></div><div><label htmlFor="smart-tool-path">Package folder (optional)</label><input id="smart-tool-path" value={editor.path} placeholder="Repository root" data-action="view.update" onChange={event => edit({path:event.target.value})}/></div></div></details>
      <ActivityRegion name="smart-tool-inspection" busy={working(inspectionOp)}><div className="a-dialog-actions"><button type="button" className="a-soft" data-operation-pending={working(inspectionOp)||undefined} aria-busy={working(inspectionOp)||undefined} data-action="smartTools.inspect" disabled={!editor.repository.trim() || anyPending} onClick={() => run('smartTools.inspect',sourceArgs())}><RefreshCw/>Inspect source</button></div><OperationNotice operation={inspectionOp} busyMessage="Reading the tool manifest…"/>
      {inspection && <div className="a-smart-source-summary"><strong>{inspection.name || inspection.manifest?.name || inspection.metadata?.name || 'Tool source'}</strong><p>{inspection.description || inspection.manifest?.description || inspection.metadata?.description}</p><dl><div><dt>Revision</dt><dd><code>{inspection.commit?.slice(0,12) || inspection.revision || editor.ref || 'Default branch'}</code></dd></div>{inspection.version || inspection.manifest?.version ? <div><dt>Version</dt><dd>{inspection.version || inspection.manifest?.version}</dd></div> : null}</dl>{(inspection.manifest?.requires || inspection.requires) && <div className="a-smart-requirements"><h5>Requirements from the tool author</h5><pre>{typeof (inspection.manifest?.requires || inspection.requires) === 'string' ? (inspection.manifest?.requires || inspection.requires) : pretty(inspection.manifest?.requires || inspection.requires)}</pre></div>}</div>}
      </ActivityRegion><ActivityRegion name="smart-tool-installation" busy={working(installationOp)}><label htmlFor="smart-tool-extras">Optional package extras</label><input id="smart-tool-extras" value={editor.extras} placeholder="For example, mcp" data-action="view.update" onChange={event => edit({extras:event.target.value})}/><p className="a-caption">Use extras documented by the tool author, separated by commas. An MCP adapter may be an optional extra.</p>
      <SettingsActions><button type="button" className="a-primary" data-operation-pending={working(installationOp)||undefined} aria-busy={working(installationOp)||undefined} data-action="smartTools.install" disabled={!editor.repository.trim() || anyPending} onClick={() => run('smartTools.install',{...sourceArgs(),...(editor.extras.trim() ? {extras:editor.extras.split(',').map(value => value.trim()).filter(Boolean)} : {})})}><Download/>{installed ? 'Install source revision' : 'Install package'}</button></SettingsActions><OperationNotice operation={installationOp} busyMessage="Installing the package in its own environment…"/>
      {installation?.previousInstallationId&&<button type="button" className="a-soft" data-action="updates.smartToolRollback" onClick={()=>act('updates.smartToolRollback',{id:installation.id})}>Restore previous version</button>}{installation && <div className="a-smart-source-summary"><strong><Check/>Package installed</strong>{installation.binDir && <><p>Executables are available in:</p><code className="a-smart-path">{installation.binDir}</code></>}{installation.guidance && <p>{typeof installation.guidance === 'string' ? installation.guidance : pretty(installation.guidance)}</p>}<p>{installation.nextStep || 'Use the tool’s documented MCP executable and arguments to add a connection.'}</p><button type="button" className="a-soft" data-action="view.update" onClick={() => {openConnection();edit({connection:{...defaults.connection,name:installation.name || installation.manifest?.name || '',command:'',installationId:installation.id}});}}><Cable/>Configure MCP connection<ArrowRight/></button><button type="button" className="a-link a-danger" data-action="smartTools.uninstall" disabled={anyPending} onClick={() => run('smartTools.uninstall',{id:installation.id})}><Trash2/>Uninstall package</button><p className="a-caption">Remove dependent connections first. This deletes the app-managed package folder; work stored elsewhere is kept.</p><OperationNotice operation={operation('smartTools.uninstall',{id:installation.id})}/></div>}
    </ActivityRegion></>}

    {editor.page === 'connection' && <form id="smart-connection-form" onSubmit={async event => {event.preventDefault();try {
      await run('smartTools.configure',{...(connection.id?{id:connection.id}:{}),name:connection.name.trim(),transport:connection.transport,
        ...(connection.installationId?{installationId:connection.installationId}:{}),
        ...(connection.transport === 'stdio' ? {command:connection.command.trim(),args:connection.args.split('\n').map(value => value.trim()).filter(Boolean),env:parseEnvironment(connection.env),...(connection.cwd.trim()?{cwd:connection.cwd.trim()}:{})} :
          {url:connection.url.trim(),auth:connection.auth,headers:connection.auth==='oauth'?{}:parseHeaders(connection.headers)})});
    } catch(caught) {setError(caught.message);}}}>
      <h4>{connection.id ? 'Edit connection' : 'Add MCP connection'}</h4><p>Save a connection, then connect when you are ready. Saving does not contact the server.</p>
      <label htmlFor="smart-tool-connection-name">Display name</label><input id="smart-tool-connection-name" required value={connection.name} placeholder="My Smart Tool" data-action="view.update" onChange={event => editConnection({name:event.target.value})}/>
      <label htmlFor="smart-tool-transport">Connection type</label><select id="smart-tool-transport" value={connection.transport} data-action="view.update" onChange={event => editConnection({transport:event.target.value})}><option value="stdio">Local executable</option><option value="streamable-http">Remote MCP endpoint</option></select>
      {connection.transport === 'stdio' ? <>
        <label htmlFor="smart-tool-command">Executable</label><input id="smart-tool-command" required value={connection.command} placeholder="/path/to/environment/bin/tool-mcp" spellCheck={false} data-action="view.update" onChange={event => editConnection({command:event.target.value})}/>
        <label htmlFor="smart-tool-arguments">Arguments — one per line</label><textarea id="smart-tool-arguments" value={connection.args} spellCheck={false} data-action="view.update" onChange={event => editConnection({args:event.target.value})}/><p className="a-caption">Each line is one argument. Shell commands are not evaluated.</p>
        <label htmlFor="smart-tool-cwd">Working folder (optional)</label><PathField id="smart-tool-cwd" value={connection.cwd} onChange={cwd => editConnection({cwd})} directory state={state} act={act}/>
        <label htmlFor="smart-tool-environment">Environment variable references (optional)</label><textarea id="smart-tool-environment" value={connection.env} spellCheck={false} placeholder="TOOL_API_KEY=MY_TOOL_API_KEY" data-action="view.update" onChange={event => editConnection({env:event.target.value})}/><p className="a-caption">Use CHILD_VARIABLE=APP_VARIABLE. Enter names, never key values.</p>
      </> : <>
        <label htmlFor="smart-tool-url">MCP endpoint</label><input id="smart-tool-url" type="url" required value={connection.url} placeholder="https://example.com/mcp" data-action="view.update" onChange={event => editConnection({url:event.target.value})}/>
        <label htmlFor="smart-tool-auth">Authorization</label><select id="smart-tool-auth" value={connection.auth} data-action="view.update" onChange={event => editConnection({auth:event.target.value})}><option value="environment">Header references or no authentication</option><option value="oauth">Browser sign-in (OAuth)</option></select>
        {connection.auth === 'environment' ? <><label htmlFor="smart-tool-headers">Header references (optional)</label><textarea id="smart-tool-headers" value={connection.headers} placeholder="Authorization=MY_TOOL_AUTHORIZATION" data-action="view.update" onChange={event => editConnection({headers:event.target.value})}/><p className="a-caption">Use HEADER_NAME=APP_VARIABLE. The variable must contain the complete header value, such as a Bearer token. Never paste credentials here.</p></> : <p className="a-caption">The server must support MCP OAuth discovery. Start sign-in after saving, then review the provider’s consent page.</p>}
      </>}
      <OperationNotice operation={operation('smartTools.configure')} busyMessage="Saving the connection…"/>
      <SettingsActions><button type="submit" form="smart-connection-form" className="a-primary" data-action="smartTools.configure" disabled={!connection.name.trim() || !(connection.transport==='stdio'?connection.command:connection.url).trim() || anyPending}><Check/>Save connection</button><button type="button" className="a-soft" data-action="view.update" onClick={() => navigate('home')}>View connections<ArrowRight/></button></SettingsActions>
    </form>}

    {(editor.page === 'server' || editor.page === 'tool') && !server && <ResultNotice phase="neutral" message="This connection is no longer configured."/>}
    {editor.page === 'server' && server && <ActivityRegion name={'smart-tool-connection-'+server.id} busy={working(lastConnectionOperation)}>
      <div className="a-smart-heading"><div><h4>{server.name}</h4><p>{server.connectionState || server.status || 'Not connected'} · {tools.length} tools</p></div><button type="button" className="a-icon" aria-label={'Edit '+server.name} data-action="view.update" onClick={() => openConnection(server)}><Settings2/></button></div>
      <div className="a-smart-source-summary" data-part="connector-account">
        <p>Account identity: {['verified','accepted'].includes(server.account?.status) ? (server.account.displayName || server.account.subject) : server.account?.status === 'changed' ? 'Changed — review required' : server.account?.status === 'unconfirmed' ? 'Unconfirmed — reconnect is blocked' : 'Unknown — the server has not supplied supported account details.'}</p>
        {server.account?.subject && <p className="a-caption">{server.account.issuer} · {server.account.subject}</p>}
        {server.account?.provenance && <p className="a-caption">Server-attested through authenticated transport. This is not independent identity verification. Last observed: {new Date(server.account.observedAt*1000).toLocaleString()}.</p>}
        {server.account?.detail && <p>{server.account.detail}</p>}
        {server.account?.status === 'changed' && <>
          <p>Previously accepted: {server.account.expected.issuer} · {server.account.expected.subject}</p>
          <p>Reported now: {server.account.candidate.displayName || server.account.candidate.subject} · {server.account.candidate.issuer} · {server.account.candidate.subject}</p>
          <p className="a-caption">The connector attests this identity through its authenticated transport. Accepting saves this account choice; it does not reconnect or call a tool.</p>
          <button type="button" className="a-soft" data-action="smartTools.accountAccept" disabled={anyPending} onClick={() => run('smartTools.accountAccept',{id:server.id,expectedRevision:server.account.revision,issuer:server.account.candidate.issuer,subject:server.account.candidate.subject})}>Accept this account change</button>
        </>}
      </div>
      {server.authorization?.requestedScopes?.length > 0 && <p className="a-caption">Requested access: {server.authorization.requestedScopes.join(', ')}</p>}
      <p className="a-caption">Granted access: {server.authorization?.grantedScopes?.join(', ') || 'Not reported'} · Consent: {server.authorization?.consent || 'Unknown'}</p>
      {server.auth === 'oauth' && <div className="a-smart-source-summary">
        <p>Sign-in: {server.login?.phase || 'Not started'}</p>
        <div className="a-dialog-actions"><button type="button" className="a-soft" data-action="smartTools.authStart" disabled={['starting','waiting'].includes(server.login?.phase)} onClick={() => run('smartTools.authStart',{id:server.id,redirectOrigin:window.location.origin})}>Sign in</button>
        {server.login?.url && <a className="a-soft" href={server.login.url} target="_blank" rel="noopener noreferrer">Review access and authorize</a>}
        {['starting','waiting'].includes(server.login?.phase) && <button type="button" className="a-soft" data-action="smartTools.authCancel" onClick={() => run('smartTools.authCancel',{id:server.id})}>Cancel sign-in</button>}
        <button type="button" className="a-link a-danger" data-action="smartTools.authForget" onClick={() => run('smartTools.authForget',{id:server.id})}>Forget local sign-in</button></div>
        {server.login?.requestedScopes?.length > 0 && server.login.requestedScopes.join(' ') !== server.authorization?.requestedScopes?.join(' ') && <p>Requested access: {server.login.requestedScopes.join(', ')}</p>}
        {server.login?.error && <ResultNotice phase="error" message={server.login.error}/>}
        <p className="a-caption">You complete consent with the provider. Forgetting sign-in removes local credentials; revoke remote access in your provider’s account settings.</p>
      </div>}
      {server.error && <ResultNotice phase="error" message={server.error}/>}
      <div className="a-dialog-actions"><button type="button" className="a-soft" aria-busy={working(operation('smartTools.connect',{id:server.id}))||undefined} data-action="smartTools.connect" disabled={anyPending || ['connected','ready'].includes(server.status)} onClick={() => run('smartTools.connect',{id:server.id})}><Cable/>{server.status === 'connected' || server.status === 'ready' ? 'Connected' : 'Connect'}</button><button type="button" className="a-soft" aria-busy={working(operation('smartTools.disconnect',{id:server.id}))||undefined} data-action="smartTools.disconnect" disabled={anyPending || !['connected','ready'].includes(server.status)} onClick={() => run('smartTools.disconnect',{id:server.id})}><Unplug/>Disconnect</button><button type="button" className="a-soft" data-action="smartTools.reconnect" disabled={anyPending || ['starting','waiting'].includes(server.login?.phase)} onClick={() => run('smartTools.reconnect',{id:server.id})}><RefreshCw/>Reconnect</button></div>
      <OperationNotice operation={lastConnectionOperation} busyMessage="Updating the connection…"/>
      {server.catalogState === 'stale' && <ResultNotice phase="neutral" message="Tool definitions need a refresh before another call."/>}<button type="button" className="a-link" data-action="smartTools.discover" disabled={anyPending || !['connected','ready'].includes(server.status)} onClick={() => run('smartTools.discover',{id:server.id,refresh:true})}>Refresh tool definitions</button><OperationNotice operation={operation('smartTools.discover',{id:server.id})}/>
      {tools.length > 4 && toolFilter}<div className="a-smart-list">{shownTools.map(item => <button type="button" key={item.name} className="a-smart-list-row" data-action="view.update" onClick={() => {navigate('tool',{toolName:item.name,values:{},raw:false,json:'{}'});if(server.catalogState!=='stale')run('smartTools.schemas',{id:server.id,names:[item.name],catalogRevision:server.catalogRevision});}}><Code2/><span><strong>{item.title || item.annotations?.title || title(item.name)}</strong><small>{item.description || item.name}</small>{item._meta?.ui?.resourceUri && <small className="a-smart-view-label">Interactive canvas view</small>}</span><ChevronRight/></button>)}</div>
      {!tools.length && <p className="a-smart-empty">Connect to discover the tools supplied by this server.</p>}
      <div className="a-dialog-actions"><button type="button" className="a-link a-danger" disabled={anyPending} data-action="smartTools.remove" onClick={async () => {if(await run('smartTools.remove',{id:server.id}))navigate('home');}}><Trash2/>Remove connection</button></div><p className="a-caption">Removing a connection keeps the installed package and the tool’s saved work.</p>
    </ActivityRegion>}

    {editor.page === 'tool' && server && !tool && <ResultNotice phase="neutral" message="This tool is no longer in the server’s tool list. Reconnect to discover its current tools."/>}
    {editor.page === 'tool' && tool && <>
      <h4>{tool.title || tool.annotations?.title || title(tool.name)}</h4><p>{tool.description}</p><code className="a-smart-tool-id">{server.name} / {tool.name}</code>
      {tool._meta?.ui?.resourceUri && <div className="a-dialog-actions"><button type="button" className="a-soft" data-action="smartTools.open" disabled={anyPending} onClick={openView}><ExternalLink/>Open interactive view</button></div>}
      <OperationNotice operation={operation('smartTools.open',{id:server.id,tool:tool.name})} busyMessage="Opening the canvas view…"/>
      {!schemaReady && <><p>Load the current tool fields before making a call.</p><button type="button" className="a-soft" data-action="smartTools.schemas" disabled={anyPending || server.catalogState==='stale'} onClick={() => run('smartTools.schemas',{id:server.id,names:[tool.name],catalogRevision:server.catalogRevision})}>Load tool fields</button></>}<OperationNotice operation={operation('smartTools.schemas',{id:server.id})}/>
      {schemaReady && <form id="smart-tool-form" onSubmit={event => {event.preventDefault();try {const args=collectArguments(toolSchema,editor.values,editor.raw,editor.json);run('smartTools.call',{id:server.id,name:tool.name,arguments:args,catalogRevision:server.catalogRevision});} catch(caught) {setError(caught.message);}}}>
        {!rawOnly && !editor.raw ? <SchemaFields schema={toolSchema} values={editor.values} edit={values => edit({values})}/> : <><label htmlFor="smart-tool-json">Tool arguments (JSON)</label><textarea id="smart-tool-json" className="a-json-editor" value={editor.json} spellCheck={false} data-action="view.update" onChange={event => edit({json:event.target.value})}/></>}
        {!rawOnly && <button type="button" className="a-link a-smart-advanced" data-action="view.update" aria-expanded={editor.raw} onClick={() => {try {if(editor.raw){const parsed=collectArguments(toolSchema,{},true,editor.json);if(Object.keys(parsed).some(key => !Object.hasOwn(toolSchema.properties,key)))throw new Error('These arguments include extra properties. Keep the JSON editor to preserve them.');const values=Object.fromEntries(Object.entries(parsed).map(([key,value]) => [key,toolSchema.properties?.[key]?.enum || typeof value === 'object' ? JSON.stringify(value) : String(value)]));edit({raw:false,values});}else {let value;try{value=collectArguments({...toolSchema,required:[]},editor.values,false,'{}');}catch {value={};}edit({raw:true,json:pretty(value)});}setError('');} catch(caught){setError(caught.message);}}}>{editor.raw ? 'Use form fields' : 'Edit arguments as JSON'}</button>}
        <SettingsActions><button type="submit" form="smart-tool-form" className="a-primary" data-operation-pending={working(callOperation)||undefined} aria-busy={working(callOperation)||undefined} data-action="smartTools.call" disabled={anyPending || !['connected','ready'].includes(server.status)}><Play/>Run tool</button></SettingsActions>
      </form>}<ActivityRegion name="smart-tool-result" busy={working(callOperation)}><OperationNotice operation={callOperation} busyMessage="Running the tool…"/><ToolResult operation={callResult}/></ActivityRegion>
    </>}
    </Collection>}
    {simple&&<div className="a-everyday-footer"><button className="a-link" data-action="view.update" onClick={()=>navigateSettings?.('tool-connections')}>Advanced tool connections<ExternalLink/></button></div>}
  </section>;
}
