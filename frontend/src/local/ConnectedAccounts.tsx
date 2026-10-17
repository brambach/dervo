import {useEffect,useRef,useState,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {local,type HarnessBoundary,type UsageAccount,type UsageSummary} from './api';
import {Segmented,Select,SettingRow,SettingsSection,Switch,TextField} from './DervoControls';
import {accountLabel} from './DervoSidebar';
import {connectionStatusQuery,describeError,type Login} from './ConnectionPreflight';
import {LIMITS,LIMIT_NOTE,limitLabel,maskKey} from './AddAccount';
import {AddRow,Confirm,CopyCommand,Disclosure,Meter,OpenCard,StatusWord,shortDate,useSaved} from './SettingsKit';
import {StatusGlyph} from './StatusGlyph';
import {accountMeter} from './UsagePopover';
import {OpenRouterSettings} from './OpenRouterSettings';
import {fableCreditLabel,TEAM_FABLE_NOTICE} from './models';
import {SignInToAdd,type Added} from './AccountSignIn';
import {modelForAccount} from './SettingsModel';
import type {SettingsProject} from './projectSettingsData';
import {BRIEFCASE,WorkTag} from './WorkMark';
import {formatList,t} from '@/i18n';

/** A project, as the host lists it: its account and whether it's Work or Personal. */
type ProjectRow={id:string;name?:string|null;profile_id:string;context?:'work'|'personal'|null};
/** What an account runs (settings-accounts): its side and the projects on it, so the split is concrete. */
export function usedLine(profile:Profile,projects:ProjectRow[]):string{
  const side=profile.category==='work'?t('Work'):t('Personal');
  const names=projects.filter(p=>p.profile_id===profile.id).map(p=>p.name||t('Unnamed project'));
  if(!names.length)return t('{side}. Not used by a project yet.',{side});
  const listed=names.length<=3?formatList(names):t('{items} and {count} more',{items:names.slice(0,2).join(', '),count:names.length-2});
  return profile.category==='work'?t('Work projects: {names}',{names:listed}):t('Personal projects: {names}',{names:listed});
}

/** Connected accounts, in Settings (settings/connected-accounts*.html). Your Claude and Codex accounts by the name you give
 * them, each with its plan, what it has left and when that resets, and a menu for the rest: rename, sign in again, check
 * sign-in, sign in from Terminal, use for new threads in this project, an API key's limit, and Remove for every account
 * (beta.18). Adding an account signs in first and shows who signed in before adding it (AccountSignIn.tsx); an API key is
 * the quieter second way, and says who bills it (Anthropic's terms for apps that run Claude Code: no built-in sign-in
 * method may be removed or restricted). A key is sent to the host once and never comes back: rows show its last four
 * characters, which is all the host keeps outside Keychain. */

/** One of the host's profiles: ``name`` is the name you gave it here, ``plan`` the Claude plan auth status last reported,
 * kept by the host in one place (provider_plans.py) and sent only on this listing. */
export type Profile={id:string;provider:string;identity:string;category:string;method?:string;monthly_limit_usd?:number|null;name?:string|null;plan?:string|null;fable_confirmed?:boolean};
/** ``signedInAs`` and ``canUseSignedIn``: the account's sign-in is a different valid account on its side (beta.18,
 * Host._checked), offered as "Use this account instead". */
type Check={state:string;canSend:boolean;detail:string;signedInAs?:string;signedInPlan?:string;canUseSignedIn?:boolean;takenBy?:string;otherSide?:string};
type Version={state:'ok'|'old'|'missing'|'unknown';version:string|null;floor:string;command:string|null;detail:string};
type Instructions={command:string;expectedIdentity:string;provider:string;detail:string};
type Status={permissions?:string;blockers?:Record<string,string>;codexVersion?:string;harness?:{boundaries?:HarnessBoundary[];actions?:string[]}};

const PROVIDER:Record<string,{label:string;company:string;plan:string;keyLabel:string;keyWhere:string;keyPrefix:string;signIn:string;cli:string}>={
  claude:{label:'Claude',company:'Anthropic',plan:'Claude',keyLabel:t('Anthropic API key'),keyWhere:'console.anthropic.com',keyPrefix:'sk-ant-',signIn:t('Sign in with Claude'),cli:'Claude Code'},
  codex:{label:'Codex',company:'OpenAI',plan:'ChatGPT',keyLabel:t('OpenAI API key'),keyWhere:'platform.openai.com',keyPrefix:'sk-',signIn:t('Sign in with ChatGPT'),cli:'Codex'},
};
// i18n-ignore: the providers' own plan names
const PLAN_NAMES:Record<string,string>={pro:'Pro',max:'Max',team:'Team',plus:'Plus',business:'Business',enterprise:'Enterprise',edu:'Edu',free:'Free'};
/** Who bills what, said once where you choose (the add card). */
export const BILLING={
  claude:{subscription:t('Uses your Claude Pro, Max or Team plan’s limits. Sign-in finishes in Claude’s own window.'),
    api_key:t('Billed per use by Anthropic to the key’s account. Your Claude plan’s limits don’t apply.')},
  codex:{subscription:t('Uses your ChatGPT plan’s limits. Sign-in finishes in OpenAI’s own window.'),
    api_key:t('Billed per use by OpenAI to the key’s account. Your ChatGPT plan’s limits don’t apply.')},
} as const;
const STORED={claude:t('Dervo checks it once, keeps it in your Mac’s Keychain and never shows it again.'),
  codex:t('Dervo checks it once, then Codex saves it in a file in its own folder on this Mac. Anything running as you can read that file, full-access runs included.')};

/** The name an account goes by everywhere: the one you gave it, or the same default the picker and sidebar use. */
export function nameOf(profile:Profile,usage?:UsageAccount){
  return accountLabel({provider:profile.provider,method:profile.method??'subscription',rate_limits:usage?.rate_limits??null,name:profile.name??usage?.name??null});
}
/** The account's second line: its plan and email, or for a key its last four characters and who bills it. */
export function accountLine(profile:Profile,plan?:string|null){
  const provider=PROVIDER[profile.provider]??PROVIDER.claude;
  if(profile.method==='api_key')return `${profile.identity.replace(/^(Anthropic|OpenAI) /,'')} · ${t('billed per use by {company}',{company:provider.company})}`;
  const name=plan&&PLAN_NAMES[plan]?t('{plan} plan',{plan:`${provider.plan} ${PLAN_NAMES[plan]}`}):null;
  return name?`${name} · ${profile.identity}`:profile.identity;
}

export function ConnectedAccounts({profiles,loading,failed,onRetry,onProfilesChanged,usage,status,onStatusChanged,projectName,projectProfileId,project:here,onProjectSettings}:{
  profiles:Profile[];loading:boolean;failed:boolean;onRetry:()=>void;onProfilesChanged:()=>Promise<unknown>;usage?:UsageSummary;status?:Status;
  onStatusChanged:()=>Promise<unknown>;projectName?:string;projectProfileId?:string;
  /** The project you came from, so an account can become what its new threads use (beta.18). */
  project?:SettingsProject;onProjectSettings?:()=>void}){
  const client=useQueryClient();
  const [adding,setAdding]=useState<string|null>(null);
  const [fresh,setFresh]=useState<{id:string;then:Start}|null>(null);
  const [notice,setNotice]=useState('');
  const projects=useQuery({queryKey:['local-projects'],queryFn:()=>local<ProjectRow[]>('projects')});
  const version=useQuery({queryKey:['claude-version'],queryFn:()=>local<Version>('claude_version',{recheck:false})});
  const usageOf=(id:string)=>usage?.accounts.find(account=>account.profile_id===id);
  async function changed(){await onProfilesChanged();await client.invalidateQueries({queryKey:['usage-summary']});}
  const section=(provider:'claude'|'codex')=>{
    const rows=profiles.filter(profile=>profile.provider===provider);
    const info=PROVIDER[provider];
    const old=provider==='claude'&&version.data?.state==='old';
    const caption=provider==='codex'?(status?.codexVersion?`Codex ${status.codexVersion}`:undefined)
      :version.data?(version.data.version?t('Claude Code {version} on this Mac',{version:version.data.version}):version.data.state==='missing'?t('Claude Code isn’t installed'):undefined):undefined;
    return <SettingsSection key={provider} title={info.label} caption={caption} id={`dv-accounts-${provider}`}>
      <div className="dv-provider">
        {old&&<ClaudeTooOld version={version.data!} checking={version.isFetching} onCheck={async()=>{client.setQueryData(['claude-version'],await local<Version>('claude_version',{recheck:true}));}}/>}
        {rows.map(profile=><AccountRow key={profile.id} profile={profile} usage={usageOf(profile.id)} blocked={old} onChanged={changed} used={usedLine(profile,Array.isArray(projects.data)?projects.data:[])}
          start={fresh?.id===profile.id?fresh.then:undefined} onStarted={()=>setFresh(null)} onProjectSettings={onProjectSettings}
          project={here&&projectName?{...here,name:projectName,provider:profiles.find(p=>p.id===here.profile_id)?.provider}:undefined} onRemoved={detail=>setNotice(detail??'')}/>)}
        {adding===provider
          ?<AddCard provider={provider} onCancel={()=>setAdding(null)} onAdded={async(id,then)=>{await changed();setAdding(null);setFresh({id,then});}}/>
          :<AddRow onClick={()=>setAdding(provider)}>{t('Add a {label} account',{label:info.label})}</AddRow>}
      </div>
    </SettingsSection>;
  };
  const project=projectProfileId?profiles.find(profile=>profile.id===projectProfileId):undefined;
  return <>
    {failed&&<div className="dv-set" role="alert"><div><p className="dv-set-line">{t('Dervo couldn’t load your accounts.')}</p></div>
      <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onRetry}>{t('Try again')}</button></div></div>}
    {!loading&&!failed&&!profiles.length&&<p className="dv-sec-note">{t('Add a Claude or Codex account to start.')}</p>}
    {notice&&<p className="dv-sec-note" role="status">{notice}</p>}
    {loading&&!profiles.length?<p className="dv-sec-note" role="status">{t('Checking…')}</p>:<>{section('claude')}{section('codex')}</>}
    <OpenRouterSettings/>
    <HelpBetweenAccounts profiles={profiles} usage={usage} boundaries={status?.harness?.boundaries??[]} actions={status?.harness?.actions??[]} refresh={onStatusChanged}/>
    {projectName&&project&&<SettingsSection title={t('In your projects')} id="dv-accounts-projects">
      <SettingRow name={t('{project} runs on {account}',{project:projectName,account:nameOf(project,usageOf(project.id))})} line={t('Each project picks its account and model, and whether to switch when one runs out.')}
        end={onProjectSettings&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onProjectSettings}>{t('Project settings')}</button>}/>
    </SettingsSection>}
    <Disclosure summary={t('What agents can and can’t do in Dervo')}>
      <dl className="dv-rules">
        <dt>{t('Claude coding')}</dt><dd>{t('Unmodified Claude Code with its own tools, settings, skills and outside MCP servers off. It works only through Dervo’s tools.')}</dd>
        <dt>{t('Claude chat')}</dt><dd>{t('Not available in Dervo yet. Ask for coding work instead.')}</dd>
        <dt>{t('Codex chat')}</dt><dd>{t('Read-only, with no network, shell, browser, apps, MCP or hooks. Permission requests are refused.')}</dd>
        <dt>{t('API keys')}</dt><dd>{t('Dervo gives a key only to that account’s own runs, through their environment, and full access is off on API-key accounts. A full-access run on any other account runs commands as you, so it can read every key saved on this Mac, and your Dervo sign-in.')}</dd>
        <dt>{t('Reading files')}</dt><dd>{t('Read-only chat can read outside the project folder. Edits stay inside it.')}</dd>
        <dt>{t('Private threads')}</dt><dd>{t('Their text never reaches shared context.')}</dd>
      </dl>
    </Disclosure>
  </>;
}

