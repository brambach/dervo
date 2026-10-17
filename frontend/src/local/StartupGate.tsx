import {useEffect,useRef,useState,type ReactNode} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {useDervoBrand} from './DervoBrand';
import {BrandWindow,DetailBlock,OpeningScreen} from './BrandMoment';
import {formatDate,t,tr} from '@/i18n';
import {betaCorner} from '@/local/platformWords';

/** Before the workspace exists (design sweep, brand/startup-*): one opening screen only if it's slow, and a failure page
 * where the error leads, with Copy details. `src/startup.rs` reports these kinds: starting, ready, runtime, configuration,
 * data, host, error, selection and switching (the app is swapping itself for a newer version, which is not a failure).
 * Since beta.17 the app carries its own Python, so there's no install-Python page: a broken copy is the runtime kind. */
export type StartupStatus={ready:boolean;kind:string;detail:string};
/** Kinds that mean "still opening": the gate keeps the opening screen and asks again until they change. */
export const OPENING_KINDS=['starting','switching'];
const POLL_MS=1000;
const CORNER=betaCorner();
/** Where a tester writes when Dervo won't start, the address the /beta page gives for email help. Send feedback lives
 * inside the app, which hasn't started. */
export const SUPPORT_EMAIL='bryce@trydervo.com';

const LEDES:Record<string,string>={
  'already-open':t('Another copy of Dervo is using this workspace.'),
  host:t('The local workspace stopped while it was starting.'),
  data:t('Dervo couldn’t use the folder where it keeps your workspace.'),
  configuration:t('Dervo was pointed at a Python it couldn’t start.'),
  runtime:t('This copy of Dervo is missing part of itself. Download Dervo again and replace this copy.'),
  selection:t('The Dervo version you chose to open couldn’t start.'),
};
const clock=(ms:number)=>formatDate(ms,{hour:'numeric',minute:'2-digit'});

async function appVersion():Promise<string|null>{
  try{const {getVersion}=await import('@tauri-apps/api/app');return await getVersion();}catch{return null;}
}

export function StartupGate({children}:{children:ReactNode}){
  useDervoBrand();
  const [status,setStatus]=useState<StartupStatus>();
  const [failure,setFailure]=useState('');
  const [busy,setBusy]=useState(false),[checkedAt,setCheckedAt]=useState<number|null>(null);
  const inFlight=useRef(false);
  useEffect(()=>{
    let alive=true;
    invoke<StartupStatus>('startup_status').then(value=>{if(alive)setStatus(value);}).catch(e=>{if(alive)setFailure(String(e));});
    return ()=>{alive=false;};
  },[]);
  // Still opening, or mid-switch to a newer version: keep the opening screen and ask again, never the failure page.
  const opening=!!status&&!status.ready&&OPENING_KINDS.includes(status.kind);
  useEffect(()=>{
    if(!opening)return;
    const timer=setTimeout(()=>{invoke<StartupStatus>('startup_status').then(setStatus).catch(e=>setFailure(String(e)));},POLL_MS);
    return ()=>clearTimeout(timer);
  },[opening,status]);
  async function retry(){
    if(inFlight.current)return;
    inFlight.current=true;setBusy(true);
    try{setStatus(await invoke<StartupStatus>('startup_retry'));setFailure('');}
    catch(e){setFailure(String(e));}
    finally{inFlight.current=false;setBusy(false);setCheckedAt(Date.now());}
  }
  if(status?.ready&&!failure)return children;
  if((!status&&!failure)||(opening&&!failure))return <OpeningScreen caption={status?.kind==='switching'?t('Opening the new version'):t('Opening your workspace')}/>;
  return <FailedPage kind={status?.kind??'error'} detail={failure||status?.detail||t('Dervo didn’t say why.')} busy={busy} checkedAt={checkedAt} onRetry={()=>void retry()}/>;
}


function FailedPage({kind,detail,busy,checkedAt,onRetry}:{kind:string;detail:string;busy:boolean;checkedAt:number|null;onRetry:()=>void}){
  const [copied,setCopied]=useState(false);
  const first=useRef<HTMLButtonElement>(null);
  useEffect(()=>{first.current?.focus();},[]);
  useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(false),2000);return ()=>clearTimeout(timer);},[copied]);
  const shown=checkedAt?`${detail}\n${t('Checked at {time}.',{time:clock(checkedAt)})}`:detail;
  async function copy(){
    const version=await appVersion();
    // i18n-ignore: the details go to Bryce, so their own words stay English
    const text=[`Dervo couldn’t open your workspace (${kind}).`,version?`Dervo ${version}`:null,checkedAt?`${detail}\nChecked at ${new Date(checkedAt).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}.`:detail].filter(Boolean).join('\n');
    try{await navigator.clipboard.writeText(text);setCopied(true);}catch{/* The details stay selectable below. */}
  }
  return <BrandWindow meadow={false} lockup corner={CORNER} label={t('Dervo setup')}>
    <div className="dv-moment"><section className="dv-moment-col" aria-label={t('Dervo setup')} aria-busy={busy}>
      <p className="dv-kicker">{t('Dervo didn’t start')}</p>
      <h1 className="dv-display-1">{kind==='already-open'?t('Dervo is already open'):t('Dervo couldn’t open your workspace')}</h1>
      <p className="dv-lede">{kind==='already-open'?t('Use the Dervo window that’s already open. To use this version, quit the other copy, then try again here. Your workspace hasn’t changed.'):t('{why} Your projects and conversations are still on this Mac. Try again, and if it happens twice, email the details below to Bryce.',{why:LEDES[kind]??t('Something stopped Dervo while it was starting.')})}</p>
      <div className="dv-moment-actions">
        <button ref={first} type="button" className="dv-btn dv-btn-primary" disabled={busy} onClick={onRetry}>{busy?t('Trying again…'):t('Try again')}</button>
        <button type="button" className="dv-btn dv-btn-ghost" onClick={()=>void copy()}>{copied?t('Copied'):t('Copy details')}</button>
        {/* Beside Copy details, as plain text to copy: there's no mail link from a window that didn't start. */}
        {kind!=='already-open'&&<span className="dv-caption" style={{alignSelf:'center',paddingLeft:'var(--dv-s2)',userSelect:'text'}}>{tr('Email {address}',{address:<b>{SUPPORT_EMAIL}</b>})}</span>}
      </div>
      <DetailBlock detail={shown} open/>
    </section></div>
  </BrandWindow>;
}
