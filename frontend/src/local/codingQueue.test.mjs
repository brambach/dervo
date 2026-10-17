import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor}=await import('@testing-library/react');
const {QueueFooter,queueRows,queueLabel,queueRowId}=await import('./CodingQueue.tsx');
const {registerConversations}=await import('./api.ts');
globalThis.localStorage=dom.window.localStorage;
registerConversations([{id:'c',project_id:'p'}]);
const {mergeHarnessRows}=await import('./Harness.tsx');
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});after(()=>dom.window.close());
const queued={id:'q1',client_id:'req-2',state:'queued',text:'Then fix the greeting',images:[],position:1,error:null,detail:'Queued. Starts when the current task ends.',created_at:1700000010};
const failed={...queued,id:'q2',client_id:'req-3',state:'failed',position:null,text:'Also tidy the README',images:['img-1'],checkProfile:'python-unittest',review:{enabled:true,reviewer:'@reviewer',effort:'high'},error:'Dervo restarted before this request started. Nothing was sent. Send it again.',detail:'Dervo restarted before this request started. Nothing was sent. Send it again.',created_at:1700000020};
function host(handler=()=>({})){const calls=[];window.__TAURI_INTERNALS__={invoke:async(command,{operation,args})=>{assert.equal(command,'local_request');calls.push({operation,args});return handler(operation,args);}};return calls;}

test('queued messages are your own rows, placed by the time they were sent, after other rows from the same second',()=>{
 const rows=mergeHarnessRows([{id:'t1:0',content:'Run the tests',pubkey:'local-user',kind:9,sig:'',created_at:1700000000,tags:[],state:'task'},{id:'h1',content:'A note',pubkey:'local-user',kind:9,sig:'',created_at:1700000010,tags:[],state:'sent'}],queueRows([queued,failed],'c'));
 assert.deepEqual(rows.map(row=>row.id),['t1:0','h1','queue:q1','queue:q2']);
 assert.equal(rows[2].content,'Then fix the greeting');assert.equal(rows[2].pubkey,'local-user');assert.equal(rows[2].state,'queued');
 assert.deepEqual(rows[2].tags,[['h','c']]);
 assert.equal(queueRowId(queued),'queue:q1');
});

test('the label says where the message stands, with plain ordinals',()=>{
 assert.equal(queueLabel({state:'queued',position:1}),'Queued · runs next');
 assert.equal(queueLabel({state:'queued',position:2}),'Queued · 2nd in line');
 assert.equal(queueLabel({state:'queued',position:3}),'Queued · 3rd in line');
 assert.equal(queueLabel({state:'queued',position:4}),'Queued · 4th in line');
 assert.equal(queueLabel({state:'queued',position:12}),'Queued · 12th in line');
 assert.equal(queueLabel({state:'queued',position:1,why:'slot'}),'Waiting for a slot','every task slot is busy elsewhere');
 assert.equal(queueLabel({state:'failed',position:null,why:'slot'}),'Couldn’t start');
 assert.equal(queueLabel({state:'failed',position:null}),'Couldn’t start');
 assert.equal(queueLabel({state:'queued',position:1,why:'slot',held:true}),'Kept after a restart','held behind lost runs: never a slot wait');
});

test('a request kept across a restart and held behind lost runs says so, and what to do (backend review, 23 Sep)',()=>{
 host();
 const detail='Kept when Dervo restarted. Starts once the runs Dervo lost track of in this project are settled.';
 render(React.createElement(QueueFooter,{item:{...queued,why:'slot',held:true,detail},projectId:'p',channelId:'c',refresh:async()=>{}}));
 assert.equal(screen.getByRole('status').textContent,'Kept after a restart');
 assert.match(document.querySelector('.dervo-queue-detail').textContent,/^Kept when Dervo restarted\. .* Mark each one stopped once you’ve checked its folder\.$/);
 assert.ok(screen.getByRole('button',{name:'Cancel'}),'it can still be cancelled');
});

