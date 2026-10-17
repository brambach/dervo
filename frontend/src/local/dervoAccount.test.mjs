import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','CustomEvent','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,screen,waitFor,cleanup,act,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {DervoAccount,DervoAccountNotice,DervoAccountIdentity}=await import('./DervoAccount.tsx');
const {ProfilePhoto,photoImage}=await import('./ProfilePhoto.tsx');
const {planSummary,trialEnding,PRICES}=await import('./DervoPlan.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
const blank={state:'signed_out',detail:'Synthetic Dervo session status.',identity:null,operation_id:null,can_start:true,can_cancel:false,can_poll:false,can_restore:false,can_sign_out:false,ownership:'local_unclaimed',sync_available:false};
function mount(handler,component=React.createElement(DervoAccount)){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});render(React.createElement(QueryClientProvider,{client},component));return {calls,client};}

test('sidebar hides cached identity and photo when account observation fails',async()=>{
 let fail=false;
 const {client,calls}=mount(()=>{
  if(fail)throw Error('Synthetic account transport failure');
  return {...blank,state:'signed_in',identity:{issuer:'fake',subject:'test',display_name:'Synthetic User'},photo:{state:'ready',data_url:'data:image/png;base64,QUJD'}};
 },React.createElement(DervoAccountIdentity));
 await screen.findByText(/Synthetic User/);
 assert.ok(document.querySelector('img'));
 fail=true;
 await act(async()=>{await client.refetchQueries({queryKey:['dervo-account']});});
 await screen.findByText('You');
 assert.equal(screen.queryByText(/Synthetic User/),null);
 assert.equal(document.querySelector('img'),null);
 assert.ok(calls.every(call=>call.operation==='dervo_account_status'));
});

test('unconfigured Dervo account is honest and exposes no fake login or provider signout',async()=>{
 const {calls}=mount(()=>({...blank,state:'unavailable',detail:"Dervo sign-in isn't available in this build.",can_start:false}));
 await screen.findByText('Local workspace');
 assert.ok(screen.getByText(/doesn’t use a Dervo sign-in/));
 assert.equal(screen.queryByRole('button',{name:'Sign in'}),null);
 assert.equal(screen.queryByRole('button',{name:'Create an account'}),null);
 assert.equal(screen.queryByRole('button',{name:'Sign out'}),null);
 assert.deepEqual(calls.map(c=>c.operation),['dervo_account_status']);
});

test('signup, pending cancellation and retry use exact Dervo operations without accepting an identity',async()=>{
 let value={...blank};
 const {calls}=mount((op,args)=>{
  if(op==='dervo_account_status')return value;
  if(op==='dervo_account_start'){assert.equal(args.mode,'sign_up');return value={...blank,state:'awaiting_user',operation_id:'first',can_start:false,can_cancel:true};}
  if(op==='dervo_account_cancel'){assert.equal(args.operationId,'first');return value={...blank,state:'cancelled'};}
  throw Error(op);
 });
 fireEvent.click(await screen.findByRole('button',{name:'Create an account'}));
 const cancel=await screen.findByRole('button',{name:'Cancel sign-in'});
 assert.equal(screen.queryByText('Signed in'),null);assert.equal(screen.queryByRole('button',{name:'Sign in'}),null);
 fireEvent.click(cancel);await screen.findByText('Sign-in cancelled');
 assert.ok(screen.getByRole('button',{name:'Sign in'}));
 assert.ok(calls.every(c=>c.operation.startsWith('dervo_account_')));
 assert.ok(calls.every(c=>!('identity' in (c.args??{}))));
});

test('restore verifies a saved identity; signout is separate from provider connections',async()=>{
 let value={...blank,state:'restore_required',can_start:false,can_restore:true};
 const {calls}=mount((op)=>{
  if(op==='dervo_account_status')return value;
  if(op==='dervo_account_restore')return value={...blank,state:'signed_in',can_start:false,can_sign_out:true,identity:{issuer:'fake',subject:'test',display_name:'Synthetic User'}};
  if(op==='dervo_account_sign_out')return value={...blank};
  throw Error(op);
 });
 const restore=await screen.findByRole('button',{name:'Check session'});assert.equal(screen.queryByText(/Synthetic User/),null);
 fireEvent.click(restore);await screen.findByText(/Synthetic User/);
 fireEvent.click(screen.getByRole('button',{name:'Sign out'}));
 assert.ok(!calls.some(c=>c.operation==='dervo_account_sign_out'),'signing out asks first, in place');
 assert.ok(screen.getByRole('group',{name:'Sign out of Dervo?'}));
 await waitFor(()=>assert.equal(document.activeElement,screen.getByRole('button',{name:'Stay signed in'})),'the way out has focus');
 fireEvent.click(screen.getByRole('button',{name:'Stay signed in'}));
 assert.ok(screen.getByText(/Synthetic User/));
 fireEvent.click(screen.getByRole('button',{name:'Sign out'}));
 fireEvent.keyDown(screen.getByRole('button',{name:'Stay signed in'}),{key:'Escape'});
 assert.equal(screen.queryByRole('group',{name:'Sign out of Dervo?'}),null,'Esc keeps you signed in');
 fireEvent.click(screen.getByRole('button',{name:'Sign out'}));
 fireEvent.click(within(screen.getByRole('group',{name:'Sign out of Dervo?'})).getByRole('button',{name:'Sign out'}));
 await screen.findByText('Signed out');assert.equal(screen.queryByText(/Synthetic User/),null);
 assert.ok(calls.every(c=>c.operation.startsWith('dervo_account_')));
});

