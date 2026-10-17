import {useEffect,useRef,useState,type FormEvent,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {invoke} from '@tauri-apps/api/core';
import {local,type DervoAccountStatus,type DervoPlan} from './api';
import {accountRefreshInterval,betaAccess} from './DervoAccount';
import {BrandWindow,MomentMark} from './BrandMoment';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {ChoiceRows,Select,Tag,type Choice} from './DervoControls';
import {connectionStatusQuery,type Login} from './ConnectionPreflight';
import {CLAUDE_FALLBACKS,CLAUDE_MODEL_FLOORS,CLAUDE_MODELS,claudeDefaultModel,claudeVersionAtLeast} from './models';
import {modelName} from './providerLabel';
import {MacOnboarding,type OnboardingAgent} from './MacOnboarding';
import {useWorkSide} from './WorkAccount';
import {SignInToAdd} from './AccountSignIn';
import {WorkTag} from './WorkMark';
import {MAC_ASKS,onboardingSources,sourceLine} from './onboarding';
import {rememberHistoryDeclined} from './historyImport';
import {AgentsStep,Command,KeyForm,OFFLINE_LINE,Stepper,useOnline,useVerified,type Phase} from './OnboardingAgents';
import {PRICES,planSummary} from './DervoPlan';
import './FirstRun.css';
import {formatList,t,tr} from '@/i18n';
import {betaCorner} from '@/local/platformWords';

/** First run (design sweep, brand/journey.html): sign in, pick a folder, Start. Bryce, 18 Sep: "an elegant onboarding
 * process... every single thought put into it", and on 19 Sep he picked a full window with the meadow and no sidebar,
 * with sign-in as step 1. The Mac check runs behind sign-in and speaks up only where it matters. The one decision is the
 * folder; the agent and what it can do arrive already chosen, one line each with Change. A tester whose beta access is
 * pending can finish setup: the project waits and its overview starts on approval. Adding a later project uses the same
 * cards inside the app, without the sign-in step. */

type Row={state:string;detail:string;install?:string;update?:string;login?:string;version?:string|null;email?:string|null;plan?:string|null;method?:string;profile_id?:string|null;profiles?:{id:string;identity:string;method?:string}[]};
export type FirstRunStatus={account:Row;claude:Row;codex:Row;git:Row;projects:number;checked_at:number};
/** The host answers within the bridge's 10 s, so a slow first launch of Claude Code or git comes back as 'checking'
 * (first_run.PROBE_WAIT, systems report problem 7). Ask again each second until the probe has an answer. */
export const stillChecking=(status?:FirstRunStatus)=>status?.claude.state==='checking'||status?.git.state==='checking';
export const firstRunStatusQuery={queryKey:['first-run-status'],queryFn:()=>local<FirstRunStatus>('first_run_status'),
  refetchInterval:(query:{state:{data?:FirstRunStatus}})=>stillChecking(query.state.data)?1000:false as const};
type Facts={path:string;name:string;git:boolean;repo:string|null;branch:string|null;uncommitted:number;stack:string[];
  /** Setup's access choice (Bryce, 9 Oct): the full-access warning per provider, the token finish needs to turn it on,
   * and the checks the folder suggests, which Dervo runs after each change. */
  full_access?:{warning:string;detail:{claude:string;codex:string};checks:string[]}};
type Access='full'|'edit'|'read';
type Profile={id:string;provider:string;identity:string;category:string;method?:string;name?:string|null;plan?:string|null};
export type Created={id:string;channelId:string;name:string;access:Access;checks?:string[];checks_detail?:string;overview:{channelId:string;state?:string;detail?:string;waiting_for?:'beta'|'plan'}|null;access_detail?:string;overview_detail?:string};
/** What the Start card runs with: your existing Claude Code, or one of your connected accounts. */
type Agent={kind:'system'}|{kind:'profile';profileId:string};
type Props={mode:'first'|'add';profiles:Profile[];onProfilesChanged:()=>Promise<unknown>;onDone:(created:Created,agent:string)=>Promise<unknown>|void;onCancel?:()=>void;
  /** The whole-Mac onboarding brought projects in (group 10): open this one. Without it, the Ask card isn't offered. */
  onBroughtIn?:(projectId:string)=>Promise<unknown>|void};
/** How first run's project step goes (group 10, onboarding-v2.md): ask first, then look at this Mac, choose one folder
 * (today's path), or start empty, which is one folder with no history offer. */
type MacFlow='ask'|'look'|'folder'|'empty';

/** The Claude model before the Mac check reports Claude Code's version. Opus 5 runs on every version Dervo supports. */
export const DEFAULT_MODELS:Record<string,string>={claude:'claude-opus-5'};
/** Claude models first run offers. Fable is left to the model picker, which says once when it's billed to paid credits. */
const FIRST_MODELS=CLAUDE_MODELS.filter(model=>!/fable/.test(model.id));
/** First run offers the models this Mac's Claude Code can run: Opus 5.5 from 2.1.280 and Sonnet 5.5 from 2.1.284,
 * otherwise Opus 5 or Sonnet 5 in their place. Unlike the picker, an unread version gets the older ones. */
export function firstModelsFor(version?:string|null):{id:string;label:string}[]{
  return FIRST_MODELS.map(m=>CLAUDE_MODEL_FLOORS[m.id]&&!claudeVersionAtLeast(version,CLAUDE_MODEL_FLOORS[m.id])?CLAUDE_FALLBACKS[m.id]:m);
}
// i18n-ignore: the providers' own plan names
const PLAN_NAMES:Record<string,string>={pro:'Pro',max:'Max',team:'Team',plus:'Plus'};
const planName=(plan?:string|null)=>plan?PLAN_NAMES[plan]??plan.charAt(0).toUpperCase()+plan.slice(1):'';
const tildePath=(path:string)=>path.replace(/^\/Users\/[^/]+(?=\/|$)/,'~');

/** Whether a checked row is done, waits on you, or is only information. */
export function rowTone(row?:Row):'done'|'action'|'note'{
  if(!row)return 'note';
  if(row.state==='ready')return 'done';
  if(['missing','signed_out','sign_in','unsupported','outdated'].includes(row.state))return 'action';
  return 'note';
}

/** The agent a new project starts with: your existing Claude Code when it works, otherwise a connected account. */
export function defaultAgent(status:FirstRunStatus|undefined,profiles:Profile[],verified?:Set<string>):Agent|undefined{
  if(status?.claude.state==='ready')return {kind:'system'};
  const mine=profiles.filter(p=>p.category==='personal'&&p.id!==status?.claude.profile_id);
  // One that's verified first: an old account whose sign-in no longer matches shouldn't be picked over one that works.
  const saved=mine.find(p=>verified?.has(p.id))??mine[0];
  return saved?{kind:'profile',profileId:saved.id}:undefined;
}

/** The one line about this Mac, under the steps: what's ready, or what isn't, in one sentence. */
export function macLine(status:FirstRunStatus|undefined,failed:boolean):{state:GlyphState;text:string;recheck:boolean}{
  if(failed&&!status)return {state:'failed',text:t('Dervo couldn’t check this Mac.'),recheck:true};
  if(!status)return {state:'running',text:t('Checking this Mac for Claude Code, Codex and git'),recheck:false};
  const {claude,codex,git}=status;
  if(stillChecking(status))return {state:'running',text:claude.state==='checking'?claude.detail:git.detail,recheck:false};
  const claudeReady=claude.state==='ready';
  const how=claude.method==='api_key'?t('signed in with an API key'):claude.plan?t('signed in on {plan}',{plan:planName(claude.plan)}):t('signed in');
  // i18n-ignore: a name and a version, then the translated words
  const claudeText=`Claude Code${claude.version?` ${claude.version}`:''}, ${how}`;
  const gitText=git.state==='ready'?`git${git.version?` ${git.version}`:''}`:'';
  const noGit=t('git isn’t installed, so projects start reading only');
  if(claudeReady)return git.state==='ready'?{state:'done',text:t('This Mac is ready: {claudeText} · {gitText}',{claudeText,gitText}),recheck:false}
    :{state:'idle',text:t('This Mac: {claudeText} · {noGit}',{claudeText,noGit}),recheck:true};
  if(codex.state!=='missing')return git.state==='ready'?{state:'done',text:t('This Mac: {gitText} is ready and Codex is built in',{gitText}),recheck:true}
    :{state:'idle',text:t('This Mac: Codex is built in · {noGit}',{noGit}),recheck:true};
  return {state:'needs',text:t('This Mac: {detail}',{detail:claude.detail}),recheck:true};
}

/** Under every Codex choice (UX review, 23 Sep, H1): the agent choice is also the project's account, and Home runs on
 * Claude Code, so a Codex project gets its threads but not Home. Said before it's chosen, not after the first message. */
export const CODEX_HOME_NOTE=t('Home, the assistant that files and starts threads, needs Claude for now.');
const codexNote=<span className="dv-fr-home-note">{CODEX_HOME_NOTE}</span>;

/** Why there's no agent yet, in one line, with the way through. */
function missingLine(claude:Row,codexBuiltIn:boolean):string{
  const codex=codexBuiltIn?` ${t('Codex is built into Dervo.')}`:'';
  if(claude.state==='missing')return `${t('Claude Code isn’t installed on this Mac.')}${codex}`;
  // What Dervo's check found, not a fact about the Mac: the check has been wrong before (21 Sep, USER missing).
  if(claude.state==='signed_out')return `${t('Dervo couldn’t find your Claude Code sign-in.')}${codex}`;
  if(claude.state==='outdated')return `${t('Claude Code {version} needs an update.',{version:claude.version??''})}${codex}`.replace('  ',' ');
  return `${claude.detail}${codex}`;
}

/** Why onboarding's Bring in is held with no agent. Not "Codex is built into Dervo" here: next to "waits until an agent
 * is connected" it read as a contradiction (design review S6, 25 Sep). */
export function onboardingHeldLine(claude:Row):string{
  return `${missingLine(claude,false)} ${t('Bring in waits until an agent is connected. Choose one folder first to connect Claude Code or Codex, then bring the rest in.')}`;
}

const CHECK=<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3 4.7-5"/></svg>;
function DoneStep({title,line,action}:{title:string;line:ReactNode;action?:ReactNode}){
  return <div className="dv-fr-step"><span className="dv-fr-num dv-done">{CHECK}</span><div className="dv-fr-step-text"><p className="dv-fr-t">{title}</p><p className="dv-fr-l">{line}</p></div>{action??<span/>}</div>;
}
function LaterStep({n,title}:{n:number;title:string}){
  return <div className="dv-fr-step dv-later"><span className="dv-fr-num">{n}</span><div className="dv-fr-step-text"><p className="dv-fr-t">{title}</p></div><span/></div>;
}
function StepCard({n,title,children,headingRef,plain}:{n:number;title:string;children:ReactNode;headingRef?:React.Ref<HTMLHeadingElement>;
  /** Onboarding's stepper numbers the steps, so the card doesn't. */plain?:boolean}){
  const id=`dv-fr-step-${n}`;
  return <div className="dv-open-card dv-fr-card" role="group" aria-labelledby={id}>
    <div className="dv-fr-card-head">{!plain&&<span className="dv-fr-num dv-fr-now">{n}</span>}<h2 className="dv-title" id={id} ref={headingRef} tabIndex={headingRef?-1:undefined}>{title}</h2></div>
    {children}
  </div>;
}

/** Step 1: sign in with the emailed code, right here. The same host operations as Account in Settings. */
type SignIn={kind:'none'}|{kind:'checking'}|{kind:'email'}|{kind:'code'}|{kind:'done';pending:boolean}|{kind:'blocked';line:string}|{kind:'problem';line:string};
export function signInState(data:DervoAccountStatus|undefined,failed:boolean,now=Date.now()):SignIn{
  if(!data)return failed?{kind:'problem',line:t('Dervo couldn’t check your account.')}:{kind:'checking'};
  if(data.method!=='email_code')return {kind:'none'};
  if(data.state==='signed_in'&&data.identity){
    const beta=betaAccess(data,now);
    if(data.membership?.status==='revoked')return {kind:'blocked',line:t('This account no longer has access to the private beta.')};
    if(data.plan?.plan==='removed')return {kind:'blocked',line:t('This account no longer has access to Dervo. Your saved work is still here.')};
    if(beta.state==='stopped')return {kind:'blocked',line:t('Your beta access period has ended.')};
    return {kind:'done',pending:data.membership?.status==='pending'};
  }
  if(data.state==='awaiting_user'&&data.can_verify)return {kind:'code'};
  if(data.can_start)return {kind:'email'};
  // A code being checked is the code step. A saved session being restored on launch also polls, but has nothing to cancel:
  // that's checking, not "Enter the code" (code review, 8 Oct).
  if(data.can_poll)return (data.state==='awaiting_user'||data.state==='verifying')&&data.can_cancel?{kind:'code'}:{kind:'checking'};
  return {kind:'problem',line:data.detail||t('Your Dervo session needs checking.')};
}

/** How long the auth service makes you wait between codes (Supabase's default), so Send a new code says when it's ready
 * instead of failing inside the wait. */
export const RESEND_SECONDS=60;
/** Where a tester writes when sign-in can't help them (the private beta's support address). */
export const SUPPORT_EMAIL='bryce@trydervo.com';
const wait=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms));

