#!/usr/bin/env node
// Interface language check (beta.17, PLAN-beta17-20260928.md section 2). Two parts:
//
// 1. English outside the catalog. It reads Dervo's own screens (frontend/src/local, not tests or fixtures) with the
//    TypeScript parser and lists the text people would see that doesn't go through t() or tr(): JSX text, and string
//    and template literals that read as words rather than code. It compares that with scripts/i18n-baseline.json, the
//    English each file still had when the catalog started. A file may lose baseline entries (a screen moved to the
//    catalog) but never gain one: new English has to go in src/i18n/en.json and be shown with t().
//    Text that isn't for people (a prompt sent to an agent, a log line) takes an `i18n-ignore` comment on its line or
//    the line above, with the reason. A comment above a statement covers all of it. A table of keys that are shown
//    through t(variable) takes `i18n-keys` instead, so scripts/i18n-wrap.mjs counts them as used.
// 2. The catalog. Every key in en.json is its own English (before any "|context"), no other language has a key en.json
//    lacks, and each translation has the same {holes} and plural forms its language needs. A key another language
//    doesn't have yet is listed but doesn't fail: the window shows the English, marked ⟦like this⟧ in development.
//    --strict fails on those too, for a release that promises the language.
//
// Usage: node scripts/check-i18n.mjs            exits 1 with what's new or wrong
//        node scripts/check-i18n.mjs --strict   also exits 1 when a language is missing a key
//        node scripts/check-i18n.mjs --update   rewrites the baseline to today's English (after moving a screen)
//        node scripts/check-i18n.mjs --report   lists every file's remaining English, largest first
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

export const SCREENS = "src/local";
export const BASELINE = "scripts/i18n-baseline.json";
export const CATALOG = "src/i18n";

// Attributes whose value is never shown: class lists, ids, roles, links, SVG geometry.
const HIDDEN_ATTRIBUTES = new Set(["className", "key", "id", "type", "role", "href", "src", "htmlFor", "rel", "target", "method",
  "autoComplete", "inputMode", "enterKeyHint", "dir", "lang", "style", "viewBox", "d", "fill", "stroke", "points", "transform",
  "xmlns", "accept", "pattern", "form", "dateTime", "testId", "data", "mode", "kind", "variant", "size", "tone", "state", "as"]);
const SHOWN_ARIA = new Set(["aria-label", "aria-description", "aria-roledescription", "aria-valuetext", "aria-placeholder"]);
// Calls whose string arguments are data, not words for people: host operations, storage keys, selectors, events, logs.
const DATA_CALLS = new Set(["local", "invoke", "getItem", "setItem", "removeItem", "querySelector", "querySelectorAll", "closest",
  "matches", "addEventListener", "removeEventListener", "getElementById", "getAttribute", "hasAttribute", "removeAttribute",
  "toggleAttribute", "matchMedia", "require", "includes", "startsWith", "endsWith",
  "CustomEvent", "Event", "KeyboardEvent", "createElement", "getPropertyValue", "setProperty", "useQuery", "queryKey", "t", "tr",
  "split", "join", "indexOf", "lastIndexOf", "padStart", "padEnd", "localeCompare", "toggle", "add", "remove", "contains",
  "track", "screenView", "trackScreen", "postMessage", "open", "emit", "listen", "dispatch", "has", "get", "set", "delete",
  "replaceAll", "go", "navigate", "useLocalStorage", "Symbol", "URL", "URLSearchParams", "RegExp", "Intl", "BigInt", "atob", "btoa"]);
// Object properties whose string value is a value the code matches on, not words: {group:'Go to', kind:'task'}.
export const DATA_PROPERTIES = new Set(["id", "code", "match", "kind", "state", "group", "tone", "value", "key", "keys", "icon", "provider", "type", "status",
  "mode", "source", "role", "rule", "context", "page", "glyph", "operation", "tool", "phase", "decision", "outcome", "method", "category"]);
