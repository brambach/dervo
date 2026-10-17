import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {ConnectionPreflight}=await import('./ConnectionPreflight.tsx');
after(()=>dom.window.close());
const idle={state:'idle',phase:null,url:null,needsCode:false,active:false,detail:'Not signed in through Dervo yet.'};
function mount(provider,handler){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args,calls);}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const view=render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client},React.createElement(ConnectionPreflight,{profileId:'p',provider}))));
 return {view,calls,client};
}
test('Connect Claude starts the owned sign-in, shows waiting state, cancels, and re-checks identity on success',async()=>{
 let login={...idle};
 let identity={state:'unchecked',canSend:false,detail:'Check the selected account before use.'};
 const {view,calls,client}=mount('claude',(operation)=>{
  if(operation==='connection_status')return identity;
  if(operation==='connection_login_status')return login;
  if(operation==='connection_login'){login={state:'waiting',phase:'browser',url:'https://claude.ai/oauth/authorize?state=x',needsCode:false,active:true,detail:'Your browser should open the official Claude sign-in page.'};return login;}
  if(operation==='connection_login_cancel'){login={...login,state:'cancelled',active:false,detail:'Sign-in cancelled.'};return login;}
  throw Error(operation);
 });
 try{
  const connect=await view.findByRole('button',{name:'Connect Claude'});
  assert.equal(calls.filter(c=>c.operation==='connection_login').length,0,'nothing starts before the click');
  fireEvent.click(connect);
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='connection_login').length,1));
  await view.findByText('Your browser should open the official Claude sign-in page.');
  assert.equal(view.getByLabelText('Official sign-in link').value,'https://claude.ai/oauth/authorize?state=x');
  assert.equal(view.queryByLabelText('Sign-in code'),null,'no code field unless the CLI asks');
  assert.equal(view.getByRole('button',{name:'Connect Claude'}).disabled,true);
  fireEvent.click(view.getByRole('button',{name:'Cancel sign-in'}));
  await view.findByText('Sign-in cancelled.');
  assert.equal(view.getByRole('button',{name:'Connect Claude'}).disabled,false);
  // Success path: the backend attaches the identity probe result; the UI refreshes the ordinary status.
  const before=calls.filter(c=>c.operation==='connection_status').length;
  identity={state:'identity_verified',canSend:false,detail:'Personal identity matches.'};
  login={state:'succeeded',phase:'finishing',url:null,needsCode:false,active:false,detail:'Sign-in finished.',identity};
  await client.refetchQueries({queryKey:['connection-login','p']});
  await waitFor(()=>assert.ok(calls.filter(c=>c.operation==='connection_status').length>before,'identity status re-read after success'));
  await view.findByText('Personal identity matches.');
  assert.equal(view.queryByLabelText('Claude sign-in progress'),null,'completed login progress does not outlive its identity check');
  assert.ok(view.getByRole('button',{name:'Sign in again'}));
  assert.ok(view.getByRole('button',{name:'Manual sign-in steps'}),'manual steps remain as the fallback');
 }finally{cleanup();client.clear();}
});
test('a paste-code prompt shows a code field that forwards once',async()=>{
 let login={state:'waiting',phase:'code_prompt',url:'https://claude.ai/oauth/authorize?state=y',needsCode:true,active:true,detail:'Claude Code is asking for the code shown in your browser.'};
 const {view,calls,client}=mount('claude',(operation,args)=>{
  if(operation==='connection_status')return {state:'unchecked',canSend:false,detail:'x'};
  if(operation==='connection_login_status')return login;
  if(operation==='connection_login_code'){assert.equal(args.code,'abc-123');login={...login,needsCode:false,phase:'starting',detail:'Code sent to Claude Code.'};return login;}
  throw Error(operation);
 });
 try{
  const field=await view.findByLabelText('Sign-in code');
  fireEvent.change(field,{target:{value:'abc-123'}});
  fireEvent.click(view.getByRole('button',{name:'Submit code'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='connection_login_code').length,1));
  await view.findByText('Code sent to Claude Code.');
  assert.equal(view.queryByLabelText('Sign-in code'),null);
 }finally{cleanup();client.clear();}
});
test('a pasted code is dropped on cancel and on a terminal state, and a rejection never echoes it',async()=>{
 const asking={state:'waiting',phase:'code_prompt',url:'https://claude.ai/oauth/authorize?state=z',needsCode:true,active:true,detail:'Claude Code is asking for the code shown in your browser.'};
 let login={...asking};
 const {view,calls,client}=mount('claude',(operation,args)=>{
  if(operation==='connection_status')return {state:'unchecked',canSend:false,detail:'x'};
  if(operation==='connection_login_status')return login;
  if(operation==='connection_login_cancel'){login={...login,state:'cancelled',active:false,needsCode:false,url:null,detail:'Sign-in cancelled.'};return login;}
  if(operation==='connection_login'){login={...asking};return login;}
  if(operation==='connection_login_code')throw new Error(`synthetic host echo ${args.code}`);
  throw Error(operation);
 });
 try{
  const field=await view.findByLabelText('Sign-in code');
  fireEvent.change(field,{target:{value:'DUMMY-CODE-not-a-credential'}});
  fireEvent.click(view.getByRole('button',{name:'Cancel sign-in'}));
  await view.findByText('Sign-in cancelled.');
  assert.equal(view.queryByLabelText('Sign-in code'),null);
  fireEvent.click(view.getByRole('button',{name:'Connect Claude'}));
  const again=await view.findByLabelText('Sign-in code');
  assert.equal(again.value,'','the earlier code does not come back with the new sign-in');
  // A host rejection that (hypothetically) repeats the code is masked before it reaches the screen.
  fireEvent.change(again,{target:{value:'DUMMY-CODE-not-a-credential'}});
  fireEvent.click(view.getByRole('button',{name:'Submit code'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='connection_login_code').length,1));
  const alert=await view.findByRole('alert');
  assert.equal(alert.textContent.includes('DUMMY-CODE-not-a-credential'),false,alert.textContent);
  assert.match(alert.textContent,/\[code\]/);
  assert.equal(view.getByLabelText('Sign-in code').value,'','the field is cleared once a code was handed to the host');
  // The CLI finishing (success or failure) ends the prompt; nothing pasted survives it.
  fireEvent.change(view.getByLabelText('Sign-in code'),{target:{value:'DUMMY-CODE-2'}});
  login={...login,state:'failed',active:false,needsCode:false,url:null,detail:'Sign-in did not complete (exit status 1).'};
  await client.refetchQueries({queryKey:['connection-login','p']});
  await view.findByText('Sign-in did not complete (exit status 1).');
  assert.equal(document.body.textContent.includes('DUMMY-CODE-2'),false);
 }finally{cleanup();client.clear();}
});
test('Codex profiles poll their login status and expose explicit Connect Codex without Connect Claude',async()=>{
 let login={...idle,expectedIdentity:'codex@example.test'};
 const {view,calls,client}=mount('codex',(operation)=>{
  if(operation==='connection_status')return {state:'unchecked',canSend:false,detail:'Check the selected account before use.'};
  if(operation==='connection_login_status')return login;
  if(operation==='connection_login'){login={...login,state:'waiting',phase:'browser',active:true,detail:'Complete official Codex browser sign-in.'};return login;}
  throw Error(operation);
 });
 try{
  await view.findByText('Check the selected account before use.');
  await view.findByText('codex@example.test');
  assert.equal(view.queryByRole('alert'),null);
  assert.equal(view.queryByRole('button',{name:'Connect Claude'}),null);
  assert.ok(view.getByRole('button',{name:'Official sign-in steps'}));
  assert.ok(view.getByRole('button',{name:'Check identity'}));
  assert.ok(calls.some(c=>c.operation==='connection_login_status'&&c.args.profileId==='p'));
  assert.equal(calls.filter(c=>c.operation==='connection_login').length,0);
  fireEvent.click(view.getByRole('button',{name:'Connect Codex'}));
  await view.findByText('Complete official Codex browser sign-in.');
  assert.deepEqual(calls.filter(c=>c.operation==='connection_login'),[{operation:'connection_login',args:{profileId:'p'}}]);
  assert.equal(view.getByRole('button',{name:'Check identity'}).disabled,true);
  assert.equal(view.queryByLabelText('Sign-in code'),null);
  assert.equal(view.queryByLabelText('Official sign-in link'),null);
  assert.ok(calls.every(c=>['connection_status','connection_login_status','connection_login'].includes(c.operation)));
 }finally{cleanup();client.clear();}
});

