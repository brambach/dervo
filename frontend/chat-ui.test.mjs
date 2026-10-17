import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
// Production frontend and Host. Synthetic protocol peers and temporary SQLite only.
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const data=await mkdtemp(path.join(tmpdir(),'dervo-chat-ui-'));
let worker,lines,pending=[],browser,server;
function startHost(){
 worker=spawn('/usr/local/bin/python3',['-E','-s','-B',path.join(root,'frontend/chat-host-fixture.py'),data],{env:{},stdio:['pipe','pipe','inherit']});
 lines=createInterface({input:worker.stdout});
 lines.on('line',line=>{const next=pending.shift();const r=JSON.parse(line);r.error?next.reject(new Error(r.error)):next.resolve(r.result);});
}
function call(operation,args={}){return new Promise((resolve,reject)=>{pending.push({resolve,reject});worker.stdin.write(JSON.stringify({operation,args})+'\n');});}
async function stopHost(){const exit=new Promise(resolve=>worker.once('exit',resolve));worker.stdin.end();await exit;lines.close();}
startHost();
try {
 const profile=await call('add_profile',{provider:'codex',identity:'synthetic@example.test',category:'personal'});
 const project=await call('_picked_project',{path:data,profile_id:profile.id,model:'synthetic-model'});
 const channel=await call('create_conversation',{projectId:project.id,name:'Chat verification',private:false});
 const scope={projectId:project.id,channelId:channel.id};
 const old=await call('queue',{...scope,clientId:'older',text:'Older queue stays local',parentId:null});
 server=createServer(async(req,res)=>{try{const file=path.resolve(root,'dist','.'+decodeURIComponent(new URL(req.url==='/'?'/index.html':req.url,'http://local').pathname));assert.ok(file.startsWith(path.join(root,'dist')+path.sep));const body=await readFile(file);res.setHeader('content-type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]??'application/octet-stream');res.end(body);}catch{res.writeHead(404);res.end();}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1280,height:820}});
 let loseHistory=false;const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.exposeFunction('hostInvoke',async(command,args)=>{
  assert.equal(command,'local_request');
  const result=await call(args.operation,args.args);
  if(args.operation==='queue'&&loseHistory){loseHistory=false;throw new Error('Synthetic lost queue acknowledgement');}
  return result;
 });
 await page.addInitScript(scope=>{
  if(location.protocol!=='http:')return;
  localStorage.setItem('dervo-project',scope.projectId);localStorage.setItem('dervo-channel',scope.channelId);
  window.__TAURI_INTERNALS__={invoke:(command,args)=>window.hostInvoke(command,args)};
 },scope);
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 const indicator=page.getByTestId('conversation-status');
 await expect(indicator).toContainText('Unchecked');
 for(const value of ['synthetic@example.test','synthetic-model','Chat only'])await expect(indicator).toContainText(value);
 assert.equal(await page.getByTestId('app-top-chrome').count(),1);
 assert.equal(await page.getByTestId('chat-header').count(),1);
 const editor=page.locator('[contenteditable=true]');
 await editor.fill('Fresh explicit chat');await editor.press('Enter');
 await expect(indicator).toContainText('Blocked');
 await expect(editor).toHaveText('Fresh explicit chat');
 const pendingId=await page.evaluate(()=>JSON.parse(localStorage.getItem(`dervo-pending:${localStorage.getItem('dervo-channel')}:root`)).clientId);
 assert.equal(await call('_fixture_starts'),0);
 await call('_fixture_signed_in',{value:true});
 await page.reload();
 await expect(editor).toHaveText('Fresh explicit chat');
 assert.equal(await call('_fixture_starts'),0,'Identity change and reload must not drain queues');
 await editor.press('Enter');
 await expect(editor).toHaveText('');
 await expect(indicator).toContainText('Idle');
 await expect(page.getByTestId('message-timeline')).not.toContainText('Personal subscription identity is missing');
 assert.equal(await call('_fixture_starts'),1);
 let rows=await call('history',scope);
 assert.equal(rows.find(row=>row.id===old.id).state,'queued');
 assert.equal(rows.filter(row=>row.content==='Fresh explicit chat').length,1);
 assert.equal(rows.filter(row=>row.content==='Synthetic answer').length,1);
 // A durable queue acknowledgement loss keeps the composer and request ID.
 loseHistory=true;await editor.fill('Acknowledgement canary');await editor.press('Enter');
 await expect(page.getByRole('alert').first()).toContainText('Synthetic lost queue acknowledgement');
 await expect(editor).toHaveText('Acknowledgement canary');
 const retryId=await page.evaluate(()=>JSON.parse(localStorage.getItem(`dervo-pending:${localStorage.getItem('dervo-channel')}:root`)).clientId);
 assert.notEqual(retryId,pendingId);
 await editor.press('Enter');
 await expect.poll(()=>call('_fixture_starts')).toBe(2);
 await expect.poll(()=>page.evaluate(()=>localStorage.getItem(`dervo-pending:${localStorage.getItem('dervo-channel')}:root`))).toBeNull();
 await expect(editor).toHaveText('');
 assert.equal(await call('_fixture_starts'),2);
 rows=await call('history',scope);
 assert.equal(rows.filter(row=>row.content==='Acknowledgement canary').length,1);
 await editor.fill('Unsent restart draft');
 const url=page.url();await page.goto('about:blank');
 await stopHost();startHost();await page.goto(url);
 await expect(editor).toHaveText('Unsent restart draft');
 await expect(indicator).toContainText('Unchecked');
 assert.equal(await call('_fixture_starts'),0);
 assert.equal((await call('history',scope)).find(row=>row.id===old.id).state,'queued');
 assert.deepEqual(errors,[]);
 await page.screenshot({path:path.join(root,'evidence/chat-slice/browser-direct-chat.png')});
 console.log('PASS: production shell, scoped status, failed-preflight draft, explicit retry, one response, lost acknowledgement, older queue and restart. Synthetic peers only.');
} finally {
 await browser?.close();await new Promise(resolve=>server?server.close(resolve):resolve());
 await stopHost();await rm(data,{recursive:true,force:true});
}
