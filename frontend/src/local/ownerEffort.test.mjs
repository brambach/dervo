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
const {render,fireEvent,waitFor,cleanup,screen,act,within}=await import('@testing-library/react');
// The per-request review is a radio group in the model picker: these read and set it the way a user would.
const reviewRadios=()=>within(screen.getByRole('radiogroup',{name:'Independent review for this request'})).getAllByRole('radio');
const reviewValue=()=>({Off:'off',On:'on'})[reviewRadios().find(r=>r.getAttribute('aria-checked')==='true').textContent]??'default';
const chooseReview=v=>fireEvent.click(reviewRadios()[{default:0,off:1,on:2}[v]]);
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {CodingPermissionDialog,outgoingEffort}=await import('./CodingPermission.tsx');
const {ReviewOverride,useReviewOverride}=await import('./CollaborationUI.tsx');
const {reviewOverridePayload}=await import('./collaboration.ts');
const {codingRequest,registerConversations,CLAUDE_EFFORTS}=await import('./api.ts');
after(()=>dom.window.close());
// Every test unmounts what it rendered, even when an assertion fails, so no DOM leaks into the next test.
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const wrap=(element,qc)=>React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:qc},element));
const mount=(element,qc=client())=>render(wrap(element,qc));
const granted=(provider,effort=null,detail='')=>({state:'granted',detail:'Coding here may read or change 2 granted file(s).',files:['greeting.py','test_sample.py'],test_file:'test_sample.py',provider,candidates:['greeting.py','test_sample.py'],test_candidates:['test_sample.py'],created_at:1,actor:'local-user',owner_effort:effort,owner_effort_detail:detail});
const binding=(provider)=>({provider,identity:'me@example.test',model:provider==='claude'?'claude-fable-5-1':'synthetic-model',cwd:'/approved',visibility:'private'});
const dialog=(provider,open=true)=>React.createElement(CodingPermissionDialog,{open,onOpenChange(){},projectId:'p',channelId:'c',binding:binding(provider),refresh:async()=>{}});
// Actual readiness: the control is enabled only once the host seed has been applied, and the seeded value is visible.
const ready=async(value)=>{const control=await screen.findByLabelText('Owner effort');await waitFor(()=>{assert.equal(control.disabled,false,'enabled once the host seed arrived');assert.equal(control.value,value,'the seeded host value');});return control;};
const setOps=calls=>calls.filter(c=>c.operation==='coding_effort_set').map(c=>c.args.effort);

test('Claude owner effort: seeded xhigh from the host, the documented levels plus provider default, a change saved exactly, nothing executed',async()=>{
 let saved='xhigh';
 const calls=host((op,args)=>{if(op==='coding_permission')return granted('claude',saved);if(op==='coding_effort_set'){saved=args.effort;return granted('claude',saved);}throw new Error(op);});
 mount(dialog('claude'));
 const select=await screen.findByLabelText('Owner effort');
 assert.equal(select.disabled,true,'not editable before the host seed arrives');
 await ready('xhigh');
 assert.deepEqual([...select.options].map(o=>o.value),['',...CLAUDE_EFFORTS],'provider default plus the documented --effort levels, nothing invented');
 fireEvent.change(select,{target:{value:'max'}});
 fireEvent.click(screen.getByRole('button',{name:'Save effort'}));
 await waitFor(()=>assert.equal(saved,'max',`alert: ${screen.queryByRole('alert')?.textContent} calls: ${JSON.stringify(calls.map(c=>c.operation))}`));
 assert.deepEqual(calls.filter(c=>c.operation==='coding_effort_set').map(c=>c.args),[{projectId:'p',channelId:'c',effort:'max'}]);
 await waitFor(()=>assert.equal(select.value,'max','the reload shows the host value after the save'));
 assert.ok(!calls.some(c=>c.operation==='coding_request'||c.operation==='task_create'),'configuration never starts coding');
 const copy=screen.getByTestId('owner-effort-detail').textContent;
 assert.match(copy,/exact --effort token/);assert.match(copy,/not checked before content is sent/);
 assert.doesNotMatch(copy,/refuses an unsupported one before sending content/,'no preflight claim for Claude Code');
});

