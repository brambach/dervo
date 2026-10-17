import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.sessionStorage=dom.window.sessionStorage;
const React=await import('react');
const {render,fireEvent,cleanup,screen,act}=await import('@testing-library/react');
const {WorkspaceSearch}=await import('./WorkspaceSearch.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();localStorage.clear();sessionStorage.clear();delete window.__TAURI_INTERNALS__;});
const row={id:'exact-message',kind:'message',channelId:'c',channelName:'Fixture',snippet:'saved needle',source:{id:'exact-message',kind:'message',channelId:'c',revision:'r',offset:0}};
const props={projectId:'p',channelId:'c',profileId:'a',mode:'conversation',open:true,onClose(){}};
function mount(handler,extra={}){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};
 function Harness(p){return React.createElement(React.Fragment,null,React.createElement('textarea',{'aria-label':'Composer draft',defaultValue:'Unsent draft'}),React.createElement(WorkspaceSearch,p));}
 const view=render(React.createElement(Harness,{...props,...extra}));
 return {...view,calls,rerenderProps:p=>view.rerender(React.createElement(Harness,{...props,...p}))};
}
async function search(){fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'needle'}});fireEvent.click(screen.getByRole('button',{name:'Search'}));await screen.findByText((_,node)=>node.classList?.contains('dervo-result-excerpt')&&node.textContent.trim()==='saved needle');}
test('production search retains query through exact source, return, close and scope switches without touching draft',async()=>{
 const view=mount(op=>op==='workspace_source'?{...row,text:'Exact saved text'}:{results:[row]});
 const draft=screen.getByLabelText('Composer draft');draft.focus();
 await search();
 fireEvent.click(screen.getByRole('button',{name:'Read conversation'}));
 await screen.findByText('Exact saved text');
 assert.equal(draft.value,'Unsent draft');
 assert.equal(document.activeElement,draft);
 assert.equal(view.calls.find(c=>c.operation==='workspace_source').args.source.id,'exact-message');
 fireEvent.click(screen.getByRole('button',{name:'Return to search'}));
 assert.equal(screen.getByLabelText('Search saved text').value,'needle');
 view.rerenderProps({open:false});view.rerenderProps({open:true});
 assert.equal(screen.getByLabelText('Search saved text').value,'needle');
 view.rerenderProps({channelId:'different'});
 assert.equal(screen.getByLabelText('Search saved text').value,'');
 view.rerenderProps({channelId:'c'});
 assert.equal(screen.getByLabelText('Search saved text').value,'needle');
 assert.equal(draft.value,'Unsent draft');
});
test('late source response after navigation cannot open old excerpt',async()=>{
 let resolve;
 const view=mount(op=>op==='workspace_source'?new Promise(r=>{resolve=r;}):{results:[row]});
 await search();fireEvent.click(screen.getByRole('button',{name:'Read conversation'}));
 view.rerenderProps({channelId:'new'});
 await act(async()=>resolve({...row,text:'Old private excerpt'}));
 assert.equal(screen.queryByText('Old private excerpt'),null);
});
test('catch-up labels an unconfirmed outcome without invented next steps and retains error recovery',async()=>{
 const task={...row,id:'task',kind:'task',status:'disconnected',owner:'codex · Fixture',blocker:'Unknown: no blocker recorded',nextAction:'Unknown: no next action recorded'};
 mount(op=>{if(op==='workspace_source')throw new Error('Source changed. Search again.');return {results:[task]};},{mode:'catchup'});
 await screen.findByText('Outcome unconfirmed');
 assert.equal(screen.queryByText('Unknown: no blocker recorded'),null);
 assert.equal(screen.queryByText('Unknown: no next action recorded'),null);
 fireEvent.click(screen.getByRole('button',{name:'Read answer'}));
 await screen.findByRole('alert');
 assert.ok(screen.getByRole('button',{name:'Refresh catch-up'}));
});
test('empty and loading states are visible',async()=>{
 let resolve;
 mount(()=>new Promise(r=>{resolve=r;}));
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'none'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 assert.ok(screen.getByText('Searching saved records…'));
 await act(async()=>resolve({results:[]}));
 assert.ok(screen.getByText('No matches yet'));
});
test('new bridge operations agree and retained shell has literal actions',()=>{
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 const api=readFileSync(new URL('./api.ts',import.meta.url),'utf8');
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 for(const op of ['workspace_search','workspace_source','project_catchup']){assert.ok(bridge.includes(`"${op}"`));assert.ok(api.includes(`'${op}'`));}
 assert.ok(readFileSync(new URL('./ThreadMenu.tsx',import.meta.url),'utf8').includes('Search in thread'));assert.ok(shell.includes("setSearchMode('conversation');setSearchOpen(true)"));assert.ok(shell.includes('<DervoSidebar'));assert.ok(shell.includes('onJump={()=>setPaletteOpen(true)}'));assert.ok(shell.includes("setSearchMode(action==='search'?'project':'catchup')"));
});

