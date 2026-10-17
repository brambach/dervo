import {useEffect,useRef,useState} from 'react';
import {local} from './api';
import {MomentMark} from './BrandMoment';
import {StatusGlyph} from './StatusGlyph';
import type {Created} from './FirstRun';
import {MicrotaskTip} from './Microtasks';
import './ProjectMoment.css';
import {t} from '@/i18n';

/** The project-created moment (design sweep, brand/project-created*.html): the top of the new project's Project overview
 * thread, so there's nothing extra to dismiss; it scrolls away with the thread as the overview arrives. It says what
 * first_run_finish returned instead of dropping it: editing that couldn't be turned on, an overview that didn't start,
 * or beta access still waiting, in which case the overview starts by itself once access is approved. Unless the overview
 * failed, it also teaches the microtask pattern (Microtasks.tsx), because this is the last screen before a first task. */
export type Moment={projectId:string;channelId:string;name:string;agent:string;state:'running'|'waiting'|'failed';accessDetail?:string;
  /** What it can do after all, when the access you chose couldn't be turned on. */
  accessNow?:'edit'|'read';
  /** Full access is on, but the checks setup offered couldn't be saved. */
  checksDetail?:string;overviewDetail?:string;
  /** What a waiting overview waits for: beta approval, or a trial or plan (paid plans, 8 Oct 2026). */
  waitingFor?:'beta'|'plan'};

export function momentFrom(created:Created,agent:string):Moment{
  const overview=created.overview;
  const state=overview?.state==='waiting'?'waiting':!overview||overview.state==='not_started'||overview.state==='blocked'||overview.state==='failed'||created.overview_detail?'failed':'running';
  return {projectId:created.id,channelId:overview?.channelId??created.channelId,name:created.name,agent,state,
    accessDetail:created.access_detail,...(created.access_detail?{accessNow:created.access==='edit'?'edit' as const:'read' as const}:{}),...(created.checks_detail?{checksDetail:created.checks_detail}:{}),overviewDetail:state==='failed'?created.overview_detail??overview?.detail:undefined,
    ...(state==='waiting'?{waitingFor:overview?.waiting_for==='plan'?'plan' as const:'beta' as const}:{})};
}

/** Whether the moment still carries the meadow photo across the window. It does while the moment is the story: the
 * overview waiting for access, or being written and the thread's run still live. Once that run has ended the overview
 * is here and the thread is an everyday thread on paper, with the intro still pinned at its top. A failed moment shows
 * an error, and the meadow never sits above an error. Before this rule the photo stayed on the overview thread for the
 * rest of the session, which is what Bryce saw on 21 Sep ("the sky everywhere"). `running` is the thread's activity
 * flag; null means the index has no row for it yet, which is the run's first moments. */
export function momentShowsMeadow(moment:Pick<Moment,'state'>,running:boolean|null|undefined):boolean{
  if(moment.state==='waiting')return true;
  if(moment.state==='failed')return false;
  return running!==false;
}

/** Projects whose overview waits for beta access, kept on this Mac so a restart doesn't lose the wait. */
const WAITING_KEY='dervo-overview-waiting';
export function waitingOverviews():string[]{
  try{const value=JSON.parse(localStorage.getItem(WAITING_KEY)??'[]');return Array.isArray(value)?value.filter((id):id is string=>typeof id==='string'):[];}catch{return [];}
}
function storeWaiting(ids:string[]){try{if(ids.length)localStorage.setItem(WAITING_KEY,JSON.stringify(ids));else localStorage.removeItem(WAITING_KEY);}catch{/* The wait still holds for this session. */}}
export function rememberWaiting(projectId:string){storeWaiting([...new Set([...waitingOverviews(),projectId])]);}
export function forgetWaiting(projectId:string){storeWaiting(waitingOverviews().filter(id=>id!==projectId));}

/** The request ID first run used, so starting it again can never start it twice. */
export const overviewClientId=(projectId:string)=>`first-run-${projectId}`;
const starting=new Set<string>();

/** When beta access turns approved (and there's a trial or plan), start every overview that was waiting for it (project_explore, idempotent per
 * project and request ID). A start still refused on access stays waiting and is tried again on the next change; any
 * other refusal is shown on the moment and the wait ends. */
export function useOverviewOnApproval(approved:boolean,onStarted:(projectId:string,channelId:string)=>void,onFailed:(projectId:string,detail:string)=>void){
  const handlers=useRef({onStarted,onFailed});
  handlers.current={onStarted,onFailed};
  useEffect(()=>{
    if(!approved)return;
    for(const projectId of waitingOverviews()){
      if(starting.has(projectId))continue;
      starting.add(projectId);
      local<{channelId:string;state?:string;error?:string|null;detail?:string}>('project_explore',{projectId,clientId:overviewClientId(projectId)})
        .then(result=>{
          forgetWaiting(projectId);
          if(result.state==='blocked'||result.state==='failed')handlers.current.onFailed(projectId,result.error||result.detail||t('The overview didn’t start.'));
          else handlers.current.onStarted(projectId,result.channelId);
        })
        .catch(failure=>{
          // Still refused on access or for want of a plan (approval can race the host's own check): keep waiting. The
          // host's plan line says "Choose a plan" or "Start your free trial" (supabase_account._no_plan_line). Anything else is an answer.
          const detail=failure instanceof Error?failure.message:String(failure);
          if(!/beta access|choose a plan|start your free trial/i.test(detail)){forgetWaiting(projectId);handlers.current.onFailed(projectId,detail);}
        })
        .finally(()=>starting.delete(projectId));
    }
  },[approved]);
}

