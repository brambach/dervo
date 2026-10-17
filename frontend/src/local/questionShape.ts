import {t} from '@/i18n';
/** A question you answer with a number key (#16, direction A). The shape a Home reply carries, the rules as code, and
 * the key reducer the tray runs. Nothing here renders. The rules (evidence/design-tests/questions/README.md):
 * - number keys pick only while the composer is empty; your own words are the other answer; Esc brings the choices back
 * - two or three options, no "Other" row, no multi-select
 * - the answer goes to Home, who asked: there is no destination field, so nothing on a question can redirect it
 * - an option that grants the shell lists what it runs and what it won't, and takes two keys: its number, then Enter */

export type QuestionGrant={runs:string[];wont:string};
/** `quiet` is a thread's Skip: drawn lighter than the question's own choices. Home's replies never carry one (readOption). */
export type QuestionOption={label:string;detail:string|null;grant:QuestionGrant|null;quiet?:boolean};
/** `asker` is the name the UI says. A Home question's answer is sent to Home; a thread's goes to the task that asked
 * (WorkerQuestions.tsx), and either way the tray itself never picks where it goes. */
export type Question={asker:string;ask:string;options:QuestionOption[]};
export type PendingQuestion=Question&{id:string};
/** What the tray hands back: the words that become your own message to Home. */
export type QuestionAnswer={asker:string;text:string;option:number;grant:QuestionGrant|null};

export const OPTION_LIMIT=3;
export const OPTION_MIN=2;
/** Caps on the text the tray shows. Over any of them the question is refused, not clipped: the prose still asks. */
export const LIMITS={ask:200,label:80,detail:120,runs:8,run:120,wont:160} as const;
// A thread question may have three choices and Skip, so the tray reads 1 to 4. Home's replies still carry at most three.
const KEYS=['1','2','3','4'] as const;

const text=(value:unknown,limit:number):string|null=>typeof value==='string'&&value.trim()&&value.trim().length<=limit?value.trim():null;
const isRecord=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);

/** One option, rebuilt from its known fields only: a label, a detail, and a grant with its runs and its won't line.
 * Anything else on it (a destination, a thread, a command) is dropped, so an option can never route or act by itself.
 * Text over the caps, or a grant without its list, makes the whole option unreadable. */
function readOption(raw:unknown):QuestionOption|null {
 if(!isRecord(raw))return null;
 const label=text(raw.label,LIMITS.label);
 if(!label)return null;
 if(raw.detail!=null&&typeof raw.detail==='string'&&raw.detail.trim()&&!text(raw.detail,LIMITS.detail))return null;
 let grant:QuestionGrant|null=null;
 if(isRecord(raw.grant)){
  const runs=Array.isArray(raw.grant.runs)?raw.grant.runs.map(run=>text(run,LIMITS.run)):[];
  const wont=text(raw.grant.wont,LIMITS.wont);
  if(!runs.length||runs.length>LIMITS.runs||runs.some(run=>run===null)||!wont)return null;
  grant={runs:runs as string[],wont};
 }else if(raw.grant!=null)return null;
 return {label,detail:typeof raw.detail==='string'?text(raw.detail,LIMITS.detail):null,grant};
}

/** The question as the tray may show it, or null when the shape is not one it trusts: an asker, the question
 * sentence, and two or three options. Four options is not a question; it is refused, not clipped. A `to` or any other
 * field on it is ignored: the answer goes to Home by construction. */
export function readQuestion(raw:unknown):Question|null {
 if(!isRecord(raw))return null;
 const asker=text(raw.asker,LIMITS.label),ask=text(raw.ask,LIMITS.ask);
 if(!asker||!ask||!Array.isArray(raw.options))return null;
 if(raw.options.length<OPTION_MIN||raw.options.length>OPTION_LIMIT)return null;
 const options=raw.options.map(readOption);
 if(options.some(option=>option===null))return null;
 return {asker,ask,options:options as QuestionOption[]};
}

