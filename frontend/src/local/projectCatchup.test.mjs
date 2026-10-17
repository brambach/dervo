import assert from 'node:assert/strict';
import {test,after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','CustomEvent','Event','KeyboardEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),0);globalThis.cancelAnimationFrame=clearTimeout;
const React=await import('react');
const {render,fireEvent,cleanup,waitFor}=await import('@testing-library/react');
const {catchupPlan,whenLabel,CAPS,ProjectCatchup,privateNeeds}=await import('./ProjectCatchup.tsx');
after(()=>dom.window.close());

const NOW=Date.UTC(2026,8,17,9,0)/1000;
const convo=(id,name,activity=null,visibility='shared')=>({id,name,visibility,project_id:'p',profile_id:'a',model:'m',activity});
const row=(id,channelId,extra)=>({id,kind:'task',channelId,channelName:channelId,snippet:'',source:{},title:`Request in ${channelId}`,...extra});

test('the plan puts what needs you first, then running work with its reasons, then what changed, then the thread you last opened',()=>{
 const conversations=[convo('a','auth'),convo('b','billing',{status:'running',running:true,step:'Editing rank.ts',unread:0,since:NOW-95,goal:'Rank titles first',why:'Old notes outrank the page',next:'Run rank tests'}),convo('c','copy'),convo('d','docs'),convo('e','private',{status:'needs',running:false,step:null,unread:0},'private')];
 const rows=[
  row('t1','a',{status:'blocked',runState:'failed',blocker:'The server is overloaded.',isNew:true,askedAt:NOW-600}),
  row('t0','a',{status:'completed',completedAt:NOW-9000}),
  row('t2','c',{status:'completed',completedAt:NOW-300,changedFiles:['upload.py','test_upload.py','README.md'],changedFileCount:3,testExitStatus:0,reviewPhase:'passed',isNew:true}),
  row('t3','d',{status:'answered',answeredAt:NOW-7200}),
  row('h','c',{status:'completed',helper:true,isNew:true}),
 ];
 const plan=catchupPlan(rows,conversations,{since:NOW-3600,last:'d',now:NOW});
 assert.match(plan.stand,/: 1 new result, 2 need you, 1 running\.$/);
 assert.deepEqual(plan.needs.map(n=>[n.name,n.what,n.action]),[['auth','Couldn’t finish: The server is overloaded.','See what stopped it'],['private','Needs you','Open it']],'a private thread the index says nothing more about says only that');
 assert.deepEqual(plan.next,{where:'needs',key:'task:t1',name:'auth',line:'Couldn’t finish: The server is overloaded.',action:'See what stopped it',glyph:'failed',channelId:'a',taskId:'t1'});
 assert.deepEqual(plan.needs.map(n=>[n.glyph,!!n.private]),[['failed',false],['needs',true]],'the glyph’s shape says which kind, as on Home and in the sidebar');
 assert.deepEqual(plan.running,[{channelId:'b',name:'billing',step:'Editing rank.ts',goal:'Rank titles first',why:'Old notes outrank the page',next:'Run rank tests',since:NOW-95}]);
 assert.equal(plan.changedHeading,'What changed');
 assert.deepEqual(plan.changed.map(c=>[c.name,c.outcome]),[['copy','Changed upload.py, test_upload.py and 1 more · check passed · review passed']],'only what landed since you looked, and never helper work');
 assert.deepEqual(plan.leftOff&&[plan.leftOff.name,plan.leftOff.asked,plan.leftOff.status],['docs','Request in d','Answered in the thread']);
});

test('with nothing waiting, the next step follows what landed, then running work, then the thread you last opened',()=>{
 const quiet=[convo('d','docs')];
 const settled=[row('t3','d',{status:'answered',answeredAt:NOW-7200})];
 const leftOnly=catchupPlan(settled,quiet,{since:NOW-60,last:'d',now:NOW});
 assert.equal(leftOnly.stand,`Nothing new since ${whenLabel(NOW-60,NOW)}. Nothing needs you.`);
 assert.deepEqual([leftOnly.next.where,leftOnly.next.name,leftOnly.next.line,leftOnly.next.action],['left','docs','Answered in the thread','Continue']);
 assert.equal(leftOnly.leftOff?.name,'docs','the next step is that thread’s own row, lifted, so it shows once');
 assert.equal(leftOnly.changedHeading,'Latest results');
 const running=catchupPlan([],[convo('b','billing',{status:'running',running:true,step:'Reading a.ts',unread:0,since:NOW-65})],{now:NOW});
 assert.deepEqual([running.next.where,running.next.name,running.next.line,running.next.action],['running','billing','Reading a.ts (1 min)','Watch it']);
 const empty=catchupPlan([],[],{now:NOW});
 assert.deepEqual([empty.stand,empty.next.action,empty.next.line],['Nothing here yet.','New thread','Start a thread and hand Dervo something to do.']);
 // Negative: a conversation that isn't in your list any more (archived, someone else's private one) never appears.
 assert.deepEqual(catchupPlan([row('x','gone',{status:'blocked',isNew:true})],[],{now:NOW}).needs,[]);
});

