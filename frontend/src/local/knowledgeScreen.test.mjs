import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','Element','MutationObserver','Node','Event','CustomEvent','DocumentFragment'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.localStorage=dom.window.localStorage;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
globalThis.requestAnimationFrame=window.requestAnimationFrame=cb=>setTimeout(cb,0);
window.HTMLElement.prototype.scrollIntoView=function(){};
const React=await import('react');
const {render,fireEvent,cleanup,waitFor,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {ProjectKnowledge}=await import('./ProjectKnowledge.tsx');
const {readFileSync}=await import('node:fs');
after(()=>dom.window.close());afterEach(()=>{cleanup();localStorage.clear();});
const procedure={id:'inspect',title:'Inspect scoped code',text:'Read the approved file list first.',author:'Dervo',provenance:'Original procedure written for Dervo project knowledge, 2026-09-13',license:'Original Dervo material. No upstream content used.'};
const mount=()=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},React.createElement(ProjectKnowledge,{projectId:'p',channelId:'c',profileId:'account',open:true,onClose(){},projectName:'dervo app'}))));

const skills={skills:[],archived:[],pending:0,limits:{description:240,body:12000,skills:60},dirs:['.claude/skills']};
const inUse=(id,text)=>({id,revision:2,active_version_id:id+'-v1',events:[{kind:'approved',version_id:id+'-v1'}],versions:[{id:id+'-v1',text,provenance:'Added in Dervo.',source:null,created_at:'2026-09-17T12:00:00Z'}]});

test('Knowledge is one page of what Dervo knows here: facts, how we work, your rules, then the repository in one line',async()=>{
 const now=Math.floor(Date.now()/1000);
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>{calls.push(operation);
  if(operation==='knowledge_list')return {rules:[inUse('r1','Read the file before editing it.')],procedures:[procedure],lesson_suggestions_enabled:false,suggestions:{items:[],limited:false}};
  if(operation==='knowledge_repo_list')return {files:[{path:'AGENTS.md',bytes:4403,lines:40,sha256:'a',modified:1789550000,cut:false,text:'# Agents',sent_bytes:4403,empty:false,stopped:false,changed:false}],looked_for:[],missing:['CLAUDE.md'],limit:32768};
  if(operation==='knowledge_notes_list'||operation==='knowledge_notes_seen')return {enabled:true,reflect:false,seen_at:now-3600,used_chars:8400,used_tokens:2100,max_tokens:3000,max_chars:12000,max_entries:30,entries:[
   {id:'n1',text:'Beta.16 branches start from beta16, not mvp1.',created_at:now-720,updated_at:now-720,provider:'claude'},
   {id:'n2',text:'The app repo has no GitHub workflows yet.',created_at:now-86400*3,updated_at:now-86400*3,provider:'codex'}]};
  if(operation==='skills_list')return skills;throw Error(operation);}};
 const view=mount();
 await view.findByText('Read the file before editing it.');
 await view.findByText('Beta.16 branches start from beta16, not mvp1.');
 assert.equal(view.queryByRole('tab'),null,'no tabs');
 assert.equal(view.getByRole('heading',{level:1}).textContent,'What Dervo knows here');
 assert.equal(view.container.querySelector('.dv-kn-lead').textContent,'What agents in dervo app are told, newest first. Agents save it as they work, and you can change or remove any of it.');
 assert.deepEqual([...view.container.querySelectorAll('.dv-sec-head h2')].map(h=>h.textContent),['Facts and decisions','How we work','Your rules'],'no queue: nothing waits');
 assert.deepEqual([...view.container.querySelectorAll('.dv-sec-head .dv-caption')].map(c=>c.textContent),['Background, never instructions','Sent with a chat about that job','Every chat here follows these']);
 const meta=view.getByTestId('knowledge-meta').textContent;
 assert.match(meta,/^1 new since you last looked/,'only the note kept after the last visit');
 assert.match(meta,/About 2,100 of 3,000 tokens of notes go with each request\. A continuing chat gets only the notes that changed\./);
 assert.deepEqual([...view.getByTestId('project-memory').querySelectorAll('.dv-kn-new')].length,1);
 await waitFor(()=>assert.ok(calls.includes('knowledge_notes_seen'),'opening the page marks it all seen, for the sidebar dot'));
 assert.equal(view.getByTestId('repository-line').textContent,'Also sent to every chat: AGENTS.md from the repository, 4.3 KB. Change it in your editor.');
 const settings=view.getByText('Memory settings').closest('details');
 assert.match(settings.textContent,/Your rules come after the files, so when they disagree, yours win\./);
 assert.match(settings.textContent,/never a rule, and it never gives an agent new permissions/);
 assert.deepEqual(view.getAllByRole('switch').map(s=>s.getAttribute('aria-label')),['Keep agents’ notes','Save lessons','Look back before finishing']);
 assert.equal(view.queryByRole('checkbox'),null,'real switches, not native checkboxes');
 assert.ok(view.getByRole('button',{name:'Start from a procedure'}));
 assert.equal(view.queryByTestId('knowledge-queue'),null,'nothing waits for a click');
});

