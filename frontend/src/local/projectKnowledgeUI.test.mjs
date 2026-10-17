import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','DocumentFragment','KeyboardEvent'])globalThis[key]=dom.window[key];
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
HTMLElement.prototype.scrollIntoView=function(){};
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);
globalThis.cancelAnimationFrame=clearTimeout;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ThemeProvider}=await import('@/shared/theme/ThemeProvider.tsx');
const {ProjectKnowledge,KnowledgeSnapshotsForTask}=await import('./ProjectKnowledge.tsx');
const {RunTold}=await import('./RunContext.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();localStorage.clear();});
const skills={skills:[],archived:[],pending:0,limits:{description:240,body:12000,skills:60},dirs:[]};
/** The page reads the rules, the memory and the skills on open, and records that you looked (knowledge_notes_seen, beta.16);
 * a test's host answers the knowledge calls and these. */
const noFiles=()=>({files:[],looked_for:[],missing:[],limit:32768});
const noNotes={enabled:true,reflect:false,entries:[],used_chars:0,max_chars:12000,max_entries:30};
const answer=(handler,skillList=()=>skills,repoList=noFiles)=>(operation,args)=>{if(operation==='skills_list')return skillList();if(operation==='knowledge_notes_list'||operation==='knowledge_notes_seen')return noNotes;
 if(operation==='knowledge_repo_list')return repoList();return handler(operation,args);};
function host(handler,skillList,repoList){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push({operation,args});return answer(handler,skillList,repoList)(operation,args);}};return calls;}
const mount=(props={})=>render(React.createElement(ThemeProvider,null,React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},React.createElement(ProjectKnowledge,{projectId:'p',channelId:'c',profileId:'account',open:true,onClose:()=>{},...props}))));
const loaded=view=>waitFor(()=>assert.ok(view.queryByLabelText('Add a rule')||view.queryByLabelText('Rule text')));
async function openDraft(view){if(!view.queryByLabelText('Rule text'))fireEvent.click(view.getByRole('button',{name:/^(Add with details|Continue draft)$/}));await view.findByLabelText('Rule text');}
const rulesGroup=view=>view.getByRole('group',{name:'Your rules'});
/** Open a rule's row onto its card. Rows are buttons named by their text. */
const openRule=(view,name)=>{fireEvent.click(within(rulesGroup(view)).getByRole('button',{name}));return view.getByRole('article',{name:/^Rule \d+$/});};
const focusedTitle=()=>document.activeElement?.classList.contains('dv-row-name');
const inUse=(id,text,extra={})=>({id,revision:2,active_version_id:id+'-v1',events:[{kind:'approved',at:'2026-09-13T12:00:00Z',version_id:id+'-v1'}],versions:[{id:id+'-v1',text,provenance:'Added in Dervo.',source:null,created_at:'2026-09-13T12:00:00Z'}],...extra});
const notice=view=>view.container.querySelector('.dv-kn-notice');
const undo=view=>within(notice(view)).getByRole('button',{name:'Undo'});
/** A host that keeps rules the way project_knowledge.py does: versions, revisions and events. */
function ruleStore(rules=[]){
 const find=id=>rules.find(rule=>rule.id===id);
 return {rules,handle(operation,args){
  if(operation==='knowledge_save'){let r=args.ruleId?find(args.ruleId):null;if(!r){r={id:'r'+(rules.length+1),revision:0,active_version_id:null,versions:[],events:[]};rules.push(r);}
   assert.equal(args.expectedRevision,r.revision,'saves check the revision');r.revision++;const v={id:`${r.id}-v${r.versions.length+1}`,text:args.text,provenance:args.provenance,source:args.source,created_at:'2026-09-19T12:00:00Z'};r.versions.push(v);r.events.push({kind:'proposed',version_id:v.id});return structuredClone(r);}
  if(operation==='knowledge_approve'){const r=find(args.ruleId);assert.equal(args.expectedRevision,r.revision,'approvals check the revision');r.active_version_id=args.versionId;r.revision++;r.events.push({kind:'approved',version_id:args.versionId});return structuredClone(r);}
  if(operation==='knowledge_archive'){const r=find(args.ruleId);assert.equal(args.expectedRevision,r.revision);r.events.push({kind:'archived',version_id:null,archived_version_id:r.active_version_id});r.active_version_id=null;r.revision++;return structuredClone(r);}
  if(operation==='knowledge_version_dismiss'){const r=find(args.ruleId);r.revision++;r.events.push({kind:args.dismissed?'dismissed':'reopened',version_id:args.versionId});return structuredClone(r);}
  return undefined;
 }};
}

test('a rule saved with details links an ended task run and is in use at once; its card opens the task',async()=>{
 const store=ruleStore(),opened=[];
 const calls=host((operation,args)=>{if(operation==='knowledge_list')return {rules:structuredClone(store.rules),procedures:[]};const done=store.handle(operation,args);if(done)return done;throw Error(operation);});
 const props={channelId:'',sourceChannelId:'c',sourceTasks:[{id:'task',context:{project_id:'p',thread_id:'c',actor:'local-user'},messages:[{role:'user',text:'Repair search query retention'}],runs:[{id:'finished',state:'completed'},{id:'active',state:'sending'}]}],onOpenTaskSource:(...args)=>opened.push(args)};
 let view=mount(props);await loaded(view);await openDraft(view);
 fireEvent.change(view.getByLabelText('Lesson source task'),{target:{value:JSON.stringify(['task','finished'])}});
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Update visible state before storage.'}});
 fireEvent.change(view.getByLabelText('Where it came from'),{target:{value:'Lesson from the search correction.'}});
 assert.ok(!Array.from(view.getByLabelText('Lesson source task').options).some(option=>option.value.includes('active')),'a run still going is never offered');
 view.unmount();view=mount(props);await loaded(view);await openDraft(view);
 assert.equal(view.getByLabelText('Lesson source task').value,JSON.stringify(['task','finished']));
 assert.equal(view.getByLabelText('Rule text').value,'Update visible state before storage.');
 fireEvent.click(view.getByRole('button',{name:'Save'}));
 const card=await view.findByRole('article',{name:'Rule 1'});
 assert.deepEqual(calls.find(c=>c.operation==='knowledge_save').args.source,{channelId:'c',taskId:'task',runId:'finished'});
 assert.equal(store.rules[0].active_version_id,'r1-v1','saving put it to use (decision 3)');
 assert.ok(calls.findIndex(c=>c.operation==='knowledge_save')<calls.findIndex(c=>c.operation==='knowledge_approve'));
 assert.match(notice(view).textContent,/Added as rule 1\. Every agent here follows it from the next task\./);
 fireEvent.click(within(card).getByRole('button',{name:'Open the task'}));
 assert.deepEqual(opened,[['c','task']]);
 fireEvent.click(within(card).getByRole('button',{name:'Edit'}));
 assert.equal(view.getByLabelText('Lesson source task').value,JSON.stringify(['task','finished']));
});

