import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ConnectionPreflight}=await import('./ConnectionPreflight.tsx');
after(()=>dom.window.close());afterEach(cleanup);
function mount(provider='codex', override={}) {
 const calls=[];let login={state:'idle',active:false,expectedIdentity:'personal@example.test'};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0},mutations:{retry:false}}});
 window.__TAURI_INTERNALS__={invoke:async(_, {operation,args})=>{
  calls.push({operation,args});
  if(override[operation])return override[operation](args);
  if(operation==='connection_login')login={...login,state:'waiting',phase:'browser',active:true,detail:'Complete browser sign-in. Identity is unverified.'};
  if(operation==='connection_login_cancel')login={...login,state:'cancelled',active:false,detail:'Sign-in cancelled. Retry Connect Codex.'};
  if(operation.startsWith('connection_login'))return login;
  if(['connection_status','connection_check'].includes(operation))return {state:'unknown',canSend:false,detail:'Identity unknown. No messages were sent.'};
  throw Error('Unexpected operation '+operation);
 }};
 const element=(profileId='p')=>React.createElement(QueryClientProvider,{client},React.createElement(ConnectionPreflight,{profileId,provider}));
 return {...render(element()),calls,client,element};
}
test('Connect Codex is explicit, shows expected identity, browser and cancel, never sends',async()=>{
 const v=mount();await v.findByText('personal@example.test');
 assert.equal(v.calls.some(c=>c.operation==='connection_login'),false);
 fireEvent.click(v.getByRole('button',{name:'Connect Codex'}));
 await v.findByText(/Complete browser sign-in/);
 assert.equal(v.getByRole('button',{name:'Check identity'}).disabled,true);
 assert.equal(v.queryByLabelText('Sign-in code'),null);
 fireEvent.click(v.getByRole('button',{name:'Cancel sign-in'}));await v.findByText(/Sign-in cancelled/);
 assert.equal(v.getByRole('button',{name:'Connect Codex'}).disabled,false);
 assert.ok(v.calls.every(c=>c.operation.startsWith('connection_')));
});
test('Codex transport errors never echo dummy tokens and offer retry',async()=>{
 const v=mount('codex',{connection_login:()=>{throw Error('DUMMY-TOKEN https://evil.test');}});
 fireEvent.click(v.getByRole('button',{name:'Connect Codex'}));
 await waitFor(()=>assert.match(v.getByRole('alert').textContent,/retry|Retry/));
 assert.doesNotMatch(v.container.textContent,/DUMMY|evil.test/);
});
for(const state of ['starting','failed','cancelled','timed_out'])test('Codex visible '+state,async()=>{
 const v=mount('codex',{connection_login_status:()=>({state:state==='starting'?'waiting':state,phase:state,active:state==='starting',expectedIdentity:'personal@example.test',detail:'Synthetic '+state,url:'https://evil.test/?token=DUMMY'})});
 await v.findByText('Synthetic '+state);assert.equal(v.queryByLabelText('Official sign-in link'),null);
 assert.match(v.container.textContent,/Identity unknown/);
});
test('Claude retains connect, code and original error behavior',async()=>{
 const v=mount('claude',{connection_login_status:()=>({state:'waiting',active:true,needsCode:true,detail:'Paste code here',url:'https://claude.ai/login'})});
 await v.findByLabelText('Sign-in code');assert.ok(v.getByRole('button',{name:'Connect Claude'}));
 assert.equal(v.queryByRole('button',{name:'Connect Codex'}),null);
 assert.equal(v.getByLabelText('Official sign-in link').value,'https://claude.ai/login');
});
test('profile switch clears local manual steps and error',async()=>{
 const v=mount('codex',{connection_login:()=>{throw Error('DUMMY');}});
 fireEvent.click(v.getByRole('button',{name:'Connect Codex'}));await v.findByRole('alert');
 v.rerender(v.element('other'));await waitFor(()=>assert.equal(v.queryByRole('alert'),null));
 assert.ok(v.calls.some(c=>c.args.profileId==='other'));
});
for(const state of ['identity_verified','identity_mismatch','unknown'])test('successful CLI exit retains '+state+' without sending',async()=>{
 const v=mount('codex',{
  connection_login_status:()=>({state:'succeeded',active:false,detail:'Process finished. Checking identity.',identity:{state,canSend:false,detail:state}}),
  connection_status:()=>({state,canSend:false,detail:state}),
 });
 await v.findByText(state);assert.ok(v.calls.every(c=>c.operation.startsWith('connection_')));
});
test('lost observation hides a previously verified identity',async()=>{
 let lost=false;
 const v=mount('codex',{connection_status:()=>{if(lost)throw Error('DUMMY-TOKEN');return {state:'identity_verified',canSend:false,detail:'Synthetic verified identity'};}});
 await v.findByText('Synthetic verified identity');lost=true;
 fireEvent.click(v.getByRole('button',{name:'Check identity'}));
 await v.findByText('Identity is unknown. Retry Check identity. No messages were sent.');
 assert.equal(v.queryByText('Synthetic verified identity'),null);assert.doesNotMatch(v.container.textContent,/DUMMY/);
});
test('pending explicit start displays starting without claiming browser success',async()=>{
 let finish;
 const v=mount('codex',{connection_login:()=>new Promise(resolve=>{finish=resolve;})});
 fireEvent.click(v.getByRole('button',{name:'Connect Codex'}));
 await v.findByText('Starting official Codex sign-in. Identity is unverified.');
 assert.equal(v.getByRole('button',{name:'Connect Codex'}).disabled,true);
 finish({state:'waiting'});
 await waitFor(()=>assert.ok(!v.queryByText('Starting official Codex sign-in. Identity is unverified.')));
});
