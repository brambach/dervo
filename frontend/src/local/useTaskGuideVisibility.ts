import {useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';

type WidthMode='unknown'|'narrow'|'wide';
type Choice={scope:string;open:boolean};

const WIDE_OVER=620;
/** A narrow reading has to hold this long before the guide closes, so a resize, a full-screen animation or a
 * route change that measures small for a frame doesn't close it. Going wide takes effect at once. */
export const NARROW_SETTLE_MS=250;

function tracing(){
 try{
  return (import.meta as {env?:{DEV?:boolean}}).env?.DEV===true
   ||!!(typeof window!=='undefined'&&(window as {__dervoGuideTrace?:unknown}).__dervoGuideTrace);
 }catch{return false;}
}

/** Wide areas open the guide by default; small windows keep the conversation visible until the user opens it.
 * An explicit open holds across widths for the thread. An explicit close holds while wide and is forgotten once the
 * area has really gone narrow. Nothing is saved across relaunches. */
/** Whether a thread whose tasks haven't loaded yet will open with Progress: its sidebar activity says a run happened.
 * A thread nobody has run anything in has none of these, so it doesn't get a column it would then lose. */
export function expectsTask(activity?:{running?:boolean;status?:string|null;at?:number|null;ended?:string|null;summary?:string|null;goal?:string|null}|null):boolean{
 return !!activity&&(!!activity.running||!!activity.status||typeof activity.at==='number'||!!activity.ended||!!activity.summary||!!activity.goal);
}

/** ``expectTask``: the thread's tasks are still loading and it will likely have one. ``reserved`` then says to hold the
 * column the guide would open into (beta.18: the right column popped in after the thread had drawn full width). */
export function useTaskGuideVisibility({scope,hasTask,expectTask=false}:{scope:string;hasTask:boolean;expectTask?:boolean}){
 // A callback ref, so measuring starts when <main> mounts. ConnectedApp renders OpeningScreen first, so a plain ref
 // read once on the first layout effect was null and the guide never opened by default.
 const [main,setMain]=useState<HTMLElement|null>(null);
 const mainRef=useCallback((node:HTMLElement|null)=>{setMain(node);},[]);
 const triggerRef=useRef<HTMLButtonElement|null>(null);
 const [mode,setMode]=useState<WidthMode>('unknown');
 const modeRef=useRef<WidthMode>('unknown');
 const widthRef=useRef<number|undefined>(undefined);
 const [choice,setChoice]=useState<Choice|null>(null);
 const choiceRef=useRef<Choice|null>(null);
 choiceRef.current=choice;
 const current=useRef({scope,hasTask});
 useLayoutEffect(()=>{
  if(current.current.scope!==scope)setChoice(null);
  current.current={scope,hasTask};
 },[scope,hasTask]);

 useLayoutEffect(()=>{
  if(!main)return;
  let disposed=false;
  let pending:ReturnType<typeof setTimeout>|undefined;
  function cancelPending(){if(pending!==undefined){clearTimeout(pending);pending=undefined;}}
  function apply(next:WidthMode){
   if(disposed||next===modeRef.current)return;
   const previous=modeRef.current;
   modeRef.current=next;
   const held=choiceRef.current;
   const explicitOpen=held?.scope===current.current.scope&&held.open;
   // Closing the guide mustn't leave keyboard focus inside its removed content.
   if(previous==='wide'&&next==='narrow'&&!explicitOpen&&main!.querySelector('.dervo-task-guide')?.contains(document.activeElement)){
    triggerRef.current?.focus();
   }
   // A settled change of width forgets a close, so the default for the new width applies. An open is kept.
   if(previous!=='unknown'&&held&&!held.open)setChoice(null);
   setMode(next);
  }
  function measure(width:number){
   if(disposed||!Number.isFinite(width)||width<0)return;
   widthRef.current=width;
   const next:WidthMode=width>WIDE_OVER?'wide':'narrow';
   if(next==='wide'){cancelPending();apply('wide');return;}
   if(modeRef.current==='narrow'){cancelPending();return;}
   // The first reading has nothing to protect yet; a zero-width first frame shows nothing and the next wide reading opens it.
   if(modeRef.current==='unknown'){apply('narrow');return;}
   if(pending===undefined)pending=setTimeout(()=>{pending=undefined;apply('narrow');},NARROW_SETTLE_MS);
  }
  const resize=()=>measure(main.getBoundingClientRect().width);
  resize();
  const observer=typeof ResizeObserver==='undefined'?undefined:new ResizeObserver(entries=>{
   const entry=entries.find(item=>item.target===main);
   if(entry)measure(entry.contentRect.width);
  });
  observer?.observe(main);
  if(!observer)window.addEventListener('resize',resize);
  return ()=>{disposed=true;cancelPending();observer?.disconnect();window.removeEventListener('resize',resize);};
 },[main]);

 const explicit=choice?.scope===scope?choice:null;
 const open=explicit?explicit.open:mode==='wide';
 const visible=hasTask&&open;
 const reserved=!hasTask&&expectTask&&open;
 // For catching #11 in a real run: every change of what decides the guide, with the last measured width.
 useEffect(()=>{
  if(!tracing())return;
  console.info('guide',{scope,width:widthRef.current,mode,choice:explicit,hasTask,visible});
 },[scope,mode,explicit?.open,explicit==null,hasTask,visible]);
 const toggle=useCallback(()=>{
  if(current.current.scope!==scope||!current.current.hasTask)return;
  setChoice(previous=>({scope,open:!(previous?.scope===scope?previous.open:modeRef.current==='wide')}));
 },[scope]);
 const close=useCallback(()=>{
  if(current.current.scope!==scope||!current.current.hasTask)return;
  setChoice({scope,open:false});
  triggerRef.current?.focus();
 },[scope]);
 return {mainRef,triggerRef,visible,reserved,toggle,close};
}
