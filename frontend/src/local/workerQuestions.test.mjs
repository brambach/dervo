import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','Element','MutationObserver','Node','Event','KeyboardEvent','localStorage'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,screen,cleanup,waitFor,act}=await import('@testing-library/react');
const {WorkerQuestions}=await import('./WorkerQuestions.tsx');
const {openQuestion,trayQuestion,trayAnswer}=await import('./workerQuestionState.ts');
const {readWorkerQuestion,answerProblem,loadQuestionDraft,saveQuestionDraft,questionDraftKey,questionReceipt}=await import('./workerQuestionState.ts');
const scope={projectId:'p',channelId:'c',taskId:'task-1',questionId:'question-1'};
const question=(extra={})=>({id:'question-1',taskId:'task-1',runId:'run-1',asker:'Claude',ask:'Which format should I use?',options:[{id:'plain',label:'Plain text',detail:'Keep it easy to edit',grant:null},{id:'markdown',label:'Markdown',detail:null,grant:null}],state:'pending',answer:null,answerClientId:null,delivery:null,canAnswer:true,canSkip:true,detail:null,createdAt:1,...extra});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const answered=(args,extra={})=>question({state:args.answer.kind==='skip'?'skipped':'answered',answer:args.answer,answerClientId:args.clientId,delivery:args.answer.kind==='skip'?null:'sent',canAnswer:false,canSkip:false,...extra});
function host(handler=(_operation,args)=>answered(args)){const calls=[];window.__TAURI_INTERNALS__={invoke:async(_command,{operation,args})=>{calls.push({operation,args});return handler(operation,args);}};return calls;}
/** The thread as ConnectedApp lays it out: the tray, then the composer's root right after it (the tray reads keys from
 * its next sibling), and an unrelated field elsewhere on the page. */
function mount(q=question(),over={}){
 const changed=[],cleared=[];const handle=React.createRef();
 const props={projectId:'p',channelId:'c',questions:[q],onChanged:async()=>{changed.push(true);},composerText:'',onClearComposer:()=>cleared.push(true),...over};
 const make=next=>React.createElement(React.Fragment,null,React.createElement(WorkerQuestions,{ref:handle,...props,...next}),
  React.createElement('div',{className:'dv-send-root'},React.createElement('textarea',{'aria-label':'Normal message',defaultValue:''})),
  React.createElement('input',{'aria-label':'Other field',defaultValue:'Keep my other text'}));
 const view=render(make({}));return {view,changed,cleared,handle,rerender:next=>view.rerender(make(next))};
}
const status=()=>screen.getByRole('status').textContent;
afterEach(()=>{cleanup();localStorage.clear();delete window.__TAURI_INTERNALS__;});after(()=>dom.window.close());

test('worker questions support free text without choices and never accept permission grants or option destinations',()=>{
 assert.equal(readWorkerQuestion(question({options:[]})).options.length,0);
 assert.equal(readWorkerQuestion(question({options:[{...question().options[0],grant:{runs:['rm'],wont:'x'}}]})),null);
 assert.equal(readWorkerQuestion(question({options:Array(4).fill(question().options[0])})),null);
 const safe=readWorkerQuestion({...question(),to:'other-thread',options:question().options.map(option=>({...option,command:'danger',to:'elsewhere'}))});
 assert.equal('to' in safe,false);assert.equal('command' in safe.options[0],false);
 assert.equal(answerProblem('😀'.repeat(2048)),null);assert.match(answerProblem('😀'.repeat(2049)),/8 KB/);
 // On the tray: its own choices, then Skip as a quiet last one, never a grant; a pick maps back to the choice's ID.
 const shown=trayQuestion(readWorkerQuestion(question()));
 assert.deepEqual(shown.options.map(option=>[option.label,!!option.quiet,option.grant]),[['Plain text',false,null],['Markdown',false,null],['Skip',true,null]]);
 assert.deepEqual(trayAnswer(readWorkerQuestion(question()),{option:1}),{kind:'option',optionId:'markdown'});
 assert.deepEqual(trayAnswer(readWorkerQuestion(question()),{option:2}),{kind:'skip'});
 assert.equal(trayAnswer(readWorkerQuestion(question({canSkip:false})),{option:2}),null,'no Skip unless the question allows it');
});

