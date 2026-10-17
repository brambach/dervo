import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLDetailsElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','PointerEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
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
const {render,fireEvent,screen,cleanup,waitFor,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {DervoSidebar}=await import('./DervoSidebar.tsx');
const {archivedGroups,forgetTidies,homeTidyLine,quietFor,tidiedLine,tidiedWhen,tidyLabel}=await import('./tidy.ts');
after(()=>dom.window.close());afterEach(()=>{cleanup();forgetTidies();});

/* Quiet threads tidy themselves (b16/lifecycle). The host decides what's quiet (backend/test_conversation_tidy.py);
   these check what the sidebar says and sends. */
const NOW=Date.parse('2026-09-25T10:00:00Z');
const day=86400;
const conv=(id,extra={})=>({id,name:id,visibility:'shared',project_id:'p',profile_id:'one',model:'m',organisationGroup:null,activity:{status:null,running:false,step:null,unread:0,at:NOW/1000-3600},...extra});
const archived=(id,extra={})=>({...conv(id),archived_at:NOW/1000-16*day,tidy_batch:null,tidied_at:null,quiet_since:null,...extra});
const TIDIED={batch:'b1',at:NOW/1000,count:2,days:14,threads:[{id:'Usage popover',name:'Usage popover',quiet_since:NOW/1000-15*day},{id:'Connectors docs',name:'Connectors docs',quiet_since:NOW/1000-19*day}]};

function host({tidy=TIDIED,pins=[],fold=[]}={}){
 const calls=[];
 let rows=fold;
 window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{
  calls.push([operation,args]);
  if(operation==='conversations_tidy')return tidy;
  if(operation==='conversations_tidy_undo'){rows=rows.filter(row=>row.tidy_batch!==args.batch);return {ids:tidy.threads.map(t=>t.id)};}
  if(operation==='conversation_pins')return pins;
  if(operation==='conversation_pin')return {id:args.channelId,pinned:args.pinned};
  if(operation==='project_tidy_setting')return {days:14,default:14,choices:[7,14,30]};
  if(operation==='conversations_archived')return rows;
  if(operation==='conversation_restore'){rows=rows.filter(row=>row.id!==args.channelId);return {id:args.channelId};}
  return [];
 }};
 return calls;
}
function mount(overrides={}){
 const calls=[];
 const record=name=>(...args)=>{calls.push([name,...args]);};
 const props={projects:[{id:'p',name:'Dervo',cwd:'/Users/bryce/dervo/app'}],projectId:'p',projectsLoading:false,projectsFailed:false,
  onRetryProjects:record('retry'),onSelectProject:record('project'),onAddProject:record('add'),ready:true,active:undefined,
  conversations:[conv('Live board'),conv('Slow first load')],channelId:'',conversationActive:true,onSelectConversation:record('open'),onAction:record('action'),onCreate:record('create'),onJump:record('jump'),
  accounts:[],onUsage:record('usage'),onWorkspaceSettings:record('settings'),onFeedback:record('feedback'),canSignOut:false,onSignOut:async()=>{},
  threadMenu:{workstreams:[],filedUnder:()=>null,cannotMove:()=>null,rename:record('rename'),move:record('move'),archive:record('archive'),remove:async()=>{},stop:async()=>{}},
  onRename:record('renamed'),onUndo:record('undo'),onConnectedAccounts:record('accounts'),accountNote:'Private beta',
  onArchivedRestored:async c=>{calls.push(['restored',c.id]);},onArchivedDelete:async c=>{calls.push(['deleted',c.id]);},...overrides};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 render(React.createElement(QueryClientProvider,{client},React.createElement(DervoSidebar,props)));
 return {calls};
}

test('the words',()=>{
 assert.equal(tidiedLine(4),'Tidied 4 quiet threads');
 assert.equal(tidiedLine(1),'Tidied 1 quiet thread');
 assert.equal(tidiedLine(0),'');
 assert.equal(homeTidyLine(4),'Put away 4 threads you hadn’t touched in two weeks.');
 assert.equal(homeTidyLine(1,7),'Put away 1 thread you hadn’t touched in a week.');
 assert.equal(homeTidyLine(0),'');
 assert.equal(quietFor(NOW/1000-15.5*day,NOW),'15 d');
 assert.equal(quietFor(null,NOW),'');
 assert.equal(tidiedWhen(NOW/1000-60,NOW),'Tidied today');
 assert.equal(tidiedWhen(NOW/1000-day,NOW),'Tidied yesterday');
 assert.equal(tidyLabel(null),'Never');assert.equal(tidyLabel(30),'30 days');
});

