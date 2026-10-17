import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createServer} from 'node:http';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
// Browser IPC harness uses the production Python host and real SQLite.
// It doesn't replace provider output or establish native WebView behavior.
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const data=await mkdtemp(path.join(tmpdir(),'dervo-local-ui-'));
let worker,lines,pending=[];
function startHost(){
 worker=spawn('/usr/local/bin/python3',['-E','-s','-B',path.join(root,'backend/host.py'),data],{env:{},stdio:['pipe','pipe','inherit']});
 lines=createInterface({input:worker.stdout});
 lines.on('line',line=>{const next=pending.shift();if(!next)throw new Error('Unexpected host response');const r=JSON.parse(line);r.error?next.reject(new Error(r.error)):next.resolve(r.result);});
}
function call(operation,args={}){return new Promise((resolve,reject)=>{pending.push({resolve,reject});worker.stdin.write(JSON.stringify({operation,args})+'\n');});}
async function stopHost(){worker.stdin.end();await new Promise(resolve=>worker.once('exit',resolve));lines.close();}
startHost();
let browser,server;
try {
 const profile=await call('add_profile',{provider:'codex',identity:'browser-check@example.test',category:'personal'});
 const project=await call('_picked_project',{path:root,profile_id:profile.id,model:'unverified-browser-test'});
 const channel=await call('create_conversation',{projectId:project.id,name:'Engineering verification',private:false});
 server=createServer(async(req,res)=>{try{const file=path.resolve(root,'dist','.'+decodeURIComponent(new URL(req.url==='/'?'/index.html':req.url,'http://local').pathname));assert.ok(file.startsWith(path.join(root,'dist')+path.sep));const target=file.endsWith(path.sep)?file+'index.html':file;const body=await readFile(target);res.setHeader('content-type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(target)]??'application/octet-stream');res.end(body);}catch{res.writeHead(404);res.end();}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1280,height:820}});
 const errors=[];
 let loseNextQueueResponse=false;
 page.on('console',msg=>{if(msg.type()==='error'){errors.push(msg.text());console.error('CONSOLE',msg.text());}});
 page.on('pageerror',e=>{errors.push(e.message);console.error('PAGEERROR',e.stack);});
 await page.exposeFunction('hostInvoke',async(command,args)=>{
  if(command==='local_request'){
   const result=await call(args.operation,args.args);
   if(args.operation==='queue'&&loseNextQueueResponse){loseNextQueueResponse=false;throw new Error('Test transport lost acknowledgement after durable commit');}
   return result;
  }
  throw new Error(`${command} isn't available in the browser IPC harness`);
 });
 await page.addInitScript(({projectId,channelId})=>{
  if(location.protocol!=='http:')return;
  if(!localStorage.getItem('dervo-project'))localStorage.setItem('dervo-project',projectId);
  if(!localStorage.getItem('dervo-channel'))localStorage.setItem('dervo-channel',channelId);
  window.__TAURI_INTERNALS__={invoke:(cmd,args)=>window.hostInvoke(cmd,args)};
 },{projectId:project.id,channelId:channel.id});
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.getByText('Start a local conversation',{exact:true}).waitFor({timeout:10000});
 await page.locator('[contenteditable=true]').waitFor();
 assert.ok((await page.getByTestId('message-timeline').boundingBox()).height>300,'Timeline needs a nonzero flex viewport');
 assert.equal(await page.locator('[data-buzz-gradient-layer]').count(),1,'Use the actual Buzz tint layers so native glass opacity changes the navigation');
 assert.equal(await page.locator('[data-buzz-content-surface]').count(),1,'Restore Buzz framed conversation surface');
 assert.equal(await page.getByTestId('app-top-chrome').count(),1,'Restore Buzz top chrome');
 assert.equal(await page.getByTestId('chat-header').count(),1,'Restore Buzz channel header');
 assert.equal(await page.getByRole('textbox',{name:'Conversation name',exact:true}).count(),0,'Creation belongs in a dialog');
 assert.equal(await page.getByRole('button',{name:'Reload history',exact:true}).count(),0);
 assert.equal(await page.getByRole('button',{name:'Inspect shared context',exact:true}).count(),0);
 await page.screenshot({path:path.join(root,'evidence/browser-channel.png')});
 assert.ok(!errors.some(e=>e.includes('uncaught render error')),errors.join('\n'));
 console.log('PASS: production UI opens a stored channel with real host data');
 const editor=page.locator('[contenteditable=true]');
 await page.getByTestId('conversation-status').getByRole('button').click();
 await page.getByRole('combobox',{name:'Account binding',exact:true}).selectOption(profile.id);
 await page.getByRole('button',{name:'Official sign-in steps',exact:true}).click({timeout:3000});
 await page.getByRole('textbox',{name:'Official sign-in command',exact:true}).waitFor();
 assert.ok((await page.getByRole('textbox',{name:'Official sign-in command',exact:true}).inputValue()).includes('CODEX_HOME='));
 await page.getByRole('button',{name:'Check identity',exact:true}).click();
 await page.getByText('This isolated profile is signed out.',{exact:false}).waitFor({timeout:15000});
 assert.equal((await call('status')).liveEnabled,false);
 await page.getByRole('button',{name:'Load Codex models',exact:true}).click({timeout:3000});
 await page.getByText('Personal subscription identity is missing, ambiguous or changed',{exact:false}).first().waitFor({timeout:30000});
 assert.equal(await page.getByRole('combobox',{name:'Available Codex model',exact:true}).count(),0);
 await page.screenshot({path:path.join(root,'evidence/browser-connection-preflight.png')});
 await page.keyboard.press('Escape');
 await page.getByRole('dialog').waitFor({state:'detached'});
 await page.getByTestId('dialog-overlay').waitFor({state:'detached'});
 console.log('PASS: account dialog displays official owned sign-in and actual signed-out Codex metadata without sending');
 await editor.fill('Shared local test, never sent to a provider.');
 await editor.press('Enter');
 await page.getByText('Queued locally',{exact:true}).waitFor();
 await page.getByRole('alert').filter({hasText:'Personal subscription identity'}).first().waitFor();
 assert.equal(await editor.innerText(),'Shared local test, never sent to a provider.','Failed preflight retains the draft');
 let history=await call('history',{projectId:project.id,channelId:channel.id});
 assert.equal(history.length,1);assert.equal(history[0].content,'Shared local test, never sent to a provider.');
 const dateSource=page.getByTestId('message-timeline-day-divider').locator('p').first();
 const datePinned=page.getByTestId('message-timeline-sticky-day-divider');
 assert.ok(await page.evaluate(()=>{
   const source=document.querySelector('[data-testid="message-timeline-day-divider"] p');
   const pinned=document.querySelector('[data-testid="message-timeline-sticky-day-divider"]');
   return !source||!pinned||getComputedStyle(source).visibility==='hidden'||getComputedStyle(pinned).opacity==='0'||source.getBoundingClientRect().bottom<0;
 }),'A short conversation must not show duplicate date chips');
 console.log('PASS: ordinary composer sends through the durable local host');
 await page.getByRole('button',{name:'Queued message options',exact:true}).first().click();
 await page.getByRole('menuitem',{name:'Send to Codex',exact:true}).click({timeout:3000});
 await page.getByRole('dialog',{name:'Send this message to Codex?'}).waitFor();
 assert.ok((await page.getByRole('dialog').innerText()).includes('browser-check@example.test'));
 assert.ok((await page.getByRole('dialog').innerText()).includes('Read-only does not confine reads'));
 await page.getByRole('button',{name:'Send this message',exact:true}).click();
 await page.getByTestId('dialog-overlay').waitFor({state:'detached'});
 await page.getByText('Personal subscription identity is missing, ambiguous or changed',{exact:false}).first().waitFor({timeout:30000});
 assert.equal((await call('history',{projectId:project.id,channelId:channel.id}))[0].state,'queued');
 await page.screenshot({path:path.join(root,'evidence/browser-codex-send-gate.png')});
 console.log('PASS: explicit send uses the production Codex lifecycle and fresh sign-in gate, retaining queued text');
 await page.getByTestId(`reply-message-${history[0].id}`).click({force:true});
 await editor.fill('Shared reply, queued only.');await editor.press('Enter');
 await page.getByTestId('message-timeline').getByText('Shared reply, queued only.',{exact:true}).waitFor({timeout:5000}).catch(async error=>{await page.screenshot({path:path.join(root,'evidence/reply-failure.png')});console.log('HISTORY',await call('history',{projectId:project.id,channelId:channel.id}));console.log('BODY',await page.locator('body').innerText());throw error;});
 history=await call('history',{projectId:project.id,channelId:channel.id});
 assert.equal(history.length,2);assert.ok(history[1].tags.some(t=>t[0]==='e'&&t[1]===history[0].id&&t[3]==='reply'));
 await expect.poll(async()=> (await call('turn_status',{projectId:project.id,channelId:channel.id,messageId:history[1].id})).active).toBe(false);
 await expect(page.getByRole('button',{name:'Queued message options',exact:true})).toHaveCount(2);
 console.log('PASS: reply composer uses the same scoped local host');
 await page.getByRole('button',{name:'Queued message options',exact:true}).last().click();
 await page.getByRole('menuitem',{name:'Cancel queued message',exact:true}).click({force:true});
 await page.getByText('Cancelled',{exact:true}).waitFor();
 await page.getByRole('button',{name:'New personal conversation',exact:true}).click({force:true});
 await page.getByRole('textbox',{name:'Conversation name',exact:true}).fill('Private verification');
 await page.getByRole('button',{name:'Create conversation',exact:true}).click();
 await page.getByText('Start a local conversation',{exact:true}).waitFor();
 await editor.fill('PRIVATE-CANARY: stays outside shared exports.');await editor.press('Enter');
 await page.getByText('Queued locally',{exact:true}).waitFor();
 await page.getByRole('alert').filter({hasText:'Personal subscription identity'}).first().waitFor();
 const privateId=await page.evaluate(()=>localStorage.getItem('dervo-channel'));
 const shared=await call('shared_context',{projectId:project.id});
 assert.equal(shared.length,2);assert.ok(!JSON.stringify(shared).includes('PRIVATE-CANARY'));
 console.log('PASS: private conversation text is excluded by the host export');
 const privateOrigin=(await call('history',{projectId:project.id,channelId:privateId}))[0];
 await page.getByTestId(`reply-message-${privateOrigin.id}`).click({force:true});
 loseNextQueueResponse=true;
 await editor.fill('Acknowledgement-loss recovery canary');await editor.press('Enter');
 await page.getByRole('alert').filter({hasText:'Test transport lost acknowledgement'}).first().waitFor();
 assert.equal(await editor.innerText(),'Acknowledgement-loss recovery canary');
 await editor.press('Enter');
 await page.getByTestId('message-timeline').getByText('Acknowledgement-loss recovery canary',{exact:true}).waitFor();
 await page.getByRole('alert').filter({hasText:'Personal subscription identity'}).first().waitFor();
 assert.equal(await editor.innerText(),'Acknowledgement-loss recovery canary','Failed delivery still retains retry draft');
 const privateRows=await call('history',{projectId:project.id,channelId:privateId});
 assert.equal(privateRows.filter(m=>m.content==='Acknowledgement-loss recovery canary').length,1);
 assert.equal(await page.getByRole('alert').filter({hasText:'Test transport lost acknowledgement'}).count(),0,'Retry must replace the lost-ack error with the current preflight error');
 console.log('PASS: lost acknowledgement preserves the draft and retry does not duplicate');
 await editor.fill('Unsent draft survives host and renderer restart');
 const restartUrl=page.url();await page.goto('about:blank');
 await stopHost();startHost();await page.goto(restartUrl);
 await page.getByText('PRIVATE-CANARY: stays outside shared exports.',{exact:true}).waitFor();
 assert.equal(await editor.innerText(),'Unsent draft survives host and renderer restart');
 assert.equal((await call('history',{projectId:project.id,channelId:channel.id}))[1].state,'cancelled');
 assert.ok(!JSON.stringify(await call('shared_context',{projectId:project.id})).includes('PRIVATE-CANARY'));
 console.log('PASS: host restart and renderer reload preserve messages, cancellation, privacy and draft');
 await page.screenshot({path:path.join(root,'evidence/browser-queued.png')});
} finally {
 await browser?.close(); await new Promise(resolve=>server?server.close(resolve):resolve());
 await stopHost();await rm(data,{recursive:true,force:true});
}
