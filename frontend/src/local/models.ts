import type {UsageAccount} from './api';
import {accountLabel} from './DervoSidebar';
import {modelName} from './providerLabel';
import {formatDate,formatList,t} from '@/i18n';

/** What the model picker and Account and model say about accounts, models and thinking (design sweep, 19 Sep, and
 * PLAN-native.md section 3, A8). One honest line per model and per thinking level, the name you gave each account, and
 * Fable's paid credits said once. Nothing here calls the host. */

export type Profile={id:string;provider:string;identity:string;category:string;method?:string;monthly_limit_usd?:number|null;
  /** The Claude plan auth status last reported (provider_plans.py), null until a check or a run reports it. */
  plan?:string|null;
  /** You've acknowledged Fable's usage notice on this account, so nothing asks again. */
  fable_confirmed?:boolean;
  /** The name you gave the account on Connected accounts ("Personal", "Acme"), when the host sends one. */
  name?:string|null};
export type Effort={reasoningEffort:string;description?:string};
/** Codex's own pointer from a model to the one that replaces it (model/list's `upgradeInfo`). `retirementAt` is seconds. */
export type UpgradeInfo={model:string;upgradeCopy?:string|null;modelLink?:string|null;migrationMarkdown?:string|null;retirementAt?:number|null};
export type CatalogModel={model:string;displayName?:string;description?:string;hidden?:boolean;supportedReasoningEfforts?:(Effort|string)[];defaultReasoningEffort?:string;
  upgrade?:string|null;upgradeInfo?:UpgradeInfo|null};
export type Catalog={state:string;detail:string;models:CatalogModel[]};

/** Published Claude Code model IDs, strongest first, the way the picker lists them. Exact IDs only: the `best` alias is
 * never offered, because on Pro it resolves to Fable (A4, claude_policy.ALIASES_REFUSED). Haiku 4.5 is `claude-haiku-4-5`,
 * the ID the Claude API model table gives (checked 19 Sep 2026); not checked against a live Claude Code. Opus 5.5 is
 * `claude-opus-5-5`, released 22 Sep 2026 and Claude Code's default from 2.1.280 (model configuration, checked 23 Sep).
 * Sonnet 5.5 is `claude-sonnet-5-5`, released 28 Sep 2026, and needs Claude Code 2.1.284 (the models overview and model
 * configuration pages, checked 29 Sep). */
// i18n-ignore: model names
export const CLAUDE_MODELS:{id:string;label:string}[]=[
  {id:'claude-fable-5-1',label:'Fable 5.1'},{id:'claude-opus-5-5',label:'Opus 5.5'},{id:'claude-sonnet-5-5',label:'Sonnet 5.5'},{id:'claude-haiku-4-5',label:'Haiku 4.5'},
];
/** Older models that still work, behind "Other models". A thread already on one keeps showing it in the picker. */
// i18n-ignore: model names
export const CLAUDE_OLDER:{id:string;label:string}[]=[{id:'claude-opus-5',label:'Opus 5'},{id:'claude-sonnet-5',label:'Sonnet 5'},{id:'claude-fable-5',label:'Fable 5'}];
/** Models that need a newer Claude Code than Dervo's own floor. backend/claude_version.py refuses a run below it with
 * the same number, so a model picked here never fails partway. */
export const CLAUDE_MODEL_FLOORS:Record<string,string>={'claude-opus-5-5':'2.1.280','claude-sonnet-5-5':'2.1.284'};
/** What takes a floored model's place on a Claude Code too old for it: the earlier model every supported version runs. */
export const CLAUDE_FALLBACKS:Record<string,{id:string;label:string}>={'claude-opus-5-5':{id:'claude-opus-5',label:'Opus 5'},'claude-sonnet-5-5':{id:'claude-sonnet-5',label:'Sonnet 5'}};
const versionParts=(text?:string|null)=>{const found=/(\d{1,4})\.(\d{1,4})\.(\d{1,6})/.exec(text??'');return found?[+found[1],+found[2],+found[3]]:null;};
/** Whether a Claude Code version is at least `floor`. An unreadable version isn't. */
export function claudeVersionAtLeast(version:string|null|undefined,floor:string):boolean{
  const have=versionParts(version),need=versionParts(floor);
  if(!have||!need)return false;
  for(let i=0;i<3;i++)if(have[i]!==need[i])return have[i]>need[i];
  return true;
}
/** The Claude model a first run or new project starts on: Opus 5.5 once this Mac's Claude Code can run it, otherwise
 * Opus 5, which every Claude Code Dervo supports runs. The same rule as claude_version.default_model. */