test('unknown and mismatched identity never show signed-in controls or automatically replay signin',async()=>{
 for(const state of ['unknown','account_mismatch','interrupted']){
  const {calls}=mount(()=>({...blank,state,can_start:false,can_restore:true}));
  await screen.findByRole('button',{name:'Check session'});
  assert.equal(screen.queryByRole('button',{name:'Sign in'}),null);
  assert.equal(screen.queryByRole('button',{name:'Sign out'}),null);
  assert.deepEqual(calls.map(c=>c.operation),['dervo_account_status']);cleanup();
 }
});

test('host error text never leaks and status retry does not resubmit an account mutation',async()=>{
 let throwNow=false;
 const {calls}=mount((op)=>{if(op==='dervo_account_start'){throwNow=true;throw Error('synthetic-sensitive-marker');}if(throwNow)throw Error('synthetic-sensitive-marker');return blank;});
 fireEvent.click(await screen.findByRole('button',{name:'Sign in'}));
 await screen.findByRole('alert');assert.doesNotMatch(document.body.textContent,/synthetic-sensitive-marker/);
 throwNow=false;fireEvent.click(screen.getByRole('button',{name:'Check status'}));
 await waitFor(()=>assert.ok(screen.getByRole('button',{name:'Sign in'})));
 assert.equal(calls.filter(c=>c.operation==='dervo_account_start').length,1);
});

test('signed-in status refreshes at expiry without polling every second throughout the session',async()=>{
 const {accountRefreshInterval}=await import('./DervoAccount.tsx');
 const now=Date.now();const signed={...blank,state:'signed_in',can_start:false,can_sign_out:true,identity:{issuer:'fake',subject:'test',display_name:'Synthetic User'},expires_at:(now+600000)/1000};
 assert.equal(accountRefreshInterval(signed,now),60000);
 assert.equal(accountRefreshInterval({...signed,expires_at:(now+5000)/1000},now),5000);
 assert.equal(accountRefreshInterval({...blank,state:'unknown'},now),false);
 assert.equal(accountRefreshInterval({...blank,can_poll:true},now),1000);
 const expiry=Date.now()+100;
 mount(()=>Date.now()<expiry?{...signed,expires_at:expiry/1000}:{...blank,state:'expired',can_start:false,can_restore:true});
 await screen.findByText(/Synthetic User/);
 await screen.findByText('Session expired',{}, {timeout:2500});
 assert.equal(screen.queryByRole('button',{name:'Sign out'}),null);
});

const emailBlank={...blank,method:'email_code',can_verify:false,can_save_profile:false,membership:null,access_valid_until:null};
const signedEmail={...emailBlank,state:'signed_in',can_start:false,can_sign_out:true,can_save_profile:true,identity:{issuer:'hidden-issuer',subject:'hidden-subject',display_name:'Bryce'},membership:{status:'pending',allowed:false,expires_at:null,release_channel:'private-beta',is_admin:false}};

test('email sign-in uses explicit scoped operations, focuses code and clears it before verification',async()=>{
 let value={...emailBlank},resolveVerify;
 const stored=[];
 const storage=window.Storage.prototype.setItem;
 window.Storage.prototype.setItem=function(...args){stored.push(args);};
 const {calls,client}=mount((operation,args)=>{
  if(operation==='dervo_account_status')return value;
  if(operation==='dervo_account_send_code')return value={...emailBlank,state:'awaiting_user',operation_id:'opaque-operation',can_start:false,can_cancel:true,can_verify:true};
  if(operation==='dervo_account_verify_code')return new Promise(resolve=>{resolveVerify=()=>{value={...signedEmail};resolve(value);};});
  throw Error('No other operation expected');
 });
 try{
  const email=await screen.findByRole('textbox',{name:'Email'});
  assert.equal(screen.queryByRole('button',{name:'Create an account'}),null);
  fireEvent.change(email,{target:{value:'fixture@example.test'}});
  fireEvent.submit(email.closest('form'));
  const code=await screen.findByRole('textbox',{name:'Email code'});
  assert.ok(screen.getByText('Not in your inbox after a minute? Check spam first.'));
  await waitFor(()=>assert.equal(document.activeElement,code));
  assert.doesNotMatch(document.body.textContent,/opaque-operation/);
  fireEvent.change(code,{target:{value:'123456'}});
  fireEvent.submit(code.closest('form'));
  assert.equal(code.value,'');assert.equal(code.disabled,true);
  fireEvent.submit(code.closest('form'));
  assert.equal(calls.filter(call=>call.operation==='dervo_account_verify_code').length,1);
  assert.deepEqual(calls.find(call=>call.operation==='dervo_account_send_code').args,{email:'fixture@example.test'});
  assert.deepEqual(calls.find(call=>call.operation==='dervo_account_verify_code').args,{operationId:'opaque-operation',code:'123456'});
  await act(async()=>resolveVerify());
  await screen.findByText('Pending');
  assert.equal(screen.queryByRole('textbox',{name:'Email code'}),null);
  assert.equal(screen.queryByRole('textbox',{name:'Email'}),null);
  assert.deepEqual(stored,[]);
 }finally{window.Storage.prototype.setItem=storage;cleanup();client.clear();}
});

