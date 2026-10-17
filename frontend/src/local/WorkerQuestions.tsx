import {forwardRef,useEffect,useImperativeHandle,useRef,useState,type RefObject} from 'react';
import {local} from './api';
import {QuestionTray} from './Questions';
import {answerProblem,answerWords,inTray,loadQuestionDraft,questionLine,readWorkerQuestion,saveQuestionDraft,takesAnswer,trayAnswer,trayQuestion,trayQuestions,type QuestionSubmission,type WorkerQuestion,type WorkerQuestionAnswer,type WorkerQuestionDraft,type WorkerQuestionScope} from './workerQuestionState';
import './Questions.css';
import './WorkerQuestions.css';
import {t} from '@/i18n';
import {COMPOSER_TEXT_EVENT} from './composerText';

/** Beta.16 (Bryce, 25 Sep): a thread's questions look like Home's: Home's tray (Questions.tsx) on the thread composer's
 * top edge, one question at a time, oldest first. Beta.16.1 (Bryce, 26 Sep: "i cant see the question only the
 * answers"): a thread's question comes from the dervo_ask_user tool, not the agent's reply, so the tray shows the
 * question itself, and so does the question's line once it's answered. A question whose run ended before you answered
 * keeps its question and choices, and your answer goes in as the thread's next message (canCarry). Keys pick, your
 * own words in the thread composer answer it, and Esc brings the choices back. Skip is a quiet last choice when the
 * question allows it. What the thread did that Home doesn't stays: the answer is saved on this device before it's
 * sent, Retry sends the same saved answer if it wasn't confirmed, and a plain line says when the run ended first. Those
 * sit in the tray's header line. Answered and skipped questions leave for one quiet line under their task
 * (QuestionHistory). Source IDs are in the task's record, not here. */
export type WorkerQuestionsProps={projectId:string;channelId:string;questions:readonly unknown[];onChanged:()=>Promise<unknown>;disabled?:boolean;
 /** What the thread composer holds. Words fold the tray, as on Home. */
 composerText:string;
 /** Esc while typing: empty the composer so the choices come back. */
 onClearComposer:()=>void};
/** ConnectedApp sends the composer's words here first: true means they answered the question, so nothing else sends them. */
export type WorkerQuestionsHandle={answerInWords:(text:string)=>Promise<boolean>};
const reason=(error:unknown)=>error instanceof Error?error.message:String(error);

export const WorkerQuestions=forwardRef<WorkerQuestionsHandle,WorkerQuestionsProps>(function WorkerQuestions({projectId,channelId,questions,onChanged,disabled=false,composerText,onClearComposer},ref){
 const {questions:tray,broken}=trayQuestions(questions,projectId,channelId);
 const active=useRef<WorkerQuestionsHandle|null>(null);
 useImperativeHandle(ref,()=>({answerInWords:text=>active.current?active.current.answerInWords(text):Promise.resolve(false)}),[]);
 const first=tray[0];
 if(!first&&!broken)return null;
 return <>
  {broken&&<p role="alert" className="dv-worker-question-error">{t('A question couldn’t be shown. Reload this thread before answering it.')}</p>}
  {first&&<ThreadQuestion key={JSON.stringify([projectId,channelId,first.taskId,first.id])} handle={active} question={first} position={{at:1,of:tray.length}}
   projectId={projectId} channelId={channelId} onChanged={onChanged} disabled={disabled} composerText={composerText} onClearComposer={onClearComposer}/>}
 </>;
});

/** A task's settled questions in the thread: one quiet line each, oldest first (beta.15, Bryce, 24 Sep). */
export function QuestionHistory({projectId,channelId,questions}:{projectId:string;channelId:string;questions?:readonly unknown[]}){
 const settled=(questions??[]).map(readWorkerQuestion).filter((question):question is WorkerQuestion=>!!question&&!inTray(question,projectId,channelId))
  .sort((a,b)=>a.createdAt-b.createdAt);
 if(!settled.length)return null;
 return <ul className="dv-question-history" aria-label={t('Questions answered in this task')}>
  {settled.map(question=><li key={question.id} className="dv-caption" data-question-id={question.id}>{questionLine(question)}</li>)}
 </ul>;
}

type ThreadQuestionProps={projectId:string;channelId:string;question:WorkerQuestion;position:{at:number;of:number};onChanged:()=>Promise<unknown>;disabled:boolean;
 composerText:string;onClearComposer:()=>void;handle:RefObject<WorkerQuestionsHandle|null>};

