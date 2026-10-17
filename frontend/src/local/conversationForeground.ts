import {getCurrentWindow} from '@tauri-apps/api/window';

type NativeFocus={isFocused:()=>Promise<boolean>;onFocusChanged:(callback:(event:{payload:boolean})=>void)=>Promise<()=>void>};

/** Native focus can recover a WebView whose document visibility stays hidden. */
export function watchConversationForeground(change:(focused:boolean)=>void,native:()=>NativeFocus=getCurrentWindow){
  let closed=false,nativeKnown=false,revision=0;
  let unlisten:(()=>void)|undefined;
  const focus=()=>{revision++;if(!closed)change(true);};
  const blur=()=>{revision++;if(!closed)change(false);};
  const visibility=()=>{if(!nativeKnown&&!closed){revision++;change(document.visibilityState==='visible');}};
  window.addEventListener('focus',focus);
  window.addEventListener('blur',blur);
  document.addEventListener('visibilitychange',visibility);
  void (async()=>{
    try{
      const app=native();
      const release=await app.onFocusChanged(({payload})=>{if(!closed){nativeKnown=true;revision++;change(payload);}});
      if(closed){release();return;}
      unlisten=release;
      const before=revision;
      const focused=await app.isFocused();
      if(!closed&&revision===before){nativeKnown=true;change(focused);}
    }catch{
      // Browser previews use DOM events. Explicit conversation reads never depend on focus.
    }
  })();
  return ()=>{
    closed=true;
    window.removeEventListener('focus',focus);
    window.removeEventListener('blur',blur);
    document.removeEventListener('visibilitychange',visibility);
    unlisten?.();
  };
}

/** Focused: Dervo is the front window. Shown: another app is in front but Dervo's document is still visible, so a thread
 * on screen is still being watched (dd #24). Hidden: minimised, on another Space or covered, as the WebView reports it. */
export type ConversationPresence='focused'|'shown'|'hidden';
export const currentPresence=():ConversationPresence=>document.visibilityState==='visible'?'focused':'hidden';

/** Focus from watchConversationForeground, split by document visibility once Dervo isn't in front. A WebView whose
 * visibility is stuck at hidden still counts as focused while native focus says so. Reports changes, and every focus,
 * since the first guess at mount can be wrong and coming to the front always catches up. */
export function watchConversationPresence(change:(presence:ConversationPresence)=>void,native:()=>NativeFocus=getCurrentWindow){
  let last=currentPresence(),focused=last==='focused',closed=false;
  const emit=(always=false)=>{
    if(closed)return;
    const next:ConversationPresence=focused?'focused':document.visibilityState==='visible'?'shown':'hidden';
    if(next!==last||always){last=next;change(next);}
  };
  const stop=watchConversationForeground(value=>{focused=value;emit(value);},native);
  const visibility=()=>emit();
  document.addEventListener('visibilitychange',visibility);
  return ()=>{closed=true;stop();document.removeEventListener('visibilitychange',visibility);};
}
