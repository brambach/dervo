// The choices tray (#16, direction A) and its rules as code. Each rule has a test and a negative control:
// keys pick only while the composer is empty; two or three options; the answer goes to Home who asked; a shell grant
// takes two keys, never one, whatever is focused, held, typed or open; the tray is neutral (no smile, no rust).
import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','HTMLTextAreaElement','Element','MutationObserver','Node','Event','KeyboardEvent','EventTarget'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,fireEvent,screen,cleanup,act}=await import('@testing-library/react');
const {readQuestion,pendingQuestion,trayKey,pickOption,confirmArmed,questionComposerCopy,TRAY_START,OPTION_LIMIT,OPTION_MIN,LIMITS}=await import('./questionShape.ts');
const {QuestionTray}=await import('./Questions.tsx');
afterEach(()=>{cleanup();document.body.innerHTML='';});after(()=>dom.window.close());

// The questions the design was made against. q1 and q3 are Home's own; q2 is Home's grant question in a thread.
const q1={asker:'Home',ask:'Reopen it, or log this as new?',options:[
 {label:'Reopen #9',detail:'Adds what you said to it',grant:null},{label:'Log as a new bug',detail:'Becomes #37, linked to #9',grant:null}]};
const GO={label:'Go',detail:'Full access for this thread only',grant:{runs:['pnpm docs:build','write docs/connectors.md'],wont:'It won’t commit or push. Those still ask.'}};
const q2={asker:'Home',ask:'Say go to let it use the shell.',options:[GO,{label:'Keep it read-only',detail:'It proposes the page as a diff you apply',grant:null}]};
const q3={asker:'Home',ask:'Which did you mean?',options:[
 {label:'Per-project connectors',detail:'Needs you',grant:null},{label:'Connectors docs',detail:'Running, started 8:33 PM',grant:null},{label:'Connector picker',detail:'Idle since Tuesday',grant:null}]};
const ARMED={hidden:false,armed:0};
const pending=(question,id='r1')=>({...question,id});
const key=(question,state,k,composerText='',extra={})=>trayKey(question,state,{key:k,composerText,...extra});
function press(k,init={}){
 const {target=document.body,...rest}=init;
 const event=new dom.window.KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true,...rest});
 act(()=>{target.dispatchEvent(event);});
 return event;
}
/** The tray above a composer, the way Home places it: the composer is the tray's next sibling. */
function mount(question,props={}){
 const answers=[],clears=[];
 const make=(over={})=>React.createElement(React.Fragment,null,
  React.createElement(QuestionTray,{question:pending(question),composerText:'',onAnswer:a=>answers.push(a),onClearComposer:()=>clears.push(1),...props,...over}),
  React.createElement('div',{className:'dv-composer'},React.createElement('textarea',{id:'composer','aria-label':'composer',defaultValue:''})));
 const view=render(make());
 const composer=document.getElementById('composer');
 return {answers,clears,view,composer,rerender:over=>view.rerender(make(over))};
}
const goButton=()=>screen.getByRole('button',{name:'Go, then Enter to allow'});
const readOnlyButton=()=>screen.getByRole('button',{name:'Keep it read-only'});

test('the shape: two or three options, capped text, and nothing on an option can route or act',()=>{
 assert.deepEqual(readQuestion(q1),q1);
 assert.deepEqual(readQuestion({...q1,to:'thread:t9'}),q1,'a destination on the question is ignored: the answer goes to Home');
 assert.equal(OPTION_LIMIT,3);assert.equal(OPTION_MIN,2);
 assert.equal(readQuestion({...q3,options:[...q3.options,{label:'A fourth',detail:null,grant:null}]}),null,'four options is refused, not clipped');
 assert.equal(readQuestion({...q1,options:[q1.options[0]]}),null,'one option is not a choice');
 assert.equal(readQuestion({...q1,options:[]}),null);
 assert.equal(readQuestion({...q1,asker:''}),null);
 const planted=readQuestion({...q1,options:[{label:'Reopen #9',to:'thread:t9',thread:'t9',command:'rm -rf',grant:null},q1.options[1]]});
 assert.deepEqual(planted.options[0],{label:'Reopen #9',detail:null,grant:null},'a destination or a command on an option is dropped');
 assert.equal(readQuestion({...q2,options:[{label:'Go',grant:{runs:[],wont:'x'}},q2.options[1]]}),null,'a grant without its list is not shown');
 assert.equal(readQuestion({...q2,options:[{label:'Go',grant:{runs:['make'],wont:''}},q2.options[1]]}),null,'a grant without its won’t line is not shown');
 // Caps: over any of them the question is refused, so a paragraph can't ride into the tray.
 assert.equal(readQuestion({...q1,options:[{...q1.options[0],label:'x'.repeat(LIMITS.label+1)},q1.options[1]]}),null);
 assert.equal(readQuestion({...q1,options:[{...q1.options[0],detail:'x'.repeat(LIMITS.detail+1)},q1.options[1]]}),null);
 assert.equal(readQuestion({...q1,ask:'x'.repeat(LIMITS.ask+1)}),null);
 assert.equal(readQuestion({...q2,options:[{...GO,grant:{...GO.grant,runs:Array(LIMITS.runs+1).fill('make')}},q2.options[1]]}),null,'too many run lines');
 assert.equal(readQuestion({...q2,options:[{...GO,grant:{...GO.grant,runs:['x'.repeat(LIMITS.run+1)]}},q2.options[1]]}),null,'a run line too long');
 assert.equal(readQuestion({...q2,options:[{...GO,grant:{...GO.grant,wont:'x'.repeat(LIMITS.wont+1)}},q2.options[1]]}),null);
});