test('query survives component unmount and project queries stay separate from private conversation queries',async()=>{
 const view=mount(()=>({results:[row]}));
 await search();
 view.rerenderProps({mode:'project'});
 assert.equal(screen.getByLabelText('Search saved text').value,'');
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'project-only'}});
 view.rerenderProps({mode:'project',channelId:'another'});
 assert.equal(screen.getByLabelText('Search saved text').value,'project-only');
 view.unmount();
 mount(()=>({results:[]}));
 assert.equal(screen.getByLabelText('Search saved text').value,'needle');
});

test('late search after close is discarded and background parent updates never open a result',async()=>{
 let resolve;
 const view=mount(()=>new Promise(r=>{resolve=r;}));
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'needle'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 view.rerenderProps({open:false});
 await act(async()=>resolve({results:[row]}));
 view.rerenderProps({open:true});
 assert.equal(screen.queryByText('saved needle'),null);
 assert.equal(screen.queryByRole('button',{name:'Return to search'}),null);
 assert.equal(view.calls.filter(c=>c.operation==='workspace_source').length,0);
});

test('workspace frontend allowlist rejects another operation before invoking the bridge',async()=>{
 const {workspaceRequest}=await import('./api.ts');
 let called=false;
 window.__TAURI_INTERNALS__={invoke:async()=>{called=true;}};
 await assert.rejects(workspaceRequest('workspace_sql',{}),/Unsupported saved-record operation/);
 assert.equal(called,false);
});

test('incomplete pages continue the original query, retain results and source return, and retry errors',async()=>{
 let continuationCalls=0;
 const view=mount((op,args)=>{
  if(op==='workspace_source')return {...row,text:'Paged source'};
  if(!args.cursor)return {results:[],cursor:'opaque-page',incomplete:true};
  assert.equal(args.query,'needle');
  assert.equal(args.cursor,'opaque-page');
  continuationCalls++;
  if(continuationCalls===1)throw new Error('Temporary read failure');
  return {results:[row],cursor:'last-page',incomplete:true};
 });
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'needle'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByRole('button',{name:'Continue search'});
 assert.equal(screen.queryByText('No matches yet'),null);
 assert.match(document.body.textContent,/Search is incomplete/);
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'edited draft query'}});
 fireEvent.click(screen.getByRole('button',{name:'Continue search'}));
 await screen.findByRole('alert');
 fireEvent.click(screen.getByRole('button',{name:'Continue search'}));
 await screen.findByText((_,node)=>node.classList?.contains('dervo-result-excerpt')&&node.textContent.trim()==='saved needle');
 fireEvent.click(screen.getByRole('button',{name:'Read conversation'}));
 await screen.findByText('Paged source');
 fireEvent.click(screen.getByRole('button',{name:'Return to search'}));
 assert.ok(screen.getByRole('button',{name:'Continue search'}));
 assert.equal(screen.getByLabelText('Search saved text').value,'edited draft query');
 assert.equal(screen.getByLabelText('Composer draft').value,'Unsent draft');
 assert.equal(view.calls.filter(c=>c.operation==='workspace_source').length,1);
});