test('an edit takes effect on save, and Undo puts back the version that was in use',async()=>{
 const store=ruleStore([inUse('r1','Inspect scoped callers')]);
 const calls=host((operation,args)=>{if(operation==='knowledge_list')return {rules:structuredClone(store.rules),procedures:[]};const done=store.handle(operation,args);if(done)return done;throw Error(operation);});
 localStorage.setItem('composer-draft','keep this draft');
 const view=mount();await loaded(view);
 const card=openRule(view,/Inspect scoped callers/);
 assert.match(card.textContent,/In use · version 1 of 1 · added in Dervo · 13 Sep/);
 fireEvent.click(within(card).getByRole('button',{name:'Edit'}));
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Inspect scoped callers and their tests'}});
 fireEvent.click(view.getByRole('button',{name:'Save'}));
 await waitFor(()=>assert.equal(store.rules[0].active_version_id,'r1-v2'));
 await waitFor(()=>assert.match(notice(view).textContent,/Saved\. Rule 1 uses your new version from the next task\./));
 const reopened=view.getByRole('article',{name:'Rule 1'});
 assert.match(reopened.textContent,/Version 2.*Inspect scoped callers and their tests.*In use/s);
 fireEvent.click(undo(view));
 await waitFor(()=>assert.equal(store.rules[0].active_version_id,'r1-v1'));
 await waitFor(()=>assert.equal(notice(view).textContent,'Undone.'));
 assert.equal(store.rules[0].versions.length,2,'both versions stay in the history');
 assert.equal(localStorage.getItem('composer-draft'),'keep this draft');
 assert.ok(calls.every(c=>c.operation.startsWith('knowledge_')||['skills_list','coding_permission'].includes(c.operation)));
});

test('source validation and stale revision errors stay beside the editor and keep the draft',async()=>{
 let saves=0;
 host((operation)=>{if(operation==='knowledge_list')return {rules:[],procedures:[]};saves++;throw Error('Knowledge changed. Reload before saving or approving.');});
 const view=mount();await loaded(view);await openDraft(view);
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Keep my rule'}});
 fireEvent.change(view.getByLabelText('Where it came from'),{target:{value:'Human'}});
 fireEvent.change(view.getByLabelText('Source conversation ID'),{target:{value:'shared'}});
 fireEvent.click(view.getByRole('button',{name:'Save'}));
 await waitFor(()=>assert.match(view.getByRole('alert').textContent,/both shared source IDs/));assert.equal(saves,0);
 fireEvent.change(view.getByLabelText('Source conversation ID'),{target:{value:''}});
 fireEvent.click(view.getByRole('button',{name:'Save'}));
 await waitFor(()=>assert.match(view.getByRole('alert').textContent,/Knowledge changed/));
 assert.equal(view.getByLabelText('Rule text').value,'Keep my rule');
});

