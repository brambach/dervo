import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','HTMLButtonElement','MutationObserver','Node','NodeFilter','CustomEvent','Event','KeyboardEvent','FocusEvent','MouseEvent','PointerEvent','DocumentFragment'])if(dom.window[key])globalThis[key]=dom.window[key];
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
// The context menu anchors to where you clicked with DOMRect.fromRect, which jsdom doesn't provide.
globalThis.DOMRect=dom.window.DOMRect??class DOMRect{constructor(x=0,y=0,width=0,height=0){Object.assign(this,{x,y,width,height,top:y,left:x,right:x+width,bottom:y+height});}static fromRect(r={}){return new DOMRect(r.x,r.y,r.width,r.height);}toJSON(){return this;}};
HTMLElement.prototype.scrollIntoView=function(){};
HTMLElement.prototype.hasPointerCapture=()=>false;HTMLElement.prototype.releasePointerCapture=()=>{};
globalThis.getComputedStyle=dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.localStorage=dom.window.localStorage;
globalThis.requestAnimationFrame=callback=>setTimeout(callback,0);
globalThis.cancelAnimationFrame=clearTimeout;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const React=await import('react');
const {render,fireEvent,waitFor,cleanup,screen,within}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {ProjectRoom,board,cardOf,whereLine,roomState,agentOf}=await import('./ProjectRoom.tsx');
after(()=>dom.window.close());
afterEach(()=>{cleanup();delete window.__TAURI_INTERNALS__;});

const profiles=[{id:'p1',provider:'claude',identity:'bryce@example.test',category:'personal'},
                {id:'p2',provider:'codex',identity:'work@example.test',category:'work'}];
const conversation=(id,name,activity,extra={})=>({id,name,visibility:'shared',project_id:'proj',profile_id:'p1',
 model:'claude-opus-5',activity,...extra});
const running=conversation('c1','Bridge and dispatch',{status:'running',running:true,step:'Editing bridge.rs',unread:0,summary:'Editing bridge.rs'});
const needs=conversation('c2','The room and the board',{status:'needs',running:false,step:null,unread:2,summary:'Changed 6 files. Checks passed.',attention:true});
const idle=conversation('c3','Reading type',{status:null,running:false,step:null,unread:0,summary:'Approved on 17 Sep.'});
const fresh=conversation('c4','Brand new',null);
const idea=(id,name,ids,extra={})=>({id,name,summary:'',state:'building',target:'0.1.0-beta.4',archived_at:null,revision:1,
 created_at:'',updated_at:'',conversations:ids.map(entry=>({id:entry,name:entry})),...extra});
const listing=(ideas)=>({ideas,archived:[],unassigned:[],states:['planned','building','done'],
 limits:{name:60,summary:2000},detail:''});

test('a card says what the conversation is doing, from the activity index and never from its text',()=>{
 assert.deepEqual(cardOf(running,profiles),{id:'c1',name:'Bridge and dispatch',state:'running',says:'Editing bridge.rs',
  agent:'Claude Code · Opus 5 · personal',unread:0,helpers:''});
 // Settled conversations show the recorded sentence, not the step of a run that has ended.
 assert.equal(cardOf(needs,profiles).says,'Changed 6 files. Checks passed.');
 assert.equal(cardOf(needs,profiles).state,'needs');
 assert.equal(cardOf(idle,profiles).state,'idle');
 // Nothing has ever run here, so the card says nothing rather than inventing a state.
 assert.equal(cardOf(fresh,profiles).says,'');
 assert.equal(cardOf(fresh,profiles).state,'idle');
 // A running task that hasn't reported a step yet still reads as working rather than as blank.
 assert.equal(cardOf(conversation('c9','x',{running:true,status:'running',step:null,unread:0}),profiles).says,'Working');
 assert.equal(agentOf({...running,profile_id:'p2'},profiles),'Codex · Opus 5 · work');
 assert.equal(agentOf({...running,profile_id:'gone'},profiles),'Account not found · Opus 5','a missing account is named, not borrowed');
});

