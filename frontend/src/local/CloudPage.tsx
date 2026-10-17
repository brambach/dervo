import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {local} from './api';
import {SettingRow,SettingsSection,TextField} from './DervoControls';
import {FieldError} from './SettingsBits';
import {checkedAgo,pollEvery,stateWord,taskTitle,type CloudPreview,type CloudRun,type CloudRunsView} from './cloudRuns';
import type {SettingsProject} from './projectSettingsData';
import {openUrl} from '@tauri-apps/plugin-opener';
import {t} from '@/i18n';
import './CloudPage.css';

const message=(failure:unknown)=>failure instanceof Error?failure.message:String(failure);
const runsKey=(projectId:string)=>['cloud-runs',projectId];

/** Send to cloud (backend/cloud_runs.py): a task that runs in Claude's or Codex's own cloud, on this project's account,
 * through that account's own CLI. The preview says what goes and where before anything is sent; the runs below show
 * only what the host could see, with Unknown where it can't. A result comes back as a local branch; nothing here checks
 * it out, merges or pushes. Visual layer is deliberately plain: the design lane owns how it finally looks. */
export function CloudPage({project}:{project:SettingsProject}){
  const client=useQueryClient();
  const runs=useQuery({queryKey:runsKey(project.id),queryFn:()=>local<CloudRunsView>('cloud_runs',{projectId:project.id}),
    refetchInterval:query=>pollEvery(query.state.data?.runs??[])});
  const [text,setText]=useState(''),[preview,setPreview]=useState<CloudPreview|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const refresh=()=>client.invalidateQueries({queryKey:runsKey(project.id)});
  async function look(){
    setBusy(true);setError('');
    try{setPreview(await local<CloudPreview>('cloud_preview',{projectId:project.id,conversationId:null,text}));}
    catch(failure){setError(message(failure));}
    finally{setBusy(false);}
  }
  async function send(){
    if(!preview?.previewId)return;
    setBusy(true);setError('');
    try{await local('cloud_send',{previewId:preview.previewId});setPreview(null);setText('');await refresh();}
    catch(failure){setError(message(failure));setPreview(null);}
    finally{setBusy(false);}
  }
  const environment=runs.data?.environment??'';
  const codex=runs.data?.provider==='codex'||!!runs.data?.environment;
  return <>
    <SettingsSection title={t('Send a task')} id="dv-cloud-send">
      <div className="dv-cloud-compose">
        <label className="dv-label" htmlFor="dv-cloud-task">{t('What should the cloud run do?')}</label>
        <textarea id="dv-cloud-task" className="dv-input dv-cloud-task" rows={4} value={text} disabled={busy||!!preview}
          onChange={event=>setText(event.target.value)} placeholder={t('Fix the flaky test in auth.spec.ts')}/>
        {!preview&&<div className="dv-cloud-actions">
          <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||!text.trim()} onClick={()=>void look()}>
            {busy?t('Checking…'):t('Review before sending')}</button>
        </div>}
        {preview&&<div className="dv-cloud-preview" role="group" aria-label={t('What goes to the cloud')}>
          <dl>
            <dt>{t('Runs in')}</dt><dd>{preview.where}</dd>
            <dt>{t('Account')}</dt><dd>{preview.account}</dd>
            <dt>{t('Starts from')}</dt><dd>{t('{branch} on {repository}',{branch:preview.branch,repository:preview.repository})}</dd>
            {preview.environment&&<><dt>{t('Environment')}</dt><dd><code>{preview.environment}</code></dd></>}
          </dl>
          {preview.notes.length>0&&<ul className="dv-cloud-notes">{preview.notes.map(note=><li key={note}>{note}</li>)}</ul>}
          {preview.detail&&<p className="dv-field-note" role="alert">{preview.detail}</p>}
          <div className="dv-cloud-actions">
            <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||!preview.canSend} onClick={()=>void send()}>
              {busy?t('Sending…'):t('Send to {where}',{where:preview.where})}</button>
            <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" disabled={busy} onClick={()=>setPreview(null)}>{t('Cancel')}</button>
          </div>
        </div>}
        {error&&<p className="dv-field-note" role="alert">{error}</p>}
      </div>
    </SettingsSection>
    {codex&&<SettingsSection title={t('Codex cloud')} id="dv-cloud-env">
      <SettingRow name={t('Environment')} line={t('Codex cloud runs in an environment you set up on chatgpt.com/codex. Paste its id here.')}
        end={<TextField label={t('Codex environment id')} value={environment} placeholder="env_…"
          onCommit={async next=>{await local('cloud_environment_set',{projectId:project.id,environment:next.trim()||null});await refresh();}}/>}/>
    </SettingsSection>}
    <SettingsSection title={t('Cloud runs')} id="dv-cloud-runs">
      {runs.isError&&<FieldError message={t('Dervo couldn’t read this project’s cloud runs.')} onRetry={()=>void runs.refetch()}/>}
      {runs.isPending&&<p className="dv-sec-note" role="status">{t('Loading…')}</p>}
      {runs.data&&runs.data.runs.length===0&&<p className="dv-field-note">{t('Nothing sent yet. Runs you send show here with what Dervo can see of them.')}</p>}
      <ul className="dv-cloud-runs">{runs.data?.runs.map(run=><CloudRunRow key={run.id} run={run} onChanged={refresh}/>)}</ul>
    </SettingsSection>
  </>;
}

