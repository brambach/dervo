import assert from 'node:assert/strict';
import {test, after, afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://localhost'});
for (const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment']) globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen}=await import('@testing-library/react');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {BetaFeedback,feedbackContext}=await import('./BetaFeedback.tsx');
after(()=>dom.window.close()); afterEach(cleanup);
const context={route:'conversation',projectId:'p1',taskId:'task:1'};
const mount=send=>render(React.createElement(ThemeProvider,null,React.createElement(BetaFeedback,{context,send,getContext:async()=>({contextId:'test-activation',ownerKey:'owner-A',appVersion:'0.5.23'})})));
const receipt=request=>({requestId:request.requestId,state:'sent',receipt:{id:request.requestId,created_at:'2026-09-16T00:00:00Z'},detail:null});

test('context capture excludes titles, errors, transcripts and DOM',()=>{
 const captured=feedbackContext({...context,title:'private',error:'credential',prompt:'private',appVersion:'1.2.3',provider:'codex'});
 assert.deepEqual(Object.keys(captured).sort(),['appVersion','projectId','provider','route','taskId','viewport']);
 assert.equal(JSON.stringify(captured).includes('private'),false);
});

test('autofocus, category, shortcut, receipt and delayed close',async()=>{
 const requests=[];mount(async request=>{requests.push(request);return receipt(request);});
 fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 const field=screen.getByRole('textbox',{name:'Your feedback'});
 await waitFor(()=>assert.ok(document.activeElement===field));
 fireEvent.click(screen.getByRole('radio',{name:'Idea'}));
 fireEvent.change(field,{target:{value:'Make the preview easier to find.'}});
 fireEvent.keyDown(field,{key:'Enter',metaKey:true});
 assert.match((await screen.findByRole('status')).textContent,/Thanks, it’s sent\./);
 assert.equal(requests.length,1);assert.equal(requests[0].category,'idea');
 assert.equal(requests[0].context.projectId,'p1');
 await new Promise(resolve=>setTimeout(resolve,1500));
 assert.ok(screen.queryByRole('dialog'),'the sent note stays for two seconds');
 await waitFor(()=>assert.ok(screen.queryByRole('dialog')===null),{timeout:1500});
 await waitFor(()=>assert.ok(document.activeElement===screen.getByRole('button',{name:'Feedback'})));
});

test('failed retry preserves draft, category and request ID, and hides raw errors',async()=>{
 const requests=[];let fail=true;mount(async request=>{requests.push(request);if(fail)throw new Error('secret');return receipt(request);});
 fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 fireEvent.click(screen.getByRole('radio',{name:'Confusing'}));
 const field=screen.getByRole('textbox');fireEvent.change(field,{target:{value:'Where is the preview?'}});
 await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));await screen.findByRole('alert');
 assert.equal(field.value,'Where is the preview?');assert.equal(screen.getByRole('radio',{name:'Confusing'}).getAttribute('aria-checked'),'true');
 assert.equal(screen.getByRole('alert').textContent.includes('secret'),false);
 fail=false;fireEvent.keyDown(field,{key:'Enter',ctrlKey:true});await screen.findByRole('status');
 assert.equal(requests[0].requestId,requests[1].requestId);
});

test('escape returns focus and reopening retains unsent draft',async()=>{
 mount(async request=>receipt(request));fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 const field=screen.getByRole('textbox');fireEvent.change(field,{target:{value:'Keep this draft'}});
 fireEvent.keyDown(field,{key:'Escape'});
 await waitFor(()=>assert.ok(screen.queryByRole('dialog')===null));
 assert.ok(document.activeElement===screen.getByRole('button',{name:'Feedback'}));
 fireEvent.click(screen.getByRole('button',{name:'Feedback'}));assert.equal(screen.getByRole('textbox').value,'Keep this draft');await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));
});

test('transport success without matching receipt keeps draft',async()=>{
 mount(async()=>({state:'sent',receipt:null}));fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 fireEvent.change(screen.getByRole('textbox'),{target:{value:'A report'}});await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));
 await screen.findByRole('alert');assert.equal(screen.getByRole('textbox').value,'A report');assert.ok(screen.queryByRole('status')===null);
});


