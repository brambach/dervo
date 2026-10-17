import {useEffect,useRef,useState,type ReactNode} from 'react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from '@/shared/ui/dialog';
import {invoke} from '@tauri-apps/api/core';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuLabel,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {local,type CodingPermission} from './api';
import {ChoiceRows,SettingRow,SettingsSection,Segmented,Switch,Tag,type Choice} from './DervoControls';
import {StatusGlyph} from './StatusGlyph';
import {folderInside} from './CodingPermission';
import {currentWhere,grantArgs,shortDate,useProjectPermission,useSaver,useSettingsRefresh,type SettingsProject,type Where} from './projectSettingsData';
import {Disclose,FieldError,Points} from './SettingsBits';
import {RunLimitsSection} from './SettingsLimits';
import {ThreadFolderSettings,threadFoldersShown} from './ThreadFolder';
import {isWindowsPlatform} from '@/shared/lib/platform';
import {t,tr} from '@/i18n';

/** Access (settings-access*.html): what agents can do here, as the three-step ladder both Claude Code and Codex use.
 * Read and explain, Edit files, Full access. The chosen level lifts onto the page's one card with its details. Full
 * access states its four risks before it turns on and while it's on. A stale grant says what's gone, with the two
 * fixes beside it. Picking a level saves it; the safer direction never asks. */

type Level='read'|'edit'|'full';
const FILE_LIMIT=32,FOLDER_LIMIT=8;
/** b16/windows milestone 4: the same words on Windows, about a PC. */
const MAC=isWindowsPlatform()?'PC':'Mac',OS=isWindowsPlatform()?'Windows':'macOS';
const PLUS=<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9"/></svg>;
export const FULL_ACCESS_POINTS:ReactNode[]=[
  // Beta.15 (Bryce, 24 Sep): testers can turn this on too, so it names the risk to their own Mac in plain words.
  t('Commands run as you, without a sandbox, on your own {computer}. They can reach files, networks and accounts outside this folder, and read the API keys and sign-ins saved on this {computer}, Dervo’s included.',{computer:MAC}),
  // Design review S9 (24 Sep): "it" had nothing to point at after a sentence about commands.
  t('A web page the agent reads can try to steer it.'),
  t('Connector actions, like sending a message or creating an event, happen at once. Rejecting the result doesn’t undo them.'),
  t('Dervo still records every changed file from git and waits for your approval before committing.'),
];
/** Security review N2 (24 Sep): with full access on, Home's unclicked jobs get this project's allowed connectors
 * (conversation_coding.home_connectors). Claude Code only: Codex gets no connectors in Dervo. */
export const HOME_JOBS_POINT=t('Home’s own jobs here, the ones you didn’t click, can use this project’s allowed connectors too, without the shell.');
export const fullAccessPoints=(provider?:string):ReactNode[]=>provider==='codex'?FULL_ACCESS_POINTS
  :[...FULL_ACCESS_POINTS.slice(0,3),HOME_JOBS_POINT,...FULL_ACCESS_POINTS.slice(3)];

export function levelOf(permission?:CodingPermission):Level{
  if(!permission||permission.state==='missing'||permission.state==='unsupported')return 'read';
  return permission.full_access?.enabled?'full':'edit';
}
/** A connector's name as the Claude account lists it, without the "claude.ai " prefix it carries. */
const connectorLabel=(name:string)=>name.replace(/^claude\.ai /,'');

