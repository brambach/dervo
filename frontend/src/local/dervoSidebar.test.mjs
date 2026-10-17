import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','PointerEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
HTMLElement.prototype.scrollIntoView=function(){};
globalThis.requestAnimationFrame=cb=>setTimeout(()=>cb(Date.now()),0);globalThis.cancelAnimationFrame=id=>clearTimeout(id);window.requestAnimationFrame=globalThis.requestAnimationFrame;window.cancelAnimationFrame=globalThis.cancelAnimationFrame;
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
const React=await import('react');
const {readFileSync}=await import('node:fs');
const {render,fireEvent,screen,cleanup,waitFor,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {DervoSidebar,sidebarThreads,QUIET_SHOWN,usageLine,usageLabels,accountLabel,projectLanding,threadGlyph,threadWhen,shortPath,automaticNote,NOTE_MS,STALE_DAYS,staleThread,staleGroup,usageFold,usageOneLine,tightestLeft}=await import('./DervoSidebar.tsx');
after(()=>dom.window.close());afterEach(cleanup);

const conv=(id,extra={})=>({id,name:id,visibility:'shared',project_id:'p',profile_id:'one',model:'m',organisationGroup:null,activity:{status:null,running:false,step:null,unread:0},...extra});
const account=(extra={})=>({profile_id:'a1',provider:'codex',identity:'bryce@example.test',method:'subscription',monthly_limit_usd:null,runs_today:0,tokens_today:0,cost_today_usd:null,cost_month_usd:null,runs_month:0,resets_at:null,percent:null,low:false,basis:'runs',...extra});
function mount(overrides={}){
 const calls=[];
 const record=name=>(...args)=>{calls.push([name,...args]);};
 const props={projects:[{id:'p',name:'Dervo',cwd:'/Users/bryce/dervo/app'},{id:'q',name:'Fieldnotes',cwd:'/Users/bryce/fieldnotes'}],projectId:'p',projectsLoading:false,projectsFailed:false,
  onRetryProjects:record('retry'),onSelectProject:record('project'),onAddProject:record('add'),ready:true,active:undefined,
  conversations:[],channelId:'',conversationActive:true,onSelectConversation:record('open'),onAction:record('action'),onCreate:record('create'),onJump:record('jump'),
  accounts:[],onUsage:record('usage'),onWorkspaceSettings:record('settings'),onReleases:record('releases'),onFeedback:record('feedback'),canSignOut:false,onSignOut:async()=>{calls.push(['signout']);},
  threadMenu:{workstreams:[{id:'i1',name:'Orchestrator',count:2}],filedUnder:c=>c.organisationGroup??null,cannotMove:c=>c.visibility==='private'?'Private threads stay unfiled':null,
   rename:c=>calls.push(['rename',c.id]),move:(c,target)=>calls.push(['move',c.id,target]),archive:c=>calls.push(['archive',c.id]),remove:async c=>{calls.push(['delete',c.id]);},stop:async c=>{calls.push(['stop',c.id]);}},
  onRename:(c,name)=>calls.push(['renamed',c.id,name]),onUndo:record('undo'),onConnectedAccounts:record('accounts'),
  accountNote:'Private beta',...overrides};
 // The picker reads archived projects and each project's counts when it opens.
 window.__TAURI_INTERNALS__={invoke:async(_,{operation})=>operation==='projects_activity'?{q:{needs:1,running:0}}:[]};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const view=render(React.createElement(QueryClientProvider,{client},React.createElement(DervoSidebar,props)));
 const rerender=view.rerender;view.rerender=element=>rerender(React.createElement(QueryClientProvider,{client},element));
 return {calls,view,props};
}
const openMenu=async trigger=>{fireEvent.keyDown(trigger,{key:'Enter'});};
const glyphOf=row=>row.querySelector('.dv-st');

test('the project button carries the project’s letter tile, the name and the folder with the home directory as ~, and its menu holds switching, add and settings',async()=>{
 const {calls}=mount();
 const trigger=screen.getByRole('button',{name:'Project Dervo'});
 // Design review 23 Sep, item 9: the project's own mark, the letter the project menu shows, on pine. The pebble is for
 // brand moments only.
 const tile=trigger.querySelector('.dv-mark.dv-ptile');
 assert.ok(tile,'the letter tile at 28px');
 assert.equal(tile.textContent,'D');
 assert.equal(trigger.querySelector('.dv-pebble'),null,'no smiley in the sidebar');
 assert.equal(trigger.querySelector('b').textContent,'Dervo');
 assert.equal(trigger.querySelector('small').textContent,'~/dervo/app');
 assert.equal(shortPath('/Users/bryce/dervo/app'),'~/dervo/app');assert.equal(shortPath('/opt/work'),'/opt/work');assert.equal(shortPath('/home/bryce/x'),'~/x');
 await openMenu(trigger);
 fireEvent.click(await screen.findByRole('menuitem',{name:'Open project Fieldnotes'}));
 assert.deepEqual(calls.at(-1),['project','q']);
 await openMenu(trigger);
 fireEvent.click(await screen.findByRole('menuitem',{name:'Project settings'}));
 assert.equal(calls.at(-1)[0],'action');assert.equal(calls.at(-1)[1],'settings');assert.equal(calls.at(-1)[2]===trigger,true);
 await openMenu(trigger);
 assert.equal(screen.queryByRole('menuitem',{name:'Manage projects'}),null,'Manage projects is gone: the picker and Settings > General hold its jobs');
 fireEvent.click(await screen.findByRole('menuitem',{name:'Add a project…'}));
 assert.equal(calls.at(-1)[0],'add');assert.equal(calls.at(-1)[1]===trigger,true);
 assert.equal(calls.some(call=>call[1]==='preview'),false);
 assert.equal(document.querySelector('.dervo-wordmark'),null,'no wordmark at the top of the sidebar (home-morning.html has none)');
});

test('a failed project list offers a retry and selects nothing',async()=>{
 const {calls}=mount({projects:[],projectsFailed:true,projectId:''});
 await openMenu(screen.getByRole('button',{name:'Choose a project'}));
 assert.match(screen.getByRole('alert').textContent,/Projects couldn’t load/);
 fireEvent.click(await screen.findByRole('menuitem',{name:'Try again'}));
 assert.deepEqual(calls,[['retry']]);
 assert.equal(screen.queryByRole('menuitem',{name:/Open project/}),null);
});

test('nav is Home with the needs-you count, then Knowledge; Search is the jump bar with ⌘K, and settings leave the nav',()=>{
 const {calls,view,props}=mount({conversations:[conv('a',{activity:{status:'needs',running:false,step:null,unread:0}}),conv('b'),conv('c',{activity:{status:'needs',running:false,step:null,unread:2}})],active:'orchestrator'});
 const home=screen.getByRole('button',{name:/^Home/});
 assert.equal(document.querySelector('.dv-sb-nav .dv-sb-item')===home,true,'the front door comes first');
 assert.equal(home.getAttribute('aria-current'),'page');
 assert.equal(within(home).getByLabelText('2 need you').textContent,'2');
 assert.equal(within(home).getByLabelText('2 need you').className,'dv-meta','the count is a quiet figure, not the accent');
 fireEvent.click(home);assert.equal(calls.at(-1)[1],'orchestrator');assert.equal(calls.at(-1)[2]===home,true);
 assert.equal(screen.queryByRole('button',{name:/^Catch up/}),null,'Catch up folded into Home');
 fireEvent.click(screen.getByRole('button',{name:'Knowledge'}));assert.equal(calls.at(-1)[1],'knowledge');
 const jump=screen.getByRole('button',{name:/Search or jump to/});
 assert.ok(jump.classList.contains('dv-sb-search'));assert.equal(jump.querySelector('kbd').textContent,'⌘K');
 fireEvent.click(jump);assert.equal(calls.at(-1)[0],'jump');assert.equal(calls.at(-1)[1]===jump,true);
 assert.equal(screen.queryByRole('button',{name:'Project settings'}),null);
 assert.equal(screen.queryByRole('button',{name:'Search',exact:true}),null);
 view.rerender(React.createElement(DervoSidebar,{...props,ready:false,conversations:[]}));
 for(const name of [/^Home/,'Knowledge',/Search or jump to/])assert.equal(screen.getByRole('button',{name}).disabled,true);
 assert.equal(screen.queryByLabelText(/need you/),null);
});

test('threads sit under their workstream with its count: what needs you first, then running, then unopened, then quiet ones, each with a glyph and a time',t=>{
 // Keep the recent rows on the same day, even when this test runs after midnight.
 t.mock.timers.enable({apis:['Date'],now:new Date(2026,8,23,12,0)});
 const at=Math.floor(Date.now()/1000)-600;
 const rows=[conv('quiet',{organisationGroup:'Search',activity:{status:null,running:false,step:null,unread:0,at:at-6*86400}}),conv('fresh',{organisationGroup:'Search',activity:{status:null,running:false,step:null,unread:3,at}}),
  conv('stuck',{organisationGroup:'Search',activity:{status:'needs',running:false,step:null,unread:1,at}}),conv('busy',{organisationGroup:'Search',activity:{status:'running',running:true,step:'Running checks',unread:0,since:at}})];
 const {calls}=mount({conversations:rows});
 const group=screen.getByRole('group',{name:'Search'});
 assert.deepEqual([...group.querySelectorAll('.dv-name')].map(n=>n.textContent),['stuck','busy','fresh'],'three threads want you, so the quiet one folds');
 assert.equal(group.querySelector('.dv-sb-group-head .dv-label').textContent,'Search');
 assert.equal(group.querySelector('.dv-sb-group-head .dv-count').textContent,'4','the count includes the folded thread');
 assert.ok(within(group).getByRole('button',{name:'1 more'}));
 const stuck=group.querySelector('[data-conversation-id="stuck"]');
 assert.equal(glyphOf(stuck).dataset.state,'needs');assert.equal(glyphOf(stuck).getAttribute('aria-label'),'Needs you');
 assert.equal(glyphOf(stuck).hasAttribute('data-accent'),true,'the first thread that needs you carries the one accent');
 const busy=group.querySelector('[data-conversation-id="busy"]');
 assert.equal(glyphOf(busy).dataset.state,'running');assert.equal(glyphOf(busy).getAttribute('aria-label'),'Running');
 assert.equal(busy.querySelector('.dv-meta').textContent,'now');
 const fresh=group.querySelector('[data-conversation-id="fresh"]');
 assert.equal(glyphOf(fresh).dataset.state,'done','a settled result you haven’t opened reads as finished');
 assert.ok(fresh.querySelector('.dv-name.dv-unseen'),'an unopened result is bold');
 assert.equal(within(fresh).getByText('3 unopened').className,'sr-only');
 assert.match(fresh.querySelector('.dv-meta').textContent,/^\d{1,2}:\d{2}$/,'a time today, without AM or PM');
 assert.equal(document.querySelector('[data-testid="moss"]'),null,'no moving mark in the list');
 assert.equal(document.querySelector('.dervo-sb-ping,.dervo-sb-unread,.dervo-sb-live'),null,'no plain dots: the glyph carries the state');
 assert.equal(document.body.textContent.includes('#'),false,'no chat-room # in front of names');
 fireEvent.click(within(fresh).getByRole('button',{name:/^fresh/}));
 assert.deepEqual(calls.at(-1),['open','fresh']);
});

test('one accent in the whole sidebar: the first thread that needs you, or the one you are in when it waits on you',()=>{
 const needs={status:'needs',running:false,step:null,unread:0,attention:true};
 const rows=[conv('a',{organisationGroup:'Alpha',activity:{...needs}}),conv('b',{organisationGroup:'Alpha',activity:{...needs}}),conv('c',{organisationGroup:'Beta',activity:{...needs}})];
 mount({conversations:rows});
 assert.deepEqual([...document.querySelectorAll('.dv-st[data-accent]')].map(g=>g.closest('[data-conversation-id]').dataset.conversationId),['a']);
 assert.deepEqual([...document.querySelectorAll('.dv-st[data-state="needs"]')].length,3,'the others keep the glyph in ink');
 cleanup();
 mount({conversations:rows,channelId:'a'});
 assert.deepEqual([...document.querySelectorAll('.dv-st[data-accent]')].map(g=>g.closest('[data-conversation-id]').dataset.conversationId),['a'],'the open thread waits on you, so it holds the one accent and no other row is rust');
 cleanup();
 // Negative: a need you have already opened isn't the one accent.
 mount({conversations:[conv('seen',{activity:{...needs,attention:false}}),conv('new',{activity:{...needs}})],channelId:'other'});
 assert.deepEqual([...document.querySelectorAll('.dv-st[data-accent]')].map(g=>g.closest('[data-conversation-id]').dataset.conversationId),['new']);
 assert.deepEqual(threadGlyph({status:'needs',running:false,step:null,unread:0}),{state:'needs',label:'Needs you'});
 assert.deepEqual(threadGlyph({status:null,running:true,step:'x',unread:0}),{state:'running',label:'Running'});
 // Read and settled rows keep the quiet ring but say how they ended, as Home and the thread do (polish, 8 Oct).
 assert.deepEqual(threadGlyph({status:null,running:false,step:null,unread:0,ended:'approved'}),{state:'idle',label:'Approved'});
 assert.deepEqual(threadGlyph({status:null,running:false,step:null,unread:1,ended:'approved'}),{state:'done',label:'Approved'});
 assert.deepEqual(threadGlyph({status:null,running:false,step:null,unread:0,ended:'answered'}),{state:'idle',label:'Answered'});
 assert.deepEqual(threadGlyph({status:null,running:false,step:null,unread:0,ended:'idle'}),{state:'idle',label:'Idle'});
 // Bryce, 26 Sep: a question waiting on you wins over Running on the row, as in the thread.
 assert.deepEqual(threadGlyph({status:'needs',running:true,step:'x',unread:0,pending_question:'Which rows?'}),{state:'needs',label:'Asked you something'});
 assert.deepEqual(threadGlyph({status:null,running:false,step:null,unread:2}),{state:'done',label:'Finished'});
 assert.deepEqual(threadGlyph(null),{state:'idle',label:'Idle'});
 // 19 Sep: how the newest task ended gives a failed run its cross and a finished one its check, as in the mockups.
 assert.deepEqual(threadGlyph({status:'needs',running:false,step:null,unread:1,ended:'blocked'}),{state:'failed',label:'Stopped with an error'});
 assert.deepEqual(threadGlyph({status:'needs',running:false,step:null,unread:1,ended:'ready_for_review'}),{state:'done',label:'Ready for review'});
 assert.deepEqual(threadGlyph({status:'needs',running:false,step:null,unread:1,ended:'disconnected'}),{state:'unknown',label:'Stopped, outcome not confirmed'});  // beta.17.1: lost track draws the question, not a stop
 assert.deepEqual(threadGlyph({status:'needs',running:false,step:null,unread:1,ended:'needs_permission'}),{state:'needs',label:'Waiting for your permission'});
 assert.deepEqual(threadGlyph({status:'needs',running:false,step:null,unread:1,ended:'asking'}),{state:'needs',label:'Asked you something'},'the thread Home marks in rust');
});

test('a thread’s time is short: the clock today, the weekday this week, the date beyond, now while running, nothing without a record',()=>{
 const now=Date.parse('2026-09-19T09:00:00');
 assert.equal(threadWhen({status:null,running:true,step:null,unread:0},now),'now');
 assert.equal(threadWhen({status:null,running:false,step:null,unread:0,at:Date.parse('2026-09-19T01:52:00')/1000},now),'1:52');
 assert.equal(threadWhen({status:null,running:false,step:null,unread:0,at:Date.parse('2026-09-15T16:10:00')/1000},now),'Tue');
 assert.match(threadWhen({status:null,running:false,step:null,unread:0,at:Date.parse('2026-08-02T16:10:00')/1000},now),/Aug/);
 assert.equal(threadWhen({status:null,running:false,step:null,unread:0,at:null},now),'');
 assert.equal(threadWhen(undefined,now),'');
});

test('quiet threads fold behind "N more", except the one you have open, and private ones sit apart, labelled',()=>{
 const quiet=Array.from({length:QUIET_SHOWN+2},(_,i)=>conv('q'+i,{organisationGroup:'Search'}));
 const rows=[...quiet,conv('mine',{visibility:'private'}),conv('loose')];
 mount({conversations:rows,channelId:'q4'});
 const group=screen.getByRole('group',{name:'Search'});
 assert.deepEqual([...group.querySelectorAll('.dv-name')].map(n=>n.textContent),['q0','q1','q2','q4'],'the open thread stays visible');
 fireEvent.click(within(group).getByRole('button',{name:'1 more'}));
 assert.equal(group.querySelectorAll('.dv-name').length,QUIET_SHOWN+2);
 fireEvent.click(within(group).getByRole('button',{name:'Show fewer'}));
 assert.equal(group.querySelectorAll('.dv-name').length,QUIET_SHOWN+1);
 const groups=[...document.querySelectorAll('.dv-sb-group')].map(node=>node.getAttribute('aria-label'));
 assert.deepEqual(groups,['Search','Not filed yet','Private'],'workstreams, then the unfiled, then private last');
 assert.match(screen.getByRole('group',{name:'Private'}).textContent,/Home doesn’t read these\./);
 // Negative: nothing private, no Private group; a group with something waiting moves up.
 const order=sidebarThreads([conv('a',{organisationGroup:'Alpha'}),conv('b',{organisationGroup:'Beta',activity:{status:'needs',running:false,step:null,unread:0}})]);
 assert.deepEqual(order.groups.map(g=>g.title),['Beta','Alpha']);
 assert.equal(order.private,null);
 assert.equal(order.needs,1);
 assert.equal(sidebarThreads([conv('p',{visibility:'private',activity:{status:'needs',running:false,step:null,unread:0}})]).needs,0,'a private thread isn’t Home’s to count');
});

test('quiet threads fold oldest first, so the newest quiet ones stay in view',()=>{
 // 19 Sep, map F12: creation order folded the newest quiet threads first.
 const day=86400,now=Math.floor(Date.now()/1000);
 const quiet=(id,daysAgo)=>conv(id,{organisationGroup:'Search',activity:{status:null,running:false,step:null,unread:0,at:now-daysAgo*day}});
 const found=sidebarThreads([quiet('oldest',9),quiet('old',5),quiet('newer',2),quiet('newest',1),conv('undated',{organisationGroup:'Search'})]);
 assert.deepEqual(found.groups[0].threads.map(c=>c.id),['newest','newer','old'],'the three newest quiet threads show');
 assert.deepEqual(found.groups[0].folded.map(c=>c.id),['oldest','undated'],'the oldest and the one with no time fold');
 // Negative: loud rows keep the host's order among themselves, ahead of every quiet one.
 const loud=(id)=>conv(id,{organisationGroup:'Search',activity:{status:'needs',running:false,step:null,unread:0}});
 const mixed=sidebarThreads([quiet('q',1),loud('b'),loud('a')]);
 assert.deepEqual(mixed.groups[0].threads.map(c=>c.id),['b','a','q']);
});

test('a row Dervo changed on its own says so for a few seconds, with Undo, and a move you made says nothing',async()=>{
 // Map, section 5: one calm line where the change lands. Who changed it comes from the host's record (source.kind), so
 // the line never fires on your own move, whatever order the refetch and the undo offer arrive in.
 const before={name:'New conversation',group:null},filed={name:'Sidebar spacing',group:'Design'};
 const state=(kind,extra={})=>({source:{kind},canUndo:true,revision:3,group:'Design',...extra});
 assert.deepEqual(automaticNote(state('home'),{name:'x',group:null},{name:'x',group:'Design'}),{line:'Home filed it under Design.',revision:3});
 assert.deepEqual(automaticNote(state('home'),{name:'x',group:'Bugs'},{name:'x',group:'Design'}),{line:'Home moved it to Design.',revision:3});
 assert.deepEqual(automaticNote(state('chat'),before,filed),{line:'Named and filed under Design from your message.',revision:3});
 assert.deepEqual(automaticNote(state('coding'),before,{name:'Sidebar spacing',group:null}),{line:'Named from your message. Rename it any time.',revision:3});
 assert.deepEqual(automaticNote(state('chat'),{name:'x',group:null},{name:'x',group:'Design'}),{line:'Filed under Design from your message.',revision:3});
 assert.deepEqual(automaticNote(state('guide'),{name:'Sidebar spacing',group:'Design'},{name:'Frosted rows',group:'Design'}),{line:'Renamed by the agent from what it’s working on.',revision:3});
 assert.deepEqual(automaticNote(state('migration'),before,filed),{line:'Named and filed from its first message when Dervo started.',revision:3});
 assert.deepEqual(automaticNote(state('history'),{name:'Sidebar spacing',group:null},filed),{line:'Filed under Design when its history was read.',revision:3});
 assert.equal(automaticNote(state('history'),filed,filed),null);
 assert.deepEqual(automaticNote(state('archive',{group:null}),{name:'x',group:'Design'},{name:'x',group:null}),{line:'Its workstream was archived. Dervo files it again at its next message.',revision:null});
 assert.equal(automaticNote(state('chat',{canUndo:false}),before,filed).revision,null,'no Undo once the record has nothing to undo');
 for(const kind of ['manual','manual-undo','',undefined])assert.equal(automaticNote({source:kind===undefined?null:{kind},canUndo:true,revision:3,group:'Design'},before,filed),null,`your own change (${kind}) gets no line`);
 assert.equal(automaticNote(state('home'),{name:'x',group:'Design'},{name:'y',group:'Design'}),null,'Home only files and moves');
 // In the sidebar: the row changes on a refetch, the host says Home did it, the line shows and Undo goes to the host.
 const calls=[];
 const rows=[conv('a',{name:'Sidebar spacing'}),conv('b',{name:'Other',organisationGroup:'Bugs'})];
 const {view,props}=mount({conversations:rows,projectId:'p'});
 window.__TAURI_INTERNALS__.invoke=async(_,{operation,args})=>{calls.push([operation,args]);
  if(operation==='organisation_get')return {name:'Sidebar spacing',group:'Design',nameLocked:false,groupLocked:true,revision:3,canUndo:true,reason:'',source:{kind:'home'},suggestion:null,peers:[]};
  if(operation==='organisation_undo')return {name:'Sidebar spacing',group:null,revision:4,canUndo:false,source:{kind:'manual-undo'}};
  return operation==='projects_activity'?{}:[];};
 assert.equal(document.querySelector('.dv-sb-row-note'),null,'the first load is never a change');
 view.rerender(React.createElement(DervoSidebar,{...props,conversations:[{...rows[0],organisationGroup:'Design'},rows[1]]}));
 const note=await screen.findByRole('status');
 assert.equal(note.querySelector('span').textContent,'Home filed it under Design.');
 assert.equal(note.dataset.conversationNote,'a');
 assert.ok(note.closest('[aria-label="Design"]'),'the line sits under the row in its new group');
 assert.deepEqual(calls.filter(c=>c[0]==='organisation_get').map(c=>c[1]),[{projectId:'p',channelId:'a'}]);
 fireEvent.click(within(note).getByRole('button',{name:/^Undo/}));
 await waitFor(()=>assert.ok(calls.some(c=>c[0]==='organisation_undo')));
 assert.deepEqual(calls.find(c=>c[0]==='organisation_undo')[1],{projectId:'p',channelId:'a',expectedRevision:3},'undo at the revision the change left');
 await waitFor(()=>assert.equal(screen.queryByRole('status'),null));
 // Negative: the same row change with the host saying you did it draws nothing, and a rename in progress is skipped.
 view.rerender(React.createElement(DervoSidebar,{...props,conversations:[{...rows[0],organisationGroup:'Bugs'},rows[1]]}));
 window.__TAURI_INTERNALS__.invoke=async(_,{operation,args})=>{calls.push([operation,args]);
  if(operation==='organisation_get')return {name:'Sidebar spacing',group:'Bugs',nameLocked:false,groupLocked:true,revision:5,canUndo:true,reason:'',source:{kind:'manual'},suggestion:null,peers:[]};
  return operation==='projects_activity'?{}:[];};
 view.rerender(React.createElement(DervoSidebar,{...props,conversations:[{...rows[0],organisationGroup:'Search'},rows[1]]}));
 await waitFor(()=>assert.ok(calls.filter(c=>c[0]==='organisation_get').length>=2));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(screen.queryByRole('status'),null,'your own move draws no line');
 const gets=calls.filter(c=>c[0]==='organisation_get').length;
 view.rerender(React.createElement(DervoSidebar,{...props,renaming:{id:'a'},conversations:[{...rows[0],name:'Typed by me',organisationGroup:'Search'},rows[1]]}));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(calls.filter(c=>c[0]==='organisation_get').length,gets,'a row being renamed is not asked about');
 assert.ok(NOTE_MS>=5000&&NOTE_MS<=10000,'long enough to read, short enough to leave');
 const css=readFileSync(new URL('./DervoSidebar.css',import.meta.url),'utf8');
 assert.match(css,/\.dv-sb-row-note \{[^}]*color: var\(--dv-muted\);/,'muted ink from the tokens, so it reads in dark mode too');
 assert.doesNotMatch(css.match(/\.dv-sb-row-note[^\n]*/g).join('\n'),/#[0-9a-f]{3,6}\b/i,'no literal colours');
});

test('the open conversation is the frosted row with no bold, holding the accent while it waits on you, and off screen it gets its marks back',()=>{
 mount({conversations:[conv('stuck',{activity:{status:'needs',running:false,step:null,unread:2,attention:true}})],channelId:'stuck'});
 const row=document.querySelector('[data-conversation-id="stuck"]');
 assert.equal(row.getAttribute('aria-current'),'page');
 assert.equal(row.querySelector('.dv-unseen'),null);
 assert.ok(row.querySelector('.dv-st[data-accent]'),'the decision in the main area and this row are the one accent (thread-review.html)');
 cleanup();
 // The same conversation behind Home is not on screen, so its marks return.
 mount({conversations:[conv('stuck',{activity:{status:'needs',running:false,step:null,unread:2,attention:true}})],channelId:'stuck',conversationActive:false});
 const off=document.querySelector('[data-conversation-id="stuck"]');
 assert.ok(off.querySelector('.dv-st[data-accent]'));
 assert.ok(off.querySelector('.dv-unseen'));
 assert.equal(off.getAttribute('aria-current'),null);
});

test('the thread you are in holds the one accent when it waits on you; otherwise the first one asking does',()=>{
 // 19 Sep, merged thread screen: the open handoff drew rust in the main area and another thread drew rust in the sidebar.
 const rows=[conv('ask',{organisationGroup:'W',activity:{status:'needs',running:false,step:null,unread:1,attention:true,ended:'asking'}}),
  conv('open',{organisationGroup:'W',activity:{status:'needs',running:false,step:null,unread:0,attention:true,ended:'blocked'}})];
 mount({conversations:rows,channelId:'open',conversationActive:true});
 const rust=[...document.querySelectorAll('.dv-sb-thread')].filter(row=>row.querySelector('.dv-st[data-accent]')).map(row=>row.getAttribute('data-conversation-id'));
 assert.deepEqual(rust,['open']);
 cleanup();
 mount({conversations:rows,channelId:'ask',conversationActive:false});
 const rustHome=[...document.querySelectorAll('.dv-sb-thread')].filter(row=>row.querySelector('.dv-st[data-accent]')).map(row=>row.getAttribute('data-conversation-id'));
 assert.deepEqual(rustHome,['ask']);
});

test('personal and work stay separate groups; Claude and Codex on the same kind of account share one',()=>{
 const rows=[conv('a',{organisationGroup:'Search'}),conv('b',{organisationGroup:'Search',profile_id:'two'}),conv('c',{organisationGroup:'Search'}),conv('d'),conv('e',{organisationGroup:'Search',activity:{status:'needs',running:false,step:null,unread:0}})];
 const original=structuredClone(rows);
 // 19 Sep: two personal accounts (Claude and Codex) drew "Search · Codex · one@..." and "Search · Claude Code · two@...".
 const same=sidebarThreads(rows,{one:'personal',two:'personal'});
 assert.deepEqual(rows,original);
 assert.deepEqual(same.groups.map(g=>[g.title,[...g.threads,...g.folded].map(c=>c.id).sort()]),[['Search',['a','b','c','e']],['Not filed yet',['d']]]);
 const split=sidebarThreads(rows,{one:'personal',two:'work'});
 assert.deepEqual(split.groups.map(g=>[g.title,g.threads.map(c=>c.id)]),[['Search · Personal',['e','a','c']],['Search · Work',['b']],['Not filed yet',['d']]]);
 assert.equal(sidebarThreads([conv('solo',{organisationGroup:'Search'})]).groups[0].title,'Search');
 mount({conversations:rows,accountKinds:{one:'personal',two:'work'}});
 const group=screen.getByRole('group',{name:'Search · Personal'});
 assert.deepEqual([...group.querySelectorAll('.dv-name')].map(n=>n.textContent),['e','a','c']);
 assert.ok(screen.getByRole('group',{name:'Search · Work'}));
});

test('each row offers the thread actions from ⋯ and from right-click, and the row stays lifted while its menu is open',async()=>{
 const {calls}=mount({conversations:[conv('alpha'),conv('beta')]});
 await openMenu(screen.getByRole('button',{name:'Options for beta'}));
 const items=await screen.findAllByRole('menuitem');
 // Pin tops the menu (b16/lifecycle): "keep this", so Dervo never tidies it away.
 assert.deepEqual(items.map(i=>i.textContent),['PinNever tidied away','Rename','Move to workstream','Archive⌘⇧A','Delete…']);
 assert.ok(document.querySelector('[data-conversation-id="beta"]').classList.contains('dv-held'));
 fireEvent.click(screen.getByRole('menuitem',{name:'Rename'}));
 assert.deepEqual(calls.at(-1),['rename','beta']);
 cleanup();
 const second=mount({conversations:[conv('alpha'),conv('beta')]});
 fireEvent.contextMenu(document.querySelector('[data-conversation-id="alpha"]'));
 fireEvent.click(await screen.findByRole('menuitem',{name:'Archive'}));
 assert.deepEqual(second.calls.at(-1),['archive','alpha']);
});

test('a new thread is a quiet row at the foot of the list that asks whether Home can see it',async()=>{
 const {calls}=mount();
 const trigger=screen.getByRole('button',{name:'New thread'});
 assert.ok(trigger.classList.contains('dv-sb-item'));
 await openMenu(trigger);
 fireEvent.click(await screen.findByRole('menuitem',{name:/^Private/}));
 assert.deepEqual(calls.at(-1),['create',true]);
 await openMenu(trigger);
 fireEvent.click(await screen.findByRole('menuitem',{name:/^Shared/}));
 assert.deepEqual(calls.at(-1),['create',false]);
});

test('usage lines name the provider and the account, show what’s left, and say only what the provider reported',()=>{
 const now=Date.parse('2026-09-17T14:00:00');
 const codex=usageLine(account({basis:'rate_limits',percent:84,low:true,resets_at:Math.floor(now/1000)+3600,window_mins:300,runs_today:2,rate_limits:{planType:'plus'}}),now);
 assert.equal(codex.provider,'Codex');assert.equal(codex.which,'ChatGPT Plus');
 assert.equal(codex.percent,16);assert.equal(codex.value,'16%');assert.equal(codex.left,'16% left');assert.equal(codex.low,true);
 assert.match(codex.summary,/^16% of the 5-hour window left, resets /);assert.match(codex.detail,/^Codex · ChatGPT Plus · bryce@example.test: 16% of/);
 assert.equal(codex.note,'ChatGPT Plus','the row is one line (item 8): the note is the account, the reset is in the summary');
 assert.match(codex.summary,/resets \d.*\. 2 runs today\.$/,'a reset later today shows the time only, and the runs follow');
 // Bryce, 17 Sep: Codex was used up and the bar said 100%.
 const spent=usageLine(account({basis:'rate_limits',percent:100,low:true,resets_at:Math.floor(now/1000)+4*86400,window_mins:10080}),now);
 assert.equal(spent.value,'0%');assert.equal(spent.percent,0);assert.equal(spent.note,'ChatGPT','a login without a reported plan is a ChatGPT login');assert.match(spent.summary,/resets [A-Z][a-z]{2} /,'a later reset names the day');
 // A reset on the hour reads without minutes, so the line fits the sidebar.
 const onTheHour=usageLine(account({basis:'rate_limits',percent:36,resets_at:Math.floor(Date.parse('2026-09-23T22:00:00')/1000),window_mins:10080}),now);
 assert.equal(onTheHour.note,'ChatGPT');assert.match(onTheHour.summary.replace(/\u202f/g,' '),/resets Wed 10 PM\./);
 const odd=usageLine(account({basis:'rate_limits',percent:10,resets_at:300}),now);
 assert.equal(odd.summary,'90% of the window left. 0 runs today.','the runs come after the window now the line has no room for them');
 // Two Claude rows tell themselves apart by the account (Bryce, 18 Sep: two rows both said Claude).
 const key=usageLine(account({provider:'claude',method:'api_key',basis:'spend_limit',percent:25,cost_month_usd:12.5,monthly_limit_usd:50}),now);
 assert.equal(key.provider,'Claude');assert.equal(key.which,'API key');assert.equal(key.note,'API key','the spend is in the summary, on hover');assert.equal(key.value,'75%');assert.match(key.summary,/About \$12\.50 of \$50\.00 spent this month/);
 const sub=usageLine(account({provider:'claude',runs_today:1,tokens_today:12345}),now);
 assert.equal(sub.provider,'Claude');assert.equal(sub.which,'Personal');assert.equal(sub.percent,null);assert.equal(sub.runs,'1 run today');assert.equal(sub.low,false);
 assert.equal(sub.note,'Personal','runs only: the row shows them beside the provider, so the note is the account alone (UX6)');
 assert.match(sub.summary,/^1 run today, 12,345 tokens\. Claude Code reports how much is left during its next run\./);
 const claude=usageLine(account({provider:'claude',basis:'rate_limits',percent:35,window_mins:10080,runs_today:3}),now);
 assert.equal(claude.value,'65%');assert.equal(claude.note,'Personal');assert.match(claude.summary,/3 runs today\.$/,'a bar row says its runs in the summary');
 assert.equal(accountLabel({provider:'codex',method:'subscription',rate_limits:{planType:'PRO'}}),'ChatGPT Pro');
 assert.equal(accountLabel({provider:'codex',method:'subscription',rate_limits:{planType:'prolite'}}),'ChatGPT Pro Lite','the $100 Pro plan (Josh, feedback #9)');
 assert.equal(accountLabel({provider:'codex',method:'api_key',rate_limits:null}),'API key');
 // A percentage without a known basis is never drawn.
 assert.equal(usageLine(account({percent:50,basis:'runs'})).percent,null);
});

test('with three accounts every usage line is one line: the account’s name, which gives way first; the reset stays in the title (item 8)',()=>{
 const now=Date.parse('2026-09-19T14:00:00');
 const at=iso=>Math.floor(Date.parse(iso)/1000);
 const lines=[
  account({profile_id:'cs',provider:'claude',basis:'rate_limits',percent:36,window_mins:10080,resets_at:at('2026-09-23T22:00:00')}),
  account({profile_id:'cw',provider:'claude',name:'Acme',basis:'rate_limits',percent:62,window_mins:10080,resets_at:at('2026-09-25T09:00:00')}),
  account({profile_id:'cx',provider:'codex',basis:'rate_limits',percent:41,window_mins:10080,resets_at:at('2026-09-26T00:00:00'),rate_limits:{planType:'plus'}}),
 ].map(a=>usageLine(a,now).note.replace(/\u202f/g,' '));
 assert.deepEqual(lines,['Personal','Acme','ChatGPT Plus']);
 assert.equal(accountLabel({provider:'claude',method:'subscription',name:'  '}),'Personal','a blank name falls back to the account kind');
 // The full wording stays in the row's title and for screen readers.
 assert.match(usageLine(account({provider:'claude',basis:'rate_limits',percent:36,window_mins:10080,resets_at:at('2026-09-23T22:00:00')}),now).summary,/resets Wed 10/);
 const css=readFileSync(new URL('./DervoComponents.css',import.meta.url),'utf8');
 const rule=css.match(/\.dv-usage small \{[^}]*\}/g)??[];
 assert.ok(rule.length>0);
 for(const block of rule)assert.match(block,/text-overflow: ellipsis/,'the account name gives way with an ellipsis so the row stays one line; the title has it whole');
 assert.match(css,/\.dv-usage \{[^}]*grid-template-columns: auto minmax\(0, 1fr\) 44px auto;/,'provider, account, bar, percent on one row');
});

test('usage is one line even with a low or unmeasured account (app #13): the low figure is marked and a click opens usage',()=>{
 const {calls}=mount({accounts:[account({profile_id:'x',basis:'rate_limits',percent:84,low:true,rate_limits:{planType:'plus'}}),account({profile_id:'y',provider:'claude',runs_today:4}),account({profile_id:'z',basis:'rate_limits',percent:30})]});
 const block=screen.getByTestId('dervo-sidebar-usage');
 const buttons=[...block.querySelectorAll('button.dv-usage')];
 assert.equal(buttons.length,1,'one line, however many accounts');
 const seen=[...buttons[0].children].filter(el=>!el.classList.contains('sr-only')).map(el=>el.textContent).join('');
 assert.equal(seen,'Codex 16%·Claude 4 runs','the lower Codex figure; a runs-only account says its runs, and no "Left" that would call them left');
 assert.equal(buttons[0].dataset.low,'true');
 assert.deepEqual([...buttons[0].querySelectorAll('.dv-usage-part')].map(part=>part.dataset.low),['true',undefined]);
 assert.equal(buttons[0].querySelectorAll('.dv-bar').length,0);
 assert.match(buttons[0].title,/Codex · ChatGPT Plus[^\n]*16% of the/,'hover has every account in full');
 fireEvent.click(buttons[0]);assert.equal(calls.at(-1)[0],'usage');
});

test('the sidebar is the mockup’s: dv-sb classes on the wash, one column that never widens into a sideways scroll',()=>{
 mount({conversations:[conv('a',{organisationGroup:'Search'})]});
 const nav=screen.getByTestId('dervo-sidebar');
 assert.equal(nav.tagName,'NAV');assert.ok(nav.classList.contains('dv-sb'));
 assert.ok(nav.querySelector('.dv-sb-project'));assert.ok(nav.querySelector('.dv-sb-search'));assert.ok(nav.querySelector('.dv-sb-list .dv-sb-group .dv-sb-item.dv-sb-thread'));assert.ok(nav.querySelector('.dv-sb-foot .dv-me'));
 const css=readFileSync(new URL('./DervoSidebar.css',import.meta.url),'utf8');
 assert.match(css,/\.dv-sb-list \{[^}]*grid-template-columns: minmax\(0, 1fr\);[^}]*overflow-x: hidden;/);
 assert.match(css,/\.dv-sb-group \{[^}]*grid-template-columns: minmax\(0, 1fr\);/);
 const components=readFileSync(new URL('./DervoComponents.css',import.meta.url),'utf8');
 // The wash is off (design, 8 Oct): the selected row is a raised surface with a hairline edge, from the shared tokens.
 assert.match(components,/\.dv-sb-item\[aria-current="page"\] \{ background: var\(--dv-selected\);[^}]*box-shadow: var\(--dv-selected-edge\);/,'the selected row is raised, with its edge');
 // UX4: "N more" keeps its words at the 8px inset but half of it is padding, so the focus ring has room and isn't clipped.
 assert.match(css,/\.dv-sb-fold \{[^}]*margin: 0 var\(--dv-s1\) var\(--dv-s1\);[^}]*padding: 2px var\(--dv-s1\);/);
 assert.match(css,/\.dv-sb-fold:focus-visible \{ outline: 2px solid var\(--dv-focus\); outline-offset: 2px; \}/,'ink at 60%, never rust (design review 23 Sep)');
 assert.doesNotMatch(css,/\.dv-sb-fold \{[^}]*padding: 2px 0;/,'no zero side padding: that put the ring on the text');
});

