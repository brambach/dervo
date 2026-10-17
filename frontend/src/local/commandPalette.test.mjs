import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
HTMLElement.prototype.scrollIntoView=function(){};
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);
globalThis.cancelAnimationFrame=clearTimeout;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,within}=await import('@testing-library/react');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {CommandPalette,paletteItems,filterPalette,usePaletteShortcuts,namesCurrent}=await import('./CommandPalette.tsx');
const {WorkspaceSearch}=await import('./WorkspaceSearch.tsx');
const {ProjectKnowledge}=await import('./ProjectKnowledge.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;localStorage.clear();sessionStorage.clear();});

const conv=(id,activity={},extra={})=>({id,name:id,visibility:'shared',project_id:'p',profile_id:'a',model:'m',organisationGroup:null,activity:{status:null,running:false,step:null,unread:0,...activity},...extra});
const names=['orchestrator','openConversation','catchUp','knowledge','projectSettings','create','toggleGuide','stopCurrent','stopConversation','changeModel','usage','addGuidance','toggleTheme','feedback','workspaceSettings','search'];
function recorder(){const calls=[];const actions=Object.fromEntries(names.map(n=>[n,(...args)=>calls.push([n,...args])]));return {calls,actions};}
const base={conversations:[],currentId:'',hasProject:true,ready:true,guide:null,canStopCurrent:false,dark:false};
const labels=items=>items.map(i=>`${i.group}: ${i.label}`);

test('needs you and running come first, then screens, then actions that can run now',()=>{
 const {calls,actions}=recorder();
 const rows=[conv('quiet'),conv('stuck',{status:'needs'}),conv('busy',{status:'running',running:true,step:'Running checks'}),conv('mine',{},{visibility:'private'})];
 const items=paletteItems({...base,conversations:rows,currentId:'quiet',guide:{visible:false},canStopCurrent:true},actions);
 const all=labels(items);
 assert.deepEqual(all.slice(0,5),['Needs you: stuck','Running: busy','Go to: Home','Go to: Knowledge','Go to: Project settings'],'Catch up is Home now');
 assert.equal(items[1].icon,'running','threads carry their own status glyph');assert.equal(items[1].detail,'Running checks');
 assert.equal(items[0].icon,'needs');assert.equal(items[0].detail,'Waiting on you');
 assert.deepEqual(items.filter(i=>i.group==='Go to'&&i.conversation).map(i=>i.label),['stuck','busy','mine']);
 assert.equal(items.find(i=>i.label==='mine'&&i.group==='Go to').detail,'Private');
 for(const label of ['New thread','New private thread','Stop this thread','Stop busy','Show progress','Change account or model…','Usage by account','Add guidance','Switch to dark appearance','Send feedback','Settings'])assert.ok(all.includes(`Actions: ${label}`),label);
 assert.equal(all.includes('Actions: Stop quiet'),false);
 assert.equal(items.find(i=>i.id==='go:home').keys,'⌘1');assert.equal(items.find(i=>i.id==='go:knowledge').keys,'⌘2');
 items.find(i=>i.label==='Stop busy').run();assert.deepEqual(calls.at(-1),['stopConversation','busy']);
 items.find(i=>i.group==='Needs you').run();assert.deepEqual(calls.at(-1),['openConversation','stuck']);
 items.find(i=>i.label==='New private thread').run();assert.deepEqual(calls.at(-1),['create',true]);
 items.find(i=>i.id==='go:home').run();assert.deepEqual(calls.at(-1),['orchestrator']);
});

test('entries that could only be refused are left out',()=>{
 const {actions}=recorder();
 const none=labels(paletteItems({...base,hasProject:false,ready:false,dark:true},actions));
 assert.deepEqual(none,['Actions: Usage by account','Actions: Switch to light appearance','Actions: Send feedback','Actions: Settings']);
 const running=labels(paletteItems({...base,conversations:[conv('busy',{status:'running',running:true})],currentId:'busy',guide:{visible:true}},actions));
 assert.equal(running.includes('Actions: Stop busy'),false);
 assert.equal(running.includes('Actions: Stop this thread'),false);
 assert.ok(running.includes('Actions: Hide progress'));
 assert.equal(labels(paletteItems({...base,ready:false},actions)).includes('Actions: Add guidance'),false);
});

test('retry entries appear only when the shell says the host allows them',()=>{
 const {calls,actions}=recorder();
 const retried=[];actions.retryHere=()=>retried.push('here');actions.retryOther=()=>retried.push('other');
 const none=labels(paletteItems({...base,currentId:'c'},actions));
 assert.equal(none.some(l=>/Retry/.test(l)),false);
 const items=paletteItems({...base,conversations:[conv('Connector picker')],currentId:'Connector picker',retryHere:true,retryOther:'Codex'},actions);
 assert.ok(labels(items).includes('Actions: Retry Connector picker on Codex'),'the thread it retries is named');
 assert.ok(labels(items).includes('Actions: Retry here'));
 items.find(i=>i.id==='act:retry-other').run();items.find(i=>i.id==='act:retry').run();
 assert.deepEqual(retried,['other','here']);assert.equal(calls.length,0);
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/retryHere:!!guideTask&&guideDetail\.data\?\.task\.id===guideTask\.id&&!!guideDetail\.data\.retry\?\.available&&!guideDetail\.data\.rebound&&!handoffHoldsRetry\(guideTask\)/,
  'after your own Stop Retry here stays, beside Retry on the other provider (behaviour review N1)');
 assert.match(shell,/retryOther:guideTask\?\.suggest\?\.other&&!guideTask\.suggest\.taken&&!guideTask\.suggest\.automatic\?guideTask\.suggest\.other\.label:null/);
});