test('identity check is disabled during sign-in and retries after internal failure',async()=>{
 let login={...idle,state:'waiting',active:true,detail:'Waiting for synthetic sign-in.'};
 let identity={state:'checking',canSend:false,detail:'Earlier identity check.'};
 const {view,calls,client}=mount('claude',(operation)=>{
  if(operation==='connection_login_status')return login;
  if(operation==='connection_status')return identity;
  if(operation==='connection_check'){identity={state:'identity_verified',canSend:false,detail:'Synthetic identity matches.'};return identity;}
  throw Error(operation);
 });
 try{
  await view.findByText('Waiting for synthetic sign-in.');
  identity={state:'unchecked',canSend:false,detail:'Check the selected account before use.'};
  await client.refetchQueries({queryKey:['connection-status','p']});
  await view.findByText(identity.detail);
  const check=view.getByRole('button',{name:'Check identity'});
  assert.equal(check.disabled,true);
  fireEvent.click(check);
  assert.equal(calls.filter(c=>c.operation==='connection_check').length,0);
  login={...idle,state:'failed',detail:'Synthetic sign-in failed.'};
  // A terminal login refresh must also clear a previously cached checking state.
  client.setQueryData(['connection-status','p'],{state:'checking',canSend:false,detail:'Earlier identity check.'});
  const before=calls.filter(c=>c.operation==='connection_status').length;
  await client.refetchQueries({queryKey:['connection-login','p']});
  await view.findByText(login.detail);
  await waitFor(()=>assert.ok(calls.filter(c=>c.operation==='connection_status').length>before));
  await waitFor(()=>assert.equal(check.disabled,false));
  fireEvent.click(check);
  await view.findByText('Synthetic identity matches.');
  assert.equal(calls.filter(c=>c.operation==='connection_check').length,1);
  assert.ok(calls.every(c=>!['queue','start_turn'].includes(c.operation)));
 }finally{cleanup();client.clear();}
});