test('catch-up loads later pages and size-limited empty search never claims absence',async()=>{
 mount((op,args)=>args.cursor?{results:[{...row,snippet:'Later blocker'}],cursor:null,incomplete:false}:{results:[],cursor:'next',incomplete:true},{mode:'catchup'});
 fireEvent.click(await screen.findByRole('button',{name:'Load more'}));
 await screen.findByText('Later blocker');
 assert.equal(screen.queryByRole('button',{name:'Load more'}),null);
 cleanup();
 mount(()=>({results:[],cursor:null,incomplete:true}));
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'needle'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByText(/Search is incomplete/);
 assert.equal(screen.queryByText('No matches yet'),null);
});

test('keyboard source inspection and return restore focus to exact result',async()=>{
 mount(op=>op==='workspace_source'?{...row,text:'Exact keyboard source'}:{results:[row]});
 await search();
 const source=screen.getByRole('button',{name:'Read conversation'});source.focus();
 fireEvent.click(source);
 await screen.findByText('Exact keyboard source');
 const back=screen.getByRole('button',{name:'Return to search'});
 assert.equal(document.activeElement,back);
 assert.ok(document.querySelector('[data-panel-focus="search"]'),'Reopening the menu can focus the still-open source view');
 fireEvent.click(back);
 assert.equal(document.activeElement,screen.getByRole('button',{name:'Read conversation'}));
 assert.equal(screen.getByLabelText('Search saved text').value,'needle');
});
test('delayed source response does not take focus from a newer composer edit',async()=>{
 let resolve;
 mount(op=>op==='workspace_source'?new Promise(r=>{resolve=r;}):{results:[row]});
 await search();
 const source=screen.getByRole('button',{name:'Read conversation'});source.focus();fireEvent.click(source);
 const draft=screen.getByLabelText('Composer draft');draft.focus();fireEvent.change(draft,{target:{value:'Newer typing'}});
 await act(async()=>resolve({...row,text:'Delayed source'}));
 assert.equal(document.activeElement,draft);assert.equal(draft.value,'Newer typing');
});
test('saved Markdown is readable without loading media, HTML or link navigation',async()=>{
 const text='**Recorded answer** with [a source](https://example.test/private). ![diagram](https://example.test/image.png) <img src="https://example.test/html.png" />';
 mount(()=>({results:[{...row,snippet:text}]}));
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'answer'}});fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByText((_,node)=>node.tagName==='STRONG'&&node.textContent==='Recorded answer');
 assert.equal(document.querySelector('.dervo-result-excerpt strong').textContent,'Recorded answer');
 assert.equal(document.querySelectorAll('img, a[href], iframe').length,0);
 assert.ok(screen.getByText('[Image: diagram]'));
});
test('Escape closes search and leaves the composer draft unchanged',()=>{
 let closed=0;mount(()=>({results:[]}),{onClose(){closed++;}});
 const input=screen.getByLabelText('Search saved text');input.focus();fireEvent.keyDown(input,{key:'Escape'});
 assert.equal(closed,1);assert.equal(screen.getByLabelText('Composer draft').value,'Unsent draft');
});

test('Catch up shows the latest ordinary answer beside tasks and opens its exact source',async()=>{
 const message={...row,id:'chat-answer',matchRole:'assistant',status:'completed',snippet:'Choose the smaller launch.',source:{...row.source,id:'chat-answer'}};
 const older={...row,id:'old-task',kind:'task',status:'completed',snippet:'An earlier coding result',source:{...row.source,id:'old-task',kind:'task'}};
 const view=mount(op=>op==='workspace_source'?{...message,text:'Choose the smaller launch. Start with sign-in.'}:{results:[message,older]},{mode:'catchup'});
 await screen.findByText('Choose the smaller launch.');
 assert.equal(screen.queryByText('An earlier coding result'),null);
 assert.ok(screen.getByText('Answer saved'));
 const draft=screen.getByLabelText('Composer draft');draft.focus();
 fireEvent.click(screen.getByRole('button',{name:'Read answer'}));
 await screen.findByText('Choose the smaller launch. Start with sign-in.');
 assert.equal(view.calls.find(c=>c.operation==='workspace_source').args.source.id,'chat-answer');
 assert.equal(draft.value,'Unsent draft');
 fireEvent.click(screen.getByRole('button',{name:'Back to briefing'}));
 fireEvent.click(screen.getByRole('button',{name:'All activity'}));
 assert.ok(screen.getByText('An earlier coding result'));
});

