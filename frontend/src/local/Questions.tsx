import {useEffect,useRef,useState} from 'react';
import {overlayOpen} from './escapeToStop';
import {TRAY_START,answerFor,confirmArmed,pickOption,trayKey,type PendingQuestion,type QuestionAnswer,type TrayState} from './questionShape';
import './Questions.css';
import {t} from '@/i18n';

/** The choices tray (#16, direction A). The question stays in the asker's own words in the chat; this is only the list
 * of answers, attached to the composer's top edge. It never writes to the composer: it asks its owner to clear it (Esc
 * while typing) and hands back the answer. Home sends it as your own message to Home. A thread (beta.16,
 * WorkerQuestions.tsx) sends it to the task that asked, through task_question_answer. No smile, no rust: the sidebar
 * carries those.
 *
 * Which keys it reads. Only keys whose target is the page itself (body), the composer it sits on (the editable inside
 * its next sibling, which is empty whenever a key can pick), or the tray's own rows. A key in any other field, on any
 * other button, or while a dialog, menu or popover is open (overlayOpen) is not the tray's. Enter on one of the tray's
 * own rows is that button's click, so Enter always acts on the row you're on, never on a row armed earlier. A held
 * key, a modified key (shift included) or one another handler already took is ignored.
 *
 * Wiring into Home.tsx (against the Home of 20 Sep, whose visible chat is `talk` and whose send() returns early while
 * a reply is thinking, a job is being sent or you're signed out):
 *   import {QuestionTray} from './Questions';
 *   import {pendingQuestion,questionComposerCopy} from './questionShape';
 *   const question=pendingQuestion(talk);
 *   const questionCopy=question?questionComposerCopy(question,text):null;
 *   // directly above <div className="dv-composer dv-home-composer">, so the two share one outline:
 *   {question&&<QuestionTray question={question} composerText={text} disabled={sending.size>0||signedOut}
 *     onClearComposer={()=>setText('')} onAnswer={answer=>void send(answer.text)}/>}
 *   // and in the composer, so the placeholder and the hint agree with the tray:
 *   placeholder={signedOut?'Sign in to ask Dervo':questionCopy?.placeholder||'Ask, or tell it what to do next'}
 *   {replyThinking?'Dervo is answering…':imageBusy?'Preparing your image…':questionCopy?.hint??composerHint(autostart)}
 * `disabled` matters: while send() would refuse, the tray takes no key, so a pick is never consumed and dropped. */
export type QuestionTrayProps={
 question:PendingQuestion;
 /** What the composer holds right now. Non-empty turns the keys off and folds the tray. */
 composerText:string;
 /** Your pick. It goes back to whoever asked: Home sends it as your message; a thread sends it to the asking task.
  * The tray never chooses a destination, and nothing on a question can redirect it. */
 onAnswer:(answer:QuestionAnswer)=>void;
 /** Esc while typing: the tray asks for the composer to be emptied so the choices come back. */
 onClearComposer:()=>void;
 /** True while Home couldn't send anyway (a job going out, signed out): the tray shows, but takes no key or click. */
 disabled?:boolean;
 /** A thread with two tasks asking at once shows one at a time: "1 of 2". Home never passes it. */
 position?:{at:number;of:number};
 /** Beta.16.1 (Bryce, 26 Sep: "i cant see the question only the answers"): a thread's question comes from a tool call,
  * not the agent's reply, so the tray shows it, quoted as the asker's words, above the choices. Home's question is
  * already in Home's reply, so Home leaves this off. */
 showAsk?:boolean;
 /** The header's words in place of "Answer goes to …", such as where an answer goes once the run that asked ended. */
 lead?:string;
};

const Key=({children}:{children:string})=><kbd className="dv-qtray-key">{children}</kbd>;