// The first argument of these is data, the rest can be words: setAttribute('aria-label', 'Close').
const FIRST_ARG_DATA = new Set(["setAttribute"]);
// Words that read the same in every language Dervo has, or are names: a text made only of these needs no translation.
export const NAMES = new Set(["Dervo", "Claude", "Claude Code", "Codex", "GitHub", "OpenRouter", "ChatGPT", "OpenAI", "Anthropic",
  "macOS", "Mac", "Windows", "Finder", "Terminal", "Git", "git", "API", "URL", "ID", "OK", "esc", "Esc", "PR", "MCP", "JSON", "Python",
  "Node", "npm", "pnpm", "Opus", "Sonnet", "Haiku", "GPT", "Bryce", "Buzz", "Supabase", "Slack", "Linear", "Notion", "Figma", "Xcode",
  "VS Code", "Cursor", "Zed", "Safari", "Chrome", "AEST", "UTC", "KB", "MB", "GB", "PDF", "PNG", "HTML", "CSS", "Markdown", "README",
  "Escape", "Enter", "Tab", "Backspace", "Meta", "Shift", "Alt", "Control", "Space", "Files", "Error", "TypeError", "Return",
  "Fable", "Sol", "Luna", "Terra", "Astra", "sha256", "base64", "UTF-8", "Rust", "TypeScript", "JavaScript", "Swift", "Go", "Ruby", "Java", "Kotlin", "Anthropic Console", "Claude Desktop", "Claude Cowork"]);

const HOLE_TEXT = "{}";
// i18n-keys marks a table whose strings are catalog keys shown through t(variable): not English outside the catalog.
const IGNORE = /i18n-ignore|i18n-keys/;

/** Whether `text` reads as words for people rather than code. `jsx` is true for JSX text and shown attributes, where a
 * single lower-case word ("tokens") is still words. */
