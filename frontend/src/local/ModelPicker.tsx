import {useEffect,useMemo,useRef,useState,type KeyboardEvent} from 'react';
import {isProjectTerminalKey} from './terminalKeys';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Popover,PopoverContent,PopoverTrigger} from '@/shared/ui/popover';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuRadioGroup,DropdownMenuRadioItem,DropdownMenuSeparator,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {local,type CheckEntry,type CollabReview,type UsageAccount} from './api';
import {agentLabel,type ReviewOverrideValue} from './collaboration';
import {nextIndex} from './DervoControls';
import {StatusGlyph} from './StatusGlyph';
import {HEAVY_EFFORTS,accountCaption,accountDetail,accountsFrom,accountsShareProvider,asksBeforeFable,chipEffort,claudeLine,codexLine,defaultEffort,defaultLine,effortDescription,effortSourceLine,
  CLAUDE_OLDER,claudeModelsFor,effortLabel,heavyLine,effortLine,effortsFor,fableCreditLabel,TEAM_FABLE_NOTICE,isFable,modelLabel,promoteLabel,thinkingLine,
  hasLeft,leavingLine,modelLeavingLine,retirementFor,type Account,type Catalog,type CatalogModel,type Efforts,type Profile,type Retirement} from './models';
import {useClaudeVersion} from './AddProject';
import {accountFor,launchLabel,type LaunchView,type Picked} from './homeLaunch';
import './ModelPicker.css';
import {WorkName} from './WorkMark';
import {HomeModelSetting,type HomeModel} from './HomeModelSetting';
import {t} from '@/i18n';

export {CLAUDE_CHOICES,PROVIDER_NAMES,effortsFor,modelLabel,fableCostsCredits,claudeLine,effortLabel} from './models';

/** The model picker (design sweep, 19 Sep; project-settings/model-picker-*.html; compact in beta.18). Two panes: your
 * accounts by name on the left, grouped under Claude and Codex, one line each with what it has left as a small figure;
 * the highlighted account's models on the right, one line each, with the pointed (or chosen) model's line under the
 * list, then Thinking with where it comes from, and this message's check and second review. Bryce switches between his
 * personal Claude account and his Team one, so every account is always in view and moving a thread is one pick. Bryce,
 * 7 Oct 2026: the old one was "tall and busy"; it's about half the area now, with every function it had.
 *
 * A thread whose account was removed (account_signin.py) says "Pick an account" on the chip and in the picker, and moves
 * only when you pick a model on one of yours.
 *
 * Pointing at another account shows its models and says what moving does before anything is saved. Picking a model on
 * the thread's own account changes only the model (conversation_model_set, the session carries on); picking one on
 * another account moves the thread in one checked write (conversation_profile_set, a new session there). Fable on a
 * paid-credits plan asks once per account. Nothing here changes the project's default: that's Account and model. */

type Binding={profile_id:string;provider:string;model:string;method?:string};
type Row={id:string;label:string;line:string;tag?:string;title?:string;unavailable?:string};
export type SettingsTarget='model'|'accounts'|'checks';

const CHEV=<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4.5 6.5L8 10l3.5-3.5"/></svg>;
const CHEV_R=<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>;
const TICK=<svg className="dv-pk-tick" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>;

/** The Codex catalog for one account, loaded once when something needs it. */
export function useCatalog(profile:Pick<Profile,'id'|'provider'>|undefined,load:boolean){
  const catalog=useQuery({queryKey:['codex-models',profile?.id??''],enabled:profile?.provider==='codex',queryFn:()=>local<Catalog>('model_status',{profileId:profile!.id}),
    refetchInterval:q=>q.state.data?.state==='checking'?500:false});
  const asked=useRef(''),[error,setError]=useState('');
  async function check(){
    if(!profile)return;
    asked.current=profile.id;setError('');
    try{await local('model_check',{profileId:profile.id});await catalog.refetch();}
    catch(failure){asked.current='';setError(failure instanceof Error?failure.message:String(failure));}
  }
  useEffect(()=>{if(load&&profile?.provider==='codex'&&catalog.data?.state==='unchecked'&&asked.current!==profile.id)void check();},[load,profile?.id,catalog.data?.state]);
  return {data:catalog.data,loading:profile?.provider==='codex'&&(!catalog.data||catalog.data.state==='checking'||catalog.data.state==='unchecked')&&!error,error:error||(catalog.error?String(catalog.error):''),retry:check};
}

/** Why the thread's own model can't run, or null: the Codex catalog loaded and doesn't list it any more. Claude's list
 * isn't read from the CLI yet (PLAN-native B5), so a Claude model is never flagged here. */
export function modelProblem(account:Pick<Account,'name'|'provider'>|undefined,binding:Binding,catalog?:Catalog):string|null{
  if(!account||binding.provider!=='codex'||catalog?.state!=='catalog')return null;
  if(catalog.models.some(m=>m.model===binding.model))return null;
  // A model Codex retired says when it left; RetireNote above the list says what that means and offers the move.
  const leaving=retirementFor(binding.model);
  return leaving?leavingLine(leaving)
    :t('{name} doesn’t offer it any more, so messages here can’t start. Pick another model.',{name:account.name});
}

/** A thread, Home or a project's default on a model that's leaving Codex (GPT-5.5 on 14 Oct): it says so where the
 * model is picked, and moves to the named replacement in one click. Nothing moves until that click (AGENTS.md: no
 * silent fallback). Without the replacement in this account's catalog there's no button, only the words. */
