import {useEffect,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Button} from '@/shared/ui/button';
import {Input} from '@/shared/ui/input';
import {local} from './api';
import {t,tr} from '@/i18n';

type Check = {state:string;canSend:boolean;detail:string};
type Instructions = Check & {command:string;expectedIdentity:string;provider:string};
export type Login = {state:string;phase:string|null;url:string|null;needsCode:boolean;detail:string;active:boolean;identity?:Check;expectedIdentity?:string};

/** One shared cache entry per account: the preflight refreshes it and the account list reads the same value. */
export const connectionStatusQuery=(profileId:string)=>({queryKey:['connection-status',profileId] as const,queryFn:()=>local<Check>('connection_status',{profileId})});
/** Identity states only. None of these means model, effort or runtime permissions were verified. */
const IDENTITY_LABELS:Record<string,string>={identity_verified:t('identity verified'),identity_mismatch:t('identity mismatch'),signed_out:t('signed out'),checking:t('checking identity'),unknown:t('identity unknown'),config_blocked:t('configuration needs review')};
export const identityLabel=(state?:string)=>IDENTITY_LABELS[state??'']??'unverified';
/** Error text shown to the user never repeats a pasted sign-in code, whatever the message contained. */
export function describeError(error:unknown,secret=''):string {
  const text=String(error),code=secret.trim();
  return code?text.split(code).join('[code]'):text;
}

