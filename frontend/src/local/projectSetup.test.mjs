import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {ReleasePreview}=await import('./ReleasePreview.tsx');
const {ProjectModel}=await import('./ProjectModel.tsx');
const {TaskDialog,checkProfileLabel,checkExitDetail}=await import('./CodingTasks.tsx');
const {grantPayload,CHECK_TEST_NAMES}=await import('./api.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const mount=(element)=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:client()},element)));

const frame=(component,props)=>React.createElement(component,props);

const profiles=[{id:'c',provider:'claude',identity:'personal@example.test',category:'personal'},{id:'x',provider:'codex',identity:'second@example.test',category:'personal'}];
const folder={selectionId:'chosen-folder',path:'/test/selected/app',name:'app'};
function setupHost(extra=()=>{},connectionState=()=>'identity_verified'){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  calls.push({command,...args});
  if(command==='choose_project_folder')return folder;
  if(command!=='local_request')return;
  if(args.operation==='connection_status')return {state:connectionState(),canSend:false,detail:'Fixture identity status.'};
  if(args.operation==='connection_login_status')return {state:'idle',active:false,detail:'Not signed in.'};
  if(args.operation==='model_status'||args.operation==='model_check')return {state:'catalog',models:[{model:'codex-model',displayName:'Codex model'}],detail:'Verified fixture catalog'};
  return extra(args.operation,args.args);
 }};
 return calls;
}
const defaults={open:true,profiles,onClose(){},onProfilesChanged:async()=>{},onCreated:async()=>{}};
async function agentStep(){
 fireEvent.click(screen.getByRole('button',{name:/Choose a project folder/}));
 await screen.findByLabelText('Project name');
 fireEvent.change(screen.getByLabelText('Project name'),{target:{value:'Dervo'}});
 fireEvent.click(screen.getByRole('button',{name:'Choose an agent'}));
}

test('model selection needs explicit save and passes the expected project default',async()=>{
 let resolveSave;const calls=setupHost(op=>{if(op==='project_model_set')return new Promise(resolve=>{resolveSave=resolve;});});
 mount(frame(ProjectModel,{projectId:'one',profileId:'c',provider:'claude',savedModel:'claude-fable-5-1'}));
 fireEvent.click(screen.getByRole('button',{name:'Change default model'}));
 fireEvent.click(screen.getByRole('button',{name:/Sonnet 5/}));
 assert.equal(calls.filter(c=>c.operation==='project_model_set').length,0);
 const save=screen.getByRole('button',{name:'Save default model'});fireEvent.click(save);fireEvent.click(save);
 assert.deepEqual(calls.filter(c=>c.operation==='project_model_set').map(c=>c.args),[{projectId:'one',expectedModel:'claude-fable-5-1',model:'claude-sonnet-5'}]);
 assert.ok(screen.getByRole('button',{name:'Cancel'}).disabled);
 resolveSave({id:'one',model:'claude-sonnet-5'});
 await screen.findByRole('button',{name:'Change default model'});
 assert.equal(calls.some(c=>/^(coding_request|queue|start_turn|connection_login)$/.test(c.operation??'')),false);
});

test('a conversation model change targets that conversation only and refreshes its binding, never the project default',async()=>{
 let resolveSave;const calls=setupHost(op=>{if(op==='conversation_model_set')return new Promise(resolve=>{resolveSave=resolve;});});
 mount(frame(ProjectModel,{projectId:'one',channelId:'chat',profileId:'c',provider:'claude',savedModel:'claude-fable-5-1'}));
 assert.ok(screen.getByText('Model for this conversation'));
 assert.match(screen.getByText(/keep the model they were approved with/).textContent,/can’t be continued/);
 fireEvent.click(screen.getByRole('button',{name:'Change conversation model'}));
 fireEvent.click(screen.getByRole('button',{name:/Sonnet 5/}));
 fireEvent.click(screen.getByRole('button',{name:'Save conversation model'}));
 assert.deepEqual(calls.filter(c=>c.operation==='conversation_model_set').map(c=>c.args),[{projectId:'one',channelId:'chat',expectedModel:'claude-fable-5-1',model:'claude-sonnet-5'}]);
 assert.equal(calls.some(c=>c.operation==='project_model_set'),false,'the project default is never sent');
 resolveSave({id:'chat',project_id:'one',model:'claude-sonnet-5'});
 await screen.findByRole('button',{name:'Change conversation model'});
});

test('a rejected model change retains the selection and explains the failure',async()=>{
 setupHost(op=>{if(op==='project_model_set')throw new Error('Finish active project work.');});
 mount(frame(ProjectModel,{projectId:'one',profileId:'c',provider:'claude',savedModel:'claude-fable-5-1'}));
 fireEvent.click(screen.getByRole('button',{name:'Change default model'}));fireEvent.click(screen.getByRole('button',{name:/Sonnet 5/}));
 fireEvent.click(screen.getByRole('button',{name:'Save default model'}));
 assert.match((await screen.findByRole('alert')).textContent,/Finish active project work/);
 assert.equal(screen.getByRole('button',{name:/Sonnet 5/}).getAttribute('aria-pressed'),'true');
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 assert.equal(screen.queryByRole('alert'),null);
});