test('lists stay short: three need you, three running, five changes, and the rest is counted',()=>{
 const many=[...Array(6)].map((_,i)=>convo(`n${i}`,`needs ${i}`,{status:'needs',running:false,step:null,unread:0},'private'));
 const runs=[...Array(5)].map((_,i)=>convo(`r${i}`,`run ${i}`,{status:'running',running:true,step:'Working',unread:0,since:NOW-i}));
 const done=[...Array(8)].map((_,i)=>convo(`d${i}`,`done ${i}`));
 const plan=catchupPlan(done.map((c,i)=>row(`t${i}`,c.id,{status:'answered',isNew:true})),[...many,...runs,...done],{now:NOW});
 assert.deepEqual([plan.needs.length,plan.moreNeeds,plan.running.length,plan.moreRunning,plan.changed.length],[CAPS.needs,3,CAPS.running,2,CAPS.changed]);
});

test('times read the way you would say them',()=>{
 const at=(d,h,m)=>Date.UTC(2026,8,d,h,m)/1000;
 assert.doesNotMatch(whenLabel(at(17,8,4),NOW),/yesterday|Sep/);
 assert.match(whenLabel(at(16,8,4),NOW),/^yesterday /);
 assert.match(whenLabel(at(1,8,4),NOW),/Sep/);
});

test('the screen opens on its briefing, reads only catch-up records, and the next step opens its task',async()=>{
 const calls=[],opened=[];
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);return {results:[row('t1','a',{status:'ready_for_review',humanApprovalRequired:true,isNew:true})],cursor:null,incomplete:false,since:NOW-3600,lessonsPending:2};}};
 const view=render(React.createElement(ProjectCatchup,{projectId:'p',profileId:'a',projectName:'Dervo',conversations:[convo('a','auth')],lastConversation:null,open:true,onClose(){},onOpenConversation:id=>opened.push(['conversation',id]),onOpenTask:(id,task)=>opened.push(['task',id,task]),onOpenKnowledge(){},onAllActivity(){},onSearch(){},onNewConversation(){}}));
 try{
  await waitFor(()=>assert.match(view.getByTestId('catchup-stand').textContent,/1 needs you/));
  assert.deepEqual(calls,[['project_catchup',{projectId:'p',channelId:'',profileId:'a',scope:'project',limit:30}]]);
  // Design review 23 Sep, item 5: the page title is Catch up, in the display serif, with the project as its kicker.
  const title=view.getByRole('heading',{level:1});
  assert.equal(title.textContent,'Catch up');
  assert.ok(title.classList.contains('dv-display-1'));
  assert.equal(view.container.querySelector('.dervo-catchup-kicker').textContent,'Dervo');
  // Home's rows and glyphs, one lifted row, no channel-style # in front of a thread's name.
  assert.equal(view.container.querySelectorAll('.dv-row.dv-open').length,1,'only the next step is lifted');
  assert.ok(view.container.querySelector('.dv-row.dv-open .dv-st'),'with the status glyph, not a new alert icon');
  assert.doesNotMatch(view.container.textContent,/#/);
  assert.ok(view.getByTestId('catchup-next').classList.contains('dv-btn-primary'),'the next step is the page’s one pine pill');
  fireEvent.click(view.getByTestId('catchup-next'));
  assert.deepEqual(opened,[['task','a','t1']]);
  assert.ok(view.getByRole('button',{name:'Knowledge · 2 lessons to review'}));
 }finally{cleanup();}
});

