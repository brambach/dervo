use serde_json::{json, Value};
use std::{io::{BufRead,BufReader,Write}, path::PathBuf, process::{Child,ChildStdin}, sync::{mpsc,Arc,Mutex}, time::{Duration,Instant}};

pub const REPLY_LIMIT: usize = 64*1024*1024;
const ERROR_LOG_LIMIT: u64 = 2*1024*1024;

fn oversized(bytes: usize, id: Option<u64>) -> String {
    let error=format!("That reply was too large to show ({} MB). Nothing was lost.", bytes/1_000_000);
    match id { Some(id) => format!("{{\"id\": {id}, \"error\": {}}}", json!(error)), None => json!({"error":error}).to_string() }
}

/// `host-errors.log` in the data directory, emptied first when it has grown past 2 MB.
fn error_log(data: &std::path::Path) -> Option<std::fs::File> {
    let path=data.join("host-errors.log");
    let truncate=std::fs::metadata(&path).map(|m| m.len()>ERROR_LOG_LIMIT).unwrap_or(false);
    std::fs::OpenOptions::new().create(true).append(!truncate).write(true).truncate(truncate).open(path).ok()
}

/// The id a reply starts with (`{"id": 7, ...}`, backend/host.py serve_one), read without parsing the rest, so an
/// oversized reply keeps its id too. None for a reply sent without one.
fn leading_id(line: &str) -> Option<u64> {
    let rest=line.strip_prefix("{\"id\":")?.trim_start();
    let digits=rest.find(|c:char|!c.is_ascii_digit()).map_or(rest,|end|&rest[..end]);
    digits.parse().ok()
}

/// What the host's heartbeat last said (backend/host_heartbeat.py) and when it arrived.
#[derive(Clone, Default)]
struct Beat { at: Option<Instant>, operation: Option<String>, seconds: f64 }

fn heartbeat(line: &str) -> Option<Beat> {
    if !line.starts_with("{\"heartbeat\"") {return None}
    let value:Value=serde_json::from_str(line).ok()?;
    let beat=value.get("heartbeat")?;
    Some(Beat {at: Some(Instant::now()), operation: beat["operation"].as_str().map(str::to_string), seconds: beat["seconds"].as_f64().unwrap_or(0.0)})
}

/// A host push line (backend/thread_states.py): `{"event": {"kind": "threads", "version": 12}}`. Its `event` value, or
/// None for any other line.
fn host_event(line: &str) -> Option<Value> {
    if !line.starts_with("{\"event\"") {return None}
    let value:Value=serde_json::from_str(line).ok()?;
    value.get("event").filter(|event|event.is_object()).cloned()
}

/// Where host push lines go: the app sets this once to emit them to the window (main.rs). Unset, they're dropped.
static EVENTS: std::sync::OnceLock<Box<dyn Fn(Value)+Send+Sync>> = std::sync::OnceLock::new();
pub fn on_host_event(forward: impl Fn(Value)+Send+Sync+'static) { let _=EVENTS.set(Box::new(forward)); }

/// Replies go to the caller; heartbeats only update `beat`; host push lines go to the window (EVENTS), never to a
/// caller waiting for a reply, which would read one as its answer. The channel is unbounded so a late reply nobody is waiting
/// for never stops the heartbeat being read.
fn pump<R: BufRead>(reader: R, tx: mpsc::Sender<String>, beat: Arc<Mutex<Beat>>, limit: usize) {
    for line in reader.lines() {
        let line=match line {
            Ok(s) if s.len()<=limit => s,
            Ok(s) => oversized(s.len(), leading_id(&s)),
            Err(_)=>break,
        };
        if let Some(fresh)=heartbeat(&line) {
            if let Ok(mut beat)=beat.lock() {*beat=fresh;}
            continue;
        }
        if let Some(event)=host_event(&line) {
            if let Some(forward)=EVENTS.get() {forward(event);}
            continue;
        }
        if tx.send(line).is_err(){break}
    }
}

/// How long the window waits before it answers "busy", and when it gives up on a host.
#[derive(Clone, Copy)]
struct Limits {
    /// A request the host hasn't answered by now is answered "busy" for the window. The host is kept.
    answer: Duration,
    /// A request that comes in while an earlier one is still unanswered waits this long for it, then is answered
    /// "busy" without being sent, so requests never pile up behind a slow one.
    queued: Duration,
    /// A host with no heartbeat for this long (or since launch) is taken to be stuck and is ended.
    stall: Duration,
}
const LIMITS: Limits = Limits {answer: Duration::from_secs(10), queued: Duration::from_secs(2), stall: Duration::from_secs(30)};
/// A new host's first request waits longer: it reconciles the last host's runs before it reads requests.
pub const STARTUP_ANSWER: Duration = Duration::from_secs(30);

/// A request the host was sent and hasn't answered yet.
struct Pending { id: u64, operation: String, sent: Instant, turned_away: u32 }

enum Wait { Reply(String), Timeout, Closed }

pub const STOPPED: &str = "Dervo’s local store stopped. It starts again with your next step. Your draft is retained.";
pub const STUCK: &str = "Dervo’s local store stopped responding, so Dervo is restarting it. Your draft is retained.";

/// An operation as words for the busy line and the log: `task_detail` is "task detail", a tick is "background work".
fn words(operation: &str) -> String {
    match operation { "tick" => "background work".into(), "" | "request" => "an earlier request".into(), name => name.trim_start_matches('_').replace('_'," ") }
}
/// This request was sent and is still going. It may still finish, so the window shouldn't send it again blindly.
pub fn busy_still(what: &str) -> String { format!("Dervo is still busy with {what}. It may still finish, so check before you try it again.") }
/// This request wasn't sent: the host is still on an earlier one.
pub fn busy_skipped(what: &str) -> String { format!("Dervo is busy with {what}, so this wasn’t done. Try again in a moment.") }

/// Local date and time, the same shape as backend/host.py's clock(), so the bridge's lines sort with the host's own.
#[cfg(unix)]
fn clock() -> String {
    // struct tm on 64-bit macOS and Linux, with room to spare after it.
    #[repr(C)] struct Tm { sec: i32, min: i32, hour: i32, mday: i32, mon: i32, year: i32, wday: i32, yday: i32, isdst: i32, gmtoff: i64, zone: *const std::ffi::c_char, spare: [u8; 32] }
    extern "C" { fn localtime_r(time: *const i64, out: *mut Tm) -> *mut Tm; }
    let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d|d.as_secs() as i64).unwrap_or(0);
    let mut tm=Tm {sec:0,min:0,hour:0,mday:0,mon:0,year:0,wday:0,yday:0,isdst:0,gmtoff:0,zone:std::ptr::null(),spare:[0;32]};
    // SAFETY: localtime_r writes one struct tm into `tm`, which is at least that size, and keeps no pointer to it.
    if unsafe {localtime_r(&now,&mut tm)}.is_null() {return format!("@{now}")}
    format!("{:04}-{:02}-{:02} {:02}:{:02}:{:02}",tm.year+1900,tm.mon+1,tm.mday,tm.hour,tm.min,tm.sec)
}
/// Not checked on Windows yet: UTC, marked as such, rather than a local time that might be wrong.
#[cfg(not(unix))]
fn clock() -> String {
    let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d|d.as_secs()).unwrap_or(0);
    let (days,rest)=(now/86400,now%86400);
    // Civil date from days since 1970 (Howard Hinnant's algorithm).
    let z=days as i64+719468; let era=z.div_euclid(146097); let doe=z-era*146097;
    let yoe=(doe-doe/1460+doe/36524-doe/146096)/365; let doy=doe-(365*yoe+yoe/4-yoe/100);
    let mp=(5*doy+2)/153; let day=doy-(153*mp+2)/5+1; let month=if mp<10 {mp+3} else {mp-9};
    let year=yoe+era*400+i64::from(month<=2);
    format!("{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02} UTC",rest/3600,rest%3600/60,rest%60)
}

pub struct Worker {
    child: Child, input: ChildStdin, output: mpsc::Receiver<String>, beat: Arc<Mutex<Beat>>,
    data: PathBuf, launched: Instant, next_id: u64, pending: Option<Pending>, limits: Limits,
}
impl Worker {
    /// `python` and `data` come from crate::launch resolution; the host gets only the script and data path.
    pub fn launch(python: &std::path::Path, script: &std::path::Path, data: &std::path::Path) -> Result<Self,String> {
        let mut command = crate::launch::host_command(python, script, data);
        let keychain = crate::keychain::configure(&mut command, python, script)?;
        // The host's own errors go to a local file so a stopped host leaves a trace. Best effort: no file, no log.
        if let Some(log)=error_log(data) {command.stderr(log);}
        let mut child = command
            .spawn().map_err(|e|format!("Local Python runtime {} couldn't start: {e}", python.display()))?;
        if let Some(endpoint) = keychain { endpoint.serve(); }
        let input=child.stdin.take().ok_or("Missing child stdin")?;
        let stdout=child.stdout.take().ok_or("Missing child stdout")?;
        let (tx,output)=mpsc::channel();
        let beat=Arc::new(Mutex::new(Beat::default()));
        let beats=beat.clone();
        // An oversized reply is answered with an error for that one request. Dropping it silently used to leave the
        // caller waiting 10 s, then kill the host (19 Sep: a 9.1 MB task reply, "Broken pipe" from then on).
        std::thread::spawn(move || pump(BufReader::new(stdout), tx, beats, REPLY_LIMIT));
        Ok(Self {child,input,output,beat,data:data.to_path_buf(),launched:Instant::now(),next_id:0,pending:None,limits:LIMITS})
    }
    /// False once the host process has exited, however it ended.
    pub fn alive(&mut self) -> bool { matches!(self.child.try_wait(), Ok(None)) }
    /// How the host ended, for the log: "exit status: 1", "signal: 9 (SIGKILL)", or "still running".
    pub fn ended(&mut self) -> String {
        match self.child.try_wait() { Ok(Some(status)) => status.to_string(), Ok(None) => "still running".into(), Err(e) => format!("unknown ({e})") }
    }
    /// One line in host-errors.log, beside the host's own. Best effort: no file, no line.
    pub fn note(&self, line: &str) {
        if let Some(mut log)=error_log(&self.data) { let _=writeln!(log,"{} {line}",clock()); }
    }
    /// How long the host has gone without a heartbeat (since launch, before its first), when that's past the limit.
    fn stalled(&self) -> Option<Duration> {
        let last=self.beat.lock().ok().and_then(|beat|beat.at).unwrap_or(self.launched);
        Some(last.elapsed()).filter(|quiet|*quiet>=self.limits.stall)
    }
    /// What the host is on now, from its heartbeat, else the request it was last sent.
    fn doing(&self) -> (String, Option<f64>) {
        let beat=self.beat.lock().map(|beat|beat.clone()).unwrap_or_default();
        match beat.operation { Some(operation) => (operation, Some(beat.seconds)), None => (self.pending.as_ref().map(|p|p.operation.clone()).unwrap_or_default(), None) }
    }
    fn wait_for(&mut self, id: u64, until: Instant) -> Wait {
        loop {
            let left=until.saturating_duration_since(Instant::now());
            match self.output.recv_timeout(left) {
                // A reply without an id is the host's startup refusal, sent before it reads ids; it answers what's waiting.
                Ok(line) => match leading_id(&line) {
                    Some(other) if other!=id => self.note(&format!("Dropped a reply to request {other}, which nothing was waiting for.")),
                    _ => return Wait::Reply(line),
                },
                Err(mpsc::RecvTimeoutError::Timeout) => return Wait::Timeout,
                Err(mpsc::RecvTimeoutError::Disconnected) => return Wait::Closed,
            }
        }
    }
    /// The host closed its output or exited: say how, and make sure it's gone so the next request starts a new one.
    fn lost(&mut self, operation: &str) -> String {
        // Closed output usually means the process is exiting; give it a moment so the log says how it ended.
        let settle=Instant::now()+Duration::from_secs(1);
        while self.alive() && Instant::now()<settle { std::thread::sleep(Duration::from_millis(20)); }
        let ended=self.ended();
        self.note(&format!("The host stopped while Dervo was waiting on a {operation} request ({ended}). The next request starts a new one."));
        if self.alive() { let _=self.child.kill(); let _=self.child.wait(); }
        STOPPED.into()
    }
    /// No answer in time. The host is ended only when it has exited or its heartbeat has stopped; otherwise it's busy.
    fn slow(&mut self, operation: &str, sent: bool) -> String {
        if !self.alive() {return self.lost(operation)}
        let (doing,seconds)=self.doing();
        let on=match seconds { Some(seconds) => format!("{doing} for {seconds:.0} s"), None => if doing.is_empty() {"nothing it said".into()} else {doing.clone()} };
        if let Some(quiet)=self.stalled() {
            self.note(&format!("Ended the host: no heartbeat for {} s while a {operation} request waited (last on {on}). The next request starts a new one.", quiet.as_secs()));
            let _=self.child.kill(); let _=self.child.wait();
            return STUCK.into();
        }
        let what=words(if doing.is_empty() {operation} else {&doing});
        if sent {
            self.note(&format!("A {operation} request had no answer after {} s. Answered busy and kept the host (it's on {on}).", self.limits.answer.as_secs_f64()));
            busy_still(&what)
        } else {
            busy_skipped(&what)
        }
    }
    pub fn call(&mut self, operation: &str, args: Value) -> Result<Value,String> { self.call_within(operation,args,self.limits.answer) }
    /// `answer` is how long to wait before answering busy: LIMITS.answer, or STARTUP_ANSWER for a new host's first request.
    pub fn call_within(&mut self, operation: &str, args: Value, answer: Duration) -> Result<Value,String> {
        // An earlier request still unanswered goes first. Its late reply is dropped, never read as this one's answer.
        if let Some(pending)=self.pending.as_ref() {
            let id=pending.id;
            match self.wait_for(id,Instant::now()+self.limits.queued) {
                Wait::Reply(_) => {
                    let p=self.pending.take().expect("pending");
                    let away=match p.turned_away { 0 => String::new(), 1 => " 1 later request was answered busy meanwhile.".into(), n => format!(" {n} later requests were answered busy meanwhile.") };
                    self.note(&format!("The late reply to a {} request (sent {} s earlier) was dropped.{away}",p.operation,p.sent.elapsed().as_secs()));
                }
                Wait::Timeout => {
                    let error=self.slow(operation,false);
                    let first=self.pending.as_mut().map(|p|{p.turned_away+=1; (p.turned_away==1).then(||p.operation.clone())});
                    if let (Some(Some(earlier)),true)=(first,error!=STUCK && error!=STOPPED) {
                        self.note(&format!("A {operation} request wasn't sent: the host is still on an earlier {earlier} request. Answering busy until it's done."));
                    }
                    return Err(error);
                }
                Wait::Closed => return Err(self.lost(operation)),
            }
        }
        self.next_id+=1;
        let id=self.next_id;
        let request=serde_json::to_string(&json!({"id":id,"operation":operation,"args":args})).map_err(|e|e.to_string())?;
        if request.len()>131072 {return Err("Request too large".into())}
        writeln!(self.input,"{request}").and_then(|_|self.input.flush()).map_err(|e|e.to_string())?;
        self.pending=Some(Pending {id,operation:operation.into(),sent:Instant::now(),turned_away:0});
        let line=match self.wait_for(id,Instant::now()+answer) {
            Wait::Reply(line) => {self.pending=None; line}
            Wait::Timeout => return Err(self.slow(operation,true)),
            Wait::Closed => return Err(self.lost(operation)),
        };
        let value:Value=serde_json::from_str(&line).map_err(|e|e.to_string())?;
        if let Some(error)=value.get("error") {return Err(error.as_str().unwrap_or("Store rejected request").into())}
        Ok(value["result"].clone())
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        // Let the Python host close its owned provider groups before exiting.
        let _=writeln!(self.input,"{}",json!({"operation":"_shutdown","args":{}}));
        let _=self.input.flush();
        let deadline=std::time::Instant::now()+Duration::from_secs(4);
        while std::time::Instant::now()<deadline {
            if matches!(self.child.try_wait(),Ok(Some(_))) {return}
            std::thread::sleep(Duration::from_millis(25));
        }
        let _=self.child.kill();
        let _=self.child.wait();
    }
}
pub use crate::startup::Host;

