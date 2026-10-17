import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
const {renderToStaticMarkup}=await import('react-dom/server');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {routeSubmit,packetLabel,roleLabel,decodeSnapshot}=await import('./collaboration.ts');
const {PacketRows,PacketInspector,HelperSource,CollaborationSettings}=await import('./CollaborationUI.tsx');
const {TaskFooter,helperLabel}=await import('./CodingTasks.tsx');
const {composerMode}=await import('./TurnControls.tsx');
after(()=>dom.window.close());

function host(handler){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
const client=()=>new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
const mount=(element,c=client())=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:c},element)));
const conversations=[{id:'main',name:'Engineering',visibility:'shared',project_id:'p',profile_id:'owner',model:'m'},{id:'review-room',name:'Review room',visibility:'shared',project_id:'p',profile_id:'cx',model:'m'},{id:'work-room',name:'Work room',visibility:'shared',project_id:'p',profile_id:'cl',model:'claude-fable-5-1'},{id:'priv',name:'Notes',visibility:'private',project_id:'p',profile_id:'owner',model:'m'}];
const granted={state:'granted'},available=(...names)=>({state:'automatic',available:names.map(n=>({name:n}))});
const packet=(over={})=>({id:'pk1',owner_task_id:'t1',root_task_id:'t1',parent_packet_id:null,depth:1,agent:'reviewer',role:'reviewer',kind:'review',
 source:{project_id:'p',thread_id:'main',task_id:'t1',run_id:'r',token:'tok',profile_id:'owner',model:'m',provider:'codex'},
 destination:{project_id:'p',thread_id:'review-room',profile_id:'cx',model:'m',provider:'codex',agent:'reviewer'},
 reason:'Review greeting.py against test_sample.py',instructions:'Say if greet() matches the test.',files:[{path:'greeting.py',sha256:'aa'},{path:'test_sample.py',sha256:'bb'}],
 payload_text:'Development task. Use only dervo host tools.\nRole: reviewer\nPurpose: Review greeting.py',payload_sha256:'cc',child_task_id:'c1',status:'returned',refusal:null,
 result:{child_state:'answered',summary:'REVIEW: line 1 is fine.',attempt:null,verified:false,test:null,diff:null,after:null,stale:false,changed_since:[]},late_result:null,error:null,
 created_at:1,queued_at:null,started_at:2,returned_at:3,stale:false,changed_since:[],...over});

test('exactly one route per message: named agents reach the coding collaboration policy, unknown targets are refused before sending, and the older paths stay',()=>{
 // Coding allowed and collaboration on here: @names go to coding, never to the text-only harness route.
 assert.deepEqual(routeSubmit('Have @reviewer look at greeting.py',granted,available('reviewer','fixer'),{state:'available',helper:'claude-abc'}),{path:'coding',agents:['reviewer']});
 assert.deepEqual(routeSubmit('Fix greeting.py and run the tests',granted,available('reviewer'),{state:'available',helper:'claude-abc'}),{path:'coding',agents:[]});
 assert.deepEqual(routeSubmit('Ask @Reviewer and @fixer',granted,{state:'mentions',available:[{name:'reviewer'},{name:'fixer'}]},null),{path:'coding',agents:['reviewer','fixer']});
 assert.throws(()=>routeSubmit('Ask @nobody to help',granted,available('reviewer'),{state:'available',helper:'claude-abc'}),/@nobody is not a named agent this conversation may ask\. Named agents: @reviewer\. Nothing was sent\./);
 assert.throws(()=>routeSubmit('Ask @codex-1a2b',granted,available('reviewer'),null),/@codex-1a2b is not a named agent/);
 // Collaboration off, unavailable or private here: the older order, an @handle keeps text-only coordination when it exists.
 for(const state of ['off','unavailable','private','channel_not_allowed']){
  assert.deepEqual(routeSubmit('Ask @codex-1a2b',granted,{state,available:[]},{state:'available',helper:'claude-abc'}),{path:'coordination',agents:[]});
  assert.deepEqual(routeSubmit('Ask @codex-1a2b',granted,{state,available:[]},{state:'unavailable',helper:null}),{path:'coding',agents:[]});
 }
 assert.deepEqual(routeSubmit('plain request',granted,undefined,undefined),{path:'coding',agents:[]});
 // No coding permission: coordination when the host resolved a helper, otherwise chat.
 assert.deepEqual(routeSubmit('hello',{state:'missing'},available('reviewer'),{state:'available',helper:'claude-abc'}),{path:'coordination',agents:[]});
 assert.deepEqual(routeSubmit('hello',{state:'missing'},undefined,{state:'unavailable',helper:null}),{path:'chat',agents:[]});
 assert.deepEqual(routeSubmit('hello',undefined,undefined,undefined),{path:'chat',agents:[]});
});

