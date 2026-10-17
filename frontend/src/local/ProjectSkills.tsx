import {useEffect,useRef,useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {local} from './api';
import {providerAuthor} from './providerLabel';
import {ago} from './ProjectMemory';
import {t} from '@/i18n';

/** Skills in Knowledge, "How we work" (b16-memory/knowledge.png): procedures for one job in this project. Since beta.16
 * (Bryce, 25 Sep) a skill an agent saves applies straight away and shows here as New, with Edit and Delete; Delete has
 * Undo, and each skill says which chats it went to so a bad one is easy to trace. You write versions too. A SKILL.md
 * imported from the project still waits for your approval, because it's the repository's words. A skill reaches a task
 * only when the task is about that job. */

export type SkillVersion={version:number;name:string;description:string;body:string;status:'approved'|'proposed'|'rejected'|'withdrawn';
 origin:'human'|'agent'|'imported';author:string;created_at:number;task_id?:string|null;thread_id?:string|null;note?:string};
export type Skill={id:string;name:string;description:string;revision:number;current:SkillVersion|null;proposals:SkillVersion[];
 uses:number;last_used_at:number|null;used_in?:string[];exported:{at:number;version:number;path:string}|null;archived_at:number|null;
 history:{version:number;status:string;origin:string;created_at:number}[]};
export type SkillListing={skills:Skill[];archived:Skill[];pending:number;limits:{description:number;body:number;skills:number};dirs:string[];
 imported?:{found:number;added:number;skipped:number;refused:string[]}};

/** Where a waiting version came from, in words. */
export function proposalOrigin(version:SkillVersion,replaces:SkillVersion|null,now=Date.now()/1000):string{
 const who=version.origin==='agent'?`Proposed by ${providerAuthor(version.author)}`:version.origin==='imported'?(version.note||'Imported from the project'):'Proposed';
 return [who,ago(version.created_at,now),replaces?`would replace version ${replaces.version}`:'new skill'].join(' · ');
}
/** Kept after you last looked: an agent's version saved since then. Your own versions are never New. */
export function skillIsNew(skill:Skill,seenAt:number|undefined):boolean{
 return !!skill.current&&skill.current.origin==='agent'&&typeof seenAt==='number'&&skill.current.created_at>seenAt;
}
/** How many chats a skill went to, for its line: "used in 2 chats", or "not used yet". */
export function usedIn(skill:Skill):string{
 const count=skill.used_in?.length||skill.uses;
 return count?`used in ${count} ${count===1?'chat':'chats'}`:'not used yet';
}
/** What a skill has done, counted rather than described. */
export function skillMeta(skill:Skill):string{
 const used=skill.uses?`used in ${skill.uses} ${skill.uses===1?'task':'tasks'}`:'not used yet';
 return [used,skill.current?`version ${skill.current.version}`:'',skill.exported?`written to ${skill.exported.path.replace(/\/[^/]+\/SKILL\.md$/,'')}`:''].filter(Boolean).join(' · ');
}
/** A skill name from what you typed: lowercase words joined by hyphens, the SKILL.md rule. */
export function skillName(value:string):string{
 return value.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,64);
}
export const skillsKey=(projectId:string,profileId:string)=>['project-skills',projectId,profileId];
/** The one skills read the page shares: the section and the queue follow the same answer. */
export function useSkills(projectId:string,profileId:string){
 return useQuery({queryKey:skillsKey(projectId,profileId),queryFn:()=>local<SkillListing>('skills_list',{projectId,profileId})});
}

export type Editing={skill:Skill|null;name:string;description:string;body:string;replacing?:SkillVersion};

