import {useQuery,useQueryClient} from '@tanstack/react-query';
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {local} from './api';
import {addOlderPage,applyConversationFeed,carryOlder,withOlder,type FeedState,type FeedResponse,type ConversationSnapshot,type OlderPage,type OlderPages} from './conversationFeedPatch';
import {currentPresence,watchConversationPresence,type ConversationPresence} from './conversationForeground';
import {t} from '@/i18n';
import {isBusyAnswer} from './hostBusy';

const IDLE_INTERVAL=30000;
/** While a run is live. 50 ms cost 8 to 16% of a core in the Python host during every watched run (efficiency review,
 * 23 Sep). A send or any action still refreshes at once, so this only paces the streamed text. */
export const ACTIVE_INTERVAL=250;
/** While a run is live and another app is in front. The thread is still on screen, so it keeps moving, at a quarter of
 * the pace (dd #24: reads used to stop on blur, and the run looked like it had stopped). Hidden windows don't read. */
export const SHOWN_ACTIVE_INTERVAL=1000;
/** A run on screen with no answer from the host for this long reads as unknown, never as a step still going. */
export const LOST_AFTER=15000;
const LOST_CHECK=2000;
/** What the thread shows when the host stops answering during a run. The screens already treat any read error as unknown. */
export const LOST_MESSAGE=t('No update from Dervo for a while, so this thread’s status is unknown.');
type Activation={key:string;enabled:boolean;id:number;state?:FeedState;older?:OlderPages;view?:FeedView;fetching?:Promise<void>;tail:Promise<unknown>;force:boolean;heardAt?:number};
/** What a read hands the screen: the snapshot and the older pages loaded so far. */
export type FeedView=FeedState&{older?:OlderPages};
/** The host's refusal for a snapshot over its limit. The feed pages history (16.4), so this should be gone; if it still
 * comes, the thread keeps what it last showed and keeps reading instead of stopping with "Status unavailable". */
export const SIZE_REFUSAL=t('This conversation is too large to update at once.');
export const isSizeRefusal=(error:unknown)=>String(error instanceof Error?error.message:error).includes(SIZE_REFUSAL);

/** A busy answer (hostBusy.ts) or a size refusal isn't this thread's status, and a read is safe to repeat: the thread keeps
 * its last snapshot and keeps reading. Before, one of them stopped the reads and put "Retry updates" on screen until you
 * pressed it. A host that stays busy still reads as unknown after LOST_AFTER (the run on screen isn't confirmed). */
const keepsReading=(error:unknown)=>isSizeRefusal(error)||isBusyAnswer(error);

const lostError=new Error(LOST_MESSAGE);

/** What each thread last showed, kept for this session so opening it again (or clicking through threads) shows the thread
 * at once while the read behind it catches up, instead of an empty thread with placeholder bars. Memory only: nothing is
 * written anywhere. A remembered view stands in only until the first fresh read lands, and gives way to the empty state
 * if that takes longer than ``REMEMBERED_GRACE``. One with a run going never stands in (remembered). */
const lastViews=new Map<string,{view:FeedView;at:number}>();
export const REMEMBERED_MAX=12;
export const REMEMBERED_FOR=10*60000;
export const REMEMBERED_GRACE=2500;
export function remember(key:string,view:FeedView){
  lastViews.delete(key);
  // Without the older pages you'd scrolled back through: the fresh read starts from the latest ones again, so what's shown
  // until it lands is the same window, and nothing above it disappears when it does.
  lastViews.set(key,{view:{...view,older:undefined},at:Date.now()});
  while(lastViews.size>REMEMBERED_MAX)lastViews.delete(lastViews.keys().next().value as string);
}
/** Whether a view had a run going: the host said so, or a task in it was working. */
const live=(view:FeedView)=>view.active||view.snapshot?.activity?.active===true||(view.snapshot?.tasks??[]).some(task=>task?.state==='working');
export function remembered(key:string,now=Date.now()):FeedView|undefined{
  const entry=lastViews.get(key);
  // A view taken while a run was going isn't used (beta.18 review): that run may have ended since, and a remembered
  // "working" would show as live until the fresh read lands. That thread starts from nothing, as before.
  return entry&&now-entry.at<REMEMBERED_FOR&&!live(entry.view)?entry.view:undefined;
}
export function forgetViews(){lastViews.clear();}

