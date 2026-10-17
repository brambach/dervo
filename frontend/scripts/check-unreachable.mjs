#!/usr/bin/env node
// Unreachable components: a screen nothing mounts (20 Sep, #16).
//
// Three finished components turned out to be invisible on the same day: ProjectCatchup, the header control that opens
// it, and QuestionTray. Each was built, tested and reviewed, and every test passed, because a component's own tests
// import it directly and never ask whether the app does. Nothing in the gate could tell a mounted screen from a dead
// one, so this step does.
//
// WHAT COUNTS AS A COMPONENT, and why this line and not another. A component here is an exported function whose name
// starts with a capital, declared in a `.tsx` file under frontend/src/local. Three deliberate narrowings:
//   - `.tsx` only. JSX can't be written anywhere else, so a `.ts` file cannot hold a thing a screen mounts. This also
//     keeps questionShape.ts, homeTime.ts and every other rules-and-helpers module out of the count, which is right:
//     an unused helper is dead code, not an invisible feature, and it's a different (much noisier) check.
//   - a capital and a `function`. Every exported `const` with a capital in src/local today is a SCREAMING_CASE
//     constant (LIMITS, SMILE, STATES), so the capital alone separates components from helpers with no false hits.
//     If an arrow component ever lands, add it here rather than widening to every export.
//   - exported only. A component used solely inside its own file needs no export and isn't what went wrong.
// Every exported function would be too noisy to be worth having, and a check people switch off is worse than none.
//
// REACHABLE means: some file that is not a test and not the component's own file imports that name. Tests are not
// importers by design; that's the exact blind spot. A dynamic `import()` counts for every export of the module it
// names, because React.lazy reaches in through a property this script doesn't try to read, and a false alarm would
// cost more than the miss.
//
// WHAT IT DOESN'T DO: it stops at one level. A component imported only by another unreachable component reads as
// reachable here. A walk from the app's entry point would catch that, but one broken link would flag a whole subtree
// and nobody would act on the list. One level caught all three of the day's cases.
//
// Usage: node scripts/check-unreachable.mjs            exits 1 and lists them
//        node scripts/check-unreachable.mjs --report   lists them and exits 0
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Components that are exported and unmounted on purpose, as `relativePath:ExportName`. A line without a reason is not
// an exception, it's a component nobody has mounted yet; say why or wire it up. Same rule as SKIP_BACKEND and
// SKIP_FRONTEND in scripts/gate.sh.
// Everything below was found by this check's FIRST run, on 20 Sep 2026, against mvp1. None of it is a decision that the
// component should stay unmounted: each line says what the run found, so the backlog is written down instead of being
// hidden by a green gate. Take a line out as you wire the component up or stop exporting it. Anything NEW that the check
// flags is a regression and belongs on a screen, not on this list.
export const ALLOWED = new Map([
  ["frontend/src/local/AccountRows.tsx:AccountRow",
   "20 Sep: no file outside its own tests imports AccountRows.tsx at all. ConnectedAccounts.tsx declares its own AccountRow and renders that one."],
  ["frontend/src/local/AccountRows.tsx:AddAccountSection",
   "20 Sep: same file, same finding. Settings' Add account is ConnectedAccounts.tsx's own AddCard."],
  ["frontend/src/local/AppearanceSettings.tsx:AppearanceSettings",
   "20 Sep: a two-line wrapper round ThemeTiles and GlassWindow, and its own comment says it is kept for callers. ConnectedApp mounts those two directly."],
  ["frontend/src/local/CheckOverride.tsx:CheckOverride",
   "20 Sep: ConnectedApp takes checkChoiceLabel and useCheckOverride from this file and never the picker itself."],
  ["frontend/src/local/CodingPermission.tsx:CodingPermissionDialog",
   "20 Sep: the only import of this file is folderInside, in SettingsAccess.tsx. Nothing opens the dialog."],
  ["frontend/src/local/CodingTasks.tsx:TaskStart",
   "20 Sep: CodingTasks.tsx is widely imported (TaskRecord, TaskDisclosure, the types), but nothing imports TaskStart."],
  ["frontend/src/local/CollaborationUI.tsx:CollaborationSettings",
   "20 Sep: ConnectedApp imports HelperSource, PacketInspector, PacketRows, reviewChoiceLabel and useReviewOverride from this file, not this screen."],
  ["frontend/src/local/CollaborationUI.tsx:ReviewOverride",
   "20 Sep: same file. useReviewOverride is mounted; the picker it documents is not."],
  ["frontend/src/local/ComposerTools.tsx:AccountTool",
   "20 Sep: ConnectedApp imports ComposerTools from this file and nothing else, so the account and model button never renders."],
  ["frontend/src/local/DervoAccount.tsx:DervoAccountIdentity",
   "20 Sep: ConnectedApp takes DervoAccount, DervoAccountNotice and accountRefreshInterval from this file, never the identity block."],
  ["frontend/src/local/ProjectModel.tsx:ProjectModel",
   "20 Sep: no file outside its own tests imports ProjectModel.tsx at all. The per-project default model has no screen."],
  ["frontend/src/local/ProjectRoom.tsx:ProjectRoom",
   "20 Sep: ConnectedApp, RoomIdeas and ConversationFiling import only this file's TYPES (Idea, IdeaListing). The screen itself is mounted by nothing."],
  ["frontend/src/local/SettingsParts.tsx:CopyButton",
   "20 Sep: no file outside its own tests imports SettingsParts.tsx at all. The A1 shared settings parts are unused."],
  ["frontend/src/local/SettingsParts.tsx:SettingsGroup", "20 Sep: same file, same finding."],
  ["frontend/src/local/SettingsParts.tsx:SettingsRow", "20 Sep: same file, same finding."],
]);