test('the fold groups by why: each tidy, least quiet first, then what you archived',()=>{
 const rows=[archived('old spike'),archived('Connectors docs',{tidy_batch:'b1',tidied_at:NOW/1000,quiet_since:NOW/1000-19*day}),
  archived('Usage popover',{tidy_batch:'b1',tidied_at:NOW/1000,quiet_since:NOW/1000-15*day})];
 const groups=archivedGroups(rows,14,NOW);
 assert.deepEqual(groups.map(g=>g.title),['Tidied today, quiet 14 days or more','Archived by you']);
 assert.deepEqual(groups[0].rows.map(r=>r.id),['Usage popover','Connectors docs']);
 assert.deepEqual(archivedGroups(rows,null,NOW)[0].title,'Tidied today');
});

test('opening the app tidies once and says so; Undo brings them back and the line goes',async()=>{
 const calls=host({fold:[archived('Usage popover',{tidy_batch:'b1',tidied_at:NOW/1000,quiet_since:NOW/1000-15*day}),
  archived('Connectors docs',{tidy_batch:'b1',tidied_at:NOW/1000,quiet_since:NOW/1000-19*day})]});
 mount();
 const line=await screen.findByText('Tidied 2 quiet threads');
 assert.equal(calls.filter(([op])=>op==='conversations_tidy').length,1);
 fireEvent.click(within(line.closest('p')).getByRole('button',{name:'Undo: Tidied 2 quiet threads'}));
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='conversations_tidy_undo')?.[1],{projectId:'p',batch:'b1'}));
 await waitFor(()=>assert.equal(screen.queryByText('Tidied 2 quiet threads'),null));
 cleanup();
 // Negative control: the same launch never tidies this project again, and says nothing.
 mount();
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(calls.filter(([op])=>op==='conversations_tidy').length,1);
 assert.equal(screen.queryByText(/Tidied/),null);
});

test('nothing quiet: no line at all',async()=>{
 const calls=host({tidy:{batch:null,at:null,count:0,days:14,threads:[]}});
 mount();
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='conversations_tidy')));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(screen.queryByText(/Tidied/),null);
});

test('See them opens the fold; opening a thread there brings it back and opens it',async()=>{
 host({fold:[archived('Usage popover',{tidy_batch:'b1',tidied_at:Date.now()/1000,quiet_since:Date.now()/1000-15*day}),archived('old spike')]});
 const {calls}=mount();
 fireEvent.click(await screen.findByRole('button',{name:'See them'}));
 const fold=screen.getByTestId('archived-conversations');
 await waitFor(()=>assert.equal(fold.open,true));
 assert.ok(within(fold).getByText('Tidied today, quiet 14 days or more'));
 assert.ok(within(fold).getByText('Archived by you'));
 assert.ok(within(fold).getByText('15 d'));
 fireEvent.click(within(fold).getByRole('button',{name:'Usage popover'}));
 await waitFor(()=>assert.deepEqual(calls.slice(-2),[['restored','Usage popover'],['open','Usage popover']]));
 // It was the only one of that tidy still put away, so the line goes and old spike stays in the fold.
 await waitFor(()=>assert.equal(screen.queryByText(/Tidied \d/),null));
 assert.ok(within(screen.getByTestId('archived-conversations')).getByText('old spike'));
});

test('a pinned thread shows a pin before its time, and Pin in the menu sends it',async()=>{
 const calls=host({pins:['Live board'],tidy:{batch:null,at:null,count:0,days:14,threads:[]}});
 mount();
 const row=document.querySelector('[data-conversation-id="Live board"]');
 await waitFor(()=>assert.ok(row.querySelector('.dv-sb-pinned')));
 assert.ok(within(row).getByText('Pinned'));
 const other=document.querySelector('[data-conversation-id="Slow first load"]');
 assert.equal(other.querySelector('.dv-sb-pinned'),null);
 fireEvent.keyDown(screen.getByRole('button',{name:'Options for Slow first load'}),{key:'Enter'});
 fireEvent.click(await screen.findByRole('menuitem',{name:/^Pin/}));
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='conversation_pin')?.[1],{projectId:'p',channelId:'Slow first load',pinned:true}));
});

test('Knowledge shows the unread dot in place of its count when something new was kept',()=>{
 host();
 mount({knowledgeUnread:true,knowledgeCount:3});
 const knowledge=screen.getByRole('button',{name:/Knowledge/});
 assert.ok(knowledge.querySelector('.dv-sb-dot'));
 assert.equal(within(knowledge).queryByText('3'),null);
 cleanup();
 host();
 mount({knowledgeCount:3});
 assert.ok(within(screen.getByRole('button',{name:/Knowledge/})).getByText('3'));
});

