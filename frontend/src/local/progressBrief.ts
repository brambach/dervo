import {yourWords} from './timelineRows';
import {activityBasename} from './DervoActivity';
import {providerAuthor} from './providerLabel';
import type {CodingTask,TaskToken} from './CodingTasks';
import {nativeStep} from './nativeSteps';
import {formatList,language,t} from '@/i18n';

/** Progress answers what people check an agent for: what it's doing, why that matters to the project, and where it is in
 * its plan. The plan, the step and the reason are the agent's own words from dervo_update_task_guide for this run,
 * labelled as reported; the live activity line under them and the result lines come from Dervo's record of tool calls.
 * Which files it opened and how many tokens it used are left to the activity log and usage screens. Nothing is predicted. */

const record=(value:unknown):Record<string,unknown>=>typeof value==='object'&&value!==null?value as Record<string,unknown>:{};
const time=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)&&value>0?value:undefined;
const LIVE=new Set(['executing','running','queued','delivering']);
const WRITES=new Set(['dervo_write_file','dervo_edit_file','dervo_create_file','native:Edit','native:Write']);
const NOW:Record<string,string>={
 dervo_read_file:t('Reading'),dervo_write_file:t('Editing'),dervo_edit_file:t('Editing'),dervo_create_file:t('Creating'),dervo_list_files:t('Looking through'),
 dervo_search_files:t('Searching'),dervo_run_tests:t('Running the check'),dervo_request_help:t('Asking a helper'),
 dervo_suggest_lesson:t('Writing down a lesson'),dervo_update_task_guide:t('Noting its plan'),
 dervo_remember:t('Saving to project memory'),dervo_search_history:t('Looking through earlier chats'),
};
const CHECK_NAMES:Record<string,string>={'python-unittest':'Python','node-test':'Node','typescript-check':'TypeScript','rust-check':'Rust','rust-test':'Rust'};
/** After this long with nothing running and nothing new recorded, Progress says so instead of implying motion. */
export const QUIET_SECONDS=90;

export function clip(text:string|undefined,limit:number){
 if(!text)return undefined;
 const flat=text.replace(/\s+/g,' ').trim();
 return flat.length>limit?`${flat.slice(0,limit-1).trimEnd()}…`:flat||undefined;
}
export function names(files:string[],shown=2){
 if(!files.length)return '';
 if(files.length<=shown)return files.length===2?formatList(files):files[0];
 return t('{items} and {count} more',{items:files.slice(0,shown).join(', '),count:files.length-shown});
}
/** "3rd" in English. Another language gets the number and writes its own ordinal mark in the catalog ("{ordinal}ª"). */
const ordinal=(n:number)=>language()!=='en'?String(n):`${n}${n%10===1&&n%100!==11?'st':n%10===2&&n%100!==12?'nd':n%10===3&&n%100!==13?'rd':'th'}`;

function currentRun(task:CodingTask){
 const run=record(task.runs.at(-1)),id=typeof run.id==='string'?run.id:undefined;
 return {run,id,tokens:id?task.tokens.filter(t=>t.run_id===id):[] as TaskToken[]};
}
/** The agent's own plan for this run, or nothing. The same current-run rule as the reported guide. */
function reported(task:CodingTask){
 const {run,id}=currentRun(task),guide=record(run.guide);
 if(!id||guide.run_id!==id)return {};
 const text=(key:string)=>typeof guide[key]==='string'&&(guide[key] as string).trim()?clip(guide[key] as string,280):undefined;
 return {goal:text('goal'),why:text('why'),next:text('next'),at:time(guide.updated_at)};
}
const distinct=(tokens:TaskToken[])=>[...new Set(tokens.map(t=>activityBasename(t.path)).filter((name):name is string=>!!name))];