test('the board files conversations under their idea and leaves the rest in their own section',()=>{
 const sections=board(listing([idea('i1','Orchestrator',['c1','c2']),idea('i2','Releases',[])]),[running,needs,idle],profiles);
 assert.deepEqual(sections.map(section=>[section.name,section.cards.length]),
  [['Orchestrator',2],['Releases',0],['Not in an idea',1]]);
 assert.equal(sections[0].where,'1 running · 1 waiting for you');
 assert.equal(sections[1].where,'No conversations yet');
 assert.equal(sections[2].where,'1 conversation, waiting to be filed');
 assert.deepEqual(roomState(sections),{running:1,needs:1,idle:1});
 // An idea naming a conversation the project no longer lists draws nothing, rather than an empty card.
 const stale=board(listing([idea('i1','Orchestrator',['c1','deleted'])]),[running],profiles);
 assert.deepEqual(stale.map(section=>section.cards.map(card=>card.id)),[['c1']]);
 // No ideas at all: every shared conversation is still visible, under one heading.
 assert.deepEqual(board(undefined,[running,needs],profiles).map(section=>[section.name,section.cards.length]),[['Not in an idea',2]]);
 // Private conversations never appear in a shared room.
 assert.deepEqual(board(undefined,[{...idle,visibility:'private'}],profiles),[]);
 assert.equal(whereLine([]),'No conversations yet');
 assert.equal(whereLine([cardOf(idle,profiles),cardOf(fresh,profiles)]),'2 conversations, nothing running');
});

function host({ideas=listing([idea('i1','Orchestrator',['c1','c2'])]),fail=false,route=null}={}){
 const calls=[];
 let current=ideas;
 window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{
  const op=args.operation??command,a=args.args??{};
  calls.push([op,a]);
  if(op==='ideas_list'){if(fail)throw new Error('ideas unavailable');return current;}
  if(op==='ideas_route')return route?{conversation:route,matched:['board']}:{conversation:null,matched:[]};
  if(op==='ideas_save'){
   if(a.ideaId===null)current={...current,ideas:[...current.ideas,idea('new','X',[],{name:a.name,target:a.target,state:a.state,revision:1})]};
   else current={...current,ideas:current.ideas.map(entry=>entry.id===a.ideaId?{...entry,name:a.name,summary:a.summary,state:a.state,target:a.target,revision:entry.revision+1}:entry)};
   return current;
  }
  if(op==='ideas_archive'){const gone=current.ideas.find(entry=>entry.id===a.ideaId);current={...current,ideas:current.ideas.filter(entry=>entry.id!==a.ideaId),archived:[{...gone,archived_at:'now'}]};return current;}
  if(op==='ideas_restore'){const back=current.archived.find(entry=>entry.id===a.ideaId);current={...current,archived:[],ideas:[...current.ideas,{...back,archived_at:null}]};return current;}
  if(op==='ideas_assign'){current={...current,ideas:current.ideas.map(entry=>({...entry,conversations:[...entry.conversations.filter(c=>c.id!==a.channelId),...(entry.id===a.ideaId?[{id:a.channelId,name:a.channelId}]:[])]}))};return current;}
  throw new Error('no fixture '+op);
 }};
 return calls;
}
const mount=props=>render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})},
 React.createElement(ProjectRoom,{projectId:'proj',profileId:'p1',projectName:'dervo',conversations:[running,needs,idle],
  profiles,onOpen(){},...props})));
const openMenu=trigger=>fireEvent.keyDown(trigger,{key:'Enter'});