function SignInCard({n,account,refetch,sentTo,setSentTo,plain}:{n:number;account:DervoAccountStatus|undefined;refetch:()=>Promise<unknown>;sentTo:string;setSentTo:(email:string)=>void;plain?:boolean}){
  const state=signInState(account,false);
  const [email,setEmail]=useState(sentTo),[code,setCode]=useState(''),[wrong,setWrong]=useState(false);
  const [busy,setBusy]=useState(''),[error,setError]=useState('');
  // When the newest code went out, for Send a new code's wait. A code sent before a restart counts as sent just now.
  const [sentAt,setSentAt]=useState(()=>account?.state==='awaiting_user'?Date.now():0),[now,setNow]=useState(Date.now()),[resent,setResent]=useState(false);
  const field=useRef<HTMLInputElement>(null),codeField=useRef<HTMLInputElement>(null);
  const online=useOnline(()=>{
    // Back online after a restore that couldn't reach the account service: try the saved session again, as promised.
    if(account?.can_restore)void local('dervo_account_restore',{}).catch(()=>{}).then(()=>refetch());else void refetch();});
  // While a new code is on its way the card stays the code card, though the host passes through "cancelled" to get there.
  const kind=busy==='resend'?'code':state.kind;
  useEffect(()=>{requestAnimationFrame(()=>(kind==='code'?codeField:field).current?.focus());},[kind]);
  // Ticks only while Send a new code is counting down (merge review of #23: it ran every second for as long as the card was up).
  const counting=kind==='code'&&sentAt+RESEND_SECONDS*1000>now;
  useEffect(()=>{if(!counting)return;const timer=setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(timer);},[counting]);
  async function run(key:string,operation:string,args:Record<string,unknown>={}){
    if(busy)return null;
    setBusy(key);setError('');
    try{await local(operation,args);return await refetch();}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));await refetch();return null;}
    finally{setBusy('');}
  }
  function send(event:FormEvent){
    event.preventDefault();
    const address=email.trim();
    if(!address||busy)return;
    setSentTo(address);setWrong(false);setResent(false);
    void run('send','dervo_account_send_code',{email:address}).then(after=>{if(after)setSentAt(Date.now());});
  }
  /** A new code to the same address, in place (onboarding audit, issue 4: the only way to resend was "Use a different
   * email" and typing the same one again, and a quick one landed on an error card). The host has no resend, so this is
   * its cancel, then a send once it's ready for one. */
  async function resend(){
    if(busy||!sentTo)return;
    setBusy('resend');setError('');setWrong(false);setCode('');
    try{
      if(account?.can_cancel&&account.operation_id)await local('dervo_account_cancel',{operationId:account.operation_id});
      for(let i=0;i<40;i++){const next=await refetch() as {data?:DervoAccountStatus};if(next?.data?.can_start)break;await wait(250);}
      await local('dervo_account_send_code',{email:sentTo});
      setSentAt(Date.now());setResent(true);
    }catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{await refetch();setBusy('');}
  }
  // The host checks a code in the background and answers "verifying" first. A code it then turns down comes back as the code
  // step again, so the wrong-code line follows that change, not the first answer (code review, 8 Oct: it failed silently).
  const submitted=useRef(false);
  async function verify(value:string){
    if(!account?.operation_id||!/^[0-9]{6,10}$/.test(value)||busy||account.state==='verifying')return;
    setCode('');setWrong(false);submitted.current=true;
    await run('verify','dervo_account_verify_code',{operationId:account.operation_id,code:value});
  }
  useEffect(()=>{
    if(!submitted.current||account?.state==='verifying'||busy==='verify')return;
    submitted.current=false;
    if(account?.state==='awaiting_user'&&account.can_verify){setWrong(true);requestAnimationFrame(()=>codeField.current?.focus());}
  },[account?.state,busy]);
  function differentEmail(){
    if(account?.can_cancel&&account.operation_id){setCode('');setEmail(sentTo);void run('cancel','dervo_account_cancel',{operationId:account.operation_id});}
    else if(account?.can_sign_out)void run('sign-out','dervo_account_sign_out');
  }
  const offline=!online&&<p className="dv-fr-offline" role="status">{OFFLINE_LINE()}</p>;
  if(kind==='code'){
    const left=Math.min(RESEND_SECONDS,Math.max(0,Math.ceil((sentAt+RESEND_SECONDS*1000-now)/1000)));
    return <StepCard n={n} plain={plain} title={t('Enter the code we emailed you')}>
    <p className="dv-card-text">{sentTo?tr('We sent it to {email}. It’s only used to confirm this sign-in.',{email:<b>{sentTo}</b>}):t('We sent it to your email. It’s only used to confirm this sign-in.')}</p>
    <form className="dv-fr-code" onSubmit={event=>{event.preventDefault();void verify(code);}}>
      <input ref={codeField} className="dv-input dv-code" aria-label={t('Email code')} type="text" inputMode="numeric" autoComplete="one-time-code" maxLength={10}
        value={code} disabled={!!busy} aria-invalid={wrong||undefined} aria-describedby={wrong?'dv-fr-code-error':undefined}
        onChange={event=>{const value=event.target.value.replace(/\D/g,'');setCode(value);setWrong(false);}}/>
      <button type="submit" className="dv-btn dv-btn-primary" disabled={!!busy||account?.state==='verifying'||!/^[0-9]{6,10}$/.test(code)} title={t('Codes are 6 to 10 digits')}>{busy==='verify'||account?.state==='verifying'?t('Checking code…'):t('Confirm code')}</button>
    </form>
    {wrong&&<p className="dv-err" id="dv-fr-code-error" role="alert">{t('That code couldn’t be confirmed. Check it against the newest email and try again.')}</p>}
    {error&&<p className="dv-err" role="alert">{error}</p>}
    {resent&&!busy&&!error&&<p className="dv-caption" role="status">{t('A new code is on its way. Only the newest one works.')}</p>}
    {offline}
    <p className="dv-caption dv-fr-mailhelp">{t('It can take a minute to arrive. Not there? Check spam and other inbox tabs, then send a new one.')}</p>
    <div className="dv-card-actions">
      <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={differentEmail}>{t('Use a different email')}</button>
      <button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy||left>0||!sentTo} onClick={()=>void resend()}>
        {busy==='resend'?t('Sending…'):left>0?t('Send a new code in {seconds}s',{seconds:left}):t('Send a new code')}</button></div>
  </StepCard>;
  }
  if(kind==='email'){
    // A send that didn't go out comes back as "failed" with the reason (supabase_account SEND_FAILED): it's shown here,
    // under the field, and the same button tries again.
    const sendFailed=account?.state==='failed'&&account.detail?account.detail:'';
    return <StepCard n={n} plain={plain} title={t('Sign in to Dervo')}>
    <p className="dv-card-text">{t('Use the email your beta invite went to. New here? The same step creates your account.')}</p>
    <form className="dv-fr-form" onSubmit={send}>
      <label className="dv-field"><span>{t('Email')}</span>
        <input ref={field} className="dv-input" type="email" aria-label={t('Email')} autoComplete="email" maxLength={254} placeholder="you@example.com" value={email} disabled={!!busy}
          aria-invalid={error||sendFailed?true:undefined} onChange={event=>{setEmail(event.target.value);setError('');}}/></label>
      {(error||sendFailed)&&<p className="dv-err" role="alert">{error||sendFailed}</p>}
      {offline}
      <div className="dv-card-actions"><span className="dv-caption">{t('We’ll email you a code.')}</span>
        <button type="submit" className="dv-btn dv-btn-primary" disabled={!!busy||!email.trim()}>{busy==='send'?t('Sending code…'):t('Email me a code')}</button></div>
    </form>
  </StepCard>;
  }
  if(kind==='blocked'||kind==='problem')return <StepCard n={n} plain={plain} title={t('Sign in to Dervo')}>
    {!online&&kind==='problem'?<p className="dv-card-text" role="status">{OFFLINE_LINE()}</p>
      :<p className="dv-card-text" role={kind==='problem'?'alert':undefined}>{'line' in state?state.line:''}</p>}
    {kind==='blocked'&&<p className="dv-caption">{t('Think that’s a mistake? Email {support} from the address you signed in with.',{support:SUPPORT_EMAIL})}</p>}
    {error&&<p className="dv-err" role="alert">{error}</p>}
    <div className="dv-card-actions">
      {kind==='problem'&&account?.can_restore&&<button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>void run('restore','dervo_account_restore')}>{busy==='restore'?t('Checking…'):t('Check session')}</button>}
      {kind==='problem'&&!account&&<button type="button" className="dv-btn dv-btn-ghost" onClick={()=>void refetch()}>{t('Try again')}</button>}
      {account?.can_sign_out&&<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={differentEmail}>{t('Use a different email')}</button>}
    </div>
  </StepCard>;
  return <StepCard n={n} plain={plain} title={t('Sign in to Dervo')}><p className="dv-card-text" role="status">{t('Checking your account…')}</p></StepCard>;
}