test('shell polish for beta.11: a disabled send fades, the empty thread sits in the composer column, View image hangs right under your request',()=>{
 const components=readFileSync(new URL('./DervoComponents.css',import.meta.url),'utf8');
 // UX1: Home's send is disabled with an empty composer and must look it, like the thread's send already does. Dark
 // audit #10 (19 Sep): disabled is a sunk fill with muted text, not a faded pine that still reads as pressable.
 assert.match(components,/:is\(\.dv-btn-primary, \.dv-btn-send\):disabled \{ background: var\(--dv-sunk\); color: var\(--dv-muted\); opacity: 1; cursor: not-allowed; box-shadow: none; \}/,'and loses the lit edge (design report, 23 Sep, #5)');
 assert.match(components,/\[data-testid="send-message"\][^\n]*:disabled \{ background: var\(--dv-sunk\); color: var\(--dv-muted\); opacity: 1; cursor: not-allowed; box-shadow: none; \}/,'the thread send keeps the same disabled look');
 assert.doesNotMatch(components,/send[^\n]*:disabled \{ opacity: \.55; \}/,'no send fades any more');
 // UX3: the empty state takes the composer's column: 748px, centred, and the same side inset when the window is narrow.
 assert.match(components,/\[data-testid="message-empty"\] \{ width: calc\(100% - 2 \* var\(--dv-s4\)\); max-width: calc\(700px \+ 2 \* var\(--dv-s5\)\); margin-inline: auto; \}/);
 assert.match(components,/\.dv-composer \{[^}]*width: min\(748px, calc\(100% - 2 \* var\(--dv-s5\)\)\); margin: 0 auto/,'the composer these numbers match');
 // #13: the row wrapper before the footer is yours, so View image goes to the bubble's edge; an agent's row leaves it alone.
 assert.match(components,/:has\(> \[data-pubkey="local-user"\]\) ~ \.dervo-sent-images \{ justify-content: flex-end; \}/);
 assert.doesNotMatch(components,/\n[^\n~]*\.dervo-sent-images \{[^}]*justify-content: flex-end/,'only after your own row, never for every footer');
 // Inside the timeline's 8px shell the gutter is s5 less 8px, so View image shares the report's and the recap's edge.
 assert.match(components,/\.dervo-sent-images \{ width: 100%; max-width: calc\(700px \+ 2 \* var\(--dv-row-gutter, var\(--dv-s5\)\)\); margin-inline: auto; padding-inline: var\(--dv-row-gutter, var\(--dv-s5\)\); \}/);
 assert.match(components,/\[data-timeline-scroll-shell\] \{ --dv-row-gutter: calc\(var\(--dv-s5\) - var\(--dv-s2\)\); \}/);
});

