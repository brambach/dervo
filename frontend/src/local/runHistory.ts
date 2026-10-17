/* Agent history (23 Sep 2026): the host's four reads and the words the three screens build from them. Every sentence
 * here is either a record field the host returned or a time put beside one. What an agent said about itself arrives
 * under ``said`` or ``step`` and is only ever shown quoted, under "It said". Nothing here guesses. */
import {local} from './api';
import {modelName} from './providerLabel';
import type {GlyphState} from './StatusGlyph';
import {formatDate,formatList,t} from '@/i18n';

export type HistoryLink={kind:'home'|'correction'|'continued'|'fresh'|'retry'|'proposed'|'answer'|'review'|'after'|'later'|'continued_by';text:string;
 taskId?:string;channelId?:string;name?:string;provider?:string|null;path?:string;at?:number|null};
export type HistoryCheck={file:string|null;profile:string|null;exit:number|null;stopped:string|null;at:number|null;text:string};
export type OwnCommand={command:string;prose:boolean;at:number|null;error:boolean;seconds:number|null};
/** What an agent wrote, to be shown quoted and only as theirs. ``proposal``, ``question`` and ``home`` are the words that
 * started a run Dervo sent for you (a proposed thread, the question you answered, Home's instruction), with who wrote them. */
export type Said={text:string;source:'reply'|'limits'|'proposal'|'question'|'home';from?:string};
/** Only ever your own words: your message in the thread or on Home, or your answer to an agent's question. */
export type Asked={text:string|null;at:number|null;where:'thread'|'home'|'answer';picked?:boolean};
/** Where a run came from when it wasn't your message in the thread, with the agent's words in ``quote``. */
export type Origin={kind:'home'|'proposal'|'answer';line:string;name:string|null;taskId:string|null;channelId:string|null;quote:string|null;quoteFrom:string|null;at:number|null};
export type Who={provider:string|null;providerLabel:string;model:string|null;account:string|null;access:string|null};
export type HistoryRow=Who&{kind:'run'|'home';key:string;taskId:string|null;runId:string|null;runIndex?:number;runCount?:number;channelId:string|null;name:string;
 private:boolean;startedAt:number|null;endedAt:number|null;glyph:GlyphState;ended:string;did:string;line:string;files:string[];checks:HistoryCheck[];
 own:OwnCommand[];asked:Asked|null;origin?:Origin|null;told:string|null;said:Said[];startedBy:'you'|'home'|'retry'|'proposal'|'answer';links:HistoryLink[];linked:string[];
 commit:{sha:string;short:string;at:number|null;reverted:boolean}|null};
/** A piece of work (beta.16, b16/git): one thread's runs grouped by the host, with its commits and pull request.
 * ``where`` is what this Mac knows: 'github' when every live commit is on a remote branch or on its pull request's
 * branch, 'local' when one isn't, null when git couldn't say. The PR is as Dervo last saw it, never read here. */
export type WorkCommit={sha:string;short:string;at:number|null;reverted:boolean;onGitHub:boolean|null};
export type WorkPr={number:number|null;url:string|null;repo:string|null;head:string|null;base:string|null;mode:'thread'|'branch'|null;attempt:string|null;detail:string|null;
 state:'open'|'closed'|'merged'|null;draft:boolean;review:'approved'|'changes_requested'|'review_required'|null;reviewers:{changes?:string[];approved?:string[];requested?:string[]};
 checks:{passing:number;failing:number;pending:number}|null;comments:{author:string;text:string;path:string|null;line:number|null;at:number|null}[];checkedAt:number|null;checkedAs:string|null};
export type WorkItem={key:string;channelId:string;name:string;private:boolean;archived:boolean;tidied:{at:number|null;quietSince:number|null}|null;glyph:GlyphState;state:string;
 agents:string[];runCount:number;files:string[];summary:string;startedAt:number|null;endedAt:number|null;at:number|null;commits:WorkCommit[];where:'github'|'local'|null;
 pr:WorkPr|null;reviewTaskId:string|null;runs:string[]};