/** What a signed-in account still needs before agent work can start (paid plans, 8 Oct 2026): nothing, a free trial to
 * start here (no card), a trial that starts at checkout, or a plan after a trial or subscription ended. */
export type PlanNeed={kind:'trial-app'|'trial-card'|'ended'|'canceled';days:number;sandbox:boolean};
export function planNeed(data:DervoAccountStatus|undefined):PlanNeed|null{
  const plan:DervoPlan|null|undefined=data?.state==='signed_in'?data.plan:null;
  if(!plan||plan.plan!=='none')return null;
  const base={days:plan.trial.days,sandbox:plan.environment==='sandbox'};
  if(plan.trial.available)return {...base,kind:plan.trial.mode==='card'?'trial-card':'trial-app'};
  return {...base,kind:plan.subscription?.status==='canceled'?'canceled':'ended'};
}
const LATER_KEY='dervo-onboarding-plan-later';
const PHASE_KEY='dervo-onboarding-step';
export function storedPhase():Phase|null{try{const value=localStorage.getItem(PHASE_KEY);return value==='agents'||value==='projects'?value:null;}catch{return null;}}
/** Onboarding's remembered step and plan choice end with it: a first project made, or another email signed in (code review). */
export function forgetOnboarding(){try{localStorage.removeItem(PHASE_KEY);localStorage.removeItem(LATER_KEY);}catch{/* Nothing kept, then. */}}
function storePhase(phase:Phase){try{localStorage.setItem(PHASE_KEY,phase);}catch{/* The step is asked again next time, at worst. */}}
function rememberLater(){try{localStorage.setItem(LATER_KEY,'1');}catch{/* Offered again next time, at worst. */}}
/** Whether an agent is ready: your existing Claude Code, or a connected personal account that's verified. */
export function agentReadyOf(status:FirstRunStatus|undefined,verified:Set<string>):boolean{return status?.claude.state==='ready'||verified.size>0;}
const laterChosen=()=>{try{return localStorage.getItem(LATER_KEY)==='1';}catch{return false;}};