#[derive(serde::Deserialize)]
#[serde(rename_all="snake_case")]
pub enum Operation { TaskQuestionAnswer, TerminalOpen, TerminalList, TerminalRead, TerminalWrite, TerminalResize, TerminalClose, BetaPlan, BetaFeedbackContext, BetaFeedbackSend, BetaFeedbackStatus, BetaUpdateStatus, BetaUpdateCheck, BetaUpdateCancel, BetaUpdateFetch, ImageCancel, ImageBegin, ImageChunk, ImageFinish, ImageDiscard, ImageGet, ImageThumbPut, ImageThumb, ImageDrafts, KeepAwakeStatus, KeepAwakeSet, OpenrouterModelCheck, OpenrouterRouteSave, OpenrouterBudgetSave, OpenrouterStatus, OpenrouterConnect, OpenrouterCheck, OpenrouterCancel, OpenrouterDisconnect, DraftsList, DraftSave, PreviewStatus, PreviewStart, PreviewCancel, PreviewOpen, PreviewFolder, PreviewStorageFolder, ProjectExplore, ProjectRename, ProjectSetupSave, FirstRunStatus, FirstRunClaude, FirstRunInspect, FirstRunFinish, ProjectModelSet, ProjectProfileSet, ProjectReveal, ProjectFileReveal, ProfileFableAck, ConversationModelSet, ConversationProfileSet, LifecycleBlockers,ProjectsArchived, ProjectArchive, ProjectRestore, ProjectDelete, ConversationsArchived, ConversationArchive, ConversationRestore, ConversationDelete, ConversationsArchive, ConversationsDeleteArchived, DervoAccountStatus, DervoAccountSendCode, DervoAccountVerifyCode, DervoAccountSaveProfile, DervoAccountPhoto, DervoAccountSavePhoto, DervoAccountRemovePhoto, DervoAccountStart, DervoAccountPoll, DervoAccountCancel, DervoAccountRestore, DervoAccountSignOut, OrganisationGet, OrganisationSet, OrganisationUndo, KnowledgeList, KnowledgeSave, KnowledgeApprove, KnowledgeArchive, KnowledgeAdopt, KnowledgeLessonsSet, KnowledgeLessonKeep, KnowledgeLessonDismiss, KnowledgeLessonList, IdeasList, IdeasSave, IdeasArchive, IdeasRestore, IdeasAssign, IdeasRoute, IdeasItemAdd, IdeasItemNote, IdeasItemSet, IdeasItemWork, IdeasItemReopen, IdeasItemList, IdeasProductList, IdeasItemProduct, PushPreview, PushStart, PushStatus, PushRecount, OrchestratorList, OrchestratorArrive, OrchestratorSend, OrchestratorResult, OrchestratorUndo, OrchestratorOffer, OrchestratorMove, OrchestratorHomeModel, ProjectPickup, ConversationPickup, SkillsList, SkillsSave, SkillsReview, SkillsArchive, SkillsRestore, SkillsImport, SkillsImportText, SkillsExport, WorkspaceSearch, WorkspaceSource, ProjectCatchup, Status, Profiles, Projects, AddProfile, ProfileKeyAdd, ProfileKeyRemove, ProfileLimitSet, ProfileRename, AccountSigninStart, AccountSigninAdd, AccountSigninCancel, AccountUseSignedIn, AccountRemove, ClaudeVersion, DervoTesters, DervoTestersOlder, DervoTesterSet, DervoWaitlist, DervoWaitlistInvite, CreateConversation, Conversations, ConversationSeen, ConversationDismiss, UsageSummary, SessionSummary, ConversationStatus, ConversationFeed, ConversationOlder, Binding, Queue, History, Cancel, SharedContext, ConnectionInstructions, ConnectionCheck, ConnectionStatus, ConnectionLogin, ConnectionLoginStatus, ConnectionLoginCancel, ConnectionLoginCode, StartTurn, StopTurn, TurnStatus, ReconcileTurn, ModelCheck, ModelStatus, Tasks, TaskCreate, TaskApprove, TaskDeny, TaskDetail, TaskReview, TaskContinue, TaskStop, TaskReconcile, TaskSettle,TaskThreadProposals,TaskThreadStart,TaskThreadDismiss, TaskCommit, TaskUndo, TaskRestoreFile, TaskPropose, Proposals, ProposalAccept, ProposalDismiss, HarnessRequest, HarnessStatus, HarnessCancel, HarnessEvents, HarnessBoundary, HarnessRequests, HarnessActions, HarnessResolve, CodingPermission, CodingPermissionGrant, CodingPermissionRevoke, CodingRequest, CodingRequests, CodingEffortSet, CodingAcceptanceSet, CodingQueueCancel, CodingWarm, CodingRouteSet, ConversationRouteSet, CodingRetryOn, CodingWaitForReset, CodingFullAccessSet, CodingComputerAccessSet, CodingConnectorsSet, CodingFreshStartSet, ProjectMemorySet, ProjectMemoryReflectSet, ProjectMemoryRemove, CollabAgents, CollabAgentSave, CollabAgentDelete, CollabPolicy, CollabPolicySet, CollabReviewerSetup, CollabPackets, CollabPacket, CreateAgentConversation, ThreadStates, ThreadOrder, RoutingOutcome, CodingChecksSet,
    // Knowledge (design sweep, 19 Sep): undo, dismissing a rule version, and the repository's instruction files.
    KnowledgeLessonReopen, KnowledgeVersionDismiss, KnowledgeNoteRestore, KnowledgeRepoList, KnowledgeRepoSend, KnowledgeRepoOpen,
    // Knowledge (beta.11, fix-memory F1): agents' notes and the memory switches, read and set with no conversation
    KnowledgeNotesList, KnowledgeNotesSet, KnowledgeNotesReflectSet, KnowledgeNoteRemove,
    KnowledgeNoteEdit, KnowledgeNoteKeep, KnowledgeNotesSeen, KnowledgeNotePairKeep,
    // Knowledge (beta.17, dd #22): pin a note so it's never retired and goes with every request
    KnowledgeNotePin,
    // Knowledge (beta.17.1, #41 step 8): move agent-made rules to notes, or drop them, in one batch with one Undo
    KnowledgeRuleToNote,
    // Design sweep, surfaces (19 Sep): the project picker's per-project counts. Read only.
    ProjectsActivity,
    // Usage events (sweep/analytics, beta.9): the renderer's two events, the Preferences switch, the one-time notice and
    // the Testers page's admin report. The host drops anything off its allowlist.
    UsageEvent, UsageSharing, UsageSharingSet, UsageNoticeSeen, UsageReport, UsagePeople,
    // Error reports (beta.11): the renderer's crash screen reports an error's type and screen, and the Testers page's
    // Errors section reads grouped counts. The host drops anything off its allowlist.
    ErrorEvent, ErrorReport,
    // Blank-project import (21 Sep): what a project folder's Claude Code and Codex history holds, and the read that
    // makes conversations and workstreams from it. Both per project; the host reads the homes and never writes them.
    HistoryLook, HistoryPreview, HistoryRead,
    // Whole-Mac onboarding (beta.15 group 10): the metadata look, the threads of accepted folders, one batch in and its undo.
    OnboardingSources, OnboardingLook, OnboardingThreads, OnboardingBringIn, OnboardingBatch, OnboardingUndo, OnboardingWaiting, OnboardingBringWaiting,
    // Agent history (23 Sep): the History list, one run's record, a file's trace and one line's. Read only.
    HistoryList, RunRecord, TraceFile, TraceLine,
    // Outside Dervo (23 Sep): Catch up's read-only look at this folder's Claude Code and Codex sessions, and its per-project switch.
    OutsideSessions, OutsideSessionsConsent, OutsideSessionsConsentSet,
    // The handoff that carries the work (23 Sep): Continue's confirm, which saves a snapshot and shows the brief, and the
    // thread's Restore with its own confirm. The host checks every one against the thread and the stopped task.
    CodingHandoffPreview, CodingHandoffRestorePreview, CodingHandoffRestore,
    // Replies that do the next step (beta.15, group 1): open a project document with its default app, read a Markdown
    // file for Preview, list previewable files git reports since a thread's first run, and the per-project switch for
    // agents running commands. The host checks every path is inside the project; nothing here runs a file.
    ProjectFileOpen, ProjectFileText, PreviewChanges, CodingCommandsSet, CodingAutoCommitSet,
    // Beta.17.1: a project's run limits (silence, run length, attempts), each one of the host's own choices.
    CodingRunLimitsSet,
    // Beta.15, groups 7 and 9: the icon picker's list (which icons this account may use), and the update prompt's notice,
    // its Later and its one-time "You're on" message. None of them downloads, installs or names a path.
    AppIconStatus, BetaUpdateNotice, BetaUpdateLater, BetaUpdateSeen,
    // Beta.18: a thread's own branch and folder (backend/thread_folders.py). The chip's read and flip before the first run,
    // the setup card's Try again, Install (only after the click, with the command shown) and Go on without, Merge (your
    // click, a merge commit on the base, never a push), Reveal in Finder, and the project's approved ignored files.
    ThreadFolder, ThreadFolderChoose, ThreadFolderRetry, ThreadFolderInstall, ThreadFolderSkipInstall, ThreadFolderMerge, ThreadFolderReveal,
    ThreadFolderSettings, ThreadFolderIgnoredSet,
    // Beta.16's shared operations (backend/shared_operations.py). Idle threads, Next up and tester reports for
    // b16/lifecycle, under its names (its project_lifecycle.FIELDS checks their fields); GitHub status and pull requests
    // for b16/git, through the user's own gh, checked by shared_operations. Opening a pull request takes the id of the
    // confirm the user saw. Nothing here merges, force-pushes or runs a command.
    ConversationsTidy, ConversationsTidied, ConversationsTidyUndo, ConversationPin, ConversationPins, ProjectTidySetting, ProjectTidySet,
    NextUpRead, NextUpAdd, NextUpMove, NextUpShip, NextUpWork, NextUpReportsHere, TesterReports, TesterReportMark, TesterReportAdd,
    GithubStatus, PrPreview, PrCreate, PrStatus, PrThreadBranch,
    // Beta.16.1: an agent's reminders due today (task_reminders.py), for Home and the notification, and marking one
    // notified or done. Words to show only: none of them starts work, sends a message or grants access.
    // 9 Oct: your own reminder for a thread (Home's "Remind me tomorrow"), and Home's Suggestions, the thread proposals
    // agents made that nobody started (thread_fanout.py). Reading or saving these starts nothing either.
    Reminders, RemindersDue, ReminderNotified, ReminderDone, ReminderAdd, HomeSuggestions,
    // Beta.17: notes between threads and a thread's review of its finished subthreads (backend/thread_notes.py). Reading
    // them, putting one away, and the project's automatic-review setting change nothing else. Passing one on starts a run
    // in that thread with the note quoted, as from its sender, and never with more access than the run that left it.
    // Beta.17.1: the project's setting for notes passing on by themselves. It only changes what the host does with a
    // note left from now on; each pass is still the sender's access and no more.
    ThreadNotes, ThreadNotePass, ThreadNoteDismiss, SubthreadReviewSet, ThreadNoteAutoSet,
    // Agents you @mention, and Home's schedules and heartbeat (8 Oct, backend/dervo_agents.py, dervo_assistant.py).
    AgentsList, AgentTemplates, AgentSave, AgentDelete, AgentMention, AgentMentions, AgentResolve, AssistantJobs, AssistantJobSave, AssistantJobPause, AssistantJobDelete, AssistantJobRunNow, AssistantRuns, AssistantParse, AssistantHeartbeat, AssistantHeartbeatSet,
    // Send to cloud (backend/cloud_runs.py): a run in Claude's or Codex's own cloud through the user's own signed-in CLI.
    // The preview names the account, repository and branch and hands out a one-use id; only the send with that id starts
    // anything. Bringing a result back fetches its branch and makes a local branch without checking it out. Nothing here
    // pushes, merges or applies to the working tree.
    // Paid plans and the free trial (8 Oct 2026, backend/supabase_account.py PLAN_FIELDS): ask the server to start the
    // trial, ask for a Paddle checkout or billing link for your own account, and check the plan again. None grants access.
    DervoPlanStartTrial, DervoBillingOpen, DervoPlanRefresh,
    CloudPreview, CloudSend, CloudRuns, CloudCheck, CloudFollowUp, CloudBringBack, CloudEnvironmentSet }
