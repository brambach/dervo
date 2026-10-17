import type {KeyboardEvent as ReactKeyboardEvent} from 'react';
import {overlayOpen} from './escapeToStop';

/** Up and Down, or j and k, from row to row on a list screen, so you can go through threads fast (dd #21, Bryce 29 Sep;
 * both Catch up screens, 2 Oct). Home's thread panel steps its own way (Home.stepThread); this is for screens whose rows
 * are [role="listitem"], like the full Catch up. A row's first enabled button takes the focus, so Enter there does what
 * clicking the row does. Wraps at the ends, as Home's panel does. */
const ROW='[role="listitem"]';
const TYPING='textarea,input,select,[contenteditable="true"]';

type Keys={key:string;metaKey:boolean;ctrlKey:boolean;altKey:boolean;defaultPrevented:boolean;isComposing?:boolean};
/** +1 for Down and j, -1 for Up and k, 0 for anything else or with a modifier. */
export function rowKeyDelta(event:Keys):1|-1|0{
  if(event.defaultPrevented||event.isComposing||event.metaKey||event.ctrlKey||event.altKey)return 0;
  return event.key==='ArrowDown'||event.key==='j'?1:event.key==='ArrowUp'||event.key==='k'?-1:0;
}

/** Each row's first enabled button, in page order. A row without one is skipped. */
export function rowTargets(root:ParentNode):HTMLElement[]{
  return [...root.querySelectorAll<HTMLElement>(ROW)].flatMap(row=>{
    const target=row.querySelector<HTMLElement>('button:not(:disabled)');
    return target?[target]:[];
  });
}

/** Focuses the row one on from the row holding ``from``. From outside every row, Down goes to the first and Up to the
 * last. True when the focus moved. */
export function stepRowFocus(root:ParentNode,from:Element|null,delta:1|-1):boolean{
  const targets=rowTargets(root);
  if(!targets.length)return false;
  const at=from?targets.findIndex(target=>target.closest(ROW)?.contains(from)):-1;
  const next=targets[at<0?(delta>0?0:targets.length-1):(at+delta+targets.length)%targets.length];
  next.focus();
  next.scrollIntoView?.({block:'nearest'});
  return true;
}

/** A list screen's onKeyDown. Keys typed into a box, and keys while a menu or dialog is open, stay theirs. */
export function onRowKeys(event:ReactKeyboardEvent<HTMLElement>){
  const delta=event.nativeEvent.isComposing?0:rowKeyDelta(event);
  if(!delta)return;
  const target=event.target as HTMLElement|null;
  if(target?.closest?.(TYPING)||overlayOpen())return;
  if(stepRowFocus(event.currentTarget,target,delta))event.preventDefault();
}
