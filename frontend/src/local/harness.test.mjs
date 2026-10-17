import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
const React=await import('react');
const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
const {renderToStaticMarkup}=await import('react-dom/server');
const {coordinate,registerConversations}=await import('./api.ts');
const {HarnessFooter,harnessRows,mergeHarnessRows,requestRowId,resultRowId,COORDINATION_LABELS}=await import('./Harness.tsx');
const {ConversationIndicator,composerMode}=await import('./TurnControls.tsx');
after(()=>dom.window.close());

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const request=(over={})=>({id:'r1',state:'running',mode:'coordinate',helper:'claude-abc',provider:'claude',text:'What date is next Friday?',linked_return:null,delegations:[],error:null,created_at:1000,detail:'',...over});

test('the composer sends to coordination with one request id, keeps the draft when nothing was recorded, and never loops',async()=>{
 localStorage.clear();registerConversations([{id:'chat',project_id:'project'}]);
 const answers=[{id:null,state:'unavailable',detail:'No helper serves this conversation.'},request({state:'queued'})];
 const calls=host(op=>{if(op==='harness_request')return answers.shift();throw new Error(op);});
 await assert.rejects(coordinate('chat','What date is next Friday?','claude-abc'),/No helper serves/);
 const pending=JSON.parse(localStorage.getItem('dervo-coordination-pending:chat'));
 assert.equal(pending.helper,'claude-abc');
 await assert.rejects(coordinate('chat','Different text','claude-abc'),/unknown outcome/);
 const result=await coordinate('chat','What date is next Friday?','claude-abc');
 assert.equal(result.state,'queued');
 assert.equal(localStorage.getItem('dervo-coordination-pending:chat'),null);
 const sent=calls.filter(c=>c.operation==='harness_request');
 assert.equal(sent.length,2);
 assert.equal(sent[0].args.clientId,sent[1].args.clientId,'a retry reuses the same request id');
 assert.deepEqual(Object.keys(sent[1].args).sort(),['channelId','clientId','helper','mentions','mode','projectId','text']);
 assert.deepEqual(sent[1].args.mentions,[]);
 assert.equal(sent[1].args.mode,'coordinate');
});

test('coordination rows show the request as yours and the return as the helper provider, merged by time',()=>{
 const done=request({state:'completed',linked_return:{summary:'Next Friday is the 18th.',refs:[],worker:'claude-cli-text-only',worker_is_fake:false,result_message_id:'m',returned_at:'2026-09-12T00:20:00.123456+00:00'}});
 const rows=harnessRows([done],'chat');
 assert.deepEqual(rows.map(r=>[r.id,r.pubkey,r.content]),[[requestRowId(done),'local-user','What date is next Friday?'],[resultRowId(done),'helper:claude','Next Friday is the 18th.']]);
 assert.ok(rows[1].created_at>rows[0].created_at,'the return is shown at its real return time');
 const merged=mergeHarnessRows([{id:'h1',content:'earlier chat',pubkey:'local-user',kind:9,sig:'',created_at:900,tags:[],state:'queued'},{id:'h2',content:'later chat',pubkey:'local-user',kind:9,sig:'',created_at:2_000_000_000,tags:[],state:'queued'}],rows);
 assert.deepEqual(merged.map(r=>r.id),['h1',requestRowId(done),resultRowId(done),'h2']);
 for(const row of merged)assert.equal('sortKey' in row,false);
 assert.deepEqual(harnessRows([request()],'chat').map(r=>r.id),[requestRowId(request())],'no answer row until the helper returned');
});

test('a later final answer stays after helpers within the same second, with stable date fallbacks',()=>{
 const order=(owner,helper)=>mergeHarnessRows([],harnessRows([request({state:'completed',
  linked_return:{summary:'Owner answer',returned_at:owner},
  delegations:[{id:'d',handle:'@codex-1',status:'returned',returned_at:helper,result:{summary:'Helper answer'}}],
 })],'chat')).map(row=>row.content);
 const owner='2026-09-13T02:00:00.123456Z';
 assert.deepEqual(order(owner,'2026-09-13T12:00:00.123123+10:00'),['What date is next Friday?','@codex-1 answered: Helper answer','Owner answer']);
 assert.deepEqual(order(owner,'2026-09-13T02:00:00.123789Z'),['What date is next Friday?','Owner answer','@codex-1 answered: Helper answer']);
 for(const date of [owner,null,'invalid','2026-09-13T02:00:00']){
  assert.deepEqual(order(date,date),['What date is next Friday?','@codex-1 answered: Helper answer','Owner answer']);
 }
});