test('Cancel sends coding_queue_cancel with the conversation\'s ids, then refreshes',async()=>{
 let refreshed=0;const calls=host();
 render(React.createElement(QueueFooter,{item:queued,projectId:'p',channelId:'c',refresh:async()=>{refreshed++;}}));
 assert.equal(screen.getByRole('status').textContent,'Queued · runs next');
 assert.equal(screen.queryByRole('button',{name:'Send again'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 await waitFor(()=>assert.equal(refreshed,1));
 assert.deepEqual(calls,[{operation:'coding_queue_cancel',args:{projectId:'p',channelId:'c',queueId:'q1'}}]);
});

test('a message that could not start shows the host\'s reason; Send again re-sends its exact text, images, check and review, then dismisses it',async()=>{
 const calls=host(op=>op==='coding_request'?{id:'r9',taskId:'t9',state:'working',detail:'Working'}:{});let refreshed=0;
 render(React.createElement(QueueFooter,{item:failed,projectId:'p',channelId:'c',refresh:async()=>{refreshed++;}}));
 assert.equal(screen.getByRole('status').textContent,'Couldn’t start');
 assert.equal(screen.getByRole('alert').textContent,'Dervo restarted before this request started. Nothing was sent. Send it again.');
 assert.equal(screen.queryByRole('button',{name:'Cancel'}),null);
 fireEvent.click(screen.getByRole('button',{name:'Send again'}));
 await waitFor(()=>assert.equal(refreshed,1));
 assert.equal(calls.length,2);
 assert.equal(calls[0].operation,'coding_request');
 const {clientId,...sent}=calls[0].args;
 assert.match(clientId,/^[0-9a-f-]{36}$/,'codingRequest owns the request id');
 assert.deepEqual(sent,{text:'Also tidy the README',projectId:'p',channelId:'c',review:{enabled:true,reviewer:'@reviewer',effort:'high'},images:['img-1'],checkProfile:'python-unittest'});
 assert.deepEqual(calls[1],{operation:'coding_queue_cancel',args:{projectId:'p',channelId:'c',queueId:'q2'}});
});

test('a refused resend keeps the failed item and shows the refusal; nothing is dismissed',async()=>{
 const calls=host(op=>{if(op==='coding_request')return {id:null,taskId:null,state:'needs_permission',detail:'Allow coding in this project first.'};return {};});
 render(React.createElement(QueueFooter,{item:failed,projectId:'p',channelId:'c',refresh:async()=>{}}));
 fireEvent.click(screen.getByRole('button',{name:'Send again'}));
 await waitFor(()=>assert.equal(screen.getAllByRole('alert').length,2));
 assert.match(screen.getAllByRole('alert')[1].textContent,/Allow coding in this project first/);
 assert.deepEqual(calls.map(c=>c.operation),['coding_request']);
 fireEvent.click(screen.getByRole('button',{name:'Dismiss'}));
 await waitFor(()=>assert.equal(calls.length,2));
 assert.deepEqual(calls[1],{operation:'coding_queue_cancel',args:{projectId:'p',channelId:'c',queueId:'q2'}});
});

test('a refused cancel keeps the row and shows why',async()=>{
 host(()=>{throw Error('That queued request is no longer waiting');});
 render(React.createElement(QueueFooter,{item:queued,projectId:'p',channelId:'c',refresh:async()=>{}}));
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/no longer waiting/));
 assert.equal(screen.getByRole('button',{name:'Cancel'}).disabled,false);
});

test('a request waiting for a reset says when, instead of its place in line',async()=>{
 const {queueLabel}=await import('./CodingQueue.tsx');
 const until=Math.floor(new Date(2026,8,17,16,30).getTime()/1000);
 assert.match(queueLabel({state:'queued',position:1,until}),/^Waiting for the reset at 4:30/);
 assert.equal(queueLabel({state:'queued',position:2,until:null}),'Queued · 2nd in line');
 assert.equal(queueLabel({state:'failed',position:null,until}),'Couldn’t start');
});
