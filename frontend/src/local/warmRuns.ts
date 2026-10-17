import {local} from './api';

/** Starting to type in a thread starts its next Claude Code ahead of time (backend/warm_runs.py), so the send that
 * follows doesn't wait for the launch. Asked at most once per WARM_ASK_MS while you stay in one thread: the host holds one for 150 s. */
export const WARM_ASK_MS=60_000;
let last={key:'',at:-Infinity};

/** The host holds one, for the thread typed in last, so moving to another thread asks at once. */
export function warmSoon(projectId:string,channelId:string,now=Date.now(),ask=(args:{projectId:string;channelId:string})=>local('coding_warm',args)){
  const key=`${projectId}:${channelId}`;
  if(key===last.key&&now-last.at<WARM_ASK_MS)return false;
  last={key,at:now};
  void Promise.resolve().then(()=>ask({projectId,channelId})).catch(()=>{/* only speed is lost */});
  return true;
}

/** Tests only. */
export function resetWarmForTest(){last={key:'',at:-Infinity};}