test('Catch up distinguishes a queued request and stopped partial answer from completed work',async()=>{
 const queued={...row,id:'queued',channelId:'queued-room',matchRole:'user',status:'queued',snippet:'Draft a launch plan'};
 const partial={...row,id:'partial',matchRole:'assistant',status:'interrupted',blocker:'Stopped before finishing',snippet:'The start of an answer'};
 mount(()=>({results:[queued,partial]}),{mode:'catchup'});
 await screen.findByText('Queued locally');
 assert.ok(screen.getByText('Stopped'));
 assert.ok(screen.getByText('Stopped before finishing'));
 assert.equal(screen.queryByText('Completed'),null);
 assert.ok(screen.getByRole('button',{name:'Read request'}));
 assert.ok(screen.getByRole('button',{name:'Read answer'}));
});

test('the default text-only coordination result opens as a saved answer without claiming a code change',async()=>{
 const answer={...row,id:'coordinated',kind:'coordination',matchRole:'assistant',status:'completed',snippet:'Start with one useful workflow.',source:{...row.source,id:'coordinated',kind:'coordination'}};
 const view=mount(op=>op==='workspace_source'?{...answer,text:answer.snippet}:{results:[answer]},{mode:'catchup'});
 await screen.findByText('Start with one useful workflow.');
 assert.ok(screen.getByText('Answer saved'));
 assert.equal(screen.queryByText('Completed'),null);
 fireEvent.click(screen.getByRole('button',{name:'Read answer'}));
 await screen.findByText('Saved conversation');
 assert.equal(view.calls.find(c=>c.operation==='workspace_source').args.source.kind,'coordination');
});


test('filters use loaded counts, keep exact sources and never request the provider',async()=>{
 const answer={...row,id:'answer',matchRole:'assistant'};
 const request={...row,id:'request',matchRole:'user',snippet:'Requested needle'};
 const helper={...row,id:'helper',helper:true,matchRole:'assistant',snippet:'Helper needle',source:{...row.source,id:'helper'}};
 const view=mount((op,args)=>op==='workspace_source'?{...helper,text:'Exact helper source'}:args.cursor?{results:[{...row,id:'older',matchRole:'assistant',snippet:'Older needle'}],incomplete:false}:{results:[answer,request,helper],cursor:'next',incomplete:true});
 await search();
 fireEvent.click(screen.getByRole('button',{name:'Helper work 1'}));
 assert.equal(screen.getAllByRole('article').length,1);
 assert.equal(screen.getByRole('article').textContent.includes('Helper needle'),true);
 assert.equal(view.calls.length,1);
 const open=screen.getByRole('button',{name:'Read answer'});open.focus();fireEvent.click(open);
 await screen.findByText('Exact helper source');
 assert.equal(view.calls.at(-1).args.source.id,'helper');
 fireEvent.click(screen.getByRole('button',{name:'Return to search'}));
 assert.equal(document.activeElement,open);
 fireEvent.click(screen.getByRole('button',{name:'Continue search'}));
 await screen.findByText('4 matches loaded.');
 assert.equal(screen.getByRole('button',{name:'Helper work 1'}).getAttribute('aria-pressed'),'true');
 assert.equal(screen.getAllByRole('article').length,1);
 fireEvent.click(screen.getByRole('button',{name:'All 4'}));
 assert.equal(screen.getAllByRole('article').length,4);
 assert.ok(view.calls.every(call=>['workspace_search','workspace_source'].includes(call.operation)));
 view.rerenderProps({channelId:'new'});
 assert.equal(screen.queryByLabelText('Filter loaded search results'),null);
});

