import {useEffect,useRef,useState} from 'react';
import {MoreHorizontal,Plus} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuLabel,DropdownMenuRadioGroup,DropdownMenuRadioItem,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {local} from './api';
import type {Idea,IdeaListing} from './ProjectRoom';
import {t} from '@/i18n';

/** Managing ideas from inside the room: a section's own menu, an inline editor, a new idea at the foot of the board
 * and the archive. Every change goes through `ideas_save` with the revision the room last saw, so an edit made
 * somewhere else is refused rather than overwritten, and every answer is the whole listing, so the board never holds
 * a half-applied change. */

export type Scope={projectId:string;profileId:string};
export const STATES:[Idea['state'],string][]=[['planned',t('Planned')],['building',t('Building')],['done',t('Done')]];

export function saveIdea(scope:Scope,idea:Idea|null,patch:Partial<Pick<Idea,'name'|'summary'|'state'|'target'>>):Promise<IdeaListing>{
 return local<IdeaListing>('ideas_save',{...scope,ideaId:idea?.id??null,expectedRevision:idea?.revision??0,
  name:patch.name??idea?.name??'',summary:patch.summary??idea?.summary??'',state:patch.state??idea?.state??'planned',
  target:patch.target!==undefined?patch.target:idea?.target??null});
}
export function archiveIdea(scope:Scope,idea:Idea,restore=false):Promise<IdeaListing>{
 return local<IdeaListing>(restore?'ideas_restore':'ideas_archive',{...scope,ideaId:idea.id,expectedRevision:idea.revision});
}

type Field='name'|'summary'|'target';
const FIELD_LABELS:Record<Field,string>={name:t('Idea name'),summary:t('What this idea is for'),target:t('Release this idea is aimed at')};

/** One field edited in place. Enter saves (a description takes Shift+Enter for a new line), Escape puts it back. */
function InlineField({field,initial,limit,onSave,onCancel}:{field:Field;initial:string;limit:number;onSave:(value:string)=>Promise<void>;onCancel:()=>void}){
 const [value,setValue]=useState(initial);
 const [busy,setBusy]=useState(false);
 const ref=useRef<HTMLInputElement&HTMLTextAreaElement>(null);
 useEffect(()=>{ref.current?.focus();ref.current?.select();},[]);
 async function save(){
  if(busy)return;
  if(value.trim()===initial.trim()){onCancel();return;}
  setBusy(true);
  try{await onSave(value.trim());}finally{setBusy(false);}
 }
 const keys=(event:React.KeyboardEvent)=>{
  if(event.key==='Escape'){event.preventDefault();onCancel();}
  if(event.key==='Enter'&&!(field==='summary'&&event.shiftKey)){event.preventDefault();void save();}
 };
 const common={ref,value,maxLength:limit,disabled:busy,'aria-label':FIELD_LABELS[field],onKeyDown:keys,
  onChange:(event:React.ChangeEvent<HTMLInputElement|HTMLTextAreaElement>)=>setValue(event.target.value),
  placeholder:field==='target'?'0.1.0-beta.5, or leave empty':field==='summary'?t('A sentence Dervo uses to file conversations here'):''};
 return <div className="dervo-room-edit" data-field={field}>
  {field==='summary'?<textarea rows={2} {...common}/>:<input {...common}/>}
  <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>{t('Cancel')}</Button>
  <Button size="sm" onClick={()=>void save()} disabled={busy||(field==='name'&&!value.trim())}>{busy?t('Saving…'):t('Save')}</Button>
 </div>;
}