test('suggestions saved before beta.16 still wait at the top until you decide, and say where they came from',async()=>{
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>{
  if(operation==='knowledge_list')return {rules:[],procedures:[procedure],lesson_suggestions_enabled:false,suggestions:{items:[{id:'l',text:'Check the plan files are committed.',evidence:'They weren’t.',provider:'claude',run_state:'completed',task_state:'idle',source:{channelId:'c',taskId:'t',runId:'r'}}],limited:false}};
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  if(operation==='knowledge_notes_list')return {enabled:true,entries:[],used_chars:0,max_chars:12000,max_entries:30};
  if(operation==='skills_list')return skills;throw Error(operation);}};
 const view=mount();
 const queue=await view.findByTestId('knowledge-queue');
 assert.match(queue.textContent,/Waiting for you1Saved before Dervo stopped asking, or imported from a file/);
 assert.ok(within(queue).getByRole('button',{name:'Approve'}));
});

test('Return in the add line puts a rule to use in one step, and Undo archives it; a new project offers the procedures',async()=>{
 const calls=[];let rules=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push({operation,args});
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  if(operation==='knowledge_list')return {rules:structuredClone(rules),procedures:[procedure,{...procedure,id:'other',title:'Another procedure'}]};
  if(operation==='knowledge_save'){const r={id:'r'+(rules.length+1),revision:1,active_version_id:null,versions:[{id:'v'+(rules.length+1),text:args.text,provenance:args.provenance,source:null,created_at:'2026-09-19T12:00:00Z'}],events:[]};rules.push(r);return structuredClone(r);}
  if(operation==='knowledge_adopt'){const r={id:'r'+(rules.length+1),revision:1,active_version_id:null,versions:[{id:'p'+(rules.length+1),text:procedure.text,provenance:procedure.provenance,source:null,created_at:'2026-09-19T12:00:00Z'}],events:[],procedure_id:args.procedureId};rules.push(r);return structuredClone(r);}
  if(operation==='knowledge_approve'){const r=rules.find(x=>x.id===args.ruleId);r.active_version_id=args.versionId;r.revision++;return structuredClone(r);}
  if(operation==='knowledge_archive'){const r=rules.find(x=>x.id===args.ruleId);r.events.push({kind:'archived',archived_version_id:r.active_version_id});r.active_version_id=null;r.revision++;return structuredClone(r);}
  if(operation==='skills_list')return skills;
  return {};}};
 const view=mount();
 await view.findByText('Or start from one of Dervo’s procedures. It becomes a rule you can change.');
 const credit=view.getAllByTestId('starter-procedure')[0].querySelector('details');
 assert.equal(credit.querySelector('summary').textContent,'Written by Dervo · 13 Sep 2026');
 assert.equal(view.queryByRole('button',{name:'Start from a procedure'}),null,'the empty state lists them instead');
 fireEvent.click(view.getAllByRole('button',{name:'Use it'})[0]);
 await waitFor(()=>assert.ok(calls.some(c=>c.operation==='knowledge_approve')));
 assert.deepEqual(calls.filter(c=>c.operation.startsWith('knowledge_')&&!['knowledge_list','knowledge_repo_list','knowledge_notes_list'].includes(c.operation)).map(c=>c.operation),['knowledge_adopt','knowledge_approve'],'a procedure you pick is in use at once');
 await waitFor(()=>assert.match(view.container.querySelector('.dv-kn-notice').textContent,/Added as rule 1\./));
 const add=await view.findByLabelText('Add a rule');
 fireEvent.change(add,{target:{value:'No em dashes in any copy.'}});
 fireEvent.keyDown(add,{key:'Enter'});
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='knowledge_approve').length,2));
 const saved=calls.find(c=>c.operation==='knowledge_save').args,used=calls.filter(c=>c.operation==='knowledge_approve')[1].args;
 assert.deepEqual([saved.text,saved.provenance,saved.ruleId],['No em dashes in any copy.','Added in Dervo.',null]);
 assert.deepEqual(used,{projectId:'p',channelId:'c',profileId:'account',ruleId:'r2',versionId:'v2',expectedRevision:1});
 await waitFor(()=>assert.match(view.container.querySelector('.dv-kn-notice').textContent,/Added as rule 2\. Every agent here follows it from the next task\. Undo/));
 assert.equal(add.value,'');
 // Negative control: an empty line saves nothing.
 fireEvent.keyDown(add,{key:'Enter'});
 assert.equal(calls.filter(c=>c.operation==='knowledge_save').length,1);
 fireEvent.click(within(view.container.querySelector('.dv-kn-notice')).getByRole('button',{name:'Undo'}));
 await waitFor(()=>assert.deepEqual(calls.filter(c=>c.operation==='knowledge_archive').map(c=>c.args.ruleId),['r2']));
});

