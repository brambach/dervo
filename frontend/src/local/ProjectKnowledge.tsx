import {yourWords} from './timelineRows';
import {Fragment,useEffect,useRef,useState,type ReactNode} from 'react';
import {local,type Conversation} from './api';
import type {CodingTask} from './CodingTasks';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {DropdownMenu,DropdownMenuContent,DropdownMenuItem,DropdownMenuTrigger} from '@/shared/ui/dropdown-menu';
import {KnowledgeHistory,MoveRules,archivedVersion,ruleDate,ruleTitle,type Delivery,type MovePreset,type NotebookSelection,type Rule,type RuleMove,type Version} from './KnowledgeNotebook';
import {KnowledgeQueue,queueItems,type LessonSuggestion,type QueueActions} from './KnowledgeQueue';
import {KnowledgeRepo,type RepoFile,type RepoListing} from './KnowledgeRepo';
import {FactsSection,isNew,snapshot,tokensLine,type FactActions,type ProjectMemoryView} from './ProjectMemory';
import {SkillsPage,skillIsNew,skillsKey,useSkills,type SkillListing} from './ProjectSkills';
import {SettingRow,Switch} from './DervoControls';
import {StatusGlyph} from './StatusGlyph';
import {providerAuthor} from './providerLabel';
export {KnowledgeHistory} from './KnowledgeNotebook';
import './ProjectKnowledge.css';
import {t,tr} from '@/i18n';

/** Knowledge, "What Dervo knows here" (beta.16, b16/memory; approved mock evidence/design-tests/b16-memory): one page,
 * newest first, of what agents in this project are told. Facts and decisions (agents' notes and lessons), How we work
 * (skills) and Your rules. Nothing an agent keeps waits for a click any more: it's kept as it happens, marked New until
 * you've looked, and you can edit or delete any of it here, with Undo. What an agent keeps is only ever a background
 * fact or a skill; rules are what you write or promote. The repository's instruction files shrink to one line at the
 * end, and a SKILL.md you import, or anything saved before beta.16, still waits at the top until you decide. */
type Procedure={id:string;title:string;text:string;author:string;provenance:string;license:string};
type LessonPage={items:LessonSuggestion[];limited:boolean;nextCursor?:number|null};
type Data={rules:Rule[];procedures:Procedure[];helper_retrieval?:string;lesson_suggestions_enabled?:boolean;suggestions?:LessonPage;repository?:RepoListing;deliveries?:Record<string,Delivery>;
 /** Agent-made rules in use and what moving them starts on (beta.17.1, #41 step 8). */
 move_presets?:Record<string,MovePreset>};
/** ``addRequest`` puts the cursor in the add line when it changes, as ⌘K's Add guidance asks. */
type Props={projectId:string;channelId:string;profileId:string;projectName?:string;open:boolean;onClose:()=>void;returnLabel?:string;sourceTasks?:CodingTask[];sourceChannelId?:string;onOpenTaskSource?:(channelId:string,taskId:string)=>void;addRequest?:number;onAddHandled?:()=>void};
type Draft={revision:number;text:string;provenance:string;sourceChannel:string;sourceMessage:string;sourceTask?:string;sourceRun?:string;editing:Rule|null};
type Notice={text:string;undo?:()=>Promise<unknown>;tone?:'done'|'error'};
type Confirm={kind:'use';rule:Rule;version:Version}|{kind:'archive';rule:Rule};
const emptyDraft=():Draft=>({revision:0,text:'',provenance:'',sourceChannel:'',sourceMessage:'',sourceTask:'',sourceRun:'',editing:null});
const CHEVRON=<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>;
const PLUS=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9"/></svg>;
const PROCEDURE_ICON=<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="dv-ico"><path d="M4 2.5h5l3 3v8H4z"/><path d="M9 2.5v3h3M6 8.5h4M6 11h3"/></svg>;
const NOTICE_MS=8000;

/** The date a starter procedure's provenance names, as "13 Sep 2026"; nothing when it names none. */
export function procedureDate(provenance:string):string {
 const match=/(\d{4})-(\d{2})-(\d{2})/.exec(provenance);
 if(!match)return '';
 // A calendar date, not an instant: local noon, so ruleDate (which formats in this Mac's timezone) shows the day it names.
 const date=new Date(Number(match[1]),Number(match[2])-1,Number(match[3]),12);
 return Number.isNaN(date.getTime())?'':ruleDate(date.toISOString(),{year:true});
}
/** A procedure's author line: "Written by Dervo · 13 Sep 2026". */
export function procedureCredit(p:Pick<Procedure,'author'|'provenance'>):string{
 const date=procedureDate(p.provenance);
 return `Written by ${p.author}${date?` · ${date}`:''}`;
}
const inUse=(rules:Rule[])=>rules.filter(rule=>rule.active_version_id);
/** How long a rule that says where something is right now goes before Knowledge asks whether it's still true. */
export const STILL_TRUE_DAYS=7;
const stillTrueKey=(projectId:string)=>`dervo-still-true:${projectId}`;
/** Rules in use whose words say where something was on a day (a commit something is at, "as of", the newest of
 * something) and are over STILL_TRUE_DAYS old, minus the versions you said are still true (dd #22, C2). On 2 Oct three
 * of this project's twelve rules still said beta.14 was the newest catalog row or where beta16 sat. Nothing changes
 * until you choose: rules never expire on their own. */
export function stillTrueRules(rules:Rule[],now=Date.now(),kept:string[]=[]):{rule:Rule;version:Version}[]{
 return inUse(rules).flatMap(rule=>{
  const version=rule.versions.find(v=>v.id===rule.active_version_id);
  const saved=version?new Date(version.created_at).getTime():NaN;
  return version&&!kept.includes(version.id)&&snapshot(version.text)&&now-saved>STILL_TRUE_DAYS*86400000?[{rule,version}]:[];
 });
}
/** Rule versions you kept as rules in Move agent-made rules to notes (beta.17.1, #41 step 8), so the card doesn't ask
 * about them again. A new version of one is asked about. */
const keptRuleKey=(projectId:string)=>`dervo-kept-as-rule:${projectId}`;
function keptAsTrue(projectId:string,key=stillTrueKey):string[]{
 try{const value=JSON.parse(localStorage.getItem(key(projectId))??'[]');return Array.isArray(value)?value.filter((v:unknown)=>typeof v==='string'):[];}
 catch{return [];}
}
const numberOf=(rules:Rule[],id:string)=>inUse(rules).findIndex(rule=>rule.id===id)+1;
const reason=(failure:unknown)=>failure instanceof Error?failure.message:String(failure);

/** Whether something new was kept since you last opened Knowledge, for the sidebar's dot (Harrison, app #19; b16/memory).
 * A note, a lesson or an agent's skill saved after ``seen_at`` counts; your own edits don't. Opening Knowledge marks it
 * all seen and clears the dot. ``b16/lifecycle`` draws the dot in DervoSidebar.tsx. */
/** The sidebar's dot. It reads again every minute, so something a run in another thread or Home kept shows without
 * leaving the page, and ConnectedApp refreshes it at once when the open thread keeps something (behaviour review S3,
 * 25 Sep: it used to read once, so the dot only lit after switching project or reopening Dervo). */
