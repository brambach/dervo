import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {local} from './api';
import './HandoffConfirm.css';
import {formatDate,t,tr} from '@/i18n';

/** What coding_handoff_preview answers: the brief Continue would send, built by the host from the stopped run's
 * records. ``id`` is what Send passes back; ``snapshot`` is set when the run left files, so Send saves them first.
 * ``said`` is the end of what the stopped agent last said and ``steps`` its last few steps in words (the end of its
 * session, from Dervo's records; absent from a host before beta.16). Opening the confirm saves nothing. */
export type HandoffBrief={id:string;taskId:string;here:string;to:string;asked:string;files:string[];changes:Record<string,string>;
 cleared:string[];also:string[];added:number;removed:number;check:string;stopped:string|null;kind?:string|null;step:string|null;next:string|null;
 said?:string|null;steps?:string[];snapshot:{untracked:number}|null;full:boolean};
/** What coding_handoff_restore_preview answers: what Restore would put back and remove, or why it won't. */
export type RestorePlan={id:string;available:boolean;reason:string|null;put:string[];remove:string[];foreign:string[];at:number;here:string;to:string};
/** The snapshot a handoff left on the stopped task (suggest.snapshot). ``removed_at`` is set once Dervo's cleanup
 * removed it, a week after the task that carried on was settled; there's nothing to restore from then on. */
export type HandoffSnapshot={handoff_id:string;ref:string;commit:string;at:number;restored_at?:number|null;removed_at?:number|null};

const named=(items:string[],shown=3)=>items.length>shown?t('{items} and {count} more',{items:items.slice(0,shown).join(', '),count:items.length-shown}):items.join(', ');
/** A line of paths that may wrap after a folder's slash, never inside a file's name. */
const Paths=({text}:{text:string})=><>{text.split('/').map((part,index)=><span key={index}>{index?<>/<wbr/></>:null}{part}</span>)}</>;

/** The line under the title: what the next agent picks up, and that Send saves a snapshot first when there's work to save. */
export function handoffNote(brief:Pick<HandoffBrief,'here'|'to'|'snapshot'>):string{
 return brief.snapshot?t('{to} picks up from the files {here} left. Send saves a snapshot first, so you can go back.',{to:brief.to,here:brief.here})
  :t('{to} gets your request and where {here} stopped.',{to:brief.to,here:brief.here});
}
/** Files: the ones the run changed. */
export function filesLine(brief:Pick<HandoffBrief,'files'>):string|null{
 return brief.files.length?`${named(brief.files)}.`:null;
}
/** How many lines those changes are, in words. */
export function linesLine(brief:Pick<HandoffBrief,'added'|'removed'>):string{
 return t('{count} lines added, {removed} removed.',{count:brief.added,removed:brief.removed});
}
/** Stopped: why, in Dervo's words, and the step the agent reported, in its own. */
export function stoppedLine(brief:Pick<HandoffBrief,'here'|'kind'|'stopped'|'step'>):string{
 const why=brief.kind==='usage_limit'?t('At {here}’s usage limit.',{here:brief.here}):brief.kind==='rate_limit'?t('At {here}’s rate limit.',{here:brief.here}):brief.kind==='stopped'?t('You paused it.'):(brief.stopped||t('Stopped.'));
 return brief.step?`${why} ${t('It was on “{step}”.',{step:brief.step.replace(/[.]$/,'')})}`:why;
}
/** Send says so when the run it starts gets full access (security review, 23 Sep, H1). */
export function sendLabel(brief:Pick<HandoffBrief,'full'>|undefined):string{return brief?.full?t('Send with full access'):t('Send');}

/** The small confirm behind Continue in <provider>: title, one line, the brief as facts, Send and Cancel. Opening it asks
 * the host for the brief, which reads the folder and writes nothing; Send carries the id the host gave, and the host
 * saves the snapshot then, only if the folder is still what the confirm showed. */