impl Operation { fn name(&self)-> &'static str {match self {Self::DervoPlanStartTrial=>"dervo_plan_start_trial",Self::DervoBillingOpen=>"dervo_billing_open",Self::DervoPlanRefresh=>"dervo_plan_refresh",Self::TaskQuestionAnswer=>"task_question_answer",Self::TerminalOpen=>"terminal_open",Self::TerminalList=>"terminal_list",Self::TerminalRead=>"terminal_read",Self::TerminalWrite=>"terminal_write",Self::TerminalResize=>"terminal_resize",Self::TerminalClose=>"terminal_close",Self::BetaPlan=>"beta_plan",Self::BetaFeedbackContext=>"beta_feedback_context",Self::BetaFeedbackSend=>"beta_feedback_send",Self::BetaFeedbackStatus=>"beta_feedback_status",Self::BetaUpdateStatus=>"beta_update_status",Self::BetaUpdateCheck=>"beta_update_check",Self::BetaUpdateCancel=>"beta_update_cancel",Self::BetaUpdateFetch=>"beta_update_fetch",Self::ImageCancel=>"image_cancel",Self::ImageBegin=>"image_begin",Self::ImageChunk=>"image_chunk",Self::ImageFinish=>"image_finish",Self::ImageDiscard=>"image_discard",Self::ImageGet=>"image_get",Self::ImageThumbPut=>"image_thumb_put",Self::ImageThumb=>"image_thumb",Self::ImageDrafts=>"image_drafts",Self::KeepAwakeStatus=>"keep_awake_status",Self::KeepAwakeSet=>"keep_awake_set",Self::OpenrouterModelCheck=>"openrouter_model_check",Self::OpenrouterRouteSave=>"openrouter_route_save",Self::OpenrouterBudgetSave=>"openrouter_budget_save",Self::OpenrouterStatus=>"openrouter_status",Self::OpenrouterConnect=>"openrouter_connect",Self::OpenrouterCheck=>"openrouter_check",Self::OpenrouterCancel=>"openrouter_cancel",Self::OpenrouterDisconnect=>"openrouter_disconnect",Self::DraftsList=>"drafts_list",Self::DraftSave=>"draft_save",Self::PreviewStatus=>"preview_status",Self::PreviewStart=>"preview_start",Self::PreviewCancel=>"preview_cancel",Self::PreviewOpen=>"preview_open",Self::PreviewFolder=>"preview_folder",Self::PreviewStorageFolder=>"preview_storage_folder",Self::ProjectExplore=>"project_explore",Self::ProjectRename=>"project_rename",Self::ProjectSetupSave=>"project_setup_save",Self::FirstRunStatus=>"first_run_status",Self::FirstRunClaude=>"first_run_claude",Self::FirstRunInspect=>"first_run_inspect",Self::FirstRunFinish=>"first_run_finish",Self::ProjectModelSet=>"project_model_set",Self::ProjectProfileSet=>"project_profile_set",Self::ProjectReveal=>"project_reveal",Self::ProjectFileReveal=>"project_file_reveal",Self::ProfileFableAck=>"profile_fable_ack",Self::ConversationModelSet=>"conversation_model_set",Self::ConversationProfileSet=>"conversation_profile_set",Self::LifecycleBlockers=>"lifecycle_blockers",Self::ProjectsArchived=>"projects_archived",Self::ProjectArchive=>"project_archive",Self::ProjectRestore=>"project_restore",Self::ProjectDelete=>"project_delete",Self::ConversationsArchived=>"conversations_archived",Self::ConversationArchive=>"conversation_archive",Self::ConversationRestore=>"conversation_restore",Self::ConversationDelete=>"conversation_delete",Self::ConversationsArchive=>"conversations_archive",Self::ConversationsDeleteArchived=>"conversations_delete_archived",Self::DervoAccountStatus=>"dervo_account_status",Self::DervoAccountSendCode=>"dervo_account_send_code",Self::DervoAccountVerifyCode=>"dervo_account_verify_code",Self::DervoAccountSaveProfile=>"dervo_account_save_profile",Self::DervoAccountPhoto=>"dervo_account_photo",Self::DervoAccountSavePhoto=>"dervo_account_save_photo",Self::DervoAccountRemovePhoto=>"dervo_account_remove_photo",Self::DervoAccountStart=>"dervo_account_start",Self::DervoAccountPoll=>"dervo_account_poll",Self::DervoAccountCancel=>"dervo_account_cancel",Self::DervoAccountRestore=>"dervo_account_restore",Self::DervoAccountSignOut=>"dervo_account_sign_out",Self::OrganisationGet=>"organisation_get",Self::OrganisationSet=>"organisation_set",Self::OrganisationUndo=>"organisation_undo",Self::KnowledgeList=>"knowledge_list",Self::KnowledgeSave=>"knowledge_save",Self::KnowledgeApprove=>"knowledge_approve",Self::KnowledgeArchive=>"knowledge_archive",Self::KnowledgeAdopt=>"knowledge_adopt",Self::KnowledgeLessonsSet=>"knowledge_lessons_set",Self::KnowledgeLessonKeep=>"knowledge_lesson_keep",Self::KnowledgeLessonDismiss=>"knowledge_lesson_dismiss",Self::KnowledgeLessonList=>"knowledge_lesson_list",Self::IdeasList=>"ideas_list",Self::IdeasSave=>"ideas_save",Self::IdeasArchive=>"ideas_archive",Self::IdeasRestore=>"ideas_restore",Self::IdeasAssign=>"ideas_assign",Self::IdeasRoute=>"ideas_route",Self::IdeasItemAdd=>"ideas_item_add",Self::IdeasItemNote=>"ideas_item_note",Self::IdeasItemSet=>"ideas_item_set",Self::IdeasItemWork=>"ideas_item_work",Self::IdeasItemReopen=>"ideas_item_reopen",Self::IdeasItemList=>"ideas_item_list",Self::IdeasProductList=>"ideas_product_list",Self::IdeasItemProduct=>"ideas_item_product",Self::PushPreview=>"push_preview",Self::PushStart=>"push_start",Self::PushStatus=>"push_status",Self::PushRecount=>"push_recount",Self::OrchestratorList=>"orchestrator_list",Self::OrchestratorArrive=>"orchestrator_arrive",Self::OrchestratorSend=>"orchestrator_send",Self::OrchestratorResult=>"orchestrator_result",Self::OrchestratorUndo=>"orchestrator_undo",Self::OrchestratorOffer=>"orchestrator_offer",Self::OrchestratorMove=>"orchestrator_move",Self::OrchestratorHomeModel=>"orchestrator_home_model",Self::ProjectPickup=>"project_pickup",Self::ConversationPickup=>"conversation_pickup",Self::SkillsList=>"skills_list",Self::SkillsSave=>"skills_save",Self::SkillsReview=>"skills_review",Self::SkillsArchive=>"skills_archive",Self::SkillsRestore=>"skills_restore",Self::SkillsImport=>"skills_import",Self::SkillsImportText=>"skills_import_text",Self::SkillsExport=>"skills_export",Self::WorkspaceSearch=>"workspace_search",Self::WorkspaceSource=>"workspace_source",Self::ProjectCatchup=>"project_catchup",Self::Status=>"status",Self::Profiles=>"profiles",Self::Projects=>"projects",Self::AddProfile=>"add_profile",Self::ProfileKeyAdd=>"profile_key_add",Self::ProfileKeyRemove=>"profile_key_remove",Self::ProfileLimitSet=>"profile_limit_set",Self::ProfileRename=>"profile_rename",Self::AccountSigninStart=>"account_signin_start",Self::AccountSigninAdd=>"account_signin_add",Self::AccountSigninCancel=>"account_signin_cancel",Self::AccountUseSignedIn=>"account_use_signed_in",Self::AccountRemove=>"account_remove",Self::ClaudeVersion=>"claude_version",Self::DervoTesters=>"dervo_testers",Self::DervoTestersOlder=>"dervo_testers_older",Self::DervoTesterSet=>"dervo_tester_set",Self::DervoWaitlist=>"dervo_waitlist",Self::DervoWaitlistInvite=>"dervo_waitlist_invite",Self::CreateConversation=>"create_conversation",Self::Conversations=>"conversations",Self::ConversationSeen=>"conversation_seen",Self::ConversationDismiss=>"conversation_dismiss",Self::UsageSummary=>"usage_summary",Self::SessionSummary=>"session_summary",Self::ConversationStatus=>"conversation_status",Self::ConversationFeed=>"conversation_feed",Self::ConversationOlder=>"conversation_older",Self::Binding=>"binding",Self::Queue=>"queue",Self::History=>"history",Self::Cancel=>"cancel",Self::SharedContext=>"shared_context",Self::ConnectionInstructions=>"connection_instructions",Self::ConnectionCheck=>"connection_check",Self::ConnectionStatus=>"connection_status",Self::ConnectionLogin=>"connection_login",Self::ConnectionLoginStatus=>"connection_login_status",Self::ConnectionLoginCancel=>"connection_login_cancel",Self::ConnectionLoginCode=>"connection_login_code",Self::StartTurn=>"start_turn",Self::StopTurn=>"stop_turn",Self::TurnStatus=>"turn_status",Self::ReconcileTurn=>"reconcile_turn",Self::ModelCheck=>"model_check",Self::ModelStatus=>"model_status",Self::Tasks=>"tasks",Self::TaskCreate=>"task_create",Self::TaskApprove=>"task_approve",Self::TaskDeny=>"task_deny",Self::TaskDetail=>"task_detail",Self::TaskReview=>"task_review",Self::TaskContinue=>"task_continue",Self::TaskStop=>"task_stop",Self::TaskReconcile=>"task_reconcile",Self::TaskSettle=>"task_settle",Self::TaskThreadProposals=>"task_thread_proposals",Self::TaskThreadStart=>"task_thread_start",Self::TaskThreadDismiss=>"task_thread_dismiss",Self::TaskCommit=>"task_commit",Self::TaskUndo=>"task_undo",Self::TaskRestoreFile=>"task_restore_file",Self::TaskPropose=>"task_propose",Self::Proposals=>"proposals",Self::ProposalAccept=>"proposal_accept",Self::ProposalDismiss=>"proposal_dismiss",Self::HarnessRequest=>"harness_request",Self::HarnessStatus=>"harness_status",Self::HarnessCancel=>"harness_cancel",Self::HarnessEvents=>"harness_events",Self::HarnessBoundary=>"harness_boundary",Self::HarnessRequests=>"harness_requests",Self::HarnessActions=>"harness_actions",Self::HarnessResolve=>"harness_resolve",Self::CodingPermission=>"coding_permission",Self::CodingPermissionGrant=>"coding_permission_grant",Self::CodingPermissionRevoke=>"coding_permission_revoke",Self::CodingRequest=>"coding_request",Self::CodingRequests=>"coding_requests",Self::CodingEffortSet=>"coding_effort_set",Self::CodingAcceptanceSet=>"coding_acceptance_set",Self::CodingQueueCancel=>"coding_queue_cancel",Self::CodingWarm=>"coding_warm",Self::CodingRouteSet=>"coding_route_set",Self::ConversationRouteSet=>"conversation_route_set",Self::CodingRetryOn=>"coding_retry_on",Self::CodingWaitForReset=>"coding_wait_for_reset",Self::CodingFullAccessSet=>"coding_full_access_set",Self::CodingComputerAccessSet=>"coding_computer_access_set",Self::CodingConnectorsSet=>"coding_connectors_set",Self::CodingFreshStartSet=>"coding_fresh_start_set",Self::ProjectMemorySet=>"project_memory_set",Self::ProjectMemoryReflectSet=>"project_memory_reflect_set",Self::ProjectMemoryRemove=>"project_memory_remove",Self::CollabAgents=>"collab_agents",Self::CollabAgentSave=>"collab_agent_save",Self::CollabAgentDelete=>"collab_agent_delete",Self::CollabPolicy=>"collab_policy",Self::CollabPolicySet=>"collab_policy_set",Self::CollabReviewerSetup=>"collab_reviewer_setup",Self::CollabPackets=>"collab_packets",Self::CollabPacket=>"collab_packet",Self::CreateAgentConversation=>"create_agent_conversation",Self::ThreadStates=>"thread_states",Self::ThreadOrder=>"thread_order",Self::RoutingOutcome=>"routing_outcome",Self::CodingChecksSet=>"coding_checks_set",
    // Knowledge (design sweep, 19 Sep)
    Self::KnowledgeLessonReopen=>"knowledge_lesson_reopen",Self::KnowledgeVersionDismiss=>"knowledge_version_dismiss",Self::KnowledgeNoteRestore=>"knowledge_note_restore",
    Self::KnowledgeRepoList=>"knowledge_repo_list",Self::KnowledgeRepoSend=>"knowledge_repo_send",Self::KnowledgeRepoOpen=>"knowledge_repo_open",
    Self::KnowledgeNotesList=>"knowledge_notes_list",Self::KnowledgeNotesSet=>"knowledge_notes_set",Self::KnowledgeNotesReflectSet=>"knowledge_notes_reflect_set",Self::KnowledgeNoteRemove=>"knowledge_note_remove",
    Self::KnowledgeNoteEdit=>"knowledge_note_edit",Self::KnowledgeNoteKeep=>"knowledge_note_keep",Self::KnowledgeNotesSeen=>"knowledge_notes_seen",Self::KnowledgeNotePairKeep=>"knowledge_note_pair_keep",Self::KnowledgeNotePin=>"knowledge_note_pin",Self::KnowledgeRuleToNote=>"knowledge_rule_to_note",
    // Design sweep, surfaces (19 Sep).
    Self::ProjectsActivity=>"projects_activity",
    // Usage events (sweep/analytics, beta.9).
    Self::UsageEvent=>"usage_event",Self::UsageSharing=>"usage_sharing",Self::UsageSharingSet=>"usage_sharing_set",Self::UsageNoticeSeen=>"usage_notice_seen",Self::UsageReport=>"usage_report",Self::UsagePeople=>"usage_people",
    // Error reports (beta.11).
    Self::ErrorEvent=>"error_event",Self::ErrorReport=>"error_report",
    // Blank-project import (21 Sep).
    Self::HistoryLook=>"history_look",Self::HistoryPreview=>"history_preview",Self::HistoryRead=>"history_read",
    Self::OnboardingSources=>"onboarding_sources",Self::OnboardingLook=>"onboarding_look",Self::OnboardingThreads=>"onboarding_threads",Self::OnboardingBringIn=>"onboarding_bring_in",Self::OnboardingBatch=>"onboarding_batch",Self::OnboardingUndo=>"onboarding_undo",Self::OnboardingWaiting=>"onboarding_waiting",Self::OnboardingBringWaiting=>"onboarding_bring_waiting",
    Self::HistoryList=>"history_list",Self::RunRecord=>"run_record",Self::TraceFile=>"trace_file",Self::TraceLine=>"trace_line",
    Self::OutsideSessions=>"outside_sessions",Self::OutsideSessionsConsent=>"outside_sessions_consent",Self::OutsideSessionsConsentSet=>"outside_sessions_consent_set",
    Self::CodingHandoffPreview=>"coding_handoff_preview",Self::CodingHandoffRestorePreview=>"coding_handoff_restore_preview",Self::CodingHandoffRestore=>"coding_handoff_restore",
    Self::ProjectFileOpen=>"project_file_open",Self::ProjectFileText=>"project_file_text",Self::PreviewChanges=>"preview_changes",Self::CodingCommandsSet=>"coding_commands_set",Self::CodingAutoCommitSet=>"coding_auto_commit_set",Self::CodingRunLimitsSet=>"coding_run_limits_set",
    Self::AppIconStatus=>"app_icon_status",Self::BetaUpdateNotice=>"beta_update_notice",Self::BetaUpdateLater=>"beta_update_later",Self::BetaUpdateSeen=>"beta_update_seen",
    // Beta.18: thread folders.
    Self::ThreadFolder=>"thread_folder",Self::ThreadFolderChoose=>"thread_folder_choose",Self::ThreadFolderRetry=>"thread_folder_retry",Self::ThreadFolderInstall=>"thread_folder_install",
    Self::ThreadFolderSkipInstall=>"thread_folder_skip_install",Self::ThreadFolderMerge=>"thread_folder_merge",Self::ThreadFolderReveal=>"thread_folder_reveal",
    Self::ThreadFolderSettings=>"thread_folder_settings",Self::ThreadFolderIgnoredSet=>"thread_folder_ignored_set",
    // Beta.16's shared operations.
    Self::ConversationsTidy=>"conversations_tidy",Self::ConversationsTidied=>"conversations_tidied",Self::ConversationsTidyUndo=>"conversations_tidy_undo",Self::ConversationPin=>"conversation_pin",Self::ConversationPins=>"conversation_pins",Self::ProjectTidySetting=>"project_tidy_setting",Self::ProjectTidySet=>"project_tidy_set",
    Self::NextUpRead=>"next_up_read",Self::NextUpAdd=>"next_up_add",Self::NextUpMove=>"next_up_move",Self::NextUpShip=>"next_up_ship",Self::NextUpWork=>"next_up_work",Self::NextUpReportsHere=>"next_up_reports_here",
    Self::TesterReports=>"tester_reports",Self::TesterReportMark=>"tester_report_mark",Self::TesterReportAdd=>"tester_report_add",
    Self::GithubStatus=>"github_status",Self::PrPreview=>"pr_preview",Self::PrCreate=>"pr_create",Self::PrStatus=>"pr_status",Self::PrThreadBranch=>"pr_thread_branch",
    Self::Reminders=>"reminders",Self::RemindersDue=>"reminders_due",Self::ReminderNotified=>"reminder_notified",Self::ReminderDone=>"reminder_done",Self::ReminderAdd=>"reminder_add",Self::HomeSuggestions=>"home_suggestions",
    Self::ThreadNotes=>"thread_notes",Self::ThreadNotePass=>"thread_note_pass",Self::ThreadNoteDismiss=>"thread_note_dismiss",Self::SubthreadReviewSet=>"subthread_review_set",Self::ThreadNoteAutoSet=>"thread_note_auto_set",
    Self::AgentsList=>"agents_list",Self::AgentTemplates=>"agent_templates",Self::AgentSave=>"agent_save",Self::AgentDelete=>"agent_delete",Self::AgentMention=>"agent_mention",Self::AgentMentions=>"agent_mentions",Self::AgentResolve=>"agent_resolve",Self::AssistantJobs=>"assistant_jobs",Self::AssistantJobSave=>"assistant_job_save",Self::AssistantJobPause=>"assistant_job_pause",Self::AssistantJobDelete=>"assistant_job_delete",Self::AssistantJobRunNow=>"assistant_job_run_now",Self::AssistantRuns=>"assistant_runs",Self::AssistantParse=>"assistant_parse",Self::AssistantHeartbeat=>"assistant_heartbeat",Self::AssistantHeartbeatSet=>"assistant_heartbeat_set",
    Self::CloudPreview=>"cloud_preview",Self::CloudSend=>"cloud_send",Self::CloudRuns=>"cloud_runs",Self::CloudCheck=>"cloud_check",Self::CloudFollowUp=>"cloud_follow_up",Self::CloudBringBack=>"cloud_bring_back",Self::CloudEnvironmentSet=>"cloud_environment_set"}} }
