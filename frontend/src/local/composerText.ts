/** The thread's message box (DvComposer) fires this, with its words as ``detail``, whenever they change. The question
 * tray reads the box through it, since a textarea's words aren't in the DOM for a mutation to see. */
export const COMPOSER_TEXT_EVENT='dervo:composer-text';
