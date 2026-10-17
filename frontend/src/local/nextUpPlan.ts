import {local} from './api';
import {formatList,t} from '@/i18n';

/** Next up, one living plan per project (b16/lifecycle; Bryce, 24 Sep): NEXT-UP.md at the project's root, read and
 * edited by the host (backend/next_up.py). The page only places what the host read; every edit goes back with the
 * revision the page last saw, so an agent's edit in between is never overwritten.
 *
 * These operations reach the host through project_lifecycle's own list. The shell's bridge (bridge.rs) still has to
 * name them (asked of b16/bridge). */

/** ``project`` names the project whose number it is (``#21 in dd``), null for this plan's own. */
export type PlanItem={key:string;title:string;day:string|null;number:number|null;project?:string|null;marks:string[];thread:string|null;
 thread_id?:string|null;thread_state?:'running'|'needs'|null;sent:[string,string][];notes:string[]};
export type ShippedGroup={version:string;day:string|null;items:PlanItem[]};
export type PlanView={exists:boolean;file:string;revision:string|null;modified:number|null;name:string;
 sections:{next:PlanItem[];progress:PlanItem[];undecided:PlanItem[]};shipped:ShippedGroup[];other:string[];
 reports_here:boolean;reports_chosen:boolean;admin:boolean};
export type WorkReply=PlanView&{thread:{id:string;name:string};text:string};
export type TesterReport={id:string;category:'feedback'|'bug'|'confusing'|'idea';message:string;created_at:number;tester:string|null;
 app_version:string|null;route:string|null;provider:string|null;seen:boolean;added:boolean;shipped_in:string|null};
export type TesterReports={state:'loading'|'ready'|'error';reports:TesterReport[];unseen:number;project:string|null};
export type MoveTo='next'|'progress'|'undecided'|'drop';

export const readPlan=(projectId:string)=>local<PlanView>('next_up_read',{projectId});
export const addToPlan=(projectId:string,expectedRevision:string|null,title:string,note:string|null=null)=>local<PlanView>('next_up_add',{projectId,expectedRevision,title,note});
export const movePlanItem=(projectId:string,expectedRevision:string|null,key:string,to:MoveTo)=>local<PlanView>('next_up_move',{projectId,expectedRevision,key,to});
export const shipPlanItem=(projectId:string,expectedRevision:string|null,key:string,version:string)=>local<PlanView>('next_up_ship',{projectId,expectedRevision,key,version});
export const workOnPlanItem=(projectId:string,expectedRevision:string|null,key:string)=>local<WorkReply>('next_up_work',{projectId,expectedRevision,key});
export const showReportsHere=(projectId:string,here:boolean)=>local<PlanView>('next_up_reports_here',{projectId,here});
export const testerReports=(reload:boolean)=>local<TesterReports>('tester_reports',{reload});
export const markReport=(reportId:string,seen:boolean)=>local<{id:string;seen:boolean}>('tester_report_mark',{reportId,seen});
export const addReport=(projectId:string,expectedRevision:string|null,reportId:string)=>local<PlanView>('tester_report_add',{projectId,expectedRevision,reportId});

/** The feedback form's screen names, as a tester would say them. */
const SCREENS:Record<string,string>={conversation:t('Thread'),'catch-up':t('Catch up'),search:t('Search'),knowledge:t('Knowledge'),settings:t('Settings'),welcome:t('Welcome'),workspace:t('Home')};
export const CATEGORY_WORDS:Record<TesterReport['category'],string>={feedback:t('Feedback'),bug:t('Bug'),confusing:t('Confusing'),idea:t('Idea')};
const MONTHS=[t('Jan'),t('Feb'),t('Mar'),t('Apr'),t('May'),t('Jun'),t('Jul'),t('Aug'),t('Sep'),t('Oct'),t('Nov'),t('Dec')];
export function shortDay(at:number):string{const when=new Date(at*1000);return `${when.getDate()} ${MONTHS[when.getMonth()]}`;}

/** Under a tester report: "Harrison · 0.1.0-beta.15 · Settings · 25 Sep", then what became of it. First names only,
 * never an email (the server doesn't send one). */
export function reportLine(report:Pick<TesterReport,'tester'|'app_version'|'route'|'created_at'|'added'|'shipped_in'>):string{
 const parts=[report.tester||t('A tester'),report.app_version,report.route?SCREENS[report.route]??report.route:null,shortDay(report.created_at)];
 if(report.shipped_in)parts.push(t('shipped in {shipped_in}',{shipped_in:report.shipped_in}));
 else if(report.added)parts.push(t('added to Next up'));
 return parts.filter(Boolean).join(' · ');
}

/** Under a plan line: who it came from and when ("You, with ⌘⇧B · 25 Sep"), then its first note. */
export function itemLine(item:Pick<PlanItem,'marks'|'day'|'notes'>):string{
 const who=[...item.marks.filter(mark=>mark!=='Idea'),item.day].filter(Boolean).join(' · ');
 return [who,item.notes[0]].filter(Boolean).join(' · ');
}

/** A shipped version folded to one row: "14 items · Run buttons, the icon picker and more". */
export function shippedLine(group:ShippedGroup,shown=3):string{
 const n=group.items.length;
 const names=group.items.slice(0,shown).map((item,i)=>i===0?item.title:item.title.charAt(0).toLowerCase()+item.title.slice(1));
 const list=n>shown?`${names.join(', ')} ${t('and more')}`:formatList(names);
 return `${t('{count} items',{count:n})}${list?` · ${list}`:''}`;
}

/** "Updated 20 min ago", from the file's own time. Who changed it isn't known, so it isn't said. */
export function updatedLine(modified:number|null,now=Date.now()):string{
 if(typeof modified!=='number'||modified<1e9)return '';
 const minutes=Math.max(0,Math.floor((now-modified*1000)/60000));
 if(minutes<1)return t('Updated just now');
 if(minutes<60)return t('Updated {minutes} min ago',{minutes});
 const hours=Math.round(minutes/60);
 if(hours<24)return t('Updated {count} hours ago',{count:hours});
 return t('Updated {day}',{day:shortDay(modified)});
}

/** What a version for Shipped may look like: the host takes the same shape. */
export const VERSION_SHAPE=/^[A-Za-z0-9][A-Za-z0-9.+_ -]{0,39}$/;