function ThreadQuestion({projectId,channelId,question,position,onChanged,disabled,composerText,onClearComposer,handle}:ThreadQuestionProps){
 const scope:WorkerQuestionScope={projectId,channelId,taskId:question.taskId,questionId:question.id};
 const [initial]=useState(()=>{try{return {draft:loadQuestionDraft(scope),error:''};}catch(error){return {draft:{text:'',pending:null},error:reason(error)};}});
 const [draft,setDraft]=useState<WorkerQuestionDraft>(initial.draft),[error,setError]=useState(initial.error),[busy,setBusy]=useState(false),[receipt,setReceipt]=useState<WorkerQuestion|null>(null);
 const working=useRef(false),alive=useRef(true),draftRef=useRef(draft);draftRef.current=draft;
 const current=receipt?(question.answerClientId===receipt.answerClientId&&question.state!=='pending'?question:receipt):question;
 const open=takesAnswer(current);
 const localSettlement=!open||disabled;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 function retain(next:WorkerQuestionDraft){draftRef.current=next;setDraft(next);saveQuestionDraft(scope,next);}
 useEffect(()=>{
  if(current.state==='pending'||!current.answerClientId||current.answerClientId!==draftRef.current.pending?.clientId)return;
  // A host receipt for this exact submission settles a reply lost before a remount.
  try{retain({text:'',pending:null});setError('');}catch{setError(t('Your answer is saved, but its local draft couldn’t be cleared. It won’t be sent again.'));}
 },[current.state,current.answerClientId]);
 /** Saves the answer on this device, then sends it. Resolves once it's saved: a send that isn't confirmed stays saved,
  * with Retry. Throws only when nothing was saved, so words typed in the composer stay there. */
 async function send(answer:WorkerQuestionAnswer,retry?:QuestionSubmission):Promise<boolean>{
  const allowed=answer.kind==='skip'?current.canSkip:open&&!disabled;
  if(working.current||!allowed)return false;
  // A stale host record permits only local settlement, never replay of its old answer.
  if(draftRef.current.pending&&!retry&&!(answer.kind==='skip'&&localSettlement&&draftRef.current.pending.answer.kind!=='skip'))return false;
  if(answer.kind==='text'){const problem=answerProblem(answer.text);if(problem){setError(problem);throw Error(problem);}}
  const submission=retry??{clientId:crypto.randomUUID(),answer};
  working.current=true;setBusy(true);setError('');
  try{retain({text:'',pending:submission});}
  catch{working.current=false;setBusy(false);const message=t('The answer couldn’t be saved for retry. Nothing was sent.');setError(message);throw Error(message);}
  try{
   const result=readWorkerQuestion(await local<WorkerQuestion>('task_question_answer',{...scope,...submission}));
   if(!alive.current)return true;
   if(!result||result.id!==question.id||result.taskId!==question.taskId||result.runId!==question.runId)throw Error(t('The answer response didn’t match this question. Check its saved status before retrying.'));
   if(result.state==='pending')throw Error(result.detail||t('The answer wasn’t confirmed.'));
   setReceipt(result);
   if(result.answerClientId===submission.clientId){try{retain({text:'',pending:null});}catch{setError(t('Your answer is saved, but its local draft couldn’t be cleared. It won’t be sent again.'));}}
   try{await onChanged();}catch{if(alive.current)setError(t('Your answer is saved. Thread updates couldn’t refresh.'));}
  }catch(failure){if(alive.current)setError(reason(failure));}
  finally{working.current=false;if(alive.current)setBusy(false);}
  return true;
 }
 const pending=current.state==='pending';
 const answerable=open&&!disabled&&!draft.pending&&!busy;
 const latest=useRef({answerable,send});latest.current={answerable,send};
 useEffect(()=>{
  handle.current={answerInWords:async text=>latest.current.answerable?latest.current.send({kind:'text',text}):false};
  return()=>{handle.current=null;};
 },[handle]);

 const asker=current.asker,saved=draft.pending;
 const retryAllowed=saved?.answer.kind==='skip'?current.canSkip:open&&!disabled;
 const errorLine=error&&<p className="dv-qtray-error" role="alert">{error}</p>;
 // Saved on this device, not confirmed yet: the line says what you answered, and Retry sends that same answer. If the
 // question can't take it any more (the run ended, or you lost access), Skip settles it without replaying it.
 if(saved||busy){
  const words=saved?answerWords(current,saved.answer):'';
  const elsewhere=!!saved&&!pending&&current.state!=='stale'&&!!current.answerClientId&&current.answerClientId!==saved.clientId;
  const line=busy&&!error?t('Sending your answer to {asker}…',{asker})
   :elsewhere?t('This was answered another way. Yours, “{words}”, wasn’t sent.',{words})
   :saved?.answer.kind==='skip'?t('Skip is saved. {asker} hasn’t confirmed it yet.',{asker}):t('Your answer, “{words}”, is saved. {asker} hasn’t confirmed it yet.',{words,asker});
  const skipInstead=!!saved&&!elsewhere&&current.canSkip&&localSettlement&&saved.answer.kind!=='skip';
  return <div className="dv-qtray" data-folded="saved" role="group" aria-label={t('{asker} is asking',{asker})} data-question-id={question.id} data-task-id={question.taskId} data-worker-questions>
   <div className="dv-qtray-head"><span className="dv-qtray-status" role="status">{line}</span>
    {saved&&<span className="dv-qtray-act">
     {!elsewhere&&(open||saved.answer.kind==='skip'&&current.canSkip)&&<button type="button" disabled={busy||!retryAllowed} onClick={()=>void send(saved.answer,saved)}>{busy?t('Checking…'):t('Retry')}</button>}
     {skipInstead&&<button type="button" disabled={busy} onClick={()=>void send({kind:'skip'}).catch(()=>{/* the error line says it */})}>{t('Skip')}</button>}
     {elsewhere&&<button type="button" onClick={()=>{try{retain({text:'',pending:null});}catch{setError(t('This device couldn’t clear it. Nothing was sent.'));}}}>{t('Dismiss')}</button>}
    </span>}</div>
   {errorLine}
  </div>;
 }
 // Settled on the host but not delivered, or the run ended and the answer can't go anywhere (the account, model or
 // access changed): one plain line, with the question under it when it was never answered.
 if(!open){
  const stale=current.state==='stale';
  const line=stale?t('{asker}’s run ended before this was answered. {detail}',{asker,detail:current.detail??''}).trim():current.delivery==='unknown'?t('Your answer is saved. Delivery to the task isn’t confirmed.'):questionLine(current);
  return <div className="dv-qtray" data-folded="ended" role="group" aria-label={t('{asker} asked',{asker})} data-question-id={question.id} data-task-id={question.taskId} data-worker-questions>
   <div className="dv-qtray-head"><span className="dv-qtray-status" role="status">{line}</span>
    {stale&&current.canSkip&&<span className="dv-qtray-act"><button type="button" disabled={busy} onClick={()=>void send({kind:'skip'})}>{t('Clear it')}</button></span>}</div>
   {stale&&<p className="dv-qtray-ask" data-asker={asker}>{current.ask}</p>}
   {errorLine}
  </div>;
 }
 const shown=trayQuestion(current);
 // The run that asked has ended: the answer starts the thread's next message instead of joining that run.
 const lead=current.state==='stale'?t('{asker}’s run ended. Your answer starts a new message',{asker}):undefined;
 // A question with no choices and no Skip: the tray says who's asking, and your words answer it.
 if(!shown.options.length)return <div className="dv-qtray" data-folded="words" role="group" aria-label={t('{asker} is asking',{asker})} data-question-id={question.id} data-task-id={question.taskId}>
  <div className="dv-qtray-head"><span>{composerText?t('Your words answer {asker}',{asker}):lead?t('{lead}. Answer in words',{lead}):t('{asker} is asking. Answer in words',{asker})}{position.of>1&&<> · <span className="dv-qtray-of">{t('{at} of {all}',{at:position.at,all:position.of})}</span></>}</span></div>
  <p className="dv-qtray-ask" data-asker={asker}>{current.ask}</p>
  {errorLine}
 </div>;
 // The error goes above the tray: the tray finds the composer as its next sibling, and shares its outline.
 return <>
  {error&&<p className="dv-worker-question-error" role="alert">{error}</p>}
  <QuestionTray question={shown} composerText={composerText} disabled={!answerable} position={position} onClearComposer={onClearComposer} showAsk lead={lead}
   onAnswer={answer=>{const picked=trayAnswer(current,answer);if(picked)void send(picked).catch(()=>{/* the error line says it */});}}/>
 </>;
}

/** What the thread's message box (DvComposer) holds, as you type. The box says so with its own event, since a textarea's
 * words aren't in the DOM for a mutation to see, and the tray watches that rather than reaching into the box. */
export function useComposerText(root:RefObject<HTMLElement|null>,enabled:boolean):string{
 const [text,setText]=useState('');
 useEffect(()=>{
  const box=root.current;
  if(!enabled||!box){setText('');return;}
  setText(box.querySelector<HTMLTextAreaElement>('[data-testid="message-input"]')?.value??'');
  const read=(event:Event)=>setText(String((event as CustomEvent<string>).detail??''));
  box.addEventListener(COMPOSER_TEXT_EVENT,read);
  return()=>box.removeEventListener(COMPOSER_TEXT_EVENT,read);
 },[root,enabled]);
 return text;
}
/** Esc while typing: the thread's message box is emptied the way you'd empty it, so ⌘Z brings the words back and the
 * draft saves as usual. */
export function clearComposer(root:HTMLElement|null){
 const field=root?.querySelector<HTMLTextAreaElement>('[data-testid="message-input"]');
 if(!field)return;
 field.focus();
 field.select();
 document.execCommand('delete');
}