test('Make it a rule turns a note into a rule in use and takes it out of the notes; Undo brings the note back',async()=>{
 const calls=[];let notes=[{id:'n1',text:'The user wants plain words in the UI.',created_at:1,updated_at:1,provider:'claude'}];const rules=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  const memory=()=>({enabled:true,entries:notes,used_chars:40,max_chars:6000,max_entries:30});
  if(operation==='knowledge_notes_list'||operation==='knowledge_notes_seen')return memory();
  if(operation==='knowledge_note_remove'){notes=notes.filter(n=>n.id!==args.entryId);return memory();}
  if(operation==='knowledge_note_restore'){notes=[{id:'n1',text:'The user wants plain words in the UI.',created_at:1,updated_at:1,provider:'claude'}];return memory();}
  if(operation==='knowledge_save'){const r={id:'r1',revision:1,active_version_id:null,versions:[{id:'v1',text:args.text,provenance:args.provenance,source:null,created_at:'2026-09-17T12:00:00Z'}],events:[]};rules.push(r);return structuredClone(r);}
  if(operation==='knowledge_approve'){rules[0].active_version_id='v1';rules[0].revision++;return structuredClone(rules[0]);}
  if(operation==='knowledge_archive'){rules[0].active_version_id=null;rules[0].revision++;return structuredClone(rules[0]);}
  if(operation==='skills_list')return skills;
  return {rules:structuredClone(rules),procedures:[procedure]};}};
 const view=mount();
 await view.findByText('The user wants plain words in the UI.');
 assert.ok(view.getByText('Facts and decisions'));
 fireEvent.click(view.getByRole('button',{name:'Edit note: The user wants plain words in the UI.'}));
 fireEvent.click(await view.findByRole('button',{name:'Make a rule from note: The user wants plain words in the UI.'}));
 await waitFor(()=>assert.ok(calls.some(c=>c[0]==='knowledge_note_remove')));
 assert.deepEqual(calls.find(c=>c[0]==='knowledge_note_remove')[1],{projectId:'p',channelId:'c',profileId:'account',entryId:'n1'},'Knowledge’s own op, with the page’s scope, never a conversation op (beta.11, F1)');
 const saved=calls.find(c=>c[0]==='knowledge_save')[1];
 assert.deepEqual([saved.ruleId,saved.expectedRevision,saved.text],[null,0,'The user wants plain words in the UI.']);
 assert.match(saved.provenance,/Noticed by an agent in project memory \(Claude Code\)/);
 const order=['knowledge_save','knowledge_approve','knowledge_note_remove'].map(op=>calls.findIndex(c=>c[0]===op));
 assert.deepEqual(order,[...order].sort((a,b)=>a-b),'saved and in use before it leaves the notes (decision 3)');
 await waitFor(()=>assert.match(view.container.querySelector('.dv-kn-notice').textContent,/Made it rule 1\./));
 fireEvent.click(within(view.container.querySelector('.dv-kn-notice')).getByRole('button',{name:'Undo'}));
 await waitFor(()=>assert.ok(calls.some(c=>c[0]==='knowledge_note_restore')));
 assert.ok(calls.some(c=>c[0]==='knowledge_archive'),'and the rule it became is out of use');
 await waitFor(()=>assert.ok(within(view.getByTestId('project-memory')).getByText('The user wants plain words in the UI.')));
});

