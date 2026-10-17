import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
globalThis.window=dom.window;globalThis.document=dom.window.document;
for(const key of Object.getOwnPropertyNames(dom.window))if(/^[A-Z]/.test(key)&&(!(key in globalThis)||/Event$|^(Event|EventTarget|Node|Element)$/.test(key)))globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=window.requestAnimationFrame=cb=>setTimeout(cb,0);
const React=await import('react');
const {render,fireEvent,cleanup,act}=await import('@testing-library/react');
const {ContinuityTool,ContinuityMenuItems}=await import('./SessionContinuity.tsx');
const {DropdownMenu,DropdownMenuTrigger,DropdownMenuContent}=await import('../shared/ui/dropdown-menu.tsx');
const {FactsSection}=await import('./ProjectMemory.tsx');
after(()=>{cleanup();dom.window.close();});

function host(){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);return {};}};return calls;}

test('a chat that keeps its context says nothing in the composer and offers Start fresh in the thread menu; a break shows its reason before sending',async()=>{
 const calls=host();let changed=0;const errors=[];
 const props={projectId:'p',channelId:'c',onChanged:async()=>{changed++;}};
 assert.equal(render(React.createElement(ContinuityTool,{...props,view:{state:'first',reason:null}})).container.textContent,'','the first message has nothing to continue');
 cleanup();
 const keeps=render(React.createElement(ContinuityTool,{...props,view:{state:'continues',reason:null}}));
 try{assert.equal(keeps.container.textContent,'','while the chat continues, the composer foot holds only the model chip (Bryce, 18 Sep)');}finally{cleanup();}
 const menu=(view)=>render(React.createElement(DropdownMenu,{open:true},React.createElement(DropdownMenuTrigger,null,React.createElement('button',null,'⋯')),React.createElement(DropdownMenuContent,null,React.createElement(ContinuityMenuItems,{...props,view,onError:m=>errors.push(m)}))));
 const first=menu({state:'first',reason:null});
 try{assert.equal(first.queryByRole('menuitem'),null,'nothing to start fresh from');}finally{cleanup();}
 const item=menu({state:'continues',reason:null});
 try{
  const fresh=await item.findByRole('menuitem',{name:/^Start fresh/});
  assert.match(fresh.title,/sends a short summary instead/);
  await act(async()=>{fireEvent.click(fresh);});
  assert.deepEqual(calls,[['coding_fresh_start_set',{projectId:'p',channelId:'c',enabled:true}]]);assert.equal(changed,1);
 }finally{cleanup();}
 calls.length=0;
 const undo=menu({state:'fresh',reason:'You chose to start fresh.',fresh_chosen:true});
 try{
  await act(async()=>{fireEvent.click(await undo.findByRole('menuitem',{name:/^Keep this chat’s context/}));});
  assert.deepEqual(calls,[['coding_fresh_start_set',{projectId:'p',channelId:'c',enabled:false}]]);
 }finally{cleanup();}
 const movedMenu=menu({state:'fresh',reason:'Codex gets a summary of this chat, not the full session.'});
 try{assert.equal(movedMenu.queryByRole('menuitem'),null,'a break Dervo chose for a reason has nothing to undo');}finally{cleanup();}
 calls.length=0;
 const chosen=render(React.createElement(ContinuityTool,{...props,view:{state:'fresh',reason:'You chose to start fresh.',fresh_chosen:true}}));
 try{
  assert.equal(chosen.getByTestId('continuity').textContent,'New session');
  await act(async()=>{fireEvent.click(chosen.getByRole('button',{name:'Undo start fresh'}));});
  assert.deepEqual(calls,[['coding_fresh_start_set',{projectId:'p',channelId:'c',enabled:false}]]);
 }finally{cleanup();}
 const moved=render(React.createElement(ContinuityTool,{...props,view:{state:'fresh',reason:'Codex gets a summary of this chat, not the full session.'}}));
 try{
  assert.equal(moved.getByTestId('continuity').textContent,'New session · Codex gets a summary of this chat, not the full session');
  assert.equal(moved.queryByRole('button'),null,'a break Dervo chose for a reason has nothing to undo here');
 }finally{cleanup();}
});