/** Below 2.1.271 Claude Code can't run Dervo's flags (PLAN-native A7): the page's one rust item, with what to run. */
function ClaudeTooOld({version,checking,onCheck}:{version:Version;checking:boolean;onCheck:()=>Promise<void>}){
  const [busy,setBusy]=useState(false);
  return <div className="dv-attention" role="alert">
    <StatusGlyph state="needs" accent/>
    <div><p className="dv-set-name">{t('Update Claude Code to {version} or later',{version:version.floor})}</p><p className="dv-set-line">{version.detail}</p>
      {version.command&&<CopyCommand command={version.command}/>}</div>
    <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||checking} onClick={()=>{setBusy(true);void onCheck().finally(()=>setBusy(false));}}>{busy?t('Checking…'):t('Check again')}</button>
  </div>;
}

/** What a new row does first: name it (just added), finish its sign-in, or say it was already here. */
type Start='rename'|'sign-in'|'existing';
type RowProject=SettingsProject&{name:string;provider?:string};
const SITE:Record<string,string>={claude:'claude.ai',codex:'chatgpt.com'};

/** What Remove does, said before it does it (beta.18): Dervo forgets the account and deletes what it keeps for it on this
 * Mac. Signing out elsewhere is the provider's, and your own Claude Code login is never touched. */
