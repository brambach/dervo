import {useEffect,useRef,useState} from 'react';
import {BookOpen} from 'lucide-react';
import {Button} from '@/shared/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogHeader,DialogTitle} from '@/shared/ui/dialog';
import {local,type HarnessRequest} from './api';

type Snapshot={project_id:string;captured_at:string;omitted:number;rules:{rule_id:string;version_id:string;text:string;provenance:string}[]};
type Props={projectId:string;channelId:string;request:HarnessRequest};

/** A deliberate read of this request's immutable context, with no background polling. */
export function ConversationMemory(props:Props){
 const [open,setOpen]=useState(false),trigger=useRef<HTMLButtonElement|null>(null);
 if(!props.request.knowledge)return null;
 return <><button ref={trigger} className="inline-flex items-center gap-1 rounded px-1 py-1 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" onClick={()=>setOpen(true)}><BookOpen size={12}/>Project memory · {props.request.knowledge.count?`${props.request.knowledge.count} ${props.request.knowledge.count===1?'rule':'rules'}`:'none'}</button>
  {open&&<MemoryDialog key={`${props.projectId}:${props.channelId}:${props.request.id}`} {...props} onClose={()=>setOpen(false)} returnFocus={()=>trigger.current?.focus()}/>}</>;
}
function MemoryDialog({projectId,channelId,request,onClose,returnFocus}:Props&{onClose:()=>void;returnFocus:()=>void}){
 const [data,setData]=useState<Snapshot|null>(null),[error,setError]=useState(''),[loaded,setLoaded]=useState(false),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  let live=true;setData(null);setError('');setLoaded(false);
  void local<HarnessRequest&{knowledge_snapshot:Snapshot|null}>('harness_status',{projectId,channelId,requestId:request.id}).then(result=>{
   if(result.id!==request.id||result.knowledge_snapshot&&result.knowledge_snapshot.project_id!==projectId)throw Error('The saved context belongs to another request.');
   if(live){setData(result.knowledge_snapshot);setLoaded(true);}
  }).catch(e=>{if(live){setError(String(e));setLoaded(true);}});
  return()=>{live=false;};
 },[projectId,channelId,request.id,attempt]);
 return <Dialog open onOpenChange={open=>{if(!open)onClose();}}><DialogContent onCloseAutoFocus={event=>{event.preventDefault();returnFocus();}} className="max-h-[80vh] overflow-y-auto sm:max-w-xl"><DialogHeader><DialogTitle>Memory for this request</DialogTitle><DialogDescription>Approved project guidance prepared for this request. This record doesn't prove the agent followed it. Change your guidance in Knowledge for future requests.</DialogDescription></DialogHeader>
  {!loaded&&<p role="status">Loading saved guidance…</p>}
  {error&&<div role="alert"><p>Couldn't load this request's saved guidance.</p><Button variant="outline" onClick={()=>{setError('');setLoaded(false);setAttempt(value=>value+1);}}>Try again</Button></div>}
  {loaded&&!error&&(data?<><p className="text-sm text-muted-foreground">{data.rules.length?`${data.rules.length} approved ${data.rules.length===1?'rule':'rules'} in the saved context`:'No project rules included in this request'}</p>
   <div className="space-y-4">{data.rules.map(rule=><article key={`${rule.rule_id}:${rule.version_id}`} className="rounded-xl border bg-muted/20 p-4"><p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{rule.text}</p>{rule.provenance&&<p className="mt-3 text-xs text-muted-foreground">{rule.provenance}</p>}</article>)}</div>
   {data.omitted>0&&<p className="text-xs text-muted-foreground">{data.omitted} omitted by source checks or context limits.</p>}
   <p className="text-xs text-muted-foreground">Saved {data.captured_at}</p></>:<p>No guidance record was saved for this earlier request.</p>)}
 </DialogContent></Dialog>;
}
