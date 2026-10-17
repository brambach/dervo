import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';
import {ChevronRight,Lock,MoreHorizontal,RotateCcw,Trash2} from 'lucide-react';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {StatusGlyph} from './StatusGlyph';
import {local} from './api';
import {restoreConversation,type Freed} from './ConversationLifecycle';
import {archivedGroups,quietFor,sizeLabel,type ArchivedThread,type TidyDays} from './tidy';
import './ConversationLifecycle.css';
import './ArchivedFold.css';

/** The Archived fold at the foot of a project's threads (design b16-lifecycle, archived-open). It groups by why, so you
 * can tell what Dervo put away from what you did: each tidy ("Tidied today, quiet 14 days or more", each thread with how
 * long it had been quiet), then "Archived by you" with the day. Opening a thread from here brings it back (Bryce, 25
 * Sep). Restore on hover brings it back without opening it, and ⋯ has Restore and Delete…, as the fold always had. */
/** After See them opens the fold, bring its rows into view, not just its heading: at the foot of a long sidebar they
 * opened below the window (design review S5, 25 Sep). The whole fold if it fits, otherwise from its heading down. */
export function revealArchived(details:HTMLElement|null){
 if(!details?.scrollIntoView)return;
 details.scrollIntoView({block:'end'});
 const summary=details.querySelector<HTMLElement>('summary');
 let scroller=details.parentElement;
 while(scroller&&!/(auto|scroll)/.test(getComputedStyle(scroller).overflowY))scroller=scroller.parentElement;
 if(summary&&scroller&&summary.getBoundingClientRect().top<scroller.getBoundingClientRect().top)summary.scrollIntoView({block:'start'});
}

export function ArchivedFold({projectId,open,onOpenChange,days,onOpen,onRestored,onDelete,onDeleteAll}:{projectId:string;open:boolean;onOpenChange:(open:boolean)=>void;
 days:TidyDays;onOpen:(conversation:ArchivedThread)=>void;onRestored:(conversation:ArchivedThread)=>Promise<unknown>;onDelete:(conversation:ArchivedThread)=>Promise<unknown>;
 /** "Delete all archived" (beta.16.1 item 3): the rows the fold shows, deleted after one confirm. Without it there's no such row. */
 onDeleteAll?:(conversations:ArchivedThread[])=>Promise<Freed&{count:number}>}){
 const client=useQueryClient();
 const archived=useQuery({queryKey:['archived-conversations',projectId],enabled:!!projectId,queryFn:()=>local<ArchivedThread[]>('conversations_archived',{projectId})});
 const [busy,setBusy]=useState(''),[error,setError]=useState<{id:string;text:string}|null>(null);
 // What the last "Delete all archived" did, said once in the fold's place after the rows are gone.
 const [freed,setFreed]=useState<(Freed&{count:number})|null>(null);
 useEffect(()=>{setFreed(null);},[projectId]);
 useEffect(()=>{if(!freed)return;const timer=setTimeout(()=>setFreed(null),FREED_MS);return()=>clearTimeout(timer);},[freed]);
 const rows=Array.isArray(archived.data)?archived.data:[];
 if(!rows.length&&!error)return freed?<FreedLine freed={freed}/>:null;
 async function restore(conversation:ArchivedThread,andOpen:boolean){
  if(busy)return;
  setBusy(conversation.id);setError(null);
  try{
   await restoreConversation(conversation);
   await client.invalidateQueries({queryKey:['archived-conversations',projectId]});
   await onRestored(conversation);
   if(andOpen)onOpen(conversation);
  }catch(failure){setError({id:conversation.id,text:`Couldn’t bring it back. ${failure instanceof Error?failure.message:String(failure)}`});}
  finally{setBusy('');}
 }
 const now=Date.now();
 return <details className="dervo-archived dv-archived-fold" data-testid="archived-conversations" open={open} onToggle={event=>{const next=(event.currentTarget as HTMLDetailsElement).open;if(next!==open)onOpenChange(next);}}>
  <summary><ChevronRight className="dervo-archived-chevron size-3.5" aria-hidden="true"/><span>Archived</span><span className="dervo-archived-count">{rows.length}</span></summary>
  <ul>
   {archivedGroups(rows,days,now).map(group=>[
    <li key={group.key} className="dv-archived-head" role="presentation">{group.title}</li>,
    ...group.rows.map(conversation=>{
     const when=group.tidied?quietFor(conversation.quiet_since,now):archivedOn(conversation.archived_at);
     return <li key={conversation.id}>
      <div className="dervo-archived-row">
       {conversation.visibility==='private'?<Lock size={14} strokeWidth={1.6} className="dervo-archived-lock" aria-label="Private"/>:<StatusGlyph state="idle"/>}
       <button type="button" className="dervo-archived-name dv-archived-open" title={`Open ${conversation.name}. It comes back to the list.`} disabled={!!busy}
        onClick={()=>void restore(conversation,true)}>{busy===conversation.id?'Bringing it back…':conversation.name}</button>
       {when&&<span className="dv-archived-when" title={group.tidied?`Quiet for ${when.replace(' d',' days')} when Dervo put it away`:undefined}>{when}</span>}
       <span className="dervo-archived-actions">
        <button type="button" className="dervo-archived-restore" aria-label={`Restore ${conversation.name}`} disabled={!!busy} onClick={()=>void restore(conversation,false)}>Restore</button>
        <FoldMenu conversation={conversation} disabled={!!busy} onRestore={()=>void restore(conversation,false)} onDelete={()=>onDelete(conversation)}/>
       </span>
      </div>
      {error?.id===conversation.id&&<p role="alert" className="dervo-archived-error">{error.text}</p>}
     </li>;
    }),
   ])}
   {onDeleteAll&&<li className="dv-archived-foot"><DeleteAll count={rows.length} disabled={!!busy} onDelete={async()=>{
    const result=await onDeleteAll(rows);
    setFreed(result);
    await client.invalidateQueries({queryKey:['archived-conversations',projectId]});
   }}/></li>}
  </ul>
 </details>;
}

