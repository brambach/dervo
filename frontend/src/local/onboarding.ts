import {local} from './api';
import {readHistory,type HistoryCandidate} from './historyImport';
import {formatDate,formatList,t} from '@/i18n';

/** Start from what's already on this Mac (beta.15 group 10; evidence/design-tests/onboarding-v2.md and .html). First
 * run asks before it looks, looks at folder names only, sets the noise aside by rule, suggests projects with a reason
 * each, and makes nothing until Bring in. Work and Personal came back on 26 Sep 2026 (Bryce: "make it very obvious WHY
 * and make sure both sides work"; evidence/design-tests/b16-work): each project is guessed Work or Personal from its
 * folder and git remotes, Move to is one click, a Work project only ever runs on a work account and a Personal one on a
 * personal account, and nothing blocks the rest. Threads need a second yes, and come in through the per-project reader
 * (history_read). One Undo on Home takes the whole batch back while nothing's been done in it.
 *
 * The host does the looking (backend/mac_survey.py, onboarding_host.py). The screens only ever hand back the keys a
 * look produced, never a path. Nothing here reads a file. */

export type Tool='claude'|'codex';
/** ``readable`` false: the list is there but Dervo couldn't read it (behaviour review S4), so its zero isn't "nothing". */
export type SurveySource={tool:Tool;path:string;present:boolean;readable?:boolean};
export type Context='work'|'personal';
/** ``context`` is the host's guess (mac_survey.context_guess), null when nothing says either way; ``context_reason``
 * says what it rests on ("guessed from ~/work", "guessed from its remote, git.acme.io"). */
export type SurveyProject={key:string;path:string;name:string;context:Context|null;context_reason:string|null;reason:string;ticked:boolean;
 conversations:number;recent:number;last:number|null;first:number|null;tools:Tool[];branch:string|null;codex_project:boolean};
export type SurveyPileFolder={path:string;conversations:number;key:string|null;name?:string};
export type SurveyPile={rule:string;count:number;label:string;folders:SurveyPileFolder[];more:number};
export type Survey={surveyId:string;sources:SurveySource[];counts:{files:number;folders:number;working:number;quieter:number;loose:number};
 projects:SurveyProject[];
 /** Conversations started in the home folder. Said, never offered: no key, so it can't come in (Bryce, 24 Sep). */
 loose_ends:(Omit<SurveyProject,'key'>&{key:null})|null;set_aside:SurveyPile[];took:number};
export type BatchProject={id:string;key?:string|null;name:string;threads:number;context?:Context|null};
/** ``waiting``: Work folders ticked with no work account yet. Kept as a record, never made or read (onboarding_host). */
export type Batch={batchId:string;at:number;undone:boolean;projects:BatchProject[];threads:number;problems:string[];
 undoable:boolean;blocked:string|null;waiting?:{name:string;path:string}[]};
/** The account one side's projects start on. */
export type SideAccount={profileId:string;model:string};
/** ``toolRuns``: how many sessions a tool ran this folder has to offer, so the opt-in shows only where it does something. */
export type Shortlist={key:string;candidates:HistoryCandidate[];limit:number;detail:string|null;toolRuns?:number};

export const onboardingSources=()=>local<{sources:SurveySource[]}>('onboarding_sources');
export const lookAtMac=()=>local<Survey>('onboarding_look');
/** How long the host keeps a look before it asks for a new one (onboarding_host.SURVEY_SECONDS). */
export const LOOK_MS=3600*1000;
/** One folder per call: the reader reads whole transcripts, and every call shares the bridge's 10 second limit. A folder is
 * read only with an account on its own side (security review S4). ``includeToolRuns`` is the screen's opt-in (NEXT-UP #38). */
export const threadsFor=(surveyId:string,key:string,context:Context,profileId:string,includeToolRuns=false)=>
 local<{projects:Shortlist[]}>('onboarding_threads',{surveyId,projects:[{key,context,profileId}],...(includeToolRuns?{includeToolRuns}:{})}).then(found=>found.projects[0]);
/** The picks after looking again (NEXT-UP #36): what you'd ticked, renamed and put under Work or Personal carries over by
 * key, since a folder keeps its key from one look to the next (mac_survey.key_for). A folder you brought back stays while
 * the new look still lists it; one that's gone is dropped and named in ``gone``. New folders come in as the look says. */
export function carryPicks(before:Pick[],found:Survey):{picks:Pick[];gone:string[]}{
 const fresh=initialPicks(found);
 const kept=new Map(before.map(pick=>[pick.key,pick]));
 const restorable=new Set(found.set_aside.flatMap(pile=>pile.folders).map(folder=>folder.key).filter(Boolean));
 const picks=[...fresh.map(pick=>kept.get(pick.key)??pick),
  ...before.filter(pick=>pick.restored&&!fresh.some(row=>row.key===pick.key)&&restorable.has(pick.key))];
 return {picks,gone:before.filter(pick=>pick.ticked&&!picks.some(row=>row.key===pick.key)).map(pick=>pick.name)};
}
/** Personal projects start on ``personal``, Work ones on ``work``. A Work project sent with no work account waits. */
export const bringIn=(surveyId:string,projects:{key:string;name:string;context:Context}[],personal:SideAccount|null,work:SideAccount|null)=>
 local<Batch>('onboarding_bring_in',{surveyId,projects,profileId:personal?.profileId??null,model:personal?.model??null,...(work?{work}:{})});
