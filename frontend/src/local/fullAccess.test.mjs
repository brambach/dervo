import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
globalThis.window=dom.window;globalThis.document=dom.window.document;
for(const key of Object.getOwnPropertyNames(dom.window))if(/^[A-Z]/.test(key)&&(!(key in globalThis)||/Event$|^(Event|EventTarget|Node|Element)$/.test(key)))globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,cleanup,act}=await import('@testing-library/react');
const {FullAccessSection,toggledConnectors,connectorLabel}=await import('./FullAccess.tsx');
const {progressSteps,progressNow}=await import('./progressBrief.ts');
const {taskFlow}=await import('./TaskFlow.tsx');
after(()=>{cleanup();dom.window.close();});

const view=(extra={})=>({enabled:false,active:false,available:true,unavailable:null,detail:'Claude Code can run any command as you in this folder.',warning:'beta.15',...extra});

test('Codex full access names Codex and still needs an explicit confirmation',async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push([operation,args]);return {};}};
 const screen=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',view:view({provider:'codex',connectors:null,computer_available:false}),onChanged:async()=>{}}));
 try{
  fireEvent.click(screen.getByRole('switch',{name:'Let Codex run commands in this project'}));
  assert.equal(calls.length,0);
  assert.match(screen.getByRole('group',{name:'Turn on full access'}).textContent,/Codex’s own commands/);
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Turn on full access'}));});
  assert.deepEqual(calls,[['coding_full_access_set',{projectId:'p',channelId:'c',enabled:true,warning:'beta.15'}]]);
  assert.equal(screen.queryByTestId('connector-picker'),null);
 }finally{cleanup();}
});

test('turning full access on takes a second, explicit click, and the warning is always on screen',async()=>{
 const calls=[];let changed=0;
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);return {};}};
 const screen=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',view:view(),onChanged:async()=>{changed++;}}));
 try{
  assert.match(screen.getByTestId('full-access-warning').textContent,/run any command as you/);
  const toggle=screen.getByRole('switch',{name:'Let Claude Code run commands in this project'});
  assert.equal(toggle.getAttribute('aria-checked'),'false');
  fireEvent.click(toggle);
  assert.deepEqual(calls,[],'the first click only asks');
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Turn on full access'}));});
  assert.deepEqual(calls,[['coding_full_access_set',{projectId:'p',channelId:'c',enabled:true,warning:'beta.15'}]]);
  assert.equal(changed,1);
 }finally{cleanup();}
 // Turning it off is one click; where it can't be recorded, it can't be turned on and says why.
 calls.length=0;
 const on=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',view:view({enabled:true,active:true}),onChanged:async()=>{}}));
 try{await act(async()=>{fireEvent.click(on.getByRole('switch'));});assert.deepEqual(calls,[['coding_full_access_set',{projectId:'p',channelId:'c',enabled:false}]]);}finally{cleanup();}
 const off=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',view:view({available:false,unavailable:'Full access works with Claude Code for now.'}),onChanged:async()=>{}}));
 try{assert.equal(off.getByRole('switch').disabled,true);assert.ok(off.getByText('Full access works with Claude Code for now.'));}finally{cleanup();}
});

test('the connector picker saves each tick, hides unticked ones and goes back to every connector when all are on',async()=>{
 const names=['claude.ai Wispr Flow','claude.ai Gmail','github'];
 assert.deepEqual(toggledConnectors(names,null,'claude.ai Gmail'),['claude.ai Wispr Flow','github'],'unticking one from every connector');
 assert.equal(toggledConnectors(names,['claude.ai Wispr Flow','github'],'claude.ai Gmail'),null,'all ticked again is every connector');
 assert.deepEqual(toggledConnectors(names,['claude.ai Wispr Flow','claude.ai Slack'],'github'),['claude.ai Wispr Flow','claude.ai Slack','github'],
  'an allowed connector missing from today’s list stays allowed');
 assert.equal(connectorLabel('claude.ai Wispr Flow'),'Wispr Flow');
 assert.equal(connectorLabel('github'),'github');
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);return {};}};
 const screen=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',onChanged:async()=>{},
  view:view({enabled:true,active:true,connectors:{names,allowed:['claude.ai Wispr Flow']}})}));
 try{
  const picker=screen.getByRole('group',{name:'Connectors Claude Code can use here'});
  assert.match(picker.textContent,/1 of 3/);
  assert.equal(screen.getByRole('checkbox',{name:'Wispr Flow'}).checked,true);
  assert.equal(screen.getByRole('checkbox',{name:'Gmail'}).checked,false);
  assert.match(picker.textContent,/A connector you add later stays off here until you tick it/);
  await act(async()=>{fireEvent.click(screen.getByRole('checkbox',{name:'github'}));});
  assert.deepEqual(calls,[['coding_connectors_set',{projectId:'p',channelId:'c',connectors:['claude.ai Wispr Flow','github']}]]);
 }finally{cleanup();}
 // Still listing: says so. Off or not in use: no picker at all.
 const listing=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',onChanged:async()=>{},view:view({enabled:true,active:true,connectors:{names:null,allowed:null}})}));
 try{assert.match(listing.getByRole('status').textContent,/Looking for your connectors/);}finally{cleanup();}
 for(const extra of [{enabled:false,connectors:{names,allowed:null}},{enabled:true,active:false,unavailable:'Switch to Claude.',connectors:{names,allowed:null}}]){
  const hidden=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',onChanged:async()=>{},view:view(extra)}));
  try{assert.equal(hidden.queryByTestId('connector-picker'),null);}finally{cleanup();}
 }
});

