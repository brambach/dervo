/* Threads that work together (beta.17, #27): Catch up groups a parent's subthreads, the note and review cards above a
   thread's composer, and the quoted opening of a run a note or review started. */
import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','CustomEvent','Event','KeyboardEvent','MouseEvent'])if(dom.window[key])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
const React=await import('react');
const {render,fireEvent,cleanup,waitFor,screen}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider}=await import('@tanstack/react-query');
const {gather}=await import('./subthreadState.ts');
const {ProjectCatchup}=await import('./ProjectCatchup.tsx');
const {ThreadNotes,NoteQuote,waitingNotes,reviewTitle}=await import('./ThreadNotes.tsx');
const {taskRows}=await import('./timelineRows.ts');
const {NOTE_TAG}=await import('./threadNoteState.ts');
const {paneSheet}=await import('./homeFamilies.tsx');
after(()=>dom.window.close());afterEach(cleanup);

const convo=(id,name,extra={})=>({id,name,visibility:'shared',project_id:'p',profile_id:'a',model:'m',activity:null,...extra});
const running=step=>({status:'running',running:true,step,unread:0,since:Date.now()/1000-30});
const family=[convo('plan','Beta 16.4 plan'),convo('runs','16.4 runs',{parentId:'plan',activity:running('Running the backend tests')}),
 convo('thread','16.4 thread',{parentId:'plan',activity:running('Taking screenshots')}),convo('ideas','Ideas storage',{activity:{status:'needs',ended:'asking',running:false,step:null,unread:1}})];

test('gather puts subthreads under their parent’s row, or a row for the parent, and leaves the lifted one alone',()=>{
 const rows=[{channelId:'runs'},{channelId:'ideas'},{channelId:'plan'},{channelId:'thread'}];
 const out=gather(rows,family);
 assert.deepEqual(out.map(e=>e.kind==='item'?[e.item.channelId,e.kids.map(k=>k.channelId)]:['parent:'+e.parentId,e.kids.map(k=>k.channelId)]),
  [['ideas',[]],['plan',['runs','thread']]],'a subthread listed before its parent joins the parent’s own row');
 const absent=gather([{channelId:'runs'},{channelId:'thread'}],family);
 assert.deepEqual(absent.map(e=>[e.kind,e.kind==='parent'?e.parentId:e.item.channelId,e.kids.length]),[['parent','plan',2]]);
 const lifted=gather([{channelId:'runs'},{channelId:'thread'}],family,item=>item.channelId==='runs');
 assert.deepEqual(lifted.map(e=>[e.kind,e.kind==='parent'?e.parentId:e.item.channelId,e.kids.length]),[['item','runs',0],['parent','plan',1]]);
 assert.deepEqual(gather([{channelId:'x'}],[]).map(e=>e.kind),['item'],'a thread with no parent is unchanged');
});

test('Catch up shows a parent’s running subthreads as one group under Running',async()=>{
 window.__TAURI_INTERNALS__={invoke:async()=>({results:[],cursor:null,incomplete:false,since:null,lessonsPending:0})};
 const opened=[];
 const view=render(React.createElement(ProjectCatchup,{projectId:'p',profileId:'a',projectName:'Dervo',conversations:family,lastConversation:null,open:true,onClose(){},
  onOpenConversation:id=>opened.push(id),onOpenTask(){},onOpenKnowledge(){},onAllActivity(){},onSearch(){},onNewConversation(){}}));
 await waitFor(()=>assert.ok(view.container.querySelector('.dervo-catchup-family')));
 const group=view.container.querySelector('.dervo-catchup-family[data-kind="running"]');
 assert.match(group.querySelector('.dv-row-name').textContent,/^Beta 16\.4 plan2 of 2 running$/);
 assert.deepEqual([...group.querySelectorAll('.dervo-catchup-kid-name')].map(n=>n.firstChild.textContent),['16.4 runs','16.4 thread']);
 assert.match(group.textContent,/Beta 16\.4 plan gets a review of them once they’ve all finished\./);
 fireEvent.click(group.querySelectorAll('.dervo-catchup-kid')[1]);
 assert.deepEqual(opened,['thread']);
 assert.equal(view.container.querySelectorAll('[data-kind="running"] .dv-row-name').length,1,'no separate rows for the subthreads');
});

