import {useEffect,useRef,useState,type FormEvent,type ReactNode} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Button} from '@/shared/ui/button';
import {local,type DervoAccountStatus} from './api';
import {ProfilePhoto,ProfileAvatar,photoImage} from './ProfilePhoto';
import {SettingRow,SettingsSection,TextField} from './DervoControls';
import {Confirm,OpenCard,StatusWord,dayLabel,useSaved} from './SettingsKit';
import type {GlyphState} from './StatusGlyph';
import {t,tr} from '@/i18n';
import {DervoPlanSection,PRICES,trialEnding} from './DervoPlan';

const labels:Record<DervoAccountStatus['state'],string>={unavailable:t('Local workspace'),signed_out:t('Signed out'),awaiting_user:t('Finish signing in'),verifying:t('Checking your account'),signed_in:t('Signed in'),expired:t('Session expired'),cancelled:t('Sign-in cancelled'),unknown:t('Account status unknown'),interrupted:t('Sign-in was interrupted'),restore_required:t('Check your saved session'),account_mismatch:t('Account selection needs review'),failed:t('Sign-in didn’t finish')};
export function accountRefreshInterval(status:DervoAccountStatus|undefined,now=Date.now()):number|false {
 if(status?.can_poll)return 1000;
 if(status?.state==='signed_in'){
  const deadlines=[status.expires_at,status.access_valid_until].filter((value):value is number=>typeof value==='number'&&Number.isFinite(value)).map(value=>value*1000);
  const membershipExpiry=status.membership?.expires_at?Date.parse(status.membership.expires_at):NaN;
  if(Number.isFinite(membershipExpiry)&&membershipExpiry>now)deadlines.push(membershipExpiry);
  if(deadlines.length)return Math.max(1000,Math.min(60000,...deadlines.map(deadline=>deadline-now)));
 }
 return false;
}
/** Beta access, as one row: the status word on the right, one line of detail, and "Check again" when it applies. */
export function betaAccess(status:DervoAccountStatus,now=Date.now()):{state:GlyphState;word:string;line:string;check:boolean}{
 const membership=status.membership;
 if(!membership)return {state:'idle',word:t('Not confirmed'),line:t('Check your session to see whether you have access.'),check:true};
 const ends=membership.expires_at?Date.parse(membership.expires_at):NaN;
 const expired=Number.isFinite(ends)&&ends<=now;
 const stale=typeof status.access_valid_until==='number'&&status.access_valid_until*1000<=now;
 const admin=membership.is_admin?` ${t('You’re an admin, so Testers is in this sidebar.')}`:'';
 if(membership.status==='revoked')return {state:'failed',word:t('Removed'),line:t('You’re still signed in. This account no longer has access to the private beta.'),check:false};
 if(membership.status==='pending')return {state:'needs',word:t('Pending'),line:t('Your account is ready. Beta access hasn’t been approved yet.'),check:false};
 if(expired)return {state:'stopped',word:t('Ended {day}',{day:dayLabel(ends/1000,{time:false,now})}),line:t('Your beta access period has ended. Your local projects are still here.'),check:false};
 if(membership.status==='approved'&&membership.allowed&&!stale)return {state:'done',word:t('Approved'),
  line:`${Number.isFinite(ends)?t('Every beta feature is on for this account, until {day}.',{day:dayLabel(ends/1000,{time:false,now})}):t('Every beta feature is on for this account, with no end date.')}${admin}`,check:false};
 return {state:'idle',word:t('Needs checking'),line:t('Check your session to confirm your current beta access.'),check:true};
}
export function DervoAccountNotice({onOpen}:{onOpen:(trigger:HTMLElement)=>void}){
 const account=useQuery({queryKey:['dervo-account'],queryFn:()=>local<DervoAccountStatus>('dervo_account_status'),refetchInterval:q=>accountRefreshInterval(q.state.data)});
 const data=account.data;
 if(!account.isError&&data?.method!=='email_code')return null;
 const plan=data?.state==='signed_in'&&!account.isError?data.plan:null;
 const box='m-4 flex shrink-0 flex-wrap items-center justify-between gap-4 rounded-2xl border border-primary/15 bg-primary/5 p-4';
 const notice=(title:string,detail:string,button:ReactNode,error?:string|null)=><section aria-label={t('Dervo account access')} className={box}><div className="min-w-0 flex-1"><p className="text-sm font-semibold">{title}</p><p className="mt-1 text-sm leading-relaxed text-muted-foreground">{detail}</p>{error&&<p role="alert" className="mt-1 text-sm text-destructive">{error}</p>}</div>{button}</section>;
 const toPlans=(label:string)=><Button size="sm" onClick={event=>onOpen(event.currentTarget)}>{label}</Button>;
 // Paid plans (8 Oct 2026). These show even while access is valid: a failed payment and a trial about to end both need
 // a choice before agent work stops.
 if(plan?.subscription?.status==='past_due')return notice(t('Your payment didn’t go through'),t('Update your payment method in billing to keep starting agent work.'),toPlans(t('Open billing')));
 const left=trialEnding(plan);
 if(left!==null)return notice(left===0?t('Your free trial ends today'):left===1?t('Your free trial ends tomorrow'):t('Your free trial ends in {days} days',{days:left}),t('Choose a plan to keep starting agent work after that. Your saved work stays either way.'),toPlans(t('Choose a plan')));
 const allowed=data?.state==='signed_in'&&data.membership?.allowed&&typeof data.access_valid_until==='number'&&data.access_valid_until*1000>Date.now();
 if(allowed&&!account.isError)return null;
 const pending=data?.state==='signed_in'&&data.membership?.status==='pending';
 const signingIn=data?.state==='awaiting_user'||(data?.state==='verifying'&&data.can_cancel);
 const checking=!account.isError&&data?.can_poll&&!signingIn&&(data.state==='verifying'||(data.state==='signed_in'&&data.membership?.status==='approved'));
 if(checking)return <section aria-label={t('Dervo account access')} className="m-4 shrink-0 rounded-2xl border bg-muted/20 p-4"><p role="status" className="text-sm font-medium">{t('Checking your Dervo account')}</p><p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t('Your saved work is available while this finishes.')}</p></section>;
 if(plan&&data?.can_start_trial){
  // A new account starts a no-card trial right here (trial mode 'app').
  const starting=data.billing?.state==='starting';
  return notice(t('Try Dervo free for {days} days',{days:plan.trial.days}),t('No card needed. Agents run on your own Claude and Codex plans.'),
   <Button size="sm" disabled={data.can_poll||starting} onClick={()=>{void local('dervo_plan_start_trial').catch(()=>{}).finally(()=>account.refetch());}}>{starting?t('Starting…'):t('Start free trial')}</Button>,
   data.billing?.state==='error'?data.billing.detail:null);
 }
 if(plan?.plan==='none'){
  if(plan.trial.available)return notice(t('Try Dervo free for {days} days',{days:plan.trial.days}),
   t('Then {month} a month or {year} a year. Cancel before the trial ends and you won’t be charged.',{month:PRICES.month,year:PRICES.year}),toPlans(t('Start free trial')));
  if(plan.subscription?.status==='canceled')return notice(t('Your Dervo Pro plan has ended'),t('Choose a plan to keep starting agent work. Your saved work is still here.'),toPlans(t('Choose a plan')));
  return notice(t('Your free trial has ended'),t('Choose a plan to keep starting agent work. Your saved work is still here.'),toPlans(t('Choose a plan')));
 }
 if(plan?.subscription?.status==='paused')return notice(t('Your Dervo Pro plan is paused'),t('Resume it in billing to start agent work again. Your saved work is still here.'),toPlans(t('Open billing')));
 const signedOut=data?.state==='signed_out'||data?.state==='cancelled';
 const title=account.isError?t('Account status is unavailable'):pending?t('Your beta access is pending'):signingIn?t('Finish signing in'):signedOut?t('Welcome to Dervo'):t('Your Dervo account needs attention');
 const detail=pending?t('Your account is ready. You can start agent work once your beta access is approved.'):signedOut?t('Sign in to connect your beta access. Your saved projects stay on this Mac.'):t('Open your account to check access. Your saved conversations are still available.');
 return <section aria-label={t('Dervo account access')} className="m-4 flex shrink-0 flex-wrap items-center justify-between gap-4 rounded-2xl border border-primary/15 bg-primary/5 p-4"><div className="min-w-0 flex-1"><p className="text-sm font-semibold">{title}</p><p className="mt-1 text-sm leading-relaxed text-muted-foreground">{detail}</p></div><Button size="sm" variant={pending?'outline':'default'} onClick={event=>onOpen(event.currentTarget)}>{signedOut?t('Sign in'):signingIn?t('Continue sign-in'):t('Open account')}</Button></section>;
}
export function DervoAccountIdentity(){
 const account=useQuery({queryKey:['dervo-account'],queryFn:()=>local<DervoAccountStatus>('dervo_account_status'),refetchInterval:q=>accountRefreshInterval(q.state.data)});
 const inFlight=useRef(false),data=account.isError?undefined:account.data;
 useEffect(()=>{
  if(inFlight.current||data?.state!=='signed_in'||!data.can_save_profile||data.photo?.state!=='not_loaded'||!data.identity)return;
  inFlight.current=true;
  void local('dervo_account_photo',{subject:data.identity.subject}).then(()=>account.refetch()).catch(()=>{}).finally(()=>{inFlight.current=false;});
 },[data]);
 const name=data?.state==='signed_in'?data.identity?.display_name:undefined;
 return <><ProfileAvatar image={photoImage(data)} name={name}/><span className="min-w-0"><span className="block truncate text-sm font-semibold">{name||t('You')}</span><span className="block text-xs text-sidebar-foreground/70">Dervo</span></span></>;
}
/** Account, in Settings (settings/account.html): Profile, Beta access and Session, or the sign-in steps when you're signed
 * out. Email and code live only in this mounted form. The host establishes identity and stores sessions. */
