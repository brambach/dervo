import {SettingsSectionFrame} from './SettingsSectionFrame';
import {ReviewerSetup} from './ReviewerSetup';
import {useCallback,useEffect,useId,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Button} from '@/shared/ui/button';
import {Input} from '@/shared/ui/input';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription} from '@/shared/ui/dialog';
import {local,createAgentConversation,CLAUDE_EFFORTS,type CollabAgent,type CollabPacket,type CollabPacketDetail,type CollabPolicy,type CollabResult,type CollabReview,type CollabReviewSetting,type Conversation,type HarnessBoundary} from './api';
import {PACKET_ACTIVE,PACKET_LABELS,NO_REVIEWER,agentLabel,decodeSnapshot,packetLabel,roleLabel,when,type ReviewOverrideValue} from './collaboration';
import {providerAuthor} from './providerLabel';
import './HelperReading.css';
import {t} from '@/i18n';

/** Cross-channel collaboration UI. Every row and every field here is read from the host's own packet records; nothing is
 * inferred from message text, nothing animates on its own, and nothing in these views accepts a result: acceptance stays
 * with the source task's recorded policy. */
export type Navigate={openChannel:(channelId:string)=>void;openTask:(channelId:string,taskId:string,trigger?:HTMLElement)=>void;inspectPacket:(packetId:string,trigger?:HTMLElement)=>void};
export type TaskLike={id:string;state:string;origin?:string;agent?:string;role?:string;packet_id?:string;collaboration?:{mode:string;agents:string[];depth:number}};
type Profile={id:string;provider:string;identity:string;category:string};
export function channelName(conversations:Conversation[]|undefined,id:string):string {return conversations?.find(c=>c.id===id)?.name??id;}
const linkClass='rounded px-1 underline decoration-dotted hover:bg-muted';

