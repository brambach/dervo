import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {CollaborationSettings,ReviewOverride}=await import('./CollaborationUI.tsx');
const {ReviewerSetup}=await import('./ReviewerSetup.tsx');
const {codingRequest,registerConversations,CLAUDE_EFFORTS}=await import('./api.ts');
const {reviewLabel}=await import('./collaboration.ts');
after(()=>dom.window.close());

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const mount=(element,c=client())=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:c},element)));
const conversations=[{id:'main',name:'Engineering',visibility:'shared',project_id:'p',profile_id:'owner',model:'m'},{id:'review-room',name:'Review room',visibility:'shared',project_id:'p',profile_id:'cx',model:'m'},{id:'fable-room',name:'Fable room',visibility:'shared',project_id:'p',profile_id:'cl',model:'claude-fable-5-1'}];
const profiles=[{id:'owner',provider:'codex',identity:'me@example.test',category:'personal'},{id:'cx',provider:'codex',identity:'me@example.test',category:'personal'},{id:'cl',provider:'claude',identity:'me@example.test',category:'personal'}];
const agents=[{name:'reviewer',profile_id:'cx',provider:'codex',model:'m',project_id:'p',channel_id:'review-room',role:'reviewer',tools:['dervo_read_file'],instructions:'',created_at:1,actor:'local-user',state:'ready',detail:'ready'},
 {name:'fable',profile_id:'cl',provider:'claude',model:'claude-fable-5-1',project_id:'p',channel_id:'fable-room',role:'reviewer',tools:['dervo_read_file'],instructions:'',created_at:1,actor:'local-user',state:'ready',detail:'ready'},
 {name:'fixer',profile_id:'cl',provider:'claude',model:'claude-fable-5-1',project_id:'p',channel_id:'fable-room',role:'worker',tools:['dervo_read_file','dervo_write_file','dervo_run_tests'],instructions:'',created_at:1,actor:'local-user',state:'ready',detail:'ready'}];
const reviewOff={enabled:false,reviewer:null,effort:null,provider:null,model:null,notice:'Independent review runs a second agent under its own account after each finished request: one review, at most one correction, one final check. That is extra usage on that account.'};
const settings=(policy,extra={})=>React.createElement(CollaborationSettings,{open:true,onOpenChange(){},projectId:'p',channelId:'main',channelName:'Engineering',binding:{provider:'codex',identity:'me@example.test',model:'m',profile_id:'owner',visibility:'shared'},conversations,profiles,boundaries:[{id:'b',profiles:['owner','cx','cl']}],refresh:async()=>{},...extra});

test('quick setup discovers efforts and atomically enables review without stale custom form disabling it',async()=>{
 let policy={state:'off',detail:'Off',mode:'off',agents:[],channels:[],limits:{max_parallel:3,max_depth:1,max_calls:6},available:[],unavailable:[],review:reviewOff};
 const catalog={state:'catalog',models:[{model:'m',defaultReasoningEffort:'medium',supportedReasoningEfforts:[{reasoningEffort:'medium'},{reasoningEffort:'high'}]}]};
 const calls=host(async(op,args)=>{
   if(op==='collab_policy'){await new Promise(r=>setTimeout(r,30));return policy;}
   if(op==='collab_agents')return [];
   if(op==='model_status'||op==='model_check')return catalog;
   if(op==='collab_reviewer_setup'){policy={...policy,review:{enabled:true,reviewer:'auto-reviewer',effort:args.effort,provider:'codex',model:'m'}};return policy;}
   if(op==='collab_policy_set'){assert.equal(args.review.enabled,true,'saving custom settings must keep the new review enabled');return policy;}
   throw new Error(op);
 });
 try{
   const view=mount(settings(policy));
   fireEvent.click(await view.findByRole('button',{name:'Set up review'}));
   const enable=await view.findByRole('button',{name:'Enable review'});
   await waitFor(()=>assert.equal(enable.disabled,false));
   assert.equal(view.queryByRole('button',{name:'low'}),null,'no invented effort outside catalog');
   assert.match(view.getByLabelText('Quick reviewer setup').textContent,/additional allowance/);
   fireEvent.click(enable);
   await waitFor(()=>assert.match(view.getByTestId('collab-review-state').textContent,/Independent review: on/));
   assert.deepEqual(calls.find(c=>c.operation==='collab_reviewer_setup').args,{projectId:'p',channelId:'main',profileId:'owner',model:'m',effort:'medium'});
   assert.equal(view.queryByLabelText('Quick reviewer setup'),null,'once review is on, the quick setup goes away');
   assert.equal(view.getAllByLabelText('Independent review of each finished request').length,1,'one review switch');
   await waitFor(()=>assert.equal(view.getByLabelText('Independent review of each finished request').checked,true));
   assert.equal(view.getByLabelText('Independent review of each finished request').closest('details'),null,'the switch is not hidden under agents');
   fireEvent.click(view.getByRole('button',{name:'Save collaboration policy'}));
   await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='collab_policy_set').length,1));
   assert.ok(!calls.some(c=>c.operation==='create_agent_conversation'||c.operation==='collab_agent_save'||c.operation==='coding_request'));
 }finally{cleanup();}
});