test('an admin sees new tester reports counted on Next up, in their project only',async()=>{
 host();
 window.__TAURI_INTERNALS__.invoke=(inner=>async(cmd,payload)=>payload.operation==='tester_reports'?{state:'ready',reports:[],unseen:2,project:'p'}:inner(cmd,payload))(window.__TAURI_INTERNALS__.invoke);
 mount({onReports:()=>{}});
 assert.ok(await screen.findByLabelText('2 new tester reports'));
 cleanup();
 window.__TAURI_INTERNALS__.invoke=async(cmd,payload)=>payload.operation==='tester_reports'?{state:'ready',reports:[],unseen:2,project:'elsewhere'}:[];
 mount({onReports:()=>{}});
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(screen.queryByLabelText('2 new tester reports'),null);
});

/* Beta.16.1 items 3 and 7: Smart, multi-select archive and "Delete all archived". The host's side is in
   backend/test_conversation_tidy.py and test_conversation_lifecycle.py. */
const {freedLine}=await import('./ArchivedFold.tsx');
const {sizeLabel}=await import('./tidy.ts');
const three=()=>[conv('Live board'),conv('Slow first load'),conv('Third')];

test('Smart and the freed line, in words',()=>{
 assert.equal(tidyLabel('smart'),'Smart');
 assert.equal(homeTidyLine(3,0),'Put away 3 finished or quiet threads.');
 assert.equal(homeTidyLine(1,'smart'),'Put away 1 finished or quiet thread.');
 const rows=[archived('Usage popover',{tidy_batch:'b1',tidied_at:NOW/1000,quiet_since:NOW/1000-2*day})];
 assert.equal(archivedGroups(rows,'smart',NOW)[0].title,'Tidied today, finished or quiet');
 assert.deepEqual([sizeLabel(0),sizeLabel(480_000),sizeLabel(3_400_000),sizeLabel(48_300_000),sizeLabel(1_200_000_000)],['0 KB','480 KB','3.4 MB','48 MB','1.2 GB']);
 assert.equal(freedLine({count:2,freedBytes:48_300_000,databaseBytes:6_100_000,fileBytes:42_200_000}).text,'Deleted 2 threads. Freed 48 MB.');
 assert.match(freedLine({count:2,freedBytes:48_300_000,databaseBytes:6_100_000,fileBytes:42_200_000}).detail,/42 MB of saved file versions left the disk\. 6\.1 MB is free inside Dervo’s database/);
 assert.equal(freedLine({count:1,freedBytes:300,databaseBytes:300,fileBytes:0}).text,'Deleted 1 thread. Freed less than 1 KB.');
});

test('⌘-click picks rows with the open thread, the bar archives them in list order, a plain click ends it',async()=>{
 host({tidy:{batch:null,at:null,count:0,days:14,threads:[]}});
 const archivedMany=[];
 const {calls}=mount({conversations:three(),channelId:'Live board',onArchiveMany:async rows=>{archivedMany.push(rows.map(r=>r.id));}});
 fireEvent.click(within(document.querySelector('[data-conversation-id="Third"]')).getByRole('button',{name:'Third'}),{metaKey:true});
 const bar=await screen.findByRole('toolbar',{name:'2 threads selected'});
 assert.ok(document.querySelector('[data-conversation-id="Live board"][data-selected]'));
 assert.ok(document.querySelector('[data-conversation-id="Third"][data-selected]'));
 assert.equal(document.querySelector('[data-conversation-id="Slow first load"][data-selected]'),null);
 assert.equal(calls.filter(([name])=>name==='open').length,0,'⌘-click never opens');
 fireEvent.click(within(bar).getByRole('button',{name:'Archive 2 threads'}));
 await waitFor(()=>assert.deepEqual(archivedMany,[['Live board','Third']]));
 await waitFor(()=>assert.equal(screen.queryByRole('toolbar'),null));
 // Picked again, a plain click opens that thread and ends the selection.
 fireEvent.click(within(document.querySelector('[data-conversation-id="Third"]')).getByRole('button',{name:'Third'}),{metaKey:true});
 await screen.findByRole('toolbar');
 fireEvent.click(within(document.querySelector('[data-conversation-id="Slow first load"]')).getByRole('button',{name:'Slow first load'}));
 await waitFor(()=>assert.equal(screen.queryByRole('toolbar'),null));
 assert.deepEqual(calls.filter(([name])=>name==='open').at(-1),['open','Slow first load']);
});

