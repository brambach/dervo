import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
// Browser acceptance for coding through the regular composer. Production frontend (dist), production Host, real broker,
// evidence and confined tests; the provider is the synthetic Codex peer in coding-host-fixture.py. Not live acceptance.
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const dist=path.resolve(process.env.DERVO_QA_DIST??path.join(root,'dist'));
const data=await mkdtemp(path.join(tmpdir(),'dervo-conversation-coding-ui-'));
const evidence=path.join(root,'evidence/conversation-coding');
let worker,lines,pending=[],browser,server;
function startHost(){worker=spawn('/usr/local/bin/python3',['-E','-s','-B',path.join(root,'frontend/coding-host-fixture.py'),data],{env:{},stdio:['pipe','pipe','inherit']});lines=createInterface({input:worker.stdout});lines.on('line',line=>{const next=pending.shift(),r=JSON.parse(line);r.error?next.reject(new Error(r.error)):next.resolve(r.result);});}
function call(operation,args={}){return new Promise((resolve,reject)=>{pending.push({resolve,reject});worker.stdin.write(JSON.stringify({operation,args})+'\n');});}
async function stopHost(){const exit=new Promise(resolve=>worker.once('exit',resolve));worker.stdin.end();await exit;lines.close();}
startHost();
try{
 await mkdir(evidence,{recursive:true});
 const target=path.join(data,'target');await mkdir(target);await writeFile(path.join(target,'test_sample.py'),'import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n');
 const profile=await call('add_profile',{provider:'codex',identity:'synthetic@example.test',category:'personal'});
 const project=await call('_picked_project',{path:target,profile_id:profile.id,model:'synthetic-model'});
 const channel=await call('create_conversation',{projectId:project.id,name:'Engineering',private:false});
 const scope={projectId:project.id,channelId:channel.id};
 server=createServer(async(req,res)=>{try{const file=path.resolve(dist,'.'+decodeURIComponent(new URL(req.url==='/'?'/index.html':req.url,'http://local').pathname));assert.ok(file.startsWith(dist+path.sep));res.setHeader('content-type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]??'application/octet-stream');res.end(await readFile(file));}catch{res.writeHead(404);res.end();}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1280,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.exposeFunction('hostInvoke',async(command,args)=>{assert.equal(command,'local_request');return call(args.operation,args.args);});
 await page.addInitScript(scope=>{if(location.protocol!=='http:')return;localStorage.setItem('dervo-project',scope.projectId);localStorage.setItem('dervo-channel',scope.channelId);window.__TAURI_INTERNALS__={invoke:(command,args)=>window.hostInvoke(command,args)};},scope);
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 const editor=page.locator('[contenteditable=true]');await editor.waitFor();
 // One-time project permission, from the conversation menu. No task form.
 await page.getByRole('button',{name:'Conversation options'}).click();
 await page.getByRole('menuitem',{name:'Allow coding in this project'}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog).toContainText('Asked once per project');
 await page.getByLabel('Allow test_sample.py').check();
 await expect(page.getByLabel('Test file',{exact:true})).toHaveValue('test_sample.py');
 await page.screenshot({path:path.join(evidence,'browser-allow-coding.png')});
 await page.getByRole('button',{name:'Allow coding',exact:true}).click();await expect(dialog).toHaveCount(0);
 await expect.poll(async()=>(await call('conversation_status',scope)).coding.state).toBe('granted');
 assert.equal(await call('_fixture_starts'),0,'allowing coding starts nothing');
 // A natural request in the regular composer. No New coding task form, no proposal, no approval step.
 await editor.fill('Add a comment to test_sample.py and run the tests');await editor.press('Enter');
 await expect(page.getByTestId('message-timeline')).toContainText('Add a comment to test_sample.py and run the tests');
 await expect.poll(async()=>(await call('tasks',scope))[0]?.state,{timeout:20000}).toBe('ready_for_review');
 await expect(page.getByTestId('message-timeline')).toContainText('Ready for review');
 assert.equal(await call('_fixture_starts'),1,'exactly one provider run');
 const requests=await call('coding_requests',scope);const tasks=await call('tasks',scope);
 assert.equal(requests.length,1);assert.equal(requests[0].task_id,tasks[0].id);assert.equal(tasks[0].origin,'conversation');
 await page.screenshot({path:path.join(evidence,'browser-composer-coding.png')});
 // The existing evidence-backed review, in the same conversation.
 await page.getByRole('button',{name:'Inspect task result'}).click();
 await expect(dialog).toContainText('synthetic browser correction');await expect(dialog).toContainText('Ran 1 test');await expect(dialog).toContainText('exit: 0');
 await page.screenshot({path:path.join(evidence,'browser-review.png')});
 await page.getByRole('button',{name:'Approve result',exact:true}).click();
 await expect(dialog).toContainText('Human result review: approved');
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
 // Restart: the request, its task link, the permission and the evidence persist; nothing replays.
 const url=page.url();await page.goto('about:blank');await stopHost();startHost();await page.goto(url);
 assert.equal(await call('_fixture_starts'),0,'Restart must not replay');
 await expect(page.getByTestId('message-timeline')).toContainText('Add a comment to test_sample.py and run the tests');
 assert.equal((await call('coding_permission',scope)).state,'granted');
 assert.equal((await call('coding_requests',scope))[0].task_id,tasks[0].id);
 assert.deepEqual(errors,[]);
 await writeFile(path.join(evidence,'browser-task-evidence.json'),JSON.stringify({providerEvidence:'Synthetic Codex protocol peer (coding-host-fixture.py), not a live provider',verification:'Actual host-confined subprocesses',detail:await call('task_detail',{...scope,taskId:tasks[0].id})},null,2));
 console.log('PASS: composer coding with one-time project permission, real broker/sandbox evidence, review, restart without replay. Synthetic provider, not live acceptance.');
}finally{await browser?.close();await new Promise(resolve=>server?server.close(resolve):resolve());await stopHost();await rm(data,{recursive:true,force:true});}
