import type {PendingQuestion,QuestionAnswer,QuestionOption} from './questionShape';
import {t} from '@/i18n';

export type WorkerQuestionAnswer={kind:'option';optionId:string}|{kind:'text';text:string}|{kind:'skip'};
export type WorkerQuestion={id:string;taskId:string;runId:string;asker:string;ask:string;options:{id:string;label:string;detail:string|null;grant:null}[];state:'pending'|'answered'|'skipped'|'stale';answer:(WorkerQuestionAnswer&{text?:string})|null;answerClientId:string|null;delivery:'saved'|'sent'|'queued'|'not_sent'|'unknown'|'cancelled'|null;canAnswer:boolean;canSkip:boolean;detail:string|null;createdAt:number;
 /** Beta.16.1: the run that asked has ended, and your answer can still go in as the thread's next message, with the
  * asking task's access and no more (task_questions.ended). False when the account, model or access changed since. */
 canCarry:boolean;
 /** Answered after its run ended, so the answer went in as the thread's next message. */
 carried:boolean};
export type WorkerQuestionScope={projectId:string;channelId:string;taskId:string;questionId:string};
export type QuestionSubmission={clientId:string;answer:WorkerQuestionAnswer};
export type WorkerQuestionDraft={text:string;pending:QuestionSubmission|null};
export const ANSWER_BYTES=8192;
const record=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
const bounded=(value:unknown,characters:number,bytes=characters*4):value is string=>typeof value==='string'&&!!value.trim()&&Array.from(value).length<=characters&&new TextEncoder().encode(value).length<=bytes;
const identity=(value:unknown):value is string=>bounded(value,256);

/** Rebuild display fields only. A worker's choices never carry permissions or a destination. */
export function readWorkerQuestion(raw:unknown):WorkerQuestion|null{
 if(!record(raw)||!identity(raw.id)||!identity(raw.taskId)||!identity(raw.runId)||!bounded(raw.asker,80)||!bounded(raw.ask,200)||!Array.isArray(raw.options)||raw.options.length>3)return null;
 const options:WorkerQuestion['options']=[];
 for(const option of raw.options){
  if(!record(option)||!identity(option.id)||options.some(item=>item.id===option.id)||!bounded(option.label,80)||option.grant!=null||(option.detail!=null&&!bounded(option.detail,120)))return null;
  options.push({id:option.id,label:option.label,detail:option.detail as string|null??null,grant:null});
 }
 if(!['pending','answered','skipped','stale'].includes(String(raw.state))||typeof raw.canAnswer!=='boolean'||typeof raw.canSkip!=='boolean'||typeof raw.createdAt!=='number'||!Number.isFinite(raw.createdAt))return null;
 if(raw.delivery!=null&&!['saved','sent','queued','not_sent','unknown','cancelled'].includes(String(raw.delivery)))return null;
 if(raw.answerClientId!=null&&!identity(raw.answerClientId)||raw.detail!=null&&typeof raw.detail!=='string')return null;
 if(raw.canCarry!=null&&typeof raw.canCarry!=='boolean')return null;
 let answer:WorkerQuestion['answer']=null;
 if(raw.answer!=null){
  const stored=readSubmission({clientId:'view',answer:raw.answer});
  if(!stored)return null;
  if(record(raw.answer)&&raw.answer.text!=null&&typeof raw.answer.text!=='string')return null;
  answer={...stored.answer,...(record(raw.answer)&&typeof raw.answer.text==='string'?{text:raw.answer.text}:{})};
 }
 return {id:raw.id,taskId:raw.taskId,runId:raw.runId,asker:raw.asker,ask:raw.ask,options,state:raw.state as WorkerQuestion['state'],answer,answerClientId:raw.answerClientId as string|null??null,delivery:raw.delivery as WorkerQuestion['delivery']??null,canAnswer:raw.canAnswer,canSkip:raw.canSkip,detail:raw.detail as string|null??null,createdAt:raw.createdAt,canCarry:raw.canCarry===true&&raw.state==='stale',carried:raw.carried===true};
}
export function answerProblem(text:string):string|null{
 if(!text.trim())return t('Write an answer or choose an option.');
 return new TextEncoder().encode(text).length>ANSWER_BYTES?t('Keep your answer within 8 KB. Your draft is still here.'):null;
}
export const questionDraftKey=(scope:WorkerQuestionScope)=>`dervo-worker-question:${JSON.stringify([scope.projectId,scope.channelId,scope.taskId,scope.questionId])}`;
function readSubmission(raw:unknown):QuestionSubmission|null{
 if(!record(raw)||!identity(raw.clientId)||!record(raw.answer))return null;
 const answer=raw.answer;
 if(answer.kind==='skip')return {clientId:raw.clientId,answer:{kind:'skip'}};
 if(answer.kind==='option'&&identity(answer.optionId))return {clientId:raw.clientId,answer:{kind:'option',optionId:answer.optionId}};
 if(answer.kind==='text'&&typeof answer.text==='string'&&!answerProblem(answer.text))return {clientId:raw.clientId,answer:{kind:'text',text:answer.text}};
 return null;
}
export function loadQuestionDraft(scope:WorkerQuestionScope):WorkerQuestionDraft{
 const value=localStorage.getItem(questionDraftKey(scope));
 if(value===null)return {text:'',pending:null};
 const raw:unknown=JSON.parse(value);
 if(!record(raw)||typeof raw.text!=='string'||raw.pending!=null&&!readSubmission(raw.pending))throw Error(t('The saved answer draft couldn’t be opened. Nothing was sent.'));
 return {text:raw.text,pending:readSubmission(raw.pending)};
}
export function saveQuestionDraft(scope:WorkerQuestionScope,draft:WorkerQuestionDraft){
 if(draft.text||draft.pending)localStorage.setItem(questionDraftKey(scope),JSON.stringify(draft));else localStorage.removeItem(questionDraftKey(scope));
}
/** Beta.15 (Bryce, 24 Sep): what sits above the composer. A question still waiting for you (pending, or stale and only
 * skippable), one whose delivery isn't confirmed, and one with an unsent draft on this device. An answered or skipped
 * one leaves for one quiet line in the thread's history (questionLine), so nothing looks like it still needs an answer. */