/** The plan card, right after sign-in: start the trial in one click, or open checkout, or set up first and decide later.
 * Setting up never waits on it: a project made without a plan keeps its overview waiting, and starts it once there is one. */
function PlanCard({need,account,refetch,onLater}:{need:PlanNeed;account:DervoAccountStatus;refetch:()=>Promise<unknown>;onLater:()=>void}){
  const [busy,setBusy]=useState(''),[error,setError]=useState('');
  const billing=account.billing;
  const online=useOnline(()=>void refetch());
  async function act(key:string,operation:string,args:Record<string,unknown>={}){
    if(busy)return;setBusy(key);setError('');
    try{await local(operation,args);await refetch();}catch(failure){setError(failure instanceof Error?failure.message:String(failure));}finally{setBusy('');}
  }
  const starting=busy==='trial'||billing?.state==='starting';
  const opened=billing?.state==='opened';
  // Monthly and Yearly give way to Check now: focus follows, so the keyboard isn't dropped on the page (a11y review).
  const checkNow=useRef<HTMLButtonElement>(null);
  useEffect(()=>{if(opened)checkNow.current?.focus();},[opened]);
  // The words are Account settings' own (DervoPlan.planSummary), so the plan reads the same here and there (merge review).
  const title=need.kind==='trial-app'||need.kind==='trial-card'?t('Start your free trial'):need.kind==='canceled'?t('Your Dervo Pro plan has ended'):t('Your free trial has ended');
  const line=account.plan?planSummary(account.plan).line:'';
  return <StepCard n={0} plain title={title}>
    <p className="dv-card-text">{line}</p>
    {opened&&<p className="dv-fr-note-inline dv-caption" role="status">{t('Checkout opened in your browser. Finish there, and Dervo picks it up within a minute.')}</p>}
    {(error||billing?.state==='error'&&billing.detail)&&<p className="dv-err" role="alert">{error||billing!.detail}</p>}
    {!online&&<p className="dv-fr-offline" role="status">{OFFLINE_LINE()}</p>}
    <div className="dv-card-actions">
      <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={onLater}>{t('Set up first, decide later')}</button>
      {need.kind==='trial-app'?<button type="button" className="dv-btn dv-btn-primary" disabled={!!busy||starting||!account.can_start_trial} onClick={()=>void act('trial','dervo_plan_start_trial')}>
          {starting?t('Starting…'):t('Start free trial')}</button>
        :opened?<button ref={checkNow} type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>void act('refresh','dervo_plan_refresh')}>{busy==='refresh'?t('Checking…'):t('Check now')}</button>
        :<><button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy||!account.can_buy} onClick={()=>void act('month','dervo_billing_open',{action:'checkout',interval:'month'})}>{busy==='month'?t('Opening…'):t('Monthly, {price}',{price:PRICES.month})}</button>
          <button type="button" className="dv-btn dv-btn-primary" disabled={!!busy||!account.can_buy} onClick={()=>void act('year','dervo_billing_open',{action:'checkout',interval:'year'})}>{busy==='year'?t('Opening…'):t('Yearly, {price}',{price:PRICES.year})}</button></>}
    </div>
    {need.sandbox&&<p className="dv-caption dv-fr-fine">{t('Test mode: no real charges')}</p>}
  </StepCard>;
}

/** Connecting Codex from the Start card (beta.18): ChatGPT's own sign-in starts at once, and Dervo shows who signed in
 * before adding it (AccountSignIn.tsx). No email to type first. */
function CodexSignIn({onAdded,onCancel}:{onAdded:(id:string)=>Promise<void>;onCancel:()=>void}){
  return <div className="dv-fr-inline"><SignInToAdd provider="codex" category="personal" onCancel={onCancel} onAdded={added=>onAdded(added.id)}/></div>;
}

export {KEY_LIMIT_LINE} from './OnboardingAgents';

/** A connected account that isn't verified yet: its sign-in in progress, or what to do about it. */
function Verifying({profile}:{profile:Profile}){
  const keyed=profile.method==='api_key';
  const status=useQuery({...connectionStatusQuery(profile.id),refetchInterval:q=>q.state.data?.state==='checking'?700:false});
  const login=useQuery({queryKey:['connection-login',profile.id],enabled:!keyed,queryFn:()=>local<Login>('connection_login_status',{profileId:profile.id}),
    refetchInterval:q=>q.state.data?.active?700:false});
  const [busy,setBusy]=useState(false);
  async function act(operation:string){setBusy(true);try{await local(operation,{profileId:profile.id});await login.refetch();await status.refetch();}catch{/* The line below says where it stands. */}finally{setBusy(false);}}
  if(status.data?.state==='identity_verified')return null;
  const active=!!login.data?.active;
  // The host names what didn't match (the email, the plan or your other side's account): its words, not a generic line
  // (onboarding audit, issue 2).
  const line=active?t('Waiting for you to finish in your browser'):status.data?.state==='identity_mismatch'?(status.data.detail||t('Signed in as a different account than this one. Nothing was sent.')):keyed?t('Checking the key'):status.data?.detail??t('Checking this account');
  return <div className="dv-fr-verify" role="status">
    <StatusGlyph state={active||status.isPending?'running':status.data?.state==='identity_mismatch'?'failed':'idle'}/><span>{line}</span>
    {!keyed&&(active?<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={busy} onClick={()=>void act('connection_login_cancel')}>{t('Cancel')}</button>
      :<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void act('connection_login')}>{t('Sign in again')}</button>)}
  </div>;
}