export type ProgressNow={text:string;file?:string;since?:number;quiet:boolean;repeat?:string};
export type ProgressStep={label:'Changed'|'Checked';text:string;state:'done'|'problem'|'none'};
// i18n-ignore: a step's kind, which tests and callers match on; its text is what's shown
const CHANGED='Changed',CHECKED='Checked';
export type PlanStep={text:string;state:'done'|'now'|'next'};
export type ProgressBrief={title:string;titleSource:'agent'|'request'|'none';now?:ProgressNow;why?:{text:string;source:'agent'|'request'};next?:{text:string;source:'agent'};steps:ProgressStep[];
 plan:PlanStep[];remembered:RememberedNote[];continuity?:string};
/** One line of what this run did to project memory. A save carries the note it made room by dropping, if any. A drop
 * whose save isn't in the record (trimmed, or a second drop for one save) is its own line with no text, so it's never
 * lost. A refused save says what the agent tried to keep. ``removed`` is a note the agent forgot (beta.11, F4), with
 * its id so Undo can bring it back; ``duplicate`` a save the project already had (F5); ``noted`` Dervo's own note from
 * a checked result (P3); ``lesson`` a lesson now waiting in Knowledge (P9); ``withheld`` a note, lesson or skill
 * Dervo refused because it looked like a key or password (its words are never kept, so ``text`` is empty). */
export type RememberedNote={id?:string;text?:string;dropped?:{id:string;text:string;reason?:string};superseded?:{id:string;text:string};refused?:true;reason?:string;removed?:{id:string};duplicate?:true;noted?:true;lesson?:true;withheld?:'note'|'lesson'|'skill'};

/** The agent's reported steps for this run, in order: finished ones, the one it's on, and what it said comes next. */
export function progressPlan(task:CodingTask):PlanStep[]{
 const {run,id}=currentRun(task),guide=record(run.guide);
 if(!id||guide.run_id!==id)return [];
 const reportedSteps=Array.isArray(run.guide_steps)?run.guide_steps.map(item=>clip(String(record(item).step??''),160)).filter((text):text is string=>!!text):[];
 const steps=reportedSteps.length?reportedSteps:[clip(String(guide.step??''),160)].filter((text):text is string=>!!text);
 const working=task.state==='working';
 const plan:PlanStep[]=steps.map((text,index)=>({text,state:working&&index===steps.length-1?'now':'done'}));
 const next=clip(typeof guide.next==='string'?guide.next:undefined,160);
 if(working&&next&&next!==steps.at(-1))plan.push({text:next,state:'next'});
 return plan;
}

/** Notes this run saved to project memory, so you can see what later chats will be told, and any note Dervo dropped
 * to make room for one (beta.11, MEM1), so you can undo that. */
export function progressRemembered(task:CodingTask):RememberedNote[]{
 const {run}=currentRun(task);
 const items=(Array.isArray(run.remembered)?run.remembered:[]).map(record);
 const str=(value:unknown)=>typeof value==='string'&&value?value:undefined;
 const notes:RememberedNote[]=[],saved=new Map<string,RememberedNote>(),drops:{for?:string;dropped:NonNullable<RememberedNote['dropped']>}[]=[];
 for(const item of items){
  const text=str(item.text);
  if(item.action==='dropped'){const id=str(item.entry_id),reason=str(item.reason);if(id&&text)drops.push({for:str(item.for_entry_id),dropped:reason?{id,text,reason}:{id,text}});continue;}
  if(item.action==='withheld'){const what=item.what==='lesson'||item.what==='skill'?item.what:'note';notes.push({text:'',withheld:what});continue;}
  if(!text)continue;
  if(item.action==='refused'){notes.push({text,refused:true,...(typeof item.reason==='string'?{reason:item.reason}:{})});continue;}
  if(item.action==='remove'){const id=str(item.entry_id);notes.push(id?{text,removed:{id}}:{text,removed:{id:''}});continue;}
  if(item.action==='duplicate'){notes.push({text,duplicate:true});continue;}
  if(item.action==='noted'){notes.push({text,noted:true});continue;}
  if(item.action==='lesson'){notes.push({text,lesson:true});continue;}
  // A correction that took an older note's place (critical review 20 Sep, #1): the older note's words ride along
  // with an Undo, the way a note dropped to make room does.
  if(item.action!=='add'&&item.action!=='replace'&&item.action!=='supersede')continue;
  const note:RememberedNote={text};
  if(item.action==='supersede'){const old=str(item.superseded_id),previous=str(item.previous),own=str(item.entry_id);if(old&&previous)note.superseded={id:old,text:previous};if(own)note.id=own;}
  notes.push(note);
  const id=str(item.entry_id);
  if(id)saved.set(id,note);
 }
 for(const drop of drops){
  const owner=drop.for?saved.get(drop.for):undefined;
  if(owner&&!owner.dropped)owner.dropped=drop.dropped;
  else notes.push({dropped:drop.dropped});
 }
 return notes;
}

