/** Everything that travels inside a sealed envelope. The relay never sees these. Each side checks shape before acting,
 * because the other end could be an old version or a stolen phone. */

export type Bucket='needs'|'working'|'finished'|'idle'|'unknown';

/** One thread as the phone shows it on Home. Built on the Mac from the host's own records, never from a model's words
 * about itself. ``line`` is the one sentence for the row. */
export type HomeRow={id:string;projectId:string;project:string;name:string;bucket:Bucket;line:string;step:string|null;unread:number;
  /** Seconds since epoch of the last thing that happened, or null when the host has no time for it. */
  at:number|null;
  /** A question the thread is waiting on you to answer. */
  question:string|null;
  /** Whether Stop would do anything right now. */
  stoppable:boolean;
  /** Alive but silent for over a minute: shown still, not spinning. */
  quiet:boolean};
export type HomeSnapshot={
  /** When the Mac built this, in ms. The phone says "as of" from this, so stale state reads as stale. */
  at:number;mac:string;projects:{id:string;name:string}[];rows:HomeRow[];
  /** Idle threads left out to fit one sealed message. */
  hidden?:number;
  /** Whether the Mac keeps itself awake while agents run (Settings, Preferences), and whether it's doing so now. Closing
   * the lid on battery still sleeps it. Left out when the Mac can't say. */
  awake?:{enabled:boolean;holding:boolean}};

export type ThreadMessage={id:string;from:'you'|'agent'|'dervo';text:string;at:number|null;
  /** Still being written (Home's reply while it's thinking). */
  pending?:boolean};
/** The channel id the phone uses for a project's Home chat with Dervo's coordinator. Never a real thread id. */
export const HOME_CHANNEL='home';
/** Something in a thread waiting on you that the phone can settle. Built on the Mac from the host's own records.
 * ``question``: an agent asked, with up to 3 choices; answering only ever sends your words, never more access.
 * ``scope``: a run waits for its scope to be approved. ``review``: a finished result waits for you to accept it, which
 * commits exactly its files on the Mac. ``phone`` is false when the Mac's review sheet is needed instead (a handoff's
 * files, or files Dervo couldn't record), so the phone offers only "Ask for changes". */
export type AskOption={id:string;label:string;detail:string};
export type Ask=
  |{kind:'question';taskId:string;questionId:string;ask:string;options:AskOption[];canSkip:boolean}
  |{kind:'scope';taskId:string;scopeVersion:number;what:string;files:string[];
    /** False when the run asks for more than its own files (the whole project, extra folders, full access): the Mac's
     * approve sheet shows that, and the phone's card doesn't, so it's approved there. */
    phone:boolean}
  |{kind:'review';taskId:string;attemptNumber:number;what:string;files:number;checked:boolean;phone:boolean};
export type ThreadDetail={projectId:string;channelId:string;name:string;messages:ThreadMessage[];at:number;
  /** What this thread waits on you for. */
  asks?:Ask[];
  /** Whether this phone may approve scopes and accept results. Set on the Mac, per phone, off until you turn it on. */
  approvals?:boolean;
  /** Older messages were left out to fit one sealed message. They're still on the Mac. */
  earlier?:boolean;
  /** The Mac has no such thread any more (deleted, or never there). Sent with no messages. */
  missing?:boolean};

export type Action=
  |{kind:'send';projectId:string;channelId:string;text:string}
  |{kind:'stop';projectId:string;channelId:string}
  /** A message to the project's Home coordinator, which starts or routes the work (``orchestrator_send``). */
  |{kind:'home';projectId:string;channelId:typeof HOME_CHANNEL;text:string}
  /** An answer to an agent's question: a choice, your own words, or skip (``task_question_answer``). */
  |{kind:'answer';projectId:string;channelId:string;taskId:string;questionId:string;answer:Answer}
  /** Approve or decline a run's scope (``task_approve`` / ``task_deny``). Only from a phone you allowed to approve. */
  |{kind:'scope';projectId:string;channelId:string;taskId:string;scopeVersion:number;decision:'approve'|'deny'}
  /** Accept a finished result, or ask for changes with what to change (``task_review``, then ``task_continue``). Only
   * from a phone you allowed to approve. */
  |{kind:'review';projectId:string;channelId:string;taskId:string;attemptNumber:number;decision:'approved'|'rejected';text?:string};
export type Answer={kind:'option';optionId:string}|{kind:'text';text:string}|{kind:'skip'};

/** Phone to Mac. Replays are dropped by the envelope's nonce (the Mac remembers the ones it has opened), an action
 * also by its id and its age (ACTION_TTL), and a pairing code works once. */
