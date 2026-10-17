import {useEffect} from 'react';
import {local} from './api';

/** Usage events (backend/usage_events.py). The renderer sends two of them: which screen is open, and the model picker
 * opening. The host records the rest itself, drops anything that isn't on its allowlist, and drops everything while
 * sharing is off. Sending is never awaited and never throws: usage is never worth a broken screen. */
export type UsageSharing={enabled:boolean;notice:boolean};
export type AppEvent='screen_viewed'|'model_picker_opened';

export function track(event:AppEvent,props:Record<string,string>={}){
  try{void local('usage_event',{event,props}).catch(()=>{});}catch{/* no host, no event */}
}

/** What's on screen, as one of the host's screen names, or null while first run or nothing is showing. Settings covers
 * the screen underneath, so it wins; then the panels that take the main column; then a thread, then Home. */
export type ScreenState={firstRun:boolean;settingsPage:string|null;knowledge:boolean;search:boolean;catchUp:boolean;releases:boolean;thread:boolean;home:boolean};
export function usageScreen(state:ScreenState):string|null{
  if(state.firstRun)return null;  // first run is counted as its funnel steps, by the host
  if(state.settingsPage)return `settings.${state.settingsPage}`;
  if(state.knowledge)return 'knowledge';
  if(state.search)return state.catchUp?'catch_up':'search';
  if(state.releases)return 'releases';
  if(state.thread)return 'thread';
  return state.home?'home':null;
}

/** The screen showing now, for an error report. Kept here because the crash screen sits outside the app's state. */
let shown:string|null=null;

/** One screen view each time the screen changes, and each time a different thread opens. */
export function useScreenView(screen:string|null,thread?:string){
  useEffect(()=>{shown=screen;if(screen)track('screen_viewed',{screen});},[screen,screen==='thread'?thread:undefined]);
}

/** Error reports (backend/error_reports.py): the crash screen sends the error's built-in name and the screen that was
 * showing. Never the message, the stack or a component name. The host drops it while sharing is off. */
export function reportCrash(error:unknown){
  const errorType=error instanceof Error&&typeof error.name==='string'?error.name:'Error';
  try{void local('error_event',{errorType,screen:shown}).catch(()=>{});}catch{/* no host, no report */}
}