const base={id:'t',state:'working',full_access:true,origin:'conversation',check_profile:'manual-review',files:[],messages:[{role:'user',text:'Fix the header',created_at:1}],runs:[{id:'run',state:'running'}],binding:{provider:'claude',model:'m',cwd:'/p'}};
const tok=(tool,extra)=>({run_id:'run',tool,native:true,state:'delivered',success:true,...extra});

test('Progress describes Claude Code’s own steps and never calls its commands a Dervo check',()=>{
 assert.deepEqual(progressNow({...base,tokens:[tok('native:Bash',{state:'running',command:'Run the unit tests',started_at:10})]},20),{text:'Running',file:'Run the unit tests',since:10,quiet:false});
 assert.equal(progressNow({...base,tokens:[tok('native:Read',{state:'running',path:'screenshot home.png'})]},0).text,'Looking at a screenshot');
 const done={...base,state:'ready_for_review',tokens:[tok('native:Read',{path:'src/Header.tsx'}),tok('native:Edit',{path:'src/Header.tsx'}),tok('native:Bash',{command:'npm test'}),tok('native:Bash',{command:'npm run build',success:false}),tok('native:Read',{path:'screenshot header.png'})]};
 assert.deepEqual(progressSteps(done).map(s=>`${s.label}: ${s.text}`),['Changed: Header.tsx','Checked: Ran 2 commands, looked at 1 screenshot, 1 returned an error. Dervo ran no check of its own.']);
 const rows=taskFlow(done);
 assert.deepEqual(rows.map(r=>`${r.state} ${r.text} ${r.files.join()}`),['done Read Header.tsx','done Edited Header.tsx','done Ran npm test','done Ran npm run build','done Looked at a screenshot header.png']);
 assert.equal(rows[3].meta,'Returned an error','the command that returned an error is a done step with a caption, not a red one');
 // Negative: without full access the same record claims nothing about commands.
 assert.doesNotMatch(progressSteps({...done,full_access:false}).map(s=>s.text).join(),/command/);
});

test('a connector call is a step that names the connector and tool, and never shows its arguments',()=>{
 const call=tok('native:Connector',{connector:'Wispr Flow',command:'search notes'});
 assert.deepEqual(progressNow({...base,tokens:[{...call,state:'running',started_at:5}]},9),{text:'Using Wispr Flow',file:'search notes',since:5,quiet:false});
 const done={...base,state:'ready_for_review',tokens:[tok('native:Bash',{command:'npm test'}),call,{...call,command:'send message'},tok('native:Connector',{connector:'Google Calendar',command:'create event'})]};
 assert.deepEqual(taskFlow(done).map(r=>`${r.state} ${r.text} ${r.files.join()}`).slice(1),['done Used Wispr Flow search notes','done Used Wispr Flow send message','done Used Google Calendar create event']);
 assert.equal(progressSteps(done).find(s=>s.label==='Checked').text,'Ran 1 command, used Wispr Flow and Google Calendar. Dervo ran no check of its own.');
 // Negative: a run with no connector calls doesn't mention connectors, and an unknown tool is still no step.
 assert.doesNotMatch(progressSteps({...done,tokens:done.tokens.slice(0,1)}).map(s=>s.text).join(),/used/);
 assert.equal(taskFlow({...done,tokens:[tok('native:Unknown',{})]}).length,0);
});

test('a web search is a step in any mode, from either provider, and says what was searched (PLAN-native A5)',async()=>{
 const {liveStepLabel}=await import('./responsePresentation.ts');
 const normal={...base,full_access:false,binding:{provider:'codex',model:'m',cwd:'/p'}};
 // Codex names the query only when the search ends, so a running search can have none yet.
 assert.deepEqual(progressNow({...normal,tokens:[tok('native:WebSearch',{state:'running',started_at:4})]},6),{text:'Searching the web',file:undefined,since:4,quiet:false});
 assert.equal(liveStepLabel({...normal,tokens:[tok('native:WebSearch',{state:'running',command:'tauri opener plugin'})]},'run'),'Searching the web for tauri opener plugin');
 const done={...normal,state:'answered',tokens:[tok('native:WebSearch',{command:'tauri opener plugin'}),tok('native:WebSearch',{state:'cancelled',command:null})]};
 assert.deepEqual(taskFlow(done).map(r=>`${r.state} ${r.text} ${r.files.join()}`),['done Searched the web for tauri opener plugin','cancelled Searching the web ']);
 // Negative: a search claims no command, check or change.
 assert.deepEqual(progressSteps(done),[]);
 assert.doesNotMatch(progressSteps({...done,full_access:true,state:'ready_for_review'}).map(s=>s.text).join(),/command|screenshot/);
});

test('an attempt Dervo couldn’t record completely offers no Approve',async()=>{
 const {canApproveAttempt}=await import('./CodingTasks.tsx');
 const attempt={number:1,state:'unverified',kind:'manual-review',test:null,argv:[],diff:{},before:{},after:{}};
 assert.equal(canApproveAttempt({check_profile:'manual-review'},attempt),true);
 assert.equal(canApproveAttempt({check_profile:'manual-review'},{...attempt,unrecorded:['a.py (deleted)']}),false);
});

test('a record from before beta.15’s warning reads as off and says why until it’s confirmed again (security N4)',()=>{
 const screen=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',view:view({reconfirm:true}),onChanged:async()=>{}}));
 try{
  assert.equal(screen.getByRole('switch').getAttribute('aria-checked'),'false');
  assert.match(screen.getByRole('status').textContent,/Off since this update, which changed the warning\. Turn it on again to confirm it\./);
 }finally{cleanup();}
 const fresh=render(React.createElement(FullAccessSection,{projectId:'p',channelId:'c',view:view(),onChanged:async()=>{}}));
 try{assert.equal(fresh.queryByText(/Off since this update/),null,'control: a project that never had it says nothing');}finally{cleanup();}
});