test('literal highlighting follows the submitted query through pagination and leaves exact sources untouched',async()=>{
 const text='**A+B** then `a+b` and axb. <script>unsafe()</script> ![A+B](https://example.invalid/image.png)';
 const view=mount((op,args)=>op==='workspace_source'?{...row,text}:{results:[{...row,id:args.cursor?'next-message':row.id,snippet:text}],cursor:args.cursor?null:'next',incomplete:!args.cursor});
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'a+b'}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByRole('article');
 const matches=()=>Array.from(document.querySelectorAll('.dervo-result-excerpt mark')).map(n=>n.textContent);
 assert.deepEqual(matches(),['A+B','a+b']);
 assert.equal(document.querySelectorAll('img,script,a[href]').length,0);
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:'axb'}});
 assert.deepEqual(matches(),['A+B','a+b']);
 fireEvent.click(screen.getByRole('button',{name:'Continue search'}));
 await act(async()=>{});
 assert.equal(view.calls.at(-1).args.query,'a+b');
 assert.deepEqual(matches(),['A+B','a+b','A+B','a+b']);
 fireEvent.click(screen.getAllByRole('button',{name:'Read conversation'})[0]);
 await screen.findByLabelText('Saved answer preview');
 assert.equal(document.querySelectorAll('.dervo-result-reader mark').length,0);
 assert.equal(document.querySelectorAll('img,script,a[href]').length,0);
});

test('coverage is visible before results and Catch up does not highlight a retained search',async()=>{
 sessionStorage.setItem(JSON.stringify(['dervo-search','p','a','project']),'needle');
 mount(()=>({results:[row],cursor:'next',incomplete:true}),{mode:'catchup'});
 const notice=await screen.findByText(/This briefing is incomplete/);
 const results=screen.getByLabelText('Project briefing');
 assert.ok(notice.compareDocumentPosition(results)&Node.DOCUMENT_POSITION_FOLLOWING);
 assert.equal(document.querySelectorAll('mark').length,0);
 assert.ok(screen.getByRole('button',{name:'Load more'}));
});


test('literal highlights preserve submitted spaces exactly as the host does',async()=>{
 const view=mount(()=>({results:[{...row,snippet:'concatenate cat catalog'}]}));
 fireEvent.change(screen.getByLabelText('Search saved text'),{target:{value:' cat '}});
 fireEvent.click(screen.getByRole('button',{name:'Search'}));
 await screen.findByRole('article');
 assert.equal(view.calls[0].args.query,' cat ');
 assert.deepEqual(Array.from(document.querySelectorAll('mark')).map(n=>n.textContent),[' cat ']);
});


test('saved test instructions render as an accessible inert table with exact cell text',async()=>{
 const text='| Action | Expected result |\n| --- | --- |\n| Search `a+b` | Only the literal phrase highlights. |\n| [Read](https://example.invalid) ![diagram](https://example.invalid/a.png) | No remote content. |';
 mount(op=>op==='workspace_source'?{...row,text}:{results:[row]});
 await search();
 fireEvent.click(screen.getByRole('button',{name:'Read conversation'}));
 const table=await screen.findByRole('table');
 assert.deepEqual(Array.from(table.querySelectorAll('th')).map(n=>n.textContent),['Action','Expected result']);
 assert.equal(table.querySelector('td').textContent,'Search a+b');
 assert.equal(screen.getByRole('region',{name:'Saved answer table'}).tabIndex,0);
 assert.equal(table.querySelectorAll('mark,a,img,script').length,0);
});

test('search scope switches between this project and the open conversation, and Catch up has no scope',async()=>{
 const scopes=[];
 const view=mount(()=>({results:[]}),{mode:'project',channelId:'',onScope:next=>scopes.push(next),conversationName:'auth refactor'});
 const group=screen.getByRole('group',{name:'Search scope'});
 const [project,conversation]=group.querySelectorAll('button');
 assert.deepEqual([project.textContent,project.getAttribute('aria-pressed'),conversation.textContent,conversation.getAttribute('aria-pressed')],['This project','true','#auth refactor','false']);
 fireEvent.click(conversation);fireEvent.click(project);
 assert.deepEqual(scopes,['conversation','project']);
 view.rerenderProps({mode:'catchup',channelId:'',onScope:next=>scopes.push(next)});
 assert.equal(screen.queryByRole('group',{name:'Search scope'}),null);
 view.rerenderProps({mode:'project',channelId:''});
 assert.equal(screen.queryByRole('group',{name:'Search scope'}),null,'without a conversation to switch to, there is no scope choice');
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/onScope=\{searchMode!=='catchup'&&current&&binding\.data\?next=>setSearchMode\(next\):undefined\}/);
});
