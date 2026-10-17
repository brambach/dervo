import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {local} from './api';
import {SettingRow,SettingsSection,TextField} from './DervoControls';
import {projectName} from './projectDisplay';
import {useSaver,type SettingsProject} from './projectSettingsData';
import {FieldError,Points} from './SettingsBits';
import {LifecycleBlockers,type OpenBlocker} from './LifecycleBlockers';
import {OutsideSessionsSetting} from './OutsideSessions';
import {t,tr} from '@/i18n';
import {showInFileManager} from '@/local/platformWords';

/** General (settings-general*.html): the project's name, edited in place, its folder, previews, and what happens when
 * you're done with it. Archive confirms in place; Delete asks you to type the project's name and says what goes and
 * what stays. Renaming saves on Return or when you leave the field. */

const COPY=<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5.5" y="5.5" width="7.5" height="7.5" rx="1.5"/><path d="M3 10.5V4a1 1 0 011-1h6.5"/></svg>;
const shortPath=(cwd:string)=>cwd.replace(/^\/Users\/[^/]+(?=\/|$)/,'~');

/** Delete a project (app #12, 23 Sep): the typed name is the whole confirmation. The host stops anything still running
 * and answers with how many runs are still closing, so this asks again until none are, then the records go. A Stop
 * gives a coding run or a chat reply 10 seconds to exit before the host ends it; a result arriving as it stops can add
 * 30 more, so the wait is bounded well past both. ``cancelled``: Keep it (or Esc) while runs are still stopping ends the
 * wait with null and nothing is deleted; before, the confirm closed and the next ask deleted the project anyway
 * (design review, 23 Sep). The runs it already stopped stay stopped, as the confirm said they would be. */
export const DELETE_WAIT_MS=60_000;
export async function deleteProject(projectId:string,onStopping:(count:number)=>void,{wait=(ms:number)=>new Promise<void>(resolve=>window.setTimeout(resolve,ms)),now=()=>Date.now(),cancelled=()=>false}:{wait?:(ms:number)=>Promise<void>;now?:()=>number;cancelled?:()=>boolean}={}){
  const until=now()+DELETE_WAIT_MS;
  for(;;){
    const result=await local<{channelIds?:string[];stopping?:number}>('project_delete',{projectId,confirmation:'DELETE '+projectId});
    if(!result.stopping)return {channelIds:result.channelIds??[]};
    onStopping(result.stopping);
    if(now()>=until)throw new Error(t('{count} runs are still stopping, so the project wasn’t deleted yet. What’s stopped stays stopped. Try again in a moment.',{count:result.stopping}));
    await wait(1000);
    if(cancelled())return null;
  }
}