test('pending: only the newest ready reply asks, and anything said after it closes the question',()=>{
 const reply={id:'r1',kind:'reply',state:'ready',question:q1};
 assert.deepEqual(pendingQuestion([{id:'y1',kind:'you'},reply]),{...q1,id:'r1'});
 assert.equal(pendingQuestion([reply,{id:'y2',kind:'you',text:'Neither'}]),null,'your own message after it answers it');
 assert.equal(pendingQuestion([reply,{id:'r2',kind:'reply',state:'thinking'}]),null);
 assert.equal(pendingQuestion([{id:'r1',kind:'reply',state:'ready'}]),null,'a reply without a question');
 assert.equal(pendingQuestion([{id:'r1',kind:'reply',state:'failed',question:q1}]),null);
 assert.equal(pendingQuestion([]),null);
});

test('a number key picks while the composer is empty, and the answer is the option in your own words',()=>{
 const out=key(q1,TRAY_START,'2');
 assert.equal(out.handled,true);
 assert.deepEqual(out.answer,{asker:'Home',text:'Log as a new bug',option:1,grant:null});
 assert.equal('to' in out.answer,false,'no destination field: the answer goes to Home by construction');
 assert.deepEqual(out.state,TRAY_START);
 assert.equal(key(q1,TRAY_START,'3').handled,false,'q1 has two options: 3 does nothing');
 assert.equal(key(q3,TRAY_START,'3').answer.text,'Connector picker');
 assert.equal(key(q1,TRAY_START,'0').handled,false);
 assert.equal(key(q1,TRAY_START,'a').handled,false,'letters go to the composer');
});

test('control: with text in the composer, number keys and Enter do nothing, and Esc clears it to bring the choices back',()=>{
 for(const k of ['1','2','Enter']){
  const out=key(q1,TRAY_START,k,'N');
  assert.equal(out.handled,false,`${k} with text is the composer’s`);
  assert.equal(out.answer,null);
 }
 assert.equal(key(q2,ARMED,'Enter','some words').answer,null,'an armed grant never fires while you type');
 const esc=key(q1,TRAY_START,'Escape','Neither, #9 was the badge');
 assert.deepEqual(esc,{state:TRAY_START,answer:null,clearComposer:true,handled:true});
 assert.equal(key(q1,TRAY_START,'1','',{composing:true}).handled,false,'an IME key is not a pick');
 assert.equal(key(q1,TRAY_START,'1','',{modifier:true}).handled,false,'cmd+1 is not a pick');
});

test('Esc hides the choices and shows them again; while hidden a digit is just a digit',()=>{
 const hidden=key(q1,TRAY_START,'Escape');
 assert.deepEqual(hidden.state,{hidden:true,armed:null});
 assert.equal(key(q1,hidden.state,'1').handled,false);
 assert.equal(key(q1,hidden.state,'1').answer,null);
 assert.deepEqual(key(q1,hidden.state,'Escape').state,TRAY_START);
});

test('a shell grant takes two keys: the number arms it, Enter allows it, and one keystroke never grants',()=>{
 const armed=key(q2,TRAY_START,'1');
 assert.equal(armed.answer,null,'the number alone grants nothing');
 assert.deepEqual(armed.state,ARMED);
 assert.equal(key(q2,TRAY_START,'Enter').answer,null,'Enter alone grants nothing');
 assert.equal(key(q2,TRAY_START,'Enter').handled,false);
 assert.equal(key(q2,{hidden:false,armed:1},'Enter').answer,null,'Enter with a plain option armed is not a grant path');
 const allowed=key(q2,ARMED,'Enter');
 assert.deepEqual(allowed.answer,{asker:'Home',text:'Go',option:0,grant:GO.grant});
 assert.deepEqual(allowed.state,TRAY_START);
 assert.deepEqual(key(q2,ARMED,'Escape').state,TRAY_START,'Esc disarms first');
 assert.equal(key(q2,ARMED,'2').answer.text,'Keep it read-only','the plain option still answers at once');
 assert.equal(key(q2,ARMED,'1').answer,null,'pressing 1 twice is not Enter');
 assert.equal(confirmArmed(q2,TRAY_START).handled,false);
 assert.equal(pickOption(q2,TRAY_START,0).answer,null);
 assert.equal(pickOption(q1,TRAY_START,0).answer.text,'Reopen #9');
});