test('labels come from the packet record and never claim execution or approval',()=>{
 assert.equal(roleLabel('reviewer'),'reviewer, read-only');assert.equal(roleLabel('worker'),'worker, may change files');assert.equal(roleLabel(null),'unknown role');
 assert.equal(packetLabel(packet()),'Returned');
 assert.equal(packetLabel(packet({stale:true,changed_since:['greeting.py']})),'Returned · stale: greeting.py changed since');
 assert.equal(packetLabel(packet({result:{...packet().result,attempt:1,verified:true}})),'Returned, verified by host tests');
 assert.equal(packetLabel(packet({result:{...packet().result,attempt:1,verified:false}})),'Returned, not verified');
 assert.equal(packetLabel(packet({status:'refused',refusal:"Agent 'nobody' is not allowed for this work request"})),"Refused: Agent 'nobody' is not allowed for this work request");
 assert.equal(packetLabel(packet({status:'queued',error:'parallel limit of 3 agents reached'})),'Pending: parallel limit of 3 agents reached');
 assert.equal(packetLabel(packet({status:'running'})),'Running');
 assert.equal(packetLabel(packet({status:'withdrawn',error:'The owner stopped'})),'Withdrawn: The owner stopped');
 assert.equal(packetLabel(packet({status:'disconnected',error:'Host restarted'})),'Disconnected: Host restarted');
 for(const label of ['Executed','Approved','Accepted'])assert.ok(!JSON.stringify(Object.values({queued:'Pending',running:'Running'})).includes(label));
 assert.equal(decodeSnapshot(btoa('def greet(): return "hi"\n')),'def greet(): return "hi"\n');
 assert.equal(decodeSnapshot(btoa('\xff\xfe')),null,'non UTF-8 bytes are reported, not guessed');
 assert.equal(helperLabel({origin:'collaboration',agent:'fixer',role:'worker'}),'Helper @fixer (worker, may change files)');
 assert.equal(helperLabel({origin:'conversation'}),'');
 assert.equal(composerMode({provider:'codex'},{state:'idle',active:false,detail:'',coding:{state:'granted',detail:'',files:[],test_file:null},collaboration:{state:'automatic',available:[{name:'reviewer'},{name:'fixer'}]}}),'Coding in this project · Codex · may ask @reviewer, @fixer');
 assert.equal(composerMode({provider:'codex'},{state:'idle',active:false,detail:'',coding:{state:'granted',detail:'',files:[],test_file:null},collaboration:{state:'off',available:[]}}),'Coding in this project · Codex');
});

