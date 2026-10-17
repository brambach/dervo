import {asksBeforeFable,claudeDefaultModel,effortLabel,effortsFor,modelLabel,type Account,type CatalogModel} from './models';
import {t} from '@/i18n';

/** Home's agent, model and thinking level (beta.15 group 8, PLAN-beta15-20260923.md). Bryce, 24 Sep: "saying, as part
 * of Home, what agent you want and what thinking level you want, and then being able to launch it based on that."
 *
 * The choice starts at the project's default (Account and model in Settings). You change it on the picker in Home's
 * composer, or say it in the message ("have Codex do search, think hard"), and the picker shows what Home understood
 * before you send. The host checks it (project_orchestrator.launch_choice) and it rides on the reply; each job Home
 * starts sets its thread to it with the thread picker's own operations before anything is sent (applyLaunch), so the
 * thread's picker agrees. A job whose thread can't be set is not sent, and says why: nothing falls back. */

export type Agent='claude'|'codex';
/** What the host stores on the reply (launch_choice). `effort` absent keeps the thread's level; null is the default. */
export type Launch={profile_id:string;provider:Agent;model:string;effort?:string|null};
/** What Home read in your words. Each part is null when the words didn't say it. */
export type Heard={agent:Agent|null;model:string|null;effort:string|null;said:string[];
 /** The message asks for connectors (mail, calendar, Slack...), which only Claude Code can use in Dervo. */
 connectors?:boolean};
/** The picker's own pick, when you made one. `effort` undefined means you didn't touch Thinking. */
export type Picked={profileId:string;model:string;effort?:string|null};
export type LaunchView={
 /** What goes with the message, or null when it's the project's default with nothing changed (the host sees no field). */
 choice:{profileId:string;model:string;effort?:string|null}|null;
 account:Account|null;provider:Agent|null;model:string;effort:string|null|undefined;
 from:'default'|'picked'|'words';
 /** The account's name for the chip, when that agent has more than one account ("API key", "Personal"), else null. */
 accountName:string|null;
 /** "From your message: Codex, think hard", when words set any of it. */
 heardLine:string|null;
 /** Why Send is held: an agent you named with no account, a model still loading. */
 problem:string|null;
 /** Something Home heard and couldn't use, said beside the chip. */
 note:string|null};

export const AGENT_NAMES:Record<Agent,string>={claude:'Claude Code',codex:'Codex'};
const LOWER=(text:string)=>text.toLowerCase().replace(/[’‘]/g,'\'');

/** A named agent only when the words hand it the work ("have Codex", "use Claude Code", "Codex, look at", "ask codex
 * to"), so "fix the Codex login" isn't read as a choice. Both named: nothing, since one message goes to one agent. */
function agentIn(text:string):{agent:Agent|null;said:string|null}{
 const lead='(?:have|use|using|let|ask|get|with|via|through|on|in|send (?:it|this) to|give (?:it|this) to|hand (?:it|this) to)';
 const found:Agent[]=[];let said:string|null=null;
 for(const [agent,word] of [['codex','codex'],['claude','claude(?: code)?']] as [Agent,string][]){
  const hit=new RegExp(`\\b${lead}\\s+${word}\\b|(?:^|[.!?]\\s*|\\b(?:and|then|while)\\s+)${word}\\s*(?:,|:|\\s+(?:do|does|should|can|to|please|look|search|write|fix|build|take|handle|check|review|test)\\b)`,'i').exec(text);
  if(hit){found.push(agent);said=said??hit[0].trim().replace(/^[.!?]\s*/,'');}
 }
 return found.length===1?{agent:found[0],said}:{agent:null,said:null};
}

const CLAUDE_WORDS:[RegExp,string][]=[[/\bopus\s*5\.5\b/,'claude-opus-5-5'],[/\bopus\s*5\b(?!\.)/,'claude-opus-5'],[/\bopus\b/,'claude-opus-5-5'],
 [/\bsonnet\s*5\.5\b/,'claude-sonnet-5-5'],[/\bsonnet\s*5\b(?!\.)/,'claude-sonnet-5'],[/\bsonnet\b/,'claude-sonnet-5-5'],[/\bhaiku(?:\s*4\.5)?\b/,'claude-haiku-4-5'],[/\bfable\s*5\.1\b/,'claude-fable-5-1'],[/\bfable\b/,'claude-fable-5-1']];
