import {useState} from 'react';
import {local,type ActiveRun} from './api';
import {StatusGlyph} from './StatusGlyph';
import './Surfaces.css';
import {t} from '@/i18n';

/** Why a coding request couldn't start, in the host's words, as a notice above the composer (surfaces/run-busy.html):
 * this thread already runs one, three are running across the workspace, or another thread holds the run. Your message
 * stays in the composer. Open and Stop act on the named run's own ids; Stop sends task_stop with the holder's project and
 * channel, because the host enforces task scope on every read. Once it's stopped the notice says so and waits for you. */
export function ActiveRunNotice({active,message,onOpen,onDismiss,refresh}:{active:ActiveRun;message?:string;onOpen:(projectId:string,channelId:string)=>void;onDismiss:()=>void;refresh:()=>Promise<unknown>}){
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[stopped,setStopped]=useState(false);
  const tag=active.name?`#${active.name}`:t('another conversation');
  const text=message||t('Another coding request is active in {tag}. Wait for it to finish or stop it first.',{tag});
  // The host names the thread as #name; the notice names it in bold, as the sidebar does.
  const at=active.name?text.indexOf(tag):-1;
  async function stop(){
    if(busy)return;
    setBusy(true);setError('');
    try{
      await local('task_stop',{projectId:active.project_id,channelId:active.thread_id,taskId:active.task_id});
      await refresh();
      setStopped(true);
    }catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }
  return <div role="status" data-testid="active-run-notice" className="dv-notice">
    <StatusGlyph state={stopped?'stopped':'running'}/>
    <span className="dv-what">{stopped?t('Paused. Send your message when you’re ready.')
      :<>{at<0?text:<>{text.slice(0,at)}<b>{active.name}</b>{text.slice(at+tag.length)}</>} {t('Your message is kept.')}</>}
      {error&&<><br/><span role="alert">{error}</span></>}</span>
    <span className="dv-acts">
      {active.name!==null&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>onOpen(active.project_id,active.thread_id)}>{t('Open {name}',{name:active.name})}</button>}
      {!stopped&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy} onClick={()=>void stop()}>{busy?t('Pausing…'):t('Pause it')}</button>}
      <button type="button" className="dv-btn dv-btn-quiet" onClick={onDismiss}>{t('Dismiss')}</button>
    </span>
  </div>;
}
