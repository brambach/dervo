import './WorkerQuestions.css';
import {t} from '@/i18n';

/** The question above your answer in a thread (beta.16.1, Bryce, 26 Sep: "i cant see the question only the answers").
 * It's the asker's words, captioned as theirs, so it never reads as something you wrote. The row gets it from the
 * dervo-question tag timelineRows.ts puts on an answer it recognised (workerQuestionState.answeredMessage). */
export function AnsweredQuestion({asker,ask}:{asker:string;ask:string}){
 return <figure className="dv-answered-q" data-asker={asker}>
  <figcaption>{t('{asker} asked',{asker})}</figcaption>
  <blockquote>{ask}</blockquote>
 </figure>;
}