test('the source task footer lists actual handoffs: sender, named helper, destination, purpose, status, with navigation and no approval',async()=>{
 const rows=[packet(),packet({id:'pk2',agent:'fixer',role:'worker',kind:'work',destination:{...packet().destination,thread_id:'work-room',provider:'claude',agent:'fixer'},reason:'Add a trailing comment',status:'running',result:null,returned_at:null,child_task_id:'c2'}),
  packet({id:'pk3',agent:'nobody',role:null,kind:null,destination:null,payload_text:null,payload_sha256:null,child_task_id:null,status:'refused',refusal:"Agent 'nobody' is not allowed for this work request",result:null,reason:'help'}),
  packet({id:'pk4',stale:true,changed_since:['greeting.py'],result:{...packet().result,stale:true,changed_since:['greeting.py']}})];
 const calls=host((op,args)=>{if(op==='collab_packets')return rows;throw new Error(op);});
 const seen=[];const navigate={openChannel:id=>seen.push(['channel',id]),openTask:(id,t)=>seen.push(['task',id,t]),inspectPacket:id=>seen.push(['packet',id])};
 const task={id:'t1',state:'working',origin:'conversation',collaboration:{mode:'automatic',agents:['reviewer','fixer'],depth:0}};
 try{
  const view=mount(React.createElement(PacketRows,{task,projectId:'p',channelId:'main',conversations,navigate}));
  await view.findAllByTestId('collab-packet');
  assert.deepEqual(calls[0],{operation:'collab_packets',args:{projectId:'p',channelId:'main',taskId:'t1'}});
  const items=view.getAllByTestId('collab-packet');
  assert.equal(items.length,4);
  assert.ok(items[0].textContent.includes('#Engineering → @reviewer (reviewer, read-only, Codex)'));
  assert.ok(items[0].textContent.includes('in #Review room'));assert.ok(items[0].textContent.includes('Review greeting.py against test_sample.py'));
  assert.equal(items[0].dataset.status,'returned');assert.equal(items[0].dataset.role,'reviewer');
  assert.ok(items[1].textContent.includes('@fixer (worker, may change files, Claude Code)'));assert.ok(items[1].textContent.includes('in #Work room'));assert.ok(items[1].textContent.includes('Running'));
  assert.ok(items[2].textContent.includes("Refused: Agent 'nobody' is not allowed"));assert.ok(items[2].textContent.includes('nowhere: refused before a helper existed'));
  assert.equal(items[2].querySelector('button').textContent,'Request details','a refused packet is inspectable but leads nowhere');
  assert.ok(items[3].textContent.includes('stale: greeting.py changed since'));
  assert.equal(view.queryByRole('button',{name:/approve|accept/i}),null,'no acceptance control in the footer');
  fireEvent.click(items[0].querySelector('button'));fireEvent.click(view.getAllByRole('button',{name:'Request details'})[1]);fireEvent.click(view.getAllByRole('button',{name:'Open helper task'})[1]);
  assert.deepEqual(seen,[['channel','review-room'],['packet','pk2'],['task','work-room','c2']]);
  cleanup();
  const off=mount(React.createElement(TaskFooter,{task:{...task,origin:'collaboration',agent:'fixer',role:'worker',session_id:'s',scope_version:1,files:[],argv:[],binding:{provider:'claude',identity:'i',model:'m',cwd:'/x',visibility:'shared'},messages:[],tokens:[],runs:[]},projectId:'p',channelId:'work-room',onInspect(){}}));
  assert.ok(off.getByTestId('helper-label').textContent.includes('Helper @fixer (worker, may change files)'),'a worker helper is visibly distinct from a read-only reviewer');
 }finally{cleanup();}
});