export type PhoneMessage=
  |{t:'pair';code:string;device:string}
  |{t:'hello'}
  |{t:'open';projectId:string;channelId:string}
  /** The phone left the thread it had open, so the Mac stops sending it updates. */
  |{t:'close'}
  /** ``at`` is when you tapped, ``sent`` when this copy left the phone, both on the phone's clock: the Mac takes only
   * their difference, plus the relay's ``held``, so the two devices' clocks never need to agree. */
  |{t:'action';id:string;at:number;sent?:number;macAt?:number;action:Action}
  |{t:'unpair'};

export type AckState='done'|'queued'|'refused'|'expired';
/** Mac to phone. */
export type MacMessage=
  |{t:'paired';mac:string}
  |{t:'snapshot';home:HomeSnapshot}
  |{t:'thread';thread:ThreadDetail}
  |{t:'ack';id:string;state:AckState;detail:string}
  |{t:'unpaired'};

/** How long an action waits for a Mac that's asleep. A message is worth sending an hour later; a Stop isn't, because by
 * then the run it meant has ended or moved on, and stopping whatever runs now would surprise you. */
export const ACTION_TTL={send:24*3600_000,home:24*3600_000,stop:2*60_000,
  // An answer, approval or review tapped while the Mac sleeps still goes when it wakes. Each names the exact question,
  // scope version or attempt it was for, and the Mac refuses it if that has moved on.
  answer:24*3600_000,scope:24*3600_000,review:24*3600_000} as const;
/** An answer in your own words reads up to this much (the host's own limit is 8 KB). */
export const MAX_ANSWER=4000;
/** Home's coordinator reads up to this much at once. */
export const MAX_HOME_TEXT=8000;
export const MAX_TEXT=20_000;

const id=(value:unknown,max=128)=>typeof value==='string'&&value.length>0&&value.length<=max;

export function readAction(value:unknown):Action|null{
  const a=value as Action;
  if(!a||typeof a!=='object'||!id(a.projectId)||!id(a.channelId))return null;
  if(a.kind==='send')return typeof a.text==='string'&&a.text.trim().length>0&&a.text.length<=MAX_TEXT?{kind:'send',projectId:a.projectId,channelId:a.channelId,text:a.text}:null;
  if(a.kind==='stop')return {kind:'stop',projectId:a.projectId,channelId:a.channelId};
  if(a.kind==='home')return a.channelId===HOME_CHANNEL&&typeof a.text==='string'&&a.text.trim().length>0&&a.text.length<=MAX_HOME_TEXT?{kind:'home',projectId:a.projectId,channelId:HOME_CHANNEL,text:a.text}:null;
  const scope={projectId:a.projectId,channelId:a.channelId};
  if(a.kind==='answer'){
    if(!id(a.taskId)||!id(a.questionId))return null;
    const answer=readAnswer(a.answer);
    return answer?{kind:'answer',...scope,taskId:a.taskId,questionId:a.questionId,answer}:null;
  }
  if(a.kind==='scope')return id(a.taskId)&&Number.isSafeInteger(a.scopeVersion)&&(a.decision==='approve'||a.decision==='deny')
    ?{kind:'scope',...scope,taskId:a.taskId,scopeVersion:a.scopeVersion,decision:a.decision}:null;
  if(a.kind==='review'){
    if(!id(a.taskId)||!Number.isSafeInteger(a.attemptNumber)||(a.decision!=='approved'&&a.decision!=='rejected'))return null;
    if(a.text!==undefined&&(typeof a.text!=='string'||a.text.length>MAX_TEXT))return null;
    const text=a.decision==='rejected'&&a.text?.trim()?a.text:undefined;
    return {kind:'review',...scope,taskId:a.taskId,attemptNumber:a.attemptNumber,decision:a.decision,...(text?{text}:{})};
  }
  return null;
}
function readAnswer(value:unknown):Answer|null{
  const a=value as Answer;
  if(!a||typeof a!=='object')return null;
  if(a.kind==='option')return id(a.optionId)?{kind:'option',optionId:a.optionId}:null;
  if(a.kind==='text')return typeof a.text==='string'&&a.text.trim().length>0&&a.text.length<=MAX_ANSWER?{kind:'text',text:a.text}:null;
  if(a.kind==='skip')return {kind:'skip'};
  return null;
}

export function readPhoneMessage(value:unknown):PhoneMessage|null{
  const m=value as PhoneMessage;
  if(!m||typeof m!=='object')return null;
  switch(m.t){
    case 'pair':return id(m.code,64)&&typeof m.device==='string'?{t:'pair',code:m.code,device:m.device.slice(0,80)||'Phone'}:null;
    case 'hello':return {t:'hello'};
    case 'unpair':return {t:'unpair'};
    case 'close':return {t:'close'};
    case 'open':return id(m.projectId)&&id(m.channelId)?{t:'open',projectId:m.projectId,channelId:m.channelId}:null;
    case 'action':{
      const action=readAction(m.action);
      return action&&id(m.id,64)&&Number.isFinite(m.at)?{t:'action',id:m.id,at:m.at,...(Number.isFinite(m.sent)?{sent:m.sent}:{}),...(Number.isSafeInteger(m.macAt)&&(m.macAt as number)>=0?{macAt:m.macAt}:{}),action}:null;
    }
    default:return null;
  }
}

