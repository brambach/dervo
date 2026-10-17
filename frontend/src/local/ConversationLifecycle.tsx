import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';
import {ChevronRight,Lock,MoreHorizontal,RotateCcw,Trash2} from 'lucide-react';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {StatusGlyph} from './StatusGlyph';
import {deleteDraftEntry,getAllDraftEntries} from '@/features/messages/lib/useDrafts';
import {local,type Conversation} from './api';
import './ConversationLifecycle.css';
import './Surfaces.css';
import {t} from '@/i18n';

export type ArchivedConversation=Conversation&{archived_at:number};

/** Forget what this renderer kept for conversations that no longer exist: drafts and pending request ids. */
export function forgetConversations(channelIds:string[]){
  for(const entry of getAllDraftEntries())if(channelIds.includes(entry.draft.channelId))deleteDraftEntry(entry.key);
  for(const id of channelIds){
    for(const key of Object.keys(localStorage))if(key.startsWith('dervo-pending:'+id+':')||key==='dervo-coding-pending:'+id||key==='dervo-coordination-pending:'+id)localStorage.removeItem(key);
  }
}
export async function archiveConversation(conversation:Pick<Conversation,'id'|'project_id'>){
  return local<{id:string;visibility:string}>('conversation_archive',{projectId:conversation.project_id,channelId:conversation.id});
}
export async function restoreConversation(conversation:Pick<Conversation,'id'|'project_id'>){
  return local<{id:string}>('conversation_restore',{projectId:conversation.project_id,channelId:conversation.id});
}
/** The host checks the confirmation against this exact id, so the renderer never sends a bare word. */
export async function deleteConversation(conversation:Pick<Conversation,'id'|'project_id'>){
  return local<{id:string;channelIds:string[]}>('conversation_delete',{projectId:conversation.project_id,channelId:conversation.id,confirmation:'DELETE '+conversation.id});
}
/** A thread archiving several at once left where it was, and why (beta.16.1 item 3). */
export type ArchiveRefused={id:string;name:string;reason:string};
/** The threads selected in the sidebar, archived in one go. ``ids`` is exactly what went, which is what Undo brings back. */
export async function archiveConversations(projectId:string,channelIds:string[]){
  return local<{ids:string[];refused:ArchiveRefused[]}>('conversations_archive',{projectId,channelIds});
}
/** What deleting archived threads freed, as the host measured it: pages in Dervo's databases that now hold nothing (they're
 * reused before the files grow) and saved file versions that left the disk. */
export type Freed={freedBytes:number;databaseBytes:number;fileBytes:number};
/** "Delete all archived": exactly the archived threads the confirm counted. The count is in the confirmation, and the host
 * refuses the lot if any of them was brought back since. */
export async function deleteArchived(projectId:string,channelIds:string[]){
  return local<{ids:string[];count:number}&Freed>('conversations_delete_archived',{projectId,channelIds,confirmation:`DELETE ${channelIds.length} ARCHIVED`});
}

/** Archived threads of the current project, folded under the list. Each row has its glyph (or the lock), Restore on
 * hover and focus, and ⋯ with Restore and Delete…, which confirms in the menu as a live thread's does. */