export function removeLine(profile:Pick<Profile,'provider'|'method'>){
  if(profile.method==='api_key')return profile.provider==='claude'?t('The key is deleted from your Mac’s Keychain. Threads that use it stop until you pick another account.'):t('Codex deletes the key from this Mac. Threads that use it stop until you pick another account.');
  return t('Dervo forgets it and deletes the sign-in it keeps for it on this Mac. You stay signed in to {site} everywhere else, and your own Claude Code login isn’t touched. Threads and projects on it wait for you to pick another account.',{site:SITE[profile.provider]??'claude.ai'});
}

const MORE=<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.3"/><circle cx="8" cy="8" r="1.3"/><circle cx="12.5" cy="8" r="1.3"/></svg>;

/** One account. The row says only what's worth saying: a healthy account shows its plan and usage and nothing else; a
 * problem gets one line on this row, with the action that fixes it. */
function AccountRow({profile,usage,blocked,onChanged,used,start,onStarted,onProjectSettings,project,onRemoved}:{profile:Profile;usage?:UsageAccount;blocked:boolean;onChanged:()=>Promise<unknown>;
  used:string;start?:Start;onStarted:()=>void;onProjectSettings?:()=>void;project?:RowProject;onRemoved:(detail?:string)=>void}){
  const keyed=profile.method==='api_key';
  const client=useQueryClient();
  const [already,setAlready]=useState(false),row=useRef<HTMLDivElement>(null);
  const [renaming,setRenaming]=useState(false),[saved,markSaved]=useSaved();
  const [mode,setMode]=useState<null|'remove'|'terminal'|'limit'>(null);
  const [steps,setSteps]=useState<Instructions|null>(null);
  const [error,setError]=useState(''),[busy,setBusy]=useState('');
  const [signing,setSigning]=useState(false);
  const checked=useRef(false),more=useRef<HTMLButtonElement>(null),nameBox=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(renaming)requestAnimationFrame(()=>{const input=nameBox.current?.querySelector('input');input?.focus();input?.select();});},[renaming]);
  const status=useQuery({...connectionStatusQuery(profile.id),refetchInterval:query=>query.state.data?.state==='checking'?500:false});
  // The sign-in process ends before the host has checked who signed in, so keep asking until that check has a result.
  const login=useQuery({queryKey:['connection-login',profile.id],enabled:!keyed,queryFn:()=>local<Login>('connection_login_status',{profileId:profile.id}),
    refetchInterval:query=>{const data=query.state.data;return data?.active||(data?.state==='succeeded'&&data.identity?.state==='checking')?700:false;}});
  const check=status.data as Check|undefined;
  // An account nobody has checked yet is checked once when the page opens: its sign-in and plan, never a model call.
  useEffect(()=>{if(check?.state==='unchecked'&&!checked.current&&!login.data?.active){checked.current=true;void run('check',()=>local('connection_check',{profileId:profile.id}).then(()=>status.refetch()));}},[check?.state,login.data?.active]);
  useEffect(()=>{if(start==='rename'){setRenaming(true);onStarted();}if(start==='sign-in'){setSigning(true);onStarted();}
    // Adding an account that's already here lands on it, said once, instead of making a second one.
    if(start==='existing'){setAlready(true);onStarted();row.current?.scrollIntoView({block:'nearest'});more.current?.focus();}},[start]);
  useEffect(()=>{if(!already)return;const timer=setTimeout(()=>setAlready(false),6000);return ()=>clearTimeout(timer);},[already]);
  useEffect(()=>{if(login.data?.active)setSigning(true);},[login.data?.active]);
  const loginIdentity=login.data?.identity?.state;
  useEffect(()=>{const state=login.data?.state;if(state==='succeeded'||state==='failed'||state==='cancelled'||state==='timed_out')void status.refetch();},[login.data?.state,loginIdentity]);
  // The host records the plan when it reads a finished check, so the listing is read again once a check settles.
  const settled=useRef(check?.state);
  useEffect(()=>{const was=settled.current;settled.current=check?.state;if((was==='checking'||was==='unchecked'||busy==='check')&&check?.state==='identity_verified')void onChanged();},[check?.state]);
  async function run(key:string,work:()=>Promise<unknown>,secret=''){
    setBusy(key);setError('');
    try{await work();}
    catch(failure){setError(describeError(failure instanceof Error?failure.message:failure,secret));}
    finally{setBusy('');}
  }
  const name=nameOf(profile,usage);
  // One source each: Claude's plan from the host's record (profiles), Codex's from its rate-limit report (usage).
  const plan=profile.provider==='claude'?profile.plan:usage?.rate_limits?.planType?.toLowerCase();
  const meter=usage?accountMeter(usage):null;
  const state=check?.state;
  const signedOut=!keyed&&state==='signed_out';
  // The host says which part didn't match (the email, the plan, or your other side's account), so its line is shown as it is.
  const problem=state==='identity_mismatch'?(check!.detail||t('Signed in as a different account than you added. No messages were sent.'))
    :state==='config_blocked'||state==='unknown'?check!.detail:keyed&&state==='signed_out'?check!.detail:'';
  const menuItem=(label:string,onSelect:()=>void,disabled?:boolean)=><DropdownMenuItem key={label} disabled={disabled} onSelect={onSelect}>{label}</DropdownMenuItem>;
  const side=busy==='check'||state==='checking'?<span className="dv-acct-side" role="status">{t('Checking…')}</span>
    :signedOut?<span className="dv-acct-side"><StatusWord state="failed">{t('Signed out')}</StatusWord>
      <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy||blocked} onClick={()=>void startSignIn()}>{t('Sign in')}</button></span>
    :meter?<Meter percent={meter.percent} value={meter.value} caption={meter.caption} title={meter.title}/>
    :<span/>;
  async function startSignIn(){
    await run('sign-in',async()=>{setSigning(true);await local<Login>('connection_login',{profileId:profile.id});await login.refetch();});
  }
  // Use this sign-in instead (beta.18): the account's sign-in is a different valid account on its side, named by the host.
  const adopt=!keyed&&state==='identity_mismatch'&&check?.canUseSignedIn&&check.signedInAs?check.signedInAs:null;
  function adoptSignIn(){void run('adopt',async()=>{await local('account_use_signed_in',{profileId:profile.id});await status.refetch();await onChanged();markSaved();});}
  const canDefault=!!project&&project.profile_id!==profile.id&&(project.context??'personal')===profile.category;
  if(mode==='remove')return <Confirm title={t('Remove {name}?',{name})} line={removeLine(profile)}
    keep={t('Keep')} act={t('Remove')} busy={busy==='remove'} busyLabel={t('Removing…')} onKeep={()=>{setMode(null);requestAnimationFrame(()=>more.current?.focus());}}
    onAct={()=>void run('remove',async()=>{const removed=await local<{detail?:string}>('account_remove',{profileId:profile.id});onRemoved(removed?.detail);await onChanged();}).then(()=>setMode(null))}/>;
  return <>
    <div ref={row} className="dv-acct" data-testid="account-row" data-method={profile.method??'subscription'} data-profile={profile.id}>
      <div>
        {renaming
          ?<div className="dv-inline" ref={nameBox}
            onKeyDown={event=>{if(event.key==='Escape'&&!event.defaultPrevented){event.preventDefault();setRenaming(false);requestAnimationFrame(()=>more.current?.focus());}}}
            onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node)&&(event.target as HTMLInputElement).value===name)setRenaming(false);}}><TextField label={t('Name for {identity}',{identity:profile.identity})} value={name} onCommit={async next=>{
              try{await local('profile_rename',{profileId:profile.id,name:next});await onChanged();markSaved();setRenaming(false);requestAnimationFrame(()=>more.current?.focus());}
              catch(failure){return failure instanceof Error?failure.message:String(failure);}
            }}/>{saved&&<span className="dv-saved" role="status">{t('Saved')}</span>}</div>
          :<p className="dv-acct-name">{name}{profile.category==='work'&&<WorkTag/>}{saved&&<span className="dv-saved" role="status">{t('Saved')}</span>}
            {already&&<span className="dv-saved" role="status">{t('Already added')}</span>}</p>}
        <p className="dv-acct-line">{accountLine(profile,plan)}</p>
        <p className="dv-acct-used">{used}</p>
        {profile.provider==='claude'&&!keyed&&fableCreditLabel(plan)&&<p className="dv-acct-note">{plan==='team'?TEAM_FABLE_NOTICE:t('Fable is billed to paid credits on this plan.')}</p>}
      </div>
      {side}
      <DropdownMenu><DropdownMenuTrigger asChild>
        <button ref={more} type="button" className="dv-btn dv-btn-icon" aria-label={t('{name} options',{name})}>{MORE}</button>
      </DropdownMenuTrigger><DropdownMenuContent align="end" onCloseAutoFocus={event=>{if(renaming||mode||signing)event.preventDefault();}}>
        {menuItem(t('Rename'),()=>{setMode(null);setRenaming(true);})}
        {keyed?[
          menuItem(t('Check key'),()=>void run('check',async()=>{await local('connection_check',{profileId:profile.id});await status.refetch();})),
          menuItem(t('Change limit'),()=>setMode('limit')),
        ]:[
          menuItem(t('Sign in again'),()=>void startSignIn(),blocked||!!login.data?.active),
          menuItem(t('Check sign-in'),()=>void run('check',async()=>{await local('connection_check',{profileId:profile.id});await status.refetch();}),!!login.data?.active),
          menuItem(t('Sign in from Terminal'),()=>void run('terminal',async()=>{setSteps(await local<Instructions>('connection_instructions',{profileId:profile.id}));setMode('terminal');})),
        ]}
        {canDefault&&menuItem(t('Use for new threads in {project}',{project:project!.name}),()=>void run('default',async()=>{
          const model=await modelForAccount(client,project!,profile,project!.provider);
          await local('project_profile_set',{projectId:project!.id,expectedProfileId:project!.profile_id,profileId:profile.id,expectedModel:project!.model,model});
          await client.invalidateQueries({queryKey:['local-projects']});markSaved();}))}
        <DropdownMenuSeparator/>
        {menuItem(t('Remove'),()=>setMode('remove'),!!login.data?.active)}
      </DropdownMenuContent></DropdownMenu>
      {problem&&<div className="dv-acct-problem" role="alert"><span>{problem}</span>
        {adopt&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} title={t('{name} uses {email} from now on. Its threads carry on there.',{name,email:adopt})} onClick={adoptSignIn}>{t('Use {email} instead',{email:adopt})}</button>}
        {!keyed&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy||blocked} onClick={()=>void startSignIn()}>{t('Sign in again')}</button>}</div>}
      {error&&<div className="dv-acct-problem" role="alert"><span>{maskKey(error,'')}</span>
        {/still use this account/.test(error)&&onProjectSettings&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onProjectSettings}>{t('Project settings')}</button>}</div>}
      {mode==='terminal'&&steps&&<div className="dv-acct-below">
        <CopyCommand command={steps.command}/>
        <p className="dv-field-note">{t('Run this in Terminal, then come back and Check sign-in.')}</p>
        <button type="button" className="dv-btn dv-btn-quiet" style={{alignSelf:'flex-start'}} onClick={()=>setMode(null)}>{t('Close')}</button></div>}
    </div>
    {mode==='limit'&&<LimitCard profile={profile} onDone={async saved=>{setMode(null);if(saved)await onChanged();}}/>}
    {signing&&!keyed&&<SigningCard profile={profile} login={login.data} busy={!!busy} starting={busy==='sign-in'} onAdopt={()=>{setSigning(false);adoptSignIn();}} onCode={code=>run('code',async()=>{await local<Login>('connection_login_code',{profileId:profile.id,code});await login.refetch();},code)}
      onRetry={()=>void startSignIn()} onTerminal={()=>void run('terminal',async()=>{setSteps(await local<Instructions>('connection_instructions',{profileId:profile.id}));setMode('terminal');})}
      onClose={()=>{setSigning(false);if(login.data?.state==='succeeded')setRenaming(!profile.name);}}
      onCancel={()=>void run('cancel',async()=>{await local<Login>('connection_login_cancel',{profileId:profile.id});await login.refetch();await status.refetch();}).then(()=>setSigning(false))}/>}
  </>;
}