export function GeneralPage({project,onChanged,onPreview,onOpenBlocker}:{project:SettingsProject;onChanged:(removed?:string,channels?:string[])=>Promise<unknown>;onPreview:()=>void;onOpenBlocker?:OpenBlocker}){
  const saver=useSaver();
  const name=projectName(project);
  const [copied,setCopied]=useState<''|'copied'|'failed'>('');
  const [confirm,setConfirm]=useState<null|'archive'|'delete'>(null),[typed,setTyped]=useState(''),[stopping,setStopping]=useState(0);
  const typedRef=useRef<HTMLInputElement>(null),deleteRef=useRef<HTMLButtonElement>(null),kept=useRef(false);
  const keep=()=>{kept.current=true;setConfirm(null);};
  const preview=useQuery({queryKey:['preview',project.id],queryFn:()=>local<{supported:boolean}>('preview_status',{projectId:project.id}),staleTime:60000});
  useEffect(()=>{if(confirm==='delete')requestAnimationFrame(()=>typedRef.current?.focus());},[confirm]);
  async function copy(){
    try{await navigator.clipboard.writeText(project.cwd);setCopied('copied');window.setTimeout(()=>setCopied(''),1600);}
    catch{setCopied('failed');}
  }
  const matches=typed.trim()===name;
  return <>
    <SettingsSection id="proj" title={t('Project')} caption={saver.caption('proj')}>
      <SettingRow name={t('Name')} line={t('1 to 80 characters on one line.')} end={<TextField className="dv-name-field" label={t('Project name')} value={name}
        onCommit={async next=>{const clean=next.trim();if(!clean||clean.length>80||/[\n\r]/.test(next))return t('Use a project name with 1 to 80 characters on one line.');
          if(clean===name)return;let problem='';
          await saver.save('proj','name',async()=>{try{await local('project_rename',{projectId:project.id,name:clean});}catch(failure){problem=failure instanceof Error?failure.message:String(failure);throw failure;}await onChanged();});
          return problem||undefined;}}/>}/>
      <SettingRow name={t('Folder')} line={<code>{project.cwd}</code>} end={<>
        <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void copy()} aria-live="polite">{COPY}{copied==='copied'?t('Copied'):copied==='failed'?t('Select and copy it instead'):t('Copy path')}</button>
        <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void saver.save('proj','reveal',()=>local('project_reveal',{projectId:project.id}))}>{showInFileManager()}</button></>}/>
      <FieldError message={saver.error('reveal')}/>
      {preview.data?.supported&&<SettingRow name={t('Build and preview')} line={t('Build this copy of Dervo and try it in its own window before you switch.')}
        end={<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onPreview}>{t('Open previews')}</button>}/>}
    </SettingsSection>
    <OutsideSessionsSetting projectId={project.id}/>
    <SettingsSection id="done" title={t('When you’re done with it')}>
      <SettingRow name={t('Archive')} line={t('Hides it from the project picker. Everything is kept, and you can restore it there.')}
        end={confirm!=='archive'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={saver.busy} onClick={()=>{setConfirm('archive');saver.clear('archive');}}>{t('Archive')}</button>}
        below={confirm==='archive'&&<div className="dv-confirm dv-project-confirm" role="group" aria-label={t('Archive project')} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();setConfirm(null);}}}><p className="dv-set-line">{t('Archive {name}? It leaves the project picker. Restore it from Archived projects.',{name})}</p>
          <div className="dv-actions"><button type="button" className="dv-btn dv-btn-primary dv-btn-sm" autoFocus disabled={saver.busy}
            onClick={()=>void saver.save('done','archive',async()=>{await local('project_archive',{projectId:project.id});await onChanged(project.id);})}>{t('Archive')}</button>
            <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setConfirm(null)}>{t('Keep it')}</button></div></div>}/>
      <FieldError message={saver.error('archive')}/>
      {confirm==='delete'?<div className="dv-choice dv-open dv-delete" role="group" aria-label={t('Delete project')} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();keep();}}}>
        <span/><div><p className="dv-c-name">{t('Delete {name} for good?',{name})}</p>
          <Points items={[<>{t('Its threads, tasks, knowledge and settings are removed from Dervo. This can’t be undone.')}</>,
            <>{t('Anything still running here is stopped first. Its edits so far stay in your folder.')}</>,
            <>{tr('Your folder at {folder}, its git history and your accounts aren’t touched.',{folder:<code>{shortPath(project.cwd)}</code>})}</>,<>{t('To keep it for later, archive it instead.')}</>]}/>
          <label className="dv-typed"><span className="dv-set-line">{tr('Type {name} to confirm',{name:<b>{name}</b>})}</span>
            <input ref={typedRef} className="dv-input" aria-label={t('Type the project name to confirm')} value={typed} onChange={event=>setTyped(event.target.value)}
              onKeyDown={event=>{if(event.key==='Enter'&&matches){event.preventDefault();deleteRef.current?.click();}}}/></label>
          <div className="dv-actions"><button ref={deleteRef} type="button" className="dv-btn dv-btn-ghost dv-btn-sm dv-delete-go" disabled={!matches||saver.busy}
            onClick={()=>{kept.current=false;void saver.save('done','delete',async()=>{const result=await deleteProject(project.id,setStopping,{cancelled:()=>kept.current});
              if(!result)return;
              try{localStorage.removeItem('dervo-explore-pending:'+project.id);}catch{/* nothing to clear */}await onChanged(project.id,result.channelIds);}).finally(()=>setStopping(0));}}>{stopping?t('Stopping {count} runs…',{count:stopping}):saver.saving('done')?t('Deleting…'):t('Delete {name}',{name})}</button>
            <button type="button" className="dv-btn dv-btn-quiet" onClick={keep}>{t('Keep it')}</button></div>
          <FieldError message={saver.error('delete')}/></div><span/></div>
        :<SettingRow name={t('Delete')} line={t('Removes its threads, tasks and knowledge from Dervo. Your folder and accounts stay.')}
          end={<button type="button" className="dv-btn dv-btn-quiet" disabled={saver.busy} onClick={()=>{setTyped('');setConfirm('delete');saver.clear('delete');}}>{t('Delete project…')}</button>}/>}
    </SettingsSection>
    {onOpenBlocker&&(saver.error('archive')||saver.error('delete'))&&<LifecycleBlockers scope={{scope:'project',projectId:project.id,channelId:null}} onOpen={onOpenBlocker}/>}
  </>;
}