test('the packet inspector shows the exact sent text and snapshot bytes untruncated, the result separately, provenance, stale warning, plain text, and nothing that approves',async()=>{
 const payload='Development task. Use only dervo host tools.\n'+'x'.repeat(9000)+'\n<script>alert(1)</script>\n**not markdown**\nEND';
 const detail=packet({payload_text:payload,payload_sha256:'deadbeef',stale:true,changed_since:['greeting.py'],
  result:{child_state:'ready_for_review',summary:'FIX: added a comment <b>bold</b>',attempt:2,verified:true,test:{exit_status:0,stopped:null,stderr:'Ran 1 test\nOK'},diff:'--- greeting.py\n+# fixed',after:{'greeting.py':'ee'},stale:true,changed_since:['greeting.py']},
  snapshots:{'greeting.py':{sha256:'aa',bytes_b64:btoa('def greet(): return "hi"\n'+'#'.repeat(5000)+'\n')},'test_sample.py':{sha256:'bb',bytes_b64:btoa('import unittest\n')}}});
 const calls=host((op,args)=>{if(op==='collab_packet')return detail;throw new Error(op);});
 const seen=[];const navigate={openChannel:id=>seen.push(['channel',id]),openTask:(id,t)=>seen.push(['task',id,t]),inspectPacket:()=>{}};
 try{
  const view=mount(React.createElement(PacketInspector,{packetId:'pk1',projectId:'p',channelId:'main',conversations,navigate,onOpenChange(){}}));
  await view.findByTestId('packet-result');
  assert.equal(view.queryByTestId('packet-payload'),null,'exact machine context is available separately from the answer');
  assert.ok(view.getByText('Say if greet() matches the test.'));
  const contextButton=view.getByRole('button',{name:'Sent context'});
  fireEvent.click(contextButton);
  assert.equal(document.activeElement?.textContent,'Sent context');
  await view.findByTestId('packet-payload');
  assert.deepEqual(calls[0],{operation:'collab_packet',args:{projectId:'p',channelId:'main',packetId:'pk1'}});
  const body=document.body;
  assert.equal(body.querySelector('[data-testid=packet-payload]').textContent,payload,'the sent text is shown exactly, not truncated');
  assert.equal(body.querySelector('script'),null,'model text is escaped, never markup');
  assert.ok(body.textContent.includes(`${payload.length} characters · sha256 deadbeef`));
  const snapshots=[...body.querySelectorAll('[data-testid=packet-snapshot]')];
  assert.equal(snapshots.length,2);
  assert.equal(snapshots[0].textContent,'def greet(): return "hi"\n'+'#'.repeat(5000)+'\n','snapshot contents are decoded in full');
  assert.ok(body.textContent.includes('greeting.py · aa'));assert.ok(body.textContent.includes('test_sample.py · bb'));
  assert.ok(body.querySelector('[data-testid=stale-warning]').textContent.includes('Stale review: greeting.py changed since this review was sent'));
  fireEvent.click(view.getByRole('button',{name:'Activity record'}));
  assert.ok(body.textContent.includes('#Engineering · Codex · m · task t1'));assert.ok(body.textContent.includes('@reviewer (reviewer, read-only) in #Review room'));
  assert.ok(body.textContent.includes('Owner instructions'));assert.ok(body.textContent.includes('Say if greet() matches the test.'));
  fireEvent.click(view.getByRole('button',{name:'Overview'}));
  const result=body.querySelector('[data-testid=packet-result]');
  assert.ok(result.textContent.includes('helper ended ready_for_review · attempt 2'));assert.ok(result.textContent.includes('verified by host tests, exit 0'));
  assert.equal(result.querySelector('[data-testid=packet-summary]').textContent,'FIX: added a comment <b>bold</b>');
  assert.equal(result.querySelector('b'),null);
  assert.ok(result.textContent.includes('Ran 1 test'));
  assert.equal(view.queryByRole('button',{name:/approve|accept|reject/i}),null,'reading a packet never approves or rejects');
  assert.ok(body.textContent.includes('Opening or copying this accepts nothing'));
  fireEvent.click(view.getByRole('button',{name:'Open source task'}));fireEvent.click(view.getByRole('button',{name:'Open helper task'}));
  assert.deepEqual(seen,[['task','main','t1'],['task','review-room','c1']]);
  assert.equal(calls.filter(c=>c.operation!=='collab_packet').length,0,'inspection reads; it sends nothing');
  cleanup();
  // The destination helper task shows its source and the request it actually received.
  const helper=mount(React.createElement(HelperSource,{task:{id:'c1',state:'answered',origin:'collaboration',agent:'reviewer',role:'reviewer',packet_id:'pk1'},projectId:'p',channelId:'review-room',conversations,navigate,full:true}));
  await helper.findByTestId('helper-received');
  assert.equal(helper.getByTestId('helper-received').textContent,payload);
  assert.ok(helper.getByTestId('helper-source').textContent.includes('Review greeting.py against test_sample.py'));
  assert.ok(helper.getByTestId('helper-source').textContent.includes('From #Engineering'));
  assert.ok(helper.getByTestId('helper-source').textContent.includes('Any required result approval happens on the source task.'));
  assert.equal(helper.getByTestId('helper-received').closest('details').open,false,'the exact request stays available without overwhelming the reader');
  fireEvent.click(helper.getByRole('button',{name:'Open owner task'}));
  assert.deepEqual(seen.at(-1),['task','main','t1']);
 }finally{cleanup();}
});