test('an empty query hides the plain conversation list; typing finds anything and adds a saved-text search when it can run',()=>{
 const {calls,actions}=recorder();
 const items=paletteItems({...base,conversations:[conv('auth refactor'),conv('Landing copy')]},actions);
 assert.equal(filterPalette(items,'',{ready:true},actions.search).some(i=>i.conversation&&i.group==='Go to'),false);
 const found=filterPalette(items,'  LANDING ',{ready:true},actions.search);
 assert.deepEqual(labels(found),['Go to: Landing copy','Search: Search all threads for “LANDING”']);
 assert.equal(found.at(-1).keys,'⌘↵');
 const nothing=filterPalette(items,'zebra',{ready:true},actions.search);
 assert.deepEqual(labels(nothing),['Search: Search all threads for “zebra”']);
 assert.equal(nothing[0].keys,'↵','with nothing else to pick, Return searches');
 found.at(-1).run();assert.deepEqual(calls.at(-1),['search','LANDING']);
 assert.deepEqual(labels(filterPalette(items,'landing',{ready:false},actions.search)),['Go to: Landing copy']);
 const busy=paletteItems({...base,conversations:[conv('upload fix',{status:'needs'}),conv('upload docs')]},actions);
 assert.deepEqual(labels(filterPalette(busy,'upload',{ready:false},actions.search)),['Needs you: upload fix','Go to: upload docs']);
});

function Palette({items,ready=true,onSearch=()=>{}}){
 const [open,setOpen]=React.useState(false);
 usePaletteShortcuts({toggle:()=>setOpen(o=>!o),catchUp:()=>items.find(i=>i.id==='go:home')?.run(),knowledge:()=>items.find(i=>i.id==='go:knowledge')?.run(),ready});
 return React.createElement(React.Fragment,null,React.createElement('textarea',{'aria-label':'Composer'}),React.createElement(CommandPalette,{open,onOpenChange:setOpen,items,ready,onSearch}));
}
const frame=props=>React.createElement(ThemeProvider,null,React.createElement(Palette,props));
const press=(key,extra={})=>fireEvent.keyDown(window,{key,metaKey:true,...extra});

