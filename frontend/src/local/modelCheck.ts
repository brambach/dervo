import {CLAUDE_MODELS,CLAUDE_MODEL_FLOORS,CLAUDE_OLDER,CODEX_LINES,CODEX_RETIREMENTS,CODEX_STARTER,claudeLine} from './models';
import {CLAUDE_NAMES} from './providerLabel';
import {HEARD_MODELS} from './homeLaunch';

/** The per-beta model check (#46, Bryce 3 Oct: "check in each beta release if new models exist"). It compares what
 * Dervo hardcodes with what the providers publish, and says what's new, what's gone and what has no label. Pure: the
 * caller fetches. frontend/scripts/check-models.mjs runs it against the live sources, and modelCheck.test.mjs runs it
 * against saved copies on every test run.
 *
 * Codex lists its models at runtime (model/list), so Dervo's own Codex entries are only lines, words and a few
 * defaults; a model with no line still shows, with Codex's own description. Claude Code has no model list a program
 * can read, so Dervo's Claude list is hardcoded and Anthropic's models overview is what it's checked against. */

export type Finding={level:'error'|'warn'|'info';provider:'claude'|'codex';model:string;says:string};

/** A Codex catalog entry as model/list sends it (camelCase) or as codex-rs/models-manager/models.json has it. */
type RawCodex={model?:string;slug?:string;hidden?:boolean;visibility?:string;minimal_client_version?:string;
  upgrade?:string|{model?:string;retirement_at?:string|null}|null;upgradeInfo?:{model?:string;retirementAt?:number|null}|null};
export type CodexEntry={model:string;minClient?:string;upgradeTo?:string;retiresOn?:string};

const day=(at:string|number|null|undefined)=>{
  if(at===null||at===undefined||at==='')return undefined;
  const when=typeof at==='number'?new Date(at<1e12?at*1000:at):new Date(at);
  return Number.isNaN(when.getTime())?undefined:when.toISOString().slice(0,10);
};
/** The models a catalog offers in a picker, from either shape, hidden ones left out. Accepts the array, `{models}`,
 * `{data}` (a model/list page) or Dervo's model_status answer. */
export function codexListed(catalog:unknown):CodexEntry[]{
  const box=catalog as {models?:unknown;data?:unknown};
  const raw=(Array.isArray(catalog)?catalog:Array.isArray(box?.models)?box.models:Array.isArray(box?.data)?box.data:[]) as RawCodex[];
  return raw.filter(m=>m&&typeof (m.model??m.slug)==='string'&&m.hidden!==true&&(m.visibility===undefined||m.visibility==='list')).map(m=>{
    const upgrade=typeof m.upgrade==='string'?{model:m.upgrade}:m.upgrade??undefined;
    return {model:(m.model??m.slug)!,minClient:m.minimal_client_version,upgradeTo:m.upgradeInfo?.model??upgrade?.model??undefined,
      retiresOn:day(m.upgradeInfo?.retirementAt)??day((upgrade as {retirement_at?:string|null}|undefined)?.retirement_at)};
  });
}
const versionTuple=(v?:string)=>(v??'').split('.').map(n=>parseInt(n,10)||0);
const newer=(a?:string,b?:string)=>{const x=versionTuple(a),y=versionTuple(b);for(let i=0;i<3;i++)if((x[i]??0)!==(y[i]??0))return (x[i]??0)>(y[i]??0);return false;};

/** Codex: a catalog's models against Dervo's lines, words, retirements and defaults. `pinned` is the Codex version
 * Dervo bundles (runtime/codex.json); a catalog model that needs a newer one is said, not counted against Dervo.
 * `homeModel` is backend/home_codex.py's MODEL. */