test('the thread tray is Home’s: its header names who gets the answer, keys pick, and Skip sits last and quiet',()=>{
 host();mount();
 const tray=screen.getByRole('group',{name:'Claude is asking'});
 assert.match(tray.querySelector('.dv-qtray-head').textContent,/^Answer goes to Claude/);
 assert.deepEqual([...tray.querySelectorAll('.dv-qtray-label')].map(label=>label.textContent),['Plain text','Markdown','Skip']);
 assert.ok(tray.querySelector('[data-quiet] .dv-qtray-label'),'Skip is quiet');
 assert.equal(tray.nextElementSibling.className,'dv-send-root','the tray sits directly on the composer');
 assert.equal(document.querySelector('textarea[aria-label="Your answer"]'),null,'no second place to type');
 assert.equal(document.querySelector('details'),null,'no Source IDs on screen');
});

test('choosing an option makes one exact scoped request and leaves every draft alone',async()=>{
 const held=deferred(),calls=host((_op,args)=>held.promise.then(()=>answered(args)));const {changed}=mount();
 const button=screen.getByRole('button',{name:/Plain text/});fireEvent.click(button);fireEvent.click(button);
 assert.equal(calls.length,1);assert.equal(calls[0].operation,'task_question_answer');
 const {clientId,...payload}=calls[0].args;assert.ok(clientId);
 assert.deepEqual(payload,{...scope,answer:{kind:'option',optionId:'plain'}});
 assert.equal(screen.getByLabelText('Other field').value,'Keep my other text');
 await act(async()=>held.resolve());await waitFor(()=>assert.equal(changed.length,1));
 assert.match(status(),/You answered “Plain text”/);
 assert.equal(localStorage.getItem(questionDraftKey(scope)),null);
});

test('a number key on the page picks, and the same key in another field or with words in the composer doesn’t',async()=>{
 const calls=host();const {rerender,cleared}=mount();
 fireEvent.keyDown(screen.getByLabelText('Other field'),{key:'1'});fireEvent.keyDown(screen.getByLabelText('Other field'),{key:'Enter'});
 assert.equal(calls.length,0,'another field’s keys are its own');
 rerender({composerText:'Both, but only while they run'});
 const folded=screen.getByRole('group',{name:'Claude is asking'});
 assert.match(folded.textContent,/Your words answer Claude/);assert.equal(folded.dataset.folded,'typing');
 fireEvent.keyDown(document.body,{key:'1'});assert.equal(calls.length,0,'words in the composer turn the keys off');
 fireEvent.keyDown(screen.getByLabelText('Normal message'),{key:'Escape'});assert.equal(cleared.length,1,'Esc empties the composer so the choices come back');
 rerender({composerText:''});
 fireEvent.keyDown(document.body,{key:'2'});await waitFor(()=>assert.equal(calls.length,1));
 assert.deepEqual(calls[0].args.answer,{kind:'option',optionId:'markdown'});
});

test('words from the thread composer answer the question exactly, and nothing when no question is open',async()=>{
 const calls=host();const {handle}=mount(question({options:[]}));const raw='  α😀\nsecond line\t';
 let answered;await act(async()=>{answered=await handle.current.answerInWords(raw);});
 assert.equal(answered,true);assert.equal(calls.length,1);assert.deepEqual(calls[0].args.answer,{kind:'text',text:raw});
 cleanup();localStorage.clear();
 const stale=mount(question({state:'stale',canAnswer:false,canSkip:true}));
 await act(async()=>{answered=await stale.handle.current.answerInWords('late words');});
 assert.equal(answered,false,'a message, not an answer');assert.equal(calls.length,1);
 assert.equal(openQuestion([question({state:'stale',canAnswer:false})],'p','c'),null);
 assert.equal(openQuestion([question()],'p','c',true),null,'nothing is answerable while account access is off');
 assert.equal(openQuestion([question()],'p','c').id,'question-1');
});

test('a lost response keeps an immutable request across remount and retry never changes its identity or answer',async()=>{
 let fail=true;const calls=host((_op,args)=>{if(fail)throw Error('Reply lost');return answered(args,{delivery:'queued'});});
 const first=mount();await act(async()=>{await first.handle.current.answerInWords('My exact answer');});
 await screen.findByRole('alert');const sent=calls[0].args;first.view.unmount();mount();
 assert.equal(calls.length,1,'remount never retries automatically');
 assert.match(status(),/Your answer, “My exact answer”, is saved\. Claude hasn’t confirmed it yet\./);
 assert.equal(screen.queryByRole('button',{name:/Plain text/}),null,'no second answer while one is saved');fail=false;
 fireEvent.click(screen.getByRole('button',{name:'Retry'}));await waitFor(()=>assert.equal(calls.length,2));
 assert.deepEqual(calls[1].args,sent);await waitFor(()=>assert.match(status(),/waits for the task to finish/));
});

