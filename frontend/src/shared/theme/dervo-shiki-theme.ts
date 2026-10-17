/**
 * Dervo's code colours for Shiki, light and dark, built from the dv- tokens in
 * `local/DervoTokens.css`. Code blocks, diffs and the terminal read these
 * instead of GitHub's palette (dark audit #5, 19 Sep 2026).
 *
 * The values are literals because Shiki wants hex, not CSS variables; keep
 * them in step with the tokens. The theme is calm on purpose: ink for names,
 * muted for punctuation and comments, sage for keywords and strings' cousins,
 * live green for strings, numbers and inserted lines. Rust marks only what
 * needs you: a deleted line, or something invalid.
 */
import type { ThemeRegistrationRaw } from "shiki";

export type DervoShikiThemeName = "dervo-light" | "dervo-dark";

interface DervoCodePalette {
  canvas: string;
  raised: string;
  sunk: string;
  ink: string;
  ink2: string;
  muted: string;
  faint: string;
  line: string;
  lineStrong: string;
  sage: string;
  live: string;
  rust: string;
  pine: string;
}

const LIGHT: DervoCodePalette = {
  canvas: "#fdfdfc",
  raised: "#ffffff",
  sunk: "#f2f1ed",
  ink: "#1b1b19",
  ink2: "#3c3c38",
  muted: "#5b5a55",
  faint: "#696762",
  line: "#ebeae5",
  lineStrong: "#dcdbd4",
  sage: "#5e7351",
  live: "#2f7844",
  rust: "#b0461f",
  pine: "#22382b",
};

const DARK: DervoCodePalette = {
  canvas: "#1b1a18",
  raised: "#242220",
  sunk: "#2e2c29",
  ink: "#eeece7",
  ink2: "#cdc9c1",
  muted: "#b4b0a7",
  faint: "#a5a198",
  line: "#2d2b28",
  lineStrong: "#433f3a",
  sage: "#adc19b",
  live: "#80c993",
  rust: "#e0845b",
  pine: "#dfe8d6",
};

function scope(
  scopes: string | string[],
  foreground: string,
  fontStyle?: "italic" | "bold" | "underline",
) {
  return {
    scope: scopes,
    settings: fontStyle ? { foreground, fontStyle } : { foreground },
  };
}