const noop=async()=>{};
const actions=(over={})=>({edit:noop,remove:noop,restore:noop,keep:noop,pin:noop,promote:noop,keepNewer:noop,keepBoth:noop,...over});

test('facts and decisions list every note newest first with who kept it, in which chat and when, and say when memory is off',async()=>{
 const now=Date.now()/1000;const removed=[],promoted=[];
 const view={enabled:true,used_chars:1500,max_chars:6000,max_entries:30,seen_at:now-3600,entries:[
  {id:'b2',text:'Tests run from backend with python3.13.',created_at:now-90000,updated_at:now-90000,provider:'codex'},
  {id:'a1',text:'The user wants plain words in the UI.',created_at:now-1800,updated_at:now-1800,provider:'claude',source:{thread_id:'t',task_id:'k'}}]};
 const opened=[];
 const screen=render(React.createElement(FactsSection,{view,seenAt:now-3600,threadName:id=>id==='t'?'Beta.16 plan':undefined,onOpenSource:(thread,task)=>opened.push([thread,task]),
  actions:actions({remove:async entry=>{removed.push(entry.id);},promote:async entry=>{promoted.push(entry.id);}})}));
 try{
  const items=[...screen.getByTestId('project-memory').querySelectorAll('.dv-row')].map(row=>row.querySelector('.dv-row-name').textContent+' | '+row.querySelector('.dv-row-line').textContent);
  assert.deepEqual(items,['The user wants plain words in the UI. | Claude Code in Beta.16 plan · 30 min ago','Tests run from backend with python3.13. | Codex · Yesterday']);
  assert.equal(screen.getByTestId('project-memory-usage').textContent,'2','the head counts the notes');
  assert.deepEqual([...screen.getByTestId('project-memory').querySelectorAll('.dv-kn-new')].map(pill=>pill.closest('.dv-row').querySelector('.dv-row-name').textContent),['The user wants plain words in the UI.'],'New only on what came after you last looked');
  fireEvent.click(screen.getByRole('button',{name:'Beta.16 plan'}));
  assert.deepEqual(opened,[['t','k']],'the chat link opens the chat it came from');
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Delete note: The user wants plain words in the UI.'}));});
  assert.equal(screen.queryByRole('button',{name:/^Make a rule from note/}),null,'hover shows Edit and Delete, as drawn');
  fireEvent.click(screen.getByRole('button',{name:'Edit note: Tests run from backend with python3.13.'}));
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Make a rule from note: Tests run from backend with python3.13.'}));});
  assert.deepEqual([removed,promoted],[['a1'],['b2']]);
 }finally{cleanup();}
 const off=render(React.createElement(FactsSection,{view:{...view,enabled:false,entries:[],used_chars:0},actions:actions()}));
 try{
  assert.match(off.getByRole('status').textContent,/^Off\./);
  assert.equal((off.container.textContent.match(/Off/g)??[]).length,1,'off is said once, by the status line');
 }finally{cleanup();}
 const empty=render(React.createElement(FactsSection,{view:{...view,entries:[]},actions:actions()}));
 try{assert.match(empty.container.textContent,/Nothing kept yet\./);}finally{cleanup();}
});