function CloudRunRow({run,onChanged}:{run:CloudRun;onChanged:()=>Promise<unknown>}){
  const [error,setError]=useState(''),[followUp,setFollowUp]=useState<string|null>(null),[busy,setBusy]=useState(false);
  const [unopened,setUnopened]=useState(false);
  const picking=run.candidates.length>0&&!run.branch&&!run.broughtBack;
  async function act(operation:string,args:Record<string,unknown>){
    setBusy(true);setError('');
    try{await local(operation,{runId:run.id,...args});await onChanged();return true;}
    catch(failure){setError(message(failure));return false;}
    finally{setBusy(false);}
  }
  const checked=checkedAgo(run.lastCheckedAt,Date.now()/1000);
  return <li className="dv-cloud-run" data-state={run.state}>
    <div className="dv-cloud-run-head">
      <span className="dv-cloud-pill" data-state={run.broughtBack?'back':picking?'pick':run.state}>{stateWord(run)}</span>
      <b className="dv-cloud-run-title" title={run.task}>{taskTitle(run.task)}</b>
      <span className="dv-meta">{run.where}</span>
    </div>
    {run.line&&<p className="dv-cloud-run-line" aria-live="polite">{run.line}</p>}
    {run.summary&&<p className="dv-cloud-run-line dv-meta">{run.summary}</p>}
    <div className="dv-cloud-run-foot">
      {run.branch&&<code>{run.branch}</code>}
      {checked&&<span className="dv-meta">{run.checking?t('Checking…'):checked}</span>}
      {run.link&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" title={run.link}
        onClick={()=>{setUnopened(false);void openUrl(run.link!).catch(()=>setUnopened(true));}}>{t('Open')}</button>}
      {run.canCheck&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy||run.checking} onClick={()=>void act('cloud_check',{})}>{t('Check now')}</button>}
      {run.canBringBack&&run.candidates.length===0&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy}
        onClick={()=>void act('cloud_bring_back',{branch:null})}>{t('Bring back as a branch')}</button>}
      {run.canFollowUp&&followUp===null&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>setFollowUp('')}>{t('Follow up')}</button>}
    </div>
    {unopened&&run.link&&<p className="dv-field-note">{t('Dervo couldn’t open the link. Copy it from here:')} <code className="dv-cloud-link">{run.link}</code></p>}
    {picking&&<p className="dv-cloud-run-line">{t('Which branch is this run’s?')}</p>}
    {picking&&<div className="dv-cloud-run-foot" role="group" aria-label={t('Which branch is this run’s?')}>
      {run.candidates.map(name=><button key={name} type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy}
        onClick={()=>void act('cloud_bring_back',{branch:name})}><code>{name}</code></button>)}
    </div>}
    {followUp!==null&&<form className="dv-cloud-follow" onSubmit={event=>{event.preventDefault();
      void act('cloud_follow_up',{text:followUp}).then(ok=>{if(ok)setFollowUp(null);});}}>
      <input className="dv-input" aria-label={t('Follow-up for this run')} value={followUp} onChange={event=>setFollowUp(event.target.value)} autoFocus/>
      <button type="submit" className="dv-btn dv-btn-primary dv-btn-sm" disabled={busy||!followUp.trim()}>{t('Send')}</button>
      <button type="button" className="dv-btn dv-btn-quiet dv-btn-sm" onClick={()=>setFollowUp(null)}>{t('Cancel')}</button>
    </form>}
    {error&&<p className="dv-field-note" role="alert">{error}</p>}
  </li>;
}