const note=(extra={})=>({id:'n1',kind:'note',fromName:'16.4 runs',fromChannelId:'runs',text:'Home’s panel still reads the project level directly.',createdAt:1790000000,
 state:'waiting',passedAt:null,trigger:null,delivery:null,taskId:null,detail:null,canPass:true,canDismiss:true,...extra});
const review=(extra={})=>({...note({id:'r1',kind:'review',fromName:'Dervo',fromChannelId:null,text:null}),late:false,uncertain:false,
 children:[{name:'16.4 runs',channelId:'runs',glyph:'done',line:'Finished. 9 files changed. The check passed.',state:'ready_for_review'},
  {name:'16.4 thread',channelId:'thread',glyph:'done',line:'Finished. 4 files changed.',state:'ready_for_review'},
  {name:'16.4 Home',channelId:'home',glyph:'failed',line:'Stopped with an error.',state:'blocked'}],...extra});
function mountNotes(view){
 const calls=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);if(operation==='thread_notes')return view;return {...view.notes[0],state:'passed',delivery:'started',taskId:'t9'};}};
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});
 const opened=[];
 const rendered=render(React.createElement(QueryClientProvider,{client},React.createElement(ThreadNotes,{projectId:'p',channelId:'home',agentName:'Claude Code',onOpenThread:id=>opened.push(id),onChanged:()=>{}})));
 return {calls,opened,rendered};
}

test('a note waits above the composer, quoted as its sender’s, and Pass it on sends only its id',async()=>{
 const {calls,opened}=mountNotes({notes:[note(),note({id:'gone',state:'passed',delivery:'started',taskId:'t1'})],auto:true});
 const card=await screen.findByRole('region',{name:'Note from 16.4 runs'});
 assert.equal(card.querySelector('blockquote').textContent,'Home’s panel still reads the project level directly.');
 assert.match(card.textContent,/Claude Code hasn’t seen this\. Passing it on starts a run here with the note quoted, as from 16\.4 runs\./);
 assert.equal(screen.getAllByTestId('thread-note').length,1,'one passed on and started is in the timeline, not here');
 fireEvent.click(screen.getByRole('button',{name:'Open 16.4 runs'}));
 assert.deepEqual(opened,['runs']);
 fireEvent.click(screen.getByRole('button',{name:'Pass it on'}));
 await waitFor(()=>assert.ok(calls.some(([op])=>op==='thread_note_pass')));
 assert.deepEqual(calls.find(([op])=>op==='thread_note_pass')[1],{projectId:'p',channelId:'home',noteId:'n1'},'no words of yours, no access: the host sends what it saved');
});

test('a queued note says it waits for the run, with nothing to press',async()=>{
 mountNotes({notes:[note({state:'passed',delivery:'queued',canPass:false,canDismiss:false,detail:'Passed on. It starts when this thread’s current run ends.'})],auto:true});
 const card=await screen.findByTestId('thread-note');
 assert.match(card.querySelector('[role="status"]').textContent,/starts when this thread’s current run ends/);
 assert.equal(card.querySelector('button'),null);
});

test('17.1: a note that waits for you anyway says why, keeps Pass it on, and holds its own switch',async()=>{
 const {calls}=mountNotes({notes:[note({held:'asked'}),note({id:'n2',held:'chain'})],auto:true,autoPass:true});
 const cards=await screen.findAllByTestId('thread-note');
 assert.match(cards[0].textContent,/16\.4 runs asked for your decision, so this didn’t pass on by itself\. Claude Code hasn’t seen this\./);
 assert.match(cards[1].textContent,/A run that started without you left this, so it waits for you instead of starting another\./);
 assert.equal(screen.getAllByRole('button',{name:'Pass it on'}).length,2);
 const boxes=screen.getAllByRole('checkbox',{name:'Pass notes on automatically'});
 assert.equal(boxes.length,1,'one switch, on the last note card');
 assert.ok(cards[1].contains(boxes[0]));
 assert.equal(boxes[0].checked,true);
 assert.equal(screen.queryByRole('checkbox',{name:'Review finished subthreads automatically'}),null,'the review switch stays on the review card');
 fireEvent.click(boxes[0]);
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='thread_note_auto_set')?.[1],{projectId:'p',auto:false}));
 assert.equal(calls.some(([op])=>op==='subthread_review_set'),false);
});

