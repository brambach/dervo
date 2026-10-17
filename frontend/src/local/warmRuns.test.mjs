import test from 'node:test';
import assert from 'node:assert/strict';
import {warmSoon,resetWarmForTest,WARM_ASK_MS} from './warmRuns.ts';

test('typing asks for a warm start once a minute while you stay in a thread, and a failed ask is quiet',async()=>{
  resetWarmForTest();
  const asks=[];
  const ask=args=>{asks.push(args);return Promise.reject(new Error('busy'));};
  assert.equal(warmSoon('p','a',1000,ask),true);
  assert.equal(warmSoon('p','a',2000,ask),false,'the next keystrokes ask nothing');
  assert.equal(warmSoon('p','b',2000,ask),true,'another thread asks for its own');
  assert.equal(warmSoon('p','a',3000,ask),true,'coming back asks again: the host held only the other one');
  assert.equal(warmSoon('p','a',4000,ask),false);
  assert.equal(warmSoon('p','a',3000+WARM_ASK_MS,ask),true);
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(asks.map(a=>a.channelId),['a','b','a','a']);
});
