#!/usr/bin/env node
// The per-beta model check (#46, Bryce 3 Oct 2026: "check in each beta release if new models exist").
//
// Run it from frontend/ when planning each beta, before Dervo's model lists are frozen:
//   pnpm check:models
//   pnpm check:models -- --codex-catalog <file.json> --save <folder>
//
// It reads only public pages. No account, no model call, nothing from ~/.claude, ~/.codex or Dervo's data folder:
//   - Codex: codex-rs/models-manager/models.json from openai/codex at the version Dervo bundles (runtime/codex.json)
//     and at the newest @openai/codex on npm. The bundled catalog is what Codex falls back on; the newest one shows
//     what OpenAI has added since, and which of those need a newer Codex than Dervo bundles.
//   - Claude: Anthropic's models overview (current and legacy models) and Claude Code's model configuration page (the
//     version each model needs). Claude Code has no model list a program can read, so these pages are the source.
//   - Dervo: models.ts, providerLabel.ts and homeLaunch.ts through modelCheck.ts, and backend/home_codex.py MODEL and
//     backend/claude_version.py FLOOR and MODEL_FLOORS read as text.
// A signed-in account's live catalog is the real answer for Codex, and only Dervo can read it. Pass one with
// --codex-catalog: a saved model/list page, Dervo's model_status answer, or scripts/check-local-codex-catalog.py's
// signed-in-catalog.json all work.
//
// ERROR means a picker, a default or Home would be wrong for someone today. WARN means a list needs a line or a move.
// INFO is for reading. It exits 1 on any ERROR. modelCheck.test.mjs runs the same comparisons on saved copies in every
// test run, so drift inside Dervo (a model with no name, a floor on a model nobody offers) fails there first.
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {checkClaude,checkCodex,checkDervo,claudeDocs,codexListed,report} from '../src/local/modelCheck.ts';

const here=dirname(fileURLToPath(import.meta.url)),root=resolve(here,'../..');
const args=process.argv.slice(2),option=name=>{const at=args.indexOf(name);return at>=0?args[at+1]:undefined;};
const save=option('--save'),liveFile=option('--codex-catalog');
const SOURCES={
  codex:version=>`https://raw.githubusercontent.com/openai/codex/rust-v${version}/codex-rs/models-manager/models.json`,
  npm:'https://registry.npmjs.org/@openai/codex/latest',
  overview:'https://platform.claude.com/docs/en/models/overview.md',
  modelConfig:'https://code.claude.com/docs/en/model-config.md',
};
async function get(url,name){
  const response=await fetch(url,{redirect:'follow'});
  if(!response.ok)throw new Error(`${url}: HTTP ${response.status}`);
  const text=await response.text();
  if(save){mkdirSync(save,{recursive:true});writeFileSync(join(save,name),text);}
  return text;
}
const backend=file=>readFileSync(join(root,'backend',file),'utf8');
const pinned=JSON.parse(readFileSync(join(root,'runtime/codex.json'),'utf8')).version;
const homeModel=/^MODEL\s*=\s*'([^']+)'/m.exec(backend('home_codex.py'))?.[1];
const claudeVersion=backend('claude_version.py');
const floor=/^FLOOR\s*=\s*\((\d+),\s*(\d+),\s*(\d+)\)/m.exec(claudeVersion)?.slice(1).join('.');
const backendFloors=Object.fromEntries([...(/^MODEL_FLOORS\s*=\s*\{([^}]*)\}/m.exec(claudeVersion)?.[1]??'').matchAll(/'([^']+)':\s*\((\d+),\s*(\d+),\s*(\d+)\)/g)].map(m=>[m[1],`${m[2]}.${m[3]}.${m[4]}`]));

const sections=[];
const add=(title,findings)=>{sections.push({title,findings});};
add('Dervo against itself',checkDervo());
const latest=JSON.parse(await get(SOURCES.npm,'npm-codex-latest.json')).version;
add(`Codex ${pinned} (bundled) catalog`,checkCodex(codexListed(JSON.parse(await get(SOURCES.codex(pinned),`codex-${pinned}-models.json`))),{homeModel}));
if(latest!==pinned){
  const fresh=checkCodex(codexListed(JSON.parse(await get(SOURCES.codex(latest),`codex-${latest}-models.json`))),{pinned,homeModel});
  // A newer catalog can only add news: a model it drops may still be served to the Codex Dervo bundles.
  add(`Codex ${latest} (newest on npm) catalog, against bundled ${pinned}`,fresh.filter(f=>!/doesn’t list|isn’t listed|doesn't list/.test(f.says)).map(f=>f.level==='error'?{...f,level:'warn'}:f));
}
if(liveFile)add(`Live catalog from ${liveFile}`,checkCodex(codexListed(JSON.parse(readFileSync(liveFile,'utf8'))),{homeModel}));
add('Claude, from Anthropic’s overview and Claude Code’s model page',checkClaude(claudeDocs(await get(SOURCES.overview,'claude-models-overview.md'),await get(SOURCES.modelConfig,'claude-code-model-config.md')),{backendFloors,floor}));

let errors=0;
for(const {title,findings} of sections){
  errors+=findings.filter(f=>f.level==='error').length;
  console.log(`\n${title}: ${findings.length?`${findings.length} to look at`:'nothing to change'}`);
  if(findings.length)console.log(report(findings));
}
console.log(`\nSources: ${SOURCES.npm} (${latest}), ${SOURCES.codex(pinned)}, ${SOURCES.overview}, ${SOURCES.modelConfig}. Checked ${new Date().toISOString().slice(0,10)}.`);
process.exit(errors?1:0);
