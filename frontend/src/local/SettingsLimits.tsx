import {local,type CodingPermission,type RunLimits} from './api';
import {SettingRow,SettingsSection,Segmented} from './DervoControls';
import {useProjectPermission,useSaver,useSettingsRefresh,type SettingsProject} from './projectSettingsData';
import {FieldError} from './SettingsBits';
import {t} from '@/i18n';

/** Run limits (beta.17.1, Bryce 5 Oct 2026): how long a run may go quiet, how long it may last and how many attempts a
 * task gets, per project, beside what agents can do. Silence always ends a run, so a stuck one still stops. Each choice is
 * one the host takes (backend/run_limits.py); a run takes them as it starts, attempts as each one is made. */

const NONE='none';
const key=(value:number|null)=>value===null?NONE:String(value);
const value=(text:string)=>text===NONE?null:Number(text);

export function hoursLabel(seconds:number|null):string{
 if(seconds===null)return t('No cap');
 if(seconds<3600)return t('{count} min',{count:Math.round(seconds/60)});
 return t('{count} h',{count:Math.round(seconds/360)/10});
}
export function attemptsLabel(count:number|null):string{return count===null?t('No limit'):String(count);}

/** What each row says under its name, from the project's current limits. */
export function limitLines(limits:RunLimits):{idle:string;run:string;attempts:string}{
 return {
  idle:t('A run that sends nothing for {time} is stopped. Continue picks it up from there.',{time:hoursLabel(limits.idle_seconds)}),
  run:limits.run_seconds===null?t('No cap on how long a run lasts. Only silence ends it.'):t('A run is stopped after {time}, however busy. Continue picks it up from there.',{time:hoursLabel(limits.run_seconds)}),
  attempts:limits.attempts===null?(limits.warning??t('No limit on attempts.')):t('A task stops after {count} attempts. Each edit, each check and each full-access run is one.',{count:limits.attempts}),
 };
}

export function RunLimitsSection({project,channelId=null}:{project:SettingsProject;channelId?:string|null}){
 const permission=useProjectPermission(project.id,channelId);
 const saver=useSaver(),refresh=useSettingsRefresh(project.id,channelId);
 const limits=permission.data?.run_limits;
 if(!limits||permission.data?.state==='unsupported')return null;
 const choices=limits.choices??{idle_seconds:[1800,3600,7200,14400],run_seconds:[21600,43200,86400,null],attempts:[16,64,null]};
 const lines=limitLines(limits);
 async function choose(name:'idleSeconds'|'runSeconds'|'attempts',next:number|null){
  await saver.save('limits',name,async()=>refresh(await local<CodingPermission>('coding_run_limits_set',{projectId:project.id,channelId,[name]:next})));
 }
 return <SettingsSection id="limits" title={t('Run limits')} caption={saver.caption('limits',t('Applies to new runs'))}>
  <SettingRow name={t('Stop a quiet run after')} line={lines.idle}
   end={<Segmented label={t('Stop a quiet run after')} value={key(limits.idle_seconds)} disabled={saver.saving('limits')}
    onChange={next=>void choose('idleSeconds',value(next))} options={choices.idle_seconds.map(seconds=>({value:key(seconds),label:hoursLabel(seconds)}))}/>}/>
  <FieldError message={saver.error('idleSeconds')}/>
  <SettingRow name={t('Longest run')} line={lines.run}
   end={<Segmented label={t('Longest run')} value={key(limits.run_seconds)} disabled={saver.saving('limits')}
    onChange={next=>void choose('runSeconds',value(next))} options={choices.run_seconds.map(seconds=>({value:key(seconds),label:hoursLabel(seconds)}))}/>}/>
  <FieldError message={saver.error('runSeconds')}/>
  <SettingRow name={t('Attempts per task')} line={lines.attempts}
   end={<Segmented label={t('Attempts per task')} value={key(limits.attempts)} disabled={saver.saving('limits')}
    onChange={next=>void choose('attempts',value(next))} options={choices.attempts.map(count=>({value:key(count),label:attemptsLabel(count)}))}/>}/>
  <FieldError message={saver.error('attempts')}/>
 </SettingsSection>;
}
