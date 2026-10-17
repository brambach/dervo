import {useEffect,useRef,useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {ChevronDown,FolderOpen,GitBranch,GitMerge} from 'lucide-react';
import {local} from './api';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {shortPath} from './DervoSidebar';
import {Popover,PopoverContent,PopoverTrigger} from '@/shared/ui/popover';
import {isWindowsPlatform} from '@/shared/lib/platform';
import {t,tr} from '@/i18n';
import './ThreadFolder.css';
import {revealInFileManager} from '@/local/platformWords';

/** A thread's own branch and folder (beta.18, direction B; backend/thread_folders.py, mocks in
 * evidence/design-tests/worktrees-explore-20261005). Dervo picks per thread: its own branch when the thread will change
 * code, the project folder for explores, plans, notes and read-only questions, its own branch when it can't tell.
 *
 * - The chip on the composer says which, before the first send, and you can flip it until the thread's first run.
 * - The setup card shows above the composer while the folder is made, and when setup stops or asks before installing.
 *   The first message waits in the thread's queue until the folder is ready. Nothing runs before that.
 * - Every clean result in the thread's folder is committed to its branch. Merge is the one human step, always your
 *   click, and it makes a merge commit on the base. It never pushes: that stays the push confirm.
 *
 * Not on Windows in beta.18 (Bryce, 6 Oct 2026): Windows can't clone node_modules into a thread folder, so threads there
 * always work in the project folder (thread_folders.pick) and none of this shows. */
export const threadFoldersShown=()=>!isWindowsPlatform();
export type FolderStep={key:'space'|'branch'|'files'|'dependencies'|'ignored';state:'done'|'running'|'later'|'needs';line:string};
export type FolderDependency={lockfile:string;folder:string;command:string[];cwd:string;state:'cloned'|'linked'|'differs'|'absent'|'present'|'installed'|'skipped'};
export type BranchState={ahead:number;behind:number;clashes:string[];empty?:boolean;checked:number;error?:string};
export type ThreadFolderView={
 choice:'own'|'project';reason:string|null;state:'chosen'|'setting_up'|'needs_install'|'ready'|'failed'|'merged'|'archived';flippable:boolean;
 branch:string|null;base:string|null;base_commit:string|null;folder:string|null;project_folder:string;project_branch:string|null;repo:boolean;
 steps:FolderStep[]|null;error:{key:string;text:string;free?:number;needed?:number;estimate?:number}|null;dependencies:FolderDependency[]|null;
 install_error:string|null;merge:{sha:string;base:string;branch:string;commits:number;at:number;where:string|null}|null;left:string|null;
 suggested_branch:string|null;branch_state:BranchState|null;unknown:string|null;
};

/** Settled: read again every 30 s, and a thread you open again within 8 s shows what it had. Each read starts git in the host
 * (about 0.2 to 0.4 s for a thread on its own branch), and the host answers one request at a time. */
const SETTING_UP_MS=700,SETTLED_MS=30000,KEPT_MS=8000;
export const folderKey=(projectId:string,channelId:string)=>['thread-folder',projectId,channelId];

/** The thread's folder view. ``draft`` is the composer's text before the first send, so the chip shows the pick that
 * send would make. Polled quickly only while setup runs. */
export function useThreadFolder(projectId:string,channelId:string,draft?:string){
 const [text,setText]=useState(draft??'');
 useEffect(()=>{const timer=setTimeout(()=>setText(draft??''),500);return()=>clearTimeout(timer);},[draft]);
 return useQuery({queryKey:[...folderKey(projectId,channelId),text],enabled:!!projectId&&!!channelId&&threadFoldersShown(),placeholderData:previous=>previous,staleTime:KEPT_MS,
  queryFn:()=>local<ThreadFolderView>('thread_folder',{projectId,channelId,...(text.trim()?{text}:{})}),
  refetchInterval:query=>query.state.data?.state==='setting_up'?SETTING_UP_MS:SETTLED_MS});
}

/** Why Dervo picked what it did, in a sentence. */
export function reasonLine(reason:string|null):string{
 switch(reason){
  case 'code':return t('Dervo picked this because the message asks to build or change something.');
  case 'explore':return t('Explores, plans and notes stay in the project folder, so notes land where they do now.');
  case 'read_only':return t('A read-only question stays in the project folder.');
  case 'no_edit':return t('This thread can only read files, so it stays in the project folder.');
  case 'not_repo':return t('The project folder isn’t a git repository, so threads work there as today.');
  case 'no_commits':return t('The repository has no commits yet, so threads work in the project folder as today.');
  case 'started':return t('This thread ran in the project folder before, so it stays there.');
  case 'you':return t('You picked this.');
  default:return t('Dervo couldn’t tell from the message, so it picked its own branch. That’s the safe side.');
 }
}

/** The chip's words: the branch once there is one, otherwise the pick. */
export function chipLabel(view:ThreadFolderView):{text:string;mono:boolean}{
 if(view.choice==='own'&&view.branch&&!view.flippable)return {text:view.branch,mono:true};
 return {text:view.choice==='own'?t('Own branch'):t('Project folder'),mono:false};
}

export function ThreadFolderChip({projectId,channelId,draft}:{projectId:string;channelId:string;draft?:string}){
 const query=useThreadFolder(projectId,channelId,draft);
 const client=useQueryClient();
 const [open,setOpen]=useState(false),[error,setError]=useState('');
 const view=query.data;
 if(!view||!view.repo||!threadFoldersShown())return null;
 // The chip is a choice until the first run. After it, a thread in the project folder has nothing to say here, and one on
 // its own branch keeps only its icon, for the branch name and Reveal in Finder (Bryce, 8 Oct 2026: only keep what's
 // needed on the composer's bar). Setup, failure and Install show on the thread's folder card, not here.
 if(!view.flippable&&view.choice==='project')return null;
 const label=chipLabel(view);
 const iconOnly=!view.flippable;
 async function choose(choice:'own'|'project'){
  setError('');
  try{await local('thread_folder_choose',{projectId,channelId,choice});await client.invalidateQueries({queryKey:folderKey(projectId,channelId)});}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
 }
 async function reveal(){
  setError('');
  try{await local('thread_folder_reveal',{projectId,channelId});}catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
 }
 const branch=view.branch??view.suggested_branch;
 const base=view.base??view.project_branch;
 return <Popover open={open} onOpenChange={setOpen}>
  <PopoverTrigger asChild>
   <button type="button" className="dv-tf-chip" data-testid="thread-folder-chip" data-choice={view.choice} data-icon-only={iconOnly||undefined} aria-haspopup="dialog"
    aria-label={iconOnly?label.text:undefined}
    title={(label.mono?`${label.text}. `:'')+(view.choice==='own'?t('This thread works on its own branch, in its own folder'):t('This thread works in the project folder'))}>
    {view.choice==='own'?<GitBranch aria-hidden="true"/>:<FolderOpen aria-hidden="true"/>}
    {!iconOnly&&<span className={label.mono?'dv-tf-mono':undefined}>{label.text}</span>}
    {view.flippable&&<ChevronDown className="dv-tf-caret" aria-hidden="true"/>}
   </button>
  </PopoverTrigger>
  <PopoverContent align="start" side="top" sideOffset={8} className="dv-tf-pop" role="dialog" aria-label={t('Where this thread works')}>
   <h4>{t('Where this thread works')}</h4>
   {view.flippable?<div role="radiogroup" aria-label={t('Where this thread works')}>
    <button type="button" role="radio" aria-checked={view.choice==='own'} className="dv-tf-opt" data-testid="thread-folder-own" onClick={()=>void choose('own')}>
     <span className="dv-tf-radio" aria-hidden="true"/><span><b>{t('Own branch and folder')}</b>
      <small>{tr('{branch}. Each result commits there, and you merge it when it’s ready.',{branch:<code>{branch??'dervo/…'}</code>})}</small></span>
    </button>
    <button type="button" role="radio" aria-checked={view.choice==='project'} className="dv-tf-opt" data-testid="thread-folder-project" onClick={()=>void choose('project')}>
     <span className="dv-tf-radio" aria-hidden="true"/><span><b>{t('Project folder')}</b>
      <small>{view.project_branch?tr('{folder} on {branch}, shared with other threads, as today.',{folder:<code>{shortPath(view.project_folder)}</code>,branch:view.project_branch})
       :tr('{folder}, shared with other threads, as today.',{folder:<code>{shortPath(view.project_folder)}</code>})}</small></span>
    </button>
   </div>
   :<div className="dv-tf-fixed">
    {view.choice==='own'?<><p className="dv-tf-branch"><GitBranch aria-hidden="true"/><code>{view.branch}</code></p>
      {base&&<p className="dv-tf-from">{view.base_commit?t('From {base} at {commit}',{base,commit:view.base_commit.slice(0,7)}):t('From {base}',{base})}</p>}
      {view.state==='ready'&&<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm dv-tf-reveal" onClick={()=>void reveal()}><FolderOpen aria-hidden="true"/>{revealInFileManager()}</button>}</>
     :<p className="dv-tf-branch"><FolderOpen aria-hidden="true"/><code>{shortPath(view.project_folder)}</code></p>}
   </div>}
   {view.flippable&&view.choice==='own'&&base&&<p className="dv-tf-base"><span>{t('Branch from')}</span><code>{base}</code></p>}
   <p className="dv-tf-why">{reasonLine(view.reason)} {view.flippable?t('Set at the first send.'):t('Fixed at the first run, so its session stays with its folder.')}</p>
   {error&&<p role="alert" className="dv-tf-error">{error}</p>}
  </PopoverContent>
 </Popover>;
}

const STEP_NAMES:Record<FolderStep['key'],()=>string>={space:()=>t('Space'),branch:()=>t('Branch'),files:()=>t('Files'),
 dependencies:()=>t('Dependencies'),ignored:()=>t('Ignored files')};
const stepGlyph=(state:FolderStep['state']):GlyphState=>state==='done'?'done':state==='running'?'running':state==='needs'?'needs':'idle';

/** Above the composer: the folder being set up, setup that stopped, or the install question. Nothing for a thread in
 * the project folder, or once the folder is ready. */
export function ThreadFolderCard({projectId,channelId,onChanged}:{projectId:string;channelId:string;onChanged?:()=>unknown}){
 const query=useThreadFolder(projectId,channelId);
 const client=useQueryClient();
 const [busy,setBusy]=useState(''),[error,setError]=useState('');
 const alive=useRef(true);
 useEffect(()=>()=>{alive.current=false;},[]);
 const view=query.data;
 if(!view||view.choice!=='own')return null;
 const showing=view.state==='setting_up'||view.state==='failed'||view.state==='needs_install';
 if(!showing&&!view.left)return null;
 async function act(operation:string,args:Record<string,unknown>={}){
  if(busy)return;
  setBusy(operation);setError('');
  try{await local(operation,{projectId,channelId,...args});await Promise.all([client.invalidateQueries({queryKey:folderKey(projectId,channelId)}),onChanged?.()]);}
  catch(failure){if(alive.current)setError(failure instanceof Error?failure.message:String(failure));}
  finally{if(alive.current)setBusy('');}
 }
 if(!showing)return <div className="dv-tf-cards"><p className="dv-tf-left" role="status"><StatusGlyph state="needs"/>{view.left}</p></div>;
 const base=view.base??view.project_branch??'';
 const steps=view.steps??[];
 const stopped=view.state==='failed';
 const asking=view.state==='needs_install';
 const waiting=(view.dependencies??[]).filter(dep=>dep.state==='differs');
 const head:ReactNode=stopped?<><StatusGlyph state="needs" accent/><span>{t('Couldn’t set up')}</span></>
  :asking?<><StatusGlyph state="needs" accent/><span>{t('Install dependencies?')}</span></>
  :<><GitBranch aria-hidden="true"/><span>{t('Own branch from {base}',{base})}</span></>;
 const shown=stopped?steps.filter(step=>step.state!=='later'):steps;
 return <div className="dv-tf-cards">
  <section className="dv-tf-card" data-testid="thread-folder-card" data-state={view.state} aria-label={stopped?t('Setup stopped'):asking?t('Install dependencies?'):t('Setting up this thread')}>
   <div className="dv-tf-card-head">{head}{view.branch&&<code className="dv-tf-mono">{view.branch}</code>}</div>
   <p className="dv-tf-card-line">{stopped?t('Nothing ran and nothing changed.'):asking
    ?t('The thread’s lockfile differs from the project folder’s, so its dependencies can’t be cloned. Installing runs the project’s own code, so Dervo asks first.')
    :t('Its own folder, so other threads’ changes can’t mix with this one’s.')}</p>
   <ol className="dv-tf-steps">
    {shown.map(step=><li key={step.key} data-later={step.state==='later'||undefined} data-step={step.key}>
     <StatusGlyph state={stepGlyph(step.state)} accent={step.state==='needs'}/><b>{STEP_NAMES[step.key]()}</b><span>{step.line}</span></li>)}
   </ol>
   {stopped&&view.error&&view.error.key!=='space'&&<p className="dv-tf-why-stopped">{view.error.text}</p>}
   {asking&&waiting.map(dep=><p key={dep.lockfile} className="dv-tf-command">{tr('In {folder}:',{folder:<code>{dep.cwd||'.'}</code>})} <code>{dep.command.join(' ')}</code></p>)}
   {asking&&view.install_error&&<p className="dv-tf-why-stopped" role="alert">{view.install_error}</p>}
   {error&&<p className="dv-tf-why-stopped" role="alert">{error}</p>}
   <div className="dv-tf-card-foot">
    {!stopped&&!asking&&<span className="dv-caption">{t('Nothing has run yet.')}</span>}
    {stopped&&<span className="dv-caption">{view.error?.key==='space'?t('Free up space, or run this thread in the project folder.'):t('Your message waits here until this is sorted.')}</span>}
    {asking&&<span className="dv-caption">{t('Your message waits here until you choose.')}</span>}
    {asking&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy} onClick={()=>void act('thread_folder_install')}>{busy==='thread_folder_install'?t('Installing…'):t('Install')}</button>}
    {asking&&<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={!!busy} onClick={()=>void act('thread_folder_skip_install')}>{t('Go on without them')}</button>}
    {stopped&&<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={!!busy} onClick={()=>void act('thread_folder_retry')}>{t('Try again')}</button>}
    {view.flippable&&<button type="button" className={stopped||asking?'dv-btn dv-btn-ghost dv-btn-sm':'dv-btn dv-btn-quiet dv-btn-sm'} disabled={!!busy}
     onClick={()=>void act('thread_folder_choose',{choice:'project'})}>{t('Run in the project folder instead')}</button>}
   </div>
  </section>
 </div>;
}

/** Under the thread's latest result: the branch against its base, and Merge. Your click is the acceptance; Dervo makes a
 * merge commit on the base and removes the folder. The branch stays. Nothing is pushed. ``moved``: the latest result's
 * state and commit. A run ending commits to the branch, and an Undo reverts it, so the branch is read again at once
 * rather than at the next 30 s read. */
export function ThreadMerge({projectId,channelId,onChanged,moved}:{projectId:string;channelId:string;onChanged?:()=>unknown;moved?:string}){
 const query=useThreadFolder(projectId,channelId);
 const client=useQueryClient();
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const seen=useRef(moved);
 useEffect(()=>{
  if(seen.current===moved)return;
  seen.current=moved;
  void client.invalidateQueries({queryKey:folderKey(projectId,channelId)});
 },[moved,client,projectId,channelId]);
 const view=query.data;
 if(!view||view.choice!=='own')return null;
 const base=view.merge?.base??view.base??'';
 async function merge(){
  setBusy(true);setError('');
  try{await local('thread_folder_merge',{projectId,channelId});await Promise.all([client.invalidateQueries({queryKey:folderKey(projectId,channelId)}),onChanged?.()]);}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  finally{setBusy(false);}
 }
 if(view.state==='merged'&&view.merge){
  return <div className="dv-decision dv-tf-merge" data-decision="done" role="group" aria-label={t('Merge')} data-testid="thread-merge" data-state="merged">
   <div className="dv-decision-line"><StatusGlyph state="done"/><p className="dv-what" role="status"><b>{t('Merged into {base}.',{base})}</b> {t('Merge commit {sha}. Nothing was pushed, and the branch stays.',{sha:view.merge.sha.slice(0,7)})}</p></div>
  </div>;
 }
 if(view.state!=='ready')return null;
 if(view.unknown)return <div className="dv-decision dv-tf-merge" data-decision="unknown" role="group" aria-label={t('Merge')} data-testid="thread-merge" data-state="unknown">
  <div className="dv-decision-line"><StatusGlyph state="unknown"/><p className="dv-what" role="status"><b>{t('Unknown folder.')}</b> {view.unknown} {t('Nothing runs here until it’s set up again.')}</p></div>
 </div>;
 const state=view.branch_state;
 if(!state||state.error||state.ahead<1)return null;
 if(state.empty)return <div className="dv-decision dv-tf-merge" data-decision="done" role="group" aria-label={t('Merge')} data-testid="thread-merge" data-state="empty">
  <div className="dv-decision-line"><StatusGlyph state="done"/><p className="dv-what" role="status"><b>{t('Nothing to merge.')}</b> {t('The branch’s changes were undone, so {base} would stay the same.',{base})}</p></div>
 </div>;
 const clashes=state.clashes??[];
 const ahead=t('{count} commits ahead of {base}',{count:state.ahead,base});
 return <div className="dv-decision dv-tf-merge" data-decision="needs" role="group" aria-label={t('Merge')} data-testid="thread-merge" data-state={clashes.length?'clash':'ready'}>
  <div className="dv-decision-line"><StatusGlyph state="needs" accent/>
   <p className="dv-what" role="status">{clashes.length
    ?<><b>{t('Can’t merge yet.')}</b> {tr('{base} also changed {files}.',{base,files:<>{clashes.slice(0,3).map((file,i)=><span key={file}>{i?', ':''}<code>{file}</code></span>)}{clashes.length>3?` ${t('and {count} more',{count:clashes.length-3})}`:''}</>})}</>
    :<><b>{t('Committed to its branch.')}</b> {t('{ahead}, no clashes.',{ahead})}</>}</p></div>
  <button type="button" className="dv-btn dv-btn-primary dv-tf-merge-button" data-testid="thread-merge-button" disabled={busy||!!clashes.length}
   title={clashes.length?t('Off until the clash is resolved'):t('Merges this branch into {base} on this Mac. Nothing is pushed.',{base})} onClick={()=>void merge()}>
   <GitMerge aria-hidden="true"/>{busy?t('Merging…'):t('Merge into {base}',{base})}</button>
  {error&&<p role="alert" className="dv-decision-note dv-decision-error">{error}</p>}
 </div>;
}

/** Project settings, Access: the ignored files each new thread folder gets a clone of. Git ignores them, so a new folder
 * wouldn't have them. Only what you tick is copied, and secret-named files are never suggested. */
export function ThreadFolderSettings({projectId}:{projectId:string}){
 const client=useQueryClient();
 const key=['thread-folder-settings',projectId];
 const query=useQuery({queryKey:key,enabled:!!projectId&&threadFoldersShown(),queryFn:()=>local<{approved:string[];candidates:string[];root:string}>('thread_folder_settings',{projectId})});
 const [error,setError]=useState('');
 const data=query.data;
 // Nothing to show until the host answers with the list (an older host, or a test's bare {}, doesn't).
 if(!data||!Array.isArray(data.approved))return null;
 const rows=[...new Set([...data.approved,...(data.candidates??[])])].sort();
 async function set(path:string,on:boolean){
  setError('');
  const paths=on?[...new Set([...(data?.approved??[]),path])]:(data?.approved??[]).filter(item=>item!==path);
  try{const next=await local<{approved:string[];candidates:string[];root:string}>('thread_folder_ignored_set',{projectId,paths});client.setQueryData(key,next);}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
 }
 return <div className="dv-tf-settings" data-testid="thread-folder-settings">
  <p className="dv-sec-note">{t('A thread that changes code gets its own branch and folder. Every finished result commits to that branch, and Merge brings it into the branch it came from. Nothing is pushed.')}</p>
  <p className="dv-tf-files-head"><b>{t('Ignored files each thread needs')}</b><span>{t('Git ignores these, so a new folder wouldn’t have them. Secret-named files are never suggested.')}</span></p>
  {rows.length?<ul className="dv-tf-files">{rows.map(path=><li key={path}><label>
   <input type="checkbox" checked={data.approved.includes(path)} onChange={event=>void set(path,event.target.checked)}/>
   <code>{path}</code><small>{data.approved.includes(path)?t('Cloned into each thread folder'):t('Not copied')}</small></label></li>)}</ul>
  :<p className="dv-caption">{t('Nothing ignored in this project folder needs copying.')}</p>}
  <p className="dv-caption">{tr('Thread folders live in {root}, never in the project folder.',{root:<code>{shortPath(data.root)}</code>})}</p>
  {error&&<p role="alert" className="dv-tf-error">{error}</p>}
 </div>;
}