export type HistoryListing={runs:HistoryRow[];work?:WorkItem[];more:number;counts:{all:number;changed:number;home:number;work?:number;waiting?:number;local?:number;prs?:number};
 providers:string[];incomplete:boolean;note?:string|null};
export type HistoryFilter='all'|'waiting'|'local'|'prs'|'changed'|'home';

export type RecordCall={tool:string;target:string;out:string;failed:boolean;at:number|null};
export type RecordStep={at:number|null;text:string|null;why:string|null;calls:RecordCall[]};
export type RunRecord=Who&{taskId:string;runId:string;runIndex:number;runCount:number;channelId:string;name:string;private:boolean;
 startedAt:number|null;endedAt:number|null;glyph:GlyphState;ended:string;state:string;canReview:boolean;
 why:{you:Asked|null;home:{text:string|null;at:number|null}|null;origin?:Origin|null;steers:{text:string|null;at:number|null;question?:string|null;picked?:boolean}[]};
 told:{recorded:boolean;repository:{path:string;bytes:number|null;cut:boolean}[];rules:number;notes:string[];skills:string[];recent:string[];workstream:string|null;
  earlier:{state:string|null;reason:string|null;taskId:string|null;name:string|null}|null};
 steps:RecordStep[];checks:{dervo:HistoryCheck[];own:OwnCommand[];note:string|null};said:Said[];
 files:{path:string;added:number|null;removed:number|null}[];commit:HistoryRow['commit'];links:HistoryLink[]};

export type TraceBlock={key:string;label:string;taskId:string|null;runIndex?:number;provider:string|null;committed:boolean;sha:string|null;first:number;last:number};
export type TraceRun={taskId:string;runId:string;runIndex:number;channelId:string;name:string;provider:string|null;at:number|null;what:string;committed:boolean|null};
export type TraceFileView={path:string;name:string;folder:string|null;total:number;first:number;last:number;cut:boolean;focus?:number|null;lines:{n:number;text:string}[];
 blocks:TraceBlock[];runs:TraceRun[];changes:number;uncommitted:number;note:string|null};
export type TraceLine={path:string;line:number;text:string;committed:boolean;commit:{sha:string;short:string;author:string|null;at:number|null;subject:string|null;taskId:string|null}|null;
 run:(Who&{taskId:string;runId:string;runIndex:number;channelId:string;name:string})|null;change:string|null;written:{at:number|null;how:string}|null;
 step:{text:string;at:number|null}|null;before:string|null;asked:Asked|null;origin?:Origin|null;checked:{text:string;at:number|null}|null;nobody:string|null;
 author:{name:string|null;at:number|null;subject:string|null}|null;note:string|null};

type Scope={projectId:string;profileId:string};
export const historyList=(scope:Scope,filter:HistoryFilter,provider:string,file:string)=>local<HistoryListing>('history_list',{...scope,filter,provider,file});
export const runRecord=(scope:Scope,taskId:string,runId='')=>local<RunRecord>('run_record',{...scope,taskId,runId});
export const traceFile=(scope:Scope,path:string,line=0)=>local<TraceFileView>('trace_file',{...scope,path,line});
export const traceLine=(scope:Scope,path:string,line:number)=>local<TraceLine>('trace_line',{...scope,path,line});

/** Where History is: the log, one run's record, or a file's trace. ``at`` makes the same place requested twice land again. */
export type HistoryPlace={view:'log'}|{view:'record';taskId:string;runId?:string}|{view:'trace';path:string;line?:number};