export function HandoffConfirm({projectId,channelId,taskId,to,onSent,onCancel}:{projectId:string;channelId:string;taskId:string;to:string;onSent:()=>Promise<unknown>|void;onCancel:()=>void}){
 const brief=useQuery({queryKey:['handoff-preview',projectId,channelId,taskId],gcTime:0,staleTime:0,retry:false,
  queryFn:()=>local<HandoffBrief>('coding_handoff_preview',{projectId,channelId,taskId})});
 const [sending,setSending]=useState(false),[error,setError]=useState('');
 const cancel=useRef<HTMLButtonElement>(null);
 useEffect(()=>{cancel.current?.focus();},[]);
 async function send(){
  if(sending||!brief.data)return;
  setSending(true);setError('');
  try{await local('coding_retry_on',{projectId,channelId,taskId,handoffId:brief.data.id});await onSent();}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));setSending(false);}
 }
 const data=brief.data,problem=brief.error?(brief.error instanceof Error?brief.error.message:String(brief.error)):'';
 const files=data&&filesLine(data);
 return <div className="dv-carry" role="group" aria-label={t('Carry on in {to}',{to})} data-testid="handoff-confirm"
  onKeyDown={event=>{if(event.key==='Escape'&&!sending){event.preventDefault();event.stopPropagation();onCancel();}}}>
  <p className="dv-carry-title">{t('Carry on in {to}',{to})}</p>
  {data?<p className="dv-carry-note">{handoffNote(data)}</p>
   :problem?<p className="dv-carry-note dv-carry-error" role="alert">{problem}</p>
   :<p className="dv-carry-note" role="status">{t('Checking what it left…')}</p>}
  {data&&<dl className="dv-facts">
   <div className="dv-facts-row"><dt>{t('Asked')}</dt><dd>{data.asked}</dd></div>
   {(files||data.also.length>0)&&<div className="dv-facts-row"><dt>Files</dt><dd>{files?<Paths text={files}/>:t('None from this run.')}
    {files&&<span className="dv-carry-also">{linesLine(data)}</span>}
    {data.also.length>0&&<span className="dv-carry-also">{tr('Also uncommitted, not from this run: {paths}.',{paths:<Paths text={named(data.also)}/>})}</span>}</dd></div>}
   <div className="dv-facts-row"><dt>{t('Last check')}</dt><dd>{data.check}</dd></div>
   <div className="dv-facts-row"><dt>{t('Stopped')}</dt><dd>{stoppedLine(data)}</dd></div>
   {data.said&&<div className="dv-facts-row"><dt>{t('Said last')}</dt><dd>“{data.said}”</dd></div>}
   {/* Its last steps go to the next agent too, so they're shown here before Send (behaviour review N2, 25 Sep). */}
   {!!data.steps?.length&&<div className="dv-facts-row"><dt>{t('Last steps')}</dt><dd>{data.steps.join('; ')}.</dd></div>}
   {data.next&&<div className="dv-facts-row"><dt>{t('Next')}</dt><dd>“{data.next.replace(/[.]$/,'')}.”</dd></div>}
  </dl>}
  {error&&<p className="dv-carry-note dv-carry-error" role="alert">{error}</p>}
  <div className="dv-row-actions">
   {data&&<button type="button" className="dv-btn dv-btn-primary" disabled={sending} onClick={()=>void send()}>{sending?t('Sending…'):sendLabel(data)}</button>}
   <button ref={cancel} type="button" className="dv-btn dv-btn-quiet" disabled={sending} onClick={onCancel}>{t('Cancel')}</button>
  </div>
 </div>;
}

/** After a handoff that saved a snapshot: "Restore the snapshot", which asks first. The host says what goes back and
 * what goes, or which files it won't overwrite because nobody carried them on; nothing changes until Restore. */
export function RestoreSnapshot({projectId,channelId,taskId,snapshot,refresh}:{projectId:string;channelId:string;taskId:string;snapshot:HandoffSnapshot;refresh:()=>Promise<unknown>}){
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[done,setDone]=useState('');
 const plan=useQuery({queryKey:['handoff-restore',projectId,channelId,taskId],enabled:open,gcTime:0,staleTime:0,retry:false,
  queryFn:()=>local<RestorePlan>('coding_handoff_restore_preview',{projectId,channelId,taskId})});
 async function restore(){
  if(busy||!plan.data)return;
  setBusy(true);setError('');
  try{const result=await local<{detail:string}>('coding_handoff_restore',{projectId,channelId,taskId,restoreId:plan.data.id});setDone(result.detail);setOpen(false);await refresh();}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));await plan.refetch();}
  finally{setBusy(false);}
 }
 if(!open)return <div className="dv-carry-restore">
  <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={()=>{setDone('');setError('');setOpen(true);}}>{t('Restore the snapshot')}</button>
  {done?<span className="dv-caption" role="status">{done}</span>
   :snapshot.restored_at?<span className="dv-caption">{t('Put back at {time}.',{time:formatDate(snapshot.restored_at*1000,{hour:'numeric',minute:'2-digit'})})}</span>:null}
 </div>;
 const data=plan.data,problem=plan.error?(plan.error instanceof Error?plan.error.message:String(plan.error)):'';
 const moves=data?[data.put.length?data.put.length===1?t('{files} goes back to how {here} left it.',{files:named(data.put),here:data.here}):t('{files} go back to how {here} left them.',{files:named(data.put),here:data.here}):'',
  data.remove.length?data.remove.length===1?t('{files} wasn’t there then, so it goes.',{files:named(data.remove)}):t('{files} weren’t there then, so they go.',{files:named(data.remove)}):''].filter(Boolean).join(' '):'';
 return <div className="dv-carry" role="group" aria-label={t('Restore the snapshot')} data-testid="handoff-restore"
  onKeyDown={event=>{if(event.key==='Escape'&&!busy){event.preventDefault();event.stopPropagation();setOpen(false);}}}>
  <p className="dv-carry-title">{t('Restore the snapshot?')}</p>
  {!data&&!problem&&<p className="dv-carry-note" role="status">{t('Checking the folder…')}</p>}
  {problem&&<p className="dv-carry-note dv-carry-error" role="alert">{problem}</p>}
  {data&&!data.available&&<p className="dv-carry-note" role="note">{data.reason}</p>}
  {data?.available&&<p className="dv-carry-note">{moves} {t('Dervo saves the folder as it is now first.')}</p>}
  {error&&<p className="dv-carry-note dv-carry-error" role="alert">{error}</p>}
  <div className="dv-row-actions">
   {data?.available&&<button type="button" className="dv-btn dv-btn-primary" disabled={busy} onClick={()=>void restore()}>{busy?t('Restoring…'):t('Restore')}</button>}
   <button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>setOpen(false)}>{data?.available?t('Cancel'):t('Close')}</button>
  </div>
 </div>;
}