test('⌘K opens with focus in the field; arrows move, Enter runs after closing, Escape closes, reopening starts fresh',async()=>{
 const {calls,actions}=recorder();
 const items=paletteItems({...base,conversations:[conv('stuck',{status:'needs'}),conv('general')]},actions);
 render(frame({items}));
 assert.equal(screen.queryByRole('dialog'),null);
 press('k');
 const input=await screen.findByRole('combobox',{name:'Search or jump to'});
 await waitFor(()=>assert.equal(document.activeElement,input));
 const options=()=>screen.getAllByRole('option');
 assert.equal(options()[0].getAttribute('aria-selected'),'true');
 assert.equal(input.getAttribute('aria-activedescendant'),options()[0].id);
 fireEvent.keyDown(input,{key:'ArrowUp'});
 assert.equal(options().at(-1).getAttribute('aria-selected'),'true');
 fireEvent.keyDown(input,{key:'ArrowDown'});fireEvent.keyDown(input,{key:'ArrowDown'});
 assert.equal(input.getAttribute('aria-activedescendant'),options()[1].id);
 assert.match(options()[1].textContent,/Home/,'where you left off is the first screen');
 fireEvent.keyDown(input,{key:'ArrowDown'});
 assert.match(options()[2].textContent,/Knowledge/);
 fireEvent.keyDown(input,{key:'Enter'});
 await waitFor(()=>assert.equal(screen.queryByRole('dialog'),null));
 await waitFor(()=>assert.deepEqual(calls.at(-1),['knowledge']));
 press('k');
 const again=await screen.findByRole('combobox');
 fireEvent.change(again,{target:{value:'gen'}});
 assert.equal(options()[0].querySelector('mark').textContent,'gen');
 fireEvent.keyDown(again,{key:'Escape'});
 await waitFor(()=>assert.equal(screen.queryByRole('dialog'),null));
 assert.equal(calls.length,1);
 press('k');
 assert.equal((await screen.findByRole('combobox')).value,'');
 press('k');
 await waitFor(()=>assert.equal(screen.queryByRole('dialog'),null));
});

test('clicking an option runs it; nothing matches says so without inventing a result',async()=>{
 const {calls,actions}=recorder();
 const items=paletteItems({...base,ready:false,hasProject:false},actions);
 render(frame({items,ready:false}));
 press('k');
 const input=await screen.findByRole('combobox');
 fireEvent.change(input,{target:{value:'zzz'}});
 assert.equal(screen.queryAllByRole('option').length,0);
 assert.match(screen.getByRole('status').textContent,/Nothing matches\. Choose a project/);
 fireEvent.change(input,{target:{value:'usage'}});
 fireEvent.click(screen.getByRole('option',{name:/Usage by account/}));
 await waitFor(()=>assert.deepEqual(calls.at(-1),['usage']));
});

test('⌘1 and ⌘2 go to Home and Knowledge only when the project can open them; other chords are ignored',async()=>{
 const {calls,actions}=recorder();
 const items=paletteItems(base,actions);
 const view=render(frame({items}));
 press('1');press('2');fireEvent.keyDown(window,{key:'1',ctrlKey:true});
 assert.deepEqual(calls.map(c=>c[0]),['orchestrator','knowledge','orchestrator'],'⌘1 is Home, where Catch up went');
 for(const chord of [{shiftKey:true},{altKey:true},{metaKey:false}]){press('k',chord);await new Promise(r=>setTimeout(r,10));assert.equal(screen.queryByRole('dialog'),null,JSON.stringify(chord));}
 view.rerender(frame({items,ready:false}));
 press('1');press('2');
 assert.equal(calls.length,3);
});

test('a search request from ⌘K runs once in project search and is handed back',async()=>{
 const calls=[];let handled=0;
 window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push({operation,args});return {results:[]};}};
 const props={projectId:'p',channelId:'',profileId:'a',mode:'project',open:true,onClose(){},onRequestHandled:()=>handled++};
 const view=render(React.createElement(WorkspaceSearch,{...props,request:{query:'flaky upload',id:1}}));
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='workspace_search').length,1));
 assert.equal(calls[0].args.query,'flaky upload');assert.equal(calls[0].args.scope,'project');
 assert.equal(screen.getByLabelText('Search saved text').value,'flaky upload');
 assert.equal(handled,1);
 view.rerender(React.createElement(WorkspaceSearch,{...props,request:null}));
 view.rerender(React.createElement(WorkspaceSearch,{...props,request:null,open:false}));
 view.rerender(React.createElement(WorkspaceSearch,{...props,request:null,open:true}));
 await new Promise(r=>setTimeout(r,20));
 assert.equal(calls.filter(c=>c.operation==='workspace_search').length,1);
 // A conversation search never runs a project request.
 cleanup();calls.length=0;
 render(React.createElement(WorkspaceSearch,{...props,mode:'conversation',channelId:'c',request:{query:'x',id:2}}));
 await new Promise(r=>setTimeout(r,20));
 assert.equal(calls.length,0);
});

