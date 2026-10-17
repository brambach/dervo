import {Fragment,useEffect,useRef,useState,type ReactNode} from 'react';
import {providerAuthor} from './providerLabel';
import {formatNumber,t} from '@/i18n';

/** A note's mark (beta.16, b16/memory): it tells its reader to run something, is written to agents, or repeats a line
 * from a file the agent read. It was saved anyway and goes to later chats as a record, never as a rule. */
export type NoteFlag={kind:'command'|'agents'|'file'|'instruction'|'decision';path?:string};
export type MemoryEntry={id:string;text:string;created_at:number;updated_at:number;provider?:string;
 source?:{thread_id?:string;task_id?:string;kind?:string;profile?:string;visibility?:string};missing?:string[];aging?:boolean;
 flag?:NoteFlag|null;flag_kept?:NoteFlag;kind?:'lesson';evidence?:string;edited_by?:string;edited_at?:number;previous?:string;supersedes?:string;
 retired_reason?:'merged'|'out_of_date'|'oldest'|'expired';merged_into?:string;dropped_at?:number;
 /** beta.17 (dd #22): pinned by you, saved again by agents this many times, and when a snapshot note retires. */
 pinned_at?:number;confirmed_count?:number;expires_at?:number};
/** Two notes that may say different things about one subject, older first (``project_memory.lookalikes``). */
export type Lookalike={older:string;newer:string;why:'number'|'words'};
export type ProjectMemoryView={enabled:boolean;reflect?:boolean;entries:MemoryEntry[];used_chars:number;max_chars:number;max_entries:number;
 used_tokens?:number;max_tokens?:number;seen_at?:number;retired?:MemoryEntry[];lookalikes?:Lookalike[];edited?:{id:string;previous:string};
 /** beta.17 (dd #22): notes that can be sent (marked ones wait apart), pins, and what one request gets. */
 counted?:number;marked?:number;max_marked?:number;pinned?:number;max_pinned?:number;sent_entries?:number;sent_chars?:number;purge_days?:number};

export function ago(seconds:number,now=Date.now()/1000){
 const d=Math.max(0,now-seconds);
 if(d<3600)return `${Math.max(1,Math.floor(d/60))} min ago`;
 if(d<86400)return `${Math.floor(d/3600)} h ago`;
 return `${Math.floor(d/86400)} d ago`;
}
const MONTHS=[t('Jan'),t('Feb'),t('Mar'),t('Apr'),t('May'),t('Jun'),t('Jul'),t('Aug'),t('Sep'),t('Oct'),t('Nov'),t('Dec')];
/** When a note was kept, the way the page says it: "12 min ago" and "2 h ago" for the last few hours, then "Today",
 * "Yesterday" and "23 Sep". */
export function keptWhen(seconds:number,now=Date.now()/1000):string{
 const d=Math.max(0,now-seconds);
 if(d<6*3600)return ago(seconds,now);
 const day=(s:number)=>{const at=new Date(s*1000);return new Date(at.getFullYear(),at.getMonth(),at.getDate()).getTime();};
 const days=Math.round((day(now)-day(seconds))/86400000);
 if(days<=0)return t('Today');
 if(days===1)return t('Yesterday');
 const at=new Date(seconds*1000);
 return `${at.getDate()} ${MONTHS[at.getMonth()]}`;
}