test('the footer labels each state honestly, offers Stop only while active, and names the unsupported coding boundary',async()=>{
 const calls=host(op=>{if(op==='harness_cancel')return request({state:'stopping'});throw new Error(op);});
 let refreshed=0;
 const mount=r=>render(React.createElement(HarnessFooter,{request:r,projectId:'project',channelId:'chat',refresh:async()=>{refreshed++;}}));
 try{
  const running=mount(request());
  assert.ok(running.getByRole('status').textContent.includes('Writing an answer · Claude Code'));
  fireEvent.click(running.getByRole('button',{name:'Stop'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='harness_cancel').length,1));
  assert.deepEqual(calls[0].args,{projectId:'project',channelId:'chat',requestId:'r1'});
  await waitFor(()=>assert.equal(refreshed,1));
  cleanup();
  const done=mount(request({state:'completed',provider:'codex',linked_return:{summary:'ok',refs:[],worker:'codex-app-server-text-only',worker_is_fake:false,result_message_id:'m',returned_at:''}}));
  assert.ok(done.getByRole('status').textContent.includes('Answer ready · Codex'));
  assert.ok(done.getByText(/This reply doesn't change project files/));
  assert.equal(done.queryByRole('button',{name:'Stop'}),null);
  cleanup();
  const failed=mount(request({state:'failed',error:'The harness refused the request: StateError: CLI is not logged in'}));
  assert.ok(failed.getByRole('status').textContent.includes('Couldn’t answer'));
  assert.ok(failed.getByRole('alert').textContent.includes('not logged in'));
  cleanup();
  const unknown=mount(request({state:'unknown',error:'Host restarted before the harness confirmed the outcome. No automatic replay.'}));
  assert.ok(unknown.getByRole('status').textContent.includes('Outcome unconfirmed'));
  assert.ok(!unknown.container.textContent.includes('Returned'));
  cleanup();
  const asked=mount(request({delegations:[{id:'d',handle:'@codex-1',status:'returned',purpose:'p',result:{summary:'s'},error:null}]}));
  assert.ok(asked.getByText(/Helpers asked: @codex-1 \(returned\)/));
  for(const state of Object.keys(COORDINATION_LABELS))assert.notEqual(COORDINATION_LABELS[state],'Executed','coordination is never shown as execution');
 }finally{cleanup();}
});

test('the indicator and composer name coordination only when the host resolved a helper',()=>{
 const binding={provider:'claude',identity:'me@example.test',model:'claude-fable-5-1'};
 const available={state:'blocked',active:false,detail:'x',harness:{state:'available',helper:'claude-abc',provider:'claude',model:'claude-fable-5-1',detail:'Coordination with Claude Code answers in text only.'}};
 assert.equal(composerMode(binding,available),'Coordination · Claude Code · text only');
 assert.equal(composerMode(binding,{state:'blocked',active:false,detail:'x',harness:{state:'unavailable',helper:null,detail:'none'}}),'Sending blocked');
 assert.equal(composerMode({provider:'codex'},{state:'unchecked',active:false,detail:'x'}),'Chat only');
 const html=renderToStaticMarkup(React.createElement(ConversationIndicator,{binding,status:available,onSettings:()=>{}}));
 assert.ok(html.includes('Coordination · Claude Code · text only'));
 assert.ok(html.includes('answers in text only'));
});

test('the app wires coordination into the existing conversation, keeps the coding path, and the bridge allows the list',()=>{
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 // The send reads the helper of the conversation it's for (here.harness), loaded or from its snapshot (#59).
 assert.match(source,/const helper=here\.harness;/);
 assert.match(source,/helper\?\.state==='available'&&helper\.helper/);
 assert.match(source,/await coordinate\(target\.id,text,helper\.helper\)/);
 assert.match(source,/useConversationReads\(projectId,channelId,!!current\)/);
 const reads=readFileSync(new URL('./useConversationReads.ts',import.meta.url),'utf8');
 assert.match(reads,/local<FeedResponse>\('conversation_feed'/);
 assert.match(reads,/requests:field\('requests'\)/);
 assert.match(source,/footers\[requestRowId\(request\)\]/);
 assert.match(source,/<ConnectedAccounts /,'account linking lives on Connected accounts');
 assert.equal(source.includes('<TaskStart '),false,'B12: the hand-entered task dialog had no way in');
 assert.doesNotMatch(source,/harness_request'/,'the composer goes through coordinate(), which owns the request id');
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['harness_requests','harness_boundary','harness_request','harness_cancel','harness_actions','harness_resolve'])assert.ok(bridge.includes(`"${op}"`),op);
});

test('mentions are extracted like the harness does and resolved by the host before anything is sent',async()=>{
 const {mentionsIn}=await import('./api.ts');
 assert.deepEqual(mentionsIn('Ask @Codex-1a2b and @codex-1a2b again, not me@example.test or /path/@x'),['@codex-1a2b']);
 assert.deepEqual(mentionsIn('no mentions here'),[]);
 localStorage.clear();registerConversations([{id:'chat',project_id:'project'}]);
 let calls=host((op,args)=>{
  if(op==='harness_resolve')return args.mention==='@codex-1a2b'?{resolved:true,handle:'@codex-1a2b',provider:'codex',asker_can_ask:true}:{resolved:false,mention:args.mention,reason:'not a helper linked to this conversation'};
  if(op==='harness_request')return request({state:'queued',mentions:args.mentions});
  throw new Error(op);
 });
 await assert.rejects(coordinate('chat','Ask @nobody-here please','claude-abc'),/@nobody-here cannot be asked: not a helper linked/);
 assert.equal(calls.filter(c=>c.operation==='harness_request').length,0,'nothing is sent when a mention does not resolve');
 assert.equal(localStorage.getItem('dervo-coordination-pending:chat'),null,'nothing was recorded, so no pending request id lingers');
 const sent=await coordinate('chat','Ask @codex-1a2b please','claude-abc');
 assert.deepEqual(sent.mentions,['@codex-1a2b']);
 assert.deepEqual(calls.filter(c=>c.operation==='harness_request')[0].args.mentions,['@codex-1a2b']);
 calls=host((op)=>{if(op==='harness_resolve')return {resolved:true,handle:'@codex-1a2b',asker_can_ask:false,note:'switch on asking linked helpers first'};throw new Error(op);});
 await assert.rejects(coordinate('chat','Ask @codex-1a2b','claude-abc'),/switch on asking linked helpers/);
});

test('a linked helper\'s returned answer is its own provider row and the footer shows the recorded help activity',async()=>{
 const {helpRowId}=await import('./Harness.tsx');
 const helped=request({state:'completed',linked_return:{summary:'Asked the Codex helper.',refs:[],worker:'claude-cli-actions',worker_is_fake:false,result_message_id:'m',returned_at:'2026-09-12T00:20:00+00:00'},
  delegations:[{id:'dlg1',handle:'@codex-1a2b',status:'returned',purpose:'Which date?',result:{summary:'The 18th.'},error:null,returned_at:'2026-09-12T00:21:00+00:00'}],
  activity:[{seq:5,kind:'help_requested',text:'Asked @codex-1a2b for help (Codex)'},{seq:9,kind:'help_returned',text:'@codex-1a2b returned an answer'}]});
 const rows=harnessRows([helped],'chat');
 assert.deepEqual(rows.map(r=>[r.id,r.pubkey]),[[requestRowId(helped),'local-user'],[resultRowId(helped),'helper:claude'],[helpRowId(helped,{id:'dlg1'}),'helper:codex']]);
 assert.equal(rows[2].content,'@codex-1a2b answered: The 18th.');
 assert.ok(rows[2].created_at>rows[1].created_at);
 try{
  const view=render(React.createElement(HarnessFooter,{request:helped,projectId:'p',channelId:'chat',refresh:async()=>{}}));
  assert.deepEqual(view.getAllByTestId('coordination-activity').map(e=>e.textContent),['Asked @codex-1a2b for help (Codex)','@codex-1a2b returned an answer']);
  cleanup();
  const waiting=render(React.createElement(HarnessFooter,{request:request({state:'completed',delegations:[{id:'d',handle:'@codex-1a2b',status:'dispatched',purpose:'p',result:null,error:null}]}),projectId:'p',channelId:'chat',refresh:async()=>{}}));
  assert.ok(waiting.getByText(/A linked helper is still working/));
 }finally{cleanup();}
 assert.equal(composerMode({provider:'claude'},{state:'blocked',active:false,detail:'x',harness:{state:'available',helper:'claude-abc',provider:'claude',detail:'d',can_ask:true,catalogue:[{handle:'@codex-1a2b',provider:'codex',model:'m',can_ask:false}]}}),'Coordination · Claude Code · text only · may ask linked helpers');
 assert.equal(composerMode({provider:'claude'},{state:'blocked',active:false,detail:'x',harness:{state:'available',helper:'claude-abc',provider:'claude',detail:'d',can_ask:true,catalogue:[]}}),'Coordination · Claude Code · text only');
});

test('configured coordination does not inherit the legacy blocked-chat warning, but host errors remain visible',()=>{
 const binding={provider:'claude',identity:'fixture@example.test',model:'fixture-model'};
 const status={state:'blocked',active:false,detail:'Chat turns are unavailable for Claude Code in Dervo.',harness:{state:'available',helper:'claude-test',provider:'claude',detail:'Configured, identity checked on send.'}};
 const html=renderToStaticMarkup(React.createElement(ConversationIndicator,{binding,status,onSettings(){}}));
 assert.ok(html.includes('Coordination configured'));
 assert.ok(!html.includes('Chat turns are unavailable'));
 const failed=renderToStaticMarkup(React.createElement(ConversationIndicator,{binding,status,error:true,onSettings(){}}));
 assert.ok(failed.includes('Status unavailable'));
 assert.ok(failed.includes('Status could not be checked'));
});