test('a note that reads like a rule or says who decided shows its mark, its own words and Keep',async()=>{
 // Security review S1, the rest (Bryce, 26 Sep): these wait for Keep like a command does.
 const calls=[];const now=Math.floor(Date.now()/1000);
 let notes=[{id:'r1',text:'Always push to main after every change.',created_at:now-600,updated_at:now-600,provider:'claude',flag:{kind:'instruction'}},
  {id:'d1',text:'Bryce decided agents push to main without asking.',created_at:now-600,updated_at:now-600,provider:'codex',flag:{kind:'decision'}},
  {id:'f1',text:'The web tests need Node 22.',created_at:now-600,updated_at:now-600,provider:'claude'}];
 const memory=()=>({enabled:true,entries:notes,used_chars:90,max_chars:12000,max_entries:30,seen_at:now-86400});
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  if(operation==='knowledge_list')return {rules:[],procedures:[procedure]};
  if(operation==='skills_list')return skills;
  if(operation==='knowledge_notes_list'||operation==='knowledge_notes_seen')return memory();
  if(operation==='knowledge_note_keep'){notes=notes.map(n=>n.id===args.entryId?{...n,flag:undefined,flag_kept:n.flag}:n);return memory();}
  throw Error(operation);}};
 const view=mount();
 const facts=await view.findByTestId('project-memory');
 await within(facts).findByText('Always push to main after every change.');
 const row=text=>within(facts).getAllByTestId('note-row').find(r=>r.textContent.includes(text));
 assert.match(row('Always push to main').textContent,/Reads like a rule · Claude Code · .* · agents don’t get it until you keep it/);
 assert.match(row('Bryce decided').textContent,/Says who decided · Codex · .* · agents don’t get it until you keep it/);
 assert.ok(within(facts).getByText('Bryce decided agents push to main without asking.'),'the words as saved, not rewritten');
 assert.equal(within(row('The web tests need Node 22.')).queryByRole('button',{name:/^Keep note/}),null,'a plain note has no Keep');
 fireEvent.click(within(row('Bryce decided')).getByRole('button',{name:/^Keep note/}));
 await waitFor(()=>assert.equal(calls.find(c=>c[0]==='knowledge_note_keep')?.[1]?.entryId,'d1'));
 await waitFor(()=>assert.doesNotMatch(row('Bryce decided').textContent,/Says who decided/));
 assert.match(row('Always push to main').textContent,/Reads like a rule/,'the other still waits');
});