test('account menu: who is signed in, settings, connected accounts, feedback, and sign out only when there is a session, confirmed in the menu',async()=>{
 const signed=mount({canSignOut:true,accountName:'Bryce'});
 const me=screen.getByRole('button',{name:'Your account'});
 assert.match(me.textContent,/Bryce/);assert.match(me.textContent,/Private beta/);
 for(const [label,call] of [['Settings','settings'],['Connected accounts','accounts'],['Releases','releases'],['Send feedback','feedback']]){
  await openMenu(me);fireEvent.click(await screen.findByRole('menuitem',{name:label}));assert.equal(signed.calls.at(-1)[0],call);
 }
 await openMenu(me);
 assert.match(screen.getByRole('menu',{name:'Your account'}).textContent,/Signed in asBryce/);
 fireEvent.click(await screen.findByRole('menuitem',{name:'Sign out…'}));
 assert.equal(signed.calls.some(c=>c[0]==='signout'),false);
 const dialog=await screen.findByRole('alertdialog');
 assert.ok(dialog.closest('[role="menu"]'),'the menu turns into its own confirm, in the same place');
 assert.match(dialog.textContent,/Only your Dervo session is removed/);
 await waitFor(()=>assert.ok(document.activeElement===within(dialog).getByRole('menuitem',{name:'Cancel'}),'the way out is first and focused'));
 fireEvent.click(within(dialog).getByRole('menuitem',{name:'Sign out'}));
 await waitFor(()=>assert.deepEqual(signed.calls.at(-1),['signout']));
 await waitFor(()=>assert.equal(screen.queryByRole('alertdialog'),null));
 cleanup();
 mount({canSignOut:false});
 await openMenu(screen.getByRole('button',{name:'Your account'}));
 await screen.findByRole('menuitem',{name:'Settings'});
 assert.equal(screen.queryByRole('menuitem',{name:'Sign out…'}),null);
 cleanup();
 // The owner-only Releases entry is left out when the shell doesn't offer it.
 mount({onReleases:undefined});
 await openMenu(screen.getByRole('button',{name:'Your account'}));
 await screen.findByRole('menuitem',{name:'Settings'});
 assert.equal(screen.queryByRole('menuitem',{name:'Releases'}),null);
});