export function claudeDefaultModel(version?:string|null):string{
  return claudeVersionAtLeast(version,CLAUDE_MODEL_FLOORS['claude-opus-5-5'])?'claude-opus-5-5':'claude-opus-5';
}
/** The Claude list a picker shows. On a Claude Code that's known to be too old for Opus 5.5 or Sonnet 5.5, Opus 5 or
 * Sonnet 5 takes its place, so the model a run would refuse isn't offered and the one it runs is. An unknown version keeps
 * the newer models: the backend only refuses a version it has read. */
export function claudeModelsFor(version?:string|null):{id:string;label:string}[]{
  if(!versionParts(version))return CLAUDE_MODELS;
  return CLAUDE_MODELS.map(m=>CLAUDE_MODEL_FLOORS[m.id]&&!claudeVersionAtLeast(version,CLAUDE_MODEL_FLOORS[m.id])?CLAUDE_FALLBACKS[m.id]:m);
}
/** "Other models" for the same version: whatever claudeModelsFor doesn't already show. */
export const claudeOlderFor=(version?:string|null)=>{const shown=claudeModelsFor(version);return CLAUDE_OLDER.filter(m=>!shown.some(s=>s.id===m.id));};
/** Every Claude model Dervo names, as [id, label]. */
export const CLAUDE_CHOICES:[string,string][]=[...CLAUDE_MODELS,...CLAUDE_OLDER].map(m=>[m.id,m.label]);
/** Models with no thinking levels, so Thinking is hidden for them (the effort API page: Haiku 4.5 takes no effort). */
export const NO_THINKING=['claude-haiku-4-5','claude-haiku-4-5-20251001'];

/** Team auth status doesn't identify the seat. Never infer paid-only Fable access from that plan alone.
 * Source: support.claude.com/en/articles/15424964, checked 22 Sep 2026. */
export const PAID_CREDIT_PLANS=['pro'];
export const isFable=(model:string)=>/^claude-fable-/.test(model);
export const fableCostsCredits=(plan?:string|null)=>!!plan&&PAID_CREDIT_PLANS.includes(plan);
export const fableCreditLabel=(plan?:string|null)=>plan==='team'?t('Credits may apply'):fableCostsCredits(plan)?t('Paid credits'):undefined;
export const TEAM_FABLE_NOTICE=t('Premium seats include Fable for up to 50% of your weekly usage limits. Standard seats use paid credits from the first message. Continuing beyond the included Fable limit uses credits. Dervo can’t detect your seat type.');
/** Acknowledge possible credit use once. Team's notice explains both seats without claiming to identify yours. */
export const asksBeforeFable=(profile:Pick<Profile,'provider'|'plan'|'fable_confirmed'>,model:string)=>
  profile.provider==='claude'&&isFable(model)&&!!fableCreditLabel(profile.plan)&&!profile.fable_confirmed;

/** Dervo's line per Claude model: what it's for, how fast, and what it does to your plan's limits. Anthropic publishes
 * no per-model limit ratios except for Fable, so these say less than the Codex lines. Pro has a paid-credits tag;
 * Team's tag preserves seat uncertainty. On Max Fable uses weekly limits
 * faster. Sources, checked 19 Sep 2026 (evidence/native-20260919/models-and-pickers.md, section 6): Claude Code's model
 * configuration page, the models overview (comparative latency), "Claude Fable models on your plan", the Opus 5 and
 * Haiku 4.5 posts, the usage-limit pages (Opus has its own limit), and the Claude Code costs page. Opus 5.5, checked 23
 * Sep 2026: its overview (long-running agentic coding, moderate latency) and the model configuration page (default from
 * Claude Code 2.1.280). Whether it shares Opus 5's separate limit wasn't published, so its line doesn't say. Sonnet 5.5,
 * checked 29 Sep 2026: its overview (fast, same price as Sonnet 5) and the model configuration page (2.1.284). Recheck monthly. */