test('a pending save clears only the submitted revision, and drafts survive remounts and account switches',async()=>{
 let resolveSave;const calls=[];
 host((operation,args)=>{
  calls.push({operation,args});if(operation==='knowledge_list')return {rules:[],procedures:[]};
  return new Promise(resolve=>{resolveSave=resolve;});
 });
 let view=mount();await loaded(view);await openDraft(view);
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Submitted A'}});
 fireEvent.change(view.getByLabelText('Where it came from'),{target:{value:'Human'}});
 fireEvent.click(view.getByRole('button',{name:'Save'}));
 await waitFor(()=>assert.ok(resolveSave));
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Later B'}});
 resolveSave({id:'r',revision:1,versions:[]});
 await waitFor(()=>assert.equal(view.getByRole('button',{name:'Save'}).disabled,false));
 assert.equal(view.getByLabelText('Rule text').value,'Later B');
 await waitFor(()=>assert.match(notice(view).textContent,/Submitted version saved and in use\. The newer changes in your draft are still unsaved\./));
 view.unmount();view=mount({channelId:'other-channel'});await loaded(view);await openDraft(view);
 assert.equal(view.getByLabelText('Rule text').value,'Later B');
 view.unmount();view=mount({profileId:'other-account'});await loaded(view);await openDraft(view);
 assert.equal(view.getByLabelText('Rule text').value,'');
 view.unmount();view=mount();await loaded(view);await openDraft(view);
 assert.equal(view.getByLabelText('Rule text').value,'Later B');
 assert.equal(calls.find(c=>c.operation==='knowledge_save').args.text,'Submitted A');
});
for(const laterEdit of [true,false])test(`quota failure during a pending save ${laterEdit?'keeps the newer draft in memory':'clears the submitted revision'}`,async()=>{
 let resolveSave;const calls=[];
 host((operation,args)=>{
  calls.push({operation,args});
  return operation==='knowledge_list'?{rules:[],procedures:[]}:new Promise(resolve=>{resolveSave=resolve;});
 });
 const view=mount();await loaded(view);await openDraft(view);
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Submitted A'}});
 fireEvent.change(view.getByLabelText('Where it came from'),{target:{value:'Human'}});
 const key='dervo-knowledge-draft:'+JSON.stringify(['p','account']);
 const persisted=localStorage.getItem(key);
 assert.equal(JSON.parse(persisted).text,'Submitted A');
 fireEvent.click(view.getByRole('button',{name:'Save'}));
 await waitFor(()=>assert.ok(resolveSave));
 const prototype=Object.getPrototypeOf(localStorage),setItem=prototype.setItem;
 let failedWrites=0;
 prototype.setItem=function(){failedWrites++;throw new dom.window.DOMException('Storage full','QuotaExceededError');};
 try{
  if(laterEdit)fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Later B'}});
  resolveSave({id:'r',revision:1,versions:[]});
  await waitFor(()=>assert.ok(failedWrites>0&&view.queryAllByRole('alert').some(node=>/Draft storage is unavailable/.test(node.textContent))));
  assert.equal(view.getByLabelText('Rule text').value,laterEdit?'Later B':'');
  assert.equal(localStorage.getItem(key),persisted);
  assert.equal(calls.find(c=>c.operation==='knowledge_save').args.text,'Submitted A');
 }finally{prototype.setItem=setItem;}
});
test('editing a rule while a draft waits keeps the draft until you discard it',async()=>{
 host(()=>({rules:[inUse('r','Saved version')],procedures:[]}));
 const view=mount();await loaded(view);await openDraft(view);
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Unsaved draft'}});fireEvent.click(view.getByRole('button',{name:'Cancel'}));
 assert.ok(view.getByRole('button',{name:'Continue draft'}),'the draft waits on the add line');
 const card=openRule(view,/Saved version/);
 fireEvent.click(within(card).getByRole('button',{name:'Edit'}));
 const key='dervo-knowledge-draft:'+JSON.stringify(['p','account']);
 assert.equal(JSON.parse(localStorage.getItem(key)).text,'Unsaved draft');
 fireEvent.click(view.getByRole('button',{name:'Keep current draft'}));
 assert.equal(view.getByLabelText('Rule text').value,'Unsaved draft');
 fireEvent.click(within(view.getByRole('article',{name:'Rule 1'})).getByRole('button',{name:'Edit'}));
 fireEvent.click(view.getByRole('button',{name:'Discard draft and edit the rule'}));
 assert.equal(view.getByLabelText('Rule text').value,'Saved version');
});

test('a failed save keeps the draft after closing and switching project or account while pending',async()=>{
 let rejectSave;
 host((operation)=>operation==='knowledge_list'?{rules:[],procedures:[]}:new Promise((_,reject)=>{rejectSave=reject;}));
 let view=mount();await loaded(view);await openDraft(view);
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Keep after failure'}});
 fireEvent.change(view.getByLabelText('Where it came from'),{target:{value:'Human'}});
 fireEvent.click(view.getByRole('button',{name:'Save'}));
 await waitFor(()=>assert.ok(rejectSave));view.unmount();
 view=mount({profileId:'other-account'});await loaded(view);await openDraft(view);
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Other account draft'}});
 rejectSave(Error('Save failed'));await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(view.getByLabelText('Rule text').value,'Other account draft');
 view.unmount();view=mount({projectId:'other-project'});await loaded(view);await openDraft(view);
 assert.equal(view.getByLabelText('Rule text').value,'');
 view.unmount();view=mount();await loaded(view);await openDraft(view);
 assert.equal(view.getByLabelText('Rule text').value,'Keep after failure');
});
test('the record keeps the old per-run disclosure for support, beside collaboration',async()=>{
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_, {operation,args})=>{calls.push({operation,args});return {task:{runs:[{id:'run',knowledge_snapshot:{captured_at:'today',omitted:0,text:'Retire version A. Current rules: []',repository:{text:'Instruction files from this project’s repository'}}}]}};}};
 const view=render(React.createElement(QueryClientProvider,{client},React.createElement(React.Fragment,null,React.createElement('p',null,'Collaboration packet remains'),React.createElement(KnowledgeSnapshotsForTask,{projectId:'p',channelId:'c',taskId:'t'}))));
 await view.findByText('Retire version A. Current rules: []');
 assert.ok(view.getByText('Instruction files from this project’s repository'));
 assert.ok(view.getByText('Collaboration packet remains'));
 assert.deepEqual(calls[0],{operation:'task_detail',args:{projectId:'p',channelId:'c',taskId:'t'}});
 view.unmount();client.clear();
});

test('a save completing after a remount clears the submitted revision in the reopened page',async()=>{
 let resolveSave;
 host((operation)=>operation==='knowledge_list'?{rules:[],procedures:[]}:new Promise(resolve=>{resolveSave=resolve;}));
 let view=mount();await loaded(view);await openDraft(view);
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Submitted before close'}});
 fireEvent.change(view.getByLabelText('Where it came from'),{target:{value:'Human'}});
 fireEvent.click(view.getByRole('button',{name:'Save'}));
 await waitFor(()=>assert.ok(resolveSave));view.unmount();
 view=mount();await loaded(view);await openDraft(view);
 assert.equal(view.getByLabelText('Rule text').value,'Submitted before close');
 resolveSave({id:'r',revision:1,versions:[]});
 await waitFor(()=>assert.equal(view.getByLabelText('Rule text').value,''));
});

