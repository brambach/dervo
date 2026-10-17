import {useEffect,useRef} from 'react';
import {isProjectTerminalKey} from './terminalKeys';
import {t} from '@/i18n';
/** Esc stops the running task, the way it interrupts Claude Code. Anything open takes Esc first: a menu, a dialog, the
 * palette, a side panel, an autocomplete list, or a reply you're writing. Only when none of those is open does Esc
 * stop, and only when something in this conversation is running. The project room never stops anything on Esc. */

export type EscapeState={
 running:boolean;
 inRoom:boolean;
 paletteOpen:boolean;
 panelOpen:boolean;
 replying:boolean;
 overlayOpen:boolean;
};

export function escapeStops(state:EscapeState):boolean{
 if(!state.running||state.inRoom)return false;
 return !(state.paletteOpen||state.panelOpen||state.replying||state.overlayOpen);
}

/** Radix menus, dialogs, popovers and listboxes, and the composer's autocomplete. Read at the moment Esc is pressed,
 * before any of them has had the chance to close, because the listener runs in the window's capture phase. */
export const OPEN_OVERLAYS=['[role="dialog"]','[role="alertdialog"]','[role="menu"]','[role="listbox"]',
 '[data-radix-popper-content-wrapper]'].join(',');

export function overlayOpen(root:ParentNode=document):boolean{
 return [...root.querySelectorAll<HTMLElement>(OPEN_OVERLAYS)].some(element=>!element.hidden&&element.getAttribute('aria-hidden')!=='true'
  &&element.getAttribute('data-state')!=='closed');
}

/** Where the working indicator was, once you stop. It promises carrying on only when the next message really resumes
 * the stopped session (checked in session_continuity: a clean stop continues Claude's session; Codex, and a stop that
 * caught an unconfirmed tool call, start fresh with a summary). */
export function stoppedLine(continuity?:string|null):string{
 return continuity==='continues'?t('Paused. Send a message or press Resume to carry on.')
  :t('Paused. Your next message starts a new session with a summary of this chat.');
}

/** The listener, on the window's capture phase so an open menu or dialog is still in the page when Esc is read.
 * ``read`` returns the latest state each time; ``stop`` is the same stop as the button and the palette. */
export function useEscapeToStop(read:()=>Omit<EscapeState,'overlayOpen'>&{stop:()=>unknown}){
 const latest=useRef(read);latest.current=read;
 useEffect(()=>{
  const onKey=(event:KeyboardEvent)=>{
   if(event.key!=='Escape'||event.defaultPrevented||event.isComposing||event.repeat||isProjectTerminalKey(event))return;
   if(event.target instanceof Element&&event.target.closest('[data-worker-questions], [data-worker-question-input]'))return;
   const now=latest.current();
   if(!escapeStops({...now,overlayOpen:overlayOpen()}))return;
   event.preventDefault();
   void now.stop();
  };
  window.addEventListener('keydown',onKey,true);
  return ()=>window.removeEventListener('keydown',onKey,true);
 },[]);
}