test('Claude legacy unset shows the provider default and saving without a change sends null',async()=>{
 const calls=host((op,args)=>{if(op==='coding_permission')return granted('claude',null,'No owner effort is set for claude-fable-5-1. The provider default applies.');if(op==='coding_effort_set')return granted('claude',args.effort);throw new Error(op);});
 mount(dialog('claude'));
 await ready('');
 fireEvent.click(screen.getByRole('button',{name:'Save effort'}));
 await waitFor(()=>assert.deepEqual(setOps(calls),[null]));
});

test('Codex owner effort: seeded high as free text, blank saves the provider default, a nonblank token goes untrimmed to the strict host rejection and the typed value survives',async()=>{
 let saved='high';
 const calls=host((op,args)=>{
  if(op==='coding_permission')return granted('codex',saved,saved?'':'No owner effort is set for synthetic-model. The provider default applies.');
  if(op==='coding_effort_set'){if(typeof args.effort==='string'&&args.effort!==args.effort.trim())throw new Error('A Codex owner effort is one token of up to 64 characters, exactly as the model catalog advertises it');saved=args.effort;return granted('codex',saved);}
  throw new Error(op);});
 mount(dialog('codex'));
 const input=await ready('high');
 assert.equal(input.tagName,'INPUT','Codex efforts come from the model catalog at launch, so the control is free text, not an invented list');
 fireEvent.change(input,{target:{value:' high '}});
 fireEvent.click(screen.getByRole('button',{name:'Save effort'}));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/exactly as the model catalog advertises/));
 assert.deepEqual(setOps(calls),[' high '],'sent exactly as typed, never trimmed into a token the user did not enter');
 assert.equal(input.value,' high ','the rejected input stays for correction; the reload does not overwrite it');
 assert.equal(saved,'high','nothing was saved');
 fireEvent.change(input,{target:{value:'  '}});
 fireEvent.click(screen.getByRole('button',{name:'Save effort'}));
 await waitFor(()=>assert.equal(saved,null));
 assert.deepEqual(setOps(calls),[' high ',null],'blank means unset, never an invented default token');
 await waitFor(()=>assert.ok(screen.queryByText(/No owner effort is set for synthetic-model/),`calls: ${JSON.stringify(calls.map(c=>c.operation))} alert: ${screen.queryByRole('alert')?.textContent}`));
});

test('outgoing effort: empty and whitespace-only are unset; anything nonblank is sent byte for byte',()=>{
 assert.equal(outgoingEffort(''),null);assert.equal(outgoingEffort('   '),null);assert.equal(outgoingEffort('\n'),null);
 assert.equal(outgoingEffort('high'),'high');assert.equal(outgoingEffort(' high '),' high ');assert.equal(outgoingEffort('xhigh\n'),'xhigh\n');
});

test('deferred host load: the control is disabled until the seed lands, then a user edit is not overwritten by a background reload, and the edit is what gets saved',async()=>{
 let release;let saved='xhigh';
 const calls=host((op,args)=>{
  if(op==='coding_permission')return release?granted('claude',saved):new Promise(resolve=>{release=()=>resolve(granted('claude',saved));});
  if(op==='coding_effort_set'){saved=args.effort;return granted('claude',saved);}
  throw new Error(op);});
 const qc=client();
 mount(dialog('claude'),qc);
 const select=await screen.findByLabelText('Owner effort');
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='coding_permission').length,1));
 assert.equal(select.disabled,true);
 assert.equal(screen.getByRole('button',{name:'Save effort'}).disabled,true,'nothing can be saved before the host record is known');
 assert.match(screen.getByTestId('owner-effort-detail').textContent,/Loading the saved owner effort/);
 await act(async()=>{release();});
 await ready('xhigh');
 fireEvent.change(select,{target:{value:'high'}});
 await act(async()=>{await qc.invalidateQueries({queryKey:['coding-permission']});});
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='coding_permission').length,2,'the reload happened'));
 assert.equal(select.value,'high','the reload with the older host value xhigh did not overwrite the edit');
 fireEvent.click(screen.getByRole('button',{name:'Save effort'}));
 await waitFor(()=>assert.equal(saved,'high'));
 assert.deepEqual(setOps(calls),['high']);
});

