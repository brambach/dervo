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
const {DervoSidebar,sidebarThreads,subthreads,rollup,rollupLine,QUIET_SHOWN}=await import('./DervoSidebar.tsx');
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

/* Subthreads under their parent (beta.17, #27): nesting, the count chip and fold, the loudest mark when folded, and a
   subthread that needs you showing from a folded parent. */
const family=(extra={})=>[conv('Plan',{organisationGroup:'Orchestrator'}),
 conv('Runs',{parentId:'Plan',activity:{status:null,running:true,step:null,unread:0}}),
 conv('Thread',{parentId:'Plan',organisationGroup:'Elsewhere',activity:{status:null,running:true,step:null,unread:0}}),
 conv('Home',{parentId:'Plan',activity:{status:'needs',ended:'asking',running:false,step:null,unread:1}}),
 conv('Ideas',{organisationGroup:'Orchestrator'}),...(extra.more??[])];
const rowOf=name=>document.querySelector(`[data-conversation-id="${name}"]`);
afterEach(()=>localStorage.clear());

test('subthreads sit under a listed parent only, and a thread with no parent is unchanged',()=>{
 const rows=[...family(),conv('Orphan',{parentId:'Gone'})];
 const kids=subthreads(rows);
 assert.deepEqual([...kids.keys()],['Plan']);
 assert.deepEqual(kids.get('Plan').map(c=>c.id),['Runs','Thread','Home'],'in the order they were started');
 const {groups}=sidebarThreads(rows);
 const listed=groups.flatMap(g=>[...g.threads,...g.folded]).map(c=>c.id);
 assert.ok(listed.includes('Orphan'),'a subthread whose parent is gone moves to the top level');
 assert.ok(!listed.includes('Runs')&&!listed.includes('Thread'),'subthreads ride with their parent, not in a workstream of their own');
 assert.ok(!groups.some(g=>g.title==='Elsewhere'),'even when Home filed one somewhere else');
});

test('a quiet parent is as loud as its loudest subthread, so it never folds behind "N more"',()=>{
 const quiet=['a','b','c','d'].map(id=>conv(id,{organisationGroup:'Orchestrator',activity:{status:null,running:false,step:null,unread:0,at:2e9}}));
 const plan=conv('Plan',{organisationGroup:'Orchestrator',activity:{status:null,running:false,step:null,unread:0,at:1e9}});
 const {groups}=sidebarThreads([...quiet,plan,conv('Kid',{parentId:'Plan',activity:{status:null,running:true,step:null,unread:0}})]);
 const group=groups.find(g=>g.title==='Orchestrator');
 assert.equal(group.threads[0].id,'Plan','first, with the running');
 assert.ok(!group.folded.some(c=>c.id==='Plan'));
 assert.equal(group.threads.length,QUIET_SHOWN);
});

test('the chip names how many and the loudest of them',()=>{
 const kids=family().filter(c=>c.parentId);
 assert.equal(rollup(kids),'needs');
 assert.equal(rollupLine(kids),'3 subthreads, 1 needs you');
 assert.equal(rollup(kids.slice(0,2)),'running');
 assert.equal(rollupLine(kids.slice(0,2)),'2 subthreads, 2 running');
 const done=[conv('x',{activity:{status:null,running:false,step:null,unread:1}}),conv('y',{activity:{status:'needs',ended:'blocked',running:false,step:null,unread:0}})];
 assert.equal(rollup(done),'failed');
 assert.equal(rollupLine(done),'2 subthreads, all finished');
});

test('a parent shows its subthreads under it with the connector, and its chip folds them and remembers',async()=>{
 const {view,props}=mount({conversations:family()});
 const chip=screen.getByRole('button',{name:'3 subthreads, 1 needs you. Hide them'});
 assert.equal(chip.closest('.dv-sb-thread'),rowOf('Plan'));
 assert.equal(rowOf('Plan').dataset.family,'open');
 const group=screen.getByRole('group',{name:'Subthreads of Plan'});
 assert.deepEqual([...group.querySelectorAll('.dv-sb-thread')].map(r=>r.dataset.conversationId),['Runs','Thread','Home']);
 assert.ok(rowOf('Home').classList.contains('dv-sb-child-last'));
 assert.ok(!rowOf('Runs').classList.contains('dv-sb-child-last'));
 assert.equal(rowOf('Ideas').querySelector('.dv-sb-kids'),null,'a thread with no subthreads gets no chip');
 assert.ok(!rowOf('Ideas').classList.contains('dv-sb-child'));
 assert.equal(screen.getByRole('group',{name:'Orchestrator'}).querySelector('.dv-count').textContent,'5','the workstream counts them');
 fireEvent.click(chip);
 // Folded: the one that needs you still shows, hanging from a dotted line; the running ones go.
 await waitFor(()=>assert.equal(rowOf('Plan').dataset.family,'peek'));
 assert.equal(rowOf('Runs'),null);
 assert.ok(rowOf('Home'));
 const folded=screen.getByRole('button',{name:'3 subthreads, 1 needs you. Show them'});
 assert.equal(folded.dataset.roll,'needs');
 assert.equal(folded.querySelector('.dv-st').dataset.state,'needs');
 assert.deepEqual(JSON.parse(localStorage.getItem('dervo:subthreads-folded:p')),['Plan']);
 // Once nothing in it needs you, folded means folded.
 const calm=family().map(c=>c.id==='Home'?{...c,activity:{status:null,running:false,step:null,unread:0}}:c);
 view.rerender(React.createElement(DervoSidebar,{...props,conversations:calm}));
 await waitFor(()=>assert.equal(rowOf('Home'),null));
 assert.equal(rowOf('Plan').dataset.family,'closed');
 assert.equal(screen.getByRole('button',{name:'3 subthreads, 2 running. Show them'}).dataset.roll,'running');
 // A new subthread arriving opens the parent again.
 view.rerender(React.createElement(DervoSidebar,{...props,conversations:[...calm,conv('Fourth',{parentId:'Plan'})]}));
 await waitFor(()=>assert.equal(rowOf('Plan').dataset.family,'open'));
 assert.ok(rowOf('Fourth'));
 assert.equal(localStorage.getItem('dervo:subthreads-folded:p'),null);
});

test('the open subthread stays in view under a folded parent',()=>{
 localStorage.setItem('dervo:subthreads-folded:p',JSON.stringify(['Plan']));
 mount({conversations:family().map(c=>c.id==='Home'?{...c,activity:{status:null,running:false,step:null,unread:0}}:c),channelId:'Runs'});
 assert.equal(rowOf('Plan').dataset.family,'peek');
 assert.ok(rowOf('Runs'));
 assert.equal(rowOf('Runs').getAttribute('aria-current'),'page');
 assert.equal(rowOf('Thread'),null);
});
