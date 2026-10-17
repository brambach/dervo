import {useEffect, useState} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {local} from './api';
import {buildChanges, buildChangesRelease} from './buildChanges';
import {refreshUpdateNotice, updateLater, updateSeen, useUpdateNotice, type UpdateNotice} from './updateNotice';
import './UpdatePrompt.css';
import {t} from '@/i18n';

/** The Mac icon you picked (Settings > Preferences > App icon, src/app_icon.rs), for the card's mark. Cream when the native
 * app can't say, as in a browser preview. */
export const markIcon=(answer:unknown)=>{
  const icon=(answer as {icon?:unknown}|null)?.icon;
  return typeof icon==='string'&&/^[a-z]{1,20}$/.test(icon)?icon:'cream';
};

/** "beta.16" from "0.1.0-beta.16". */
export const shortVersion=(id:string)=>id.replace(/^\d+\.\d+\.\d+-/,'');
/** "148 MB", and "4.2 MB" under 10 MB, where the decimal still says something. */
export const mb=(bytes:number)=>{const value=bytes/1000000;return `${value<10?value.toFixed(1):Math.round(value)} MB`;};

/** What the prompt card says and offers, from the host's notice. Kept apart from the component so it's tested alone. */
export function promptView(notice:UpdateNotice):{kind:'updated'|'offer'|'downloading'|'unpacking'|'ready'|'failed'|'move'|'blocked'|'none';releaseId?:string}{
  if(notice.updated)return {kind:'updated',releaseId:notice.updated.releaseId};
  const prompt=notice.prompt, status=notice.status, problem=status?.installProblem;
  // Running from Downloads or a disk image (Harrison, 25 Sep): offer the move on open, before any update fails.
  if(!prompt||!status)return problem?.kind==='move'?{kind:'move'}:{kind:'none'};
  const releaseId=prompt.releaseId;
  // A newer build that couldn't replace this app where it runs: say how to fix that instead of offering Update now.
  if(problem&&status.state!=='downloading'&&status.state!=='unpacking')return {kind:'blocked',releaseId};
  if(status.state==='ready'&&status.update?.releaseId===releaseId)return {kind:'ready',releaseId};
  if(status.release?.release_id!==releaseId)return {kind:'offer',releaseId};
  if(status.state==='downloading')return {kind:'downloading',releaseId};
  if(status.state==='unpacking')return {kind:'unpacking',releaseId};
  if(status.state==='error')return {kind:'failed',releaseId};
  return {kind:'offer',releaseId};
}

/** The update prompt (beta.15, group 9). On open, when there's a newer build: its version, a few lines of what's new,
 * Update now and Later. Update now is Settings > Updates' Download update, then Update and restart, in place. A build
 * that only ships as an installer opens Settings > Updates instead. Later hides it until tomorrow or the next build.
 * After an update, the first open says which version you're on and what changed, once. Nothing installs by itself. */