test('quick setup blocks unsupported model, preserves rejected setup and prevents duplicate submission',async()=>{
 let available=false, rejectSave;
 const calls=host((op)=>{
   if(op==='model_status'||op==='model_check')return {state:'catalog',models:available?[{model:'m',defaultReasoningEffort:'high',supportedReasoningEfforts:[{reasoningEffort:'high'}]}]:[]};
   if(op==='collab_reviewer_setup')return new Promise((_,reject)=>{rejectSave=reject;});
   throw new Error(op);
 });
 let saved=0;
 try{
   const view=mount(React.createElement(ReviewerSetup,{projectId:'p',channelId:'main',binding:{profile_id:'owner',provider:'codex',model:'m',identity:'fixture@example.test',visibility:'shared'},onSaved:async()=>{saved++;}}));
   fireEvent.click(view.getByRole('button',{name:'Set up review'}));
   await view.findByText('Review options aren’t available for this model yet.');
   assert.equal(view.getByRole('button',{name:'Enable review'}).disabled,true);
   available=true;fireEvent.click(view.getByRole('button',{name:'Reload model options'}));
   await waitFor(()=>assert.equal(view.getByRole('button',{name:'Enable review'}).disabled,false));
   fireEvent.click(view.getByRole('button',{name:'Enable review'}));
   fireEvent.click(view.getByRole('button',{name:'Setting up…'}));
   await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='collab_reviewer_setup').length,1));
   rejectSave(new Error('Account changed. Open setup again.'));
   await view.findByRole('alert');
   assert.equal(saved,0);
   assert.equal(view.getByRole('button',{name:'high'}).getAttribute('aria-pressed'),'true');
 }finally{cleanup();}
});

test('composer setup action opens settings without starting a request',()=>{
 let opened=0;
 const calls=host(op=>{throw new Error(op);});
 try{const view=mount(React.createElement(ReviewOverride,{review:reviewOff,value:'default',onChange(){},onSetup(){opened++;}}));
   fireEvent.click(view.getByRole('button',{name:'Set up a reviewer'}));assert.equal(opened,1);assert.equal(calls.length,0);
 }finally{cleanup();}
});