export const batchFor=(projectId:string)=>local<Batch|null>('onboarding_batch',{projectId});
export const undoBatch=(batchId:string)=>local<Batch>('onboarding_undo',{batchId});
export {readHistory};

/** One suggestion as you've left it: ticked or not, renamed, and Work, Personal or not yet either (null). ``picked``
 * means you chose its side, so the row says "you picked" instead of the guess. */
export type Pick={key:string;name:string;ticked:boolean;context:Context|null;picked?:boolean;restored?:boolean};
export function initialPicks(survey:Survey):Pick[]{
 return survey.projects.map(project=>({key:project.key,name:project.name,ticked:project.ticked,context:project.context??null}));
}
/** A folder brought back from a pile: it joins the list unticked, with no guess, so it asks Work or Personal. */
export function restorePick(picks:Pick[],folder:SurveyPileFolder):Pick[]{
 if(!folder.key||picks.some(pick=>pick.key===folder.key))return picks;
 return [...picks,{key:folder.key,name:folder.name??folder.path.split('/').pop()??folder.path,ticked:false,context:null,restored:true}];
}

/** The why, said once where the choice is made (Bryce, 26 Sep 2026: "make it very obvious WHY"). */
export const WHY_LEAD=t('Work code only ever goes to a work account, and personal code only to a personal one,');
export const WHY_REST=t('so your employer’s code never lands on your personal subscription, and the other way round.');
/** Someone with only Personal projects sees no sides at all: no Work group, no why, no Move buttons in view. */
export const personalOnly=(picks:Pick[])=>picks.length>0&&picks.every(pick=>pick.context==='personal');
export const sideName=(context:Context)=>context==='work'?t('Work'):t('Personal');

/** Where Suggest stands: which ticked projects come in now, which wait, and why. ``work`` is whether a work account is
 * ready, ``adding`` whether its card is open. ``go``: Bring in can go, because something comes in now and nothing ticked
 * is held by a name or a missing agent. Never held by a Work or Personal question: those rows wait, the rest come in. */
export type Plan={now:Pick[];waiting:Pick[];unsure:Pick[];status:string|null;aside:string|null;addWork:boolean;go:boolean};
export function suggestPlan(picks:Pick[],agent:{ready:boolean;line:string},work:{ready:boolean;adding?:boolean}):Plan{
 const ticked=picks.filter(pick=>pick.ticked);
 const personal=ticked.filter(pick=>pick.context==='personal'),works=ticked.filter(pick=>pick.context==='work'),unsure=ticked.filter(pick=>!pick.context);
 const now=[...(agent.ready?personal:[]),...(work.ready?works:[])];
 const waiting=work.ready?[]:works;
 // Nothing comes in and it's all work: the one way on is a work account, so that's the main button (suggest-work-only).
 const addWork=!now.length&&waiting.length>0&&!unsure.length&&(!personal.length||agent.ready);
 const aside=!waiting.length?null:addWork&&!personal.length?t('These all look like work.')
  :work.adding?t('{count} work projects wait for the account.',{count:waiting.length}):t('{count} work projects wait for a work account.',{count:waiting.length});
 let status:string|null=null;
 const unnamed=ticked.filter(pick=>!pick.name.trim()).length;
 if(!ticked.length)status=t('Tick a project to bring it in.');
 else if(personal.length&&!agent.ready)status=agent.line||t('Bring in waits until an agent is connected.');
 else if(unnamed)status=t('Give the {count} ticked projects a name to bring them in.',{count:unnamed});
 else if(unsure.length){
  const one=unsure.length===1;
  status=now.length?`${one?t('{name} waits until you pick Work or Personal.',{name:unsure[0].name}):t('{count} projects wait until you pick Work or Personal.',{count:unsure.length})} ${t('The other {count} can come in now.',{count:now.length})}`
   :one?t('Pick Work or Personal for {name} to bring it in.',{name:unsure[0].name}):t('Pick Work or Personal for {count} projects to bring them in.',{count:unsure.length});
 }
 const go=now.length>0&&!unnamed&&!(personal.length&&!agent.ready);
 return {now,waiting,unsure,status,aside,addWork,go};
}
/** What a group says its guess rests on. */
export function groupGuess(context:Context|null,count:number,only:boolean):string{
 if(only)return t('All of these look personal. If one is your employer’s, hover it and choose Move to Work.');
 if(!context)return count===1?t('Nothing in its folder name or git remote says which. Pick one to bring it in.')
  :t('Nothing in their folder names or git remotes says which. Pick one for each to bring it in.');
 return t('Guessed from folder names and git remotes. Each row says which.');
}
/** A row's own reason for its side: what you picked, or what the look guessed it from. */
export function rowGuess(pick:Pick,project?:SurveyProject):string|null{
 if(!pick.context)return null;
 if(pick.picked||!project||project.context!==pick.context)return t('you picked {side}',{side:sideName(pick.context)});
 return project.context_reason;
}