test('1. Enter acts on the row you are on, never on a row armed earlier',()=>{
 const {answers}=mount(q2);
 press('1');
 assert.equal(goButton().getAttribute('aria-pressed'),'true');
 readOnlyButton().focus();
 const enter=press('Enter',{target:readOnlyButton()});
 assert.equal(answers.length,0,'Enter on the read-only row is that row’s click, not the armed grant');
 assert.equal(enter.defaultPrevented,false,'the button keeps its own Enter');
 fireEvent.click(readOnlyButton());  // what the browser does with Enter on a focused button
 assert.deepEqual(answers.map(a=>[a.text,a.grant]),[['Keep it read-only',null]]);
 // The armed row itself: Enter is its click, which confirms.
 cleanup();
 const again=mount(q2);
 press('1');
 press('Enter',{target:goButton()});
 assert.equal(again.answers.length,0);
 fireEvent.click(goButton());
 assert.deepEqual(again.answers.map(a=>a.text),['Go']);
});

test('2. an arm does not survive a typing detour: arm, type, delete, Enter grants nothing',()=>{
 assert.equal(key(q2,ARMED,'h','h').state.armed,null,'the reducer drops the arm on any key while text is in');
 const {answers,rerender}=mount(q2);
 press('1');
 assert.equal(goButton().getAttribute('aria-pressed'),'true');
 rerender({composerText:'hmm'});
 rerender({composerText:''});
 assert.equal(goButton().getAttribute('aria-pressed'),'false','the arm is gone once you typed');
 press('Enter');
 assert.equal(answers.length,0);
 press('1');press('Enter');
 assert.deepEqual(answers.map(a=>a.text),['Go'],'arming again from scratch still works');
});

test('3. only the page and the tray’s own composer feed it: a key in any other field is that field’s',()=>{
 const {answers,composer}=mount(q2);
 const other=document.createElement('input');document.body.appendChild(other);other.focus();
 const one=press('1',{target:other});
 press('Enter',{target:other});
 assert.equal(answers.length,0,'an empty unrelated input never arms or grants');
 assert.equal(one.defaultPrevented,false,'the digit reaches the field');
 const button=document.createElement('button');document.body.appendChild(button);button.focus();
 press('1',{target:button});press('Enter',{target:button});
 assert.equal(answers.length,0,'nor does a button elsewhere');
 // The composer the tray sits on does count, and so does the page itself.
 composer.focus();
 const fromComposer=press('2',{target:composer});
 assert.equal(fromComposer.defaultPrevented,true,'the digit is taken, so it never lands in the composer');
 assert.deepEqual(answers.map(a=>a.text),['Keep it read-only']);
 press('2');
 assert.equal(answers.length,2);
});

test('4. a held key is one key: auto-repeat never picks or grants',()=>{
 assert.equal(key(q1,TRAY_START,'1','',{repeat:true}).handled,false);
 const {answers}=mount(q2);
 press('1');
 for(let n=0;n<8;n++)press('Enter',{repeat:true});
 assert.equal(answers.length,0,'eight repeated Enters grant nothing');
 const plain=mount(q1);
 for(let n=0;n<8;n++)press('1',{repeat:true});
 assert.equal(plain.answers.length,0);
});

test('5. Escape belongs to whatever is open: a dialog, a menu, or a handler that already took it',()=>{
 const {answers,view}=mount(q2);
 const dialog=document.createElement('div');dialog.setAttribute('role','dialog');dialog.setAttribute('data-state','open');
 const close=document.createElement('button');dialog.appendChild(close);document.body.appendChild(dialog);close.focus();
 const escape=press('Escape',{target:close});
 assert.equal(escape.defaultPrevented,false,'the dialog keeps its Escape');
 assert.equal(view.container.querySelector('[data-folded]'),null,'the tray stays open');
 // Even from the page itself, an open overlay keeps every key.
 press('1');
 assert.equal(goButton().getAttribute('aria-pressed'),'false');
 assert.equal(answers.length,0);
 dialog.remove();
 // A handler that already took the key (defaultPrevented) is respected.
 const taker=event=>event.preventDefault();document.addEventListener('keydown',taker);
 press('Escape');
 assert.equal(view.container.querySelector('[data-folded]'),null);
 document.removeEventListener('keydown',taker);
 press('Escape');
 assert.equal(view.container.querySelector('[data-folded="hidden"]')!==null,true,'with nothing open, Esc hides the choices');
});

