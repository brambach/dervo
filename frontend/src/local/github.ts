/* GitHub from a thread (beta.16, b16/git): the host's five GitHub operations and the words the screens build from them.
 * The operation names and fields are b16/bridge's (backend/shared_operations.py); the host's handler is
 * backend/github_host.py, and these types are what it returns. Everything said here is a record field or GitHub's
 * answer as Dervo last saw it, with when; reviewers' words arrive quoted and stay quoted. */
import {local} from './api';
import {clock} from './runHistory';
import {formatList,t} from '@/i18n';

export type GhState='ready'|'missing'|'signed_out'|'too_old'|'unknown';
export type GitHubStatus={state:GhState;host:string;login:string|null;version:string|null;detail:string;installCommand:string|null;signInCommand:string|null;
 checkedAt:number|null;repo?:string|null;installPage?:string};
export type PrMode='thread'|'branch';
export type PrCommit={sha:string;short:string;subject:string;channelId:string|null;thread:string|null;outside:boolean};
export type PrPreview={id:string|null;mode:PrMode;repo:string|null;host:string|null;account:string|null;base:string|null;head:string|null;title:string;draft:boolean;
 commits:PrCommit[];pushNeeded:boolean;fetched:boolean;available:boolean;reason:string|null;branch:string|null;dependsOn:PrCommit[];others:number;outside:number;
 previousAccount:string|null;body?:string|null;ghState?:GhState;signIn?:string|null;install?:string|null};
export type PrReviewComment={author:string;text:string;path:string|null;line:number|null;at:number|null};
export type PrChecks={passing:number;failing:number;pending:number};
export type PullRequest={number:number;url:string|null;repo:string|null;base:string|null;head:string|null;draft:boolean;state:'open'|'closed'|'merged';
 review:'approved'|'changes_requested'|'review_required'|null;checks:PrChecks|null;comments:PrReviewComment[];
 reviewers?:{changes?:string[];approved?:string[];requested?:string[]};checkedAt:number|null;checkedAs:string|null;mode?:PrMode;checkError?:string|null};
export type PrAttempt={id:string;state:'running'|'opened'|'failed'|'unknown'|'draft_refused';step:string|null;detail:string|null;head:string|null;repo:string|null;
 base:string|null;draft:boolean|null;mode:PrMode|null;startedAt:number|null;finishedAt:number|null;dependsOn:PrCommit[]};
export type PrStatus={pr:PullRequest|null;attempt:PrAttempt|null;pullOffer:{command:string;count:number;base:string}|null};
/** What a branch of only this thread's commits would hold, or the commits it depends on that aren't on GitHub. Writes nothing. */
export type ThreadBranchPlan={branch:string|null;base:string|null;commits:PrCommit[];dependsOn:PrCommit[];available:boolean;reason:string|null};

export const githubStatus=(projectId:string,recheck?:boolean)=>local<GitHubStatus>('github_status',recheck===undefined?{projectId}:{projectId,recheck});
export const prPreview=(projectId:string,channelId:string,mode:PrMode)=>local<PrPreview>('pr_preview',{projectId,channelId,mode});
export const prCreate=(projectId:string,channelId:string,previewId:string,draft:boolean)=>local<PrAttempt>('pr_create',{projectId,channelId,previewId,draft});
export const prStatus=(projectId:string,channelId:string,refresh?:boolean)=>local<PrStatus>('pr_status',refresh===undefined?{projectId,channelId}:{projectId,channelId,refresh});
export const prThreadBranch=(projectId:string,channelId:string)=>local<ThreadBranchPlan>('pr_thread_branch',{projectId,channelId});


