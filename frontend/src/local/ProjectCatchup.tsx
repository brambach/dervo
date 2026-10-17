import {cloneElement,useEffect,useRef,useState,type ReactElement} from 'react';
import {RefreshCw,Search,X} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import {CatchupActions} from './CatchupActions';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {duration} from './TaskFlow';
import {savedStatus} from './WorkspaceSearch';
import {conversationDismiss,workspaceRequest,type Conversation,type WorkspacePage,type WorkspaceRow} from './api';
import './ProjectCatchup.css';
import {OutsideSessions} from './OutsideSessions';
import {gather,subthreads,type Gathered} from './subthreadState';
import {onRowKeys} from './rowKeys';
import {t} from '@/i18n';
import {blockedEnding} from './runEnding';

/** Catch up, 17 Sep: pick up a project in seconds. Top to bottom it answers where things stand, the one thing to do next,
 * what needs you, what's running and why, what changed since you last looked, and the thread you last had open. Every
 * line comes from a saved record: the catch-up rows (shared threads) and the live thread list (the same one the sidebar
 * reads). The running agent's why and next are its own reported words. Caps follow the research: short lists, no feed.
 *
 * 20 Sep, once Home's pane could open it (F1): this is a queue of decisions, not a wider copy of the pane. The pane is
 * the glance and withholds actions; this is the sit-down, because a 328 px column is a bad place to approve a commit.
 *
 * 23 Sep (design review item 5): rebuilt on the dv- system Home uses. The page title is "Catch up" in the display serif
 * with the project as its kicker, each section is Home's sheet of rows with the status glyph, and the next step is the
 * one lifted row, with the only pine pill on the page. The row it names carries the one accent when it asks you
 * something. Nothing enters: the page paints finished. */

export const CAPS={needs:3,running:3,changed:5};
const known=(value?:string|null)=>value&&!value.startsWith('Unknown:')?value:undefined;
const plural=(count:number,one:string,many:string)=>`${count} ${count===1?one:many}`;

export type NeedsItem={key:string;channelId:string;name:string;what:string;glyph:GlyphState;detail?:string;asked?:string;action:string;row?:WorkspaceRow;private?:boolean};
export type RunningItem={channelId:string;name:string;step:string;goal?:string;why?:string;next?:string;since?:number};
export type ChangedItem={key:string;channelId:string;name:string;title?:string;outcome:string;glyph:GlyphState;at?:number;row:WorkspaceRow;isNew:boolean};
/** The one thing to do next, and where it lives on the page: the row it names is the one lifted, with the pine pill. */
export type NextStep={where:'needs'|'changed'|'running'|'left'|'none';key?:string;name?:string;line:string;action:string;glyph:GlyphState;channelId?:string;taskId?:string};
/** Knowledge's line in Catch up: what's waiting for you there, lessons and skills, or just its name. */
export function knowledgeLabel(lessons?:number|null,skills?:number|null):string{
 const waiting=[lessons?plural(lessons,'lesson','lessons'):'',skills?plural(skills,'skill','skills'):''].filter(Boolean);
 return waiting.length?`Knowledge · ${waiting.join(' and ')} to review`:t('Knowledge');
}
export type LeftOff={channelId:string;name:string;asked?:string;at?:number;status?:string;glyph:GlyphState};
export type CatchupPlan={stand:string;next:NextStep;needs:NeedsItem[];moreNeeds:number;running:RunningItem[];moreRunning:number;changed:ChangedItem[];changedHeading:string;leftOff?:LeftOff};

export {blockedEnding};