test('a host receipt reconciles only its own saved submission and unknown delivery never offers a resend',async()=>{
 const submission={clientId:'human-1',answer:{kind:'text',text:'Saved answer'}};saveQuestionDraft(scope,{text:'',pending:submission});
 const calls=host();mount(question({state:'answered',answer:submission.answer,answerClientId:'human-1',delivery:'unknown',canAnswer:false,canSkip:false}));
 await waitFor(()=>assert.equal(localStorage.getItem(questionDraftKey(scope)),null));
 assert.match(status(),/Delivery to the task isn’t confirmed/);assert.equal(screen.queryByRole('button',{name:/Retry/}),null);assert.equal(calls.length,0);
});

test('another answer does not erase your saved one or send it to a settled question',()=>{
 saveQuestionDraft(scope,{text:'',pending:{clientId:'my-answer',answer:{kind:'text',text:'My unsent answer'}}});host();
 mount(question({state:'answered',answerClientId:'another-answer',answer:{kind:'text',text:'Other answer'},delivery:'sent',canAnswer:false,canSkip:false}));
 assert.match(status(),/answered another way\. Yours, “My unsent answer”, wasn’t sent/);
 assert.equal(loadQuestionDraft(scope).pending.clientId,'my-answer');assert.equal(screen.queryByRole('button',{name:/Retry/}),null);
 fireEvent.click(screen.getByRole('button',{name:'Dismiss'}));assert.equal(localStorage.getItem(questionDraftKey(scope)),null,'only when you dismiss it');
});

test('Skip stays available for a stale question with account access disabled and starts no composer send',async()=>{
 const calls=host();mount(question({state:'stale',canAnswer:false,canSkip:true}),{disabled:true});
 assert.match(status(),/Claude’s run ended before this was answered/);
 fireEvent.click(screen.getByRole('button',{name:'Clear it'}));await waitFor(()=>assert.equal(calls.length,1));
 assert.deepEqual(calls[0].args.answer,{kind:'skip'});await waitFor(()=>assert.match(status(),/You skipped it/));
});

test('a late answer response cannot overwrite the next thread or clear its draft',async()=>{
 const held=deferred(),calls=host((_op,args)=>held.promise.then(()=>answered(args)));const {rerender,changed,handle}=mount();
 let pending;act(()=>{pending=handle.current.answerInWords('First thread');});
 rerender({channelId:'other',questions:[question({id:'other-question',taskId:'other-task',runId:'other-run',ask:'What about this thread?'})]});
 await act(async()=>{held.resolve();await pending;});
 assert.equal(changed.length,0);assert.equal(calls.length,1);assert.ok(screen.getByRole('button',{name:/Plain text/}),'the next thread’s question is still open');
 assert.equal(loadQuestionDraft(scope).pending.clientId,calls[0].args.clientId,'old submission waits for its own host receipt');
});

test('a failed answer that becomes stale can still be skipped without replaying that answer',async()=>{
 let fail=true;const calls=host((_op,args)=>{if(fail)throw Error('Access changed');return answered(args);});const {rerender,handle}=mount();
 await act(async()=>{await handle.current.answerInWords('Keep the old answer');});await screen.findByRole('alert');
 rerender({questions:[question({state:'stale',canAnswer:false,canSkip:true})],disabled:true});
 assert.match(status(),/“Keep the old answer”, is saved/);fail=false;fireEvent.click(screen.getByRole('button',{name:'Skip'}));
 await waitFor(()=>assert.equal(calls.length,2));assert.deepEqual(calls[1].args.answer,{kind:'skip'});assert.notEqual(calls[0].args.clientId,calls[1].args.clientId);
});

test('an answer response for another task is refused without clearing the retry',async()=>{
 host((_op,args)=>answered(args,{taskId:'another-task'}));mount();fireEvent.click(screen.getByRole('button',{name:/Markdown/}));
 assert.match((await screen.findByRole('alert')).textContent,/didn’t match/);assert.ok(loadQuestionDraft(scope).pending);assert.ok(screen.getByRole('button',{name:'Retry'}));
});