export function FirstRun({mode,profiles,onProfilesChanged,onDone,onCancel,onBroughtIn}:Props){
  const client=useQueryClient();
  const status=useQuery(firstRunStatusQuery);
  const account=useQuery({queryKey:['dervo-account'],queryFn:()=>local<DervoAccountStatus>('dervo_account_status'),refetchInterval:q=>accountRefreshInterval(q.state.data),enabled:mode==='first'});
  const [sentTo,setSentTo]=useState('');
  const [facts,setFacts]=useState<Facts|null>(null),[selectionId,setSelectionId]=useState(''),[name,setName]=useState('');
  const [choice,setChoice]=useState<string|null>(null),[picking,setPicking]=useState(false),[model,setModel]=useState(DEFAULT_MODELS.claude);
  const [chosenAccess,setAccess]=useState<Access|null>(null);
  const [busy,setBusy]=useState(''),[error,setError]=useState('');
  const startHeading=useRef<HTMLHeadingElement>(null),projectHeading=useRef<HTMLHeadingElement>(null);
  const [codexForm,setCodexForm]=useState(false);
  const asks=mode==='first'&&!!onBroughtIn;
  const [macFlow,setMacFlow]=useState<MacFlow>(asks?'ask':'folder');
  const [macNote,setMacNote]=useState('');
  const sources=useQuery({queryKey:['onboarding-sources'],enabled:asks&&macFlow==='ask',queryFn:onboardingSources,retry:false});
  const s=status.data;
  // Coming back from Terminal or a browser sign-in: check again without being asked.
  useEffect(()=>{const again=()=>void status.refetch();window.addEventListener('focus',again);return ()=>window.removeEventListener('focus',again);},[]);
  const signIn=mode==='first'?signInState(account.data,account.isError):{kind:'none'} as SignIn;
  const signedIn=signIn.kind==='none'||signIn.kind==='done';
  // Signing in changes the account row the Mac check reports, so ask again once it's done.
  useEffect(()=>{if(signIn.kind==='done')void client.invalidateQueries({queryKey:['first-run-status']});},[signIn.kind]);
  useEffect(()=>{if(mode!=='add'||!onCancel)return;const key=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!event.defaultPrevented&&!busy)onCancel();};window.addEventListener('keydown',key);return ()=>window.removeEventListener('keydown',key);},[mode,onCancel,busy]);

  // The agent: chosen for you from what the check found, until you choose. While Claude Code is still being checked
  // nothing is known yet, so the step says it's checking instead of picking Codex for you (design review, 23 Sep, 1).
  // A check asked again (coming back to the window, Check again) answers "checking" while Claude Code is slow to start.
  // The last real answer stands meanwhile, so the agent choices, and a Codex sign-in open inside them, don't unmount and
  // cancel themselves (onboarding audit, issue 8). Only the very first check has nothing to show.
  const lastKnown=useRef<FirstRunStatus|undefined>(undefined);
  if(s&&s.claude.state!=='checking')lastKnown.current=s;
  const known=s?.claude.state==='checking'?lastKnown.current:s;
  const personal=profiles.filter(p=>p.category==='personal'&&p.id!==known?.claude.profile_id);
  const checks=useVerified(mode==='first'?personal:[]);
  const verifiedIds=new Set(personal.filter(p=>checks[p.id]?.state==='identity_verified').map(p=>p.id));
  const fallback=defaultAgent(known,profiles,verifiedIds);
  const chosen=choice??(fallback?.kind==='system'?'system':fallback?.kind==='profile'?`p:${fallback.profileId}`:known&&known.codex.state!=='missing'?'codex':null);
  const chosenProfile=chosen?.startsWith('p:')?profiles.find(p=>p.id===chosen.slice(2)):undefined;
  const verified=useQuery({...connectionStatusQuery(chosenProfile?.id??''),enabled:!!chosenProfile,refetchInterval:q=>q.state.data?.state==='checking'?700:false});
  const agent:Agent|undefined=chosen==='system'&&known?.claude.state==='ready'?{kind:'system'}:chosenProfile&&verified.data?.state==='identity_verified'?{kind:'profile',profileId:chosenProfile.id}:undefined;
  const provider=agent?.kind==='system'?'claude':chosenProfile?.provider;
  const codexModels=useQuery({queryKey:['codex-models',chosenProfile?.id??''],enabled:!!agent&&provider==='codex',
    queryFn:()=>local<{state:string;detail:string;models:{model:string;displayName?:string;hidden?:boolean}[]}>('model_status',{profileId:chosenProfile!.id}),
    refetchInterval:q=>q.state.data?.state==='checking'?600:false});
  // A Codex account's models come from its own catalog: ask for it once, then take the first one it lists.
  useEffect(()=>{if(agent&&provider==='codex'&&codexModels.data?.state==='unchecked')void local('model_check',{profileId:chosenProfile!.id}).then(()=>codexModels.refetch()).catch(()=>{});},[codexModels.data?.state,chosenProfile?.id]);
  const codexChoices=(codexModels.data?.models??[]).filter(m=>!m.hidden);
  const claudeDefault=claudeDefaultModel(s?.claude.version),firstModels=firstModelsFor(s?.claude.version);
  useEffect(()=>{
    if(provider==='claude'&&!firstModels.some(m=>m.id===model))setModel(claudeDefault);
    if(provider==='codex'&&!codexChoices.some(m=>m.model===model))setModel(codexChoices[0]?.model??'');
  },[provider,claudeDefault,codexChoices.map(m=>m.model).join()]);
  // Nothing usable yet: the choices stay open, and only when nothing is chosen does the line ask for one.
  const listOpen=picking||(!!known&&!agent);
  // The plan (paid plans, 8 Oct): a signed-in account with no trial or plan is offered one right after sign-in. "Set up
  // first, decide later" is remembered on this Mac, so quitting and coming back doesn't ask again.
  const need=mode==='first'&&signIn.kind==='done'?planNeed(account.data):null;
  const [later,setLater]=useState(laterChosen);
  const [savedPhase,setSavedPhase]=useState<Phase|null>(storedPhase);
  // Quitting halfway and opening Dervo again lands on the step you'd reached, with what's done still done: the account
  // and connected agents are the host's, the step is this Mac's (onboarding-step, beside dervo-welcomed).
  const [resumed]=useState(()=>mode==='first'&&storedPhase()!==null);
  const goTo=(next:Phase)=>{setSavedPhase(next);storePhase(next);};
  // Signed out, from here or from Settings: the step and plan choice were that account's, so they go too (merge review of #23).
  useEffect(()=>{if(signIn.kind==='email'&&(savedPhase||later)){forgetOnboarding();setSavedPhase(null);setLater(false);}},[signIn.kind]);
  // While the account is still being read on launch, a saved step stands, so a returning tester doesn't see sign-in flash.
  const phaseNow:Phase=signIn.kind==='checking'&&savedPhase?'agents':!signedIn||(need&&!later)?'sign-in':'agents';
  // A connected account whose check hasn't answered yet isn't a missing agent: the saved step stands until it does.
  const checksPending=personal.some(p=>{const state=checks[p.id]?.state;return !state||state==='checking'||state==='unchecked';});
  // Projects needs an agent that's ready. Until the first check answers, a saved step is trusted, so a returning tester
  // doesn't see Your agents flash past; an agent that's gone since sends them back to it.
  const phase:Phase=mode!=='first'?'projects':phaseNow==='agents'&&savedPhase==='projects'&&(!known||checksPending||agentReadyOf(known,verifiedIds))?'projects':phaseNow;
  const step:'sign-in'|'agents'|'project'|'start'=mode==='first'&&phase!=='projects'?phase:!facts?'project':'start';
  const agentsHeading=useRef<HTMLHeadingElement>(null),signInTitle=useRef<HTMLHeadingElement>(null),projectsTitle=useRef<HTMLHeadingElement>(null);
  // Signed in with a plan still to choose: the code field that had focus is gone, so the page title takes it.
  useEffect(()=>{if(signedIn&&step==='sign-in')requestAnimationFrame(()=>signInTitle.current?.focus());},[signedIn&&step==='sign-in']);
  useEffect(()=>{if(step==='project')requestAnimationFrame(()=>(mode==='first'?projectsTitle:projectHeading).current?.focus());if(step==='start')requestAnimationFrame(()=>startHeading.current?.focus());
    if(step==='agents')requestAnimationFrame(()=>agentsHeading.current?.focus());},[step]);

  async function act(key:string,work:()=>Promise<void>){if(busy)return;setBusy(key);setError('');try{await work();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy('');}}
  /** `flow` is the way in you picked it from, kept only once a folder is picked: cancelling the picker (or a folder it
   * refuses) leaves the screen as it was, import card and all (RC review, 9 Oct). */
  function choose(flow?:MacFlow){void act('folder',async()=>{
    const picked=await invoke<{selectionId:string;path:string;name:string}|null>('choose_project_folder');
    if(!picked)return;
    if(flow)setMacFlow(flow);
    const found=await local<Facts>('first_run_inspect',{selectionId:picked.selectionId});
    setSelectionId(picked.selectionId);setFacts(found);setName(found.repo??found.name);setAccess(null);
  });}
  useEffect(()=>{const key=(event:KeyboardEvent)=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='o'&&phase==='projects'){event.preventDefault();choose(macFlow==='ask'||macFlow==='look'?'folder':undefined);}};window.addEventListener('keydown',key);return ()=>window.removeEventListener('keydown',key);},[phase,busy,macFlow]);
  const agentName=agent?.kind==='system'?'Claude Code':provider==='codex'?'Codex':'Claude Code';
  /** The account a new project starts on: your existing Claude Code made into a Dervo account, or the connected one. */
  async function prepareAgent(){
    let profileId=agent?.kind==='profile'?agent.profileId:undefined;
    if(agent?.kind==='system'){const made=await local<{id:string}>('first_run_claude');profileId=made.id;await onProfilesChanged();}
    if(!profileId||!model)throw new Error(t('Choose an agent first.'));
    return {profileId,model};
  }
  function start(){void act('start',async()=>{
    const {profileId}=await prepareAgent();
    const created=await local<Created>('first_run_finish',{selectionId,profileId,model,name:name.trim(),access,...(access==='full'?{warning:facts?.full_access?.warning}:{})});
    // Start empty: the project comes in with nothing read, so the blank-project offer doesn't ask again.
    if(macFlow==='empty')rememberHistoryDeclined(created.id);
    await client.invalidateQueries({queryKey:['first-run-status']});
    forgetOnboarding();
    await onDone(created,agentName);
  });}
  async function added(id:string){await onProfilesChanged();setChoice(`p:${id}`);}

  // The agent line and its choices.
  const claude=known?.claude;
  // The work side, for onboarding's Work group: a verified work account, or the way to add one in place.
  const workSide=useWorkSide(profiles,s?.claude.version);
  const agentValue=agent?.kind==='system'?<>Claude Code · {modelName(model)}<small>{claude?.method==='api_key'?t('Your existing sign-in, with an Anthropic API key. Billed per use.'):claude?.plan?t('Your existing sign-in, {who} ({plan})',{who:claude?.email??t('on this Mac'),plan:planName(claude.plan)}):t('Your existing sign-in, {who}',{who:claude?.email??t('on this Mac')})}</small></>
    :agent&&chosenProfile?<>{chosenProfile.provider==='codex'?'Codex':'Claude'} · {model?modelName(model):t('choosing a model')}{chosenProfile.category==='work'&&<> <WorkTag/></>}<small>{chosenProfile.method==='api_key'?t('{provider} API key, billed per use',{provider:chosenProfile.provider==='codex'?'OpenAI':'Anthropic'}):chosenProfile.identity}</small></>
    :chosenProfile?<>{chosenProfile.provider==='codex'?'Codex':'Claude'}<small>{chosenProfile.method==='api_key'?t('Checking the key'):chosenProfile.identity}</small></>
    :choice==='codex'&&codexForm?<>Codex<small>{t('Sign in with ChatGPT below. Dervo shows which account signed in before adding it.')}</small></>:choice==='key'?<>{t('An API key')}<small>{t('Add it below.')}</small></>:null;
  const choices:Choice<string>[]=[];
  let claudeChoice:Choice<string>|undefined;
  if(claude){
    const ready=claude.state==='ready';
    const command=claude.state==='missing'?claude.install:claude.state==='outdated'?claude.update:claude.state==='signed_out'?'claude':undefined;
    claudeChoice={value:'system',name:'Claude Code',
      line:ready?(claude.method==='api_key'?t('Your existing sign-in, with an Anthropic API key. Billed per use by Anthropic.'):`${t('Your existing sign-in')} · ${claude.email??t('this Mac')}${claude.plan?` · ${planName(claude.plan)}`:''}`)
        :<>{claude.state==='missing'?t('Install it in Terminal, then come back. Dervo notices on its own.'):claude.state==='outdated'?t('Version {version} is too old for Dervo. Update it in Terminal, then come back.',{version:claude.version??''})
          :claude.state==='signed_out'?t('Dervo couldn’t find its sign-in. If you’re already signed in, choose Check again. If not, run it in Terminal and sign in, then come back.'):claude.detail}{command&&<Command text={command}/>}</>};
    if(ready)choices.push(claudeChoice);
  }
  // Work accounts are listed too, marked, so a Work project can be added by hand: the account you pick decides its side.
  for(const p of [...personal,...profiles.filter(p=>p.category==='work')]){
    const who=p.method==='api_key'?t('{provider} API key, billed per use',{provider:p.provider==='codex'?'OpenAI':'Anthropic'}):p.identity;
    const work=p.category==='work'&&<span className="dv-fr-work-note">{t('A Work project: it only ever runs on work accounts.')}</span>;
    choices.push({value:`p:${p.id}`,name:<>{p.provider==='codex'?'Codex':'Claude'} · {p.name||(p.method==='api_key'?t('API key'):p.identity)}{p.category==='work'&&<> <WorkTag/></>}</>,
      line:p.provider==='codex'?<>{who}{work}{codexNote}</>:<>{who}{work}</>,body:<Verifying profile={p}/>});
  }
  // Codex is built in, so without a working Claude Code it's the way through that needs no Terminal: it leads.
  if(known&&known.codex.state!=='missing')choices.push({value:'codex',name:'Codex',line:<>{t('Built into Dervo. Sign in with your ChatGPT account in the browser.')}{codexNote}</>,
    end:!codexForm&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>{setChoice('codex');setCodexForm(true);}}>{t('Sign in with ChatGPT')}</button>,
    body:codexForm?<CodexSignIn onAdded={added} onCancel={()=>setCodexForm(false)}/>:undefined});
  if(claudeChoice&&claude?.state!=='ready')choices.push(claudeChoice);
  choices.push({value:'key',name:t('An API key'),line:t('Anthropic or OpenAI. Billed per use by the provider, not your plan.'),
    end:<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" tabIndex={-1} onClick={()=>setChoice('key')}>{t('Add a key')}</button>,body:<KeyForm onAdded={added}/>});

  // What the agent can do (Bryce, 9 Oct): full access with Dervo's checks is recommended and chosen until you choose,
  // editing with your approval is the safer pick, and every choice is on screen with what it means. Editing needs git, so
  // every change can be undone; full access needs it too, and isn't offered on an API-key account, whose key any command
  // could print (API_KEY_FULL_ACCESS).
  const keyed=agent?.kind==='system'?claude?.method==='api_key':chosenProfile?.method==='api_key';
  const fullWhy=!facts?.git?t('Needs git, so Dervo can record and undo every change.'):!facts.full_access?t('Turn it on later in Project settings.')
    :keyed?t('Not on an API-key account: any command it runs could print the key.'):provider&&provider!=='claude'&&provider!=='codex'?t('Needs a Claude Code or Codex account.'):'';
  const access:Access=facts&&!facts.git?'read':chosenAccess==='full'&&fullWhy?'edit':chosenAccess??(fullWhy?'edit':'full');
  const fullText=facts?.full_access?.detail[provider==='codex'?'codex':'claude'];
  const checkNames=facts?.full_access?.checks??[];
  const factsLine=facts?[tildePath(facts.path),facts.git?[facts.branch?t('on {branch}',{branch:facts.branch}):t('a git repository'),facts.uncommitted?t('{count} uncommitted files',{count:facts.uncommitted}):t('nothing uncommitted')].join(', '):t('not a git repository'),
    facts.stack.length?t('looks like {stack}',{stack:formatList(facts.stack)}):''].filter(Boolean).join(' · '):'';
  const canStart=!!agent&&!!model&&!!selectionId&&!!name.trim()&&!busy;
  const caption=!agent?t('Start waits until an agent is connected.'):!model?t('Start waits for this account’s models.'):!name.trim()?t('Give the project a name.')
    :need&&later?t('It’s set up now. The overview starts once you start a trial or choose a plan.')
    :t('First it reads the project and writes you an overview. It changes nothing in that run.');
  const mac=macLine(s,status.isError);
  const macShown=mode==='first'||mac.state!=='done';
  const accountRow=s?.account;
  const doneEmail=accountRow?.email||sentTo||account.data?.identity?.display_name||t('Signed in');

  /** A tester waiting for approval who signed in with the wrong email: sign out, and the email step is offered again. */
  function changeEmail(){void act('sign-out',async()=>{setSentTo('');forgetOnboarding();setLater(false);setSavedPhase(null);await local('dervo_account_sign_out',{});await account.refetch();});}

  const waiting=signIn.kind==='done'&&signIn.pending;
  const steps:ReactNode[]=[];
  let n=0;
  if(mode==='first'&&signIn.kind!=='none'&&phase==='sign-in'){
    n++;
    if(signedIn){
      steps.push(<DoneStep key="in" title={t('Signed in')} line={`${doneEmail} · ${waiting?t('beta access pending'):t('in the private beta')}`}/>);
      if(need)steps.push(<PlanCard key="plan" need={need} account={account.data!} refetch={()=>account.refetch()} onLater={()=>{rememberLater();setLater(true);}}/>);
    }else steps.push(<SignInCard key="in" n={n} plain account={account.data} refetch={()=>account.refetch()} sentTo={sentTo} setSentTo={setSentTo}/>);
  }
  n++;
  const plain=mode==='first';
  if(phase==='projects'){
  if(step==='start'&&facts)steps.push(<DoneStep key="project" title={t('Your project')} line={`${name.trim()||facts.name} · ${tildePath(facts.path)}`}
    action={<button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>choose()}>{t('Choose another')}</button>}/>);
  else if(step==='project'&&asks&&macFlow==='ask')steps.push(<StepCard key="project" n={n} plain={plain} title={t('Bring in what you’re working on')} headingRef={projectHeading}>
    <p className="dv-card-text">{t('Claude Code and Codex keep a list of every folder you’ve worked in. Dervo can look at that list and suggest what to bring in, so you don’t start empty.')}</p>
    <ul className="dv-ob-sources" aria-label={t('Where Dervo would look')}>{(sources.data?.sources??[{tool:'claude',path:'~/.claude/projects',present:false},{tool:'codex',path:'~/.codex',present:false}]).map(source=>{const line=sourceLine(source);
      return <li key={source.tool}><b>{line.name}</b><code>{line.path}</code><span className="dv-caption" data-present={sources.data?String(source.present):undefined}>{sources.data?line.state:t('Checking')}</span></li>;})}</ul>
    <dl className="dv-ob-promise"><dt>{t('Looks at')}</dt><dd>{t('Folder names, dates, and how many conversations each one has.')}</dd>
      <dt>{t('Doesn’t open')}</dt><dd>{t('Anything you or an agent wrote. That comes later, only for projects you pick.')}</dd></dl>
    {macNote&&<p className="dv-fr-note-inline dv-caption" role="status">{macNote}</p>}
    <div className="dv-ob-ask-actions">
      <button type="button" className="dv-btn dv-btn-primary" disabled={!!sources.data&&!sources.data.sources.some(x=>x.present)} onClick={()=>{setMacNote('');setMacFlow('look');}}>{t('Look at folder names')}</button>
      <button type="button" className="dv-fr-folder" disabled={!!busy} onClick={()=>choose('folder')}>
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true"><path d="M2.5 5.5a1.5 1.5 0 011.5-1.5h3.6l1.8 2H16a1.5 1.5 0 011.5 1.5v7A1.5 1.5 0 0116 16H4a1.5 1.5 0 01-1.5-1.5z"/></svg>
        <span><b>{t('Choose one folder')}</b><small>{t('Dervo offers to read that folder’s history once it’s added.')}</small></span><kbd>⌘O</kbd>
      </button>
      <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>choose('empty')}>{t('Start empty')}</button>
    </div>
    {sources.data&&!sources.data.sources.some(x=>x.present)&&<p className="dv-caption" role="status">{t('Claude Code and Codex haven’t kept any folders on this Mac yet, so there’s nothing to look at. Choose one folder to start.')}</p>}
    {error&&<p className="dv-err" role="alert">{error}</p>}
    <p className="dv-caption dv-ob-fine">{t('Nothing leaves this Mac and no model is called. Dervo makes nothing until you’ve picked.')} {MAC_ASKS}</p>
  </StepCard>);
  else if(step==='project')steps.push(<StepCard key="project" n={n} plain={plain} title={t('Your project')} headingRef={projectHeading}>
    <button type="button" className="dv-fr-folder" disabled={!!busy} onClick={()=>choose()}>
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true"><path d="M2.5 5.5a1.5 1.5 0 011.5-1.5h3.6l1.8 2H16a1.5 1.5 0 011.5 1.5v7A1.5 1.5 0 0116 16H4a1.5 1.5 0 01-1.5-1.5z"/></svg>
      <span><b>{busy==='folder'?t('Opening…'):t('Choose a project folder')}</b><small>{macFlow==='empty'?t('It starts empty: nothing from its history is read.'):t('Choosing a folder doesn’t let anything change it yet.')}</small></span><kbd>⌘O</kbd>
    </button>
    {asks&&<div className="dv-card-actions"><span/><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>setMacFlow('ask')}>{t('Other ways to start')}</button></div>}
    {error&&<p className="dv-err" role="alert">{error}</p>}
  </StepCard>);
  else if(mode==='add')steps.push(<LaterStep key="project" n={n} title={t('Your project')}/>);
  n++;
  if(step==='start'&&facts)steps.push(<StepCard key="start" n={n} plain={plain} title={t('Start')} headingRef={startHeading}>
    <label className="dv-field"><span>{t('Name')}</span>
      <input className="dv-input" aria-label={t('Name')} value={name} maxLength={80} aria-describedby="dv-fr-facts" onChange={event=>setName(event.target.value)}/>
      <small id="dv-fr-facts">{factsLine}</small></label>
    <div className="dv-fr-setting" data-open={listOpen||undefined}>
      <span className="dv-fr-k">{t('Agent')}</span>
      {agentValue?<span className="dv-fr-v">{agentValue}</span>
        :<span className="dv-fr-v dv-fr-needs"><StatusGlyph state={known?'needs':'running'} accent={!!known}/><span>{known?t('Choose an agent'):t('Checking this Mac')}<small>{known?missingLine(known.claude,known.codex.state!=='missing'):s?s.claude.detail:t('Claude Code, Codex and git')}</small></span></span>}
      {!!agent&&<button type="button" className="dv-btn dv-btn-quiet" aria-expanded={listOpen} onClick={()=>setPicking(open=>!open)}>{listOpen?t('Done'):t('Change')}</button>}
    </div>
    {/* Picking Codex opens its sign-in at once, like the API key's form, instead of waiting for the row's button (audit,
        issue 3). */}
    {listOpen&&<ChoiceRows label={t('Agent for this project')} value={chosen} open lift={chosen} onChange={next=>{setChoice(next);if(next==='codex')setCodexForm(true);}} choices={choices}/>}
    {/* The model shows while the choices are open, and whenever there isn't one yet: a Codex catalog that's loading or
        failed held Start with no reason (audit, issue 6). */}
    {agent&&(listOpen||!model)&&<div className="dv-fr-setting dv-fr-model"><span className="dv-fr-k">{t('Model')}</span><span className="dv-fr-v">
      {provider==='claude'?<Select label={t('Model')} value={model} onChange={setModel} groups={[{options:firstModels.map(m=>({value:m.id,label:m.label}))}]}/>
        :codexChoices.length?<Select label={t('Model')} value={model} onChange={setModel} groups={[{options:codexChoices.map(m=>({value:m.model,label:m.displayName??m.model}))}]}/>
        :<small role="status">{codexModels.data?.state==='failed'?(codexModels.data.detail||t('Dervo couldn’t load this account’s models.')):codexModels.data?.detail||t('Loading this account’s models')}</small>}</span>
      {provider==='codex'&&codexModels.data?.state==='failed'?<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>void local('model_check',{profileId:chosenProfile!.id}).catch(()=>{}).then(()=>codexModels.refetch())}>{t('Try again')}</button>:<span/>}</div>}
    <p className="dv-fr-access-k" aria-hidden="true">{t('What it can do')}</p>
    <ChoiceRows label={t('What it can do')} value={access} open onChange={next=>setAccess(next)} choices={[
      {value:'full',name:<>{t('Full access with Dervo’s checks')}{!fullWhy&&<Tag>{t('Recommended')}</Tag>}</>,disabled:!!fullWhy,
        line:fullWhy||t('{agent} can run commands as you, like installs, builds and tests.',{agent:agentName}),
        body:<div className="dv-fr-full">
          <p>{checkNames.length?t('After each change, Dervo runs the project’s tests ({checks}) and shows you what passed.',{checks:formatList(checkNames)})
            :t('Dervo didn’t find tests in this folder yet. Once there are some, turn on checks in Project settings and it runs them after each change.')}</p>
          {fullText&&<p className="dv-fr-warning"><b>{fullText.split(/(?<=\.) /)[0]}</b> {fullText.split(/(?<=\.) /).slice(1).join(' ')}</p>}
        </div>},
      {value:'edit',name:<>{t('Edit files, with your approval')}{!fullWhy&&<Tag>{t('Safer')}</Tag>}</>,disabled:!facts.git,
        line:facts.git?(facts.uncommitted?t('It changes files but can’t run commands. Your uncommitted work stays as it is, and nothing’s committed until you approve.'):t('It changes files but can’t run commands. You review every change, and nothing’s committed until you approve.')):t('Editing needs git, so every change can be undone. Turn it on later in Project settings.')},
      {value:'read',name:t('Read and explain only'),line:t('It reads the project and answers questions. It can’t change anything.')}]}/>
    {error&&<p className="dv-err" role="alert">{error}</p>}
    <div className="dv-card-actions"><span className="dv-caption">{caption}</span>
      <button type="button" className="dv-btn dv-btn-primary" disabled={!canStart} onClick={start}>{busy==='start'?t('Starting…'):t('Start')}
        {busy!=='start'&&<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 8h9M8.5 4l4 4-4 4"/></svg>}</button></div>
  </StepCard>);
  else if(mode==='add')steps.push(<LaterStep key="start" n={n} title={t('Start')}/>);
  }

  const macRow=macShown&&<p className="dv-fr-mac" aria-live="polite"><StatusGlyph state={mac.state}/><span>{mac.text}</span>
    {mac.recheck&&<button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={status.isFetching} onClick={()=>void status.refetch()}>{status.isFetching?t('Checking…'):mac.state==='failed'?t('Try again'):t('Check again')}</button>}</p>;

  if(mode==='add')return <section className="dv-fr-add" aria-label={t('Add a project')}><div className="dv-fr-col">
    <div className="dv-fr-add-head"><h1 className="dv-display-2">{t('Add a project.')}</h1>{onCancel&&<button type="button" className="dv-btn dv-btn-quiet" onClick={onCancel}>{t('Cancel')}</button>}</div>
    <p className="dv-lede">{t('Each project keeps its own agent and what it can do.')}</p>
    <div className="dv-fr-steps">{steps}</div>{macRow}
  </div></section>;
  const personalWho=agent?.kind==='system'?claude?.email:chosenProfile?.method==='api_key'?undefined:chosenProfile?.identity;
  const onboardingAgent:OnboardingAgent={ready:!!agent&&!!model,name:`${agentName} · ${model?modelName(model):t('choosing a model')}`,
    who:personalWho?`${agentName} · ${personalWho}`:undefined,
    line:known?onboardingHeldLine(known.claude):t('Still checking this Mac for an agent.'),
    prepare:prepareAgent};
  // Onboarding (8 Oct 2026): three steps under one stepper, Sign in, Your agents, Your projects, each its own screen that
  // arrives with one soft rise. The agents step is where the Mac check speaks; past it, nothing can be held for want of an
  // agent, which is what left the whole-Mac look dead-ended before (onboarding audit, issue 1).
  const done:Phase[]=[...(signedIn&&!(need&&!later)?['sign-in' as Phase]:[]),...(phase==='projects'?['agents' as Phase]:[])];
  const head=<div className="dv-ob-head">
    <MomentMark size={30}/>
    <Stepper phase={phase} done={done} onGo={next=>{if(next==='agents'){setFacts(null);setMacFlow(asks?'ask':'folder');goTo('agents');}}}/>
  </div>;
  const pendingNote=waiting&&phase!=='sign-in'&&<div className="dv-ob-pending" role="note"><StatusGlyph state="queued"/>
    <div><p><b>{doneEmail}</b> · {t('beta access pending')}</p><p>{t('Your beta access is waiting for Bryce to approve it. Set everything up now, and the agent starts the moment he does. Dervo checks every minute while it’s open. Invited under a different email? Switch now: once you set up a project, this Mac stays with the account you’re signed in as.')}</p>
    {account.data?.can_sign_out&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={changeEmail}>{t('Use a different email')}</button>}</div></div>;
  const resumeLine=resumed&&phase!=='sign-in'&&<p className="dv-caption dv-ob-resume">{t('Welcome back. You’re where you left off.')}</p>;
  const startsOn=agent?(model?`${agentName} · ${modelName(model)}`:agentName):undefined;
  let body:ReactNode;
  // The stepper is there from the first screen, so the three steps the lede names are visible, not promised (design review,
  // 8 Oct). After sign-in, only the plan stands between you and your agents, and the lede says that instead.
  const welcomeBack=need&&(need.kind==='ended'||need.kind==='canceled');
  if(phase==='sign-in')body=<>
    {head}
    <h1 className="dv-display-1 dv-ob-title" ref={signInTitle} tabIndex={-1}>{signedIn?(welcomeBack?t('Welcome back.'):t('You’re in.')):t('Let’s get you set up.')}</h1>
    <p className="dv-lede">{signedIn?t('One thing before your agents.'):t('Three short steps: sign in, connect the agents you already use, and bring in what you’re working on.')}</p>
    <div className="dv-fr-steps">{steps}</div>
    {macRow}
  </>;
  else if(phase==='agents')body=<>{head}{pendingNote}{resumeLine}
    <AgentsStep status={known} failed={status.isError} checking={status.isFetching} recheck={()=>void status.refetch()} profiles={profiles} onProfilesChanged={onProfilesChanged}
      onAdded={id=>setChoice(`p:${id}`)} startsOn={startsOn} headingRef={agentsHeading} onContinue={()=>goTo('projects')}/></>;
  else if(asks&&macFlow==='look'&&step==='project')body=<>{head}
    <MacOnboarding agent={onboardingAgent} work={{side:workSide,profiles,onChanged:onProfilesChanged}} onBack={()=>setMacFlow('ask')} onStartEmpty={()=>{setMacFlow('empty');}}
      onChooseFolder={()=>choose('folder')}
      onDone={projectId=>{if(projectId){forgetOnboarding();void onBroughtIn?.(projectId);}else{setMacFlow('ask');setMacNote(t('Nothing new came in from this Mac. Choose a personal folder to start with.'));}}}/></>;
  else body=<>{head}{pendingNote}{resumeLine}
    <h1 className="dv-display-1 dv-ob-title" ref={projectsTitle} tabIndex={-1}>{step==='start'?t('Almost there.'):t('Bring in your projects.')}</h1>
    <p className="dv-lede">{step==='start'?t('Check its name and who runs it. First its agent reads the project and writes you an overview, changing nothing.')
      :t('Dervo can suggest what to bring in from this Mac, or start with one folder.')}</p>
    <div className="dv-fr-steps">{steps}</div>
  </>;
  return <BrandWindow corner={betaCorner()} label={t('Welcome to Dervo')}>
    <div className="dv-moment dv-fr" data-phase={phase}><div className="dv-moment-col">
      <div key={phase==='projects'?`projects-${macFlow==='look'?'look':'cards'}`:phase} className="dv-ob-phase">{body}</div>
    </div></div>
  </BrandWindow>;
}