test('invalid code is not sent and cancellation clears the field before a new operation',async()=>{
 let value={...emailBlank,state:'awaiting_user',can_start:false,can_cancel:true,can_verify:true,operation_id:'first'};
 const {calls,client}=mount(operation=>{
  if(operation==='dervo_account_status')return value;
  if(operation==='dervo_account_cancel')return value={...emailBlank,state:'cancelled'};
  if(operation==='dervo_account_send_code')return value={...emailBlank,state:'awaiting_user',can_start:false,can_verify:true,operation_id:'second'};
  throw Error('Unexpected operation');
 });
 try{
  const code=await screen.findByRole('textbox',{name:'Email code'});
  fireEvent.change(code,{target:{value:'not-code'}});fireEvent.submit(code.closest('form'));
  assert.equal(calls.filter(call=>call.operation==='dervo_account_verify_code').length,0);
  fireEvent.click(screen.getByRole('button',{name:'Use a different email'}));
  const email=await screen.findByRole('textbox',{name:'Email'});
  assert.deepEqual(calls.find(call=>call.operation==='dervo_account_cancel').args,{operationId:'first'});
  fireEvent.change(email,{target:{value:'fixture@example.test'}});fireEvent.submit(email.closest('form'));
  const second=await screen.findByRole('textbox',{name:'Email code'});
  assert.equal(second.value,'');await waitFor(()=>assert.equal(document.activeElement,second));
 }finally{cleanup();client.clear();}
});

test('membership states are distinct and photos never upload just by opening settings',async()=>{
 for(const [changes,label] of [[{},'Pending'],[{status:'approved',allowed:true},'Approved'],[{status:'revoked'},'Removed'],[{status:'approved',allowed:false,expires_at:'2000-01-01T12:00:00Z'},/^Ended 1 Jan 2000$/]]){
  const {calls,client}=mount(()=>({...signedEmail,membership:{...signedEmail.membership,...changes}}));
  try{
   await screen.findByText(label);
   assert.doesNotMatch(document.body.textContent,/hidden-subject|hidden-issuer|private-beta/);
   assert.ok(screen.getByRole('button',{name:'Choose photo'}));
   assert.equal(screen.queryByRole('button',{name:'Save photo'}),null);
   assert.deepEqual(calls.map(call=>call.operation),['dervo_account_status']);
  }finally{cleanup();client.clear();}
 }
});

test('your name saves on Return or leaving the field, keeps a newer draft, puts the old one back on Esc, and never claims a save it did not see',async()=>{
 for(const scenario of ['saved','newer-draft','background','blur']){
  let value={...signedEmail},resolveSave;
  const {calls,client}=mount(operation=>{
   if(operation==='dervo_account_status')return value;
   if(operation==='dervo_account_save_profile')return new Promise(resolve=>{resolveSave=()=>{value=scenario==='background'?{...signedEmail,state:'verifying',can_save_profile:false}:{...signedEmail,identity:{...signedEmail.identity,display_name:'Fieldnotes'}};resolve(value);};});
   throw Error('Unexpected operation');
  });
  try{
   const name=await screen.findByRole('textbox',{name:'Your name'});
   await waitFor(()=>assert.equal(name.value,'Bryce'));
   fireEvent.change(name,{target:{value:'Fieldnotes'}});
   assert.equal(calls.filter(call=>call.operation==='dervo_account_save_profile').length,0,'typing alone saves nothing');
   if(scenario==='blur')fireEvent.blur(name);else fireEvent.keyDown(name,{key:'Enter'});
   if(scenario==='newer-draft')fireEvent.change(name,{target:{value:'Next draft'}});
   await act(async()=>resolveSave());
   assert.deepEqual(calls.find(call=>call.operation==='dervo_account_save_profile').args,{displayName:'Fieldnotes'});
   if(scenario==='saved'||scenario==='blur')await screen.findByText('Saved');
   if(scenario==='background'){assert.equal(screen.queryByText('Saved'),null);await screen.findByText('Checking your account');}
   if(scenario==='newer-draft')assert.equal(screen.getByRole('textbox',{name:'Your name'}).value,'Next draft');
  }finally{cleanup();client.clear();}
 }
 const {calls,client}=mount(()=>({...signedEmail}));
 try{
  const name=await screen.findByRole('textbox',{name:'Your name'});
  await waitFor(()=>assert.equal(name.value,'Bryce'));
  fireEvent.change(name,{target:{value:'Oops'}});
  fireEvent.keyDown(name,{key:'Escape'});
  assert.equal(name.value,'Bryce','Esc puts the saved name back');
  fireEvent.change(name,{target:{value:'   '}});fireEvent.keyDown(name,{key:'Enter'});
  await screen.findByText('Your name can’t be empty.');
  assert.equal(calls.filter(call=>call.operation==='dervo_account_save_profile').length,0);
 }finally{cleanup();client.clear();}
});