test('reopened dialog: an unsaved edit is dropped on close and the reopened dialog shows the host value again, only once ready',async()=>{
 let saved='medium';
 host((op,args)=>{if(op==='coding_permission')return granted('claude',saved);if(op==='coding_effort_set'){saved=args.effort;return granted('claude',saved);}throw new Error(op);});
 const qc=client();
 const view=render(wrap(dialog('claude',true),qc));
 const select=await ready('medium');
 fireEvent.change(select,{target:{value:'low'}});
 assert.equal(select.value,'low');
 view.rerender(wrap(dialog('claude',false),qc));
 await waitFor(()=>assert.equal(screen.queryByLabelText('Owner effort'),null,'closed'));
 view.rerender(wrap(dialog('claude',true),qc));
 const again=await ready('medium');
 assert.equal(again.value,'medium','the unsaved edit did not survive the close; the host value did');
 fireEvent.change(again,{target:{value:'xhigh'}});
 fireEvent.click(screen.getByRole('button',{name:'Save effort'}));
 await waitFor(()=>assert.equal(saved,'xhigh'));
 view.rerender(wrap(dialog('claude',false),qc));
 view.rerender(wrap(dialog('claude',true),qc));
 await ready('xhigh');
});

const reviewOn={enabled:true,reviewer:'reviewer',effort:'low',provider:'codex',model:'m',notice:'Independent review runs a second agent under its own account. That is extra usage on that account.'};
const reviewOff={enabled:false,reviewer:null,effort:null,provider:null,model:null,notice:reviewOn.notice};

test('the per-request Independent review override offers project default, off and on with the configured reviewer, states the extra usage, and explains what On needs when no reviewer is configured',async()=>{
 const calls=host(op=>{throw new Error(op);});
 const changes=[];
 mount(React.createElement(ReviewOverride,{review:reviewOn,value:'default',onChange:v=>changes.push(v)}));
 assert.deepEqual(reviewRadios().map(r=>r.textContent),['Project default (on)','Off','On']);
 assert.match(reviewRadios()[2].title,/@reviewer/,'On names the configured reviewer');
 assert.ok(!reviewRadios()[2].disabled);
 assert.equal(screen.queryByRole('note'),null,'no configuration hint when a reviewer is configured');
 chooseReview('on');
 assert.deepEqual(changes,['on']);
 cleanup();
 mount(React.createElement(ReviewOverride,{review:reviewOn,value:'on',onChange(){}}));
 assert.match(screen.getByRole('status').textContent,/extra usage/);
 assert.match(screen.getByRole('status').textContent,/Codex · m · effort low/);
 cleanup();
 mount(React.createElement(ReviewOverride,{review:reviewOff,value:'default',onChange(){}}));
 assert.ok(reviewRadios()[2].disabled,'on is unavailable without a configured reviewer');
 assert.match(reviewRadios()[0].textContent,/Project default \(off\)/);
 fireEvent.click(screen.getByText('Set up a reviewer'));
 assert.match(screen.getByRole('note').textContent,/Choose a reviewer in Project settings, then Agents and review/);
 assert.match(screen.getByRole('note').textContent,/Agents and review/);
 cleanup();
 mount(React.createElement(ReviewOverride,{review:reviewOff,value:'on',onChange(){}}));
 assert.match(screen.getByRole('alert').textContent,/No reviewer is configured/);
 assert.equal(calls.length,0,'rendering the override sends nothing');
});