test('submit waits for account activation and uses that activation with runtime version',async()=>{
 let resolveContext;const requests=[];
 const getContext=()=>new Promise(resolve=>{resolveContext=resolve;});
 render(React.createElement(ThemeProvider,null,React.createElement(BetaFeedback,{context,getContext,send:async request=>{requests.push(request);return receipt(request);}})));
 fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 fireEvent.change(screen.getByRole('textbox'),{target:{value:'A report'}});
 assert.ok(screen.getByRole('button',{name:'Send feedback'}).disabled);
 resolveContext({contextId:'activation-A',ownerKey:'owner-A',appVersion:'0.5.24'});
 await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));
 fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));await screen.findByRole('status');
 assert.equal(requests[0].contextId,'activation-A');assert.equal(requests[0].context.appVersion,'0.5.24');
});


test('reopen refreshes same-owner activation but preserves uncertain UUID and original context',async()=>{
 let activation='old';const requests=[];
 const getContext=async()=>({contextId:activation,ownerKey:'owner-A',appVersion:activation});
 const send=async request=>{requests.push(request);if(requests.length===1)throw new Error('uncertain');return receipt(request);};
 render(React.createElement(ThemeProvider,null,React.createElement(BetaFeedback,{context,getContext,send})));
 fireEvent.click(screen.getByRole('button',{name:'Feedback'}));fireEvent.change(screen.getByRole('textbox'),{target:{value:'Keep exact report'}});
 await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));await screen.findByRole('alert');
 fireEvent.keyDown(screen.getByRole('textbox'),{key:'Escape'});await waitFor(()=>assert.ok(screen.queryByRole('dialog')===null));
 activation='fresh';fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));await screen.findByRole('status');
 assert.equal(requests[1].contextId,'fresh');assert.equal(requests[1].requestId,requests[0].requestId);assert.deepEqual(requests[1].context,requests[0].context);
});

test('reopened form cannot transfer an old report to a different account',async()=>{
 let ownerKey='owner-A';const requests=[];
 const getContext=async()=>({contextId:ownerKey,ownerKey});
 const send=async request=>{requests.push(request);throw new Error('uncertain');};
 render(React.createElement(ThemeProvider,null,React.createElement(BetaFeedback,{context,getContext,send})));
 fireEvent.click(screen.getByRole('button',{name:'Feedback'}));fireEvent.change(screen.getByRole('textbox'),{target:{value:'A report from A'}});
 await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));await screen.findByRole('alert');
 fireEvent.keyDown(screen.getByRole('textbox'),{key:'Escape'});await waitFor(()=>assert.ok(screen.queryByRole('dialog')===null));
 ownerKey='owner-B';fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));
 assert.equal(requests.length,1);assert.equal(screen.getByRole('textbox').value,'A report from A');
});

test('the account menu opens feedback without a sidebar launcher, once per request',async()=>{
 const getContext=async()=>({contextId:'menu-activation',ownerKey:'owner-A',appVersion:'0.5.23'});
 const element=requestOpen=>React.createElement(ThemeProvider,null,React.createElement(BetaFeedback,{context,getContext,send:async request=>receipt(request),launcher:false,requestOpen}));
 const view=render(element(0));
 assert.equal(screen.queryByRole('button',{name:'Feedback'}),null);
 assert.equal(screen.queryByRole('dialog'),null);
 view.rerender(element(1));
 await screen.findByRole('dialog');
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 await waitFor(()=>assert.equal(screen.queryByRole('dialog'),null));
 // Re-rendering with the same request number doesn't reopen a closed dialog.
 view.rerender(element(1));
 assert.equal(screen.queryByRole('dialog'),null);
 view.rerender(element(2));
 await screen.findByRole('dialog');
});

test('each kind of feedback has its own prompt, and one line says exactly what is sent with it',async()=>{
 render(React.createElement(ThemeProvider,null,React.createElement(BetaFeedback,{context,view:'Home in dervo app',send:async request=>receipt(request),getContext:async()=>({contextId:'test-activation',ownerKey:'owner-A',appVersion:'0.1.0-beta.8'})})));
 fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 const field=screen.getByRole('textbox',{name:'Your feedback'});
 assert.match(field.placeholder,/what you like/);
 fireEvent.click(screen.getByRole('radio',{name:'Bug'}));
 assert.match(field.placeholder,/what happened instead/);
 fireEvent.click(screen.getByRole('radio',{name:'Confusing'}));
 assert.match(field.placeholder,/stop making sense/);
 fireEvent.keyDown(screen.getByRole('radio',{name:'Confusing'}),{key:'ArrowRight'});
 assert.match(field.placeholder,/more useful/,'arrows move along Bug, Confusing, Idea');
 const included=(await screen.findByText('Sent with it: Home in dervo app, your account, Dervo 0.1.0-beta.8')).closest('details');
 assert.equal(included.open,false);
 assert.match(included.textContent,/and the project ID, and the thread or task ID\. Threads, messages and files aren’t attached\. Leave out secrets\./);
 assert.ok(screen.getByText('⌘ Return to send'));
 assert.equal(screen.queryByText(/3,500|of 4,000/),null,'no count until the limit is close');
 fireEvent.change(field,{target:{value:'x'.repeat(3600)}});
 assert.ok(screen.getByText('3,600 of 4,000 characters'));
});