test('17.1: a note passed on automatically to a busy thread is marked so, with nothing to press but the switch',async()=>{
 mountNotes({notes:[note({state:'passed',trigger:'auto',delivery:'queued',canPass:false,canDismiss:false,detail:'Passed on. It starts when this thread’s current run ends.'})],auto:true,autoPass:true});
 const card=await screen.findByTestId('thread-note');
 assert.equal(card.dataset.trigger,'auto');
 assert.equal(card.querySelector('.dv-thread-note-mark').textContent,'Passed on automatically');
 assert.equal(card.querySelector('[role="status"]').textContent,'Claude Code gets it when this thread’s current run ends, quoted as from 16.4 runs.');
 assert.equal(card.querySelector('button'),null);
 assert.equal(screen.getByRole('checkbox',{name:'Pass notes on automatically'}).checked,true);
});

test('17.1: with automatic passing off the switch shows unticked, and a held line never shows on a plain note',async()=>{
 mountNotes({notes:[note()],auto:true,autoPass:false});
 const card=await screen.findByTestId('thread-note');
 assert.equal(screen.getByRole('checkbox',{name:'Pass notes on automatically'}).checked,false);
 assert.equal(card.querySelector('.dv-thread-note-mark'),null);
 assert.match(card.querySelector('.dv-thread-note-why').textContent,/^Claude Code hasn’t seen this\./);
});

test('the review card lists each subthread, starts on a click, and holds the automatic switch',async()=>{
 const {calls,opened}=mountNotes({notes:[review()],auto:false});
 const card=await screen.findByTestId('subthread-review');
 assert.equal(card.querySelector('.dv-row-name').textContent,'Its 3 subthreads finished');
 assert.deepEqual([...card.querySelectorAll('.dv-facts dt')].map(dt=>[dt.querySelector('.dv-st').dataset.state,dt.textContent]),[['done','16.4 runs'],['done','16.4 thread'],['failed','16.4 Home']]);
 assert.match(card.textContent,/Starts a read-only run in this thread\..*not as your words\. Nothing is approved or changed\./);
 fireEvent.click(screen.getByRole('button',{name:'16.4 Home'}));
 assert.deepEqual(opened,['home']);
 const box=screen.getByRole('checkbox',{name:'Review finished subthreads automatically'});
 assert.equal(box.checked,false);
 fireEvent.click(box);
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='subthread_review_set')?.[1],{projectId:'p',auto:true}));
 fireEvent.click(screen.getByRole('button',{name:'Review them here'}));
 await waitFor(()=>assert.deepEqual(calls.find(([op])=>op==='thread_note_pass')?.[1],{projectId:'p',channelId:'home',noteId:'r1'}));
 assert.equal(reviewTitle(review({late:true,children:[review().children[2]]})),'16.4 Home finished again');
 assert.deepEqual(waitingNotes({notes:[review({state:'dismissed'}),review({state:'withdrawn'})],auto:true}),[]);
});