/** Under a source task: one row per handoff the host actually recorded for it (the whole chain for an owner, its own asks for a worker). */
export function PacketRows({task,projectId,channelId,conversations,navigate}:{task:TaskLike;projectId:string;channelId:string;conversations?:Conversation[];navigate:Navigate}) {
  const packets=useQuery({queryKey:['collab-packets',projectId,channelId,task.id,task.state],queryFn:()=>local<CollabPacket[]>('collab_packets',{projectId,channelId,taskId:task.id}),
    refetchInterval:q=>task.state==='working'||(q.state.data??[]).some(p=>PACKET_ACTIVE.has(p.status))?700:false});
  if(packets.error)return <p role="alert" className="text-destructive">{t('Handoffs could not be read: {error}',{error:String(packets.error)})}</p>;
  const rows=packets.data??[];
  if(!rows.length)return null;
  return <ul className="mt-1 space-y-1" data-testid="collab-packets" aria-label={t('Help this task asked for')}>{rows.map(p=>{
    const dest=p.destination;
    return <li key={p.id} data-testid="collab-packet" data-packet-id={p.id} data-status={p.status} data-role={p.role??''} className="flex flex-wrap items-center gap-x-2">
      <span className="text-foreground">#{channelName(conversations,p.source.thread_id)} → {agentLabel(p.agent)}{p.role?` (${roleLabel(p.role)}, ${providerAuthor(dest?.provider)})`:''}</span>
      {dest?<button className={linkClass} onClick={()=>navigate.openChannel(dest.thread_id)}>in #{channelName(conversations,dest.thread_id)}</button>:<span>nowhere: refused before a helper existed</span>}
      <span>· {p.reason}</span>
      <span role="status" data-testid="collab-packet-status">· {packetLabel(p)}</span>
      <button className={linkClass} onClick={event=>navigate.inspectPacket(p.id,event.currentTarget)}>{t('Request details')}</button>
      {dest&&p.child_task_id&&<button className={linkClass} onClick={event=>navigate.openTask(dest.thread_id,p.child_task_id as string,event.currentTarget)}>{t('Open helper task')}</button>}
    </li>;})}</ul>;
}

/** Under a helper task in the destination conversation: who asked, from where, why, and the exact text this helper received. */
export function HelperSource({task,projectId,channelId,conversations,navigate,full}:{task:TaskLike;projectId:string;channelId:string;conversations?:Conversation[];navigate:Navigate;full?:boolean}) {
  const packet=useQuery({queryKey:['collab-packet',projectId,channelId,task.packet_id],enabled:!!task.packet_id,queryFn:()=>local<CollabPacketDetail>('collab_packet',{projectId,channelId,packetId:task.packet_id}),
    refetchInterval:task.state==='working'?1500:false});
  if(!task.packet_id)return null;
  if(packet.error)return <p role="alert" className="text-destructive">{t('The request behind this helper could not be read: {error}',{error:String(packet.error)})}</p>;
  const p=packet.data;
  if(!p)return <p>{t('Reading the request this helper received.')}</p>;
  const source=channelName(conversations,p.source.thread_id);
  return <div className="dervo-helper-source" data-testid="helper-source">
    <p className="dervo-helper-kicker">{roleLabel(p.role)} · {providerAuthor(p.source.provider)}</p>
    <p className="dervo-helper-source-purpose">{p.reason}</p>
    <p>{t('From')} <button className={linkClass} onClick={()=>navigate.openChannel(p.source.thread_id)}>#{source}</button></p>
    <div className="dervo-helper-links"><Button size="sm" variant="outline" onClick={event=>navigate.openTask(p.source.thread_id,p.owner_task_id,event.currentTarget)}>{t('Open owner task')}</Button><Button size="sm" variant="ghost" onClick={event=>navigate.inspectPacket(p.id,event.currentTarget)}>{t('Request details')}</Button></div>
    <p role="status">{packetLabel(p)}. {t('Any required result approval happens on the source task.')}</p>
    {full&&<details><summary>{t('Request this helper actually received')} · sha256 {p.payload_sha256}</summary><pre data-testid="helper-received" className="whitespace-pre-wrap break-all rounded bg-muted p-2">{p.payload_text}</pre></details>}
  </div>;
}

function ResultView({result,label}:{result:CollabResult;label:string}) {
  return <div data-testid="packet-result" className="space-y-1">
    <p className="font-medium">{label} · {t('helper ended {state}',{state:result.child_state})}{result.attempt?` · ${t('attempt {number}',{number:result.attempt})} ${result.state??''}`:` · ${t('no file attempt')}`} · {result.verified?t('verified by host tests, exit 0'):t('not verified by host tests')}</p>
    {result.test&&<p>{t('Tests: exit {status}',{status:result.test.exit_status??t('not run')})}{result.test.stopped?` · ${t('stopped: {why}',{why:result.test.stopped})}`:''}</p>}
    <pre data-testid="packet-summary" className="dervo-helper-answer">{result.summary??t('No text summary came back.')}</pre>
    {result.diff&&<details><summary>{t('Helper diff (the host clips this record at 8000 characters; the helper task holds the full attempt)')}</summary><pre className="whitespace-pre-wrap break-all rounded bg-muted p-2">{result.diff}</pre></details>}
    {result.after&&Object.keys(result.after).length>0&&<details><summary>{t('Snapshot hashes after the helper\'s verified attempt')}</summary><pre className="whitespace-pre-wrap break-all">{JSON.stringify(result.after,null,2)}</pre></details>}
    {result.test?.stderr&&<details><summary>{t('Test stderr (clipped by the host at 2000 characters)')}</summary><pre className="whitespace-pre-wrap break-all rounded bg-muted p-2">{result.test.stderr}</pre></details>}
  </div>;
}

/** The exact packet: sent text and snapshot bytes as recorded, the result separately, provenance, and warnings. Plain text only. */
export function PacketInspector({packetId,projectId,channelId,conversations,navigate,onOpenChange,onCloseAutoFocus}:{packetId:string;projectId:string;channelId:string;conversations?:Conversation[];navigate:Navigate;onOpenChange:(v:boolean)=>void;onCloseAutoFocus?:(event:Event)=>void}) {
  const query=useQuery({queryKey:['collab-packet',projectId,channelId,packetId],queryFn:()=>local<CollabPacketDetail>('collab_packet',{projectId,channelId,packetId}),
    refetchInterval:q=>PACKET_ACTIVE.has(q.state.data?.status??'')?1000:false});
  const [copied,setCopied]=useState('');
  const [page,setPage]=useState<'overview'|'context'|'record'>('overview');
  useEffect(()=>{setPage('overview');setCopied('');},[packetId]);
  const p=query.data;
  const body=useRef<HTMLDivElement>(null),heading=useRef<HTMLHeadingElement>(null);
  async function copy(text:string){try{await navigator.clipboard.writeText(text);setCopied(t('Copied to the clipboard. Copying accepts nothing.'));}catch(e){setCopied(String(e));}}
  return <Dialog open onOpenChange={onOpenChange}><DialogContent className="dervo-helper-inspector" onCloseAutoFocus={onCloseAutoFocus} onOpenAutoFocus={event=>{event.preventDefault();heading.current?.focus();}}>
    <DialogHeader className="dervo-helper-heading"><div><DialogTitle ref={heading} tabIndex={-1}>{p?t('Help from {agent}',{agent:agentLabel(p.agent)}):t('Helper request')}</DialogTitle><DialogDescription>{t('Read the answer, follow its source or inspect what Dervo sent. Opening or copying this accepts nothing.')}</DialogDescription></div></DialogHeader>
    <nav className="dervo-helper-nav" aria-label={t('Helper request views')}>{(['overview','context','record'] as const).map(value=><button key={value} aria-pressed={page===value} onClick={event=>{setPage(value);event.currentTarget.focus();if(body.current)body.current.scrollTop=0;}}>{value==='overview'?t('Overview'):value==='context'?t('Sent context'):t('Activity record')}</button>)}</nav>
    <div ref={body} className="dervo-helper-body" data-testid="packet-inspector">
      {query.error&&<p role="alert" className="text-destructive">{t('The helper record couldn’t be read. {error}',{error:String(query.error)})}</p>}
      {!p&&!query.error&&<p role="status">{t('Reading the helper request…')}</p>}
      {p&&<>
        <p className="dervo-helper-status" role="status" data-testid="packet-status">{PACKET_LABELS[p.status]??p.status}</p>
        {p.stale&&<p role="alert" data-testid="stale-warning" className="dervo-helper-warning">{t('Stale review: {files} changed since this review was sent. The review describes the saved snapshot, not the current files.',{files:p.changed_since.join(', ')})}</p>}
        {p.refusal&&<p role="alert" className="dervo-helper-warning">{t('Refused: {why}',{why:p.refusal})}</p>}
        {p.error&&!p.refusal&&<p role="alert" className="dervo-helper-warning">{p.error}</p>}
        {page==='overview'&&<>
          <section className="dervo-helper-return"><h3>{t('The answer')}</h3>
            {p.result&&<ResultView result={p.result} label={t('Result')}/>}
            {p.late_result&&<ResultView result={p.late_result} label={t('Late result, kept on the record and never delivered to the owner')}/>}
            {!p.result&&!p.late_result&&<p>{PACKET_ACTIVE.has(p.status)?t('An answer has not returned yet.'):t('No answer is recorded for this request.')}</p>}
          </section>
          <section className="dervo-helper-purpose"><p className="dervo-helper-kicker">{t('What was asked')}</p><p>{p.instructions||p.reason||t('No request was recorded.')}</p><p className="dervo-helper-role">{roleLabel(p.role)}{p.destination?` · ${providerAuthor(p.destination.provider)} · ${p.destination.model}`:''}</p></section>
          <div className="dervo-helper-links"><Button variant="outline" onClick={event=>navigate.openTask(p.source.thread_id,p.owner_task_id,event.currentTarget)}>{t('Open source task')}</Button>{p.destination&&p.child_task_id&&<Button variant="ghost" onClick={event=>navigate.openTask(p.destination!.thread_id,p.child_task_id!,event.currentTarget)}>{t('Open helper task')}</Button>}</div>
          <p className="dervo-helper-note">{t('Any required approval happens on the source task.')}</p>
        </>}
        {page==='context'&&<div className="dervo-helper-context"><p className="dervo-helper-note">{t('Exact text and file snapshots sent to this helper. These are saved records, not current file contents.')}</p>
        <section className="space-y-1"><h3 className="font-medium">{t('Files sent (path · sha256 at send time)')}</h3>{p.files.length?<ul>{p.files.map(f=><li key={f.path} className="break-all font-mono text-xs">{f.path} · {f.sha256??t('not sent')}</li>)}</ul>:<p>{t('No files.')}</p>}</section>
        <section className="space-y-1"><h3 className="font-medium">{t('Sent text, exact')} · {t('{length} characters',{length:p.payload_text?.length??0})} · sha256 {p.payload_sha256??t('none')}</h3>
          {p.payload_text!==null?<><pre data-testid="packet-payload" className="whitespace-pre-wrap break-all rounded bg-muted p-2">{p.payload_text}</pre><Button size="sm" variant="outline" onClick={()=>void copy(p.payload_text as string)}>{t('Copy sent text')}</Button></>:<p>{t('Nothing was sent.')}</p>}</section>
        <section className="space-y-1"><h3 className="font-medium">{t('Snapshot contents sent')}</h3>
          {Object.entries(p.snapshots).map(([path,s])=>{const text=decodeSnapshot(s.bytes_b64);return <details key={path}><summary className="break-all font-mono text-xs">{path} · {s.sha256}</summary>{text===null?<p>{t('Not UTF-8 text: {length} base64 characters recorded, not shown.',{length:s.bytes_b64.length})}</p>:<pre data-testid="packet-snapshot" data-path={path} className="whitespace-pre-wrap break-all rounded bg-muted p-2">{text}</pre>}</details>;})}
          {!Object.keys(p.snapshots).length&&<p>{t('No snapshot was recorded.')}</p>}</section>

        </div>}
        {page==='record'&&<div className="dervo-helper-record">
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
          <dt>{t('Status')}</dt><dd >{packetLabel(p)}</dd>
          <dt>{t('From')}</dt><dd>#{channelName(conversations,p.source.thread_id)} · {providerAuthor(p.source.provider)} · {p.source.model} · {t('task {id}',{id:p.source.task_id})} · <button className={linkClass} onClick={event=>navigate.openTask(p.source.thread_id,p.owner_task_id,event.currentTarget)}>{t('Open source task')}</button></dd>
          <dt>{t('To')}</dt><dd>{p.destination?<>{agentLabel(p.agent)} ({roleLabel(p.role)}) in #{channelName(conversations,p.destination.thread_id)} · {providerAuthor(p.destination.provider)} · {p.destination.model}{p.child_task_id&&<> · <button className={linkClass} onClick={event=>navigate.openTask(p.destination?.thread_id as string,p.child_task_id as string,event.currentTarget)}>{t('Open helper task')}</button></>}</>:t('{agent}: refused before any helper existed',{agent:agentLabel(p.agent)})}</dd>
          <dt>{t('Purpose')}</dt><dd className="whitespace-pre-wrap break-all">{p.reason}</dd>
          <dt>{t('Owner instructions')}</dt><dd className="whitespace-pre-wrap break-all">{p.instructions||'none'}</dd>
          <dt>{t('Chain')}</dt><dd>{t('depth {depth}',{depth:p.depth})}{p.parent_packet_id?` · ${t('nested under packet {id}',{id:p.parent_packet_id})}`:` · ${t('asked by the owner')}`} · {t('root task {id}',{id:p.root_task_id})}</dd>
          <dt>{t('Times')}</dt><dd>{t('created {when}',{when:when(p.created_at)})}{p.queued_at?` · ${t('queued {when}',{when:when(p.queued_at)})}`:''}{p.started_at?` · ${t('started {when}',{when:when(p.started_at)})}`:''}{p.returned_at?` · ${t('ended {when}',{when:when(p.returned_at)})}`:''}</dd>
        </dl>

          <p>{t('Request {id}',{id:p.id})}</p><p>{t('Payload and result text is shown as plain text. Reading this record doesn\'t approve a result.')}</p>
        </div>}
        {copied&&<p role="status">{copied}</p>}
      </>}
    </div></DialogContent></Dialog>;
}

type Binding={provider:string;identity:string;model:string;profile_id:string;visibility:string};
const ROLES=[['reviewer',t('Reviewer: read-only review of the files sent')],['worker',t('Worker: may change the listed files and run the host tests')]] as const;
/** Project collaboration settings: the policy (off by default, explicit enable) and the named agents it may allowlist. */
/** The per-request review, in the model picker beside the check: project default, off, or on with the configured
 * reviewer. It only shapes the next coding request's payload; the host validates the binding again before anything is
 * recorded or sent. Without a reviewer, Set up a reviewer opens the project's Agents and review settings. */
export function ReviewOverride({review,value,onChange,onSetup}:{review:CollabReview|null|undefined;value:ReviewOverrideValue;onChange:(v:ReviewOverrideValue)=>void;onSetup?:()=>void}) {
  const on=!!review?.enabled&&!!review.reviewer;
  const skipped=on&&!!review?.skipped_here;
  const configured=on&&review?`${agentLabel(review.reviewer??'')} · ${providerAuthor(review.provider??undefined)} · ${review.model} · ${t('effort {level}',{level:review.effort??''})}`:'';
  const projectDefault=skipped?t('skipped here'):on?'on':'off';
  return <div className="dervo-model-effort" data-testid="review-override">
    <p className="dervo-model-heading">{t('Review')} <span>{on?configured:t('no reviewer configured')}</span></p>
    <div className="dervo-model-levels" role="radiogroup" aria-label={t('Independent review for this request')}>
      <button type="button" role="radio" aria-checked={value==='default'} onClick={()=>onChange('default')}>{t('Project default ({value})',{value:projectDefault})}</button>
      <button type="button" role="radio" aria-checked={value==='off'} onClick={()=>onChange('off')}>{t('Off')}</button>
      <button type="button" role="radio" aria-checked={value==='on'} disabled={!on||skipped} title={skipped?t('Unavailable for this account'):on?t('On: {configured}',{configured}):t('Unavailable: no reviewer configured')} onClick={()=>onChange('on')}>{t('On')}</button>
    </div>
    {skipped&&value==='default'&&<p role="note" className="dervo-model-note">{review!.skipped_here}</p>}
    {value==='on'&&on&&<p role="status" className="dervo-model-note">{t('Reviews this request with {reviewer}. That is extra usage on the reviewer’s account.',{reviewer:configured})} {review!.notice}</p>}
    {value==='on'&&!on&&<p role="alert" className="dervo-model-note">{NO_REVIEWER}</p>}
    {value!=='on'&&!on&&(onSetup?<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dervo-model-setup" onClick={onSetup}>{t('Set up a reviewer')}</button>:<details className="dervo-model-note"><summary>{t('Set up a reviewer')}</summary><p role="note">{t('Choose a reviewer in Project settings, then Agents and review. Review runs separately from your approval of the result.')}</p></details>)}
  </div>;
}
/** The review's label for the model chip when it isn't the project default. */
export function reviewChoiceLabel(value:ReviewOverrideValue):string|null {
  return value==='default'?null:value==='on'?t('Review on'):t('Review off');
}
/** The composer-side owner of the per-request override. One value per current conversation: a conversation switch resets it
 * to the project default; ``settle(sentFrom)`` is called when a send completes and resets it only when that conversation is
 * still the current one, returning whether it was. An old in-flight completion therefore never wipes a newer conversation's
 * choice (or, through the caller, its reply target). A failed send leaves the value with the draft for the retry. */
export function useReviewOverride(channelId:string):{value:ReviewOverrideValue;setValue:(v:ReviewOverrideValue)=>void;settle:(sentFrom:string)=>boolean} {
  const [value,setValue]=useState<ReviewOverrideValue>('default');
  const current=useRef(channelId);
  useEffect(()=>{current.current=channelId;setValue('default');},[channelId]);
  const settle=useCallback((sentFrom:string)=>{const same=current.current===sentFrom;if(same)setValue('default');return same;},[]);
  return {value,setValue,settle};
}
/** Settings display name. Setup-created reviewers carry a hashed name, so they read "Reviewer", with a short id only when two
 * would read the same. Saved records, permission checks and aria labels keep the real name. */
function agentName(name:string,all:{name:string}[]):string {
  const label=agentLabel(name);
  return label==='Reviewer'&&all.filter(o=>agentLabel(o.name)==='Reviewer').length>1?t('Reviewer {id}',{id:name.slice(7,13)}):label;
}
// The long aria labels are the controls' existing accessible names; the title and text are what the card shows.
const MODES=[['off',t('Off'),t('The coding model never asks another agent.'),t('Off: the coding model never asks another agent')],['mentions',t('Mentions'),t('Only agents you name with @ in your message.'),t('Mentions: only an agent you name with @ in your message')],['automatic',t('Automatic'),t('Any allowed agent, when the coding model judges it useful.'),t('Automatic: any allowed agent, when the coding model judges it useful')]] as const;
const settingsCard='space-y-4 rounded-xl border bg-background p-4';
const pill='shrink-0 rounded-full px-2 py-0.5 text-2xs font-medium';
const fieldGrid='grid grid-cols-[max-content_minmax(0,1fr)] items-center gap-x-4 gap-y-3';
const selectClass='w-full min-w-0 rounded-md border bg-background px-2 py-1.5';
type Place='review'|'agents'|'policy';
export function CollaborationSettings({embedded=false,open,onOpenChange,projectId,channelId,channelName:ownerChannel,binding,conversations,profiles,boundaries,refresh}:{embedded?:boolean;open:boolean;onOpenChange:(v:boolean)=>void;projectId:string;channelId:string;channelName:string;binding:Binding;conversations:Conversation[];profiles:Profile[];boundaries:HarnessBoundary[];refresh:()=>Promise<unknown>}) {
  const titleRef=useRef<HTMLHeadingElement>(null);
  const id=useId();
  const policy=useQuery({queryKey:['collab-policy',projectId,channelId],enabled:open,queryFn:()=>local<CollabPolicy>('collab_policy',{projectId,channelId})});
  const agents=useQuery({queryKey:['collab-agents'],enabled:open,queryFn:()=>local<CollabAgent[]>('collab_agents',{})});
  const [mode,setMode]=useState<'off'|'mentions'|'automatic'>('off'),[allowed,setAllowed]=useState<string[]>([]),[channels,setChannels]=useState<string[]>([]);
  const [parallel,setParallel]=useState(3),[depth,setDepth]=useState(1),[calls,setCalls]=useState(6),[seeded,setSeeded]=useState(false);
  const [name,setName]=useState(''),[role,setRole]=useState<'reviewer'|'worker'>('reviewer'),[profileId,setProfileId]=useState(''),[channel,setChannel]=useState(''),[newName,setNewName]=useState(''),[model,setModel]=useState(''),[instructions,setInstructions]=useState('');
  const [editing,setEditing]=useState(''),[confirming,setConfirming]=useState('');
  // One message line at a time, shown under the card whose button caused it rather than at the bottom of the page.
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[place,setPlace]=useState<Place>('review');
  // Independent review: an explicit reviewer binding and effort. The key is sent only when the user touched it or it is
  // already on, so an untouched save keeps the older request shape exactly.
  const [reviewOn,setReviewOn]=useState(false),[reviewer,setReviewer]=useState(''),[effort,setEffort]=useState(''),[reviewTouched,setReviewTouched]=useState(false);
  const data=policy.data;
  useEffect(()=>{if(data&&!seeded){setMode(data.mode);setAllowed(data.agents);setChannels(data.channels.length?data.channels:[channelId]);setParallel(data.limits.max_parallel);setDepth(data.limits.max_depth);setCalls(data.limits.max_calls);
    if(data.review){setReviewOn(data.review.enabled);setReviewer(data.review.reviewer??'');setEffort(data.review.effort??'');}setSeeded(true);}},[data,seeded,channelId]);
  const personal=profiles.filter(p=>p.category==='personal');
  const linked=(id:string)=>id===binding.profile_id||boundaries.some(b=>b.profiles.includes(id)&&b.profiles.includes(binding.profile_id));
  const shared=conversations.filter(c=>c.visibility==='shared');
  const bound=shared.filter(c=>c.profile_id===profileId&&c.id!==channelId);
  const projectAgents=(agents.data??[]).filter(a=>a.project_id===projectId);
  const toggle=(list:string[],set:(v:string[])=>void,value:string,on:boolean)=>set(on?[...new Set([...list,value])]:list.filter(v=>v!==value));
  async function act(where:Place,fn:()=>Promise<void>,done:string){setPlace(where);setBusy(true);setError('');setNotice('');try{await fn();setNotice(done);}catch(e){setError(String(e));}finally{setBusy(false);}}
  const feedback=(where:Place)=>place!==where?null:error?<p role="alert" className="text-xs leading-relaxed text-destructive">{error}</p>:notice?<p role="status" className="text-xs leading-relaxed text-muted-foreground">{notice}</p>:null;
  const reviewers=projectAgents.filter(a=>a.role==='reviewer');
  const reviewerAgent=reviewers.find(a=>a.name===reviewer);
  const saved=data?.review;
  const reviewState=saved?(saved.enabled?`${t('Independent review: on')} · ${agentLabel(saved.reviewer!)} · ${providerAuthor(saved.provider??undefined)} · ${saved.model} · ${t('effort {level}',{level:saved.effort??''})}`:t('Independent review: off')):'';
  const reviewDirty=reviewOn!==!!saved?.enabled||(reviewOn&&(reviewer!==(saved?.reviewer??'')||effort!==(saved?.effort??'')));
  const reviewReady=!reviewOn||(!!reviewer&&!!effort);
  // Both cards save through the one policy endpoint. Each card sends its own edits and the host's saved values for the other
  // card, so one Save never commits the other card's unsaved edits, and the policy Save never sends an incomplete review.
  const savedPolicy=()=>({mode:data!.mode,agents:data!.agents,channels:data!.channels.length?data!.channels:[channelId],maxParallel:data!.limits.max_parallel,maxDepth:data!.limits.max_depth,maxCalls:data!.limits.max_calls});
  const saveReview=()=>act('review',async()=>{
    if(!data)throw new Error(t('The project policy has not loaded yet. Nothing was saved.'));
    if(!reviewReady)throw new Error(t('Choose a reviewer and an effort first. Nothing was saved.'));
    const review:CollabReviewSetting={enabled:reviewOn,reviewer:reviewOn?reviewer:null,effort:reviewOn?effort:null};
    await local('collab_policy_set',{projectId,channelId,...savedPolicy(),review});await policy.refetch();setReviewTouched(false);await refresh();
  },t('Review saved. It applies to new requests only.'));
  const savePolicy=()=>act('policy',async()=>{
    const review:CollabReviewSetting|undefined=saved?.enabled?{enabled:true,reviewer:saved.reviewer??null,effort:saved.effort??null}:undefined;
    await local('collab_policy_set',{projectId,channelId,mode,agents:allowed,channels,maxParallel:parallel,maxDepth:depth,maxCalls:calls,...(review?{review}:{})});await policy.refetch();await refresh();
  },reviewDirty?t('Policy saved. Your unsaved review changes are still unsaved; use Save review for those.'):t('Policy saved. It applies to new requests only.'));
  const agentEditor=useRef<HTMLDetailsElement>(null);
  function openEditor(){const el=agentEditor.current;if(!el)return;el.open=true;requestAnimationFrame(()=>{el.scrollIntoView({block:'nearest'});document.getElementById(`${id}-name`)?.focus();});}
  function clearAgent(){setEditing('');setName('');setRole('reviewer');setProfileId('');setChannel('');setNewName('');setModel('');setInstructions('');}
  function closeEditor(){clearAgent();if(agentEditor.current)agentEditor.current.open=false;}
  function startNew(){setConfirming('');clearAgent();openEditor();}
  function edit(a:CollabAgent){setConfirming('');setEditing(a.name);setName(a.name);setRole(a.role);setProfileId(a.profile_id);setChannel(a.channel_id);setInstructions(a.instructions);setModel(a.model);openEditor();}
  const saveAgent=()=>act('agents',async()=>{
    let target=channel;
    if(channel==='__new__'){const made=await createAgentConversation(projectId,profileId,model,newName);target=made.id;}
    await local('collab_agent_save',{name,profileId,channelId:target,projectId,role,instructions});
    await agents.refetch();await refresh();await policy.refetch();closeEditor();
  },t('Agent saved. To let the coding model ask it, allow it under Asking for help.'));
  const deleteAgent=(agent:string)=>act('agents',async()=>{await local('collab_agent_delete',{name:agent});setConfirming('');if(editing===agent)closeEditor();await agents.refetch();await policy.refetch();await refresh();},t('Deleted @{agent}. Its recorded requests are kept.',{agent}));
  const canSaveAgent=!!name&&!!profileId&&(channel==='__new__'?!!newName.trim()&&!!model.trim():!!channel);
  const missing=!name?t('Add a name to save.'):!profileId?t('Choose an account to save.'):channel==='__new__'?t('Name the new conversation and enter its model to save.'):t('Choose a conversation to save.');
  return <SettingsSectionFrame embedded={embedded} open={open} onOpenChange={onOpenChange} title={t('Agents and review')} description={t('Choose who can help and whether a second agent reviews the work.')} titleRef={titleRef}>
    <div className={embedded?"space-y-5 text-sm":"min-h-0 space-y-5 overflow-y-auto pr-2 text-sm"}>
      <p className="truncate text-xs text-muted-foreground" title={ownerChannel}>{t('For #{channel}',{channel:ownerChannel})} · {providerAuthor(binding.provider)}</p>

      <section className={settingsCard} aria-label={t('Review')}>
        <fieldset className="space-y-4" disabled={!seeded||busy} aria-label={t('Independent review')}><legend className="sr-only">{t('Independent review')}</legend>
          <div className="flex items-start gap-3">
            <input id={`${id}-review`} type="checkbox" className="mt-1" aria-label={t('Independent review of each finished request')} checked={reviewOn} onChange={e=>{setReviewOn(e.target.checked);setReviewTouched(true);}}/>
            <label htmlFor={`${id}-review`} className="min-w-0 flex-1 cursor-pointer"><span className="block font-semibold">{t('Independent review')}</span><span className="block text-xs leading-relaxed text-muted-foreground">{t('A second agent reviews each finished request under its own account, which is extra usage there. Your approval of the result stays separate.')}</span></label>
            {saved&&<span role="status" data-testid="collab-review-state" title={reviewState} className={`${pill} ${saved.enabled?'bg-primary/10 text-primary':'bg-muted text-muted-foreground'}`}><span className="sr-only">{reviewState}</span><span aria-hidden="true">{saved.enabled?t('On'):t('Off')}</span></span>}
          </div>
          {reviewOn&&<div className={`${fieldGrid} pl-7`}>
            <label htmlFor={`${id}-reviewer`} className="text-muted-foreground">{t('Reviewer')}</label>
            <select id={`${id}-reviewer`} aria-label={t('Reviewer agent')} className={selectClass} value={reviewer} onChange={e=>{setReviewer(e.target.value);setEffort('');setReviewTouched(true);}}><option value="">{reviewers.length?t('Choose a read-only reviewer agent'):t('No reviewer agent yet')}</option>{reviewers.map(a=><option key={a.name} value={a.name}>{agentName(a.name,reviewers)} · {providerAuthor(a.provider)} · {a.model}</option>)}</select>
            <label htmlFor={`${id}-effort`} className="text-muted-foreground">{t('Effort')}</label>
            {/* The effort control depends on the reviewer's provider, so it waits for a reviewer instead of guessing one. */}
            {!reviewerAgent
              ?<select id={`${id}-effort`} aria-label={t('Reviewer effort')} className={selectClass} value={effort} disabled><option value="">{t('Choose a reviewer first')}</option>{effort&&<option value={effort}>{effort}</option>}</select>
              :reviewerAgent.provider==='claude'
                ?<select id={`${id}-effort`} aria-label={t('Reviewer effort')} className={selectClass} value={effort} onChange={e=>{setEffort(e.target.value);setReviewTouched(true);}}><option value="">{t('Choose an effort level')}</option>{CLAUDE_EFFORTS.map(v=><option key={v} value={v}>{v}</option>)}</select>
                :<Input id={`${id}-effort`} aria-label={t('Reviewer effort')} placeholder={t('for example low')} value={effort} onChange={e=>{setEffort(e.target.value);setReviewTouched(true);}}/>}
            {reviewerAgent&&reviewerAgent.provider!=='claude'&&<p className="col-start-2 text-xs leading-relaxed text-muted-foreground">{t('Use the exact effort the Codex model catalog lists for this model. Dervo checks the catalog before sending and never substitutes.')}</p>}
          </div>}
          <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">{t('How a review runs')}</summary><p className="mt-2 leading-relaxed">{saved?.notice??t('Independent review runs a second agent under its own account after each finished request: one review, at most one correction, one final check. That is extra usage on that account.')} {t('Its verdict is recorded on the task; your review of the result stays separate.')}</p></details>
        </fieldset>
        {data&&!saved?.enabled&&<ReviewerSetup key={`${projectId}:${channelId}:${binding.profile_id}:${binding.model}`} projectId={projectId} channelId={channelId} binding={binding} onSaved={async()=>{const updated=await policy.refetch();if(updated.error)throw updated.error;const review=updated.data?.review;if(review){setReviewOn(review.enabled);setReviewer(review.reviewer??'');setEffort(review.effort??'');setReviewTouched(false);}await agents.refetch();await refresh();}}/>}
        {(reviewTouched||saved?.enabled)&&<div className="flex flex-wrap items-center gap-3"><Button disabled={busy||!seeded||!reviewDirty||!reviewReady} onClick={()=>void saveReview()}>{t('Save review')}</Button>{reviewDirty&&<span className="text-xs text-muted-foreground">{reviewReady?t('Unsaved changes'):t('Choose a reviewer and an effort to save.')}</span>}</div>}
        {feedback('review')}
      </section>

      <section className={settingsCard} aria-label={t('Named agents')}>
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1"><h3 className="font-semibold">{t('Named agents')}</h3><p className="text-xs leading-relaxed text-muted-foreground">{t('Each agent works in its own conversation under its own account.')}</p></div>
          <Button size="sm" variant="outline" disabled={busy} onClick={startNew}>{t('Add agent')}</Button>
        </div>
        {agents.error&&<div role="alert" className="space-y-2 text-destructive"><p>{t('Agents could not be loaded: {error}',{error:String(agents.error)})}</p><Button variant="outline" onClick={()=>void agents.refetch()}>{t('Try loading agents again')}</Button></div>}
        {!agents.data&&!agents.error&&<p role="status" className="text-muted-foreground">{t('Loading the project\'s agents.')}</p>}
        {agents.data&&!projectAgents.length&&<p className="rounded-lg border border-dashed p-4 text-center text-xs leading-relaxed text-muted-foreground">{t('No agents yet. Add a reviewer for Independent review, or a worker that can take on part of a request.')}</p>}
        {projectAgents.length>0&&<ul className="space-y-2">{projectAgents.map(a=>{
          const label=agentName(a.name,projectAgents);
          const identity=profiles.find(p=>p.id===a.profile_id)?.identity??a.profile_id;
          const meta=`${roleLabel(a.role)} · ${providerAuthor(a.provider)} · ${a.model} · #${channelName(conversations,a.channel_id)}`;
          return <li key={a.name} data-testid="collab-agent" className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
            <span aria-hidden="true" className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-xs font-semibold uppercase text-muted-foreground">{label.replace(/^@/,'').slice(0,1)}</span>
            <div className="min-w-0 flex-1 basis-48">
              <p className="flex flex-wrap items-center gap-2"><span className="font-medium" title={`@${a.name}`}>{label}</span><span className={`${pill} ${a.state==='ready'?'bg-primary/10 text-primary':'bg-destructive/10 text-destructive'}`}>{a.state==='ready'?t('Ready'):t('Unusable')}</span></p>
              <p className="truncate text-xs text-muted-foreground" title={meta}>{meta}</p>
              <p className="truncate text-xs text-muted-foreground" title={identity}>{identity}</p>
              {a.state!=='ready'&&<p className="text-xs leading-relaxed text-destructive">{a.detail}</p>}
            </div>
            {confirming===a.name
              ?<div className="flex flex-wrap items-center gap-2"><span className="text-xs">{t('Delete {name}? Its recorded requests are kept.',{name:label})}</span><Button size="sm" variant="outline" className="text-destructive" disabled={busy} aria-label={t('Confirm delete @{name}',{name:a.name})} onClick={()=>void deleteAgent(a.name)}>{t('Delete')}</Button><Button size="sm" variant="ghost" onClick={()=>setConfirming('')}>{t('Keep')}</Button></div>
              :<div className="flex items-center gap-1"><Button size="sm" variant="ghost" aria-label={t('Edit @{name}',{name:a.name})} onClick={()=>edit(a)}>{t('Edit')}</Button><Button size="sm" variant="ghost" disabled={busy} aria-label={t('Delete @{name}',{name:a.name})} onClick={()=>setConfirming(a.name)}>{t('Delete')}</Button></div>}
          </li>;})}</ul>}
        <details ref={agentEditor} className="rounded-lg border p-4"><summary className="cursor-pointer font-medium">{editing?t('Edit {name}',{name:agentName(editing,projectAgents)}):t('Add an agent')}</summary>
          <form className="mt-4 space-y-4" onSubmit={e=>{e.preventDefault();void saveAgent();}}>
            <div className={fieldGrid}>
              <label htmlFor={`${id}-name`} className="text-muted-foreground">{t('Name')}</label>
              <Input id={`${id}-name`} aria-label={t('Agent name')} placeholder={t('lowercase letters, digits, hyphens')} value={name} onChange={e=>setName(e.target.value.toLowerCase())}/>
              <label htmlFor={`${id}-role`} className="text-muted-foreground">{t('Role')}</label>
              <select id={`${id}-role`} aria-label={t('Agent role')} className={selectClass} value={role} onChange={e=>setRole(e.target.value as 'reviewer'|'worker')}>{ROLES.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>
              <label htmlFor={`${id}-account`} className="text-muted-foreground">{t('Account')}</label>
              <select id={`${id}-account`} aria-label={t('Agent account')} className={selectClass} value={profileId} onChange={e=>{setProfileId(e.target.value);setChannel('');}}><option value="">{t('Choose an existing personal account')}</option>{personal.map(p=><option key={p.id} value={p.id}>{providerAuthor(p.provider)} · {p.identity}{linked(p.id)?'':` · ${t('not linked to this conversation’s account')}`}</option>)}</select>
              {profileId&&!linked(profileId)&&<p className="col-start-2 text-xs leading-relaxed text-warning">{t('This account isn’t linked to #{channel}’s account. Link the two in Settings, on Connected accounts, under Help between your accounts, first. Linking is explicit by account and grants no file access by itself.',{channel:ownerChannel})}</p>}
              <label htmlFor={`${id}-conversation`} className="text-muted-foreground">{t('Conversation')}</label>
              <select id={`${id}-conversation`} aria-label={t('Agent conversation')} className={selectClass} value={channel} disabled={!profileId} onChange={e=>setChannel(e.target.value)}><option value="">{profileId?t('Choose a shared conversation bound to this account'):t('Choose an account first')}</option>{bound.map(c=><option key={c.id} value={c.id}>#{c.name} · {c.model}</option>)}<option value="__new__">{t('Create a new conversation bound to this account')}</option></select>
              {channel==='__new__'&&<>
                <label htmlFor={`${id}-new-name`} className="text-muted-foreground">{t('New conversation')}</label>
                <Input id={`${id}-new-name`} aria-label={t('New conversation name')} placeholder={t('Conversation name')} value={newName} onChange={e=>setNewName(e.target.value)}/>
                <label htmlFor={`${id}-model`} className="text-muted-foreground">{t('Model')}</label>
                <Input id={`${id}-model`} aria-label={t('Agent model')} placeholder={t('Exact model ID for this account')} value={model} onChange={e=>setModel(e.target.value)}/>
                <p className="col-start-2 text-xs leading-relaxed text-muted-foreground">{t('Only this new conversation is bound to the chosen account and model. The project\'s own binding, other conversations and sign-in stay as they are.')}</p>
              </>}
              <label htmlFor={`${id}-instructions`} className="self-start pt-2 text-muted-foreground">{t('Instructions')}</label>
              <div className="space-y-1"><textarea id={`${id}-instructions`} aria-label={t('Agent instructions')} rows={4} className="w-full rounded-md border bg-background p-2" maxLength={2000} value={instructions} onChange={e=>setInstructions(e.target.value)}/><p className="text-right text-2xs text-muted-foreground">{t('Text only')} · {instructions.length} / 2000</p></div>
            </div>
            <div className="flex flex-wrap items-center gap-3"><Button type="submit" disabled={busy||!canSaveAgent}>{t('Save agent')}</Button><Button type="button" variant="ghost" onClick={closeEditor}>{t('Cancel')}</Button>{!canSaveAgent&&<span className="text-xs text-muted-foreground">{missing}</span>}</div>
          </form>
        </details>
        {feedback('agents')}
      </section>

      <section className={settingsCard} aria-label={t('Collaboration policy')}>
        <div><h3 className="font-semibold">{t('Asking for help')}</h3><p role="status" className="text-xs leading-relaxed text-muted-foreground" data-testid="collab-policy-state">{data?t('Now: {state} · {detail}',{state:data.state,detail:data.detail}):policy.error?String(policy.error):t('Reading the project policy.')}</p></div>
        <fieldset className="space-y-2" disabled={!seeded||busy}><legend className="mb-2 font-medium">{t('When the coding model may ask')}</legend>
          <div className="grid gap-2 sm:grid-cols-3">{MODES.map(([value,title,text,label])=><label key={value} className={`flex cursor-pointer items-start gap-2 rounded-lg border p-3 ${mode===value?'border-primary/40 bg-primary/5':'hover:bg-muted/30'}`}><input type="radio" className="mt-1" name={`${id}-mode`} aria-label={label} checked={mode===value} onChange={()=>setMode(value)}/><span className="min-w-0"><span className="block font-medium">{title}</span><span className="block text-xs leading-relaxed text-muted-foreground">{text}</span></span></label>)}</div>
        </fieldset>
        {mode!=='off'&&<fieldset className="space-y-2" disabled={!seeded||busy}><legend className="mb-2 font-medium">{t('Allowed agents')}</legend>
          {projectAgents.map(a=><label key={a.name} className="flex cursor-pointer items-center gap-3 rounded-lg border p-3 hover:bg-muted/30"><input type="checkbox" aria-label={t('Allow agent {name}',{name:a.name})} checked={allowed.includes(a.name)} onChange={e=>toggle(allowed,setAllowed,a.name,e.target.checked)}/><span className="min-w-0 flex-1"><span className="block font-medium">{agentName(a.name,projectAgents)}</span><span className="block truncate text-xs text-muted-foreground">{roleLabel(a.role)} · {providerAuthor(a.provider)} · #{channelName(conversations,a.channel_id)}{a.state!=='ready'?` · ${t('unusable: {why}',{why:a.detail??''})}`:''}</span></span></label>)}
          {agents.data&&!projectAgents.length&&<p className="text-xs text-muted-foreground">{t('No agents yet. Add one under Named agents.')}</p>}
        </fieldset>}
        <details className="rounded-lg border p-3"><summary className="cursor-pointer font-medium">{t('Conversations and limits')}</summary><div className="mt-4 space-y-4">
          <fieldset className="space-y-2" disabled={!seeded||busy}><legend className="mb-2 text-xs font-medium text-muted-foreground">{t('Conversations that may ask (shared only)')}</legend>
            {shared.map(c=><label key={c.id} className="flex items-center gap-2"><input type="checkbox" aria-label={t('Allow asking from {name}',{name:c.name})} checked={channels.includes(c.id)} onChange={e=>toggle(channels,setChannels,c.id,e.target.checked)}/> #{c.name}</label>)}
          </fieldset>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="space-y-1"><span className="block text-xs text-muted-foreground">{t('Agents at once (1 to 3, you included)')}</span><Input aria-label={t('Agents at once')} type="number" min={1} max={3} className="w-20" value={parallel} onChange={e=>setParallel(Number(e.target.value))}/></label>
            <label className="space-y-1"><span className="block text-xs text-muted-foreground">{t('Chain depth (1 to 2)')}</span><Input aria-label={t('Chain depth')} type="number" min={1} max={2} className="w-20" value={depth} onChange={e=>setDepth(Number(e.target.value))}/></label>
            <label className="space-y-1"><span className="block text-xs text-muted-foreground">{t('Asks per request (1 to 6)')}</span><Input aria-label={t('Asks per request')} type="number" min={1} max={6} className="w-20" value={calls} onChange={e=>setCalls(Number(e.target.value))}/></label>
          </div>
        </div></details>
        <div className="flex flex-wrap items-center gap-3"><Button disabled={busy||!seeded} onClick={()=>void savePolicy()}>{t('Save collaboration policy')}</Button><span className="text-xs text-muted-foreground">{t('Applies to new requests only.')}</span></div>
        {feedback('policy')}
      </section>

      <details className="text-xs leading-relaxed text-muted-foreground"><summary className="cursor-pointer">{t('How help and permissions work')}</summary><div className="mt-3 space-y-2"><p>{t('The owner is the conversation you send from: #{channel} · {provider} · {identity} (expected). Each helper uses its configured account and conversation. Dervo records the request and returned answer.',{channel:ownerChannel,provider:providerAuthor(binding.provider),identity:binding.identity})}</p><p>{t('Agent instructions don\'t grant tools or file access. Independent review and your result approval are separate project settings. Any required approval happens on the owner task.')}</p></div></details>
    </div></SettingsSectionFrame>;
}