test('losing beta access still permits local Skip and an uncertain Skip retries its original identity',async()=>{
 const calls=host(()=>{throw Error('Reply lost');});const {rerender}=mount();
 fireEvent.click(screen.getByRole('button',{name:/Markdown/}));await screen.findByRole('alert');rerender({disabled:true});
 fireEvent.click(screen.getByRole('button',{name:'Skip'}));await waitFor(()=>assert.equal(calls.length,2));await screen.findByRole('alert');
 assert.deepEqual(calls[1].args.answer,{kind:'skip'});assert.notEqual(calls[0].args.clientId,calls[1].args.clientId);
 fireEvent.click(screen.getByRole('button',{name:'Retry'}));await waitFor(()=>assert.equal(calls.length,3));assert.deepEqual(calls[1].args,calls[2].args);
});

test('over-limit words and local storage failure send nothing and leave the words in the composer',async()=>{
 const calls=host();const {handle}=mount();
 let refused=null;await act(async()=>{await handle.current.answerInWords('😀'.repeat(2049)).catch(error=>{refused=error;});});assert.match(String(refused),/8 KB/);
 assert.match(screen.getByRole('alert').textContent,/8 KB/);assert.equal(calls.length,0);
 const original=dom.window.Storage.prototype.setItem;
 dom.window.Storage.prototype.setItem=function(){throw Error('Quota full');};
 try{refused=null;await act(async()=>{await handle.current.answerInWords('Still here').catch(error=>{refused=error;});});assert.match(String(refused),/Nothing was sent/);assert.equal(calls.length,0);}
 finally{dom.window.Storage.prototype.setItem=original;}
});

test('an answered question isn’t shown above the composer while pending questions stay actionable, one at a time',()=>{
 // Beta.15 (Bryce, 24 Sep): a "Previous answer" card stayed above the composer during the next run, with two receipts.
 host();mount(question(),{questions:[question(),question({id:'old',state:'answered',answer:{kind:'text',text:'Before'},answerClientId:'old-answer',delivery:'sent',canAnswer:false,canSkip:false,detail:'Sent to the source task. Dervo can’t confirm that the agent read it.'})]});
 assert.equal(document.querySelector('[data-question-id="old"]'),null);
 assert.ok(screen.getByRole('button',{name:/Plain text/}));
 cleanup();
 for(const settled of [question({state:'answered',answer:{kind:'option',optionId:'plain'},answerClientId:'a',delivery:'sent',canAnswer:false,canSkip:false}),question({state:'skipped',answer:{kind:'skip'},answerClientId:'s',canAnswer:false,canSkip:false})]){
  mount(settled);assert.equal(document.querySelector('.dv-qtray'),null,`${settled.state}: nothing above the composer`);cleanup();
 }
 // Two tasks asking at once: the oldest first, with "1 of 2".
 mount(question(),{questions:[question({id:'newer',taskId:'task-2',ask:'Newer?',createdAt:5}),question({createdAt:2})]});
 const head=document.querySelector('.dv-qtray-head').textContent;
 assert.match(head,/Answer goes to Claude · 1 of 2/);assert.equal(document.querySelector('.dv-qtray-opt').closest('[role="group"]').getAttribute('aria-label'),'Claude is asking');
 assert.ok(screen.getByRole('button',{name:/Plain text/}),'the older question is the one shown');
 assert.equal(questionReceipt(question({state:'answered',delivery:'not_sent'})),'Answer saved. It wasn’t sent to the agent.');
});

test('a question whose delivery isn’t confirmed says so once',()=>{
 host();mount(question({state:'answered',answer:{kind:'text',text:'Beta.15'},answerClientId:'a',delivery:'unknown',canAnswer:false,canSkip:false,detail:'Answer saved. Dervo couldn’t confirm it reached the agent.'}));
 assert.equal(screen.getAllByRole('status').length,1);assert.equal(status(),'Your answer is saved. Delivery to the task isn’t confirmed.');
});

