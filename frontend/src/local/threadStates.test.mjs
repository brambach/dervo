import assert from 'node:assert/strict';
import {test} from 'node:test';
const {mergeChanges,eventIsNewer,resetThreadStatesForTest}=await import('./threadStates.ts');

const row=(id,version,state='working',projectId='one')=>({id,projectId,name:id,visibility:'shared',archived:false,version,state,step:null,since:null,aliveAt:null,summary:null,goal:null,next:null,request:null,attention:false,unread:0,lost:false,at:null});
const empty={version:0,records:new Map()};

test('a read replaces the rows that moved, drops deleted ones and ignores an answer older than what it has',()=>{
  const first=mergeChanges(empty,{version:3,threads:[row('a',2,'idle'),row('b',3,'working')]});
  assert.equal(first.records.get('b').state,'working');
  const moved=mergeChanges(first,{version:5,threads:[row('b',5,'needs_you'),{id:'a',gone:true}]});
  assert.deepEqual([...moved.records.keys()],['b']);
  assert.equal(moved.records.get('b').state,'needs_you');
  assert.equal(mergeChanges(moved,{version:4,threads:[row('b',4,'working')]}),moved,'a late, older answer changes nothing');
});

test('only a threads event newer than what the window has starts a read',()=>{
  assert.equal(eventIsNewer({kind:'threads',version:6},5),true);
  assert.equal(eventIsNewer({kind:'threads',version:5},5),false);
  assert.equal(eventIsNewer({kind:'other',version:9},5),false);
  assert.equal(eventIsNewer(null,5),false);
});

test('reads ask for what changed since the last one, and pushes that arrive mid-read make one more read, not one each',async()=>{
  const asked=[];let release;
  const answers=[{version:2,threads:[row('a',2)]},{version:4,threads:[row('a',4,'finished')]}];
  const store=resetThreadStatesForTest(since=>{asked.push(since);const answer=answers.shift();return asked.length===1?new Promise(done=>{release=()=>done(answer);}):Promise.resolve(answer);});
  const first=store.refresh();
  void store.refresh();void store.refresh();  // two pushes while the first read is out
  release();await first;
  assert.deepEqual(asked,[0,2]);
  assert.equal(store.get().records.get('a').state,'finished');
});

function fakeWiring(){
  const timers=[];let onEvent=null;let listens=0;
  return {timers,push:payload=>onEvent?.(payload),get listens(){return listens;},
    wiring:{listen:handler=>{listens+=1;onEvent=handler;return Promise.resolve(()=>{onEvent=null;});},
      setTimer:(run,ms)=>{const timer={run,ms};timers.push(timer);return timer;},
      clearTimer:timer=>{const i=timers.indexOf(timer);if(i>=0)timers.splice(i,1);}}};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));

test('one listener however many screens read, a slow safety read once pushes work, and a push starts a read',async()=>{
  const {SAFETY_MS}=await import('./threadStates.ts');
  const fake=fakeWiring();const asked=[];
  const store=resetThreadStatesForTest(since=>{asked.push(since);return Promise.resolve({version:since+1,threads:[row('a',since+1)]});},fake.wiring);
  const offA=store.subscribe(()=>{});const offB=store.subscribe(()=>{});
  await settle();await settle();
  assert.equal(fake.listens,1);
  assert.deepEqual(fake.timers.map(t=>t.ms),[SAFETY_MS]);
  fake.push({kind:'threads',version:5});await settle();
  assert.deepEqual(asked,[0,1]);
  offA();offB();
  assert.equal(fake.timers.length,0,'nothing left running once nobody reads');
});

test('a failed read is tried again soon, backing off, and a host on a fresh store is read from the start',async()=>{
  const {RETRY_MS}=await import('./threadStates.ts');
  const fake=fakeWiring();const asked=[];
  const answers=[()=>Promise.reject(new Error('busy')),()=>Promise.reject(new Error('busy')),()=>Promise.resolve({version:7,threads:[row('a',7)]}),
    ()=>Promise.resolve({version:2,threads:[]}),()=>Promise.resolve({version:2,threads:[row('b',2)]})];
  const store=resetThreadStatesForTest(since=>{asked.push(since);return answers.shift()();},fake.wiring);
  const off=store.subscribe(()=>{});
  await settle();await settle();
  assert.equal(fake.timers.at(-1).ms,RETRY_MS);
  fake.timers.at(-1).run();await settle();await settle();
  assert.equal(fake.timers.at(-1).ms,RETRY_MS*2,'backs off');
  fake.timers.at(-1).run();await settle();await settle();
  assert.equal(store.get().version,7);
  await store.refresh();  // the host restarted on another store: its newest number is lower
  assert.deepEqual(asked,[0,0,0,7,0]);
  assert.deepEqual([...store.get().records.keys()],['b']);
  off();
});

test('a subscribe, unsubscribe and subscribe before listening starts leaves one listener, not two',async()=>{
  let live=0;const timers=[];
  const wiring={listen:()=>{live+=1;return new Promise(resolve=>setTimeout(()=>resolve(()=>{live-=1;}),0));},
    setTimer:(run,ms)=>{const t={run,ms};timers.push(t);return t;},clearTimer:t=>{const i=timers.indexOf(t);if(i>=0)timers.splice(i,1);}};
  const store=resetThreadStatesForTest(()=>Promise.resolve({version:1,threads:[]}),wiring);
  const off=store.subscribe(()=>{});off();
  const off2=store.subscribe(()=>{});
  await new Promise(resolve=>setTimeout(resolve,5));
  assert.equal(live,1);
  off2();assert.equal(live,0);
});