/** Signing in: the provider's own sign-in, as steps, with the code field when Claude Code asks for one. */
function SigningCard({profile,login,busy,starting,onAdopt,onCode,onRetry,onTerminal,onClose,onCancel}:{profile:Profile;login?:Login;busy:boolean;starting:boolean;onAdopt:()=>void;onCode:(code:string)=>Promise<void>;
  onRetry:()=>void;onTerminal:()=>void;onClose:()=>void;onCancel:()=>void}){
  const info=PROVIDER[profile.provider]??PROVIDER.claude;
  const [code,setCode]=useState(''),[sent,setSent]=useState(false),[copied,setCopied]=useState(false);
  const field=useRef<HTMLInputElement>(null);
  const active=starting||!login||!!login.active,state=starting||!login?'starting':login.state;
  // Anything that isn't running, starting or done has stopped, including a sign-in that never started.
  const ended=!active&&state!=='succeeded';
  const identity=login?.identity?.state;
  const verified=identity==='identity_verified';
  const mismatch=identity==='identity_mismatch';
  const seen=login?.identity as Check|undefined,adopt=mismatch&&seen?.canUseSignedIn&&seen.signedInAs?seen.signedInAs:null;
  // The check can also end signed out, unknown or blocked. That's an answer too, so it never stays a spinner.
  const unsure=state==='succeeded'&&!!identity&&identity!=='checking'&&!verified&&!mismatch;
  useEffect(()=>{if(login?.needsCode)field.current?.focus();},[login?.needsCode]);
  useEffect(()=>{if(state==='succeeded'&&verified){const timer=setTimeout(onClose,900);return ()=>clearTimeout(timer);}},[state,verified]);
  const step=(glyph:'done'|'running'|'idle'|'failed',text:ReactNode,small?:ReactNode,now?:boolean)=><div className="dv-step" data-now={now||undefined}><StatusGlyph state={glyph}/><div>{text}{small&&<small>{small}</small>}</div></div>;
  const waiting=profile.provider==='claude'?t('Waiting for the code from your browser'):t('Waiting for you to finish in your browser');
  return <OpenCard title={t('Signing in to {label}',{label:info.label})} onEscape={active?onCancel:onClose}>
    <div className="dv-steps">
      {step('done',t('Started {cli}’s own sign-in',{cli:info.cli}),profile.provider==='claude'?t('Your existing Claude login isn’t read or changed'):undefined)}
      {ended?step('failed',state==='timed_out'?t('The sign-in timed out'):state==='cancelled'?t('Sign-in was cancelled'):t('Sign-in didn’t finish'),login?.detail||undefined)
        :step(active&&!sent?'running':'done',sent&&active?t('Code sent. Waiting for confirmation'):waiting,login?.detail,active)}
      {step(verified?'done':mismatch||unsure?'failed':state==='succeeded'?'running':'idle',t('Check it’s the account you meant'),
        mismatch?(login?.identity?.detail||t('Signed in as a different account than you added. No messages were sent.'))
          :unsure?(identity==='signed_out'?t('{cli} says {identity} isn’t signed in. No messages were sent.',{cli:info.cli,identity:profile.identity}):t('Dervo couldn’t confirm which account signed in. No messages were sent.'))
          :profile.identity)}
    </div>
    {profile.provider==='claude'&&login?.needsCode&&active&&<div className="dv-field"><span>{t('Code from your browser')}</span>
      <form className="dv-inline" onSubmit={event=>{event.preventDefault();const pasted=code.trim();if(!pasted)return;setCode('');setSent(true);void onCode(pasted);}}>
        <input ref={field} className="dv-input" aria-label={t('Sign-in code')} placeholder={t('Paste the code Claude shows')} value={code} autoComplete="off" spellCheck={false} onChange={event=>setCode(event.target.value)}/>
        <button type="submit" className="dv-btn dv-btn-primary" disabled={busy||!code.trim()}>{t('Submit code')}</button></form>
      <small>{t('Page didn’t open?')} {login.url&&<><button type="button" className="dv-link" onClick={()=>{void navigator.clipboard?.writeText(login.url!).then(()=>setCopied(true)).catch(()=>{});}}>{copied?t('Link copied'):t('Copy the sign-in link')}</button> {t('or')} </>}
        <button type="button" className="dv-link" onClick={onTerminal}>{t('sign in from Terminal')}</button>.</small></div>}
    <div className="dv-card-actions"><span className="dv-caption">{t('Dervo never sees your password.')}</span>
      {ended||mismatch||unsure?<><button type="button" className="dv-btn dv-btn-quiet" onClick={onClose}>{t('Close')}</button>
        {adopt&&<button type="button" className="dv-btn dv-btn-ghost" disabled={busy} onClick={onAdopt}>{t('Use {email} instead',{email:adopt})}</button>}
        <button type="button" className="dv-btn dv-btn-primary" disabled={busy} onClick={onRetry}>{mismatch?t('Sign in again'):t('Try again')}</button></>
        :active?<button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={onCancel}>{t('Cancel sign-in')}</button>
        :<button type="button" className="dv-btn dv-btn-quiet" onClick={onClose}>{t('Close')}</button>}
    </div>
  </OpenCard>;
}

