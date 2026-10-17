import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLTextAreaElement','HTMLFormElement','Node','Event','MouseEvent','KeyboardEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,cleanup,screen,waitFor}=await import('@testing-library/react');
const {RememberedLines,rememberedItems,rememberedSummary,nowState,forgetMemoryReads}=await import('./RememberedLine.tsx');
after(()=>dom.window.close());afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;forgetMemoryReads();});

// The run records project_memory.py and project_skills.py write (b16/memory), for the reply at 2:44 in the mock.
const run={id:'run-1',remembered:[
 {action:'dropped',entry_id:'old9',text:'Beta.14 is the newest catalog row.',for_entry_id:'n1',reason:'merged',merged_into:'n1',at:1},
 {action:'add',entry_id:'n1',text:'The ideas listing already knows which files each running thread edits.',at:1},
 {action:'add',entry_id:'n2',text:'Run node --test frontend/projectRoom.test.mjs after changing ProjectRoom.',flag:{kind:'command'},at:2},
 {action:'supersede',entry_id:'n3',text:'The web tests need Node 22.',superseded_id:'n0',previous:'The web tests need Node 20.',at:3},
 {action:'add',entry_id:'n4',text:'Harrison wants the thinking default visible.',kind:'lesson',at:4},
 {action:'duplicate',entry_id:'n1',text:'The ideas listing already knows which files each running thread edits.',at:5},
 {action:'withheld',what:'note',at:6},
 {action:'refused',text:'A note that couldn’t fit.',reason:'Project memory is full.',at:7}],
 skills_proposed:[{name:'check-overlaps',version:1,skill_id:'s1',description:'Check which files running threads share from Dervo’s records.',applied:true,at:8},
  {name:'old-proposal',version:1,at:1}]};
const task={id:'t1',runs:[{id:'run-0',remembered:[{action:'add',entry_id:'x',text:'An earlier run’s note.'}]},run],binding:{profile_id:'claude-1'}};

test('the record becomes one line per thing kept, in order, with what each one was',()=>{
 const items=rememberedItems(task);
 assert.deepEqual(items.map(item=>[item.kind,item.text.slice(0,24)]),[
  ['retired','Beta.14 is the newest ca'],['note','The ideas listing alread'],['flagged','Run node --test frontend'],['note','The web tests need Node '],
  ['lesson','Harrison wants the think'],['withheld','Didn’t save a note: it l'],['refused','A note that couldn’t fit'],['skill','Check which files runnin']]);
 assert.equal(items.find(item=>item.kind==='skill').skill.name,'check-overlaps');
 assert.equal(items.some(item=>item.text.includes('old-proposal')),false,'a skill that only waited for review before beta.16 is not a New skill');
 assert.deepEqual(rememberedItems(task,'run-0').map(item=>item.text),['An earlier run’s note.'],'the reply’s own run');
 assert.deepEqual(rememberedItems({runs:[]}),[]);
});

// #41, direction A (Bryce, 5 Oct): the lines under every reply felt chunky. One line of counts, the first naming its noun.
test('the summary counts what the run did, and how many marked notes wait for you',()=>{
 assert.deepEqual(rememberedSummary(rememberedItems(task)),{text:'Kept 3 notes, updated 1, retired 1, a new skill, didn’t save 2',waits:1});
 const only=action=>rememberedSummary(rememberedItems({runs:[{remembered:[action]}]}));
 assert.deepEqual(only({action:'supersede',entry_id:'b',text:'B.',superseded_id:'a',previous:'A.'}),{text:'Updated 1 note',waits:0});
 assert.deepEqual(only({action:'dropped',entry_id:'a',text:'A.',reason:'oldest'}),{text:'Retired 1 note',waits:0});
 assert.deepEqual(only({action:'remove',entry_id:'a',text:'A.'}),{text:'Forgot 1 note',waits:0});
 assert.equal(rememberedSummary(rememberedItems(task),0).waits,0,'a mark kept since doesn’t wait');
 // Negative control: a run that kept nothing, or only met a note it already had, has no line.
 assert.equal(only({action:'duplicate',entry_id:'a',text:'A.'}),null);
});

