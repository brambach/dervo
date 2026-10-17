import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
// Bounded acceptance of the browser fixture itself, without a browser: the same host process, the same real broker,
// evidence, file writes and confined tests, the same synthetic three-role peers, driven through the exact operations the
// frontend calls. It proves the contract the browser test relies on and leaves JSON evidence per step. Run with:
//   /usr/local/bin/node --import ./test-loader.mjs --experimental-strip-types --test collaboration-fixture.test.mjs
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const evidence=path.join(root,'evidence/collaboration-ui');
const SAMPLE='import unittest\nfrom greeting import greet\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertEqual(greet(), "hi")\n';
const GREETING='def greet(): return "hi"\n';
const sha=text=>createHash('sha256').update(text).digest('hex');

test('three roles across three channels through the fixture: atomic agent conversations, explicit policy, packets, refusals, stop, restart without replay',async()=>{
 const data=await mkdtemp(path.join(tmpdir(),'dervo-collaboration-fixture-'));
 const target=path.join(data,'target');
 let worker,lines,pending=[];
 const startHost=()=>{worker=spawn('/usr/local/bin/python3',['-E','-s','-B',path.join(root,'frontend/collaboration-host-fixture.py'),data,target],{env:{},stdio:['pipe','pipe','inherit']});lines=createInterface({input:worker.stdout});lines.on('line',line=>{const next=pending.shift(),r=JSON.parse(line);r.error?next.reject(new Error(r.error)):next.resolve(r.result);});};
 const call=(operation,args={})=>new Promise((resolve,reject)=>{pending.push({resolve,reject});worker.stdin.write(JSON.stringify({operation,args})+'\n');});
 const stopHost=async()=>{const exit=new Promise(resolve=>worker.once('exit',resolve));worker.stdin.end();await exit;lines.close();};
 const until=async(fn,timeout=40000)=>{const deadline=Date.now()+timeout;for(;;){const value=await fn();if(value)return value;if(Date.now()>deadline)throw new Error('timed out');await new Promise(r=>setTimeout(r,100));}};
 const record=(step,facts)=>writeFile(path.join(evidence,`fixture-${step}.json`),JSON.stringify({providerEvidence:'Synthetic Codex and Claude protocol peers (collaboration-host-fixture.py), not live providers',verification:'Actual host-confined subprocesses and real packet records',...facts},null,2));
 startHost();
 try{
  await mkdir(evidence,{recursive:true});await mkdir(target);
  await writeFile(path.join(target,'test_sample.py'),SAMPLE);await writeFile(path.join(target,'greeting.py'),GREETING);
  await writeFile(path.join(target,'notes.py'),'# owner only\n');await writeFile(path.join(target,'secret_notes.py'),'# never in any scope\n');
  const owner=await call('add_profile',{provider:'codex',identity:'synthetic@example.test',category:'personal'});
  const cx=await call('add_profile',{provider:'codex',identity:'synthetic@example.test',category:'personal'});
  const cl=await call('add_profile',{provider:'claude',identity:'personal@example.test',category:'personal'});
  const work=await call('add_profile',{provider:'codex',identity:'work@example.test',category:'work'});
  const project=await call('_picked_project',{path:target,profile_id:owner.id,model:'synthetic-model'});
  const channel=await call('create_conversation',{projectId:project.id,name:'Engineering',private:false});
  const priv=await call('create_conversation',{projectId:project.id,name:'Notes',private:true});
  const scope={projectId:project.id,channelId:channel.id};
  await call('harness_boundary',{profileIds:[owner.id,cx.id,cl.id]});
  await call('coding_permission_grant',{...scope,files:['greeting.py','notes.py','test_sample.py'],testFile:'test_sample.py'});
  // Settings exactly as the dialog calls them: atomic conversations on the other accounts, agents, then an explicit policy.
  const review=await call('create_agent_conversation',{projectId:project.id,profileId:cx.id,model:'synthetic-model',name:'Review room'});
  const workRoom=await call('create_agent_conversation',{projectId:project.id,profileId:cl.id,model:'synthetic-model',name:'Work room'});
  await assert.rejects(call('create_agent_conversation',{projectId:project.id,profileId:work.id,model:'synthetic-model',name:'Work account room'}),/Work account/);
  assert.equal((await call('projects'))[0].profile_id,owner.id,'the project binding is untouched');
  await call('collab_agent_save',{name:'reviewer',profileId:cx.id,channelId:review.id,projectId:project.id,role:'reviewer',instructions:'Review for correctness. Point at exact lines.'});
  await call('collab_agent_save',{name:'fixer',profileId:cl.id,channelId:workRoom.id,projectId:project.id,role:'worker',instructions:'Make the smallest change that makes the tests pass.'});
  assert.equal((await call('collab_policy',scope)).state,'off','agents alone enable nothing');
  const policy=await call('collab_policy_set',{...scope,mode:'automatic',agents:['reviewer','fixer'],channels:[channel.id],maxParallel:3,maxDepth:1,maxCalls:6});
  assert.deepEqual([policy.state,policy.available.map(a=>a.name)],['automatic',['fixer','reviewer']]);
  const status=await call('conversation_status',scope);
  assert.equal(status.collaboration.state,'automatic');assert.equal(status.coding.state,'granted');
  assert.deepEqual(await call('_fixture_starts'),[],'configuration starts nothing');
  await record('settings',{policy,agents:await call('collab_agents'),conversations:await call('conversations',{projectId:project.id})});
  // Three agents.
  const started=await call('coding_request',{...scope,clientId:'req-1',text:'Check greeting.py and make sure the tests stay green'});
  assert.ok(started.taskId,started.detail);
  await until(async()=>(await call('tasks',scope))[0].state==='ready_for_review');
  const ownerTask=(await call('tasks',scope))[0];
  const starts=await call('_fixture_starts');
  assert.deepEqual(starts.map(s=>s[0]).sort(),['claude','codex','codex']);assert.equal(new Set(starts.map(s=>s[1])).size,3);
  const packets=await call('collab_packets',{...scope,taskId:ownerTask.id});
  assert.deepEqual(packets.map(p=>[p.agent,p.role,p.status,p.source.thread_id,p.destination.thread_id]),[['reviewer','reviewer','returned',channel.id,review.id],['fixer','worker','returned',channel.id,workRoom.id]]);
  assert.equal(sha(packets[0].payload_text),packets[0].payload_sha256);
  assert.equal(await readFile(path.join(target,'greeting.py'),'utf8'),GREETING+'# fixed by helper\n');
  assert.equal(packets[1].result.verified,true);assert.ok(packets[1].result.test.stderr.includes('Ran 1 test'));
  const one=await call('collab_packet',{...scope,packetId:packets[0].id});
  assert.equal(one.snapshots['greeting.py'].sha256,sha(GREETING));
  assert.equal(Buffer.from(one.snapshots['greeting.py'].bytes_b64,'base64').toString(),GREETING,'the inspector decodes exactly the sent bytes');
  const fromHelper=await call('collab_packet',{projectId:project.id,channelId:review.id,packetId:packets[0].id});
  assert.equal(fromHelper.payload_text,packets[0].payload_text,'the destination reads the same packet');
  const reviewerTask=(await call('tasks',{projectId:project.id,channelId:review.id}))[0];
  assert.deepEqual([reviewerTask.origin,reviewerTask.role,reviewerTask.agent,reviewerTask.packet_id,reviewerTask.state],['collaboration','reviewer','reviewer',packets[0].id,'answered']);
  const attempt=(await call('task_detail',{...scope,taskId:ownerTask.id})).evidence.attempts.at(-1);
  assert.equal((await call('task_review',{...scope,taskId:ownerTask.id,attemptNumber:attempt.number,decision:'approved'})).state,'idle','acceptance is the owner-level review');
  await record('three-agents',{starts,packets,reviewerTask:{id:reviewerTask.id,state:reviewerTask.state,tools:reviewerTask.tokens.map(t=>t.tool)},greetingAfter:GREETING+'# fixed by helper\n'});
  // Refusals and the private boundary.
  await call('_fixture_scenario',{name:'refusal'});
  await call('collab_policy_set',{...scope,mode:'automatic',agents:['reviewer'],channels:[channel.id],maxParallel:3,maxDepth:1,maxCalls:6});
  const second=await call('coding_request',{...scope,clientId:'req-2',text:'Try everything'});
  await until(async()=>(await call('tasks',scope)).at(-1).state!=='working');
  const refused=await call('collab_packets',{...scope,taskId:second.taskId});
  assert.deepEqual(refused.map(p=>[p.agent,p.status,p.child_task_id!==null]),[['fixer','refused',false],['reviewer','refused',false],['reviewer','returned',true]]);
  assert.match(refused[0].refusal,/not allowed/);assert.match(refused[1].refusal,/outside/);
  assert.equal(await readFile(path.join(target,'secret_notes.py'),'utf8'),'# never in any scope\n');
  assert.equal((await call('collab_policy',{projectId:project.id,channelId:priv.id})).state,'private');
  assert.equal((await call('conversation_status',{projectId:project.id,channelId:review.id})).collaboration.state,'channel_not_allowed');
  await record('boundaries',{refused,privateState:'private',reviewRoomState:'channel_not_allowed'});
  // Stop with two helpers running, then restart.
  await call('_fixture_scenario',{name:'hang'});
  await call('collab_policy_set',{...scope,mode:'automatic',agents:['reviewer','fixer'],channels:[channel.id],maxParallel:3,maxDepth:1,maxCalls:6});
  const before=(await call('_fixture_starts')).length;
  const third=await call('coding_request',{...scope,clientId:'req-3',text:'Ask and wait'});
  await until(async()=>{const p=await call('collab_packets',{...scope,taskId:third.taskId});return p.filter(x=>x.status==='running').length===2&&(await call('_fixture_starts')).length-before>=3;});
  await call('task_stop',{...scope,taskId:third.taskId});
  await until(async()=>(await call('tasks',scope)).at(-1).state==='blocked'&&(await call('collab_packets',{...scope,taskId:third.taskId})).every(p=>p.status==='withdrawn'));
  const stopped=await call('collab_packets',{...scope,taskId:third.taskId});
  await stopHost();startHost();await new Promise(r=>setTimeout(r,400));
  assert.deepEqual(await call('_fixture_starts'),[],'a restart replays nothing');
  assert.deepEqual((await call('collab_packets',{...scope,taskId:ownerTask.id})).map(p=>p.status),['returned','returned']);
  assert.deepEqual((await call('collab_packets',{...scope,taskId:third.taskId})).map(p=>p.status),['withdrawn','withdrawn']);
  assert.equal((await call('collab_policy',scope)).state,'automatic');assert.equal((await call('collab_agents')).length,2);
  assert.equal((await call('coding_request',{...scope,clientId:'req-3',text:'Ask and wait'})).taskId,third.taskId,'the same request id returns the same task');
  assert.deepEqual(await call('_fixture_starts'),[]);
  await record('stop-restart',{stopped:stopped.map(p=>({agent:p.agent,status:p.status,error:p.error,late_result:p.late_result})),startsAfterRestart:0});
 }finally{await stopHost();await rm(data,{recursive:true,force:true});}
});