/** The question waiting on you: the newest entry is a ready reply that carries one. Anything said after it, or a reply
 * still thinking, means there is nothing to answer with a key. Home's live lines about its jobs don't count. */
export function pendingQuestion(entries:ReadonlyArray<{id:string;kind:string;state?:string|null;question?:unknown;news?:unknown}>):PendingQuestion|null {
 // A line about a job's thread (home_news) isn't something said after the question: it doesn't hide it.
 const said=entries.filter(entry=>!entry.news);
 const last=said[said.length-1];
 if(!last||last.kind!=='reply'||last.state!=='ready')return null;
 const question=readQuestion(last.question);
 return question?{...question,id:last.id}:null;
}

export function answerFor(question:Question,index:number):QuestionAnswer {
 const option=question.options[index];
 return {asker:question.asker,text:option.label,option:index,grant:option.grant};
}

/** The tray's own state: hidden by Esc, and which grant option is armed and waiting for Enter. */
export type TrayState={hidden:boolean;armed:number|null};
export const TRAY_START:TrayState={hidden:false,armed:null};
/** One key as the tray sees it. `modifier` covers shift too: Shift+Enter is the composer's newline, never a confirm. */
export type TrayKey={key:string;composerText:string;composing?:boolean;modifier?:boolean;repeat?:boolean};
export type TrayOutcome={state:TrayState;answer:QuestionAnswer|null;clearComposer:boolean;handled:boolean};

const idle=(state:TrayState):TrayOutcome=>({state,answer:null,clearComposer:false,handled:false});

/** Choose option `index` the way its key would: a plain option answers at once, a grant option arms and waits for Enter. */
export function pickOption(question:Question,state:TrayState,index:number):TrayOutcome {
 const option=question.options[index];
 if(!option||state.hidden)return idle(state);
 if(option.grant)return {state:{hidden:false,armed:index},answer:null,clearComposer:false,handled:true};
 return {state:TRAY_START,answer:answerFor(question,index),clearComposer:false,handled:true};
}

/** Enter confirms the armed grant. Nothing else answers on Enter: one keystroke never grants the shell. */
export function confirmArmed(question:Question,state:TrayState):TrayOutcome {
 if(state.armed===null||!question.options[state.armed]?.grant)return idle(state);
 return {state:TRAY_START,answer:answerFor(question,state.armed),clearComposer:false,handled:true};
}

/** One key, given what the composer holds. Keys pick only while the composer is empty. With text in it the only key
 * the tray takes is Esc, which clears the text and brings the choices back, and any arm is dropped: a typing detour
 * never leaves a grant one Enter away. A held key (auto-repeat), an IME key or a modified key is never a pick. */
export function trayKey(question:Question,state:TrayState,event:TrayKey):TrayOutcome {
 if(event.composing||event.modifier||event.repeat)return idle(state);
 if(event.composerText!==''){
  if(event.key==='Escape')return {state:TRAY_START,answer:null,clearComposer:true,handled:true};
  return idle({hidden:state.hidden,armed:null});
 }
 if(state.hidden){
  if(event.key==='Escape')return {state:TRAY_START,answer:null,clearComposer:false,handled:true};
  return idle(state);
 }
 const number=KEYS.indexOf(event.key as typeof KEYS[number]);
 if(number>=0&&number<question.options.length)return pickOption(question,state,number);
 if(event.key==='Enter')return confirmArmed(question,state);
 if(event.key==='Escape'){
  if(state.armed!==null)return {state:TRAY_START,answer:null,clearComposer:false,handled:true};
  return {state:{hidden:true,armed:null},answer:null,clearComposer:false,handled:true};
 }
 return idle(state);
}

/** What the composer says while a question waits: its placeholder and the line under it. Home reads this so the two
 * surfaces agree; the tray itself never touches the composer. */
export function questionComposerCopy(question:Question,composerText:string):{placeholder:string;hint:string} {
 if(composerText!=='')return {placeholder:'',hint:t('Enter sends it to {asker} as your answer',{asker:question.asker})};
 return {placeholder:t('Or answer in words'),hint:t('Esc hides or shows the choices')};
}
