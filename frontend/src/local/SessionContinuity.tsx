import {useState} from 'react';
import {RotateCcw,X} from 'lucide-react';
import {DropdownMenuItem} from '@/shared/ui/dropdown-menu';
import {local} from './api';
import {t} from '@/i18n';

export type ContinuityView={state:'first'|'continues'|'fresh';reason:string|null;fresh_chosen?:boolean;quiet?:boolean};

/** The host's reason (session_continuity.decide), cut to what the composer foot needs: its first clause, without a
 * repeat of "new session", and full access as a plain on or off. The whole reason stays in the tooltip. */
export function shortReason(reason:string):string{
 const access=/^Full access was turned (on|off)/.exec(reason);
 if(access)return access[1]==='on'?t('Full access on'):t('Full access off');
 return reason.split(/\. |, so /)[0].replace(/\.$/,'');
}

/** The composer's word on continuity, only when the next message won't keep this chat's context: the reason shows in
 * the composer foot before you send, while you can still decide, and a Start fresh you chose can be undone there.
 * While the chat continues, nothing shows here; Start fresh lives in the thread's ⋯ menu (ContinuityMenuItems). */
export function ContinuityTool({projectId,channelId,view,onChanged}:{projectId:string;channelId:string;view?:ContinuityView;onChanged:()=>Promise<unknown>}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 // The first follow-up to the onboarding overview starts fresh without a word (session_continuity.after_overview).
 if(!view||view.state!=='fresh'||view.quiet)return null;
 async function keep(){
  setBusy(true);setError('');
  try{await local('coding_fresh_start_set',{projectId,channelId,enabled:false});await onChanged();}
  catch(failure){setError(failure instanceof Error?failure.message:String(failure));}
  finally{setBusy(false);}
 }
 return <span className="dv-hint dv-continuity" data-testid="continuity" title={view.reason??undefined}>
  <span>{t('New session')}{view.reason&&!view.fresh_chosen?` · ${shortReason(view.reason)}`:''}</span>
  {view.fresh_chosen&&<button type="button" className="dv-continuity-undo" aria-label={t('Undo start fresh')} title={t('Keep this chat’s context')} disabled={busy} onClick={()=>void keep()}><X size={12} aria-hidden="true"/></button>}
  {error&&<span role="alert" className="dv-continuity-error">{error}</span>}
 </span>;
}

/** Start fresh, in the thread's ⋯ menu: the next message sends a short summary instead of continuing the session.
 * Once chosen it reads as the undo. The first message has nothing to continue, so there is no item. */
export function ContinuityMenuItems({projectId,channelId,view,onChanged,onError}:{projectId:string;channelId:string;view?:ContinuityView;onChanged:()=>Promise<unknown>;onError:(message:string)=>void}){
 if(!view||view.state==='first')return null;
 async function set(enabled:boolean){
  try{await local('coding_fresh_start_set',{projectId,channelId,enabled});await onChanged();}
  catch(failure){onError(failure instanceof Error?failure.message:String(failure));}
 }
 // Two lines in the thread menu (surfaces/thread-menu.html): what it does is the second line.
 if(view.state==='continues')return <DropdownMenuItem className="dv-mi-two" title={t('The next message continues this chat’s session, so the agent keeps everything it read and ran. Start fresh sends a short summary instead.')} onSelect={()=>void set(true)}>
  <RotateCcw size={16} strokeWidth={1.5}/><span className="dv-mi-t"><span>{t('Start fresh')}</span><small>{t('Your next message starts a new session')}</small></span></DropdownMenuItem>;
 if(view.fresh_chosen)return <DropdownMenuItem className="dv-mi-two" onSelect={()=>void set(false)}>
  <RotateCcw size={16} strokeWidth={1.5}/><span className="dv-mi-t"><span>{t('Keep this chat’s context')}</span><small>{t('Your next message carries on this session')}</small></span></DropdownMenuItem>;
 return null;
}