test('6. Shift+Enter is the composer’s newline, never a confirm',()=>{
 assert.equal(key(q2,ARMED,'Enter','',{modifier:true}).answer,null);
 const {answers,composer}=mount(q2);
 press('1');
 press('Enter',{shiftKey:true});
 press('Enter',{shiftKey:true,target:composer});
 assert.equal(answers.length,0);
 press('Enter');
 assert.deepEqual(answers.map(a=>a.text),['Go'],'a plain Enter still confirms');
});

test('disabled: while Home could not send, the tray takes no key and no click, so a pick is never consumed and lost',()=>{
 const {answers,rerender}=mount(q2,{disabled:true});
 const one=press('1');
 assert.equal(one.defaultPrevented,false);
 press('Enter');
 fireEvent.click(readOnlyButton());
 assert.equal(answers.length,0);
 rerender({disabled:false});
 press('2');
 assert.deepEqual(answers.map(a=>a.text),['Keep it read-only']);
});

test('the answer goes to Home who asked, the UI says so, and the tray never carries a destination',()=>{
 const {answers}=mount(q1);
 assert.equal(screen.getByRole('group',{name:'Home is asking'}).textContent.includes('Answer goes to Home'),true);
 press('1');
 assert.deepEqual(answers,[{asker:'Home',text:'Reopen #9',option:0,grant:null}]);
});

test('in the tray: a key on the page answers, a key while typing does not, and Esc asks Home to clear the composer',()=>{
 const {answers,clears,rerender}=mount(q1);
 const e=press('1');
 assert.equal(e.defaultPrevented,true);
 assert.deepEqual(answers.map(a=>a.text),['Reopen #9']);
 rerender({composerText:'N'});
 assert.equal(screen.getByRole('group').textContent.includes('Your words answer Home'),true);
 assert.equal(screen.queryByRole('button'),null,'the list folds while you type');
 const typed=press('2');
 assert.equal(typed.defaultPrevented,false);
 assert.equal(answers.length,1,'nothing picked while typing');
 press('Escape');
 assert.equal(clears.length,1);
});

test('in the tray: the grant lists what it runs and what it won’t, and needs the number then Enter',()=>{
 const {answers}=mount(q2);
 const go=goButton();
 assert.equal(go.textContent.includes('pnpm docs:build'),true);
 assert.equal(go.textContent.includes('write docs/connectors.md'),true);
 assert.equal(go.textContent.includes('won’t commit or push'),true);
 press('Enter');
 assert.equal(answers.length,0,'Enter alone does nothing');
 press('1');
 assert.equal(answers.length,0,'the number alone grants nothing');
 assert.equal(go.getAttribute('aria-pressed'),'true');
 press('Enter');
 assert.equal(answers.length,1);
 assert.deepEqual(answers[0].grant.runs,GO.grant.runs);
});

test('a click is a key: one click answers a plain option, a grant needs a second click or Enter',()=>{
 const {answers}=mount(q2);
 fireEvent.click(goButton());
 assert.equal(answers.length,0,'one click never grants');
 fireEvent.click(goButton());
 assert.equal(answers.length,1);
 cleanup();
 const plain=mount(q1);
 fireEvent.click(screen.getByRole('button',{name:'Log as a new bug'}));
 assert.deepEqual(plain.answers.map(a=>a.text),['Log as a new bug']);
});

test('a new question starts the tray fresh: an arm on the old one never confirms on the new one',()=>{
 const {answers,view}=mount(q2);
 press('1');
 view.rerender(React.createElement(QuestionTray,{question:pending(q2,'r2'),composerText:'',onAnswer:a=>answers.push(a),onClearComposer:()=>{}}));
 press('Enter');
 assert.equal(answers.length,0);
});

test('the tray is neutral: no smile, no rust, and the composer copy names the asker',()=>{
 const {view}=mount(q2);
 assert.equal(view.container.querySelector('svg'),null,'no mark in the tray');
 assert.equal(view.container.innerHTML.includes('rust'),false);
 assert.equal(view.container.innerHTML.includes('dv-work'),false);
 assert.deepEqual(questionComposerCopy(q1,''),{placeholder:'Or answer in words',hint:'Esc hides or shows the choices'});
 assert.deepEqual(questionComposerCopy(q1,'N'),{placeholder:'',hint:'Enter sends it to Home as your answer'});
});
