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
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
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
const withClient=element=>h(ThemeProvider,null,h(QueryClientProvider,{client:client()},element));
const conv=(id,extra={})=>({id,name:id,visibility:'shared',project_id:'p',profile_id:'a',model:'m',organisationGroup:null,activity:{status:null,running:false,step:null,unread:0},...extra});

function model(calls,extra={}){
 return {workstreams:[{id:'i1',name:'Orchestrator',count:3},{id:'i2',name:'Releases',count:2}],filedUnder:c=>c.organisationGroup??null,cannotMove:c=>c.visibility==='private'?'Private threads stay unfiled':null,
  rename:c=>calls.push(['rename',c.id]),move:(c,target)=>calls.push(['move',c.id,target]),archive:c=>calls.push(['archive',c.id]),
  remove:async c=>{calls.push(['remove',c.id]);},stop:async c=>{calls.push(['stop',c.id]);},...extra};
}
test('rename happens in the row: the name is selected, Return saves a changed name, Escape or an unchanged name saves nothing',async()=>{
 const calls=[];
 const props={projects:[{id:'p',name:'dervo app',cwd:'/x'}],projectId:'p',projectsLoading:false,projectsFailed:false,onRetryProjects(){},onSelectProject(){},onAddProject(){},ready:true,
  conversations:[conv('Connector picker',{organisationGroup:'Orchestrator'})],channelId:'',conversationActive:true,onSelectConversation(){},onAction(){},onCreate(){},onJump(){},
  accounts:[],onUsage(){},onWorkspaceSettings(){},onConnectedAccounts(){},onFeedback(){},canSignOut:false,onSignOut:async()=>{},threadMenu:model([]),
  renaming:{id:'Connector picker'},onRename:(c,name)=>calls.push([c.id,name]),onUndo(){},accountNote:''};
 host(()=>[]);
 const view=render(withClient(h(DervoSidebar,props)));
 const field=screen.getByRole('textbox',{name:'Rename Connector picker'});
 await waitFor(()=>assert.ok(document.activeElement===field));
 assert.equal(field.selectionStart,0);assert.equal(field.selectionEnd,'Connector picker'.length,'the whole name is selected');
 assert.match(document.querySelector('.dv-tip').textContent,/↵ Save.*esc Cancel/);
 fireEvent.change(field,{target:{value:'  Connector picker v2 '}});
 fireEvent.keyDown(field,{key:'Enter'});
 assert.deepEqual(calls.at(-1),['Connector picker','Connector picker v2']);
 view.unmount();
 render(withClient(h(DervoSidebar,props)));
 fireEvent.keyDown(screen.getByRole('textbox',{name:'Rename Connector picker'}),{key:'Escape'});
 assert.deepEqual(calls.at(-1),['Connector picker',null],'Escape cancels');
 cleanup();
 render(withClient(h(DervoSidebar,props)));
 fireEvent.keyDown(screen.getByRole('textbox',{name:'Rename Connector picker'}),{key:'Enter'});
 assert.deepEqual(calls.at(-1),['Connector picker',null],'unchanged saves nothing');
 cleanup();
 render(withClient(h(DervoSidebar,{...props,renaming:{id:'Connector picker',error:'Couldn’t rename it. The name is too long.'}})));
 assert.match(screen.getByRole('alert').textContent,/Couldn’t rename it/,'a failed save keeps the field with the reason under the row');
 assert.ok(screen.getByRole('textbox',{name:'Rename Connector picker'}));
});

// ---------------------------------------------------------------- Adding a project: which account
const profiles=[
 {id:'cs',provider:'claude',identity:'bryce@trydervo.com',category:'personal',method:'subscription',name:'Personal',plan:'max'},
 {id:'cd',provider:'claude',identity:'bryce@acme.com',category:'personal',method:'subscription',name:'Acme',plan:'team'},
 {id:'cx',provider:'codex',identity:'bryce@trydervo.com',category:'personal',method:'subscription'},
 {id:'wk',provider:'claude',identity:'someone@work.test',category:'work',method:'subscription'},
];
const facts={path:'/Users/bryce/code/shop',name:'shop',git:true,repo:'shop',branch:'main',uncommitted:2,stack:['Next.js','TypeScript']};