export function ArchivedConversations({projectId,onRestored,onDelete}:{projectId:string;onRestored:(conversation:ArchivedConversation)=>Promise<unknown>;onDelete:(conversation:ArchivedConversation)=>Promise<unknown>}){
  const client=useQueryClient();
  const archived=useQuery({queryKey:['archived-conversations',projectId],enabled:!!projectId,queryFn:()=>local<ArchivedConversation[]>('conversations_archived',{projectId})});
  const [busy,setBusy]=useState(''),[error,setError]=useState<{id:string;text:string}|null>(null);
  const rows=archived.data??[];
  if(!rows.length&&!error)return null;
  async function restore(conversation:ArchivedConversation){
    if(busy)return;
    setBusy(conversation.id);setError(null);
    try{await restoreConversation(conversation);await client.invalidateQueries({queryKey:['archived-conversations',projectId]});await onRestored(conversation);}
    catch(failure){setError({id:conversation.id,text:t('Couldn’t restore it. {why}',{why:failure instanceof Error?failure.message:String(failure)})});}
    finally{setBusy('');}
  }
  return <details className="dervo-archived" data-testid="archived-conversations">
    <summary><ChevronRight className="dervo-archived-chevron size-3.5" aria-hidden="true"/><span>{t('Archived')}</span><span className="dervo-archived-count">{rows.length}</span></summary>
    <ul>
      {rows.map(conversation=><li key={conversation.id}>
        <div className="dervo-archived-row">
          {conversation.visibility==='private'?<Lock size={14} strokeWidth={1.6} className="dervo-archived-lock" aria-label={t('Private')}/>:<StatusGlyph state="idle"/>}
          <span className="dervo-archived-name" title={conversation.name}>{conversation.name}</span>
          <span className="dervo-archived-actions">
            <button type="button" className="dervo-archived-restore" aria-label={t('Restore {name}',{name:conversation.name})} disabled={!!busy} onClick={()=>void restore(conversation)}>{busy===conversation.id?t('Restoring…'):t('Restore')}</button>
            <ArchivedMenu conversation={conversation} disabled={!!busy} onRestore={()=>void restore(conversation)} onDelete={()=>onDelete(conversation)}/>
          </span>
        </div>
        {error?.id===conversation.id&&<p role="alert" className="dervo-archived-error">{error.text}</p>}
      </li>)}
    </ul>
  </details>;
}

function ArchivedMenu({conversation,disabled,onRestore,onDelete}:{conversation:ArchivedConversation;disabled:boolean;onRestore:()=>void;onDelete:()=>Promise<unknown>}){
  const [confirming,setConfirming]=useState(false);
  return <DropdownMenu onOpenChange={open=>{if(!open)setConfirming(false);}}>
    <DropdownMenuTrigger asChild disabled={disabled}><button type="button" className="dervo-archived-more" aria-label={t('Options for {name}',{name:conversation.name})}><MoreHorizontal size={14}/></button></DropdownMenuTrigger>
    <DropdownMenuContent align="start" side="right" sideOffset={6} className="dv-menu-thread" aria-label={conversation.name}>
      {confirming?<ArchivedDelete conversation={conversation} onDelete={onDelete}/>:<>
        <DropdownMenuItem onSelect={onRestore}><RotateCcw size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Restore')}</span></DropdownMenuItem>
        <DropdownMenuSeparator/>
        <DropdownMenuItem onSelect={event=>{event.preventDefault();setConfirming(true);}}><Trash2 size={16} strokeWidth={1.5}/><span className="dv-mi-t">{t('Delete…')}</span></DropdownMenuItem>
      </>}
    </DropdownMenuContent>
  </DropdownMenu>;
}

/** The same tier 2 confirm as a live thread: what goes, what stays, Cancel first and focused. */
function ArchivedDelete({conversation,onDelete}:{conversation:ArchivedConversation;onDelete:()=>Promise<unknown>}){
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const cancel=useRef<HTMLButtonElement>(null);
  useEffect(()=>{requestAnimationFrame(()=>cancel.current?.focus());},[]);
  return <div className="dv-mconfirm" role="alertdialog" aria-labelledby={`dv-del-${conversation.id}`}>
    <p className="dv-q" id={`dv-del-${conversation.id}`}>{t('Delete {name}?',{name:conversation.name})}</p>
    <ul className="dv-points"><li>{t('Its messages and tasks are removed from Dervo. This can’t be undone.')}</li><li>{t('Your project folder stays as it is, including any files it changed.')}</li><li>{t('Restore brings it back instead.')}</li></ul>
    {error&&<p className="dv-merror" role="alert">{error}</p>}
    <div className="dv-acts">
      <DropdownMenuPrimitive.Item asChild><button ref={cancel} type="button" className="dv-btn dv-btn-quiet">{t('Cancel')}</button></DropdownMenuPrimitive.Item>
      <DropdownMenuPrimitive.Item asChild onSelect={event=>{event.preventDefault();if(busy)return;setBusy(true);setError('');
        void onDelete().catch(failure=>setError(failure instanceof Error?failure.message:String(failure))).finally(()=>setBusy(false));}}>
        <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" aria-disabled={busy||undefined}>{busy?t('Deleting…'):t('Delete thread')}</button></DropdownMenuPrimitive.Item>
    </div>
  </div>;
}