export function inTray(question:WorkerQuestion,projectId:string,channelId:string):boolean{
 if(question.state==='pending'||question.state==='stale'||question.delivery==='unknown')return true;
 // Beta.16: your words are in the thread's composer now, so only an answer saved but not confirmed keeps a settled
 // question here. A beta.15 draft's unsent text can't answer a settled question, so it doesn't hold the tray.
 try{return !!loadQuestionDraft({projectId,channelId,taskId:question.taskId,questionId:question.id}).pending;}
 catch{return true;}  // a draft that can't be read stays where its error shows
}
/** Beta.16 (Bryce, 25 Sep): a thread's questions sit on Home's tray above the thread composer, one at a time, oldest
 * first ("1 of 2" when two tasks ask at once). These are the ones the tray has, in that order. */
export function trayQuestions(questions:readonly unknown[],projectId:string,channelId:string):{questions:WorkerQuestion[];broken:boolean}{
 const parsed=questions.map(readWorkerQuestion);
 const tray=parsed.filter((question):question is WorkerQuestion=>!!question&&inTray(question,projectId,channelId)).sort((a,b)=>a.createdAt-b.createdAt);
 return {questions:tray,broken:parsed.some(question=>!question)};
}
/** The question the thread composer's words answer right now: the first in the tray, still waiting, answerable here,
 * with nothing already saved for it. Null means your words go to the thread as a message, as usual. */
export function openQuestion(questions:readonly unknown[],projectId:string,channelId:string,disabled=false):WorkerQuestion|null{
 const first=trayQuestions(questions,projectId,channelId).questions[0];
 if(!first||disabled||!takesAnswer(first))return null;
 try{if(loadQuestionDraft({projectId,channelId,taskId:first.taskId,questionId:first.id}).pending)return null;}catch{return null;}
 return first;
}
/** Whether the question can take an answer now: still waiting on its run, or its run ended and the answer goes in as
 * the thread's next message (beta.16.1). */
export function takesAnswer(question:WorkerQuestion):boolean{
 return question.state==='pending'&&question.canAnswer||question.state==='stale'&&question.canCarry;
}
/** Whether an answer to this question is saved on this device and not yet confirmed (the tray's Retry line). */
export function savedAnswer(question:WorkerQuestion,projectId:string,channelId:string):boolean{
 try{return !!loadQuestionDraft({projectId,channelId,taskId:question.taskId,questionId:question.id}).pending;}catch{return false;}
}
/** A worker's question as Home's tray shows it: its own choices, then Skip as a quiet last choice when it allows one.
 * No choice ever carries a grant: a worker's options are display fields only (readWorkerQuestion). */