#[tauri::command]
pub async fn local_request(window: tauri::WebviewWindow, state:tauri::State<'_,Host>, operation:Operation,args:Value)->Result<Value,String> {
    if window.label()!="main" {return Err("Only the local main window can use this store".into())}
    state.call(operation.name(),args)
}

#[cfg(target_os = "macos")]
#[tauri::command]
pub fn pick_project(window:tauri::WebviewWindow, state:tauri::State<'_,Host>, profile_id:String, model:String)->Result<Value,String> {
    if window.label()!="main" {return Err("Only the main window can select a project".into())}
    use objc2::MainThreadMarker;
    use objc2_app_kit::NSOpenPanel;
    let mtm=MainThreadMarker::new().ok_or("Folder picker must run on the main thread")?;
    let panel=NSOpenPanel::openPanel(mtm);
    panel.setCanChooseDirectories(true); panel.setCanChooseFiles(false); panel.setAllowsMultipleSelection(false);
    if panel.runModal()!=1 {return Ok(Value::Null)}
    let url=panel.URL().ok_or("No folder selected")?;
    let path=url.path().ok_or("Selected folder has no local path")?.to_string();
    state.call("_picked_project",json!({"path":path,"profile_id":profile_id,"model":model}))
}

#[cfg(target_os = "macos")]
#[tauri::command]
pub fn choose_project_folder(window:tauri::WebviewWindow, state:tauri::State<'_,Host>)->Result<Value,String> {
    if window.label()!="main" {return Err("Only the main window can select a project".into())}
    use objc2::MainThreadMarker;
    use objc2_app_kit::NSOpenPanel;
    let mtm=MainThreadMarker::new().ok_or("Folder picker must run on the main thread")?;
    let panel=NSOpenPanel::openPanel(mtm);
    panel.setCanChooseDirectories(true); panel.setCanChooseFiles(false); panel.setAllowsMultipleSelection(false);
    if panel.runModal()!=1 {return Ok(Value::Null)}
    let url=panel.URL().ok_or("No folder selected")?;
    let path=url.path().ok_or("Selected folder has no local path")?.to_string();
    state.call("_stage_project_folder",json!({"path":path}))
}

