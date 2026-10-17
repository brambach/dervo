import {useState,type ReactNode,type SelectHTMLAttributes} from 'react';
import {ChevronDown,Eye,Shield,ShieldAlert,ShieldCheck,Sparkles} from 'lucide-react';
import {setConversationRoute,type ConversationRoute} from './api';
import {providerAuthor} from './providerLabel';
import type {Interruption} from './interruptions';
import './ComposerTools.css';
import {t} from '@/i18n';

/** Who works on this conversation's next request. The account and model show as a button that opens the account and model
 * picker; beside it, Auto or staying on this account. */
export function AccountTool({projectId,channelId,route,provider,model,method,onChangeAccount,onChanged}:{projectId:string;channelId:string;route:ConversationRoute|undefined;provider:string;model:string;method?:string;onChangeAccount:(trigger:HTMLElement)=>void;onChanged:()=>Promise<unknown>}){
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const here=`${providerAuthor(provider)}${method==='api_key'?` ${t('API key')}`:''}`;
  const mode=route?.mode??'fixed';
  const explain=mode==='auto'
    ?route?.alternative?t('Auto uses {here}. When it’s low or fails, Dervo suggests {label}, or moves there if this project switches by itself.',{here,label:route.alternative.label}):t('Auto uses {here}. Add an account on the other provider so Auto has somewhere to move.',{here})
    :t('Every request here runs on {here} · {model}.',{here,model});
  async function choose(next:'auto'|'fixed'){
    if(busy||next===mode)return;
    setBusy(true);setError('');
    try{await setConversationRoute(projectId,channelId,next);await onChanged();}
    catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
    finally{setBusy(false);}
  }
  return <div className="dervo-composer-tool" data-testid="account-tool">
    <button type="button" className="dervo-composer-tool-account" aria-label={t('Account and model: {here} · {model}',{here,model})} title={t('Change this conversation’s account or model')} onClick={event=>onChangeAccount(event.currentTarget)}>
      {here} · {model}<ChevronDown size={12} aria-hidden="true"/>
    </button>
    {mode==='auto'&&<Sparkles size={13} aria-hidden="true"/>}
    <label className="sr-only" htmlFor={`dervo-route-${channelId}`}>{t('When this account is low or fails')}</label>
    <FitSelect shown={mode==='auto'?t('Auto'):t('Stay on it')} id={`dervo-route-${channelId}`} title={explain} disabled={busy||!route} value={mode} onChange={event=>void choose(event.target.value as 'auto'|'fixed')}>
      <option value="auto">{t('Auto')}</option>
      <option value="fixed">{t('Stay on it')}</option>
    </FitSelect>
    <span className="sr-only" role="note">{explain}</span>
    {error&&<p role="alert" className="dervo-composer-tool-error">{error}</p>}
  </div>;
}

/** A native select shown as its chosen option's text, so it's only as wide as that text. ``shown`` is that text. */
export function FitSelect({shown,...props}:{shown:string}&SelectHTMLAttributes<HTMLSelectElement>){
  return <span className="dervo-fit-select"><span aria-hidden="true">{shown}</span><select {...props}/></span>;
}

/** The composer foot's left side, inside the pill before attach and send (thread-review.html): only what you choose before
 * sending. The model chip, the access icon, and the thread folder chip while it can still be flipped. Bryce, 8 Oct 2026:
 * "this bottom bar feels horrible. only keep what's actually needed". Seven items had to share one row at 1052, so every
 * label was cut off. What's only said, not chosen (a new session, the queue), is ComposerNotes, above the box. */
export function ComposerTools({children}:{children:ReactNode}){
  return <div className="dv-composer-tools" data-testid="composer-tools">{children}</div>;
}

/** Above the composer, beside the thread's status: the word on continuity when the next message starts a new session,
 * and the queue count while a task runs. With nothing queued there's no note: the placeholder already says a message
 * adds to the running task. After a message sent mid-run, ``interruption`` says which way it went: it joined the run,
 * the agent read it, or it waits in the queue (interruptions.ts). Nothing here when there's nothing to say. */
export function ComposerNotes({children,busy,queued,interruption}:{children?:ReactNode;busy:boolean;queued:number;interruption?:Interruption|null}){
  const hint=busy&&(interruption
    ?<span className="dv-hint" role="status" data-interruption={interruption.kind}>{interruption.text}</span>
    :queued>0&&<span className="dv-hint" role="status">{t('{count} queued',{count:queued})}</span>);
  return <div className="dv-composer-notes" data-testid="composer-notes">{children}{hint}</div>;
}

export type AccessState='full'|'stale'|'edit'|'read';

/** The thread's access as one icon that opens Access for the thread. The word is shown only when it needs you
 * ("Check access"); otherwise it's in the tooltip and the accessible name, so the row stays short. */
export function AccessTool({state,onOpen}:{state:AccessState;onOpen:(trigger:HTMLElement)=>void}){
  const word=state==='full'?t('Full access'):state==='stale'?t('Check access'):state==='edit'?t('Edit files'):t('Read only');
  const Icon=state==='full'?ShieldCheck:state==='stale'?ShieldAlert:state==='edit'?Shield:Eye;
  return <button type="button" className="dv-access-tool" data-testid="thread-access" data-state={state}
    aria-label={t('Access: {state}',{state:word})} title={t('{state}. Change this thread’s access',{state:word})} onClick={event=>onOpen(event.currentTarget)}>
    <Icon aria-hidden="true"/>{state==='stale'&&<span>{word}</span>}
  </button>;
}