test('using an older version confirms in place, focuses Cancel, and Undo goes back',async()=>{
 const versions=[1,2,3].map(n=>({id:'v'+n,text:'Guidance '+n,provenance:'Recorded reason '+n,source:null,created_at:'2026-09-13T12:00:00Z'}));
 const store=ruleStore([{id:'r',revision:5,active_version_id:'v2',versions,events:[{kind:'approved',at:'2026-09-13T12:00:00Z',version_id:'v2'}]}]);
 const calls=host((operation,args)=>{if(operation==='knowledge_list')return {rules:structuredClone(store.rules),procedures:[]};const done=store.handle(operation,args);if(done)return done;throw Error(operation);});
 const view=mount();await loaded(view);
 const card=openRule(view,/Guidance 2/);
 assert.match(within(card).getByLabelText('Versions').textContent,/Version 3.*Saved 13 Sep, not in use.*Version 2.*In use.*Version 1/s);
 fireEvent.click(within(card).getByRole('button',{name:'Use version 1'}));
 const confirm=view.getByRole('alertdialog',{name:'Use version 1'});
 assert.match(confirm.textContent,/Use version 1 from the next task\? Version 2 stays in the history\./);
 await waitFor(()=>assert.equal(document.activeElement?.textContent,'Cancel'),'focus goes to the safe choice');
 assert.equal(calls.some(c=>c.operation==='knowledge_approve'),false,'nothing changes before you confirm');
 fireEvent.keyDown(confirm,{key:'Escape'});
 assert.equal(view.queryByRole('alertdialog'),null,'Esc closes the confirmation first');
 assert.ok(view.getByRole('article',{name:'Rule 1'}),'and the card stays open');
 fireEvent.click(within(view.getByRole('article',{name:'Rule 1'})).getByRole('button',{name:'Use version 1'}));
 fireEvent.click(within(view.getByRole('alertdialog')).getByRole('button',{name:'Use version 1'}));
 await waitFor(()=>assert.equal(store.rules[0].active_version_id,'v1'));
 assert.deepEqual(calls.find(c=>c.operation==='knowledge_approve').args,{projectId:'p',channelId:'c',profileId:'account',ruleId:'r',versionId:'v1',expectedRevision:5});
 await waitFor(()=>assert.match(notice(view).textContent,/Rule 1 now uses version 1\./));
 fireEvent.click(undo(view));
 await waitFor(()=>assert.equal(store.rules[0].active_version_id,'v2'));
});

test('archiving confirms in place with the consequence, and Undo restores the version it had',async()=>{
 const store=ruleStore([inUse('r1','Run backend tests with the framework Python 3.13.'),inUse('r2','No em dashes in any copy.')]);
 const calls=host((operation,args)=>{if(operation==='knowledge_list')return {rules:structuredClone(store.rules),procedures:[]};const done=store.handle(operation,args);if(done)return done;throw Error(operation);});
 const view=mount();await loaded(view);
 const card=openRule(view,/No em dashes/);
 fireEvent.click(within(card).getByRole('button',{name:'Archive'}));
 const confirm=view.getByRole('alertdialog',{name:'Archive rule 2'});
 assert.match(confirm.textContent,/Archive this rule\? New tasks stop getting it\. Its version stays in its history, and you can restore it\./);
 await waitFor(()=>assert.equal(document.activeElement?.textContent,'Keep it'));
 fireEvent.click(view.getByRole('button',{name:'Keep it'}));
 assert.equal(calls.some(c=>c.operation==='knowledge_archive'),false);
 fireEvent.click(within(view.getByRole('article',{name:'Rule 2'})).getByRole('button',{name:'Archive'}));
 fireEvent.click(view.getByRole('button',{name:'Archive rule 2'}));
 await waitFor(()=>assert.equal(store.rules[1].active_version_id,null));
 await waitFor(()=>assert.match(notice(view).textContent,/Rule 2 is archived\. New tasks stop getting it\./));
 assert.equal(within(rulesGroup(view)).queryByText('No em dashes in any copy.'),null);
 assert.ok(view.getByText('Archived · 1'),'it waits under Archived with Restore');
 fireEvent.click(undo(view));
 await waitFor(()=>assert.equal(store.rules[1].active_version_id,'r2-v1'));
});

test('rules open one at a time, Esc closes the open card, and a draft waits as Continue draft',async()=>{
 const calls=[];
 host((operation)=>{calls.push(operation);return {rules:[inUse('r','Use plain language.'),inUse('s','Commit each slice.')],procedures:[]};});
 const closed=[];
 const view=mount({projectName:'Dervo',onClose:()=>closed.push(true)});
 await loaded(view);
 assert.equal(view.queryByRole('article',{name:/^Rule/}),null,'nothing is open until you open it');
 assert.match(within(rulesGroup(view)).getByRole('button',{name:/Use plain language/}).textContent,/^1Use plain language\.13 Sep$/);
 openRule(view,/Use plain language/);
 await waitFor(()=>assert.ok(focusedTitle()));
 openRule(view,/Commit each slice/);
 assert.equal(view.getAllByRole('article',{name:/^Rule/}).length,1);
 fireEvent.keyDown(view.getByRole('article',{name:'Rule 2'}),{key:'Escape'});
 assert.equal(view.queryByRole('article',{name:/^Rule/}),null);
 assert.equal(closed.length,0,'Esc closed the card, not the page');
 fireEvent.click(view.getByRole('button',{name:'Add with details'}));
 await waitFor(()=>assert.equal(document.activeElement?.getAttribute('aria-label'),'Rule text'));
 fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'An unfinished preference'}});
 fireEvent.keyDown(view.getByLabelText('Rule text'),{key:'Escape'});
 assert.equal(view.queryByLabelText('Rule text'),null,'Esc closes the editor and keeps the draft');
 fireEvent.click(view.getByRole('button',{name:'Continue draft'}));
 assert.equal(view.getByLabelText('Rule text').value,'An unfinished preference');
 fireEvent.keyDown(view.getByLabelText('Rule text'),{key:'Escape'});
 fireEvent.keyDown(view.getByRole('heading',{level:1}),{key:'Escape'});
 assert.equal(closed.length,1,'with nothing open, Esc leaves the page');
 assert.deepEqual(calls.filter(c=>c.startsWith('knowledge')&&c!=='knowledge_notes_seen'),['knowledge_list'],'reading changes nothing but when you last looked');
});