/** One sequential snapshot owner. Only changed data crosses the bridge during active work. */
export function useConversationReads(projectId:string,channelId:string,enabled:boolean){
  const key=JSON.stringify([projectId,channelId]);
  const client=useQueryClient();
  const current=useRef<Activation|null>(null);
  const serial=useRef(0);
  if(current.current?.key!==key||current.current.enabled!==enabled){
    current.current={key,enabled,id:++serial.current,tail:Promise.resolve(),force:false};
  }
  const activation=current.current;
  const mounted=useRef(true);
  const [presence,setPresence]=useState<ConversationPresence>(currentPresence);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const alive=()=>mounted.current&&current.current===activation&&activation.enabled;
  function read(){
    const pending=activation.tail.then(async()=>{
      if(!alive())throw new Error(t('Conversation is no longer selected.'));
      const force=activation.force;activation.force=false;
      const previous=activation.state;
      try{
        const response=await local<FeedResponse>('conversation_feed',{projectId,channelId,cursor:previous?.cursor??null,force});
        activation.heardAt=Date.now();
        if(!alive())throw new Error(t('Conversation changed while its update arrived.'));
        const next=applyConversationFeed(previous,response);
        const older=carryOlder(activation.older,previous?.snapshot,next.snapshot);
        activation.state=next;
        // An unchanged read hands back the same object, so nothing on screen re-renders for it.
        if(!activation.view||next!==previous||older!==activation.older)activation.view={...next,older};
        activation.older=older;
        remember(key,activation.view);
        return activation.view;
      }catch(error){
        // A size refusal is still the host answering, so it doesn't count toward a lost thread.
        if(isSizeRefusal(error))activation.heardAt=Date.now();
        // The next explicit refresh requests a full snapshot. Never guess a missing patch.
        activation.state=undefined;
        activation.view=undefined;
        throw error;
      }
    });
    activation.tail=pending.catch(()=>undefined);
    return pending;
  }
  // The activation whose remembered view ran out of time (REMEMBERED_GRACE), so it shows the empty state instead.
  const [overdue,setOverdue]=useState(-1);
  const feed=useQuery({
    queryKey:['conversation-feed',projectId,channelId,activation.id],
    enabled,
    queryFn:read,
    // A new activation of the same conversation (its enabled state flipped and came back) keeps showing that
    // conversation's last snapshot until the fresh read lands, so its task list and guide don't blink out for a
    // frame (#11). Another conversation's live snapshot is never shown. A thread this session has already shown starts
    // from what it last showed (remembered), for a moment, and from nothing otherwise, as before.
    placeholderData:(previous,previousQuery)=>{
      if(!enabled)return undefined;
      if(previousQuery?.queryKey[1]===projectId&&previousQuery.queryKey[2]===channelId)return previous;
      return overdue===activation.id?undefined:remembered(key);
    },
    retry:false,gcTime:0,structuralSharing:false,refetchOnWindowFocus:false,
    notifyOnChangeProps:['data','error','status'],
    refetchInterval:query=>presence==='hidden'||query.state.error&&!keepsReading(query.state.error)?false:query.state.data?.active?presence==='focused'?ACTIVE_INTERVAL:SHOWN_ACTIVE_INTERVAL:IDLE_INTERVAL,
    // This hook owns foreground state, including native focus. React Query's DOM-only gate can be stale.
    refetchIntervalInBackground:true,
  });
  useEffect(()=>{
    if(!enabled||!feed.isPlaceholderData)return;
    const timer=setTimeout(()=>setOverdue(activation.id),REMEMBERED_GRACE);
    return()=>clearTimeout(timer);
  },[activation.id,feed.isPlaceholderData]);
  const refresh=useCallback(async ()=>{
    if(!alive())return false;
    activation.force=true;
    let result=await feed.refetch();
    // React Query shares an initial pending read. Finish the requested refresh after it settles.
    if(alive()&&activation.force)result=await feed.refetch();
    return alive()&&!result.isError;
  },[activation,feed.refetch]);
  useEffect(()=>{
    if(!enabled)return;
    let was=currentPresence();
    return watchConversationPresence(next=>{
      setPresence(next);
      // Coming to the front, or back on screen after being hidden, catches up at once.
      if(next==='focused'||was==='hidden'&&next==='shown')void refresh();
      was=next;
    });
  },[activation]);
  /** This thread's snapshot once a read has landed: the one on screen, or the first read's when none has yet. Send asks
   * it before choosing a path, so a message sent the moment a thread opens isn't routed as if it had no mode. */
  const loaded=useCallback(async ():Promise<ConversationSnapshot|undefined>=>{
    if(!alive())return undefined;
    const now=feed.data as FeedView|undefined;
    if(now)return now.snapshot;
    const result=await feed.refetch({cancelRefetch:false});  // joins the first read when it's still on its way
    return alive()&&!result.isError?(result.data as FeedView|undefined)?.snapshot:undefined;
  },[activation,feed.data,feed.refetch]);
  const [fetchingOlder,setFetchingOlder]=useState(false);
  /** The page before what's loaded, for MessageTimeline's scroll-back. One at a time; a late page for another
   * conversation or an older position is dropped. */
  const fetchOlder=useCallback(async()=>{
    const older=activation.older;
    if(!alive()||!older?.hasOlder||older.before===null||activation.fetching)return;
    const before=older.before;
    const fetching=(async()=>{
      setFetchingOlder(true);
      try{
        const page=await local<OlderPage>('conversation_older',{projectId,channelId,before});
        if(!alive()||!activation.older||!activation.state)return;
        activation.older=addOlderPage(activation.older,before,page);
        activation.view={...activation.state,older:activation.older};
        remember(key,activation.view);
        client.setQueryData(['conversation-feed',projectId,channelId,activation.id],activation.view);
      }finally{
        activation.fetching=undefined;
        if(mounted.current)setFetchingOlder(false);
      }
    })();
    activation.fetching=fetching;
    return fetching;
  },[activation,client]);
  const view=feed.data as FeedView|undefined;
  // Lost: a run is on screen and the host hasn't answered a read for LOST_AFTER. Counted from when reading was last
  // expected to start, so coming back from hidden doesn't flash unknown before the catch-up read lands.
  const watching=enabled&&presence!=='hidden'&&!!view?.active;
  const [lost,setLost]=useState(false);
  useEffect(()=>{
    setLost(false);
    if(!watching)return;
    const since=Date.now();
    const timer=setInterval(()=>{if(alive())setLost(Date.now()-Math.max(activation.heardAt??0,since)>=LOST_AFTER);},LOST_CHECK);
    return()=>clearInterval(timer);
  },[watching,activation]);
  // New arrays only when a read or a page lands, so the thread's memos and effects don't rerun on every render.
  const shown=useMemo(()=>view?withOlder(view.snapshot,view.older):undefined,[view]);
  // A size refusal or a busy answer isn't this thread's status: the last snapshot stays and reads carry on.
  const error=feed.error&&!keepsReading(feed.error)?feed.error:watching&&lost?lostError:null;
  function field<K extends keyof ConversationSnapshot>(name:K){
    const data=name==='history'?shown?.history:name==='tasks'?shown?.tasks:view?.snapshot[name];
    return {...feed,error,isError:!!error,data:data as ConversationSnapshot[K]|undefined,refetch:refresh};
  }
  const hasOlder=!!view?.older?.hasOlder;
  return {activity:field('activity'),history:field('history'),tasks:field('tasks'),requests:field('requests'),queue:field('queue'),refresh,loaded,
    older:{hasOlder,fetchOlder,isFetching:fetchingOlder}};
}