test('Dervo’s own notes say so, and a note that may be out of date says why without removing anything',async()=>{
 const {noteAuthor,noteDoubt,flagLabel,tokensLine,keptWhen,retiredWhy,isNew}=await import('./ProjectMemory.tsx');
 assert.equal(noteAuthor({id:'a',text:'x',created_at:1,updated_at:1,provider:'dervo',source:{kind:'dervo_check',profile:'node-test'}}),'Noted by Dervo from a checked result');
 assert.equal(noteAuthor({id:'b',text:'x',created_at:1,updated_at:1,provider:'claude'}),'Claude Code');
 assert.equal(noteAuthor({id:'b',text:'x',created_at:1,updated_at:1,provider:'claude',edited_by:'local-user'}),'Edited by you');
 assert.doesNotMatch(noteAuthor({id:'c',text:'x',created_at:1,updated_at:1,provider:'dervo'}),/Provider \(/,'never the raw provider id');
 assert.equal(noteDoubt({id:'d',text:'x',created_at:1,updated_at:1,missing:['src/old.ts']}),'Mentions src/old.ts, which isn’t in the project now.');
 assert.equal(noteDoubt({id:'e',text:'x',created_at:1,updated_at:1,missing:['a/b.ts','c/d.py']}),'Mentions a/b.ts and c/d.py, which aren’t in the project now.');
 assert.equal(noteDoubt({id:'f',text:'x',created_at:1,updated_at:1,aging:true}),'Nobody has updated this in over six weeks.');
 assert.equal(noteDoubt({id:'g',text:'x',created_at:1,updated_at:1}),null);
 assert.deepEqual([flagLabel({kind:'command'}),flagLabel({kind:'agents'}),flagLabel({kind:'file',path:'docs/README.md'}),flagLabel(null)],['Has a command','Written to agents','Copied from README.md','']);
 assert.equal(tokensLine({enabled:true,entries:[],used_chars:8400,max_chars:12000,max_entries:30}),'About 2,100 of 3,000 tokens of notes go with each request. A continuing chat gets only the notes that changed.');
 const now=new Date(2026,8,25,15).getTime()/1000;
 assert.deepEqual([keptWhen(now-720,now),keptWhen(now-7200,now),keptWhen(now-86400*2,now)],['12 min ago','2 h ago','23 Sep']);
 assert.equal(retiredWhy({id:'r',text:'x',created_at:1,updated_at:1,retired_reason:'merged'}),'Another note says the same with newer details');
 assert.equal(retiredWhy({id:'r',text:'x',created_at:1,updated_at:1,retired_reason:'out_of_date',missing:['a.py']}),'It names a.py, which the project no longer has');
 assert.equal(isNew({id:'n',text:'x',created_at:10,updated_at:10},5),true);
 assert.equal(isNew({id:'n',text:'x',created_at:10,updated_at:10,edited_by:'local-user'},5),false,'your own edit is never New');
 assert.equal(isNew({id:'n',text:'x',created_at:10,updated_at:10},undefined),false,'nothing is New before the page knows when you last looked');
});

test('a note that couldn’t be deleted says why under it, with Try again',async()=>{
 const {render,fireEvent,waitFor,cleanup}=await import('@testing-library/react');
 let fail=true;const tries=[];
 const view={enabled:true,entries:[{id:'n',text:'A note.',created_at:1,updated_at:1,provider:'codex'}],used_chars:7,max_chars:6000,max_entries:30};
 const screen=render(React.createElement(FactsSection,{view,actions:actions({remove:async()=>{tries.push(1);if(fail)throw new Error('The note changed while you were reading.');}})}));
 fireEvent.click(screen.getByRole('button',{name:'Delete note: A note.'}));
 await waitFor(()=>assert.equal(screen.getByRole('alert').textContent,'Couldn’t delete it. The note changed while you were reading. Try again'));
 fail=false;fireEvent.click(screen.getByRole('button',{name:'Try again'}));
 await waitFor(()=>assert.equal(screen.queryByRole('alert'),null));
 assert.equal(tries.length,2);
 cleanup();
});

// dd #22 (Bryce, 2 Oct): Pin, expiring snapshots, notes agents keep saving, and more kept than one request gets.
test('a pinned note comes first and says so, and Pin works on any plain note but not a marked one or Dervo’s own',async()=>{
 const now=Date.now()/1000;const pins=[];
 const expires=new Date(2026,9,16,12).getTime()/1000;
 const view={enabled:true,used_chars:400,max_chars:32000,max_entries:80,counted:4,marked:1,sent_entries:30,sent_chars:12000,entries:[
  {id:'old',text:'Bryce reviews UI at 1052 by 854.',created_at:now-86400*20,updated_at:now-86400*20,provider:'claude',pinned_at:now-60},
  {id:'new',text:'The dev server listens on port 5173.',created_at:now-600,updated_at:now-600,provider:'codex',confirmed_count:3},
  {id:'snap',text:'The beta branch is at 8b2d79a.',created_at:now-1200,updated_at:now-1200,provider:'claude',expires_at:expires},
  {id:'mk',text:'Run ./deploy.sh after every change.',created_at:now-300,updated_at:now-300,provider:'claude',flag:{kind:'command'}},
  {id:'dv',text:'Dervo checks changes here with its Node test check.',created_at:now-900,updated_at:now-900,provider:'dervo',source:{kind:'dervo_check',profile:'node-test'}}]};
 const screen=render(React.createElement(FactsSection,{view,actions:actions({pin:async(entry,pinned)=>{pins.push([entry.id,pinned]);}})}));
 try{
  const rows=[...screen.getByTestId('project-memory').querySelectorAll('[data-testid="note-row"]')];
  assert.equal(rows[0].querySelector('.dv-row-name').textContent,'Bryce reviews UI at 1052 by 854.','pinned first, though it’s the oldest');
  assert.match(rows[0].querySelector('.dv-row-line').textContent,/^Pinned · /);
  assert.equal(screen.getByRole('button',{name:'Unpin note: Bryce reviews UI at 1052 by 854.'}).getAttribute('aria-pressed'),'true');
  const line=id=>rows.find(row=>row.querySelector('.dv-row-name').textContent===view.entries.find(e=>e.id===id).text).querySelector('.dv-row-line').textContent;
  assert.match(line('new'),/ · Saved again in 3 other chats$/);
  assert.match(line('snap'),/ · Retires 16 Oct unless pinned$/);
  assert.equal(screen.queryByRole('button',{name:/^Pin note: Run \.\/deploy/}),null,'a marked note is kept first');
  assert.equal(screen.queryByRole('button',{name:/^Pin note: Dervo checks/}),null,'Dervo’s own note is always kept and sent');
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Pin note: The dev server listens on port 5173.'}));});
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Unpin note: Bryce reviews UI at 1052 by 854.'}));});
  assert.deepEqual(pins,[['new',true],['old',false]]);
 }finally{cleanup();}
});

test('the page says what one request gets once memory keeps more, and why a note retired',async()=>{
 const {tokensLine,notesCount,retiredWhy,expiryLine,confirmedLine,snapshot}=await import('./ProjectMemory.tsx');
 const base={enabled:true,entries:[],max_chars:32000,max_entries:80,sent_entries:30,sent_chars:12000,max_tokens:3000};
 assert.equal(tokensLine({...base,used_chars:8400,counted:20}),'About 2,100 of 3,000 tokens of notes go with each request. A continuing chat gets only the notes that changed.');
 assert.equal(tokensLine({...base,used_chars:14000,counted:35}),'Up to 3,000 tokens of notes go with each request: pinned notes and the ones that best match it. The rest are listed by their first words, for the agent to look up when its task touches one.');
 assert.equal(notesCount({...base,used_chars:400,counted:2,entries:[{},{},{}]}),'2 of 80','a marked note waits apart and isn’t counted');
 assert.equal(retiredWhy({id:'r',text:'x',created_at:1,updated_at:1,retired_reason:'expired'}),'It said where something was, over two weeks ago');
 const at=new Date(2026,9,16,12).getTime()/1000;
 assert.deepEqual([expiryLine({id:'s',text:'x',created_at:1,updated_at:1,expires_at:at},at-86400),expiryLine({id:'s',text:'x',created_at:1,updated_at:1,expires_at:at},at+1),
  expiryLine({id:'s',text:'x',created_at:1,updated_at:1,expires_at:at,pinned_at:1}),expiryLine({id:'s',text:'x',created_at:1,updated_at:1})],
  ['Retires 16 Oct unless pinned','Retires now unless pinned',null,null]);
 assert.deepEqual([confirmedLine({confirmed_count:1}),confirmedLine({confirmed_count:2}),confirmedLine({})],['Saved again in 1 other chat','Saved again in 2 other chats',null]);
 for(const text of ['As of 23 Sep the newest catalog row is 0.1.0-beta.14.','On 23 Sep 0.1.0-beta.14 became the newest catalog row.','beta16 is at `8b2d79a`.'])assert.equal(snapshot(text),true,text);
 for(const text of ['Since beta.15 (8521e7c), Home needs a Claude account.','On 2 Oct Bryce picked all of it.','The header is defaced at 1052 px.'])assert.equal(snapshot(text),false,text);
});
