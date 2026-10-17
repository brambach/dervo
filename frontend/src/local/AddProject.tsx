import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {invoke} from '@tauri-apps/api/core';
import {X} from 'lucide-react';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '@/shared/ui/dialog';
import {Segmented,type SelectGroup} from './DervoControls';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuLabel,DropdownMenuRadioGroup,DropdownMenuRadioItem,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {StatusGlyph} from './StatusGlyph';
import {CODEX_STARTER,accountCaption,accountsFrom,claudeDefaultModel,type Account,type Catalog,type Profile} from './models';
import {local,type UsageAccount} from './api';
import {firstRunStatusQuery} from './FirstRun';
import './Surfaces.css';
import {formatList,t,tr} from '@/i18n';

/** Adding a project after the first (surfaces/add-project.html). The macOS folder panel opens at once, then one short
 * dialog: the name, which of your accounts new threads use, and what agents can do. The account defaults to the one
 * the project you're in uses, so a Acme project doesn't land on Personal by accident. First run keeps its
 * own page for the first project; these rows match its account and access choices. */

export type Facts={path:string;name:string;git:boolean;repo:string|null;branch:string|null;uncommitted:number;stack:string[]};
export type Created={id:string;channelId:string;name:string;access:'edit'|'read';overview:{channelId:string}|null};
type Staged={selectionId:string;facts:Facts};
/** Your existing Claude Code sign-in, before Dervo has made an account of it. */
const SYSTEM='system';

const home=(path:string)=>path.replace(/^\/Users\/[^/]+(?=\/|$)/,'~');

/** The folder in one line under the title: where it is, its git state and what it looks like. */
export function folderLine(facts:Facts):string{
  const changes=facts.uncommitted?t('{count} uncommitted files',{count:facts.uncommitted}):t('nothing uncommitted');
  const git=facts.git?(facts.branch?t('A git repository on {branch} with {changes}.',{branch:facts.branch,changes}):t('A git repository with {changes}.',{changes}))
    :t('Not a git repository yet.');
  const stack=facts.stack.length?` ${t('Looks like {stack}.',{stack:formatList(facts.stack)})}`:'';
  return `${git}${stack}`;
}

/** The account a new project starts on: the one the project you're in uses when it's still yours, otherwise your first. */
export function defaultAccount(accounts:Account[],currentProfileId?:string|null):string|null{
  if(currentProfileId&&accounts.some(a=>a.id===currentProfileId))return currentProfileId;
  return accounts[0]?.id??null;
}

/** The Claude model new work starts on here: Opus 5.5 when this Mac's Claude Code can run it, otherwise Opus 5. The
 * version comes from the same kept answer Connected accounts reads, so this starts no session and makes no model call. */
export async function claudeDefaultOnThisMac():Promise<string>{
  const found=await local<{version?:string|null}>('claude_version',{recheck:false}).catch(()=>undefined);
  return claudeDefaultModel(found?.version);
}
/** This Mac's Claude Code version for a picker, from the same query and kept answer Connected accounts reads, or null
 * until it's known or when it can't be read. */
export function useClaudeVersion(enabled=true):string|null{
  const found=useQuery({queryKey:['claude-version'],enabled,staleTime:60_000,retry:false,
    queryFn:()=>local<{version?:string|null}>('claude_version',{recheck:false})});
  return found.data?.version??null;
}

/** The model a new project starts with on an account: the project you're in's when it's the same account, the Claude
 * default, or the first model the Codex account offers. */
export async function modelFor(account:Pick<Account,'id'|'provider'>|undefined,current?:{profile_id:string;model:string}):Promise<string>{
  if(!account)return claudeDefaultOnThisMac();
  if(current&&current.profile_id===account.id&&current.model)return current.model;
  if(account.provider==='claude')return claudeDefaultOnThisMac();
  const catalog=await local<Catalog>('model_status',{profileId:account.id}).catch(()=>undefined);
  return catalog?.state==='catalog'?catalog.models.find(m=>!m.hidden)?.model??CODEX_STARTER:CODEX_STARTER;
}

/** Opens the folder panel, then the dialog. `start` is what ⌘O and "Add a project…" call. */
export function useAddProject({projects,onExisting}:{projects:{id:string;cwd:string}[];onExisting:(id:string)=>void}){
  const [staged,setStaged]=useState<Staged|null>(null),[error,setError]=useState('');
  const busy=useRef(false);
  async function choose(){
    if(busy.current)return;
    busy.current=true;setError('');
    try{
      const picked=await invoke<{selectionId:string;path:string;name:string}|null>('choose_project_folder');
      if(!picked)return;
      // A folder that's already a project opens that project.
      const existing=projects.find(project=>project.cwd===picked.path);
      if(existing){setStaged(null);onExisting(existing.id);return;}
      const facts=await local<Facts>('first_run_inspect',{selectionId:picked.selectionId});
      setStaged({selectionId:picked.selectionId,facts});
    }catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{busy.current=false;}
  }
  return {staged,error,start:()=>void choose(),change:()=>void choose(),close:()=>{setStaged(null);setError('');}};
}