test('override payloads: default sends no review key, off sends the explicit off record, on sends the configured binding, on without a reviewer is an explicit error',()=>{
 assert.equal(reviewOverridePayload('default',reviewOn),undefined);
 assert.deepEqual(reviewOverridePayload('off',reviewOn),{enabled:false,reviewer:null,effort:null});
 assert.deepEqual(reviewOverridePayload('on',reviewOn),{enabled:true,reviewer:'reviewer',effort:'low'});
 assert.throws(()=>reviewOverridePayload('on',reviewOff),/No reviewer is configured/);
 assert.throws(()=>reviewOverridePayload('on',undefined),/No reviewer is configured/);
 assert.equal(reviewOverridePayload('default',undefined),undefined);
});

test('a failed send keeps the override with the draft for the retry; success clears it; a plain request keeps the older shape',async()=>{
 localStorage.clear();registerConversations([{id:'chat',project_id:'project'},{id:'other',project_id:'project'}]);
 const answers=[new Error('Lost acknowledgement'),{id:'r1',taskId:'t1',state:'working',detail:'Working'},{id:'r2',taskId:'t2',state:'working',detail:'Working'}];
 const calls=host(op=>{if(op==='coding_request'){const next=answers.shift();if(next instanceof Error)throw next;return next;}throw new Error(op);});
 const override=reviewOverridePayload('off',reviewOn);
 await assert.rejects(codingRequest('chat','Fix the greeting',override),/Lost acknowledgement/);
 const pending=JSON.parse(localStorage.getItem('dervo-coding-pending:chat'));
 assert.deepEqual(pending.review,override,'the override is part of the unresolved draft');
 assert.equal(localStorage.getItem('dervo-coding-pending:other'),null,'nothing spills to another room');
 await codingRequest('chat','Fix the greeting',override);
 const sent=calls.filter(c=>c.operation==='coding_request');
 assert.equal(sent[0].args.clientId,sent[1].args.clientId);
 assert.deepEqual(sent[1].args.review,override,'the retry carries the same override');
 assert.equal(localStorage.getItem('dervo-coding-pending:chat'),null);
 await codingRequest('chat','Next request',reviewOverridePayload('default',reviewOn));
 assert.ok(!('review' in calls.filter(c=>c.operation==='coding_request')[2].args),'project default keeps the older request shape');
});

// The connected composer lifecycle: the production hook (useReviewOverride), the production control (ReviewOverride), the
// production payload and request path (reviewOverridePayload, codingRequest), wired the way ConnectedApp wires them. The draft
// box here stands in for MessageComposer, which is keyed per conversation in ConnectedApp; that component itself is not mounted.
function Composer({channelId,review}){
 const override=useReviewOverride(channelId);
 const [drafts,setDrafts]=React.useState({});
 const [error,setError]=React.useState('');
 const [reply,setReply]=React.useState('reply-target');
 async function send(){
  const from=channelId;const text=drafts[from]??'';setError('');
  try{await codingRequest(from,text,reviewOverridePayload(override.value,review));if(override.settle(from)){setReply('');setDrafts(d=>({...d,[from]:''}));}}
  catch(e){setError(String(e));}
 }
 return React.createElement(React.Fragment,null,
  React.createElement(ReviewOverride,{review,value:override.value,onChange:override.setValue}),
  React.createElement('textarea',{key:channelId,'aria-label':'Draft',value:drafts[channelId]??'',onChange:e=>setDrafts(d=>({...d,[channelId]:e.target.value}))}),
  React.createElement('button',{onClick:()=>void send()},'Send'),
  React.createElement('p',{'data-testid':'reply'},reply),
  error?React.createElement('p',{role:'alert'},error):null);
}