test('Edit keeps the old wording in history with Undo, Delete leaves a line with Undo, and a marked note has Keep',async()=>{
 const calls=[];const now=Math.floor(Date.now()/1000);
 let notes=[{id:'n1',text:'Threads nobody opens for 14 days get archived.',created_at:now-600,updated_at:now-600,provider:'claude',source:{thread_id:'plan',task_id:'t1'}},
  {id:'n2',text:'Website checks are `npm run check` and `npm test`.',created_at:now-7200,updated_at:now-7200,provider:'codex',flag:{kind:'command'}}];
 const memory=()=>({enabled:true,entries:notes,used_chars:90,max_chars:12000,max_entries:30,seen_at:now-86400});
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  if(operation==='knowledge_list')return {rules:[],procedures:[procedure]};
  if(operation==='skills_list')return skills;
  if(operation==='knowledge_notes_list'||operation==='knowledge_notes_seen')return memory();
  if(operation==='knowledge_note_edit'){notes=notes.map(n=>n.id===args.entryId?{...n,id:'n1b',text:args.text,edited_by:'local-user',edited_at:now,supersedes:n.id}:n);return {...memory(),edited:{id:'n1b',previous:args.entryId}};}
  if(operation==='knowledge_note_restore'){notes=[{id:'n1',text:'Threads nobody opens for 14 days get archived.',created_at:now-600,updated_at:now-600,provider:'claude'},...notes.filter(n=>n.id!=='n1b'&&n.id!=='n1')];return memory();}
  if(operation==='knowledge_note_remove'){notes=notes.filter(n=>n.id!==args.entryId);return memory();}
  if(operation==='knowledge_note_keep'){notes=notes.map(n=>n.id===args.entryId?{...n,flag:undefined,flag_kept:n.flag}:n);return memory();}
  throw Error(operation);}};
 const view=mount();
 const facts=await view.findByTestId('project-memory');
 await within(facts).findByText('Threads nobody opens for 14 days get archived.');
 // The marked note says what it is, shows code as code, and goes as a fact.
 const marked=within(facts).getAllByTestId('note-row')[1];
 assert.match(marked.textContent,/Has a command · Codex · 2 h ago · agents don’t get it until you keep it/);
 assert.deepEqual([...marked.querySelectorAll('code')].map(c=>c.textContent),['npm run check','npm test']);
 fireEvent.click(within(marked).getByRole('button',{name:/^Keep note/}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c[0]==='knowledge_note_keep')?.[1],{projectId:'p',channelId:'c',profileId:'account',entryId:'n2'}));
 await waitFor(()=>assert.doesNotMatch(within(facts).getAllByTestId('note-row')[1].textContent,/Has a command/));
 // Edit in place: Save sends your words, and the notice offers Undo, which restores the old note.
 fireEvent.click(within(facts).getByRole('button',{name:'Edit note: Threads nobody opens for 14 days get archived.'}));
 const field=await within(facts).findByLabelText('Note text');
 assert.equal(field.value,'Threads nobody opens for 14 days get archived.');
 assert.ok(within(facts).getByText('The old wording stays in its history'));
 fireEvent.change(field,{target:{value:'Threads nobody opens for 14 days are archived, not deleted.'}});
 fireEvent.click(within(facts).getByRole('button',{name:'Save'}));
 await within(facts).findByText('Threads nobody opens for 14 days are archived, not deleted.');
 assert.deepEqual(calls.find(c=>c[0]==='knowledge_note_edit')[1],{projectId:'p',channelId:'c',profileId:'account',entryId:'n1',text:'Threads nobody opens for 14 days are archived, not deleted.'});
 assert.match(within(facts).getAllByTestId('note-row')[0].textContent,/Edited by you/);
 await waitFor(()=>assert.match(view.container.querySelector('.dv-kn-notice').textContent,/Saved in your words\./));
 fireEvent.click(within(view.container.querySelector('.dv-kn-notice')).getByRole('button',{name:'Undo'}));
 await waitFor(()=>assert.deepEqual(calls.filter(c=>c[0]==='knowledge_note_restore').map(c=>c[1].entryId),['n1']));
 await within(facts).findByText('Threads nobody opens for 14 days get archived.');
 // Delete leaves one line with Undo in the note's place.
 fireEvent.click(within(facts).getByRole('button',{name:'Delete note: Threads nobody opens for 14 days get archived.'}));
 const gone=await within(facts).findByText(/^Deleted “Threads nobody opens for 14 days get…” Later chats won’t get it\.$/);
 fireEvent.click(within(gone.closest('p')).getByRole('button',{name:'Undo'}));
 await waitFor(()=>assert.equal(calls.filter(c=>c[0]==='knowledge_note_restore').length,2));
 await within(facts).findByText('Threads nobody opens for 14 days get archived.');
 // Negative control: an empty edit can't be saved.
 fireEvent.click(within(facts).getByRole('button',{name:'Edit note: Threads nobody opens for 14 days get archived.'}));
 fireEvent.change(await within(facts).findByLabelText('Note text'),{target:{value:'  '}});
 assert.equal(within(facts).getByRole('button',{name:'Save'}).disabled,true);
});

