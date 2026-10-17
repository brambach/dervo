import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent','MouseEvent','PointerEvent','FocusEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);window.requestAnimationFrame=globalThis.requestAnimationFrame;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
HTMLElement.prototype.scrollIntoView=function(){};
HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,within,act}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ConnectedAccounts,accountLine,nameOf,BILLING}=await import('./ConnectedAccounts.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
const h=React.createElement;

/** Connected accounts (settings/connected-accounts*.html, interactions.md): accounts by name with plan and usage, the menu,
 * adding by sign-in or API key with who bills which, the Claude Code floor, and help between accounts. A fake host. */
const KEY='sk-ant-api03-fixture-key-not-real-000000000000000000abcd';
const profiles=[
 {id:'cs',provider:'claude',identity:'bryce@trydervo.com',category:'personal',method:'subscription',monthly_limit_usd:null,name:'Personal',plan:'max'},
 {id:'cw',provider:'claude',identity:'bryce@acme.com',category:'personal',method:'subscription',monthly_limit_usd:null,name:null},
 {id:'cx',provider:'codex',identity:'bryce@trydervo.com',category:'personal',method:'subscription',monthly_limit_usd:null,name:'Personal'},
 {id:'ok',provider:'codex',identity:'OpenAI API key ending 4242',category:'personal',method:'api_key',monthly_limit_usd:50,name:'OpenAI key'},
];
const usage={at:0,accounts:[
 {profile_id:'cs',provider:'claude',identity:'bryce@trydervo.com',method:'subscription',name:'Personal',monthly_limit_usd:null,runs_today:18,tokens_today:0,cost_today_usd:null,cost_month_usd:null,runs_month:40,resets_at:null,window_mins:10080,percent:36,low:false,basis:'rate_limits',rate_limits:{}},
 {profile_id:'cx',provider:'codex',identity:'bryce@trydervo.com',method:'subscription',name:'Personal',monthly_limit_usd:null,runs_today:7,tokens_today:0,cost_today_usd:null,cost_month_usd:null,runs_month:88,resets_at:null,window_mins:10080,percent:41,low:false,basis:'rate_limits',rate_limits:{planType:'plus'}},
 {profile_id:'ok',provider:'codex',identity:'OpenAI API key ending 4242',method:'api_key',name:'OpenAI key',monthly_limit_usd:50,runs_today:2,tokens_today:0,cost_today_usd:null,cost_month_usd:12.5,runs_month:9,resets_at:null,window_mins:null,percent:25,low:false,basis:'spend_limit',rate_limits:null},
]};
function host(handler={}){
 const calls=[];
 const checks={cs:{state:'identity_verified',canSend:false,detail:''},cw:{state:'unchecked',canSend:false,detail:''},cx:{state:'identity_verified',canSend:false,detail:''},ok:{state:'identity_verified',canSend:false,detail:''}};
 window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
  if(command!=='local_request')return null;
  const {operation,args:a}=args;calls.push({operation,args:a});
  if(handler[operation])return handler[operation](a);
  if(operation==='claude_version')return {state:'ok',version:'2.1.274',floor:'2.1.271',command:null,detail:''};
  if(operation==='connection_status')return checks[a.profileId];
  // The host strips the plan from a check and keeps it in provider_plans; the profiles listing carries it.
  if(operation==='connection_check'){checks[a.profileId]={state:'identity_verified',canSend:false,detail:''};return checks[a.profileId];}
  if(operation==='connection_login_status')return {state:'idle',phase:null,url:null,needsCode:false,detail:'',active:false};
  if(operation==='openrouter_status')return {available:false};
  return {};
 }};
 return calls;
}
function mount(props={},handler){
 const calls=host(handler);
 let changed=0;
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const view=render(h(QueryClientProvider,{client},h(ConnectedAccounts,{profiles,loading:false,failed:false,onRetry(){},onProfilesChanged:async()=>{changed++;},usage,
  status:{codexVersion:'0.156.1',harness:{boundaries:[],actions:[]}},onStatusChanged:async()=>{},projectName:'dervo app',projectProfileId:'cs',onProjectSettings(){},...props})));
 return {calls,view,changed:()=>changed};
}
const row=id=>document.querySelector(`[data-profile="${id}"]`);
const menu=async(id,label)=>{fireEvent.keyDown(within(row(id)).getByRole('button',{name:label}),{key:'Enter'});return screen.findByRole('menu');};
const named=profiles.map(p=>p.id==='cw'?{...p,name:'Acme'}:p);