/** Needs you, from the row: what it's waiting on, in words you'd act on, and the glyph whose shape says which kind. */
function needsOf(row:WorkspaceRow):NeedsItem|undefined{
 if(row.helper||row.kind!=='task'||row.status==='working')return undefined;
 const title=row.title&&row.title!==row.channelName?row.title:undefined;
 const base={key:`${row.kind}:${row.id}`,channelId:row.channelId,name:row.channelName,row,asked:title};
 if(row.outcomeUncertain||['disconnected','unknown','uncertain','lost'].includes(row.status??''))return {...base,glyph:'unknown',what:t('Outcome not confirmed'),detail:title,action:t('Check what happened')};
 if(row.stale)return {...base,glyph:'needs',what:t('Files changed after this result'),detail:title,action:t('Ask for a fresh check')};
 if(row.status==='ready_for_review'&&row.reviewPending)return undefined;
 if(row.status==='ready_for_review'&&row.humanDecision==='rejected')return {...base,glyph:'needs',what:t('Waiting for your correction'),detail:title,action:t('Write the correction')};
 if(row.status==='ready_for_review')return {...base,glyph:known(row.blocker)?'failed':'done',what:known(row.blocker)?t('Needs another look: {blocker}',{blocker:row.blocker??''}):t('Ready for your review'),detail:row.summary||title,action:t('Review the changes')};
 const ended=row.status==='blocked'?blockedEnding(row.ending):null;
 if(ended)return {...base,...ended,detail:known(row.blocker)??title};
 if(row.status==='blocked')return {...base,glyph:'failed',what:known(row.blocker)?t('Couldn’t finish: {blocker}',{blocker:row.blocker??''}):t('Couldn’t finish'),detail:title,action:t('See what stopped it')};
 if(row.status==='needs_permission')return {...base,glyph:'needs',what:t('Waiting for file permission'),detail:title,action:t('Choose the files')};
 return undefined;
}

/** A private thread has no shared records, so all Catch up knows is the host's activity index: how its newest task
 * ended, and its one-line summary. It used to say "Waiting for you" for every one of them, whatever Home said (UX review
 * 23 Sep, M7). Now it says what Home says for the same state, and when the index doesn't say how it ended, only what
 * the index does say. */
export function privateNeeds(c:Conversation):NeedsItem{
 const activity=c.activity,summary=activity?.summary?.trim()||undefined;
 const base={key:`conversation:${c.id}`,channelId:c.id,name:c.name,private:c.visibility==='private'};
 const said=(what:string)=>summary&&!summary.toLowerCase().startsWith(what.toLowerCase())?summary:undefined;
 const item=(glyph:GlyphState,what:string,action:string):NeedsItem=>({...base,glyph,what,detail:said(what),action});
 switch(activity?.ended){
  case 'asking':return item('needs',t('Asked you one thing before it carries on.'),t('Answer'));
  case 'needs_permission':return item('needs',t('Waiting for your permission before it starts.'),t('Review the scope'));
  // Since 17.1 the host saves 'blocked' only for work that failed (conversation_activity.end_of), and re-sorts older
  // ones once, so this X is a real failure.
  case 'blocked':return item('failed',t('Stopped'),t('Pick it up'));
  case 'disconnected':return {...item('unknown',t('Stopped'),t('Pick it up')),detail:said(t('Stopped'))??t('Dervo couldn’t confirm how it ended.')};
  case 'limit':return item('stopped',t('Stopped at a limit'),t('Pick it up'));  // beta.17.1, backend/run_ending.py
  case 'not_started':return item('stopped',t('Didn’t start'),t('Pick it up'));
  case 'snag':return item('needs',t('Finished, a step didn’t complete'),t('Open it'));
  case 'stopped':return {...item('stopped',t('Paused'),t('Pick it up')),detail:said(t('Paused'))??t('You paused it.')};
  case 'ready_for_review':return item('done',t('Finished'),t('Review'));
  default:return {...base,glyph:'needs',what:summary??t('Needs you'),action:t('Open it')};
 }
}