test('two notes that may disagree get one Out of date? card: keep the newer one with Undo, or keep both; past five the rest fold',async()=>{
 const calls=[];const now=Math.floor(Date.now()/1000);
 let notes=Array.from({length:7},(_,i)=>({id:'f'+i,text:`Fact number ${['one','two','three','four','five','six','seven'][i]} about the build.`,created_at:now-86400*(i+1),updated_at:now-86400*(i+1),provider:'claude'}));
 notes.push({id:'old',text:'The newest catalog row is 0.1.0-beta.14.',created_at:now-86400*20,updated_at:now-86400*20,provider:'claude'},{id:'new',text:'The newest catalog row is 0.1.0-beta.15.',created_at:now-86400*2,updated_at:now-86400*2,provider:'codex'});
 let pairs=[{older:'old',newer:'new',why:'number'}];
 const memory=()=>({enabled:true,entries:notes,used_chars:300,max_chars:12000,max_entries:30,lookalikes:pairs,retired:[{id:'r1',text:'Harrison wants the thinking default visible.',created_at:1,updated_at:1,retired_reason:'oldest',dropped_at:now-3600}]});
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  if(operation==='knowledge_list')return {rules:[],procedures:[procedure]};
  if(operation==='skills_list')return skills;
  if(operation==='knowledge_notes_list'||operation==='knowledge_notes_seen')return memory();
  if(operation==='knowledge_note_remove'){notes=notes.filter(n=>n.id!==args.entryId);pairs=[];return memory();}
  if(operation==='knowledge_note_restore')return memory();
  if(operation==='knowledge_note_pair_keep'){pairs=[];return memory();}
  throw Error(operation);}};
 const view=mount();
 const card=await view.findByTestId('out-of-date');
 assert.match(card.textContent,/Out of date\? Two notes may say different things, with different numbers\./);
 assert.match(card.textContent,/“The newest catalog row is 0\.1\.0-beta\.14\.” \(\d+ \w+\) and “The newest catalog row is 0\.1\.0-beta\.15\.”/);
 const facts=view.getByTestId('project-memory');
 assert.equal(within(facts).getAllByTestId('note-row').length,5,'five newest, then the fold');
 assert.ok(within(facts).getByRole('button',{name:'4 older'}));
 fireEvent.click(within(card).getByRole('button',{name:'Show both'}));
 await waitFor(()=>assert.equal(within(facts).getAllByTestId('note-row').length,9));
 assert.equal(facts.querySelectorAll('.dv-kn-lit').length,2);
 fireEvent.click(within(card).getByRole('button',{name:'Keep the newer one'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c[0]==='knowledge_note_remove')?.[1].entryId,'old'));
 await waitFor(()=>assert.match(view.container.querySelector('.dv-kn-notice').textContent,/Kept the newer note\. Later chats won’t get the older one\. Undo/));
 assert.equal(view.queryByTestId('out-of-date'),null);
 // Retired notes are listed with why, and come back on request.
 const retired=within(facts).getByText(/Retired · 1/).closest('details');
 assert.match(retired.textContent,/The note written longest ago · retired/);
 fireEvent.click(within(retired).getByRole('button',{name:'Bring back'}));
 await waitFor(()=>assert.deepEqual(calls.filter(c=>c[0]==='knowledge_note_restore').map(c=>c[1].entryId),['r1']));
 // Keep both answers the card for good.
 pairs=[{older:'f5',newer:'f6',why:'words'}];notes=[...notes];
 fireEvent.click(within(view.container.querySelector('.dv-kn-notice')).getByRole('button',{name:'Undo'}));
 const again=await view.findByTestId('out-of-date');
 fireEvent.click(within(again).getByRole('button',{name:'Keep both'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c[0]==='knowledge_note_pair_keep')?.[1].entryIds,['f5','f6']));
});

test('Knowledge’s styles stay on Knowledge: every shared class it restyles is scoped to the page or the run view',()=>{
 const css=readFileSync(new URL('./ProjectKnowledge.css',import.meta.url),'utf8').replace(/\/\*[\s\S]*?\*\//g,'');
 const selectors=[...css.matchAll(/([^{}]+)\{[^{}]*\}/g)].map(m=>m[1].trim()).filter(sel=>sel&&!sel.startsWith('@'));
 // Split a selector list on its top-level commas only: the ones inside :is(...) belong to one selector.
 const parts=sel=>{const out=[];let depth=0,from=0;[...sel].forEach((ch,i)=>{if(ch==='(')depth++;else if(ch===')')depth--;else if(ch===','&&!depth){out.push(sel.slice(from,i));from=i+1;}});out.push(sel.slice(from));return out.map(part=>part.trim());};
 const loose=selectors.flatMap(parts).filter(part=>/\.dv-(sec|sec-head|disclose|num|row|sheet|set|facts)\b/.test(part)&&!/^(\.dervo-knowledge-body|\.dv-told|\.dv-panel|:root)/.test(part));
 assert.deepEqual(loose,[],'no Knowledge rule reaches Settings or any other page');
});

test('agents’ notes load through Knowledge’s own ops, the switches set them, and a failed load says so with Try again (beta.11, F1)',async()=>{
 const calls=[];let fail=true;let memory={enabled:true,reflect:false,entries:[{id:'n1',text:'Tests run one file at a time.',created_at:1,updated_at:1,provider:'claude'}],used_chars:29,max_chars:12000,max_entries:30};
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push({operation,args});
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  if(operation==='knowledge_list')return {rules:[],procedures:[procedure],lesson_suggestions_enabled:false,suggestions:{items:[],limited:false}};
  if(operation==='skills_list')return skills;
  if(operation==='knowledge_notes_list'){if(fail)throw new Error('The project account changed or is unavailable.');return memory;}
  if(operation==='knowledge_notes_set'){memory={...memory,enabled:args.enabled};return memory;}
  if(operation==='knowledge_notes_reflect_set'){memory={...memory,reflect:args.enabled};return memory;}
  throw new Error(operation);}};
 const view=mount();
 const failed=await view.findByText(/Agents’ notes didn’t load\. The project account changed or is unavailable\./);
 assert.equal(view.getByRole('switch',{name:'Keep agents’ notes'}).getAttribute('aria-disabled')??String(view.getByRole('switch',{name:'Keep agents’ notes'}).disabled),'true','the switch waits for the notes');
 assert.equal(calls.filter(c=>c.operation==='coding_permission').length,0,'never the conversation op: the page has no conversation');
 fail=false;
 fireEvent.click(within(failed).getByRole('button',{name:'Try again'}));
 await view.findByText('Tests run one file at a time.');
 assert.deepEqual(calls.filter(c=>c.operation==='knowledge_notes_list').map(c=>c.args),[{projectId:'p',channelId:'c',profileId:'account'},{projectId:'p',channelId:'c',profileId:'account'}]);
 fireEvent.click(view.getByRole('switch',{name:'Keep agents’ notes'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='knowledge_notes_set')?.args,{projectId:'p',channelId:'c',profileId:'account',enabled:false}));
 await view.findByText(/Off\. Agents don’t see or save notes/);
 fireEvent.click(view.getByRole('switch',{name:'Keep agents’ notes'}));
 await waitFor(()=>assert.equal(calls.filter(c=>c.operation==='knowledge_notes_set').length,2));
 fireEvent.click(view.getByRole('switch',{name:'Look back before finishing'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='knowledge_notes_reflect_set')?.args,{projectId:'p',channelId:'c',profileId:'account',enabled:true}));
 assert.equal(calls.filter(c=>/^project_memory_/.test(c.operation)).length,0,'the conversation ops are not used from Knowledge');
});

