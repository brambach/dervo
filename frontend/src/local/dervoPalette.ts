import {useSyncExternalStore} from 'react';

/** The Dervo theme (beta.16.1 item 10, Bryce 26 Sep: "a nice dark forest green that will be easy on the eyes"). It's
 * Dark underneath, so everything that asks "is it dark?" (code blocks, the terminal, the native window) keeps working,
 * and a forest palette laid over Dark's tokens (DervoTokens.css, DervoBrand.css) by the root attribute
 * data-dervo-palette="forest". The choice is stored here, apart from ThemeProvider's theme, and index.html reads the same
 * key so a cold boot paints forest paper. The palette only shows with the dark class, so switching to Light in ⌘K shows
 * Light, and Dark in ⌘K brings the forest back until Light or Dark is picked in Preferences. */
export type Palette='forest'|null;
export const PALETTE_KEY='dervo-palette';
const listeners=new Set<()=>void>();

export function readPalette():Palette{
  try{return window.localStorage.getItem(PALETTE_KEY)==='forest'?'forest':null;}catch{return null;}
}

/** Put the stored palette on the document root, or take it off. */
export function applyPalette(palette:Palette=readPalette()){
  const root=document.documentElement;
  if(palette)root.setAttribute('data-dervo-palette',palette);else root.removeAttribute('data-dervo-palette');
}

export function setPalette(palette:Palette){
  try{if(palette)window.localStorage.setItem(PALETTE_KEY,palette);else window.localStorage.removeItem(PALETTE_KEY);}catch{/* no storage: this session only */}
  applyPalette(palette);
  for(const listener of listeners)listener();
}

function subscribe(change:()=>void){
  const storage=(event:StorageEvent)=>{if(event.key===PALETTE_KEY){applyPalette();change();}};
  listeners.add(change);
  window.addEventListener('storage',storage);
  return ()=>{listeners.delete(change);window.removeEventListener('storage',storage);};
}

export function usePalette():Palette{
  return useSyncExternalStore(subscribe,readPalette,()=>null);
}