export const COMPONENT_ROOT = "frontend/src/local";
// The whole frontend is scanned for importers: src/local mounts most of itself, but src/app and src/shared reach into
// it too (ConnectedApp, the shell's routes), and a component only they mount is mounted.
export const IMPORT_ROOTS = ["frontend/src"];

const isTest = (file) => /\.test\.[cm]?[jt]sx?$/.test(file) || /\.spec\.[cm]?[jt]sx?$/.test(file);
const CODE = /\.(mjs|cjs|js|jsx|ts|tsx)$/;

/** `export function Name(`, `export default function Name(`, `export async function Name(`. Capital first letter. */
const DECLARED = /^export\s+(?:default\s+)?(?:async\s+)?function\s+([A-Z][A-Za-z0-9_]*)\s*[(<]/gm;
/** `export {Name}` and `export {Name as Other}`, so a component declared without `export` but exported at the foot counts. */
const RE_EXPORTED = /^export\s*\{([^}]*)\}(?!\s*from)/gm;

/** The component names one `.tsx` file exports. */
export function componentsIn(text) {
  const names = new Set();
  for (const match of text.matchAll(DECLARED)) names.add(match[1]);
  for (const match of text.matchAll(RE_EXPORTED)) {
    for (const piece of match[1].split(",")) {
      const shown = piece.trim().split(/\s+as\s+/).pop()?.trim();
      // Only a name this file also declares as a function: `export {answerFor}` re-exporting a helper isn't a component.
      if (shown && /^[A-Z]/.test(shown) && new RegExp(`function\\s+${shown}\\s*[(<]`).test(text)) names.add(shown);
    }
  }
  return [...names];
}

/** Whether the file that declares `name` also uses it: `<AccountRow .../>` inside the list that renders it, or the name
 * handed to something as a value. A component its own file mounts is on a screen already, whatever its export says; the
 * export is untidy, not invisible, and flagging it is the kind of noise that gets a check switched off. */
/** Comments and string literals out. Without this, `import './ProjectRoom.css'` and a doc comment that names the screen
 * both read as the file using it, and an unmounted screen reads as mounted. */
export const stripText = (text) =>
  text.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ").replace(/'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`/g, "''");

export function usedInOwnFile(text, name) {
  // Its own declaration and its own export line don't count as using it; everything left does.
  const body = stripText(text).replace(new RegExp(`function\\s+${name}\\b`, "g"), "").replace(RE_EXPORTED, "");
  return new RegExp(`<${name}[\\s/>]`).test(body) || new RegExp(`(^|[^\\w.$<])${name}\\b`, "m").test(body);
}

const FROM = /(?:^|[\s;])(?:import|export)\s+([^;]*?)\s*from\s*['"]([^'"]+)['"]/gm;
const BARE = /(?:^|[\s;])import\s*['"]([^'"]+)['"]/gm;
const DYNAMIC = /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/gm;

/** Every (specifier, module) pair one file imports. `*` means "the whole module", used for namespace and dynamic imports. */
export function importsIn(text) {
  const found = [];
  for (const match of text.matchAll(FROM)) {
    const [, clause, from] = match;
    const braces = clause.match(/\{([^}]*)\}/);
    if (braces) {
      for (const piece of braces[1].split(",")) {
        const name = piece.trim().replace(/^type\s+/, "").split(/\s+as\s+/)[0]?.trim();
        if (name) found.push([name, from]);
      }
    }
    const outside = clause.replace(/\{[^}]*\}/g, "").replace(/^type\s+/, "");
    if (/\*\s*as\s+\w/.test(outside)) found.push(["*", from]);
    else if (/[A-Za-z_$][\w$]*/.test(outside.replace(/[\s,]/g, "")) && outside.trim()) found.push(["default", from]);
  }
  for (const match of text.matchAll(BARE)) found.push(["*", match[1]]);
  for (const match of text.matchAll(DYNAMIC)) found.push(["*", match[1]]);
  return found;
}

