import test from 'node:test';
import assert from 'node:assert/strict';
const {isStopping,markStopping}=await import('./stopping.ts');

test('a Stop is spent once the task stops, so a resumed run on the same task is not read as pausing',()=>{
 const working={id:'t1',state:'working',runs:[{state:'running'}]};
 markStopping([working]);
 assert.equal(isStopping(working),true,'pausing while the stopped run ends');
 assert.equal(isStopping({id:'t1',state:'blocked',runs:[{state:'interrupted'}]}),false);
 assert.equal(isStopping({...working,runs:[{state:'interrupted'},{state:'running'}]}),false,'Resume: the same task working again');
 assert.equal(isStopping({id:'t2',state:'working',runs:[{state:'stopping'}]}),true,'the host saying so still counts');
});

test('a Pause from Home that nobody watched is spent once the task runs again (reviewer, 8 Oct)',()=>{
 // Paused from Home, resumed from catch-up: no read of the task while it was stopped.
 markStopping([{id:'t3',state:'working',runs:[{state:'running'}]}]);
 assert.equal(isStopping({id:'t3',state:'working',runs:[{state:'interrupted'},{state:'running'}]}),false);
 assert.equal(isStopping({id:'t3',state:'working',runs:[{state:'running'}]}),true,'the run the Pause was for is still ending');
});

test('Pause, Resume, Pause: the second Pause reads as pausing too (review of #19)',()=>{
 markStopping([{id:'t4',state:'working',runs:[{state:'running'}]}]);
 const resumed={id:'t4',state:'working',runs:[{state:'interrupted'},{state:'running'}]};
 assert.equal(isStopping(resumed),false,'the first Pause is spent');
 markStopping([resumed]);
 assert.equal(isStopping(resumed),true,'the second Pause counts');
});

test('a Pause marked without its task row holds until the task stops (review of #19)',()=>{
 markStopping([{id:'t5'}]);
 assert.equal(isStopping({id:'t5',state:'working',runs:[{state:'running'}]}),true);
 assert.equal(isStopping({id:'t5',state:'blocked',runs:[{state:'interrupted'}]}),false);
});

test('a Pause marked without its task row is spent once the task runs again (review of #19)',()=>{
 markStopping([{id:'t6'}]);
 assert.equal(isStopping({id:'t6',state:'working',runs:[{state:'running'}]}),true,'learns the run being paused');
 assert.equal(isStopping({id:'t6',state:'working',runs:[{state:'interrupted'},{state:'running'}]}),false,'the resumed run is not pausing');
});
