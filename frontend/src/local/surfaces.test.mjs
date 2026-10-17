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

// ---------------------------------------------------------------- Tier 1 undo, first: it listens on the window
function UndoHarness({onUndo}){
 const undo=useThreadUndo();
 return h('div',null,
  h('button',{type:'button',onClick:()=>undo.offer({id:'b',kind:'archived',label:'Archived',conversation:conv('b'),index:0,fromGroup:null,undo:async()=>{onUndo();}})},'Archive b'),
  h('input',{'aria-label':'Composer'}),
  h('p',{'data-testid':'live'},undo.entry?undo.entry.label:'none'),
  h('p',{'data-testid':'failed'},JSON.stringify(undo.failed)));
}

test('tier 1: ⌘Z takes back the last archive, move or rename, but never inside a text field, and the offer lapses',async()=>{
 let undone=0;
 render(h(UndoHarness,{onUndo:()=>{undone++;}}));
 fireEvent.click(screen.getByRole('button',{name:'Archive b'}));
 assert.equal(screen.getByTestId('live').textContent,'Archived');
 fireEvent.keyDown(screen.getByRole('textbox',{name:'Composer'}),{key:'z',metaKey:true});
 assert.equal(undone,0,'⌘Z in a field is the field’s own undo');
 fireEvent.keyDown(document.body,{key:'z',metaKey:true});
 await waitFor(()=>assert.equal(undone,1));
 assert.equal(screen.getByTestId('live').textContent,'none');
 fireEvent.keyDown(document.body,{key:'z',metaKey:true});
 await act(async()=>{await new Promise(r=>setTimeout(r,10));});
 assert.equal(undone,1,'one undo per action');
 assert.equal(editableTarget(screen.getByRole('textbox',{name:'Composer'})),true);assert.equal(editableTarget(document.body),false);
 assert.equal(UNDO_MS,6000);
});

// ---------------------------------------------------------------- Picker A: counts, filter, restore
const projects=[{id:'p1',name:'dervo app',cwd:'/Users/bryce/dervo/app'},{id:'p2',name:'dervo website',cwd:'/Users/bryce/dervo/website'},{id:'p3',name:'shop',cwd:'/Users/bryce/code/shop'}];

test('a project row says what needs you there first, otherwise what runs, and nothing when the host can’t say',()=>{
 const counts={p1:{needs:2,running:1},p2:{needs:0,running:1},p3:{needs:0,running:0}};
 assert.equal(projectFigure(counts,'p1'),'2 needs you','needs you outranks running');
 assert.equal(projectFigure(counts,'p2'),'1 running');
 assert.equal(projectFigure(counts,'p3'),'');
 assert.equal(projectFigure(undefined,'p1'),'','hidden when the host can’t say');
 assert.deepEqual(filterProjects(projects,'  WEB ').map(p=>p.id),['p2']);
 assert.deepEqual(filterProjects(projects,'code/').map(p=>p.id),['p3'],'the folder matches too');
});

test('the picker opens on its filter, reads the counts as it opens, and each other project shows its figure',async()=>{
 const calls=host(op=>op==='projects_activity'?{p1:{needs:3,running:0},p2:{needs:1,running:0},p3:{needs:0,running:1}}:op==='projects_archived'?[]:null);
 const chosen=[];
 render(withClient(h(ProjectPicker,{projects,projectId:'p1',loading:false,failed:false,onRetry(){},onSelect:id=>chosen.push(id),onAdd(){},onSettings(){}})));
 assert.equal(calls.length,0,'nothing is read until the menu opens');
 fireEvent.keyDown(screen.getByRole('button',{name:'Project dervo app'}),{key:'Enter'});
 const filter=await screen.findByRole('textbox',{name:'Find a project'});
 await waitFor(()=>assert.ok(document.activeElement===filter));
 await waitFor(()=>assert.ok(screen.getByRole('menuitem',{name:'Open project dervo website, 1 needs you'})));
 assert.ok(screen.getByRole('menuitem',{name:'Open project shop, 1 running'}));
 const here=screen.getByRole('menuitem',{name:'Open project dervo app'});
 assert.equal(here.getAttribute('aria-current'),'true');
 assert.equal(here.textContent.includes('needs you'),false,'the project you’re in shows its tick, not a count');
 assert.ok(calls.some(c=>c.operation==='projects_activity'));
 fireEvent.click(screen.getByRole('menuitem',{name:/^Open project shop/}));
 assert.deepEqual(chosen,['p3']);
});