export function useKnowledgeUnread(projectId:string,profileId:string|undefined,enabled:boolean):boolean{
 const notes=useQuery({queryKey:['knowledge-unread',projectId,profileId],enabled:enabled&&!!profileId,retry:false,staleTime:30000,refetchInterval:60000,
  queryFn:()=>local<ProjectMemoryView>('knowledge_notes_list',{projectId,channelId:'',profileId})});
 const skills=useQuery({queryKey:skillsKey(projectId,profileId??''),enabled:enabled&&!!profileId,retry:false,staleTime:30000,refetchInterval:60000,
  queryFn:()=>local<SkillListing>('skills_list',{projectId,profileId})});
 return unreadCount(notes.data,skills.data)>0;
}
/** How many things were kept since you last looked. */
export function unreadCount(view:ProjectMemoryView|undefined|null,skills:SkillListing|undefined,seenAt=view?.seen_at):number{
 if(!view||!Array.isArray(view.entries))return 0;
 return view.entries.filter(entry=>isNew(entry,seenAt)).length+(skills?.skills??[]).filter(skill=>skillIsNew(skill,seenAt)).length;
}
/** How many things still wait for a click in Knowledge: suggestions saved before beta.16, rule versions not in use, and
 * imported skill files. The sidebar showed this as a count until b16/lifecycle moves it to useKnowledgeUnread's dot. */
