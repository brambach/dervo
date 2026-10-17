import assert from 'node:assert/strict';
import {test} from 'node:test';
import {replyRun,runSummary,elapsed} from './replyTrail.ts';
import {presentResponses,instantReplies} from './responsePresentation.ts';

const native=(tool,path,state,at,extra={})=>({tool:`native:${tool}`,native:true,path,command:null,state,run_id:'r',started_at:at,...extra});
const base={id:'t',state:'working',binding:{provider:'claude',model:'claude-opus-5-5'},runs:[{id:'r',state:'running'}],
 messages:[{role:'user',text:'Show the row',created_at:100}],tokens:[]};

test('the trail is the last two finished steps and the live one, from the recorded steps (16.2, mock F)',()=>{
 const task={...base,tokens:[native('Read','frontend/a.ts','delivered',101),native('Grep',null,'delivered',102,{command:'coding_request'}),native('Read','b.ts','delivered',103),native('Edit','src/ConnectedApp.tsx','running',104)]};
 const run=replyRun(task,'r');
 assert.equal(run.model,'Opus 5.5');
 assert.equal(run.startedAt,100,'the clock runs from the message that started it');
 assert.equal(run.live,true);
 assert.deepEqual(run.done.map(step=>[step.text,step.subject]),[['Searched for','coding_request'],['Read','b.ts']]);
 assert.deepEqual([run.now.text,run.now.subject],['Editing','ConnectedApp.tsx']);
 assert.equal(run.steps,3);
});

test('a sub-agent’s steps stay out of the trail; its helpers are one crumb with a dot each, then "2 helpers finished"',()=>{
 const subagents=[{id:'a1',state:'running',called_at:102},{id:'a2',state:'completed',called_at:102.5},{id:'a3',state:'running',parent:'a1',called_at:103},{id:'a4',state:'refused',called_at:103}];
 const task={...base,runs:[{id:'r',state:'running',subagents}],tokens:[native('Read','a.ts','delivered',101),native('Read','inside.ts','running',103,{parent:'a1'})]};
 const live=replyRun(task,'r');
 assert.deepEqual(live.now,{id:'r:helpers',text:'Waiting on 2 helpers',helpers:[false,true]},'only the run’s own helpers, not refused ones or a helper’s helper');
 assert.deepEqual(live.done.map(step=>step.text),['Read'],'the helper’s own read isn’t the run’s step');
 const back={...task,runs:[{id:'r',state:'running',subagents:subagents.map(s=>({...s,state:s.state==='running'?'completed':s.state}))}],tokens:[...task.tokens,native('Edit','x.ts','running',110)]};
 const after=replyRun(back,'r');
 assert.deepEqual(after.done.map(step=>step.text),['Read','2 helpers finished']);
 assert.equal(after.now.text,'Editing');
 // A background helper that has only been launched is still working.
 const launched=replyRun({...task,runs:[{id:'r',state:'running',subagents:[{id:'b',state:'returned',background:true,called_at:102}]}]},'r');
 assert.equal(launched.now.text,'Waiting on 1 helper');
});

test('before the first tool runs the step says only that it is starting or thinking; Codex never has a helpers crumb',()=>{
 assert.equal(replyRun({...base,runs:[{id:'r',state:'checking'}]},'r').now.text,'Starting Claude Code');
 assert.equal(replyRun(base,'r').now.text,'Thinking');
 const codex=replyRun({...base,binding:{provider:'codex',model:'gpt-6-sol'},runs:[{id:'r',state:'checking'}]},'r');
 assert.equal(codex.now.text,'Starting Codex');
 assert.equal(codex.helpers,0);
});

test('a finished run keeps the model and its total time and folds into "6 steps · 2 helpers · 13s"',()=>{
 const task={...base,state:'completed',runs:[{id:'r',state:'completed',ended_at:113}],tokens:[native('Read','a.ts','delivered',101)],messages:[...base.messages,{role:'assistant',text:'Done.',run_id:'r',created_at:112}]};
 const run=replyRun(task,'r');
 assert.equal(run.live,false);assert.equal(run.now,undefined);assert.equal(run.endedAt,113);
 assert.equal(runSummary({steps:6,helpers:2,startedAt:100,endedAt:113}),'6 steps · 2 helpers · 13s');
 assert.equal(runSummary({steps:1,helpers:0,startedAt:100,endedAt:100+125}),'1 step · 2:05');
 assert.deepEqual([elapsed(9.9),elapsed(3725)],['9s','1:02:05']);
 // Presented: the model rides on every reply of the run, the chip only on its last one.
 const rows=[{id:'t:0',author:'You',body:'Show the row',pubkey:'local-user',createdAt:100,depth:0,time:''},{id:'t:1',author:'Claude Code',body:'Done.',pubkey:'provider',createdAt:112,depth:0,time:''}];
 const shown=presentResponses(rows,[],[task],{active:false,state:'idle'});
 assert.equal(shown[1].replyRun.model,'Opus 5.5');assert.equal(shown[1].replyRun.tail,true);
 assert.equal(shown[0].replyRun,undefined,'your own message never gets a run');
});