test('fresh identity controls connected presentation when a previous login is terminal',async()=>{
 let identity={state:'identity_verified',canSend:false,detail:'Personal account verified.'};
 let failStatus=false;
 const {view,calls,client}=mount('claude',(operation)=>{
  if(operation==='connection_login_status')return {...idle,state:'succeeded',detail:'Sign-in finished. Checking identity.',identity:{state:'checking'}};
  if(operation==='connection_status'){if(failStatus)throw Error('Synthetic status unavailable');return identity;}
  if(operation==='connection_check'){identity={state:'signed_out',canSend:false,detail:'This account is signed out.'};return identity;}
  throw Error(operation);
 });
 try{
  await view.findByRole('button',{name:'Sign in again'});
  assert.equal(view.queryByText('Sign-in finished. Checking identity.'),null);
  assert.equal(view.queryByLabelText('Official sign-in link'),null);
  failStatus=true;
  await client.refetchQueries({queryKey:['connection-status','p']});
  await view.findByRole('alert');
  assert.equal(view.queryByRole('button',{name:'Sign in again'}),null,'cached verified data cannot mask a failed status read');
  assert.equal(view.queryByText('Personal account verified.'),null);
  failStatus=false;
  fireEvent.click(view.getByRole('button',{name:'Check identity'}));
  await view.findByText('This account is signed out.');
  assert.ok(view.getByRole('button',{name:'Connect Claude'}));
  assert.equal(view.queryByLabelText('Claude sign-in progress'),null);
  assert.equal(calls.filter(c=>c.operation==='connection_login').length,0,'rendering and checks never start sign-in');
 }finally{cleanup();client.clear();}
});

test('Check identity recovers a transient login-status query failure without starting sign-in',async()=>{
 let failLogin=false;
 const identity={state:'identity_verified',canSend:false,detail:'Fresh synthetic identity matches.'};
 const {view,calls,client}=mount('claude',operation=>{
  if(operation==='connection_status'||operation==='connection_check')return identity;
  if(operation==='connection_login_status'){if(failLogin)throw Error('Transient login status read');return {...idle,state:'succeeded',identity};}
  throw Error(operation);
 });
 try{
  await view.findByRole('button',{name:'Sign in again'});
  failLogin=true;
  await client.refetchQueries({queryKey:['connection-login','p']});
  await view.findByRole('alert');
  failLogin=false;
  fireEvent.click(view.getByRole('button',{name:'Check identity'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='connection_check').length,1));
  await waitFor(()=>assert.equal(!!view.queryByRole('alert'),false,'successful retry must clear the failed observation'));
  assert.ok(view.getByRole('button',{name:'Sign in again'}));
  assert.equal(calls.filter(c=>c.operation==='connection_login').length,0);
 }finally{cleanup();client.clear();}
});