/** Only a break in context is worth a line: a message that continued its session says nothing. */
export function progressContinuity(task:CodingTask){
 const continuity=record((task as unknown as Record<string,unknown>).continuity);
 return continuity.state==='fresh'&&typeof continuity.reason==='string'?t('New session. {reason}',{reason:continuity.reason}):undefined;
}

/** The live step: the running tool call and its file, or the time since the last recorded step when nothing runs. */
export function progressNow(task:CodingTask,nowSeconds:number):ProgressNow|undefined{
 if(task.state!=='working')return undefined;
 const {tokens}=currentRun(task);
 const live=[...tokens].reverse().find(t=>LIVE.has(t.state));
 const who=providerAuthor(task.binding.provider);
 if(live){
  const raw=record(live),native=nativeStep(live);
  if(native)return {text:native.present,file:native.subject?(native.file?activityBasename(native.subject)??native.subject:native.subject):undefined,since:time(raw.started_at),quiet:false};
  const file=activityBasename(live.path);
  const verb=NOW[live.tool]??t('Working');
  const checkName=CHECK_NAMES[task.check_profile??''];
  const text=live.tool==='dervo_run_tests'?(checkName?t('Running the {check}',{check:t('{name} check',{name:checkName})}):t('Running the configured check')):verb;
  let repeat:string|undefined;
  if(WRITES.has(live.tool)&&file){
   const count=tokens.filter(t=>WRITES.has(t.tool)&&activityBasename(t.path)===file).length;
   if(count>=3)repeat=t('{ordinal} edit to this file',{ordinal:ordinal(count)});
  }
  return {text:live.state==='queued'?t('Waiting to start: {step}',{step:`${text.charAt(0).toLowerCase()}${text.slice(1)}`}):text,file:live.tool==='dervo_run_tests'?undefined:file,since:time(raw.started_at),quiet:false,repeat};
 }
 const last=tokens.at(-1),ended=last?time(record(last).ended_at)??time(record(last).started_at):undefined;
 const quiet=!!ended&&nowSeconds-ended>=QUIET_SECONDS;
 if(!last)return {text:t('{who} is reading your request',{who}),quiet:false};
 return {text:quiet?t('No new step recorded'):t('{who} is deciding the next step',{who}),since:ended,quiet};
}