test('project collaboration settings: off by default, explicit mode, allowlists and bounded limits, and an agent conversation is created atomically on the named account',async()=>{
 let policy={state:'off',detail:'Collaboration is off for this project.',mode:'off',agents:[],channels:[],limits:{max_parallel:3,max_depth:1,max_calls:6},available:[],unavailable:[]};
 let agents=[];
 const calls=host((op,args)=>{
  if(op==='collab_policy')return policy;
  if(op==='collab_agents')return agents;
  if(op==='create_agent_conversation')return {id:'new-room',name:args.name,visibility:'shared',project_id:args.projectId,profile_id:args.profileId,provider:'claude',model:args.model};
  if(op==='collab_agent_save'){agents=[{name:args.name,profile_id:args.profileId,provider:'claude',model:'claude-fable-5-1',project_id:'p',channel_id:args.channelId,role:args.role,tools:['dervo_read_file'],instructions:args.instructions,created_at:1,actor:'local-user',state:'ready',detail:'ready'}];return agents[0];}
  if(op==='collab_policy_set'){policy={...policy,state:args.mode,mode:args.mode,agents:args.agents,channels:args.channels,limits:{max_parallel:args.maxParallel,max_depth:args.maxDepth,max_calls:args.maxCalls},available:agents};return policy;}
  throw new Error(op);
 });
 const profiles=[{id:'owner',provider:'codex',identity:'me@example.test',category:'personal'},{id:'cl',provider:'claude',identity:'me@example.test',category:'personal'},{id:'work',provider:'codex',identity:'work@example.test',category:'work'}];
 let refreshed=0;
 try{
  const view=mount(React.createElement(CollaborationSettings,{open:true,onOpenChange(){},projectId:'p',channelId:'main',channelName:'Engineering',binding:{provider:'codex',identity:'me@example.test',model:'m',profile_id:'owner',visibility:'shared'},conversations,profiles,boundaries:[{id:'b',profiles:['owner','cl'],created_at:1}],refresh:async()=>{refreshed++;}}));
  await waitFor(()=>assert.ok(view.getByTestId('collab-policy-state').textContent.includes('Now: off')));
  assert.equal(view.getByLabelText(/^Off:/).checked,true,'off by default');
  assert.ok(document.body.textContent.includes('The owner is the conversation you send from: #Engineering · Codex · me@example.test'));
  assert.deepEqual([...view.getByLabelText('Agent account').options].map(o=>o.value),['','owner','cl'],'work accounts are never offered');
  assert.equal(view.queryByLabelText('Allow asking from Notes'),null,'private conversations are never sources');
  // The agent form starts folded; Add agent opens it (Dervo's own redesign, 17 Sep).
  fireEvent.click(view.getByRole('button',{name:'Add agent'}));
  fireEvent.click(view.getByText('Conversations and limits'));
  // A new agent on the Claude account gets its own conversation in one step; nothing else is rebound.
  fireEvent.change(view.getByLabelText('Agent name'),{target:{value:'Reviewer'}});
  fireEvent.change(view.getByLabelText('Agent account'),{target:{value:'cl'}});
  assert.deepEqual([...view.getByLabelText('Agent conversation').options].map(o=>o.value),['','work-room','__new__'],'only shared conversations bound to that account, or a new one');
  fireEvent.change(view.getByLabelText('Agent conversation'),{target:{value:'__new__'}});
  fireEvent.change(view.getByLabelText('New conversation name'),{target:{value:'Review room 2'}});
  fireEvent.change(view.getByLabelText('Agent model'),{target:{value:'claude-fable-5-1'}});
  fireEvent.change(view.getByLabelText('Agent instructions'),{target:{value:'Point at exact lines.'}});
  fireEvent.click(view.getByRole('button',{name:'Save agent'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='collab_agent_save').length,1));
  assert.deepEqual(calls.find(c=>c.operation==='create_agent_conversation').args,{projectId:'p',profileId:'cl',model:'claude-fable-5-1',name:'Review room 2'});
  assert.deepEqual(calls.find(c=>c.operation==='collab_agent_save').args,{name:'reviewer',profileId:'cl',channelId:'new-room',projectId:'p',role:'reviewer',instructions:'Point at exact lines.'});
  assert.equal(calls.filter(c=>['bind_project','create_conversation'].includes(c.operation)).length,0,'no bind_project dance');
  await waitFor(()=>assert.equal(refreshed,1));
  // Edit then Add agent starts a clean form, account included (the reviewer's third finding).
  const editButton=await view.findByRole('button',{name:'Edit @reviewer'});
  fireEvent.click(editButton);
  await waitFor(()=>assert.equal(view.getByLabelText('Agent account').value,'cl'));
  fireEvent.click(view.getByRole('button',{name:'Add agent'}));
  assert.equal(view.getByLabelText('Agent account').value,'');assert.equal(view.getByLabelText('Agent name').value,'');
  // The allowed-agents list shows only once asking for help isn't Off.
  fireEvent.click(view.getByLabelText(/^Automatic:/));
  const allow=await view.findByLabelText('Allow agent reviewer');fireEvent.click(allow);
  fireEvent.change(view.getByLabelText('Agents at once'),{target:{value:'2'}});fireEvent.change(view.getByLabelText('Chain depth'),{target:{value:'2'}});fireEvent.change(view.getByLabelText('Asks per request'),{target:{value:'4'}});
  assert.equal(view.getByLabelText('Allow asking from Engineering').checked,true,'the owner conversation is preselected as a source only in the form; saving is explicit');
  fireEvent.click(view.getByRole('button',{name:'Save collaboration policy'}));
  await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='collab_policy_set').length,1));
  assert.deepEqual(calls.find(c=>c.operation==='collab_policy_set').args,{projectId:'p',channelId:'main',mode:'automatic',agents:['reviewer'],channels:['main'],maxParallel:2,maxDepth:2,maxCalls:4});
  await waitFor(()=>assert.ok(view.getByTestId('collab-policy-state').textContent.includes('Now: automatic')));
  assert.equal(calls.filter(c=>['coding_request','harness_request','task_create'].includes(c.operation)).length,0,'configuration launches nothing');
 }finally{cleanup();}
});