// dd #21 (Bryce, 29 Sep and 2 Oct): Up and Down go through the threads here as they do in Home's panel.
test('Up and Down, or j and k, go from row to row, starting from the heading, and wrap at the ends',async()=>{
 const rows=[row('t1','a',{status:'blocked',runState:'failed',blocker:'Stopped.',isNew:true,askedAt:NOW-600}),row('t2','c',{status:'completed',completedAt:NOW-300,isNew:true})];
 window.__TAURI_INTERNALS__={invoke:async()=>({results:rows,cursor:null,incomplete:false,since:NOW-3600,lessonsPending:0})};
 const running={status:'running',running:true,step:'Editing rank.ts',unread:0,since:NOW-95};
 const view=render(React.createElement(ProjectCatchup,{projectId:'p',profileId:'a',projectName:'Dervo',conversations:[convo('a','auth'),convo('b','billing',running),convo('c','copy')],lastConversation:null,open:true,onClose(){},onOpenConversation(){},onOpenTask(){},onOpenKnowledge(){},onAllActivity(){},onSearch(){},onNewConversation(){}}));
 try{
  await waitFor(()=>assert.match(view.getByTestId('catchup-stand').textContent,/1 needs you/));
  const heading=view.getByRole('heading',{level:1});
  await waitFor(()=>assert.equal(document.activeElement,heading),'the screen opens on its heading');
  const press=key=>fireEvent.keyDown(document.activeElement,{key});
  const at=()=>document.activeElement.closest('[role="listitem"]')?.textContent;
  assert.equal(press('ArrowDown'),false,'the key was taken');
  assert.equal(document.activeElement,view.getByTestId('catchup-next'),'the lifted row first, on its own button');
  press('ArrowDown');assert.match(at(),/^billing/);
  press('j');assert.match(at(),/^copy/);
  press('ArrowDown');assert.equal(document.activeElement,view.getByTestId('catchup-next'),'past the last row is the first');
  press('k');assert.match(at(),/^copy/);
  press('Escape');
 }finally{cleanup();}
});

test('a result still under review isn’t a result yet, and the sidebar fallback never doubles a conversation with its own rows (17 Sep review)',()=>{
 const running={status:'running',running:true,step:'Independent review in progress',unread:0,since:NOW-30};
 const plan=catchupPlan([row('t1','a',{status:'ready_for_review',reviewPending:true,isNew:true})],[convo('a','auth',running)],{now:NOW});
 assert.deepEqual([plan.changed.length,plan.running.length],[0,1]);
 const shared=catchupPlan([row('t2','b',{status:'answered',isNew:true})],[convo('b','billing',{status:'needs',running:false,step:null,unread:0})],{now:NOW});
 assert.deepEqual([shared.needs.length,shared.changed.length],[0,1],'its own newest row speaks for it');
});

test('a conversation you moved on from or dismissed no longer needs you, and Dismiss clears it (17 Sep: "no way to clear them")',async()=>{
 const blocked=row('t1','a',{status:'blocked',runState:'failed',blocker:'Stopped.',isNew:true});
 const still=catchupPlan([blocked],[convo('a','auth',{status:'needs',running:false,step:null,unread:0,attention:false})],{now:NOW});
 assert.equal(still.needs.length,1,'opening it is not acting on it: Catch up keeps it');
 const cleared=catchupPlan([blocked],[convo('a','auth',{status:null,running:false,step:null,unread:0})],{now:NOW});
 assert.deepEqual([cleared.needs.length,cleared.next.action],[0,'Read it'],'the index says it no longer needs you, so it reads as a result');
 const calls=[];let refreshed=0;
 window.__TAURI_INTERNALS__={invoke:async(_c,{operation,args})=>{calls.push([operation,args]);return operation==='project_catchup'?{results:[blocked],cursor:null,incomplete:false,since:NOW-3600,lessonsPending:0}:{id:args.channelId};}};
 const view=render(React.createElement(ProjectCatchup,{projectId:'p',profileId:'a',projectName:'Dervo',conversations:[convo('a','auth',{status:'needs',running:false,step:null,unread:0,attention:true})],lastConversation:null,open:true,onConversationsChanged:async()=>{refreshed++;},onClose(){},onOpenConversation(){},onOpenTask(){},onOpenKnowledge(){},onAllActivity(){},onSearch(){},onNewConversation(){}}));
 try{
  const dismiss=await waitFor(()=>view.getByRole('button',{name:'Dismiss'}));
  fireEvent.click(dismiss);
  await waitFor(()=>assert.deepEqual(calls.filter(c=>c[0]==='conversation_dismiss'),[['conversation_dismiss',{projectId:'p',channelId:'a'}]]));
  await waitFor(()=>assert.equal(refreshed,1));
 }finally{cleanup();}
});