test('each account shows its name, plan and email or key, and what it has left; a healthy account says nothing more',async()=>{
 mount();
 await screen.findByText('Claude Max plan · bryce@trydervo.com');
 assert.equal(within(row('cs')).getByText('Personal').className,'dv-acct-name');
 assert.ok(within(row('cs')).getByText('64% left'));
 assert.ok(within(row('ok')).getByText('API key ending 4242 · billed per use by OpenAI'));
 assert.ok(within(row('ok')).getByText('$37.50 left'));
 assert.ok(within(row('ok')).getByText('$12.50 of $50 this month'));
 assert.ok(within(row('cx')).getByText('ChatGPT Plus plan · bryce@trydervo.com'));
 assert.equal(nameOf(profiles[1]),'Personal','an unnamed Claude login gets the same default the sidebar and picker use');
 assert.equal(document.body.textContent.includes('verified'),false,'no "verified" paragraph per account');
 assert.ok(await screen.findByText('Claude Code 2.1.274 on this Mac'));
 assert.ok(screen.getByText('Codex 0.156.1'));
 assert.ok(screen.getByText('dervo app runs on Personal'));
 assert.equal(document.querySelectorAll('[data-accent]').length,0,'nothing needs you, so no rust');
});

test('Fable on Team explains premium inclusion and the unknown seat type from the plan the host keeps',async()=>{
 const calls=host();
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 let listed=profiles,reloads=0;
 const page=()=>h(QueryClientProvider,{client},h(ConnectedAccounts,{profiles:listed,loading:false,failed:false,onRetry(){},usage,status:{},onStatusChanged:async()=>{},
  onProfilesChanged:async()=>{reloads++;listed=profiles.map(p=>p.id==='cw'?{...p,plan:'team'}:p);setTimeout(()=>view.rerender(page()),0);}}));
 const view=render(page());
 await screen.findByText('Claude Max plan · bryce@trydervo.com');
 assert.ok(!within(row('cs')).queryByText('Fable is billed to paid credits on this plan.'),'Max plans show nothing');
 assert.ok(!within(row('cw')).queryByText('Fable is billed to paid credits on this plan.'),'no plan known yet, so nothing is claimed');
 // cw was unchecked: the page checks it once, the host records its plan, and the listing is read again.
 await waitFor(()=>assert.ok(within(row('cw')).getByText(/Premium seats include Fable for up to 50% of your weekly usage limits/)));
 assert.ok(within(row('cw')).getByText(/Standard seats use paid credits from the first message/));
 assert.ok(!within(row('cw')).queryByText('Fable is billed to paid credits on this plan.'));
 assert.ok(reloads>=1,'the profiles listing is read again once the check settles');
 assert.ok(within(row('cw')).getByText('Claude Team plan · bryce@acme.com'));
 assert.equal(calls.filter(c=>c.operation==='connection_check').map(c=>c.args.profileId).join(),'cw','only the unchecked account is checked, once');
 assert.equal(accountLine(profiles[1],'pro'),'Claude Pro plan · bryce@acme.com');
 assert.ok(within(row('cx')).getByText('ChatGPT Plus plan · bryce@trydervo.com'),'a Codex plan comes from its usage report');
});

test('Rename saves the name the picker and sidebar show, on Return, and Esc leaves it as it was',async()=>{
 const {calls,changed}=mount({},{profile_rename:a=>({id:a.profileId,name:a.name})});
 await screen.findByText('Claude Max plan · bryce@trydervo.com');
 fireEvent.click(within(await menu('cs','Personal options')).getByRole('menuitem',{name:'Rename'}));
 const field=await screen.findByRole('textbox',{name:'Name for bryce@trydervo.com'});
 fireEvent.change(field,{target:{value:'Acme'}});
 fireEvent.keyDown(field,{key:'Enter'});
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='profile_rename').args,{profileId:'cs',name:'Acme'}));
 await screen.findByText('Saved');
 assert.ok(changed()>=1,'the list reloads, so every screen shows the new name');
 fireEvent.click(within(await menu('cs','Personal options')).getByRole('menuitem',{name:'Rename'}));
 const again=await screen.findByRole('textbox',{name:'Name for bryce@trydervo.com'});
 fireEvent.keyDown(again,{key:'Escape'});
 await waitFor(()=>assert.ok(!screen.queryByRole('textbox',{name:'Name for bryce@trydervo.com'})));
 assert.equal(calls.filter(c=>c.operation==='profile_rename').length,1,'Esc saves nothing');
});

