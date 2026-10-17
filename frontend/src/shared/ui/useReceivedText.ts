import {useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore} from 'react';
import {advanceReceived, incomingRate, INITIAL_STREAM_RATE, releaseRate, safeTextBoundary, STREAM_FRAME_MS, wordBoundary} from './receivedText';

const reducedQuery = '(prefers-reduced-motion: reduce)';
function subscribeMotion(callback: () => void) {
  const query = typeof window !== 'undefined' ? window.matchMedia?.(reducedQuery) : undefined;
  query?.addEventListener('change', callback);
  return () => query?.removeEventListener('change', callback);
}
function reducedSnapshot() {return typeof window !== 'undefined' && !!window.matchMedia?.(reducedQuery).matches;}

/** A leaf owns its clock. Saved text, copy actions and the workspace feed stay authoritative. */
export function useReceivedText(text: string, active: boolean, identity: string) {
  const reduced = useSyncExternalStore(subscribeMotion, reducedSnapshot, () => true);
  const paced = active && !reduced;
  const [visible, setVisible] = useState({identity, source: text, end: text.length});
  const state = useRef({identity, text, cursor: text.length, rate: INITIAL_STREAM_RATE, release: INITIAL_STREAM_RATE, receivedAt: performance.now(), tickAt: performance.now()});

  useLayoutEffect(() => {
    const now = performance.now();
    const current = state.current;
    const reset = current.identity !== identity || !text.startsWith(current.text);
    if (reset) {
      state.current = {identity, text, cursor: text.length, rate: INITIAL_STREAM_RATE, release: INITIAL_STREAM_RATE, receivedAt: now, tickAt: now};
    } else {
      current.rate = incomingRate(current.rate, text.length - current.text.length, now - current.receivedAt);
      // What just arrived is released over about as long as it took to arrive, so a burst never pops in whole.
      if (text !== current.text) {current.release = releaseRate(current.rate, text.length - current.cursor, now - current.receivedAt); current.receivedAt = now;}
      current.text = text;
      if (!paced) current.cursor = text.length;
    }
    const next = state.current;
    const end = reset || !paced ? text.length : wordBoundary(text, next.cursor);
    // Shown text only grows while the same reply streams: a word held back never disappears again.
    setVisible(previous => ({identity, source: text, end: !reset && previous.identity === identity && text.startsWith(previous.source.slice(0, previous.end))
      ? Math.max(end, Math.min(previous.end, text.length)) : end}));
  }, [text, paced, identity]);

  useEffect(() => {
    if (!paced) return;
    state.current.tickAt = performance.now();
    // Every display frame, so received text flows instead of stepping. Without animation frames, a timer at the same cadence.
    const frame = typeof requestAnimationFrame === 'function'
      ? {next: (callback: () => void) => requestAnimationFrame(callback), cancel: (handle: number) => cancelAnimationFrame(handle)}
      : {next: (callback: () => void) => setTimeout(callback, STREAM_FRAME_MS / 3) as unknown as number, cancel: (handle: number) => clearTimeout(handle)};
    let handle: number;
    const tick = () => {
      const current = state.current;
      if (current.identity === identity) {
        const now = performance.now();
        current.cursor = advanceReceived(current.cursor, current.text.length, current.release, now - current.tickAt);
        current.tickAt = now;
        const boundary = wordBoundary(current.text, current.cursor);
        setVisible(previous => {
          const end = previous.identity === identity && current.text.startsWith(previous.source.slice(0, previous.end)) ? Math.max(boundary, previous.end) : boundary;
          return previous.identity === identity && previous.source === current.text && previous.end === end ? previous : {identity, source: current.text, end};
        });
      }
      handle = frame.next(tick);
    };
    handle = frame.next(tick);
    return () => frame.cancel(handle);
  }, [paced, identity]);

  if (!paced) return text;
  if (visible.identity !== identity || !text.startsWith(visible.source)) return '';
  return text.slice(0, safeTextBoundary(text, visible.end));
}