test('email-code failures stay generic and checking status never retries the submitted code',async()=>{
 const {calls,client}=mount(operation=>{
  if(operation==='dervo_account_status')return {...emailBlank,state:'awaiting_user',can_start:false,can_verify:true,operation_id:'code-failure'};
  if(operation==='dervo_account_verify_code')throw Error('do-not-display-private-code');
  throw Error('Unexpected operation');
 });
 try{
  const code=await screen.findByRole('textbox',{name:'Email code'});
  fireEvent.change(code,{target:{value:'123456'}});fireEvent.submit(code.closest('form'));
  await screen.findByRole('alert');assert.equal(code.value,'');
  assert.doesNotMatch(document.body.textContent,/do-not-display-private-code/);
  fireEvent.click(screen.getByRole('button',{name:'Check status'}));
  await waitFor(()=>assert.equal(screen.queryByRole('alert'),null));
  assert.equal(calls.filter(call=>call.operation==='dervo_account_verify_code').length,1);
 }finally{cleanup();client.clear();}
});

test('beta authorization deadline triggers a read before token expiry and stale access is not labelled approved',async()=>{
 const {accountRefreshInterval}=await import('./DervoAccount.tsx');
 const now=Date.now();
 const value={...signedEmail,expires_at:(now+600000)/1000,access_valid_until:(now+5000)/1000,membership:{...signedEmail.membership,status:'approved',allowed:true}};
 assert.equal(accountRefreshInterval(value,now),5000);
 assert.equal(accountRefreshInterval({...value,access_valid_until:null,membership:{...value.membership,expires_at:new Date(now+3000).toISOString()}},now),3000);
 const {calls,client}=mount(()=>({...value,access_valid_until:(now-1000)/1000}));
 try{
  await screen.findByText('Check your session to confirm your current beta access.');assert.equal(screen.queryByText('Approved'),null);
  fireEvent.click(screen.getByRole('button',{name:'Check again'}));
  await waitFor(()=>assert.equal(calls.length,2));
  assert.ok(calls.every(call=>call.operation==='dervo_account_status'));
 }finally{cleanup();client.clear();}
});

test('code focus waits until a pending send releases the disabled field',async()=>{
 let value={...emailBlank},resolveSend;
 const {client}=mount(operation=>{
  if(operation==='dervo_account_status')return value;
  if(operation==='dervo_account_send_code')return new Promise(resolve=>{resolveSend=resolve;});
  throw Error('Unexpected operation');
 });
 try{
  const email=await screen.findByRole('textbox',{name:'Email'});
  fireEvent.change(email,{target:{value:'fixture@example.test'}});fireEvent.submit(email.closest('form'));
  value={...emailBlank,state:'awaiting_user',can_start:false,can_verify:true,operation_id:'early-status'};
  await act(async()=>client.setQueryData(['dervo-account'],value));
  const code=await screen.findByRole('textbox',{name:'Email code'});assert.equal(code.disabled,true);
  await act(async()=>resolveSend(value));
  await waitFor(()=>assert.equal(document.activeElement,code));assert.equal(code.disabled,false);
 }finally{cleanup();client.clear();}
});

test('background verification polls status without replaying the code',async()=>{
 let reads=0;
 const {calls,client}=mount(operation=>{
  if(operation!=='dervo_account_status')throw Error('Only status reads expected');
  return ++reads===1?{...emailBlank,state:'verifying',can_start:false,can_poll:true,operation_id:'verifying'}:signedEmail;
 });
 try{
  await screen.findByText('Checking your account');
  assert.equal(screen.queryByRole('textbox',{name:'Email code'}),null);
  await screen.findByText('Pending',{}, {timeout:2500});
  assert.ok(calls.length>=2);assert.ok(calls.every(call=>call.operation==='dervo_account_status'));
 }finally{cleanup();client.clear();}
});