// i18n-ignore: a developer report for scripts/check-models.mjs and its test, never shown in the app
export function checkCodex(catalog:CodexEntry[],{pinned,homeModel,today=new Date().toISOString().slice(0,10)}:{pinned?:string;homeModel?:string;today?:string}={}):Finding[]{
  const out:Finding[]=[],say=(level:Finding['level'],model:string,says:string)=>out.push({level,provider:'codex',model,says});
  const usable=catalog.filter(m=>!pinned||!m.minClient||!newer(m.minClient,pinned));
  const listed=new Set(usable.map(m=>m.model));
  for(const m of catalog){
    if(pinned&&m.minClient&&newer(m.minClient,pinned)){say('warn',m.model,`needs Codex ${m.minClient}; Dervo bundles ${pinned}, so it won't be offered until runtime/codex.json moves.`);continue;}
    if(!CODEX_LINES[m.model])say('warn',m.model,'no Dervo line in models.ts CODEX_LINES. The picker shows Codex’s own description until one is written.');
    if(!HEARD_MODELS.codex.includes(m.model))say('info',m.model,'Home can’t hear it named in a message (homeLaunch.ts CODEX_WORDS).');
    const known=CODEX_RETIREMENTS[m.model];
    if(m.retiresOn&&!known)say('warn',m.model,`Codex says it retires on ${m.retiresOn}${m.upgradeTo?` for ${m.upgradeTo}`:''}. Add it to models.ts CODEX_RETIREMENTS so pickers say so.`);
    if(known&&m.retiresOn&&m.retiresOn!==known.date)say('warn',m.model,`Codex says it retires on ${m.retiresOn}; CODEX_RETIREMENTS says ${known.date}.`);
    if(known&&m.upgradeTo&&m.upgradeTo!==known.successor)say('info',m.model,`Codex points it at ${m.upgradeTo}; CODEX_RETIREMENTS names ${known.successor}. The catalog's pointer wins at runtime.`);
    if(known&&today>=known.date)say('warn',m.model,`still listed after its retirement day, ${known.date}.`);
  }
  for(const model of Object.keys(CODEX_LINES))if(!listed.has(model)){
    const known=CODEX_RETIREMENTS[model];
    say(known&&today>=known.date?'info':'warn',model,known&&today>=known.date?`retired on ${known.date} and gone from the catalog. Keep its line and CODEX_RETIREMENTS entry while saved threads may still be on it.`
      :'Dervo has a line for it but this catalog doesn’t list it.');
  }
  for(const [model,{successor}] of Object.entries(CODEX_RETIREMENTS))if(!listed.has(successor))say('error',successor,`named as ${model}'s replacement in CODEX_RETIREMENTS, but this catalog doesn't list it, so the move button won't show.`);
  if(!listed.has(CODEX_STARTER))say('error',CODEX_STARTER,'models.ts CODEX_STARTER, the model a new Codex project starts on without a catalog, isn’t listed.');
  if(homeModel&&!listed.has(homeModel))say('error',homeModel,'backend/home_codex.py MODEL, the model Home’s Codex replies run on, isn’t listed. Home refuses to answer on Codex.');
  return out;
}

/** What Anthropic publishes: current models (the overview's "Claude API alias" row), legacy models still available,
 * and the Claude Code version each model needs (the model configuration page's "requires … v2.1.284" sentences). */
export type ClaudeDocs={current:string[];legacy:string[];floors:Record<string,string>};
export function claudeDocs(overview:string,modelConfig=''):ClaudeDocs{
  const row=overview.split('\n').find(line=>/^\|\s*Claude API alias\s*\|/.test(line))??'';
  const current=[...row.matchAll(/`(claude-[a-z0-9-]+)`/g)].map(m=>m[1]);
  const legacyLine=overview.split('\n').find(line=>/^Legacy models/i.test(line))??'';
  const legacy=[...legacyLine.matchAll(/\/models\/([a-z]+-[0-9-]+)\/overview/g)].map(m=>`claude-${m[1]}`);
  const floors:Record<string,string>={};
  // "Sonnet 5.5 requires Claude Code v2.1.284 or later, and Opus 5.5 requires v2.1.280 or later."
  for(const m of modelConfig.matchAll(/\b(Fable|Opus|Sonnet|Haiku|Mythos) (\d+(?:\.\d+)?) requires (?:Claude Code )?v(\d+\.\d+\.\d+)/g))
    floors[`claude-${m[1].toLowerCase()}-${m[2].replace('.','-')}`]=m[3];
  return {current,legacy,floors};
}

/** Claude: Anthropic's lists against Dervo's. `backendFloors` is backend/claude_version.py's MODEL_FLOORS and `floor`
 * its FLOOR, as "2.1.271". */
