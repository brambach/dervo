import { promises as fs } from "node:fs";
import path from "node:path";

/**
 * Shared file-size guard: one size discipline for source files.
 *
 * Each rule names a root, the extensions it covers and the most lines a file
 * under it may have. A file over its rule's limit fails the check, so a module
 * gets split before it grows past the point where it's easy to read and review.
 *
 * A file that already sits over its limit can be listed in a rule's optional
 * `exceptions` map ("relativePath" -> its current line count). It may shrink but
 * never grow past that count, so the exception only ratchets down.
 */

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

function countLines(content) {
  if (content.length === 0) {
    return 0;
  }
  const lines = content.split(/\r?\n/).length;
  return content.endsWith("\n") ? lines - 1 : lines;
}

/**
 * @param {object} options
 * @param {string} options.projectRoot Absolute path the rule roots resolve against.
 * @param {Array<{root: string, extensions: Set<string>, maxLines: number, exceptions?: Record<string, number>}>} options.rules
 * @param {string} options.label Human label for the failure header.
 * @returns {Promise<Array<{relativePath: string, lines: number, limit: number}>>} The files over their limit.
 */
export async function findOversizedFiles({ projectRoot, rules }) {
  const oversized = [];
  for (const rule of rules) {
    const dir = path.join(projectRoot, rule.root);
    const files = await fs
      .access(dir)
      .then(() => walkFiles(dir))
      .catch(() => []);
    for (const filePath of files) {
      if (!rule.extensions.has(path.extname(filePath))) {
        continue;
      }
      const relativePath = path
        .relative(projectRoot, filePath)
        .split(path.sep)
        .join("/");
      const lines = countLines(await fs.readFile(filePath, "utf8"));
      const limit = Math.max(rule.maxLines, rule.exceptions?.[relativePath] ?? 0);
      if (lines > limit) {
        oversized.push({ relativePath, lines, limit });
      }
    }
  }
  return oversized.sort((a, b) => a.relativePath.localeCompare(b.relativePath));
}

/** Prints each file over its limit and exits 1, or returns quietly when every file fits. */
export async function runFileSizeCheck({ projectRoot, rules, label }) {
  const oversized = await findOversizedFiles({ projectRoot, rules });
  if (oversized.length > 0) {
    console.error(`${label} file-size check failed:`);
    for (const file of oversized) {
      console.error(`- ${file.relativePath}: ${file.lines} lines (limit ${file.limit})`);
    }
    console.error(
      "Split the file into smaller modules. A file that was already over the " +
        "limit can be listed in its rule's `exceptions` at its current size, " +
        "and may only shrink from there.",
    );
    process.exit(1);
  }
}