test('workspace account notice opens setup explicitly and disappears only with current access',async()=>{
 let value={...blank,method:'email_code',membership:null,access_valid_until:null};
 let opened=0,trigger=null;
 const {calls,client}=mount(()=>value,React.createElement(DervoAccountNotice,{onOpen:element=>{opened++;trigger=element;}}));
 const button=await screen.findByRole('button',{name:'Sign in'});
 assert.equal(opened,0);
 fireEvent.click(button);assert.equal(opened,1);assert.ok(trigger===button);
 assert.ok(calls.every(call=>call.operation==='dervo_account_status'));
 value={...value,state:'signed_in',membership:{status:'approved',allowed:true},access_valid_until:Date.now()/1000+30};
 await act(async()=>{await client.invalidateQueries({queryKey:['dervo-account']});});
 await waitFor(()=>assert.ok(screen.queryByRole('region',{name:'Dervo account access'})===null));
 value={...value,access_valid_until:Date.now()/1000-1};
 await act(async()=>{await client.invalidateQueries({queryKey:['dervo-account']});});
 assert.ok(await screen.findByRole('button',{name:'Open account'}));
 value={...value,membership:{status:'pending',allowed:false}};
 await act(async()=>{await client.invalidateQueries({queryKey:['dervo-account']});});
 assert.ok(await screen.findByText('Your beta access is pending'));
});

test('automatic session checks show progress, then approved access clears the notice',async()=>{
 let value={...emailBlank,state:'verifying',can_start:false,can_poll:true,can_cancel:false};
 const {client,calls}=mount(()=>value,React.createElement(DervoAccountNotice,{onOpen:()=>assert.fail('No action is needed while checking')}));
 try{
  await screen.findByText('Checking your Dervo account');
  assert.ok(screen.queryByText('Your Dervo account needs attention')===null);
  assert.ok(screen.queryByRole('button',{name:'Open account'})===null);
  value={...signedEmail,membership:{...signedEmail.membership,status:'approved',allowed:true},access_valid_until:Date.now()/1000+60};
  await act(async()=>{await client.refetchQueries({queryKey:['dervo-account']});});
  await waitFor(()=>assert.ok(screen.queryByRole('region',{name:'Dervo account access'})===null));
  assert.ok(calls.every(call=>call.operation==='dervo_account_status'));
 }finally{cleanup();client.clear();}
});

test('checking copy never hides failed reads, finished unknown status or pending and revoked membership',async()=>{
 let value={...signedEmail,can_poll:true,membership:{...signedEmail.membership,status:'approved',allowed:false},access_valid_until:Date.now()/1000-1};
 let fail=false;
 const {client}=mount(()=>{if(fail)throw Error('private transport details');return value;},React.createElement(DervoAccountNotice,{onOpen:()=>{}}));
 try{
  await screen.findByText('Checking your Dervo account');
  fail=true;
  await act(async()=>{await client.refetchQueries({queryKey:['dervo-account']});});
  await screen.findByText('Account status is unavailable');
  assert.ok(screen.queryByText('Checking your Dervo account')===null);
  assert.doesNotMatch(document.body.textContent,/private transport details/);
  fail=false;value={...value,state:'unknown',can_poll:false,membership:null};
  await act(async()=>{await client.refetchQueries({queryKey:['dervo-account']});});
  await screen.findByText('Your Dervo account needs attention');
  for(const status of ['pending','revoked']){
   value={...signedEmail,can_poll:true,membership:{...signedEmail.membership,status,allowed:false}};
   await act(async()=>{await client.refetchQueries({queryKey:['dervo-account']});});
   assert.ok(screen.queryByText('Checking your Dervo account')===null);
   assert.ok(screen.getByRole('button',{name:'Open account'}));
  }
  value={...emailBlank,state:'verifying',can_poll:true,can_cancel:true};
  await act(async()=>{await client.refetchQueries({queryKey:['dervo-account']});});
  await screen.findByRole('button',{name:'Continue sign-in'});
 }finally{cleanup();client.clear();}
});