export function useKnowledgeWaiting(projectId:string,profileId:string|undefined,enabled:boolean):number{
 const knowledge=useQuery({queryKey:['knowledge-waiting',projectId,profileId],enabled:enabled&&!!profileId,retry:false,staleTime:30000,
  queryFn:()=>local<Data>('knowledge_list',{projectId,channelId:'',profileId})});
 const skills=useQuery({queryKey:skillsKey(projectId,profileId??''),enabled:enabled&&!!profileId,retry:false,staleTime:30000,
  queryFn:()=>local<SkillListing>('skills_list',{projectId,profileId})});
 if(!knowledge.data)return 0;
 return queueItems(knowledge.data.suggestions?.items??[],skills.data,knowledge.data.rules).length;
}
export function ProjectKnowledge(props:Props){
 return <KnowledgeEditor key={JSON.stringify([props.projectId,props.profileId])} {...props}/>;
}
function KnowledgeEditor({projectId,channelId,profileId,projectName,open,onClose,returnLabel=t('Back to conversation'),sourceTasks=[],sourceChannelId=channelId,onOpenTaskSource,addRequest=0,onAddHandled}:Props){
 const titleRef=useRef<HTMLHeadingElement>(null),textRef=useRef<HTMLTextAreaElement>(null),decisionRef=useRef<HTMLButtonElement>(null),addRef=useRef<HTMLInputElement>(null);
 const client=useQueryClient();
 const [notice,setNoticeState]=useState<Notice|null>(null);
 const [selection,setSelection]=useState<NotebookSelection>({ruleId:'',versionId:'',history:false,reading:false});
 const [noteFocus,setNoteFocus]=useState(0);
 const [adding,setAdding]=useState('');
 const [freshLines,setFresh]=useState<Record<string,string>>({});
 const [queueErrors,setQueueErrors]=useState<Record<string,string>>({});
 const queueErrorsRef=useRef(queueErrors);queueErrorsRef.current=queueErrors;
 const [switchErrors,setSwitchErrors]=useState<Record<string,string>>({});
 const navigation=useRef(0);
 const storageKey='dervo-knowledge-draft:'+JSON.stringify([projectId,profileId]);
 function readDraft():Draft{try{return JSON.parse(localStorage.getItem(storageKey)??'null')??emptyDraft();}catch{return emptyDraft();}}
 const [draft,setDraft]=useState<Draft>(readDraft),draftRef=useRef(draft);
 const [editor,setEditor]=useState<boolean>(()=>{const saved=readDraft();return !!(saved.text||saved.editing);});
 const editorRef=useRef(editor);editorRef.current=editor;
 const {text,provenance,sourceChannel,sourceMessage,sourceTask='',sourceRun='',editing}=draft;
 const [data,setData]=useState<Data|null>(null),[error,setError]=useState(''),[loadError,setLoadError]=useState(''),[busy,setBusy]=useState('');
 const [confirm,setConfirm]=useState<Confirm|null>(null);
 const [stillKept,setStillKept]=useState<string[]>(()=>keptAsTrue(projectId));
 const [keptRules,setKeptRules]=useState<string[]>(()=>keptAsTrue(projectId,keptRuleKey));
 const [pendingEdit,setPendingEdit]=useState<{rule:Rule;version:Version}|null>(null);
 const scope={projectId,channelId,profileId};
 const [memoryView,setMemoryView]=useState<ProjectMemoryView|null>(null),[memoryError,setMemoryError]=useState('');
 // The repository's files load on their own, so they show even when the rules can't load (knowledge-a-error.html).
 const [repo,setRepo]=useState<RepoListing|null>(null),[repoError,setRepoError]=useState('');
 async function loadRepo(){try{setRepo(await local<RepoListing>('knowledge_repo_list',{projectId,channelId,profileId}));setRepoError('');}catch(failure){setRepoError(reason(failure));}}
 useEffect(()=>{if(open)void loadRepo();},[open,projectId,profileId]);
 const skills=useSkills(projectId,profileId);
 // The shell already loaded this project's threads; read the cache for thread names, never fetch here.
 const conversations=useQuery<Conversation[]>({queryKey:['local-conversations',projectId],enabled:false,queryFn:()=>client.getQueryData<Conversation[]>(['local-conversations',projectId])??[]});
 const threadName=(id:string)=>(conversations.data??client.getQueryData<Conversation[]>(['local-conversations',projectId]))?.find(c=>c.id===id)?.name;
 // Agents' notes load on their own through Knowledge's own ops (beta.11, F1): the page has no conversation, and a
 // project on a work account still shows its notes even when its rules can't load.
 const asView=(value:unknown):ProjectMemoryView=>{const view=value as ProjectMemoryView;if(!view||!Array.isArray(view.entries))throw new Error(t('The host sent no notes.'));return view;};
 const notes=async(op:string,args:Record<string,unknown>={})=>{const view=asView(await local(op,{...scope,...args}));setMemoryView(view);return view;};
 // New stays on what was kept before this visit, so it can clear the sidebar's dot at once without hiding what's new.
 const [seenBefore,setSeenBefore]=useState<number|undefined>(undefined);
 async function markSeen(view:ProjectMemoryView){
  setSeenBefore(current=>current??view.seen_at??0);
  try{const seen=asView(await local('knowledge_notes_seen',scope));client.setQueryData(['knowledge-unread',projectId,profileId],seen);}
  catch{/* Until the host has the operation (b16/bridge), New and the dot follow the last recorded visit. */}
 }
 async function loadMemory(){try{const view=await notes('knowledge_notes_list');setMemoryError('');void markSeen(view);}catch(failure){setMemoryError(reason(failure));}}
 useEffect(()=>{if(open){setSeenBefore(undefined);void loadMemory();}},[open,projectId,profileId]);
 const retainedSource=!!sourceTask&&!!sourceRun&&!sourceTasks.some(task=>task.id===sourceTask&&sourceChannelId===sourceChannel&&task.runs.some(value=>(value as {id:string}).id===sourceRun));

 // One notice line under the lead, with Undo where undo exists. It stays until the next action or eight seconds.
 function show(text:string,undo?:()=>Promise<unknown>,tone:Notice['tone']='done'){setNoticeState({text,undo,tone});}
 useEffect(()=>{if(!notice)return;const timer=setTimeout(()=>setNoticeState(current=>current===notice?null:current),NOTICE_MS);return()=>clearTimeout(timer);},[notice]);
 async function undo(action:()=>Promise<unknown>){
  setNoticeState(null);setBusy('undo');
  try{await action();show(t('Undone.'));}catch(failure){show(`Couldn’t undo it. ${reason(failure)}`,undefined,'error');}finally{setBusy('');}
 }

 function change(patch:Partial<Draft>){
  const next={...draftRef.current,...patch,revision:Math.max(draftRef.current.revision,readDraft().revision)+1};
  draftRef.current=next;setDraft(next);
  try{localStorage.setItem(storageKey,JSON.stringify(next));window.dispatchEvent(new CustomEvent('dervo-knowledge-draft',{detail:storageKey}));}catch{setError(t('Draft storage is unavailable. Keep this page open until you’ve saved your text.'));}
 }
 useEffect(()=>{
  const sync=()=>{const next=readDraft();draftRef.current=next;setDraft(next);};
  const changed=(event:Event)=>{if((event as CustomEvent<string>).detail===storageKey)sync();};
  const stored=(event:StorageEvent)=>{if(event.key===storageKey)sync();};
  window.addEventListener('dervo-knowledge-draft',changed);window.addEventListener('storage',stored);
  sync();return()=>{window.removeEventListener('dervo-knowledge-draft',changed);window.removeEventListener('storage',stored);};
 },[storageKey]);
 const setText=(text:string)=>change({text}),setProvenance=(provenance:string)=>change({provenance});
 const setSourceChannel=(sourceChannel:string)=>change({sourceChannel,sourceTask:'',sourceRun:''}),setSourceMessage=(sourceMessage:string)=>change({sourceMessage,sourceTask:'',sourceRun:''});
 async function refresh(){const value=await local<Data>('knowledge_list',scope);setData(value);setLoadError('');client.setQueryData(['knowledge-waiting',projectId,profileId],value);return value;}
 async function load(){setBusy('load');try{await refresh();}catch(failure){setLoadError(reason(failure));}finally{setBusy('');}}
 /** Runs one change. A failure shows beside the editor, or in the notice line. */
 async function act(label:string,fn:()=>Promise<void>,{where='notice'}:{where?:'notice'|'editor'}={}){
  setBusy(label);setError('');setNoticeState(null);
  try{await fn();}
  catch(failure){if(where==='editor')setError(reason(failure));else show(reason(failure),undefined,'error');}
  finally{setBusy('');}
 }
 // Opens on the rules as last read (the sidebar dot's own read keeps them under this key) and refreshes behind them, rather than
 // blanking to "Reading this project’s rules…" every time.
 useEffect(()=>{let active=true;if(open){setData(client.getQueryData<Data>(['knowledge-waiting',projectId,profileId])??null);setBusy('load');setLoadError('');void local<Data>('knowledge_list',scope).then(value=>{if(active){setData(value);client.setQueryData(['knowledge-waiting',projectId,profileId],value);}}).catch(e=>{if(active)setLoadError(reason(e));}).finally(()=>{if(active)setBusy('');});}return()=>{active=false;};},[open,projectId,channelId,profileId]);
 function reset(){change(emptyDraft());}
 function selectEdit(r:Rule,v:Version){navigation.current++;setEditor(true);change({editing:r,text:v.text,provenance:v.provenance,sourceChannel:v.source?.channelId??'',sourceMessage:v.source?.messageId??'',sourceTask:v.source?.taskId??'',sourceRun:v.source?.runId??''});setPendingEdit(null);setConfirm(null);requestAnimationFrame(()=>textRef.current?.focus());}
 useEffect(()=>{if(confirm||pendingEdit){decisionRef.current?.focus();decisionRef.current?.scrollIntoView({block:'nearest'});}},[confirm,pendingEdit]);
 useEffect(()=>{if(open)requestAnimationFrame(()=>titleRef.current?.focus());},[open]);
 function leave(){navigation.current++;setConfirm(null);setPendingEdit(null);}
 function openEditor(){leave();setEditor(true);requestAnimationFrame(()=>textRef.current?.focus());}
 function closeEditor(){leave();setEditor(false);}
 // ⌘K's Add guidance: the cursor lands in the add line, or in the draft you left. The shell clears the request.
 useEffect(()=>{if(open&&addRequest){if(draftRef.current.text||draftRef.current.editing)openEditor();else requestAnimationFrame(()=>addRef.current?.focus());onAddHandled?.();}},[open,addRequest]);

 // ---------- Undo helpers: every one reads the rule again, so it acts on the revision that's there now.
 async function latest(id:string){const value=await local<Data>('knowledge_list',scope);return value.rules.find(rule=>rule.id===id);}
 async function archiveNow(id:string){const rule=await latest(id);if(rule?.active_version_id)await local('knowledge_archive',{...scope,ruleId:id,expectedRevision:rule.revision});await refresh();}
 async function useNow(id:string,versionId:string){const rule=await latest(id);if(rule&&rule.active_version_id!==versionId)await local('knowledge_approve',{...scope,ruleId:id,versionId,expectedRevision:rule.revision});await refresh();}
 function freshLine(id:string,line:string){setFresh(current=>({...current,[id]:line}));}

 /** Saved and put to use in one step, so every agent here follows it from the next task. Undo archives it. */
 async function saveAndUse(args:{ruleId:string|null;expectedRevision:number;text:string;provenance:string;source:unknown}){
  const saved=await local<Rule>('knowledge_save',{...scope,...args});
  const version=saved.versions?.at(-1);
  if(version)await local('knowledge_approve',{...scope,ruleId:saved.id,versionId:version.id,expectedRevision:saved.revision});
  return saved;
 }
 async function addRule(){
  const words=adding.trim();
  if(!words||busy)return;
  await act('add',async()=>{
   const saved=await saveAndUse({ruleId:null,expectedRevision:0,text:words,provenance:t('Added in Dervo.'),source:null});
   setAdding('');const value=await refresh();freshLine(saved.id,t('Added just now'));
   show(`Added as rule ${numberOf(value.rules,saved.id)}. Every agent here follows it from the next task.`,()=>archiveNow(saved.id));
  });
  requestAnimationFrame(()=>addRef.current?.focus());
 }
 async function adopt(p:Procedure){
  await act('adopt',async()=>{
   const saved=await local<Rule>('knowledge_adopt',{...scope,procedureId:p.id});
   const version=saved.versions?.at(-1);
   if(version&&saved.active_version_id!==version.id)await local('knowledge_approve',{...scope,ruleId:saved.id,versionId:version.id,expectedRevision:saved.revision});
   const value=await refresh();freshLine(saved.id,t('Added just now, from Dervo’s procedure'));
   show(`Added as rule ${numberOf(value.rules,saved.id)}. Every agent here follows it from the next task.`,()=>archiveNow(saved.id));
  });
 }
 const openRule=(id:string)=>{navigation.current++;setSelection({ruleId:id,versionId:'',history:false,reading:true});setNoteFocus(value=>value+1);};

 // ---------- Waiting for you
 async function queued(key:string,what:'approve'|'dismiss',fn:()=>Promise<void>){
  setBusy(`queue:${what}:${key}`);setNoticeState(null);setQueueErrors(current=>{const next={...current};delete next[key];return next;});
  // Never rejects: a failure stays on the item's card, and the answer says whether it worked.
  try{await fn();return true;}catch(failure){setQueueErrors(current=>({...current,[key]:reason(failure)}));return false;}finally{setBusy('');}
 }
 const setSkills=(value:SkillListing)=>client.setQueryData(skillsKey(projectId,profileId),value);
 const skillRevision=(id:string)=>client.getQueryData<SkillListing>(skillsKey(projectId,profileId))?.skills.find(skill=>skill.id===id)?.revision;
 const reopenSkill=(id:string,version:number)=>async()=>{setSkills(await local<SkillListing>('skills_review',{projectId,profileId,skillId:id,expectedRevision:skillRevision(id)??0,version,decision:'reopen'}));};
 const reopenLesson=(id:string)=>async()=>{await local('knowledge_lesson_reopen',{...scope,lessonId:id});await refresh();};
 const actions:QueueActions={
  approveLesson:(lesson,words)=>queued(`lesson:${lesson.id}`,'approve',async()=>{
   let rule=await local<Rule>('knowledge_lesson_keep',{...scope,lessonId:lesson.id});
   let version=rule.versions.at(-1)!;
   if(words&&words!==lesson.text){rule=await local<Rule>('knowledge_save',{...scope,ruleId:rule.id,expectedRevision:rule.revision,text:words,provenance:version.provenance,source:version.source});version=rule.versions.at(-1)!;}
   if(rule.active_version_id!==version.id)await local('knowledge_approve',{...scope,ruleId:rule.id,versionId:version.id,expectedRevision:rule.revision});
   const value=await refresh();freshLine(rule.id,words&&words!==lesson.text?t('Approved just now, in your words'):t('Approved just now, from a lesson'));
   show(`Added as rule ${numberOf(value.rules,rule.id)}. Every agent here follows it from the next task.`,reopenLesson(lesson.id));
  }),
  dismissLesson:lesson=>queued(`lesson:${lesson.id}`,'dismiss',async()=>{
   await local('knowledge_lesson_dismiss',{...scope,lessonId:lesson.id});await refresh();
   show(t('Dismissed. It wasn’t added.'),reopenLesson(lesson.id));
  }),
  approveSkill:(skill,version)=>queued(`skill:${skill.id}:${version.version}`,'approve',async()=>{
   setSkills(await local<SkillListing>('skills_review',{projectId,profileId,skillId:skill.id,expectedRevision:skill.revision,version:version.version,decision:'approve'}));
   show(t('{name} is approved. Tasks about it get these steps from now on.',{name:version.name}),reopenSkill(skill.id,version.version));
  }),
  dismissSkill:(skill,version)=>queued(`skill:${skill.id}:${version.version}`,'dismiss',async()=>{
   setSkills(await local<SkillListing>('skills_review',{projectId,profileId,skillId:skill.id,expectedRevision:skill.revision,version:version.version,decision:'reject'}));
   show(t('Dismissed. It wasn’t added.'),reopenSkill(skill.id,version.version));
  }),
  editSkill:(edited,skill,version)=>queued(`skill:${skill.id}:${version.version}`,'approve',async()=>{
   const same=edited.name===version.name&&edited.description.trim()===version.description&&edited.body.trim()===version.body.trim();
   if(same){if(!await actions.approveSkill(skill,version))throw new Error(queueErrorsRef.current[`skill:${skill.id}:${version.version}`]??t('Couldn’t approve it.'));return;}
   const saved=await local<SkillListing>('skills_save',{projectId,profileId,skillId:skill.id,expectedRevision:skill.revision,name:edited.name,description:edited.description,body:edited.body});
   const after=saved.skills.find(entry=>entry.id===skill.id);
   // Your wording replaces the proposal, which stays in the skill's record as set aside.
   setSkills(after?await local<SkillListing>('skills_review',{projectId,profileId,skillId:skill.id,expectedRevision:after.revision,version:version.version,decision:'reject'}):saved);
   show(t('{name} is saved in your words. Tasks about it get these steps from now on.',{name:edited.name}));
  }),
  approveRule:(rule,version)=>queued(`rule:${rule.id}:${version.id}`,'approve',async()=>{
   const before=rule.active_version_id;
   await local('knowledge_approve',{...scope,ruleId:rule.id,versionId:version.id,expectedRevision:rule.revision});
   const value=await refresh();const n=numberOf(value.rules,rule.id);
   if(before)show(`Rule ${n} now uses version ${rule.versions.indexOf(version)+1}.`,()=>useNow(rule.id,before));
   else{freshLine(rule.id,t('Approved just now'));show(t('Added as rule {n}. Every agent here follows it from the next task.',{n}),()=>archiveNow(rule.id));}
  }),
  dismissRule:(rule,version)=>queued(`rule:${rule.id}:${version.id}`,'dismiss',async()=>{
   await local('knowledge_version_dismiss',{...scope,ruleId:rule.id,versionId:version.id,expectedRevision:rule.revision,dismissed:true});await refresh();
   show(rule.active_version_id?t('Dismissed. The version in use doesn’t change.'):t('Dismissed. It wasn’t added.'),async()=>{
    const now=await latest(rule.id);if(now)await local('knowledge_version_dismiss',{...scope,ruleId:rule.id,versionId:version.id,expectedRevision:now.revision,dismissed:false});await refresh();});
  }),
 };

 // ---------- Facts and decisions: every change here is at once, with Undo, and none of it makes a note a rule
 // except Make it a rule, which is your click.
 const facts:FactActions={
  edit:async(entry,text)=>{
   setNoticeState(null);
   const view=await notes('knowledge_note_edit',{entryId:entry.id,text});
   show(t('Saved in your words. Later chats get this wording, and the old one stays in its history.'),()=>notes('knowledge_note_restore',{entryId:entry.id}));
   return view;
  },
  remove:async entry=>{setNoticeState(null);await notes('knowledge_note_remove',{entryId:entry.id});},
  restore:entry=>notes('knowledge_note_restore',{entryId:entry.id}),
  keep:async entry=>{setNoticeState(null);await notes('knowledge_note_keep',{entryId:entry.id});show(t('Kept. It still goes to later chats as a fact, never a rule.'));},
  pin:async(entry,pinned)=>{setNoticeState(null);await notes('knowledge_note_pin',{entryId:entry.id,pinned});
   show(pinned?t('Pinned. It goes with every request and is never retired.'):t('Unpinned. It’s a note like the others again.'),()=>notes('knowledge_note_pin',{entryId:entry.id,pinned:!pinned}));},
  promote:async(entry,text=entry.text)=>{
   setNoticeState(null);
   const saved=await saveAndUse({ruleId:null,expectedRevision:0,text,provenance:`Noticed by an agent in project memory${entry.provider?` (${providerAuthor(entry.provider)})`:''}`,source:null});
   await notes('knowledge_note_remove',{entryId:entry.id});
   const value=await refresh();freshLine(saved.id,t('Made a rule just now, from an agent’s note'));
   show(`Made it rule ${numberOf(value.rules,saved.id)}. Every chat here follows it from the next task.`,async()=>{await archiveNow(saved.id);await notes('knowledge_note_restore',{entryId:entry.id});});
  },
  keepNewer:async older=>{
   setNoticeState(null);await notes('knowledge_note_remove',{entryId:older.id});
   show(t('Kept the newer note. Later chats won’t get the older one.'),()=>notes('knowledge_note_restore',{entryId:older.id}));
  },
  keepBoth:async(older,newer)=>{setNoticeState(null);await notes('knowledge_note_pair_keep',{entryIds:[older.id,newer.id]});show(t('Both stay. Dervo won’t ask about these two again.'));},
 };
 const openSource=(threadId:string,taskId:string)=>onOpenTaskSource?.(threadId,taskId);
 /** A rule in use whose source no longer checks out is saved again without it, in the same words, so tasks get it
  * from the next one (beta.11, F3). Undo puts the linked version back in use. */
 async function unlinkRule(rule:Rule,version:Version){
  await act('unlink',async()=>{
   const before=rule.active_version_id;
   const saved=await saveAndUse({ruleId:rule.id,expectedRevision:rule.revision,text:version.text,provenance:version.provenance,source:null});
   const value=await refresh();const n=numberOf(value.rules,saved.id);
   show(t('Rule {n} goes out again from the next task, without its source.',{n}),before?()=>useNow(rule.id,before):undefined);
  });
 }

 // ---------- Switches
 async function flip(key:string,fn:()=>Promise<unknown>){
  setBusy(`switch:${key}`);setSwitchErrors(current=>({...current,[key]:''}));
  try{await fn();}catch(failure){setSwitchErrors(current=>({...current,[key]:`Couldn’t change it. ${reason(failure)}`}));}finally{setBusy('');}
 }

 // ---------- Repository files
 async function sendFile(file:RepoFile,send:boolean){
  setNoticeState(null);
  try{
   setRepo(await local<RepoListing>('knowledge_repo_send',{...scope,path:file.path,send}));
   if(!send)show(t('{path} isn’t sent any more. The file isn’t changed.',{path:file.path}),async()=>{setRepo(await local<RepoListing>('knowledge_repo_send',{...scope,path:file.path,send:true}));});
   else show(t('{path} is sent again from the next task.',{path:file.path}));
  }catch(failure){show(reason(failure),undefined,'error');throw failure;}
 }
 async function openFile(file:RepoFile,how:'editor'|'finder'){
  try{await local('knowledge_repo_open',{...scope,path:file.path,how});}catch(failure){show(reason(failure),undefined,'error');}
 }

 if(!open)return null;
 const rules=data?.rules??[];
 const used=inUse(rules);
 const archived=rules.filter(rule=>!!archivedVersion(rule));
 const stale=stillTrueRules(rules,Date.now(),stillKept);
 const presets=data?.move_presets??{};
 const movable=used.map((rule,index)=>({rule,version:rule.versions.find(v=>v.id===rule.active_version_id)!,number:index+1})).filter(row=>row.version&&row.rule.id in presets&&!keptRules.includes(row.version.id));
 function moveRules(moves:RuleMove[],kept:string[]){
  if(kept.length){
   const next=[...keptRules.filter(id=>!kept.includes(id)),...kept].slice(-200);
   setKeptRules(next);
   try{localStorage.setItem(keptRuleKey(projectId),JSON.stringify(next));}catch{/* this visit still hides them */}
  }
  if(!moves.length)return;
  void act('move',async()=>{
   const done=await local<{batch:string;moved:unknown[];dropped:unknown[]}>('knowledge_rule_to_note',{...scope,moves,undoBatch:null});
   await refresh();void loadMemory();
   show(t('Moved {moved} to notes and dropped {dropped}. Your rules hold what you write.',{moved:done.moved.length,dropped:done.dropped.length}),async()=>{
    await local('knowledge_rule_to_note',{...scope,moves:null,undoBatch:done.batch});await refresh();void loadMemory();});
  });
 }
 function stillTrue(version:Version){
  const next=[...stillKept.filter(id=>id!==version.id),version.id].slice(-200);
  setStillKept(next);
  try{localStorage.setItem(stillTrueKey(projectId),JSON.stringify(next));}catch{/* this visit still hides it */}
 }
 const lessons=data?.suggestions?.items??[];
 const items=data?queueItems(lessons,skills.data,rules):[];
 const draftWaiting=!!(draft.text||draft.editing)&&!editor;
 const procedures=data?.procedures??[];
 const adoptedIn=(p:Procedure)=>used.find(rule=>rule.procedure_id===p.id);
 const selected=selection.reading?used.find(rule=>rule.id===selection.ruleId):undefined;
 const decision:ReactNode=confirm&&selected&&confirm.rule.id===selected.id?(()=>{
  const n=numberOf(rules,selected.id);
  if(confirm.kind==='use'){const k=selected.versions.findIndex(v=>v.id===confirm.version.id)+1,current=selected.versions.findIndex(v=>v.id===selected.active_version_id)+1;
   return <div className="dv-kn-confirm" role="alertdialog" aria-label={t('Use version {k}',{k})}>
    <p className="dv-kn-what"><b>Use version {k} from the next task?</b> Version {current} stays in the history.</p>
    <div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void act('use',async()=>{
     const before=selected.active_version_id!;
     await local('knowledge_approve',{...scope,ruleId:selected.id,versionId:confirm.version.id,expectedRevision:selected.revision});
     setConfirm(null);await refresh();setNoteFocus(value=>value+1);show(t('Rule {n} now uses version {k}.',{n,k}),()=>useNow(selected.id,before));})}>Use version {k}</button>
     <button type="button" ref={decisionRef} className="dv-btn dv-btn-quiet" onClick={()=>{setConfirm(null);setNoteFocus(value=>value+1);}}>{t('Cancel')}</button></div>
   </div>;}
  const count=selected.versions.length;
  return <div className="dv-kn-confirm" role="alertdialog" aria-label={t('Archive rule {n}',{n})}>
   <p className="dv-kn-what"><b>Archive this rule?</b> New tasks stop getting it. {count>2?t('All {count} versions stay',{count}):count===2?t('Both versions stay'):t('Its version stays')} in its history, and you can restore it.</p>
   <div className="dv-row-actions"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void act('archive',async()=>{
    const before=selected.active_version_id!;
    await local('knowledge_archive',{...scope,ruleId:selected.id,expectedRevision:selected.revision});
    setConfirm(null);setSelection(current=>({...current,reading:false}));await refresh();show(t('Rule {n} is archived. New tasks stop getting it.',{n}),()=>useNow(selected.id,before));})}>Archive rule {n}</button>
    <button type="button" ref={decisionRef} className="dv-btn dv-btn-quiet" onClick={()=>{setConfirm(null);setNoteFocus(value=>value+1);}}>{t('Keep it')}</button></div>
  </div>;
 })():null;

 function save(){
  const submitted=draftRef.current;const navigationAtSave=navigation.current;const previous=submitted.editing?.active_version_id??null;
  void act('save',async()=>{
   if(!sourceTask&&!!sourceChannel!==!!sourceMessage)throw new Error(t('Enter both shared source IDs or leave both empty.'));
   const saved=await saveAndUse({ruleId:editing?.id??null,expectedRevision:editing?.revision??0,text,provenance:provenance.trim()||t('Added in Dervo.'),source:sourceTask?{channelId:sourceChannel,taskId:sourceTask,runId:sourceRun}:sourceChannel?{channelId:sourceChannel,messageId:sourceMessage}:null});
   const persisted=readDraft();const current=draftRef.current.revision>persisted.revision?draftRef.current:persisted;
   if(current.revision===submitted.revision){reset();}else if(current.editing?.id===saved.id){change({...current,editing:{...current.editing,revision:saved.revision+1}});}
   const retainedNewer=current.revision!==submitted.revision;const settledRevision=draftRef.current.revision;
   const value=await refresh();const n=numberOf(value.rules,saved.id);
   const undoSave=previous?()=>useNow(saved.id,previous):()=>archiveNow(saved.id);
   if(retainedNewer||draftRef.current.revision!==settledRevision)show(t('Submitted version saved and in use. The newer changes in your draft are still unsaved.'),undoSave);
   else show(previous?t('Saved. Rule {n} uses your new version from the next task.',{n}):t('Added as rule {n}. Every agent here follows it from the next task.',{n}),undoSave);
   if(!retainedNewer&&draftRef.current.revision===settledRevision&&readDraft().revision===settledRevision&&editorRef.current&&navigation.current===navigationAtSave){
    setSelection({ruleId:saved.id,versionId:'',history:false,reading:true});setEditor(false);setNoteFocus(value=>value+1);}
  },{where:'editor'});
 }
 const ruleEditor=editor&&<form className="dv-row dv-open dv-kn-editor" aria-label={editing?t('Edit this rule'):t('Add a rule with details')} onSubmit={e=>{e.preventDefault();save();}}
  onKeyDown={event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)&&text.trim()&&!busy){event.preventDefault();save();}}}>
  <div>
   <p className="dv-row-name">{editing?`Edit rule ${numberOf(rules,editing.id)||''}`.trim():t('New rule')}</p>
   {pendingEdit&&<div className="dv-kn-confirm" role="alert">
    <p className="dv-kn-what"><b>Your current draft is still here.</b> Discard it to edit the rule you chose?</p>
    <div className="dv-row-actions"><button type="button" ref={decisionRef} className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>setPendingEdit(null)}>{t('Keep current draft')}</button><button type="button" className="dv-btn dv-btn-quiet" onClick={()=>selectEdit(pendingEdit.rule,pendingEdit.version)}>{t('Discard draft and edit the rule')}</button></div>
   </div>}
   <div className="dv-kn-edit">
    <textarea ref={textRef} aria-label={t('Rule text')} value={text} onChange={e=>setText(e.target.value)} required maxLength={4096}/>
    <span className="dv-kn-hint">{t('Saving puts it to use from the next task. Every version stays in its history. ⌘Return saves.')}</span>
   </div>
   <details className="dv-disclose" open={!!(provenance&&provenance!=='Added in Dervo.')||!!sourceTask||!!sourceChannel||undefined}><summary>{t('More about where it came from')}{CHEVRON}</summary>
    <div className="dv-kn-edit">
     <label className="dv-kn-field">{t('Where it came from')}<input aria-label={t('Where it came from')} placeholder={t('A preference, a project decision or a lesson from past work')} value={provenance} onChange={e=>setProvenance(e.target.value)} maxLength={1024}/></label>
     <label className="dv-kn-field">{t('Learn from a saved task')}
      <select aria-label={t('Lesson source task')} value={sourceTask&&sourceRun?JSON.stringify([sourceTask,sourceRun]):''} onChange={e=>{
       const ids=e.target.value?JSON.parse(e.target.value) as [string,string]:null;
       change({sourceTask:ids?.[0]??'',sourceRun:ids?.[1]??'',sourceChannel:ids?(ids[0]===sourceTask&&ids[1]===sourceRun?sourceChannel:sourceChannelId):'',sourceMessage:''});
      }}><option value="">{t('No task')}</option>{retainedSource&&<option value={JSON.stringify([sourceTask,sourceRun])}>The task you chose earlier{threadName(sourceChannel)?` · ${threadName(sourceChannel)}`:''}</option>}{sourceTasks.flatMap(task=>task.runs.map((value,index)=>{
       const run=value as {id:string;state:string};
       if(!['completed','failed','interrupted','uncertain'].includes(run.state))return null;
       return <option key={run.id} value={JSON.stringify([task.id,run.id])}>{yourWords(task.messages.find(m=>m.role==='user')?.text??'').slice(0,90)||t('Coding task')} · run {index+1} · {run.state==='completed'?'finished':run.state}</option>;
      }))}</select>
     </label>
     <p className="dv-kn-hint">{t('Write what should change next time. Choosing a task links its saved run; it doesn’t copy its answer or claim the task succeeded.')}</p>
     {sourceTask?<p className="dv-kn-hint">Linked to {threadName(sourceChannel)??'a saved task'}, run {sourceRun.slice(0,8)} <button type="button" className="dv-kn-link" onClick={()=>change({sourceTask:'',sourceRun:'',sourceChannel:'',sourceMessage:''})}>{t('Clear lesson source')}</button></p>
      :<details className="dv-disclose"><summary>{t('Link a message it came from')}{CHEVRON}</summary><p>{t('Paste the conversation and message IDs from a shared conversation in this project, so you can find where this came from later. Nothing from the message is copied.')}</p>
       <div className="dv-kn-edit"><label className="dv-kn-field">{t('Source conversation ID')}<input aria-label={t('Source conversation ID')} value={sourceChannel} onChange={e=>setSourceChannel(e.target.value)}/></label><label className="dv-kn-field">{t('Source message ID')}<input aria-label={t('Source message ID')} value={sourceMessage} onChange={e=>setSourceMessage(e.target.value)}/></label></div></details>}
    </div>
   </details>
   {error&&<p className="dv-kn-error" role="alert">{error}</p>}
   <div className="dv-row-actions"><button type="submit" className="dv-btn dv-btn-primary dv-btn-sm" disabled={!!busy||!text.trim()}>{busy==='save'?t('Saving…'):t('Save')}</button>{(text||provenance||editing)&&<button type="button" className="dv-btn dv-btn-quiet" onClick={reset}>{t('Discard')}</button>}<button type="button" className="dv-btn dv-btn-quiet" onClick={closeEditor}>{t('Cancel')}</button></div>
  </div>
 </form>;

 const procedureRows=<div className="dv-sheet">{procedures.map(p=>{const adopted=adoptedIn(p);return <div key={p.id} className="dv-row" data-testid="starter-procedure">
  {PROCEDURE_ICON}
  <div><div className="dv-row-name">{p.title}</div><div className="dv-row-line">{p.text}</div>
   <details className="dv-disclose"><summary>{procedureCredit(p)}{CHEVRON}</summary><p>{p.license}</p></details></div>
  <div className="dv-row-end">{adopted?<button type="button" className="dv-btn dv-btn-quiet" onClick={()=>openRule(adopted.id)}>{t('In your rules')}</button>
   :<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={!!busy} onClick={()=>void adopt(p)}>{t('Use it')}</button>}</div>
 </div>;})}</div>;
 const noRules=!!data&&!used.length;

 // What the head counts as new: notes and agents' skills kept since the visit before this one.
 const keptNew=unreadCount(memoryView,skills.data,seenBefore);
 const repoLine=repo?repositoryLine(repo):null;

 return <section aria-label={t('Project knowledge')} className="dervo-knowledge-workspace" onKeyDown={event=>{
  if(event.key!=='Escape'||event.defaultPrevented)return;
  event.stopPropagation();
  if(confirm){setConfirm(null);setNoteFocus(value=>value+1);return;}
  if(pendingEdit){setPendingEdit(null);return;}
  if(editor){closeEditor();requestAnimationFrame(()=>addRef.current?.focus());return;}
  onClose();
 }}>
  <header className="dv-main-head dervo-knowledge-bar"><span className="dv-title">{projectName??t('Your project')}</span><span className="dv-crumb">{t('Knowledge')}</span><div className="dv-tools"><button type="button" className="dv-btn dv-btn-quiet dervo-knowledge-return" onClick={onClose}>{returnLabel}</button></div></header>
  <div className="dv-column dervo-knowledge-body"><div className="dv-measure">
   <section className="dv-kn-head" aria-label={t('Knowledge')}>
    <h1 className="dv-display-1" ref={titleRef} tabIndex={-1}>{t('What Dervo knows here')}</h1>
    <p className="dv-kn-lead">What agents in {projectName??t('this project')} are told, newest first. Agents save it as they work, and you can change or remove any of it.</p>
    {memoryView&&<p className="dv-kn-meta" data-testid="knowledge-meta">{keptNew>0&&<span><b>{keptNew} new</b> since you last looked</span>}<span>{tokensLine(memoryView)}</span></p>}
   </section>
   {/* The one notice line sits under the lead and stays in view while you work further down the page. */}
   {notice&&<p className="dv-kn-notice" role="status" data-tone={notice.tone}>{notice.tone!=='error'&&<StatusGlyph state="done"/>}<span>{notice.text}{notice.undo&&<> <button type="button" className="dv-kn-undo" disabled={!!busy} onClick={()=>void undo(notice.undo!)}>{t('Undo')}</button></>}</span></p>}

   {data&&<KnowledgeQueue items={items} rulesInUse={used.length} busy={busy} errors={queueErrors} limited={data.suggestions?.limited??false} threadName={threadName}
    limits={skills.data?.limits} actions={actions} onOpenTaskSource={onOpenTaskSource}
    onClearError={key=>{setQueueErrors(current=>{const next={...current};delete next[key];return next;});void load();void skills.refetch();}}
    onMore={()=>void act('more',async()=>{const before=data.suggestions?.nextCursor;if(before==null)return;const next=await local<LessonPage>('knowledge_lesson_list',{...scope,before});setData(current=>current?.suggestions?.nextCursor===before?{...current,suggestions:{...next,items:[...current.suggestions.items,...next.items]}}:current);})}/>}

   <FactsSection view={memoryView} error={!memoryView?memoryError:''} onRetry={()=>void loadMemory()} seenAt={seenBefore} actions={facts}
    threadName={threadName} onOpenSource={onOpenTaskSource?openSource:undefined}/>

   <SkillsPage projectId={projectId} profileId={profileId} onNotice={(text,undoIt)=>show(text,undoIt)} seenAt={seenBefore}
    threadName={threadName} onOpenSource={onOpenTaskSource?openSource:undefined}/>

   <section className="dv-sec" aria-labelledby="dervo-knowledge-rules" data-testid="your-rules">
    <div className="dv-sec-head"><h2 className="dv-title" id="dervo-knowledge-rules">{t('Your rules')}</h2><span className="dv-kn-count">{data&&used.length?used.length:''}</span>
     <span className="dv-caption">{used.length>12?t('Each chat gets the 12 that best match it'):t('Every chat here follows these')}</span></div>
    {loadError&&<p className="dv-kn-line" role="alert">Your rules didn’t load. {loadError} <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={busy==='load'} onClick={()=>void load()}>{t('Try again')}</button></p>}
    {!data&&!loadError&&<p className="dv-kn-line" role="status">{t('Reading this project’s rules…')}</p>}
    {data&&movable.length>0&&<MoveRules rows={movable} presets={presets} busy={!!busy} onMove={moveRules}/>}
    {data&&stale.length>0&&<div className="dv-kn-dup" data-testid="still-true" role="group" aria-label={t('Still true?')}>
     <p>{stale.length===1?tr('{label} This rule says where something was on a day over a week ago. Rules don’t expire, so check it.',{label:<b>{t('Still true?')}</b>}):tr('{label} These rules say where something was on a day over a week ago. Rules don’t expire, so check them.',{label:<b>{t('Still true?')}</b>})}</p>
     {stale.map(({rule,version})=>{const n=numberOf(rules,rule.id);return <div key={version.id} className="dv-kn-still">
      <p><button type="button" className="dv-kn-link" onClick={()=>openRule(rule.id)}>{t('Rule {n}',{n})}</button>, {ruleDate(version.created_at)}: {ruleTitle(version.text)}</p>
      <div className="dv-row-actions">
       <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm dv-kn-pill" disabled={!!busy} aria-label={t('Archive rule {n}',{n})} onClick={()=>{openRule(rule.id);setConfirm({kind:'archive',rule});}}>{t('Archive')}</button>
       <button type="button" className="dv-btn dv-btn-quiet" aria-label={t('Rule {n} is still true',{n})} onClick={()=>stillTrue(version)}>{t('It’s still true')}</button>
      </div></div>;})}
    </div>}
    {data&&<div className="dv-sheet" role="group" aria-labelledby="dervo-knowledge-rules">
     <KnowledgeHistory rules={used} busy={!!busy} selection={selection} focusRequest={noteFocus} lines={freshLines} deliveries={data?.deliveries} decision={decision} onOpenTaskSource={onOpenTaskSource} onUnlink={(rule,version)=>void unlinkRule(rule,version)}
      onSelectionChange={value=>{navigation.current++;setConfirm(null);setSelection(value);}}
      onEdit={(r,v)=>{if(text||provenance||sourceChannel||sourceMessage||editing){setConfirm(null);setPendingEdit({rule:r,version:v});setEditor(true);}else selectEdit(r,v);}}
      onUseVersion={(rule,version)=>{setPendingEdit(null);setConfirm({kind:'use',rule,version});}}
      onArchive={rule=>{setPendingEdit(null);setConfirm({kind:'archive',rule});}}/>
     {ruleEditor}
     {!editor&&<label className="dv-add-line">{PLUS}
      <input ref={addRef} type="text" placeholder={t('Add a rule every agent here follows')} aria-label={t('Add a rule')} value={adding} disabled={busy==='add'} maxLength={4096} onChange={e=>setAdding(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();void addRule();}}}/>
      <span className="dv-hint">{t('Return to add')}</span>
     </label>}
     {!editor&&<div className="dv-kn-add-more">
      <button type="button" className="dv-btn dv-btn-quiet" onClick={()=>{if(adding.trim()&&!draftWaiting)change({text:adding.trim()});setAdding('');openEditor();}}>{draftWaiting?t('Continue draft'):t('Add with details')}</button>
      {!noRules&&procedures.length>0&&<DropdownMenu>
       <DropdownMenuTrigger asChild><button type="button" className="dv-btn dv-btn-quiet">{t('Start from a procedure')}</button></DropdownMenuTrigger>
       <DropdownMenuContent align="start" className="dv-kn-menu">{procedures.map(p=>{const adopted=adoptedIn(p);return <DropdownMenuItem key={p.id} onSelect={()=>{if(adopted)openRule(adopted.id);else setTimeout(()=>void adopt(p),0);}}>
        <span className="dv-kn-menu-item"><b>{p.title}</b><small>{adopted?t('In your rules'):p.text.split('. ')[0].replace(/\.$/,'')+'.'}</small></span></DropdownMenuItem>;})}</DropdownMenuContent>
      </DropdownMenu>}
     </div>}
    </div>}
    {noRules&&procedures.length>0&&<><p className="dv-kn-line dv-kn-procedures">{t('Or start from one of Dervo’s procedures. It becomes a rule you can change.')}</p>{procedureRows}</>}
    {archived.length>0&&<details className="dv-disclose"><summary>Archived · {archived.length}{CHEVRON}</summary>
     <div className="dv-sheet">{archived.map(rule=>{const version=archivedVersion(rule)!;return <div key={rule.id} className="dv-row"><span aria-hidden="true"/>
      <span className="dv-row-text"><span className="dv-row-name">{ruleTitle(version.text)}</span><span className="dv-row-line">Archived · {rule.versions.length} {rule.versions.length===1?'version':'versions'}</span></span>
      <span className="dv-row-end"><button type="button" className="dv-btn dv-btn-quiet" disabled={!!busy} onClick={()=>void act('restore',async()=>{await local('knowledge_approve',{...scope,ruleId:rule.id,versionId:version.id,expectedRevision:rule.revision});
       const value=await refresh();show(`Restored as rule ${numberOf(value.rules,rule.id)}.`,()=>archiveNow(rule.id));})}>{t('Restore')}</button></span></div>;})}</div>
    </details>}
   </section>

   <footer className="dv-kn-foot">
    {repoLine&&<p className="dv-kn-foot-line" data-testid="repository-line">{repoLine}</p>}
    {!repo&&repoError&&<p className="dv-kn-foot-line" role="alert">The repository’s files didn’t load. {repoError} <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={()=>void loadRepo()}>{t('Try again')}</button></p>}
    {repo&&(repo.files.length>0||repo.missing?.length)&&<details className="dv-disclose"><summary>{t('Which repository files go')}{CHEVRON}</summary>
     <div className="dv-kn-grp"><KnowledgeRepo listing={repo} busy={!!busy} onSend={sendFile} onOpen={openFile}/></div></details>}
    <details className="dv-disclose dv-kn-learn"><summary>{t('Memory settings')}{CHEVRON}</summary>
     <p>{t('Every chat here starts with the repository’s files, then your rules, then what agents kept. Your rules come after the files, so when they disagree, yours win. What agents keep is a background fact or a skill, never a rule, and it never gives an agent new permissions. All of it is stored on your Mac and sent only with your chats.')}</p>
     <div>
      <SettingRow name={t('Keep agents’ notes')} line={t('Agents save facts and decisions as they work, and every chat here starts with them. Off, they don’t see or save notes. Saved notes stay.')}
       disabled={!memoryView} why={switchErrors.notes||(!memoryView&&memoryError?t('Agents’ notes didn’t load, so this can’t change yet.'):undefined)}
       end={<Switch label={t('Keep agents’ notes')} checked={!!memoryView?.enabled} disabled={!memoryView||busy==='switch:notes'}
        onChange={enabled=>void flip('notes',()=>notes('knowledge_notes_set',{enabled}))}/>}/>
      <SettingRow name={t('Save lessons')} line={t('When a shared chat ends, the agent can save one lesson as a note. Private conversations are excluded.')}
       why={switchErrors.lessons||undefined} end={<Switch label={t('Save lessons')} checked={data?.lesson_suggestions_enabled===true} disabled={!data||busy==='switch:lessons'}
        onChange={enabled=>void flip('lessons',async()=>{await local('knowledge_lessons_set',{...scope,enabled});await refresh();})}/>}/>
      <SettingRow name={t('Look back before finishing')} line={t('Before its final answer, the agent checks once for a note or a skill worth keeping. It uses a little more of each run.')}
       disabled={!memoryView?.enabled} why={switchErrors.reflect||(memoryView&&!memoryView.enabled?t('Needs Keep agents’ notes on.'):undefined)}
       end={<Switch label={t('Look back before finishing')} checked={!!memoryView?.enabled&&!!memoryView?.reflect} disabled={!memoryView?.enabled||busy==='switch:reflect'}
        onChange={enabled=>void flip('reflect',()=>notes('knowledge_notes_reflect_set',{enabled}))}/>}/>
     </div>
    </details>
   </footer>
  </div></div>
 </section>;
}

