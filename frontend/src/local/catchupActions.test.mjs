import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor}=await import('@testing-library/react');
const {CatchupActions,catchupAction}=await import('./CatchupActions.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});
function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const row=(extra={})=>({id:'t1',kind:'task',channelId:'c1',channelName:'Ship it',snippet:'',source:{},status:'ready_for_review',humanApprovalRequired:true,humanDecision:null,...extra});
const attempt={number:3,state:'verified',kind:'change',diff:{},before:{},after:{},argv:[]};
function mount(r){let done=0;render(React.createElement(CatchupActions,{row:r,projectId:'p',disabled:false,onDone:async()=>{done++;}}));return ()=>done;}

test('each card nominates at most one action from its saved state',()=>{
 assert.equal(catchupAction(row()),'approve');
 assert.equal(catchupAction(row({status:'working'})),'stop');
 assert.equal(catchupAction(row({status:'blocked',runState:'failed'})),'retry');
 assert.equal(catchupAction(row({status:'blocked',runState:'interrupted'})),'retry');
 for(const extra of [{humanDecision:'rejected'},{stale:true},{outcomeUncertain:true},{reviewPending:true},{humanApprovalRequired:false},{helper:true},{kind:'message'},
   {status:'blocked',runState:'completed'},{status:'blocked',runState:'uncertain'},{status:'completed'},{status:'disconnected'},{status:'answered'}])
  assert.equal(catchupAction(row(extra)),null,JSON.stringify(extra));
});

test('approve asks first, reads the record, then approves exactly the latest attempt',async()=>{
 const calls=host(op=>op==='task_detail'?{task:{state:'ready_for_review'},git:true,retry:{available:false},evidence:{attempts:[{...attempt,number:2},attempt]}}:{});
 const done=mount(row());
 fireEvent.click(screen.getByRole('button',{name:'Approve result'}));
 // Rule change (#27): the confirm reads the record (read-only) to know whether approving commits. Still no approval is sent before the confirmation.
 assert.ok(calls.every(c=>c.operation==='task_detail'),'nothing but a read before the confirmation');
 await waitFor(()=>assert.match(screen.getByRole('group',{name:'Approve result'}).textContent,/Approve and commit this result without opening it\?/));
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 assert.ok(screen.getByRole('button',{name:'Approve result'}));
 fireEvent.click(screen.getByRole('button',{name:'Approve result'}));
 fireEvent.click(await screen.findByRole('button',{name:'Approve and commit'}));
 await waitFor(()=>assert.equal(done(),1));
 assert.deepEqual(calls.filter(c=>c.operation==='task_review'),[{operation:'task_review',args:{projectId:'p',channelId:'c1',taskId:'t1',attemptNumber:3,decision:'approved'}}]);
 assert.deepEqual(calls.at(-2),{operation:'task_detail',args:{projectId:'p',channelId:'c1',taskId:'t1'}},'the record is read again right before approving');
});

test('#27: outside a git repository, or before the record answers, the confirm never promises a commit',async()=>{
 for(const extra of [{git:false},{}]){
  host(op=>op==='task_detail'?{task:{state:'ready_for_review'},retry:{available:false},evidence:{attempts:[attempt]},...extra}:{});
  mount(row());
  fireEvent.click(screen.getByRole('button',{name:'Approve result'}));
  const group=screen.getByRole('group',{name:'Approve result'});
  assert.match(group.textContent,/Approve this result without opening it\?/,'before the read answers');
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.match(group.textContent,/Approve this result without opening it\?/,JSON.stringify(extra));
  assert.doesNotMatch(group.textContent,/commit/i,JSON.stringify(extra));
  assert.ok(screen.getByRole('button',{name:'Approve'}));
  cleanup();
 }
});

test('a result that carried on from a handoff isn’t approved from Catch up: it commits files you haven’t seen listed',async()=>{
 const carried={id:'plan-1',from_task_id:'t0',here:'Claude',to:'Codex',files:['notes.md'],flagged:[]};
 const calls=host(op=>op==='task_detail'?{task:{state:'ready_for_review'},git:true,carried,retry:{available:false},evidence:{attempts:[attempt]}}:{});
 mount(row());
 fireEvent.click(screen.getByRole('button',{name:'Approve result'}));
 const group=screen.getByRole('group',{name:'Approve result'});
 await waitFor(()=>assert.equal(group.querySelector('span').textContent,'This result includes files Claude changed before the handoff. Open it to see them before approving.'));
 assert.equal(screen.queryByRole('button',{name:'Approve and commit'}),null);
 assert.equal(calls.filter(c=>c.operation==='task_review').length,0);
});

test('approve refuses when the record no longer waits for review, and says to open it',async()=>{
 for(const detail of [{task:{state:'completed'},retry:{available:false},evidence:{attempts:[attempt]}},{task:{state:'ready_for_review'},retry:{available:false},evidence:{attempts:[]}},
   {task:{state:'ready_for_review'},rebound:true,retry:{available:false},evidence:{attempts:[attempt]}},{task:{state:'ready_for_review'},retry:{available:true},evidence:{attempts:[attempt]}}]){
  const calls=host(op=>op==='task_detail'?detail:{});
  const done=mount(row());
  fireEvent.click(screen.getByRole('button',{name:'Approve result'}));fireEvent.click(screen.getByRole('button',{name:'Approve'}));
  assert.match((await screen.findByRole('alert')).textContent,/can’t be approved from here\. See the record\./);
  // Rule change (#27): the confirm's own read adds one task_detail; still nothing but reads.
  assert.ok(calls.every(c=>c.operation==='task_detail'),JSON.stringify(calls));assert.equal(done(),0);
  cleanup();
 }
});

test('retry continues only when the record allows it, and points at the other provider when that’s the way',async()=>{
 let calls=host(op=>op==='task_detail'?{task:{state:'blocked'},retry:{available:true},evidence:{attempts:[]}}:{});
 let done=mount(row({status:'blocked',runState:'failed'}));
 fireEvent.click(screen.getByRole('button',{name:'Retry'}));
 await waitFor(()=>assert.equal(done(),1));
 assert.deepEqual(calls.at(-1),{operation:'task_continue',args:{projectId:'p',channelId:'c1',taskId:'t1',text:'Please try that again.'}});
 cleanup();
 calls=host(op=>op==='task_detail'?{task:{state:'blocked',suggest:{other:{label:'Claude Code'},taken:null}},retry:{available:false},evidence:{attempts:[]}}:{});
 done=mount(row({status:'blocked',runState:'failed'}));
 fireEvent.click(screen.getByRole('button',{name:'Retry'}));
 assert.match((await screen.findByRole('alert')).textContent,/can be tried on Claude Code from its conversation/);
 assert.deepEqual(calls.map(c=>c.operation),['task_detail']);assert.equal(done(),0);
});

test('stop sends task_stop for exactly that task',async()=>{
 const calls=host(()=>({}));
 const done=mount(row({status:'working'}));
 fireEvent.click(screen.getByRole('button',{name:'Pause'}));
 await waitFor(()=>assert.equal(done(),1));
 assert.deepEqual(calls,[{operation:'task_stop',args:{projectId:'p',channelId:'c1',taskId:'t1'}}]);
});
