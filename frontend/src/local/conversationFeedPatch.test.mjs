import {test} from 'node:test';
import assert from 'node:assert/strict';
import {applyConversationFeed,carryOlder,addOlderPage,withOlder} from './conversationFeedPatch.ts';
const a='a'.repeat(32),b='b'.repeat(32);
function state(){return {cursor:a,active:true,snapshot:{activity:{active:true,state:'working'},history:[{id:'one',content:'Earlier reply'},{id:'two',content:'🌱 hello'}],tasks:[],requests:[]}};}
const delta=patches=>({cursor:b,base:a,active:true,reset:false,patches});
test('append uses UTF-16 offsets and preserves earlier records and other collections',()=>{
 const before=state();const after=applyConversationFeed(before,delta([{op:'append',path:['history',1,'content'],length:8,text:' world'}]));
 assert.equal(after.snapshot.history[1].content,'🌱 hello world');assert.equal(before.snapshot.history[1].content,'🌱 hello');
 assert.equal(after.snapshot.history[0],before.snapshot.history[0]);assert.equal(after.snapshot.tasks,before.snapshot.tasks);
});
test('empty changes preserve the complete snapshot reference',()=>{const before=state();assert.equal(applyConversationFeed(before,delta([])).snapshot,before.snapshot);});
test('terminal text and status changes appear in one returned snapshot',()=>{
 const next=applyConversationFeed(state(),{...delta([{op:'set',path:['history',1,'content'],value:'Final answer'},{op:'set',path:['activity'],value:{active:false,state:'completed'}}]),active:false});
 assert.equal(next.active,false);assert.equal(next.snapshot.activity.state,'completed');assert.equal(next.snapshot.history[1].content,'Final answer');
});
test('stale or out-of-order cursors and wrong append positions are rejected',()=>{
 const before=state();assert.throws(()=>applyConversationFeed(before,{...delta([]),base:b}));
 assert.throws(()=>applyConversationFeed(undefined,delta([])));
 assert.throws(()=>applyConversationFeed(before,delta([{op:'append',path:['history',1,'content'],length:7,text:'bad'}])));
});
test('prototype paths, unknown indexes and too many patches never mutate the source',()=>{
 for(const path of [['__proto__','polluted'],['history',9],['activity','constructor','prototype','polluted']])assert.throws(()=>applyConversationFeed(state(),delta([{op:'set',path,value:true}])));
 assert.equal({}.polluted,undefined);
 assert.throws(()=>applyConversationFeed(state(),delta(Array(2049).fill({op:'set',path:['activity'],value:{}}))));
});
test('explicit reset accepts a fresh authoritative snapshot and rejects malformed shape',()=>{
 const before=state();assert.equal(applyConversationFeed(undefined,{cursor:a,base:null,active:true,reset:true,snapshot:before.snapshot}).snapshot,before.snapshot);
 assert.throws(()=>applyConversationFeed(before,{cursor:b,base:a,active:false,reset:true,snapshot:{}}));
});

test('unchanged cursor and content retain whole state reference',()=>{const before=state();assert.equal(applyConversationFeed(before,{...delta([]),cursor:a}),before);});
test('malformed activity and collection records are rejected',()=>{
 for(const bad of [{activity:[]},{activity:{state:'working'}},{history:[null]},{tasks:[{}]},{requests:[[]]}])assert.throws(()=>applyConversationFeed(undefined,{cursor:a,base:null,active:true,reset:true,snapshot:{...state().snapshot,...bad}}));
});
test('a queue collection is optional, and a broken queue row is rejected like any other collection',()=>{
 const snapshot={activity:{active:true,state:'working'},history:[],tasks:[],requests:[],queue:[{id:'q1',state:'queued',text:'Then fix the greeting'}]};
 const next=applyConversationFeed(undefined,{cursor:a,base:null,active:true,reset:true,snapshot});
 assert.equal(next.snapshot.queue[0].text,'Then fix the greeting');
 assert.throws(()=>applyConversationFeed(undefined,{cursor:a,base:null,active:true,reset:true,snapshot:{...snapshot,queue:[{text:'no id'}]}}));
 assert.throws(()=>applyConversationFeed(undefined,{cursor:a,base:null,active:true,reset:true,snapshot:{...snapshot,queue:{}}}));
 assert.equal(applyConversationFeed(undefined,{cursor:a,base:null,active:true,reset:true,snapshot:{...snapshot,queue:undefined}}).snapshot.queue,undefined);
});