export const clock=(at:number)=>formatDate(new Date(at*1000),{hour:'numeric',minute:'2-digit'});
const meridiem=(text:string)=>text.match(/\s?([AaPp]\.?[Mm]\.?)$/)?.[1];
/** "1:38 to 1:52 AM", or "11:40 PM to 12:10 AM" across noon or midnight. A run still going has only its start. */
export function span(start:number|null,end:number|null):string{
 if(!start)return end?clock(end):'';
 if(!end||end-start<60)return clock(start);
 const from=clock(start),to=clock(end),mark=meridiem(from);
 const sameDay=new Date(start*1000).toDateString()===new Date(end*1000).toDateString();
 return t('{from} to {to}',{from:sameDay&&mark&&mark===meridiem(to)?from.replace(/\s?[AaPp]\.?[Mm]\.?$/,''):from,to});
}
/** How long it took: "14 min", "2 h 5 min". */
export function took(start:number|null,end:number|null):string{
 if(!start||!end||end<=start)return '';
 const minutes=Math.max(1,Math.round((end-start)/60));
 if(minutes<60)return t('{minutes} min',{minutes});
 const hours=Math.floor(minutes/60),rest=minutes%60;
 return rest?t('{hours} h {rest} min',{hours,rest}):`${hours} h`;
}
/** A day heading: Today, Yesterday, the weekday this week, else "Mon 14 Sep". */
export function dayLabel(at:number,now=Date.now()/1000):string{
 const then=new Date(at*1000),today=new Date(now*1000);
 const midnight=(date:Date)=>new Date(date.getFullYear(),date.getMonth(),date.getDate()).getTime();
 const days=Math.round((midnight(today)-midnight(then))/86400000);
 if(days<=0)return t('Today');
 if(days===1)return t('Yesterday');
 if(days<7)return formatDate(then,{weekday:'long'});
 return formatDate(then,{weekday:'short',day:'numeric',month:'short'});
}
/** The day as it sits inside a sentence: "today", "yesterday", or the day as dayLabel writes it. */
export const dayInLine=(at:number,now=Date.now()/1000)=>{const day=dayLabel(at,now);return day===t('Today')?t('today'):day===t('Yesterday')?t('yesterday'):day;};
export function byDay<T extends {startedAt:number|null;at?:number|null}>(rows:T[],now=Date.now()/1000):{day:string;rows:T[]}[]{
 const days:{day:string;rows:T[]}[]=[];
 for(const row of rows){
  // A piece of work goes under the day it was last worked on; a run under the day it started.
  const when=row.at??row.startedAt;
  const day=when?dayLabel(when,now):t('Earlier');
  if(days.at(-1)?.day!==day)days.push({day,rows:[]});
  days.at(-1)!.rows.push(row);
 }
 return days;
}
/** The caption beside the title: how many runs and since when, and that each line is from what it did. */
export function sinceLine(rows:HistoryRow[],now=Date.now()/1000):string{
 const runs=rows.filter(row=>row.kind==='run');
 if(!runs.length)return t('Nothing has run in this project yet.');
 const first=Math.min(...runs.map(row=>row.startedAt??now));
 const day=dayLabel(first,now);
 const since=day===t('Today')?t('today'):day===t('Yesterday')?t('since yesterday'):t('since {day}',{day});
 return t('{count} runs {since}, from what each one did',{count:runs.length,since});
}
/** The caption over the grouped view: "6 pieces of work, 9 runs, since Wed, Sep 9". */
export function workSinceLine(work:WorkItem[],runs:HistoryRow[],now=Date.now()/1000):string{
 if(!work.length)return t('Nothing has run in this project yet.');
 const first=Math.min(...work.map(item=>item.startedAt??item.at??now));
 const day=dayLabel(first,now);
 const since=day===t('Today')?t('today'):day===t('Yesterday')?t('since yesterday'):t('since {day}',{day});
 const count=runs.filter(row=>row.kind==='run').length;
 return `${t('{count} pieces of work',{count:work.length})}, ${t('{count} runs',{count})}, ${since}`;
}
/** A run's quiet line inside its card: "Run 1 · Opus 5 · check passed, exit 0", or Home's "Started this thread from your message". */
export function runLine(row:HistoryRow,number:number):string{
 if(row.kind==='home')return t('Started this thread from your message');
 const check=row.checks.at(-1);
 const parts=[t('Run {number}',{number}),row.model?modelName(row.model):row.providerLabel];
 if(check)parts.push(check.stopped?t('check stopped'):check.exit===0?t('check passed, exit 0'):check.exit!==null?t('check failed, exit {exit}',{exit:check.exit}):t('check ran'));
 else if(row.glyph==='running')parts.push('running');
 else if(row.glyph!=='done')parts.push(row.ended.charAt(0).toLowerCase()+row.ended.slice(1));
 if(row.commit&&!row.commit.reverted)parts.push(t('committed {short}',{short:row.commit.short}));
 return parts.join(' · ');
}
/** The byline after the agent's name: model, account, and the access when it isn't the ordinary chosen files. */
export function bylineParts(who:Who):string[]{
 return [who.model?modelName(who.model):'',who.account??'',who.access&&who.access!=='Chosen files'?who.access:''].filter(Boolean);
}
/** A command it ran itself, with when and whether it errored. Dervo keeps no output, so that's all a line can say. */
export function ownLine(own:OwnCommand):string{
 // Claude Code sometimes names a command by its own description instead of the command: that's its words, so quoted.
 const command=own.prose?t('a command it described as “{command}”',{command:own.command}):`\`${own.command}\``;
 const at=own.at?` ${t('at {time}',{time:clock(own.at)})}`:'';
 return own.error?t('Ran {command} itself{at}, which ended in an error.',{command,at}):t('Ran {command} itself{at}, no error.',{command,at});
}
/** The open row's "Did": what it changed, the checks Dervo ran with their times, and the commands it ran itself. */
export function didLines(row:Pick<HistoryRow,'files'|'checks'|'own'|'did'>):string[]{
 const lines:string[]=[];
 const names=row.files.map(path=>path.split('/').pop()!);
 if(names.length)lines.push(t('Changed {files}.',{files:names.length<=3?(names.length===2?formatList(names.map(name=>`\`${name}\``)):names.map(name=>`\`${name}\``).join(', ')):t('{count} files',{count:names.length})}));
 for(const check of row.checks)lines.push(checkLine(check));
 for(const own of row.own.slice(0,2))lines.push(ownLine(own));
 if(row.own.length>2)lines.push(t('It ran {count} more like them.',{count:row.own.length-2}));
 if(!row.checks.length&&!row.own.length&&names.length)lines.push(t('No check ran in Dervo.'));
 return lines.length?lines:[row.did];
}
/** A check Dervo ran, with its time put in where the record has one: "Dervo’s check ran test_sample.py at 1:40 AM: exit 0." */
export function checkLine(check:Pick<HistoryCheck,'at'|'text'>):string{
 // i18n-ignore: the host writes the check line in English, and the time goes inside its own sentence
 return check.at?check.text.replace(/(: exit| and stopped|\. No exit)/,` at ${clock(check.at)}$1`):check.text;
}
/** Who wrote a line, as the Written fact says it: "2:57 AM by Codex, GPT-5.5 on ChatGPT Plus, through Dervo’s write tool." */
export function writtenLine(line:TraceLine):string{
 if(!line.run||!line.written)return '';
 const on=[line.run.model?modelName(line.run.model):'',line.run.account?t('on {account}',{account:line.run.account}):''].filter(Boolean).join(' ');
 return `${line.written.at?`${clock(line.written.at)} `:''}${t('by {who}',{who:line.run.providerLabel})}${on?`, ${on}`:''}, ${line.written.how}.`;
}
/** Backticks in record text become code, nothing else: the words stay as recorded. */
export function codeParts(text:string):{code:boolean;text:string}[]{
 return text.split(/(`[^`\n]+`)/).filter(Boolean).map(part=>part.startsWith('`')&&part.endsWith('`')&&part.length>2?{code:true,text:part.slice(1,-1)}:{code:false,text:part});
}
/** The one row per view that takes the accent: the newest run waiting on you. */
export function accentKey(rows:HistoryRow[]):string|undefined{return rows.find(row=>row.glyph==='needs')?.key;}
