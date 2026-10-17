import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {local} from './api';
import {ruleDate,ruleTitle,type Rule} from './KnowledgeNotebook';
import {fileSize,type RepoListing} from './KnowledgeRepo';
import {providerAuthor} from './providerLabel';
import './ProjectKnowledge.css';
import {formatDate,t} from '@/i18n';

/** "What it was told" (design sweep, run-context.html): what Dervo sent a run before the agent started, read from the
 * run's own record. The repository files with their versions, your rules by number, agents' notes and skills. It's
 * what was sent, not proof the agent followed it. The old per-run disclosure stays in the Record for support. */
type SentFile={path:string;sha256:string;bytes:number;sent_bytes:number;cut:boolean;modified:number};
type SentRule={rule_id:string;version_id:string;text:string};
/** A rule in use the run didn't get, with why (beta.11, fix-memory F2 and F3). Older records have ``omitted`` only. */
export type NotSent={rule_id:string;version_id:string;reason:string};
export type RunRecord={id:string;state?:string;started_at?:number;created_at?:number;
 knowledge_snapshot?:{captured_at:string;text:string;omitted:number;profile_id?:string;rules:SentRule[];not_sent?:NotSent[];
  repository?:{files:SentFile[];left_out:{path:string;reason:string;detail?:string}[];text:string;limit:number}};
 told?:{notes?:{id:string;text:string;provider?:string}[];skills?:{id:string;name:string;version:number;description?:string}[]}};

const FILE=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="dv-ico"><path d="M4 2.5h5l3 3v8H4z"/><path d="M9 2.5v3h3"/></svg>;
const NOTE=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="dv-ico"><path d="M3 3.5h10v9H3z"/><path d="M5.5 6.5h5M5.5 9h3"/></svg>;
const SKILL=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="dv-ico"><path d="M6.5 4.5h6M6.5 8h6M6.5 11.5h6"/><path d="M3 4.5h.5M3 8h.5M3 11.5h.5"/></svg>;
const OUT=<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5.5 10.5l5-5M6.5 5.5h4v4"/></svg>;
const MORE=<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>;
const REASONS:Record<string,string>={stopped:t('you stopped sending it'),unreadable:t('it couldn’t be read'),total:t('the files together passed 128 KB')};
const WHY_NOT:Record<string,string>={limit:t('past the 12-rule or 16 KB limit'),source_private:t('the chat it came from is private now'),
 source_changed:t('the chat it came from changed account or model since'),source_unavailable:t('its source no longer checks out'),work_account:t('this chat uses a work account')};

/** The one line about rules a run in use didn't get: "2 weren't sent: 1 past the 12-rule or 16 KB limit, 1 because the
 * chat it came from is private now. Fix it in Knowledge." Nothing when every rule went. */
export function notSentLine(notSent:NotSent[]|undefined,omitted:number):string{
 if(!notSent){return omitted>0?t('{omitted} left out: past the 12-rule or 16 KB limit, or a source that no longer checks out.',{omitted}):'';}
 if(!notSent.length)return '';
 const counts=new Map<string,number>();
 for(const item of notSent)counts.set(item.reason,(counts.get(item.reason)??0)+1);
 const parts=[...counts].map(([reason,n])=>{const why=WHY_NOT[reason]??t('its source no longer checks out');return reason==='limit'?`${n} ${why}`:t('{count} because {why}',{count:n,why});});
 const fixable=[...counts.keys()].some(reason=>reason!=='limit'&&reason!=='work_account');
 const n=notSent.length;
 return `${t('{count} rules weren’t sent: {parts}.',{count:n,parts:parts.join(', ')})}${fixable?` ${t('Fix it in Knowledge.')}`:''}`;
}

function clock(value:string):string{
 const at=new Date(value);
 if(Number.isNaN(at.getTime()))return '';
 const today=at.toDateString()===new Date().toDateString();
 const time=formatDate(at,{hour:'numeric',minute:'2-digit'});
 return today?t('at {time}',{time}):t('on {date} at {time}',{date:ruleDate(value),time});
}
function day(seconds:number):string{return ruleDate(new Date(seconds*1000).toISOString());}

/** The rule line's note when the run had a version that's no longer the newest: "Version 1. Version 2 was saved later." */
export function versionNote(sent:SentRule,rules:Rule[]|undefined):string{
 const rule=rules?.find(entry=>entry.id===sent.rule_id);
 if(!rule)return '';
 const index=rule.versions.findIndex(version=>version.id===sent.version_id);
 if(index<0)return '';
 if(!rule.active_version_id)return t('Version {number}. The rule is archived now.',{number:index+1});
 return index<rule.versions.length-1?t('Version {number}. Version {latest} was saved later.',{number:index+1,latest:rule.versions.length}):'';
}

/** The view itself, from one run's record. ``rules`` and ``repository`` are Knowledge as it is now, for the notes that
 * compare; without them the view still lists exactly what was sent. */