test('a new project starts on the account the project you’re in uses, and only your own accounts are offered',async()=>{
 const accounts=accountsFrom(profiles,[]);
 assert.deepEqual(accounts.map(a=>a.id),['cs','cd','cx'],'a work profile is never offered');
 assert.equal(defaultAccount(accounts,'cd'),'cd','a Acme project’s next project starts on Acme');
 assert.equal(defaultAccount(accounts,'gone'),'cs');
 assert.equal(defaultAccount([],'cd'),null);
 assert.equal(folderLine(facts),'A git repository on main with 2 uncommitted files. Looks like Next.js and TypeScript.');
 assert.equal(folderLine({...facts,git:false,stack:[]}),'Not a git repository yet.');
 assert.equal(await modelFor({id:'cd',provider:'claude'},{profile_id:'cd',model:'claude-sonnet-5'}),'claude-sonnet-5','same account, same model');
 assert.equal(await modelFor({id:'cs',provider:'claude'},{profile_id:'cd',model:'claude-sonnet-5'}),'claude-opus-5');
});

test('a new Claude project starts on Opus 5.5 once this Mac’s Claude Code can run it',async()=>{
 let version='2.1.280';
 const calls=host(operation=>operation==='claude_version'?{state:'ok',version,floor:'2.1.271',command:null,detail:''}:null);
 assert.equal(await modelFor({id:'cs',provider:'claude'}),'claude-opus-5-5');
 version='2.1.278';
 assert.equal(await modelFor({id:'cs',provider:'claude'}),'claude-opus-5','an older Claude Code starts on Opus 5');
 assert.equal(await modelFor(undefined),'claude-opus-5');
 assert.deepEqual(calls.filter(c=>c.operation==='claude_version').map(c=>c.args),[{recheck:false},{recheck:false},{recheck:false}],'the kept version is read, never a fresh check');
 assert.equal(await modelFor({id:'cs',provider:'claude'},{profile_id:'cs',model:'claude-sonnet-5'}),'claude-sonnet-5','the project you’re in keeps its own model');
});

test('adding a project sends the chosen account with the folder, name and access, and Cancel creates nothing',async()=>{
 const calls=host(op=>op==='first_run_status'?{account:{state:'ready',detail:''},claude:{state:'ready',detail:'',profile_id:'cs'},codex:{state:'ready',detail:''},git:{state:'ready',detail:''},projects:2,checked_at:1}
  :op==='first_run_finish'?{id:'new',channelId:'c1',name:'shop',access:'edit',overview:{channelId:'ov'}}:null);
 const done=[];let closed=0;
 const staged={selectionId:'sel-1',facts};
 const element=current=>withClient(h(AddProjectDialog,{staged,onChangeFolder(){},onClose:()=>{closed++;},profiles,usage:[],current,onConnect(){},onDone:created=>{done.push(created.id);}}));
 render(element({name:'client site',profile_id:'cd',model:'claude-sonnet-5'}));
 assert.equal(screen.getByRole('heading',{name:'Add shop'}).tagName,'H2');
 const account=screen.getByRole('button',{name:/^Account for new threads in this project: Claude, Acme/});
 assert.ok(account,'it starts on the account the project you’re in uses');
 assert.match(document.body.textContent,/New threads here use it\. Same as client site\./);
 const name=screen.getByRole('textbox',{name:'Name'});
 await waitFor(()=>assert.ok(document.activeElement===name));
 fireEvent.change(name,{target:{value:'  Shop  '}});
 fireEvent.click(screen.getByRole('radio',{name:'Read only'}));
 fireEvent.click(screen.getByRole('button',{name:'Add project'}));
 await waitFor(()=>assert.deepEqual(done,['new']));
 assert.deepEqual(calls.find(c=>c.operation==='first_run_finish').args,{selectionId:'sel-1',profileId:'cd',model:'claude-sonnet-5',name:'Shop',access:'read'});
 cleanup();
 const cancelCalls=host(op=>op==='first_run_status'?{account:{state:'ready'},claude:{state:'ready',profile_id:'cs'},codex:{state:'ready'},git:{state:'ready'},projects:2,checked_at:1}:null);
 render(element(undefined));
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 assert.equal(closed,1);
 assert.equal(cancelCalls.some(c=>c.operation==='first_run_finish'),false,'nothing is created');
});

test('a folder that isn’t a git repository starts read only, and editing says why it needs git',async()=>{
 host(op=>op==='first_run_status'?{account:{state:'ready'},claude:{state:'ready',profile_id:'cs'},codex:{state:'ready'},git:{state:'ready'},projects:2,checked_at:1}:null);
 render(withClient(h(AddProjectDialog,{staged:{selectionId:'s',facts:{...facts,git:false}},onChangeFolder(){},onClose(){},profiles,usage:[],onConnect(){},onDone(){}})));
 assert.equal(screen.getByRole('radio',{name:'Read only'}).getAttribute('aria-checked'),'true');
 assert.equal(screen.getByRole('radio',{name:'Edit, with your review'}).disabled,true);
 assert.match(document.body.textContent,/Editing needs git, so every change can be undone\. Run git init there first\./);
});

