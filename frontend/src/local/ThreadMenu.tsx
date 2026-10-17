import {useEffect,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';
import * as ContextMenuPrimitive from '@radix-ui/react-context-menu';
import {Archive,FolderInput,Pencil,Plus,Search,Trash2} from 'lucide-react';
import {DropdownMenuItem,DropdownMenuSeparator,DropdownMenuSub,DropdownMenuSubContent,DropdownMenuSubTrigger} from '@/shared/ui/dropdown-menu';
import {ContextMenuItem,ContextMenuSeparator,ContextMenuSub,ContextMenuSubContent,ContextMenuSubTrigger} from '@/shared/ui/context-menu';
import {StatusGlyph} from './StatusGlyph';
import type {Conversation} from './api';
import {LifecycleBlockers,type OpenBlocker} from './LifecycleBlockers';
import './Surfaces.css';

/** The thread menus (surfaces/thread-menu.html, row-menu-move.html, row-delete-confirm.html): one set of rows for the
 * sidebar row's ⋯, its right-click and the thread header's ⋯. Rename happens in the row, Move is a submenu of the
 * project's workstreams, Archive just happens with Undo in the row it left (tier 1), and Delete turns the menu into its
 * own confirm, Cancel first and focused (tier 2). The host still checks the delete against its confirmation token. */

export type Workstream={id:string;name:string;count:number};
/** Where Move can put a thread: an existing workstream, "Not filed", or a new workstream by name. */
export type MoveTarget={ideaId:string|null;name:string}|{create:string};
export type ThreadMenuModel={
  /** The project's workstreams, or null while they can't be listed. */
  workstreams:Workstream[]|null;
  /** The workstream a thread is filed under, if any. */
  filedUnder:(conversation:Conversation)=>string|null;
  /** Why a thread can't be moved, or null when it can. */
  cannotMove:(conversation:Conversation)=>string|null;
  rename:(conversation:Conversation)=>void;
  move:(conversation:Conversation,target:MoveTarget)=>void;
  archive:(conversation:Conversation)=>void;
  remove:(conversation:Conversation)=>Promise<void>;
  stop:(conversation:Conversation)=>Promise<void>;
  openBlocker?:OpenBlocker;
};

/** A thread's archive is refused while it runs (the host's own rule), so the row says so rather than failing. */
export const archiveBlocked=(conversation:Conversation)=>conversation.activity?.running?'It’s running. Stop it first.':null;

/** The workstreams whose name contains the words, and whether the words name a new one. */
export function matchWorkstreams(workstreams:Workstream[],query:string):{shown:Workstream[];create:string|null}{
  const words=query.trim();
  if(!words)return {shown:workstreams,create:null};
  const lower=words.toLowerCase();
  const shown=workstreams.filter(w=>w.name.toLowerCase().includes(lower));
  return {shown,create:workstreams.some(w=>w.name.toLowerCase()===lower)?null:words};
}

type Kind='dropdown'|'context';
const PARTS={
  dropdown:{Item:DropdownMenuItem,Separator:DropdownMenuSeparator,Sub:DropdownMenuSub,SubTrigger:DropdownMenuSubTrigger,SubContent:DropdownMenuSubContent,Bare:DropdownMenuPrimitive.Item},
  context:{Item:ContextMenuItem,Separator:ContextMenuSeparator,Sub:ContextMenuSub,SubTrigger:ContextMenuSubTrigger,SubContent:ContextMenuSubContent,Bare:ContextMenuPrimitive.Item},
} as const;
const TICK=<svg className="dv-tick" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>;

/** The rows, or the delete confirm once Delete… was chosen. `header` adds the thread header's own rows. */
export function ThreadMenuBody({kind,conversation,model,confirming,onConfirm,header}:{kind:Kind;conversation:Conversation;model:ThreadMenuModel;
  confirming:boolean;onConfirm:(next:boolean)=>void;header?:{search?:()=>void;continuity?:ReactNode}}){
  const P=PARTS[kind];
  if(confirming)return <DeleteConfirm kind={kind} conversation={conversation} model={model}/>;
  const blocked=model.cannotMove(conversation),filed=model.filedUnder(conversation),archiving=archiveBlocked(conversation);
  const two=(label:string,why:string)=><span className="dv-mi-t"><span>{label}</span><small>{why}</small></span>;
  return <>
    <P.Item onSelect={()=>model.rename(conversation)}><Pencil size={16} strokeWidth={1.5}/><span className="dv-mi-t">Rename</span></P.Item>
    {blocked?<P.Item disabled className="dv-mi-two"><FolderInput size={16} strokeWidth={1.5}/>{two('Move to workstream',blocked)}</P.Item>
      :<P.Sub>
        <P.SubTrigger><FolderInput size={16} strokeWidth={1.5}/><span className="dv-mi-t">Move to workstream</span>{header&&filed&&<span className="dv-mi-end" aria-hidden="true"><span className="dv-val">{filed}</span></span>}</P.SubTrigger>
        <P.SubContent className="dv-menu-move" aria-label="Move to workstream"><MoveRows kind={kind} conversation={conversation} model={model} filed={filed}/></P.SubContent>
      </P.Sub>}
    {header&&(header.search||header.continuity)&&<><P.Separator/>
      {header.search&&<P.Item onSelect={header.search}><Search size={16} strokeWidth={1.5}/><span className="dv-mi-t">Search in thread</span><span className="dv-mi-end" aria-hidden="true">⌘F</span></P.Item>}
      {header.continuity}</>}
    <P.Separator/>
    {archiving?<P.Item disabled className="dv-mi-two"><Archive size={16} strokeWidth={1.5}/>{two('Archive',archiving)}</P.Item>
      :<P.Item onSelect={()=>model.archive(conversation)}><Archive size={16} strokeWidth={1.5}/><span className="dv-mi-t">Archive</span><span className="dv-mi-end" aria-hidden="true">⌘⇧A</span></P.Item>}
    {!header&&<P.Separator/>}
    <P.Item onSelect={event=>{event.preventDefault();onConfirm(true);}}><Trash2 size={16} strokeWidth={1.5}/><span className="dv-mi-t">Delete…</span></P.Item>
  </>;
}

/** Move to workstream: a filter, the workstreams with their counts and a tick on the current one, Not filed, and New
 * workstream. Typing a name that doesn't exist offers to make it. Home's job rows open the same rows (dd #10). */
export function MoveRows({kind,conversation,model,filed}:{kind:Kind;conversation:Conversation;model:ThreadMenuModel;filed:string|null}){
  const P=PARTS[kind];
  const [query,setQuery]=useState('');
  const filter=useRef<HTMLInputElement>(null);
  useEffect(()=>{requestAnimationFrame(()=>filter.current?.focus());},[]);
  const list=model.workstreams;
  const {shown,create}=matchWorkstreams(list??[],query);
  function keys(event:KeyboardEvent<HTMLInputElement>){
    if(event.key==='ArrowDown'){event.preventDefault();event.stopPropagation();(event.currentTarget.closest('[role="menu"]')?.querySelector<HTMLElement>('[role="menuitem"]:not([data-disabled])'))?.focus();return;}
    if(event.key==='Enter'){
      event.preventDefault();event.stopPropagation();
      // Choosing through the row itself closes the menu the way a click does.
      const exact=shown.find(w=>w.name.toLowerCase()===query.trim().toLowerCase())??(create?null:shown[0]);
      const key=exact?exact.id:create?'create':'';
      if(key)event.currentTarget.closest('[role="menu"]')?.querySelector<HTMLElement>(`[data-move="${CSS.escape(key)}"]`)?.click();
      return;
    }
    if(event.key==='Escape'&&query){event.preventDefault();event.stopPropagation();setQuery('');return;}
    if(event.key==='ArrowLeft'&&!query)return;
    if(event.key!=='Escape'&&event.key!=='Tab')event.stopPropagation();
  }
  return <>
    <div className="dv-mfilter">
      <Search size={14} strokeWidth={1.6} aria-hidden="true"/>
      <input ref={filter} value={query} placeholder="Find or add a workstream" aria-label="Find or add a workstream" autoComplete="off" spellCheck={false}
        onChange={event=>setQuery(event.target.value)} onKeyDown={keys}/>
    </div>
    {list===null&&<P.Item disabled><StatusGlyph state="running"/><span className="dv-mi-t">Loading workstreams…</span></P.Item>}
    {shown.map(w=><P.Item key={w.id} data-move={w.id} data-current={w.name===filed?'':undefined} aria-checked={w.name===filed} onSelect={()=>{if(w.name!==filed)model.move(conversation,{ideaId:w.id,name:w.name});}}>
      <span className="dv-mi-t">{w.name}</span><span className="dv-mi-end" aria-hidden="true"><span className="dv-val">{w.count}</span>{w.name===filed?TICK:null}</span>
    </P.Item>)}
    {(shown.length>0||list===null)&&<P.Separator/>}
    {!query.trim()&&<P.Item onSelect={()=>{if(filed)model.move(conversation,{ideaId:null,name:'Not filed yet'});}}><span className="dv-mi-t">Not filed</span>{!filed?<span className="dv-mi-end" aria-hidden="true">{TICK}</span>:null}</P.Item>}
    {create?<P.Item data-move="create" onSelect={()=>model.move(conversation,{create})}><Plus size={16} strokeWidth={1.5}/><span className="dv-mi-t">New workstream “{create}”</span></P.Item>
      :<P.Item onSelect={event=>{event.preventDefault();filter.current?.focus();}}><Plus size={16} strokeWidth={1.5}/><span className="dv-mi-t">New workstream…</span></P.Item>}
  </>;
}

/** Delete, confirmed in the menu: what goes, what stays, and that Archive keeps it instead. A running thread offers to
 * stop it first. Cancel is first and takes focus. */
function DeleteConfirm({kind,conversation,model}:{kind:Kind;conversation:Conversation;model:ThreadMenuModel}){
  const P=PARTS[kind];
  const [busy,setBusy]=useState<''|'deleting'|'stopping'>(''),[error,setError]=useState('');
  const cancel=useRef<HTMLButtonElement>(null);
  useEffect(()=>{requestAnimationFrame(()=>cancel.current?.focus());},[]);
  const running=!!conversation.activity?.running;
  async function act(event:Event){
    event.preventDefault();
    if(busy)return;
    setBusy(running?'stopping':'deleting');setError('');
    try{if(running)await model.stop(conversation);else await model.remove(conversation);}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy('');}
  }
  return <div className="dv-mconfirm" role="alertdialog" aria-labelledby={`dv-del-${conversation.id}`}>
    <p className="dv-q" id={`dv-del-${conversation.id}`}>Delete {conversation.name}?</p>
    {running?<p className="dv-small dv-muted">It’s running. Stop it first.</p>
      :<ul className="dv-points"><li>Its messages and tasks are removed from Dervo. This can’t be undone.</li><li>Your project folder stays as it is, including any files it changed.</li><li>Archive keeps it out of the way instead.</li></ul>}
    {error&&<p className="dv-merror" role="alert">{error}</p>}
    {(running||error)&&model.openBlocker&&<LifecycleBlockers scope={{scope:'conversation',projectId:conversation.project_id,channelId:conversation.id}} onOpen={model.openBlocker}
      renderAction={blocker=><P.Bare asChild onSelect={()=>model.openBlocker?.(blocker)}><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm">{blocker.taskId?'Open task':'Open thread'}</button></P.Bare>}/>}
    <div className="dv-acts">
      <P.Bare asChild><button ref={cancel} type="button" className="dv-btn dv-btn-quiet">Cancel</button></P.Bare>
      <P.Bare asChild onSelect={event=>void act(event)}><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} aria-disabled={!!busy||undefined}>
        {busy==='deleting'?'Deleting…':busy==='stopping'?'Stopping…':running?'Stop it':'Delete thread'}</button></P.Bare>
    </div>
  </div>;
}


/** Tier 1 undo, held for 6 seconds or until the next undoable action: the row the change left says what happened,
 * with Undo, and ⌘Z does the same while focus isn't in a text field. */
/** ``others``: the rest of a multi-select archive (beta.16.1 item 3). They leave the list with ``conversation``, whose row
 * says how many went, and one Undo brings them all back. */
export type UndoEntry={id:string;kind:'archived'|'moved'|'renamed';label:string;conversation:Conversation;others?:Conversation[];index:number;fromGroup:string|null;toGroup?:string|null;undo:()=>Promise<void>};
export const UNDO_MS=6000;
export function editableTarget(target:EventTarget|null):boolean{
  const element=target as HTMLElement|null;
  return !!element&&(element.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(element.tagName));
}
export function useThreadUndo(){
  const [entry,setEntry]=useState<UndoEntry|null>(null),[failed,setFailed]=useState<Record<string,string>>({});
  const latest=useRef(entry);latest.current=entry;
  useEffect(()=>{if(!entry)return;const timer=setTimeout(()=>setEntry(current=>current===entry?null:current),UNDO_MS);return()=>clearTimeout(timer);},[entry]);
  async function undo(){
    const current=latest.current;if(!current)return;
    setEntry(null);
    try{await current.undo();}
    catch(failure){setFailed(rows=>({...rows,[current.id]:`Couldn’t undo. ${failure instanceof Error?failure.message:String(failure)}`}));}
  }
  useEffect(()=>{
    function onKey(event:globalThis.KeyboardEvent){
      if(!latest.current||!(event.metaKey||event.ctrlKey)||event.shiftKey||event.altKey||event.key.toLowerCase()!=='z'||editableTarget(event.target))return;
      event.preventDefault();void undo();
    }
    window.addEventListener('keydown',onKey);
    return()=>window.removeEventListener('keydown',onKey);
  },[]);
  return {entry,offer:(next:UndoEntry)=>{setFailed(rows=>{const {[next.id]:_,...rest}=rows;return rest;});setEntry(next);},clear:()=>setEntry(null),undo,
    failed,fail:(id:string,message:string)=>setFailed(rows=>({...rows,[id]:message})),settle:(id:string)=>setFailed(rows=>{const {[id]:_,...rest}=rows;return rest;})};
}

/** What the sidebar shows while an undo is live: the archived thread comes back as the row it left, a moved thread
 * shows in its new group with a row saying where it went left in the old one. Everything else is as the host says. */
export function withUndoRows(conversations:Conversation[],entry:UndoEntry|null):(Conversation&{gone?:UndoEntry})[]{
  if(!entry||entry.kind==='renamed')return conversations;
  const went=new Set([entry.conversation.id,...(entry.others??[]).map(c=>c.id)]);
  const rows:(Conversation&{gone?:UndoEntry})[]=conversations.filter(c=>!(entry.kind==='archived'&&went.has(c.id)))
    .map(c=>entry.kind==='moved'&&c.id===entry.conversation.id?{...c,organisationGroup:entry.toGroup??null}:c);
  const ghost={...entry.conversation,id:`gone:${entry.conversation.id}`,organisationGroup:entry.fromGroup,gone:entry};
  rows.splice(Math.min(Math.max(0,entry.index),rows.length),0,ghost);
  return rows;
}