type Props={staged:Staged|null;onChangeFolder:()=>void;onClose:()=>void;profiles:Profile[];usage?:UsageAccount[];
  current?:{name:string;profile_id:string;model:string};onConnect:()=>void;onDone:(created:Created)=>Promise<unknown>|void};

export function AddProjectDialog({staged,onChangeFolder,onClose,profiles,usage,current,onConnect,onDone}:Props){
  const open=!!staged;
  return <Dialog open={open} onOpenChange={next=>{if(!next)onClose();}}>
    {staged&&<AddProjectBody key={staged.selectionId} staged={staged} onChangeFolder={onChangeFolder} onClose={onClose} profiles={profiles} usage={usage}
      current={current} onConnect={onConnect} onDone={onDone}/>}
  </Dialog>;
}

function AddProjectBody({staged,onChangeFolder,onClose,profiles,usage,current,onConnect,onDone}:Omit<Props,'staged'>&{staged:Staged}){
  const client=useQueryClient();
  const {facts,selectionId}=staged;
  // A probe still going comes back as 'checking' (first_run.PROBE_WAIT): the shared query asks again until it answers.
  const status=useQuery(firstRunStatusQuery);
  // Both sides: the account you pick decides whether the new project is Work or Personal, and it stays that way.
  const accounts=accountsFrom(profiles,usage,Date.now(),'both');
  const system=status.data?.claude.state==='ready'&&!status.data.claude.profile_id;
  const [name,setName]=useState(facts.repo??facts.name);
  const [account,setAccount]=useState<string|null>(()=>defaultAccount(accounts,current?.profile_id));
  const [access,setAccess]=useState<'edit'|'read'>(facts.git?'edit':'read');
  const [adding,setAdding]=useState(false),[error,setError]=useState('');
  const field=useRef<HTMLInputElement>(null);
  useEffect(()=>{if(!account){const chosen=defaultAccount(accounts,current?.profile_id)??(system?SYSTEM:null);if(chosen)setAccount(chosen);}},[profiles.length,system]);
  const chosen=accounts.find(a=>a.id===account);
  const provider=account===SYSTEM?'claude':chosen?.provider;
  // One row above Name when Claude Code or git needs you first; Add project waits for it.
  const s=status.data;
  const checking=s?.claude.state==='checking';
  const blocker=s&&provider==='claude'&&['missing','signed_out','unsupported'].includes(s.claude.state)?{what:'Claude Code',detail:s.claude.detail}
    :null;
  const groups:SelectGroup<string>[]=(['claude','codex'] as const).map(p=>({label:p==='claude'?'Claude':'Codex',
    options:[...accounts.filter(a=>a.provider===p).map(a=>({value:a.id,label:a.work?t('{name} · Work',{name:a.name}):a.name,sub:a.work?`${t('Work account. It makes this a Work project.')} ${accountCaption(a)}`:accountCaption(a)})),
      ...(p==='claude'&&system?[{value:SYSTEM,label:'Claude Code',sub:s?.claude.email?t('Your sign-in, {email}',{email:s.claude.email}):t('Your sign-in')}]:[])]}))
    .filter(group=>group.options.length>0);
  const sameAsCurrent=!!current&&account===current.profile_id;
  const canAdd=!!name.trim()&&!!account&&!blocker&&!adding;
  async function add(){
    if(!canAdd)return;
    setAdding(true);setError('');
    try{
      let profileId=account!;
      if(profileId===SYSTEM){profileId=(await local<{id:string}>('first_run_claude')).id;await client.invalidateQueries({queryKey:['local-profiles']});}
      const model=await modelFor(profileId===account?chosen:{id:profileId,provider:'claude'},current);
      const created=await local<Created>('first_run_finish',{selectionId,profileId,model,name:name.trim(),access});
      await client.invalidateQueries({queryKey:['first-run-status']});
      await onDone(created);
    }catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setAdding(false);}
  }
  return <DialogContent className="dv-dlg" showCloseButton={false} aria-describedby="dv-add-line"
    onOpenAutoFocus={event=>{event.preventDefault();requestAnimationFrame(()=>{field.current?.focus();field.current?.select();});}}
    onInteractOutside={event=>{if(adding)event.preventDefault();}} onEscapeKeyDown={event=>{if(adding)event.preventDefault();}}>
    <div className="dv-dlg-head">
      <div>
        <DialogTitle className="dv-title">{t('Add {name}',{name:name.trim()||facts.name})}</DialogTitle>
        <DialogDescription id="dv-add-line" className="dv-dlg-line">{tr('From {folder}.',{folder:<code>{home(facts.path)}</code>})} {folderLine(facts)} <button type="button" className="dv-link" disabled={adding} onClick={onChangeFolder}>{t('Change folder')}</button></DialogDescription>
      </div>
      <button type="button" className="dv-btn dv-btn-icon" aria-label={t('Close')} disabled={adding} onClick={onClose}><X size={16} strokeWidth={1.6}/></button>
    </div>
    {blocker&&<div className="dv-dlg-note" role="status"><StatusGlyph state="needs" accent/><span><b>{blocker.what}</b>: {blocker.detail}</span>
      <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={status.isFetching} onClick={()=>void status.refetch()}>{status.isFetching?t('Checking…'):t('Check again')}</button></div>}
    <div className="dv-dlg-fields">
      <div className="dv-fld"><label className="dv-k" htmlFor="dv-add-name">{t('Name')}</label>
        <span className="dv-v"><input ref={field} id="dv-add-name" className="dv-input" value={name} maxLength={80} disabled={adding} onChange={event=>setName(event.target.value)}
          onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();void add();}}}/></span></div>
      <div className="dv-fld dv-top"><span className="dv-k">{t('Account')}</span>
        <span className="dv-v">
          {groups.length?<AccountSelect groups={groups} value={account} disabled={adding} onChange={setAccount} onConnect={onConnect}
            current={account===SYSTEM?{provider:'Claude',name:'Claude Code'}:chosen?{provider:chosen.provider==='claude'?'Claude':'Codex',name:chosen.name}:null}/>
            :checking?null:<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onConnect}>{t('Connect an account…')}</button>}
          {/* Still checking Claude Code (design review, 23 Sep, 6): say that, not "connect one first", until it answers. */}
          <p className="dv-line" role={checking?'status':undefined}>{!groups.length?(checking?s!.claude.detail:t('Connect Claude or Codex first. New threads here use it.')):sameAsCurrent?t('New threads here use it. Same as {name}.',{name:current!.name}):t('New threads here use it.')}</p>
        </span></div>
      <div className="dv-fld dv-top"><span className="dv-k">{t('Agents can')}</span>
        <span className="dv-v">
          <Segmented label={t('What agents can do in this project')} value={access} disabled={adding} onChange={setAccess}
            options={[{value:'edit',label:t('Edit, with your review'),disabled:!facts.git,title:facts.git?undefined:t('Needs git, so every change can be undone')},{value:'read',label:t('Read only')}]}/>
          <p className="dv-line">{!facts.git?t('Editing needs git, so every change can be undone. Run git init there first.')
            :access==='edit'?t('Every change waits for your review, and nothing is committed until you approve it.'):t('It reads the project and answers questions. It can’t change anything.')}</p>
        </span></div>
    </div>
    {error&&<p className="dv-field-note" role="alert">{error}</p>}
    <p className="dv-dlg-caption">{t('First, the agent reads it and writes you an overview. It won’t change anything in that run.')}</p>
    <div className="dv-dlg-foot">
      <button type="button" className="dv-btn dv-btn-quiet" disabled={adding} onClick={onClose}>{t('Cancel')}</button>
      <button type="button" className="dv-btn dv-btn-primary" disabled={!canAdd} onClick={()=>void add()}>{adding?t('Adding…'):t('Add project')}</button>
    </div>
  </DialogContent>;
}

