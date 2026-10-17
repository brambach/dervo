import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {readFileSync} from 'node:fs';
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
HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
globalThis.requestAnimationFrame=cb=>setTimeout(()=>cb(Date.now()),0);globalThis.cancelAnimationFrame=id=>clearTimeout(id);
window.requestAnimationFrame=globalThis.requestAnimationFrame;window.cancelAnimationFrame=globalThis.cancelAnimationFrame;
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,within,act}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ProjectPicker,projectFigure,filterProjects}=await import('./ProjectPicker.tsx');
const {ThreadMenuBody,matchWorkstreams,withUndoRows,useThreadUndo,editableTarget,UNDO_MS}=await import('./ThreadMenu.tsx');
const {DropdownMenu,DropdownMenuTrigger,DropdownMenuContent}=await import('@/shared/ui/dropdown-menu');
const {DervoSidebar}=await import('./DervoSidebar.tsx');
const {AddProjectDialog,defaultAccount,folderLine,modelFor}=await import('./AddProject.tsx');
const {accountsFrom}=await import('./models.ts');
const {organisationRequest}=await import('./api.ts');
const h=React.createElement;
after(()=>dom.window.close());
// Close any menu a test left open before unmounting it: a trapped focus scope left behind by one test fights the next
// test's scope for focus, and enough of them run the process out of memory.
afterEach(async()=>{
 for(let i=0;i<3&&document.querySelector('[role="menu"]');i++){fireEvent.keyDown(document.activeElement??document.body,{key:'Escape'});await act(async()=>{await new Promise(r=>setTimeout(r,0));});}
 cleanup();delete window.__TAURI_INTERNALS__;
});

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(command,payload={})=>{
  if(command==='local_request'){calls.push({operation:payload.operation,args:payload.args});return handler(payload.operation,payload.args);}
  calls.push({command});return handler(command,payload);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const withClient=element=>h(QueryClientProvider,{client:client()},element);
const conv=(id,extra={})=>({id,name:id,visibility:'shared',project_id:'p',profile_id:'a',model:'m',organisationGroup:null,activity:{status:null,running:false,step:null,unread:0},...extra});

// ---------------------------------------------------------------- Thread menus: tiers, confirm, move
function Menu({conversation,model,header}){
 const [confirming,setConfirming]=React.useState(false);
 return h(DropdownMenu,{onOpenChange:open=>{if(!open)setConfirming(false);}},
  h(DropdownMenuTrigger,{asChild:true},h('button',{type:'button'},'Thread options')),
  h(DropdownMenuContent,null,h(ThreadMenuBody,{kind:'dropdown',conversation,model,confirming,onConfirm:setConfirming,header})));
}
function model(calls,extra={}){
 return {workstreams:[{id:'i1',name:'Orchestrator',count:3},{id:'i2',name:'Releases',count:2}],filedUnder:c=>c.organisationGroup??null,cannotMove:c=>c.visibility==='private'?'Private threads stay unfiled':null,
  rename:c=>calls.push(['rename',c.id]),move:(c,target)=>calls.push(['move',c.id,target]),archive:c=>calls.push(['archive',c.id]),
  remove:async c=>{calls.push(['remove',c.id]);},stop:async c=>{calls.push(['stop',c.id]);},...extra};
}
const open=async()=>{fireEvent.keyDown(screen.getByRole('button',{name:'Thread options'}),{key:'Enter'});return screen.findAllByRole('menuitem');};

test('Delete asks in the menu itself: nothing is removed until Delete thread, Cancel is first and focused',async()=>{
 const calls=[];
 render(h(Menu,{conversation:conv('Connector picker'),model:model(calls)}));
 await open();
 fireEvent.click(screen.getByRole('menuitem',{name:'Delete…'}));
 const confirm=await screen.findByRole('alertdialog');
 assert.equal(calls.length,0,'tier 2: choosing Delete… only asks');
 assert.ok(confirm.closest('[role="menu"]'),'the confirm sits where the menu was');
 assert.match(confirm.textContent,/^Delete Connector picker\?/);
 assert.match(confirm.textContent,/This can’t be undone/);assert.match(confirm.textContent,/Archive keeps it out of the way instead/);
 const buttons=within(confirm).getAllByRole('menuitem').map(b=>b.textContent);
 assert.deepEqual(buttons,['Cancel','Delete thread'],'the way out first');
 await waitFor(()=>assert.equal(document.activeElement.textContent,'Cancel'));
 fireEvent.click(within(confirm).getByRole('menuitem',{name:'Delete thread'}));
 await waitFor(()=>assert.deepEqual(calls,[['remove','Connector picker']]));
});

test('a running thread can’t be deleted or archived until it stops: the confirm offers Stop it, and Archive says why',async()=>{
 const calls=[];
 const running=conv('Live board',{activity:{status:'running',running:true,step:'Editing',unread:0}});
 render(h(Menu,{conversation:running,model:model(calls)}));
 await open();
 const archive=screen.getByRole('menuitem',{name:/^Archive/});
 assert.equal(archive.getAttribute('data-disabled'),'','the host refuses to archive a running thread, so the row is disabled');
 assert.match(archive.textContent,/It’s running\. Stop it first\./);
 fireEvent.click(screen.getByRole('menuitem',{name:'Delete…'}));
 const confirm=await screen.findByRole('alertdialog');
 assert.match(confirm.textContent,/It’s running\. Stop it first\./);
 fireEvent.click(within(confirm).getByRole('menuitem',{name:'Stop it'}));
 await waitFor(()=>assert.deepEqual(calls,[['stop','Live board']]),'Stop it stops the run; nothing is deleted');
});

test('a failed delete keeps the confirm with the host’s reason',async()=>{
 const calls=[];
 render(h(Menu,{conversation:conv('Beta plan'),model:model(calls,{remove:async()=>{throw new Error('Finish or reconcile this conversation’s tasks first.');}})}));
 await open();
 fireEvent.click(screen.getByRole('menuitem',{name:'Delete…'}));
 fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('menuitem',{name:'Delete thread'}));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/reconcile/));
 assert.ok(screen.getByRole('alertdialog'));
});