const lesson={id:'suggestion',text:'Update visible state before storage.',evidence:'Storage can throw. Browser behavior was not checked.',provider:'codex',run_state:'interrupted',task_state:'blocked',created_at:'2026-09-17T12:52:00Z',source:{channelId:'source',taskId:'task',runId:'run'}};
function lessonHost({rules=[],items=[lesson],enabled=false,fail}={}){
 const store=ruleStore(rules);const state={items:structuredClone(items),enabled,status:{}};
 const calls=host((operation,args)=>{
  if(operation==='knowledge_list')return {rules:structuredClone(store.rules),procedures:[],lesson_suggestions_enabled:state.enabled,suggestions:{items:structuredClone(state.items),limited:false}};
  if(fail&&fail(operation))throw Error('Source is unavailable.');
  if(operation==='knowledge_lessons_set'){state.enabled=args.enabled;return {enabled:state.enabled};}
  if(operation==='knowledge_lesson_keep'){const item=state.items.find(i=>i.id===args.lessonId);state.items=state.items.filter(i=>i!==item);state.status[item.id]='saved';
   return store.handle('knowledge_save',{ruleId:null,expectedRevision:0,text:item.text,provenance:'Suggested by codex during this task. Evidence: '+item.evidence,source:item.source});}
  if(operation==='knowledge_lesson_dismiss'){state.items=state.items.filter(i=>i.id!==args.lessonId);state.status[args.lessonId]='dismissed';return {dismissed:true};}
  if(operation==='knowledge_lesson_reopen'){state.status[args.lessonId]='pending';state.items=[structuredClone(items.find(i=>i.id===args.lessonId)),...state.items];
   for(const r of store.rules)if(r.active_version_id&&r.versions[0].text===lesson.text){r.events.push({kind:'archived',archived_version_id:r.active_version_id});r.active_version_id=null;r.revision++;}return {reopened:true};}
  const done=store.handle(operation,args);if(done)return done;throw Error(operation);
 });
 return {calls,store,state};
}

test('approving a lesson adds it as the next rule in one step, and Undo puts it back in the queue',async()=>{
 const opened=[];const {calls,store}=lessonHost({rules:[inUse('r1','Run the tests.')]});
 const view=mount({onOpenTaskSource:(...args)=>opened.push(args)});
 const card=await view.findByRole('article',{name:'Lesson waiting for you'});
 assert.match(card.textContent,/WhyStorage can throw\. Browser behavior was not checked\.FromCodex, when its run was interrupted · 17 Sep.*BecomesRule 2, for every task here/s);
 assert.equal(view.getByRole('switch',{name:'Save lessons'}).getAttribute('aria-checked'),'false','the switch stays opt-in');
 fireEvent.click(view.getByRole('switch',{name:'Save lessons'}));
 await waitFor(()=>assert.equal(view.getByRole('switch',{name:'Save lessons'}).getAttribute('aria-checked'),'true'));
 await openDraft(view);fireEvent.change(view.getByLabelText('Rule text'),{target:{value:'Keep my unfinished preference'}});
 fireEvent.click(within(card).getByRole('button',{name:'Approve'}));
 await waitFor(()=>assert.match(notice(view).textContent,/Added as rule 2\. Every agent here follows it from the next task\./));
 assert.deepEqual(calls.find(c=>c.operation==='knowledge_lesson_keep').args,{projectId:'p',channelId:'c',profileId:'account',lessonId:'suggestion'});
 assert.deepEqual(calls.find(c=>c.operation==='knowledge_approve').args,{projectId:'p',channelId:'c',profileId:'account',ruleId:'r2',versionId:'r2-v1',expectedRevision:1});
 assert.equal(view.queryByRole('article',{name:'Lesson waiting for you'}),null,'the queue is gone when nothing waits');
 assert.match(within(rulesGroup(view)).getByRole('button',{name:/Update visible state/}).textContent,/Approved just now, from a lesson/);
 assert.equal(JSON.parse(localStorage.getItem('dervo-knowledge-draft:'+JSON.stringify(['p','account']))).text,'Keep my unfinished preference','your draft is untouched');
 fireEvent.click(undo(view));
 await view.findByRole('article',{name:'Lesson waiting for you'});
 assert.deepEqual(calls.find(c=>c.operation==='knowledge_lesson_reopen').args,{projectId:'p',channelId:'c',profileId:'account',lessonId:'suggestion'});
 assert.equal(store.rules[1].active_version_id,null,'the rule it became is out of use again');
});

test('Edit first approves your wording, keeps the agent’s in the history, and needs text',async()=>{
 const {calls,store}=lessonHost();
 const view=mount();
 const card=await view.findByRole('article',{name:'Lesson waiting for you'});
 fireEvent.click(within(card).getByRole('button',{name:'Edit first'}));
 const field=view.getByLabelText('Lesson text');
 await waitFor(()=>assert.ok(document.activeElement===field,`focus is on ${document.activeElement?.tagName}`));
 assert.ok(view.getByText('Edit the lesson, then approve it'));
 fireEvent.change(field,{target:{value:'  '}});
 assert.equal(view.getByRole('button',{name:'Approve this wording'}).disabled,true);
 assert.ok(view.getByText('Write the lesson first.'));
 fireEvent.keyDown(field,{key:'Escape'});
 assert.equal(view.queryByLabelText('Lesson text'),null,'Esc restores the suggestion');
 fireEvent.click(within(view.getByRole('article',{name:'Lesson waiting for you'})).getByRole('button',{name:'Edit first'}));
 fireEvent.change(view.getByLabelText('Lesson text'),{target:{value:'Update visible state first, then storage.'}});
 fireEvent.keyDown(view.getByLabelText('Lesson text'),{key:'Enter',metaKey:true});
 await waitFor(()=>assert.equal(store.rules[0]?.active_version_id,'r1-v2'));
 assert.deepEqual(store.rules[0].versions.map(v=>v.text),['Update visible state before storage.','Update visible state first, then storage.']);
 assert.deepEqual(calls.filter(c=>c.operation.startsWith('knowledge_')&&!['knowledge_list','knowledge_repo_list','knowledge_notes_list','knowledge_notes_seen'].includes(c.operation)).map(c=>c.operation),['knowledge_lesson_keep','knowledge_save','knowledge_approve']);
});

