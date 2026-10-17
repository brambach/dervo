import {useEffect,useRef,useState} from 'react';

type Inspector={id:string;activation:number;context:string;valid:boolean;open:boolean;onCloseAutoFocus:(event:Event)=>void};

/** Keep the actual pointer or keyboard opener. macOS pointer clicks may leave activeElement on the document. */
export function useInspectorNavigation(projectId:string,channelId:string,fallback:()=>HTMLElement|null) {
 const context=JSON.stringify([projectId,channelId]);
 const current=useRef({context,fallback});current.current={context,fallback};
 const taskRef=useRef<Inspector|null>(null),packetRef=useRef<Inspector|null>(null);
 const activation=useRef(0);
 const [task,setTask]=useState<Inspector|null>(null),[packet,setPacket]=useState<Inspector|null>(null);
 function invalidate(record:Inspector|null){if(record){record.valid=false;record.open=false;}}
 function capture(id:string,targetChannel:string,trigger?:HTMLElement,targetProject=projectId):Inspector {
  const record:Inspector={id,activation:++activation.current,context:JSON.stringify([targetProject,targetChannel]),valid:true,open:true,onCloseAutoFocus:event=>{
   event.preventDefault();
   if(!record.valid||record.context!==current.current.context)return;
   // A replaced inspector mustn't steal focus from a newer dialog.
   const newer=[taskRef.current,packetRef.current].find(other=>other!==record&&other?.open&&other.valid);
   if(newer&&(!trigger?.isConnected||!trigger.closest('[role="dialog"]')))return;
   const target=trigger?.isConnected&&!trigger.closest('[hidden],[inert]')?trigger:current.current.fallback();
   target?.focus({preventScroll:true});
  }};
  return record;
 }
 function navigateAway(){invalidate(taskRef.current);invalidate(packetRef.current);setTask(null);setPacket(null);}
 function openTask(targetChannel:string,id:string,trigger?:HTMLElement,targetProject=projectId){
  invalidate(taskRef.current);invalidate(packetRef.current);setPacket(null);
  const next=capture(id,targetChannel,targetChannel===channelId&&targetProject===projectId?trigger:undefined,targetProject);
  taskRef.current=next;setTask(next);
 }
 function openPacket(id:string,trigger?:HTMLElement){
  invalidate(packetRef.current);
  const next=capture(id,channelId,trigger);packetRef.current=next;setPacket(next);
 }
 function closeTask(){if(taskRef.current)taskRef.current.open=false;setTask(null);}
 function closePacket(){if(packetRef.current)packetRef.current.open=false;setPacket(null);}
 useEffect(()=>{
  for(const [record,set] of [[taskRef.current,setTask],[packetRef.current,setPacket]] as const){
   if(record&&record.context!==context){invalidate(record);set(null);}
  }
 },[context]);
 return {task,packet,openTask,openPacket,closeTask,closePacket,navigateAway};
}
