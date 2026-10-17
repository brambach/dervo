import {useEffect,useRef} from 'react';
import {listen} from '@tauri-apps/api/event';
import {isProjectTerminalKey} from './terminalKeys';

/** The menu bar's own items (src/menu.rs) send one event to the main window, naming what was chosen. Each runs the same
 * handler as its key: Settings… is ⌘, and Report a Dervo Bug… is ⌘⇧B. */
export type MenuAction='settings'|'check-updates'|'report-bug'|'send-feedback';
export const MENU_EVENT='dervo-menu';
const ACTIONS:readonly MenuAction[]=['settings','check-updates','report-bug','send-feedback'];

export function menuAction(payload:unknown):MenuAction|null{
  return typeof payload==='string'&&(ACTIONS as readonly string[]).includes(payload)?payload as MenuAction:null;
}

/** ⌘⇧B, or Ctrl+Shift+B. By code as well as key, so a keyboard layout that puts another letter there still works. */
export function isBugKey(event:Pick<KeyboardEvent,'metaKey'|'ctrlKey'|'shiftKey'|'altKey'|'key'|'code'>):boolean{
  return (event.metaKey||event.ctrlKey)&&event.shiftKey&&!event.altKey&&(event.key.toLowerCase()==='b'||event.code==='KeyB');
}

/** ⌘⇧B from anywhere in the window. It's read in the capture phase, before the composer sees it: the editor's Bold
 * answers ⌘⇧B as well as ⌘B, and would bold your text as the sheet opened. A held key opens it once. The project
 * terminal keeps its own keys. */
export function useBugKey(run:()=>void){
  const latest=useRef(run);latest.current=run;
  useEffect(()=>{
    function onKey(event:KeyboardEvent){
      if(event.defaultPrevented||event.isComposing||isProjectTerminalKey(event)||!isBugKey(event))return;
      event.preventDefault();event.stopImmediatePropagation();
      if(event.repeat)return;
      keyRan('report-bug');latest.current();
    }
    window.addEventListener('keydown',onKey,true);
    return()=>window.removeEventListener('keydown',onKey,true);
  },[]);
}

/** WebKit gives a key to the page first and passes it to the menu bar only when the page left it alone, so a key the
 * page handled never reaches its menu item. Should both ever arrive for one press, the menu's copy is dropped: a menu
 * event for the same action within this many milliseconds of the key is the same press. */
export const SAME_PRESS_MS=500;
let lastKey:{action:MenuAction;at:number}|null=null;
export function keyRan(action:MenuAction,now=Date.now()){lastKey={action,at:now};}
export function menuShouldRun(action:MenuAction,now=Date.now()):boolean{
  const same=!!lastKey&&lastKey.action===action&&now-lastKey.at>=0&&now-lastKey.at<SAME_PRESS_MS;
  if(same)lastKey=null;
  return !same;
}

/** Runs ``run`` for each menu choice while mounted. Outside Dervo's window (a browser preview) there's no menu. */
export function useNativeMenu(run:(action:MenuAction)=>void){
  const latest=useRef(run);latest.current=run;
  useEffect(()=>{
    let alive=true,stop:(()=>void)|null=null;
    try{
      void listen<unknown>(MENU_EVENT,event=>{const action=menuAction(event.payload);if(alive&&action&&menuShouldRun(action))latest.current(action);})
        .then(unlisten=>{if(alive)stop=unlisten;else unlisten();}).catch(()=>{/* no menu here */});
    }catch{/* no Tauri here */}
    return()=>{alive=false;stop?.();};
  },[]);
}