// i18n-ignore: a developer report for scripts/check-models.mjs and its test, never shown in the app
export function checkClaude(docs:ClaudeDocs,{backendFloors={},floor}:{backendFloors?:Record<string,string>;floor?:string}={}):Finding[]{
  const out:Finding[]=[],say=(level:Finding['level'],model:string,says:string)=>out.push({level,provider:'claude',model,says});
  const main=CLAUDE_MODELS.map(m=>m.id),older=CLAUDE_OLDER.map(m=>m.id);
  if(!docs.current.length){say('error','(none)','couldn’t read the overview’s “Claude API alias” row. Check the page by hand.');return out;}
  for(const id of docs.current)if(!main.includes(id))say('error',id,older.includes(id)?'current on Anthropic’s overview but under Other models in models.ts. Move it to CLAUDE_MODELS.'
    :'a current Claude model Dervo doesn’t offer. Add it to models.ts CLAUDE_MODELS with a line, a name in providerLabel.ts and the words Home hears.');
  for(const id of main)if(!docs.current.includes(id))say(docs.legacy.includes(id)?'warn':'error',id,docs.legacy.includes(id)?'now a legacy model. Move it to CLAUDE_OLDER.':'not on Anthropic’s overview at all. Check whether it retired.');
  for(const id of older)if(!docs.current.includes(id)&&!docs.legacy.includes(id))say('warn',id,'under Other models but not listed as current or legacy. Check whether it retired.');
  for(const [id,need] of Object.entries(docs.floors)){
    if(!main.includes(id)&&!older.includes(id))continue;
    const ours=CLAUDE_MODEL_FLOORS[id];
    if(floor&&!newer(need,floor)){if(ours)say('info',id,`needs ${need}, which Dervo's floor ${floor} already covers; CLAUDE_MODEL_FLOORS can drop it.`);continue;}
    if(ours!==need)say('error',id,`Claude Code ${need} or later per the model configuration page; models.ts CLAUDE_MODEL_FLOORS says ${ours??'nothing'}.`);
    if(backendFloors[id]!==need)say('error',id,`Claude Code ${need} or later per the model configuration page; claude_version.py MODEL_FLOORS says ${backendFloors[id]??'nothing'}.`);
  }
  return out;
}

/** Dervo against itself, with no network: every Claude model it offers has a name, a line and the words Home hears,
 * the floors name offered models, and every retirement points at a model with a line. */
// i18n-ignore: a developer report for scripts/check-models.mjs and its test, never shown in the app
export function checkDervo():Finding[]{
  const out:Finding[]=[],say=(level:Finding['level'],provider:Finding['provider'],model:string,says:string)=>out.push({level,provider,model,says});
  for(const {id,label} of [...CLAUDE_MODELS,...CLAUDE_OLDER]){
    if(CLAUDE_NAMES[id]!==label)say('error','claude',id,`providerLabel.ts names it ${CLAUDE_NAMES[id]??'nothing'}; models.ts says ${label}.`);
    if(!claudeLine(id))say('error','claude',id,'no line in models.ts claudeLine.');
  }
  for(const {id} of CLAUDE_MODELS)if(!HEARD_MODELS.claude.includes(id))say('warn','claude',id,'Home can’t hear it named in a message (homeLaunch.ts CLAUDE_WORDS).');
  for(const id of HEARD_MODELS.claude)if(!CLAUDE_NAMES[id])say('error','claude',id,'Home hears it, but providerLabel.ts has no name for it.');
  for(const id of Object.keys(CLAUDE_MODEL_FLOORS))if(!CLAUDE_MODELS.some(m=>m.id===id))say('error','claude',id,'has a floor in CLAUDE_MODEL_FLOORS but isn’t offered.');
  for(const [model,{successor,date}] of Object.entries(CODEX_RETIREMENTS)){
    if(!CODEX_LINES[successor])say('error','codex',successor,`${model}'s replacement has no line in CODEX_LINES.`);
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date))say('error','codex',model,`retirement date ${date} isn't YYYY-MM-DD.`);
  }
  if(!CODEX_LINES[CODEX_STARTER])say('error','codex',CODEX_STARTER,'CODEX_STARTER has no line.');
  for(const id of HEARD_MODELS.codex)if(!CODEX_LINES[id])say('warn','codex',id,'Home hears it, but CODEX_LINES has no line for it.');
  return out;
}

/** The report, one line per finding, errors first. */
export function report(findings:Finding[]):string{
  const order={error:0,warn:1,info:2};
  return [...findings].sort((a,b)=>order[a.level]-order[b.level]||a.provider.localeCompare(b.provider)).map(f=>`${f.level.toUpperCase().padEnd(5)} ${f.provider.padEnd(6)} ${f.model}: ${f.says}`).join('\n');
}
