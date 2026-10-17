import { promises as fs } from "node:fs";
import path from "node:path";

/**
 * Shared "no ad-hoc pubkey truncation" guard.
 *
 * A truncated pubkey prefix is forgeable (anyone can grind a key that starts
 * with the same characters), so every display truncation goes through the
 * canonical `truncatePubkey` / `<PubKey>` helpers, which show both ends. This
 * guard stops new `pubkey.slice(0, N)` / `pubkey.substring(0, N)` forms from
 * fragmenting that again.
 *
 * It flags a `.slice(0, …)`, `.substring(0, …)` or `.substr(0, …)` call on an
 * identifier whose name ends in "pubkey" (any case, optional `?.`), such as
 * `pubkey.slice(0, 8)`, `authorPubkey?.slice(0, 12)` or `msg.pubkey.substring(0, 6)`.
 *
 * Non-display uses live in the `overrides` allowlist ("relativePath:lineNumber")
 * and whole files in `allowedFiles`, both supplied by each app.
 */

// The name ends in "pubkey", so `pubkeys.slice(0, 3)` (the first three keys of a list) isn't a truncation.
const PUBKEY_PREFIX_RE = /\b\w*pubkey\??\.(?:slice|substring|substr)\(\s*0\s*,/gi;

async function walkFiles(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const fullPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        return walkFiles(fullPath);
      }
      return [fullPath];
    }),
  );
  return files.flat();
}

/**
 * @param {object} options
 * @param {string} options.projectRoot Absolute path the rule roots resolve against.
 * @param {Array<{root: string, extensions: Set<string>}>} options.rules Where to scan.
 * @param {string} options.label Human label for the failure header.
 * @param {Set<string>} [options.overrides] Allowlisted "relativePath:lineNumber" entries.
 * @param {Set<string>} [options.allowedFiles] Relative paths skipped entirely.
 * @param {string} options.scriptPath Path mentioned in the failure hint.
 */
export async function runPubkeyTruncationCheck({
  projectRoot,
  rules,
  label,
  overrides = new Set(),
  allowedFiles = new Set(),
  scriptPath,
}) {
  const candidateFiles = (
    await Promise.all(
      rules.map((rule) => {
        const dir = path.join(projectRoot, rule.root);
        return fs
          .access(dir)
          .then(() => walkFiles(dir))
          .catch(() => []);
      }),
    )
  ).flat();

  const violations = [];

  for (const filePath of candidateFiles) {
    // Rule roots and allowlist keys are authored with `/`; compare in posix form
    // so Windows paths match too.
    const relativePath = path
      .relative(projectRoot, filePath)
      .split(path.sep)
      .join("/");
    const rule = rules.find((r) => relativePath.startsWith(`${r.root}/`));
    if (!rule || !rule.extensions.has(path.extname(relativePath))) {
      continue;
    }
    if (allowedFiles.has(relativePath)) {
      continue;
    }

    const content = await fs.readFile(filePath, "utf8");
    content.split(/\r?\n/).forEach((line, index) => {
      const lineNumber = index + 1;
      for (const match of line.match(PUBKEY_PREFIX_RE) ?? []) {
        if (!overrides.has(`${relativePath}:${lineNumber}`)) {
          violations.push({ relativePath, lineNumber, match });
        }
      }
    });
  }

  if (violations.length > 0) {
    console.error(`${label} pubkey-truncation check failed:`);
    for (const v of violations) {
      console.error(`- ${v.relativePath}:${v.lineNumber}: ${v.match}`);
    }
    console.error(
      "Show pubkeys with `truncatePubkey` or `<PubKey>`, which keep both ends " +
        "so a ground vanity prefix can't pass for someone else. If this isn't " +
        "an identity shown to people (an array window, a color seed), add a " +
        `\`relativePath:lineNumber\` exception with its reason in \`${scriptPath}\`.`,
    );
    process.exit(1);
  }
}