export function RetireNote({retirement,catalog,stays,gone,move,busy,onMove}:{retirement:Retirement;catalog?:CatalogModel[];
  /** Before the day: what stays on the model until you move it. From the day: what can't run. */
  stays:string;gone:string;
  /** The button, given the replacement's name: "Move this thread to GPT-6-Sol". */
  move:(model:string)=>string;busy?:boolean;onMove:(successor:string,label:string)=>void}){
  const name=modelLabel('codex',retirement.model,catalog),next=modelLabel('codex',retirement.successor,catalog);
  const offered=!catalog||catalog.some(m=>m.model===retirement.successor);
  const left=hasLeft(retirement);
  return <div className="dv-pk-note dv-pk-retire" role="note" data-left={left||undefined}>
    <p><b>{modelLeavingLine(name,retirement)}</b> {left?gone:stays}{!offered&&` ${t('This account doesn’t offer {model}, so pick another model.',{model:next})}`}</p>
    {offered&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>onMove(retirement.successor,next)}>{move(next)}</button>}
  </div>;
}

function rowsFor(account:Account,binding:Binding,catalog:Catalog|undefined,own:boolean,claudeVersion:string|null):Row[]{
  if(account.provider==='claude'){
    const creditLabel=fableCreditLabel(account.profile.plan);
    const rows:Row[]=claudeModelsFor(claudeVersion).map(m=>({id:m.id,label:m.label,line:claudeLine(m.id,account.profile.plan),tag:isFable(m.id)?creditLabel:undefined}));
    // The thread's own model stays in view even when it's one the list doesn't name (an older Fable, a typed ID).
    if(own&&!rows.some(r=>r.id===binding.model))rows.push({id:binding.model,label:modelLabel('claude',binding.model),line:claudeLine(binding.model,account.profile.plan)||t('A model ID you chose. Claude checks access when a task starts.'),
      tag:isFable(binding.model)?creditLabel:undefined});
    return rows;
  }
  const models=catalog?.state==='catalog'?catalog.models:[];
  const rows:Row[]=models.map((m:CatalogModel)=>({id:m.model,label:m.displayName??modelLabel('codex',m.model,models),line:codexLine(m),title:m.description&&m.description!==codexLine(m)?m.description:undefined}));
  const problem=own?modelProblem(account,binding,catalog):null;
  if(problem)rows.unshift({id:binding.model,label:modelLabel('codex',binding.model),line:problem,unavailable:problem});
  return rows;
}

/** An account's row figure: what it has left, or an API key's spend against its limit. The full caption goes to the row's
 * title and to screen readers. Nothing when the provider hasn't reported. */
export function accountFigure(account:Account):string{
  const u=account.usage;
  if(u?.basis==='spend_limit'&&u.monthly_limit_usd)return `$${Math.round(u.cost_month_usd??0)}/$${Math.round(u.monthly_limit_usd)}`;
  return account.left!==null?`${account.left}%`:'';
}

type Props={projectId:string;channelId:string;profiles:Profile[];usage?:UsageAccount[];binding:Binding;
  /** The project's default account and model, for "New threads use Personal · Opus 5". */
  project?:{profile_id:string;model:string;context?:'work'|'personal'|null};
  /** What a run here uses (coding_permission's owner_effort). The picker reads where it comes from itself. */
  effort?:string|null;disabled?:boolean;
  /** Why the thread can't switch right now (a task running, a message queued), or null. */
  busy?:string|null;
  /** The helper agent this thread belongs to, which keeps its account and model. */
  agent?:string|null;
  onChanged:()=>Promise<unknown>;
  checks?:CheckEntry[];check?:string;onCheck?:(value:string)=>void;
  review?:CollabReview|null;reviewValue?:ReviewOverrideValue;onReview?:(value:ReviewOverrideValue)=>void;
  overrides?:string[];
  onSettings?:(page:SettingsTarget)=>void;
  /** Another way in asked for this thread's picker ("Change model for this conversation"): it opens, then says so. */
  openNow?:boolean;onOpened?:()=>void;
  /** Each time the popover opens, however it was opened (usage.ts counts it). */
  onOpen?:()=>void};

