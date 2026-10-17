import {useEffect,useRef,useState,type ReactNode} from 'react';
import {useQuery} from '@tanstack/react-query';
import {local} from './api';
import {describeError,type Login} from './ConnectionPreflight';
import {StatusGlyph,type GlyphState} from './StatusGlyph';
import {t,tr} from '@/i18n';
import './AccountSignIn.css';

/** Adding a Claude or Codex account by signing in first (beta.18; Bryce, 7 Oct 2026: testers fell off when the email they
 * typed didn't match the one they signed in with). The provider's own sign-in starts at once, in a folder that isn't an
 * account yet; when it finishes the host reads who signed in from the provider (account_signin.py) and this card says so:
 * "Signed in as you@x.com, Claude Max plan", with Add and Use a different account. Nothing is an account, and nothing can
 * run on it, until Add. The same card says when that account is already here (Use it), when it's your account on the
 * other side (refused by name) and when its plan can't be used on this side. Used by Settings' Add card, first run and
 * the work account in onboarding. */

export type Found={state:'checking'|'signed_in'|'existing'|'other_side'|'refused'|'signed_out'|'unknown';canSend:false;detail:string;
  signedInAs?:string;plan?:string;profileId?:string;name?:string;otherSide?:'personal'|'work'};
type SignIn=Login&{identity?:Found};
/** What Add did, with the plan the sign-in reported (the host keeps it too, provider_plans). */
export type Added={id:string;state:'added'|'existing';name?:string;identity?:string;plan?:string};

const TOOL:Record<'claude'|'codex',{cli:string;label:string;site:string}>={
  claude:{cli:'Claude Code',label:'Claude',site:'claude.ai'},
  codex:{cli:'Codex',label:'ChatGPT',site:'chatgpt.com'}};

// i18n-ignore: the providers' own plan names
const PLAN_NAMES:Record<string,string>={pro:'Pro',prolite:'Pro Lite',max:'Max',team:'Team',plus:'Plus',business:'Business',enterprise:'Enterprise',edu:'Edu'};
/** "Team plan", or nothing when the provider hasn't said. */
export const planWords=(plan?:string|null)=>plan?t('{plan} plan',{plan:PLAN_NAMES[plan]??plan.charAt(0).toUpperCase()+plan.slice(1)}):'';
/** "Claude Max plan", or nothing when the provider didn't say. */
export function planLine(provider:'claude'|'codex',plan?:string|null){
  const words=planWords(plan);
  return words?`${TOOL[provider].label} ${words}`:'';
}

