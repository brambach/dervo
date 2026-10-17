import {formatDate,t} from '@/i18n';
import {hostMessage} from '@/i18n/hostMessages';
import type {GlyphState} from './StatusGlyph';

/** How a task's newest run ended, as the host sorted it (backend/run_ending.py, beta.17.1). Only ``failed`` is a failure:
 * ``finished`` is work that finished with a later Dervo step left undone, ``unknown`` is a run Dervo lost track of,
 * ``stopped`` is your own stop or Dervo's quit, ``limit`` is one of the project's run limits or the provider's usage
 * limit, and ``not_started`` never reached the agent. Bryce, 5 Oct 2026: "things shouldn't fail unless they are absolutely
 * something that failed". */
export type RunEnding={kind:'failed'|'finished'|'unknown'|'stopped'|'limit'|'not_started';limit?:'idle'|'hard'|'stop'|'output'|'attempts'|'provider';
 snag?:'record'|'checks'|'changed'|'nothing';by?:'you'|'quit'|'restart';confirmed?:boolean;cause?:string;last_seen?:number|null;step?:string|null;
 /** Beta.18: you left a lost run stopped (Leave it stopped, Mark as stopped until now). */left?:boolean;
 /** Beta.18: Don't run on a permission request. */declined?:boolean};

/** Beta.18 (backend/lost_runs.py): what the provider's own record said about a run Dervo lost when it restarted. ``paused``
 * runs are settled as paused (ending ``by: 'restart'``); ``unsure`` names the one step that may have reached outside the
 * folder with no end in the record; ``unreadable`` keeps the old card. */
export type LostRecord={state:'paused'|'unsure'|'unreadable';provider?:string;step?:{tool:string;text?:string|null;at?:number|null}|null};

// i18n-ignore: sent to the agent as your message, like GuideActions' RESUME_TEXT
export const CONTINUE_TEXT='Carry on from where you stopped. Dervo ended the last run at one of its limits.';

export function endingOf(task:{state:string;ending?:RunEnding|null}):RunEnding|null{return task.ending&&typeof task.ending==='object'&&typeof task.ending.kind==='string'?task.ending:null;}

/** The record read of the newest run Dervo lost when it restarted, or null when there wasn't one (or it hasn't finished). */
export function lostRecord(task:{runs?:unknown[]}):LostRecord|null{
 const run=task.runs?.at(-1) as {after_close?:{record?:LostRecord|null}}|undefined;
 const record=run&&typeof run==='object'?run.after_close?.record:null;
 return record&&typeof record==='object'&&typeof record.state==='string'?record:null;
}

/** "gh workflow run preview.yml" or "a shell command": the step a record left without an end, for the card's lead. */
export function stepName(step:LostRecord['step']):string{
 const text=typeof step?.text==='string'?step.text.trim():'';
 if(text)return text.length>80?`${text.slice(0,79)}…`:text;
 const tool=typeof step?.tool==='string'?step.tool:'';
 if(tool==='Bash'||tool==='shell')return t('a shell command');
 if(tool.startsWith('mcp__'))return t('a connector call');
 if(tool==='Agent'||tool==='Task')return t('a sub-agent');
 return tool?t('a {tool} step',{tool}):t('a step');
}

// i18n-ignore: sent to the agent as your message, like CONTINUE_TEXT
export const RESTART_TEXT='Carry on from where you stopped. Dervo restarted mid-run, and every step you had started had finished.';
// i18n-ignore: sent to the agent as your message, like CONTINUE_TEXT
export const RECHECK_TEXT='Run the configured checks again on the current files, then finish.';

/** A limit Continue can pick up from: Dervo's silence, run length and output limits, and the attempt limit once it's
 * raised. Not the provider's usage limit, whose handoff row offers the other provider instead. */
export function continuable(ending:RunEnding|null):boolean{return ending?.kind==='limit'&&ending.limit!=='provider';}

/** "Last seen 3:41 PM, running the backend tests." from what the run last reported. Empty when nothing was recorded. */
export function lastSeenLine(ending:RunEnding|null,now=Date.now()/1000):string{
 const at=typeof ending?.last_seen==='number'&&ending.last_seen>1e9?ending.last_seen:null;
 const step=typeof ending?.step==='string'?ending.step.trim().replace(/[.]$/,''):'';
 if(!at&&!step)return '';
 const clock=at?formatDate(at*1000,{hour:'numeric',minute:'2-digit'})+(new Date(at*1000).toDateString()===new Date(now*1000).toDateString()?'':` ${formatDate(at*1000,{weekday:'short'})}`):'';
 if(clock&&step)return t('Last seen {clock}: {step}.',{clock,step});
 return clock?t('Last seen {clock}.',{clock}):t('It was on: {step}.',{step});
}

const LIMIT_LEADS:Record<string,string>={idle:t('Stopped after a long silence.'),hard:t('Stopped at the run length limit.'),output:t('Stopped at the output limit.'),attempts:t('Stopped at the attempt limit.')};
const SNAGS:Record<string,string>={record:t('Dervo couldn’t record its changes.'),checks:t('Its checks didn’t run to the end.'),changed:t('Its files changed after the check, so the check no longer stands.'),nothing:t('Dervo recorded no file change or check from it.')};

/** Beta.18: finished work whose checks didn't run to the end, or whose files changed after them, offers Run the checks again. */
export function recheckable(task:{state:string;ending?:RunEnding|null;unrecorded?:unknown[]}):boolean{
 const ending=endingOf(task);
 return task.state==='blocked'&&ending?.kind==='finished'&&(ending.snag==='checks'||ending.snag==='changed')&&!task.unrecorded?.length;
}