test('a failed sign-out keeps the confirm open with the reason',async()=>{
 mount({canSignOut:true,onSignOut:async()=>{throw new Error('No verified Dervo session is available to sign out');}});
 await openMenu(screen.getByRole('button',{name:'Your account'}));
 fireEvent.click(await screen.findByRole('menuitem',{name:'Sign out…'}));
 fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('menuitem',{name:'Sign out'}));
 assert.match((await screen.findByRole('alert')).textContent,/No verified Dervo session/);
 assert.ok(screen.getByRole('alertdialog'));
});

test('opening a project lands on the orchestrator, and only falls back to a conversation when it cannot open',()=>{
 const quiet=[conv('a'),conv('b')];
 const needs=[conv('a',{activity:{status:'needs',running:false,step:null,unread:0}})];
 // The front door, whatever changed: it lists what's waiting for you itself.
 for(const rows of [quiet,needs])for(const channelId of ['','a'])
  assert.deepEqual(projectLanding(rows,{ready:true,channelId,last:'b'}),{screen:'orchestrator'});
 // Without readable project records the orchestrator can't open: stay put, or go to the last conversation.
 assert.equal(projectLanding(quiet,{ready:false,channelId:'',last:'b'}).channel,'b');
 assert.equal(projectLanding(quiet,{ready:false,channelId:'a',last:'b'}),null);
 assert.equal(projectLanding(quiet,{ready:false,channelId:'',last:'gone'}),null);
});