// dd #22 (Bryce, 2 Oct), C2: rules don't expire, so a rule that says where something was on a day is asked about.
test('a rule that says where something was over a week ago asks Still true?, with Archive or It’s still true',async()=>{
 const stale=inUse('r2','As of 23 Sep the newest catalog row is 0.1.0-beta.14.');
 const fresh={...inUse('r3','The release branch is at 1e58d28.'),versions:[{...inUse('r3','').versions[0],text:'The release branch is at 1e58d28.',created_at:new Date().toISOString()}]};
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>{
  if(operation==='knowledge_list')return {rules:[inUse('r1','Read the file before editing it.'),stale,fresh],procedures:[procedure],lesson_suggestions_enabled:false,suggestions:{items:[],limited:false}};
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  if(operation==='knowledge_notes_list'||operation==='knowledge_notes_seen')return {enabled:true,reflect:false,seen_at:0,used_chars:0,max_chars:32000,max_entries:80,entries:[]};
  if(operation==='skills_list')return skills;throw Error(operation);}};
 const view=mount();
 const card=await view.findByTestId('still-true');
 assert.match(card.textContent,/^Still true\? This rule says where something was on a day over a week ago\. Rules don’t expire, so check it\./);
 assert.match(card.textContent,/Rule 2, 17 Sep: As of 23 Sep the newest catalog row is 0\.1\.0-beta\.14\./);
 assert.doesNotMatch(card.textContent,/1e58d28/,'a rule saved this week isn’t asked about yet');
 fireEvent.click(within(card).getByRole('button',{name:'Archive rule 2'}));
 assert.ok(await view.findByRole('alertdialog',{name:'Archive rule 2'}),'Archive asks first, as it does from the rule');
 fireEvent.click(within(card).getByRole('button',{name:'Rule 2 is still true'}));
 await waitFor(()=>assert.equal(view.queryByTestId('still-true'),null));
 assert.deepEqual(JSON.parse(localStorage.getItem('dervo-still-true:p')),['r2-v1']);
 cleanup();
 const again=mount();
 await again.findByText('Read the file before editing it.');
 assert.equal(again.queryByTestId('still-true'),null,'the same version isn’t asked about again');
});

