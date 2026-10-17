import {useEffect,useRef,useState,type FormEvent,type ReactNode,type RefObject} from 'react';
import {useQueries,useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {Segmented} from './DervoControls';
import {connectionStatusQuery,type Login} from './ConnectionPreflight';
import {maskKey} from './AddAccount';
import {SignInToAdd,planWords} from './AccountSignIn';
import {t} from '@/i18n';
import './Onboarding.css';

/** Onboarding's middle step, "Connect your agents" (8 Oct 2026, onboarding thread). Before, the agent was a line on the
 * Start card, reached only after a folder was picked, so a tester without a ready Claude Code met the problem last, and
 * the whole-Mac look dead-ended with "choose one folder first to connect" (onboarding audit, issue 1). Now the Mac check
 * is the step: one row each for Claude Code, Codex and git, each saying what Dervo really found and the one thing to do
 * about it, in place. Continue waits for one agent that's ready, and nothing past this step can be held for want of one.
 * Every line is the host's own answer (first_run.py, connection_status); nothing is shown working that isn't. */

type Row={state:string;detail:string;install?:string;update?:string;version?:string|null;email?:string|null;plan?:string|null;method?:string;profile_id?:string|null};
export type MacStatus={account:Row;claude:Row;codex:Row;git:Row;projects:number;checked_at:number};
export type AgentProfile={id:string;provider:string;identity:string;category:string;method?:string;name?:string|null;plan?:string|null};
type Check={state:string;canSend:boolean;detail:string;canUseSignedIn?:boolean;signedInAs?:string};

/** A command to copy, inline. */
export function Command({text}:{text:string}){
  const [copied,setCopied]=useState(false);
  useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(false),2000);return ()=>clearTimeout(timer);},[copied]);
  return <span className="dv-fr-cmd"><code>{text}</code>
    <button type="button" className="dv-btn-icon" aria-label={copied?t('Copied'):t('Copy {text}',{text})} onClick={event=>{event.stopPropagation();void navigator.clipboard?.writeText(text).then(()=>setCopied(true)).catch(()=>{});}}>
      {copied?<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>
        :<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5V4a1.5 1.5 0 00-1.5-1.5H4A1.5 1.5 0 002.5 4v5A1.5 1.5 0 004 10.5h1.5"/></svg>}
    </button></span>;
}

/** The $50 set here is a number Dervo shows spend against. Nothing refuses a run when it's reached (security review, 19 Sep,
 * finding 2), so onboarding says what Connected accounts says: where the real hard stop lives. */
export const KEY_LIMIT_LINE:Record<'claude'|'codex',string>={
  claude:t('Dervo shows your spend against $50 a month but doesn’t stop runs at it. For a hard stop, set a spend limit in the Anthropic Console.'),
  codex:t('Codex doesn’t tell Dervo what a run costs, so Dervo can’t track spend or stop runs. For a hard stop, set a budget on the OpenAI platform.')};

export function KeyForm({onAdded,onCancel,autoFocus}:{onAdded:(id:string)=>Promise<void>;onCancel?:()=>void;autoFocus?:boolean}){
  const [provider,setProvider]=useState<'claude'|'codex'>('claude'),[key,setKey]=useState('');
  const field=useRef<HTMLInputElement>(null);
  // Opened from a link that's gone once it opens: the field takes focus, so the keyboard isn't left on the page.
  useEffect(()=>{if(autoFocus)field.current?.focus();},[]);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(event:FormEvent){
    event.preventDefault();
    if(key.trim().length<8||busy)return;
    setBusy(true);setError('');
    try{const added=await local<{id:string}>('profile_key_add',{provider,key,monthlyLimitUsd:50});setKey('');await onAdded(added.id);}
    catch(failure){setError(maskKey(failure instanceof Error?failure.message:failure,key));}
    finally{setBusy(false);}
  }
  const label=provider==='claude'?t('Anthropic API key'):t('OpenAI API key');
  return <form className="dv-fr-inline" onSubmit={event=>void submit(event)}>
    <Segmented label={t('Provider')} value={provider} disabled={busy} onChange={next=>{setProvider(next);setError('');}} options={[{value:'claude',label:'Anthropic'},{value:'codex',label:'OpenAI'}]}/>
    <label className="dv-field"><span>{label}</span>
      <input ref={field} className="dv-input dv-mono" type="password" aria-label={label} autoComplete="off" spellCheck={false} placeholder={provider==='claude'?'sk-ant-…':'sk-…'}
        value={key} disabled={busy} onChange={event=>{setKey(event.target.value);setError('');}}/>
      <small>{t('Checked once, then kept on this Mac and never shown again.')} {KEY_LIMIT_LINE[provider]}</small></label>
    {error&&<p className="dv-err" role="alert">{error}</p>}
    <div className="dv-fr-inline-actions">{onCancel&&<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={busy} onClick={onCancel}>{t('Cancel')}</button>}
      <button type="submit" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||key.trim().length<8}>{busy?t('Checking the key…'):t('Check and add key')}</button></div>
  </form>;
}