/** Who wrote a note, in words: an agent by name, Dervo itself for the notes it keeps from checked results, or you. */
export function noteAuthor(entry:MemoryEntry):string{
 if(entry.source?.kind==='dervo_check'||entry.provider==='dervo')return t('Noted by Dervo from a checked result');
 // A rule moved to notes is yours, like an edit (beta.17.1, #41 step 8).
 if(entry.source?.kind==='rule')return t('Moved from your rules');
 if(entry.edited_by)return t('Edited by you');
 return entry.provider?providerAuthor(entry.provider):t('Saved');
}
/** Why a note might be out of date, from what Dervo can see now. Only ever a reason; nothing is removed for you. */
export function noteDoubt(entry:MemoryEntry):string|null{
 const gone=entry.missing??[];
 if(gone.length)return `Mentions ${gone.join(' and ')}, which ${gone.length===1?'isn’t':'aren’t'} in the project now.`;
 if(entry.aging)return t('Nobody has updated this in over six weeks.');
 return null;
}
/** A note's mark in two or three words, for the line under it and on a reply. */
export function flagLabel(flag:NoteFlag|null|undefined):string{
 if(!flag)return '';
 if(flag.kind==='agents')return t('Written to agents');
 if(flag.kind==='file')return `Copied from ${flag.path?.split('/').pop()||'a file'}`;
 // Security review S1 (Bryce, 26 Sep): a standing instruction, or what a named person decided, waits for Keep too.
 if(flag.kind==='instruction')return t('Reads like a rule');
 if(flag.kind==='decision')return t('Says who decided');
 return t('Has a command');
}
/** How full the notes are, for the group head: "2 of 80", and how full once it's getting there. Marked notes wait
 * apart and don't count (beta.17, dd #22). */
export function notesCount(view:ProjectMemoryView):string{
 const counted=view.counted??view.entries.length;
 const percent=Math.min(100,Math.round(view.used_chars/Math.max(1,view.max_chars)*100));
 const count=t('{counted} of {max_entries}',{counted,max_entries:view.max_entries});
 if(counted>=view.max_entries)return t('{count} · full',{count});
 return percent>=75?t('{count} · {percent}% full',{count,percent}):count;
}
/** What memory costs, for the page head. Since beta.17 (dd #22) Dervo keeps more than one request gets: "About 1,100
 * of 1,500 tokens of notes go with each request", or, once they don't all fit, which ones go. Since beta.17.1 (#41, A)
 * the rest go as an index line and a continuing chat gets only the notes that changed. It speaks of notes only: rules
 * and the repository's files still go in full with every message until A's step 4. */
export function tokensLine(view:ProjectMemoryView):string{
 const max=view.max_tokens??Math.floor((view.sent_chars??view.max_chars)/4);
 const sentChars=view.sent_chars??view.max_chars,counted=view.counted??view.entries.length;
 if(counted>(view.sent_entries??Infinity)||view.used_chars>sentChars)
  return t('Up to {max} tokens of notes go with each request: pinned notes and the ones that best match it. The rest are listed by their first words, for the agent to look up when its task touches one.',{max:formatNumber(max)});
 const used=view.used_tokens??Math.round(view.used_chars/4);
 // Nothing sent yet read as "About 0 of 1,500" (polish, 8 Oct): say that none go, and how many can.
 if(used<=0)return t('No notes go with requests yet. Up to {max} tokens can.',{max:formatNumber(max)});
 const round=(n:number)=>n>=1000?Math.round(n/100)*100:n;
 return t('About {used} of {max} tokens of notes go with each request. A continuing chat gets only the notes that changed.',{used:formatNumber(round(used)),max:formatNumber(max)});
}
/** The day a snapshot note retires, for the line under it: "Retires 16 Oct unless pinned". */
export function expiryLine(entry:MemoryEntry,now=Date.now()/1000):string|null{
 if(!entry.expires_at||entry.pinned_at)return null;
 const at=new Date(entry.expires_at*1000);
 return entry.expires_at<=now?t('Retires now unless pinned'):t('Retires {day} unless pinned',{day:`${at.getDate()} ${MONTHS[at.getMonth()]}`});
}
/** How many other chats saved a note again, which keeps it longer when memory fills. Chats, not saves, so one run
 * can't make a note last by saving it over and over. */
export function confirmedLine(entry:MemoryEntry):string|null{
 const n=entry.confirmed_count??0;
 return n<1?null:n===1?t('Saved again in 1 other chat'):t('Saved again in {n} other chats',{n});
}
/** Whether a note says where something is right now: the same test the backend uses to expire notes
 * (``project_memory.snapshot``), so Knowledge can ask about rules that say the same kind of thing. */
