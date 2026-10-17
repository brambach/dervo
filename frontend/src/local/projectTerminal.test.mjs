import assert from 'node:assert/strict';
import {test} from 'node:test';
import {ProjectTerminalController,terminalBytes,terminalBase64,terminalOutput,terminalSize} from './projectTerminalSession.ts';
const session=(projectId='p',extra={})=>({sessionId:`shell-${projectId}`,projectId,state:'running',cwd:`/fixture/${projectId}`,cols:80,rows:18,nextInputSeq:0,startCursor:0,endCursor:0,exitCode:null,pendingInputBytes:0,...extra});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const settle=async check=>{for(let i=0;i<30;i++){if(check())return;await new Promise(resolve=>setTimeout(resolve,1));}assert.ok(check(),'operation settled');};
const sink=()=>{const output=[];let resets=0;return {output,get resets(){return resets;},write:async bytes=>output.push(bytes),reset:()=>resets++};};

test('discovery and view changes never start or close a shell',async()=>{
 const calls=[];const controller=new ProjectTerminalController('p',async(operation,args)=>{calls.push({operation,args});return {sessions:[]};});
 const output=sink(),detach=controller.attach(output);await controller.load();detach();controller.attach(sink());await controller.load();
 assert.deepEqual(calls.map(call=>call.operation),['terminal_list','terminal_list']);assert.equal(controller.snapshot.session,null);
});

test('an uncertain start retries its original identity and remount discovery reattaches without starting',async()=>{
 const calls=[];let fail=true,starts=0;const created=new Map();
 const request=async(operation,args)=>{
  calls.push({operation,args});
  if(operation==='terminal_list')return {sessions:[...created.values()]};
  if(operation==='terminal_open'){
   if(!created.has(args.clientId)){starts++;created.set(args.clientId,session());}
   if(fail){fail=false;throw Error('Acknowledgement lost');}return created.get(args.clientId);
  }
 };
 const controller=new ProjectTerminalController('p',request,()=> 'open-1');controller.attach(sink());
 await controller.start();assert.equal(controller.snapshot.issue.action,'start');await controller.retry();
 assert.equal(starts,1);assert.deepEqual(calls.filter(call=>call.operation==='terminal_open').map(call=>call.args.clientId),['open-1','open-1']);
 const reattached=new ProjectTerminalController('p',request);reattached.attach(sink());await reattached.load();
 assert.equal(reattached.snapshot.session.sessionId,'shell-p');assert.equal(starts,1);
});

test('input retry preserves sequence and bytes, and later input stays in order',async()=>{
 const calls=[],accepted=[];let fail=true;const gate=deferred();
 const controller=new ProjectTerminalController('p',async(operation,args)=>{
  calls.push({operation,args});if(operation==='terminal_list')return {sessions:[session()]};
  if(operation==='terminal_write'){
   if(args.sequence===0&&fail){accepted.push(args.dataBase64);await gate.promise;fail=false;throw Error('Acknowledgement lost');}
   if(args.sequence!==0)accepted.push(args.dataBase64);
   return {sessionId:'shell-p',sequence:args.sequence,nextInputSeq:args.sequence+1,acceptedBytes:terminalOutput(args.dataBase64).length,pendingInputBytes:0};
  }
 });
 controller.attach(sink());await controller.load();controller.enqueue(terminalBytes('α😀'));controller.enqueue(terminalBytes('\r'));
 gate.resolve();await settle(()=>controller.snapshot.issue?.action==='input');
 // Reopening this project mustn't erase the uncertain input's recovery action.
 await controller.load();assert.equal(controller.snapshot.issue.action,'input');await controller.retry();
 assert.deepEqual(calls.filter(call=>call.operation==='terminal_write').map(call=>[call.args.sequence,call.args.dataBase64]),[[0,terminalBase64(terminalBytes('α😀'))],[0,terminalBase64(terminalBytes('α😀'))],[1,terminalBase64(terminalBytes('\r'))]]);
 assert.deepEqual(accepted.map(value=>Buffer.from(terminalOutput(value)).toString()),['α😀','\r']);assert.equal(controller.snapshot.queuedBytes,0);
});

test('large input is chunked by bytes without corrupting Unicode, binary input stays binary, and rejected input is explicit',async()=>{
 const chunks=[];
 const controller=new ProjectTerminalController('p',async(operation,args)=>{
  if(operation==='terminal_list')return {sessions:[session()]};
  chunks.push(terminalOutput(args.dataBase64));return {sessionId:'shell-p',sequence:args.sequence,nextInputSeq:args.sequence+1,acceptedBytes:chunks.at(-1).length,pendingInputBytes:0};
 });
 await controller.load();const text='😀'.repeat(9000);controller.enqueue(terminalBytes(text));await settle(()=>controller.snapshot.queuedBytes===0);
 assert.equal(chunks.length,3);assert.ok(chunks.every(bytes=>bytes.length<=16384));assert.deepEqual(Buffer.concat(chunks),Buffer.from(text));
 assert.deepEqual([...terminalBytes('\u0000\u0080\u00ff',true)],[0,128,255]);
 const count=chunks.length;controller.enqueue(new Uint8Array(65537));assert.equal(chunks.length,count);assert.equal(controller.snapshot.issue.action,'input-limit');assert.match(controller.snapshot.issue.message,/wasn’t sent/);
});