/** Whether each connected personal account is verified: the agents step and the Start card share one cache entry per
 * account, so a check here is the same answer the project starts on. */
/** Checks asked for and not answered yet, shared by every useVerified on screen (first run and its agents step), so one
 * account is checked once. */
const asking=new Set<string>();
export function useVerified(profiles:AgentProfile[]):Record<string,Check|undefined>{
  // Asked again only while an answer is coming. A settled answer (verified, or what's wrong) stays until something changes
  // it, and the action that does refetches (code review, 8 Oct: an old account polled the host every 1.5 s).
  const results=useQueries({queries:profiles.map(p=>({...connectionStatusQuery(p.id),refetchInterval:(q:{state:{data?:Check}})=>q.state.data?.state==='checking'?700:false as const}))});
  // An account nobody has checked since Dervo opened (an API key just added, every account after a relaunch) is checked:
  // its sign-in and plan, never a model call. Without this it read "unchecked" for good and Continue never opened
  // (code review, 8 Oct, the useWorkSide pattern). Checked again each time it falls back to unchecked.
  const seen=useRef(new Map<string,string|undefined>());
  const states=results.map(r=>(r.data as Check|undefined)?.state);
  useEffect(()=>{profiles.forEach((p,n)=>{const state=states[n],before=seen.current.get(p.id);seen.current.set(p.id,state);
    if(state==='unchecked'&&before!=='unchecked'&&!asking.has(p.id)){asking.add(p.id);
      void local('connection_check',{profileId:p.id}).then(()=>results[n]?.refetch()).catch(()=>{}).finally(()=>asking.delete(p.id));}});},
    [profiles.map(p=>p.id).join(),states.join()]);
  return Object.fromEntries(profiles.map((p,i)=>[p.id,results[i]?.data as Check|undefined]));
}

/** After a button that's replaced or removed by its own answer, focus goes to the fallback instead of the page (a11y
 * review, 8 Oct). Only when it was lost: a reader who moved on keeps their place. */
function keepFocus(fallback:{current:HTMLElement|null}|undefined){
  requestAnimationFrame(()=>{const at=document.activeElement;if(!at||at===document.body)fallback?.current?.focus();});
}

/** One row: the mark of what Dervo found, the agent, one line, and the one thing to do about it. */
function AgentRow({glyph,name,line,side,children,order,accent}:{glyph:GlyphState;name:ReactNode;line:ReactNode;side?:ReactNode;children?:ReactNode;order:number;accent?:boolean}){
  return <li className="dv-ag-row" data-state={glyph} style={{['--dv-ag-i' as string]:order}}>
    <span className="dv-ag-glyph"><StatusGlyph state={glyph} accent={accent}/></span>
    <div className="dv-ag-text"><p className="dv-ag-name">{name}</p><div className="dv-ag-line">{line}</div></div>
    <div className="dv-ag-side">{side}</div>
    {children&&<div className="dv-ag-body">{children}</div>}
  </li>;
}

/** A connected account: verified, being checked, or what's wrong with it and how through. Shown in full, the host's own
 * reason included, so an account added before beta.18 with an email that didn't match isn't a dead end (audit, issue 2). */