// The last change sent to the host. After each one the rows read memory again, so reads are skipped.
const acted=calls=>calls.filter(([op])=>op!=='knowledge_notes_list').at(-1);

function host(){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='skills_list')return {skills:[{id:'s1',revision:3}],archived:[]};
  // Memory as it is now: every note this run kept is still there as it was kept.
  if(operation==='knowledge_notes_list')return {entries:['n1','n3','n4','x'].map(id=>({id,text:id})).concat([{id:'n2',text:'n2',flag:{kind:'command'}}])};
  return {entries:[]};}};
 return calls;
}
const rows=()=>[...document.querySelectorAll('.dv-rem-row')].map(row=>[row.querySelector('.dv-rem-kind').textContent,row.querySelector('.dv-rem-said').textContent,
 row.querySelector('.dv-rem-acts').textContent]);

test('one line with Show; opened, one row per note with what waits for you first, and the same actions as before',async()=>{
 const calls=host();
 const opened=[];
 render(React.createElement(RememberedLines,{task,projectId:'p',channelId:'c',projectProfileId:'owner',onOpenKnowledge:()=>opened.push(1)}));
 assert.equal(document.querySelector('.dv-rem-line').textContent,'Kept 3 notes, updated 1, retired 1, a new skill, didn’t save 2 · 1 waits for youShow');
 assert.equal(document.querySelectorAll('.dv-rem-row').length,0,'closed, nothing else under the reply');
 const show=screen.getByRole('button',{name:'Show'});
 assert.equal(show.getAttribute('aria-expanded'),'false');
 fireEvent.click(show);
 assert.equal(screen.getByRole('button',{name:'Hide'}).getAttribute('aria-expanded'),'true');
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='knowledge_notes_list')));
 assert.deepEqual(rows(),[
  ['Waits for you','Run node --test frontend/projectRoom.test.mjs after changing ProjectRoom.','KeepRemove'],
  ['Kept','The ideas listing already knows which files each running thread edits.','EditUndo'],
  ['Updated','The web tests need Node 22.','EditUndo'],
  ['Lesson','Harrison wants the thinking default visible.','EditUndo'],
  ['New skill','Check which files running threads share from Dervo’s records.','EditUndo'],
  ['Retired','Beta.14 is the newest catalog row.','Undo'],
  ['Didn’t save','A note that couldn’t fit.',''],
  ['Didn’t save','Didn’t save a note: it looked like a key or password.','']]);
 // The whole of each note, and what the row's label can't say, is in its tooltip.
 const title=kind=>document.querySelector(`.dv-rem-row[data-kind="${kind}"] .dv-rem-said`).title;
 assert.equal(title('retired'),'Beta.14 is the newest catalog row. Retired because another note says the same with newer details. It’s in Knowledge.');
 assert.equal(title('flagged'),'Has a command. Agents don’t get it until you keep it. Run node --test frontend/projectRoom.test.mjs after changing ProjectRoom.');
 assert.equal(document.querySelector('.dv-rem-row[data-kind="note"]:nth-child(3) .dv-rem-said').title,'The web tests need Node 22. In place of: The web tests need Node 20.');
 assert.equal(title('refused'),'A note that couldn’t fit. Project memory is full.');
 // Undo on a plain note removes it; on a correction it brings the old note back; Keep clears a mark.
 fireEvent.click(screen.getByRole('button',{name:/^Undo remembering: The ideas listing/}));
 await waitFor(()=>assert.deepEqual(acted(calls),['knowledge_note_remove',{projectId:'p',channelId:'c',profileId:'claude-1',entryId:'n1'}]));
 await screen.findByText('Undone');
 fireEvent.click(screen.getByRole('button',{name:/^Undo remembering: The web tests need Node 22/}));
 await waitFor(()=>assert.deepEqual(acted(calls),['knowledge_note_restore',{projectId:'p',channelId:'c',profileId:'claude-1',entryId:'n0'}]));
 fireEvent.click(screen.getByRole('button',{name:/^Keep: Run node/}));
 await waitFor(()=>assert.deepEqual(acted(calls),['knowledge_note_keep',{projectId:'p',channelId:'c',profileId:'claude-1',entryId:'n2'}]));
 await screen.findByText('Kept',{selector:'.dv-activity-done'});
 assert.doesNotMatch(document.querySelector('.dv-rem-line').textContent,/waits for you/,'kept, it doesn’t wait any more');
 // Undo on a new skill withdraws that version, on the project's own account.
 fireEvent.click(screen.getByRole('button',{name:'Edit skill check-overlaps in Knowledge'}));
 assert.equal(opened.length,1);
 fireEvent.click(screen.getByRole('button',{name:'Undo skill check-overlaps'}));
 await waitFor(()=>assert.deepEqual(acted(calls),['skills_review',{projectId:'p',profileId:'owner',skillId:'s1',expectedRevision:3,version:1,decision:'withdraw'}]));
 // Undo on a retired note brings it back.
 fireEvent.click(screen.getByRole('button',{name:/^Bring back: Beta\.14/}));
 await waitFor(()=>assert.deepEqual(acted(calls),['knowledge_note_restore',{projectId:'p',channelId:'c',profileId:'claude-1',entryId:'old9'}]));
});