test('the run a note started opens with the note quoted as its sender’s and one line from Dervo, never as your message',()=>{
 const sent='A note from #16.4 runs, passed on by the user…';
 const origin={id:'n1',kind:'note',fromName:'16.4 runs',fromChannelId:'runs',text:'Home’s panel still reads the project level directly.',trigger:'click',late:false,children:[]};
 const task={id:'t9',origin:'conversation',note_origin:origin,messages:[{role:'user',text:sent,created_at:10},{role:'assistant',text:'Fixed.',created_at:11}]};
 const [first,second]=taskRows([task],'home');
 assert.equal(first.pubkey,'dervo-coordinator');
 assert.equal(first.content,'You passed this on from 16.4 runs.');
 assert.deepEqual(first.tags.find(tag=>tag[0]===NOTE_TAG),[NOTE_TAG,'note','16.4 runs',origin.text,sent]);
 assert.equal(second.pubkey,'provider');
 const reviewTask={...task,note_origin:{...origin,kind:'review',fromName:'Dervo',text:null,trigger:'auto',children:['16.4 runs','16.4 thread','16.4 Home']}};
 const [opening]=taskRows([reviewTask],'plan');
 assert.equal(opening.pubkey,'dervo-coordinator');
 assert.match(opening.content,/^Started on its own when they’d all finished\./);
 assert.equal(opening.tags.find(tag=>tag[0]===NOTE_TAG)[3],'16.4 runs, 16.4 thread and 16.4 Home');
 // 17.1: one Dervo passed on by itself says so, so the thread shows why it started again without you.
 const [auto]=taskRows([{...task,note_origin:{...origin,trigger:'auto'}}],'home');
 assert.equal(auto.pubkey,'dervo-coordinator');
 assert.equal(auto.content,'Passed on automatically from 16.4 runs, because this project passes notes on by itself.');
 assert.deepEqual(auto.tags.find(tag=>tag[0]===NOTE_TAG),[NOTE_TAG,'note','16.4 runs',origin.text,sent]);
 // Without the host's mark the same words stay an ordinary message: the row is never guessed from the text.
 assert.equal(taskRows([{...task,note_origin:undefined}],'home')[0].pubkey,'local-user');
 const quote=render(React.createElement(NoteQuote,{kind:'note',from:'16.4 runs',quote:origin.text,sent}));
 assert.equal(quote.container.querySelector('figcaption').textContent,'Note from 16.4 runs');
 assert.equal(quote.container.querySelector('blockquote').textContent,origin.text);
 cleanup();
 const sentReview=render(React.createElement(NoteQuote,{kind:'review',from:'Dervo',quote:'16.4 runs and 16.4 Home',sent:'Your subthreads have finished.'}));
 assert.equal(sentReview.container.querySelector('figcaption').textContent,'Review of 16.4 runs and 16.4 Home');
 assert.equal(sentReview.container.querySelector('details pre').textContent,'Your subthreads have finished.');
});

test('Home’s pane gathers subthreads under their parent the same way, and keeps the open card in its place',()=>{
 const items=[{id:'ideas',name:'Ideas storage',kind:'review'},{id:'runs',name:'16.4 runs',kind:'running'},{id:'thread',name:'16.4 thread',kind:'running'}];
 const opened=[];
 const sheet=open=>paneSheet(items,family,{open,where:'running',onOpen:id=>opened.push(id),
  row:(item,isOpen)=>React.createElement('article',{key:item.id,'data-row':item.id,'data-open':isOpen||undefined},item.name),
  kid:item=>({id:item.id,name:item.name,glyph:'running',line:`Working on ${item.name}`,when:'3 min'})});
 const view=render(React.createElement('div',null,sheet(null)));
 const group=view.container.querySelector('.dv-now-family');
 assert.match(group.querySelector('.dv-row-name').textContent,/^Beta 16\.4 plan2 of 2 running$/);
 assert.deepEqual([...group.querySelectorAll('.dv-now-kid-name')].map(n=>n.firstChild.textContent),['16.4 runs','16.4 thread']);
 assert.match(group.textContent,/Beta 16\.4 plan gets a review of them once they’ve all finished/);
 fireEvent.click(group.querySelectorAll('.dv-now-kid')[0]);
 fireEvent.click(group.querySelector('.dv-home-row-open'));
 assert.deepEqual(opened,['runs','plan'],'a subthread opens on its own; the parent row opens the parent');
 assert.ok(view.container.querySelector('[data-row="ideas"]'),'a thread with no parent keeps its own row');
 cleanup();
 const lifted=render(React.createElement('div',null,sheet('runs')));
 assert.ok(lifted.container.querySelector('[data-row="runs"][data-open]'),'the open card stays where it was');
 assert.deepEqual([...lifted.container.querySelectorAll('.dv-now-kid-name')].map(n=>n.firstChild.textContent),['16.4 thread']);
});
