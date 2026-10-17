import {isHostMessage} from '@/i18n/hostMessages';

/** The bridge's two busy answers (src/bridge.rs): a request turned away because the host was still on a long one ("so this
 * wasn’t done"), or one that got no answer in 10 s ("it may still finish"). They say nothing about what was asked, and a
 * read is safe to repeat, so a read that gets one tries again rather than reporting a failure. */
export const isBusyAnswer=(error:unknown)=>isHostMessage(error,'host_busy_skipped')||isHostMessage(error,'host_busy_still');