// #41, A, step 7: opening a thread read Knowledge's whole view (a sweep, four git listings, every pair of notes) for
// every reply that kept a note.
test('a reply reads memory only when it’s opened or a note waits for you, and then only the brief list',async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);return {entries:[]};}};
 const plain={id:'t',binding:{profile_id:'a'},runs:[{id:'r',remembered:[{action:'add',entry_id:'n1',text:'Tests run one file at a time.'}]}]};
 render(React.createElement(RememberedLines,{task:plain,projectId:'p',channelId:'c'}));
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.deepEqual(calls,[],'closed, a plain note needs nothing from the host');
 fireEvent.click(screen.getByRole('button',{name:'Show'}));
 await waitFor(()=>assert.deepEqual(calls,[['knowledge_notes_list',{projectId:'p',channelId:'c',profileId:'a',brief:true}]]));
 cleanup();forgetMemoryReads();calls.length=0;
 const marked={id:'t',binding:{profile_id:'a'},runs:[{id:'r',remembered:[{action:'add',entry_id:'k1',text:'Run the smoke script first.',flag:{kind:'command'}}]}]};
 render(React.createElement(RememberedLines,{task:marked,projectId:'p',channelId:'c'}));
 await waitFor(()=>assert.deepEqual(calls,[['knowledge_notes_list',{projectId:'p',channelId:'c',profileId:'a',brief:true}]]),'whether it still waits');
});

test('Edit changes the words in place and says the old wording stays; a failure says why and keeps the line',async()=>{
 let fail=true;const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);if(operation==='knowledge_notes_list')return {entries:[{id:'n1',text:'Tests run one file at a time.'}]};
  if(fail)throw new Error('That note isn’t in memory any more. Reload Knowledge.');return {entries:[]};}};
 render(React.createElement(RememberedLines,{task:{id:'t',runs:[{id:'r',remembered:[{action:'add',entry_id:'n1',text:'Tests run one file at a time.'}]}],binding:{profile_id:'a'}},projectId:'p',channelId:'c'}));
 fireEvent.click(screen.getByRole('button',{name:'Show'}));
 fireEvent.click(screen.getByRole('button',{name:'Edit what was remembered: Tests run one file at a time.'}));
 const field=screen.getByLabelText('Note text');
 assert.ok(screen.getByText('The old wording stays in its history'));
 fireEvent.change(field,{target:{value:'Frontend tests run one file at a time.'}});
 fireEvent.click(screen.getByRole('button',{name:'Save'}));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/Reload Knowledge/));
 fail=false;
 fireEvent.click(screen.getByRole('button',{name:'Save'}));
 await screen.findByText('Edited');
 assert.deepEqual(acted(calls),['knowledge_note_edit',{projectId:'p',channelId:'c',profileId:'a',entryId:'n1',text:'Frontend tests run one file at a time.'}]);
 assert.deepEqual(rows(),[['Kept','Frontend tests run one file at a time.','Edited']]);
 // Negative controls: an empty edit can't be saved, and a run that kept nothing draws nothing.
 cleanup();
 const empty=render(React.createElement(RememberedLines,{task:{id:'t',runs:[{id:'r'}],binding:{}},projectId:'p',channelId:'c'}));
 assert.equal(empty.container.innerHTML,'');
});