// 16.4 item 1: the feed carries the newest page; older pages join it with no gap and no repeat.
const row=(n)=>({id:`m${n}`,content:`Message ${n}`,created_at:n,state:'completed'});
const job=(n,state='completed')=>({id:`t${n}`,state,messages:[{role:'user',text:'Ask',created_at:n}]});
const snap=(from,to,more,extra={})=>({activity:{active:false,state:'completed'},history:Array.from({length:to-from},(_,i)=>row(from+i)),tasks:[],requests:[],page:{before:from,has_older:more},...extra});
const ids=(rows)=>rows.map(item=>item.id);
test('a page field is optional and a malformed one is rejected',()=>{
 assert.equal(applyConversationFeed(undefined,{cursor:a,base:null,active:false,reset:true,snapshot:snap(10,12,true)}).snapshot.page.before,10);
 for(const page of [[],{before:'10',has_older:true},{before:10},{before:Infinity,has_older:false}])
  assert.throws(()=>applyConversationFeed(undefined,{cursor:a,base:null,active:false,reset:true,snapshot:{...snap(10,12,true),page}}));
});
test('older pages load in order before what is shown, with no gap or repeat, and a late page is ignored',()=>{
 let older=carryOlder(undefined,undefined,snap(20,30,true));
 assert.deepEqual([older.before,older.hasOlder,older.top],[20,true,20]);
 older=addOlderPage(older,20,{history:[row(10),row(11)],tasks:[job(12)],before:10,has_older:true});
 assert.equal(addOlderPage(older,20,{history:[row(19)],tasks:[],before:19,has_older:true}),older,'a page for a position already passed');
 older=addOlderPage(older,10,{history:[row(0),row(5)],tasks:[],before:0,has_older:false});
 assert.equal(older.hasOlder,false);
 const shown=withOlder(snap(20,30,true),older);
 assert.deepEqual(ids(shown.history),['m0','m5','m10','m11',...Array.from({length:10},(_,i)=>`m${20+i}`)]);
 assert.deepEqual(ids(shown.tasks),['t12']);
 assert.throws(()=>addOlderPage(older,0,{history:[],tasks:[],before:5,has_older:true}),'a page must end before the position asked for');
});
test('rows that slide out of the newest page move into older pages, so nothing on screen disappears',()=>{
 const first=snap(20,30,true);
 let older=carryOlder(undefined,undefined,first);
 older=addOlderPage(older,20,{history:[row(15)],tasks:[],before:15,has_older:true});
 const next=snap(25,32,true);
 older=carryOlder(older,first,next);
 assert.deepEqual([older.before,older.top],[15,25]);
 assert.deepEqual(ids(withOlder(next,older).history),['m15',...Array.from({length:17},(_,i)=>`m${20+i}`).filter(id=>Number(id.slice(1))<32)]);
 // Without the previous snapshot the rows in between are unknown, so loaded pages are dropped rather than left with a gap.
 const fresh=carryOlder(older,undefined,snap(28,32,true));
 assert.deepEqual([fresh.history.length,fresh.before,fresh.top,fresh.hasOlder],[0,28,28,true]);
});
test('a newest page that reaches further back keeps only what it does not carry',()=>{
 const first=snap(20,30,true);
 let older=addOlderPage(carryOlder(undefined,undefined,first),20,{history:[row(10),row(15)],tasks:[],before:10,has_older:true});
 older=carryOlder(older,first,snap(15,30,true));
 assert.deepEqual(ids(older.history),['m10']);assert.equal(older.top,15);
 assert.equal(carryOlder(older,snap(15,30,true),snap(5,30,false)).history.length,0);
});
test('a task in the snapshot is its newest copy, and an unchanged read keeps the same older pages',()=>{
 const first={...snap(20,30,true),tasks:[job(5,'working')]};
 let older=addOlderPage(carryOlder(undefined,undefined,first),20,{history:[],tasks:[job(5,'working'),job(8)],before:5,has_older:false});
 const steady=carryOlder(older,first,first);assert.deepEqual(ids(steady.tasks),['t8']);
 assert.equal(carryOlder(steady,first,first),steady,'after that, unchanged reads keep the same object');
 const shown=withOlder(first,older);
 assert.deepEqual(ids(shown.tasks),['t5','t8']);assert.equal(shown.tasks[0],first.tasks[0]);
 const settled={...first,tasks:[job(5,'completed')]};
 older=carryOlder(older,first,settled);
 assert.deepEqual(ids(older.tasks),['t8']);
 assert.equal(withOlder(settled,older).tasks[0].state,'completed');
 assert.equal(withOlder(first,undefined).history,first.history,'nothing loaded keeps the snapshot rows as they are');
});
