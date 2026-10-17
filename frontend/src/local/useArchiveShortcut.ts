import {useEffect} from 'react';
import {isProjectTerminalKey} from './terminalKeys';

/** Archive is a deliberate chord, never a plain letter while someone types. */
export function archiveKey(event:KeyboardEvent){
  return !event.defaultPrevented&&!event.repeat&&!event.isComposing&&!event.altKey&&!isProjectTerminalKey(event)
    &&(event.metaKey||event.ctrlKey)&&event.shiftKey&&event.key.toLowerCase()==='a';
}

export function useArchiveShortcut(enabled:boolean,archive:()=>void){
  useEffect(()=>{
    const onKey=(event:KeyboardEvent)=>{
      if(!enabled||!archiveKey(event)||document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"], [data-dv-surface="dialog"]'))return;
      event.preventDefault();archive();
    };
    window.addEventListener('keydown',onKey);
    return()=>window.removeEventListener('keydown',onKey);
  },[enabled,archive]);
}
