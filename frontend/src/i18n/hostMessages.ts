import {t,type MessageKey} from './index';

/** The host's own words, in the window's language (beta.17, PLAN-beta17-20260928.md section 2, item 3). A refusal the host
 * sends (a Rejected's message) is plain English, and src/bridge.rs forwards only that text. So each message the window
 * shows often has a stable code here and its exact English: the window shows the translation for a known message and the
 * English as sent for anything else. Once the bridge passes a code too (evidence/b17-language/ASKS.md), `hostMessage`
 * takes it and the English stops mattering.
 *
 * The list is the refusals in host-errors.log on Bryce's Mac from 23 Sep to 2 Oct 2026, most frequent first, plus the
 * line every unexpected error gets. Each text is the host's English and the catalog key, so English shows exactly what the host said. */
type Known={code:string;text:MessageKey;match?:RegExp};
// i18n-keys: each text is the host's English, shown through t(text) by hostMessage
const KNOWN:Known[]=[
  {code:'conversation_other_account',text:'This conversation uses another account.'},
  {code:'conversation_too_large',text:'This conversation is too large to update at once. Its saved work is retained.'},
  {code:'file_missing',text:'That file couldn’t be found. It may have moved or been deleted.'},
  {code:'account_checking',text:'Dervo is checking your account. Try again in a moment.'},
  {code:'conversation_changed',text:'This conversation connection changed. Reload before continuing.'},
  {code:'review_needs_evidence',text:'Review requires current completed evidence and unchanged files'},
  {code:'beta_access_unconfirmed',text:'Dervo beta access needs confirmation. Open Account in Settings. Your saved work is still available.'},
  {code:'conversation_other_project',text:'Conversation does not belong to this project'},
  {code:'terminal_other_project',text:'This terminal doesn’t belong to the selected project or is no longer available.'},
  {code:'conversation_archived',text:'This conversation is archived. Restore it before opening or changing it.'},
  {code:'conversation_archived_pin',text:'This conversation is archived. Restore it before pinning it.'},
  {code:'project_archived',text:'This project is archived. Restore it before opening its records.'},
  {code:'coding_permission_stale',text:'Coding permission for this project is no longer current. Allow coding again before continuing this task.'},
  {code:'nothing_to_commit',text:'This task changed no files, so there is nothing to commit.'},
  {code:'still_running_settle',text:'A task or check is still running. Wait for it to finish before marking this stopped.'},
  {code:'attachment_too_long',text:'This file has more than 60 KB of text. Attach a smaller section so nothing is left out.'},
  {code:'draft_unavailable',text:'This conversation isn’t available for saving a draft.'},
  {code:'unknown_project',text:'Unknown project'},
  {code:'unknown_profile',text:'Unknown account profile'},
  {code:'project_unmarked',text:'This project isn’t marked Work or Personal, so no account can run it'},
  {code:'terminal_stopped',text:'This terminal has stopped. Open a new terminal to type commands.'},
  {code:'draft_too_large',text:'This draft is too large to save. Shorten it or copy it somewhere safe before closing Dervo.'},
  {code:'testers_admin_only',text:'Only Dervo admins can see testers.'},
  {code:'full_access_running',text:'Finish or stop the running task before changing full access.'},
  {code:'connectors_running',text:'Finish or stop the running task before changing its connectors.'},
  {code:'coding_text_limit',text:'A coding request needs text up to 32 KiB'},
  {code:'agent_conversation',text:'This is an agent\'s conversation. Remove the agent in Agents and review first.'},
  {code:'link_outside_project',text:'That file is outside this project. Open its project before following the link.'},
  {code:'organisation_changed',text:'Organisation changed. Refresh before saving or undoing.'},
  {code:'switch_needs_sign_in',text:'Finish Dervo sign-in before switching versions.'},
  {code:'unsupported_operation',text:'Unsupported local operation'},
  // onboarding_host._survey: the look is gone (the host restarted) or more than an hour old. The Threads screen offers
  // Scan this Mac again for these two (NEXT-UP #36), so it has to know them in any language (isHostMessage).
  {code:'look_gone',text:'Look at this Mac again. The last look isn’t available any more.'},
  {code:'look_stale',text:'That look is more than an hour old. Look at this Mac again before bringing anything in.'},
  {code:'find_task',text:'Open this thread’s Progress panel and find task {task}. Wait for this task to finish, or use Stop.',
    match:/^Open this thread’s Progress panel and find task (?<task>\S+)\. Wait for this task to finish, or use Stop\.$/},
  {code:'file_ambiguous',text:'More than one file in this project ends with {file}. Use its full path.',
    match:/^More than one file in this project ends with (?<file>.+)\. Use its full path\.$/},
  {code:'reviewer_gone',text:'Independent review: the reviewer agent @{agent} no longer exists',
    match:/^Independent review: the reviewer agent @(?<agent>\S+) no longer exists$/},
  {code:'home_text_limit',text:'Home reads up to {limit} characters at once. Split it into two messages.',
    match:/^Home reads up to (?<limit>\d+) characters at once\. Split it into two messages\.$/},
  {code:'file_held',text:'{path} already held {who}’s uncommitted change when this task started, and Dervo can’t separate the two. Commit {who} first, then this one. Nothing was committed.',
    match:/^(?<path>.+) already held (?<who>.+)’s uncommitted change when this task started, and Dervo can’t separate the two\. Commit \k<who> first, then this one\. Nothing was committed\.$/},
  {code:'unexpected',text:'Something went wrong in Dervo’s workspace{ref}. Try again, and send feedback if it repeats.',
    match:/^Something went wrong in Dervo’s workspace(?<ref> \(ref [^)]+\))?\. Try again, and send feedback if it repeats\.$/},
  // Beta.18: src/bridge.rs answers these itself, instead of killing the host after 10 s. {what} is the operation in
  // plain words ("task detail", "background work") and stays as the bridge sent it.
  {code:'host_busy_skipped',text:'Dervo is busy with {what}, so this wasn’t done. Try again in a moment.',
    match:/^Dervo is busy with (?<what>.+), so this wasn’t done\. Try again in a moment\.$/},
  {code:'host_busy_still',text:'Dervo is still busy with {what}. It may still finish, so check before you try it again.',
    match:/^Dervo is still busy with (?<what>.+)\. It may still finish, so check before you try it again\.$/},
  {code:'host_stopped',text:'Dervo’s local store stopped. It starts again with your next step. Your draft is retained.'},
  {code:'host_stuck',text:'Dervo’s local store stopped responding, so Dervo is restarting it. Your draft is retained.'},  // Beta.18 accounts (account_signin.py): a removed account, Remove while a run is live, and Add before the sign-in is done.
  {code:'account_removed',text:'That account was removed from Dervo. Pick another account. Nothing was sent.'},
  {code:'account_already_removed',text:'That account was already removed.'},
  {code:'account_task_running',text:'A task is running on it right now. Nothing was removed.'},
  {code:'account_message_running',text:'A message is running on it right now. Nothing was removed.'},
  {code:'signin_unfinished',text:'Finish signing in first. Nothing was added.'},
  {code:'signin_checking',text:'Dervo is still checking which account signed in. Try again in a moment.'},
  {code:'signin_gone',text:'That sign-in isn’t running any more. Start adding the account again.'},
];
const BY_CODE=new Map(KNOWN.map(known=>[known.code,known]));
const BY_TEXT=new Map(KNOWN.filter(known=>!known.match).map(known=>[known.text as string,known]));
const lookup=t as (key:MessageKey,vars?:Record<string,string>)=>string;

