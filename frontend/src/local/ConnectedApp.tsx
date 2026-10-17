import {SidebarResize} from './SidebarResize';
import {useArchiveShortcut} from './useArchiveShortcut';
import {LocalFileLinks} from '@/shared/ui/markdown/localFileLinks';
import {ReplyActionsContext,type PreviewTarget,type ReplyActions} from '@/shared/ui/markdown/replyActions';
import {projectTerminalController} from './projectTerminalSession';
import {useProjectPermission} from './projectSettingsData';
import {invoke} from '@tauri-apps/api/core';
import {BetaPlan,releasesAllowed} from './BetaPlan';
import {BetaUpdates, Licences} from './BetaUpdates';
import {UpdatePrompt} from './UpdatePrompt';
import {BetaFeedback} from './BetaFeedback';
import {BugCapture,noteShownError,type CaptureContext,type CaptureTarget} from './BugCapture';
import {keyRan,useBugKey,useNativeMenu} from './nativeMenu';
import {instantReplies,presentResponses,type StartingReply} from './responsePresentation';
import {DervoAccount,DervoAccountNotice,accountRefreshInterval} from './DervoAccount';
import {ReleasePreview} from './ReleasePreview';
import type {LifecycleBlocker} from './LifecycleBlockers';
import {FirstRun} from './FirstRun';
import {Welcome,rememberWelcomeSeen,welcomeSeen} from './Welcome';
import {SettingsFrame,SettingsNav,isProjectPage,pageHead,useSettingsEscape,useSettingsSurface} from './DervoSettings';
import {NotificationSettings} from './NotificationSettings';
import {KeepAwakeSetting} from './KeepAwakeSetting';
import {PhoneSettings} from './PhoneSettings';
import {usePhoneLink} from './phoneLink';
import {useNotifications} from './useNotifications';
import {projectName} from './projectDisplay';
import {useDervoBrand} from './DervoBrand';
import {DervoMainHead,MoreGlyph,PanelGlyph,ProjectTerminalButton} from './DervoShell';
import {ProjectTerminal} from './ProjectTerminal';
import {WorkerQuestions,clearComposer,useComposerText,type WorkerQuestionsHandle} from './WorkerQuestions';
import {openQuestion,savedAnswer,trayQuestions} from './workerQuestionState';
import {isProjectTerminalKey} from './terminalKeys';
import {useInspectorNavigation} from './useInspectorNavigation';
import {ConversationStop,conversationStopTargets} from './ConversationStop';
import {CommandPalette,paletteItems,usePaletteShortcuts,useThreadPin,type SettingsTarget} from './CommandPalette';
import {toast} from 'sonner';
import {AccessTool,ComposerNotes,ComposerTools} from './ComposerTools';
import {armSendMotion,cancelSendMotion,playSendMotion,rememberSent,sentRowKeys,handedOverImages,type SentKey} from './sendMotion';
import {interruptionLabel} from './interruptions';
import './ChatMotion.css';
import {ContinuityMenuItems,ContinuityTool} from './SessionContinuity';
import {ModelPicker,modelProblem,useCatalog} from './ModelPicker';
import {AccountAndModelPage} from './SettingsModel';
import {AccessPage,ThreadAccessDialog} from './SettingsAccess';
import {ChecksPage} from './SettingsChecks';
import {HelpersPage} from './SettingsHelpers';
import {CloudPage} from './CloudPage';
import {GeneralPage} from './SettingsGeneral';
import {accountsFrom} from './models';
import {ProviderSuggestion,RouteWhy} from './ProviderSuggestion';
import {useTheme} from '@/shared/theme/ThemeProvider';
import {ActiveRunNotice} from './ActiveRunNotice';
import {QueueFooter,queueRowId,queueRows,sendingRow,type SendingRow} from './CodingQueue';
import {checkChoiceLabel,useCheckOverride} from './CheckOverride';
import {archiveConversation,archiveConversations,deleteArchived,deleteConversation,forgetConversations,restoreConversation} from './ConversationLifecycle';
import {useConversationReads} from './useConversationReads';
import {useAccountForeground} from './useAccountForeground';
import {DervoSidebar,projectLanding,shortPath,type ProjectAction,type Renaming} from './DervoSidebar';
import {ThreadMenuBody,useThreadUndo,withUndoRows,type MoveTarget,type ThreadMenuModel} from './ThreadMenu';
import {AddProjectDialog,useAddProject} from './AddProject';
import {saveIdea,archiveIdea} from './RoomIdeas';
import {UsagePopover} from './UsagePopover';
import {ConnectedAccounts} from './ConnectedAccounts';
import {TestersPage,testersQuery} from './TestersPage';
import {WaitlistInvites} from './WaitlistInvites';
import {TestersErrors,TestersPeople,TestersUsage,UsageSharingSettings,useUsageNotice} from './UsageSharing';
import {track,usageScreen,useScreenView} from './usage';
import {NextUp} from './NextUp';
import {feedbackCommandKind,takeFeedbackCommand} from './feedbackCommand';
import {ProjectKnowledge,KnowledgeSnapshotsForTask,useKnowledgeUnread,useKnowledgeWaiting} from './ProjectKnowledge';
import {RememberedLines} from './RememberedLine';
import {ThreadFolderCard,ThreadFolderChip,ThreadMerge} from './ThreadFolder';
import {HistoryScreen} from './History';
import type {HistoryPlace} from './runHistory';
import {Fragment,useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {WorkspaceSearch,type SearchMode} from './WorkspaceSearch';
import {ProjectCatchup} from './ProjectCatchup';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {SAFETY_MS,useThreadChanges} from './threadStates';
import {MessageTimeline,type MessageTimelineHandle} from '@/features/messages/ui/MessageTimeline';
import {LocalImageComposer,SentImages} from './LocalImageComposer';
import {warmSoon} from './warmRuns';
import {ConversationMentions,useConversationMentionNote} from './ConversationMentions';
import {useSendMessageMutation} from '@/features/messages/hooks';
import {useIdentityQuery} from '@/shared/api/hooks';
import {Sidebar,SidebarProvider,SidebarContent} from '@/shared/ui/sidebar';
import {Button} from '@/shared/ui/button';
import {AppTopChrome} from '@/app/AppTopChrome';
import {DropdownMenu,DropdownMenuTrigger,DropdownMenuContent} from '@/shared/ui/dropdown-menu';

import {GlassWindow,ThemeTiles} from './AppearanceSettings';
import {WorkspaceWelcome} from './WorkspaceWelcome';
// Design sweep, first run and brand moments: one opening screen, the project-created moment, the overview on approval.
import {OpeningScreen} from './BrandMoment';
import {ProjectMoment,momentFrom,momentShowsMeadow,overviewClientId,rememberWaiting,useOverviewOnApproval,type Moment} from './ProjectMoment';
import type {IdeaListing} from './ProjectRoom';
import {Home} from './Home';
import {ImportHistory,useHistoryOffer} from './ImportHistory';
import {lookHistory} from './historyImport';
import {ThreadPickup} from './ThreadPickup';
import {ThreadLive} from './ThreadLive';
import {FilingPrompt,filingSuggestion,dismissKey,type FilingSuggestion} from './ConversationFiling';
import {useEscapeToStop,stoppedLine} from './escapeToStop';
import {isStopping,markStopping,useStoppingVersion} from './stopping';
import {ChannelNavigationProvider} from '@/shared/context/ChannelNavigationContext';
import {loadDraftEntry,persistDraftEntry} from '@/features/messages/lib/useDrafts';
import {markShownInPlace} from '@/features/messages/ui/useMentionSendFlow.helpers';
import type {Channel} from '@/shared/api/types';
import type {TimelineMessage} from '@/features/messages/types';
import {local,organisationRequest,registerConversations,conversationSeen,usageSummary,retryOnOther,coordinate,codingRequest,CodingBusyError,type ActiveRun,type Conversation,type LocalEvent,type HarnessStatus,type DervoAccountStatus,type OrganisationState,type BetaFeedbackCategory} from './api';
import {HelperSource,PacketInspector,PacketRows,reviewChoiceLabel,useReviewOverride,type Navigate} from './CollaborationUI';
import {routeSubmit,reviewOverridePayload} from './collaboration';

import {MessageActions} from './MessageActions';
import {TurnControls,ConversationIndicator} from './TurnControls';
import {CopyTick,TaskFooter,TaskDialog,correctionTarget,handoffHoldsRetry,type CodingTask,type Detail} from './CodingTasks';
import {ChecksOffer} from './ChecksOffer';
import {RESUME_TEXT} from './GuideActions';
import {ThreadErrors,errorWords,noteError,setOpenThread} from './ErrorNotes';
import {TaskGuide,TaskGuideLoading,selectGuideTask} from './TaskGuide';
import {expectsTask,useTaskGuideVisibility} from './useTaskGuideVisibility';
import {PreviewPane,thread,ColumnSwitch,FoldSidebarWhile,usePreviewShortcuts,type ColumnView,type PreviewRequest} from './PreviewPane';
import {useCompletionDiffs} from './TaskFlow';
import {HarnessFooter,harnessRows,mergeHarnessRows,requestRowId} from './Harness';
import {AgentsPage} from './SettingsAgents';
import {t} from '@/i18n';
import {agentMention,agentMentionRows,agentMentions,agentsList,agentsNamed,agentAuthor,isAgentPubkey,SETTLED_MENTION} from './agents';
import {modelName,providerAuthor} from './providerLabel';
import {messageAvatar,type AvatarSources} from './messageAvatars';
import {photoImage} from './ProfilePhoto';
import {orderRows,taskFooterKey,taskRowId,hasHelperRequest} from './timelineRows';
import {ThreadNotes} from './ThreadNotes';
import {WorkSignedOut,useAccountCheck} from './WorkAccount';

type Profile={id:string;provider:string;identity:string;category:string;method?:string;monthly_limit_usd?:number|null};
type Project={id:string;cwd:string;name?:string|null;profile_id:string;model:string;context?:'work'|'personal'|null};
type Binding={identity:string;provider:string;model:string;visibility:string;cwd:string;profile_id:string;method?:string};
/** A message shown at send. ``quiet``: a coordination or plain chat send, which gets no "Starting …" reply row. */
type Sent=SendingRow&{quiet?:boolean};
function asChannel(c:Conversation):Channel {return {id:c.id,name:c.name,channelType:c.visibility==='private'?'dm':'stream',visibility:c.visibility==='private'?'private':'open',description:'Local conversation. No relay.',topic:null,purpose:null,memberCount:1,memberPubkeys:[],lastMessageAt:null,archivedAt:null,participants:[],participantPubkeys:[],isMember:true,ttlSeconds:null,ttlDeadline:null};}
function author(pubkey:string,providerLabel:string):string {if(pubkey==='dervo-coordinator')return 'Dervo';if(pubkey==='provider')return providerLabel;if(pubkey.startsWith('helper:'))return `${providerAuthor(pubkey.slice(7))} (coordination)`;if(isAgentPubkey(pubkey))return agentAuthor(pubkey);return 'You';}
/** The last conversation you had open in each project, kept per viewer for A3's landing. Storage failures only lose the convenience. */
const lastKey=(projectId:string)=>`dervo-last-conversation:${projectId}`;
function lastConversation(projectId:string):string|null{try{return localStorage.getItem(lastKey(projectId));}catch{return null;}}
function rememberConversation(projectId:string,channelId:string){try{localStorage.setItem(lastKey(projectId),channelId);}catch{/* Convenience only. */}}
function timeline(rows:LocalEvent[],providerLabel:string,avatars:AvatarSources):TimelineMessage[] {return rows.map(r=>({id:r.id,createdAt:r.created_at,pubkey:r.pubkey,author:author(r.pubkey,providerLabel),avatarUrl:messageAvatar(r.pubkey,avatars),time:new Date(r.created_at*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),body:r.content,depth:0,kind:r.kind,tags:r.tags,parentId:r.tags.find(t=>t[0]==='e'&&t[3]==='reply')?.[1],rootId:r.tags.find(t=>t[0]==='e'&&t[3]==='root')?.[1]}));}
/** How much this thread's runs have kept so far, as one string that changes whenever a run keeps something more. */
export function keptSignature(tasks:{id:string;runs:unknown[]}[]):string{
  let count=0,latest='';
  for(const task of tasks)for(const run of task.runs){
    const record=run&&typeof run==='object'?run as {remembered?:{at?:unknown}[];skills_proposed?:{at?:unknown}[]}:{};
    for(const item of [...(Array.isArray(record.remembered)?record.remembered:[]),...(Array.isArray(record.skills_proposed)?record.skills_proposed:[])]){
      count++;
      const at=String(item?.at??'');
      if(at>latest)latest=at;
    }
  }
  return count?`${count}:${latest}`:'';
}

export function ConnectedApp(){
  useDervoBrand();
  useAccountForeground();
  const dervoAccount=useQuery({queryKey:['dervo-account'],queryFn:()=>local<DervoAccountStatus>('dervo_account_status'),refetchInterval:q=>accountRefreshInterval(q.state.data)});
  const accountData=dervoAccount.data;
  const imageAccessReady=!!accountData&&!dervoAccount.isError&&(accountData.method!=='email_code'||(accountData.state==='signed_in'&&!!accountData.membership?.allowed&&typeof accountData.access_valid_until==='number'&&accountData.access_valid_until*1000>Date.now()));
  // A tester whose beta access is pending: agents can't start yet, so the composer says so instead of failing a send.
  const accessPending=accountData?.method==='email_code'&&accountData.state==='signed_in'&&accountData.membership?.status==='pending';
  // The project-created moment, and the overview that waits for beta access and starts by itself on approval.
  const [moment,setMoment]=useState<Moment|null>(null);
  const momentClient=useQueryClient();
  // A project made before choosing a plan waits for one too (paid plans, 8 Oct 2026): beta.17 and earlier hosts send no plan.
  useOverviewOnApproval(imageAccessReady&&accountData?.plan?.plan!=='none',(pid,cid)=>{setMoment(m=>m?.projectId===pid?{...m,state:'running',channelId:cid}:m);void momentClient.invalidateQueries({queryKey:['local-conversations',pid]});},
    (pid,detail)=>setMoment(m=>m?.projectId===pid?{...m,state:'failed',overviewDetail:detail}:m));
  // Settings is one surface over everything else (DervoSettings.tsx): the screen you came from stays underneath.
  const settings=useSettingsSurface();
  const settingsOpen=settings.page!==null;
  const [previewOpen,setPreviewOpen]=useState(false);
  const [knowledgeOpen,setKnowledgeOpen]=useState(false);
  const [bugsOpen,setBugsOpen]=useState(false);
  const [releasesOpen,setReleasesOpen]=useState(false);
  const [historyAt,setHistoryAt]=useState<{place:HistoryPlace;request:number}|null>(null);  // History (run_history.py), in the main column
  const [searchMode,setSearchMode]=useState<SearchMode>('conversation'),[searchOpen,setSearchOpen]=useState(false),[catchupAll,setCatchupAll]=useState(false);
  // One conversation-sized screen at a time: any of these takes the main panel, so the conversation steps aside.
  const panel=searchOpen||knowledgeOpen||bugsOpen||settingsOpen||releasesOpen||!!historyAt;
  // What the main panel shows under Settings: Settings covers it without closing it, so Esc finds it as you left it.
  const underlay=searchOpen||knowledgeOpen||bugsOpen||releasesOpen||!!historyAt;
  useEffect(()=>{if(!searchOpen||searchMode!=='catchup')setCatchupAll(false);},[searchOpen,searchMode]);
  const [projectId,setProject]=useState(localStorage.getItem('dervo-project')??'');
  const [terminalProjects,setTerminalProjects]=useState<Set<string>>(()=>new Set());
  const terminalOpen=terminalProjects.has(projectId);
  function showTerminal(id:string,open:boolean){setTerminalProjects(previous=>{const next=new Set(previous);if(open)next.add(id);else next.delete(id);return next;});}
  function hideTerminal(){showTerminal(projectId,false);requestAnimationFrame(()=>document.querySelector<HTMLElement>('[data-terminal-trigger]')?.focus());}
  const [channelId,storeChannel]=useState(localStorage.getItem('dervo-channel')??'');
  function setChannel(id:string){setFocusCreated(null);storeChannel(id);setHistoryAt(null);setSearchOpen(false);setKnowledgeOpen(false);setBugsOpen(false);settings.go(null);setReleasesOpen(false);}
  const [error,setError]=useState('');
  const [accessFor,setAccessFor]=useState<string|null>(null);
  const [busyRun,setBusyRun]=useState<{active:ActiveRun;message:string}|null>(null);
  // Your message shows the moment you press Enter; the host's own row replaces it once the request is recorded.
  const [sending,setSending]=useState<Sent[]>([]);
  // The send moment (#30, sendMotion.ts): the composer is measured on Enter, before the editor clears; once your row has
  // committed it travels from there into place. Each pending row plays once.
  const composerRoot=useRef<HTMLDivElement|null>(null);
  const timelineRef=useRef<MessageTimelineHandle|null>(null);
  const played=useRef(new Set<string>());
  const sentKeys=useRef(new Map<string,SentKey>());
  useLayoutEffect(()=>()=>cancelSendMotion(),[projectId,channelId]);
  useLayoutEffect(()=>{
    for(const row of sending){
      if(played.current.has(row.id))continue;
      played.current.add(row.id);
      // The list draws its rows a beat after this commit, still before paint: look once the commit has settled.
      queueMicrotask(()=>playSendMotion(row.id,composerRoot.current));
    }
    if(!sending.length)played.current.clear();
  },[sending]);
  const conversationMenuTrigger=useRef<HTMLButtonElement|null>(null);
  const pendingPanelFocus=useRef<'search'|null>(null);
  const contextTrigger=useRef<HTMLElement|null>(null);
  const projectSettingsTrigger=useRef<HTMLElement|null>(null);
  function closeContextPanel(close:()=>void){const trigger=contextTrigger.current??conversationMenuTrigger.current;close();requestAnimationFrame(()=>{if(trigger?.isConnected)trigger.focus();});}
  const [reply,setReply]=useState<TimelineMessage|null>(null);
  const [focusCreated,setFocusCreated]=useState<{projectId:string;channelId:string}|null>(null);
  const inspectors=useInspectorNavigation(projectId,channelId,()=>conversationMenuTrigger.current);
  // The per-request Independent review override: one conversation's next coding request only. It never touches plain
  // chat or coordination, resets on a conversation switch and after a successful send from the still-current conversation,
  // and stays with a failed draft. See useReviewOverride for the in-flight completion rule.
  const review=useReviewOverride(channelId);
  const check=useCheckOverride(channelId);
  const projectClient=useQueryClient();
  /** Where adding a project puts you. A folder Claude Code or Codex has already worked in is offered its history
   * (ImportHistory.tsx), and that offer only renders with no thread selected, so landing in the new Project overview
   * thread hid it: the reason Bryce added ~/dd on 21 Sep and got the overview both times. The look is the same query
   * the offer makes, under the same key, so the panel opens on what's cached here rather than reading the folder
   * twice. Nothing on disk, a host too old to answer, or a read that fails all land in the overview as before. */
  async function landAfterCreate(project:string,overviewChannel:string){
    let found=false;
    try{const look=await projectClient.fetchQuery({queryKey:['history-look',project],staleTime:60000,retry:false,queryFn:()=>lookHistory(project)});
      found=!!look&&look.conversations>0;}
    catch{found=false;}
    setChannel(found?'':overviewChannel);
  }
  const status=useQuery({queryKey:['local-status'],queryFn:()=>local<{blockers:Record<string,string>;permissions:string;codexVersion?:string;harness?:HarnessStatus}>('status')});
  const profiles=useQuery({queryKey:['local-profiles'],queryFn:()=>local<Profile[]>('profiles')});
  const projects=useQuery({queryKey:['local-projects'],queryFn:()=>local<Project[]>('projects')});
  // First run replaces the empty workspace. Every later project comes through the add-project dialog (AddProject.tsx).
  const addProject=useAddProject({projects:projects.data??[],onExisting:id=>openProject(id)});
  useEffect(()=>{if(addProject.error)setError(`The folder couldn’t be added. ${addProject.error}`);},[addProject.error]);
  const firstRunMode:'first'|null=projects.data&&projects.data.length===0&&!projects.isError?'first':null;
  // The welcome comes before first run, once per Mac (Welcome.tsx). To put it inside setup instead, move the check below.
  const [welcomed,setWelcomed]=useState(welcomeSeen);
  const selectedProject=projects.data?.find(project=>project.id===projectId);
  const hasSelectedProject=!!selectedProject;
  const projectProfile=profiles.data?.find(profile=>profile.id===selectedProject?.profile_id);
  const projectRecordsReady=hasSelectedProject&&projectProfile?.category==='personal';
  const knowledgeWaiting=useKnowledgeWaiting(projectId,selectedProject?.profile_id,projectRecordsReady);
  // Something new was kept since you last looked at Knowledge (b16/memory): the sidebar draws a dot.
  const knowledgeUnread=useKnowledgeUnread(projectId,selectedProject?.profile_id,projectRecordsReady);
  // The state spine pushes when a row moves (threadStates.ts), so the list re-reads then. The slow timer is a safety net.
  const conversations=useQuery({queryKey:['local-conversations',projectId],enabled:hasSelectedProject,refetchInterval:SAFETY_MS,queryFn:async()=>{const rows=await local<Conversation[]>('conversations',{projectId});registerConversations(rows);return rows;}});
  useThreadChanges(projectId,()=>void projectClient.invalidateQueries({queryKey:['local-conversations',projectId]}));
  // A poll that fails while the list is on screen (the host answered "busy" because a long request was ahead of it) keeps the list
  // and the page. Only a first read that fails shows the can't-load screen: the poll's own error used to swap Home for it.
  const listFailed=conversations.isError&&conversations.data===undefined;
  const current=hasSelectedProject?conversations.data?.find(c=>c.id===channelId):undefined;
  // A link in the open thread is looked for in that thread's own folder first (project_files.folders). Home has none.
  const fileThread=current?channelId:'';
  const revealFile=useCallback((path:string)=>local('project_file_reveal',{projectId,path,...thread(fileThread)}),[projectId,fileThread]);
  // A blank project (no threads, none archived) is offered its folder's Claude Code and Codex history (ImportHistory.tsx).
  // Declining leaves a normal empty project; finishing lands on Home with the threads the reader made.
  const historyOffer=useHistoryOffer(hasSelectedProject?projectId:'',hasSelectedProject&&!conversations.isError?conversations.data:undefined);
  const [historyArrived,setHistoryArrived]=useState<string|null>(null);
  const usage=useQuery({queryKey:['usage-summary'],queryFn:usageSummary,refetchInterval:30000});
  // A run starting or ending moves its thread's row: the runs count re-reads then, not up to 30 s later (polish sweep, 8 Oct).
  useThreadChanges(null,()=>void projectClient.invalidateQueries({queryKey:['usage-summary']}),(next,before)=>next?.running!==before?.running);
  const [usageOpen,setUsageOpen]=useState(false),[usageLifted,setUsageLifted]=useState<string|null>(null),[feedbackAsk,setFeedbackAsk]=useState<{n:number;category?:BetaFeedbackCategory}>({n:0});
  /** Send feedback, on the kind ``category`` names, or as you left it. */
  function openFeedback(category?:BetaFeedbackCategory){setFeedbackAsk(ask=>({n:ask.n+1,category}));}
  // ⌘⇧B, /bug, ⌘K and the menu bar's Report a Dervo Bug…: the Dervo admin gets bug capture (BugCapture.tsx), which saves to
  // a project's Bugs and ideas marked as about Dervo. Everyone else gets Send feedback with Bug picked, for the hosted inbox.
  const [captureOpen,setCaptureOpen]=useState(false);
  const admin=releasesAllowed(accountData);
  function reportBug(){if(admin){setPaletteOpen(false);setCaptureOpen(true);}else openFeedback('bug');}
  // The usage popover sits beside the sidebar's usage lines, whichever of its entry points opened it (settings/usage-b.html).
  const usageTrigger=useRef<HTMLElement|null>(null);
  // A virtual anchor: the sidebar's right edge, as tall as the usage lines, so the popover sits just outside the sidebar.
  const usageAnchor=useRef<{getBoundingClientRect:()=>DOMRect}|null>(null);
  function openUsage(trigger:HTMLElement|null){
    const lines=document.querySelector<HTMLElement>('[data-testid="dervo-sidebar-usage"]');
    usageTrigger.current=trigger;
    if(!lines||!usage.data?.accounts.length){settings.open('usage-manage',trigger);return;}
    usageAnchor.current={getBoundingClientRect:()=>{
      const box=lines.getBoundingClientRect(),edge=lines.closest('[data-testid="app-sidebar"]')?.getBoundingClientRect().right??box.right;
      return new DOMRect(edge,box.top,0,box.height);
    }};
    setUsageLifted(trigger?.dataset.profile??null);
    requestAnimationFrame(()=>setUsageOpen(true));
  }
  const [paletteOpen,setPaletteOpen]=useState(false),[searchRequest,setSearchRequest]=useState<{query:string;id:number}|null>(null),[addGuidanceRequest,setAddGuidanceRequest]=useState(0);
  const theme=useTheme();
  const binding=useQuery({queryKey:['local-binding',projectId,channelId],enabled:!!current,queryFn:()=>local<Binding>('binding',{projectId,channelId})});
  // Testers, for admins only: loaded while Settings is open, so the nav can count who's asking to join.
  const testers=useQuery({...testersQuery,enabled:settingsOpen&&accountData?.membership?.is_admin===true});
  const settingsProject=selectedProject&&selectedProject.profile_id?{...selectedProject,profile_id:selectedProject.profile_id,model:selectedProject.model}:undefined;
  const {activity,history,tasks,requests,queue,refresh:refreshConversation,loaded:loadedConversation,older:olderHistory}=useConversationReads(projectId,channelId,!!current);
  // Beta.16: a thread's questions sit on Home's tray above its composer (WorkerQuestions.tsx). The first one in the tray
  // is what the thread is waiting on; while it's open to an answer, your words in the composer answer it.
  const threadQuestions=useMemo(()=>(tasks.data??[]).flatMap(task=>task.questions??[]),[tasks.data]);
  // Something this thread's runs kept (a note, a lesson, a skill) lights Knowledge's dot at once (behaviour review S3).
  const keptHere=useMemo(()=>keptSignature(tasks.data??[]),[tasks.data]);
  // Only when this thread keeps something it hadn't when you got here: opening a thread whose runs kept things earlier
  // (a different signature from the last thread's) used to re-read both lists every click. The first read of a thread sets the mark.
  const keptMark=useRef({scope:'',signature:''});
  useEffect(()=>{
    if(!projectId||tasks.data===undefined)return;
    const scope=`${projectId}:${channelId}`,mark=keptMark.current;
    keptMark.current={scope,signature:keptHere};
    if(mark.scope!==scope||!keptHere||keptHere===mark.signature)return;
    void projectClient.invalidateQueries({queryKey:['knowledge-unread',projectId]});
    void projectClient.invalidateQueries({queryKey:['project-skills',projectId]});
  },[keptHere,projectId,channelId,tasks.data===undefined]);
  const questionsOff=!imageAccessReady||!binding.data;
  const trayFirst=current?trayQuestions(threadQuestions,projectId,channelId).questions[0]:undefined;
  const askingNow=current?openQuestion(threadQuestions,projectId,channelId,questionsOff):null;
  const questionsRef=useRef<WorkerQuestionsHandle|null>(null);
  const composerText=useComposerText(composerRoot,!!trayFirst);
  // Beta.18: before a thread's first run, its chip shows the pick the first send would make, so it reads the draft.
  const folderDraft=useComposerText(composerRoot,!!current&&!(tasks.data?.length));
  // Which threads belong to a helper agent: their account and model stay as the agent was made (the picker says so).
  // "Change model for this conversation" (thread menu, sidebar menu, ⌘K) opens that thread's picker, where its account
  // and model live now; the thread's own settings page is gone.
  const [pickerFor,setPickerFor]=useState<string|null>(null);
  // Agents you @mention (agents.ts): this project's list, and the asks this thread made with their answers. The answers
  // poll quickly only while one is still out.
  const projectAgents=useQuery({queryKey:['dervo-agents',projectId],enabled:hasSelectedProject,staleTime:15000,queryFn:()=>agentsList(projectId)});
  const mentionsHere=useQuery({queryKey:['agent-mentions',projectId,channelId],enabled:!!current&&current.visibility==='shared'&&!!projectAgents.data?.length,
    refetchInterval:query=>(query.state.data??[]).some(m=>!SETTLED_MENTION.includes(m.state))?2000:15000,queryFn:()=>agentMentions(projectId,channelId)});
  const pickerAgents=useQuery({queryKey:['collab-agents'],enabled:!!current,staleTime:30000,queryFn:()=>local<{name:string;channel_id:string}[]>('collab_agents',{})});
  // A Codex model the account no longer offers: the chip carries the one rust glyph and Send waits for another pick.
  const threadAccount=accountsFrom(profiles.data??[],usage.data?.accounts,Date.now(),'both').find(account=>account.id===binding.data?.profile_id);
  // The sidebar's "Left" line counts only the open project's side: a Work project shows its work accounts (thread-work).
  const projectSide=selectedProject?.context??'personal';
  const sideUsage=(usage.data?.accounts??[]).filter(account=>(profiles.data?.find(p=>p.id===account.profile_id)?.category??'personal')===projectSide);
  // A Work thread whose work account is signed out says so where you'd type, and never switches to a personal one.
  const workThread=selectedProject?.context==='work'&&!!binding.data;
  const workCheck=useAccountCheck(workThread?binding.data!.profile_id:'');
  const workOut=workThread&&threadAccount&&(workCheck==='signed_out'||workCheck==='identity_mismatch')?threadAccount:null;
  const threadCatalog=useCatalog(threadAccount?.profile,false);
  const modelGone=binding.data?modelProblem(threadAccount,binding.data,threadCatalog.data):null;
  const coordination=activity.data?.harness;
  const guideTask=current?selectGuideTask(tasks.data??[]):undefined;
  const guideScope=JSON.stringify([projectId,channelId]);
  // Beta.18: while a thread's tasks load, the column it will open with is held (TaskGuideLoading), so the thread doesn't
  // draw full width and then get squeezed. Held for a thread whose activity shows a run, or that showed one this session.
  const hadTask=useRef(new Set<string>());
  if(guideTask)hadTask.current.add(guideScope);
  const expectTask=!!current&&tasks.data===undefined&&!tasks.error&&(expectsTask(current.activity)||hadTask.current.has(guideScope));
  const {mainRef:guideMainRef,triggerRef:guideTrigger,visible:guideVisible,reserved:guideReserved,toggle:toggleGuide,close:closeGuide}=useTaskGuideVisibility({scope:guideScope,hasTask:!!guideTask,expectTask});
  // Panel direction C: the column shows Progress or Preview, never both. The choice belongs to this thread and resets to
  // Progress when the column closes. ⌘\ shows or hides the column; ⌥⌘P switches; esc in Preview goes back.
  const [column,setColumn]=useState<{scope:string;view:ColumnView}>({scope:'',view:'progress'});
  const columnView:ColumnView=column.scope===guideScope?column.view:'progress';
  const previewShown=guideVisible&&!!guideTask&&columnView==='preview';
  function showColumn(view:ColumnView){setColumn({scope:guideScope,view});if(!guideVisible)toggleGuide();}
  function closeColumn(){setColumn({scope:guideScope,view:'progress'});closeGuide();}
  // Beta.15, group 1: replies that do the next step. Run types a command into this project's terminal; a path or local
  // page opens in Preview (or, with no task in the thread to hang Preview on, in its own app).
  // Scoped to the thread it came from and cleared once Preview has shown it, like the column view above: a page opened
  // from one thread's reply isn't opened again when Preview next opens in another thread, or reopens here (design review, 23 Sep).
  const [previewRequest,setPreviewRequest]=useState<(PreviewRequest&{scope:string})|null>(null);
  const projectPermission=useProjectPermission(projectId,null,hasSelectedProject);
  const replyActions=useMemo<ReplyActions|null>(()=>!projectId?null:{
    commandsAllowed:projectPermission.data?.full_access?.commands===true,
    previewHere:!!guideTask,
    run:async(command,label)=>{
      if(settingsOpen)settings.close();
      showTerminal(projectId,true);
      await projectTerminalController(projectId).run(command,label);
    },
    preview:(target:PreviewTarget)=>{
      if(guideTask){setPreviewRequest({target,at:Date.now(),scope:guideScope});showColumn('preview');return;}
      const outside=target.kind==='page'?invoke('preview_open_outside',{url:target.url}):local('project_file_open',{projectId,path:target.path,...thread(fileThread)});
      void outside.catch(failure=>toast.error(failure instanceof Error?failure.message:String(failure)));
    },
    reveal:revealFile,
    openDefault:(path:string)=>local('project_file_open',{projectId,path,...thread(fileThread)}),
  },[projectId,fileThread,projectPermission.data?.full_access?.commands,settingsOpen,guideTask?.id,guideScope,guideVisible,revealFile]);
  usePreviewShortcuts(!!current&&!!guideTask,{toggleColumn:()=>{if(guideVisible)closeColumn();else toggleGuide();},switchView:()=>showColumn(guideVisible&&columnView==='preview'?'progress':'preview')});
  const guideDiffs=useCompletionDiffs(projectId,channelId,guideVisible&&!tasks.error?guideTask:undefined);
  // C5 and C6: the guide's own read of its task, shared by cache key with the Record tab, and a way to open Record from outside.
  const guideDetail=useQuery({queryKey:['task-detail',projectId,channelId,guideTask?.id??''],enabled:(guideVisible||guideTask?.state==='blocked')&&!!guideTask&&!tasks.error,
    refetchInterval:query=>query.state.data?.task.state==='working'?700:5000,
    queryFn:()=>local<Detail>('task_detail',{projectId,channelId,taskId:guideTask!.id})});
  const [guideRecordRequest,setGuideRecordRequest]=useState(0);
  function inspectTask(task:CodingTask,trigger:HTMLElement){
    // The guide's task opens in the guide's Record tab; any other task keeps the Task result window.
    if(task.id===guideTask?.id&&!tasks.error&&!activity.error){if(!guideVisible)toggleGuide();setGuideRecordRequest(value=>value+1);return;}
    inspectors.openTask(channelId,task.id,trigger);
  }
  const identityQuery=useIdentityQuery();
  const send=useSendMessageMutation(current?asChannel(current):null,identityQuery.data);
  useEffect(()=>{localStorage.setItem('dervo-project',projectId);setReply(null);},[projectId]);
  // A refusal belongs to the conversation it happened in, so it doesn't follow you to the next one.
  useEffect(()=>{localStorage.setItem('dervo-channel',channelId);setReply(null);setError('');setBusyRun(null);},[channelId]);
  // Opening a project lands on the orchestrator, its front door; without readable records, on its last conversation.
  // Decided once per opening, from the host's activity index, after the conversations and accounts have loaded.
  const landing=useRef<string|null>(projectId||null);
  useEffect(()=>{
    if(!projectId||landing.current!==projectId||!conversations.data||!profiles.data)return;
    landing.current=null;
    const next=projectLanding(conversations.data,{ready:projectRecordsReady,channelId,last:lastConversation(projectId)});
    if(next&&'screen' in next)openOrchestrator();
    else if(next)storeChannel(next.channel);
  },[projectId,conversations.data,profiles.data,projectRecordsReady]);
  useEffect(()=>{if(current)rememberConversation(projectId,current.id);},[projectId,current?.id]);
  // C10: opening a conversation clears its unread count; results that settle while it's on screen are cleared as they land.
  const onScreen=!!current&&!panel;
  const unseen=current?.activity?.unread??0;
  const seenFor=useRef('');
  useEffect(()=>{
    if(!onScreen||!current)return;
    if(seenFor.current===current.id&&unseen===0)return;
    seenFor.current=current.id;
    let live=true;
    void conversationSeen(projectId,current.id).then(()=>{if(live&&unseen>0)void conversations.refetch();}).catch(()=>{/* The count stays until the next open. */});
    return()=>{live=false;};
  },[onScreen,projectId,current?.id,unseen]);
  // A13: a clicked notification opens its conversation with the Task guide, or the project's Catch up for a summary note.
  const pendingGuide=useRef('');
  function openFromNotification({projectId:target,channelId:conversation}:{projectId:string;channelId:string}){
    if(!target)return;
    if(target!==projectId){inspectors.navigateAway();setProject(target);landing.current=null;}
    // A thread in this project comes back first if it was put away (behaviour review S1, 25 Sep). Another project's
    // list isn't loaded yet, so that one opens as before.
    if(conversation){if(target===projectId)void openThread(conversation);else setChannel(conversation);pendingGuide.current=conversation;}
    else{setChannel('');settings.go(null);setKnowledgeOpen(false);setBugsOpen(false);setSearchMode('catchup');setSearchOpen(true);}
  }
  function openHomeFromNotification(target:string){if(!target)return;if(target!==projectId){inspectors.navigateAway();setProject(target);landing.current=null;}openOrchestrator();}
  useEffect(()=>{if(pendingGuide.current&&pendingGuide.current===channelId&&guideTask){pendingGuide.current='';if(!guideVisible)toggleGuide();}},[channelId,guideTask?.id,guideVisible]);
  usePhoneLink();
  useNotifications({screen:()=>({focused:document.visibilityState==='visible'&&document.hasFocus(),projectId,channelId:onScreen?channelId:''}),onOpen:openFromNotification,onOpenHome:openHomeFromNotification});
  function feedbackCommand(text:string){const kind=feedbackCommandKind(text);if(!kind)return false;cancelSendMotion();if(kind==='bug')reportBug();else openFeedback();return true;}
  function openProject(id:string){if(id!==projectId){setChannel('');inspectors.navigateAway();landing.current=id;}setProject(id);}
  function openBlocker(blocker:LifecycleBlocker){
    if(blocker.kind==='terminal'){
      setPreviewOpen(false);openProject(blocker.projectId);landing.current=null;openOrchestrator();showTerminal(blocker.projectId,true);return;
    }
    if(!blocker.channelId)return;
    setPreviewOpen(false);landing.current=null;setProject(blocker.projectId);setChannel(blocker.channelId);
    if(blocker.taskId)inspectors.openTask(blocker.channelId,blocker.taskId,undefined,blocker.projectId);
    else{inspectors.navigateAway();pendingGuide.current=blocker.channelId;}
  }
  // A12: the jump bar is the focus-return point for screens opened from ⌘K.
  const jumpTrigger=()=>document.querySelector<HTMLElement>('.dervo-sb-jump')??document.body;
  async function stopTargets(channel:string,targets:{operation:string;args:Record<string,string>}[],rows:CodingTask[]=tasks.data??[]){
    const failures:string[]=[];
    for(const target of targets){try{await local(target.operation,{projectId,channelId:channel,...target.args});markStopping([rows.find(task=>task.id===target.args.taskId)??(target.args.taskId?{id:target.args.taskId}:null)]);}catch(failure){failures.push(String(failure));}}
    if(failures.length)setError(failures.join(' '));
  }
  // Esc stops the running task in this conversation, the same stop as the button and the palette. Anything open takes Esc
  // first; the listener runs in the window's capture phase so it sees a menu or dialog before Radix closes it.
  // A task already ending because you pressed Stop isn't stopped again (b11/visible, stopping.ts).
  useStoppingVersion();
  const unstopped=()=>conversationStopTargets(activity.data,tasks.data??[],requests.data??[]).filter(target=>!isStopping((tasks.data??[]).find(task=>task.id===target.args.taskId)));
  const stoppingHere=!!current&&(tasks.data??[]).some(task=>isStopping(task));
  async function stopHere(){
    const targets=unstopped();
    if(!targets.length)return;
    await stopTargets(channelId,targets);
    await refreshConversation();
  }
  // You stopped the newest task: its last run ended interrupted, which only a stop does. Shown however you stopped it,
  // and gone once the next message starts a new task.
  const newestTask=tasks.data?.at(-1);
  // Beta.18: a run Dervo settled as paused after a restart isn't your pause; its card says so and offers Continue.
  const stoppedByYou=!!newestTask&&newestTask.state!=='working'&&(newestTask.runs.at(-1) as {state?:string}|undefined)?.state==='interrupted'
    &&(newestTask.runs.at(-1) as {end_cause?:string}|undefined)?.end_cause!=='restart_paused';
  // A question in the tray takes Esc, as on Home: it hides or brings back the choices. Stop is still the button.
  useEscapeToStop(()=>({running:!!current&&conversationStopTargets(activity.data,tasks.data??[],requests.data??[]).length>0,inRoom:!current,
    paletteOpen,panelOpen:panel||usageOpen||captureOpen||!!addProject.staged||previewShown||!!trayFirst,replying:!!reply,stop:stopHere}));
  const threadPin=useThreadPin(projectId,current?.id??'',projectRecordsReady,setError);
  const paletteActions={
    openConversation:(id:string)=>setChannel(id),
    orchestrator:()=>openOrchestrator(),
    catchUp:()=>openOrchestrator(),  // Catch up is Home now
    knowledge:()=>projectAction('knowledge',jumpTrigger()),
    history:()=>projectAction('history',jumpTrigger()),
    projectSettings:()=>settings.open('palette-project',jumpTrigger()),
    create:(isPrivate:boolean)=>openCreate(isPrivate),
    toggleGuide:()=>toggleGuide(),
    retryHere:()=>{if(guideTask)void local('task_continue',{projectId,channelId,taskId:guideTask.id,text:'Please try that again.'}).then(()=>refreshConversation()).catch(failure=>setError(String(failure)));},
    retryOther:()=>{if(guideTask)void retryOnOther(projectId,channelId,guideTask.id).then(()=>refreshConversation()).catch(failure=>setError(String(failure)));},
    stopCurrent:()=>void stopHere(),
    stopConversation:(id:string)=>void local<CodingTask[]>('tasks',{projectId,channelId:id}).then(rows=>stopTargets(id,conversationStopTargets(undefined,rows,[]),rows)).catch(failure=>setError(String(failure))).finally(()=>void conversations.refetch()),
    changeModel:()=>{if(channelId)setPickerFor(channelId);else settings.open('change-model',jumpTrigger());},
    usage:()=>openUsage(jumpTrigger()),
    addGuidance:()=>{projectAction('knowledge',jumpTrigger());setAddGuidanceRequest(value=>value+1);},
    toggleTheme:()=>theme.applyAppearance({theme:theme.isDark?'buzz':'buzz-dark',accent:theme.accentColor,followSystem:false}),
    feedback:()=>openFeedback(),
    reportBug:()=>reportBug(),
    bugs:()=>projectAction('bugs',jumpTrigger()),
    workspaceSettings:()=>settings.open('palette-settings',jumpTrigger()),
    settingsPage:(page:SettingsTarget)=>{settings.open('palette-settings',jumpTrigger());settings.go(page);},
    overview:()=>void askOverview(),
    search:(query:string)=>{projectAction('search',jumpTrigger());setSearchRequest({query,id:Date.now()});},
    pin:threadPin.pin,
  };
  /** "Ask for a project overview" from ⌘K (moved from Manage projects). The palette has closed by the time it can fail,
   * so a failure is the one toast here, with Try again (surfaces, toast rules). */
  async function askOverview(){
    const target=projectId,key='dervo-explore-pending:'+target;
    let clientId:string;
    try{clientId=localStorage.getItem(key)??crypto.randomUUID();localStorage.setItem(key,clientId);}catch{clientId=crypto.randomUUID();}
    try{
      const result=await local<{channelId:string;taskId:string|null;detail?:string}>('project_explore',{projectId:target,clientId});
      if(!result.taskId)throw new Error(result.detail??'Try again in a moment.');
      try{localStorage.removeItem(key);}catch{/* a convenience */}
      await projectClient.invalidateQueries({queryKey:['local-conversations',target]});
      if(target===projectId)setChannel(result.channelId);
    }catch(failure){
      toast('The overview couldn’t start.',{description:failure instanceof Error?failure.message:String(failure),duration:Infinity,action:{label:'Try again',onClick:()=>void askOverview()}});
    }
  }
  const palette=paletteItems({conversations:hasSelectedProject?conversations.data??[]:[],currentId:current?.id??'',hasProject:hasSelectedProject,ready:projectRecordsReady,
    guide:guideTask&&!tasks.error?{visible:guideVisible}:null,canStopCurrent:!!current&&conversationStopTargets(activity.data,tasks.data??[],requests.data??[]).length>0,dark:theme.isDark,
    retryHere:!!guideTask&&guideDetail.data?.task.id===guideTask.id&&!!guideDetail.data.retry?.available&&!guideDetail.data.rebound&&!handoffHoldsRetry(guideTask),
    retryOther:guideTask?.suggest?.other&&!guideTask.suggest.taken&&!guideTask.suggest.automatic?guideTask.suggest.other.label:null,pinned:threadPin.pinned},paletteActions);
  usePaletteShortcuts({toggle:()=>setPaletteOpen(open=>!open),catchUp:paletteActions.catchUp,knowledge:paletteActions.knowledge,ready:projectRecordsReady});
  // The shortcuts the menus show (surfaces, interactions.md): ⌘, Settings, ⌘O Add a project, ⌘N New thread, ⌘F Search
  // in thread. None of them is on the native menu bar, which has only the standard Edit and Window items.
  const shortcuts=useRef({settings:()=>{},add:()=>{},create:()=>{},search:null as null|(()=>void)});
  shortcuts.current={
    settings:()=>settings.open(selectedProject?'project-picker':'account-menu',document.querySelector<HTMLElement>(selectedProject?'.dervo-sb-picker':'.dervo-sb-me')),
    add:()=>{if(!firstRunMode)openAddProject();},
    create:()=>{if(projectRecordsReady)openCreate(false);},
    search:current&&binding.data&&!settingsOpen?()=>{contextTrigger.current=conversationMenuTrigger.current;setKnowledgeOpen(false);setBugsOpen(false);setSearchMode('conversation');setSearchOpen(true);
      requestAnimationFrame(()=>document.querySelector<HTMLElement>('[data-panel-focus="search"]')?.focus());}:null,
  };
  useEffect(()=>{
    function onKey(event:KeyboardEvent){
      if(event.defaultPrevented||event.isComposing||event.repeat||isProjectTerminalKey(event))return;
      if(!(event.metaKey||event.ctrlKey)||event.altKey||event.shiftKey)return;
      const key=event.key.toLowerCase(),run=shortcuts.current;
      if(key===','){event.preventDefault();keyRan('settings');run.settings();}
      else if(key==='o'){event.preventDefault();run.add();}
      else if(key==='n'){event.preventDefault();run.create();}
      else if(key==='f'&&run.search){event.preventDefault();run.search();}
    }
    window.addEventListener('keydown',onKey);
    return()=>window.removeEventListener('keydown',onKey);
  },[]);
  // ⌘⇧B, before the composer's Bold can see it (nativeMenu.ts).
  useBugKey(reportBug);
  // The menu bar (src/menu.rs): each of Dervo's own items runs what its key runs.
  useNativeMenu(action=>{
    if(action==='settings')shortcuts.current.settings();
    else if(action==='check-updates'){settings.open('palette-settings',null);settings.go('updates');}
    else if(action==='report-bug')reportBug();
    else openFeedback();
  });
  async function act(fn:()=>Promise<void>){setError('');try{await fn();}catch(e){setError(String(e));}}
  const submit=useCallback(async (text:string,mentions:string[],media?:string[][],images:string[]=[])=>{
    setError('');setBusyRun(null);
    const target=current;
    if(!target||!binding.data)throw new Error('Wait for the account binding before queueing.');
    // A message that names one of this project's agents goes to those agents, each in its own thread, and their answers
    // come back here. The thread's own agent doesn't get it as well: you were talking to the agent.
    // Until this project's agents are known, an @name can't be told apart from a thread helper, and guessing could send
    // it to an agent with more access than the one you named. The draft stays.
    let knownAgents=projectAgents.data;
    if(/(?<![\w@.])@[A-Za-z]/.test(text)&&!knownAgents){
      knownAgents=(await projectAgents.refetch()).data;
      if(!knownAgents)throw new Error(t('Dervo couldn’t load this project’s agents, so it can’t tell who you named. Send again in a moment.'));
    }
    if(agentsNamed(text,knownAgents??[]).length){
      if(images.length||media?.length)throw new Error(t('Agents take text for now. Send the image without an @mention.'));
      timelineRef.current?.scrollToBottomOnNextUpdate();
      const pending:Sent={...sendingRow(target.id,text,Date.now()),quiet:true};rememberSent(sentKeys.current,text,`sent:${pending.id}`);setSending(rows=>[...rows,pending]);
      try{await agentMention(target.project_id,target.id,text);await mentionsHere.refetch();setReply(null);}catch(e){setError(String(e));throw markShownInPlace(e);}
      finally{setSending(rows=>rows.filter(row=>row.id!==pending.id));}
      return;
    }
    // Exactly one path per message, decided before anything is sent. With coding allowed and collaboration on here, an
    // @name must be an agent the policy lets this conversation ask (the host attaches the allowed helpers to the owner
    // task at request time); an unknown @name is refused and the draft stays. With collaboration off, an @handle keeps
    // the older text-only coordination path exactly as before.
    // A message sent the moment a thread opens, before its first read lands, waits for that read: without it the thread's
    // mode is unknown and the message went to plain chat, which a coding thread's account refuses (critic, 9 Oct). The
    // draft stays if the read fails.
    const here=activity.data?{activity:activity.data,harness:coordination,tasks:tasks.data}:await loadedConversation().then(snapshot=>snapshot?.activity?{activity:snapshot.activity,harness:snapshot.activity.harness,tasks:snapshot.tasks}:null);
    if(!here)throw new Error(t('Dervo is still loading this thread, so it can’t tell where to send this yet. Send again in a moment.'));
    const route=(()=>{try{return routeSubmit(text,here.activity.coding,here.activity.collaboration,here.harness);}catch(e){setError(String(e));throw e;}})();
    if(images.length&&route.path!=='coding')throw new Error('Images work with your project agent. Send this without an @mention, or open a project conversation with reading or coding access.');
    if(media?.length)throw new Error('This attachment type isn’t supported here yet. Use the paperclip to attach an image.');
    if(route.path==='coding'){
      // Coding is allowed in this project: the message starts coding through the existing task lifecycle, with the granted
      // files only, no form and no per-message approval.
      // The override is resolved here, before anything is sent: on without a configured reviewer is an error that keeps the draft.
      // A completion that lands after a conversation switch must not reset the newer conversation's reply target or override.
      timelineRef.current?.scrollToBottomOnNextUpdate();
      const pending=sendingRow(target.id,text,Date.now(),images);rememberSent(sentKeys.current,text,`sent:${pending.id}`);setSending(rows=>[...rows,pending]);
      // After Ask for changes, what you type next is that correction: it carries on the same task, as the result's own
      // field would, instead of starting a second one and stranding the first (polish review, 9 Oct).
      const waiting=correctionTarget(here.tasks??[],images.length>0);
      if(waiting){
        try{await local('task_continue',{projectId:target.project_id,channelId:target.id,taskId:waiting.id,text});if(await refreshConversation())setReply(null);}catch(e){setError(String(e));throw markShownInPlace(e);}
        finally{setSending(rows=>rows.filter(row=>row.id!==pending.id));}
        return;
      }
      try{await codingRequest(target.id,text,reviewOverridePayload(review.value,here.activity.collaboration?.review),images,check.payload());check.settle(target.id);if(await refreshConversation()&&review.settle(target.id))setReply(null);}catch(e){if(e instanceof CodingBusyError)setBusyRun({active:e.active,message:e.message});else setError(String(e));throw markShownInPlace(e);}
      finally{setSending(rows=>rows.filter(row=>row.id!==pending.id));}
      return;
    }
    // Beta.18: every send shows your message at once, as coding does, and the saved row takes it over. These two get no
    // "Starting Claude Code" row under it (``quiet``): a helper or the chat answers them, not the project agent.
    timelineRef.current?.scrollToBottomOnNextUpdate();
    const pending:Sent={...sendingRow(target.id,text,Date.now()),quiet:true};rememberSent(sentKeys.current,text,`sent:${pending.id}`);setSending(rows=>[...rows,pending]);
    try{
      const helper=here.harness;
      if(route.path==='coordination'&&helper?.state==='available'&&helper.helper){
        // The host resolved this conversation's own helper: the message goes to coordination (text only), not to the chat queue.
        try{await coordinate(target.id,text,helper.helper);if(await refreshConversation())setReply(null);}catch(e){setError(String(e));throw markShownInPlace(e);}
        return;
      }
      try{await send.mutateAsync({targetChannel:asChannel(target),channelId:target.id,content:text,mentionPubkeys:mentions,mediaTags:media,parentEventId:reply?.id??null});if(await refreshConversation())setReply(currentReply=>currentReply?.id===reply?.id?null:currentReply);}catch(e){setError(String(e));throw markShownInPlace(e);}
    }finally{setSending(rows=>rows.filter(row=>row.id!==pending.id));}
  },[projectAgents.data,projectAgents.refetch,mentionsHere.refetch,current,binding.data,activity.data,tasks.data,loadedConversation,coordination,review.value,review.settle,reply?.id,send.mutateAsync,refreshConversation]);
  const conversationMentionNote=useConversationMentionNote(conversations.data??[]);
  const shownRows=sentRowKeys(presentResponses(timeline(mergeHarnessRows(orderRows(history.data??[],tasks.data??[],channelId,current?.origin==='overview'),[...harnessRows(requests.data??[],channelId),...agentMentionRows(mentionsHere.data??[],channelId),...queueRows(queue.data??[],channelId),...sending.filter(row=>row.tags[0]?.[1]===channelId)]),providerAuthor(binding.data?.provider),{photo:photoImage(accountData),provider:binding.data?.provider}),history.data??[],tasks.data??[],activity.error?undefined:activity.data,binding.data?.provider),sentKeys.current);
  // The reply row under a message you just sent (16.2 item 1): drawn while the host records it, then taken over by the
  // real reply. Only when a new reply is starting. With a run working or waiting here the message joins it or queues.
  const handedReplies=useRef(new Map<string,string>());
  const replyBusy=!!activity.data?.active||(tasks.data??[]).some(task=>task.state==='working'||task.state==='needs_permission')||(queue.data??[]).some(item=>item.state==='queued');
  const startingReply=useMemo<StartingReply|null>(()=>{
    const provider=binding.data?.provider;
    if(replyBusy||!provider)return null;
    // Auto can hand the request to the other provider, so the placeholder names nobody it might not be.
    const either=activity.data?.coding?.conversation_route?.mode==='auto'&&!!activity.data.coding.conversation_route.alternative;
    return {author:providerAuthor(provider),avatarUrl:messageAvatar('provider',{provider}),label:either?'Starting':`Starting ${providerAuthor(provider)}`,model:either?undefined:modelName(binding.data?.model)||undefined};
  },[replyBusy,binding.data?.provider,binding.data?.model,activity.data?.coding?.conversation_route]);
  const messages=instantReplies(shownRows,sending.filter(row=>row.tags[0]?.[1]===channelId&&!row.quiet),handedReplies.current,startingReply);
  // ↑ in an empty box brings your last message back to change and send again (DvComposer).
  const lastOwnMessage=[...messages].reverse().find(message=>message.pubkey==='local-user'&&!message.pending)?.body;
  // The chip a finished reply folds into opens Progress (replyTrail.ts).
  useEffect(()=>{
    const open=()=>{if(!guideVisible)toggleGuide();};
    window.addEventListener('dervo-open-progress',open);
    return()=>window.removeEventListener('dervo-open-progress',open);
  },[guideVisible,toggleGuide]);
  // A message sent while the agent worked: the composer says whether it joined the run or waits in the queue.
  const workingTask=(tasks.data??[]).find(task=>task.state==='working'&&task.origin!=='collaboration');
  // A steer is said once, under the message it's about (below). Only the queue's own note, which has no message, sits in the composer.
  const steerLine=interruptionLabel(workingTask,queue.data??[]);
  const interruption=steerLine?.messageIndex===undefined?steerLine:null;
  // Send becomes Stop while something runs here and nothing is typed (#30). Same stop as Esc, the button and the palette.
  const running=!!current&&unstopped().length>0;
  // Edit on a sent message puts its words back in the composer (MessageActions).
  const putBack=useRef<((text:string)=>boolean)|null>(null);
  const insertReady=useCallback((insert:((text:string)=>boolean)|null)=>{putBack.current=insert;},[]);
  const footers:Record<string,React.ReactNode>=Object.fromEntries((history.data??[]).filter(row=>row.pubkey==='local-user').map(row=>[row.id,
    <TurnControls key={row.id} row={row} projectId={projectId} channelId={channelId} binding={binding.data} refresh={refreshConversation}/>
  ]));
  // Cross-channel navigation: a handoff row or packet links to the destination conversation and helper task, and a helper task links back to its owner.
  async function conversationGone(id:string){if(id===channelId){inspectors.navigateAway();setChannel('');}projectClient.removeQueries({predicate:query=>query.queryKey.includes(id)});await conversations.refetch();}
  // #names in messages are this project's conversations: a pill opens one in place, since Dervo has no channel route.
  const mentionChannels=useMemo<Channel[]>(()=>(conversations.data??[]).map(conversation=>({id:conversation.id,name:conversation.name,
    channelType:'stream',visibility:conversation.visibility==='private'?'private':'open',description:'',topic:null,purpose:null,memberCount:1,
    memberPubkeys:[],lastMessageAt:null,archivedAt:null,participants:[],participantPubkeys:[],isMember:true,ttlSeconds:null,ttlDeadline:null}) as Channel),
    [conversations.data]);
  // Stable for the navigation provider, and always the newest openThread (it reads the current thread list).
  const openThreadNow=useRef(openThread);openThreadNow.current=openThread;
  const openMentioned=useCallback((id:string)=>{inspectors.navigateAway();void openThreadNow.current(id);},[]);
  // After a send that names a conversation filed under an idea, offer to file this one beside it. Same cache as the room.
  const ideaListing=useQuery({queryKey:['project-ideas',projectId,selectedProject?.profile_id??''],enabled:hasSelectedProject&&!!selectedProject,
    queryFn:()=>local<IdeaListing>('ideas_list',{projectId,profileId:selectedProject!.profile_id})});
  const [filing,setFiling]=useState<{channelId:string;suggestion:FilingSuggestion}|null>(null);
  const [filingBusy,setFilingBusy]=useState(false);
  useEffect(()=>{setFiling(value=>value&&value.channelId===channelId?value:null);},[channelId]);
  function offerFiling(text:string){
    if(!current)return;
    const listing=projectClient.getQueryData<IdeaListing>(['project-ideas',projectId,selectedProject?.profile_id??''])??ideaListing.data;
    const suggestion=filingSuggestion({text,channelId:current.id,visibility:current.visibility,conversations:conversations.data??[],listing});
    setFiling(suggestion?{channelId:current.id,suggestion}:null);
  }
  async function fileHere(){
    if(!filing||!selectedProject)return;
    setFilingBusy(true);
    try{const listing=await local<IdeaListing>('ideas_assign',{projectId,profileId:selectedProject.profile_id,channelId:filing.channelId,ideaId:filing.suggestion.ideaId});
     projectClient.setQueryData(['project-ideas',projectId,selectedProject.profile_id],listing);setFiling(null);void conversations.refetch();}
    catch(failure){setError(String(failure));}
    finally{setFilingBusy(false);}
  }
  function notNow(){
    if(!filing)return;
    try{localStorage.setItem(dismissKey(filing.channelId,filing.suggestion.ideaId),'1');}catch{}
    setFiling(null);
  }
  // Thread menus (ThreadMenu.tsx): rename in the row, move to a workstream, archive with Undo in the row it left, and
  // delete confirmed in the menu. Archive, move and rename are tier 1: they happen at once and ⌘Z or Undo takes them back.
  const undo=useThreadUndo();
  const [renaming,setRenaming]=useState<Renaming|null>(null),[arrived,setArrived]=useState<string|null>(null);
  useEffect(()=>{undo.clear();setRenaming(null);},[projectId]);
  useEffect(()=>{if(!arrived)return;const timer=setTimeout(()=>setArrived(null),1200);return()=>clearTimeout(timer);},[arrived]);
  const message=(failure:unknown)=>failure instanceof Error?failure.message:String(failure);
  const hostIndex=(conversation:Conversation)=>Math.max(0,(conversations.data??[]).findIndex(c=>c.id===conversation.id));
  const archivingNow=useRef(false);
  async function archive(conversation:Conversation){
    if(archivingNow.current)return;
    archivingNow.current=true;
    setError('');undo.settle(conversation.id);
    const index=hostIndex(conversation);
    try{
      await archiveConversation(conversation);
      await projectClient.invalidateQueries({queryKey:['archived-conversations',conversation.project_id]});
      undo.offer({id:conversation.id,kind:'archived',label:'Archived',conversation,index,fromGroup:conversation.organisationGroup??null,undo:async()=>{
        await restoreConversation(conversation);
        await projectClient.invalidateQueries({queryKey:['archived-conversations',conversation.project_id]});
        await conversations.refetch();setArrived(conversation.id);
      }});
      await conversationGone(conversation.id);
    }catch(failure){undo.fail(conversation.id,`Couldn’t archive. ${message(failure)}`);}
    finally{archivingNow.current=false;}
  }
  useArchiveShortcut(!!current&&!panel&&!running&&!current.activity?.running,()=>{if(current)void archive(current);});
  /** The sidebar's selection, archived in one go (beta.16.1 item 3). The first row it left says how many went, with one
   * Undo for all of them. A thread the host kept (running, queued, an agent's) stays in the list and says why. */
  async function archiveMany(rows:Conversation[]){
    if(archivingNow.current||!rows.length)return;
    archivingNow.current=true;
    setError('');for(const row of rows)undo.settle(row.id);
    const index=Math.min(...rows.map(hostIndex));
    try{
      const result=await archiveConversations(projectId,rows.map(row=>row.id));
      await projectClient.invalidateQueries({queryKey:['archived-conversations',projectId]});
      for(const kept of result.refused)undo.fail(kept.id,rows.find(row=>row.id===kept.id)?.activity?.running?'It’s running, so it stayed. Stop it first.':`It stayed. ${kept.reason}`);
      const went=rows.filter(row=>result.ids.includes(row.id));
      if(went.length){
        const [first,...others]=went;
        undo.offer({id:first.id,kind:'archived',label:went.length===1?'Archived':`Archived ${went.length} threads`,conversation:first,others,index,fromGroup:first.organisationGroup??null,undo:async()=>{
          for(const row of went)await restoreConversation(row);
          await projectClient.invalidateQueries({queryKey:['archived-conversations',projectId]});
          await conversations.refetch();setArrived(first.id);
        }});
        if(went.some(row=>row.id===channelId)){inspectors.navigateAway();setChannel('');}
        for(const row of went)projectClient.removeQueries({predicate:query=>query.queryKey.includes(row.id)});
        await conversations.refetch();
      }
    }catch(failure){for(const row of rows)undo.fail(row.id,`Couldn’t archive. ${message(failure)}`);}
    finally{archivingNow.current=false;}
  }
  const ideaScope=selectedProject?{projectId,profileId:selectedProject.profile_id}:null;
  const ideaOf=(id:string)=>ideaListing.data?.ideas.find(idea=>idea.conversations.some(c=>c.id===id))??null;
  async function assign(channel:string,ideaId:string|null){
    const listing=await local<IdeaListing>('ideas_assign',{...ideaScope!,channelId:channel,ideaId});
    projectClient.setQueryData(['project-ideas',projectId,ideaScope!.profileId],listing);
    return listing;
  }
  async function moveThread(conversation:Conversation,target:MoveTarget){
    if(!ideaScope)return;
    undo.settle(conversation.id);
    const index=hostIndex(conversation),before=ideaOf(conversation.id);
    try{
      let toId:string|null,toName:string|null,made:string|null=null;
      if('create' in target){
        const listing=await saveIdea(ideaScope,null,{name:target.create});
        const idea=listing.ideas.find(i=>i.name.toLowerCase()===target.create.trim().toLowerCase());
        if(!idea)throw new Error('The new workstream wasn’t saved.');
        made=idea.id;toId=idea.id;toName=idea.name;
      }else{toId=target.ideaId;toName=target.ideaId?target.name:null;}
      await assign(conversation.id,toId);
      await conversations.refetch();setArrived(conversation.id);
      undo.offer({id:conversation.id,kind:'moved',label:`Moved to ${toName??'Not filed yet'}`,conversation,index,fromGroup:conversation.organisationGroup??null,toGroup:toName,undo:async()=>{
        const listing=await assign(conversation.id,before?.id??null);
        // Undo takes the thread out of a workstream it made; the empty workstream is archived with it.
        const left=made?listing.ideas.find(i=>i.id===made):undefined;
        if(left&&!left.conversations.length)projectClient.setQueryData(['project-ideas',projectId,ideaScope.profileId],await archiveIdea(ideaScope,left));
        await conversations.refetch();setArrived(conversation.id);
      }});
    }catch(failure){undo.fail(conversation.id,`Couldn’t move it. ${message(failure)}`);}
  }
  async function renameThread(conversation:Conversation,name:string|null){
    if(name===null){setRenaming(null);return;}
    setRenaming({id:conversation.id,busy:true});
    try{
      const state=await organisationRequest<OrganisationState>('organisation_get',{projectId,channelId:conversation.id});
      const saved=await organisationRequest<OrganisationState>('organisation_set',{projectId,channelId:conversation.id,expectedRevision:state.revision,name,group:state.group,nameLocked:true,groupLocked:state.groupLocked});
      setRenaming(null);
      await conversations.refetch();
      undo.offer({id:conversation.id,kind:'renamed',label:'Renamed',conversation,index:0,fromGroup:null,undo:async()=>{
        await organisationRequest('organisation_undo',{projectId,channelId:conversation.id,expectedRevision:saved.revision});
        await conversations.refetch();
      }});
    }catch(failure){setRenaming({id:conversation.id,error:`Couldn’t rename it. ${message(failure)}`});}
  }
  async function stopThread(conversation:Conversation){
    const rows=await local<CodingTask[]>('tasks',{projectId,channelId:conversation.id});
    for(const target of conversationStopTargets(undefined,rows,[]))await local(target.operation,{projectId,channelId:conversation.id,...target.args});
    await conversations.refetch();
  }
  /** "Delete all archived": the rows the fold showed, after its confirm. What they freed comes back for the fold to say. */
  async function deleteAllArchived(rows:Conversation[]){
    const result=await deleteArchived(projectId,rows.map(row=>row.id));
    forgetConversations(result.ids);
    for(const id of result.ids){undo.settle(id);projectClient.removeQueries({predicate:query=>query.queryKey.includes(id)});}
    await conversations.refetch();
    return result;
  }
  async function deleteThread(conversation:Conversation){
    await deleteConversation(conversation);
    forgetConversations([conversation.id]);
    undo.settle(conversation.id);
    await projectClient.invalidateQueries({queryKey:['archived-conversations',conversation.project_id]});
    await conversationGone(conversation.id);
  }
  const threadMenu:ThreadMenuModel={
    openBlocker,
    workstreams:ideaListing.data?ideaListing.data.ideas.map(idea=>({id:idea.id,name:idea.name,count:idea.conversations.length})):null,
    filedUnder:c=>ideaOf(c.id)?.name??null,
    cannotMove:c=>c.visibility==='private'?'Private threads stay unfiled':c.profile_id!==selectedProject?.profile_id?'It’s on another account, so it stays unfiled':ideaListing.isError?'Workstreams couldn’t load':null,
    rename:c=>{undo.clear();setRenaming({id:c.id});},
    move:(c,target)=>void moveThread(c,target),
    archive:c=>void archive(c),
    remove:deleteThread,
    stop:stopThread,
  };
  const navigate:Navigate={openChannel:id=>{inspectors.navigateAway();void openThread(id);},openTask:(id,taskId,trigger)=>{void openThread(id).then(opened=>{if(opened)inspectors.openTask(id,taskId,trigger);});},inspectPacket:inspectors.openPacket};
  /** Opening a thread from History, search, catch-up or a link (b16/lifecycle, Bryce 25 Sep: an archived thread comes
   * back when it's opened, and the host won't open one that's still archived). One the sidebar lists opens at once.
   * Any other is restored first. Resolves true once it's open. */
  async function openThread(id:string):Promise<boolean>{
    if(!id||(conversations.data??[]).some(conversation=>conversation.id===id)){setChannel(id);return true;}
    try{
      await restoreConversation({id,project_id:projectId});
      await projectClient.invalidateQueries({queryKey:['archived-conversations',projectId]});
      await conversations.refetch();setArrived(id);
    }catch(failure){
      // The list may simply not have had it yet: open it if it's there now, and say so if it's still archived.
      const fresh=await conversations.refetch();
      if(!fresh.data?.some(conversation=>conversation.id===id)){setError(`That thread couldn’t be brought back. ${failure instanceof Error?failure.message:String(failure)}`);return false;}
    }
    setChannel(id);
    return true;
  }
  const collaborationOf=(task:CodingTask,full:boolean)=><>{full&&task.origin==='collaboration'&&<HelperSource task={task} projectId={projectId} channelId={channelId} conversations={conversations.data} navigate={navigate} full={full}/>}{task.collaboration&&['mentions','automatic'].includes(task.collaboration.mode)&&<PacketRows task={task} projectId={projectId} channelId={channelId} conversations={conversations.data} navigate={navigate}/>}</>;
  const latestConversationTask=[...(tasks.data??[])].reverse().find(task=>task.origin==='conversation')?.id;
  // One key and one shape for the pictures under your message, from the moment it's sent through the saved row to its
  // task's own, so they're never drawn twice or remounted at a switch.
  const sentImages=(ids:string[],existing?:React.ReactNode)=><><SentImages key={`images:${projectId}:${channelId}:${ids.join(',')}`} projectId={projectId} channelId={channelId} ids={ids}/>{existing}</>;
  const sendingHere=sending.filter(row=>row.tags[0]?.[1]===channelId);
  for(const row of sendingHere){
    if(row.images?.length)footers[row.id]=sentImages(row.images,footers[row.id]);
  }
  const withImages=new Set<string>();
  for(const task of tasks.data??[]){
    footers[taskFooterKey(task)]=<TaskFooter key={task.id} task={task} onResume={task.id===latestConversationTask?resumeHere:undefined} onRunIt={()=>setPreviewOpen(true)} onOpenHome={openOrchestrator} guideVisible={guideVisible&&guideTask?.id===task.id&&!tasks.error&&!activity.error} projectId={projectId} channelId={channelId} onInspect={trigger=>inspectTask(task,trigger)} extra={<>{collaborationOf(task,false)}<RouteWhy task={task}/>{!(guideVisible&&guideTask?.id===task.id)&&<ProviderSuggestion task={task} projectId={projectId} channelId={channelId} policy={activity.data?.coding?.route} latest={task.id===latestConversationTask} conversationProfileId={binding.data?.profile_id} resetsAt={usage.data?.accounts.find(account=>account.profile_id===task.route?.profile_id)?.resets_at} refresh={refreshConversation}/>}{task.id===latestConversationTask&&<ChecksOffer task={task} projectId={projectId} coding={activity.data?.coding} refresh={refreshConversation}/>}{task.id===latestConversationTask&&<ThreadMerge projectId={projectId} channelId={channelId} onChanged={refreshConversation} moved={`${task.state}:${task.commit?.sha??''}:${task.commit?.reverted?'reverted':''}`}/>}</>}/>;
    if(task.images?.length){
      const imageKey=taskRowId(task,0),existing=footers[imageKey];withImages.add(imageKey);
      footers[imageKey]=sentImages(task.images,existing);
    }
    if(hasHelperRequest(task)){
      const requestKey=taskRowId(task,0),existing=footers[requestKey];
      footers[requestKey]=<><div className="dervo-helper-request"><HelperSource task={task} projectId={projectId} channelId={channelId} conversations={conversations.data} navigate={navigate}/></div>{existing}</>;
    }
  }
  for(const [id,images] of handedOverImages(shownRows,sendingHere))if(!withImages.has(id))footers[id]=sentImages(images,footers[id]);
  // What each run kept (b16/memory, RememberedLine.tsx), under the last reply that run wrote. It draws nothing when the
  // run kept nothing.
  for(const task of tasks.data??[]){
    const last=new Map<string,number>();
    task.messages.forEach((message,index)=>{if(message.role==='assistant'&&message.run_id)last.set(message.run_id,index);});
    for(const [runId,index] of last){
      const key=taskRowId(task,index),existing=footers[key];
      // In the reply's column, like the other footers (design review M1, 25 Sep); empty, the slot doesn't show.
      footers[key]=<><div key={`remembered:${task.id}:${runId}`} className="dervo-task-footer dv-rem-slot"><RememberedLines task={task} runId={runId} projectId={projectId} channelId={channelId} projectProfileId={selectedProject?.profile_id} threadProfileId={binding.data?.profile_id} onOpenKnowledge={()=>{settings.go(null);setKnowledgeOpen(true);}}/></div>{existing}</>;
    }
  }
  if(workingTask&&steerLine?.messageIndex!==undefined){
    const key=taskRowId(workingTask,steerLine.messageIndex),existing=footers[key];
    footers[key]=<><p key={`interruption:${key}`} className="dervo-task-footer dv-interruption" data-interruption={steerLine.kind}>{steerLine.text}</p>{existing}</>;
  }
  for(const request of requests.data??[])footers[requestRowId(request)]=<HarnessFooter key={request.id} request={request} projectId={projectId} channelId={channelId} refresh={refreshConversation}/>;
  // A message that waited behind this conversation's own run, with Cancel, or Send again and Dismiss once it couldn't start.
  for(const item of queue.data??[])footers[queueRowId(item)]=<QueueFooter key={item.id} item={item} projectId={projectId} channelId={channelId} refresh={refreshConversation}/>;
  // A conversation whose messages go to the project agent, where Edit (and ↑ in the empty box) can ask again.
  const codingThread=routeSubmit('',activity.data?.coding,null,null).path==='coding';
  // Copy and Edit right under every message shown, before whatever else hangs there (MessageActions). No Edit while a run
  // is going: sending then would add to that run, not ask again, as the Claude app holds Edit while it answers.
  // Plain chat has Copy only: Dervo's thread row has no Reply, reactions or Delete (9 Oct), as the Claude app has none.
  for(const message of messages)footers[message.id]=<Fragment key={`actions:${message.id}`}>
    <MessageActions text={message.body} own={message.pubkey==='local-user'} onEdit={running||!codingThread?undefined:()=>{try{if(putBack.current?.(message.body)===false)setError(t('The message box is busy, so nothing was changed. Try Edit again once it’s free.'));}catch(e){setError(String(e));}}}/>{footers[message.id]}</Fragment>;
  const inspectedTask=tasks.data?.find(task=>task.id===inspectors.task?.id);
  /** Adding a project uses first run's screen, in its "add" mode. */
  function openAddProject(){setError('');addProject.start();}
  async function projectChanged(removed?:string,channels?:string[]){
      if(removed===projectId){setProject('');setChannel('');inspectors.navigateAway();}
      forgetConversations(channels??[]);
      if(channels&&removed)for(const key of Object.keys(localStorage)){
        if(key.startsWith('dervo-knowledge-draft:')){try{if(JSON.parse(key.slice('dervo-knowledge-draft:'.length))[0]===removed)localStorage.removeItem(key);}catch{/* Keep unrelated malformed keys. */}}
      }
      if(removed)projectClient.removeQueries({predicate:query=>query.queryKey.includes(removed)});
      await projects.refetch();
  }
  /** The orchestrator hands a message to a conversation and opens it, when a job it sent there needs you first (for
   * example coding isn't allowed in this project yet). The words are written as that conversation's draft first, then its composer
   * sends them itself once the conversation's account and status have loaded, through the composer's own auto-submit.
   * So the message takes exactly the route a message typed there would, and a refused send leaves your words in the
   * composer the way any refused send does. */
  const [roomSend,setRoomSend]=useState<{key:string;fired:boolean}|null>(null);
  /** Opens ``targetId`` with ``text`` in its composer, and sends it once the thread has loaded unless ``send`` is false.
   * Next up's Work on passes false: its words come from NEXT-UP.md or a tester's report, so you read them before they
   * go out as your message (security review, 25 Sep, M2). */
  function handToConversation(targetId:string,text:string,send=true){
    const key=`dervo:${projectId}:${targetId}`;
    persistDraftEntry(key,text,targetId,[],[]);
    setRoomSend(send?{key,fired:false}:null);
    setChannel(targetId);
    setFocusCreated({projectId,channelId:targetId});
  }
  // Leaving before the send happened keeps the words as a draft there and cancels the send.
  useEffect(()=>{if(roomSend&&roomSend.key!==`dervo:${projectId}:${channelId}`)setRoomSend(null);},[projectId,channelId,roomSend]);
  // Handed to the composer only once this conversation's account and status have loaded, so the message is routed
  // exactly as one typed here would be; the composer itself then waits for its images before sending.
  const roomSendKey=roomSend&&!roomSend.fired&&roomSend.key===`dervo:${projectId}:${channelId}`&&binding.data&&activity.data?roomSend.key:null;
  /** Resume after a Pause (16.4): "Carry on from where you paused." goes as your next message here, by the same route as
   * one you type. It never replaces words you've started in the composer. */
  function resumeHere(text=RESUME_TEXT):string|undefined{  // beta.17.1: Continue after a limit passes its own words
    if(loadDraftEntry(`dervo:${projectId}:${channelId}`)?.content.trim())return 'Your draft is still in the composer. Send it, or clear it and press Resume.';
    handToConversation(channelId,text);
    return undefined;
  }
  /** A new thread opens at once with the composer focused; Dervo names it from your first message (surfaces, inventory 7). */
  const creatingThread=useRef(false);
  function openCreate(isPrivate:boolean){
    if(creatingThread.current||!hasSelectedProject)return;
    creatingThread.current=true;setFocusCreated(null);
    void act(async()=>{const c=await local<{id:string}>('create_conversation',{projectId,name:'',private:isPrivate,automaticName:true});await conversations.refetch();setChannel(c.id);setFocusCreated({projectId,channelId:c.id});})
      .finally(()=>{creatingThread.current=false;});
  }
  /** The orchestrator is the project screen with no conversation open and no panel over it. */
  function openOrchestrator(){contextTrigger.current=null;inspectors.navigateAway();settings.go(null);setKnowledgeOpen(false);setBugsOpen(false);setReleasesOpen(false);setSearchOpen(false);setChannel('');}
  function openHistory(place:HistoryPlace){settings.go(null);setKnowledgeOpen(false);setBugsOpen(false);setReleasesOpen(false);setSearchOpen(false);setHistoryAt({place,request:Date.now()});}
  function projectAction(action:ProjectAction,trigger:HTMLElement){
    if(action==='history'){contextTrigger.current=trigger;openHistory({view:'log'});return;}
    if(['search','catchup','knowledge','bugs'].includes(action))setHistoryAt(null);
    if(action==='orchestrator'){openOrchestrator();return;}
    if(action==='settings'){settings.open('project-picker',trigger);return;}
    if(action==='bugs'){contextTrigger.current=trigger;settings.go(null);setReleasesOpen(false);setKnowledgeOpen(false);setSearchOpen(false);setBugsOpen(true);return;}
    if(action==='search'||action==='catchup'||action==='knowledge'){
      contextTrigger.current=trigger;settings.go(null);setReleasesOpen(false);setBugsOpen(false);
      setKnowledgeOpen(action==='knowledge');setSearchOpen(action!=='knowledge');
      if(action!=='knowledge'){setSearchMode(action==='search'?'project':'catchup');requestAnimationFrame(()=>document.querySelector<HTMLElement>('[data-panel-focus="search"]')?.focus());}
      return;
    }
    projectSettingsTrigger.current=trigger;
    if(action==='preview')setPreviewOpen(true);
    if(action==='connection')settings.open('connection',trigger);
  }
  // The thread's per-request choices sit with the model: the chip says when one differs from the default.
  const chipOverrides=[checkChoiceLabel(activity.data?.coding?.checks,check.value),reviewChoiceLabel(review.value)].filter((label):label is string=>!!label);
  // The picker opens on every thread with an account, granted or reading only: switching accounts doesn't need a grant.
  // The check and second review for one message only mean something once coding is allowed.
  const granted=activity.data?.coding?.state==='granted';
  const threadBusy=(tasks.data??[]).some(task=>task.state==='working'||task.state==='needs_permission')||(queue.data??[]).some(item=>item.state==='queued')
    ?'Wait for this thread’s task to finish before switching.':null;
  const threadAgent=pickerAgents.data?.find(agent=>agent.channel_id===channelId)?.name??null;
  const composerFoot=current&&activity.data?.coding&&activity.data.coding.state!=='unsupported'&&binding.data
    ?<ComposerTools>
      <ModelPicker key={`model:${channelId}`} onOpen={()=>track('model_picker_opened')} projectId={projectId} channelId={channelId} profiles={profiles.data??[]} usage={usage.data?.accounts} binding={binding.data}
        project={selectedProject?{profile_id:selectedProject.profile_id,model:selectedProject.model,context:selectedProject.context}:undefined} effort={activity.data.coding.owner_effort} busy={threadBusy} agent={threadAgent}
        onChanged={async()=>{await Promise.all([activity.refetch(),binding.refetch()]);}} overrides={chipOverrides}
        checks={granted?activity.data.coding.checks:undefined} check={check.value} onCheck={granted?check.setValue:undefined}
        review={activity.data.collaboration?.review} reviewValue={review.value} onReview={granted?review.setValue:undefined}
        onSettings={page=>settings.open(page==='accounts'?'usage-manage':page==='checks'?'review-setup':'change-model',document.querySelector<HTMLElement>('[data-testid="model-picker"]'))}
        openNow={pickerFor===channelId} onOpened={()=>setPickerFor(null)}/>
      <AccessTool state={activity.data.coding.full_access?.active?'full':activity.data.coding.state==='stale'?'stale':granted?'edit':'read'} onOpen={()=>setAccessFor(channelId)}/>
      {granted&&<ThreadFolderChip key={`folder:${projectId}:${channelId}`} projectId={projectId} channelId={channelId} draft={folderDraft}/>}
    </ComposerTools>
    :undefined;
  // What's only said, not chosen, sits above the box with the thread's status, not in the pill (Bryce, 8 Oct 2026).
  const composerNotes=composerFoot&&<ComposerNotes busy={!!tasks.data?.some(task=>task.state==='working')} queued={(queue.data??[]).filter(item=>item.state==='queued').length} interruption={interruption}>
    {/* Not while a run goes: "New session · The last message is still running" read as wrong mid-resume (polish, 8 Oct). */}
    {granted&&!tasks.data?.some(task=>task.state==='working')&&<ContinuityTool key={`continuity:${channelId}`} projectId={projectId} channelId={channelId} view={activity.data?.coding?.continuity} onChanged={()=>activity.refetch()}/>}
  </ComposerNotes>;
  // The thread's head: the project, the workstream it's filed under (or its own name until it is), Progress and ⋯.
  const [headMenu,setHeadMenu]=useState(false),[headConfirm,setHeadConfirm]=useState(false);
  const terminalTool=<ProjectTerminalButton open={terminalOpen} onToggle={()=>showTerminal(projectId,!terminalOpen)}/>;
  const threadHead=current&&<DervoMainHead title={projectName(selectedProject)} work={selectedProject?.context==='work'} crumb={current.organisationGroup??current.name} here={current.organisationGroup?current.name:undefined} detail={current.name} live={<ThreadLive key={`live:${projectId}:${channelId}`} projectId={projectId} channelId={channelId}
      task={[...(tasks.data??[])].reverse().find(task=>task.origin!=='collaboration')} who={binding.data?providerAuthor(binding.data.provider):undefined}
      question={trayFirst?.state==='pending'?{...trayFirst,saved:savedAnswer(trayFirst,projectId,channelId)}:null}
      open={guideVisible} preview={previewShown} poll={!!(moment&&current&&moment.channelId===channelId&&moment.projectId===projectId)&&!messages.some(message=>message.pubkey==='local-user')} onToggle={guideTask?()=>{if(guideVisible)closeColumn();else toggleGuide();}:undefined}/>} tools={<>
    {terminalTool}
    {guideTask&&<Button ref={guideTrigger} variant="ghost" size="icon" aria-label="Progress" title={guideVisible?(previewShown?'Hide Preview (⌘\\)':'Hide Progress (⌘\\)'):'Show Progress (⌘\\)'} aria-expanded={guideVisible} onClick={()=>{if(guideVisible)closeColumn();else toggleGuide();}}><PanelGlyph/></Button>}
    <DropdownMenu onOpenChange={open=>{setHeadMenu(open);if(!open)setHeadConfirm(false);}}><DropdownMenuTrigger asChild><Button ref={conversationMenuTrigger} aria-label="Thread options" title="Thread options" size="icon" variant="ghost"><MoreGlyph/></Button></DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="dv-menu-thread-head" aria-label="Thread options" onCloseAutoFocus={event=>{if(inspectors.task){event.preventDefault();return;}const panel=pendingPanelFocus.current;if(panel||renaming){event.preventDefault();pendingPanelFocus.current=null;if(panel)requestAnimationFrame(()=>document.querySelector<HTMLElement>(`[data-panel-focus="${panel}"]`)?.focus());}}}>
      {headMenu&&<ThreadMenuBody kind="dropdown" conversation={current} model={threadMenu} confirming={headConfirm} onConfirm={setHeadConfirm} header={{
        search:binding.data?()=>{contextTrigger.current=conversationMenuTrigger.current;pendingPanelFocus.current='search';setKnowledgeOpen(false);setBugsOpen(false);setSearchMode('conversation');setSearchOpen(true);}:undefined,
        continuity:activity.data?.coding?.state==='granted'&&binding.data?<ContinuityMenuItems projectId={projectId} channelId={channelId} view={activity.data.coding.continuity} onChanged={()=>activity.refetch()} onError={setError}/>:undefined}}/>}
    </DropdownMenuContent></DropdownMenu>
  </>}/>;
  // Settings, one page at a time. Each page shows today's controls for its subject until its area build replaces them,
  // so nothing that works today goes missing on the way.
  useSettingsEscape(settingsOpen,settings.close);
  // The project's five pages read and save on the project itself (channelId null), so none of them needs a thread.
  const settingsPage=settings.page;
  const settingsBody=settingsPage&&<>
    {isProjectPage(settingsPage)&&!selectedProject&&<p className="dv-settings-soon">Choose a project first. Its settings open here.</p>}
    {settingsPage==='model'&&settingsProject&&<AccountAndModelPage key={`model:${projectId}`} project={settingsProject} profiles={profiles.data??[]} usage={usage.data?.accounts}
      conversations={conversations.data??[]} onOpenThread={id=>{settings.close();setChannel(id);}} onAddAccount={()=>settings.go('accounts')}/>}
    {settingsPage==='access'&&settingsProject&&<AccessPage key={`access:${projectId}`} project={settingsProject} onChecks={()=>settings.go('checks')}/>}
    {settingsPage==='checks'&&settingsProject&&<ChecksPage key={`checks:${projectId}`} project={settingsProject} profiles={profiles.data??[]} usage={usage.data?.accounts} onAccess={()=>settings.go('access')}/>}
    {settingsPage==='agents'&&settingsProject&&<AgentsPage key={`agents:${projectId}`} project={settingsProject} profiles={profiles.data??[]} usage={usage.data?.accounts}/>}
    {settingsPage==='helpers'&&settingsProject&&<HelpersPage key={`helpers:${projectId}`} project={settingsProject} profiles={profiles.data??[]} usage={usage.data?.accounts}
      conversations={conversations.data??[]} onAccounts={()=>settings.go('accounts')}/>}
    {settingsPage==='cloud'&&settingsProject&&<CloudPage key={`cloud:${projectId}`} project={settingsProject}/>}
    {settingsPage==='general'&&settingsProject&&<GeneralPage onOpenBlocker={openBlocker} key={`general:${projectId}`} project={settingsProject} onChanged={projectChanged} onPreview={()=>setPreviewOpen(true)}/>}
    {settingsPage==='account'&&<DervoAccount/>}
    {settingsPage==='preferences'&&<><ThemeTiles/><NotificationSettings/><KeepAwakeSetting/><PhoneSettings/><GlassWindow/></>}
    {settingsPage==='preferences'&&<UsageSharingSettings/>}
    {settingsPage==='accounts'&&<ConnectedAccounts profiles={profiles.data??[]} loading={profiles.isPending} failed={profiles.isError} onRetry={()=>void profiles.refetch()}
      onProfilesChanged={()=>profiles.refetch()} usage={usage.data} status={status.data} onStatusChanged={()=>status.refetch()}
      projectName={selectedProject?projectName(selectedProject):undefined} projectProfileId={selectedProject?.profile_id} project={settingsProject??undefined} onProjectSettings={selectedProject?()=>settings.go('model'):undefined}/>}
    {settingsPage==='updates'&&<BetaUpdates projectId={projectId||undefined} onAccount={()=>settings.go('account')}/>}
    {settingsPage==='updates'&&<Licences/>}
    {settingsPage==='testers'&&accountData?.membership?.is_admin===true&&<TestersPage/>}
    {settingsPage==='testers'&&accountData?.membership?.is_admin===true&&<WaitlistInvites/>}
    {settingsPage==='testers'&&accountData?.membership?.is_admin===true&&<TestersUsage/>}
    {settingsPage==='testers'&&accountData?.membership?.is_admin===true&&<TestersPeople/>}
    {settingsPage==='testers'&&accountData?.membership?.is_admin===true&&<TestersErrors/>}
    {error&&<p role="alert" className="text-destructive">{error}</p>}
  </>;
  // Usage events: the screen that's showing, once per change (usage.ts), and the one-time notice about them.
  useScreenView(usageScreen({firstRun:!!firstRunMode,settingsPage:settings.page,knowledge:knowledgeOpen,search:searchOpen,catchUp:searchMode==='catchup',
    releases:releasesOpen,thread:!!current,home:hasSelectedProject}),current?.id);
  useUsageNotice(()=>settings.go('preferences'),firstRunMode==='first'&&!welcomed);
  // The project-created moment sits at the top of its Project overview thread (ProjectMoment.tsx).
  const momentIntro=moment&&current&&moment.channelId===channelId&&moment.projectId===projectId?<ProjectMoment moment={moment} checkedAt={dervoAccount.dataUpdatedAt||undefined} checking={dervoAccount.isFetching}
    onCheck={()=>void (moment.waitingFor==='plan'?local('dervo_plan_refresh').catch(()=>{}):Promise.resolve()).then(()=>dervoAccount.refetch())} onHome={openOrchestrator} onSettings={trigger=>{settings.open('project-picker',trigger);settings.go('access');}}
    onRetry={async()=>{const pid=moment.projectId;try{const result=await local<{channelId:string;state?:string;error?:string|null;detail?:string}>('project_explore',{projectId:pid,clientId:`${overviewClientId(pid)}-${Date.now()}`});
      setMoment(m=>m&&{...m,channelId:result.channelId,...(result.state==='blocked'||result.state==='failed'?{state:'failed',overviewDetail:result.error||result.detail}:{state:'running',overviewDetail:undefined})});}
      catch(failure){setMoment(m=>m&&{...m,state:'failed',overviewDetail:failure instanceof Error?failure.message:String(failure)});}
      await conversations.refetch();}}/>:null;
  // What the feedback dialog says it's opened over: the screen under Settings, never "Settings" itself.
  const feedbackProject=selectedProject?projectName(selectedProject):'';
  const feedbackView=firstRunMode?'Setting up a project':knowledgeOpen?`Knowledge${feedbackProject?` in ${feedbackProject}`:''}`:searchOpen?`${searchMode==='catchup'?'Catch up':'Search'}${feedbackProject?` in ${feedbackProject}`:''}`
    :current?`A thread${feedbackProject?` in ${feedbackProject}`:''}`:feedbackProject?`Home in ${feedbackProject}`:'Dervo';
  // What bug capture sends with a report: the screen by name, and the project, thread, task and model by name and id.
  const captureScreen=firstRunMode?'First run':settings.page?`Settings, ${pageHead(settings.page,'','').title}`:bugsOpen?'Next up'
    :knowledgeOpen?'Knowledge':searchOpen?(searchMode==='catchup'?'Catch up':'Search'):releasesOpen?'Releases':current?'A thread':hasSelectedProject?'Home':'No project open';
  const captureContext:CaptureContext={project:selectedProject?{id:projectId,name:projectName(selectedProject)}:null,screen:captureScreen,
    thread:current?{id:current.id,name:current.name}:null,task:current&&guideTask?{id:guideTask.id,state:guideTask.state}:null,
    provider:(current?binding.data?.provider:projectProfile?.provider)??null,model:(current?binding.data?.model:selectedProject?.model)??null};
  // Reports are kept in a project's Bugs and ideas, which needs a project on a personal account: the open one, or else
  // the first that is, so the key never dead-ends.
  const reportHome=projects.data?.find(project=>profiles.data?.find(profile=>profile.id===project.profile_id)?.category==='personal');
  const captureTarget:CaptureTarget|null=projectRecordsReady&&selectedProject?{projectId,profileId:selectedProject.profile_id,name:projectName(selectedProject),here:true}
    :reportHome?{projectId:reportHome.id,profileId:reportHome.profile_id,name:projectName(reportHome),here:false}:null;
  // The errors this window shows, kept for a report as the host's error reports keep them: kind and screen, never words.
  const screenNow=useRef(captureScreen);screenNow.current=captureScreen;
  useEffect(()=>{if(error)noteShownError(error,screenNow.current);},[error]);
  // Errors you can keep (23 Sep): the alert's words go into this thread's list too, so they stay after it clears.
  const shownError=error||status.error||history.error?errorWords(error||status.error||history.error):'';
  useEffect(()=>{setOpenThread(current?channelId:null);},[current,channelId]);
  useEffect(()=>{if(error)noteError(error,{channelId:current?channelId:null});},[error]);
  useEffect(()=>{if(status.error)noteShownError(status.error,screenNow.current);},[status.error]);
  useEffect(()=>{if(history.error)noteShownError(history.error,screenNow.current);},[history.error]);
  /** Which sidebar row reads as where you are. Catch up is Home at its second depth, so the Home row stays current
   * while it's open (F1, 20 Sep): it used to say nothing at all, and the screen covers Home's own head, so there was a
   * moment with nowhere named and no sign Home still existed. */
  const sidebarActive:'orchestrator'|'history'|'knowledge'|'bugs'|'search'|'settings'|undefined=
    settingsOpen?'settings':knowledgeOpen?'knowledge':bugsOpen?'bugs':historyAt?'history'
    :searchOpen?(searchMode==='catchup'?'orchestrator':'search')
    :releasesOpen?undefined:!channelId&&hasSelectedProject?'orchestrator':undefined;
  const firstRun=firstRunMode&&<FirstRun key={firstRunMode} mode={firstRunMode} profiles={profiles.data??[]} onProfilesChanged={()=>profiles.refetch()}
    onBroughtIn={async projectId=>{await projects.refetch();setProject(projectId);inspectors.navigateAway();}}
    onDone={async(created,agent)=>{const next=momentFrom(created,agent);if(next.state==='waiting')rememberWaiting(created.id);setMoment(next);await projects.refetch();setProject(created.id);
      // A moment that's waiting on access or has failed is the thing to read first, so it keeps the thread. Only the
      // ordinary case, an overview being written, gives way to the history offer.
      if(next.state==='running')await landAfterCreate(created.id,next.channelId);else setChannel(next.channelId);
      inspectors.navigateAway();}}/>;
  // Design sweep, brand moments: one opening screen until projects load, and the first project's setup as a full window.
  if(projects.isPending)return <OpeningScreen/>;
  if(firstRunMode==='first'&&!welcomed)return <Welcome onContinue={()=>{rememberWelcomeSeen();setWelcomed(true);}}/>;
  if(firstRunMode==='first')return firstRun;
  // The meadow photo across the window: the welcome pick (projects exist, none chosen) and the project-created moment
  // while it's still the story (momentShowsMeadow). Everything else is on paper.
  const meadow=(!!momentIntro&&!!moment&&momentShowsMeadow(moment,current?.activity?.running))||(!current&&!hasSelectedProject&&!underlay&&!projects.isError&&!!projects.data?.length);
  return <ReplyActionsContext.Provider value={replyActions}><LocalFileLinks.Provider value={revealFile}><SidebarProvider className="dv-window flex h-screen min-h-0 w-full overflow-hidden" data-meadow={meadow?'photo':undefined}>
    <FoldSidebarWhile active={previewShown}/>
    {/* Reading what's already here is a full-window moment, the way the mockup draws it: the sidebar folds while the
        offer and the read are on screen, at any width, and comes back with Home once the threads are there. */}
    <FoldSidebarWhile active={!current&&!underlay&&!!historyOffer.look} width={()=>0}/>
    <Sidebar collapsible="offcanvas" data-testid="app-sidebar">
      <AppTopChrome canGoBack={false} canGoForward={false} onGoBack={()=>{}} onGoForward={()=>{}}/>
      <SidebarContent>
        {settings.page&&<SettingsNav page={settings.page} onPage={settings.go} onBack={settings.close} onFeedback={()=>openFeedback()}
          projectName={selectedProject?projectName(selectedProject):undefined} admin={accountData?.membership?.is_admin===true} testersCount={testers.data?.people.filter(person=>person.status==='pending').length||undefined}/>}
        <div className="dv-keep" data-away={settingsOpen||undefined}><DervoSidebar knowledgeCount={knowledgeWaiting} knowledgeUnread={knowledgeUnread} projects={projects.data??[]} projectId={projectId} projectsLoading={projects.isPending} projectsFailed={projects.isError} onRetryProjects={()=>void projects.refetch()} onSelectProject={openProject} onAddProject={()=>openAddProject()}
          ready={projectRecordsReady} active={sidebarActive} onReports={releasesAllowed(accountData)?()=>projectAction('bugs',document.activeElement as HTMLElement):undefined}
          conversations={hasSelectedProject?withUndoRows(conversations.data??[],undo.entry):[]} channelId={channelId} conversationActive={!panel} onSelectConversation={id=>{setChannel(id);}}
          onAction={projectAction} onCreate={openCreate} onJump={()=>setPaletteOpen(true)}
          accounts={sideUsage} onUsage={openUsage} usageLifted={usageOpen?usageLifted:null} onWorkspaceSettings={trigger=>settings.open('account-menu',trigger)} onConnectedAccounts={trigger=>settings.open('usage-manage',trigger)} onReleases={releasesAllowed(accountData)?()=>{setSearchOpen(false);setKnowledgeOpen(false);setBugsOpen(false);settings.go(null);setReleasesOpen(true);}:undefined} onFeedback={()=>openFeedback()}
          canSignOut={!!accountData?.can_sign_out} onSignOut={async()=>{await local('dervo_account_sign_out');await dervoAccount.refetch();}}
          threadMenu={threadMenu} renaming={renaming} onRename={(conversation,next)=>void renameThread(conversation,next)} onUndo={()=>void undo.undo()} rowErrors={undo.failed} arrived={arrived}
          renamed={undo.entry?.kind==='renamed'?undo.entry.conversation.id:null} accountKinds={Object.fromEntries((profiles.data??[]).map(p=>[p.id,p.category]))} accountName={accountData?.state==='signed_in'?accountData.identity?.display_name??undefined:undefined} accountPhoto={photoImage(accountData)} accountNote={accountData?.state==='signed_in'?'Private beta':'Dervo'}
          onArchivedRestored={async conversation=>{await conversations.refetch();setArrived(conversation.id);}} onArchivedDelete={deleteThread}
          onArchiveMany={archiveMany} onDeleteArchived={deleteAllArchived}/>
</div>
      </SidebarContent>
      {accessFor===channelId&&current&&binding.data&&selectedProject&&<ThreadAccessDialog key={`access:${projectId}:${channelId}:${binding.data.profile_id}`} open onOpenChange={open=>{if(!open)setAccessFor(null);}} project={{...selectedProject,profile_id:binding.data.profile_id,model:binding.data.model}} channelId={channelId} accountName={threadAccount?.name??binding.data.identity}/>}
      <BetaFeedback launcher={false} requestOpen={feedbackAsk.n} initialCategory={feedbackAsk.category} view={feedbackView} key={`${accountData?.identity?.issuer??''}:${accountData?.identity?.subject??''}:${accountData?.state??'unknown'}`} context={{route:firstRunMode?'settings':knowledgeOpen?'knowledge':searchOpen?searchMode==='catchup'?'catch-up':'search':current?'conversation':'workspace',projectId:projectId||undefined,conversationId:current?.id,taskId:guideTask?.id,sessionId:guideTask?.session_id??undefined,provider:binding.data?.provider==='codex'?'codex':binding.data?.provider==='claude'?'claude':binding.data?.provider==='openrouter'?'openrouter':undefined}}/>
      <SidebarResize/>
    </Sidebar>
    <main ref={guideMainRef} className="dervo-connected-main dv-main flex min-w-0 flex-1 flex-col" data-coding-thread={codingThread||undefined}><ChannelNavigationProvider onOpenTask={(id,taskId)=>navigate.openTask(id,taskId)} channels={mentionChannels} onOpenChannel={openMentioned} matchUnknown>
      {settings.page&&<SettingsFrame page={settings.page} projectName={selectedProject?projectName(selectedProject):'this project'} folder={selectedProject?shortPath(selectedProject.cwd):undefined}>{settingsBody}</SettingsFrame>}
      <div className="dv-keep" data-away={settingsOpen||undefined}>
      {firstRunMode?firstRun:<>
      {!(momentIntro&&moment?.state==='waiting')&&<DervoAccountNotice onOpen={trigger=>settings.open('account-notice',trigger)}/>}
      <UpdatePrompt onOpenUpdates={()=>{settings.open('palette-settings',null);settings.go('updates');}}/>
      {!underlay&&(current?threadHead:hasSelectedProject&&selectedProject?<DervoMainHead title={projectName(selectedProject)} work={selectedProject.context==='work'} crumb="Home" tools={terminalTool}/>:null)}
      {projectRecordsReady&&selectedProject&&<NextUp key={`next-up:${projectId}`} projectId={projectId} profileId={selectedProject.profile_id} projectName={projectName(selectedProject)} open={bugsOpen} returnLabel={current?'Back to thread':'Back to project'} onClose={()=>closeContextPanel(()=>setBugsOpen(false))}
        onWork={(threadId,text)=>{setBugsOpen(false);handToConversation(threadId,text,false);}} onOpenThread={threadId=>{setBugsOpen(false);void openThread(threadId);}}/>}
      {projectRecordsReady&&selectedProject&&<ProjectKnowledge addRequest={addGuidanceRequest} onAddHandled={()=>setAddGuidanceRequest(0)} returnLabel={current?'Back to thread':'Back to project'} projectName={projectName(projects.data?.find(project=>project.id===projectId))} key={`knowledge:${projectId}`} projectId={projectId} channelId="" profileId={selectedProject.profile_id} open={knowledgeOpen} onClose={()=>closeContextPanel(()=>setKnowledgeOpen(false))} sourceChannelId={channelId} sourceTasks={current?.visibility==='shared'&&binding.data?.profile_id===selectedProject.profile_id?tasks.data??[]:[]} onOpenTaskSource={(sourceChannel,sourceTask)=>{setKnowledgeOpen(false);setBugsOpen(false);navigate.openTask(sourceChannel,sourceTask);}}/>}
      {projectRecordsReady&&selectedProject&&<HistoryScreen key={`history:${projectId}`} projectId={projectId} profileId={selectedProject.profile_id} projectName={projectName(selectedProject)} open={!!historyAt&&!searchOpen&&!knowledgeOpen&&!bugsOpen&&!releasesOpen} place={historyAt?.place} placeRequest={historyAt?.request} returnLabel={current?'Back to thread':'Back to project'}
        onClose={()=>closeContextPanel(()=>setHistoryAt(null))} onOpenThread={id=>void openThread(id)} onOpenTask={(id,taskId)=>navigate.openTask(id,taskId)}/>}
      <BetaPlan open={releasesOpen} returnLabel={current?'Back to thread':'Back to project'} onClose={()=>{setReleasesOpen(false);requestAnimationFrame(()=>document.querySelector<HTMLElement>('.dervo-sb-me')?.focus());}}/>
      {searchMode==='catchup'&&!catchupAll&&projectRecordsReady&&selectedProject&&<ProjectCatchup onConversationsChanged={()=>conversations.refetch()} projectId={projectId} profileId={selectedProject.profile_id} projectName={projectName(selectedProject)} conversations={conversations.data??[]} lastConversation={lastConversation(projectId)} open={searchOpen} returnLabel={current?'Back to thread':'Back to Home'} onClose={()=>closeContextPanel(()=>setSearchOpen(false))}
        onOpenConversation={id=>{setSearchOpen(false);void openThread(id).then(opened=>{if(opened)requestAnimationFrame(()=>document.querySelector<HTMLElement>('[data-testid="message-input"]')?.focus());});}} onOpenTask={(id,taskId)=>{setSearchOpen(false);navigate.openTask(id,taskId);}} onOpenKnowledge={()=>{setSearchOpen(false);setKnowledgeOpen(true);}}
        onAllActivity={()=>setCatchupAll(true)} onSearch={()=>setSearchMode('project')} onNewConversation={()=>openCreate(false)}/>}
      {((searchMode==='conversation'&&current&&binding.data)||(searchMode==='project'&&projectRecordsReady&&selectedProject)||(searchMode==='catchup'&&catchupAll&&projectRecordsReady&&selectedProject))&&<WorkspaceSearch request={searchMode==='project'?searchRequest:null} onRequestHandled={()=>setSearchRequest(null)} onScope={searchMode!=='catchup'&&current&&binding.data?next=>setSearchMode(next):undefined} conversationName={current?.name} returnLabel={current?'Back to thread':'Back to project'} projectName={projectName(projects.data?.find(project=>project.id===projectId))} onOpenConversation={id=>{setSearchOpen(false);void openThread(id).then(opened=>{if(opened)requestAnimationFrame(()=>document.querySelector<HTMLElement>('[data-testid="message-input"]')?.focus());});}} onOpenTask={(id,taskId)=>{setSearchOpen(false);navigate.openTask(id,taskId);}} onOpenKnowledge={()=>{setSearchOpen(false);setKnowledgeOpen(true);}} projectId={projectId} channelId={searchMode==='conversation'?channelId:''} profileId={searchMode==='conversation'?binding.data!.profile_id:selectedProject!.profile_id} mode={searchMode} open={searchOpen} onClose={()=>closeContextPanel(()=>setSearchOpen(false))}/>}
      {(error||status.error||history.error)&&<div role="alert" className="dv-thread-alert px-5 py-2 text-sm text-destructive"><span className="dv-thread-alert-text">{shownError}</span><CopyTick text={shownError} label="Copy"/>{history.error&&<Button variant="outline" onClick={()=>void refreshConversation()}>Retry updates</Button>}</div>}
      {current&&<ThreadErrors channelId={channelId} except={shownError}/>}
      {current?<div className={underlay?'hidden':'dervo-conversation-workspace'}>
        <div className="dervo-conversation-main">
        {!(momentIntro&&!messages.some(message=>message.pubkey==='local-user'))&&<ThreadPickup key={`pickup:${projectId}:${channelId}`} projectId={projectId} channelId={channelId} overview={current.origin==='overview'} headless
          task={[...(tasks.data??[])].reverse().find(task=>task.origin!=='collaboration')} who={binding.data?providerAuthor(binding.data.provider):undefined} question={trayFirst?.state==='pending'?{...trayFirst,saved:savedAnswer(trayFirst,projectId,channelId)}:null}/>}
        <div className="relative flex min-h-0 flex-1 flex-col" style={{'--buzz-channel-content-top-padding':'0px','--channel-top-chrome-height':'0px'} as React.CSSProperties}><MessageTimeline hasComposerOverlay={false} pinnedIntro={momentIntro} ref={timelineRef} channelId={channelId} channelName={current.name} channelType={current.visibility==='private'?'dm':'stream'} messages={messages} mainEntries={messages.map(message=>({message,summary:null}))} currentPubkey="local-user" isLoading={history.isLoading} skeletonShape="thread" fetchOlder={olderHistory.fetchOlder} hasOlderMessages={olderHistory.hasOlder} isFetchingOlder={olderHistory.isFetching} historyExhausted={!olderHistory.hasOlder} messageFooters={footers} emptyTitle="What would you like to work on?" emptyDescription="Ask a question or describe what you want to do. Your conversation stays here so you can return to it."/></div>
        <div className="dv-composer-status flex items-start justify-between gap-2"><div className="min-w-0 flex-1">{stoppingHere?<p role="status" className="dervo-stopped-line">Pausing. It’ll finish the step it’s on.</p>:stoppedByYou?<p role="status" className="dervo-stopped-line">{stoppedLine(activity.data?.coding?.continuity?.state)}</p>:trayFirst?.state==='pending'&&!activity.error?null:<ConversationIndicator taskFooterShown task={guideTask} preparing={current?.origin==='overview'&&(!guideTask||(!guideTask.error&&!guideTask.runs.length&&['needs_permission','working'].includes(guideTask.state)))} binding={binding.error?undefined:binding.data} status={activity.data} error={!!activity.error} guideVisible={guideVisible&&!previewShown&&!!guideTask&&!tasks.error&&!activity.error} onSettings={trigger=>{if(composerFoot)setPickerFor(channelId);else settings.open('change-model',trigger);}}/>}{composerNotes}</div><ConversationStop key={`${projectId}:${channelId}`} projectId={projectId} channelId={channelId} activity={activity.data} tasks={tasks.data??[]} requests={requests.data??[]} refresh={refreshConversation} tasksStoppedElsewhere/></div>
        {busyRun&&<ActiveRunNotice active={busyRun.active} message={busyRun.message} onOpen={(project,channel)=>{setBusyRun(null);if(project!==projectId)setProject(project);setChannel(channel);}} onDismiss={()=>setBusyRun(null)} refresh={refreshConversation}/>}
        {filing&&filing.channelId===channelId&&<FilingPrompt suggestion={filing.suggestion} busy={filingBusy} onFile={()=>void fileHere()} onNotNow={notNow}/>}
        {activity.data&&<ThreadFolderCard key={`folder-card:${projectId}:${channelId}`} projectId={projectId} channelId={channelId} onChanged={refreshConversation}/>}
        <ThreadNotes key={`notes:${projectId}:${channelId}`} projectId={projectId} channelId={channelId} agentName={providerAuthor(binding.data?.provider)} onOpenThread={id=>void openThread(id)} onChanged={refreshConversation}/>
        <WorkerQuestions ref={questionsRef} key={`questions:${projectId}:${channelId}`} projectId={projectId} channelId={channelId} questions={threadQuestions} onChanged={refreshConversation} disabled={questionsOff}
          composerText={composerText} onClearComposer={()=>clearComposer(composerRoot.current)}/>
        {workOut&&<WorkSignedOut name={workOut.name} profile={workOut.profile} project={projectName(selectedProject)} mismatch={workCheck==='identity_mismatch'}
          onSignIn={trigger=>settings.open('connection',trigger)} onOther={()=>setPickerFor(channelId)}/>}
        <ConversationMentions conversations={conversations.data??[]} currentId={channelId}><div ref={composerRoot} className="dv-send-root" onInputCapture={()=>{if(binding.data?.provider==='claude')warmSoon(projectId,channelId);}} onKeyDownCapture={event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing&&event.nativeEvent.keyCode!==229)armSendMotion(composerRoot.current);}} onClickCapture={event=>{if((event.target as Element).closest('[data-testid="send-message"]'))armSendMotion(composerRoot.current);}}><LocalImageComposer onStop={running?()=>void stopHere():undefined} channelSuggestionNote={conversationMentionNote} onInsertReady={insertReady} formClassName="dv-composer" containerClassName="dv-composer-dock" toolbarLeadingActions={composerFoot} onLocalCommand={feedbackCommand} agents={projectAgents.data} recall={running||!codingThread?undefined:lastOwnMessage} accessReady={imageAccessReady||accessPending} focusWhenReady={focusCreated?.projectId===projectId&&focusCreated?.channelId===channelId} projectId={projectId} key={`${projectId}:${channelId}`} autoSubmitDraftKey={roomSendKey} onAutoSubmitComplete={()=>setRoomSend(value=>value&&{...value,fired:true})} channelId={channelId} draftKey={`dervo:${projectId}:${channelId}`} disabled={!binding.data||!!modelGone||accessPending} isSending={send.isPending} placeholder={accessPending?'Agents can start once your access is approved':workOut?`Sign ${workOut.name} in again to send`:modelGone?'Pick a model this account offers to keep going':askingNow?'Or answer in words':tasks.data?.some(task=>task.state==='working')?'Add to what the agent is doing…':(history.data?.length??0)>0?`Reply to ${providerAuthor(binding.data?.provider)}`:`Tell ${providerAuthor(binding.data?.provider)} what to do`} onSend={async(...args:Parameters<typeof submit>)=>{
          // Words typed while a question waits answer it (the tray's header says so). Images still go as a message.
          if(askingNow&&!args[3]?.length){let answered=false;try{answered=!!await questionsRef.current?.answerInWords(args[0]);}catch(failure){throw markShownInPlace(failure);}if(answered)return;}
          await submit(...args);offerFiling(args[0]);}}/></div></ConversationMentions>
        </div>
        {previewShown&&<PreviewPane key={`preview:${projectId}:${channelId}`} projectId={projectId} threadId={channelId} tasks={tasks.data??[]} request={previewRequest?.scope===guideScope?previewRequest:null} onRequestHandled={()=>setPreviewRequest(null)} onProgress={()=>showColumn('progress')} onClose={closeColumn}/>}
        {guideReserved&&<TaskGuideLoading switcher={<ColumnSwitch value="progress" onChange={showColumn}/>}/>}
        {guideVisible&&guideTask&&!previewShown&&<TaskGuide switcher={<ColumnSwitch value="progress" onChange={showColumn}/>} key={`${projectId}:${channelId}:${guideTask.id}`} task={guideTask} askedAt={trayFirst?.state==='pending'&&trayFirst.taskId===guideTask.id?trayFirst.createdAt:null} diffs={guideDiffs} projectId={projectId} channelId={channelId} onOpenSource={(sourceChannel,sourceTask,trigger)=>navigate.openTask(sourceChannel,sourceTask,trigger)} unavailable={!!tasks.error||!!activity.error} onClose={closeGuide} refresh={refreshConversation} detail={guideDetail.data?.task.id===guideTask.id?guideDetail.data:undefined} onDetailChanged={()=>guideDetail.refetch()} recordRequest={guideRecordRequest} onOpenRecord={()=>openHistory({view:'record',taskId:guideTask.id})} onTraceFile={path=>openHistory({view:'trace',path})} records={<KnowledgeSnapshotsForTask projectId={projectId} channelId={channelId} taskId={guideTask.id}/>} extra={collaborationOf(guideTask,true)} onRunIt={()=>setPreviewOpen(true)} onOpenKnowledge={()=>setKnowledgeOpen(true)} onFollowUp={()=>document.querySelector<HTMLElement>('[data-testid="message-input"]')?.focus()} suggestion={(guideTask.suggest||guideTask.route?.suggest)&&<ProviderSuggestion task={guideTask} projectId={projectId} channelId={channelId} policy={activity.data?.coding?.route} latest={guideTask.id===latestConversationTask} conversationProfileId={binding.data?.profile_id} resetsAt={usage.data?.accounts.find(account=>account.profile_id===guideTask.route?.profile_id)?.resets_at} refresh={refreshConversation}/>}/>}
      </div>:!underlay?(hasSelectedProject&&selectedProject&&!conversations.isPending&&!listFailed
        ?historyOffer.look
         ?<ImportHistory key={`history:${projectId}`} projectId={projectId} projectName={projectName(selectedProject)} look={historyOffer.look}
           onDecline={historyOffer.decline} onDone={()=>{setHistoryArrived(projectId);historyOffer.finish();}}/>
         :<Home onFeedback={()=>{if(takeFeedbackCommand()==='bug')reportBug();else openFeedback();}} onConnectedAccounts={trigger=>settings.open('usage-manage',trigger)} key={projectId} projectId={projectId} profileId={selectedProject.profile_id} projectName={projectName(selectedProject)} conversations={conversations.data??[]}
          archived={historyOffer.state==='archived'?historyOffer.archived:0} arrived={historyArrived===projectId}
          onOpen={id=>void openThread(id)} onOpenTask={navigate.openTask} onHandOff={handToConversation} onChanged={()=>conversations.refetch()} onSignIn={trigger=>settings.open('account-notice',trigger)}
          onCatchUp={projectRecordsReady?trigger=>projectAction('catchup',trigger):undefined} threadMenu={threadMenu}/>
        :<WorkspaceWelcome loading={hasSelectedProject&&conversations.isPending} failed={projects.isError||(hasSelectedProject&&listFailed)} detail={(projects.error??conversations.error)?.message} projects={projects.data??[]} onOpen={openProject} onAdd={()=>openAddProject()} onRetry={()=>{void projects.refetch();if(hasSelectedProject)void conversations.refetch();}}/>):null}

    </>}</div>
    {selectedProject&&<ProjectTerminal key={selectedProject.id} projectId={selectedProject.id} projectName={projectName(selectedProject)} cwd={selectedProject.cwd} visible={terminalOpen&&!settingsOpen&&!underlay&&!firstRunMode} onHide={hideTerminal}/>}
    </ChannelNavigationProvider></main>
    {inspectedTask&&<TaskDialog key={inspectedTask.id} task={inspectedTask} threadName={current?.name} projectId={projectId} channelId={channelId} refresh={refreshConversation} open={true} activation={inspectors.task?.activation} onCloseAutoFocus={inspectors.task?.onCloseAutoFocus} onOpenChange={open=>{if(!open)inspectors.closeTask();}} extra={collaborationOf(inspectedTask,true)} records={<KnowledgeSnapshotsForTask projectId={projectId} channelId={channelId} taskId={inspectedTask.id}/>}/>}
    {inspectors.packet&&<PacketInspector key={inspectors.packet.id} packetId={inspectors.packet.id} projectId={projectId} channelId={channelId} conversations={conversations.data} navigate={navigate} onCloseAutoFocus={inspectors.packet.onCloseAutoFocus} onOpenChange={open=>{if(!open)inspectors.closePacket();}}/>}
    {projectId&&<ReleasePreview key={`preview:${projectId}`} projectId={projectId} open={previewOpen} onClose={()=>setPreviewOpen(false)} onOpenBlocker={openBlocker}/>}
    {admin&&<BugCapture open={captureOpen} onOpenChange={setCaptureOpen} target={captureTarget} context={captureContext}/>}
    <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} items={palette} ready={projectRecordsReady} current={current?.name} onSearch={paletteActions.search}/>
    <UsagePopover open={usageOpen} onOpenChange={setUsageOpen} anchor={usageAnchor} returnFocus={usageTrigger} summary={usage.data} failed={usage.isError} onRetry={()=>void usage.refetch()}
      onManage={()=>{setUsageOpen(false);settings.open('usage-manage',usageTrigger.current);}}/>
    <AddProjectDialog staged={addProject.staged} onChangeFolder={addProject.change} onClose={()=>{addProject.close();requestAnimationFrame(()=>document.querySelector<HTMLElement>('.dervo-sb-picker')?.focus());}}
      profiles={profiles.data??[]} usage={usage.data?.accounts} current={selectedProject?{name:projectName(selectedProject),profile_id:selectedProject.profile_id,model:selectedProject.model}:undefined}
      onConnect={()=>{addProject.close();settings.open('usage-manage',document.querySelector<HTMLElement>('.dervo-sb-picker'));}}
      onDone={async created=>{await projects.refetch();addProject.close();setProject(created.id);await landAfterCreate(created.id,created.overview?.channelId??created.channelId);inspectors.navigateAway();}}/>
  </SidebarProvider></LocalFileLinks.Provider></ReplyActionsContext.Provider>;
}
