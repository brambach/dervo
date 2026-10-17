import {useId,useState} from 'react';
import {PathChip} from '@/shared/ui/markdown/ReplyChips';
import './ResultNotes.css';
import {t} from '@/i18n';

/** What Dervo couldn't record from a full-access run, and what another chat changed alongside it (native_evidence.py).
 * Beta.15, Bryce 23 Sep: these used to be one rust sentence with the file list inside it. Now they sit inside the result
 * card: the files Dervo couldn't record as rows with a plain reason each, and another chat's work as one quiet line that
 * opens to its files. Neither is shouted: the lead line above says whether you can approve. */
export type Unrecorded={path:string;why:string;count?:number};
export type Alongside={count:number;files:string[]};
const SHOWN=5;
const files=(count:number)=>t('{count} files',{count});

/** Rows for the files Dervo couldn't record, then the one thing to do about them. */
export function UnrecordedFiles({rows,blocked}:{rows:Unrecorded[];blocked:boolean}){
 const [all,setAll]=useState(false);
 const shown=all?rows:rows.slice(0,SHOWN);
 const one=rows.length===1&&(rows[0].count??1)===1;
 return <div className="dv-result-files" role="group" aria-label={t('Files Dervo couldn’t record')}>
  <ul>{shown.map(row=><li key={row.path}>
   {/* A row for several files names them in words ("a.py, b.py and 3 more"), which isn't a path to open or reveal. */}
   {(row.count??1)===1?<PathChip path={row.path}><code>{row.path}</code></PathChip>:<code>{row.path}</code>}
   <span>{row.count&&row.count>1?`${row.why}. ${files(row.count)}.`:row.why}</span>
  </li>)}</ul>
  {rows.length>SHOWN&&<button type="button" className="dv-link dv-result-more" onClick={()=>setAll(value=>!value)}>{all?t('Show fewer'):t('Show {count} more',{count:rows.length-SHOWN})}</button>}
  <p className="dv-result-hint">{blocked?one?t('It’s in your project folder as the agent left it. Commit or remove it, then send the request again.'):t('They’re in your project folder as the agent left them. Commit or remove them, then send the request again.')
   :one?t('The rest of the result is here to review. To approve it, commit or remove this file, then ask for a correction.'):t('The rest of the result is here to review. To approve it, commit or remove these files, then ask for a correction.')}</p>
 </div>;
}

export type Inherited={path:string;task_id:string|null};

/** Beta.15 (Bryce, 24 Sep): files an earlier Dervo result left in the folder unapproved, which this run changed again.
 * They're recorded here instead of blocking as "your" edits, so the line says approving this commits them too. Design
 * review S7 (24 Sep): "here" and "held an earlier result" were hard to parse, so it names the files and what approving does. */
export function InheritedNote({rows}:{rows:Inherited[]}){
 const [open,setOpen]=useState(false);
 const id=useId();
 return <div className="dv-result-alongside" data-testid="result-inherited">
  <p>{rows.length===1?t('One of these files also has changes from an earlier result you didn’t approve. Approving this commits those too.'):t('{count} of these files also have changes from an earlier result you didn’t approve. Approving this commits them too.',{count:rows.length})}
   <button type="button" className="dv-link" aria-expanded={open} aria-controls={id} onClick={()=>setOpen(value=>!value)}>{open?t('Hide'):rows.length===1?t('Which file'):t('Which files')}</button></p>
  {open&&<ul id={id}>{rows.map(row=><li key={row.path}><PathChip path={row.path}><code>{row.path}</code></PathChip></li>)}</ul>}
 </div>;
}

/** Another chat worked in this folder during the run: one quiet line, and its files behind a toggle. Informational only. */
export function AlongsideNote({alongside}:{alongside:Alongside}){
 const [open,setOpen]=useState(false);
 const id=useId();
 const more=alongside.count-alongside.files.length;
 return <div className="dv-result-alongside">
  <p><span className="dv-result-pair" aria-hidden="true"><i/><i/></span>
   {t('Another chat worked in this folder too. This result has only this task’s edits.')}
   <button type="button" className="dv-link" aria-expanded={open} aria-controls={id} onClick={()=>setOpen(value=>!value)}>{open?t('Hide'):t('Its {files}',{files:files(alongside.count)})}</button></p>
  {open&&<ul id={id}>{alongside.files.map(path=><li key={path}><PathChip path={path}><code>{path}</code></PathChip></li>)}
   {more>0&&<li className="dv-result-hint">{t('and {count} more',{count:more})}</li>}</ul>}
 </div>;
}