test('the composer says before sending when the project reviewer can’t review this conversation’s account',()=>{
 host(op=>{throw new Error(op);});
 const reason="@reviewer runs on Codex, which isn't linked to this conversation's Claude Code account, so no review was run. Link the two accounts in Settings, on Connected accounts, under Help between your accounts, to review here.";
 const on={...reviewOff,enabled:true,reviewer:'reviewer',effort:'low',provider:'codex',model:'gpt'};
 try{const view=mount(React.createElement(ReviewOverride,{review:{...on,skipped_here:reason},value:'default',onChange(){}}));
   const radios=()=>view.getAllByRole('radio');
   assert.deepEqual(radios().map(r=>r.textContent),['Project default (skipped here)','Off','On']);
   assert.equal(view.getByRole('radiogroup',{name:'Independent review for this request'}).children.length,3);
   assert.equal(radios()[2].disabled,true,'asking for review by name would be refused, so it isn’t offered');
   assert.equal(view.getByRole('note').textContent,reason);
   view.rerender(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:new QueryClient()},React.createElement(ReviewOverride,{review:{...on,skipped_here:null},value:'default',onChange(){}}))));
   assert.equal(radios()[0].textContent,'Project default (on)');
   assert.equal(radios()[2].disabled,false);
   assert.equal(view.queryByRole('note'),null);
 }finally{cleanup();}
});

test('independent review is off by default, binds one reviewer agent and an effort explicitly, states the extra usage, and is saved only as an explicit choice',async()=>{
 let policy={state:'off',detail:'Collaboration is off for this project.',mode:'off',agents:[],channels:[],limits:{max_parallel:3,max_depth:1,max_calls:6},available:[],unavailable:[],review:reviewOff};
 const calls=host((op,args)=>{
  if(op==='collab_policy')return policy;
  if(op==='collab_agents')return agents;
  if(op==='collab_policy_set'){policy={...policy,mode:args.mode,agents:args.agents,channels:args.channels,review:args.review?{...reviewOff,...args.review,provider:'claude',model:'claude-fable-5-1'}:policy.review};return policy;}
  throw new Error(op);
 });
 try{
  const view=mount(settings(policy));
  const toggle=await view.findByLabelText('Independent review of each finished request');
  await waitFor(()=>assert.ok(view.getByTestId('collab-policy-state').textContent.includes('Now: off')));
  assert.equal(toggle.checked,false,'off by default');
  assert.ok(document.body.textContent.includes('extra usage on that account'),'the usage notice is the host text');
  assert.equal(view.queryByLabelText('Reviewer agent'),null,'nothing to bind while the review is off');
  // Saving without touching the review sends the legacy shape: no review key at all.
  fireEvent.click(view.getByRole('button',{name:'Save collaboration policy'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='collab_policy_set').length,1));
  assert.equal('review' in calls.find(c=>c.operation==='collab_policy_set').args,false);
  fireEvent.click(toggle);
  const reviewer=view.getByLabelText('Reviewer agent');
  assert.deepEqual([...reviewer.options].map(o=>o.value),['','reviewer','fable'],'only read-only reviewer agents of this project can be bound; a worker never is');
  fireEvent.change(reviewer,{target:{value:'fable'}});
  const effort=view.getByLabelText('Reviewer effort');
  assert.deepEqual([...effort.options].map(o=>o.value),['',...CLAUDE_EFFORTS],'a Claude reviewer offers exactly the documented CLI effort levels');
  assert.deepEqual(CLAUDE_EFFORTS,['low','medium','high','xhigh','max']);
  // Each card saves only its own edits: the policy Save leaves an unsaved review alone and says so.
  fireEvent.click(view.getByRole('button',{name:'Save collaboration policy'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='collab_policy_set').length,2));
  assert.equal('review' in calls.filter(c=>c.operation==='collab_policy_set')[1].args,false,'no half-finished review rides along with the policy');
  await waitFor(()=>assert.ok(document.body.textContent.includes('Your unsaved review changes are still unsaved')));
  assert.equal(view.getByRole('button',{name:'Save review'}).disabled,true,'a review needs its effort before it can be saved');
  fireEvent.change(effort,{target:{value:'low'}});
  fireEvent.click(view.getByRole('button',{name:'Save review'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='collab_policy_set').length,3));
  assert.deepEqual(calls.filter(c=>c.operation==='collab_policy_set')[2].args.review,{enabled:true,reviewer:'fable',effort:'low'});
  await waitFor(()=>assert.ok(document.body.textContent.includes('Independent review: on · @fable · Claude Code · claude-fable-5-1 · effort low')));
  assert.equal(calls.filter(c=>['coding_request','task_create','harness_request'].includes(c.operation)).length,0,'configuration launches nothing');
  cleanup();
  // A Codex reviewer: the effort is whatever the Codex model catalog advertises, checked by the host before anything is sent.
  const codex=mount(settings(policy));
  fireEvent.click(await codex.findByLabelText('Independent review of each finished request'));
  // Before a reviewer is chosen there's no effort to pick and no Codex hint (the reviewer's first finding).
  fireEvent.change(codex.getByLabelText('Reviewer agent'),{target:{value:''}});
  assert.equal(codex.getByLabelText('Reviewer effort').disabled,true);
  assert.equal(document.body.textContent.includes('Codex model catalog'),false);
  fireEvent.change(codex.getByLabelText('Reviewer agent'),{target:{value:'reviewer'}});
  const free=codex.getByLabelText('Reviewer effort');
  assert.equal(free.tagName,'INPUT','no invented Codex effort list');
  assert.ok(document.body.textContent.includes('the exact effort the Codex model catalog lists'));
 }finally{cleanup();}
});