/** Account metadata doesn't grant permission to send queued conversations. */
export function ConnectionPreflight(props:{profileId:string;provider?:string;method?:string}) {
  return <PreflightAccount key={`${props.provider}:${props.profileId}`} {...props}/>;
}
function PreflightAccount({profileId,provider,method}:{profileId:string;provider?:string;method?:string}) {
  // An API-key account has no sign-in: Check identity confirms the provider is using the key; nothing else applies.
  const keyed=method==='api_key';
  const [steps,setSteps]=useState<Instructions|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [starting,setStarting]=useState(false);
  const [code,setCode]=useState('');
  const status=useQuery({...connectionStatusQuery(profileId),
    refetchInterval:query=>query.state.data?.state==='checking'?500:false});
  const login=useQuery({queryKey:['connection-login',profileId],enabled:!keyed&&(provider==='claude'||provider==='codex'),
    queryFn:()=>local<Login>('connection_login_status',{profileId}),
    refetchInterval:query=>{const data=query.state.data;return data?.active||(data?.identity?.state==='checking'&&(status.isError||status.data?.state!=='identity_verified'))?700:false;}});
  const loginState=login.data?.state,identityState=login.data?.identity?.state;
  useEffect(()=>{if(loginState==='failed'||loginState==='cancelled'||loginState==='timed_out'||(loginState==='succeeded'&&identityState&&identityState!=='checking'))void status.refetch();},[loginState,identityState]);
  const active=!!login.data?.active,needsCode=!!login.data?.needsCode;
  const verified=!status.isError&&!login.isError&&!active&&status.data?.state==='identity_verified';
  const showLoginProgress=login.data&&loginState!=='idle'&&loginState!=='succeeded'&&(!verified||active);
  // A pasted code lives only while the running sign-in is asking for it. Cancel, failure, success or a
  // no-longer-asking CLI clears it; a profile switch remounts this component, so nothing carries over.
  useEffect(()=>{if(!active||!needsCode)setCode('');},[active,needsCode]);
  async function run(fn:()=>Promise<unknown>,secret='') {
    setBusy(true);setError('');
    try {await fn();}
    catch(e){setError(provider==='codex'?t('Codex connection request failed. Retry Connect Codex or Check identity.'):describeError(e,secret));}
    finally{setBusy(false);}
  }
  const check=()=>run(async()=>{
    await local<Check>('connection_check',{profileId});
    await status.refetch();
    if(!keyed&&(provider==='claude'||provider==='codex'))await login.refetch();
  });
  const showSteps=()=>run(async()=>setSteps(await local<Instructions>('connection_instructions',{profileId})));
  const connect=()=>run(async()=>{
    setStarting(true);
    try {await local<Login>('connection_login',{profileId});await login.refetch();await status.refetch();}
    finally {setStarting(false);}
  });
  const cancelLogin=()=>run(async()=>{setCode('');await local<Login>('connection_login_cancel',{profileId});await login.refetch();await status.refetch();});
  const sendCode=()=>run(async()=>{const pasted=code;setCode('');await local<Login>('connection_login_code',{profileId,code:pasted});await login.refetch();},code);
  return <section className="space-y-3 text-sm leading-relaxed" aria-label={t('Selected account connection')}>
    <div className="flex flex-wrap gap-2">
      {!keyed&&(provider==='codex'||provider==='claude')&&<Button variant={verified?'outline':'default'} disabled={busy||active} onClick={()=>void connect()}>{verified?t('Sign in again'):provider==='claude'?t('Connect Claude'):t('Connect Codex')}</Button>}
      {active&&<Button variant="outline" disabled={busy} onClick={()=>void cancelLogin()}>{t('Cancel sign-in')}</Button>}
      <Button variant="outline" disabled={busy||active||status.data?.state==='checking'} onClick={()=>void check()}>{t('Check identity')}</Button>
      {!keyed&&<Button variant="outline" onClick={()=>void showSteps()}>{provider==='claude'?t('Manual sign-in steps'):t('Official sign-in steps')}</Button>}
    </div>
    {provider==='codex'&&starting&&<p role="status">{t('Starting official Codex sign-in. Identity is unverified.')}</p>}
    {!keyed&&provider==='codex'&&login.data?.expectedIdentity&&<p>{tr('Expected personal account: {identity}',{identity:<strong>{login.data.expectedIdentity}</strong>})}</p>}
    {showLoginProgress&&login.data&&<div className="space-y-2" aria-label={provider==='codex'?t('Codex sign-in progress'):t('Claude sign-in progress')}>
      <p role="status">{login.data.detail}</p>
      {provider==='claude'&&login.data.url&&active&&<textarea aria-label={t('Official sign-in link')} readOnly value={login.data.url} rows={2} className="w-full resize-y rounded-md border bg-muted p-2 font-mono text-xs" onFocus={e=>e.currentTarget.select()}/>}
      {provider==='claude'&&needsCode&&active&&<form className="flex gap-2" onSubmit={e=>{e.preventDefault();void sendCode();}}><Input aria-label={t('Sign-in code')} placeholder={t('Code from the browser')} value={code} onChange={e=>setCode(e.target.value)}/><Button type="submit" disabled={busy||!code.trim()}>{t('Submit code')}</Button></form>}
    </div>}
    <p role="status">{status.error||login.error?t('Identity is unknown. Retry Check identity. No messages were sent.'):status.isPending?t('Loading saved connection status…'):status.data?.detail??t('Identity not checked. No messages will be sent.')}</p>
    {(error||status.error||login.error)&&<p role="alert" className="text-destructive">{error||(provider==='codex'?t('Codex connection status is unknown. Retry Check identity.'):describeError(status.error??login.error,code))}</p>}
    {steps&&<div className="space-y-2">
      <p>{tr('Expected personal account: {identity}',{identity:<strong>{steps.expectedIdentity}</strong>})}</p>
      <p>{steps.detail}</p>
      <textarea aria-label={t('Official sign-in command')} readOnly value={steps.command} rows={5} className="w-full resize-y rounded-md border bg-muted p-2 font-mono text-xs" onFocus={e=>e.currentTarget.select()}/>
      {steps.provider==='claude'&&<p>{t('Use these steps if the sign-in button doesn\'t open your browser. They connect the same Claude Code account for project reading and coding.')}</p>}
    </div>}
  </section>;
}