/** tsconfig.json's `paths`, the half of it that matters here: main.tsx reaches src/local as `@/local/ConnectedApp`,
 * and reading that as a package would have called the whole app unreachable. */
export const ALIAS = { "@/": "frontend/src/" };

/** Where a specifier points, as a path with no extension, relative to the repo root. Null for a package. */
export function resolveFrom(importer, specifier) {
  for (const [prefix, root] of Object.entries(ALIAS)) {
    if (specifier.startsWith(prefix)) return (root + specifier.slice(prefix.length)).replace(CODE, "").replace(/\/index$/, "");
  }
  if (!specifier.startsWith(".")) return null;
  const joined = path.posix.join(path.posix.dirname(importer), specifier);
  return joined.replace(CODE, "").replace(/\/index$/, "");
}

async function walk(directory) {
  let out = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === "dist") continue;
      out = out.concat(await walk(full));
    } else if (CODE.test(entry.name)) out.push(full);
  }
  return out;
}

const relative = (root, file) => path.relative(root, file).split(path.sep).join("/");

/** Every exported component under src/local that no non-test file outside it imports. */
export async function scanProject(projectRoot, allowed = ALLOWED) {
  const components = new Map(); // "path/without/extension" -> {file, names:[]}
  for (const file of (await walk(path.join(projectRoot, COMPONENT_ROOT))).sort()) {
    const shown = relative(projectRoot, file);
    if (!shown.endsWith(".tsx") || isTest(shown)) continue;
    const text = await fs.readFile(file, "utf8");
    const names = componentsIn(text).filter((name) => !usedInOwnFile(text, name));
    if (names.length) components.set(shown.replace(CODE, ""), { file: shown, names });
  }

  const reached = new Map(); // module key -> Set of names some other non-test file imports
  const seen = new Set();
  for (const root of IMPORT_ROOTS) {
    for (const file of await walk(path.join(projectRoot, root))) {
      const shown = relative(projectRoot, file);
      if (seen.has(shown)) continue;
      seen.add(shown);
      if (isTest(shown)) continue; // the blind spot itself: a component's own tests never make it reachable
      for (const [name, from] of importsIn(await fs.readFile(file, "utf8"))) {
        const target = resolveFrom(shown, from);
        if (!target || target === shown.replace(CODE, "")) continue; // a file importing itself proves nothing
        if (!reached.has(target)) reached.set(target, new Set());
        reached.get(target).add(name);
      }
    }
  }

  const unreachable = [];
  const still = new Set();
  for (const [key, { file, names }] of [...components].sort()) {
    const hits = reached.get(key);
    for (const name of names.sort()) {
      if (hits && (hits.has(name) || hits.has("*") || hits.has("default"))) continue;
      if (allowed.has(`${file}:${name}`)) { still.add(`${file}:${name}`); continue; }
      unreachable.push({ file, name });
    }
  }
  // An allowlist that outlives what it excused stops being read. A line with no reason is not an exception either: it is
  // a component nobody has mounted, with the record of that missing.
  const stale = [...allowed.keys()].filter((entry) => !still.has(entry));
  const unexplained = [...allowed].filter(([, reason]) => !reason || reason.trim().length < 20).map(([entry]) => entry);
  return { unreachable, stale, unexplained };
}

async function main() {
  const report = process.argv.includes("--report");
  const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
  const { unreachable, stale, unexplained } = await scanProject(projectRoot);
  const out = report ? "log" : "error";
  if (unreachable.length === 0 && stale.length === 0 && unexplained.length === 0) {
    console.log(`Unreachable check: every component under ${COMPONENT_ROOT} is imported by something that isn't a test`
      + ` (${ALLOWED.size} on the allowlist, each with a reason).`);
    return;
  }
  if (unreachable.length) {
    console[out](`Unreachable check: ${unreachable.length} component${unreachable.length === 1 ? "" : "s"} nothing mounts${report ? " (report mode)" : ""}:`);
    for (const row of unreachable) console[out](`- ${row.file}: ${row.name}`);
    console[out]("Mount it on a screen, stop exporting it, or add it to ALLOWED in frontend/scripts/check-unreachable.mjs with a reason.");
  }
  for (const entry of stale) console[out](`- ALLOWED still lists ${entry}, which is mounted now or gone. Delete that line.`);
  for (const entry of unexplained) console[out](`- ALLOWED lists ${entry} with no reason. Say why, or wire it up.`);
  if (!report) process.exit(1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