export function SkillEditor({editing,limits,busy,onChange,onSave,onCancel,saveLabel}:{editing:Editing;limits:SkillListing['limits'];busy:boolean;
 onChange:(patch:Partial<Editing>)=>void;onSave:()=>void;onCancel:()=>void;saveLabel?:string}){
 const first=useRef<HTMLInputElement>(null);
 useEffect(()=>{first.current?.focus();},[]);
 const named=skillName(editing.name);
 return <form className="dv-kn-edit" aria-label={editing.skill?`Edit ${editing.skill.name}`:'New skill'}
  onSubmit={event=>{event.preventDefault();onSave();}} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();onCancel();}
   else if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();onSave();}}}>
  <label className="dv-kn-field">Name<input ref={first} value={editing.name} maxLength={80} placeholder="add-a-bridge-operation" onChange={event=>onChange({name:event.target.value})}/></label>
  {editing.name&&named!==editing.name&&<p className="dv-kn-hint">Saved as <code>{named||'…'}</code>, the SKILL.md name rule.</p>}
  <label className="dv-kn-field">When it applies<input value={editing.description} maxLength={limits.description} placeholder="One line, like Adding an operation the renderer calls through the bridge." onChange={event=>onChange({description:event.target.value})}/></label>
  <label className="dv-kn-field">Steps<textarea rows={8} value={editing.body} maxLength={limits.body} placeholder={'1. …\n2. …'} onChange={event=>onChange({body:event.target.value})}/></label>
  <div className="dv-row-actions">
   <button type="submit" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||!named||!editing.description.trim()||!editing.body.trim()}>{busy?'Saving…':saveLabel??(editing.skill?'Save version':'Save skill')}</button>
   <button type="button" className="dv-btn dv-btn-quiet" onClick={onCancel} disabled={busy}>Cancel</button>
  </div>
 </form>;
}

const SKILL_ICON=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="dv-ico"><path d="M6.5 4.5h6M6.5 8h6M6.5 11.5h6"/><path d="M3 4.5h.5M3 8h.5M3 11.5h.5"/></svg>;
const MORE=<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2"/><circle cx="8" cy="8" r="1.2"/><circle cx="12.5" cy="8" r="1.2"/></svg>;
const CHEVRON=<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>;

export type SkillNotice=(text:string,undo?:()=>Promise<unknown>)=>void;
type Props={projectId:string;profileId:string;onNotice?:SkillNotice;seenAt?:number;threadName?:(threadId:string)=>string|undefined;
 onOpenSource?:(threadId:string,taskId:string)=>void};