export function looksLikeCopy(raw, jsx = false) {
  const text = raw.replace(/\s+/g, " ").trim();
  if (!/[A-Za-z]{2,}/.test(text)) return false;
  const words = [...NAMES].filter((name) => name.includes(" ")).reduce((rest, name) => rest.split(name).join(" "), text)
    .replace(/\{\}/g, " ").replace(/[.,;:?!…()“”"'’‘·•→←↑↓⌘⇧⌥–\-/]+/g, " ").trim().split(/\s+/).filter(Boolean);
  if (words.length && words.every((word) => NAMES.has(word) || !/[A-Za-z]{2,}/.test(word))) return false;
  if (NAMES.has(text)) return false;
  const tokens = text.split(" ");
  // One token: an identifier, a path, a class, a key name or a value.
  if (tokens.length === 1) {
    if (/[-_/.:@#=<>[\]{}()\\|$%&*+]/.test(text.replace(/[.?!…:]$/, ""))) return false;
    if (/^[a-z][A-Za-z0-9]*[.?!…]?$/.test(text)) return jsx;
    if (/^[A-Z][a-z]+[A-Z]/.test(text) || /^[A-Z0-9_]+$/.test(text)) return false;
    return /^[A-Z][a-z’']+[.?!…:]?$/.test(text) || jsx;
  }
  // A class list or a CSS value: lower-case tokens, one with a hyphen, colon, slash or bracket inside, and no sentence.
  const codeish = tokens.every((token) => /^[!@a-z0-9_:\-[\]/.%#=()>~*+,&]+$/.test(token));
  if (codeish && tokens.some((token) => /[a-z0-9][-_:[\]/.=#][a-z0-9[\]]/.test(token) || /^[-.#[]/.test(token))) return false;
  if (/^[a-z-]+:\s/.test(text)) return false;
  // Markup, like an SVG fragment: <g fill="{}">.
  if (/^<[a-z][\w-]*[\s>/]/.test(text) && /="/.test(text)) return false;
  // A CSS value: sizes with units, or functions like minmax(0, 1fr).
  if (/(\d|\{\})(px|rem|em|fr|vh|vw|ms|deg|%)/.test(text) && /^[\w\s.,(){}%#/+-]+$/.test(text)) return false;
  // A format string or a regex source isn't words.
  if (/^[%\\^]/.test(text) || /[\\][dswb]/.test(text)) return false;
  if (/\(\?[:=!<]/.test(text) || /\w\|\w/.test(text)) return false;
  return true;
}

const lineOf = (source, node) => source.getLineAndCharacterOfPosition(node.getStart(source)).line;

/** An i18n-ignore comment on a text's line or the line above, or above any statement it's in, which covers the whole
 * statement (a table of search words, a function that writes a report in English). */
function ignored(source, lines, node) {
  const marked = (at) => IGNORE.test(lines[at] ?? "") || IGNORE.test(lines[at - 1] ?? "");
  if (marked(lineOf(source, node))) return true;
  for (let up = node.parent; up && !ts.isSourceFile(up); up = up.parent)
    if (ts.isStatement(up) && marked(lineOf(source, up))) return true;
  return false;
}

function calleeName(expression) {
  if (ts.isIdentifier(expression)) return expression.text;
  if (ts.isPropertyAccessExpression(expression)) return expression.name.text;
  return "";
}

function templateText(node) {
  return node.head.text + node.templateSpans.map((span) => HOLE_TEXT + span.literal.text).join("");
}

/** The English text in one file that people would see outside t()/tr(): [{line, text}], in order. */
export function findCopy(text, fileName = "file.tsx") {
  return copyNodes(text, fileName).hits.map(({ line, text: found }) => ({ line, text: found }));
}

/** findCopy with the parsed file and each hit's node, for scripts/i18n-wrap.mjs. */
export function copyNodes(text, fileName = "file.tsx") {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, kind);
  const lines = text.split(/\r?\n/);
  const found = [];
  const report = (node, value, jsx) => {
    const clean = value.replace(/\s+/g, " ").trim();
    if (!looksLikeCopy(clean, jsx) || ignored(source, lines, node)) return;
    found.push({ line: lineOf(source, node) + 1, text: clean, node, jsx });
  };
  // `shown` is true where a literal is a shown JSX attribute's value, so one lower-case word counts.
  const visit = (node, shown = false) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node) || ts.isTypeNode(node) || ts.isTypeAliasDeclaration(node)
      || ts.isInterfaceDeclaration(node) || ts.isModuleDeclaration(node) || ts.isEnumDeclaration(node)) return;
    if (ts.isJsxText(node)) { report(node, node.text, true); return; }
    if (ts.isJsxAttribute(node)) {
      const name = node.name.getText(source);
      if (HIDDEN_ATTRIBUTES.has(name) || ((name.startsWith("aria-") || name.startsWith("data-")) && !SHOWN_ARIA.has(name))) return;
      if (node.initializer) visit(node.initializer, SHOWN_ARIA.has(name) || ["title", "placeholder", "alt", "label"].includes(name));
      return;
    }
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const name = calleeName(node.expression);
      // ['Home', 'End'].includes(event.key): the list a data call reads is data too.
      const receiver = ts.isPropertyAccessExpression(node.expression) ? node.expression.expression : null;
      if (receiver && DATA_CALLS.has(name) && (ts.isArrayLiteralExpression(receiver) || ts.isStringLiteralLike(receiver))) {}
      else visit(node.expression);
      const args = node.arguments ?? [];
      if (DATA_CALLS.has(name) || (ts.isPropertyAccessExpression(node.expression) && ts.isIdentifier(node.expression.expression) && node.expression.expression.text === "console")) {
        // A host operation's or a storage call's strings are data; any function passed in can still show words.
        for (const arg of args) if (ts.isArrowFunction(arg) || ts.isFunctionExpression(arg)) visit(arg);
        return;
      }
      args.forEach((arg, index) => { if (!(FIRST_ARG_DATA.has(name) && index === 0)) visit(arg); });
      return;
    }
    if (ts.isBinaryExpression(node) && [ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken,
      ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken, ts.SyntaxKind.InKeyword].includes(node.operatorToken.kind)) {
      for (const side of [node.left, node.right]) if (!ts.isStringLiteralLike(side)) visit(side);
      return;
    }
    if (ts.isCaseClause(node)) { node.statements.forEach((statement) => visit(statement)); return; }
    if (ts.isPropertyAssignment(node)) {
      if (ts.isComputedPropertyName(node.name)) visit(node.name);
      if (ts.isIdentifier(node.name) && DATA_PROPERTIES.has(node.name.text) && ts.isStringLiteralLike(node.initializer)) return;
      visit(node.initializer, shown);
      return;
    }
    if (ts.isElementAccessExpression(node)) { visit(node.expression); if (!ts.isStringLiteralLike(node.argumentExpression)) visit(node.argumentExpression); return; }
    if (ts.isTaggedTemplateExpression(node)) return;
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) { report(node, node.text, shown); return; }
    if (ts.isTemplateExpression(node)) {
      report(node, templateText(node), shown);
      node.templateSpans.forEach((span) => visit(span.expression));
      return;
    }
    if (ts.isJsxExpression(node)) { if (node.expression) visit(node.expression, shown); return; }
    if (ts.isConditionalExpression(node)) { visit(node.condition); visit(node.whenTrue, shown); visit(node.whenFalse, shown); return; }
    if (ts.isParenthesizedExpression(node)) { visit(node.expression, shown); return; }
    if (ts.isBinaryExpression(node) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.PlusToken].includes(node.operatorToken.kind)) {
      visit(node.left, shown); visit(node.right, shown);
      return;
    }
    ts.forEachChild(node, (child) => visit(child));
  };
  visit(source);
  return { source, hits: found };
}

const keep = (name) => /\.tsx?$/.test(name) && !/\.test\./.test(name) && !/\.d\.ts$/.test(name) && !/Fixture\.tsx?$/.test(name);

async function walk(directory) {
  let out = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) out = out.concat(await walk(full));
    else if (keep(entry.name)) out.push(full);
  }
  return out;
}

/** {file: [text, ...]} for every screen file with English outside the catalog, each text once, sorted. */
export async function scanScreens(frontendRoot) {
  const result = {};
  for (const file of (await walk(path.join(frontendRoot, SCREENS))).sort()) {
    const relative = path.relative(frontendRoot, file).split(path.sep).join("/");
    const texts = [...new Set(findCopy(await fs.readFile(file, "utf8"), file).map((hit) => hit.text))].sort();
    if (texts.length) result[relative] = texts;
  }
  return result;
}

/** What's new against the baseline: [{file, text}]. Entries the baseline has and the file lost are fine. */
export function newCopy(current, baseline) {
  const added = [];
  for (const [file, texts] of Object.entries(current)) {
    const known = new Set(baseline[file] ?? []);
    for (const text of texts) if (!known.has(text)) added.push({ file, text });
  }
  return added;
}

// A plural form may leave out {count} ("A run is still stopping"); every other hole has to match.
const holes = (text) => [...new Set([...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).filter((name) => name !== "count"))].sort().join(",");
const forms = (value) => (typeof value === "string" ? { other: value } : value);

/** What's wrong with the catalogs, and the keys each other language doesn't have yet:
 * {en: {...}, "pt-BR": {...}} -> {problems: ["pt-BR: “Copy path” has {name} where English has {}"], missing: {"pt-BR": ["Copy path"]}}. */
export function catalogProblems(catalogs) {
  const problems = [];
  const missing = {};
  const english = catalogs.en ?? {};
  for (const [key, value] of Object.entries(english)) {
    const shown = key.split("|")[0];
    const each = forms(value);
    if (typeof each !== "object" || typeof each.other !== "string") { problems.push(`en: “${key}” has no text`); continue; }
    if (each.other !== shown) problems.push(`en: “${key}” should be its own English, but reads “${each.other}”`);
    if (typeof value === "object" && !Object.values(value).every((form) => holes(form) === holes(each.other)))
      problems.push(`en: “${key}” has forms with different {holes}`);
  }
  for (const [language, catalog] of Object.entries(catalogs)) {
    if (language === "en") continue;
    // The forms counts of things actually take (0 to 1,000), not every category the language has: Portuguese lists
    // "many", but only for numbers written like 1,2 mi.
    const rules = new Intl.PluralRules(language);
    const needed = [...new Set(Array.from({ length: 1001 }, (_, n) => rules.select(n)))];
    missing[language] = [];
    for (const key of Object.keys(english)) {
      if (!(key in catalog)) { missing[language].push(key); continue; }
      const theirs = forms(catalog[key]);
      const ours = forms(english[key]);
      if (typeof english[key] === "object") {
        if (typeof catalog[key] !== "object") { problems.push(`${language}: “${key}” needs plural forms`); continue; }
        for (const category of needed) if (typeof theirs[category] !== "string") problems.push(`${language}: “${key}” has no “${category}” form`);
      } else if (typeof catalog[key] !== "string") {
        problems.push(`${language}: “${key}” should be one text`); continue;
      }
      for (const form of Object.values(theirs)) if (holes(form) !== holes(ours.other))
        problems.push(`${language}: “${key}” has {${holes(form)}} where English has {${holes(ours.other)}}`);
      if (!Object.values(theirs).every((form) => typeof form === "string" && form.trim())) problems.push(`${language}: “${key}” is empty`);
    }
    for (const key of Object.keys(catalog)) if (!(key in english)) problems.push(`${language}: “${key}” isn’t in en.json`);
  }
  return { problems, missing };
}

export async function readCatalogs(frontendRoot) {
  const directory = path.join(frontendRoot, CATALOG);
  const catalogs = {};
  for (const name of (await fs.readdir(directory)).filter((file) => file.endsWith(".json")).sort())
    catalogs[name.replace(/\.json$/, "")] = JSON.parse(await fs.readFile(path.join(directory, name), "utf8"));
  return catalogs;
}

async function main() {
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const current = await scanScreens(frontendRoot);
  const baselinePath = path.join(frontendRoot, BASELINE);
  if (process.argv.includes("--update")) {
    await fs.writeFile(baselinePath, `${JSON.stringify(current, null, 1)}\n`);
    const count = Object.values(current).reduce((sum, texts) => sum + texts.length, 0);
    console.log(`Interface language: baseline rewritten, ${count} English texts in ${Object.keys(current).length} files.`);
    return;
  }
  if (process.argv.includes("--report")) {
    for (const [file, texts] of Object.entries(current).sort((a, b) => b[1].length - a[1].length)) console.log(`${String(texts.length).padStart(4)} ${file}`);
    return;
  }
  const baseline = JSON.parse(await fs.readFile(baselinePath, "utf8"));
  const added = newCopy(current, baseline);
  const strict = process.argv.includes("--strict");
  const catalogs = await readCatalogs(frontendRoot);
  const { problems, missing } = catalogProblems(catalogs);
  const left = Object.values(current).reduce((sum, texts) => sum + texts.length, 0);
  const before = Object.values(baseline).reduce((sum, texts) => sum + texts.length, 0);
  const gaps = Object.entries(missing).filter(([, keys]) => keys.length);
  for (const [language, keys] of gaps)
    (strict ? console.error : console.log)(`${language} is missing ${keys.length} of ${Object.keys(catalogs.en).length} texts, shown in English: ${keys.slice(0, 5).map((key) => `“${key}”`).join(", ")}${keys.length > 5 ? ", …" : ""}`);
  if (!added.length && !problems.length && !(strict && gaps.length)) {
    console.log(`Interface language: no new English outside the catalog (${left} texts left to move, baseline ${before}); catalogs agree.`);
    if (left < before) console.log("Some baseline English has moved. Run with --update to shrink the baseline.");
    return;
  }
  for (const hit of added) console.error(`- ${hit.file}: English outside the catalog: “${hit.text}”`);
  for (const problem of problems) console.error(`- ${problem}`);
  console.error("Put new words in src/i18n/en.json (and each other language) and show them with t() or tr(). Text that isn’t for people takes an i18n-ignore comment with the reason.");
  process.exit(1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