export function claudeLine(model:string,plan?:string|null):string{
  const heavy=plan==='max'?` ${t('Uses weekly limits faster than other models.')}`:'';
  switch(model){
    case 'claude-fable-5-1':return `${t('Hardest, longest tasks. Slowest.')}${fableCostsCredits(plan)?'':heavy}`;
    case 'claude-opus-5-5':return t('Complex coding and long agent work. Moderate speed. Needs Claude Code {version} or later.',{version:'2.1.280'});
    case 'claude-opus-5':return t('The earlier Opus. Moderate speed. Has its own Opus limit.');
    case 'claude-sonnet-5-5':return t('Everyday coding. Fast. Costs less than Opus. Needs Claude Code {version} or later.',{version:'2.1.284'});
    case 'claude-sonnet-5':return t('The earlier Sonnet. Fast. Costs less than Opus.');
    case 'claude-haiku-4-5':case 'claude-haiku-4-5-20251001':return t('Quick, simple tasks. Fastest. Stretches your limits. No thinking levels.');
    case 'claude-fable-5':return `${t('The earlier Fable. 5.1 is stronger.')}${fableCostsCredits(plan)?'':heavy}`;
    default:return '';
  }
}
/** Dervo's line per Codex model. "A third", "about as many" and "twenty times" are Dervo's reading of OpenAI's messages
 * per five hours table (the top of each Plus range), not OpenAI's words. GPT-6 Sol is the yardstick. Sources, checked 5
 * Oct 2026: the Codex models page (learn.chatgpt.com/docs/models: Astra, GPT-6.1 Sol and GPT-6 Luna recommended, GPT-6.1
 * Sol "near-Astra performance for complex work at a lower cost than Astra", GPT-5.6 kept during the rollout, GPT-5.5
 * retiring on 14 October), the pricing page (Plus: Astra 45, GPT-6.1 Sol 160, GPT-6 Sol 150, GPT-6 Luna 3,000), and
 * Codex 0.160.0's bundled catalog (GPT-6.1 Sol "Latest workhorse model", GPT-6 Sol "Previous generation workhorse
 * model"). scripts/check-models.mjs flags a catalog model with no line here. */
export const CODEX_LINES:Record<string,string>={
  'gpt-6-astra':t('Hardest end-to-end work. Slower. About a third of GPT-6 Sol’s messages.'),
  'gpt-6.1-sol':t('Complex coding and everyday work, close to Astra. About as many messages as GPT-6 Sol.'),
  'gpt-6-sol':t('Complex coding and agent work. OpenAI’s replacement for GPT-5.5.'),
  'gpt-6-luna':t('Focused, high-volume tasks. About twenty times GPT-6 Sol’s messages.'),
  'gpt-5.6-sol':t('Previous generation of Sol. Available while GPT-6 rolls out.'),
  'gpt-5.6-terra':t('Everyday work. Previous generation, available while GPT-6 rolls out.'),
  'gpt-5.6-luna':t('Previous generation of Luna. Available while GPT-6 rolls out.'),
  'gpt-5.5':t('Previous generation.'),
};
/** Codex models OpenAI has said leave ChatGPT sign-in, the day they go and what it names in their place. Codex's own
 * catalog wins when its upgradeInfo gives a successor or a date; this covers a catalog that gives neither (0.160.0's
 * bundled one has GPT-5.5's pointer to GPT-6 Sol with no date) and a thread whose model the catalog no longer lists.
 * Source: learn.chatgpt.com/docs/models, checked 5 Oct 2026: "On October 14, 2026, GPT-5.5 will retire from ChatGPT,
 * ChatGPT Work, and Codex on all plans", replaced by GPT-6 Sol on paid plans. It stays on the API. */
