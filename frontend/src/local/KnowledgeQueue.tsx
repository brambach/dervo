import {useEffect,useRef,useState,type ReactNode} from 'react';
import {StatusGlyph} from './StatusGlyph';
import {providerAuthor} from './providerLabel';
import {ago} from './ProjectMemory';
import {ruleDate,ruleOrigin,ruleTitle,shownVersion,waitingVersion,type Rule,type Version} from './KnowledgeNotebook';
import {SkillEditor,type Editing,type Skill,type SkillListing,type SkillVersion} from './ProjectSkills';

/** What still waits for a click on the Knowledge page. Since beta.16 (b16/memory) agents' notes, lessons and skills
 * save at once, so this only holds suggestions saved before that, rule versions you saved without putting them in use,
 * and SKILL.md files you imported (the repository's words, not yours). Oldest first. The first is open on the white card
 * with Why, From and Becomes, and Approve, Edit first and Dismiss; the rest are one-line rows that open in place. The page
 * does the work and offers Undo; this component only lays the queue out, and shows nothing when nothing waits. */
export type LessonSuggestion={id:string;text:string;evidence:string;provider:string;run_state:string;task_state:string;created_at?:string;
 source:{channelId:string;taskId:string;runId:string}};

type Item=
 |{kind:'lesson';key:string;at:number;lesson:LessonSuggestion}
 |{kind:'skill';key:string;at:number;skill:Skill;version:SkillVersion}
 |{kind:'rule';key:string;at:number;rule:Rule;version:Version;number:number};

/** Each action settles to whether it worked; a failure shows on the item's own card. */
export type QueueActions={
 approveLesson:(lesson:LessonSuggestion,text?:string)=>Promise<boolean>;dismissLesson:(lesson:LessonSuggestion)=>Promise<boolean>;
 approveSkill:(skill:Skill,version:SkillVersion)=>Promise<boolean>;dismissSkill:(skill:Skill,version:SkillVersion)=>Promise<boolean>;
 editSkill:(editing:Editing,skill:Skill,version:SkillVersion)=>Promise<boolean>;
 approveRule:(rule:Rule,version:Version)=>Promise<boolean>;dismissRule:(rule:Rule,version:Version)=>Promise<boolean>};

/** A lesson's evidence line, as the old rows wrote it; kept for the From line. */
export function lessonLine(item:Pick<LessonSuggestion,'provider'|'run_state'>):string{
 return `Suggested by ${providerAuthor(item.provider)} · its run ${item.run_state==='completed'?'finished':item.run_state}`;
}
function when(value:string|number|undefined):number{
 if(typeof value==='number')return value*1000;
 const parsed=value?Date.parse(value):NaN;
 return Number.isNaN(parsed)?0:parsed;
}
function day(value:string|number|undefined):string{
 const at=when(value);
 if(!at)return '';
 return Date.now()-at<86400000?ago(at/1000):ruleDate(new Date(at).toISOString());
}
/** Every waiting item, oldest first. Rules number from what's in use; a draft becomes the next rule. */
export function queueItems(lessons:LessonSuggestion[],skills:SkillListing|undefined,rules:Rule[]):Item[]{
 const inUse=rules.filter(rule=>rule.active_version_id);
 const items:Item[]=[
  ...lessons.map(lesson=>({kind:'lesson' as const,key:`lesson:${lesson.id}`,at:when(lesson.created_at),lesson})),
  ...(skills?.skills??[]).flatMap(skill=>skill.proposals.map(version=>({kind:'skill' as const,key:`skill:${skill.id}:${version.version}`,at:when(version.created_at),skill,version}))),
  ...rules.flatMap(rule=>{const version=waitingVersion(rule);return version?[{kind:'rule' as const,key:`rule:${rule.id}:${version.id}`,at:when(version.created_at),rule,version,number:inUse.indexOf(rule)+1}]:[];}),
 ];
 return items.sort((a,b)=>a.at-b.at);
}
function label(item:Item):string{
 if(item.kind==='lesson')return 'Lesson';
 if(item.kind==='skill')return item.version.origin==='imported'?'Imported skill':item.skill.current?'Skill update':'New skill';
 return 'Rule';
}

type Props={items:Item[];rulesInUse:number;busy:string;errors:Record<string,string>;limited:boolean;threadName:(channelId:string)=>string|undefined;
 limits?:SkillListing['limits'];actions:QueueActions;onMore:()=>void;onOpenTaskSource?:(channelId:string,taskId:string)=>void;onClearError:(key:string)=>void};