/** The "It had changed a, b. Those edits are in your project folder, not committed." sentence a paused run's error ends
 * with (native_evidence.unfinished_note), without the restart line before it. */
function filesSaid(said:string):string{
 const at=said.indexOf('It had changed ');
 return at>=0?said.slice(at):'';
}

/** The decision bar for an ending that isn't a failure, or null to let CodingTasks' decisionLine say it the way it
 * always has (a failure, your Pause, a quit, files held by another thread, a provider handoff). */
export function endingDecision(task:{state:string;error?:string;ending?:RunEnding|null;unrecorded?:unknown[];suggest?:unknown;binding?:{provider?:string}}):{glyph:GlyphState;accent?:boolean;lead:string;clause?:string;inspect:string}|null{
 const ending=endingOf(task);
 if(!ending||task.suggest)return null;
 const said=task.error?hostMessage(String(task.error)):'';
 const join=(...parts:string[])=>parts.filter(Boolean).join(' ')||undefined;
 // The attempt limit's own line says what to do next; the others get when it was last seen and what Continue does.
 if(ending.kind==='limit'&&ending.limit==='attempts')return {glyph:'stopped',lead:LIMIT_LEADS.attempts,clause:join(said),inspect:t('Inspect')};
 if(ending.kind==='limit'&&ending.limit!=='provider')
  return {glyph:'stopped',lead:LIMIT_LEADS[ending.limit??'']??t('Stopped at a limit.'),clause:join(said,lastSeenLine(ending),t('Continue picks up where it stopped.')),inspect:t('Inspect')};
 if(ending.kind==='not_started'&&ending.declined)return {glyph:'stopped',lead:t('Didn’t start.'),clause:t('You chose not to run it. Nothing was sent.'),inspect:t('Inspect')};
 if(ending.kind==='not_started')return {glyph:'stopped',lead:t('Didn’t start.'),clause:join(said,said.includes('Nothing was sent')?'':t('Nothing was sent.')),inspect:t('Inspect')};
 // Beta.18: Dervo restarted mid-run and the provider's own record shows every step it started ended (backend/lost_runs.py).
 if(ending.kind==='stopped'&&ending.by==='restart'){
  const who=task.binding?.provider==='codex'?'Codex':'Claude Code';
  return {glyph:'stopped',lead:t('Paused when Dervo restarted.'),clause:join(t('{who}’s own record shows every step it started finished, so nothing is uncertain.',{who}),filesSaid(said),t('Continue picks up in the same session.')),inspect:t('Inspect')};
 }
 // Beta.18: Leave it stopped. Stopped, never the failure X; what the run did is still unconfirmed, and the line says so.
 if(ending.kind==='stopped'&&ending.left)return {glyph:'stopped',lead:t('Left stopped.'),clause:join(said),inspect:t('Inspect')};
 if(ending.kind==='finished'&&!task.unrecorded?.length)return {glyph:'needs',accent:true,lead:t('Finished.'),clause:said||SNAGS[ending.snag??'record']||SNAGS.record,inspect:t('Inspect')};
 // Claude Code didn't exit after your Stop, so Dervo ended it: still your stop.
 if(ending.kind==='stopped'&&task.state==='blocked')return {glyph:'stopped',lead:t('Paused.'),clause:join(said),inspect:t('Inspect')};
 return null;
}

/** The task sheet and Progress panel's one word and glyph for an ending that isn't a failure, or null. */
export function endingWord(task:{state:string;ending?:RunEnding|null;unrecorded?:unknown[];runs?:unknown[]}):{glyph:GlyphState;word:string}|null{
 const ending=endingOf(task);
 if(!ending)return null;
 if(ending.kind==='unknown')return lostRecord(task)?.state==='unsure'?{glyph:'unknown',word:t('One step not confirmed')}:{glyph:'unknown',word:t('Not confirmed')};
 if(ending.kind==='limit')return {glyph:'stopped',word:ending.limit==='provider'?t('Stopped at the usage limit'):t('Stopped at a limit')};
 if(ending.kind==='not_started')return {glyph:'stopped',word:t('Didn’t start')};
 // The decision bar under the report says which step; the one word stays short enough for the panel's line.
 if(ending.kind==='finished')return {glyph:'needs',word:task.unrecorded?.length?t('Nothing to approve'):t('Finished')};
 if(ending.kind==='stopped')return {glyph:'stopped',word:ending.left?t('Left stopped'):t('Paused')};
 return null;
}

/** Beta.17.1: a blocked row that didn't fail (backend/run_ending.py) says what ended it, with no X. Null for a failure. */
export function blockedEnding(ending:RunEnding['kind']|null|undefined):{glyph:GlyphState;what:string;action:string}|null{
 if(ending==='limit')return {glyph:'stopped',what:t('Stopped at a limit'),action:t('Pick it up')};
 if(ending==='not_started')return {glyph:'stopped',what:t('Didn’t start'),action:t('Pick it up')};
 if(ending==='stopped')return {glyph:'stopped',what:t('Paused'),action:t('Pick it up')};  // Leave it stopped too (beta.18): stopped, not failed
 if(ending==='finished')return {glyph:'needs',what:t('Finished, a step didn’t complete'),action:t('Open it')};
 if(ending==='unknown')return {glyph:'unknown',what:t('Outcome not confirmed'),action:t('Check what happened')};
 return null;
}