/** The account row's select: "Claude · Personal" on the button, and each account by name with what's left in the menu,
 * grouped under Claude and Codex, then "Connect an account…" (surfaces/add-project.html). */
function AccountSelect({groups,value,current,disabled,onChange,onConnect}:{groups:SelectGroup<string>[];value:string|null;current:{provider:string;name:string}|null;
  disabled?:boolean;onChange:(id:string)=>void;onConnect:()=>void}){
  return <DropdownMenu>
    <DropdownMenuTrigger asChild disabled={disabled}>
      <button type="button" className="dv-select" aria-label={current?t('Account for new threads in this project: {provider}, {name}',{provider:current.provider,name:current.name}):t('Account for new threads in this project')}>
        <span className="dv-select-value">{current?<>{current.provider} · <span className="dv-sub">{current.name}</span></>:t('Choose an account')}</span>
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4.5 6.5L8 10l3.5-3.5"/></svg>
      </button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="start" className="dv-menu-account" aria-label={t('Accounts')}>
      <DropdownMenuRadioGroup value={value??''} onValueChange={onChange}>
        {groups.map(group=><div key={group.label} role="group" aria-label={group.label}>
          <DropdownMenuLabel>{group.label}</DropdownMenuLabel>
          {group.options.map(option=><DropdownMenuRadioItem key={option.value} value={option.value} className="dv-mi-two">
            <span className="dv-mi-t"><span>{option.label}</span>{option.sub&&<small>{option.sub}</small>}</span>
          </DropdownMenuRadioItem>)}
        </div>)}
      </DropdownMenuRadioGroup>
      <DropdownMenuSeparator/>
      <DropdownMenuItem onSelect={onConnect}><span className="dv-mi-t">{t('Connect an account…')}</span></DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