test('a dismissal that fails stays on its card, and a dismissal that works offers Undo',async()=>{
 let rejected=true;const {calls,state}=lessonHost({fail:op=>op==='knowledge_lesson_dismiss'&&rejected});
 const view=mount();const card=await view.findByRole('article',{name:'Lesson waiting for you'});
 fireEvent.click(within(card).getByRole('button',{name:'Dismiss'}));
 await waitFor(()=>assert.match(within(view.getByRole('article',{name:'Lesson waiting for you'})).getByRole('alert').textContent,/Source is unavailable/));
 rejected=false;
 fireEvent.click(within(view.getByRole('article',{name:'Lesson waiting for you'})).getByRole('button',{name:'Dismiss'}));
 await waitFor(()=>assert.match(notice(view).textContent,/Dismissed\. It wasn’t added\./));
 assert.equal(state.status.suggestion,'dismissed');
 assert.equal(calls.some(c=>c.operation==='knowledge_save'||c.operation==='knowledge_approve'),false,'dismissing saves nothing');
 fireEvent.click(undo(view));
 await view.findByRole('article',{name:'Lesson waiting for you'});
 assert.equal(state.status.suggestion,'pending');
});

test('the queue is oldest first with the oldest open; rows open in place, and older suggestions load at the end',async()=>{
 const older={...lesson,id:'older',text:'A still-useful older lesson.',created_at:'2026-09-10T12:00:00Z'};
 const calls=host((operation)=>{
  if(operation==='knowledge_list')return {rules:[],procedures:[],suggestions:{items:[lesson,{...lesson,id:'newest',text:'The newest lesson.',created_at:'2026-09-18T12:00:00Z'}],limited:true,nextCursor:51}};
  if(operation==='knowledge_lesson_list')return {items:[older],limited:false,nextCursor:null};
  throw Error(operation);
 });
 const view=mount();
 const queue=await view.findByTestId('knowledge-queue');
 assert.match(within(queue).getByRole('article').textContent,/Update visible state before storage\./,'the oldest is open');
 fireEvent.click(within(queue).getByRole('button',{name:/The newest lesson/}));
 assert.match(within(queue).getByRole('article').textContent,/The newest lesson\./);
 assert.equal(within(queue).getAllByRole('article').length,1,'one open at a time');
 fireEvent.click(within(queue).getByRole('button',{name:'Show older'}));
 await view.findByText('A still-useful older lesson.');
 assert.equal(within(queue).queryByRole('button',{name:'Show older'}),null);
 assert.deepEqual(calls.find(c=>c.operation==='knowledge_lesson_list').args,{projectId:'p',channelId:'c',profileId:'account',before:51});
});

test('a newer version of a rule waits in the queue until you use it or dismiss it',async()=>{
 const rule=inUse('r1','No em dashes in any copy.');
 rule.versions.push({id:'r1-v2',text:'No em dashes in any copy, commit messages or code comments.',provenance:'Added in Dervo.',source:null,created_at:'2026-09-18T12:00:00Z'});
 rule.events.push({kind:'proposed',version_id:'r1-v2'});rule.revision=3;
 const store=ruleStore([rule]);
 const calls=host((operation,args)=>{if(operation==='knowledge_list')return {rules:structuredClone(store.rules),procedures:[]};const done=store.handle(operation,args);if(done)return done;throw Error(operation);});
 const view=mount();
 const card=await view.findByRole('article',{name:'Rule waiting for you'});
 assert.match(card.textContent,/NowNo em dashes in any copy\./);
 assert.match(within(rulesGroup(view)).getByRole('button',{name:/No em dashes in any copy\./}).textContent,/^1No em dashes in any copy\./,'the rule stays in use on its version');
 fireEvent.click(within(card).getByRole('button',{name:'Dismiss'}));
 await waitFor(()=>assert.match(notice(view).textContent,/Dismissed\. The version in use doesn’t change\./));
 assert.deepEqual(calls.find(c=>c.operation==='knowledge_version_dismiss').args,{projectId:'p',channelId:'c',profileId:'account',ruleId:'r1',versionId:'r1-v2',expectedRevision:3,dismissed:true});
 assert.equal(view.queryByRole('article',{name:'Rule waiting for you'}),null);
 fireEvent.click(undo(view));
 const back=await view.findByRole('article',{name:'Rule waiting for you'});
 fireEvent.click(within(back).getByRole('button',{name:'Approve'}));
 await waitFor(()=>assert.equal(store.rules[0].active_version_id,'r1-v2'));
 await waitFor(()=>assert.match(notice(view).textContent,/Rule 1 now uses version 2\./));
 fireEvent.click(undo(view));
 await waitFor(()=>assert.equal(store.rules[0].active_version_id,'r1-v1'));
});