test('the shell mounts the sidebar on the wash with the window chrome above it, marks a conversation seen when it is on screen, and lands through projectLanding',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/<DervoSidebar /);
 assert.match(shell,/<SidebarProvider className="dv-window /,'the window carries the wash');
 assert.match(shell,/<Sidebar collapsible="offcanvas" data-testid="app-sidebar">\s*<AppTopChrome /,'the traffic-light row sits at the top of the sidebar column');
 assert.match(shell,/<main ref=\{guideMainRef\} className="dervo-connected-main dv-main /,'the main panel is flat canvas');
 for(const retired of ['<GradientLayer','<ContentSurface','<DervoWordmark','dervo-workspace-brand','<ChatHeader'])assert.equal(shell.includes(retired),false,retired);
 assert.match(shell,/const next=projectLanding\(conversations\.data,\{ready:projectRecordsReady,channelId,last:lastConversation\(projectId\)\}\);/);
 assert.match(shell,/if\(next&&'screen' in next\)openOrchestrator\(\);/);
 assert.match(shell,/if\(action==='orchestrator'\)\{openOrchestrator\(\);return;\}/);
 // One screen takes the main panel at a time, so a conversation is only on screen when none of them is open.
 assert.match(shell,/const panel=searchOpen\|\|knowledgeOpen\|\|bugsOpen\|\|settingsOpen\|\|releasesOpen\|\|!!historyAt;/);
 assert.match(shell,/const onScreen=!!current&&!panel;/);
 assert.match(shell,/void conversationSeen\(projectId,current\.id\)/);
 assert.match(shell,/useEffect\(\(\)=>\{if\(current\)rememberConversation\(projectId,current\.id\);\},\[projectId,current\?\.id\]\);/);
 assert.match(shell,/function openProject\(id:string\)\{if\(id!==projectId\)\{setChannel\(''\);inspectors\.navigateAway\(\);landing\.current=id;\}setProject\(id\);\}/);
 for(const retired of ['<ProjectPicker','<ProjectTools','<SidebarSection','conversationSections('])assert.equal(shell.includes(retired),false,retired);
 const api=readFileSync(new URL('./api.ts',import.meta.url),'utf8');
 assert.match(api,/local<\{id:string\}>\('conversation_seen',\{projectId,channelId\}\)/);
 assert.match(api,/local<UsageSummary>\('usage_summary'\)/);
 assert.match(api,/local<RoutePolicy>\('coding_route_set',\{projectId,channelId,autoSwitch\}\)/);
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['conversation_seen','usage_summary','coding_route_set'])assert.ok(bridge.includes(`"${op}"`),op);
});