/// B7: choose a folder inside the project for coding access. The panel opens at the project folder; the path comes back
/// as the absolute path macOS gave, and the renderer keeps it only when it lies inside the project. The host checks the
/// granted folders again when coding is allowed, so this picker grants nothing by itself.
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn choose_folder_in(window:tauri::WebviewWindow, root:String)->Result<Option<String>,String> {
    if window.label()!="main" {return Err("Only the main window can choose a folder".into())}
    if root.is_empty()||!root.starts_with('/') {return Err("Choose a folder inside the project".into())}
    use objc2::MainThreadMarker;
    use objc2_app_kit::NSOpenPanel;
    use objc2_foundation::{NSString,NSURL};
    let mtm=MainThreadMarker::new().ok_or("Folder picker must run on the main thread")?;
    let panel=NSOpenPanel::openPanel(mtm);
    panel.setCanChooseDirectories(true); panel.setCanChooseFiles(false); panel.setAllowsMultipleSelection(false);
    let url=NSURL::fileURLWithPath_isDirectory(&NSString::from_str(&root),true);
    panel.setDirectoryURL(Some(&url));
    if panel.runModal()!=1 {return Ok(None)}
    let chosen=panel.URL().ok_or("No folder selected")?;
    Ok(Some(chosen.path().ok_or("Selected folder has no local path")?.to_string()))
}