test('output advances only after xterm parses the bytes and a retained-buffer gap resets the parser',async()=>{
 const calls=[],output=sink(),parsed=deferred();let writes=0;
 output.write=async bytes=>{output.output.push(bytes);writes++;if(writes===1)await parsed.promise;};
 const controller=new ProjectTerminalController('p',async(operation,args)=>{
  if(operation==='terminal_list')return {sessions:[session('p',{endCursor:7})]};calls.push(args.cursor);
  return {...session('p',{startCursor:4,endCursor:7}),dataBase64:terminalBase64(Uint8Array.from([0xf0,0x9f,0x98])),nextCursor:7,dropped:Math.max(0,4-args.cursor)};
 });
 controller.attach(output);await controller.load();const read=controller.read();await settle(()=>writes===1);await controller.read();assert.deepEqual(calls,[0]);
 parsed.resolve();await read;await controller.read();assert.deepEqual(calls,[0,7]);assert.deepEqual([...output.output[0]],[0xf0,0x9f,0x98]);assert.equal(controller.snapshot.lostOutput,true);assert.ok(output.resets>=2);
});

test('detached reads cannot write into another view or advance its output cursor',async()=>{
 const held=deferred(),calls=[];let reads=0;
 const controller=new ProjectTerminalController('p',async(operation,args)=>{
  if(operation==='terminal_list')return {sessions:[session()]};calls.push(args);if(++reads===1)return held.promise;
  return {...session('p',{endCursor:3}),dataBase64:btoa('new'),nextCursor:3,dropped:0};
 });
 const old=sink(),detach=controller.attach(old);await controller.load();const pending=controller.read();detach();const current=sink();controller.attach(current);
 held.resolve({...session('p',{endCursor:3}),dataBase64:btoa('old'),nextCursor:3,dropped:0});await pending;assert.equal(old.output.length,0);assert.equal(current.output.length,0);
 await controller.read();assert.deepEqual(calls.map(call=>call.cursor),[0,0]);assert.equal(Buffer.from(current.output[0]).toString(),'new');assert.ok(calls.every(call=>call.projectId==='p'&&call.sessionId==='shell-p'));
});

test('stale read and resize acknowledgements cannot return a closed shell to running',async()=>{
 const parsed=deferred(),resized=deferred();let writing=false;
 const controller=new ProjectTerminalController('p',async(operation,args)=>{
  if(operation==='terminal_list')return {sessions:[session()]};
  if(operation==='terminal_read')return {...session('p',{endCursor:4}),dataBase64:btoa('done'),nextCursor:4,dropped:0};
  if(operation==='terminal_resize')return resized.promise;
  if(operation==='terminal_close')return session('p',{state:'closed',endCursor:4,exitCode:0});
 });
 controller.attach({reset(){},write:async()=>{writing=true;await parsed.promise;}});await controller.load();
 const reading=controller.read();await settle(()=>writing);controller.resize(100,22);await controller.close();
 parsed.resolve();resized.resolve(session('p',{cols:100,rows:22}));await reading;await new Promise(resolve=>setTimeout(resolve,5));
 assert.equal(controller.snapshot.session.state,'closed');
});

test('close failure stays recoverable and never launches a replacement shell',async()=>{
 const calls=[];let fail=true;
 const controller=new ProjectTerminalController('p',async(operation,args)=>{
  calls.push(operation);if(operation==='terminal_list')return {sessions:[session()]};
  if(operation==='terminal_close'){if(fail){fail=false;throw Error('Close reply lost');}return session('p',{state:'closed'});}
 });
 await controller.load();await controller.close();assert.equal(controller.snapshot.issue.action,'close');await controller.retry();assert.equal(controller.snapshot.session.state,'closed');
 assert.deepEqual(calls,['terminal_list','terminal_close','terminal_close']);
});

test('project mismatches and failed output reads remain explicit',async()=>{
 let wrong=true;
 const controller=new ProjectTerminalController('p',async(operation)=>operation==='terminal_list'?{sessions:[wrong?session('other'):session()]}:Promise.reject(Error('Host unavailable')));
 controller.attach(sink());await controller.load();assert.equal(controller.snapshot.session,null);assert.match(controller.snapshot.issue.message,/another project/);
 wrong=false;await controller.retry();await controller.read();assert.equal(controller.snapshot.issue.action,'read');
 assert.deepEqual(terminalSize(999,0),{cols:500,rows:18});assert.deepEqual(terminalSize(1,999),{cols:2,rows:300});
});