test('the room shows each idea with its conversations, the counts and the one accent on what waits for you',async()=>{
 host();
 mount();
 const section=await screen.findByRole('region',{name:'Orchestrator'});
 assert.deepEqual([...section.querySelectorAll('.dervo-room-name')].map(node=>node.textContent),
  ['Bridge and dispatch','The room and the board']);
 assert.ok(within(section).getByText('1 running · 1 waiting for you'));
 assert.ok(within(section).getByText('Building'));
 assert.ok(within(section).getByText('for 0.1.0-beta.4'));
 const cards=[...document.querySelectorAll('.dervo-room-card')];
 assert.deepEqual(cards.map(card=>card.dataset.state),['running','needs','idle']);
 assert.equal(document.querySelectorAll('.dervo-room-ask').length,1,'exactly one card offers an action');
 assert.equal(document.querySelectorAll('.dervo-room-dot[data-live]').length,1,'only a running conversation gets a live dot');
 assert.doesNotMatch(document.body.textContent,/space bar/,'keyboard dragging is off, so nothing tells you to press space');
 assert.match(document.body.textContent,/context menu and choose Move to/);
 assert.match(document.querySelector('.dervo-room-state').textContent,/1 running1 waiting for you1 idle/);
 assert.ok(within(cards[1]).getByText('2'),'unread results stay on the card');
});