test('Shift-click takes the range, ⌘⇧A archives the selection and Escape clears it',async()=>{
 host({tidy:{batch:null,at:null,count:0,days:14,threads:[]}});
 const archivedMany=[];
 mount({conversations:three(),channelId:'',onArchiveMany:async rows=>{archivedMany.push(rows.map(r=>r.id));}});
 const open=name=>within(document.querySelector(`[data-conversation-id="${name}"]`)).getByRole('button',{name});
 fireEvent.click(open('Slow first load'),{metaKey:true});
 fireEvent.click(open('Third'),{shiftKey:true});
 await screen.findByRole('toolbar',{name:'2 threads selected'});
 assert.equal(document.querySelector('[data-conversation-id="Live board"][data-selected]'),null);
 // The app's own ⌘⇧A (the open thread only) must see it handled.
 let reached=false;const later=event=>{reached=!event.defaultPrevented?true:reached;};
 window.addEventListener('keydown',later);
 fireEvent.keyDown(window,{key:'A',metaKey:true,shiftKey:true});
 window.removeEventListener('keydown',later);
 await waitFor(()=>assert.deepEqual(archivedMany,[['Slow first load','Third']]));
 assert.equal(reached,false);
 fireEvent.click(open('Third'),{metaKey:true});
 await screen.findByRole('toolbar');
 fireEvent.keyDown(window,{key:'Escape'});
 await waitFor(()=>assert.equal(screen.queryByRole('toolbar'),null));
});

test('without onArchiveMany, ⌘-click just opens the thread',()=>{
 host({tidy:{batch:null,at:null,count:0,days:14,threads:[]}});
 const {calls}=mount({conversations:three()});
 fireEvent.click(within(document.querySelector('[data-conversation-id="Third"]')).getByRole('button',{name:'Third'}),{metaKey:true});
 assert.equal(screen.queryByRole('toolbar'),null);
 assert.deepEqual(calls.at(-1),['open','Third']);
});

test('Delete all archived: one confirm with the count, Cancel first, then the fold says what it freed',async()=>{
 host({tidy:{batch:null,at:null,count:0,days:14,threads:[]},fold:[archived('old spike'),archived('Usage popover',{tidy_batch:'b1',tidied_at:NOW/1000,quiet_since:NOW/1000-15*day})]});
 let gone=false;const sent=[];
 const inner=window.__TAURI_INTERNALS__.invoke;
 window.__TAURI_INTERNALS__.invoke=async(cmd,payload)=>payload.operation==='conversations_archived'&&gone?[]:inner(cmd,payload);
 let fail=true;
 mount({onDeleteArchived:async rows=>{sent.push(rows.map(r=>r.id));if(fail)throw new Error('Nothing was deleted. Keep me: wait for it.');gone=true;
  return {ids:rows.map(r=>r.id),count:rows.length,freedBytes:48_300_000,databaseBytes:6_100_000,fileBytes:42_200_000};}});
 const fold=await screen.findByTestId('archived-conversations');
 fireEvent.click(fold.querySelector('summary'));
 fireEvent.click(await within(fold).findByRole('button',{name:'Delete all archived…'}));
 const dialog=within(fold).getByRole('alertdialog',{name:'Delete all 2 archived threads?'});
 assert.ok(within(dialog).getByText('Your project folder stays as it is, including any files they changed.'));
 await waitFor(()=>assert.equal(document.activeElement?.textContent,'Cancel'));
 // Escape puts the row back and nothing is sent.
 fireEvent.keyDown(dialog,{key:'Escape'});
 assert.equal(within(fold).queryByRole('alertdialog'),null);
 assert.deepEqual(sent,[]);
 // A refusal is said in the confirm, which stays.
 fireEvent.click(within(fold).getByRole('button',{name:'Delete all archived…'}));
 fireEvent.click(within(fold).getByRole('button',{name:'Delete 2 threads'}));
 assert.ok(await within(fold).findByText('Nothing was deleted. Keep me: wait for it.'));
 fail=false;
 fireEvent.click(within(fold).getByRole('button',{name:'Delete 2 threads'}));
 const done=await screen.findByText('Deleted 2 threads. Freed 48 MB.');
 assert.equal(done.closest('[role="status"]')?.getAttribute('title')?.includes('Your project folder is as it was.'),true);
 assert.deepEqual([...sent.at(-1)].sort(),['Usage popover','old spike']);
 assert.equal(screen.queryByTestId('archived-conversations'),null);
});

test('the fold has no Delete all archived without its handler',async()=>{
 host({tidy:{batch:null,at:null,count:0,days:14,threads:[]},fold:[archived('old spike')]});
 mount();
 const fold=await screen.findByTestId('archived-conversations');
 fireEvent.click(fold.querySelector('summary'));
 await within(fold).findByText('old spike');
 assert.equal(within(fold).queryByRole('button',{name:'Delete all archived…'}),null);
});
