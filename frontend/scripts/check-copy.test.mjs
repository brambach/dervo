import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { BANNED, copyIn, scanProject, scanText } from "./check-copy.mjs";

const DASH = String.fromCharCode(0x2014);

const DIRTY_TSX = [
  `// A status line ${DASH} with an em dash in a comment`,
  "const title = 'Great question, here is the plan';",
  "export const line = `Done! ${count} files changed`;",
  "<p>Everything is seamless and robust.</p>",
  'const ok = busy ? "" : "Rest assured, nothing ran.";',
].join("\n");

const CLEAN_TSX = [
  "const busy = !ready && value !== other; // not copy, not an exclamation in copy",
  "const css = 'display: none !important';",
  "const robustParser = parse(input); // an identifier, not a string",
  "<p>Ready for review. No checks ran in Dervo.</p>",
  "const note = `${!busy ? 'Idle' : 'Working'} since ${when}`;",
  "const label = `${agentLabel(review!.reviewer!)} reviews ${count(data!)} files`; // non-null assertions are code",
  "const range = 'resets 9–10 PM'; // an en dash range is fine",
].join("\n");

test("the dirty file is caught on every rule, once per hit", () => {
  const hits = scanText(DIRTY_TSX, "tsx");
  assert.deepEqual(
    hits.map((h) => [h.line, h.rule, h.match]),
    [
      [1, "em dash", `// A status line ${DASH} with an em dash in a comment`],
      [2, "banned", "great question"],
      [3, "exclamation", "Done!   files changed"], // the template hole is blanked: it is code, not copy
      [4, "banned", "seamless"],
      [4, "banned", "robust"],
      [5, "banned", "rest assured"],
    ],
  );
});

test("negative control: legitimate code and clean copy pass", () => {
  assert.deepEqual(scanText(CLEAN_TSX, "tsx"), []);
  assert.deepEqual(scanText("detail = 'Ready for review. The check passed.'\nx = a if not b else c\n", "py"), []);
});

test("python strings are copy, python comments with em dashes are still wrong", () => {
  const hits = scanText(`# a comment ${DASH} dashed\nREPLY = 'Let me know if that helps'\nDONE = "Done!"\n`, "py");
  assert.deepEqual(hits.map((h) => [h.line, h.rule]), [[1, "em dash"], [2, "banned"], [3, "exclamation"]]);
});

test("JSX text counts only for tsx files", () => {
  const line = "<span>Feel free to retry</span>";
  assert.deepEqual(copyIn(line, "tsx"), ["Feel free to retry"]);
  assert.deepEqual(copyIn(line, "ts"), []);
  assert.deepEqual(scanText(line, "ts"), []);
  assert.equal(scanText(line, "tsx").length, 1);
});

test("the banned list stays short and certain", () => {
  assert.ok(BANNED.length <= 16);
  for (const word of BANNED) assert.equal(word, word.toLowerCase());
});

test("scanProject reads the UI folder and the backend, skipping tests and fakes", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "copy-check-"));
  try {
    await fs.mkdir(path.join(root, "frontend/src/local/deep"), { recursive: true });
    await fs.mkdir(path.join(root, "backend/nested"), { recursive: true });
    await fs.writeFile(path.join(root, "frontend/src/local/A.tsx"), "<p>Seamless!</p>\n");
    await fs.writeFile(path.join(root, "frontend/src/local/deep/b.ts"), "export const x = 'fine';\n");
    await fs.writeFile(path.join(root, "frontend/src/local/A.test.mjs"), "const bad = 'Great question';\n");
    await fs.writeFile(path.join(root, "backend/voice.py"), "TEXT = 'robust'\n");
    await fs.writeFile(path.join(root, "backend/test_voice.py"), "TEXT = 'robust'\n");
    await fs.writeFile(path.join(root, "backend/fake_thing.py"), "TEXT = 'robust'\n");
    await fs.writeFile(path.join(root, "backend/nested/deep.py"), "TEXT = 'robust'\n");
    await fs.writeFile(path.join(root, "backend/other.py"), "TEXT = 'robust'\n");
    const found = await scanProject(root);
    assert.deepEqual(
      found.map((v) => `${v.file}:${v.line}:${v.rule}`),
      ["frontend/src/local/A.tsx:1:banned", "frontend/src/local/A.tsx:1:exclamation", "backend/other.py:1:banned"],
      "the UI folder first, then the backend; voice.py may name the banned words, other.py may not",
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