test('composer lifecycle: channel switch resets the choice, an old in-flight completion does not wipe the new channel, reopen starts at the project default, and off/on/default each send the right payload',async()=>{
 localStorage.clear();registerConversations([{id:'a',project_id:'project'},{id:'b',project_id:'project'}]);
 const pending=new Map();
 const calls=host((op,args)=>{if(op==='coding_request')return new Promise((resolve,reject)=>pending.set(args.channelId,{resolve,reject}));throw new Error(op);});
 const view=render(React.createElement(Composer,{channelId:'a',review:reviewOn}));
 const draft=()=>screen.getByLabelText('Draft');
 // Channel a: choose on, type, send; the host holds the request in flight.
 chooseReview('on');
 fireEvent.change(draft(),{target:{value:'Fix a'}});
 fireEvent.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>assert.ok(pending.has('a')));
 // Switch to channel b while a is in flight: the choice resets, b gets its own choice and draft.
 view.rerender(React.createElement(Composer,{channelId:'b',review:reviewOn}));
 await waitFor(()=>assert.equal(reviewValue(),'default','a conversation switch resets the override'));
 chooseReview('off');
 fireEvent.change(draft(),{target:{value:'Fix b'}});
 // The old send completes now. It must not touch b.
 await act(async()=>{pending.get('a').resolve({id:'r-a',taskId:'t-a',state:'working',detail:'Working'});});
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(reviewValue(),'off','the old completion did not reset the new channel\'s choice');
 assert.equal(draft().value,'Fix b','the old completion did not clear the new channel\'s draft');
 assert.equal(screen.getByTestId('reply').textContent,'reply-target','the old completion did not clear the new channel\'s reply target');
 assert.deepEqual(calls.filter(c=>c.operation==='coding_request').map(c=>[c.args.channelId,c.args.text,c.args.review]),[['a','Fix a',{enabled:true,reviewer:'reviewer',effort:'low'}]]);
 // A failed send from b keeps the choice and the draft.
 fireEvent.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>assert.ok(pending.has('b')));
 await act(async()=>{pending.get('b').reject(new Error('Lost acknowledgement'));});
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/Lost acknowledgement/));
 assert.equal(reviewValue(),'off');assert.equal(draft().value,'Fix b');
 // The retry from the still-current channel completes: now the choice, draft and reply target reset.
 pending.delete('b');
 fireEvent.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>assert.ok(pending.has('b')));
 await act(async()=>{pending.get('b').resolve({id:'r-b',taskId:'t-b',state:'working',detail:'Working'});});
 await waitFor(()=>assert.equal(reviewValue(),'default','a completed send from the current conversation resets the override'));
 assert.equal(draft().value,'');assert.equal(screen.getByTestId('reply').textContent,'');
 const b=calls.filter(c=>c.operation==='coding_request'&&c.args.channelId==='b');
 assert.equal(b.length,2);assert.equal(b[0].args.clientId,b[1].args.clientId,'the retry reuses the request id');
 assert.deepEqual(b.map(c=>c.args.review),[{enabled:false,reviewer:null,effort:null},{enabled:false,reviewer:null,effort:null}]);
 // Reopen a: project default again, and a default send carries no review key.
 view.rerender(React.createElement(Composer,{channelId:'a',review:reviewOn}));
 await waitFor(()=>assert.equal(reviewValue(),'default'));
 fireEvent.change(draft(),{target:{value:'Again a'}});
 pending.delete('a');
 fireEvent.click(screen.getByRole('button',{name:'Send'}));
 await waitFor(()=>assert.ok(pending.has('a')));
 await act(async()=>{pending.get('a').resolve({id:'r-a2',taskId:'t-a2',state:'working',detail:'Working'});});
 await waitFor(()=>assert.equal(draft().value,''));
 const last=calls.filter(c=>c.operation==='coding_request').at(-1).args;
 assert.equal(last.text,'Again a');assert.ok(!('review' in last),'project default keeps the older request shape');
 // Project policy off: on is unavailable and explained; choosing it is impossible from the control, and default sends no key.
 view.rerender(React.createElement(Composer,{channelId:'a',review:reviewOff}));
 assert.ok(reviewRadios()[2].disabled);
 fireEvent.click(screen.getByText('Set up a reviewer'));
 assert.match(screen.getByRole('note').textContent,/Choose a reviewer in Project settings, then Agents and review/);
});