export function ModelPicker({projectId,channelId,profiles,usage=[],binding,project,effort,disabled,busy,agent,onChanged,checks,check='default',onCheck,review,reviewValue='default',onReview,overrides=[],onSettings,openNow,onOpened,onOpen}:Props){
  const client=useQueryClient();
  // Only the project's side (b16-work picker-work): a Work project lists work accounts, and says why the others don't show.
  const work=project?.context==='work';
  const accounts=useMemo(()=>accountsFrom(profiles,usage,Date.now(),work?'work':'personal'),[profiles,usage,work]);
  const own=accounts.find(a=>a.id===binding.profile_id);
  // Its account isn't one of yours on this side any more (removed, beta.18): nothing runs until you pick one.
  const lost=!own&&profiles.length>0;
  const [open,setOpen]=useState(false);
  const [pointedModel,setPointedModel]=useState<string|null>(null);
  const [viewing,setViewing]=useState(binding.profile_id);
  const [pending,setPending]=useState(''),[error,setError]=useState('');
  const [asking,setAsking]=useState<string|null>(null);
  const [more,setMore]=useState(false),[pointed,setPointed]=useState<string|null|undefined>(undefined);
  const shown=accounts.find(a=>a.id===viewing)??own??accounts[0];
  const onOwn=!!shown&&shown.id===binding.profile_id;
  const ownCatalog=useCatalog(own?.profile,open);
  const shownCatalog=useCatalog(shown?.profile,open);
  const catalog=onOwn?ownCatalog:shownCatalog;
  const problem=modelProblem(own,binding,ownCatalog.data);
  // A thread on a model leaving Codex: said on the chip and at the top of the picker, moved only by your click.
  const leaving=binding.provider==='codex'?retirementFor(binding.model,ownCatalog.data?.models):null;
  const claudeVersion=useClaudeVersion(open&&shown?.provider==='claude');
  // This thread's own level, the defaults it falls back to and which one a run uses (16.4 item 3).
  const efforts=useQuery({queryKey:['coding-efforts',projectId,channelId],staleTime:30000,
    queryFn:async()=>(await local<{efforts?:Efforts}>('coding_permission',{projectId,channelId})).efforts??null});
  const levelsOf=efforts.data??undefined;
  const rows=shown?rowsFor(shown,binding,catalog.data,onOwn,claudeVersion):[];
  const locked=agent?t('This thread belongs to {agent}, which keeps its account and model.',{agent:agentLabel(agent)}):busy??null;
  const accountRefs=useRef<(HTMLButtonElement|null)[]>([]),modelRefs=useRef<(HTMLButtonElement|null)[]>([]);
  const hover=useRef<number>(0),confirmRef=useRef<HTMLButtonElement>(null),chipRef=useRef<HTMLButtonElement>(null);

  useEffect(()=>{if(!open){setViewing(binding.profile_id);setAsking(null);setError('');setMore(false);setPointed(undefined);setPointedModel(null);}},[open,binding.profile_id]);
  useEffect(()=>()=>window.clearTimeout(hover.current),[]);
  useEffect(()=>{if(!openNow)return;if(!disabled&&chipRef.current?.getClientRects().length)setOpen(true);onOpened?.();},[openNow,disabled]);
  useEffect(()=>{if(open)onOpen?.();},[open]);  // however it opened: click, ⌘⇧M or a jump
  // The Fable question takes focus when it opens (interactions.md: "Focus to Use Fable 5.1").
  useEffect(()=>{if(asking)requestAnimationFrame(()=>confirmRef.current?.focus());},[asking]);
  // ⌘⇧M opens the picker from anywhere in the thread (interactions.md; no other composer shortcut uses it).
  useEffect(()=>{
    // Only while the chip is on screen: Settings keeps the thread mounted out of sight, and a picker there would float alone.
    const keys=(event:globalThis.KeyboardEvent)=>{if(event.defaultPrevented||event.isComposing||event.repeat||isProjectTerminalKey(event))return;if(event.metaKey&&event.shiftKey&&!event.altKey&&!event.ctrlKey&&event.key.toLowerCase()==='m'&&!disabled&&chipRef.current?.getClientRects().length){event.preventDefault();setOpen(true);}};
    window.addEventListener('keydown',keys);return ()=>window.removeEventListener('keydown',keys);
  },[disabled]);

  async function save(key:string,work:()=>Promise<unknown>,close=true){
    if(pending)return;
    setPending(key);setError('');
    try{await work();await onChanged();if(close)setOpen(false);}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setPending('');}
  }
  const refresh=()=>Promise.all([['local-binding',projectId,channelId],['local-conversations',projectId],['conversation-feed',projectId,channelId],['local-profiles'],['coding-efforts',projectId,channelId]]
    .map(queryKey=>client.invalidateQueries({queryKey})));
  /** Picks a model on the account being shown. The thread's own account changes only the model; another account moves
   * the thread there in one write. Fable on Pro or Team asks once per account first. */
  function pick(row:Row,confirmed=false){
    if(!shown||row.unavailable||locked)return;
    if(onOwn&&row.id===binding.model)return;
    if(!confirmed&&asksBeforeFable(shown.profile,row.id)){setAsking(row.id);return;}
    setAsking(null);
    const account=shown;
    void save(`model:${row.id}`,async()=>{
      if(confirmed)await local('profile_fable_ack',{profileId:account.id});
      if(account.id===binding.profile_id)await local('conversation_model_set',{projectId,channelId,expectedModel:binding.model,model:row.id});
      else await local('conversation_profile_set',{projectId,channelId,expectedProfileId:binding.profile_id,profileId:account.id,expectedModel:binding.model,model:row.id});
      await refresh();
    });
  }
  /** Thinking in the thread's picker sets this thread only. Default clears it, so the thread falls back. */
  function setEffort(level:string|null,scope:'thread'|'promote'='thread'){
    if(!levelsOf||(scope==='thread'&&(level??null)===levelsOf.thread))return;
    void save(scope==='promote'?'promote':'effort',async()=>{
      await local('coding_effort_set',{projectId,channelId,effort:level,level:scope});
      await client.invalidateQueries({queryKey:['coding-efforts',projectId]});
    },false);
  }
  function view(account:Account){window.clearTimeout(hover.current);if(account.id!==shown?.id){setViewing(account.id);setAsking(null);setError('');setPointedModel(null);}}

  // Keys (interactions.md): ↑ ↓ within a pane, ← → between them, a letter jumps to the next row starting with it.
  function typeAhead(event:KeyboardEvent,labels:string[],at:number,focus:(i:number)=>void){
    if(event.key.length!==1||!/\S/.test(event.key)||event.metaKey||event.ctrlKey||event.altKey)return false;
    const letter=event.key.toLowerCase();
    for(let n=1;n<=labels.length;n++){const i=(at+n)%labels.length;if(labels[i].toLowerCase().startsWith(letter)){event.preventDefault();focus(i);return true;}}
    return false;
  }
  const modelIndex=()=>{const i=rows.findIndex(r=>onOwn&&r.id===binding.model&&!r.unavailable);return i>=0?i:Math.max(0,rows.findIndex(r=>!r.unavailable));};
  function accountKeys(event:KeyboardEvent,at:number){
    const focus=(i:number)=>{accountRefs.current[i]?.focus();view(accounts[i]);};
    if(event.key==='ArrowRight'){event.preventDefault();modelRefs.current[modelIndex()]?.focus();return;}
    if(typeAhead(event,accounts.map(a=>a.name),at,focus))return;
    const next=nextIndex(event.key==='ArrowDown'||event.key==='ArrowUp'||event.key==='Home'||event.key==='End'?event.key:'',at,accounts.map(()=>false));
    if(next>=0){event.preventDefault();focus(next);}
  }
  function modelKeys(event:KeyboardEvent,at:number){
    if(event.key==='ArrowLeft'){event.preventDefault();accountRefs.current[Math.max(0,accounts.findIndex(a=>a.id===shown?.id))]?.focus();return;}
    if(typeAhead(event,rows.map(r=>r.label),at,i=>modelRefs.current[i]?.focus()))return;
    const next=nextIndex(['ArrowDown','ArrowUp','Home','End'].includes(event.key)?event.key:'',at,rows.map(()=>false));
    if(next>=0){event.preventDefault();modelRefs.current[next]?.focus();}
  }

  const named=modelLabel(binding.provider,binding.model,ownCatalog.data?.models);
  // A model the account no longer offers has no thinking to speak of: the chip names the model alone.
  // The chip names a level only when this thread's differs from the default it would fall back to.
  const chipLevel=chipEffort(levelsOf);
  const choices=`${chipLevel&&!problem?` · ${effortLabel(chipLevel)}`:''}${overrides.map(label=>` · ${label}`).join('')}`;
  const ownLevel=levelsOf?.thread??null;
  const showAccount=!!own&&(work||accountsShareProvider(accounts));
  const projectAccount=accounts.find(a=>a.id===project?.profile_id);
  const projectLine=project&&projectAccount?`${projectAccount.name} · ${modelLabel(projectAccount.provider,project.model)}`:null;
  const levels=onOwn&&!problem?effortsFor(binding.provider,binding.model,ownCatalog.data?.models):[];
  const heavy=levels.filter(l=>HEAVY_EFFORTS.includes(l)),light=levels.filter(l=>!HEAVY_EFFORTS.includes(l));
  // A lone heavy level (Claude's Max) shows in the row: a More button in its place takes as much room (Harry, 27 Sep).
  const unfolded=more||heavy.length===1||(!!ownLevel&&heavy.includes(ownLevel));
  const resolved=defaultEffort(binding.provider,binding.model,ownCatalog.data?.models);
  const lineLevel=pointed!==undefined?pointed:ownLevel;
  const showsReview=!!onReview;const showsCheck=!!onCheck&&!!checks&&checks.length>1;
  const reviewOn=!!review?.enabled&&!!review.reviewer&&!review.skipped_here;
  const summary=`${own?`${own.name}, `:''}${binding.provider==='claude'?'Claude ':''}${named}${choices}`;
  // One description at a time, under the list: the model you're pointing at, else the one chosen here.
  const described=rows.find(r=>r.id===pointedModel)??(onOwn?rows.find(r=>r.id===binding.model&&!r.unavailable):undefined);

  return <Popover open={open} onOpenChange={next=>{setOpen(next);if(next)void client.invalidateQueries({queryKey:['local-binding',projectId,channelId]});}}>
    <PopoverTrigger asChild>
      <button ref={chipRef} type="button" className="dv-chip dv-pk-chip" data-testid="model-picker" disabled={disabled} aria-haspopup="dialog"
        aria-label={lost?t('Account and model: this thread’s account isn’t connected any more. Pick an account.'):problem?t('Account and model: {summary}. This model isn’t offered any more',{summary}):leaving?`${t('Account and model: {summary}',{summary})}. ${leavingLine(leaving)}`:t('Account and model: {summary}',{summary})} title={[work?t('Work'):null,own?.name,own?.identity,named,(levelsOf?.effective??effort)?effortLabel((levelsOf?.effective??effort)!):null,leaving?leavingLine(leaving):null].filter(Boolean).join(' · ')}>
        {(problem||leaving||lost)&&<StatusGlyph state="needs" accent={lost||!!problem||(!!leaving&&hasLeft(leaving))}/>}
        {showAccount&&<><span className="dv-pk-acct-name">{work?<WorkName>{own!.name}</WorkName>:own!.name}</span><span className="dv-pk-sep" aria-hidden="true"/></>}
        <span className="dv-pk-model-name">{lost?t('Pick an account'):<>{named}{choices}</>}</span>{CHEV}
      </button>
    </PopoverTrigger>
    <PopoverContent align="start" side="top" sideOffset={8} className="dv-picker dv-thread-picker" aria-label={t('Account and model for this thread')} role="dialog"
      // Before a Codex catalog loads there's no model row yet: focus the thread's own account, not the first one, because
      // focusing an account shows its models and the picker would open on another account's list.
      onOpenAutoFocus={event=>{event.preventDefault();requestAnimationFrame(()=>(modelRefs.current[modelIndex()]??accountRefs.current[Math.max(0,accounts.findIndex(a=>a.id===binding.profile_id))])?.focus());}}
      onEscapeKeyDown={event=>{if(!asking)return;event.preventDefault();const at=rows.findIndex(r=>r.id===asking);setAsking(null);modelRefs.current[at]?.focus();}}>
      {!accounts.length?<div className="dv-pk-empty"><p>{work?t('No work accounts yet. This is a Work project, so only a work account can run it. Add one in Connected accounts.'):t('No accounts yet. Add one in Connected accounts.')}</p><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>{setOpen(false);onSettings?.('accounts');}}>{t('Open Connected accounts')}</button></div>:<>
      <div className="dv-pk-accts" role="listbox" aria-label={t('Account')} aria-orientation="vertical">
        {(['claude','codex'] as const).map(provider=>{const group=accounts.filter(a=>a.provider===provider);const label=`${provider==='claude'?'Claude':'Codex'}${work?` · ${t('Work')}`:''}`;
          if(!group.length&&work)return <div key={provider} role="group" aria-label={label} className="dv-pk-group"><p className="dv-label" aria-hidden="true">{label}</p>
            <div className="dv-pk-add">{t('No work {provider} account',{provider:provider==='claude'?'Claude':'Codex'})}<button type="button" className="dv-link" onClick={()=>{setOpen(false);onSettings?.('accounts');}}>{t('Add one')}</button></div></div>;
          return group.length?<div key={provider} role="group" aria-label={label} className="dv-pk-group">
          <p className="dv-label" aria-hidden="true">{label}</p>
          {group.map(account=>{const at=accounts.indexOf(account);const off=!!locked&&account.id!==binding.profile_id;return <button key={account.id} ref={el=>{accountRefs.current[at]=el;}} type="button" role="option"
            className="dv-pk-acct" aria-selected={account.id===shown?.id} aria-current={account.id===binding.profile_id?'true':undefined} aria-disabled={off||undefined}
            tabIndex={account.id===shown?.id?0:-1} title={`${account.identity} · ${accountCaption(account)}`} onClick={()=>view(account)} onFocus={()=>{if(!off)view(account);}} onKeyDown={event=>accountKeys(event,at)}
            onPointerEnter={()=>{if(off)return;window.clearTimeout(hover.current);hover.current=window.setTimeout(()=>view(account),150);}} onPointerLeave={()=>window.clearTimeout(hover.current)}>
            <b>{account.name}</b><small data-low={account.left!==null&&account.left<=10||undefined}><span className="dv-pk-fig" aria-hidden="true">{accountFigure(account)}</span><span className="sr-only">{accountCaption(account)}</span></small>
            {account.id===binding.profile_id&&TICK}
          </button>;})}
        </div>:null;})}
        {work&&<p className="dv-pk-side-note">{t('This is a Work project, so only work accounts show. Your personal ones never run it.')}</p>}
        <button type="button" className="dv-btn dv-btn-quiet dv-pk-manage" onClick={()=>{setOpen(false);onSettings?.('accounts');}}>{t('Manage accounts')}</button>
      </div>
      <div className="dv-pk-models">
        {shown&&<div className="dv-pk-head"><span className="dv-title">{shown.name}</span><span className="dv-caption">{accountDetail(shown)}</span></div>}
        {locked?<p className="dv-pk-note" role="note">{locked}</p>
          :lost&&shown?<p className="dv-pk-note" role="note">{t('This thread’s account isn’t connected any more, so nothing runs here until you pick one. Picking a model moves it to {name}, with this thread’s recent messages.',{name:shown.name})}</p>
          :!onOwn&&shown&&<p className="dv-pk-note" role="note">{t('Picking a model here moves this thread to {name}. Its next message starts a new session there, with this thread’s recent messages.',{name:shown.name})}</p>}
        {onOwn&&!locked&&leaving&&ownCatalog.data?.state==='catalog'&&<RetireNote retirement={leaving} catalog={ownCatalog.data.models} busy={!!pending}
          stays={t('This thread stays on it until you move it.')} gone={t('Messages here can’t start until you move this thread.')}
          move={model=>t('Move this thread to {model}',{model})} onMove={(id,label)=>pick({id,label,line:''})}/>}
        {shown?.provider==='codex'&&catalog.loading&&!rows.length?<p className="dv-pk-note" role="status">{t('Loading {name}’s models…',{name:shown.name})}</p>
          :shown?.provider==='codex'&&(catalog.error||(catalog.data&&catalog.data.state!=='catalog'&&catalog.data.state!=='checking'&&catalog.data.state!=='unchecked'))&&!rows.length?<div className="dv-pk-note" role="alert"><p>{t('Couldn’t load {name}’s models: {error}',{name:shown.name,error:catalog.error||catalog.data?.detail||''})}</p><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void catalog.retry()}>{t('Try again')}</button></div>
          :<div className="dv-pk-list" role="listbox" aria-label={t('{name} models',{name:shown?.name??''})}>
          {rows.map((row,at)=>{
            const current=onOwn&&row.id===binding.model&&!row.unavailable;
            const off=!!row.unavailable||(!!locked&&!current)||(!!pending&&!pending.endsWith(row.id));
            const confirming=asking===row.id;
            return <div key={row.id} className="dv-pk-item" data-open={confirming||undefined}>
              <button ref={el=>{modelRefs.current[at]=el;}} type="button" role="option" className="dv-pk-model" aria-selected={current} aria-disabled={off||undefined}
                data-busy={pending===`model:${row.id}`||undefined} title={row.title} onClick={()=>{if(!off)pick(row);}} onKeyDown={event=>modelKeys(event,at)}
                onPointerEnter={()=>setPointedModel(row.id)} onPointerLeave={()=>setPointedModel(null)} onFocus={()=>setPointedModel(row.id)} onBlur={()=>setPointedModel(null)}>
                <b>{row.label}{row.tag&&<span className="dv-tag">{row.tag}</span>}</b>
                <small>{pending===`model:${row.id}`?t('Switching…'):row.line}</small>
                {current&&TICK}
              </button>
              {confirming&&shown&&<div className="dv-pk-confirm" role="group" aria-label={fableCreditLabel(shown.profile.plan)}>
                <p><b>{t('Use {model} in this thread?',{model:row.label})}</b></p>
                <p>{shown.profile.plan==='team'?TEAM_FABLE_NOTICE
                  :t('Each message here will cost money on top of your {plan} plan.',{plan:shown.plan||'Pro'})} {t('Dervo asks once for {name}.',{name:shown.name})}</p>
                <div className="dv-pk-actions"><button ref={confirmRef} type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!pending}
                  onClick={()=>pick(row,true)}>{t('Use {model}',{model:row.label})}</button><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>{setAsking(null);modelRefs.current[at]?.focus();}}>{t('Cancel')}</button></div>
              </div>}
            </div>;
          })}
        </div>}
        {described&&!described.unavailable&&!asking&&<p className="dv-pk-line dv-pk-desc" aria-hidden="true">{described.line}</p>}
        {error&&<p className="dv-pk-error" role="alert"><StatusGlyph state="failed"/>{error}</p>}
        {levels.length>0&&<div className="dv-pk-sec" role="group" aria-label={t('Thinking')}>
          <div className="dv-pk-row-head"><span className="dv-label">{t('Thinking')}</span><span className="dv-caption">{t('for {model} in this thread',{model:named})}</span></div>
          <ThinkingLevels value={ownLevel} light={light} heavy={unfolded?heavy:[]} more={heavy.length>0&&!unfolded} disabled={!levelsOf||pending==='effort'||pending==='promote'}
            describe={level=>effortDescription(binding.model,level,ownCatalog.data?.models)}
            onPick={level=>setEffort(level)} onMore={()=>setMore(true)} onPoint={setPointed}/>
          <p className="dv-pk-line" aria-live="polite">{more&&pointed===undefined&&!(ownLevel&&heavy.includes(ownLevel))?heavyLine(heavy)
            :lineLevel||!levelsOf?thinkingLine(lineLevel,resolved,binding.model,binding.provider):defaultLine(levelsOf,resolved,binding.model,binding.provider)}</p>
          {levelsOf&&<div className="dv-pk-effort-foot" data-testid="thinking-source">
            <span>{t('Thinking: {source}',{source:effortSourceLine(levelsOf,binding.provider)})}</span>
            {/* Only when the thread has its own level: two quiet ways back, never a dialog (16.4 item 3). */}
            {levelsOf.thread&&<span className="dv-pk-effort-actions">
              <button type="button" className="dv-btn dv-btn-quiet" disabled={!!pending} onClick={()=>setEffort(null)}>{t('Back to default')}</button>
              <button type="button" className="dv-btn dv-btn-quiet" disabled={!!pending} onClick={()=>setEffort(levelsOf.thread,'promote')}>{promoteLabel(levelsOf)}</button>
            </span>}
          </div>}
        </div>}
        {onOwn&&!problem&&(showsCheck||showsReview)&&<div className="dv-pk-sec" role="group" aria-label={t('This message')}>
          <div className="dv-pk-row-head"><span className="dv-label">{t('This message')}</span></div>
          <div className="dv-pk-inline">
            {showsCheck&&<InlineSelect label={t('Check')} value={check} onChange={value=>onCheck!(value)}
              options={[{value:'default',label:checks![0].label,sub:checks![0].test_file?t('on {test_file}',{test_file:checks![0].test_file}):undefined},...checks!.slice(1).map(entry=>({value:entry.profile,label:entry.label,sub:entry.test_file?t('on {test_file}',{test_file:entry.test_file}):undefined}))]}/>}
            {showsReview&&<InlineSelect label={t('Second review')} value={reviewValue} onChange={value=>onReview!(value as ReviewOverrideValue)}
              shown={reviewValue==='default'?(reviewOn?t('On'):t('Off')):reviewValue==='on'?t('On'):t('Off')}
              options={[{value:'default',label:review?.skipped_here?t('Project default (skipped here)'):reviewOn?t('Project default (on)'):t('Project default (off)')},{value:'off',label:t('Off')},{value:'on',label:t('On'),disabled:!reviewOn}]}
              action={!review?.enabled?{label:t('Set up a reviewer'),onSelect:()=>{setOpen(false);onSettings?.('checks');}}:undefined}/>}
          </div>
          {reviewValue==='on'&&reviewOn&&<p className="dv-pk-line">{t('Reviews this message with {reviewer}. That uses {provider} limits too.',{reviewer:agentLabel(review!.reviewer!),provider:review!.provider==='claude'?'Claude':'Codex'})}</p>}
          {reviewValue==='default'&&review?.skipped_here&&<p className="dv-pk-line">{review.skipped_here}</p>}
        </div>}
        <div className="dv-pk-foot">
          <span>{onOwn?`${t('This thread only.')}${projectLine?` ${t('New threads use {what}.',{what:projectLine})}`:''}`:`${lost?t('This thread needs an account.'):t('This thread is on {name}.',{name:own?.name??t('another account')})}${projectAccount?` ${t('New threads use {what}.',{what:projectAccount.name})}`:''}`}</span>
          <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>{setOpen(false);onSettings?.('model');}}>{t('Project defaults')}</button>
        </div>
      </div></>}
    </PopoverContent>
  </Popover>;
}