test('two unnamed Claude accounts show their sign-in in the sidebar, not "Personal" twice', () => {
 // beta.9: Bryce's two Claude logins both read "Personal" under the usage bars.
 const rows=[account({profile_id:'c1',provider:'claude',identity:'bryce@personal.test'}),account({profile_id:'c2',provider:'claude',identity:'bryce@work.test'}),
  account({profile_id:'x1',provider:'codex',identity:'bryce@personal.test'})];
 const labels=usageLabels(rows);
 assert.deepEqual([labels.get('c1'),labels.get('c2'),labels.get('x1')],['bryce@personal.test','bryce@work.test','ChatGPT']);
 assert.equal(usageLine(rows[1],Date.now(),labels.get('c2')).note,'bryce@work.test','a runs-only row: the sign-in alone, the runs sit beside the provider (UX6)');
 assert.equal(usageLine({...rows[1],basis:'rate_limits',percent:20},Date.now(),labels.get('c2')).note,'bryce@work.test','a bar row is the sign-in alone too; the reset is in its title');
 // A name you gave wins, and one unnamed account alone keeps the short label.
 const named=usageLabels([{...rows[0],name:'Acme'},rows[1]]);
 assert.deepEqual([named.get('c1'),named.get('c2')],['Acme','Personal']);
});