/** The result in at most two lines: what changed and how it was checked. An answer changes nothing, so it has none. */
export function progressSteps(task:CodingTask):ProgressStep[]{
 const {id,tokens}=currentRun(task);
 const delivered=tokens.filter(t=>t.state==='delivered');
 const ok=delivered.filter(t=>t.success!==false);
 const steps:ProgressStep[]=[];
 const completion=task.completion&&task.completion.run_id===id&&!task.completion.stale?task.completion:undefined;
 if(task.state==='answered')return steps;
 const written=completion?completion.changed_files.map(f=>activityBasename(f)??f):distinct(ok.filter(t=>WRITES.has(t.tool)));
 const halted=task.state==='blocked';
 const unconfirmed=task.state==='disconnected'||task.runs.some(r=>record(r).state==='uncertain');
 if(written.length)steps.push({label:CHANGED,text:`${names(written)}${halted&&!completion?t(', before it stopped'):''}`,state:'done'});
 else if(!task.read_only&&(completion||task.state==='blocked'||task.state==='idle'))steps.push({label:CHANGED,text:t('No files changed'),state:'none'});
 const checks=delivered.filter(t=>t.tool==='dervo_run_tests');
 const name=CHECK_NAMES[task.check_profile??''];
 const checkName=name?t('{name} check',{name}):t('Check');
 // Only this run's completion speaks for review; an earlier run's pass says nothing about this one.
 const review=completion?.review_phase;
 const reviewed=review==='passed'?t(', and independent review passed'):review==='unresolved'||review==='failed'?t(', but independent review found problems'):'';
 const exit=completion&&typeof completion.test_exit_status==='number'?completion.test_exit_status:undefined;
 if(task.full_access){
  // Full access: Claude Code ran its own commands. Dervo ran no check, and says so.
  const commands=delivered.filter(t=>t.tool==='native:Bash').length,shots=ok.filter(t=>(t.path??'').startsWith('screenshot ')).length;
  const failed=delivered.filter(t=>t.tool==='native:Bash'&&t.success===false).length;
  // Connector actions happen outside the project and aren't undone by rejecting the result, so they're named here.
  const connectors=[...new Set(delivered.filter(token=>token.tool==='native:Connector').map(token=>token.connector||t('a connector')))];
  const used=connectors.length?t('used {connectors}',{connectors:connectors.length>2?t('{items} and {count} more',{items:connectors.slice(0,2).join(', '),count:connectors.length-2}):formatList(connectors)}):undefined;
  const parts=[commands?t('Ran {count} commands',{count:commands}):t('Ran no commands'),shots?t('looked at {count} screenshots',{count:shots}):undefined,used,failed?t('{failed} returned an error',{failed}):undefined].filter(Boolean).join(', ');
  if(written.length||commands||connectors.length)steps.push({label:CHECKED,text:t('{parts}. Dervo ran no check of its own.',{parts}),state:failed?'problem':'none'});
  if(unconfirmed)return [...steps.filter(s=>s.label!==CHECKED),{label:CHECKED,text:t('Outcome not confirmed'),state:'problem'} as ProgressStep].slice(0,3);
  return steps.slice(0,3);
 }
 if(checks.length||exit!==undefined){
  const failed=exit!==undefined?exit!==0:checks.at(-1)!.success===false;
  // A check call that errored before a result isn't a failing test: only a recorded exit status says failed.
  steps.push(failed?{label:CHECKED,text:exit!==undefined?t('{checkName} failed',{checkName}):t('{checkName} didn’t finish',{checkName}),state:'problem'}:{label:CHECKED,text:t('{checkName} passed{reviewed}',{checkName,reviewed}),state:reviewed.startsWith(', but')?'problem':'done'});
 }else if(task.check_profile==='manual-review'&&(completion||task.state==='ready_for_review'||task.state==='idle'))steps.push({label:CHECKED,text:t('No automated checks ran{reviewed}',{reviewed}),state:'none'});
 else if(written.length&&halted)steps.push({label:CHECKED,text:t('Not checked before it stopped'),state:'none'});
 if(unconfirmed)return [...steps.filter(s=>s.label!==CHECKED),{label:CHECKED,text:t('Outcome not confirmed'),state:'problem'} as ProgressStep].slice(0,3);
 return steps.slice(0,3);
}

export function progressBrief(task:CodingTask,nowSeconds=Date.now()/1000):ProgressBrief{
 const plan=reported(task);
 const asked=[...task.messages].reverse().find(m=>m.role==='user')?.text;
 const request=asked===undefined?undefined:yourWords(asked);  // a job Home routed: your words, not its brief
 const title=plan.goal??clip(request?.split('\n')[0],90);
 const working=task.state==='working';
 return {
  title:title??t('Task'),titleSource:plan.goal?'agent':title?'request':'none',
  now:progressNow(task,nowSeconds),
  why:working?(plan.why?{text:plan.why,source:'agent'}:request?{text:clip(request,160)!,source:'request'}:undefined):undefined,
  next:working&&plan.next?{text:plan.next,source:'agent'}:undefined,
  steps:working?[]:progressSteps(task),
  plan:progressPlan(task),remembered:progressRemembered(task),continuity:progressContinuity(task),
 };
}
