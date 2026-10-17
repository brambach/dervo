// Code colours come from Dervo's own Shiki theme, built from the dv- tokens, not GitHub's (dark audit #5).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const { DERVO_SHIKI_LIGHT, DERVO_SHIKI_DARK, isDervoShikiThemeName } = await import("./dervo-shiki-theme.ts");
const { resolveShikiThemeName, shikiThemeInput, loadThemeData, extractThemeInfo } = await import("./theme-loader.ts");
const { createHighlighter } = await import("shiki");

const tokensCss = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "local", "DervoTokens.css"), "utf8");
const token = (block, name) => new RegExp(`${block}[\\s\\S]*?--dv-${name}:\\s*(#[0-9a-f]{6})`, "i").exec(tokensCss)?.[1];
const light = (name) => token(":root\\[data-dervo-local\\] \\{", name);
const dark = (name) => token(":root\\[data-dervo-local\\]\\.dark \\{", name);

test("the two themes read the tokens: canvas, ink, live for added, rust for deleted", () => {
  assert.equal(DERVO_SHIKI_LIGHT.colors["editor.background"], light("canvas"));
  assert.equal(DERVO_SHIKI_LIGHT.colors["editor.foreground"], light("ink"));
  assert.equal(DERVO_SHIKI_LIGHT.colors["gitDecoration.addedResourceForeground"], light("live"));
  assert.equal(DERVO_SHIKI_LIGHT.colors["gitDecoration.deletedResourceForeground"], light("rust"));
  assert.equal(DERVO_SHIKI_DARK.colors["editor.background"], dark("canvas"));
  assert.equal(DERVO_SHIKI_DARK.colors["editor.foreground"], dark("ink"));
  assert.equal(DERVO_SHIKI_DARK.colors["gitDecoration.addedResourceForeground"], dark("live"));
  assert.equal(DERVO_SHIKI_DARK.colors["gitDecoration.deletedResourceForeground"], dark("rust"));
  // Negative control: the themes are not each other, and neither is GitHub's.
  assert.notEqual(DERVO_SHIKI_DARK.colors["editor.background"], DERVO_SHIKI_LIGHT.colors["editor.background"]);
  assert.notEqual(DERVO_SHIKI_DARK.colors["editor.background"], "#24292e");
});

test("buzz and buzz-dark resolve to Dervo's themes; bundled names pass through", async () => {
  assert.equal(resolveShikiThemeName("buzz"), "dervo-light");
  assert.equal(resolveShikiThemeName("buzz-dark"), "dervo-dark");
  assert.equal(resolveShikiThemeName("nord"), "nord");
  assert.equal(shikiThemeInput("dervo-dark"), DERVO_SHIKI_DARK);
  assert.equal(shikiThemeInput("nord"), "nord");
  assert.ok(isDervoShikiThemeName("dervo-light") && !isDervoShikiThemeName("github-dark"));
  const info = extractThemeInfo("buzz-dark", await loadThemeData("buzz-dark"));
  assert.equal(info.bg, dark("canvas"));
  assert.equal(info.added, dark("live"));
  assert.equal(info.deleted, dark("rust"));
});

test("Shiki loads the theme and colours a diff with live and rust only", async () => {
  const highlighter = await createHighlighter({ themes: [shikiThemeInput("dervo-dark")], langs: ["diff"] });
  const { tokens } = highlighter.codeToTokens("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n", { lang: "diff", theme: "dervo-dark" });
  const colourOf = (text) => tokens.flat().find((t) => t.content.startsWith(text))?.color?.toLowerCase();
  assert.equal(colourOf("+new"), dark("live"));
  assert.equal(colourOf("-old"), dark("rust"));
  assert.equal(colourOf("@@"), dark("muted"));
  // Negative control: an unchanged colour is not GitHub Dark's green or red.
  assert.ok(!["#85e89d", "#fdaeb7"].includes(colourOf("+new")));
  highlighter.dispose();
});