function build(
  name: DervoShikiThemeName,
  type: "light" | "dark",
  p: DervoCodePalette,
): ThemeRegistrationRaw {
  return {
    name,
    displayName: type === "dark" ? "Dervo Dark" : "Dervo",
    type,
    colors: {
      "editor.background": p.canvas,
      "editor.foreground": p.ink,
      "editorLineNumber.foreground": p.faint,
      "editor.selectionBackground": p.sunk,
      "editorGutter.addedBackground": p.live,
      "editorGutter.deletedBackground": p.rust,
      "editorGutter.modifiedBackground": p.sage,
      "gitDecoration.addedResourceForeground": p.live,
      "gitDecoration.deletedResourceForeground": p.rust,
      "gitDecoration.modifiedResourceForeground": p.sage,
      "diffEditor.insertedTextBackground": p.live,
      "diffEditor.removedTextBackground": p.rust,
      "terminal.background": p.canvas,
      "terminal.foreground": p.ink,
      "terminal.ansiBlack": type === "dark" ? p.sunk : p.ink,
      "terminal.ansiRed": p.rust,
      "terminal.ansiGreen": p.live,
      "terminal.ansiYellow": p.sage,
      "terminal.ansiBlue": p.ink2,
      "terminal.ansiMagenta": p.rust,
      "terminal.ansiCyan": p.sage,
      "terminal.ansiWhite": type === "dark" ? p.ink : p.lineStrong,
      "terminal.ansiBrightBlack": p.muted,
      "terminal.ansiBrightRed": p.rust,
      "terminal.ansiBrightGreen": p.live,
      "terminal.ansiBrightYellow": p.sage,
      "terminal.ansiBrightBlue": p.ink2,
      "terminal.ansiBrightMagenta": p.rust,
      "terminal.ansiBrightCyan": p.sage,
      "terminal.ansiBrightWhite": type === "dark" ? p.ink : p.canvas,
    },
    settings: [
      { settings: { background: p.canvas, foreground: p.ink } },
      scope(["comment", "punctuation.definition.comment"], p.muted, "italic"),
      scope(
        [
          "punctuation",
          "meta.brace",
          "keyword.operator",
          "punctuation.separator",
          "punctuation.terminator",
          "punctuation.definition.string",
          "punctuation.definition.tag",
        ],
        p.muted,
      ),
      scope(
        [
          "keyword",
          "storage",
          "storage.type",
          "storage.modifier",
          "keyword.control",
          "keyword.operator.new",
          "keyword.operator.expression",
          "keyword.operator.logical.python",
          "variable.language",
          "constant.language.import-export-all",
        ],
        p.sage,
      ),
      scope(
        ["string", "string.quoted", "string.template", "string.regexp", "constant.other.symbol"],
        p.live,
      ),
      scope(
        ["constant.numeric", "constant.language", "constant.character", "constant.character.escape"],
        p.live,
      ),
      scope(["entity.name.function", "support.function", "meta.function-call.generic"], p.ink),
      scope(
        ["entity.name.type", "entity.name.class", "support.type", "support.class", "entity.name.namespace"],
        p.ink,
      ),
      scope(["variable", "variable.parameter", "meta.definition.variable"], p.ink),
      scope(
        ["variable.other.property", "support.type.property-name", "meta.object-literal.key", "entity.name.tag.yaml"],
        p.ink2,
      ),
      scope(["entity.name.tag", "meta.tag"], p.sage),
      scope(["entity.other.attribute-name"], p.ink2),
      scope(["support.type.property-name.css", "support.type.vendored.property-name.css"], p.ink2),
      scope(["entity.name.tag.css", "entity.other.attribute-name.class.css", "entity.other.attribute-name.id.css"], p.sage),
      scope(["meta.decorator", "punctuation.decorator", "entity.name.function.decorator"], p.sage),
      scope(["meta.preprocessor", "keyword.control.directive"], p.sage),
      scope(["markup.heading", "entity.name.section.markdown"], p.ink),
      scope(["markup.italic"], p.ink2, "italic"),
      scope(["markup.bold"], p.ink),
      scope(["markup.inline.raw", "markup.raw"], p.ink2),
      scope(["markup.underline.link", "string.other.link", "constant.other.reference.link"], p.sage),
      scope(["markup.quote"], p.muted),
      scope(["markup.list punctuation.definition.list.begin"], p.faint),
      scope(["markup.inserted", "punctuation.definition.inserted", "meta.diff.header.to-file"], p.live),
      scope(["markup.deleted", "punctuation.definition.deleted", "meta.diff.header.from-file"], p.rust),
      scope(["markup.changed"], p.sage),
      scope(["meta.diff.header", "meta.diff.range", "meta.diff.index", "punctuation.definition.range.diff"], p.muted),
      scope(["invalid", "invalid.illegal", "invalid.deprecated"], p.rust),
    ],
  };
}

export const DERVO_SHIKI_LIGHT: ThemeRegistrationRaw = build("dervo-light", "light", LIGHT);
export const DERVO_SHIKI_DARK: ThemeRegistrationRaw = build("dervo-dark", "dark", DARK);

export const DERVO_SHIKI_THEMES: Readonly<Record<DervoShikiThemeName, ThemeRegistrationRaw>> = {
  "dervo-light": DERVO_SHIKI_LIGHT,
  "dervo-dark": DERVO_SHIKI_DARK,
};

export function isDervoShikiThemeName(name: string): name is DervoShikiThemeName {
  return name === "dervo-light" || name === "dervo-dark";
}