/** Change an API key's monthly limit in Dervo, in place. */
function LimitCard({profile,onDone}:{profile:Profile;onDone:(saved:boolean)=>Promise<void>}){
  const [limit,setLimit]=useState<string>(String(profile.monthly_limit_usd??'none')),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const options=LIMITS.map(value=>({value:String(value??'none'),label:limitLabel(value)}));
  async function save(){
    setBusy(true);setError('');
    try{await local('profile_limit_set',{profileId:profile.id,monthlyLimitUsd:limit==='none'?null:Number(limit)});await onDone(true);}
    catch(failure){setError(String(failure instanceof Error?failure.message:failure));setBusy(false);}
  }
  return <OpenCard title={t('Monthly limit for {name}',{name:nameOf(profile)})} onEscape={()=>void onDone(false)}>
    <Segmented label={t('Monthly limit in Dervo')} value={options.some(o=>o.value===limit)?limit:'none'} onChange={setLimit} options={options}/>
    <p className="dv-card-text">{LIMIT_NOTE[profile.provider]??LIMIT_NOTE.claude}</p>
    {error&&<p className="dv-field-note" role="alert">{error}</p>}
    <div className="dv-card-actions"><button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>void onDone(false)}>{t('Cancel')}</button>
      <button type="button" className="dv-btn dv-btn-primary" disabled={busy} onClick={()=>void save()}>{busy?t('Saving…'):t('Save limit')}</button></div>
  </OpenCard>;
}

