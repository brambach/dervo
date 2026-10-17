import {formatList,t} from '@/i18n';

/** The opening row of a run that a note or a review started (beta.17, #27). The host marks such a task with
 * ``note_origin`` from its own records (thread_notes.annotate), so the row is never guessed from the message text. */
export type NoteOrigin={id:string;kind:'note'|'review';fromName:string|null;fromChannelId:string|null;text:string|null;trigger:'auto'|'click'|null;late:boolean;children:string[]};
/** The row tag that carries it to the message row: [tag, kind, from, quote, what was sent]. MessageRow draws NoteQuote. */
export const NOTE_TAG='dervo-note';
const listed=(names:string[])=>formatList(names);

/** Dervo's one line under the quote, and the tag. Your own words never appear here: you only clicked. */
export function noteRow(origin:NoteOrigin,sent:string):{content:string;tag:string[]}{
 if(origin.kind==='review'){
  const who=listed(origin.children);
  const content=origin.late?t('Started when they’d finished again, after the earlier review.')
   :origin.trigger==='auto'?t('Started on its own when they’d all finished. It reads; it doesn’t change files.')
   :t('You started this review. It reads; it doesn’t change files.');
  return {content,tag:[NOTE_TAG,'review','Dervo',who,sent]};
 }
 const from=origin.fromName??t('another thread');
 // Beta.17.1: one Dervo passed on by itself says so, so the thread shows why it started again without you.
 const content=origin.trigger==='auto'?t('Passed on automatically from {from}, because this project passes notes on by itself.',{from})
  :t('You passed this on from {from}.',{from});
 return {content,tag:[NOTE_TAG,'note',from,origin.text??'',sent]};
}