test('Next up is one click from the sidebar and opens as a page, not a panel', async()=>{
  const {readFile}=await import('node:fs/promises');
  const sidebar=await readFile(new URL('./DervoSidebar.tsx',import.meta.url),'utf8');
  const app=await readFile(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
  // Beta.16 (b16/lifecycle): Next up takes Bugs and ideas' place and action; ConnectedApp opens it by the old name.
  assert.match(sidebar,/onAction\('bugs',event\.currentTarget\)/);
  assert.match(sidebar,/aria-current=\{active==='bugs'\|\|active==='reports'\?'page':undefined\}/);
  assert.match(app,/if\(action==='bugs'\)\{[^}]*setBugsOpen\(true\)/);
  assert.match(app,/<NextUp key=\{`next-up:\$\{projectId\}`\}[^\n]*open=\{bugsOpen\}/);
  // Negative control: every navigation that closes Knowledge also closes Bugs and ideas, so it never lingers under a thread.
  const others=app.replace(/if\(action==='bugs'\)\{[^\n]*\n/,'').replace(/function openReport\([^]*?\n  \}/,'');  // the one place that closes Knowledge to open Bugs and ideas
  const knowledgeCloses=(others.match(/setKnowledgeOpen\(false\);/g)||[]).length;
  const bugsCloses=(others.match(/setKnowledgeOpen\(false\);setBugsOpen\(false\);/g)||[]).length;
  assert.ok(knowledgeCloses>0);
  assert.equal(bugsCloses,knowledgeCloses);
});

test('a stop you pressed reads as "You paused this", not an error (handoff-phase2 item 11)',()=>{
 assert.deepEqual(threadGlyph({status:null,ended:'stopped',unread:1,running:false}),{state:'stopped',label:'You paused this'});
 // Negative controls: a stop with an error still says so, and a seen stop is quiet.
 assert.deepEqual(threadGlyph({status:'needs',ended:'blocked',unread:1,running:false}),{state:'failed',label:'Stopped with an error'});
 assert.deepEqual(threadGlyph({status:null,ended:'stopped',unread:0,running:false}),{state:'idle',label:'Idle'});
});

// ---------- Fresh-eyes item 8: a sidebar that holds 20 threads ----------
const DAY=86400;
const nowS=()=>Math.floor(Date.now()/1000);
const quietAt=(id,group,daysAgo,extra={})=>conv(id,{organisationGroup:group,activity:{status:null,running:false,step:null,unread:0,at:nowS()-daysAgo*DAY,...extra}});

test('a thread is stale after three quiet days from the host’s time, and never while it runs, needs you, is unopened or has no time',()=>{
 assert.equal(STALE_DAYS,3);
 const now=Date.now(),at=Math.floor(now/1000)-STALE_DAYS*DAY;
 assert.equal(staleThread({status:null,running:false,step:null,unread:0,at},now),true,'exactly three days is stale');
 assert.equal(staleThread({status:null,running:false,step:null,unread:0,at:at+1},now),false,'a second short of three days is not');
 assert.equal(staleThread({status:null,running:false,step:null,unread:0,at:at-30*DAY},now),true);
 // Negative controls: the loud states are never stale, however old their time.
 assert.equal(staleThread({status:'running',running:true,step:'Running checks',unread:0,at:at-DAY},now),false,'running');
 assert.equal(staleThread({status:'needs',running:false,step:null,unread:0,at:at-DAY},now),false,'needs you');
 assert.equal(staleThread({status:'needs',running:false,step:null,unread:0,at:at-DAY,attention:false},now),false,'needs you, even quietly');
 assert.equal(staleThread({status:null,running:false,step:null,unread:2,at:at-DAY},now),false,'unopened results');
 assert.equal(staleThread({status:null,running:false,step:null,unread:0},now),false,'no time: nothing says how long it has been quiet');
 assert.equal(staleThread({status:null,running:false,step:null,unread:0,at:null},now),false);
 assert.equal(staleThread(null,now),false,'no record at all');
 assert.equal(staleThread(undefined,now),false);
 // A group is stale only when every thread in it is.
 const old=(id)=>quietAt(id,'Old',5);
 assert.equal(staleGroup({threads:[old('a'),old('b')],folded:[old('c')]},now),true);
 assert.equal(staleGroup({threads:[old('a'),quietAt('r','Old',5,{status:'running',running:true})],folded:[old('c')]},now),false,'one running thread keeps the group');
 assert.equal(staleGroup({threads:[old('a')],folded:[quietAt('f','Old',1)]},now),false,'one fresh folded thread keeps the group');
 assert.equal(staleGroup({threads:[],folded:[]},now),false,'an empty group is nothing to fold');
});

test('a workstream quiet for three days folds to its header with the count, opens on a click, and folds back',()=>{
 mount({conversations:[quietAt('r1','Releases',4),quietAt('r2','Releases',5),quietAt('r3','Releases',9)]});
 const group=screen.getByRole('group',{name:'Releases'});
 assert.equal(group.dataset.stale,'true');
 assert.equal(group.querySelectorAll('.dv-sb-thread').length,0,'no rows: the header alone');
 const head=within(group).getByRole('button',{name:/^Releases/});
 assert.equal(head.getAttribute('aria-expanded'),'false');
 assert.equal(head.querySelector('.dv-label').textContent,'Releases');assert.equal(head.querySelector('.dv-count').textContent,'3','the count stays on the folded header');
 assert.match(head.title,/^Quiet for 3 days\. Show all 3$/);
 assert.ok(head.querySelector('svg'),'a chevron says it is closed');
 fireEvent.click(head);
 assert.deepEqual([...group.querySelectorAll('.dv-name')].map(n=>n.textContent),['r1','r2','r3'],'open, every thread shows');
 assert.equal(group.dataset.stale,undefined);
 fireEvent.click(within(group).getByRole('button',{name:'Show fewer'}));
 assert.equal(group.querySelectorAll('.dv-sb-thread').length,0,'folded again');
 assert.ok(within(group).getByRole('button',{name:/^Releases/}));
});

test('a running thread, a needs-you thread or an unopened one inside an otherwise idle workstream keeps it open and stays visible',()=>{
 const idle=[quietAt('r1','Releases',4),quietAt('r2','Releases',5),quietAt('r3','Releases',9)];
 const cases=[
  ['running',quietAt('live','Releases',6,{status:'running',running:true,step:'Running checks'}),'running'],
  ['needs you',quietAt('ask','Releases',6,{status:'needs',ended:'asking'}),'needs'],
  ['unopened',quietAt('fresh','Releases',6,{unread:1}),'done'],
 ];
 for(const [what,loud,state] of cases){
  const {view}=mount({conversations:[...idle,loud]});
  const group=screen.getByRole('group',{name:'Releases'});
  assert.equal(group.dataset.stale,undefined,`${what}: the group does not fold`);
  const row=group.querySelector(`[data-conversation-id="${loud.id}"]`);
  assert.ok(row,`${what}: its row is on screen`);
  assert.equal(glyphOf(row).dataset.state,state);
  assert.equal(group.querySelectorAll('.dv-name').length,QUIET_SHOWN,`${what}: the loud row plus the usual quiet ones; the rest fold behind "N more" as ever`);
  assert.ok(within(group).getByRole('button',{name:'1 more'}));
  assert.equal(within(group).queryByRole('button',{name:/^Releases/}),null,'the header is not a button');
  view.unmount();
 }
 // Negative control: the same group with the loud thread gone folds.
 mount({conversations:idle});
 assert.equal(screen.getByRole('group',{name:'Releases'}).dataset.stale,'true');
});

test('the open thread, a rename in progress and a row that just left keep a stale workstream open; a thread two days quiet does too',()=>{
 const idle=[quietAt('r1','Releases',4),quietAt('r2','Releases',5)];
 const {view}=mount({conversations:idle,channelId:'r2'});
 let group=screen.getByRole('group',{name:'Releases'});
 assert.equal(group.dataset.stale,undefined,'you are in r2');
 assert.ok(group.querySelector('[data-conversation-id="r2"][aria-current="page"]'));
 view.unmount();
 mount({conversations:idle,renaming:{id:'r1'}});
 group=screen.getByRole('group',{name:'Releases'});
 assert.equal(group.dataset.stale,undefined,'a rename is in progress');
 assert.ok(group.querySelector('input.dv-rename'));
 cleanup();
 mount({conversations:[...idle,quietAt('r3','Releases',2)]});
 group=screen.getByRole('group',{name:'Releases'});
 assert.equal(group.dataset.stale,undefined,'two days quiet is not three');
 assert.equal(group.querySelectorAll('.dv-name').length,3);
 cleanup();
 mount({conversations:[...idle,conv('r4',{organisationGroup:'Releases'})]});
 assert.equal(screen.getByRole('group',{name:'Releases'}).dataset.stale,undefined,'a thread with no time is not called stale');
});

test('twenty threads: stale workstreams are headers, the live ones stay in view; one thread, no workstreams, all idle and all running each degrade sensibly',()=>{
 // Two live workstreams, two stale ones, three unfiled: twenty threads.
 const rows=[
  quietAt('ideas','Orchestrator',0,{status:'needs',unread:1,ended:'asking'}),quietAt('picker','Orchestrator',0,{status:'needs',unread:1,ended:'blocked'}),quietAt('board','Orchestrator',0,{unread:1}),quietAt('o4','Orchestrator',1),quietAt('o5','Orchestrator',2),
  quietAt('slow','Connectors',0,{status:'running',running:true,step:'Reading the connectors list'}),quietAt('c2','Connectors',4),quietAt('c3','Connectors',5),
  quietAt('beta','Releases',4),quietAt('restart','Releases',5),quietAt('notes','Releases',6),quietAt('feedback','Releases',8),quietAt('testers','Releases',9),
  quietAt('queue','Knowledge',5),quietAt('lessons','Knowledge',7),quietAt('memory','Knowledge',11),quietAt('skills','Knowledge',12),
  quietAt('n1',null,1),quietAt('n2',null,1),quietAt('n3',null,2),
 ];
 assert.equal(rows.length,20);
 mount({conversations:rows});
 const names=g=>[...screen.getByRole('group',{name:g}).querySelectorAll('.dv-name')].map(n=>n.textContent);
 assert.deepEqual(names('Orchestrator'),['ideas','picker','board'],'three loud rows leave no quiet slot, so the two quiet ones are behind "2 more"');
 assert.ok(within(screen.getByRole('group',{name:'Orchestrator'})).getByRole('button',{name:'2 more'}));
 assert.deepEqual(names('Connectors'),['slow','c2','c3'],'the running thread keeps Connectors open, with its four-day-old siblings');
 for(const stale of ['Releases','Knowledge']){
  const group=screen.getByRole('group',{name:stale});
  assert.equal(group.dataset.stale,'true',`${stale} folds to its header`);
  assert.equal(group.querySelectorAll('.dv-sb-thread').length,0);
 }
 assert.equal(screen.getByRole('group',{name:'Releases'}).querySelector('.dv-count').textContent,'5');
 assert.deepEqual(names('Not filed yet'),['n1','n2','n3']);
 assert.equal(document.querySelectorAll('.dv-sb-thread').length,9,'nine rows on screen out of twenty, every live one among them');
 assert.deepEqual([...document.querySelectorAll('.dv-sb-group')].map(g=>g.getAttribute('aria-label')),['Orchestrator','Connectors','Releases','Knowledge','Not filed yet'],'stale groups keep their place, they do not sink');
 cleanup();
 // One thread: a row, no fold of any kind.
 mount({conversations:[quietAt('only','Orchestrator',0)]});
 assert.equal(document.querySelectorAll('.dv-sb-thread').length,1);assert.equal(document.querySelector('[data-stale]'),null);assert.equal(document.querySelector('.dv-sb-fold'),null);
 cleanup();
 // No workstreams, all stale: "Not filed yet" folds like any other group.
 mount({conversations:[quietAt('a',null,4),quietAt('b',null,5)]});
 assert.equal(screen.getByRole('group',{name:'Not filed yet'}).dataset.stale,'true');
 cleanup();
 // Every thread idle for a week: every group is a header, and the count still says what is there.
 mount({conversations:rows.map(c=>({...c,activity:{...c.activity,status:null,running:false,unread:0,at:nowS()-7*DAY}}))});
 assert.equal(document.querySelectorAll('.dv-sb-thread').length,0);
 assert.deepEqual([...document.querySelectorAll('.dv-sb-group[data-stale] .dv-count')].map(n=>n.textContent),['5','3','5','4','3']);
 cleanup();
 // Every thread running: nothing folds anywhere, all twenty show.
 mount({conversations:rows.map(c=>({...c,activity:{...c.activity,status:'running',running:true,unread:0}}))});
 assert.equal(document.querySelectorAll('.dv-sb-thread').length,20);
 assert.equal(document.querySelector('[data-stale]'),null);assert.equal(document.querySelector('.dv-sb-fold'),null);
 // Private threads fold the same way, and a private thread that needs you does not.
 cleanup();
 mount({conversations:[quietAt('p1',null,4,{}),conv('p2',{visibility:'private',activity:{status:null,running:false,step:null,unread:0,at:nowS()-4*DAY}})]});
 assert.equal(screen.getByRole('group',{name:'Private'}).dataset.stale,'true');
});

test('usage is one line whenever there are accounts, and each account counts its tightest limit',()=>{
 const rate=(percent,extra={})=>account({basis:'rate_limits',percent,...extra});
 assert.equal(usageFold([rate(36),rate(41)]),'one');
 assert.equal(usageFold([rate(51),rate(41,{low:true})]),'one','low no longer unfolds it');
 assert.equal(usageFold([rate(36),account({provider:'claude',runs_today:3})]),'one','nor does an unmeasured account');
 assert.equal(usageFold([rate(36)]),'one');
 assert.equal(usageFold([]),'rows','nothing to show');
 assert.deepEqual(tightestLeft(rate(20,{windows:[{window_mins:300,percent:91},{window_mins:10080,percent:40}]})),{left:9,low:true,window:'5h'},'the 5-hour window is the tightest, and 91% used is low');
 assert.deepEqual(tightestLeft(account({basis:'spend_limit',percent:25})),{left:75,low:false,window:null});
 assert.deepEqual(tightestLeft(account({basis:'runs',percent:10})),{left:null,low:false,window:null},'a percentage with no basis is never trusted');
 const one=usageOneLine([rate(36,{profile_id:'cs',provider:'claude'}),rate(41,{profile_id:'cx',rate_limits:{planType:'plus'}})]);
 assert.deepEqual(one.parts,[{id:'cs',name:'Claude',value:'64%',low:false},{id:'cx',name:'Codex',value:'59%',low:false}]);
 assert.equal(one.low,false);
 assert.match(one.detail,/^Claude · Personal · bryce@example.test: 64% of the window left.*\nCodex · ChatGPT Plus/);
 const two=usageOneLine([rate(36,{profile_id:'cs',provider:'claude'}),rate(38,{profile_id:'cw',provider:'claude',name:'Acme'}),rate(41,{profile_id:'cx'})]);
 assert.deepEqual(two.parts.map(({id,value})=>[id,value]),[['cw','62%'],['cx','59%']],'two accounts of one provider share an entry with the lower figure, so the line fits');
 assert.deepEqual(usageOneLine([rate(38,{profile_id:'cw',provider:'claude',name:'Acme'}),rate(36,{profile_id:'cs',provider:'claude'})]).parts.map(part=>part.id),['cw'],'the lower figure, whichever comes first');
 assert.equal((two.detail.match(/\n/g)||[]).length,2,'hover still lists all three accounts');
 assert.deepEqual(usageOneLine([account({profile_id:'a',provider:'claude',runs_today:1}),rate(10,{profile_id:'b',provider:'claude'})]).parts.map(part=>part.value),['90%'],'a measured account beats one with only runs');
});

test('the usage line is one size, so "Left" matches the figures and a low figure keeps its window at 232px (design review, 23 Sep)',()=>{
 const css=readFileSync(new URL('./DervoSidebar.css',import.meta.url),'utf8');
 assert.match(css,/\.dv-usage-one \{[^}]*white-space: nowrap; font-size: var\(--dv-fs-caption\); \}/);
});

test('the usage line is one button naming each provider and what is left, and opens usage',()=>{
 const high=[account({profile_id:'cs',provider:'claude',basis:'rate_limits',percent:36}),account({profile_id:'cx',basis:'rate_limits',percent:41,rate_limits:{planType:'plus'}})];
 const {calls,view,props}=mount({accounts:high,usageLifted:'cx'});
 const block=screen.getByTestId('dervo-sidebar-usage');
 assert.equal(block.dataset.fold,'one');
 const buttons=block.querySelectorAll('button.dv-usage');
 assert.equal(buttons.length,1,'one line in total');
 const one=buttons[0];
 assert.ok(one.classList.contains('dv-usage-one'));
 const seen=[...one.children].filter(el=>!el.classList.contains('sr-only')).map(el=>el.textContent).join('');
 assert.equal(seen,'Left Claude 64%·Codex 59%','"Left" once, first');
 assert.equal(one.querySelectorAll('.dv-bar').length,0,'no bars on the line');
 assert.match(one.title,/Claude · Personal[^\n]*\nCodex · ChatGPT Plus/,'hover has every account in full, one per line');
 assert.equal(one.getAttribute('aria-haspopup'),'dialog');assert.equal(one.getAttribute('aria-expanded'),'true','lifted when the popover is open for any of its accounts');
 assert.equal(one.querySelectorAll('.dv-usage-sep').length,1);
 fireEvent.click(one);assert.equal(calls.at(-1)[0],'usage');assert.equal(calls.at(-1)[1]===one,true);
 // An account at half left, or with a 5-hour window nearly used, stays on the same line; the low one is marked.
 view.rerender(React.createElement(DervoSidebar,{...props,accounts:[high[0],{...high[1],percent:50,windows:[{window_mins:300,percent:85}]}]}));
 const again=screen.getByTestId('dervo-sidebar-usage').querySelectorAll('button.dv-usage');
 assert.equal(again.length,1);
 assert.equal([...again[0].children].filter(el=>!el.classList.contains('sr-only')).map(el=>el.textContent).join(''),'Left Claude 64%·Codex 15% (5h)','a low figure names its window');
 assert.equal(again[0].querySelector('.dv-usage-part[data-low="true"] .dv-pct').textContent,'15% (5h)');
 assert.equal(again[0].dataset.low,'true');
});

// ---------- Fresh-eyes item 10: dark mode with depth, tokens only ----------
test('dark cards rise by a top highlight, not a hairline; the Asked strip drops to canvas; state words read in ink. Light keeps the hairline',()=>{
 const tokens=readFileSync(new URL('./DervoTokens.css',import.meta.url),'utf8');
 const light=tokens.slice(tokens.indexOf(':root[data-dervo-local] {'),tokens.indexOf(':root[data-dervo-local].dark {'));
 const dark=tokens.slice(tokens.indexOf(':root[data-dervo-local].dark {'));
 assert.match(light,/--dv-card-edge: 0 0 0 1px var\(--dv-line\);/,'light: the hairline the cards always had, so nothing there changes');
 assert.match(dark,/--dv-card-edge: inset 0 1px 0 rgb\(255 255 255 \/ \.04\);/,'dark: a 1px top highlight on raised');
 const css=readFileSync(new URL('./DervoComponents.css',import.meta.url),'utf8');
 for(const card of ['.dv-row.dv-open','.dv-decision','.dv-jobs','.dv-choice.dv-open']){
  const rule=css.match(new RegExp(card.replace(/[.]/g,'\\.')+' \\{[^}]*\\}'))?.[0];
  assert.ok(rule,card);
  assert.match(rule,/box-shadow: var\(--dv-card-edge\)/,`${card} takes its edge from the token`);
  assert.doesNotMatch(rule,/0 0 0 1px var\(--dv-line\)/,`${card} has no hairline of its own`);
 }
 assert.match(css,/:root\[data-dervo-local\]\.dark \.dv-recap \{ background: var\(--dv-canvas\); box-shadow: inset 0 0 0 1px var\(--dv-line\); \}/,'the Asked strip on canvas at night');
 assert.match(css,/\.dv-recap \{[^}]*background: var\(--dv-sunk\)/,'and on sunk in light, as before');
 assert.match(css,/:root\[data-dervo-local\]\.dark \.dv-state:is\(\[data-state="running"\], \[data-state="done"\], \[data-accent\]\) \{ color: var\(--dv-ink-2\); \}/,'state words to ink at night');
 assert.match(css,/\.dv-state\[data-state="running"\] \{ color: var\(--dv-live\); \}/,'light keeps the green word');
 assert.match(css,/\.dv-state\[data-accent\] \{ color: var\(--dv-rust\); \}/);
});

test('one Next up replaces Bugs and ideas and the admin’s Dervo reports (b16/lifecycle)',()=>{
 const {calls}=mount({onReports:()=>calls.push(['reports']),active:'reports'});
 const next=screen.getByRole('button',{name:'Next up'});
 assert.equal(next.getAttribute('aria-current'),'page','the old reports page still reads as Next up');
 assert.equal(screen.queryByRole('button',{name:'Dervo reports'}),null);
 assert.equal(screen.queryByRole('button',{name:'Bugs and ideas'}),null);
 fireEvent.click(next);assert.deepEqual(calls.at(-1).slice(0,2),['action','bugs']);
 cleanup();
 // Negative control: a tester's sidebar has the same one row, and no reports row.
 mount();
 assert.equal(screen.queryByRole('button',{name:'Dervo reports'}),null);
 assert.ok(screen.getByRole('button',{name:'Next up'}));
});