/** Adding an account (beta.18): what it's for, then one button that starts the provider's own sign-in. Dervo shows who
 * signed in and adds that account on your click (AccountSignIn.tsx): no email to type, so nothing to mistype. An API key
 * is the quieter second way, with who bills it. A work account only runs Work projects and a personal one only Personal
 * ones (settings-add-work). */
export function AddCard({provider,onCancel,onAdded}:{provider:'claude'|'codex';onCancel:()=>void;onAdded:(id:string,then:Start)=>Promise<void>}){
  const info=PROVIDER[provider];
  const [category,setCategory]=useState<'work'|'personal'>('personal');
  const [method,setMethod]=useState<'subscription'|'api_key'|'signing'>('subscription');
  const waiting=useQuery({queryKey:['onboarding-waiting'],enabled:category==='work',queryFn:()=>local<{names:string[]}>('onboarding_waiting'),retry:false});
  const [key,setKey]=useState(''),[limit,setLimit]=useState<string>('50');
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const seg=useRef<HTMLDivElement>(null),go=useRef<HTMLButtonElement>(null),anchor=useRef<HTMLSpanElement>(null);
  useEffect(()=>{seg.current?.querySelector<HTMLElement>('[role="radio"][tabindex="0"]')?.focus();},[]);
  // The card opens under the account list, often past the window's edge: each step brings the whole card into view.
  useEffect(()=>{anchor.current?.closest('.dv-open-card')?.scrollIntoView({block:'nearest'});},[method]);
  const mark=<span ref={anchor} hidden/>;
  const keyReady=key.trim().length>=8;
  async function submit(){
    if(busy||method!=='api_key')return;
    setBusy(true);setError('');
    try{
      const added=await local<{id:string}>('profile_key_add',{provider,key,category,...(limit==='none'?{}:{monthlyLimitUsd:Number(limit)})});
      setKey('');
      await onAdded(added.id,'rename');
    }catch(failure){setError(maskKey(failure instanceof Error?failure.message:failure,key));setBusy(false);}
  }
  const title=t('Add a {label} account',{label:info.label});
  if(method==='signing')return <OpenCard title={title} caption={category==='work'?t('Work'):t('Personal')}>
    {mark}<SignInToAdd provider={provider} category={category} onCancel={()=>{setMethod('subscription');requestAnimationFrame(()=>go.current?.focus());}}
      onAdded={(added:Added)=>onAdded(added.id,added.state==='existing'?'existing':'rename')}/>
  </OpenCard>;
  const side=<div ref={seg} className="dv-field"><span>{t('This account is for')}</span>
    <Segmented label={t('This account is for')} value={category} disabled={busy} onChange={next=>{setCategory(next);setError('');}}
      options={[{value:'work',label:<span className="dv-work-name">{BRIEFCASE}{t('Work')}</span>},{value:'personal',label:t('Personal')}]}/>
    <small>{t('A work account only runs projects under Work, and a personal one only projects under Personal, so your employer’s code never lands on your personal subscription.')}</small></div>;
  const runs=category==='work'&&<div className="dv-field"><span>{t('It will run')}</span>
    <small>{waiting.data?.names.length?waiting.data.names.length===1?t('{names}, the Work project waiting for a work account, once you bring it in from Home.',{names:formatList(waiting.data.names)})
      :t('{names}, the Work projects waiting for a work account, once you bring them in from Home.',{names:formatList(waiting.data.names)}):t('Projects under Work.')}
      {' '}{t('You can change a project’s account in its settings.')}</small></div>;
  if(method==='subscription')return <OpenCard title={title} onEscape={onCancel}>
    {mark}{side}{runs}
    <p className="dv-card-text">{category==='work'
      ?t('Opens {provider}’s sign-in page in your browser. Sign in with your work account. The sign-in is kept in a folder of its own on this Mac, apart from your personal one.',{provider:info.company==='OpenAI'?'OpenAI':'Claude'})
      :BILLING[provider].subscription} {t('Dervo shows which account signed in before adding it.')}</p>
    <div className="dv-card-actions"><button type="button" className="dv-link dv-add-key" onClick={()=>{setMethod('api_key');setError('');}}>{t('Use an API key instead')}</button>
      <button type="button" className="dv-btn dv-btn-quiet" onClick={onCancel}>{t('Cancel')}</button>
      <button ref={go} type="button" className="dv-btn dv-btn-primary" onClick={()=>setMethod('signing')}>{info.signIn}</button></div>
  </OpenCard>;
  return <OpenCard title={title} onEscape={busy?undefined:onCancel}>
    <form style={{display:'contents'}} onSubmit={event=>{event.preventDefault();if(keyReady)void submit();}}>
      {mark}{side}
      <p className="dv-card-text">{BILLING[provider].api_key}</p>
      <label className="dv-field"><span>{info.keyLabel}</span>
        <input className="dv-input dv-mono" type="password" aria-label={info.keyLabel} autoComplete="off" spellCheck={false} placeholder={`${info.keyPrefix}…`}
          value={key} disabled={busy} aria-invalid={error?true:undefined} onChange={event=>{setKey(event.target.value);setError('');}}/>
        <small>{t('Get one at {site}.',{site:info.keyWhere})} {STORED[provider]}</small></label>
      <div className="dv-field"><span id={`dv-limit-${provider}`}>{t('Monthly limit in Dervo')}</span>
        <Segmented label={t('Monthly limit in Dervo')} value={limit} onChange={setLimit} disabled={busy} options={LIMITS.map(value=>({value:String(value??'none'),label:limitLabel(value)}))}/>
        <small>{LIMIT_NOTE[provider]}</small></div>
      {runs}
      {error&&<p className="dv-field-note" role="alert">{error}</p>}
      <div className="dv-card-actions"><button type="button" className="dv-link dv-add-key" disabled={busy} onClick={()=>{setMethod('subscription');setError('');}}>{t('Sign in instead')}</button>
        <button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={onCancel}>{t('Cancel')}</button>
        <button type="submit" className="dv-btn dv-btn-primary" disabled={busy||!keyReady}>{busy?t('Checking the key…'):t('Check and add key')}</button></div>
    </form>
  </OpenCard>;
}