const CODEX_WORDS:[RegExp,string][]=[[/\bgpt[- ]?6[- ]?astra\b/,'gpt-6-astra'],[/\bgpt[- ]?6\.1[- ]?sol\b/,'gpt-6.1-sol'],[/\bgpt[- ]?6[- ]?sol\b/,'gpt-6-sol'],[/\bgpt[- ]?6[- ]?luna\b/,'gpt-6-luna'],
 [/\bgpt[- ]?5\.6[- ]?sol\b/,'gpt-5.6-sol'],[/\bgpt[- ]?5\.6[- ]?luna\b/,'gpt-5.6-luna'],[/\bgpt[- ]?5\.6[- ]?terra\b/,'gpt-5.6-terra'],[/\bgpt[- ]?5\.5\b/,'gpt-5.5']];
/** Every model Home can hear named in a message, so modelCheck.ts can say which offered model it can't. */
export const HEARD_MODELS={claude:[...new Set(CLAUDE_WORDS.map(([,id])=>id))],codex:[...new Set(CODEX_WORDS.map(([,id])=>id))]};
/** Levels by the words people use for them, strongest phrasing first. */
const EFFORT_WORDS:[RegExp,string][]=[
 [/\bultrathink\b|\bthink (?:as hard as (?:you )?(?:can|possible)|the hardest)\b|\bmax(?:imum)? (?:thinking|effort)\b/,'max'],
 [/\bthink (?:really|very|extra|super) hard\b|\bthink harder\b|\bextra[- ]high (?:thinking|effort)\b|\bxhigh\b/,'xhigh'],
 [/\bthink hard\b|\bhigh (?:thinking|effort)\b|\bthink (?:it )?through carefully\b/,'high'],
 [/\bmedium (?:thinking|effort)\b/,'medium'],
 [/\bthink (?:a (?:bit|little)|less|briefly)\b|\blow (?:thinking|effort)\b|\bdon'?t overthink\b|\bquick(?:ly)? and light\b/,'low'],
];

/** Words that need your connectors. Codex has none in Dervo (codex_policy.py mcp_servers {}), so Home says so. */
const CONNECTOR_WORDS=/\b(?:connectors?|mcp|e-?mails?|inbox|gmail|outlook|calendar|slack)\b/;  // not "docs" or "linear": too often about code
/** Behaviour review S6 (24 Sep): said beside the chip when Codex would get a job that needs connectors. */
export const CODEX_NO_CONNECTORS=t('Codex can’t use your connectors in Dervo. Claude Code can.');

/** What the words say, if anything. Pure: the picker shows it, and nothing happens until you send. */
export function hear(text:string):Heard{
 const lower=LOWER(text),said:string[]=[];
 const named=agentIn(lower);
 let agent=named.agent,model:string|null=null;
 for(const [pattern,id] of CLAUDE_WORDS){const hit=pattern.exec(lower);if(hit){model=id;said.push(hit[0]);break;}}
 if(!model)for(const [pattern,id] of CODEX_WORDS){const hit=pattern.exec(lower);if(hit){model=id;said.push(hit[0]);break;}}
 const modelAgent:Agent|null=model?(model.startsWith('claude-')?'claude':'codex'):null;
 // A model says its agent. A model from the other agent than the one named is a contradiction: keep the agent, drop it.
 if(model&&agent&&modelAgent!==agent){model=null;said.pop();}
 agent=agent??modelAgent;
 if(named.said)said.unshift(named.said);
 let effort:string|null=null;
 for(const [pattern,level] of EFFORT_WORDS){const hit=pattern.exec(lower);if(hit){effort=level;said.push(hit[0]);break;}}
 return {agent,model,effort,said,connectors:CONNECTOR_WORDS.test(lower)};
}

/** The model a newly chosen agent starts on: Claude Code's default for this Mac, or the first model the Codex catalog
 * lists. Null while the catalog is loading. */
export function firstModel(agent:Agent,claudeVersion:string|null|undefined,catalog?:CatalogModel[]|null):string|null{
 if(agent==='claude')return claudeDefaultModel(claudeVersion);
 return catalog?.find(model=>!model.hidden)?.model??null;
}

/** The account a named agent's jobs go to (design review M3, 24 Sep): the project's own account when it's that agent's,
 * then a subscription, and an API key only when it's the only kind there is. Words never pick a pay-per-use key over a
 * plan you already pay for. */
export function accountFor(accounts:Account[],agent:Agent,projectProfileId?:string|null):Account|null{
 const theirs=accounts.filter(a=>a.provider===agent);
 return theirs.find(a=>a.id===projectProfileId)??theirs.find(a=>a.profile.method!=='api_key')??theirs[0]??null;
}
/** The account as the chip and the heard line name it, only when its agent has more than one: "API key", or
 * "Side project, API key" for a named key. */
export function accountName(accounts:Account[],account:Account|null):string|null{
 if(!account||accounts.filter(a=>a.provider===account.provider).length<2)return null;
 return account.plan==='API key'&&!account.name.includes('API key')?t('{name}, API key',{name:account.name}):account.name;
}

/** The choice as it stands, from the project default, your pick and your words. Words win over a pick made before
 * them; `ignoreWords` is "Use my pick instead". */
export function resolveLaunch({accounts,project,picked,heard,ignoreWords=false,claudeVersion,catalogFor}:{
 accounts:Account[];project:{profile_id:string;model:string}|null;picked:Picked|null;heard:Heard;ignoreWords?:boolean;
 claudeVersion?:string|null;catalogFor:(account:Account)=>CatalogModel[]|null|undefined}):LaunchView{
 const words=ignoreWords?{agent:null,model:null,effort:null,said:[]} as Heard:heard;
 const base=picked??(project?{profileId:project.profile_id,model:project.model}:null);
 let account=accounts.find(a=>a.id===base?.profileId)??null;
 let model=base?.model??'';
 let effort:string|null|undefined=picked?.effort;
 let problem:string|null=null,note:string|null=null;
 const from:LaunchView['from']=(words.agent||words.model||words.effort)?'words':picked?'picked':'default';
 if(words.agent&&account?.provider!==words.agent){
  const other=accountFor(accounts,words.agent,project?.profile_id);
  if(!other)problem=t('Home heard {agent}, but no {provider} account is connected. Connect one, or pick another agent.',{agent:AGENT_NAMES[words.agent],provider:words.agent==='codex'?'Codex':'Claude'});
  else{account=other;model=words.model??firstModel(words.agent,claudeVersion,catalogFor(other))??'';effort=undefined;
   if(!model)problem=t('Loading {name}’s models…',{name:other.name});}
 }else if(words.model)model=words.model;
 const provider=(account?.provider as Agent|undefined)??null;
 if(!account&&!problem)problem=t('Choose an account for these jobs.');
 const catalog=account?catalogFor(account):undefined;
 if(provider==='codex'&&model&&catalog&&!catalog.some(m=>m.model===model)){problem=t('{name} doesn’t offer {model}. Pick another model.',{name:account!.name,model:modelLabel('codex',model)});}
 // Fable on a plan that bills credits asks once per account, on the picker, as a thread's picker does.
 if(!problem&&account&&model&&asksBeforeFable(account.profile,model))problem=t('{model} may use paid credits on {name}. Pick it on the picker to confirm.',{model:modelLabel('claude',model),name:account.name});
 if(words.effort&&provider&&model&&!problem){
  const levels=effortsFor(provider,model,catalog??undefined);
  // Behaviour review S1 (24 Sep): a thinking level is kept per project and model (coding_effort_set), so one said in
  // Home's message changes it for later threads too. Said beside the chip before you send.
  if(levels.includes(words.effort)){effort=words.effort;
   if(picked?.effort===undefined)note=t('{level} thinking becomes this project’s level for {model}.',{level:effortLabel(words.effort),model:modelLabel(provider,model,catalog??undefined)});}
  else note=levels.length?t('{model} doesn’t offer {level}, so its thinking stays as it is.',{model:modelLabel(provider,model,catalog??undefined),level:effortLabel(words.effort)})
   :t('{model} has no thinking levels.',{model:modelLabel(provider,model,catalog??undefined)});
 }
 // Nothing chosen: the jobs start as they always have, on the project's default, and a thread checks its own account
 // and model when it runs. Home holds nothing back for a choice you didn't make.
 if(from==='default'){problem=null;note=null;}
 if(provider==='codex'&&heard.connectors&&!problem)note=[CODEX_NO_CONNECTORS,note].filter(Boolean).join(' ');
 const same=!!project&&account?.id===project.profile_id&&model===project.model&&effort===undefined;
 const choice=problem||!account||!model||(same&&from==='default')?null:{profileId:account.id,model,...(effort!==undefined?{effort}:{})};
 const named=accountName(accounts,account);
 // A named agent says which of its accounts the words landed on, when it has more than one (design review M3).
 const agentSaid=words.agent?`${AGENT_NAMES[words.agent]}${named&&provider===words.agent?` (${named})`:''}`:null;
 const heardLine=from==='words'?t('From your message: {heard}',{heard:[agentSaid,words.model&&provider?modelLabel(provider,words.model,catalog??undefined):null,words.effort&&effort===words.effort?t('{level} thinking',{level:effortLabel(words.effort).toLowerCase()}):null].filter(Boolean).join(', ')}):null;
 return {choice,account,accountName:named,provider,model,effort,from,heardLine:heardLine==='From your message: '?null:heardLine,problem,note};
}

/** The chip's words: "Codex · GPT-6 Sol · High", and "Claude Code (API key) · Opus 5.5" when Claude Code has more than
 * one account. */
export function launchLabel(view:Pick<LaunchView,'provider'|'model'|'effort'>&{accountName?:string|null},catalog?:CatalogModel[]|null):string{
 if(!view.provider)return t('Choose an agent');
 const parts=[`${AGENT_NAMES[view.provider]}${view.accountName?` (${view.accountName})`:''}`,view.model?modelLabel(view.provider,view.model,catalog??undefined):t('choosing a model')];
 if(view.effort)parts.push(effortLabel(view.effort));
 return parts.join(' · ');
}

type Invoke=<T>(operation:string,args:Record<string,unknown>)=>Promise<T>;
type Binding={profile_id:string;model:string;provider?:string};
/** Set a job's thread to the choice, with the thread picker's own operations and the values the thread has now as the
 * expected ones: the account (and model) in one checked write when it differs, else the model alone, then the thinking
 * level when one was chosen. Throws the host's reason when any of it is refused, and the job isn't sent. */
export async function applyLaunch(launch:Launch,{projectId,channelId}:{projectId:string;channelId:string},invoke:Invoke):Promise<void>{
 const binding=await invoke<Binding>('binding',{projectId,channelId});
 if(binding.profile_id!==launch.profile_id)
  await invoke('conversation_profile_set',{projectId,channelId,expectedProfileId:binding.profile_id,profileId:launch.profile_id,expectedModel:binding.model,model:launch.model});
 else if(binding.model!==launch.model)
  await invoke('conversation_model_set',{projectId,channelId,expectedModel:binding.model,model:launch.model});
 if('effort' in launch)await invoke('coding_effort_set',{projectId,channelId,effort:launch.effort??null});
}

/** Under a job Home added to a thread you already had (behaviour review S2): the choice only sets up threads Home makes,
 * so when this thread runs on something else, it says what. Null when they match or nothing was chosen. */
export function keptLine(part:{action:'start'|'add';state?:string},launch:Launch|null|undefined,thread:{profile_id:string;model:string}|null|undefined):string|null{
 if(part.action!=='add'||part.state!=='sent'||!launch||!thread)return null;
 if(thread.profile_id===launch.profile_id&&thread.model===launch.model)return null;
 const agent:Agent=thread.model.startsWith('claude-')?'claude':'codex';
 if(agent!==launch.provider)return t('Kept this thread’s own agent, {agent}',{agent:AGENT_NAMES[agent]});
 if(thread.model!==launch.model)return t('Kept this thread’s own model, {model}',{model:modelLabel(agent,thread.model)});
 return t('Kept this thread’s own account');
}

/** The sentence under a job that couldn't be set up, so it's clear nothing ran elsewhere. */
export function launchFailed(launch:Launch,reason:string):string{
 return t('Couldn’t start it on {agent} ({model}), so nothing was sent: {reason}',{agent:AGENT_NAMES[launch.provider],model:modelLabel(launch.provider,launch.model),reason});
}