test('a refused name stays in the field with the host’s reason under it',async()=>{
 mount({},{profile_rename:()=>{throw new Error('Another Claude account is already called Personal.');}});
 await screen.findByText('Claude Max plan · bryce@trydervo.com');
 fireEvent.click(within(await menu('cs','Personal options')).getByRole('menuitem',{name:'Rename'}));
 const field=await screen.findByRole('textbox',{name:'Name for bryce@trydervo.com'});
 fireEvent.change(field,{target:{value:'personal'}});fireEvent.keyDown(field,{key:'Enter'});
 assert.match((await screen.findByRole('alert')).textContent,/already called Personal/);
 assert.equal(screen.getByRole('textbox',{name:'Name for bryce@trydervo.com'}).value,'personal');
});

test('adding an account leads with the provider’s sign-in, keeps an API key as the quieter choice, and says who bills which',async()=>{
 mount();
 fireEvent.click(await screen.findByRole('button',{name:'Add a Claude account'}));
 const card=screen.getByRole('group',{name:'Add a Claude account'});
 // What it's for comes first (b16-work), Personal unless you pick Work, with the why under it.
 assert.deepEqual(within(within(card).getByRole('radiogroup',{name:'This account is for'})).getAllByRole('radio').map(r=>[r.textContent,r.getAttribute('aria-checked')]),[['Work','false'],['Personal','true']]);
 assert.match(card.textContent,/your employer’s code never lands on your personal subscription/);
 // Beta.18: no email to type. One button starts the sign-in; the key is a link beside Cancel.
 assert.equal(within(card).queryByRole('textbox'),null,'nothing to type before signing in');
 assert.deepEqual(within(card).getAllByRole('button').map(b=>b.textContent).slice(-3),['Use an API key instead','Cancel','Sign in with Claude']);
 assert.match(card.textContent,new RegExp(BILLING.claude.subscription.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+' Dervo shows which account signed in before adding it\\.'));
 fireEvent.click(within(card).getByRole('button',{name:'Use an API key instead'}));
 assert.ok(within(card).getByText('Billed per use by Anthropic to the key’s account. Your Claude plan’s limits don’t apply.'));
 assert.match(card.textContent,/keeps it in your Mac’s Keychain and never shows it again/);
 assert.ok(within(card).getByRole('button',{name:'Sign in instead'}),'and back to signing in');
 fireEvent.keyDown(card,{key:'Escape'});
 assert.ok(!screen.queryByRole('group',{name:'Add a Claude account'}),'Esc closes the card');
 fireEvent.click(screen.getByRole('button',{name:'Add a Codex account'}));
 const codex=screen.getByRole('group',{name:'Add a Codex account'});
 assert.ok(within(codex).getByRole('button',{name:'Sign in with ChatGPT'}));
 fireEvent.click(within(codex).getByRole('button',{name:'Use an API key instead'}));
 assert.ok(within(codex).getByText('Billed per use by OpenAI to the key’s account. Your ChatGPT plan’s limits don’t apply.'));
 assert.match(codex.textContent,/Codex saves it in a file in its own folder on this Mac\. Anything running as you can read that file, full-access runs included/,'says honestly where a Codex key is kept, and who can read it');
});

/** The host's side of signing in first (account_signin.py): a pending id, then its sign-in status, then what it found. */
function pendingSignIn(found,{added={id:'n1',state:'added',identity:'me@example.test'}}={}){
 let phase='running';
 return {set:next=>{phase=next;},handler:{
  account_signin_start:a=>({pendingId:'n1',state:'waiting',phase:'browser',url:null,needsCode:false,detail:'',active:true,args:a}),
  account_signin_add:()=>added,account_signin_cancel:()=>({state:'cancelled'}),
  connection_login_status:a=>a.profileId!=='n1'?{state:'idle',phase:null,url:null,needsCode:false,detail:'',active:false}
   :phase==='running'?{state:'waiting',phase:'browser',url:'https://claude.ai/oauth',needsCode:false,detail:'',active:true}
   :{state:'succeeded',phase:'finishing',url:null,needsCode:false,active:false,detail:'',identity:found}}};
}

