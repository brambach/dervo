import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {CodingPermissionDialog}=await import('./CodingPermission.tsx');
const {taskProgress}=await import('./CodingTasks.tsx');
const {grantPayload}=await import('./api.ts');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const mount=(element)=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:client()},element)));
const permission=(folders)=>({state:'granted',detail:'Coding here may read or change 2 granted file(s).',files:['greeting.py','test_sample.py'],folders,creation_detail:folders.length?`Inside ${folders.join(', ')} coding may list, search, read, edit and create regular files. Dot names, secret-named paths, links and host data stay denied.`:'No folder is granted: coding lists nothing, searches nothing and creates no file.',folder_risk:'Name rules do not prove the other files in a granted folder hold no secrets.',test_file:'test_sample.py',provider:'codex',candidates:['greeting.py','test_sample.py'],test_candidates:['test_sample.py'],created_at:1,actor:'local-user',owner_effort:null,owner_effort_detail:'',acceptance:{required:false,source:'default',detail:''}});
const binding={provider:'codex',identity:'me@example.test',model:'synthetic-model',cwd:'/approved',visibility:'private'};
const dialog=()=>React.createElement(CodingPermissionDialog,{open:true,onOpenChange(){},projectId:'p',channelId:'c',binding,refresh:async()=>{}});
const grants=calls=>calls.filter(c=>c.operation==='coding_permission_grant').map(c=>c.args);

test('grant payload: a file-only grant keeps its older shape; named folders are trimmed, deduplicated and sent once',()=>{
 assert.deepEqual(grantPayload(['a.py','test_a.py'],'test_a.py',['','  ']),{files:['a.py','test_a.py'],testFile:'test_a.py'});
 assert.deepEqual(grantPayload(['a.py'],'test_a.py',[' src ','src/','tests','src']),{files:['a.py'],testFile:'test_a.py',folders:['src','tests']});
});

test('the permission form shows the granted folders and creation authority once, and sends folders only when named',async()=>{
 let saved=permission([]);
 const calls=host((op,args)=>{if(op==='coding_permission')return saved;if(op==='coding_permission_grant'){saved=permission(args.folders??[]);return saved;}throw new Error(op);});
 mount(dialog());
 const folders=await screen.findByLabelText('Granted folders');
 await waitFor(()=>assert.match(screen.getByTestId('coding-permission-state').textContent,/Allowed: 2 file\(s\); tests run test_sample.py/));
 assert.match(screen.getByTestId('creation-scope').textContent,/No folder is granted/);
 assert.doesNotMatch(screen.getByTestId('coding-permission-state').textContent,/folder\(s\)/,'no default folder grant is implied');
 fireEvent.click(screen.getByRole('button',{name:/^(Allow coding|Save file access)$/}));
 await waitFor(()=>assert.equal(grants(calls).length,1));
 assert.deepEqual(grants(calls)[0],{projectId:'p',channelId:'c',files:['greeting.py','test_sample.py'],testFile:'test_sample.py',checkTimeoutSeconds:5},'a file-only grant carries no folders key');
 fireEvent.change(folders,{target:{value:'src\n\ndocs'}});
 fireEvent.click(screen.getByRole('button',{name:/^(Allow coding|Save file access)$/}));
 await waitFor(()=>assert.equal(grants(calls).length,2));
 assert.deepEqual(grants(calls)[1].folders,['src','docs']);
 await waitFor(()=>assert.match(screen.getByTestId('coding-permission-state').textContent,/2 file\(s\), 2 folder\(s\)/));
 assert.match(screen.getByTestId('creation-scope').textContent,/Inside src, docs/);
 assert.ok(!calls.some(c=>c.operation==='coding_request'||c.operation==='task_create'),'granting never starts coding');
 assert.match(document.body.textContent,/Not granted: the project root, files or folders outside these lists/);
 assert.match(screen.getByTestId('folder-risk').textContent,/do not prove/,'the truthful limit on name filtering stays visible');
 assert.match(screen.getByTestId('creation-scope').textContent,/stay denied/);
});

test('the working-task progress line names the folder tools from the host record, never a success claim',()=>{
 const token=(tool,state,path,attempt=null)=>({tool,state,path,attempt});
 assert.equal(taskProgress({state:'working',tokens:[token('dervo_list_files','executing','src')]}),'Listing src');
 assert.equal(taskProgress({state:'working',tokens:[token('dervo_search_files','delivered','src/pkg')]}),'Searched src/pkg. Waiting for the provider.');
 assert.equal(taskProgress({state:'working',tokens:[token('dervo_create_file','running','src/new.py',2)]}),'Creating src/new.py (attempt 2)');
});
