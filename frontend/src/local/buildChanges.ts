import {t} from '@/i18n';
/** Public notes for the source in this build. Release identity comes from the installed app. The first three are what
 * the "You're on" card shows after an update (UpdatePrompt.tsx), so they name what testers will notice most. Beta.16,
 * 26 Sep 2026. */
export const buildChanges = [
 t('Threads ask their questions the way Home does, above the composer, and a summary strip at the top says where the thread stands.'),
 t('Mark each account as Work or Personal. Work projects only run on work accounts and personal projects on personal ones, and onboarding says why.'),
 t('Continue in Codex or Claude after any stop, including your own Stop or one that left files half changed.'),
 t('See what Dervo remembers: notes and skills apply straight away, replies show the notes they used, and Knowledge lists them all.'),
 t('Open a pull request from a thread and follow its status. History groups runs by thread.'),
 t('Each project has a Next up list. Threads you haven’t touched in 14 days are put away under Archived and come back when you open them.'),
 t('Codex 0.156.1 is bundled, so GPT-6 Sol and Luna show up in the model picker.'),
 t('Pin a thread from ⌘K.'),
 t('Saved notes that read like a rule, or say who decided, carry a mark so you can tell them from plain facts.'),
];

/** The release these notes were written for. "You're on" shows them only for this version, so a later build that
 * forgets to replace them shows no list rather than older notes. */
export const buildChangesRelease = '0.1.0-beta.16';