/** The repository's files in one line (b16-memory/knowledge-lower.png): "Also sent to every chat: AGENTS.md from the
 * repository, 4.3 KB. Change it in your editor." Files you stopped sending aren't counted. */
export function repositoryLine(listing:RepoListing):ReactNode{
 const sent=listing.files.filter(file=>!(file as {error?:string}).error&&!(file as {stopped?:boolean}).stopped&&!(file as {empty?:boolean}).empty) as {path:string;sent_bytes?:number;bytes?:number}[];
 if(!sent.length)return listing.files.length?t('No repository files are sent to chats here.'):null;
 const bytes=sent.reduce((sum,file)=>sum+(file.sent_bytes??file.bytes??0),0);
 const size=bytes<1024?t('{bytes} bytes',{bytes}):`${(bytes/1024).toFixed(bytes<10240?1:0)} KB`;
 const names=sent.map((file,index)=><Fragment key={file.path}>{index?index===sent.length-1?' and ':', ':''}<code>{file.path}</code></Fragment>);
 return <>Also sent to every chat: {names} from the repository, {size}. Change {sent.length===1?'it':'them'} in your editor.</>;
}

type KnowledgeSnapshot={text:string;captured_at:string;omitted:number;selection?:{method:string;eligible:number;detail:string}};
/** The per-run delivery record, kept in the Record for support. "What it was told" (RunContext.tsx) is the view people read. */
export function KnowledgeSnapshots({runs}:{runs:unknown[]}){
 return <details className="text-sm">
  <summary className="cursor-pointer">{t('Project knowledge per run')}</summary>
  <p>{t('Saved before sending. Check the run status for the delivery outcome.')}</p>
  {runs.map((value,i)=>{
   const run=value as {id:string;knowledge_snapshot?:KnowledgeSnapshot&{repository?:{text?:string}}};
   const snapshot=run.knowledge_snapshot;
   return <div className="space-y-2 py-2" key={run.id??i}>
    <p className="break-all">Run {run.id}</p>
    {snapshot?<>
     <p>{snapshot.captured_at} · {snapshot.omitted} rules omitted by source checks or limits</p>
     {snapshot.selection&&<p className="text-xs text-muted-foreground">{snapshot.selection.eligible} approved rules passed source checks. {snapshot.selection.detail}</p>}
     {snapshot.repository?.text&&<pre className="whitespace-pre-wrap break-all text-xs">{snapshot.repository.text}</pre>}
     <pre className="whitespace-pre-wrap break-all text-xs">{snapshot.text||t('No approved rules delivered.')}</pre>
    </>:<p>{t('No knowledge delivery snapshot recorded. Helper retrieval is unsupported.')}</p>}
   </div>;
  })}
 </details>;
}

export function KnowledgeSnapshotsForTask({projectId,channelId,taskId}:{projectId:string;channelId:string;taskId:string}){
 const detail=useQuery({queryKey:['task-detail',projectId,channelId,taskId],refetchInterval:700,queryFn:()=>local<{task:{runs:unknown[]}}>('task_detail',{projectId,channelId,taskId})});
 if(detail.error)return <p role="alert">{t('Couldn\'t load project knowledge delivery records.')}</p>;
 if(!detail.data)return <p role="status">{t('Loading project knowledge delivery records...')}</p>;
 return <KnowledgeSnapshots runs={detail.data.task.runs}/>;
}