function AccountRow({profile,check,order,onChanged,fallback}:{profile:AgentProfile;check?:Check;order:number;onChanged:()=>Promise<unknown>;fallback?:{current:HTMLElement|null}}){
  const keyed=profile.method==='api_key';
  const login=useQuery({queryKey:['connection-login',profile.id],enabled:!keyed,queryFn:()=>local<Login>('connection_login_status',{profileId:profile.id}),
    refetchInterval:q=>q.state.data?.active?700:false});
  const [busy,setBusy]=useState(''),[error,setError]=useState('');
  const client=useQueryClient();
  async function act(key:string,operation:string){setBusy(key);setError('');
    try{await local(operation,{profileId:profile.id});await login.refetch();await onChanged();await client.invalidateQueries({queryKey:['connection-status',profile.id]});}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}finally{setBusy('');keepFocus(fallback);}}
  const tool=profile.provider==='codex'?'Codex':'Claude';
  const plan=planWords(profile.plan);
  const name=<>{tool} · {profile.name||(keyed?t('API key'):profile.identity)}</>;
  const who=keyed?t('{provider} API key, billed per use',{provider:profile.provider==='codex'?'OpenAI':'Anthropic'}):[profile.identity,plan].filter(Boolean).join(' · ');
  if(check?.state==='identity_verified')return <AgentRow order={order} glyph="done" name={keyed?name:tool} line={who}/>;
  const active=!!login.data?.active;
  const state=check?.state;
  const problem=state==='identity_mismatch'?(check!.detail||t('Signed in as a different account than this one. Nothing was sent.'))
    :state==='signed_out'?(keyed?check!.detail:t('Signed out. Sign in again to use it.')):state&&state!=='checking'?check!.detail:'';
  const adopt=!keyed&&state==='identity_mismatch'&&check?.canUseSignedIn&&check.signedInAs?check.signedInAs:null;
  return <AgentRow order={order} glyph={active||!check||state==='checking'?'running':'needs'} accent={!!problem&&!active} name={name}
    line={active?t('Waiting for you to finish in your browser'):problem||(keyed?t('Checking the key'):t('Checking this account'))}
    side={!keyed&&active?<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={!!busy} onClick={()=>void act('cancel','connection_login_cancel')}>{t('Cancel')}</button>:null}>
    {/* The account that did sign in is the likely fix, so it leads (design review, 8 Oct): one primary, the rest quiet. */}
    {problem&&!active&&<div className="dv-ag-acts">
      {adopt&&<button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy} onClick={()=>void act('adopt','account_use_signed_in')}>{t('Use {email} instead',{email:adopt})}</button>}
      {!keyed&&<button type="button" className={`dv-btn dv-btn-sm ${adopt?'dv-btn-quiet':'dv-btn-ghost'}`} disabled={!!busy} onClick={()=>void act('sign-in','connection_login')}>{t('Sign in again')}</button>}
      <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={!!busy} onClick={()=>void act('remove','account_remove')}>{busy==='remove'?t('Removing…'):t('Remove this account')}</button>
    </div>}
    {error&&<p className="dv-err" role="alert">{error}</p>}
  </AgentRow>;
}

type Props={status?:MacStatus;failed:boolean;checking:boolean;recheck:()=>void;profiles:AgentProfile[];
  onProfilesChanged:()=>Promise<unknown>;onAdded:(id:string)=>Promise<void>|void;onContinue:()=>void;
  /** "Claude Code · Opus 5.5", what new projects will start on, once one is ready. */
  startsOn?:string;headingRef?:RefObject<HTMLHeadingElement|null>};

