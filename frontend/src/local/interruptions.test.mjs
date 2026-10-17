import assert from 'node:assert/strict';
import {test} from 'node:test';
const {interruptionLabel,steeredMessage}=await import('./interruptions.ts');

const task=(extra={})=>({state:'working',binding:{provider:'claude'},runs:[{id:'r2',state:'running'}],
 messages:[{role:'user',text:'Fix the header',created_at:1},{role:'assistant',text:'On it.',created_at:2,run_id:'r2'}],...extra});
const steered=(steer,run_id='r2')=>task({messages:[...task().messages,{role:'user',text:'Also the footer',created_at:3,run_id,steer}]});

test('a message that joined the run says so, then that the agent read it; one that waits says it is queued',()=>{
 assert.deepEqual(interruptionLabel(steered('sent'),[]),{kind:'steered',text:'Added to the run · Claude Code reads it at its next step',messageIndex:2});
 assert.deepEqual(interruptionLabel(steered('read'),[]),{kind:'read',text:'Claude Code read your message and is working with it',messageIndex:2});
 assert.deepEqual(interruptionLabel(task(),[{state:'queued',position:1}]),{kind:'queued',text:'Queued · runs when this task ends'});
 assert.deepEqual(interruptionLabel(task(),[{state:'queued',position:1},{state:'queued',position:2}]),{kind:'queued',text:'2 queued · they run when this task ends'});
 assert.equal(interruptionLabel(task({binding:{provider:'codex'}}),[]),null,'an ordinary request: nothing to say');
});

test('negative controls: no run, an earlier run, a failed queue item, or no steer record say nothing',()=>{
 assert.equal(interruptionLabel(undefined,[{state:'queued',position:1}]),null,'nothing runs: the queue footer speaks for itself');
 assert.equal(interruptionLabel(steered('sent',{state:'completed'}.state&&'r2'),[]).kind,'steered');
 assert.equal(interruptionLabel(task({...steered('sent'),state:'completed'}),[]),null,'the task finished');
 assert.equal(interruptionLabel(steered('read','r1'),[]),null,'a steer from an earlier run of the same task');
 assert.equal(interruptionLabel(task(),[{state:'failed',position:null}]),null,'a queue item that could not start has its own row');
 assert.equal(steeredMessage(task({messages:[...task().messages,{role:'user',text:'Plain follow-up',created_at:3}]})),null,'a user message without a steer record');
 assert.equal(steeredMessage(task({runs:[]})),null);
});