test('ideas are made, renamed, set and archived from the room, each with the revision it last saw',async()=>{
 const calls=host();
 mount();
 await screen.findByRole('region',{name:'Orchestrator'});
 fireEvent.click(screen.getByRole('button',{name:'New idea'}));
 fireEvent.change(screen.getByRole('textbox',{name:'Idea name'}),{target:{value:'Releases'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Release this idea is aimed at'}),{target:{value:'0.1.0-beta.5'}});
 fireEvent.click(screen.getByRole('button',{name:'Add idea'}));
 await screen.findByRole('region',{name:'Releases'});
 assert.deepEqual(calls.find(([op])=>op==='ideas_save')[1],{projectId:'proj',profileId:'p1',ideaId:null,expectedRevision:0,name:'Releases',summary:'',state:'planned',target:'0.1.0-beta.5'});
 assert.match(screen.getByRole('region',{name:'Releases'}).textContent,/No conversations yet/);
 openMenu(screen.getByRole('button',{name:'Options for Orchestrator'}));
 fireEvent.click(await screen.findByRole('menuitem',{name:'Rename'}));
 const name=await screen.findByRole('textbox',{name:'Idea name'});
 fireEvent.change(name,{target:{value:'Room'}});
 fireEvent.keyDown(name,{key:'Enter'});
 await screen.findByRole('region',{name:'Room'});
 const rename=calls.filter(([op])=>op==='ideas_save').at(-1)[1];
 assert.equal(rename.name,'Room');assert.equal(rename.expectedRevision,1);assert.equal(rename.state,'building');
 openMenu(screen.getByRole('button',{name:'Options for Room'}));
 fireEvent.click(await screen.findByRole('menuitemradio',{name:'Done'}));
 await waitFor(()=>assert.ok(within(screen.getByRole('region',{name:'Room'})).getByText('Done')));
 openMenu(screen.getByRole('button',{name:'Options for Room'}));
 fireEvent.click(await screen.findByRole('menuitem',{name:'Archive idea'}));
 await waitFor(()=>assert.equal(screen.queryByRole('region',{name:'Room'})===null,true));
 assert.ok(screen.getByRole('region',{name:'Not in an idea'}),'its conversations are not lost, they come back unfiled');
 fireEvent.click(screen.getByText(/Archived ideas/));
 fireEvent.click(screen.getByRole('button',{name:'Restore'}));
 await screen.findByRole('region',{name:'Room'});
});

test('a conversation is filed from the suggestion or the context menu, and a refusal puts it back',async()=>{
 const base=listing([idea('i1','Orchestrator',['c1']),idea('i2','Releases',[])]);
 const calls=host({ideas:{...base,suggestions:{c3:'i2'}}});
 mount();
 await screen.findByRole('region',{name:'Releases'});
 const loose=screen.getByRole('region',{name:'Not in an idea'});
 const chip=within(loose).getByRole('button',{name:/File under Releases/});
 fireEvent.click(chip);
 await waitFor(()=>assert.ok(within(screen.getByRole('region',{name:'Releases'})).getByText('Reading type')));
 assert.deepEqual(calls.find(([op])=>op==='ideas_assign')[1],{projectId:'proj',profileId:'p1',channelId:'c3',ideaId:'i2'});
 assert.ok(!calls.some(([op])=>op==='ideas_route'),'filing never routes a message anywhere');
 // A suggestion is never shown on a card that's already filed.
 assert.equal(document.querySelectorAll('.dervo-room-suggest').length,0);
 fireEvent.contextMenu(within(screen.getByRole('region',{name:'Orchestrator'})).getByText('Bridge and dispatch'));
 fireEvent.click(await screen.findByRole('menuitem',{name:'Releases'}));
 await waitFor(()=>assert.ok(within(screen.getByRole('region',{name:'Releases'})).getByText('Bridge and dispatch')));
});

test('ideas that cannot load still leave every conversation visible, and the orchestrator sits beside the board',async()=>{
 host({fail:true});
 mount({chat:React.createElement('section',{'aria-label':'Orchestrator chat stand-in'},'chat')});
 const alert=await screen.findByRole('alert');
 assert.match(alert.textContent,/ideas couldn’t load/);
 assert.equal(document.querySelectorAll('.dervo-room-card').length,3,'every conversation is still reachable');
 assert.ok(screen.getByRole('region',{name:'Not in an idea'}));
 const split=document.querySelector('.dervo-room-split');
 assert.ok(split,'the chat and the board share the screen');
 assert.equal(split.firstElementChild.getAttribute('aria-label'),'Orchestrator chat stand-in','the chat comes first');
 assert.equal(document.querySelector('.dervo-room-composer'),null,'the room has no composer of its own');
 // Negative: without a chat the board has the screen to itself.
 cleanup();host();mount();
 await screen.findByRole('region',{name:'Orchestrator'});
 assert.equal(document.querySelector('.dervo-room-split'),null);
});

test('two running conversations editing one file are named under their idea, in plain words',async()=>{
 const {overlapLines}=await import('./ProjectRoom.tsx');
 assert.deepEqual(overlapLines([{idea_id:'i1',file:'backend/host.py',conversations:['Bridge','Room']},{idea_id:'i2',file:'x',conversations:['a','b']}],'i1'),
  ['Bridge and Room are both editing backend/host.py.']);
 assert.deepEqual(overlapLines([{idea_id:'i1',file:'a.ts',conversations:['A','B','C']}],'i1'),['A, B and C are all editing a.ts.']);
 assert.deepEqual(overlapLines(undefined,'i1'),[]);
 host({ideas:{...listing([idea('i1','Orchestrator',['c1','c2'])]),overlaps:[{idea_id:'i1',file:'backend/host.py',conversations:['Bridge and dispatch','The room and the board']}]}});
 mount();
 const note=await screen.findByRole('note');
 assert.match(note.textContent,/Bridge and dispatch and The room and the board are both editing backend\/host\.py\./);
 assert.match(note.textContent,/approving one after the other can overwrite the first/);
});

test('a card says which helpers its newest task asked and where each stands',async()=>{
 const {helperLine}=await import('./ProjectRoom.tsx');
 assert.equal(helperLine([]),'');
 assert.equal(helperLine([{who:'fixer',state:'working'}]),'Asked fixer (working)');
 assert.equal(helperLine([{who:'fixer',state:'working'},{who:'Independent review',state:'answered'}]),'Asked fixer (working) and Independent review (answered)');
 const card=cardOf({...running,activity:{...running.activity,helpers:[{who:'fixer',state:'working'}]}},profiles);
 assert.equal(card.helpers,'Asked fixer (working)');
});