export function IdeaMenu({idea,scope,limits,onListing,onError,editing,setEditing}:{idea:Idea;scope:Scope;limits:IdeaListing['limits'];
 onListing:(listing:IdeaListing)=>void;onError:(message:string)=>void;editing:Field|null;setEditing:(field:Field|null)=>void}){
 async function apply(run:()=>Promise<IdeaListing>){
  try{onListing(await run());setEditing(null);}catch(error){onError(String(error));throw error;}
 }
 return <>
  <DropdownMenu>
   <DropdownMenuTrigger asChild><button type="button" className="dervo-room-more" aria-label={t('Options for {name}',{name:idea.name})}><MoreHorizontal size={16}/></button></DropdownMenuTrigger>
   <DropdownMenuContent align="end" className="dervo-room-menu">
    <DropdownMenuItem onSelect={()=>setEditing('name')}>{t('Rename')}</DropdownMenuItem>
    <DropdownMenuItem onSelect={()=>setEditing('summary')}>{idea.summary?t('Edit what it’s for'):t('Describe this idea')}</DropdownMenuItem>
    <DropdownMenuSeparator/>
    <DropdownMenuLabel>{t('Where it stands')}</DropdownMenuLabel>
    <DropdownMenuRadioGroup value={idea.state} onValueChange={value=>void apply(()=>saveIdea(scope,idea,{state:value as Idea['state']})).catch(()=>{})}>
     {STATES.map(([value,label])=><DropdownMenuRadioItem key={value} value={value}>{label}</DropdownMenuRadioItem>)}
    </DropdownMenuRadioGroup>
    <DropdownMenuItem onSelect={()=>setEditing('target')}>{idea.target?t('Aimed at {target}',{target:idea.target}):t('Aim at a release')}</DropdownMenuItem>
    <DropdownMenuSeparator/>
    {/* Archiving removes this menu's own section, so it waits for the menu to finish closing and hand focus back. */}
    <DropdownMenuItem onSelect={()=>{setTimeout(()=>void apply(()=>archiveIdea(scope,idea)).catch(()=>{}),0);}}>{t('Archive idea')}</DropdownMenuItem>
   </DropdownMenuContent>
  </DropdownMenu>
  {editing&&editing!=='name'&&<div className="dervo-room-edit-row"><InlineField key={editing} field={editing}
   initial={(editing==='summary'?idea.summary:idea.target)??''} limit={editing==='summary'?limits.summary:32}
   onCancel={()=>setEditing(null)} onSave={value=>apply(()=>saveIdea(scope,idea,editing==='summary'?{summary:value}:{target:value||null}))}/></div>}
 </>;
}

/** The idea's name, or its inline editor while renaming. */
export function IdeaName({idea,scope,limits,editing,setEditing,onListing,onError}:{idea:Idea;scope:Scope;limits:IdeaListing['limits'];
 editing:Field|null;setEditing:(field:Field|null)=>void;onListing:(listing:IdeaListing)=>void;onError:(message:string)=>void}){
 if(editing!=='name')return <h2>{idea.name}</h2>;
 return <InlineField field="name" initial={idea.name} limit={limits.name} onCancel={()=>setEditing(null)}
  onSave={async value=>{try{onListing(await saveIdea(scope,idea,{name:value}));setEditing(null);}catch(error){onError(String(error));}}}/>;
}

/** A new idea at the foot of the board: a name, and optionally the release it's aimed at. */
export function NewIdea({scope,limits,onListing,onError}:{scope:Scope;limits:IdeaListing['limits'];onListing:(listing:IdeaListing)=>void;onError:(message:string)=>void}){
 const [open,setOpen]=useState(false);
 const [name,setName]=useState('');
 const [target,setTarget]=useState('');
 const [busy,setBusy]=useState(false);
 const nameRef=useRef<HTMLInputElement>(null);
 useEffect(()=>{if(open)nameRef.current?.focus();},[open]);
 function close(){setOpen(false);setName('');setTarget('');}
 async function add(){
  if(!name.trim()||busy)return;
  setBusy(true);
  try{onListing(await saveIdea(scope,null,{name:name.trim(),state:'planned',target:target.trim()||null}));close();}
  catch(error){onError(String(error));}
  finally{setBusy(false);}
 }
 if(!open)return <button type="button" className="dervo-room-add" onClick={()=>setOpen(true)}><Plus size={15}/>{t('New idea')}</button>;
 const keys=(event:React.KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();close();}if(event.key==='Enter'){event.preventDefault();void add();}};
 return <div className="dervo-room-new" role="group" aria-label={t('New idea')}>
  <input ref={nameRef} value={name} maxLength={limits.name} placeholder={t('Name the idea, like Releases or Onboarding')} aria-label={t('Idea name')} onChange={event=>setName(event.target.value)} onKeyDown={keys}/>
  <input value={target} maxLength={32} placeholder={t('Aim at a release, optional')} aria-label={t('Release this idea is aimed at')} onChange={event=>setTarget(event.target.value)} onKeyDown={keys}/>
  <Button size="sm" variant="ghost" onClick={close} disabled={busy}>{t('Cancel')}</Button>
  <Button size="sm" onClick={()=>void add()} disabled={busy||!name.trim()}>{busy?t('Adding…'):t('Add idea')}</Button>
 </div>;
}

/** Ideas you archived, each one a click from coming back. Its conversations are untouched while it's away. */
export function ArchivedIdeas({archived,scope,onListing,onError}:{archived:Idea[];scope:Scope;onListing:(listing:IdeaListing)=>void;onError:(message:string)=>void}){
 if(!archived.length)return null;
 return <details className="dervo-room-archived">
  <summary>{t('Archived ideas')} <span>{archived.length}</span></summary>
  <ul>{archived.map(idea=><li key={idea.id}><span>{idea.name}</span>
   <Button size="sm" variant="ghost" onClick={()=>void archiveIdea(scope,idea,true).then(onListing).catch(error=>onError(String(error)))}>{t('Restore')}</Button></li>)}</ul>
 </details>;
}