test('the app wires collaboration into the existing shell, and the bridge allows exactly the collaboration operations',()=>{
 const source=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(source,/settingsPage==='helpers'&&settingsProject&&<HelpersPage /);assert.match(source,/PacketInspector/);assert.match(source,/PacketRows/);assert.match(source,/HelperSource/);
 assert.match(source,/extra=\{<>\{collaborationOf\(task,false\)\}<RouteWhy task=\{task\}\/>/);assert.match(source,/extra=\{collaborationOf\(inspectedTask,true\)\} records=\{<KnowledgeSnapshotsForTask projectId=\{projectId\} channelId=\{channelId\} taskId=\{inspectedTask.id\}\/>\}/);
 assert.doesNotMatch(source,/mentionsIn\(text\)/,'the app no longer decides routing inline; routeSubmit owns it');
 assert.doesNotMatch(source,/collab_packet_accept|task_review.*packet/,'no packet-level approval path');
 const bridge=readFileSync(new URL('../../../src/bridge.rs',import.meta.url),'utf8');
 for(const op of ['collab_agents','collab_agent_save','collab_agent_delete','collab_policy','collab_policy_set','collab_packets','collab_packet','create_agent_conversation'])assert.ok(bridge.includes(`"${op}"`),op);
 for(const name of ['BindAgent','CollabRun','CollabApprove','CreateAgentConversationAll'])assert.ok(!bridge.includes(name),`${name} must not be an allowlisted operation`);
});


test('fresh project questions use reading while explicit mentions preserve coordination',()=>{
 const coding={state:'missing',reading:{state:'available'}};
 const harness={state:'available',helper:'owner'};
 assert.equal(routeSubmit('Summarize this project',coding,null,harness).path,'coding');
 assert.equal(routeSubmit('@reviewer help',coding,null,harness).path,'coordination');
 assert.equal(routeSubmit('Summarize this project',{state:'missing',reading:{state:'unavailable'}},null,harness).path,'coordination');
 assert.equal(composerMode({provider:'codex'},{state:'idle',active:false,detail:'',coding}), 'Reading this project · Codex');
});

test('generated reviewer labels hide storage names without changing request bindings',async()=>{
 const {agentLabel,reviewLabel,reviewOverridePayload}=await import('./collaboration.ts');
 const {taskRows}=await import('./timelineRows.ts');
 const name='review-2f70353c3edfea781a387dca';
 assert.equal(agentLabel(name),'Reviewer');
 assert.equal(agentLabel('review-design'),'@review-design');
 const review={enabled:true,reviewer:name,effort:'low',phase:'passed'};
 assert.equal(reviewLabel(review),'Independent review (effort low): passed, no open findings');
 const legacy={...review,phase:'skipped',error:'The owner ended disconnected: Host restarted. No review was sent.'};
 assert.equal(reviewLabel(legacy,'Host restarted'),'Independent review (effort low): skipped: The owner ended disconnected. No review was sent.','a record saved with the task error embedded shows it once, in the task, not here');
 assert.equal(reviewLabel(legacy,'Something else'),'Independent review (effort low): skipped: The owner ended disconnected. No review was sent.','the legacy shape is recognised even after the task error was repaired');
 assert.equal(reviewLabel(legacy),'Independent review (effort low): skipped: The owner ended disconnected. No review was sent.');
 assert.equal(reviewLabel({...legacy,error:'The owner ended blocked. No independent review was sent.'}),'Independent review (effort low): skipped: The owner ended blocked. No independent review was sent.','the new shape is untouched');
 assert.equal(reviewLabel({...legacy,phase:'failed',error:'Reviewer timed out: no reply'}),'Independent review (effort low): failed: Reviewer timed out: no reply','a real review error keeps its colon');
 assert.equal(reviewOverridePayload('on',review).reviewer,name);
 assert.equal(helperLabel({origin:'collaboration',agent:name,role:'reviewer'}),'Helper Reviewer (reviewer, read-only)');
 const original={id:'r',origin:'collaboration',packet_id:'packet',agent:name,role:'reviewer',messages:[{role:'user',text:'Original request',created_at:1}]};
 assert.equal(taskRows([original],'c')[0].content,'Review requested from Reviewer.');
 assert.equal(original.agent,name);
 assert.equal(original.messages[0].text,'Original request');
});