test('permission loading and failure never claim an empty project or enable a grant, and retry restores the files',async()=>{
 let rejectFirst;let attempts=0;
 const calls=host(op=>{if(op!=='coding_permission')throw new Error(op);attempts++;if(attempts===1)return new Promise((_,reject)=>{rejectFirst=reject;});return granted('codex');});
 mount(dialog('codex'));
 assert.equal(screen.queryByText(/No usable files/),null);
 assert.equal(screen.getByRole('button',{name:'Allow coding'}).disabled,true);
 await act(async()=>rejectFirst(new Error('Project access unavailable')));
 await screen.findByRole('alert');
 assert.equal(screen.queryByText(/No usable files/),null);
 assert.equal(screen.getByRole('button',{name:'Allow coding'}).disabled,true);
 fireEvent.click(screen.getByRole('button',{name:'Try again'}));
 await screen.findByLabelText('Allow greeting.py');
 assert.equal(attempts,2);
 assert.equal(calls.some(c=>c.operation==='coding_permission_grant'),false);
});


test('saved nested file grants stay visible and can be removed without changing another file',async()=>{
 let saved={...granted('codex'),files:['greeting.py','test_sample.py','src/nested.ts']};
 const calls=host((op,args)=>{if(op==='coding_permission')return saved;if(op==='coding_permission_grant'){saved={...saved,files:args.files};return saved;}throw new Error(op);});
 mount(dialog('codex'));
 const nested=await screen.findByLabelText('Allow src/nested.ts');
 await waitFor(()=>assert.equal(nested.checked,true));
 fireEvent.click(nested);
 assert.equal(screen.getByLabelText('Allow src/nested.ts').checked,false);
 fireEvent.click(screen.getByRole('button',{name:'Save file access'}));
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='coding_permission_grant').length,1));
 assert.deepEqual(calls.find(c=>c.operation==='coding_permission_grant').args.files,['greeting.py','test_sample.py']);
});

test('check duration keeps legacy five, validates edits and preserves rejected input and nested grants',async()=>{
 let saved=undefined,reject=true;
 const value=()=>({...granted('codex'),files:['src/search.py','test_sample.py'],folders:['src'],check_timeout_seconds:saved});
 const calls=host((op,args)=>{
  if(op==='coding_permission')return value();
  if(op==='coding_permission_grant'){if(reject)throw new Error('Fixture refused save');saved=args.checkTimeoutSeconds;return value();}
  throw new Error(op);
 });
 const qc=client();const view=mount(dialog('codex'),qc);
 await ready('');
 const input=screen.getByRole('spinbutton',{name:'Maximum check time in seconds'});
 assert.equal(input.value,'5');
 const button=screen.getByRole('button',{name:'Save file access'});
 for(const value of ['','4','121','5.5']){fireEvent.change(input,{target:{value}});assert.equal(button.disabled,true);}
 fireEvent.change(input,{target:{value:'60'}});assert.equal(button.disabled,false);
 fireEvent.click(button);
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/Fixture refused save/));
 assert.equal(input.value,'60');assert.equal(saved,undefined);
 reject=false;fireEvent.click(button);
 await waitFor(()=>assert.equal(saved,60));
 const grant=calls.filter(c=>c.operation==='coding_permission_grant').at(-1).args;
 assert.deepEqual(grant.files,['src/search.py','test_sample.py']);assert.deepEqual(grant.folders,['src']);
 assert.equal(grant.checkTimeoutSeconds,60);
 await act(async()=>{await qc.invalidateQueries({queryKey:['coding-permission','p','c']});});
 assert.equal(input.value,'60','same-second save keeps the saved duration');
 fireEvent.change(input,{target:{value:'90'}});
 view.rerender(wrap(dialog('codex',false),qc));view.rerender(wrap(dialog('codex',true),qc));
 await waitFor(()=>assert.equal(screen.getByRole('spinbutton',{name:'Maximum check time in seconds'}).value,'60'));
 assert.equal(calls.some(c=>['task_create','coding_request'].includes(c.operation)),false);
});