const SNAPSHOT_WORDS=/\b(?:as of|right now|currently|at the moment|for now|so far|as things stand|(?:is|are|was|became|is now) the (?:newest|latest|current))\b/i;
const SNAPSHOT_COMMIT=/(?:^|[^\w-])at\s+`?(?=[0-9a-f]{0,39}[0-9])[0-9a-f]{7,40}`?(?![\w-])/;
export function snapshot(text:string):boolean{return SNAPSHOT_WORDS.test(text)||SNAPSHOT_COMMIT.test(text);}
/** Kept after you last looked, by anyone but you. */
export function isNew(entry:MemoryEntry,seenAt:number|undefined):boolean{
 return !entry.edited_by&&typeof seenAt==='number'&&entry.created_at>seenAt;
}
/** Why Dervo retired a note, in the words the Retired list uses. */
export function retiredWhy(entry:MemoryEntry):string{
 if(entry.retired_reason==='merged')return t('Another note says the same with newer details');
 if(entry.retired_reason==='expired')return t('It said where something was, over two weeks ago');
 if(entry.retired_reason==='out_of_date')return entry.missing?.length?`It names ${entry.missing.join(' and ')}, which the project no longer has`:t('Nobody had updated it in six weeks');
 return t('The note written longest ago');
}
/** A note's words with `code` in backticks shown as code, the way agents write commands and paths. */
export function noteText(text:string):ReactNode{
 const parts=text.split(/(`[^`\n]+`)/);
 if(parts.length===1)return text;
 return parts.map((part,index)=>part.startsWith('`')&&part.endsWith('`')&&part.length>2?<code key={index}>{part.slice(1,-1)}</code>:<Fragment key={index}>{part}</Fragment>);
}

export const NOTE_ICON=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="dv-ico"><path d="M3 3.5h10v9H3z"/><path d="M5.5 6.5h5M5.5 9h3"/></svg>;
const SHOWN=5;
const newestFirst=(a:MemoryEntry,b:MemoryEntry)=>b.updated_at-a.updated_at||b.created_at-a.created_at;

export type FactActions={
 edit:(entry:MemoryEntry,text:string)=>Promise<unknown>;
 remove:(entry:MemoryEntry)=>Promise<unknown>;
 restore:(entry:MemoryEntry)=>Promise<unknown>;
 keep:(entry:MemoryEntry)=>Promise<unknown>;
 pin:(entry:MemoryEntry,pinned:boolean)=>Promise<unknown>;
 promote:(entry:MemoryEntry,text?:string)=>Promise<unknown>;
 keepNewer:(older:MemoryEntry,newer:MemoryEntry)=>Promise<unknown>;
 keepBoth:(older:MemoryEntry,newer:MemoryEntry)=>Promise<unknown>;
};
type Props={view:ProjectMemoryView|null;error?:string;onRetry?:()=>void;seenAt?:number;actions:FactActions;
 threadName?:(threadId:string)=>string|undefined;onOpenSource?:(threadId:string,taskId:string)=>void};

/** "Facts and decisions" on the Knowledge page (b16-memory/knowledge.png): what agents keep as they work, newest first.
 * Each note shows who kept it, in which chat and when, and New until you've looked. Edit and Delete show on hover and
 * keyboard focus. Edit keeps the old wording in the note's history; Delete leaves one line with Undo where the note was.
 * Edit also offers Make it a rule, the one way a note becomes a rule: your click, in your words.
 * A marked note says what it is and that agents don't get it until you keep it (security review S1, 25 Sep), with Keep to clear the mark. Two notes that
 * may disagree get one Out of date? card. Past five, the rest fold behind "N older". Notes Dervo retired are listed last
 * with Bring back. Since beta.17 (dd #22) Pin keeps a note for good and sends it with every request, pinned notes come
 * first, and a note says when it retires if it's a snapshot and how often agents saved it again. */
export function FactsSection({view,error='',onRetry,seenAt,actions,threadName=()=>undefined,onOpenSource}:Props){
 const [busy,setBusy]=useState('');
 const [errors,setErrors]=useState<Record<string,{text:string;retry:()=>void}>>({});
 const [editing,setEditing]=useState<{id:string;text:string}|null>(null);
 const [deleted,setDeleted]=useState<Record<string,{entry:MemoryEntry;undone?:boolean}>>({});
 const [older,setOlder]=useState(false);
 const [shown,setShown]=useState<string[]>([]);
 const field=useRef<HTMLTextAreaElement>(null);
 const rows=useRef(new Map<string,HTMLElement>());
 useEffect(()=>{if(editing)requestAnimationFrame(()=>{const f=field.current;if(f){f.focus();f.setSelectionRange(f.value.length,f.value.length);}});},[editing?.id]);
 // Every change settles to whether it worked. A failure says why under the note, with Try again.
 async function run(key:string,fn:()=>Promise<unknown>,failed:string):Promise<boolean>{
  setBusy(key);setErrors(current=>{const next={...current};delete next[key];return next;});
  try{await fn();return true;}
  catch(failure){setErrors(current=>({...current,[key]:{text:`${failed} ${failure instanceof Error?failure.message:String(failure)}`,retry:()=>void run(key,fn,failed)}}));return false;}
  finally{setBusy('');}
 }
 const problem=(key:string,className='dv-row-line dv-kn-alert')=>errors[key]&&<p className={className} role="alert">{errors[key].text} <button type="button" className="dv-kn-link" onClick={errors[key].retry}>{t('Try again')}</button></p>;
 const head=(count:ReactNode)=><div className="dv-sec-head"><h2 className="dv-title" id="dervo-knowledge-facts">{t('Facts and decisions')}</h2>{count}
  <span className="dv-caption">{t('Background, never instructions')}</span></div>;
 if(!view&&error)return <section className="dv-sec" aria-labelledby="dervo-knowledge-facts" data-testid="project-memory">{head(null)}
  <p className="dv-kn-line" role="alert">Agents’ notes didn’t load. {error}{onRetry&&<> <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onRetry}>{t('Try again')}</button></>}</p></section>;
 if(!view)return <section className="dv-sec" aria-labelledby="dervo-knowledge-facts" data-testid="project-memory">{head(null)}<p className="dv-kn-line" role="status">{t('Reading what agents kept…')}</p></section>;
 const pinnedFirst=(a:MemoryEntry,b:MemoryEntry)=>Number(!!b.pinned_at)-Number(!!a.pinned_at)||newestFirst(a,b);
 const live=[...view.entries].sort(pinnedFirst);
 const ids=new Set(live.map(entry=>entry.id));
 // A note you just deleted keeps its place as one line with Undo until you leave the page.
 const gone=Object.values(deleted).filter(item=>!ids.has(item.entry.id)&&!item.undone).map(item=>item.entry);
 const all=[...live,...gone].sort(pinnedFirst);
 const byId=new Map(view.entries.map(entry=>[entry.id,entry]));
 const pair=(view.lookalikes??[]).map(p=>({older:byId.get(p.older),newer:byId.get(p.newer),why:p.why})).find(p=>p.older&&p.newer) as {older:MemoryEntry;newer:MemoryEntry;why:string}|undefined;
 const first=older?all:all.slice(0,SHOWN),rest=older?[]:all.slice(SHOWN);
 const retired=view.retired??[];

 function source(entry:MemoryEntry):ReactNode{
  const thread=entry.source?.thread_id,name=thread?threadName(thread):undefined,task=entry.source?.task_id;
  const who=noteAuthor(entry);
  if(!name)return who;
  return <>{who} in {onOpenSource&&task?<button type="button" className="dv-kn-link" onClick={()=>onOpenSource(thread!,task)}>{name}</button>:name}</>;
 }
 function show(ids:string[]){
  setOlder(true);setShown(ids);
  requestAnimationFrame(()=>rows.current.get(ids[0])?.scrollIntoView({block:'center'}));
 }
 function row(entry:MemoryEntry):ReactNode{
  if(!ids.has(entry.id)){
   const words=entry.text.length>40?`${entry.text.slice(0,entry.text.lastIndexOf(' ',40)>20?entry.text.lastIndexOf(' ',40):40)}…`:entry.text;
   const key=`restore:${entry.id}`;
   return <p key={entry.id} className="dv-activity dv-kn-gone" role="status">
    <span className="dv-activity-text">Deleted “{words}” Later chats won’t get it.</span>
    <button type="button" className="dv-activity-action" disabled={!!busy} onClick={()=>void run(key,async()=>{await actions.restore(entry);setDeleted(current=>({...current,[entry.id]:{entry,undone:true}}));},t('Couldn’t bring it back.'))}>{t('Undo')}</button>
    {problem(key,'dv-kn-alert')}
   </p>;
  }
  const open=editing?.id===entry.id,fresh=isNew(entry,seenAt),doubt=noteDoubt(entry),flag=entry.flag;
  const expiry=expiryLine(entry),again=confirmedLine(entry),own=entry.source?.kind==='dervo_check';
  const key=entry.id;
  const lit=shown.includes(entry.id);
  const ref=(node:HTMLElement|null)=>{if(node)rows.current.set(entry.id,node);else rows.current.delete(entry.id);};
  if(open)return <form key={entry.id} ref={ref} className="dv-row dv-open dv-kn-editing" aria-label={t('Edit this note')} data-testid="note-edit"
   onSubmit={event=>{event.preventDefault();const text=editing.text.trim();if(!text||busy)return;void run(key,()=>actions.edit(entry,text),t('Couldn’t save it.')).then(ok=>{if(ok)setEditing(null);});}}
   onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();setEditing(null);}else if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();event.currentTarget.requestSubmit();}}}>
   {NOTE_ICON}
   <div>
    <textarea ref={field} aria-label={t('Note text')} value={editing.text} maxLength={400} rows={2} onChange={event=>setEditing({id:entry.id,text:event.target.value})}/>
    {problem(key)}
    <div className="dv-row-actions">
     <button type="submit" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy||!editing.text.trim()}>{busy===key?t('Saving…'):t('Save')}</button>
     <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setEditing(null)}>{t('Cancel')}</button>
     {/* Promote sits in Edit, not on hover: the mock draws Edit and Delete there, and a third button squeezes the note
         at 854 px. The rule takes the words in the box, so you can tidy them first. */}
     <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Make a rule from note: {text}',{text:entry.text})} disabled={!!busy||!editing.text.trim()}
      onClick={()=>{const text=editing.text.trim();void run(key,()=>actions.promote(entry,text),t('Couldn’t make it a rule.')).then(ok=>{if(ok)setEditing(null);});}}>{t('Make it a rule')}</button>
     <span className="dv-caption">{t('The old wording stays in its history')}</span>
    </div>
   </div>
   <div className="dv-row-end">{fresh&&<span className="dv-kn-new">{t('New')}</span>}</div>
  </form>;
  return <div key={entry.id} ref={ref} className={`dv-row dv-kn-note${lit?' dv-kn-lit':''}`} data-testid="note-row">
   {NOTE_ICON}
   <div>
    <div className="dv-row-name dv-kn-note-name">{noteText(entry.text)}</div>
    <div className="dv-row-line">
     {flag&&<><b className="dv-kn-flag">{flagLabel(flag)}</b> · </>}
     {entry.pinned_at&&<><b className="dv-kn-pinned">{t('Pinned')}</b> · </>}
     {entry.kind==='lesson'&&!flag&&<>{t('Lesson ·')} </>}
     {source(entry)} · {keptWhen(entry.edited_at??entry.updated_at)}
     {flag&&<> {t('· agents don’t get it until you keep it')}</>}
     {again&&<> · {again}</>}
     {expiry&&<span data-testid="memory-expiry"> · {expiry}</span>}
     {doubt&&<span data-testid="memory-doubt"> · {doubt}</span>}
    </div>
    {problem(key)}
   </div>
   <div className="dv-row-end">
    {fresh&&<span className="dv-kn-new">{t('New')}</span>}
    <span className="dv-kn-acts">
     {flag&&<button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Keep note: {text}',{text:entry.text})} disabled={!!busy} onClick={()=>void run(key,()=>actions.keep(entry),t('Couldn’t keep it.'))}>{t('Keep')}</button>}
     {!flag&&!own&&<button type="button" className="dv-btn dv-btn-quiet" aria-label={entry.pinned_at?t('Unpin note: {text}',{text:entry.text}):t('Pin note: {text}',{text:entry.text})} aria-pressed={!!entry.pinned_at} disabled={!!busy}
      onClick={()=>void run(key,()=>actions.pin(entry,!entry.pinned_at),entry.pinned_at?t('Couldn’t unpin it.'):t('Couldn’t pin it.'))}>{entry.pinned_at?t('Unpin'):t('Pin')}</button>}
     <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Edit note: {text}',{text:entry.text})} disabled={!!busy||entry.source?.kind==='dervo_check'} onClick={()=>setEditing({id:entry.id,text:entry.text})}>{t('Edit')}</button>
     <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Delete note: {text}',{text:entry.text})} disabled={!!busy} onClick={()=>void run(key,async()=>{await actions.remove(entry);setDeleted(current=>({...current,[entry.id]:{entry}}));},t('Couldn’t delete it.'))}>{t('Delete')}</button>
    </span>
   </div>
  </div>;
 }
 const card=pair&&<div className="dv-kn-dup" data-testid="out-of-date" role="group" aria-label={t('Out of date?')}>
  <p><b>Out of date?</b> Two notes may say different things{pair.why==='number'?', with different numbers':''}.</p>
  <p>“{pair.older.text}” ({keptWhen(pair.older.updated_at)}) and “{pair.newer.text}” ({keptWhen(pair.newer.updated_at)}).</p>
  {problem('pair','dv-kn-alert')}
  <div className="dv-row-actions">
   <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dv-kn-pill" disabled={!!busy} onClick={()=>void run('pair',()=>actions.keepNewer(pair.older,pair.newer),t('Couldn’t change it.'))}>{t('Keep the newer one')}</button>
   <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void run('pair',()=>actions.keepBoth(pair.older,pair.newer),t('Couldn’t change it.'))}>{t('Keep both')}</button>
   <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>show([pair.older.id,pair.newer.id])}>{t('Show both')}</button>
  </div>
 </div>;
 return <section className="dv-sec" aria-labelledby="dervo-knowledge-facts" data-testid="project-memory">
  {head(<span className="dv-kn-count" data-testid="project-memory-usage">{view.entries.length||''}</span>)}
  {!view.enabled&&<p className="dv-kn-line" role="status">{t('Off. Agents don’t see or save notes, and chats don’t hear about each other. Saved notes stay here.')}</p>}
  {view.enabled&&!all.length&&<p className="dv-kn-line">{t('Nothing kept yet. Facts and decisions arrive here as agents learn about this project.')}</p>}
  {all.length>0&&<div className="dv-sheet">
   {first.map(row)}
   {card}
   {rest.length>0&&<button type="button" className="dv-kn-older" aria-expanded={false} onClick={()=>setOlder(true)}>{rest.length} older</button>}
  </div>}
  {retired.length>0&&<details className="dv-disclose dv-kn-retired"><summary>{t('Retired · {count}',{count:retired.length})}<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg></summary>
   <p>{t('Dervo keeps up to {max} notes. When that’s full it retires one instead of losing it: an older copy first, then one that looks out of date, then the oldest. A note that says where something is right now retires after two weeks. Notes you edited or pinned stay. Agents can still find retired notes, and they’re deleted after {days} days.',{max:view.max_entries,days:view.purge_days??90})}</p>
   <div className="dv-sheet">{retired.map(entry=>{const key=`bring:${entry.id}`;return <div key={entry.id} className="dv-row" data-testid="retired-row">{NOTE_ICON}
    <div><div className="dv-row-name dv-kn-note-name">{noteText(entry.text)}</div><div className="dv-row-line">{retiredWhy(entry)} · retired {keptWhen(entry.dropped_at??entry.updated_at)}</div>
     {problem(key)}</div>
    <div className="dv-row-end"><button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void run(key,()=>actions.restore(entry),t('Couldn’t bring it back.'))}>{t('Bring back')}</button></div>
   </div>;})}</div>
  </details>}
 </section>;
}