export function AccessPage({project,onChecks,channelId=null}:{project:SettingsProject;onChecks?:()=>void;channelId?:string|null}){
  const permission=useProjectPermission(project.id,channelId);
  const saver=useSaver(),refresh=useSettingsRefresh(project.id,channelId);
  const [confirming,setConfirming]=useState<null|'full'|'stop'|'computer'>(null);
  const [draft,setDraft]=useState<Where|null>(null);
  // A confirm takes focus when it opens, on the button that says yes (interactions.md, Access).
  const confirmRef=useRef<HTMLButtonElement>(null);
  useEffect(()=>{if(confirming)requestAnimationFrame(()=>confirmRef.current?.focus());},[confirming]);
  const data=permission.data;
  const level=levelOf(data);
  const where=draft??currentWhere(data);
  const full=data?.full_access;
  const fullWhy=full?.unavailable??null;
  const custom=!!data&&data.state!=='missing'&&!data.project_access;

  async function grant(next:Where,key='where'){
    const view=await saver.save('can',key,async()=>refresh(await local<CodingPermission>('coding_permission_grant',{projectId:project.id,channelId,...grantArgs(data,next)})));
    if(view!==undefined)setDraft(null);
  }
  async function revoke(){await saver.save('can','level',async()=>refresh(await local<CodingPermission>('coding_permission_revoke',{projectId:project.id,channelId})));setConfirming(null);}
  // Turning it on sends the warning's token from the view (security review N4): the host refuses it without one, so the
  // confirm under the warning is the only way on. Off needs nothing.
  async function setFull(enabled:boolean){await saver.save('can','level',async()=>refresh(await local<CodingPermission>('coding_full_access_set',{projectId:project.id,channelId,enabled,...(enabled?{warning:full?.warning}:{})})));setConfirming(null);}
  async function setCommands(enabled:boolean){await saver.save('can','commands',async()=>refresh(await local<CodingPermission>('coding_commands_set',{projectId:project.id,channelId,enabled})));}
  async function setAutoCommit(enabled:boolean){await saver.save('can','autoCommit',async()=>refresh(await local<CodingPermission>('coding_auto_commit_set',{projectId:project.id,channelId,enabled})));}
  async function setComputer(enabled:boolean){await saver.save('can','computer',async()=>refresh(await local<CodingPermission>('coding_computer_access_set',{projectId:project.id,channelId,enabled})));setConfirming(null);}
  function pick(next:Level){
    saver.clear('level');
    if(next==='read'){if(custom&&level!=='read'){setConfirming('stop');return;}void revoke();return;}
    if(next==='edit'){setConfirming(null);if(level==='full')void setFull(false);else if(level==='read')void grant({projectAccess:true},'level');return;}
    setConfirming('full');
  }
  // The path list: a file or folder joins it, or leaves it, and the grant saves each time. A list needs one path.
  function setPaths(files:string[],folders:string[]){
    const next={projectAccess:false,files,folders};
    if(!files.length&&!folders.length){setDraft(next);return;}
    void grant(next);
  }
  async function addFolder(){
    saver.clear('where');
    try{
      const chosen=await invoke<string|null>('choose_folder_in',{root:project.cwd});
      if(!chosen)return;
      const relative=folderInside(project.cwd,chosen);
      if(!relative){saver.fail('where',t('Choose something inside this project, not the project itself or a folder outside it.'));return;}
      if(!(where.folders??[]).includes(relative))setPaths(where.files??[],[...(where.folders??[]),relative]);
    }catch(failure){saver.fail('where',failure);}
  }

  const stale=data?.state==='stale';
  const gone=[...(data?.unusable_files??[]),...(data?.unusable_folders??[])];
  const checkProblem=stale&&!gone.length&&/check can/i.test(data?.detail??'');
  const problem=stale&&<div className="dv-problem">
    <StatusGlyph state="needs" accent/>
    <div><p className="dv-set-name">{t('Agents can’t start here right now')}</p>
      <p className="dv-set-line">{gone.length?gone.length>1?tr('{path} and {more} more aren’t in the project any more, and they’re on the list below.',{path:<code>{gone[0]}</code>,more:gone.length-1})
        :tr('{path} isn’t in the project any more, and it’s on the list below.',{path:<code>{gone[0]}</code>})
        :/folder changed|folder was replaced/i.test(data?.detail??'')?t('The project folder changed since coding was allowed.'):data?.detail}</p>
      <div className="dv-actions">
        {!checkProblem&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={saver.saving('can')} onClick={()=>void grant({projectAccess:true},'fix')}>{t('Use the whole folder')}</button>}
        {gone.length>0&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={saver.saving('can')}
          onClick={()=>void grant({projectAccess:false,files:(data?.files??[]).filter(f=>!gone.includes(f)),folders:(data?.folders??[]).filter(f=>!gone.includes(f))},'fix')}>{t('Take it off the list')}</button>}
        {checkProblem&&onChecks&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onChecks}>{t('Open Checks and review')}</button>}
      </div>
      <FieldError message={saver.error('fix')}/></div>
  </div>;

  const pathRow=(path:string,kind:'file'|'folder')=><p key={`${kind}:${path}`} className="dv-path"><code>{path}</code>
    {gone.includes(path)?<span className="dv-caption">{t('Missing')}</span>:<button type="button" className="dv-btn dv-btn-quiet dv-path-remove" disabled={saver.saving('can')} aria-label={t('Remove {path}',{path})}
      onClick={()=>kind==='file'?setPaths((where.files??[]).filter(f=>f!==path),where.folders??[]):setPaths(where.files??[],(where.folders??[]).filter(f=>f!==path))}>{t('Remove')}</button>}</p>;
  const addable=(data?.candidates??[]).filter(name=>!(where.files??[]).includes(name));
  const editBody=<>
    {problem}
    <SettingRow name={t('Where')} line={where.projectAccess?t('Hidden files, credentials, databases, links and generated folders stay out.'):undefined}
      end={<Segmented label={t('Where agents can edit')} value={where.projectAccess?'whole':'some'} disabled={saver.saving('can')}
        onChange={value=>{if(value==='whole')void grant({projectAccess:true});else setDraft({projectAccess:false,files:data?.project_access?[]:data?.files??[],folders:data?.project_access?[]:data?.folders??[]});}}
        options={[{value:'whole',label:t('Whole folder')},{value:'some',label:t('Only some files and folders')}]}/>}/>
    {!where.projectAccess&&<div className="dv-paths">
      {!!where.files?.length&&<><p className="dv-label">Files</p>{where.files.map(f=>pathRow(f,'file'))}</>}
      {!!where.folders?.length&&<><p className="dv-label">{t('Folders')}</p>{where.folders.map(f=>pathRow(f,'folder'))}</>}
      {!where.files?.length&&!where.folders?.length&&<p className="dv-set-line">{t('Add at least one file or folder to save this.')}</p>}
      <DropdownMenu><DropdownMenuTrigger asChild>
        <button type="button" className="dv-btn dv-btn-quiet dv-add" disabled={saver.saving('can')||((where.files?.length??0)>=FILE_LIMIT&&(where.folders?.length??0)>=FOLDER_LIMIT)}>{PLUS}{t('Add a file or folder')}</button>
      </DropdownMenuTrigger><DropdownMenuContent align="start">
        {(where.folders?.length??0)<FOLDER_LIMIT&&<DropdownMenuItem onSelect={()=>void addFolder()}>{t('Choose a folder…')}</DropdownMenuItem>}
        {addable.length>0&&(where.files?.length??0)<FILE_LIMIT&&<><DropdownMenuSeparator/><DropdownMenuLabel>{t('Files at the top of the project')}</DropdownMenuLabel>
          {addable.slice(0,20).map(name=><DropdownMenuItem key={name} onSelect={()=>setPaths([...(where.files??[]),name],where.folders??[])}>{name}</DropdownMenuItem>)}</>}
      </DropdownMenuContent></DropdownMenu>
      {((where.files?.length??0)>=FILE_LIMIT||(where.folders?.length??0)>=FOLDER_LIMIT)&&<p className="dv-set-line">{t('That’s the most: {files} files and {folders} folders. Use the whole folder for more.',{files:FILE_LIMIT,folders:FOLDER_LIMIT})}</p>}
      {!!where.folders?.length&&<p className="dv-set-line">{data?.folder_risk??t('Agents may read any regular file in a folder you add. Add only folders whose contents they may see.')}</p>}
    </div>}
    <FieldError message={saver.error('where')}/>
    {data?.created_at&&data.state!=='missing'&&<p className="dv-caption">{t('Allowed by you on {date}.',{date:shortDate(data.created_at)})}</p>}
  </>;
  const stopBody=confirming==='stop'&&<div className="dv-confirm dv-project-confirm" onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();setConfirming(null);}}}><p className="dv-set-name">{t('Stop editing here?')}</p>
    <p className="dv-set-line">{t('Your list of {files} and {folders} is forgotten.',{files:t('{count} files',{count:data?.files.length??0}),folders:t('{count} folders',{count:data?.folders?.length??0})})}</p>
    <div className="dv-actions"><button ref={confirmRef} type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>void revoke()}>{t('Stop editing')}</button><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setConfirming(null)}>{t('Cancel')}</button></div></div>;
  const names=full?.connectors?.names??null,allowed=full?.connectors?.allowed??null;
  const on=(name:string)=>allowed===null||allowed.includes(name);
  const fullBody=confirming==='full'?<div className="dv-confirm dv-project-confirm" onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();setConfirming(null);}}}><p className="dv-set-name">{t('Turn on full access for {name}?',{name:project.name||t('this project')})}</p><Points items={fullAccessPoints(full?.provider)}/>
    <div className="dv-actions"><button ref={confirmRef} type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={saver.saving('can')} onClick={()=>void setFull(true)}>{t('Turn on full access')}</button>
      <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setConfirming(null)}>{t('Cancel')}</button></div></div>
    :level==='full'&&<>
      {/* With agent commands off, "Commands run as you" isn't true of this project, so that point goes; the switch below
          says what Off means (design review, 23 Sep). The confirm above keeps all four: commands start on. */}
      <Points items={fullAccessPoints(full?.provider).slice(full?.commands===false?1:0)}/>
      {/* Beta.15 (Bryce, 23 Sep): agents may run commands without a click, per project, on by default with full access. */}
      <SettingRow name={t('Agents run commands')} line={full?.commands!==false?t('Builds, tests and previews run without asking. Commands in a reply still have Run.')
        :full?.provider==='codex'?t('Off: Codex edits files without running commands. Commands in its reply have Run, and ones that change files ask first.')
        :t('Off: Claude Code edits files without running commands. Commands in its reply have Run, and ones that change files ask first.')}
        end={<Switch label={t('Agents run commands')} checked={full?.commands!==false} disabled={saver.saving('can')||!full?.active} onChange={enabled=>void setCommands(enabled)}/>}/>
      <FieldError message={saver.error('commands')}/>
      {/* 16.2 (Bryce, 27 Sep): full access can finish the job, commit included. Off by default, per project. */}
      <SettingRow name={t('Commit results automatically')} line={full?.auto_commit?t('A finished result is approved and committed without waiting for you, when Dervo recorded every file. Undo reverts it.')
        :t('Off: each finished result waits for you to approve and commit it.')}
        end={<Switch label={t('Commit results automatically')} checked={full?.auto_commit===true} disabled={saver.saving('can')||!full?.active} onChange={enabled=>void setAutoCommit(enabled)}/>}/>
      <FieldError message={saver.error('autoCommit')}/>
      <SettingRow name={t('Files across this {computer}',{computer:MAC})} line={t('Claude Code only. Includes Downloads and other folders your {computer} account can access. This permission stays with this project and its chosen account.',{computer:MAC})}
        end={<Switch label={t('Files across this {computer}',{computer:MAC})} checked={full?.computer_access===true} disabled={saver.saving('can')||!full?.active||full?.computer_available===false}
          onChange={enabled=>{if(enabled)setConfirming('computer');else void setComputer(false);}}/>}/>
      {confirming==='computer'&&<div className="dv-confirm dv-project-confirm" onKeyDown={event=>{if(event.key==='Escape')setConfirming(null);}}>
        <p className="dv-set-name">{t('Allow files outside this project?')}</p>
        <p className="dv-set-line">{t('Claude Code can read and change files across this {computer} for your requests, including personal and work folders. Dervo can’t undo changes outside this project. {os} permissions still apply. Stop any running task before turning this off.',{computer:MAC,os:OS})}</p>
        <div className="dv-actions"><button ref={confirmRef} type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={saver.saving('can')} onClick={()=>void setComputer(true)}>{t('Allow computer files')}</button>
          <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setConfirming(null)}>{t('Cancel')}</button></div>
      </div>}
      <FieldError message={saver.error('computer')}/>
      {full&&!full.active&&full.unavailable&&<p className="dv-why">{t('On, but not in use right now: {why}',{why:full.unavailable})}</p>}
      {full?.provider==='codex'?<p className="dv-set-line">{t('Codex connectors and computer tools aren’t enabled by full access.')}</p>:<><SettingRow name={t('Connectors Claude Code can use here')} line={allowed===null?t('All connectors from this Claude account are allowed, including ones you add later. Pick a specific list to limit access.'):t('From your Claude account. One you add later stays off here until you turn it on.')}
        end={names&&names.length>0?<span className="dv-caption">{t('{on} of {all} on',{on:names.filter(on).length,all:names.length})}</span>:undefined}/>
      {names===null?<p className="dv-set-line" role="status">{t('Looking for your connectors. This takes a few seconds the first time.')}</p>
        :!names.length?<p className="dv-set-line">{t('No MCP servers or claude.ai connectors found for this Claude account.')}</p>
        :<div className="dv-conns">{names.map(name=><label key={name} className="dv-conn"><span>{connectorLabel(name)}</span>
          <Switch label={connectorLabel(name)} checked={on(name)} disabled={saver.saving('can')}
            onChange={next=>void saver.save('can','connectors',async()=>refresh(await local<CodingPermission>('coding_connectors_set',{projectId:project.id,channelId,
              connectors:(next?[...(allowed??names),name]:(allowed??names).filter(n=>n!==name)).filter((n,i,all)=>all.indexOf(n)===i)})))}/></label>)}</div>}
      <FieldError message={saver.error('connectors')}/></>}
      {full?.created_at&&<p className="dv-caption">{t('Turned on by you on {date}.',{date:shortDate(full.created_at)})}</p>}
    </>;

  const choices:Choice<Level>[]=[
    {value:'read',name:t('Read and explain'),line:t('Reads the project and answers questions. Changes nothing.'),
      body:confirming==='stop'?stopBody:<p className="dv-set-line">{t('To let agents change files, pick Edit files. Nothing is granted until you do.')}</p>},
    {value:'edit',name:t('Edit files'),line:t('Reads, edits and creates files in this folder.'),body:editBody},
    {value:'full',name:<>{t('Full access')}<Tag>{full?.provider==='codex'?'Codex':'Claude Code'}</Tag></>,line:<>{t('Also runs commands as you: installs, builds, tests, previews and web pages.')}{fullWhy&&level!=='full'&&confirming!=='full'&&<span className="dv-why">{fullWhy}</span>}
      {/* Security review N4: a record from before beta.15's warning reads as off until it's confirmed again. */}
      {!fullWhy&&full?.reconfirm&&level!=='full'&&confirming!=='full'&&<span className="dv-why">{t('Off since this update, which changed the warning. Turn it on again to confirm it.')}</span>}</>,
      disabled:!!fullWhy&&level!=='full',body:fullBody||undefined},
  ];
  return <>
    {permission.isError&&<FieldError message={t('This project’s access couldn’t load: {why}',{why:permission.error instanceof Error?permission.error.message:String(permission.error)})} onRetry={()=>void permission.refetch()}/>}
    <SettingsSection id="can" title={t('What agents can do')} caption={saver.caption('can',t('Applies to new requests'))}>
      <p className="dv-sec-note">{channelId?t('These settings use this thread’s connected account. Full access stays with that account when you change models. A different account needs its own grant.'):t('These settings use the project’s default account. For a thread on another account, use its access button beside the model. Full access stays with the connected account when you change models.')}</p>
      {!data&&!permission.isError?<p className="dv-sec-note" role="status">{t('Loading this project’s access…')}</p>:<>
        {data?.state==='unsupported'?<p className="dv-why">{data.detail}</p>:<>
          <ChoiceRows label={t('What agents can do')} value={level} lift={confirming==='full'?'full':confirming==='stop'?'read':level} open choices={choices} onChange={pick}/>
        </>}
        <FieldError message={saver.error('level')}/>
      </>}
      <Disclose summary={t('What no level allows')} className="dv-never"><p>{t('Dervo never shows an agent your passwords or API keys, deploys for you, changes your accounts or spends money. With full access, the provider’s own commands can still reach what your {computer} user can, including the API keys and Dervo sign-in saved on this {computer}.',{computer:MAC})}</p></Disclose>
    </SettingsSection>
    <RunLimitsSection project={project} channelId={channelId}/>
    {/* Beta.18: the ignored files each thread folder gets (thread_folders.py). Per project, so not in a thread's dialog.
        Not on Windows, where threads always work in the project folder for now. */}
    {!channelId&&threadFoldersShown()&&<SettingsSection id="thread-branches" title={t('Thread branches')} caption={t('Applies to new threads')}><ThreadFolderSettings projectId={project.id}/></SettingsSection>}
  </>;
}


/** The same access controls, using the thread's binding instead of the project's default account. */
export function ThreadAccessDialog({project,channelId,accountName,open,onOpenChange}:{project:SettingsProject;channelId:string;accountName:string;open:boolean;onOpenChange:(open:boolean)=>void}){
 const returnFocus=useRef(document.activeElement instanceof HTMLElement?document.activeElement:null);
 return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl" onCloseAutoFocus={event=>{if(returnFocus.current?.isConnected){event.preventDefault();returnFocus.current.focus();}}}>
  <DialogTitle>{t('Thread access')}</DialogTitle><DialogDescription>{accountName} · {project.model}</DialogDescription>
  <AccessPage project={project} channelId={channelId}/>
 </DialogContent></Dialog>;
}