test('a request can override the project setting explicitly, and the composer sends exactly that',async()=>{
 registerConversations(conversations);
 const calls=host((op,args)=>{if(op==='coding_request')return {id:'r1',taskId:'t1',task_id:'t1',state:'working',detail:'',text:args.text};throw new Error(op);});
 await codingRequest('main','Add a comment',{enabled:true,reviewer:'fable',effort:'max'});
 assert.deepEqual(calls[0].args.review,{enabled:true,reviewer:'fable',effort:'max'});
 await codingRequest('main','Add another comment');
 assert.equal('review' in calls[1].args,false,'no override means the project setting, sent as the legacy shape');
 assert.equal(reviewLabel(null),'');
 assert.equal(reviewLabel({phase:'passed',reviewer:'fable',effort:'low'}),'Independent review by @fable (effort low): passed, no open findings');
 assert.equal(reviewLabel({phase:'unresolved',reviewer:'fable',effort:'low',unresolved:'line 1: still no docstring'}),'Independent review by @fable (effort low): unresolved after one correction: line 1: still no docstring');
 assert.equal(reviewLabel({phase:'stale',reviewer:'fable',effort:'low',unresolved:'line 1'}),'Independent review by @fable (effort low): stale, the files changed while it ran; findings returned: line 1');
 assert.equal(reviewLabel({phase:'initial',reviewer:'fable',effort:'low'}),'Independent review by @fable (effort low): initial review running');
 for(const phase of ['passed','unresolved','stale','failed','interrupted'])assert.ok(!/accept|approv/i.test(reviewLabel({phase,reviewer:'r',effort:'low',error:'x',unresolved:'y'})),'a review verdict never reads as human acceptance');
});

test('the review line under a task shows the first finding and a count, not every finding',()=>{
 const findings=['app/CollaborationUI.tsx:203 — Save collaboration policy sends the review with no reviewer. '+'x'.repeat(200),'app/collaboration-ui.test.mjs:46 — the test targets a retired menu item.','Recorded checks show no test output.'].join('\n');
 const review={phase:'unresolved',reviewer:'reviewer',effort:'low',unresolved:findings};
 const short=reviewLabel(review,undefined,{short:true});
 assert.match(short,/^Independent review by @reviewer \(effort low\): unresolved after one correction: app\/CollaborationUI\.tsx:203/);
 assert.match(short,/… \(2 more in the task record\)$/);assert.ok(short.length<300,String(short.length));
 assert.equal(reviewLabel(review),`Independent review by @reviewer (effort low): unresolved after one correction: ${findings}`,'the record keeps the full text');
});
