/** Received-text pacing, informed by Convex Agent's Apache-2.0 useSmoothText.
 * This implementation also bounds backlog and preserves UTF-16 boundaries.
 * Reference: https://github.com/get-convex/agent/blob/main/src/react/useSmoothText.ts
 */
export const STREAM_FRAME_MS = 50;
export const INITIAL_STREAM_RATE = 128;

export function safeTextBoundary(text: string, index: number): number {
  const end = Math.max(0, Math.min(text.length, Math.floor(index)));
  const before = text.charCodeAt(end - 1);
  const after = text.charCodeAt(end);
  return before >= 0xd800 && before <= 0xdbff && (end === text.length || after >= 0xdc00 && after <= 0xdfff)
    ? end - 1 : end;
}

/** Reveal whole words: the end moves back to just after the last space or line break, unless everything received is
 * shown or the word is too long to hold back (a path, a URL or a code token). */
export function wordBoundary(text: string, end: number, longest = 24): number {
  const at = safeTextBoundary(text, end);
  if (at >= text.length || at === 0 || /\s/.test(text[at - 1])) return at;
  for (let i = at - 1; i >= Math.max(0, at - longest); i--) if (/\s/.test(text[i])) return i + 1;
  return at;
}

export function incomingRate(previous: number, added: number, elapsedMs: number): number {
  if (added <= 0 || elapsedMs <= 0) return previous;
  const observed = added * 1000 / Math.max(STREAM_FRAME_MS, elapsedMs);
  return Math.max(INITIAL_STREAM_RATE, Math.min(32_768, previous * 0.65 + observed * 0.35));
}

/** A burst spreads over about as long as it took to arrive, within these bounds: token-by-token streams lag no more
 * than the lower bound, and a chunk that came after a long pause still starts flowing at once instead of popping in. */
export const MIN_SPREAD_MS = 400;
export const MAX_SPREAD_MS = 1500;

/** The rate to release what is waiting: the observed incoming rate, or faster when a burst would otherwise lag longer
 * than its spread. Bursty providers (a paragraph every second or two) read as a steady flow of words, not as paragraphs
 * appearing whole. */
export function releaseRate(rate: number, backlog: number, sinceLastMs: number): number {
  const spread = Math.max(MIN_SPREAD_MS, Math.min(MAX_SPREAD_MS, Number.isFinite(sinceLastMs) ? sinceLastMs : MAX_SPREAD_MS));
  const steady = Number.isFinite(rate) && rate > 0 ? rate : INITIAL_STREAM_RATE;
  return Math.max(INITIAL_STREAM_RATE, steady, Math.max(0, backlog) * 1000 / spread);
}

export function advanceReceived(cursor: number, length: number, rate: number, elapsedMs: number): number {
  const effectiveRate = Math.max(INITIAL_STREAM_RATE, Number.isFinite(rate) ? rate : INITIAL_STREAM_RATE);
  return Math.min(length, cursor + effectiveRate * Math.max(0, Math.min(250, elapsedMs)) / 1000);
}