// Beta.15, group 1: Run from a reply or Settings, and the header's running-command state.
test('Run starts a shell in the project folder only when none runs, types the command with Return, and labels it',async()=>{
 const calls=[],typed=[];let open=null;
 const controller=new ProjectTerminalController('p',async(operation,args)=>{
  calls.push(operation);
  if(operation==='terminal_list')return {sessions:open?[open]:[]};
  if(operation==='terminal_open'){open=session();return open;}
  if(operation==='terminal_write'){typed.push(Buffer.from(terminalOutput(args.dataBase64)).toString());return {sessionId:'shell-p',sequence:args.sequence,nextInputSeq:args.sequence+1,acceptedBytes:terminalOutput(args.dataBase64).length,pendingInputBytes:0};}
 },()=> 'id-1');
 controller.attach(sink());await controller.load();
 const run=await controller.run('$ ignored? no\r\ncd frontend\nnpm test  ','From a reply');
 await settle(()=>typed.length>0);
 assert.deepEqual(calls.filter(call=>call==='terminal_open').length,1);
 assert.equal(typed.join(''),'$ ignored? no\rcd frontend\rnpm test\r','each line with Return, as if typed');
 assert.equal(run.label,'From a reply');assert.equal(controller.snapshot.runs.length,1);assert.equal(controller.snapshot.runs[0].sessionId,'shell-p');
 await controller.run('ls','From Settings');await settle(()=>typed.length>1);
 assert.equal(calls.filter(call=>call==='terminal_open').length,1,'the running shell is reused');
 for(let i=0;i<8;i++)await controller.run(`echo ${i}`,'From a reply');
 assert.equal(controller.snapshot.runs.length,6,'only the latest few blocks are kept');
 controller.dismissRuns();assert.deepEqual(controller.snapshot.runs,[]);
 await assert.rejects(controller.run('   ','x'),/empty or too long/);
 await assert.rejects(controller.run('x'.repeat(4001),'x'),/empty or too long/);
 const before=typed.length;
 await assert.rejects(controller.run('ls\u001b[2K; rm -rf ~','From a reply'),/hidden characters/);
 await assert.rejects(controller.run('echo \u202eevil','From a reply'),/hidden characters/);
 assert.equal(typed.length,before,'nothing typed');
});

test('Run refuses to type into a shell that is busy with something else, and names it (backend review, 23 Sep)',async()=>{
 const typed=[];let foreground='node';
 const controller=new ProjectTerminalController('p',async(operation,args)=>{
  if(operation==='terminal_list')return {sessions:[session('p',{foreground})]};
  if(operation==='terminal_write'){typed.push(Buffer.from(terminalOutput(args.dataBase64)).toString());return {sessionId:'shell-p',sequence:args.sequence,nextInputSeq:args.sequence+1,acceptedBytes:terminalOutput(args.dataBase64).length,pendingInputBytes:0};}
 },()=> 'id-1');
 controller.attach(sink());await controller.load();
 await assert.rejects(controller.run('npm test','From a reply'),/busy with node\. Stop it there or let it finish, then press Run again\./);
 assert.deepEqual(typed,[],'nothing typed into the dev server');
 assert.equal(controller.snapshot.runs.length,0);
 foreground=null;
 await controller.run('npm test','From a reply');await settle(()=>typed.length>0);
 assert.equal(typed.join(''),'npm test\r','at the prompt again, Run types as before');
});

test('Run refuses while the terminal needs attention, rather than typing into an uncertain shell',async()=>{
 const controller=new ProjectTerminalController('p',async operation=>{
  if(operation==='terminal_list')return {sessions:[session()]};
  if(operation==='terminal_write')throw Error('Acknowledgement lost');
 });
 controller.attach(sink());await controller.load();
 await controller.run('ls','From a reply');await settle(()=>controller.snapshot.issue?.action==='input');
 await assert.rejects(controller.run('pwd','From a reply'),/needs attention first/);
 assert.equal(controller.snapshot.runs.length,1,'the refused command isn’t shown as sent');
});

test('the empty-screen line shows only before any shell, never over an ended shell’s output (design review, 23 Sep)',async()=>{
 const {terminalIdle}=await import('./projectTerminalSession.ts');
 const view=(extra={})=>({session:null,busy:null,issue:null,lostOutput:false,queuedBytes:0,runs:[],...extra});
 assert.equal(terminalIdle(view()),true,'nothing started: the screen is empty, so it says what this is');
 assert.equal(terminalIdle(view({session:session('p',{state:'exited',exitCode:1})})),false,'its output is still on screen');
 assert.equal(terminalIdle(view({session:session('p',{state:'closed'})})),false);
 assert.equal(terminalIdle(view({busy:'load'})),false);
 assert.equal(terminalIdle(view({issue:{action:'load',message:'x'}})),false);
});