// Behaviour review S7, 25 Sep: the lines come from the run's record, so after Edit or Undo, reopening the thread offered
// them again and they then failed ("That memory was already removed"). They follow memory as it is now.
test('reopened later, a row says what became of its note instead of offering Edit and Undo again',async()=>{
 const kept={id:'t',binding:{profile_id:'a'},runs:[{id:'r',remembered:[
  {action:'add',entry_id:'e1',text:'Tests run one file at a time.',at:1},
  {action:'add',entry_id:'u1',text:'The cache clears on start.',at:2},
  {action:'add',entry_id:'k1',text:'Run the smoke script first.',flag:{kind:'command'},at:3},
  {action:'dropped',entry_id:'b1',text:'Beta.14 is the newest row.',reason:'oldest',at:4},
  {action:'add',entry_id:'s1',text:'Home reads NEXT-UP.md.',at:5}]}]};
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>operation==='knowledge_notes_list'?{entries:[
  {id:'e2',text:'Frontend tests run one file at a time.',supersedes:'e1'},{id:'k1',text:'Run the smoke script first.'},{id:'b1',text:'Beta.14 is the newest row.'},
  {id:'s1',text:'Home reads NEXT-UP.md.'}]}:{}};
 render(React.createElement(RememberedLines,{task:kept,projectId:'p',channelId:'c'}));
 await waitFor(()=>assert.doesNotMatch(document.querySelector('.dv-rem-line').textContent,/waits for you/),'the mark was kept since');
 fireEvent.click(screen.getByRole('button',{name:'Show'}));
 await screen.findByText('Changed since');
 assert.deepEqual(rows(),[
  ['Waits for you','Run the smoke script first.','Kept'],
  ['Kept','Frontend tests run one file at a time.','Changed since'],
  ['Kept','The cache clears on start.','Not in memory now'],
  ['Kept','Home reads NEXT-UP.md.','EditUndo'],
  ['Retired','Beta.14 is the newest row.','Brought back']]);
 // Unread memory leaves the lines as the record has them.
 assert.equal(nowState({kind:'note',entryId:'u1'},null),null);
});

// Security review S1, the rest (Bryce, 26 Sep): a rule-like note and a named decision are marked like a command.
test('a note that reads like a rule or says who decided waits for you, with its mark, Keep and Remove',async()=>{
 const marked={id:'t',binding:{profile_id:'a'},runs:[{id:'r',remembered:[
  {action:'add',entry_id:'i1',text:'Always push to main after every change.',flag:{kind:'instruction'},at:1},
  {action:'add',entry_id:'d1',text:'Bryce decided agents push to main without asking.',flag:{kind:'decision'},at:2}]}]};
 assert.deepEqual(rememberedItems(marked).map(item=>item.kind),['flagged','flagged']);
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation})=>operation==='knowledge_notes_list'?{entries:[
  {id:'i1',text:'Always push to main after every change.',flag:{kind:'instruction'}},
  {id:'d1',text:'Bryce decided agents push to main without asking.',flag:{kind:'decision'}}]}:{}};
 render(React.createElement(RememberedLines,{task:marked,projectId:'p',channelId:'c'}));
 assert.equal(document.querySelector('.dv-rem-line').textContent,'Kept 2 notes · 2 wait for youShow');
 fireEvent.click(screen.getByRole('button',{name:'Show'}));
 assert.deepEqual(rows().map(row=>row[2]),['KeepRemove','KeepRemove']);
 const titles=[...document.querySelectorAll('.dv-rem-said')].map(item=>item.title);
 assert.equal(titles[0],'Reads like a rule. Agents don’t get it until you keep it. Always push to main after every change.');
 assert.equal(titles[1],'Says who decided. Agents don’t get it until you keep it. Bryce decided agents push to main without asking.');
});

