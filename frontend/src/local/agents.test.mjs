import assert from 'node:assert/strict';
import {test} from 'node:test';
import {agentsNamed,agentMentionRows,mentionStateLine,SETTLED_MENTION} from './agents.ts';

const agents=[{handle:'reviewer'},{handle:'catch-up'}];

test('only this project’s agents count, not emails, code or repeats',()=>{
  assert.deepEqual(agentsNamed('@Reviewer look, then @catch-up and @reviewer again',agents),['reviewer','catch-up']);
  assert.deepEqual(agentsNamed('mail bob@reviewer.dev or see `@reviewer` and @someone',agents),[]);
});

test('one ask naming two agents is one row of yours and one answer row each',()=>{
  const base={from_thread:'c',agent_name:'',channel_id:'x',asked:'@reviewer @catch-up look',task_id:null,detail:null,answer:null,created_at:100,answered_at:null};
  const rows=agentMentionRows([
    {...base,id:'1',handle:'reviewer',client_id:'k',state:'answered',answer:'Looks fine.',detail:'Its changes wait for your review in @reviewer.',answered_at:120},
    {...base,id:'2',handle:'catch-up',client_id:'k',state:'unknown',detail:'Its run record is gone.'},
  ],'c');
  assert.equal(rows.length,3);
  assert.equal(rows.filter(row=>row.pubkey==='local-user').length,1);
  // An answer whose changes still wait for you says so, in the thread too.
  assert.match(rows[1].content,/Looks fine\.\n\nIts changes wait for your review in @reviewer\./);
  assert.equal(rows[2].pubkey,'agent:catch-up');
  assert.equal(rows[2].content,mentionStateLine({...base,state:'unknown',detail:'Its run record is gone.'}));
  assert.ok(SETTLED_MENTION.includes('unknown'));
});