const BUCKETS=new Set<Bucket>(['needs','working','finished','idle','unknown']);
const str=(value:unknown,max:number)=>typeof value==='string'?value.slice(0,max):'';

export function readHomeRow(value:unknown):HomeRow|null{
  const r=value as HomeRow;
  if(!r||typeof r!=='object'||!id(r.id)||!id(r.projectId)||!BUCKETS.has(r.bucket))return null;
  return {id:r.id,projectId:r.projectId,project:str(r.project,120),name:str(r.name,200)||'Untitled thread',bucket:r.bucket,line:str(r.line,400),
    step:typeof r.step==='string'?r.step.slice(0,40):null,unread:Number.isSafeInteger(r.unread)&&r.unread>0?r.unread:0,
    at:typeof r.at==='number'&&Number.isFinite(r.at)?r.at:null,question:typeof r.question==='string'?r.question.slice(0,600):null,stoppable:r.stoppable===true,quiet:r.quiet===true};
}

export function readMacMessage(value:unknown):MacMessage|null{
  const m=value as MacMessage;
  if(!m||typeof m!=='object')return null;
  switch(m.t){
    case 'paired':return {t:'paired',mac:str(m.mac,80)||'Your Mac'};
    case 'unpaired':return {t:'unpaired'};
    case 'ack':return id(m.id,64)&&['done','queued','refused','expired'].includes(m.state)?{t:'ack',id:m.id,state:m.state,detail:str(m.detail,600)}:null;
    case 'snapshot':{
      const h=m.home;
      if(!h||typeof h!=='object'||!Number.isFinite(h.at)||!Array.isArray(h.rows)||!Array.isArray(h.projects))return null;
      return {t:'snapshot',home:{at:h.at,mac:str(h.mac,80)||'Your Mac',
        projects:h.projects.filter(p=>p&&id(p.id)).map(p=>({id:p.id,name:str(p.name,120)})),
        rows:h.rows.map(readHomeRow).filter((row):row is HomeRow=>row!==null),
        ...(Number.isSafeInteger(h.hidden)&&h.hidden!>0?{hidden:h.hidden}:{}),
        ...(h.awake&&typeof h.awake==='object'&&typeof h.awake.enabled==='boolean'&&typeof h.awake.holding==='boolean'
          ?{awake:{enabled:h.awake.enabled,holding:h.awake.holding}}:{})}};
    }
    case 'thread':{
      const d=m.thread;
      if(!d||typeof d!=='object'||!id(d.projectId)||!id(d.channelId)||!Array.isArray(d.messages))return null;
      const asks=Array.isArray(d.asks)?d.asks.map(readAsk).filter((ask):ask is Ask=>ask!==null).slice(0,12):[];
      return {t:'thread',thread:{projectId:d.projectId,channelId:d.channelId,name:str(d.name,200),at:Number.isFinite(d.at)?d.at:0,...(d.earlier===true?{earlier:true}:{}),...(d.missing===true?{missing:true}:{}),
        ...(asks.length?{asks}:{}),...(d.approvals===true?{approvals:true}:{}),
        messages:d.messages.filter(x=>x&&id(x.id,256)&&['you','agent','dervo'].includes(x.from)).map(x=>({id:x.id,from:x.from,text:str(x.text,MAX_TEXT),at:typeof x.at==='number'?x.at:null,...(x.pending===true?{pending:true}:{})}))}};
    }
    default:return null;
  }
}

function readAsk(value:unknown):Ask|null{
  const a=value as Ask;
  if(!a||typeof a!=='object'||!id(a.taskId))return null;
  if(a.kind==='question'){
    if(!id(a.questionId)||!Array.isArray(a.options))return null;
    const options=a.options.filter(o=>o&&id(o.id)).slice(0,3).map(o=>({id:o.id,label:str(o.label,80),detail:str(o.detail,120)}));
    return {kind:'question',taskId:a.taskId,questionId:a.questionId,ask:str(a.ask,2000),options,canSkip:a.canSkip===true};
  }
  if(a.kind==='scope')return Number.isSafeInteger(a.scopeVersion)?{kind:'scope',taskId:a.taskId,scopeVersion:a.scopeVersion,what:str(a.what,400),
    files:Array.isArray(a.files)?a.files.filter(f=>typeof f==='string').slice(0,20).map(f=>f.slice(0,300)):[],phone:a.phone===true}:null;
  if(a.kind==='review')return Number.isSafeInteger(a.attemptNumber)?{kind:'review',taskId:a.taskId,attemptNumber:a.attemptNumber,what:str(a.what,400),
    files:Number.isSafeInteger(a.files)&&a.files>=0?a.files:0,checked:a.checked===true,phone:a.phone===true}:null;
  return null;
}