/** What the bridge says it was busy with is the operation's own name ("orchestrator send", "task detail", src/bridge.rs words()).
 * Said in plain words for the ones a person meets; any other name stays as it came. */
const WHAT:[RegExp,MessageKey][]=[
  [/^(orchestrator send|coding request|coordinate|task continue|task propose|task approve|task commit|task undo)$/,'your message'],
  [/^(orchestrator list|orchestrator arrive|project pickup)$/,'your threads'],
  [/^(conversation feed|conversation status|conversation older|conversation pickup|conversation seen|task detail|tasks|binding|thread folder|thread notes|image drafts|turn status|coding permission)$/,'this thread'],
  [/^background work$/,'background work'],
];
const plainWhat=(what:string)=>{const found=WHAT.find(([pattern])=>pattern.test(what));return found?lookup(found[1]):what;};

/** The host's message in the window's language: by its code when the host sent one, else by its exact English. A message
 * Dervo doesn't know shows as the host sent it. */
export function hostMessage(text:string,code?:string|null):string{
  const known=(code&&BY_CODE.get(code))||BY_TEXT.get(text);
  if(known&&!known.match)return lookup(known.text);
  for(const each of known?[known]:KNOWN){
    const found=each.match?.exec(text);
    if(found){
      const vars=Object.fromEntries(Object.entries(found.groups??{}).map(([name,value])=>[name,value??'']));
      if(each.code.startsWith('host_busy_')&&vars.what)vars.what=plainWhat(vars.what);
      return lookup(each.text,vars);
    }
  }
  return text;
}

/** For a host call's rejection: a string from the host is translated; anything else is left as it was thrown. */
export const hostFailure=(failure:unknown)=>typeof failure==='string'?hostMessage(failure):failure;

/** A message with names in it, as a pattern: the window's language's template with each ``{name}`` as a wildcard. */
const patternOf=(template:string)=>new RegExp('^'+template.split(/\{\w+\}/).map(part=>part.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('(.+)')+'$');

/** Whether a failure a host call threw is the known message ``code``, as sent or as shown in the window's language. A
 * message with names in it ("Dervo is still busy with {what}.") matches with any names, in English as sent or in the
 * window's language, so callers classify by code and not by the English words. */
export function isHostMessage(failure:unknown,code:string):boolean{
  const known=BY_CODE.get(code);
  const text=failure instanceof Error?failure.message:String(failure);
  if(!known)return false;
  if(!known.match)return text===known.text||text===lookup(known.text);
  return known.match.test(text)||patternOf(lookup(known.text)).test(text);
}

/** Tests only: the codes Dervo knows. */
export const knownCodes=()=>KNOWN.map(known=>known.code);
