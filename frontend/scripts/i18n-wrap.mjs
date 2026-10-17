#!/usr/bin/env node
// Moves the plain cases of a screen's English onto the catalog (beta.17, interface language), so the hand work is only
// the sentences that need it. For each file given, using the same finder as scripts/check-i18n.mjs:
//   - a shown attribute's string, aria-label="Search settings", becomes aria-label={t('Search settings')},
//   - a string or template in code, 'Copied' or `Back to ${projectName}`, becomes t('Copied') or
//     t('Back to {projectName}',{projectName}) when every hole is a name or a property,
//   - JSX text that's the only text in its element, beside icons or other elements, becomes {t('Copy path')}.
// It adds `import {t} from '@/i18n'` and each new key to src/i18n/en.json. It leaves the rest for a person: text split
// around a value or an inline <b> or <code> (a sentence, which needs tr() and one key), holes that are expressions,
// plurals, entities. Run the check after it to see what's left, and read the diff: a string that's sent to an agent or
// compared somewhere isn't for people and goes back with an i18n-ignore comment.
//
// Usage: node scripts/i18n-wrap.mjs src/local/SettingsGeneral.tsx [more files]
//        node scripts/i18n-wrap.mjs --keys src/local/SettingsGeneral.tsx [more files]
//          adds the keys of t('…') and tr('…') calls written by hand to en.json, without wrapping anything. A plural
//          still needs its forms written in en.json by hand.
//        node scripts/i18n-wrap.mjs --prune
//          drops keys no source file under src/ uses any more (calledKeys) from every catalog.
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { DATA_PROPERTIES, copyNodes } from "./check-i18n.mjs";