test('a work account is added as Work by signing in, and says which waiting projects it will run',async()=>{
 const flow=pendingSignIn({state:'signed_in',canSend:false,detail:'',signedInAs:'you@acme.test',plan:'team'});
 const {calls}=mount({},{...flow.handler,onboarding_waiting:()=>({names:['billing-api','checkout-web']})});
 fireEvent.click(await screen.findByRole('button',{name:'Add a Claude account'}));
 const card=screen.getByRole('group',{name:'Add a Claude account'});
 fireEvent.click(within(card).getByRole('radio',{name:'Work'}));
 assert.match(card.textContent,/Sign in with your work account\. The sign-in is kept in a folder of its own on this Mac, apart from your personal one\./);
 await within(card).findByText(/billing-api and checkout-web, the Work projects waiting for a work account/);
 fireEvent.click(within(card).getByRole('button',{name:'Sign in with Claude'}));
 await waitFor(()=>assert.ok(calls.find(c=>c.operation==='account_signin_start')));
 assert.deepEqual(calls.find(c=>c.operation==='account_signin_start').args,{provider:'claude',category:'work'});
 assert.equal(calls.some(c=>c.operation==='add_profile'),false,'no account exists before you confirm');
});

test('a work account carries the Work mark and each account says what it runs',async()=>{
 mount({profiles:[...profiles,{id:'cz',provider:'claude',identity:'you@acme.test',category:'work',method:'subscription',monthly_limit_usd:null,name:'Acme',plan:'team'}]},
  {projects:()=>[{id:'p1',name:'tidepool',profile_id:'cs',context:'personal'},{id:'p2',name:'billing-api',profile_id:'cz',context:'work'},{id:'p3',name:'checkout-web',profile_id:'cz',context:'work'}],
   connection_status:()=>({state:'identity_verified',canSend:false,detail:''})});
 await waitFor(()=>assert.ok(within(row('cz')).getByText('Work projects: billing-api and checkout-web')));
 assert.ok(within(row('cz')).getByText('Work').closest('.dv-work-tag'),'the mark is a briefcase and the word');
 assert.ok(within(row('cs')).getByText('Personal projects: tidepool'));
 assert.ok(within(row('cx')).getByText('Personal. Not used by a project yet.'));
 assert.equal(within(row('cs')).queryByText('Work'),null,'Personal stays unmarked');
});

test('an API key is sent to the host once, then the field is emptied and the key appears nowhere on the page',async()=>{
 const {calls}=mount({},{profile_key_add:()=>({id:'k1'})});
 fireEvent.click(await screen.findByRole('button',{name:'Add a Claude account'}));
 fireEvent.click(screen.getByRole('button',{name:'Use an API key instead'}));
 const field=screen.getByLabelText('Anthropic API key');
 assert.equal(field.type,'password');
 fireEvent.change(field,{target:{value:KEY}});
 fireEvent.click(screen.getByRole('radio',{name:'$100'}));
 fireEvent.click(screen.getByRole('button',{name:'Check and add key'}));
 await waitFor(()=>assert.ok(calls.find(c=>c.operation==='profile_key_add')));
 assert.deepEqual(calls.find(c=>c.operation==='profile_key_add').args,{provider:'claude',key:KEY,category:'personal',monthlyLimitUsd:100});
 assert.equal(calls.filter(c=>c.operation==='profile_key_add').length,1);
 await waitFor(()=>assert.ok(!screen.queryByRole('group',{name:'Add a Claude account'})));
 assert.equal(document.body.innerHTML.includes(KEY.slice(8)),false,'the key is gone from the page');
 assert.equal(calls.filter(c=>c.operation!=='profile_key_add').some(c=>JSON.stringify(c).includes(KEY.slice(8))),false,'and went nowhere else');
});

test('a refused key keeps the card open, masks the key in the host’s error and sends nothing else',async()=>{
 mount({},{profile_key_add:a=>{throw new Error(`Anthropic rejected ${a.key}. Nothing was saved.`);}});
 fireEvent.click(await screen.findByRole('button',{name:'Add a Claude account'}));
 fireEvent.click(screen.getByRole('button',{name:'Use an API key instead'}));
 fireEvent.change(screen.getByLabelText('Anthropic API key'),{target:{value:KEY}});
 fireEvent.click(screen.getByRole('button',{name:'Check and add key'}));
 const alert=await screen.findByRole('alert');
 assert.match(alert.textContent,/Anthropic rejected \[key\]\. Nothing was saved\./);
 assert.equal(alert.textContent.includes(KEY.slice(8)),false);
});

