import {useState} from 'react';
import {local,type CodingPermission,type RunCheck} from './api';
import type {CodingTask} from './CodingTasks';
import {runChecks} from './CodingTasks';
import {StatusGlyph} from './StatusGlyph';
import {t} from '@/i18n';

/** Under a full-access result that ran no checks, in a project whose files suggest some: one tap turns them on for every
 * run after, and Not now keeps the offer away until you choose in Settings (project_checks.not_now). Critic, 9 Oct: a
 * trial user won't open Settings in week one, so the offer sits where the result is. */
const SETTLED=new Set(['ready_for_review','completed','idle']);
export function checksOffer(task:Pick<CodingTask,'full_access'|'state'|'origin'|'runs'>,coding?:CodingPermission):RunCheck[]{
  const run=coding?.run_checks;
  if(!task.full_access||task.origin!=='conversation'||!SETTLED.has(task.state)||runChecks(task))return [];
  // Not where they'd be skipped: full access is off now, or this project's runs can't run commands (start_checks).
  if(!run||run.commands.length||!run.offer||!coding?.full_access?.enabled||coding.full_access.commands===false)return [];
  return run.suggested;
}
/** "npm test", "npm test and npm run lint", "npm test, npm run lint and 1 more". */
export function offerWords(checks:RunCheck[]):string{
  const shown=checks.slice(0,2).map(check=>`${check.argv.join(' ')}${check.cwd&&check.cwd!=='.'?` (${check.cwd})`:''}`);
  if(checks.length<=2)return shown.length===2?t('{first} and {second}',{first:shown[0],second:shown[1]}):shown[0]??'';
  return t('{first}, {second} and {count} more',{first:shown[0],second:shown[1],count:checks.length-2});
}

export function ChecksOffer({task,projectId,coding,refresh}:{task:CodingTask;projectId:string;coding?:CodingPermission;refresh:()=>Promise<unknown>}){
  const [busy,setBusy]=useState<'on'|'later'|null>(null),[error,setError]=useState('');
  const checks=checksOffer(task,coding);
  if(!checks.length)return null;
  async function save(kind:'on'|'later'){
    setBusy(kind);setError('');
    try{
      await local('coding_checks_set',{projectId,channelId:null,names:kind==='on'?checks.map(check=>check.name):[],...(kind==='later'?{notNow:true}:{})});
      await refresh();
    }catch(e){setError(String(e));}finally{setBusy(null);}
  }
  return <div className="dv-sheet dv-handoff" data-testid="checks-offer"><div className="dv-row dv-open">
    <StatusGlyph state="needs"/>
    <div>
      <div className="dv-row-name">{t('Dervo can run {checks} after each change',{checks:offerWords(checks)})}</div>
      <div className="dv-row-line">{t('Dervo runs them, not the agent, and tells you only what newly broke. Change them any time in Settings, Checks and review.')}</div>
      <div className="dv-row-actions">
        <button type="button" className="dv-btn dv-btn-ghost" disabled={!!busy} onClick={()=>void save('on')}>{busy==='on'?t('Turning on…'):t('Run them after each change')}</button>
        <button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void save('later')}>{t('Not now')}</button>
      </div>
      {error&&<p role="alert" className="dv-row-line dv-suggest-error">{error}</p>}
    </div>
  </div></div>;
}