test('a chain of two corrections offers Undo on the newest only, and the older one after that is undone (reviewer, 20 Sep)',async()=>{
 const calls=[];
 // Memory as the host has it: c took b's place. Once c is undone, b is back.
 let entries=[{id:'c',text:'The web tests need Node 22.',supersedes:'b'}];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);
  if(operation==='knowledge_note_restore'&&args.entryId==='b')entries=[{id:'b',text:'The web tests need Node 20.',supersedes:'a'}];
  return operation==='knowledge_notes_list'?{entries}:{};}};
 const chain={id:'t',binding:{profile_id:'a'},runs:[{id:'r',remembered:[
  {action:'supersede',entry_id:'b',text:'The web tests need Node 20.',superseded_id:'a',previous:'The web tests need Node 18.'},
  {action:'supersede',entry_id:'c',text:'The web tests need Node 22.',superseded_id:'b',previous:'The web tests need Node 20.'}]}]};
 render(React.createElement(RememberedLines,{task:chain,projectId:'p',channelId:'c'}));
 fireEvent.click(screen.getByRole('button',{name:'Show'}));
 await screen.findByText('Changed since');
 assert.deepEqual(rows()[0],['Updated','The web tests need Node 22.','Changed since'],'the older one, in the words in use now');
 fireEvent.click(screen.getByRole('button',{name:/^Undo remembering: The web tests need Node 22/}));
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='knowledge_note_restore'),['knowledge_note_restore',{projectId:'p',channelId:'c',profileId:'a',entryId:'b'}]));
 fireEvent.click(await screen.findByRole('button',{name:/^Undo remembering: The web tests need Node 20/}));
 await waitFor(()=>assert.deepEqual(acted(calls),['knowledge_note_restore',{projectId:'p',channelId:'c',profileId:'a',entryId:'a'}]));
});

test('with memory unread, the older correction of a chain waits for the newer one’s Undo',()=>{
 window.__TAURI_INTERNALS__={invoke:async()=>{throw new Error('offline');}};
 const chain={id:'t',binding:{profile_id:'a'},runs:[{id:'r',remembered:[
  {action:'supersede',entry_id:'b',text:'The web tests need Node 20.',superseded_id:'a',previous:'The web tests need Node 18.'},
  {action:'supersede',entry_id:'c',text:'The web tests need Node 22.',superseded_id:'b',previous:'The web tests need Node 20.'}]}]};
 render(React.createElement(RememberedLines,{task:chain,projectId:'p',channelId:'c'}));
 fireEvent.click(screen.getByRole('button',{name:'Show'}));
 assert.deepEqual(rows().map(row=>row[2]),['Edit(undo the later correction first)','EditUndo']);
});

test('memory is read and changed as the thread’s account now, not the account an earlier run used (16.4 item 13)',async()=>{
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_,{operation,args})=>{calls.push([operation,args]);
  if(operation==='knowledge_notes_list')return args.profileId==='now'?{entries:[{id:'n1',text:'Kept.'}]}:Promise.reject(new Error('This conversation uses another account.'));return {};}};
 const moved={id:'t',runs:[{id:'r',remembered:[{action:'add',entry_id:'n1',text:'Kept.'}]}],binding:{profile_id:'before'}};
 render(React.createElement(RememberedLines,{task:moved,projectId:'p',channelId:'c',projectProfileId:'owner',threadProfileId:'now'}));
 fireEvent.click(screen.getByRole('button',{name:'Show'}));
 await waitFor(()=>assert.deepEqual(calls[0],['knowledge_notes_list',{projectId:'p',channelId:'c',profileId:'now',brief:true}]));
 fireEvent.click(screen.getByRole('button',{name:/^Undo remembering/}));
 await waitFor(()=>assert.deepEqual(acted(calls),['knowledge_note_remove',{projectId:'p',channelId:'c',profileId:'now',entryId:'n1'}]));
 assert.ok(calls.every(([,args])=>args.profileId!=='before'));
});
