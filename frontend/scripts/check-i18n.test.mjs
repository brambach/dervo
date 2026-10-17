import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promises as fs } from "node:fs";
import { BASELINE, catalogProblems, findCopy, looksLikeCopy, newCopy, readCatalogs, scanScreens } from "./check-i18n.mjs";
import { calledKeys, unusedKeys, wrap } from "./i18n-wrap.mjs";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const texts = (source) => findCopy(source, "screen.tsx").map((hit) => hit.text);

test("words for people count as copy; class lists, names and identifiers don't", () => {
  assert.equal(looksLikeCopy("Show in Finder"), true);
  assert.equal(looksLikeCopy("Copied"), true);
  assert.equal(looksLikeCopy("dv-btn dv-btn-quiet"), false);
  assert.equal(looksLikeCopy("Claude Code"), false, "a name reads the same in every language");
  assert.equal(looksLikeCopy("project_rename"), false);
  assert.equal(looksLikeCopy("tokens"), false, "one lower-case word in code is a value");
  assert.equal(looksLikeCopy("tokens", true), true, "but in JSX text it's shown");
});

test("the finder sees JSX text and shown attributes, not hidden ones, host calls, comparisons or t()", () => {
  const found = texts(`
    import {t} from '@/i18n';
    export function Row({state}) {
      if (state === 'Done for now') return null;
      void local('project_rename', {name: 'Some name here'});
      console.log('Rename failed badly');
      return <div className="dv-set dv-row" aria-label="Project settings" data-kind="Not shown">
        <button title="Copy the path">{t('Copy path')}</button>
        Show in Finder
      </div>;
    }`);
  assert.deepEqual(found, ["Project settings", "Copy the path", "Show in Finder"]);
});

test("an i18n-ignore comment covers its line, the next line, or the whole statement below it", () => {
  const found = texts(`
    const prompt = 'Summarise this thread for the coordinator'; // i18n-ignore: sent to an agent
    // i18n-ignore: the agent's instructions
    const other = 'Answer in one short paragraph';
    /* i18n-ignore: search words */
    const TERMS = {model: 'account model thinking', access: 'files folders commands'};
    const shown = 'Still shown to people';`);
  assert.deepEqual(found, ["Still shown to people"]);
});

test("only English that's new against the baseline fails; English a screen lost is fine", () => {
  const baseline = { "src/local/A.tsx": ["Copy path", "Show in Finder"] };
  assert.deepEqual(newCopy({ "src/local/A.tsx": ["Copy path"] }, baseline), []);
  assert.deepEqual(newCopy({ "src/local/A.tsx": ["Copy path", "Open it"], "src/local/B.tsx": ["Hello there"] }, baseline),
    [{ file: "src/local/A.tsx", text: "Open it" }, { file: "src/local/B.tsx", text: "Hello there" }]);
});

test("the catalogs: English keys are their own text, a missing translation is listed apart, a wrong one fails", () => {
  const en = { "Copy path": "Copy path", "Delete {name}": "Delete {name}", "{count} runs": { one: "{count} run", other: "{count} runs" }, "Open|state": "Open" };
  assert.deepEqual(catalogProblems({ en }), { problems: [], missing: {} });
  const { problems, missing } = catalogProblems({
    en: { ...en, "Typo here": "Typo hear" },
    "pt-BR": { "Delete {name}": "Excluir {nome}", "{count} runs": { other: "{count} execuções" }, "Open|state": "Aberto", "Extra": "Extra" },
  });
  assert.deepEqual(missing, { "pt-BR": ["Copy path", "Typo here"] });
  assert.ok(problems.includes("en: “Typo here” should be its own English, but reads “Typo hear”"));
  assert.ok(problems.includes("pt-BR: “Delete {name}” has {nome} where English has {name}"));
  assert.ok(problems.includes("pt-BR: “{count} runs” has no “one” form"), "Portuguese needs one and other");
  assert.ok(problems.includes("pt-BR: “Extra” isn’t in en.json"));
  assert.ok(!problems.some((problem) => problem.includes("many")), "“many” is only for numbers like 1,2 mi");
});

test("the wrap helper moves plain labels and simple templates, and leaves sentences split around markup", () => {
  const before = `import {x} from './x';
export const Row = ({name}) => <div aria-label="Search settings">
  <button>{ICON}Copy path</button>
  <p>Archive <b>{name}</b> now?</p>
  <li>Download the <b>macOS installer</b> and run it.</li>
  <span title={\`Back to \${name}\`}/>
</div>;`;
  const { text, keys, left } = wrap(before, "Row.tsx");
  assert.match(text, /import \{t\} from '@\/i18n';/);
  assert.match(text, /aria-label=\{t\('Search settings'\)\}/);
  assert.match(text, /\{ICON\}\{t\('Copy path'\)\}/);
  assert.match(text, /title=\{t\('Back to \{name\}',\{name\}\)\}/);
  assert.deepEqual(keys.sort(), ["Back to {name}", "Copy path", "Search settings"]);
  assert.deepEqual(left.map((hit) => hit.text), ["Archive", "now?", "Download the", "macOS installer", "and run it."], "a sentence around <b> is hand work, the bold words too");
  assert.deepEqual(calledKeys(text, "Row.tsx").sort(), ["Back to {name}", "Copy path", "Search settings"]);
});

test("a table under i18n-keys counts as keys in use, not English outside the catalog, and a dropped t() frees its key", () => {
  const source = `// i18n-keys: shown through t(TERMS[page])
const TERMS = {model: 'account model thinking', 'gpt-6': 'Complex coding work'};
const RETRY = 'Please try that again'; // i18n-ignore: sent to the agent
export const label = () => t('Copy path');`;
  assert.deepEqual(texts(source), []);
  assert.deepEqual(calledKeys(source, "terms.ts").sort(), ["Complex coding work", "Copy path", "account model thinking"]);
  assert.deepEqual(unusedKeys({ "Copy path": "", "Please try that again": "", "account model thinking": "" }, [{ name: "terms.ts", text: source }]),
    ["Please try that again"]);
});

test("Dervo's screens have no English outside the catalog that the baseline doesn't list, and the catalogs agree", async () => {
  const baseline = JSON.parse(await fs.readFile(path.join(frontendRoot, BASELINE), "utf8"));
  const added = newCopy(await scanScreens(frontendRoot), baseline);
  assert.deepEqual(added, [], "put new words in src/i18n/en.json and show them with t(); see scripts/check-i18n.mjs");
  assert.deepEqual(catalogProblems(await readCatalogs(frontendRoot)).problems, []);
});