test('Add guidance from ⌘K puts the cursor in the add line once and hands the request back',async()=>{
 window.__TAURI_INTERNALS__={invoke:async(_,{operation})=>{if(operation==='knowledge_list')return {rules:[],procedures:[]};throw Error(operation);}};
 let handled=0;
 const props={projectId:'p',channelId:'c',profileId:'account',open:true,onClose(){},onAddHandled:()=>handled++};
 const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
 const qc=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const knowledge=extra=>React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:qc},React.createElement(ProjectKnowledge,{...props,...extra})));
 const view=render(knowledge({addRequest:0}));
 const add=await screen.findByLabelText('Add a rule');
 assert.notEqual(document.activeElement,add);
 view.rerender(knowledge({addRequest:1}));
 await waitFor(()=>assert.ok(document.activeElement===add,'the add line has the cursor'));
 assert.equal(handled,1);
});

test('the shell opens the palette from the jump bar and wires each action to its real path',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/onJump=\{\(\)=>setPaletteOpen\(true\)\}/);
 assert.match(shell,/<CommandPalette open=\{paletteOpen\} onOpenChange=\{setPaletteOpen\} items=\{palette\}/);
 assert.match(shell,/usePaletteShortcuts\(\{toggle:\(\)=>setPaletteOpen\(open=>!open\)/);
 // The palette, the button's targets and Esc share one stop: stopHere, over the same conversation stop targets.
 assert.match(shell,/stopCurrent:\(\)=>void stopHere\(\)/);
 // A task already ending because you pressed Stop is left out (b11/visible, stopping.ts), so a second press sends nothing.
 assert.match(shell,/const unstopped=\(\)=>conversationStopTargets\(activity\.data,tasks\.data\?\?\[\],requests\.data\?\?\[\]\)\.filter\(target=>!isStopping\(/);
 assert.match(shell,/async function stopHere\(\)\{\n\s*const targets=unstopped\(\);/);
 assert.match(shell,/useEscapeToStop\([^\n]*\n[^\n]*stop:stopHere\}\)\)/);
 assert.match(shell,/local<CodingTask\[\]>\('tasks',\{projectId,channelId:id\}\)\.then\(rows=>stopTargets\(id,conversationStopTargets\(undefined,rows,\[\]\),rows\)\)/);
 assert.match(shell,/<WorkspaceSearch request=\{searchMode==='project'\?searchRequest:null\} onRequestHandled=\{\(\)=>setSearchRequest\(null\)\}/);
 assert.match(shell,/<ProjectKnowledge addRequest=\{addGuidanceRequest\} onAddHandled=\{\(\)=>setAddGuidanceRequest\(0\)\}/);
 assert.match(shell,/toggleTheme:\(\)=>theme\.applyAppearance\(\{theme:theme\.isDark\?'buzz':'buzz-dark',accent:theme\.accentColor,followSystem:false\}\)/);
});

test('⌘K offers Log a Dervo bug with its key and Next up under Go to; Home says Catch up',()=>{
 const {actions}=recorder();
 let reported=0,bugs=0;actions.reportBug=()=>reported++;actions.bugs=()=>bugs++;
 const items=paletteItems(base,actions);
 const log=items.find(i=>i.id==='act:report-bug'),go=items.find(i=>i.id==='go:next-up');
 assert.deepEqual([log.group,log.label,log.keys],['Actions','Log a Dervo bug','⌘⇧B']);
 assert.deepEqual([go.group,go.label],['Go to','Next up']);
 assert.equal(items.some(i=>/Bugs and ideas|Dervo reports/.test(i.label)),false,'Next up replaces both (b16/lifecycle)');
 log.run();go.run();assert.deepEqual([reported,bugs],[1,1]);
 assert.equal(items.find(i=>i.id==='go:home').detail,'Catch up','the pane’s name since 20 Sep, not the leftover “Where you left off”');
 assert.ok(labels(filterPalette(items,'',{ready:true},actions.search)).includes('Actions: Log a Dervo bug'),'listed before you type');
 assert.equal(labels(filterPalette(items,'dervo bug',{ready:true},actions.search))[0],'Actions: Log a Dervo bug');
 // Negative controls: no project that keeps records has no Next up row, and a shell with no capture has no entry.
 assert.equal(paletteItems({...base,ready:false},actions).some(i=>i.id==='go:next-up'),false);
 assert.equal(paletteItems(base,recorder().actions).some(i=>i.id==='act:report-bug'),false);
});

test('⌘K pins or unpins the thread on screen, and only once its pin is known',()=>{
 const {actions}=recorder();
 const pins=[];actions.pin=next=>pins.push(next);
 const pin=paletteItems({...base,currentId:'c',pinned:false},actions).find(i=>i.id==='act:pin');
 assert.deepEqual([pin.group,pin.label,pin.detail],['Actions','Pin this thread','Never tidied away']);
 pin.run();
 const unpin=paletteItems({...base,currentId:'c',pinned:true},actions).find(i=>i.id==='act:pin');
 assert.deepEqual([unpin.label,unpin.detail],['Unpin this thread','Dervo can tidy it away']);
 unpin.run();
 assert.deepEqual(pins,[true,false]);
 // Negative controls: no thread on screen, pins not loaded yet, or a shell without the action: no entry.
 assert.equal(paletteItems({...base,currentId:'',pinned:false},actions).some(i=>i.id==='act:pin'),false);
 assert.equal(paletteItems({...base,currentId:'c'},actions).some(i=>i.id==='act:pin'),false);
 assert.equal(paletteItems({...base,currentId:'c',pinned:false},recorder().actions).some(i=>i.id==='act:pin'),false);
});

test('useThreadPin reads the sidebar’s pins and sends conversation_pin for the thread on screen',async()=>{
 const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
 const {useThreadPin}=await import('./CommandPalette.tsx');
 const sent=[];let pinned=['c'];
 window.__TAURI_INTERNALS__={invoke:async(command,{operation,args:body})=>{
  assert.equal(command,'local_request');
  sent.push(operation);
  if(operation==='conversation_pins')return pinned;
  if(operation==='conversation_pin'){pinned=body.pinned?[body.channelId]:[];return {id:body.channelId,pinned:body.pinned};}
  throw new Error(`unexpected ${operation}`);
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 let seen;
 function Probe(){seen=useThreadPin('p','c',true);return null;}
 render(React.createElement(QueryClientProvider,{client},React.createElement(Probe)));
 assert.equal(seen.pinned,undefined,'nothing until the pins load');
 await waitFor(()=>assert.equal(seen.pinned,true));
 seen.pin(false);
 await waitFor(()=>assert.equal(seen.pinned,false));
 assert.deepEqual(sent.filter(op=>op==='conversation_pin'),['conversation_pin']);
});

test('a refused pin from ⌘K says why instead of doing nothing',async()=>{
 const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
 const {useThreadPin}=await import('./CommandPalette.tsx');
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation})=>{
  if(operation==='conversation_pins')return [];
  throw new Error('This conversation is archived. Restore it before pinning it.');
 }};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const said=[];let seen;
 function Probe(){seen=useThreadPin('p','c',true,message=>said.push(message));return null;}
 render(React.createElement(QueryClientProvider,{client},React.createElement(Probe)));
 await waitFor(()=>assert.equal(seen.pinned,false));
 seen.pin(true);
 await waitFor(()=>assert.deepEqual(said,['Couldn’t pin it. This conversation is archived. Restore it before pinning it.']));
});

