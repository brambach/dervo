import assert from 'node:assert/strict';
import {test} from 'node:test';
import {presentResponses,plainDashes} from './responsePresentation.ts';
const status={active:true,state:'working',detail:''};
const request={id:'t:0',author:'You',body:'request',pubkey:'local-user',createdAt:1,depth:0,time:'now'};
const task={id:'t',state:'working',binding:{provider:'codex'},runs:[{id:'r',state:'running'}],messages:[{role:'user',text:'request',created_at:1}]};
test('first coding response replaces the presentation slot with a stable render key',()=>{
 const waiting=presentResponses([request],[],[task],status);assert.equal(waiting.length,2);assert.equal(waiting[1].body,'');assert.equal(waiting[1].author,'Codex');
 const reply={...request,id:'t:1',pubkey:'provider',body:'actual text'};
 const active=presentResponses([request,reply],[],[{...task,messages:[...task.messages,{role:'assistant',text:'actual text',run_id:'r'}]}],status);
 assert.equal(active[1].renderKey,waiting[1].renderKey);assert.equal(active[1].body,'actual text');assert.equal(request.responsePresentation,undefined);
});
test('completed, stopping, reviewing and uncertain activity never animates or invents a row',()=>{
 for(const changed of [{active:false},{state:'blocked'},{review_active:true},{outcome_uncertain:true},{interrupted:true}])assert.deepEqual(presentResponses([request],[],[task],{...status,...changed}),[request]);
 for(const state of ['stopping','completed','failed','uncertain'])assert.deepEqual(presentResponses([request],[],[{...task,runs:[{id:'r',state}]}],status),[request]);
});
test('only current-run latest assistant text is paced, not prior runs or reviewer output',()=>{
 const messages=[task.messages[0],{role:'assistant',run_id:'old'},{role:'user'},{role:'assistant',run_id:'r'},{role:'assistant',run_id:'r'}];
 const rows=messages.map((_,i)=>({...request,id:`t:${i}`,body:`body${i}`}));
 const shown=presentResponses(rows,[],[{...task,messages}],status);
 assert.deepEqual(shown.filter(row=>row.responsePresentation).map(row=>row.id),['t:4']);
});
test('plain chat presentation follows the actual request and stops immediately with it',()=>{
 const user={id:'u',pubkey:'local-user',state:'running'},reply={id:'u:item',pubkey:'provider',state:'streaming'};
 const rows=[{...request,id:'u'},{...request,id:'u:item',body:'answer'}];
 assert.ok(presentResponses(rows,[user,reply],[],status)[1].responsePresentation);
 assert.equal(presentResponses(rows,[{...user,state:'stopping'},reply],[],status)[1].responsePresentation,undefined);
 const waiting=presentResponses([rows[0]],[user],[],status);
 assert.equal(waiting[1].renderKey,presentResponses(rows,[user,reply],[],status)[1].renderKey);
});

test('completion preserves the real row key and an older resumed task keeps its slot',()=>{
 const reply={...request,id:'t:1',body:'actual'},liveTask={...task,messages:[...task.messages,{role:'assistant',run_id:'r'}]};
 const live=presentResponses([request,reply],[],[liveTask],status);
 const done=presentResponses([request,reply],[],[{...liveTask,state:'completed'}],{...status,active:false});
 assert.equal(live[1].renderKey,done[1].renderKey);
 const other={...request,id:'other:0'};
 assert.deepEqual(presentResponses([request,other],[],[task],status).map(row=>row.id),['t:0','t:1','other:0']);
});
test('a second raw-chat item never shares the first reply render key',()=>{
 const history=[{id:'u',pubkey:'local-user',state:'running'},{id:'u:a',pubkey:'provider',state:'answer'},{id:'u:b',pubkey:'provider',state:'streaming'}];
 const rows=history.map(row=>({...request,...row,body:'text'}));
 const shown=presentResponses(rows,history,[],status);
 assert.equal(new Set(shown.map(row=>row.renderKey??row.id)).size,3);
 assert.equal(shown[2].responsePresentation?.identity,'u');
});

test('an agent’s em dash displays as a spaced hyphen; your own words and the stored text are untouched',()=>{
 assert.equal(plainDashes('Ideas are stored per project—modelled on knowledge.'),'Ideas are stored per project - modelled on knowledge.');
 assert.equal(plainDashes('a — b'),'a - b');
 const reply={...request,id:'t:1',pubkey:'provider',body:'Done—tests pass.'};
 const yours={...request,body:'Try it—please.'};
 const shown=presentResponses([yours,reply],[],[],undefined);
 assert.equal(shown[1].body,'Done - tests pass.');
 assert.equal(shown[0].body,'Try it—please.','your own message keeps its dash');
 assert.equal(reply.body,'Done—tests pass.','display only: the stored row is unchanged');
});

test('after a handoff, the request the new task repeats is not shown twice; a different request is',()=>{
 const stopped={...task,id:'old',state:'blocked',messages:[{role:'user',text:'Pick connectors',created_at:1}]};
 const retried={...task,id:'new',state:'working',route:{retry_of:'old'},messages:[{role:'user',text:'Pick connectors',created_at:2},{role:'assistant',text:'Starting with the tests.',run_id:'r',created_at:3}]};
 const rows=[{...request,id:'old:0',body:'Pick connectors'},{...request,id:'new:0',body:'Pick connectors'},{...request,id:'new:1',pubkey:'provider',body:'Starting with the tests.'}];
 assert.deepEqual(presentResponses(rows,[],[stopped,retried],undefined).map(row=>row.id),['old:0','new:1']);
 // Negative controls: no retry_of, or different words, and both requests stay.
 assert.deepEqual(presentResponses(rows,[],[stopped,{...retried,route:undefined}],undefined).map(row=>row.id),['old:0','new:0','new:1']);
 assert.deepEqual(presentResponses(rows,[],[stopped,{...retried,messages:[{role:'user',text:'Pick connectors, but only three',created_at:2}]}],undefined).map(row=>row.id),['old:0','new:0','new:1']);
});

test('after a handoff each reply carries the provider that wrote it, from the task record, so the byline changes where the provider did',()=>{
 const claude={...task,id:'old',state:'blocked',binding:{provider:'claude'},messages:[{role:'user',text:'Pick connectors',created_at:1},{role:'assistant',text:'Each launch allows the ticked connectors.',run_id:'a',created_at:2}]};
 const codex={...task,id:'new',state:'working',binding:{provider:'codex'},route:{retry_of:'old'},messages:[{role:'user',text:'Pick connectors',created_at:3},{role:'assistant',text:'Starting with the tests.',run_id:'r',created_at:4}]};
 const rows=[{...request,id:'old:0',body:'Pick connectors'},{...request,id:'old:1',pubkey:'provider',author:'Codex',body:'Each launch allows the ticked connectors.'},{...request,id:'new:0',body:'Pick connectors'},{...request,id:'new:1',pubkey:'provider',author:'Codex',body:'Starting with the tests.'}];
 const shown=presentResponses(rows,[],[claude,codex],undefined,'codex');
 assert.deepEqual(shown.map(row=>[row.id,row.author,row.pubkey]),[['old:0','You','local-user'],['old:1','Claude Code','provider:claude'],['new:1','Codex','provider']]);
 assert.ok(shown[1].avatarUrl.startsWith('data:image/svg+xml'));
 // Negative control: with one provider throughout nothing is renamed.
 assert.deepEqual(presentResponses(rows,[],[{...claude,binding:{provider:'codex'}},codex],undefined,'codex').map(row=>row.author),['You','Codex','Codex']);
});