test('typing finds an archived project, and Return restores it and opens it',async()=>{
 const calls=host(op=>op==='projects_archived'?[{id:'p4',name:'buzz fork',cwd:'/Users/bryce/code/buzz',archived_at:1757635200}]:op==='projects_activity'?{}:op==='project_restore'?{id:'p4'}:null);
 const chosen=[];
 render(withClient(h(ProjectPicker,{projects,projectId:'p1',loading:false,failed:false,onRetry(){},onSelect:id=>chosen.push(id),onAdd(){},onSettings(){}})));
 fireEvent.keyDown(screen.getByRole('button',{name:'Project dervo app'}),{key:'Enter'});
 const filter=await screen.findByRole('textbox',{name:'Find a project'});
 await waitFor(()=>assert.ok(calls.some(c=>c.operation==='projects_archived')));
 fireEvent.change(filter,{target:{value:'bu'}});
 assert.match(screen.getByRole('status').textContent,/No open project matches “bu”\./);
 assert.ok(await screen.findByRole('menuitem',{name:'Restore and open buzz fork'}));
 fireEvent.keyDown(filter,{key:'Enter'});
 await waitFor(()=>assert.deepEqual(chosen,['p4']));
 assert.deepEqual(calls.filter(c=>c.operation==='project_restore').map(c=>c.args),[{projectId:'p4'}]);
});

test('Move matches workstreams as you type and offers a new one by name',()=>{
 const list=[{id:'i1',name:'Orchestrator',count:3},{id:'i2',name:'Releases',count:2}];
 assert.deepEqual(matchWorkstreams(list,''),{shown:list,create:null});
 assert.deepEqual(matchWorkstreams(list,'re'),{shown:[list[1]],create:'re'});
 assert.deepEqual(matchWorkstreams(list,' releases '),{shown:[list[1]],create:null},'an exact name is the existing one');
});

// ---------------------------------------------------------------- Wiring kept from the organisation panel's tests
test('the organisation operations stay allowlisted and in the native enum; the panel is gone and rename lives in the row',()=>{
 assert.rejects(()=>organisationRequest('organisation_sql',{}),/Unsupported/);
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['organisation_get','organisation_set','organisation_undo','projects_activity'])assert.ok(bridge.includes(`"${op}"`),`Native seam missing: ${op}`);
 const app=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.doesNotMatch(app,/Rename and organise|ConversationOrganisation|ProjectManager|ConversationAccountDialog/);
 assert.match(app,/organisationRequest<OrganisationState>\('organisation_set',\{projectId,channelId:conversation\.id,expectedRevision:state\.revision,name,group:state\.group,nameLocked:true/);
 assert.match(app,/organisationRequest\('organisation_undo',\{projectId,channelId:conversation\.id,expectedRevision:saved\.revision\}\)/);
 assert.match(app,/await restoreConversation\(conversation\);/,'Undo after archive restores the thread');
});
test('a Codex account on ChatGPT’s $100 Pro plan ("prolite") says Pro Lite, not nothing',()=>{
 // Josh, feedback #9 (27 Sep 2026): Codex reports that plan as "prolite".
 const profile={id:'cx',provider:'codex',identity:'you@example.test',category:'personal',method:'subscription',name:null};
 const usage=planType=>[{profile_id:'cx',provider:'codex',identity:'you@example.test',method:'subscription',percent:41,basis:'rate_limits',resets_at:null,rate_limits:{planType}}];
 assert.equal(accountsFrom([profile],usage('prolite'))[0].plan,'Pro Lite');
 assert.equal(accountsFrom([profile],usage('pro'))[0].plan,'Pro');
});
