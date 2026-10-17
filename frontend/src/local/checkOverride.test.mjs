import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLSelectElement','Element','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.localStorage=dom.window.localStorage;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,screen,cleanup,within}=await import('@testing-library/react');
const {CheckOverride,checkChoiceLabel}=await import('./CheckOverride.tsx');
const {grantPayload,codingRequest,registerConversations}=await import('./api.ts');
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;localStorage.clear();});after(()=>dom.window.close());
const checks=[{profile:'python-unittest',test_file:'test_app.py',label:'Python unittest'},{profile:'node-test',test_file:'app.test.mjs',label:'Node test'},{profile:'rust-check',test_file:'lib.rs',rust_toolchain:'/tc',label:'Rust type check'}];

test('the check choice appears only for a grant with several checks, defaults to the first and offers the others as radios in the model picker',()=>{
 const changes=[];
 const view=render(React.createElement(CheckOverride,{checks:checks.slice(0,1),value:'default',onChange:v=>changes.push(v)}));
 assert.equal(screen.queryByTestId('check-override'),null);
 view.rerender(React.createElement(CheckOverride,{checks,value:'default',onChange:v=>changes.push(v)}));
 const group=screen.getByRole('radiogroup',{name:'Check for this request'});
 const radios=within(group).getAllByRole('radio');
 assert.deepEqual(radios.map(r=>r.textContent),['Python unittest (default)','Node test','Rust type check']);
 assert.deepEqual(radios.map(r=>r.title),['Python unittest on test_app.py','Node test on app.test.mjs','Rust type check on lib.rs'],'the file each check runs is the tooltip');
 assert.deepEqual(radios.map(r=>r.getAttribute('aria-checked')),['true','false','false']);
 fireEvent.click(radios[1]);
 assert.deepEqual(changes,['node-test']);
 view.rerender(React.createElement(CheckOverride,{checks,value:'node-test',onChange:v=>changes.push(v)}));
 assert.deepEqual(within(group).getAllByRole('radio').map(r=>r.getAttribute('aria-checked')),['false','true','false']);
 assert.equal(checkChoiceLabel(checks,'default'),null);assert.equal(checkChoiceLabel(checks,'node-test'),'Node test','the chip names a check that isn’t the default');
});

test('a request names the chosen check only when it is not the default',async()=>{
 registerConversations([{id:'chat',project_id:'project'}]);
 const calls=[];window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push({operation,args});return {id:'r',taskId:'t',state:'working',detail:'x'};}};
 await codingRequest('chat','Run the node tests',undefined,[],'node-test');
 await codingRequest('chat','Run the default',undefined,[],undefined);
 assert.equal(calls[0].args.checkProfile,'node-test');
 assert.equal('checkProfile' in calls[1].args,false);
});

test('the grant payload carries extra checks with their files and keeps project access on the check files',()=>{
 const payload=grantPayload(['a.py','test_app.py'],'test_app.py',[],'python-unittest','',5,false,[{checkProfile:'node-test',testFile:'app.test.mjs'},{checkProfile:'rust-check',testFile:'lib.rs',rustToolchain:' /tc '},{checkProfile:'',testFile:''}]);
 assert.deepEqual(payload.checks,[{checkProfile:'node-test',testFile:'app.test.mjs'},{checkProfile:'rust-check',testFile:'lib.rs',rustToolchain:'/tc'}]);
 const access=grantPayload([],'test_app.py',[],'python-unittest','',5,true,[{checkProfile:'node-test',testFile:'app.test.mjs'}]);
 assert.deepEqual(access.files,['test_app.py','app.test.mjs']);
 assert.equal('checks' in grantPayload(['a.py'],'',[],'manual-review','',5,false,[{checkProfile:'node-test',testFile:'app.test.mjs'}]),false,'manual review carries no checks');
 assert.equal('checks' in grantPayload(['test_app.py'],'test_app.py',[]),false,'the legacy shape is unchanged');
});