export const CODEX_RETIREMENTS:Record<string,{successor:string;date:string}>={'gpt-5.5':{successor:'gpt-6-sol',date:'2026-10-14'}};
/** A model on its way out of Codex: the model, what replaces it, and when it goes (ms, local midnight), if known. */
export type Retirement={model:string;successor:string;at:number|null};
const dayStart=(date:string)=>{const [y,m,d]=date.split('-').map(Number);return new Date(y,m-1,d).getTime();};
/** Whether a Codex model is leaving, from the catalog first and Dervo's list second. An upgrade pointer alone isn't a
 * retirement: GPT-5.6 points at GPT-6 too and stays offered, so it needs a date or an entry in CODEX_RETIREMENTS. */
export function retirementFor(model:string|null|undefined,catalog?:CatalogModel[]|null):Retirement|null{
  if(!model)return null;
  const entry=catalog?.find(m=>m.model===model),known=CODEX_RETIREMENTS[model];
  const stamp=entry?.upgradeInfo?.retirementAt;
  const at=typeof stamp==='number'&&stamp>0?(stamp<1e12?stamp*1000:stamp):known?dayStart(known.date):null;
  const successor=entry?.upgradeInfo?.model||entry?.upgrade||known?.successor;
  return successor&&at!==null&&successor!==model?{model,successor,at}:null;
}
/** Whether a retirement's day has come. */
export const hasLeft=(retirement:Retirement,now=Date.now())=>retirement.at!==null&&now>=retirement.at;
/** "Leaves Codex on 14 October." before the day, "Left Codex on 14 October." from it. */
const leavingDay=(retirement:Retirement)=>formatDate(new Date(retirement.at!),{day:'numeric',month:'long'});
export function leavingLine(retirement:Retirement,now=Date.now()):string{
  const day=leavingDay(retirement);
  return hasLeft(retirement,now)?t('Left Codex on {day}.',{day}):t('Leaves Codex on {day}.',{day});
}
/** The same with the model named: "GPT-5.5 leaves Codex on 14 October." */
export function modelLeavingLine(model:string,retirement:Retirement,now=Date.now()):string{
  const day=leavingDay(retirement);
  return hasLeft(retirement,now)?t('{model} left Codex on {day}.',{model,day}):t('{model} leaves Codex on {day}.',{model,day});
}
/** One line for a Codex model: Dervo's own when it has one, otherwise the description Codex's catalog gives at
 * runtime (A8), otherwise nothing, then when it leaves Codex if it's leaving. The catalog's own words also sit in the
 * row's tooltip. */
export function codexLine(entry:CatalogModel,now=Date.now()):string{
  const line=CODEX_LINES[entry.model]??entry.description?.trim()??'';
  const leaving=retirementFor(entry.model,[entry]);
  return leaving?[line,leavingLine(leaving,now)].filter(Boolean).join(' '):line;
}
/** The Codex model Dervo starts a new project or account on when the account's catalog can't be read: OpenAI's named
 * replacement for GPT-5.5, which Codex 0.156.1 lists. The run still checks it against the catalog before sending. */
export const CODEX_STARTER='gpt-6-sol';

const EFFORT_LABELS:Record<string,string>={none:t('None'),minimal:t('Minimal'),low:t('Low'),medium:t('Medium'),high:t('High'),xhigh:t('Extra high'),max:t('Max|thinking level'),ultra:t('Ultra')};
/** A level's name. Codex's `ultra` read "ultra" in lower case before (PLAN-native section 3, bug one). */
export const effortLabel=(level:string)=>EFFORT_LABELS[level]??level.charAt(0).toUpperCase()+level.slice(1);
/** Levels behind More: they use limits fastest, and most tasks don't need them (the Codex TUI does the same). */
export const HEAVY_EFFORTS=['max','ultra'];
/** The line under More once it's open: "Max and Ultra use your limits faster. Most tasks don't need them." */
export const heavyLine=(heavy:string[])=>heavy.length>1?t('{levels} use your limits faster. Most tasks don’t need them.',{levels:formatList(heavy.map(effortLabel))})
  :t('{level} uses your limits faster. Most tasks don’t need it.',{level:effortLabel(heavy[0]??'')});