test('signing in first shows who signed in, and only Add makes the account',async()=>{
 const flow=pendingSignIn({state:'signed_in',canSend:false,detail:'',signedInAs:'me@example.test',plan:'max'});
 const {calls,changed}=mount({},flow.handler);
 fireEvent.click(await screen.findByRole('button',{name:'Add a Claude account'}));
 fireEvent.click(screen.getByRole('button',{name:'Sign in with Claude'}));
 await screen.findByText('Sign in with the Claude account you want to add');
 assert.deepEqual(calls.find(c=>c.operation==='account_signin_start').args,{provider:'claude',category:'personal'});
 assert.equal(screen.queryByRole('button',{name:'Add this account'}),null,'nothing to add while the sign-in runs');
 flow.set('done');
 const add=await screen.findByRole('button',{name:'Add this account'},{timeout:3000});
 const card=screen.getByRole('group',{name:'Add a Claude account'});
 assert.match(card.textContent,/Signed in as me@example\.test/);
 assert.match(card.textContent,/Claude Max plan · Personal account/);
 assert.ok(within(card).getByRole('button',{name:'Use a different account'}));
 assert.equal(calls.some(c=>c.operation==='account_signin_add'),false);
 fireEvent.click(add);
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='account_signin_add').args,{pendingId:'n1'}));
 await waitFor(()=>assert.ok(changed()>=1,'the list reloads with the new account'));
 await waitFor(()=>assert.ok(!screen.queryByRole('group',{name:'Add a Claude account'})));
 assert.equal(calls.some(c=>c.operation==='account_signin_cancel'),false,'an added account’s folder is kept');
});

test('an account that’s already here is used, not added twice, and the row says so',async()=>{
 const flow=pendingSignIn({state:'existing',canSend:false,detail:'',signedInAs:'bryce@trydervo.com',profileId:'cs',name:'Personal'},{added:{id:'cs',state:'existing',name:'Personal'}});
 const {calls}=mount({},flow.handler);
 fireEvent.click(await screen.findByRole('button',{name:'Add a Claude account'}));
 fireEvent.click(screen.getByRole('button',{name:'Sign in with Claude'}));
 flow.set('done');
 const use=await screen.findByRole('button',{name:'Use Personal'},{timeout:3000});
 assert.match(screen.getByRole('group',{name:'Add a Claude account'}).textContent,/bryce@trydervo\.com is already added.*It’s Personal in Connected accounts\. Nothing new is added\./);
 fireEvent.click(use);
 await waitFor(()=>assert.ok(within(row('cs')).getByText('Already added')));
 assert.equal(calls.filter(c=>c.operation==='account_signin_add').length,1);
});

test('your account on the other side is refused by name, with no Add, and Cancel ends the sign-in',async()=>{
 const flow=pendingSignIn({state:'other_side',canSend:false,detail:'',signedInAs:'bryce@trydervo.com',otherSide:'personal'});
 const {calls}=mount({},flow.handler);
 fireEvent.click(await screen.findByRole('button',{name:'Add a Claude account'}));
 fireEvent.click(screen.getByRole('radio',{name:'Work'}));
 fireEvent.click(screen.getByRole('button',{name:'Sign in with Claude'}));
 flow.set('done');
 await screen.findByText('That’s your personal account',{},{timeout:3000});
 const card=screen.getByRole('group',{name:'Add a Claude account'});
 assert.match(card.textContent,/bryce@trydervo\.com already runs your Personal projects, so it can’t be a work account too\. Nothing was added\./);
 assert.match(card.textContent,/Sign out of claude\.ai in your browser first/);
 assert.equal(within(card).queryByRole('button',{name:'Add this account'}),null);
 fireEvent.click(within(card).getByRole('button',{name:'Cancel'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='account_signin_cancel')?.args,{pendingId:'n1'}));
});

test('the signing-in card takes the code once, clears the field, and never shows it again',async()=>{
 const code='secret-code-4812';
 const {calls}=mount({},{connection_login_status:a=>a.profileId==='cs'?{state:'running',phase:'code',url:'https://claude.ai/oauth',needsCode:true,detail:'',active:true}:{state:'idle',active:false,needsCode:false,url:null,detail:'',phase:null},
  connection_login_code:()=>({state:'running',active:true})});
 const field=await screen.findByRole('textbox',{name:'Sign-in code'});
 assert.ok(screen.getByText('Signing in to Claude'));
 fireEvent.change(field,{target:{value:code}});
 fireEvent.click(screen.getByRole('button',{name:'Submit code'}));
 await waitFor(()=>assert.ok(calls.find(c=>c.operation==='connection_login_code')));
 assert.deepEqual(calls.find(c=>c.operation==='connection_login_code').args,{profileId:'cs',code});
 assert.equal(screen.getByRole('textbox',{name:'Sign-in code'}).value,'');
 assert.equal(document.body.textContent.includes(code),false);
 fireEvent.click(screen.getByRole('button',{name:'Cancel sign-in'}));
 await waitFor(()=>assert.ok(calls.find(c=>c.operation==='connection_login_cancel')));
});