/** A result that landed, in one line. The row's own summary when there is one. */
function outcomeOf(row:WorkspaceRow):string{
 if(row.status==='answered')return t('Answered in the thread');
 if(row.status==='idle'&&row.humanDecision==='approved'){
  const files=row.changedFileCount?`${plural(row.changedFileCount,'file','files')} changed`:'';
  return [t('Approved by you'),files].filter(Boolean).join(' · ');
 }
 if(row.status==='completed'){
  const files=row.changedFiles?.length?`Changed ${row.changedFiles.slice(0,2).join(', ')}${(row.changedFileCount??0)>2?` and ${(row.changedFileCount??0)-2} more`:''}`:t('No files changed');
  const check=typeof row.testExitStatus==='number'?(row.testExitStatus===0?t('check passed'):t('check failed')):undefined;
  return [files,check,row.reviewPhase==='passed'?t('review passed'):undefined].filter(Boolean).join(' · ');
 }
 if(row.kind==='message')return row.matchRole==='assistant'?t('New reply'):t('New message');
 return savedStatus(row);
}
/** The glyph for a saved record, the same shapes Home and the sidebar use: running, done, failed, stopped, idle. */
function glyphOfRow(row:WorkspaceRow|undefined):GlyphState{
 if(!row)return 'idle';
 if(row.status==='working')return 'running';
 if(row.outcomeUncertain||['disconnected','unknown','uncertain','lost'].includes(row.status??''))return 'unknown';
 if(row.status==='blocked')return blockedEnding(row.ending)?.glyph??'failed';  // an X only for failed work (17.1)
 if(row.status==='needs_permission')return 'needs';
 if(row.status==='completed'&&typeof row.testExitStatus==='number'&&row.testExitStatus!==0)return 'failed';
 if(['completed','answered','idle','ready_for_review'].includes(row.status??'')||row.kind==='message')return 'done';
 return 'idle';
}
/** A saved time the way you'd say it: 5:04 PM today, yesterday 5:04 PM, Tue 5:04 PM this week, 12 Sep before that. */
export function whenLabel(seconds:number,now=Date.now()/1000):string{
 const date=new Date(seconds*1000),today=new Date(now*1000);
 const clock=date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
 const days=Math.round((new Date(today.toDateString()).getTime()-new Date(date.toDateString()).getTime())/86400000);
 if(days<=0)return clock;
 if(days===1)return t('yesterday {clock}',{clock});
 if(days<7)return `${date.toLocaleDateString([],{weekday:'short'})} ${clock}`;
 return date.toLocaleDateString([],{day:'numeric',month:'short'});
}
const rowAt=(row:WorkspaceRow)=>row.completedAt??row.answeredAt??row.askedAt??undefined;

export function catchupPlan(rows:WorkspaceRow[],conversations:Conversation[],{since,last,now=Date.now()/1000}:{since?:number|null;last?:string|null;now?:number}):CatchupPlan{
 const names=new Map(conversations.map(c=>[c.id,c.name]));
 const privately=new Set(conversations.filter(c=>c.visibility==='private').map(c=>c.id));
 const own=rows.filter(row=>!row.helper&&names.has(row.channelId));
 // The newest record per thread speaks for it, like the sidebar.
 const latest=new Map<string,WorkspaceRow>();
 for(const row of own)if(!latest.has(row.channelId))latest.set(row.channelId,row);
 const needs:NeedsItem[]=[];
 // The host's index decides whether a thread still needs you: a newer message or Dismiss clears it.
 const activity=new Map(conversations.map(c=>[c.id,c.activity]));
 const stillNeeds=(channelId:string)=>{const a=activity.get(channelId);return !a||a.status==='needs';};
 for(const row of latest.values()){const item=needsOf(row);if(item&&stillNeeds(row.channelId))needs.push({...item,name:names.get(row.channelId)??item.name,private:privately.has(row.channelId)});}
 // Private threads aren't in the shared catch-up rows; the sidebar's own index still says how they ended.
 for(const c of conversations)if(c.activity?.status==='needs'&&!latest.has(c.id))needs.push(privateNeeds(c));
 const running:RunningItem[]=conversations.filter(c=>c.activity?.running).map(c=>({channelId:c.id,name:c.name,step:c.activity!.step||t('Starting'),goal:c.activity!.goal??undefined,why:c.activity!.why??undefined,next:c.activity!.next??undefined,since:c.activity!.since??undefined}))
  .sort((a,b)=>(b.since??0)-(a.since??0));
 const settled=[...latest.values()].filter(row=>row.kind==='task'?row.status!=='working'&&!row.reviewPending&&!(needsOf(row)&&stillNeeds(row.channelId)):row.matchRole==='assistant');
 const fresh=settled.filter(row=>row.isNew);
 const changedRows=(fresh.length?fresh:settled).slice(0,CAPS.changed);
 const changed=changedRows.map(row=>({key:`${row.kind}:${row.id}`,channelId:row.channelId,name:names.get(row.channelId)??row.channelName,title:row.title&&row.title!==row.channelName?row.title:undefined,outcome:outcomeOf(row),glyph:glyphOfRow(row),at:rowAt(row),row,isNew:!!row.isNew}));
 const parts=[fresh.length?t('{count} new results',{count:fresh.length}):undefined,needs.length?t('{count} need you',{count:needs.length}):undefined,running.length?t('{length} running',{length:running.length}):undefined].filter(Boolean);
 const stand=parts.length?`${since?`Since ${whenLabel(since,now)}: `:''}${parts.join(', ')}.`:since?`Nothing new since ${whenLabel(since,now)}. Nothing needs you.`:rows.length||conversations.length?t('Nothing needs you right now.'):t('Nothing here yet.');
 const leftRow=last?latest.get(last)??own.find(row=>row.channelId===last):undefined;
 const leftName=last?names.get(last):undefined;
 const leftOff=last&&leftName?{channelId:last,name:leftName,asked:leftRow?.kind==='task'?leftRow.title:undefined,at:leftRow?rowAt(leftRow):undefined,status:leftRow?outcomeOf(leftRow):undefined,glyph:glyphOfRow(leftRow)}:undefined;
 let next:NextStep;
 const firstFresh=fresh[0]&&changed.find(item=>item.row===fresh[0]);
 if(needs[0])next={where:'needs',key:needs[0].key,name:needs[0].name,line:needs[0].what,action:needs[0].action,glyph:needs[0].glyph,channelId:needs[0].channelId,taskId:needs[0].row?.id};
 else if(firstFresh)next={where:'changed',key:firstFresh.key,name:firstFresh.name,line:firstFresh.outcome,action:t('Read it'),glyph:firstFresh.glyph,channelId:firstFresh.channelId,taskId:firstFresh.row.kind==='task'?firstFresh.row.id:undefined};
 else if(running[0])next={where:'running',key:running[0].channelId,name:running[0].name,line:`${running[0].step}${running[0].since?` (${duration(Math.max(0,now-running[0].since))})`:''}`,action:t('Watch it'),glyph:'running',channelId:running[0].channelId};
 else if(leftOff)next={where:'left',key:leftOff.channelId,name:leftOff.name,line:leftOff.status??t('Nothing new here since you left it.'),action:t('Continue'),glyph:leftOff.glyph,channelId:leftOff.channelId};
 else next={where:'none',line:t('Start a thread and hand Dervo something to do.'),action:t('New thread'),glyph:'idle'};
 const listed=new Set([...needs.slice(0,CAPS.needs).map(n=>n.channelId),...running.slice(0,CAPS.running).map(r=>r.channelId),...changed.map(c=>c.channelId)]);
 return {stand,next,needs:needs.slice(0,CAPS.needs),moreNeeds:Math.max(0,needs.length-CAPS.needs),running:running.slice(0,CAPS.running),moreRunning:Math.max(0,running.length-CAPS.running),changed,changedHeading:fresh.length?t('What changed'):t('Latest results'),
  leftOff:leftOff&&(next.where==='left'||(!listed.has(leftOff.channelId)&&next.channelId!==leftOff.channelId))?leftOff:undefined};
}