/** Help between your accounts: which of your accounts are linked, whether each Claude account may ask its linked helpers,
 * and linking two more. Linking is by account, never by matching email. There's no unlink yet. */
function HelpBetweenAccounts({profiles,usage,boundaries:all,actions,refresh}:{profiles:Profile[];usage?:UsageSummary;boundaries:HarnessBoundary[];actions:string[];refresh:()=>Promise<unknown>}){
  // A link with a removed account in it (beta.18 Remove) links nothing any more, so it isn't listed.
  const boundaries=all.filter(boundary=>boundary.profiles.every(id=>profiles.some(profile=>profile.id===id)));
  const [linking,setLinking]=useState(false),[first,setFirst]=useState<string|null>(null),[second,setSecond]=useState<string|null>(null);
  const [busy,setBusy]=useState(''),[error,setError]=useState('');
  const personal=profiles.filter(profile=>profile.category==='personal');
  const linked=new Set(boundaries.flatMap(boundary=>boundary.profiles));
  const candidates=personal.filter(profile=>!linked.has(profile.id));
  const full=(id:string)=>{const profile=profiles.find(p=>p.id===id);return profile?`${(PROVIDER[profile.provider]??PROVIDER.claude).label} ${nameOf(profile,usage?.accounts.find(a=>a.profile_id===id))}`:t('An account that was removed');};
  async function run(key:string,work:()=>Promise<unknown>){setBusy(key);setError('');try{await work();await refresh();}catch(failure){setError(String(failure instanceof Error?failure.message:failure));}finally{setBusy('');}}
  if(personal.length<2&&!boundaries.length)return null;
  const options=(exclude:string|null)=>[{options:candidates.filter(p=>p.id!==exclude).map(p=>({value:p.id,label:full(p.id)}))}];
  return <SettingsSection title={t('Help between your accounts')} id="dv-accounts-help">
    <p className="dv-sec-note">{t('Linked accounts can ask each other for help, in text, recorded in the thread. Nothing links by matching email.')}</p>
    {boundaries.map(boundary=><SettingRow key={boundary.id} name={formatList(boundary.profiles.map(full))}
      line={t('Linked on {date}.',{date:shortDate(boundary.created_at)})}/>)}
    {personal.filter(profile=>profile.provider==='claude').map(profile=>{
      const label=t('{name} can ask its helpers',{name:full(profile.id)}),on=linked.has(profile.id);
      return <SettingRow key={profile.id} name={label} disabled={!on} line={on?t('Off by default. Codex helpers only answer. They never ask.'):t('Link this account first.')}
        end={<Switch label={label} checked={actions.includes(profile.id)} disabled={!on||!!busy} onChange={next=>void run('ask',()=>local('harness_actions',{profileId:profile.id,enabled:next}))}/>}/>;
    })}
    {linking?<OpenCard title={t('Link two accounts')} onEscape={()=>setLinking(false)}>
      <div className="dv-link-pick"><Select label={t('First account')} placeholder={t('First account')} value={first} groups={options(second)} onChange={setFirst}/>
        <span className="dv-caption">{t('and')}</span><Select label={t('Second account')} placeholder={t('Second account')} value={second} groups={options(first)} onChange={setSecond}/></div>
      <div className="dv-card-actions"><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setLinking(false)}>{t('Cancel')}</button>
        <button type="button" className="dv-btn dv-btn-primary" disabled={!!busy||!first||!second||first===second}
          onClick={()=>void run('link',()=>local('harness_boundary',{profileIds:[first,second]})).then(()=>{setLinking(false);setFirst(null);setSecond(null);})}>{busy==='link'?t('Linking…'):t('Link')}</button></div>
    </OpenCard>
    :<AddRow disabled={candidates.length<2} title={candidates.length<2?t('Add another personal account to link.'):undefined} onClick={()=>setLinking(true)}>{t('Link two accounts')}</AddRow>}
    {candidates.length<2&&!linking&&<p className="dv-field-note">{t('Add another personal account to link.')}</p>}
    {error&&<p className="dv-field-note" role="alert">{error}</p>}
  </SettingsSection>;
}
