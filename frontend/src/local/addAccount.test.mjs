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
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);window.requestAnimationFrame=globalThis.requestAnimationFrame;
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {AddAccountForm,maskKey}=await import('./AddAccount.tsx');
const {AccountRow}=await import('./AccountRows.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
const KEY='sk-ant-api03-fixture-key-not-real-0000000000000000abcd';
function host(extra=()=>({})){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(command,{operation,args}={})=>{calls.push({operation,args});
  if(operation==='connection_status')return {state:'identity_verified',canSend:false,detail:'API key ready.'};
  return extra(operation,args);}};
 return calls;
}
const mount=element=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},element)));

test('an API key is sent once to profile_key_add with the provider and limit, never to add_profile, and the field is cleared',async()=>{
 const added=[];const calls=host(op=>op==='profile_key_add'?{id:'k1'}:{});
 mount(React.createElement(AddAccountForm,{defaultProvider:'claude',onAdded:async p=>{added.push(p);}}));
 fireEvent.click(screen.getByRole('button',{name:/^Use an API key/}));
 const field=screen.getByLabelText('Anthropic API key');
 assert.equal(field.type,'password');assert.equal(field.getAttribute('autocomplete'),'off');
 assert.ok(screen.getByRole('button',{name:'Check and add key'}).disabled);
 fireEvent.change(field,{target:{value:KEY}});
 fireEvent.click(screen.getByRole('button',{name:'$100'}));
 fireEvent.click(screen.getByRole('button',{name:'Check and add key'}));
 await waitFor(()=>assert.equal(added.length,1));
 assert.deepEqual(calls.filter(c=>c.operation!=='connection_status'),[{operation:'profile_key_add',args:{provider:'claude',key:KEY,monthlyLimitUsd:100}}]);
 assert.deepEqual(added,[{id:'k1',provider:'claude',method:'api_key'}]);
 await waitFor(()=>assert.equal(screen.getByLabelText('Anthropic API key').value,''),'the key leaves the field once the host has it');
});

test('No limit sends no limit, the OpenAI label follows the provider, and a refusal never repeats the key',async()=>{
 const calls=host(op=>{if(op==='profile_key_add')throw new Error('OpenAI rejected that key '+KEY+' and more');return {};});
 mount(React.createElement(AddAccountForm,{defaultProvider:'claude',onAdded:async()=>{}}));
 fireEvent.click(screen.getByRole('button',{name:'Codex'}));
 fireEvent.click(screen.getByRole('button',{name:/^Use an API key/}));
 // Security review, 19 Sep: the limit is shown, never enforced, and a Codex key's file is readable by anything running as you.
 assert.match(document.body.textContent,/Anything running as you can read that file, full-access runs included\. Codex doesn’t tell Dervo what a run costs, so the limit isn’t measured yet, and it doesn’t stop runs\./);
 assert.doesNotMatch(document.body.textContent,/readable by you only/);
 fireEvent.change(screen.getByLabelText('OpenAI API key'),{target:{value:KEY}});
 fireEvent.click(screen.getByRole('button',{name:'No limit'}));
 fireEvent.click(screen.getByRole('button',{name:'Check and add key'}));
 await waitFor(()=>screen.getByRole('alert'));
 assert.deepEqual(calls.filter(c=>c.operation==='profile_key_add').map(c=>c.args),[{provider:'codex',key:KEY}]);
 assert.equal(screen.getByRole('alert').textContent,'Error: OpenAI rejected that key [key] and more');
 assert.equal(maskKey('x '+KEY+' y',KEY),'x [key] y');
});

test('the subscription path signs in first and hands on the account the provider reported',async()=>{
 const added=[];const calls=host(op=>op==='account_signin_start'?{pendingId:'n1',state:'waiting',active:true,needsCode:false,url:null,detail:'',phase:'browser'}
  :op==='connection_login_status'?{state:'succeeded',active:false,needsCode:false,url:null,detail:'',phase:'finishing',identity:{state:'signed_in',canSend:false,detail:'',signedInAs:'me@example.test',plan:'plus'}}
  :op==='account_signin_add'?{id:'n1',state:'added',identity:'me@example.test'}:{});
 mount(React.createElement(AddAccountForm,{defaultProvider:'codex',onAdded:async p=>{added.push(p);}}));
 assert.equal(screen.getByRole('button',{name:/Sign in with ChatGPT/}).getAttribute('aria-pressed'),'true');
 assert.equal(screen.queryByLabelText('Account email'),null,'no email to type');
 fireEvent.click(screen.getByRole('button',{name:'Sign in'}));
 fireEvent.click(await screen.findByRole('button',{name:'Add this account'}));
 await waitFor(()=>assert.equal(added.length,1));
 assert.deepEqual(calls.filter(c=>c.operation==='account_signin_start').map(c=>c.args),[{provider:'codex',category:'personal'}]);
 assert.equal(calls.some(c=>c.operation==='add_profile'),false);
 assert.deepEqual(added,[{id:'n1',provider:'codex',method:'subscription'}]);
});

test('an API-key account row shows how it pays, hides sign-in, and removes only after a confirm',async()=>{
 const calls=host(op=>op==='profile_key_remove'?{id:'k1',state:'removed'}:{});let changed=0;
 mount(React.createElement(AccountRow,{profile:{id:'k1',provider:'claude',identity:'Anthropic API key ending abcd',category:'personal',method:'api_key',monthly_limit_usd:50},onChanged:async()=>{changed++;}}));
 assert.match(screen.getByTestId('account-row').textContent,/API key · pay per use · \$50 monthly limit, doesn’t stop runs/);
 assert.doesNotMatch(screen.getByTestId('account-row').textContent,/up to \$50/,'the limit is shown, not enforced, so the row can’t read as a cap');
 assert.equal(screen.queryByRole('button',{name:/Connect Claude|Sign in again|Manual sign-in steps/}),null);
 assert.ok(screen.getByRole('button',{name:'Check identity'}));
 fireEvent.click(screen.getByRole('button',{name:'Remove'}));
 assert.equal(calls.filter(c=>c.operation==='profile_key_remove').length,0,'the first click only asks');
 fireEvent.click(screen.getByRole('button',{name:'Remove'}));
 await waitFor(()=>assert.equal(changed,1));
 assert.deepEqual(calls.filter(c=>c.operation==='profile_key_remove').map(c=>c.args),[{profileId:'k1'}]);
 cleanup();
 mount(React.createElement(AccountRow,{profile:{id:'s1',provider:'codex',identity:'me@example.test',category:'personal',method:'subscription'},onChanged:async()=>{}}));
 assert.match(screen.getByTestId('account-row').textContent,/Subscription/);
 assert.equal(screen.queryByRole('button',{name:'Remove'}),null);
 assert.ok(screen.getByRole('button',{name:'Connect Codex'}));
});