/** "Draft pull request #215", "Pull request #209, merged". */
export function prTitle(pr:Pick<PullRequest,'number'|'draft'|'state'>):string{
 if(pr.state==='merged')return t('Pull request #{number}, merged',{number:pr.number});
 if(pr.state==='closed')return t('Pull request #{number}, closed',{number:pr.number});
 return `${pr.draft?t('Draft pull request'):t('Pull request')} #${pr.number}`;
}
/** The chip on a History card: "PR #214 · Draft · Changes requested", "PR #209 · Merged". */
export function prChip(pr:Pick<PullRequest,'number'|'draft'|'state'|'review'>):string{
 const parts=[`PR #${pr.number}`];
 if(pr.state==='merged')parts.push(t('Merged'));
 else if(pr.state==='closed')parts.push(t('Closed'));
 else{if(pr.draft)parts.push(t('Draft'));const review=reviewWord(pr.review);if(review)parts.push(review);}
 return parts.join(' · ');
}
export function reviewWord(review:PullRequest['review']):string{
 return review==='changes_requested'?t('Changes requested'):review==='approved'?t('Approved'):review==='review_required'?t('Review requested'):'';
}
/** "2 of 2 passing", "1 failing of 3", "Running, 1 of 3 done". Null when GitHub reported none. */
export function checksWords(checks:PrChecks|null|undefined):string|null{
 if(!checks)return null;
 const total=checks.passing+checks.failing+checks.pending;
 if(!total)return t('None on GitHub');
 if(checks.failing)return t('{failing} failing, {passing} of {total} passing',{failing:checks.failing,passing:checks.passing,total});
 if(checks.pending)return t('{pending} still running, {passing} of {total} passing',{pending:checks.pending,passing:checks.passing,total});
 return t('{passing} of {total} passing',{passing:checks.passing,total});
}
/** Who asked for what, from GitHub's reviews: "Changes requested by ana-l." */
export function decisionWords(pr:Pick<PullRequest,'review'|'reviewers'|'state'>):string|null{

 if(pr.state==='merged')return t('Merged.');
 const by=(names:string[]|undefined)=>names?.length?names.join(', '):'';
 if(pr.review==='changes_requested')return by(pr.reviewers?.changes)?t('Changes requested by {names}.',{names:by(pr.reviewers?.changes)}):t('Changes requested.');
 if(pr.review==='approved')return by(pr.reviewers?.approved)?t('Approved by {names}.',{names:by(pr.reviewers?.approved)}):t('Approved.');
 if(pr.review==='review_required')return by(pr.reviewers?.requested)?t('Waiting for review by {names}.',{names:by(pr.reviewers?.requested)}):t('Waiting for review.');
 return null;
}
/** When Dervo last read it and as whom, or that it couldn't: never a guess about now. */
export function checkedLine(pr:Pick<PullRequest,'checkedAt'|'checkedAs'|'checkError'>):string{
 if(pr.checkError)return pr.checkedAt?t('Not checked since {time}. {checkError}',{time:clock(pr.checkedAt),checkError:pr.checkError}):t('Not checked. {checkError}',{checkError:pr.checkError});
 if(!pr.checkedAt)return t('Not checked on GitHub yet.');
 return pr.checkedAs?t('Checked on GitHub at {time}, as {who}.',{time:clock(pr.checkedAt),who:pr.checkedAs}):t('Checked on GitHub at {time}.',{time:clock(pr.checkedAt)});
}
/** The second choice's line: what else is on the branch, counted by where it came from. */
export function othersLine(preview:Pick<PrPreview,'commits'|'others'|'outside'>,channelId:string):string{
 const names=[...new Set(preview.commits.filter(c=>!c.outside&&c.channelId!==channelId&&c.thread).map(c=>c.thread as string))];
 const parts=[];
 if(preview.others)parts.push(t('{count} commits from {where}',{count:preview.others,where:names.length?formatList(names):t('other threads')}));
 if(preview.outside)parts.push(t('{outside} made outside Dervo',{outside:preview.outside}));
 return parts.length?t('Also {parts}.',{parts:parts.join(`, ${t('and')} `)}):t('Only this thread’s commits are on it.');
}
/** What the one click does on GitHub, named before it's clicked. */
export function clickWords(preview:Pick<PrPreview,'mode'|'head'|'pushNeeded'|'base'>):string{
 const head=preview.head??t('the branch');
 if(preview.mode==='thread')return t('This fetches {base} from GitHub to build {head}, pushes {head} to GitHub, then opens the pull request. Both show on GitHub straight away. Nothing is merged.',{base:preview.base??t('the default branch'),head});
 if(preview.pushNeeded)return t('This pushes {head} to GitHub, then opens the pull request. Both show on GitHub straight away. Nothing is merged.',{head});
 return t('{head} is on GitHub already. This opens the pull request, which shows on GitHub straight away. Nothing is merged.',{head});
}
/** What the attempt is doing or how it ended, in a sentence. */
export function attemptWords(attempt:PrAttempt):string{
 if(attempt.detail)return attempt.detail;
 return attempt.state==='running'?t('Working on it.'):attempt.state==='unknown'?t('Dervo doesn’t know how this ended.'):'';
}