const CLAUDE_EFFORTS=['low','medium','high','xhigh','max'];
/** Dervo's line per level, the same for both providers (PLAN-native section 3). Effort is calibrated per model, so none
 * of these compares models. Codex's own description of a level, when its catalog gives one, sits in the tooltip. */
export function effortLine(level:string):string{
  switch(level){
    case 'none':return t('No reasoning. For the quickest replies.');
    case 'minimal':return t('Barely thinks. For the simplest replies.');
    case 'low':return t('Fastest and lightest on limits. For short, simple jobs.');
    case 'medium':return t('Saves limits on routine work, with some quality lost.');
    case 'high':return t('The best balance of quality and speed.');
    case 'xhigh':return t('Deeper reasoning for long coding tasks. Uses more limits.');
    case 'max':return t('Most thorough and slowest. Heaviest on limits. Can overthink.');
    case 'ultra':return t('Splits the work across parallel agents. Most tasks don’t need it.');
    default:return '';
  }
}
const catalogEfforts=(entry?:CatalogModel):Effort[]=>(entry?.supportedReasoningEfforts??[])
  .map(e=>typeof e==='string'?{reasoningEffort:e}:e).filter((e):e is Effort=>typeof e?.reasoningEffort==='string'&&!!e.reasoningEffort);
/** The thinking levels a model offers: Claude Code's documented levels (none for Haiku), or what the Codex catalog lists. */
export function effortsFor(provider:string,model:string,catalog?:CatalogModel[]):string[]{
  if(provider==='claude')return NO_THINKING.includes(model)?[]:[...CLAUDE_EFFORTS];
  return catalogEfforts(catalog?.find(m=>m.model===model)).map(e=>e.reasoningEffort);
}
/** Codex's own words for a level, from its catalog. Claude's catalog isn't read yet (PLAN-native B5). */
export const effortDescription=(model:string,level:string,catalog?:CatalogModel[])=>catalogEfforts(catalog?.find(m=>m.model===model)).find(e=>e.reasoningEffort===level)?.description;
/** What the provider's own Default resolves to, when Dervo can read it: for Codex, the level the catalog names for the
 * model. For Claude Code, nothing. Default sends no --effort and Claude Code doesn't report which level it then uses, so
 * Dervo doesn't name one (16.4 item 12: "Default is Medium" was a guess from Anthropic's docs, and wrong for Harrison). */
export function defaultEffort(provider:string,model:string,catalog?:CatalogModel[]):string|undefined{
  if(provider==='claude')return undefined;
  return catalog?.find(m=>m.model===model)?.defaultReasoningEffort;
}
/** The provider's own default in words, when nothing in Dervo sets a level. */
export function providerDefaultLine(provider?:string,model?:string,resolved?:string):string{
  const claude=provider?provider==='claude':!!model?.startsWith('claude');
  // Dervo starts Claude Code with no settings sources and its own environment (claude_policy.argv,
  // transport.clean_environment), so a level set in Claude Code itself never reaches a Dervo run.
  if(claude)return model?t('Claude Code’s own default for {model}. Dervo can’t see which level that is, and a level set in Claude Code itself doesn’t apply here.',{model:modelLabel('claude',model)})
    :t('Claude Code’s own default. Dervo can’t see which level that is, and a level set in Claude Code itself doesn’t apply here.');
  if(!resolved)return t('The provider’s default.');
  const line=effortLine(resolved);
  return line?t('Default is {level}: {line}',{level:effortLabel(resolved),line:`${line.charAt(0).toLowerCase()}${line.slice(1)}`}):t('Default is {level}.',{level:effortLabel(resolved)});
}
/** The line under Thinking: "High: the best balance of quality and speed." With no level, what the provider does. */
export function thinkingLine(level:string|null|undefined,resolved:string|undefined,model?:string,provider?:string):string{
  if(!level)return providerDefaultLine(provider,model,resolved);
  const line=effortLine(level);
  return line?`${effortLabel(level)}: ${line.charAt(0).toLowerCase()}${line.slice(1)}`:effortLabel(level);
}