/** "How we work": its head with the count and caption, then the skills in use, newest first, then the ways to add one. */
export function SkillsPage({projectId,profileId,onNotice,seenAt,threadName=()=>undefined,onOpenSource}:Props){
 const scope={projectId,profileId};
 const client=useQueryClient();
 const key=skillsKey(projectId,profileId);
 const skills=useSkills(projectId,profileId);
 const importInput=useRef<HTMLInputElement>(null);
 const [busy,setBusy]=useState('');
 const [error,setError]=useState('');
 const [localNotice,setLocalNotice]=useState('');
 const [editing,setEditing]=useState<Editing|null>(null);
 const [open,setOpen]=useState<Record<string,boolean>>({});
 const [chats,setChats]=useState<Record<string,boolean>>({});
 const [deleted,setDeleted]=useState<Record<string,Skill>>({});
 const listing=skills.data;
 const notice:SkillNotice=onNotice??((text)=>setLocalNotice(text));
 async function act(label:string,run:()=>Promise<SkillListing>,done?:(value:SkillListing)=>void){
  setBusy(label);setError('');setLocalNotice('');
  try{const value=await run();client.setQueryData(key,value);done?.(value);return value;}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));return null;}
  finally{setBusy('');}
 }
 const save=()=>{if(!editing)return;void act('save',()=>local<SkillListing>('skills_save',{...scope,skillId:editing.skill?.id??null,
  expectedRevision:editing.skill?.revision??0,name:skillName(editing.name),description:editing.description,body:editing.body}),
  value=>{setEditing(null);const saved=value.skills.find(entry=>entry.name===skillName(editing.name));notice(`${saved?.name??skillName(editing.name)} is saved. Chats about it get these steps from now on.`);});};
 const approved=(listing?.skills??[]).filter(skill=>skill.current).sort((a,b)=>(b.current!.created_at-a.current!.created_at));
 const dirs=(listing?.dirs??['.claude/skills','.agents/skills']);
 const restore=(skill:Skill)=>act(`restore:${skill.id}`,async()=>{const fresh=client.getQueryData<SkillListing>(key)?.archived.find(entry=>entry.id===skill.id);
  return local<SkillListing>('skills_restore',{...scope,skillId:skill.id,expectedRevision:fresh?.revision??skill.revision+1});},()=>setDeleted(current=>{const next={...current};delete next[skill.id];return next;}));
 const shownDeleted=Object.values(deleted).filter(skill=>!approved.some(entry=>entry.id===skill.id));

 function source(skill:Skill):ReactNode{
  const current=skill.current!;
  if(current.origin==='human')return 'Written by you';
  if(current.origin==='imported')return current.note||'Imported from the project';
  const who=providerAuthor(current.author),thread=current.thread_id,name=thread?threadName(thread):undefined;
  if(!name)return who;
  return <>{who} in {onOpenSource&&current.task_id?<button type="button" className="dv-kn-link" onClick={()=>onOpenSource(thread!,current.task_id!)}>{name}</button>:name}</>;
 }
 function chatList(skill:Skill):ReactNode{
  const names=(skill.used_in??[]).map(id=>threadName(id)??'a chat you can’t open here');
  return <p className="dv-row-line dv-kn-chats">{names.length?`Went to ${[...new Set(names)].join(', ')}.`:'Not sent to a chat yet.'} Delete it if it misled one.</p>;
 }

 return <section className="dv-sec" aria-labelledby="dervo-knowledge-skills" data-testid="skills">
  <div className="dv-sec-head"><h2 className="dv-title" id="dervo-knowledge-skills">How we work</h2>{approved.length>0&&<span className="dv-kn-count">{approved.length}</span>}
   <span className="dv-caption">Sent with a chat about that job</span></div>
  {localNotice&&<p className="dv-kn-line" role="status">{localNotice}</p>}
  {error&&<p className="dv-kn-line" role="alert">{error}</p>}
  <div className="dv-sheet">
   {skills.isError&&<p className="dv-kn-line" role="alert">Skills didn’t load. <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void skills.refetch()}>Try again</button></p>}
   {skills.isPending&&<p className="dv-kn-line" role="status">Reading skills…</p>}
   {approved.map(skill=>{
    const editingThis=editing?.skill?.id===skill.id&&!editing.replacing;
    const fresh=skillIsNew(skill,seenAt);
    return <div key={skill.id} className={`dv-row dv-kn-note${editingThis?' dv-open':''}`} data-testid="skill-row">
     {SKILL_ICON}
     <div>
      <button type="button" className="dv-row-name dv-kn-skill-name" aria-expanded={!!open[skill.id]} onClick={()=>setOpen({...open,[skill.id]:!open[skill.id]})}>{skill.description}</button>
      <div className="dv-row-line"><span className="dv-kn-mono-name">{skill.name}</span> · {source(skill)} · {usedIn(skill).startsWith('used in ')
       // Only the count is the link, as in the mock: "used in <u>2 chats</u>".
       ?<>used in <button type="button" className="dv-kn-link" aria-label={usedIn(skill)} aria-expanded={!!chats[skill.id]} onClick={()=>setChats({...chats,[skill.id]:!chats[skill.id]})}>{usedIn(skill).slice('used in '.length)}</button></>
       :usedIn(skill)}</div>
      {chats[skill.id]&&chatList(skill)}
      {open[skill.id]&&skill.current&&<p className="dv-kn-steps">{skill.current.body}</p>}
      {editingThis&&listing&&<SkillEditor editing={editing} limits={listing.limits} busy={busy==='save'} onChange={patch=>setEditing({...editing,...patch})} onSave={save} onCancel={()=>setEditing(null)}/>}
     </div>
     <div className="dv-row-end">
      {fresh&&<span className="dv-kn-new">New</span>}
      <span className="dv-kn-acts">
       <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Edit skill: {name}',{name:skill.name})} disabled={!!busy||!!editing} onClick={()=>skill.current&&setEditing({skill,name:skill.name,description:skill.current.description,body:skill.current.body})}>{t('Edit')}</button>
       <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Delete skill: {name}',{name:skill.name})} disabled={!!busy} onClick={()=>void act(`archive:${skill.id}`,()=>local<SkillListing>('skills_archive',{...scope,skillId:skill.id,expectedRevision:skill.revision}),
        ()=>setDeleted(current=>({...current,[skill.id]:skill})))}>{t('Delete')}</button>
       <DropdownMenu>
        <DropdownMenuTrigger asChild><button type="button" className="dv-btn dv-btn-icon" aria-label={t('More for {name}',{name:skill.name})}>{MORE}</button></DropdownMenuTrigger>
        <DropdownMenuContent align="end">
         <DropdownMenuItem onSelect={()=>void act(`export:${skill.id}`,()=>local<SkillListing>('skills_export',{...scope,skillId:skill.id}),
          value=>notice(t('Wrote {path}. Claude Code and other agents can read it there.',{path:value.skills.find(entry=>entry.id===skill.id)?.exported?.path??'SKILL.md'})))}>{skill.exported?t('Write to project again'):t('Write to project')}</DropdownMenuItem>
        </DropdownMenuContent>
       </DropdownMenu>
      </span>
     </div>
    </div>;
   })}
   {shownDeleted.map(skill=><p key={skill.id} className="dv-activity dv-kn-gone" role="status"><span className="dv-activity-text">{t('Deleted {name}. Chats about it won’t get it.',{name:skill.name})}</span>
    <button type="button" className="dv-activity-action" disabled={!!busy} onClick={()=>void restore(skill)}>{t('Undo')}</button></p>)}
   {editing&&!editing.skill&&listing&&<div className="dv-row dv-open dv-kn-editor"><div><p className="dv-row-name">New skill</p>
    <SkillEditor editing={editing} limits={listing.limits} busy={busy==='save'} onChange={patch=>setEditing({...editing,...patch})} onSave={save} onCancel={()=>setEditing(null)}/></div></div>}
   {listing&&!approved.length&&!(editing&&!editing.skill)&&<p className="dv-kn-line">No skills yet. Agents save one when they work out how to do a job here, or you can write one or import the SKILL.md files in {dirs.map((dir,index)=><span key={dir}>{index?' or ':''}<code>{dir}</code></span>)}.</p>}
  </div>
  <div className="dv-kn-add-more">
   <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy||!!editing} onClick={()=>setEditing({skill:null,name:'',description:'',body:''})}>New skill</button>
   <input ref={importInput} type="file" accept=".md,text/markdown,text/plain" className="hidden" aria-label="Import skill file" disabled={!!busy} onChange={event=>{
    const file=event.target.files?.[0];event.target.value='';if(!file)return;
    if(file.size>24000){setError('Choose a UTF-8 SKILL.md file up to 24 KB.');return;}
    void act('import-file',async()=>local<SkillListing>('skills_import_text',{...scope,text:await file.text()}),()=>notice('The skill is waiting for review above. A file’s skill doesn’t reach a chat until you approve it.'));
   }}/>
   <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void act('import',()=>local<SkillListing>('skills_import',scope),value=>{
    const got=value.imported;if(!got)return;
    if(!got.found){notice(`No SKILL.md files in ${dirs.join(' or ')}.`);return;}
    notice([got.added?`${got.added} ${got.added===1?'skill is':'skills are'} waiting for you above.`:'Nothing new to import.',
     got.skipped?`${got.skipped} already here.`:'',got.refused.length?`${got.refused.length} couldn’t be read.`:''].filter(Boolean).join(' '));
   })}>{busy==='import'?'Importing…':'Import from project'}</button>
   <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>importInput.current?.click()}>Import file</button>
  </div>
  {listing?.imported?.refused.length?<details className="dv-disclose"><summary>Why {listing.imported.refused.length} {listing.imported.refused.length===1?'file wasn’t':'files weren’t'} imported{CHEVRON}</summary>
   <ul>{listing.imported.refused.map(line=><li key={line}>{line}</li>)}</ul></details>:null}
  {listing&&listing.archived.filter(skill=>!deleted[skill.id]).length>0&&<details className="dv-disclose"><summary>Deleted · {listing.archived.filter(skill=>!deleted[skill.id]).length}{CHEVRON}</summary>
   <div className="dv-sheet">{listing.archived.filter(skill=>!deleted[skill.id]).map(skill=><div key={skill.id} className="dv-row">{SKILL_ICON}<div><div className="dv-row-name">{skill.description}</div><div className="dv-row-line"><span className="dv-kn-mono-name">{skill.name}</span></div></div>
    <div className="dv-row-end"><button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy}
     onClick={()=>void act(`restore:${skill.id}`,()=>local<SkillListing>('skills_restore',{...scope,skillId:skill.id,expectedRevision:skill.revision}),()=>notice(`${skill.name} is back. Chats about it get it again.`))}>Bring back</button></div></div>)}</div>
  </details>}
 </section>;
}