test('a skill an agent proposed is approved or dismissed from the queue, with Undo',async()=>{
 const proposal={version:1,name:'add-a-bridge-operation',description:'Adding an operation the renderer calls through the bridge',body:'1. Add it to FIELDS.',status:'proposed',origin:'agent',author:'claude',created_at:Math.floor(Date.now()/1000)-1200};
 let listing={...skills,skills:[{id:'s',name:'add-a-bridge-operation',description:proposal.description,revision:1,current:null,proposals:[proposal],uses:0,last_used_at:null,exported:null,archived_at:null,history:[]}],pending:1};
 const calls=host((operation,args)=>{
  if(operation==='knowledge_list')return {rules:[],procedures:[]};
  if(operation==='skills_review'){const s=listing.skills[0];assert.equal(args.expectedRevision,s.revision);
   listing={...listing,skills:[args.decision==='approve'?{...s,revision:s.revision+1,current:{...proposal,status:'approved'},proposals:[]}:args.decision==='reject'?{...s,revision:s.revision+1,proposals:[]}:{...s,revision:s.revision+1,current:null,proposals:[proposal]}]};return listing;}
  throw Error(operation);
 },()=>listing);
 const view=mount();
 const card=await view.findByRole('article',{name:'New skill waiting for you'});
 assert.match(card.textContent,/Proposed by Claude Code · 20 min ago/);
 fireEvent.click(within(card).getByRole('button',{name:'Approve'}));
 await waitFor(()=>assert.match(notice(view).textContent,/add-a-bridge-operation is approved\. Tasks about it get these steps from now on\./));
 assert.equal(view.queryByRole('article',{name:'New skill waiting for you'}),null);
 assert.ok(within(view.getByTestId('skills')).getByText('Adding an operation the renderer calls through the bridge'));
 fireEvent.click(undo(view));
 await view.findByRole('article',{name:'New skill waiting for you'});
 fireEvent.click(within(view.getByRole('article',{name:'New skill waiting for you'})).getByRole('button',{name:'Dismiss'}));
 await waitFor(()=>assert.match(notice(view).textContent,/Dismissed\. It wasn’t added\./));
 assert.deepEqual(calls.filter(c=>c.operation==='skills_review').map(c=>c.args.decision),['approve','reopen','reject']);
});

test('a repository file opens read only; Stop sending confirms in place, and Undo sends it again',async()=>{
 let repository={files:[{path:'AGENTS.md',bytes:4403,lines:40,sha256:'a',modified:1790000000,cut:false,text:'# Agents\n'+'line\n'.repeat(30),sent_bytes:4403,empty:false,stopped:false,changed:false}],
  looked_for:[],missing:['CLAUDE.md','.claude/CLAUDE.md','.claude/rules'],limit:32768};
 const calls=host((operation,args)=>{
  if(operation==='knowledge_list')return {rules:[],procedures:[]};
  if(operation==='knowledge_repo_send'){repository={...repository,files:repository.files.map(f=>({...f,stopped:!args.send}))};return repository;}
  if(operation==='knowledge_repo_open')return {opened:true};
  throw Error(operation);
 },undefined,()=>repository);
 const view=mount();
 const group=await view.findByTestId('repo-files');
 assert.match(group.textContent,/From the repository1 fileRead only\. Change them in your editor\.AGENTS\.md4\.3 KB · every task gets the whole file/);
 assert.match(group.textContent,/No CLAUDE\.md, \.claude\/CLAUDE\.md or \.claude\/rules in this repository\./);
 fireEvent.click(within(group).getByRole('button',{name:/AGENTS\.md/}));
 const card=within(group).getByRole('article',{name:'AGENTS.md'});
 assert.match(card.textContent,/4\.3 KB, 40 lines/);
 fireEvent.click(within(card).getByRole('button',{name:'Show all 40 lines'}));
 assert.ok(within(card).getByRole('button',{name:'Show less'}));
 fireEvent.click(within(card).getByRole('button',{name:'Open in editor'}));
 await waitFor(()=>assert.deepEqual(calls.find(c=>c.operation==='knowledge_repo_open')?.args,{projectId:'p',channelId:'c',profileId:'account',path:'AGENTS.md',how:'editor'}));
 fireEvent.click(within(card).getByRole('button',{name:'Stop sending it'}));
 const confirm=within(card).getByRole('alertdialog',{name:'Stop sending AGENTS.md'});
 assert.match(confirm.textContent,/Stop sending AGENTS\.md\? New tasks won’t get it\. The file isn’t changed\./);
 assert.equal(calls.some(c=>c.operation==='knowledge_repo_send'),false,'nothing changes before you confirm');
 fireEvent.click(within(confirm).getByRole('button',{name:'Stop sending'}));
 await waitFor(()=>assert.match(within(group).getByText(/Not sent/).textContent,/Not sent\. New tasks don’t get it\./));
 assert.ok(within(group).getByRole('button',{name:'Send it again'}));
 fireEvent.click(undo(view));
 await waitFor(()=>assert.equal(repository.files[0].stopped,false));
 assert.deepEqual(calls.filter(c=>c.operation==='knowledge_repo_send').map(c=>c.args.send),[false,true]);
});

test('what a run was told: files with their version, rules by number, notes and skills, from its own record',()=>{
 const rules=[{id:'r1',revision:2,active_version_id:'a',versions:[{id:'a',text:'Run backend tests.'}],events:[]},
  {id:'r3',revision:3,active_version_id:'b1',versions:[{id:'b1',text:'No em dashes in any copy.'},{id:'b2',text:'No em dashes anywhere.'}],events:[]}];
 const run={id:'run',knowledge_snapshot:{captured_at:'2026-09-17T12:38:00Z',text:'RULES_TEXT',omitted:0,profile_id:'p',
  rules:[{rule_id:'r1',version_id:'a',text:'Run backend tests.'},{rule_id:'r3',version_id:'b1',text:'No em dashes in any copy.'}],
  repository:{files:[{path:'AGENTS.md',sha256:'x',bytes:4403,sent_bytes:4403,cut:false,modified:1789550000}],left_out:[{path:'CLAUDE.md',reason:'stopped'}],text:'REPO_TEXT',limit:32768}},
  told:{notes:[{id:'n1',text:'Bryce wants plain words.'}],skills:[]}};
 const view=render(React.createElement(RunTold,{run,provider:'claude',rules,repository:{files:[{path:'AGENTS.md',sha256:'x'}],missing:[],looked_for:[],limit:32768}}));
 const text=view.container.textContent;
 assert.match(text,/before Claude Code started\. It’s what Dervo sent, not proof the agent followed it\./);
 assert.match(text,/AGENTS\.mdThe \d+ Sep version, the whole file · still current/);
 assert.match(text,/Not sent: CLAUDE\.md, because you stopped sending it\./);
 assert.match(text,/Your rules · 2 of 2/);
 assert.match(text,/2No em dashes in any copy\.Version 1\. Version 2 was saved later\./,'rules by their number now, with the version the run had');
 assert.match(text,/Agents’ notes · 1Bryce wants plain words\./);
 assert.match(text,/SkillsNone matched this task\./);
 assert.equal(view.queryByText(/REPO_TEXT/),null);
 fireEvent.click(view.getByRole('button',{name:'Exactly what was sent'}));
 assert.match(view.container.querySelector('.dv-told-sent').textContent,/REPO_TEXT\nRULES_TEXT/,'the files went before the rules');
 view.unmount();
 const old=render(React.createElement(RunTold,{run:{id:'old'}}));
 assert.match(old.container.textContent,/No record for this run\. It ran before Dervo kept one\./);
});