test('Knowledge’s line in Catch up names what waits there, lessons and skills together',async()=>{
 const {knowledgeLabel}=await import('./ProjectCatchup.tsx');
 assert.equal(knowledgeLabel(0,0),'Knowledge');
 assert.equal(knowledgeLabel(null,undefined),'Knowledge');
 assert.equal(knowledgeLabel(1,0),'Knowledge · 1 lesson to review');
 assert.equal(knowledgeLabel(0,2),'Knowledge · 2 skills to review');
 assert.equal(knowledgeLabel(2,1),'Knowledge · 2 lessons and 1 skill to review');
});

test('a private thread says how it ended in Home’s words, and only what the index says when it doesn’t (UX review 23 Sep, M7)',()=>{
 const needs=(ended,summary)=>privateNeeds(convo('p','private thread',{status:'needs',running:false,step:null,unread:1,ended,summary},'private'));
 const said=item=>[item.glyph,item.what,item.detail??null,item.action];
 assert.deepEqual(said(needs('asking')),['needs','Asked you one thing before it carries on.',null,'Answer']);
 assert.deepEqual(said(needs('needs_permission')),['needs','Waiting for your permission before it starts.',null,'Review the scope']);
 assert.deepEqual(said(needs('blocked','Claude hit its usage limit.')),['failed','Stopped','Claude hit its usage limit.','Pick it up']);
 assert.deepEqual(said(needs('disconnected')),['unknown','Stopped','Dervo couldn’t confirm how it ended.','Pick it up']);  // beta.17.1: unknown, not stopped
 assert.deepEqual(said(needs('ready_for_review','Finished. Waiting for your review.')),['done','Finished',null,'Review'],'the summary isn’t repeated when it opens with the same word');
 // Negative: nothing about how it ended, so it never claims "Waiting for you" or a state Home doesn't show.
 assert.deepEqual(said(needs(null,'Changed 6 files. Checks passed.')),['needs','Changed 6 files. Checks passed.',null,'Open it']);
 assert.deepEqual(said(needs(null,null)),['needs','Needs you',null,'Open it']);
 assert.equal(needs('asking').private,true);
});

test('rust lands once, on the lifted row, and only when its glyph says it asks you something',async()=>{
 window.__TAURI_INTERNALS__={invoke:async()=>({results:[row('t2','b',{status:'blocked',runState:'failed',blocker:'Timed out.',isNew:true})],cursor:null,incomplete:false,since:NOW-3600,lessonsPending:0})};
 const asking=convo('a','ideas',{status:'needs',running:false,step:null,unread:1,ended:'asking'},'private');
 const view=render(React.createElement(ProjectCatchup,{projectId:'p',profileId:'a',projectName:'Dervo',conversations:[asking,convo('b','billing',{status:'needs',running:false,step:null,unread:1})],lastConversation:null,open:true,onClose(){},onOpenConversation(){},onOpenTask(){},onOpenKnowledge(){},onAllActivity(){},onSearch(){},onNewConversation(){}}));
 try{
  await waitFor(()=>assert.match(view.getByTestId('catchup-stand').textContent,/2 need you/));
  const accents=view.container.querySelectorAll('.dv-st[data-accent]');
  const lead=view.container.querySelector('.dv-row.dv-open');
  // The shared record comes first in the queue, so the failed run is lifted: a cross never turns rust.
  assert.equal(lead.querySelector('.dv-st').dataset.state,'failed');
  assert.equal(accents.length,0);
  assert.match(view.container.textContent,/ideas · privateAsked you one thing before it carries on\./,'the private thread in Home’s words');
 }finally{cleanup();}
 window.__TAURI_INTERNALS__={invoke:async()=>({results:[],cursor:null,incomplete:false,since:NOW-3600,lessonsPending:0})};
 const alone=render(React.createElement(ProjectCatchup,{projectId:'p',profileId:'a',projectName:'Dervo',conversations:[asking],lastConversation:null,open:true,onClose(){},onOpenConversation(){},onOpenTask(){},onOpenKnowledge(){},onAllActivity(){},onSearch(){},onNewConversation(){}}));
 try{
  await waitFor(()=>assert.match(alone.getByTestId('catchup-stand').textContent,/1 needs you/));
  const accents=[...alone.container.querySelectorAll('.dv-st[data-accent]')];
  assert.equal(accents.length,1,'the question it asked is the one rust mark');
  assert.ok(alone.container.querySelector('.dv-row.dv-open').contains(accents[0]));
  assert.equal(alone.getByTestId('catchup-next').textContent,'Answer');
 }finally{cleanup();}
});