const FREED_MS=12000;
/** "Deleted 12 threads. Freed 48 MB." The figure is the host's measurement; hovering says what it's made of. */
export function freedLine(freed:Freed&{count:number}):{text:string;detail:string}{
 const what=`Deleted ${freed.count} ${freed.count===1?'thread':'threads'}.`;
 const amount=freed.freedBytes<1000?'Freed less than 1 KB.':`Freed ${sizeLabel(freed.freedBytes)}.`;
 const parts=[freed.fileBytes>0&&`${sizeLabel(freed.fileBytes)} of saved file versions left the disk.`,
  freed.databaseBytes>0&&`${sizeLabel(freed.databaseBytes)} is free inside Dervo’s database, which reuses it before the file grows.`].filter(Boolean);
 return {text:`${what} ${amount}`,detail:[...parts,'Your project folder is as it was.'].join(' ')};
}
function FreedLine({freed}:{freed:Freed&{count:number}}){
 const line=freedLine(freed);
 return <p className="dv-activity dv-archived-freed" role="status" title={line.detail}><span className="dv-activity-text">{line.text}</span><span className="sr-only"> {line.detail}</span></p>;
}

/** The fold's last row: "Delete all archived…", which turns into its own confirm in place (tier 2, as a thread's Delete is):
 * the count, what goes, that the project folder stays, Cancel first and focused. Escape cancels. */