test('the thread header adds Search in thread and the continuity row; a private thread can’t be moved and says why',async()=>{
 const calls=[];let searched=0;
 render(h(Menu,{conversation:conv('Notes',{visibility:'private'}),model:model(calls),header:{search:()=>{searched++;}}}));
 const rows=(await open()).map(r=>r.textContent);
 assert.deepEqual(rows,['Rename','Move to workstreamPrivate threads stay unfiled','Search in thread⌘F','Archive⌘⇧A','Delete…']);
 fireEvent.click(screen.getByRole('menuitem',{name:'Search in thread'}));
 assert.equal(searched,1);
});

test('tier 1: the row an archive or a move left shows in place, and a rename changes nothing in the list',()=>{
 const rows=[conv('a',{organisationGroup:'Orchestrator'}),conv('b',{organisationGroup:'Orchestrator'}),conv('c')];
 const archived={id:'b',kind:'archived',label:'Archived',conversation:rows[1],index:1,fromGroup:'Orchestrator',undo:async()=>{}};
 const afterArchive=withUndoRows([rows[0],rows[2]],archived);
 assert.deepEqual(afterArchive.map(r=>r.id),['a','gone:b','c'],'the row stays where it was');
 assert.equal(afterArchive[1].gone,archived);
 // Until the host's list catches up, the thread itself isn't shown twice.
 assert.deepEqual(withUndoRows(rows,archived).map(r=>r.id),['a','gone:b','c']);
 const moved={id:'a',kind:'moved',label:'Moved to Releases',conversation:rows[0],index:0,fromGroup:'Orchestrator',toGroup:'Releases',undo:async()=>{}};
 const afterMove=withUndoRows(rows,moved);
 assert.deepEqual(afterMove.map(r=>[r.id,r.organisationGroup]),[['gone:a','Orchestrator'],['a','Releases'],['b','Orchestrator'],['c',null]]);
 assert.equal(withUndoRows(rows,{...moved,kind:'renamed'}),rows);
 assert.equal(withUndoRows(rows,null),rows);
});


test('running thread deletion can open its exact blocker without stopping or deleting it',async()=>{
 const actions=[],opened=[],blocker={projectId:'p',projectName:'Dervo',channelId:'running',threadName:'Running work',taskId:'exact',taskName:'The active task',kind:'run',instruction:'Wait for this task or stop it.'};
 const reads=host(op=>{if(op==='lifecycle_blockers')return [blocker];throw Error(op);});
 render(withClient(h(Menu,{conversation:conv('running',{activity:{running:true}}),model:model(actions,{openBlocker:b=>opened.push(b)})})));
 await open();fireEvent.click(screen.getByRole('menuitem',{name:'Delete…'}));
 fireEvent.click(await screen.findByRole('menuitem',{name:'Open task'}));
 await waitFor(()=>assert.equal(screen.queryByRole('menu'),null));
 assert.deepEqual(opened,[blocker]);assert.deepEqual(actions,[]);
 assert.deepEqual(reads[0].args,{scope:'conversation',projectId:'p',channelId:'running'});
});
