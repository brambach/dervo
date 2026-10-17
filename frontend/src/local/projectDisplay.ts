import {t} from '@/i18n';
export type DisplayProject = {id:string; cwd:string; name?:string|null; profile_id?:string; model?:string;
  /** Work or Personal (b16-work): a Work project carries the Work mark wherever its name is. */
  context?:'work'|'personal'|null};

export function projectName(project?:DisplayProject):string {
  if (!project) return t('Project');
  return project.name?.trim() || project.cwd.split('/').filter(Boolean).at(-1) || project.cwd;
}