function DeleteAll({count,disabled,onDelete}:{count:number;disabled:boolean;onDelete:()=>Promise<void>}){
 const [confirming,setConfirming]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const cancel=useRef<HTMLButtonElement>(null),trigger=useRef<HTMLButtonElement>(null);
 useEffect(()=>{if(confirming)requestAnimationFrame(()=>cancel.current?.focus());},[confirming]);
 const close=()=>{setConfirming(false);setError('');requestAnimationFrame(()=>trigger.current?.focus());};
 const threads=`${count} ${count===1?'thread':'threads'}`;
 if(!confirming)return <button ref={trigger} type="button" className="dv-archived-delete-all" disabled={disabled} onClick={()=>setConfirming(true)}>
  <Trash2 size={14} strokeWidth={1.6} aria-hidden="true"/>Delete all archived…</button>;
 async function go(){
  if(busy)return;
  setBusy(true);setError('');
  try{await onDelete();setConfirming(false);}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  finally{setBusy(false);}
 }
 return <div className="dv-mconfirm dv-archived-confirm" role="alertdialog" aria-labelledby="dv-delete-all-q"
  onKeyDown={event=>{if(event.key==='Escape'&&!busy){event.preventDefault();event.stopPropagation();close();}}}>
  <p className="dv-q" id="dv-delete-all-q">Delete {count===1?'the archived thread':`all ${count} archived threads`}?</p>
  <ul className="dv-points"><li>{count===1?'Its':'Their'} messages and tasks are removed from Dervo. This can’t be undone.</li><li>Your project folder stays as it is, including any files {count===1?'it':'they'} changed.</li></ul>
  {error&&<p className="dv-merror" role="alert">{error}</p>}
  <div className="dv-acts">
   <button ref={cancel} type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={close}>Cancel</button>
   <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" aria-disabled={busy||undefined} onClick={()=>void go()}>{busy?'Deleting…':`Delete ${threads}`}</button>
  </div>
 </div>;
}

const archivedOn=(at:number|null|undefined)=>typeof at==='number'&&at>1e9?new Date(at*1000).toLocaleDateString([],{day:'numeric',month:'short'}):'';

function FoldMenu({conversation,disabled,onRestore,onDelete}:{conversation:ArchivedThread;disabled:boolean;onRestore:()=>void;onDelete:()=>Promise<unknown>}){
 const [confirming,setConfirming]=useState(false);
 return <DropdownMenu onOpenChange={open=>{if(!open)setConfirming(false);}}>
  <DropdownMenuTrigger asChild disabled={disabled}><button type="button" className="dervo-archived-more" aria-label={`Options for ${conversation.name}`}><MoreHorizontal size={14}/></button></DropdownMenuTrigger>
  <DropdownMenuContent align="start" side="right" sideOffset={6} className="dv-menu-thread" aria-label={conversation.name}>
   {confirming?<FoldDelete conversation={conversation} onDelete={onDelete}/>:<>
    <DropdownMenuItem onSelect={onRestore}><RotateCcw size={16} strokeWidth={1.5}/><span className="dv-mi-t">Restore</span></DropdownMenuItem>
    <DropdownMenuSeparator/>
    <DropdownMenuItem onSelect={event=>{event.preventDefault();setConfirming(true);}}><Trash2 size={16} strokeWidth={1.5}/><span className="dv-mi-t">Delete…</span></DropdownMenuItem>
   </>}
  </DropdownMenuContent>
 </DropdownMenu>;
}

/** The same tier 2 confirm as a live thread: what goes, what stays, Cancel first and focused. */
function FoldDelete({conversation,onDelete}:{conversation:ArchivedThread;onDelete:()=>Promise<unknown>}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const cancel=useRef<HTMLButtonElement>(null);
 useEffect(()=>{requestAnimationFrame(()=>cancel.current?.focus());},[]);
 return <div className="dv-mconfirm" role="alertdialog" aria-labelledby={`dv-fold-del-${conversation.id}`}>
  <p className="dv-q" id={`dv-fold-del-${conversation.id}`}>Delete {conversation.name}?</p>
  <ul className="dv-points"><li>Its messages and tasks are removed from Dervo. This can’t be undone.</li><li>Your project folder stays as it is, including any files it changed.</li><li>Restore brings it back instead.</li></ul>
  {error&&<p className="dv-merror" role="alert">{error}</p>}
  <div className="dv-acts">
   <DropdownMenuPrimitive.Item asChild><button ref={cancel} type="button" className="dv-btn dv-btn-quiet">Cancel</button></DropdownMenuPrimitive.Item>
   <DropdownMenuPrimitive.Item asChild onSelect={event=>{event.preventDefault();if(busy)return;setBusy(true);setError('');
    void onDelete().catch(failure=>setError(failure instanceof Error?failure.message:String(failure))).finally(()=>setBusy(false));}}>
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" aria-disabled={busy||undefined}>{busy?'Deleting…':'Delete thread'}</button></DropdownMenuPrimitive.Item>
  </div>
 </div>;
}