export function QuestionTray({question,composerText,onAnswer,onClearComposer,disabled=false,position,showAsk=false,lead}:QuestionTrayProps) {
 const root=useRef<HTMLDivElement>(null);
 const [tray,setTray]=useState<{id:string;state:TrayState}>({id:question.id,state:TRAY_START});
 const state=tray.id===question.id?tray.state:TRAY_START;
 const setState=(next:TrayState)=>setTray({id:question.id,state:next});
 const latest=useRef({question,composerText,state,onAnswer,onClearComposer,disabled});
 latest.current={question,composerText,state,onAnswer,onClearComposer,disabled};

 // Words in the composer drop any arm: coming back to an empty composer never finds a grant one Enter away.
 useEffect(()=>{if(composerText!==''&&state.armed!==null)setState({hidden:state.hidden,armed:null});},[composerText]);  // eslint-disable-line react-hooks/exhaustive-deps

 useEffect(()=>{
  const listen=(event:KeyboardEvent)=>{
   const now=latest.current;
   if(now.disabled||event.defaultPrevented||event.repeat||event.isComposing)return;
   const target=event.target;
   const tray=root.current;
   const composer=tray?.nextElementSibling??null;
   const onPage=target===document.body||target===document.documentElement;
   const inComposer=!!composer&&target instanceof Node&&composer.contains(target);
   const inTray=!!tray&&target instanceof Node&&tray.contains(target);
   if(!onPage&&!inComposer&&!inTray)return;
   if(inTray&&event.key==='Enter')return;  // the row's own click: Enter acts on the row you're on
   if(overlayOpen())return;
   const outcome=trayKey(now.question,now.state,{key:event.key,composerText:now.composerText,composing:event.isComposing,repeat:event.repeat,
    modifier:event.metaKey||event.ctrlKey||event.altKey||event.shiftKey});
   setTray({id:now.question.id,state:outcome.state});
   if(!outcome.handled)return;
   event.preventDefault();
   if(outcome.clearComposer)now.onClearComposer();
   if(outcome.answer)now.onAnswer(outcome.answer);
  };
  window.addEventListener('keydown',listen);
  return ()=>window.removeEventListener('keydown',listen);
 },[]);

 const typing=composerText!=='';
 const grant=question.options.some(option=>option.grant);
 const armed=state.armed!==null&&!!question.options[state.armed]?.grant?state.armed:null;
 const count=question.options.length;

 function click(index:number){
  if(disabled)return;
  const outcome=state.armed===index?confirmArmed(question,state):pickOption(question,state,index);
  if(!outcome.handled)return;
  setState(outcome.state);
  if(outcome.answer)onAnswer(outcome.answer);
 }

 const label=t('{asker} is asking',{asker:question.asker});
 const asked=showAsk&&<p className="dv-qtray-ask" data-asker={question.asker}>{question.ask}</p>;
 if(typing)return <div ref={root} className="dv-qtray" data-folded="typing" role="group" aria-label={label}>
  <div className="dv-qtray-head"><span>{t('Your words answer {asker}',{asker:question.asker})}</span>
   <span className="dv-qtray-hint"><Key>Esc</Key> {t('brings the choices back')}</span></div>
  {asked}
 </div>;
 if(state.hidden)return <div ref={root} className="dv-qtray" data-folded="hidden" role="group" aria-label={label}>
  <div className="dv-qtray-head"><span>{label}</span>
   <span className="dv-qtray-hint"><Key>Esc</Key> {t('shows the choices')}</span></div>
  {asked}
 </div>;
 return <div ref={root} className="dv-qtray" role="group" aria-label={label} data-grant={grant||undefined} aria-disabled={disabled||undefined}>
  <div className="dv-qtray-head"><span>{lead??t('Answer goes to {asker}',{asker:question.asker})}{position&&position.of>1&&<> · <span className="dv-qtray-of">{t('{at} of {all}',{at:position.at,all:position.of})}</span></>}</span>
   <span className="dv-qtray-hint">{armed!==null
    ?<><Key>↵</Key> {t('to allow')} · <Key>Esc</Key> {t('to cancel')}</>
    :grant?<><Key>1</Key> {t('then')} <Key>↵</Key> {t('to allow')}</>
    :<><Key>1</Key>{count>1&&<> – <Key>{String(count)}</Key></>} {t('to pick')}</>}</span></div>
  {asked}
  <ul className="dv-qtray-list">
   {question.options.map((option,index)=><li key={index}>
    <button type="button" className="dv-qtray-opt" data-armed={armed===index||undefined} data-quiet={option.quiet||undefined} aria-pressed={option.grant?armed===index:undefined}
     aria-label={option.grant?t('{label}, then Enter to allow',{label:option.label}):option.label} disabled={disabled} onClick={()=>click(index)}>
     <Key>{String(index+1)}</Key>
     <span className="dv-qtray-text">
      <span className="dv-qtray-label">{option.label}</span>
      {option.detail&&<span className="dv-qtray-detail">{option.detail}</span>}
      {option.grant&&<span className="dv-qtray-grant">
       {option.grant.runs.map((run,n)=><code key={n}>{run}</code>)}
       <span className="dv-qtray-wont">{option.grant.wont}</span>
      </span>}
     </span>
    </button>
   </li>)}
  </ul>
 </div>;
}

export {answerFor};