/** Thinking: Default, the everyday levels, and More for the heavy ones. One tab stop; arrows move and choose, and
 * stepping stops before Max and Ultra until More shows them (PLAN-native section 3). */
export function ThinkingLevels({value,light,heavy,more,disabled,describe=()=>undefined,onPick,onMore,onPoint=()=>{},size='small'}:{value:string|null;light:string[];heavy:string[];more:boolean;disabled:boolean;
  describe?:(level:string)=>string|undefined;onPick:(level:string|null)=>void;onMore:()=>void;onPoint?:(level:string|null|undefined)=>void;size?:'small'|'regular'}){
  const options:(string|null)[]=[null,...light,...heavy];
  const refs=useRef<(HTMLButtonElement|null)[]>([]);
  const current=Math.max(0,options.indexOf(value));
  function keys(event:KeyboardEvent,at:number){
    const next=nextIndex(event.key,at,options.map(()=>false));
    if(next<0)return;
    event.preventDefault();refs.current[next]?.focus();onPick(options[next]);
  }
  return <div className={`dv-seg${size==='small'?' dv-pk-seg':' dv-thinking-seg'}`} role="radiogroup" aria-label={t('Thinking')} onPointerLeave={()=>onPoint(undefined)}>
    {options.map((level,at)=><button key={level??'default'} ref={el=>{refs.current[at]=el;}} type="button" role="radio" aria-checked={level===value} tabIndex={at===current?0:-1} disabled={disabled}
      title={level?[effortLine(level),describe(level)].filter(Boolean).join(' '):undefined}
      onPointerEnter={()=>onPoint(level)} onFocus={()=>onPoint(level)} onBlur={()=>onPoint(undefined)} onClick={()=>onPick(level)} onKeyDown={event=>keys(event,at)}>{level?effortLabel(level):t('Default')}</button>)}
    {more&&<button type="button" className="dv-pk-more" tabIndex={-1} disabled={disabled} onClick={onMore} aria-label={t('More thinking levels')}>{t('More')}{CHEV_R}</button>}
  </div>;
}