/** The host's sign-in status for cs as it moves: running, then finished while the identity check runs, then its result. */
function signInFor(phases){
 let phase='running';
 const idle={state:'idle',phase:null,url:null,needsCode:false,detail:'',active:false};
 const status=a=>a.profileId!=='cs'?idle:phase==='running'?{state:'waiting',phase:'browser',url:'https://claude.ai/oauth',needsCode:false,detail:'',active:true}
  :{state:'succeeded',phase:'finishing',url:null,needsCode:false,active:false,detail:'Sign-in finished. Checking that the signed-in identity matches this profile.',identity:phases[phase]};
 return {status,set:next=>{phase=next;}};
}

test('step 3 keeps asking after sign-in ends, and the card closes once the account matches',async()=>{
 // Bug, 19 Sep: sign-in finished, then step 3 span forever. The sign-in process ends before its identity check does, and
 // the card stopped asking the host the moment the process ended, so it never saw the result.
 const flow=signInFor({checking:{state:'checking',canSend:false,detail:''},verified:{state:'identity_verified',canSend:false,detail:''}});
 const {calls}=mount({},{connection_login_status:flow.status});
 await screen.findByText('Signing in to Claude');
 flow.set('checking');
 await screen.findByText('Sign-in finished. Checking that the signed-in identity matches this profile.',{},{timeout:3000});
 const asked=calls.filter(c=>c.operation==='connection_login_status'&&c.args.profileId==='cs').length;
 flow.set('verified');
 await waitFor(()=>assert.ok(calls.filter(c=>c.operation==='connection_login_status'&&c.args.profileId==='cs').length>asked,'still asking after the process ended'),{timeout:3000});
 // Compare to null rather than assert.equal(element,null): Node inspects the element to build the failure even with a
 // message, and a rendered element carries React's whole fiber tree, so every failed retry inside waitFor walked it and
 // the run ran out of memory before the card's 900 ms close.
 await waitFor(()=>assert.ok(screen.queryByText('Signing in to Claude')===null,'the card closes once the account matches'),{timeout:4000});
});

test('a check that can’t confirm the account says so, with Try again, instead of spinning',async()=>{
 const flow=signInFor({unknown:{state:'unknown',canSend:false,detail:'Identity check failed. Check identity again.'}});
 mount({},{connection_login_status:flow.status});
 await screen.findByText('Signing in to Claude');
 flow.set('unknown');
 await screen.findByText('Dervo couldn’t confirm which account signed in. No messages were sent.',{},{timeout:3000});
 assert.ok(screen.getByRole('button',{name:'Try again'}));
 assert.ok(screen.getByRole('button',{name:'Close'}));
 assert.ok(screen.queryByRole('button',{name:'Cancel sign-in'})===null,'no Cancel once the check has answered');
});

test('Claude Code below 2.1.271 is the page’s one rust item, with the command and Check again',async()=>{
 let old=true;
 const {calls}=mount({},{claude_version:a=>old&&!a.recheck?{state:'old',version:'2.1.263',floor:'2.1.271',command:'claude update',detail:'Dervo’s runs need a newer one, so no Claude account can run until it’s updated.'}
  :{state:'ok',version:'2.1.274',floor:'2.1.271',command:null,detail:''}});
 await screen.findByText('Update Claude Code to 2.1.271 or later');
 assert.ok(screen.getByText('claude update'));
 assert.ok(screen.getByRole('button',{name:'Copy claude update'}));
 assert.equal(document.querySelectorAll('[data-accent]').length,1,'one rust item');
 assert.equal(within(row('cs')).getByRole('button',{name:'Personal options'}).disabled,false);
 old=false;
 fireEvent.click(screen.getByRole('button',{name:'Check again'}));
 await screen.findByText('Claude Code 2.1.274 on this Mac');
 assert.deepEqual(calls.filter(c=>c.operation==='claude_version').map(c=>c.args),[{recheck:false},{recheck:true}]);
 assert.ok(!screen.queryByText('Update Claude Code to 2.1.271 or later'));
});

