import {useState,type ReactNode} from 'react';
import './ActivityLine.css';
import {t} from '@/i18n';

/** What Dervo did on its own, said once in one muted line (beta.11, b11/visible; mockup in
 * evidence/design-tests/activity-lines). The host writes the words; this only places them.
 *
 * One line, caption size, the muted ink. At most one action (Undo, Open thread, Hide), or a short word in its place once
 * the action is spent ("Undone"). No colour bar, no icon: the glyphs on the rows already carry state. It fades in once
 * when it mounts and never moves after that.
 *
 * Each kind of line has one home, so nothing is said twice:
 * - Home reply, under Dervo's words: what that answer changed (filed, moved, renamed, started a workstream) with Undo, then its notes.
 * - Home Now pane, under the heading: runs Dervo lost track of when it last closed. At the foot: the summary it couldn't write.
 * - Thread recap: what the newest message's session got, and a note the agent stopped before reading.
 * - Sidebar row: a thread Dervo named, filed or moved on its own, for a few seconds, with Undo.
 */
export type ActivityAction={label:string;onClick:()=>void;disabled?:boolean;describe?:string};

export function ActivityLine({text,action,done,live=false,className,note}:{text:ReactNode;action?:ActivityAction|null;done?:string|null;live?:boolean;className?:string;
 note?:string}){
 return <p className={className?`dv-activity ${className}`:'dv-activity'} role={live?'status':undefined} data-conversation-note={note}>
  <span className="dv-activity-text">{text}</span>
  {action?<button type="button" className="dv-activity-action" disabled={action.disabled} aria-label={action.describe} onClick={action.onClick}>{action.label}</button>
   :done?<span className="dv-activity-done">{done}</span>:null}
 </p>;
}

/** The most lines a group shows before the rest fold behind "N more". A reply that filed twelve threads is still one
 * glance, never a feed. */
export const ACTIVITY_SHOWN=3;
export function foldLines<T>(lines:T[],open:boolean,shown=ACTIVITY_SHOWN):{shown:T[];hidden:number}{
 // Folding one line saves nothing: "1 more" takes the same room as the line.
 if(open||lines.length<=shown+1)return {shown:lines,hidden:0};
 return {shown:lines.slice(0,shown),hidden:lines.length-shown};
}

/** A few lines together, folded past three. ``items`` are already the words and actions; ``more`` names what folded. */
export function ActivityLines({items,more='more',className}:{items:{key:string;text:ReactNode;action?:ActivityAction|null;done?:string|null}[];more?:string;className?:string}){
 const [open,setOpen]=useState(false);
 const fold=foldLines(items,open);
 if(!items.length)return null;
 return <div className={className?`dv-activity-list ${className}`:'dv-activity-list'}>
  {fold.shown.map(item=><ActivityLine key={item.key} text={item.text} action={item.action} done={item.done}/>)}
  {fold.hidden>0&&<ActivityLine text={`${fold.hidden} ${more}`} action={{label:t('Show'),onClick:()=>setOpen(true)}}/>}
 </div>;
}