const INLINE = new Set(["b", "strong", "em", "i", "code", "a", "kbd", "u", "s", "mark", "small", "abbr", "time"]);
const quote = (text) => `'${text.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
const collapse = (text) => text.replace(/\s+/g, " ").trim();

function holeName(expression) {
  if (ts.isNonNullExpression(expression) || ts.isParenthesizedExpression(expression)) return holeName(expression.expression);
  if (ts.isIdentifier(expression)) return expression.text;
  if (ts.isPropertyAccessExpression(expression)) return expression.name.text;
  return null;
}

/** An expression beside JSX text that can't be words in its sentence: an element, an icon constant, `x&&<b/>`. */
function decorative(expression) {
  if (!expression) return true;
  if (ts.isParenthesizedExpression(expression)) return decorative(expression.expression);
  if (ts.isJsxElement(expression) || ts.isJsxSelfClosingElement(expression) || ts.isJsxFragment(expression)) return true;
  if (ts.isIdentifier(expression)) return /^[A-Z][A-Z0-9_]*$/.test(expression.text);
  if (ts.isPropertyAccessExpression(expression)) {
    let root = expression;
    while (ts.isPropertyAccessExpression(root)) root = root.expression;
    return ts.isIdentifier(root) && /^[A-Z][A-Z0-9_]*$/.test(root.text);
  }
  if (ts.isStringLiteral(expression)) return !/\S/.test(expression.text);
  if (ts.isBinaryExpression(expression) && expression.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) return decorative(expression.right);
  if (ts.isConditionalExpression(expression)) return decorative(expression.whenTrue) && decorative(expression.whenFalse);
  if (expression.kind === ts.SyntaxKind.NullKeyword) return true;
  if (ts.isCallExpression(expression) && ts.isPropertyAccessExpression(expression.expression) && expression.expression.name.text === "map") return true;
  return false;
}

const tagOf = (element) => (ts.isJsxElement(element) ? element.openingElement : element).tagName.getText();

function standalone(node) {
  const parent = node.parent;
  if (!parent || !("children" in parent)) return false;
  if (/&[#\w]+;/.test(node.text)) return false;
  // The words in <b>macOS installer</b> inside "Download the … and follow its steps" are part of that sentence.
  if (ts.isJsxElement(parent) && INLINE.has(tagOf(parent)) && parent.parent && "children" in parent.parent
    && parent.parent.children.some((child) => child !== parent && (ts.isJsxText(child) ? /\S/.test(child.text) : ts.isJsxExpression(child) && !decorative(child.expression)))) return false;
  for (const child of parent.children) {
    if (child === node) continue;
    if (ts.isJsxText(child)) { if (/\S/.test(child.text)) return false; continue; }
    if (ts.isJsxExpression(child)) { if (!decorative(child.expression)) return false; continue; }
    if ((ts.isJsxElement(child) || ts.isJsxSelfClosingElement(child)) && INLINE.has(tagOf(child))) return false;
  }
  return true;
}

export function wrap(text, fileName) {
  const { source, hits } = copyNodes(text, fileName);
  const edits = [];
  const keys = new Set();
  const left = [];
  // A string inside a template that's left for hand work belongs to that sentence: wrapping it alone makes a fragment.
  const within = (node, outer) => node !== outer && node.getStart(source) >= outer.getStart(source) && node.end <= outer.end;
  const complex = hits.filter((hit) => ts.isTemplateExpression(hit.node) && !hit.node.templateSpans.every((span) => holeName(span.expression)));
  for (const hit of hits) {
    const node = hit.node;
    if (complex.some((outer) => within(node, outer.node))) { left.push(hit); continue; }
    if (ts.isJsxText(node)) {
      if (!standalone(node)) { left.push(hit); continue; }
      const raw = node.getFullText(source);
      const lead = raw.length - raw.trimStart().length, trail = raw.length - raw.trimEnd().length;
      const key = collapse(raw);
      if (/[{}]/.test(key)) { left.push(hit); continue; }
      edits.push([node.getFullStart() + lead, node.end - trail, `{t(${quote(key)})}`]);
      keys.add(key);
    } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      const key = node.text;
      if (key !== collapse(key) || /[{}]/.test(key)) { left.push(hit); continue; }
      const inAttribute = ts.isJsxAttribute(node.parent);
      edits.push([node.getStart(source), node.end, inAttribute ? `{t(${quote(key)})}` : `t(${quote(key)})`]);
      keys.add(key);
    } else if (ts.isTemplateExpression(node)) {
      const names = new Map();
      let key = node.head.text, ok = !/[{}]/.test(node.head.text);
      for (const span of node.templateSpans) {
        const name = holeName(span.expression);
        if (!name || /[{}]/.test(span.literal.text)) { ok = false; break; }
        const expression = span.expression.getText(source);
        let unique = name, n = 2;
        while (names.has(unique) && names.get(unique) !== expression) unique = `${name}${n++}`;
        names.set(unique, expression);
        key += `{${unique}}${span.literal.text}`;
      }
      if (!ok || key !== collapse(key)) { left.push(hit); continue; }
      const vars = [...names].map(([name, expression]) => (name === expression ? name : `${name}:${expression}`)).join(",");
      edits.push([node.getStart(source), node.end, `t(${quote(key)},{${vars}})`]);
      keys.add(key);
    } else left.push(hit);
  }
  edits.sort((a, b) => b[0] - a[0]);
  let out = text;
  let end = Infinity;
  for (const [start, stop, replacement] of edits) {
    if (stop > end) continue; // nested in an edit already made
    out = out.slice(0, start) + replacement + out.slice(stop);
    end = start;
  }
  if (edits.length && !/import\s*\{[^}]*\bt\b[^}]*\}\s*from\s*'@\/i18n'/.test(out)) {
    const imports = [...out.matchAll(/^import[^;]*;[ \t]*$/gm)];
    const at = imports.length ? imports[imports.length - 1].index + imports[imports.length - 1][0].length : 0;
    out = at ? `${out.slice(0, at)}\nimport {t} from '@/i18n';${out.slice(at)}` : `import {t} from '@/i18n';\n\n${out}`;
  }
  const shadows = [...out.matchAll(/[(,]\s*(t|tr)\s*(?:[,)=:]|=>)|\b(?:const|let|var)\s+(t|tr)\b/g)].length;
  return { text: out, keys: [...keys], left, shadows };
}

/** The keys a file uses: every t('…') and tr('…') call's, and every string in a statement under an i18n-keys comment
 * (a table shown through t(variable)). */
export function calledKeys(text, fileName) {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, kind);
  const lines = text.split(/\r?\n/);
  const keys = [];
  const marked = (node) => /i18n-keys/.test(lines[source.getLineAndCharacterOfPosition(node.getStart(source)).line - 1] ?? "");
  const visit = (node, table = false) => {
    if (table && ts.isStringLiteralLike(node)) keys.push(node.text);
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && ["t", "tr"].includes(node.expression.text)
      && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0])) keys.push(node.arguments[0].text);
    const inTable = table || (ts.isStatement(node) && marked(node));
    // In a table, a property like {code:'unknown_project'} or {id:'x'} holds a value, not a key.
    const dataProperty = ts.isPropertyAssignment(node) && ts.isIdentifier(node.name) && DATA_PROPERTIES.has(node.name.text);
    ts.forEachChild(node, (child) => visit(child, inTable && !dataProperty && (!ts.isPropertyAssignment(node) || child === node.initializer)));
  };
  visit(source);
  return keys;
}

/** The keys no source file uses, by calledKeys. sources: [{name, text}]. */
export function unusedKeys(catalog, sources) {
  const used = new Set(sources.flatMap(({ name, text }) => calledKeys(text, name)));
  return Object.keys(catalog).filter((key) => !used.has(key));
}

async function sourceTexts(directory) {
  const out = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) out.push(...(await sourceTexts(full)));
    else if (/\.tsx?$/.test(entry.name) && !/\.test\./.test(entry.name)) out.push({ name: full, text: await fs.readFile(full, "utf8") });
  }
  return out;
}

export function addKeys(catalog, keys) {
  const next = { ...catalog };
  for (const key of keys) if (!(key in next)) next[key] = key.split("|")[0];
  return Object.fromEntries(Object.entries(next).sort(([a], [b]) => a.localeCompare(b, "en", { sensitivity: "base" }) || (a < b ? -1 : 1)));
}

async function main() {
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const catalogPath = path.join(frontendRoot, "src/i18n/en.json");
  let catalog = JSON.parse(await fs.readFile(catalogPath, "utf8"));
  if (process.argv.includes("--prune")) {
    const unused = unusedKeys(catalog, await sourceTexts(path.join(frontendRoot, "src")));
    for (const name of (await fs.readdir(path.join(frontendRoot, "src/i18n"))).filter((file) => file.endsWith(".json"))) {
      const file = path.join(frontendRoot, "src/i18n", name);
      const each = JSON.parse(await fs.readFile(file, "utf8"));
      for (const key of unused) delete each[key];
      await fs.writeFile(file, `${JSON.stringify(each, null, 1)}\n`);
    }
    console.log(`${unused.length} unused keys dropped${unused.length ? `: ${unused.map((key) => `“${key}”`).join(", ")}` : ""}`);
    return;
  }
  const onlyKeys = process.argv.includes("--keys");
  for (const file of process.argv.slice(2).filter((arg) => arg !== "--keys")) {
    const full = path.resolve(file);
    if (onlyKeys) {
      const keys = calledKeys(await fs.readFile(full, "utf8"), full).filter((key) => !(key in catalog));
      catalog = addKeys(catalog, keys);
      console.log(`${path.relative(frontendRoot, full)}: ${keys.length} keys added`);
      continue;
    }
    const result = wrap(await fs.readFile(full, "utf8"), full);
    await fs.writeFile(full, result.text);
    catalog = addKeys(catalog, calledKeys(result.text, full));
    console.log(`${path.relative(frontendRoot, full)}: ${result.keys.length} keys moved, ${result.left.length} left for hand work${result.shadows ? `, check ${result.shadows} local t/tr names` : ""}`);
    for (const hit of result.left) console.log(`  ${hit.line}: ${hit.text.slice(0, 100)}`);
  }
  await fs.writeFile(catalogPath, `${JSON.stringify(catalog, null, 1)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