export function UpdatePrompt({onOpenUpdates}:{onOpenUpdates:()=>void}){
  const notice=useUpdateNotice();
  const view=promptView(notice);
  const [sending,setSending]=useState(false),[error,setError]=useState('');
  // Not now hides the move card until Dervo next opens. The update prompt keeps its own Later.
  const [moveHidden,setMoveHidden]=useState(false);
  const shown=view.kind!=='none'&&!(view.kind==='move'&&moveHidden);
  const [mark,setMark]=useState('cream');
  useEffect(()=>{setError('');},[view.releaseId,view.kind]);
  useEffect(()=>{
    if(!shown)return;
    let disposed=false;
    invoke('app_icon_get').then(answer=>{if(!disposed)setMark(markIcon(answer));}).catch(()=>{});
    return ()=>{disposed=true;};
  },[shown]);
  if(!shown)return null;
  const releaseId=view.releaseId??'';
  const name=shortVersion(releaseId);
  async function act(step:()=>Promise<unknown>){
    if(sending)return;
    setSending(true);setError('');
    try{await step();await refreshUpdateNotice();}
    catch(failure){setError(String(failure).replace(/^Error: /,'')||t('Dervo couldn’t do that. Open Updates in Settings to try again.'));}
    finally{setSending(false);}
  }
  const later=()=>void act(()=>view.kind==='updated'?updateSeen(releaseId):updateLater(releaseId));
  function updateNow(){
    const status=notice.status;
    if(status?.canFetch)return void act(()=>local('beta_update_fetch',{context:status.context,releaseId}));
    // A disk image downloads through the native folder picker on Settings > Updates.
    onOpenUpdates();
  }
  const restart=()=>void act(()=>invoke('install_update'));
  const status=notice.status;
  let title:string, body, actions;
  if(view.kind==='move'){
    title=t('Move Dervo to Applications');
    body=<p className="dv-card-text">{t('Dervo is running from where it was downloaded, so it can’t update itself. Quit Dervo, drag Dervo Local.app into your Applications folder and open it from there.')}</p>;
    actions=<button type="button" className="dv-btn dv-btn-primary" onClick={()=>setMoveHidden(true)}>{t('Not now')}</button>;
  }else if(view.kind==='blocked'){
    title=t('Dervo {name} is out',{name});
    body=<p className="dv-card-text" role="alert">{status!.installProblem!.line}</p>;
    actions=<button type="button" className="dv-btn dv-btn-primary" disabled={sending} onClick={later}>{t('Later')}</button>;
  }else if(view.kind==='updated'){
    // Without recorded notes, this build's own list, but only when it was written for this version.
    const lines=notice.updated!.notes.length?notice.updated!.notes:releaseId===buildChangesRelease?buildChanges.slice(0,3):[];
    title=t('You’re on {name}',{name});
    body=<>{lines.length>0&&<ul className="dv-up-notes">{lines.map((line,index)=><li key={index}>{line}</li>)}</ul>}</>;
    actions=<><button type="button" className="dv-btn dv-btn-quiet" disabled={sending} onClick={()=>void act(async()=>{await updateSeen(releaseId);onOpenUpdates();})}>{t('What’s new')}</button>
      <button type="button" className="dv-btn dv-btn-primary" disabled={sending} onClick={later}>OK</button></>;
  }else{
    const prompt=notice.prompt!;
    title=view.kind==='downloading'?t('Downloading {name}',{name}):view.kind==='unpacking'?t('Unpacking {name}',{name})
      :view.kind==='ready'?t('Dervo {name} is ready',{name}):view.kind==='failed'?t('Dervo {name} didn’t download',{name}):t('Dervo {name} is out',{name});
    const progress=view.kind==='downloading'&&status?.release?<div className="dv-up-progress">
      <progress aria-label={t('Update download')} max={status.release.bytes} value={status.received}/>
      <span className="dv-caption">{t('{done} of {size}',{done:mb(status.received),size:mb(status.release.bytes)})}</span></div>:null;
    body=<>
      {prompt.notes.length>0&&view.kind==='offer'&&<ul className="dv-up-notes">{prompt.notes.map((line,index)=><li key={index}>{line}</li>)}</ul>}
      {progress}
      {view.kind==='unpacking'&&<p className="dv-card-text" role="status">{t('Unpacking the new version…')}</p>}
      {view.kind==='ready'&&<p className="dv-card-text">{t('Dervo backs up your workspace, closes and opens {name}. The version you’re using is kept so you can go back. Finish or stop running threads first.',{name})}</p>}
      {view.kind==='failed'&&<p className="dv-card-text" role="alert">{status?.detail||t('Dervo couldn’t download this update.')}</p>}
    </>;
    actions=view.kind==='offer'?<><button type="button" className="dv-btn dv-btn-quiet" disabled={sending} onClick={later}>{t('Later')}</button>
        <button type="button" className="dv-btn dv-btn-primary" disabled={sending} onClick={updateNow}>{sending?t('Starting…'):t('Update now')}</button></>
      :view.kind==='ready'?<><button type="button" className="dv-btn dv-btn-quiet" disabled={sending} onClick={later}>{t('Later')}</button>
        <button type="button" className="dv-btn dv-btn-primary" disabled={sending} onClick={restart}>{sending?t('Updating…'):t('Restart to update')}</button></>
      :view.kind==='failed'?<><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={onOpenUpdates}>{t('Open Updates')}</button>
        <button type="button" className="dv-btn dv-btn-quiet" disabled={sending} onClick={later}>{t('Later')}</button>
        {status?.canFetch&&<button type="button" className="dv-btn dv-btn-primary" disabled={sending} onClick={updateNow}>{sending?t('Starting…'):t('Try again')}</button>}</>
      :<button type="button" className="dv-btn dv-btn-quiet" disabled={sending||!status?.busy} onClick={()=>void act(()=>local('beta_update_cancel',{context:status!.context}))}>{t('Cancel')}</button>;
  }
  const caption=view.kind==='offer'&&notice.prompt?.bytes?mb(notice.prompt.bytes):undefined;
  return <div className="dv-update-prompt" role="region" aria-labelledby="dv-update-title" data-kind={view.kind} data-testid="update-prompt"
    onKeyDown={event=>{if(event.key==='Escape'&&!event.defaultPrevented&&(view.kind==='offer'||view.kind==='updated')){event.preventDefault();later();}}}>
    <img className="dv-up-mark" src={`/app-icons/${mark}.png`} alt="" width={40} height={40}/>
    <div className="dv-up-body">
      <div className="dv-card-head"><h3 className="dv-title" id="dv-update-title" aria-live="polite">{title}</h3>{caption&&<span className="dv-caption">{caption}</span>}</div>
      {body}
      {error&&<p className="dv-field-note" role="alert">{error}</p>}
      <div className="dv-card-actions">{actions}</div>
    </div>
  </div>;
}