const photoFixture={...blank,state:'signed_in',method:'email_code',identity:{issuer:'https://fixture.test/auth/v1',subject:'user-a',display_name:'Bryce'},can_start:false,can_save_profile:true,can_poll:false,photo:{state:'missing',data_url:null}};
const imageFixture='data:image/png;base64,iVBORw0KGgo=';
function photoCanvas(){
 const canvas=dom.window.HTMLCanvasElement.prototype,oldGet=canvas.getContext,oldData=canvas.toDataURL,oldBitmap=globalThis.createImageBitmap;
 canvas.getContext=()=>({drawImage(){}});canvas.toDataURL=()=>imageFixture;
 globalThis.createImageBitmap=async()=>({width:640,height:480,close(){}});
 return()=>{canvas.getContext=oldGet;canvas.toDataURL=oldData;globalThis.createImageBitmap=oldBitmap;};
}
test('photo selection stays local until Save photo and failed upload keeps the preview',async t=>{
 t.after(photoCanvas());const calls=[];
 render(React.createElement(ProfilePhoto,{data:photoFixture,busy:false,onAction:async(op,args)=>{calls.push({op,args});return true;}}));
 fireEvent.change(screen.getByLabelText('Choose profile photo'),{target:{files:[new dom.window.File(['fixture'],'photo.png',{type:'image/png'})]}});
 const save=await screen.findByRole('button',{name:'Save photo'});
 assert.equal(calls.length,0);
 fireEvent.click(save);
 await waitFor(()=>assert.equal(calls.length,1));
 assert.deepEqual(calls[0],{op:'dervo_account_save_photo',args:{subject:'user-a',png:'iVBORw0KGgo='}});
 // The button reads "Saving…" until the save settles; wait for it rather than racing it (beta.10's build run lost that race).
 await waitFor(()=>assert.ok(screen.getByRole('button',{name:'Save photo'})));
});
test('switching accounts discards an image that finishes decoding late',async t=>{
 t.after(photoCanvas());let finish;
 globalThis.createImageBitmap=()=>new Promise(resolve=>{finish=resolve;});
 const onAction=async()=>assert.fail('No photo should be sent');
 const view=render(React.createElement(ProfilePhoto,{data:photoFixture,busy:false,onAction}));
 fireEvent.change(screen.getByLabelText('Choose profile photo'),{target:{files:[new dom.window.File(['fixture'],'photo.png',{type:'image/png'})]}});
 view.rerender(React.createElement(ProfilePhoto,{data:{...photoFixture,identity:{...photoFixture.identity,subject:'user-b'}},busy:false,onAction}));
 await act(async()=>{finish({width:128,height:128,close(){}});});
 assert.equal(screen.queryByRole('button',{name:'Save photo'}),null);
});
test('private photo display rejects remote URLs and disappears when signed out',()=>{
 assert.equal(photoImage({...photoFixture,photo:{state:'ready',data_url:'https://example.test/personal.png'}}),undefined);
 assert.equal(photoImage({...photoFixture,photo:{state:'ready',data_url:imageFixture}}),imageFixture);
 assert.equal(photoImage({...photoFixture,state:'signed_out',photo:{state:'ready',data_url:imageFixture}}),undefined);
});

test('a failed photo save shows one confirmation error and preserves the selected image',async t=>{
 t.after(photoCanvas());let finish;
 const action=()=>new Promise(resolve=>{finish=resolve;});
 const view=render(React.createElement(ProfilePhoto,{data:photoFixture,busy:false,onAction:action}));
 fireEvent.change(screen.getByLabelText('Choose profile photo'),{target:{files:[new dom.window.File(['fixture'],'photo.png',{type:'image/png'})]}});
 fireEvent.click(await screen.findByRole('button',{name:'Save photo'}));
 await act(async()=>{
  view.rerender(React.createElement(ProfilePhoto,{data:{...photoFixture,photo:{state:'error',data_url:null}},busy:false,onAction:action}));
 });
 await act(async()=>{finish(true);});
 assert.equal(screen.getAllByRole('alert').length,1);
 assert.ok(screen.getByRole('button',{name:'Check photo'}));
 assert.ok(screen.getByRole('button',{name:'Save photo'}));
 assert.equal(document.querySelector('img').getAttribute('src'),imageFixture);
});

test('beta access is one row: its status word once, and the admin line only for admins',async()=>{
 const signedIn={...emailBlank,state:'signed_in',can_start:false,can_sign_out:true,can_save_profile:true,identity:{issuer:'i',subject:'s',display_name:'Synthetic User'},photo:{state:'missing',data_url:null},access_valid_until:Date.now()/1000+600};
 mount(()=>({...signedIn,membership:{status:'approved',allowed:true,expires_at:null,release_channel:'private-beta',is_admin:true}}));
 assert.ok(await screen.findByText('Approved'));
 assert.equal(screen.getAllByText('Approved').length,1,'said once');
 assert.ok(screen.getByText(/with no end date\. You’re an admin, so Testers is in this sidebar\./));
 cleanup();
 mount(()=>({...signedIn,membership:{status:'approved',allowed:true,expires_at:new Date(Date.now()+10*86400000).toISOString(),release_channel:'private-beta',is_admin:false}}));
 assert.ok(await screen.findByText(/Every beta feature is on for this account, until \d+ [A-Z][a-z]{2}\./));
 assert.equal(screen.queryByText(/You’re an admin/),null);
 cleanup();
 mount(()=>({...signedIn,membership:{status:'pending',allowed:false,expires_at:null,release_channel:'private-beta',is_admin:false}}));
 await screen.findByText('Pending');
 assert.equal(screen.queryByText('Approved'),null);
});

