import {useState} from 'react';
import {Pause} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import {local,type HarnessRequest} from './api';
import {REVIEW_OPEN,type CodingTask} from './CodingTasks';
import type {ConversationStatus} from './TurnControls';
import {t} from '@/i18n';

type StopTarget={operation:'stop_turn'|'task_stop'|'harness_cancel';args:Record<string,string>};
export function conversationStopTargets(activity:ConversationStatus|undefined,tasks:CodingTask[],requests:HarnessRequest[]):StopTarget[]{
  return [
    ...(activity?.active_turn_ids??[]).map(messageId=>({operation:'stop_turn' as const,args:{messageId}})),
    ...tasks.filter(task=>task.state==='working'||REVIEW_OPEN.has(task.collaboration?.review?.phase??'')).map(task=>({operation:'task_stop' as const,args:{taskId:task.id}})),
    ...requests.filter(request=>['queued','running','stopping'].includes(request.state)).map(request=>({operation:'harness_cancel' as const,args:{requestId:request.id}})),
  ];
}
/** Keep interruption reachable when the original request has scrolled out of view. */
export function ConversationStop({projectId,channelId,activity,tasks,requests,refresh,tasksStoppedElsewhere=false}:{projectId:string;channelId:string;activity?:ConversationStatus;tasks:CodingTask[];requests:HarnessRequest[];refresh:()=>Promise<unknown>;tasksStoppedElsewhere?:boolean}){
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const targets=conversationStopTargets(activity,tasks,requests);
  async function stop(){
    if(busy)return;
    setBusy(true);setError('');
    const failures:string[]=[];
    for(const target of targets){
      try{await local(target.operation,{projectId,channelId,...target.args});}
      catch(error){failures.push(String(error));}
    }
    try{await refresh();}catch(error){failures.push(String(error));}
    setError(failures.join(' '));setBusy(false);
  }
  // A task already has its own Stop, beside its status or in the Task guide. This one is for everything else.
  if((!targets.length||tasksStoppedElsewhere&&targets.every(target=>target.operation==='task_stop'))&&!error)return null;
  return <div className="mr-5 flex max-w-xs flex-col items-end gap-1 pb-2">
    {!!targets.length&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>void stop()} aria-label={targets.length>1?t('Pause all work in this conversation'):t('Pause work in this conversation')} className="h-8 gap-1.5 rounded-full"><Pause aria-hidden="true" className="size-3 fill-current"/>{busy?t('Pausing…'):targets.length>1?t('Pause all'):t('Pause')}</Button>}
    {error&&<p role="alert" className="text-xs text-destructive">{error}</p>}
  </div>;
}
