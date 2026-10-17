import {Fragment,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {codingRequest,local,registerConversations,type Conversation} from './api';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {GuideActions} from './GuideActions';
import {SETTLED_DETAIL_MS,decisionLine,type CodingTask,type Detail} from './CodingTasks';
import {readWorkerQuestion} from './workerQuestionState';
import {plainSummary} from './homeRequests';
import type {PeekFact,PickupItem} from './Home';
import {ReminderFailure,RemindToggle,type ReminderControl} from './Reminders';

/** Home's thread panel and a row's peek (16.2, #30; evidence/design-tests/b162-home). Home decides what each says from
 * the pickup record and passes it in; this file only draws it and, for the panel, reads the thread's own task record.
 * The panel's decision is the thread's decision bar: decisionLine for the words and GuideActions for the buttons, with
 * the same operations and the same record conditions, so there's no second way to approve. What the bar can't do here
 * (a correction, the scope, a question with choices) opens the thread where it can. */

const Close=()=><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true" focusable="false"><path d="M4 4l8 8M12 4l-8 8"/></svg>;
const Arrow=()=><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M8 13V3M3.5 7.5L8 3l4.5 4.5"/></svg>;

/** Facts as the design system's two-column list. Changed files are listed in mono, with "and N more" past the cap. */
export function FactList({facts,className=''}:{facts:PeekFact[];className?:string}){
 if(!facts.length)return null;
 return <dl className={`dv-facts ${className}`}>{facts.map(fact=><Fragment key={fact.label}>
  <dt>{fact.label}</dt>
  <dd>{fact.files?<span className="dv-home-files">{fact.files.map(path=><code key={path}>{path}</code>)}{fact.more?<span className="dv-caption">and {fact.more} more</span>:null}</span>:fact.text}</dd>
 </Fragment>)}</dl>;
}

type Place={top:number;left:number;width:number};
/** Where the peek goes: beside the row when Home has room for it on either side, else under the row (over the rows
 * below it, which is what a peek is for), flipped above when it would run off the window. */
export function peekPlace(row:{top:number;bottom:number;left:number;right:number},bounds:{left:number;right:number},height:number,viewHeight:number,wide=380):Place{
 const width=Math.min(wide,bounds.right-bounds.left-16);
 let left:number,top:number;
 if(bounds.right-row.right>=width+16){left=row.right+8;top=row.top;}
 else if(row.left-bounds.left>=width+16){left=row.left-width-8;top=row.top;}
 else{
  left=Math.max(bounds.left+8,Math.min(row.right-width,bounds.right-width-8));
  top=row.bottom+6;
  if(top+height>viewHeight-8)top=row.top-height-6;
 }
 return {top:Math.max(8,Math.min(top,viewHeight-height-8)),left,width};
}

/** The peek beside a row: what you asked, the changed files, checks, what wasn't checked and what's next. It opens on
 * hover after a short wait and on keyboard focus, and Esc closes it (Home owns those). Nothing in it takes focus or the
 * pointer: clicking the row opens the thread panel, where the actions are. */
export function HomePeek({id,item,sentence,facts,glyph,accent,row,bounds}:{id:string;item:PickupItem;sentence:string|null;facts:PeekFact[];glyph:GlyphState;accent:boolean;
 row:{top:number;bottom:number;left:number;right:number};bounds:{left:number;right:number}}){
 const ref=useRef<HTMLDivElement>(null);
 const [place,setPlace]=useState<Place|null>(null);
 useLayoutEffect(()=>{
  const node=ref.current;
  if(node)setPlace(peekPlace(row,bounds,node.offsetHeight,window.innerHeight));
 },[item.id,row.top,row.left,row.right,bounds.left,bounds.right]);
 return <div ref={ref} id={id} role="tooltip" className="dv-home-peek" data-testid="home-peek"
  style={place?{top:place.top,left:place.left,width:place.width}:{top:0,left:0,width:380,visibility:'hidden'}}>
  <div className="dv-home-peek-head"><StatusGlyph state={glyph} accent={accent}/><b>{item.name}</b></div>
  {sentence&&<p className="dv-home-peek-did">{sentence}</p>}
  <FactList facts={facts} className="dv-home-peek-facts"/>
  <p className="dv-caption dv-home-peek-foot">Click to open here · Esc closes</p>
 </div>;
}

// A draft typed in one thread's panel stays with that thread while you move between them.
const drafts=new Map<string,string>();

/** The newest task of the thread that's its own work (not a helper's), or the one the pickup named. */
export function panelTask(tasks:CodingTask[]|undefined,taskId:string|null|undefined):CodingTask|null{
 const rows=(tasks??[]).filter(task=>task.origin!=='collaboration');
 return rows.find(task=>task.id===taskId)??rows.at(-1)??null;
}
/** The last thing the agent said in this task, as one plain paragraph for the panel. */
export function lastReply(task:CodingTask|null):string|null{
 const text=[...(task?.messages??[])].reverse().find(message=>message.role==='assistant'&&typeof message.text==='string'&&message.text.trim())?.text;
 if(!text)return null;
 const plain=plainSummary(text).replace(/^#{1,6}\s+/gm,'').replace(/^\s*[-*•]\s+/gm,'').replace(/\s+/g,' ').trim();
 return plain.length>600?plain.slice(0,599).replace(/\s+\S*$/,'')+'…':plain;
}

type PanelProps={item:PickupItem;projectId:string;glyph:GlyphState;accent:boolean;asks:boolean;sentence:string|null;step:string|null;facts:PeekFact[];when:string;
 position:{at:number;of:number};focusComposer:number;notice:string;stopping:boolean;
 onClose:()=>void;onStep:(delta:1|-1)=>void;onOpenThread:(channelId:string,focus?:boolean)=>void;
 onOpenTask?:(channelId:string,taskId:string,trigger:HTMLElement)=>void;onStop:(channelId:string)=>void;
 onActed:(channelId:string)=>void;onChanged:()=>Promise<unknown>|void;
 /** Remind me tomorrow on this thread (9 Oct), as the bell in the head. */
 reminder?:ReminderControl};

/** One thread where Home's chat was: its sentence, the question or the decision with its buttons, what you asked, the
 * changed files, the last reply and a composer that sends to this thread. */
export function HomePanel({item,projectId,glyph,accent,asks,sentence,step,facts,when,position,focusComposer,notice,stopping,onClose,onStep,onOpenThread,onOpenTask,onStop,onActed,onChanged,reminder}:PanelProps){
 const client=useQueryClient();
 const channelId=item.id;
 const running=item.kind==='running';
 const tasks=useQuery({queryKey:['home-panel-tasks',projectId,channelId],retry:false,refetchInterval:running?5000:20000,
  queryFn:()=>local<CodingTask[]>('tasks',{projectId,channelId})});
 const task=panelTask(tasks.data,item.task_id);
 const settled=!!task&&task.state!=='working'&&task.state!=='answered';
 // The thread's decision bar reads the same record under the same key (CodingTasks.TaskFooter), so they share it.
 const detail=useQuery({queryKey:['task-detail',projectId,channelId,task?.id],enabled:settled,retry:false,refetchInterval:SETTLED_DETAIL_MS,
  queryFn:()=>local<Detail>('task_detail',{projectId,channelId,taskId:task!.id})});
 const latest=task&&detail.data?.task.id===task.id?detail.data.evidence.attempts.at(-1):undefined;
 const decision=task&&!running?decisionLine(task,latest):null;
 const worker=(task?.questions??[]).map(readWorkerQuestion).find(question=>question?.state==='pending'&&question.canAnswer)??null;
 const reply=lastReply(task);
 const [draft,setDraft]=useState(()=>drafts.get(channelId)??'');
 const [sending,setSending]=useState(false),[error,setError]=useState('');
 const field=useRef<HTMLTextAreaElement>(null),body=useRef<HTMLDivElement>(null),inspect=useRef<HTMLButtonElement>(null);
 useEffect(()=>{if(focusComposer)field.current?.focus();},[focusComposer]);
 useEffect(()=>{body.current?.scrollTo?.({top:0});},[channelId]);
 function edit(value:string){setDraft(value);if(value)drafts.set(channelId,value);else drafts.delete(channelId);}
 async function refresh(){
  await tasks.refetch();
  if(settled)await detail.refetch();
  await client.invalidateQueries({queryKey:['conversation-feed',projectId,channelId]});
  await onChanged();
  onActed(channelId);
 }
 function openTask(trigger:HTMLElement){
  if(task&&onOpenTask)onOpenTask(channelId,task.id,trigger);
  else onOpenThread(channelId);
 }
 async function send(){
  const text=draft.trim();
  if(!text||sending)return;
  setSending(true);setError('');
  try{
   // The thread composer's own call (api.codingRequest), with the thread's defaults: the host decides as it would there.
   // The pickup only lists this project's threads, so the binding it needs is this project's.
   registerConversations([{id:channelId,project_id:projectId} as Conversation]);
   await codingRequest(channelId,text);
   edit('');
   await refresh();
  }catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  finally{setSending(false);}
 }
 const question=item.question&&item.next?(worker?.ask??item.next):null;
 return <section className="dv-home-panel" aria-label={`Thread: ${item.name}`} data-kind={item.kind} data-testid="home-panel">
  <header className="dv-home-panel-head">
   <StatusGlyph state={glyph} accent={accent}/>
   <h2 className="dv-home-panel-name" title={item.name}>{item.name}{item.private?<span className="dv-caption"> · private</span>:null}</h2>
   {when&&<span className="dv-when">{when}</span>}
   <span className="dv-home-panel-spacer"/>
   {reminder&&<RemindToggle control={reminder} channelId={channelId} name={item.name}/>}
   <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>onOpenThread(channelId)}>Open thread</button>
   <button type="button" className="dv-btn-icon dv-home-panel-close" aria-label="Back to Home" title="Back to Home" onClick={onClose}><Close/></button>
  </header>
  <div className="dv-home-panel-body" ref={body}>
   {reminder&&<ReminderFailure control={reminder} place="panel" channelId={channelId}/>}
   {sentence&&<p className="dv-home-panel-did">{sentence}</p>}
   {asks&&<div className="dv-home-panel-ask" role="group" aria-label={item.kind==='permission'?'Wants your permission':'Asks you'}>
    <p className="dv-home-panel-who">{item.kind==='permission'?'Wants your permission':'Asks you'}</p>
    {item.kind==='permission'
     ?<p className="dv-home-panel-question">It can’t start until you check where it may work.</p>
     :question&&<p className="dv-home-panel-question">{question}</p>}
    <div className="dv-row-actions">
     {item.kind==='permission'
      ?<button type="button" className="dv-btn dv-btn-primary" onClick={event=>openTask(event.currentTarget)}>Review the scope</button>
      // A question with choices is answered in the thread's tray; anything else is answered by writing below.
      :worker?<button type="button" className="dv-btn dv-btn-primary" onClick={()=>onOpenThread(channelId,true)}>Answer in the thread</button>
      :<button type="button" className="dv-btn dv-btn-primary" onClick={()=>field.current?.focus()}>Answer</button>}
    </div>
   </div>}
   {running&&<div className="dv-home-panel-live">
    <StatusGlyph state="running"/><span className="dv-home-panel-step">{step??'Working'}</span>
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={stopping} aria-label={stopping?`Pausing ${item.name}`:`Pause ${item.name}`} onClick={()=>onStop(channelId)}>{stopping?'Pausing…':'Pause'}</button>
   </div>}
   {/* Under a question the bar shows only for a result that still waits for review: a finished answer's "Ask for
       changes" beside Answer would be two ways to say the same thing. */}
   {decision&&task&&item.kind!=='permission'&&(!asks||task.state==='ready_for_review')&&<div className="dv-decision dv-home-panel-decision" data-decision={decision.glyph} role="group" aria-label="Your decision">
    <div className="dv-decision-line"><StatusGlyph state={decision.glyph} accent={accent&&!!decision.accent}/>
     <p className="dv-what" role="status"><b>{decision.lead}</b>{decision.clause?` ${decision.clause}`:''}</p></div>
    <button ref={inspect} type="button" className="dv-btn dv-btn-quiet" onClick={event=>openTask(event.currentTarget)}>{decision.inspect}</button>
    <GuideActions task={task} projectId={projectId} channelId={channelId} detail={detail.data?.task.id===task.id?detail.data:undefined}
     onChanged={refresh} onCorrection={()=>openTask(inspect.current??document.body)}/>
   </div>}
   <FactList facts={facts} className="dv-home-panel-facts"/>
   {reply&&<div className="dv-home-panel-last">
    <p className="dv-caption">Last reply</p>
    <p className="dv-home-panel-reply">{reply}</p>
    <button type="button" className="dv-btn-quiet dv-home-panel-read" onClick={()=>onOpenThread(channelId)}>Read it in the thread</button>
   </div>}
   {tasks.isError&&<p className="dv-caption" role="alert">This thread’s record couldn’t be read just now. Open thread still works.</p>}
  </div>
  <div className="dv-composer dv-home-panel-composer">
   <label className="sr-only" htmlFor="dervo-home-panel-reply">Reply in {item.name}</label>
   <textarea id="dervo-home-panel-reply" ref={field} className="dv-field" rows={1} value={draft} placeholder={`Reply in ${item.name}`}
    onChange={event=>edit(event.target.value)}
    onKeyDown={event=>{
     if(event.key==='Escape'){event.preventDefault();event.currentTarget.blur();return;}
     if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing&&draft.trim()){event.preventDefault();void send();}
    }}/>
   {error&&<p className="dv-caption dv-home-failure" role="alert">{error}</p>}
   <div className="dv-foot">
    <span className="dv-hint">Goes to this thread, not Home</span>
    <button type="button" className="dv-btn-send" aria-label={`Send to ${item.name}`} disabled={!draft.trim()||sending} onClick={()=>void send()}><Arrow/></button>
   </div>
  </div>
  <footer className="dv-home-panel-foot">
   <button type="button" className="dv-home-panel-key" aria-label="Previous thread" onClick={()=>onStep(-1)}><kbd>↑</kbd></button>
   <button type="button" className="dv-home-panel-key" aria-label="Next thread" onClick={()=>onStep(1)}><kbd>↓</kbd></button>
   <span>next thread{position.of>1?` · ${position.at} of ${position.of}`:''}</span>
   <span className="dv-home-panel-notice" role="status">{notice}</span>
   <button type="button" className="dv-home-panel-key" onClick={onClose}><kbd>Esc</kbd><span>back to Home</span></button>
  </footer>
 </section>;
}