export function AgentsStep({status,failed,checking,recheck,profiles,onProfilesChanged,onAdded,onContinue,startsOn,headingRef}:Props){
  const [signingIn,setSigningIn]=useState(false),[keyOpen,setKeyOpen]=useState(false);
  const ownHeading=useRef<HTMLHeadingElement>(null),title=headingRef??ownHeading;
  const signInBox=useRef<HTMLDivElement>(null),keyLink=useRef<HTMLButtonElement>(null);
  // The sign-in panel replaces the button that opened it: focus goes into the panel.
  useEffect(()=>{if(signingIn)signInBox.current?.focus();},[signingIn]);
  const personal=profiles.filter(p=>p.category==='personal'&&p.id!==status?.claude.profile_id);
  const checks=useVerified(personal);
  const claude=status?.claude,codex=status?.codex,git=status?.git;
  const claudeReady=claude?.state==='ready';
  const verified=personal.filter(p=>checks[p.id]?.state==='identity_verified');
  const ready=claudeReady||verified.length>0;
  const codexAccounts=personal.filter(p=>p.provider==='codex');
  async function added(id:string){setSigningIn(false);setKeyOpen(false);await onProfilesChanged();await onAdded(id);keepFocus(title);}

  // Claude Code: what the check found, and the one command that fixes it when it's a Terminal thing.
  let claudeRow:ReactNode;
  if(!claude||claude.state==='checking')claudeRow=<AgentRow order={0} glyph="running" name="Claude Code" line={claude?.detail||t('Looking for Claude Code on this Mac')}/>;
  else if(claudeReady){
    const how=claude.method==='api_key'?t('Your existing sign-in, with an Anthropic API key. Billed per use.')
      :[claude.email??t('Your existing sign-in'),planWords(claude.plan)].filter(Boolean).join(' · ');
    claudeRow=<AgentRow order={0} glyph="done" name={<>Claude Code{claude.version&&<span className="dv-ag-ver">{claude.version}</span>}</>} line={how}/>;
  }else{
    const command=claude.state==='missing'?claude.install:claude.state==='outdated'?claude.update:claude.state==='signed_out'?'claude':undefined;
    const line=claude.state==='missing'?t('Not on this Mac yet. Paste this in Terminal, then come back. Dervo notices on its own.')
      :claude.state==='outdated'?t('Version {version} is too old for Dervo. Update it in Terminal, then come back.',{version:claude.version??''})
      :claude.state==='signed_out'?t('Installed, but Dervo couldn’t find its sign-in. Run it in Terminal and sign in, then come back.')
      :claude.detail;
    claudeRow=<AgentRow order={0} glyph="idle" name="Claude Code" line={line}
      side={<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={checking} onClick={recheck}>{checking?t('Checking…'):t('Check again')}</button>}>
      {command&&<Command text={command}/>}
    </AgentRow>;
  }

  // Codex: built into Dervo, so signing in with ChatGPT is the way through that needs no Terminal.
  const codexBuiltIn=!!codex&&codex.state!=='missing';
  // One Codex row: an account that's here (even one that needs fixing) is that row, so a second sign-in isn't offered
  // beside it (design review, 8 Oct). While the sign-in panel is open its own steps say where it stands.
  const codexRow=codexBuiltIn&&codexAccounts.length===0&&<AgentRow order={1} glyph="idle" name="Codex"
    line={signingIn?t('Sign in with the ChatGPT account you want Dervo to use.'):t('Built into Dervo. Sign in in your browser with ChatGPT Plus, Pro Lite or Pro.')}
    side={!signingIn&&<button type="button" className={`dv-btn dv-btn-sm ${claudeReady?'dv-btn-ghost':'dv-btn-primary'}`} onClick={()=>{setKeyOpen(false);setSigningIn(true);}}>{t('Sign in with ChatGPT')}</button>}>
    {signingIn&&<div className="dv-ag-signin" ref={signInBox} tabIndex={-1} aria-label={t('Sign in with ChatGPT')} role="group"><SignInToAdd provider="codex" category="personal" onCancel={()=>{setSigningIn(false);keepFocus(title);}} onAdded={done=>added(done.id)}/></div>}
  </AgentRow>;

  const gitRow=git&&git.state!=='checking'&&<AgentRow order={2+personal.length} glyph={git.state==='ready'?'done':'idle'} name="git"
    line={git.state==='ready'?t('Version {version}. Every change an agent makes can be undone.',{version:git.version??''})
      :t('Not installed, so projects start reading only. Install it with Xcode’s command line tools, then come back.')}>
    {git.state!=='ready'&&<Command text="xcode-select --install"/>}
  </AgentRow>;

  const caption=!status&&!failed?t('Checking this Mac for Claude Code, Codex and git')
    :ready?(startsOn?t('New projects start on {agent}. You can change it for each project.',{agent:startsOn}):t('Ready.'))
    :t('Connect one to continue. Codex needs no Terminal.');
  return <section className="dv-ag" aria-labelledby="dv-ag-title">
    <h1 className="dv-display-1" id="dv-ag-title" ref={title} tabIndex={-1}>{ready?t('Your agents are ready.'):t('Connect your agents.')}</h1>
    <p className="dv-lede">{t('Dervo runs your own Claude Code and Codex, on your own plans. It never sees your password, and checks which account signed in before anything is sent.')}</p>
    <ul className="dv-ag-list" aria-busy={!status}>
      {claudeRow}
      {codexAccounts.map((p,i)=><AccountRow key={p.id} order={1+i} profile={p} check={checks[p.id]} onChanged={onProfilesChanged} fallback={title}/>)}
      {codexRow}
      {personal.filter(p=>p.provider!=='codex').map((p,i)=><AccountRow key={p.id} order={2+codexAccounts.length+i} profile={p} check={checks[p.id]} onChanged={onProfilesChanged} fallback={title}/>)}
      {gitRow}
      {/* Before the first check answers, every row is there and looking, so nothing jumps in under the reader. */}
      {!status&&!failed&&<><AgentRow order={1} glyph="running" name="Codex" line={t('Getting Codex ready')}/><AgentRow order={2} glyph="running" name="git" line={t('Looking for git')}/></>}
    </ul>
    {failed&&!status&&<p className="dv-err" role="alert">{t('Dervo couldn’t check this Mac.')} <button type="button" className="dv-link" onClick={recheck}>{t('Try again')}</button></p>}
    {keyOpen?<div className="dv-ag-key"><KeyForm autoFocus onAdded={added} onCancel={()=>{setKeyOpen(false);requestAnimationFrame(()=>keyLink.current?.focus());}}/></div>
      // No agent yet, and maybe no Claude or ChatGPT plan at all: the step says what each way needs, and the one that
      // needs no plan is a button, so nobody is left on a disabled Continue (merge review of #23).
      :status&&!ready&&!signingIn?<div className="dv-ag-none" role="note">
        <p><b>{t('No Claude or ChatGPT plan?')}</b> {t('Claude Code runs on Claude Pro or Max, and Codex on ChatGPT Plus, Pro Lite or Pro. Without one, an API key works: make one at console.anthropic.com or platform.openai.com, and pay the provider per use.')}</p>
        <button ref={keyLink} type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>{setSigningIn(false);setKeyOpen(true);}}>{t('Add an API key')}</button></div>
      :<p className="dv-ag-alt"><button ref={keyLink} type="button" className="dv-link" onClick={()=>{setSigningIn(false);setKeyOpen(true);}}>{t('Use an API key instead')}</button>
        <span className="dv-caption">{t('Anthropic or OpenAI. Billed per use by the provider, not your plan.')}</span></p>}
    <div className="dv-ob-next">
      <span className="dv-caption" role="status" id="dv-ag-next-why">{caption}</span>
      <button type="button" className="dv-btn dv-btn-primary" disabled={!ready} aria-describedby="dv-ag-next-why" onClick={onContinue}>{t('Continue')}
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8h9M8.5 4l4 4-4 4"/></svg></button>
    </div>
    {!claudeReady&&ready&&verified.every(p=>p.provider==='codex')&&<p className="dv-caption dv-ag-note">{t('Quick asks from Home need Claude for now. Your Codex projects work fully.')}</p>}
  </section>;
}