test('⌘⇧B and /bug open it on Bug for a tester; a plain request keeps the kind you left',async()=>{
 const h=React.createElement;
 const props={context,send:async request=>receipt(request),getContext:async()=>({contextId:'test-activation',ownerKey:'owner-A',appVersion:'0.5.23'}),launcher:false};
 const view=render(h(ThemeProvider,null,h(BetaFeedback,{...props,requestOpen:1,initialCategory:'bug'})));
 await screen.findByRole('dialog');
 assert.equal(screen.getByRole('radio',{name:'Bug'}).getAttribute('aria-checked'),'true');
 assert.match(screen.getByRole('textbox',{name:'Your feedback'}).placeholder,/what did you expect/);
 fireEvent.click(screen.getByRole('radio',{name:'Idea'}));
 fireEvent.keyDown(screen.getByRole('textbox'),{key:'Escape'});
 await waitFor(()=>assert.ok(screen.queryByRole('dialog')===null));
 // Negative control: Send feedback from the menu or ⌘K names no kind, so it opens as you left it.
 view.rerender(h(ThemeProvider,null,h(BetaFeedback,{...props,requestOpen:2})));
 await screen.findByRole('dialog');
 assert.equal(screen.getByRole('radio',{name:'Idea'}).getAttribute('aria-checked'),'true');
 view.rerender(h(ThemeProvider,null,h(BetaFeedback,{...props,requestOpen:3,initialCategory:'bug'})));
 await waitFor(()=>assert.equal(screen.getByRole('radio',{name:'Bug'}).getAttribute('aria-checked'),'true'));
});

test('the hourly limit says so in the host’s words; any other failure keeps the fixed line',async()=>{
 const {feedbackProblem}=await import('./BetaFeedback.tsx');
 const limit='You’ve sent a lot of feedback this hour. Your draft is kept, so try again later.';
 let answer=request=>({requestId:request.requestId,state:'error',receipt:null,detail:limit});
 const requests=[];mount(async request=>{requests.push(request);return answer(request);});
 fireEvent.click(screen.getByRole('button',{name:'Feedback'}));
 const field=screen.getByRole('textbox');fireEvent.change(field,{target:{value:'One more thing'}});
 await waitFor(()=>assert.ok(!screen.getByRole('button',{name:'Send feedback'}).disabled));
 fireEvent.keyDown(field,{key:'Enter',metaKey:true});
 assert.equal((await screen.findByRole('alert')).textContent,limit);
 assert.equal(field.value,'One more thing','the draft is kept');
 // Negative controls: an error result with no line, and a thrown error's own words, show the fixed sentence.
 answer=request=>({requestId:request.requestId,state:'error',receipt:null,detail:'  '});
 fireEvent.keyDown(field,{key:'Enter',metaKey:true});
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/^Feedback wasn’t confirmed\./));
 assert.equal(requests.at(-1).requestId,requests[0].requestId,'a retry is the same request');
 assert.match(feedbackProblem(new Error('secret path')),/^Feedback wasn’t confirmed\./);
 assert.match(feedbackProblem(null),/^Feedback wasn’t confirmed\./);
 assert.equal(feedbackProblem(Object.assign(new Error('x'),{detail:limit})),limit,'the api’s thrown refusal carries the line');
});

test('sendBetaFeedback passes the host’s line on an error result and nothing on a missing receipt',async()=>{
 const {sendBetaFeedback}=await import('./api.ts');
 const limit='You’ve sent a lot of feedback this hour. Your draft is kept, so try again later.';
 const request={contextId:'c',requestId:'r1',category:'bug',message:'m',context:{route:'workspace'}};
 window.__TAURI_INTERNALS__={invoke:async()=>({requestId:'r1',state:'error',receipt:null,detail:limit})};
 await assert.rejects(sendBetaFeedback(request),error=>error.detail===limit&&/^Feedback wasn’t confirmed/.test(error.message));
 window.__TAURI_INTERNALS__={invoke:async()=>({requestId:'r1',state:'sent',receipt:{id:'other',created_at:'x'},detail:'not shown'})};
 await assert.rejects(sendBetaFeedback(request),error=>error.detail===null);
 delete window.__TAURI_INTERNALS__;
});