export function KnowledgeQueue({items,rulesInUse,busy,errors,limited,threadName,limits,actions,onMore,onOpenTaskSource,onClearError}:Props){
 const [openKey,setOpenKey]=useState('');
 const [editing,setEditing]=useState<{key:string;text:string}|null>(null);
 const [skillEdit,setSkillEdit]=useState<{key:string;editing:Editing}|null>(null);
 const heading=useRef<HTMLDivElement>(null),field=useRef<HTMLTextAreaElement>(null),moved=useRef(false);
 const open=items.find(item=>item.key===openKey)??items[0];
 useEffect(()=>{if(editing)requestAnimationFrame(()=>{const f=field.current;if(f){f.focus();f.setSelectionRange(f.value.length,f.value.length);}});},[editing?.key]);
 useEffect(()=>{if(moved.current){moved.current=false;heading.current?.focus();}},[open?.key]);
 if(!items.length)return null;
 const pending=busy.startsWith('queue:');
 function choose(item:Item){setOpenKey(item.key);setEditing(null);setSkillEdit(null);moved.current=true;}
 function from(channelId:string|undefined,taskId:string|undefined,who:string,tail:string):ReactNode{
  const name=channelId?threadName(channelId):undefined;
  return <>{who}{name&&<> in {onOpenTaskSource&&taskId?<button type="button" className="dv-kn-link" onClick={()=>onOpenTaskSource(channelId!,taskId)}>{name}</button>:name}</>}{tail}</>;
 }
 function closedLine(item:Item):string{
  if(item.kind==='lesson'){const name=threadName(item.lesson.source.channelId);return `Suggested by ${providerAuthor(item.lesson.provider)}${name?` in ${name}`:''}${item.lesson.created_at?` · ${day(item.lesson.created_at)}`:''}`;}
  if(item.kind==='skill'){const v=item.version;return v.origin==='imported'?`${v.note||'Imported from the project'} · ${day(v.created_at)}`:`${v.origin==='agent'?`Proposed by ${providerAuthor(v.author)}`:'Proposed'} · ${day(v.created_at)}`;}
  return `${item.rule.active_version_id?`New version of rule ${item.number}`:'Draft rule'} · saved by you · ${day(item.version.created_at)}`;
 }
 function title(item:Item):string{
  if(item.kind==='lesson')return item.lesson.text;
  if(item.kind==='skill')return item.version.description;
  return ruleTitle(item.version.text);
 }
 function facts(item:Item):ReactNode{
  if(item.kind==='lesson'){const l=item.lesson;return <dl className="dv-facts">
   <dt>Why</dt><dd>{l.evidence}</dd>
   <dt>From</dt><dd>{from(l.source.channelId,l.source.taskId,providerAuthor(l.provider),`, when its run ${l.run_state==='completed'?'finished':l.run_state==='failed'?'failed':`was ${l.run_state}`}${l.created_at?` · ${day(l.created_at)}`:''}`)}</dd>
   {editing?.key!==item.key&&<><dt>Becomes</dt><dd>Rule {rulesInUse+1}, for every task here</dd></>}
  </dl>;}
  if(item.kind==='skill'){const v=item.version;return <><dl className="dv-facts">
   <dt>Name</dt><dd><code>{v.name}</code></dd>
   {v.note&&v.origin==='agent'&&<><dt>Why</dt><dd>{v.note}</dd></>}
   <dt>From</dt><dd>{v.origin==='imported'?(v.note||'Imported from the project'):v.origin==='agent'?`Proposed by ${providerAuthor(v.author)}`:'Proposed'} · {day(v.created_at)}</dd>
   <dt>Becomes</dt><dd>{item.skill.current?`Version ${v.version} of ${item.skill.name}, replacing version ${item.skill.current.version}`:'A skill, sent only with a task about this job'}</dd>
  </dl>
   <details className="dv-disclose"><summary>The steps<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg></summary><p className="dv-kn-steps">{v.body}</p></details>
   {item.skill.current&&<details className="dv-disclose"><summary>The approved version it would replace<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg></summary><p className="dv-kn-steps">{item.skill.current.body}</p></details>}
  </>;}
  const current=item.rule.active_version_id?shownVersion(item.rule):undefined;
  return <dl className="dv-facts">
   {current&&<><dt>Now</dt><dd>{current.text}</dd></>}
   <dt>From</dt><dd>Saved by you, {ruleOrigin(item.version.provenance)} · {day(item.version.created_at)}</dd>
   <dt>Becomes</dt><dd>{current?`Rule ${item.number}, version ${item.rule.versions.indexOf(item.version)+1}. The version in use now stays in its history.`:`Rule ${rulesInUse+1}, for every task here`}</dd>
  </dl>;
 }
 function approve(item:Item,text?:string){
  if(item.kind==='lesson')return actions.approveLesson(item.lesson,text);
  if(item.kind==='skill')return actions.approveSkill(item.skill,item.version);
  return actions.approveRule(item.rule,item.version);
 }
 function dismiss(item:Item){
  if(item.kind==='lesson')return actions.dismissLesson(item.lesson);
  if(item.kind==='skill')return actions.dismissSkill(item.skill,item.version);
  return actions.dismissRule(item.rule,item.version);
 }
 const error=open?errors[open.key]:'';
 return <section className="dv-sec" aria-labelledby="dervo-knowledge-waiting" data-testid="knowledge-queue">
  <div className="dv-sec-head"><h2 className="dv-title" id="dervo-knowledge-waiting">Waiting for you</h2><span className="dv-kn-count">{items.length}{limited?'+':''}</span>
   <span className="dv-caption">Saved before Dervo stopped asking, or imported from a file</span></div>
  <div className="dv-sheet">{items.map(item=>{
   if(item!==open)return <button key={item.key} type="button" className="dv-row" onClick={()=>choose(item)} aria-expanded={false}>
    <StatusGlyph state="needs"/>
    <span className="dv-row-text"><span className="dv-row-name">{title(item)}</span><span className="dv-row-line">{closedLine(item)}</span></span>
    <span className="dv-row-end"><span className="dv-kn-kind">{label(item)}</span></span>
   </button>;
   const editingLesson=editing?.key===item.key;
   const editingSkill=skillEdit?.key===item.key;
   const approving=busy===`queue:approve:${item.key}`;
   return <article key={item.key} className="dv-row dv-open" aria-label={`${label(item)} waiting for you`}
    onKeyDown={event=>{
     if(event.key==='Escape'&&editingLesson){event.stopPropagation();setEditing(null);}
     else if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)&&!editingSkill&&!pending){event.preventDefault();if(editingLesson){if(editing.text.trim())void approve(item,editing.text.trim()).then(ok=>{if(ok)setEditing(null);});}else void approve(item);}
    }}>
    <StatusGlyph state="needs"/>
    <div>
     <div className="dv-row-name" ref={heading} tabIndex={-1}>{editingLesson?'Edit the lesson, then approve it':editingSkill?'Edit the skill, then approve it':title(item)}</div>
     {item.kind==='rule'&&!editingLesson&&item.version.text.trim()!==title(item)&&<p className="dv-kn-text">{item.version.text}</p>}
     {editingLesson&&<div className="dv-kn-edit">
      <textarea ref={field} aria-label="Lesson text" value={editing.text} maxLength={4096} onChange={event=>setEditing({key:item.key,text:event.target.value})}/>
      <span className="dv-kn-hint">{editing.text.trim()?'Your wording is what agents get. ⌘Return approves it.':'Write the lesson first.'}</span>
     </div>}
     {editingSkill&&limits&&<SkillEditor editing={skillEdit.editing} limits={limits} busy={pending} saveLabel="Approve this version"
      onChange={patch=>setSkillEdit({key:item.key,editing:{...skillEdit.editing,...patch}})}
      onSave={()=>{if(item.kind==='skill')void actions.editSkill(skillEdit.editing,item.skill,item.version).then(ok=>{if(ok)setSkillEdit(null);});}}
      onCancel={()=>{setSkillEdit(null);heading.current?.focus();}}/>}
     {!editingSkill&&facts(item)}
     {error&&<p className="dv-kn-error" role="alert">{error} <button type="button" className="dv-kn-link" onClick={()=>onClearError(item.key)}>Reload</button></p>}
     {!editingSkill&&<div className="dv-row-actions">
      {editingLesson?<>
       <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={pending||!editing.text.trim()} onClick={()=>void approve(item,editing.text.trim()).then(ok=>{if(ok)setEditing(null);})}>{approving?'Approving…':'Approve this wording'}</button>
       <button type="button" className="dv-btn dv-btn-quiet" disabled={pending} onClick={()=>{setEditing(null);heading.current?.focus();}}>Cancel</button>
      </>:<>
       <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={pending} onClick={()=>void approve(item)}>{approving?'Approving…':'Approve'}</button>
       {item.kind==='lesson'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={pending} onClick={()=>setEditing({key:item.key,text:item.lesson.text})}>Edit first</button>}
       {item.kind==='skill'&&<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={pending} onClick={()=>setSkillEdit({key:item.key,editing:{skill:item.skill,name:item.version.name,description:item.version.description,body:item.version.body,replacing:item.version}})}>Edit first</button>}
       <button type="button" className="dv-btn dv-btn-quiet" disabled={pending} onClick={()=>void dismiss(item)}>{busy===`queue:dismiss:${item.key}`?'Dismissing…':'Dismiss'}</button>
      </>}
     </div>}
    </div>
    <div className="dv-row-end"><span className="dv-kn-kind">{label(item)}</span></div>
   </article>;
  })}
  {limited&&<button type="button" className="dv-btn dv-btn-quiet dv-kn-more" disabled={!!busy} onClick={onMore}>{busy==='more'?'Loading…':'Show older'}</button>}
  </div>
 </section>;
}