/** Where a run's thinking level comes from (16.4, Bryce 28 Sep): the first one set of this thread, this project and
 * your default for the account, else the provider's own. The host sends it as coding_permission's ``efforts``. */
export type EffortSource='thread'|'project'|'personal'|null;
export type Efforts={thread:string|null;project:string|null;personal:string|null;effective:string|null;source:EffortSource};
export const EFFORT_SOURCES:Record<Exclude<EffortSource,null>,string>={thread:t('This thread'),project:t('This project’s default'),personal:t('Your default')};
/** The level a thread falls back to without its own, and where that comes from. */
export function effortFallback(efforts:Efforts):{level:string|null;source:EffortSource}{
  if(efforts.project)return {level:efforts.project,source:'project'};
  if(efforts.personal)return {level:efforts.personal,source:'personal'};
  return {level:null,source:null};
}
/** The line for a thread's Default: the level it falls back to and whose it is, or what the provider does. */
export function defaultLine(efforts:Efforts,resolved:string|undefined,model:string,provider:string):string{
  const fallback=effortFallback(efforts);
  if(!fallback.level)return providerDefaultLine(provider,model,resolved);
  return fallback.source==='project'?t('Default here is {level}, this project’s default.',{level:effortLabel(fallback.level)}):t('Default here is {level}, your default.',{level:effortLabel(fallback.level)});
}
/** "Use for new threads" says which default it writes: the project's when it has one, otherwise yours. */
export const promoteLabel=(efforts:Efforts)=>efforts.project?t('Use for new threads in this project'):t('Use for all new threads');
/** The picker's footer: where the level comes from, and the level. */
export function effortSourceLine(efforts:Efforts,provider:string):string{
  if(!efforts.source)return provider==='claude'?t('Claude Code’s own default'):PROVIDER_NAMES[provider]?t('{provider}’s own default',{provider:PROVIDER_NAMES[provider]}):t('The provider’s own default');
  return `${EFFORT_SOURCES[efforts.source]} · ${effortLabel(efforts.effective!)}`;
}
/** The chip names the thread's level only when it differs from the default it would fall back to. */
export function chipEffort(efforts:Efforts|undefined):string|null{
  if(!efforts?.thread)return null;
  return efforts.thread===effortFallback(efforts).level?null:efforts.thread;
}

export function modelLabel(provider:string,model:string,catalog?:CatalogModel[]){
  if(provider==='claude')return CLAUDE_CHOICES.find(([id])=>id===model)?.[1]??modelName(model);
  return modelName(model,catalog?.find(m=>m.model===model)?.displayName);
}
export const PROVIDER_NAMES:Record<string,string>={claude:'Claude',codex:'Codex'};

/** One account as the picker and project settings show it. */
export type Account={id:string;provider:string;profile:Profile;name:string;identity:string;
  /** A work account (b16-work): it only ever runs Work projects. */
  work:boolean;
  /** "Pro", "Team", "Plus", "API key": the plan, when the provider reported one. */
  plan:string;usage?:UsageAccount;
  /** What's left of the reported window, 0 to 100, or null when nothing honest fills it. */
  left:number|null;resets:string};