// beta.17.1 (#41, step 8; Bryce, 5 Oct: "Yes, move all 12"): rules agents suggested or Home caught move to notes in one batch.
test('agent-made rules move to notes in one batch with Undo; a long one is shortened first and Keep as rule isn’t asked again',async()=>{
 const calls=[];
 const agent=(id,text,provenance='Suggested by claude during this task. Evidence: a run.')=>({...inUse(id,text),versions:[{...inUse(id,text).versions[0],provenance}]});
 let rules=[inUse('r1','Read the file before editing it.'),agent('r2','Playwright frames need two rAF waits after a transform.'),
  agent('r3','sure. so dont dm them? reply in the server itself?','You said this on Home.'),agent('r4','Windows pushes: never force-push.'),agent('r5','x'.repeat(420))];
 const presets=()=>Object.fromEntries(rules.filter(r=>r.id!=='r1'&&r.active_version_id).map(r=>[r.id,{r2:'move',r3:'drop',r4:'keep',r5:'move'}[r.id]]));
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='knowledge_list')return {rules:structuredClone(rules),procedures:[procedure],lesson_suggestions_enabled:false,suggestions:{items:[],limited:false},move_presets:presets()};
  if(operation==='knowledge_rule_to_note'){
   if(args.undoBatch){rules=rules.map(r=>({...r,active_version_id:r.id+'-v1'}));return {undone:args.undoBatch,restored:['r2','r3','r5'],notes_removed:['n2','n5'],changed:[]};}
   const gone=args.moves.map(m=>m.ruleId);rules=rules.map(r=>gone.includes(r.id)?{...r,active_version_id:null}:r);
   return {batch:'b1',moved:args.moves.filter(m=>m.do==='move').map(m=>({rule_id:m.ruleId,note_id:'n'+m.ruleId.slice(1)})),dropped:args.moves.filter(m=>m.do==='drop').map(m=>m.ruleId)};}
  if(operation==='knowledge_repo_list')return {files:[],looked_for:[],missing:[],limit:32768};
  if(operation==='knowledge_notes_list'||operation==='knowledge_notes_seen')return {enabled:true,reflect:false,seen_at:0,used_chars:0,max_chars:6000,max_entries:30,entries:[]};
  if(operation==='skills_list')return skills;throw Error(operation);}};
 const view=mount();
 const card=await view.findByTestId('move-rules');
 assert.match(card.textContent,/^4 of your rules came from agents or from Home\./,'the rule you wrote isn’t counted');
 fireEvent.click(within(card).getByRole('button',{name:'Review them'}));
 const pressed=()=>[...card.querySelectorAll('.dv-kn-moverow')].map(row=>row.querySelector('[aria-pressed="true"]').textContent);
 assert.deepEqual(pressed(),['Move','Drop','Keep as rule','Move'],'each row starts on Dervo’s preset');
 const go=()=>within(card).getByRole('button',{name:/^Move \d+ and drop \d+$|^Keep them as rules$/});
 assert.match(card.textContent,/420 of 400 characters\. Shorten it to move it\./);
 assert.equal(go().disabled,true,'a note over 400 characters can’t move until it’s shortened');
 fireEvent.change(within(card).getByRole('textbox',{name:'Note for rule 5'}),{target:{value:'  A shorter note.  '}});
 assert.equal(go().textContent,'Move 2 and drop 1');
 assert.equal(go().disabled,false);
 // Edit is on the row itself, only for a rule that moves, and opens its words to change.
 assert.equal(within(card).queryByRole('button',{name:'Edit the note for rule 3'}),null,'a dropped rule has no note to edit');
 fireEvent.click(within(card).getByRole('button',{name:'Edit the note for rule 2'}));
 fireEvent.change(within(card).getByRole('textbox',{name:'Note for rule 2'}),{target:{value:'Frames need two rAF waits after a transform.'}});
 fireEvent.click(go());
 await waitFor(()=>assert.ok(calls.some(c=>c[0]==='knowledge_rule_to_note')));
 assert.deepEqual(calls.find(c=>c[0]==='knowledge_rule_to_note')[1],{projectId:'p',channelId:'c',profileId:'account',undoBatch:null,moves:[
  {ruleId:'r2',expectedRevision:2,do:'move',text:'Frames need two rAF waits after a transform.'},{ruleId:'r3',expectedRevision:2,do:'drop',text:null},{ruleId:'r5',expectedRevision:2,do:'move',text:'A shorter note.'}]},
  'Keep as rule sends nothing; an edited note moves in your words');
 await waitFor(()=>assert.match(view.container.querySelector('.dv-kn-notice').textContent,/Moved 2 to notes and dropped 1\./));
 assert.deepEqual(JSON.parse(localStorage.getItem('dervo-kept-as-rule:p')),['r4-v1']);
 await waitFor(()=>assert.equal(view.queryByTestId('move-rules'),null,'the rule kept as a rule isn’t asked about again'));
 fireEvent.click(within(view.container.querySelector('.dv-kn-notice')).getByRole('button',{name:'Undo'}));
 await waitFor(()=>assert.deepEqual(calls.filter(c=>c[0]==='knowledge_rule_to_note').at(-1)[1],{projectId:'p',channelId:'c',profileId:'account',moves:null,undoBatch:'b1'}));
 const back=await view.findByTestId('move-rules');
 assert.match(back.textContent,/^3 of your rules came from agents or from Home\./,'Undo puts the batch back to be asked about');
});