// The three pickers above are AppKit's NSOpenPanel. Other platforms get the same commands here, with Tauri's dialog
// plugin (b16/windows). They're async so the dialog never holds the window's own thread while it waits for a person.
#[cfg(not(target_os = "macos"))]
fn pick_folder(window:&tauri::WebviewWindow, start:Option<&std::path::Path>)->Result<Option<String>,String> {
    use tauri_plugin_dialog::DialogExt;
    let mut dialog=window.dialog().file().set_parent(window);
    if let Some(start)=start {dialog=dialog.set_directory(start);}
    let Some(chosen)=dialog.blocking_pick_folder() else {return Ok(None)};
    let path=chosen.into_path().map_err(|_|"Selected folder has no local path")?;
    Ok(Some(path.to_str().ok_or("Selected folder has no local path")?.to_string()))
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub async fn pick_project(window:tauri::WebviewWindow, state:tauri::State<'_,Host>, profile_id:String, model:String)->Result<Value,String> {
    if window.label()!="main" {return Err("Only the main window can select a project".into())}
    let Some(path)=pick_folder(&window,None)? else {return Ok(Value::Null)};
    state.call("_picked_project",json!({"path":path,"profile_id":profile_id,"model":model}))
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub async fn choose_project_folder(window:tauri::WebviewWindow, state:tauri::State<'_,Host>)->Result<Value,String> {
    if window.label()!="main" {return Err("Only the main window can select a project".into())}
    let Some(path)=pick_folder(&window,None)? else {return Ok(Value::Null)};
    state.call("_stage_project_folder",json!({"path":path}))
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub async fn choose_folder_in(window:tauri::WebviewWindow, root:String)->Result<Option<String>,String> {
    if window.label()!="main" {return Err("Only the main window can choose a folder".into())}
    let root=std::path::PathBuf::from(root);
    if !root.is_absolute() {return Err("Choose a folder inside the project".into())}
    pick_folder(&window,Some(&root))
}

#[cfg(test)] mod tests {
    #[test] fn beta_update_bridge_never_accepts_download_paths_or_urls() {
        for name in ["beta_update_status", "beta_update_check", "beta_update_cancel", "beta_update_fetch", "beta_update_notice", "beta_update_later", "beta_update_seen"] {
            let op: super::Operation = serde_json::from_value(serde_json::json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        // The update plan and its paths come only from the native Update and restart command.
        // The icon choice itself goes through the native app_icon_set command, which asks the host first (app_icon.rs).
        for name in ["_beta_update_download", "beta_update_download", "beta_update_url", "beta_update_publish", "_prepare_update", "prepare_update", "beta_update_install", "_app_icon_check", "app_icon_set", "app_icon_founder_set"] {
            assert!(serde_json::from_value::<super::Operation>(serde_json::json!(name)).is_err());
        }
    }

    use super::*;
    #[test] fn organisation_operations_round_trip_and_grant_nothing_else() {
        for name in ["organisation_get", "organisation_set", "organisation_undo"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["organisation", "organisation_sql", "organisation_get_all", "organisation_set_all", "organisation_delete", "organisation_auto_approve", "OrganisationGet", "organization_get"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
        for value in [Value::Null, json!(1), json!({"organisation_get": {}})] {
            assert!(serde_json::from_value::<Operation>(value).is_err());
        }
    }
    #[test] fn thread_note_operations_round_trip_and_nothing_note_shaped_sends_on_its_own() {
        for name in ["thread_notes", "thread_note_pass", "thread_note_dismiss", "subthread_review_set", "thread_note_auto_set"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["thread_note_leave", "thread_note_send", "thread_note_pass_all", "subthread_review_start", "thread_notes_delete", "ThreadNotes"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn agent_and_assistant_operations_round_trip_and_none_runs_code() {
        for name in ["agents_list", "agent_templates", "agent_save", "agent_delete", "agent_mention", "agent_mentions", "agent_resolve", "assistant_jobs", "assistant_job_save", "assistant_job_pause", "assistant_job_delete", "assistant_job_run_now", "assistant_runs", "assistant_parse", "assistant_heartbeat", "assistant_heartbeat_set"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["agent_run", "agent_exec", "assistant_script", "assistant_gate_script", "agents_delete_all", "AgentSave"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn cloud_operations_round_trip_and_none_of_them_pushes_or_applies() {
        for name in ["cloud_preview", "cloud_send", "cloud_runs", "cloud_check", "cloud_follow_up", "cloud_bring_back", "cloud_environment_set"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["cloud_apply", "cloud_push", "cloud_teleport", "cloud_send_again", "CloudSend"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn knowledge_operations_are_explicit_and_grant_no_execution() {
        for name in ["knowledge_list", "knowledge_save", "knowledge_approve", "knowledge_archive", "knowledge_adopt", "knowledge_lessons_set", "knowledge_lesson_keep", "knowledge_lesson_dismiss", "knowledge_lesson_list",
            // Knowledge (design sweep, 19 Sep)
            "knowledge_lesson_reopen", "knowledge_version_dismiss", "knowledge_note_restore", "knowledge_repo_list", "knowledge_repo_send", "knowledge_repo_open",
            // Knowledge (beta.11, fix-memory F1)
            "knowledge_notes_list", "knowledge_notes_set", "knowledge_notes_reflect_set", "knowledge_note_remove",
            // Knowledge (beta.16, b16/memory): edit a note, keep a flagged one or a similar pair, mark the page seen
            "knowledge_note_edit", "knowledge_note_keep", "knowledge_notes_seen", "knowledge_note_pair_keep",
            // Knowledge (beta.17, dd #22): pin a note
            "knowledge_note_pin",
            // Knowledge (beta.17.1, #41 step 8): agent-made rules to notes
            "knowledge_rule_to_note"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["knowledge_delete", "knowledge_auto_approve", "knowledge_load_url", "knowledge_install", "knowledge_share_private", "knowledge_repo_write", "knowledge_repo_run", "knowledge_repo_settings",
            "knowledge_notes_clear", "knowledge_note_add", "knowledge_notes_remove", "knowledge_notes",
            "knowledge_notes_edit", "knowledge_note_keep_all", "knowledge_note_seen", "knowledge_notes_pair_keep", "knowledge_note_pair_merge",
            "knowledge_notes_pin", "knowledge_note_pin_all", "knowledge_note_unpin",
            "knowledge_rules_to_notes", "knowledge_rule_to_notes", "knowledge_note_to_rule"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn idea_operations_round_trip_and_nothing_else_idea_shaped_is_accepted() {
        for name in ["ideas_list", "ideas_save", "ideas_archive", "ideas_restore", "ideas_assign", "ideas_route"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        // An idea is project metadata. It never deletes a conversation, runs anything or reaches another project.
        for name in ["ideas_delete", "idea_list", "ideas_run", "ideas_assign_all", "ideas_import", "ideas_move_project", "ideas_route_send", "ideas_dispatch"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn reply_actions_open_documents_and_nothing_runs_a_path_or_a_command() {
        for name in ["project_file_open","project_file_text","preview_changes","coding_commands_set"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        // Run from a reply types into the project terminal through terminal_write, as you would. There's no host
        // operation that runs a command or a file on the renderer's say-so.
        for name in ["project_file_run","project_file_exec","terminal_run","command_run","run_command","project_command","open_path","open_file"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err(),"{name} must not round-trip");
        }
    }
    #[test] fn thread_folder_operations_round_trip_and_none_of_them_pushes_squashes_or_merges_on_its_own() {
        for name in ["thread_folder","thread_folder_choose","thread_folder_retry","thread_folder_install","thread_folder_skip_install",
                     "thread_folder_merge","thread_folder_reveal","thread_folder_settings","thread_folder_ignored_set"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        // Beta.18: Merge is the person's click and a merge commit. There's no squash, no automatic merge, no push from
        // here, and no way for the renderer to name a folder or a command.
        for name in ["thread_folder_squash","thread_folder_merge_auto","thread_folder_auto_merge_set","thread_folder_push","thread_folder_install_command","thread_folder_path_set","thread_folder_delete_branch"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err(), "{name} must not round-trip");
        }
    }
    #[test] fn commit_results_automatically_is_one_setting_and_nothing_that_commits_on_the_renderers_say_so() {
        let op: Operation = serde_json::from_value(json!("coding_auto_commit_set")).unwrap();
        assert_eq!(op.name(), "coding_auto_commit_set");
        // 16.2: the setting only says whether a finished full-access result is committed; the host decides and commits.
        for name in ["coding_auto_commit", "task_auto_commit", "coding_commit_now", "coding_auto_push_set", "coding_auto_branch_set"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err(), "{name} must not round-trip");
        }
    }
    #[test] fn the_connector_picker_is_one_setting_and_nothing_that_lists_or_calls_a_connector() {
        let op: Operation = serde_json::from_value(json!("coding_connectors_set")).unwrap();
        assert_eq!(op.name(), "coding_connectors_set");
        // The backend lists connectors itself, from the account's own Claude Code profile; the renderer never lists,
        // calls or authorises one.
        for name in ["coding_connectors_list", "coding_connector_call", "connectors_set", "coding_connectors_allow_all", "mcp_list"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn bugs_and_ideas_operations_round_trip_and_there_is_no_delete() {
        // The one list per project (MEM2): add, note, set and list. Items close or archive; nothing deletes one.
        // ideas_item_reopen (20 Sep, #16): a done or closed item back to open, the answer "Reopen #9" needs.
        for name in ["ideas_item_add", "ideas_item_note", "ideas_item_set", "ideas_item_work", "ideas_item_reopen", "ideas_item_list"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["ideas_item_delete", "ideas_item_import", "ideas_items"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn push_operations_round_trip_and_there_is_no_force_or_pull() {
        // Push to GitHub (#29): preview, start with the confirm token, poll, and recount an unknown result. Nothing
        // force-pushes, pulls, fetches or pushes tags through the bridge.
        for name in ["push_preview", "push_start", "push_status", "push_recount"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["push_force", "push_tags", "push_all", "git_pull", "git_fetch", "push"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn orchestrator_operations_round_trip_and_none_of_them_runs_anything_itself() {
        // orchestrator_undo (19 Sep) takes back one filing, move or rename Home made on its own, by its index in a reply.
        // orchestrator_offer (beta.16.1, item 9) starts one job a reply offered, by its id: your click on its Start.
        // orchestrator_home_model (beta.17) saves the model and thinking Home's own replies run on in a project.
        // orchestrator_move (8 Oct) moves a routed reply's job, before it goes, to another thread, a new one or Home.
        for name in ["orchestrator_list", "orchestrator_arrive", "orchestrator_send", "orchestrator_result", "orchestrator_undo", "orchestrator_offer", "orchestrator_move", "orchestrator_home_model"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        // Home records and asks. Sending a job goes through create_conversation and coding_request, one job per request;
        // there's no operation that runs a job, clears the chat or reaches another project. The first orchestrator's
        // local planner is gone.
        for name in ["orchestrator_plan", "orchestrator_run", "orchestrator_dispatch", "orchestrator_clear", "orchestrator_delete", "orchestrator_send_all", "orchestrators_list", "orchestrator_undo_all", "orchestrator_redo"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn history_operations_round_trip_and_only_read() {
        for name in ["history_list", "run_record", "trace_file", "trace_line"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["history_save", "run_record_set", "trace_write", "history_delete"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn pickup_operations_round_trip_and_only_read() {
        // Where you left off, from records: two reads, one for Home and one for a thread. Nothing writes through them.
        for name in ["project_pickup", "conversation_pickup"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["pickup_save", "pickup_clear", "project_pickup_set", "conversation_pickup_set", "pickups"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn reminder_and_suggestion_operations_round_trip_and_none_of_them_starts_work() {
        // Words to show on Home (9 Oct): an agent's reminders and your own, and the thread proposals nobody started.
        // Starting a proposal is still task_thread_start, from the thread where its whole request shows.
        for name in ["reminders", "reminders_due", "reminder_notified", "reminder_done", "reminder_add", "home_suggestions"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["reminder_delete", "reminder_send", "reminders_add", "home_suggestion_start", "suggestions", "HomeSuggestions"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn skill_operations_round_trip_and_none_of_them_runs_or_deletes_anything() {
        for name in ["skills_list", "skills_save", "skills_review", "skills_archive", "skills_restore", "skills_import", "skills_export"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        // A skill is reviewed text. Nothing here deletes one, runs one, approves every proposal at once or loads a URL.
        for name in ["skills_delete", "skill_list", "skills_run", "skills_approve_all", "skills_import_url", "skills_install", "skills_propose"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn workspace_search_operations_round_trip() {
        for name in ["workspace_search", "workspace_source", "project_catchup"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["workspace_sql", "workspace_search_all", "workspace_source_file"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn conversation_feed_is_an_explicit_read_operation() {
        let op: Operation = serde_json::from_value(json!("conversation_feed")).unwrap();
        assert_eq!(op.name(), "conversation_feed");
        for name in ["conversation_sql", "conversation_feed_all", "conversation_feed_write"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn conversation_older_is_an_explicit_read_operation() {
        let op: Operation = serde_json::from_value(json!("conversation_older")).unwrap();
        assert_eq!(op.name(), "conversation_older");
        assert!(serde_json::from_value::<Operation>(json!("conversation_older_all")).is_err());
    }
    #[test] fn scoped_status_operation_round_trips() {
        let op: Operation = serde_json::from_value(json!("conversation_status")).unwrap();
        assert_eq!(op.name(), "conversation_status");
    }
    #[test] fn staged_setup_accepts_a_selection_handle_but_not_a_renderer_folder() {
        let op:Operation=serde_json::from_value(json!("project_setup_save")).unwrap();
        assert_eq!(op.name(),"project_setup_save");
        let model:Operation=serde_json::from_value(json!("project_model_set")).unwrap();
        assert_eq!(model.name(),"project_model_set");
        let conversation:Operation=serde_json::from_value(json!("conversation_model_set")).unwrap();
        assert_eq!(conversation.name(),"conversation_model_set");
        // conversation_profile_set is the one conversation-account operation, added 17 September 2026; near misses stay out.
        let account:Operation=serde_json::from_value(json!("conversation_profile_set")).unwrap();
        assert_eq!(account.name(),"conversation_profile_set");
        for op in ["conversation_account_set","conversation_bind","thread_model_set","thread_profile_set","conversation_profile","conversation_provider_set"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
        for op in ["_stage_project_folder","project_setup_path","add_picked_project"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err());
        }
    }
    #[test] fn project_settings_operations_round_trip_and_near_misses_are_refused() {
        // Design sweep, 19 Sep: the project's default account, Show in Finder (the host opens the project's own folder,
        // never a path from the renderer) and the once-per-account Fable confirmation.
        for name in ["project_profile_set","project_reveal","project_file_reveal","profile_fable_ack"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["project_account_set","project_profile","project_open","project_reveal_path","reveal_path","open_path","finder_open",
                   "profile_fable","fable_ack","profile_fable_ack_all","profile_fable_reset","ProjectProfileSet"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn conversation_lifecycle_round_trips_and_grants_no_other_removal() {
        for name in ["conversations_archived","conversation_archive","conversation_restore","conversation_delete","conversations_archive","conversations_delete_archived"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        // Beta.16.1 item 3: many at once, archive and "Delete all archived" only. Nothing deletes without its confirm.
        for op in ["conversation_purge","thread_delete","channel_archive","channel_delete","conversation_hide","messages_delete","conversations_delete","conversations_delete_all","conversations_purge"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn dervo_commits_and_undoes_a_task_and_nothing_else_git_shaped_round_trips() {
        for name in ["task_commit","task_undo"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["task_push","task_branch","task_revert","git_commit","task_git","task_uncommit","task_checkout"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn settings_operations_round_trip_and_near_misses_do_not() {
        // Connected accounts (a name, the Claude Code version) and Testers (admins only; the server checks again).
        for name in ["profile_rename","claude_version","dervo_testers","dervo_testers_older","dervo_tester_set","dervo_waitlist","dervo_waitlist_invite"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["profile_name","profile_update","profile_set","claude_update","claude_version_set","dervo_tester_delete",
                   "dervo_testers_all","dervo_membership_set","dervo_set_membership","dervo_admin","dervo_admins","dervo_audit"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn usage_operations_round_trip_and_near_misses_do_not() {
        // Usage events (sweep/analytics): nothing here reads rows back. usage_people names testers, to admins only.
        for name in ["usage_event","usage_sharing","usage_sharing_set","usage_notice_seen","usage_report","usage_people"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["usage_events","usage_rows","usage_export","usage_delete","usage_user","usage_send","usage_flush","usage_report_all","usage_person","usage_people_all","telemetry"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn error_report_operations_round_trip_and_near_misses_do_not() {
        for name in ["error_event","error_report"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["error_events","error_rows","error_text","error_send","error_report_all","crash_report"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn plan_operations_round_trip_and_near_misses_do_not() {
        for name in ["dervo_plan_start_trial","dervo_billing_open","dervo_plan_refresh"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["dervo_plan_set","dervo_billing_grant","dervo_trial_extend","dervo_subscription_set","dervo_billing_webhook","dervo_plan"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn beta18_account_operations_round_trip_and_near_misses_do_not() {
        // backend/account_signin.py FIELDS: adding by sign-in first, using another sign-in, and removing any account.
        for name in ["account_signin_start","account_signin_add","account_signin_cancel","account_use_signed_in","account_remove"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["account_signin","account_add","account_delete","account_signout","account_sign_out","account_signin_code","account_use","profile_remove"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn api_key_accounts_have_exactly_three_operations() {
        for (op, name) in [("profile_key_add","profile_key_add"),("profile_key_remove","profile_key_remove"),("profile_limit_set","profile_limit_set")] {
            let parsed:Operation=serde_json::from_value(json!(op)).unwrap();
            assert_eq!(parsed.name(),name);
        }
        for op in ["profile_key_read","profile_key_get","profile_key","profile_remove","profile_delete","profile_key_set","profile_key_check"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn conversation_route_and_retry_on_round_trip_and_near_misses_do_not() {
        for name in ["conversation_route_set","coding_retry_on","coding_wait_for_reset"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["conversation_route","conversation_route_get","conversation_auto","coding_retry","coding_retry_other","task_retry_on","retry_on","coding_switch","coding_wait","wait_for_reset","coding_wait_reset","coding_resume"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn beta16_shared_operations_round_trip_and_nothing_that_merges_or_runs_gh_is_accepted() {
        // The same lists as backend/shared_operations.py LIFECYCLE and OPERATIONS (test_shared_operations reads this file to check).
        for name in ["conversations_tidy","conversations_tidied","conversations_tidy_undo","conversation_pin","conversation_pins","project_tidy_setting","project_tidy_set",
                     "next_up_read","next_up_add","next_up_move","next_up_ship","next_up_work","next_up_reports_here","tester_reports","tester_report_mark","tester_report_add",
                     "github_status","pr_preview","pr_create","pr_status","pr_thread_branch"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["pr_merge","pr_close","pr_comment","pr_review","pr_force_push","github_login","github_token","gh_run","gh_api","next_up_delete","tester_report_delete","conversations_delete_idle",
                   // bridge's first names for lifecycle's operations, before Bryce chose lifecycle's (25 Sep)
                   "next_up_list","next_up_drop","conversation_pin_set","project_tidy_get","tester_reports_list","tester_report_seen"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn handoff_operations_round_trip_and_nothing_that_writes_git_is_accepted() {
        for name in ["coding_handoff_preview","coding_handoff_restore_preview","coding_handoff_restore"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for op in ["coding_handoff","coding_handoff_send","coding_snapshot","coding_snapshot_restore","coding_restore","coding_git_stash","coding_stash_apply","coding_handoff_restore_all"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn continuity_and_memory_operations_round_trip_and_near_misses_are_refused() {
        for name in ["coding_fresh_start_set", "project_memory_set", "project_memory_reflect_set", "project_memory_remove"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for name in ["project_memory_add", "project_memory_clear", "coding_fresh_start", "project_memory"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn coding_full_access_set_round_trips_and_near_misses_are_refused() {
        let op:Operation=serde_json::from_value(json!("coding_full_access_set")).unwrap();
        assert_eq!(op.name(),"coding_full_access_set");
        let computer:Operation=serde_json::from_value(json!("coding_computer_access_set")).unwrap();
        assert_eq!(computer.name(),"coding_computer_access_set");
        for name in ["coding_full_access","full_access_set","coding_shell","coding_full_access_get","CodingFullAccessSet"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err(),"{name} must not be accepted");
        }
    }
    #[test] fn coding_route_set_round_trips_and_no_other_route_operation_does() {
        let op:Operation=serde_json::from_value(json!("coding_route_set")).unwrap();
        assert_eq!(op.name(),"coding_route_set");
        for op in ["coding_route","coding_route_get","coding_auto_switch","route_set","coding_route_clear"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn coding_queue_cancel_round_trips_and_nothing_else_reaches_the_queue() {
        let op:Operation=serde_json::from_value(json!("coding_queue_cancel")).unwrap();
        assert_eq!(op.name(),"coding_queue_cancel");
        for op in ["coding_queue","coding_queue_start","coding_queue_clear","coding_queue_reorder","queue_cancel"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn session_summary_round_trips_and_no_other_session_operation_does() {
        let op:Operation=serde_json::from_value(json!("session_summary")).unwrap();
        assert_eq!(op.name(),"session_summary");
        for op in ["session_start","session_end","session_mark","good_morning","session_summary_reset"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn usage_summary_round_trips_and_no_other_usage_operation_does() {
        let op:Operation=serde_json::from_value(json!("usage_summary")).unwrap();
        assert_eq!(op.name(),"usage_summary");
        for op in ["usage","usage_reset","usage_set","provider_usage","rate_limits","usage_summary_reset"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn task_settle_round_trips_and_near_misses_fail() {
        let op:Operation=serde_json::from_value(json!("task_settle")).unwrap();
        assert_eq!(op.name(),"task_settle");
        for near in ["task_settled","task_settle_all","tasks_settle","task_resolve"] {
            assert!(serde_json::from_value::<Operation>(json!(near)).is_err(),"{near}");
        }
    }
    #[test] fn history_import_operations_round_trip_and_near_misses_fail() {
        for name in ["history_look","history_preview","history_read"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for near in ["history_import","history_write","history_delete","history_look_all","codex_history"] {
            assert!(serde_json::from_value::<Operation>(json!(near)).is_err(),"{near}");
        }
    }
    #[test] fn onboarding_operations_round_trip_and_near_misses_fail() {
        for name in ["onboarding_sources","onboarding_look","onboarding_threads","onboarding_bring_in","onboarding_batch","onboarding_undo","onboarding_waiting","onboarding_bring_waiting"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for near in ["onboarding","onboarding_scan","onboarding_read","onboarding_bringin","onboarding_undo_all","onboarding_delete"] {
            assert!(serde_json::from_value::<Operation>(json!(near)).is_err(),"{near}");
        }
    }
    #[test] fn outside_sessions_operations_round_trip_and_near_misses_fail() {
        for name in ["outside_sessions","outside_sessions_consent","outside_sessions_consent_set"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for near in ["outside_session","outside_sessions_read","outside_sessions_write","outside_sessions_consent_get","outside_sessions_all"] {
            assert!(serde_json::from_value::<Operation>(json!(near)).is_err(),"{near}");
        }
    }
    #[test] fn first_run_operations_round_trip_and_near_misses_fail() {
        for name in ["first_run_status","first_run_claude","first_run_inspect","first_run_finish"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for near in ["first_run","first_run_reset","first_run_claude_login","first_run_scan"] {
            assert!(serde_json::from_value::<Operation>(json!(near)).is_err(),"{near}");
        }
    }
    #[test] fn conversation_dismiss_round_trips_and_near_misses_fail() {
        let op:Operation=serde_json::from_value(json!("conversation_dismiss")).unwrap();
        assert_eq!(op.name(),"conversation_dismiss");
        for near in ["conversation_dismissed","conversations_dismiss","task_dismiss","conversation_dismiss_all"] {
            assert!(serde_json::from_value::<Operation>(json!(near)).is_err(),"{near}");
        }
    }
    #[test] fn conversation_seen_round_trips_and_marks_nothing_else() {
        let op:Operation=serde_json::from_value(json!("conversation_seen")).unwrap();
        assert_eq!(op.name(),"conversation_seen");
        for op in ["conversation_unseen","conversations_seen","conversation_mark_all","conversation_read_receipts"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
    #[test] fn question_answers_have_one_scoped_operation_without_permission_shortcuts() {
        let op: Operation = serde_json::from_value(json!("task_question_answer")).unwrap();
        assert_eq!(op.name(),"task_question_answer");
        for name in ["task_question_grant","task_question_execute","task_question_route","task_question_answer_all","task_question_approve"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err(),"{name}");
        }
    }

    #[test] fn terminal_operations_are_explicit_and_have_no_shell_command_shortcut() {
        for name in ["terminal_open","terminal_list","terminal_read","terminal_write","terminal_resize","terminal_close"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        for name in ["terminal_exec","terminal_run","terminal_shell","terminal_env","terminal_cwd","terminal_write_all","terminal_close_all"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err(),"{name}");
        }
    }

    #[test] fn feedback_operations_round_trip_without_reading_or_admin_access() {
        for name in ["beta_feedback_context", "beta_feedback_send", "beta_feedback_status"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["beta_feedback_list", "beta_feedback_admin", "beta_feedback_sql", "beta_feedback_delete"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn narrow_operation_allowlist() {
        for name in ["shell","sql","env","launch_live","record","reconcile_codex","_picked_project","_stage_project_folder","_shutdown","_prepare_release_switch","_prepare_release_rollback"] {assert!(serde_json::from_value::<Operation>(json!(name)).is_err());}
        assert!(serde_json::from_value::<Operation>(json!("queue")).is_ok());
    }
    #[test] fn harness_coordination_operations_round_trip_and_nothing_else_from_the_harness() {
        for name in ["harness_request","harness_status","harness_cancel","harness_events","harness_boundary","harness_requests","harness_actions","harness_resolve"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["harness_execute","harness_open","supervisor","request_help"] {assert!(serde_json::from_value::<Operation>(json!(name)).is_err());}
    }
    #[test] fn conversation_coding_operations_round_trip_and_grant_nothing_else() {
        for name in ["coding_permission","coding_permission_grant","coding_permission_revoke","coding_request","coding_requests","coding_effort_set"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["coding_shell","coding_root","coding_permission_all","task_run","coding_effort","coding_effort_get","coding_effort_set_all"] {assert!(serde_json::from_value::<Operation>(json!(name)).is_err());}
    }
    #[test] fn result_approval_policy_round_trips_and_grants_nothing_else() {
        let op: Operation = serde_json::from_value(json!("coding_acceptance_set")).unwrap();
        assert_eq!(op.name(), "coding_acceptance_set");
        for name in ["coding_acceptance","coding_acceptance_get","coding_acceptance_set_all","task_complete","task_auto_approve"] {assert!(serde_json::from_value::<Operation>(json!(name)).is_err());}
    }
    #[test] fn collaboration_operations_round_trip_and_grant_nothing_else() {
        for name in ["collab_agents","collab_agent_save","collab_agent_delete","collab_policy","collab_policy_set","collab_reviewer_setup","collab_packets","collab_packet","create_agent_conversation"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["collab_run","collab_approve","collab_packet_accept","bind_agent","bind_project","create_agent_conversation_all"] {assert!(serde_json::from_value::<Operation>(json!(name)).is_err());}
    }
    #[test] fn task_reconcile_round_trips_and_grants_no_replay_or_arbitrary_write() {
        let op: Operation = serde_json::from_value(json!("task_reconcile")).unwrap();
        assert_eq!(op.name(), "task_reconcile");
        for name in ["task_replay","task_reconcile_all","task_retry","task_restore","task_delete_file","task_overwrite","reconcile","task_write"] {assert!(serde_json::from_value::<Operation>(json!(name)).is_err());}
    }
    #[test] fn human_file_restoration_round_trips() {
        let op: Operation = serde_json::from_value(json!("task_restore_file")).unwrap();
        assert_eq!(op.name(), "task_restore_file");
        for name in ["task_restore_all", "task_delete_file", "task_rollback", "provider_restore_file"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn native_sign_in_operations_round_trip() {
        for name in ["connection_login","connection_login_status","connection_login_cancel","connection_login_code"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
    }
    #[test] fn profile_photo_operations_are_scoped() {
        for name in ["dervo_account_photo", "dervo_account_save_photo", "dervo_account_remove_photo"] {
            let op: Operation = serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(), name);
        }
        for name in ["storage_upload", "storage_delete", "dervo_account_service_key"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err());
        }
    }
    #[test] fn project_picker_counts_round_trip_and_near_misses_do_not() {
        // Design sweep, surfaces (19 Sep): one read-only operation for the picker's "1 needs you" and "1 running".
        let op:Operation=serde_json::from_value(json!("projects_activity")).unwrap();
        assert_eq!(op.name(),"projects_activity");
        for op in ["project_activity","projects_activity_all","projects_counts","ProjectsActivity","projects_activity_set"] {
            assert!(serde_json::from_value::<Operation>(json!(op)).is_err(),"{op} must not round-trip");
        }
    }
}

#[cfg(test)]
mod host_recovery_tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn lines(input: &str, limit: usize) -> Vec<String> {
        let (tx,rx)=mpsc::channel();
        pump(std::io::Cursor::new(input.to_string()), tx, Arc::new(Mutex::new(Beat::default())), limit);
        rx.try_iter().collect()
    }

    /// A shell script standing in for python3, in its own folder with its own host-errors.log.
    struct Stub { dir: std::path::PathBuf }
    impl Stub {
        fn new(name: &str, script: &str) -> Self {
            let dir=std::env::temp_dir().join(format!("dervo bridge {name} {}", std::process::id()));
            let _=std::fs::remove_dir_all(&dir);
            std::fs::create_dir_all(&dir).unwrap();
            let stub=dir.join("python3");
            // The first line is a heartbeat, so a test can wait until the stub runs: a new script's first start can take
            // a second or more while macOS checks it, more so with other tests starting theirs.
            std::fs::write(&stub, format!("#!/bin/sh\necho '{{\"heartbeat\": {{\"operation\": null}}}}'\n{script}")).unwrap();
            std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).unwrap();
            Self {dir}
        }
        fn launch(&self, limits: Limits) -> Worker {
            let mut worker=Worker::launch(&self.dir.join("python3"), &self.dir.join("host.py"), &self.dir).unwrap();
            worker.limits=limits;
            let deadline=Instant::now()+Duration::from_secs(20);
            while worker.beat.lock().unwrap().at.is_none() && Instant::now()<deadline { std::thread::sleep(Duration::from_millis(10)); }
            assert!(worker.beat.lock().unwrap().at.is_some(), "the stub host started");
            worker
        }
        fn log(&self) -> String { std::fs::read_to_string(self.dir.join("host-errors.log")).unwrap_or_default() }
    }
    impl Drop for Stub { fn drop(&mut self) { let _=std::fs::remove_dir_all(&self.dir); } }
    const QUICK: Limits = Limits {answer: Duration::from_millis(400), queued: Duration::from_millis(1500), stall: Duration::from_secs(30)};
    /// A heartbeat line every 0.1 s for `tenths` tenths of a second, naming `operation`.
    fn beating(operation: &str, tenths: u32) -> String {
        format!("i=0; while [ $i -lt {tenths} ]; do echo '{{\"heartbeat\": {{\"operation\": \"{operation}\", \"seconds\": 1.0}}}}'; sleep 0.1; i=$((i+1)); done\n")
    }

    #[test] fn a_host_push_line_never_reaches_a_waiting_caller_and_goes_to_the_window() {
        // The state spine (backend/thread_states.py): an event line read as a reply would answer the wrong request.
        assert_eq!(host_event("{\"event\": {\"kind\": \"threads\", \"version\": 12}}"), Some(json!({"kind":"threads","version":12})));
        assert_eq!(host_event("{\"id\": 3, \"result\": {\"event\": 1}}"), None);
        assert_eq!(host_event("{\"event\": 5}"), None, "only an object is an event");
        let got=lines("{\"event\": {\"kind\": \"threads\", \"version\": 2}}\n{\"id\": 1, \"result\": true}\n", 1024);
        assert_eq!(got, vec!["{\"id\": 1, \"result\": true}".to_string()], "the reply alone reaches the caller");
        let seen=Arc::new(Mutex::new(Vec::new()));
        let into=seen.clone();
        on_host_event(move |event| into.lock().unwrap().push(event));
        lines("{\"event\": {\"kind\": \"threads\", \"version\": 3}}\n", 1024);
        assert!(seen.lock().unwrap().contains(&json!({"kind":"threads","version":3})), "it reached the window");
    }
    #[test] fn the_thumbnail_operations_round_trip() {
        for name in ["image_thumb","image_thumb_put"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
    }
    #[test] fn the_keep_awake_operations_round_trip() {
        for name in ["keep_awake_status","keep_awake_set"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
    }
    #[test] fn the_routing_operations_round_trip() {
        for name in ["thread_order","routing_outcome"] {
            let op:Operation=serde_json::from_value(json!(name)).unwrap();
            assert_eq!(op.name(),name);
        }
        assert!(serde_json::from_value::<Operation>(json!("routing_log")).is_err(), "the log is read on this Mac, never from the window");
    }
    #[test] fn the_thread_states_read_round_trips_and_near_misses_do_not() {
        let op:Operation=serde_json::from_value(json!("thread_states")).unwrap();
        assert_eq!(op.name(),"thread_states");
        for name in ["thread_state","thread_states_all","thread_states_set","ThreadStates"] {
            assert!(serde_json::from_value::<Operation>(json!(name)).is_err(),"{name} must not round-trip");
        }
    }
    #[test] fn a_reply_starts_with_its_id_and_an_oversized_one_keeps_it() {
        assert_eq!(leading_id("{\"id\": 7, \"result\": 1}"), Some(7));
        assert_eq!(leading_id("{\"id\":12,\"error\":\"x\"}"), Some(12));
        assert_eq!(leading_id("{\"result\": {\"id\": 3}}"), None, "an id inside the result isn't the reply's");
        assert_eq!(leading_id("{\"error\": \"Another Dervo host owns this database\"}"), None);
        let big=format!("{{\"id\": 41, \"result\": \"{}\"}}\n", "x".repeat(64));
        let got=lines(&big, 32);
        assert_eq!(leading_id(&got[0]), Some(41), "{got:?}");
        assert!(serde_json::from_str::<Value>(&got[0]).unwrap()["error"].as_str().unwrap().contains("too large"));
    }
    #[test] fn heartbeats_update_the_beat_and_never_reach_the_caller() {
        let (tx,rx)=mpsc::channel();
        let beat=Arc::new(Mutex::new(Beat::default()));
        let input="{\"heartbeat\": {\"operation\": \"task_detail\", \"id\": 3, \"started\": 1.0, \"seconds\": 12.5}}\n{\"id\": 3, \"result\": 1}\n{\"heartbeat\": {\"operation\": null}}\n";
        pump(std::io::Cursor::new(input.to_string()), tx, beat.clone(), 1024);
        assert_eq!(rx.try_iter().collect::<Vec<_>>(), vec!["{\"id\": 3, \"result\": 1}".to_string()]);
        let last=beat.lock().unwrap().clone();
        assert!(last.at.is_some());
        assert_eq!(last.operation, None, "the last beat said the host was waiting");
        let one=heartbeat("{\"heartbeat\": {\"operation\": \"task_detail\", \"seconds\": 12.5}}").unwrap();
        assert_eq!((one.operation.as_deref(), one.seconds), (Some("task_detail"), 12.5));
        assert!(heartbeat("{\"id\": 1, \"result\": {\"heartbeat\": 1}}").is_none());
    }
    #[test] fn busy_words_name_the_operation_in_plain_words() {
        assert_eq!(words("task_detail"), "task detail");
        assert_eq!(words("tick"), "background work");
        assert_eq!(words("_picked_project"), "picked project");
        assert_eq!(busy_skipped(&words("conversation_feed")), "Dervo is busy with conversation feed, so this wasn’t done. Try again in a moment.");
        assert_eq!(busy_still(&words("task_detail")), "Dervo is still busy with task detail. It may still finish, so check before you try it again.");
        assert!(clock().len()>=19, "{}", clock());
    }
    #[test] fn the_window_knows_every_line_the_bridge_answers_with() {
        // frontend/src/i18n/hostMessages.ts finds these by their English to show them in the window's language.
        let catalog:Value=serde_json::from_str(&std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"),"/frontend/src/i18n/en.json")).unwrap()).unwrap();
        for line in [busy_still("{what}"), busy_skipped("{what}"), STOPPED.into(), STUCK.into()] {
            assert_eq!(catalog[&line].as_str(), Some(line.as_str()), "{line} is in en.json");
        }
    }
    #[test] fn a_late_reply_is_dropped_and_never_read_as_the_next_answer() {
        // Request 1 is answered after 1 s, past the 0.4 s limit, while the host beats. Request 2 must get its own answer.
        let stub=Stub::new("late", &format!("read line\n{}echo '{{\"id\": 1, \"result\": \"late\"}}'\nread line\necho '{{\"id\": 2, \"result\": \"second\"}}'\nread line\n", beating("task_detail", 10)));
        let mut worker=stub.launch(QUICK);
        let first=worker.call("task_detail", json!({})).unwrap_err();
        assert_eq!(first, busy_still("task detail"));
        assert!(worker.alive(), "a slow host is kept");
        assert_eq!(worker.call("status", json!({})).unwrap(), json!("second"));
        let log=stub.log();
        assert!(log.contains("A task_detail request had no answer after 0.4 s. Answered busy and kept the host (it's on task_detail for 1 s)."), "{log}");
        assert!(log.contains("The late reply to a task_detail request (sent 1 s earlier) was dropped."), "{log}");
        drop(worker);
    }
    #[test] fn a_stray_reply_with_another_id_is_dropped() {
        let stub=Stub::new("stray", "read line\necho '{\"id\": 99, \"result\": \"stray\"}'\necho '{\"id\": 1, \"result\": \"mine\"}'\nread line\n");
        let mut worker=stub.launch(QUICK);
        assert_eq!(worker.call("status", json!({})).unwrap(), json!("mine"));
        assert!(stub.log().contains("Dropped a reply to request 99"), "{}", stub.log());
    }
    #[test] fn a_request_behind_a_slow_one_is_answered_busy_without_being_sent() {
        // The host stays on request 1 for 3 s. Request 2 comes in meanwhile and must not be written to the host.
        let stub=Stub::new("queued", &format!("read line\n{}echo '{{\"id\": 1, \"result\": \"one\"}}'\nread line\necho \"$line\" > \"$(dirname \"$0\")/seen\"\necho '{{\"id\": 2, \"result\": \"two\"}}'\nread line\n", beating("git_status", 30)));
        let mut worker=stub.launch(Limits {answer: Duration::from_millis(300), queued: Duration::from_millis(300), stall: Duration::from_secs(30)});
        assert_eq!(worker.call("task_commit", json!({})).unwrap_err(), busy_still("git status"));
        assert_eq!(worker.call("conversation_feed", json!({})).unwrap_err(), busy_skipped("git status"));
        assert!(worker.alive());
        std::thread::sleep(Duration::from_millis(3000));
        assert_eq!(worker.call("conversation_feed", json!({})).unwrap(), json!("two"));
        let seen:Value=serde_json::from_str(&std::fs::read_to_string(stub.dir.join("seen")).unwrap()).unwrap();
        assert_eq!((seen["id"].as_u64(), seen["operation"].as_str()), (Some(2), Some("conversation_feed")), "the turned-away request was never sent");
        let log=stub.log();
        assert!(log.contains("A conversation_feed request wasn't sent: the host is still on an earlier task_commit request."), "{log}");
        assert!(log.contains("1 later request was answered busy meanwhile."), "{log}");
    }
    #[test] fn a_host_whose_heartbeat_stops_is_ended_after_the_stall_limit() {
        // Beats for 0.3 s, then hangs silently.
        let stub=Stub::new("stall", &format!("read line\n{}sleep 30\n", beating("status", 3)));
        let mut worker=stub.launch(Limits {answer: Duration::from_millis(300), queued: Duration::from_millis(300), stall: Duration::from_millis(1200)});
        assert_eq!(worker.call("status", json!({})).unwrap_err(), busy_still("status"), "beating a moment ago: busy, kept");
        assert!(worker.alive());
        std::thread::sleep(Duration::from_millis(1500));
        assert_eq!(worker.call("status", json!({})).unwrap_err(), STUCK);
        assert!(!worker.alive(), "a host with no heartbeat past the limit is ended");
        let log=stub.log();
        assert!(log.contains("Ended the host: no heartbeat for 1 s while a status request waited"), "{log}");
    }
    #[test] fn a_host_that_exits_mid_request_is_reported_stopped_and_logged() {
        let stub=Stub::new("exit", "read line\nexit 3\n");
        let mut worker=stub.launch(QUICK);
        assert_eq!(worker.call("task_detail", json!({})).unwrap_err(), STOPPED);
        assert!(!worker.alive());
        let log=stub.log();
        assert!(log.contains("The host stopped while Dervo was waiting on a task_detail request (exit status: 3)."), "{log}");
    }
    /// The real backend/host.py, offline (release_checks.py's account patch), with `status` sleeping when asked. Ignored
    /// by default: it takes about 20 s and needs Python 3.13 (DERVO_TEST_PYTHON, else the framework's). Run it with
    /// `cargo test --bin dervo-local real_host -- --ignored --nocapture`.
    #[test] #[ignore] fn real_host_a_15_second_request_is_answered_busy_and_the_host_keeps_running() {
        let python=std::path::PathBuf::from(std::env::var("DERVO_TEST_PYTHON").unwrap_or("/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13".into()));
        if !python.exists() {eprintln!("no {}; skipped", python.display()); return}
        let stub=Stub::new("real host", "");
        let backend=concat!(env!("CARGO_MANIFEST_DIR"),"/backend");
        let wrapper=stub.dir.join("slow_host.py");
        std::fs::write(&wrapper, format!("import sys, time\nsys.path.insert(0, {backend:?})\nimport account_config\ncreate = account_config.create\naccount_config.create = lambda store, data_root, resources=None: create(store, data_root, resources) if resources is not None else None\nimport host\nh = host.Host(sys.argv[1])\nreal = h.dispatch\ndef dispatch(operation, args):\n    if operation == 'status' and args.get('sleep'):\n        time.sleep(args.pop('sleep'))\n    return real(operation, args)\nh.dispatch = dispatch\ntry:\n    host.serve(h)\nfinally:\n    h.close()\n")).unwrap();
        let data=stub.dir.join("data");
        std::fs::create_dir_all(&data).unwrap();
        let started=Instant::now();
        let mut worker=Worker::launch(&python, &wrapper, &data).unwrap();
        let at=|| format!("{:5.1} s", started.elapsed().as_secs_f64());
        assert!(worker.call_within("status", json!({}), STARTUP_ANSWER).is_ok());
        eprintln!("{} status answered", at());
        let slow=worker.call("status", json!({"sleep": 15})).unwrap_err();
        eprintln!("{} slow status: {slow}", at());
        assert_eq!(slow, busy_still("status"));
        assert!(worker.alive(), "the host is kept");
        let queued=worker.call("projects", json!({})).unwrap_err();
        eprintln!("{} projects while it's busy: {queued}", at());
        assert_eq!(queued, busy_skipped("status"));
        std::thread::sleep(Duration::from_secs(5));
        let after=worker.call("projects", json!({}));
        eprintln!("{} projects after it finished: {after:?}", at());
        assert!(after.is_ok(), "{after:?}");
        assert!(worker.alive());
        let log=std::fs::read_to_string(data.join("host-errors.log")).unwrap_or_default();
        eprintln!("host-errors.log:\n{log}");
        assert!(log.contains("A status request had no answer after 10 s. Answered busy and kept the host"), "{log}");
        assert!(log.contains("The late reply to a status request (sent ") && log.contains("was dropped. 1 later request was answered busy meanwhile."), "{log}");
        assert!(!log.contains("refused"), "{log}");
        drop(worker);
    }
    #[test] fn a_reply_without_an_id_still_answers_the_request() {
        // backend/host.py's startup refusal is sent before the host reads ids.
        let stub=Stub::new("no id", "read line\necho '{\"error\": \"Another Dervo host owns this database\"}'\n");
        let mut worker=stub.launch(QUICK);
        assert_eq!(worker.call("status", json!({})).unwrap_err(), "Another Dervo host owns this database");
    }
    #[test] fn oversized_reply_becomes_an_error_and_later_replies_still_arrive() {
        let got=lines("{\"result\":1}\n{\"result\":\"0123456789\"}\n{\"result\":3}\n", 16);
        assert_eq!(got.len(), 3, "{got:?}");
        assert_eq!(got[0], "{\"result\":1}");
        assert!(got[1].contains("too large"), "{got:?}");
        assert_eq!(got[2], "{\"result\":3}");
    }
    #[test] fn replies_within_the_limit_pass_unchanged() {
        // Negative control: the same reply under a limit it fits is not turned into an error.
        let got=lines("{\"result\":\"0123456789\"}\n", 64);
        assert_eq!(got, vec!["{\"result\":\"0123456789\"}".to_string()]);
    }
    #[test] fn alive_is_false_once_the_host_exits() {
        let dir=std::env::temp_dir().join(format!("dervo bridge test {}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let stub=dir.join("python3");
        std::fs::write(&stub, "#!/bin/sh\nread line\necho '{\"result\":{}}'\nread line\nexit 0\n").unwrap();
        std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut worker=Worker::launch(&stub, &dir.join("host.py"), &dir).unwrap();
        assert!(worker.alive(), "a running host is alive");
        worker.call("status", json!({})).unwrap();
        writeln!(worker.input, "{{}}").unwrap(); worker.input.flush().unwrap();
        let deadline=std::time::Instant::now()+Duration::from_secs(5);
        while worker.alive() && std::time::Instant::now()<deadline { std::thread::sleep(Duration::from_millis(20)); }
        assert!(!worker.alive(), "an exited host is not alive");
        assert!(dir.join("host-errors.log").exists(), "the host's stderr goes to a log file");
        drop(worker);
        let _=std::fs::remove_dir_all(&dir);
    }
}