test('⌘⇧B runs once, before the composer’s Bold sees it, and never in the terminal or for ⌘B',async()=>{
 const {isBugKey,useBugKey,keyRan,menuShouldRun,menuAction,SAME_PRESS_MS}=await import('./nativeMenu.ts');
 const k=extra=>({metaKey:false,ctrlKey:false,shiftKey:false,altKey:false,key:'b',code:'KeyB',...extra});
 assert.equal(isBugKey(k({metaKey:true,shiftKey:true,key:'B'})),true);
 assert.equal(isBugKey(k({ctrlKey:true,shiftKey:true,key:'B'})),true);
 assert.equal(isBugKey(k({metaKey:true,shiftKey:true,key:'∫',code:'KeyB'})),true,'by code too');
 assert.equal(isBugKey(k({metaKey:true})),false,'⌘B stays Bold');
 assert.equal(isBugKey(k({metaKey:true,shiftKey:true,altKey:true})),false);
 assert.equal(isBugKey(k({shiftKey:true,key:'B'})),false);
 let runs=0,bolded=0;
 function Probe(){useBugKey(()=>runs++);return React.createElement('div',null,
  React.createElement('div',{contentEditable:true,'data-testid':'editor',onKeyDown:event=>{if(event.key.toLowerCase()==='b'&&event.metaKey)bolded++;}}),
  React.createElement('div',{'data-project-terminal':''},React.createElement('textarea',{'aria-label':'Terminal input'})));}
 const view=render(React.createElement(Probe));
 const editor=screen.getByTestId('editor');
 const press=(target,extra={})=>fireEvent.keyDown(target,{key:'B',code:'KeyB',metaKey:true,shiftKey:true,...extra});
 assert.equal(press(editor),false,'the default is prevented');
 assert.deepEqual([runs,bolded],[1,0],'capture opens and the editor never sees the key');
 press(editor,{repeat:true});assert.deepEqual([runs,bolded],[1,0],'a held key opens it once');
 fireEvent.keyDown(editor,{key:'b',code:'KeyB',metaKey:true});assert.deepEqual([runs,bolded],[1,1],'negative control: ⌘B still reaches Bold');
 press(screen.getByLabelText('Terminal input'));assert.equal(runs,1,'the terminal keeps its keys');
 view.unmount();press(document.body);assert.equal(runs,1,'gone with the shell');
 // The menu bar's copy of a press the page already ran is dropped, once; other actions and later choices run.
 assert.equal(menuAction('report-bug'),'report-bug');assert.equal(menuAction('quit'),null);assert.equal(menuAction(3),null);
 keyRan('report-bug',1000);
 assert.equal(menuShouldRun('report-bug',1000+SAME_PRESS_MS-1),false);
 assert.equal(menuShouldRun('report-bug',1000+SAME_PRESS_MS-1),true,'only the one copy');
 keyRan('settings',5000);assert.equal(menuShouldRun('report-bug',5001),true);assert.equal(menuShouldRun('settings',5000+SAME_PRESS_MS),true);
});