const user=(id,renderKey,body='Fix it')=>({id,renderKey,author:'You',body,pubkey:'local-user',createdAt:100,depth:0,time:''});
const starting={author:'Claude Code',avatarUrl:null,label:'Starting Claude Code',model:'Opus 5.5'};
test('the reply row appears with your message, in the same render, while the host records it (16.2 item 1)',()=>{
 const handed=new Map();
 const pending=[{id:'sending:1',created_at:100}];
 const shown=instantReplies([user('sending:1','sent:sending:1')],pending,handed,starting);
 assert.equal(shown.length,2);
 const reply=shown[1];
 assert.deepEqual([reply.author,reply.pubkey,reply.body,reply.renderKey],['Claude Code','provider','','reply:sent:sending:1']);
 assert.equal(reply.responsePresentation.label,'Starting Claude Code','it only claims to be starting');
 assert.equal(reply.replyRun.now.text,'Starting Claude Code');assert.equal(reply.replyRun.startedAt,100);
 // The host saved your message but the run hasn't shown yet: the placeholder stays under the saved copy.
 const saved=instantReplies([user('t:0','sent:sending:1')],pending,handed,starting);
 assert.equal(saved[1].renderKey,'reply:sent:sending:1');
 // The real reply arrives: it takes over the placeholder's key, so the row doesn't remount, and keeps it afterwards.
 const real={id:'t:1',renderKey:'response:t:r',author:'Claude Code',body:'',pubkey:'provider',createdAt:101,depth:0,time:'',responsePresentation:{identity:'t:r',label:'Getting started'}};
 const taken=instantReplies([user('t:0','sent:sending:1'),real],[],handed,null);
 assert.deepEqual(taken.map(row=>row.renderKey),['sent:sending:1','reply:sent:sending:1']);
 const later=instantReplies([user('t:0',undefined),{...real,body:'Done',responsePresentation:undefined}],[],handed,null);
 assert.equal(later[1].renderKey,'reply:sent:sending:1','after the sent key expires the reply keeps its key');
});

test('a refused send takes its placeholder with it; a message that joins a running task or queues gets none',()=>{
 const handed=new Map();
 instantReplies([user('sending:2','sent:sending:2')],[{id:'sending:2',created_at:100}],handed,starting);
 // coding_request refused: ConnectedApp drops the pending row, so nothing is left under the draft's message.
 assert.deepEqual(instantReplies([],[],handed,starting),[]);
 // A run is working here (added_to_run or queued): ConnectedApp passes no starting reply.
 assert.equal(instantReplies([user('sending:3','sent:sending:3')],[{id:'sending:3',created_at:100}],handed,null).length,1);
 // A reply already on screen after your message never has its key changed by one that wasn't drawn.
 const shown={id:'t:1',renderKey:'response:t:r',body:'',pubkey:'provider',responsePresentation:{identity:'t:r',label:'Thinking'}};
 assert.equal(instantReplies([user('t:0','sent:sending:3'),shown],[],handed,null)[1].renderKey,'response:t:r');
 // Auto routing may pick the other provider, so the placeholder names nobody.
 const either={...starting,label:'Starting',model:undefined};
 assert.equal(instantReplies([user('sending:4','sent:sending:4')],[{id:'sending:4',created_at:100}],new Map(),either)[1].responsePresentation.label,'Starting');
});

test('Progress says it’s waiting on the helpers while they work, the same words as the trail, and on the provider otherwise',async()=>{
 const {taskFlow}=await import('./TaskFlow.tsx');
 const waiting=task=>taskFlow(task).find(row=>row.id==='r:waiting')?.text;
 const helpers=[{id:'a1',state:'running',called_at:102},{id:'a2',state:'completed',called_at:103}];
 const task={...base,runs:[{id:'r',state:'running',subagents:helpers}],tokens:[native('Read','a.ts','delivered',101)]};
 assert.equal(waiting(task),'Waiting on 2 helpers');
 assert.equal(replyRun(task,'r').now.text,waiting(task));
 // Negative controls: helpers all back, or none at all, and the run is waiting on Claude Code again.
 assert.equal(waiting({...task,runs:[{id:'r',state:'running',subagents:helpers.map(h=>({...h,state:'completed'}))}]}),'Waiting for Claude Code');
 assert.equal(waiting({...task,runs:[{id:'r',state:'running'}]}),'Waiting for Claude Code');
 // A helper's helper or a refused one doesn't count.
 assert.equal(waiting({...task,runs:[{id:'r',state:'running',subagents:[{id:'x',state:'running',parent:'a1'},{id:'y',state:'refused'}]}]}),'Waiting for Claude Code');
});