test('a photo kept on this Mac shows at once and offers Check photo only after a failed check',()=>{
 const onAction=async()=>assert.fail('Nothing should be sent just by showing the photo');
 const cached={state:'cached',data_url:imageFixture,checked:false};
 assert.equal(photoImage({...photoFixture,photo:cached}),imageFixture);
 // Negative control: an unconfirmed copy with no failed check yet shows the image and no alert.
 const view=render(React.createElement(ProfilePhoto,{data:{...photoFixture,photo:cached},busy:false,onAction}));
 assert.equal(document.querySelector('img').getAttribute('src'),imageFixture);
 assert.equal(screen.queryByRole('button',{name:'Check photo'}),null);
 assert.equal(screen.queryAllByRole('alert').length,0);
 view.rerender(React.createElement(ProfilePhoto,{data:{...photoFixture,photo:{...cached,error:'photo_service'}},busy:false,onAction}));
 assert.equal(document.querySelector('img').getAttribute('src'),imageFixture);
 assert.ok(screen.getByText('Your photo couldn’t be confirmed.'));
 assert.ok(screen.getByRole('button',{name:'Check photo'}));
});

// Paid plans and the free trial (8 Oct 2026).
const signedIn={...blank,state:'signed_in',method:'email_code',can_start:false,can_sign_out:true,can_save_profile:true,identity:{issuer:'fake',subject:'test',display_name:'Synthetic User'},photo:{state:'missing',data_url:null},
 membership:{status:'pending',allowed:false,expires_at:null,release_channel:'private-beta',is_admin:false},access_valid_until:null,billing:{state:'idle',action:null,detail:null}};
const planDoc=(plan,extra={})=>({plan,environment:'sandbox',trial:{days:7,mode:'card',ends_at:null,available:false},subscription:null,can_buy:true,can_manage:false,...extra});

test('a new account starts its free trial from the notice, with no card',async()=>{
 const state={...signedIn,plan:planDoc('none',{trial:{days:7,mode:'app',ends_at:null,available:true}}),can_start_trial:true,can_buy:true};
 const {calls,client}=mount(()=>state,React.createElement(DervoAccountNotice,{onOpen:()=>assert.fail('The trial starts here, not in Settings')}));
 try{
  await screen.findByText('Try Dervo free for 7 days');
  assert.ok(screen.getByText(/No card needed/));
  assert.equal(screen.queryByText(/beta access is pending/),null);
  fireEvent.click(screen.getByRole('button',{name:'Start free trial'}));
  await waitFor(()=>assert.ok(calls.some(c=>c.operation==='dervo_plan_start_trial')));
 }finally{cleanup();client.clear();}
});

test('an ended trial points to plans instead of beta approval',async()=>{
 let opened=0;
 const {client}=mount(()=>({...signedIn,plan:planDoc('none'),can_buy:true}),React.createElement(DervoAccountNotice,{onOpen:()=>{opened++;}}));
 try{
  await screen.findByText('Your free trial has ended');
  assert.equal(screen.queryByText(/beta access is pending/),null);
  fireEvent.click(screen.getByRole('button',{name:'Choose a plan'}));
  assert.equal(opened,1);
 }finally{cleanup();client.clear();}
});

test('Plan in Account shows the trial and opens checkout through the host, never a link',async()=>{
 let state={...signedIn,plan:planDoc('trial',{trial:{days:7,mode:'app',ends_at:2e9,available:false}}),can_buy:true,membership:{...signedIn.membership,status:'approved',allowed:true},access_valid_until:Date.now()/1000+60};
 const {calls,client}=mount(op=>{if(op==='dervo_billing_open')state={...state,billing:{state:'opened',action:'checkout',detail:null}};return state;});
 try{
 await screen.findAllByText('Plan');
 assert.ok(screen.getByText('Test mode: no real charges'));
 assert.equal(screen.queryByText('Beta access'),null);
 assert.ok(screen.getByText(new RegExp(PRICES.year.replace('$','\\$'))));
 fireEvent.click(screen.getByRole('button',{name:'Yearly, $144'}));
 await screen.findByText('Checkout opened in your browser');
 assert.ok(screen.getByText('Checkout opened in your browser').closest('[role=status]'));
 assert.deepEqual(calls.find(c=>c.operation==='dervo_billing_open').args,{action:'checkout',interval:'year'});
 fireEvent.click(screen.getByRole('button',{name:'Check now'}));
 await waitFor(()=>assert.ok(calls.some(c=>c.operation==='dervo_plan_refresh')));
 }finally{cleanup();client.clear();}
});

test('a server without billing keeps the Beta access row',async()=>{
 const {client}=mount(()=>({...signedIn,plan:null,membership:{...signedIn.membership,status:'approved',allowed:true},access_valid_until:Date.now()/1000+300}));
 try{
  await screen.findByText('Beta access');
  assert.equal(screen.queryByText('Plan'),null);
 }finally{cleanup();client.clear();}
});