test('the page says what a full-access run can read, and that a monthly limit stops nothing',async()=>{
 // Security review, 19 Sep: finding 1 (keys and the Dervo sign-in saved on this Mac are in reach of any full-access run)
 // and finding 2 (the limit is shown against spend and never enforced).
 mount();
 await screen.findByText('Claude Max plan · bryce@trydervo.com');
 const rules=document.querySelector('.dv-rules').textContent;
 assert.match(rules,/A full-access run on any other account runs commands as you, so it can read every key saved on this Mac, and your Dervo sign-in/);
 assert.doesNotMatch(rules,/Passed only|because its commands could read the key/);
 fireEvent.click(within(await menu('ok','OpenAI key options')).getByRole('menuitem',{name:'Change limit'}));
 const limit=await screen.findByRole('group',{name:'Monthly limit for OpenAI key'});
 assert.match(limit.textContent,/Codex doesn’t tell Dervo what a run costs, so the limit isn’t measured yet, and it doesn’t stop runs/);
 assert.doesNotMatch(limit.textContent,/stop at the limit/);
 fireEvent.keyDown(limit,{key:'Escape'});
 fireEvent.click(screen.getByRole('button',{name:'Add a Claude account'}));
 const card=screen.getByRole('group',{name:'Add a Claude account'});
 fireEvent.click(within(card).getByRole('button',{name:'Use an API key instead'}));
 assert.match(card.textContent,/Dervo shows this month’s spend against the limit, from Claude Code’s own cost estimate\. The limit doesn’t stop runs\. For a hard stop, set a spend limit in the Anthropic Console\./);
});

test('every account can be removed, only after an in-place confirm that says what happens to it',async()=>{
 const {calls}=mount({},{account_remove:a=>({id:a.profileId,state:'removed',projects:1,threads:3})});
 await screen.findByText('Claude Max plan · bryce@trydervo.com');
 const items=within(await menu('ok','OpenAI key options')).getAllByRole('menuitem').map(i=>i.textContent);
 assert.deepEqual(items,['Rename','Check key','Change limit','Remove']);
 fireEvent.click(screen.getByRole('menuitem',{name:'Remove'}));
 const confirm=await screen.findByRole('group',{name:'Remove OpenAI key?'});
 assert.match(confirm.textContent,/Codex deletes the key from this Mac/);
 assert.equal(calls.some(c=>c.operation==='account_remove'),false);
 fireEvent.click(within(confirm).getByRole('button',{name:'Remove'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='account_remove').args,{profileId:'ok'}));
 // Beta.18: a subscription account has Remove too, and says it never signs you out anywhere else.
 const sub=within(await menu('cx','Personal options')).getAllByRole('menuitem').map(i=>i.textContent);
 assert.deepEqual(sub,['Rename','Sign in again','Check sign-in','Sign in from Terminal','Remove']);
 fireEvent.click(screen.getByRole('menuitem',{name:'Remove'}));
 const second=await screen.findByRole('group',{name:'Remove Personal?'});
 assert.match(second.textContent,/Dervo forgets it and deletes the sign-in it keeps for it on this Mac\. You stay signed in to chatgpt\.com everywhere else, and your own Claude Code login isn’t touched\. Threads and projects on it wait for you to pick another account\./);
 fireEvent.click(within(second).getByRole('button',{name:'Keep'}));
 assert.equal(calls.filter(c=>c.operation==='account_remove').length,1,'Keep removes nothing');
});

test('a refused removal says why on the row and keeps the account',async()=>{
 mount({},{account_remove:()=>{throw new Error('A task is running on it right now. Nothing was removed.');}});
 await screen.findByText('Claude Max plan · bryce@trydervo.com');
 fireEvent.click(within(await menu('cs','Personal options')).getByRole('menuitem',{name:'Remove'}));
 fireEvent.click(within(await screen.findByRole('group',{name:'Remove Personal?'})).getByRole('button',{name:'Remove'}));
 await waitFor(()=>assert.match(within(row('cs')).getByRole('alert').textContent,/A task is running on it right now\. Nothing was removed\./));
});

test('a sign-in that is a different valid account offers Use it instead, named, and only on the click',async()=>{
 const {calls}=mount({},{connection_status:a=>a.profileId==='cs'?{state:'identity_mismatch',canSend:false,detail:'Claude Code is signed in with a different email, not bryce@trydervo.com.',signedInAs:'me@personal.test',canUseSignedIn:true}
  :{state:'identity_verified',canSend:false,detail:''},account_use_signed_in:a=>({id:a.profileId,identity:'me@personal.test'})});
 const use=await screen.findByRole('button',{name:'Use me@personal.test instead'});
 assert.match(within(row('cs')).getByRole('alert').textContent,/signed in with a different email/);
 assert.equal(calls.some(c=>c.operation==='account_use_signed_in'),false,'nothing changes until you click');
 fireEvent.click(use);
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='account_use_signed_in').args,{profileId:'cs'}));
});