test('the shell gives the admin bug capture and everyone else Send feedback on Bug, from the key, /bug, ⌘K and the menu',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/const admin=releasesAllowed\(accountData\);\n\s*function reportBug\(\)\{if\(admin\)\{setPaletteOpen\(false\);setCaptureOpen\(true\);\}else openFeedback\('bug'\);\}/);
 assert.match(shell,/useBugKey\(reportBug\);/);
 assert.match(shell,/if\(kind==='bug'\)reportBug\(\);else openFeedback\(\);return true;/,'/bug in a thread');
 assert.match(shell,/<Home onFeedback=\{\(\)=>\{if\(takeFeedbackCommand\(\)==='bug'\)reportBug\(\);else openFeedback\(\);\}\}/,'/bug on Home');
 assert.match(shell,/reportBug:\(\)=>reportBug\(\),\n\s*bugs:\(\)=>projectAction\('bugs',jumpTrigger\(\)\),/);
 assert.match(shell,/else if\(action==='report-bug'\)reportBug\(\);/);
 assert.match(shell,/if\(action==='settings'\)shortcuts\.current\.settings\(\);/);
 assert.match(shell,/\{admin&&<BugCapture open=\{captureOpen\} onOpenChange=\{setCaptureOpen\} target=\{captureTarget\} context=\{captureContext\}\/>\}/);
 assert.match(shell,/<BetaFeedback launcher=\{false\} requestOpen=\{feedbackAsk\.n\} initialCategory=\{feedbackAsk\.category\}/);
 assert.match(shell,/panelOpen:panel\|\|usageOpen\|\|captureOpen\|\|/,'Esc closes the sheet, never stops a task under it');
 assert.equal(shell.includes('Back to conversation'),false,'“Thread” wherever a person reads it');
 assert.match(shell,/returnLabel=\{current\?'Back to thread':'Back to project'\}/);
});

test('"You’re in" only answers a query that reads as the open thread’s name (reviewer, 8 Oct)',()=>{
 assert.equal(namesCurrent('Fix auth','fix'),true);
 assert.equal(namesCurrent('Fix auth','au'),true,'the start of a later word');
 assert.equal(namesCurrent('Fix auth','a'),false,'one letter is just typing');
 assert.equal(namesCurrent('Fix auth','th'),false,'the middle of a word');
 assert.equal(namesCurrent(undefined,'fix'),false);
});
