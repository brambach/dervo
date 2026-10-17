import {t} from '@/i18n';

/** Send to cloud's records as the host shows them (backend/cloud_runs.py, CloudRuns.view). The state is the host's; the
 * window never moves a run on by itself, and a Codex status the host hasn't confirmed lately already arrives as unknown. */
export type CloudState='starting'|'working'|'ready'|'failed'|'unknown';
export type CloudRun={id:string;kind:'cloud';provider:'claude'|'codex';where:string;projectId:string;conversationId:string|null;task:string;
  state:CloudState;line:string|null;summary:string|null;providerStatus:string|null;link:string|null;remoteId:string|null;baseBranch:string;
  branch:string|null;candidates:string[];broughtBack:string|null;account:string|null;createdAt:number;lastCheckedAt:number|null;
  lastSeenAt:number|null;checking:boolean;following:boolean;canCheck:boolean;canFollowUp:boolean;canBringBack:boolean};
export type CloudRunsView={runs:CloudRun[];environment:string|null;provider:'claude'|'codex'|null};
export type CloudPreview={previewId:string|null;provider:'claude'|'codex';where:string;account:string;repository:string;remote:string;
  branch:string;environment:string|null;notes:string[];canSend:boolean;detail:string|null};

/** The word on a run's state pill. */
export function stateWord(run:Pick<CloudRun,'state'|'broughtBack'>&Partial<Pick<CloudRun,'branch'|'candidates'>>):string{
  if(run.broughtBack)return t('Brought back');
  if(run.state==='ready'&&!run.branch&&(run.candidates?.length??0)>0)return t('Pick a branch');
  switch(run.state){
    case 'starting':return t('Starting');
    case 'working':return t('Working');
    case 'ready':return t('Result ready');
    case 'failed':return t('Failed');
    default:return t('Unknown');
  }
}

/** "Checked 3 min ago", from the host's last look at the provider; null before the first one. Seconds since the epoch. */
export function checkedAgo(at:number|null,now:number):string|null{
  if(at==null)return null;
  const minutes=Math.floor(Math.max(0,now-at)/60);
  if(minutes<1)return t('Checked just now');
  if(minutes<60)return t('Checked {minutes} min ago',{minutes});
  return t('Checked {hours} h ago',{hours:Math.floor(minutes/60)});
}

/** A task's first line, for the row's title. */
export function taskTitle(task:string,limit=90):string{
  const line=(task.split('\n').find(part=>part.trim())??'').trim();
  return line.length>limit?`${line.slice(0,limit-1).trimEnd()}…`:line;
}

/** How often the page reads the records while something is still open: the host does the provider checks on its own
 * schedule, so this only picks up what it settled. Quickly while a launch or check is in flight or Codex reports
 * progress, slowly while the host is still following an unknown run (a Claude run only moves when a branch appears), and
 * not at all once nothing open is being followed. Check now refreshes the list on its own. */
export function pollEvery(runs:Pick<CloudRun,'state'|'checking'|'following'>[]):number|false{
  if(runs.some(run=>run.state==='starting'||run.state==='working'||run.checking))return 4000;
  return runs.some(run=>run.state==='unknown'&&run.following)?15000:false;
}