test('an account on this project’s side can become what its new threads use, from its menu',async()=>{
 const project={id:'p1',cwd:'/tmp/p1',profile_id:'cs',model:'claude-opus-5-5',context:'personal'};
 const {calls}=mount({project,projectProfileId:'cs'},{project_profile_set:a=>({id:a.projectId,profile_id:a.profileId})});
 await screen.findByText('Claude Max plan · bryce@trydervo.com');
 const own=within(await menu('cs','Personal options')).getAllByRole('menuitem').map(i=>i.textContent);
 assert.equal(own.includes('Use for new threads in dervo app'),false,'not on the account it already uses');
 fireEvent.keyDown(document.activeElement,{key:'Escape'});
 fireEvent.click(within(await menu('cw','Personal options')).getByRole('menuitem',{name:'Use for new threads in dervo app'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='project_profile_set').args,
  {projectId:'p1',expectedProfileId:'cs',profileId:'cw',expectedModel:'claude-opus-5-5',model:'claude-opus-5-5'}),'same tool, same model');
});

test('linking two accounts is explicit, by account, never by email, and asking helpers waits for a link',async()=>{
 const {calls}=mount({status:{harness:{boundaries:[],actions:[]}},profiles:[...named,{id:'w',provider:'codex',identity:'bryce@trydervo.com',category:'work',method:'subscription',name:null}]},
  {harness_boundary:a=>({id:'x',profiles:a.profileIds}),harness_actions:a=>({profileId:a.profileId,enabled:a.enabled})});
 await screen.findByText('Help between your accounts');
 assert.match(document.body.textContent,/Nothing links by matching email\./);
 const ask=screen.getByRole('switch',{name:'Claude Personal can ask its helpers'});
 assert.equal(ask.disabled,true);
 assert.ok(screen.getAllByText('Link this account first.').length>=1);
 fireEvent.click(screen.getByRole('button',{name:'Link two accounts'}));
 fireEvent.keyDown(screen.getByRole('button',{name:'First account'}),{key:'Enter'});
 const first=await screen.findByRole('menu');
 assert.equal(within(first).queryAllByRole('menuitemradio').some(i=>/work/.test(i.textContent)),false);
 assert.equal(within(first).getAllByRole('menuitemradio').length,4,'personal accounts only; the work one is never offered');
 fireEvent.click(within(first).getByRole('menuitemradio',{name:'Claude Personal'}));
 fireEvent.keyDown(screen.getByRole('button',{name:'Second account'}),{key:'Enter'});
 fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitemradio',{name:'Codex OpenAI key'}));
 fireEvent.click(screen.getByRole('button',{name:'Link'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='harness_boundary').args,{profileIds:['cs','ok']}));
 cleanup();
 const linked=mount({profiles:named,status:{harness:{boundaries:[{id:'x',profiles:['cs','cx'],created_at:1789740000}],actions:[]}}},{harness_actions:a=>({profileId:a.profileId,enabled:a.enabled})});
 await screen.findByText('Claude Personal and Codex Personal');
 fireEvent.click(screen.getByRole('switch',{name:'Claude Personal can ask its helpers'}));
 await waitFor(()=>assert.deepEqual(linked.calls.find(c=>c.operation==='harness_actions').args,{profileId:'cs',enabled:true}));
});

test('the bridge passes exactly the host’s beta.18 account operations (account_signin.py FIELDS)',async()=>{
 const {readFileSync}=await import('node:fs');
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 const backend=readFileSync(new URL('../../../backend/account_signin.py',import.meta.url),'utf8');
 const fields=backend.slice(backend.indexOf('FIELDS = {'),backend.indexOf('}\n',backend.indexOf('FIELDS = {'))+1);
 const names=[...fields.matchAll(/^    '(\w+)':/gm)].map(m=>m[1]);
 assert.deepEqual(names,['account_signin_start','account_signin_add','account_signin_cancel','account_use_signed_in','account_remove']);
 for(const name of names){
  const variant=name.split('_').map(w=>w[0].toUpperCase()+w.slice(1)).join('');
  assert.ok(bridge.includes(`Self::${variant}=>"${name}"`),`${name} is named in the bridge`);
  assert.ok(new RegExp(`pub enum Operation \\{[^}]*\\b${variant}\\b`).test(bridge),`${variant} is an operation`);
 }
});

test('a link with a removed account in it isn’t listed under Help between your accounts',async()=>{
 mount({profiles:named,status:{harness:{boundaries:[{id:'x',profiles:['cs','cx'],created_at:1789740000},{id:'y',profiles:['cs','gone'],created_at:1789740000}],actions:[]}}});
 await screen.findByText('Claude Personal and Codex Personal');
 assert.equal(screen.queryByText(/An account that was removed/),null);
});
