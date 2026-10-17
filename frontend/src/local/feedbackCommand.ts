/** `/feedback` opens the Send feedback form, and `/bug` opens bug capture (Send feedback on Bug for a tester). Neither
 * starts a task, and the composer keeps its draft. */
export type FeedbackCommand='feedback'|'bug';
export function feedbackCommandKind(text:string):FeedbackCommand|null{
  const word=text.trim().toLowerCase();
  return word==='/feedback'?'feedback':word==='/bug'?'bug':null;
}
/** Home asks only whether its text is a command, then calls its onFeedback with nothing, so the kind this matched is
 * kept here for the shell to take (takeFeedbackCommand). */
let last:FeedbackCommand|null=null;
export function isFeedbackCommand(text:string):boolean{last=feedbackCommandKind(text);return last!==null;}
export function takeFeedbackCommand():FeedbackCommand{const kind=last??'feedback';last=null;return kind;}
