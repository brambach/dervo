import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {grantPayload,local,type CodingPermission,type CheckEntry,type ExtraCheck} from './api';
import {t} from '@/i18n';

/** What the five project settings pages share (design sweep, 19 Sep; project-settings/interactions.md): the project's
 * own coding permission read without a thread, a save that shows "Saving…" then "Saved" beside its section title, and
 * the one grant write both Access and Checks and review make, each keeping the other page's half as it is. */

export type SettingsProject={id:string;cwd:string;name?:string|null;profile_id:string;model:string;context?:'work'|'personal'|null};

/** The project's permission, read on its own binding (channelId null): no thread needed. ``enabled`` false waits for a
 * project: a fresh install has none, and the host refuses the read with "Unknown project" (Windows tester's log, 26 Sep). */
export const permissionKey=(projectId:string,channelId:string|null=null)=>channelId?['project-permission',projectId,channelId]:['project-permission',projectId];
export function useProjectPermission(projectId:string,channelId:string|null=null,enabled=true){
  return useQuery({queryKey:permissionKey(projectId,channelId),enabled,queryFn:()=>local<CodingPermission>('coding_permission',{projectId,channelId})});
}

const message=(failure:unknown)=>failure instanceof Error?failure.message:String(failure);

/** Saving as you change things: the control changes at once, the section's caption says "Saving…" while the host call
 * runs and "Saved" for two seconds after, and an error sits under the control it's about. */
export function useSaver(){
  const [phase,setPhase]=useState<Record<string,'saving'|'saved'|undefined>>({});
  const [errors,setErrors]=useState<Record<string,string>>({});
  const timers=useRef<Record<string,number>>({});
  useEffect(()=>()=>{for(const id of Object.values(timers.current))window.clearTimeout(id);},[]);
  async function save<T>(section:string,key:string,work:()=>Promise<T>):Promise<T|undefined>{
    window.clearTimeout(timers.current[section]);
    setPhase(p=>({...p,[section]:'saving'}));setErrors(e=>({...e,[key]:''}));
    try{
      const result=await work();
      setPhase(p=>({...p,[section]:'saved'}));
      timers.current[section]=window.setTimeout(()=>setPhase(p=>({...p,[section]:undefined})),2000);
      return result;
    }catch(failure){
      setPhase(p=>({...p,[section]:undefined}));setErrors(e=>({...e,[key]:message(failure)}));
      return undefined;
    }
  }
  return {
    save,
    saving:(section:string)=>phase[section]==='saving',
    busy:Object.values(phase).includes('saving'),
    caption:(section:string,rest?:string)=>phase[section]==='saving'?t('Saving…'):phase[section]==='saved'?t('Saved'):rest,
    error:(key:string)=>errors[key]||'',
    clear:(key:string)=>setErrors(e=>({...e,[key]:''})),
    fail:(key:string,failure:unknown)=>setErrors(e=>({...e,[key]:message(failure)})),
  };
}

/** After a settings write: the page reads the host's answer, and open threads pick the change up. */
export function useSettingsRefresh(projectId:string,channelId:string|null=null){
  const client=useQueryClient();
  return async(view?:CodingPermission)=>{
    if(view&&typeof view==='object'&&'state' in view)client.setQueryData(permissionKey(projectId,channelId),view);
    else await client.invalidateQueries({queryKey:permissionKey(projectId,channelId)});
    await Promise.all([['conversation-feed',projectId],['coding-permission',projectId],['local-projects']].map(queryKey=>client.invalidateQueries({queryKey})));
  };
}

export const MANUAL='manual-review';
/** The grant's checks as the grant write takes them: the primary one, then the extras. A project with no grant, or one
 * that reviews by hand, checks by your review alone. */
export function currentChecks(permission?:CodingPermission):{primary:CheckEntry;extra:ExtraCheck[]}{
  const checks=permission?.state!=='missing'?permission?.checks??[]:[];
  const primary=checks[0]??{profile:MANUAL,test_file:null,label:t('Your review')};
  const extra=primary.profile===MANUAL?[]:checks.slice(1).filter(c=>c.test_file).map(c=>({checkProfile:c.profile,testFile:c.test_file!,...(c.rust_toolchain?{rustToolchain:c.rust_toolchain}:{})}));
  return {primary,extra};
}
export type Where={projectAccess:boolean;files?:string[];folders?:string[]};
/** The one grant write. Access changes where agents work and keeps the checks; Checks and review changes the checks and
 * keeps where. A restricted grant always names the check's own files, as the host requires. */
export function grantArgs(permission:CodingPermission|undefined,where:Where,checks=currentChecks(permission),timeout=permission?.check_timeout_seconds??5){
  const manual=checks.primary.profile===MANUAL;
  const checkFiles=manual?[]:[checks.primary.test_file,...checks.extra.map(c=>c.testFile)].filter((f):f is string=>!!f);
  const files=[...new Set([...(where.files??[]),...checkFiles])];
  return grantPayload(files,checks.primary.test_file??'',where.folders??[],checks.primary.profile,checks.primary.rust_toolchain??'',manual?5:timeout,where.projectAccess,checks.extra);
}
/** Where the current grant lets agents work. */
export function currentWhere(permission?:CodingPermission):Where{
  if(!permission||permission.state==='missing')return {projectAccess:true};
  return permission.project_access?{projectAccess:true}:{projectAccess:false,files:permission.files,folders:permission.folders??[]};
}
/** "17 Sep", from seconds. */
export function shortDate(at?:number|null){
  if(typeof at!=='number'||at<1e9)return '';
  const when=new Date(at*1000);
  return `${when.getDate()} ${[t('Jan'),t('Feb'),t('Mar'),t('Apr'),t('May'),t('Jun'),t('Jul'),t('Aug'),t('Sep'),t('Oct'),t('Nov'),t('Dec')][when.getMonth()]}`;
}