export function SignInToAdd({provider,category,onAdded,onCancel}:{provider:'claude'|'codex';category:'personal'|'work';
  onAdded:(added:Added)=>Promise<unknown>|void;onCancel:()=>void}){
  const tool=TOOL[provider];
  const [pending,setPending]=useState<string|null>(null),[busy,setBusy]=useState('start'),[error,setError]=useState('');
  const [code,setCode]=useState(''),[sent,setSent]=useState(false),[copied,setCopied]=useState(false),[again,setAgain]=useState(false),[round,setRound]=useState(0);
  const live=useRef<string|null>(null),codeField=useRef<HTMLInputElement>(null),addButton=useRef<HTMLButtonElement>(null);
  const login=useQuery({queryKey:['account-signin',pending??'',round],enabled:!!pending,queryFn:()=>local<SignIn>('connection_login_status',{profileId:pending!}),
    refetchInterval:query=>{const data=query.state.data;return data?.active||(data?.state==='succeeded'&&(!data.identity||data.identity.state==='checking'))?700:false;}});
  async function run(key:string,work:()=>Promise<unknown>,secret=''){
    setBusy(key);setError('');
    try{await work();}catch(failure){setError(describeError(failure instanceof Error?failure.message:failure,secret));}finally{setBusy('');}
  }
  async function start(){
    await run('start',async()=>{
      const started=await local<SignIn&{pendingId:string}>('account_signin_start',{provider,category});
      live.current=started.pendingId;setSent(false);setCode('');setRound(n=>n+1);setPending(started.pendingId);
    });
  }
  async function drop(){
    const id=live.current;live.current=null;
    if(id)await local('account_signin_cancel',{pendingId:id}).catch(()=>{});
  }
  // One click on "Sign in with …" got here, so the sign-in starts now. Leaving the card, by any way, ends a sign-in that
  // never became an account, and the host removes its folder.
  const begun=useRef(false),leaving=useRef(0);
  useEffect(()=>{window.clearTimeout(leaving.current);if(!begun.current){begun.current=true;void start();}
    return ()=>{leaving.current=window.setTimeout(()=>void drop(),0);};},[]);
  const data=login.data,found=data?.identity;
  useEffect(()=>{if(data?.needsCode)codeField.current?.focus();},[data?.needsCode]);
  useEffect(()=>{if(found?.state==='signed_in'||found?.state==='existing')requestAnimationFrame(()=>addButton.current?.focus());},[found?.state]);
  // The card grows as the steps answer; keep its end, with the buttons, in view.
  const box=useRef<HTMLDivElement>(null);
  useEffect(()=>{box.current?.scrollIntoView?.({block:'nearest'});},[found?.state,data?.needsCode,data?.state]);
  function different(){void run('start',async()=>{setAgain(true);await drop();setPending(null);
    const started=await local<SignIn&{pendingId:string}>('account_signin_start',{provider,category});
    live.current=started.pendingId;setSent(false);setCode('');setRound(n=>n+1);setPending(started.pendingId);});}
  function add(){void run('add',async()=>{
    const added=await local<Added>('account_signin_add',{pendingId:pending!});
    live.current=null;await onAdded({...added,plan:added.plan??found?.plan});
  });}
  function cancel(){void drop().then(onCancel);}

  const starting=busy==='start'||!pending||!data;
  const active=starting||!!data?.active,state=starting?'starting':data!.state;
  const ended=!active&&state!=='succeeded';
  const checking=state==='succeeded'&&(!found||found.state==='checking');
  const done=state==='succeeded'&&!!found&&found.state!=='checking';
  const ok=found?.state==='signed_in'||found?.state==='existing';
  const plan=planLine(provider,found?.plan);
  const step=(glyph:GlyphState,text:ReactNode,small?:ReactNode,now?:boolean,bad?:boolean)=><div className="dv-step" data-now={now||undefined} data-bad={bad||undefined}>
    <StatusGlyph state={glyph} accent={bad}/><div>{text}{small&&<small>{small}</small>}</div></div>;
  const result=!done?step(checking?'running':'idle',t('Dervo reads which account signed in'),t('You confirm it before anything is added'),checking)
    :found!.state==='signed_in'?step('done',tr('Signed in as {email}',{email:<b>{found!.signedInAs}</b>}),[plan,category==='work'?t('Work account'):t('Personal account')].filter(Boolean).join(' · '))
    :found!.state==='existing'?step('done',tr('{email} is already added',{email:<b>{found!.signedInAs}</b>}),t('It’s {name} in Connected accounts. Nothing new is added.',{name:found!.name??''}))
    :found!.state==='other_side'?step('failed',found!.otherSide==='work'?t('That’s your work account'):t('That’s your personal account'),
      found!.otherSide==='work'?t('{email} already runs your Work projects, so it can’t be a personal account too. Nothing was added.',{email:found!.signedInAs??''})
        :t('{email} already runs your Personal projects, so it can’t be a work account too. Nothing was added.',{email:found!.signedInAs??''}),false,true)
    :found!.state==='refused'?step('failed',found!.signedInAs?t('{email} can’t be added here',{email:found!.signedInAs}):t('That account can’t be added here'),found!.detail,false,true)
    :found!.state==='signed_out'?step('failed',t('{cli} says nobody signed in',{cli:tool.cli}),t('Nothing was added.'),false,true)
    :step('failed',t('Dervo couldn’t tell which account signed in'),t('Nothing was added.'),false,true);
  const hint=done&&!ok||again?t('Signing in as someone else? Sign out of {site} in your browser first, or the same account signs in again.',{site:tool.site}):null;

  return <div ref={box} className="dv-signin-add" aria-live="polite">
    <div className="dv-steps">
      {step('done',t('Started {cli}’s own sign-in',{cli:tool.cli}),category==='work'?t('In a folder of its own, apart from your personal sign-in'):t('Your existing {label} login isn’t read or changed',{label:tool.label}))}
      {ended?step('failed',state==='timed_out'?t('The sign-in timed out'):state==='cancelled'?t('Sign-in was cancelled'):t('Sign-in didn’t finish'),data?.detail||undefined,false,true)
        :state==='succeeded'?step('done',t('Signed in in your browser'),t('{label} sent the sign-in back',{label:tool.label}))
        :step('running',sent?t('Code sent. Waiting for confirmation'):t('Sign in with the {label} account you want to add',{label:tool.label}),t('{label}’s sign-in page opens in your browser',{label:tool.label}),true)}
      {result}
    </div>
    {provider==='claude'&&data?.needsCode&&active&&<div className="dv-field"><span>{t('Code from your browser')}</span>
      <form className="dv-inline" onSubmit={event=>{event.preventDefault();const pasted=code.trim();if(!pasted||!pending)return;setCode('');setSent(true);
        void run('code',async()=>{await local<Login>('connection_login_code',{profileId:pending,code:pasted});await login.refetch();},pasted);}}>
        <input ref={codeField} className="dv-input" aria-label={t('Sign-in code')} placeholder={t('Paste the code Claude shows')} value={code} autoComplete="off" spellCheck={false} onChange={event=>setCode(event.target.value)}/>
        <button type="submit" className="dv-btn dv-btn-primary" disabled={!!busy||!code.trim()}>{t('Submit code')}</button></form>
      {data.url&&<small>{tr('Page didn’t open? {copy}.',{copy:<button type="button" className="dv-link" onClick={()=>{void navigator.clipboard?.writeText(data.url!).then(()=>setCopied(true)).catch(()=>{});}}>{copied?t('Link copied'):t('Copy the sign-in link')}</button>})}</small>}</div>}
    {hint&&<p className="dv-field-note">{hint}</p>}
    {error&&<p className="dv-field-note" role="alert">{error}</p>}
    <div className="dv-card-actions"><span className="dv-caption">{t('Dervo never sees your password.')}</span>
      <button type="button" className="dv-btn dv-btn-quiet" disabled={busy==='add'} onClick={cancel}>{t('Cancel')}</button>
      {(ok||ended||(done&&!ok))&&<button type="button" className={`dv-btn ${ok?'dv-btn-quiet':'dv-btn-primary'}`} disabled={!!busy} onClick={different}>
        {ended?t('Try again'):t('Use a different account')}</button>}
      {ok&&<button ref={addButton} type="button" className="dv-btn dv-btn-primary" disabled={!!busy} onClick={add}>
        {busy==='add'?t('Adding…'):found!.state==='existing'?t('Use {name}',{name:found!.name??''}):t('Add this account')}</button>}
    </div>
  </div>;
}
