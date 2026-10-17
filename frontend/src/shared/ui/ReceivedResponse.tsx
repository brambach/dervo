import {useEffect, useState, type ReactNode} from 'react';
import {useReceivedText} from './useReceivedText';
import {PresenceText} from '@/local/Presence';
import {ReplyRunChip, ReplyTrailLine} from '@/local/ReplyTrailView';
import type {ReplyRun} from '@/local/replyTrail';
import './ReceivedResponse.css';

export type ResponsePresentation = {identity: string; label: string};

/** After this long without new text, a live reply says what's happening under it instead of looking finished. */
export const RESPONSE_IDLE_MS = 900;

/** A coding run's reply (``run``) shows its trail under the words while it works and folds into the end chip after
 * (16.2, mock F). The working smile left the reply row then: it stays in Progress and the composer's status row. */
export function ReceivedResponse({text, presentation, run, children}: {
  text: string; presentation?: ResponsePresentation; run?: ReplyRun;
  children: (visible: string, transient: boolean, streamingCompatible: boolean) => ReactNode;
}) {
  const visible = useReceivedText(text, !!presentation, presentation?.identity ?? 'saved');
  const [streamed, setStreamed] = useState(!!presentation);
  if (presentation && !streamed) setStreamed(true);
  const [waitingFor, setWaitingFor] = useState<string>();
  useEffect(() => {
    if (!presentation || text) {setWaitingFor(undefined); return;}
    const timer = setTimeout(() => setWaitingFor(presentation.identity), 160);
    return () => clearTimeout(timer);
  }, [presentation?.identity, text]);
  // A step named by the run shows at once; plain Thinking only once the text has paused, so flowing text isn't interrupted.
  const caughtUp = visible.length >= text.length;
  const [idleFor, setIdleFor] = useState<string>();
  useEffect(() => {
    setIdleFor(undefined);
    if (!presentation || !text || !caughtUp) return;
    const timer = setTimeout(() => setIdleFor(text), RESPONSE_IDLE_MS);
    return () => clearTimeout(timer);
  }, [presentation?.identity, text, caughtUp]);
  const trailing = !!presentation && !!text && caughtUp && (presentation.label !== 'Thinking' || idleFor === text);
  if (run) {
    // One trail element from the first frame to the last, so its steps never restart when the words arrive.
    const chip = !presentation && run.tail && run.latest && !run.live;
    return <div className="dervo-received-response" data-response-active={!!presentation || undefined}>
      {text || !presentation ? children(visible, !!presentation, streamed) : null}
      {presentation && <div className={text ? 'dervo-response-activity' : 'dervo-response-waiting'} role="status" aria-live="polite" aria-atomic="false"><ReplyTrailLine run={run}/></div>}
      {chip && <div className="dervo-response-chip"><ReplyRunChip run={run}/></div>}
    </div>;
  }
  return <div className="dervo-received-response" data-response-active={!!presentation || undefined}>
    {presentation && !text ? <div className="dervo-response-waiting" role="status" aria-live="polite" aria-atomic="false">
      {waitingFor === presentation.identity && <PresenceText className="dv-live-step" active>{presentation.label}</PresenceText>}
    </div> : <>
      {children(visible, !!presentation, streamed)}
      {presentation && <div className="dervo-response-activity" role="status" aria-live="polite" aria-atomic="false">
        {trailing && <PresenceText className="dv-live-step" active>{presentation.label}</PresenceText>}
      </div>}
    </>}
  </div>;
}