/** A small select for this message's choices: "Check: Your review ▾", a kit menu under it. */
function InlineSelect({label,value,shown,options,onChange,action}:{label:string;value:string;shown?:string;options:{value:string;label:string;sub?:string;disabled?:boolean}[];
  onChange:(value:string)=>void;action?:{label:string;onSelect:()=>void}}){
  const chosen=options.find(o=>o.value===value);
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><button type="button" className="dv-select" aria-label={`${label}: ${shown??chosen?.label??''}`}>
      <span className="dv-select-value">{label}: <span className="dv-sub">{shown??chosen?.label}</span></span>{CHEV}
    </button></DropdownMenuTrigger>
    <DropdownMenuContent align="start" side="top">
      <DropdownMenuRadioGroup value={value} onValueChange={onChange}>
        {options.map(o=><DropdownMenuRadioItem key={o.value} value={o.value} disabled={o.disabled}><span>{o.label}{o.sub&&<span className="dv-sub"> {o.sub}</span>}</span></DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
      {action&&<><DropdownMenuSeparator/><DropdownMenuItem onSelect={action.onSelect}>{action.label}</DropdownMenuItem></>}
    </DropdownMenuContent>
  </DropdownMenu>;
}

/** Home's agent, model and thinking level for the jobs one message starts (beta.15 group 8; homeLaunch.ts). The same
 * accounts, model lines and Thinking as a thread's picker, laid out for a choice that hasn't happened yet: pick the
 * agent, then its model and how hard it thinks. What Home heard in your words shows at the top, with a way back to
 * your own pick. Nothing is saved here: the choice goes with the message, and each job's thread is set to it. */
export function HomeLaunchPicker({accounts,view,catalog,claudeVersion,homeModel,homeSetting,hasPick=false,disabled,onPick,onIgnoreWords,onReset,onSettings}:{
  accounts:Account[];view:LaunchView;catalog?:CatalogModel[]|null;claudeVersion:string|null;
  /** The model Home's own answers run on, said in the foot rather than beside the chip (design review S2). */
  homeModel?:string|null;
  /** Home's own model for this project, changed from the foot (beta.17, HomeModelSetting.tsx). */
  homeSetting?:{home:HomeModel;projectId:string;profileId:string;onSaved:(listing:unknown)=>void};
  /** You picked on the picker before typing, so ignoring the words goes back to that pick, not the project default. */
  hasPick?:boolean;disabled?:boolean;
  onPick:(pick:Picked)=>void;onIgnoreWords:()=>void;onReset:()=>void;onSettings?:()=>void}){
  const [open,setOpen]=useState(false),[more,setMore]=useState(false),[asking,setAsking]=useState<string|null>(null),[error,setError]=useState('');
  const client=useQueryClient();
  const account=view.account;
  const provider=view.provider;
  const [viewing,setViewing]=useState<string|null>(null);
  const shown=accounts.find(a=>a.id===viewing)??account??accounts[0];
  const shownCatalog=useCatalog(shown?.profile,open);
  const models:CatalogModel[]|undefined=shown?.provider==='codex'?(shown.id===account?.id?catalog??shownCatalog.data?.models:shownCatalog.data?.models):undefined;
  const [homeOpen,setHomeOpen]=useState(false);
  useEffect(()=>{if(!open){setViewing(null);setAsking(null);setError('');setMore(false);setHomeOpen(false);}},[open]);
  const onChosen=!!shown&&shown.id===account?.id;
  // A project already on an older Claude model keeps showing it, as a thread's picker does (CLAUDE_OLDER).
  const claudeRows=[...claudeModelsFor(claudeVersion),...(onChosen&&provider==='claude'?CLAUDE_OLDER.filter(m=>m.id===view.model&&!claudeModelsFor(claudeVersion).some(s=>s.id===m.id)):[])];
  const rows=!shown?[]:shown.provider==='claude'
    ?claudeRows.map(m=>({id:m.id,label:m.label,line:claudeLine(m.id,shown.profile.plan),tag:isFable(m.id)?fableCreditLabel(shown.profile.plan):undefined}))
    :(models??[]).filter(m=>!m.hidden).map(m=>({id:m.model,label:m.displayName??modelLabel('codex',m.model,models),line:codexLine(m),tag:undefined as string|undefined}));
  const levels=onChosen&&provider&&view.model?effortsFor(provider,view.model,catalog??undefined):[];
  const heavy=levels.filter(l=>HEAVY_EFFORTS.includes(l)),light=levels.filter(l=>!HEAVY_EFFORTS.includes(l));
  const unfolded=more||heavy.length===1||(!!view.effort&&heavy.includes(view.effort));
  const agents:('claude'|'codex')[]=['claude','codex'];
  async function pickModel(id:string,confirmed=false){
    if(!shown)return;
    if(!confirmed&&asksBeforeFable(shown.profile,id)){setAsking(id);return;}
    setError('');
    try{
      if(confirmed){await local('profile_fable_ack',{profileId:shown.id});await client.invalidateQueries({queryKey:['local-profiles']});}
      setAsking(null);onPick({profileId:shown.id,model:id});
    }catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  }
  const label=launchLabel(view,catalog);
  const siblings=shown?accounts.filter(a=>a.provider===shown.provider):[];
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild>
      <button type="button" className="dv-chip dv-pk-chip dv-home-launch" data-testid="home-launch" data-from={view.from} disabled={disabled} aria-haspopup="dialog"
        aria-label={[t('Agent for the jobs this message starts: {label}',{label}),view.heardLine,view.problem,view.note].filter(Boolean).join('. ')}>
        {view.problem&&<StatusGlyph state="needs" accent/>}
        <span className="dv-pk-model-name">{label}</span>{CHEV}
      </button>
    </PopoverTrigger>
    <PopoverContent align="start" side="top" sideOffset={8} className="dv-picker dv-home-launch-pop" aria-label={t('Agent, model and thinking for the jobs this message starts')} role="dialog">
      {homeOpen&&homeSetting?<HomeModelSetting {...homeSetting} account={accounts.find(a=>a.id===homeSetting.home.profile_id)} claudeVersion={claudeVersion} onBack={()=>setHomeOpen(false)}/>:
      <div className="dv-pk-models">
        <div className="dv-pk-head"><span className="dv-title">{t('For the jobs this message starts')}</span><span className="dv-caption">{view.from==='default'?t('Your project’s default'):view.from==='words'?t('From your message'):t('Your pick')}</span></div>
        {view.heardLine&&<div className="dv-pk-note dv-home-heard" role="note"><p>{view.heardLine}.</p>
          {/* Design review S4: with no pick of yours, ignoring the words means the project default, so it says that. */}
          <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={()=>{onIgnoreWords();}}>{hasPick?t('Use my pick instead'):t('Ignore my words')}</button></div>}
        {view.problem&&<p className="dv-pk-note" role="alert">{view.problem}</p>}
        {view.note&&<p className="dv-pk-note" role="note">{view.note}</p>}
        <div className="dv-home-launch-scroll">
          <div className="dv-seg dv-home-agents" role="radiogroup" aria-label={t('Agent')}>
            {agents.map(agent=>{const first=accounts.find(a=>a.provider===agent);const on=(shown?.provider??provider)===agent;
              return <button key={agent} type="button" role="radio" aria-checked={on} disabled={!first} title={first?undefined:t('No {provider} account is connected',{provider:agent==='codex'?'Codex':'Claude'})}
                onClick={()=>{if(first&&!on){setViewing((agent===provider?account:accountFor(accounts,agent))?.id??first.id);setAsking(null);}}}>{agent==='claude'?'Claude Code':'Codex'}</button>;})}
          </div>
          {/* Design review S3: accounts are their own small list under a label, one line each, so they don't read as models. */}
          {siblings.length>1&&<div className="dv-pk-sec dv-home-accounts">
            <span className="dv-label">{t('Account')}</span>
            <div className="dv-seg dv-home-acct-seg" role="radiogroup" aria-label={t('Account')}>
              {siblings.map(a=><button key={a.id} type="button" role="radio" aria-checked={a.id===shown!.id} title={accountCaption(a)} onClick={()=>setViewing(a.id)}>
                {a.name}{a.plan&&!a.name.includes(a.plan)?<small>{a.plan}</small>:null}</button>)}
            </div>
          </div>}
          {siblings.length>1&&<span className="dv-label dv-home-models-label">{t('Model')}</span>}
          {shown?.provider==='codex'&&!models?.length?<p className="dv-pk-note" role="status">{shownCatalog.error?t('Couldn’t load {name}’s models: {error}',{name:shown.name,error:shownCatalog.error}):t('Loading {name}’s models…',{name:shown.name})}</p>
            :<div className="dv-pk-list" role="listbox" aria-label={t('{name} models',{name:shown?.name??''})}>
            {rows.map(row=>{const current=onChosen&&row.id===view.model;
              return <div key={row.id} className="dv-pk-item" data-open={asking===row.id||undefined}>
                <button type="button" role="option" className="dv-pk-model" aria-selected={current} onClick={()=>void pickModel(row.id)}>
                  <b>{row.label}{row.tag&&<span className="dv-tag">{row.tag}</span>}</b><small>{row.line}</small>{current&&TICK}</button>
                {asking===row.id&&shown&&<div className="dv-pk-confirm" role="group" aria-label={fableCreditLabel(shown.profile.plan)}>
                  <p><b>{t('Use {model} for these jobs?',{model:row.label})}</b></p>
                  <p>{shown.profile.plan==='team'?TEAM_FABLE_NOTICE:t('Each message will cost money on top of your {plan} plan.',{plan:shown.plan||'Pro'})} {t('Dervo asks once for {name}.',{name:shown.name})}</p>
                  <div className="dv-pk-actions"><button type="button" className="dv-btn dv-btn-primary dv-btn-sm" onClick={()=>void pickModel(row.id,true)}>{t('Use {model}',{model:row.label})}</button>
                    <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setAsking(null)}>{t('Cancel')}</button></div>
                </div>}
              </div>;})}
          </div>}
          {error&&<p className="dv-pk-error" role="alert"><StatusGlyph state="failed"/>{error}</p>}
          {levels.length>0&&<div className="dv-pk-sec" role="group" aria-label={t('Thinking')}>
            <div className="dv-pk-row-head"><span className="dv-label">{t('Thinking')}</span><span className="dv-caption">{t('for {model} in this project',{model:modelLabel(provider!,view.model,catalog??undefined)})}</span></div>
            <ThinkingLevels value={view.effort??null} light={light} heavy={unfolded?heavy:[]} more={heavy.length>0&&!unfolded} disabled={false}
              describe={level=>effortDescription(view.model,level,catalog??undefined)}
              onPick={level=>onPick({profileId:account!.id,model:view.model,effort:level})} onMore={()=>setMore(true)}/>
            <p className="dv-pk-line">{thinkingLine(view.effort,defaultEffort(provider!,view.model,catalog??undefined),view.model,provider!)}</p>
          </div>}
        </div>
        <div className="dv-pk-foot">
          {/* Design review S4, behaviour review S1 and S2: what the choice really does, said once. */}
          <span>{t('New threads from this message open on this. Threads it adds to keep their own. Thinking you pick or say becomes this project’s level for that model.')}{homeModel?` ${t('Home answers with {model}.',{model:homeModel})}`:''}</span>
          <span className="dv-home-launch-actions">
            {view.from!=='default'&&<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>{onReset();setOpen(false);}}>{t('Project default')}</button>}
            {homeSetting&&<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setHomeOpen(true)}>{t('Home’s model')}</button>}
            {onSettings&&<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>{setOpen(false);onSettings();}}>{t('Settings')}</button>}
          </span>
        </div>
      </div>}
    </PopoverContent>
  </Popover>;
}
