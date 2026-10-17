import assert from 'node:assert/strict';
import {test} from 'node:test';
const {initialVoice,voiceReducer,sent,spokenReceipt,replyTo,setupLine}=await import('./voiceState.ts');

// Voice on Home (8 Oct): the voice process decides; the window shows it, gives Undo and Yes/No, and sends in order.
const run=(events,start=initialVoice(),now=1000)=>events.reduce((view,event)=>voiceReducer(view,event,now),start);

test('a heard request shows with its undo window, then moves to the outbox when released',()=>{
 let view=run([{type:'ready',mode:'push_to_talk'},{type:'utterance',id:'u1',text:'Fix the export crash',undo_seconds:3}]);
 assert.deepEqual(view.heard,[{id:'u1',text:'Fix the export crash',until:4000,replay:false}]);
 assert.deepEqual(view.outbox,[]);
 view=voiceReducer(view,{type:'send',id:'u1',text:'Fix the export crash'});
 assert.deepEqual(view.heard,[]);
 assert.deepEqual(view.outbox,[{id:'u1',text:'Fix the export crash'}]);
 assert.deepEqual(sent(view,'u1').outbox,[]);
});

test('requests said back to back go out in the order you said them, and a replayed send is not doubled',()=>{
 const view=run([{type:'send',id:'a',text:'one'},{type:'send',id:'b',text:'two'},{type:'send',id:'a',text:'one',replay:true}]);
 assert.deepEqual(view.outbox.map(item=>item.id),['a','b']);
});

test('undo and a spoken never mind take it off the screen; a timed-out yes says so',()=>{
 let view=run([{type:'utterance',id:'u1',text:'x'},{type:'cancelled',id:'u1',reason:'click'}]);
 assert.deepEqual(view.heard,[]);
 view=run([{type:'confirm',id:'u2',text:'Push the export branch',what:'pushes code',readback:'That pushes code. Say yes to go ahead.',seconds:8}]);
 assert.equal(view.confirm.until,9000);
 view=voiceReducer(view,{type:'cancelled',id:'u2',reason:'timeout'});
 assert.equal(view.confirm,null);
 assert.match(view.note,/didn’t hear a yes/);
});

test('an outward request leaves the confirm card once it is confirmed and sent',()=>{
 const view=run([{type:'confirm',id:'u3',text:'Merge it',what:'merges a branch',readback:'r'},{type:'send',id:'u3',text:'Merge it'},{type:'confirmed',id:'u3'}]);
 assert.equal(view.confirm,null);
 assert.deepEqual(view.outbox.map(item=>item.id),['u3']);
});

test('a crash restarting and a lost microphone are said, never shown as listening',()=>{
 let view=run([{type:'ready'},{type:'state',state:'listening'},{type:'supervisor',state:'restarting',code:3}]);
 assert.equal(view.process,'restarting');
 assert.equal(view.state,'off');
 view=voiceReducer(view,{type:'audio',audio:'denied'});
 assert.match(view.note,/microphone/);
});

test('the model download reports progress and failure in words',()=>{
 let view=run([{type:'unavailable',reason:'models'},{type:'setup',name:'asr',state:'downloading',bytes:50,of:200}]);
 assert.equal(setupLine(view.setup),'Downloading voice, part 4 of 5, 25%');
 view=voiceReducer(view,{type:'setup',name:'asr',state:'failed',detail:'checksum'});
 assert.match(setupLine(view.setup),/didn’t finish: checksum/);
 view=voiceReducer(view,{type:'setup',state:'done'});
 assert.equal(view.unavailable,null);
});

test('the spoken receipt names where it went, and waits for the reply to be ready',()=>{
 assert.equal(spokenReceipt({id:'r',kind:'reply',created_at:1,state:'thinking'}),null);
 const reply={id:'r',kind:'reply',created_at:1,state:'ready',text:'**Started** it.',parts:[
  {index:0,action:'start',text:'x',conversation:{id:'c1',name:'Export fix'},idea:null},
  {index:1,action:'add',text:'y',conversation:{id:'c2',name:'Auth'},idea:null}]};
 assert.equal(spokenReceipt(reply),'Started Export fix. Sent to Auth.');
 assert.equal(spokenReceipt({...reply,parts:[]}),'Started it.');
 assert.match(spokenReceipt({...reply,state:'failed'}),/didn’t go through/);
});

test('the reply to a spoken request is found by its words and time, not by guessing the newest',()=>{
 const entries=[
  {id:'y0',kind:'you',created_at:5,text:'Fix the export crash',request_id:'q0'},
  {id:'r0',kind:'reply',created_at:6,state:'ready',request_id:'q0',text:'old'},
  {id:'y1',kind:'you',created_at:100,text:'Fix the export crash',request_id:'q1'},
  {id:'r1',kind:'reply',created_at:101,state:'ready',request_id:'q1',text:'new'}];
 assert.equal(replyTo(entries,'Fix the export crash',99).id,'r1');
 assert.equal(replyTo(entries,'Something else',99),undefined);
});

test('after a window reload, status catches up: voice shows on, and unconfirmed sends are queued again once',()=>{
 const view=run([{type:'send',id:'a',text:'one'},{type:'status',state:'idle',mode:'wake',speaking:false,pending:[{id:'p',text:'later'}],
  released:[{id:'a',text:'one'},{id:'b',text:'two',scope:'proj:work'}]}]);
 assert.equal(view.on,true);
 assert.equal(view.process,'running');
 assert.equal(view.mode,'wake');
 assert.deepEqual(view.outbox.map(item=>item.id),['a','b']);
 assert.equal(view.outbox[1].scope,'proj:work');
 assert.deepEqual(view.heard.map(item=>item.id),['p']);
});

test('a send keeps the project it was heard in; a refused one says so',()=>{
 let view=run([{type:'send',id:'a',text:'one',scope:'proj:work'}]);
 assert.equal(view.outbox[0].scope,'proj:work');
 view=voiceReducer(view,{type:'send_failed',id:'a',detail:'Signed out'});
 assert.match(view.note,/didn’t reach Home: Signed out/);
});

test('giving up after repeated crashes keeps saying it failed; a plain stop clears everything',()=>{
 let view=run([{type:'ready'},{type:'supervisor',state:'failed'},{type:'supervisor',state:'stopped'}],{...initialVoice(),on:true});
 assert.equal(view.process,'failed');
 view=run([{type:'ready'},{type:'utterance',id:'u',text:'x'},{type:'supervisor',state:'stopped'}],{...initialVoice(),on:true});
 assert.equal(view.on,false);
 assert.deepEqual(view.heard,[]);
});
