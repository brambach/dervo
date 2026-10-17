import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createServer} from 'node:http';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
// Browser acceptance for cross-channel collaboration: production frontend (dist), production Host, real collaboration
// broker, evidence and confined tests; the three providers (Codex owner, Codex reviewer, Claude worker) are the
// synthetic peers in collaboration-host-fixture.py. Not live acceptance. Evidence is written incrementally per step to
// evidence/collaboration-ui/ so a failure mid-way still leaves the earlier steps' facts on disk.
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const dist=path.resolve(process.env.DERVO_QA_DIST??path.join(root,'dist'));
const data=await mkdtemp(path.join(tmpdir(),'dervo-collaboration-ui-'));
const target=path.join(data,'target');
const evidence=path.join(root,'evidence/collaboration-ui');
const SAMPLE='import unittest\nfrom greeting import greet\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertEqual(greet(), "hi")\n';
const GREETING='def greet(): return "hi"\n';
const sha=text=>createHash('sha256').update(text).digest('hex');
let worker,lines,pending=[],browser,server;const steps={};
function startHost(){worker=spawn('/usr/local/bin/python3',['-E','-s','-B',path.join(root,'frontend/collaboration-host-fixture.py'),data,target],{env:{},stdio:['pipe','pipe','inherit']});lines=createInterface({input:worker.stdout});lines.on('line',line=>{const next=pending.shift(),r=JSON.parse(line);r.error?next.reject(new Error(r.error)):next.resolve(r.result);});}
function call(operation,args={}){return new Promise((resolve,reject)=>{pending.push({resolve,reject});worker.stdin.write(JSON.stringify({operation,args})+'\n');});}
async function stopHost(){const exit=new Promise(resolve=>worker.once('exit',resolve));worker.stdin.end();await exit;lines.close();}
async function record(step,facts){steps[step]=facts;await writeFile(path.join(evidence,`browser-${step}.json`),JSON.stringify({providerEvidence:'Synthetic Codex and Claude protocol peers (collaboration-host-fixture.py), not live providers',verification:'Actual host-confined subprocesses and real packet records',...facts},null,2));}
startHost();
try{
 await mkdir(evidence,{recursive:true});await mkdir(target);
 await writeFile(path.join(target,'test_sample.py'),SAMPLE);await writeFile(path.join(target,'greeting.py'),GREETING);
 await writeFile(path.join(target,'notes.py'),'# owner only\n');await writeFile(path.join(target,'secret_notes.py'),'# never in any scope\n');
 const owner=await call('add_profile',{provider:'codex',identity:'synthetic@example.test',category:'personal'});
 const cx=await call('add_profile',{provider:'codex',identity:'synthetic@example.test',category:'personal'});
 const cl=await call('add_profile',{provider:'claude',identity:'personal@example.test',category:'personal'});
 const project=await call('_picked_project',{path:target,profile_id:owner.id,model:'synthetic-model'});
 const channel=await call('create_conversation',{projectId:project.id,name:'Engineering',private:false});
 const priv=await call('create_conversation',{projectId:project.id,name:'Notes',private:true});
 const scope={projectId:project.id,channelId:channel.id};
 // Existing account boundary setup, reused: linking is explicit by account id. Coding permission is the existing one-time grant.
 await call('harness_boundary',{profileIds:[owner.id,cx.id,cl.id]});
 await call('coding_permission_grant',{...scope,files:['greeting.py','notes.py','test_sample.py'],testFile:'test_sample.py'});
 server=createServer(async(req,res)=>{try{const file=path.resolve(dist,'.'+decodeURIComponent(new URL(req.url==='/'?'/index.html':req.url,'http://local').pathname));assert.ok(file.startsWith(dist+path.sep));res.setHeader('content-type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]??'application/octet-stream');res.end(await readFile(file));}catch{res.writeHead(404);res.end();}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1280,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.exposeFunction('hostInvoke',async(command,args)=>{assert.equal(command,'local_request');return call(args.operation,args.args);});
 await page.addInitScript(scope=>{if(location.protocol!=='http:')return;localStorage.setItem('dervo-project',scope.projectId);if(!localStorage.getItem('dervo-channel'))localStorage.setItem('dervo-channel',scope.channelId);window.__TAURI_INTERNALS__={invoke:(command,args)=>window.hostInvoke(command,args)};},scope);
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 const editor=page.locator('[contenteditable=true]');await editor.waitFor();
 const dialog=page.getByRole('dialog');
 // Step 1: real settings controls. Off by default; two named agents on two other accounts, each with a conversation created atomically; explicit automatic enable.
 await page.getByRole('button',{name:'Conversation options'}).click();
 await page.getByRole('menuitem',{name:'Collaboration in this project'}).click();
 await expect(page.getByTestId('collab-policy-state')).toContainText('Now: off');
 await expect(dialog).toContainText('The owner is the conversation you send from: #Engineering · Codex · synthetic@example.test');
 for(const agent of [{name:'reviewer',account:cx.id,room:'Review room',role:'reviewer',instructions:'Review for correctness. Point at exact lines.'},{name:'fixer',account:cl.id,room:'Work room',role:'worker',instructions:'Make the smallest change that makes the tests pass.'}]){
  // The agent form is collapsed until Add agent opens it, and each open starts from an empty form.
  await page.getByRole('button',{name:'Add agent',exact:true}).click();
  await page.getByLabel('Agent name').fill(agent.name);
  await page.getByLabel('Agent role').selectOption(agent.role);
  await page.getByLabel('Agent account').selectOption(agent.account);
  await page.getByLabel('Agent conversation').selectOption('__new__');
  await page.getByLabel('New conversation name').fill(agent.room);
  await page.getByLabel('Agent model').fill('synthetic-model');
  await page.getByLabel('Agent instructions').fill(agent.instructions);
  await page.getByRole('button',{name:'Save agent'}).click();
  // Allowed agents only shows once a mode other than Off is chosen, so the saved agent is checked in its Named agents row.
  await expect(page.getByRole('button',{name:`Edit @${agent.name}`})).toBeVisible();
 }
 const projectsAfter=await call('projects');assert.equal(projectsAfter[0].profile_id,owner.id,'the project binding is untouched');
 const conversations=await call('conversations',{projectId:project.id});
 assert.deepEqual(conversations.filter(c=>c.visibility==='shared').map(c=>[c.name,c.profile_id]),[['Engineering',owner.id],['Review room',cx.id],['Work room',cl.id]]);
 assert.equal((await call('collab_policy',scope)).state,'off','saving agents enables nothing');
 await page.getByLabel(/^Automatic:/).check();
 await page.getByLabel('Allow agent reviewer').check();await page.getByLabel('Allow agent fixer').check();
 await expect(page.getByLabel('Allow asking from Engineering')).toBeChecked();
 await page.screenshot({path:path.join(evidence,'browser-settings.png')});
 await page.getByRole('button',{name:'Save collaboration policy'}).click();
 await expect(page.getByTestId('collab-policy-state')).toContainText('Now: automatic');
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
 const policy=await call('collab_policy',scope);
 assert.deepEqual([policy.state,policy.agents,policy.channels,policy.limits],['automatic',['fixer','reviewer'],[channel.id],{max_parallel:3,max_depth:1,max_calls:6}]);
 assert.deepEqual(await call('_fixture_starts'),[],'configuration starts nothing');
 await expect(page.getByTestId('conversation-status')).toContainText('may ask @fixer, @reviewer');
 await record('settings',{policy,agents:await call('collab_agents'),conversations,projectBindingUnchanged:projectsAfter[0].profile_id===owner.id});
 // Step 2: one natural composer message; the owner's coding model asks both agents; three agents run across three channels.
 await editor.fill('Check greeting.py and make sure the tests stay green');await editor.press('Enter');
 await expect(page.getByTestId('message-timeline')).toContainText('Check greeting.py and make sure the tests stay green');
 await expect.poll(async()=>(await call('tasks',scope))[0]?.state,{timeout:40000}).toBe('ready_for_review');
 const tasks=await call('tasks',scope);const ownerTask=tasks[0];
 assert.equal(ownerTask.origin,'conversation');assert.deepEqual(ownerTask.collaboration.agents,['fixer','reviewer']);
 const starts=await call('_fixture_starts');
 assert.deepEqual(starts.map(s=>s[0]).sort(),['claude','codex','codex'],'owner plus two helpers, one start each');
 assert.equal(new Set(starts.map(s=>s[1])).size,3,'each under its own profile');
 const packets=await call('collab_packets',{...scope,taskId:ownerTask.id});
 assert.deepEqual(packets.map(p=>[p.agent,p.role,p.status]),[['reviewer','reviewer','returned'],['fixer','worker','returned']]);
 assert.equal(sha(packets[0].payload_text),packets[0].payload_sha256);
 assert.equal(await readFile(path.join(target,'greeting.py'),'utf8'),GREETING+'# fixed by helper\n','the worker really changed the file');
 assert.equal(packets[1].result.verified,true);assert.ok(packets[1].result.test.stderr.includes('Ran 1 test'));
 // Footer rows only: the task dialog renders the same rows again while it is open.
 const footerRows=id=>page.locator(`[data-testid="task-footer"][data-task-id="${id}"] [data-testid="collab-packet"]`);
 const rows=footerRows(ownerTask.id);
 await expect(rows).toHaveCount(2);
 await expect(rows.nth(0)).toContainText('#Engineering → @reviewer (reviewer, read-only, Codex)');await expect(rows.nth(0)).toContainText('in #Review room');
 await expect(rows.nth(0)).toContainText('Review greeting.py against test_sample.py');await expect(rows.nth(0)).toContainText('Returned');
 await expect(rows.nth(1)).toContainText('@fixer (worker, may change files, Claude Code)');await expect(rows.nth(1)).toContainText('in #Work room');await expect(rows.nth(1)).toContainText('Returned, verified by host tests');
 await expect(page.getByTestId('message-timeline')).toContainText('Ready for review');
 await page.screenshot({path:path.join(evidence,'browser-three-agents.png')});
 await record('three-agents',{starts,ownerTask:{id:ownerTask.id,state:ownerTask.state,tokens:ownerTask.tokens.map(t=>[t.tool,t.state])},packets,greetingAfter:await readFile(path.join(target,'greeting.py'),'utf8')});
 // Step 3: exact packet inspection, then source -> destination -> source navigation.
 await rows.nth(0).getByRole('button',{name:'Request details'}).click();
 const inspector=page.getByTestId('packet-inspector');
 await expect(inspector.getByTestId('packet-status')).toContainText('Returned');
 assert.equal(await inspector.getByTestId('packet-payload').textContent(),packets[0].payload_text,'the inspector shows exactly the sent text');
 await expect(inspector).toContainText(`sha256 ${packets[0].payload_sha256}`);
 const one=await call('collab_packet',{...scope,packetId:packets[0].id});
 assert.equal(one.snapshots['greeting.py'].sha256,sha(GREETING),'the reviewer was sent the file as it was before the fix');
 await inspector.locator('details',{hasText:'greeting.py'}).first().locator('summary').click();
 assert.equal(await inspector.getByTestId('packet-snapshot').first().textContent(),GREETING);
 await expect(inspector.getByTestId('packet-summary')).toContainText('REVIEW: greet() returns "hi"');
 assert.equal(await inspector.getByRole('button',{name:/approve|accept|reject/i}).count(),0,'inspection never approves');
 await page.screenshot({path:path.join(evidence,'browser-packet-inspector.png')});
 await inspector.getByRole('button',{name:'Open helper task'}).click();
 await expect(page.getByRole('heading',{name:'Review room'})).toBeVisible();
 await expect(dialog).toContainText('Inspect helper task · Helper @reviewer (reviewer, read-only)');
 await expect(dialog.getByTestId('helper-source')).toContainText('Asked by the owner task in #Engineering (Codex · synthetic-model) · purpose: Review greeting.py against test_sample.py');
 assert.equal(await dialog.getByTestId('helper-received').textContent(),packets[0].payload_text,'the destination shows the request actually received');
 await expect(page.locator('[data-testid="task-footer"] [data-testid="helper-label"]')).toContainText('Helper @reviewer (reviewer, read-only)');
 const reviewerTask=(await call('tasks',{projectId:project.id,channelId:packets[0].destination.thread_id}))[0];
 assert.deepEqual([reviewerTask.origin,reviewerTask.role,reviewerTask.state,reviewerTask.tokens.map(t=>t.tool)],['collaboration','reviewer','answered',['dervo_read_file']]);
 await page.screenshot({path:path.join(evidence,'browser-helper-task.png')});
 await dialog.getByRole('button',{name:'Open owner task'}).click();
 await expect(page.getByRole('heading',{name:'Engineering',includeHidden:true})).toBeVisible();
 await expect(dialog).toContainText('Inspect coding task');
 await expect(page.getByTestId('helper-label')).toHaveCount(0);
 await expect(rows).toHaveCount(2);
 // Owner-level acceptance stays the existing review, in the source conversation.
 await page.getByRole('button',{name:'Approve result',exact:true}).click();
 await expect(dialog).toContainText('Human result review: approved');
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await page.locator('[data-slot="dialog-overlay"]').waitFor({state:'detached'});
 await record('navigation',{inspected:packets[0].id,snapshotSha:one.snapshots['greeting.py'].sha256,reviewerTask:{id:reviewerTask.id,state:reviewerTask.state,tools:reviewerTask.tokens.map(t=>t.tool)},ownerReview:(await call('task_detail',{...scope,taskId:ownerTask.id})).evidence.review});
 // Step 4: rejection and private boundaries. Only the reviewer allowed; the fixer and an out-of-scope file are refused without a child.
 await call('_fixture_scenario',{name:'refusal'});
 await call('collab_policy_set',{...scope,mode:'automatic',agents:['reviewer'],channels:[channel.id],maxParallel:3,maxDepth:1,maxCalls:6});
 await editor.fill('Try everything');await editor.press('Enter');
 await expect.poll(async()=>(await call('tasks',scope)).at(-1)?.state,{timeout:40000}).not.toBe('working');
 const refusedTask=(await call('tasks',scope)).at(-1);
 const refused=await call('collab_packets',{...scope,taskId:refusedTask.id});
 assert.deepEqual(refused.map(p=>[p.agent,p.status,p.child_task_id!==null]),[['fixer','refused',false],['reviewer','refused',false],['reviewer','returned',true]]);
 const refusedRows=footerRows(refusedTask.id);
 await expect(refusedRows).toHaveCount(3);
 await expect(refusedRows.nth(0)).toContainText("Refused: Agent 'fixer' is not allowed for this work request");
 await expect(refusedRows.nth(1)).toContainText('Refused: secret_notes.py is outside this task\'s granted files');
 await expect(refusedRows.nth(2)).toContainText('Returned');
 assert.equal(await readFile(path.join(target,'secret_notes.py'),'utf8'),'# never in any scope\n');
 await page.screenshot({path:path.join(evidence,'browser-refusals.png')});
 assert.equal((await call('collab_policy',{projectId:project.id,channelId:priv.id})).state,'private');
 await page.evaluate(id=>localStorage.setItem('dervo-channel',id),priv.id);await page.reload();await page.locator('[contenteditable=true]').waitFor();
 await expect(page.getByTestId('conversation-status')).toContainText('Coding in this project · Codex');
 await expect(page.getByTestId('conversation-status')).not.toContainText('may ask');
 await page.evaluate(id=>localStorage.setItem('dervo-channel',id),channel.id);await page.reload();await page.locator('[contenteditable=true]').waitFor();
 await record('boundaries',{refused,privatePolicy:await call('collab_policy',{projectId:project.id,channelId:priv.id}),secretUnchanged:true});
 // Step 5: stop while two helpers are running, then restart the host: withdrawn packets, no replay, everything persists.
 await call('_fixture_scenario',{name:'hang'});
 await call('collab_policy_set',{...scope,mode:'automatic',agents:['reviewer','fixer'],channels:[channel.id],maxParallel:3,maxDepth:1,maxCalls:6});
 const startsBefore=(await call('_fixture_starts')).length;
 await page.locator('[contenteditable=true]').fill('Ask and wait');await page.locator('[contenteditable=true]').press('Enter');
 await expect.poll(async()=>{const t=(await call('tasks',scope)).at(-1);if(!t||t.state!=='working')return 0;const p=await call('collab_packets',{...scope,taskId:t.id});return p.filter(x=>x.status==='running').length+((await call('_fixture_starts')).length-startsBefore>=3?10:0);},{timeout:40000}).toBe(12);
 const hangTask=(await call('tasks',scope)).at(-1);
 const hangRows=footerRows(hangTask.id);
 await expect(hangRows.nth(0)).toContainText('Running');await expect(hangRows.nth(1)).toContainText('Running');
 await page.screenshot({path:path.join(evidence,'browser-running.png')});
 await page.getByRole('button',{name:'Stop task'}).click();
 await expect.poll(async()=>(await call('tasks',scope)).at(-1).state,{timeout:40000}).toBe('blocked');
 await expect.poll(async()=>(await call('collab_packets',{...scope,taskId:hangTask.id})).map(p=>p.status).join(','),{timeout:40000}).toBe('withdrawn,withdrawn');
 await expect(hangRows.nth(0)).toContainText('Withdrawn');await expect(hangRows.nth(1)).toContainText('Withdrawn');
 const stopped=await call('collab_packets',{...scope,taskId:hangTask.id});
 const startsAtStop=(await call('_fixture_starts')).length;
 const url=page.url();await page.goto('about:blank');await stopHost();startHost();await page.goto(url);await page.locator('[contenteditable=true]').waitFor();
 await new Promise(resolve=>setTimeout(resolve,500));
 assert.deepEqual(await call('_fixture_starts'),[],'a restart replays nothing');
 assert.deepEqual((await call('collab_packets',{...scope,taskId:ownerTask.id})).map(p=>p.status),['returned','returned'],'finished packets keep their results');
 assert.deepEqual((await call('collab_packets',{...scope,taskId:hangTask.id})).map(p=>p.status),['withdrawn','withdrawn']);
 assert.equal((await call('collab_policy',scope)).state,'automatic');assert.equal((await call('collab_agents')).length,2);
 await expect(page.getByTestId('collab-packet')).toHaveCount(7);
 await expect(hangRows.nth(1)).toContainText('Withdrawn');
 await page.screenshot({path:path.join(evidence,'browser-after-restart.png')});
 await record('stop-restart',{stopped:stopped.map(p=>({agent:p.agent,status:p.status,error:p.error,late_result:p.late_result})),hangTaskState:(await call('tasks',scope)).at(-1).state,startsAtStop,startsAfterRestart:(await call('_fixture_starts')).length,pageErrors:errors});
 assert.deepEqual(errors,[]);
 console.log('PASS: real settings controls (agents on two other accounts, atomic conversations, explicit automatic enable), three agents across three channels with actual file and test evidence, exact packet inspection, source/destination navigation, owner-level acceptance, refusal and private boundaries, stop and restart without replay. Synthetic providers, not live acceptance.');
}finally{await browser?.close();await new Promise(resolve=>server?server.close(resolve):resolve());await stopHost();await rm(data,{recursive:true,force:true});}