test('a settled question is one quiet line in its task’s history',async()=>{
 const {QuestionHistory}=await import('./WorkerQuestions.tsx');
 const icon=question({id:'icon',ask:'Should the Mac icon picker and founder icon go into beta.15?',options:[{id:'b15',label:'Beta.15',detail:null,grant:null}],state:'answered',answer:{kind:'option',optionId:'b15'},answerClientId:'a',delivery:'sent',canAnswer:false,canSkip:false,createdAt:2});
 const skipped=question({id:'skip',ask:'Which format should I use?',state:'skipped',answer:{kind:'skip'},answerClientId:'s',canAnswer:false,canSkip:false,createdAt:1});
 render(React.createElement(QuestionHistory,{projectId:'p',channelId:'c',questions:[icon,skipped,question({id:'waiting'})]}));
 const lines=[...document.querySelectorAll('.dv-question-history li')].map(line=>line.textContent);
 assert.deepEqual(lines,['Claude asked “Which format should I use?” You skipped it.','Claude asked “Should the Mac icon picker and founder icon go into beta.15?” You answered “Beta.15”.'],'oldest first, the pending one stays in the tray');
 const {questionLine}=await import('./workerQuestionState.ts');
 assert.equal(questionLine(readWorkerQuestion(question({state:'answered',answer:{kind:'text',text:'Plain'},delivery:'not_sent',ask:'Format'}))),'Claude asked “Format”. You answered “Plain”. It wasn’t sent to the agent.');
});

test('beta.16.1: the tray shows the question itself above its choices, and while you type the answer',()=>{
 host();const {rerender}=mount();
 // Bryce, 26 Sep: "i cant see the question only the answers". The tray listed the choices without the question.
 const tray=screen.getByRole('group',{name:'Claude is asking'});
 const ask=tray.querySelector('.dv-qtray-ask');
 assert.equal(ask?.textContent,'Which format should I use?');
 assert.equal(ask.getAttribute('data-asker'),'Claude','quoted as the asker’s words');
 assert.ok(ask.compareDocumentPosition(tray.querySelector('.dv-qtray-list'))&Node.DOCUMENT_POSITION_FOLLOWING,'the question sits above the choices');
 rerender({composerText:'Plain, please'});
 assert.equal(screen.getByRole('group',{name:'Claude is asking'}).querySelector('.dv-qtray-ask')?.textContent,'Which format should I use?','still there while you type');
 cleanup();mount(question({options:[],canSkip:false}));
 assert.equal(document.querySelector('.dv-qtray[data-folded="words"] .dv-qtray-ask')?.textContent,'Which format should I use?');
});

test('beta.16.1: a question whose run ended keeps its question and choices, and your pick goes in as the next message',async()=>{
 const calls=host((_op,args)=>answered(args,{carried:true,delivery:'queued'}));
 const stale=question({state:'stale',canAnswer:false,canSkip:true,canCarry:true,detail:'The source task stopped or its outcome is uncertain. Start a new message to continue.'});
 mount(stale);
 const tray=screen.getByRole('group',{name:'Claude is asking'});
 assert.match(tray.querySelector('.dv-qtray-head').textContent,/^Claude’s run ended\. Your answer starts a new message/);
 assert.equal(tray.querySelector('.dv-qtray-ask')?.textContent,'Which format should I use?');
 assert.deepEqual([...tray.querySelectorAll('.dv-qtray-label')].map(label=>label.textContent),['Plain text','Markdown','Skip']);
 assert.equal(screen.queryByRole('button',{name:'Clear it'}),null,'Skip is the quiet last choice instead');
 assert.equal(openQuestion([stale],'p','c')?.id,'question-1','words in the composer answer it too');
 fireEvent.click(screen.getByRole('button',{name:/Plain text/}));
 await waitFor(()=>assert.equal(calls.length,1));
 assert.deepEqual([calls[0].operation,calls[0].args.answer],['task_question_answer',{kind:'option',optionId:'plain'}]);
});

test('beta.16.1: a question that can’t be answered any more still shows what was asked, with Clear it',()=>{
 host();
 const stuck=question({state:'stale',canAnswer:false,canSkip:true,canCarry:false,detail:'Project access changed after this question. Start a new message with the current permissions.'});
 mount(stuck);
 const tray=screen.getByRole('group',{name:'Claude asked'});
 assert.equal(status(),'Claude’s run ended before this was answered. Project access changed after this question. Start a new message with the current permissions.');
 assert.equal(tray.querySelector('.dv-qtray-ask')?.textContent,'Which format should I use?');
 assert.ok(screen.getByRole('button',{name:'Clear it'}));
 assert.equal(openQuestion([stuck],'p','c'),null,'your words stay a normal message');
 assert.equal(readWorkerQuestion(question({state:'pending',canCarry:true})).canCarry,false,'only a stale question carries');
 assert.equal(readWorkerQuestion(question({canCarry:'yes'})),null);
});