// i18n-ignore: the providers' own plan names
const CLAUDE_PLANS:Record<string,string>={pro:'Pro',max:'Max',team:'Team',enterprise:'Enterprise',free:'Free'};
// i18n-ignore: the providers' own plan names
const CODEX_PLANS:Record<string,string>={plus:'Plus',prolite:'Pro Lite',pro:'Pro',team:'Team',business:'Business',enterprise:'Enterprise',edu:'Edu',free:'Free'};
/** A window reset, as seconds since the epoch: "10:00 PM" today, "Wed 10:00 PM" later. */
export function resetWhen(at:number|null|undefined,now=Date.now()):string{
  if(typeof at!=='number'||at<1e9||at>4e9)return '';
  const when=new Date(at*1000),time=formatDate(when,{hour:'numeric',minute:'2-digit'});
  return when.toDateString()===new Date(now).toDateString()?time:`${formatDate(when,{weekday:'short'})} ${time}`;
}
/** Your personal accounts, Claude first then Codex, each by the name you gave it. Without a name each falls back to
 * the sidebar's label ("Personal", "ChatGPT Plus", "API key"), and two that would read the same show their sign-in. */
/** Work or Personal accounts, or both. A Work project lists only work accounts and a Personal one only personal
 * accounts, because nothing else can run it (dervo_connections.require_context). */
export type Side='work'|'personal'|'both';
export function accountsFrom(profiles:Profile[],usage:UsageAccount[]=[],now=Date.now(),side:Side='personal'):Account[]{
  const personal=profiles.filter(p=>(side==='both'?p.category==='personal'||p.category==='work':p.category===side)&&(p.provider==='claude'||p.provider==='codex'));
  const rows=personal.map(profile=>{
    const used=usage.find(u=>u.profile_id===profile.id);
    const named=profile.name?.trim()||used?.name?.trim()||'';
    const label=named||accountLabel({provider:profile.provider,method:profile.method??'subscription',rate_limits:used?.rate_limits,name:null});
    const planType=used?.rate_limits?.planType;
    // i18n-ignore: "API key" is the plan's value, compared elsewhere; planLabel shows it
    const plan=profile.method==='api_key'?'API key':profile.provider==='claude'?CLAUDE_PLANS[profile.plan??'']??'':
      typeof planType==='string'?CODEX_PLANS[planType.toLowerCase()]??'':'';
    const left=used&&used.percent!==null&&used.basis!=='runs'?Math.max(0,Math.min(100,100-used.percent)):null;
    return {id:profile.id,provider:profile.provider,profile,name:label,named:!!named,identity:profile.identity,work:profile.category==='work',plan,usage:used,left,resets:used?.basis==='rate_limits'?resetWhen(used.resets_at,now):''};
  });
  const count=(name:string)=>rows.filter(row=>row.name===name).length;
  const order=(provider:string)=>provider==='claude'?0:1;
  return rows.map(({named,...row})=>!named&&count(row.name)>1?{...row,name:row.identity}:row).sort((a,b)=>order(a.provider)-order(b.provider));
}
/** What an account's row says under its name: "Pro · 64% left", "API key · $12 of $50", or "No usage yet". */
export function accountCaption(account:Account):string{
  const u=account.usage;
  const amount=u?.basis==='spend_limit'&&u.monthly_limit_usd?t('{spent} of {limit}',{spent:`$${Math.round(u.cost_month_usd??0)}`,limit:`$${Math.round(u.monthly_limit_usd)}`})
    :account.left!==null?t('{percent}% left',{percent:account.left}):t('No usage yet');
  return account.plan?`${planLabel(account.plan)} · ${amount}`:amount;
}
/** The models pane's heading caption: "Claude Pro · resets Wed 10:00 PM", "Codex · resets Sat 12:00 AM". */
export function accountDetail(account:Account):string{
  const provider=PROVIDER_NAMES[account.provider]??account.provider;
  const plan=account.plan&&account.plan!=='API key'&&!account.name.includes(account.plan)?` ${account.plan}`:account.plan==='API key'?` ${t('API key')}`:'';
  return `${provider}${plan}${account.resets?` · ${t('resets {when}',{when:account.resets})}`:''}`;
}
/** An account's plan as shown: the provider's own name, or "API key" in the window's language. `plan` itself stays
 * "API key", which other code compares against. */
export const planLabel=(plan:string)=>plan==='API key'?t('API key'):plan;
/** Whether two of your accounts share a provider, so the composer chip names the account. */
export const accountsShareProvider=(accounts:Account[])=>new Set(accounts.map(a=>a.provider)).size<accounts.length;
