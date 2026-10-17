import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
// Synthetic provider only. Test evidence comes from real confined subprocesses.
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const data=await mkdtemp(path.join(tmpdir(),'dervo-coding-ui-'));
let worker,lines,pending=[],browser,server;
function startHost(){worker=spawn('/usr/local/bin/python3',['-E','-s','-B',path.join(root,'frontend/coding-host-fixture.py'),data],{env:{},stdio:['pipe','pipe','inherit']});lines=createInterface({input:worker.stdout});lines.on('line',line=>{const next=pending.shift(),r=JSON.parse(line);r.error?next.reject(new Error(r.error)):next.resolve(r.result);});}
function call(operation,args={}){return new Promise((resolve,reject)=>{pending.push({resolve,reject});worker.stdin.write(JSON.stringify({operation,args})+'\n');});}
async function stopHost(){const exit=new Promise(resolve=>worker.once('exit',resolve));worker.stdin.end();await exit;lines.close();}
startHost();
try{
 const target=path.join(data,'target');await mkdir(target);await writeFile(path.join(target,'test_sample.py'),'import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n');
 const profile=await call('add_profile',{provider:'codex',identity:'synthetic@example.test',category:'personal'});
 const project=await call('_picked_project',{path:target,profile_id:profile.id,model:'synthetic-model'});
 const channel=await call('create_conversation',{projectId:project.id,name:'Private coding test',private:true});
 const scope={projectId:project.id,channelId:channel.id};
 server=createServer(async(req,res)=>{try{const file=path.resolve(root,'dist','.'+decodeURIComponent(new URL(req.url==='/'?'/index.html':req.url,'http://local').pathname));assert.ok(file.startsWith(path.join(root,'dist')+path.sep));res.setHeader('content-type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]??'application/octet-stream');res.end(await readFile(file));}catch{res.writeHead(404);res.end();}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1280,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.exposeFunction('hostInvoke',async(command,args)=>{assert.equal(command,'local_request');return call(args.operation,args.args);});
 await page.addInitScript(scope=>{if(location.protocol!=='http:')return;localStorage.setItem('dervo-project',scope.projectId);localStorage.setItem('dervo-channel',scope.channelId);window.__TAURI_INTERNALS__={invoke:(command,args)=>window.hostInvoke(command,args)};},scope);
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.getByRole('button',{name:'Conversation options'}).click();
 await page.getByRole('menuitem',{name:'New coding task'}).click();
 const dialog=page.getByRole('dialog');
 for(const text of ['synthetic@example.test','synthetic-model','private','filesystem','existing files'])await expect(dialog).toContainText(text);
 await page.getByLabel('Development request').fill('Synthetic browser task');
 await page.getByLabel('Allowed files').fill('test_sample.py');await page.getByLabel('Test file',{exact:true}).fill('test_sample.py');
 await page.getByRole('button',{name:'Save task scope'}).click();await expect(dialog).toHaveCount(0);
 await expect(page.getByTestId('message-timeline')).toContainText('Needs permission');assert.equal(await call('_fixture_starts'),0);
 await page.getByRole('button',{name:'Review execution scope'}).click();
 await page.getByRole('button',{name:'Approve scope and start'}).click();
 // The inspector stays mounted while streaming moves the result footer.
 await expect.poll(async()=> (await call('tasks',scope))[0].state,{timeout:20000}).toBe('ready_for_review');
 await expect(dialog).toHaveCount(1);
 if(await dialog.count())await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
 await page.locator('[data-slot="dialog-overlay"]').waitFor({state:'detached'});
 await page.getByRole('button',{name:'Inspect task result'}).click();
 await expect(dialog).toContainText('synthetic browser correction');await expect(dialog).toContainText('Ran 1 test');await expect(dialog).toContainText('exit: 0');
 assert.equal(await call('_fixture_starts'),1);
 const before=(await call('tasks',scope))[0];assert.ok(before.session_id);assert.equal((await call('binding',scope)).session_id,null);
 await dialog.getByRole('button',{name:'Ask for changes',exact:true}).click();
 await expect(page.getByLabel('Continue task feedback')).toBeVisible();
 await page.screenshot({path:path.join(root,'evidence/integration/coding-review.png')});
 const url=page.url();await page.goto('about:blank');await stopHost();startHost();await page.goto(url);
 assert.equal(await call('_fixture_starts'),0,'Restart must not replay');
 await page.getByRole('button',{name:'Continue task',exact:true}).click();await page.getByLabel('Continue task feedback').fill('Correct this same task');
 await dialog.getByRole('button',{name:'Continue task',exact:true}).click();
 await expect.poll(async()=> (await call('tasks',scope))[0].runs.length).toBe(2);
 await expect.poll(async()=> (await call('tasks',scope))[0].state,{timeout:20000}).toBe('ready_for_review');
 const after=(await call('tasks',scope))[0];assert.equal(after.id,before.id);assert.equal(after.session_id,before.session_id);
 if(await dialog.count())await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await page.locator('[data-slot="dialog-overlay"]').waitFor({state:'detached'});
 await page.getByRole('button',{name:'Inspect task result'}).click();await page.getByRole('button',{name:'Approve result',exact:true}).click().catch(async error=>{console.log('TASKS',JSON.stringify(await call('tasks',scope)));console.log('BODY',await page.locator('body').innerText());await page.screenshot({path:path.join(root,'evidence/integration/coding-failure.png')});throw error;});
 await expect(dialog).toContainText('Human result review: approved');assert.deepEqual(await call('shared_context',{projectId:project.id}),[]);assert.deepEqual(errors,[]);
 await writeFile(path.join(root,'evidence/integration/synthetic-task-evidence.json'),JSON.stringify({providerEvidence:'Synthetic protocol peer, not a live provider',verification:'Actual host-confined subprocesses',detail:await call('task_detail',{...scope,taskId:after.id})},null,2));
 console.log('PASS: production task UI approval, actual sandbox/diff/output, correction, separate UUID, restart same task/session, private exclusion. Synthetic provider, not live acceptance.');
}finally{await browser?.close();await new Promise(resolve=>server?server.close(resolve):resolve());await stopHost();await rm(data,{recursive:true,force:true});}