/** The three steps at the top of onboarding: where you are, what's done, and the way back to a done step. */
export type Phase='sign-in'|'agents'|'projects';
export function Stepper({phase,done,onGo}:{phase:Phase;done:Phase[];onGo:(phase:Phase)=>void}){
  const steps:{id:Phase;label:string}[]=[{id:'sign-in',label:t('Sign in')},{id:'agents',label:t('Your agents')},{id:'projects',label:t('Your projects')}];
  return <ol className="dv-ob-stepper" aria-label={t('Setup steps')}>
    {steps.map((step,i)=>{const now=step.id===phase,finished=done.includes(step.id)&&!now;
      // Going back is offered for Your agents only: a finished sign-in has nothing to redo here (Settings has the account).
      const back=finished&&step.id==='agents';
      // The check mark is drawn, so where each step stands is said in words too.
      const said=<span className="dv-ob-sr">{finished?t(', done'):now?t(', current step'):''}</span>;
      const inner=<><span className="dv-ob-track" aria-hidden="true"/><span className="dv-ob-label"><span className="dv-ob-n">{finished?<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3 4.7-5"/></svg>:i+1}</span>{step.label}{said}</span></>;
      return <li key={step.id} data-now={now||undefined} data-done={finished||undefined} aria-current={now?'step':undefined}>
        {back?<button type="button" className="dv-ob-step" onClick={()=>onGo(step.id)} title={t('Back to {step}',{step:step.label})}>{inner}</button>:<span className="dv-ob-step">{inner}</span>}
      </li>;})}
  </ol>;
}

/** Whether the Mac is offline, kept current, so a failed account call can say so and try again on its own. */
export function useOnline(onBack?:()=>void):boolean{
  const [online,setOnline]=useState(()=>typeof navigator==='undefined'||navigator.onLine!==false);
  // The newest callback, read when the connection comes back, so the listeners are added once, not on every render.
  const back=useRef(onBack);back.current=onBack;
  useEffect(()=>{
    const up=()=>{setOnline(true);back.current?.();},down=()=>setOnline(false);
    window.addEventListener('online',up);window.addEventListener('offline',down);
    return ()=>{window.removeEventListener('online',up);window.removeEventListener('offline',down);};
  },[]);
  return online;
}
export const OFFLINE_LINE=()=>t('This Mac looks offline. Dervo carries on by itself when you’re back.');
