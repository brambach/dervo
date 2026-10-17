import {Check, CircleAlert, Pause} from 'lucide-react';
import type {CodingTask, TaskToken} from './CodingTasks';
import {WorkingMark} from './WorkingMark';
import {PresenceText} from './Presence';
import './DervoActivity.css';
import {t} from '@/i18n';

export type ActivityTone = 'neutral' | 'completed' | 'attention' | 'error' | 'cancelled';

/** A file label never contains directories, tool arguments or control characters. */
export function activityBasename(path?: string | null): string | undefined {
  if (!path) return undefined;
  const name = path.split(/[\\/]/).filter(Boolean).at(-1)?.replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, '');
  if (!name || name === '.' || name === '..') return undefined;
  return name.length > 48 ? `${name.slice(0, 45)}…` : name;
}

const operations: Record<string, string> = {
  dervo_read_file: t('Reading'), dervo_write_file: t('Editing'), dervo_edit_file: t('Editing'), dervo_create_file: t('Creating'),
  dervo_list_files: t('Listing project files'), dervo_search_files: t('Searching project files'),
  dervo_run_tests: t('Running configured checks'), dervo_request_help: t('Waiting for a helper'),
  dervo_update_task_guide: t('Updating the task guide'), dervo_suggest_lesson: t('Suggesting project guidance'),
};
export function observableActivity(token?: TaskToken): string | undefined {
  if (!token || token.success === false || !['executing', 'running', 'queued', 'delivering'].includes(token.state)) return undefined;
  const action = operations[token.tool];
  if (!action) return undefined;
  if (token.state === 'delivering') return t('Returning a tool result');
  if (token.state === 'queued') return token.tool === 'dervo_run_tests' ? t('Waiting for configured checks') : t('Waiting for a tool request');
  if (['dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_create_file'].includes(token.tool)) {
    const name = activityBasename(token.path);
    return name ? `${action} ${name}` : t('{action} project files',{action});
  }
  return action;
}
export function latestObservableActivity(task?: CodingTask): string | undefined {
  if (!task || task.state !== 'working' || task.runs.some(run => (run as {state?: string})?.state === 'uncertain') || task.tokens.some(token => token.state === 'uncertain')) return undefined;
  const run = task.runs.at(-1) as {id?: string} | undefined;
  if (!run?.id) return undefined;
  const token = [...task.tokens].reverse().find(item => item.run_id === run.id && ['executing', 'running', 'queued', 'delivering'].includes(item.state));
  return observableActivity(token);
}

/** One mounted status row across work, review and recovery. CSS owns the quiet active cue. While work runs the icon is
 * the working smile (#31), where the moss spinner sat before. */
export function DervoActivity({label, active = false, tone = 'neutral', secondary, className = '', announce = true}: {
  label: string; active?: boolean; tone?: ActivityTone; secondary?: string; className?: string; announce?: boolean;
}) {
  const isActive = active && tone === 'neutral';
  const Icon = tone === 'completed' ? Check : tone === 'cancelled' ? Pause : tone === 'error' || tone === 'attention' ? CircleAlert : undefined;
  return <span className={`dervo-activity ${className}`} data-active={isActive || undefined} data-tone={tone}>
    <span className="dervo-activity-icon" aria-hidden="true">{Icon ? <Icon size={17}/> : isActive ? <WorkingMark size={18} working/> : <span className="dervo-activity-dot"/>}</span>
    <span className="dervo-activity-copy" role={announce ? 'status' : undefined} aria-atomic={announce || undefined}>
      <PresenceText className="dervo-activity-label" active={isActive}>{label}</PresenceText>
      <span className="dervo-activity-secondary" aria-hidden={!secondary || undefined}>{secondary}</span>
    </span>
  </span>;
}
