#!/usr/bin/env node
// Copy check for the build: one voice on every screen (BRIEF-wedge.md, scenario 11).
//
// It scans the strings people see: the UI copy in frontend/src/local (JSX text and string literals in .ts and .tsx)
// and the sentences the backend returns to the screen (string literals in backend/*.py). It flags:
//   - an em dash anywhere in a scanned file, code comments included (house style: never one),
//   - a short, certain banned list inside strings: filler ("Great question", "Let me know", "Feel free",
//     "Rest assured") and inflated words ("seamless", "robust", "comprehensive", "streamlined", "holistic",
//     "granular", "bespoke", "leverage"),
//   - an exclamation mark that ends a word inside a string ("Done!"). `!==`, `!busy` and `!important` never match.
// Tests and fakes are left out: they hold the bad copy on purpose. The list is short so it never flags legitimate code.
//
// Usage: node scripts/check-copy.mjs            exits 1 with the violations listed
//        node scripts/check-copy.mjs --report   lists them and exits 0 (for a report, or a build that isn't ready yet)
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const BANNED = [
  "great question",
  "let me know",
  "feel free",
  "rest assured",
  "you're absolutely right",
  "seamless",
  "robust",
  "comprehensive",
  "streamlined",
  "holistic",
  "granular",
  "bespoke",
  "leverage",
];

const EM_DASH = String.fromCharCode(0x2014); // the character itself never appears in this file
// Single-line string literals and template literals. A template with `${}` inside is still copy around the holes.
const STRING_RE = /'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\\n]|\\.)*`/g;
// JSX text: what sits between a closing `>` and the next `<`, with no braces (an expression) inside.
const JSX_TEXT_RE = />([^<>{}\n]*[A-Za-z][^<>{}\n]*)</g;
// A word ending in "!" inside copy: a letter or digit, then "!", then a space, a quote, punctuation or the end.
const EXCLAMATION_RE = /[A-Za-z0-9]!(?=[\s'"`.,)\]}]|$)/;

// A `${...}` hole in a template literal is code (`${data!.name}` is a non-null assertion), not copy.
const TEMPLATE_HOLE_RE = /\$\{[^}]*\}/g;
// Files allowed to break one rule, as `path:rule`. voice.py spells out the words it bans so the prompts can name them;
// conversation_activity.py spells out stock sign-offs ("let me know") so it can recognise them, never to show them.
export const OVERRIDES = new Set(["backend/voice.py:banned", "backend/conversation_activity.py:banned"]);

/** The pieces of one line that are copy, by file kind. */
export function copyIn(line, kind) {
  const pieces = [];
  for (const match of line.matchAll(STRING_RE)) pieces.push(match[0].slice(1, -1).replace(TEMPLATE_HOLE_RE, " "));
  if (kind === "tsx") for (const match of line.matchAll(JSX_TEXT_RE)) pieces.push(match[1]);
  return pieces;
}

/**
 * Scan one file's text. `kind` is "ts", "tsx" or "py". Returns [{line, rule, match}].
 */
export function scanText(text, kind) {
  const found = [];
  text.split(/\r?\n/).forEach((line, index) => {
    const number = index + 1;
    if (line.includes(EM_DASH)) found.push({ line: number, rule: "em dash", match: line.trim().slice(0, 80) });
    for (const piece of copyIn(line, kind)) {
      const lower = piece.toLowerCase();
      for (const word of BANNED) {
        if (lower.includes(word)) found.push({ line: number, rule: "banned", match: word });
      }
      if (EXCLAMATION_RE.test(piece)) found.push({ line: number, rule: "exclamation", match: piece.slice(0, 80) });
    }
  });
  return found;
}

async function walk(directory, keep) {
  let out = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      out = out.concat(await walk(full, keep));
    } else if (keep(entry.name)) out.push(full);
  }
  return out;
}

export const ROOTS = [
  { root: "frontend/src/local", keep: (name) => /\.tsx?$/.test(name) && !/\.test\./.test(name), kind: (name) => (name.endsWith(".tsx") ? "tsx" : "ts") },
  { root: "backend", keep: (name) => /\.py$/.test(name) && !/^(test_|fake_)/.test(name), kind: () => "py", shallow: true },
];

export async function scanProject(projectRoot) {
  const violations = [];
  for (const rule of ROOTS) {
    const directory = path.join(projectRoot, rule.root);
    let files;
    try {
      files = rule.shallow
        ? (await fs.readdir(directory, { withFileTypes: true })).filter((e) => e.isFile() && rule.keep(e.name)).map((e) => path.join(directory, e.name))
        : await walk(directory, rule.keep);
    } catch {
      continue;
    }
    for (const file of files.sort()) {
      const relative = path.relative(projectRoot, file).split(path.sep).join("/");
      for (const hit of scanText(await fs.readFile(file, "utf8"), rule.kind(path.basename(file)))) {
        if (!OVERRIDES.has(`${relative}:${hit.rule}`)) violations.push({ file: relative, ...hit });
      }
    }
  }
  return violations;
}

async function main() {
  const report = process.argv.includes("--report");
  const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
  const violations = await scanProject(projectRoot);
  if (violations.length === 0) {
    console.log("Copy check: no em dashes, banned words or exclamation marks in the UI strings.");
    return;
  }
  console[report ? "log" : "error"](`Copy check: ${violations.length} violation${violations.length === 1 ? "" : "s"}${report ? " (report mode)" : ""}:`);
  for (const v of violations) console[report ? "log" : "error"](`- ${v.file}:${v.line}: ${v.rule}: ${v.match}`);
  if (!report) {
    console.error("Use a spaced hyphen or a full stop for an em dash, plain words for the banned list, and a full stop for an exclamation mark.");
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