test('plan summaries say what happens next',()=>{
 assert.deepEqual({...PRICES},{month:'$15',year:'$144'},'the prices Bryce locked on 8 Oct 2026');
 const now=Date.UTC(2026,9,8);
 const sub=extra=>planDoc('paid',{can_buy:false,can_manage:true,subscription:{status:'active',interval:'year',period_ends_at:now/1000+86400*30,cancel_at:null,access_until:now/1000+86400*32,...extra}});
 assert.equal(planSummary(sub({}),now).word,'Active');
 assert.match(planSummary(sub({}),now).line,/Dervo Pro, yearly\. Renews/);
 assert.equal(planSummary(sub({status:'past_due'}),now).word,'Payment failed');
 assert.match(planSummary(sub({cancel_at:now/1000+86400}),now).line,/won’t renew/);
 assert.equal(planSummary(planDoc('removed'),now).state,'failed');
 assert.equal(planSummary(planDoc('beta'),now).word,'Beta');
 assert.equal(planSummary(planDoc('none'),now).word,'Trial ended');
 assert.match(planSummary(planDoc('none',{trial:{days:7,mode:'app',ends_at:null,available:true}}),now).line,/7 days\. No card needed/);
 assert.match(planSummary(planDoc('none',{trial:{days:7,mode:'card',ends_at:null,available:true}}),now).line,/7 days, then \$15 a month or \$144 a year/);
 // A missing date drops its clause instead of leaving a blank.
 for(const doc of [sub({period_ends_at:null}),sub({status:'past_due',access_until:null}),planDoc('trial'),sub({status:'trialing',period_ends_at:null})]){
  assert.doesNotMatch(planSummary(doc,now).line,/ \.|  |after \.|until ,/,JSON.stringify(doc));
 }
 assert.equal(planSummary(sub({status:'paused'}),now).word,'Paused');
 const trialing=planDoc('trial',{subscription:{status:'trialing',interval:'month',period_ends_at:now/1000+86400*5,cancel_at:null,access_until:null}});
 assert.match(planSummary(trialing,now).line,/^Free until .+, then Dervo Pro, monthly\./);
 assert.equal(planSummary(planDoc('none',{subscription:{status:'canceled',interval:'month',period_ends_at:null,cancel_at:null,access_until:null}}),now).word,'Ended');
});

test('the trial-ending notice only shows in the last 3 days of a no-card trial',()=>{
 const now=new Date(2026,9,8).getTime(); // local midnight: days are counted on this Mac's calendar
 const trial=days=>planDoc('trial',{trial:{days:7,mode:'app',ends_at:now/1000+86400*days,available:false}});
 assert.equal(trialEnding(trial(5),now),null);
 assert.equal(trialEnding(trial(2.5),now),2,'counted by date: midday the day after next is 2 days away');
 assert.equal(trialEnding(trial(3.5),now),3);
 assert.equal(trialEnding(trial(0.5),now),0,'later today');
 assert.equal(trialEnding(trial(25/24),now),1,'25 hours ahead is tomorrow, matching the end date Settings shows');
 assert.equal(trialEnding(trial(-1),now),null);
 assert.equal(trialEnding({...trial(1),subscription:{status:'trialing',interval:'month',period_ends_at:null,cancel_at:null,access_until:null}},now),null,'Paddle trials renew on their own');
});

test('with a card trial, the notice sends a new account to plans, priced, instead of starting a trial',async()=>{
 let opened=0;
 const {calls,client}=mount(()=>({...signedIn,plan:planDoc('none',{trial:{days:7,mode:'card',ends_at:null,available:true}}),can_start_trial:false,can_buy:true}),
  React.createElement(DervoAccountNotice,{onOpen:()=>{opened++;}}));
 try{
  await screen.findByText('Try Dervo free for 7 days');
  assert.ok(screen.getByText(/Then \$15 a month or \$144 a year/));
  fireEvent.click(screen.getByRole('button',{name:'Start free trial'}));
  assert.equal(opened,1);
  assert.ok(!calls.some(c=>c.operation==='dervo_plan_start_trial'));
 }finally{cleanup();client.clear();}
});

test('a failed payment shows on Home even while access is still valid',async()=>{
 const state={...signedIn,membership:{...signedIn.membership,status:'approved',allowed:true},access_valid_until:Date.now()/1000+3600,can_manage_billing:true,
  plan:planDoc('paid',{can_buy:false,can_manage:true,subscription:{status:'past_due',interval:'month',period_ends_at:null,cancel_at:null,access_until:Date.now()/1000+3600}})};
 const {client}=mount(()=>state,React.createElement(DervoAccountNotice,{onOpen:()=>{}}));
 try{
  await screen.findByText('Your payment didn’t go through');
  assert.ok(screen.getByRole('button',{name:'Open billing'}));
 }finally{cleanup();client.clear();}
});

test('a beta tester keeps the Beta access details, with no plan to buy',async()=>{
 const {client}=mount(()=>({...signedIn,plan:planDoc('beta',{can_buy:false}),can_buy:false,membership:{...signedIn.membership,status:'approved',allowed:true},access_valid_until:Date.now()/1000+300}));
 try{
  await screen.findByText('Beta access');
  assert.equal(screen.queryByText('Choose a plan'),null);
 }finally{cleanup();client.clear();}
});