export const toolsLabel=(tools:Tool[])=>tools.length===2?t('Claude Code and Codex'):tools[0]==='codex'?'Codex':'Claude Code';

/** The Ask screen's source rows: where each list lives, and whether it's here. */
export function sourceLine(source:SurveySource):{name:string;path:string;state:string}{
 return {name:source.tool==='claude'?'Claude Code':'Codex',path:source.tool==='codex'?t('{path} thread list',{path:source.path}):source.path,state:source.present?t('On this Mac'):t('Not on this Mac')};
}

/** The three numbers the collapse rests on. */
export function collapseNumbers(counts:Survey['counts']):[string,string,string]{
 return [t('{count} conversation files.',{count:counts.files}),t('{count} folders.',{count:counts.folders}),t('{count} things you’re working on.',{count:counts.working})];
}
/** The line under them: the quieter ones, and conversations that never had a project. */
export function quieterLine(counts:Survey['counts']):string|null{
 const parts=[counts.quieter?t('{count} quieter ones',{count:counts.quieter}):'',counts.loose?t('{count} conversations that never had a project folder',{count:counts.loose}):''].filter(Boolean);
 if(!parts.length)return null;
 return parts.length===2?t('And {first}, plus {second}.',{first:parts[0],second:parts[1]}):t('And {first}.',{first:parts[0]});
}
/** Piles that count folders; the others (tool_run, copy) count threads. mac_survey.FOLDER_RULES. */
export const FOLDER_PILES=new Set(['temp','gone','unreadable','tool_folder','folded','dervo','one_off']);
/** Suggest's one line about Loose ends: they stay where they are, and aren't a project to tick. */
export function looseLine(loose:{conversations:number}|null|undefined):string|null{
 if(!loose?.conversations)return null;
 return t('{count} conversations started in your home folder, not in a project. They stay where they are.',{count:loose.conversations});
}
/** A fresh Mac: neither tool has anything. Said plainly, with the two ways on. */
export const nothingFound=(survey:Survey)=>!survey.projects.length&&!survey.loose_ends&&!survey.counts.files&&!unreadLine(survey);
/** Under the collapse when a tool's list is on this Mac but couldn't be read: its folders aren't in the numbers, and
 * that's said rather than shown as zero. Null when every list read. */
export function unreadLine(survey:{sources:SurveySource[]}):string|null{
 const names=survey.sources.filter(source=>source.present&&source.readable===false).map(source=>source.tool==='claude'?'Claude Code':'Codex');
 if(!names.length)return null;
 return names.length>1?t('Dervo couldn’t read Claude Code’s or Codex’s list, so their folders aren’t counted here.'):t('Dervo couldn’t read {tool}’s list, so its folders aren’t counted here.',{tool:names[0]});
}
/** On the Ask card: macOS may ask before Dervo lists a protected folder (behaviour review S3). */
export const MAC_ASKS=t('macOS may ask before Dervo looks in Documents, Desktop or Downloads. Saying no only leaves those folders out.');

/** "Bring in 2 projects", "Bring in 2 projects and 14 threads": every ticked project. */
export function bringLabel(projects:number,threads=0):string{
 if(!projects)return t('Bring in');
 return threads?t('Bring in {projects} and {threads}',{projects:t('{count} projects',{count:projects}),threads:t('{count} threads',{count:threads})}):t('Bring in {count} projects',{count:projects});
}
/** The second yes, in the design's words. */
export function threadsQuestion(count:number):string{
 return t('To name each conversation and see where it stopped, Dervo reads the first and last message of each one in the {count} projects you picked. On this Mac, with the same rules a new thread uses. Nothing is sent and no model is called.',{count});
}

/** Home's line for a batch: what came in, when, and that nothing has run. */
export function broughtLine(batch:Batch,now=Date.now()/1000):string{
 const when=now-batch.at<86400?formatDate(batch.at*1000,{hour:'numeric',minute:'2-digit'}):formatDate(batch.at*1000,{day:'numeric',month:'short'});
 const projects=t('{count} projects',{count:batch.projects.length});
 const what=batch.threads?t('{projects} and {threads}',{projects,threads:t('{count} threads',{count:batch.threads})}):projects;
 return t('Brought in {what} from this Mac at {when}. No agent has run.',{what,when});
}
/** The Work folders that stayed out for want of a work account, by name: on Bring in's progress and on Home. */
export function waitingLine(names:string[]):string{
 if(names.length===1)return t('{name} waits for a work account. Nothing in it has been read or sent.',{name:names[0]});
 const list=names.length<=3?formatList(names):t('{names} and {count} others',{names:names.slice(0,2).join(', '),count:names.length-2});
 return t('{names} wait for a work account. Nothing in them has been read or sent.',{names:list});
}
/** The Work folders still waiting, Mac-wide, and bringing them in once a work account is signed in. */
export const waitingWork=()=>local<{names:string[]}>('onboarding_waiting');
export const bringWaiting=(work:SideAccount)=>local<Batch>('onboarding_bring_waiting',{work});