test('a rule whose source no longer checks out says so on its row and card, and Keep it without its source sends it again (beta.11, F3)',async()=>{
 const linked={channelId:'c',messageId:'m1'};
 const store=ruleStore([inUse('r1','Commit each slice with a descriptive message.',{versions:[{id:'r1-v1',text:'Commit each slice with a descriptive message.',provenance:'From a message.',source:linked,created_at:'2026-09-13T12:00:00Z'}]}),
  inUse('r2','Tests run one file at a time.'),inUse('r3','No em dashes in any copy.')]);
 const deliveries=()=>({r1:store.rules[0].versions.at(-1).source?{sent:false,reason:'source_private'}:{sent:true,reason:null},r2:{sent:true,reason:'source_deleted'},r3:{sent:true,reason:null}});
 const calls=host((operation,args)=>{if(operation==='knowledge_list')return {rules:structuredClone(store.rules),procedures:[],deliveries:deliveries()};const done=store.handle(operation,args);if(done)return done;throw Error(operation);});
 const view=mount({channelId:''});await loaded(view);
 const rows=within(rulesGroup(view)).getAllByTestId('rule-row');
 assert.match(rows[0].textContent,/Not sent to tasks: the chat it came from is private now\./);
 assert.match(rows[1].textContent,/The chat it came from was deleted\. Tasks still get it\./);
 assert.doesNotMatch(rows[2].textContent,/sent/,'a rule that simply goes out says nothing');
 const card=openRule(view,/^1Commit each slice/);
 assert.match(within(card).getByTestId('rule-delivery').textContent,/Not sent to tasks: the chat it came from is private now\./);
 fireEvent.click(within(card).getByRole('button',{name:'Keep it without its source'}));
 await waitFor(()=>assert.equal(store.rules[0].active_version_id,'r1-v2'));
 const saved=calls.find(c=>c.operation==='knowledge_save').args;
 assert.deepEqual([saved.ruleId,saved.text,saved.provenance,saved.source],['r1','Commit each slice with a descriptive message.','From a message.',null],'the same words, no source');
 await waitFor(()=>assert.match(notice(view).textContent,/Rule 1 goes out again from the next task, without its source\./));
 await waitFor(()=>assert.doesNotMatch(within(rulesGroup(view)).getAllByTestId('rule-row')[0]?.textContent??view.getByRole('article',{name:'Rule 1'}).textContent,/Not sent/));
 fireEvent.click(undo(view));
 await waitFor(()=>assert.equal(store.rules[0].active_version_id,'r1-v1'),'Undo puts the linked version back in use');
 // Negative control: a deleted source is a note, not a problem, so its card offers nothing to fix.
 const deleted=openRule(view,/^2Tests run one file/);
 assert.match(within(deleted).getByTestId('rule-delivery').textContent,/deleted\. Tasks still get it\./);
 assert.equal(within(deleted).queryByRole('button',{name:'Keep it without its source'}),null);
});

test('what a run was told names why each rule in use was left out, from the record’s reasons (beta.11, F2 and F3)',()=>{
 const rules=[{id:'r1',revision:2,active_version_id:'a',versions:[{id:'a',text:'Run backend tests.'}],events:[]}];
 const snapshot={captured_at:'2026-09-17T12:38:00Z',text:'RULES_TEXT',omitted:2,profile_id:'p',rules:[{rule_id:'r1',version_id:'a',text:'Run backend tests.'}],
  not_sent:[{rule_id:'r2',version_id:'b',reason:'limit'},{rule_id:'r3',version_id:'c',reason:'source_private'}],repository:{files:[],left_out:[],text:'',limit:32768}};
 let view=render(React.createElement(RunTold,{run:{id:'run',knowledge_snapshot:snapshot,told:{notes:[],skills:[]}},provider:'claude',rules}));
 assert.match(view.container.textContent,/Your rules · 1 of 3/);
 assert.match(view.container.textContent,/2 rules weren’t sent: 1 past the 12-rule or 16 KB limit, 1 because the chat it came from is private now\. Fix it in Knowledge\./);
 view.unmount();
 // A work account's chat: every rule left out, and nothing to fix in Knowledge.
 view=render(React.createElement(RunTold,{run:{id:'run',knowledge_snapshot:{...snapshot,rules:[],omitted:1,not_sent:[{rule_id:'r1',version_id:'a',reason:'work_account'}]}},provider:'codex',rules}));
 assert.match(view.container.textContent,/1 rule wasn’t sent: 1 because this chat uses a work account\./);
 assert.doesNotMatch(view.container.textContent,/Fix it in Knowledge/);
 view.unmount();
 // An older record has only the count, and says so the old way; a record with nothing left out says nothing.
 view=render(React.createElement(RunTold,{run:{id:'run',knowledge_snapshot:{...snapshot,not_sent:undefined}},provider:'claude',rules}));
 assert.match(view.container.textContent,/2 left out: past the 12-rule or 16 KB limit, or a source that no longer checks out\./);
 view.unmount();
 view=render(React.createElement(RunTold,{run:{id:'run',knowledge_snapshot:{...snapshot,omitted:0,not_sent:[]}},provider:'claude',rules}));
 assert.doesNotMatch(view.container.textContent,/sent:|left out/);
});