export function RunTold({run,provider,rules,repository,onOpenKnowledge}:{run:RunRecord|undefined;provider?:string;rules?:Rule[];repository?:RepoListing;onOpenKnowledge?:()=>void}){
 const [sent,setSent]=useState(false);
 const snapshot=run?.knowledge_snapshot;
 if(!run||!snapshot)return <div className="dv-told"><p className="dv-told-note">{t('No record for this run. It ran before Dervo kept one.')}</p>
  {onOpenKnowledge&&<div className="dv-told-links"><button type="button" className="dv-btn dv-btn-quiet" onClick={onOpenKnowledge}>{t('Open Knowledge')}{OUT}</button></div>}</div>;
 const files=snapshot.repository?.files??[],leftOut=snapshot.repository?.left_out??[];
 const inUse=(rules??[]).filter(rule=>rule.active_version_id);
 const notes=run.told?.notes??[],skills=run.told?.skills??[];
 const total=snapshot.rules.length+(snapshot.not_sent?snapshot.not_sent.length:snapshot.omitted);
 const who=provider?providerAuthor(provider):t('the agent');
 const exact=[snapshot.repository?.text??'',snapshot.text].filter(Boolean).join('\n');
 return <div className="dv-told">
  <p className="dv-told-note">{t('Sent with the task {when}, before {who} started. It’s what Dervo sent, not proof the agent followed it.',{when:clock(snapshot.captured_at),who})}</p>
  <section aria-label={t('From the repository')}>
   <h3 className="dv-label">{t('From the repository')}</h3>
   {files.length?<ul className="dv-told-list">{files.map(file=>{
    const now=repository?.files.find(entry=>entry.path===file.path);
    const current=!now?'':now.sha256===file.sha256?` · ${t('still current')}`:` · ${t('changed since')}`;
    return <li key={file.path}>{FILE}<span><span className="dv-told-name">{file.path}</span>
     <small>{t('The {day} version, {part}',{day:day(file.modified),part:file.cut?t('the first {sent} of {size}',{sent:fileSize(file.sent_bytes),size:fileSize(file.bytes)}):t('the whole file')})}{current}</small></span></li>;
   })}</ul>:<p className="dv-told-note">{snapshot.repository?t('None. The repository has no instruction files Dervo sends.'):t('None. This run is older than Dervo’s repository files.')}</p>}
   {leftOut.length>0&&<p className="dv-told-note">{t('Not sent: {items}.',{items:leftOut.map(item=>t('{path}, because {why}',{path:item.path,why:REASONS[item.reason]??item.reason})).join('; ')})}</p>}
  </section>
  <section aria-label={t('Your rules')}>
   <h3 className="dv-label">{t('Your rules · {sent} of {total}',{sent:snapshot.rules.length,total})}</h3>
   {snapshot.rules.length?<ul className="dv-told-list">{snapshot.rules.map(rule=>{
    const number=inUse.findIndex(entry=>entry.id===rule.rule_id)+1,note=versionNote(rule,rules);
    return <li key={rule.rule_id}><span className="dv-num">{number||'·'}</span><span>{ruleTitle(rule.text)}{note&&<small>{note}</small>}</span></li>;
   })}</ul>:<p className="dv-told-note">{t('None were in use.')}</p>}
   {notSentLine(snapshot.not_sent,snapshot.omitted)&&<p className="dv-told-note">{notSentLine(snapshot.not_sent,snapshot.omitted)}</p>}
  </section>
  <section aria-label={t('Agents’ notes')}>
   <h3 className="dv-label">{t('Agents’ notes')}{run.told?` · ${notes.length}`:''}</h3>
   {notes.length?<ul className="dv-told-list">{notes.map(note=><li key={note.id}>{NOTE}<span>{note.text}</span></li>)}</ul>
    :<p className="dv-told-note">{run.told?t('None.'):t('Not recorded for this run. It ran before Dervo kept notes in the record.')}</p>}
  </section>
  <section aria-label={t('Skills')}>
   <h3 className="dv-label">{t('Skills')}</h3>
   {skills.length?<ul className="dv-told-list">{skills.map(skill=><li key={skill.id}>{SKILL}<span><span className="dv-told-name">{skill.name}</span><small>{t('Version {version}',{version:skill.version})}{skill.description?` · ${skill.description}`:''}</small></span></li>)}</ul>
    :<p className="dv-told-note">{run.told?t('None matched this task.'):t('Not recorded for this run.')}</p>}
  </section>
  <div className="dv-told-links">
   <button type="button" className="dv-btn dv-btn-quiet" aria-expanded={sent} onClick={()=>setSent(value=>!value)}>{t('Exactly what was sent')}{MORE}</button>
   {sent&&<pre className="dv-told-sent">{exact||t('Nothing. No files or rules were in use.')}</pre>}
   {onOpenKnowledge&&<button type="button" className="dv-btn dv-btn-quiet" onClick={onOpenKnowledge}>{t('Open Knowledge')}{OUT}</button>}
  </div>
 </div>;
}

/** The view for one task in the Progress panel: its latest run, read from the task's record. */
export function RunToldForTask({projectId,channelId,taskId,provider,onOpenKnowledge}:{projectId:string;channelId:string;taskId:string;provider?:string;onOpenKnowledge?:()=>void}){
 const detail=useQuery({queryKey:['task-detail',projectId,channelId,taskId],queryFn:()=>local<{task:{runs:RunRecord[]}}>('task_detail',{projectId,channelId,taskId})});
 const run=detail.data?.task.runs.at(-1);
 const profileId=run?.knowledge_snapshot?.profile_id;
 const knowledge=useQuery({queryKey:['run-told-knowledge',projectId,profileId],enabled:!!profileId,retry:false,
  queryFn:()=>local<{rules:Rule[]}>('knowledge_list',{projectId,channelId:'',profileId})});
 const repository=useQuery({queryKey:['run-told-repository',projectId,profileId],enabled:!!profileId,retry:false,
  queryFn:()=>local<RepoListing>('knowledge_repo_list',{projectId,channelId:'',profileId})});
 if(detail.error)return <div className="dv-told"><p className="dv-told-note" role="alert">{t('Couldn’t load what this run was told.')} <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void detail.refetch()}>{t('Try again')}</button></p></div>;
 if(!detail.data)return <div className="dv-told"><p className="dv-told-note" role="status">{t('Reading what it was told…')}</p></div>;
 return <RunTold run={run} provider={provider} rules={knowledge.data?.rules} repository={repository.data} onOpenKnowledge={onOpenKnowledge}/>;
}