function agoText(at:number,now:number):string{
  const seconds=Math.max(0,Math.round((now-at)/1000));
  if(seconds<45)return t('checked just now');
  if(seconds<90)return t('checked a minute ago');
  return t('checked {minutes} minutes ago',{minutes:Math.round(seconds/60)});
}

export function ProjectMoment({moment,checkedAt,checking,onCheck,onRetry,onSettings,onHome}:{moment:Moment;checkedAt?:number;checking?:boolean;
  onCheck:()=>void;onRetry:()=>Promise<void>;onSettings:(trigger:HTMLElement)=>void;onHome?:()=>void}){
  const [now,setNow]=useState(Date.now()),[retrying,setRetrying]=useState(false);
  useEffect(()=>{if(moment.state!=='waiting')return;const timer=setInterval(()=>setNow(Date.now()),15000);return ()=>clearInterval(timer);},[moment.state]);
  useEffect(()=>{setNow(Date.now());},[checkedAt]);
  const {name,agent,state}=moment;
  return <section className="dv-pm" aria-label={t('Project created')}>
    <div className="dv-pm-heading">
      {state!=='failed'&&<MomentMark size={28} enter/>}
      <h2>{t('{name} is set up.',{name})}</h2>
    </div>
    {state==='running'&&<p className="dv-pm-lede">{t('A read-only introduction to your project, written by {agent}.',{agent})}</p>}
    {state==='waiting'&&moment.waitingFor==='plan'&&<>
      <p className="dv-pm-lede">{t('{agent} will read it and write you an overview as soon as you start a free trial or choose a plan in Settings › Account. Everything else is set up.',{agent})}</p>
      <p className="dv-status-line dv-pm-status"><StatusGlyph state="queued"/><span role="status">{t('Waiting for a plan')}</span>{checkedAt?` · ${agoText(checkedAt,now)}`:''}
        <button type="button" className="dv-btn dv-btn-quiet" disabled={checking} onClick={onCheck}>{checking?t('Checking…'):t('Check now')}</button></p>
    </>}
    {state==='waiting'&&moment.waitingFor!=='plan'&&<>
      <p className="dv-pm-lede">{t('{agent} will read it and write you an overview as soon as your beta access is approved. You don’t need to do anything else. Invited under a different address? Tell whoever sent your invite which one you signed in with.',{agent})}</p>
      <p className="dv-status-line dv-pm-status"><StatusGlyph state="queued"/><span role="status">{t('Waiting for approval')}</span>{checkedAt?` · ${agoText(checkedAt,now)}`:''}
        <button type="button" className="dv-btn dv-btn-quiet" disabled={checking} onClick={onCheck}>{checking?t('Checking…'):t('Check now')}</button></p>
    </>}
    {state==='failed'&&<>
      <p className="dv-pm-lede">{moment.overviewDetail?t('The overview didn’t start: {why}. The project is saved, and you can start it again.',{why:moment.overviewDetail.replace(/\.$/,'')}):t('The overview didn’t start. The project is saved, and you can start it again.')}</p>
      <div className="dv-moment-actions"><button type="button" className="dv-btn dv-btn-primary" disabled={retrying} onClick={()=>{setRetrying(true);void onRetry().finally(()=>setRetrying(false));}}>{retrying?t('Starting…'):t('Try again')}</button></div>
    </>}
    {moment.accessDetail&&<p className="dv-status-line dv-pm-status"><StatusGlyph state="idle"/><span>{moment.accessNow==='edit'?t('Full access isn’t on, so it can edit files with your approval for now. {why}',{why:moment.accessDetail}):t('Editing couldn’t be turned on: {why}. It can read and explain for now.',{why:moment.accessDetail.replace(/\.$/,'')})}</span>
      <button type="button" className="dv-btn dv-btn-quiet" onClick={event=>onSettings(event.currentTarget)}>{t('Open Project settings')}</button></p>}
    {moment.checksDetail&&<p className="dv-status-line dv-pm-status"><StatusGlyph state="idle"/><span>{t('Full access is on, but its checks couldn’t be saved: {why}. Choose them in Project settings.',{why:moment.checksDetail.replace(/\.$/,'')})}</span>
      <button type="button" className="dv-btn dv-btn-quiet" onClick={event=>onSettings(event.currentTarget)}>{t('Open Project settings')}</button></p>}
    {state!=='failed'&&<MicrotaskTip approved={state!=='waiting'} onHome={onHome}/>}
  </section>;
}
