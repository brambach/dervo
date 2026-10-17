import './Microtasks.css';
import {t} from '@/i18n';

/** The microtask pattern, taught in first run. Bryce, 30 Sep 2026: the founder has run 1523 tasks at a median 49 seconds,
 * while testers try one big task and stop, so first run says "keep Dervo open beside you and fire off tiny tasks". The words
 * live here once, so the welcome, the project-created moment and Home's empty state can't drift apart.
 *
 * It promises only what Home does today: you ask in plain words, Dervo finds the right thread or starts one, and the task
 * shows under Running, then under Needs you or Since you left. The examples show size and nothing else. They're plain text,
 * never rows that look like work in progress, because first run has no live activity to show. */

/** The welcome's headline and the sentence under it. Since 30 Sep the welcome leads with the pattern itself. The 19 Sep
 * router line it replaced lives on in posts and replies (notes/POSITIONING.md), and putting it back is one string. */
// i18n-keys: shown through t() where they're used.
export const MICROTASK_HEADLINE='Keep Dervo open beside you and fire off tiny tasks.';
export const MICROTASK_WELCOME='A failing test, a rename, a question about the code. Your Claude Code or Codex does each one, and Dervo tells you what finished and what needs you.'; // i18n-keys

/** What tiny looks like. Illustrations of size, not suggestions for this project. */
// i18n-keys: catalog keys, shown through t().
export const MICROTASK_EXAMPLES=['Why is the signup test failing?','Where do we handle refunds?','Rename getUser to fetchUser.'] as const;

/** The lesson on the project-created moment. Before approval Home can't take a task, so the line says when it can and no
 * button is offered. After it, one button goes to Home, where a task is asked for. */
export function MicrotaskTip({approved=true,onHome}:{approved?:boolean;onHome?:()=>void}){
  return <div className="dv-mt">
    <h3 className="dv-mt-title">{t('Keep Dervo open beside you')}</h3>
    <p className="dv-mt-line">{approved?t('When something small comes up, ask Dervo on Home and get back to what you were doing. Dervo finds the right thread or starts a new one, and shows you when each task is done or needs you.')
      :t('Once you’re approved, ask Dervo on Home when something small comes up and get back to what you were doing. Dervo finds the right thread or starts a new one, and shows you when each task is done or needs you.')}</p>
    <p className="dv-mt-label">{t('Tiny tasks look like this:')}</p>
    <ul className="dv-mt-list">{MICROTASK_EXAMPLES.map(text=><li key={text}>“{t(text)}”</li>)}</ul>
    {approved&&onHome&&<div className="dv-moment-actions"><button type="button" className="dv-btn dv-btn-ghost" onClick={onHome}>{t('Try one on Home')}</button></div>}
  </div>;
}