function useNow(active:boolean){
 const [now,setNow]=useState(()=>Date.now()/1000);
 useEffect(()=>{if(!active)return;const timer=setInterval(()=>setNow(Date.now()/1000),1000);return ()=>clearInterval(timer);},[active]);
 return now;
}

type Props={onConversationsChanged?:()=>Promise<unknown>;projectId:string;profileId:string;projectName?:string;conversations:Conversation[];lastConversation?:string|null;open:boolean;returnLabel?:string;onClose:()=>void;
 onOpenConversation:(channelId:string)=>void;onOpenTask:(channelId:string,taskId:string)=>void;onOpenKnowledge:()=>void;onAllActivity:()=>void;onSearch:()=>void;onNewConversation:()=>void};

export function ProjectCatchup(props:Props){
 const {projectId,profileId,open,conversations}=props;
 const [page,setPage]=useState<WorkspacePage|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const generation=useRef(0),heading=useRef<HTMLHeadingElement>(null);
 async function load(){
  const token=++generation.current;setBusy(true);setError('');
  try{
   const result=await workspaceRequest<WorkspacePage>('project_catchup',{projectId,channelId:'',profileId,scope:'project',limit:30});
   if(generation.current!==token)return;
   // The first pass on an older workspace organises records; ask again shortly rather than show an empty briefing.
   if(result.indexing){setTimeout(()=>{if(generation.current===token)void load();},750);return;}
   // A refresh marks this visit as seen, so keep what was new when you arrived and the time you last looked.
   setPage(old=>old?{...result,since:old.since,results:result.results.map(row=>({...row,isNew:row.isNew||old.results.some(o=>o.kind===row.kind&&o.id===row.id&&o.isNew)}))}:result);
  }catch(e){if(generation.current===token)setError(e instanceof Error?e.message:String(e));}
  finally{if(generation.current===token)setBusy(false);}
 }
 useEffect(()=>{if(!open)return;setPage(null);void load();requestAnimationFrame(()=>heading.current?.focus());return ()=>{generation.current++;};},[open,projectId,profileId]);
 async function dismiss(channelId:string){
  try{await conversationDismiss(projectId,channelId);await props.onConversationsChanged?.();await load();}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
 }
 const plan=catchupPlan(page?.results??[],conversations,{since:page?.since,last:props.lastConversation});
 // When a running conversation settles, its result belongs in What changed: read the records again.
 const runningKey=conversations.filter(c=>c.activity?.running).map(c=>c.id).sort().join();
 const previousRunning=useRef(runningKey);
 useEffect(()=>{const before=previousRunning.current;previousRunning.current=runningKey;if(open&&page&&before.split(',').some(id=>id&&!runningKey.split(',').includes(id)))void load();},[runningKey]);
 const ticking=useNow(open&&plan.running.length>0);
 if(!open)return null;
 const loading=!page&&!error;
 const next=plan.next;
 const openNext=()=>{if(next.channelId&&next.taskId)props.onOpenTask(next.channelId,next.taskId);else if(next.channelId)props.onOpenConversation(next.channelId);else props.onNewConversation();};
 const lifted=(where:NextStep['where'],key:string)=>next.where===where&&next.key===key;
 // The one accent on the page: the lifted row's glyph, and only when its shape says it asks you something.
 const accent=next.where==='needs'&&next.glyph==='needs';
 const primary=<button type="button" className="dv-btn dv-btn-primary" data-testid="catchup-next" onClick={openNext}>{next.action}</button>;
 const name=(text:string,priv?:boolean)=><>{text}{priv&&<span className="dv-caption"> {t('· private')}</span>}</>;

 const needsRow=(item:NeedsItem)=>{
  const open=lifted('needs',item.key);
  const act=()=>item.row?props.onOpenTask(item.channelId,item.row.id):props.onOpenConversation(item.channelId);
  const dismissButton=<button type="button" className="dv-btn dv-btn-quiet dervo-catchup-dismiss" disabled={busy} onClick={()=>void dismiss(item.channelId)}>{t('Dismiss')}</button>;
  if(open)return <article key={item.key} role="listitem" className="dv-row dv-open" data-kind="needs" data-lead="">
   <StatusGlyph state={item.glyph} accent={accent}/>
   <div>
    <div className="dv-row-name">{name(item.name,item.private)}</div>
    <div className="dv-row-line">{item.what}</div>
    {(item.asked||(item.detail&&item.detail!==item.asked))&&<dl className="dv-facts">
     {item.asked&&<><dt>{t('Asked')}</dt><dd>{item.asked}</dd></>}
     {item.detail&&item.detail!==item.asked&&<><dt>{item.row?t('Result'):t('Latest')}</dt><dd>{item.detail}</dd></>}
    </dl>}
    <div className="dv-row-actions dervo-catchup-actions">
     {primary}
     {item.row&&<CatchupActions row={item.row} projectId={projectId} disabled={busy} onDone={load}/>}
     {item.row&&<button type="button" className="dv-btn dv-btn-ghost" onClick={()=>props.onOpenConversation(item.channelId)}>{t('Open thread')}</button>}
     {dismissButton}
    </div>
   </div>
  </article>;
  return <article key={item.key} role="listitem" className="dv-row" data-kind="needs">
   <StatusGlyph state={item.glyph}/>
   <div>
    <button type="button" className="dv-row-name" onClick={()=>props.onOpenConversation(item.channelId)}>{name(item.name,item.private)}</button>
    <div className="dv-row-line">{item.what}</div>
    {item.detail&&<div className="dv-row-line dervo-catchup-detail">{item.detail}</div>}
   </div>
   <div className="dv-row-end">
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={act}>{item.action}</button>
    {dismissButton}
   </div>
  </article>;
 };

 // A run asks nothing of you: one row you read, the whole row opening the thread. Why and Next are the agent's own
 // words, which the pane never has; the line under the name is the step Dervo recorded.
 const runningRow=(item:RunningItem)=>{
  const open=lifted('running',item.channelId);
  const said=<>{item.why&&<div className="dv-row-line dervo-catchup-detail"><span className="dervo-catchup-said">{t('Why')}</span>{item.why}</div>}
   {item.next&&<div className="dv-row-line dervo-catchup-detail"><span className="dervo-catchup-said">{t('Next')}</span>{item.next}</div>}</>;
  const when=item.since?<span className="dv-when">{duration(Math.max(0,ticking-item.since))}</span>:null;
  if(open)return <article key={item.channelId} role="listitem" className="dv-row dv-open" data-kind="running" data-lead="">
   <StatusGlyph state="running"/>
   <div><div className="dv-row-name">{item.name}</div><div className="dv-row-line">{item.step}</div>{said}<div className="dv-row-actions">{primary}</div></div>
   <div className="dv-row-end">{when}</div>
  </article>;
  return <div key={item.channelId} role="listitem" className="dervo-catchup-item" data-kind="running">
   <button type="button" className="dv-row dervo-catchup-button" onClick={()=>props.onOpenConversation(item.channelId)}>
    <StatusGlyph state="running"/>
    <span className="dervo-catchup-copy"><span className="dv-row-name">{item.name}</span><span className="dv-row-line">{item.step}</span>{said}</span>
    <span className="dv-row-end">{when}</span>
   </button>
  </div>;
 };

 const changedRow=(item:ChangedItem)=>{
  const open=lifted('changed',item.key);
  const openIt=()=>item.row.kind==='task'?props.onOpenTask(item.channelId,item.row.id):props.onOpenConversation(item.channelId);
  const end=<>{item.isNew&&<span className="dv-tag">{t('New')}</span>}{item.at&&<span className="dv-when">{whenLabel(item.at)}</span>}</>;
  if(open)return <article key={item.key} role="listitem" className="dv-row dv-open" data-kind="changed" data-lead="">
   <StatusGlyph state={item.glyph}/>
   <div><div className="dv-row-name">{item.name}</div><div className="dv-row-line">{item.outcome}</div>
    {item.title&&<dl className="dv-facts"><dt>{t('Asked')}</dt><dd>{item.title}</dd></dl>}
    <div className="dv-row-actions">{primary}{item.row.kind==='task'&&<button type="button" className="dv-btn dv-btn-ghost" onClick={()=>props.onOpenConversation(item.channelId)}>{t('Open thread')}</button>}</div></div>
   <div className="dv-row-end">{end}</div>
  </article>;
  return <div key={item.key} role="listitem" className="dervo-catchup-item" data-kind="changed">
   <button type="button" className="dv-row dervo-catchup-button" onClick={openIt}>
    <StatusGlyph state={item.glyph}/>
    <span className="dervo-catchup-copy"><span className="dv-row-name">{item.name}</span>
     {item.title&&<span className="dv-row-line dervo-catchup-detail">{item.title}</span>}<span className="dv-row-line">{item.outcome}</span></span>
    <span className="dv-row-end">{end}</span>
   </button>
  </div>;
 };

 const leftRow=(left:LeftOff)=>{
  const end=left.at?<span className="dv-when">{whenLabel(left.at)}</span>:null;
  if(next.where==='left')return <article role="listitem" className="dv-row dv-open" data-kind="left" data-lead="">
   <StatusGlyph state={left.glyph}/>
   <div><div className="dv-row-name">{left.name}</div><div className="dv-row-line">{next.line}</div>
    {left.asked&&<dl className="dv-facts"><dt>{t('Asked')}</dt><dd>{left.asked}</dd></dl>}
    <div className="dv-row-actions">{primary}</div></div>
   <div className="dv-row-end">{end}</div>
  </article>;
  return <div role="listitem" className="dervo-catchup-item" data-kind="left">
   <button type="button" className="dv-row dervo-catchup-button" onClick={()=>props.onOpenConversation(left.channelId)}>
    <StatusGlyph state={left.glyph}/>
    <span className="dervo-catchup-copy"><span className="dv-row-name">{left.name}</span>
     {left.asked&&<span className="dv-row-line dervo-catchup-detail">You asked: {left.asked}</span>}{left.status&&<span className="dv-row-line">{left.status}</span>}</span>
    <span className="dv-row-end">{end}</span>
   </button>
  </div>;
 };

 // Subthreads gather under their parent within each section (beta.17, #27): the parent's row there carries them, or a
 // row for the parent holds them where the first one stood. The lifted next step keeps its own place.
 type Kid={channelId:string;name:string;glyph:GlyphState;line:string;when?:string;open:()=>void};
 const kidsBlock=(kids:Kid[])=><div className="dervo-catchup-kids" role="list">{kids.map(kid=>
  <button key={kid.channelId} type="button" role="listitem" className="dervo-catchup-kid" onClick={kid.open}>
   <StatusGlyph state={kid.glyph}/>
   <span className="dervo-catchup-kid-copy"><span className="dervo-catchup-kid-name">{kid.name}{kid.when&&<span className="dv-when">{kid.when}</span>}</span><span className="dervo-catchup-kid-line">{kid.line}</span></span>
  </button>)}</div>;
 const families=subthreads(conversations);
 const family=(where:'needs'|'running'|'changed',parentId:string,kids:Kid[])=>{
  const parent=conversations.find(c=>c.id===parentId),all=families.get(parentId)??[];
  const runningNow=all.filter(c=>c.activity?.running).length;
  const sub=where==='running'?t('{runningNow} of {length} running',{runningNow,length:all.length}):where==='needs'?t('{count} need you',{count:kids.length}):t('{length} finished',{length:kids.length});
  return <div key={`family:${where}:${parentId}`} role="listitem" className="dervo-catchup-family" data-kind={where}>
   <button type="button" className="dv-row dervo-catchup-button" onClick={()=>props.onOpenConversation(parentId)}>
    <StatusGlyph state={parent?.activity?.running?'running':'idle'}/>
    <span className="dervo-catchup-copy"><span className="dv-row-name">{parent?.name??t('Its parent thread')}<span className="dervo-catchup-sub">{sub}</span></span></span>
   </button>
   {kidsBlock(kids)}
   {where==='running'&&runningNow>0&&<p className="dv-caption dervo-catchup-family-note">{t('{parent} gets a review of them once they’ve all finished.',{parent:parent?.name??t('Its parent')})}</p>}
  </div>;
 };
 function grouped<T extends {channelId:string}>(where:'needs'|'running'|'changed',items:T[],render:(item:T)=>ReactElement,kid:(item:T)=>Kid,keyOf:(item:T)=>string){
  return gather(items,conversations,item=>lifted(where,keyOf(item))).map((entry:Gathered<T>)=>{
   if(entry.kind==='parent')return family(where,entry.parentId,entry.kids.map(kid));
   if(!entry.kids.length)return render(entry.item);
   return <div key={`family:${where}:${entry.item.channelId}`} role="listitem" className="dervo-catchup-family" data-kind={where}>
    {cloneElement(render(entry.item) as ReactElement<{role?:string}>,{role:undefined})}{kidsBlock(entry.kids.map(kid))}
   </div>;
  });
 }
 const needsKid=(item:NeedsItem):Kid=>({channelId:item.channelId,name:item.name,glyph:item.glyph,line:item.what,when:item.row&&rowAt(item.row)?whenLabel(rowAt(item.row)!):undefined,
  open:()=>item.row?props.onOpenTask(item.channelId,item.row.id):props.onOpenConversation(item.channelId)});
 const runningKid=(item:RunningItem):Kid=>({channelId:item.channelId,name:item.name,glyph:'running',line:item.step,when:item.since?duration(Math.max(0,ticking-item.since)):undefined,open:()=>props.onOpenConversation(item.channelId)});
 const changedKid=(item:ChangedItem):Kid=>({channelId:item.channelId,name:item.name,glyph:item.glyph,line:item.outcome,when:item.at?whenLabel(item.at):undefined,
  open:()=>item.row.kind==='task'?props.onOpenTask(item.channelId,item.row.id):props.onOpenConversation(item.channelId)});
 const label=(id:string,text:string,count?:number)=><h2 id={id} className="dv-label dervo-catchup-label">{text}{count!==undefined&&<span className="dervo-catchup-count"> · {count}</span>}</h2>;
 const needsSection=plan.needs.length>0&&<section key="needs" className="dervo-catchup-section" aria-labelledby="catchup-needs">
  {label('catchup-needs',t('Needs you'),plan.needs.length+plan.moreNeeds)}
  <div className="dv-sheet" role="list">{grouped('needs',plan.needs,needsRow,needsKid,item=>item.key)}</div>
  {plan.moreNeeds>0&&<p className="dv-caption dervo-catchup-more">{t('{count} more wait for you. They’re marked in the sidebar.',{count:plan.moreNeeds})}</p>}
 </section>;
 const runningSection=plan.running.length>0&&<section key="running" className="dervo-catchup-section" aria-labelledby="catchup-running">
  {label('catchup-running',t('Running now'),plan.running.length+plan.moreRunning)}
  <div className="dv-sheet" role="list">{grouped('running',plan.running,runningRow,runningKid,item=>item.channelId)}</div>
  {plan.running.some(item=>item.why||item.next)&&<p className="dv-caption dervo-catchup-more">{t('Why and Next are each agent’s own words. The line above them is what Dervo recorded.')}</p>}
  {plan.moreRunning>0&&<p className="dv-caption dervo-catchup-more">{t('{count} more are running. See the sidebar.',{count:plan.moreRunning})}</p>}
 </section>;
 const changedSection=plan.changed.length>0&&<section key="changed" className="dervo-catchup-section" aria-labelledby="catchup-changed">
  {label('catchup-changed',plan.changedHeading,plan.changed.length)}
  <div className="dv-sheet" role="list">{grouped('changed',plan.changed,changedRow,changedKid,item=>item.key)}</div>
 </section>;
 // The section that holds the next step comes first, so the lifted row is always the first thing under the lead. Up and
 // Down, or j and k, go from row to row (dd #21, rowKeys.ts), starting from the heading the screen opens on.
 const sections=next.where==='changed'?[needsSection,changedSection,runningSection]:[needsSection,runningSection,changedSection];
 return <section className="dervo-catchup" aria-label={t('Catch up')} data-testid="project-catchup" onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();props.onClose();return;}onRowKeys(event);}}>
  <div className="dv-column dervo-catchup-scroll">
   <div className="dv-measure">
    <header className="dv-page-head dervo-catchup-head">
     <div className="dervo-catchup-top">
      <p className="dervo-catchup-kicker">{props.projectName??t('Your project')}</p>
      <div className="dervo-catchup-tools">
       <Button variant="ghost" size="icon" aria-label={t('Search this project')} onClick={props.onSearch}><Search/></Button>
       <Button variant="ghost" size="icon" aria-label={t('Refresh')} disabled={busy} onClick={()=>void load()}><RefreshCw className={busy&&page?'animate-spin motion-reduce:animate-none':''}/></Button>
       <Button variant="ghost" size="icon" aria-label={props.returnLabel??t('Close catch up')} onClick={props.onClose}><X/></Button>
      </div>
     </div>
     <h1 ref={heading} tabIndex={-1} data-panel-focus="search" className="dv-display-1">{t('Catch up')}</h1>
     <p className="dv-page-lead" role="status" data-testid="catchup-stand">{loading?t('Reading your saved work…'):plan.stand}</p>
    </header>

    {error&&<div role="alert" className="dervo-catchup-fail"><StatusGlyph state="failed"/><p>{error}</p><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void load()}>{t('Try again')}</button></div>}

    {!loading&&<>
     {next.where==='none'&&<div className="dv-sheet" role="list"><article role="listitem" className="dv-row dv-open" data-kind="start" data-lead="">
      <StatusGlyph state="idle"/><div><div className="dv-row-name">{next.line}</div><div className="dv-row-actions">{primary}</div></div>
     </article></div>}
     {sections}
     {plan.leftOff&&<section className="dervo-catchup-section" aria-labelledby="catchup-left">
      {label('catchup-left',t('Last thread you opened'))}
      <div className="dv-sheet" role="list">{leftRow(plan.leftOff)}</div>
     </section>}
     <OutsideSessions projectId={projectId} variant="screen" onOpen={props.onOpenConversation} onCreated={props.onConversationsChanged}/>
     <footer className="dervo-catchup-foot">
      <button type="button" className="dv-btn dv-btn-quiet" onClick={props.onAllActivity}>{t('All activity')}</button>
      <button type="button" className="dv-btn dv-btn-quiet" onClick={props.onOpenKnowledge}>{knowledgeLabel(page?.lessonsPending,page?.skillsPending)}</button>
      {page?.incomplete&&<span className="dv-caption">{t('Some older records weren’t included.')}</span>}
     </footer>
    </>}
   </div>
  </div>
 </section>;
}