export function trayQuestion(question:WorkerQuestion):PendingQuestion{
 const options:QuestionOption[]=question.options.map(option=>({label:option.label,detail:option.detail,grant:null}));
 if(question.canSkip)options.push({label:t('Skip'),detail:t('Leave it unanswered'),grant:null,quiet:true});
 return {id:question.id,asker:question.asker,ask:question.ask,options};
}
/** The tray's pick as the task's answer: a choice by its ID, or the quiet Skip. Anything else isn't this question's. */
export function trayAnswer(question:WorkerQuestion,answer:QuestionAnswer):WorkerQuestionAnswer|null{
 const option=question.options[answer.option];
 if(option)return {kind:'option',optionId:option.id};
 return question.canSkip&&answer.option===question.options.length?{kind:'skip'}:null;
}
/** Your saved answer in words, for the tray's line while it waits to be confirmed. */
export function answerWords(question:WorkerQuestion,answer:WorkerQuestionAnswer):string{
 if(answer.kind==='skip')return t('Skip');
 if(answer.kind==='option')return question.options.find(option=>option.id===answer.optionId)?.label??t('your choice');
 return clip(answer.text,60);
}
const clip=(text:string,characters=120)=>{const chars=Array.from(text.trim());return chars.length>characters?`${chars.slice(0,characters-1).join('')}…`:chars.join('');};
/** One line for the thread's history: who asked what, and what you said. Delivery shows only when the answer didn't
 * simply reach the agent. */
export function questionLine(question:WorkerQuestion):string{
 const ask=clip(question.ask),asked=t('{asker} asked “{ask}”',{asker:question.asker,ask})+(/[?.!]$/.test(ask)?'':'.');
 if(question.state==='skipped')return t('{asked} You skipped it.',{asked});
 if(question.state==='stale')return t('{asked} It wasn’t answered.',{asked});
 const answer=question.answer;
 const said=answer?.kind==='option'?question.options.find(option=>option.id===answer.optionId)?.label??answer.text:answer?.text;
 const note=question.delivery==='not_sent'?t('It wasn’t sent to the agent.'):question.delivery==='cancelled'?t('Delivery was cancelled.'):question.delivery==='queued'?(question.carried?t('It waits to go in as the next message.'):t('It waits for the task to finish.')):'';
 return `${asked} ${said?t('You answered “{answer}”.',{answer:clip(said)}):t('You answered.')}${note?` ${note}`:''}`;
}
export function questionReceipt(question:WorkerQuestion):string{
 if(question.state==='skipped')return t('Question skipped. No new work was started.');
 if(question.state==='stale')return t('This question can’t be answered for its original task.');
 if(question.delivery==='unknown')return t('Answer saved. Delivery isn’t confirmed.');
 if(question.delivery==='queued')return t('Answer saved and queued.');
 if(question.delivery==='sent')return t('Answer sent to the task. This doesn’t confirm it was read.');
 if(question.delivery==='not_sent')return t('Answer saved. It wasn’t sent to the agent.');
 if(question.delivery==='cancelled')return t('Answer saved. Delivery was cancelled.');
 return t('Answer saved.');
}
/** Beta.16.1 (Bryce, 26 Sep: "i cant see the question only the answers"). An answer reaches the thread as the words
 * task_questions.answer_text sends: a header with the question's ID, the question, then "My answer:". The thread shows
 * your answer as your message with the question above it, quoted as the asker's, and no ID. Only a question on record
 * in this thread with that exact wording is shown that way; anything else stays the text it is. */
const ANSWER_HEAD=/^Answer to question ([A-Za-z0-9_-]{1,256}) (?:from this task|asked before its run ended):\n/;
export type AnsweredMessage={asker:string;ask:string;answer:string};
export function answeredMessage(text:string,questions:ReadonlyMap<string,{asker:string;ask:string}>):AnsweredMessage|null{
 const head=ANSWER_HEAD.exec(text);
 const question=head?questions.get(head[1]):undefined;
 if(!head||!question)return null;
 // i18n-ignore: the answer as it was sent to the agent, matched word for word
 const rest=text.slice(head[0].length),lead=`${question.ask}\n\nMy answer:\n`;
 if(!rest.startsWith(lead)||!rest.slice(lead.length).trim())return null;
 return {asker:question.asker,ask:question.ask,answer:rest.slice(lead.length)};
}
/** Every question on record in a thread's tasks, by ID, for answeredMessage. */
export function threadQuestions(tasks:readonly {questions?:readonly unknown[]}[]):Map<string,{asker:string;ask:string}>{
 const found=new Map<string,{asker:string;ask:string}>();
 for(const task of tasks)for(const raw of task.questions??[]){const question=readWorkerQuestion(raw);if(question)found.set(question.id,{asker:question.asker,ask:question.ask});}
 return found;
}
/** The row tag that carries an answered question to the message row (MessageRow draws it with AnsweredQuestion). */
export const QUESTION_TAG='dervo-question';