export function DervoAccount(){
 const account=useQuery({queryKey:['dervo-account'],queryFn:()=>local<DervoAccountStatus>('dervo_account_status'),refetchInterval:q=>accountRefreshInterval(q.state.data)});
 const [pendingOperation,setPendingOperation]=useState<string|null>(null),[failed,setFailed]=useState(false);
 const busy=pendingOperation!==null;
 const inFlight=useRef(false);
 const [email,setEmail]=useState(''),[sentTo,setSentTo]=useState(''),[code,setCode]=useState(''),[wrongCode,setWrongCode]=useState(false);
 const [confirmSignOut,setConfirmSignOut]=useState(false),[saved,markSaved]=useSaved();
 const codeField=useRef<HTMLInputElement>(null);
 const data=account.data;
 const emailCode=data?.method==='email_code';
 const verifyingCode=emailCode&&data?.state==='awaiting_user'&&data.can_verify&&!!data.operation_id;
 useEffect(()=>{setCode('');},[verifyingCode,data?.operation_id]);
 useEffect(()=>{if(verifyingCode&&!busy)codeField.current?.focus();},[verifyingCode,data?.operation_id,busy]);
 useEffect(()=>{if(data?.state==='signed_in'||data?.state==='signed_out'||data?.state==='unavailable'){setEmail('');setCode('');}},[data?.state]);
 useEffect(()=>{if(!data?.can_sign_out)setConfirmSignOut(false);},[data?.can_sign_out]);
 async function action(operation:string,args:Record<string,unknown>={}){
  if(inFlight.current)return;
  inFlight.current=true;
  setPendingOperation(operation);setFailed(false);
  try{await local(operation,args);return await account.refetch();}
  catch{setFailed(true);await account.refetch();}
  finally{inFlight.current=false;setPendingOperation(null);}
 }
 function sendCode(event:FormEvent){event.preventDefault();if(!data?.can_start||!email.trim()||inFlight.current)return;setSentTo(email.trim());setWrongCode(false);void action('dervo_account_send_code',{email:email.trim()});}
 async function verifyCode(event:FormEvent){
  event.preventDefault();if(!verifyingCode||!data?.operation_id||!/^[0-9]{6,10}$/.test(code)||inFlight.current)return;
  const submitted=code;setCode('');setWrongCode(false);
  const result=await action('dervo_account_verify_code',{operationId:data.operation_id,code:submitted});
  if(result?.data?.state==='awaiting_user'&&result.data.can_verify){setWrongCode(true);requestAnimationFrame(()=>codeField.current?.focus());}
 }
 async function saveName(next:string):Promise<string|void>{
  if(!data?.can_save_profile||data.state!=='signed_in'||inFlight.current)return t('Wait for your account to finish checking, then try again.');
  const submitted=next.trim();
  if(!submitted)return t('Your name can’t be empty.');
  const result=await action('dervo_account_save_profile',{displayName:submitted});
  if(!result?.isSuccess||result.data.state!=='signed_in'||result.data.identity?.display_name!==submitted)return t('Dervo couldn’t confirm your name. Check your account, then try again.');
  markSaved();
 }
 const checkStatus=()=>{setFailed(false);void account.refetch();};
 const problem=(failed||account.isError)&&<div className="dv-set" role="alert"><div><p className="dv-set-line">{account.isError&&!data?t('Dervo couldn’t load your account. Your projects are still here.'):t('Dervo couldn’t confirm the account update. Check the status before trying again.')}</p></div>
  <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={checkStatus}>{account.isError&&!data?t('Try again'):t('Check status')}</button></div></div>;
 if(!data)return <>{problem||<p className="dv-sec-note" role="status">{t('Checking your account…')}</p>}</>;
 const signedIn=data.state==='signed_in'&&!!data.identity&&!account.isError;
 const beta=signedIn&&emailCode?betaAccess(data):null;
 const signingIn=!signedIn&&(verifyingCode||(emailCode&&data.can_start)||(!emailCode&&data.can_start));
 return <>
  {problem}
  {signedIn&&emailCode&&<SettingsSection title={t('Profile')} id="dv-account-profile">
   <ProfilePhoto data={data} busy={busy} onAction={action}/>
   <SettingRow name={t('Name')} line={t('Shown in the sidebar and on threads you start.')} end={<>{saved&&<span className="dv-saved" role="status">{t('Saved')}</span>}
    <TextField label={t('Your name')} value={data.identity!.display_name} disabled={busy||!data.can_save_profile} onCommit={saveName}/></>}/>
  </SettingsSection>}
  {signedIn&&emailCode&&data.plan&&(data.plan.plan!=='beta'||data.can_manage_billing)&&<DervoPlanSection data={data} busy={busy} onAction={action}/>}
  {beta&&(!data.plan||data.plan.plan==='beta')&&<SettingsSection title={t('Beta access')} id="dv-account-beta">
   <SettingRow name={t('Private beta')} line={beta.line} end={beta.check
    ?<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||account.isFetching} onClick={checkStatus}>{account.isFetching?t('Checking…'):t('Check again')}</button>
    :<StatusWord state={beta.state}>{beta.word}</StatusWord>}/>
  </SettingsSection>}
  {signingIn?<SettingsSection title={t('Sign in to Dervo')} id="dv-account-sign-in">
   <p className="dv-sec-note">{t('Your Dervo account holds your beta access. New here? The same steps create one.')}</p>
   {verifyingCode?<OpenCard title={t('Enter the code we emailed you')} onEscape={()=>{if(data.can_cancel&&data.operation_id){setCode('');void action('dervo_account_cancel',{operationId:data.operation_id});}}}>
     <p className="dv-card-text">{sentTo?tr('We sent it to {email}. It’s only used to confirm this sign-in.',{email:<b>{sentTo}</b>}):t('It’s only used to confirm this sign-in.')}</p>
     <form className="dv-inline" onSubmit={event=>void verifyCode(event)}>
      <input ref={codeField} className="dv-input dv-code" aria-label={t('Email code')} type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6,10}" maxLength={10}
       value={code} disabled={busy} aria-invalid={wrongCode||undefined} onChange={event=>{setCode(event.target.value);setWrongCode(false);}}/>
      <button type="submit" className="dv-btn dv-btn-primary" disabled={busy||!/^[0-9]{6,10}$/.test(code)}>{pendingOperation==='dervo_account_verify_code'?t('Checking code…'):t('Confirm code')}</button>
     </form>
     {wrongCode&&<p className="dv-field-note" role="alert">{t('That code didn’t work. Check the latest email and try again.')}</p>}
     <div className="dv-card-actions"><span className="dv-caption">{t('Not in your inbox after a minute? Check spam first.')}</span>
      {data.can_cancel&&data.operation_id&&<button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>{setCode('');setEmail(sentTo);void action('dervo_account_cancel',{operationId:data.operation_id});}}>{t('Use a different email')}</button>}</div>
    </OpenCard>
   :emailCode?<form className="dv-set" onSubmit={sendCode}>
     <div><p className="dv-set-name"><label htmlFor="dervo-account-email">{t('Email')}</label></p><p className="dv-set-line">{t('We’ll email you a code. It’s only used to confirm this sign-in.')}</p></div>
     <div className="dv-set-end"><input id="dervo-account-email" className="dv-input" type="email" autoComplete="email" required maxLength={254} value={email} disabled={busy}
      onChange={event=>setEmail(event.target.value)} placeholder="you@example.com"/>
      <button type="submit" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||!email.trim()}>{pendingOperation==='dervo_account_send_code'?t('Sending code…'):t('Email me a code')}</button></div>
    </form>
   :<div className="dv-set"><div><p className="dv-set-name">{labels[data.state]}</p><p className="dv-set-line">{data.detail}</p></div>
     <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>void action('dervo_account_start',{mode:'sign_up'})}>{t('Create an account')}</button>
      <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy} onClick={()=>void action('dervo_account_start',{mode:'sign_in'})}>{t('Sign in')}</button></div></div>}
  </SettingsSection>
  :<SettingsSection title={t('Session')} id="dv-account-session">
   {confirmSignOut&&data.can_sign_out
    ?<Confirm title={t('Sign out of Dervo?')} line={t('Your projects, threads and connected accounts stay on this Mac. You’ll need a new email code to sign back in.')}
      keep={t('Stay signed in')} act={t('Sign out')} busy={pendingOperation==='dervo_account_sign_out'} busyLabel={t('Signing out…')}
      onKeep={()=>setConfirmSignOut(false)} onAct={()=>{void action('dervo_account_sign_out').then(()=>setConfirmSignOut(false));}}/>
    :<SettingRow name={signedIn?(data.identity!.display_name?t('Signed in as {display_name}',{display_name:data.identity!.display_name}):t('Signed in')):labels[data.state]}
      line={signedIn?t('Signing out removes only your Dervo session.'):data.state==='unavailable'?t('This copy of Dervo doesn’t use a Dervo sign-in. Your projects and threads stay on this Mac.'):data.detail}
      end={<>{data.can_cancel&&data.operation_id&&<button type="button" className="dv-btn dv-btn-quiet" disabled={busy} onClick={()=>{setCode('');void action('dervo_account_cancel',{operationId:data.operation_id});}}>{t('Cancel sign-in')}</button>}
       {data.can_restore&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void action('dervo_account_restore')}>{pendingOperation==='dervo_account_restore'?t('Checking…'):t('Check session')}</button>}
       {data.can_sign_out&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>setConfirmSignOut(true)}>{t('Sign out')}</button>}</>}/>}
  </SettingsSection>}
 </>;
}
